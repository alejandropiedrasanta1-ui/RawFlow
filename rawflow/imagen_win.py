"""Paso 4 en Windows sin clave API: maneja la app ImagenAI (con tu sesión ya iniciada) como lo harías tú.

Es el mismo camino que rawflow/imagen_ui.py hace en el Mac, con los mismos rótulos (la app es la misma):
  ImagenAI → Proyectos → Nuevo proyecto → Carpeta → Explorar → <evento>/2 DNGs → Elegir carpeta
  → nombre = evento · tipo Bodas → Siguiente → Siguiente (perfil) → Crear proyecto
  → esperar a que la lista diga que está lista para revisar → Revisar Ediciones → Descargar todas las ediciones
  → Descargar ediciones (esto es lo que COBRA Imagen) → las ediciones quedan dentro de los DNG.

Los botones se encuentran leyendo la pantalla (rawflow/winscreen.py: captura + OCR de Windows), no con coordenadas
fijas: la ventana de ImagenAI no deja leerlos por accesibilidad. Nunca se toca el inicio de sesión ni nada de la
cuenta: solo se pulsan los mismos botones que pulsarías tú.

`charge_ok=False` llega hasta tener la edición lista y se para ANTES de descargar (nada se cobra): para pruebas.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from . import core, live
from . import winscreen as ws
from .imagen_ui import UIError, _key, norm

Log = Callable[[str], None]
EXE = "ImagenAI.exe"
_ROAM = Path(os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming"))
WORK = _ROAM / "RawFlow"
LOGS = _ROAM / "ImagenAI" / "logs"
HIST = WORK / "imagen_proyectos.txt"
PROG = [lambda f: None]

DL_TRIES = 3          # descargas de ediciones de un mismo proyecto antes de dar el paso por fallido
DL_WAIT = 15          # segundos entre una y otra (×1, ×2…)
PROMPT_WAIT = 15      # tras «SUCCESS», segundos esperando la pregunta «¿Dónde quieres revisar?»
SETTLE_WAIT = 60      # tras «SUCCESS», segundos para que los DNG terminen de llevar las ediciones


def _prog(f: float) -> None:
    try:
        PROG[0](max(0.0, min(1.0, f)))
    except Exception:  # noqa: BLE001
        pass


def _mmss(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


def app_path() -> Optional[Path]:
    from . import programas
    return programas.imagen()


def no_apps() -> bool:
    """RAWFLOW_SIN_APPS=1 (lo ponen las pruebas del proyecto): RawFlow NUNCA abre ni maneja ImagenAI, Bridge ni
    Retouch4me de verdad. El 23-nov-2026 una prueba simulada creó un proyecto «p01_evento» en la cuenta real de
    Imagen porque nada se lo impedía."""
    return os.environ.get("RAWFLOW_SIN_APPS") == "1"


def available() -> bool:
    return sys.platform == "win32" and not no_apps() and app_path() is not None


# ------------------------------------------------------------------ pantalla
class Screen:
    """Lo mismo que imagen_ui.Screen (leer rótulos, tocar, escribir), sobre una ventana de Windows."""

    def __init__(self, log: Log, should_stop=lambda: False, exe: str = EXE):
        self.log, self.should_stop, self.exe = log, should_stop, exe
        self.rect = (0, 0, 0, 0)
        self._last = ("", [])
        self._fcheck = 0.0

    # ---- ventana
    def window(self) -> Optional[dict]:
        """La ventana del programa que está más delante (su diálogo, si tiene uno abierto; si no, la principal)."""
        wins = [w for w in ws.windows(exe=self.exe) if w["rect"][2] > 300 and w["rect"][3] > 200 and not w["min"]]
        return wins[0] if wins else None

    def main_window(self) -> Optional[dict]:
        wins = [w for w in ws.windows(exe=self.exe) if w["rect"][2] > 300 and w["rect"][3] > 200]
        return max(wins, key=lambda w: w["rect"][2] * w["rect"][3]) if wins else None

    def front(self, force: bool = False) -> None:
        """Si otra ventana se puso delante, vuelve a traer la del programa (RawFlow solo ve y toca lo de delante)."""
        if not force and time.time() - self._fcheck < 1.5:
            return
        self._fcheck = time.time()
        fg = ws.foreground()
        if fg.get("exe", "").lower() == self.exe.lower():
            return
        w = self.window() or self.main_window()
        if w:
            ws.activate(w["hwnd"], maximize=w["rect"][2] > 900, primary=w["rect"][2] > 900)
            time.sleep(0.5)

    def activate(self) -> None:
        w = self.main_window()
        if not w:
            raise UIError(f"No encuentro la ventana de {self.exe[:-4]}.")
        ws.activate(w["hwnd"], maximize=True, primary=True)
        time.sleep(0.8)

    # ---- leer
    def ocr(self, scale: int = 1) -> List[dict]:
        try:
            self.front()
        except Exception:  # noqa: BLE001
            pass
        w = self.window()
        if not w:
            return []
        self.rect = w["rect"]
        if scale > 1 and max(w["rect"][2], w["rect"][3]) * scale > 9000:   # límite del OCR de Windows (~10.000 px)
            scale = 1
        return ws.ocr(w["rect"], scale=scale)

    def find(self, text: str, exact: bool = False, boxes=None, region=None) -> List[dict]:
        """`region` = (x0, y0, x1, y1) en fracciones de la ventana (0..1): vale para cualquier tamaño de pantalla."""
        t = norm(text)
        res = []
        for b in boxes if boxes is not None else self.ocr():
            n = norm(b["t"]).strip()
            if (n == t or n.strip("•·◉○●◦▪▫◆◇-–—>» ").strip() == t) if exact else (t in n):
                if region and not self.inside(b, region):
                    continue
                res.append(b)
        return res

    def inside(self, b: dict, region) -> bool:
        x, y, w, h = self.rect
        cx, cy = (b["x"] + b["w"] / 2 - x) / max(1, w), (b["y"] + b["h"] / 2 - y) / max(1, h)
        return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]

    def wait(self, *texts: str, timeout: float = 30, exact: bool = False, region=None) -> dict:
        t0, k = time.time(), 0
        while time.time() - t0 < timeout:
            if self.should_stop():
                raise UIError("Cancelado.")
            # una vuelta sí y otra no, leída AMPLIADA ×2: la letra pequeña («Bodas», 12 px) y la blanca sobre rojo
            # («Explorar», «Siguiente») a veces no se leían a tamaño real y el paso 4 se paraba (24-nov-2026)
            boxes = self.ocr(scale=2 if k % 2 else 1)
            k += 1
            for txt in texts:
                f = self.find(txt, exact, boxes, region)
                if f:
                    self._last = (txt, f)
                    return f[0]
            time.sleep(0.2)
        diag(self, "no-aparece-" + texts[0], boxes if "boxes" in locals() else None)
        raise UIError(f"No apareció «{' / '.join(texts)}» en pantalla.")

    # ---- tocar
    def click(self, x: float, y: float, n: int = 1) -> None:
        ws.click(x, y, n)

    def click_box(self, b: dict, after: float = 0.8) -> None:
        ws.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        time.sleep(after)

    def tap(self, *texts: str, timeout: float = 30, exact: bool = False, region=None, pick: int = 0,
            after: float = 0.8) -> dict:
        b = self.wait(*texts, timeout=timeout, exact=exact, region=region)
        boxes = sorted(self._last[1] or [b], key=lambda x: (x["y"], x["x"]))    # pick=-1 → el de más abajo
        b = boxes[pick]
        self.click_box(b, after)
        return b

    def keys(self, combo: str) -> None:
        ws.keys(combo)

    def type(self, text: str) -> None:
        ws.type_text(text)

    def esc(self) -> None:
        ws.keys("esc")


def diag(s: Screen, label: str, boxes=None) -> None:
    """Guarda lo que ve RawFlow cuando algo no aparece (captura + ventana de delante + textos leídos) en
    %APPDATA%/RawFlow/diagnostico (los 40 últimos), para entender el fallo sin tener que repetirlo. El registro dice
    qué captura es (4.25: antes no lo decía y había que adivinarla por la hora)."""
    try:
        import re as _re
        d = WORK / "diagnostico"
        d.mkdir(parents=True, exist_ok=True)
        for old in sorted(d.glob("*"))[:-78]:
            old.unlink()
        label = _re.sub(r"[^\w-]+", "-", norm(label)).strip("-")[:50] or "fallo"
        stem = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
        try:
            s.log(f"   (captura del fallo: {stem.name}.png en «diagnostico»)")
        except Exception:  # noqa: BLE001
            pass
        fg = ws.foreground()
        w = s.window() or s.main_window()
        if w:
            ws.save_png(str(stem) + ".png", ws.capture(w["rect"]))
        txt = [f"{round(b['x'])},{round(b['y'])} {b['t']}" for b in (boxes if boxes is not None else s.ocr())][:120]
        Path(str(stem) + ".txt").write_text(
            f"delante: {fg.get('title')} ({fg.get('exe')}) {fg.get('rect')}\nventana leída: {w and w['title']} "
            f"{w and w['rect']}\nventanas del programa: {[(q['title'], q['rect'], q['min']) for q in ws.windows(exe=s.exe)]}\n"
            + "\n".join(txt), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ la app
def _launch(s: Screen, wait: float = 90) -> None:
    """ImagenAI abierta y delante, a pantalla completa."""
    if not s.main_window():
        p = app_path()
        if not p:
            raise UIError("No encuentro la app ImagenAI en esta PC.")
        s.log("Imagen: abriendo la app…")
        subprocess.Popen([str(p)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
        t0 = time.time()
        while not s.main_window():
            if s.should_stop():
                raise UIError("Cancelado.")
            if time.time() - t0 > wait:
                raise UIError("La app ImagenAI no llegó a abrirse.")
            time.sleep(1)
        time.sleep(4)
    s.activate()


def _sidebar(s: Screen, boxes) -> Optional[dict]:
    side = [b for b in boxes if norm(b["t"]).replace(" ", "") == "proyectos" and s.inside(b, (0, 0.12, 0.06, 0.6))]
    if side:
        return side[0]
    # Imagen abre ahora en «Inicio» y el OCR de Windows a veces NO lee «Proyectos» en la barra lateral (24-nov-2026: 4
    # intentos seguidos sin leerlo; sí leía «Inicio», «Perfiles de IA», «Social Spotlight»). Su botón está siempre entre
    # «Perfiles de IA» y «Social Spotlight»: se toma ese hueco.
    reg = (0, 0.1, 0.08, 0.75)
    perf = [b for b in boxes if norm(b["t"]).strip().startswith("perfiles de") and s.inside(b, reg)]
    soc = [b for b in boxes if norm(b["t"]).strip().startswith("social") and s.inside(b, reg)]
    if perf and soc and soc[0]["y"] - perf[0]["y"] > 3 * perf[0]["h"]:
        a, c = perf[0], soc[0]
        y = (a["y"] + c["y"]) / 2 + 0.4 * a["h"]
        return {"t": "Proyectos", "x": min(a["x"], c["x"]), "y": y, "w": max(a["w"], c["w"]), "h": a["h"]}
    return None


def _clear(s: Screen, timeout: float = 90) -> None:
    """Deja Imagen en una pantalla normal (con su barra lateral): cierra con Esc / «Cancelar» lo que quedara abierto."""
    t0 = time.time()
    while True:
        if s.should_stop():
            raise UIError("Cancelado.")
        boxes = s.ocr()
        w = s.window()
        dialog = bool(w) and w["rect"][2] < 0.7 * (s.main_window() or w)["rect"][2]      # un selector de archivos
        modal = s.find("actualizar ajustes", boxes=boxes) or any(k in norm(b["t"]) for b in boxes for k in WIZARD)
        if _sidebar(s, boxes) and not modal and not dialog:
            return
        if any(k in norm(" ".join(b["t"] for b in boxes)) for k in ("iniciar sesion", "inicia sesion", "log in", "sign in")) \
                and not _sidebar(s, boxes):
            raise UIError("ImagenAI pide iniciar sesión: ábrela, entra con tu cuenta y vuelve a empezar.")
        if time.time() - t0 > timeout:
            diag(s, "imagen-sin-proyectos", boxes)
            raise UIError("Imagen no muestra su pantalla principal («Proyectos»).")
        s.front(force=True)
        if _close_wizard(s, boxes):
            continue
        s.esc()
        time.sleep(0.8)
        now = s.ocr()
        leave = [b for b in now if norm(b["t"]).strip() in ("salir ahora", "exit now")]
        if leave:                                    # «Proyecto aún no creado · Cancelar / Salir ahora»: SALIR (con
            s.click_box(leave[0], after=1.0)         # «Cancelar» el asistente se quedaba abierto y cada reintento fallaba)
            continue
        close = [b for b in now if norm(b["t"]).strip() in ("cancelar", "cerrar", "descartar cambios", "cancel", "close")]
        if close and not _sidebar(s, boxes):
            s.log("Imagen: cerrando una ventana que quedó abierta…")
            s.click_box(max(close, key=lambda b: b["y"]))
        time.sleep(0.8)


WIZARD = ("elige origen", "donde estan tus fotos", "nombra tu proyecto", "fotos seleccionadas", "herramientas seleccionadas",
          "esto es lo que te espera", "descargar todas las ediciones", "tus fotos siguen",   # y la vista previa / descarga
          "tu flujo de trabajo", "elige un tipo de fotografia", "proyecto aun no creado")    # (asistente de nov-2026)


def _close_wizard(s: Screen, boxes=None) -> bool:
    """Si quedó abierto un diálogo de Imagen (el asistente de «Nuevo proyecto», la vista previa de «Revisar Ediciones»
    o el de descarga: Esc no los cierra), se cierra con su ✕. Nunca se pulsa «Descargar». True si había alguno."""
    boxes = boxes if boxes is not None else s.ocr()
    if not any(k in norm(b["t"]) for b in boxes for k in WIZARD):
        return False
    xs = [b for b in boxes if b["t"].strip() in ("x", "X", "×", "✕") and s.inside(b, (0.5, 0.1, 0.85, 0.45))]
    if xs:
        s.click_box(min(xs, key=lambda b: b["y"]), after=1.0)
    else:                                                  # el OCR no leyó la ✕: está en la esquina del diálogo
        x, y, w, h = s.rect
        t = [b for b in boxes if any(k in norm(b["t"]) for k in ("elige origen", "configuracion del", "tu flujo de trabajo",
                                                                 "herramientas ia")) and s.inside(b, (0.25, 0.1, 0.5, 0.3))]
        if t:
            s.click(x + 0.703 * w, t[0]["y"] + t[0]["h"] / 2)
            time.sleep(1.0)
    for b in s.ocr():                                      # «¿Descartar el proyecto?» / «Proyecto aún no creado»
        if norm(b["t"]).strip() in ("descartar", "si, descartar", "salir", "salir ahora", "discard", "exit now"):
            s.click_box(b, after=1.0)
            break
    return True


def _close_uploads(s: Screen) -> None:
    """El panel «Gestionar subidas» tapa la columna de botones de la lista: se cierra con su botón de contraer."""
    for _ in range(3):
        boxes = s.ocr()
        title = s.find("Gestionar subidas", boxes=boxes)
        if not title:
            return
        tb = title[0]
        # su botón de contraer está arriba a la derecha del panel. El borde derecho se saca de lo que hay DENTRO del
        # panel (debajo del título): en la fila del título, a su derecha, está «+ Nuevo proyecto», que no es del panel
        # (23-nov-2026: el clic caía en ese botón y abría «Elige origen» encima de la lista)
        inner = [b["x"] + b["w"] for b in boxes if tb["y"] + 2 * tb["h"] < b["y"] < tb["y"] + 9 * tb["h"]
                 and tb["x"] - tb["h"] < b["x"] and b["x"] + b["w"] < tb["x"] + 46 * tb["h"]]
        right = max(inner, default=tb["x"] + 41.5 * tb["h"])
        s.click(right + 0.2 * tb["h"], tb["y"] + 1.1 * tb["h"])
        time.sleep(1.0)
        _close_wizard(s)
    if s.find("Gestionar subidas"):
        s.esc()
        time.sleep(0.6)


def _open_projects(s: Screen) -> None:
    """Imagen delante, sin ventanas a medias, en la lista de proyectos."""
    _launch(s)
    _clear(s)
    side = _sidebar(s, s.ocr())
    if not side:
        raise UIError("No encuentro «Proyectos» en la barra lateral de Imagen.")
    s.click_box(side, after=1.2)
    s.wait("Archivados", "Nuevo proyecto", timeout=25)
    _close_uploads(s)


def _columns(s: Screen, boxes) -> dict:
    """Dónde empieza cada columna de la lista (por sus títulos): así no depende del tamaño de la ventana."""
    def col(name: str) -> Optional[float]:
        f = [b for b in boxes if norm(b["t"]).strip() == norm(name) and s.inside(b, (0.03, 0.08, 0.95, 0.2))]
        return f[0]["x"] if f else None
    x, _y, w, _h = s.rect
    return {"nombre": col("Nombre del proyecto") or x + 0.08 * w, "perfil": col("Perfil") or x + 0.24 * w,
            "estado": col("Estado") or x + 0.40 * w, "fotos": col("Fotos") or x + 0.57 * w}


def _row(s: Screen, project: str, boxes) -> Optional[dict]:
    """Caja del nombre EXACTO del proyecto en la lista (sin parecidos: «20 nov 2026» nunca vale por
    «20 nov 2026 #2»). Solo si Imagen recorta un nombre largo con «…», vale el principio."""
    c = _columns(s, boxes)
    want = _key(project)
    cand = [b for b in boxes if c["nombre"] - 30 <= b["x"] < c["perfil"] - 10 and s.inside(b, (0, 0.14, 1, 1))]

    def match(t: str) -> bool:
        t, k = t.strip(), _key(t)
        return k == want or (t.endswith(("…", "...")) and len(k) >= 8 and want.startswith(k))
    hits = [b for b in cand if match(b["t"])]
    rows: List[List[dict]] = []                       # un nombre largo puede salir partido en dos cajas de la fila
    for b in sorted(cand, key=lambda q: (q["y"], q["x"])):
        for r in rows:
            if abs(r[0]["y"] - b["y"]) < 0.7 * b["h"]:
                r.append(b)
                break
        else:
            rows.append([b])
    for r in rows:
        if len(r) > 1:
            r.sort(key=lambda q: q["x"])
            t = " ".join(q["t"].strip() for q in r)
            if match(t):
                hits.append(dict(r[0], t=t, w=max(q["x"] + q["w"] for q in r) - r[0]["x"]))
    return min(hits, key=lambda b: b["y"]) if hits else None


def _row_parts(s: Screen, row: dict, boxes):
    """(estado, nº de fotos, cajas de botones) de la fila de un proyecto."""
    c = _columns(s, boxes)
    same = [b for b in boxes if abs((b["y"] + b["h"] / 2) - (row["y"] + row["h"] / 2)) < 1.6 * row["h"]]
    state = " ".join(b["t"] for b in sorted(same, key=lambda q: q["x"]) if c["estado"] - 20 <= b["x"] < c["fotos"] - 20)
    fix = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"})
    count = None
    for b in same:
        t = b["t"].strip().translate(fix)
        if c["fotos"] - 30 <= b["x"] < c["fotos"] + 0.08 * s.rect[2] and t.isdigit():
            count = int(t)
    buttons = [b for b in same if b["x"] > c["fotos"] + 0.08 * s.rect[2]]
    return state, count, buttons


def _find_row(s: Screen, project: str, tries: int = 4):
    boxes: List[dict] = []
    for k in range(tries):
        boxes = s.ocr(scale=2 if k % 2 else 1)            # (ampliada una vuelta sí y otra no: ver Screen.wait)
        r = _row(s, project, boxes)
        if r is not None:
            return r, boxes
        time.sleep(1.2)
    return None, boxes


def free_name(base: str, visible: List[str]) -> str:
    """Primer nombre libre: base, «base #2», «base #3»… (los que se ven en Imagen y los que RawFlow ya creó)."""
    seen = {_key(t) for t in visible}
    try:
        seen |= {_key(line) for line in HIST.read_text(encoding="utf-8").splitlines()}
    except OSError:
        pass
    name, n = base, 1
    while _key(name) in seen:
        n += 1
        name = f"{base} #{n}"
    return name


def _remember(name: str) -> None:
    try:
        HIST.parent.mkdir(parents=True, exist_ok=True)
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(name + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ crear el proyecto
PICK = ("elegir carpeta", "seleccionar carpeta", "select folder", "choose folder")


def _pick_folder(s: Screen, folder: Path) -> None:
    """Selector de carpetas de Windows: se escribe la ruta en «Carpeta:» y se pulsa su botón («Elegir carpeta» en
    Imagen, «Seleccionar carpeta» en Bridge)."""
    picker = lambda: [w for w in ws.windows(exe=s.exe)                                  # noqa: E731
                      if any(k in w["title"].lower() for k in ("carpeta", "folder", "abrir", "examinar"))]
    t0, k, again = time.time(), 0, False
    lab, btn, boxes = [], [], []
    while time.time() - t0 < 30:
        if s.should_stop():
            raise UIError("Cancelado.")
        boxes = s.ocr(scale=2 if k % 2 else 1)           # (ampliada una vuelta sí y otra no: ver Screen.wait)
        k += 1
        w = s.window()
        lab = [b for b in boxes if norm(b["t"]).strip().rstrip(":") in ("carpeta", "folder")
               and b["y"] > w["rect"][1] + 0.6 * w["rect"][3]] if w else []
        btn = [b for b in boxes if norm(b["t"]).strip() in PICK and w and b["y"] > w["rect"][1] + 0.6 * w["rect"][3]]
        if lab and btn:
            break
        if not again and time.time() - t0 > 10 and not picker():   # el clic en «Explorar» no llegó: otra vez
            ex = [b for b in boxes if "explorar" in norm(b["t"])]
            if ex:
                s.log("   (el selector de carpetas no se abrió: pulso «Explorar» otra vez)")
                s.click_box(max(ex, key=lambda q: q["y"]), after=1.5)
            again = True
        time.sleep(0.4)
    else:
        diag(s, "selector-de-carpetas", boxes)
        raise UIError("No se abrió el selector de carpetas.")
    for intento in range(2):
        b = lab[0]
        s.click(b["x"] + b["w"] + 6 * b["h"], b["y"] + b["h"] / 2)          # el campo, a la derecha del rótulo
        time.sleep(0.3)
        s.keys("ctrl+a")
        s.type(str(folder))
        time.sleep(0.5)
        s.click_box(max(btn, key=lambda q: q["y"]), after=1.2)
        t1 = time.time()
        while picker() and time.time() - t1 < 6:         # ¿se cerró el selector? (si no, la ruta no entró)
            time.sleep(0.4)
        if not picker():
            return
        s.log("   (el selector de carpetas sigue abierto: escribo la ruta otra vez)")
        boxes = s.ocr(scale=2)
        lab = [q for q in boxes if norm(q["t"]).strip().rstrip(":") in ("carpeta", "folder")] or lab
        btn = [q for q in boxes if norm(q["t"]).strip() in PICK] or btn
    diag(s, "selector-no-acepta-ruta", boxes)
    raise UIError(f"El selector de carpetas no aceptó la ruta «{folder}».")


def _type_name(s: Screen, name_box: dict, dng_dir: Path, project: str) -> None:
    """Escribe el nombre en «Nombra tu proyecto» y COMPRUEBA que quedó escrito. El clic va sobre el texto que ya trae
    la casilla (el nombre de la carpeta, «2 DNGs»). 25-nov-2026: el clic calculado bajo el rótulo no cayó en la
    casilla, Ctrl+A seleccionó toda la página y el proyecto se creó como «2 DNGs»; luego RawFlow no lo encontraba."""
    h = name_box["h"]
    below = lambda b: 0 < b["y"] - name_box["y"] < 4 * h and -2 * h < b["x"] - name_box["x"] < 30 * h   # noqa: E731
    time.sleep(1.0)                                       # el diálogo termina de aparecer
    for k in range(3):
        boxes = s.ocr(scale=2 if k else 1)
        cur = [b for b in boxes if below(b) and norm(b["t"]).strip() in (norm(dng_dir.name), norm(project))]
        if cur:
            s.click_box(cur[0], after=0.4)                # dentro de la casilla, sobre su texto
        else:
            s.click(name_box["x"] + 7 * h, name_box["y"] + 2.9 * h)
            time.sleep(0.4)
        s.keys("ctrl+a")
        s.type(project)
        time.sleep(0.8)
        if any(norm(project)[:18] in norm(b["t"]) for b in s.ocr(scale=2) if below(b)):
            return
    raise UIError("No pude escribir el nombre del proyecto en Imagen.")


def _create_project(s: Screen, dng_dir: Path, project: str, photo_type: str, pfile: Path) -> str:
    log = s.log
    log(f"Imagen: creando el proyecto «{project}»…")
    for _attempt in range(3):
        s.tap("Nuevo proyecto", timeout=15, region=(0.6, 0.03, 1, 0.14))
        try:
            s.wait("Elige origen", "Dónde están tus fotos", timeout=10)
            break
        except UIError:
            s.esc()
            time.sleep(3)
    else:
        raise UIError("No se abrió «Nuevo proyecto» en Imagen.")
    s.tap("Carpeta", exact=True, timeout=20)
    s.tap("Explorar", timeout=20, pick=-1, after=1.5)                    # el del diálogo (no «Explorar la tienda»)
    _pick_folder(s, dng_dir)
    s.front(force=True)
    name_box = s.wait("Nombra tu proyecto", timeout=90)
    got = [b for b in s.ocr() if norm(b["t"]).startswith("total de fotos")]
    n_dng = len(list(dng_dir.glob("*.dng")))
    m = re.search(r"(\d+)", got[0]["t"]) if got else None
    if m and int(m.group(1)) != n_dng:
        s.esc()
        raise UIError(f"Imagen ve {m.group(1)} fotos en la carpeta y hay {n_dng} DNG: no se crea el proyecto.")
    _type_name(s, name_box, dng_dir, project)
    for n in range(2, 8):                                 # si Imagen dice que el nombre ya existe: «… #2», «… #3»
        if not (s.find("ya existe") or s.find("already exists") or s.find("nombre en uso")):
            break
        project = f"{project.rsplit(' #', 1)[0]} #{n}"
        log(f"Imagen: ese nombre ya existe, uso «{project}».")
        s.keys("ctrl+a")
        s.type(project)
        time.sleep(0.8)
    _remember(project)
    pfile.write_text(project, encoding="utf-8")          # si se corta aquí, el reintento lo busca antes de crear otro
    try:
        s.tap(photo_type, exact=True, timeout=10)
        s.tap("guiente", timeout=10, pick=-1, after=1.2)      # «Siguiente»
        s.wait("Perfil IA", "Selección con IA", timeout=30)
        s.tap("guiente", timeout=10, pick=-1, after=1.2)
        s.wait("Herramientas seleccionadas", "Herramientas IA", timeout=30)
        s.tap("crear proyecto", timeout=10, pick=-1, after=2.5)
    except UIError:
        # No se llegó a pulsar «Crear proyecto»: ese proyecto NO existe. Sin borrar la marca, cada reintento lo
        # buscaba en la lista, no lo encontraba y el evento se quedaba sin salida hasta borrar el archivo a mano.
        pfile.unlink(missing_ok=True)
        raise
    pfile.write_text(project, encoding="utf-8")
    log(f"Imagen: subiendo y editando «{project}»…")
    return project


# ------------------------------------------------------------------ esperar la edición
READY = ("resena", "revisar", "revision", "review", "listo", "lista")
BUSY = ("subiendo", "edicion", "editando", "procesando", "cargando", "en cola", "preparando")


def _wait_ready(s: Screen, dng_dir: Path, project: str, wait_minutes: int) -> None:
    log = s.log
    dngs = list(dng_dir.glob("*.dng"))
    n_dng = len(dngs)
    total = sum(p.stat().st_size for p in dngs) or 1
    up0 = edit_t0 = None
    t0, last, lost = time.time(), "", 0
    while True:
        if s.should_stop():
            raise UIError("Cancelado.")
        if time.time() - t0 > wait_minutes * 60:
            raise UIError("Imagen tardó demasiado en editar. Revisa el proyecto en la app.")
        _close_uploads(s)
        boxes = s.ocr()
        row = _row(s, project, boxes)
        if row is None:
            lost += 1
            if lost == 8:                                  # ¿se salió de la lista? volver a ella
                _open_projects(s)
            if lost > 40:
                raise UIError(f"No encuentro el proyecto «{project}» en la lista de Imagen.")
            time.sleep(1.5)
            continue
        lost = 0
        st, got, buttons = _row_parts(s, row, boxes)
        nst = norm(st)
        btn = norm(" ".join(b["t"] for b in buttons))
        m = re.search(r"(\d{1,3})\s*%", st)
        if "subiendo" in nst and m:
            pct = min(100, int(m.group(1))) / 100
            now = time.time()
            up0 = up0 or (now, pct)
            dt, dp = now - up0[0], pct - up0[1]
            speed = dp * total / dt if dt > 0.5 and dp > 0 else 0
            left = (1 - pct) * total / speed if speed else 0
            _prog(0.02 + 0.30 * pct)
            log(f"Subiendo a Imagen: {int(pct * 100)}%  ·  {speed / 1e6:.1f} MB/s"
                + (f"  ·  quedan {_mmss(left)}" if left else ""))
            live.set("subida", f"{speed / 1e6:.1f} MB/s  ·  {int(pct * 100)} %" + (f"  ·  quedan {_mmss(left)}" if left else ""))
        elif any(k in nst for k in ("edicion", "editando", "procesando")) and "ediciones" not in btn:
            edit_t0 = edit_t0 or time.time()
            el = time.time() - edit_t0
            _prog(0.32 + 0.43 * min(0.97, el / max(60.0, 6.0 * n_dng)))
            if int(el) % 15 < 3:
                log(f"Imagen editando en la nube · {_mmss(el)}")
            live.set("subida", "")
            live.set("imagen", f"editando en la nube  ·  {_mmss(el)}")
        elif st != last:
            log(f"   Imagen: {st.strip(' •') or '…'}")
        last = st
        if any(k in nst for k in ("fallid", "error", "failed")):      # Imagen no pudo con el proyecto: no esperar
            (dng_dir / ".rawflow_imagen_proyecto").unlink(missing_ok=True)
            raise UIError(f"Imagen marcó el proyecto «{project}» como fallido: se crea otro al reintentar.")
        if "ediciones" in btn or (any(k in nst for k in READY) and not any(k in nst for k in BUSY)):
            if got is not None and got < n_dng:            # ANTES de descargar (que es lo que cobra)
                (dng_dir / ".rawflow_imagen_proyecto").unlink(missing_ok=True)
                raise UIError(f"Imagen solo recibió {got} de {n_dng} fotos en «{project}»: "
                              "se crea un proyecto nuevo con todas.")
            return
        time.sleep(1.5 if up0 and not edit_t0 else 3)


# ------------------------------------------------------------------ descargar las ediciones (COBRA)
def _log_file() -> Optional[Path]:
    try:
        logs = sorted(LOGS.glob("renderer*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return logs[-1] if logs else None


def _log_mark():
    p = _log_file()
    try:
        return (p, p.stat().st_size) if p else (None, 0)
    except OSError:
        return (None, 0)


def _same_dir(a: str, b: str) -> bool:
    k = lambda t: os.path.normcase(os.path.normpath(t.replace("/", "\\"))).rstrip("\\")  # noqa: E731
    return k(a) == k(b)


def download_result(mark, dng_dir: Path):
    """Cómo terminó (según el registro de Imagen) la descarga de ediciones a `dng_dir` pedida después de `mark`:
    (estado, correctas, total, código de error), p. ej. ("FAILURE", 0, 120, "DOWNLOAD_XMP_ERROR"). None = aún nada."""
    p0, size0 = mark
    p = _log_file()
    chunks = []
    for f, start in ((p0, size0), (p, 0)) if p0 and p and p != p0 else ((p or p0, size0 if p == p0 else 0),):
        if not f:
            continue
        try:
            with open(f, "rb") as h:
                h.seek(start)
                chunks.append(h.read().decode("utf-8", "replace"))
        except OSError:
            pass
    res = None
    for line in "\n".join(chunks).splitlines():
        if "edit_download_directory_xmp_finished" not in line:
            continue
        m = re.search(r'"path":"((?:[^"\\]|\\.)*)"', line)
        try:
            path = json.loads('"' + m.group(1) + '"') if m else ""
        except ValueError:
            path = m.group(1) if m else ""
        if not _same_dir(path, str(dng_dir)):
            continue
        st = re.search(r'"status":"(\w+)"', line)
        okn = re.search(r'"totalSuccess":(\d+)', line)
        tot = re.search(r'"total":(\d+)', line)
        code = re.search(r'"errorCode":"(\w+)"', line)
        res = (st.group(1) if st else "", int(okn.group(1)) if okn else 0, int(tot.group(1)) if tot else 0,
               code.group(1) if code else "")
    return res


def _open_download_dialog(s: Screen, dng_dir: Path, project: str) -> None:
    """Lista de proyectos → «Revisar Ediciones» del proyecto → diálogo «Descargar ediciones» (comprobando que
    descarga en la carpeta de este evento)."""
    _close_uploads(s)
    row, boxes = _find_row(s, project)
    if not row:
        raise UIError(f"No encuentro el proyecto «{project}» en Imagen.")
    rev: List[dict] = []
    for k in range(6):          # el botón es blanco sobre rojo: a tamaño real el OCR a veces no lo lee (25-nov-2026)
        if k:
            time.sleep(0.8)
            row, boxes = _find_row(s, project, tries=2)
            if not row:
                continue
        _st, _n, buttons = _row_parts(s, row, boxes)
        rev = [b for b in buttons if "revisar" in norm(b["t"]) or "ediciones" in norm(b["t"])]   # a veces «evisar…»
        if rev:
            break
    if not rev:
        diag(s, "sin-revisar-ediciones", boxes)
        raise UIError(f"El proyecto «{project}» aún no ofrece «Revisar Ediciones».")
    s.click_box(rev[0], after=1.2)
    b = s.wait("Descargar todas las ediciones", "Tus fotos siguen", "Descargar ediciones", timeout=20)
    if "todas" in norm(b["t"]):
        s.click_box(b, after=1.2)
        s.wait("Tus fotos siguen", "Descargar ediciones", timeout=20)
    boxes = s.ocr()
    paths = [q["t"] for q in boxes if "\\" in q["t"] and (":" in q["t"] or "dngs" in norm(q["t"]))]
    want = [norm(dng_dir.parent.name), norm(dng_dir.name)]
    if paths and not any(n in norm(t) for t in paths for n in want):
        s.esc()
        raise UIError(f"Imagen quiere descargar en otra carpeta ({paths[0]}), no en «{dng_dir.parent.name}».")


def _await_download(s: Screen, dng_dir: Path, mark, dngs: List[Path]):
    """Espera a que Imagen termine de bajar las ediciones (su registro dice al momento si fue bien o mal). Si
    pregunta dónde revisar, se elige «Bridge (Adobe Camera Raw)»."""
    t0, t_ok, asked = time.time(), None, False
    limit = max(300, 2 * len(dngs))
    res = None
    while time.time() - t0 < limit:
        if s.should_stop():
            raise UIError("Cancelado.")
        res = download_result(mark, dng_dir) if mark[0] else None
        if not asked:
            b = s.find("Bridge (Adobe Camera Raw)") or s.find("Adobe Camera Raw")
            if b:
                s.click_box(b[0], after=1)
                asked = True
        if res and res[0].upper() != "SUCCESS":
            return res
        if res:
            t_ok = t_ok or time.time()
            if asked or time.time() - t_ok > PROMPT_WAIT:
                return res
        elif not mark[0] and not core.without_imagen(dngs):
            return None
        time.sleep(1)
    return res


def _download_edits(s: Screen, dng_dir: Path, project: str) -> None:
    """Baja las ediciones del proyecto a sus DNG. CANDADO: no sale de aquí sin las ediciones de Imagen en TODOS los
    DNG, comprobadas una a una."""
    log = s.log
    dngs = sorted(dng_dir.glob("*.dng"))
    falta: List[Path] = list(dngs)
    for intento in range(1, DL_TRIES + 1):
        _open_download_dialog(s, dng_dir, project)
        _prog(0.78)
        mark = _log_mark()
        s.tap("Descargar ediciones", exact=True, timeout=20, pick=-1, after=1)      # el botón, no el título
        log("Imagen: descargando ediciones a los DNG…" + (f" (intento {intento} de {DL_TRIES})" if intento > 1 else ""))
        live.set("imagen", "descargando las ediciones a los DNG…")
        res = _await_download(s, dng_dir, mark, dngs)
        falta = core.without_imagen(dngs)
        t_w = time.time()
        while falta and (res is None or res[0].upper() == "SUCCESS") and time.time() - t_w < SETTLE_WAIT:
            if s.should_stop():
                raise UIError("Cancelado.")
            time.sleep(2)
            falta = core.without_imagen(falta)
        if not falta:
            log(f"Imagen: ediciones en {len(dngs)} de {len(dngs)} DNG (comprobadas una a una).")
            return
        why = " ".join(x for x in (res or ("sin respuesta de Imagen",)) if isinstance(x, str) and x).strip()
        log(f"⚠ Imagen: la descarga de ediciones falló ({why}): {len(dngs) - len(falta)} de {len(dngs)} DNG "
            "con ediciones. No se sigue sin Imagen.")
        if intento < DL_TRIES:
            _open_projects(s)
            t_w = time.time()
            while time.time() - t_w < DL_WAIT * intento:
                if s.should_stop():
                    raise UIError("Cancelado.")
                time.sleep(1)
    raise UIError(f"Imagen no puso sus ediciones en {len(falta)} de {len(dngs)} fotos de «{project}» "
                  f"(probé {DL_TRIES} veces): sin Imagen no se sigue.")


# ------------------------------------------------------------------ todo el paso
def edit(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False, project: str = "",
         photo_type: str = "Bodas", progress=lambda f: None, charge_ok: bool = True, wait_minutes: int = 90) -> str:
    """Crea (o retoma) el proyecto de Imagen de esa carpeta, espera la edición y descarga las ediciones a los DNG.
    Deja «.rawflow_imagen_ok» para no volver a pedir (ni pagar) esas ediciones. Devuelve el nombre del proyecto."""
    PROG[0] = progress
    dng_dir = Path(event_dir) / dng_subfolder
    dngs = sorted(dng_dir.glob("*.dng"))
    if not dngs:
        raise UIError(f"No hay DNG en «{dng_dir}».")
    mark, pfile = dng_dir / ".rawflow_imagen_ok", dng_dir / ".rawflow_imagen_proyecto"
    prev = pfile.read_text(encoding="utf-8").strip() if pfile.exists() else ""
    if mark.exists():
        if not core.without_imagen(dngs):
            return prev
        mark.unlink(missing_ok=True)
        log("Imagen: la marca decía «hecho» pero faltan ediciones en los DNG: se vuelven a pedir.")
    if no_apps():
        raise UIError("RAWFLOW_SIN_APPS=1: en pruebas no se maneja la app de Imagen.")
    s = Screen(log, should_stop)
    _open_projects(s)
    if prev:
        row, boxes = _find_row(s, prev)
        if row is None:
            # ya se creó (y se subió) ese proyecto: NUNCA se crea otro igual, que se cobraría dos veces
            raise UIError(f"No encuentro el proyecto «{prev}» en la lista de Imagen.")
        got = _row_parts(s, row, boxes)[1]
        if got is not None and got < len(dngs):
            pfile.unlink(missing_ok=True)
            raise UIError(f"Imagen solo tiene {got} de {len(dngs)} fotos en «{prev}»: se crea un proyecto nuevo con todas.")
        project = prev
        log(f"Imagen: retomo el proyecto «{project}» (no se crea otro).")
    else:
        project = free_name(project or Path(event_dir).name, [b["t"] for b in s.ocr()])
        project = _create_project(s, dng_dir, project, photo_type, pfile)
    _wait_ready(s, dng_dir, project, wait_minutes)
    if not charge_ok:
        raise UIError("Imagen terminó de editar; falta descargar las ediciones (cobra).")
    _download_edits(s, dng_dir, project)
    falta = core.without_imagen(dngs)
    if falta:
        raise UIError(f"Imagen no puso sus ediciones en {len(falta)} de {len(dngs)} DNG ({falta[0].name}…).")
    mark.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
    return project


# ------------------------------------------------------------------ Bridge: de los DNG a JPEG
BRIDGE_EXE = "Adobe Bridge.exe"


def bridge_path() -> Optional[Path]:
    if no_apps():
        return None
    from . import programas
    return programas.bridge()


def clean_exports(out_dir: Path) -> int:
    """«DSC0001-DxO_DeepPRIME XD3.jpg» → «DSC0001.jpg». Si ya había una de un intento anterior, la nueva
    la sustituye (nunca quedan fotos repetidas). Devuelve cuántas fotos hay."""
    for p in sorted(out_dir.glob("*.jpg")):
        if "-DxO" in p.stem:
            os.replace(p, out_dir / (p.stem.split("-DxO")[0] + ".jpg"))
    return len([p for p in out_dir.glob("*.jpg") if "-DxO" not in p.stem])


def _open_bridge(s: Screen, dng_dir: Path) -> None:
    exe = bridge_path()
    if not exe:
        raise UIError("No encuentro Adobe Bridge en esta PC.")
    def launch():
        subprocess.Popen([str(exe), str(dng_dir)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
    launch()
    t0 = t_launch = time.time()
    while not s.main_window():
        if s.should_stop():
            raise UIError("Cancelado.")
        if time.time() - t0 > 120:
            raise UIError("Adobe Bridge no llegó a abrirse.")
        if time.time() - t_launch > 25 and not _running(BRIDGE_EXE):   # el arranque se perdió: se lanza otra vez
            launch()
            t_launch = time.time()
        time.sleep(1)
    w = s.main_window()
    ws.activate(w["hwnd"], maximize=True, primary=True)        # en la pantalla principal su letra se lee bien
    time.sleep(1.0)


def _close_extra(s: Screen) -> bool:
    """Cierra las ventanitas de Bridge que tapan la principal («Progreso de la exportación» se abre sola al exportar
    y se queda delante; avisos sueltos). True si cerró alguna."""
    mw = s.main_window()
    closed = False
    for w in ws.windows(exe=s.exe):
        if mw and w["hwnd"] != mw["hwnd"] and w["rect"][2] > 200 and w["rect"][3] > 100:
            ws.close_window(w["hwnd"])
            closed = True
    if closed:
        time.sleep(0.8)
    return closed


def _thumbs(s: Screen, boxes, stems: List[str]) -> List[dict]:
    """Rótulos de las miniaturas de los DNG en el panel de contenido (por el principio de su nombre)."""
    keys = {_key(x)[:7] for x in stems}
    return [b for b in boxes if _key(b["t"])[:7] in keys and s.inside(b, (0.12, 0.2, 0.9, 0.95))]


def bridge_export(s: Screen, dng_dir: Path, out_dir: Path, expected: int, progress=lambda f: None) -> int:
    """Bridge abre la carpeta de DNG, selecciona todo y exporta JPEG (calidad 12) a `out_dir` con
    Archivo → Exportar a → Exportación personalizada. Devuelve cuántos JPEG hay en `out_dir`."""
    log = s.log
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*-DxO*.jpg"):              # restos de un intento anterior a medias
        p.unlink()
    before = {p.name for p in out_dir.glob("*.jpg")}
    stems = [p.stem for p in dng_dir.glob("*.dng")]
    _open_bridge(s, dng_dir)
    log(f"Bridge: exportando JPEG a «{out_dir.name}»…")
    t0, reopened = time.time(), 0
    while True:                                       # las miniaturas tardan unos segundos (en frío, ~25 s)
        if s.should_stop():
            raise UIError("Cancelado.")
        _close_extra(s)                               # «Progreso de la exportación» de otra vez, avisos…
        boxes = s.ocr()
        here = [b for b in boxes if norm(dng_dir.name) in norm(b["t"])]
        thumbs = _thumbs(s, boxes, stems)
        if thumbs and here:
            break
        el = time.time() - t0
        if el > 25 * (reopened + 1) and reopened < 2:  # un Bridge que aún arrancaba ignora la carpeta pedida
            reopened += 1
            _open_bridge(s, dng_dir)
        if el > 120:
            raise UIError("Bridge no muestra los DNG.")
        time.sleep(0.5)
    t = min(thumbs, key=lambda b: (b["y"], b["x"]))
    s.click(t["x"] + t["w"] / 2, t["y"] - 3.5 * t["h"])    # la miniatura (encima de su nombre)
    time.sleep(0.6)
    s.keys("ctrl+a")                                        # todas
    time.sleep(1.0)
    for k in range(3):
        try:
            # (un Bridge recién abierto a veces no despliega el menú al primer clic: se reintenta AQUÍ, sin volver a
            # abrir Bridge ni repetir todo el paso, que costaba ~1 min)
            s.tap("Archivo", exact=True, timeout=10, region=(0, 0, 0.12, 0.07), after=0.8)
            ea = s.tap("Exportar a", exact=True, timeout=8, after=0.9)
            cp = [b for b in s.ocr() if "personalizada" in norm(b["t"]) and abs(b["y"] - ea["y"]) < 3 * ea["h"]
                  and b["x"] > ea["x"] + ea["w"]]
            if cp:
                s.click_box(cp[0], after=1.5)
            s.wait("Opciones de guardado", "Formato de imagen", timeout=10)
            break
        except UIError:
            diag(s, f"bridge-exportar-{k + 1}")
            if k == 2:
                raise UIError("No se abrió «Exportación personalizada» en Bridge.")
            s.esc()
            s.esc()
            time.sleep(0.8)
    # Guardar en → Carpeta específica → Explorar → ruta
    boxes = s.ocr()
    if not s.find("Carpeta específica", boxes=boxes):
        if not s.find("Guardar en", exact=True, boxes=boxes):          # «Opciones de guardado» está plegado: abrirlo
            og = s.wait("Opciones de guardado", timeout=5)
            s.click(og["x"] - 1.8 * og["h"], og["y"] + og["h"] / 2)
            time.sleep(0.7)
        s.tap("Ubicación del archivo original", timeout=8, after=0.8)
        s.tap("Carpeta específica", timeout=6, after=0.8)
    s.tap("Explorar", exact=True, timeout=10, after=1.2)
    _pick_folder(s, out_dir)
    # plegar «Opciones de guardado» para ver la calidad y ponerla en 12
    og = s.wait("Opciones de guardado", timeout=10)
    for _ in range(3):
        if not s.find("Guardar en", exact=True):
            break
        s.click(og["x"] - 1.8 * og["h"], og["y"] + og["h"] / 2)        # la flecha, a la izquierda del rótulo
        time.sleep(0.7)
    fmt = s.wait("Formato", exact=True, timeout=10)
    boxes = s.ocr()
    if not [b for b in boxes if norm(b["t"]).strip() == "jpeg" and 0 < b["y"] - fmt["y"] < 4 * fmt["h"]]:
        s.click(fmt["x"] + 8 * fmt["h"], fmt["y"] + 3 * fmt["h"])
        s.tap("JPEG", exact=True, timeout=5, after=1)
    q = s.wait("Calidad de imagen", timeout=10)

    def quality(bs):
        row = [b for b in bs if 0.8 * q["h"] < b["y"] - q["y"] < 3.5 * q["h"] and b["x"] > q["x"]]
        num = [b for b in row if b["t"].strip().isdigit() and 1 <= int(b["t"].strip()) <= 12]
        return (num[0] if num else None), any(norm(b["t"]).strip() == "maxima" for b in row)
    num, top = quality(s.ocr())
    if not (num and num["t"].strip() == "12" and top):
        if not num:
            raise UIError("No encuentro la calidad JPEG en el diálogo de Bridge.")
        s.click(num["x"] + num["w"] / 2, num["y"] + num["h"] / 2)
        time.sleep(0.3)
        s.keys("ctrl+a")
        s.type("12")
        s.keys("tab")
        time.sleep(0.8)
        num, top = quality(s.ocr())
        if not (top or (num and num["t"].strip() == "12")):
            raise UIError("No pude poner la calidad JPEG en máxima en Bridge.")
    go = [b for b in s.ocr() if norm(b["t"]).strip() == "exportar" and s.inside(b, (0.75, 0, 1, 0.3))]
    if not go:
        raise UIError("No encuentro el botón «Exportar» del diálogo de Bridge.")
    s.click_box(go[0], after=0.8)
    t0 = last_t = time.time()
    last_n = 0
    while True:                                  # vigilante: sin JPEG nuevo mucho más de lo normal → reintento
        new = [p for p in out_dir.glob("*.jpg") if p.name not in before]
        if len(new) >= expected and core.files_settled(new, 0.5):
            break
        if s.should_stop():
            raise UIError("Cancelado.")
        if len(new) != last_n:
            last_n, last_t = len(new), time.time()
            progress(last_n / max(1, expected))
            el = last_t - t0
            log(f"Bridge: {last_n} de {expected} JPEG" + (f"  ·  quedan {_mmss(el / last_n * (expected - last_n))}"
                                                           if last_n else ""))
            if last_n:
                live.set("bridge", f"{last_n:,} de {expected:,} JPEG  ·  {el / last_n:.1f} s/foto  ·  calidad 12")
        per = (last_t - t0) / last_n if last_n else 0
        if time.time() - last_t > (max(120, per * 8) if last_n else 5 * 60):
            raise UIError(f"Bridge dejó de exportar ({last_n} de {expected}).")
        time.sleep(0.5)
    n = clean_exports(out_dir)
    time.sleep(1.0)
    _close_extra(s)                                   # la ventana de progreso que deja abierta
    log(f"✓  Bridge: {n} JPEG en «{out_dir.name}».")
    live.set("bridge", "")
    return n


def close_app(exe: str, wait: float = 10) -> None:
    """Pide a la app que se cierre (como pulsar la X); no se fuerza: si pregunta algo, se queda abierta."""
    try:
        subprocess.run(["taskkill", "/IM", exe], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        return
    t0 = time.time()
    # Hasta que el PROCESO sale, no solo su ventana: Bridge quita la ventana en ~1 s pero tarda ~2 s en salir, y si
    # se abría otra vez en ese hueco el arranque nuevo se perdía («no llegó a abrirse» y 120 s de espera; 24-nov-2026).
    while (ws.windows(exe=exe) or _running(exe)) and time.time() - t0 < wait:
        time.sleep(0.5)


def _running(exe: str) -> bool:
    """¿Hay algún proceso con ese nombre?"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH", "/FO", "CSV"], capture_output=True,
                           text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return exe.lower() in r.stdout.lower()
    except Exception:  # noqa: BLE001
        return False


def run(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False, photo_type: str = "Bodas",
        out_dir: Optional[Path] = None, progress=lambda f: None, charge_ok: bool = True) -> Path:
    """Todo el paso en Windows con la app: Imagen edita y descarga las ediciones a los DNG, y Bridge exporta los
    JPEG a `out_dir`. Lo que ya estaba hecho no se repite (ni se vuelve a pagar)."""
    event_dir = Path(event_dir)
    dng_dir = event_dir / dng_subfolder
    out = Path(out_dir) if out_dir else event_dir / core.IMAGEN_SUBFOLDER
    dngs = sorted(dng_dir.glob("*.dng"))
    edit(event_dir, dng_subfolder, log, should_stop, photo_type=photo_type,
         progress=lambda f: progress(0.8 * f), charge_ok=charge_ok)
    falta = core.without_imagen(dngs)
    if falta:                                    # CANDADO: nunca a Bridge una foto sin las ediciones de Imagen
        raise UIError(f"{len(falta)} DNG sin las ediciones de Imagen: no se exportan.")
    close_app(EXE)
    bridge_export(Screen(log, should_stop, BRIDGE_EXE), dng_dir, out, len(dngs),
                  progress=lambda f: progress(0.8 + 0.2 * f))
    close_app(BRIDGE_EXE)
    return out


def bridge_only(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False,
                out_dir: Optional[Path] = None, progress=lambda f: None) -> Path:
    """Imagen AI DESACTIVADO: Bridge exporta los DNG tal cual (sin Imagen) a `out_dir`."""
    event_dir = Path(event_dir)
    dng_dir = event_dir / dng_subfolder
    out = Path(out_dir) if out_dir else event_dir / core.JPEG_SIN_SUBFOLDER
    bridge_export(Screen(log, should_stop, BRIDGE_EXE), dng_dir, out, len(list(dng_dir.glob("*.dng"))), progress)
    close_app(BRIDGE_EXE)
    return out

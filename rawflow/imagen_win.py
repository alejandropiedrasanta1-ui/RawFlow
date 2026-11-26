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
RETAP_AFTER = 20      # segundos con el botón «Descargar ediciones» aún en pantalla antes de pulsarlo otra vez
RETAP_MAX = 3         # …como mucho estas veces


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
        self._blind = False           # True: la última vez Windows no dejó la ventana delante (no se pulsa a ciegas)
        try:
            ws.APP_EXES.add(exe.lower())  # modo tranquilo: sus ventanas van al monitor de trabajo y no son «de la persona»
        except Exception:  # noqa: BLE001
            pass

    # ---- ventana
    def window(self) -> Optional[dict]:
        """La ventana del programa que está más delante (su diálogo, si tiene uno abierto; si no, la principal)."""
        wins = [w for w in ws.windows(exe=self.exe) if w["rect"][2] > 300 and w["rect"][3] > 200 and not w["min"]]
        return wins[0] if wins else None

    def main_window(self) -> Optional[dict]:
        allw = ws.windows(exe=self.exe)
        wins = [w for w in allw if w["rect"][2] > 300 and w["rect"][3] > 200]
        if not wins:
            # 25-nov-2026: Imagen MINIMIZADA mide 199×34 y no contaba como ventana: RawFlow dejaba de verla y esperaba
            # 20 min una descarga que no había empezado. Minimizada también vale (front() la devuelve a su sitio).
            wins = [w for w in allw if w["min"] and w["title"].strip()]
        return max(wins, key=lambda w: w["rect"][2] * w["rect"][3]) if wins else None

    _SYSTEM = ("explorer.exe", "rawflow.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
               "searchhost.exe", "searchapp.exe", "textinputhost.exe", "applicationframehost.exe", "dwm.exe")

    def _covering(self) -> List[dict]:
        """Ventanas de OTROS programas que tapan (más del 3 %) la del programa."""
        w = self.window()
        if not w:
            return []
        x, y, ww, hh = w["rect"]
        res = []
        for o in ws.windows():                                   # de delante hacia atrás
            if o["hwnd"] == w["hwnd"]:
                break
            exe = (o.get("exe") or "").lower()
            system = self._SYSTEM if not ws.QUIET[0] else tuple(e for e in self._SYSTEM if e not in (
                "explorer.exe", "applicationframehost.exe"))     # (modo tranquilo: ya no se pone delante para leer:
            if o["min"] or not exe or exe == self.exe.lower() or exe in system:   # una carpeta abierta la taparía)
                continue
            if o.get("pid") == os.getpid():                      # RawFlow misma (también desde el código, con
                continue                                         # python.exe): su pantalla y su botón «Parar» no
            if exe == "explorer.exe" and not o.get("title"):     # (el escritorio y la barra de tareas, nunca)
                continue
            ox, oy, ow, oh = o["rect"]
            ix, iy = min(x + ww, ox + ow) - max(x, ox), min(y + hh, oy + oh) - max(y, oy)
            if ix > 0 and iy > 0 and ix * iy > 0.03 * ww * hh:
                res.append(o)
        return res

    def _uncover(self) -> bool:
        """Minimiza las ventanas de OTROS programas que tapan la del programa. 25-nov-2026: una ventana «siempre
        visible» (Claude) quedaba encima de Imagen aunque Imagen estuviera delante; RawFlow leía en pantalla el texto
        de esa ventana, sus clics caían en ella y el paso 4 se paró varias veces. Solo se minimiza (nada se cierra).
        26-nov-2026: se COMPRUEBA que quedó al descubierto (antes no se miraba) y, si no, queda dicho en el registro.
        True = nada la tapa."""
        try:
            tapan = self._covering()
            for o in tapan:
                self.log(f"   («{o['title'][:40] or o.get('exe')}» tapaba la ventana: la minimizo)")
                ws.user32.ShowWindow(o["hwnd"], 6)               # SW_MINIMIZE
                time.sleep(0.3)
            if not tapan:
                return True
            siguen = self._covering()
            for o in siguen:
                self.log(f"   ⚠ «{o['title'][:40] or o.get('exe')}» sigue tapando la ventana (no se deja minimizar)")
            return not siguen
        except Exception:  # noqa: BLE001
            return True

    def _is_front(self) -> bool:
        return (ws.foreground().get("exe") or "").lower() == self.exe.lower()

    def front(self, force: bool = False) -> None:
        """Si otra ventana se puso delante, vuelve a traer la del programa (RawFlow solo ve y toca lo de delante).
        26-nov-2026: lo que devuelve ws.activate no se miraba; si Windows no la deja delante se prueba UNA vez más y,
        si tampoco, UIError: antes se seguía y los clics caían a ciegas en la ventana de otro programa."""
        if not force and time.time() - self._fcheck < 1.5:
            return
        if self.should_stop():
            raise UIError("Cancelado.")
        self._fcheck = time.time()
        ws.keep_on_work(self.exe)                  # modo tranquilo: sus ventanas, en el monitor de trabajo
        self._uncover()
        if self._is_front():
            self._blind = False
            return
        w = self.window() or self.main_window()
        if not w:
            return
        for k in range(2):
            if k:
                self.log(f"   (Windows no deja {self.exe[:-4]} delante: lo intento otra vez)")
                self._uncover()
                time.sleep(0.6)
                w = self.window() or self.main_window() or w
            big = w["rect"][2] > 900 or w["min"]          # minimizada: es la principal, vuelve maximizada
            ok = ws.activate(w["hwnd"], maximize=big, primary=big)
            time.sleep(0.5)
            if ok or self._is_front():
                self._blind = False
                return
        self._not_front()

    def _not_front(self) -> None:
        """No queda delante: una captura (SIN volver a leer la pantalla: diag→ocr→front se llamaba a sí mismo en
        bucle durante minutos, revisión 26-nov-2026) y UIError. La próxima vez se espera 1,5 s antes de reintentar."""
        self._blind = True
        self._fcheck = time.time()
        if time.time() - getattr(self, "_diag_front_t", 0.0) > 60:      # una captura por minuto, no una por intento
            self._diag_front_t = time.time()
            diag(self, "no-queda-delante", boxes=[])
        raise UIError(f"No consigo poner {self.exe[:-4]} delante (otra ventana no la deja): no pulso a ciegas.")

    def activate(self) -> None:
        w = self.main_window()
        if not w:
            raise UIError(f"No encuentro la ventana de {self.exe[:-4]}.")
        for k in range(2):                                # 26-nov-2026: antes no se miraba si quedó delante
            if ws.activate(w["hwnd"], maximize=True, primary=True) or self._is_front():
                time.sleep(0.8)
                return
            self._uncover()
            time.sleep(0.8)
        self._not_front()

    def _sure_front(self) -> None:
        """Antes de pulsar o escribir: si la última vez la ventana no quedó delante (el fallo se tragó en una lectura),
        se intenta otra vez y, si no, UIError (26-nov-2026: nada de clics a ciegas en otra ventana)."""
        if getattr(self, "_blind", False):
            self.front(force=True)

    # ---- leer
    def ocr(self, scale: int = 1) -> List[dict]:
        try:
            if ws.QUIET[0]:
                # modo tranquilo: para LEER no hace falta tenerla delante (está debajo de la pantalla de proceso, que
                # no sale en la captura); no se le quita el teclado a la persona. Solo en su monitor y destapada.
                if time.time() - self._fcheck >= 1.5:
                    self._fcheck = time.time()
                    ws.keep_on_work(self.exe)
                    self._uncover()
                    w0 = self.window()
                    if w0 is None and (self.main_window() or {}).get("min"):
                        self.front(force=True)     # minimizada: hay que devolverla a su sitio
            else:
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

    def ocr_tiles(self, scale: int = 3, cols: int = 3, rows: int = 2) -> List[dict]:
        """La ventana leída en trozos (con algo de solape) ampliados `scale` veces: para la letra muy pequeña (en un
        monitor de 1080p al 100 % los rótulos de Imagen miden ~9 px). Mismo formato que ocr()."""
        self.ocr(scale=1)                           # (pone self.rect y, en modo normal, la ventana delante)
        w = self.window()
        if not w:
            return []
        x, y, ww, hh = w["rect"]
        out, seen = [], set()
        tw, th = ww / cols, hh / rows
        for r in range(rows):
            for c in range(cols):
                rx, ry = int(x + max(0, c * tw - 0.06 * tw)), int(y + max(0, r * th - 0.06 * th))
                rect = (rx, ry, int(min(x + ww, x + (c + 1) * tw + 0.06 * tw) - rx),
                        int(min(y + hh, y + (r + 1) * th + 0.06 * th) - ry))
                try:
                    for b in ws.ocr(rect, scale=scale):
                        key = (norm(b["t"]).strip(), round(b["x"] / 8), round(b["y"] / 8))
                        if key not in seen:
                            seen.add(key)
                            out.append(b)
                except Exception:  # noqa: BLE001
                    continue
        return out

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
            # («Explorar», «Siguiente») a veces no se leían a tamaño real y el paso 4 se paraba (24-nov-2026).
            # 26-nov-2026 (prueba de 5 fotos en el monitor de 1080p): ahí «Bodas» mide ~9 px y ni ×2 la leía; cada
            # tercera vuelta se lee POR TROZOS a ×3 (la ventana entera ampliada pierde la letra pequeña; un trozo, no).
            boxes = self.ocr_tiles(3) if k % 3 == 2 else self.ocr(scale=2 if k % 3 == 1 else 1)
            k += 1
            for txt in texts:
                f = self.find(txt, exact, boxes, region)
                if f:
                    self._last = (txt, f)
                    return f[0]
            if self.exe == EXE and k % 4 == 3:          # un anuncio de Imagen encima lo tapa todo (26-nov-2026)
                try:
                    _close_promo(self, boxes)
                except UIError:
                    raise
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.2)
        diag(self, "no-aparece-" + texts[0], boxes if "boxes" in locals() else None)
        raise UIError(f"No apareció «{' / '.join(texts)}» en pantalla.")

    # ---- tocar
    def _ready(self) -> None:
        """Antes de pulsar o escribir. Modo tranquilo: recuerda la ventana de la persona, espera a que esté quieta,
        trae la app delante solo si no lo está y vuelve a mirar que la persona siga quieta JUSTO antes de actuar (el
        teclado se le devuelve después: ws.end_action)."""
        ws.begin_action(self.should_stop)
        if ws.QUIET[0]:
            if not self._is_front():
                self.front(force=True)
            ws.wait_user_idle(1.0, self.should_stop)
        else:
            self._sure_front()

    def _guard(self):
        """Modo tranquilo: mientras se escribe, la app sigue delante (si la persona pulsa en su ventana, se para)."""
        return self._is_front if ws.QUIET[0] else None

    def _act(self, fn) -> None:
        """Una acción de ratón o teclado con su antes y su después; si la persona se llevó el foco a mitad, UIError
        (se repite el intento) en vez de seguir escribiendo en su ventana. (Revisión 26-nov-2026: si front() fallaba,
        end_action no se llamaba y el teclado ya no se le devolvía nunca.)"""
        try:
            self._ready()
            fn()
        except ws.FocusLost as e:
            raise UIError(f"Se interrumpió: {e}.")
        finally:
            ws.end_action()

    def holding(self):
        """`with s.holding():` varias acciones seguidas (un menú) cuentan como UNA: el teclado no se le devuelve a la
        persona a mitad (cerraría el menú) y el cursor no vuelve a su sitio hasta el final."""
        import contextlib
        scr = self

        @contextlib.contextmanager
        def _cm():
            ws.begin_action(scr.should_stop)
            scr._hold = getattr(scr, "_hold", 0) + 1
            try:
                yield
            finally:
                scr._hold -= 1
                ws.end_action()
                if not scr._hold:
                    ws.restore_cursor()
        return _cm()

    def click(self, x: float, y: float, n: int = 1, restore: bool = True) -> None:
        restore = restore and not getattr(self, "_hold", 0)
        self._act(lambda: ws.click(x, y, n, restore=restore))

    def click_box(self, b: dict, after: float = 0.8, restore: bool = True) -> None:
        restore = restore and not getattr(self, "_hold", 0)
        self._act(lambda: ws.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, restore=restore))
        time.sleep(after)

    def tap(self, *texts: str, timeout: float = 30, exact: bool = False, region=None, pick: int = 0,
            after: float = 0.8) -> dict:
        b = self.wait(*texts, timeout=timeout, exact=exact, region=region)
        boxes = sorted(self._last[1] or [b], key=lambda x: (x["y"], x["x"]))    # pick=-1 → el de más abajo
        b = boxes[pick]
        self.click_box(b, after)
        return b

    def keys(self, combo: str) -> None:
        self._act(lambda: ws.keys(combo, guard=self._guard()))

    def type(self, text: str) -> None:
        self._act(lambda: ws.type_text(text, guard=self._guard()))

    def esc(self) -> None:
        if ws.QUIET[0]:                             # (un Esc con la ventana de la persona delante le cerraría algo)
            self._act(lambda: ws.keys("esc", guard=self._guard()))
        else:
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
    if not soc:
        # 25-nov-2026 (evento de 585 fotos parado en el paso 4, 4 intentos): el OCR tampoco leyó «Social», solo su
        # segunda línea, «Spotlight». Vale igual: está una línea más abajo.
        soc = [dict(b, y=b["y"] - b["h"]) for b in boxes
               if norm(b["t"]).strip().startswith("spotlight") and s.inside(b, reg)]
    if perf and soc and soc[0]["y"] - perf[0]["y"] > 3 * perf[0]["h"]:
        a, c = perf[0], soc[0]
        y = (a["y"] + c["y"]) / 2 + 0.4 * a["h"]
        return {"t": "Proyectos", "x": min(a["x"], c["x"]), "y": y, "w": max(a["w"], c["w"]), "h": a["h"]}
    return None


def _tap_next(s: Screen, timeout: float = 12) -> None:
    """«Siguiente» del asistente de Nuevo proyecto. 26-nov-2026 (prueba de 5 fotos, monitor de 1080p): el botón,
    blanco sobre rojo, no se leía y «guiente» pescaba el encabezado «Seguiente paso» de la lista de detrás: el clic
    caía ahí y el asistente no avanzaba. Ahora: el botón por su texto (nunca «… paso») y, si no se lee, por su sitio,
    justo a la derecha de «Atrás» (que sí se lee), en la misma fila."""
    t0, k = time.time(), 0
    while time.time() - t0 < timeout:
        if s.should_stop():
            raise UIError("Cancelado.")
        boxes = s.ocr_tiles(3) if k % 3 == 2 else s.ocr(scale=2 if k % 3 == 1 else 1)
        k += 1
        btn = [b for b in boxes if "guiente" in norm(b["t"]) and "paso" not in norm(b["t"])
               and s.inside(b, (0.4, 0.5, 1.0, 1.0))]
        if btn:
            s.click_box(max(btn, key=lambda b: (b["y"], b["x"])), after=1.2)
            return
        back = [b for b in boxes if norm(b["t"]).strip() in ("atras", "back") and s.inside(b, (0.4, 0.5, 1.0, 1.0))]
        if back and k >= 2:
            a = max(back, key=lambda b: b["y"])
            s.log("   («Siguiente» no se lee: lo pulso por su sitio, a la derecha de «Atrás»)")
            s.click(a["x"] + a["w"] + 5.0 * a["h"], a["y"] + a["h"] / 2)
            time.sleep(1.2)
            return
        time.sleep(0.3)
    diag(s, "sin-siguiente")
    raise UIError("No encuentro «Siguiente» en el asistente de Imagen.")


def _find_sidebar(s: Screen, boxes=None) -> Optional[dict]:
    """«Proyectos» en la barra lateral; si a tamaño real no se lee, otra lectura ×2. (26-nov-2026, prueba de 5 fotos en
    el monitor izquierdo, 1920×1080 al 100 %: la letra pequeña de la barra no salía a tamaño real y el paso 4 se
    paraba con «Imagen no muestra su pantalla principal».)"""
    side = _sidebar(s, boxes if boxes is not None else s.ocr())
    if side:
        return side
    # Leer SOLO la franja de la barra, ampliada: medido en vivo (1938×1098 en el monitor de 1080p) la ventana entera
    # ×2 no leía nada de la barra; la franja ×2 lee «Perfiles de» y «Social», y ×3 ya lee «Proyectos».
    w = s.window()
    if w:
        s.rect = w["rect"]
        x, y, ww, hh = w["rect"]
        strip = (x, y + int(0.08 * hh), max(40, int(0.07 * ww)), int(0.6 * hh))
        for sc in (3, 2):
            try:
                side = _sidebar(s, ws.ocr(strip, scale=sc))
            except Exception:  # noqa: BLE001
                side = None
            if side:
                return side
    return _sidebar(s, s.ocr(scale=2))


PROMO = ("black friday", "por tiempo limitado", "oferta", "limitless", "% de descuento", "ahorra un", "suscribete",
         "mejora tu plan", "upgrade", "special offer", "limited time")
PROMO_NO = ("quiza el ano que viene", "quiza mas tarde", "ahora no", "mas tarde", "no, gracias", "no gracias",
            "maybe later", "not now", "no thanks")


def _close_promo(s: Screen, boxes) -> bool:
    """Imagen saca a veces un anuncio encima de todo («Un año de Limitless: −30 %… Black Friday», 26-nov-2026) que
    tapa la barra lateral y no se cierra con Esc. Se cierra con su botón de RECHAZAR («Quizá el año que viene», «Ahora
    no»…) o con su ✕; NUNCA se pulsa el de comprar. True si había uno."""
    text = norm(" ".join(b["t"] for b in boxes))
    if not any(k in text for k in PROMO):
        return False
    no = [b for b in boxes if any(k in norm(b["t"]) for k in PROMO_NO)]
    if no:
        s.log("Imagen: cierro un anuncio que tapaba la pantalla («" + no[0]["t"].strip()[:40] + "»)")
        s.click_box(no[0], after=1.0)
        return True
    xs = [b for b in boxes if b["t"].strip() in ("x", "X", "×", "✕") and s.inside(b, (0.3, 0.08, 1.0, 0.95))]
    if xs:
        s.log("Imagen: cierro un anuncio que tapaba la pantalla (✕)")
        s.click_box(min(xs, key=lambda b: b["y"]), after=1.0)
        return True
    return False


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
        if not modal and not dialog and _find_sidebar(s, boxes):
            # (un anuncio a la vista NO impide seguir: el de la columna derecha no tapa nada; si hay uno, se intenta
            # cerrar y se sigue. Prueba de 5 fotos, 26-nov-2026: exigir «sin anuncio» dejaba el paso esperando)
            _close_promo(s, boxes)
            return
        if any(k in norm(" ".join(b["t"] for b in boxes)) for k in ("iniciar sesion", "inicia sesion", "log in", "sign in")) \
                and not _sidebar(s, boxes):
            raise UIError("ImagenAI pide iniciar sesión: ábrela, entra con tu cuenta y vuelve a empezar.")
        if time.time() - t0 > timeout:
            diag(s, "imagen-sin-proyectos", boxes)
            raise UIError("Imagen no muestra su pantalla principal («Proyectos»).")
        s.front(force=True)
        if _close_promo(s, boxes) or _close_promo(s, s.ocr(scale=2)):
            continue
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
    side = _find_sidebar(s)
    if not side:
        raise UIError("No encuentro «Proyectos» en la barra lateral de Imagen.")
    # 25-nov-2026: con otra ventana con el foco (el Administrador de tareas), el clic en «Proyectos» no entraba y
    # RawFlow esperaba 25 s una lista que no iba a salir: 4 intentos perdidos seguidos. Ahora, si a los 5 s no sale,
    # trae Imagen delante y vuelve a pulsar (hasta 4 veces) antes de darlo por fallido.
    for k in range(4):
        s.front(force=True)
        s.click_box(side, after=1.2)
        t0 = time.time()
        while time.time() - t0 < 5:
            if s.should_stop():
                raise UIError("Cancelado.")
            boxes = s.ocr(scale=2 if k % 2 else 1)
            if s.find("Archivados", boxes=boxes) or s.find("Nuevo proyecto", boxes=boxes):
                _close_uploads(s)
                return
            time.sleep(0.4)
        _close_promo(s, s.ocr())                     # (un anuncio que salió justo ahora taparía la lista)
        side = _find_sidebar(s) or side
    s.wait("Archivados", "Nuevo proyecto", timeout=10)
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
    """Primer nombre libre: base, «base n2», «base n3»… (los que se ven en Imagen y los que RawFlow ya creó).
    25-nov-2026: antes «base #2», pero el OCR de Windows NO lee «#2» en Imagen («25 nov 2026 #2» → «25 nov 2026», a
    cualquier tamaño y con el cursor donde sea): el nombre quedaba bien escrito y RawFlow creía que no (paso 4 parado
    4 intentos, dos veces). «n2» lo lee siempre y no se confunde con un evento llamado «… 2»."""
    seen = {_key(t) for t in visible}
    try:
        seen |= {_key(line) for line in HIST.read_text(encoding="utf-8").splitlines()}
    except OSError:
        pass
    name, n = base, 1
    while _key(name) in seen:
        n += 1
        name = f"{base} n{n}"
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


NAME_STAY = 3.0          # segundos que el nombre escrito debe seguir en la casilla para darlo por bueno


def _name_look(s: Screen, name_box: dict, scale: int):
    """(rótulo «Nombra tu proyecto» —el de antes si no se lee—, su alto, lo leído en la casilla justo debajo)."""
    boxes = s.ocr(scale=scale)
    lab = s.find("Nombra tu proyecto", boxes=boxes)
    nb = lab[0] if lab else name_box
    h = nb["h"]
    return nb, h, [b for b in boxes if 1.2 * h < b["y"] - nb["y"] < 3.8 * h and -2 * h < b["x"] - nb["x"] < 30 * h]


def _name_written(s: Screen, name_box: dict, project: str) -> bool:
    """¿Está `project` escrito en la casilla del nombre? El cursor parpadea pegado al final y el OCR a veces lee
    «… n2» como «… n?» o «… r-,21» (visto en vivo): con el principio exacto y ALGO detrás vale (lo escribimos
    nosotros sobre toda la casilla; la propuesta de Imagen, «2 DNGs 0517», no empieza así). Sin «n2» detrás
    («25 nov 2026» a secas) no vale."""
    full = norm(project).strip()
    base = norm(re.sub(r" n[0-9]+$", "", project)).strip()
    tight = lambda t: t.replace(" ", "")                  # noqa: E731
    for scale in (2, 1):
        for b in _name_look(s, name_box, scale)[2]:
            t = norm(b["t"]).strip()
            if full[:18] in t or (base != full and t.startswith(base) and len(tight(t)) > len(tight(base))):
                return True
    return False


def _name_still_there(s: Screen, name_box: dict, project: str) -> None:
    """Justo antes de «Siguiente»: el nombre SIGUE en la casilla. 26-nov-2026 (revisión del 25-nov): Imagen puede
    recolocar el diálogo y poner su propuesta también DESPUÉS de comprobarlo (al elegir el tipo de foto, o con el
    aviso de nombre repetido); se creaba el proyecto con el nombre de Imagen y el reintento ya no lo encontraba.
    Si no está, se vuelve a escribir (y a comprobar). Sin el rótulo en pantalla no se toca nada (no se sabe dónde
    está la casilla)."""
    if not s.find("Nombra tu proyecto", boxes=s.ocr(scale=2)) and not s.find("Nombra tu proyecto"):
        s.log("   (no leo «Nombra tu proyecto» para comprobar el nombre: sigo)")
        return
    if _name_written(s, name_box, project):
        return
    s.log(f"   (Imagen cambió el nombre del proyecto: vuelvo a escribir «{project}»)")
    try:
        _type_name(s, name_box, Path("."), project)
    except UIError as e:                       # (revisión 26-nov-2026: una comprobación EXTRA no debe tumbar una
        if str(e) == "Cancelado.":             # creación de proyecto que antes salía bien; se sigue y se avisa)
            raise
        s.log(f"   ⚠ no pude confirmar el nombre «{project}» ({e}): sigo")


def _type_name(s: Screen, name_box: dict, dng_dir: Path, project: str) -> None:
    """Escribe el nombre en «Nombra tu proyecto» y COMPRUEBA que quedó escrito Y QUE SIGUE AHÍ.
    25-nov-2026 (visto en vivo, paso 4 parado cuatro veces): unos 9 s después de abrirse el diálogo, Imagen recoloca
    su contenido (la casilla subió 49 px) y CAMBIA lo escrito por el nombre que ella propone («2 DNGs 0517»). RawFlow
    escribía bien el nombre, Imagen lo pisaba, y los reintentos iban al sitio antiguo: caían fuera de la casilla y
    Ctrl+A seleccionaba toda la página. Ahora en cada vuelta se vuelve a buscar el rótulo (la casilla está justo
    debajo, tenga dentro el ejemplo, el nombre de la carpeta o lo que sea) y el nombre solo vale si aguanta."""
    written = lambda: _name_written(s, name_box, project)     # noqa: E731
    time.sleep(1.0)                                       # el diálogo termina de aparecer
    boxes = None
    for k in range(7):
        if s.should_stop():
            raise UIError("Cancelado.")
        nb, h, inside = _name_look(s, name_box, 2 if k % 2 else 1)
        if inside:
            s.click_box(min(inside, key=lambda b: b["x"]), after=0.4)      # dentro de la casilla, sobre su texto
        else:
            s.click(nb["x"] + 7 * h, nb["y"] + 2.9 * h)                    # casilla vacía o sin leer: por su sitio
            time.sleep(0.4)
        s.keys("ctrl+a")
        s.type(project)
        time.sleep(0.8)
        if written():
            time.sleep(NAME_STAY)                         # ¿lo pisa Imagen con su propuesta?
            if written():
                return
    diag(s, "imagen-nombre-proyecto", boxes)
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
    for n in range(2, 8):                                 # si Imagen dice que el nombre ya existe: «… n2», «… n3»
        if not (s.find("ya existe") or s.find("already exists") or s.find("nombre en uso")):
            break
        project = f"{re.sub(r' (?:#|n)[0-9]+$', '', project)} n{n}"
        log(f"Imagen: ese nombre ya existe, uso «{project}».")
        s.keys("ctrl+a")
        s.type(project)
        time.sleep(0.8)
    _remember(project)
    pfile.write_text(project, encoding="utf-8")          # si se corta aquí, el reintento lo busca antes de crear otro
    try:
        s.tap(photo_type, exact=True, timeout=10)
        _name_still_there(s, name_box, project)               # Imagen pudo pisarlo después de comprobarlo
        _tap_next(s)                                          # «Siguiente»
        s.wait("Perfil IA", "Selección con IA", timeout=30)
        _tap_next(s)
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
            if up0 and not edit_t0 and up0[1] < 0.9:      # subida vista de principio a fin: su velocidad real
                from .timing import record
                record("subida", n_dng, int((1 - up0[1]) * total), time.time() - up0[0])
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
    if not rev and row:
        # 25-nov-2026: 6 lecturas seguidas sin leer el botón (blanco sobre rojo), con el proyecto «Lista para reseña»
        # y el botón en pantalla: un intento entero perdido. Si la fila dice que está lista, el botón está en su
        # columna («Siguiente paso»), a la altura de la fila: se pulsa por su sitio. Lo siguiente (el diálogo de
        # descarga) se comprueba igual, así que un clic en falso no pasa de ahí.
        st, _n, _b = _row_parts(s, row, boxes)
        head = [b for b in boxes if norm(b["t"]).strip() in ("siguiente paso", "seguiente paso", "next step")
                and s.inside(b, (0.6, 0.08, 1, 0.2))]
        if head and any(k in norm(st) for k in READY):
            s.log("   («Revisar Ediciones» no se lee: lo pulso por su sitio en la fila)")
            rev = [{"t": "Revisar Ediciones", "x": head[0]["x"], "y": row["y"], "w": 0.058 * s.rect[2], "h": row["h"]}]
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


def _download_button(s: Screen, boxes) -> Optional[dict]:
    """El BOTÓN rojo «Descargar ediciones» del diálogo de descarga (None si el diálogo no está abierto).
    25-nov-2026 (visto en vivo, descarga que nunca arrancaba): el OCR lee el botón (blanco sobre rojo) a trozos,
    «Desca» + «ar ediciones»; lo único que leía entero era el TÍTULO del diálogo, que se llama igual, y el clic iba
    al título. El botón es lo que queda ABAJO del todo, bajo «Explorar»; si ni a trozos se lee, se pulsa por su sitio
    (misma distancia bajo «Explorar» que el título tiene encima, ×1,5)."""
    ex = [b for b in boxes if norm(b["t"]).strip() == "explorar"]
    if not ex or not any("tus fotos siguen" in norm(b["t"]) for b in boxes):
        return None
    e = min(ex, key=lambda b: b["y"])
    low = [b for b in boxes if b["y"] > e["y"] + 3 * e["h"] and len(b["t"].strip()) <= 24 and e["x"] - 40 * e["h"] < b["x"] < e["x"] + e["w"] + 2 * e["h"]      # dentro del diálogo, no la lista de detrás
           and (norm(b["t"]).strip().startswith("desca") or "ediciones" in norm(b["t"]))]
    if low:
        y = max(b["y"] for b in low)
        line = [b for b in low if abs(b["y"] - y) < 0.8 * b["h"]]
        x0, x1 = min(b["x"] for b in line), max(b["x"] + b["w"] for b in line)
        return {"t": "Descargar ediciones", "x": x0, "y": y, "w": x1 - x0, "h": line[0]["h"]}
    title = [b for b in boxes if norm(b["t"]).strip() == "descargar ediciones" and b["y"] < e["y"] - 2 * e["h"]]
    if not title:
        return None
    d = e["y"] - min(b["y"] for b in title)
    cx, cy = e["x"] + e["w"] / 2 - 0.56 * d, e["y"] + e["h"] / 2 + 1.51 * d
    return {"t": "Descargar ediciones", "x": cx - 2 * e["h"], "y": cy - e["h"] / 2, "w": 4 * e["h"], "h": e["h"]}


def _await_download(s: Screen, dng_dir: Path, mark, dngs: List[Path]):
    """Espera a que Imagen termine de bajar las ediciones (su registro dice al momento si fue bien o mal). Si
    pregunta dónde revisar, se elige «Bridge (Adobe Camera Raw)»."""
    t0, t_ok, asked = time.time(), None, False
    limit = max(300, 2 * len(dngs))
    res = None
    t_tap, taps, looks = time.time(), 0, 0
    # 26-nov-2026 (revisión): «nada bajado aún» se medía como «ningún DNG lleva ediciones»; en el 2.º intento (o si
    # algunos DNG ya las traían) eso nunca era cierto y el botón no se volvía a pulsar jamás. Ahora se compara con
    # cuántos faltaban AL EMPEZAR (n0): si siguen faltando los mismos, la descarga no ha empezado.
    n0 = len(core.without_imagen(dngs))
    while time.time() - t0 < limit:
        if s.should_stop():
            raise UIError("Cancelado.")
        res = download_result(mark, dng_dir) if mark[0] else None
        # 25-nov-2026: el clic en «Descargar ediciones» no entró (otra ventana se puso delante), el diálogo siguió
        # abierto y RawFlow esperó 20 min una descarga que no había empezado. Si a los RETAP_AFTER s el botón sigue
        # ahí y ningún DNG lleva ediciones, se vuelve a pulsar (el diálogo se cierra en cuanto la descarga empieza).
        if res is None and time.time() - t_tap > RETAP_AFTER:
            t_tap = time.time()
            # (una lectura a tamaño real y la siguiente ×2: contaba las PULSACIONES, y si el botón no se leía la
            # escala no cambiaba nunca — 26-nov-2026)
            btn = _download_button(s, s.ocr(scale=2 if looks % 2 else 1))
            looks += 1
            if btn and len(core.without_imagen(dngs)) >= n0:
                if taps >= RETAP_MAX:
                    # 26-nov-2026: ya pulsado RETAP_MAX veces más y el diálogo sigue ahí sin bajar nada: se para
                    # YA con un aviso claro, en vez de esperar los 5+ min del límite para nada.
                    # (no UIError: así _download_edits hace sus DL_TRIES —volver a Proyectos, esperar, reabrir el
                    # diálogo—, que es lo que lo arregla; con la excepción se saltaban. Revisión 26-nov-2026)
                    diag(s, "descarga-no-arranca")
                    return ("NO_ARRANCA", f"pulsé «Descargar ediciones» {RETAP_MAX + 1} veces y el diálogo sigue "
                                          "abierto sin bajar nada")
                taps += 1
                s.log(f"   (el botón «Descargar ediciones» sigue ahí: lo pulso otra vez, {taps} de {RETAP_MAX})")
                s.click_box(btn, after=1)
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
        btn, t_b, k = None, time.time(), 0
        while btn is None and time.time() - t_b < 20:                               # el botón, no el título
            btn = _download_button(s, s.ocr(scale=2 if k % 2 else 1))
            k += 1
        if btn is None:
            diag(s, "sin-boton-descargar")
            raise UIError("No encuentro el botón «Descargar ediciones» en Imagen.")
        s.click_box(btn, after=1)
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
    if not ws.activate(w["hwnd"], maximize=True, primary=True):    # en la pantalla principal su letra se lee bien
        s.log("   (Bridge no quedó delante al abrirse: lo vuelvo a intentar antes de pulsar nada)")
        s._blind = True                       # 26-nov-2026: el primer clic lo reintenta y, si no, para con UIError
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


def _bridge_export_button(s: Screen, tries: int = 4) -> Optional[dict]:
    """El botón azul «Exportar» del diálogo de exportación de Bridge (arriba a la derecha, sobre «Cancelar»).
    25-nov-2026 (visto en vivo, evento parado con las ediciones ya bajadas): el OCR lee ese botón (blanco sobre azul)
    como «capono» o «Expottor», nunca «Exportar» exacto. Vale un parecido; y si ni eso, está justo ENCIMA de
    «Cancelar», que sí se lee: se pulsa por su sitio. Siempre a tamaño real (ampliado ×2, las cajas de este diálogo
    pequeño salen desplazadas unos 30 px)."""
    from difflib import SequenceMatcher
    like = lambda t, w: SequenceMatcher(None, norm(t).strip(), w).ratio()      # noqa: E731
    for k in range(tries):
        if k:
            time.sleep(0.5)
        reg = [b for b in s.ocr() if s.inside(b, (0.7, 0.03, 1, 0.35))]         # (0.03: el título «Exportar» no)
        hit = [b for b in reg if like(b["t"], "exportar") >= 0.7]
        if hit:
            return min(hit, key=lambda b: b["y"])
        can = [b for b in reg if like(b["t"], "cancelar") >= 0.7]
        if can:
            c = min(can, key=lambda b: b["y"])
            return {"t": "Exportar", "x": c["x"], "y": c["y"] - 3.8 * c["h"], "w": c["w"], "h": c["h"]}
        # 26-nov-2026 (prueba de 5 fotos, monitor de 1080p): ni «Exportar» ni «Cancelar» se leían (letra blanca y
        # pequeña). Se lee SOLO la columna de los botones, ampliada ×3.
        w = s.window() if hasattr(s, "window") else None
        if w and k == 1:
            x, y, ww, hh = w["rect"]
            col = (int(x + 0.7 * ww), int(y + 0.03 * hh), int(0.3 * ww), int(0.32 * hh))
            try:
                big = ws.ocr(col, scale=3)
            except Exception:  # noqa: BLE001
                big = []
            hit = [b for b in big if like(b["t"], "exportar") >= 0.7]
            if hit:
                return min(hit, key=lambda b: b["y"])
            can = [b for b in big if like(b["t"], "cancelar") >= 0.7]
            if can:
                c = min(can, key=lambda b: b["y"])
                return {"t": "Exportar", "x": c["x"], "y": c["y"] - 3.8 * c["h"], "w": c["w"], "h": c["h"]}
    # último recurso: el diálogo «Exportar» de Bridge es siempre igual (730×591 a 100 %): el botón está arriba a la
    # derecha, en el 90 % del ancho y el 12,5 % del alto. Solo si la ventana delante ES ese diálogo.
    w = s.window() if hasattr(s, "window") else None
    if w and norm(w.get("title", "")).strip() == "exportar" and w["rect"][2] < 1400:
        x, y, ww, hh = w["rect"]
        s.log("   (Bridge: el botón «Exportar» no se lee: lo pulso por su sitio, arriba a la derecha del diálogo)")
        return {"t": "Exportar", "x": x + 0.899 * ww - 20, "y": y + 0.125 * hh - 8, "w": 40, "h": 16}
    return None


MISSING_DIR = "RawFlow faltan en Bridge"     # (dentro de «2 DNGs») enlaces a los DNG que aún no tienen su JPEG


def _jpeg_complete(p: Path) -> bool:
    """¿Termina el JPEG con su marca de fin (FF D9)? Uno cortado a medias (Bridge parado escribiéndolo) no."""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            if n < 1024:
                return False
            f.seek(max(0, n - 4096))                # (revisión 26-nov-2026: con 64 bytes, un JPEG bueno con datos
            return b"\xff\xd9" in f.read()          # detrás del final —relleno, MPF— se daba por cortado y se borraba)
    except OSError:
        return False


def _missing_dngs(dng_dir: Path, out_dir: Path) -> List[Path]:
    """DNG de `dng_dir` sin su JPEG completo en `out_dir` (los JPEG cortados a medias se borran: no valen)."""
    clean_exports(out_dir) if out_dir.is_dir() else None
    want = {core.dng_base(q): q for q in dng_dir.glob("*.dng")}
    have = set()
    for p in (out_dir.glob("*.jpg") if out_dir.is_dir() else []):
        if p.stem.lower() not in want:          # JPEG sin DNG en este lote (apartada, de la cámara…): ni se mira ni
            continue                            # se borra (revisión 26-nov-2026)
        if _jpeg_complete(p):
            have.add(p.stem.lower())
        else:
            p.unlink(missing_ok=True)
    return sorted(q for k, q in want.items() if k not in have)


def _link_missing(dng_dir: Path, missing: List[Path]) -> Optional[Path]:
    """«2 DNGs/RawFlow faltan en Bridge» con enlaces (instantáneos, sin ocupar disco) a esos DNG y sus .xmp.
    None si el disco no deja hacer enlaces (entonces se exporta todo, como antes)."""
    import shutil
    sub = dng_dir / MISSING_DIR
    shutil.rmtree(sub, ignore_errors=True)
    try:
        sub.mkdir()
        for q in missing:
            os.link(q, sub / q.name)
            side = q.with_suffix(".xmp")
            if side.exists():
                os.link(side, sub / side.name)
        return sub
    except OSError:
        shutil.rmtree(sub, ignore_errors=True)
        return None


def bridge_export(s: Screen, dng_dir: Path, out_dir: Path, expected: int, progress=lambda f: None) -> int:
    """Bridge exporta a `out_dir` los JPEG que FALTAN de los DNG de `dng_dir`. Devuelve cuántos JPEG hay en `out_dir`.
    26-nov-2026 (revisión del 25-nov, paso 4 de ~3 h): si un intento anterior exportó solo una parte, esos JPEG se
    quedan (son buenos; los cortados a medias se borran) y Bridge exporta solo los que faltan, desde una carpeta con
    enlaces a esos DNG. Antes se exportaba todo otra vez: más tiempo y, como los JPEG ya existentes no contaban como
    nuevos, el vigilante nunca llegaba a `expected` y daba «Bridge dejó de exportar»."""
    import shutil
    shutil.rmtree(dng_dir / MISSING_DIR, ignore_errors=True)     # resto de un intento cortado
    out_dir.mkdir(parents=True, exist_ok=True)
    dngs = sorted(dng_dir.glob("*.dng"))
    missing = _missing_dngs(dng_dir, out_dir)
    if dngs and not missing:
        s.log(f"Bridge: los {len(dngs)} JPEG ya estaban exportados en «{out_dir.name}».")
        return clean_exports(out_dir)
    if not dngs or len(missing) == len(dngs):
        return _bridge_export_dir(s, dng_dir, out_dir, expected, progress)
    sub = _link_missing(dng_dir, missing)
    if sub is None:
        s.log("   (no puedo hacer enlaces en este disco: Bridge exporta todas otra vez)")
        return _bridge_export_dir(s, dng_dir, out_dir, expected, progress)
    s.log(f"Bridge: ya había {len(dngs) - len(missing)} de {len(dngs)} JPEG (se quedan); exporto los {len(missing)} "
          "que faltan.")
    try:
        _bridge_export_dir(s, sub, out_dir, len(missing), progress)
    finally:
        close_app(BRIDGE_EXE)                # (suelta los DNG enlazados para poder borrar la carpeta)
        shutil.rmtree(sub, ignore_errors=True)
    return clean_exports(out_dir)


def _bridge_selected(boxes) -> Optional[int]:
    """«120 elementos, 120 seleccionados - 2,81 GB» (barra de estado de Bridge) → 120. None si no se lee."""
    for b in boxes:
        m = re.search(r"(\d[\d.]*)\s*(?:seleccionad|selected)", norm(b["t"]))
        if m:
            try:
                return int(m.group(1).replace(".", ""))
            except ValueError:
                pass
    return None


def _bridge_unfold_format(s: Screen) -> None:
    """La sección «Formato de imagen» del diálogo de exportación, DESPLEGADA (ahí están «Formato» y la calidad).
    26-nov-2026 (revisión): Bridge recuerda qué secciones se plegaron; con esa plegada no se veía «Formato» y el
    paso se paraba a los 10 s. Solo se pulsa la flecha (a la izquierda del rótulo, como en «Opciones de guardado»)
    si NO se ve lo de dentro."""
    for _ in range(2):
        boxes = s.ocr()
        if s.find("Calidad de imagen", boxes=boxes) or s.find("Formato", exact=True, boxes=boxes):
            return
        hdr = s.find("Formato de imagen", boxes=boxes)
        if not hdr:
            return                                  # (sin el rótulo no se pulsa nada: lo de abajo dirá qué falta)
        s.log("   (Bridge: «Formato de imagen» estaba plegado: lo despliego)")
        s.click(hdr[0]["x"] - 1.8 * hdr[0]["h"], hdr[0]["y"] + hdr[0]["h"] / 2)
        time.sleep(0.7)


def _bridge_quality(s: Screen, reads: int = 4):
    """(caja del número de calidad JPEG, ¿dice «Máxima»?), leído hasta que DOS lecturas seguidas coinciden, una a
    tamaño real y otra ×2. 26-nov-2026 (revisión): con una sola lectura, un «12» mal leído (o un «2» del «12»
    partido) bastaba para dar la calidad por buena o por mala. La posición se busca respecto a «Calidad de imagen»
    leído EN LA MISMA lectura (×2, las cajas de este diálogo salen algo desplazadas). Para pulsar se devuelve la
    caja de una lectura a tamaño real. Si nunca coinciden: (None, False) → se escribe 12 y se comprueba otra vez."""
    def one(scale: int):
        bs = s.ocr(scale=scale)
        qs = s.find("Calidad de imagen", boxes=bs)
        if not qs:
            return None, False
        q = qs[0]
        row = [b for b in bs if 0.8 * q["h"] < b["y"] - q["y"] < 3.5 * q["h"] and b["x"] > q["x"]]
        num = [b for b in row if b["t"].strip().isdigit() and 1 <= int(b["t"].strip()) <= 12]
        return (num[0] if num else None), any(norm(b["t"]).strip() == "maxima" for b in row)
    prev, box1, box = None, None, None
    for k in range(reads):
        num, top = one(2 if k % 2 else 1)
        box = num or box
        if not k % 2 and num:
            box1 = num
        key = (num["t"].strip() if num else None, top)
        if prev is not None and key == prev and key[0] is not None:
            return (box1 or num), top
        prev = key
    s.log("   (Bridge: la calidad JPEG no se lee igual dos veces seguidas: la escribo)")
    return (box1 or box), False


def _bridge_export_dir(s: Screen, dng_dir: Path, out_dir: Path, expected: int, progress=lambda f: None) -> int:
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
    for k in range(3):
        s.click(t["x"] + t["w"] / 2, t["y"] - 3.5 * t["h"])    # la miniatura (encima de su nombre)
        time.sleep(0.6)
        if k == 0:
            s.keys("ctrl+a")                                    # todas
        else:
            # 26-nov-2026 (prueba de 5 fotos): Ctrl+A no seleccionaba nada más («7 elementos, 1 seleccionado») tres
            # veces seguidas. El menú Edición → Seleccionar todo no depende de qué panel de Bridge tenga el foco.
            try:
                with s.holding():
                    s.tap("Editar", exact=True, timeout=6, region=(0, 0, 0.3, 0.08), after=0.8)
                    s.tap("Seleccionar todo", timeout=6, after=0.6)
            except UIError:
                s.esc()
                s.keys("ctrl+a")
        time.sleep(1.0)
        # 26-nov-2026 (revisión): se exportaba sin mirar si Ctrl+A había entrado (a veces solo quedaba elegida la
        # miniatura pulsada y Bridge exportaba UNA foto). La barra de estado dice «N elementos, M seleccionados»;
        # (puede haber carpetas: M ≥ DNG vale). Si no se lee, se sigue: el vigilante de JPEG de abajo lo pillaría.
        sel = _bridge_selected(s.ocr())
        if sel is None:
            sel = _bridge_selected(s.ocr(scale=2))            # la barra de estado es letra pequeña
        if sel is None or sel >= len(stems):
            break
        log(f"   (Bridge: solo {sel} de {len(stems)} fotos seleccionadas: Ctrl+A otra vez)")
    else:
        # (revisión 26-nov-2026: el OCR puede leer mal el número —«720» como «20»— y se paraba un paso que iba bien.
        # Se avisa y se sigue: lo que Bridge no exporte lo pilla el vigilante de JPEG y se repite SOLO eso)
        diag(s, "bridge-seleccion-incompleta")
        log(f"   ⚠ Bridge parece tener {sel} de {len(stems)} fotos seleccionadas: exporto y compruebo foto a foto.")
    for k in range(3):
        try:
            # (un Bridge recién abierto a veces no despliega el menú al primer clic: se reintenta AQUÍ, sin volver a
            # abrir Bridge ni repetir todo el paso, que costaba ~1 min)
            # (modo tranquilo: el menú entero es UNA acción —el teclado no se devuelve a mitad— y el cursor se queda en
            # el menú hasta elegir; luego vuelve a donde lo tenía la persona)
            with s.holding():
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
    _bridge_unfold_format(s)
    fmt = s.wait("Formato", exact=True, timeout=10)
    jpeg_below = lambda bs: [b for b in bs if norm(b["t"]).strip() == "jpeg" and 0 < b["y"] - fmt["y"] < 4 * fmt["h"]]  # noqa: E731
    # 26-nov-2026 (prueba de 5 fotos, monitor de 1080p): «JPEG» no se leía a tamaño real dentro del desplegable; se
    # abría para elegirlo, la lista (letra pequeña) tampoco se leía y se quedaba ABIERTA tapando la calidad. Ahora
    # se mira también por trozos ×3 antes de tocar nada, y si hay que abrirla se elige «JPEG» DE LA LISTA y se
    # comprueba que se cerró.
    if not (jpeg_below(s.ocr()) or jpeg_below(s.ocr_tiles(3))):
        s.click(fmt["x"] + 8 * fmt["h"], fmt["y"] + 3 * fmt["h"])          # abre el desplegable
        time.sleep(0.8)
        for _ in range(3):
            bs = s.ocr_tiles(3)
            items = [b for b in bs if norm(b["t"]).strip() == "jpeg" and b["y"] - fmt["y"] > 3.5 * fmt["h"]]
            if items:
                s.click_box(min(items, key=lambda b: b["y"]), after=0.8)   # «JPEG» de la lista
            if not [b for b in s.ocr_tiles(3) if norm(b["t"]).strip() in ("tiff", "png")]:
                break                                                       # la lista ya se cerró
        else:                                   # sigue abierta: otro clic en el desplegable la cierra (sin cambiarlo)
            s.click(fmt["x"] + 8 * fmt["h"], fmt["y"] + 3 * fmt["h"])
            time.sleep(0.8)
    s.wait("Calidad de imagen", timeout=10)
    num, top = _bridge_quality(s)
    if not (num and num["t"].strip() == "12" and top):
        if not num:
            diag(s, "bridge-sin-calidad")
            raise UIError("No encuentro la calidad JPEG en el diálogo de Bridge.")
        s.click(num["x"] + num["w"] / 2, num["y"] + num["h"] / 2)
        time.sleep(0.3)
        s.keys("ctrl+a")
        s.type("12")
        s.keys("tab")
        time.sleep(0.8)
        num, top = _bridge_quality(s)
        if not (top or (num and num["t"].strip() == "12")):
            diag(s, "bridge-calidad-no-12")
            raise UIError("No pude poner la calidad JPEG en máxima en Bridge.")
    go = _bridge_export_button(s)
    if not go:
        diag(s, "bridge-sin-boton-exportar")
        raise UIError("No encuentro el botón «Exportar» del diálogo de Bridge.")
    s.click_box(go, after=0.8)
    for _ in range(2):                           # ¿sigue abierto el diálogo «Exportar»? el clic no entró: otra vez
        time.sleep(2.5)
        if not any(norm(w["title"]).strip() == "exportar" for w in ws.windows(exe=s.exe)):
            break
        go = _bridge_export_button(s) or go
        s.click_box(go, after=0.8)
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
    from .timing import record                   # (4.26) en Windows no se anotaban: Imagen se estimaba 1 h 13, tardó 14 min
    nbytes = sum(p.stat().st_size for p in dngs)
    fresh = not (dng_dir / ".rawflow_imagen_ok").exists() and not (dng_dir / ".rawflow_imagen_proyecto").exists()
    t0 = time.monotonic()
    edit(event_dir, dng_subfolder, log, should_stop, photo_type=photo_type,
         progress=lambda f: progress(0.8 * f), charge_ok=charge_ok)
    if fresh:                                    # solo una pasada entera (subir + editar + descargar) dice el tiempo
        record("imagen", len(dngs), nbytes, time.monotonic() - t0)
    falta = core.without_imagen(dngs)
    if falta:                                    # CANDADO: nunca a Bridge una foto sin las ediciones de Imagen
        raise UIError(f"{len(falta)} DNG sin las ediciones de Imagen: no se exportan.")
    close_app(EXE)
    t0 = time.monotonic()
    k = bridge_export(Screen(log, should_stop, BRIDGE_EXE), dng_dir, out, len(dngs),
                      progress=lambda f: progress(0.8 + 0.2 * f))
    if k >= len(dngs):
        record("bridge", len(dngs), nbytes, time.monotonic() - t0)
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

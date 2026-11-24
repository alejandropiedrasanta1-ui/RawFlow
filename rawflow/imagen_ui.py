"""Paso 3 sin clave API: maneja la app ImagenAI y Adobe Bridge como lo harías tú.

Secuencia (la del video):
  ImagenAI → Nuevo proyecto → Carpeta → Explorar → <evento>/DNGs → Elegir carpeta
  → nombre = evento · tipo Bodas → Siguiente → Siguiente (perfil) → Crear proyecto
  → esperar «Lista para reseña» → Revisar Ediciones en Adobe → Descargar todas las ediciones
  → Descargar ediciones (esto COBRA en Imagen) → Bridge (Adobe Camera Raw)
  → Bridge: seleccionar todo → Exportación personalizada → carpeta <evento>/Fotos finales,
    JPEG calidad 12 → Exportar → nombres limpios (DSC01592.jpg).

Encuentra cada botón leyendo la pantalla con el OCR de macOS (tools/rfui), no con
coordenadas fijas. Necesita la pantalla desbloqueada y los permisos de Accesibilidad y
Grabación de pantalla para RawFlow. Si algo no aparece, se detiene y dice en qué paso.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from . import live
from . import core
from .core import files_settled
from typing import Callable, List, Optional

FINAL_SUBFOLDER = "4 Fotos finales"
IMAGEN_APP = "ImagenAI"
BRIDGE_ID = "com.adobe.bridge"
Log = Callable[[str], None]


class UIError(Exception):
    pass


PROG = [lambda f: None]
_CLIP = [None]                   # portapapeles del usuario antes del evento
STEP3 = [False]                  # True mientras corre el paso 3 (run): sus fases dicen cuánto le falta al paso


def _bridge_est(n: int) -> float:
    """Segundos que tardará Bridge en exportar n JPEG en este Mac (medido)."""
    try:
        from .timing import model
        f, spp, _ = model("bridge")
        return f + spp * n
    except Exception:  # noqa: BLE001
        return 22.0 + 3.0 * n


def restore_clipboard():
    if _CLIP[0] is not None:
        try:
            subprocess.run(["pbcopy"], input=_CLIP[0], timeout=5)
        except Exception:  # noqa: BLE001
            pass
        _CLIP[0] = None          # avance del paso 3 (0..1) hacia la pantalla de RawFlow


def _prog(f: float):
    try:
        PROG[0](max(0.0, min(1.0, f)))
    except Exception:  # noqa: BLE001
        pass


def _mmss(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _rfui() -> str:
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for p in (here / "rfui", here / "tools" / "rfui", Path(__file__).resolve().parent.parent / "tools" / "rfui"):
        if p.exists():
            return str(p)
    raise UIError("Falta la herramienta rfui dentro de RawFlow.")


BULLETS = "•·◉○●◦▪▫◆◇-–—>»"            # marcas de opción / viñetas que el OCR pone delante de un rótulo


def norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


_ACT = Path.home() / "Library/Application Support/RawFlow/acciones.log"
_T0 = [time.monotonic()]


def act(msg: str):
    """Registro fino de cada acción con su hora (para encontrar esperas que sobran)."""
    try:
        now = time.monotonic()
        with open(_ACT, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} +{now - _T0[0]:6.2f}s  {msg}\n")
        _T0[0] = now
    except OSError:
        pass


class Screen:
    def __init__(self, log: Log, should_stop=lambda: False):
        self._last = ("", [])
        self.rf = _rfui()
        self.log, self.should_stop = log, should_stop
        self.front = None            # (proceso, orden AppleScript para traerlo) de la app que se está manejando
        self._fcheck = 0.0

    # ---- bajo nivel
    def run(self, *a):
        subprocess.run([self.rf, *map(str, a)], check=False)

    def keep_front(self):
        """Si otra app se puso delante (Bridge al conectar una memoria, un aviso de fallo…), vuelve a traer
        la app que se está manejando: RawFlow solo ve y toca la que está en primer plano."""
        if not self.front or time.time() - self._fcheck < 1.5:
            return
        self._fcheck = time.time()
        proc, target = self.front
        r = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first process '
                            'whose frontmost is true'], capture_output=True, text=True, timeout=10).stdout.strip()
        if r and r != proc and r not in ("Open and Save Panel Service", "SecurityAgent"):
            dismiss_crash_dialogs()
            subprocess.run(["osascript", "-e", f"tell {target} to activate"], capture_output=True, timeout=15)
            time.sleep(0.8)

    def ocr(self) -> List[dict]:
        try:
            self.keep_front()
        except Exception:  # noqa: BLE001
            pass
        out = subprocess.run([self.rf, "ocr"], capture_output=True, text=True, timeout=60).stdout
        try:
            boxes = json.loads(out or "[]")
        except ValueError:
            return []
        from . import overlays                      # ignorar el panel flotante y el aviso de la memoria de RawFlow
        for r in (overlays.OVERLAY_RECT, overlays.TOAST_RECT):
            if r:
                boxes = [b for b in boxes if not (r[0] <= b["x"] + b["w"] / 2 <= r[0] + r[2]
                                                  and r[1] <= b["y"] + b["h"] / 2 <= r[1] + r[3])]
        return boxes

    def click(self, x, y, n=1):
        self.run("click", round(x), round(y), n)

    def paste(self, text: str):
        """Pega el texto de una vez (mucho más rápido que escribirlo letra a letra). El portapapeles del
        usuario se guarda la primera vez y se devuelve al terminar el evento (restore_clipboard): devolverlo
        al instante hacía que una app lenta pegara el contenido viejo."""
        try:
            if _CLIP[0] is None:
                _CLIP[0] = subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5)
            self.keys("v", "command")                    # ⌘V
            time.sleep(0.3)
        except Exception:  # noqa: BLE001
            self.type(text)

    def type(self, text: str):
        self.run("type", text)

    def keys(self, key: str, mods: str = ""):
        """Atajos con modificadores por System Events (las apps Electron no aceptan los de CGEvent)."""
        using = f" using {{{', '.join(m + ' down' for m in mods.split(','))}}}" if mods else ""
        k = f'key code {key}' if key.isdigit() else f'keystroke "{key}"'
        subprocess.run(["osascript", "-e", f'tell application "System Events" to {k}{using}'], check=False)

    def activate(self, app: str = "", bundle: str = "", process: str = ""):
        target = f'application id "{bundle}"' if bundle else f'application "{app}"'
        self.front = (process or app, target)
        subprocess.run(["osascript", "-e", f"tell {target} to activate"], check=False)
        time.sleep(1.0)
        self.fill(process or app)

    def fill(self, process: str):
        """Pone la ventana de la app ocupando toda la pantalla (debajo de la barra de menús),
        para que los botones estén siempre en el mismo sitio, la hayas movido o no."""
        if not process:
            return
        try:
            b = subprocess.run(["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
                               capture_output=True, text=True, timeout=10).stdout
            w, h = [int(x) for x in b.replace(" ", "").split(",")][2:4]
        except Exception:  # noqa: BLE001
            w, h = 1280, 832
        subprocess.run(["osascript", "-e",
                        f'tell application "System Events" to tell process "{process}"\n'
                        f'  if (count of windows) > 0 then\n'
                        f'    set position of window 1 to {{0, 29}}\n'
                        f'    set size of window 1 to {{{w}, {h - 29}}}\n'
                        f'  end if\nend tell'], capture_output=True, timeout=15)
        time.sleep(0.6)

    # ---- por texto
    def find(self, text: str, exact=False, region=None, boxes=None) -> List[dict]:
        t = norm(text)
        res = []
        for b in boxes if boxes is not None else self.ocr():
            n = norm(b["t"])
            # una opción marcada se lee con su viñeta («• Carpeta»): para comparar exacto se quita
            if (n == t or n.strip().lstrip(BULLETS).strip() == t) if exact else (t in n):
                if region and not (region[0] <= b["x"] <= region[2] and region[1] <= b["y"] <= region[3]):
                    continue
                res.append(b)
        return res

    def wait(self, *texts: str, timeout=30, exact=False, region=None) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.should_stop():
                raise UIError("Cancelado.")
            boxes = self.ocr()
            for txt in texts:
                f = self.find(txt, exact, region, boxes)
                if f:
                    self._last = (txt, f)
                    act(f"apareció «{txt}» ({time.time() - t0:.1f}s)")
                    return f[0]
            time.sleep(0.15)                        # cada lectura ya tarda ~0,3 s: reaccionar enseguida
        raise UIError(f"No apareció «{' / '.join(texts)}» en pantalla.")

    def tap(self, *texts: str, timeout=30, exact=False, region=None, pick=0, after=0.8) -> dict:
        b = self.wait(*texts, timeout=timeout, exact=exact, region=region)
        boxes = self._last[1]                       # las cajas de esa misma lectura (sin releer)
        boxes = sorted(boxes or [b], key=lambda x: (x["y"], x["x"]))   # pick=-1 → el de más abajo
        b = boxes[pick]
        self.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        act(f"clic «{b['t']}»  (+{after}s de espera)")
        time.sleep(after)
        return b

    def goto_folder(self, path: Path):
        """En un selector de carpetas de macOS: Cmd+Mayús+G, escribe la ruta, Intro."""
        self.keys("g", "command,shift")
        time.sleep(0.9)
        self.keys("a", "command")
        self.type(str(path).rstrip("/") + "/")          # «/» final: entra en la carpeta (no solo la selecciona)
        time.sleep(0.8)                                 # (pegar con ⌘V falla en el diálogo de Bridge: se escribe)
        self.keys("36")
        time.sleep(1.0)
        act(f"carpeta {path.name}")


def _win(process: str = "ImagenAI"):
    out = subprocess.run(["osascript", "-e", f'tell application "System Events" to tell process "{process}" '
                          'to get {position, size} of window 1'], capture_output=True, text=True).stdout
    n = [int(v) for v in out.replace(" ", "").split(",") if v.strip().lstrip("-").isdigit()]
    return n if len(n) == 4 else [0, 29, 1280, 803]


def check_permissions() -> str:
    """'' si hay permisos; si no, el texto de lo que falta (se comprueba en la propia RawFlow,
    que es la app que aparece en Ajustes; su herramienta interna hereda sus permisos)."""
    from .permissions import _ax_trusted, _screen_ok
    miss = []
    if not _ax_trusted(False):
        miss.append("Accesibilidad")
    if not _screen_ok(False):
        miss.append("Grabación de pantalla")
    return ", ".join(miss)


# --------------------------------------------------------------------------
def _key(t: str) -> str:
    return "".join(c for c in norm(t) if c.isalnum() or c == "#")


def _row(s: Screen, project: str, boxes=None) -> Optional[dict]:
    """Caja OCR del nombre EXACTO del proyecto en la lista (sin parecidos: «20 nov 2026» nunca vale por
    «20 nov 2026 3 #2»). Solo si Imagen recorta un nombre largo con «…», vale el principio."""
    want = _key(project)
    hits = []
    cand = [b for b in (boxes if boxes is not None else s.ocr()) if b["x"] < 420 and 140 < b["y"] < 900]

    def match(t: str) -> bool:
        t, k = t.strip(), _key(t)
        return k == want or (t.endswith(("…", "...")) and len(k) >= 8 and want.startswith(k))
    for b in cand:
        if match(b["t"]):
            hits.append(b)
    # un nombre largo («Tanda 1 20 nov 2026 2») a veces sale partido en dos cajas de la MISMA fila: se unen
    rows: List[List[dict]] = []
    for b in sorted(cand, key=lambda x: (x["y"], x["x"])):
        for r in rows:
            if abs(r[0]["y"] - b["y"]) < 8:
                r.append(b)
                break
        else:
            rows.append([b])
    for r in rows:
        if len(r) > 1:
            r.sort(key=lambda x: x["x"])
            t = " ".join(x["t"].strip() for x in r)
            if match(t):
                hits.append(dict(r[0], t=t, w=max(x["x"] + x["w"] for x in r) - r[0]["x"]))
    return min(hits, key=lambda b: b["y"]) if hits else None


def _close_uploads(s: Screen):
    """El panel «Gestionar subidas» tapa la columna de botones (p. ej. «Revisar Ediciones»): se cierra con
    su propio botón de contraer (arriba a la derecha del panel) y se comprueba que se fue."""
    for _ in range(3):
        boxes = s.ocr()
        title = s.find("Gestionar subidas", boxes=boxes)
        if not title and not s.find("Upload for editing", boxes=boxes):
            return
        if title:
            tb = title[0]
            right = max((b["x"] + b["w"] for b in boxes
                         if tb["y"] - 10 < b["y"] < tb["y"] + 260 and b["x"] > tb["x"]), default=tb["x"] + 558)
            s.click(right - 4, tb["y"] + tb["h"] / 2)
        else:
            s.keys("53")
        time.sleep(1.0)


def dismiss_crash_dialogs():
    """Cierra las ventanas «X se cerró inesperadamente» de macOS: se ponen delante y tapan la app que se maneja."""
    for n in ("Problem Reporter", "ReportCrash"):
        subprocess.run(["pkill", "-x", n], capture_output=True)


_HIST = Path.home() / "Library/Application Support/RawFlow/imagen_proyectos.txt"


def _remember_project(name: str):
    try:
        _HIST.parent.mkdir(parents=True, exist_ok=True)
        with open(_HIST, "a", encoding="utf-8") as f:
            f.write(name + "\n")
    except OSError:
        pass


def _free_project_name(s: Screen, base: str) -> str:
    """Nombre de proyecto que no exista ya en Imagen: «20 nov 2026», si no «20 nov 2026 #2», «#3»…
    Mira los proyectos que se ven en la lista de Imagen y los que RawFlow ya creó antes."""
    return free_name(base, [b["t"] for b in s.ocr()])


def free_name(base: str, visible: List[str]) -> str:
    """Primer nombre libre: base, «base #2», «base #3»… (comparando sin espacios ni signos, como lo lee el OCR)."""
    seen = {_key(t) for t in visible}
    try:
        seen |= {_key(l) for l in _HIST.read_text(encoding="utf-8").splitlines()}
    except OSError:
        pass
    name, n = base, 1
    while _key(name) in seen:
        n += 1
        name = f"{base} #{n}"
    return name


def _clear_imagen(s: Screen, timeout: int = 90):
    """Deja Imagen en su pantalla principal: si quedó abierta una ventana (p. ej. «actualizar ajustes» de un
    perfil, una subida, un menú), la cierra con Esc / «Cancelar» / «Cerrar» hasta ver «Proyectos»."""
    t0 = time.time()
    while True:
        if s.should_stop():
            raise UIError("Cancelado.")
        boxes = s.ocr()
        side = [b for b in boxes if norm(b["t"]) == "proyectos" and b["x"] < 200]
        if side and not s.find("actualizar ajustes", boxes=boxes):
            break
        if time.time() - t0 > timeout:
            raise UIError("Imagen no muestra su pantalla principal («Proyectos»).")
        dismiss_crash_dialogs()
        if not boxes or not s.find("imagen", boxes=boxes):        # otra ventana le quitó el sitio: traerla
            subprocess.run(["osascript", "-e", f'tell application "{IMAGEN_APP}" to activate'], capture_output=True)
            time.sleep(1)
            continue
        if boxes:                                   # la app ya pinta algo: hay algo abierto encima
            s.keys("53")
            time.sleep(0.6)
            close = [b for b in boxes if norm(b["t"]) in ("cancelar", "cerrar", "descartar", "cancel", "close", "discard")]
            if close:
                b = max(close, key=lambda b: b["y"])
                s.log("Imagen: cerrando una ventana que quedó abierta…")
                s.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        time.sleep(1.2)
    for _ in range(2):                              # menús/paneles sueltos
        s.keys("53")
        time.sleep(0.3)


def imagen_edit(s: Screen, dng_dir: Path, project: str, photo_type: str = "Bodas",
                charge_ok: bool = True, wait_minutes: int = 90) -> bool:
    """Edita en Imagen y descarga. True si hizo todo (crear + subir + editar + descargar); False si retomó un
    proyecto ya creado (su tiempo no es el de un evento normal: no se aprende)."""
    log = s.log
    _open_projects(s)
    pfile = dng_dir / ".rawflow_imagen_proyecto"      # proyecto ya creado para este evento (reintento)
    prev = pfile.read_text(encoding="utf-8").strip() if pfile.exists() else ""
    n_dng = len(list(dng_dir.glob("*.dng")))
    if prev:
        old, boxes = _find_row(s, prev)
        if old is None:
            # ya se creó (y se subió) ese proyecto: NUNCA se crea otro igual, que se cobraría dos veces
            raise UIError(f"No encuentro el proyecto «{prev}» en la lista de Imagen.")
        got = _row_count(boxes, old["y"])
        if got is not None and got < n_dng:
            pfile.unlink(missing_ok=True)
            raise UIError(f"Imagen solo tiene {got} de {n_dng} fotos en «{prev}»: se crea un proyecto nuevo con todas.")
        project = prev
        log(f"Imagen: retomo el proyecto «{project}» (no se crea otro).")
    else:
        project = _free_project_name(s, project)
        project = _create_project(s, dng_dir, project, photo_type, pfile)
    _wait_and_download(s, dng_dir, project, charge_ok, wait_minutes)
    return not prev


def _open_projects(s: Screen):
    """Imagen delante, sin ventanas a medias, en la lista de proyectos."""
    if not Path("/Applications/ImagenAI.app").exists():
        raise UIError("No encuentro la app ImagenAI en Aplicaciones.")
    subprocess.run(["open", "-a", IMAGEN_APP], check=False)
    s.activate(IMAGEN_APP)
    _clear_imagen(s)
    side = [b for b in s.ocr() if norm(b["t"]) == "proyectos" and b["x"] < 200]
    if not side:
        raise UIError("No encuentro «Proyectos» en la barra lateral de Imagen.")
    s.click(side[0]["x"] + side[0]["w"] / 2, side[0]["y"] + side[0]["h"] / 2)
    s.wait("Archivados", "Nuevo proyecto", timeout=20)
    _close_uploads(s)                  # con otra tanda subiendo, su panel tapa botones de la lista


def _find_row(s: Screen, project: str, tries: int = 4):
    """Fila del proyecto en la lista, con varias lecturas (la lista se está dibujando o moviendo)."""
    boxes = []
    for k in range(tries):
        boxes = s.ocr()
        r = _row(s, project, boxes)
        if r is not None:
            return r, boxes
        time.sleep(1.2)
    return None, boxes


def start_project(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False, project: str = "",
                  photo_type: str = "Bodas") -> str:
    """Por tandas, mientras DxO sigue: crea el proyecto en Imagen con esa carpeta y lo deja SUBIENDO (no espera
    la edición). Si ya estaba creado (marca), no hace nada. Devuelve el nombre del proyecto."""
    dng_dir = event_dir / dng_subfolder
    pfile = dng_dir / ".rawflow_imagen_proyecto"
    if pfile.exists():
        return pfile.read_text(encoding="utf-8").strip()
    miss = check_permissions()
    if miss:
        raise UIError(f"RawFlow necesita permiso de {miss} (botón «Permisos»).")
    s = Screen(log, should_stop)
    dismiss_crash_dialogs()
    _open_projects(s)
    name = _free_project_name(s, project or event_dir.name)
    return _create_project(s, dng_dir, name, photo_type, pfile, bridge=False)


def edit_only(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False, project: str = "",
              photo_type: str = "Bodas", progress=lambda f: None) -> None:
    """Imagen de una carpeta (crea o retoma su proyecto, espera la edición y descarga) SIN Bridge. Deja la marca
    «.rawflow_imagen_ok» para no volver a pedir (ni cobrar) esas ediciones."""
    PROG[0] = progress
    dng_dir = event_dir / dng_subfolder
    mark = dng_dir / ".rawflow_imagen_ok"
    dngs = list(dng_dir.glob("*.dng"))
    if mark.exists():
        if dngs and not core.without_imagen(dngs):    # la marca sola no basta: cada DNG lleva sus ediciones
            return
        mark.unlink(missing_ok=True)
        log("Imagen: la marca decía «hecho» pero faltan ediciones en los DNG: se vuelven a pedir.")
    miss = check_permissions()
    if miss:
        raise UIError(f"RawFlow necesita permiso de {miss} (botón «Permisos»).")
    if not dngs:
        raise UIError(f"No hay DNG en «{dng_dir}».")
    from .timing import record
    s = Screen(log, should_stop)
    dismiss_crash_dialogs()
    t0 = time.monotonic()
    if imagen_edit(s, dng_dir, project or event_dir.name, photo_type):
        record("imagen", len(dngs), sum(p.stat().st_size for p in dngs), time.monotonic() - t0)
    falta = core.without_imagen(dngs)
    if falta:                                          # nunca «hecho» sin las ediciones en TODOS los DNG
        raise UIError(f"Imagen no puso sus ediciones en {len(falta)} de {len(dngs)} DNG ({falta[0].name}…).")
    mark.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")


def _create_project(s: Screen, dng_dir: Path, project: str, photo_type: str, pfile: Path, bridge: bool = True) -> str:
    log = s.log
    log(f"Imagen: creando el proyecto «{project}»…")
    for attempt in range(3):                       # una notificación de macOS puede tapar el botón
        try:
            s.tap("Nuevo proyecto", timeout=12)
        except UIError:
            act = s.wait("Archivados", timeout=5)
            x, y, w, h = _win()
            s.click(x + w - 92, act["y"] + act["h"] / 2)
        try:
            s.wait("Elige origen", "Dónde están tus fotos", timeout=8)
            break
        except UIError:
            s.keys("53")
            time.sleep(4)
    else:
        raise UIError("No se abrió «Nuevo proyecto» en Imagen.")
    s.tap("Carpeta", exact=True, timeout=20)
    s.tap("Explorar", timeout=20, after=1.2)
    s.goto_folder(dng_dir)
    s.tap("Elegir carpeta", timeout=20, after=1)
    s.wait("Nombra tu proyecto", timeout=60)

    name_box = s.wait("Nombra tu proyecto")
    s.click(name_box["x"] + 120, name_box["y"] + 28)  # campo debajo del rótulo
    time.sleep(0.4)
    s.keys("a", "command")
    s.type(project)
    time.sleep(0.8)
    for n in range(2, 8):                          # si Imagen dice que el nombre ya existe: «… 2», «… 3»…
        if not (s.find("ya existe") or s.find("already exists") or s.find("nombre en uso")):
            break
        project = f"{project.rsplit(' #', 1)[0]} #{n}"
        log(f"Imagen: ese nombre ya existe, uso «{project}».")
        s.keys("a", "command")
        s.type(project)
        time.sleep(0.8)
    _remember_project(project)
    pfile.write_text(project, encoding="utf-8")       # si se corta aquí, el reintento lo busca antes de crear otro
    s.tap(photo_type, exact=True, timeout=10)
    s.tap("guiente", timeout=10, region=(850, 600, 1300, 900), after=1)   # «Siguiente»
    s.wait("Perfil IA", "Tu flujo de trabajo", timeout=30)
    s.tap("guiente", timeout=10, region=(850, 600, 1300, 900), after=1)
    s.wait("Herramientas", timeout=30)
    s.tap("crear proyecto", timeout=10, after=2)
    pfile.write_text(project, encoding="utf-8")
    log(f"Imagen: subiendo y editando «{project}»…")
    if bridge:
        prelaunch_bridge()                      # mientras Imagen edita en la nube, Bridge ya va abriendo
    return project


def prelaunch_bridge():
    """Abre Bridge en segundo plano (sin ponerlo delante) para que esté listo cuando toque exportar: en frío
    tarda ~25 s en mostrar la carpeta; ya abierto, 2-3 s. La ventana de Arams solo si el paso 4 la necesita."""
    try:
        if subprocess.run(["pgrep", "-x", Path(_bridge_app()).stem], capture_output=True).returncode != 0:
            subprocess.Popen(["open", "-g", "-a", _bridge_app()], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        from .retouch_engine import usable
        if not usable()[0] and Path("/Applications/Retouch4me Arams.app").exists():
            subprocess.Popen(["open", "-g", "-a", "/Applications/Retouch4me Arams.app"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _row_count(boxes, y) -> Optional[int]:
    """Número de la columna «Fotos» en la fila del proyecto."""
    fix = str.maketrans({"З": "3", "з": "3", "О": "0", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1",
                         "S": "5", "B": "8", "Б": "6", "Ч": "4"})
    for b in boxes:
        t = b["t"].strip().translate(fix)
        if abs(b["y"] - y) < 14 and 600 < b["x"] < 780 and t.isdigit():
            return int(t)
    return None


def _wait_and_download(s: Screen, dng_dir: Path, project: str, charge_ok: bool, wait_minutes: int):
    log = s.log
    prelaunch_bridge()                         # también al retomar un proyecto: Bridge listo para exportar
    dngs = list(dng_dir.glob("*.dng"))
    n_dng = len(dngs)
    total = sum(p.stat().st_size for p in dngs) or 1
    from .timing import model
    f_img, spp_img, _ = model("imagen")
    expect_edit = max(30.0, (f_img + spp_img * n_dng) * 0.6)     # parte de edición en la nube (medido)
    up0 = None                                                     # (momento, %) al empezar a ver la subida
    edit_t0 = None
    import re as _re

    t0, last = time.time(), ""
    while True:
        if s.should_stop():
            raise UIError("Cancelado.")
        if time.time() - t0 > wait_minutes * 60:
            raise UIError("Imagen tardó demasiado en editar. Revisa el proyecto en la app.")
        _close_uploads(s)
        boxes = s.ocr()
        r = _row(s, project, boxes)
        row = [r] if r else []
        if row:
            y = row[0]["y"]
            st = " ".join(b["t"] for b in boxes if abs(b["y"] - y) < 14 and 450 < b["x"] < 1400)
            m = _re.search(r"(\d{1,3})\s*%", st)
            if "subiendo" in norm(st) and m:                       # subida: % real, MB/s y lo que falta
                pct = min(100, int(m.group(1))) / 100
                now = time.time()
                if up0 is None:
                    up0 = (now, pct)
                dt, dp = now - up0[0], pct - up0[1]
                speed = dp * total / dt if dt > 0.5 and dp > 0 else 0
                left = (1 - pct) * total / speed if speed else 0
                _prog(0.02 + 0.30 * pct)
                log(f"Subiendo a Imagen: {int(pct * 100)}%  ·  {speed / 1e6:.1f} MB/s  ·  "
                    f"{(pct * total) / 1e6:.0f} de {total / 1e6:.0f} MB" + (f"  ·  quedan {_mmss(left)}" if left else ""))
                live.set("subida", f"{speed / 1e6:.1f} MB/s  ·  {pct * total / 1e6:,.0f} de {total / 1e6:,.0f} MB  ·  "
                                   f"{int(pct * 100)} %" + (f"  ·  quedan {_mmss(left)}" if left else ""))
                if STEP3[0] and left:
                    live.set_eta(core.STEP_IMG, left + expect_edit + _bridge_est(n_dng))
                last = st
            elif "edicion" in norm(st) or "procesando" in norm(st) or "editando" in norm(st):
                if edit_t0 is None and up0 is not None and time.time() - up0[0] > 2:   # subida terminada:
                    from .timing import record                                         # guardar su velocidad real
                    record("subida", n_dng, int((1 - up0[1]) * total), time.time() - up0[0])
                edit_t0 = edit_t0 or time.time()
                el = time.time() - edit_t0
                _prog(0.32 + 0.43 * min(0.97, el / expect_edit))
                log(f"Imagen editando en la nube · {_mmss(el)}  ·  normalmente ~{_mmss(expect_edit)}")
                live.set("subida", "")
                live.set("imagen", f"editando en la nube  ·  {_mmss(el)} (lo normal ~{_mmss(expect_edit)})")
                if STEP3[0]:
                    live.set_eta(core.STEP_IMG, max(0.0, expect_edit - el) + _bridge_est(n_dng))
                last = st
            elif st != last:
                log(f"   Imagen: {st.strip(' •')}")
                last = st
            if "resena" in norm(st) or "revisar ediciones" in norm(st):
                got = _row_count(boxes, y)
                if got is not None and got < n_dng:       # ANTES de descargar (que es lo que cobra)
                    (dng_dir / ".rawflow_imagen_proyecto").unlink(missing_ok=True)
                    raise UIError(f"Imagen solo recibió {got} de {n_dng} fotos en «{project}»: "
                                  "se crea un proyecto nuevo con todas.")
                break
        time.sleep(1.5 if up0 and not edit_t0 else 3)   # durante la subida se mira más seguido

    if not charge_ok:
        raise UIError("Imagen terminó de editar; falta descargar las ediciones (cobra).")
    _download_edits(s, dng_dir, project)


def _download_edits(s: Screen, dng_dir: Path, project: str):
    """Baja las ediciones del proyecto a sus DNG. CANDADO: no sale de aquí sin las ediciones de Imagen en TODOS los
    DNG, comprobadas una a una (JUTIAPA, 22-nov: la descarga de la tanda 1 falló DENTRO de Imagen —DOWNLOAD_XMP_ERROR,
    0 de 120, mientras otra tanda subía— y RawFlow siguió: 120 fotos llegaron a Bridge y a Retouch4me sin Imagen)."""
    log = s.log
    dngs = sorted(dng_dir.glob("*.dng"))
    falta: List[Path] = list(dngs)
    for intento in range(1, DL_TRIES + 1):
        _open_download_dialog(s, dng_dir, project)
        _prog(0.78)
        mark = _imagen_log_mark()
        s.tap("Descargar ediciones", exact=True, timeout=20, pick=-1, after=1)   # el botón, no el título
        log("Imagen: descargando ediciones a los DNG…" + (f" (intento {intento} de {DL_TRIES})" if intento > 1 else ""))
        live.set("imagen", "descargando las ediciones a los DNG…")
        res = _await_download(s, dng_dir, mark, dngs)
        falta = core.without_imagen(dngs)                  # foto a foto: ¿lleva DE VERDAD las ediciones?
        t_w = time.time()                                  # «SUCCESS»: dar unos segundos a que acabe de escribir
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
        _diag(s, "imagen-descarga-fallida")
        if intento < DL_TRIES:
            _open_projects(s)                                # cierra el aviso de error de Imagen
            _wait_uploads_idle(s)                            # otra tanda subiendo llena la red: que acabe antes
            wait = DL_WAIT * intento
            log(f"   Vuelvo a pedir las ediciones del mismo proyecto en {wait} s (no se crea otro)…")
            t_w = time.time()
            while time.time() - t_w < wait:
                if s.should_stop():
                    raise UIError("Cancelado.")
                time.sleep(1)
    raise UIError(f"Imagen no puso sus ediciones en {len(falta)} de {len(dngs)} fotos de «{project}» "
                  f"(probé {DL_TRIES} veces): sin Imagen no se sigue.")


DL_TRIES = 3          # descargas de ediciones de un mismo proyecto antes de dar el paso por fallido
DL_WAIT = 15          # segundos entre una y otra (×1, ×2…), después de que acaben las subidas
PROMPT_WAIT = 15      # tras «SUCCESS», segundos esperando la pregunta «¿Dónde quieres revisar?»
SETTLE_WAIT = 60      # tras «SUCCESS», segundos para que los DNG terminen de llevar las ediciones
IMAGEN_LOGS = Path.home() / "Library/Application Support/ImagenAI/logs"


def _open_download_dialog(s: Screen, dng_dir: Path, project: str):
    """Lista de proyectos → «Revisar Ediciones» del proyecto → diálogo «Descargar ediciones» (comprobando que
    descarga en la carpeta de este evento)."""
    _close_uploads(s)
    row, _ = _find_row(s, project)
    if not row:
        raise UIError(f"No encuentro el proyecto «{project}» en Imagen.")
    s.tap("Revisar Ediciones", region=(1000, row["y"] - 25, 1400, row["y"] + 30), after=1)
    # a veces sale un menú («Descargar todas las ediciones») y a veces directamente el diálogo
    b = s.wait("Descargar todas las ediciones", "Tus fotos siguen", timeout=20)
    if "todas" in norm(b["t"]):
        s.tap("Descargar todas las ediciones", timeout=10, after=1)
        s.wait("Tus fotos siguen", "Descargar ediciones", timeout=20)
    boxes = s.ocr()
    paths = [b["t"] for b in boxes if "/" in b["t"] and ("users" in norm(b["t"]) or "dngs" in norm(b["t"]))]
    if paths and not any(n in norm(t) for t in paths for n in _dest_names(dng_dir)):
        raise UIError(f"Imagen quiere descargar en otra carpeta ({paths[0]}), no en «{dng_dir.parent.name}».")


def _wait_uploads_idle(s: Screen, max_s: int = 600):
    """Mientras otra tanda SUBE, la descarga de ediciones puede fallar (JUTIAPA: la tanda 5 subía y la descarga de la
    tanda 1 dio DOWNLOAD_XMP_ERROR a los 30 s). Espera a que en la lista no quede nada «Subiendo» (máx. max_s)."""
    t0, said = time.time(), False
    while time.time() - t0 < max_s:
        if s.should_stop():
            raise UIError("Cancelado.")
        _close_uploads(s)
        if not any("subiendo" in norm(b["t"]) for b in s.ocr()):
            return
        if not said:
            s.log("   Imagen está subiendo otra tanda: espero a que termine para volver a descargar…")
            said = True
        time.sleep(5)


def _imagen_log() -> Optional[Path]:
    """El registro de la ventana de Imagen (el del día): ahí anota cómo terminó cada descarga de ediciones."""
    try:
        logs = sorted(IMAGEN_LOGS.glob("renderer.*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return logs[-1] if logs else None


def _imagen_log_mark():
    p = _imagen_log()
    try:
        return (p, p.stat().st_size) if p else (None, 0)
    except OSError:
        return (None, 0)


def download_result(mark, dng_dir: Path):
    """Cómo terminó (según Imagen) la descarga de ediciones a `dng_dir` pedida después de `mark`:
    (estado, correctas, total, código de error), p. ej. ("FAILURE", 0, 120, "DOWNLOAD_XMP_ERROR"). None = aún nada."""
    import re
    p0, size0 = mark
    p = _imagen_log()
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
    want = unicodedata.normalize("NFC", str(dng_dir)).rstrip("/").lower()
    res = None
    for line in "\n".join(chunks).splitlines():
        if "edit_download_directory_xmp_finished" not in line:
            continue
        m = re.search(r'"path":"((?:[^"\\]|\\.)*)"', line)
        try:
            path = json.loads('"' + m.group(1) + '"') if m else ""
        except ValueError:
            path = m.group(1) if m else ""
        if unicodedata.normalize("NFC", path).rstrip("/").lower() != want:
            continue
        st = re.search(r'"status":"(\w+)"', line)
        okn = re.search(r'"totalSuccess":(\d+)', line)
        tot = re.search(r'"total":(\d+)', line)
        code = re.search(r'"errorCode":"(\w+)"', line)
        res = (st.group(1) if st else "", int(okn.group(1)) if okn else 0, int(tot.group(1)) if tot else 0,
               code.group(1) if code else "")
    return res


def _await_download(s: Screen, dng_dir: Path, mark, dngs: List[Path]):
    """Espera a que Imagen termine de bajar las ediciones. Su registro dice al momento si fue bien o mal (no hace
    falta esperar minutos para saber que falló). Si fue bien, Imagen pregunta dónde revisar → «Bridge (Adobe
    Camera Raw)». Sin registro: hasta que todos los DNG lleven las ediciones (o se acabe el tiempo)."""
    t0, t_ok, asked = time.time(), None, False
    limit = max(300, 2 * len(dngs))
    res = None
    while time.time() - t0 < limit:
        if s.should_stop():
            raise UIError("Cancelado.")
        res = download_result(mark, dng_dir) if mark[0] else None
        if not asked:
            b = s.find("Bridge (Adobe Camera Raw)")
            if b:
                s.click(b[0]["x"] + b[0]["w"] / 2, b[0]["y"] + b[0]["h"] / 2)
                asked = True
                time.sleep(1)
        if res and res[0].upper() != "SUCCESS":
            return res                                        # falló: se sabe ya, sin esperar
        if res:
            t_ok = t_ok or time.time()
            if asked or time.time() - t_ok > PROMPT_WAIT:     # si Imagen recuerda la elección, no pregunta
                return res
        elif not mark[0] and not core.without_imagen(dngs):   # sin registro de Imagen: los propios DNG
            return None
        time.sleep(1)
    return res


def dxo_modules_dialog(log: Log, should_stop=lambda: False) -> Optional[str]:
    """PureRAW (en modo lote) se queda parado con el aviso «Módulos ópticos DxO» cuando llega un equipo nuevo (cámara
    + objetivo) cuyo módulo aún no tiene: sin respuesta NO procesa nada (medido: Nikon Z6 II + Nikkor Z 28-75,
    0 de 480 en 3 intentos). Si el aviso está en pantalla se acepta lo que propone PureRAW: descargar y guardar ese
    módulo (= las mismas correcciones ópticas que si pulsaras «Guardar» tú). Devuelve el equipo, o None si no hay aviso."""
    if check_permissions():
        return None
    s = Screen(log, should_stop)

    def find_dialog():
        boxes = s.ocr()
        title = [b for b in boxes if "modulos opticos" in norm(b["t"]) or "optics modules" in norm(b["t"])]
        if not title:
            return boxes, None, []
        btn = [b for b in boxes if norm(b["t"]).strip() in ("guardar", "save") and b["y"] > title[0]["y"]]
        return boxes, title[0], btn
    # PureRAW va en segundo plano y otras ventanas (p. ej. Imagen, que se abre por adelantado mientras DxO trabaja)
    # tapan su aviso: primero se trae delante y DESPUÉS se mira la pantalla
    subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                    '(every process whose name is "PureRAWv6") to true'], capture_output=True, timeout=10)
    time.sleep(0.6)
    boxes, title, btn = find_dialog()
    if title is None or not btn:
        return None
    b = max(btn, key=lambda x: x["y"])
    gear = [x["t"].strip(" •·") for x in boxes if "+" in x["t"] and title["y"] < x["y"] < b["y"]]
    s.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
    act(f"DxO: módulo óptico «{gear[0] if gear else '?'}» → Guardar")
    return gear[0] if gear else "un equipo nuevo"


def _dest_names(dng_dir: Path) -> List[str]:
    """Lo que tiene que verse en la ruta de descarga de Imagen: el nombre del evento y, si es una tanda
    («2 DNGs/Tanda 2»), también vale el de la tanda (la ruta larga puede salir cortada por el medio)."""
    if dng_dir.name.startswith("Tanda ") and dng_dir.parent.parent != dng_dir.parent:
        return [norm(dng_dir.parent.parent.name), norm(dng_dir.name)]
    return [norm(dng_dir.parent.name)]


def _edits_in_one(p: Path) -> bool:
    """¿Tiene ya las ediciones de Imagen? (foto a foto, ver core.has_imagen_edits)"""
    return core.has_imagen_edits(p)


def _edits_in(dng_dir: Path) -> bool:
    return not core.without_imagen(dng_dir.glob("*.dng"))


def _bridge_app() -> str:
    apps = sorted(Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app"))
    if not apps:
        raise UIError("No encuentro Adobe Bridge en Aplicaciones.")
    return str(apps[-1])


def _bridge_prompts(s: Screen):
    """Avisos de Bridge que bloquean (p. ej. «¿Desea abrir automáticamente el cuadro de diálogo de importación?»
    al conectar una memoria): «No volver a mostrar» + «No»."""
    for _ in range(3):
        boxes = s.ocr()
        q = s.find("abrir automaticamente el cuadro", boxes=boxes) or s.find("cuadro de dialogo importar", boxes=boxes)
        if not q:
            return
        s.log("Bridge: cerrando el aviso de importación…")
        nv = s.find("No volver a mostrar", boxes=boxes)
        if nv:
            s.click(nv[0]["x"] + 8, nv[0]["y"] + nv[0]["h"] / 2)
            time.sleep(0.4)
        no = [b for b in boxes if norm(b["t"]) == "no" and b["y"] > q[0]["y"]]
        if no:
            b = no[0]
            s.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        else:
            s.keys("53")
        time.sleep(1.2)


def archive_project(s: Screen, project: str) -> bool:
    """Archiva en Imagen el proyecto del evento (ya descargado y exportado) para que la lista quede limpia.
    Se puede recuperar desde «Archivados». Comprueba que la fila no se mueve antes de abrir su menú y que,
    después, ese proyecto (y solo ese) desapareció de la lista."""
    s.activate(IMAGEN_APP)
    _close_uploads(s)
    for _ in range(3):
        r1 = _row(s, project)
        if not r1:
            return False
        time.sleep(0.7)
        boxes = s.ocr()
        r2 = _row(s, project, boxes)
        if not r2 or abs(r2["y"] - r1["y"]) > 3:          # la lista aún se está moviendo
            continue
        before = {_key(b["t"]) for b in boxes if b["x"] < 420 and 140 < b["y"] < 900}
        x, y, w, h = _win()
        s.click(x + w - 51, r2["y"] + r2["h"] / 2)          # «⋮» al final de esa fila
        try:
            s.wait("Archivar", timeout=4)
        except UIError:
            s.keys("53")
            continue
        opt = [b for b in s.ocr() if norm(b["t"]).strip(" •·") == "archivar"]
        if not opt:
            s.keys("53")
            continue
        s.click(opt[0]["x"] + opt[0]["w"] / 2, opt[0]["y"] + opt[0]["h"] / 2)
        time.sleep(1.5)
        after = {_key(b["t"]) for b in s.ocr() if b["x"] < 420 and 140 < b["y"] < 900}
        gone = before - after
        if _key(project) in gone:
            s.log(f"Imagen: proyecto «{project}» archivado (la lista queda limpia).")
            return True
    s.log(f"   (no pude archivar «{project}» en Imagen)")
    return False


BRIDGE_PROG = [lambda f: None]


def clean_exports(out_dir: Path) -> int:
    """«DSC0001-DxO_DeepPRIME XD3.jpg» → «DSC0001.jpg». Si ya había una de un intento anterior, la nueva
    la sustituye (nunca quedan fotos repetidas). Devuelve cuántas fotos hay."""
    for p in sorted(out_dir.glob("*.jpg")):
        if "-DxO" in p.stem:
            os.replace(p, out_dir / (p.stem.split("-DxO")[0] + ".jpg"))
    return len([p for p in out_dir.glob("*.jpg") if "-DxO" not in p.stem])


def _diag(s: Screen, label: str, boxes=None):
    """Guarda lo que ve RawFlow cuando algo no aparece (captura sin la pantalla de RawFlow + app delante + textos
    leídos) en Application Support/RawFlow/diagnostico (las 12 últimas), para entender el fallo sin repetirlo."""
    try:
        d = Path.home() / "Library/Application Support/RawFlow/diagnostico"
        d.mkdir(parents=True, exist_ok=True)
        for old in sorted(d.glob("*"))[:-22]:
            old.unlink()
        stem = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
        s.run("shot", str(stem) + ".png")
        front = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first process '
                                'whose frontmost is true'], capture_output=True, text=True, timeout=10).stdout.strip()
        txt = [f"{round(b['x'])},{round(b['y'])} {b['t']}" for b in (boxes or [])][:80]
        Path(str(stem) + ".txt").write_text(f"delante: {front}\n" + "\n".join(txt), encoding="utf-8")
        act(f"diagnóstico {stem.name} (delante: {front})")
    except Exception:  # noqa: BLE001
        pass


def quality_is_12(boxes: List[dict], label: dict) -> bool:
    """¿La casilla numérica de «Calidad de imagen» ya dice 12? (está a la izquierda del rótulo Baja/…/Máxima,
    en la misma fila). «Máxima» sola no basta: también es 10 u 11."""
    if norm(label["t"]) != "maxima":
        return False
    cy = label["y"] + label["h"] / 2
    return any(b["t"].strip() == "12" and abs(b["y"] + b["h"] / 2 - cy) < 12 and label["x"] - 140 < b["x"] < label["x"]
               for b in boxes)


def bridge_export(s: Screen, dng_dir: Path, out_dir: Path, expected: int) -> int:
    log = s.log
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*-DxO*.jpg"):              # restos de un intento anterior a medias
        p.unlink()
    before = {p.name for p in out_dir.glob("*.jpg")}
    subprocess.run(["open", "-a", _bridge_app(), str(dng_dir)], check=False)
    time.sleep(0.5)
    s.activate(bundle=BRIDGE_ID, process=Path(_bridge_app()).stem)
    for k in range(9):                  # ya abierto (se abrió mientras Imagen editaba): 2-3 s; en frío ~25 s
        try:
            s.wait(f"Contenido: {dng_dir.name}", timeout=10)
            break
        except UIError:
            if k == 8:
                raise
            # un Bridge que aún estaba arrancando ignora la carpeta pedida: se vuelve a pedir
            subprocess.run(["open", "-a", _bridge_app(), str(dng_dir)], check=False)
            s.activate(bundle=BRIDGE_ID, process=Path(_bridge_app()).stem)
    _bridge_prompts(s)
    log(f"Bridge: exportando JPEG a «{out_dir.name}»…")
    s.keys("53")
    # con Bridge ya abierto, «Contenido: X» sale al instante pero las miniaturas tardan unos segundos más (más aún
    # si Retouch4me está usando la GPU, como pasa por tandas): esperar a que se vean los nombres de los DNG
    t0w, reopened, diag = time.time(), 0, False
    while True:
        boxes = s.ocr()
        thumbs = [b for b in boxes if ".dng" in norm(b["t"]) and 250 < b["x"] < 1040 and b["y"] > 180]
        if thumbs:
            break
        if s.should_stop():
            raise UIError("Cancelado.")
        el = time.time() - t0w
        if el > 4 and not diag:                       # para entender el caso si vuelve a pasar
            diag = True
            _diag(s, "bridge-sin-dng", boxes)
        if el > 8 * (reopened + 1) and reopened < 2:  # recuperar: volver a pedir la carpeta y traer Bridge delante
            reopened += 1
            act(f"Bridge sin DNG a la vista: reabro «{dng_dir.name}» ({reopened})")
            subprocess.run(["open", "-a", _bridge_app(), str(dng_dir)], check=False)
            s.activate(bundle=BRIDGE_ID, process=Path(_bridge_app()).stem)
        if el > 30:
            _diag(s, "bridge-sin-dng-final", s.ocr())
            raise UIError("Bridge no muestra los DNG.")
        time.sleep(0.3)
    t = min(thumbs, key=lambda b: (b["y"], b["x"]))
    s.click(t["x"] + 35, t["y"] - 60)            # una miniatura
    time.sleep(0.6)
    s.keys("a", "command")                        # todas
    time.sleep(1)
    # destino del arrastre: «Exportación personalizada» (el OCR a veces no la lee por su icono;
    # está justo encima de «Exportar a DNG»)
    boxes = s.ocr()
    cp = s.find("Exportación personalizada", boxes=boxes) or s.find("Exportacion personalizada", boxes=boxes)
    if cp:
        tx, ty = cp[0]["x"] + cp[0]["w"] / 2, cp[0]["y"] + cp[0]["h"] / 2
    else:
        ed = s.find("Exportar a DNG", boxes=boxes)
        if not ed:
            raise UIError("No encuentro el panel «Exportar» de Bridge (Ventana → Exportar).")
        tx, ty = ed[0]["x"] + 60, ed[0]["y"] + ed[0]["h"] / 2 - 30
    for k in range(3):                 # si Bridge aún cargaba miniaturas, el arrastre no cuenta: repetirlo aquí
        s.run("drag", round(t["x"] + 35), round(t["y"] - 70), round(tx), round(ty))
        try:
            s.wait("Guardar en", "Opciones de guardado", timeout=8)
            break
        except UIError:
            if k == 2:
                raise
            log("   Bridge no recibió el arrastre: lo repito…")
            s.keys("53")
            s.click(t["x"] + 35, t["y"] - 60)
            time.sleep(0.6)
            s.keys("a", "command")
            time.sleep(1.0)
    time.sleep(0.3)
    # Guardar en → Carpeta específica → Explorar → ruta
    g = s.wait("Ubicación del archivo original", "Carpeta específica", timeout=10)
    if "ubicacion" in norm(g["t"]):
        s.click(g["x"] + 100, g["y"] + g["h"] / 2)
        s.tap("Carpeta específica", timeout=5, after=0.3)
    s.tap("Explorar", timeout=10, region=(300, 350, 700, 500), after=0.2)
    s.wait("Elegir", exact=True, timeout=10)          # el selector de carpetas ya está abierto
    s.goto_folder(out_dir)
    s.tap("Elegir", exact=True, timeout=15, after=0.5)
    # contraer «Opciones de guardado» para ver la calidad y ponerla en 12
    og = s.wait("Opciones de guardado", timeout=10)
    for _ in range(3):
        if not s.find("Guardar en", exact=True):
            break
        s.click(og["x"] - 20, og["y"] + og["h"] / 2)     # la flecha ˅ a la izquierda
        time.sleep(0.6)
    fmt = s.wait("Formato", exact=True, timeout=10)
    boxes = s.ocr()
    if not [b for b in boxes if norm(b["t"]) == "jpeg" and abs(b["y"] - fmt["y"]) < 50]:
        s.click(fmt["x"] + 200, fmt["y"] + 26)
        s.tap("JPEG", exact=True, timeout=5, after=1)
    q = s.wait("Calidad de imagen", timeout=10)
    qboxes = s.ocr()
    lv = [b for b in qboxes if norm(b["t"]) in ("baja", "media", "alta", "maxima")
          and 0 < b["y"] - q["y"] < 45 and b["x"] > q["x"] + 150]
    if not lv:
        raise UIError("No encuentro la calidad JPEG en el diálogo de Bridge.")
    if not quality_is_12(qboxes, lv[0]):                         # Bridge suele recordar el 12 de la última vez
        s.click(lv[0]["x"] - 14, lv[0]["y"] + lv[0]["h"] / 2, 2)   # casilla numérica a la izquierda
        time.sleep(0.3)
        s.keys("a", "command")
        s.type("12")
        s.keys("48")
        time.sleep(0.8)
        if not s.find("Máxima"):
            raise UIError("No pude poner la calidad JPEG en máxima en Bridge.")
    else:
        act("calidad ya en 12")
    s.tap("Exportar", exact=True, region=(780, 180, 1100, 260), after=0.5)
    t0 = last_t = time.time()
    last_n = 0
    while True:                                  # vigilante: sin JPEG nuevo mucho más de lo normal → reintento
        new = [p for p in out_dir.glob("*.jpg") if p.name not in before]
        if len(new) >= expected and files_settled(new, 0.5):
            break
        if s.should_stop():
            raise UIError("Cancelado.")
        if len(new) != last_n:
            last_n, last_t = len(new), time.time()
            BRIDGE_PROG[0](last_n / max(1, expected))
            el = last_t - t0
            log(f"Bridge: {last_n} de {expected} JPEG" + (f"  ·  quedan {_mmss(el / last_n * (expected - last_n))}"
                                                           if last_n else ""))
            if last_n:
                live.set("bridge", f"{last_n:,} de {expected:,} JPEG  ·  {el / last_n:.1f} s/foto  ·  calidad 12")
                if last_n >= 3:                        # Bridge es lo último del paso 3: su ritmo real manda
                    live.set_eta(core.STEP_IMG, el / last_n * (expected - last_n))
        per = (last_t - t0) / last_n if last_n else 0
        if time.time() - last_t > (max(120, per * 8) if last_n else 5 * 60):
            raise UIError(f"Bridge dejó de exportar ({last_n} de {expected}).")
        time.sleep(0.5)
    n = clean_exports(out_dir)
    s.keys("53")
    log(f"✓  Bridge: {n} JPEG en «{out_dir.name}».")
    live.set("bridge", "")
    return n


def archive_event(dng_dir: Path, log: Log, should_stop=lambda: False) -> None:
    """Archiva en Imagen el proyecto del evento (ya descargado y exportado). Se puede llamar en paralelo con el
    retoque por motor directo, que no usa la pantalla: así no suma tiempo al evento."""
    pf = Path(dng_dir) / ".rawflow_imagen_proyecto"
    if not pf.exists():
        return
    try:
        if archive_project(Screen(log, should_stop), pf.read_text(encoding="utf-8").strip()):
            pf.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log(f"   (no pude archivar el proyecto en Imagen: {e})")


def run(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False,
        photo_type: str = "Bodas", out_dir: Optional[Path] = None, progress=lambda f: None,
        archive: bool = True, project: Optional[str] = None) -> Path:
    """Imagen (si aún no editó) + Bridge para los DNG de event_dir/dng_subfolder. `dng_subfolder` puede ser
    «2 DNGs» (todo el evento) o «2 DNGs/Tanda N» (una tanda); `project` = nombre del proyecto en Imagen."""
    PROG[0] = progress
    STEP3[0] = True
    try:
        return _run(event_dir, dng_subfolder, log, should_stop, photo_type, out_dir, archive, project)
    finally:
        STEP3[0] = False
        live.set("imagen", "")


def bridge_only(event_dir: Path, dng_subfolder: str, log: Log, should_stop=lambda: False,
                out_dir: Optional[Path] = None, progress=lambda f: None) -> Path:
    """Imagen AI DESACTIVADO (doble clic en su paso): solo Bridge, con los DNG tal cual, sin pedir nada a Imagen."""
    PROG[0] = progress
    STEP3[0] = True
    try:
        miss = check_permissions()
        if miss:
            raise UIError(f"RawFlow necesita permiso de {miss} (botón «Permisos»).")
        dng_dir = event_dir / dng_subfolder
        dngs = [p for p in dng_dir.glob("*") if p.suffix.lower() == ".dng"]
        if not dngs:
            raise UIError(f"No hay DNG en «{dng_dir}».")
        s = Screen(log, should_stop)
        dismiss_crash_dialogs()
        out = Path(out_dir) if out_dir else event_dir / FINAL_SUBFOLDER
        BRIDGE_PROG[0] = lambda f: _prog(f)
        t0 = time.monotonic()
        bridge_export(s, dng_dir, out, len(dngs))
        _prog(1.0)
        from .timing import record
        record("bridge", len(dngs), sum(p.stat().st_size for p in dngs), time.monotonic() - t0)
        return out
    finally:
        STEP3[0] = False
        live.set("imagen", "")


def _run(event_dir, dng_subfolder, log, should_stop, photo_type, out_dir, archive, project) -> Path:
    miss = check_permissions()
    if miss:
        raise UIError(f"RawFlow necesita permiso de {miss} (botón «Permisos»).")
    dng_dir = event_dir / dng_subfolder
    dngs = [p for p in dng_dir.glob("*") if p.suffix.lower() == ".dng"]
    if not dngs:
        raise UIError(f"No hay DNG en «{dng_dir}».")
    s = Screen(log, should_stop)
    nbytes = sum(p.stat().st_size for p in dngs)
    from .timing import record
    mark = dng_dir / ".rawflow_imagen_ok"
    mark_was = mark.exists()
    if mark.exists():                                   # reintento: las ediciones ya se descargaron (no cobrar otra vez)
        falta = core.without_imagen(dngs)
        if falta:                                       # CANDADO: a Bridge solo DNG con las ediciones de Imagen
            mark.unlink(missing_ok=True)
            raise UIError(f"{len(falta)} DNG no llevan las ediciones de Imagen ({falta[0].name}…): "
                          "no se exportan sin Imagen.")
        log("Imagen ya editó estas fotos: paso directo a Bridge.")
    else:
        dismiss_crash_dialogs()
        t0 = time.monotonic()
        if imagen_edit(s, dng_dir, project or event_dir.name, photo_type):
            record("imagen", len(dngs), nbytes, time.monotonic() - t0)   # tiempo real en la nube
        falta = core.without_imagen(dngs)
        if falta:
            raise UIError(f"Imagen no puso sus ediciones en {len(falta)} de {len(dngs)} DNG ({falta[0].name}…).")
        mark.write_text(time.strftime("%Y-%m-%d %H:%M"))
    dismiss_crash_dialogs()
    out = Path(out_dir) if out_dir else event_dir / FINAL_SUBFOLDER
    t0 = time.monotonic()
    base = 0.0 if mark_was else 0.80                  # Imagen ya hecho antes: el paso es solo Bridge
    BRIDGE_PROG[0] = lambda f: _prog(base + (1 - base) * f)
    bridge_export(s, dng_dir, out, len(dngs))
    # CANDADO: cada JPEG exportado lleva las ediciones de Imagen; el que no, se borra y el paso se repite
    sin = core.without_imagen(j for j in (out / (q.stem.split("-DxO")[0] + ".jpg") for q in dngs) if j.exists())
    if sin:
        for j in sin:
            j.unlink(missing_ok=True)
        raise UIError(f"Bridge exportó {len(sin)} JPEG sin las ediciones de Imagen ({sin[0].name}…): se repiten.")
    _prog(1.0)
    record("bridge", len(dngs), nbytes, time.monotonic() - t0)
    if archive:                                        # todo descargado y exportado: archivar en Imagen
        archive_event(dng_dir, log, should_stop)
    return out

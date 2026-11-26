"""La secuencia completa de RawFlow, siempre igual y en orden:

  1  Memoria → <evento>/Original            (mueve los RAW: copia, verifica y borra de la memoria)
  2  DxO PureRAW (CINEMA) → <evento>/DNGs
  3  Imagen AI → <evento>/DNGs/Imagen AI    (respaldo de lo que entrega Imagen)
  4  Retouch4me Arams (CINEMA) → <evento>/4 Fotos finales (de «3 Imagen AI»)

Corre en un hilo; si un paso falla, se puede reanudar desde ese paso con la misma carpeta.

Nunca dos trabajos de GPU a la vez (medido: DxO + Retouch4me juntos → Retouch4me 5× más lento y DxO 1,7×). Cada
paso cierra su programa al terminar (DxO; Imagen y Bridge; Arams si hizo falta su ventana): no quedan ventanas.

Eventos grandes (core.tanda_size): mientras DxO trabaja, cada tanda de fotos ya listas sube a Imagen (red, no GPU)
en su propio proyecto («Tanda N <evento>», carpeta «2 DNGs/Tanda N» con enlaces a los DNG). Al terminar DxO solo
queda descargar las ediciones, devolverlo todo a «2 DNGs» y exportar con Bridge de una vez; luego Retouch4me solo.
Las carpetas finales quedan igual que sin tandas.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List

from PySide6.QtCore import QThread, Signal

from . import core, live


class LaneFailed(RuntimeError):
    """Falló el carril de fondo (DxO del resto o Imagen/Bridge de la tanda 2): se reintenta desde ESE paso."""

    def __init__(self, msg: str, step: int):
        super().__init__(msg)
        self.step = step

class SetupMissing(RuntimeError):
    """El programa está instalado pero le falta algo que RawFlow no puede arreglar solo (el preajuste, un plugin…):
    reintentar no sirve y omitir el paso daría un «FIN OK» sin ese paso. El evento se para con el aviso."""


# Motivos de available()/usable() por los que el paso SÍ se omite: el programa no está en este equipo o son pruebas.
# Cualquier otro motivo (falta el preajuste, plugins de máscara…) para el evento con SetupMissing.
NOT_HERE = ("solo en", "desactivado en pruebas", "no encuentro Retouch4me", "no encuentro Luminar",
            "falta r4m_worker")


def not_installed(why: str) -> bool:
    return str(why).startswith(NOT_HERE)


DXO_EXES = ("pureraw", "dxo pureraw")   # .exe de DxO PureRAW (en minúsculas): sus ventanas van minimizadas

RAW_DIR = core.RAW_SUBFOLDER          # "1 Original"
DNG_DIR = core.OUTPUT_SUBFOLDER       # "2 DNGs"
IMAGEN_DIR = core.IMAGEN_SUBFOLDER    # «3 Imagen AI» (en la carpeta del evento, desde 4.15)
JPEG_SIN_DIR = core.JPEG_SIN_SUBFOLDER  # «3 JPEG sin Imagen», si Imagen está DESACTIVADO (doble clic en su paso)
from .core import STEP_DXO, STEP_FINAL, STEP_IMG, STEP_LUM, STEP_MOVE, STEP_PRES, STEP_RET, STEP_SEL  # noqa: E402
TOGGLEABLE = (STEP_SEL, STEP_IMG, STEP_RET, STEP_LUM, STEP_PRES)   # con doble clic (Mover y DxO, siempre)
FINAL_DIR = core.FINAL_SUBFOLDER      # «4 Fotos finales»
LUM_DIR = core.LUMINAR_SUBFOLDER      # «5 Fotos finales Luminar»
PRES_DIR = core.PRES_SUBFOLDER        # «6 Presentación de fotos»
CINESLIDE_DIR = Path.home() / "Library/Application Support/CineSlide"
PRES_THEME = "4"                      # Tema 4 · Montón de fotos

STEPS = [                              # (paso, la carpeta donde deja lo suyo), en el orden de STEP_*
    ("Mover", "→ Original"),
    ("Selección IA", "→ Fotos buenas"),
    ("DxO", "→ DNGs"),
    ("Imagen AI", "→ Imagen AI"),
    ("Retouch4me", "→ Fotos finales"),
    ("Luminar", "→ Finales Luminar"),
    ("CineSlide", "→ Presentación"),
]


def deliver_dir(ev: Path, off=()) -> Path:
    """Las fotos que se entregan y con las que se hace la presentación: las de Luminar si ese paso está activo y ya
    exportó; si no, las de Retouch4me («4 Fotos finales»)."""
    lum = Path(ev) / LUM_DIR
    if STEP_LUM not in set(off) and lum.is_dir() and any(lum.glob("*.jp*g")):
        return lum
    return Path(ev) / FINAL_DIR


def pres_name(v: Path, n: int) -> str:
    """Nombre del video en «6 Presentación de fotos»: «Presentación horizontal.mp4» / «Presentación vertical.mp4»."""
    s = v.stem.lower()                    # CineSlide añade la fecha: «Presentación_horizontal 2026-11-26 08.00»
    for k in ("horizontal", "vertical"):
        if k in s:
            return f"Presentación {k}.mp4"
    return "Presentación.mp4" if n == 1 else v.name


def presentation_current(out: Path, final: Path) -> bool:
    """¿Hay video y es posterior a TODAS las fotos finales? (si se rehízo alguna foto, el video se rehace)"""
    vids = list(out.glob("*.mp4")) if out.is_dir() else []
    names = {v.name.lower() for v in vids}
    if not {"presentación horizontal.mp4", "presentación vertical.mp4"} <= names:   # faltan los dos formatos
        return False
    newest = max((p.stat().st_mtime for p in final.glob("*.jp*g")), default=0.0) if final.is_dir() else 0.0
    return min(v.stat().st_mtime for v in vids) >= newest - 60


def event_root(path: Path) -> "Path | None":
    """Si una carpeta arrastrada es un evento de RawFlow (o una de sus carpetas), el evento; si no, None."""
    path = Path(path)
    if (path / RAW_DIR).is_dir():
        return path
    if core.is_event_dir(path.name) and ((path.parent / RAW_DIR).is_dir() or (path.parent / DNG_DIR).is_dir()):
        return path.parent
    return None


def resume_step(ev: Path, off=()):
    """(paso por el que seguir, qué falta) de un evento. Si está todo: (Imagen, ""): ese paso revisa foto a foto que
    todo lleve Imagen, y Retouch4me y CineSlide rehacen solo lo que no esté al día (como se arregló JUTIAPA).
    None = la carpeta no tiene fotos en «1 Original»."""
    off = set(off)
    core.migrate_event_layout(ev)                      # evento de antes de 4.15: carpetas 1-5
    raw, dngs, img, final = folders(ev, STEP_IMG not in off)
    goods = core.good_raws(ev)
    if not goods:
        return None
    done = core.processed_stems(dngs)
    sueltas = [q for q in goods if q.parent == raw and q.stem.lower() not in done]
    from . import seleccion
    if sueltas and STEP_SEL not in off and seleccion.engine():
        return STEP_SEL, "separar las fotos buenas y malas"
    aparte = set(core.apartadas(ev))
    want = {q.stem.lower() for q in goods} - aparte
    if not want <= done:
        return STEP_DXO, "DNG"
    n_dng = len([q for q in dngs.glob("*.dng") if core.dng_base(q) not in aparte])
    if len(core.jpg_stems(img)) < n_dng:
        return STEP_IMG, "JPEG"
    n_fin = len([q for q in final.glob("*.jp*g") if not q.name.startswith(".")]) if final.is_dir() else 0
    if n_fin < len(core.jpg_stems(img)):
        return STEP_RET, "fotos finales"
    if STEP_LUM not in off:
        from . import luminar_win
        lum = ev / LUM_DIR
        n_lum = len([q for q in lum.glob("*.jp*g") if not q.name.startswith(".")]) if lum.is_dir() else 0
        if luminar_win.available()[0] and n_lum < n_fin:
            return STEP_LUM, "las fotos de Luminar"
    if STEP_PRES not in off and not presentation_current(ev / PRES_DIR, deliver_dir(ev, off)):
        return STEP_PRES, "la presentación"
    return STEP_IMG, ""


def folders(event: Path, imagen: bool = True):
    dngs = event / DNG_DIR
    return event / RAW_DIR, dngs, event / (IMAGEN_DIR if imagen else JPEG_SIN_DIR), event / FINAL_DIR


_SM_FLAG = Path.home() / "Library/Application Support/RawFlow/stage_manager_on"


def stage_manager(on: bool):
    """Apaga (on=False) el Stage Manager mientras RawFlow trabaja, recordando que estaba encendido."""
    import subprocess
    if core.SYSTEM != "Darwin" or on:
        return
    r = subprocess.run(["defaults", "read", "com.apple.WindowManager", "GloballyEnabled"],
                       capture_output=True, text=True).stdout.strip()
    if r == "1":
        _SM_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _SM_FLAG.write_text("1")
        subprocess.run(["defaults", "write", "com.apple.WindowManager", "GloballyEnabled", "-bool", "false"])
        time.sleep(1.0)


# Nunca se cierran: RawFlow, el Finder y lo que es parte de macOS.
# Al empezar un evento se cierra TODO lo demás, sin excepción (pedido del usuario): solo sigue RawFlow y lo que es
# parte de macOS. Las ventanas del Finder también se cierran.
_KEEP = {"RawFlow", "Finder", "Dock", "SystemUIServer", "Control Center", "Centro de control",
         "Notification Center", "Centro de notificaciones", "loginwindow", "WindowManager", "Python", "python3"}


def quit_other_apps(log=lambda s: None, wait: float = 12.0):
    """Cierra (como ⌘Q, sin forzar) todas las apps abiertas para que RawFlow trabaje sin interrupciones.
    Si una app pide guardar cambios o se niega a cerrar, NO se fuerza (no se pierde nada): se oculta.
    RAWFLOW_NO_CERRAR="App1,App2" añade apps que no se cierran."""
    import os
    import subprocess
    from concurrent.futures.thread import ThreadPoolExecutor   # directo: en Python 3.15 el otro es perezoso y falla
    if core.SYSTEM == "Windows":
        if os.environ.get("RAWFLOW_SIN_APPS") != "1":   # pruebas: en Windows nunca se cierra nada de verdad (en el Mac
            quit_other_apps_windows(log, wait)          # las pruebas simulan osascript y sí lo recorren)
        return
    if core.SYSTEM != "Darwin":
        return
    keep = _KEEP |{x.strip() for x in os.environ.get("RAWFLOW_NO_CERRAR", "").split(",") if x.strip()}
    try:                                                 # ventanas del Finder: fuera también
        subprocess.run(["osascript", "-e", 'tell application "Finder" to close every window'],
                       capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of every process '
                          'whose background only is false'], capture_output=True, text=True, timeout=20).stdout
    names = [n.strip() for n in out.strip().split(",") if n.strip() and n.strip() not in keep]
    if not names:
        kill_orphan_helpers()
        return
    log(f"Cerrando {len(names)} apps: {', '.join(names)}")

    try:
        from . import vm_programas as _vp
        if "UTM" in names and _vp.BUSY:                # RawFlow está instalando una máquina virtual: UTM sigue a la vista
            names.remove("UTM")
            log("UTM sigue abierto: RawFlow está instalando una máquina virtual.")
    except Exception:  # noqa: BLE001
        pass

    def q(n):
        if n == "UTM":
            stop_vms(log)                              # Windows se apaga bien (a la vez que se cierra lo demás)
        subprocess.run(["osascript", "-e", f'with timeout of 8 seconds\n tell application "{n}" to quit\nend timeout'],
                       capture_output=True, timeout=12)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(q, names))
    t0 = time.time()
    left = names
    while left and time.time() - t0 < wait:
        left = [n for n in left if subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0]
        time.sleep(0.5)
    for n in left:                                   # pidió guardar o no quiso cerrarse: solo ocultarla
        subprocess.run(["osascript", "-e", f'tell application "System Events" to set visible of process "{n}" '
                        'to false'], capture_output=True, timeout=10)
    if left:
        log(f"No se cerraron (se ocultan, sin perder nada): {', '.join(left)}")
    kill_orphan_helpers()                            # y los ayudantes que quedaron huérfanos de otra vez


# Windows: lo que es parte de Windows (escritorio, barra de tareas, menú Inicio, búsqueda, teclado en pantalla…) no
# se cierra nunca; tampoco RawFlow ni el programa desde el que se abrió (si RawFlow se abre desde una terminal,
# cerrarla lo cerraría a él). Todo lo demás con ventana, sí (pedido del usuario: «que quede camino libre»).
_KEEP_WIN = {"explorer.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
             "searchapp.exe", "textinputhost.exe", "lockapp.exe", "applicationframehost.exe", "systemsettings.exe",
             "rawflow.exe", "securityhealthsystray.exe", "ctfmon.exe", "dwm.exe", "taskmgr.exe",
             # 25-nov-2026, pedido del usuario: poder usar el navegador (en la otra pantalla) mientras el evento
             # trabaja; antes se cerraba al empezar. Si una ventana suya tapa la app que RawFlow maneja, se minimiza
             # (imagen_win.Screen._uncover), no se cierra.
             "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
# En segundo plano se cierra TODO lo que no es de Windows (Adobe, telemetrías de programas, actualizadores, Claude,
# servidores locales…), salvo lo que cuida el equipo: el control de ventiladores y bombas (sin él, con la tarjeta al
# 100 % durante horas, el equipo podría calentarse) y el antivirus.
_KEEP_BG_WORDS = ("fancontrol", "fan control", "speedfan", "argus", "icue", "nzxt", "liquidctl", "aquasuite",
                  "defender", "securityhealth", "antivirus", "avast", "avg", "eset", "kaspersky", "norton", "mcafee",
                  "bitdefender", "malwarebytes", "sophos", "trend micro",
                  "claude")          # 25-nov-2026, pedido del usuario: Claude abierto para vigilar y arreglar el evento


def windows_apps_to_close(keep_extra=()) -> dict:
    """{pid: (programa, [ventanas])} de las apps con ventana que se cerrarían (sin cerrar nada)."""
    from . import winscreen as ws
    keep = _KEEP_WIN | {x.lower() for x in keep_extra}
    mine = _own_tree()                               # RawFlow y quien lo abrió (terminal, Claude Code…)
    apps: dict = {}
    for w in ws.windows():
        exe = (w.get("exe") or "").lower()
        if not exe or exe in keep or w["pid"] in mine or any(k in exe for k in _KEEP_BG_WORDS):
            continue                                 # (ventiladores y antivirus, aunque tengan ventana: nunca)
        x, y, ww, hh = w["rect"]
        if ww < 50 or hh < 30:                       # ventanitas ocultas de servicio
            continue
        apps.setdefault(w["pid"], (w["exe"], []))[1].append(w["hwnd"])
    return apps


def _kill_tree(p) -> None:
    """Termina un proceso y todos sus subprocesos (si en 5 s no salen, a la fuerza)."""
    import psutil
    try:
        tree = p.children(recursive=True) + [p]
    except psutil.Error:
        return
    for q in tree:
        try:
            q.terminate()
        except psutil.Error:
            pass
    try:
        _, alive = psutil.wait_procs(tree, timeout=5)
    except psutil.Error:                  # uno no deja mirarse (acceso denegado): forzar a todos
        alive = tree
    for q in alive:
        try:
            q.kill()
        except psutil.Error:
            pass


def quit_other_apps_windows(log=lambda s: None, wait: float = 12.0, keep_extra=None) -> dict:
    """Windows: cierra TODAS las apps con ventana (primero como su X, para que las que pueden guarden solas) y luego
    termina, con sus subprocesos, las que se quedan en segundo plano o en la bandeja (Claude, Chrome, Steam…) y las
    que siguen abiertas preguntando algo («¿guardar?»): esas se FUERZAN y lo que no estuviera guardado se pierde
    (decisión del usuario, 24-nov-2026: «que cierre todo sin excepciones»). Después, todo lo de segundo plano
    (background_sweep). Devuelve {"cerradas": [...], "forzadas": [...], "fondo": [...]}."""
    import os
    import psutil
    from . import winscreen as ws
    if keep_extra is None:
        keep_extra = [x.strip() for x in os.environ.get("RAWFLOW_NO_CERRAR", "").split(",") if x.strip()]
        keep_extra = [x if x.lower().endswith(".exe") else x + ".exe" for x in keep_extra]
    apps = windows_apps_to_close(keep_extra)
    if not apps:                                     # ninguna con ventana: igualmente, todo lo de segundo plano
        return {"cerradas": [], "forzadas": [], "fondo": background_sweep(log, keep_extra)}
    names = sorted({exe[:-4] if exe.lower().endswith(".exe") else exe for exe, _ in apps.values()}, key=str.lower)
    log(f"Cerrando {len(names)} apps para que RawFlow tenga el equipo libre: {', '.join(names)}")
    procs = {}
    for pid in apps:
        try:
            procs[pid] = psutil.Process(pid)
        except psutil.Error:
            pass
    for pid, (_, hwnds) in apps.items():
        for h in hwnds:
            ws.close_window(h)
    t0 = time.time()
    while time.time() - t0 < wait:                   # esperar a que salgan o escondan su ventana
        vivas = {w["pid"] for w in ws.windows()}
        if not any(p.is_running() and pid in vivas for pid, p in procs.items()):
            break
        time.sleep(0.5)
    vivas = {w["pid"] for w in ws.windows()}
    forzadas, cerradas = set(), set()
    for pid, p in procs.items():
        exe = apps[pid][0]
        try:
            if not p.is_running():
                cerradas.add(exe)
                continue
            (forzadas if pid in vivas else cerradas).add(exe)   # con ventana todavía = preguntaba algo
            _kill_tree(p)
        except psutil.Error:
            cerradas.add(exe)
    if forzadas:
        log(f"Cerradas a la fuerza (preguntaban algo, p. ej. si guardar; lo no guardado se pierde): "
            f"{', '.join(sorted(forzadas))}")
    fondo = background_sweep(log, keep_extra)
    return {"cerradas": sorted(cerradas - forzadas), "forzadas": sorted(forzadas), "fondo": fondo}


def _own_tree() -> set:
    """PIDs de RawFlow, de los procesos que lo abrieron (terminal, Claude Code…) y de los que RawFlow lanzó."""
    import psutil
    out = set()
    try:
        me = psutil.Process()
        out |= {c.pid for c in me.children(recursive=True)}
        p = me
        while p is not None:
            out.add(p.pid)
            p = p.parent()
    except psutil.Error:
        pass
    return out


def background_sweep(log=lambda s: None, keep_extra=(), protected=(), filtro=None) -> List[str]:
    """Termina (con sus subprocesos) todo lo que sigue corriendo en segundo plano de este usuario y NO es de Windows:
    Adobe (Creative Cloud, CoreSync, IPCBroker…), telemetrías y actualizadores de programas, Claude, Ollama, docks…
    Nunca: lo que está en la carpeta de Windows, `protected`, `_KEEP_WIN`, los ventiladores/antivirus
    (`_KEEP_BG_WORDS`) ni `keep_extra`. `filtro(proc)` (pruebas) limita a qué procesos se aplica.
    Devuelve los nombres que cerró."""
    import os
    import psutil
    sysroot = (os.environ.get("SystemRoot") or r"C:\Windows").lower().rstrip("\\") + "\\"
    keep = _KEEP_WIN | {x.lower() for x in keep_extra}
    protected = set(protected) | _own_tree()
    try:
        me = psutil.Process().username()
    except psutil.Error:
        return []
    procs = list(psutil.process_iter(["pid", "ppid", "name", "exe", "username"]))
    info = {p.pid: p.info for p in procs}

    def kept(i) -> bool:
        low = ((i.get("name") or "") + " " + (i.get("exe") or "")).lower()
        return (i.get("name") or "").lower() in keep or any(w in low for w in _KEEP_BG_WORDS)

    def under_kept(pid) -> bool:
        """¿Cuelga de algo que se queda (Claude)? Sus herramientas (node, pwsh, python, uv…) también se quedan:
        25-nov-2026, el barrido las cerraba y Claude se quedaba sin poder vigilar el evento."""
        seen = set()
        i = info.get(pid)
        while i and i.get("ppid") and i["ppid"] not in seen and len(seen) < 25:
            seen.add(i["ppid"])
            i = info.get(i["ppid"])
            if i and kept(i):
                return True
        return False
    targets = []
    for p in procs:
        try:
            name, exe = (p.info["name"] or ""), (p.info["exe"] or "")
            if (p.info["username"] != me or not exe or exe.lower().startswith(sysroot) or p.pid in protected
                    or kept(p.info) or under_kept(p.pid)):
                continue
            if filtro is not None and not filtro(p):
                continue
            targets.append(p)
        except psutil.Error:
            continue
    if not targets:
        return []
    ram = 0
    for p in targets:
        try:
            ram += p.memory_info().rss
            p.terminate()
        except psutil.Error:
            pass
    def wait(procs, timeout):
        """Como psutil.wait_procs, pero un proceso que no deja mirarse («ASUS DriverHub»: acceso denegado) cuenta
        como vivo en vez de cortar el barrido entero (24-nov-2026: la excepción mataba el hilo de cierre)."""
        t_end = time.monotonic() + timeout
        alive = list(procs)
        while alive and time.monotonic() < t_end:
            still = []
            for p in alive:
                try:
                    if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                        still.append(p)
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    still.append(p)
            alive = still
            if alive:
                time.sleep(0.2)
        return alive
    alive = wait(targets, 4)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    alive = wait(alive, 2)
    names = sorted({(p.info.get("name") or "?") for p in targets if p not in alive}, key=str.lower)
    if names:
        log(f"Cerrados {len(targets) - len(alive)} procesos en segundo plano (≈{ram / 2**30:.1f} GB de RAM): "
            + ", ".join(n[:-4] if n.lower().endswith(".exe") else n for n in names))
    if alive:
        log(f"No se pudieron cerrar (protegidos por Windows o como administrador): "
            + ", ".join(sorted({p.info.get('name') or '?' for p in alive})))
    return names


LAUNCH_CLOSE = ("UTM","ImagenAI", "Retouch4me Arams", "PureRAWv6", "DxO PureRAW 6", "CineSlide")


def close_on_launch(log=lambda s: None, running=None, quit_=None, stop=None) -> list:
    """Al ABRIR RawFlow, todo empieza cerrado: UTM (con Windows apagado BIEN antes) y los programas del proceso
    (DxO, Imagen, Bridge, Retouch4me, CineSlide) que quedaran abiertos de antes se cierran, y sus ayudantes huérfanos.
    UTM solo se queda si RawFlow está instalando una máquina virtual. Tus demás apps no se tocan al abrir: se cierran
    al pulsar Empezar. RAWFLOW_NO_CERRAR="UTM,…" las respeta (pruebas). Devuelve lo que pidió cerrar."""
    import os
    import subprocess
    if core.SYSTEM != "Darwin":
        return []
    keep = {x.strip() for x in os.environ.get("RAWFLOW_NO_CERRAR", "").split(",") if x.strip()}
    running = running or (lambda n: subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0)
    quit_ = quit_ or (lambda names: quit_apps(names, wait=12))
    stop = stop or stop_vms
    names = [n for n in list(LAUNCH_CLOSE) + _bridge_names() if n not in keep and running(n)]
    try:
        from . import vm_programas as _vp
        if "UTM" in names and _vp.BUSY:
            names.remove("UTM")
    except Exception:  # noqa: BLE001
        pass
    if names:
        log(f"Al abrir RawFlow se cierra lo que quedó abierto: {', '.join(names)}")
        if "UTM" in names:
            stop(log)                                 # Windows se apaga bien antes de cerrar UTM
        quit_(names)
    try:
        kill_orphan_helpers()
    except Exception:  # noqa: BLE001
        pass
    return names


VM_STOP_WAIT = 20.0      # s que se espera a que Windows se apague bien antes de apagarlo del todo


def stop_vms(log=lambda s: None, wait: float = 0.0) -> bool:
    """Antes de cerrar UTM: las máquinas virtuales encendidas (Windows usa 4 núcleos y 8 GB) se apagan BIEN, como con
    el botón de encendido; la que a los `wait` s siga encendida se apaga del todo (UTM la cortaría igual al cerrarse).
    Si RawFlow está instalando una máquina virtual (vm_programas.BUSY), no se toca nada y devuelve False."""
    import subprocess
    try:
        from . import vm_programas as vp
    except Exception:  # noqa: BLE001
        return True
    if vp.BUSY:
        log("UTM sigue abierto: RawFlow está instalando una máquina virtual.")
        return False
    try:
        on = [u for u, st, _ in vp.utm_rows() if st == "started"]
    except Exception:  # noqa: BLE001
        return True
    if not on:
        return True
    wait = wait or VM_STOP_WAIT
    log(f"Apagando {len(on)} máquina(s) virtual(es) para dejar el Mac entero a RawFlow…")
    for u in on:
        subprocess.Popen([vp.UTMCTL, "stop", u, "--request"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(1)
        try:
            on = [u for u, st, _ in vp.utm_rows() if st == "started" and u in on]
        except Exception:  # noqa: BLE001
            break
        if not on:
            return True
    for u in on:                                     # no se apagó a tiempo: del todo
        subprocess.run([vp.UTMCTL, "stop", u], capture_output=True, timeout=30)
    return True


_HELPER_APPS = ("com.dxo-labs.PureRAWv6.standalone", "ImagenAI", "com.adobe.bridge", "com.retouch4me.arams")


def full_speed():
    """Todo al 100 % mientras dura el evento: el Mac no se duerme ni baja el ritmo, y macOS no frena
    (App Nap) a DxO, Imagen, Bridge y Retouch4me aunque estén tapados por la pantalla de RawFlow."""
    import os
    import subprocess
    if core.SYSTEM != "Darwin":
        return
    for b in _HELPER_APPS:
        subprocess.run(["defaults", "write", b, "NSAppSleepDisabled", "-bool", "YES"], capture_output=True)
    # sin reposo del sistema, la pantalla ni el disco, mientras RawFlow siga abierta
    return subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])


def prelaunch(*apps: str):
    """Abre apps en segundo plano (sin ponerlas delante) para que ya estén listas cuando toque su paso."""
    import subprocess
    if core.SYSTEM != "Darwin":
        return
    for a in apps:
        subprocess.Popen(["open", "-g", "-a", a], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _bridge_and_arams():
    apps = sorted(str(p) for p in Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app"))[-1:] or [""]
    if Path("/Applications/Retouch4me Arams.app").exists():
        apps.append("/Applications/Retouch4me Arams.app")
    return apps


def _bridge_names() -> list:
    return [Path(p).stem for p in Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app")]


def quit_apps(names, wait: float = 10.0):
    """Cierra esas apps como ⌘Q (sin forzar: si una pide algo, se queda abierta y se cierra al final)."""
    import subprocess
    if core.SYSTEM != "Darwin":
        return
    for n in names:
        try:
            subprocess.run(["osascript", "-e", f'if application "{n}" is running then tell application "{n}" to quit'],
                           capture_output=True, timeout=wait)
        except subprocess.TimeoutExpired:
            pass


_ORPHAN_BUNDLES = ("/Applications/ImagenAI.app/", "/Applications/Retouch4me Arams.app/")


def kill_orphan_helpers() -> int:
    """Procesos de ayuda de Imagen y Retouch4me (imagent, crashpad, motores…) que quedaron huérfanos al cerrarse su
    app (sobre todo si hubo que forzarla): con la app ya cerrada no hacen nada y se quedaban abiertos días.
    Solo los huérfanos (padre = launchd) y nunca el ejecutable principal de la app. Devuelve cuántos cerró."""
    import signal
    import subprocess
    if core.SYSTEM != "Darwin":
        return 0
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:  # noqa: BLE001
        return 0
    rows = [ln.split(None, 2) for ln in out.splitlines() if len(ln.split(None, 2)) == 3]
    n = 0
    for bundle in _ORPHAN_BUNDLES:
        if any(cmd.startswith(bundle + "Contents/MacOS/") for _, _, cmd in rows):
            continue                            # la app sigue abierta: sus ayudantes son suyos
        for pid, ppid, cmd in rows:
            if ppid == "1" and cmd.startswith(bundle):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    n += 1
                except (OSError, ValueError):
                    pass
    return n


def _engine_ok() -> bool:
    """¿El paso 4 puede ir por el motor directo de Retouch4me (sin la ventana de Arams)?"""
    if core.SYSTEM not in ("Darwin", "Windows"):
        return False
    try:
        from . import retouch_engine
        return retouch_engine.usable()[0]
    except Exception:  # noqa: BLE001
        return False


def _running(*names) -> bool:
    import subprocess
    if core.SYSTEM == "Windows":                     # tasklist: «PureRAW» → PureRAW*.exe
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 timeout=15, creationflags=0x08000000).stdout.lower()
        except Exception:  # noqa: BLE001
            return True                              # sin poder mirar: no cortar el paso por error
        return any(n.lower().split()[0] in out for n in names)
    if core.SYSTEM != "Darwin":
        return True
    return any(subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0 for n in names)


def _bytes(paths) -> int:
    """Lo que pesan esos archivos (los que ya no estén cuentan 0)."""
    n = 0
    for p in paths:
        try:
            n += os.path.getsize(p)
        except OSError:
            pass
    return n


def restore_stage_manager():
    """Vuelve a encender el Stage Manager si RawFlow lo apagó (también tras un cierre inesperado)."""
    import subprocess
    if core.SYSTEM == "Darwin" and _SM_FLAG.exists():
        subprocess.run(["defaults", "write", "com.apple.WindowManager", "GloballyEnabled", "-bool", "true"])
        _SM_FLAG.unlink(missing_ok=True)


def _rename(src: Path, dst: Path, tries: int = 6):
    """Renombrar aguantando un bloqueo pasajero de Windows (el antivirus o las miniaturas del Explorador tienen el
    archivo recién escrito abierto un instante): unos reintentos cortos antes de darlo por fallido."""
    for k in range(tries):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if k == tries - 1 or core.SYSTEM != "Windows":
                raise
            time.sleep(0.4)


class Pipeline(QThread):
    step = Signal(int, str)            # índice, "run" | "done" | "skip" | "error"
    status = Signal(str)
    detail = Signal(str)
    progress = Signal(float)           # 0..1 del paso actual; <0 = indeterminado
    finished_all = Signal(str, str)    # carpeta del evento, error ("" si todo bien)
    ejected = Signal(str)              # JSON [{name, disk}] de las memorias soltadas (para el aviso hasta sacarlas)
    check = Signal(int, str, str, str)  # paso (5 = revisión final), "ok" | "warn" | "off", lo comprobado, corto

    def __init__(self, event: Path, files: List[core.RawFile], dxo_path: str, preset: str,
                 eject: List[str], start: int = 0, parent=None, off=(), copy_roots=()):
        super().__init__(parent)
        self.ev_dir, self.files, self.dxo_path, self.preset = event, files, dxo_path, preset
        self.eject, self.start_at = eject, start
        self.copy_roots = set(copy_roots)   # carpetas arrastradas de OTRO disco: se copian (el original se queda)
        self.off = {int(i) for i in off if int(i) in TOGGLEABLE}   # pasos desactivados (doble clic)
        self._stop = False
        self.summary: dict = {}
        self.retried: set = set()
        self.no_learn: set = set()      # pasos cuyo tiempo no es «normal» (por tandas): no se aprenden
        self.work: dict = {}            # paso → (fotos, bytes) que hizo DE VERDAD esta vez (al reanudar, solo lo que
        #                                 faltaba): con eso se aprende su tiempo, no con el total del evento
        self._quitter = None            # hilo que cierra las demás apps al empezar (lo arranca run())
        self._archiver = None           # (compatibilidad) archivado en segundo plano
        self._to_archive: List[str] = []        # proyectos de Imagen ya descargados, para archivar

    def stop(self):
        self._stop = True

    def _join_archiver(self, timeout: float = 90.0):
        """Espera a que termine de archivar el proyecto en Imagen (usa la pantalla): antes de usar la pantalla
        para otra cosa, de cerrar las apps o de limpiar las marcas del evento."""
        t = self._archiver
        if t is not None and t.is_alive():
            t.join(timeout)
        self._archiver = None

    def stopped(self) -> bool:
        return self._stop

    # ------------------------------------------------------------------
    def run(self):
        awake = full_speed()
        import threading
        # cerrar TODAS las demás apps, siempre: al empezar y al reintentar desde cualquier paso (Retouch4me y CineSlide
        # también necesitan el Mac entero); mientras, el paso 1 ya va copiando la memoria
        self._quitter = threading.Thread(target=quit_other_apps, args=(self.detail.emit,), daemon=True)
        self._quitter.start()
        stage_manager(False)            # con el Stage Manager, macOS esconde y recoloca las apps que RawFlow maneja
        try:
            self._run_steps()
        finally:
            restore_stage_manager()
            if core.SYSTEM == "Darwin":
                from .imagen_ui import restore_clipboard
                restore_clipboard()
            if awake:
                awake.terminate()

    def _run_steps(self):
        i = self.start_at
        try:
            movidas = core.migrate_event_layout(self.ev_dir)       # evento de antes de 4.15: carpetas 1-5
            if movidas:
                self.detail.emit("Carpetas en el orden de los pasos: " + ", ".join(movidas))
            import os
            stop_after = int(os.environ.get("RAWFLOW_PARAR_TRAS", "0") or 0)   # solo para pruebas
            steps = (self._move, self._select, self._dxo, self._imagen, self._retouch, self._luminar, self._present)
            for k in range(self.start_at):
                self.step.emit(k, "done")
                self._verify_step(k, "done")       # lo de antes también se comprueba (y se ve)
            rewound = set()                     # pasos a los que ya se volvió una vez en este evento
            i = self.start_at
            while i < len(steps):
                if stop_after and i >= stop_after:
                    raise RuntimeError(f"Prueba: detenido tras el paso {stop_after}.")
                if self._stop:
                    raise RuntimeError("Cancelado.")
                if i >= 1 and self._quitter is not None:   # el cierre de apps tiene que haber acabado
                    self._quitter.join(20)
                    self._quitter = None
                live.clear("mover", "dxo", "imagen", "bridge", "ret", "lum", "pres")   # la subida sigue entre pasos
                self.step.emit(i, "run")
                self.progress.emit(0.0)
                try:
                    state = self._with_retries(i, steps[i])
                except LaneFailed as e:          # faltan fotos de un paso anterior: volver a él SOLO, sin parar
                    j = getattr(e, "step", i)
                    if j >= i or j in rewound or self._stop:
                        raise
                    rewound.add(j)
                    self.detail.emit(f"{e} Vuelvo al paso {j + 1} solo para esas y sigo…")
                    for k in range(j, i + 1):
                        self.step.emit(k, "wait")
                    i = j
                    continue
                self.step.emit(i, state)
                self._verify_step(i, state)
                i += 1
            live.reset()
            self._join_archiver()
            self._audit_imagen()                # nada se entrega sin Imagen: si falta, vuelve al paso 3
            self._final_check()
            self._close_apps()
            self._clean_traces()
            restore_stage_manager()
            self.finished_all.emit(str(self.ev_dir), "")
        except Exception as e:  # noqa: BLE001
            live.reset()
            try:                                # aunque se pare (error o Esc ×3): nada abierto (Imagen se abre
                self._close_apps()              # antes de tiempo durante DxO y se quedaba abierta)
            except Exception:  # noqa: BLE001
                pass
            restore_stage_manager()
            self.step.emit(i, "error")
            self.summary["failed_step"] = getattr(e, "step", i)   # p. ej. faltan fotos de DxO: reintentar desde DxO
            self.finished_all.emit(str(self.ev_dir), str(e))

    # ---- reparación automática
    RETRIES = {STEP_DXO: 3, STEP_IMG: 4, STEP_RET: 4, STEP_LUM: 3, STEP_PRES: 3}   # manejan otras apps: se reintentan

    def _f(self):
        """Carpetas de ESTE evento (los JPEG del paso 3 van a «JPEG sin Imagen» si Imagen está desactivado)."""
        return folders(self.ev_dir, STEP_IMG not in getattr(self, "off", ()))

    def _with_retries(self, i: int, fn):
        tries = self.RETRIES.get(i, 1)
        for k in range(1, tries + 1):
            try:
                return fn() or "done"
            except Exception as e:  # noqa: BLE001
                if self._stop or str(e) == "Cancelado." or isinstance(e, (LaneFailed, SetupMissing)):
                    raise
                msg = str(e)
                fatal = msg.startswith(("No encuentro", "RawFlow necesita permiso", "PureRAW no admite"))
                if fatal and core.SYSTEM == "Windows" and msg.startswith("No encuentro") and not msg.startswith((
                        "No encuentro la app", "No encuentro Adobe Bridge", "No encuentro DxO", "No encuentro Retouch4me",
                        "No encuentro el perfil", "No encuentro el proyecto")):
                    # Windows: «No encuentro …» casi siempre es que el OCR no leyó UNA vez un rótulo (no falta ninguna
                    # app): eso se arregla reintentando. 25-nov-2026 (revisión): todos los de Luminar paraban el evento
                    # entero en el paso 6 sin reintentar ni apartar nada. Solo es fatal si falta un programa, el perfil
                    # o el proyecto de Imagen (reintentar no lo crea y podría cerrar Imagen mientras sube).
                    fatal = False
                if fatal:
                    raise                                  # falta una app o un permiso: reintentar no sirve
                if k == tries:                             # último intento: apartar lo que no sale y SEGUIR
                    state = self._salvage(i, e)
                    if state:
                        return state
                    raise
                self.retried.add(i)
                self.detail.emit(f"Se trabó ({str(e).splitlines()[0][:80]}). Reparando… intento {k + 1} de {tries}")
                self._reset_app(i)

    def _warn(self, text: str):
        """Algo no salió perfecto pero el evento sigue: se avisa en pantalla, en el registro y al final."""
        self.summary.setdefault("avisos", []).append(text)
        self.detail.emit("⚠ " + text)

    def _salvage(self, i: int, err: Exception):
        """El paso falló en todos sus intentos. En vez de parar el evento entero: lo que sí se hizo sigue adelante,
        lo que no se pudo se aparta (queda dicho al final) y el evento termina. None = no hay nada que salvar."""
        import shutil
        raw, dngs, img, final = self._f()
        why = str(err).splitlines()[0][:100]
        # todas las del evento: sueltas en «1 Original» o ya en «Fotos buenas» (contando solo las sueltas, tras la
        # Selección IA salía 0 y nunca se apartaba nada: una sola foto que fallara paraba el evento entero)
        n_all = len(core.good_raws(self.ev_dir))

        def few(k: int) -> bool:
            """¿Son fotos sueltas? (si fallan muchas es que algo no funciona: mejor parar y reintentar ese paso).
            Como mucho 5 o el 10 % del evento, y SIEMPRE menos de la mitad (en un evento de 4 fotos, si fallan las 4
            no se entregan 4 sin retocar: se para)."""
            return 0 < k <= max(5, round(0.1 * n_all)) and 2 * k < max(n_all, 1)
        try:
            if i == STEP_DXO and self.dxo_path:                # DxO: RAW que no revela (p. ej. archivo dañado)
                pend = core.pending_raws(self.ev_dir, exts=core.dxo_extensions(self.dxo_path))
                if not few(len(pend)) or not core.processed_stems(dngs):
                    return None                                # no salió NINGÚN DNG: DxO no funciona → parar
                names = [Path(p).name for p in pend]
                core.apartar(self.ev_dir, [Path(p).stem for p in pend], "DxO no pudo revelarla")
                self._warn(f"{len(pend)} foto(s) que DxO no pudo revelar se quedan en «{RAW_DIR}» "
                           f"({', '.join(names[:5])}): el resto sigue.")
                return "done"
            if i == STEP_IMG:                                  # Imagen/Bridge: DNG que no se exporta
                if self._tanda_dirs(dngs) or not core.jpg_stems(img):
                    return None                                # Imagen no editó / tandas a medias: reintentar
                pend = self._pending_dngs(dngs, img)
                if not few(len(pend)):
                    return None
                core.apartar(self.ev_dir, [core.dng_base(q) for q in pend], "Imagen/Bridge no la exportó")
                self._warn(f"{len(pend)} foto(s) que Bridge no pudo exportar se quedan como DNG en «{DNG_DIR}» "
                           f"({', '.join(q.name for q in pend[:3])}): el resto sigue.")
                return "done"
            if i == STEP_RET:                                  # Retouch4me: se entrega sin retocar
                src = sorted(p for p in img.glob("*.jp*g") if not p.name.startswith("."))
                if not src:
                    return None
                final.mkdir(parents=True, exist_ok=True)
                hechas = {p.stem.lower() for p in final.glob("*.jp*g")}
                falta = [p for p in src if p.stem.lower() not in hechas]
                if falta and not few(len(falta)):
                    return None                                # Retouch4me no va con casi ninguna: parar aquí
                if not falta and all(p.stem.isdigit() for p in final.glob("*.jp*g")):
                    return None
                for p in falta:
                    shutil.copy2(p, final / p.name)
                self._number_finals(final)
                if falta:
                    self._warn(f"{len(falta)} foto(s) van sin retocar, tal como salieron de Imagen "
                               f"({', '.join(p.stem for p in falta[:5])}): Retouch4me falló con ellas ({why}).")
                self.summary["final"] = len(src)
                return "done"
            if i == STEP_LUM:                                  # Luminar: las finales de Retouch4me siguen valiendo
                self._warn(f"Luminar no pudo aplicar su preajuste ({why}). Las fotos finales de Retouch4me están "
                           f"completas en «{FINAL_DIR}»; se puede rehacer con «Reintentar desde el paso {STEP_LUM + 1}».")
                self.summary["sin_luminar"] = True
                return "done"
            if i == STEP_PRES:                                 # CineSlide: las fotos ya están; la presentación no
                self._warn(f"La presentación no se pudo crear ({why}). Las fotos finales están completas; "
                           f"se puede rehacer con «Reintentar desde el paso {STEP_PRES + 1}».")
                self.summary["sin_presentacion"] = True
                return "done"
        except Exception as e2:  # noqa: BLE001
            self.detail.emit(f"(no pude apartar lo que falló: {e2})")
        return None

    def _reset_app(self, i: int):
        """Cierra la app que se trabó para empezar el paso limpio."""
        import subprocess
        if core.SYSTEM == "Windows":
            if i == STEP_DXO:                        # DxO en Windows: cerrar PureRAW para reintentar
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "PureRAW*"], capture_output=True, timeout=30,
                                   creationflags=0x08000000)
                except (OSError, subprocess.SubprocessError):
                    pass
                time.sleep(2)
            elif i == STEP_IMG:                      # Imagen/Bridge en Windows: cerrarlos para reintentar limpio (un
                try:                                 # Bridge que sigue exportando tenía abiertos los JPEG a rehacer)
                    from . import imagen_win
                    if not imagen_win.no_apps():     # (en pruebas no se toca ninguna app de verdad)
                        imagen_win.close_app(imagen_win.BRIDGE_EXE)
                        imagen_win.close_app(imagen_win.EXE)
                except Exception:  # noqa: BLE001
                    pass
            elif i == STEP_LUM:                      # Luminar: cerrarlo para reintentar limpio
                try:
                    from . import imagen_win, luminar_win
                    if not imagen_win.no_apps():
                        imagen_win.close_app(luminar_win.EXE, wait=20)
                except Exception:  # noqa: BLE001
                    pass
            return
        if core.SYSTEM != "Darwin":
            return
        apps = {STEP_DXO: ["PureRAWv6", "DxO PureRAW 6"], STEP_IMG: ["ImagenAI"] + [Path(p).stem for p in Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app")],
                STEP_RET: ["Retouch4me Arams"], STEP_PRES: []}.get(i, [])
        for n in apps:
            subprocess.run(["osascript", "-e", f'if application "{n}" is running then tell application "{n}" to quit'],
                           capture_output=True, timeout=20)
        for _ in range(20):
            if not any(subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0 for n in apps):
                break
            time.sleep(0.5)
        for n in apps:
            subprocess.run(["pkill", "-x", n], capture_output=True)
        time.sleep(2)
        for n in ("Problem Reporter", "ReportCrash"):       # «se cerró inesperadamente» tapa la app al reintentar
            subprocess.run(["pkill", "-x", n], capture_output=True)

    # ---- 6 Luminar
    def _luminar(self):
        """Luminar Neo (Windows): catálogo nuevo del evento, «CINEMA AJUSTE» a todas las fotos finales y exportación
        JPEG al 100 % → <evento>/5 Fotos finales Luminar (mismos nombres: 1.jpg, 2.jpg…). Ver luminar_win."""
        if STEP_LUM in self.off:
            self.detail.emit("Luminar desactivado: las fotos finales son las de Retouch4me.")
            return "off"
        _, _, _, final = self._f()
        src = sorted(p for p in final.glob("*.jp*g") if not p.name.startswith(".")) if final.is_dir() else []
        if not src:
            self.detail.emit("No hay fotos finales para Luminar: paso omitido.")
            return "skip"
        from . import luminar_win
        ok, why = luminar_win.available()
        if not ok:
            if not not_installed(why):
                raise SetupMissing(f"Luminar: {why}. Créalo con ese nombre (o desactiva Luminar con doble clic en "
                                   "su paso) y pulsa Reintentar.")
            self.detail.emit(f"Luminar no disponible en este equipo ({why}): paso omitido.")
            return "skip"
        out = self.ev_dir / LUM_DIR
        hechas = {p.stem.lower() for p in out.glob("*.jp*g")} if out.is_dir() else set()
        if src and all(p.stem.lower() in hechas for p in src) and min(
                (q.stat().st_mtime for q in out.glob("*.jp*g")), default=0) >= max(p.stat().st_mtime for p in src) - 60:
            self.detail.emit("Las fotos de Luminar ya estaban hechas.")
            return "done"
        self.work[STEP_LUM] = (len(src), _bytes(src))
        self.progress.emit(-1)
        self.status.emit(f"Luminar aplicando «{luminar_win.PRESET}» a {len(src):,} fotos…")
        n = luminar_win.run(self.ev_dir, final, out, self.detail.emit, should_stop=self.stopped,
                            progress=self.progress.emit)
        if n < len(src):
            raise RuntimeError(f"Luminar exportó {n} de {len(src)} fotos.")
        self.summary["luminar"] = n
        return "done"

    # ---- 7
    def _present(self):
        """CineSlide (tema 4) con las fotos finales (las de Luminar si ese paso está activo) →
        <evento>/6 Presentación de fotos/Presentación.mp4."""
        import subprocess
        if STEP_PRES in self.off:
            self.detail.emit("CineSlide desactivado: este evento va sin presentación.")
            return "off"
        final = deliver_dir(self.ev_dir, self.off)
        out = self.ev_dir / PRES_DIR
        from . import programas                       # Mac: ~/Library/Application Support/CineSlide;
        py = programas.cineslide_python()             # Windows: C:\Program Files (x86)\CineSlide (su propio Python)
        if not py:
            self.detail.emit("CineSlide no está instalado: paso omitido.")
            return "skip"
        cs_dir, cs_data = programas.cineslide_dir(), programas.cineslide_data()
        if presentation_current(out, final):
            self.detail.emit("La presentación ya estaba hecha.")
            return "done"
        if out.is_dir() and any(out.glob("*.mp4")):
            self.detail.emit("Las fotos finales cambiaron después del video: rehago la presentación.")
        if not any(final.glob("*.jp*g")):
            if core.SYSTEM != "Darwin":             # Windows: sin Imagen ni Retouch4me no hay fotos finales; no es un fallo
                self.detail.emit("Sin fotos finales en este equipo (Imagen AI y Retouch4me se omitieron): sin presentación.")
                self.summary["sin_presentacion"] = True
                return "skip"
            raise RuntimeError("No hay fotos finales para la presentación.")
        self.status.emit("CineSlide creando la presentación (tema 4 · Montón de fotos)…")
        tmp = self.ev_dir / (".presentacion-" + str(int(time.time())))
        import sys
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        script = next((p for p in (base / "rawflow/cineslide_run.py", Path(__file__).with_name("cineslide_run.py"))
                       if p.exists()), None)
        if not script:
            raise RuntimeError("Falta cineslide_run.py dentro de RawFlow.")
        cs_data.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CINESLIDE_DIR=str(cs_dir), PYTHONIOENCODING="utf-8")
        p = subprocess.Popen([str(py), str(script), str(final), str(tmp), "Presentación", PRES_THEME],
                             cwd=str(cs_data), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        made, last = [], ""
        t_pres = time.monotonic()
        import shutil
        import threading
        seen = [time.monotonic()]

        def kill_all():                               # CineSlide y lo que lanzó (el codificador de video)
            try:
                import psutil
                _kill_tree(psutil.Process(p.pid))
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except OSError:
                    pass

        def watchdog():                               # colgado sin avanzar 5 min → se corta y se reintenta
            while p.poll() is None:
                # 25-nov-2026 (revisión): «Parar» solo se veía cuando CineSlide escribía una línea; callado (al
                # codificar) «Parando…» podía tardar hasta 5 min. Ahora se mira cada 2 s.
                if self._stop or time.monotonic() - seen[0] > 300:
                    kill_all()
                    return
                time.sleep(2)
        threading.Thread(target=watchdog, daemon=True).start()
        for line in p.stdout:
            seen[0] = time.monotonic()
            if self._stop:
                kill_all()
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError("Cancelado.")
            line = line.strip()
            if line.startswith("PROG "):
                parts = line.split(" ", 2)
                try:
                    f = float(parts[1])
                    self.progress.emit(f)
                    el = time.monotonic() - t_pres
                    if f >= 0.04 and el > 5:                  # lo que falta según el avance real de CineSlide
                        live.set_eta(STEP_PRES, el / f - el)
                except ValueError:
                    pass
                txt = parts[2] if len(parts) > 2 else ""
                if txt and txt != last:
                    self.detail.emit(f"CineSlide: {txt}")
                    live.set("pres", txt)
                    last = txt
            elif line.startswith("OK "):
                made.append(Path(line[3:]))
            elif line:
                last_err = line
        rc = p.wait()
        if self._stop:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("Cancelado.")
        if rc != 0 or not made:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("CineSlide no pudo crear el video" + (f": {locals().get('last_err', '')}" or "."))
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.mp4"):                 # los videos de antes (de otras fotos finales) sobran
            old.unlink(missing_ok=True)
        for v in made:
            v.replace(out / pres_name(v, len(made)))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        self.summary["presentation"] = str(out)
        self.detail.emit(f"✓ Presentación en «{PRES_DIR}»")

    # ---- candado de Imagen (JUTIAPA, 22-nov-2026: 120 fotos llegaron a Retouch4me y a las finales sin Imagen)
    @staticmethod
    def _imagen_app() -> bool:
        """¿Imagen va por su app (sin clave API)? Entonces las ediciones viajan DENTRO de los DNG y valen los candados
        foto a foto. Mac: app + OCR (imagen_ui). Windows: lo mismo con imagen_win."""
        from . import imagen
        if core.SYSTEM == "Windows":
            from . import imagen_win
            return imagen_win.available() and not imagen.get_api_key()
        return core.SYSTEM == "Darwin" and Path("/Applications/ImagenAI.app").exists() and not imagen.get_api_key()

    @staticmethod
    def _drop_jpgs_without_imagen(img: Path) -> List[str]:
        """Un JPEG de «Imagen AI» sin las ediciones de Imagen no vale (se exportó sin ellas): se borra para que el
        paso 3 lo rehaga con Imagen. Devuelve sus nombres."""
        if not img.is_dir():
            return []
        bad = core.without_imagen(p for p in img.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                                  and not p.name.startswith("."))
        for p in bad:
            p.unlink(missing_ok=True)
        return sorted(p.name for p in bad)

    @staticmethod
    def _final_ok(f: Path, srcs: dict, all_src_ok: bool) -> bool:
        """¿Esta foto final viene de un JPEG con Imagen y está al día? Lleva la marca de Imagen y su JPEG no se
        rehízo después; si Retouch4me no le dejó los metadatos: su JPEG lleva Imagen y ella es más nueva."""
        stem = f.stem.lower() if not f.stem.isdigit() else core.source_stem(f)
        s = srcs.get(stem) if stem else None
        if core.has_imagen_edits(f):                        # lleva Imagen: vale, salvo que su JPEG se rehiciera después
            return s is None or s.stat().st_mtime <= f.stat().st_mtime + 60
        if s is not None:                                   # sin la marca: solo si se hizo DE ese JPEG con Imagen
            return core.has_imagen_edits(s) and f.stat().st_mtime >= s.stat().st_mtime - 1
        return not stem and all_src_ok                      # sin metadatos: vale si TODOS los JPEG llevan Imagen

    def _drop_finals_to_redo(self, final: Path, img: Path) -> int:
        """Fotos finales que se rehacen: sin Imagen o de un JPEG que se rehízo después. Si ya estaban numeradas
        (1.jpg, 2.jpg…), todas vuelven antes a llamarse como su foto (lo dicen sus metadatos) para que Retouch4me
        haga SOLO esas; al acabar se numeran otra vez igual que antes (mismo orden: fecha del RAW)."""
        if not final.is_dir():
            return 0
        fotos = [p for p in final.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                 and not p.name.startswith(".")]
        srcs = {p.stem.lower(): p for p in img.glob("*.jp*g") if not p.name.startswith(".")}
        all_src_ok = not core.without_imagen(srcs.values())
        malas = [p for p in fotos if not self._final_ok(p, srcs, all_src_ok)]
        if not malas:
            return 0
        moved = self._unnumber_finals(final, srcs)
        malas_set = {moved.get(p, p) for p in malas}
        for p in malas_set:
            p.unlink(missing_ok=True)
        self._warn(f"{len(malas_set)} foto(s) finales estaban sin las ediciones de Imagen (o de un JPEG que se rehízo): "
                   "se rehacen y conservan su número.")
        return len(malas_set)

    @staticmethod
    def _unnumber_finals(final: Path, srcs: dict) -> dict:
        """Las finales numeradas (1.jpg, 2.jpg…) vuelven a llamarse como su foto (lo dicen sus metadatos), para hacer
        solo las que faltan; al acabar se numeran igual que antes. Devuelve {antes: ahora}."""
        nombre = {}
        for p in final.iterdir():
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg") and p.stem.isdigit():
                st = core.source_stem(p)
                if not st or st not in srcs:
                    raise RuntimeError(f"No sé de qué foto es la final «{p.name}»: no puedo rehacer solo las que "
                                       "faltan.")
                nombre[p] = srcs[st].stem + p.suffix.lower()
        tmp, moved = [], {}
        for p, n in nombre.items():                         # en dos pasos para no pisar nombres
            t = p.with_name(f".rf-desnumerar-{p.name}")
            p.rename(t)
            tmp.append((p, t, n))
        for p, t, n in tmp:
            dst = final / n
            if dst.exists():
                dst.unlink()                                # (no debería: dos finales de la misma foto)
            t.rename(dst)
            moved[p] = dst
        return moved

    def _bridge_only(self):
        """Imagen AI DESACTIVADO (doble clic en su paso): Bridge exporta los DNG tal cual, sin pedir nada a Imagen,
        a «3 JPEG sin Imagen» (nunca a «3 Imagen AI»: así nadie confunde unas fotos con otras)."""
        _, dngs, img, _ = self._f()
        if core.SYSTEM != "Darwin":                       # Windows: Bridge con imagen_win (mismo recorrido, por menú)
            from . import imagen_win
            if core.SYSTEM != "Windows" or not imagen_win.bridge_path():
                self.detail.emit("Imagen AI desactivado y falta Adobe Bridge para exportar los JPEG: paso omitido.")
                return "off"
            pend = self._pending_dngs(dngs, img)
            if not pend:
                self.detail.emit("Imagen AI desactivado: los JPEG sin Imagen ya estaban exportados.")
                return "off"
            self.progress.emit(-1)
            self.status.emit(f"Imagen AI desactivado · Bridge exportando {len(pend):,} JPEG sin Imagen…")
            imagen_win.bridge_only(self.ev_dir, DNG_DIR, self.detail.emit, self.stopped, img, self.progress.emit)
            left = self._pending_dngs(dngs, img)
            if left:
                raise RuntimeError(f"Bridge no exportó {len(left)} foto(s) ({', '.join(q.name for q in left[:3])}).")
            self.summary["sin_imagen"] = len(core.jpg_stems(img))
            return "off"
        for t in self._tanda_dirs(dngs):                  # tandas de un intento con Imagen: sus DNG vuelven
            self._merge_tanda(t)
        pend = self._pending_dngs(dngs, img)
        if not pend:
            self.detail.emit("Imagen AI desactivado: los JPEG sin Imagen ya estaban exportados.")
            quit_apps(["ImagenAI"] + _bridge_names())
            return "off"
        from . import imagen_ui
        self.progress.emit(-1)
        quit_apps(["ImagenAI"])
        self.status.emit(f"Imagen AI desactivado · Bridge exportando {len(pend):,} JPEG sin Imagen…")
        if len(pend) == len(list(dngs.glob("*.dng"))):
            imagen_ui.bridge_only(self.ev_dir, DNG_DIR, self.detail.emit, self.stopped, img, self.progress.emit)
        else:                                             # solo las que faltan
            t = self._new_tanda(dngs, pend)
            imagen_ui.bridge_only(self.ev_dir, f"{DNG_DIR}/{t.name}", self.detail.emit, self.stopped, img,
                                  self.progress.emit)
            self._merge_tanda(t)
        quit_apps(_bridge_names())
        left = self._pending_dngs(dngs, img)
        if left:
            raise RuntimeError(f"Bridge no exportó {len(left)} foto(s) ({', '.join(q.name for q in left[:3])}).")
        self.summary["sin_imagen"] = len(core.jpg_stems(img))
        return "off"

    def _finals_without_retouch(self):
        """Retouch4me DESACTIVADO (doble clic en su paso): las fotos finales son los JPEG del paso 3 tal cual, copiados
        y numerados en «4 Fotos finales» (las que ya estuvieran retocadas de antes se quedan)."""
        import shutil
        _, dngs, img, final = self._f()
        con_imagen = self._imagen_app() and STEP_IMG not in self.off
        if con_imagen:                                    # Imagen activado: el candado es el mismo
            malos = self._drop_jpgs_without_imagen(img)
            if malos:
                raise LaneFailed(f"{len(malos)} foto(s) no llevan las ediciones de Imagen ({', '.join(malos[:3])}): "
                                 "no se entregan sin Imagen.", STEP_IMG)
        falta = self._pending_dngs(dngs, img)
        if falta:
            raise LaneFailed(f"Faltan {len(falta)} fotos por pasar por el paso de Imagen.", STEP_IMG)
        src = sorted(p for p in img.glob("*.jp*g") if not p.name.startswith(".")) if img.is_dir() else []
        if not src:
            raise RuntimeError("No hay JPEG del paso 3 para hacer las fotos finales.")
        if con_imagen:
            self._drop_finals_to_redo(final, img)
        final.mkdir(parents=True, exist_ok=True)
        fotos = [p for p in final.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                 and not p.name.startswith(".")]
        if len(fotos) >= len(src):
            self._number_finals(final)
            self.detail.emit("Retouch4me desactivado: las fotos finales ya estaban.")
            return "off"
        if any(p.stem.isdigit() for p in fotos):
            self._unnumber_finals(final, {p.stem.lower(): p for p in src})
        hechas = {p.stem.lower() for p in final.glob("*.jp*g")}
        nuevas = [p for p in src if p.stem.lower() not in hechas]
        quit_apps(["Retouch4me Arams"])
        self.status.emit(f"Retouch4me desactivado · {len(nuevas):,} fotos finales sin retocar…")
        for k, p in enumerate(nuevas, 1):
            shutil.copy2(p, final / p.name)
            self.progress.emit(k / len(nuevas))
        self._number_finals(final)
        self.summary["final"] = len(src)
        self.summary["sin_retocar"] = len(nuevas)
        self.detail.emit(f"Retouch4me desactivado: {len(nuevas):,} fotos finales tal como salieron del paso 3.")
        return "off"

    def _emit_check(self, i: int, state: str, text: str, short: str = ""):
        live.check(i, state, text)
        self.summary.setdefault("checks", {})[i] = (state, text)
        self.check.emit(i, state, text, short or text)

    def _verify_step(self, i: int, state: str):
        """Comprobación INDEPENDIENTE de lo que dejó cada paso en su carpeta (no de lo que dice el programa): se ve en
        el panel COMPROBADO de la pantalla de proceso y en las pastillas de la ventana («✓ 597 con Imagen»)."""
        try:
            raw, dngs, img, final = self._f()
            aparte = set(core.apartadas(self.ev_dir))
            goods = core.good_raws(self.ev_dir)
            bad_dir = raw / core.BAD_SUBFOLDER
            n_bad = len([q for q in bad_dir.iterdir() if q.suffix.lower() in core.RAW_EXTENSIONS]) if bad_dir.is_dir() else 0
            n_dng = len([q for q in dngs.glob("*.dng") if core.dng_base(q) not in aparte]) if dngs.is_dir() else 0
            jpgs = [q for q in img.glob("*.jp*g") if not q.name.startswith(".")] if img.is_dir() else []
            fin = [q for q in final.glob("*.jp*g") if not q.name.startswith(".")] if final.is_dir() else []
            if i == STEP_MOVE:
                n = len(goods) + n_bad
                res = ("ok", f"{n:,} fotos en «{RAW_DIR}» · copiadas y verificadas", f"{n:,} fotos")
            elif i == STEP_SEL:
                if STEP_SEL in self.off:
                    res = ("off", f"desactivada · pasan las {len(goods):,}", "pasan todas")
                elif state == "skip":
                    res = ("off", "omitida en este equipo", "omitida")
                elif state == "off":                    # este equipo no puede analizar las fotos: no «✓ N buenas»
                    res = ("off", f"no disponible aquí · pasan las {len(goods):,}", "no disponible")
                else:
                    res = ("ok", f"{len(goods):,} buenas · {n_bad:,} malas (en «{RAW_DIR}/{core.BAD_SUBFOLDER}», "
                                 "con su motivo)", f"{len(goods):,} buenas · {n_bad:,} malas")
            elif i == STEP_DXO:
                n, want = len(core.processed_stems(dngs)), max(0, len(goods) - len(aparte))
                res = ("ok" if n >= want else "warn",
                       f"{n:,} de {want:,} DNG completos (solo las buenas)" + (f" · {len(aparte)} apartada(s)" if aparte else ""),
                       f"{n:,} DNG" if n >= want else f"{n:,} de {want:,} DNG")
            elif i == STEP_IMG:
                if STEP_IMG in self.off:
                    res = ("off", f"desactivado · {len(jpgs):,} JPEG sin Imagen", "sin Imagen")
                elif state == "skip" and not jpgs:
                    res = ("off", "omitido en este equipo", "omitido")
                elif self._imagen_app():
                    sin = len(core.without_imagen(jpgs))
                    ok = not sin and len(jpgs) >= n_dng
                    res = ("ok" if ok else "warn", f"{len(jpgs) - sin:,} de {n_dng:,} con Imagen · comprobadas una a una",
                           f"{len(jpgs) - sin:,} con Imagen" if ok else f"{len(jpgs) - sin:,} de {n_dng:,} con Imagen")
                else:
                    res = ("ok" if len(jpgs) >= n_dng else "warn", f"{len(jpgs):,} de {n_dng:,} JPEG de Imagen",
                           f"{len(jpgs):,} JPEG")
            elif i == STEP_RET:
                if STEP_RET in self.off:
                    res = ("off", f"desactivado · {len(fin):,} fotos sin retocar", "sin retocar")
                elif state == "skip" and not fin:
                    res = ("off", "omitido en este equipo", "omitido")
                else:
                    ok = len(fin) >= len(jpgs)
                    res = ("ok" if ok else "warn", f"{len(fin):,} retocadas · numeradas del 1 al {len(fin):,}",
                           f"{len(fin):,} retocadas")
            elif i == STEP_LUM:
                lum = self.ev_dir / LUM_DIR
                n_lum = len([q for q in lum.glob("*.jp*g") if not q.name.startswith(".")]) if lum.is_dir() else 0
                if STEP_LUM in self.off:
                    res = ("off", "desactivado · se entregan las de Retouch4me", "sin Luminar")
                elif state == "skip" and not n_lum:
                    res = ("off", "omitido en este equipo", "omitido")
                else:
                    ok = n_lum >= len(fin)
                    res = ("ok" if ok else "warn", f"{n_lum:,} de {len(fin):,} con Luminar en «{LUM_DIR}»",
                           f"{n_lum:,} con Luminar" if ok else f"{n_lum:,} de {len(fin):,}")
            elif i == STEP_PRES:
                out = self.ev_dir / PRES_DIR
                vids = sorted(out.glob("*.mp4")) if out.is_dir() else []
                final = deliver_dir(self.ev_dir, self.off)
                if STEP_PRES in self.off:
                    res = ("off", "desactivado · sin presentación", "sin video")
                elif vids and presentation_current(out, final):
                    mb = sum(v.stat().st_size for v in vids) / 1e6
                    res = ("ok", f"horizontal y vertical · {mb:,.0f} MB · con las {len(fin):,} finales · 3 s por foto",
                           "videos al día")
                elif state == "skip" and not fin:
                    res = ("off", "omitido en este equipo", "omitido")
                else:
                    res = ("warn", "sin presentación: «Reintentar» desde CineSlide", "sin video")
            else:
                return
            self._emit_check(i, *res)
        except Exception:  # noqa: BLE001
            pass

    def _final_check(self):
        """Revisión final (después del candado): cuántas fotos finales y todas con Imagen."""
        _, _, _, final = self._f()
        n = len([q for q in final.glob("*.jp*g") if not q.name.startswith(".")]) if final.is_dir() else 0
        if self._imagen_app() and STEP_IMG not in self.off:
            self._emit_check(STEP_FINAL, "ok", f"{n:,} de {n:,} fotos finales vienen de Imagen", f"{n:,} con Imagen")
        else:
            self._emit_check(STEP_FINAL, "ok", f"{n:,} fotos finales", f"{n:,} finales")

    def _audit_imagen(self):
        """Última comprobación antes de dar el evento por terminado: TODAS las fotos finales vienen de Imagen.
        Si alguna no, el evento no se entrega así: vuelve al paso 3 («Reintentar» las rehace)."""
        if not self._imagen_app() or STEP_IMG in self.off:   # Imagen desactivado a propósito: no se exige
            return
        _, _, img, final = self._f()
        if not final.is_dir():
            return
        srcs = {p.stem.lower(): p for p in img.glob("*.jp*g") if not p.name.startswith(".")}
        all_src_ok = not core.without_imagen(srcs.values())
        malas = [p for p in final.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                 and not p.name.startswith(".") and not self._final_ok(p, srcs, all_src_ok)]
        if malas:
            raise LaneFailed(f"{len(malas)} foto(s) finales sin las ediciones de Imagen "
                             f"({', '.join(p.name for p in malas[:5])}): el evento no se entrega así. "
                             "«Reintentar» las rehace con Imagen.", STEP_IMG)

    # ---- al terminar
    def _clean_traces(self):
        """Evento terminado bien: no deja rastro de trabajo (marcas internas, carpetas temporales, copias
        de Retouch4me), para que el siguiente evento empiece limpio."""
        import shutil
        dngs = self.ev_dir / DNG_DIR
        aparte = core.apartadas(self.ev_dir)                # quedan dichas en el resumen final, no en la carpeta
        if aparte:
            self.summary["apartadas"] = aparte
        for name in (".rawflow_imagen_ok", ".rawflow_imagen_proyecto", ".rawflow_tandas", core.APARTADAS):
            (dngs / name).unlink(missing_ok=True)
        for p in self.ev_dir.glob(".presentacion-*"):
            shutil.rmtree(p, ignore_errors=True)
        for t in self._tanda_dirs(dngs):                # tandas ya devueltas a «2 DNGs» (vacías)
            try:
                t.rmdir()
            except OSError:
                pass
        for p in list(self.ev_dir.rglob("*.part")) + list(self.ev_dir.rglob(".rf-*")):
            p.unlink(missing_ok=True)
        shutil.rmtree(Path.home() / "Library/Application Support/RawFlow/retouch", ignore_errors=True)
        try:
            core._batch_dir().exists() and [p.unlink() for p in core._batch_dir().glob("DxO-BatchFile-RawFlow-*")]
        except Exception:  # noqa: BLE001
            pass

    def _close_apps(self):
        """Cierra las apps que usó el proceso (DxO, Imagen, Bridge, Retouch4me)."""
        if core.SYSTEM != "Darwin":
            return
        import subprocess
        self.detail.emit("Cerrando DxO, Imagen, Bridge y Retouch4me…")
        names = ["ImagenAI", "Retouch4me Arams"]
        names += [Path(p).stem for p in Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app")]
        names += [Path(self.dxo_path).stem] if self.dxo_path else []
        names += [Path(p).stem for pat in ("DxO PureRAW*.app", "*/DxO PureRAW*.app")
                  for p in Path("/Applications").glob(pat)]
        names = list(dict.fromkeys(names))      # sin repetidos, en orden
        for n in names:
            try:
                subprocess.run(["osascript", "-e",
                                f'if application "{n}" is running then tell application "{n}" to quit'],
                               capture_output=True, timeout=20)
            except subprocess.TimeoutExpired:
                pass
        for _ in range(30):                     # esperar a que cierren solas (hasta 15 s)
            if not any(subprocess.run(["pgrep", "-x", n], capture_output=True).returncode == 0 for n in names):
                break
            time.sleep(0.5)
        else:
            for n in names:                     # la que siga colgada, se fuerza
                subprocess.run(["pkill", "-x", n], capture_output=True)
            time.sleep(1)
        kill_orphan_helpers()

    # ---- 1
    def _move(self):
        raw, *_ = self._f()
        if not self.files:
            if any(raw.glob("*")):
                if self.start_at == STEP_MOVE:    # se retoma un evento que se paró MOVIENDO, ya sin la memoria delante
                    n = len(core.good_raws(self.ev_dir))
                    self._warn(f"El paso 1 se había quedado a medias: el evento sigue con las {n:,} fotos que ya "
                               f"estaban en «{RAW_DIR}». Si quedaron fotos en la memoria, no están incluidas.")
                return "skip"
            raise RuntimeError("No hay fotos RAW para mover.")
        total = sum(f.size for f in self.files) or 1
        t0 = time.monotonic()
        self.status.emit(f"Moviendo {len(self.files):,} fotos a «{RAW_DIR}»…")

        def prog(done: int, name: str):
            el = max(0.5, time.monotonic() - t0)
            speed = done / el
            eta = int((total - done) / speed) if speed else 0
            self.progress.emit(done / total)
            self.detail.emit(f"{name}  ·  {core.human_size(speed)}/s  ·  quedan {eta // 60}:{eta % 60:02d}")
            live.set("mover", f"{core.human_size(speed)}/s  ·  {core.human_size(done)} de {core.human_size(total)}")
            live.set_eta(STEP_MOVE, eta)

        copiar = [f for f in self.files if f.drive in getattr(self, "copy_roots", ())]
        mover = [f for f in self.files if f.drive not in getattr(self, "copy_roots", ())]

        def pasada(grupo, move, cb):
            return core.move_files_parallel(grupo, raw, cb, self.stopped, move=move) if grupo else core.CopyStats()
        st = pasada(mover, True, prog)
        if st.failed and not self._stop:          # un fallo de lectura suelto: otra pasada solo con esas
            again = [f for f in mover if os.path.exists(f.path)]
            if again:
                self.detail.emit(f"{st.failed} fotos no se leyeron bien: lo intento otra vez…")
                time.sleep(2)
                st2 = pasada(again, True, None)
                st.copied += st2.copied
                st.skipped += st2.skipped
                st.failed, st.errors = st2.failed, st2.errors
        if copiar and not self._stop:             # carpeta arrastrada de OTRO disco: se copia, el original se queda
            base = sum(f.size for f in mover)
            st_c = pasada(copiar, False, lambda d, n: prog(base + d, n))
            if st_c.failed and not self._stop:    # otra pasada: las ya copiadas se saltan (mismo nombre y tamaño)
                self.detail.emit(f"{st_c.failed} fotos no se leyeron bien: lo intento otra vez…")
                time.sleep(2)
                st_c = pasada(copiar, False, None)
            st.copied += st_c.copied
            st.skipped += st_c.skipped
            st.failed += st_c.failed
            st.errors += st_c.errors
        self.summary["moved"] = st.copied + st.skipped
        if self._stop:
            raise RuntimeError("Cancelado.")
        if st.failed:
            if not st.copied + st.skipped:        # no se pudo leer nada: la memoria no responde
                raise RuntimeError(f"{st.failed} fotos no se pudieron copiar:\n" + "\n".join(st.errors[:8]))
            names = [e.split(":")[0] for e in st.errors if "no se pudo borrar" not in e][:5]
            self._warn(f"{st.failed} foto(s) no se pudieron leer de la memoria y siguen en ella "
                       f"({', '.join(names)}): el resto del evento sigue sin ellas.")
        sin_borrar = [e for e in st.errors if "no se pudo borrar" in e]
        if sin_borrar:                            # copiadas, pero la memoria no deja borrar (seguro puesto, protegidas…)
            self._warn(f"{len(sin_borrar)} foto(s) se copiaron bien pero siguen también en la memoria (no deja "
                       "borrarlas: ¿tiene el seguro puesto o están protegidas?). Bórralas tú antes de reutilizarla.")
        out, bad, items = [], [], []
        for mp in self.eject:                     # soltar la memoria en cuanto se sacaron las fotos
            self.detail.emit(f"Expulsando {Path(mp).name}…")
            if core.SYSTEM == "Darwin":           # desmontar sin expulsar: así se ve cuándo la retiras
                disk = core.release_drive(mp)
                ok = disk is not None
            else:
                dev = core.win_device_id(mp) if core.SYSTEM == "Windows" else ""
                disk, ok = "", core.eject_drive(mp)
                if ok and dev:                    # el aviso se queda hasta que la quitas de verdad: mientras el
                    present, _problem = core.win_devnode(dev)     # dispositivo siga en la lista (26-nov-2026: antes
                    # solo con el problema 47; si no, el aviso se iba a los 9 s)
                    disk = ("win:" + dev) if present else "wait:"
                elif ok and core.SYSTEM == "Windows":
                    disk = "wait:"                # no se sabe cuál es: el aviso se queda hasta que lo cierres tú
            (out if ok else bad).append(Path(mp).name)
            if ok:
                items.append({"name": Path(mp).name, "disk": disk or ""})
        msg = f"{st.copied + st.skipped:,} fotos en «{RAW_DIR}»"
        if out:
            msg += f" · {', '.join(out)} expulsada{'s' if len(out) > 1 else ''}: ya puedes sacarla"
        if bad:
            msg += f" · no pude expulsar {', '.join(bad)} (expúlsala a mano)"
        self.detail.emit(msg)
        if items:
            import json
            self.ejected.emit(json.dumps(items, ensure_ascii=False))

    # ---- 2
    def _select(self):
        """2 · Selección IA (Mac: IA de Apple; Windows: OpenCV, ver seleccion.engine): dentro de «1 Original», las tomas
        malas (y las repetidas) a «Fotos malas», con su motivo (nunca se borran), y las buenas a «Fotos buenas». DxO,
        Imagen, Bridge y Retouch4me SOLO trabajan con las buenas. Si la IA falla, NO para el evento: pasan todas."""
        from . import seleccion
        raw, dngs, *_ = self._f()
        good = raw / core.GOOD_SUBFOLDER
        top = sorted(q for q in raw.iterdir() if q.is_file() and q.suffix.lower() in core.RAW_EXTENSIONS
                     and not q.name.startswith(".")) if raw.is_dir() else []
        motor = seleccion.engine()                     # «mac», «win» o «» (este equipo no puede analizar)
        off = STEP_SEL in self.off or not motor
        if not top:
            if good.is_dir():
                self.detail.emit("Las fotos ya estaban separadas en buenas y malas.")
            return "off" if off else ("done" if good.is_dir() else "skip")
        if off:                                        # desactivada: no se analiza; si ya había separación, a buenas
            if good.is_dir():
                seleccion.move_rest_to_good(raw)
            self.detail.emit("Selección IA desactivada: pasan todas las fotos.")
            return "off"
        done = core.processed_stems(dngs)
        dec = {}
        try:
            rf = None                                  # Windows: sin ayudante (seleccion_win, dentro de RawFlow)
            if motor == "mac":
                from .imagen_ui import _rfui
                rf = _rfui()
            n_new = len([q for q in top if q.stem.lower() not in done])
            self.status.emit(f"Selección IA: separando {n_new:,} fotos en buenas y malas…")
            self.progress.emit(0.0)
            live.set("ia", f"analizando {n_new:,} fotos" + (" con la IA del Mac" if motor == "mac" else ""))
            dec = seleccion.run(raw, rf, done, self.detail.emit, self.stopped, self.progress.emit)
        except Exception as e:  # noqa: BLE001
            if self._stop or str(e) == "Cancelado.":
                raise RuntimeError("Cancelado.")
            self.detail.emit(f"(Selección IA no pudo analizar: {str(e).splitlines()[0][:80]}; pasan todas)")
        finally:
            live.set("ia", "")
        n_good = seleccion.move_rest_to_good(raw)          # las que quedan (las buenas) → «Fotos buenas»
        self.summary["descartadas"] = self.summary.get("descartadas", 0) + len(dec)
        self.summary.setdefault("malas", {}).update(dec)            # {foto: motivo}, para el resumen final
        self.detail.emit(f"Selección IA: {n_good:,} buenas · {len(dec):,} malas"
                         + (f" ({seleccion.summary(dec)}) en «{RAW_DIR}/{core.BAD_SUBFOLDER}», con su motivo; "
                            "ninguna se borra" if dec else ""))
        return "done"

    # ---- 3
    def _dxo(self):
        _, dngs, *_ = self._f()
        if not self.dxo_path or not core.is_pureraw(self.dxo_path):
            raise RuntimeError("No encuentro DxO PureRAW (necesario para crear los DNG).")
        preset = core.ensure_preset_first(self.dxo_path, self.preset, destination=dngs)
        ok_ext = core.dxo_extensions(self.dxo_path)
        # (también los de «Fotos buenas»: tras la Selección IA ya no quedan sueltos en «1 Original»)
        other = sorted(p.name for p in core.good_raws(self.ev_dir) if p.suffix.lower() not in ok_ext)
        if other:
            exts = ", ".join(sorted({Path(n).suffix.upper() for n in other}))
            self.summary["dxo_unsupported"] = other
            self.detail.emit(f"{len(other)} RAW ({exts}) no son compatibles con PureRAW: se quedan en «Original».")
        core.kill_pureraw_batches()       # un lote anterior de RawFlow vivo haría que PureRAW ignore el nuevo (y
        #                                   seguiría escribiendo DNG mientras aquí se mira cuáles faltan)
        n_rec = core.collect_dxo_dngs(self.ev_dir)    # Windows: DNG que PureRAW dejó junto a los RAW → «2 DNGs»
        if n_rec:
            self.detail.emit(f"{n_rec} DNG que DxO dejó junto a los originales: pasan a «{DNG_DIR}».")
        n_cut = core.remove_partial_dxo_dngs(self.ev_dir)   # Windows: DNG a medias junto a su RAW (DxO se cortó)
        broken = core.remove_broken_dngs(dngs)        # DNG cortados de un intento anterior: se rehacen
        if n_cut:
            self.detail.emit(f"{n_cut} DNG a medias que DxO dejó junto a los originales: se vuelven a hacer.")
        if broken:
            self.detail.emit(f"{len(broken)} DNG cortados de un intento anterior: se vuelven a hacer.")
        raws = core.pending_raws(self.ev_dir, exts=ok_ext)
        if not raws and other and not core.processed_stems(dngs):
            raise RuntimeError(f"PureRAW no admite estos RAW ({exts}). Quedan en «Original».")
        if not raws:
            self.detail.emit("Los DNG ya estaban hechos.")
            return "done"
        stems = {Path(r).stem.lower() for r in raws}
        self.work[STEP_DXO] = (len(stems), _bytes(raws))   # al reanudar, DxO solo hace las que faltan
        tsize = self._tanda_plan(dngs, raws)          # evento grande: Imagen sube por tandas mientras DxO trabaja
        self.status.emit(f"DxO procesando {len(stems):,} fotos con {preset}…")
        batch = core.start_pureraw_batch(self.dxo_path, raws)
        if not tsize:                     # (por tandas, Imagen se abre al crear el primer proyecto)
            extra = [] if (_engine_ok() or STEP_RET in self.off) else _bridge_and_arams()[1:]
            prelaunch(*([] if STEP_IMG in self.off else ["ImagenAI"]), *extra)
        self._dxo_clock = {}
        try:
            if tsize:
                self._dxo_with_uploads(dngs, stems, tsize)
            else:
                self._wait_dxo(dngs, stems, len(stems), self.stopped, emit=True)
        finally:
            self._dxo_clock = {}
            self._quit_dxo(batch)         # DxO terminado (o cortado): se cierra por completo, sin ventanas
        core.collect_dxo_dngs(self.ev_dir)
        broken = core.remove_broken_dngs(dngs)          # comprobación final: ningún DNG a medias
        if broken:
            raise RuntimeError(f"{len(broken)} DNG salieron cortados ({', '.join(broken[:3])}): se repiten.")
        self.summary["dngs"] = len(stems)

    def _quit_dxo(self, batch):
        """Cierra PureRAW del todo: el lote de RawFlow (y su ventanita «¡Listo!») y la app si quedó abierta."""
        core.close_pureraw_helper(batch)
        core.kill_pureraw_batches()
        names = list(dict.fromkeys(([Path(self.dxo_path).stem] if self.dxo_path else []) + ["DxO PureRAW 6"]))
        quit_apps(names, wait=8)

    def _wait_dxo(self, dngs: Path, stems: set, want: int, stop, emit: bool = True) -> set:
        """Espera a que DxO tenga `want` DNG completos de `stems`. Si DxO no avanza, mira si está parado en su aviso
        «Módulos ópticos DxO» (equipo nuevo) y lo acepta. Vigilante: DxO cerrado, o sin un DNG nuevo mucho más de lo
        normal → RuntimeError (se reintenta solo con los que faltan)."""
        clk = self.__dict__.setdefault("_dxo_clock", {})     # el ritmo se mide en todo el lote (no por espera)
        t0 = clk.setdefault("t0", time.monotonic())
        changed = time.monotonic()
        last, total = -1, len(stems)
        dlg_t = hide_t = 0.0
        while True:
            if stop():
                raise RuntimeError("Cancelado.")
            if core.SYSTEM == "Windows" and time.monotonic() - hide_t > 0.4:
                hide_t = time.monotonic()                    # en silencio (26-nov-2026): DxO trabaja por lotes y en
                try:                                         # Windows nunca se mira su ventana: minimizada, sin foco
                    from . import winscreen
                    winscreen.minimize_quiet(DXO_EXES)
                except Exception:  # noqa: BLE001
                    pass
            core.collect_dxo_dngs(self.ev_dir)               # Windows: PureRAW los deja junto al RAW
            done_set = stems & core.processed_stems(dngs)
            done = len(done_set)
            now = time.monotonic()
            if done != last:
                last, changed = done, now
                if done >= 1 and "first" not in clk:
                    clk["first"] = now
                first_t = clk.get("first", t0)
                if emit and clk.get("shown") != done:        # al empezar otra espera no se repite la misma línea
                    clk["shown"] = done
                    eta = ""
                    if done:
                        # (varios DNG vistos a la vez la primera vez: sin esto salía «0.0 s por foto» y «faltan 0»)
                        per = ((now - first_t) / (done - 1) if done >= 2 and now - first_t > 1
                               else (now - t0) / done)
                        r = int(per * (total - done))
                        eta = f"  ·  {per:.1f} s por foto" + (f"  ·  quedan {r // 60}:{r % 60:02d}" if r else "")
                        live.set("dxo", f"{done:,} de {total:,} DNG  ·  {per:.1f} s/foto  ·  DeepPRIME XD3")
                        if done >= 2:
                            live.set_eta(STEP_DXO, r)
                    self.detail.emit(f"{done:,} / {total:,} DNG{eta}")
                    self.progress.emit(min(1.0, done / max(1, total)))
            if done >= want and core.files_settled(
                    [q for q in dngs.glob("*.dng") if core.dng_base(q) in done_set], 0.5):
                return done_set
            if now - changed > 12 and now - dlg_t > 6:      # ¿parado en un aviso de PureRAW?
                dlg_t = now
                gear = self._dxo_dialog(stop)
                if gear:
                    self.detail.emit(f"DxO pidió el módulo óptico de «{gear}»: lo descarga y sigue…")
                    changed = now                            # la descarga lleva su tiempo: el vigilante empieza de nuevo
            per = (changed - t0) / done if done else 0
            if now - changed > (max(180, per * 6) if done else 5 * 60):
                if not done:
                    raise RuntimeError(f"DxO no empezó (0 de {total}): mira si DxO PureRAW pide algo en su ventana.")
                raise RuntimeError(f"DxO dejó de avanzar ({done} de {total}): reintento con los que faltan.")
            if now - t0 > 25 and not _running("PureRAWv6", "DxO PureRAW 6"):
                raise RuntimeError(f"DxO se cerró ({done} de {total} hechos): reintento con los que faltan.")
            time.sleep(0.5)

    def _dxo_dialog(self, stop):
        """Acepta el aviso «Módulos ópticos DxO» si está en pantalla (ver imagen_ui.dxo_modules_dialog)."""
        if core.SYSTEM != "Darwin":
            return None
        try:
            from .imagen_ui import dxo_modules_dialog
            return dxo_modules_dialog(lambda m: None, stop)
        except Exception:  # noqa: BLE001
            return None

    # ---- por tandas: Imagen sube mientras DxO trabaja (nunca dos trabajos de GPU a la vez)
    def _tanda_plan(self, dngs: Path, raws: List[str]) -> int:
        """Fotos por tanda si este evento sube a Imagen por tandas mientras DxO trabaja; 0 = todo seguido. Solo en un
        evento nuevo (sin DNG, sin JPEG de Imagen, sin tandas a medias), en Mac con Imagen por su app."""
        from . import imagen
        img = dngs.parent / IMAGEN_DIR
        if STEP_IMG in self.off:                       # Imagen desactivado: nada que subir
            return 0
        if core.SYSTEM != "Darwin" or not Path("/Applications/ImagenAI.app").exists() or imagen.get_api_key():
            return 0
        if core.processed_stems(dngs) or core.jpg_stems(img) or self._tanda_dirs(dngs):
            return 0
        if any((dngs / m).exists() for m in (".rawflow_imagen_ok", ".rawflow_imagen_proyecto")):
            return 0
        return core.tanda_size(len(raws))

    def _dxo_with_uploads(self, dngs: Path, stems: set, tsize: int):
        """DxO procesa todo; cada vez que tiene `tsize` fotos nuevas listas, van a una «Tanda N» y su proyecto de
        Imagen empieza a SUBIR (red, no GPU) mientras DxO sigue. Cuando DxO termina, la última tanda."""
        assigned: set = set()
        total = len(stems)
        self.no_learn |= {STEP_DXO, STEP_IMG}         # tiempos de DxO e Imagen distintos de un evento seguido
        while True:
            want = min(total, len(assigned) + tsize)
            done = self._wait_dxo(dngs, stems, want, self.stopped, emit=True)
            last = len(done) >= total
            new = done - assigned
            if new and (len(new) >= tsize or last):
                files = sorted((q for q in dngs.glob("*.dng") if core.dng_base(q) in new),
                               key=lambda q: q.stat().st_mtime)
                if not last:
                    files = files[:tsize]             # tandas parejas; lo demás va en la siguiente
                t = self._new_tanda(dngs, files)
                assigned |= {core.dng_base(q) for q in files}
                self.summary["tandas"] = self.summary.get("tandas", 0) + 1
                self._start_upload(t, len(assigned), total)
            if last:
                return

    def _start_upload(self, t: Path, ready: int, total: int):
        """Crea el proyecto de Imagen de la tanda y lo deja subiendo; si algo falla, esa tanda se hace en el paso 3."""
        if os.environ.get("RAWFLOW_PRUEBA_SIN_IMAGEN") == "1":         # solo pruebas: no gastar en Imagen
            (t / ".rawflow_imagen_ok").write_text("prueba", encoding="utf-8")
            return
        from . import imagen_ui
        try:
            name = imagen_ui.start_project(self.ev_dir, f"{DNG_DIR}/{t.name}", self.detail.emit, self.stopped,
                                           project=f"{t.name} {self.ev_dir.name}")
            self.detail.emit(f"Imagen: «{name}» sube mientras DxO sigue ({ready} de {total} DNG listos).")
        except Exception as e:  # noqa: BLE001
            if self._stop or str(e) == "Cancelado.":
                raise RuntimeError("Cancelado.")
            self.detail.emit(f"   (Imagen: la {t.name.lower()} se sube al terminar DxO: {str(e).splitlines()[0][:70]})")

    @staticmethod
    def _tanda_dirs(dngs: Path) -> List[Path]:
        if not dngs.is_dir():
            return []
        return sorted((d for d in dngs.iterdir() if d.is_dir() and d.name.startswith(core.TANDA_PREFIX)),
                      key=lambda d: int(d.name[len(core.TANDA_PREFIX):]) if d.name[len(core.TANDA_PREFIX):].isdigit()
                      else 999)

    @staticmethod
    def _pending_dngs(dngs: Path, img: "Path | None" = None) -> List[Path]:
        """DNG de «2 DNGs» que aún no tienen su JPEG de Imagen/Bridge en «Imagen AI» (sin las apartadas)."""
        have = core.jpg_stems(img if img is not None else dngs.parent / IMAGEN_DIR)
        skip = core.apartadas(dngs.parent)
        return sorted(q for q in dngs.glob("*.dng") if core.dng_base(q) not in have and core.dng_base(q) not in skip)

    @staticmethod
    def _new_tanda(dngs: Path, files: List[Path]) -> Path:
        """«2 DNGs/Tanda N» con enlaces a esos DNG (instantáneo, no ocupa disco). Imagen trabaja ahí.
        N nunca se repite en el evento (ni en los reintentos): cada tanda es un proyecto distinto en Imagen."""
        import shutil
        cnt = dngs / ".rawflow_tandas"
        try:
            i = int(cnt.read_text(encoding="utf-8").strip() or 0) + 1
        except (OSError, ValueError):
            i = 1
        while (dngs / f"{core.TANDA_PREFIX}{i}").exists():
            i += 1
        cnt.write_text(str(i), encoding="utf-8")
        t = dngs / f"{core.TANDA_PREFIX}{i}"
        t.mkdir()
        for f in files:
            try:
                os.link(f, t / f.name)
            except OSError:
                shutil.copy2(f, t / f.name)
        return t

    @staticmethod
    def _merge_tanda(t: Path):
        """Devuelve a «2 DNGs» los DNG de la tanda YA editados por Imagen (y cualquier .xmp que dejara) y borra la
        carpeta de la tanda. Si Imagen editó el archivo en su sitio, el enlace ya es el mismo archivo."""
        dngs = t.parent
        for p in sorted(t.iterdir()):
            if p.is_dir():
                continue
            if p.name.startswith("."):                       # marcas de RawFlow, .DS_Store…
                p.unlink(missing_ok=True)
                continue
            os.replace(p, dngs / p.name)
            if p.exists():                                   # era el mismo archivo (enlace): rename no hace nada
                p.unlink()
        try:
            t.rmdir()
        except OSError:
            pass

    def _edit_tanda(self, t: Path):
        """Imagen de una tanda: retoma su proyecto (o lo crea), espera la edición y la descarga. Sin Bridge."""
        from . import imagen_ui
        if os.environ.get("RAWFLOW_PRUEBA_SIN_IMAGEN") == "1":
            (t / ".rawflow_imagen_ok").write_text("prueba", encoding="utf-8")
        imagen_ui.edit_only(self.ev_dir, f"{DNG_DIR}/{t.name}", self.detail.emit, self.stopped,
                            project=f"{t.name} {self.ev_dir.name}", progress=self.progress.emit)
        pf = t / ".rawflow_imagen_proyecto"
        if pf.exists():
            name = pf.read_text(encoding="utf-8").strip()
            if name and name not in self._to_archive:
                self._to_archive.append(name)

    def _finish_imagen(self, dngs: Path):
        """Imagen y Bridge terminaron: archivar sus proyectos en Imagen (la lista queda limpia) y CERRAR los dos."""
        from . import imagen_ui
        pf = dngs / ".rawflow_imagen_proyecto"
        if pf.exists():
            name = pf.read_text(encoding="utf-8").strip()
            if name and name not in self._to_archive:
                self._to_archive.append(name)
        for name in list(self._to_archive):
            if self._stop:
                break
            try:
                if imagen_ui.archive_project(imagen_ui.Screen(self.detail.emit, self.stopped), name):
                    self._to_archive.remove(name)
                    if pf.exists() and pf.read_text(encoding="utf-8").strip() == name:
                        pf.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                self.detail.emit(f"   (no pude archivar «{name}» en Imagen: {e})")
        quit_apps(["ImagenAI"] + _bridge_names())

    # ---- 4
    def _imagen(self):
        if STEP_IMG in self.off:
            return self._bridge_only()
        _, dngs, img, _ = self._f()
        from . import imagen, imagen_ui
        if core.SYSTEM != "Darwin" and not imagen.get_api_key():
            from . import imagen_win                        # Windows sin clave API: la app ImagenAI + Bridge
            if core.SYSTEM != "Windows" or not imagen_win.available() or not imagen_win.bridge_path():
                self.detail.emit("Imagen AI: falta la app ImagenAI con Adobe Bridge, o una clave API (botón «Imagen AI»): "
                                 "paso omitido.")
                return "skip"
            hechos = [p for p in dngs.glob("*") if p.suffix.lower() == ".dng"]
            if hechos and not self._pending_dngs(dngs, img) and not core.without_imagen(hechos):
                self.detail.emit("Imagen ya estaba hecho.")
                return "done"
            self.progress.emit(-1)
            self.status.emit("Imagen AI editando…")
            from PySide6.QtCore import QSettings
            tipo = str(QSettings("RawFlow", "RawFlow").value("imagen_type", imagen.DEFAULT_TYPE))
            imagen_win.run(self.ev_dir, DNG_DIR, self.detail.emit, should_stop=self.stopped, photo_type=tipo,
                           out_dir=img, progress=self.progress.emit,
                           charge_ok=os.environ.get("RAWFLOW_PRUEBA_SIN_COBRO") != "1")   # solo pruebas: no descargar
            malos = self._drop_jpgs_without_imagen(img)       # CANDADO: lo exportado lleva Imagen, foto a foto
            if malos:
                raise RuntimeError(f"Bridge dejó {len(malos)} JPEG sin las ediciones de Imagen "
                                   f"({', '.join(malos[:3])}): se rehacen.")
            self.summary["imagen"] = len(core.jpg_stems(img))
            left = self._pending_dngs(dngs, img)
            if left:
                raise RuntimeError(f"Bridge no exportó {len(left)} foto(s) ({', '.join(q.name for q in left[:3])}).")
            return "done"
        n_dng = len([p for p in dngs.glob("*") if p.suffix.lower() == ".dng"])
        mac_ui = core.SYSTEM == "Darwin" and not imagen.get_api_key() and Path("/Applications/ImagenAI.app").exists()
        if not mac_ui:                                       # Windows / clave API: como siempre
            if img.is_dir() and len(list(img.glob("*.jp*g"))) >= n_dng > 0:
                self.detail.emit("Imagen ya estaba hecho.")
                return "done"
            self.progress.emit(-1)
            self.status.emit("Imagen AI editando…")
            if imagen.get_api_key():
                from PySide6.QtCore import QSettings      # perfil y tipo elegidos en «Configurar Imagen»
                st = QSettings("RawFlow", "RawFlow")
                imagen.edit_event(self.ev_dir, DNG_DIR, self.detail.emit, self.progress.emit,
                                  profile_name=str(st.value("imagen_profile", imagen.DEFAULT_PROFILE)),
                                  photo_type=str(st.value("imagen_type", imagen.DEFAULT_TYPE)),
                                  should_stop=self.stopped, out_dir=img)
            else:
                raise RuntimeError("No encuentro la app ImagenAI ni una clave API de Imagen.")
            self.summary["imagen"] = len(list(img.glob("*.jp*g")))
            return "done"
        if self.dxo_path:
            falta = core.pending_raws(self.ev_dir, exts=core.dxo_extensions(self.dxo_path))
            if falta:                                         # DxO quedó a medias: Imagen no puede ir sin esas fotos
                raise LaneFailed(f"Faltan {len(falta)} fotos por pasar por DxO.", STEP_DXO)
        malos = self._drop_jpgs_without_imagen(img)       # CANDADO: exportados sin Imagen → se rehacen
        if malos:
            self._warn(f"{len(malos)} JPEG estaban sin las ediciones de Imagen ({', '.join(malos[:3])}"
                       f"{'…' if len(malos) > 3 else ''}): se rehacen con Imagen.")
        sin_ed = set(core.without_imagen(dngs.glob("*.dng")))
        if sin_ed:                                        # la marca «Imagen ya editó» no vale si falta alguna
            (dngs / ".rawflow_imagen_ok").unlink(missing_ok=True)
        tandas, pend = self._tanda_dirs(dngs), self._pending_dngs(dngs, img)
        if not tandas and not pend and n_dng > 0:
            self.detail.emit("Imagen ya estaba hecho.")
            quit_apps(["ImagenAI"] + _bridge_names())
            return "done"
        self.progress.emit(-1)
        some_done = bool(core.jpg_stems(img))
        if tandas or (pend and some_done):
            in_tandas = {core.dng_base(q) for t in tandas for q in t.glob("*.dng")}
            loose = [q for q in pend if core.dng_base(q) not in in_tandas]
            if loose:                                         # lo que falta y no está en ninguna tanda: una más
                hechos = [q for q in loose if q not in sin_ed]    # ya con Imagen (comprobado): solo Bridge
                for grupo in (hechos, [q for q in loose if q in sin_ed]):
                    if not grupo:
                        continue
                    t = self._new_tanda(dngs, grupo)
                    if grupo is hechos:
                        (t / ".rawflow_imagen_ok").write_text("ok", encoding="utf-8")
                    tandas = tandas + [t]
            for t in tandas:                                  # ediciones de cada tanda (ya subidas mientras DxO)
                self.status.emit(f"Imagen AI: ediciones de la {t.name.lower()}…")
                self._edit_tanda(t)
            self.status.emit("Bridge exportando los JPEG…")
            if some_done:                                     # reintento: Bridge solo con lo que falta
                for t in tandas:
                    imagen_ui.run(self.ev_dir, f"{DNG_DIR}/{t.name}", self.detail.emit, should_stop=self.stopped,
                                  out_dir=img, progress=self.progress.emit, archive=False)
                    self._merge_tanda(t)
            else:                                             # evento nuevo por tandas: TODO a «2 DNGs» y UN Bridge
                for t in tandas:
                    self._merge_tanda(t)
                sin = core.without_imagen(dngs.glob("*.dng"))
                if sin:                                       # nunca a Bridge sin Imagen: esas, a su propia tanda
                    t = self._new_tanda(dngs, sin)
                    raise RuntimeError(f"{len(sin)} DNG sin las ediciones de Imagen: se piden en la {t.name.lower()}.")
                (dngs / ".rawflow_imagen_ok").write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
                imagen_ui.run(self.ev_dir, DNG_DIR, self.detail.emit, should_stop=self.stopped, out_dir=img,
                              progress=self.progress.emit, archive=False)
        else:
            self.status.emit("Imagen AI editando…")
            imagen_ui.run(self.ev_dir, DNG_DIR, self.detail.emit, should_stop=self.stopped, out_dir=img,
                          progress=self.progress.emit, archive=False)
        self._finish_imagen(dngs)                             # archivar y cerrar Imagen y Bridge
        malos = self._drop_jpgs_without_imagen(img)           # CANDADO: lo exportado lleva Imagen, foto a foto
        if malos:
            raise RuntimeError(f"Bridge dejó {len(malos)} JPEG sin las ediciones de Imagen ({', '.join(malos[:3])}): "
                               "se rehacen.")
        self.summary["imagen"] = len(core.jpg_stems(img))
        left = self._pending_dngs(dngs, img)                  # ¿Bridge se dejó alguna? → reintento solo con esas
        if left:
            raise RuntimeError(f"Bridge no exportó {len(left)} foto(s) ({', '.join(q.name for q in left[:3])}).")

    # ---- 5
    def _retouch(self):
        _, dngs, img, final = self._f()
        from . import arams_ui
        no_mac = core.SYSTEM != "Darwin"
        if no_mac and STEP_RET not in self.off:   # Windows: solo por el motor directo (sin la ventana de Arams)
            from . import retouch_engine as _re
            ok_w, why_w = _re.usable() if core.SYSTEM == "Windows" else (False, "solo en Mac y Windows")
            for _ in range(4):                    # la base de datos de Arams puede estar ocupada un momento (Arams
                if ok_w or not str(why_w).startswith("no pude leer"):   # guardando): eso no es que falte algo
                    break
                time.sleep(3)
                ok_w, why_w = _re.usable()
            if not ok_w:
                if not not_installed(why_w):
                    raise SetupMissing(f"Retouch4me: {why_w}. Arréglalo en Arams (o desactiva Retouch4me con doble "
                                       "clic en su paso) y pulsa Reintentar.")
                self.detail.emit(f"Retouch4me automático no disponible en este equipo ({why_w}): paso omitido.")
                return "skip"
            if not core.jpg_stems(img):           # Imagen se omitió (sin clave API): no hay nada que retocar
                self.detail.emit("No hay JPEG de Imagen AI que retocar (ese paso se omitió): paso omitido.")
                return "skip"
        if STEP_RET in self.off:
            return self._finals_without_retouch()
        if not no_mac and not arams_ui.APP_PATH.exists():
            raise RuntimeError("No encuentro Retouch4me Arams.")
        if self._imagen_app() and STEP_IMG not in self.off:   # CANDADO: Retouch4me JAMÁS con una foto sin Imagen
            malos = self._drop_jpgs_without_imagen(img)
            if malos:
                raise LaneFailed(f"{len(malos)} foto(s) no llevan las ediciones de Imagen ({', '.join(malos[:3])}"
                                 f"{'…' if len(malos) > 3 else ''}): no se retocan sin Imagen.", STEP_IMG)
        falta = self._pending_dngs(dngs, img)
        if falta:                                 # DNG sin su JPEG de Imagen/Bridge: eso no lo arregla Retouch4me
            raise LaneFailed(f"Faltan {len(falta)} fotos por pasar por Imagen/Bridge.", STEP_IMG)
        src = sorted(img.glob("*.jp*g"))
        if self._imagen_app() and STEP_IMG not in self.off:
            self._drop_finals_to_redo(final, img)             # finales sin Imagen / de un JPEG ya rehecho
        if final.is_dir() and src and len([p for p in final.glob("*.jp*g") if not p.name.startswith(".")]) >= len(src):
            self._number_finals(final)
            self.detail.emit("Las fotos finales ya estaban retocadas.")
            return "done"
        if final.is_dir() and any(p.stem.isdigit() for p in final.glob("*.jp*g")):
            try:                                      # ya numeradas y falta alguna: que se llamen como su foto,
                self._unnumber_finals(final, {p.stem.lower(): p for p in src})   # así SOLO se hace la que falta
            except RuntimeError:
                # No se sabe de qué foto es cada final numerada: se rehacen TODAS. Las numeradas de antes se quitan:
                # si se quedaran, al numerar otra vez se juntarían con las nuevas y cada foto saldría DOS veces.
                viejas = [p for p in final.glob("*.jp*g") if p.stem.isdigit()]
                for p in viejas:
                    p.unlink(missing_ok=True)
                self.detail.emit(f"No se sabe de qué foto es cada una de las {len(viejas)} finales numeradas: "
                                 "se rehacen todas (sin duplicar ninguna).")
        hechas = {p.stem.lower() for p in final.glob("*.jp*g")} if final.is_dir() else set()
        todo = [p for p in src if p.stem.lower() not in hechas]
        self.work[STEP_RET] = (len(todo), _bytes(todo))   # el motor se salta las que ya estaban retocadas
        self.progress.emit(-1)
        # nada más abierto: Retouch4me tiene la GPU (y la memoria) para él solo
        quit_apps(["ImagenAI", "Retouch4me Arams"] + _bridge_names(), wait=8)
        from . import retouch_engine
        ok, why = retouch_engine.usable()
        n = None
        if ok:                                    # motor directo: mismo resultado que Arams, sin ventana ni clics
            self.status.emit(f"Retouch4me retocando {len(src):,} fotos con CINEMA…")
            try:
                n = retouch_engine.retouch(img, final, self.detail.emit, should_stop=self.stopped,
                                           progress=self.progress.emit, expected_total=len(src))
            except retouch_engine.EngineUnavailable as e:
                why = str(e)
        if n is None and no_mac:                  # en Windows no hay camino con la ventana de Arams
            raise RuntimeError(f"Retouch4me no pudo retocar con su motor ({why}).")
        if n is None:                             # algo que el motor directo no cubre: Arams con su ventana
            self.detail.emit(f"Retouch4me con la ventana de Arams ({why}).")
            self.status.emit(f"Retouch4me retocando {len(src):,} fotos con CINEMA (≈1 min por foto)…")
            try:
                n = arams_ui.retouch(img, self.ev_dir.name, self.detail.emit, should_stop=self.stopped, dest=final,
                                     progress=self.progress.emit)
            finally:
                quit_apps(["Retouch4me Arams"])   # sin ventanas abiertas al terminar el paso
        self.summary["final"] = n
        self._number_finals(final)

    def _number_finals(self, final: Path):
        """Renombra las fotos finales 1.jpg, 2.jpg, 3.jpg… en el orden en que se tomaron
        (fecha del RAW original; si no se encuentra, por nombre)."""
        import re
        fotos = [p for p in final.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                 and not p.name.startswith(".")]
        if not fotos or all(re.fullmatch(r"\d+", p.stem) for p in fotos):
            return
        cuando = {}
        for r in core.good_raws(self.ev_dir):          # sueltos en «1 Original» o en «Fotos buenas»
            try:
                cuando[r.stem.lower()] = r.stat().st_mtime
            except OSError:
                pass
        nat = lambda t: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", t.lower())]  # noqa: E731
        fotos.sort(key=lambda p: (cuando.get(p.stem.lower(), float("inf")), nat(p.stem)))
        tmp, hechos = [], []
        try:
            for i, p in enumerate(fotos, 1):          # en dos pasos para no pisar nombres que ya fueran números
                t = p.with_name(f".rf-{i}{p.suffix.lower()}")
                _rename(p, t)
                hechos.append((p, t))
                tmp.append(t)
        except OSError:                               # una no se deja renombrar: todas vuelven a su nombre (si no,
            for p, t in reversed(hechos):             # quedaban ocultas como «.rf-N» y se retocaban otra vez)
                try:
                    t.rename(p)
                except OSError:
                    pass
            raise
        for i, t in enumerate(tmp, 1):
            _rename(t, final / f"{i}{t.suffix}")
        self.detail.emit(f"Fotos finales numeradas del 1 al {len(tmp)}.")

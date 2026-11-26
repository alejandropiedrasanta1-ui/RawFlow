"""Paso 4 sin ventanas: retoque con el MOTOR de Retouch4me (el que Arams usa por dentro) y tu preajuste de Arams.

Por qué es más rápido: de lo que tarda Arams, casi todo es el motor (con CINEMA, Stray Hairs ~45 s por foto
en la GPU). La ventana de Arams solo añadía tiempo y memoria: abrirla, crear el proyecto, importar, generar
vistas previas, exportar con OCR y clics, y borrar el proyecto. Aquí se llama al MISMO motor con la MISMA
cadena de plugins que arma Arams con el preajuste (leído de su base de datos) → fotos idénticas píxel a píxel.

El motor corre en un proceso aparte (el propio ejecutable de Arams en modo Node, ver r4m_worker.js) que carga
los plugins UNA vez y retoca las fotos una tras otra. Cada foto terminada aparece entera en «Fotos finales»
(se escribe con nombre temporal y se renombra): si algo se corta, no se pierde ninguna y al reintentar solo se
hacen las que faltan.

Si el preajuste usa algo que este camino no reproduce (máscaras, caras, plugins que no están aquí…) o un plugin
no carga, se lanza EngineUnavailable y el paso usa Arams con la pantalla, como antes (arams_ui).
"""
from __future__ import annotations

import collections
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import live
from .core import STEP_RET

if sys.platform == "win32":             # Windows: mismo Arams (1.10), mismo motor y misma base de datos; cambian las rutas
    from . import programas as _prog
    BIN = _prog.arams() or Path(r"C:\Program Files\Retouch4me Arams\Retouch4me Arams.exe")
    APP = BIN.parent
    _ROAM = Path(os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming"))
    DB = _ROAM / "retouch4me-arams/data/db.sqlite"
    WORK = _ROAM / "RawFlow"
    PLUGINS_DEFAULT = r"C:\Program Files\Common Files\Adobe\Plug-Ins\CC\Filters"
    PLUGIN_EXT = ".8bf"                 # en Windows cada plugin es un archivo .8bf (en Mac, una carpeta .plugin)
else:
    APP = Path("/Applications/Retouch4me Arams.app")
    BIN = APP / "Contents/MacOS/Retouch4me Arams"
    DB = Path.home() / "Library/Application Support/retouch4me-arams/data/db.sqlite"
    WORK = Path.home() / "Library/Application Support/RawFlow"
    PLUGINS_DEFAULT = "/Library/Application Support/Adobe/Plug-Ins/CC"
    PLUGIN_EXT = ".plugin"
PRESET = "CINEMA"
TMP_PREFIX = ".rf-r4m-"

# Orden en que Arams encadena los plugins (su lista «Om»), y los que este camino sabe preparar igual que Arams.
ORDER = ["Clean Backdrop", "Heal", "Dust", "Fabric", "Stray Hairs", "Eye Vessels", "Eye Brilliance",
         "White Teeth", "Mattifier", "Dodge Burn", "Skin Tone", "Portrait Volumes", "Tone Mapping",
         "Color Correction", "Lab Image Details"]
MASKS = ("mask_skin", "mask_not_skin", "mask_eyes", "mask_not_eyes")
SCHEMA: Dict[str, Tuple[str, ...]] = {          # parámetros de cada plugin (sin las máscaras, que van siempre)
    "Heal": ("Scale", "Alpha1", "Alpha2"),
    "Stray Hairs": ("Alpha1",),
    "Mattifier": ("Scale", "Alpha1"),
    "Dodge Burn": ("Scale", "Alpha1", "Alpha2"),
    "Portrait Volumes": ("Scale", "Alpha1"),
}
# plugins que, instalados, cambian las máscaras que Arams manda (no se reproducen aquí → Arams con ventana)
MASK_PLUGINS = ("Skin Mask", "Eye Brilliance")


class EngineUnavailable(Exception):
    """Este camino no sirve para este preajuste / este Mac: usar Arams con la pantalla."""


# ------------------------------------------------------------------ preajuste
def _db_rows(sql: str, args=()) -> list:
    if not DB.exists():
        raise EngineUnavailable("no encuentro la base de datos de Arams")
    last = None
    for _ in range(5):                                 # Arams puede estar escribiendo justo ahora
        try:
            uri = (DB.as_uri() if sys.platform == "win32" else f"file:{DB}") + "?mode=ro"   # C:\… con espacios
            con = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                return con.execute(sql, args).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            last = e
            time.sleep(0.4)
    raise EngineUnavailable(f"no pude leer la base de datos de Arams ({last})")


def read_settings() -> Dict[str, str]:
    return {k: v for k, v in _db_rows("SELECT id, value FROM Settings")}


def read_preset(name: str = PRESET) -> dict:
    rows = _db_rows("SELECT value FROM Presets WHERE path = ?", (name,))
    if not rows:
        raise EngineUnavailable(f"no encuentro el preajuste «{name}» en Arams")
    try:
        return json.loads(rows[0][0])
    except ValueError as e:
        raise EngineUnavailable(f"el preajuste «{name}» no se puede leer ({e})")


def _plugin_paths(folder: Path) -> Dict[str, Path]:
    """{«Heal»: …/Retouch4me Heal.plugin, …} como los encuentra Arams. Las versiones demo no cuentan: Arams
    no las aplica al exportar, así que con una demo el paso va por Arams con ventana (hace lo mismo que él)."""
    found: Dict[str, Path] = {}
    if not folder.is_dir():
        return found
    bundles = []
    for root, dirs, files in os.walk(folder):          # como Arams: no se entra dentro de los .plugin
        if PLUGIN_EXT == ".plugin":
            for d in list(dirs):
                if d.lower().endswith(".plugin"):
                    bundles.append(Path(root) / d)
                    dirs.remove(d)
        else:
            bundles += [Path(root) / f for f in files if f.lower().endswith(PLUGIN_EXT)]
    for p in sorted(bundles):
        n = p.name[:-len(PLUGIN_EXT)].lower()
        if not n.startswith("retouch4me ") or n.endswith("automation") or " demo" in n:
            continue
        key = n[len("retouch4me "):]
        for pid in ORDER + list(MASK_PLUGINS):
            if pid.lower() == key and pid not in found:
                found[pid] = p
    return found


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_plugins(preset: dict, settings: Dict[str, str], installed: Dict[str, Path]) -> List[dict]:
    """La cadena de plugins EXACTA que Arams manda a su motor al exportar en local con este preajuste
    (funciones Fr/wT/vT/zI/kI de Arams 1.10, caso sin caras ni ajustes por género)."""
    if not isinstance(preset, dict) or not isinstance(preset.get("preset"), list):
        raise EngineUnavailable("el preajuste tiene un formato que no conozco")
    if preset.get("overrides"):
        raise EngineUnavailable("el preajuste tiene ajustes por persona/género")
    if preset.get("customLut"):
        raise EngineUnavailable("el preajuste lleva una LUT")
    if str(settings.get("faceAutoSearchEnabled", "0")) not in ("0", "false", ""):
        raise EngineUnavailable("Arams tiene activada la búsqueda de caras")
    if str(settings.get("useCloudProcessing", "false")) in ("1", "true"):
        raise EngineUnavailable("Arams está en modo de procesado en la nube")
    if any(m in installed for m in MASK_PLUGINS):
        raise EngineUnavailable("hay plugins de máscara instalados (Skin Mask / Eye Brilliance)")
    expert = str(settings.get("enableExpertSettings", "0")) in ("1", "true")
    out = []
    for entry in preset["preset"]:
        pid, params = entry.get("id"), entry.get("params") or {}
        if not entry.get("selected"):
            continue
        if pid not in SCHEMA:
            raise EngineUnavailable(f"el preajuste usa «{pid}»")
        if pid not in installed:
            raise EngineUnavailable(f"no está instalado el plugin «{pid}»")
        if any(params.get(m) for m in MASKS):
            raise EngineUnavailable(f"«{pid}» usa máscaras")
        a1 = _num(params.get("Alpha1"), 100.0)
        a2 = _num(params.get("Alpha2"), 0.0)
        if not (a1 > 0 or (pid == "Heal" and a2 > 0)):
            continue                                       # intensidad 0: Arams no lo manda
        up: dict = {}
        for k in SCHEMA[pid]:
            if k == "Scale":
                up["Scale"] = int(_num(params.get("Scale"), 0)) if expert else 0      # 0 = Auto
            elif k == "Alpha1":
                up["Alpha1"] = a1 * 0.01 if a1 else a1
            elif k == "Alpha2":
                up["Alpha2"] = a2 * 0.01 if a2 else a2
        up.update({m: False for m in MASKS})
        if pid == "Heal":
            up["moles"] = up.get("Alpha2", 0)
        up["process_image"] = True
        up["splitProcessing"] = {"intensityGenderMale": 0, "intensityGenderFemale": 0, "intensityRest": 1}
        up["metadata"] = {"faces": []}
        out.append({"id": pid, "User Params": up, "path": str(installed[pid])})
    out.sort(key=lambda p: ORDER.index(p["id"]))
    if not out:
        raise EngineUnavailable(f"el preajuste «{PRESET}» no tiene ningún retoque activo")
    return out


def plan(name: str = PRESET) -> List[dict]:
    settings = read_settings()
    folder = Path(settings.get("pluginsPath") or PLUGINS_DEFAULT)
    return build_plugins(read_preset(name), settings, _plugin_paths(folder))


def worker_js() -> Optional[Path]:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for p in (base / "rawflow/r4m_worker.js", Path(__file__).with_name("r4m_worker.js")):
        if p.exists():
            return p
    return None


def usable() -> Tuple[bool, str]:
    """(True, resumen) si el paso 4 puede ir por el motor directo; si no, (False, motivo)."""
    if sys.platform not in ("darwin", "win32"):
        return False, "solo en Mac y Windows"
    if sys.platform == "win32" and os.environ.get("RAWFLOW_SIN_APPS") == "1":   # pruebas: nunca el motor de verdad
        return False, "desactivado en pruebas"
    if not BIN.exists():
        return False, "no encuentro Retouch4me Arams"
    if not worker_js():
        return False, "falta r4m_worker.js dentro de RawFlow"
    try:
        chain = plan()
    except EngineUnavailable as e:
        return False, str(e)
    return True, " · ".join(f"{p['id']} {round(p['User Params'].get('Alpha1', 0) * 100)}" for p in chain)


# ------------------------------------------------------------------ retoque
def _jpgs(folder: Path) -> List[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg") and not p.name.startswith("."))


def _mmss(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600} h {sec % 3600 // 60:02d} min"


def retouch(src: Path, dest: Path, log, should_stop=lambda: False, progress=lambda f: None,
            preset: str = PRESET, expected_total: Optional[int] = None) -> int:
    """Retoca los JPEG de `src` con el preajuste de Arams y los deja en `dest` con el mismo nombre.
    Devuelve cuántas fotos retocadas hay en `dest`. Las que ya estaban hechas no se repiten.
    `expected_total`: fotos de TODO el evento (por tandas aún no están todas en `src`), para el «X de N · quedan»."""
    src, dest = Path(src), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for p in dest.glob(TMP_PREFIX + "*"):              # restos de un intento cortado
        p.unlink(missing_ok=True)
    photos = _jpgs(src)
    if not photos:
        raise RuntimeError(f"No hay JPEG en «{src}».")
    done_names = {p.name for p in dest.glob("*.jp*g") if p.stat().st_size > 100_000} if dest != src else set()
    todo = [p for p in photos if p.name not in done_names]
    already = len(photos) - len(todo)
    if not todo:
        log(f"Retouch4me: las {len(photos)} fotos ya estaban retocadas.")
        return len(photos)
    chain = plan(preset)
    if already:
        log(f"Retouch4me: {already} ya estaban hechas; sigo con las {len(todo)} que faltan.")
    stop_file = WORK / "retouch4me-parar"
    WORK.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)
    items = [{"in": str(p), "tmp": str(dest / (TMP_PREFIX + p.name)), "out": str(dest / p.name)} for p in todo]
    LAST_DECISION[0] = ""
    k = processes(len(items))
    if LAST_DECISION[0]:
        log(LAST_DECISION[0])
    aviso = gpu_warning() if os.environ.get("RAWFLOW_SIN_APPS") != "1" else ""
    if aviso:
        log("⚠ " + aviso)
        live.set("ret", aviso)
    parts = [list(range(i, len(items), k)) for i in range(k)]   # foto i → proceso i % k (cada uno, su parte)
    env0 = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    for v in ("NODE_OPTIONS", "ELECTRON_NO_ASAR", "RF_FD", "RF_FILE"):
        env0.pop(v, None)
    lines: "queue.Queue[Tuple[int, Optional[str]]]" = queue.Queue()
    procs: List[subprocess.Popen] = []
    early: "collections.deque" = collections.deque()   # avisos leídos mientras arrancaban los motores
    for old in WORK.glob("retouch4me-trabajo-*.json"):      # los de otra vez con más procesos: fuera
        old.unlink(missing_ok=True)
    for w, idx in enumerate(parts):
        job_file = WORK / ("retouch4me-trabajo.json" if w == 0 else f"retouch4me-trabajo-{w + 1}.json")
        job_file.write_text(json.dumps({"logDir": str(WORK / "retouch4me-registro"), "stopFile": str(stop_file),
                                        "plugins": chain, "quality": 100, "items": [items[i] for i in idx]},
                                       ensure_ascii=False), encoding="utf-8")
        procs.append(_start_worker(w, job_file, env0, lines))
        if w < k - 1:
            # Escalonados: el siguiente motor arranca cuando este ya cargó sus plugins. Arrancando los 3 a la vez, a
            # veces uno no cargaba («código 3», 24-nov-2026) y se repetía todo el paso. Cuesta ~1 s por motor.
            t_w = time.monotonic()
            while time.monotonic() - t_w < 90:
                try:
                    got = lines.get(timeout=1.0)
                except queue.Empty:
                    if should_stop():
                        break
                    continue
                early.append(got)
                if got[0] == w and (got[1] is None or got[1].startswith(("READY", "FATAL", "LOADERR"))):
                    break

    total = len(todo)
    ok_n, errors, loaderr, durs = 0, [], [], []
    t_start = last_seen = time.monotonic()
    cur = [-1] * k                                    # foto en curso de cada proceso (índice en `items`)
    photo_t0 = [t_start] * k
    ready_n = ended = finished_n = 0
    rcs: List[Optional[int]] = [None] * k

    def pace() -> float:
        """Segundos por foto de CADA proceso: media de las ya hechas en este evento (sin la primera, que lleva el
        arranque del motor), o lo típico con CINEMA mientras no hay datos. Con k procesos salen k a la vez."""
        d = durs[1:] or durs
        return sum(d) / len(d) if d else 55.0

    def stop_all():
        for p in procs:
            if p.poll() is None:
                p.kill()

    try:
        while ended < k:
            if should_stop():
                stop_file.touch()                          # cada motor corta la foto en curso y sale
                for p in procs:
                    try:
                        p.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        p.kill()
                raise RuntimeError("Cancelado.")
            try:
                w, line = early.popleft() if early else lines.get(timeout=1.0)   # primero lo leído al arrancar
            except queue.Empty:
                w, line = -1, ""
            now = time.monotonic()
            if line is None:                                # ese proceso terminó
                ended += 1
                continue
            if not line:
                # vigilante: sin señal de NINGÚN motor mucho más de lo normal → cortar y reintentar las que faltan
                if now - last_seen > (max(300.0, pace() * 6) if ok_n else 600.0):
                    stop_all()
                    raise RuntimeError(f"El motor de Retouch4me dejó de avanzar ({ok_n} de {total}): "
                                       "reintento con las que faltan.")
                busy = [min(0.95, (now - photo_t0[j]) / pace()) for j in range(k) if cur[j] >= 0]
                if busy:                                    # barra suave dentro de cada foto (por tiempo)
                    progress((ok_n + sum(busy)) / total)
                continue
            last_seen = now
            kind, _, rest = line.partition(" ")
            if kind == "READY":
                ready_n += 1
                if ready_n == 1:
                    log(f"Retouch4me: motor listo ({' → '.join(p['id'] for p in chain)}) · {total} fotos"
                        + (f" · {k} a la vez" if k > 1 else ""))
            elif kind == "START":
                j = int(rest)
                cur[w], photo_t0[w] = (parts[w][j] if 0 <= j < len(parts[w]) else -1), now
            elif kind == "OK":
                ok_n += 1
                durs.append(now - photo_t0[w])
                cur[w] = -1
                all_n = max(expected_total or 0, already + total)
                left = all_n - already - ok_n
                progress(ok_n / total)
                # lo que falta: con el ritmo de las ÚLTIMAS fotos (si el equipo se calienta o vienen fotos de grupo,
                # se nota enseguida) mezclado con la media del evento; con k procesos, k fotos a la vez
                recent = durs[-25:] if len(durs) > 3 else durs
                eta_pace = (0.6 * (sum(recent) / len(recent)) + 0.4 * pace()) / k
                log(f"Retouch4me: {already + ok_n} de {all_n} retocadas  ·  {pace() / k:.0f} s por foto"
                    + (f"  ·  quedan {_mmss(eta_pace * left)}" if left > 0 else ""))
                live.set("ret", f"{already + ok_n:,} de {all_n:,} fotos  ·  {pace() / k:.0f} s por foto"
                                + (f" ({k} a la vez)" if k > 1 else "") + "  ·  CINEMA")
                live.set_eta(STEP_RET, eta_pace * left)
            elif kind == "ERR":
                p_ = rest.split(" ", 2)
                j = int(p_[0]) if p_ and p_[0].lstrip("-").isdigit() else -1
                i = parts[w][j] if 0 <= j < len(parts[w]) else -1
                name = Path(items[i]["in"]).name if 0 <= i < len(items) else "?"
                why = p_[2] if len(p_) > 2 else rest
                errors.append(f"{name}: {why}")
                log(f"   Retouch4me no pudo con {name} ({why})")
            elif kind in ("LOADERR", "FATAL"):
                loaderr.append(rest)
            elif kind in ("END", "STOPPED"):
                finished_n += kind == "END"
            # PROG: solo señal de vida (el motor reinicia su cuenta en cada plugin)
        for j, p in enumerate(procs):
            rcs[j] = p.wait(timeout=30)
    finally:
        stop_all()
        for p in dest.glob(TMP_PREFIX + "*"):
            p.unlink(missing_ok=True)
    if loaderr or ready_n < k:
        raise EngineUnavailable("el motor no cargó los plugins"
                                + (f" ({loaderr[0]})" if loaderr else f" (código {next((c for c in rcs if c), None)})"))
    if errors or finished_n < k:
        msg = f"Retouch4me: {len(errors) or total - ok_n} fotos sin retocar"
        raise RuntimeError(msg + (f" ({errors[0]})" if errors else "") + ": reintento con las que faltan.")
    log(f"✓  Retouch4me: {already + ok_n} fotos retocadas con {preset} en «{dest.name}» "
        f"(motor directo{f', {k} a la vez' if k > 1 else ''}, {_mmss(time.monotonic() - t_start)}).")
    return already + ok_n


def _start_worker(w: int, job_file: Path, env0: dict, lines: "queue.Queue") -> subprocess.Popen:
    """Arranca un proceso del motor con su trabajo; sus avisos llegan a `lines` como (w, línea) y (w, None) al salir."""
    env = dict(env0)
    noise = open(WORK / ("retouch4me-salida.log" if w == 0 else f"retouch4me-salida-{w + 1}.log"), "w",
                 encoding="utf-8")                    # lo que imprime el motor suelto
    if sys.platform == "win32":                       # Windows no hereda descriptores sueltos: los avisos van a un
        chan = WORK / ("retouch4me-avisos.txt" if w == 0 else f"retouch4me-avisos-{w + 1}.txt")   # archivo que se sigue
        chan.write_text("", encoding="utf-8")
        env["RF_FILE"] = str(chan)
        try:
            proc = subprocess.Popen([str(BIN), str(worker_js()), str(job_file)], stdin=subprocess.PIPE,
                                    stdout=noise, stderr=subprocess.STDOUT, env=env, cwd=str(WORK),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            noise.close()

        def reader():
            buf = ""
            with open(chan, "r", encoding="utf-8", errors="replace") as f:
                while True:
                    chunk = f.read()
                    if chunk:
                        buf += chunk
                        *full, buf = buf.split("\n")
                        for line in full:
                            lines.put((w, line.rstrip("\r")))
                        continue
                    if proc.poll() is not None:           # el motor salió: lo último que dejara escrito y fin
                        rest = f.read()
                        for line in (buf + rest).split("\n"):
                            if line.strip():
                                lines.put((w, line.rstrip("\r")))
                        break
                    time.sleep(0.2)
            lines.put((w, None))
    else:
        rfd, wfd = os.pipe()                          # canal de avisos del trabajador (ver r4m_worker.js)
        env["RF_FD"] = str(wfd)
        try:
            proc = subprocess.Popen([str(BIN), str(worker_js()), str(job_file)], stdin=subprocess.PIPE,
                                    stdout=noise, stderr=subprocess.STDOUT, env=env, cwd=str(WORK),
                                    pass_fds=(wfd,))
        finally:
            os.close(wfd)
            noise.close()

        def reader():
            with os.fdopen(rfd, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    lines.put((w, line.rstrip("\n")))
            lines.put((w, None))
    threading.Thread(target=reader, daemon=True).start()
    return proc


def processes(n_fotos: int) -> int:
    """Cuántos motores a la vez. Medido (24-nov-2026, RTX 5070 Ti 16 GB): 1 → 23 s por foto, 3 → 16 s, fotos
    idénticas byte a byte. Cada motor ocupa ≈3,5 GB de la tarjeta: se usan los que quepan en la memoria LIBRE de la
    tarjeta (con margen), como mucho 3. Mac: 1 (memoria compartida con todo el sistema; sin medir).
    RAWFLOW_R4M_PROCESOS lo fija (1 = como antes)."""
    try:
        n = int(os.environ.get("RAWFLOW_R4M_PROCESOS", "0"))
    except ValueError:
        n = 0
    if n <= 0:
        n = 1
        if sys.platform == "win32":
            # 25-nov-2026 (revisión): era UNA lectura de nvidia-smi; si tardaba o la tarjeta aún soltaba memoria de
            # las apps recién cerradas, iba con 1 motor TODO el paso (≈23 s/foto en vez de ≈16,5) sin decir nada. Se
            # reintenta si no responde y, si da menos de 3, se vuelve a mirar unos segundos (vale la ÚLTIMA lectura).
            free = None
            for _ in range(3):
                free = _gpu_free_mb()
                if free is not None:
                    break
                time.sleep(2)
            t0 = time.time()
            want = min(MAX_PROCS, n_fotos)               # (revisión 26-nov-2026: con 1-2 fotos esperaba 20 s para nada)
            while free is not None and int((free - 1500) // VRAM_PER_PROC) < want and time.time() - t0 < 20:
                time.sleep(4)
                free = _gpu_free_mb() or free
            if free is not None:
                n = max(1, min(MAX_PROCS, int((free - 1500) // VRAM_PER_PROC)))
            LAST_DECISION[0] = (f"Retouch4me: {min(n, n_fotos)} motor{'es' if min(n, n_fotos) > 1 else ''} a la vez · "
                                + (f"{free / 1024:.1f} GB libres en la tarjeta" if free is not None
                                   else "no pude leer la tarjeta gráfica"))
    return max(1, min(n, n_fotos))


LAST_DECISION = [""]          # cómo se eligió el nº de motores (para el registro)


MAX_PROCS = 3                 # 25-nov-2026, sin otras apps en la tarjeta: 3 → 17,5 s/foto, 4 → 17,4 s/foto y 15,3 GB de
#                               16 (la GPU ya va al tope con 3): no compensa el riesgo de llenarla
VRAM_PER_PROC = 3600          # MB por motor (medido: 1 motor +3,7 GB; 3 motores ≈ +10,5 GB)


GPU_HOGS = {"resolve.exe": "DaVinci Resolve", "adobe premiere pro.exe": "Premiere Pro", "afterfx.exe": "After Effects",
            "photoshop.exe": "Photoshop", "lightroom.exe": "Lightroom", "obs64.exe": "OBS", "topaz video ai.exe":
            "Topaz Video AI", "cineslide.exe": "CineSlide"}


def gpu_hogs() -> List[Tuple[str, float]]:
    """[(app, MB de la tarjeta que ocupa)] de las apps pesadas abiertas que usan la tarjeta gráfica (Windows)."""
    if sys.platform != "win32":
        return []
    try:
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return []
    mb: Dict[str, float] = {}
    for line in r.stdout.splitlines():                 # (con WDDM la memoria por app suele salir «[N/A]»)
        name, _, used = line.rpartition(",")
        try:
            mb[Path(name.strip()).name.lower()] = float(used)
        except ValueError:
            pass
    return [(app, mb.get(exe, 0.0)) for exe, app in GPU_HOGS.items() if f'"{exe}"' in t]


def gpu_warning() -> str:
    """Aviso si otra app pesada ocupa la tarjeta y deja poca memoria: medido con DaVinci Resolve abierto (9-11 GB),
    Retouch4me fue de 23 s a 98 s por foto y no cabía más de un motor."""
    free = _gpu_free_mb()
    if free is None or free >= MAX_PROCS * VRAM_PER_PROC + 1500:
        return ""
    hogs = [a for a, _ in gpu_hogs()]
    if not hogs:
        return ""
    return (f"{' y '.join(hogs)} {'está' if len(hogs) == 1 else 'están'} usando la tarjeta gráfica (quedan "
            f"{free / 1024:.1f} GB libres): Retouch4me irá bastante más lento. Ciérralo para que vaya más rápido.")


def _gpu_free_mb() -> Optional[float]:
    """Memoria libre de la tarjeta NVIDIA (MB), o None si no hay nvidia-smi."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        vals = [float(x) for x in r.stdout.split() if x.strip()]
        return max(vals) if r.returncode == 0 and vals else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

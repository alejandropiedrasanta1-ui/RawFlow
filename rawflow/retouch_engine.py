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
    job_file = WORK / "retouch4me-trabajo.json"
    stop_file = WORK / "retouch4me-parar"
    WORK.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)
    items = [{"in": str(p), "tmp": str(dest / (TMP_PREFIX + p.name)), "out": str(dest / p.name)} for p in todo]
    job_file.write_text(json.dumps({"logDir": str(WORK / "retouch4me-registro"), "stopFile": str(stop_file),
                                    "plugins": chain, "quality": 100, "items": items}, ensure_ascii=False),
                        encoding="utf-8")
    win = sys.platform == "win32"
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    for k in ("NODE_OPTIONS", "ELECTRON_NO_ASAR", "RF_FD", "RF_FILE"):
        env.pop(k, None)
    noise = open(WORK / "retouch4me-salida.log", "w", encoding="utf-8")   # lo que imprime el motor suelto
    lines: "queue.Queue[Optional[str]]" = queue.Queue()
    if win:                                           # Windows no hereda descriptores sueltos: los avisos van a un
        chan = WORK / "retouch4me-avisos.txt"         # archivo que el trabajador escribe línea a línea y aquí se sigue
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
                            lines.put(line.rstrip("\r"))
                        continue
                    if proc.poll() is not None:           # el motor salió: lo último que dejara escrito y fin
                        rest = f.read()
                        for line in (buf + rest).split("\n"):
                            if line.strip():
                                lines.put(line.rstrip("\r"))
                        break
                    time.sleep(0.2)
            lines.put(None)
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
                    lines.put(line.rstrip("\n"))
            lines.put(None)
    threading.Thread(target=reader, daemon=True).start()

    total = len(todo)
    ok_n, errors, loaderr, durs = 0, [], [], []
    t_start = last_seen = photo_t0 = time.monotonic()
    cur, rc = -1, None
    ready = finished = False

    def pace() -> float:
        """Segundos por foto: media de las ya hechas en este evento (sin la primera, que lleva el arranque del
        motor), o lo típico con CINEMA en este Mac mientras no hay datos."""
        d = durs[1:] or durs
        return sum(d) / len(d) if d else 55.0

    try:
        while True:
            if should_stop():
                stop_file.touch()                          # el motor corta la foto en curso y sale
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("Cancelado.")
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                line = ""
            now = time.monotonic()
            if line is None:                                # el proceso terminó
                break
            if not line:
                # vigilante: sin señal del motor mucho más de lo normal → cortar y reintentar las que faltan
                if now - last_seen > (max(300.0, pace() * 6) if ok_n else 600.0):
                    proc.kill()
                    raise RuntimeError(f"El motor de Retouch4me dejó de avanzar ({ok_n} de {total}): "
                                       "reintento con las que faltan.")
                if cur >= 0:                                # barra suave dentro de cada foto (por tiempo)
                    progress((ok_n + min(0.95, (now - photo_t0) / pace())) / total)
                continue
            last_seen = now
            kind, _, rest = line.partition(" ")
            if kind == "READY":
                ready = True
                log(f"Retouch4me: motor listo ({' → '.join(p['id'] for p in chain)}) · {total} fotos")
            elif kind == "START":
                cur, photo_t0 = int(rest), now
            elif kind == "OK":
                ok_n += 1
                durs.append(now - photo_t0)
                all_n = max(expected_total or 0, already + total)
                left = all_n - already - ok_n
                progress(ok_n / total)
                # lo que falta: con el ritmo de las ÚLTIMAS fotos (si el Mac se calienta o vienen fotos de grupo,
                # se nota enseguida) mezclado con la media del evento
                recent = durs[-25:] if len(durs) > 3 else durs
                eta_pace = 0.6 * (sum(recent) / len(recent)) + 0.4 * pace()
                log(f"Retouch4me: {already + ok_n} de {all_n} retocadas  ·  {pace():.0f} s por foto"
                    + (f"  ·  quedan {_mmss(eta_pace * left)}" if left > 0 else ""))
                live.set("ret", f"{already + ok_n:,} de {all_n:,} fotos  ·  {durs[-1]:.0f} s la última  ·  "
                                f"{pace():.0f} s de media  ·  CINEMA")
                live.set_eta(STEP_RET, eta_pace * left)
            elif kind == "ERR":
                parts = rest.split(" ", 2)
                i = int(parts[0]) if parts and parts[0].lstrip("-").isdigit() else -1
                name = Path(items[i]["in"]).name if 0 <= i < len(items) else "?"
                why = parts[2] if len(parts) > 2 else rest
                errors.append(f"{name}: {why}")
                log(f"   Retouch4me no pudo con {name} ({why})")
            elif kind in ("LOADERR", "FATAL"):
                loaderr.append(rest)
            elif kind in ("END", "STOPPED"):
                finished = kind == "END"
            # PROG: solo señal de vida (el motor reinicia su cuenta en cada plugin)
        rc = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        for p in dest.glob(TMP_PREFIX + "*"):
            p.unlink(missing_ok=True)
    if loaderr or not ready:
        raise EngineUnavailable("el motor no cargó los plugins"
                                + (f" ({loaderr[0]})" if loaderr else f" (código {rc})"))
    if errors or not finished:
        msg = f"Retouch4me: {len(errors) or total - ok_n} fotos sin retocar"
        raise RuntimeError(msg + (f" ({errors[0]})" if errors else "") + ": reintento con las que faltan.")
    log(f"✓  Retouch4me: {already + ok_n} fotos retocadas con {preset} en «{dest.name}» "
        f"(motor directo, {_mmss(time.monotonic() - t_start)}).")
    return already + ok_n

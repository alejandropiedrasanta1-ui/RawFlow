"""Paso 6 · Luminar Neo (Windows), pedido del usuario el 24-nov-2026 (video «PASO 5 LUMINAR»):

  1. catálogo NUEVO para el evento (Archivo → Catálogo → Nuevo), así no se mezclan fotos de eventos distintos;
  2. «Añadir carpeta con imágenes» (Ctrl+Mayús+N) → la carpeta «4 Fotos finales» del evento;
  3. a una foto, el preajuste «CINEMA AJUSTE» (Mis preajustes); Acciones → Ajustes → Copiar ajustes; todas
     seleccionadas → Pegar ajustes;
  4. Exportar → «JPEG · 100 % de calidad» → carpeta «5 Fotos finales Luminar» (mismos nombres: 1.jpg, 2.jpg…).

Luminar no tiene línea de comandos: se maneja su ventana leyendo la pantalla (OCR de Windows) y con clics, como
Bridge e Imagen en imagen_win. Las fotos nunca se tocan: Luminar solo lee «4 Fotos finales» y escribe en la nueva.
Al terminar se comprueba que cada foto exportada CAMBIÓ respecto a la de Retouch4me (si el preajuste no se aplicó,
saldrían iguales) y se cierra Luminar.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

EXE = "Luminar Neo.exe"
PRESET = "CINEMA AJUSTE"
_ROAM = Path(os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming"))
DATA = _ROAM / "Luminar Neo"
Log = Callable[[str], None]


def app_path() -> Optional[Path]:
    for base in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for p in (Path(base) / "Skylum" / "Luminar Neo" / EXE, Path(base) / "Programs" / "Luminar Neo" / EXE):
            if p.exists():
                return p
    return None


def preset_exists(name: str = PRESET) -> bool:
    """¿Está «CINEMA AJUSTE» en Mis preajustes? (cada preajuste es una carpeta .lnp con su template.lmps en JSON)."""
    for f in (DATA / "Data" / "Presets" / "Users").glob("*.lnp/template.lmps"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("name", "").strip().lower() == name.lower():
                return True
        except (OSError, ValueError):
            continue
    return False


def available() -> Tuple[bool, str]:
    if sys.platform != "win32":
        return False, "solo en Windows"
    from . import imagen_win
    if imagen_win.no_apps():
        return False, "desactivado en pruebas"
    if not app_path():
        return False, "no encuentro Luminar Neo"
    if not preset_exists():
        return False, f"no encuentro el preajuste «{PRESET}» en Mis preajustes de Luminar"
    return True, ""


def run(event_dir: Path, src: Path, out: Path, log: Log, should_stop=lambda: False,
        progress=lambda f: None) -> int:
    """Todo el paso. Devuelve cuántas fotos hay en `out` al terminar (con el preajuste comprobado)."""
    from . import core
    from . import imagen_win as iw
    event_dir, src, out = Path(event_dir), Path(src), Path(out)
    fotos = _jpgs(src)
    n = len(fotos)
    if not n:
        return 0
    s = iw.Screen(log, should_stop, EXE)
    cat = catalog_dir() / (core.clean_name(event_dir.name) or "Evento")
    cat = core.unique_folder(cat)                    # (si se rehace, otro catálogo nuevo: nunca se mezclan)
    tmp = out.with_name(out.name + ".exportando")    # se exporta aparte y se cambia al final: nunca a medias
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _open(s, log)
        _new_catalog(s, cat.parent / (cat.name + ".luminarneo"), log)   # Luminar crea él la carpeta «<evento>»
        _add_folder(s, src, n, log)
        _select_all(s)
        _apply_preset(s, log)
        _sync_all(s, n, log)
        k = _export(s, tmp, n, log, should_stop, progress)
        if k < n:
            raise iw.UIError(f"Luminar exportó {k} de {n} fotos.")
        ok, same = _changed(src, tmp)
        if ok * 2 < n:                               # la mayoría salió igual: el ajuste no se pegó → reintentar
            raise iw.UIError(f"«{PRESET}» no se aplicó a {len(same)} de {n} fotos ({', '.join(same[:5])}).")
        if same:
            log(f"Luminar: {len(same)} foto(s) casi no cambian con «{PRESET}» ({', '.join(same[:5])}): se dejan así.")
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
        log(f"✓  Luminar: {n:,} fotos con «{PRESET}» en «{out.name}».")
        return len(_jpgs(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            iw.close_app(EXE, wait=30)
        except Exception:  # noqa: BLE001
            pass


def catalog_dir() -> Path:
    """Donde quedan los catálogos de cada evento («Imágenes\\Catálogos Luminar RawFlow»)."""
    pics = Path(os.environ.get("USERPROFILE") or Path.home()) / "Pictures"
    return pics / "Catálogos Luminar RawFlow"


# ------------------------------------------------------------------------------------------------- pantalla
def _jpgs(folder: Path) -> List[Path]:
    return sorted(p for p in Path(folder).glob("*.jp*g") if not p.name.startswith(".")) if Path(folder).is_dir() else []


def _open(s, log: Log) -> None:
    """Luminar cerrado y abierto otra vez (estado conocido), maximizado en la pantalla principal."""
    from . import imagen_win as iw
    from . import winscreen as ws
    if s.main_window() or iw._running(EXE):
        iw.close_app(EXE, wait=30)
    exe = app_path()
    log("Luminar: abriendo…")
    subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
    t0 = time.time()
    while True:
        if s.should_stop():
            raise iw.UIError("Cancelado.")
        wins = ws.windows(exe=EXE)
        big = [w for w in wins if w["rect"][2] > 900 or w["min"]]
        if big and not any(w["rect"][2] < 900 and not w["min"] for w in wins):   # ya no está la pantalla de carga
            break
        if time.time() - t0 > 150:
            raise iw.UIError("Luminar no llegó a abrirse.")
        time.sleep(1)
    w = big[0]
    if w["min"]:                                     # a veces se abre minimizado
        import ctypes
        ctypes.windll.user32.ShowWindow(w["hwnd"], 9)
        time.sleep(1.5)
    ws.activate(w["hwnd"], maximize=True, primary=True)
    s.wait("Exportar", "Añadir fotos", timeout=90)    # la interfaz ya responde
    time.sleep(6)                                    # (y termina de cargar el catálogo anterior)


def _menu(s, *path: str, then: str = "") -> None:
    """Menú del logo «luminar» → Archivo → … Los submenús de Luminar se abren al PASAR el ratón: tras cada clic se
    comprueba que se ve lo siguiente (path[i+1] o `then`) y, si no, se vuelve a poner el ratón sobre ese elemento."""
    from . import imagen_win as iw
    for _ in range(8):                               # (con un catálogo grande, Luminar tarda en responder al abrir)
        s.front(force=True)                          # Luminar delante (otra ventana o Alt+Tab se llevan el clic)
        if s.find("Archivo", exact=True, region=(0, 0.04, 0.08, 0.12)):
            break
        s.click(s.rect[0] + 0.033 * s.rect[2], s.rect[1] + 0.033 * s.rect[3])   # el logo (arriba a la izquierda)
        t0 = time.time()
        while time.time() - t0 < 4 and not s.find("Archivo", exact=True, region=(0, 0.04, 0.08, 0.12)):
            time.sleep(0.4)
    for i, item in enumerate(path):
        nxt = path[i + 1] if i + 1 < len(path) else then
        for _try in range(4):
            boxes = s.ocr()
            hits = [b for b in s.find(item, exact=True, boxes=boxes) if b["y"] > s.rect[1] + 0.05 * s.rect[3]]
            if not hits:
                break
            b = min(hits, key=lambda q: q["x"]) if not i else max(hits, key=lambda q: q["x"])
            s.click_box(b, after=0.9)
            if not nxt or any(q["x"] > b["x"] + b["w"] and q["y"] > s.rect[1] + 0.05 * s.rect[3]
                              for q in s.find(nxt, boxes=s.ocr())):
                break                                # se abrió su submenú (lo siguiente está a su derecha, no
            #                                          el «Catálogo» de la barra de arriba)
        else:
            hits = []
        if not hits:
            iw.diag(s, f"luminar-menu-{item}")
            raise iw.UIError(f"No encuentro «{item}» en el menú de Luminar.")


def _save_as(s, path: Path) -> None:
    """Diálogo «Guardar como» de Windows: la ruta completa en «Nombre de archivo» y Guardar."""
    from . import imagen_win as iw
    from . import winscreen as ws
    t0 = time.time()
    while time.time() - t0 < 30:
        w = s.window()
        if w and "guardar" in w["title"].lower():
            ws.activate(w["hwnd"], maximize=False)
            time.sleep(0.8)
            if not s.find("Nombre de archivo", boxes=s.ocr()):
                time.sleep(0.5)
                continue
            s.keys("alt+n")                          # «Nombre de archivo» (atajo de los diálogos de Windows)
            time.sleep(0.4)
            s.keys("ctrl+a")
            s.type(str(path))
            time.sleep(0.6)
            if any(path.stem[:12].lower() in iw.norm(b["t"]) for b in s.ocr()):   # se ve escrito: Guardar
                s.keys("enter")
                time.sleep(1.5)
                return
            lab = s.find("Nombre de archivo")        # no entró: clic en el campo y otra vez
            if lab:
                b = lab[0]
                s.click(b["x"] + b["w"] + 8 * b["h"], b["y"] + b["h"] / 2)
                time.sleep(0.4)
        time.sleep(0.5)
    iw.diag(s, "luminar-guardar-como")
    raise iw.UIError("No pude escribir el nombre del catálogo en «Guardar como» de Luminar.")


def _new_catalog(s, path: Path, log: Log) -> None:
    from . import imagen_win as iw
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Luminar: catálogo nuevo «{path.stem}»…")
    _menu(s, "Archivo", "Catálogo", then="Nuevo")
    s.tap("Nuevo...", "Nuevo…", timeout=10)
    _save_as(s, path)
    t0 = time.time()
    while time.time() - t0 < 60:                     # el catálogo NUEVO está vacío: «Aún no hay fotos»
        boxes = s.ocr()
        if s.find("Aún no hay fotos", boxes=boxes):
            return
        err = s.find("Unable to create", boxes=boxes) or s.find("No se pudo crear", boxes=boxes)
        if err:                                      # (p. ej. «Path is too long»): Luminar sigue con el catálogo
            iw.diag(s, "luminar-catalogo")           # de antes; NO se sigue: se mezclarían eventos
            ok = s.find("Aceptar", exact=True, boxes=boxes) or s.find("OK", exact=True, boxes=boxes)
            if ok:
                s.click_box(ok[0])
            raise iw.UIError(f"Luminar no pudo crear el catálogo en «{path.parent}».")
        b = s.find("Sí", exact=True, boxes=boxes) or s.find("Reemplazar", boxes=boxes)   # ya existía: reemplazar
        if b:
            s.click_box(b[0])
        time.sleep(1)
    iw.diag(s, "luminar-catalogo")
    raise iw.UIError("Luminar no abrió el catálogo nuevo (vacío).")


def _select_all(s) -> None:
    """Todas las fotos de la carpeta seleccionadas (clic en la cuadrícula/película y Ctrl+A)."""
    s.front(force=True)
    s.click(s.rect[0] + 0.45 * s.rect[2], s.rect[1] + 0.5 * s.rect[3])
    time.sleep(0.5)
    s.keys("ctrl+a")
    time.sleep(1.0)


def _apply_preset(s, log: Log, name: str = PRESET) -> None:
    """Pestaña Preajustes (barra derecha) → Mis Preajustes → «CINEMA AJUSTE» (con las fotos seleccionadas)."""
    from . import imagen_win as iw
    key = name[:10].lower()                          # «cinema aju»: en la lista sale cortado («CINEMA AJUS…»)
    right = (0.8, 0.05, 1.0, 0.95)
    for _ in range(4):
        boxes = s.ocr()
        if any(key in iw.norm(b["t"]) for b in boxes if s.inside(b, right)):
            break
        mis = s.find("Mis Preajustes", boxes=boxes, region=right)
        if mis:
            s.click_box(mis[0], after=1.5)
            continue
        tab = s.find("Preajustes", exact=True, boxes=boxes, region=(0.95, 0.05, 1.0, 0.2))
        if tab:
            s.click_box(tab[0], after=1.5)
        else:
            s.keys("t")                              # atajo de la vista Preajustes
            time.sleep(1.5)
    item = [b for b in s.ocr() if key in iw.norm(b["t"]) and s.inside(b, right)]
    if not item:
        iw.diag(s, "luminar-preajuste")
        raise iw.UIError(f"No encuentro el preajuste «{name}» en Mis preajustes de Luminar.")
    log(f"Luminar: aplicando «{name}»…")
    s.click_box(item[0], after=3.0)


def _acciones(s, item: str) -> None:
    """Botón «Acciones» (arriba) → Ajustes → `item` («Copiar Ajustes», «Pegar Ajustes»…)."""
    from . import imagen_win as iw
    for _ in range(3):
        s.front(force=True)
        s.tap("Acciones", timeout=15, region=(0.75, 0, 0.9, 0.06), after=1.0)
        for _h in range(3):
            aj = [b for b in s.find("Ajustes", exact=True) if b["y"] > s.rect[1] + 0.06 * s.rect[3]]
            if not aj:
                break
            s.click_box(min(aj, key=lambda b: b["y"]), after=0.9)   # (pasar por encima abre su submenú)
            it = s.find(item, exact=True)
            if it:
                s.click_box(it[0], after=1.2)
                return
        s.esc()
        time.sleep(1)
    iw.diag(s, "luminar-acciones")
    raise iw.UIError(f"No encuentro Acciones → Ajustes → «{item}» en Luminar.")


def _sync_all(s, n: int, log: Log) -> None:
    """Los ajustes de la foto activa (con el preajuste) a TODAS: Copiar Ajustes → todas seleccionadas → Pegar
    Ajustes (como en el video del usuario). Espera a que termine «Pegando ajustes»."""
    from . import imagen_win as iw
    log(f"Luminar: copiando «{PRESET}» a las {n:,} fotos…")
    _acciones(s, "Copiar Ajustes")
    _select_all(s)
    _acciones(s, "Pegar Ajustes")
    t0 = time.time()
    time.sleep(2)
    while time.time() - t0 < max(60, n * 1.5):      # mientras se ve «Pegando ajustes…»
        if not any("pegando" in iw.norm(b["t"]) for b in s.ocr()):
            break
        time.sleep(1)
    time.sleep(2)


def _changed(src: Path, out: Path, min_diff: float = 2.0) -> Tuple[int, List[str]]:
    """¿Cada foto exportada cambió respecto a su original de Retouch4me? (diferencia media de píxeles en pequeño; sin
    el preajuste, volver a guardar el JPEG da ≈0,8-1,1; con «CINEMA AJUSTE», ≈5). (cuántas cambiaron, las que no)."""
    import cv2
    import numpy as np
    ok, same = 0, []
    for p in _jpgs(src):
        q = Path(out) / p.name
        try:
            a = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
            b = cv2.imdecode(np.fromfile(str(q), np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
            b = cv2.resize(b, (a.shape[1], a.shape[0]))
            d = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
        except Exception:  # noqa: BLE001
            d = 0.0
        if d >= min_diff:
            ok += 1
        else:
            same.append(p.name)
    return ok, same


def _export(s, out: Path, n: int, log: Log, should_stop=lambda: False, progress=lambda f: None) -> int:
    """Exportar → «JPEG · 100 % de calidad» → carpeta `out`. Espera a que estén las `n` fotos enteras."""
    import re
    from . import imagen_win as iw
    out.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in _jpgs(out)}
    for _ in range(3):
        _select_all(s)
        s.tap("Exportar", exact=True, timeout=15, region=(0.85, 0, 0.97, 0.06), after=1.5)
        boxes = s.ocr()
        sel = [b for b in boxes if "seleccionad" in iw.norm(b["t"])]
        k = int(re.findall(r"\d+", sel[0]["t"])[0]) if sel and re.findall(r"\d+", sel[0]["t"]) else 0
        if k >= n:
            break
        s.esc()
        time.sleep(1)
    else:
        iw.diag(s, "luminar-exportar")
        raise iw.UIError(f"Luminar no seleccionó las {n} fotos para exportar.")
    q = [b for b in s.ocr() if "100" in b["t"] and "calidad" in iw.norm(b["t"])]
    if not q:
        iw.diag(s, "luminar-exportar")
        raise iw.UIError("No encuentro «JPEG · 100 % de calidad» en Exportar de Luminar.")
    log(f"Luminar: exportando {n:,} JPEG (100 %) a «{out.name}»…")
    s.click_box(min(q, key=lambda b: b["y"]), after=1.5)
    iw._pick_folder(s, out)
    t0, last, last_t = time.time(), -1, time.time()
    while True:                                      # hasta que estén todas y ninguna esté a medio escribir
        if should_stop():
            raise iw.UIError("Cancelado.")
        fs = [p for p in _jpgs(out) if p.name not in before or p.stat().st_mtime >= t0 - 5]
        k = len(fs)
        progress(min(1.0, k / max(1, n)))
        if k != last:
            last, last_t = k, time.time()
            if k:
                log(f"Luminar: {k:,} de {n:,} exportadas")
        if k >= n:
            time.sleep(3)                            # la última termina de escribirse
            return len(_jpgs(out))
        if time.time() - last_t > max(180, 30 + n * 3):
            iw.diag(s, "luminar-exportando")
            raise iw.UIError(f"Luminar dejó de exportar ({k} de {n}).")
        time.sleep(1.5)


def _add_folder(s, src: Path, n: int, log: Log) -> None:
    from . import imagen_win as iw
    log(f"Luminar: añadiendo las {n:,} fotos de «{src.name}»…")
    s.front(force=True)
    s.keys("ctrl+shift+n")                           # Archivo → Añadir carpeta con imágenes…
    time.sleep(1.5)
    iw._pick_folder(s, src)
    t0 = time.time()
    while time.time() - t0 < max(120, n * 2):        # hasta que se ve la carpeta con sus fotos
        if s.should_stop():
            raise iw.UIError("Cancelado.")
        boxes = s.ocr()
        if s.find(src.name, boxes=boxes) and not s.find("Aún no hay fotos", boxes=boxes):
            time.sleep(3)
            return
        time.sleep(1)
    iw.diag(s, "luminar-importar")
    raise iw.UIError("Luminar no mostró las fotos añadidas.")

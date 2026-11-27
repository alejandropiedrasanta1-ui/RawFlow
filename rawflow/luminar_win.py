"""Paso 6 · Luminar Neo (Windows), pedido del usuario el 24-nov-2026 (video «PASO 5 LUMINAR»):

  1. catálogo NUEVO para el evento (Archivo → Catálogo → Nuevo), así no se mezclan fotos de eventos distintos;
  2. «Añadir carpeta con imágenes» (Ctrl+Mayús+N) → la carpeta «1 Original/Retouch4me» del evento;
  3. a una foto, el preajuste «CINEMA AJUSTE» (Mis preajustes); Acciones → Ajustes → Copiar ajustes; todas
     seleccionadas → Pegar ajustes;
  4. Exportar → «JPEG · 100 % de calidad» → carpeta «3 Fotos finales» (mismos nombres: 1.jpg, 2.jpg…).

Luminar no tiene línea de comandos: se maneja su ventana leyendo la pantalla (OCR de Windows) y con clics, como
Bridge e Imagen en imagen_win. Las fotos nunca se tocan: Luminar solo lee «1 Original/Retouch4me» y escribe en la nueva.
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
PRESETS = ("CINEMA AJUSTE", "CINEMA")    # el primero que exista en Mis preajustes (26-nov-2026: el del usuario es «CINEMA»)
PRESET = PRESETS[0]                     # el que se usa: lo fija available() con el que encuentre
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


def _user_presets() -> List[str]:
    """Nombres de Mis preajustes (cada preajuste es una carpeta .lnp con su template.lmps en JSON)."""
    out = []
    for f in (DATA / "Data" / "Presets" / "Users").glob("*.lnp/template.lmps"):
        try:
            out.append(str(json.loads(f.read_text(encoding="utf-8")).get("name", "")).strip())
        except (OSError, ValueError):
            continue
    return out


def find_preset() -> Optional[str]:
    """El nombre, tal cual está en Luminar, del primero de PRESETS que exista; None si ninguno."""
    have = {n.lower(): n for n in _user_presets()}
    return next((have[p.lower()] for p in PRESETS if p.lower() in have), None)


def preset_exists(name: Optional[str] = None) -> bool:
    if name is None:
        return find_preset() is not None
    return name.lower() in {n.lower() for n in _user_presets()}


def _is_item(text: str, name: str) -> bool:
    """¿Este rótulo de la lista es el preajuste `name`? Los largos salen cortados («CINEMA AJUS…»); uno corto como
    «CINEMA» se compara entero (que no valga «Cinematic» ni otro que empiece igual)."""
    from . import imagen_win as iw
    t, n = iw.norm(text).strip(" .…"), iw.norm(name)
    if len(n) >= 10:
        return n[:10] in t
    return t == n


def available() -> Tuple[bool, str]:
    if sys.platform != "win32":
        return False, "solo en Windows"
    from . import imagen_win
    if imagen_win.no_apps():
        return False, "desactivado en pruebas"
    if not app_path():
        return False, "no encuentro Luminar Neo"
    global PRESET
    found = find_preset()
    if not found:
        return False, f"no encuentro el preajuste «{' » ni «'.join(PRESETS)}» en Mis preajustes de Luminar"
    PRESET = found
    return True, ""


def _catalog_for(event_dir: Path) -> Path:
    from . import core
    cat = catalog_dir() / (core.clean_name(Path(event_dir).name) or "Evento")
    cat = core.unique_folder(cat)                    # (si se rehace, otro catálogo nuevo: nunca se mezclan)
    return cat.parent / (cat.name + ".luminarneo")   # Luminar crea él la carpeta «<evento>»


def prepare(event_dir: Path, log: Log, should_stop=lambda: False) -> Path:
    """27-nov-2026 (más rápido): Luminar abierto y con el catálogo NUEVO y vacío del evento, mientras Retouch4me
    termina sus últimas fotos (el motor de Retouch4me no tiene ventana: no se estorban). No toca ninguna foto: el
    paso 6 empieza igual, después del 5, solo que sin esperar a que Luminar se abra (~1:30 menos). Devuelve el
    catálogo; si algo falla, Luminar queda cerrado y el paso 6 lo hace todo como siempre."""
    from . import imagen_win as iw
    s = iw.Screen(log, should_stop, EXE)
    cat = _catalog_for(event_dir)
    try:
        _open(s, log)
        _new_catalog(s, cat, log)
        return cat
    except Exception:
        try:
            iw.close_app(EXE, wait=30)
        except Exception:  # noqa: BLE001
            pass
        raise


def _still_prepared(s, cat: Path, log: Log = lambda m: None) -> bool:
    """¿Sigue abierto Luminar con el catálogo vacío que se preparó?
    27-nov-2026 (evento «26 nov 2026»): antes se ponía delante para mirarlo; con Chrome de la persona delante eso
    fallaba sin decir nada y se abría todo otra vez (catálogo de más, ~1 min perdido). Leer no necesita tenerlo
    delante (está en el monitor de trabajo): se mira tal cual y solo si no se ve se intenta traer delante."""
    if not cat or not Path(cat).exists():
        log("   (lo preparado antes no sirve: el catálogo ya no está; abro Luminar de nuevo)")
        return False
    if not s.main_window():
        log("   (lo preparado antes no sirve: Luminar se cerró; lo abro de nuevo)")
        return False
    empty = lambda boxes: bool(s.find("Aún no hay fotos", boxes=boxes))         # noqa: E731
    for k in range(3):
        try:
            if k == 2:
                s.front(force=True)
            if empty(s.ocr()) or empty(s.ocr_tiles(3)):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
    log("   (lo preparado antes no sirve: Luminar no muestra el catálogo vacío; lo abro de nuevo)")
    return False


def run(event_dir: Path, src: Path, out: Path, log: Log, should_stop=lambda: False,
        progress=lambda f: None, prepared: Optional[Path] = None) -> int:
    """Todo el paso. Devuelve cuántas fotos hay en `out` al terminar (con el preajuste comprobado).
    `prepared`: catálogo vacío ya abierto por prepare() (si ya no está, se abre todo como siempre)."""
    from . import imagen_win as iw
    event_dir, src, out = Path(event_dir), Path(src), Path(out)
    fotos = _jpgs(src)
    n = len(fotos)
    if not n:
        return 0
    s = iw.Screen(log, should_stop, EXE)
    tmp = out.with_name(out.name + ".exportando")    # se exporta aparte y se cambia al final: nunca a medias
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        if prepared and _still_prepared(s, prepared, log):
            log(f"Luminar: ya estaba abierto con el catálogo nuevo «{Path(prepared).stem}».")
        else:
            _open(s, log)
            _new_catalog(s, _catalog_for(event_dir), log)
        # 27-nov-2026 (evento «26 nov 2026»: Luminar se abrió 4 veces): si un clic de la persona en otra ventana corta
        # una parte, se repite ESA parte con Luminar como está, no el paso entero (catálogo nuevo + volver a añadir).
        local = lambda what, fn, again=lambda: True: _local(s, log, should_stop, what, fn, again)   # noqa: E731
        local("añadir las fotos", lambda: _add_folder(s, src, n, log), lambda: _nothing_added(s))
        for vez in range(2):
            local("aplicar el preajuste", lambda: (_select_all(s), _apply_preset(s, log)))
            local("copiar el preajuste", lambda: _sync_all(s, n, log))
            k = local("exportar", lambda: _export(s, tmp, n, log, should_stop, progress), lambda: not _jpgs(tmp))
            if k < n:
                raise iw.UIError(f"Luminar exportó {k} de {n} fotos.")
            ok, same = _changed(src, tmp)
            if ok * 2 >= n:
                break
            # la mayoría salió igual: el ajuste no se pegó. 27-nov-2026 (evento «26 nov 2026»: se pegó solo en la foto
            # activa, 342 de 343 sin CINEMA, y era el último intento → el evento se paró). Se repite aquí UNA vez,
            # con Luminar como está (catálogo y fotos ya dentro), antes de gastar un intento entero del paso.
            if vez:
                raise iw.UIError(f"«{PRESET}» no se aplicó a {len(same)} de {n} fotos ({', '.join(same[:5])}).")
            log(f"   («{PRESET}» no se pegó en {len(same)} de {n} fotos: vuelvo a aplicarlo y exportar, sin cerrar "
                "Luminar)")
            shutil.rmtree(tmp, ignore_errors=True)
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


def _local(s, log: Log, should_stop, what: str, fn, again=lambda: True):
    """`fn()` hasta 3 veces dentro del paso. Solo se repite si se cortó por la ventana (otra se puso delante, un clic
    de la persona…), Luminar sigue abierto y `again()` dice que repetirla no duplica nada; si no, el error sube y
    el paso se reintenta entero como siempre."""
    from . import imagen_win as iw
    from . import winscreen as ws
    for k in range(3):
        try:
            return fn()
        except (iw.UIError, ws.FocusLost) as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            if msg == "Cancelado." or should_stop() or s.should_stop() or k == 2 or not s.main_window():
                raise
            try:
                ok = again()
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                raise
            log(f"   ({what}: {msg[:90]} · lo repito aquí, sin empezar Luminar de cero)")
            for w in ws.windows(exe=s.exe):          # un selector de carpetas a medias se cierra
                if any(x in w["title"].lower() for x in ("carpeta", "folder", "abrir", "examinar")):
                    try:
                        ws.close_window(w["hwnd"])
                    except Exception:  # noqa: BLE001
                        pass
            try:
                s.esc()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)


def _nothing_added(s) -> bool:
    """¿El catálogo sigue vacío? (entonces volver a añadir la carpeta no duplica nada)"""
    return bool(s.find("Aún no hay fotos", boxes=s.ocr()) or s.find("Aún no hay fotos", boxes=s.ocr_tiles(3)))


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
    comprueba que se ve lo siguiente (path[i+1] o `then`) y, si no, se vuelve a poner el ratón sobre ese elemento.
    Modo tranquilo (26-nov-2026): todo el recorrido cuenta como UNA acción (el teclado no se devuelve a mitad, que
    cerraría el menú) y el cursor se queda en el menú hasta el final (si volviera a su sitio tras cada clic, el
    submenú no se abriría); al acabar vuelve a donde lo tenía la persona."""
    from . import winscreen as ws
    ws.begin_action(s.should_stop)
    try:
        _menu_walk(s, path, then)
    finally:
        ws.end_action()
        if not getattr(s, "_hold", 0):        # (dentro de s.holding(): el cursor sigue en el menú hasta el final)
            ws.restore_cursor()


def _menu_walk(s, path, then: str) -> None:
    from . import imagen_win as iw

    def read(k: int):
        """Una vuelta a tamaño real y otra POR TROZOS ×3: en el monitor de 1080p (modo tranquilo) el menú de Luminar
        se veía abierto pero su letra pequeña no se leía a tamaño real (prueba de 5 fotos, 26-nov-2026)."""
        return s.ocr() if k % 2 == 0 else s.ocr_tiles(3)

    for k in range(8):                               # (con un catálogo grande, Luminar tarda en responder al abrir)
        s.front(force=True)                          # Luminar delante (otra ventana o Alt+Tab se llevan el clic)
        if k and (k % 2 == 0 or s.find("Buscar en sus fotos", boxes=s.ocr())):
            # 27-nov-2026 (prueba de 5 fotos): un Ctrl+F suelto abrió «Buscar en sus fotos» encima y el menú no se
            # abría (3 intentos perdidos). Esc cierra la búsqueda, un menú a medias o la vista de una foto.
            s.esc()
            time.sleep(0.6)
        if s.find("Archivo", exact=True, boxes=read(k), region=(0, 0.04, 0.08, 0.12)) or                 s.find("Archivo", exact=True, boxes=read(k + 1), region=(0, 0.04, 0.08, 0.12)):
            break
        s.click(s.rect[0] + 0.033 * s.rect[2], s.rect[1] + 0.033 * s.rect[3], restore=False)   # el logo
        t0, j = time.time(), 0
        while time.time() - t0 < 5 and not s.find("Archivo", exact=True, boxes=read(j),
                                                     region=(0, 0.04, 0.08, 0.12)):
            j += 1
            time.sleep(0.3)
    for i, item in enumerate(path):
        nxt = path[i + 1] if i + 1 < len(path) else then
        for _try in range(4):
            boxes = read(_try)
            hits = [b for b in s.find(item, exact=True, boxes=boxes) if b["y"] > s.rect[1] + 0.05 * s.rect[3]]
            if not hits and _try % 2 == 0:
                boxes = read(1)
                hits = [b for b in s.find(item, exact=True, boxes=boxes) if b["y"] > s.rect[1] + 0.05 * s.rect[3]]
            if not hits:
                break
            b = min(hits, key=lambda q: q["x"]) if not i else max(hits, key=lambda q: q["x"])
            s.click_box(b, after=0.9, restore=False)
            if not nxt or any(q["x"] > b["x"] + b["w"] and q["y"] > s.rect[1] + 0.05 * s.rect[3]
                              for bs in (s.ocr(), s.ocr_tiles(3)) for q in s.find(nxt, boxes=bs)):
                break                                # se abrió su submenú (lo siguiente está a su derecha, no
            #                                          el «Catálogo» de la barra de arriba)
        else:
            hits = []
        if not hits:
            iw.diag(s, f"luminar-menu-{item}")
            raise iw.UIError(f"No encuentro «{item}» en el menú de Luminar.")


def _name_typed(path: Path, boxes) -> bool:
    """¿Se lee en pantalla el principio del nombre del archivo? Sin tildes ni mayúsculas en los dos lados."""
    from . import imagen_win as iw

    def canon(t: str) -> str:
        # (26-nov-2026, prueba de 5 fotos: se escribió bien pero el OCR leyó «RawFIow» —I por l— y se daba por mal
        # escrito; las letras que el OCR confunde valen igual)
        t = iw.norm(t)
        for a, b in (("l", "i"), ("1", "i"), ("|", "i"), ("0", "o")):
            t = t.replace(a, b)
        return t.strip()
    want = canon(path.stem[:12])
    return bool(want) and any(want in canon(b["t"]) for b in boxes)


def _save_as(s, path: Path) -> None:
    """Diálogo «Guardar como» de Windows: la ruta completa en «Nombre de archivo» y Guardar."""
    from . import imagen_win as iw
    from . import winscreen as ws
    t0 = time.time()
    while time.time() - t0 < 30:
        w = s.window()
        if w and "guardar" in iw.norm(w["title"]):
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
            # se ve escrito: Guardar. 26-nov-2026 (revisión): las DOS partes con norm (sin tildes ni mayúsculas);
            # antes solo lo leído, y un catálogo «Boda José…» nunca «se veía escrito» y se reintentaba 30 s.
            if _name_typed(path, s.ocr()):
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
    # (26-nov-2026, prueba de 5 fotos: el submenú se abre al pasar el ratón; si el cursor volvía a su sitio al acabar
    # _menu, se cerraba antes de pulsar «Nuevo…». Todo el recorrido, con el último clic, va junto)
    with s.holding():
        _menu(s, "Archivo", "Catálogo", then="Nuevo")
        s.tap("Nuevo...", "Nuevo…", timeout=10)
    _save_as(s, path)
    t0 = time.time()
    while time.time() - t0 < 60:                     # el catálogo NUEVO está vacío: «Aún no hay fotos»
        if s.should_stop():
            raise iw.UIError("Cancelado.")
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


def _apply_preset(s, log: Log, name: Optional[str] = None) -> None:
    """Pestaña Preajustes (barra derecha) → Mis Preajustes → el preajuste (con las fotos seleccionadas)."""
    from . import imagen_win as iw
    name = name or PRESET
    right = (0.8, 0.05, 1.0, 0.95)
    for _ in range(4):
        boxes = s.ocr()
        if any(_is_item(b["t"], name) for b in boxes if s.inside(b, right)):
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
    item = [b for b in s.ocr() if _is_item(b["t"], name) and s.inside(b, right)]
    if not item:
        iw.diag(s, "luminar-preajuste")
        raise iw.UIError(f"No encuentro el preajuste «{name}» en Mis preajustes de Luminar.")
    log(f"Luminar: aplicando «{name}»…")
    s.click_box(item[0], after=3.0)


def _acciones(s, item: str) -> None:
    """Botón «Acciones» (arriba) → Ajustes → `item` («Copiar Ajustes», «Pegar Ajustes»…). Modo tranquilo: como
    _menu, todo es UNA acción y el cursor se queda en el menú hasta el final."""
    from . import winscreen as ws
    ws.begin_action(s.should_stop)
    try:
        _acciones_walk(s, item)
    finally:
        ws.end_action()
        ws.restore_cursor()


def _acciones_walk(s, item: str) -> None:
    from . import imagen_win as iw
    for _ in range(3):
        s.front(force=True)
        s.tap("Acciones", timeout=15, region=(0.75, 0, 0.9, 0.06), after=1.0)
        for _h in range(3):
            aj = [b for b in s.find("Ajustes", exact=True) if b["y"] > s.rect[1] + 0.06 * s.rect[3]]
            if not aj:
                break
            s.click_box(min(aj, key=lambda b: b["y"]), after=0.9, restore=False)   # (pasar por encima abre su submenú)
            it = s.find(item, exact=True)
            if it:
                s.click_box(it[0], after=1.2, restore=False)
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
    # 25-nov-2026 (revisión): salía a la PRIMERA lectura sin «pegando» (el OCR la falla a veces, o Luminar aún no la
    # dibujó): ahora hacen falta 3 lecturas seguidas sin ella (alternando tamaño), y se mira si pulsaron «Parar».
    quiet, j = 0, 0
    while time.time() - t0 < max(60, n * 1.5) and quiet < 3:     # mientras se ve «Pegando ajustes…»
        if s.should_stop():
            raise iw.UIError("Cancelado.")
        busy = any("pegando" in iw.norm(b["t"]) for b in s.ocr(scale=2 if j % 2 else 1))
        j += 1
        quiet = 0 if busy else quiet + 1
        time.sleep(1)
    time.sleep(2)


def _changed(src: Path, out: Path, min_diff: float = 2.0) -> Tuple[int, List[str]]:
    """¿Cada foto exportada cambió respecto a su original de Retouch4me? (diferencia media de píxeles en pequeño; sin
    el preajuste, volver a guardar el JPEG da ≈0,8-1,1; con «CINEMA AJUSTE», ≈5). (cuántas cambiaron, las que no)."""
    import cv2
    import numpy as np
    from concurrent.futures.thread import ThreadPoolExecutor

    def diff(p: Path) -> float:
        q = Path(out) / p.name
        try:
            a = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
            b = cv2.imdecode(np.fromfile(str(q), np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
            b = cv2.resize(b, (a.shape[1], a.shape[0]))
            return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
        except Exception:  # noqa: BLE001
            return 0.0
    fotos = _jpgs(src)
    with ThreadPoolExecutor(max(2, min(8, (os.cpu_count() or 4) // 2))) as ex:   # (4.26) 411 fotos: 68 s → unos 12 s
        ds = list(ex.map(diff, fotos))
    same = [p.name for p, d in zip(fotos, ds) if d < min_diff]
    return len(fotos) - len(same), same


def _settled(files: List[Path], most: float = 3.0) -> None:
    """Espera a que los archivos más nuevos dejen de crecer (dos lecturas iguales seguidas), como mucho `most` s."""
    def mtime(q):
        try:
            return q.stat().st_mtime
        except OSError:
            return 0.0

    def sizes():
        out = []
        for p in sorted(files, key=mtime)[-4:]:
            try:
                out.append(p.stat().st_size)
            except OSError:
                out.append(-1)
        return out
    t0, last = time.time(), None
    while time.time() - t0 < most:
        cur = sizes()
        if cur == last and -1 not in cur and 0 not in cur:
            return
        last = cur
        time.sleep(0.4)


def _count(text: str, n: int) -> int:
    """Cuántas fotos dice «N seleccionadas». El OCR a veces parte el número («41 1») o se come una cifra repetida
    («411» → «41»): si lo leído es `n` con UNA cifra menos, cuenta como `n` (la exportación comprueba luego que
    salgan las `n`)."""
    import re
    digits = "".join(re.findall(r"\d", text.split("eleccionad")[0]))
    if not digits:
        return 0
    k = int(digits)
    full = str(n)
    if k < n and len(full) >= 3 and len(digits) == len(full) - 1 and any(full[:i] + full[i + 1:] == digits for i in range(len(full))):
        return n
    return k


def _export(s, out: Path, n: int, log: Log, should_stop=lambda: False, progress=lambda f: None) -> int:
    """Exportar → «JPEG · 100 % de calidad» → carpeta `out`. Espera a que estén las `n` fotos enteras."""
    import re
    from . import imagen_win as iw
    out.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in _jpgs(out)}
    for _ in range(3):
        _select_all(s)
        s.tap("Exportar", exact=True, timeout=15, region=(0.85, 0, 0.97, 0.06), after=1.5)
        k, t1, j, seen = 0, time.time(), 0, ""
        while time.time() - t1 < max(20, n * 0.15):  # tras pegar a cientos de fotos el menú sale vacío o gris un rato
            if should_stop() or s.should_stop():
                raise iw.UIError("Cancelado.")
            boxes = s.ocr(scale=2 if j % 2 else 1)
            j += 1
            for b in boxes:
                if "seleccionad" in iw.norm(b["t"]):
                    seen = b["t"]
                    k = max(k, _count(b["t"], n))
            if k >= n:
                break
            time.sleep(1)
        if k >= n:
            break
        log(f"Luminar: el menú Exportar muestra {k} de {n} seleccionadas («{seen}»); reintento…")
        s.esc()
        time.sleep(1)
    else:
        iw.diag(s, "luminar-exportar")
        raise iw.UIError(f"Luminar no seleccionó las {n} fotos para exportar.")
    # 25-nov-2026 (revisión): era UNA lectura a tamaño real exigiendo «100» literal; el OCR parte o se come cifras en
    # este menú («411» → «41»). Se prueba primero lo ya leído y luego ~10 s alternando tamaño. Nunca otra calidad.
    is_q = lambda b: "calidad" in iw.norm(b["t"]) and "100" in re.sub(r"\s", "", b["t"])     # noqa: E731
    q = [b for b in boxes if is_q(b)]
    t_q, j = time.time(), 0
    while not q and time.time() - t_q < 10:
        if should_stop() or s.should_stop():
            raise iw.UIError("Cancelado.")
        q = [b for b in s.ocr(scale=2 if j % 2 else 1) if is_q(b)]
        j += 1
        if not q:
            time.sleep(0.5)
    if not q:
        iw.diag(s, "luminar-exportar")
        raise iw.UIError("No encuentro «JPEG · 100 % de calidad» en Exportar de Luminar.")
    log(f"Luminar: exportando {n:,} JPEG (100 %) a «{out.name}»…")
    s.click_box(min(q, key=lambda b: b["y"]), after=1.5)
    iw._pick_folder(s, out)
    t0, last, last_t = time.time(), -1, time.time()
    first_t = t0
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
                if k == 1:
                    first_t = time.time()
                elif k >= 3:                         # lo que falta con el ritmo REAL de esta exportación (26-nov-2026:
                    try:                             # se quedaba la estimación de antes de empezar: 8:10 → tardó 9:26)
                        from . import core, live
                        per = (time.time() - first_t) / (k - 1)
                        live.set_eta(core.STEP_LUM, per * (n - k))
                        live.set("lum", f"{k:,} de {n:,} fotos  ·  {per:.1f} s por foto  ·  CINEMA")
                    except Exception:  # noqa: BLE001
                        pass
        if k >= n:
            _settled(fs)                             # la última termina de escribirse (antes 3 s fijos)
            return len(_jpgs(out))
        if time.time() - last_t > max(180, 30 + n * 3):
            iw.diag(s, "luminar-exportando")
            raise iw.UIError(f"Luminar dejó de exportar ({k} de {n}).")
        time.sleep(1.5)


def _add_folder(s, src: Path, n: int, log: Log) -> None:
    from . import imagen_win as iw
    log(f"Luminar: añadiendo las {n:,} fotos de «{src.name}»…")
    from . import winscreen as ws
    picker = lambda: [w for w in ws.windows(exe=s.exe)                                 # noqa: E731
                      if any(k in w["title"].lower() for k in ("carpeta", "folder", "abrir", "examinar"))]
    for intento in range(3):
        # 25-nov-2026 (revisión): el atajo se mandaba UNA vez; si Luminar aún cambiaba de catálogo o no tenía el
        # foco se perdía, y tras 30 s se repetía TODO el paso. Ahora, si a los 8 s no hay selector de carpetas, se
        # manda otra vez, solo con Luminar delante y sin ningún selector abierto (dentro de uno, Ctrl+Mayús+N
        # crearía una «Nueva carpeta»). 26-nov-2026 (revisión): si Luminar no quedaba delante se salía del bucle SIN
        # mandar el atajo; ahora se vuelve a traer delante y se intenta otra vez.
        if picker():
            break
        try:
            s.front(force=True)                      # (26-nov-2026: front da UIError si no queda delante; aquí
        except iw.UIError:                           # ya hay sus propios reintentos)
            pass
        if (ws.foreground().get("exe") or "").lower() != s.exe.lower():
            time.sleep(1.0)
            continue
        s.keys("ctrl+shift+n")                       # Archivo → Añadir carpeta con imágenes…
        t0 = time.time()
        while not picker() and time.time() - t0 < 8:
            if s.should_stop():
                raise iw.UIError("Cancelado.")
            time.sleep(0.4)
        if picker():
            break
        if not intento:
            log("   (no se abrió el selector de carpetas: vuelvo a pedirlo)")
    time.sleep(0.5)
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

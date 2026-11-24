"""Paso 4 de RawFlow: retoque con Retouch4me Arams (preajuste CINEMA) y reemplazo de «Fotos finales».

Secuencia (la del video):
  Arams → Inicio → Crear proyecto (nombre = evento) → Añadir fotos → <evento>/Fotos finales
  → seleccionar todas → Retoque (varita) → preajuste CINEMA → Exportar
  (Disco duro · carpeta de archivos originales · subcarpeta «export» · JPEG 100 %) → Exportar
  → al terminar, cada «X-retouched.jpg» reemplaza a «X.jpg» en «Fotos finales» y se borra «export».

Igual que imagen_ui: encuentra los botones leyendo la pantalla (OCR) y necesita la pantalla
desbloqueada y los permisos de Accesibilidad y Grabación de pantalla. Trabaja en local.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .core import files_settled
from .imagen_ui import Screen, UIError, _key, check_permissions, norm

APP = "Retouch4me Arams"
APP_PATH = Path("/Applications/Retouch4me Arams.app")
EXPORT_SUB = "export"
PRESET = "CINEMA"


def _window(s: Screen):
    out = subprocess.run(["osascript", "-e",
                          f'tell application "System Events" to tell process "{APP}" to '
                          'get {position, size} of window 1'], capture_output=True, text=True).stdout
    n = [int(x) for x in out.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]
    return n if len(n) == 4 else [0, 25, 1280, 807]


def _home(s: Screen):
    x, y, w, h = _window(s)
    if not s.find("Repetir recorrido") and not s.find("Crear proyecto", exact=True):
        s.click(x + w - 109, y + h - 20)        # icono de casa (abajo a la derecha)
        time.sleep(2.5)
    s.wait("Repetir recorrido", "Crear proyecto", timeout=20)
    # con muchos proyectos, la tarjeta «Crear proyecto» queda abajo: desplazarse hasta verla
    # «Crear proyecto» es siempre la última tarjeta: saltar directo al final
    for k in range(6):
        if s.find("Crear proyecto", exact=True, region=(0, 80, w - 300, h - 60)):
            return
        s.run("scroll", round(x + (w - 300) / 2), round(y + 250), -10, 6 if k == 0 else 3)
        time.sleep(0.3)
    raise UIError("No encuentro la tarjeta «Crear proyecto» en Retouch4me.")


def _jpgs(folder: Path):
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg") and p.is_file())


def retouch(src: Path, project: str, log, should_stop=lambda: False, preset: str = PRESET,
            dest: Optional[Path] = None, progress=lambda f: None) -> int:
    """Retoca los JPEG de `src` con el preajuste y deja el resultado en `dest`
    (con el mismo nombre: DSC01592.jpg). Sin `dest`, reemplaza los de `src`."""
    dest = Path(dest) if dest else src
    # Carpeta de trabajo con SOLO los JPEG a retocar (así Retouch4me nunca importa DNG ni subcarpetas)
    import shutil
    stage_root = Path.home() / "Library/Application Support/RawFlow/retouch"
    shutil.rmtree(stage_root, ignore_errors=True)
    finals = stage_root / "".join(c for c in project if c not in '/:\\') or stage_root / "fotos"
    finals.mkdir(parents=True, exist_ok=True)
    # reanudar sin pérdidas: las que ya están retocadas en el destino no se vuelven a hacer
    already = ({p.name for p in dest.glob("*.jpg") if p.stat().st_size > 100_000}
               if dest.exists() and dest != Path(src) else set())
    for p in _jpgs(Path(src)):
        if p.name in already:
            continue
        try:
            os.link(p, finals / p.name)               # enlace: instantáneo y sin ocupar espacio
        except OSError:
            shutil.copy2(p, finals / p.name)
    if not APP_PATH.exists():
        raise UIError("No encuentro Retouch4me Arams en Aplicaciones.")
    miss = check_permissions()
    if miss:
        raise UIError(f"RawFlow necesita permiso de {miss} (botón «Permisos»).")
    photos = _jpgs(finals)
    if not photos:
        if already:
            log(f"Retouch4me: las {len(already)} fotos ya estaban retocadas.")
            return len(already)
        raise UIError(f"No hay JPEG en «{finals}».")
    if already:
        log(f"Retouch4me: {len(already)} ya estaban hechas; sigo con las {len(photos)} que faltan.")
    exp = finals / EXPORT_SUB
    if exp.exists():
        for p in exp.iterdir():
            p.unlink()
    s = Screen(log, should_stop)
    from .imagen_ui import dismiss_crash_dialogs
    dismiss_crash_dialogs()
    subprocess.run(["open", "-a", str(APP_PATH)], check=False)
    s.activate(APP)
    s.wait("Añadir fotos", "Crear proyecto", "Repetir recorrido", timeout=90)
    s.keys("53")

    # nombre único (se borra al terminar): nunca choca con uno que ya exista y no hay que reintentar
    project = f"{project} RF{time.strftime('%H%M%S')}"
    log("Retouch4me: creando proyecto…")
    _home(s)
    cr = None
    for intento in range(4):                     # la tarjeta a veces no responde al primer clic
        cards = s.find("Crear proyecto", exact=True)
        if not cards:
            s.keys("53")
            _home(s)
            cards = s.find("Crear proyecto", exact=True)
        card = cards[0]
        dy = (140, 140, 100, 60)[intento]        # el «+» está 140 px encima del rótulo
        s.click(card["x"] + card["w"] / 2, card["y"] - dy)
        try:
            s.wait("Crear", exact=True, timeout=2.5)
            cr = max(s.find("Crear", exact=True), key=lambda b: b["y"])
            break
        except UIError:
            s.keys("53")
            time.sleep(1)
    if not cr:
        raise UIError("No se abrió el diálogo «Crear proyecto» de Retouch4me.")
    cx, cy = cr["x"] + cr["w"] / 2, cr["y"] + cr["h"] / 2
    for k in range(1, 8):                        # si el nombre ya existe, Arams no crea: probar «Nombre 2»…
        s.click(cx - 143, cy - 142)              # campo «Nombre»
        time.sleep(0.4)
        s.keys("a", "command")
        s.type(project if k == 1 else f"{project} {k}")
        time.sleep(0.4)
        s.click(cx, cy)
        try:
            s.wait("Añadir fotos", timeout=4)
            break
        except UIError:
            pass
    else:
        raise UIError("Retouch4me no creó el proyecto.")
    made = project if k == 1 else f"{project} {k}"

    log("Retouch4me: añadiendo las fotos finales…")
    s.tap("Añadir fotos", timeout=20, after=1.2)
    s.goto_folder(finals)
    s.keys("36")                                 # Abrir: importa la carpeta (solo tiene los JPEG)
    t0 = time.time()
    want = f"{len(photos)} fotos"
    while time.time() - t0 < 120:
        if s.find(want):
            break
        time.sleep(2)
    else:
        got = [b["t"] for b in s.ocr() if "fotos /" in b["t"]]
        raise UIError(f"Retouch4me no cargó las {len(photos)} fotos ({got[0] if got else 'sin fotos'}).")
    time.sleep(2)

    thumbs = [b for b in s.ocr() if norm(b["t"]).startswith(norm(photos[0].stem)[:6]) and b["y"] > 150]
    if not thumbs:
        raise UIError("No veo las miniaturas en Arams.")
    t = min(thumbs, key=lambda b: (b["y"], b["x"]))
    s.click(t["x"] + 40, t["y"] + 60)
    time.sleep(0.5)
    s.keys("a", "command")
    time.sleep(1)
    if not s.find(f"{len(photos)} seleccionada"):
        raise UIError("No pude seleccionar todas las fotos en Arams.")

    log(f"Retouch4me: aplicando el preajuste {preset}…")
    x, y, w, h = _window(s)
    for _ in range(2):
        if s.find("Restablecer configuraciones"):
            break
        s.click(x + w - 25, y + 106)             # varita «Retoque» de la barra derecha
        time.sleep(2)
    if not s.find("Restablecer configuraciones"):
        raise UIError("No pude abrir el panel de Retoque de Arams.")
    pr = s.find(preset, exact=True, region=(850, 100, 1280, 800))
    if not pr:
        raise UIError(f"No encuentro el preajuste «{preset}» en Arams.")
    s.click(pr[0]["x"] + 60, pr[0]["y"] + pr[0]["h"] / 2)
    time.sleep(2)

    s.tap("Exportar", exact=True, region=(900, 680, 1280, 820), after=2.5)
    for need in ("Disco duro", "Carpeta de archivos originales", EXPORT_SUB):
        if not s.find(need):
            raise UIError(f"La exportación de Arams no está como en tu video (falta «{need}»).")
    log(f"Retouch4me: retocando y exportando {len(photos)} fotos (≈1 min por foto)…")
    s.tap("Exportar", exact=True, region=(900, 680, 1280, 820), after=1)

    # vigilante: cada foto terminada pasa al destino al momento (si algo falla no se pierde nada);
    # si Arams se cierra o deja de sacar fotos mucho más de lo normal, se corta para reintentar
    t0 = last = time.time()
    moved = 0
    dest.mkdir(parents=True, exist_ok=True)
    while True:
        if should_stop():
            raise UIError("Cancelado.")
        for f in (list(exp.glob("*-retouched*.jpg")) if exp.exists() else []):
            orig = f.name.split("-retouched")[0] + ".jpg"
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size > 100_000 and time.time() - st.st_mtime > 1.5 and files_settled([f], 0.4):
                os.replace(f, dest / orig)
                moved += 1
                if moved == 1:
                    first = time.time()
                last = time.time()
                rest = len(photos) - moved
                if moved >= 2:                       # ritmo real (sin contar el arranque del primer retoque)
                    per = (last - first) / (moved - 1)
                else:
                    per = last - t0
                log(f"Retouch4me: {moved + len(already)} de {len(photos) + len(already)} retocadas  ·  "
                    f"{per:.0f} s por foto" + (f"  ·  quedan {int(per * rest) // 60}:{int(per * rest) % 60:02d}"
                                               if rest else ""))
        progress(moved / len(photos))
        if moved >= len(photos):
            break
        per = (last - t0) / moved if moved else 0
        limit = max(4 * 60, per * 5) if moved else 10 * 60
        if time.time() - last > limit:
            raise UIError(f"Arams dejó de avanzar ({moved} de {len(photos)}): reintento con las que faltan.")
        if subprocess.run(["pgrep", "-x", APP], capture_output=True).returncode != 0:
            raise UIError(f"Arams se cerró ({moved} de {len(photos)} hechas): reintento con las que faltan.")
        time.sleep(1)
    n = moved + len(already)
    for p in exp.iterdir():
        p.unlink()
    exp.rmdir()
    shutil.rmtree(stage_root, ignore_errors=True)
    log(f"✓  Retouch4me: {n} fotos retocadas con {preset} en «{dest.name}».")
    try:                                         # el proyecto de trabajo ya no sirve: se borra para que la
        delete_project(s, made)                  # lista de Arams no crezca (cada evento iría más lento)
    except Exception as e:  # noqa: BLE001
        log(f"   (no pude borrar el proyecto «{made}» de Arams: {e})")
    return n


def delete_project(s: Screen, name: str):
    """Borra de Arams el proyecto «name» (solo ese: el diálogo de Arams dice el nombre y se comprueba)."""
    s.keys("53")
    _home(s)
    x, y, w, h = _window(s)
    s.run("scroll", round(x + (w - 300) / 2), round(y + 250), 10, 8)    # desde arriba del todo
    time.sleep(0.4)
    at_end = False
    for _ in range(40):
        boxes = s.ocr()
        card = [b for b in boxes if _key(b["t"]) == _key(name) and b["x"] < x + w - 300]
        if card:
            c = card[0]
            d = None
            for _ in range(3):                    # el primer clic a veces solo activa la ventana
                s.click(c["x"] + 226, c["y"] + c["h"] / 2 + 6)  # papelera de esa tarjeta
                try:
                    d = s.wait("eliminar el proyecto", timeout=2.5)
                    break
                except UIError:
                    pass
            if d is None:
                raise UIError("no se abrió el diálogo de borrar")
            txt = " ".join(b["t"] for b in s.ocr() if abs(b["y"] - d["y"]) < 45)
            if _key(name) not in _key(txt):
                s.tap("Cancelar", exact=True, timeout=5)
                raise UIError("el diálogo de borrar no era de ese proyecto")
            s.tap("Aceptar", exact=True, timeout=5, after=0.8)
            s.log(f"Retouch4me: proyecto de trabajo «{name}» borrado de Arams.")
            return
        if s.find("Crear proyecto", exact=True, boxes=boxes):
            if at_end:                                           # ya miró la fila de encima: no está
                break
            at_end = True                                        # al final, la última fila de nombres
            s.run("scroll", round(x + (w - 300) / 2), round(y + 250), 2, 1)   # queda arriba, tapada
        else:
            s.run("scroll", round(x + (w - 300) / 2), round(y + 250), -3, 2)
        time.sleep(0.4)
    s.log(f"   (no encontré el proyecto «{name}» en Arams para borrarlo)")

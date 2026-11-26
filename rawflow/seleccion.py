"""Selección IA: aparta las tomas malas y las REPETIDAS antes de DxO, sin internet. NUNCA borra.

Cada foto se MIDE con el motor del equipo (ver engine()): en el Mac, la IA de Apple (Vision, ayudante «rfui cull»); en
Windows, OpenCV con modelos que van dentro de RawFlow (rawflow/seleccion_win.py), que da las mismas medidas en la misma
escala. Lo que se aparta lo DECIDE este módulo, con las mismas reglas y umbrales en los dos sistemas. (En Windows no
hay nota estética ni etiquetas de escena: «al piso» y la «fallida» por nota no se aplican; la «fallida» por nitidez sí.)

Es el paso 2, justo después de «Mover»: analiza la vista previa de cada RAW (≈0,07 s por foto) y, dentro de
«1 Original», deja las malas en «Fotos malas» (con el motivo de cada una en «motivos.txt») y las buenas en
«Fotos buenas». DxO, Imagen (que cobra por foto), Bridge y Retouch4me SOLO trabajan con las buenas.
Para recuperar una: pasarla a «Fotos buenas» y «Seguir» (RawFlow la procesa y no la vuelve a apartar).

Qué se aparta (medido foto a foto con JUTIAPA y 20 nov; juzgar cada foto sola tira fotos BUENAS de boda —
detalles de vestido/anillos/ramo, noches con flash, gente mirando abajo, besos y maquillaje con los ojos cerrados):
  · REPETIDAS: la misma pose disparada otra vez (≤5 s entre tomas y parecido de Vision ≤0,16: medido, 0,18 o más ya
    es otro encuadre, gesto o momento). De cada grupo se queda la mejor: la que tiene menos gente con los ojos
    cerrados (y luego a medio cerrar), comparando a cada persona consigo misma en las otras tomas; luego la de mejor
    calidad de cara (Vision la da justo para elegir la mejor de una ráfaga), la más nítida y la más estética. Las
    demás SOLO se apartan si la que se queda es igual o mejor para TODAS las personas (better_in): si una tiene a
    alguien con los ojos más abiertos, la cara más nítida o mejor, o se le ve la cara que en la otra se tapa, se queda
    también. Un horquillado (HDR: misma toma con distinta exposición) no cuenta como repetida;
  · cada cara se mide en la vista previa GRANDE del RAW (rfui), y si Vision no ve en una toma una cara que en la de al
    lado sí, se vuelve a buscar en su sitio (rescue: «rfui caras»); si no aparece, está tapada o girada;
  · ojos cerrados / movida / mal expuesta: solo comparando con la toma de AL LADO casi igual, y en la MISMA persona
    (misma cara, mismo sitio): si en esa vecina está bien, se aparta la mala (nunca las dos);
  · la exposición se mide en las caras, no en el cuadro (las noches con flash tienen el fondo negro);
  · mirar abajo (cabeza inclinada) o de perfil no es un parpadeo;
  · tomas accidentales (negra, blanca, piso, pared, techo o cielo sin nadie ni ningún detalle; fallidas sin ninguna
    cara a las que la IA da muy mala nota, o sin ninguna cara y casi sin nitidez frente al resto del evento —esta
    también en Windows—) se apartan aunque estén solas; las caras movidas, quemadas, oscuras o con los ojos cerrados
    se apartan si en ese MOMENTO queda otra foto (si no, se queda la menos mala: ver más abajo);
  · si por comparación salieran más de 3 y del 15 % del evento, algo no cuadra: esas no se apartan;
  · RESPALDO (26-nov-2026): de 3 o más tomas casi iguales se quedan las 2 mejores (de 2, la mejor);
  · NINGÚN MOMENTO SIN FOTO (26-nov-2026): si todas las tomas de un momento (seguidas, ≤10 s) se apartaban, la menos
    mala se queda (ver keep_one_per_moment).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .core import BAD_SUBFOLDER as DIR_NAME, GOOD_SUBFOLDER as GOOD_NAME   # «Fotos malas» / «Fotos buenas»
REASONS_FILE = "motivos.txt"

BURST_GAP = 3.0          # s entre dos tomas de la misma ráfaga (hora de la cámara, con centésimas)
BURST_DIST = 0.33        # parecido (huella de Vision): misma ráfaga ≈0,18-0,25; momentos distintos ≈0,42-0,49
EYE_CLOSED = 0.12        # alto/ancho del ojo: abiertos ≈0,27; cerrados < 0,12
EYE_OPEN = 0.18
LOOK_DOWN = 0.25         # inclinación de la cabeza (rad, + = hacia abajo): mirando abajo no es un parpadeo
PROFILE = 0.6            # giro (rad): de perfil, los ojos no se miden bien
EYE_PX = 60              # ancho mínimo de la cara medida (px) para fiarse de sus ojos
Q_CLEAR = 0.20           # calidad de cara de Vision: diferencia CLARA entre dos tomas de la misma persona (ruido
                         # medido entre repetidas casi iguales: mediana 0,03, p95 0,12-0,15)
Q_CLEAR_MAIN = 0.15      # …para las caras grandes y bien medidas (≥150 px): ahí el ruido es menor
EYES_CLEAR = 1.35        # ojos «más abiertos»: 1,35 veces o más (y abiertos en una, no en la otra)
SHARP_CLEAR = 0.5        # nitidez de una cara: la mitad o menos = claramente más movida (ruido: p10 0,4-0,7)
MAX_SHARE = 0.15         # por comparación con la de al lado, más que esto = algo no cuadra: esas no se apartan

DUP_GAP = 5.0            # s entre una toma y la siguiente de la misma pose (medido: «otra más» en fotos de grupo ≤4 s)
DUP_DIST = 0.16          # parecido máximo entre TODAS las tomas de un grupo de repetidas (0,18+ = otra foto)
DUP_STRICT = 0.10        # si con 0,16 saliera repetida más de la mitad del evento, algo no cuadra: solo las clavadas
DUP_MAX_SHARE = 0.5
DUP_EV = 0.7             # misma toma con ≥0,7 EV de diferencia = horquillado (HDR), no repetida
BACKUP_FROM = 3          # de un grupo de 3 o más casi iguales se quedan las 2 mejores (una de respaldo): 26-nov-2026,
#                          el usuario: «siempre saca 2 buenas para que me quede respaldo» (de 2, se queda 1)
MOMENT_GAP = 10.0        # s entre dos tomas seguidas del MISMO momento (familia en la iglesia, brindis…): ningún
#                          momento se queda sin foto; si todas salen malas, se queda la menos mala
DUP_BACK = 4             # rfui da el parecido con las 4 tomas anteriores: un grupo abarca como mucho 5 seguidas
FAILED_AEST = -0.2       # sin ninguna cara y con nota de la IA por debajo de esto: toma fallida (medido: OSZ_0001
                         # negra -0,91, baile movido -0,50, disparo al techo -0,27; la peor buena, la banda, -0,04)
FACE_BLOWN = 245         # luz media de las caras: blancas del todo
FACE_BLACK = 15          # …negras del todo (y el cuadro también oscuro: no saltó el flash)
BLUR_REL = 0.03          # sin ninguna cara y con la nitidez por debajo del 3 % de la mediana del evento: desenfocada
BLUR_ABS = 20            # o movida del todo (y además nitidez < 20). Medido (24-nov-2026, sin nota estética en Windows):
                         # boda 14 nov las 7 fallidas ≤0,02; la buena sin caras más blanda de 5 eventos ≥0,059
SOFT_MAX_SHARE = 0.05   # las reglas «a solas» más finas (fallida, caras blancas/negras): si saltan en más del 5 %,
                         # algo no cuadra con esa cámara y no se aplican

# Toma al piso / al monte en Windows (sin etiquetas de escena): «igual» = parte de la foto (rejilla 4×4) del mismo color
# que el resto; «verde» = parte de la foto de hierba. Medido en el evento 25-nov-2026: las 8 tomas al monte que pasaron
# como buenas tenían igual 0,69-0,94 y verde ≥ 0,96; las buenas sin caras (jardín con árboles, caballos, cortejo de
# espaldas, de cerca, movida) igual ≤ 0,31.
GROUND_SAME = 0.6
GROUND_GREEN = 0.6
FLOOR_WALL = {"floor", "wall", "ceiling", "tile", "carpet", "concrete", "ground", "asphalt", "rug", "wood_processed",
              "brick", "pavement", "sidewalk", "linoleum", "textile", "fabric", "blur", "dark", "night_sky",
              "sky", "cloudy", "clouds", "overcast"}
DETAIL = {"jewelry", "ring", "flower", "bouquet", "flower_arrangement", "cake", "wedding_dress", "dress", "shoes",
          "footwear", "decoration", "food", "baked_goods", "dessert", "candle", "document", "text", "sign", "art",
          "people", "adult", "child", "baby", "wedding", "bride", "ceremony", "celebration", "animal", "dog", "cat",
          "car", "vehicle", "table", "furniture", "glass", "drink", "invitation", "card", "lights", "string_lights"}


# ------------------------------------------------------------------------------------------------- análisis
def engine() -> str:
    """Con qué se miden las fotos en este equipo: «mac» (Vision, por rfui), «win» (OpenCV, seleccion_win) o «» (no se
    puede: la Selección IA queda apagada y pasan todas las fotos)."""
    from . import core
    if core.SYSTEM == "Darwin":
        return "mac"
    if core.SYSTEM == "Windows":
        try:
            from . import seleccion_win
            return "win" if seleccion_win.available()[0] else ""
        except Exception:  # noqa: BLE001
            return ""
    return ""


def analyze(paths: List[str], rfui: str, log: Callable[[str], None] = lambda m: None,
            should_stop: Callable[[], bool] = lambda: False,
            progress: Callable[[float], None] = lambda f: None) -> List[dict]:
    """Una fila por foto (en el orden dado: el de la toma), con lo que ve la IA del Mac (ver «rfui cull»)."""
    rows: List[dict] = []
    if not paths:
        return rows
    p = subprocess.Popen([rfui, "cull"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace")
    try:
        p.stdin.write("\n".join(paths) + "\n")
        p.stdin.close()
        t0 = time.monotonic()
        for line in p.stdout:
            if should_stop():
                p.kill()
                raise RuntimeError("Cancelado.")
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
            k = len(rows)
            progress(k / len(paths))
            if k % 25 == 0 or k == len(paths):
                el = time.monotonic() - t0
                log(f"Selección IA: {k} de {len(paths)} fotos analizadas"
                    + (f"  ·  quedan {int((len(paths) - k) * el / k)} s" if k < len(paths) else ""))
        p.wait(30)
    finally:
        if p.poll() is None:
            p.kill()
    if paths and not rows:
        raise RuntimeError("la IA del Mac no respondió (¿ayudante rfui antiguo?)")
    return rows


# ------------------------------------------------------------------------------------------------- decisión
def _area(f: dict) -> float:
    return float(f.get("w", 0)) * float(f.get("h", 0))


def _main_faces(r: dict) -> List[dict]:
    fs = [f for f in r.get("caras", []) if _area(f) >= 0.0025]
    if not fs:
        return []
    big = max(_area(f) for f in fs)
    return [f for f in fs if _area(f) >= 0.45 * big]


def _measurable(f: dict) -> bool:
    """¿Se pueden juzgar sus ojos? De frente, sin mirar abajo y con la cara medida a ≥60 px (en la vista grande)."""
    return (f.get("ojoI", -1) >= 0 and f.get("ojoD", -1) >= 0 and abs(f.get("yaw", 0.0)) <= PROFILE
            and f.get("pitch", 0.0) <= LOOK_DOWN and f.get("px", EYE_PX) >= EYE_PX)


def _blink(f: dict) -> bool:
    return _measurable(f) and f["ojoI"] < EYE_CLOSED and f["ojoD"] < EYE_CLOSED


def _open(f: dict) -> bool:
    return _measurable(f) and f["ojoI"] >= EYE_OPEN and f["ojoD"] >= EYE_OPEN


def _sharp(r: dict) -> float:
    fs = _main_faces(r)
    vals = [f.get("nit", -1) for f in fs if f.get("nit", -1) > 0]
    return max(vals) if vals else float(r.get("nit", 0) or 0)


def _face_lum(r: dict) -> Optional[float]:
    vals = [f.get("lum", -1) for f in _main_faces(r) if f.get("lum", -1) >= 0]
    return sum(vals) / len(vals) if vals else None


def _labels(r: dict) -> set:
    return {str(x[0]).lower() for x in r.get("escena", []) if isinstance(x, (list, tuple)) and x}


def _accidental(r: dict) -> str:
    """Tomas que no sirven aunque estén solas, sin nadie y sin ningún detalle (siempre se apartan)."""
    if r.get("caras") or r.get("personas", 0):
        return ""
    lum, negro, quem = float(r.get("lum", 128)), float(r.get("negro", 0)), float(r.get("quemado", 0))
    if lum < 10 and negro > 90:
        return "toma negra (tapa puesta, sin flash o disparo sin querer)"
    if quem > 85:
        return "toma blanca (quemada)"
    labs = _labels(r)
    if (float(r.get("desv", 99)) < 20 and float(r.get("estetica", 1)) < 0.3 and labs & FLOOR_WALL
            and not labs & DETAIL):
        return "toma al piso, a la pared, al techo o al cielo (sin nadie ni ningún detalle)"
    same = r.get("igual")                                   # Windows: sin etiquetas, por la textura de toda la foto
    if same is not None and float(same) >= GROUND_SAME and (
            float(r.get("verde", 0)) >= GROUND_GREEN or float(r.get("desv", 99)) < 20):
        return "toma al piso, al monte o a la pared (sin nadie: toda la foto es el mismo suelo)"
    return ""


def _soft_bad(r: dict, nit_med: float = 0.0) -> str:
    """Tomas malas a solas, por reglas más finas (con tope: ver SOFT_MAX_SHARE). nit_med: nitidez mediana del evento."""
    est = r.get("estetica")
    if not r.get("caras") and est is not None and float(est) < FAILED_AEST:
        return "toma fallida: movida o sin un sujeto claro (la IA del Mac le da muy mala nota)"
    nit = float(r.get("nit", -1) or -1)
    if not r.get("caras") and nit_med > 0 and 0 <= nit < BLUR_ABS and nit < BLUR_REL * nit_med:
        return "toma fallida: desenfocada o movida del todo, sin ninguna cara (no hay nada nítido)"
    fl = _face_lum(r)
    if fl is not None and fl >= FACE_BLOWN:
        return "caras quemadas (blancas, sin detalle)"
    if fl is not None and fl <= FACE_BLACK and float(r.get("lum", 128)) <= 25:
        return "muy oscura: no se ven las caras (¿no saltó el flash?)"
    return ""


# Defectos a solas (25-nov-2026, el usuario: «dejó tomas quemadas, con ojos cerrados o borrosas»). Calibrado mirando
# las 411 buenas del evento «24 nov 2026 - Evento 1» (recortes de cada cara): todas las que marcan estas cifras lo eran.
BURNT = 40.0             # % del cuadro blanco del todo y la imagen entera muy clara (luz media ≥ BURNT_LUM)…
BURNT_LUM = 210
BURNT_ANY = 55.0         # …o este % aunque no sea tan clara. Solo SIN caras: con la cara bien expuesta lo blanco es el
#                          cielo nublado o el mantel (XV 8 nov: retratos buenos con 35-54 % de cielo blanco)
BLUR_FACE_REL = 0.028    # la cara más nítida por debajo del 2,8 % de la mediana del evento: movida o desenfocada
#                          (caras nítidas pero de poco contraste con poca luz dan 0,031-0,043: boda 14 nov, revisadas)
BLUR_FACE_PX = 60        # (caras medidas a ≥ 60 px: más pequeñas no se juzgan)
BLUR_NORM = 0.05         # Windows: la cara más nítida con «nitn» (seleccion_win.face_sharpness) por debajo: movida o
#                          desenfocada. 26-nov-2026, el usuario: «dejaste pasar fotos borrosas». Revisadas a ojo las 447
#                          buenas con cara de «25 nov 2026»: las 18 por debajo de 0,05 eran borrosas o blandas; por
#                          encima empiezan las nítidas (0,058 en adelante)
EYES_SHUT = 0.11         # los DOS ojos por debajo de esto: cerrados del todo (a medio cerrar o sonriendo, no)
DEFECT_MAX_SHARE = 0.12  # si saltan en más del 12 % del evento, algo no cuadra con esa cámara: no se aparta ninguna


def _hugging(fs: List[dict]) -> bool:
    """¿Dos caras pegadas (beso, abrazo, baile)? Ahí los ojos cerrados son a propósito."""
    for i, a in enumerate(fs):
        for b in fs[i + 1:]:
            ax, ay, bx, by = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2, b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            if abs(ax - bx) < 1.3 * max(a["w"], b["w"]) and abs(ay - by) < 1.3 * max(a["h"], b["h"]):
                return True
    return False


def _defect(r: dict, face_med: float = 0.0) -> str:
    """Defecto claro aunque no haya otra toma igual con la que comparar (con tope: ver DEFECT_MAX_SHARE)."""
    q, lum = float(r.get("quemado", 0) or 0), float(r.get("lum", 0) or 0)
    fs = _main_faces(r)
    if not fs and ((q >= BURNT and lum >= BURNT_LUM) or q >= BURNT_ANY):
        return f"quemada: {q:.0f} % de la foto blanco del todo, sin detalle"
    if fs and face_med > 0:
        nit = _sharp(r)
        if max(f.get("px", 0) for f in fs) >= BLUR_FACE_PX and 0 <= nit < BLUR_FACE_REL * face_med:
            return "movida o desenfocada: ninguna cara está nítida"
    big = [f.get("nitn", -1) for f in fs if f.get("px", 0) >= BLUR_FACE_PX]
    if big and min(big) >= 0 and max(big) < BLUR_NORM:      # (solo Windows mide «nitn»)
        return "movida o desenfocada: ninguna cara está nítida"
    shut = [f for f in fs if _measurable(f) and f["ojoI"] < EYES_SHUT and f["ojoD"] < EYES_SHUT]
    if shut and not _hugging(fs):
        return ("ojos cerrados" + (" (una persona del grupo)" if len(fs) > 1 else "")
                + ": no hay otra toma igual con los ojos abiertos")
    return ""


def bursts(rows: List[dict]) -> List[List[int]]:
    """Grupos de tomas seguidas casi iguales (ráfagas), por hora de la cámara y parecido."""
    groups: List[List[int]] = []
    for i, r in enumerate(rows):
        if i and groups and _same_moment(rows[i - 1], r, BURST_DIST):
            groups[-1].append(i)
            continue
        groups.append([i])
    return groups


def _same_moment(a: dict, b: dict, limit: float) -> bool:
    """¿b es la toma SIGUIENTE a «a», casi igual? (hora de la cámara ≤ 3 s y parecido de Vision)"""
    dist = b.get("dist_prev")
    if dist is None:
        return False
    if "t" in a and "t" in b:
        return 0 <= b["t"] - a["t"] <= BURST_GAP and dist <= limit
    return dist <= min(limit, 0.2)


def _center(f: dict):
    return f["x"] + f["w"] / 2, f["y"] + f["h"] / 2


def _near(p: dict, other: List[dict], dx: float = 0.0, dy: float = 0.0, lo: float = 0.6, hi: float = 1.6):
    """(la cara de «other» más cerca de donde estaría p tras moverse la cámara dx, dy, con tamaño parecido; distancia)"""
    cx, cy = _center(p)
    best, bd = None, 9.0
    for g in other:
        if not lo <= _area(g) / max(_area(p), 1e-9) <= hi:
            continue
        gx, gy = _center(g)
        d = ((gx - dx - cx) ** 2 + (gy - dy - cy) ** 2) ** 0.5
        if d < bd:
            best, bd = g, d
    return best, bd


MAX_SHIFT = 0.06         # movimiento de cámara máximo entre dos repetidas (fracción del encuadre)


def _close(p: dict) -> float:
    return 0.7 * max(p["w"], 0.02)       # en una foto de grupo la vecina está a ≥1,5 caras; la gente se balancea un poco


def camera_shift(ref: List[dict], other: List[dict]):
    """Lo que se movió la cámara entre dos tomas: el desplazamiento con el que MÁS caras coinciden (si una persona se
    mueve, mandan las demás; con la mediana de dos caras mandaba justo la que se movía)."""
    if not ref or not other:
        return 0.0, 0.0
    cands = [(0.0, 0.0)]
    for p in ref:
        g, d = _near(p, other)
        if g is not None and d < MAX_SHIFT:
            cands.append((_center(g)[0] - _center(p)[0], _center(g)[1] - _center(p)[1]))

    def votes(c):
        return sum(1 for p in ref if _near(p, other, *c)[1] <= _close(p))
    best = max(cands, key=lambda c: (votes(c), -abs(c[0]) - abs(c[1])))
    # un movimiento de cámara lo tienen que confirmar AL MENOS DOS caras: con una sola, cualquier salto parece «la
    # cámara se movió» (el novio de una toma y la novia de la otra, al caminar entre bengalas)
    return best if best == (0.0, 0.0) or votes(best) >= 2 else (0.0, 0.0)


def pair_faces(ref: List[dict], other: List[dict]) -> List[Optional[dict]]:
    """La MISMA persona en la otra toma, para cada cara de «ref» (None si no sale). Primero se corrige el pequeño
    movimiento de la cámara entre una toma y otra (camera_shift); luego cada cara tiene que caer a menos de media cara
    de donde estaba y con el mismo tamaño (o, si el tamaño cambió mucho —un bebé que se gira—, a menos de un tercio de
    cara). Un margen fijo confundía a vecinos en las fotos de grupo (la gente apretada está a menos de un 6 %)."""
    if not ref or not other:
        return [None] * len(ref)
    dx, dy = camera_shift(ref, other)
    out: List[Optional[dict]] = []
    used = set()
    for p in ref:
        g, d = _near(p, [o for o in other if id(o) not in used], dx, dy)
        if g is None or d > _close(p):
            g, d = _near(p, [o for o in other if id(o) not in used], dx, dy, 0.3, 3.3)
            if g is not None and d > 0.35 * max(p["w"], 0.02):
                g = None
        if g is not None:
            used.add(id(g))
        out.append(g)
    for k, g in enumerate(out):                         # y MUTUA: esa cara, de vuelta, también es la más cercana a
        if g is None:                                   # esta persona (si no, quizá es otra que se movió: la novia en
            continue                                    # el sitio del novio al caminar)
        back = min(ref, key=lambda q: (_center(q)[0] + dx - _center(g)[0]) ** 2 + (_center(q)[1] + dy - _center(g)[1]) ** 2)
        if back is not ref[k]:
            out[k] = None
    return out


# --- caras que Vision no vio en una toma (en las fotos de grupo pasa: de 15 personas ve 7 en una toma y 10 en otra)
def rescue_requests(rows: List[dict]) -> Dict[int, List[list]]:
    """{toma: [zonas]} donde volver a buscar una cara: en cada par de tomas que se van a comparar (repetidas y vecinas
    casi iguales), las caras de una que no salen en la otra, en el sitio donde estarían (corregido el movimiento)."""
    pairs = set()
    for g in repeat_groups(rows, DUP_DIST):
        pairs |= {(a, b) for a in g for b in g if a != b}
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if not a.get("error") and not b.get("error") and _same_moment(a, b, STRICT_DIST):
            pairs |= {(i - 1, i), (i, i - 1)}
    req: Dict[int, List[list]] = {}
    for a, b in sorted(pairs):
        fx = _people(rows[a])
        if not fx:
            continue
        others = [f for f in rows[b].get("caras", []) if _area(f) >= 0.0004]
        dx, dy = camera_shift(fx, others)
        for f, g in zip(fx, pair_faces(fx, others)):
            if g is not None:
                continue
            box = [f["x"] + dx, f["y"] + dy, f["w"], f["h"]]
            lst = req.setdefault(b, [])
            if not any(abs(box[0] - o[0]) < 0.5 * f["w"] and abs(box[1] - o[1]) < 0.5 * f["h"] for o in lst):
                lst.append(box)
    return req


def rescue(rows: List[dict], req: Dict[int, List[list]], path_of: Callable[[str], Path], rfui: str) -> int:
    """Vuelve a buscar esas caras con «rfui caras» (en una zona pequeña Vision sí las ve) y las añade a su toma; las
    que no aparecen quedan como «ocultas» (tapadas por un brazo, un teléfono, o giradas). Devuelve cuántas encontró."""
    if not req:
        return 0
    lines = [json.dumps({"foto": str(path_of(rows[i]["foto"])), "cajas": boxes}) for i, boxes in sorted(req.items())]
    p = subprocess.run([rfui, "caras"], input="\n".join(lines) + "\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60 + 3 * len(lines))
    got: Dict[str, dict] = {}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            got[o.get("foto", "")] = o
    n = 0
    for i in req:
        o = got.get(rows[i]["foto"])
        if not o or o.get("error"):
            continue
        have = rows[i].setdefault("caras", [])
        for f in o.get("caras", []):                     # sin repetir una cara que ya estaba (la zona la vuelve a ver)
            if any(abs(_center(f)[0] - _center(g)[0]) < 0.5 * g["w"] and abs(_center(f)[1] - _center(g)[1]) < 0.5 * g["h"]
                   for g in have):
                continue
            have.append(f)
            n += 1
        rows[i].setdefault("ocultas", []).extend(o.get("ocultas", []))
    return n


def _worse_than(x: dict, y: dict) -> str:
    """Motivo si «x» es claramente peor que su vecina «y» (casi igual): misma persona con los ojos cerrados en x y
    abiertos en y, o su cara mucho más movida, o mucho peor de luz. '' si no hay una diferencia clara."""
    mx = _main_faces(x)
    pairs = [(f, g) for f, g in zip(mx, pair_faces(mx, y.get("caras", []))) if g is not None]
    if not pairs:
        return ""
    if any(_blink(f) and _open(g) for f, g in pairs) and not any(_blink(g) for g in (gg for _, gg in pairs)):
        return "ojos cerrados"
    fn = [(f.get("nit", -1), g.get("nit", -1)) for f, g in pairs if f.get("nit", -1) > 0 and g.get("nit", -1) > 0]
    if fn and all(a < 0.25 * b for a, b in fn) and float(x.get("nit", 0)) < 0.6 * float(y.get("nit", 1) or 1):
        return "movida o desenfocada"
    fl = [(f.get("lum", -1), g.get("lum", -1)) for f, g in pairs if f.get("lum", -1) >= 0 and g.get("lum", -1) >= 0]
    if fl and all((a < 35 or a > 235) and 60 <= b <= 210 for a, b in fl):
        return "caras oscuras" if fl[0][0] < 35 else "caras quemadas"
    return ""


STRICT_DIST = 0.22       # para comparar dos tomas, tienen que ser CASI IGUALES (no otro momento de la ráfaga)


# --- repetidas
def _dist(rows: List[dict], i: int, j: int) -> Optional[float]:
    """Parecido de Vision entre la toma i y una anterior j (rfui da el de las 4 anteriores). None = no se sabe."""
    k = i - j - 1
    ds = rows[i].get("dists")
    if isinstance(ds, list) and 0 <= k < len(ds) and ds[k] is not None and ds[k] >= 0:
        return float(ds[k])
    if k == 0 and rows[i].get("dist_prev") is not None:
        return float(rows[i]["dist_prev"])
    return None


def _twins(rows: List[dict], i: int, j: int, limit: float) -> Optional[float]:
    """Parecido si i y j (anterior) son la misma pose repetida; None si no lo son."""
    a, b = rows[j], rows[i]
    d = _dist(rows, i, j)
    if d is None:
        return None
    if not ("t" in a and "t" in b):
        limit = min(limit, DUP_STRICT)                  # sin hora de la cámara: solo si son clavadas
    if d > limit:
        return None
    if "ev" in a and "ev" in b and abs(float(a["ev"]) - float(b["ev"])) >= DUP_EV:
        return None                                      # horquillado (HDR)
    return d


def repeat_groups(rows: List[dict], limit: float = DUP_DIST, skip=()) -> List[List[int]]:
    """Grupos de la misma pose repetida: cada toma, a ≤DUP_GAP s de la anterior del grupo, y TODAS parecidas entre sí
    (no solo con la de al lado: así una ráfaga larga que va cambiando no se junta entera)."""
    groups: List[List[int]] = []
    for i, r in enumerate(rows):
        if r.get("error") or r.get("foto") in skip:
            continue
        best, bd = None, 9.0
        for g in groups[-(DUP_BACK + 1):]:
            if i - g[0] > DUP_BACK:
                continue
            last = rows[g[-1]]
            if "t" in last and "t" in r and abs(float(r["t"]) - float(last["t"])) > DUP_GAP:
                continue
            ds = [_twins(rows, i, m, limit) for m in g]
            if any(d is None for d in ds):
                continue
            if max(ds) < bd:
                best, bd = g, max(ds)
        if best is not None:
            best.append(i)
        else:
            groups.append([i])
    return [g for g in groups if len(g) > 1]


def _people(r: dict) -> List[dict]:
    """Las caras de la foto que cuentan (también las pequeñas de una foto de grupo; no la gente lejos del fondo)."""
    fs = [f for f in r.get("caras", []) if _area(f) >= 0.0004]
    if not fs:
        return []
    big = max(_area(f) for f in fs)
    return [f for f in fs if _area(f) >= 0.12 * big]


def _openness(f: Optional[dict]) -> Optional[float]:
    """Lo abiertos que están sus ojos (el más cerrado de los dos), si se pueden juzgar."""
    return min(float(f["ojoI"]), float(f["ojoD"])) if f and _measurable(f) else None


def _more_open(f: Optional[dict], g: Optional[dict]) -> bool:
    """¿Ojos claramente más abiertos en f que en g? (abiertos en f; en g cerrados o a medio cerrar, p. ej. comiendo
    pastel o riéndose; y bastante más: ≥1,35 veces)."""
    a, b = _openness(f), _openness(g)
    return a is not None and b is not None and a >= EYE_OPEN and b < EYE_OPEN and a >= EYES_CLEAR * b


def _eyes(f: Optional[dict]) -> str:
    """'a' abiertos, 'c' cerrados, '' no se sabe (de perfil, mirando abajo o a medias)."""
    if not f or not _measurable(f):
        return ""
    if f["ojoI"] < EYE_CLOSED and f["ojoD"] < EYE_CLOSED:
        return "c"
    if f["ojoI"] >= EYE_OPEN and f["ojoD"] >= EYE_OPEN:
        return "a"
    return ""


def _better(f: dict, g: Optional[dict], big: float) -> str:
    """Qué tiene CLARAMENTE mejor esta persona en la cara f que en g (ella misma en otra toma); '' si nada.
    big = la cara más grande de la toma de f (para saber si es protagonista)."""
    main = _area(f) >= 0.45 * big
    if g is None:                                       # en la otra toma no se le ve la cara
        return "se le ve la cara" if main and float(f.get("calidad", 0) or 0) >= 0.35 else ""
    if _more_open(f, g):
        return "ojos abiertos"
    nf, ng = float(f.get("nit", -1)), float(g.get("nit", -1))
    if main and nf > 0 and 0 < ng < SHARP_CLEAR * nf:
        return "más nítida"
    qf, qg = f.get("calidad"), g.get("calidad")
    lim = Q_CLEAR_MAIN if main and min(f.get("px", 0), g.get("px", 0)) >= 150 else Q_CLEAR
    if qf is not None and qg is not None and float(qf) - float(qg) >= lim:
        return "mejor cara"
    return ""


def better_in(x: dict, y: dict) -> str:
    """'' si la toma y es IGUAL O MEJOR que x para todas las personas de x (entonces x sobra); si no, qué tiene x mejor
    («ojos abiertos», «más nítida», «mejor cara», «se le ve la cara»). Así nunca se tira una toma que tiene algo
    bueno que la otra no tiene (p. ej. en una foto de grupo, una persona con los ojos abiertos)."""
    fx = _people(x)
    if not fx:                                          # sin caras (detalles, salón): nitidez y nota de toda la foto
        nx, ny = float(x.get("nit", 0) or 0), float(y.get("nit", 0) or 0)
        if nx > 0 and ny < SHARP_CLEAR * nx:
            return "más nítida"
        ex, ey = x.get("estetica"), y.get("estetica")
        if ex is not None and ey is not None and float(ex) - float(ey) >= Q_CLEAR:
            return "más bonita"
        return ""
    big = max(_area(f) for f in fx)
    others = [f for f in y.get("caras", []) if _area(f) >= 0.0004]
    for f, g in zip(fx, pair_faces(fx, others)):
        why = _better(f, g, big)
        if why:
            return why
    return ""


def best_of(rows: List[dict], g: List[int]):
    """(tomas del grupo de mejor a peor, {toma: personas con los ojos cerrados que en otra toma los tienen abiertos}).
    La mejor: menos ojos cerrados, luego menos a medio cerrar; luego mejor calidad de cara (pesan más las caras grandes: los protagonistas),
    más nítida y más estética."""
    faces = {i: _people(rows[i]) for i in g}
    ref = max(g, key=lambda i: len(faces[i]))               # la toma donde se ven más caras
    persons = faces[ref]
    seen = {i: (persons if i == ref else pair_faces(persons, faces[i])) for i in g}
    closed = {i: 0 for i in g}
    half = {i: 0 for i in g}                                # ojos a medio cerrar donde en otra toma están abiertos
    for k in range(len(persons)):
        states = {i: _eyes(seen[i][k]) for i in g}
        if "a" in states.values():                          # esa persona sale bien en alguna toma
            for i, st in states.items():
                closed[i] += st == "c"
        for i in g:
            half[i] += any(_more_open(seen[j][k], seen[i][k]) for j in g if j != i) and states[i] != "c"
    sharp_max = max(_sharp(rows[i]) for i in g) or 1.0

    weights = [_area(p) ** 0.5 for p in persons]

    def score(i):
        qs = [float((f or {}).get("calidad", 0) or 0) for f in seen[i]]
        q = sum(w * v for w, v in zip(weights, qs)) / sum(weights) if qs else 0.0
        return (-closed[i], -half[i], q + 0.3 * _sharp(rows[i]) / sharp_max + 0.2 * float(rows[i].get("estetica", 0) or 0), -i)

    return sorted(g, key=score, reverse=True), closed


DEFECT = {"ojos cerrados": ", con ojos cerrados", "movida o desenfocada": ", movida",
          "caras oscuras": ", más oscura", "caras quemadas": ", quemada"}


def _repeats(rows: List[dict], skip, limit: float = DUP_DIST, kept_better: Optional[dict] = None) -> Dict[str, str]:
    """Repetidas que sobran. De cada grupo se queda la mejor, y TAMBIÉN cualquier otra que tenga algo claramente mejor
    que todas las que se quedan (una persona con los ojos abiertos, más nítida o con mejor cara): solo sobra la toma
    que otra que se queda iguala o mejora en todo. kept_better: {foto: qué tenía mejor} de las que se salvaron así."""
    out: Dict[str, str] = {}
    for g in repeat_groups(rows, limit, skip):
        ranked, closed = best_of(rows, g)
        best = ranked[0]
        kept = [best]
        for i in ranked[1:]:
            if len(g) >= BACKUP_FROM and len(kept) < 2:   # respaldo: de 3 o más casi iguales se quedan DOS
                kept.append(i)
                continue
            k = next((k for k in kept if not better_in(rows[i], rows[k])), None)
            if k is None:                                 # tiene algo mejor que cada una de las que se quedan
                kept.append(i)
                if kept_better is not None:
                    kept_better[rows[i]["foto"]] = better_in(rows[i], rows[best])
                continue
            # el defecto solo se nombra si es CLARO (misma persona, ver _worse_than); si no, es la misma foto, peor nota
            extra = ", con ojos cerrados" if closed[i] > closed[k] else DEFECT.get(_worse_than(rows[i], rows[k]), "")
            if len(g) == 2:
                tail = " (la mejor de las dos)" if k == best else " (igual o mejor en todo)"
                quedan = Path(rows[k]["foto"]).stem
            else:                                         # se nombran las dos que se quedan
                tail = f" (las 2 mejores de las {len(g)}: una de respaldo)"
                quedan = " y ".join(Path(rows[j]["foto"]).stem for j in kept[:2])
            out[rows[i]["foto"]] = f"repetida{extra}: se quedan {quedan}{tail}" if len(g) > 2 else \
                f"repetida{extra}: se queda {quedan}{tail}"
    return out


def decide(rows: List[dict]) -> Dict[str, str]:
    """{foto: motivo} de las que se apartan. Ver la explicación del módulo."""
    ok_rows = [r for r in rows if not r.get("error")]
    n = len(ok_rows)
    accidental = {r["foto"]: why for r in ok_rows if (why := _accidental(r))}
    nits = sorted(float(r["nit"]) for r in ok_rows if float(r.get("nit", 0) or 0) > 0)
    nit_med = nits[len(nits) // 2] if len(nits) >= 20 else 0.0     # con pocas fotos no hay mediana fiable
    soft = {r["foto"]: why for r in ok_rows if r["foto"] not in accidental and (why := _soft_bad(r, nit_med))}
    if len(soft) > max(3, int(SOFT_MAX_SHARE * n)):     # tantas «a solas» = algo raro con esa cámara: no
        soft = {}
    fn = sorted(_sharp(r) for r in ok_rows if _main_faces(r) and _sharp(r) > 0)
    face_med = fn[len(fn) // 2] if len(fn) >= 20 else 0.0
    defects = {r["foto"]: why for r in ok_rows
               if r["foto"] not in accidental and r["foto"] not in soft and (why := _defect(r, face_med))}
    if len(defects) > max(3, int(DEFECT_MAX_SHARE * n)):
        defects = {}
    shut = {k: v for k, v in defects.items() if v.startswith("ojos cerrados")}   # al final: si hay una toma gemela,
    defects = {k: v for k, v in defects.items() if k not in shut}                # su motivo («en A1… está bien») vale más
    lone = {**accidental, **soft, **defects}
    repes = _repeats(rows, lone)
    if len(repes) > max(10, DUP_MAX_SHARE * n):          # más de la mitad repetida: solo las clavadas
        repes = _repeats(rows, lone, DUP_STRICT)
    taken = set(lone) | set(repes)
    comp: Dict[str, str] = {}
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if a.get("error") or b.get("error") or not _same_moment(a, b, STRICT_DIST):
            continue
        for x, y in ((a, b), (b, a)):
            if x["foto"] in comp or y["foto"] in comp or x["foto"] in taken or y["foto"] in taken:
                continue
            why = _worse_than(x, y)
            if why and not _worse_than(y, x) and not better_in(x, y):    # y igual o mejor para todas las personas
                comp[x["foto"]] = f"{why} (en {Path(y['foto']).stem}, la toma de al lado, está bien)"
    if len(comp) > max(3, int(MAX_SHARE * n)):           # demasiadas por comparación: algo no cuadra (p. ej. una
        comp = {}                                        # cámara rara): esas no
    out = {**comp, **repes, **lone}
    out = {**{k: v for k, v in shut.items() if k not in out}, **out}
    for keep in keep_one_per_moment(ok_rows, out):
        out.pop(keep, None)
    return out


NOT_A_MOMENT = ("toma negra", "toma blanca", "toma al piso", "toma fallida")


def moments(rows: List[dict]) -> List[List[int]]:
    """Tomas seguidas del mismo momento (≤ MOMENT_GAP s entre una y la siguiente; sin hora, casi iguales)."""
    groups: List[List[int]] = []
    for i, r in enumerate(rows):
        a = rows[i - 1] if i else None
        if a is not None and groups:
            if "t" in a and "t" in r:
                same = 0 <= float(r["t"]) - float(a["t"]) <= MOMENT_GAP
            else:
                same = r.get("dist_prev") is not None and float(r["dist_prev"]) <= BURST_DIST
            if same:
                groups[-1].append(i)
                continue
        groups.append([i])
    return groups


def _least_bad(r: dict, why: str):
    """Para quedarse con una de un momento donde todas salieron mal: con caras, sin ojos cerrados, la más nítida."""
    fs = [f for f in _main_faces(r) if f.get("px", 0) >= BLUR_FACE_PX]
    nn = [f.get("nitn", -1) for f in fs if f.get("nitn", -1) >= 0]
    return (bool(_main_faces(r)), "ojos cerrados" not in why, max(nn) if nn else 0.0, _sharp(r))


def keep_one_per_moment(rows: List[dict], out: Dict[str, str]) -> List[str]:
    """26-nov-2026, el usuario: «nunca me dejes sin ninguna foto de algún momento… si las 4 fotos están malas, aunque sea
    déjame una». Fotos de un momento donde TODAS se apartaban: la menos mala se queda. (Las tomas accidentales —negra,
    blanca, al piso, fallida sin nadie— no son un momento.)"""
    keep = []
    for g in moments(rows):
        names = [rows[i]["foto"] for i in g]
        if any(n not in out for n in names):
            continue
        cands = [i for i in g if not out[rows[i]["foto"]].startswith(NOT_A_MOMENT)]
        if cands:
            keep.append(rows[max(cands, key=lambda i: _least_bad(rows[i], out[rows[i]["foto"]]))]["foto"])
    return keep


KINDS = (("repetida", "repetida", "repetidas"), ("fallida", "fallida", "fallidas"),
         ("ojos cerrados", "con ojos cerrados", "con ojos cerrados"), ("movida", "movida", "movidas"),
         ("negra", "negra", "negras"), ("blanca", "blanca", "blancas"), ("piso", "al piso o a la pared", "al piso o a la pared"),
         ("oscur", "oscura", "oscuras"), ("quemad", "quemada", "quemadas"))


def kind(why: str) -> str:
    """Tipo de motivo (la clave de KINDS), para el resumen."""
    w = why.lower()
    return next((k for k, *_ in KINDS if k in w), "otra")


def summary(decisions: Dict[str, str]) -> str:
    """«8 repetidas · 2 con ojos cerrados · 1 negra»."""
    from collections import Counter
    names = {k: (one, many) for k, one, many in KINDS}
    c = Counter(kind(w) for w in decisions.values())
    return " · ".join(f"{v} {names.get(k, ('otra', 'otras'))[v != 1]}" for k, v in c.most_common())


# ------------------------------------------------------------------------------------------------- apartar
def apply(raw_dir: Path, decisions: Dict[str, str]) -> int:
    """Mueve las apartadas a «1 Original/Fotos malas» (mismo disco: instantáneo; nunca se borran) y apunta el
    motivo de cada una en «motivos.txt». Devuelve cuántas movió."""
    if not decisions:
        return 0
    dest = Path(raw_dir) / DIR_NAME
    dest.mkdir(exist_ok=True)
    moved = []
    for name, why in sorted(decisions.items()):
        src = Path(raw_dir) / name
        if not src.is_file():
            continue
        target = dest / name
        if target.exists():
            target = dest / f"{src.stem}_{int(time.time())}{src.suffix}"
        os.replace(src, target)
        moved.append((target.name, why))
        for side in (src.with_suffix(".xmp"), src.with_suffix(".XMP")):     # su .xmp, si lo tenía, la acompaña
            if side.exists():
                os.replace(side, dest / side.name)
    if moved:
        with open(dest / REASONS_FILE, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("Fotos que la Selección IA de RawFlow apartó (no se borró ninguna).\n"
                        "Para recuperar una: muévela a «Fotos buenas» y pulsa «Seguir» en RawFlow.\n\n")
            for n, why in moved:
                f.write(f"{n}\t{why}\n")
    return len(moved)


def kept_restored(raw_dir: Path) -> set:
    """Fotos que tú sacaste de «Fotos malas» (a «1 Original» o a «Fotos buenas»): no se vuelven a apartar nunca."""
    f = Path(raw_dir) / DIR_NAME / REASONS_FILE
    if not f.exists():
        return set()
    names = {line.split("\t", 1)[0] for line in f.read_text(encoding="utf-8").splitlines() if "\t" in line}
    return {n for n in names if (Path(raw_dir) / n).exists() or (Path(raw_dir) / GOOD_NAME / n).exists()}


def bad_photos(raw_dir: Path) -> List[tuple]:
    """[(ruta, motivo)] de las fotos que siguen en «1 Original/Fotos malas», en orden de nombre (el de la toma)."""
    from . import core
    bad = Path(raw_dir) / DIR_NAME
    if not bad.is_dir():
        return []
    why: Dict[str, str] = {}
    f = bad / REASONS_FILE
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "\t" in line:
                n, w = line.split("\t", 1)
                why[n] = w
    fotos = sorted(q for q in bad.iterdir() if q.is_file() and q.suffix.lower() in core.RAW_EXTENSIONS
                   and not q.name.startswith("."))
    return [(q, why.get(q.name, "")) for q in fotos]


def restore(raw_dir: Path, names: List[str]) -> int:
    """Devuelve a «Fotos buenas» las fotos elegidas de «Fotos malas» (con su .xmp). Su línea en «motivos.txt» se queda:
    así kept_restored() sabe que tú la recuperaste y nunca se vuelve a apartar. Devuelve cuántas movió."""
    raw_dir = Path(raw_dir)
    bad, good = raw_dir / DIR_NAME, raw_dir / GOOD_NAME
    good.mkdir(exist_ok=True)
    n = 0
    for name in names:
        src = bad / name
        if not src.is_file():
            continue
        target = good / name
        if target.exists():                              # (no debería: la misma foto en buenas y en malas)
            continue
        os.replace(src, target)
        n += 1
        for side in (src.with_suffix(".xmp"), src.with_suffix(".XMP")):
            if side.exists():
                os.replace(side, good / side.name)
    return n


def move_rest_to_good(raw_dir: Path) -> int:
    """Las fotos que quedan sueltas en «1 Original» (las buenas) → «1 Original/Fotos buenas» (con su .xmp, si tenían).
    Mismo disco: instantáneo. Devuelve cuántas movió."""
    from . import core
    raw_dir = Path(raw_dir)
    fotos = [q for q in raw_dir.iterdir() if q.is_file() and q.suffix.lower() in core.RAW_EXTENSIONS
             and not q.name.startswith(".")] if raw_dir.is_dir() else []
    if not fotos:
        return 0
    good = raw_dir / GOOD_NAME
    good.mkdir(exist_ok=True)
    n = 0
    for q in fotos:
        target = good / q.name
        if target.exists():
            target = good / f"{q.stem}_{int(time.time())}{q.suffix}"
        os.replace(q, target)
        n += 1
        for side in (q.with_suffix(".xmp"), q.with_suffix(".XMP")):
            if side.exists():
                os.replace(side, good / side.name)
    return n


def _camera(stem: str) -> str:
    """Prefijo del nombre (EDN_, OSZ_, _DSC, IMG_…): con dos cámaras en el mismo evento, cada una con sus tomas seguidas
    (las repetidas siempre son de la misma cámara)."""
    i = 0
    while i < len(stem) and not stem[i].isdigit():
        i += 1
    return stem[:i].upper()


def run(raw_dir: Path, rfui: str, done_stems: set, log: Callable[[str], None] = lambda m: None,
        should_stop: Callable[[], bool] = lambda: False,
        progress: Callable[[float], None] = lambda f: None) -> Dict[str, str]:
    """Selección IA de las fotos de «1 Original» que aún no tienen DNG (las recuperadas a mano nunca se vuelven a
    apartar): las analiza en el orden de la toma, decide y aparta. Devuelve {foto: motivo} de las apartadas.
    rfui: el ayudante del Mac; vacío o None = el motor de Windows (seleccion_win)."""
    from . import core
    raw_dir = Path(raw_dir)
    keep = kept_restored(raw_dir)
    fotos = [q for q in raw_dir.iterdir() if q.is_file() and q.suffix.lower() in core.RAW_EXTENSIONS
             and not q.name.startswith(".") and q.stem.lower() not in done_stems and q.name not in keep]
    fotos.sort(key=lambda q: (_camera(q.stem), q.stat().st_mtime, q.name))    # orden de la toma, cámara por cámara
    if not fotos:
        return {}
    win = None
    if not rfui:
        from . import seleccion_win as win
    rows = (analyze([str(q) for q in fotos], rfui, log, should_stop, progress) if rfui
            else win.analyze([str(q) for q in fotos], log, should_stop, progress))
    try:                                                 # sin esto, más prudente: si no se ve a alguien, se quedan las dos
        req = rescue_requests(rows)
        if req and not should_stop():
            by_name = {q.name: q for q in fotos}
            n = (rescue(rows, req, lambda name: by_name[name], rfui) if rfui
                 else win.rescue(rows, req, lambda name: by_name[name]))
            log(f"Selección IA: {n} caras que no se vieron a la primera, encontradas en {len(req)} tomas")
    except Exception:  # noqa: BLE001
        pass
    decisions = {k: v for k, v in decide(rows).items() if k not in keep}
    apply(raw_dir, decisions)
    return decisions


if __name__ == "__main__":       # calibrar: python -m rawflow.seleccion analisis.jsonl [carpeta de los RAW, para rescatar caras]
    import sys
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.startswith("{")]
    if len(sys.argv) > 2:
        rq = rescue_requests(rows)
        n = rescue(rows, rq, lambda name: Path(sys.argv[2]) / name, str(Path(__file__).resolve().parent.parent / "build" / "rfui"))
        print(f"rescatadas {n} caras en {len(rq)} tomas · ocultas {sum(len(r.get('ocultas', [])) for r in rows)}")
    d = decide(rows)
    print(f"{len(d)} de {len(rows)} apartadas: {summary(d)}")
    for k, v in sorted(d.items()):
        print(f"  {k}\t{v}")

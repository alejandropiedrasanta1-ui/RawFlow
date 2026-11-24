"""Selección IA en Windows: lo mismo que «rfui cull» / «rfui caras» del Mac (ver tools/rfui.swift), pero con OpenCV.

En el Mac cada foto la mide la IA de Apple (Vision). En Windows no existe, así que este módulo saca las MISMAS medidas
con modelos pequeños que van dentro de RawFlow (rawflow/modelos, sin internet):
  · caras: YuNet (OpenCV Zoo, licencia MIT), sobre la vista previa de 1600 px;
  · ojos y giro de la cabeza: la malla facial de 478 puntos de MediaPipe (Google, Apache 2.0), sobre la cara recortada
    de la vista previa GRANDE del RAW; el ojo se mide igual que en el Mac (alto/ancho a lo largo del ojo);
  · parecido entre tomas: huella de MobileNetV3 (MediaPipe Image Embedder, Apache 2.0);
  · luz, negro, quemado y nitidez: las mismas cuentas que rfui (regionStats).
Lo que Vision da y aquí no hay (nota estética y etiquetas de escena) simplemente no se rellena: las reglas que lo usan
(toma «fallida», toma «al piso») no se aplican en Windows; las repetidas, los ojos cerrados y las tomas negras o
blancas, sí.

Las medidas se devuelven EN LA ESCALA DE VISION (parecido, ojos), con las equivalencias medidas en eventos reales
(ver _to_vision y EYE_*): así rawflow/seleccion.py decide con las mismas reglas y los mismos umbrales en Mac y Windows.
Nada de aquí escribe en las fotos: solo las lee.
"""
from __future__ import annotations

import math
import mmap
import struct
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

MODELS = Path(__file__).resolve().parent / "modelos"
YUNET = "face_detection_yunet_2023mar.onnx"
TASK = "face_landmarker.task"                  # zip de Google: dentro va face_landmarks_detector.tflite
MESH = "face_landmarks_detector.tflite"
EMBED = "mobilenet_v3_large.tflite"

PREVIEW = 1600           # lado mayor de la imagen donde se buscan las caras (como la vista previa de rfui)
BACK = 4                 # parecido con las 4 tomas anteriores (como CULL_BACK en rfui)
DET_SCORE = 0.6          # confianza mínima de una cara
RESCUE_SCORE = 0.35     # …al volver a buscarla en su sitio (zona pequeña, ampliada)

# ojo: índices del contorno de los párpados en la malla (ojo derecho e izquierdo de la persona)
EYE_R = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246)
EYE_L = (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466)


def _models_dir() -> Path:
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if base and (Path(base) / "rawflow" / "modelos").is_dir():
        return Path(base) / "rawflow" / "modelos"
    return MODELS


def available() -> Tuple[bool, str]:
    """¿Se puede analizar en este equipo? (OpenCV instalado y los modelos en su sitio)"""
    try:
        import cv2 as _c  # noqa: F401
        import numpy as _n  # noqa: F401
        del _c, _n
    except Exception as e:  # noqa: BLE001
        return False, f"falta OpenCV ({type(e).__name__})"
    d = _models_dir()
    miss = [n for n in (YUNET, TASK, EMBED) if not (d / n).is_file()]
    if miss:
        return False, "faltan los modelos: " + ", ".join(miss)
    return True, ""


# ------------------------------------------------------------------------------------------- vista previa del RAW
_TYPES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def _tiff(m) -> Optional[dict]:
    """Lee la estructura TIFF de un RAW (NEF, ARW, CR2, DNG, PEF…): dónde están sus JPEG incrustados, cómo está girada
    la foto y los datos de la toma (hora con centésimas, apertura, velocidad, ISO). None si no es un TIFF."""
    if len(m) < 16:
        return None
    bo = {b"II": "<", b"MM": ">"}.get(bytes(m[0:2]))
    if not bo:
        return None
    out = {"jpegs": [], "orient": 1, "exif": {}}
    seen = set()

    def val(typ, count, raw4, as_list=False):
        size = _TYPES.get(typ, 1) * count
        if size <= 4:
            data = raw4[:size]
        else:
            off = struct.unpack(bo + "I", raw4)[0]
            if off + size > len(m):
                return None
            data = bytes(m[off:off + size])
        if typ == 2:
            return data.split(b"\0")[0].decode("ascii", "replace")
        fmt = {1: "B", 3: "H", 4: "I", 9: "i", 13: "I"}.get(typ)
        if typ in (5, 10):
            v = struct.unpack(bo + ("II" if typ == 5 else "ii") * count, data)
            r = [v[i] / v[i + 1] if v[i + 1] else 0.0 for i in range(0, len(v), 2)]
        elif fmt:
            r = list(struct.unpack(bo + fmt * count, data))
        else:
            return None
        return r if as_list else (r[0] if r else None)

    def ifd(off, depth=0, exif=False):
        if not off or off in seen or off + 2 > len(m) or depth > 4 or len(seen) > 40:
            return
        seen.add(off)
        n = struct.unpack(bo + "H", m[off:off + 2])[0]
        if n > 512 or off + 2 + 12 * n + 4 > len(m):
            return
        tags = {}
        for i in range(n):
            e = bytes(m[off + 2 + 12 * i: off + 14 + 12 * i])
            tag, typ, count = struct.unpack(bo + "HHI", e[:8])
            tags[tag] = (typ, count, e[8:12])
        get = lambda t, lst=False: val(*tags[t], as_list=lst) if t in tags else None  # noqa: E731
        if exif:
            ex = out["exif"]
            for t, k in ((0x9003, "fecha"), (0x9291, "subseg"), (0x829A, "vel"), (0x829D, "f"), (0x8827, "iso")):
                v = get(t)
                if v is not None and k not in ex:
                    ex[k] = v
            return
        if depth == 0 and 0x0112 in tags and len(seen) == 1:
            out["orient"] = int(get(0x0112) or 1)
        j_off, j_len = get(0x0201), get(0x0202)
        if j_off and j_len:
            out["jpegs"].append((int(j_off), int(j_len)))
        if get(0x0103) in (6, 7) and 0x0111 in tags and 0x0117 in tags:      # tira comprimida en JPEG (CR2, DNG)
            so, sl = get(0x0111, True), get(0x0117, True)
            if so and sl and len(so) == 1:
                out["jpegs"].append((int(so[0]), int(sl[0])))
        if 0x8769 in tags:
            ifd(int(get(0x8769) or 0), depth + 1, exif=True)
        for sub in (get(0x014A, True) or []):
            ifd(int(sub), depth + 1)
        nxt = struct.unpack(bo + "I", m[off + 2 + 12 * n: off + 6 + 12 * n])[0]
        ifd(nxt, depth)

    try:
        ifd(struct.unpack(bo + "I", m[4:8])[0])
    except (struct.error, ValueError, IndexError):
        pass
    return out


def _scan_jpegs(m, limit: int = 6) -> List[Tuple[int, int]]:
    """Sin estructura TIFF que valga (CR3, RAF, RW2…): los JPEG incrustados se buscan por su cabecera."""
    out, pos = [], 0
    while len(out) < limit:
        i = m.find(b"\xff\xd8\xff", pos)
        if i < 0:
            break
        if m[i + 3] in (0xDB, 0xE0, 0xE1, 0xC4, 0xEE, 0xE2):
            out.append((i, len(m) - i))
        pos = i + 3
    return out


def _exif_time(ex: dict) -> Optional[float]:
    try:
        t = time.mktime(time.strptime(str(ex["fecha"])[:19], "%Y:%m:%d %H:%M:%S"))
    except (KeyError, ValueError, OverflowError):
        return None
    ss = str(ex.get("subseg", "")).strip()
    if ss.isdigit():
        t += float("0." + ss)
    return t


def read_raw(path) -> Tuple[Optional["object"], dict]:
    """(vista previa GRANDE del RAW como imagen BGR ya derecha, {"t": hora de la toma, "ev": exposición a ISO 100}).
    La imagen es None si el archivo no trae ninguna vista previa que se pueda leer."""
    import cv2
    import numpy as np
    info: dict = {}
    best = None
    try:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
            t = _tiff(m)
            cands = list(t["jpegs"]) if t else []
            if t:
                tt = _exif_time(t["exif"])
                if tt is not None:
                    info["t"] = tt
                n, sh, iso = (float(t["exif"].get(k) or 0) for k in ("f", "vel", "iso"))
                if n > 0 and sh > 0 and iso > 0:
                    info["ev"] = math.log2(n * n / sh) - math.log2(iso / 100)
            cands = [(o, ln) for o, ln in cands if 0 < o < len(m) - 4 and bytes(m[o:o + 2]) == b"\xff\xd8"]
            if not cands:
                cands = _scan_jpegs(m)
            for o, ln in sorted(cands, key=lambda c: -c[1])[:3]:         # el más grande primero
                buf = np.frombuffer(m, dtype=np.uint8, count=min(ln, len(m) - o), offset=o)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
                del buf
                if img is not None and (best is None or img.shape[1] * img.shape[0] > best.shape[1] * best.shape[0]):
                    best = img
                if best is not None and max(best.shape[:2]) >= PREVIEW:
                    break
            orient = t["orient"] if t else 1
    except (OSError, ValueError):
        return None, info
    if best is None or min(best.shape[:2]) < 64:
        return None, info
    rot = {3: cv2.ROTATE_180, 6: cv2.ROTATE_90_CLOCKWISE, 8: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(orient)
    if rot is not None:
        best = cv2.rotate(best, rot)
    return best, info


# ------------------------------------------------------------------------------------------- medidas (como rfui)
def _gray_resized(img, w: int):
    import cv2
    h = max(1, int(img.shape[0] * w / img.shape[1]))
    small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if w < img.shape[1] else cv2.INTER_LINEAR)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small


def region_stats(gray) -> Dict[str, float]:
    """Luz media, % negro, % quemado, desviación y nitidez (varianza del laplaciano): las cuentas de regionStats."""
    import numpy as np
    g = gray.astype(np.float32)
    if g.shape[0] < 5 or g.shape[1] < 5:
        return {"lum": -1.0, "nit": -1.0}
    c = g[1:-1, 1:-1]
    lap = 4 * c - g[1:-1, :-2] - g[1:-1, 2:] - g[:-2, 1:-1] - g[2:, 1:-1]
    return {"lum": float(c.mean()), "negro": float((c < 12).mean() * 100), "quemado": float((c > 250).mean() * 100),
            "desv": float(c.std()), "nit": float(lap.var())}


def _eye_ratio(pts) -> float:
    """Alto/ancho del ojo medido A LO LARGO DEL OJO (de comisura a comisura), como eyeRatio en rfui."""
    import numpy as np
    p = np.asarray(pts, dtype=np.float64)
    if len(p) < 4:
        return -1.0
    d = ((p[:, None, :] - p[None, :, :]) ** 2).sum(-1)
    a, b = np.unravel_index(int(d.argmax()), d.shape)
    ang = math.atan2(p[b, 1] - p[a, 1], p[b, 0] - p[a, 0])
    c, s = math.cos(-ang), math.sin(-ang)
    xs, ys = p[:, 0] * c - p[:, 1] * s, p[:, 0] * s + p[:, 1] * c
    wd, ht = xs.max() - xs.min(), ys.max() - ys.min()
    return float(ht / wd) if wd > 0 else -1.0


# Equivalencias con Vision, medidas en eventos reales (ver tests/datos/seleccion/LEEME.md, «Windows»):
#   · ojo: la malla de MediaPipe da el párpado algo más abierto que Vision; EYE_GAIN/EYE_BIAS lo llevan a su escala
#     (abiertos ≈0,27; cerrados <0,12);
#   · parecido: distancia entre huellas de MobileNetV3 → distancia de la huella de Vision (repetidas ≤0,16).
EYE_GAIN, EYE_BIAS = 1.0, 0.0
# (medida aquí, distancia equivalente de Vision), a trozos. Dos medidas, y manda la que diga «más distintas»:
#   · contenido: distancia entre huellas de MobileNetV3;
#   · composición: 1 − correlación de las dos tomas en pequeño (48×32), probando pequeños movimientos de cámara.
EMB_KNOTS = ((0.0, 0.0), (0.26, 0.10), (0.37, 0.16), (0.46, 0.22), (0.68, 0.33), (1.10, 0.50), (1.42, 1.05))
STR_KNOTS = ((0.0, 0.0), (0.05, 0.16), (0.10, 0.22), (0.25, 0.33), (1.0, 1.05))


def _interp(ks, d: float) -> float:
    if d <= ks[0][0]:
        return ks[0][1]
    for (x0, y0), (x1, y1) in zip(ks, ks[1:]):
        if d <= x1:
            return y0 + (y1 - y0) * (d - x0) / max(x1 - x0, 1e-9)
    return ks[-1][1]


def _to_vision(emb: float, st: float) -> float:
    return max(_interp(EMB_KNOTS, emb), _interp(STR_KNOTS, st))


def _struct_dist(a, b, s: int = 3) -> float:
    """1 − la mejor correlación entre dos tomas en pequeño, moviendo una hasta ±s píxeles (≈ ±6 % del encuadre)."""
    import numpy as np
    best = -1.0
    h, w = a.shape
    for dy in range(-s, s + 1):
        for dx in range(-s, s + 1):
            xa = a[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            xb = b[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            xa, xb = xa - xa.mean(), xb - xb.mean()
            den = float(np.sqrt((xa * xa).sum() * (xb * xb).sum()))
            if den > 0:
                best = max(best, float((xa * xb).sum()) / den)
    return 1.0 - best


class Engine:
    """Los tres modelos cargados una vez. No es seguro usar un mismo Engine desde dos hilos a la vez."""

    def __init__(self):
        import cv2
        try:                                           # (OpenCV avisa por consola de cosas que aquí no importan)
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:  # noqa: BLE001
            pass
        d = _models_dir()
        self.cv2 = cv2
        import numpy as np
        self.np = np
        # Los modelos se leen con Python y se pasan a OpenCV EN MEMORIA: dándole la ruta, OpenCV no abre carpetas con
        # acentos o eñes en Windows (un usuario «José» se quedaría sin Selección IA).
        buf = lambda name: np.frombuffer((d / name).read_bytes(), dtype=np.uint8)  # noqa: E731
        self.det = cv2.FaceDetectorYN.create("onnx", buf(YUNET), np.empty(0, np.uint8), (320, 320), DET_SCORE, 0.3, 5000)
        with zipfile.ZipFile(d / TASK) as z:                     # el .tflite va dentro del .task de Google
            mesh = z.read(MESH)
        self.mesh = cv2.dnn.readNetFromTFLite(np.frombuffer(mesh, dtype=np.uint8))
        self.mesh_out = self.mesh.getUnconnectedOutLayersNames()
        self.emb = cv2.dnn.readNetFromTFLite(buf(EMBED))

    # ---- caras
    def detect(self, img, score: float = DET_SCORE):
        """Caras de una imagen BGR: [(x, y, w, h, ojo_d, ojo_i, confianza)] en píxeles de esa imagen."""
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        self.det.setScoreThreshold(score)
        _, faces = self.det.detect(img)
        out = []
        for f in (faces if faces is not None else []):
            x, y, fw, fh = (float(v) for v in f[:4])
            if fw < 8 or fh < 8:
                continue
            out.append((x, y, fw, fh, (float(f[4]), float(f[5])), (float(f[6]), float(f[7])), float(f[14])))
        return out

    def measure(self, big, box, eyes=None) -> dict:
        """Ojos, giro, nitidez y luz de UNA cara, medidos en la imagen grande. box = (x, y, w, h) en píxeles de `big`."""
        cv2, np = self.cv2, self.np
        H, W = big.shape[:2]
        x, y, w, h = box
        d: dict = {"px": int(round(w))}
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(x + w)), min(H, int(y + h))
        if x1 - x0 >= 12 and y1 - y0 >= 12:
            st = region_stats(_gray_resized(big[y0:y1, x0:x1], min(256, x1 - x0)))
            d["lum"], d["nit"] = st["lum"], st["nit"]
        else:
            d["lum"], d["nit"] = -1.0, -1.0
        # recorte cuadrado con margen, enderezado por la línea de los ojos, a 256 px (lo que espera la malla)
        roll = math.atan2(eyes[1][1] - eyes[0][1], eyes[1][0] - eyes[0][0]) if eyes else 0.0
        cx, cy = x + w / 2, y + h / 2
        side = 1.5 * max(w, h)
        M = cv2.getRotationMatrix2D((cx, cy), math.degrees(roll), 256.0 / side)
        M[0, 2] += 128 - cx
        M[1, 2] += 128 - cy
        crop = cv2.warpAffine(big, M, (256, 256), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        blob = cv2.dnn.blobFromImage(crop, 1 / 255.0, (256, 256), swapRB=True)
        self.mesh.setInput(blob)
        outs = self.mesh.forward(self.mesh_out)
        pts = None
        presence = 0.0
        for o in outs:
            if o.size == 1434:
                pts = o.reshape(478, 3).astype(np.float64)
            elif o.size == 1 and o.ndim == 4:
                presence = 1.0 / (1.0 + math.exp(-float(o.ravel()[0])))
        d["roll"] = roll
        if pts is None:
            d["ojoI"] = d["ojoD"] = -1.0
            return d
        d["malla"] = round(presence, 3)
        d["ojoD"] = max(0.0, EYE_GAIN * _eye_ratio(pts[list(EYE_R), :2]) + EYE_BIAS)
        d["ojoI"] = max(0.0, EYE_GAIN * _eye_ratio(pts[list(EYE_L), :2]) + EYE_BIAS)
        # giro (rad): de mejilla a mejilla (yaw) y de frente a barbilla (pitch, + = mirando abajo), en 3D
        lc, rc, top, chin = pts[234], pts[454], pts[10], pts[152]
        d["yaw"] = math.atan2(rc[2] - lc[2], rc[0] - lc[0])
        d["pitch"] = math.atan2(chin[2] - top[2], chin[1] - top[1])
        return d

    # ---- huella de la foto (parecido)
    def signature(self, img):
        """(huella de contenido, la toma en pequeño) de una foto: con eso se compara con las anteriores."""
        cv2, np = self.cv2, self.np
        k = 640.0 / max(img.shape[:2])
        small = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else img
        blob = cv2.dnn.blobFromImage(small, 1 / 255.0, (224, 224), swapRB=True)     # (0-1: medido, separa mucho mejor)
        self.emb.setInput(blob)
        v = self.emb.forward().ravel().astype(np.float64)
        n = float(np.linalg.norm(v))
        g = cv2.cvtColor(cv2.resize(small, (48, 32), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g.astype(np.float32), (0, 0), 1.0)
        return (v / n if n > 0 else v), g

    def distance(self, a, b) -> Tuple[float, float]:
        """(contenido, composición) entre dos fotos, en crudo."""
        return distance(a, b)

    # ---- una foto entera (lo que hace «rfui cull» por línea)
    def analyze_file(self, path, prev: list, raw_metrics: bool = False) -> dict:
        o, cur = self.measure_file(path)
        link(o, cur, prev, raw_metrics)
        return o

    def measure_file(self, path):
        """Todo lo de UNA foto que no depende de las demás: (fila, huella o None). Se puede hacer en paralelo (un
        Engine por hilo); el parecido con las anteriores lo añade link(), en el orden de la toma."""
        cv2 = self.cv2
        o: dict = {"foto": Path(path).name}
        big, info = read_raw(path)
        if big is None:
            o["error"] = "sin vista previa"
            return o, None
        o.update(info)
        H, W = big.shape[:2]
        k = PREVIEW / max(H, W)
        img = cv2.resize(big, (max(1, int(W * k)), max(1, int(H * k))), interpolation=cv2.INTER_AREA) if k < 1 else big
        g = region_stats(_gray_resized(img, 768))
        for key in ("lum", "negro", "quemado", "desv", "nit"):
            o[key] = g.get(key, -1.0)
        sx, sy = W / img.shape[1], H / img.shape[0]
        caras = []
        # (Probado el 24-nov-2026: buscar también con la foto girada 90° cuando no sale ninguna cara encuentra a alguien
        # tumbado, pero de 6 «caras» aceptadas en 3 eventos 5 eran flores, andamios o manos: no se usa.)
        for (x, y, w, h, er, el, sc) in self.detect(img):
            box = (x * sx, y * sy, w * sx, h * sy)
            d = self.measure(big, box, ((er[0] * sx, er[1] * sy), (el[0] * sx, el[1] * sy)))
            d.update({"x": box[0] / W, "y": box[1] / H, "w": box[2] / W, "h": box[3] / H, "conf": round(sc, 3)})
            d["calidad"] = _quality(d)
            caras.append(d)
        o["caras"] = caras
        o["personas"] = len(caras)
        return o, self.signature(img)


def distance(a, b) -> Tuple[float, float]:
    """(contenido, composición) entre dos huellas, en crudo."""
    import numpy as np
    return float(np.linalg.norm(a[0] - b[0])), _struct_dist(a[1], b[1])


def link(o: dict, cur, prev: list, raw_metrics: bool = False) -> None:
    """Parecido de la foto con las BACK anteriores (en el orden de la toma); `prev` guarda sus huellas."""
    if cur is not None:
        ds, crudo = [], []
        for p in reversed(prev):
            if p is None:
                ds.append(-1)
                crudo.append(None)
            else:
                e, s = distance(cur, p)
                ds.append(round(_to_vision(e, s), 4))
                crudo.append([round(e, 4), round(s, 4)])
        if ds and ds[0] >= 0:
            o["dist_prev"] = ds[0]
        if ds:
            o["dists"] = ds
        if raw_metrics and crudo:
            o["crudo"] = crudo                       # (contenido, composición) sin convertir, para calibrar
    prev.append(cur)
    del prev[:-BACK]


def _quality(f: dict) -> float:
    """Nota de la cara (0-1), en lugar de la «calidad de captura» de Vision: una cara bien vista, de frente, nítida y
    con los ojos abiertos saca ≈0,6; girada, tapada, movida o con los ojos cerrados, menos. Solo sirve para comparar
    a la MISMA persona entre dos tomas casi iguales (como en el Mac)."""
    conf = float(f.get("conf", 0.0))
    q = 0.75 * max(0.0, min(1.0, (conf - 0.5) / 0.45))
    q *= 1.0 - 0.6 * min(1.0, abs(float(f.get("yaw", 0.0))) / 1.0)
    q *= 1.0 - 0.4 * min(1.0, max(0.0, abs(float(f.get("pitch", 0.0))) - 0.2) / 0.6)
    eyes = [e for e in (f.get("ojoI", -1), f.get("ojoD", -1)) if e >= 0]
    if eyes:
        q *= 0.7 + 0.3 * min(1.0, min(eyes) / 0.18)
    return round(max(0.0, min(1.0, q)), 4)


# ------------------------------------------------------------------------------------------- lo que usa seleccion.py
def analyze(paths: List[str], log: Callable[[str], None] = lambda m: None,
            should_stop: Callable[[], bool] = lambda: False,
            progress: Callable[[float], None] = lambda f: None, raw_metrics: bool = False) -> List[dict]:
    """Una fila por foto (en el orden dado: el de la toma), con las mismas claves que «rfui cull»."""
    rows: List[dict] = []
    if not paths:
        return rows
    import threading
    from concurrent.futures import ThreadPoolExecutor
    local = threading.local()
    engines: List[Engine] = []
    lock = threading.Lock()

    def one(p):                                      # en un hilo: su propio Engine (no se comparten)
        if should_stop():
            return {"foto": Path(p).name, "error": "Cancelado."}, None
        eng_ = getattr(local, "eng", None)
        if eng_ is None:
            eng_ = local.eng = Engine()
            with lock:
                engines.append(eng_)
        try:
            return eng_.measure_file(p)
        except Exception as e:  # noqa: BLE001  (una foto rara no para el análisis de las demás)
            return {"foto": Path(p).name, "error": f"{type(e).__name__}: {e}"[:120]}, None

    prev: list = []
    t0 = time.monotonic()
    n_workers = workers(len(paths))
    pool = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="seleccion") if n_workers > 1 else None
    try:
        # en orden y con pocas fotos por delante (cada vista previa grande ocupa ≈60 MB en memoria)
        it = iter(paths)
        pend = []
        if pool:
            for p in it:
                pend.append(pool.submit(one, p))
                if len(pend) >= 2 * n_workers:
                    break
        k = 0
        while True:
            if pool:
                if not pend:
                    break
                o, cur = pend.pop(0).result()
                nxt = next(it, None)
                if nxt is not None:
                    pend.append(pool.submit(one, nxt))
            else:
                p = next(it, None)
                if p is None:
                    break
                o, cur = one(p)
            if should_stop():
                raise RuntimeError("Cancelado.")
            k += 1
            link(o, cur, prev, raw_metrics)
            rows.append(o)
            _after_one(k, len(paths), t0, log, progress)
    finally:
        if pool:
            for f in pend:
                f.cancel()
            pool.shutdown(wait=True)
    if rows and all(r.get("error") for r in rows):
        raise RuntimeError("ninguna foto trae una vista previa que se pueda leer")
    return rows


def workers(n_fotos: int) -> int:
    """Cuántas fotos se miden a la vez: la mitad de los núcleos, como mucho 4 (OpenCV ya usa varios por foto y cada
    vista previa grande ocupa memoria). RAWFLOW_IA_HILOS lo fija (1 = de una en una, como antes)."""
    import os
    try:
        n = int(os.environ.get("RAWFLOW_IA_HILOS", "0"))
    except ValueError:
        n = 0
    if n <= 0:
        n = max(1, min(4, (os.cpu_count() or 2) // 2))
    return max(1, min(n, n_fotos))


def _after_one(k: int, total: int, t0: float, log, progress) -> None:
    progress(k / total)
    if k % 25 == 0 or k == total:
        el = time.monotonic() - t0
        log(f"Selección IA: {k} de {total} fotos analizadas"
            + (f"  ·  quedan {int((total - k) * el / k)} s" if k < total else ""))


def rescue(rows: List[dict], req: Dict[int, List[list]], path_of: Callable[[str], Path]) -> int:
    """Vuelve a buscar las caras que no se vieron en una toma, cada una en su sitio (zona pequeña y ampliada: ahí sí
    se ven), y las añade; las que no aparecen quedan como «ocultas». Como «rfui caras». Devuelve cuántas encontró."""
    if not req:
        return 0
    import cv2
    eng = Engine()
    n = 0
    for i, boxes in sorted(req.items()):
        big, _ = read_raw(path_of(rows[i]["foto"]))
        if big is None:
            continue
        H, W = big.shape[:2]
        have = rows[i].setdefault("caras", [])
        for b in boxes:
            bx, by, bw, bh = b[0] * W, b[1] * H, b[2] * W, b[3] * H
            x0, y0 = max(0, int(bx - 0.6 * bw)), max(0, int(by - 0.6 * bh))
            x1, y1 = min(W, int(bx + 1.6 * bw)), min(H, int(by + 1.6 * bh))
            if x1 - x0 < 24 or y1 - y0 < 24:
                rows[i].setdefault("ocultas", []).append(b)
                continue
            roi = big[y0:y1, x0:x1]
            k = min(4.0, max(1.0, 480.0 / max(roi.shape[:2])))                 # ampliada: la cara, a buen tamaño
            if k > 1.05:
                roi = cv2.resize(roi, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
            else:
                k = 1.0
            best, bd = None, 1e9
            for (x, y, w, h, er, el, sc) in eng.detect(roi, RESCUE_SCORE):
                fx, fy, fw, fh = x0 + x / k, y0 + y / k, w / k, h / k
                d = math.hypot(fx + fw / 2 - (bx + bw / 2), fy + fh / 2 - (by + bh / 2))
                if d <= 0.6 * bw and 0.5 < fw / max(bw, 1e-6) < 2 and d < bd:
                    bd = d
                    best = ((fx, fy, fw, fh), ((x0 + er[0] / k, y0 + er[1] / k), (x0 + el[0] / k, y0 + el[1] / k)), sc)
            if best is None:
                rows[i].setdefault("ocultas", []).append(b)
                continue
            box, eyes, sc = best
            f = eng.measure(big, box, eyes)
            f.update({"x": box[0] / W, "y": box[1] / H, "w": box[2] / W, "h": box[3] / H, "conf": round(sc, 3),
                      "rescatada": True})
            f["calidad"] = _quality(f)
            cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
            if any(abs(cx - (g["x"] + g["w"] / 2)) < 0.5 * g["w"] and abs(cy - (g["y"] + g["h"] / 2)) < 0.5 * g["h"]
                   for g in have):
                continue                                                        # ya estaba (la zona la vuelve a ver)
            have.append(f)
            n += 1
    return n

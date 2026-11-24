"""Hoja de contactos para calibrar la Selección IA (solo lee fotos): por cada par X:K, X entera, K entera y la cara de
la persona que MÁS cambia entre las dos (ojos y calidad), recortada de la vista previa del RAW (no de los JPEG de Imagen:
van recortados y enderezados y las cajas no cuadran).

  .venv/bin/python tools/calibrar/hoja.py salida.jpg DATOS.jsonl CARPETA_RAW X:K [X:K …]
    DATOS.jsonl  tests/datos/seleccion/… o la salida de `rfui cull`
    CARPETA_RAW  «…/1 Original» de ese evento (busca también en «Fotos buenas» y «Fotos malas»)
La primera vez compila tools/calibrar/prevjpg (vista previa del RAW → JPEG de 3200 px)."""
import json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from rawflow import seleccion as S  # noqa: E402
from PySide6.QtCore import QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

out, data, raw = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
pairs = [p.split(":") for p in sys.argv[4:]]
app = QGuiApplication([])
rows = {Path(r["foto"]).stem: r for r in map(json.loads, (l for l in open(data, encoding="utf-8") if l.startswith("{")))}
tool, cache = HERE / "prevjpg", out.with_name(out.stem + "_vistas")
if not tool.exists():
    subprocess.run(["swiftc", "-O", str(HERE / "prevjpg.swift"), "-o", str(tool)], check=True)
cache.mkdir(exist_ok=True)


def raw_path(stem):
    name = rows[stem]["foto"]
    return next((d / name for d in (raw, raw / "Fotos buenas", raw / "Fotos malas") if (d / name).exists()), raw / name)


need = [n for p in pairs for n in p if not (cache / f"{n}.jpg").exists()]
if need:
    subprocess.run([str(tool), str(cache), "3200"] + [str(raw_path(n)) for n in need], check=True)


def crop(im, f, pad=0.3):
    W, H = im.width(), im.height()
    x0, y0, w, h = f["x"] * W, (1 - f["y"] - f["h"]) * H, f["w"] * W, f["h"] * H
    return im.copy(QRect(int(x0 - w * pad), int(y0 - h * pad), int(w * (1 + 2 * pad)), int(h * (1 + 2 * pad))))


tw, th = 300, 210
sheet = QImage(4 * tw, len(pairs) * (th + 24), QImage.Format.Format_RGB32)
sheet.fill(QColor("#111"))
pt = QPainter(sheet)
pt.setFont(QFont("Helvetica", 11))
for r_, (x, k) in enumerate(pairs):
    fx = S._people(rows[x])
    fk = S.pair_faces(fx, [q for q in rows[k].get("caras", []) if S._area(q) >= 0.0004])
    cand = sorted(((abs((S._openness(a) or 0) - (S._openness(b) or 0)) + abs(a.get("calidad", 0) - b.get("calidad", 0)), a, b)
                   for a, b in zip(fx, fk) if b is not None), key=lambda c: -c[0])
    ix, ik = QImage(str(cache / f"{x}.jpg")), QImage(str(cache / f"{k}.jpg"))
    cells = [(ix, x), (ik, k)]
    if cand:
        _, a, b = cand[0]
        e = lambda c: f"ojos {c.get('ojoI', -1):.2f}/{c.get('ojoD', -1):.2f} q{c.get('calidad', 0):.2f} n{c.get('nit', 0):.0f}"  # noqa: E731
        cells += [(crop(ix, a), f"{x}: {e(a)}"), (crop(ik, b), f"{k}: {e(b)}")]
    for c, (im, t) in enumerate(cells):
        sc = im.scaled(tw - 6, th - 6, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pt.drawImage(c * tw + 3, r_ * (th + 24) + 3, sc)
        pt.setPen(QColor("#d7ff4f"))
        pt.drawText(QRectF(c * tw + 4, r_ * (th + 24) + th, tw, 20), Qt.AlignmentFlag.AlignLeft, t)
pt.end()
sheet.save(str(out), "JPG", 85)
print("ok", out)

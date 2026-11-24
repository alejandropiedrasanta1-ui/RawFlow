"""Genera los gráficos de Windows a partir del icono vectorial de RawFlow (rawflow/widgets.py):

    rawflow/RawFlow.ico          icono con todos los tamaños (16…256), nítido en Escritorio, barra y Explorador
    build/windows/wizard.bmp     imagen lateral del instalador
    build/windows/wizard_s.bmp   imagen pequeña (esquina) del instalador

Uso:  python tools/hacer_icono.py
"""
import struct
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

TAMANOS = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def _dib(img: QImage) -> bytes:
    """Entrada BMP de un .ico: cabecera de 40 bytes, BGRA de abajo arriba y máscara AND (vacía: manda el alfa)."""
    n = img.width()
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    filas = [bytes(img.constScanLine(y))[: n * 4] for y in range(n - 1, -1, -1)]
    mascara = bytes(((n + 31) // 32) * 4) * n
    cab = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, n * n * 4 + len(mascara), 0, 0, 0, 0)
    return cab + b"".join(filas) + mascara


def hacer_ico(destino: Path, icon_pixmap) -> None:
    datos = []
    for n in TAMANOS:
        img = icon_pixmap(n).toImage()
        datos.append(_png(img) if n >= 256 else _dib(img))      # 256 en PNG (como hace Windows); el resto, BMP
    out = struct.pack("<HHH", 0, 1, len(TAMANOS))
    pos = 6 + 16 * len(TAMANOS)
    for n, d in zip(TAMANOS, datos):
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(d), pos)
        pos += len(d)
    destino.write_bytes(out + b"".join(datos))


def hacer_bmp(destino: Path, ancho: int, alto: int, lado: int, fondo: str, icon_pixmap) -> None:
    img = QImage(ancho, alto, QImage.Format.Format_RGB32)
    img.fill(QColor(fondo))
    p = QPainter(img)
    p.drawPixmap((ancho - lado) // 2, (alto - lado) // 2, icon_pixmap(lado))
    p.end()
    img.save(str(destino), "BMP")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(["icono", "-platform", "offscreen"])  # noqa: F841
    from rawflow import theme as T
    from rawflow.widgets import icon_pixmap
    hacer_ico(RAIZ / "rawflow" / "RawFlow.ico", icon_pixmap)
    out = RAIZ / "build" / "windows"
    out.mkdir(parents=True, exist_ok=True)
    hacer_bmp(out / "wizard.bmp", 328, 628, 220, T.BG, icon_pixmap)          # 2× (pantallas HiDPI)
    hacer_bmp(out / "wizard_s.bmp", 110, 110, 110, "#ffffff", icon_pixmap)   # cabecera clara del asistente
    print("Icono:", RAIZ / "rawflow" / "RawFlow.ico", "·", len(TAMANOS), "tamaños")
    return 0


if __name__ == "__main__":
    sys.exit(main())

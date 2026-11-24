"""Ventana «Fotos malas»: ver lo que apartó la Selección IA (miniatura y motivo) y recuperar las que sí valen.

Recuperar = pasar la foto a «1 Original/Fotos buenas» (seleccion.restore). Al cerrar, RawFlow ofrece procesarlas
(«Procesar las recuperadas»): DxO, Imagen y Retouch4me SOLO con esas, y nunca se vuelven a apartar.
Las miniaturas salen de la vista previa JPEG que trae cada RAW (sin programas externos: vale en Mac y Windows).
"""
from __future__ import annotations

import mmap
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QBuffer, QByteArray, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout)

from . import seleccion
from . import theme as T

THUMB = 220

QSS = f"""
QDialog {{ background: {T.BG}; }}
QListWidget {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 12px; padding: 8px; }}
QListWidget::item {{ color: {T.TEXT}; border-radius: 8px; padding: 6px; }}
QListWidget::item:selected {{ background: {T.SURFACE_2}; border: 2px solid {T.ACCENT}; color: {T.TEXT}; }}
#primary {{ padding: 9px 22px; font-size: 14px; border-radius: 10px; }}
"""


def preview_image(path: Path, side: int = THUMB) -> Optional[QImage]:
    """La vista previa JPEG incrustada en el RAW, reducida a `side` px de lado mayor y ya derecha. None si no trae."""
    from . import seleccion_win as sw                  # (lectura del TIFF en Python puro: no necesita OpenCV)
    try:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
            t = sw._tiff(m)
            cands = list(t["jpegs"]) if t else []
            cands = [(o, ln) for o, ln in cands if 0 < o < len(m) - 4 and bytes(m[o:o + 2]) == b"\xff\xd8"]
            if not cands:
                cands = sw._scan_jpegs(m)
            orient = t["orient"] if t else 1
            best = None
            for o, ln in sorted(cands, key=lambda c: c[1]):       # la más pequeña que llegue al tamaño
                data = QByteArray(bytes(m[o:o + min(ln, len(m) - o)]))
                buf = QBuffer(data)
                r = QImageReader(buf, b"jpg")
                r.setAutoTransform(False)
                sz = r.size()
                if not sz.isValid():
                    continue
                best = (data, sz)
                if max(sz.width(), sz.height()) >= side:
                    break
    except (OSError, ValueError):
        return None
    if best is None:
        return None
    data, sz = best
    buf = QBuffer(data)                                # (que viva mientras se lee)
    r = QImageReader(buf, b"jpg")
    r.setAutoTransform(False)
    k = min(1.0, side / max(sz.width(), sz.height()))
    r.setScaledSize(QSize(max(1, int(sz.width() * k)), max(1, int(sz.height() * k))))
    img = r.read()
    if img.isNull():
        return None
    ang = {3: 180, 6: 90, 8: -90}.get(orient)
    return img.transformed(QTransform().rotate(ang)) if ang else img


class ThumbWorker(QThread):
    """Carga las miniaturas en segundo plano (la ventana se abre al momento)."""
    thumb = Signal(int, QImage)

    def __init__(self, paths: List[Path], parent=None):
        super().__init__(parent)
        self.paths = paths
        self.stop = False

    def run(self):
        for i, p in enumerate(self.paths):
            if self.stop:
                return
            img = preview_image(p)
            if img is not None:
                self.thumb.emit(i, img)


class MalasDialog(QDialog):
    def __init__(self, event_dir: Path, parent=None):
        super().__init__(parent)
        from . import core
        self.raw = Path(event_dir) / core.RAW_SUBFOLDER
        self.restored = 0
        self.setWindowTitle("Fotos malas")
        self.setMinimumSize(900, 620)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        lay.addWidget(QLabel("SELECCIÓN IA  ·  FOTOS MALAS", objectName="sectionTitle"))
        self.intro = QLabel("", objectName="muted")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 24, THUMB + 64))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.itemSelectionChanged.connect(self._sel_changed)
        self.grid.itemDoubleClicked.connect(self._big)
        lay.addWidget(self.grid, 1)

        row = QHBoxLayout()
        self.msg = QLabel("", objectName="faint")
        row.addWidget(self.msg, 1)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.back = QPushButton("Recuperar", objectName="primary")
        self.back.clicked.connect(self._restore)
        row.addWidget(self.back)
        lay.addLayout(row)
        self.worker: Optional[ThumbWorker] = None
        self._fill()

    def _fill(self):
        if self.worker:
            self.worker.stop = True
            self.worker.wait(3000)
        self.grid.clear()
        fotos = seleccion.bad_photos(self.raw)
        self.paths = [p for p, _ in fotos]
        blank = QPixmap(THUMB, int(THUMB * 2 / 3))
        blank.fill(Qt.GlobalColor.transparent)
        for p, why in fotos:
            it = QListWidgetItem(QIcon(blank), f"{p.stem}\n{seleccion.kind(why) or why[:28]}")
            it.setToolTip(f"{p.name}\n{why}" if why else p.name)
            it.setData(Qt.ItemDataRole.UserRole, p.name)
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.grid.addItem(it)
        n = len(fotos)
        self.intro.setText(
            (f"{n} foto{'s' if n != 1 else ''} apartada{'s' if n != 1 else ''} en «1 Original/Fotos malas» (ninguna se "
             "borró). Elige las que sí valen y pulsa «Recuperar»: pasan a «Fotos buenas», RawFlow las procesa y no "
             "las vuelve a apartar. Doble clic para verla grande.") if n else
            "No queda ninguna foto en «Fotos malas».")
        self._sel_changed()
        if self.paths:
            self.worker = ThumbWorker(list(self.paths), self)
            self.worker.thumb.connect(self._set_thumb)
            self.worker.start()

    def _set_thumb(self, i: int, img: QImage):
        it = self.grid.item(i)
        if it is not None:
            it.setIcon(QIcon(QPixmap.fromImage(img)))

    def _sel_changed(self):
        k = len(self.grid.selectedItems())
        self.back.setEnabled(k > 0)
        self.back.setText(f"Recuperar {k}" if k else "Recuperar")
        if self.restored:
            self.msg.setText(f"✓  {self.restored} recuperada{'s' if self.restored != 1 else ''} en «Fotos buenas».")

    def _restore(self):
        names = [it.data(Qt.ItemDataRole.UserRole) for it in self.grid.selectedItems()]
        self.restored += seleccion.restore(self.raw, names)
        self._fill()

    def _big(self, it: QListWidgetItem):
        p = self.raw / seleccion.DIR_NAME / it.data(Qt.ItemDataRole.UserRole)
        img = preview_image(p, 1400)
        if img is None:
            return
        d = QDialog(self)
        d.setWindowTitle(f"{p.name} · {it.toolTip().splitlines()[-1]}")
        d.setStyleSheet(f"QDialog {{ background: {T.BG}; }}")
        v = QVBoxLayout(d)
        lab = QLabel()
        lab.setPixmap(QPixmap.fromImage(img).scaled(1200, 800, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))
        v.addWidget(lab)
        d.exec()

    def done(self, r):
        if self.worker:
            self.worker.stop = True
            self.worker.wait(3000)
        super().done(r)

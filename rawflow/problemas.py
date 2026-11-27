"""Registro de problemas (27-nov-2026, a petición): cada reintento, fallo, aviso o captura de fallo de un paso se
apunta también en «problemas.log» (junto a registro.log) con el evento y el paso, y un icono abajo a la derecha de
la ventana de RawFlow lo enseña, con cuántos hay sin ver. Así se ve de un vistazo qué falló y se puede copiar para
arreglarlo, sin buscar en las miles de líneas del registro."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme as T

MAX_BYTES = 1_000_000

# lo que cuenta como problema (en el texto tal como va al registro)
_START = ("Se trabó", "⚠", "=== FIN ERROR", "✗")
_HAS = ("(captura del fallo:", "lo repito aquí", "no salió", "reintento", "vuelvo a pedirlo", "no se abrió",
        "Windows no deja", "No consigo", "no consigo", "bloqueada o apagada", "lo preparado antes no sirve",
        "Reparando…", "] error", "no se pegó", "no se aplicó")


def is_problem(text: str) -> bool:
    t = text.strip().lstrip("·").strip()
    if not t or t.startswith("=== FIN OK"):
        return False
    return t.startswith(_START) or any(k in t for k in _HAS)


class Recorder:
    """Lo usa MainWindow._log: sabe el evento y el paso en marcha para que cada problema diga dónde fue."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.event = ""
        self.step = ""

    def feed(self, text: str) -> None:
        t = text.strip()
        if t.startswith("=== EVENTO"):
            self.event = Path(t[len("=== EVENTO"):].split("  (")[0].strip()).name
            self.step = ""
        elif t.startswith("[paso ") and "]" in t:
            self.step = t[1:t.index("]")]
        if not is_problem(text):
            return
        where = " · ".join(x for x in (self.event, self.step) if x)
        line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + (f"[{where}]  " if where else "") + t.lstrip("·").strip()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_suffix(".anterior.log"))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def seed(path: Path, log_path: Path) -> bool:
    """Primera vez (no hay problemas.log): se llena con los problemas que ya estaban en registro.log, con su hora."""
    path, log_path = Path(path), Path(log_path)
    if path.exists() or not log_path.exists():
        return False
    r = Recorder(path)
    out = []
    try:
        for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp, text = raw[:19], raw[21:]
            before = r.path
            r.path = Path(os.devnull)                # solo para seguir evento y paso
            r.feed(text)
            r.path = before
            if is_problem(text):
                where = " · ".join(x for x in (r.event, r.step) if x)
                out.append(stamp + "  " + (f"[{where}]  " if where else "") + text.strip().lstrip("·").strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(x + "\n" for x in out[-2000:]), encoding="utf-8")
        return True
    except OSError:
        return False


def read_lines(path: Path) -> List[str]:
    try:
        return [x for x in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    except OSError:
        return []


def _open_path(p: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(p))                     # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    except Exception:  # noqa: BLE001
        pass


class ProblemsButton(QWidget):
    """Icono redondo abajo a la derecha: triángulo de aviso y, si hay problemas sin ver, su número en rojo."""
    clicked = Signal()
    SIZE = 44

    def __init__(self, path: Path, settings, parent=None):
        super().__init__(parent)
        self.path, self.settings = Path(path), settings
        self.total, self.unseen, self._hover = 0, 0, False
        self.setFixedSize(self.SIZE + 14, self.SIZE + 10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Problemas: reintentos y fallos de los pasos")
        self._size = -1
        self._timer = QTimer(self, interval=2000, timeout=self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        if size == self._size:
            return
        self._size = size
        self.total = len(read_lines(self.path))
        seen = int(self.settings.value("problemas_vistos", 0) or 0)
        if seen > self.total:                        # el archivo se rotó o se vació
            seen = 0
            self.settings.setValue("problemas_vistos", 0)
        self.unseen = self.total - seen
        self.setToolTip(f"Problemas: {self.unseen} sin ver de {self.total}" if self.total else
                        "Problemas: ninguno por ahora")
        self.update()

    def mark_seen(self) -> None:
        self.settings.setValue("problemas_vistos", self.total)
        self.unseen = 0
        self.update()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.SIZE
        c = QRectF(4, 6, d, d)
        p.setPen(QPen(QColor(T.BORDER_HI if self._hover else T.BORDER), 1))
        p.setBrush(QColor(T.SURFACE_2 if self._hover else T.SURFACE))
        p.drawEllipse(c)
        col = QColor(T.DANGER if self.unseen else T.MUTED)
        cx, cy = c.center().x(), c.center().y()
        tri = QPainterPath()
        tri.moveTo(cx, cy - 10)
        tri.lineTo(cx + 11, cy + 9)
        tri.lineTo(cx - 11, cy + 9)
        tri.closeSubpath()
        p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tri)
        p.drawLine(int(cx), int(cy - 3), int(cx), int(cy + 3))
        p.drawPoint(int(cx), int(cy + 6))
        if self.unseen:
            txt = str(self.unseen) if self.unseen < 100 else "99+"
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            w = max(18, p.fontMetrics().horizontalAdvance(txt) + 10)
            r = QRectF(self.width() - w - 1, 1, w, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.DANGER))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class ProblemsDialog(QDialog):
    """Lista de problemas (lo más nuevo arriba). Doble clic en uno con captura la abre; «Copiar» deja todo en el
    portapapeles para pegarlo y arreglarlo."""

    def __init__(self, path: Path, log_path: Path, diag_dir: Path, parent=None):
        super().__init__(parent)
        self.path, self.log_path, self.diag_dir = Path(path), Path(log_path), Path(diag_dir)
        self.setWindowTitle("RawFlow · Problemas")
        self.resize(980, 560)
        self.setStyleSheet(f"QDialog {{ background: {T.BG}; }}"
                           f"QListWidget {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 12px;"
                           f" padding: 6px; font-size: 12px; }}"
                           f"QListWidget::item {{ padding: 5px 6px; border-bottom: 1px solid {T.BORDER}; }}"
                           f"QListWidget::item:selected {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        self.head = QLabel()
        self.head.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {T.TEXT};")
        lay.addWidget(self.head)
        sub = QLabel("Cada reintento, fallo o aviso de los pasos, con el evento y el paso donde pasó. "
                     "Doble clic en uno con 📷 para ver la captura del fallo.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {T.MUTED};")
        lay.addWidget(sub)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(self._open_item)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for txt, fn in (("Copiar todo", self._copy), ("Abrir capturas", lambda: _open_path(self.diag_dir)),
                        ("Abrir registro completo", lambda: _open_path(self.log_path))):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.copied = QLabel("")
        self.copied.setStyleSheet(f"color: {T.OK};")
        row.addWidget(self.copied)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        lines = read_lines(self.path)
        self.lines = lines
        self.list.clear()
        self.head.setText(f"{len(lines)} problema{'s' if len(lines) != 1 else ''} registrado{'s' if len(lines) != 1 else ''}"
                          if lines else "Sin problemas registrados")
        for ln in reversed(lines):
            shot = self._shot(ln)
            it = QListWidgetItem(("📷  " if shot else "") + ln)
            if shot:
                it.setData(Qt.ItemDataRole.UserRole, str(shot))
            if "Se trabó" in ln or "FIN ERROR" in ln or "] error" in ln:
                it.setForeground(QColor(T.DANGER))
            self.list.addItem(it)

    def _shot(self, line: str):
        if "captura del fallo:" not in line:
            return None
        name = line.split("captura del fallo:", 1)[1].strip().split(" ")[0]
        p = self.diag_dir / name
        return p if p.exists() else None

    def _open_item(self, it: QListWidgetItem) -> None:
        p = it.data(Qt.ItemDataRole.UserRole)
        if p:
            _open_path(Path(p))

    def _copy(self) -> None:
        QApplication.clipboard().setText("\n".join(self.lines))
        self.copied.setText("Copiado ✓")
        QTimer.singleShot(2500, lambda: self.copied.setText(""))

"""Diálogo «Permisos»: pide de una vez todos los permisos de macOS y muestra cuáles faltan."""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)

from . import permissions as P
from . import theme as T


# Permisos que se activan en Ajustes del Sistema (con su interruptor)
SETTINGS_KEYS = ("ax", "screen", "fulldisk")


class PermWorker(QThread):
    """Pide (opcional) y comprueba permisos fuera del hilo de la interfaz:
    los avisos de macOS se quedan esperando a que respondas."""
    status = Signal(str, object)
    finished_all = Signal()

    def __init__(self, perms: List[P.Perm], ask: bool, parent=None):
        super().__init__(parent)
        self.perms, self.ask = perms, ask

    def run(self):
        for p in self.perms:
            ok = False
            try:
                ok = p.check()
                if self.ask and ok is False and p.key not in SETTINGS_KEYS:
                    p.request()
                    ok = p.check()
            except Exception:  # noqa: BLE001
                ok = False
            self.status.emit(p.key, ok)
        self.finished_all.emit()


class PermissionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Permisos de RawFlow")
        self.setMinimumWidth(760)
        self.setStyleSheet(f"QDialog {{ background: {T.BG}; }}")
        self.perms = P.all_perms()
        self.chips: Dict[str, QLabel] = {}
        self.worker: PermWorker | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        lay.addWidget(QLabel("PERMISOS", objectName="sectionTitle"))
        intro = QLabel("Pulsa «Pedir todos» y acepta cada aviso de macOS con «Permitir» o «Aceptar». "
                       "macOS los recuerda: no volverá a preguntar.", objectName="muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for r, p in enumerate(self.perms):
            names = P.section_names()
            t = QLabel(p.title if p.key not in names or names[p.key] == p.title
                       else f"{p.title}  ·  en Ajustes: «{names[p.key]}»")
            t.setStyleSheet("font-weight:600;")
            w = QLabel(p.why, objectName="faint")
            w.setWordWrap(True)
            box = QVBoxLayout()
            box.setSpacing(1)
            box.addWidget(t)
            box.addWidget(w)
            grid.addLayout(box, r, 0)
            chip = QLabel("…", objectName="chip")
            self.chips[p.key] = chip
            grid.addWidget(chip, r, 1, Qt.AlignmentFlag.AlignRight)
            if p.key in SETTINGS_KEYS:
                b = QPushButton("Abrir Ajustes")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(p.request)
                grid.addWidget(b, r, 2)
        grid.setColumnStretch(0, 1)
        lay.addLayout(grid)

        self.note = QLabel(
            "Si algo sale en «Falta» aunque en Ajustes lo veas activado, es la aprobación de una versión "
            "anterior de RawFlow. Pulsa «Reparar y activar»: RawFlow borra las aprobaciones viejas y te lleva "
            "a cada sección de Ajustes; activa RawFlow con tu huella y pasa sola a la siguiente.",
            objectName="faint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        row = QHBoxLayout()
        row.addStretch(1)
        self.recheck = QPushButton("Comprobar")
        self.recheck.clicked.connect(lambda: self._run(False))
        row.addWidget(self.recheck)
        fix = QPushButton("Reparar y activar")
        fix.setToolTip("Borra las aprobaciones viejas de RawFlow y te guía sección por sección.")
        fix.clicked.connect(self._repair)
        row.addWidget(fix)
        relaunch = QPushButton("Reabrir RawFlow")
        relaunch.setToolTip("Grabación de pantalla solo se aplica al reabrir la app.")
        relaunch.clicked.connect(lambda: (P.relaunch(), __import__("PySide6.QtWidgets").QtWidgets.QApplication.quit()))
        row.addWidget(relaunch)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.go = QPushButton("Pedir todos", objectName="primary")
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.clicked.connect(lambda: self._run(True))
        row.addWidget(self.go)
        lay.addLayout(row)
        self._run(False)

    def _repair(self):
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self, "Reparar y activar",
            "RawFlow borrará sus aprobaciones viejas y te llevará a cada sección de Ajustes:\n\n"
            f"1. «{P.section_names()['ax']}»\n2. «{P.section_names()['screen']}»\n"
            f"3. «{P.section_names()['fulldisk']}»\n\n"
            "En cada una activa RawFlow (si no aparece, pulsa «+» y elígela en Aplicaciones) y confirma con "
            "tu huella. RawFlow detecta el cambio y pasa sola a la siguiente. ¿Empezar?")
        if r != QMessageBox.StandardButton.Yes:
            return
        err = P.repair()
        if err:
            QMessageBox.warning(self, "Reparar", f"macOS no dejó borrar alguna aprobación:\n{err}\n\n"
                                "Quítala a mano en Ajustes con «–» y vuelve a añadir RawFlow con «+».")
        self.queue = [p for p in self.perms if p.key in SETTINGS_KEYS]
        self._next_section()

    def _next_section(self):
        from PySide6.QtCore import QTimer
        while self.queue and self.queue[0].check():
            self._set(self.queue.pop(0).key, True)
        if not self.queue:
            self.note.setText("✓ Listo. Si Grabación de pantalla sigue en «Falta», pulsa «Reabrir RawFlow».")
            self._run(True)
            return
        p = self.queue[0]
        name = P.section_names().get(p.key, p.title)
        self.note.setText(f"➜ En Ajustes, «{name}»: activa RawFlow (o «+» → Aplicaciones → RawFlow) y pon tu "
                          f"huella. Esperando…")
        self.note.setStyleSheet(f"color:{T.ACCENT}; font-weight:600;")
        p.request()
        self.wait_n = 0
        if not hasattr(self, "poll"):
            self.poll = QTimer(self, interval=1000, timeout=self._poll_section)
        self.poll.start()

    def _poll_section(self):
        self.wait_n += 1
        p = self.queue[0]
        ok = p.check()
        # Grabación de pantalla no se detecta hasta reabrir RawFlow: se da 40 s y se sigue
        if ok or self.wait_n > (40 if p.key == "screen" else 240):
            self.poll.stop()
            self._set(p.key, ok)
            self.queue.pop(0)
            if p.key == "screen" and not ok:
                self.need_relaunch = True
            self._next_section()

    def _run(self, ask: bool):
        if self.worker:
            return
        for c in self.chips.values():
            c.setText("Pidiendo…" if ask else "…")
        self.go.setEnabled(False)
        self.recheck.setEnabled(False)
        self.worker = PermWorker(self.perms, ask, self)
        self.worker.status.connect(self._set)
        self.worker.finished_all.connect(self._done)
        self.worker.start()

    def _set(self, key: str, ok):
        c = self.chips[key]
        if ok is None:
            c.setText("●  Al abrirla")
            c.setObjectName("chip")
            c.setToolTip("Esa app está cerrada; macOS lo preguntará (o ya lo tiene) cuando RawFlow la use.")
        else:
            c.setText("●  Concedido" if ok else "●  Falta")
            c.setObjectName("chipOk" if ok else "chipWarn")
        c.style().unpolish(c)
        c.style().polish(c)

    def _done(self):
        self.worker = None
        self.go.setEnabled(True)
        self.recheck.setEnabled(True)

    def all_granted(self) -> bool:
        return not any(c.text().endswith("Falta") for c in self.chips.values())

    def closeEvent(self, e):  # noqa: N802
        self.hide()          # solo se oculta; el hilo (si está pidiendo) termina solo
        e.ignore()

    def reject(self):
        self.hide()

    def accept(self):
        self.hide()

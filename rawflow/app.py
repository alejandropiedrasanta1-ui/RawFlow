"""Ventana principal de RawFlow: conectas la memoria, escribes el evento y pulsas Empezar."""
from __future__ import annotations


import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import (QDate, QEasingCurve, QLocale, QRectF, QSettings, Qt, QThread, QTimer, QVariantAnimation,
                            Signal, Slot)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import core
from . import theme as T
from .core import STEP_FINAL, STEP_IMG, STEP_PRES, STEP_RET, STEP_SEL
from .pipeline import (FINAL_DIR, IMAGEN_DIR, JPEG_SIN_DIR, PRES_DIR, STEPS, TOGGLEABLE, Pipeline, event_root,
                       folders, resume_step)
from .timing import Estimator, fmt
from . import anim
from .widgets import (ActivityView, DriveCard, ElidedLabel, EmptyDrives, RippleButton, SlimProgress, app_icon, font,
                      hide_from_capture, icon_pixmap_named, set_button_icon, tint_titlebar)

APP_NAME = "RawFlow"
IMAGEN_APP = Path("/Applications/ImagenAI.app")
ARAMS_APP = Path("/Applications/Retouch4me Arams.app")


# ==========================================================================
# Hilos
# ==========================================================================
class DriveWatcher(QThread):
    """Vigila las memorias fuera de la interfaz (diskutil puede tardar y congelaría la ventana)."""
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        last = None
        while not self._stop:
            try:
                drives = core.list_drives()
                keys = sorted(d.key for d in drives)
                if keys != last:
                    # olvidar la info de volúmenes que ya no están (otra tarjeta puede montarse igual)
                    for k in list(core._MAC_INFO_CACHE):
                        if k not in keys:
                            core._MAC_INFO_CACHE.pop(k, None)
                    last = keys
                    self.changed.emit(drives)
            except Exception:  # noqa: BLE001
                pass
            for _ in range(8):                  # ~0,8 s entre vueltas, cortable
                if self._stop:
                    return
                time.sleep(0.1)


class UpdateWorker(QThread):
    """Mira en GitHub si hay versión nueva (o la descarga), sin parar la interfaz."""
    found = Signal(dict)                # hay una versión más nueva: sus datos
    progress = Signal(float)            # 0..1 de la descarga
    ready = Signal(str, str)            # (ruta del instalador, error)

    def __init__(self, parent=None, info: dict = None):
        super().__init__(parent)
        self.info = info                # None = solo mirar; con datos = descargar esa versión
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from . import __version__, update
        if self.info is None:
            info = update.check(__version__)
            if info and not self._stop:
                self.found.emit(info)
            return
        try:
            path = update.download(self.info, self.progress.emit, lambda: self._stop)
            self.ready.emit(str(path), "")
        except Exception as e:  # noqa: BLE001
            self.ready.emit("", str(e))


class ScanWorker(QThread):
    progress = Signal(str, int, int)
    finished_scan = Signal(str, list)

    def __init__(self, key: str, parent=None, paths=None):
        super().__init__(parent)
        self.key = key
        self.paths = paths              # carpeta o fotos arrastradas (si no, la memoria `key`)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        last = [0.0]

        def prog(visited, found):
            now = time.monotonic()
            if now - last[0] > 0.08:
                last[0] = now
                self.progress.emit(self.key, visited, found)

        if self.paths:
            files = core.scan_paths(self.paths, self.key, prog, lambda: self._stop)
        else:
            files = core.scan_drive(self.key, prog, lambda: self._stop)
        if not self._stop:
            self.finished_scan.emit(self.key, files)


# ==========================================================================
# Piezas
# ==========================================================================
def panel() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    return f


def label(text: str, name: str = "") -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    return lb


class Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = label("—", "bigNumber")
        self.value.setFont(font(32, QFont.Weight.Bold))
        lay.addWidget(self.value)
        lay.addWidget(label(caption, "bigLabel"))
        self._n, self._fmt = 0.0, None
        self._a = QVariantAnimation(self, duration=520, easingCurve=QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(lambda v: self._fmt and self.value.setText(self._fmt(float(v))))

    def set_number(self, n: float, fmt) -> None:
        """Pone un número; si cambió y se ve, cuenta desde el anterior hasta el nuevo (no salta de golpe)."""
        n = float(n)
        self._fmt = fmt
        if n == self._n or not self.isVisible() or T.reduce_motion():
            self._a.stop()
            self._n = n
            self.value.setText(fmt(n))
            return
        start = float(self._a.currentValue()) if self._a.state() == QVariantAnimation.State.Running else self._n
        self._a.stop()
        self._n = n
        self._a.setStartValue(start)
        self._a.setEndValue(n)
        self._a.start()


def _qc(c: str) -> QColor:
    """«#RRGGBB», «transparent» o «rgba(r,g,b,a)» → QColor (para animar los colores del tema)."""
    c = c.strip()
    if c.startswith("rgba("):
        r, g, b, a = (float(x) for x in c[5:-1].split(","))
        q = QColor(int(r), int(g), int(b))
        q.setAlphaF(a)
        return q
    if c == "transparent":
        q = QColor(0, 0, 0)
        q.setAlphaF(0.0)
        return q
    return QColor(c)


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                           a.blueF() + (b.blueF() - a.blueF()) * k, a.alphaF() + (b.alphaF() - a.alphaF()) * k)


STEP_ICONS = ("ph.download-simple", "ph.sparkle", "ph.aperture", "ph.magic-wand", "ph.user-focus", "ph.play-circle")


class StepPill(QFrame):
    """Un paso de la secuencia: número, nombre y su carpeta («→ 3 Imagen AI»); al acabar, lo COMPROBADO
    («✓ 597 con Imagen»). Cambia de color con una transición corta. Doble clic: activa o desactiva el paso
    (Imagen AI, Retouch4me, CineSlide)."""
    double_clicked = Signal(int)
    COLORS = {"wait": (T.SURFACE_2, T.BORDER, T.FAINT), "run": ("rgba(215,255,79,0.10)", T.ACCENT, T.ACCENT),
              "off": ("transparent", T.BORDER, T.FAINT),
              "done": ("rgba(91,228,155,0.08)", "rgba(91,228,155,0.45)", T.OK),
              "skip": ("rgba(91,228,155,0.08)", "rgba(91,228,155,0.45)", T.OK),
              "error": ("rgba(255,107,107,0.10)", "rgba(255,107,107,0.5)", T.DANGER)}

    def __init__(self, n: int, title: str, sub: str):
        super().__init__()
        self.setObjectName("stepPill")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 9, 6, 9)
        lay.setSpacing(7)
        self.num = QLabel(str(n))
        self.num.setFixedSize(20, 20)
        self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num.setFont(font(11, QFont.Weight.Bold))
        lay.addWidget(self.num)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        self.title = QLabel(title)
        self.title.setFont(font(12, QFont.Weight.DemiBold))
        self.sub = ElidedLabel(sub, mode=Qt.TextElideMode.ElideRight)   # si no cabe: «…» (y entero al pasar el ratón)
        self.sub.setFont(font(10.5))
        txt.addWidget(self.title)
        txt.addWidget(self.sub)
        lay.addLayout(txt, 1)
        self.ico = QLabel()                        # icono del paso, a la derecha (toma el color de su estado)
        self.ico.setFixedSize(18, 18)
        self._ico_name = STEP_ICONS[n - 1] if 0 < n <= len(STEP_ICONS) else ""
        lay.addWidget(self.ico)
        lay.addSpacing(4)
        self.sub_text, self.off, self.state, self.note, self.note_state = sub, False, "wait", "", ""
        self.toggleable = (n - 1) in TOGGLEABLE
        self._hover = False
        bg, border, _ = self.COLORS["wait"]
        self._from = self._to = (_qc(bg), _qc(border))
        self._k, self._dash = 1.0, False
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=280,
                                       easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._anim_step)
        if self.toggleable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Doble clic: activar o desactivar este paso")
        else:
            self.setToolTip("Este paso siempre se hace")
        self.set("wait")

    def _anim_step(self, v):
        self._k = float(v)
        self.update()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.double_clicked.emit(int(self.property("n") or 0) - 1)
        e.accept()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = _mix(self._from[0], self._to[0], self._k)
        border = _mix(self._from[1], self._to[1], self._k)
        if self._hover and self.toggleable and self.state in ("wait", "done", "skip", "error"):
            border = _mix(border, QColor(T.ACCENT if self.state == "wait" and not self.off else T.BORDER_HI), 0.55)
        pen = QPen(border, 1.0, Qt.PenStyle.CustomDashLine if self._dash else Qt.PenStyle.SolidLine)
        if self._dash:
            pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

    def set_off(self, off: bool):
        """Paso desactivado: gris, «–» y «Desactivado» (se ve antes de empezar y durante el proceso)."""
        self.off = off
        self.set(self.state)

    def set_note(self, state: str, text: str):
        """Lo comprobado al acabar el paso («✓ 597 con Imagen»): en lugar de la carpeta, hasta el próximo evento."""
        self.note, self.note_state = text, state
        self.set(self.state)

    def set(self, state: str):
        self.state = state
        if self.off and state == "wait":
            state = "off"
        bg, border, fg = self.COLORS.get(state, self.COLORS["wait"])
        new = (_qc(bg), _qc(border))
        if (new[0].rgba(), new[1].rgba()) != (self._to[0].rgba(), self._to[1].rgba()):
            cur = (_mix(self._from[0], self._to[0], self._k), _mix(self._from[1], self._to[1], self._k))
            self._from, self._to = cur, new
            self._anim.stop()
            if T.reduce_motion() or not self.isVisible():
                self._k = 1.0
            else:
                self._k = 0.0
                self._anim.start()
        self._dash = state == "off"
        mark = {"done": "✓", "skip": "✓", "error": "!", "off": "–"}.get(state)
        if mark:
            self.num.setText(mark)
        elif self.num.text() in ("✓", "!", "–"):
            self.num.setText(str(self.property("n") or ""))
        note = {"ok": "✓ ", "warn": "! ", "off": "– "}.get(self.note_state, "") + self.note if self.note else ""
        self.sub.setText("Desactivado" if state == "off" else (note or self.sub_text))
        self.num.setStyleSheet(f"background:{border if state not in ('wait', 'off') else T.BORDER}; color:"
                               f"{T.ACCENT_INK if state in ('run', 'done', 'skip') else T.TEXT}; border-radius:10px;")
        self.title.setStyleSheet(f"color:{T.TEXT if state not in ('wait', 'off') else T.MUTED};"
                                 + (" text-decoration: line-through;" if state == "off" else ""))
        note_col = {"ok": T.OK, "warn": T.DANGER}.get(self.note_state) if self.note and state != "off" else None
        self.sub.setStyleSheet(f"color:{note_col or (fg if state != 'wait' else T.FAINT)};")
        pm = icon_pixmap_named(self._ico_name, fg if state not in ("wait", "off") else T.BORDER_HI, 18) \
            if self._ico_name else None
        if pm is not None:
            self.ico.setPixmap(pm)
        self.ico.setVisible(pm is not None)
        self.update()


class TimePanel(QFrame):
    """Tiempos de cada paso: estimado antes de empezar; transcurrido y restante durante."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = label("TIEMPO ESTIMADO", "sectionTitle")
        lay.addWidget(self.title)
        lay.addSpacing(10)
        self.rows = []
        for i, (t, _) in enumerate(STEPS, 1):
            r = QHBoxLayout()
            r.setContentsMargins(0, 7, 0, 7)
            name = QLabel(f"{i}   {t}")
            name.setFont(font(13, QFont.Weight.DemiBold))
            val = QLabel("—")
            val.setFont(font(13, mono=True))
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(name, 1)
            r.addWidget(val)
            lay.addLayout(r)
            self.rows.append((name, val))
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        lay.addSpacing(6)
        lay.addWidget(line)
        lay.addSpacing(10)
        tr = QHBoxLayout()
        self.total_name = QLabel("Total")
        self.total_name.setFont(font(15, QFont.Weight.Bold))
        self.total = QLabel("—")
        self.total.setFont(font(22, QFont.Weight.Bold))
        self.total.setStyleSheet(f"color:{T.ACCENT};")
        self.total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tr.addWidget(self.total_name, 1)
        tr.addWidget(self.total)
        lay.addLayout(tr)
        self.foot = label("", "faint")
        self.foot.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addSpacing(4)
        lay.addWidget(self.foot)
        self.est = [0.0] * len(STEPS)
        self.start = [0.0] * len(STEPS)
        self.dur: List[float | None] = [None] * len(STEPS)
        self.cur = -1
        self.t0 = 0.0
        self.frac = -1.0
        self.off: set = set()

    def _style(self, i: int, state: str):
        name, val = self.rows[i]
        col = {"wait": T.MUTED, "run": T.ACCENT, "done": T.OK, "error": T.DANGER, "off": T.FAINT}[state]
        name.setStyleSheet(f"color:{T.TEXT if state != 'wait' else T.MUTED};")
        val.setStyleSheet(f"color:{col};")

    def show_estimate(self, est: List[float]):
        self.title.setText("TIEMPO ESTIMADO")
        self.est = list(est)
        for i, e in enumerate(est):
            if i in self.off and i != STEP_IMG:           # Imagen desactivado aún tiene Bridge: su tiempo cuenta
                self.rows[i][1].setText("desactivado")
                self._style(i, "off")
            else:
                self.rows[i][1].setText(f"≈ {fmt(e)}" + ("  · solo Bridge" if i in self.off else ""))
                self._style(i, "wait")
        self.total_name.setText("Total")
        self.total.setText(f"≈ {fmt(sum(est))}")
        end = time.localtime(time.time() + sum(est))
        self.foot.setText(f"Si empiezas ahora, termina ≈ {time.strftime('%H:%M', end)}")

    def begin_run(self, est: List[float], start_at: int):
        self.title.setText("TIEMPO DEL PROCESO")
        self.est = list(est)
        self.dur = [0.0 if i < start_at else None for i in range(len(STEPS))]
        self.cur, self.t0, self.frac = -1, time.monotonic(), -1.0
        for i in range(len(STEPS)):
            self._style(i, "done" if i < start_at else "wait")
            self.rows[i][1].setText("—" if i < start_at else f"≈ {fmt(est[i])}")
        self.tick()

    def step(self, i: int, state: str):
        now = time.monotonic()
        if state == "run":
            self.cur, self.frac = i, -1.0
            self.start[i] = now
            self._style(i, "run")
        elif state == "off":
            self.dur[i] = (now - self.start[i]) if self.start[i] else 0.0
            self._style(i, "off")
            self.rows[i][1].setText("desactivado" + (f"  · {fmt(self.dur[i])}" if self.dur[i] >= 1 else ""))
            if i == self.cur:
                self.cur = -1
        elif state in ("done", "skip", "error") and self.start[i]:
            self.dur[i] = now - self.start[i]
            self._style(i, "error" if state == "error" else "done")
            self.rows[i][1].setText(("✗ " if state == "error" else "✓ ") + fmt(self.dur[i]))
            if i == self.cur:
                self.cur = -1
        self.tick()

    def remaining_current(self) -> float:
        if self.cur < 0:
            return 0.0
        el = time.monotonic() - self.start[self.cur]
        from . import live
        own = live.eta(self.cur, max_age=300)      # el propio paso sabe en qué fase va y a qué ritmo real
        if own is not None:
            return own
        model = max(0.0, self.est[self.cur] - el)
        if 0.02 <= self.frac < 1 and el > 3:
            seen = el / self.frac - el                # lo que dice el avance real de este paso
            w = min(1.0, 0.35 + self.frac * 1.3)      # cuanto más avanza, más manda lo real
            return w * seen + (1 - w) * model
        if self.frac >= 1:
            return 0.0
        return model

    def left_seconds(self) -> float:
        return self.remaining_current() + sum(self.est[j] for j in range(len(STEPS))
                                              if self.dur[j] is None and j != self.cur)

    def tick(self):
        if self.cur >= 0:
            el = time.monotonic() - self.start[self.cur]
            rem = self.remaining_current()
            self.rows[self.cur][1].setText(f"{fmt(el)}  ·  faltan {fmt(rem)}" if rem > 1 else f"{fmt(el)}  ·  casi")
        left = self.remaining_current() + sum(self.est[j] for j in range(len(STEPS))
                                              if self.dur[j] is None and j != self.cur)
        if self.t0:
            el_total = time.monotonic() - self.t0
            self.total_name.setText(f"Total  {fmt(el_total)}")
            self.total.setText(f"faltan {fmt(left)}" if left > 1 else "terminando…")
            end = time.localtime(time.time() + left)
            self.foot.setText(f"Termina ≈ {time.strftime('%H:%M', end)}")

    def finish(self, ok: bool):
        el = time.monotonic() - self.t0 if self.t0 else 0
        self.cur = -1
        self.total_name.setText("Total")
        self.total.setText(fmt(el))
        self.foot.setText("Terminado ✓" if ok else "Se detuvo")
        self.t0 = 0.0


# ==========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1180, 800)
        self.setMinimumSize(1060, 720)

        self.settings = QSettings(APP_NAME, APP_NAME)
        self.base_dir = self.settings.value("dest_base_v2", core.default_base_dir())
        apps = [a for a in core.find_dxo_apps() if core.is_pureraw(a)] or core.find_dxo_apps()
        saved = self.settings.value("dxo_path", "")
        self.dxo_path = saved if saved and os.path.exists(saved) else (apps[0] if apps else "")
        self.preset = self.settings.value("dxo_preset", core.DEFAULT_PRESET)
        self.steps_off = self._load_steps_off()
        # Ganchos de PRUEBA, solo para este arranque: nada se guarda en tus ajustes (README, «Prueba con memoria falsa»):
        #   open --env RAWFLOW_SIMULAR_USB=PRUEBA_SD --env "RAWFLOW_EVENTO=…" --env RAWFLOW_AUTOEJECUTAR=1
        #        --env RAWFLOW_PASOS=todos -a /Applications/RawFlow.app
        self.steps_test = os.environ.get("RAWFLOW_PASOS", "").strip().lower() == "todos"
        if self.steps_test:
            self.steps_off = set()                      # los 6 pasos, sin tocar «pasos_apagados»
        if any(os.environ.get(k) for k in ("RAWFLOW_SIMULAR_USB", "RAWFLOW_EVENTO", "RAWFLOW_AUTOEJECUTAR", "RAWFLOW_PASOS")):
            self.setWindowTitle(f"{APP_NAME} · PRUEBA")  # que se vea que este arranque es de prueba

        self.cards: Dict[str, DriveCard] = {}
        self.dropped: Dict[str, List[str]] = {}    # carpetas / fotos ARRASTRADAS: como memorias, nunca se expulsan
        self._drop_n = 0
        self.setAcceptDrops(True)
        self.drop_overlay = None                   # «Suelta para añadir» (se crea con la ventana)
        self.results: Dict[str, List[core.RawFile]] = {}
        self.scanners: Dict[str, ScanWorker] = {}
        self.pipe: Pipeline | None = None
        self.last_event: Path | None = None
        self.failed_step = 0
        self.perm_dialog = None
        self.vm_install_dialog = None
        self.estimator = Estimator(self.settings)
        self.run_n, self.run_bytes = 0, 0
        self.clock = QTimer(self, interval=1000, timeout=lambda: (self.timepanel.tick(), self._run_tick()))
        self.power_timer = QTimer(self, interval=5000)       # ¿sin cargador durante el evento? → avisar
        self.power_timer.timeout.connect(self._power_tick)
        self.power_note = {"warned": False, "level": -1, "last": 0.0}
        self.sampler = None
        self.countdown_left = 0
        self.autorun_keys = ()
        from .overlays import Curtain, MiniPanel
        self.mini = MiniPanel()
        self.curtain = Curtain()
        self.mirrored = False
        from .runscreen import RunScreen
        from .inputlock import InputLock
        self.runscreen = RunScreen(STEPS)
        self.inputlock = InputLock(self)
        self.inputlock.cancel.connect(self._lock_cancel)
        self.inputlock.state.connect(self._lock_state)

        from .pipeline import restore_stage_manager
        restore_stage_manager()                 # por si RawFlow se cerró a mitad de un evento
        self._build()
        self._refresh_ready()
        # programas instalados DESPUÉS de abrir RawFlow (p. ej. en un PC nuevo): se detectan solos, sin reiniciar
        self.ready_timer = QTimer(self, interval=15000)
        self.ready_timer.timeout.connect(self._refresh_ready_idle)
        self.ready_timer.start()
        ev, desde = os.environ.get("RAWFLOW_PRUEBA_EVENTO"), os.environ.get("RAWFLOW_PRUEBA_DESDE")
        if ev and desde and Path(ev).is_dir():   # solo pruebas: reintentar un evento desde el paso N
            QTimer.singleShot(1500, lambda: self._run(Path(ev), [], [], int(desde) - 1))
        else:
            self._offer_pending()               # un evento que quedó a medias (aunque RawFlow se cerrara)
        self._update_summary()

        if core.SYSTEM == "Darwin" and getattr(sys, "frozen", False):
            try:                                   # abrir RawFlow sola al conectar una memoria con RAW
                from . import autostart
                autostart.install()
            except Exception:  # noqa: BLE001
                pass
        self.watcher = DriveWatcher(self)
        self.watcher.changed.connect(self._drives_changed)
        self.watcher.start()
        if core.SYSTEM == "Darwin" and getattr(sys, "frozen", False) \
                and not self.settings.value("perms_shown_v2", False, type=bool):
            self.settings.setValue("perms_shown_v2", True)
            QTimer.singleShot(900, self._show_permissions)

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = self._outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 26)
        outer.setSpacing(18)

        head = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background:{T.ACCENT}; border-radius:6px;")
        word = label("RAWFLOW", "wordmark")
        word.setFont(font(21, QFont.Weight.ExtraBold))
        head.addWidget(dot)
        head.addSpacing(10)
        head.addWidget(word)
        from . import __version__
        ver = label(f"v{__version__}", "faint")
        ver.setFont(font(11, QFont.Weight.DemiBold))
        ver.setStyleSheet(f"color:{T.MUTED}; border:1px solid {T.BORDER}; border-radius:7px; padding:1px 6px;")
        head.addSpacing(8)
        head.addWidget(ver, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(16)
        head.addStretch(1)
        self.btn_update = QPushButton("", objectName="ghost")     # aparece solo si hay una versión nueva en GitHub
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.setStyleSheet(f"color:{T.ACCENT}; font-weight:600;")
        self.btn_update.clicked.connect(self._update_now)
        self.btn_update.hide()
        head.addWidget(self.btn_update)
        head.addSpacing(6)
        self.ready_chip = label("", "chip")
        head.addWidget(self.ready_chip)
        head.addSpacing(6)
        self.btn_auto = QPushButton("")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto.setToolTip("Al conectar una memoria con RAW, RawFlow empieza sola (con cuenta atrás de 3 s).")
        self.btn_auto.setChecked(self.settings.value("autorun", True, type=bool))
        if os.environ.get("RAWFLOW_AUTOEJECUTAR") == "1":      # prueba: encendido en este arranque (no se guarda)
            self.btn_auto.setChecked(True)
        self.btn_auto.toggled.connect(self._auto_toggled)
        self._auto_style()
        head.addWidget(self.btn_auto)
        self.btn_perms = QPushButton("Permisos", objectName="ghost")
        self.btn_perms.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_perms.clicked.connect(self._show_permissions)
        self.btn_perms.setVisible(core.SYSTEM == "Darwin")
        head.addWidget(self.btn_perms)
        self.btn_vm = QPushButton("Instalar máquina virtual", objectName="ghost")   # secundario: el lima es de «Empezar»
        self.btn_vm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_vm.clicked.connect(self._install_vm)
        self.btn_vm.setVisible(core.SYSTEM == "Darwin")
        for b in (self.btn_auto, self.btn_perms, self.btn_vm):
            b.setMinimumWidth(b.sizeHint().width())
        head.addWidget(self.btn_vm)
        # Windows: Imagen AI va con clave API (en Mac se maneja su app); aquí se escribe y se elige el perfil
        self.btn_imagen = QPushButton("Imagen AI", objectName="ghost")
        self.btn_imagen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_imagen.clicked.connect(self._config_imagen)
        self.btn_imagen.setVisible(core.SYSTEM != "Darwin")
        head.addWidget(self.btn_imagen)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(20)
        outer.addLayout(body, 1)

        # --- memorias
        left = panel()
        left.setFixedWidth(320)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(20, 20, 20, 20)
        ll.setSpacing(14)
        self.drive_head = QWidget()
        lh = QHBoxLayout(self.drive_head)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(label("MEMORIAS Y CARPETAS", "sectionTitle"))
        lh.addStretch(1)
        self.drive_count = label("", "faint")
        lh.addWidget(self.drive_count)
        ll.addWidget(self.drive_head)
        self.empty = EmptyDrives()
        ll.addWidget(self.empty, 1)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.cards_lay = QVBoxLayout(holder)
        self.cards_lay.setContentsMargins(0, 0, 6, 0)
        self.cards_lay.setSpacing(10)
        self.cards_lay.addStretch(1)
        self.cards_scroll.setWidget(holder)
        self.cards_scroll.hide()
        ll.addWidget(self.cards_scroll, 1)
        self.cards_hint = label("Toca una tarjeta para incluirla o excluirla.", "faint")
        ll.addWidget(self.cards_hint)
        self.time_sep = QFrame()
        self.time_sep.setFixedHeight(1)
        self.time_sep.setStyleSheet(f"background:{T.BORDER};")
        ll.addWidget(self.time_sep)
        self.timepanel = TimePanel()
        ll.addWidget(self.timepanel)
        self.time_fill = QWidget()                 # empuja los tiempos arriba durante el proceso
        ll.addWidget(self.time_fill, 1)
        self.time_fill.hide()
        self.time_sep.hide()
        self.timepanel.hide()
        body.addWidget(left)

        right = QVBoxLayout()
        right.setSpacing(18)
        body.addLayout(right, 1)

        # --- evento
        ev = panel()
        el = QVBoxLayout(ev)
        el.setContentsMargins(24, 20, 24, 20)
        el.setSpacing(10)
        el.addWidget(label("EVENTO", "sectionTitle"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.name_edit = QLineEdit(core.date_name() + " ")
        self.name_edit.setAcceptDrops(False)        # soltar una carpeta encima no escribe su ruta en el nombre
        self.auto_name = core.date_name()          # se actualiza solo si cambia el día y no lo editaste
        self.name_edit.setPlaceholderText(core.date_name())
        self.name_edit.setToolTip("Ya lleva la fecha de hoy. Puedes añadir algo (ej. «Boda Ana») o empezar así.")
        if os.environ.get("RAWFLOW_EVENTO", "").strip():          # prueba: nombre del evento (no se guarda)
            self.name_edit.setText(os.environ["RAWFLOW_EVENTO"].strip())
        self.name_edit.textChanged.connect(self._update_dest)
        self.name_edit.returnPressed.connect(lambda: self.go.isEnabled() and self._start())
        row.addWidget(self.name_edit, 1)
        el.addLayout(row)
        drow = QHBoxLayout()
        drow.setSpacing(8)
        drow.addWidget(label("→", "faint"))
        self.dest_label = ElidedLabel()
        self.dest_label.setObjectName("pathLabel")
        self.dest_label.setFont(font(12.5, mono=True))
        drow.addWidget(self.dest_label, 1)
        b = QPushButton("Cambiar", objectName="ghost")
        set_button_icon(b, "ph.folder-open", T.MUTED)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._choose_base)
        drow.addWidget(b)
        el.addLayout(drow)
        self.tree = label("", "faint")
        self.tree.setFont(font(11.5, mono=True))
        el.addWidget(self.tree)
        right.addWidget(ev)

        # --- resumen
        sm = panel()
        sl = QVBoxLayout(sm)
        sl.setContentsMargins(24, 20, 24, 20)
        sl.setSpacing(12)
        sl.addWidget(label("PARA PROCESAR", "sectionTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(36)
        self.st_count = Stat("fotos RAW")
        self.st_size = Stat("en total")
        self.st_days = Stat("fecha")
        for i, w in enumerate((self.st_count, self.st_size, self.st_days)):
            grid.addWidget(w, 0, i)
        grid.setColumnStretch(3, 1)
        sl.addLayout(grid)
        self.fmt_row = QHBoxLayout()
        self.fmt_row.setSpacing(6)
        self.fmt_row.addStretch(1)
        sl.addLayout(self.fmt_row)
        right.addWidget(sm)

        # --- acción
        ac = panel()
        al = QVBoxLayout(ac)
        al.setContentsMargins(24, 20, 24, 22)
        al.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(6)
        self.pills: List[StepPill] = []
        for i, (t, sub) in enumerate(STEPS, 1):
            p = StepPill(i, t, sub)
            p.setProperty("n", i)
            p.double_clicked.connect(self._toggle_step)
            p.set_off(i - 1 in self.steps_off)
            self.pills.append(p)
            steps.addWidget(p, 1)
        al.addLayout(steps)
        self.progress = SlimProgress()
        al.addWidget(self.progress)
        self.status = ElidedLabel("Conecta una memoria o arrastra una carpeta de fotos para empezar.")
        self.status.setObjectName("muted")
        self.status.fade_changes = True            # cada mensaje nuevo entra con un fundido corto
        al.addWidget(self.status)
        self.detail = ElidedLabel("")
        self.detail.setStyleSheet(f"color:{T.ACCENT}; font-weight:600;")
        self.detail.setFont(font(12, mono=True))
        al.addWidget(self.detail)

        al.addSpacing(6)
        self.activity = ActivityView()             # lo que RawFlow va haciendo; llena el hueco y deja «Empezar» abajo
        al.addWidget(self.activity, 1)
        al.addSpacing(4)
        brow = QHBoxLayout()
        brow.setSpacing(10)
        self.go = RippleButton("Empezar", objectName="primary")      # con onda al pulsar
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.setMinimumHeight(56)
        self.go.clicked.connect(self._start)
        self.go_glow = anim.Glow(self.go)
        brow.addWidget(self.go, 1)
        self.retry = QPushButton("Reintentar")
        set_button_icon(self.retry, "ph.arrow-counter-clockwise", T.TEXT, 16)
        self.retry.setMinimumHeight(56)
        self.retry.clicked.connect(self._retry)
        self.retry.hide()
        brow.addWidget(self.retry)
        self.open_btn = QPushButton("Abrir carpeta")
        set_button_icon(self.open_btn, "ph.folder-open", T.TEXT, 16)
        self.open_btn.setMinimumHeight(56)
        self.open_btn.clicked.connect(lambda: self.last_event and core.reveal_in_file_manager(self.last_event))
        self.open_btn.hide()
        brow.addWidget(self.open_btn)
        self.cancel = QPushButton("Cancelar", objectName="danger")
        set_button_icon(self.cancel, "ph.x", T.DANGER, 15)
        self.cancel.setMinimumHeight(56)
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        brow.addWidget(self.cancel)
        al.addLayout(brow)
        right.addWidget(ac, 1)                     # llena el alto: la columna derecha ya no deja media pantalla vacía
        self._update_dest()

    # ------------------------------------------------------------ apps necesarias
    def _missing_apps(self) -> List[str]:
        """Programas que faltan, en Mac y en Windows (un equipo con todo instalado se detecta solo)."""
        from . import imagen, programas
        miss = programas.missing(api_key=bool(imagen.get_api_key()))
        if not (self.dxo_path and core.is_pureraw(self.dxo_path)):
            miss = ["DxO PureRAW"] + [m for m in miss if m != "DxO PureRAW"]
        return miss

    @Slot()
    def _refresh_ready_idle(self):
        """Cada 15 s, sin evento en marcha: ¿se instaló o se quitó algún programa? (si cambia DxO, se toma el nuevo)"""
        if self.pipe is not None and self.pipe.isRunning():
            return
        from . import programas
        d = programas.dxo()
        if d and not (self.dxo_path and core.is_pureraw(self.dxo_path) and os.path.exists(self.dxo_path)):
            self.dxo_path = d
        before = self.ready_chip.text()
        self._refresh_ready()
        if self.ready_chip.text() != before:
            self._refresh_ready_changed()

    def _refresh_ready_changed(self):
        """Algo cambió (p. ej. acabas de instalar DxO): se vuelve a mirar si ya se puede empezar."""
        try:
            self._update_summary()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ actualizaciones
    def start_update_check(self):
        """Al abrir (y cada 6 horas): ¿hay una versión nueva publicada? Si la hay, aparece su botón arriba."""
        from . import update
        if not update.enabled():
            return
        self.update_info = None
        self._upd_worker = None
        self._upd_timer = QTimer(self, interval=6 * 3600 * 1000, timeout=self._check_update)
        self._upd_timer.start()
        QTimer.singleShot(4000, self._check_update)

    def _check_update(self):
        w = getattr(self, "_upd_worker", None)
        if w is not None and w.isRunning():
            return
        self._upd_worker = UpdateWorker(self)
        self._upd_worker.found.connect(self._update_found)
        self._upd_worker.start()

    def _update_found(self, info: dict):
        self.update_info = info
        self.btn_update.setText(f"Actualizar a {info['version']}")
        set_button_icon(self.btn_update, "ph.arrow-circle-up", T.ACCENT, 16)
        self.btn_update.setToolTip(f"Hay una versión nueva de RawFlow ({info['version']}). Se descarga, se instala y "
                                   "RawFlow se abre otra vez sola. Tus ajustes y tus eventos no se tocan.")
        if not self.btn_update.isVisible():
            self.btn_update.show()
            anim.fade_in(self.btn_update, 400)
        self._log(f"Hay una versión nueva de RawFlow: {info['version']}")

    def _update_now(self):
        info = getattr(self, "update_info", None)
        if not info:
            return
        if self.pipe is not None and self.pipe.isRunning():
            QMessageBox.information(self, APP_NAME, "Hay un evento en marcha: actualiza cuando termine.")
            return
        mb = info["size"] / 1e6 if info.get("size") else 0
        r = QMessageBox.question(self, APP_NAME, f"¿Actualizar RawFlow a la versión {info['version']}?\n\n"
                                 f"Se descarga{f' ({mb:.0f} MB)' if mb else ''}, se instala y RawFlow se abre otra vez. "
                                 "Tus ajustes y tus eventos no se tocan.")
        if r != QMessageBox.StandardButton.Yes:
            return
        self.btn_update.setEnabled(False)
        self.go.setEnabled(False)
        self.status.setText("Descargando la actualización…")
        self.progress.setIndeterminate(False)
        w = self._upd_dl = UpdateWorker(self, info)
        w.progress.connect(lambda f: (self.progress.setValue(f), self.btn_update.setText(f"Descargando… {int(f * 100)} %")))
        w.ready.connect(self._update_ready)
        w.start()

    def _update_ready(self, path: str, err: str):
        info = getattr(self, "update_info", None) or {}
        if err or not path:
            self.btn_update.setEnabled(True)
            self.btn_update.setText(f"Actualizar a {info.get('version', '')}")
            self.status.setText(f"No se pudo actualizar: {err}")
            self._log(f"⚠ Actualización: {err}")
            self._update_summary()
            return
        from . import update
        try:
            update.install(Path(path))
        except Exception as e:  # noqa: BLE001
            self.btn_update.setEnabled(True)
            self.status.setText(f"No se pudo abrir el instalador: {e}")
            return
        self._log(f"Instalando RawFlow {info.get('version', '')}: RawFlow se cierra y se abre actualizada.")
        self.status.setText("Instalando… RawFlow se abrirá sola en un momento.")
        QTimer.singleShot(600, self.close)

    def _config_imagen(self):
        """«Configurar Imagen»: clave API, perfil y tipo de fotografía."""
        if self.pipe is not None and self.pipe.isRunning():
            return
        from .imagen_dialog import ImagenDialog
        ImagenDialog(self.settings, self).exec()
        self._refresh_ready()

    def _imagen_button(self):
        """El botón dice si hay clave: sin ella, el paso de Imagen (y con él Retouch4me y CineSlide) se omite."""
        if core.SYSTEM == "Darwin":
            return
        from . import imagen, imagen_win
        key = bool(imagen.get_api_key())
        app = imagen_win.available() and imagen_win.bridge_path() is not None
        self.btn_imagen.setText("Imagen AI  ✓  clave API" if key else "Imagen AI  ✓  con su app" if app
                                else "Imagen AI: falta")
        self.btn_imagen.setStyleSheet("" if (key or app) else f"color:{T.DANGER};")
        self.btn_imagen.setToolTip(
            "Imagen AI con tu clave API. Pulsa para cambiar la clave, el perfil o el tipo de fotografía." if key else
            "Imagen AI con su app (ImagenAI, con tu sesión iniciada) y Adobe Bridge: RawFlow las maneja por ti.\n"
            "Con una clave API iría sin abrir ventanas: pulsa para escribirla." if app else
            "Falta la app ImagenAI con Adobe Bridge, o una clave API: sin eso el evento se queda en los DNG.\n"
            "Pulsa para escribir tu clave.")

    def _refresh_ready(self):
        self._imagen_button()
        miss = self._missing_apps()
        if miss:
            self.ready_chip.setObjectName("chipWarn")
            self.ready_chip.setText("●  Falta: " + ", ".join(miss))
        else:
            self.ready_chip.setObjectName("chipOk")
            self.ready_chip.setText("●  Todo listo")
        self.ready_chip.setToolTip("DxO · Imagen · Bridge · Retouch4me · CineSlide" + (" — falta: " + ", ".join(miss)
                                   if miss else " listos") + (f"\n{self.dxo_path}" if self.dxo_path else ""))
        self.ready_chip.style().unpolish(self.ready_chip)
        self.ready_chip.style().polish(self.ready_chip)

    # ------------------------------------------------------------ memorias
    def _drives_changed(self, drives: list):
        if self.pipe and self.pipe.isRunning():
            return                      # mientras se mueven fotos no tocamos las tarjetas
        now = {d.key: d for d in drives}
        for key in list(self.cards):
            if key not in now and key not in self.dropped:     # lo arrastrado no es una memoria: se queda
                self._remove_drive(key)
        for key, d in now.items():
            if key not in self.cards:
                self._add_drive(d)
        n = len(self.cards)
        self.drive_count.setText(f"{n} detectada{'s' if n != 1 else ''}" if n else "")
        if n:
            self.showing_result = False
        self._update_summary()

    def _add_drive(self, d: core.Drive):
        card = DriveCard(d)
        card.toggled.connect(lambda *_: self._update_summary())
        self.cards[d.key] = card
        self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
        anim.slide_in(card)                        # la tarjeta entra deslizándose y aparece
        w = ScanWorker(d.key, self, paths=self.dropped.get(d.key))
        w.progress.connect(self._scan_progress)
        w.finished_scan.connect(self._scan_done)
        w.finished.connect(w.deleteLater)
        self.scanners[d.key] = w
        w.start()

    def _remove_drive(self, key: str):
        self.autorun_keys = tuple(k for k in self.autorun_keys if k != key)
        if key in self.cards and self.countdown_left:
            self._stop_countdown()
        w = self.scanners.pop(key, None)
        if w:
            w.stop()
        self.dropped.pop(key, None)
        card = self.cards.pop(key)
        card.setEnabled(False)                       # ya no cuenta: sale encogiéndose y se quita al terminar

        def gone(c=card):
            c.setParent(None)
            c.deleteLater()
        anim.slide_out(card, gone)
        self.results.pop(key, None)

    def _scan_progress(self, key, visited, found):
        if key in self.cards:
            self.cards[key].set_scanning(visited, found)

    def _scan_done(self, key, files):
        self.scanners.pop(key, None)
        if key not in self.cards:
            return
        self.results[key] = files
        res = core.ScanResult(files)
        self.cards[key].set_done(len(files), res.total_bytes, [f for f, _ in res.by_format().most_common()])
        self._update_summary()

    # ------------------------------------------------------------ resumen
    def _selected_files(self) -> List[core.RawFile]:
        out: List[core.RawFile] = []
        for key, card in self.cards.items():
            if card.selected:
                out += self.results.get(key, [])
        out.sort(key=lambda f: f.mtime)
        return out

    def _update_summary(self):
        files = self._selected_files()
        res = core.ScanResult(files)
        scanning = any(self.cards[k].selected for k in self.scanners if k in self.cards)
        self.st_count.set_number(len(files), lambda v: f"{int(round(v)):,}")
        self.st_size.set_number(res.total_bytes if files else 0, lambda v: core.human_size(int(v)) if v >= 1 else "—")
        a, b = res.date_range()
        fmt = lambda d: QLocale().toString(QDate(d.year, d.month, d.day), "dd MMM")  # noqa: E731
        self.st_days.value.setText((fmt(a) if a == b else f"{fmt(a)} – {fmt(b)}") if a else "—")
        fmts = res.by_format().most_common()
        if fmts != getattr(self, "_fmts", None):           # solo si cambian: rehacerlas siempre las hacía parpadear
            self._fmts = fmts
            while self.fmt_row.count() > 1:
                it = self.fmt_row.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            for k, (ext, n) in enumerate(fmts):
                chip = label(f"{ext}  {n:,}", "formatChip")
                self.fmt_row.insertWidget(self.fmt_row.count() - 1, chip)
                chip.show()
                anim.fade_in(chip, 260, delay=40 * k)      # entran una tras otra

        today = core.date_name()
        if today != self.auto_name:                # cambió el día con la app abierta
            if self.name_edit.text().strip() == self.auto_name:
                self.name_edit.setText(today + " ")
            self.auto_name = today
            self.name_edit.setPlaceholderText(today)
        if self.pipe and self.pipe.isRunning():
            return
        if scanning:
            self.status.setText("Analizando memorias…")
            self.progress.setIndeterminate(True)
        else:
            self.progress.setIndeterminate(False)
            if not self.cards:
                self.status.setText(getattr(self, "pending_msg", "") or "Conecta una memoria o arrastra una carpeta de fotos para empezar.")
            elif not files:
                self.status.setText("No hay fotos RAW en las memorias seleccionadas.")
            else:
                self.status.setText("Escribe el nombre del evento y pulsa Empezar.")
        self.go.setEnabled(bool(files) and not scanning)
        if files and not scanning:
            if getattr(self, "showing_result", False):          # otro evento: las comprobaciones del anterior se van
                for p in self.pills:
                    p.set_note("", "")
            self.showing_result = False
            self.timepanel.off = set(self.steps_off)
            self.timepanel.show_estimate(self._apply_off(self.estimator.estimate(len(files), res.total_bytes),
                                                         len(files)))
        if getattr(self, "showing_result", False) and not self.cards:
            self._left_mode("result")          # deja a la vista los tiempos del último evento
        else:
            self._left_mode("cards")
            self.timepanel.setVisible(bool(files) and not scanning)
            self.time_sep.setVisible(self.timepanel.isVisible())
        if not self.countdown_left:
            self.go.setText(f"Empezar  ·  {len(files):,} fotos" if files else "Empezar")
        keys = tuple(sorted(k for k, c in self.cards.items() if c.selected and self.results.get(k)))
        if (self.btn_auto.isChecked() and files and not scanning and not self.countdown_left
                and keys and keys != self.autorun_keys):
            self.autorun_keys = keys
            self._begin_countdown()

    # ------------------------------------------------------------ autoejecutar
    def _auto_style(self):
        on = self.btn_auto.isChecked()
        # interruptor (estado), no un botón de acción: el único relleno en lima es «Empezar»
        self.btn_auto.setText("●  Autoejecutar: sí" if on else "○  Autoejecutar: no")
        self.btn_auto.setMinimumWidth(self.btn_auto.fontMetrics().horizontalAdvance(self.btn_auto.text()) + 34)
        self.btn_auto.setStyleSheet(
            f"color:{T.ACCENT}; background:rgba(215,255,79,0.08); border:1px solid rgba(215,255,79,0.45);"
            f" font-weight:600; border-radius:13px; padding:5px 14px;" if on
            else f"color:{T.MUTED}; background:transparent; border:1px solid {T.BORDER}; border-radius:13px;"
                 f" padding:5px 14px;")

    def _auto_toggled(self, on: bool):
        self.settings.setValue("autorun", on)
        self._auto_style()
        if not on:
            self._stop_countdown()

    def _begin_countdown(self):
        self.countdown_left = 3
        self.cancel.show()
        self.cancel.setText("No empezar")
        self.raise_()
        self.activateWindow()
        self._countdown_tick()

    def _countdown_tick(self):
        if not self.countdown_left:
            return
        n = self._selected_files()
        self.go.setText(f"Empieza solo en {self.countdown_left}…  ·  {len(n):,} fotos")
        self.status.setText("Autoejecutar: memoria con RAW detectada. Empieza sola.")
        self.countdown_left -= 1
        if self.countdown_left <= 0:
            self.countdown_left = 0
            self.cancel.setText("Cancelar")
            self.cancel.hide()
            n = len(self._selected_files())
            self.go.setText(f"Empezar  ·  {n:,} fotos" if n else "Empezar")
            self._start()
        else:
            QTimer.singleShot(1000, self._countdown_tick)

    def _stop_countdown(self):
        if self.countdown_left:
            self.countdown_left = 0
            self.cancel.setText("Cancelar")
            self.cancel.hide()
            self._update_summary()

    # ------------------------------------------------------------ siempre delante
    def _pin(self, on: bool):
        """on: RawFlow por encima de todo (barra de menús y Dock incluidos), con el fondo que tapa las demás apps."""
        from .overlays import LEVEL_MAIN, set_level
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if on:
            self.curtain.cover()
        else:
            self.curtain.hide()
        self.show()
        set_level(self, LEVEL_MAIN if on else 0)
        self.raise_()
        self.activateWindow()

    def _mini_mode(self, on: bool):
        """Pasos 3-4 (manejan otras apps): RawFlow pasa a un panel compacto flotante encima de todo."""
        if on == self.mirrored:
            return
        self.mirrored = on
        if on:                 # la ventana se oculta (no roba el foco a la app que se maneja) y el fondo la pinta
            self.curtain.mirror(self, "RawFlow está manejando Imagen AI, Bridge y Retouch4me por debajo: "
                                      "no toques el ratón ni el teclado hasta que termine.")
            self.hide()
        else:
            self.curtain.mirror(None)
            self._pin(bool(self.pipe and self.pipe.isRunning()))

    def _mini_tick(self):
        if self.mini.isVisible():
            tp = self.timepanel
            left = tp.total.text()
            self.mini.set(right=left, frac=tp.frac if 0 <= tp.frac <= 1 else -1)

    # ------------------------------------------------------------ destino
    def _dest(self) -> Path:
        """Escritorio/<nombre> (por defecto la fecha de hoy); si ya existe: «… 2», «… 3»."""
        name = core.clean_name(self.name_edit.text()) or core.date_name()
        return core.unique_folder(Path(self.base_dir) / name)

    def _update_dest(self, *_):
        d = self._dest()
        self.dest_label.setText(str(d))
        self.tree.setText(f"{d.name}/   {core.RAW_SUBFOLDER} · {core.OUTPUT_SUBFOLDER} · "
                          f"{IMAGEN_DIR if STEP_IMG not in self.steps_off else JPEG_SIN_DIR} · {FINAL_DIR} · {PRES_DIR}")

    def _choose_base(self):
        d = QFileDialog.getExistingDirectory(self, "¿Dónde crear la carpeta del evento?", self.base_dir)
        if d:
            self.base_dir = d
            self.settings.setValue("dest_base_v2", d)
            self._update_dest()

    # ------------------------------------------------------------ arrastrar carpetas y fotos (sin excepción)
    def _overlay(self, on: bool):
        from .widgets import DropOverlay
        if self.drop_overlay is None and on:
            self.drop_overlay = DropOverlay(self.centralWidget())
        if self.drop_overlay is not None:
            self.drop_overlay.show_over(on)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            if not (self.pipe is not None and self.pipe.isRunning()):
                self.status.setText("Suelta aquí: carpetas de fotos, fotos sueltas o un evento de RawFlow.")
                self._overlay(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):  # noqa: N802
        self._overlay(False)
        self._update_summary()

    def dropEvent(self, e):  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        self._overlay(False)
        self._add_dropped(paths)

    def _add_dropped(self, paths: List[Path]):
        """Lo que se suelta en RawFlow: cada carpeta (con sus subcarpetas) es una tarjeta más, como una memoria; las
        fotos sueltas van juntas en otra; un evento de RawFlow se ofrece para seguir (o revisar) donde se quedó."""
        if self.pipe is not None and self.pipe.isRunning():
            self.status.setText("Mientras trabaja no se pueden añadir fotos: espera a que termine.")
            return
        sueltas, nuevas = [], 0
        for p in paths:
            if p.is_dir():
                ev = event_root(p)
                if ev is not None:
                    self._offer_event(ev)
                    continue
                key = str(p)
                if key not in self.cards:
                    self.dropped[key] = [key]
                    self._add_drive(core.folder_drive(p))
                    nuevas += 1
            elif p.is_file():
                sueltas.append(str(p))
        if sueltas:
            self._drop_n += 1
            n = len(sueltas)
            key = f"arrastradas-{self._drop_n}"
            self.dropped[key] = sueltas
            self._add_drive(core.Drive(mountpoint=key, label=f"{n} foto{'s' if n != 1 else ''} sueltas",
                                       fstype="sueltas", total=0, used=0, removable=False))
            nuevas += 1
        if nuevas:
            self.showing_result = False
            n = len(self.cards)
            self.drive_count.setText(f"{n} detectada{'s' if n != 1 else ''}" if n else "")
            self._log(f"Arrastrado a RawFlow: {', '.join(str(p) for p in paths)[:300]}")
            self._update_summary()

    def _offer_event(self, ev: Path):
        """Un evento de RawFlow arrastrado: botón para seguir desde el paso que le falta (o revisarlo entero)."""
        r = resume_step(ev, self.steps_off)
        if r is None:
            self.status.setText(f"«{ev.name}» no tiene fotos en «{core.RAW_SUBFOLDER}».")
            return
        step, falta = r
        self.last_event, self.failed_step = ev, step
        if falta:
            self.retry.setText(f"Seguir «{ev.name}» desde el paso {step + 1}")
            self.status.setText(f"«{ev.name}»: le falta {falta}. Pulsa «Seguir» y sigue desde el paso {step + 1} "
                                f"({STEPS[step][0]}).")
        else:
            self.retry.setText(f"Revisar «{ev.name}»")
            self.status.setText(f"«{ev.name}» está terminado. «Revisar» comprueba foto a foto que todo lleve Imagen "
                                "y rehace solo lo que no esté bien.")
        self.retry.show()
        self._log(f"Evento arrastrado: {ev} → paso {step + 1}" + (f" (falta {falta})" if falta else " (revisar)"))

    # ------------------------------------------------------------ pasos activados / desactivados (doble clic)
    def _load_steps_off(self) -> set:
        """Pasos desactivados, guardados por NOMBRE («Imagen AI», …): así añadir un paso no cambia cuáles están
        apagados. Los de antes de la Selección IA (por número, y su interruptor) se pasan una sola vez."""
        names_by_step = {t: i for i, (t, _) in enumerate(STEPS)}
        raw = self.settings.value("pasos_apagados", None)
        if raw is None:                                   # primera vez con 6 pasos: lo de antes, por nombre
            old = str(self.settings.value("pasos_desactivados", "") or "")
            viejos = {2: "Imagen AI", 3: "Retouch4me", 4: "CineSlide"}
            names = [viejos[int(x)] for x in old.split(",") if x.strip().isdigit() and int(x) in viejos]
            if not self.settings.value("seleccion_ia", True, type=bool):
                names.append("Selección IA")
            raw = ",".join(names)
            self.settings.setValue("pasos_apagados", raw)
            try:                                          # evento a medias guardado con la numeración de 5 pasos
                if int(self.settings.value("pasos_version", 1) or 1) < 2:
                    st = self.settings.value("pendiente_paso", None)
                    if st is not None and int(st) >= 1:
                        self.settings.setValue("pendiente_paso", int(st) + 1)
                    self.settings.setValue("pasos_version", 2)
            except (TypeError, ValueError):
                pass
        return {names_by_step[n] for n in str(raw).split(",") if n in names_by_step and names_by_step[n] in TOGGLEABLE}

    STEP_OFF_TXT = {STEP_SEL: "Selección IA desactivada: todas las fotos pasan por DxO, Imagen y Retouch4me.",
                    STEP_IMG: "Imagen AI desactivado: Bridge exporta los JPEG sin Imagen (a «3 JPEG sin Imagen»).",
                    STEP_RET: "Retouch4me desactivado: las fotos finales salen de Imagen sin retocar.",
                    STEP_PRES: "CineSlide desactivado: el evento va sin presentación."}

    def _toggle_step(self, i: int):
        """Doble clic en un paso: lo activa o lo desactiva (se recuerda para los próximos eventos)."""
        if self.pipe is not None and self.pipe.isRunning():
            self.status.setText("Mientras trabaja no se pueden cambiar los pasos: espera a que termine.")
            return
        if i not in TOGGLEABLE:
            self.status.setText(f"«{STEPS[i][0]}» siempre se hace: sin él no hay "
                                + ("fotos en la carpeta del evento." if i == 0 else "DNG para los demás pasos."))
            return
        if i in self.steps_off:
            self.steps_off.discard(i)
            msg = f"{STEPS[i][0]} activado otra vez."
        else:
            self.steps_off.add(i)
            msg = self.STEP_OFF_TXT[i]
        if not getattr(self, "steps_test", False):       # en una prueba con RAWFLOW_PASOS no se guarda nada
            self.settings.setValue("pasos_apagados", ",".join(STEPS[k][0] for k in sorted(self.steps_off)))
        self.pills[i].set_off(i in self.steps_off)
        if hasattr(self, "tree"):
            self._update_dest()                          # «3 Imagen AI» o «3 JPEG sin Imagen» en la vista previa
        self._log(f"Paso {i + 1} ({STEPS[i][0]}): {'desactivado' if i in self.steps_off else 'activado'} con doble clic")
        self._update_summary()
        self.status.setText(msg)

    def _apply_off(self, est: List[float], n: int) -> List[float]:
        """Tiempo estimado sin los pasos desactivados (Imagen desactivado = solo Bridge)."""
        est = list(est)
        if STEP_SEL in self.steps_off or core.SYSTEM != "Darwin":
            est[STEP_SEL] = 0.0
        if STEP_IMG in self.steps_off and est[STEP_IMG] > 0:
            from . import timing
            f, spp, _ = timing.model("bridge")
            est[STEP_IMG] = f + spp * n
        if STEP_RET in self.steps_off:
            est[STEP_RET] = min(est[STEP_RET], 2.0 + 0.05 * n)   # solo copiar las fotos
        if STEP_PRES in self.steps_off:
            est[STEP_PRES] = 0.0
        return est

    # ------------------------------------------------------------ secuencia
    def _start(self):
        files = self._selected_files()
        if not files:
            return
        miss = self._missing_apps()
        if miss:
            QMessageBox.warning(self, APP_NAME, "Para la secuencia completa falta:\n\n• " + "\n• ".join(miss))
            return
        if core.SYSTEM == "Darwin" and IMAGEN_APP.exists():
            from . import imagen, imagen_ui
            if not imagen.get_api_key() and imagen_ui.check_permissions():
                QMessageBox.information(self, APP_NAME, "RawFlow necesita los permisos de Accesibilidad y "
                                        "Grabación de pantalla para manejar Imagen, Bridge y Retouch4me.")
                self._show_permissions()
                return
        dest = self._dest()
        need = sum(f.size for f in files)
        probe = dest
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        if free < need * 3.2:              # RAW + DNG (~1,8×) + JPEG
            r = QMessageBox.question(self, APP_NAME,
                                     f"Puede faltar espacio: necesitas ~{core.human_size(need * 3.2)} y quedan "
                                     f"{core.human_size(free)}. ¿Continuar?")
            if r != QMessageBox.StandardButton.Yes:
                return
        eject = [k for k, c in self.cards.items() if c.selected and k not in self.dropped]   # carpetas: nunca
        self._run(dest, files, eject, 0, copy_roots=self._copy_roots(dest))

    def _retry(self):
        if self.last_event:
            self._run(self.last_event, [], [], self.failed_step)

    # ------------------------------------------------------------ evento a medias
    def _remember_pending(self, ev: Path, step):
        """Guarda el evento que se detuvo (y en qué paso) para poder retomarlo aunque RawFlow se cierre;
        si ese evento termina bien, se olvida."""
        self.pending_msg = ""
        try:
            if step is None:
                if self.settings.value("pendiente_evento", "") == str(ev):
                    self.settings.remove("pendiente_evento")
                    self.settings.remove("pendiente_paso")
            else:
                self.settings.setValue("pendiente_evento", str(ev))
                self.settings.setValue("pendiente_paso", int(step))
        except Exception:  # noqa: BLE001
            pass

    def _offer_pending(self):
        """Al abrir RawFlow: si un evento quedó a medias, botón «Reintentar «evento» desde el paso N»."""
        try:
            ev = Path(str(self.settings.value("pendiente_evento", "") or ""))
            step = int(self.settings.value("pendiente_paso", 0) or 0)
        except (TypeError, ValueError):
            return
        if not str(ev) or str(ev) == "." or not ev.is_dir() or not (0 <= step < len(STEPS)):
            return
        self.last_event, self.failed_step = ev, step
        self.retry.setText(f"Reintentar «{ev.name}» desde el paso {step + 1}")
        self.retry.show()
        self.pending_msg = (f"«{ev.name}» quedó a medias en «{STEPS[step][0]}». "
                            "Pulsa Reintentar para seguir donde se quedó.")
        self.status.setText(self.pending_msg)

    def _copy_roots(self, dest: Path) -> set:
        """Carpetas arrastradas de OTRO disco: sus fotos se COPIAN (el original se queda donde estaba). Las del mismo
        disco se mueven (instantáneo, sin ocupar el doble)."""
        probe = Path(dest)
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            dev = os.stat(probe).st_dev
        except OSError:
            return set(self.dropped)
        out = set()
        for key, paths in self.dropped.items():
            try:
                if any(os.stat(x).st_dev != dev for x in paths):
                    out.add(key)
            except OSError:
                out.add(key)
        return out

    def _run(self, event: Path, files, eject, start: int, copy_roots=()):
        if self.pipe is not None and self.pipe.isRunning():   # un doble clic no arranca el mismo evento dos veces
            return
        self.last_event = event
        if event.is_dir():
            core.migrate_event_layout(event)           # evento de antes de 4.15: carpetas 1-5 (solo renombra)
        if files:
            self.run_n, self.run_bytes = len(files), sum(f.size for f in files)
        else:                               # reintento: contar lo que ya está en la carpeta
            raw = folders(event)[0]
            rs = [p for p in raw.glob("*") if p.suffix.lower() in core.RAW_EXTENSIONS]
            self.run_n, self.run_bytes = len(rs), sum(p.stat().st_size for p in rs)
        est = self.estimator.estimate_event(event if not files else None, self.run_n, self.run_bytes, start)
        est = self._apply_off(est, self.run_n)
        self._left_mode("run")
        self.timepanel.off = set(self.steps_off)
        self.timepanel.begin_run(est, start)
        self.clock.start()
        for i, p in enumerate(self.pills):
            p.set_note("", "")
            p.set("done" if i < start else "wait")
        from . import live as _live
        _live.clear_checks()                      # el panel COMPROBADO empieza limpio en cada evento
        if self.steps_off:
            self._log("    Pasos desactivados: " + ", ".join(STEPS[k][0] for k in sorted(self.steps_off)))
        self.pipe = Pipeline(event, files, self.dxo_path, self.preset, eject, start, self, off=self.steps_off,
                             copy_roots=copy_roots)
        self.pipe.step.connect(self._on_step)
        self.pipe.status.connect(self.status.setText)
        self.pipe.detail.connect(self.detail.setText)
        self.pipe.progress.connect(self._on_progress)
        self.pipe.finished_all.connect(self._on_finished)
        self.pipe.ejected.connect(self._eject_toast)
        self.pipe.check.connect(self._on_check)
        self.pipe.status.connect(self.runscreen.set_status)
        self.pipe.detail.connect(self.runscreen.set_detail)
        self.pipe.progress.connect(self.runscreen.set_step_frac)
        self.pipe.status.connect(self._log_status)
        self.pipe.detail.connect(self._log_detail)
        self.pipe.step.connect(self._log_step)
        self.pipe.finished_all.connect(self._log_end)
        self._log(f"=== EVENTO {event}  ({self.run_n} fotos, desde el paso {start + 1})")
        # pantalla completa de proceso + ratón y teclado bloqueados hasta que termine
        self.runscreen.target = self.screen()                  # la pantalla de proceso, donde está la ventana
        self.runscreen.open(event.name, f"{self.run_n:,} fotos  ·  {event}", start)
        for w in (self.runscreen, self.mini, self.curtain):     # Windows: no tapan lo que RawFlow lee por debajo
            hide_from_capture(w)
        for k in self.steps_off:
            if k >= start:
                self.runscreen.set_step(k, "off")
        self.runscreen.set_texts(status="Empezando…", detail="")
        self.hide()
        if os.environ.get("RAWFLOW_SIN_BLOQUEO") != "1":       # solo pruebas: sin bloquear ratón ni teclado
            self.runscreen.locked = self.inputlock.lock()
        from . import live, metrics                 # mediciones en vivo (programas, MB/s, CPU/GPU) y batería
        live.reset()
        self.runscreen.set_power("")
        self.power_note = {"warned": False, "level": -1, "last": 0.0}
        self.sampler = metrics.Sampler(every=3.0)
        self.sampler.start()
        self.power_timer.start()
        self._run_tick()
        for w in (self.go, self.retry, self.open_btn):
            w.hide()
        self.cancel.show()
        self.name_edit.setEnabled(False)
        self.detail.setText("")
        self.pipe.start()

    def _left_mode(self, mode: str):
        """cards: memorias (+ estimación abajo) · run/result: solo los tiempos, arriba."""
        cards = mode == "cards"
        n = len(self.cards)
        self.drive_head.setVisible(cards)
        self.empty.setVisible(cards and n == 0)
        self.cards_scroll.setVisible(cards and n > 0)
        self.cards_hint.setVisible(cards and n > 1)
        self.time_sep.setVisible(False)
        self.time_fill.setVisible(not cards)
        if not cards:
            self.timepanel.show()

    @Slot(str)
    def _eject_toast(self, data: str):
        """Aviso «Ya puedes retirar la memoria»: se queda hasta que la sacas de verdad."""
        import json
        from .overlays import EjectToast
        from .overlays import LEVEL_TOAST, set_level
        try:
            items = json.loads(data)
        except ValueError:
            items = data                       # solo nombres
        old = getattr(self, "toast", None)
        try:
            if old is not None and old.isVisible() and not old._closing and isinstance(items, list):
                old.add(items)                 # ya hay un aviso esperando otra memoria: se suma
                return
        except RuntimeError:                   # el aviso anterior ya se cerró (objeto de Qt borrado)
            pass
        self.toast = EjectToast(items)
        self.toast.popup()
        set_level(self.toast, LEVEL_TOAST)

    def _on_step(self, i: int, state: str):
        self.pills[i].set(state)
        self.runscreen.set_step(i, state)
        if state == "run":                       # pasos que usan GPU / Neural Engine a tope: pantalla en ahorro
            self.runscreen.set_eco(i >= 1)         # todo el procesado: no calentar el Mac ni quitar GPU
        if state == "run":
            self.status.setText(f"Paso {i + 1} de {len(STEPS)} · {STEPS[i][0]}…")
            if getattr(self, "last_event", None):       # si RawFlow se cierra de golpe (o se va la luz), al abrirlo
                self._remember_pending(self.last_event, i)   # ofrece seguir desde ESTE paso, no desde cero
        self.timepanel.step(i, state)
        self._run_tick()
        retried = bool(self.pipe and (i in getattr(self.pipe, "retried", set())
                                      or i in getattr(self.pipe, "no_learn", set())))   # reintentos o tandas
        if state == "done" and self.timepanel.dur[i] and not retried:   # con reintentos no es un tiempo normal
            # lo que el paso hizo DE VERDAD (al reanudar, DxO o Retouch4me solo hacen las que faltaban)
            n, nb = getattr(self.pipe, "work", {}).get(i, (self.run_n, self.run_bytes))
            if n > 0:
                self.estimator.learn(i, self.timepanel.dur[i], n, nb)

    @Slot(int, str, str, str)
    def _on_check(self, i: int, state: str, text: str, short: str):
        """Lo comprobado de cada paso: en su pastilla («✓ 597 con Imagen») y en el registro."""
        if 0 <= i < len(self.pills):
            self.pills[i].set_note(state, short)
        self._log(f"    ✓ comprobado · {('paso ' + str(i + 1)) if i < STEP_FINAL else 'revisión final'}: {text}"
                  if state == "ok" else f"    {'⚠' if state == 'warn' else '–'} paso {i + 1}: {text}")

    def _on_progress(self, f: float):
        self.timepanel.frac = f
        if f < 0:
            self.progress.setIndeterminate(True)
        else:
            self.progress.setIndeterminate(False)
            self.progress.setValue(f)

    def _cancel(self):
        if self.countdown_left:
            self._stop_countdown()
            self.status.setText("Autoejecutar cancelado para esta memoria. Pulsa Empezar cuando quieras.")
            return
        if self.pipe:
            self.pipe.stop()
            self.status.setText("Cancelando…")

    # ------------------------------------------------------------ registro (registro.log)
    LOG = (Path(os.environ.get("APPDATA") or Path.home()) / "RawFlow/registro.log" if sys.platform == "win32"
           else Path.home() / "Library/Application Support/RawFlow/registro.log")

    def _log(self, text: str):
        try:
            self.LOG.parent.mkdir(parents=True, exist_ok=True)
            if self.LOG.exists() and self.LOG.stat().st_size > 3_000_000:
                self.LOG.replace(self.LOG.with_suffix(".anterior.log"))
            with open(self.LOG, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")
        except OSError:
            pass
        act = getattr(self, "activity", None)
        if act is not None:
            act.add(text)                          # vale desde cualquier hilo (el panel lo recoge en el suyo)

    @Slot(str)
    def _log_status(self, t: str):
        self._log("· " + t)

    @Slot(str)
    def _log_detail(self, t: str):
        self._log("    " + t)

    @Slot(int, str)
    def _log_step(self, i: int, st: str):
        extra = ""
        if st == "run":
            extra = f"  (estimado {fmt(self.timepanel.est[i])}; total que falta {fmt(self.timepanel.left_seconds())})"
        elif st in ("done", "skip") and self.timepanel.dur[i] is not None:
            extra = f"  en {fmt(self.timepanel.dur[i])} (estimado {fmt(self.timepanel.est[i])})"
        self._log(f"[paso {i + 1} {STEPS[i][0]}] {st}{extra}")

    @Slot(str, str)
    def _log_end(self, ev: str, err: str):
        self._log("=== FIN " + ("OK" if not err else "ERROR: " + err.replace("\n", " | ")))

    def _run_tick(self):
        """Pasa los tiempos a la pantalla de proceso."""
        if not self.runscreen.isVisible() or self.runscreen.fin:
            return
        tp = self.timepanel
        el = time.monotonic() - tp.t0 if tp.t0 else 0.0
        left = tp.left_seconds()
        end = time.strftime("termina ≈ %H:%M", time.localtime(time.time() + left))
        self.runscreen.set_times([v.text() for _, v in tp.rows], fmt(el),
                                 f"faltan {fmt(left)}" if left > 1 else "terminando…", end,
                                 el / (el + left) if el + left > 0 else 0.0)

    LOW_POWER_TXT = "Modo de bajo consumo activado: todo va más lento  ·  desactívalo en Ajustes del Sistema → Batería"

    def _low_power_tick(self):
        """Con el cargador puesto: si el modo de bajo consumo está activado (macOS frena CPU y GPU), aviso en la
        pantalla del proceso y notificación; se quita sola al desactivarlo. Se mira cada 30 s."""
        pn = self.power_note
        now = time.monotonic()
        if now - pn.get("lp_t", -1e9) < 30:
            return
        pn["lp_t"] = now
        on = core.low_power_mode()
        if on and not pn.get("lp"):
            pn["lp"] = True
            self.runscreen.set_power(self.LOW_POWER_TXT)
            self._log("    ⚠ " + self.LOW_POWER_TXT)
            core.alert("RawFlow: modo de bajo consumo", self.LOW_POWER_TXT, "")
        elif not on and pn.get("lp"):
            pn["lp"] = False
            self.runscreen.set_power("")
            self._log("    ✓ Modo de bajo consumo desactivado: a toda velocidad.")

    @Slot()
    def _power_tick(self):
        """Sin cargador durante el evento: aviso rojo en la pantalla, sonido y notificación; con poca batería
        también voz. (El 21-nov-2026 el Mac se quedó sin batería en la última foto y durmió 24 h.)"""
        from . import live
        pw = live.metrics().get("power")
        if not pw or self.pipe is None:
            return
        ac, pct, mins = pw
        pn = self.power_note
        if ac or pct is None:
            if pn["warned"]:
                pn.update(warned=False, level=-1)
                self.runscreen.set_power("")
                self._log("    ✓ Cargador conectado otra vez.")
            self._low_power_tick()
            return
        left = self.timepanel.left_seconds()
        txt = f"Conecta el cargador  ·  batería {pct} %"
        if mins:
            txt += f"  ·  da para ≈ {fmt(mins * 60)}" + (f" y faltan {fmt(left)}" if left > mins * 60 else "")
        self.runscreen.set_power(txt)
        level = 3 if pct <= 8 else 2 if pct <= 15 else 1 if pct <= 30 else 0
        now = time.monotonic()
        if not pn["warned"] or level > pn["level"] or now - pn["last"] >= (600, 300, 120, 60)[level]:
            first = not pn["warned"]
            pn.update(warned=True, level=level, last=now)
            self._log(f"    ⚠ Sin cargador: batería {pct} %" + (f" (da para ≈ {fmt(mins * 60)})" if mins else ""))
            speak = (f"Batería al {pct} por ciento. Conecta el cargador para que RawFlow no se detenga."
                     if first or level >= 2 else "")
            core.alert("RawFlow: conecta el cargador", txt, speak)

    @Slot(bool)
    def _lock_state(self, on: bool):
        """El bloqueo se puso o se quitó: la pantalla de proceso dice la verdad (candado y «Esc tres veces»)."""
        self.runscreen.locked = bool(on) and self.pipe is not None
        self.runscreen.update()

    @Slot()
    def _lock_cancel(self):
        """Esc ×3 durante el proceso: cancelar (el bloqueo ya se soltó)."""
        self.runscreen.locked = False
        self.runscreen.set_texts(status="Cancelando…", detail="Esc pulsado tres veces")
        if self.pipe:
            self.pipe.stop()

    def _on_finished(self, event: str, err: str):
        summary = self.pipe.summary if self.pipe else {}
        self.pipe = None
        self.clock.stop()
        self.inputlock.release()
        self.runscreen.locked = False
        self.power_timer.stop()
        self.runscreen.set_power("")
        if self.sampler is not None:
            self.sampler.stop()
            self.sampler = None
        self.timepanel.finish(not err)
        ev = Path(event)
        if err:
            step = STEPS[summary.get("failed_step", 0)][0]
            title, text = (("Cancelado", "Puedes reintentar desde donde se quedó.") if err == "Cancelado."
                           else (f"Se detuvo en «{step}»", err.splitlines()[0][:120]))
        else:
            n = len(list(folders(ev)[3].glob("*.jp*g")))
            avisos = summary.get("avisos", [])
            title = "¡Listo!" if not avisos else "¡Listo, con avisos!"
            pres = "" if (summary.get("sin_presentacion") or STEP_PRES in getattr(self, "steps_off", ())) else " y la presentación"
            con_imagen = (summary.get("checks", {}).get(STEP_FINAL, ("", ""))[0] == "ok" and STEP_IMG not in self.steps_off)
            k = summary.get("descartadas", 0)
            text = (f"{n:,} fotos finales" + (", todas con Imagen," if con_imagen else "")
                    + f"{pres} en «{ev.name}»  ·  {self.timepanel.total.text()}"
                    + (f"  ·  {k} foto{'s' if k != 1 else ''} mala{'s' if k != 1 else ''} apartada{'s' if k != 1 else ''}" if k else ""))
            if avisos:
                text += f"  ·  {len(avisos)} aviso{'s' if len(avisos) > 1 else ''}"
            for a in avisos:
                self._log("    ⚠ AVISO: " + a)
        self.runscreen.finish(not err, title, text, lambda: self._after_finish(event, err, summary),
                              hold=2.8 if not err else 2.0)

    def _after_finish(self, event: str, err: str, summary: dict):
        try:                                   # el aviso de la memoria sigue si no la sacaste: ahora un clic lo cierra
            if getattr(self, "toast", None) is not None:
                self.toast.allow_dismiss()
        except RuntimeError:
            pass
        self.mini.hide()
        self.mirrored = False
        self.curtain.mirror(None)
        self._pin(False)
        self.cancel.hide()
        self.go.show()
        self.name_edit.setEnabled(True)
        self.open_btn.show()
        self.progress.setIndeterminate(False)
        ev = Path(event)
        self._remember_pending(ev, summary.get("failed_step", 0) if err else None)
        if err:
            self.failed_step = summary.get("failed_step", 0)
            self.retry.setText(f"Reintentar desde el paso {self.failed_step + 1}")
            self.retry.show()
            self.status.setText(f"Se detuvo en «{STEPS[self.failed_step][0]}»: {err.splitlines()[0]}")
            if err != "Cancelado.":
                QMessageBox.warning(self, APP_NAME, f"Paso {self.failed_step + 1} ({STEPS[self.failed_step][0]}):\n\n{err}")
        else:
            _, _, img, final = folders(ev, STEP_IMG not in self.steps_off)
            n = len(list(final.glob("*.jp*g")))
            self.progress.setValue(1.0)
            avisos = summary.get("avisos", [])
            off = [STEPS[k][0] for k in sorted(self.steps_off)]
            k = summary.get("descartadas", 0)
            from . import seleccion
            por = seleccion.summary(summary.get("malas") or {})          # «10 repetidas · 2 con ojos cerrados»
            if k:
                self._log(f"    Selección IA: {k} apartada(s) en «{core.RAW_SUBFOLDER}/{core.BAD_SUBFOLDER}»"
                          + (f": {por}" if por else ""))
            self.status.setText(f"✓  Listo: «{ev.name}» · {n:,} fotos finales"
                                + (f" · {len(avisos)} aviso(s)" if avisos else
                                   (" retocadas" if STEP_RET not in self.steps_off else ""))
                                + (f" · {k} foto{'s' if k != 1 else ''} mala{'s' if k != 1 else ''} apartada{'s' if k != 1 else ''} "
                                   "en «1 Original/Fotos malas»" + (f" ({por})" if por else "") if k else "")
                                + (f" · desactivado: {', '.join(off)}" if off else ""))
            self.detail.setText(f"{core.RAW_SUBFOLDER} · {core.OUTPUT_SUBFOLDER} · {img.name} "
                                f"({len(list(img.glob('*.jp*g')))}) · {FINAL_DIR} ({n})")
            core.reveal_in_file_manager(ev)
            if avisos:                          # el evento terminó entero; esto es lo que no salió perfecto
                QMessageBox.information(self, APP_NAME, "El evento terminó, con estos avisos:\n\n• "
                                        + "\n• ".join(avisos))
        self.showing_result = True
        for key in list(self.dropped):         # lo arrastrado ya está en el evento: su tarjeta sobra
            if key in self.cards:
                self._remove_drive(key)
            self.dropped.pop(key, None)
        try:                                   # las memorias se expulsaron durante el proceso
            self._drives_changed(core.list_drives())
        except Exception:  # noqa: BLE001
            pass
        self._update_summary()

    # ------------------------------------------------------------ otros
    def _show_permissions(self):
        from .permissions_dialog import PermissionsDialog
        if self.perm_dialog is None:
            self.perm_dialog = PermissionsDialog(self)
        self.perm_dialog.show()
        self.perm_dialog.raise_()
        self.perm_dialog.activateWindow()

    def _install_vm(self):
        from .vm_dialog import InstallVMDialog
        if self.vm_install_dialog is None or (self.vm_install_dialog.worker is None
                                              and not self.vm_install_dialog.isVisible()):
            self.vm_install_dialog = InstallVMDialog(self.settings, self)
        self.vm_install_dialog.show()
        self.vm_install_dialog.raise_()

    MAX_CONTENT = 1840                              # en un monitor ultrapanorámico el contenido no se estira: se centra

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        lay = getattr(self, "_outer", None)
        if lay is not None:
            side = max(28, (self.width() - self.MAX_CONTENT) // 2)
            m = lay.contentsMargins()
            if m.left() != side:
                lay.setContentsMargins(side, m.top(), side, m.bottom())

    def closeEvent(self, e):  # noqa: N802
        if self.pipe and self.pipe.isRunning():
            r = QMessageBox.question(self, APP_NAME, "Hay un evento en proceso. ¿Detenerlo y salir?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        try:                                        # la próxima vez se abre en la pantalla donde la dejaste
            if self.screen() is not None:
                self.settings.setValue("pantalla", self.screen().name())
        except Exception:  # noqa: BLE001
            pass
        self.shutdown()
        super().closeEvent(e)

    def shutdown(self):
        """Salir LIMPIO: se para todo lo que trabaja en segundo plano y se espera a cada hilo lo que necesita (un
        «diskutil» puede tardar 5 s). Si un hilo de Qt siguiera vivo al destruir la ventana, Qt aborta y macOS
        enseña «se cerró inesperadamente»: por eso main() sale sin destruir nada si aún queda alguno."""
        for t in (getattr(self, "clock", None), getattr(self, "power_timer", None),
                  getattr(self, "ready_timer", None)):
            try:
                t and t.stop()
            except RuntimeError:
                pass
        if self.pipe is not None and self.pipe.isRunning():
            self.pipe.stop()
        try:
            self.inputlock.release()          # ratón y teclado libres, pase lo que pase
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "sampler", None) is not None:
            try:
                self.sampler.stop()
            except Exception:  # noqa: BLE001
                pass
        self.watcher.stop()
        for w in list(self.scanners.values()):
            w.stop()
        for w in (self.mini, self.curtain):
            try:
                w.close()
            except RuntimeError:
                pass
        threads = [self.pipe, self.watcher, self.inputlock] + list(self.scanners.values())
        deadline = time.monotonic() + 20
        for th in threads:
            try:
                if th is not None and th.isRunning():
                    th.wait(max(100, int((deadline - time.monotonic()) * 1000)))
            except RuntimeError:
                pass
        try:
            self.settings.sync()
        except Exception:  # noqa: BLE001
            pass

    def threads_alive(self) -> bool:
        for th in [self.pipe, self.watcher, self.inputlock] + list(self.scanners.values()):
            try:
                if th is not None and th.isRunning():
                    return True
            except RuntimeError:
                pass
        return False


def _crash_guard():
    """Fallos inesperados: quedan escritos (con su detalle) en Application Support/RawFlow/fallos.log en vez de cerrar
    RawFlow de golpe. Los de Python dentro de la interfaz se registran y la app sigue; los nativos (si los hubiera)
    dejan su traza ahí mismo para poder arreglarlos."""
    import faulthandler
    import traceback
    d = Path.home() / ("Library/Application Support/RawFlow" if sys.platform == "darwin"
                       else "AppData/Roaming/RawFlow")
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = open(d / "fallos.log", "a", encoding="utf-8")         # abierto toda la vida de la app (faulthandler)
        faulthandler.enable(file=f)
    except Exception:  # noqa: BLE001
        f = None

    def hook(kind, value, tb):
        txt = "".join(traceback.format_exception(kind, value, tb))
        try:
            if f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + txt + "\n")
                f.flush()
        except Exception:  # noqa: BLE001
            pass
        sys.__stderr__ and sys.__stderr__.write(txt)
    sys.excepthook = hook
    try:
        import threading
        threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:  # noqa: BLE001
        pass


def start_screen(settings):
    """En qué pantalla se abre RawFlow: donde la dejaste la última vez; si nunca la moviste y hay más de un monitor,
    en el que NO es el principal (el principal queda libre para ver trabajar a DxO, Imagen, Bridge…); si solo hay
    uno, en ese. None = dejar que el sistema decida."""
    from PySide6.QtGui import QGuiApplication
    screens = QGuiApplication.screens()
    if len(screens) < 2 or sys.platform != "win32":      # en el Mac se abre donde decide macOS, como siempre
        return None
    saved = str(settings.value("pantalla", "") or "")
    for s in screens:
        if saved and s.name() == saved:
            return s
    prim = QGuiApplication.primaryScreen()
    otros = [s for s in screens if s is not prim]
    return max(otros, key=lambda s: s.geometry().width() * s.geometry().height()) if otros else None


def main():
    _crash_guard()
    if sys.platform == "win32":             # barra de tareas: icono y grupo propios (no los de Python)
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RawFlow.RawFlow")
        except Exception:  # noqa: BLE001
            pass
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    QLocale.setDefault(QLocale(QLocale.Language.Spanish, QLocale.Country.Mexico))
    try:                                    # los botones de Qt («Sí», «No», «Aceptar»…) en español, no «Yes / No»
        from PySide6.QtCore import QLibraryInfo, QTranslator
        tr = QTranslator(app)
        if tr.load("qtbase_es", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(tr)
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":             # RawFlow es oscuro siempre: con Windows en tema claro los avisos salían con
        try:                                # letra blanca sobre fondo claro (ilegibles) y la barra de título, blanca
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        except Exception:  # noqa: BLE001
            pass
    app.setStyle("Fusion")
    app.setFont(font(13))
    app.setStyleSheet(T.QSS)
    app.setWindowIcon(app_icon())
    win = MainWindow()
    app.aboutToQuit.connect(win.shutdown)   # también al salir con ⌘Q o desde el Dock
    import threading
    from .pipeline import close_on_launch    # al abrir: UTM y los programas del proceso, cerrados (en segundo plano)
    threading.Thread(target=close_on_launch, args=(win._log,), daemon=True, name="cerrar-al-abrir").start()
    scr = start_screen(win.settings)        # con dos monitores: por defecto en el OTRO (el que no es el principal)
    if scr is not None:
        g = scr.availableGeometry()
        win.setGeometry(g.x() + 40, g.y() + 40, min(1280, g.width() - 80), min(820, g.height() - 80))
    tint_titlebar(win)                      # Windows 11: la barra de título, del color de la ventana
    win.showMaximized()                     # grande, ocupando la pantalla
    win.start_update_check()                # ¿hay versión nueva en GitHub? (en segundo plano)
    anim.fade_in_window(win)
    code = app.exec()
    win.shutdown()
    if win.threads_alive():                 # algo no paró a tiempo: salir sin destruir hilos vivos (sin aviso de fallo)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    sys.exit(code)


if __name__ == "__main__":
    main()

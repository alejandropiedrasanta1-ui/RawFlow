"""Pequeñas animaciones de la interfaz principal (entradas suaves, brillo del botón Empezar)."""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget

from . import theme as T

_keep = []          # mantener vivas las animaciones en curso


def _drop(a):
    _keep[:] = [x for x in _keep if x is not a]


def _hold(a):
    """Arranca la animación y la mantiene viva hasta que acabe (o hasta que su widget desaparezca)."""
    try:
        _keep.append(a)
        a.finished.connect(lambda: _drop(a))
        a.destroyed.connect(lambda *_: _drop(a))      # el widget se quitó a media animación: no se queda guardada
        a.start()
    except RuntimeError:          # el widget ya no existe (se quitó antes de empezar): no hay nada que animar
        _drop(a)


def fade_in_window(w: QWidget, ms: int = 380):
    w.setWindowOpacity(0.0)
    a = QPropertyAnimation(w, b"windowOpacity")
    a.setDuration(ms)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.Type.OutCubic)
    _hold(a)


def slide_in(w: QWidget, ms: int = 420):
    """La tarjeta crece desde 0 de alto y aparece (sin descolocar el resto de la columna)."""
    if T.reduce_motion():             # «Reducir movimiento»: aparece ya en su sitio
        return
    lo, hi = w.minimumHeight(), w.maximumHeight()
    fixed = lo == hi                  # alto fijo (las tarjetas de memoria): con solo bajar el máximo no encogía,
    #                                   el mínimo mandaba y la tarjeta salía de golpe; y al acabar perdía su alto fijo
    target = lo if fixed else max(w.sizeHint().height(), 60)
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    w.setFixedHeight(0) if fixed else w.setMaximumHeight(0)
    a = QVariantAnimation(w, startValue=0.0, endValue=1.0, duration=ms,
                          easingCurve=QEasingCurve.Type.OutCubic)

    def step(v):
        v = float(v)
        try:
            h = int(target * v)
            w.setFixedHeight(h) if fixed else w.setMaximumHeight(h)
            eff.setOpacity(v)
        except RuntimeError:          # la tarjeta ya no existe
            a.stop()

    def done():
        try:
            if fixed:
                w.setFixedHeight(target)
            else:
                w.setMaximumHeight(hi)
            if w.graphicsEffect() is eff:
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass

    a.valueChanged.connect(step)
    a.finished.connect(done)
    _hold(a)


def slide_out(w: QWidget, then=None, ms: int = 240):
    """La tarjeta se encoge hasta 0 de alto mientras se desvanece, y entonces se quita (`then`)."""
    if T.reduce_motion() or not w.isVisible():
        if then:
            then()
        return
    eff = QGraphicsOpacityEffect(w)
    w.setGraphicsEffect(eff)
    h0 = max(1, w.height())
    a = QVariantAnimation(w, startValue=0.0, endValue=1.0, duration=ms, easingCurve=QEasingCurve.Type.InOutCubic)

    def step(v):
        v = float(v)
        try:
            w.setFixedHeight(int(h0 * (1.0 - v)))     # alto exacto en cada fotograma (la tarjeta no tiene «tamaño ideal»)
            eff.setOpacity(1.0 - v)
        except RuntimeError:          # ya no existe
            a.stop()

    def done():
        if then:
            try:
                then()
            except RuntimeError:
                pass

    a.valueChanged.connect(step)
    a.finished.connect(done)
    _hold(a)


def fade_in(w: QWidget, ms: int = 220, start: float = 0.0, delay: int = 0):
    """El widget aparece con un fundido (desde `start` de opacidad). Al terminar se quita el efecto: no cuesta nada."""
    if T.reduce_motion() or not w.isVisible():
        return
    old = getattr(w, "_rf_fade", None)
    if old is not None:
        try:
            old.stop()
            old.deleteLater()
        except RuntimeError:
            pass
        _drop(old)                    # (parada a mano no avisa de que «terminó»: se quedaba guardada para siempre)
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(start)
    w.setGraphicsEffect(eff)
    a = QVariantAnimation(w, startValue=float(start), endValue=1.0, duration=ms, easingCurve=QEasingCurve.Type.OutCubic)

    def step(v):
        try:
            eff.setOpacity(float(v))
        except RuntimeError:
            a.stop()

    def done():
        try:
            if w.graphicsEffect() is eff:
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass

    a.valueChanged.connect(step)
    a.finished.connect(done)
    w._rf_fade = a
    if delay:
        QTimer.singleShot(delay, lambda: _hold(a))
    else:
        _hold(a)


class Glow:
    """Brillo lima que «respira» alrededor de un botón mientras está activo (llama a pulsar Empezar)."""

    def __init__(self, button: QWidget):
        self.b = button
        self.eff = QGraphicsDropShadowEffect(button)
        self.eff.setOffset(0, 0)
        self.eff.setColor(QColor(T.ACCENT))
        self.eff.setBlurRadius(0)
        button.setGraphicsEffect(self.eff)
        self.t = 0.0
        self.timer = QTimer(button, interval=33)        # 30 fps: respira suave (≈2,5 ms por fotograma)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        # solo con RawFlow delante: al fondo (o tapada) no se anima, así no gasta CPU ni calienta el Mac
        w = self.b.window()
        if not self.b.isVisible() or not self.b.isEnabled() or not (w and w.isActiveWindow()):
            if self.eff.blurRadius():
                self.eff.setBlurRadius(0)
            return
        self.t += 0.033
        k = 0.5 + 0.5 * math.sin(self.t * 2.4)
        c = QColor(T.ACCENT)
        c.setAlphaF(0.35 + 0.35 * k)
        self.eff.setColor(c)
        self.eff.setBlurRadius(14 + 22 * k)

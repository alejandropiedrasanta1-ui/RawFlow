"""Ventanas flotantes de RawFlow:
  · EjectToast — «Ya puedes retirar la memoria» con la tarjeta saliendo (animación).
  · MiniPanel  — panel compacto siempre encima mientras RawFlow maneja otras apps (pasos 3 y 4);
                 no recibe clics y RawFlow lo ignora al leer la pantalla.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme as T
from .widgets import font

# Rectángulo (x, y, w, h) en puntos de pantalla del panel flotante visible, o None.
# imagen_ui.Screen.ocr() descarta lo que caiga ahí dentro.
OVERLAY_RECT = None


def _flags(click_through: bool) -> Qt.WindowType:
    f = (Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
         | Qt.WindowType.WindowDoesNotAcceptFocus)
    if click_through:
        f |= Qt.WindowType.WindowTransparentForInput
    return f


# --------------------------------------------------------------------------
TOAST_RECT = None        # rectángulo del aviso de la memoria en pantalla (el OCR lo ignora), o None


class EjectToast(QWidget):
    """«Ya puedes retirar la memoria», con la tarjeta saliendo del lector. Se QUEDA en pantalla, animándose, hasta que
    sacas la memoria de verdad: RawFlow la desmonta sin expulsarla y vigila /dev/diskN, que macOS borra al retirarla.
    En Windows vigila el dispositivo USB expulsado («win:<id>», en espera hasta desenchufarlo; core.win_devnode).
    No recibe clics (RawFlow sigue manejando las apps de debajo). Al acabar el evento, un clic también lo cierra, por
    si algún lector no avisara al sacar la tarjeta."""

    CYCLE, MOVE = 3.2, 1.6          # s: la tarjeta sale en 1,6 s y espera 1,6 s antes de repetir

    def __init__(self, items):
        super().__init__(None, _flags(True))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if isinstance(items, str):                     # solo nombres (sin disco que vigilar)
            items = [{"name": n.strip(), "disk": ""} for n in items.split(",") if n.strip()]
        self.items = [dict(it) for it in items]
        if any(it.get("disk") for it in self.items):   # las que ya no estaban no hace falta esperarlas
            self.items = [it for it in self.items if it.get("disk")]
        self.t, self.a = 0.0, 1.0                      # avance de la tarjeta y su opacidad al reaparecer
        self.t0 = time.monotonic()
        self.done_t = 0.0
        self._closing = False
        self.resize(440, 170)
        self.tick = QTimer(self, interval=33)          # ~30 fps solo mientras la tarjeta se mueve
        self.tick.timeout.connect(self._tick)
        self.poll = QTimer(self, interval=600)         # ¿sigue puesta?
        self.poll.timeout.connect(self._check)
        self.setWindowOpacity(0.0)
        self.fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=250)
        self.fade.valueChanged.connect(self._opacity)

    @property
    def names(self) -> str:
        return ", ".join(it["name"] for it in self.items)

    def watching(self) -> bool:
        return any(it.get("disk") for it in self.items)

    def add(self, items):
        """Otra memoria soltada mientras el aviso sigue en pantalla: se suma a la espera."""
        have = {(it["name"], it.get("disk")) for it in self.items}
        self.items += [dict(it) for it in items if (it["name"], it.get("disk")) not in have and it.get("disk")]
        self.update()
        if self.watching() and not self.poll.isActive():
            self.poll.start()

    def _opacity(self, v):
        self.setWindowOpacity(float(v))

    def place(self):
        """A la izquierda del anillo de la pantalla de proceso (no tapa el nombre del evento ni los pasos)."""
        global TOAST_RECT
        g = (getattr(self, "target", None) or QGuiApplication.primaryScreen()).geometry()   # la de la pantalla de proceso
        cx, cy = g.center().x(), g.top() + g.height() * 0.43
        R = min(g.width(), g.height()) * 0.165
        x, y = int(cx - R - 48 - self.width()), int(cy - self.height() / 2)
        if x < g.left() + 16:                          # pantalla estrecha: arriba, en el centro
            x, y = g.center().x() - self.width() // 2, g.top() + 60
        self.move(x, y)
        TOAST_RECT = (x, y, self.width(), self.height())

    def popup(self):
        self.place()
        self.show()
        self.raise_()
        self.fade.start()
        if not T.reduce_motion():                      # «Reducir movimiento»: tarjeta quieta en la ranura
            self.tick.start()
        if self.watching():
            self.poll.start()
        else:                                          # no hay disco que vigilar (lector que solo suelta la tarjeta)
            QTimer.singleShot(9000, self._out)         # (antes 4,2 s: se iba antes de llegar a sacarla)

    def poke(self):
        """Windows avisó de un cambio de dispositivos: mirar ya si la quitaste (sin esperar a la siguiente vuelta)."""
        if self.watching() and not self._closing and not self.done_t:
            self._check()

    def allow_dismiss(self):
        """Fin del evento: si el aviso sigue, un clic lo cierra."""
        if self._closing or not self.isVisible():
            return
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, False)
        self.show()                                    # setWindowFlag la oculta: volver a mostrarla y subirla
        set_level(self, LEVEL_TOAST)

    def _tick(self):
        if self.done_t:                                # «✓ Memoria retirada»: la palomita se dibuja (≈0,4 s)
            if time.monotonic() - self.done_t < 0.5:
                self.update()
            return
        el = (time.monotonic() - self.t0) % self.CYCLE
        if el < self.MOVE:                             # sale del lector y se desvanece
            t, a = el / self.MOVE, 1.0
        else:                                          # vuelve a aparecer en la ranura y espera
            t, a = 0.0, min(1.0, (el - self.MOVE) / 0.35)
        if (t, a) == (self.t, self.a):
            return                                     # quieta: no se repinta
        self.t, self.a = t, a
        self.update()

    def _check(self):
        from . import core
        still = [it for it in self.items if core.drive_present(it.get("disk", ""))]
        if len(still) != len(self.items):
            self.items = still or self.items
            self.update()
        if not still and not self.done_t:              # retirada: «✓ Memoria retirada» y se va
            self.poll.stop()
            self.done_t = time.monotonic()
            if not self.tick.isActive() and not T.reduce_motion():
                self.tick.start()                      # (para dibujar la palomita)
            self.update()
            QTimer.singleShot(1400, self._out)

    def _out(self):
        global TOAST_RECT
        if self._closing:
            return
        self._closing = True
        TOAST_RECT = None
        self.tick.stop()
        self.poll.stop()
        self.fade.setDirection(QVariantAnimation.Direction.Backward)
        self.fade.finished.connect(self.close)
        self.fade.start()

    def mousePressEvent(self, e):  # noqa: N802
        self._out()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        ok = bool(self.done_t)
        # (4.26) al retirarla ya no cambia de golpe: el borde pasa a verde, la palomita se dibuja y el texto entra
        k = 1.0 if (not ok or T.reduce_motion()) else min(1.0, (time.monotonic() - self.done_t) / 0.4)
        ease = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(k)
        if ok:
            a, b = QColor(T.ACCENT), QColor(T.OK)
            edge = QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * ease, a.greenF() + (b.greenF() - a.greenF()) * ease,
                                   a.blueF() + (b.blueF() - a.blueF()) * ease)
        else:
            edge = QColor(T.ACCENT)
        p.setPen(QPen(edge, 1.5))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(r, 20, 20)
        if ok:                                         # memoria retirada
            path = QPainterPath()
            path.moveTo(r.left() + 52, r.center().y() + 2)
            path.lineTo(r.left() + 70, r.center().y() + 20)
            path.lineTo(r.left() + 104, r.center().y() - 18)
            pen = QPen(QColor(T.OK), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            if ease < 1.0:                             # trazo parcial: la palomita se dibuja de izquierda a derecha
                n = path.length() / 4.0                # (el patrón va en anchos de línea)
                pen.setDashPattern([max(0.01, n * ease), n + 1])
            p.setPen(pen)
            p.drawPath(path)
            p.setOpacity(ease)
            p.setPen(QColor(T.TEXT))
            p.setFont(font(20, QFont.Weight.Bold))
            p.drawText(QRectF(r.left() + 150 - 12 * (1 - ease), r.top(), r.width() - 165, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Memoria retirada")
            return
        # ranura del lector (abajo) y tarjeta SD saliendo hacia arriba, desvaneciéndose
        slot = QRectF(r.left() + 28, r.bottom() - 34, 100, 10)
        x0 = slot.left() + 22
        y = slot.top() - 70 - 48 * QEasingCurve(QEasingCurve.Type.InOutCubic).valueForProgress(self.t)
        card = QPainterPath()
        card.moveTo(x0, y + 76)
        card.lineTo(x0, y + 14)
        card.lineTo(x0 + 14, y)
        card.lineTo(x0 + 56, y)
        card.lineTo(x0 + 56, y + 76)
        card.closeSubpath()
        p.save()
        p.setClipRect(QRectF(r.left(), r.top() + 6, 160, slot.top() + 5 - (r.top() + 6)))
        p.setOpacity(self.a * (1.0 - max(0.0, self.t - 0.65) / 0.35))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT))
        p.drawPath(card)
        p.setBrush(QColor(T.ACCENT_INK))
        for k in range(4):
            p.drawRoundedRect(QRectF(x0 + 18 + k * 9, y + 6, 5, 14), 2, 2)
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER_HI))
        p.drawRoundedRect(slot, 5, 5)
        # textos
        p.setPen(QColor(T.TEXT))
        p.setFont(font(19, QFont.Weight.Bold))
        p.drawText(QRectF(r.left() + 150, r.top() + 30, r.width() - 165, 30), Qt.AlignmentFlag.AlignLeft,
                   "Ya puedes retirar")
        p.drawText(QRectF(r.left() + 150, r.top() + 56, r.width() - 165, 30), Qt.AlignmentFlag.AlignLeft,
                   "la memoria")
        p.setPen(QColor(T.MUTED))
        p.setFont(font(12.5))
        p.drawText(QRectF(r.left() + 150, r.top() + 94, r.width() - 165, 50),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   f"{self.names}: fotos copiadas. Este aviso sigue hasta que la saques; RawFlow continúa."
                   if self.watching() else
                   f"{self.names}: fotos copiadas y memoria expulsada. RawFlow sigue con el resto.")


# --------------------------------------------------------------------------
class MiniPanel(QWidget):
    """Panel compacto, siempre encima, abajo en el centro: paso actual, tiempo y lo que falta."""

    def __init__(self):
        super().__init__(None, _flags(True))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(560, 74)
        self.title, self.sub, self.right = "", "", ""
        self.frac = -1.0
        self.phase = 0.0
        self.timer = QTimer(self, interval=60, timeout=self._spin)

    def _spin(self):
        self.phase = (self.phase + 0.02) % 1.0
        self.update()

    def place(self):
        global OVERLAY_RECT
        g = QGuiApplication.primaryScreen().availableGeometry()
        x, y = g.center().x() - self.width() // 2, g.bottom() - self.height() - 14
        self.move(x, y)
        OVERLAY_RECT = (x - 6, y - 6, self.width() + 12, self.height() + 12)

    def set(self, title: str = None, sub: str = None, right: str = None, frac: float = None):
        if title is not None:
            self.title = title
        if sub is not None:
            self.sub = sub
        if right is not None:
            self.right = right
        if frac is not None:
            self.frac = frac
        self.update()

    def showEvent(self, e):  # noqa: N802
        self.place()
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        global OVERLAY_RECT
        OVERLAY_RECT = None
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(T.ACCENT), 1.2))
        p.setBrush(QColor(13, 14, 17, 238))
        p.drawRoundedRect(r, 18, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT))
        p.drawEllipse(QRectF(r.left() + 18, r.center().y() - 5, 10, 10))
        p.setPen(QColor(T.TEXT))
        p.setFont(font(14, QFont.Weight.Bold))
        p.drawText(QRectF(r.left() + 40, r.top() + 12, r.width() - 200, 22), Qt.AlignmentFlag.AlignLeft,
                   f"RAWFLOW · {self.title}")
        p.setPen(QColor(T.MUTED))
        p.setFont(font(11.5))
        fm = p.fontMetrics()
        p.drawText(QRectF(r.left() + 40, r.top() + 36, r.width() - 200, 20), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.sub, Qt.TextElideMode.ElideRight, int(r.width() - 200)))
        p.setPen(QColor(T.ACCENT))
        p.setFont(font(13, QFont.Weight.Bold))
        p.drawText(QRectF(r.right() - 160, r.top() + 12, 145, 22), Qt.AlignmentFlag.AlignRight, self.right)
        # barra
        bar = QRectF(r.left() + 40, r.bottom() - 12, r.width() - 56, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER))
        p.drawRoundedRect(bar, 2, 2)
        p.setBrush(QColor(T.ACCENT))
        if 0 <= self.frac <= 1:
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * self.frac, bar.height()), 2, 2)
        else:
            w = bar.width() * 0.25
            p.drawRoundedRect(QRectF(bar.left() + (bar.width() - w) * self.phase, bar.top(), w, bar.height()), 2, 2)


def now() -> float:
    return time.monotonic()


# --------------------------------------------------------------------------
# Niveles de ventana de macOS: por encima de la barra de menús (24) y del Dock (20).
LEVEL_CURTAIN, LEVEL_MAIN, LEVEL_TOAST = 25, 26, 27


def set_level(w: QWidget, level: int):
    """Pone la ventana nativa de `w` en un nivel de macOS (0 = normal). Sin efecto fuera de Mac."""
    import sys
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        win = send(ctypes.c_void_p(int(w.winId())), objc.sel_registerName(b"window"))
        if not win:
            return
        setl = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(("objc_msgSend", objc))
        setl(win, objc.sel_registerName(b"setLevel:"), level)
        # en todos los escritorios y fuera del Stage Manager / Cmd+Tab
        setl(win, objc.sel_registerName(b"setCollectionBehavior:"), (1 | 16 | 64 | 256) if level else 0)
    except Exception:  # noqa: BLE001
        pass


class Curtain(QWidget):
    """Fondo de RawFlow a pantalla completa durante todo el proceso: tapa DxO, Imagen, Bridge y Retouch4me.
    No recibe clics (pasan a la app de debajo) ni el foco. Mientras RawFlow maneja otras apps (pasos 3-4)
    la ventana de RawFlow se oculta y aquí se pinta su imagen en directo, en el mismo sitio."""

    def __init__(self):
        super().__init__(None, _flags(True))
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.src = None            # ventana de RawFlow a pintar (oculta) o None
        self.src_pos = None
        self.snap = None
        self.hint = ""
        self.timer = QTimer(self, interval=500, timeout=self._refresh)

    def cover(self):
        self.setGeometry(QGuiApplication.primaryScreen().geometry())
        self.show()
        set_level(self, LEVEL_CURTAIN)

    def mirror(self, w: QWidget = None, hint: str = ""):
        """w: ventana de RawFlow que se acaba de ocultar (se pinta aquí); None: dejar de pintarla."""
        self.src, self.hint = w, hint
        if w is not None:
            self.src_pos = w.geometry().topLeft() - self.geometry().topLeft()
            self._refresh()
            self.timer.start()
        else:
            self.timer.stop()
            self.snap = None
            self.update()

    def _refresh(self):
        if self.src is not None:
            self.snap = self.src.grab()
            self.update()

    def hideEvent(self, e):  # noqa: N802
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))
        if self.snap is not None and self.src_pos is not None:
            p.drawPixmap(self.src_pos, self.snap)
        if self.hint:
            p.setPen(QColor(T.MUTED))
            p.setFont(font(12.5))
            p.drawText(QRectF(0, self.height() - 46, self.width(), 24), Qt.AlignmentFlag.AlignCenter, self.hint)

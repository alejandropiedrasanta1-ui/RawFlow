"""Ventanas flotantes de RawFlow:
  · EjectToast — «Ya puedes retirar la memoria» con la tarjeta saliendo (animación).
  · MiniPanel  — panel compacto siempre encima mientras RawFlow maneja otras apps (pasos 3 y 4);
                 no recibe clics y RawFlow lo ignora al leer la pantalla.
"""
from __future__ import annotations

import sys
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

    CYCLE, MOVE = 2.4, 1.2          # s: la USB sale del puerto en 1,2 s y vuelve a empezar
    # 26-nov-2026, el usuario: «tiene que estar hasta que yo retire la USB… que esté en grande avisando saca USB».
    # Grande, en el centro de la pantalla de proceso, con el borde latiendo; no se va sola hasta que la quitas.

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
        self.resize(900, 400)                          # (place() lo ajusta a la pantalla)
        self.tick = QTimer(self, interval=40)          # ~25 fps mientras está en pantalla (es grande: no gastar)
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
        """GRANDE y en el centro de la pantalla de proceso (que se vea desde lejos)."""
        global TOAST_RECT
        import sys
        g = (getattr(self, "target", None) or QGuiApplication.primaryScreen()).geometry()   # la de la pantalla de proceso
        if sys.platform == "darwin":
            # Mac: el OCR de Imagen IGNORA lo que cae dentro de TOAST_RECT (imagen_ui): uno grande en el centro le
            # taparía los botones toda la vida del aviso (revisión 26-nov-2026). Ahí sigue pequeño y a un lado.
            w, h = 520, 200
            self.resize(w, h)
            cx, cy = g.center().x(), g.top() + g.height() * 0.43
            R = min(g.width(), g.height()) * 0.165
            x, y = int(cx - R - 48 - w), int(cy - h / 2)
            if x < g.left() + 16:
                x, y = g.center().x() - w // 2, g.top() + 60
            self.move(x, y)
            TOAST_RECT = (x, y, w, h)
            return
        # Windows: no sale en las capturas (hide_from_capture) ni recibe clics: puede ir grande y en el centro
        w = int(max(560, min(1100, g.width() * 0.62)))
        h = int(max(260, min(480, g.height() * 0.40, w * 0.44)))
        self.resize(w, h)
        x, y = g.center().x() - w // 2, int(g.top() + g.height() * 0.45 - h / 2)
        self.move(x, y)
        TOAST_RECT = None

    def popup(self):
        self.place()
        self.show()
        self.raise_()
        self.fade.start()
        if not T.reduce_motion():                      # «Reducir movimiento»: tarjeta quieta en la ranura
            self.tick.start()
        if self.watching():
            self.poll.start()
        elif sys.platform == "darwin":                 # Mac sin disco que vigilar: ya no estaba puesta (release_drive
            QTimer.singleShot(9000, self._out)         # devolvió ""): no hay nada que esperar
        # (Windows: siempre hay algo que vigilar —«win:» o «wait:»—; se queda hasta que la quitas o la cierras)

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
        if el < self.MOVE:                             # sale del puerto y se desvanece
            t, a = el / self.MOVE, 1.0
        else:                                          # vuelve a aparecer enchufada y espera
            t, a = 0.0, min(1.0, (el - self.MOVE) / 0.35)
        self.t, self.a = t, a
        self.update()                                  # (el borde late todo el rato)

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
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        H = r.height()
        ok = bool(self.done_t)
        # al retirarla no cambia de golpe: el borde pasa a verde, la palomita se dibuja y el texto entra
        k = 1.0 if (not ok or T.reduce_motion()) else min(1.0, (time.monotonic() - self.done_t) / 0.4)
        ease = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(k)
        if ok:
            a, b = QColor(T.ACCENT), QColor(T.OK)
            edge = QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * ease, a.greenF() + (b.greenF() - a.greenF()) * ease,
                                   a.blueF() + (b.blueF() - a.blueF()) * ease)
            width = 4.0
        else:                                          # el borde LATE (se ve de reojo desde lejos)
            beat = 0.5 + 0.5 * math.sin((time.monotonic() - self.t0) * 2 * math.pi / 1.2)
            edge = QColor(T.ACCENT)
            edge.setAlphaF(0.45 + 0.55 * (0.0 if T.reduce_motion() else beat))
            width = 3.0 + 5.0 * (0.0 if T.reduce_motion() else beat)
        p.setPen(QPen(edge, width))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(r.adjusted(width / 2, width / 2, -width / 2, -width / 2), 28, 28)
        art = QRectF(r.left() + H * 0.12, r.top() + H * 0.10, H * 0.62, H * 0.80)   # el dibujo, a la izquierda
        text_x = art.right() + H * 0.14
        tw = r.right() - text_x - H * 0.10
        if ok:                                         # memoria retirada: palomita grande
            path = QPainterPath()
            cx, cy, u = art.center().x(), art.center().y(), H * 0.16
            path.moveTo(cx - 1.4 * u, cy)
            path.lineTo(cx - 0.4 * u, cy + u)
            path.lineTo(cx + 1.5 * u, cy - 1.1 * u)
            pen = QPen(QColor(T.OK), H * 0.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            if ease < 1.0:                             # trazo parcial: la palomita se dibuja de izquierda a derecha
                n = path.length() / pen.widthF()
                pen.setDashPattern([max(0.01, n * ease), n + 1])
            p.setPen(pen)
            p.drawPath(path)
            p.setOpacity(ease)
            p.setPen(QColor(T.OK))
            p.setFont(font(H * 0.15, QFont.Weight.Bold))
            p.drawText(QRectF(text_x - 12 * (1 - ease), r.top(), tw, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "USB RETIRADA")
            return
        # puerto (abajo) y memoria USB saliendo hacia arriba, con una flecha que sube
        port = QRectF(art.left(), art.bottom() - H * 0.10, art.width(), H * 0.10)
        sw = art.width() * 0.42                        # ancho de la USB
        sx = art.center().x() - sw / 2
        rise = H * 0.30 * QEasingCurve(QEasingCurve.Type.InOutCubic).valueForProgress(self.t)
        body_h, plug_h = H * 0.36, H * 0.13
        top = port.top() - body_h - plug_h * 0.25 - rise
        p.save()
        p.setClipRect(QRectF(r.left(), r.top(), art.right() - r.left() + H * 0.05, port.top() + 2 - r.top()))
        p.setOpacity(self.a * (1.0 - max(0.0, self.t - 0.7) / 0.3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER_HI))                # conector metálico
        p.drawRoundedRect(QRectF(sx + sw * 0.18, top + body_h - 2, sw * 0.64, plug_h), 4, 4)
        p.setBrush(QColor(T.SURFACE))
        for k2 in range(2):
            p.drawRect(QRectF(sx + sw * (0.30 + 0.25 * k2), top + body_h + plug_h * 0.25, sw * 0.14, plug_h * 0.3))
        p.setBrush(QColor(T.ACCENT))                   # cuerpo de la USB
        p.drawRoundedRect(QRectF(sx, top, sw, body_h), sw * 0.18, sw * 0.18)
        p.setPen(QPen(QColor(T.ACCENT_INK), max(2.0, H * 0.012)))
        p.setFont(font(H * 0.06, QFont.Weight.Bold))
        p.drawText(QRectF(sx, top, sw, body_h), Qt.AlignmentFlag.AlignCenter, "USB")
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER_HI))                # el puerto
        p.drawRoundedRect(port, 8, 8)
        p.setBrush(QColor(T.BG) if hasattr(T, "BG") else QColor(0, 0, 0))
        p.drawRoundedRect(QRectF(sx + sw * 0.12, port.top() + port.height() * 0.3, sw * 0.76, port.height() * 0.4), 3, 3)
        # flecha hacia arriba, a la derecha de la USB, que se desliza
        ax = art.right() - art.width() * 0.10
        ay = art.top() + H * 0.30 - H * 0.08 * (0.0 if T.reduce_motion() else self.t)
        arrow = QPainterPath()
        u = H * 0.07
        arrow.moveTo(ax, ay - u)
        arrow.lineTo(ax + u, ay)
        arrow.lineTo(ax + u * 0.4, ay)
        arrow.lineTo(ax + u * 0.4, ay + 1.6 * u)
        arrow.lineTo(ax - u * 0.4, ay + 1.6 * u)
        arrow.lineTo(ax - u * 0.4, ay)
        arrow.lineTo(ax - u, ay)
        arrow.closeSubpath()
        p.setOpacity(0.5 + 0.5 * self.a)
        p.setBrush(QColor(T.ACCENT))
        p.drawPath(arrow)
        p.setOpacity(1.0)
        # textos: lo importante, enorme
        p.setPen(QColor(T.ACCENT))
        p.setFont(font(H * 0.19, QFont.Weight.Black if hasattr(QFont.Weight, "Black") else QFont.Weight.Bold))
        p.drawText(QRectF(text_x, r.top() + H * 0.10, tw, H * 0.30),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SACA LA USB")
        p.setPen(QColor(T.TEXT))
        p.setFont(font(H * 0.075, QFont.Weight.Bold))
        p.drawText(QRectF(text_x, r.top() + H * 0.42, tw, H * 0.14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Fotos copiadas: ya puedes retirarla")
        p.setPen(QColor(T.MUTED))
        p.setFont(font(H * 0.05))
        p.drawText(QRectF(text_x, r.top() + H * 0.58, tw, H * 0.30),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   f"{self.names} · este aviso sigue en pantalla hasta que la saques. RawFlow continúa con el evento.")


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

"""Widgets dibujados a mano para un look moderno y consistente en Mac y Windows."""
from __future__ import annotations

import math
from typing import List

from PySide6.QtCore import (QEasingCurve, QPointF, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QWidget

from . import theme as T
from .core import Drive, human_size


_FONTS: dict = {}       # (tamaño, peso, mono) → QFont ya resuelta
_FAMILY: dict = {}      # mono → familia elegida ("" = la del sistema); se mira una vez, no en cada pintado


def _load_fonts() -> None:
    """Windows: carga la Inter que viene DENTRO de RawFlow (rawflow/fonts, licencia OFL), para que la interfaz se
    vea igual en cualquier PC, tenga o no esa fuente instalada (sin ella todo salía en Segoe UI)."""
    import sys
    from pathlib import Path
    if sys.platform != "win32":
        return
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for d in (base / "rawflow" / "fonts", Path(__file__).resolve().with_name("fonts")):
        if d.is_dir():
            for p in sorted(d.glob("*.ttf")):
                QFontDatabase.addApplicationFont(str(p))
            return


def _family(mono: bool) -> str:
    if mono not in _FAMILY:
        import sys
        if not _FAMILY:
            _load_fonts()
        have = set(QFontDatabase.families())            # lento con muchas fuentes instaladas: solo una vez
        if not mono:
            pref = ["Inter 18pt", "Inter"] if sys.platform == "win32" else ["Inter"]   # la incluida tiene todos los pesos
        else:                                           # en Windows la fija del sistema es Courier New: mejor una moderna
            pref = ["Cascadia Mono", "Cascadia Code", "Consolas"] if sys.platform == "win32" else []
        _FAMILY[mono] = next((f for f in pref if f in have), "")
    return _FAMILY[mono]


def font(size: float, weight=QFont.Weight.Normal, mono=False, tnum=False) -> QFont:
    """`tnum`: cifras de igual ancho (tiempos, contadores): los números no «bailan» al cambiar y las columnas
    cuadran, con la misma letra que el resto (antes los tiempos iban en una fuente de terminal)."""
    key = (round(size), weight, bool(mono), bool(tnum))
    f = _FONTS.get(key)
    if f is None:
        kind = QFontDatabase.SystemFont.FixedFont if mono else QFontDatabase.SystemFont.GeneralFont
        f = QFontDatabase.systemFont(kind)  # SF en Mac, Segoe UI en Windows
        fam = _family(bool(mono))
        if fam:
            f.setFamily(fam)
        f.setPixelSize(max(1, key[0]))
        f.setWeight(weight)
        if tnum and not mono:
            try:
                f.setFeature(QFont.Tag("tnum"), 1)
            except Exception:  # noqa: BLE001  (Qt < 6.7: sin cifras tabulares, nada más)
                pass
        _FONTS[key] = f
    return QFont(f)                         # copia barata: quien la reciba puede cambiarla sin tocar la guardada


def tint_titlebar(w: QWidget, color: str = "") -> None:
    """Windows 11: pinta la barra de título del color de la ventana (en Windows 10 se queda la oscura normal)."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        c = QColor(color or T.BG)
        ref = ctypes.c_uint(c.red() | (c.green() << 8) | (c.blue() << 16))            # COLORREF = 0x00BBGGRR
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        hwnd = ctypes.c_void_p(int(w.winId()))
        dwm(hwnd, 35, ctypes.byref(ref), 4)                                            # DWMWA_CAPTION_COLOR
        dwm(hwnd, 34, ctypes.byref(ctypes.c_uint(QColor(T.BORDER).red() | (QColor(T.BORDER).green() << 8)
                                                 | (QColor(T.BORDER).blue() << 16))), 4)   # DWMWA_BORDER_COLOR
    except Exception:  # noqa: BLE001
        pass

def hide_from_capture(w: QWidget) -> None:
    """Windows: esta ventana de RawFlow no sale en las capturas de pantalla (sí la ves tú). Así RawFlow puede leer
    ImagenAI y Bridge, que trabajan DEBAJO de su pantalla de proceso (en Mac lo hace rfui al capturar)."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(w.winId())), 0x11)   # EXCLUDEFROMCAPTURE
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
class ElidedLabel(QLabel):
    """Etiqueta que recorta el texto con «…» si no cabe (por el medio: útil para rutas largas; o por el final) y
    enseña el texto entero al pasar el ratón. Nunca ensancha su fila."""

    def __init__(self, text: str = "", parent=None, mode=Qt.TextElideMode.ElideMiddle):
        super().__init__(parent)
        self._full, self._mode = text, mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def full(self) -> str:
        return self._full

    fade_changes = False        # True: al cambiar el texto aparece con un fundido corto (no salta de golpe)

    def setText(self, text: str) -> None:  # noqa: N802
        changed = text != self._full
        self._full = text
        self._update()
        if changed and self.fade_changes and text and self.isVisible() and not T.reduce_motion():
            # (4.26) con cambios seguidos («Analizando… 3 fotos», «… 4 fotos») cada uno volvía a empezar el fundido
            # desde 25 %: el texto parpadeaba. Ahora solo entra con fundido el primero de una racha; los demás se
            # cambian sin más (si el fundido aún va, sigue hasta el final).
            import time as _t
            now = _t.monotonic()
            quiet = now - getattr(self, "_last_change", 0.0) > 0.7
            self._last_change = now
            if quiet:
                from . import anim
                anim.fade_in(self, 200, start=0.25)

    def setFont(self, f) -> None:  # noqa: N802
        super().setFont(f)
        self._update()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._update()

    def _update(self):
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full, self._mode, max(40, self.width()))
        super().setText(shown)
        # el texto entero al pasar el ratón SOLO si está recortado: repetir lo que ya se lee no ayuda, y en los pasos
        # tapaba el aviso «Doble clic: activar o desactivar este paso»
        self.setToolTip(self._full if shown != self._full else "")


class StatusLabel(ElidedLabel):
    """La línea de estado: verde si algo terminó bien («✓ Listo…»), roja si se detuvo o falló, gris lo demás.
    Así el resultado se ve de un vistazo, sin leerlo."""

    BAD = ("⚠", "✗", "✘", "Se detuvo", "No se pudo")

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent, mode=Qt.TextElideMode.ElideRight)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        s = (text or "").lstrip()
        tone = "ok" if s.startswith(("✓", "✔")) else "bad" if s.startswith(self.BAD) else ""
        if tone != getattr(self, "_tone", None):
            self._tone = tone
            col = {"ok": T.OK, "bad": T.DANGER}.get(tone, T.MUTED)
            self.setStyleSheet(f"color:{col};" + (" font-weight:600;" if tone else ""))


class PathLabel(QWidget):
    """Ruta de la carpeta del evento: la carpeta que se crea, resaltada al final; lo de antes, atenuado. Si no cabe se
    recorta por el medio de la parte atenuada (el nombre del evento siempre se ve). Entera al pasar el ratón."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._f1, self._f2 = font(13), font(13, QFont.Weight.DemiBold)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(QFontMetrics(self._f2).height() + 4)

    def text(self) -> str:
        return self._text

    full = text

    def setText(self, text: str) -> None:  # noqa: N802
        if text != self._text:
            self._text = text
            self.setToolTip(text)
            self.update()

    def paintEvent(self, _):  # noqa: N802
        t = self._text
        cut = max(t.rfind("\\"), t.rfind("/"))
        base, name = (t[:cut + 1], t[cut + 1:]) if cut >= 0 else ("", t)
        fm1, fm2 = QFontMetrics(self._f1), QFontMetrics(self._f2)
        w, h = self.width(), self.height()
        if fm2.horizontalAdvance(name) >= w - 40:                 # ni el nombre cabe: solo él, recortado al final
            base, name = "", fm2.elidedText(name, Qt.TextElideMode.ElideRight, w)
        elif fm1.horizontalAdvance(base) > w - fm2.horizontalAdvance(name):
            base = fm1.elidedText(base, Qt.TextElideMode.ElideMiddle, w - fm2.horizontalAdvance(name))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bw = fm1.horizontalAdvance(base)
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        p.setFont(self._f1)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(0, 0, bw + 2, h), align, base)
        p.setFont(self._f2)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(bw, 0, max(0, w - bw), h), align, name)


class SwitchButton(QPushButton):
    """Interruptor (un estado, no una acción): pastilla con su palanca, lima si está encendido. Sigue siendo un
    QPushButton «checkable» (toggled, setChecked, barra espaciadora…); solo cambia cómo se ve."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFont(font(12, QFont.Weight.DemiBold))
        self._k, self._hov = 0.0, False
        self._a = QVariantAnimation(self, duration=180, easingCurve=QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(self._step)
        self.toggled.connect(self._animate)

    def _step(self, v) -> None:
        self._k = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        to = 1.0 if on else 0.0
        self._a.stop()
        if T.reduce_motion() or not self.isVisible():
            self._step(to)
            return
        self._a.setStartValue(self._k)
        self._a.setEndValue(to)
        self._a.start()

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(12 + 28 + 9 + QFontMetrics(self.font()).horizontalAdvance(self.text()) + 14, 28)

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, e):  # noqa: N802
        self._hov = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hov = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self._k
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        off_bg = QColor(T.SURFACE_2)
        off_bg.setAlpha(0)
        on_bg = QColor(T.ACCENT)
        on_bg.setAlpha(22)
        on_border = QColor(T.ACCENT)
        on_border.setAlpha(115)
        p.setPen(QPen(mix(QColor(T.BORDER_HI if self._hov else T.BORDER), on_border, k), 1))
        p.setBrush(mix(off_bg, on_bg, k))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        tw, th = 28.0, 16.0                                           # la palanca
        tr = QRectF(r.left() + 11, r.center().y() - th / 2, tw, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(QColor(T.BORDER_HI), QColor(T.ACCENT), k))
        p.drawRoundedRect(tr, th / 2, th / 2)
        d = th - 4
        p.setBrush(mix(QColor(T.MUTED if not self._hov else T.TEXT), QColor(T.ACCENT_INK), k))
        p.drawEllipse(QRectF(tr.left() + 2 + (tw - d - 4) * k, tr.top() + 2, d, d))
        p.setPen(mix(QColor(T.TEXT if self._hov else T.MUTED), QColor(T.ACCENT), k))
        p.setFont(self.font())
        p.drawText(QRectF(tr.right() + 9, r.top(), r.right() - tr.right() - 9, r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


# --------------------------------------------------------------------------
class SlimProgress(QWidget):
    """Barra de progreso fina con animación suave y modo indeterminado."""

    def __init__(self, parent=None, height=8):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._value = 0.0
        self._indeterminate = False
        self._phase = 0.0
        self._anim = QVariantAnimation(self, duration=350,
                                       easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_raw)
        self._timer = QTimer(self, interval=16, timeout=self._tick)

    def _set_raw(self, v):
        self._value = float(v)
        self.update()

    def setValue(self, v: float, animate=True):  # noqa: N802
        v = max(0.0, min(1.0, v))
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._value)
            self._anim.setEndValue(v)
            self._anim.start()
        else:
            self._set_raw(v)

    def setIndeterminate(self, on: bool):  # noqa: N802
        self._indeterminate = on
        self._timer.start() if on else self._timer.stop()
        self.update()

    def _tick(self):
        if not self.isVisible():          # ventana escondida durante el proceso: no gastar 60 ticks por segundo
            return
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(r, rad, rad)
        if self._indeterminate:
            w = r.width() * 0.3
            x = -w + (r.width() + w) * self._phase
            g = QLinearGradient(x, 0, x + w, 0)
            c = QColor(T.ACCENT)
            c0 = QColor(c)
            c0.setAlpha(0)
            g.setColorAt(0, c0)
            g.setColorAt(0.5, c)
            g.setColorAt(1, c0)
            path = QPainterPath()
            path.addRoundedRect(r, rad, rad)
            p.setClipPath(path)
            p.setBrush(g)
            p.drawRect(QRectF(x, 0, w, r.height()))
        elif self._value > 0:
            fill = QRectF(0, 0, max(r.height(), r.width() * self._value), r.height())
            g = QLinearGradient(0, 0, fill.width(), 0)
            g.setColorAt(0, QColor(T.ACCENT_DIM))
            g.setColorAt(1, QColor(T.ACCENT))
            p.setBrush(g)
            p.drawRoundedRect(fill, rad, rad)


_ICONS: dict = {}


def icon_pixmap_named(name: str, color: str, size: int = 16, dpr: float = 2.0):
    """Icono de la colección Phosphor (librería qtawesome), p. ej. «ph.folder-open», como imagen nítida del color
    pedido. None si la librería no está o ese icono no existe: quien lo pide sigue sin icono, sin romper nada."""
    key = (name, color, size, dpr)
    if key not in _ICONS:
        pm = None
        try:
            import os
            os.environ.setdefault("QT_API", "pyside6")
            import qtawesome as qta
            pm = qta.icon(name, color=QColor(color)).pixmap(int(size * dpr), int(size * dpr))
            pm.setDevicePixelRatio(dpr)
        except Exception:  # noqa: BLE001
            pm = None
        _ICONS[key] = pm
    return _ICONS[key]


def set_button_icon(btn, name: str, color: str, size: int = 15) -> None:
    pm = icon_pixmap_named(name, color, size)
    if pm is not None:
        from PySide6.QtCore import QSize
        btn.setIcon(QIcon(pm))
        btn.setIconSize(QSize(size, size))


class RippleButton(QPushButton):
    """Botón que, al pulsarlo, suelta una onda desde donde tocaste (se nota que «entró» el clic)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._pos = QPointF(0, 0)
        self._k = 1.0
        self._a = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=460,
                                    easingCurve=QEasingCurve.Type.OutCubic)
        self._a.valueChanged.connect(self._step)

    def _step(self, v) -> None:
        self._k = float(v)
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled() and not T.reduce_motion():
            self._pos = QPointF(e.position())
            self._a.stop()
            self._a.start()
        super().mousePressEvent(e)

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if self._a.state() != QVariantAnimation.State.Running:
            return
        # (4.26) la onda era un círculo de borde duro tan grande que se veía como una franja barriendo el botón y
        # tapaba el texto. Ahora es una onda suave (borde difuminado) que llega a la esquina más lejana y se apaga.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.setClipPath(clip)
        far = max(((self._pos.x() - x) ** 2 + (self._pos.y() - y) ** 2) ** 0.5
                  for x in (0, self.width()) for y in (0, self.height()))
        r = 12 + self._k * far
        g = QRadialGradient(self._pos, r)
        a = 0.22 * (1.0 - self._k) ** 1.4
        for stop, f in ((0.0, 0.55), (0.75, 1.0), (1.0, 0.0)):
            c = QColor("#FFFFFF")
            c.setAlphaF(a * f)
            g.setColorAt(stop, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(self._pos, r, r)


def mix(a: QColor, b: QColor, k: float) -> QColor:
    """Color entre `a` (k=0) y `b` (k=1), con su transparencia: para pasar de un estado a otro sin saltos."""
    k = 0.0 if k < 0 else 1.0 if k > 1 else k
    return QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                  round(a.blue() + (b.blue() - a.blue()) * k), round(a.alpha() + (b.alpha() - a.alpha()) * k))


class ActivityView(QWidget):
    """Lo último que hizo RawFlow, en renglones (lo más nuevo abajo; lo antiguo se desvanece hacia arriba). Cada
    renglón nuevo entra subiendo con un fundido. `add()` se puede llamar desde cualquier hilo."""

    MAX = 400
    IN_MS = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        import collections
        self._pending: "collections.deque" = collections.deque()
        self._lines: List[tuple] = []                 # (hora, texto, momento en que entró)
        self.setMinimumHeight(0)                      # lo único que cede si la ventana es baja (nunca pisa «Empezar»)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._poll = QTimer(self, interval=150, timeout=self._drain)
        self._poll.start()
        self._frames = QTimer(self, interval=16, timeout=self.update)     # solo mientras entra un renglón

    def add(self, text: str) -> None:
        import time as _t
        s = text.strip()
        s = s.lstrip("·").strip()
        if s and not s.startswith("==="):
            self._pending.append((_t.strftime("%H:%M:%S"), s))

    def _drain(self) -> None:
        if not self._pending:
            return
        import time as _t
        now = _t.monotonic()
        while self._pending:
            hh, s = self._pending.popleft()
            if self._lines and self._lines[-1][1] == s:           # el mismo aviso repetido: una sola vez
                continue
            self._lines.append((hh, s, now))
        del self._lines[:-self.MAX]
        if self.isVisible() and not T.reduce_motion():
            self._frames.start()
        self.update()

    def paintEvent(self, _):  # noqa: N802
        import time as _t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        p.fillRect(QRectF(0, 0, w, 1), QColor(T.BORDER))          # separa lo de ahora (arriba) del historial
        if h < 56:                                                 # ventana muy baja: sin sitio para renglones
            self._frames.stop()
            return
        p.setFont(font(11, QFont.Weight.DemiBold))
        p.setPen(QColor(T.MUTED))
        f = p.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.drawText(QPointF(0, 32), "ACTIVIDAD")
        top = 46
        if not self._lines:
            p.setFont(font(12.5))
            p.setPen(QColor(T.FAINT))
            p.drawText(QRectF(0, top, w, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "Aquí verás, paso a paso, lo que RawFlow va haciendo.")
            self._frames.stop()
            return
        now = _t.monotonic()
        tf, lf = font(11.5, tnum=True), font(12)
        fm = QFontMetrics(lf)
        row = 23
        n = max(1, int((h - top) // row))
        vis = self._lines[-n:]
        age = (now - vis[-1][2]) * 1000.0
        k = 1.0 if age >= self.IN_MS or T.reduce_motion() else 1 - (1 - age / self.IN_MS) ** 3    # salida suave
        if k >= 1.0:
            self._frames.stop()
        lleno = len(self._lines) > n                                 # ya no caben todos: la lista se desplaza
        shift = (1.0 - k) * row if lleno else 0.0                    # todo sube un renglón mientras entra el nuevo
        y0 = h - len(vis) * row if lleno else top                    # mientras caben, bajo el título y hacia abajo
        p.setClipRect(QRectF(0, top - 4, w, h - top + 4))
        for i, (hh, s, _t0) in enumerate(vis):
            y = y0 + i * row + shift
            depth = len(vis) - 1 - i                                 # 0 = el más nuevo
            a = max(0.22, 1.0 - depth * (0.78 / max(4, n)))
            if depth == 0:
                a *= k
            c = QColor(T.FAINT)
            c.setAlphaF(a * 0.9)
            p.setPen(c)
            p.setFont(tf)
            p.drawText(QRectF(0, y, 70, row), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hh)
            c = QColor(T.TEXT if depth == 0 else T.MUTED)
            if s.startswith(("✓", "✔")):
                c = QColor(T.OK)
            elif s.startswith(("⚠", "✗", "✘")):
                c = QColor(T.DANGER)
            c.setAlphaF(a)
            p.setPen(c)
            p.setFont(lf)
            p.drawText(QRectF(78, y, w - 78, row), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(s, Qt.TextElideMode.ElideRight, int(w - 82)))


# --------------------------------------------------------------------------
class DriveCard(QFrame):
    """Tarjeta de una memoria/USB. Clic para incluirla o excluirla."""

    toggled = Signal(str, bool)
    H = 116

    def __init__(self, drive: Drive, parent=None):
        super().__init__(parent)
        self.drive = drive
        self.selected = True
        self.state = "scanning"  # scanning | done | empty
        self.visited = 0
        self.found = 0
        self.found_bytes = 0
        self.formats: List[str] = []
        self.events = 0                          # eventos que trae (separados por horas sin fotos); 0 = sin saber
        self._hover = False
        self._pressed = False
        self._sel, self._hov = 1.0, 0.0          # 0..1: cuánto de «marcada» y de «ratón encima» (se animan)
        self._a_sel = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.Type.OutCubic)
        self._a_sel.valueChanged.connect(lambda v: self._blend("_sel", v))
        self._a_hov = QVariantAnimation(self, duration=150, easingCurve=QEasingCurve.Type.OutCubic)
        self._a_hov.valueChanged.connect(lambda v: self._blend("_hov", v))
        self._phase = 0.0
        import time as _time
        self._now = _time.monotonic
        self._born = self._now()                 # (26-nov-2026) «¡encontré una!»: ondas desde el icono al aparecer
        self._done_t = 0.0                       # …y al contar sus fotos: el número sube y el icono da un salto
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        if drive.fstype == "carpeta":
            self.setToolTip(drive.mountpoint)             # la ruta entera (en la tarjeta solo cabe dónde está)
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._timer.start()
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=380,
                                       easingCurve=QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(lambda _: self.update())
        self._fade.start()

    # --- estado ---------------------------------------------------------
    def set_scanning(self, visited: int, found: int):
        self.state = "scanning"
        self.visited, self.found = visited, found
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    RIPPLE, COUNT = 1.6, 0.9                     # s: ondas al aparecer; el número contando al terminar

    def set_done(self, found: int, found_bytes: int, formats: List[str]):
        new = self.state == "scanning" or found != self.found
        self.state = "done" if found else "empty"
        self.found, self.found_bytes, self.formats = found, found_bytes, formats
        if new and found and not T.reduce_motion():
            self._done_t = self._now()           # el número sube contando y el icono salta
            self._timer.start()
        elif not self._animating():
            self._timer.stop()
        self.update()

    def _animating(self) -> bool:
        if T.reduce_motion():
            return False
        now = self._now()
        return now - self._born < self.RIPPLE or (self._done_t and now - self._done_t < self.COUNT)

    def set_events(self, n: int):
        if n != self.events:
            self.events = n
            self.update()

    def _tick(self):
        self._phase = (self._phase + 0.01) % 1.0
        if self.state != "scanning" and not self._animating():
            self._timer.stop()                   # terminó todo: no se repinta más
        self.update()

    # --- interacción ----------------------------------------------------
    def _blend(self, attr: str, v) -> None:
        setattr(self, attr, float(v))
        self.update()

    def _go(self, a: QVariantAnimation, attr: str, to: float) -> None:
        """Lleva la mezcla a `to` con una transición corta (o de golpe con «Reducir movimiento»)."""
        if T.reduce_motion() or not self.isVisible():
            a.stop()
            self._blend(attr, to)
            return
        a.stop()
        a.setStartValue(float(getattr(self, attr)))
        a.setEndValue(float(to))
        a.start()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._pressed = False
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.selected = not self.selected
            self._go(self._a_sel, "_sel", 1.0 if self.selected else 0.0)
            self.toggled.emit(self.drive.key, self.selected)
        self.update()

    def enterEvent(self, _):  # noqa: N802
        self._hover = True
        self._go(self._a_hov, "_hov", 1.0)

    def leaveEvent(self, _):  # noqa: N802
        self._hover = False
        self._pressed = False
        self._go(self._a_hov, "_hov", 0.0)

    # --- dibujo ---------------------------------------------------------
    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        v = self._fade.currentValue()
        p.setOpacity(1.0 if v is None else float(v))
        # mientras entra o sale (su alto se anima) se dibuja ENTERA y se ve lo que cabe: con el alto del momento,
        # la barra y los textos de abajo se montaban sobre el nombre
        r = QRectF(0, 0, self.width(), max(self.height(), self.H)).adjusted(1, 1, -1, -1)

        sel, hov = self._sel, self._hov
        bg = mix(mix(QColor(T.SURFACE_2), QColor("#20232B"), hov), QColor("#1E2318"), sel)
        if self._pressed:
            bg = bg.darker(112)                   # se «hunde» un poco mientras se pulsa
        acc = QColor(T.ACCENT)
        acc.setAlpha(170)
        border = mix(mix(QColor(T.BORDER), QColor(T.BORDER_HI), hov), acc, sel)
        now = self._now()
        rip = 1.0 if T.reduce_motion() else min(1.0, (now - self._born) / self.RIPPLE)      # 0→1 al aparecer
        cnt = 1.0 if not self._done_t else min(1.0, (now - self._done_t) / self.COUNT)     # 0→1 al contar
        glow = (1.0 - rip) ** 2                   # el borde se enciende al llegar y se apaga
        flash = (1.0 - cnt) ** 2 if self._done_t else 0.0
        if glow > 0.01 or flash > 0.01:
            halo = QColor(T.OK if flash > glow else T.ACCENT)
            halo.setAlphaF(min(1.0, 0.9 * max(glow, flash)))
            border = mix(border, halo, max(glow, flash))
        p.setPen(QPen(border, 1.3 + 1.7 * max(glow, flash)))
        p.setBrush(bg)
        p.drawRoundedRect(r, 14, 14)

        # icono: tarjeta SD, o carpeta si se arrastró a RawFlow
        ix, iy = r.left() + 18, r.top() + 18
        icon = QRectF(ix, iy, 40, 40)
        if rip < 1.0:                             # ondas tipo sonar desde el icono: «¡encontré una!»
            p.save()
            p.setClipRect(r)
            for k0 in (0.0, 0.28):
                t = (rip - k0) / (1.0 - k0) if rip > k0 else 0.0
                if 0.0 < t < 1.0:
                    e = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(t)
                    rad = 20 + 70 * e
                    ring = QColor(T.ACCENT)
                    ring.setAlphaF(0.75 * (1.0 - t))
                    p.setPen(QPen(ring, 2.0 + 2.0 * (1.0 - t)))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(icon.center(), rad, rad)
            p.restore()
        pop = 1.0
        if self._done_t and cnt < 1.0:            # el icono da un saltito al terminar de contar
            pop = 1.0 + 0.18 * math.sin(math.pi * min(1.0, cnt / 0.45))
        p.save()
        if pop != 1.0:
            c0 = icon.center()
            p.translate(c0)
            p.scale(pop, pop)
            p.translate(-c0)
        p.setPen(Qt.PenStyle.NoPen)
        chip = mix(QColor(T.BORDER_HI), QColor(T.ACCENT), sel)
        p.setBrush(chip)
        p.drawRoundedRect(icon, 10, 10)
        ink = mix(QColor(T.MUTED), QColor(T.ACCENT_INK), sel)
        if self.drive.fstype in ("carpeta", "sueltas"):
            fx, fy = ix + 9, iy + 12
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(fx, fy, 10, 5), 1.5, 1.5)          # pestaña
            p.drawRoundedRect(QRectF(fx, fy + 3, 22, 15), 2.5, 2.5)     # carpeta
        else:
            sd = QPainterPath()
            sx, sy, sw, sh = ix + 12, iy + 9, 16, 22
            sd.moveTo(sx + 5, sy)
            sd.lineTo(sx + sw, sy)
            sd.lineTo(sx + sw, sy + sh)
            sd.lineTo(sx, sy + sh)
            sd.lineTo(sx, sy + 5)
            sd.closeSubpath()
            p.setBrush(ink)
            p.drawPath(sd)
            p.setBrush(chip)
            for k in range(3):
                p.drawRect(QRectF(sx + 5 + k * 3.6, sy + 2.5, 1.6, 5))
        p.restore()

        # textos (el nombre deja sitio al check; la línea de debajo ya no lo tiene encima y usa todo el ancho)
        tx = ix + 56
        tw = r.width() - (tx - r.left()) - 52
        p.setPen(QColor(T.TEXT))
        p.setFont(font(14, QFont.Weight.DemiBold))
        fm = QFontMetrics(p.font())
        p.drawText(QPointF(tx, iy + 16), fm.elidedText(self.drive.label, Qt.TextElideMode.ElideRight, int(tw)))
        p.setFont(font(11.5))
        p.setPen(QColor(T.MUTED))
        if self.drive.fstype == "carpeta":                  # dónde está (la carpeta de arriba), en Mac y en Windows
            import os
            up = os.path.basename(os.path.dirname(self.drive.mountpoint.rstrip("\\/")))
            sub = f"Carpeta · en {up}" if up else "Carpeta arrastrada"
        elif self.drive.fstype == "sueltas":
            sub = "Fotos arrastradas"
        else:
            kind = "Memoria" if self.drive.removable else "Disco externo"
            sub = f"{kind} · {human_size(self.drive.total)} · {self.drive.fstype or '—'}"
        fm = QFontMetrics(p.font())
        p.drawText(QPointF(tx, iy + 36), fm.elidedText(sub, Qt.TextElideMode.ElideRight, int(r.right() - 18 - tx)))

        # check
        cr = QRectF(r.right() - 38, r.top() + 18, 22, 22)
        base_op = p.opacity()
        if sel < 1.0:                             # el aro vacío se va mientras entra el check
            p.setOpacity(base_op * (1.0 - sel))
            p.setPen(QPen(QColor(T.BORDER_HI), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cr)
        if sel > 0.0:                             # el check entra creciendo desde el centro
            p.setOpacity(base_op * sel)
            k = 0.6 + 0.4 * sel
            c = cr.center()
            p.save()
            p.translate(c)
            p.scale(k, k)
            p.translate(-c)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(cr)
            pen = QPen(QColor(T.ACCENT_INK), 2.2, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(cr.left() + 6, cr.center().y()))
            path.lineTo(cr.left() + 9.5, cr.center().y() + 3.5)
            path.lineTo(cr.right() - 5.5, cr.top() + 7.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.restore()
        p.setOpacity(base_op)

        # barra / estado
        by = r.bottom() - 32
        bar = QRectF(r.left() + 18, by, r.width() - 36, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BG))
        if self.state == "scanning" or self.drive.total:   # carpeta arrastrada: no tiene «capacidad», sin barra vacía
            p.drawRoundedRect(bar, 2, 2)
        if self.state == "scanning":
            w = bar.width() * 0.28
            x = bar.left() - w + (bar.width() + w) * self._phase
            g = QLinearGradient(x, 0, x + w, 0)
            c = QColor(T.ACCENT)
            c0 = QColor(c)
            c0.setAlpha(0)
            g.setColorAt(0, c0)
            g.setColorAt(.5, c)
            g.setColorAt(1, c0)
            clip = QPainterPath()
            clip.addRoundedRect(bar, 2, 2)
            p.save()
            p.setClipPath(clip)
            p.setBrush(g)
            p.drawRect(QRectF(x, bar.top(), w, bar.height()))
            p.restore()
        elif self.drive.total:
            frac = min(1.0, self.drive.used / self.drive.total)
            p.setBrush(mix(QColor(T.FAINT), QColor(T.ACCENT), sel))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4, bar.width() * frac), 4), 2, 2)

        p.setFont(font(11.5, QFont.Weight.Medium))
        ty = r.bottom() - 10
        if self.state == "scanning":
            p.setPen(QColor(T.MUTED))
            p.drawText(QPointF(bar.left(), ty),
                       f"Analizando…  {self.visited:,} archivos  ·  {self.found:,} RAW")
        elif self.state == "empty":
            p.setPen(QColor(T.FAINT))
            p.drawText(QPointF(bar.left(), ty), "Sin archivos RAW")
        else:
            col = mix(QColor(T.MUTED), QColor(T.ACCENT), sel)
            if flash > 0.01:
                col = mix(col, QColor(T.OK), flash)
            p.setPen(col)
            shown = self.found if cnt >= 1.0 else int(round(
                self.found * QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(cnt)))
            txt = f"{shown:,} RAW  ·  {human_size(self.found_bytes)}"
            if self.events:
                txt += f"  ·  {self.events} evento{'s' if self.events != 1 else ''}"
            p.drawText(QPointF(bar.left(), ty), txt)
            fm = QFontMetrics(p.font())
            p.setPen(QColor(T.MUTED))
            fmt = "  ".join(self.formats[:4])
            p.drawText(QPointF(bar.right() - fm.horizontalAdvance(fmt), ty), fmt)


# --------------------------------------------------------------------------
class EmptyDrives(QWidget):
    """Estado vacío: invita a conectar una memoria."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._phase = 0.0
        self._t = QTimer(self, interval=30, timeout=self._tick)
        self._t.start()

    def _tick(self):
        w = self.window()                       # oculta, al fondo o durante un evento: no se anima (CPU y calor)
        if not self.isVisible() or not (w and w.isActiveWindow()):
            return
        self._phase = (self._phase + 0.015) % 1.0
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pen = QPen(QColor(T.BORDER_HI), 1.3, Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 14, 14)
        c = r.center()
        # pulso
        import math
        for k in range(2):
            ph = (self._phase + k * 0.5) % 1.0
            col = QColor(T.ACCENT)
            col.setAlphaF(0.35 * (1 - ph))
            p.setPen(QPen(col, 1.5))
            rad = 18 + 26 * ph
            p.drawEllipse(QPointF(c.x(), c.y() - 22), rad, rad)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT))
        p.drawEllipse(QPointF(c.x(), c.y() - 22), 6 + math.sin(self._phase * 6.283) * 0.8, 6)
        tr = QRectF(r.left() + 18, c.y() + 18, r.width() - 36, 60)        # el texto se ajusta al ancho: nunca se corta
        wrap = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        p.setPen(QColor(T.TEXT))
        p.setFont(font(14, QFont.Weight.DemiBold))
        used = p.boundingRect(tr, wrap, "Conecta una memoria\no arrastra aquí una carpeta")
        p.drawText(tr, wrap, "Conecta una memoria\no arrastra aquí una carpeta")
        p.setPen(QColor(T.MUTED))
        p.setFont(font(12))
        p.drawText(QRectF(tr.left(), used.bottom() + 8, tr.width(), 40), wrap,
                   "También fotos sueltas o un evento de RawFlow")


class DropOverlay(QWidget):
    """Mientras arrastras algo encima de RawFlow: «Suelta para añadir», con un marco lima a rayas que se mueve.
    No recibe clics ni lo que se suelta (eso lo hace la ventana): solo se ve, y aparece y se va con un fundido."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAcceptDrops(False)
        self._k, self._target, self._dash = 0.0, 0.0, 0.0
        self._t = QTimer(self, interval=16, timeout=self._tick)
        self.hide()

    def show_over(self, on: bool):
        self._target = 1.0 if on else 0.0
        if T.reduce_motion():                     # «Reducir movimiento» de macOS: sin fundido, al instante
            self._k = self._target
        if on:
            self.setGeometry(self.parentWidget().rect())
            self.raise_()
            self.show()
        self._t.start()

    def _tick(self):
        # entrar: rápido y frenando (decelera); salir: aún más rápido
        step = 0.16 if self._target > self._k else 0.22
        self._k += (self._target - self._k) * step if abs(self._target - self._k) > 0.01 else (self._target - self._k)
        self._dash = (self._dash + 0.6) % 16
        if self._k <= 0.001 and self._target == 0.0:
            self._t.stop()
            self.hide()
        self.update()

    def paintEvent(self, _):  # noqa: N802
        k = self._k
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(T.BG)
        bg.setAlphaF(0.95 * k)
        p.fillRect(self.rect(), bg)
        r = QRectF(self.rect()).adjusted(22, 22, -22, -22)
        r.adjust((1 - k) * 18, (1 - k) * 18, -(1 - k) * 18, -(1 - k) * 18)   # crece un poco al aparecer
        acc = QColor(T.ACCENT)
        acc.setAlphaF(0.9 * k)
        pen = QPen(acc, 2.2, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([6, 5])
        pen.setDashOffset(self._dash)
        p.setPen(pen)
        fill = QColor(T.ACCENT)
        fill.setAlphaF(0.05 * k)
        p.setBrush(fill)
        p.drawRoundedRect(r, 22, 22)
        c = r.center()
        # icono: carpeta con flecha hacia abajo
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(acc)
        p.drawRoundedRect(QRectF(c.x() - 34, c.y() - 74, 28, 12), 4, 4)
        p.drawRoundedRect(QRectF(c.x() - 34, c.y() - 66, 68, 48), 8, 8)
        ink = QColor(T.ACCENT_INK)
        p.setPen(QPen(ink, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(c.x(), c.y() - 56), QPointF(c.x(), c.y() - 30))
        path = QPainterPath(QPointF(c.x() - 10, c.y() - 40))
        path.lineTo(c.x(), c.y() - 30)
        path.lineTo(c.x() + 10, c.y() - 40)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        txt = QColor(T.TEXT)
        txt.setAlphaF(k)
        p.setPen(txt)
        p.setFont(font(24, QFont.Weight.Bold))
        p.drawText(QRectF(r.left(), c.y() + 2, r.width(), 36), Qt.AlignmentFlag.AlignHCenter, "Suelta para añadir")
        mut = QColor(T.MUTED)
        mut.setAlphaF(k)
        p.setPen(mut)
        p.setFont(font(14))
        p.drawText(QRectF(r.left() + 30, c.y() + 42, r.width() - 60, 50),
                   Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                   "Carpetas de fotos (con sus subcarpetas), fotos sueltas o un evento de RawFlow")


# --------------------------------------------------------------------------
def icon_pixmap(size: int = 256) -> QPixmap:
    """El icono de RawFlow dibujado nítido a «size» píxeles (vectorial: no se reescala un mapa de bits)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 256, size / 256)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(T.BG))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 56, 56)
    p.setBrush(QColor(T.ACCENT))
    path = QPainterPath()
    path.moveTo(98, 56)
    path.lineTo(180, 56)
    path.lineTo(180, 200)
    path.lineTo(76, 200)
    path.lineTo(76, 78)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor(T.BG))
    for k in range(4):
        p.drawRoundedRect(QRectF(104 + k * 18, 72, 9, 30), 3, 3)
    p.setPen(QPen(QColor(T.BG), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(128, 128, 128, 176)
    p.drawLine(108, 156, 128, 176)
    p.drawLine(148, 156, 128, 176)
    p.end()
    return pm


def app_icon() -> QIcon:
    ico = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ico.addPixmap(icon_pixmap(s))
    return ico

"""Pantalla de proceso de RawFlow: ocupa TODA la pantalla mientras trabaja (por encima de la barra de menús
y del Dock), tapa DxO, Imagen, Bridge y Retouch4me y enseña en grande qué paso va, cuánto falta y qué hace.

No recibe clics ni el foco (RawFlow maneja las apps de debajo). El ratón y el teclado ya NO se bloquean (23-nov-2026);
inputlock.py solo escucha «Esc tres veces» para cancelar. Todo se dibuja a mano a ~60 fps con animaciones suaves.
"""
from __future__ import annotations

import math
import time
from typing import Callable, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QWidget

from . import theme as T
from .overlays import LEVEL_MAIN, set_level
from .widgets import font


FRAME_MS = 33                         # 30 fps con animación
ECO_MS = 1000                         # 1 fps en ahorro (mientras DxO, Retouch4me… usan la tarjeta)


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _col(hex_: str, a: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class RunScreen(QWidget):
    def __init__(self, steps: List[tuple]):
        flags = (Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)       # pintamos todo: Qt no borra antes
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.steps = steps
        n = len(steps)
        self.state = ["wait"] * n
        self.changed = [0.0] * n          # cuándo cambió cada paso (para animar)
        self.times = [""] * n
        self.ev_name, self.subtitle = "", ""
        self.status, self.detail, self.old_detail, self.detail_t = "", "", "", 0.0
        self.elapsed, self.left, self.end = "", "", ""
        self.overall, self.shown = 0.0, 0.0
        self.step_frac = -1.0
        self.esc = False                  # True: se está escuchando «Esc ×3» (se enseña cómo cancelar)
        self.eco = False
        self.power = ""                   # aviso de batería («Conecta el cargador…»), vacío si va con cargador
        self._closing = False
        self.t0 = time.monotonic()
        self.fin: Optional[tuple] = None   # (ok, título, texto, momento)
        self.on_done: Optional[Callable] = None
        self._bg, self._bg_t, self._op, self._fonts = None, -1.0, -1.0, {}
        self._base, self._base_key = None, None
        self.timer = QTimer(self, interval=FRAME_MS)    # 30 fps (4.24; antes 15: el anillo y las ondas daban saltitos)
        self._boost = 0.0                 # hasta cuándo va fluida aunque esté en ahorro (al cambiar de paso)
        self._last_paint = 0.0
        self.timer.timeout.connect(self._frame)
        self._full_t = 0.0

    def set_eco(self, on: bool):
        """Modo ahorro: mientras Retouch4me/DxO/CineSlide usan la tarjeta gráfica al 100 %, la pantalla de RawFlow
        se queda quieta y solo se actualiza 1 vez por segundo (no les quita GPU ni calienta el Mac).
        Con «Reducir movimiento» de macOS activado, siempre quieta."""
        on = on or T.reduce_motion()
        if on == self.eco:
            return
        self.eco = on
        self.timer.setInterval(ECO_MS if on else FRAME_MS)
        self.update()

    def _fade(self, now: float):
        """Fundido de entrada/salida. Se cambia la opacidad AQUÍ (fuera del pintado): cambiarla dentro de
        paintEvent hace que macOS rehaga la ventana a mitad de pintar y Qt se cierra."""
        op = _ease((now - self.t0) / 0.45)
        if self.fin and len(self.fin) == 5:
            f = (now - self.fin[4]) / 0.45
            if f >= 1 and not self._closing:
                self._closing = True
                QTimer.singleShot(0, self._close_now)
            op = min(op, 1 - _ease(f))
        if abs(op - self._op) > 0.004:
            self._op = op
            self.setWindowOpacity(op)

    def _frame(self):
        """Cada fotograma repinta solo lo que se mueve; la pantalla entera, 1 vez por segundo o en fundidos."""
        now = time.monotonic()
        self._fade(now)
        fading = now - self.t0 < 0.5 or (self.fin is not None)
        if self.eco:                      # en ahorro, 1 fps; pero el cambio de paso (✓ que se dibuja, tramo que avanza)
            want = FRAME_MS if (fading or now < self._boost) else ECO_MS     # se ve fluido ~1 s y vuelve a ahorro
            if self.timer.interval() != want:
                self.timer.setInterval(want)
        if self.eco and not fading:
            self.update()
            return
        # pantalla entera cada fotograma (la base va en caché). Repintar solo regiones parciales hacía que
        # macOS cerrara RawFlow al alternar ahorro/normal (QBackingStore::flush con una región a medias).
        self.update()

    # ------------------------------------------------------------- control
    def open(self, event: str, subtitle: str, start_at: int = 0):
        self.ev_name, self.subtitle = event, subtitle
        self.state = ["done" if i < start_at else "wait" for i in range(len(self.steps))]
        self.changed = [0.0] * len(self.steps)
        self.times = [""] * len(self.steps)
        self.status = self.detail = self.old_detail = ""
        self.overall = self.shown = 0.0
        self.fin, self.on_done = None, None
        self.step_frac, self._base = -1.0, None
        self.eco = T.reduce_motion()                     # «Reducir movimiento» de macOS: sin animaciones
        self._closing, self._op = False, -1.0
        self.timer.setInterval(ECO_MS if self.eco else FRAME_MS)
        self.t0 = time.monotonic()
        scr = getattr(self, "target", None) or QGuiApplication.primaryScreen()     # la pantalla de la ventana de RawFlow
        self.setGeometry(scr.geometry())
        self.setWindowOpacity(0.0)
        self.show()
        set_level(self, LEVEL_MAIN)
        self.timer.start()

    def set_step(self, i: int, state: str):
        if 0 <= i < len(self.state) and self.state[i] != state:
            self.state[i] = state
            self.changed[i] = time.monotonic()
            if self.eco and self.isVisible():
                self._boost = self.changed[i] + 1.0
                self.timer.setInterval(FRAME_MS)
            if state == "run":
                self.step_frac = -1.0

    @Slot(str)
    def set_status(self, t: str):
        self.set_texts(status=t)

    @Slot(str)
    def set_detail(self, t: str):
        self.set_texts(detail=t)

    @Slot(float)
    def set_step_frac(self, f: float):
        self.step_frac = f

    def set_power(self, txt: str):
        if txt != self.power:
            self.power = txt
            self.update()

    def set_texts(self, status: str = None, detail: str = None):
        if status is not None:
            self.status = status
        if detail is not None and detail != self.detail:
            self.old_detail, self.detail, self.detail_t = self.detail, detail, time.monotonic()

    def set_times(self, times: List[str], elapsed: str, left: str, end: str, overall: float):
        self.times, self.elapsed, self.left, self.end = times, elapsed, left, end
        self.overall = max(self.overall, min(1.0, overall)) if overall >= 0 else self.overall

    def finish(self, ok: bool, title: str, text: str, done: Callable, hold: float = 2.6):
        self.set_eco(False)
        self.fin = (ok, title, text, time.monotonic())
        self.on_done = done
        if ok:
            self.overall = 1.0
        QTimer.singleShot(int(hold * 1000), self._fade_out)

    def _fade_out(self):
        self.fin = (*self.fin[:3], self.fin[3], time.monotonic()) if self.fin else None

    def _close_now(self):
        self.timer.stop()
        self.hide()
        cb, self.on_done = self.on_done, None
        if cb:
            cb()

    # ------------------------------------------------------------- dibujo
    def paintEvent(self, e):  # noqa: N802
        now = time.monotonic()
        t = now - self.t0
        dt = min(1.0, now - self._last_paint) if self._last_paint else 0.066
        self._last_paint = now
        self.shown += (self.overall - self.shown) * (1 - math.exp(-2.5 * dt))   # igual de suave a 30 fps que a 1 fps

        W, H = self.width(), self.height()
        # capa base (todo lo que no se mueve a cada fotograma): se rehace solo si algo cambió
        animating = (self.fin is not None or t < 0.6 or now - self.detail_t < 0.45
                     or any(c and now - c < 0.8 for c in self.changed) or abs(self.overall - self.shown) > 0.002)
        key = (W, H, tuple(self.state), tuple(self.times), self.left, self.elapsed, self.end, self.status,
               self.detail, self.ev_name, self.subtitle, self.esc, round(self.shown, 3), int(t),
               round(self.step_frac, 2), self.power)
        if animating or self._base is None or key != self._base_key:
            self._base, self._base_key = self._render_base(W, H, t, now), key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._base)
        # lo que se mueve: punto del logo, cometa del anillo y ondas del paso actual
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pulse = 0.6 if self.eco else 0.55 + 0.45 * math.sin(t * 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_col(T.ACCENT, 0.18 * pulse))
        p.drawEllipse(QPointF(46, 50), 13, 13)
        p.setBrush(QColor(T.ACCENT))
        p.drawEllipse(QPointF(46, 50), 7, 7)
        cx, cy, R = W / 2, H * 0.43, min(W, H) * 0.165
        if not self.fin and not self.eco:
            self._comet(p, cx, cy, R, t)
            self._pulses(p, W, H, t)
            self._ring_head(p, cx, cy, R, t)
        elif self.fin and self.fin[0]:
            self._confetti(p, W, H, cx, cy, now - self.fin[3])
        if not self.fin:
            self._live_panel(p, W, H, cx, cy, R)
        self._checks_panel(p, W, H, cx, cy, R, now)

    def _render_base(self, W, H, t, now) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = self._base                   # se reutiliza: a pantalla completa (3440×1440 al 150 %) son ~45 MB, y crearla
        if pm is None or pm.width() != int(W * dpr) or pm.height() != int(H * dpr) or pm.devicePixelRatio() != dpr:
            pm = QPixmap(int(W * dpr), int(H * dpr))      # de nuevo en cada fotograma gastaba memoria y CPU
            pm.setDevicePixelRatio(dpr)
        if self._bg is None or (t - self._bg_t > 1.0 and not self.eco) or self._bg.deviceIndependentSize().width() < W - 1:
            self._bg, self._bg_t = self._render_bg(W, H, t), t
        p = QPainter(pm)
        p.drawPixmap(0, 0, self._bg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setPen(QColor(T.TEXT))
        p.setFont(self._f(24, QFont.Weight.ExtraBold, spacing=4))
        p.drawText(QRectF(68, 32, 400, 36), Qt.AlignmentFlag.AlignVCenter, "RAWFLOW")
        if self.power and not self.fin:
            self._power_banner(p, W)

        # evento
        p.setPen(QColor(T.TEXT))
        p.setFont(self._f(min(52, H * 0.058), QFont.Weight.Bold))
        p.drawText(QRectF(0, H * 0.085, W, 70), Qt.AlignmentFlag.AlignCenter,      # un nombre muy largo no se sale
                   p.fontMetrics().elidedText(self.ev_name, Qt.TextElideMode.ElideRight, int(W * 0.86)))
        p.setPen(QColor(T.MUTED))
        p.setFont(self._f(16))
        p.drawText(QRectF(0, H * 0.085 + 62, W, 26), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(self.subtitle, Qt.TextElideMode.ElideMiddle, int(W * 0.7)))

        cx, cy, R = W / 2, H * 0.43, min(W, H) * 0.165
        self._ring(p, cx, cy, R, t)
        self._center_text(p, cx, cy, R)
        self._steps(p, W, H, t, now)
        self._detail(p, W, H, now)

        if self.esc and not self.fin:                      # cómo cancelar, siempre legible
            self._esc_hint(p, W, H)
        if self.fin:
            self._final(p, cx, cy, R, now)
        p.end()
        return pm

    def _f(self, size, weight=QFont.Weight.Normal, mono=False, spacing=0.0, tnum=False) -> QFont:
        k = (round(size), weight, mono, spacing, tnum)
        f = self._fonts.get(k)
        if f is None:
            f = font(size, weight, mono, tnum=tnum)
            if spacing:
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
            self._fonts[k] = f
        return f

    def _render_bg(self, W, H, t) -> QPixmap:
        """Fondo con brillos que se mueven despacio: se pinta pequeño (1/8) y se escala suavizado."""
        w, h = max(8, W // 8), max(8, H // 8)
        pm = QPixmap(w, h)
        pm.fill(QColor(T.BG))
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._glow(q, w, h, t)
        q.end()
        dpr = self.devicePixelRatioF()
        big = pm.scaled(int(W * dpr), int(H * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)     # una vez; luego se copia tal cual
        big.setDevicePixelRatio(dpr)
        return big

    def _glow(self, p: QPainter, W, H, t):
        for k, (ax, ay, sp, a) in enumerate(((0.28, 0.32, 0.21, 0.16), (0.74, 0.62, 0.17, 0.11),
                                                (0.52, 0.95, 0.13, 0.08))):
            x = W * (ax + 0.06 * math.sin(t * sp + k * 2.1))
            y = H * (ay + 0.05 * math.cos(t * sp * 1.3 + k))
            r = max(W, H) * 0.42
            g = QRadialGradient(QPointF(x, y), r)
            g.setColorAt(0, _col(T.ACCENT if k != 1 else T.OK, a * 0.32))
            g.setColorAt(1, _col(T.BG, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(x, y), r, r)

    def _esc_hint(self, p: QPainter, W, H):
        """«Para cancelar, pulsa [esc] [esc] [esc]» con teclas dibujadas: se entiende de un vistazo."""
        f, fk = self._f(14, QFont.Weight.DemiBold), self._f(12, QFont.Weight.Bold)
        pre, post = "Para cancelar, pulsa", "y luego reintenta desde este paso"
        fm, fmk = QFontMetrics(f), QFontMetrics(fk)
        kw, gap = fmk.horizontalAdvance("esc") + 18, 6
        total = fm.horizontalAdvance(pre) + 14 + 3 * kw + 2 * gap + 14 + fm.horizontalAdvance(post)
        x, y = (W - total) / 2, H - 50
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(x, y, fm.horizontalAdvance(pre) + 2, 26), Qt.AlignmentFlag.AlignVCenter, pre)
        x += fm.horizontalAdvance(pre) + 14
        for _ in range(3):
            r = QRectF(x, y + 1, kw, 24)
            p.setPen(QPen(QColor(T.BORDER_HI), 1))
            p.setBrush(QColor(T.SURFACE_2))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(T.TEXT))
            p.setFont(fk)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "esc")
            x += kw + gap
        x += 14 - gap
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(x, y, fm.horizontalAdvance(post) + 2, 26), Qt.AlignmentFlag.AlignVCenter, post)

    def _power_banner(self, p: QPainter, W):
        """Sin cargador: aviso rojo arriba, en el centro (macOS dormirá el Mac al quedarse sin batería)."""
        f = self._f(14, QFont.Weight.Bold)
        w = min(W - 80, QFontMetrics(f).horizontalAdvance(self.power) + 64)
        r = QRectF((W - w) / 2, 30, w, 40)
        p.setPen(QPen(_col(T.DANGER, 0.9), 1.5))
        p.setBrush(_col(T.DANGER, 0.16))
        p.drawRoundedRect(r, 20, 20)
        x, y = r.left() + 24, r.center().y()                    # rayo
        bolt = QPainterPath()
        for k, (dx, dy) in enumerate(((2, -9), (-4, 1), (0, 1), (-2, 9), (4, -1), (0, -1))):
            (bolt.moveTo if k == 0 else bolt.lineTo)(x + dx, y + dy)
        bolt.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.DANGER))
        p.drawPath(bolt)
        p.setPen(QColor(T.TEXT))
        p.setFont(f)
        p.drawText(QRectF(r.left() + 42, r.top(), r.width() - 54, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.power, Qt.TextElideMode.ElideRight, int(r.width() - 54)))

    def _live_panel(self, p: QPainter, W, H, cx, cy, R):
        """EN MARCHA: qué programa trabaja y a qué ritmo (subida en MB/s…), y cuánto se usa del Mac."""
        from . import live
        from .live import NAMES
        x0 = cx + R + 56
        w = min(540.0, W - 36 - x0)
        if w < 300:
            return
        lanes = live.lanes()[:4]
        m = live.metrics()
        if not lanes and not m:
            return
        dec = lambda v, d=1: f"{v:.{d}f}".replace(".", ",")     # noqa: E731
        lines = []                                               # (texto, color, fuente)
        for k, txt in lanes:
            lines.append(("● " + NAMES.get(k, k), T.TEXT, self._f(14, QFont.Weight.DemiBold)))
            lines.append(("    " + txt, T.MUTED, self._f(13)))
        if m:
            if lines:
                lines.append(("", T.FAINT, self._f(6)))
            gpu = m.get("gpu")                                   # None: no se puede leer (Windows): no «GPU — %»
            lines.append((f"CPU {m.get('cpu', 0):.0f} %    " + (f"GPU {gpu} %    " if gpu is not None else "")
                          + f"Memoria {dec(m.get('mem_used', 0))} de {m.get('mem_total', 0):.0f} GB",
                          T.TEXT, self._f(13, QFont.Weight.DemiBold)))
            lines.append((f"Red  ↑ {dec(m.get('up', 0))} MB/s    ↓ {dec(m.get('down', 0))} MB/s",
                          T.MUTED, self._f(13)))
            ncpu = max(1, m.get("ncpu", 1))
            progs = [f"{k} {v / ncpu:.0f} %" for k, v in m.get("procs", [])[:3] if v / ncpu >= 1]
            if progs:
                import sys
                lines.append((("Usan el Mac:  " if sys.platform == "darwin" else "Usan el equipo:  ")
                              + "  ·  ".join(progs), T.MUTED, self._f(13)))
        heights = [max(8.0, QFontMetrics(fn).height() + 4) for _, _, fn in lines]
        h = 44 + sum(heights) + 14
        r = QRectF(x0, cy - h / 2, w, h)
        p.setPen(QPen(_col(T.BORDER_HI, 0.8), 1))
        p.setBrush(_col(T.SURFACE, 0.78))
        p.drawRoundedRect(r, 18, 18)
        p.setPen(QColor(T.FAINT))
        p.setFont(self._f(11.5, QFont.Weight.Bold, spacing=2))
        p.drawText(QRectF(r.left() + 20, r.top() + 14, w - 40, 18), Qt.AlignmentFlag.AlignLeft, "EN MARCHA")
        y = r.top() + 40
        for (txt, col, fn), lh in zip(lines, heights):
            if txt:
                p.setFont(fn)
                x0 = r.left() + 20
                if txt.startswith("● "):                       # programa trabajando: punto lima (no blanco)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(T.ACCENT))
                    p.drawEllipse(QPointF(x0 + 4, y + lh / 2), 4, 4)
                    txt, x0 = txt[2:], x0 + 16
                is_gpu_full = txt.startswith("CPU") and m.get("gpu") is not None and m.get("gpu", 0) >= 90
                p.setPen(QColor(T.ACCENT if is_gpu_full else col))
                p.drawText(QRectF(x0, y, r.right() - 20 - x0, lh), Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(fn).elidedText(txt, Qt.TextElideMode.ElideRight, int(r.right() - 20 - x0)))
            y += lh

    def _checks_panel(self, p: QPainter, W, H, cx, cy, R, now):
        """COMPROBADO: lo que se verificó de verdad al acabar cada paso, en su carpeta («597 de 597 con Imagen ·
        comprobadas una a una»), el que va en marcha, los pendientes y los desactivados. Al lado izquierdo del anillo
        (simétrico a EN MARCHA); también en la pantalla final, como resumen de todo lo comprobado."""
        from . import live
        x1 = cx - R - 56
        w = min(540.0, x1 - 36)
        if w < 300:
            return
        checks = live.checks()
        final = len(self.steps)                          # la revisión final va después del último paso
        rows = list(range(len(self.steps))) + ([final] if final in checks else [])
        f_t, f_s = self._f(14, QFont.Weight.DemiBold), self._f(13)
        lh = QFontMetrics(f_t).height() + QFontMetrics(f_s).height() + 12
        h = 44 + lh * len(rows) + 8
        r = QRectF(x1 - w, cy - h / 2, w, h)
        p.setPen(QPen(_col(T.BORDER_HI, 0.8), 1))
        p.setBrush(_col(T.SURFACE, 0.78))
        p.drawRoundedRect(r, 18, 18)
        p.setPen(QColor(T.FAINT))
        p.setFont(self._f(11.5, QFont.Weight.Bold, spacing=2))
        p.drawText(QRectF(r.left() + 20, r.top() + 14, w - 40, 18), Qt.AlignmentFlag.AlignLeft, "COMPROBADO")
        y = r.top() + 40
        for i in rows:
            st, txt, ts = checks.get(i, ("", "", 0.0))
            title = "Revisión final" if i == final else f"{i + 1}  {self.steps[i][0]}"
            run = i < len(self.state) and self.state[i] == "run" and not st
            if not st:
                st = "run" if run else ("off" if i < len(self.state) and self.state[i] == "off" else "wait")
                txt = {"run": "en marcha…", "off": "desactivado", "wait": "pendiente"}[st]
            f = _ease((now - ts) / 0.45) if ts else 1.0           # la marca se dibuja y la línea aparece (≤ 0,5 s)
            ic = QPointF(r.left() + 30, y + lh / 2)
            col = {"ok": T.OK, "warn": T.DANGER, "run": T.ACCENT, "off": T.FAINT, "wait": T.BORDER_HI}[st]
            if st in ("ok", "warn"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_col(col, 0.25 + 0.75 * f))
                p.drawEllipse(ic, 10, 10)
                if st == "ok":
                    self._check(p, ic.x(), ic.y(), 5.5, f, T.ACCENT_INK, 2.2)
                else:
                    p.setPen(QColor(T.ACCENT_INK))
                    p.setFont(self._f(12, QFont.Weight.Black))
                    p.drawText(QRectF(ic.x() - 10, ic.y() - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, "!")
            elif st == "run":
                p.setPen(QPen(_col(T.ACCENT, 0.25), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(ic, 9, 9)
                spin = 0 if self.eco else (now * 300) % 360
                p.setPen(QPen(QColor(T.ACCENT), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(QRectF(ic.x() - 9, ic.y() - 9, 18, 18), int(-spin * 16), 100 * 16)
            else:
                p.setPen(QPen(QColor(col), 1.6, Qt.PenStyle.DashLine if st == "off" else Qt.PenStyle.SolidLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(ic, 9, 9)
            tx = r.left() + 52
            tw = int(r.right() - 20 - tx)
            p.setFont(f_t)
            p.setPen(_col(T.TEXT if st in ("ok", "warn", "run") else T.MUTED, 0.35 + 0.65 * f))
            p.drawText(QRectF(tx, y + 4, tw, QFontMetrics(f_t).height()), Qt.AlignmentFlag.AlignVCenter, title)
            p.setFont(f_s)
            p.setPen(_col({"ok": T.OK, "warn": T.DANGER, "run": T.ACCENT}.get(st, T.FAINT), 0.35 + 0.65 * f))
            p.drawText(QRectF(tx, y + 4 + QFontMetrics(f_t).height(), tw, QFontMetrics(f_s).height() + 2),
                       Qt.AlignmentFlag.AlignVCenter, QFontMetrics(f_s).elidedText(txt, Qt.TextElideMode.ElideRight, tw))
            y += lh

    def _ring(self, p: QPainter, cx, cy, R, t):
        ok = self.fin and self.fin[0]
        bad = self.fin and not self.fin[0]
        col = T.OK if ok else (T.DANGER if bad else T.ACCENT)
        rect = QRectF(cx - R, cy - R, 2 * R, 2 * R)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(T.BORDER), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(rect)
        # halo suave
        for k in range(3):
            p.setPen(QPen(_col(col, 0.05 - k * 0.012), 22 + k * 12))
            p.drawArc(rect, 90 * 16, -int(360 * 16 * max(0.002, self.shown)))
        p.setPen(QPen(QColor(col), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, -int(360 * 16 * max(0.002, self.shown)))

    def _ring_head(self, p: QPainter, cx, cy, R, t):
        """Punto de luz que late en la punta del anillo de progreso."""
        if self.shown < 0.004:
            return
        a = 2 * math.pi * self.shown
        x, y = cx + R * math.sin(a), cy - R * math.cos(a)
        k = 0.5 + 0.5 * math.sin(t * 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_col(T.ACCENT, 0.18 + 0.14 * k))
        p.drawEllipse(QPointF(x, y), 13 + 5 * k, 13 + 5 * k)
        p.setBrush(QColor("#F6FFD6"))
        p.drawEllipse(QPointF(x, y), 4.5, 4.5)

    def _confetti(self, p: QPainter, W, H, cx, cy, age):
        """Confeti con los colores de RawFlow al terminar bien (2,5 s)."""
        if age > 2.6:
            return
        import random
        rnd = random.Random(7)
        cols = (T.ACCENT, T.OK, "#FFFFFF", "#9DE1FF", "#FFC56B")
        for _ in range(90):
            ang = rnd.uniform(0, 2 * math.pi)
            sp = rnd.uniform(220, 620)
            x = cx + math.cos(ang) * sp * age
            y = cy + math.sin(ang) * sp * age * 0.8 + 380 * age * age      # gravedad
            a = max(0.0, 1 - age / 2.6)
            p.save()
            p.translate(x, y)
            p.rotate(rnd.uniform(0, 360) + age * rnd.uniform(-400, 400))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_col(rnd.choice(cols), a))
            w = rnd.uniform(5, 10)
            p.drawRect(QRectF(-w / 2, -w / 4, w, w / 2))
            p.restore()

    def _comet(self, p: QPainter, cx, cy, R, t):
        """Cometa que gira alrededor del anillo: hay trabajo en marcha."""
        if not self.fin:
            a = (t * 0.9) % (2 * math.pi)
            for k in range(14):
                aa = a - k * 0.045
                x, y = cx + R * math.sin(aa), cy - R * math.cos(aa)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_col(T.TEXT, 0.55 * (1 - k / 14)))
                p.drawEllipse(QPointF(x, y), 4.2 * (1 - k / 20), 4.2 * (1 - k / 20))

    def _center_text(self, p: QPainter, cx, cy, R):
        if self.fin:
            return
        p.setPen(QColor(T.ACCENT))
        txt = self.left or "…"
        size = R * 0.30                                   # «faltan 7 h 51 min» tiene que caber dentro del anillo
        while size > 14 and QFontMetrics(self._f(size, QFont.Weight.Bold)).horizontalAdvance(txt) > 1.62 * R:
            size *= 0.92
        p.setFont(self._f(size, QFont.Weight.Bold))
        p.drawText(QRectF(cx - R, cy - R * 0.42, 2 * R, R * 0.42), Qt.AlignmentFlag.AlignHCenter
                   | Qt.AlignmentFlag.AlignBottom, txt)
        cur = next((i for i, s in enumerate(self.state) if s == "run"), -1)
        p.setPen(QColor(T.TEXT))
        p.setFont(self._f(max(15, R * 0.095), QFont.Weight.DemiBold))
        name = f"Paso {cur + 1} de {len(self.steps)} · {self.steps[cur][0]}" if cur >= 0 else "Preparando…"
        age = time.monotonic() - self.changed[cur] if cur >= 0 and self.changed[cur] else 9.0
        f = _ease(age / 0.5)
        p.setPen(_col(T.TEXT, f))
        p.drawText(QRectF(cx - R, cy + R * 0.04 + 12 * (1 - f), 2 * R, R * 0.2), Qt.AlignmentFlag.AlignCenter, name)
        p.setPen(QColor(T.MUTED))
        p.setFont(self._f(max(12, R * 0.07)))
        sub = "  ·  ".join(x for x in (f"llevas {self.elapsed}" if self.elapsed else "", self.end) if x)
        p.drawText(QRectF(cx - R, cy + R * 0.25, 2 * R, R * 0.18), Qt.AlignmentFlag.AlignCenter, sub)

    def _step_xy(self, W, H):
        n = len(self.steps)
        x0, x1 = W * 0.16, W * 0.84
        return [x0 + (x1 - x0) * i / (n - 1) for i in range(n)], H * 0.745

    def _pulses(self, p: QPainter, W, H, t):
        """Ondas que salen del paso en marcha."""
        xs, y = self._step_xy(W, H)
        for i, st in enumerate(self.state):
            if st != "run":
                continue
            for k in range(2):
                ph = ((t * 0.8) + k * 0.5) % 1.0
                p.setPen(QPen(_col(T.ACCENT, 0.45 * (1 - ph)), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(xs[i], y), 23 + ph * 22, 23 + ph * 22)

    def _steps(self, p: QPainter, W, H, t, now):
        n = len(self.steps)
        y = H * 0.745
        x0, x1 = W * 0.16, W * 0.84
        xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
        r = 19
        # línea base y tramo completado (animado)
        p.setPen(QPen(QColor(T.BORDER), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        for i in range(n - 1):
            if self.state[i] in ("done", "skip", "off"):
                f = _ease((now - self.changed[i]) / 0.7) if self.changed[i] else 1.0
                p.setPen(QPen(QColor(T.OK), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(xs[i], y), QPointF(xs[i] + (xs[i + 1] - xs[i]) * f, y))
        for i, (title, sub) in enumerate(self.steps):
            st, x = self.state[i], xs[i]
            age = now - self.changed[i] if self.changed[i] else 9.0
            pop = 1 + 0.25 * math.sin(min(1.0, age / 0.35) * math.pi) if age < 0.35 else 1.0
            rr = r * pop
            if st == "run":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T.ACCENT))
            elif st in ("done", "skip"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T.OK))
            elif st == "error":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T.DANGER))
            elif st == "off":                                   # desactivado con doble clic: gris, a rayas
                p.setPen(QPen(QColor(T.BORDER_HI), 2, Qt.PenStyle.DashLine))
                p.setBrush(QColor(T.SURFACE))
            else:
                p.setPen(QPen(QColor(T.BORDER_HI), 2))
                p.setBrush(QColor(T.SURFACE))
            p.drawEllipse(QPointF(x, y), rr, rr)
            if st == "run" and 0 < self.step_frac <= 1:          # progreso del paso en marcha
                p.setPen(QPen(QColor(T.ACCENT), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(x - rr - 7, y - rr - 7, 2 * rr + 14, 2 * rr + 14), 90 * 16,
                          -int(360 * 16 * self.step_frac))
            # contenido del círculo
            if st in ("done", "skip"):
                self._check(p, x, y, rr * 0.55, _ease(age / 0.5), T.ACCENT_INK, 3)
            else:
                p.setPen(QColor(T.ACCENT_INK if st == "run" else (T.TEXT if st == "error" else T.MUTED)))
                p.setFont(self._f(15, QFont.Weight.Bold))
                p.drawText(QRectF(x - rr, y - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter,
                           "!" if st == "error" else ("–" if st == "off" else str(i + 1)))
            # textos
            p.setPen(QColor(T.TEXT if st not in ("wait", "off") else T.MUTED))
            p.setFont(self._f(16, QFont.Weight.Bold))
            p.drawText(QRectF(x - 110, y + 30, 220, 24), Qt.AlignmentFlag.AlignCenter, title)
            p.setPen(QColor({"run": T.ACCENT, "done": T.OK, "skip": T.OK, "error": T.DANGER}.get(st, T.FAINT)))
            p.setFont(self._f(13, QFont.Weight.Medium, tnum=True))     # como en la ventana (antes, letra de terminal)
            p.drawText(QRectF(x - 110, y + 54, 220, 20), Qt.AlignmentFlag.AlignCenter,
                       "Desactivado" if st == "off" and not (i < len(self.times) and self.times[i])
                       else (self.times[i] if i < len(self.times) and self.times[i] else sub))

    def _check(self, p: QPainter, x, y, s, f, color, w):
        path = QPainterPath()
        pts = [QPointF(x - s * 0.9, y + 0.05 * s), QPointF(x - s * 0.25, y + s * 0.65), QPointF(x + s, y - s * 0.6)]
        seg1 = math.dist((pts[0].x(), pts[0].y()), (pts[1].x(), pts[1].y()))
        seg2 = math.dist((pts[1].x(), pts[1].y()), (pts[2].x(), pts[2].y()))
        L = (seg1 + seg2) * f
        path.moveTo(pts[0])
        if L <= seg1:
            k = L / seg1
            path.lineTo(pts[0] + (pts[1] - pts[0]) * k)
        else:
            path.lineTo(pts[1])
            k = (L - seg1) / seg2
            path.lineTo(pts[1] + (pts[2] - pts[1]) * k)
        p.setPen(QPen(QColor(color), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _detail(self, p: QPainter, W, H, now):
        if self.fin:
            return
        y = H * 0.875
        p.setPen(QColor(T.TEXT))
        p.setFont(self._f(17, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        p.drawText(QRectF(0, y, W, 26), Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(self.status, Qt.TextElideMode.ElideRight, int(W * 0.8)))
        f = _ease((now - self.detail_t) / 0.35)
        p.setFont(self._f(14))
        fm = p.fontMetrics()
        for txt, a, dy in ((self.old_detail, 1 - f, -10 * f), (self.detail, f, 10 * (1 - f))):
            if txt and a > 0.01:
                p.setPen(_col(T.MUTED, a))
                p.drawText(QRectF(0, y + 30 + dy, W, 22), Qt.AlignmentFlag.AlignCenter,
                           fm.elidedText(txt, Qt.TextElideMode.ElideMiddle, int(W * 0.8)))

    def _final(self, p: QPainter, cx, cy, R, now):
        ok, title, text, t0 = self.fin[:4]
        f = _ease((now - t0) / 0.6)
        col = T.OK if ok else T.DANGER
        rr = R * 0.62 * (0.6 + 0.4 * f)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_col(col, 0.16 * f))
        p.drawEllipse(QPointF(cx, cy), rr * 1.35, rr * 1.35)
        p.setBrush(_col(col, f))
        p.drawEllipse(QPointF(cx, cy), rr, rr)
        if ok:
            self._check(p, cx, cy - rr * 0.05, rr * 0.5, _ease((now - t0 - 0.25) / 0.5), T.ACCENT_INK, 9)
        else:
            p.setPen(_col(T.TEXT, f))
            p.setFont(self._f(rr, QFont.Weight.Bold))
            p.drawText(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter, "!")
        H = self.height()
        p.setPen(_col(T.TEXT, f))
        p.setFont(self._f(30, QFont.Weight.Bold))
        wmax = int(self.width() * 0.9)                         # en una pantalla estrecha el resumen no se corta a lo bruto
        p.drawText(QRectF(0, H * 0.875 - 8, self.width(), 40), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, wmax))
        p.setPen(_col(T.MUTED, f))
        p.setFont(self._f(15))
        p.drawText(QRectF(0, H * 0.875 + 34, self.width(), 24), Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, wmax))

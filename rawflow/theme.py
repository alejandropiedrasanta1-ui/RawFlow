"""Tema visual: grafito oscuro con acento lima (estilo pantalla de cámara)."""

BG = "#0D0E11"
SURFACE = "#15171C"
SURFACE_2 = "#1C1F26"
BORDER = "#262A33"
BORDER_HI = "#353A46"
TEXT = "#F1F2F4"
MUTED = "#8B909B"
FAINT = "#7F8490"          # (4.10) antes #5A5F6A: 2,8:1 sobre los paneles; ahora 4,8:1 (WCAG AA)
ACCENT = "#D7FF4F"
ACCENT_DIM = "#9DBA3A"
ACCENT_INK = "#11140A"
DANGER = "#FF6B6B"
OK = "#5BE49B"

FONT_FAMILIES = ["Inter", "SF Pro Display", ".AppleSystemUIFont",
                 "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue", "Arial"]
MONO_FAMILIES = ["JetBrains Mono", "SF Mono", "Menlo", "Cascadia Mono", "Consolas", "monospace"]

QSS = f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, #root {{ background: {BG}; }}

#panel {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
#sectionTitle {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; }}
#wordmark {{ font-size: 22px; font-weight: 800; letter-spacing: 3px; }}
#tagline {{ color: {MUTED}; font-size: 13px; }}
#muted {{ color: {MUTED}; }}
#faint {{ color: {FAINT}; font-size: 12px; }}
#bigNumber {{ font-size: 34px; font-weight: 700; }}
#bigLabel {{ color: {MUTED}; font-size: 12px; }}
#pathLabel {{ color: {TEXT}; font-size: 13px; }}

#chip {{
    background: {SURFACE_2}; border: 1px solid {BORDER};
    border-radius: 11px; padding: 3px 10px; color: {MUTED}; font-size: 12px;
}}
#chipOk {{
    background: rgba(91,228,155,0.10); border: 1px solid rgba(91,228,155,0.35);
    border-radius: 11px; padding: 3px 10px; color: {OK}; font-size: 12px;
}}
#chipWarn {{
    background: rgba(255,107,107,0.10); border: 1px solid rgba(255,107,107,0.35);
    border-radius: 11px; padding: 3px 10px; color: {DANGER}; font-size: 12px;
}}
#formatChip {{
    background: rgba(215,255,79,0.08); border: 1px solid rgba(215,255,79,0.30);
    border-radius: 9px; padding: 2px 8px; color: {ACCENT}; font-size: 11px; font-weight: 600;
}}

QLineEdit, QDateEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 10px 12px; font-size: 15px; font-weight: 400; selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}
QLineEdit:focus, QDateEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit::placeholder {{ color: {FAINT}; }}
QDateEdit::drop-down {{ border: none; width: 26px; }}
QDateEdit::down-arrow {{ image: none; width: 0; }}

QCalendarWidget QWidget {{ background: {SURFACE_2}; color: {TEXT}; }}
QCalendarWidget QToolButton {{ background: transparent; color: {TEXT}; font-weight: 600; padding: 6px; }}
QCalendarWidget QAbstractItemView:enabled {{
    background: {SURFACE}; selection-background-color: {ACCENT}; selection-color: {ACCENT_INK};
}}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 8px 14px; color: {TEXT}; font-weight: 500;
}}
QPushButton:hover {{ border-color: {BORDER_HI}; background: #222631; }}
QPushButton:pressed {{ background: #1A1D24; }}
QPushButton:disabled {{ color: {FAINT}; }}

#primary {{
    background: {ACCENT}; color: {ACCENT_INK}; border: none; border-radius: 14px;
    padding: 16px 22px; font-size: 16px; font-weight: 700;
}}
#primary:hover {{ background: #E3FF7A; }}
#primary:pressed {{ background: {ACCENT_DIM}; }}
#primary:disabled {{ background: #2A2E36; color: {FAINT}; }}

#ghost {{ background: transparent; border: none; color: {MUTED}; padding: 4px 6px; }}
#ghost:hover {{ color: {TEXT}; }}
#danger {{ background: transparent; border: 1px solid rgba(255,107,107,0.4); color: {DANGER}; }}

QCheckBox {{ color: {MUTED}; spacing: 8px; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 6px;
    border: 1px solid {BORDER_HI}; background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; font-size: 14px; }}
QMessageBox QPushButton {{ min-width: 76px; }}

QToolTip {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_HI};
    border-radius: 6px; padding: 6px;
}}
"""


_REDUCE_MOTION: list = []


def reduce_motion() -> bool:
    """¿El Mac tiene «Reducir movimiento» (Accesibilidad)? Entonces nada de animaciones decorativas: se respeta."""
    if not _REDUCE_MOTION:
        v = False
        try:
            import subprocess
            import sys
            if sys.platform == "darwin":
                out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                     capture_output=True, text=True, timeout=3).stdout.strip()
                v = out == "1"
        except Exception:  # noqa: BLE001
            pass
        _REDUCE_MOTION.append(v)
    return _REDUCE_MOTION[0]

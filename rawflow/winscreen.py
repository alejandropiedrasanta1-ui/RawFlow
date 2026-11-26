"""Ver y tocar la pantalla en Windows (lo que en Mac hace tools/rfui): leer el texto que hay a la vista con el OCR
de Windows, hacer clic, escribir y mandar atajos.

Las apps que maneja RawFlow (ImagenAI, Adobe Bridge) no dejan leer sus botones por accesibilidad (medido el
23-nov-2026: la ventana de ImagenAI solo expone un panel vacío, incluso con --force-renderer-accessibility), así que
se leen como en el Mac: captura de pantalla + OCR, y los botones se encuentran por su rótulo, no por coordenadas.

Todo va en píxeles reales del escritorio virtual (varias pantallas, también con coordenadas negativas). Los clics y
teclas se mandan con SendInput: llevan la marca «inyectado», así que no cuentan para el «Esc ×3» que escucha
rawflow/winlock.py (el ratón y el teclado físicos ya no se bloquean).
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND      # (sin esto, un identificador de ventana alto salía negativo)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_DPI = [False]
_ENGINE: list = []
_LOCK = threading.Lock()


def dpi_aware() -> None:
    """Píxeles reales (sin el escalado de Windows). En RawFlow ya lo pone Qt; fuera de Qt (pruebas) se pone aquí."""
    if _DPI[0]:
        return
    _DPI[0] = True
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))      # PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001
        pass


def virtual_rect() -> Tuple[int, int, int, int]:
    """(x, y, ancho, alto) de todas las pantallas juntas."""
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


# ------------------------------------------------------------------ captura
class _BMI(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def capture(rect: Optional[Tuple[int, int, int, int]] = None, scale: int = 1) -> Tuple[int, int, int, int, bytes]:
    """Captura (x, y, ancho, alto) del escritorio → (x, y, ancho, alto, BGRA de arriba abajo). Con `scale` > 1 la
    imagen sale ampliada (ancho y alto × scale): la letra pequeña de algunas apps se lee mucho mejor así."""
    dpi_aware()
    x, y, sw, sh = rect or virtual_rect()
    w, h = sw * scale, sh * scale
    for f, res, args in ((user32.GetDC, wintypes.HDC, [wintypes.HWND]),
                         (gdi32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC]),
                         (gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_int, ctypes.c_int]),
                         (gdi32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ])):
        f.restype, f.argtypes = res, args
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if scale == 1:
            gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
        else:
            gdi32.SetStretchBltMode(mdc, 4)                                    # HALFTONE: ampliación suavizada
            gdi32.StretchBlt(mdc, 0, 0, w, h, sdc, x, y, sw, sh, 0x00CC0020 | 0x40000000)
        bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)        # alto negativo: de arriba abajo
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        return x, y, w, h, buf.raw
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)


def save_png(path: str, shot) -> None:
    """Guarda una captura como PNG (solo para diagnóstico; sin dependencias)."""
    import struct
    import zlib
    _x, _y, w, h, bgra = shot
    rows = bytearray()
    mv = memoryview(bgra)
    for r in range(h):
        row = bytearray(mv[r * w * 4:(r + 1) * w * 4])
        row[0::4], row[2::4] = row[2::4], row[0::4]                 # BGRA → RGBA
        row[3::4] = b"\xff" * w
        rows += b"\x00" + row

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(rows), 3)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ OCR
def _engine():
    if not _ENGINE:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        eng = None
        for tag in ("es-MX", "es-ES", "es"):                        # las apps están en español
            try:
                lang = Language(tag)
                if OcrEngine.is_language_supported(lang):
                    eng = OcrEngine.try_create_from_language(lang)
                    if eng:
                        break
            except Exception:  # noqa: BLE001
                continue
        eng = eng or OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            raise RuntimeError("Windows no tiene ningún idioma de OCR instalado (Configuración → Hora e idioma → Idioma).")
        _ENGINE.append(eng)
    return _ENGINE[0]


def _recognize(w: int, h: int, bgra: bytes):
    import asyncio
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    dw = DataWriter()
    dw.write_bytes(bgra)
    bmp = SoftwareBitmap.create_copy_with_alpha_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h,
                                                            BitmapAlphaMode.IGNORE)

    async def go():
        return await _engine().recognize_async(bmp)
    return asyncio.run(go())


TILE_W, TILE_H, TILE_OVER = 1500, 820, 140


def _crop(w: int, bgra: bytes, x: int, y: int, cw: int, ch: int) -> bytes:
    mv = memoryview(bgra)
    return b"".join(mv[(y + r) * w * 4 + x * 4:(y + r) * w * 4 + (x + cw) * 4] for r in range(ch))


def _lines(res, x0: float, y0: float) -> List[dict]:
    """Las palabras de un mismo renglón se juntan solo si están pegadas: dos botones en la misma fila
    («Cancelar» … «Guardar») salen por separado."""
    out: List[dict] = []
    for line in res.lines:
        grupo: list = []

        def cerrar():
            if grupo:
                xs = min(g[1] for g in grupo)
                ys = min(g[2] for g in grupo)
                xe = max(g[1] + g[3] for g in grupo)
                ye = max(g[2] + g[4] for g in grupo)
                out.append({"t": " ".join(g[0] for g in grupo), "x": x0 + xs, "y": y0 + ys, "w": xe - xs, "h": ye - ys})
                grupo.clear()
        for wd in line.words:
            r = wd.bounding_rect
            if grupo:
                px, _py, pw, ph = grupo[-1][1:]
                if r.x - (px + pw) > max(ph, r.height) * 1.1:       # hueco grande: es otro rótulo
                    cerrar()
            grupo.append((wd.text, r.x, r.y, r.width, r.height))
        cerrar()
    return out


def ocr(rect: Optional[Tuple[int, int, int, int]] = None, shot=None, scale: int = 1) -> List[dict]:
    """Texto a la vista → [{"t", "x", "y", "w", "h"}] en píxeles del escritorio.
    Una ventana grande se lee por trozos solapados: el OCR de Windows se deja rótulos (medido: en una ventana de
    3462×1462 no veía «Atrás» ni «Siguiente» del pie del diálogo; leyendo esa franca aparte, sí)."""
    if scale > 1 and shot is None:                     # leer ampliado y devolver las cajas en píxeles reales
        x0, y0, w, h, bgra = capture(rect, scale)
        out = ocr(shot=(0, 0, w, h, bgra))
        return [{"t": b["t"], "x": x0 + b["x"] / scale, "y": y0 + b["y"] / scale, "w": b["w"] / scale,
                 "h": b["h"] / scale} for b in out]
    with _LOCK:
        x0, y0, w, h, bgra = shot or capture(rect)
        if w <= TILE_W * 1.15 and h <= TILE_H * 1.15:
            return _lines(_recognize(w, h, bgra), x0, y0)
        nx = max(1, -(-(w - TILE_OVER) // (TILE_W - TILE_OVER)))
        ny = max(1, -(-(h - TILE_OVER) // (TILE_H - TILE_OVER)))
        tw, th = -(-(w + (nx - 1) * TILE_OVER) // nx), -(-(h + (ny - 1) * TILE_OVER) // ny)
        found: List[dict] = []
        for j in range(ny):
            for i in range(nx):
                tx = min(max(0, i * (tw - TILE_OVER)), w - tw)
                ty = min(max(0, j * (th - TILE_OVER)), h - th)
                found += _lines(_recognize(tw, th, _crop(w, bgra, tx, ty, tw, th)), x0 + tx, y0 + ty)
    # en los solapes un rótulo sale dos veces (o cortado en uno de los trozos): se queda el más largo
    found.sort(key=lambda b: -len(b["t"]))
    out: List[dict] = []
    for b in found:
        dup = False
        for o in out:
            ix = min(b["x"] + b["w"], o["x"] + o["w"]) - max(b["x"], o["x"])
            iy = min(b["y"] + b["h"], o["y"] + o["h"]) - max(b["y"], o["y"])
            if ix > 0 and iy > 0 and ix * iy > 0.5 * b["w"] * b["h"]:
                dup = True
                break
        if not dup:
            out.append(b)
    out.sort(key=lambda b: (b["y"], b["x"]))
    return out

# ------------------------------------------------------------------ ratón y teclado
class _MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _U(ctypes.Union):
    _fields_ = [("mi", _MI), ("ki", _KI)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs: _INPUT) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _mouse(flags: int, x: int = 0, y: int = 0, data: int = 0) -> _INPUT:
    return _INPUT(0, _U(mi=_MI(x, y, data & 0xFFFFFFFF, flags, 0, None)))


def move(x: float, y: float) -> None:
    dpi_aware()
    vx, vy, vw, vh = virtual_rect()
    ax = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((y - vy) * 65535 / max(1, vh - 1)))
    _send(_mouse(0x0001 | 0x8000 | 0x4000, ax, ay))                 # MOVE | ABSOLUTE | VIRTUALDESK


AVOID: List[int] = []     # ventanas de RawFlow que reciben clics (botón «Parar»): se apartan si un clic cae encima


def _out_of_the_way(x: float, y: float) -> List[int]:
    """Esconde un momento las ventanas de AVOID que tapan (x, y): el clic de RawFlow va a la app de debajo, no al
    botón «Parar» (revisión 26-nov-2026: se lo tragaba, y dos seguidos en 4 s paraban el evento)."""
    hidden = []
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    for hwnd in list(AVOID):
        try:
            r = wintypes.RECT()
            if user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(r)) \
                    and r.left <= x < r.right and r.top <= y < r.bottom:
                user32.ShowWindow(hwnd, 0)                          # SW_HIDE
                hidden.append(hwnd)
        except Exception:  # noqa: BLE001
            continue
    if hidden:
        time.sleep(0.05)
    return hidden


def click(x: float, y: float, n: int = 1, right: bool = False) -> None:
    hidden = _out_of_the_way(x, y)
    try:
        move(x, y)
        time.sleep(0.06)
        down, up = (0x0008, 0x0010) if right else (0x0002, 0x0004)
        for i in range(max(1, n)):
            _send(_mouse(down))
            time.sleep(0.03)
            _send(_mouse(up))
            if i < n - 1:
                time.sleep(0.08)
    finally:
        if hidden:
            time.sleep(0.15)
            for hwnd in hidden:
                user32.ShowWindow(hwnd, 4)                          # SW_SHOWNOACTIVATE: vuelve sin coger el foco


def scroll(x: float, y: float, clicks: int) -> None:
    move(x, y)
    time.sleep(0.05)
    _send(_mouse(0x0800, data=clicks * 120))


VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "home": 0x24,
      "end": 0x23, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "pgup": 0x21, "pgdn": 0x22,
      "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "f2": 0x71, "f4": 0x73, "f5": 0x74}
_EXT = {0x2E, 0x24, 0x23, 0x26, 0x28, 0x25, 0x27, 0x21, 0x22, 0x5B}


def _vk(k: str) -> int:
    k = k.lower()
    if k in VK:
        return VK[k]
    if len(k) == 1:
        return user32.VkKeyScanW(ord(k)) & 0xFF
    raise ValueError(f"tecla desconocida: {k}")


def _key(vk: int, up: bool = False) -> _INPUT:
    return _INPUT(1, _U(ki=_KI(vk, 0, (0x0002 if up else 0) | (0x0001 if vk in _EXT else 0), 0, None)))


def keys(combo: str) -> None:
    """Atajo tipo «ctrl+a», «ctrl+shift+e», «enter», «alt+f4»."""
    parts = [_vk(p.strip()) for p in combo.split("+") if p.strip()]
    for vk in parts:
        _send(_key(vk))
        time.sleep(0.02)
    for vk in reversed(parts):
        _send(_key(vk, True))
        time.sleep(0.02)


def type_text(text: str, delay: float = 0.004) -> None:
    """Escribe el texto tal cual (Unicode), sin depender de la distribución del teclado."""
    for ch in text:
        if ch == "\n":
            keys("enter")
            continue
        unit = ch.encode("utf-16-le")                               # 2 bytes, o 4 si va fuera del plano básico
        for i in range(0, len(unit), 2):
            code = int.from_bytes(unit[i:i + 2], "little")
            _send(_INPUT(1, _U(ki=_KI(0, code, 0x0004, 0, None))),          # KEYEVENTF_UNICODE
                  _INPUT(1, _U(ki=_KI(0, code, 0x0004 | 0x0002, 0, None))))
        time.sleep(delay)


# ------------------------------------------------------------------ ventanas
def windows(title_part: str = "", exe: str = "") -> List[dict]:
    """Ventanas visibles de primer nivel cuyo título contiene `title_part` y/o cuyo programa es `exe` (p. ej.
    «ImagenAI.exe»): [{"hwnd", "title", "pid", "exe", "rect": (x, y, w, h)}], la de delante primero."""
    dpi_aware()
    found: List[dict] = []
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def exe_of(pid: int) -> str:
        h = kernel32.OpenProcess(0x1000, False, pid)                # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value.rsplit("\\", 1)[-1]
            return ""
        finally:
            kernel32.CloseHandle(h)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if not title or (title_part and title_part.lower() not in title.lower()):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = exe_of(pid.value)
        if exe and name.lower() != exe.lower():
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append({"hwnd": hwnd, "title": title, "pid": pid.value, "exe": name,
                      "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                      "min": bool(user32.IsIconic(hwnd))})
        return True
    user32.EnumWindows(each, 0)                                     # en orden Z: la de delante primero
    return found


def minimize_quiet(exe_prefixes: Tuple[str, ...]) -> int:
    """Minimiza SIN activarlas (no roba el foco ni el ratón) las ventanas visibles de los programas cuyo .exe empieza
    por alguno de `exe_prefixes` (en minúsculas). Para lo que trabaja solo y no hace falta ver (DxO por lotes).
    Devuelve cuántas minimizó."""
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    n = 0
    for w in windows():
        if not w["min"] and w["exe"].lower().startswith(exe_prefixes):
            user32.ShowWindow(w["hwnd"], 7)                         # SW_SHOWMINNOACTIVE
            n += 1
    return n


def foreground() -> dict:
    """La ventana que está delante: {"hwnd", "title", "pid", "exe"}."""
    hwnd = user32.GetForegroundWindow()
    for w in windows():
        if w["hwnd"] == hwnd:
            return w
    return {"hwnd": hwnd, "title": "", "pid": 0, "exe": ""}


def dpi_scale(hwnd: int) -> float:
    """Escala de Windows de la pantalla donde está la ventana (1.0 = 100 %, 1.5 = 150 %…)."""
    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        return max(1.0, user32.GetDpiForWindow(hwnd) / 96.0)
    except Exception:  # noqa: BLE001
        return 1.0


def activate(hwnd: int, maximize: bool = True, primary: bool = False) -> bool:
    """Trae la ventana delante (y la maximiza: así los botones caen siempre en el mismo sitio). Con `primary`, antes
    la pasa a la pantalla principal (donde está la pantalla de proceso de RawFlow, que no sale en las capturas)."""
    dpi_aware()
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    if primary:
        r = wintypes.RECT()
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pw, ph = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if not (0 <= cx < pw and 0 <= cy < ph):                     # está en otra pantalla
            user32.ShowWindow(hwnd, 9)                              # SW_RESTORE (una maximizada no se deja mover)
            user32.SetWindowPos(hwnd, None, 60, 60, min(1600, pw - 120), min(900, ph - 120), 0x0004 | 0x0010)
            time.sleep(0.3)
    user32.ShowWindow(hwnd, 3 if maximize else 9)                   # SW_MAXIMIZE / SW_RESTORE
    if user32.GetForegroundWindow() != hwnd:
        _send(_key(0x12), _key(0x12, True))                         # un toque de Alt: Windows deja cambiar de ventana
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    return user32.GetForegroundWindow() == hwnd


def close_window(hwnd: int) -> None:
    """Cierra una ventana como si se pulsara su X (no fuerza nada)."""
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW(hwnd, 0x0010, 0, 0)                         # WM_CLOSE


# ------------------------------------------------------------------ portapapeles
def set_clipboard(text: str) -> None:
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("el portapapeles está ocupado")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(13, h)                              # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def get_clipboard() -> Optional[str]:
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(13)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()

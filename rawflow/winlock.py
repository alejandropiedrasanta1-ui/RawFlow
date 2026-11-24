"""Bloqueo de ratón, trackpad y teclado físicos en Windows (lo mismo que «rfui block» en Mac).

    pythonw -m rawflow.winlock          (o RawFlow.exe --bloqueo)

Ganchos de bajo nivel de Windows (WH_MOUSE_LL / WH_KEYBOARD_LL): descarta lo que viene del hardware (touchpad, cualquier
ratón o teclado USB/Bluetooth) y deja pasar lo inyectado (los clics y teclas que envía RawFlow llevan la marca
LLMHF_INJECTED / LLKHF_INJECTED). Al descartar el movimiento, el cursor no se mueve. Salidas, como en Mac:
  · Esc tres veces (pulsaciones, no la tecla mantenida) → imprime «CANCEL» y se suelta al instante.
  · stdin cerrado (RawFlow lo soltó o se cerró) o RawFlow muerto → se suelta.
  · Ctrl+Alt+Supr siempre funciona (Windows no deja bloquearlo) y la pantalla de bloqueo tampoco se toca.
Imprime «BLOCKING hook» al empezar.
"""
from __future__ import annotations

import os
import sys
import threading
import time


def main() -> int:
    if sys.platform != "win32":
        print("ERROR solo Windows", flush=True)
        return 2
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
    WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 0x100, 0x101, 0x104, 0x105, 0x12
    VK_ESCAPE, LLKHF_INJECTED, LLMHF_INJECTED = 0x1B, 0x10, 0x01

    class KBD(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MSE(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    main_tid = kernel32.GetCurrentThreadId()
    state = {"esc": [], "held": False, "test_injected": os.environ.get("RAWFLOW_BLOQUEO_PRUEBA") == "1"}

    def bye(code: int = 0):
        user32.PostThreadMessageW(main_tid, WM_QUIT, 0, 0)

    @HOOKPROC
    def kb(n, wparam, lparam):
        if n == 0:
            k = KBD.from_address(lparam)
            injected = bool(k.flags & LLKHF_INJECTED) and not state["test_injected"]
            if not injected:
                if k.vkCode == VK_ESCAPE:
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if not state["held"]:                 # mantener Esc no cuenta (autorrepetición)
                            now = time.monotonic()
                            state["esc"] = [t for t in state["esc"] if now - t < 2.5] + [now]
                            if len(state["esc"]) >= 3:
                                print("CANCEL", flush=True)
                                threading.Timer(0.05, bye).start()
                        state["held"] = True
                    elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                        state["held"] = False
                return 1                                      # tecla física: descartada
        return user32.CallNextHookEx(None, n, wparam, lparam)

    @HOOKPROC
    def ms(n, wparam, lparam):
        if n == 0:
            m = MSE.from_address(lparam)
            if not (m.flags & LLMHF_INJECTED) or state["test_injected"]:
                return 1                                      # ratón/touchpad físico: descartado (cursor quieto)
        return user32.CallNextHookEx(None, n, wparam, lparam)

    hmod = kernel32.GetModuleHandleW(None)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb, hmod, 0)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, ms, hmod, 0)
    if not hk or not hm:
        print(f"ERROR no hook ({ctypes.get_last_error()})", flush=True)
        return 3
    print("BLOCKING hook", flush=True)

    def watch_stdin():                                        # RawFlow cerró la tubería: soltar
        try:
            while sys.stdin.readline():
                pass
        except Exception:  # noqa: BLE001
            pass
        bye()

    def watch_parent():                                       # RawFlow murió: soltar
        ppid = os.getppid()
        try:
            import psutil
        except Exception:  # noqa: BLE001
            return
        while True:
            time.sleep(1)
            if not psutil.pid_exists(ppid):
                bye()
                return
    if sys.stdin is not None:
        threading.Thread(target=watch_stdin, daemon=True).start()
    threading.Thread(target=watch_parent, daemon=True).start()

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hk)
    user32.UnhookWindowsHookEx(hm)
    return 0


if __name__ == "__main__":
    sys.exit(main())

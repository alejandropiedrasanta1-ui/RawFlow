"""Escucha «Esc tres veces» en Windows para cancelar el evento (lo mismo que «rfui escuchar» en Mac).

    pythonw -m rawflow.winlock          (o RawFlow.exe --escuchar)

YA NO BLOQUEA NADA (23-nov-2026): el ratón, el touchpad y el teclado siguen libres mientras RawFlow trabaja. Solo mira el
teclado con un gancho de bajo nivel de Windows (WH_KEYBOARD_LL) y deja pasar todas las teclas tal cual:
  · Esc tres veces en 2,5 s (pulsaciones, no la tecla mantenida) → imprime «CANCEL» y termina.
  · Las teclas que envía RawFlow (marca LLKHF_INJECTED) no cuentan: solo las del teclado de verdad.
  · stdin cerrado (RawFlow lo soltó o se cerró) o RawFlow muerto → termina.
Imprime «LISTENING hook» al empezar.
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
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 0x100, 0x101, 0x104, 0x105, 0x12
    VK_ESCAPE, LLKHF_INJECTED = 0x1B, 0x10

    class KBD(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
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
            if not injected and k.vkCode == VK_ESCAPE:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not state["held"]:                     # mantener Esc no cuenta (autorrepetición)
                        now = time.monotonic()
                        state["esc"] = [t for t in state["esc"] if now - t < 2.5] + [now]
                        if len(state["esc"]) >= 3:
                            state["esc"] = []
                            print("CANCEL", flush=True)
                            threading.Timer(0.05, bye).start()
                    state["held"] = True
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    state["held"] = False
        return user32.CallNextHookEx(None, n, wparam, lparam)     # TODAS las teclas siguen su camino: nada se bloquea

    hmod = kernel32.GetModuleHandleW(None)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kb, hmod, 0)
    if not hk:
        print(f"ERROR no hook ({ctypes.get_last_error()})", flush=True)
        return 3
    try:        # Windows quita en silencio un gancho que tarda en responder: con DxO o Retouch4me al 100 % de CPU,
        #         este proceso va con prioridad alta para que Esc ×3 siga funcionando todo el evento
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x80)     # HIGH_PRIORITY_CLASS
    except Exception:  # noqa: BLE001
        pass
    print("LISTENING hook", flush=True)

    def watch_stdin():                                        # RawFlow cerró la tubería: terminar
        try:
            while sys.stdin.readline():
                pass
        except Exception:  # noqa: BLE001
            pass
        bye()

    def watch_parent():                                       # RawFlow murió: terminar
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
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""«Esc tres veces» para cancelar el evento mientras RawFlow trabaja, en Mac y en Windows.

YA NO BLOQUEA NADA (23-nov-2026, a petición del usuario): el ratón, el trackpad y el teclado siguen libres todo el evento.
Lo único que queda es un ayudante que ESCUCHA el teclado sin tocarlo («rfui escuchar» en Mac, rawflow/winlock.py en
Windows) y avisa si pulsas Esc tres veces seguidas: es la manera de cancelar, porque la pantalla de proceso tapa la
ventana de RawFlow y no recibe clics.
  · Esc tres veces seguidas → cancela el proceso.
  · Si RawFlow se cierra o se cuelga, el ayudante termina solo (stdin cerrado / proceso padre muerto).
Si el ayudante se cerrara solo a mitad del evento (no por Esc), se vuelve a lanzar.
(El módulo y la clase conservan su nombre de cuando sí bloqueaban.)
"""
from __future__ import annotations

import subprocess
import time

from PySide6.QtCore import QThread, Signal

from . import core

MAX_RESTARTS = 30
# Windows, modo tranquilo (26-nov-2026): las apps que maneja RawFlow. Si la persona teclea con una de ellas delante,
# winlock se traga la tecla (no llega: Supr en Bridge borraría fotos) y avisa «FOCO» → el teclado vuelve a su ventana.
GUARD_EXES = ("imagenai.exe", "adobe bridge.exe", "luminar neo.exe")


class InputLock(QThread):
    cancel = Signal()
    state = Signal(bool)          # True: escuchando Esc ×3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self._want = False        # True mientras el evento está en marcha

    def _spawn(self):
        cwd, flags = None, 0
        if core.SYSTEM == "Darwin":
            from .imagen_ui import _rfui
            cmd = [_rfui(), "escuchar"]         # (un rfui antiguo no conoce «escuchar»: no arranca y no bloquea nada)
        else:                                    # Windows: gancho de teclado que solo mira (rawflow/winlock.py)
            import os
            import sys
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--escuchar"]
            else:
                exe = sys.executable
                w = os.path.join(os.path.dirname(exe), "pythonw.exe")
                cmd = [w if os.path.exists(w) else exe, "-m", "rawflow.winlock"]
                cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = None
        if core.SYSTEM == "Windows":             # modo tranquilo: las teclas de la persona no llegan a estas apps
            import os as _os
            env = dict(_os.environ, RAWFLOW_GUARD_EXES=",".join(GUARD_EXES))
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1, cwd=cwd, creationflags=flags, env=env)
        first = (p.stdout.readline() or "").strip()             # «LISTENING …» o «ERROR …»
        if not first.startswith("LISTENING"):                   # (jamás se acepta un «BLOCKING»: nada debe bloquear)
            self._kill(p)
            return None
        return p

    @staticmethod
    def _kill(p):
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.terminate()
            p.wait(2)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def listen(self) -> bool:
        """Empieza a escuchar Esc ×3 (sin bloquear nada). True si el ayudante arrancó."""
        if core.SYSTEM not in ("Darwin", "Windows"):
            return False
        if self.active():
            self._want = True
            return True
        if self.isRunning():                      # el hilo de la escucha anterior aún no terminó
            self.wait(3000)
        try:
            p = self._spawn()
        except Exception:  # noqa: BLE001
            p = None
        if not p:
            return False
        self.proc, self._want = p, True
        self.start()
        self.state.emit(True)
        return True

    def run(self):
        restarts = 0
        while True:
            p = self.proc
            if p is None:
                break
            cancelled = False
            try:
                for line in p.stdout:
                    if "CANCEL" in line:
                        cancelled = True
                        self.cancel.emit()
                    elif "FOCO" in line:                # tecleó con una app de RawFlow delante: su teclado, de vuelta
                        try:
                            from . import winscreen
                            winscreen.give_back_now()
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
            try:
                p.wait(5)
            except Exception:  # noqa: BLE001
                pass
            if cancelled or not self._want or self.proc is not p or restarts >= MAX_RESTARTS:
                break
            # se cerró solo a mitad del evento (no por Esc ni porque RawFlow lo soltara): volver a escuchar
            restarts += 1
            self.state.emit(False)
            time.sleep(2)
            if not self._want:
                break
            try:
                np_ = self._spawn()
            except Exception:  # noqa: BLE001
                np_ = None
            if np_ is None:
                continue                          # se reintenta (como mucho MAX_RESTARTS veces)
            if not self._want or self.proc is not p:
                self._kill(np_)
                break
            self.proc = np_
            self.state.emit(True)
        self.state.emit(False)

    def release(self):
        self._want = False
        p, self.proc = self.proc, None
        if not p:
            return
        self._kill(p)
        self.state.emit(False)

    def active(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

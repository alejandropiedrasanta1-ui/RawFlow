"""Bloquea el ratón, el trackpad y el teclado físicos mientras RawFlow trabaja (solo Mac).

Lanza «rfui block»: un filtro de eventos de macOS que descarta lo que viene del hardware (el trackpad del Mac y
cualquier ratón, trackpad o teclado USB/Bluetooth) y deja pasar lo que envía RawFlow (sus clics y teclas sintéticos).
El cursor se queda quieto donde lo dejó RawFlow. Así nadie puede cambiar de app, mover el ratón ni hacer un clic falso
hasta que el evento termine. Dura TODO el evento (antes se soltaba solo a las 3 horas). Salidas de emergencia:
  · Esc tres veces seguidas → cancela el proceso y suelta el bloqueo al instante.
  · Si RawFlow se cierra o se cuelga, el filtro se suelta solo (stdin cerrado / proceso padre muerto).
  · Con la pantalla bloqueada (al despertar del reposo) deja pasar todo, para poder escribir la contraseña.
Si rfui se cerrara solo a mitad del evento (no por Esc), se vuelve a lanzar.
"""
from __future__ import annotations

import subprocess
import time

from PySide6.QtCore import QThread, Signal

from . import core

MAX_RESTARTS = 30


class InputLock(QThread):
    cancel = Signal()
    state = Signal(bool)          # True: bloqueando

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self._want = False        # True mientras el evento está en marcha

    def _spawn(self):
        cwd, flags = None, 0
        if core.SYSTEM == "Darwin":
            from .imagen_ui import _rfui
            cmd = [_rfui(), "block", "0"]
        else:                                    # Windows: ganchos de bajo nivel (rawflow/winlock.py), mismo protocolo
            import os
            import sys
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--bloqueo"]
            else:
                exe = sys.executable
                w = os.path.join(os.path.dirname(exe), "pythonw.exe")
                cmd = [w if os.path.exists(w) else exe, "-m", "rawflow.winlock"]
                cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1, cwd=cwd, creationflags=flags)
        first = (p.stdout.readline() or "").strip()             # «BLOCKING hid» o «ERROR …»
        if not first.startswith("BLOCKING"):
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

    def lock(self) -> bool:
        if core.SYSTEM not in ("Darwin", "Windows"):
            return False
        if self.active():
            self._want = True
            return True
        if self.isRunning():                      # el hilo del bloqueo anterior aún no terminó
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
            except Exception:  # noqa: BLE001
                pass
            try:
                p.wait(5)
            except Exception:  # noqa: BLE001
                pass
            if cancelled or not self._want or self.proc is not p or restarts >= MAX_RESTARTS:
                break
            # se cerró solo a mitad del evento (no por Esc ni porque RawFlow lo soltara): volver a bloquear
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

"""Windows: telemetrías de Windows fuera, UNA vez por equipo (pedido del usuario, 24-nov-2026: «cierre telemetrías…
te doy permiso total»).

Son servicios y tareas del sistema: sin permiso de administrador no se pueden parar, así que RawFlow lo pide una sola
vez (el aviso de Windows «¿Quieres permitir…?») y quedan desactivadas para siempre (también tras reiniciar). Lo que hace:
  · para y desactiva los servicios DiagTrack («Experiencias del usuario y telemetría asociadas») y dmwappushservice;
  · directiva AllowTelemetry = 0 (en Windows Pro queda en el mínimo que Windows permite);
  · desactiva las tareas que recogen datos (Compatibility Appraiser, ProgramDataUpdater, Consolidator, UsbCeip,
    DiskDiagnosticDataCollector).
Se deshace con run_elevated(undo=True). Nada de esto toca los programas ni los ajustes de RawFlow.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict

SERVICES = ("DiagTrack", "dmwappushservice")
_AE = "\\Microsoft\\Windows\\Application Experience\\"
_CEIP = "\\Microsoft\\Windows\\Customer Experience Improvement Program\\"
TASKS = ((_AE, "Microsoft Compatibility Appraiser"), (_AE, "Microsoft Compatibility Appraiser Exp"),
         (_AE, "ProgramDataUpdater"), (_AE, "MareBackup"),
         (_CEIP, "Consolidator"), (_CEIP, "UsbCeip"),
         ("\\Microsoft\\Windows\\DiskDiagnostic\\", "Microsoft-Windows-DiskDiagnosticDataCollector"))
POLICY = r"HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection"
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _start_type(service: str) -> str:
    """«DISABLED», «AUTO», «MANUAL»… o «» si no existe. Del registro (se lee sin ser administrador y no depende del
    idioma de Windows: «sc qc» responde «TIPO_INICIO» en español)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{service}") as k:
            v = winreg.QueryValueEx(k, "Start")[0]
    except OSError:
        return ""
    return {2: "AUTO", 3: "MANUAL", 4: "DISABLED"}.get(int(v), str(v))


def _policy() -> object:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection") as k:
            return winreg.QueryValueEx(k, "AllowTelemetry")[0]
    except OSError:
        return None


def _task_enabled(path: str, name: str):
    """True/False, o None si esa tarea no existe en este Windows (del XML de la tarea: no depende del idioma)."""
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", path + name, "/xml"], capture_output=True, text=True,
                           timeout=10, creationflags=_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return "<Enabled>false</Enabled>" not in r.stdout


def status() -> Dict[str, object]:
    """Servicios (tipo de inicio), directiva AllowTelemetry y tareas de recogida de datos que siguen activas."""
    st: Dict[str, object] = {s: _start_type(s) for s in SERVICES}
    st["AllowTelemetry"] = _policy()
    st["tareas activas"] = [n for p, n in TASKS if _task_enabled(p, n)]
    return st


def disabled() -> bool:
    """¿Ya está todo desactivado? (servicios en DISABLED o inexistentes, directiva en 0, ninguna tarea activa)."""
    if sys.platform != "win32":
        return True
    st = status()
    return (all(st[s] in ("", "DISABLED") for s in SERVICES) and st["AllowTelemetry"] == 0
            and not st["tareas activas"])


def script(undo: bool = False) -> str:
    """El PowerShell que hace (o deshace) todo; corre como administrador."""
    if undo:
        lines = ["Set-Service DiagTrack -StartupType Automatic", "Start-Service DiagTrack",
                 "Set-Service dmwappushservice -StartupType Manual",
                 f"Remove-ItemProperty -Path '{POLICY}' -Name AllowTelemetry"]
        lines += [f"Enable-ScheduledTask -TaskPath '{p}' -TaskName '{n}' | Out-Null" for p, n in TASKS]
    else:
        lines = [f"Stop-Service {s} -Force; Set-Service {s} -StartupType Disabled" for s in SERVICES]
        lines += [f"New-Item -Path '{POLICY}' -Force | Out-Null",
                  f"New-ItemProperty -Path '{POLICY}' -Name AllowTelemetry -PropertyType DWord -Value 0 -Force | Out-Null"]
        lines += [f"Disable-ScheduledTask -TaskPath '{p}' -TaskName '{n}' | Out-Null" for p, n in TASKS]
    return "$ErrorActionPreference = 'SilentlyContinue'\n" + "\n".join(lines) + "\n"


def run_elevated(undo: bool = False, wait: float = 120.0) -> bool:
    """Pide permiso de administrador (el aviso de Windows) y aplica el script. Devuelve True si quedó hecho (o
    deshecho). False si dijiste que no en el aviso o no terminó a tiempo."""
    if sys.platform != "win32":
        return False
    import ctypes
    f = Path(tempfile.gettempdir()) / ("rawflow-telemetria-" + ("deshacer" if undo else "quitar") + ".ps1")
    f.write_text(script(undo), encoding="utf-8-sig")       # con BOM: Windows PowerShell 5 lee bien los acentos
    args = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{f}"'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 0)
    if rc <= 32:                                            # ≤32 = error (p. ej. dijiste «No» en el aviso)
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        if disabled() != undo:
            return True
        time.sleep(1.0)
    return False


if __name__ == "__main__":     # python -m rawflow.winopt [estado|quitar|deshacer]
    cmd = (sys.argv[1:] or ["estado"])[0]
    if cmd == "estado":
        print(status(), "desactivadas" if disabled() else "ACTIVAS")
    else:
        print("hecho" if run_elevated(undo=cmd == "deshacer") else "no se hizo")
        print(status())

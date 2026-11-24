"""Permisos de macOS que RawFlow necesita, pedidos todos de una vez (solo Mac).

macOS solo deja que la persona los acepte; aquí se provoca cada aviso y se comprueba
si ya está concedido. Los que no tienen aviso propio (Acceso total al disco) se abren
en Ajustes del Sistema para activarlos a mano.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

HOME = Path.home()


@dataclass
class Perm:
    key: str
    title: str
    why: str
    check: Callable[[], bool]          # True si ya está concedido
    request: Callable[[], None]        # provoca el aviso o abre Ajustes


def _can_list(p: Path) -> bool:
    try:
        os.listdir(p)
        return True
    except OSError:
        return False


def _osa(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)


def _automation_ok(app: str) -> bool:
    """True si RawFlow ya puede controlar la app. No espera al aviso: si macOS aún
    no tiene respuesta, cuenta como pendiente."""
    if app not in ("System Events", "Finder") and \
            _osa(f'application "{app}" is running').stdout.strip() != "true":
        return None          # no se puede saber sin abrirla: no es «Falta»
    try:
        r = _osa(f'tell application "{app}" to get name', 4)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def _ask_automation(app: str):
    subprocess.run(["open", "-g", "-a", app], check=False)
    try:
        _osa(f'tell application "{app}" to get name', 180)   # macOS muestra «¿Permitir controlar…?»
    except subprocess.TimeoutExpired:
        pass


def _settings(anchor: str):
    subprocess.run(["open", f"x-apple.systempreferences:com.apple.preference.security?{anchor}"], check=False)


# --- Accesibilidad y Grabación de pantalla pedidas por la PROPIA RawFlow (no por su herramienta
#     interna): así macOS pone a «RawFlow» en la lista de Ajustes con su interruptor.
def _ax_trusted(prompt: bool = False) -> bool:
    try:
        import ctypes
        import ctypes.util
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        if not prompt:
            ax.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(ax.AXIsProcessTrusted())
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                          ctypes.c_void_p, ctypes.c_void_p]
        key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
        true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(key)
        vals = (ctypes.c_void_p * 1)(true.value)
        kcb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        vcb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        d = cf.CFDictionaryCreate(None, keys, vals, 1, kcb, vcb)
        ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        return bool(ax.AXIsProcessTrustedWithOptions(d))
    except Exception:  # noqa: BLE001
        return False


def _screen_ok(request: bool = False) -> bool:
    try:
        import ctypes
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        fn = cg.CGRequestScreenCaptureAccess if request else cg.CGPreflightScreenCaptureAccess
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def _app_bundle() -> str:
    """La RawFlow.app en ejecución (o la de Aplicaciones)."""
    import sys
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return str(p)
    return "/Applications/RawFlow.app"


def _guide(anchor: str):
    """Abre la sección exacta de Ajustes del Sistema (y la deja delante)."""
    _settings(anchor)
    time.sleep(1.5)
    subprocess.run(["osascript", "-e", 'tell application "System Settings" to activate'], check=False)


def macos_major() -> int:
    import platform
    try:
        return int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        return 0


def section_names() -> dict:
    """Cómo se llama cada sección en esta versión de macOS."""
    new = macos_major() >= 26
    return {
        "ax": "System & App Protection Override" if new else "Accesibilidad",
        "screen": "Grabación de audio del sistema y pantalla" if new else "Grabación de pantalla",
        "fulldisk": "Acceso completo al disco",
    }


def repair() -> str:
    """Borra las aprobaciones viejas de RawFlow (atadas a compilaciones anteriores) para que las
    nuevas queden ligadas a su firma estable. Solo afecta a RawFlow (com.rawflow.app).
    Devuelve '' si fue bien, o el error de macOS."""
    errs = []
    for svc in ("Accessibility", "ScreenCapture", "SystemPolicyAllFiles", "AppleEvents"):
        r = subprocess.run(["tccutil", "reset", svc, "com.rawflow.app"], capture_output=True, text=True)
        if r.returncode != 0:
            errs.append(f"{svc}: {(r.stderr or r.stdout).strip()}")
    time.sleep(0.8)
    _ax_trusted(True)            # vuelve a poner a RawFlow en la lista, con esta firma
    _screen_ok(True)
    return "; ".join(errs)


def relaunch():
    """Cierra y vuelve a abrir RawFlow (Grabación de pantalla solo se aplica al reabrir)."""
    subprocess.Popen(["/bin/sh", "-c", f'sleep 1.5; open "{_app_bundle()}"'])


def _full_disk_ok() -> bool:
    # Carpetas que solo se pueden leer con Acceso total al disco
    for p in (HOME / "Library/Safari", HOME / "Library/Containers/com.apple.stocks",
              Path("/Library/Application Support/com.apple.TCC")):
        if p.exists():
            return _can_list(p)
    return False


def all_perms() -> List[Perm]:
    perms = []
    for folder, title in (("Documents", "Carpeta Documentos"), ("Downloads", "Carpeta Descargas"),
                          ("Desktop", "Escritorio"), ("Pictures", "Carpeta Imágenes")):
        p = HOME / folder
        perms.append(Perm(folder.lower(), title, "Buscar ISOs y guardar las fotos importadas.",
                          (lambda p=p: _can_list(p)), (lambda p=p: _can_list(p))))
    perms.append(Perm("volumes", "Memorias y discos externos", "Leer las tarjetas y USB conectados.",
                      lambda: all(_can_list(v) for v in Path("/Volumes").iterdir() if v.is_dir()),
                      lambda: [_can_list(v) for v in Path("/Volumes").iterdir() if v.is_dir()]))
    if Path("/Applications/UTM.app").exists():
        perms.append(Perm("utm", "Controlar UTM", "Crear e instalar máquinas virtuales.",
                          lambda: _automation_ok("UTM"), lambda: _ask_automation("UTM")))
    perms.append(Perm("ax", "Accesibilidad", "Hacer los clics en Imagen, Bridge y Retouch4me.",
                      lambda: _ax_trusted(False),
                      lambda: (_ax_trusted(True), _guide("Privacy_Accessibility"))))
    perms.append(Perm("screen", "Grabación de pantalla", "Leer la pantalla de Imagen, Bridge y Retouch4me.",
                      lambda: _screen_ok(False),
                      lambda: (_screen_ok(True), _guide("Privacy_ScreenCapture"))))
    bridge = sorted(Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app"))
    for app, why in (("System Events", "Pulsar atajos de teclado y colocar las ventanas."),
                     ("Finder", "Saber el tamaño de la pantalla y mostrar carpetas."),
                     ("ImagenAI", "Abrir Imagen y traerla al frente."),
                     (bridge[-1].stem if bridge else "", "Abrir Bridge para exportar los JPEG."),
                     ("Retouch4me Arams", "Abrir Retouch4me y traerla al frente.")):
        if app and (app in ("System Events", "Finder") or Path(f"/Applications/{app}.app").exists()
                    or any(b.stem == app for b in bridge)):
            perms.append(Perm("auto_" + app, f"Controlar {app}", why,
                              lambda a=app: _automation_ok(a), lambda a=app: _ask_automation(a)))
    perms.append(Perm("fulldisk", "Acceso total al disco",
                      "Que macOS no vuelva a preguntar por carpetas.",
                      _full_disk_ok, lambda: _guide("Privacy_AllFiles")))
    return perms

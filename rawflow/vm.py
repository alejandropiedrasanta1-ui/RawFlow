"""Crear máquinas virtuales en UTM a partir de una ISO (solo Mac). Sin interfaz.

Mismo proceso que la skill «utm-maquina-virtual»:
  1. localizar la ISO y deducir sistema y arquitectura,
  2. comprobar UTM,
  3. crear la VM con AppleScript y una configuración adecuada para este Mac,
  4. corregir lo que AppleScript deja mal (sin pantalla; Hypervisor en x86),
  5. arrancarla hasta el instalador.
"""
from __future__ import annotations

import os
import platform
import plistlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

UTM_APP = Path("/Applications/UTM.app")
UTMCTL = UTM_APP / "Contents/MacOS/utmctl"
UTM_DOCS = Path.home() / "Library/Containers/com.utmapp.UTM/Data/Documents"
SEARCH_DIRS = [Path.home() / d for d in ("Documents", "Downloads", "Desktop")]

Log = Callable[[str], None]


class VMError(Exception):
    pass


# --------------------------------------------------------------------------
# Detección
# --------------------------------------------------------------------------
def is_mac() -> bool:
    return platform.system() == "Darwin"


def host_is_arm() -> bool:
    return platform.machine() in ("arm64", "aarch64")


def utm_version() -> str:
    """Versión de UTM instalada, o '' si no está."""
    plist = UTM_APP / "Contents/Info.plist"
    if not plist.exists():
        return ""
    try:
        with open(plist, "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "?")
    except Exception:  # noqa: BLE001
        return "?"


def find_isos(max_depth: int = 4) -> List[Path]:
    """ISOs en Documentos, Descargas y Escritorio (las más nuevas primero)."""
    out: List[Path] = []
    for base in SEARCH_DIRS:
        if not base.is_dir():
            continue
        base_depth = len(base.parts)
        for root, dirs, files in os.walk(base):
            if len(Path(root).parts) - base_depth >= max_depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith((".app", ".utm"))]
            for name in files:
                if name.lower().endswith(".iso"):
                    out.append(Path(root) / name)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


@dataclass
class IsoInfo:
    path: Path
    arch: str          # "x86_64" | "aarch64"
    os_kind: str       # "windows" | "linux" | "otro"
    windows11: bool
    label: str         # descripción para mostrar
    emulated: bool     # True si este Mac tendrá que emular (lento)


def inspect_iso(path: Path) -> IsoInfo:
    name = path.stem.lower()
    arch = "aarch64" if re.search(r"arm64|aarch64|\barm\b", name) else "x86_64"
    win_words = ("win", "windows", "tomex", "ltsc", "tiny10", "tiny11", "ghost spectre", "atlas")
    if any(w in name for w in win_words):
        os_kind = "windows"
    elif re.search(r"ubuntu|debian|fedora|mint|arch|kali|linux|manjaro|pop|zorin|elementary", name):
        os_kind = "linux"
    else:
        os_kind = "otro"
    win11 = os_kind == "windows" and bool(re.search(r"(win(dows)?|tomexos|tiny)\s*[-_ ]?11", name))
    emulated = host_is_arm() and arch == "x86_64"
    kind = {"windows": "Windows", "linux": "Linux", "otro": "Sistema"}[os_kind]
    ver = " 11" if win11 else (" 10" if os_kind == "windows" and "10" in name else "")
    label = f"{kind}{ver} · {'x64' if arch == 'x86_64' else 'ARM64'}"
    label += " · emulado (lento)" if emulated else " · nativo (rápido)"
    return IsoInfo(path, arch, os_kind, win11, label, emulated)


def recommended() -> dict:
    """Memoria, núcleos y disco razonables para este Mac."""
    try:
        mem_gb = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) // 2**30
    except Exception:  # noqa: BLE001
        mem_gb = 8
    ncpu = os.cpu_count() or 4
    return {
        "memory_mb": max(2048, min(8192, mem_gb * 1024 // 2)),
        "cores": max(2, min(4, ncpu // 2)),
        "disk_gb": 64,
        "max_memory_mb": max(2048, (mem_gb - 4) * 1024),
        "max_cores": max(1, ncpu - 2),
    }


def default_name(info: IsoInfo) -> str:
    base = re.sub(r"[\s_\-]+", " ", info.path.stem)
    base = re.sub(r"\b(v\d+(\.\d+)*|x64|x86|amd64|arm64|iso)\b", "", base, flags=re.I)
    base = re.sub(r"\s{2,}", " ", base).strip(" -") or "Máquina virtual"
    words = base.split()
    return " ".join(words[:3]) + (" x64" if info.arch == "x86_64" else " ARM")


# --------------------------------------------------------------------------
# UTM
# --------------------------------------------------------------------------
def _run(cmd: List[str], timeout: int = 120) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise VMError((p.stderr or p.stdout).strip() or f"falló: {' '.join(cmd[:2])}")
    return p.stdout.strip()


def _osa(script: str, timeout: int = 180) -> str:
    return _run(["osascript", "-e", script], timeout)


def _automation_error(e: Exception) -> VMError:
    t = str(e)
    if "-1743" in t or "not allowed" in t.lower() or "no está autorizad" in t.lower():
        return VMError("macOS no dejó que RawFlow controle UTM. Ve a Ajustes del Sistema → Privacidad y "
                       "seguridad → Automatización, activa «UTM» bajo RawFlow y vuelve a intentarlo.")
    return VMError(f"No pude hablar con UTM: {t}")


def list_vms() -> List[dict]:
    """[{uuid, status, name}] de las VMs de UTM, por AppleScript (utmctl de respaldo).
    Lanza VMError si no se puede saber: así nunca se crea una VM con un nombre repetido.
    No lee la carpeta privada de UTM: una app compilada no tiene permiso para eso."""
    if not utm_version():
        return []
    script = ('tell application "UTM"\n'
              '  set r to ""\n'
              '  repeat with v in virtual machines\n'
              '    set r to r & (id of v) & tab & (status of v as text) & tab & (name of v) & linefeed\n'
              '  end repeat\n'
              '  return r\n'
              'end tell')
    try:
        out = _osa(script, 60)
        return [{"uuid": p[0], "status": p[1], "name": p[2]}
                for p in (line.split("\t", 2) for line in out.splitlines()) if len(p) == 3]
    except VMError as e:
        if UTMCTL.exists():
            try:
                out = _run([str(UTMCTL), "list"], 30)
                return [{"uuid": p[0], "status": p[1], "name": p[2]}
                        for p in (line.split(None, 2) for line in out.splitlines()[1:]) if len(p) == 3]
            except Exception:  # noqa: BLE001
                pass
        raise _automation_error(e)


def unique_name(name: str) -> str:
    try:
        taken = {v["name"].lower() for v in list_vms()}
    except VMError:
        taken = set()        # create_vm vuelve a comprobarlo y se detiene si no puede
    cand, n = name, 2
    while cand.lower() in taken:
        cand, n = f"{name} {n}", n + 1
    return cand


@dataclass
class VMSpec:
    iso: Path
    name: str
    memory_mb: int
    cores: int
    disk_gb: int
    info: IsoInfo = field(repr=False, default=None)  # type: ignore[assignment]
    extra_isos: List[Path] = field(default_factory=list)   # CDs adicionales (respuestas, drivers)
    press_boot_key: bool = True                            # pulsa sola «Press any key to boot from CD»


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def create_vm(spec: VMSpec, log: Log) -> str:
    """Crea, corrige y arranca la VM. Devuelve su UUID."""
    if not is_mac():
        raise VMError("Crear máquinas virtuales con UTM solo funciona en Mac.")
    if not utm_version():
        raise VMError("UTM no está instalado. Descárgalo de https://mac.getutm.app y vuelve a intentarlo.")
    if not spec.iso.exists():
        raise VMError(f"No encuentro la ISO:\n{spec.iso}")
    info = spec.info or inspect_iso(spec.iso)
    vms = list_vms()                     # si falla, se detiene aquí: nunca nombres repetidos
    if spec.name.lower() in {v["name"].lower() for v in vms}:
        raise VMError(f"Ya existe una VM llamada «{spec.name}» en UTM. Elige otro nombre.")
    running = [v["name"] for v in vms if v["status"] not in ("stopped", "detenido")]
    if running:
        log("Aviso: hay VMs encendidas (" + ", ".join(running) + "); compartirán la memoria del Mac.")

    x86 = info.arch == "x86_64"
    emulated = x86 and host_is_arm()
    log(f"Creando «{spec.name}» ({info.label}) · {spec.memory_mb // 1024} GB · "
        f"{spec.cores} núcleos · disco {spec.disk_gb} GB…")
    iface = "ide" if x86 else "usb"
    net = '{hardware:"e1000", mode:shared}' if x86 else '{hardware:"virtio-net-pci", mode:shared}'
    display = "virtio-vga" if x86 else "virtio-ramfb-gl"
    cds = [spec.iso] + [Path(p) for p in spec.extra_isos]
    sets = "\n".join(f'  set iso{i} to POSIX file "{_esc(str(p))}"' for i, p in enumerate(cds))
    drives = ", ".join(f"{{removable:true, interface:{iface}, source:iso{i}}}" for i in range(len(cds)))
    drives += f", {{interface:nvme, guest size:{spec.disk_gb * 1024}}}"
    tpm = "true" if info.windows11 else "false"
    # Todo en una sola orden: sin leer la carpeta privada de UTM ni reiniciarlo.
    script = f'''
tell application "UTM"
{sets}
  set vm to make new virtual machine with properties {{backend:qemu, configuration:{{name:"{_esc(spec.name)}", architecture:"{info.arch}", memory:{spec.memory_mb}, cpu cores:{spec.cores}, hypervisor:{str(not emulated).lower()}, uefi:true, tpm:{tpm}, drives:{{{drives}}}, network interfaces:{{{net}}}, displays:{{{{hardware:"{display}", dynamic resolution:true}}}}}}}}
  return id of vm
end tell'''
    try:
        uuid = _osa(script, 300)
    except VMError as e:
        raise _automation_error(e) from e
    log(f"VM creada ({uuid}): pantalla, UEFI" + (", TPM" if info.windows11 else "") +
        (", emulación x86" if emulated else ", virtualización nativa") + ".")

    log("Arrancando la máquina virtual…")
    try:
        _osa(f'tell application "UTM" to start (virtual machine id "{uuid}")', 120)
    except VMError:
        _run([str(UTMCTL), "start", uuid], 120)
    if spec.press_boot_key:
        press_boot_key(uuid, log)
    log("✓  Encendida. En la ventana de UTM sale el instalador.")
    return uuid


def press_boot_key(uuid: str, log: Log, seconds: int = 10):
    """Pulsa «q» dentro de la VM unos segundos para pasar «Press any key to boot from CD».
    Usa el AppleScript de UTM: no necesita foco ni mover el ratón.
    Ojo: NO usar espacio/Intro: si el instalador ya está en pantalla activarían «Cancelar».
    «q» no es atajo de ningún botón del instalador de Windows."""
    log("Pulsando una tecla para arrancar desde el CD…")
    n = max(1, int(seconds / 0.5))
    try:
        _osa(f'''tell application "UTM"
  set vm to virtual machine id "{uuid}"
  repeat {n} times
    try
      input keystroke vm text "q"
    end try
    delay 0.5
  end repeat
end tell''', seconds + 60)
    except VMError as e:
        log(f"(No pude pulsar la tecla automáticamente: {e})")


BOOT_TIPS = (
    "Si aparece «Press any key to boot from CD or DVD», haz clic dentro de la ventana "
    "de la VM y pulsa una tecla enseguida.\n\n"
    "Si llegas tarde y sale «Shell>» (consola UEFI), escribe  fs0:\\efi\\boot\\bootx64.efi  "
    "y pulsa Intro y luego cualquier tecla. Ojo: en esa consola el teclado es de EE. UU. "
    "(«:» está en Mayús+Ñ y «\\» en la tecla «Ç»).\n\n"
    "Cuando termine la instalación, quita la ISO desde el icono de CD de la ventana de UTM."
)

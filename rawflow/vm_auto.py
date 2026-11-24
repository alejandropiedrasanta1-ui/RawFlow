"""Instalación de Windows en UTM sin clics («Instalar máquina virtual»). Sin interfaz.

Hace de una vez lo que antes eran muchos pasos a mano:
  1. busca la ISO de Windows (prefiere la ARM64 nativa) y los drivers de UTM (Guest Tools),
  2. crea un CD de respuestas: los drivers de UTM + un Autounattend.xml propio
     (idioma, teclado, edición Pro, disco entero, cuenta local sin red, privacidad,
     e instala los drivers al primer inicio de sesión),
  3. crea la VM con la ISO de Windows + el CD de respuestas (vm.create_vm),
  4. pulsa sola «Press any key to boot from CD»,
  5. espera hasta que Windows arranca con los drivers (el agente responde con su IP).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional
from xml.sax.saxutils import escape

from . import vm

Log = Callable[[str], None]

# La misma que usa UTM («Instalar herramientas de invitado»); la «-latest» sin 3d es antigua (0.1.271)
GUEST_TOOLS_URL = "https://getutm.app/downloads/utm-guest-tools-3d-latest.iso"
UTM_TOOLS_DIR = (Path.home() / "Library/Containers/com.utmapp.UTM/Data/Library/"
                 "Application Support/GuestSupportTools")
RAWFLOW_DIR = Path.home() / "Library/Application Support/RawFlow"
WIN_PAGE = "https://www.microsoft.com/es-es/software-download/windows11arm64"
# Clave genérica pública de Microsoft (GVLK) de Windows Pro: solo elige la edición, no activa.
PRO_KEY = "W269N-WFGWX-YVC9B-4J6C9-T83GX"


# --------------------------------------------------------------------------
# Qué instalar
# --------------------------------------------------------------------------
def find_windows_iso() -> Optional[Path]:
    """La mejor ISO de Windows disponible: ARM64 primero (va nativa), luego oficiales x64."""
    cands = []
    for p in vm.find_isos():
        info = vm.inspect_iso(p)
        if info.os_kind != "windows":
            continue
        official = bool(re.search(r"win(dows)?\s*_?1[01]|Win1[01]_", p.name, re.I)) and "tomex" not in p.name.lower()
        score = (2 if info.arch == "aarch64" else 0) + (1 if official else 0)
        cands.append((score, p.stat().st_mtime, p))
    cands.sort(reverse=True)
    return cands[0][2] if cands else None


def iso_language(iso: Path) -> str:
    m = re.search(r"_([a-z]{2})-([a-z]{2})_", iso.name.lower())
    if m:
        return f"{m.group(1)}-{m.group(2).upper()}"
    return "es-MX"


def host_keyboard() -> str:
    """InputLocale de Windows que coincide con el teclado del Mac."""
    try:
        out = subprocess.check_output(
            ["defaults", "read", str(Path.home() / "Library/Preferences/com.apple.HIToolbox")],
            text=True, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        out = ""
    table = {
        "Latin American": "080a:0000080a",
        "Spanish - ISO": "0c0a:0000040a", "Spanish": "0c0a:0000040a",
        "U.S.": "0409:00000409", "ABC": "0409:00000409", "US": "0409:00000409",
        "British": "0809:00000809", "French": "040c:0000040c", "German": "0407:00000407",
        "Brazilian": "0416:00010416", "Portuguese": "0816:00000816", "Italian": "0410:00000410",
    }
    for name, code in table.items():
        if f'"KeyboardLayout Name" = "{name}"' in out or f"KeyboardLayout Name\" = {name};" in out:
            return code
    return "080a:0000080a"


_TZ = {  # IANA (Mac) -> zona de Windows
    "America/Guatemala": "Central America Standard Time", "America/El_Salvador": "Central America Standard Time",
    "America/Tegucigalpa": "Central America Standard Time", "America/Managua": "Central America Standard Time",
    "America/Costa_Rica": "Central America Standard Time", "America/Mexico_City": "Central Standard Time (Mexico)",
    "America/Monterrey": "Central Standard Time (Mexico)", "America/Merida": "Central Standard Time (Mexico)",
    "America/Cancun": "Eastern Standard Time (Mexico)", "America/Tijuana": "Pacific Standard Time (Mexico)",
    "America/Hermosillo": "US Mountain Standard Time", "America/Chihuahua": "Central Standard Time (Mexico)",
    "America/Bogota": "SA Pacific Standard Time", "America/Lima": "SA Pacific Standard Time",
    "America/Panama": "SA Pacific Standard Time", "America/Guayaquil": "SA Pacific Standard Time",
    "America/Caracas": "Venezuela Standard Time", "America/Santiago": "Pacific SA Standard Time",
    "America/Argentina/Buenos_Aires": "Argentina Standard Time", "America/Montevideo": "Montevideo Standard Time",
    "America/La_Paz": "SA Western Standard Time", "America/Santo_Domingo": "SA Western Standard Time",
    "America/Puerto_Rico": "SA Western Standard Time", "America/Asuncion": "Paraguay Standard Time",
    "America/Havana": "Cuba Standard Time", "America/Sao_Paulo": "E. South America Standard Time",
    "America/New_York": "Eastern Standard Time", "America/Chicago": "Central Standard Time",
    "America/Denver": "Mountain Standard Time", "America/Los_Angeles": "Pacific Standard Time",
    "Europe/Madrid": "Romance Standard Time", "Europe/London": "GMT Standard Time",
    "Europe/Paris": "Romance Standard Time", "Europe/Berlin": "W. Europe Standard Time",
    "Atlantic/Canary": "GMT Standard Time", "UTC": "UTC",
}


def host_timezone() -> str:
    """Zona horaria de Windows equivalente a la del Mac ('' si no se conoce)."""
    try:
        name = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
    except OSError:
        return ""
    return _TZ.get(name, "")


# --------------------------------------------------------------------------
# Drivers de UTM (Guest Tools)
# --------------------------------------------------------------------------
def guest_tools_iso(log: Log) -> Path:
    """ISO oficial de drivers de UTM: la que ya bajó UTM o, si no hay, se descarga de getutm.app."""
    RAWFLOW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAWFLOW_DIR / "utm-guest-tools-3d-latest.iso"
    (RAWFLOW_DIR / "utm-guest-tools-latest.iso").unlink(missing_ok=True)   # versión antigua
    if dest.exists() and dest.stat().st_size > 50_000_000:
        return dest
    try:   # la copia de UTM (una app compilada normalmente no puede leer esa carpeta)
        for p in sorted(UTM_TOOLS_DIR.glob("utm-guest-tools*.iso"), key=lambda p: p.stat().st_mtime, reverse=True):
            shutil.copy2(p, dest)
            return dest
    except OSError:
        pass
    log("Descargando los drivers de UTM (≈150 MB) desde getutm.app…")
    part = dest.with_suffix(".part")
    r = subprocess.run(["curl", "-L", "--fail", "-s", "-S", "--retry", "5", "-C", "-",
                        "-o", str(part), GUEST_TOOLS_URL])
    if r.returncode != 0 or not part.exists():
        raise vm.VMError("No pude descargar los drivers de UTM. Revisa la conexión y reintenta.")
    part.rename(dest)
    return dest


# --------------------------------------------------------------------------
# Autounattend.xml
# --------------------------------------------------------------------------
def build_unattend(arch: str, user: str, lang: str, keyboard: str, computer: str = "*",
                   timezone: str = "") -> str:
    a = "arm64" if arch == "aarch64" else "amd64"
    c = (f'processorArchitecture="{a}" publicKeyToken="31bf3856ad364e35" '
         f'language="neutral" versionScope="nonSxS"')
    user = escape(user)
    intl = (f"<InputLocale>{keyboard}</InputLocale><SystemLocale>{lang}</SystemLocale>"
            f"<UILanguage>{lang}</UILanguage><UserLocale>{lang}</UserLocale>")
    bypass = "".join(
        f'<RunSynchronousCommand wcm:action="add"><Order>{i}</Order>'
        f"<Path>reg add HKLM\\System\\Setup\\LabConfig /v {k} /t REG_DWORD /d 1 /f</Path>"
        f"</RunSynchronousCommand>"
        for i, k in enumerate(["BypassTPMCheck", "BypassSecureBootCheck", "BypassRAMCheck", "BypassCPUCheck"], 1))
    drv = "ARM64" if a == "arm64" else "amd64"
    driver_paths = "".join(
        f'<PathAndCredentials wcm:action="add" wcm:keyValue="{i}"><Path>{L}:\\Drivers\\{d}\\w10\\{drv}</Path></PathAndCredentials>'
        for i, (L, d) in enumerate([(L, d) for L in "DEF" for d in ("viostor", "vioscsi", "NetKVM")], 1))
    tools = ("cmd /c for %d in (D E F G H) do for %f in (%d:\\utm-guest-tools*.exe) do "
             "start \"\" /wait \"%f\" /S")
    tz = f"<TimeZone>{escape(timezone)}</TimeZone>" if timezone else ""
    return f'''<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <!-- Generado por RawFlow «Instalar máquina virtual» -->
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" {c}>
      <SetupUILanguage><UILanguage>{lang}</UILanguage></SetupUILanguage>{intl}
    </component>
    <component name="Microsoft-Windows-PnpCustomizationsWinPE" {c}>
      <DriverPaths>{driver_paths}</DriverPaths>
    </component>
    <component name="Microsoft-Windows-Setup" {c}>
      <Diagnostics><OptIn>false</OptIn></Diagnostics>
      <RunSynchronous>{bypass}</RunSynchronous>
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall><OSImage><InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo></OSImage></ImageInstall>
      <UserData>
        <AcceptEula>true</AcceptEula>
        <FullName>{user}</FullName>
        <ProductKey><Key>{PRO_KEY}</Key><WillShowUI>OnError</WillShowUI></ProductKey>
      </UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Deployment" {c}>
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add"><Order>1</Order>
          <Path>reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE /v BypassNRO /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>2</Order>
          <Path>reg add HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\OOBE /v DisablePrivacyExperience /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>3</Order>
          <Path>reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation /v RealTimeIsUniversal /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
      </RunSynchronous>
    </component>
    <component name="Microsoft-Windows-Security-SPP-UX" {c}><SkipAutoActivation>true</SkipAutoActivation></component>
    <component name="Microsoft-Windows-SQMApi" {c}><CEIPEnabled>0</CEIPEnabled></component>
    <component name="Microsoft-Windows-Shell-Setup" {c}><ComputerName>{computer}</ComputerName>{tz}</component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" {c}>{intl}</component>
    <component name="Microsoft-Windows-Shell-Setup" {c}>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <ProtectYourPC>3</ProtectYourPC>
        <VMModeOptimizations><SkipWinREInitialization>true</SkipWinREInitialization></VMModeOptimizations>
      </OOBE>
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Name>{user}</Name><DisplayName>{user}</DisplayName><Group>Administrators</Group>
            <Password><Value></Value><PlainText>true</PlainText></Password>
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>
      <AutoLogon>
        <Enabled>true</Enabled><Username>{user}</Username><LogonCount>1</LogonCount>
        <Password><Value></Value><PlainText>true</PlainText></Password>
      </AutoLogon>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add"><Order>1</Order>
          <CommandLine>cmd /c powercfg -h off</CommandLine><Description>Sin hibernación</Description></SynchronousCommand>
        <SynchronousCommand wcm:action="add"><Order>2</Order>
          <CommandLine>{tools}</CommandLine><Description>Drivers de UTM</Description></SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
'''


def build_answers_iso(arch: str, user: str, lang: str, keyboard: str, out: Path, log: Log) -> Path:
    """CD «respuestas»: contenido de los drivers de UTM + nuestro Autounattend.xml."""
    tools = guest_tools_iso(log)
    log("Preparando el CD de respuestas (drivers + instalación automática)…")
    for old in RAWFLOW_DIR.glob("respuestas-*.iso"):   # solo hacen falta durante la instalación
        if old != out and time.time() - old.stat().st_mtime > 3 * 3600:
            old.unlink(missing_ok=True)
    work = Path(tempfile.mkdtemp(prefix="rawflow-unattend-"))
    mnt = None
    try:
        r = subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noverify", str(tools)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise vm.VMError(f"No pude abrir los drivers de UTM: {r.stderr.strip()}")
        mnt = r.stdout.strip().splitlines()[-1].split("\t")[-1]
        src = work / "cd"
        subprocess.run(["ditto", mnt, str(src)], check=True)
        subprocess.run(["chmod", "-R", "u+w", str(src)], check=False)
        for old in src.glob("[Aa]utounattend.xml"):
            old.unlink()
        (src / "Autounattend.xml").write_text(build_unattend(arch, user, lang, keyboard, timezone=host_timezone()),
                                              encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        r = subprocess.run(["hdiutil", "makehybrid", "-iso", "-joliet", "-default-volume-name",
                            "RAWFLOW_AUTO", "-o", str(out), str(src)], capture_output=True, text=True)
        if r.returncode != 0:
            raise vm.VMError(f"No pude crear el CD de respuestas: {r.stderr.strip()}")
        return out
    finally:
        if mnt:
            subprocess.run(["hdiutil", "detach", mnt, "-quiet"], check=False)
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# Todo junto
# --------------------------------------------------------------------------
def query_ip(uuid: str) -> List[str]:
    try:
        out = vm._osa(f'tell application "UTM" to query ip (virtual machine id "{uuid}")', 20)
    except Exception:  # noqa: BLE001
        return []
    return [s.strip() for s in out.split(",") if re.match(r"\d+\.\d+\.\d+\.\d+$", s.strip())]


def install_windows(iso: Path, user: str, log: Log, memory_mb: int = 0, cores: int = 0,
                    disk_gb: int = 64, wait_minutes: int = 90, should_stop=lambda: False, name: str = "") -> str:
    """Crea la VM, la instala sola con drivers y espera a que Windows tenga red. Devuelve el UUID."""
    info = vm.inspect_iso(iso)
    if info.os_kind != "windows":
        raise vm.VMError("La instalación automática es para ISOs de Windows 10/11.")
    rec = vm.recommended()
    name = vm.unique_name(name or ("Windows 11 ARM" if info.arch == "aarch64" and info.windows11
                                   else vm.default_name(info)))
    lang, kb = iso_language(iso), host_keyboard()
    log(f"ISO: {iso.name}  ·  {info.label}  ·  idioma {lang}  ·  cuenta «{user}»")
    answers = build_answers_iso(info.arch, user, lang, kb, RAWFLOW_DIR / f"respuestas-{int(time.time())}.iso", log)
    spec = vm.VMSpec(iso, name, memory_mb or rec["memory_mb"], cores or rec["cores"], disk_gb, info,
                     extra_isos=[answers])
    uuid = vm.create_vm(spec, log)
    log("Instalando Windows solo: copia, reinicios, cuenta y drivers. No hace falta tocar nada.")
    t0 = time.monotonic()
    last = -1
    while time.monotonic() - t0 < wait_minutes * 60:
        if should_stop():
            log("Seguimiento detenido; la instalación sigue dentro de la VM.")
            return uuid
        ips = query_ip(uuid)
        if ips:
            log(f"✓  Windows listo con drivers e internet (IP {ips[0]}) en "
                f"{(time.monotonic() - t0) / 60:.0f} min.")
            return uuid
        mins = int((time.monotonic() - t0) // 60)
        if mins != last and mins % 2 == 0:
            last = mins
            log(f"   …instalando ({mins} min)")
        time.sleep(15)
    log("La instalación sigue en marcha dentro de la VM (tarda más de lo normal). Mira la ventana de UTM.")
    return uuid

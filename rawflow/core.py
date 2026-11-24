"""Lógica sin interfaz: detección de unidades, escaneo de RAW, copia y DxO.

Funciona en macOS, Windows y Linux. No depende de Qt, así que se puede
probar o usar desde la línea de comandos.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import os
import platform
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import psutil

SYSTEM = platform.system()  # "Darwin", "Windows", "Linux"

# --------------------------------------------------------------------------
# Formatos RAW de foto (no incluye RAW de video como .braw / .r3d)
# --------------------------------------------------------------------------
RAW_EXTENSIONS = {
    ".dng",                                  # Adobe / Leica / Pentax / móviles
    ".cr2", ".cr3", ".crw",                  # Canon
    ".nef", ".nrw",                          # Nikon
    ".arw", ".srf", ".sr2",                  # Sony
    ".raf",                                  # Fujifilm
    ".orf", ".ori",                          # Olympus / OM System
    ".rw2", ".raw", ".rwl",                  # Panasonic / Leica
    ".pef", ".ptx",                          # Pentax
    ".srw",                                  # Samsung
    ".x3f",                                  # Sigma
    ".3fr", ".fff",                          # Hasselblad
    ".iiq", ".cap", ".eip",                  # Phase One
    ".mos",                                  # Leaf
    ".mef",                                  # Mamiya
    ".mrw",                                  # Minolta
    ".kdc", ".dcr", ".dcs",                  # Kodak
    ".erf",                                  # Epson
    ".gpr",                                  # GoPro
    ".bay",                                  # Casio
    ".k25", ".mdc",                          # Kodak antiguas / Minolta
}

# Lo que DxO PureRAW sabe abrir (se lee de su Info.plist; esta lista es la de PureRAW 6)
PURERAW_EXTENSIONS_DEFAULT = {".3fr", ".arw", ".cr2", ".cr3", ".crw", ".dng", ".fff", ".gpr", ".iiq",
                              ".mrw", ".nef", ".nrw", ".orf", ".ori", ".pef", ".raf", ".rw2", ".rwl", ".srw"}
_DXO_EXT_CACHE: dict = {}


def dxo_extensions(app_path: str = "") -> set:
    """Extensiones RAW que acepta la versión instalada de PureRAW."""
    if app_path in _DXO_EXT_CACHE:
        return _DXO_EXT_CACHE[app_path]
    exts = set()
    try:
        import plistlib
        with open(Path(app_path) / "Contents/Info.plist", "rb") as f:
            info = plistlib.load(f)
        for dt_ in info.get("CFBundleDocumentTypes", []):
            for e in dt_.get("CFBundleTypeExtensions", []):
                e = "." + str(e).lower().lstrip(".")
                if e in RAW_EXTENSIONS:
                    exts.add(e)
    except Exception:  # noqa: BLE001
        pass
    exts = exts or set(PURERAW_EXTENSIONS_DEFAULT)
    _DXO_EXT_CACHE[app_path] = exts
    return exts

# Carpetas que nunca vale la pena recorrer
SKIP_DIRS = {
    ".spotlight-v100", ".trashes", ".fseventsd", ".temporaryitems",
    ".documentrevisions-v100", "system volume information", "$recycle.bin",
    "recycler", ".trash", ".trash-1000", "lost+found",
}


# --------------------------------------------------------------------------
# Unidades
# --------------------------------------------------------------------------
@dataclass
class Drive:
    mountpoint: str
    label: str
    fstype: str
    total: int
    used: int
    removable: bool

    @property
    def key(self) -> str:
        return self.mountpoint


def _win_volume(root: str):
    """Etiqueta y sistema de archivos de una unidad de Windows (API nativa); OSError si no hay nada dentro."""
    import ctypes

    buf = ctypes.create_unicode_buffer(261)
    fs_buf = ctypes.create_unicode_buffer(261)
    if not ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, fs_buf, 261):
        raise OSError(f"sin volumen en {root}")
    return buf.value, fs_buf.value

_WIN_VOL_CACHE: dict = {}                # letra → (etiqueta, sistema de archivos, total, usado) mientras siga puesta
_WIN_BUS_EXTERNOS = {7, 12, 13}          # USB y tarjetas de memoria (SD, MMC); lo interno es SATA=11, NVMe=17, RAID=8…


def _win_probe(root: str):
    """(bus, lista) de una letra de Windows: por dónde está conectado su disco (7=USB, 12=SD, 13=MMC, 11=SATA,
    17=NVMe…; -1 si no se sabe) y si tiene algo dentro (un lector sin tarjeta no). Son dos preguntas al controlador,
    sin leer el disco ni pedir permisos de administrador: se pueden repetir cada segundo sin frenar la memoria."""
    import ctypes
    import struct
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.CreateFileW("\\\\.\\" + root[:2], 0, 3, None, 3, 0, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return -1, False
    try:
        bus = -1
        query = struct.pack("<III", 0, 0, 0)                 # StorageDeviceProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(1024)
        n = wintypes.DWORD(0)
        if k.DeviceIoControl(h, 0x2D1400, query, len(query), out, len(out), ctypes.byref(n), None) and n.value >= 32:
            bus = struct.unpack_from("<I", out.raw, 28)[0]   # STORAGE_DEVICE_DESCRIPTOR.BusType
        lista = bool(k.DeviceIoControl(h, 0x2D0800, None, 0, None, 0, ctypes.byref(n), None))   # CHECK_VERIFY2
        return bus, lista
    finally:
        k.CloseHandle(h)


def _win_bus_type(root: str) -> int:
    return _win_probe(root)[0]

_MAC_INFO_CACHE: dict = {}


def _mac_disk_info(mountpoint: str) -> dict:
    """Consulta diskutil (una vez por volumen) si el disco es interno/extraíble."""
    if mountpoint in _MAC_INFO_CACHE:
        return _MAC_INFO_CACHE[mountpoint]
    info = {}
    try:
        import plistlib
        out = subprocess.run(["diskutil", "info", "-plist", mountpoint],
                             capture_output=True, timeout=5).stdout
        d = plistlib.loads(out)
        bus = str(d.get("BusProtocol", ""))
        info = {
            # .dmg / .iso montados (instaladores, ISOs de máquinas virtuales): no son memorias
            "virtual": bus == "Disk Image" or d.get("VirtualOrPhysical") == "Virtual",
            "internal": bool(d.get("Internal", False)) and d.get("BusProtocol") not in ("USB", "SD"),
            "removable": bool(d.get("RemovableMedia") or d.get("Removable")
                              or d.get("Ejectable") or d.get("BusProtocol") in ("USB", "SD")),
        }
    except Exception:
        pass
    _MAC_INFO_CACHE[mountpoint] = info
    return info


def list_drives() -> List[Drive]:
    """Lista memorias, USB y discos externos (nunca los discos internos del equipo)."""
    drives: List[Drive] = []
    seen = set()

    if SYSTEM == "Windows":
        # Todo con la API nativa (ctypes suelta el GIL): psutil.disk_partitions() tarda segundos con algunos discos y,
        # como no lo suelta, congelaba la ventana cada vez que el vigilante miraba las memorias.
        import ctypes
        k = ctypes.windll.kernel32
        sys_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\") + "\\"
        mask = k.GetLogicalDrives()
        presentes = set()
        for i, letter in enumerate(string.ascii_uppercase):
            root = f"{letter}:\\"
            if not (mask >> i) & 1 or root == sys_drive:
                continue
            dtype = k.GetDriveTypeW(ctypes.c_wchar_p(root))
            if dtype not in (2, 3):  # red, CD, disco RAM…
                continue
            bus, lista = _win_probe(root)
            if bus not in _WIN_BUS_EXTERNOS and not (bus == -1 and dtype == 2):
                continue  # solo USB y tarjetas: los discos internos de la PC (SATA, NVMe…) ni se tocan
            if not lista:
                continue  # lector sin tarjeta
            try:
                if root not in _WIN_VOL_CACHE:      # etiqueta y tamaños se leen UNA vez por memoria: preguntarlos en
                    u = shutil.disk_usage(root)     # cada vuelta tarda (hasta 1 s en algunas USB) y la frena
                    _WIN_VOL_CACHE[root] = _win_volume(root) + (u.total, u.used)
                label, fstype, total, used = _WIN_VOL_CACHE[root]
            except Exception:
                continue  # aún montándose, o ilegible
            presentes.add(root)
            drives.append(Drive(root, label or f"Disco {root[:2]}", fstype, total, used, dtype == 2))
        for root in [r for r in _WIN_VOL_CACHE if r not in presentes]:
            _WIN_VOL_CACHE.pop(root, None)             # la sacaron: la siguiente puede llamarse distinto
        return drives

    # macOS / Linux
    if SYSTEM == "Darwin":
        prefixes = ("/Volumes/",)
    else:
        prefixes = ("/media/", "/run/media/", "/mnt/")

    for p in psutil.disk_partitions(all=False):
        mp = p.mountpoint
        if not mp.startswith(prefixes) or mp in seen:
            continue
        # "Macintosh HD" en /Volumes suele ser un enlace a "/"
        try:
            if os.path.realpath(mp) == "/":
                continue
        except OSError:
            continue
        seen.add(mp)
        try:
            u = psutil.disk_usage(mp)
        except Exception:
            continue
        removable = p.fstype.lower() in {"msdos", "exfat", "vfat", "fat32", "ntfs", "fuseblk"}
        if SYSTEM == "Darwin":
            info = _mac_disk_info(mp)
            if info.get("virtual") and not (os.environ.get("RAWFLOW_SIMULAR_USB")
                                            and os.path.basename(mp) == os.environ["RAWFLOW_SIMULAR_USB"]):
                continue  # imágenes de disco montadas (UTM.dmg, ISOs…): no son dispositivos
            if info.get("internal") and not info.get("removable"):
                continue  # Recovery, Preboot, particiones del disco interno…
            removable = removable or info.get("removable", False)
        drives.append(Drive(mp, os.path.basename(mp.rstrip("/")) or mp,
                            p.fstype, u.total, u.used, removable))
    return drives


# --------------------------------------------------------------------------
# Escaneo
# --------------------------------------------------------------------------
@dataclass
class RawFile:
    path: str
    size: int
    mtime: float
    drive: str

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].upper().lstrip(".")


@dataclass
class ScanResult:
    files: List[RawFile] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def by_format(self) -> collections.Counter:
        return collections.Counter(f.ext for f in self.files)

    def event_date(self) -> Optional[dt.date]:
        """Fecha más frecuente de las fotos (= día del evento)."""
        if not self.files:
            return None
        days = collections.Counter(
            dt.date.fromtimestamp(f.mtime) for f in self.files
        )
        return days.most_common(1)[0][0]

    def date_range(self):
        if not self.files:
            return None, None
        ts = [f.mtime for f in self.files]
        return dt.date.fromtimestamp(min(ts)), dt.date.fromtimestamp(max(ts))


EVENT_DIRS = ("1 original", "2 dngs", "3 imagen ai", "3 jpeg sin imagen", "4 fotos finales", "5 presentaci",
              "fotos buenas", "fotos malas",
              "3 fotos finales", "4 presentaci")      # carpetas de un evento de RawFlow (también las de antes de 4.15)


def is_event_dir(name: str) -> bool:
    """¿Es una de las carpetas de un evento ya hecho («1 Original», «2 DNGs»…)?"""
    import unicodedata
    n = unicodedata.normalize("NFC", name).lower()
    return any(n.startswith(e) for e in EVENT_DIRS)


def folder_drive(path) -> "Drive":
    """Una carpeta arrastrada a RawFlow: se trata como una memoria más (pero nunca se expulsa)."""
    p = Path(path)
    return Drive(mountpoint=str(p), label=p.name or str(p), fstype="carpeta", total=0, used=0, removable=False)


def scan_paths(paths, root: str, on_progress: Optional[Callable[[int, int], None]] = None,
               should_stop: Callable[[], bool] = lambda: False) -> List[RawFile]:
    """Fotos arrastradas sueltas (o carpetas): sus RAW, con `root` como su «memoria»."""
    found: List[RawFile] = []
    for x in paths:
        if should_stop():
            break
        if os.path.isdir(x):
            for f in scan_drive(x, None, should_stop, skip_events=True):
                found.append(RawFile(f.path, f.size, f.mtime, root))
        elif os.path.splitext(x)[1].lower() in RAW_EXTENSIONS and not os.path.basename(x).startswith("._"):
            try:
                st = os.stat(x)
            except OSError:
                continue
            if st.st_size:
                found.append(RawFile(str(x), st.st_size, st.st_mtime, root))
        if on_progress:
            on_progress(len(found), len(found))
    return found


def scan_drive(root: str,
               on_progress: Optional[Callable[[int, int], None]] = None,
               should_stop: Callable[[], bool] = lambda: False, skip_events: bool = False) -> List[RawFile]:
    """Recorre una unidad (o una carpeta arrastrada) y devuelve sus archivos RAW. `skip_events`: no entra en las
    carpetas de eventos ya hechos (así arrastrar el Escritorio nunca se lleva las fotos de otros eventos)."""
    found: List[RawFile] = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        if should_stop():
            break
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and not d.startswith(".")
                       and not (skip_events and is_event_dir(d))]
        for name in filenames:
            visited += 1
            if name.startswith("._"):  # basura de macOS en tarjetas FAT
                continue
            if os.path.splitext(name)[1].lower() in RAW_EXTENSIONS:
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size == 0:
                    continue
                found.append(RawFile(full, st.st_size, st.st_mtime, root))
        if on_progress and visited % 200 < len(filenames) + 1:
            on_progress(visited, len(found))
    if on_progress:
        on_progress(visited, len(found))
    return found


# --------------------------------------------------------------------------
# Carpeta del evento
# --------------------------------------------------------------------------
_BAD_CHARS = '<>:"/\\|?*'


def clean_name(text: str) -> str:
    text = "".join("-" if c in _BAD_CHARS else c for c in text).strip(" .")
    return " ".join(text.split())


MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def date_name(d: Optional[dt.date] = None) -> str:
    """19 nov 2026"""
    d = d or dt.date.today()
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def unique_folder(path: Path) -> Path:
    """Si ya existe una carpeta con ese nombre, devuelve «Nombre 2», «Nombre 3»…"""
    if not path.exists():
        return path
    n = 2
    while (path.parent / f"{path.name} {n}").exists():
        n += 1
    return path.parent / f"{path.name} {n}"


def event_folder(base: str, date: dt.date, event_name: str) -> Path:
    """<base>/<Nombre del evento>  (si no hay nombre: <base>/2026-11-18)"""
    clean = clean_name(event_name)
    return Path(base) / (clean or date.strftime("%Y-%m-%d"))


def default_base_dir() -> str:
    """Escritorio (ahí se crea la carpeta del evento). En Windows se pregunta al sistema dónde está de verdad
    (con OneDrive suele ser «OneDrive\\Escritorio», no «C:\\Users\\…\\Desktop»)."""
    if SYSTEM == "Windows":
        try:
            import ctypes
            import uuid
            guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                d = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                if d.exists():
                    return str(d)
        except Exception:  # noqa: BLE001
            pass
    desk = Path.home() / "Desktop"
    return str(desk if desk.exists() else Path.home())


# --------------------------------------------------------------------------
# Copia
# --------------------------------------------------------------------------
CHUNK = 8 * 1024 * 1024


@dataclass
class CopyStats:
    copied: int = 0
    skipped: int = 0
    removed: int = 0          # RAW borrados de la memoria (modo mover)
    failed: int = 0
    bytes_done: int = 0
    errors: List[str] = field(default_factory=list)


def _same_content(a: str, b: Path) -> bool:
    """¿Es exactamente la misma foto? (tamaño y bytes). Dos cámaras pueden dar el mismo nombre y,
    con RAW sin comprimir, también el mismo tamaño: comparar solo el tamaño borraría fotos distintas."""
    import filecmp
    try:
        return os.path.getsize(a) == b.stat().st_size and filecmp.cmp(a, str(b), shallow=False)
    except OSError:
        return False


def _unique_target(dest_dir: Path, name: str, size: int, src: str = "", taken: Optional[set] = None):
    """Devuelve (ruta, ya_existe_igual). Nunca pisa ni descarta una foto distinta:
      · mismo nombre y MISMO contenido (la misma memoria otra vez) → ya está, se salta;
      · mismo nombre (o mismo nombre base con otra extensión: DSC0001.ARW / DSC0001.CR3) y otra foto →
        DSC0001_1.ARW, DSC0001_2.ARW… (DxO, Imagen y las fotos finales usan el nombre base)."""
    stem, ext = os.path.splitext(name)
    taken = taken if taken is not None else set()
    existing = {}
    try:
        for p in dest_dir.iterdir():
            if not p.name.endswith(".part"):
                existing.setdefault(p.stem.lower(), []).append(p)
    except OSError:
        pass
    n = 0
    while True:
        cand = name if n == 0 else f"{stem}_{n}{ext}"
        cstem = os.path.splitext(cand)[0].lower()
        same_name = [p for p in existing.get(cstem, []) if p.name.lower() == cand.lower()]
        if same_name and src and _same_content(src, same_name[0]):
            return same_name[0], True
        if not same_name and cstem not in existing and cstem not in taken:
            return dest_dir / cand, False
        n += 1


def copy_files(files: Iterable[RawFile], dest_dir: Path,
               on_progress: Optional[Callable[[int, str], None]] = None,
               should_stop: Callable[[], bool] = lambda: False,
               move: bool = False) -> CopyStats:
    """Copia los RAW a dest_dir. Con move=True borra cada original de la memoria,
    pero solo cuando su copia está completa, en disco y con el mismo tamaño."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    def remove_source(f, name):
        try:
            os.remove(f.path)
            stats.removed += 1
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"{name}: copiado, pero no se pudo borrar de la memoria ({e})")

    stats = CopyStats()
    for f in files:
        if should_stop():
            break
        name = os.path.basename(f.path)
        target, same = _unique_target(dest_dir, name, f.size, f.path)
        if same:
            stats.skipped += 1
            stats.bytes_done += f.size
            if move:
                remove_source(f, name)
            if on_progress:
                on_progress(stats.bytes_done, name)
            continue
        tmp = target.with_name(target.name + ".part")
        try:
            with open(f.path, "rb") as src, open(tmp, "wb") as dst:
                while True:
                    if should_stop():
                        raise InterruptedError
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    stats.bytes_done += len(chunk)
                    if on_progress:
                        on_progress(stats.bytes_done, name)
                dst.flush()
                os.fsync(dst.fileno())          # que esté de verdad en el disco antes de borrar
            shutil.copystat(f.path, tmp)
            if os.path.getsize(tmp) != f.size:
                raise IOError("el tamaño no coincide tras copiar")
            os.replace(tmp, target)
            stats.copied += 1
            if move:
                remove_source(f, name)
        except InterruptedError:
            tmp.unlink(missing_ok=True)
            break
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            stats.failed += 1
            stats.errors.append(f"{name}: {e}")
    return stats


def move_files_parallel(files: List[RawFile], dest_dir: Path,
                        on_progress: Optional[Callable[[int, str], None]] = None,
                        should_stop: Callable[[], bool] = lambda: False,
                        move: bool = True) -> CopyStats:
    """Como copy_files, pero más rápido: copia nativa de macOS (fcopyfile) y una tarea
    por memoria, así dos tarjetas se leen a la vez. Cada original se borra solo cuando su
    copia está completa, sincronizada en disco y con el mismo tamaño."""
    import threading
    from concurrent.futures.thread import ThreadPoolExecutor   # directo: en Python 3.15 el otro es perezoso y falla
    dest_dir.mkdir(parents=True, exist_ok=True)
    stats = CopyStats()
    lock = threading.Lock()
    reserved: set = set()

    def pick(name: str, size: int, src: str):
        with lock:                               # reserva por nombre base: dos memorias a la vez no chocan
            target, same = _unique_target(dest_dir, name, size, src, reserved)
            if not same:
                reserved.add(target.stem.lower())
            return target, same

    def done(nbytes: int, name: str, kind: str, err: str = ""):
        with lock:
            stats.bytes_done += nbytes
            if kind == "copied":
                stats.copied += 1
            elif kind == "skipped":
                stats.skipped += 1
            elif kind == "failed":
                stats.failed += 1
                stats.errors.append(f"{name}: {err}")
            elif kind == "removed_err":
                stats.errors.append(f"{name}: copiado, pero no se pudo borrar de la memoria ({err})")
            if kind == "removed":
                stats.removed += 1
            b = stats.bytes_done
        if on_progress and kind in ("copied", "skipped", "failed"):
            on_progress(b, name)

    def remove(f: RawFile, name: str):
        if not move:
            return
        try:
            os.remove(f.path)
            done(0, name, "removed")
        except Exception as e:  # noqa: BLE001
            done(0, name, "removed_err", str(e))

    def one_drive(group: List[RawFile]):
        for f in group:
            if should_stop():
                return
            name = os.path.basename(f.path)
            target, same = pick(name, f.size, f.path)
            if same:
                done(f.size, name, "skipped")
                remove(f, name)
                continue
            tmp = target.with_name(target.name + ".part")
            try:
                shutil.copyfile(f.path, tmp)            # fcopyfile: copia en el kernel
                fd = os.open(tmp, os.O_RDWR if SYSTEM == "Windows" else os.O_RDONLY)  # Windows: fsync pide escritura
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                shutil.copystat(f.path, tmp)
                if os.path.getsize(tmp) != f.size:
                    raise IOError("el tamaño no coincide tras copiar")
                os.replace(tmp, target)
                done(f.size, name, "copied")
                remove(f, name)
            except Exception as e:  # noqa: BLE001
                Path(tmp).unlink(missing_ok=True)
                done(f.size, name, "failed", str(e))

    groups: Dict[str, List[RawFile]] = {}
    for f in files:
        groups.setdefault(f.drive, []).append(f)
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(groups)))) as ex:
        list(ex.map(one_drive, groups.values()))
    return stats


# --------------------------------------------------------------------------
# DxO
# --------------------------------------------------------------------------
def find_dxo_apps() -> List[str]:
    """Busca DxO PureRAW / PhotoLab instalados. Lo más nuevo primero."""
    found: List[str] = []
    if SYSTEM == "Darwin":
        patterns = [
            "/Applications/DxO PureRAW*.app",
            "/Applications/*/DxO PureRAW*.app",
            "/Applications/DxO PhotoLab*.app",
            "/Applications/*/DxO PhotoLab*.app",
            os.path.expanduser("~/Applications/DxO*.app"),
        ]
    elif SYSTEM == "Windows":
        pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
        patterns = []
        for base in {p for p in pf if p}:
            patterns += [
                os.path.join(base, "DxO", "*PureRAW*", "*PureRAW*.exe"),
                os.path.join(base, "DxO", "*PhotoLab*", "*PhotoLab*.exe"),
                os.path.join(base, "DxO Labs", "*", "*PureRAW*.exe"),
                os.path.join(base, "DxO Labs", "*", "*PhotoLab*.exe"),
            ]
    else:
        patterns = []
    for pat in patterns:
        for p in glob.glob(pat):
            low = os.path.basename(p).lower()
            if "uninstall" in low or "unins" in low or "helper" in low:
                continue
            if p not in found:
                found.append(p)
    found.sort(key=lambda p: ("pureraw" not in p.lower(), -_version_of(p)))
    return found


def _version_of(path: str) -> int:
    digits = "".join(c if c.isdigit() else " " for c in os.path.basename(path)).split()
    return int(digits[-1]) if digits else 0


def dxo_display_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def open_in_dxo(app_path: str, folder: Path) -> None:
    """Abre la carpeta del evento en DxO (PureRAW la mete en la cola directo)."""
    folder = str(folder)
    if SYSTEM == "Darwin":
        subprocess.Popen(["open", "-a", app_path, folder])
    elif SYSTEM == "Windows":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([app_path, folder], creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([app_path, folder])


def reveal_in_file_manager(folder: Path) -> None:
    if SYSTEM == "Darwin":                     # una ventana del Finder delante, grande y centrada
        p = str(folder).replace("\\", "\\\\").replace('"', '\\"')
        script = f'''
tell application "Finder"
  activate
  set w to make new Finder window to (POSIX file "{p}" as alias)
  set current view of w to icon view
  set b to bounds of window of desktop
  set W to item 3 of b
  set H to item 4 of b
  set bounds of w to {{(W - 1000) div 2, (H - 620) div 2, (W + 1000) div 2, (H + 620) div 2}}
end tell'''
        r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)
        if r.returncode != 0:
            subprocess.Popen(["open", str(folder)])
    elif SYSTEM == "Windows":
        os.startfile(str(folder))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def eject_drive(mountpoint: str) -> bool:
    try:
        if SYSTEM == "Darwin":
            import time as _t
            for i in range(4):                    # Spotlight u otra app puede tenerla ocupada un momento
                if not os.path.exists(mountpoint):
                    return True
                if subprocess.run(["diskutil", "eject", mountpoint], capture_output=True,
                                  timeout=60).returncode == 0:
                    return True
                _t.sleep(1.5 * (i + 1))
            return subprocess.run(["diskutil", "unmount", mountpoint], capture_output=True,
                                  timeout=60).returncode == 0
        if SYSTEM == "Windows":
            letter = mountpoint[:2]
            ps = ("(New-Object -comObject Shell.Application).Namespace(17)"
                  f".ParseName('{letter}').InvokeVerb('Eject')")
            return subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                  capture_output=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                  ).returncode == 0
        return subprocess.run(["udisksctl", "unmount", "-b", mountpoint],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def release_drive(mountpoint: str) -> Optional[str]:
    """Mac: deja la memoria lista para sacarla SIN expulsarla: la desmonta (todo escrito, nada abierto) pero el lector
    sigue viéndola, así RawFlow sabe cuándo la retiras de verdad (desaparece /dev/diskN; ver drive_present).
    Devuelve el disco que vigilar («disk4»), "" si ya no estaba montada, o None si no se pudo (sigue ocupada)."""
    import plistlib
    import time as _t
    if not os.path.exists(mountpoint):
        return ""
    whole = ""
    try:
        d = plistlib.loads(subprocess.run(["diskutil", "info", "-plist", mountpoint],
                                          capture_output=True, timeout=20).stdout)
        whole = str(d.get("ParentWholeDisk") or d.get("DeviceIdentifier") or "")
    except Exception:  # noqa: BLE001
        pass
    target = f"/dev/{whole}" if whole else mountpoint
    for i in range(4):                            # Spotlight u otra app puede tenerla ocupada un momento
        if not os.path.exists(mountpoint):
            return whole if whole and drive_present(whole) else ""
        cmd = ["diskutil", "unmountDisk", target] if whole else ["diskutil", "unmount", mountpoint]
        try:
            if subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0:
                return whole if whole and drive_present(whole) else ""
        except Exception:  # noqa: BLE001
            pass
        _t.sleep(1.5 * (i + 1))
    return None


def drive_present(disk: str) -> bool:
    """¿Sigue puesta la memoria «diskN»? (al sacarla, macOS borra /dev/diskN al instante)."""
    return bool(disk) and os.path.exists(f"/dev/{disk}")


def power_state():
    """(con cargador, % de batería, minutos que quedan con batería) en Mac. Sin batería o si no se sabe:
    (True, None, None)."""
    if SYSTEM == "Windows":                    # portátil con Windows: lo dice psutil (sin batería: None)
        try:
            b = psutil.sensors_battery()
        except Exception:  # noqa: BLE001
            b = None
        if b is None:
            return True, None, None
        secs = b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None
        return bool(b.power_plugged), int(round(b.percent)), (secs // 60 if secs and not b.power_plugged else None)
    if SYSTEM != "Darwin":
        return True, None, None
    import re
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return True, None, None
    first = out.splitlines()[0] if out else ""
    ac = "Battery Power" not in first
    m = re.search(r"(\d+)%", out)
    pct = int(m.group(1)) if m else None
    m = re.search(r"(\d+):(\d\d) remaining", out)
    mins = int(m.group(1)) * 60 + int(m.group(2)) if m and not ac and "discharging" in out else None
    return ac, pct, (mins or None)


def low_power_mode() -> bool:
    """¿Está el modo de bajo consumo (macOS frena CPU y GPU; en Windows, el plan «Ahorro de energía»)? RawFlow no
    puede quitarlo solo (pide contraseña de administrador): solo avisa."""
    import re
    try:
        if SYSTEM == "Darwin":
            out = subprocess.run(["pmset", "-g"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"^\s*lowpowermode\s+(\d)", out, re.M)
            return bool(m and m.group(1) == "1")
        if SYSTEM == "Windows":
            out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=5,
                                 creationflags=0x08000000).stdout.lower()
            return "a1841308-3541-4fab-bc81-f71556f20b4a" in out      # «Economizador / Ahorro de energía»
    except Exception:  # noqa: BLE001
        pass
    return False


_VOICE: list = []


def _spanish_voice() -> str:
    """Una voz en español de las instaladas en el Mac («» si no hay)."""
    if not _VOICE:
        import re
        v = ""
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=8).stdout
            names = [m.group(1).strip() for m in re.finditer(r"^(.*?)\s+(es_[A-Z]{2})\s+#", out, re.M)]
            for want in ("Paulina", "Mónica", "Monica", "Flo (Español (México))", "Eddy (Español (México))",
                         "Flo (Español (España))", "Eddy (Español (España))"):
                if want in names:
                    v = want
                    break
            if not v and names:
                v = names[0]
        except Exception:  # noqa: BLE001
            pass
        _VOICE.append(v)
    return _VOICE[0]


def alert(title: str, text: str, speak: str = ""):
    """Mac: sonido + notificación (+ voz en español si `speak`). Windows: sonido de alerta (+ voz). No espera a nada."""
    if SYSTEM == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            if speak:                          # la voz del sistema (en español si Windows está en español)
                q = speak.replace("'", "''")
                subprocess.Popen(["powershell", "-NoProfile", "-Command",
                                  "Add-Type -AssemblyName System.Speech; "
                                  f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{q}')"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
        return
    if SYSTEM != "Darwin":
        return
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')    # noqa: E731
        subprocess.Popen(["osascript", "-e", f'display notification "{q(text)}" with title "{q(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if speak:
            voice = _spanish_voice()
            subprocess.Popen(["say"] + (["-v", voice] if voice else []) + [speak],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------
# Procesado automático en DxO PureRAW con un preajuste (ej. CINEMA)
# --------------------------------------------------------------------------
# PureRAW no tiene línea de comandos oficial, pero acepta el mismo modo que usa
# su plugin de Lightroom: "--as-lightroom-last-settings-plugin --batch-file=…".
# En ese modo procesa SIN abrir la interfaz con el PRIMER preajuste de
# ProcessingPresets.json. Por eso RawFlow pone el preajuste elegido primero y le
# activa la subcarpeta de salida (DNG).
DEFAULT_PRESET = "CINEMA"
# Carpeta del evento: una carpeta numerada por paso, en el orden en que trabajan (el Finder las muestra así)
# los pasos, por nombre (su número es la posición): así añadir un paso no descoloca nada
STEP_MOVE, STEP_SEL, STEP_DXO, STEP_IMG, STEP_RET, STEP_PRES = range(6)
STEP_FINAL = 6                               # la revisión final (panel COMPROBADO), después del último paso

RAW_SUBFOLDER = "1 Original"                 # 1 Mover: los RAW
GOOD_SUBFOLDER = "Fotos buenas"              # 2 Selección IA: dentro de «1 Original», las que siguen el proceso
BAD_SUBFOLDER = "Fotos malas"                #   … y las apartadas (con su motivo en «motivos.txt»; nunca se borran)
OUTPUT_SUBFOLDER = "2 DNGs"                  # 2 DxO
IMAGEN_SUBFOLDER = "3 Imagen AI"             # 3 Imagen + Bridge: JPEG con las ediciones de Imagen
JPEG_SIN_SUBFOLDER = "3 JPEG sin Imagen"     # 3 si Imagen está desactivado (doble clic): Bridge tal cual
FINAL_SUBFOLDER = "4 Fotos finales"          # 4 Retouch4me retoca las de «3 Imagen AI»
PRES_SUBFOLDER = "5 Presentación de fotos"   # 5 CineSlide


def migrate_event_layout(ev) -> List[str]:
    """Eventos de antes de 4.15 («2 DNGs/Imagen AI», «3 Fotos finales», «4 Presentación de fotos») → carpetas 1-5
    en el orden de los pasos. Solo cambia carpetas de sitio (renombrar: instantáneo): no copia ni borra ninguna foto.
    Si ya existen las dos, junta lo que falte en la nueva. Devuelve lo que movió."""
    ev = Path(ev)
    moves = [(ev / "4 Presentación de fotos", ev / PRES_SUBFOLDER),          # primero las de número más alto
             (ev / "3 Fotos finales", ev / FINAL_SUBFOLDER),
             (ev / OUTPUT_SUBFOLDER / "Imagen AI", ev / IMAGEN_SUBFOLDER),
             (ev / OUTPUT_SUBFOLDER / "JPEG sin Imagen", ev / JPEG_SIN_SUBFOLDER)]
    done = []
    for old, new in moves:
        try:
            if not old.is_dir() or old.resolve() == new.resolve():
                continue
            if not new.exists():
                os.rename(old, new)
            else:
                for p in list(old.iterdir()):
                    if p.name == ".DS_Store":
                        p.unlink()
                    elif not (new / p.name).exists():
                        os.rename(p, new / p.name)
                try:
                    old.rmdir()
                except OSError:
                    pass
            done.append(f"«{old.relative_to(ev)}» → «{new.name}»")
        except OSError:
            pass
    return done


def is_pureraw(app_path: str) -> bool:
    return "pureraw" in os.path.basename(app_path).lower()


def pureraw_presets_file(app_path: str = "") -> Optional[Path]:
    ver = _version_of(app_path) or 6
    names = [f"DxO PureRAW {ver}", "DxO PureRAW 6"]
    roots: List[Path] = []
    if SYSTEM == "Darwin":
        roots = [Path.home() / "Library" / "DxO_Labs"]
    elif SYSTEM == "Windows":
        for env in ("LOCALAPPDATA", "APPDATA"):
            if os.environ.get(env):
                base = Path(os.environ[env])
                roots += [base / "DxO_Labs", base / "DxO Labs", base / "DxO"]
    for root in roots:
        for n in names:
            p = root / n / "ProcessingPresets.json"
            if p.exists():
                return p
    for root in roots:  # búsqueda más amplia
        hits = list(root.glob("*PureRAW*/ProcessingPresets.json")) if root.exists() else []
        if hits:
            return hits[0]
    return None


def list_pureraw_presets(app_path: str = "") -> List[str]:
    p = pureraw_presets_file(app_path)
    if not p:
        return []
    try:
        import json
        return [x.get("name", "") for x in json.loads(p.read_text(encoding="utf-8"))]
    except Exception:
        return []


def ensure_preset_first(app_path: str, preset: str = DEFAULT_PRESET,
                        subfolder: str = OUTPUT_SUBFOLDER, destination: Optional[Path] = None) -> str:
    """Pone el preajuste primero. Con `destination`, los DNG van a esa carpeta exacta
    (p. ej. <evento>/DNGs); si no, a <carpeta de los RAW>/<subfolder>.
    Devuelve el nombre exacto del preajuste. Lanza RuntimeError si no existe."""
    import json
    path = pureraw_presets_file(app_path)
    if not path:
        raise RuntimeError("No encontré los preajustes de PureRAW (ProcessingPresets.json).")
    data = json.loads(path.read_text(encoding="utf-8"))
    match = next((x for x in data if x.get("name", "").strip().lower() == preset.strip().lower()), None)
    if not match:
        names = ", ".join(x.get("name", "") for x in data)
        raise RuntimeError(f"El preajuste «{preset}» no existe en PureRAW. Hay: {names}")
    if destination is not None:
        Path(destination).mkdir(parents=True, exist_ok=True)
        wanted = {
            "SubfolderActivatedValue": False,
            "CustomDestinationFolderActivatedValue": True,
            "CustomDestinationFolderValue": str(destination),
            "DngOutputFormat": True,
        }
    else:
        wanted = {
            "SubfolderActivatedValue": bool(subfolder),
            "SubfolderValue": subfolder,
            "CustomDestinationFolderActivatedValue": False,
            "DngOutputFormat": True,
        }
    changed = data[0] is not match or any(match.get(k) != v for k, v in wanted.items())
    if changed:
        backup = path.with_name("ProcessingPresets.backup-antes-de-RawFlow.json")
        if not backup.exists():
            shutil.copy2(path, backup)
        match.update(wanted)
        data.remove(match)
        data.insert(0, match)
        tmp = path.with_suffix(".rawflow.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    return match["name"]


def _batch_dir() -> Path:
    # PureRAW (y su plugin) usan /private/tmp en Mac; en Windows el TEMP normal.
    if SYSTEM == "Darwin":
        return Path("/private/tmp")
    import tempfile
    return Path(tempfile.gettempdir())


def start_pureraw_batch(app_path: str, raw_paths: List[str]) -> Path:
    """Manda los RAW a PureRAW para que los procese solo. Devuelve el batch file."""
    import uuid
    batch = _batch_dir() / f"DxO-BatchFile-RawFlow-{uuid.uuid4().hex[:12]}.txt"
    batch.write_text("\n".join(raw_paths), encoding="utf-8")
    args = ["--as-lightroom-last-settings-plugin", "--lr-version=14.0", f"--batch-file={batch}"]
    if SYSTEM == "Darwin":
        subprocess.Popen(["open", "-n", "-g", "-a", app_path, "--args", *args])
    elif SYSTEM == "Windows":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([app_path, *args], creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([app_path, *args])
    return batch


_DNG_OK: dict = {}


def dng_complete(path) -> bool:
    """Igual que _dng_complete, pero recuerda los ya comprobados (mismo tamaño y fecha → mismo resultado)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    k = (str(path), st.st_size, st.st_mtime_ns)
    if k not in _DNG_OK:
        if len(_DNG_OK) > 20000:
            _DNG_OK.clear()
        _DNG_OK[k] = _dng_complete(path)
    return _DNG_OK[k]


def _dng_complete(path) -> bool:
    """¿El DNG está entero? Un DNG dice dónde están todos sus datos de imagen; si el archivo termina antes
    (DxO se cerró a mitad de escribirlo), está cortado y NO cuenta como hecho."""
    import struct
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(8)
        if head[:2] == b"II": e = "<"
        elif head[:2] == b"MM": e = ">"
        else: return False
        if struct.unpack(e + "H", head[2:4])[0] != 42: return False
        first = struct.unpack(e + "I", head[4:8])[0]
        need, seen, todo = 0, set(), [first]
        TS = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8}
        def vals(typ, cnt, raw_off):
            sz = TS.get(typ, 1) * cnt
            if sz <= 4: data = raw_off
            else:
                off = struct.unpack(e + "I", raw_off)[0]
                if off + sz > size: return None
                f.seek(off); data = f.read(sz)
            if typ == 3: return list(struct.unpack(e + "%dH" % cnt, data[:2 * cnt]))
            if typ in (4, 13): return list(struct.unpack(e + "%dI" % cnt, data[:4 * cnt]))
            return []
        while todo:
            off = todo.pop()
            if off in seen or off == 0 or off + 2 > size: continue
            seen.add(off)
            f.seek(off); n = struct.unpack(e + "H", f.read(2))[0]
            ents = f.read(12 * n)
            if len(ents) < 12 * n: return False
            tags = {}
            for i in range(n):
                tag, typ, cnt = struct.unpack(e + "HHI", ents[12 * i:12 * i + 8])
                tags[tag] = (typ, cnt, ents[12 * i + 8:12 * i + 12])
            nxt = f.read(4)
            if len(nxt) == 4: todo.append(struct.unpack(e + "I", nxt)[0])
            if 330 in tags:
                v = vals(*tags[330]); todo += v or []
            for o_tag, c_tag in ((273, 279), (324, 325)):
                if o_tag in tags and c_tag in tags:
                    offs, cnts = vals(*tags[o_tag]), vals(*tags[c_tag])
                    if offs is None or cnts is None: return False
                    for a, b in zip(offs, cnts): need = max(need, a + b)
        return need > 0 and need <= size


# --------------------------------------------------------------------------
# Eventos grandes por TANDAS: mientras DxO trabaja (GPU/Neural Engine), las fotos que ya tiene suben a Imagen por
# tandas (red). Así, al terminar DxO, Imagen ya tiene casi todo subido y editado. Medido con 480 NEF (65 MB por DNG):
# DxO ~10 s/foto, subida a Imagen ~5 s/foto (12 MB/s): la subida le sigue el paso a DxO.
# --------------------------------------------------------------------------
TANDA_PREFIX = "Tanda "


def tanda_size(n: int) -> int:
    """Fotos por tanda para un evento de `n` fotos; 0 = sin tandas (todo seguido, como siempre).
    RAWFLOW_TANDAS=0 las desactiva; RAWFLOW_TANDA=k fuerza tandas de k fotos (pruebas)."""
    import math
    if os.environ.get("RAWFLOW_TANDAS", "1") == "0":
        return 0
    forced = os.environ.get("RAWFLOW_TANDA", "") or os.environ.get("RAWFLOW_TANDA1", "")
    if forced.isdigit() and 0 < int(forced) < n:
        return int(forced)
    if n < 60:
        return 0
    return max(30, math.ceil(n / 5))


def dng_base(p) -> str:
    """«DSC01592-DxO_DeepPRIME XD3.dng» → «dsc01592» (el nombre de la foto, igual que el RAW y el JPEG)."""
    return Path(p).stem.split("-DxO")[0].lower()


def jpg_stems(folder: Path) -> set:
    """Nombres (sin extensión, en minúsculas) de los JPEG visibles de una carpeta."""
    if not Path(folder).is_dir():
        return set()
    return {p.stem.lower() for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg") and not p.name.startswith(".")}


# ---- candado de Imagen: ninguna foto sigue (ni a Retouch4me ni a las finales) sin las ediciones de Imagen
IMAGEN_MARK = b"crs:Temperature"     # Imagen SIEMPRE fija el balance de blancos y Camera Raw lo guarda así: dentro
                                      # del DNG (XMP) y en los metadatos del JPEG que exporta Bridge. Un DNG de DxO
                                      # sin Imagen, o un JPEG exportado sin Imagen, no lo llevan (medido: 1.077 fotos)


def _xmp_blocks(p: Path) -> Optional[List[bytes]]:
    """XMP de un DNG/TIFF (etiqueta 700 del primer IFD) o de un JPEG (segmentos APP1). None = no se pudo leer
    la estructura (entonces se busca en el archivo entero)."""
    import struct
    try:
        with open(p, "rb") as f:
            head = f.read(16)
            if head[:2] in (b"II", b"MM"):                         # DNG / TIFF
                e = "<" if head[:2] == b"II" else ">"
                magic, off = struct.unpack(e + "HI", head[2:8])
                if magic != 42:
                    return None
                f.seek(off)
                n = struct.unpack(e + "H", f.read(2))[0]
                if not 0 < n < 1000:
                    return None
                for _ in range(n):
                    tag, typ, cnt, val = struct.unpack(e + "HHII", f.read(12))
                    if tag == 700:
                        if cnt <= 4 or cnt > 64_000_000:
                            return None
                        f.seek(val)
                        return [f.read(cnt)]
                return []
            if head[:2] == b"\xff\xd8":                              # JPEG: APP1 hasta el comienzo de la imagen
                f.seek(2)
                out = []
                while True:
                    m = f.read(4)
                    if len(m) < 4 or m[0] != 0xFF:
                        return out if out else None
                    kind, size = m[1], struct.unpack(">H", m[2:4])[0]
                    if kind == 0xDA:                                 # empieza la imagen: ya no hay más metadatos
                        return out
                    data = f.read(max(0, size - 2))
                    if kind == 0xE1 and b"ns.adobe.com/x" in data[:64]:
                        out.append(data)
    except (OSError, struct.error):
        return None
    return None


def has_imagen_edits(p) -> bool:
    """¿Lleva esta foto (DNG de «2 DNGs» o JPEG de «Imagen AI» / «Fotos finales») las ediciones de Imagen?
    Lee solo sus metadatos (milisegundos); si no se entiende su estructura, busca en el archivo entero."""
    p = Path(p)
    side = p.with_suffix(".xmp")                                     # Imagen por clave API: .xmp al lado del DNG
    try:
        if side.exists() and IMAGEN_MARK in side.read_bytes():
            return True
    except OSError:
        pass
    blocks = _xmp_blocks(p)
    if blocks is not None:
        return any(IMAGEN_MARK in b for b in blocks)
    try:
        with open(p, "rb") as f:
            tail = b""
            while True:
                chunk = f.read(1_000_000)
                if not chunk:
                    return False
                if IMAGEN_MARK in tail + chunk:
                    return True
                tail = chunk[-32:]
    except OSError:
        return False


def without_imagen(paths) -> List[Path]:
    """Las que NO llevan las ediciones de Imagen."""
    return [Path(p) for p in paths if not has_imagen_edits(p)]


def source_stem(p) -> str:
    """De qué foto viene un JPEG ya numerado («37.jpg»): Camera Raw deja en sus metadatos el nombre del RAW original
    (xmpMM:PreservedFileName) y el del DNG (crs:RawFileName). «EDN_4441.NEF» → «edn_4441». '' si no lo dice."""
    import re
    blocks = _xmp_blocks(Path(p)) or []
    data = b"".join(blocks)
    for key in (rb"PreservedFileName", rb"RawFileName"):
        m = re.search(key + rb'(?:="|>)([^"<]+)', data)
        if m:
            name = m.group(1).decode("utf-8", "replace").strip()
            return dng_base(Path(name)).rsplit(".", 1)[0].lower() if name else ""
    return ""


def processed_stems(out_dir: Path) -> set:
    """Nombres base de RAW que ya tienen su DNG procesado (DSC0001-DxO_….dng)."""
    done = set()
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            if p.suffix.lower() == ".dng" and "-DxO" in p.stem and dng_complete(p):
                done.add(p.stem.split("-DxO")[0].lower())
    return done


def files_settled(paths, wait: float = 0.6) -> bool:
    """True si ninguno de los archivos cambia de tamaño en `wait` segundos (ya se terminaron de escribir)."""
    import time as _t
    def sizes():
        out = {}
        for p in paths:
            try:
                out[str(p)] = os.path.getsize(p)
            except OSError:
                out[str(p)] = -1
        return out
    a = sizes()
    _t.sleep(wait)
    return a == sizes() and all(v > 0 for v in a.values())


def remove_broken_dngs(out_dir: Path) -> List[str]:
    """Borra los DNG cortados (para que DxO los vuelva a hacer). Devuelve sus nombres."""
    gone = []
    if out_dir.is_dir():
        for p in out_dir.glob("*.dng"):
            try:
                if not dng_complete(p):
                    p.unlink()
                    gone.append(p.name)
            except OSError:
                pass
    return gone


def collect_dxo_dngs(event_dir: Path, subfolder: str = OUTPUT_SUBFOLDER) -> int:
    """Windows: PureRAW, lanzado en modo plugin, deja cada DNG JUNTO a su RAW e ignora la carpeta de destino del
    preajuste y la de sus ajustes guardados (medido el 23-nov-2026 con PureRAW 6: los 5 DNG salieron en «1 Original» y
    RawFlow esperaba en «2 DNGs» hasta dar «DxO no empezó»). Aquí se pasan a «2 DNGs» los que ya están completos; uno
    que PureRAW aún escribe no se deja mover (Windows lo tiene abierto) y entra en la siguiente vuelta.
    Solo se toca un «X-DxO….dng» que tiene su RAW «X» al lado: un DNG que venía así de la cámara o de la memoria se queda.
    Devuelve cuántos movió. En Mac no hace nada (allí PureRAW sí respeta el destino)."""
    if SYSTEM != "Windows":
        return 0
    out_dir = Path(event_dir) / subfolder
    moved = 0
    for d in (raw_dir(event_dir), raw_dir(event_dir) / GOOD_SUBFOLDER):
        if not d.is_dir():
            continue
        try:
            files = [p for p in d.iterdir() if p.is_file()]
        except OSError:
            continue
        fuentes = {p.stem.lower() for p in files if p.suffix.lower() in RAW_EXTENSIONS and "-DxO" not in p.stem}
        for p in files:
            if p.suffix.lower() != ".dng" or "-DxO" not in p.stem or dng_base(p) not in fuentes:
                continue
            try:
                if not dng_complete(p):
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                os.replace(p, out_dir / p.name)
                moved += 1
            except OSError:
                continue                # PureRAW aún lo tiene abierto: en la siguiente vuelta
    return moved

def raw_dir(event_dir: Path) -> Path:
    """Donde están los RAW del evento: <evento>/Original (o la propia carpeta si es antigua)."""
    sub = event_dir / RAW_SUBFOLDER
    return sub if sub.is_dir() else event_dir


APARTADAS = ".rawflow_apartadas.json"     # en «2 DNGs»: fotos que un paso no pudo hacer tras todos sus reintentos


def apartadas(event_dir: Path) -> Dict[str, str]:
    """{nombre base en minúsculas: motivo} de las fotos apartadas del evento (el resto sigue sin ellas)."""
    import json
    try:
        d = json.loads((Path(event_dir) / OUTPUT_SUBFOLDER / APARTADAS).read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def apartar(event_dir: Path, stems: Iterable[str], motivo: str) -> None:
    """Aparta esas fotos del resto del evento (se guarda al momento: un reintento tampoco se atasca con ellas)."""
    import json
    d = apartadas(event_dir)
    for s in stems:
        d.setdefault(str(s).lower(), motivo)
    f = Path(event_dir) / OUTPUT_SUBFOLDER / APARTADAS
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def good_raws(event_dir: Path, exts: Optional[set] = None) -> List[Path]:
    """Los RAW que siguen el proceso: los de «1 Original» (aún sin separar, o de antes de la Selección IA) y los de
    «1 Original/Fotos buenas». Nunca los de «Fotos malas»."""
    exts = exts or RAW_EXTENSIONS
    out: List[Path] = []
    for d in (raw_dir(event_dir), raw_dir(event_dir) / GOOD_SUBFOLDER):
        if d.is_dir():
            out += [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")]
    return sorted(out, key=lambda p: p.name)


def pending_raws(event_dir: Path, subfolder: str = OUTPUT_SUBFOLDER, exts: Optional[set] = None) -> List[str]:
    done = processed_stems(event_dir / subfolder)
    skip = apartadas(event_dir)
    return [str(p) for p in good_raws(event_dir, exts) if p.stem.lower() not in done and p.stem.lower() not in skip]


def kill_pureraw_batches() -> None:
    """Cierra cualquier PureRAW que RawFlow dejara procesando un lote (por un corte o un reintento). Medido: si hay
    uno vivo, PureRAW abre el lote nuevo con la lista VACÍA y no procesa nada, sin avisar."""
    if SYSTEM == "Windows":
        try:
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*DxO-BatchFile-RawFlow*' "
                  "-and $_.Name -like 'PureRAW*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        return
    if SYSTEM != "Darwin":
        return
    try:
        if subprocess.run(["pgrep", "-f", "DxO-BatchFile-RawFlow"], capture_output=True).returncode == 0:
            subprocess.run(["pkill", "-f", "DxO-BatchFile-RawFlow"], capture_output=True)
            import time as _t
            for _ in range(20):
                if subprocess.run(["pgrep", "-f", "DxO-BatchFile-RawFlow"], capture_output=True).returncode != 0:
                    break
                _t.sleep(0.25)
    except Exception:
        pass


def close_pureraw_helper(batch: Path) -> None:
    """Cierra la ventanita «¡Listo!» que deja el modo plugin al terminar."""
    try:
        if SYSTEM == "Darwin":
            subprocess.run(["pkill", "-f", batch.name], capture_output=True)
        elif SYSTEM == "Windows":
            ps = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{batch.name}*' }}"
                  " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass

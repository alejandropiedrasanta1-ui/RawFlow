"""Dónde están los programas que usa RawFlow, en Mac y en Windows.

En un Windows (o un Mac) con los programas ya instalados, RawFlow los encuentra solo: busca en las carpetas donde los
dejan sus instaladores (Archivos de programa, Archivos de programa (x86), AppData\\Local\\Programs y Aplicaciones).
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import List, Optional

from . import core

WIN = core.SYSTEM == "Windows"
MAC = core.SYSTEM == "Darwin"


def _program_dirs() -> List[str]:
    """Carpetas de programas de Windows (sin repetir): 64 bits, 32 bits y las de usuario."""
    out: List[str] = []
    for k in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        v = os.environ.get(k, "")
        if v and v not in out:
            out.append(v)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        out.append(os.path.join(local, "Programs"))
    return out


def _first(patterns: List[str]) -> Optional[Path]:
    for pat in patterns:
        for p in sorted(glob.glob(pat), reverse=True):        # la versión más nueva primero
            low = os.path.basename(p).lower()
            if "unins" in low or "uninstall" in low or "helper" in low or "update" in low:
                continue
            if os.path.exists(p):
                return Path(p)
    return None


def dxo() -> str:
    apps = [a for a in core.find_dxo_apps() if core.is_pureraw(a)]
    return apps[0] if apps else ""


def imagen() -> Optional[Path]:
    if MAC:
        p = Path("/Applications/ImagenAI.app")
        return p if p.exists() else None
    if WIN:
        pats = []
        for d in _program_dirs():
            pats += [os.path.join(d, "*magen*", "ImagenAI.exe"), os.path.join(d, "*magen*", "Imagen*.exe")]
        return _first(pats)
    return None


def bridge() -> Optional[Path]:
    if MAC:
        hits = sorted(Path("/Applications").glob("Adobe Bridge*/Adobe Bridge*.app"), reverse=True)
        return hits[0] if hits else None
    if WIN:
        pats = []
        for d in _program_dirs():
            pats += [os.path.join(d, "Adobe", "Adobe Bridge*", "Adobe Bridge.exe"),
                     os.path.join(d, "Adobe", "Adobe Bridge*", "Bridge.exe")]
        return _first(pats)
    return None


def arams() -> Optional[Path]:
    if MAC:
        p = Path("/Applications/Retouch4me Arams.app")
        return p if p.exists() else None
    if WIN:
        pats = []
        for d in _program_dirs():
            pats += [os.path.join(d, "Retouch4me Arams", "Retouch4me Arams.exe"),
                     os.path.join(d, "*arams*", "Retouch4me Arams.exe"),
                     os.path.join(d, "Retouch4me*", "*Arams*.exe")]
        return _first(pats)
    return None


def cineslide_dir() -> Optional[Path]:
    """Carpeta del programa CineSlide (la que tiene su propio Python)."""
    if MAC:
        d = Path.home() / "Library/Application Support/CineSlide"
        return d if (d / "python/bin/python3").exists() else None
    if WIN:
        for k in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
            v = os.environ.get(k, "")
            if v and (Path(v) / "CineSlide" / "python" / "python.exe").exists():
                return Path(v) / "CineSlide"
    return None


def cineslide_python() -> Optional[Path]:
    d = cineslide_dir()
    if not d:
        return None
    p = d / ("python/bin/python3" if MAC else "python/python.exe")
    return p if p.exists() else None


def cineslide_data() -> Optional[Path]:
    d = cineslide_dir()
    return (d / "datos") if d else None


def found() -> dict:
    """{nombre: ruta o ""} de todos los programas."""
    return {"DxO PureRAW": dxo(), "Imagen AI": str(imagen() or ""), "Adobe Bridge": str(bridge() or ""),
            "Retouch4me Arams": str(arams() or ""), "CineSlide": str(cineslide_dir() or "")}


def missing(api_key: bool = False) -> List[str]:
    """Programas que faltan (Imagen no hace falta con clave de API; Bridge solo hace falta si se usa la app de Imagen)."""
    f = found()
    out = [n for n in ("DxO PureRAW", "Retouch4me Arams", "CineSlide") if not f[n]]
    if not f["Imagen AI"] and not api_key:
        out.insert(1, "Imagen AI")
    if f["Imagen AI"] and not api_key and not f["Adobe Bridge"]:
        out.append("Adobe Bridge")
    return out

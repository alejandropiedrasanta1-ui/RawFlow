"""Actualizaciones de RawFlow desde GitHub.

RawFlow mira la última versión publicada en https://github.com/<REPO>/releases y, si es más nueva que la instalada,
ofrece actualizar: descarga el instalador de su sistema (Windows: «…Windows-Instalador.exe»; Mac: «…Mac.zip», que
lleva «Instalar RawFlow.app»), comprueba que llegó entero y lo abre. El instalador cierra RawFlow, lo reemplaza y lo
vuelve a abrir; los ajustes y los eventos no se tocan.

Solo se pregunta a GitHub por la última versión (una petición pública, sin cuenta ni datos del equipo). Nunca se
actualiza con un evento en marcha. `RAWFLOW_SIN_ACTUALIZAR=1` lo desactiva.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

REPO = "alejandropiedrasanta1-ui/RawFlow"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
HOSTS = ("https://github.com/", "https://objects.githubusercontent.com/", "https://release-assets.githubusercontent.com/")


class UpdateError(Exception):
    pass


def enabled() -> bool:
    return os.environ.get("RAWFLOW_SIN_ACTUALIZAR") != "1" and os.environ.get("RAWFLOW_SIN_APPS") != "1"


def version_tuple(v: str) -> Tuple[int, ...]:
    """«v4.19», «4.19.1» → (4, 19) / (4, 19, 1). Lo que no sea número se ignora."""
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:4])


def newer(remote: str, local: str) -> bool:
    a, b = version_tuple(remote), version_tuple(local)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _asset_for_this_system(assets: list) -> Optional[dict]:
    for a in assets:
        name = str(a.get("name", "")).lower()
        if sys.platform == "win32" and name.endswith(".exe") and "windows" in name:
            return a
        if sys.platform == "darwin" and name.endswith(".zip") and "mac" in name:
            return a
    return None


def check(current: str, timeout: float = 8.0) -> Optional[dict]:
    """La última versión publicada, si es más nueva que `current` y trae instalador para este sistema:
    {"version", "name", "url", "size", "sha256", "notes"}. None si no hay nada nuevo (o no hay red)."""
    if not enabled():
        return None
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "RawFlow"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tag = str(data.get("tag_name", ""))
    if data.get("draft") or data.get("prerelease") or not newer(tag, current):
        return None
    a = _asset_for_this_system(data.get("assets") or [])
    if not a:
        return None
    url = str(a.get("browser_download_url", ""))
    if not url.startswith("https://github.com/" + REPO + "/"):
        return None
    digest = str(a.get("digest") or "")
    return {"version": ".".join(str(x) for x in version_tuple(tag)), "name": Path(str(a.get("name"))).name, "url": url,
            "size": int(a.get("size") or 0), "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
            "notes": str(data.get("body") or "").strip()}


def download(info: dict, progress: Callable[[float], None] = lambda f: None,
             should_stop: Callable[[], bool] = lambda: False) -> Path:
    """Baja el instalador a una carpeta temporal y comprueba tamaño y huella (SHA-256) contra lo que dice GitHub."""
    folder = Path(tempfile.gettempdir()) / "RawFlow-actualizacion"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        try:
            if old.is_file():
                old.unlink()
        except OSError:
            pass
    dest = folder / info["name"]
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(info["url"], headers={"User-Agent": "RawFlow"})
    h = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            if not str(r.geturl()).startswith(HOSTS):
                raise UpdateError("La descarga no viene de GitHub: no se instala.")
            while True:
                if should_stop():
                    raise UpdateError("Cancelado.")
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if info.get("size"):
                    progress(min(1.0, got / info["size"]))
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"No se pudo descargar la actualización ({e}).")
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
    if info.get("size") and got != info["size"]:
        tmp.unlink(missing_ok=True)
        raise UpdateError("La actualización llegó incompleta. Inténtalo otra vez.")
    if info.get("sha256") and h.hexdigest().lower() != info["sha256"].lower():
        tmp.unlink(missing_ok=True)
        raise UpdateError("La actualización no coincide con la publicada (huella distinta): no se instala.")
    os.replace(tmp, dest)
    return dest


def install(path: Path) -> None:
    """Abre el instalador descargado. Quien llama debe cerrar RawFlow justo después: el instalador lo reemplaza y lo
    vuelve a abrir."""
    path = Path(path)
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], creationflags=flags,
                         close_fds=True)
        return
    if sys.platform == "darwin":
        out = path.parent / "instalador"
        subprocess.run(["rm", "-rf", str(out)], check=False)
        r = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(path), str(out)], capture_output=True, text=True)
        apps = sorted(out.glob("*.app"))
        if r.returncode != 0 or not apps:
            raise UpdateError("No se pudo abrir el instalador descargado.")
        subprocess.Popen(["/usr/bin/open", str(apps[0])])
        return
    raise UpdateError("Las actualizaciones automáticas son para Windows y Mac.")

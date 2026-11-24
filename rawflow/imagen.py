"""Paso 3 de RawFlow: editar los DNG con Imagen AI (API oficial) y guardar los JPEG finales.

Mismo flujo que el video de la app ImagenAI, pero sin clics:
  crear proyecto → subir los DNG de <evento>/DNGs → editar con tu perfil (p. ej. CLEAN & CRISP,
  tipo Bodas, recortar/enderezar/máscara de sujeto) → exportar a JPEG → descargar a
  <evento>/Fotos finales.  Además guarda los .xmp de Imagen junto a los DNG, para abrirlos
  en Bridge / Camera Raw con la edición aplicada.

Usa la API documentada en https://api-docs.imagen-ai.com (la misma del SDK oficial
imagen-ai-sdk), con la biblioteca estándar de Python: no requiere dependencias.
La clave API se guarda en el Llavero de macOS (en Windows, cifrada con DPAPI), nunca en texto plano.
Imagen trabaja en la nube: este paso necesita internet.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

BASE_URL = "https://api.imagen-ai.com/v1"
FINAL_SUBFOLDER = "4 Fotos finales"
KEYCHAIN_SERVICE = "RawFlow-ImagenAI"
UA = "RawFlow/1.2 (Imagen API)"

PHOTO_TYPES = {  # texto en la app -> valor de la API
    "Bodas": "WEDDING", "Eventos": "EVENTS", "Retratos": "PORTRAITS",
    "Familia / Recién nacido": "FAMILY_NEWBORN", "Escuela": "SCHOOL", "Deportes": "SPORTS",
    "Boudoir": "BOUDOIR", "Paisaje / Naturaleza": "LANDSCAPE_NATURE",
    "Inmobiliaria": "REAL_ESTATE", "Otro": "OTHER",
}
DEFAULT_PROFILE = "CLEAN & CRISP"
DEFAULT_TYPE = "Bodas"
# Herramientas del video que la API admite (dientes/fondo/reflejos no están en la API)
DEFAULT_TOOLS = {"crop": True, "straighten": True, "subject_mask": True}

Log = Callable[[str], None]


class ImagenError(Exception):
    pass


# --------------------------------------------------------------------------
# Clave API en el Llavero
# --------------------------------------------------------------------------
def _key_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "RawFlow"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Cifra / descifra con la protección de datos de Windows (DPAPI): solo tu usuario de Windows, en esta PC,
    puede leerlo. Es el equivalente al Llavero del Mac; la clave nunca queda en texto plano."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "DPAPI no pudo " + ("cifrar" if protect else "descifrar"))
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def get_api_key() -> str:
    if sys.platform == "win32":               # Windows: variable de entorno, o la clave cifrada con DPAPI
        k = os.environ.get("IMAGEN_API_KEY", "").strip()
        if k:
            return k
        dat, txt = _key_dir() / "imagen_key.dat", _key_dir() / "imagen_key.txt"
        try:
            if dat.exists():
                return _dpapi(dat.read_bytes(), False).decode("utf-8").strip()
            if txt.exists():                  # de versiones anteriores (texto plano): se cifra y se borra
                k = txt.read_text(encoding="utf-8", errors="ignore").strip()
                if k:
                    set_api_key(k)
                return k
        except Exception:  # noqa: BLE001
            pass
        return ""
    if sys.platform != "darwin":              # Linux: variable de entorno o archivo de ajustes
        k = os.environ.get("IMAGEN_API_KEY", "")
        f = Path(os.environ.get("APPDATA", Path.home())) / "RawFlow" / "imagen_key.txt"
        return k or (f.read_text().strip() if f.exists() else "")
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "api", "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def set_api_key(key: str) -> None:
    key = key.strip()
    if sys.platform == "win32":
        dat, txt = _key_dir() / "imagen_key.dat", _key_dir() / "imagen_key.txt"
        if not key:
            txt.unlink(missing_ok=True)
            dat.unlink(missing_ok=True)
            return
        try:
            dat.parent.mkdir(parents=True, exist_ok=True)
            tmp = dat.with_suffix(".tmp")
            tmp.write_bytes(_dpapi(key.encode("utf-8"), True))
            os.replace(tmp, dat)
        except OSError as e:
            raise ImagenError(f"No pude guardar la clave en esta PC: {e}")
        txt.unlink(missing_ok=True)           # la de texto plano se borra cuando la cifrada YA está guardada
        return
    if sys.platform != "darwin":
        f = Path(os.environ.get("APPDATA", Path.home())) / "RawFlow" / "imagen_key.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(key)
        return
    if not key:
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "api"],
                       capture_output=True)
        return
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", "api",
                        "-l", "RawFlow · clave de Imagen AI", "-w", key], capture_output=True, text=True)
    if r.returncode != 0:
        raise ImagenError(f"No pude guardar la clave en el Llavero: {r.stderr.strip()}")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Client:
    def __init__(self, api_key: str, base_url: str = BASE_URL):
        if not api_key:
            raise ImagenError("Falta la clave API de Imagen («Configurar Imagen»).")
        self.key, self.base = api_key, base_url.rstrip("/")

    def _req(self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None,
             content_type: Optional[str] = "application/json", retries: int = 3):
        url = f"{self.base}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"x-api-key": self.key, "User-Agent": UA})
            if data is not None:
                # content_type=None → cabecera vacía (lo que pide /edit); si no, urllib pondría form-urlencoded
                req.add_header("Content-Type", content_type or "")
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                    return _unwrap(json.loads(raw)) if raw else {}
            except urllib.error.HTTPError as e:
                msg = _error_message(e)
                if e.code == 401:
                    raise ImagenError("Imagen rechazó la clave API (401). Revísala en «Configurar Imagen».")
                if e.code >= 500 and attempt < retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise ImagenError(f"Imagen respondió {e.code}: {msg}")
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                # (también los cortes a media respuesta —tiempo agotado, conexión reiniciada—, que urllib no envuelve:
                # salían como un error cualquiera y el paso entero se repetía, creando OTRO proyecto en Imagen)
                if attempt < retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise ImagenError(f"Sin conexión con Imagen ({getattr(e, 'reason', None) or e}). "
                                  "Este paso necesita internet.")

    # ---- API
    def profiles(self) -> List[dict]:
        r = self._req("GET", "/profiles")
        return r if isinstance(r, list) else r.get("profiles", [])

    def create_project(self, name: str) -> str:
        return self._req("POST", "/projects/", {"name": name})["project_uuid"]

    def upload_links(self, uuid: str, names: List[str]) -> Dict[str, str]:
        r = self._req("POST", f"/projects/{uuid}/get_temporary_upload_links",
                      {"files_list": [{"file_name": n} for n in names]})
        return {f["file_name"]: f["upload_link"] for f in r["files_list"]}

    def start_edit(self, uuid: str, profile_key: int, photo_type: str, tools: dict):
        body = {"profile_key": int(profile_key), "photography_type": photo_type, **tools}
        # La API pide el POST de /edit sin cabecera Content-Type (igual que el SDK oficial)
        self._req("POST", f"/projects/{uuid}/edit", body, content_type=None)

    def status(self, uuid: str, op: str) -> dict:
        return self._req("GET", f"/projects/{uuid}/{op}/status")

    def start_export(self, uuid: str):
        self._req("POST", f"/projects/{uuid}/export")

    def links(self, uuid: str, op: str) -> List[dict]:
        path = "edit/get_temporary_download_links" if op == "edit" else "export/get_temporary_download_links"
        return self._req("GET", f"/projects/{uuid}/{path}")["files_list"]


def _unwrap(p):
    return p["data"] if isinstance(p, dict) and set(p) == {"data"} else p


def _error_message(e: urllib.error.HTTPError) -> str:
    try:
        d = json.loads(e.read() or b"{}")
        err = d.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        return str(err or d.get("detail") or d)
    except Exception:  # noqa: BLE001
        return e.reason


def _safe_url(url: str) -> str:
    """Codifica espacios y otros caracteres sin tocar el %-encoding que ya traiga (URLs firmadas)."""
    return urllib.parse.quote(url, safe=":/?&=%+@,;~#!$'()*[]")


def _put_file(url: str, path: Path):
    url = _safe_url(url)
    size = path.stat().st_size
    with open(path, "rb") as f:
        req = urllib.request.Request(url, data=f, method="PUT", headers={"Content-Length": str(size)})
        for attempt in range(3):
            try:
                f.seek(0)
                with urllib.request.urlopen(req, timeout=600) as r:
                    r.read()
                return
            except (urllib.error.URLError, OSError):
                if attempt == 2:
                    raise
                time.sleep(4 * (attempt + 1))


def _download(url: str, dest: Path):
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(3):                  # un corte al bajar UNA foto no tira el paso (con la edición ya hecha)
        try:
            with urllib.request.urlopen(urllib.request.Request(_safe_url(url), headers={"User-Agent": UA}),
                                        timeout=600) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
            return
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt == 2:
                raise
            time.sleep(4 * (attempt + 1))


API_MARK = ".rawflow_imagen_api.json"       # en «2 DNGs»: el proyecto de Imagen (por API) que este evento ya envió


def _read_mark(f: Path) -> dict:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("uuid") else {}
    except (OSError, ValueError):
        return {}


def _write_mark(f: Path, d: dict) -> None:
    try:
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def _name_from(link: dict, url_key: str = "download_link") -> str:
    name = link.get("file_name") or Path(urllib.parse.urlparse(link[url_key]).path).name
    return Path(name).name


def final_name(name: str, default_ext: str) -> str:
    """«DSC01592-DxO_DeepPRIME XD3.jpg» → «DSC01592.jpg» (nombre limpio para entregar)."""
    p = Path(name)
    return p.stem.split("-DxO")[0] + (p.suffix or default_ext)


def find_profile(profiles: List[dict], name: str) -> dict:
    want = name.strip().lower()
    for p in profiles:
        if p.get("profile_name", "").strip().lower() == want:
            return p
    names = ", ".join(p.get("profile_name", "?") for p in profiles) or "ninguno"
    raise ImagenError(f"No encuentro el perfil «{name}» en tu cuenta de Imagen. Perfiles disponibles: {names}")


# --------------------------------------------------------------------------
# Todo el paso 3
# --------------------------------------------------------------------------
def edit_event(event_dir: Path, dng_subfolder: str, log: Log, progress: Callable[[float], None] = lambda f: None,
               profile_name: str = DEFAULT_PROFILE, photo_type: str = DEFAULT_TYPE,
               tools: Optional[dict] = None, should_stop=lambda: False, api_key: str = "",
               out_dir: Optional[Path] = None) -> Path:
    """Sube <evento>/<dng_subfolder>/*.dng a Imagen, edita, exporta JPEG a <evento>/Fotos finales."""
    dng_dir = event_dir / dng_subfolder
    dngs = sorted(p for p in dng_dir.glob("*") if p.suffix.lower() == ".dng") if dng_dir.is_dir() else []
    if not dngs:
        raise ImagenError(f"No hay DNG en «{dng_dir}».")
    out = Path(out_dir) if out_dir else event_dir / FINAL_SUBFOLDER
    have = {p.stem.lower() for p in out.glob("*.jp*g")} if out.is_dir() else set()
    todo = [p for p in dngs if final_name(p.name, ".jpg")[:-4].lower() not in have]
    if not todo:
        log("Imagen: todas las fotos ya están en «Fotos finales».")
        return out

    # Pruebas: la API de Imagen COBRA por foto. Con estas variables (las ponen las pruebas de RawFlow) jamás se llama,
    # aunque en este equipo haya una clave guardada.
    if os.environ.get("RAWFLOW_SIN_APPS") == "1" or os.environ.get("RAWFLOW_PRUEBA_SIN_COBRO") == "1":
        raise ImagenError("Pruebas: no se llama a la API de Imagen (cobra por foto).")

    c = Client(api_key or get_api_key())
    # ¿Este evento ya envió un proyecto con estas fotos y la edición quedó pedida? Entonces se RETOMA ese proyecto.
    # Antes, cualquier corte después de pedir la edición (internet, una descarga) hacía que el reintento creara OTRO
    # proyecto y volviera a subir y a editar todo: la misma tanda, pagada dos veces.
    marca = dng_dir / API_MARK
    prev = _read_mark(marca)
    nombres = sorted(p.name for p in todo)
    retomar = (prev.get("fase") in ("editando", "exportando") and set(nombres) <= set(prev.get("fotos") or []))
    if retomar:
        uuid, fase = str(prev["uuid"]), prev["fase"]
        log(f"Imagen: se retoma el proyecto «{prev.get('nombre', uuid)}», ya enviado: no se sube ni se edita otra vez.")
    else:
        prof = find_profile(c.profiles(), profile_name)
        ptype = PHOTO_TYPES.get(photo_type, photo_type)
        name = f"{event_dir.name} · RawFlow {time.strftime('%Y-%m-%d %H%M%S')}"
        log(f"Imagen: proyecto «{name}» · perfil {prof['profile_name']} · {photo_type} · {len(todo)} fotos")
        uuid, fase = c.create_project(name), "editando"
        prev = {"uuid": uuid, "nombre": name, "fotos": nombres, "fase": "subiendo"}
        _write_mark(marca, prev)

        links = c.upload_links(uuid, [p.name for p in todo])
        total = sum(p.stat().st_size for p in todo)
        sent = 0
        for i, p in enumerate(todo, 1):
            if should_stop():
                raise ImagenError("Cancelado.")
            url = links.get(p.name)
            if not url:
                raise ImagenError(f"Imagen no dio enlace de subida para {p.name}.")
            log(f"   subiendo {i}/{len(todo)}  {p.name}")
            _put_file(url, p)
            sent += p.stat().st_size
            progress(0.45 * sent / total)

        c.start_edit(uuid, prof["profile_key"], ptype, tools or DEFAULT_TOOLS)
        prev["fase"] = "editando"
        _write_mark(marca, prev)                  # desde aquí, un reintento retoma ESTE proyecto

    try:
        if fase == "editando":
            _wait(c, uuid, "edit", log, progress, 0.45, 0.75, should_stop)

            # XMP de Imagen junto a los DNG (Bridge / Camera Raw los aplica al abrir)
            try:
                for link in c.links(uuid, "edit"):
                    n = _name_from(link)
                    if n.lower().endswith(".xmp"):
                        _download(link["download_link"], dng_dir / n)
                log("   ediciones .xmp guardadas junto a los DNG (para Bridge / Camera Raw)")
            except Exception as e:  # noqa: BLE001
                log(f"   (no se pudieron bajar los .xmp: {e})")

            c.start_export(uuid)
            prev["fase"] = "exportando"
            _write_mark(marca, prev)
        _wait(c, uuid, "export", log, progress, 0.75, 0.9, should_stop)
    except ImagenError as e:
        if str(e).startswith("Imagen no pudo"):   # Imagen dio ese proyecto por FALLIDO: no se retoma, se hace otro
            marca.unlink(missing_ok=True)
        raise
    out.mkdir(parents=True, exist_ok=True)
    exp = c.links(uuid, "export")
    for i, link in enumerate(exp, 1):
        n = final_name(_name_from(link), ".jpg")
        if (out / n).exists() and (out / n).stat().st_size > 0:      # ya bajada en un intento anterior
            progress(0.9 + 0.1 * i / max(1, len(exp)))
            continue
        log(f"   descargando {i}/{len(exp)}  {n}")
        _download(link["download_link"], out / n)
        progress(0.9 + 0.1 * i / max(1, len(exp)))
    marca.unlink(missing_ok=True)
    log(f"✓  Imagen: {len(exp)} JPEG en «{out.name}».")
    return out


def _wait(c: Client, uuid: str, op: str, log: Log, progress, a: float, b: float, should_stop):
    t0, step, last = time.time(), 8.0, ""
    while True:
        if should_stop():
            raise ImagenError("Cancelado.")
        s = c.status(uuid, op)
        st, pct = s.get("status", ""), s.get("progress")
        if pct is not None:
            progress(a + (b - a) * float(pct) / 100)
        label = f"   Imagen {'editando' if op == 'edit' else 'exportando'}: {st}" + (f" {float(pct):.0f}%" if pct is not None else "")
        if label != last:
            log(label)
            last = label
        if st == "Completed":
            return
        if st == "Failed":
            raise ImagenError(f"Imagen no pudo {'editar' if op == 'edit' else 'exportar'}: {s.get('details') or 'sin detalles'}")
        if time.time() - t0 > 6 * 3600:
            raise ImagenError("Imagen tardó más de 6 horas; revisa el proyecto en la app de Imagen.")
        time.sleep(step)
        step = min(step * 1.2, 45)

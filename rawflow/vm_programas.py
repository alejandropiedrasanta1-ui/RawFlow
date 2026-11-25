"""«Instalar máquina virtual», segunda parte: mete en Windows los programas y RawFlow y los instala solos.

Toma lo que haya en «Instaladores/WINDOWS/Programas para Windows» (DxO, Imagen AI, Adobe/Creative Cloud, Retouch4me,
CineSlide) y, de «Instaladores/MÁQUINA VIRTUAL (solo en el Mac)», Python con sus librerías y el código de RawFlow para
Windows; los copia dentro de la máquina con
el agente de UTM (utmctl file push) y los instala sin ventanas (utmctl exec). Lo que pide cuenta o licencia (Adobe, DxO,
Retouch4me, Imagen) queda instalado; iniciar sesión lo hace el usuario.
"""
from __future__ import annotations

import os
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

Log = Callable[[str], None]
BUSY = False             # True mientras «Instalar máquina virtual» trabaja: al empezar un evento no se apaga la VM
UTMCTL = "/Applications/UTM.app/Contents/MacOS/utmctl"
STAGE = r"C:\RawFlow-Instalacion"
APPDIR = r"C:\RawFlow"                                  # RawFlow desde el código (con Python)
EXEDIR = r"C:\Program Files\RawFlow"                    # RawFlow.exe (como en un PC)
CONHOST = r"C:\Windows\System32\conhost.exe"           # --headless: PowerShell sin ninguna ventana (ni de Terminal)


# La carpeta «Instaladores», ordenada por sistema (desde 4.17; las de antes, sin ordenar, también se entienden):
#   MAC/                                 RawFlow X para Mac.zip
#   WINDOWS/                             RawFlow X para Windows (INSTALADOR).zip  ← lo ÚNICO que se copia a la PC
#   WINDOWS/Programas para Windows/      DxO, Imagen AI, Bridge, Retouch4me, CineSlide (sus instaladores)
#   MÁQUINA VIRTUAL (solo en el Mac)/    ISO de Windows 11, RawFlow-X-Windows-codigo.zip, Python para Windows
# Desde 4.26 la máquina virtual NO está en «Instaladores» (ahí solo quedan los instaladores): está en
# PROGRAMA/MAQUINA VIRTUAL MAC (junto al código). Se sigue entendiendo el sitio de antes.
MAC_DIR = "MAC"
WIN_DIR = "WINDOWS"
PROGRAMS_DIR = "Programas para Windows"
VM_DIR = "MÁQUINA VIRTUAL (solo en el Mac)"
VM_DIR_NEW = "MAQUINA VIRTUAL MAC"                # dentro de PROGRAMA (el proyecto)
PYTHON_DIR = "Python para Windows"
OLD_RAWFLOW_DIR = "RawFlow para Windows"          # antes de 4.17


def programs_dir(root: Path) -> Path:
    """Los instaladores de los programas que usa RawFlow en Windows (DxO, Imagen AI, Bridge, Retouch4me, CineSlide)."""
    new, old = Path(root) / WIN_DIR / PROGRAMS_DIR, Path(root) / PROGRAMS_DIR
    return old if old.is_dir() and not new.is_dir() else new


def vm_dir(root: Path) -> Path:
    """Lo que solo usa «Instalar máquina virtual» desde el Mac: el código de RawFlow para Windows, Python y la ISO."""
    proj = Path(__file__).resolve().parent.parent / VM_DIR_NEW          # PROGRAMA/MAQUINA VIRTUAL MAC (desde 4.26)
    sibling = Path(root).parent / "PROGRAMA" / VM_DIR_NEW
    for d in (proj, sibling):
        if d.is_dir():
            return d
    new, old = Path(root) / VM_DIR, Path(root) / OLD_RAWFLOW_DIR
    return old if old.is_dir() and not new.is_dir() else new


def python_dir(root: Path) -> Optional[Path]:
    """Python para Windows (x64 y ARM) y sus librerías, sin internet."""
    cands = [vm_dir(root) / PYTHON_DIR, Path(root) / VM_DIR / PYTHON_DIR] + sorted(programs_dir(root).glob("*Python*"))
    return next((d for d in cands if d.is_dir()), None)


def program_folder(root: Path, name: str) -> Path:
    """La carpeta de un programa dentro de «Programas para Windows» (la que ya haya, con o sin número delante)."""
    pw = programs_dir(root)
    words = name.split()
    key = words[1] if words[0].isdigit() and len(words) > 1 else words[0]      # «1 DxO PureRAW» → DxO
    hit = sorted(d for d in pw.glob(f"*{key}*") if d.is_dir()) if pw.is_dir() else []
    return hit[0] if hit else pw / name


def installers_root() -> Optional[Path]:
    """La carpeta «Instaladores» del proyecto (RAWFLOW_INSTALADORES la cambia)."""
    env = os.environ.get("RAWFLOW_INSTALADORES")
    if env and Path(env).is_dir():
        return Path(env)
    for base in (Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Escritorio"):
        for cand in list(base.glob("*/Instaladores")) + [base / "Instaladores"]:
            if any((cand / d).is_dir() for d in (Path(WIN_DIR) / PROGRAMS_DIR, VM_DIR, PROGRAMS_DIR)):
                return cand
    return None


# --------------------------------------------------------------------------- agente de UTM
def _utm(args: List[str], data: Optional[bytes] = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([UTMCTL, *args], input=data, capture_output=True, timeout=timeout)


def host_ip() -> str:
    """La IP del Mac en la red de las máquinas virtuales de UTM (bridge100; normalmente 192.168.64.1)."""
    try:
        out = subprocess.run(["ifconfig", "bridge100"], capture_output=True, text=True, timeout=5).stdout
        import re
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "192.168.64.1"


class Servidor:
    """Sirve la carpeta «Instaladores» SOLO a la red interna de las máquinas virtuales mientras se instala (el agente
    de UTM copia a ~0,25 MB/s; por la red interna va a cientos de MB/s). Solo lectura; se cierra al acabar."""

    def __init__(self, root: Path):
        import functools
        import threading
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        root_dir = Path(root)
        # (4.26) la máquina virtual ya no está dentro de «Instaladores» (PROGRAMA/MAQUINA VIRTUAL MAC): se sirve la
        # carpeta que contiene las dos para que sus archivos sigan teniendo dirección
        served = Path(os.path.commonpath([str(root_dir.resolve()), str(vm_dir(root_dir).resolve())]))

        class _Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_PUT(self):                                 # Windows sube aquí el RawFlow.exe que compila
                from urllib.parse import unquote
                rel = unquote(self.path.lstrip("/"))
                dest = (served / rel).resolve()
                ok = ((dest.parent == vm_dir(root_dir).resolve() and dest.suffix == ".zip")
                      or (dest.parent.parent == programs_dir(root_dir).resolve()
                          and dest.suffix.lower() in (".exe", ".msi", ".zip")))
                if not ok:
                    self.send_error(403)
                    return
                n = int(self.headers.get("Content-Length", "0"))
                tmp = dest.with_name(dest.name + ".subiendo")
                with open(tmp, "wb") as f:
                    while n > 0:
                        b = self.rfile.read(min(n, 1 << 20))
                        if not b:
                            break
                        f.write(b)
                        n -= len(b)
                if n == 0:
                    tmp.replace(dest)
                    self.send_response(201)
                else:
                    tmp.unlink(missing_ok=True)
                    self.send_response(400)
                self.end_headers()
        self.root, self.ip = served, host_ip()
        class _Srv(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass                                          # p. ej. curl cortó: no ensuciar el registro
        self.httpd = _Srv((self.ip, 0), functools.partial(_Quiet, directory=str(self.root)))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, local: Path) -> str:
        from urllib.parse import quote
        return f"http://{self.ip}:{self.port}/" + quote(Path(local).relative_to(self.root).as_posix())

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def push(vm: str, local: Path, remote: str, log: Log = print, server: Optional["Servidor"] = None) -> None:
    """Copia un archivo del Mac a Windows: por la red interna si hay servidor (rápido), si no por el agente (lento)."""
    t0 = time.monotonic()
    mb = local.stat().st_size / 1e6
    if server is not None:
        rc, out = ps(vm, f"$ProgressPreference='SilentlyContinue'; & curl.exe -s -f -o '{remote}' '{server.url(local)}'; "
                         "if ($LASTEXITCODE -eq 0) {'LISTO'} else {'ERROR ' + $LASTEXITCODE}", timeout=3600)
        if "LISTO" not in out:
            raise RuntimeError(f"No pude copiar {local.name} a Windows: {out.strip()[-200:]}")
    else:
        with open(local, "rb") as f:
            r = subprocess.run([UTMCTL, "file", "push", vm, remote], stdin=f, capture_output=True, timeout=7200)
        if r.returncode != 0:
            raise RuntimeError(f"No pude copiar {local.name} a Windows: {r.stderr.decode(errors='replace')[:200]}")
    log(f"   copiado {local.name} ({mb:,.0f} MB, {mb / max(0.1, time.monotonic() - t0):.0f} MB/s)")


def pull(vm: str, remote: str) -> str:
    r = _utm(["file", "pull", vm, remote], timeout=120)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def run(vm: str, cmd: str, timeout: int = 1800) -> tuple:
    """Ejecuta una línea de cmd.exe en Windows (como el sistema, con permisos de administrador) y devuelve
    (código, salida)."""
    r = _utm(["exec", vm, "--cmd", "cmd.exe", "/c", f"{cmd} 2>&1"], timeout=timeout)   # la salida llega por utmctl
    return r.returncode, (r.stdout or b"").decode("utf-8", errors="replace") + (r.stderr or b"").decode(
        "utf-8", errors="replace")


def ps(vm: str, script: str, timeout: int = 1800) -> tuple:
    """Ejecuta PowerShell en Windows (como el sistema). Va codificado: así ninguna comilla o espacio se rompe por el
    camino (UTM vuelve a entrecomillar los argumentos y cmd.exe los partía)."""
    import base64
    enc = base64.b64encode(("$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; "
                            + script).encode("utf-16-le")).decode()
    try:
        r = _utm(["exec", vm, "--cmd", "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                  "-EncodedCommand", enc], timeout=timeout)
    except subprocess.TimeoutExpired:              # Windows no contestó a tiempo (apagándose, colgado…): no reventar
        return 124, "TIMEOUT"
    return r.returncode, (r.stdout or b"").decode("utf-8", errors="replace") + (r.stderr or b"").decode(
        "utf-8", errors="replace")


def ps_run(vm: str, exe: str, args: List[str], timeout: int = 2400) -> int:
    """Ejecuta un instalador y espera a que termine; devuelve su código de salida (-1 si no se pudo)."""
    al = ",".join("'" + a.replace("'", "''") + "'" for a in args) if args else "@()"
    rc, out = ps(vm, f"$p = Start-Process -FilePath '{exe}' -ArgumentList {al} -Wait -PassThru -WindowStyle Hidden; "
                     "'CODIGO ' + $p.ExitCode", timeout=timeout)
    import re
    m = re.search(r"CODIGO (-?\d+)", out)
    return int(m.group(1)) if m else -1


def agent_ok(vm: str) -> bool:
    try:
        return _utm(["exec", vm, "--cmd", "cmd.exe", "/c", "ver"], timeout=60).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- qué hay que instalar
def plan(root: Path, arch: str = "arm64") -> dict:
    """Qué se va a instalar, según lo que haya en la carpeta de instaladores."""
    pw, vd, py = programs_dir(root), vm_dir(root), python_dir(root)
    a = "arm64" if arch == "arm64" else "amd64"

    def newest(files):                                # el más reciente gana (por nombre, «1.9» iría detrás de «1.10»)
        files = [f for f in files if not f.name.endswith((".part", ".subiendo"))]
        return sorted(files, key=lambda f: f.stat().st_mtime)
    pick = lambda pat: (newest(pw.glob(pat)) or [None])[-1]   # noqa: E731
    rf = newest(vd.glob("RawFlow-*-Windows-codigo.zip"))
    exe = newest(vd.glob("RawFlow-*-Windows.zip"))
    return {
        "python": (newest(py.glob(f"python-*-{a}.exe")) or [None])[-1] if py else None,
        "wheels": (py / f"librerias-win_{a}") if py and (py / f"librerias-win_{a}").is_dir() else None,
        "rawflow": rf[-1] if rf else None,
        "rawflow_exe": exe[-1] if exe else None,
        "dxo": pick("*DxO*/*.exe"),                    # con o sin número delante («1 DxO PureRAW» o «2 DxO PureRAW»)
        "imagen": pick("*Imagen*/*.exe"),
        "adobe": pick("*Adobe*/*.exe"),
        "retouch4me": pick("*Retouch4me*/*.exe"),
        "cineslide": pick("*CineSlide*/*.exe"),
    }


def provision(vm: str, log: Log = print, arch: str = "arm64", root: Optional[Path] = None,
              skip: tuple = (), tidy: bool = True) -> dict:
    """Instala en Windows todo lo que haya en «Instaladores». Devuelve {programa: "ok" | "falta" | error}.
    `skip`: claves del plan que no se instalan esta vez (p. ej. un instalador que aún se descarga).
    `tidy`: además deja Windows limpio y ordena escritorio y barra (False: solo instala)."""
    root = root or installers_root()
    if not root:
        raise RuntimeError("No encuentro la carpeta «Instaladores» (con «WINDOWS/Programas para Windows» dentro).")
    p = {k: (None if k in skip else v) for k, v in plan(root, arch).items()}
    p["_skip"] = tuple(skip)
    res = {}
    try:
        srv = Servidor(root)                                  # copia rápida por la red interna de la VM
    except Exception as e:  # noqa: BLE001
        log(f"   (sin red interna: copio por el agente, más lento: {e})")
        srv = None
    try:
        stage = STAGE + "\\" + time.strftime("%Y%m%d-%H%M%S")    # carpeta propia: nada choca con un intento anterior
        if tidy:
            res["Windows limpio"] = debloat(vm, log)      # fuera lo que no sirve: cada máquina sale igual
        _provision(vm, log, p, res, srv, stage)
        if tidy:
            res["Escritorio y barra"] = organize_desktop(vm, log)
        return res
    finally:
        if srv:
            srv.close()


def _provision(vm: str, log: Log, p: dict, res: dict, srv, STAGE: str) -> dict:   # noqa: N803
    ps(vm, f"New-Item -ItemType Directory -Force -Path '{STAGE}','{STAGE}\\librerias','{APPDIR}' | Out-Null")
    if p.get("rawflow_exe"):                          # como en un PC de verdad: RawFlow.exe, sin Python
        log(f"RawFlow ({p['rawflow_exe'].name})…")
        push(vm, p["rawflow_exe"], f"{STAGE}\\{p['rawflow_exe'].name}", log, server=srv)
        rc, out = ps(vm, f"""
Get-Process -Name RawFlow -ErrorAction SilentlyContinue | Stop-Process -Force
$d = '{EXEDIR}'
if (Test-Path $d) {{ Remove-Item -Recurse -Force $d }}
Expand-Archive -Force '{STAGE}\\{p['rawflow_exe'].name}' (Split-Path $d)
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('C:\\Users\\Public\\Desktop\\RawFlow.lnk')
$s.TargetPath = "$d\\RawFlow.exe"; $s.WorkingDirectory = $d; $s.Save()
if (Test-Path "$d\\RawFlow.exe") {{ 'RAWFLOW_OK' }}
""", 900)
        res["RawFlow"] = "ok (RawFlow.exe)" if "RAWFLOW_OK" in out else f"error: {out.strip()[-300:]}"
        p = dict(p, python=None, wheels=None, rawflow=None)
    # 1) Python + librerías (sin internet: las ruedas van en la carpeta)
    if p["python"]:
        log(f"Python para RawFlow ({p['python'].name})…")
        push(vm, p["python"], f"{STAGE}\\{p['python'].name}", log, server=srv)
        rc0, have = ps(vm, "if (Get-ChildItem 'C:\\Program Files\\Python3*\\python.exe' -ErrorAction SilentlyContinue) "
                           "{ 'YA' } else { 'NO' }", 60)
        if "YA" in have:                              # repetir el instalador de Python lo DESINSTALA: solo si falta
            res["Python"] = "ok (ya estaba)"
        else:
            rc = ps_run(vm, f"{STAGE}\\{p['python'].name}",
                        ["/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0", "Include_launcher=1"], 1200)
            res["Python"] = "ok" if rc == 0 else f"error {rc}"
    if p["wheels"]:
        for w in sorted(p["wheels"].glob("*.whl")):
            push(vm, w, f"{STAGE}\\librerias\\{w.name}", log, server=srv)
    # 2) RawFlow: carpeta C:\RawFlow, su entorno con las librerías y un acceso directo en el escritorio
    if p["rawflow"]:
        log(f"RawFlow ({p['rawflow'].name})…")
        push(vm, p["rawflow"], f"{STAGE}\\{p['rawflow'].name}", log, server=srv)
        top = zipfile.ZipFile(p["rawflow"]).namelist()[0].split("/")[0]
        rc, out = ps(vm, f"""
$ErrorActionPreference = 'Stop'
Expand-Archive -Force '{STAGE}\\{p['rawflow'].name}' '{STAGE}\\rf'
robocopy '{STAGE}\\rf\\{top}' '{APPDIR}' /E /NFL /NDL /NJH /NJS | Out-Null
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) {{ $py = (Get-ChildItem 'C:\\Program Files\\Python3*\\python.exe' | Select-Object -Last 1).FullName }}
& $py -3 -m venv '{APPDIR}\\.venv' 2>$null; if (-not (Test-Path '{APPDIR}\\.venv\\Scripts\\python.exe')) {{ & $py -m venv '{APPDIR}\\.venv' }}
& '{APPDIR}\\.venv\\Scripts\\python.exe' -m pip install --no-index --find-links '{STAGE}\\librerias' -r '{APPDIR}\\requirements.txt' -q
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('C:\\Users\\Public\\Desktop\\RawFlow.lnk')
$s.TargetPath = '{APPDIR}\\.venv\\Scripts\\pythonw.exe'; $s.Arguments = '"{APPDIR}\\RawFlow.py"'
$s.WorkingDirectory = '{APPDIR}'; $s.Save()
'RAWFLOW_OK'
""", timeout=2400)
        res["RawFlow"] = "ok" if "RAWFLOW_OK" in out else f"error: {out.strip()[-300:]}"
    # 3) Programas (silenciosos): lo que pida cuenta o licencia se activa después a mano
    silent = {
        "imagen": ["/S", "/allusers"],                     # NSIS de Electron
        "cineslide": ["/instalar", "/silencioso"],         # instalador propio de CineSlide
        "retouch4me": ["/S"],                              # Retouch4me Arams: NSIS (para todo el equipo)
        "dxo": ["/quiet", "/norestart"],
        "adobe": ["--silent"],
    }
    for key in ("dxo", "adobe"):                          # los demás: según el tipo de instalador que sea
        if p.get(key):
            kind = installer_kind(p[key])
            if kind in SILENT_BY_KIND:
                silent[key] = SILENT_BY_KIND[kind]
    names = {"imagen": "Imagen AI", "cineslide": "CineSlide", "dxo": "DxO PureRAW", "retouch4me": "Retouch4me",
             "adobe": "Adobe Creative Cloud"}
    for key in ("dxo", "imagen", "adobe", "retouch4me", "cineslide"):
        f = p[key]
        if not f:
            if key not in p.get("_skip", ()):
                res[names[key]] = "falta el instalador"
            continue
        log(f"{names[key]} ({f.name})…")
        push(vm, f, f"{STAGE}\\{f.name}", log, server=srv)
        if key == "cineslide":
            res[names[key]] = _install_cineslide(vm, f"{STAGE}\\{f.name}", silent[key], log)
            continue
        rc = ps_run(vm, f"{STAGE}\\{f.name}", silent[key], 2400)
        res[names[key]] = "ok" if rc == 0 else f"terminó con código {rc}"
    return res


def detect(vm: str) -> str:
    """Qué programas ve RawFlow dentro de Windows (con su propio módulo de detección: el .exe si está)."""
    out_f = STAGE + "\\programas.json"
    rc, out = ps(vm, f"""
Remove-Item '{out_f}' -ErrorAction SilentlyContinue
if (Test-Path '{EXEDIR}\\RawFlow.exe') {{ Start-Process -FilePath '{EXEDIR}\\RawFlow.exe' -ArgumentList '--programas','{out_f}' -Wait -WindowStyle Hidden }}
elseif (Test-Path '{APPDIR}\\.venv\\Scripts\\python.exe') {{ Set-Location '{APPDIR}'; & '{APPDIR}\\.venv\\Scripts\\python.exe' RawFlow.py --programas '{out_f}' }}
if (Test-Path '{out_f}') {{ Get-Content '{out_f}' -Raw -Encoding UTF8 }}
""", timeout=300)
    import re
    m = re.search(r"\{.*\}", out, re.S)
    return m.group(0) if m else out.strip()[-300:]


def open_in_session(vm: str, exe: str, args: str = "", workdir: str = "", elevated: bool = True) -> str:
    """Abre un programa en la sesión de Windows del usuario conectado (lo que lanza el agente de UTM corre como el
    sistema, en un escritorio invisible): tarea programada interactiva, sin contraseña. `elevated=False`: como un
    doble clic normal (sin permisos de administrador)."""
    wd = workdir or "C:\\"
    argpart = f"-Argument '{args}' " if args else ""          # Windows no acepta un argumento vacío
    level = "Highest" if elevated else "Limited"
    rc, out = ps(vm, f"""
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $u) {{ 'SIN_SESION'; exit }}
$a = New-ScheduledTaskAction -Execute '{exe}' {argpart}-WorkingDirectory '{wd}'
$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel {level}
$n = 'RawFlowAbrir-' + [guid]::NewGuid().ToString('N').Substring(0, 8)   # nombre propio: nunca choca con otra abierta
$st = New-ScheduledTaskSettingsSet -MultipleInstances Parallel -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $n -Action $a -Principal $p -Settings $st -Force | Out-Null
Start-ScheduledTask -TaskName $n
Get-ScheduledTask -TaskName 'RawFlowAbrir*' | Where-Object {{ $_.TaskName -ne $n -and $_.State -ne 'Running' }} | Unregister-ScheduledTask -Confirm:$false
'ABIERTO para ' + $u
""", timeout=120)
    return out.strip()


def build_exe(vm: str, version: str, log: Log = print, root: Optional[Path] = None) -> Path:
    """Compila RawFlow.exe para Windows de 64 bits (x64) DENTRO de la máquina (PyInstaller no compila para Windows
    desde el Mac) con el Python x64 de «Instaladores», y lo deja en «Instaladores/MÁQUINA VIRTUAL (solo en el Mac)» como
    RawFlow-<versión>-Windows.zip: en un PC basta con descomprimir y abrir RawFlow.exe (no hace falta Python).
    Necesita RawFlow ya copiado en C:\\RawFlow (provision)."""
    root = root or installers_root()
    p = plan(root, "amd64")
    if not p["python"] or not p["wheels"]:
        raise RuntimeError(f"Faltan Python x64 y sus librerías en «{vm_dir(root) / PYTHON_DIR}».")
    vm_dir(root).mkdir(parents=True, exist_ok=True)
    srv = Servidor(root)
    stage = STAGE + "\\x64-" + time.strftime("%Y%m%d-%H%M%S")
    try:
        ps(vm, f"New-Item -ItemType Directory -Force -Path '{stage}','{stage}\\librerias' | Out-Null")
        push(vm, p["python"], f"{stage}\\{p['python'].name}", log, server=srv)
        for w in sorted(p["wheels"].glob("*.whl")):
            push(vm, w, f"{stage}\\librerias\\{w.name}", log, server=srv)
        log("Python x64 (para compilar RawFlow.exe)…")
        rc0, have = ps(vm, "$ok = $false; if (Test-Path 'C:\\Python314-x64\\python.exe') { "
                           "$v = & 'C:\\Python314-x64\\python.exe' -c 'import encodings, venv; print(42)' 2>$null; "
                           "$ok = ($v -eq '42') }; if ($ok) { 'YA' } else { 'NO' }", 120)   # que ARRANQUE, no solo que esté
        if "YA" not in have:                          # repetir el instalador de Python lo DESINSTALA
            ps_run(vm, f"{stage}\\{p['python'].name}", ["/quiet", "/uninstall"], 1200)   # restos de un intento roto
            ps(vm, "Remove-Item -Recurse -Force 'C:\\Python314-x64' -ErrorAction SilentlyContinue", 300)
            ps_run(vm, f"{stage}\\{p['python'].name}",
                   ["/quiet", "InstallAllUsers=1", "PrependPath=0", "Include_test=0", "Include_launcher=0",
                    "TargetDir=C:\\Python314-x64"], 1200)
        log("Compilando RawFlow.exe (x64)…")
        zname = f"RawFlow-{version}-Windows.zip"
        rc, out = ps(vm, f"""
$b = 'C:\\RawFlowBuild'
if (Test-Path $b) {{ Remove-Item -Recurse -Force $b }}
New-Item -ItemType Directory -Force -Path $b | Out-Null
robocopy '{APPDIR}' "$b\\src" /E /XD .venv __pycache__ /NFL /NDL /NJH /NJS | Out-Null
& 'C:\\Python314-x64\\python.exe' -m venv "$b\\venv"
& "$b\\venv\\Scripts\\python.exe" -m pip install --find-links '{stage}\\librerias' PySide6 psutil pyinstaller -q
Set-Location "$b\\src"
& "$b\\venv\\Scripts\\pyinstaller.exe" --noconfirm --windowed --name RawFlow --distpath "$b\\dist" --workpath "$b\\work" `
  --icon 'rawflow\\RawFlow.ico' `
  --add-data 'rawflow\\cineslide_run.py;rawflow' --add-data 'rawflow\\r4m_worker.js;rawflow' RawFlow.py 2>&1 | Select-Object -Last 3
if (-not (Test-Path "$b\\dist\\RawFlow\\RawFlow.exe")) {{ 'SIN_EXE'; exit }}
Compress-Archive -Force -Path "$b\\dist\\RawFlow" -DestinationPath "$b\\{zname}"
& curl.exe -s -f -T "$b\\{zname}" '{srv.url(vm_dir(root) / zname)}'
if ($LASTEXITCODE -eq 0) {{ 'SUBIDO' }} else {{ 'ERROR_SUBIDA ' + $LASTEXITCODE }}
""", timeout=3600)
        dest = vm_dir(root) / zname
        if "SUBIDO" not in out or not dest.exists():
            raise RuntimeError(f"No salió RawFlow.exe: {out.strip()[-400:]}")
        log(f"✓  {zname} ({dest.stat().st_size / 1e6:,.0f} MB) en «Instaladores/{dest.parent.name}».")
        return dest
    finally:
        srv.close()


def guest_screenshot(vm: str, local: Path) -> bool:
    """Foto de la pantalla de Windows tomada DENTRO de la sesión del usuario (sirve aunque la pantalla del Mac esté
    apagada o la ventana de UTM oculta) y traída al Mac."""
    remote = STAGE + "\\captura.png"
    cap = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
           "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
           "$i = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
           "$g = [System.Drawing.Graphics]::FromImage($i); "
           "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $i.Size); "
           f"$i.Save('{remote}', [System.Drawing.Imaging.ImageFormat]::Png)")
    import base64
    enc = base64.b64encode(cap.encode("utf-16-le")).decode()
    ps(vm, f"Remove-Item '{remote}' -ErrorAction SilentlyContinue")
    open_in_session(vm, CONHOST, "--headless powershell.exe " + f"-NoProfile -WindowStyle Hidden -EncodedCommand {enc}")
    for _ in range(30):
        time.sleep(1)
        rc, out = ps(vm, f"if (Test-Path '{remote}') {{ (Get-Item '{remote}').Length }} else {{ 0 }}", 60)
        import re
        m = re.search(r"^(\d+)", out.strip())
        if m and int(m.group(1)) > 1000:
            break
    r = _utm(["file", "pull", vm, remote], timeout=300)
    if r.returncode != 0 or len(r.stdout) < 1000:
        return False
    Path(local).write_bytes(r.stdout)
    return True


def guest_keys(vm: str, keys: str) -> None:
    """Pulsa teclas en la sesión de Windows (formato SendKeys: '{ESC}', '%{F4}'…)."""
    import base64
    k = keys.replace("'", "''")
    enc = base64.b64encode(("Add-Type -AssemblyName System.Windows.Forms; "
                            f"[System.Windows.Forms.SendKeys]::SendWait('{k}')").encode("utf-16-le")).decode()
    open_in_session(vm, CONHOST, "--headless powershell.exe " + f"-NoProfile -WindowStyle Hidden -EncodedCommand {enc}")
    time.sleep(3)


def guest_front(vm: str, title: str) -> None:
    """Trae al frente, en la sesión de Windows, la ventana cuyo título empieza por `title` (cierra el menú Inicio)."""
    import base64
    t = title.replace("'", "''")
    enc = base64.b64encode((f"$w = New-Object -ComObject WScript.Shell; Start-Sleep -Milliseconds 300; "
                            f"$null = $w.AppActivate('{t}')").encode("utf-16-le")).decode()
    open_in_session(vm, CONHOST, "--headless powershell.exe " + f"-NoProfile -WindowStyle Hidden -EncodedCommand {enc}")
    time.sleep(3)


def _install_cineslide(vm: str, exe: str, args: List[str], log: Log, minutes: int = 30) -> str:
    """El instalador de CineSlide instala (y precalienta sus plantillas) en silencio, pero al final espera un clic en
    una ventana que como el sistema no se ve: se da por terminado cuando CineSlide está entero y su precalentado acabó,
    y entonces se cierra el instalador."""
    al = ",".join("'" + a + "'" for a in args)
    ps(vm, f"Start-Process -FilePath '{exe}' -ArgumentList {al} -WindowStyle Hidden", 60)
    c = "C:\\Program Files (x86)\\CineSlide"
    t0 = time.monotonic()
    while time.monotonic() - t0 < minutes * 60:
        time.sleep(10)
        rc, out = ps(vm, f"""
$p = Get-Process -Name 'Instalar_CineSlide*' -ErrorAction SilentlyContinue
$ok = (Test-Path '{c}\\python\\python.exe') -and (Test-Path '{c}\\CineSlide.py')
$log = Get-ChildItem '{c}\\datos\\logs\\motor.log' -ErrorAction SilentlyContinue
$pre = $log -and (Select-String -Path $log.FullName -Pattern 'Express \\[plantillas\\] terminado' -Quiet)
if (-not $p) {{ 'FIN ' + $ok }} elseif ($ok -and $pre) {{ Start-Sleep 5; $p | Stop-Process -Force; 'LISTO' }} else {{ 'SIGUE' }}
""", 120)
        if "LISTO" in out or "FIN True" in out:
            log(f"   CineSlide instalado en {(time.monotonic() - t0) / 60:.0f} min.")
            return "ok"
        if "FIN False" in out:
            return "el instalador terminó sin instalar CineSlide"
    return "el instalador no terminó a tiempo"


def update_rawflow(vm: str, log: Log = print, root: Optional[Path] = None) -> str:
    """Pone en C:\\RawFlow la última versión del código de RawFlow para Windows (conserva su entorno .venv)."""
    root = root or installers_root()
    p = plan(root)
    if not p["rawflow"]:
        return "falta el zip de RawFlow para Windows"
    srv = Servidor(root)
    stage = STAGE + "\\upd-" + time.strftime("%Y%m%d-%H%M%S")
    try:
        ps(vm, f"New-Item -ItemType Directory -Force -Path '{stage}' | Out-Null")
        push(vm, p["rawflow"], f"{stage}\\{p['rawflow'].name}", log, server=srv)
        top = zipfile.ZipFile(p["rawflow"]).namelist()[0].split("/")[0]
        rc, out = ps(vm, f"""
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object {{ $_.CommandLine -like '*RawFlow.py*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}
Expand-Archive -Force '{stage}\\{p['rawflow'].name}' '{stage}\\rf'
robocopy '{stage}\\rf\\{top}' '{APPDIR}' /E /XD .venv /NFL /NDL /NJH /NJS | Out-Null
'ACTUALIZADO'
""", 600)
        return "ok" if "ACTUALIZADO" in out else out.strip()[-200:]
    finally:
        srv.close()


# --------------------------------------------------------------------------- instaladores bajados en Windows → Mac
DEST_DIRS = (("dxo", "1 DxO PureRAW", ("pureraw", "dxo")),
             ("imagen", "2 Imagen AI", ("imagen",)),
             ("adobe", "3 Adobe Bridge (Creative Cloud)", ("creative", "adobe", "bridge", "cc_", "acc")),
             ("retouch4me", "4 Retouch4me", ("retouch4me", "arams", "r4m")),
             ("cineslide", "5 CineSlide", ("cineslide",)))


def collect_downloads(vm: str, log: Log = print, root: Optional[Path] = None) -> dict:
    """Los instaladores que el usuario bajó en Windows (Descargas) se guardan en «Instaladores/WINDOWS/Programas para Windows»
    del Mac, cada uno en su carpeta: la próxima máquina los instala sola, sin volver a bajarlos de sus cuentas."""
    root = root or installers_root()
    rc, out = ps(vm, "Get-ChildItem 'C:\\Users\\*\\Downloads\\*' -Include *.exe,*.msi,*.zip -File "
                     "-ErrorAction SilentlyContinue | ForEach-Object { $_.FullName + '|' + $_.Length }", 120)
    got = {}
    srv = Servidor(root)
    try:
        for line in out.splitlines():
            if "|" not in line:
                continue
            path, size = line.rsplit("|", 1)
            name = path.split("\\")[-1]
            low = name.lower()
            hit = next(((k, d) for k, d, words in DEST_DIRS if any(w in low for w in words)), None)
            if not hit:
                continue
            key, folder = hit
            dest = program_folder(root, folder) / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size == int(size):
                got[key] = f"ya estaba ({name})"
                continue
            rc, o = ps(vm, f"& curl.exe -s -f -T '{path}' '{srv.url(dest)}'; "
                           "if ($LASTEXITCODE -eq 0) {'SUBIDO'} else {'ERROR ' + $LASTEXITCODE}", 3600)
            ok = "SUBIDO" in o and dest.exists()
            got[key] = f"guardado ({name}, {int(size) / 1e6:,.0f} MB)" if ok else f"no se pudo copiar {name}"
            log(f"   {got[key]}")
    finally:
        srv.close()
    return got


# --------------------------------------------------------------------------- máquina «base» (sesiones ya iniciadas)
BASE = "RawFlow Windows (base)"


def utm_rows() -> list:
    """[(uuid, estado, nombre)] de las máquinas de UTM (sin UTM abierto, la lista sale vacía)."""
    if not Path(UTMCTL).exists():
        return []
    r = _utm(["list"], timeout=60)
    rows = []
    for line in r.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def _utm_list() -> dict:
    """{nombre: estado} de las máquinas de UTM (también por UUID: a la VM se le puede llamar de las dos formas)."""
    out = {}
    for uuid, st, name in utm_rows():
        out[name] = st
        out[uuid] = st
    return out


def vm_running(vm: str) -> bool:
    """¿Sigue encendida la máquina? (si no se puede saber, se da por encendida: no cortar por un fallo al mirar)"""
    try:
        st = _utm_list().get(vm)
    except Exception:  # noqa: BLE001
        return True
    return st is None or st == "started"


APAGADA = "Windows se apagó a mitad (p. ej. empezó un evento en RawFlow): la guía sigue la próxima vez"


def wait_session(vm: str, log: Log = print, minutes: int = 8) -> bool:
    """Espera a que Windows responda y a que la sesión del usuario esté abierta (inicio automático)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < minutes * 60:
        if agent_ok(vm):
            try:
                rc, out = ps(vm, "if ((Get-Process explorer -ErrorAction SilentlyContinue) -and "
                                 "(Get-CimInstance Win32_ComputerSystem).UserName) { 'SESION' }", 60)
                if "SESION" in out:
                    return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(4)
    return False


def start_vm(vm: str, log: Log = print) -> bool:
    if _utm_list().get(vm) != "started":
        _utm(["start", vm], timeout=120)
    ok = wait_session(vm, log)
    log("✓  Windows encendido y con la sesión abierta." if ok else "Windows arrancó; aún está iniciando.")
    return ok


def restart_windows(vm: str, log: Log = print) -> bool:
    """Reinicia Windows (la barra de tareas y el menú Inicio quedan fijos al volver a entrar) y espera la sesión."""
    log("Reiniciando Windows para que la barra de tareas quede fija…")
    try:
        ps(vm, "shutdown.exe /r /t 5 /f", 60)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(30)                                    # que de verdad se apague antes de preguntar
    ok = wait_session(vm, log)
    log("✓  Windows reiniciado." if ok else "Windows aún está reiniciando.")
    return ok


def make_base(vm: str, log: Log = print) -> str:
    """Guarda la máquina (con los programas instalados y las sesiones iniciadas) como «base»: copia instantánea que
    no ocupa espacio de más. «Instalar máquina virtual» la clonará en vez de instalarlo todo desde cero."""
    vms = _utm_list()
    if vms.get(vm) == "started":
        log("Apagando Windows para guardar la base…")
        _utm(["stop", vm, "--request"], timeout=120)
        for _ in range(60):
            time.sleep(3)
            if _utm_list().get(vm) == "stopped":
                break
    if BASE in _utm_list():
        log("Ya había una base anterior: se reemplaza.")
        _utm(["delete", BASE], timeout=300)
    r = _utm(["clone", vm, "--name", BASE], timeout=1800)
    if r.returncode != 0 or BASE not in _utm_list():
        raise RuntimeError("No pude guardar la base: " + r.stderr.decode(errors="replace")[:200])
    log(f"✓  Base guardada: «{BASE}».")
    return BASE


def from_base(name: str, log: Log = print) -> Optional[str]:
    """Nueva máquina clonada de la base (segundos) y arrancada. None si no hay base."""
    vms = _utm_list()
    if BASE not in vms:
        return None
    from . import vm as vmmod
    name = vmmod.unique_name(name)
    log(f"Copiando la base (Windows, programas y sesiones ya iniciadas) a «{name}»…")
    r = _utm(["clone", BASE, "--name", name], timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("No pude clonar la base: " + r.stderr.decode(errors="replace")[:200])
    _utm(["start", name], timeout=120)
    if wait_session(name, log):
        log(f"✓  «{name}» lista (desde la base).")
    else:
        log(f"«{name}» arrancó; Windows aún está iniciando.")
    return name


SIGN_IN = {"DxO PureRAW": "dxo", "Retouch4me Arams": "retouch4me", "Imagen AI": "imagen"}   # piden iniciar sesión


def install_missing(vm: str, log: Log = print, root: Optional[Path] = None) -> list:
    """En una copia de la base: instala los programas cuyo instalador se añadió a «Instaladores» después de guardar la
    base (p. ej. DxO). Devuelve los nombres de los que se instalaron."""
    import json
    root = root or installers_root()
    try:
        have = json.loads(detect(vm))
    except Exception:  # noqa: BLE001
        return []
    p = plan(root)
    keys = {"DxO PureRAW": "dxo", "Imagen AI": "imagen", "Retouch4me Arams": "retouch4me", "CineSlide": "cineslide"}
    falta = [name for name, k in keys.items() if not have.get(name) and p.get(k)]
    if not falta:
        return []
    log("Instalando lo que se añadió a «Instaladores» después de guardar la base: " + ", ".join(falta) + "…")
    todo = ("python", "wheels", "rawflow", "rawflow_exe", "dxo", "imagen", "adobe", "retouch4me", "cineslide")
    res = provision(vm, log, root=root, tidy=True, skip=tuple(k for k in todo if k not in {keys[n] for n in falta}))
    for k, v in res.items():
        log(f"   {k}: {v}")
    return falta


def finish_setup(vm: str, log: Log = print) -> dict:
    """Último paso de «Instalar máquina virtual»: escritorio y barra con todos los programas, reinicio (la barra queda
    fija), lo que ve RawFlow y la base guardada (la próxima vez: copia en segundos con las sesiones iniciadas)."""
    if not vm_running(vm):
        log(APAGADA)
        return {"Base": "pendiente: " + APAGADA}
    res = {"Escritorio y barra": organize_desktop(vm, log)}
    res["Reinicio"] = "ok" if restart_windows(vm, log) else "Windows tardó en volver"
    res["RawFlow ve"] = detect(vm)
    try:
        make_base(vm, log)
        res["Base"] = "ok"
    except Exception as e:  # noqa: BLE001
        res["Base"] = f"error: {e}"
    start_vm(vm, log)
    return res


# --------------------------------------------------------------------------- escritorio y barra de tareas ordenados
_ORDEN_PS = r"""
function Key($p) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }   # New-Item -Force BORRA una clave que ya existe
$sm = 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs'
$pub = 'C:\Users\Public\Desktop'
$sh = New-Object -ComObject WScript.Shell
function Lnk($path, $target, $dir) { $s = $sh.CreateShortcut($path); $s.TargetPath = $target; $s.WorkingDirectory = $dir; $s.Save() }
# 1) RawFlow: acceso en el menú Inicio (la barra de tareas solo admite accesos del menú Inicio)
$rf = @('C:\Program Files\RawFlow\RawFlow.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($rf) { Lnk "$sm\RawFlow.lnk" $rf (Split-Path $rf) }
elseif (Test-Path 'C:\RawFlow\.venv\Scripts\pythonw.exe') { $s = $sh.CreateShortcut("$sm\RawFlow.lnk"); $s.TargetPath = 'C:\RawFlow\.venv\Scripts\pythonw.exe'; $s.Arguments = '"C:\RawFlow\RawFlow.py"'; $s.WorkingDirectory = 'C:\RawFlow'; $s.Save() }
# 2) Programas: su acceso del menú Inicio; si el instalador no lo creó, se crea con su .exe
$roots = @($sm, 'C:\Users\*\AppData\Roaming\Microsoft\Windows\Start Menu\Programs')
function FindLnk($pat) { Get-ChildItem $roots -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.BaseName -match $pat -and $_.BaseName -notmatch 'desinstal|uninstall|ayuda|help|manual' } | Select-Object -First 1 }
function FindExe($pats) { foreach ($p in $pats) { $h = Get-ChildItem $p -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'unins|uninstall|helper|update' } | Select-Object -First 1; if ($h) { return $h.FullName } }; return $null }
$apps = [ordered]@{
  'RawFlow'          = @('^RawFlow$', @());
  'Imagen AI'        = @('imagen', @('C:\Program Files\ImagenAI\ImagenAI.exe', 'C:\Users\*\AppData\Local\Programs\*magen*\ImagenAI.exe'));
  'DxO PureRAW'      = @('pureraw', @('C:\Program Files\DxO\*PureRAW*\*PureRAW*.exe'));
  'Retouch4me Arams' = @('arams|retouch4me', @('C:\Program Files\Retouch4me Arams\Retouch4me Arams.exe', 'C:\Users\*\AppData\Local\Programs\*arams*\Retouch4me Arams.exe'));
  'CineSlide'        = @('^CineSlide$', @())
}
$pins = @()
foreach ($n in $apps.Keys) {
  $l = FindLnk $apps[$n][0]
  if (-not $l) { $e = FindExe $apps[$n][1]; if ($e) { Lnk "$sm\$n.lnk" $e (Split-Path $e); $l = Get-Item "$sm\$n.lnk" } }
  if ($l) {
    $pins += $l.FullName
    Copy-Item $l.FullName "$pub\$n.lnk" -Force          # en el escritorio, con el mismo nombre de siempre
    'PROGRAMA ' + $n
  }
}
# 3) Escritorio ordenado: solo los programas de RawFlow (y la papelera)
Get-ChildItem $pub, 'C:\Users\*\Desktop' -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object {
  $keep = $false; foreach ($n in $apps.Keys) { if ($_.BaseName -eq $n) { $keep = $true } }
  -not $keep } | Remove-Item -Force -ErrorAction SilentlyContinue
# 4) Barra de tareas: SOLO esos programas (sustituye los anclados de Windows)
New-Item -ItemType Directory -Force -Path 'C:\ProgramData\RawFlow' | Out-Null
$x = @('<?xml version="1.0" encoding="utf-8"?>',
 '<LayoutModificationTemplate xmlns="http://schemas.microsoft.com/Start/2014/LayoutModification" xmlns:defaultlayout="http://schemas.microsoft.com/Start/2014/FullDefaultLayout" xmlns:start="http://schemas.microsoft.com/Start/2014/StartLayout" xmlns:taskbar="http://schemas.microsoft.com/Start/2014/TaskbarLayout" Version="1">',
 '  <CustomTaskbarLayoutCollection PinListPlacement="Replace">', '    <defaultlayout:TaskbarLayout>', '      <taskbar:TaskbarPinList>')
foreach ($p in $pins) { $x += '        <taskbar:DesktopApp DesktopApplicationLinkPath="' + $p + '" />' }
$x += @('      </taskbar:TaskbarPinList>', '    </defaultlayout:TaskbarLayout>', '  </CustomTaskbarLayoutCollection>', '</LayoutModificationTemplate>')
$x | Set-Content -Path 'C:\ProgramData\RawFlow\barra.xml' -Encoding UTF8
$pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer'
Key $pol
Set-ItemProperty $pol -Name StartLayoutFile -Value 'C:\ProgramData\RawFlow\barra.xml' -Type ExpandString
Set-ItemProperty $pol -Name LockedStartLayout -Value 1 -Type DWord
Key 'HKLM:\SOFTWARE\Policies\Microsoft\Dsh'
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Dsh' -Name AllowNewsAndInterests -Value 0 -Type DWord   # sin widgets
Key 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search'
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Name SearchOnTaskbarMode -Value 0 -Type DWord   # sin buscador
Key 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot'
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot' -Name TurnOffWindowsCopilot -Value 1 -Type DWord
# Menú Inicio: anclados solo los de RawFlow, Configuración y el Explorador (fuera los reclamos de WhatsApp, juegos…)
$sp = @(); foreach ($p in $pins) { $sp += @{ desktopAppLink = $p } }
$sp += @{ packagedAppId = 'windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel' }
$sp += @{ desktopAppLink = '%APPDATA%\Microsoft\Windows\Start Menu\Programs\File Explorer.lnk' }
$stk = 'HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\Start'
Key $stk
Set-ItemProperty $stk -Name ConfigureStartPins -Value (@{ pinnedList = $sp } | ConvertTo-Json -Compress -Depth 4) -Type String
Set-ItemProperty $stk -Name ConfigureStartPins_ProviderSet -Value 1 -Type DWord
& gpupdate.exe /target:computer /force | Out-Null
# 5) Sin buscador, vista de tareas, chat ni Copilot en la barra (cuenta del usuario conectado)
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if ($u) {
  $sid = (New-Object System.Security.Principal.NTAccount($u)).Translate([System.Security.Principal.SecurityIdentifier]).Value
  $adv = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
  foreach ($kv in @(@('ShowTaskViewButton', 0), @('TaskbarMn', 0), @('ShowCopilotButton', 0), @('TaskbarDa', 0))) {
    try { Set-ItemProperty $adv -Name $kv[0] -Value $kv[1] -Type DWord -ErrorAction Stop } catch {} }
  $srch = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Search"
  Key $srch
  Set-ItemProperty $srch -Name SearchboxTaskbarMode -Value 0 -Type DWord
  # los anclados que Windows trae: fuera (se vuelven a poner solo los de RawFlow al iniciar sesión)
  Remove-Item "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband" -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem "C:\Users\$($u.Split('\')[-1])\AppData\Roaming\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar" -Filter *.lnk -ErrorAction SilentlyContinue | Remove-Item -Force
}
Get-Process explorer -ErrorAction SilentlyContinue | Stop-Process -Force   # Windows lo vuelve a abrir solo, ya ordenado
'ORDEN_OK ' + $pins.Count
"""


def organize_desktop(vm: str, log: Log = print) -> str:
    """Escritorio y barra de tareas de Windows con SOLO lo de RawFlow: RawFlow, Imagen AI, DxO, Retouch4me y CineSlide
    (los que estén instalados). Fuera el buscador, la vista de tareas, los widgets, Copilot y los anclados de Windows.
    Los anclados nuevos se ven del todo al siguiente inicio de sesión (o reinicio)."""
    rc, out = ps(vm, _ORDEN_PS, 300)
    progs = [line[9:] for line in out.splitlines() if line.startswith("PROGRAMA ")]
    ok = "ORDEN_OK" in out
    if ok:
        log("Escritorio y barra de tareas ordenados: " + ", ".join(progs))
    return ("ok: " + ", ".join(progs)) if ok else ("error: " + out.strip()[-300:])


# --------------------------------------------------------------------------- Windows limpio (siempre igual)
QUITAR = (  # apps de reclamo que Windows trae y RawFlow no usa (se quedan: Edge, Tienda, Fotos, Reproductor multimedia,
            # Paint, Recortes, Bloc de notas, Calculadora, Terminal, Seguridad y los códecs de imagen y vídeo)
    "Clipchamp.Clipchamp", "Microsoft.BingNews", "Microsoft.BingSearch", "Microsoft.BingWeather", "Microsoft.BingTranslator",
    "Microsoft.Edge.GameAssist", "Microsoft.GetHelp", "Microsoft.Getstarted", "Microsoft.MicrosoftOfficeHub",
    "Microsoft.MicrosoftSolitaireCollection", "Microsoft.MicrosoftStickyNotes", "Microsoft.MicrosoftJournal",
    "Microsoft.OutlookForWindows", "Microsoft.WindowsCommunicationsApps", "Microsoft.People", "Microsoft.Todos",
    "Microsoft.PowerAutomateDesktop", "Microsoft.Windows.DevHome", "Microsoft.WindowsAlarms", "Microsoft.WindowsCamera",
    "Microsoft.WindowsFeedbackHub", "Microsoft.WindowsMaps", "Microsoft.WindowsSoundRecorder", "Microsoft.GamingApp",
    "Microsoft.Xbox.TCUI", "Microsoft.XboxApp", "Microsoft.XboxGameOverlay", "Microsoft.XboxGamingOverlay",
    "Microsoft.XboxIdentityProvider", "Microsoft.XboxSpeechToTextOverlay", "Microsoft.YourPhone", "Microsoft.ZuneVideo",
    "Microsoft.549981C3F5F10", "Microsoft.Copilot", "Microsoft.Windows.Ai.Copilot.Provider", "Microsoft.SkypeApp",
    "Microsoft.Office.OneNote", "Microsoft.MixedReality.Portal", "Microsoft.Microsoft3DViewer", "Microsoft.OneDriveSync",
    "MicrosoftCorporationII.QuickAssist", "MicrosoftCorporationII.MicrosoftFamily", "MicrosoftWindows.CrossDevice",
    "MicrosoftWindows.Client.WebExperience", "Microsoft.WidgetsPlatformRuntime", "MSTeams", "MicrosoftTeams",
)

_LIMPIEZA_PS = r"""
function Key($p) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }
# 1) Apps que no sirven: fuera para todos los usuarios y para los que se creen después
$prov = @(Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue)
$hecho = @(); $fallo = @()
foreach ($n in $QUITAR) {
  $pk = @(Get-AppxPackage -AllUsers -Name $n -ErrorAction SilentlyContinue)
  $mal = $false
  foreach ($p in $pk) { try { Remove-AppxPackage -Package $p.PackageFullName -AllUsers -ErrorAction Stop } catch { $mal = $true } }
  foreach ($q in ($prov | Where-Object { $_.DisplayName -eq $n })) {
    try { Remove-AppxProvisionedPackage -Online -PackageName $q.PackageName -AllUsers -ErrorAction Stop | Out-Null }
    catch { try { Remove-AppxProvisionedPackage -Online -PackageName $q.PackageName -ErrorAction Stop | Out-Null } catch {} } }
  if ($mal) { $fallo += $n } elseif ($pk.Count -gt 0) { $hecho += $n }
}
# 2) Que Windows no vuelva a meter apps de reclamo, sugerencias ni «terminemos de configurar tu equipo»
$cc = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Key $cc
foreach ($n in 'DisableWindowsConsumerFeatures','DisableConsumerAccountStateContent','DisableCloudOptimizedContent','DisableSoftLanding') {
  Set-ItemProperty $cc -Name $n -Value 1 -Type DWord }
function UserTweaks($root) {
  $cdm = "$root\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Key $cdm
  foreach ($n in 'ContentDeliveryAllowed','FeatureManagementEnabled','OemPreInstalledAppsEnabled','PreInstalledAppsEnabled',
      'PreInstalledAppsEverEnabled','SilentInstalledAppsEnabled','SoftLandingEnabled','SubscribedContentEnabled',
      'SystemPaneSuggestionsEnabled','RotatingLockScreenOverlayEnabled','SubscribedContent-310093Enabled',
      'SubscribedContent-338387Enabled','SubscribedContent-338388Enabled','SubscribedContent-338389Enabled',
      'SubscribedContent-338393Enabled','SubscribedContent-353694Enabled','SubscribedContent-353696Enabled',
      'SubscribedContent-353698Enabled') { Set-ItemProperty $cdm -Name $n -Value 0 -Type DWord }
  $upe = "$root\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement"; Key $upe
  Set-ItemProperty $upe -Name ScoobeSystemSettingEnabled -Value 0 -Type DWord
  $adv = "$root\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Key $adv
  foreach ($kv in @(@('Start_IrisRecommendations', 0), @('Start_AccountNotifications', 0), @('TaskbarDa', 0),
                    @('TaskbarMn', 0), @('ShowCopilotButton', 0), @('ShowTaskViewButton', 0))) {
    Set-ItemProperty $adv -Name $kv[0] -Value $kv[1] -Type DWord }
  $run = "$root\Software\Microsoft\Windows\CurrentVersion\Run"
  foreach ($n in 'OneDrive','OneDriveSetup') { Remove-ItemProperty $run -Name $n -ErrorAction SilentlyContinue }
}
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if ($u) { $sid = (New-Object System.Security.Principal.NTAccount($u)).Translate([System.Security.Principal.SecurityIdentifier]).Value
          UserTweaks "Registry::HKEY_USERS\$sid" }
& reg.exe load 'HKU\RFDefault' 'C:\Users\Default\NTUSER.DAT' 2>&1 | Out-Null       # y para cualquier usuario nuevo
if (Test-Path 'Registry::HKEY_USERS\RFDefault') {
  UserTweaks 'Registry::HKEY_USERS\RFDefault'
  [gc]::Collect(); [gc]::WaitForPendingFinalizers(); Start-Sleep 1
  & reg.exe unload 'HKU\RFDefault' 2>&1 | Out-Null }
# 3) Edge: sin bienvenida, sin barra lateral ni reclamos, descargas directas a Descargas y sin reabrir pestañas viejas
$edge = 'HKLM:\SOFTWARE\Policies\Microsoft\Edge'; Key $edge
foreach ($kv in @(@('HideFirstRunExperience', 1), @('PromptForDownloadLocation', 0), @('RestoreOnStartup', 5),
                  @('HideRestoreDialogEnabled', 1), @('StartupBoostEnabled', 0), @('BackgroundModeEnabled', 0),
                  @('DefaultBrowserSettingEnabled', 0), @('ShowRecommendationsEnabled', 0), @('HubsSidebarEnabled', 0),
                  @('EdgeShoppingAssistantEnabled', 0), @('PersonalizationReportingEnabled', 0),
                  @('SpotlightExperiencesAndRecommendationsEnabled', 0), @('NewTabPageContentEnabled', 0),
                  @('NewTabPageQuickLinksEnabled', 0), @('NewTabPageHideDefaultTopSites', 1), @('TranslateEnabled', 0),
                  @('Microsoft365CopilotChatIconEnabled', 0), @('CopilotPageContext', 0), @('BrowserSignin', 0))) {
  Set-ItemProperty $edge -Name $kv[0] -Value $kv[1] -Type DWord }
# 4) OneDrive: fuera (no hace falta en esta máquina)
$odp = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive'; Key $odp
Set-ItemProperty $odp -Name DisableFileSyncNGSC -Value 1 -Type DWord
Get-Process OneDrive -ErrorAction SilentlyContinue | Stop-Process -Force
$od = Get-ChildItem 'C:\Windows\System32\OneDriveSetup.exe', 'C:\Windows\SysWOW64\OneDriveSetup.exe',
        'C:\Program Files\Microsoft OneDrive\*\OneDriveSetup.exe', 'C:\Users\*\AppData\Local\Microsoft\OneDrive\*\OneDriveSetup.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
$odHay = (Test-Path 'C:\Program Files\Microsoft OneDrive\OneDrive.exe') -or [bool](Get-ChildItem 'C:\Users\*\AppData\Local\Microsoft\OneDrive\OneDrive.exe' -ErrorAction SilentlyContinue)
if ($od -and $odHay) { 'ONEDRIVE ' + $od.FullName }
'QUITADOS ' + ($hecho -join ',')
'FALLOS ' + ($fallo -join ',')
'LIMPIO_OK'
"""


def debloat(vm: str, log: Log = print) -> str:
    """Quita de Windows lo que no sirve para RawFlow (juegos, Xbox, Outlook, Teams, noticias, widgets, OneDrive…) y
    evita que vuelva. Se hace en cada máquina nueva, así todas quedan igual. Se puede repetir sin problema."""
    log("Quitando de Windows lo que no sirve…")
    script = "$QUITAR = @(" + ",".join(f"'{n}'" for n in QUITAR) + ")\n" + _LIMPIEZA_PS
    rc, out = ps(vm, script, 1800)
    import re
    quit_ = [x for x in (re.search(r"QUITADOS (.*)", out) or [None, ""])[1].strip().split(",") if x]
    fail = [x for x in (re.search(r"FALLOS (.*)", out) or [None, ""])[1].strip().split(",") if x]
    m = re.search(r"ONEDRIVE (.+)", out)
    if m:                                             # OneDrive va por usuario: se desinstala desde su sesión
        setup = m.group(1).strip()
        ps(vm, f"Start-Process -FilePath '{setup}' -ArgumentList '/uninstall','/allusers' -Wait -WindowStyle Hidden", 600)
        open_in_session(vm, setup, "/uninstall")
        quit_.append("OneDrive")
    if "LIMPIO_OK" not in out:
        return "error: " + out.strip()[-300:]
    log(f"   fuera {len(quit_)} apps" + (f" (no se pudieron quitar: {', '.join(fail)})" if fail else ""))
    return f"ok: fuera {len(quit_)} apps" + (f"; quedan {', '.join(fail)}" if fail else "")


# --------------------------------------------------------------------------- inicio de sesión guiado (una página)
def open_page(vm: str, url: str) -> str:
    """UNA sola ventana de Edge, grande, con UNA sola página (cierra las demás que hubiera)."""
    ps(vm, "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force", 60)
    time.sleep(2)
    rc, out = ps(vm, "(Get-ChildItem 'C:\\Program Files*\\Microsoft\\Edge\\Application\\msedge.exe' "
                     "-ErrorAction SilentlyContinue | Select-Object -First 1).FullName", 60)
    edge = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not edge.lower().endswith("msedge.exe"):
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    return open_in_session(vm, edge, f"--new-window --start-maximized {url}", elevated=False)


def wait_download(vm: str, words: tuple, minutes: int = 30, log: Log = print) -> Optional[str]:
    """Espera a que en Descargas de Windows aparezca, ya ENTERO, un instalador cuyo nombre lleve una de `words`
    (Edge lo baja como .crdownload y lo renombra al acabar; además el tamaño tiene que quedarse quieto)."""
    import re
    pat = "|".join(re.escape(w) for w in words)
    last, t0 = None, time.monotonic()
    while time.monotonic() - t0 < minutes * 60:
        if not vm_running(vm):
            return None
        rc, out = ps(vm, f"""
$f = Get-ChildItem 'C:\\Users\\*\\Downloads\\*' -Include *.exe,*.msi,*.zip -File -ErrorAction SilentlyContinue |
     Where-Object {{ $_.Name -match '{pat}' }} | Sort-Object LastWriteTime | Select-Object -Last 1
$part = @(Get-ChildItem 'C:\\Users\\*\\Downloads\\*' -Include *.crdownload,*.partial -File -ErrorAction SilentlyContinue).Count
if ($f) {{ 'ARCHIVO ' + $f.FullName + '|' + $f.Length + '|' + $part }} else {{ 'NADA ' + $part }}
""", 90)
        m = re.search(r"ARCHIVO (.+)\|(\d+)\|(\d+)", out)
        if m and m.group(3) == "0":
            cur = (m.group(1).strip(), int(m.group(2)))
            if cur == last and cur[1] > 1_000_000:
                log(f"   descargado {cur[0].split(chr(92))[-1]} ({cur[1] / 1e6:,.0f} MB)")
                return cur[0]
            last = cur
        time.sleep(5)
    return None


def installer_kind(path: Path) -> str:
    """Qué tipo de instalador es (para saber cómo instalarlo sin ventanas): nsis | inno | burn | msi | installshield |
    advanced | desconocido."""
    p = Path(path)
    if p.suffix.lower() == ".msi":
        return "msi"
    with open(p, "rb") as f:
        head = f.read(16 << 20)
    for kind, marks in (("inno", (b"Inno Setup", b"InnoSetupLdr")), ("nsis", (b"Nullsoft", b"NSIS Error")),
                        ("burn", (b"WixBundle", b".wixburn", b"WixBurn")), ("installshield", (b"InstallShield",)),
                        ("advanced", (b"Advanced Installer",))):
        if any(m in head for m in marks):
            return kind
    return "desconocido"


SILENT_BY_KIND = {
    "nsis": ["/S"],
    "inno": ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
    "burn": ["/quiet", "/norestart"],
    "installshield": ["/s", "/v/qn"],
    "advanced": ["/exenoui", "/qn", "/norestart"],
    "msi": ["/qn", "/norestart"],
}

DXO_LOGIN = "https://shop.dxo.com/en/customer/account/"   # tienda de DxO: tras iniciar sesión, tus productos y descargas
GUIA = STAGE + "\\guia.ps1"

# Aviso grande abajo en la pantalla de Windows con el paso que toca; abre el programa y espera a que el usuario cierre
# su ventana (o a que Python lo pare). No roba el foco: se escribe y se hace clic en el programa, no en el aviso.
_GUIA_PS = r"""
param([string]$TextoArchivo, [string]$Exe = '', [string]$Proc = '', [string]$Estado, [string]$Alto, [int]$Minutos = 45)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$Texto = [IO.File]::ReadAllText($TextoArchivo, [Text.Encoding]::UTF8)
try {                       # ventana que nunca se activa (ni al hacer clic): el foco sigue en el programa
  Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @'
public class RFAviso : System.Windows.Forms.Form {
  protected override bool ShowWithoutActivation { get { return true; } }
  protected override System.Windows.Forms.CreateParams CreateParams {
    get { System.Windows.Forms.CreateParams cp = base.CreateParams; cp.ExStyle |= 0x08000000 | 0x00000080 | 0x00000008; return cp; } }
}
'@
  $f = New-Object RFAviso
} catch { $f = New-Object System.Windows.Forms.Form }
$f.FormBorderStyle = 'None'; $f.TopMost = $true; $f.ShowInTaskbar = $false; $f.StartPosition = 'Manual'
$f.BackColor = [System.Drawing.Color]::FromArgb(28, 30, 36)
$w = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$f.Width = [Math]::Min(1000, $w.Width - 32); $f.Height = 66
$f.Left = $w.Left + [int](($w.Width - $f.Width) / 2); $f.Top = $w.Bottom - $f.Height - 12
$l = New-Object System.Windows.Forms.Label
$l.Dock = 'Fill'; $l.ForeColor = [System.Drawing.Color]::White; $l.TextAlign = 'MiddleCenter'
$l.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 13); $l.Text = $Texto
$f.Controls.Add($l)
$f.Show(); [System.Windows.Forms.Application]::DoEvents()
if ($Exe) { Start-Process -FilePath $Exe }
$visto = $false; $sin = 0; $t0 = Get-Date
while (((Get-Date) - $t0).TotalMinutes -lt $Minutos) {
  for ($i = 0; $i -lt 4; $i++) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 250 }
  if (Test-Path $Alto) { break }
  if ($Proc) {
    $v = @(Get-Process -Name $Proc -ErrorAction SilentlyContinue)
    $vis = @($v | Where-Object { $_.MainWindowHandle -ne 0 })
    if ($vis.Count -gt 0) { $visto = $true; $sin = 0 } elseif ($visto) { $sin++ }
    $lim = if ($v.Count -eq 0) { 4 } else { 20 }      # cerrado del todo: 4 s; sigue vivo sin ventana (cargando o
    if ($visto -and $sin -ge $lim) { Set-Content -Path $Estado -Value 'CERRADO'; break }   # en la bandeja): 20 s
  }
}
$f.Dispose()
"""


def _guest_write(vm: str, remote: str, text: str) -> None:
    """Escribe un archivo de texto en Windows (UTF-8 con BOM: PowerShell lee bien las tildes)."""
    import base64
    b = base64.b64encode(text.encode("utf-8")).decode()
    ps(vm, f"New-Item -ItemType Directory -Force -Path '{STAGE}' | Out-Null; "
           f"[IO.File]::WriteAllText('{remote}', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b}')), "
           "(New-Object System.Text.UTF8Encoding $true))", 120)


def guide_show(vm: str, texto: str, exe: str = "", proc: str = "", minutos: int = 45) -> str:
    """Pone el aviso del paso en Windows (y abre `exe`). Devuelve una marca para esperar o quitar ese aviso."""
    import uuid
    tag = uuid.uuid4().hex[:8]
    _guest_write(vm, GUIA, _GUIA_PS)
    _guest_write(vm, f"{STAGE}\\guia-{tag}.txt", texto)
    extra = (f'-Exe "{exe}" ' if exe else "") + (f'-Proc "{proc}" ' if proc else "")   # nunca un argumento vacío
    open_in_session(vm, CONHOST, "--headless powershell.exe " + (
                    f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{GUIA}" '
                    f'-TextoArchivo "{STAGE}\\guia-{tag}.txt" {extra}'
                    f'-Estado "{STAGE}\\guia-{tag}.estado" -Alto "{STAGE}\\guia-{tag}.alto" -Minutos {minutos}'),
                    elevated=False)                   # lo que abre el aviso va como un doble clic normal
    return tag


def guide_closed(vm: str, tag: str) -> bool:
    rc, out = ps(vm, f"if (Test-Path '{STAGE}\\guia-{tag}.estado') {{ 'CERRADO' }} else {{ 'ABIERTO' }}", 60)
    return "CERRADO" in out


def guide_hide(vm: str, tag: str) -> None:
    ps(vm, f"Set-Content -Path '{STAGE}\\guia-{tag}.alto' -Value 'ALTO'", 60)


def guided_setup(vm: str, log: Log = print, root: Optional[Path] = None, minutos: int = 45,
                 only: Optional[tuple] = None) -> dict:
    """Inicio de sesión guiado, UNA cosa cada vez y sin pestañas de más:
    1) DxO, si su instalador aún no está en el Mac: una sola página (la tienda de DxO) → iniciar sesión → «Descargar».
       En cuanto baja, se guarda en «Instaladores» del Mac y se instala solo (la próxima máquina ya no lo pide).
    2) Cada programa que pide cuenta (DxO PureRAW, Retouch4me Arams, Imagen AI) se abre solo, de uno en uno, con un
       aviso abajo que dice qué hacer; al cerrar su ventana se abre el siguiente.
    `only`: en una copia de la base, solo los programas recién instalados (los demás ya traen la sesión)."""
    import json
    root = root or installers_root()
    res: dict = {}

    def found() -> dict:
        try:
            return json.loads(detect(vm))
        except Exception:  # noqa: BLE001
            return {}

    progs = found()
    pasos = []                                        # (nombre, exe) de los que piden iniciar sesión
    web = not progs.get("DxO PureRAW") and not plan(root)["dxo"]
    total = (1 if web else 0) + sum(1 for k in ("DxO PureRAW", "Retouch4me Arams", "Imagen AI")
                                    if (progs.get(k) and (only is None or k in only)) or k == "DxO PureRAW")
    n = 0
    dxo_nuevo = False
    if not progs.get("DxO PureRAW"):
        if web:
            n += 1
            tag = guide_show(vm, f"Paso {n} de {total} · DxO: inicia sesión y pulsa «Download» en DxO PureRAW 6 para "
                                 "Windows. Yo lo instalo solo.", minutos=minutos + 5)
            open_page(vm, DXO_LOGIN)
            log(f"Paso {n} de {total} · en la ventana de Windows: inicia sesión en DxO y pulsa «Download» en "
                "DxO PureRAW 6 para Windows (lo instalo yo)…")
            path = wait_download(vm, ("pureraw", "dxo"), minutos, log)
            if not vm_running(vm):
                log(APAGADA)
                return dict(res, Windows=APAGADA)
            guide_hide(vm, tag)
            ps(vm, "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force", 60)
            if not path:
                res["DxO PureRAW"] = "no se descargó (a tiempo)"
            else:
                collect_downloads(vm, log, root)      # al Mac: la próxima máquina lo instala sin pedir nada
        inst = plan(root)["dxo"]
        if inst and installer_kind(inst) in SILENT_BY_KIND:
            log(f"Instalando DxO PureRAW ({installer_kind(inst)}, sin ventanas)…")
            r = provision(vm, log, root=root, tidy=False,
                          skip=("python", "wheels", "rawflow", "rawflow_exe", "imagen", "adobe", "retouch4me", "cineslide"))
            res["DxO PureRAW"] = r.get("DxO PureRAW", "?")
            progs = found()
        if inst and not progs.get("DxO PureRAW"):     # instalador que no se deja instalar solo: lo instala el usuario
            srv = Servidor(root)
            try:
                remote = f"{STAGE}\\{inst.name}"
                push(vm, inst, remote, log, server=srv)
            finally:
                srv.close()
            tag = guide_show(vm, "Instala DxO PureRAW: acepta y pulsa «Siguiente» hasta «Finalizar».", exe=remote,
                             proc=inst.stem, minutos=minutos)
            t0 = time.monotonic()
            while time.monotonic() - t0 < minutos * 60 and not guide_closed(vm, tag):
                time.sleep(5)
            guide_hide(vm, tag)
            progs = found()
            res["DxO PureRAW"] = "ok (a mano)" if progs.get("DxO PureRAW") else "no se instaló"
        dxo_nuevo = bool(progs.get("DxO PureRAW"))
    for k in ("DxO PureRAW", "Retouch4me Arams", "Imagen AI"):
        if progs.get(k) and (only is None or k in only or (k == "DxO PureRAW" and dxo_nuevo)):
            pasos.append((k, progs[k]))
    total = n + len(pasos)
    que = {"DxO PureRAW": "actívalo con tu cuenta de DxO",
           "Retouch4me Arams": "inicia sesión e instala tus plugins",
           "Imagen AI": "inicia sesión"}
    for k, exe in pasos:
        n += 1
        proc = Path(exe.replace("\\", "/")).stem
        ps(vm, f"Get-Process -Name '{proc}' -ErrorAction SilentlyContinue | Stop-Process -Force", 60)
        tag = guide_show(vm, f"Paso {n} de {total} · {k}: {que[k]}. Después cierra su ventana y se abre el siguiente.",
                         exe=exe, proc=proc, minutos=minutos)
        log(f"Paso {n} de {total} · en la ventana de Windows: {k}, {que[k]}; después cierra su ventana…")
        t0 = time.monotonic()
        while time.monotonic() - t0 < minutos * 60 and not guide_closed(vm, tag):
            if not vm_running(vm):
                log(APAGADA)
                return dict(res, Windows=APAGADA)
            time.sleep(5)
        guide_hide(vm, tag)
        res[k] = "hecho" if guide_closed(vm, tag) else "sin cerrar (a tiempo)"
    tag = guide_show(vm, "Listo: programas instalados y con tu sesión iniciada.", minutos=1)
    time.sleep(8)
    guide_hide(vm, tag)
    return res


def update_exe(vm: str, log: Log = print, root: Optional[Path] = None) -> str:
    """En una copia de la base: instala la última RawFlow.exe de «Instaladores» (las sesiones de los programas ya vienen
    dentro de la base)."""
    root = root or installers_root()
    pl = plan(root)
    if not pl.get("rawflow_exe"):
        return "sin RawFlow.exe nuevo"
    res = provision(vm, log, root=root, skip=("python", "wheels", "rawflow", "dxo", "imagen", "adobe", "retouch4me",
                                               "cineslide"))
    return res.get("RawFlow", "?")


if __name__ == "__main__":   # .venv/bin/python -m rawflow.vm_programas compilar|instalar|limpiar|ordenar|detectar [vm]
    import sys
    from . import __version__
    vm_name = sys.argv[2] if len(sys.argv) > 2 else "RawFlow Windows"
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "compilar":
        print(build_exe(vm_name, __version__))
    elif cmd == "instalar":
        for k, v in provision(vm_name).items():
            print(f"  {k}: {v}")
        print(detect(vm_name))
    elif cmd == "limpiar":
        print(debloat(vm_name))
    elif cmd == "ordenar":
        print(organize_desktop(vm_name))
    elif cmd == "detectar":
        print(detect(vm_name))

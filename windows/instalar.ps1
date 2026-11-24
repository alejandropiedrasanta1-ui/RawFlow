# Instala RawFlow en esta PC con Windows: SIN internet y SIN permisos de administrador.
# Usa un Python 3.10+ que ya funcione (de esta arquitectura) o instala el que viene aquí, solo para tu usuario.
# RawFlow queda en %LOCALAPPDATA%\Programs\RawFlow, con accesos directos en el Escritorio y en el menú Inicio.
# Volver a ejecutarlo actualiza RawFlow (no duplica nada).

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $env:LOCALAPPDATA 'Programs\RawFlow'

function Paso([string]$t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Green }
function Falla([string]$t) {
    Write-Host ''
    Write-Host "ERROR: $t" -ForegroundColor Red
    Write-Host 'Si no sabes qué hacer, haz una foto de esta ventana y mándala.' -ForegroundColor Yellow
    Read-Host 'Pulsa Intro para cerrar' | Out-Null
    exit 1
}

function Python-Bueno([string]$arch) {
    $cands = @()
    $cands += Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') -ErrorAction SilentlyContinue
    $cands += Get-ChildItem (Join-Path $env:ProgramFiles 'Python3*\python.exe') -ErrorAction SilentlyContinue
    foreach ($c in ($cands | Sort-Object FullName -Descending)) {
        try {
            $out = & $c.FullName -c "import sys, sysconfig, venv; print(sys.version_info[0]*100+sys.version_info[1], sysconfig.get_platform())" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $parts = ("$out").Trim() -split ' '
                if ([int]$parts[0] -ge 310 -and $parts[1] -eq "win-$arch") { return $c.FullName }
            }
        } catch { }
    }
    return $null
}

Write-Host 'RawFlow · instalador para Windows' -ForegroundColor Yellow
try {
    $arch = 'amd64'
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { $arch = 'arm64' }
    $libs = Join-Path $here "librerias-win_$arch"
    if (-not (Test-Path $libs)) { Falla "Falta la carpeta 'librerias-win_$arch' junto al instalador (descomprime el zip entero)." }
    if (-not (Test-Path (Join-Path $here 'RawFlow\RawFlow.py'))) { Falla "Falta la carpeta 'RawFlow' junto al instalador (descomprime el zip entero)." }

    Paso "Buscando Python (equipo $arch)..."
    $py = Python-Bueno $arch
    if (-not $py) {
        $inst = Get-ChildItem (Join-Path $here 'python') -Filter "python-*-$arch.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $inst) { Falla "Falta el instalador de Python para $arch en la carpeta 'python'." }
        Paso "Instalando $($inst.Name), solo para tu usuario (1-2 minutos)..."
        $p = Start-Process -FilePath $inst.FullName -Wait -PassThru -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0', 'Include_test=0', 'Include_doc=0', 'Shortcuts=0', 'AssociateFiles=0'
        if ($p.ExitCode -ne 0) { Falla "El instalador de Python terminó con el código $($p.ExitCode)." }
        $py = Python-Bueno $arch
        if (-not $py) { Falla 'Python se instaló pero no responde. Reinicia la PC y vuelve a abrir «Instalar RawFlow».' }
    }
    Write-Host "    $py"

    Paso 'Cerrando RawFlow si estaba abierto...'
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*RawFlow.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Paso "Copiando RawFlow a $app ..."
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    robocopy (Join-Path $here 'RawFlow') $app /E /XD .venv __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Falla "No se pudo copiar RawFlow (robocopy $LASTEXITCODE)." }

    Paso 'Preparando sus librerías (PySide6 y psutil, sin internet)...'
    $venv = Join-Path $app '.venv'
    $vpy = Join-Path $venv 'Scripts\python.exe'
    if (Test-Path $vpy) {
        & $vpy -c "import sys" 2>$null
        if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue }   # entorno roto: de cero
    }
    if (-not (Test-Path $vpy)) { & $py -m venv $venv }
    if (-not (Test-Path $vpy)) { Falla 'No se pudo crear el entorno de Python para RawFlow.' }
    & $vpy -m pip install --no-index --find-links $libs --disable-pip-version-check -r (Join-Path $here 'requisitos-windows.txt')
    if ($LASTEXITCODE -ne 0) { Falla "No se pudieron instalar las librerías (pip $LASTEXITCODE)." }
    & $vpy -c "import PySide6.QtWidgets, psutil" 2>$null
    if ($LASTEXITCODE -ne 0) { Falla 'Las librerías se instalaron pero no cargan.' }

    Paso 'Creando los accesos directos (Escritorio y menú Inicio)...'
    $wsh = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        if (-not $dir) { continue }
        $s = $wsh.CreateShortcut((Join-Path $dir 'RawFlow.lnk'))
        $s.TargetPath = Join-Path $venv 'Scripts\pythonw.exe'
        $s.Arguments = '"' + (Join-Path $app 'RawFlow.py') + '"'
        $s.WorkingDirectory = $app
        $s.IconLocation = (Join-Path $app 'rawflow\RawFlow.ico') + ',0'
        $s.Description = 'RawFlow'
        $s.Save()
    }

    Paso 'Listo: RawFlow está instalado. Se abre ahora; las próximas veces, con su icono del Escritorio.'
    Start-Process -FilePath (Join-Path $venv 'Scripts\pythonw.exe') -ArgumentList ('"' + (Join-Path $app 'RawFlow.py') + '"') -WorkingDirectory $app
    Start-Sleep -Seconds 5
    exit 0
} catch {
    Falla $_.Exception.Message
}

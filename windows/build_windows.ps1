# Compila RawFlow para Windows y crea su instalador:
#   dist\windows\RawFlow\RawFlow.exe                             (el programa; no necesita Python)
#   dist\windows\RawFlow <versión> para Windows (Instalador).exe (lo único que hay que llevar a la PC)
# Necesita: Python 3.14 (py) e Inno Setup 6 (https://jrsoftware.org/isdl.php). Las librerías se bajan solas, las últimas.
# No toca dist\RawFlow ni build\RawFlow (son los del Mac).

$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
function Paso([string]$t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Green }

$ver = [regex]::Match((Get-Content 'rawflow\__init__.py' -Raw), '__version__\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $ver) { throw 'No se pudo leer la versión de rawflow\__init__.py' }
$n = @($ver.Split('.') + '0', '0', '0')[0..3] -join ', '
Write-Host "RawFlow $ver · compilación para Windows" -ForegroundColor Yellow

Paso 'Entorno de compilación (.venv-win) con las librerías más nuevas...'
$vpy = Join-Path $raiz '.venv-win\Scripts\python.exe'
# Python 3.14 (el mismo que usa RawFlow en el Mac). Con 3.15 las pruebas de mover fotos fallaban hasta blindar una
# importación; se sigue compilando con 3.14 hasta probar 3.15 en un evento de verdad.
$pyv = '3.14'
if ((Test-Path $vpy) -and ((& $vpy -c "import sys; print('%d.%d' % sys.version_info[:2])") -ne $pyv)) {
    Remove-Item -Recurse -Force (Join-Path $raiz '.venv-win')
}
if (-not (Test-Path $vpy)) {
    if ($env:RAWFLOW_PYTHON) { & $env:RAWFLOW_PYTHON -m venv .venv-win }      # GitHub: el Python que prepara la compilación
    else { py "-$pyv" -m venv .venv-win }
}
if (-not (Test-Path $vpy)) { throw "Falta Python $pyv (https://www.python.org/downloads/)" }
& $vpy -m pip install --upgrade --disable-pip-version-check -q pip -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip no pudo instalar las librerías' }
& $vpy -c "import PySide6, psutil, PyInstaller, cv2, numpy; print('    PySide6', PySide6.__version__, '· psutil', psutil.__version__, '· PyInstaller', PyInstaller.__version__, '· OpenCV', cv2.__version__, '· numpy', numpy.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Falta alguna librería (PySide6, psutil, PyInstaller, OpenCV o numpy)' }
foreach ($m in 'face_detection_yunet_2023mar.onnx', 'face_landmarker.task', 'mobilenet_v3_large.tflite') {
    if (-not (Test-Path "rawflow\modelos\$m")) { throw "Falta el modelo de la Selección IA: rawflow\modelos\$m (ver rawflow\modelos\LEEME.txt)" }
}

Paso 'Pruebas (si alguna falla, no se compila)...'
# Como en build_mac.sh. Simulan DxO, Imagen, Bridge y Retouch4me (RAWFLOW_SIN_APPS): no tocan programas ni fotos
# reales y pasan también sin esos programas instalados (como en el servidor de GitHub). Las solo-Mac salen «saltadas».
$qpa = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONIOENCODING = 'utf-8'      # la consola de GitHub (cp1252) no sabe escribir «✓» ni «✗»
$fallo = $false
foreach ($t in 'tests\test_duplicados.py', 'tests\pruebas_rawflow.py') {
    & $vpy $t
    if ($LASTEXITCODE -ne 0) { $fallo = $true }
}
$env:QT_QPA_PLATFORM = $qpa
if ($fallo) { throw 'PRUEBAS FALLIDAS: no se compila' }

Paso 'Icono (todos los tamaños) e imágenes del instalador...'
& $vpy tools\hacer_icono.py
if ($LASTEXITCODE -ne 0) { throw 'No se pudo generar el icono' }

Paso 'Compilando RawFlow.exe...'
$vi = Join-Path $raiz 'build\windows\version.txt'
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($n), prodvers=($n), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('0C0A04B0', [
      StringStruct('CompanyName', 'RawFlow'),
      StringStruct('FileDescription', 'RawFlow'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'RawFlow'),
      StringStruct('OriginalFilename', 'RawFlow.exe'),
      StringStruct('ProductName', 'RawFlow'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [0x0C0A, 1200])])
  ]
)
"@ | Set-Content $vi -Encoding utf8
$qm = Join-Path $raiz '.venv-win\Lib\site-packages\PySide6\translations\qtbase_es.qm'   # botones «Sí / No» en español
& $vpy -m PyInstaller --noconfirm --clean --windowed --name RawFlow --log-level WARN `
    --icon "$raiz\rawflow\RawFlow.ico" --version-file $vi `
    --distpath 'dist\windows' --workpath 'build\windows\work' --specpath 'build\windows' `
    --add-data "$raiz\rawflow\cineslide_run.py;rawflow" --add-data "$raiz\rawflow\r4m_worker.js;rawflow" `
    --add-data "$qm;PySide6/translations" `
    --add-data "$raiz\rawflow\fonts;rawflow/fonts" --collect-data qtawesome --hidden-import qtawesome `
    --add-data "$raiz\rawflow\modelos;rawflow/modelos" --hidden-import cv2 --hidden-import numpy `
    --collect-submodules winrt --collect-binaries winrt `
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc `
    RawFlow.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path 'dist\windows\RawFlow\RawFlow.exe')) { throw 'No salió RawFlow.exe' }

Paso 'Creando el instalador...'
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Falta Inno Setup 6 (https://jrsoftware.org/isdl.php). RawFlow.exe sí quedó en dist\windows\RawFlow.' }
& $iscc /Qp "/DAppVer=$ver" 'windows\RawFlow.iss'
if ($LASTEXITCODE -ne 0) { throw "Inno Setup terminó con el código $LASTEXITCODE" }

$exe = Get-Item "dist\windows\RawFlow $ver para Windows (Instalador).exe"
Paso ("Listo: {0}  ({1:N0} MB)" -f $exe.FullName, ($exe.Length / 1MB))

#!/bin/bash
# Crea dist/RawFlow.app y el instalador de un clic «Instalar RawFlow.app» (dentro lleva RawFlow.app):
# doble clic → se instala en Aplicaciones, se pone en el Dock, se abre y el instalador se cierra solo.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller -q

# Icono: se dibuja con el mismo código de la app (rawflow/widgets.py)
rm -rf build/RawFlow.iconset && mkdir -p build/RawFlow.iconset
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'EOF'
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication([])
from rawflow.widgets import app_icon
ic = app_icon()
for s in (16, 32, 128, 256, 512):
    for k, suf in ((1, ""), (2, "@2x")):
        pm = ic.pixmap(256).scaled(s * k, s * k, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        pm.save(f"build/RawFlow.iconset/icon_{s}x{s}{suf}.png")
EOF
iconutil -c icns build/RawFlow.iconset -o build/RawFlow.icns

# Ayudante nativo (OCR de pantalla + clics) para el paso de Imagen/Bridge
# ---- pruebas: si alguna falla (duplicados, nombres, proyectos…), no se compila
FALLO=0                                    # se pasan las dos tandas enteras para ver TODO lo que falla de una vez
.venv/bin/python tests/test_duplicados.py || FALLO=1
.venv/bin/python tests/pruebas_rawflow.py || FALLO=1
[ "$FALLO" = "0" ] || { echo "PRUEBAS FALLIDAS: no se compila"; exit 1; }

swiftc -O tools/rfui.swift -o build/rfui 2>/dev/null || { echo "rfui (ayudante nativo) no compila: no se sigue"; exit 1; }
cp build/rfui tools/rfui                # el mismo ayudante al abrir RawFlow desde el código

.venv/bin/python -m PyInstaller --noconfirm --windowed --name RawFlow \
  --icon build/RawFlow.icns --add-binary "build/rfui:." --add-data "rawflow/cineslide_run.py:rawflow" --add-data "rawflow/r4m_worker.js:rawflow" \
  --collect-data qtawesome --hidden-import qtawesome \
  --osx-bundle-identifier com.rawflow.app RawFlow.py

# Permiso para controlar UTM (botones de máquina virtual) y datos de versión
P=dist/RawFlow.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string RawFlow controla UTM para crear e instalar máquinas virtuales." "$P" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSAppleEventsUsageDescription RawFlow controla UTM para crear e instalar máquinas virtuales." "$P"
# Sin App Nap: macOS no frena RawFlow aunque su ventana quede tapada o en segundo plano
/usr/libexec/PlistBuddy -c "Add :NSAppSleepDisabled bool true" "$P" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSAppSleepDisabled true" "$P"
VERSION=$(.venv/bin/python -c "import rawflow; print(rawflow.__version__)")
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$P"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "$P" 2>/dev/null || true

# Firma SIEMPRE con el mismo certificado local («RawFlow Firma Local», en el llavero):
# macOS reconoce que es la misma app y conserva los permisos en cada actualización.
# Si no existe (otro Mac), firma ad hoc.
ID=$(security find-identity -p codesigning 2>/dev/null | awk '/"RawFlow Firma Local"/ {print $2; exit}')
codesign --force --deep --sign "${ID:--}" dist/RawFlow.app
[ -n "$ID" ] && echo "Firmado con «RawFlow Firma Local»" || echo "AVISO: firma ad hoc (los permisos se pedirán de nuevo)"
codesign --verify --deep dist/RawFlow.app

rm -f dist/RawFlow-Mac.zip
ditto -c -k --sequesterRsrc --keepParent dist/RawFlow.app dist/RawFlow-Mac.zip

# ---- Instalador de un clic: «Instalar RawFlow.app» (su programa es mac/instalar.sh; RawFlow.app va dentro)
INST="dist/Instalar RawFlow.app"
rm -rf "$INST"
mkdir -p "$INST/Contents/MacOS" "$INST/Contents/Resources"
cp mac/instalar.sh "$INST/Contents/MacOS/instalar"
chmod 755 "$INST/Contents/MacOS/instalar"
cp build/RawFlow.icns "$INST/Contents/Resources/RawFlow.icns"
ditto dist/RawFlow.app "$INST/Contents/Resources/RawFlow.app"
VERSION_I=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$P")
cat > "$INST/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Instalar RawFlow</string>
  <key>CFBundleDisplayName</key><string>Instalar RawFlow</string>
  <key>CFBundleIdentifier</key><string>com.rawflow.instalador</string>
  <key>CFBundleExecutable</key><string>instalar</string>
  <key>CFBundleIconFile</key><string>RawFlow</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION_I</string>
  <key>CFBundleVersion</key><string>$VERSION_I</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
# misma firma que RawFlow (la app de dentro ya va firmada; aquí se firma el instalador entero)
codesign --force --deep --sign "${ID:--}" "$INST"
codesign --verify --deep "$INST"
rm -f "dist/Instalar RawFlow (Mac).zip"
ditto -c -k --sequesterRsrc --keepParent "$INST" "dist/Instalar RawFlow (Mac).zip"

# ---- ../Instaladores, ordenada por sistema (lo de Mac en MAC, lo de Windows en WINDOWS, y aparte lo que solo usa
#      «Instalar máquina virtual» en este Mac):
#   Instaladores/MAC/Instalar RawFlow X (Mac).zip                    (dentro: «Instalar RawFlow.app», de un clic)
#   Instaladores/WINDOWS/RawFlow X para Windows (INSTALADOR).zip     ← lo ÚNICO que se copia a la PC
#   Instaladores/WINDOWS/Programas para Windows/                     (DxO, Imagen AI, Bridge, Retouch4me, CineSlide)
#   PROGRAMA/MAQUINA VIRTUAL MAC/                                    (código para la máquina, Python, ISO de Windows)
# Solo Mac (sin rehacer el de Windows):  RAWFLOW_WINDOWS=0 ./build_mac.sh
VER=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$P")
IM="../INSTALADORES/MAC"
IW="../INSTALADORES/WINDOWS"
IV="MAQUINA VIRTUAL MAC"        # (4.26) junto al código, fuera de los instaladores
mkdir -p "$IM" "$IW/Programas para Windows" "$IV"
rm -f "$IM"/RawFlow*Mac.zip "$IM"/Instalar*RawFlow*.zip
cp "dist/Instalar RawFlow (Mac).zip" "$IM/Instalar RawFlow $VER (Mac).zip"
if [ "${RAWFLOW_WINDOWS:-1}" != "1" ]; then
  echo "Listo: Instaladores/MAC/Instalar RawFlow $VER (Mac).zip (Windows sin tocar)"
  exit 0
fi
# código para la máquina virtual (C:\RawFlow dentro de Windows; no es para la PC)
rm -f "$IV"/RawFlow-*-Windows-codigo.zip
STAGE=$(mktemp -d)/RawFlow-$VER-Windows
mkdir -p "$STAGE"
cp -R rawflow RawFlow.py requirements.txt README.md "$STAGE/"
cp windows/build_windows.bat "windows/Abrir RawFlow (Windows).bat" "$STAGE/"
find "$STAGE" -name "__pycache__" -prune -exec rm -rf {} +
ZIP="$(cd "$IV" && pwd)/RawFlow-$VER-Windows-codigo.zip"
( cd "$(dirname "$STAGE")" && zip -qr "$ZIP" "$(basename "$STAGE")" )
rm -rf "$(dirname "$STAGE")"
# ---- instalador para una PC con Windows (sin máquina virtual, sin internet, sin administrador):
#   arriba SOLO «Instalar RawFlow.bat», «Desinstalar RawFlow.bat» y LEEME; lo demás (Python 3.14 x64 y ARM, PySide6
#   Essentials, psutil, RawFlow) dentro de «archivos (no tocar)»
WPY="$IV/Python para Windows"
[ -d "$WPY" ] || WPY="../INSTALADORES/Programas para Windows/1 Python (para RawFlow)"      # orden de antes
# OJO: el instalador de Windows de verdad («RawFlow X para Windows (Instalador).exe», con RawFlow.exe y sin Python)
# se compila EN WINDOWS con windows\build_windows.bat. Este zip antiguo (Python + librerías) ya solo se rehace si se
# pide a propósito:  RAWFLOW_INSTALADOR_ZIP=1 ./build_mac.sh
if [ -d "$WPY" ] && [ "${RAWFLOW_INSTALADOR_ZIP:-0}" = "1" ]; then
  NAME="RawFlow $VER para Windows (INSTALADOR)"
  IST=$(mktemp -d)/$NAME
  AR="$IST/archivos (no tocar)"
  mkdir -p "$AR/python" "$AR/RawFlow"
  cp windows/"Instalar RawFlow.bat" windows/"Desinstalar RawFlow.bat" windows/LEEME.txt "$IST/"
  cp windows/instalar.ps1 windows/requisitos-windows.txt "$AR/"
  for A in amd64 arm64; do
    if ls "$WPY"/python-*-$A.exe >/dev/null 2>&1; then
      cp "$WPY"/python-*-$A.exe "$AR/python/"
      mkdir -p "$AR/librerias-win_$A"
      cp "$WPY/librerias-win_$A"/pyside6_essentials-*.whl "$WPY/librerias-win_$A"/shiboken6-*.whl \
         "$WPY/librerias-win_$A"/psutil-*.whl "$AR/librerias-win_$A/"
    fi
  done
  cp -R rawflow RawFlow.py requirements.txt README.md "$AR/RawFlow/"
  find "$IST" -name "__pycache__" -prune -exec rm -rf {} +
  rm -f "$IW"/RawFlow*Windows*INSTALADOR*.zip "$IW"/RawFlow-*-Windows-Instalador.zip
  ZI="$(cd "$IW" && pwd)/$NAME.zip"
  ( cd "$(dirname "$IST")" && zip -qr "$ZI" "$(basename "$IST")" )
  rm -rf "$(dirname "$IST")"
  echo "Instalador de Windows: Instaladores/WINDOWS/$NAME.zip"
fi
echo "Listo: Instaladores/MAC/Instalar RawFlow $VER (Mac).zip (el de Windows se compila en Windows: windows/build_windows.bat)"

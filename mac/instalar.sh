#!/bin/bash
# Instalador de RawFlow para Mac: es el programa de «Instalar RawFlow.app» (lo arma build_mac.sh).
# Doble clic y listo: copia RawFlow a Aplicaciones (reemplaza la versión anterior; tus ajustes y tus eventos se
# conservan), lo pone en el Dock, lo abre y este instalador se cierra solo. No pide nada ni abre la Terminal.
#
# RawFlow.app viaja DENTRO del instalador (Contents/Resources/RawFlow.app), así que funciona desde cualquier sitio:
# Descargas, un USB o la carpeta donde descomprimiste el zip.

NOMBRE="RawFlow"
ORIGEN="$(cd "$(dirname "$0")/../Resources" 2>/dev/null && pwd)/$NOMBRE.app"

aviso() {      # un aviso normal de macOS (sin Terminal)
  /usr/bin/osascript -e "display alert \"Instalar RawFlow\" message \"$1\" as critical buttons {\"Cerrar\"} default button 1" >/dev/null 2>&1
}

if [ ! -d "$ORIGEN" ]; then
  aviso "A este instalador le falta RawFlow dentro. Vuelve a descomprimir el zip entero y ábrelo otra vez."
  exit 1
fi

# Dónde se instala: Aplicaciones; si este usuario no puede escribir ahí, en su propia carpeta Aplicaciones.
DESTINO="/Applications/$NOMBRE.app"
if [ ! -w /Applications ]; then
  mkdir -p "$HOME/Applications"
  DESTINO="$HOME/Applications/$NOMBRE.app"
fi

# Si RawFlow está abierto, se cierra bien antes de reemplazarlo (con un evento en marcha RawFlow pregunta: se espera).
if /usr/bin/pgrep -x "$NOMBRE" >/dev/null 2>&1; then
  /usr/bin/osascript -e 'tell application id "com.rawflow.app" to quit' >/dev/null 2>&1
  for _ in $(seq 1 40); do
    /usr/bin/pgrep -x "$NOMBRE" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if /usr/bin/pgrep -x "$NOMBRE" >/dev/null 2>&1; then
    aviso "RawFlow sigue abierto (¿hay un evento en marcha?). Ciérralo y abre el instalador otra vez."
    exit 1
  fi
fi

# Se copia primero a un nombre temporal y solo si salió entero se cambia por el bueno: nunca queda a medias.
NUEVO="$DESTINO.instalando"
rm -rf "$NUEVO"
if ! /usr/bin/ditto "$ORIGEN" "$NUEVO"; then
  rm -rf "$NUEVO"
  aviso "No se pudo copiar RawFlow a $(dirname "$DESTINO"). Mira si queda espacio en el disco."
  exit 1
fi
rm -rf "$DESTINO"
if ! mv "$NUEVO" "$DESTINO"; then
  aviso "No se pudo dejar RawFlow en $(dirname "$DESTINO")."
  exit 1
fi
/usr/bin/xattr -dr com.apple.quarantine "$DESTINO" >/dev/null 2>&1     # que no vuelva a avisar al abrirlo

# En el Dock (una sola vez: si ya estaba, no se toca).
if ! /usr/bin/defaults read com.apple.dock persistent-apps 2>/dev/null | /usr/bin/grep -q "/$NOMBRE.app"; then
  /usr/bin/defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$DESTINO</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>"
  /usr/bin/killall Dock >/dev/null 2>&1          # el Dock se recarga un instante y aparece RawFlow
  sleep 1.5
fi

# Se abre RawFlow y el instalador termina (se cierra solo).
/usr/bin/open "$DESTINO"
exit 0

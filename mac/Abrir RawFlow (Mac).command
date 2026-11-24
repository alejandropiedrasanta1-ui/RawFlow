#!/bin/bash
# Doble clic para abrir RawFlow en Mac. La primera vez instala lo necesario.
cd "$(dirname "$0")"
[ -f RawFlow.py ] || cd ..                 # desde «mac/»: el código está una carpeta arriba
if [ ! -x ".venv/bin/python" ]; then
  echo "Preparando RawFlow por primera vez…"
  python3 -m venv .venv || { echo "Necesitas Python 3: https://www.python.org/downloads/"; read; exit 1; }
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt -q
fi
.venv/bin/python RawFlow.py &
disown
exit 0

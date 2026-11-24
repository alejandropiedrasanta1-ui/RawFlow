@echo off
REM Doble clic para abrir RawFlow en Windows. La primera vez instala lo necesario.
cd /d "%~dp0"
if not exist "RawFlow.py" cd ..
if not exist ".venv\Scripts\pythonw.exe" (
  echo Preparando RawFlow por primera vez...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Necesitas Python 3 desde https://www.python.org/downloads/  ^(marca "Add python.exe to PATH"^)
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
)
start "" ".venv\Scripts\pythonw.exe" RawFlow.py

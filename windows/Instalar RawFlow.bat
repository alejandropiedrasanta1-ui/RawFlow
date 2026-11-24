@echo off
REM Doble clic: instala RawFlow en esta PC (sin internet y sin permisos de administrador).
title Instalar RawFlow
cd /d "%~dp0"
set "PS1=%~dp0archivos (no tocar)\instalar.ps1"
if not exist "%PS1%" set "PS1=%~dp0instalar.ps1"
if not exist "%PS1%" goto falta
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
  echo.
  echo No se pudo terminar la instalacion: mira el mensaje de arriba.
  pause
  exit /b 1
)
exit /b 0
:falta
echo Falta la carpeta "archivos (no tocar)". Descomprime el zip ENTERO: clic derecho, Extraer todo.
pause
exit /b 1

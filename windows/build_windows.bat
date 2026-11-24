@echo off
REM Doble clic: compila RawFlow.exe y crea su instalador en dist\windows (ver build_windows.ps1).
title Compilar RawFlow para Windows
where pwsh >nul 2>nul && (set "PS=pwsh") || (set "PS=powershell")
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
echo.
pause

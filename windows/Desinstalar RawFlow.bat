@echo off
REM Quita RawFlow de esta PC. Tus fotos y tus eventos NO se tocan.
title Desinstalar RawFlow
echo Se quitara RawFlow de esta PC (tus fotos y tus eventos no se tocan).
choice /M "Seguir"
if errorlevel 2 exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*RawFlow.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 1; Remove-Item -Recurse -Force (Join-Path $env:LOCALAPPDATA 'Programs\RawFlow') -ErrorAction SilentlyContinue; Remove-Item -Force (Join-Path ([Environment]::GetFolderPath('Desktop')) 'RawFlow.lnk'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'RawFlow.lnk') -ErrorAction SilentlyContinue"
echo.
echo Listo: RawFlow se quito de esta PC.
pause

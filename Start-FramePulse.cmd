@echo off
setlocal
set "ELECTRON_RUN_AS_NODE="
set "FRAMEPULSE_DATA=%~dp0runtime\library"
if exist "%~dp0dist\win-unpacked\FramePulse.exe" (
  start "" "%~dp0dist\win-unpacked\FramePulse.exe" --data "%FRAMEPULSE_DATA%"
  exit /b 0
)
if exist "%~dp0desktop\node_modules\electron\dist\electron.exe" if exist "%~dp0.venv\Scripts\python.exe" (
  start "" "%~dp0desktop\node_modules\electron\dist\electron.exe" "%~dp0desktop" --data "%FRAMEPULSE_DATA%"
  exit /b 0
)
if exist "%~dp0desktop\node_modules\electron\dist\electron.exe" if exist "%~dp0.qa-venv\Scripts\python.exe" (
  start "" "%~dp0desktop\node_modules\electron\dist\electron.exe" "%~dp0desktop" --data "%FRAMEPULSE_DATA%"
  exit /b 0
)
echo FramePulse desktop has not been built yet.
echo For source setup, run Setup-FramePulse.cmd (Python and Node.js required).
echo Or build the standalone app: powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-desktop.ps1"
pause
exit /b 1

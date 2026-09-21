@echo off
if exist "%~dp0dist\win-unpacked\resources\backend\framepulse-backend.exe" (
  "%~dp0dist\win-unpacked\resources\backend\framepulse-backend.exe" %*
  exit /b
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run Setup-FramePulse.cmd first, or use the standalone desktop package.
  exit /b 2
)
set "PYTHONPATH=%~dp0src"
"%~dp0.venv\Scripts\python.exe" -m framepulse %*
exit /b %errorlevel%

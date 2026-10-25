@echo off
rem Double-click launcher: Docker stack if Docker is running, otherwise a local run.
cd /d "%~dp0.."
docker info >nul 2>&1
if %errorlevel%==0 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rag.ps1" up
  start "" http://localhost:8080
) else (
  if not exist "backend\.venv" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rag.ps1" setup -Yes
  start "" http://127.0.0.1:8000
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rag.ps1" run
)
pause

@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo JARVIS ist noch nicht installiert. Starte install.bat ...
  call install.bat
  if not exist ".venv\Scripts\python.exe" exit /b 1
)

".venv\Scripts\python.exe" scripts\check_setup.py --quick
if errorlevel 1 (
  pause
  exit /b 1
)

set "PORT=8765"
for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"JARVIS_PORT=" .env 2^>nul') do if not "%%b"=="" set "PORT=%%b"

echo.
echo  JARVIS startet ... Browser oeffnet sich gleich: http://127.0.0.1:%PORT%
echo  (Beenden mit Strg+C)
echo.
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"
cd backend
"..\.venv\Scripts\python.exe" -m jarvis.main
pause

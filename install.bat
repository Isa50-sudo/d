@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  =============================================
echo    JARVIS - Installation
echo  =============================================
echo.

rem --- Python finden (py-Launcher bevorzugt) ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [FAIL] Python wurde nicht gefunden.
  echo        Bitte Python 3.10+ installieren: https://www.python.org/downloads/
  echo        Beim Installieren "Add python.exe to PATH" aktivieren.
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo [FAIL] Python 3.10 oder neuer wird benoetigt.
  %PY% --version
  pause
  exit /b 1
)

rem --- Virtuelle Umgebung ---
if not exist ".venv\Scripts\python.exe" (
  echo [....] Erstelle virtuelle Umgebung .venv
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [FAIL] Virtuelle Umgebung konnte nicht erstellt werden.
    pause
    exit /b 1
  )
)

echo [....] Installiere Abhaengigkeiten
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo [FAIL] Installation der Pakete fehlgeschlagen.
  pause
  exit /b 1
)

rem --- Konfiguration ---
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [ OK ] .env aus .env.example erstellt
)
if not exist "data" mkdir data
if not exist "logs" mkdir logs

echo [....] Lade lokale Sprachmodelle (Stimme + Spracherkennung, einmalig)
".venv\Scripts\python.exe" scripts\download_models.py

".venv\Scripts\python.exe" scripts\check_setup.py

echo.
echo  Naechster Schritt:
echo    1. Ollama installieren und starten: https://ollama.com/download
echo    2. Modell laden:  ollama pull qwen3:8b
echo    3. start.bat ausfuehren
echo.
pause

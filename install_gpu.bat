@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  JARVIS - GPU-Beschleunigung fuer die Spracherkennung (NVIDIA)
echo  Installiert CUDA-12-Bibliotheken (cuBLAS, cuDNN 9) in die .venv (~1,5 GB).
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [FAIL] Bitte zuerst install.bat ausfuehren.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r backend\requirements-gpu.txt
if errorlevel 1 (
  echo [FAIL] Installation fehlgeschlagen.
  pause
  exit /b 1
)
echo.
echo [ OK ] Fertig. JARVIS neu starten und in SETTINGS "Rechengeraet Whisper" auf "automatisch" oder "NVIDIA-GPU" stellen.
echo.
pause

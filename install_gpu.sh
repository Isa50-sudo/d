#!/usr/bin/env bash
# JARVIS – GPU-Beschleunigung für die Spracherkennung (NVIDIA, Linux)
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Bitte zuerst ./install.sh ausführen."; exit 1; }
.venv/bin/python -m pip install -r backend/requirements-gpu.txt
echo "[ OK ] Fertig. JARVIS neu starten."

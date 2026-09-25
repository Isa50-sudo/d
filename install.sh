#!/usr/bin/env bash
# JARVIS – Installation (Linux / macOS)
set -euo pipefail
cd "$(dirname "$0")"

echo
echo " ============================================="
echo "   JARVIS - Installation"
echo " ============================================="
echo

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  echo "[FAIL] Python wurde nicht gefunden. Bitte Python 3.10+ installieren."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "[FAIL] Python 3.10 oder neuer wird benötigt ($("$PY" --version))."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[....] Erstelle virtuelle Umgebung .venv"
  if ! "$PY" -m venv .venv; then
    echo "[FAIL] venv fehlgeschlagen. Unter Debian/Ubuntu: sudo apt install python3-venv"
    exit 1
  fi
fi

echo "[....] Installiere Abhängigkeiten"
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r backend/requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "[ OK ] .env aus .env.example erstellt"
fi
mkdir -p data logs

echo "[....] Lade lokale Sprachmodelle (Stimme + Spracherkennung, einmalig)"
.venv/bin/python scripts/download_models.py || true

.venv/bin/python scripts/check_setup.py || true

echo
echo " Nächster Schritt:"
echo "   1. Ollama installieren und starten: https://ollama.com/download"
echo "   2. Modell laden:  ollama pull qwen3:8b"
echo "   3. ./start.sh ausführen"
echo

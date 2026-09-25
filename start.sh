#!/usr/bin/env bash
# JARVIS – Start (Linux / macOS)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "JARVIS ist noch nicht installiert – starte install.sh ..."
  ./install.sh
fi

.venv/bin/python scripts/check_setup.py --quick

PORT="$(grep -E '^JARVIS_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)"
PORT="${PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

echo
echo " JARVIS startet ... ${URL}   (Beenden mit Strg+C)"
echo

(
  sleep 3
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$URL" || true
  fi
) &

cd backend
exec ../.venv/bin/python -m jarvis.main

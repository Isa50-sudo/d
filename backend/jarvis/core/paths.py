"""Zentrale Pfaddefinitionen des Projekts."""
from __future__ import annotations

import os
from pathlib import Path

# backend/jarvis/core/paths.py -> Projektwurzel
ROOT_DIR: Path = Path(__file__).resolve().parents[3]
BACKEND_DIR: Path = ROOT_DIR / "backend"
FRONTEND_DIR: Path = ROOT_DIR / "frontend"
# Überschreibbar (z. B. für Tests oder eigene Speicherorte)
DATA_DIR: Path = Path(os.environ.get("JARVIS_DATA_DIR", ROOT_DIR / "data"))
LOG_DIR: Path = Path(os.environ.get("JARVIS_LOG_DIR", ROOT_DIR / "logs"))
CONFIG_DIR: Path = ROOT_DIR / "config"
ENV_FILE: Path = ROOT_DIR / ".env"

SETTINGS_FILE: Path = DATA_DIR / "settings.json"
DATABASE_FILE: Path = DATA_DIR / "jarvis.db"


def ensure_directories() -> None:
    """Legt alle Laufzeitordner an, falls sie fehlen."""
    for directory in (DATA_DIR, LOG_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)

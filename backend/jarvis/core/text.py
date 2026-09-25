"""Text-Hilfen: repariert fehlerhaft dekodierte Zeichenketten (v. a. unter Windows)."""
from __future__ import annotations

from typing import Any


def clean_text(value: str) -> str:
    """Repariert Zeichenketten mit Surrogate-Zeichen (z. B. falsch dekodierte Windows-Texte)."""
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        raw = value.encode("utf-8", "surrogateescape")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")


def clean_payload(obj: Any) -> Any:
    if isinstance(obj, str):
        return clean_text(obj)
    if isinstance(obj, dict):
        return {clean_payload(k): clean_payload(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_payload(v) for v in obj]
    return obj

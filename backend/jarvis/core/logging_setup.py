"""Entwickler-Logging (logs/jarvis.log) mit Schutz vor Secret-Lecks."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from jarvis.core.paths import LOG_DIR

# Google-API-Keys und generische Bearer-/Passwort-Muster
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)(api[_-]?key|password|passwort|token|secret)(\s*[=:]\s*)([^\s&\"',]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
]
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Registriert einen konkreten Secret-Wert, der niemals geloggt werden darf."""
    if value and len(value) >= 8:
        _extra_secrets.add(value)


def redact(text: str) -> str:
    for secret in _extra_secrets:
        text = text.replace(secret, "***REDACTED***")
    text = _SECRET_PATTERNS[0].sub("***REDACTED***", text)
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        LOG_DIR / "jarvis.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console)
    set_level(level)

    # Laute Bibliotheken dämpfen – insbesondere keine Request-Dumps mit Headern
    for noisy in ("httpx", "httpcore", "websockets", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_level(level: str) -> None:
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))

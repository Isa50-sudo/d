"""Internes, benutzerlesbares Ereignisprotokoll (LOGS-Seite) + Audit-Trail.

Gespeichert werden nur Metadaten (was, wann, Ergebnis). Tool-Argumente und
Transkripte werden nur protokolliert, wenn dies in den Settings aktiv ist.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from collections import deque
from typing import Any, Literal

from jarvis.core.hub import Hub
from jarvis.core.logging_setup import redact
from jarvis.core.paths import LOG_DIR

Level = Literal["debug", "info", "success", "warning", "error"]
log = logging.getLogger("jarvis.events")

_MAX_FILE_BYTES = 2 * 1024 * 1024


class EventLog:
    def __init__(self, hub: Hub, capacity: int = 1000) -> None:
        self._hub = hub
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counter = itertools.count(1)
        self._file = LOG_DIR / "events.jsonl"
        self._file_lock = threading.Lock()

    def add(
        self,
        category: str,
        message: str,
        level: Level = "info",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": next(self._counter),
            "ts": time.time(),
            "level": level,
            "category": category,
            "message": redact(message),
            "data": json.loads(redact(json.dumps(data, default=str))) if data else None,
        }
        self._entries.append(entry)
        py_level = {"debug": 10, "info": 20, "success": 20, "warning": 30, "error": 40}[level]
        log.log(py_level, "[%s] %s", category, entry["message"])
        self._persist(entry)
        self._hub.broadcast_nowait("log", {"entry": entry})
        return entry

    def _persist(self, entry: dict[str, Any]) -> None:
        with self._file_lock:
            try:
                if self._file.exists() and self._file.stat().st_size > _MAX_FILE_BYTES:
                    self._file.replace(self._file.with_suffix(".1.jsonl"))
                with self._file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def recent(self, limit: int = 300, category: str | None = None) -> list[dict[str, Any]]:
        items = [e for e in self._entries if category is None or e["category"] == category]
        return items[-limit:]

    def clear(self) -> None:
        self._entries.clear()

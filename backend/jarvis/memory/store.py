"""Memory-System.

* Kurzzeit-Kontext: ConversationBuffer (RAM) – letzte Gesprächsrunden, wird
  bei einer neuen Gemini-Session als Kontext mitgegeben. Nicht persistent.
* Langzeit-Memory: SQLite (data/jarvis.db) – NUR explizit gespeicherte Fakten,
  Präferenzen und Ereignisse. Jederzeit einsehbar und löschbar.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MemoryCategory = Literal["fact", "preference", "event", "note"]
CATEGORIES: tuple[str, ...] = ("fact", "preference", "event", "note")


@dataclass
class Turn:
    role: Literal["user", "jarvis"]
    text: str
    ts: float


class ConversationBuffer:
    def __init__(self, max_turns: int = 20) -> None:
        self._turns: deque[Turn] = deque(maxlen=max(1, max_turns))

    def resize(self, max_turns: int) -> None:
        self._turns = deque(self._turns, maxlen=max(1, max_turns))

    def add(self, role: Literal["user", "jarvis"], text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._turns.append(Turn(role=role, text=text, ts=time.time()))

    def turns(self) -> list[Turn]:
        return list(self._turns)

    def clear(self) -> None:
        self._turns.clear()

    def as_text(self, limit_chars: int = 4000) -> str:
        lines = [f"{'Benutzer' if t.role == 'user' else 'JARVIS'}: {t.text}" for t in self._turns]
        text = "\n".join(lines)
        return text[-limit_chars:]


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'fact',
                    source TEXT NOT NULL DEFAULT 'user',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.commit()

    # -- low level -------------------------------------------------------
    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            self._conn.commit()
            if cur.lastrowid and sql.lstrip().upper().startswith("INSERT"):
                return [{"id": cur.lastrowid}]
            if sql.lstrip().upper().startswith(("DELETE", "UPDATE")):
                return [{"changed": cur.rowcount}]
            return rows

    async def run(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._run, sql, params)

    def healthy(self) -> bool:
        try:
            self._run("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    # -- memories --------------------------------------------------------
    async def add(self, content: str, category: str = "fact", source: str = "user") -> dict[str, Any]:
        if category not in CATEGORIES:
            category = "note"
        content = content.strip()[:2000]
        row = await self.run(
            "INSERT INTO memories (content, category, source, created_at) VALUES (?, ?, ?, ?)",
            (content, category, source, time.time()),
        )
        return {"id": row[0]["id"], "content": content, "category": category, "source": source}

    async def list(self, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if query:
            like = f"%{query.strip()}%"
            return await self.run(
                "SELECT * FROM memories WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?", (like, limit)
            )
        return await self.run("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,))

    async def delete(self, memory_id: int) -> bool:
        res = await self.run("DELETE FROM memories WHERE id = ?", (memory_id,))
        return bool(res and res[0].get("changed"))

    async def clear(self) -> int:
        res = await self.run("DELETE FROM memories")
        return int(res[0].get("changed", 0)) if res else 0

    async def context_block(self, limit: int = 50) -> str:
        items = await self.list(limit=limit)
        if not items:
            return ""
        labels = {"fact": "Fakt", "preference": "Präferenz", "event": "Ereignis", "note": "Notiz"}
        return "\n".join(f"- [{labels.get(i['category'], i['category'])}] {i['content']}" for i in reversed(items))

    # -- reminders -------------------------------------------------------
    async def add_reminder(self, text: str, due_at: float) -> int:
        row = await self.run(
            "INSERT INTO reminders (text, due_at, created_at) VALUES (?, ?, ?)", (text.strip()[:500], due_at, time.time())
        )
        return int(row[0]["id"])

    async def list_reminders(self, include_fired: bool = False) -> list[dict[str, Any]]:
        if include_fired:
            return await self.run("SELECT * FROM reminders ORDER BY due_at ASC")
        return await self.run("SELECT * FROM reminders WHERE fired = 0 ORDER BY due_at ASC")

    async def due_reminders(self, now: float) -> list[dict[str, Any]]:
        return await self.run("SELECT * FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at", (now,))

    async def mark_reminder_fired(self, reminder_id: int) -> None:
        await self.run("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

    async def delete_reminder(self, reminder_id: int) -> bool:
        res = await self.run("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return bool(res and res[0].get("changed"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

"""WebSocket-Hub: verwaltet verbundene Browser-Clients und verteilt Events."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from jarvis.core.text import clean_payload

log = logging.getLogger(__name__)


class ClientConnection:
    """Eine Browser-Verbindung. Sendevorgänge werden serialisiert."""

    def __init__(self, websocket: WebSocket) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.websocket = websocket
        self.connected_at = time.time()
        self._send_lock = asyncio.Lock()
        self.closed = False

    async def send_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        await self._send({"type": event_type, "ts": time.time(), **(payload or {})})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        async with self._send_lock:
            try:
                if self.websocket.application_state == WebSocketState.CONNECTED:
                    try:
                        await self.websocket.send_json(message)
                    except UnicodeEncodeError:
                        await self.websocket.send_json(clean_payload(message))
            except Exception:  # noqa: BLE001
                self.closed = True

    async def send_audio(self, pcm: bytes) -> None:
        if self.closed:
            return
        async with self._send_lock:
            try:
                await self.websocket.send_bytes(pcm)
            except Exception:  # noqa: BLE001
                self.closed = True


class Hub:
    def __init__(self) -> None:
        self._clients: dict[str, ClientConnection] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add(self, client: ClientConnection) -> None:
        self._clients[client.id] = client

    def remove(self, client: ClientConnection) -> None:
        self._clients.pop(client.id, None)

    def get(self, client_id: str | None) -> ClientConnection | None:
        if not client_id:
            return None
        return self._clients.get(client_id)

    def clients(self) -> list[ClientConnection]:
        return list(self._clients.values())

    async def broadcast(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        clients = self.clients()
        if clients:
            await asyncio.gather(*(c.send_event(event_type, payload) for c in clients))

    def broadcast_nowait(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Broadcast aus synchronem Code (z. B. Logging) heraus planen."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.broadcast(event_type, payload))

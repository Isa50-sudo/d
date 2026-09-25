"""ConfirmationManager – explizite Benutzerbestätigung für kritische Aktionen.

Die Bestätigung erfolgt ausschließlich über eine bewusste Aktion im Browser
(Klick auf "AUSFÜHREN"). Sie kann NICHT vom Sprachmodell erteilt werden –
so kann eine missverstandene Spracheingabe niemals direkt eine kritische
Aktion auslösen.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.hub import ClientConnection, Hub

log = logging.getLogger(__name__)


@dataclass
class PendingConfirmation:
    id: str
    tool: str
    title: str
    summary: str
    risk: str
    details: dict[str, Any]
    client_id: str | None
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future[bool] = field(default_factory=lambda: asyncio.get_running_loop().create_future())

    def public(self, timeout_s: int) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "title": self.title,
            "summary": self.summary,
            "risk": self.risk,
            "details": self.details,
            "expires_at": self.created_at + timeout_s,
        }


class ConfirmationManager:
    def __init__(self, hub: Hub) -> None:
        self._hub = hub
        self._pending: dict[str, PendingConfirmation] = {}

    @property
    def pending(self) -> list[PendingConfirmation]:
        return list(self._pending.values())

    async def request(
        self,
        *,
        tool: str,
        title: str,
        summary: str,
        risk: str,
        details: dict[str, Any],
        client: ClientConnection | None,
        timeout_s: int,
    ) -> bool:
        """Fragt den Benutzer und wartet. Liefert True nur bei expliziter Zustimmung."""
        pending = PendingConfirmation(
            id=secrets.token_hex(8),
            tool=tool,
            title=title,
            summary=summary,
            risk=risk,
            details=details,
            client_id=client.id if client else None,
        )
        self._pending[pending.id] = pending
        payload = {"request": pending.public(timeout_s)}
        if client is not None and not client.closed:
            await client.send_event("confirm.request", payload)
        else:
            await self._hub.broadcast("confirm.request", payload)
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout_s)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(pending.id, None)
            await self._hub.broadcast("confirm.closed", {"id": pending.id})

    def respond(self, confirmation_id: str, approved: bool, client_id: str | None) -> bool:
        pending = self._pending.get(confirmation_id)
        if pending is None or pending.future.done():
            return False
        # Nur der auslösende Client (falls bekannt) darf bestätigen
        if pending.client_id and client_id and pending.client_id != client_id:
            log.warning("Bestätigung von fremdem Client abgelehnt")
            return False
        pending.future.set_result(bool(approved))
        return True

    def cancel_all(self, client_id: str | None = None) -> None:
        for pending in list(self._pending.values()):
            if client_id is None or pending.client_id == client_id:
                if not pending.future.done():
                    pending.future.set_result(False)

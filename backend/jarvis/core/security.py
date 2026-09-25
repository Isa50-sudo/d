"""Schutz des lokalen Backends gegen Zugriffe fremder Webseiten.

Ein lokaler Server mit Computerzugriff ist ein attraktives Ziel: Jede
beliebige Webseite im Browser könnte versuchen, http://127.0.0.1:8765 oder
ws://127.0.0.1:8765/ws anzusprechen (CSRF / Cross-Site-WebSocket-Hijacking)
oder per DNS-Rebinding einen fremden Hostnamen auf 127.0.0.1 zeigen zu lassen.

Daher:
  * Host-Header muss localhost/127.0.0.1/[::1] sein  (DNS-Rebinding)
  * Origin (falls gesendet) muss exakt unsere eigene Origin sein (CSRF/CSWSH)
"""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import PlainTextResponse

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_only(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def is_allowed_origin(origin: str | None, port: int) -> bool:
    if origin is None:
        return True  # Same-Origin-Navigation / Tools wie curl
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if host == "::1":
        host = "[::1]"
    return parts.scheme == "http" and host in _LOCAL_HOSTS and (parts.port or 80) == port


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = _host_only(headers.get("host", ""))
        origin = headers.get("origin")
        if host not in _LOCAL_HOSTS or not is_allowed_origin(origin, self.port):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            response = PlainTextResponse("Forbidden: JARVIS akzeptiert nur lokale Anfragen.", 403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

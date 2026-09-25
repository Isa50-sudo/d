"""Sicherer HTTP-Client für Webzugriffe.

Schützt vor SSRF: JARVIS darf über Web-Tools keine Adressen im lokalen Netz
(Router, 127.0.0.1-Dienste, Cloud-Metadaten) abrufen – sonst könnte eine
präparierte Webseite/Anweisung interne Systeme ausspähen.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from jarvis.tools.base import ToolError

USER_AGENT = "JARVIS-LocalAssistant/1.0 (+https://github.com/; personal use)"
MAX_BYTES = 3 * 1024 * 1024


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError("Nur http- und https-Adressen sind erlaubt.")
    host = parts.hostname
    if not host:
        raise ToolError("Die Adresse ist ungültig.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolError("Die Webseite konnte nicht gefunden werden.", detail=str(exc)) from exc
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise ToolError("Zugriffe auf lokale oder private Netzwerkadressen sind nicht erlaubt.")


class WebClient:
    def __init__(self, timeout_s: float = 12.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"},
        )

    async def get(self, url: str, *, params: dict[str, Any] | None = None, max_redirects: int = 5) -> httpx.Response:
        """GET mit SSRF-Prüfung bei jedem Redirect-Hop."""
        current = url
        for _ in range(max_redirects + 1):
            await assert_public_url(current)
            try:
                response = await self._client.get(current, params=params)
            except httpx.TimeoutException as exc:
                raise ToolError("Die Webseite hat nicht rechtzeitig geantwortet.", detail=str(exc)) from exc
            except httpx.HTTPError as exc:
                raise ToolError("Die Webseite ist nicht erreichbar.", detail=str(exc)) from exc
            if response.is_redirect and response.headers.get("location"):
                current = urljoin(str(response.url), response.headers["location"])
                params = None
                continue
            if len(response.content) > MAX_BYTES:
                raise ToolError("Die Antwort der Webseite ist zu groß.")
            return response
        raise ToolError("Zu viele Weiterleitungen.")

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.get(url, params=params)
        if response.status_code >= 400:
            raise ToolError(f"Der Dienst antwortete mit Fehler {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError("Der Dienst lieferte keine gültigen Daten.") from exc

    async def close(self) -> None:
        await self._client.aclose()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str, max_chars: int = 8000) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - kaputtes HTML
        pass
    raw = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    text = "\n".join(line for line in lines if len(line) > 1)
    return parser.title.strip(), text[:max_chars]

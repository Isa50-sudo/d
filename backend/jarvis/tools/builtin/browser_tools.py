"""Browser-Steuerung: Webseiten im Standardbrowser des Betriebssystems öffnen.

Erweiterungspunkt: Für echte Browser-Automation (Playwright) kann hier ein
weiteres Tool mit Risk.WRITE/CRITICAL ergänzt werden.
"""
from __future__ import annotations

import asyncio
import webbrowser
from urllib.parse import quote_plus, urlsplit

from pydantic import BaseModel, Field

from jarvis.tools.base import Risk, ToolContext, ToolError, tool

KNOWN_SITES = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "wikipedia": "https://de.wikipedia.org",
    "github": "https://github.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.de",
    "twitch": "https://www.twitch.tv",
    "reddit": "https://www.reddit.com",
    "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com",
    "ollama": "https://ollama.com",
    "outlook": "https://outlook.live.com",
    "spotify": "https://open.spotify.com",
    "tagesschau": "https://www.tagesschau.de",
    "orf": "https://orf.at",
}


def normalize_url(raw: str) -> str:
    value = raw.strip()
    if value.lower() in KNOWN_SITES:
        return KNOWN_SITES[value.lower()]
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in parts.netloc:
        raise ToolError("Das ist keine gültige Webadresse.")
    return value


class OpenUrlArgs(BaseModel):
    url: str = Field(min_length=2, max_length=2000, description="Webadresse (z. B. 'https://www.google.com', 'youtube.com') oder bekannter Name wie 'google', 'youtube'")


@tool(
    name="open_url",
    description="Öffnet eine Webseite im Standardbrowser des Benutzers.",
    args=OpenUrlArgs,
    risk=Risk.WRITE,
    category="browser",
    summarize=lambda a: f"Webseite öffnen: {a.url}",
)
async def open_url(args: OpenUrlArgs, ctx: ToolContext) -> dict:
    url = normalize_url(args.url)
    ok = await asyncio.to_thread(webbrowser.open, url, 2)
    if not ok:
        raise ToolError("Ich konnte den Browser nicht öffnen.")
    return {"opened": url}


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Suchbegriff")
    engine: str = Field(default="google", description="google, youtube, wikipedia, maps")


@tool(
    name="open_web_search",
    description="Öffnet eine Suche (Google, YouTube, Wikipedia oder Maps) im Browser des Benutzers, damit er die Ergebnisse selbst sieht.",
    args=WebSearchArgs,
    risk=Risk.WRITE,
    category="browser",
    summarize=lambda a: f"Im Browser suchen ({a.engine}): {a.query}",
)
async def open_web_search(args: WebSearchArgs, ctx: ToolContext) -> dict:
    q = quote_plus(args.query)
    urls = {
        "google": f"https://www.google.com/search?q={q}",
        "youtube": f"https://www.youtube.com/results?search_query={q}",
        "wikipedia": f"https://de.wikipedia.org/w/index.php?search={q}",
        "maps": f"https://www.google.com/maps/search/{q}",
    }
    url = urls.get(args.engine.lower(), urls["google"])
    await asyncio.to_thread(webbrowser.open, url, 2)
    return {"opened": url}

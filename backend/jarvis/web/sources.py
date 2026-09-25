"""Aktuelle Informationsquellen: Geocoding, Wetter, Nachrichten, Wikipedia.

Alle Quellen sind öffentlich und ohne API-Key nutzbar:
  * Open-Meteo (Geocoding + Wettervorhersage)   https://open-meteo.com
  * RSS-Feeds seriöser Nachrichtenquellen (in den Settings konfigurierbar)
  * Wikipedia REST API
Jede Antwort enthält Quelle und Abrufzeitpunkt.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import re
from typing import Any
from urllib.parse import quote

from defusedxml import ElementTree as SafeET

from jarvis.tools.base import ToolError
from jarvis.web.http import WebClient, html_to_text

WMO_CODES = {
    0: "klar", 1: "überwiegend klar", 2: "teilweise bewölkt", 3: "bedeckt",
    45: "Nebel", 48: "gefrierender Nebel", 51: "leichter Nieselregen", 53: "Nieselregen",
    55: "starker Nieselregen", 56: "gefrierender Nieselregen", 57: "starker gefrierender Nieselregen",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen", 67: "starker gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel",
    80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    85: "Schneeschauer", 86: "starke Schneeschauer", 95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


async def geocode(web: WebClient, name: str, language: str = "de") -> dict[str, Any]:
    data = await web.get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 1, "language": language, "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        # Fallback: Nominatim (OpenStreetMap) für Sehenswürdigkeiten/Adressen
        nominatim = await web.get_json(
            "https://nominatim.openstreetmap.org/search",
            params={"q": name, "format": "json", "limit": 1, "accept-language": language},
        )
        if not nominatim:
            raise ToolError(f"Den Ort '{name}' konnte ich nicht finden.")
        hit = nominatim[0]
        return {
            "name": hit.get("display_name", name).split(",")[0],
            "full_name": hit.get("display_name", name),
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "country": None,
            "timezone": None,
            "source": "OpenStreetMap Nominatim",
        }
    hit = results[0]
    parts = [hit.get("name"), hit.get("admin1"), hit.get("country")]
    return {
        "name": hit.get("name", name),
        "full_name": ", ".join(p for p in parts if p),
        "lat": hit["latitude"],
        "lon": hit["longitude"],
        "country": hit.get("country"),
        "timezone": hit.get("timezone"),
        "population": hit.get("population"),
        "source": "Open-Meteo Geocoding",
    }


async def weather(web: WebClient, location: str, days: int = 3) -> dict[str, Any]:
    place = await geocode(web, location)
    data = await web.get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["lat"],
            "longitude": place["lon"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": max(1, min(days, 7)),
        },
    )
    current = data.get("current", {})
    daily = data.get("daily", {})
    forecast = []
    for i, day in enumerate(daily.get("time", [])):
        forecast.append(
            {
                "date": day,
                "condition": WMO_CODES.get(daily["weather_code"][i], "unbekannt"),
                "temp_max_c": daily["temperature_2m_max"][i],
                "temp_min_c": daily["temperature_2m_min"][i],
                "precipitation_probability_percent": (daily.get("precipitation_probability_max") or [None] * 7)[i],
                "precipitation_mm": daily["precipitation_sum"][i],
                "wind_max_kmh": daily["wind_speed_10m_max"][i],
            }
        )
    return {
        "location": place["full_name"],
        "coordinates": {"lat": place["lat"], "lon": place["lon"]},
        "current": {
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "condition": WMO_CODES.get(current.get("weather_code"), "unbekannt"),
            "wind_kmh": current.get("wind_speed_10m"),
            "precipitation_mm": current.get("precipitation"),
        },
        "forecast": forecast,
        "source": "Open-Meteo (open-meteo.com)",
        "retrieved_at": now_iso(),
    }


def _text(el: Any, *names: str) -> str:
    for name in names:
        found = el.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _strip_tags(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _parse_date(value: str) -> str | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().isoformat(timespec="minutes")
    except ValueError:
        return value


async def fetch_feed(web: WebClient, name: str, url: str, limit: int = 8) -> list[dict[str, Any]]:
    response = await web.get(url)
    if response.status_code >= 400:
        raise ToolError(f"Nachrichtenquelle {name} nicht erreichbar ({response.status_code}).")
    try:
        root = SafeET.fromstring(response.content)
    except Exception as exc:  # noqa: BLE001 - defusedxml wirft diverse Typen
        raise ToolError(f"Nachrichtenquelle {name} lieferte ungültige Daten.") from exc

    atom = "{http://www.w3.org/2005/Atom}"
    rss1 = "{http://purl.org/rss/1.0/}"
    dc = "{http://purl.org/dc/elements/1.1/}"
    items = root.findall(".//item") or root.findall(f".//{rss1}item") or root.findall(f".//{atom}entry")
    result = []
    for item in items[:limit]:
        link = _text(item, "link", f"{rss1}link")
        if not link:
            link_el = item.find(f"{atom}link")
            link = link_el.get("href", "") if link_el is not None else ""
        result.append(
            {
                "title": _strip_tags(_text(item, "title", f"{rss1}title", f"{atom}title")),
                "summary": _strip_tags(_text(item, "description", f"{rss1}description", f"{atom}summary"))[:300],
                "link": link,
                "published": _parse_date(_text(item, "pubDate", f"{dc}date", f"{atom}updated", f"{atom}published")),
                "source": name,
            }
        )
    return result


async def wikipedia_summary(web: WebClient, topic: str, language: str = "de") -> dict[str, Any]:
    language = language if re.fullmatch(r"[a-z]{2,3}", language) else "de"
    search = await web.get_json(
        f"https://{language}.wikipedia.org/w/api.php",
        params={"action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json"},
    )
    if not search or len(search) < 2 or not search[1]:
        raise ToolError(f"Zu '{topic}' habe ich keinen Wikipedia-Artikel gefunden.")
    title = search[1][0]
    data = await web.get_json(f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
    return {
        "title": data.get("title", title),
        "summary": data.get("extract", ""),
        "url": (data.get("content_urls") or {}).get("desktop", {}).get("page"),
        "coordinates": data.get("coordinates"),
        "last_modified": data.get("timestamp"),
        "source": f"Wikipedia ({language})",
        "retrieved_at": now_iso(),
    }


async def fetch_page_text(web: WebClient, url: str, max_chars: int = 8000) -> dict[str, Any]:
    response = await web.get(url)
    if response.status_code >= 400:
        raise ToolError(f"Die Webseite antwortete mit Fehler {response.status_code}.")
    ctype = response.headers.get("content-type", "")
    if "html" in ctype:
        title, text = html_to_text(response.text, max_chars)
    elif ctype.startswith("text/") or "json" in ctype:
        title, text = "", response.text[:max_chars]
    else:
        raise ToolError("Diese Adresse liefert keinen lesbaren Text (z. B. Bild oder Download).")
    return {
        "url": str(response.url),
        "title": title,
        "text": text,
        "source": response.url.host,
        "retrieved_at": now_iso(),
    }

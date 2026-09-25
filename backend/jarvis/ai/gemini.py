"""Zugriff auf die Google Gemini API (google-genai SDK).

Der API-Key wird ausschließlich hier aus der Umgebung gelesen und nie
an den Browser gesendet.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from google import genai

from jarvis.config.env import EnvSettings
from jarvis.core.logging_setup import register_secret

log = logging.getLogger(__name__)

GeminiState = Literal["unconfigured", "unknown", "ok", "error"]

MSG_NO_KEY = (
    "Es ist kein Gemini API Key hinterlegt. Trage ihn in der Datei .env im JARVIS-Ordner ein "
    "(GEMINI_API_KEY=...) und starte JARVIS neu."
)
MSG_UNREACHABLE = "Die Verbindung zu Gemini konnte nicht hergestellt werden."


@dataclass
class ModelCapabilities:
    """Protokollunterschiede der Live-Modellgenerationen."""

    extended_thinking: bool
    supports_scheduling: bool
    legacy: bool

    @classmethod
    def for_model(cls, model: str) -> "ModelCapabilities":
        name = model.lower()
        legacy = any(tag in name for tag in ("2.0", "2.5", "3.1-flash-live"))
        extended = "extended-thinking" in name
        return cls(extended_thinking=extended, supports_scheduling=not extended and not legacy, legacy=legacy)


class GeminiProvider:
    def __init__(self, env: EnvSettings) -> None:
        self._env = env
        self._client: genai.Client | None = None
        self.state: GeminiState = "unconfigured" if not env.has_gemini_key else "unknown"
        self.last_error: str | None = None
        self.last_check: float = 0.0
        if env.has_gemini_key:
            register_secret(env.gemini_api_key.get_secret_value())  # type: ignore[union-attr]

    @property
    def configured(self) -> bool:
        return self._env.has_gemini_key

    def client(self) -> genai.Client:
        if not self.configured:
            raise RuntimeError(MSG_NO_KEY)
        if self._client is None:
            self._client = genai.Client(api_key=self._env.gemini_api_key.get_secret_value())  # type: ignore[union-attr]
        return self._client

    def mark(self, state: GeminiState, error: str | None = None) -> None:
        self.state = state
        self.last_error = error
        self.last_check = time.time()

    async def check(self, model: str) -> tuple[bool, str]:
        """Prüft Key + Erreichbarkeit mit einem leichten API-Aufruf."""
        if not self.configured:
            self.mark("unconfigured", MSG_NO_KEY)
            return False, MSG_NO_KEY
        try:
            pager = await asyncio.wait_for(self.client().aio.models.list(config={"page_size": 50}), timeout=12)
            names = [m.name or "" for m in pager.page]
            self.mark("ok")
            available = any(model in n for n in names)
            note = "" if available or not names else f" (Hinweis: Modell '{model}' nicht in der ersten Modellliste gefunden)"
            return True, "Gemini erreichbar" + note
        except asyncio.TimeoutError:
            self.mark("error", MSG_UNREACHABLE)
            return False, MSG_UNREACHABLE
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            log.warning("Gemini-Prüfung fehlgeschlagen: %s", text)
            if "API key" in text or "API_KEY" in text or "401" in text or "403" in text:
                msg = "Der Gemini API Key wurde abgelehnt. Bitte in der .env prüfen."
            else:
                msg = MSG_UNREACHABLE
            self.mark("error", msg)
            return False, msg

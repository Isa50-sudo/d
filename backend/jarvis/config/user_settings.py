"""Benutzereinstellungen zur Laufzeit (data/settings.json).

Enthält KEINE Secrets. Startwerte kommen aus der .env, danach sind alle
Werte über die SETTINGS-Seite änderbar und werden atomar gespeichert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, HttpUrl, ValidationError

from jarvis.config.env import EnvSettings
from jarvis.core.paths import SETTINGS_FILE

log = logging.getLogger(__name__)

PolicyName = Literal["allow", "confirm", "deny"]


class AISettings(BaseModel):
    # Ollama-Modell (muss Tool-Calling unterstützen, z. B. qwen3, llama3.1, mistral-nemo)
    model: str = "qwen3:8b"
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    context_tokens: int = Field(default=8192, ge=2048, le=131072)
    # Reasoning-Modelle (qwen3, deepseek-r1): "Denken" abschalten = schnellere Antworten
    disable_thinking: bool = True
    # Spracherkennung (faster-whisper): tiny, base, small, medium, large-v3, large-v3-turbo
    stt_model: str = "small"
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"


class VoiceSettings(BaseModel):
    # Piper-Stimmen (Dateien in data/voices/, laden mit scripts/download_models.py)
    name: str = "de_DE-thorsten-high"
    name_en: str = "en_GB-alan-medium"
    language: str = "de-DE"
    speed: Literal["slow", "calm", "normal", "fast"] = "calm"
    style: str = "tief, ruhig, warm, souverän und professionell"
    # True: JARVIS antwortet immer in VOICE_LANGUAGE (kein automatischer Sprachwechsel)
    lock_language: bool = False


class ConversationSettings(BaseModel):
    wake_word: str = "jarvis"
    activation_mode: Literal["wake_word", "continuous", "push_to_talk"] = "wake_word"
    # Sekunden Stille nach JARVIS' Antwort, bis wieder auf das Wake Word gewartet wird
    follow_up_window_s: int = Field(default=12, ge=3, le=120)
    microphone_device_id: str | None = None
    vad_sensitivity: Literal["low", "normal", "high"] = "normal"


class PermissionSettings(BaseModel):
    access_level: Literal["READ_ONLY", "LIMITED", "FULL_ACCESS"] = "LIMITED"
    tool_overrides: dict[str, PolicyName] = Field(default_factory=dict)
    confirmation_timeout_s: int = Field(default=60, ge=10, le=600)


class NotificationSettings(BaseModel):
    enabled: bool = True
    speak: bool = True
    browser_notifications: bool = False
    disk_threshold_percent: int = Field(default=90, ge=50, le=99)
    memory_threshold_percent: int = Field(default=92, ge=50, le=99)
    cpu_threshold_percent: int = Field(default=95, ge=50, le=100)
    email_check_minutes: int = Field(default=5, ge=1, le=120)
    email_alerts: bool = True


class MemorySettings(BaseModel):
    enabled: bool = True
    inject_into_context: bool = True
    # Modell darf Erinnerungen speichern – aber nur auf ausdrücklichen Wunsch
    allow_model_save: bool = True
    confirm_saves: bool = False
    short_term_turns: int = Field(default=20, ge=0, le=200)


class NewsSource(BaseModel):
    name: str
    url: HttpUrl
    language: str = "de"
    enabled: bool = True


def _default_news_sources() -> list[NewsSource]:
    return [
        NewsSource(name="tagesschau", url="https://www.tagesschau.de/index~rss2.xml", language="de"),
        NewsSource(name="ORF News", url="https://rss.orf.at/news.xml", language="de"),
        NewsSource(name="Deutsche Welle", url="https://rss.dw.com/rdf/rss-de-all", language="de"),
        NewsSource(name="BBC World", url="https://feeds.bbci.co.uk/news/world/rss.xml", language="en"),
        NewsSource(name="Reuters (Google News)", url="https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en", language="en"),
    ]


class WebSettings(BaseModel):
    enabled: bool = True
    news_sources: list[NewsSource] = Field(default_factory=_default_news_sources)
    weather_default_location: str = ""
    request_timeout_s: int = Field(default=12, ge=3, le=60)


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Aus Datenschutzgründen standardmäßig AUS
    log_tool_arguments: bool = False
    log_transcripts: bool = False


class UISettings(BaseModel):
    theme: Literal["arc", "amber", "crimson", "mono"] = "arc"
    reduced_motion: bool = False
    show_subtitles: bool = True


class UserSettings(BaseModel):
    ai: AISettings = Field(default_factory=AISettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    conversation: ConversationSettings = Field(default_factory=ConversationSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    web: WebSettings = Field(default_factory=WebSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    ui: UISettings = Field(default_factory=UISettings)

    @classmethod
    def from_env(cls, env: EnvSettings) -> "UserSettings":
        settings = cls()
        settings.ai.model = env.ollama_model
        settings.ai.stt_model = env.stt_model
        settings.ai.stt_device = env.stt_device
        settings.voice.name = env.voice_name
        settings.voice.name_en = env.voice_name_en
        settings.voice.language = env.voice_language
        settings.voice.speed = env.voice_speed
        settings.voice.style = env.voice_style
        settings.permissions.access_level = env.access_level
        level = env.log_level.upper()
        if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            settings.logging.level = level  # type: ignore[assignment]
        return settings


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and key != "tool_overrides":
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class SettingsStore:
    """Thread-/async-sicherer Speicher für Benutzereinstellungen."""

    def __init__(self, env: EnvSettings) -> None:
        self._env = env
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[UserSettings, UserSettings], Any]] = []
        self._settings = self._load()

    @property
    def current(self) -> UserSettings:
        return self._settings

    def _load(self) -> UserSettings:
        defaults = UserSettings.from_env(self._env)
        if not SETTINGS_FILE.exists():
            self._write(defaults)
            return defaults
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            # Migration von der Gemini-Version: alte Stimm-/Modellwerte verwerfen
            voice = raw.get("voice", {})
            if isinstance(voice, dict) and "-" not in str(voice.get("name", "-")):
                voice.pop("name", None)
            if isinstance(raw.get("ai"), dict) and "live_model" in raw["ai"]:
                raw["ai"] = {}
            merged = _deep_merge(defaults.model_dump(mode="json"), raw)
            return UserSettings.model_validate(merged)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            log.error("settings.json ungültig – verwende Standardwerte: %s", exc)
            backup = SETTINGS_FILE.with_suffix(".invalid.json")
            try:
                SETTINGS_FILE.replace(backup)
            except OSError:
                pass
            self._write(defaults)
            return defaults

    @staticmethod
    def _write(settings: UserSettings) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(settings.model_dump(mode="json"), indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.replace(tmp, SETTINGS_FILE)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def on_change(self, listener: Callable[[UserSettings, UserSettings], Any]) -> None:
        self._listeners.append(listener)

    async def update(self, patch: dict[str, Any]) -> UserSettings:
        """Teil-Update (deep merge) + Validierung + atomares Speichern."""
        async with self._lock:
            old = self._settings
            merged = _deep_merge(old.model_dump(mode="json"), patch)
            new = UserSettings.model_validate(merged)
            await asyncio.to_thread(self._write, new)
            self._settings = new
        for listener in self._listeners:
            try:
                result = listener(old, new)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - Listener dürfen Speichern nicht verhindern
                log.exception("Settings-Listener fehlgeschlagen")
        return new

    async def reset(self) -> UserSettings:
        defaults = UserSettings.from_env(self._env)
        return await self.update(defaults.model_dump(mode="json"))

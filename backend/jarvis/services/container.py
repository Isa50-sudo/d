"""Service-Container: verdrahtet alle Komponenten von JARVIS."""
from __future__ import annotations

import asyncio
import socket
import time
from typing import TYPE_CHECKING

from jarvis.ai.ollama import OllamaProvider
from jarvis.config.env import EnvSettings
from jarvis.config.user_settings import SettingsStore, UserSettings
from jarvis.core.eventlog import EventLog
from jarvis.core.hub import Hub
from jarvis.core.logging_setup import set_level
from jarvis.core.paths import DATABASE_FILE
from jarvis.memory.store import ConversationBuffer, MemoryStore
from jarvis.permissions.confirmation import ConfirmationManager
from jarvis.permissions.manager import PermissionManager
from jarvis.services.email_service import EmailService
from jarvis.services.notifications import NotificationService
from jarvis.system.monitor import SystemMonitor
from jarvis.tools.manager import ToolManager
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.stt import SpeechToText
from jarvis.voice.tts import TextToSpeech
from jarvis.web.http import WebClient

if TYPE_CHECKING:
    from jarvis.ai.live_session import LiveSession


class Services:
    def __init__(self, env: EnvSettings) -> None:
        self.env = env
        self.started_at = time.time()
        self.hub = Hub()
        self.events = EventLog(self.hub)
        self.settings = SettingsStore(env)
        self.ai = OllamaProvider(env)
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self.monitor = SystemMonitor()
        self.memory = MemoryStore(DATABASE_FILE)
        self.conversation = ConversationBuffer(self.settings.current.memory.short_term_turns)
        self.confirmations = ConfirmationManager(self.hub)
        self.permissions = PermissionManager(lambda: self.settings.current)
        self.registry = ToolRegistry()
        self.registry.load_builtin()
        self.tools = ToolManager(self.registry, self.permissions, self)
        self.web = WebClient(timeout_s=self.settings.current.web.request_timeout_s)
        self.email = EmailService(env)
        self.notifications = NotificationService(self)
        self.live_sessions: dict[str, "LiveSession"] = {}
        self._internet_cache: tuple[float, bool] = (0.0, False)
        self.settings.on_change(self._on_settings_changed)

    async def start(self) -> None:
        self.notifications.start()

    async def stop(self) -> None:
        for session in list(self.live_sessions.values()):
            await session.close()
        await self.notifications.stop()
        await self.web.close()
        await self.ai.close()
        self.memory.close()

    async def check_internet(self) -> bool:
        ts, value = self._internet_cache
        if time.time() - ts < 30:
            return value

        def _probe() -> bool:
            for host in (("dns.google", 443), ("duckduckgo.com", 443)):
                try:
                    socket.getaddrinfo(*host)
                    return True
                except OSError:
                    continue
            return False

        value = await asyncio.to_thread(_probe)
        self._internet_cache = (time.time(), value)
        return value

    def _on_settings_changed(self, old: UserSettings, new: UserSettings) -> None:
        set_level(new.logging.level)
        self.conversation.resize(new.memory.short_term_turns)
        session_relevant = (
            old.ai != new.ai
            or old.voice != new.voice
            or old.conversation.activation_mode != new.conversation.activation_mode
            or old.conversation.wake_word != new.conversation.wake_word
            or old.conversation.vad_sensitivity != new.conversation.vad_sensitivity
            or old.permissions.access_level != new.permissions.access_level
            or old.web.enabled != new.web.enabled
            or old.memory != new.memory
        )
        if session_relevant:
            for session in self.live_sessions.values():
                session.reset_for_new_config()
        self.events.add("settings", "Einstellungen gespeichert", "info")

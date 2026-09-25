"""Gemini-Live-Session: Echtzeit-Sprachdialog zwischen Browser und Gemini.

Datenfluss:
  Browser-Mikrofon --PCM16 16 kHz (WebSocket binär)--> LiveSession
      --> Gemini Live API (send_realtime_input(audio=...))
  Gemini --PCM16 24 kHz + Transkripte + Tool-Calls--> LiveSession
      --> Browser (Audio binär, Events als JSON)
  Tool-Calls --> ToolManager (Validierung, Berechtigung, Bestätigung) --> Gemini

Features: Server-VAD mit Unterbrechung (Barge-in), Ein-/Ausgabe-Transkription,
Function Calling, Google-Suche (Grounding, inkl. Quellenanzeige),
Kontextfenster-Kompression, Session-Resumption bei GoAway/Verbindungsabbruch,
automatisches Schließen bei Inaktivität.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from google.genai import errors as genai_errors
from google.genai import types

from jarvis.ai.gemini import MSG_NO_KEY, MSG_UNREACHABLE, ModelCapabilities
from jarvis.ai.prompts import build_system_instruction
from jarvis.voice.profile import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, VoiceProfile

if TYPE_CHECKING:
    from jarvis.core.hub import ClientConnection
    from jarvis.services.container import Services

log = logging.getLogger(__name__)

AUDIO_MIME = f"audio/pcm;rate={INPUT_SAMPLE_RATE}"
_END = object()


class GeminiUnavailable(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class LiveSession:
    def __init__(self, services: "Services", client: "ClientConnection") -> None:
        self.services = services
        self.client = client
        self._audio_q: asyncio.Queue[Any] = asyncio.Queue(maxsize=400)
        self._runner: asyncio.Task[None] | None = None
        self._session: Any = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._resume_handle: str | None = None
        self._tool_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity = time.time()
        self._user_buf = ""
        self._jarvis_buf = ""
        self._model_active = False
        self._pending_announcements: list[str] = []
        self._model = ""
        self._caps = ModelCapabilities.for_model("")
        self._fatal_error: str | None = None
        self._idle_closed = False

    # ------------------------------------------------------------------
    # Öffentliche API (vom WebSocket-Handler aufgerufen)
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._session is not None and self._ready.is_set()

    @property
    def active(self) -> bool:
        """Session läuft oder baut sich gerade auf (Audio wird dann gepuffert)."""
        return self._runner is not None and not self._runner.done()

    def start_nowait(self) -> None:
        """Startet den Verbindungsaufbau sofort (synchron), damit nachfolgendes Audio gepuffert wird."""
        if not self.services.gemini.configured:
            raise GeminiUnavailable(MSG_NO_KEY)
        if self._runner is None or self._runner.done():
            self._stop.clear()
            self._ready.clear()
            self._fatal_error = None
            self._idle_closed = False
            while not self._audio_q.empty():  # kein veraltetes Audio in die neue Session
                self._audio_q.get_nowait()
            self._last_activity = time.time()
            self._runner = asyncio.create_task(self._run(), name=f"live-{self.client.id}")

    async def ensure_started(self, timeout: float = 20.0) -> None:
        self.start_nowait()
        if self._ready.is_set():
            return
        assert self._runner is not None
        ready = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait({ready, self._runner}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if ready not in done:
            ready.cancel()
            raise GeminiUnavailable(self._fatal_error or MSG_UNREACHABLE)

    async def push_audio(self, pcm: bytes) -> None:
        self._last_activity = time.time()
        if self._audio_q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio_q.get_nowait()  # älteste Daten verwerfen statt zu blockieren
        self._audio_q.put_nowait(pcm)

    async def end_audio(self) -> None:
        """Mikrofon pausiert (Push-to-talk losgelassen / Wake-Modus beendet)."""
        with contextlib.suppress(asyncio.QueueFull):
            self._audio_q.put_nowait(_END)

    async def send_text(self, text: str) -> None:
        await self.ensure_started()
        self._last_activity = time.time()
        self._finalize_user()
        self.services.conversation.add("user", text)
        if self.services.settings.current.logging.log_transcripts:
            self.services.events.add("conversation", f"Benutzer (Text): {text}")
        await self._session.send_realtime_input(text=text)

    async def announce(self, text: str) -> None:
        """Lässt JARVIS eine Systembenachrichtigung aussprechen (nur bei aktiver Session)."""
        if not self.connected:
            return
        if self._model_active or self._tool_tasks:
            self._pending_announcements.append(text)
            return
        await self._inject_announcement(text)

    async def close(self, reason: str = "closed") -> None:
        self._stop.set()
        for task in list(self._tool_tasks.values()):
            task.cancel()
        self.services.confirmations.cancel_all(self.client.id)
        if self._runner and not self._runner.done():
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
        self._runner = None
        self._session = None
        self._ready.clear()
        await self._status("standby" if reason == "idle" else "closed")

    def reset_for_new_config(self) -> None:
        """Nach Settings-Änderung (Modell/Stimme): nächste Aktivierung startet neu."""
        self._resume_handle = None
        if self._runner and not self._runner.done():
            asyncio.create_task(self.close("reconfigure"))

    # ------------------------------------------------------------------
    # Verbindungsaufbau
    # ------------------------------------------------------------------
    async def _build_config(self) -> types.LiveConnectConfig:
        settings = self.services.settings.current
        voice = VoiceProfile.from_settings(settings.voice)
        self._model = settings.ai.live_model
        self._caps = ModelCapabilities.for_model(self._model)

        memories = ""
        if settings.memory.enabled and settings.memory.inject_into_context:
            memories = await self.services.memory.context_block()
        recent = "" if self._resume_handle else self.services.conversation.as_text()

        instruction = build_system_instruction(
            voice=voice,
            activation_mode=settings.conversation.activation_mode,
            wake_word=settings.conversation.wake_word,
            memories=memories,
            recent_conversation=recent,
            google_search=settings.ai.google_search,
            web_enabled=settings.web.enabled,
            access_level=settings.permissions.access_level,
        )

        behavior = None if self._caps.legacy else types.Behavior.NON_BLOCKING
        declarations = self.services.registry.to_function_declarations(
            self.services.registry.active(self.services), behavior
        )
        tools: list[types.Tool] = [types.Tool(function_declarations=declarations)]
        if settings.ai.google_search and settings.web.enabled:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        sensitivity = settings.conversation.vad_sensitivity
        vad = types.AutomaticActivityDetection(
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH
            if sensitivity == "high"
            else types.StartSensitivity.START_SENSITIVITY_LOW
            if sensitivity == "low"
            else None,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW if sensitivity == "low" else None,
            silence_duration_ms=700 if sensitivity == "low" else None,
        )

        config: dict[str, Any] = {
            "response_modalities": [types.Modality.AUDIO],
            "speech_config": voice.speech_config(),
            "system_instruction": types.Content(parts=[types.Part(text=instruction)]),
            "tools": tools,
            "input_audio_transcription": types.AudioTranscriptionConfig(),
            "output_audio_transcription": types.AudioTranscriptionConfig(),
            "realtime_input_config": types.RealtimeInputConfig(
                automatic_activity_detection=vad,
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            "context_window_compression": types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            "session_resumption": types.SessionResumptionConfig(handle=self._resume_handle),
        }
        if self._caps.extended_thinking:
            config["thinking_config"] = types.ThinkingConfig(thinking_level="low")
        return types.LiveConnectConfig(**config)

    async def _run(self) -> None:
        attempts = 0
        await self._status("connecting")
        while not self._stop.is_set():
            try:
                config = await self._build_config()
                client = self.services.gemini.client()
                connected_at = time.time()
                async with client.aio.live.connect(model=self._model, config=config) as session:
                    self._session = session
                    self._ready.set()
                    self.services.gemini.mark("ok")
                    await self._status("connected")
                    self.services.events.add(
                        "gemini", f"Gemini Live verbunden ({self._model}, Stimme {config.speech_config.voice_config.prebuilt_voice_config.voice_name})", "success"  # type: ignore[union-attr]
                    )
                    reconnect = await self._pump(session)
                self._session = None
                self._ready.clear()
                if not reconnect:
                    break
                if time.time() - connected_at >= 5:
                    attempts = 0
                else:
                    attempts += 1  # Schutz vor Endlos-Reconnect-Schleifen
                    if attempts >= 3:
                        raise ConnectionError("Gemini-Verbindung bricht wiederholt ab")
                    await asyncio.sleep(attempts)
                self.services.events.add("gemini", "Gemini-Verbindung wird erneuert (Session-Resumption)", "info")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._session = None
                self._ready.clear()
                attempts += 1
                user_msg = self._friendly_error(exc)
                log.warning("Gemini Live Fehler (Versuch %d): %r", attempts, exc)
                self.services.events.add("gemini", f"Gemini-Fehler: {user_msg}", "error", {"detail": type(exc).__name__})
                self.services.gemini.mark("error", user_msg)
                if self._resume_handle and attempts == 1:
                    # Evtl. abgelaufener Resumption-Handle – frisch verbinden
                    self._resume_handle = None
                    continue
                if attempts >= 3 or self._is_fatal(exc):
                    self._fatal_error = user_msg
                    await self._status("error", user_msg)
                    await self.client.send_event("error", {"message": user_msg, "source": "gemini"})
                    return
                await asyncio.sleep(1.5 * attempts)
        self._session = None
        self._ready.clear()
        await self._status("standby" if self._idle_closed else "closed")

    @staticmethod
    def _is_fatal(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(k in text for k in ("api key", "permission", "not found", "invalid", "unsupported", "1007", "1008"))

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc)
        low = text.lower()
        if "api key" in low or "api_key" in low or "permission" in low or "401" in text or "403" in text:
            return "Der Gemini API Key wurde abgelehnt. Bitte GEMINI_API_KEY in der .env prüfen."
        if "not found" in low or "is not supported" in low or "model" in low and "invalid" in low:
            return "Das eingestellte Gemini-Modell ist nicht verfügbar. Bitte in den Settings ein anderes Modell wählen."
        if "quota" in low or "429" in text or "resource_exhausted" in low:
            return "Das Gemini-Kontingent ist aktuell erschöpft. Bitte später erneut versuchen."
        return MSG_UNREACHABLE

    async def _pump(self, session: Any) -> bool:
        """Läuft bis zum Ende der Verbindung. True = neu verbinden (Resumption)."""
        sender = asyncio.create_task(self._sender(session))
        receiver = asyncio.create_task(self._receiver(session))
        watchdog = asyncio.create_task(self._idle_watchdog())
        stopper = asyncio.create_task(self._stop.wait())
        done, pending = await asyncio.wait({sender, receiver, watchdog, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if stopper in done:
            return False
        if watchdog in done:
            self.services.events.add("gemini", "Gemini-Session wegen Inaktivität pausiert", "info")
            self._idle_closed = True
            self._stop.set()
            return False
        if receiver in done:
            exc = receiver.exception()
            if exc is None or isinstance(exc, _GoAway):
                return True
            if isinstance(exc, genai_errors.APIError) and self._resume_handle:
                return True  # Verbindung abgebrochen -> mit Resumption-Handle fortsetzen
            raise exc
        exc = sender.exception() if sender in done else None
        if exc is not None:
            raise exc
        return bool(self._resume_handle)

    async def _idle_watchdog(self) -> None:
        while True:
            await asyncio.sleep(5)
            timeout = self.services.settings.current.ai.session_idle_timeout_s
            if (
                time.time() - self._last_activity > timeout
                and not self._tool_tasks
                and not self._model_active
                and not self.services.confirmations.pending
            ):
                return

    # ------------------------------------------------------------------
    # Senden
    # ------------------------------------------------------------------
    async def _sender(self, session: Any) -> None:
        while True:
            item = await self._audio_q.get()
            if item is _END:
                await session.send_realtime_input(audio_stream_end=True)
                continue
            await session.send_realtime_input(audio=types.Blob(data=item, mime_type=AUDIO_MIME))

    # ------------------------------------------------------------------
    # Empfangen
    # ------------------------------------------------------------------
    async def _receiver(self, session: Any) -> None:
        while True:
            async for message in session.receive():
                self._last_activity = time.time()
                await self._handle(message)

    async def _handle(self, msg: types.LiveServerMessage) -> None:
        if msg.session_resumption_update and msg.session_resumption_update.resumable and msg.session_resumption_update.new_handle:
            self._resume_handle = msg.session_resumption_update.new_handle

        if msg.voice_activity and msg.voice_activity.voice_activity_type:
            kind = msg.voice_activity.voice_activity_type.value
            await self.client.send_event("voice.activity", {"activity": "start" if kind == "ACTIVITY_START" else "end"})

        content = msg.server_content
        if content is not None:
            await self._handle_content(content)

        if msg.tool_call and msg.tool_call.function_calls:
            self._finalize_user()
            for call in msg.tool_call.function_calls:
                call_id = call.id or f"call-{time.time_ns()}"
                task = asyncio.create_task(self._run_tool(call_id, call.name or "", dict(call.args or {})))
                self._tool_tasks[call_id] = task

        if msg.tool_call_cancellation and msg.tool_call_cancellation.ids:
            for call_id in msg.tool_call_cancellation.ids:
                task = self._tool_tasks.pop(call_id, None)
                if task:
                    task.cancel()
                    self.services.events.add("tool", "Tool-Aufruf vom Modell abgebrochen", "warning")

        if msg.go_away is not None:
            self.services.events.add("gemini", "Gemini kündigt Verbindungsende an (GoAway)", "info")
            raise _GoAway()

    async def _handle_content(self, content: types.LiveServerContent) -> None:
        if content.input_transcription and content.input_transcription.text:
            self._user_buf += content.input_transcription.text
            await self.client.send_event("transcript", {"role": "user", "text": self._user_buf, "final": False})

        if content.model_turn and content.model_turn.parts:
            for part in content.model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    if not self._model_active:
                        self._model_active = True
                        self._finalize_user()
                        await self.client.send_event("model.speaking", {"sample_rate": OUTPUT_SAMPLE_RATE})
                    await self.client.send_audio(part.inline_data.data)

        if content.output_transcription and content.output_transcription.text:
            self._finalize_user()
            self._jarvis_buf += content.output_transcription.text
            await self.client.send_event("transcript", {"role": "jarvis", "text": self._jarvis_buf, "final": False})

        if content.grounding_metadata:
            await self._handle_grounding(content.grounding_metadata)

        if content.interrupted:
            self._model_active = False
            self._finalize_jarvis(interrupted=True)
            await self.client.send_event("interrupted", {})

        status = content.interaction_status.value if content.interaction_status else None
        if status:
            await self.client.send_event("interaction.status", {"status": status})

        turn_done = bool(content.turn_complete) and (not self._caps.extended_thinking or status in (None, "IDLE"))
        if turn_done or status == "IDLE":
            self._model_active = False
            self._finalize_user()
            self._finalize_jarvis()
            await self.client.send_event("turn.complete", {})
            await self._flush_announcements()

    async def _handle_grounding(self, meta: types.GroundingMetadata) -> None:
        items = []
        for chunk in meta.grounding_chunks or []:
            if chunk.web and chunk.web.uri:
                items.append({"title": chunk.web.title or chunk.web.domain or "Quelle", "uri": chunk.web.uri, "publisher": chunk.web.domain or chunk.web.title})
        queries = list(meta.web_search_queries or [])
        if queries:
            self.services.events.add("web", "Google-Suche durchgeführt", "info", {"queries": queries} if self.services.settings.current.logging.log_transcripts else None)
            await self.client.send_event("search", {"queries": queries})
        if items:
            from jarvis.web.sources import now_iso

            ts = now_iso()
            for item in items:
                item["retrieved_at"] = ts
            await self.client.send_event("sources", {"sources": items[:8], "via": "Google Search"})

    def _finalize_user(self) -> None:
        text = self._user_buf.strip()
        if not text:
            return
        self._user_buf = ""
        self.services.conversation.add("user", text)
        if self.services.settings.current.logging.log_transcripts:
            self.services.events.add("conversation", f"Benutzer: {text}")
        asyncio.create_task(self.client.send_event("transcript", {"role": "user", "text": text, "final": True}))

    def _finalize_jarvis(self, interrupted: bool = False) -> None:
        text = self._jarvis_buf.strip()
        if not text:
            return
        self._jarvis_buf = ""
        self.services.conversation.add("jarvis", text + (" …" if interrupted else ""))
        if self.services.settings.current.logging.log_transcripts:
            self.services.events.add("conversation", f"JARVIS: {text}")
        asyncio.create_task(self.client.send_event("transcript", {"role": "jarvis", "text": text, "final": True, "interrupted": interrupted}))

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    async def _run_tool(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        try:
            result = await self.services.tools.execute(name, args, client=self.client, call_id=call_id)
            session = self._session
            if session is None:
                return
            response_kwargs: dict[str, Any] = {"id": call_id, "name": name, "response": result}
            if self._caps.supports_scheduling:
                response_kwargs["scheduling"] = types.FunctionResponseScheduling.WHEN_IDLE
            await session.send_tool_response(function_responses=[types.FunctionResponse(**response_kwargs)])
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Tool-Antwort an Gemini fehlgeschlagen")
        finally:
            self._tool_tasks.pop(call_id, None)
            if not self._tool_tasks:
                await self._flush_announcements()

    # ------------------------------------------------------------------
    # Benachrichtigungen
    # ------------------------------------------------------------------
    async def _flush_announcements(self) -> None:
        if self._pending_announcements and not self._model_active and not self._tool_tasks:
            text = " ".join(self._pending_announcements)
            self._pending_announcements.clear()
            await self._inject_announcement(text)

    async def _inject_announcement(self, text: str) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_client_content(
                turns=[
                    types.Content(
                        role="user",
                        parts=[types.Part(text=f"[Systembenachrichtigung – teile dem Benutzer dies kurz und ruhig mit, ohne Rückfrage]: {text}")],
                    )
                ],
                turn_complete=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("Benachrichtigung konnte nicht gesprochen werden")

    async def _status(self, state: str, message: str | None = None) -> None:
        await self.client.send_event("gemini.status", {"state": state, "message": message, "model": self._model or self.services.settings.current.ai.live_model})


class _GoAway(Exception):
    pass

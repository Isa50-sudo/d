"""Sprachsitzung: Echtzeit-Dialog zwischen Browser und lokalem Sprachmodell (Ollama).

Datenfluss (vollständig lokal):
  Browser-Mikrofon --PCM16 16 kHz (WebSocket binär)--> LiveSession
      --> VAD (Äußerung erkennen) --> faster-whisper (Text + Sprache)
      --> Ollama /api/chat (Streaming + Tool-Calling) --> ToolManager
      --> Piper TTS (satzweise) --PCM16 (WebSocket binär)--> Browser-Lautsprecher

Features: satzweises Sprechen noch während das Modell schreibt (geringe Latenz),
Barge-in (Dazwischenreden unterbricht JARVIS), mehrstufige Tool-Aufrufe,
automatische Antwortsprache (erkannt von Whisper), Kurzzeit-Kontext,
Textchat als Fallback über dieselbe Pipeline.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from jarvis.ai.ollama import MSG_UNREACHABLE, OllamaError
from jarvis.ai.prompts import LANG_NAMES, build_system_instruction
from jarvis.voice.profile import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, VoiceProfile
from jarvis.voice.stt import STTUnavailable
from jarvis.voice.tts import TTSUnavailable
from jarvis.voice.vad import SpeechSegmenter, VadConfig

if TYPE_CHECKING:
    from jarvis.core.hub import ClientConnection
    from jarvis.services.container import Services

log = logging.getLogger(__name__)

_END = object()
MAX_TOOL_ROUNDS = 6
STT_TIMEOUT_S = 900  # inkl. einmaligem Modell-Download
OLLAMA_FIRST_TOKEN_TIMEOUT_S = 180
OLLAMA_CHUNK_TIMEOUT_S = 90
AUDIO_CHUNK_BYTES = 16 * 1024
# Satzende: . ! ? … : ; oder Zeilenumbruch, gefolgt von Leerraum
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class AIUnavailable(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class _Speaker:
    """Wandelt Sätze nacheinander in Sprache um und streamt sie zum Browser."""

    def __init__(self, session: "LiveSession", voice: str, speed: str) -> None:
        self.session = session
        self.voice = voice
        self.speed = speed
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run())
        self.started = False

    def say(self, text: str) -> None:
        if text.strip():
            self.queue.put_nowait(text.strip())

    async def finish(self) -> None:
        self.queue.put_nowait(None)
        await self.task

    def cancel(self) -> None:
        self.task.cancel()

    async def _run(self) -> None:
        s = self.session
        while True:
            text = await self.queue.get()
            if text is None:
                return
            if not s.tts_ok:
                continue
            try:
                pcm = await s.services.tts.synthesize(text, voice=self.voice, speed=self.speed)
            except TTSUnavailable as exc:
                s.tts_ok = False
                await s.client.send_event("error", {"message": f"{exc.user_message} JARVIS antwortet bis dahin nur als Text.", "source": "tts"})
                continue
            except Exception:  # noqa: BLE001
                log.exception("Sprachausgabe fehlgeschlagen")
                continue
            if not pcm:
                continue
            if not self.started:
                self.started = True
                await s.client.send_event("model.speaking", {"sample_rate": OUTPUT_SAMPLE_RATE})
            now = time.monotonic()
            s.speak_until = max(s.speak_until, now) + len(pcm) / 2 / OUTPUT_SAMPLE_RATE
            for i in range(0, len(pcm), AUDIO_CHUNK_BYTES):
                await s.client.send_audio(pcm[i : i + AUDIO_CHUNK_BYTES])


class LiveSession:
    def __init__(self, services: "Services", client: "ClientConnection") -> None:
        self.services = services
        self.client = client
        self._audio_q: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
        self._runner: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._fatal_error: str | None = None
        self._turn: asyncio.Task[None] | None = None
        self._pending: tuple[str, Any] | None = None
        self._in_tool = False
        self._stt_task: asyncio.Task[None] | None = None
        self._segmenter = SpeechSegmenter()
        self._model = ""
        self.tts_ok = True
        self.speak_until = 0.0
        self._memories = ""
        self._partial = ""

    # ------------------------------------------------------------------
    # Öffentliche API (vom WebSocket-Handler aufgerufen)
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._ready.is_set() and self.active

    @property
    def active(self) -> bool:
        """Sitzung läuft oder startet gerade (Audio wird dann gepuffert)."""
        return self._runner is not None and not self._runner.done()

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self.speak_until

    @property
    def responding(self) -> bool:
        return (self._turn is not None and not self._turn.done()) or self.speaking

    def start_nowait(self) -> None:
        """Startet die Sitzung sofort (synchron), damit nachfolgendes Audio gepuffert wird."""
        if self._runner is None or self._runner.done():
            self._ready.clear()
            self._fatal_error = None
            while not self._audio_q.empty():  # kein veraltetes Audio in die neue Sitzung
                self._audio_q.get_nowait()
            self._segmenter = SpeechSegmenter(VadConfig.for_sensitivity(self.services.settings.current.conversation.vad_sensitivity))
            self._runner = asyncio.create_task(self._run(), name=f"voice-{self.client.id}")

    async def ensure_started(self, timeout: float = 60.0) -> None:
        self.start_nowait()
        if self._ready.is_set():
            return
        assert self._runner is not None
        ready = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait({ready, self._runner}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if ready not in done:
            ready.cancel()
            raise AIUnavailable(self._fatal_error or MSG_UNREACHABLE)

    async def push_audio(self, pcm: bytes) -> None:
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
        self.services.conversation.add("user", text)
        if self.services.settings.current.logging.log_transcripts:
            self.services.events.add("conversation", f"Benutzer (Text): {text}")
        await self._schedule("respond", (text, None))

    async def announce(self, text: str) -> None:
        """Lässt JARVIS eine Systembenachrichtigung aussprechen."""
        if not self.connected:
            return
        await self._schedule("announce", text, interrupt=False)

    async def close(self, reason: str = "closed") -> None:
        for task in (self._turn, self._stt_task, self._runner):
            if task and not task.done():
                task.cancel()
        self.services.confirmations.cancel_all(self.client.id)
        for task in (self._turn, self._runner):
            if task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._runner = None
        self._turn = None
        self._ready.clear()
        await self._status("closed")

    def reset_for_new_config(self) -> None:
        """Nach Settings-Änderung: Sitzung neu aufbauen (Modell/Stimme werden neu geprüft/geladen)."""
        if self.active:
            asyncio.create_task(self.close("reconfigure"))

    # ------------------------------------------------------------------
    # Start: Ollama prüfen, Stimme + Spracherkennung laden
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        settings = self.services.settings.current
        self._model = settings.ai.model
        await self._status("connecting")
        ok, message = await self.services.ai.check(self._model)
        if not ok:
            self._fatal_error = message
            self.services.events.add("ai", f"Ollama nicht bereit: {message}", "error")
            await self._status("error", message)
            await self.client.send_event("error", {"message": message, "source": "ai"})
            return

        voice = VoiceProfile.from_settings(settings.voice)
        self.tts_ok = True
        try:
            await self.services.tts.preload_async(voice.name)
        except TTSUnavailable as exc:
            self.tts_ok = False
            await self.client.send_event("error", {"message": f"{exc.user_message} JARVIS antwortet bis dahin nur als Text.", "source": "tts"})
        self._stt_started = time.monotonic()
        if not self.services.stt.is_ready(settings.ai.stt_model, settings.ai.stt_device):
            self.services.events.add(
                "voice",
                f"Lade Spracherkennung (Whisper {settings.ai.stt_model}) – beim ersten Mal wird das Modell heruntergeladen, das kann einige Minuten dauern",
                "info",
            )
        self._stt_task = asyncio.create_task(self.services.stt.load(settings.ai.stt_model, settings.ai.stt_device))
        self._stt_task.add_done_callback(self._stt_loaded)

        self._ready.set()
        await self._status("connected", message)
        self.services.events.add("ai", f"Sprachsitzung bereit (Ollama {self._model}, Stimme {voice.name})", "success")

        while True:
            item = await self._audio_q.get()
            self._segmenter.boost = 2.5 if self.speaking else 1.0
            events = self._segmenter.flush() if item is _END else self._segmenter.feed(item)
            for kind, pcm in events:
                await self._on_vad(kind, pcm)

    def _stt_loaded(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            message = exc.user_message if isinstance(exc, STTUnavailable) else f"Spracherkennung konnte nicht geladen werden ({type(exc).__name__}: {exc})"
            log.error("Whisper-Laden fehlgeschlagen: %r", exc)
            self.services.events.add("voice", message, "error")
            asyncio.create_task(self.client.send_event("error", {"message": message, "source": "stt"}))
        else:
            stt = self.services.stt
            secs = time.monotonic() - getattr(self, "_stt_started", time.monotonic())
            self.services.events.add("voice", f"Spracherkennung (Whisper) geladen – {(stt.device_in_use or 'cpu').upper()} ({secs:.1f} s)", "success")
            if stt.notice:
                self.services.events.add("voice", stt.notice, "warning")
                asyncio.create_task(self.client.send_event("notification", {"title": "Spracherkennung", "message": stt.notice, "level": "warning"}))

    async def _on_vad(self, kind: str, pcm: bytes | None) -> None:
        if kind == "start":
            await self.client.send_event("voice.activity", {"activity": "start"})
            if self.responding and not self._in_tool:
                await self._interrupt()  # Barge-in: der Benutzer redet dazwischen
            return
        await self.client.send_event("voice.activity", {"activity": "end"})
        if not pcm:
            await self.client.send_event("turn.complete", {})  # nur Geräusch
            return
        await self._schedule("utterance", pcm)

    # ------------------------------------------------------------------
    # Turn-Verwaltung
    # ------------------------------------------------------------------
    async def _schedule(self, kind: str, payload: Any, interrupt: bool = True) -> None:
        busy = self._turn is not None and not self._turn.done()
        if busy and (self._in_tool or not interrupt):
            # Während ein Tool läuft (z. B. Bestätigungsdialog) nicht abbrechen – danach verarbeiten
            self._pending = (kind, payload)
            return
        if busy:
            await self._interrupt()
        self._turn = asyncio.create_task(self._run_turn(kind, payload))

    async def _interrupt(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._turn
        self.speak_until = 0.0
        await self.client.send_event("interrupted", {})

    async def _run_turn(self, kind: str, payload: Any) -> None:
        try:
            if kind == "utterance":
                await self._handle_utterance(payload)
            elif kind == "respond":
                await self._respond(*payload)
            elif kind == "announce":
                await self._speak_only(payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Fehler im Gesprächsablauf")
            await self.client.send_event("error", {"message": "Ich konnte diese Anfrage nicht verarbeiten.", "source": "ai"})
            await self.client.send_event("turn.complete", {})
        # Während eines Tools eingegangene Äußerung/Benachrichtigung jetzt verarbeiten
        pending, self._pending = self._pending, None
        if pending:
            self._turn = asyncio.create_task(self._run_turn(*pending))

    async def _handle_utterance(self, pcm: bytes) -> None:
        settings = self.services.settings.current
        voice = VoiceProfile.from_settings(settings.voice)
        seconds = len(pcm) / 2 / INPUT_SAMPLE_RATE
        if not self.services.stt.is_ready(settings.ai.stt_model, settings.ai.stt_device):
            await self.client.send_event(
                "notification",
                {"title": "Einen Moment", "message": "Die Spracherkennung wird noch geladen (beim ersten Mal inkl. Download). Deine Frage wird danach beantwortet.", "level": "info"},
            )
        started = time.monotonic()
        try:
            # transcribe() lädt das Modell bei Bedarf selbst – nie auf eine (evtl. abgebrochene) Lade-Task warten
            transcript = await asyncio.wait_for(
                self.services.stt.transcribe(
                    pcm,
                    language=voice.primary_language if voice.lock_language else None,
                    model_name=settings.ai.stt_model,
                    device=settings.ai.stt_device,
                ),
                timeout=STT_TIMEOUT_S,
            )
        except STTUnavailable as exc:
            await self._fail(exc.user_message, "stt")
            return
        except asyncio.TimeoutError:
            await self._fail("Die Spracherkennung hat zu lange gebraucht. Wähle in SETTINGS ein kleineres Whisper-Modell (z. B. small oder base).", "stt")
            return
        took = time.monotonic() - started
        self.services.events.add(
            "voice",
            f"Spracheingabe ({seconds:.1f} s) erkannt in {took:.1f} s – {len(transcript.text)} Zeichen, Sprache {transcript.language or '?'}",
            "info" if transcript.text else "warning",
        )
        if not transcript.text:
            await self.client.send_event("notification", {"title": "Nicht verstanden", "message": "Ich habe dich nicht verstanden – bitte noch einmal.", "level": "info"})
            await self.client.send_event("turn.complete", {})
            return
        await self.client.send_event("transcript", {"role": "user", "text": transcript.text, "final": True, "language": transcript.language})
        self.services.conversation.add("user", transcript.text)
        if settings.logging.log_transcripts:
            self.services.events.add("conversation", f"Benutzer: {transcript.text}")
        await self._respond(transcript.text, transcript.language)

    async def _fail(self, message: str, source: str) -> None:
        """Fehler sichtbar machen und den Turn sauber beenden (nie in THINKING hängen bleiben)."""
        self.services.events.add(source if source in ("ai", "voice") else "voice", message, "error")
        await self.client.send_event("error", {"message": message, "source": source})
        await self.client.send_event("turn.complete", {})

    def _messages(self, language: str | None, voice: VoiceProfile) -> list[dict[str, Any]]:
        settings = self.services.settings.current
        system = build_system_instruction(
            voice=voice,
            activation_mode=settings.conversation.activation_mode,
            wake_word=settings.conversation.wake_word,
            memories=self._memories,
            recent_conversation="",
            web_enabled=settings.web.enabled,
            access_level=settings.permissions.access_level,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in self.services.conversation.turns():
            messages.append({"role": "user" if turn.role == "user" else "assistant", "content": turn.text})
        if language and not voice.lock_language and language != voice.primary_language:
            name = LANG_NAMES.get(language, language)
            messages.insert(-1, {"role": "system", "content": f"Der Benutzer spricht gerade {name}. Antworte auf {name}."})
        return messages

    async def _respond(self, user_text: str, language: str | None) -> None:
        settings = self.services.settings.current
        voice = VoiceProfile.from_settings(settings.voice)
        self._memories = ""
        self._partial = ""
        if settings.memory.enabled and settings.memory.inject_into_context:
            self._memories = await self.services.memory.context_block()
        messages = self._messages(language, voice)
        tools = self.services.registry.to_ollama_tools(self.services.registry.active(self.services))
        speaker = _Speaker(self, voice.voice_for(language), voice.speed)
        spoken = ""
        self._partial = ""
        interrupted = False
        started = time.monotonic()
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                content, calls = await self._stream_round(messages, tools, speaker, spoken)
                self.services.events.add(
                    "ai",
                    f"Ollama-Antwort (Runde {_round + 1}) nach {time.monotonic() - started:.1f} s – {len(content)} Zeichen, {len(calls)} Tool-Aufruf(e)",
                    "info",
                )
                spoken = (spoken + " " + content).strip() if content else spoken
                if not calls:
                    break
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for call in calls:
                    messages.append(await self._run_tool_call(call))
            await speaker.finish()
        except asyncio.CancelledError:
            interrupted = True
            spoken = self._partial or spoken  # bis zur Unterbrechung Geschriebenes
            speaker.cancel()
            raise
        except asyncio.TimeoutError:
            message = (
                f"Ollama hat nach {OLLAMA_FIRST_TOKEN_TIMEOUT_S} s nicht geantwortet. Läuft Ollama? "
                "Evtl. ist das Modell zu groß für deinen Rechner (in SETTINGS z. B. qwen3:4b wählen)."
            )
            self.services.events.add("ai", message, "error")
            await self.client.send_event("error", {"message": message, "source": "ai"})
        except OllamaError as exc:
            log.warning("Ollama-Fehler: %s", exc)
            self.services.events.add("ai", f"Ollama-Fehler: {exc.user_message}", "error")
            await self.client.send_event("error", {"message": exc.user_message, "source": "ai"})
            speaker.say("Ich kann mein Sprachmodell gerade nicht erreichen.")
            await speaker.finish()
        finally:
            if spoken:
                self.services.conversation.add("jarvis", spoken + (" …" if interrupted else ""))
                if settings.logging.log_transcripts:
                    self.services.events.add("conversation", f"JARVIS: {spoken}")
                with contextlib.suppress(Exception):
                    await self.client.send_event("transcript", {"role": "jarvis", "text": spoken, "final": True, "interrupted": interrupted})
            if not interrupted:
                await self.client.send_event("turn.complete", {})

    async def _stream_round(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], speaker: _Speaker, spoken_before: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Eine Modellantwort streamen; fertige Sätze sofort vorlesen."""
        settings = self.services.settings.current
        raw = ""
        emitted = 0  # bereits vorgelesener Anteil von `visible`
        calls: list[dict[str, Any]] = []
        stream = self.services.ai.chat_stream(
            model=settings.ai.model,
            messages=messages,
            tools=tools or None,
            temperature=settings.ai.temperature,
            context_tokens=settings.ai.context_tokens,
            disable_thinking=settings.ai.disable_thinking,
        ).__aiter__()
        first = True
        try:
            while True:
                try:
                    # Erste Antwort darf länger dauern (Ollama lädt das Modell in den Speicher)
                    timeout = OLLAMA_FIRST_TOKEN_TIMEOUT_S if first else OLLAMA_CHUNK_TIMEOUT_S
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                first = False
                message = chunk.get("message") or {}
                calls.extend(message.get("tool_calls") or [])
                piece = message.get("content") or ""
                if not piece:
                    continue
                raw += piece
                visible = _visible_text(raw)
                # Vollständige Sätze an die Sprachausgabe geben
                pending = visible[emitted:]
                parts = _SENTENCE_END.split(pending)
                if len(parts) > 1:
                    complete = pending[: len(pending) - len(parts[-1])]
                    speaker.say(complete)
                    emitted += len(complete)
                if visible.strip():
                    self._partial = (spoken_before + " " + visible).strip()
                    await self.client.send_event("transcript", {"role": "jarvis", "text": self._partial, "final": False})
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
        visible = _visible_text(raw)
        speaker.say(visible[emitted:])
        return visible.strip(), calls

    async def _run_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        self._in_tool = True
        try:
            result = await self.services.tools.execute(name, args, client=self.client, call_id=f"{name}-{time.time_ns()}")
        finally:
            self._in_tool = False
        return {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False, default=str)[:8000]}

    async def _speak_only(self, text: str) -> None:
        voice = VoiceProfile.from_settings(self.services.settings.current.voice)
        speaker = _Speaker(self, voice.name, voice.speed)
        speaker.say(text)
        try:
            await speaker.finish()
        except asyncio.CancelledError:
            speaker.cancel()
            raise
        await self.client.send_event("transcript", {"role": "jarvis", "text": text, "final": True})
        await self.client.send_event("turn.complete", {})

    async def _status(self, state: str, message: str | None = None) -> None:
        await self.client.send_event(
            "ai.status", {"state": state, "message": message, "model": self._model or self.services.settings.current.ai.model}
        )


def _visible_text(raw: str) -> str:
    """Entfernt <think>…</think>-Blöcke von Reasoning-Modellen (auch unvollständige)."""
    text = _THINK_BLOCK.sub("", raw)
    start = text.find("<think>")
    if start != -1:
        text = text[:start]  # Denkblock läuft noch – nichts davon vorlesen
    return text


__all__ = ["AIUnavailable", "LiveSession", "INPUT_SAMPLE_RATE", "OUTPUT_SAMPLE_RATE"]

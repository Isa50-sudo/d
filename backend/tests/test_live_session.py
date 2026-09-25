"""LiveSession gegen eine simulierte Gemini-Live-Verbindung.

Prüft den kompletten Ablauf: Audio rein -> Transkript -> Tool-Call -> ToolManager
-> Tool-Antwort an Gemini -> Audio + Transkript an den Browser -> Turn-Ende.
"""
from __future__ import annotations

import asyncio
import contextlib

from google.genai import types

from jarvis.ai.live_session import LiveSession


class FakeSession:
    def __init__(self) -> None:
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.tool_responses: list[types.FunctionResponse] = []
        self.stream_end = 0
        self._tool_answered = asyncio.Event()
        self._step = 0

    async def send_realtime_input(self, *, audio=None, text=None, audio_stream_end=None, **_):
        if audio is not None:
            self.sent_audio.append(audio.data)
        if text is not None:
            self.sent_text.append(text)
        if audio_stream_end:
            self.stream_end += 1

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)
        self._tool_answered.set()

    async def send_client_content(self, **_):
        pass

    async def receive(self):
        if self._step == 0:
            self._step = 1
            yield types.LiveServerMessage(
                session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle="h1", resumable=True)
            )
            yield types.LiveServerMessage(server_content=types.LiveServerContent(input_transcription=types.Transcription(text="Wie spät ")))
            yield types.LiveServerMessage(server_content=types.LiveServerContent(input_transcription=types.Transcription(text="ist es?")))
            yield types.LiveServerMessage(
                tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id="c1", name="get_current_time", args={})])
            )
            await self._tool_answered.wait()
            yield types.LiveServerMessage(
                server_content=types.LiveServerContent(
                    model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00" * 480, mime_type="audio/pcm;rate=24000"))]),
                    output_transcription=types.Transcription(text="Es ist 10 Uhr."),
                    grounding_metadata=types.GroundingMetadata(
                        web_search_queries=["uhrzeit"],
                        grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://example.org", title="Example", domain="example.org"))],
                    ),
                )
            )
            yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
            return
        await asyncio.Event().wait()  # weitere Turns: warten wie eine offene Verbindung


class FakeLive:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.config: types.LiveConnectConfig | None = None
        self.model: str | None = None

    @contextlib.asynccontextmanager
    async def connect(self, *, model, config):
        self.model, self.config = model, config
        yield self.session


class FakeGenaiClient:
    def __init__(self) -> None:
        self.live = FakeLive()
        self.aio = self


async def test_full_voice_turn_with_tool_call(services, fake_client):
    fake = FakeGenaiClient()
    services.gemini._client = fake  # noqa: SLF001 - Test-Injektion
    services.hub.add(fake_client)  # type: ignore[arg-type]

    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.ensure_started(timeout=5)
    await session.push_audio(b"\x00\x00" * 640)

    for _ in range(200):
        if fake_client.of("turn.complete"):
            break
        await asyncio.sleep(0.02)

    # Konfiguration: Stimme, Transkription, Tools, Google-Suche, Resumption
    cfg = fake.live.config
    assert fake.live.model == services.settings.current.ai.live_model
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"
    assert cfg.input_audio_transcription is not None and cfg.output_audio_transcription is not None
    names = {d.name for t in cfg.tools if t.function_declarations for d in t.function_declarations}
    assert {"open_url", "get_weather", "show_location_on_globe"} <= names
    assert any(t.google_search is not None for t in cfg.tools)
    assert "JARVIS" in cfg.system_instruction.parts[0].text

    # Audio wurde an Gemini weitergeleitet
    assert fake.live.session.sent_audio == [b"\x00\x00" * 640]

    # Tool wurde über den ToolManager ausgeführt und beantwortet
    response = fake.live.session.tool_responses[0]
    assert response.id == "c1" and response.name == "get_current_time"
    assert response.response["status"] == "ok"
    assert response.scheduling == types.FunctionResponseScheduling.WHEN_IDLE

    # Browser hat Transkripte, Audio, Tool-Events, Quellen und Turn-Ende erhalten
    user_final = [p for p in fake_client.of("transcript") if p["role"] == "user" and p["final"]]
    assert user_final and user_final[0]["text"] == "Wie spät ist es?"
    jarvis_final = [p for p in fake_client.of("transcript") if p["role"] == "jarvis" and p["final"]]
    assert jarvis_final and jarvis_final[0]["text"] == "Es ist 10 Uhr."
    assert fake_client.audio and fake_client.of("model.speaking")
    assert fake_client.of("tool.start") and fake_client.of("tool.end")[0]["ok"] is True
    assert fake_client.of("sources")[0]["sources"][0]["uri"] == "https://example.org"
    assert [p["state"] for p in fake_client.of("gemini.status")][:2] == ["connecting", "connected"]

    # Kurzzeit-Kontext wurde gefüllt
    turns = services.conversation.turns()
    assert [t.role for t in turns[-2:]] == ["user", "jarvis"]
    assert session._resume_handle == "h1"  # noqa: SLF001

    # Text-Fallback nutzt dieselbe Session
    await session.send_text("Hallo")
    assert fake.live.session.sent_text == ["Hallo"]

    await session.close()
    assert not session.active


async def test_missing_key_gives_friendly_error(services, fake_client):
    services.gemini._env = services.gemini._env.model_copy(update={"gemini_api_key": None})  # noqa: SLF001
    from jarvis.ai.live_session import GeminiUnavailable

    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    try:
        await session.ensure_started(timeout=2)
        raise AssertionError("sollte fehlschlagen")
    except GeminiUnavailable as exc:
        assert "GEMINI_API_KEY" in exc.user_message


async def test_connection_failure_reports_user_message(services, fake_client):
    class Broken:
        def __init__(self):
            self.aio = self
            self.live = self

        def connect(self, **_):
            raise ConnectionError("network down")

    services.gemini._client = Broken()  # noqa: SLF001
    from jarvis.ai.live_session import GeminiUnavailable

    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    try:
        await session.ensure_started(timeout=10)
        raise AssertionError("sollte fehlschlagen")
    except GeminiUnavailable as exc:
        assert exc.user_message == "Die Verbindung zu Gemini konnte nicht hergestellt werden."
    assert fake_client.of("error")

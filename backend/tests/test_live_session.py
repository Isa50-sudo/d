"""Sprachsitzung (Whisper -> Ollama -> Piper) gegen simulierte Komponenten.

Prüft den kompletten Ablauf: Audio rein -> VAD -> Transkript -> Ollama-Stream
mit Tool-Call -> ToolManager -> zweite Modellrunde -> satzweise Sprachausgabe
-> Transkript + Audio an den Browser -> Turn-Ende. Außerdem Barge-in,
Text-Fallback, Fehlermeldungen und Sprachwahl.
"""
from __future__ import annotations

import asyncio

import numpy as np

from jarvis.ai.live_session import AIUnavailable, LiveSession, _visible_text
from jarvis.ai.ollama import OllamaError
from jarvis.voice.stt import Transcript


def tone(ms: int, amp: float = 0.3, rate: int = 16000) -> bytes:
    t = np.arange(int(rate * ms / 1000)) / rate
    return (np.sin(2 * np.pi * 220 * t) * amp * 32767).astype("<i2").tobytes()


def silence(ms: int, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(rate * ms / 1000)


class FakeAI:
    """Simuliert Ollama /api/chat (Streaming + Tool-Calling)."""

    def __init__(self, rounds: list[list[dict]], ok: bool = True, delay: float = 0.0) -> None:
        self.rounds = rounds
        self.requests: list[dict] = []
        self.ok = ok
        self.delay = delay
        self.state = "ok"
        self.last_error = None
        self.base_url = "http://fake"

    async def check(self, model):
        return (True, "Ollama test") if self.ok else (False, "Ollama ist nicht erreichbar. Bitte starte Ollama.")

    async def chat_stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        chunks = self.rounds.pop(0) if self.rounds else [{"message": {"content": "Okay."}, "done": True}]
        for chunk in chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def close(self):
        pass


class FakeSTT:
    def __init__(self, text: str = "Wie spät ist es?", language: str = "de") -> None:
        self.text, self.language = text, language
        self.calls = 0
        self.loaded = True

    async def load(self, model_name, device):
        pass

    async def transcribe(self, pcm, *, language, model_name, device):
        self.calls += 1
        return Transcript(self.text, self.language)


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def preload_async(self, name):
        pass

    async def synthesize(self, text, *, voice, speed):
        self.spoken.append((text, voice))
        return b"\x01\x00" * 2205  # 0,1 s bei 22,05 kHz


def install(services, ai, stt=None, tts=None):
    services.ai = ai
    services.stt = stt or FakeSTT()
    services.tts = tts or FakeTTS()
    return services


async def wait_for(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_full_voice_turn_with_tool_call(services, fake_client):
    ai = FakeAI(
        [
            [{"message": {"content": "", "tool_calls": [{"function": {"name": "get_current_time", "arguments": {}}}]}, "done": True}],
            [
                {"message": {"content": "Es ist gerade "}},
                {"message": {"content": "zehn Uhr. Kann ich "}},
                {"message": {"content": "sonst helfen?"}},
                {"message": {"content": ""}, "done": True},
            ],
        ]
    )
    install(services, ai)
    services.hub.add(fake_client)  # type: ignore[arg-type]
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.ensure_started(timeout=5)

    # Sprechen: 0,5 s Ton, dann 1 s Stille -> VAD beendet die Äußerung
    for i in range(0, 8000 * 2, 1280):
        await session.push_audio(tone(500)[i : i + 1280])
    await session.push_audio(silence(1000))

    assert await wait_for(lambda: fake_client.of("turn.complete"))

    # VAD-Ereignisse und Transkript des Benutzers
    assert [p["activity"] for p in fake_client.of("voice.activity")][:2] == ["start", "end"]
    user = [p for p in fake_client.of("transcript") if p["role"] == "user"]
    assert user and user[0]["text"] == "Wie spät ist es?" and user[0]["final"]

    # Ollama bekam System-Prompt, Verlauf und Tools im Ollama-Format
    first = ai.requests[0]
    assert first["messages"][0]["role"] == "system" and "JARVIS" in first["messages"][0]["content"]
    assert first["messages"][-1] == {"role": "user", "content": "Wie spät ist es?"}
    names = {t["function"]["name"] for t in first["tools"]}
    assert {"open_url", "web_search", "get_weather", "show_location_on_globe"} <= names
    for t in first["tools"]:
        assert "anyOf" not in str(t["function"]["parameters"])

    # Tool wurde ausgeführt und das Ergebnis in Runde 2 an Ollama gegeben
    second = ai.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_name"] == "get_current_time"
    assert '"status": "ok"' in second[-1]["content"]
    assert fake_client.of("tool.start") and fake_client.of("tool.end")[0]["ok"] is True

    # Satzweise Sprachausgabe mit deutscher Stimme, Audio + Transkript an den Browser
    spoken = [t for t, _ in services.tts.spoken]
    assert spoken == ["Es ist gerade zehn Uhr.", "Kann ich sonst helfen?"]
    assert {v for _, v in services.tts.spoken} == {"de_DE-thorsten-high"}
    assert fake_client.of("model.speaking") and fake_client.audio
    final = [p for p in fake_client.of("transcript") if p["role"] == "jarvis" and p["final"]]
    assert final[-1]["text"] == "Es ist gerade zehn Uhr. Kann ich sonst helfen?"
    assert [t.role for t in services.conversation.turns()[-2:]] == ["user", "jarvis"]
    assert [p["state"] for p in fake_client.of("ai.status")][:2] == ["connecting", "connected"]

    await session.close()
    assert not session.active


async def test_english_speech_uses_english_voice(services, fake_client):
    ai = FakeAI([[{"message": {"content": "It is ten o'clock."}, "done": True}]])
    install(services, ai, stt=FakeSTT("What time is it?", "en"))
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.ensure_started(timeout=5)
    await session.push_audio(tone(500))
    await session.push_audio(silence(1000))
    assert await wait_for(lambda: fake_client.of("turn.complete"))
    assert services.tts.spoken == [("It is ten o'clock.", "en_GB-alan-medium")]
    notes = [m["content"] for m in ai.requests[0]["messages"] if m["role"] == "system"][1:]
    assert notes and "Englisch" in notes[0]
    await session.close()


async def test_text_fallback_and_think_blocks_are_not_spoken(services, fake_client):
    ai = FakeAI([[{"message": {"content": "<think>Nachdenken…</think>"}}, {"message": {"content": "Hallo! Wie kann ich helfen?"}, "done": True}]])
    install(services, ai)
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.send_text("Hallo")
    assert await wait_for(lambda: fake_client.of("turn.complete"))
    assert services.stt.calls == 0
    assert [t for t, _ in services.tts.spoken] == ["Hallo!", "Wie kann ich helfen?"]
    assert all("think" not in p["text"] for p in fake_client.of("transcript"))
    await session.close()


async def test_barge_in_interrupts_speaking(services, fake_client):
    ai = FakeAI([[{"message": {"content": f"Satz Nummer {i}. "}} for i in range(40)]], delay=0.05)
    install(services, ai)
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.send_text("Erzähl mir etwas Langes")
    assert await wait_for(lambda: fake_client.of("model.speaking"))
    # Benutzer redet laut dazwischen
    await session.push_audio(tone(400, amp=0.8))
    assert await wait_for(lambda: fake_client.of("interrupted"))
    assert len(services.tts.spoken) < 40
    interrupted = [p for p in fake_client.of("transcript") if p["role"] == "jarvis" and p["final"]]
    assert interrupted and interrupted[-1]["interrupted"] is True
    await session.close()


async def test_ollama_unreachable_gives_friendly_error(services, fake_client):
    install(services, FakeAI([], ok=False))
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    try:
        await session.ensure_started(timeout=5)
        raise AssertionError("sollte fehlschlagen")
    except AIUnavailable as exc:
        assert "Ollama" in exc.user_message
    assert fake_client.of("error")
    assert fake_client.of("ai.status")[-1]["state"] == "error"


async def test_ollama_error_mid_conversation(services, fake_client):
    class Failing(FakeAI):
        async def chat_stream(self, **kwargs):
            raise OllamaError("Das Modell 'x' ist in Ollama nicht installiert.")
            yield  # pragma: no cover

    install(services, Failing([]))
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.send_text("Hallo")
    assert await wait_for(lambda: fake_client.of("turn.complete"))
    assert "nicht installiert" in fake_client.of("error")[0]["message"]
    assert services.tts.spoken == [("Ich kann mein Sprachmodell gerade nicht erreichen.", "de_DE-thorsten-high")]
    await session.close()


async def test_noise_is_ignored(services, fake_client):
    install(services, FakeAI([]))
    session = LiveSession(services, fake_client)  # type: ignore[arg-type]
    await session.ensure_started(timeout=5)
    await session.push_audio(tone(100))  # zu kurz für eine Äußerung
    await session.push_audio(silence(1000))
    await asyncio.sleep(0.3)
    assert services.stt.calls == 0
    await session.close()


def test_visible_text_strips_thinking():
    assert _visible_text("<think>abc</think>Hallo") == "Hallo"
    assert _visible_text("Hallo <think>unfertig") == "Hallo "

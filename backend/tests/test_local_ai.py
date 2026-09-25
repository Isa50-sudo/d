"""Ollama-Client, Tool-Schemas, VAD, Websuche-Parser und TTS-Hilfsfunktionen."""
from __future__ import annotations

import json

import httpx
import numpy as np

from jarvis.ai.ollama import OllamaError, OllamaProvider
from jarvis.config.env import EnvSettings
from jarvis.tools.registry import ToolRegistry, simplify_schema
from jarvis.voice.tts import clean_for_speech, resample
from jarvis.voice.vad import SpeechSegmenter, VadConfig
from jarvis.web.sources import parse_duckduckgo


def provider_with(handler) -> OllamaProvider:
    provider = OllamaProvider(EnvSettings(ollama_host="http://ollama.test"))
    provider._client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))  # noqa: SLF001
    return provider


async def test_ollama_check_and_stream():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "size": 5_000_000_000, "details": {"family": "qwen3"}}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools", "thinking"]})
        if request.url.path == "/api/chat":
            seen["payload"] = json.loads(request.content)
            lines = [
                {"message": {"role": "assistant", "content": "Hallo"}, "done": False},
                {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "open_url", "arguments": {"url": "google"}}}]}, "done": True},
            ]
            return httpx.Response(200, content="\n".join(json.dumps(l) for l in lines).encode())
        return httpx.Response(404)

    provider = provider_with(handler)
    ok, message = await provider.check("qwen3:8b")
    assert ok and "0.12.0" in message
    ok, message = await provider.check("llama3.1:8b")
    assert not ok and "ollama pull llama3.1:8b" in message

    chunks = [c async for c in provider.chat_stream(model="qwen3:8b", messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}], temperature=0.5, context_tokens=4096, disable_thinking=True)]
    assert chunks[0]["message"]["content"] == "Hallo"
    assert chunks[1]["message"]["tool_calls"][0]["function"]["name"] == "open_url"
    payload = seen["payload"]
    assert payload["think"] is False and payload["stream"] is True
    assert payload["options"] == {"temperature": 0.5, "num_ctx": 4096}
    await provider.close()


async def test_ollama_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    provider = provider_with(handler)
    ok, message = await provider.check("qwen3:8b")
    assert not ok and "Ollama ist nicht erreichbar" in message
    try:
        async for _ in provider.chat_stream(model="m", messages=[], tools=None, temperature=0.5, context_tokens=2048, disable_thinking=False):
            pass
        raise AssertionError("sollte fehlschlagen")
    except OllamaError as exc:
        assert "nicht erreichbar" in exc.user_message
    await provider.close()


def test_tool_schemas_are_simple_for_local_models():
    registry = ToolRegistry()
    registry.load_builtin()
    tools = registry.to_ollama_tools(registry.all())
    assert len(tools) == len(registry.all())
    for t in tools:
        params = t["function"]["parameters"]
        assert params["type"] == "object"
        assert "anyOf" not in json.dumps(params)
    reminder = next(t for t in tools if t["function"]["name"] == "create_reminder")["function"]["parameters"]
    assert reminder["properties"]["in_minutes"]["type"] == "number"
    assert simplify_schema({"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}) == {"type": "string"}


def _tone(ms, amp=0.3):
    t = np.arange(int(16 * ms)) / 16000
    return (np.sin(2 * np.pi * 200 * t) * amp * 32767).astype("<i2").tobytes()


def test_vad_segments_speech():
    seg = SpeechSegmenter(VadConfig.for_sensitivity("normal"))
    events = seg.feed(b"\x00\x00" * 8000) + seg.feed(_tone(600)) + seg.feed(b"\x00\x00" * 16000)
    kinds = [k for k, _ in events]
    assert kinds == ["start", "end"]
    audio = events[1][1]
    assert audio and len(audio) >= 600 * 32  # mindestens die Sprachdauer (16 kHz, 2 Byte)


def test_vad_ignores_short_clicks_and_flushes():
    seg = SpeechSegmenter()
    events = seg.feed(_tone(100)) + seg.feed(b"\x00\x00" * 16000)
    assert events and events[-1] == ("end", b"")
    seg.feed(_tone(500))
    assert seg.in_speech
    flushed = seg.flush()
    assert flushed and flushed[0][0] == "end" and flushed[0][1]


def test_duckduckgo_parser():
    html = """
    <div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa&rut=x">Beispiel <b>Titel</b></a>
    <a class="result__snippet" href="#">Ein kurzer Ausschnitt.</a></div>
    <div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Werbung</a></div>
    <div class="result"><a class="result__a" href="https://example.com/b">Zweiter</a></div>"""
    results = parse_duckduckgo(html, 5)
    assert results[0] == {"title": "Beispiel Titel", "url": "https://example.org/a", "snippet": "Ein kurzer Ausschnitt."}
    assert [r["url"] for r in results] == ["https://example.org/a", "https://example.com/b"]


def test_tts_helpers():
    assert clean_for_speech("**Wichtig:** siehe https://x.org\n- Punkt") == "Wichtig: siehe Punkt"
    out = resample(np.ones(16000, dtype=np.float32), 16000, 22050)
    assert out.size == 22050

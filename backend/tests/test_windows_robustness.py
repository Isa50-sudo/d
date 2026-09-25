"""Fehler aus echten Windows-Logs: Surrogate-Zeichen und falsch erkannte Sprache."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import httpx
import numpy as np

from jarvis.ai.ollama import OllamaProvider, clean_payload, clean_text
from jarvis.ai.prompts import _format_now
from jarvis.config.env import EnvSettings
from jarvis.voice import stt as stt_module
from jarvis.voice.stt import SpeechToText

# So liefert Python unter deutschem Windows teils den Zeitzonennamen:
BROKEN = "Mitteleurop\udce4ische Sommerzeit"


def test_clean_text_repairs_windows_surrogates():
    assert clean_text(BROKEN) == "Mitteleuropäische Sommerzeit"
    assert clean_text("normal äöü") == "normal äöü"
    cleaned = clean_payload({"messages": [{"content": BROKEN}]})
    json.dumps(cleaned).encode("utf-8")


async def test_chat_request_with_surrogates_is_sent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=json.dumps({"message": {"content": "ok"}, "done": True}).encode())

    provider = OllamaProvider(EnvSettings(ollama_host="http://ollama.test"))
    provider._client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))  # noqa: SLF001
    chunks = [c async for c in provider.chat_stream(model="m", messages=[{"role": "system", "content": BROKEN}], tools=None, temperature=0.5, context_tokens=2048, disable_thinking=False)]
    assert chunks[0]["message"]["content"] == "ok"
    assert seen["body"]["messages"][0]["content"] == "Mitteleuropäische Sommerzeit"
    await provider.close()


def test_prompt_time_has_no_os_timezone_name():
    now = dt.datetime(2026, 9, 26, 1, 40, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert _format_now(now) == "Samstag, 26.09.2026, 01:40 Uhr (UTC+02:00)"


def test_implausible_language_is_retranscribed_in_primary_language(monkeypatch):
    calls = []

    class Model:
        def transcribe(self, audio, language=None, **kwargs):
            calls.append(language)
            if language is None:
                return [SimpleNamespace(text=" Привет", no_speech_prob=0.1, avg_logprob=-0.3)], SimpleNamespace(language="ru")
            return [SimpleNamespace(text=" Öffne Spotify", no_speech_prob=0.1, avg_logprob=-0.3)], SimpleNamespace(language="de")

    stt = SpeechToText()
    monkeypatch.setattr(stt, "_create", lambda name, device: Model())
    monkeypatch.setattr(stt_module, "register_cuda_libraries", lambda: [])
    result = stt._transcribe(np.zeros(16000, dtype=np.int16).tobytes(), None, "small", "cpu", ("de", "en"))  # noqa: SLF001
    assert calls == [None, "de"]
    assert result.text == "Öffne Spotify" and result.language == "de"

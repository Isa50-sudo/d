"""Test-Setup: isolierte Daten-/Log-Ordner, kein echter API-Key, keine echte .env."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="jarvis-test-"))
os.environ["JARVIS_DATA_DIR"] = str(_TMP / "data")
os.environ["JARVIS_LOG_DIR"] = str(_TMP / "logs")
os.environ["GEMINI_API_KEY"] = "test-key-not-real-000000"
os.environ["ALLOWED_PATHS"] = str(_TMP / "sandbox")
(_TMP / "sandbox").mkdir(parents=True, exist_ok=True)
(_TMP / "logs").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture
def sandbox() -> Path:
    return _TMP / "sandbox"


@pytest.fixture
async def services():
    from jarvis.config.env import get_env
    from jarvis.services.container import Services

    get_env.cache_clear()
    svc = Services(get_env())
    yield svc
    await svc.web.close()
    svc.memory.close()


class FakeClient:
    """Ersetzt eine Browser-Verbindung und zeichnet alle Events auf."""

    def __init__(self) -> None:
        self.id = "fakeclient"
        self.closed = False
        self.events: list[tuple[str, dict]] = []
        self.audio: list[bytes] = []

    async def send_event(self, event_type: str, payload: dict | None = None) -> None:
        self.events.append((event_type, payload or {}))

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    def of(self, event_type: str) -> list[dict]:
        return [p for t, p in self.events if t == event_type]


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()

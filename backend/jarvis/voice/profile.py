"""Stimmkonfiguration und Abstraktion der Sprachausgabe.

JARVIS spricht über eine lokale Kette:
    Mikrofon -> VAD -> faster-whisper (STT) -> Ollama (LLM + Tools) -> Piper (TTS)

Austauschbarkeit: Die Sprachausgabe steckt in voice/tts.py (``TextToSpeech``).
Ein anderer Anbieter (z. B. Coqui XTTS, ElevenLabs, Azure) wird als Klasse mit
derselben Methode ``synthesize(text, voice=, speed=) -> PCM16`` eingebunden.
Der WebSocket-Vertrag zum Browser (PCM16 mono, Sample-Rate im hello-Event)
bleibt dabei identisch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jarvis.config.user_settings import VoiceSettings

# Empfohlene Piper-Stimmen (https://huggingface.co/rhasspy/piper-voices).
# "high"/"medium" = bessere Qualität. Für JARVIS: männlich, ruhig, tief.
VOICES: list[dict[str, str | bool]] = [
    {"name": "de_DE-thorsten-high", "character": "männlich, klar, ruhig", "language": "de", "recommended": True},
    {"name": "de_DE-thorsten-medium", "character": "männlich, klar (schneller)", "language": "de", "recommended": False},
    {"name": "de_DE-karlsson-low", "character": "männlich, tief", "language": "de", "recommended": False},
    {"name": "de_DE-pavoque-low", "character": "männlich, weich", "language": "de", "recommended": False},
    {"name": "en_GB-alan-medium", "character": "male, British, calm", "language": "en", "recommended": True},
    {"name": "en_GB-northern_english_male-medium", "character": "male, British, deep", "language": "en", "recommended": False},
    {"name": "en_US-ryan-high", "character": "male, American, clear", "language": "en", "recommended": False},
    {"name": "en_US-joe-medium", "character": "male, American, warm", "language": "en", "recommended": False},
]

SPEED_HINTS = {
    "slow": "Formuliere ruhig und bedächtig.",
    "calm": "Formuliere gelassen und souverän, nie hektisch.",
    "normal": "Formuliere natürlich.",
    "fast": "Formuliere knapp und effizient.",
}
# Piper length_scale: >1 = langsamer
SPEED_LENGTH_SCALE = {"slow": 1.2, "calm": 1.08, "normal": 1.0, "fast": 0.88}

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 22050


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    name_en: str
    language: str
    speed: str
    style: str
    lock_language: bool

    @classmethod
    def from_settings(cls, s: VoiceSettings) -> "VoiceProfile":
        return cls(s.name, s.name_en, s.language, s.speed, s.style, s.lock_language)

    @property
    def primary_language(self) -> str:
        return self.language.split("-")[0].lower()

    def voice_for(self, language: str | None) -> str:
        """Englische Stimme, wenn auf Englisch geantwortet wird – sonst die Hauptstimme."""
        if not self.lock_language and language == "en" and self.primary_language != "en":
            return self.name_en
        return self.name

    def style_instruction(self) -> str:
        return (
            f"Deine Art zu sprechen: {self.style}. {SPEED_HINTS.get(self.speed, SPEED_HINTS['calm'])} "
            "Klinge wie ein erfahrener, menschlicher, persönlicher Assistent – warm, beruhigend, präzise. "
            "Niemals roboterhaft, übertrieben begeistert oder theatralisch."
        )


class VoiceBackend(Protocol):
    """Vertrag für Sprach-Sitzungen (aktuell: lokale Kette Whisper -> Ollama -> Piper)."""

    async def push_audio(self, pcm16: bytes) -> None: ...
    async def end_audio(self) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def close(self) -> None: ...

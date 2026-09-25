"""Stimmkonfiguration und Abstraktion der Sprachausgabe.

Aktuell spricht JARVIS über die native Audioausgabe der Gemini Live API
(Spracherkennung, Denken und Sprechen in einem Modell = geringste Latenz,
natürlichste Stimme, Unterbrechbarkeit).

Austauschbarkeit: Ein anderer Anbieter (z. B. ElevenLabs, Azure, lokales
Piper-TTS) wird als weiterer ``VoiceBackend`` implementiert – etwa ein
Kaskaden-Backend "Live-Transkription -> Textmodell -> externes TTS". Der
WebSocket-Vertrag zum Browser (PCM16 mono, Sample-Rate im Event
``voice.format``) bleibt dabei identisch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from google.genai import types

from jarvis.config.user_settings import VoiceSettings

# Prebuilt-Voices der Gemini Live API mit Charakterbeschreibung.
# Die für JARVIS empfohlenen tiefen, ruhigen, männlich klingenden Stimmen
# sind markiert. (Quelle: Gemini API Speech/Voice-Dokumentation)
VOICES: list[dict[str, str | bool]] = [
    {"name": "Charon", "character": "informativ, tief, ruhig", "gender": "male", "recommended": True},
    {"name": "Orus", "character": "fest, sonor", "gender": "male", "recommended": True},
    {"name": "Iapetus", "character": "klar, gelassen", "gender": "male", "recommended": True},
    {"name": "Algenib", "character": "rau, tief", "gender": "male", "recommended": True},
    {"name": "Schedar", "character": "gleichmäßig, ruhig", "gender": "male", "recommended": True},
    {"name": "Umbriel", "character": "entspannt, warm", "gender": "male", "recommended": True},
    {"name": "Rasalgethi", "character": "informativ", "gender": "male", "recommended": False},
    {"name": "Sadaltager", "character": "kenntnisreich", "gender": "male", "recommended": False},
    {"name": "Alnilam", "character": "bestimmt", "gender": "male", "recommended": False},
    {"name": "Enceladus", "character": "hauchig, weich", "gender": "male", "recommended": False},
    {"name": "Fenrir", "character": "lebhaft", "gender": "male", "recommended": False},
    {"name": "Puck", "character": "fröhlich", "gender": "male", "recommended": False},
    {"name": "Achird", "character": "freundlich", "gender": "male", "recommended": False},
    {"name": "Zubenelgenubi", "character": "locker", "gender": "male", "recommended": False},
    {"name": "Kore", "character": "fest", "gender": "female", "recommended": False},
    {"name": "Aoede", "character": "luftig", "gender": "female", "recommended": False},
    {"name": "Leda", "character": "jugendlich", "gender": "female", "recommended": False},
    {"name": "Zephyr", "character": "hell", "gender": "female", "recommended": False},
    {"name": "Sulafat", "character": "warm", "gender": "female", "recommended": False},
]

SPEED_HINTS = {
    "slow": "Sprich langsam und bedächtig, mit Pausen zwischen den Gedanken.",
    "calm": "Sprich in ruhigem, gemäßigtem Tempo – gelassen und souverän, nie hektisch.",
    "normal": "Sprich in natürlichem Gesprächstempo.",
    "fast": "Sprich zügig und effizient, aber deutlich.",
}

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    language: str
    speed: str
    style: str
    lock_language: bool

    @classmethod
    def from_settings(cls, s: VoiceSettings) -> "VoiceProfile":
        return cls(s.name, s.language, s.speed, s.style, s.lock_language)

    def speech_config(self) -> types.SpeechConfig:
        kwargs: dict = {
            "voice_config": types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.name)
            )
        }
        # Ohne language_code erkennt das native Audiomodell die Sprache selbst
        # und antwortet z. B. auf Englisch, wenn Englisch gesprochen wird.
        if self.lock_language:
            kwargs["language_code"] = self.language
        return types.SpeechConfig(**kwargs)

    def style_instruction(self) -> str:
        return (
            f"Deine Stimme und Sprechweise: {self.style}. {SPEED_HINTS.get(self.speed, SPEED_HINTS['calm'])} "
            "Klinge wie ein erfahrener, menschlicher, persönlicher Assistent – warm, beruhigend, präzise. "
            "Niemals roboterhaft, übertrieben begeistert oder theatralisch."
        )


class VoiceBackend(Protocol):
    """Vertrag für Sprach-Backends (Gemini Live, später z. B. Kaskade mit externem TTS)."""

    input_sample_rate: int
    output_sample_rate: int

    async def start(self) -> None: ...
    async def push_audio(self, pcm16: bytes) -> None: ...
    async def end_audio(self) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def close(self) -> None: ...

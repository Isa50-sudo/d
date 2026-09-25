"""Sprachausgabe lokal mit Piper (neuronale TTS, ONNX).

Stimmen liegen als <name>.onnx + <name>.onnx.json in data/voices/.
Einmalig herunterladen: python scripts/download_models.py
Ausgabe: PCM16 mono mit OUTPUT_SAMPLE_RATE (wird bei Bedarf umgerechnet).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path

import numpy as np

from jarvis.core.paths import DATA_DIR
from jarvis.voice.profile import OUTPUT_SAMPLE_RATE, SPEED_LENGTH_SCALE

log = logging.getLogger(__name__)
VOICE_DIR = DATA_DIR / "voices"

_MARKDOWN = re.compile(r"[*_#`>|~]+")
_URL = re.compile(r"https?://\S+")


class TTSUnavailable(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def installed_voices() -> list[str]:
    if not VOICE_DIR.exists():
        return []
    return sorted(p.name[: -len(".onnx")] for p in VOICE_DIR.glob("*.onnx") if p.with_suffix(".onnx.json").exists())


def clean_for_speech(text: str) -> str:
    """Entfernt Markdown/URLs, die vorgelesen merkwürdig klingen würden."""
    text = _URL.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or samples.size == 0:
        return samples
    duration = samples.size / src_rate
    dst_len = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=dst_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


class TextToSpeech:
    def __init__(self) -> None:
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    def _voice(self, name: str):  # type: ignore[no-untyped-def]
        if name in self._voices:
            return self._voices[name]
        model = VOICE_DIR / f"{name}.onnx"
        if not model.exists() or not Path(f"{model}.json").exists():
            raise TTSUnavailable(
                f"Die Stimme '{name}' ist nicht installiert. Bitte ausführen: python scripts/download_models.py"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSUnavailable("Sprachausgabe nicht installiert (Paket piper-tts fehlt).") from exc
        log.info("Lade Piper-Stimme %s", name)
        voice = PiperVoice.load(str(model))
        self._voices[name] = voice
        return voice

    def preload(self, name: str) -> None:
        with self._lock:
            self._voice(name)

    def _synthesize(self, text: str, voice_name: str, speed: str) -> bytes:
        from piper import SynthesisConfig

        text = clean_for_speech(text)
        if not text:
            return b""
        with self._lock:
            voice = self._voice(voice_name)
            config = SynthesisConfig(length_scale=SPEED_LENGTH_SCALE.get(speed, 1.0), normalize_audio=True)
            chunks = list(voice.synthesize(text, syn_config=config))  # type: ignore[attr-defined]
        if not chunks:
            return b""
        rate = chunks[0].sample_rate
        audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        audio = resample(audio, rate, OUTPUT_SAMPLE_RATE)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    async def synthesize(self, text: str, *, voice: str, speed: str) -> bytes:
        return await asyncio.to_thread(self._synthesize, text, voice, speed)

    async def preload_async(self, name: str) -> None:
        await asyncio.to_thread(self.preload, name)

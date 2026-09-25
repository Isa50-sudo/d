"""Voice Activity Detection: zerlegt den Mikrofonstrom in einzelne Äußerungen.

Energiebasiert mit adaptivem Rauschpegel (keine nativen Abhängigkeiten).
Erwartet PCM16 mono 16 kHz. Liefert Ereignisse:
  ("start", None)        – Sprache beginnt (für Barge-in / LISTENING-Anzeige)
  ("end", pcm_bytes)     – Äußerung abgeschlossen (inkl. kurzem Vorlauf)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2

SENSITIVITY = {
    # silence_ms, Faktor über Rauschpegel, Mindest-RMS
    "low": (900, 4.0, 0.020),
    "normal": (700, 3.0, 0.012),
    "high": (500, 2.2, 0.008),
}


@dataclass
class VadConfig:
    silence_ms: int = 700
    factor: float = 3.0
    min_rms: float = 0.012
    start_frames: int = 4          # 80 ms Sprache bis "start"
    preroll_ms: int = 300
    min_speech_ms: int = 250       # kürzere Geräusche verwerfen
    max_utterance_s: float = 30.0

    @classmethod
    def for_sensitivity(cls, name: str) -> "VadConfig":
        silence, factor, min_rms = SENSITIVITY.get(name, SENSITIVITY["normal"])
        return cls(silence_ms=silence, factor=factor, min_rms=min_rms)


class SpeechSegmenter:
    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._buf = b""
        self._noise = 0.005
        self._preroll: deque[bytes] = deque(maxlen=max(1, self.config.preroll_ms // FRAME_MS))
        self._speech: list[bytes] = []
        self._in_speech = False
        self._voiced_run = 0
        self._silent_run = 0
        self._voiced_total = 0
        # Während JARVIS spricht: höhere Schwelle gegen Echo-Auslösung
        self.boost = 1.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._buf = b""
        self._speech = []
        self._in_speech = False
        self._voiced_run = self._silent_run = self._voiced_total = 0
        self._preroll.clear()

    def _threshold(self) -> float:
        return max(self.config.min_rms, self._noise * self.config.factor) * self.boost

    def feed(self, pcm: bytes) -> list[tuple[str, bytes | None]]:
        events: list[tuple[str, bytes | None]] = []
        self._buf += pcm
        while len(self._buf) >= FRAME_BYTES:
            frame, self._buf = self._buf[:FRAME_BYTES], self._buf[FRAME_BYTES:]
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples * samples)))
            voiced = rms > self._threshold()
            if not voiced and not self._in_speech:
                # Rauschpegel nur in Sprechpausen nachführen
                self._noise = 0.95 * self._noise + 0.05 * rms
            events.extend(self._step(frame, voiced))
        return events

    def _step(self, frame: bytes, voiced: bool) -> list[tuple[str, bytes | None]]:
        cfg = self.config
        if not self._in_speech:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= cfg.start_frames:
                self._in_speech = True
                self._speech = list(self._preroll)
                self._voiced_total = self._voiced_run
                self._silent_run = 0
                return [("start", None)]
            return []

        self._speech.append(frame)
        if voiced:
            self._voiced_total += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        too_long = len(self._speech) * FRAME_MS >= cfg.max_utterance_s * 1000
        if self._silent_run * FRAME_MS >= cfg.silence_ms or too_long:
            return self._finish()
        return []

    def _finish(self) -> list[tuple[str, bytes | None]]:
        speech = b"".join(self._speech)
        long_enough = self._voiced_total * FRAME_MS >= self.config.min_speech_ms
        self._speech = []
        self._in_speech = False
        self._voiced_run = self._silent_run = self._voiced_total = 0
        self._preroll.clear()
        return [("end", speech if long_enough else b"")]

    def flush(self) -> list[tuple[str, bytes | None]]:
        """Mikrofon pausiert (Push-to-talk losgelassen): laufende Äußerung abschließen."""
        if self._in_speech:
            return self._finish()
        return []

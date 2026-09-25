"""Spracherkennung lokal mit faster-whisper (OpenAI Whisper, CTranslate2).

Das Modell wird beim ersten Gebrauch geladen (bzw. einmalig heruntergeladen –
vorab möglich mit scripts/download_models.py). Die Sprache wird automatisch
erkannt, damit JARVIS in der Sprache des Benutzers antworten kann.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import site
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jarvis.core.paths import DATA_DIR

log = logging.getLogger(__name__)
MODEL_DIR = DATA_DIR / "models" / "whisper"

# Typische Whisper-Halluzinationen bei Stille/Rauschen
_HALLUCINATIONS = re.compile(
    r"^(untertitel.*|vielen dank( fürs zuschauen)?\.?|danke\.?|tschüss\.?|\.+|thank you\.?|thanks for watching.*|you\.?|bye\.?|amara\.org.*|copyright.*)$",
    re.IGNORECASE,
)


class STTUnavailable(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class Transcript:
    text: str
    language: str | None


_CUDA_ERROR = re.compile(r"cublas|cudnn|cudart|cuda|libcu|\.dll|\.so", re.IGNORECASE)
_dll_dirs_registered = False


def register_cuda_libraries() -> list[str]:
    """Macht pip-installierte NVIDIA-Bibliotheken (nvidia-cublas-cu12, nvidia-cudnn-cu12) auffindbar.

    Unter Windows sucht CTranslate2 cublas64_12.dll / cudnn*.dll nur im PATH.
    Die pip-Pakete legen sie unter site-packages/nvidia/*/bin ab.
    """
    global _dll_dirs_registered
    if _dll_dirs_registered:
        return []
    _dll_dirs_registered = True
    found: list[str] = []
    for base in {Path(p) for p in site.getsitepackages() + [site.getusersitepackages()]}:
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in ("bin", "lib"):
            for directory in nvidia.glob(f"*/{sub}"):
                found.append(str(directory))
    for directory in found:
        if hasattr(os, "add_dll_directory"):
            with contextlib.suppress(OSError):
                os.add_dll_directory(directory)
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
    if found:
        log.info("NVIDIA-Bibliotheken registriert: %s", ", ".join(found))
    return found


class SpeechToText:
    def __init__(self) -> None:
        self._model = None
        self._model_key: tuple[str, str] | None = None
        self._lock = threading.Lock()
        self.device_in_use: str | None = None
        # Hinweis für die Oberfläche (z. B. "GPU nicht nutzbar – läuft auf CPU")
        self.notice: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def is_ready(self, model_name: str, device: str) -> bool:
        return self._model is not None and self._model_key == (model_name, device)

    def _create(self, model_name: str, device: str):  # type: ignore[no-untyped-def]
        from faster_whisper import WhisperModel

        compute = "float16" if device == "cuda" else "int8"
        log.info("Lade Whisper-Modell %s (%s, %s)", model_name, device, compute)
        model = WhisperModel(model_name, device=device, compute_type=compute, download_root=str(MODEL_DIR))
        if device == "cuda":
            # Probelauf: fehlende CUDA-Bibliotheken fallen erst bei der ersten Berechnung auf
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="de", beam_size=1)
            list(segments)
        return model

    def _load(self, model_name: str, device: str) -> None:
        key = (model_name, device)
        if self._model is not None and self._model_key == key:
            return
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise STTUnavailable("Spracherkennung nicht installiert (Paket faster-whisper fehlt).") from exc
        MODEL_DIR.mkdir(parents=True, exist_ok=True)

        want_gpu = device == "cuda"
        if device == "auto":
            try:
                import ctranslate2

                want_gpu = ctranslate2.get_cuda_device_count() > 0
            except Exception:  # noqa: BLE001
                want_gpu = False

        self.notice = None
        self._model = None
        if want_gpu:
            register_cuda_libraries()
            try:
                self._model = self._create(model_name, "cuda")
                self.device_in_use = "cuda"
            except Exception as exc:  # noqa: BLE001
                if not _CUDA_ERROR.search(str(exc)):
                    log.exception("Whisper-Modell konnte nicht geladen werden")
                    raise self._download_error(model_name) from exc
                log.warning("GPU für Whisper nicht nutzbar (%s) – wechsle auf CPU", exc)
                self.notice = (
                    "Die Grafikkarte kann für die Spracherkennung nicht genutzt werden (CUDA-12-Bibliotheken fehlen). "
                    "JARVIS nutzt jetzt die CPU. Für GPU-Beschleunigung: install_gpu.bat ausführen."
                )
        if self._model is None:
            try:
                self._model = self._create(model_name, "cpu")
                self.device_in_use = "cpu"
            except Exception as exc:  # noqa: BLE001
                log.exception("Whisper-Modell konnte nicht geladen werden")
                raise self._download_error(model_name) from exc
        self._model_key = key

    @staticmethod
    def _download_error(model_name: str) -> STTUnavailable:
        return STTUnavailable(
            f"Das Spracherkennungsmodell '{model_name}' konnte nicht geladen werden. "
            "Beim ersten Start wird eine Internetverbindung zum Herunterladen benötigt "
            "(oder vorab: python scripts/download_models.py)."
        )

    async def load(self, model_name: str, device: str) -> None:
        await asyncio.to_thread(self._locked_load, model_name, device)

    def _locked_load(self, model_name: str, device: str) -> None:
        with self._lock:
            self._load(model_name, device)

    def _transcribe(self, pcm16: bytes, language: str | None, model_name: str, device: str) -> Transcript:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            self._load(model_name, device)
            try:
                parts, info = self._run(audio, language)
            except RuntimeError as exc:
                if self.device_in_use != "cuda" or not _CUDA_ERROR.search(str(exc)):
                    raise
                # GPU fällt erst während der Nutzung aus -> einmalig auf CPU wechseln
                log.warning("GPU-Fehler bei der Spracherkennung (%s) – wechsle auf CPU", exc)
                self._model = self._create(model_name, "cpu")
                self.device_in_use = "cpu"
                self.notice = "Die Grafikkarte ist für die Spracherkennung nicht nutzbar – JARVIS nutzt jetzt die CPU."
                parts, info = self._run(audio, language)
        text = " ".join(p.strip() for p in parts).strip()
        if _HALLUCINATIONS.match(text):
            text = ""
        return Transcript(text=text, language=getattr(info, "language", None))

    def _run(self, audio: np.ndarray, language: str | None):  # type: ignore[no-untyped-def]
        segments, info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        parts = [s.text for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.2]
        return parts, info

    async def transcribe(self, pcm16: bytes, *, language: str | None, model_name: str, device: str) -> Transcript:
        return await asyncio.to_thread(self._transcribe, pcm16, language, model_name, device)

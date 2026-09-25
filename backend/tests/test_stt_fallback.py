"""Spracherkennung: automatischer CPU-Fallback, wenn CUDA-Bibliotheken fehlen (z. B. cublas64_12.dll)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from jarvis.voice import stt as stt_module
from jarvis.voice.stt import SpeechToText

CUBLAS_ERROR = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


class FakeModel:
    def __init__(self, device: str, fail_at_runtime: bool = False) -> None:
        self.device = device
        self.fail_at_runtime = fail_at_runtime

    def transcribe(self, audio, **kwargs):
        if self.fail_at_runtime:
            raise CUBLAS_ERROR
        seg = SimpleNamespace(text=" Hallo Jarvis", no_speech_prob=0.1, avg_logprob=-0.2)
        return [seg], SimpleNamespace(language="de")


def pcm() -> bytes:
    return (np.zeros(16000, dtype=np.int16)).tobytes()


def test_missing_cuda_libraries_fall_back_to_cpu(monkeypatch):
    monkeypatch.setattr(stt_module, "register_cuda_libraries", lambda: [])
    stt = SpeechToText()

    def create(model_name, device):
        if device == "cuda":
            raise CUBLAS_ERROR  # wie beim Probelauf auf einem PC ohne CUDA 12
        return FakeModel("cpu")

    monkeypatch.setattr(stt, "_create", create)
    result = stt._transcribe(pcm(), None, "small", "cuda")  # noqa: SLF001
    assert result.text == "Hallo Jarvis"
    assert stt.device_in_use == "cpu"
    assert stt.notice and "CPU" in stt.notice


def test_cuda_failure_during_use_switches_to_cpu(monkeypatch):
    monkeypatch.setattr(stt_module, "register_cuda_libraries", lambda: [])
    stt = SpeechToText()
    monkeypatch.setattr(stt, "_create", lambda name, device: FakeModel(device, fail_at_runtime=(device == "cuda")))
    result = stt._transcribe(pcm(), None, "small", "cuda")  # noqa: SLF001
    assert result.text == "Hallo Jarvis"
    assert stt.device_in_use == "cpu"


def test_non_cuda_errors_are_reported(monkeypatch):
    stt = SpeechToText()

    def create(model_name, device):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(stt, "_create", create)
    try:
        stt._transcribe(pcm(), None, "small", "cpu")  # noqa: SLF001
        raise AssertionError("sollte fehlschlagen")
    except stt_module.STTUnavailable as exc:
        assert "download_models" in exc.user_message

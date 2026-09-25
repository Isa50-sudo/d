"""Umgebungskonfiguration (.env).

JARVIS läuft vollständig lokal: Sprachmodell über Ollama, Spracherkennung mit
faster-whisper, Sprachausgabe mit Piper. Es werden keine API-Keys benötigt.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.core.paths import ENV_FILE

AccessLevelName = Literal["READ_ONLY", "LIMITED", "FULL_ACCESS"]


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Ollama (Sprachmodell) ---
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"

    # --- Spracherkennung (faster-whisper) ---
    stt_model: str = "small"
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"

    # --- Sprachausgabe (Piper) ---
    voice_name: str = "de_DE-thorsten-high"
    voice_name_en: str = "en_GB-alan-medium"
    voice_language: str = "de-DE"
    voice_speed: Literal["slow", "calm", "normal", "fast"] = "calm"
    voice_style: str = (
        "tief, ruhig, warm, souverän und professionell – wie ein erfahrener persönlicher Assistent"
    )

    jarvis_host: str = "127.0.0.1"
    jarvis_port: int = Field(default=8765, ge=1, le=65535)
    log_level: str = "INFO"

    access_level: AccessLevelName = "LIMITED"
    allowed_paths: str = ""

    email_address: str = ""
    email_imap_host: str = ""
    email_imap_port: int = 993
    email_smtp_host: str = ""
    email_smtp_port: int = 587

    @field_validator("voice_name", "voice_name_en", mode="before")
    @classmethod
    def _piper_voice_name(cls, value: object, info) -> object:  # type: ignore[no-untyped-def]
        # Migration: alte Gemini-Stimmnamen (z. B. "Charon") sind keine Piper-Stimmen
        if isinstance(value, str) and not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)", value.strip()):
            return "de_DE-thorsten-high" if info.field_name == "voice_name" else "en_GB-alan-medium"
        return value

    def allowed_path_list(self) -> list[Path]:
        """Liste der Wurzelordner, auf die Datei-Tools zugreifen dürfen."""
        raw = [p.strip() for p in self.allowed_paths.split(";") if p.strip()]
        if not raw:
            return [Path.home().resolve()]
        return [Path(p).expanduser().resolve() for p in raw]

    @property
    def email_configured(self) -> bool:
        return bool(self.email_address and self.email_imap_host)


@lru_cache(maxsize=1)
def get_env() -> EnvSettings:
    return EnvSettings()

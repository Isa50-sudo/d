"""Umgebungs-/Secret-Konfiguration (.env).

Secrets (GEMINI_API_KEY) werden ausschließlich hier gelesen, als SecretStr
gehalten und niemals an das Frontend, in Logs oder in Dateien geschrieben.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    gemini_api_key: SecretStr | None = None
    gemini_live_model: str = "gemini-3.8-live"

    voice_name: str = "Charon"
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

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def has_gemini_key(self) -> bool:
        return self.gemini_api_key is not None and bool(self.gemini_api_key.get_secret_value())

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

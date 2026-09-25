"""Zugriff auf das lokale Sprachmodell über Ollama (REST-API, http://127.0.0.1:11434).

Verwendet /api/chat mit Streaming und Tool-Calling. Ollama läuft als eigener
Dienst auf dem Rechner; JARVIS benötigt keinerlei API-Keys.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Literal

import httpx

from jarvis.config.env import EnvSettings
from jarvis.core.text import clean_payload, clean_text  # noqa: F401 (Re-Export)

log = logging.getLogger(__name__)

AIState = Literal["unknown", "ok", "error", "missing_model"]

MSG_UNREACHABLE = (
    "Ollama ist nicht erreichbar. Bitte starte Ollama (https://ollama.com) und versuche es erneut."
)


class OllamaError(Exception):
    def __init__(self, user_message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message


def missing_model_message(model: str) -> str:
    return f"Das Modell '{model}' ist in Ollama nicht installiert. Installiere es mit: ollama pull {model}"


class OllamaProvider:
    def __init__(self, env: EnvSettings) -> None:
        self.base_url = env.ollama_host.rstrip("/")
        # Ollama läuft lokal – niemals über einen System-Proxy leiten
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(10.0, read=300.0), trust_env=False)
        self.state: AIState = "unknown"
        self.last_error: str | None = None
        self.last_check = 0.0
        self.version: str | None = None
        self._capabilities: dict[str, list[str]] = {}

    # Kompatibel zur restlichen Anwendung: Ollama braucht keine Schlüssel
    @property
    def configured(self) -> bool:
        return True

    def mark(self, state: AIState, error: str | None = None) -> None:
        self.state = state
        self.last_error = error
        self.last_check = time.time()

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.mark("error", MSG_UNREACHABLE)
            raise OllamaError(MSG_UNREACHABLE, detail=str(exc)) from exc
        models = response.json().get("models", [])
        return [
            {
                "name": m.get("name") or m.get("model"),
                "size_gb": round((m.get("size") or 0) / 1024**3, 1),
                "family": (m.get("details") or {}).get("family"),
                "parameters": (m.get("details") or {}).get("parameter_size"),
            }
            for m in models
        ]

    async def capabilities(self, model: str) -> list[str]:
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            response = await self._client.post("/api/show", json={"model": model})
            response.raise_for_status()
            caps = list(response.json().get("capabilities") or [])
        except (httpx.HTTPError, ValueError):
            caps = []
        self._capabilities[model] = caps
        return caps

    async def check(self, model: str) -> tuple[bool, str]:
        """Prüft Erreichbarkeit, installiertes Modell und Tool-Unterstützung."""
        try:
            version = await self._client.get("/api/version")
            self.version = version.json().get("version") if version.status_code == 200 else None
            names = [m["name"] for m in await self.list_models()]
        except OllamaError as exc:
            return False, exc.user_message
        except httpx.HTTPError:
            self.mark("error", MSG_UNREACHABLE)
            return False, MSG_UNREACHABLE
        if not any(n == model or n == f"{model}:latest" for n in names):
            msg = missing_model_message(model)
            self.mark("missing_model", msg)
            return False, msg
        caps = await self.capabilities(model)
        self.mark("ok")
        if caps and "tools" not in caps:
            return True, f"Ollama {self.version or ''} · Achtung: '{model}' unterstützt kein Tool-Calling"
        return True, f"Ollama {self.version or ''} · {len(names)} Modelle installiert".replace("  ", " ")

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        context_tokens: int,
        disable_thinking: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streamt /api/chat. Liefert die einzelnen JSON-Chunks von Ollama."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": context_tokens},
        }
        if tools:
            payload["tools"] = tools
        caps = await self.capabilities(model)
        if disable_thinking and "thinking" in caps:
            payload["think"] = False

        payload = clean_payload(payload)
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code == 404:
                    raise OllamaError(missing_model_message(model))
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    if "does not support tools" in body:
                        raise OllamaError(f"Das Modell '{model}' unterstützt kein Tool-Calling. Bitte ein anderes Modell wählen (z. B. qwen3:8b).")
                    raise OllamaError("Ollama hat die Anfrage abgelehnt.", detail=body)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError("Ollama meldet einen Fehler.", detail=str(chunk["error"]))
                    yield chunk
        except httpx.ConnectError as exc:
            self.mark("error", MSG_UNREACHABLE)
            raise OllamaError(MSG_UNREACHABLE, detail=str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama hat nicht rechtzeitig geantwortet.", detail=str(exc)) from exc
        self.mark("ok")

    async def close(self) -> None:
        await self._client.aclose()

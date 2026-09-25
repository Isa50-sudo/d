"""Grundbausteine des Tool-Systems.

Ein Tool ist eine klar definierte, validierte Aktion. Das Sprachmodell kann ein Tool
nur per strukturiertem Function Call *anfordern* – ob und wie es ausgeführt
wird, entscheidet ausschließlich das Backend (ToolManager + PermissionManager).
Es gibt bewusst KEIN Tool für beliebigen Code oder beliebige Shell-Befehle.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel

if TYPE_CHECKING:
    from jarvis.core.hub import ClientConnection
    from jarvis.services.container import Services


class Risk(str, enum.Enum):
    """Risikostufe eines Tools – Grundlage des Permission-Systems."""

    READ = "read"          # liest nur (Systeminfos, Dateien lesen, Websuche)
    WRITE = "write"        # verändert etwas Harmloses/Umkehrbares (App öffnen, Datei anlegen)
    CRITICAL = "critical"  # destruktiv oder nach außen wirkend (löschen, senden, installieren)


class NoArgs(BaseModel):
    """Parametermodell für Tools ohne Parameter."""


class ToolError(Exception):
    """Fehler mit benutzerfreundlicher Meldung (wird an das Sprachmodell/UI weitergegeben)."""

    def __init__(self, user_message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message


@dataclass
class ToolContext:
    services: "Services"
    client: "ClientConnection | None" = None
    call_id: str | None = None

    async def ui(self, event_type: str, payload: dict[str, Any]) -> None:
        """Schickt ein UI-Event an den auslösenden Client (oder alle)."""
        if self.client is not None:
            await self.client.send_event(event_type, payload)
        else:
            await self.services.hub.broadcast(event_type, payload)


ToolFunc = Callable[[Any, ToolContext], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    risk: Risk
    category: str
    func: ToolFunc
    # Erzwingt IMMER eine Bestätigung, unabhängig von Zugriffsstufe/Overrides
    always_confirm: bool = False
    # In Stufe LIMITED bestätigen lassen, obwohl Risk=WRITE
    confirm_in_limited: bool = False
    timeout_s: float = 30.0
    # Menschlich lesbare Zusammenfassung für den Bestätigungsdialog
    summarize: Callable[[Any], str] | None = None
    # Verfügbarkeit abhängig von Konfiguration (z. B. E-Mail)
    available: Callable[["Services"], bool] | None = None
    tags: list[str] = field(default_factory=list)

    def json_schema(self) -> dict[str, Any] | None:
        schema = self.args_model.model_json_schema()
        if not schema.get("properties"):
            return None
        return _clean_schema(schema)

    def describe_call(self, args: BaseModel) -> str:
        if self.summarize is not None:
            try:
                return self.summarize(args)
            except Exception:  # noqa: BLE001
                pass
        return f"{self.name}({args.model_dump_json()})"


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Entfernt für das Sprachmodell irrelevante Pydantic-Felder (title) rekursiv."""
    if isinstance(schema, dict):
        return {
            k: _clean_schema(v)
            for k, v in schema.items()
            if k != "title" or not isinstance(v, str)
        }
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]  # type: ignore[return-value]
    return schema


# ---------------------------------------------------------------------------
# Deklaratives Registrieren per Decorator
# ---------------------------------------------------------------------------
_DECLARED: list[Tool] = []


def tool(
    *,
    name: str,
    description: str,
    args: type[BaseModel] = NoArgs,
    risk: Risk,
    category: str,
    always_confirm: bool = False,
    confirm_in_limited: bool = False,
    timeout_s: float = 30.0,
    summarize: Callable[[Any], str] | None = None,
    available: Callable[["Services"], bool] | None = None,
) -> Callable[[ToolFunc], ToolFunc]:
    def decorator(func: ToolFunc) -> ToolFunc:
        _DECLARED.append(
            Tool(
                name=name,
                description=description,
                args_model=args,
                risk=risk,
                category=category,
                func=func,
                always_confirm=always_confirm,
                confirm_in_limited=confirm_in_limited,
                timeout_s=timeout_s,
                summarize=summarize,
                available=available,
            )
        )
        return func

    return decorator


def declared_tools() -> list[Tool]:
    return list(_DECLARED)

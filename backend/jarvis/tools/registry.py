"""ToolRegistry – zentrale Sammlung aller verfügbaren Tools."""
from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any, Iterable

from jarvis.tools.base import Tool, declared_tools

if TYPE_CHECKING:
    from jarvis.services.container import Services

log = logging.getLogger(__name__)

BUILTIN_MODULES = (
    "jarvis.tools.builtin.system_tools",
    "jarvis.tools.builtin.file_tools",
    "jarvis.tools.builtin.app_tools",
    "jarvis.tools.builtin.browser_tools",
    "jarvis.tools.builtin.web_tools",
    "jarvis.tools.builtin.memory_tools",
    "jarvis.tools.builtin.reminder_tools",
    "jarvis.tools.builtin.world_tools",
    "jarvis.tools.builtin.email_tools",
)

# Tools, die nur relevant sind, wenn Webzugriff aktiviert ist
WEB_CATEGORIES = {"web"}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' ist bereits registriert")
        self._tools[tool.name] = tool

    def load_builtin(self) -> None:
        for module in BUILTIN_MODULES:
            importlib.import_module(module)
        for tool in declared_tools():
            if tool.name not in self._tools:
                self.register(tool)
        log.info("%d Tools registriert", len(self._tools))

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: (t.category, t.name))

    def active(self, services: "Services") -> list[Tool]:
        settings = services.settings.current
        result: list[Tool] = []
        for tool in self.all():
            if tool.category in WEB_CATEGORIES and not settings.web.enabled:
                continue
            if tool.category == "memory" and not settings.memory.enabled:
                continue
            if tool.available is not None and not tool.available(services):
                continue
            result.append(tool)
        return result

    @staticmethod
    def to_ollama_tools(tools: Iterable[Tool]) -> list[dict[str, Any]]:
        """Tool-Definitionen im Format der Ollama-Chat-API (OpenAI-kompatibel)."""
        result = []
        for tool in tools:
            schema = tool.json_schema() or {"type": "object", "properties": {}}
            result.append(
                {
                    "type": "function",
                    "function": {"name": tool.name, "description": tool.description, "parameters": simplify_schema(schema)},
                }
            )
        return result


def simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Vereinfacht Pydantic-JSON-Schemas für lokale Modelle.

    Ollama/kleinere Modelle verstehen ``anyOf: [X, null]`` (Optional) schlecht –
    daraus wird einfach ``X``. Nicht benötigte Felder (default=null) entfallen.
    """
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "anyOf" and isinstance(value, list):
            non_null = [v for v in value if not (isinstance(v, dict) and v.get("type") == "null")]
            if len(non_null) == 1:
                out.update(simplify_schema(non_null[0]))
                continue
            out[key] = [simplify_schema(v) for v in value]
        elif key == "properties" and isinstance(value, dict):
            out[key] = {name: simplify_schema(prop) for name, prop in value.items()}
        elif key == "items":
            out[key] = simplify_schema(value)
        elif key == "default" and value is None:
            continue
        else:
            out[key] = value
    return out

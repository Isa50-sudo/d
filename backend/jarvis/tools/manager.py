"""ToolManager – validiert, autorisiert, bestätigt, führt aus und protokolliert."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from jarvis.permissions.manager import Decision, PermissionManager, Policy
from jarvis.tools.base import Tool, ToolContext, ToolError
from jarvis.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from jarvis.core.hub import ClientConnection
    from jarvis.services.container import Services

log = logging.getLogger(__name__)

FRIENDLY_FAILURE = "Ich konnte diese Aktion nicht ausführen."


class ToolManager:
    def __init__(self, registry: ToolRegistry, permissions: PermissionManager, services: "Services") -> None:
        self.registry = registry
        self.permissions = permissions
        self.services = services

    def describe_tools(self) -> list[dict[str, Any]]:
        """Übersicht für die Settings-Seite (inkl. aktueller Richtlinie)."""
        active = {t.name for t in self.registry.active(self.services)}
        result = []
        for tool in self.registry.all():
            decision = self.permissions.decide(tool)
            result.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "category": tool.category,
                    "risk": tool.risk.value,
                    "always_confirm": tool.always_confirm,
                    "policy": decision.policy.value,
                    "policy_reason": decision.reason,
                    "locked": decision.locked,
                    "active": tool.name in active,
                    "override": self.services.settings.current.permissions.tool_overrides.get(tool.name),
                }
            )
        return result

    async def execute(
        self,
        name: str,
        raw_args: dict[str, Any] | None,
        *,
        client: "ClientConnection | None" = None,
        call_id: str | None = None,
        source: str = "gemini",
    ) -> dict[str, Any]:
        """Führt einen Tool-Call aus und liefert IMMER ein strukturiertes Ergebnis."""
        events = self.services.events
        settings = self.services.settings.current
        tool = self.registry.get(name)
        active_names = {t.name for t in self.registry.active(self.services)}
        if tool is None or tool.name not in active_names:
            events.add("tool", f"Unbekanntes/inaktives Tool angefordert: {name}", "warning")
            return {"status": "error", "message": f"Das Werkzeug '{name}' ist nicht verfügbar."}

        # 1) Parameter validieren
        try:
            args = tool.args_model.model_validate(raw_args or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            events.add("tool", f"Ungültige Parameter für {name}", "warning", {"errors": problems})
            return {"status": "error", "message": f"Ungültige Parameter: {problems}"}

        log_data: dict[str, Any] = {"tool": name, "source": source}
        if settings.logging.log_tool_arguments:
            log_data["args"] = args.model_dump(mode="json")

        # 2) Berechtigung prüfen
        decision: Decision = self.permissions.decide(tool)
        if decision.policy == Policy.DENY:
            events.add("permission", f"Aktion verweigert: {name} ({decision.reason})", "warning", log_data)
            await self._emit(client, "tool.denied", {"tool": name, "reason": decision.reason, "call_id": call_id})
            return {
                "status": "denied",
                "message": f"Diese Aktion ist durch die Berechtigungseinstellungen nicht erlaubt ({decision.reason}).",
            }

        # 3) Bestätigung einholen
        summary = tool.describe_call(args)
        if decision.policy == Policy.CONFIRM:
            events.add("confirmation", f"Bestätigung angefordert: {name}", "info", log_data)
            approved = await self.services.confirmations.request(
                tool=name,
                title=_confirm_title(tool),
                summary=summary,
                risk=tool.risk.value,
                details=args.model_dump(mode="json"),
                client=client,
                timeout_s=settings.permissions.confirmation_timeout_s,
            )
            if not approved:
                events.add("confirmation", f"Nicht bestätigt: {name}", "warning", log_data)
                return {
                    "status": "cancelled",
                    "message": "Der Benutzer hat die Aktion nicht bestätigt. Sie wurde NICHT ausgeführt.",
                }
            events.add("confirmation", f"Bestätigt: {name}", "success", log_data)

        # 4) Ausführen
        await self._emit(client, "tool.start", {"tool": name, "summary": summary, "call_id": call_id, "category": tool.category})
        started = time.perf_counter()
        ctx = ToolContext(services=self.services, client=client, call_id=call_id)
        try:
            result = await asyncio.wait_for(tool.func(args, ctx), timeout=tool.timeout_s)
            duration = round((time.perf_counter() - started) * 1000)
            events.add("tool", f"Tool ausgeführt: {name} ({duration} ms)", "success", {**log_data, "ms": duration})
            await self._emit(client, "tool.end", {"tool": name, "ok": True, "call_id": call_id, "ms": duration})
            return {"status": "ok", "result": result}
        except ToolError as exc:
            events.add("tool", f"Tool fehlgeschlagen: {name}: {exc}", "error", log_data)
            await self._emit(client, "tool.end", {"tool": name, "ok": False, "call_id": call_id, "message": exc.user_message})
            return {"status": "error", "message": exc.user_message}
        except asyncio.TimeoutError:
            events.add("tool", f"Zeitüberschreitung: {name}", "error", log_data)
            await self._emit(client, "tool.end", {"tool": name, "ok": False, "call_id": call_id, "message": "Zeitüberschreitung"})
            return {"status": "error", "message": f"{FRIENDLY_FAILURE} (Zeitüberschreitung)"}
        except asyncio.CancelledError:
            await self._emit(client, "tool.end", {"tool": name, "ok": False, "call_id": call_id, "message": "abgebrochen"})
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Tool %s: unerwarteter Fehler", name)
            events.add("tool", f"Unerwarteter Fehler in {name}: {type(exc).__name__}", "error", log_data)
            await self._emit(client, "tool.end", {"tool": name, "ok": False, "call_id": call_id, "message": FRIENDLY_FAILURE})
            return {"status": "error", "message": FRIENDLY_FAILURE}

    async def _emit(self, client: "ClientConnection | None", event: str, payload: dict[str, Any]) -> None:
        # Tool-Aktivität sehen alle Tabs (Activity-Feed), Bestätigungen nur der Auslöser
        await self.services.hub.broadcast(event, payload)


def _confirm_title(tool: Tool) -> str:
    titles = {
        "delete_file": "Soll ich diese Datei wirklich löschen?",
        "email_send": "Soll ich diese E-Mail wirklich versenden?",
        "install_application": "Soll ich dieses Programm installieren?",
        "close_application": "Soll ich dieses Programm wirklich beenden?",
        "edit_file": "Soll ich diese Datei wirklich ändern?",
        "create_file": "Soll ich diese Datei anlegen?",
    }
    return titles.get(tool.name, "Soll ich diese Aktion wirklich ausführen?")

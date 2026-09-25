"""Langzeit-Memory-Tools. Gespeichert wird nur auf ausdrücklichen Wunsch."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jarvis.tools.base import Risk, ToolContext, ToolError, tool


class RememberArgs(BaseModel):
    content: str = Field(min_length=2, max_length=1000, description="Was gemerkt werden soll, als kurzer, eigenständiger Satz")
    category: Literal["fact", "preference", "event", "note"] = Field(default="fact", description="fact, preference, event oder note")


@tool(
    name="remember",
    description=(
        "Speichert eine Information dauerhaft im Langzeitgedächtnis. NUR verwenden, wenn der Benutzer "
        "ausdrücklich darum bittet (z. B. 'merk dir …'). Niemals Passwörter, Gesundheits- oder Finanzdaten speichern."
    ),
    args=RememberArgs,
    risk=Risk.WRITE,
    category="memory",
    summarize=lambda a: f"Merken: {a.content}",
)
async def remember(args: RememberArgs, ctx: ToolContext) -> dict:
    settings = ctx.services.settings.current.memory
    if not settings.allow_model_save:
        raise ToolError("Das Speichern von Erinnerungen durch JARVIS ist in den Einstellungen deaktiviert.")
    if settings.confirm_saves:
        approved = await ctx.services.confirmations.request(
            tool="remember",
            title="Soll ich mir das dauerhaft merken?",
            summary=args.content,
            risk="write",
            details={"content": args.content, "category": args.category},
            client=ctx.client,
            timeout_s=ctx.services.settings.current.permissions.confirmation_timeout_s,
        )
        if not approved:
            return {"saved": False, "message": "Nicht gespeichert – der Benutzer hat nicht bestätigt."}
    item = await ctx.services.memory.add(args.content, args.category, source="jarvis")
    await ctx.services.hub.broadcast("memory.changed", {})
    ctx.services.events.add("memory", "Erinnerung gespeichert", "success", {"id": item["id"], "category": args.category})
    return {"saved": True, "id": item["id"]}


class RecallArgs(BaseModel):
    query: str | None = Field(default=None, max_length=200, description="Optionales Stichwort")


@tool(name="recall_memories", description="Durchsucht das Langzeitgedächtnis nach gespeicherten Informationen.", args=RecallArgs, risk=Risk.READ, category="memory")
async def recall_memories(args: RecallArgs, ctx: ToolContext) -> dict:
    items = await ctx.services.memory.list(args.query, limit=30)
    return {"memories": [{"id": i["id"], "content": i["content"], "category": i["category"]} for i in items]}


class ForgetArgs(BaseModel):
    memory_id: int = Field(ge=1, description="ID der Erinnerung (aus recall_memories)")


@tool(
    name="forget_memory",
    description="Löscht eine bestimmte gespeicherte Erinnerung, wenn der Benutzer darum bittet.",
    args=ForgetArgs,
    risk=Risk.WRITE,
    category="memory",
    summarize=lambda a: f"Erinnerung #{a.memory_id} löschen",
)
async def forget_memory(args: ForgetArgs, ctx: ToolContext) -> dict:
    ok = await ctx.services.memory.delete(args.memory_id)
    if not ok:
        raise ToolError("Diese Erinnerung existiert nicht.")
    await ctx.services.hub.broadcast("memory.changed", {})
    return {"deleted": args.memory_id}

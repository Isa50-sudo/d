"""Erinnerungen (werden vom ReminderService zur Fälligkeit gemeldet)."""
from __future__ import annotations

import datetime as dt
import time

from pydantic import BaseModel, Field, model_validator

from jarvis.tools.base import Risk, ToolContext, ToolError, tool


class ReminderArgs(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Woran erinnert werden soll")
    in_minutes: float | None = Field(default=None, gt=0, le=60 * 24 * 365, description="In wie vielen Minuten")
    at: str | None = Field(default=None, description="Alternativ: Zeitpunkt als ISO-8601, z. B. '2026-09-26T08:30:00'")

    @model_validator(mode="after")
    def _one_time(self) -> "ReminderArgs":
        if (self.in_minutes is None) == (self.at is None):
            raise ValueError("Genau eines von 'in_minutes' oder 'at' angeben")
        return self


@tool(
    name="create_reminder",
    description="Erstellt eine Erinnerung. JARVIS meldet sich zum angegebenen Zeitpunkt.",
    args=ReminderArgs,
    risk=Risk.WRITE,
    category="reminders",
    summarize=lambda a: f"Erinnerung: {a.text}",
)
async def create_reminder(args: ReminderArgs, ctx: ToolContext) -> dict:
    if args.in_minutes is not None:
        due = time.time() + args.in_minutes * 60
    else:
        try:
            when = dt.datetime.fromisoformat(args.at)  # type: ignore[arg-type]
        except ValueError as exc:
            raise ToolError("Den Zeitpunkt habe ich nicht verstanden.") from exc
        if when.tzinfo is None:
            when = when.astimezone()
        due = when.timestamp()
        if due < time.time() - 60:
            raise ToolError("Dieser Zeitpunkt liegt in der Vergangenheit.")
    reminder_id = await ctx.services.memory.add_reminder(args.text, due)
    await ctx.services.hub.broadcast("reminders.changed", {})
    return {"id": reminder_id, "due": dt.datetime.fromtimestamp(due).astimezone().isoformat(timespec="minutes")}


@tool(name="list_reminders", description="Listet alle offenen Erinnerungen.", risk=Risk.READ, category="reminders")
async def list_reminders(args: BaseModel, ctx: ToolContext) -> dict:
    items = await ctx.services.memory.list_reminders()
    return {
        "reminders": [
            {"id": r["id"], "text": r["text"], "due": dt.datetime.fromtimestamp(r["due_at"]).astimezone().isoformat(timespec="minutes")}
            for r in items
        ]
    }


class DeleteReminderArgs(BaseModel):
    reminder_id: int = Field(ge=1)


@tool(name="delete_reminder", description="Löscht eine Erinnerung.", args=DeleteReminderArgs, risk=Risk.WRITE, category="reminders")
async def delete_reminder(args: DeleteReminderArgs, ctx: ToolContext) -> dict:
    if not await ctx.services.memory.delete_reminder(args.reminder_id):
        raise ToolError("Diese Erinnerung existiert nicht.")
    await ctx.services.hub.broadcast("reminders.changed", {})
    return {"deleted": args.reminder_id}

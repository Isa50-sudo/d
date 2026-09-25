"""E-Mail-Tools. Nur aktiv, wenn das E-Mail-Modul eingerichtet ist."""
from __future__ import annotations

from pydantic import BaseModel, Field

from jarvis.tools.base import Risk, ToolContext, tool


def _ready(services) -> bool:  # type: ignore[no-untyped-def]
    return services.email.ready


class ListMailArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    unread_only: bool = Field(default=False, description="Nur ungelesene E-Mails")


@tool(name="email_list_recent", description="Listet die neuesten E-Mails im Posteingang (Absender, Betreff, Datum, ungelesen, vermutlich wichtig).", args=ListMailArgs, risk=Risk.READ, category="email", available=_ready, timeout_s=40)
async def email_list_recent(args: ListMailArgs, ctx: ToolContext) -> dict:
    return {"messages": await ctx.services.email.arun("list_recent", args.limit, args.unread_only)}


class SearchMailArgs(BaseModel):
    query: str = Field(min_length=1, max_length=100, description="Suchbegriff für Betreff oder Absender")
    limit: int = Field(default=15, ge=1, le=50)


@tool(name="email_search", description="Sucht E-Mails nach Betreff oder Absender.", args=SearchMailArgs, risk=Risk.READ, category="email", available=_ready, timeout_s=40)
async def email_search(args: SearchMailArgs, ctx: ToolContext) -> dict:
    return {"messages": await ctx.services.email.arun("search", args.query, args.limit)}


class ReadMailArgs(BaseModel):
    uid: int = Field(ge=1, description="UID der E-Mail (aus email_list_recent / email_search)")


@tool(name="email_read", description="Liest den Inhalt einer E-Mail (zum Zusammenfassen).", args=ReadMailArgs, risk=Risk.READ, category="email", available=_ready, timeout_s=40)
async def email_read(args: ReadMailArgs, ctx: ToolContext) -> dict:
    return await ctx.services.email.arun("read", args.uid)


class ComposeArgs(BaseModel):
    to: str = Field(min_length=3, max_length=500, description="Empfänger (mehrere mit Komma getrennt)")
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)


@tool(
    name="email_create_draft",
    description="Speichert einen E-Mail-Entwurf im Entwürfe-Ordner (wird NICHT gesendet).",
    args=ComposeArgs,
    risk=Risk.WRITE,
    category="email",
    available=_ready,
    timeout_s=40,
    summarize=lambda a: f"Entwurf an {a.to}: {a.subject}",
)
async def email_create_draft(args: ComposeArgs, ctx: ToolContext) -> dict:
    return await ctx.services.email.arun("create_draft", args.to, args.subject, args.body)


@tool(
    name="email_send",
    description="Sendet eine E-Mail. Erfordert IMMER eine ausdrückliche Bestätigung des Benutzers im Dialog.",
    args=ComposeArgs,
    risk=Risk.CRITICAL,
    category="email",
    always_confirm=True,
    available=_ready,
    timeout_s=60,
    summarize=lambda a: f"E-Mail senden an {a.to} – Betreff: {a.subject}",
)
async def email_send(args: ComposeArgs, ctx: ToolContext) -> dict:
    return await ctx.services.email.arun("send", args.to, args.subject, args.body)

"""REST-API für Seiten, Einstellungen und Statusabfragen."""
from __future__ import annotations

import asyncio
import importlib.util
import platform
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from jarvis.ai.ollama import OllamaError
from jarvis.services.container import Services
from jarvis.voice.profile import VOICES
from jarvis.voice.tts import installed_voices

router = APIRouter(prefix="/api")

RECOMMENDED_MODELS = [
    {"id": "qwen3:8b", "label": "Qwen3 8B – empfohlen (Deutsch + Tools, ~5 GB)"},
    {"id": "qwen3:14b", "label": "Qwen3 14B – klüger, braucht mehr RAM/VRAM"},
    {"id": "qwen3:4b", "label": "Qwen3 4B – für schwächere Rechner"},
    {"id": "llama3.1:8b", "label": "Llama 3.1 8B – Tools, eher Englisch"},
    {"id": "mistral-nemo", "label": "Mistral NeMo 12B – gutes Deutsch, Tools"},
]
STT_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]


def svc(request: Request) -> Services:
    return request.app.state.services


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {
        "status": "ok",
        "uptime_s": round(time.time() - s.started_at),
        "ai": {"state": s.ai.state, "message": s.ai.last_error, "model": s.settings.current.ai.model},
        "clients": s.hub.client_count,
    }


@router.get("/startup-check")
async def startup_check(request: Request) -> dict[str, Any]:
    """Echte Systemprüfungen für die Startsequenz."""
    s = svc(request)
    settings = s.settings.current
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, status: str, detail: str) -> None:
        checks.append({"key": key, "label": label, "status": status, "detail": detail})

    try:
        info = await asyncio.to_thread(s.monitor.system_info)
        add("system", "SYSTEM", "OK", f"{info['os']} · {info['cpu_cores_logical']} Threads · {info['memory_total_gb']} GB RAM")
    except Exception as exc:  # noqa: BLE001
        add("system", "SYSTEM", "FAIL", f"Systeminformationen nicht lesbar: {type(exc).__name__}")

    internet = await s.check_internet()
    add("network", "NETWORK", "OK" if internet else "WARN", "Internet erreichbar" if internet else "Kein Internet – Webfunktionen nicht verfügbar")

    ok, msg = await s.ai.check(settings.ai.model)
    add("ollama", "OLLAMA", "OK" if ok else "FAIL", f"{settings.ai.model} · {msg}" if ok else msg)

    stt_ok = importlib.util.find_spec("faster_whisper") is not None
    add("stt", "STT", "OK" if stt_ok else "FAIL",
        f"faster-whisper · Modell {settings.ai.stt_model}" + ("" if s.stt.loaded else " (wird beim Aktivieren geladen)") if stt_ok else "Paket faster-whisper fehlt – install ausführen")

    voices = installed_voices()
    voice_ok = settings.voice.name in voices
    add("voice", "VOICE", "OK" if voice_ok else "FAIL",
        f"Piper · {settings.voice.name} · {settings.voice.speed}" if voice_ok else f"Stimme {settings.voice.name} fehlt – python scripts/download_models.py ausführen")

    active = s.registry.active(s)
    add("tools", "TOOLS", "OK", f"{len(active)} Tools aktiv · Zugriffsstufe {settings.permissions.access_level}")

    mem_ok = await asyncio.to_thread(s.memory.healthy)
    count = len(await s.memory.list(limit=10000)) if mem_ok else 0
    add("memory", "MEMORY", "OK" if mem_ok else "FAIL", f"{count} Langzeit-Erinnerungen" if mem_ok else "Datenbank nicht verfügbar")

    email = s.email.status()
    add("email", "EMAIL", "OK" if email.configured and email.password_stored else "SKIP", email.message)

    s.events.add("system", "Startsequenz-Prüfung durchgeführt", "info", {c["key"]: c["status"] for c in checks})
    return {"checks": checks, "ai_ready": ok, "ai_message": msg, "voice_ready": voice_ok, "ollama_host": s.ai.base_url}


# --- Settings -----------------------------------------------------------
@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    s = svc(request)
    try:
        installed = [m["name"] for m in await s.ai.list_models()]
    except OllamaError:
        installed = []
    voices_installed = installed_voices()
    catalog = {v["name"] for v in VOICES}
    voices = [{**v, "installed": v["name"] in voices_installed} for v in VOICES] + [
        {"name": n, "character": "installiert", "language": n[:2], "recommended": False, "installed": True}
        for n in voices_installed if n not in catalog
    ]
    return {
        "settings": s.settings.current.model_dump(mode="json"),
        "voices": voices,
        "models": [{"id": n, "label": f"{n} (installiert)"} for n in installed]
        + [m for m in RECOMMENDED_MODELS if m["id"] not in installed],
        "installed_models": installed,
        "stt_models": STT_MODELS,
        "env": {
            "ollama_host": s.ai.base_url,
            "ollama_state": s.ai.state,
            "allowed_paths": [str(p) for p in s.env.allowed_path_list()],
            "email_configured": s.env.email_configured,
            "platform": platform.system(),
        },
    }


@router.put("/settings")
async def put_settings(request: Request, patch: dict[str, Any]) -> dict[str, Any]:
    s = svc(request)
    try:
        new = await s.settings.update(patch)
    except ValidationError as exc:
        raise HTTPException(422, detail=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]) from exc
    await s.hub.broadcast("settings.changed", {"settings": new.model_dump(mode="json")})
    return {"settings": new.model_dump(mode="json")}


@router.post("/settings/reset")
async def reset_settings(request: Request) -> dict[str, Any]:
    s = svc(request)
    new = await s.settings.reset()
    await s.hub.broadcast("settings.changed", {"settings": new.model_dump(mode="json")})
    return {"settings": new.model_dump(mode="json")}


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    return {"tools": svc(request).tools.describe_tools()}


# --- System -------------------------------------------------------------
@router.get("/system/info")
async def system_info(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(svc(request).monitor.system_info)


@router.get("/system/snapshot")
async def system_snapshot(request: Request) -> dict[str, Any]:
    s = svc(request)
    snap = await s.monitor.snapshot_async()
    snap["processes"] = await asyncio.to_thread(s.monitor.processes, 15)
    return snap


# --- Memory -------------------------------------------------------------
class MemoryIn(BaseModel):
    content: str = Field(min_length=2, max_length=1000)
    category: str = "fact"


@router.get("/memories")
async def list_memories(request: Request, q: str | None = None) -> dict[str, Any]:
    s = svc(request)
    return {
        "memories": await s.memory.list(q),
        "short_term": [{"role": t.role, "text": t.text, "ts": t.ts} for t in s.conversation.turns()],
    }


@router.post("/memories")
async def add_memory(request: Request, body: MemoryIn) -> dict[str, Any]:
    s = svc(request)
    item = await s.memory.add(body.content, body.category, source="user")
    s.events.add("memory", "Erinnerung manuell hinzugefügt", "success")
    await s.hub.broadcast("memory.changed", {})
    return item


@router.delete("/memories/{memory_id}")
async def delete_memory(request: Request, memory_id: int) -> dict[str, Any]:
    s = svc(request)
    if not await s.memory.delete(memory_id):
        raise HTTPException(404, "Nicht gefunden")
    s.events.add("memory", "Erinnerung gelöscht", "info")
    await s.hub.broadcast("memory.changed", {})
    return {"deleted": memory_id}


@router.delete("/memories")
async def clear_memories(request: Request) -> dict[str, Any]:
    s = svc(request)
    count = await s.memory.clear()
    s.events.add("memory", f"Alle Erinnerungen gelöscht ({count})", "warning")
    await s.hub.broadcast("memory.changed", {})
    return {"deleted": count}


@router.delete("/conversation")
async def clear_conversation(request: Request) -> dict[str, Any]:
    s = svc(request)
    s.conversation.clear()
    await s.hub.broadcast("memory.changed", {})
    return {"cleared": True}


@router.get("/reminders")
async def reminders(request: Request) -> dict[str, Any]:
    return {"reminders": await svc(request).memory.list_reminders()}


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(request: Request, reminder_id: int) -> dict[str, Any]:
    s = svc(request)
    await s.memory.delete_reminder(reminder_id)
    await s.hub.broadcast("reminders.changed", {})
    return {"deleted": reminder_id}


# --- Logs ---------------------------------------------------------------
@router.get("/logs")
async def logs(request: Request, limit: int = 300, category: str | None = None) -> dict[str, Any]:
    return {"entries": svc(request).events.recent(min(limit, 1000), category)}


@router.delete("/logs")
async def clear_logs(request: Request) -> dict[str, Any]:
    svc(request).events.clear()
    return {"cleared": True}


# --- News-Feed (Seitenleiste) -------------------------------------------
@router.get("/news")
async def news(request: Request, limit: int = 8) -> dict[str, Any]:
    s = svc(request)
    if not s.settings.current.web.enabled:
        return {"items": [], "disabled": True}
    from jarvis.tools.base import ToolError
    from jarvis.tools.builtin.web_tools import collect_news

    try:
        return await collect_news(s, None, None, min(limit, 30))
    except ToolError as exc:
        return {"items": [], "error": exc.user_message}


# --- Geocoding (WORLD-Suche) ---------------------------------------------
@router.get("/geocode")
async def geocode(request: Request, q: str) -> dict[str, Any]:
    from jarvis.tools.base import ToolError
    from jarvis.web.sources import geocode as do_geocode

    s = svc(request)
    if not s.settings.current.web.enabled:
        raise HTTPException(400, "Webzugriff ist deaktiviert.")
    try:
        return await do_geocode(s.web, q[:150])
    except ToolError as exc:
        raise HTTPException(404, exc.user_message) from exc


# --- E-Mail -------------------------------------------------------------
@router.get("/email/status")
async def email_status(request: Request) -> dict[str, Any]:
    st = svc(request).email.status(fresh=True)
    return {"configured": st.configured, "password_stored": st.password_stored, "address": st.address, "message": st.message}


async def _email_tool(request: Request, name: str, args: dict[str, Any], client_id: str | None) -> dict[str, Any]:
    s = svc(request)
    result = await s.tools.execute(name, args, client=s.hub.get(client_id), source="ui")
    if result["status"] != "ok":
        raise HTTPException(400, result.get("message", "Fehler"))
    return result["result"]


@router.get("/email/messages")
async def email_messages(request: Request, limit: int = 20, unread_only: bool = False, q: str | None = None) -> dict[str, Any]:
    if q:
        return await _email_tool(request, "email_search", {"query": q, "limit": min(limit, 50)}, None)
    return await _email_tool(request, "email_list_recent", {"limit": min(limit, 50), "unread_only": unread_only}, None)


@router.get("/email/messages/{uid}")
async def email_message(request: Request, uid: int) -> dict[str, Any]:
    return await _email_tool(request, "email_read", {"uid": uid}, None)


class ComposeIn(BaseModel):
    to: str
    subject: str
    body: str


@router.post("/email/draft")
async def email_draft(request: Request, body: ComposeIn, x_jarvis_client: str | None = Header(default=None)) -> dict[str, Any]:
    return await _email_tool(request, "email_create_draft", body.model_dump(), x_jarvis_client)


@router.post("/email/send")
async def email_send(request: Request, body: ComposeIn, x_jarvis_client: str | None = Header(default=None)) -> dict[str, Any]:
    # Läuft durch ToolManager -> Bestätigungsdialog ist Pflicht
    return await _email_tool(request, "email_send", body.model_dump(), x_jarvis_client)

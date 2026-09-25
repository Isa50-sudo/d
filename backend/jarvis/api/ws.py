"""WebSocket-Endpunkt /ws – Echtzeitkanal zwischen Browser und Backend.

Client -> Server
  binär                     PCM16 mono 16 kHz Mikrofon-Audio
  {"type":"voice.start"}    Sprachsitzung aktivieren (Gemini Live verbinden)
  {"type":"voice.pause"}    Mikrofon-Stream pausiert (audio_stream_end)
  {"type":"voice.stop"}     Sprachsitzung beenden
  {"type":"text","text":…}  Textnachricht (Fallback)
  {"type":"confirm.response","id":…, "approved":bool}
  {"type":"conversation.clear"}
  {"type":"ping"}

Server -> Client
  binär                     PCM16 mono 24 kHz JARVIS-Stimme
  hello, gemini.status, transcript, model.speaking, voice.activity, interrupted,
  turn.complete, interaction.status, tool.start/end/denied, confirm.request/closed,
  sources, search, system.stats, notification, log, ui.navigate, world.focus,
  world.clear, memory.changed, reminders.changed, email.new, error, pong
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jarvis.ai.live_session import GeminiUnavailable, LiveSession
from jarvis.core.hub import ClientConnection
from jarvis.services.container import Services
from jarvis.voice.profile import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

log = logging.getLogger(__name__)
router = APIRouter()

MAX_TEXT_LEN = 4000
MAX_AUDIO_FRAME = 64 * 1024


_background: set[asyncio.Task[None]] = set()


def _spawn(coro) -> None:  # type: ignore[no-untyped-def]
    """Längere Vorgänge nicht im Empfangs-Loop abwarten (sonst stockt z. B. die Bestätigung)."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _start_voice(session: LiveSession, client: ClientConnection) -> None:
    try:
        await session.ensure_started()
        await client.send_event("voice.ready", {})
    except GeminiUnavailable as exc:
        await client.send_event("error", {"message": exc.user_message, "source": "gemini"})


async def _send_text(session: LiveSession, client: ClientConnection, content: str) -> None:
    try:
        await session.send_text(content)
    except GeminiUnavailable as exc:
        await client.send_event("error", {"message": exc.user_message, "source": "gemini"})
    except Exception:  # noqa: BLE001
        log.exception("Text konnte nicht gesendet werden")
        await client.send_event("error", {"message": "Die Nachricht konnte nicht an Gemini gesendet werden.", "source": "gemini"})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    services: Services = websocket.app.state.services
    await websocket.accept()
    client = ClientConnection(websocket)
    services.hub.add(client)
    session = LiveSession(services, client)
    services.live_sessions[client.id] = session
    services.events.add("system", "Browser verbunden", "info", {"client": client.id})

    await client.send_event(
        "hello",
        {
            "client_id": client.id,
            "audio": {"input_sample_rate": INPUT_SAMPLE_RATE, "output_sample_rate": OUTPUT_SAMPLE_RATE},
            "gemini": {"configured": services.gemini.configured, "state": services.gemini.state, "message": services.gemini.last_error},
            "settings": services.settings.current.model_dump(mode="json"),
            "pending_confirmations": [p.public(services.settings.current.permissions.confirmation_timeout_s) for p in services.confirmations.pending],
        },
    )

    streaming_logged = False
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if len(data) > MAX_AUDIO_FRAME or len(data) % 2:
                    continue
                if session.active:
                    await session.push_audio(data)
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            kind = payload.get("type")

            if kind == "voice.start":
                if not streaming_logged:
                    services.events.add("voice", "Mikrofon aktiviert – Sprachsitzung angefordert", "info")
                    streaming_logged = True
                try:
                    session.start_nowait()
                except GeminiUnavailable as exc:
                    await client.send_event("error", {"message": exc.user_message, "source": "gemini"})
                    continue
                _spawn(_start_voice(session, client))
            elif kind == "voice.pause":
                if session.active:
                    await session.end_audio()
            elif kind == "voice.stop":
                await session.close()
                streaming_logged = False
            elif kind == "text":
                content = str(payload.get("text", "")).strip()[:MAX_TEXT_LEN]
                if content:
                    _spawn(_send_text(session, client, content))
            elif kind == "confirm.response":
                ok = services.confirmations.respond(str(payload.get("id")), bool(payload.get("approved")), client.id)
                if not ok:
                    await client.send_event("confirm.closed", {"id": payload.get("id")})
            elif kind == "conversation.clear":
                services.conversation.clear()
                services.events.add("memory", "Kurzzeit-Kontext gelöscht", "info")
            elif kind == "ping":
                await client.send_event("pong", {})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("WebSocket-Fehler")
    finally:
        client.closed = True
        services.hub.remove(client)
        services.live_sessions.pop(client.id, None)
        await session.close()
        services.events.add("system", "Browser getrennt", "info", {"client": client.id})

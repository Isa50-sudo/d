"""System-Tools: echte Messwerte des lokalen Computers (nur lesend)."""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.tools.base import Risk, ToolContext, ToolError, tool


@tool(name="get_system_info", description="Liefert Betriebssystem, Hostname, CPU-Modell, Kernanzahl und Gesamt-RAM des Computers.", risk=Risk.READ, category="system")
async def get_system_info(args: BaseModel, ctx: ToolContext) -> dict:
    return await asyncio.to_thread(ctx.services.monitor.system_info)


@tool(name="get_cpu_usage", description="Aktuelle CPU-Auslastung gesamt und pro Kern in Prozent, Taktfrequenz und Load Average.", risk=Risk.READ, category="system")
async def get_cpu_usage(args: BaseModel, ctx: ToolContext) -> dict:
    return await asyncio.to_thread(ctx.services.monitor.cpu)


@tool(name="get_memory_usage", description="Aktuelle Arbeitsspeicher-(RAM-)Nutzung in GB und Prozent.", risk=Risk.READ, category="system")
async def get_memory_usage(args: BaseModel, ctx: ToolContext) -> dict:
    return await asyncio.to_thread(ctx.services.monitor.memory)


@tool(name="get_gpu_usage", description="GPU-Auslastung, VRAM-Nutzung und GPU-Temperatur (NVIDIA über nvidia-smi).", risk=Risk.READ, category="system")
async def get_gpu_usage(args: BaseModel, ctx: ToolContext) -> dict:
    gpus = await asyncio.to_thread(ctx.services.monitor.gpu.read)
    if gpus is None:
        return {"available": False, "message": "GPU-Werte sind auf diesem System nicht verfügbar (kein nvidia-smi gefunden)."}
    return {"available": True, "gpus": gpus}


@tool(name="get_disk_usage", description="Belegung aller Laufwerke/Partitionen in GB und Prozent.", risk=Risk.READ, category="system")
async def get_disk_usage(args: BaseModel, ctx: ToolContext) -> dict:
    return {"disks": await asyncio.to_thread(ctx.services.monitor.disks)}


@tool(name="get_network_status", description="Netzwerkstatus: aktive Schnittstellen, IP-Adressen, aktuelle Up-/Download-Rate und Internet-Erreichbarkeit.", risk=Risk.READ, category="system")
async def get_network_status(args: BaseModel, ctx: ToolContext) -> dict:
    net = await asyncio.to_thread(ctx.services.monitor.network)
    net["internet_reachable"] = await ctx.services.check_internet()
    return net


@tool(name="get_temperatures", description="Hardware-Temperaturen und Akkustand, sofern das System sie bereitstellt.", risk=Risk.READ, category="system")
async def get_temperatures(args: BaseModel, ctx: ToolContext) -> dict:
    temps = await asyncio.to_thread(ctx.services.monitor.temperatures)
    battery = await asyncio.to_thread(ctx.services.monitor.battery)
    return {
        "temperatures": temps if temps else "nicht verfügbar",
        "battery": battery if battery else "nicht verfügbar",
    }


class ListProcessesArgs(BaseModel):
    sort_by: Literal["cpu", "memory"] = Field(default="cpu", description="Sortierung nach CPU oder Arbeitsspeicher")
    limit: int = Field(default=10, ge=1, le=50, description="Anzahl der Prozesse")
    name_filter: str | None = Field(default=None, max_length=100, description="Optional: nur Prozesse, deren Name diesen Text enthält")


@tool(name="list_processes", description="Listet laufende Prozesse mit CPU- und RAM-Verbrauch.", args=ListProcessesArgs, risk=Risk.READ, category="system")
async def list_processes(args: ListProcessesArgs, ctx: ToolContext) -> dict:
    procs = await asyncio.to_thread(ctx.services.monitor.processes, args.limit, args.sort_by, args.name_filter)
    return {"processes": procs}


class TimeArgs(BaseModel):
    timezone: str | None = Field(default=None, description="Optional IANA-Zeitzone, z. B. 'America/New_York'. Leer = lokale Zeit.")


@tool(name="get_current_time", description="Aktuelles Datum und Uhrzeit (lokal oder in einer bestimmten Zeitzone).", args=TimeArgs, risk=Risk.READ, category="system")
async def get_current_time(args: TimeArgs, ctx: ToolContext) -> dict:
    if args.timezone:
        try:
            from zoneinfo import ZoneInfo

            now = dt.datetime.now(ZoneInfo(args.timezone))
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Die Zeitzone '{args.timezone}' kenne ich nicht.") from exc
    else:
        now = dt.datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%A, %d.%m.%Y"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": str(now.tzinfo),
    }

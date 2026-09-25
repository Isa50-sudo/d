"""World-/UI-Tools: Globus steuern, Orte markieren, zwischen Seiten wechseln."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jarvis.tools.base import Risk, ToolContext, tool
from jarvis.web import sources


class ShowLocationArgs(BaseModel):
    place: str = Field(min_length=1, max_length=150, description="Ort, Stadt, Land oder Sehenswürdigkeit, z. B. 'Washington'")
    label: str | None = Field(default=None, max_length=120, description="Optionale Beschriftung/Info für die Markierung")
    keep_existing_markers: bool = Field(default=True, description="Bereits markierte Orte behalten")


@tool(
    name="show_location_on_globe",
    description="Öffnet die WORLD-Ansicht, fliegt mit dem 3D-Globus zu einem Ort und markiert ihn. Verwenden bei 'zeig mir …' oder wenn eine Information einen Ort betrifft.",
    args=ShowLocationArgs,
    risk=Risk.READ,
    category="world",
)
async def show_location_on_globe(args: ShowLocationArgs, ctx: ToolContext) -> dict:
    place = await sources.geocode(ctx.services.web, args.place)
    marker = {
        "name": place["name"],
        "full_name": place["full_name"],
        "lat": place["lat"],
        "lon": place["lon"],
        "label": args.label or place["full_name"],
    }
    await ctx.ui("ui.navigate", {"page": "world"})
    await ctx.ui("world.focus", {"marker": marker, "keep": args.keep_existing_markers})
    return {"shown": place["full_name"], "lat": place["lat"], "lon": place["lon"], "source": place["source"]}


@tool(name="clear_globe_markers", description="Entfernt alle Markierungen vom Globus.", risk=Risk.READ, category="world")
async def clear_globe_markers(args: BaseModel, ctx: ToolContext) -> dict:
    await ctx.ui("world.clear", {})
    return {"cleared": True}


class NavigateArgs(BaseModel):
    page: Literal["jarvis", "world", "system", "memory", "email", "logs", "settings"] = Field(description="Zielseite der Oberfläche")


@tool(name="navigate_ui", description="Wechselt die angezeigte Seite der JARVIS-Oberfläche (jarvis, world, system, memory, email, logs, settings).", args=NavigateArgs, risk=Risk.READ, category="ui")
async def navigate_ui(args: NavigateArgs, ctx: ToolContext) -> dict:
    await ctx.ui("ui.navigate", {"page": args.page})
    return {"page": args.page}

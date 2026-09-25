"""Programme öffnen, beenden und (mit Bestätigung) installieren.

Es werden niemals frei formulierte Shell-Befehle ausgeführt. Programme werden
über eine bekannte Liste (config/apps.json) oder einen streng validierten
Programmnamen gestartet; Prozesse werden als Argumentliste (ohne Shell)
aufgerufen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, Field

from jarvis.core.paths import CONFIG_DIR
from jarvis.tools.base import Risk, ToolContext, ToolError, tool

log = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[\w][\w .+\-]{0,59}$", re.UNICODE)
SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,99}$")
PLATFORM = "windows" if sys.platform.startswith("win") else "darwin" if sys.platform == "darwin" else "linux"


def load_apps() -> dict[str, dict[str, Any]]:
    apps: dict[str, dict[str, Any]] = {}
    for file in (CONFIG_DIR / "apps.json", CONFIG_DIR / "apps.local.json"):
        if file.exists():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                apps.update({k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)})
            except (OSError, json.JSONDecodeError) as exc:
                log.error("App-Konfiguration %s ungültig: %s", file, exc)
    return apps


def find_app(name: str) -> tuple[str, dict[str, Any]] | None:
    needle = name.strip().lower()
    for key, entry in load_apps().items():
        if needle == key or needle in [a.lower() for a in entry.get("aliases", [])]:
            return key, entry
    return None


def _spawn(args: list[str]) -> None:
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if PLATFORM == "windows":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)  # noqa: S603 - Argumentliste, keine Shell


def _launch(display: str, entry: dict[str, Any] | None) -> str:
    spec = (entry or {}).get(PLATFORM, {})
    if PLATFORM == "windows":
        if "uri" in spec:
            os.startfile(spec["uri"])  # type: ignore[attr-defined]  # noqa: S606
            return display
        target = spec.get("start", display)
        if not SAFE_NAME.match(target):
            raise ToolError("Ungültiger Programmname.")
        # "start" löst App-Pfade (App Paths-Registry) auf; Name ist streng validiert
        _spawn(["cmd", "/c", "start", "", target])
        return target
    if PLATFORM == "darwin":
        app = spec.get("app", display)
        result = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise ToolError(f"Das Programm '{display}' wurde nicht gefunden.", detail=result.stderr)
        return app
    cmd = [os.path.expanduser(c) for c in spec.get("cmd", [])] or [display]
    exe = shutil.which(cmd[0])
    if exe:
        _spawn([exe, *cmd[1:]])
        return cmd[0]
    if shutil.which("gtk-launch"):
        result = subprocess.run(["gtk-launch", display], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return display
    raise ToolError(f"Das Programm '{display}' wurde auf diesem Computer nicht gefunden.")


class OpenAppArgs(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="Name des Programms, z. B. 'Spotify', 'Rechner', 'VS Code'")


@tool(
    name="open_application",
    description="Öffnet ein installiertes Programm auf dem Computer (z. B. Spotify, Rechner, Editor, VS Code, Explorer).",
    args=OpenAppArgs,
    risk=Risk.WRITE,
    category="apps",
    summarize=lambda a: f"Programm öffnen: {a.name}",
)
async def open_application(args: OpenAppArgs, ctx: ToolContext) -> dict:
    found = find_app(args.name)
    if found is None and not SAFE_NAME.match(args.name.strip()):
        raise ToolError("Dieser Programmname ist ungültig.")
    key, entry = found if found else (args.name.strip(), None)
    try:
        launched = await asyncio.to_thread(_launch, args.name.strip() if not found else key, entry)
    except ToolError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"Ich konnte '{args.name}' nicht öffnen.", detail=str(exc)) from exc
    return {"opened": launched, "known_app": found is not None}


class CloseAppArgs(BaseModel):
    name: str = Field(min_length=2, max_length=60, description="Name des Programms bzw. Prozesses")


def _matching_processes(name: str) -> list[psutil.Process]:
    found = find_app(name)
    names = {n.lower() for n in (found[1].get("process_names", []) if found else [])}
    if not names:
        names = {name.lower(), f"{name.lower()}.exe"}
    critical = {"system", "explorer.exe", "winlogon.exe", "csrss.exe", "lsass.exe", "services.exe", "systemd", "launchd", "kernel_task", "loginwindow", "python", "python.exe", "python3"}
    procs = []
    own = os.getpid()
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info.get("name") or "").lower()
        if proc.info["pid"] == own or pname in critical:
            continue
        if pname in names:
            procs.append(proc)
    return procs


@tool(
    name="close_application",
    description="Beendet ein laufendes Programm (sanft, ungespeicherte Daten können verloren gehen). Erfordert Bestätigung.",
    args=CloseAppArgs,
    risk=Risk.CRITICAL,
    category="apps",
    summarize=lambda a: f"Programm beenden: {a.name}",
)
async def close_application(args: CloseAppArgs, ctx: ToolContext) -> dict:
    def _close() -> dict:
        procs = _matching_processes(args.name)
        if not procs:
            raise ToolError(f"Ich habe kein laufendes Programm namens '{args.name}' gefunden.")
        for proc in procs:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        gone, alive = psutil.wait_procs(procs, timeout=5)
        return {"closed_processes": len(gone), "still_running": len(alive)}

    return await asyncio.to_thread(_close)


class InstallArgs(BaseModel):
    package_id: str = Field(min_length=1, max_length=100, description="Paket-ID, z. B. 'Spotify.Spotify' (winget) oder 'spotify' (Homebrew cask)")


def _install(package_id: str) -> dict:
    if not SAFE_PACKAGE.match(package_id):
        raise ToolError("Ungültige Paket-ID.")
    if PLATFORM == "windows" and shutil.which("winget"):
        cmd = ["winget", "install", "--id", package_id, "-e", "--silent",
               "--accept-source-agreements", "--accept-package-agreements"]
    elif PLATFORM == "darwin" and shutil.which("brew"):
        cmd = ["brew", "install", "--cask", package_id]
    else:
        raise ToolError(
            "Automatische Installation ist auf diesem System nicht verfügbar "
            "(benötigt winget unter Windows bzw. Homebrew unter macOS)."
        )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise ToolError("Die Installation ist fehlgeschlagen.", detail=(result.stderr or result.stdout)[-500:])
    return {"installed": package_id, "manager": cmd[0]}


@tool(
    name="install_application",
    description="Installiert ein Programm über den Paketmanager (winget/Homebrew). Erfordert IMMER Bestätigung.",
    args=InstallArgs,
    risk=Risk.CRITICAL,
    category="apps",
    always_confirm=True,
    timeout_s=900,
    summarize=lambda a: f"Programm installieren: {a.package_id}",
)
async def install_application(args: InstallArgs, ctx: ToolContext) -> dict:
    return await asyncio.to_thread(_install, args.package_id)


@tool(name="list_known_applications", description="Listet die Programme, die JARVIS namentlich kennt und öffnen kann.", risk=Risk.READ, category="apps")
async def list_known_applications(args: BaseModel, ctx: ToolContext) -> dict:
    return {"applications": sorted(load_apps().keys()), "config_file": str(Path("config/apps.json"))}

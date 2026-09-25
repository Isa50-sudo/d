"""Datei-Tools – strikt auf freigegebene Ordner (ALLOWED_PATHS) beschränkt.

Sicherheitsregeln:
  * Pfade werden aufgelöst (inkl. Symlinks) und müssen innerhalb eines
    freigegebenen Wurzelordners liegen.
  * Sensible Orte (SSH-Schlüssel, Browserprofile, .env, Schlüsselbunde …)
    sind grundsätzlich gesperrt.
  * Löschen verschiebt in den Papierkorb und erfordert IMMER Bestätigung.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import fnmatch
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.config.env import get_env
from jarvis.tools.base import Risk, ToolContext, ToolError, tool

MAX_READ_BYTES = 200_000
MAX_WRITE_CHARS = 200_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".css", ".xml", ".yaml", ".yml",
    ".ini", ".cfg", ".toml", ".rst", ".bat", ".sh", ".ps1", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".sql",
}
BLOCKED_PARTS = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", "keychains", ".git-credentials",
    "appdata\\roaming\\microsoft\\credentials", ".mozilla", "google\\chrome", ".config/google-chrome",
    "library/keychains", "library/cookies",
}
BLOCKED_NAMES = {".env", "id_rsa", "id_ed25519", "known_hosts", ".netrc", ".pgpass", "credentials", "login data", "cookies"}


def allowed_roots() -> list[Path]:
    return get_env().allowed_path_list()


def resolve_safe_path(raw: str, *, must_exist: bool = False) -> Path:
    if not raw or "\x00" in raw:
        raise ToolError("Ungültiger Pfad.")
    candidate = Path(os.path.expandvars(raw)).expanduser()
    roots = allowed_roots()
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolError("Der Pfad konnte nicht aufgelöst werden.") from exc

    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ToolError("Dieser Ort liegt außerhalb der freigegebenen Ordner.")
    lowered = str(resolved).lower().replace("\\", "/")
    if any(part.replace("\\", "/") in lowered for part in BLOCKED_PARTS) or resolved.name.lower() in BLOCKED_NAMES:
        raise ToolError("Auf diesen Ort darf ich aus Sicherheitsgründen nicht zugreifen.")
    if must_exist and not resolved.exists():
        raise ToolError("Diese Datei bzw. dieser Ordner existiert nicht.")
    return resolved


def _fmt_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}"
        num /= 1024  # type: ignore[assignment]
    return f"{num:.1f} TB"


class SearchFilesArgs(BaseModel):
    pattern: str = Field(min_length=1, max_length=200, description="Dateiname oder Muster, z. B. 'bericht*' oder '*.pdf'. Teiltext genügt.")
    directory: str | None = Field(default=None, description="Startordner (optional). Standard: Benutzerordner")
    max_results: int = Field(default=20, ge=1, le=100)


def _search(args: SearchFilesArgs) -> list[dict]:
    start = resolve_safe_path(args.directory, must_exist=True) if args.directory else allowed_roots()[0]
    pattern = args.pattern if any(c in args.pattern for c in "*?[") else f"*{args.pattern}*"
    results: list[dict] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(start):
        # Versteckte Ordner und gesperrte Orte überspringen
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("node_modules", "__pycache__", "appdata")]
        for name in filenames + dirnames:
            scanned += 1
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                results.append(
                    {
                        "path": str(path),
                        "type": "dir" if path.is_dir() else "file",
                        "size": _fmt_size(stat.st_size),
                        "modified": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
                    }
                )
                if len(results) >= args.max_results:
                    return results
        if scanned > 200_000:
            break
    return results


@tool(name="search_files", description="Sucht Dateien und Ordner nach Namen in den freigegebenen Ordnern.", args=SearchFilesArgs, risk=Risk.READ, category="files", timeout_s=45)
async def search_files(args: SearchFilesArgs, ctx: ToolContext) -> dict:
    results = await asyncio.to_thread(_search, args)
    return {"count": len(results), "results": results}


class ListDirArgs(BaseModel):
    directory: str = Field(description="Ordnerpfad")


@tool(name="list_directory", description="Listet den Inhalt eines Ordners auf.", args=ListDirArgs, risk=Risk.READ, category="files")
async def list_directory(args: ListDirArgs, ctx: ToolContext) -> dict:
    path = resolve_safe_path(args.directory, must_exist=True)
    if not path.is_dir():
        raise ToolError("Das ist kein Ordner.")

    def _list() -> list[dict]:
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
            try:
                entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file", "size": _fmt_size(child.stat().st_size)})
            except OSError:
                continue
        return entries

    return {"directory": str(path), "entries": await asyncio.to_thread(_list)}


class ReadFileArgs(BaseModel):
    path: str = Field(description="Pfad der Textdatei")
    max_chars: int = Field(default=20000, ge=100, le=MAX_READ_BYTES)


@tool(name="read_file", description="Liest den Inhalt einer Textdatei.", args=ReadFileArgs, risk=Risk.READ, category="files")
async def read_file(args: ReadFileArgs, ctx: ToolContext) -> dict:
    path = resolve_safe_path(args.path, must_exist=True)
    if not path.is_file():
        raise ToolError("Das ist keine Datei.")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ToolError("Ich kann nur Textdateien lesen.")

    def _read() -> str:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(args.max_chars + 1)

    content = await asyncio.to_thread(_read)
    return {"path": str(path), "truncated": len(content) > args.max_chars, "content": content[: args.max_chars]}


class CreateFileArgs(BaseModel):
    path: str = Field(description="Pfad der neuen Datei")
    content: str = Field(default="", max_length=MAX_WRITE_CHARS, description="Textinhalt")


@tool(
    name="create_file",
    description="Erstellt eine NEUE Textdatei. Überschreibt niemals bestehende Dateien.",
    args=CreateFileArgs,
    risk=Risk.WRITE,
    category="files",
    confirm_in_limited=True,
    summarize=lambda a: f"Neue Datei anlegen: {a.path} ({len(a.content)} Zeichen)",
)
async def create_file(args: CreateFileArgs, ctx: ToolContext) -> dict:
    path = resolve_safe_path(args.path)
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ToolError("Ich darf nur Textdateien anlegen.")
    if path.exists():
        raise ToolError("Diese Datei existiert bereits – ich überschreibe keine Dateien.")

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(args.content)

    await asyncio.to_thread(_write)
    return {"created": str(path)}


class EditFileArgs(BaseModel):
    path: str = Field(description="Pfad der Textdatei")
    mode: Literal["append", "replace_text"] = Field(description="append = Text anhängen; replace_text = find durch replace ersetzen")
    text: str = Field(max_length=MAX_WRITE_CHARS, description="Anzuhängender Text bzw. Ersatztext")
    find: str | None = Field(default=None, max_length=10_000, description="Nur für replace_text: zu ersetzender Text")


@tool(
    name="edit_file",
    description="Bearbeitet eine bestehende Textdatei (Text anhängen oder Textstelle ersetzen). Vorher wird eine .bak-Sicherung erstellt.",
    args=EditFileArgs,
    risk=Risk.WRITE,
    category="files",
    confirm_in_limited=True,
    summarize=lambda a: f"Datei ändern ({a.mode}): {a.path}",
)
async def edit_file(args: EditFileArgs, ctx: ToolContext) -> dict:
    path = resolve_safe_path(args.path, must_exist=True)
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        raise ToolError("Ich kann nur bestehende Textdateien bearbeiten.")

    def _edit() -> dict:
        original = path.read_text(encoding="utf-8", errors="strict")
        if args.mode == "append":
            updated = original + ("" if original.endswith("\n") or not original else "\n") + args.text
            count = 1
        else:
            if not args.find:
                raise ToolError("Für replace_text muss 'find' angegeben werden.")
            count = original.count(args.find)
            if count == 0:
                raise ToolError("Die zu ersetzende Textstelle wurde nicht gefunden.")
            updated = original.replace(args.find, args.text)
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(original, encoding="utf-8")
        path.write_text(updated, encoding="utf-8")
        return {"path": str(path), "changes": count, "backup": str(backup)}

    try:
        return await asyncio.to_thread(_edit)
    except UnicodeDecodeError as exc:
        raise ToolError("Die Datei ist keine gültige UTF-8-Textdatei.") from exc


class DeleteFileArgs(BaseModel):
    path: str = Field(description="Pfad der Datei bzw. des Ordners")


@tool(
    name="delete_file",
    description="Verschiebt eine Datei oder einen Ordner in den Papierkorb. Erfordert IMMER eine Bestätigung durch den Benutzer.",
    args=DeleteFileArgs,
    risk=Risk.CRITICAL,
    category="files",
    always_confirm=True,
    summarize=lambda a: f"In den Papierkorb verschieben: {a.path}",
)
async def delete_file(args: DeleteFileArgs, ctx: ToolContext) -> dict:
    path = resolve_safe_path(args.path, must_exist=True)
    if path in allowed_roots():
        raise ToolError("Einen freigegebenen Wurzelordner lösche ich nicht.")
    from send2trash import send2trash

    await asyncio.to_thread(send2trash, str(path))
    return {"moved_to_trash": str(path)}

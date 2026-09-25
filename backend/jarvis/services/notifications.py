"""Proaktive Benachrichtigungen: Systemwarnungen, fällige Erinnerungen, neue wichtige E-Mails.

Jede Benachrichtigung wird im UI angezeigt und – falls aktiviert und eine
Sprachsitzung aktiv ist – von JARVIS ausgesprochen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.services.container import Services

log = logging.getLogger(__name__)

# Gleiche Warnung höchstens alle 30 Minuten
COOLDOWN_S = 30 * 60


class NotificationService:
    def __init__(self, services: "Services") -> None:
        self.services = services
        self._last_sent: dict[str, float] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._cpu_high_since: float | None = None

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._stats_loop(), name="stats-loop"),
            asyncio.create_task(self._reminder_loop(), name="reminder-loop"),
            asyncio.create_task(self._email_loop(), name="email-loop"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def notify(self, key: str, title: str, message: str, level: str = "info", *, speak: bool = True, cooldown: bool = True) -> None:
        settings = self.services.settings.current.notifications
        if not settings.enabled:
            return
        now = time.time()
        if cooldown and now - self._last_sent.get(key, 0) < COOLDOWN_S:
            return
        self._last_sent[key] = now
        self.services.events.add("notification", f"{title}: {message}", "warning" if level == "warning" else "info")
        await self.services.hub.broadcast(
            "notification", {"title": title, "message": message, "level": level, "browser": settings.browser_notifications}
        )
        if speak and settings.speak:
            for session in list(self.services.live_sessions.values()):
                await session.announce(f"{title}. {message}")

    # ------------------------------------------------------------------
    async def _stats_loop(self) -> None:
        """Sendet Live-Systemwerte an alle Clients und prüft Schwellwerte."""
        tick = 0
        while True:
            try:
                if self.services.hub.client_count:
                    snap = await self.services.monitor.snapshot_async()
                    snap["gemini"] = {"state": self.services.gemini.state, "message": self.services.gemini.last_error}
                    if tick % 3 == 0:
                        snap["processes"] = await asyncio.to_thread(self.services.monitor.processes, 12)
                    await self.services.hub.broadcast("system.stats", {"stats": snap})
                    await self._check_thresholds(snap)
                tick += 1
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Stats-Loop Fehler")
            await asyncio.sleep(2)

    async def _check_thresholds(self, snap: dict[str, Any]) -> None:
        cfg = self.services.settings.current.notifications
        for disk in snap.get("disks", []):
            if disk["percent"] >= cfg.disk_threshold_percent:
                await self.notify(
                    f"disk:{disk['mount']}",
                    "Speicherplatz wird knapp",
                    f"Laufwerk {disk['mount']} ist zu {disk['percent']:.0f} Prozent belegt, noch {disk['free_gb']:.1f} GB frei.",
                    "warning",
                )
        mem = snap.get("memory", {})
        if mem.get("percent", 0) >= cfg.memory_threshold_percent:
            await self.notify("memory", "Arbeitsspeicher fast voll", f"Der Arbeitsspeicher ist zu {mem['percent']:.0f} Prozent ausgelastet.", "warning")
        cpu = snap.get("cpu", {}).get("percent", 0)
        if cpu >= cfg.cpu_threshold_percent:
            self._cpu_high_since = self._cpu_high_since or time.time()
            if time.time() - self._cpu_high_since > 60:
                await self.notify("cpu", "Hohe CPU-Last", f"Die CPU läuft seit über einer Minute bei {cpu:.0f} Prozent.", "warning")
        else:
            self._cpu_high_since = None

    async def _reminder_loop(self) -> None:
        while True:
            try:
                for reminder in await self.services.memory.due_reminders(time.time()):
                    await self.services.memory.mark_reminder_fired(reminder["id"])
                    await self.notify(f"reminder:{reminder['id']}", "Erinnerung", reminder["text"], "reminder", cooldown=False)
                    await self.services.hub.broadcast("reminders.changed", {})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Reminder-Loop Fehler")
            await asyncio.sleep(10)

    async def _email_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            cfg = self.services.settings.current.notifications
            try:
                if cfg.enabled and cfg.email_alerts and self.services.email.ready:
                    fresh = await self.services.email.arun("new_mail_since_last_check")
                    important = [m for m in fresh if m.get("likely_important")]
                    if important:
                        first = important[0]
                        sender = first["from_name"] or first["from_address"]
                        await self.notify(
                            f"mail:{first['uid']}",
                            "Neue wichtige E-Mail",
                            f"Von {sender}: {first['subject']}" + (f" (und {len(important) - 1} weitere)" if len(important) > 1 else ""),
                            "info",
                            cooldown=False,
                        )
                    elif fresh:
                        await self.services.hub.broadcast("email.new", {"count": len(fresh)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("E-Mail-Prüfung fehlgeschlagen: %s", exc)
            await asyncio.sleep(max(60, cfg.email_check_minutes * 60))

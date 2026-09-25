"""Systemmonitoring – ausschließlich echte Messwerte (psutil / nvidia-smi).

Nicht verfügbare Werte werden als None geliefert und im UI als
"nicht verfügbar" angezeigt – es werden niemals Werte erfunden.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

import psutil

log = logging.getLogger(__name__)


def _gb(value: float) -> float:
    return round(value / (1024**3), 2)


class GpuProbe:
    """Liest NVIDIA-GPUs über nvidia-smi. Andere Hersteller: None (nicht verfügbar)."""

    def __init__(self) -> None:
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._last: tuple[float, list[dict[str, Any]] | None] = (0.0, None)

    @property
    def available(self) -> bool:
        return self._nvidia_smi is not None

    def read(self) -> list[dict[str, Any]] | None:
        if not self._nvidia_smi:
            return None
        ts, cached = self._last
        if time.time() - ts < 2.0:
            return cached
        query = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
        try:
            out = subprocess.run(
                [self._nvidia_smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            log.debug("nvidia-smi fehlgeschlagen: %s", exc)
            self._last = (time.time(), None)
            return None
        gpus = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue

            def num(v: str) -> float | None:
                try:
                    return float(v)
                except ValueError:
                    return None

            gpus.append(
                {
                    "name": parts[0],
                    "utilization_percent": num(parts[1]),
                    "vram_used_mb": num(parts[2]),
                    "vram_total_mb": num(parts[3]),
                    "temperature_c": num(parts[4]),
                    "power_w": num(parts[5]),
                }
            )
        self._last = (time.time(), gpus)
        return gpus


class SystemMonitor:
    def __init__(self) -> None:
        self.gpu = GpuProbe()
        self._last_net = psutil.net_io_counters()
        self._last_net_ts = time.time()
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._started = time.time()

    # -- static info -----------------------------------------------------
    def system_info(self) -> dict[str, Any]:
        uname = platform.uname()
        freq = psutil.cpu_freq()
        return {
            "os": f"{uname.system} {uname.release}",
            "os_version": uname.version,
            "machine": uname.machine,
            "hostname": socket.gethostname(),
            "processor": uname.processor or platform.processor() or uname.machine,
            "cpu_cores_physical": psutil.cpu_count(logical=False),
            "cpu_cores_logical": psutil.cpu_count(logical=True),
            "cpu_max_mhz": round(freq.max) if freq and freq.max else None,
            "memory_total_gb": _gb(psutil.virtual_memory().total),
            "python": platform.python_version(),
            "boot_time": dt.datetime.fromtimestamp(psutil.boot_time()).isoformat(timespec="seconds"),
            "gpu_monitoring": "nvidia-smi" if self.gpu.available else "nicht verfügbar",
        }

    # -- live values -----------------------------------------------------
    def cpu(self) -> dict[str, Any]:
        freq = psutil.cpu_freq()
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except (AttributeError, OSError):
            load = None
        return {
            "percent": psutil.cpu_percent(interval=None),
            "per_core": psutil.cpu_percent(interval=None, percpu=True),
            "freq_mhz": round(freq.current) if freq else None,
            "load_avg": load,
        }

    def memory(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "percent": vm.percent,
            "used_gb": _gb(vm.total - vm.available),
            "total_gb": _gb(vm.total),
            "available_gb": _gb(vm.available),
            "swap_percent": sw.percent,
        }

    def disks(self) -> list[dict[str, Any]]:
        result = []
        seen: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen or "loop" in part.device or part.fstype in ("squashfs", "tmpfs", "overlay"):
                continue
            seen.add(part.mountpoint)
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            result.append(
                {
                    "mount": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "percent": usage.percent,
                    "used_gb": _gb(usage.used),
                    "total_gb": _gb(usage.total),
                    "free_gb": _gb(usage.free),
                }
            )
        return result

    def network(self) -> dict[str, Any]:
        now = time.time()
        counters = psutil.net_io_counters()
        elapsed = max(now - self._last_net_ts, 1e-3)
        up = (counters.bytes_sent - self._last_net.bytes_sent) / elapsed
        down = (counters.bytes_recv - self._last_net.bytes_recv) / elapsed
        self._last_net, self._last_net_ts = counters, now
        interfaces = []
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup or name.lower().startswith(("lo", "loopback")):
                continue
            ipv4 = [a.address for a in addrs if a.family == socket.AF_INET]
            if ipv4:
                interfaces.append({"name": name, "ipv4": ipv4, "speed_mbit": st.speed or None})
        return {
            "up_kbps": round(max(up, 0) / 1024, 1),
            "down_kbps": round(max(down, 0) / 1024, 1),
            "total_sent_gb": _gb(counters.bytes_sent),
            "total_recv_gb": _gb(counters.bytes_recv),
            "interfaces": interfaces,
        }

    def temperatures(self) -> list[dict[str, Any]] | None:
        reader = getattr(psutil, "sensors_temperatures", None)
        if reader is None:
            return None
        try:
            temps = reader()
        except (OSError, RuntimeError):
            return None
        if not temps:
            return None
        result = []
        for chip, entries in temps.items():
            for entry in entries[:4]:
                if entry.current:
                    result.append({"sensor": f"{chip} {entry.label}".strip(), "celsius": round(entry.current, 1)})
        return result[:12] or None

    def battery(self) -> dict[str, Any] | None:
        reader = getattr(psutil, "sensors_battery", None)
        try:
            bat = reader() if reader else None
        except (OSError, RuntimeError):
            bat = None
        if bat is None:
            return None
        return {"percent": round(bat.percent), "plugged": bat.power_plugged}

    def processes(self, limit: int = 15, sort: str = "cpu", name_filter: str | None = None) -> list[dict[str, Any]]:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "username", "status"]):
            info = p.info
            if name_filter and name_filter.lower() not in (info.get("name") or "").lower():
                continue
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "?",
                    "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 1),
                    "status": info.get("status"),
                }
            )
        key = "memory_percent" if sort == "memory" else "cpu_percent"
        procs.sort(key=lambda x: x[key], reverse=True)
        return procs[:limit]

    def snapshot(self) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "cpu": self.cpu(),
            "memory": self.memory(),
            "disks": self.disks(),
            "network": self.network(),
            "gpu": self.gpu.read(),
            "temperatures": self.temperatures(),
            "battery": self.battery(),
            "uptime_s": round(time.time() - psutil.boot_time()),
            "backend_uptime_s": round(time.time() - self._started),
        }

    async def snapshot_async(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.snapshot)

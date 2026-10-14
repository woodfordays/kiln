"""AMD GPU telemetry from sysfs (amdgpu): busy %, VRAM, power, temperature, clock."""
import asyncio
import time
from collections import deque
from pathlib import Path

from . import config, db, events

LIVE_SECONDS = 600


def _read(p: Path) -> float | None:
    try:
        return float(p.read_text().strip())
    except (OSError, ValueError):
        return None


class GpuSampler:
    def __init__(self) -> None:
        self.device: Path | None = None
        self.hwmon: Path | None = None
        self.name = ""
        self.live: deque = deque(maxlen=int(LIVE_SECONDS / config.GPU_SAMPLE_S) + 1)
        self._minute: list[dict] = []
        self._minute_ts = 0
        self._find()

    def _find(self) -> None:
        best = None
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
            dev = card / "device"
            if "-" in card.name or not (dev / "vendor").exists():
                continue
            if (dev / "vendor").read_text().strip() != "0x1002":
                continue
            total = _read(dev / "mem_info_vram_total") or 0
            if best is None or total > best[0]:
                best = (total, dev)
        if best:
            self.device = best[1]
            hw = sorted((self.device / "hwmon").glob("hwmon*"))
            self.hwmon = hw[0] if hw else None
            self.name = (self.device / "product_name").read_text().strip() if (self.device / "product_name").exists() else ""

    @property
    def available(self) -> bool:
        return self.device is not None

    def sample(self) -> dict | None:
        if not self.device:
            return None
        d, h = self.device, self.hwmon
        power = None
        temp = None
        sclk = None
        if h:
            power = _read(h / "power1_average")
            if power is None:
                power = _read(h / "power1_input")
            temp = _read(h / "temp2_input") or _read(h / "temp1_input")  # junction, else edge
            sclk = _read(h / "freq1_input")
        return {
            "t": time.time(),
            "busy": _read(d / "gpu_busy_percent"),
            "vram_used": _read(d / "mem_info_vram_used"),
            "vram_total": _read(d / "mem_info_vram_total"),
            "power_w": power / 1e6 if power is not None else None,
            "temp_c": temp / 1000 if temp is not None else None,
            "sclk_mhz": sclk / 1e6 if sclk else None,
        }

    def mean_power(self, t0: float, t1: float) -> float | None:
        vals = [s["power_w"] for s in self.live if t0 - config.GPU_SAMPLE_S <= s["t"] <= t1 + config.GPU_SAMPLE_S and s["power_w"] is not None]
        return sum(vals) / len(vals) if vals else None

    def _flush_minute(self) -> None:
        if not self._minute:
            return

        def avg(k):
            vals = [s[k] for s in self._minute if s[k] is not None]
            return sum(vals) / len(vals) if vals else None

        db.execute(
            "INSERT OR REPLACE INTO gpu_minutes(ts, busy, vram_used, vram_total, power_w, temp_c, sclk_mhz) VALUES (?,?,?,?,?,?,?)",
            (self._minute_ts, avg("busy"), avg("vram_used"), avg("vram_total"), avg("power_w"), avg("temp_c"), avg("sclk_mhz")),
        )
        self._minute = []

    async def run(self) -> None:
        if not self.available:
            return
        while True:
            s = await asyncio.to_thread(self.sample)
            if s:
                self.live.append(s)
                minute = int(s["t"] // 60) * 60
                if minute != self._minute_ts:
                    self._flush_minute()
                    self._minute_ts = minute
                self._minute.append(s)
                events.publish("gpu", s)
            await asyncio.sleep(config.GPU_SAMPLE_S)


sampler = GpuSampler()

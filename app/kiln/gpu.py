"""GPU telemetry: busy %, VRAM, power, temperature, clock.

Two sources, picked automatically (or with KILN_GPU_TELEMETRY=amd|nvidia|none):
- amd:    amdgpu sysfs under /sys/class/drm (no tools needed)
- nvidia: nvidia-smi, which the NVIDIA container toolkit mounts into the container
Hosts with several GPUs report one combined reading: VRAM and power summed, busy averaged,
temperature the hottest card.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

from . import config, db, events

LIVE_SECONDS = 600
NVSMI_FIELDS = "name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu,clocks.sm"


def _read(p: Path) -> float | None:
    try:
        return float(p.read_text().strip())
    except (OSError, ValueError):
        return None


def _num(s: str) -> float | None:
    try:
        return float(s.strip())
    except ValueError:  # "[N/A]", "[Not Supported]"
        return None


def _combine(cards: list[dict]) -> dict:
    def vals(k):
        return [c[k] for c in cards if c.get(k) is not None]

    busy, temp, sclk = vals("busy"), vals("temp_c"), vals("sclk_mhz")
    used, total, power = vals("vram_used"), vals("vram_total"), vals("power_w")
    return {
        "t": time.time(),
        "busy": sum(busy) / len(busy) if busy else None,
        "vram_used": sum(used) if used else None,
        "vram_total": sum(total) if total else None,
        "power_w": sum(power) if power else None,
        "temp_c": max(temp) if temp else None,
        "sclk_mhz": sum(sclk) / len(sclk) if sclk else None,
        "gpus": len(cards),
    }


class GpuSampler:
    def __init__(self) -> None:
        self.source = "none"
        self.device: Path | None = None
        self.hwmon: Path | None = None
        self.nvsmi: str | None = None
        self.name = ""
        self.live: deque = deque(maxlen=int(LIVE_SECONDS / config.GPU_SAMPLE_S) + 1)
        self._minute: list[dict] = []
        self._minute_ts = 0
        want = os.environ.get("KILN_GPU_TELEMETRY", "auto").lower()
        # Follow the image's backend first: a CUDA host can also show an AMD iGPU in /sys.
        order = ["nvidia", "amd"] if os.environ.get("KILN_BACKEND") == "cuda" else ["amd", "nvidia"]
        for src in order if want == "auto" else [want]:
            if (src == "amd" and self._find_amd()) or (src == "nvidia" and self._find_nvidia()):
                self.source = src
                break

    # ------------------------------------------------------------ discovery

    def _find_amd(self) -> bool:
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
        if not best:
            return False
        self.device = best[1]
        hw = sorted((self.device / "hwmon").glob("hwmon*"))
        self.hwmon = hw[0] if hw else None
        pn = self.device / "product_name"
        self.name = pn.read_text().strip() if pn.exists() else ""
        return True

    def _find_nvidia(self) -> bool:
        self.nvsmi = shutil.which("nvidia-smi")
        if not self.nvsmi:
            return False
        cards = self._nvidia_cards()
        if not cards:
            return False
        names = sorted({c["name"] for c in cards})
        self.name = (f"{len(cards)}× " if len(cards) > 1 else "") + " + ".join(names)
        return True

    # ------------------------------------------------------------ sampling

    @property
    def available(self) -> bool:
        return self.source != "none"

    def sample(self) -> dict | None:
        if self.source == "amd":
            return self._sample_amd()
        if self.source == "nvidia":
            cards = self._nvidia_cards()
            return _combine(cards) if cards else None
        return None

    def _sample_amd(self) -> dict:
        d, h = self.device, self.hwmon
        power = temp = sclk = None
        if h:
            power = _read(h / "power1_average")
            if power is None:
                power = _read(h / "power1_input")
            temp = _read(h / "temp2_input") or _read(h / "temp1_input")  # junction, else edge
            sclk = _read(h / "freq1_input")
        return _combine([{
            "busy": _read(d / "gpu_busy_percent"),
            "vram_used": _read(d / "mem_info_vram_used"),
            "vram_total": _read(d / "mem_info_vram_total"),
            "power_w": power / 1e6 if power is not None else None,
            "temp_c": temp / 1000 if temp is not None else None,
            "sclk_mhz": sclk / 1e6 if sclk else None,
        }])

    def _nvidia_cards(self) -> list[dict]:
        try:
            out = subprocess.run([self.nvsmi, f"--query-gpu={NVSMI_FIELDS}", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        return parse_nvidia_smi(out)

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


def parse_nvidia_smi(out: str) -> list[dict]:
    cards = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        name = ",".join(parts[:-6])  # names can't contain commas today, but be safe
        busy, used, total, power, temp, sclk = (_num(p) for p in parts[-6:])
        cards.append({
            "name": name, "busy": busy,
            "vram_used": used * 2**20 if used is not None else None,
            "vram_total": total * 2**20 if total is not None else None,
            "power_w": power, "temp_c": temp, "sclk_mhz": sclk,
        })
    return cards


sampler = GpuSampler()

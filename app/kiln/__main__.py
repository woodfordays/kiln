"""Entry point: `python -m kiln` runs the control UI and the API proxy on one event loop."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn

from . import api, config, db, flags, gpu, hf, proxy, stats
from .server import get_preset, manager
from .tuner import tuner

log = logging.getLogger("kiln")


class _Server(uvicorn.Server):
    """uvicorn server that leaves signal handling to us (two servers share one loop)."""

    def install_signal_handlers(self) -> None:  # uvicorn < 0.29
        pass

    @contextlib.contextmanager
    def capture_signals(self):  # uvicorn >= 0.29
        yield


async def _retention_loop() -> None:
    while True:
        days = db.get_settings().get("retention_days") or 0
        if days:
            n = stats.purge(int(days))
            if n:
                log.info("retention: removed %d requests older than %d days", n, days)
        await asyncio.sleep(6 * 3600)


async def _autostart() -> None:
    pid = db.get_settings().get("autostart_preset_id")
    if not pid:
        return
    p = get_preset(int(pid))
    if not p:
        return
    try:
        log.info("autostart: preset %s", p["name"])
        await manager.start(p["model"], p["flags"], p["env"], preset=p)
    except Exception as e:
        log.error("autostart failed: %s", e)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init()
    meta = await asyncio.to_thread(flags.load)
    if meta["error"]:
        log.error("llama-server not usable at %s: %s", config.LLAMA_SERVER, meta["error"])
    else:
        log.info("llama-server %s, %d flags, devices: %s", meta["version"], len(meta["flags"]), meta["devices"] or "none")
    log.info("GPU telemetry: %s", gpu.sampler.name or ("available" if gpu.sampler.available else "not found"))

    ui = _Server(uvicorn.Config(api.app, host=config.UI_HOST, port=config.UI_PORT, log_level="warning",
                                lifespan="off", access_log=False))
    px = _Server(uvicorn.Config(proxy.app, host=config.API_HOST, port=config.API_PORT, log_level="warning",
                                lifespan="off", access_log=False, timeout_keep_alive=30))

    tasks = [
        asyncio.create_task(gpu.sampler.run()),
        asyncio.create_task(hf.downloader.run()),
        asyncio.create_task(_retention_loop()),
    ]

    stopping = asyncio.Event()

    def _on_signal() -> None:
        stopping.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    async def _shutdown_when_signalled() -> None:
        await stopping.wait()
        log.info("shutting down")
        await tuner.cancel()
        await manager.shutdown()
        ui.should_exit = px.should_exit = True

    log.info("Kiln UI on http://%s:%d, API on http://%s:%d", config.UI_HOST, config.UI_PORT, config.API_HOST, config.API_PORT)
    asyncio.create_task(_autostart())
    watcher = asyncio.create_task(_shutdown_when_signalled())
    await asyncio.gather(ui.serve(), px.serve())
    watcher.cancel()
    for t in tasks:
        t.cancel()
    await proxy.close()


if __name__ == "__main__":
    asyncio.run(main())

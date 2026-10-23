import contextlib
import re
import unittest
import unittest.mock

import httpx

from kiln import api, config, db, gpu, metrics, proxy
from kiln.server import manager
from . import ROOT
from .helpers import fake_gpu, llm, make_preset

LINE = re.compile(r'^[A-Za-z_:][A-Za-z0-9_:]*(\{([A-Za-z_][A-Za-z0-9_]*="([^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+$|^# (HELP|TYPE) ')


def model(name, **kw):
    return llm(ROOT / "models" / f"{name}.gguf", **kw)


def samples(text: str, name: str) -> dict[str, float]:
    """Sample lines of one metric, as {label string: value}."""
    out = {}
    for line in text.splitlines():
        m = re.match(rf"^{re.escape(name)}(\{{.*\}})? (\S+)$", line)
        if m:
            out[m.group(1) or ""] = float(m.group(2))
    return out


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await manager.stop()
        manager.blocked = None
        metrics.reset()
        db.execute("DELETE FROM presets")
        db.set_settings({"swap_on_request": True, "max_instances": 4})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for p in fake_gpu(manager):
            self.stack.enter_context(p)
        self.ui = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://kiln", timeout=60)
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url="http://kiln", timeout=60)
        self.upstream, proxy._client = proxy._client, httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))

    async def asyncTearDown(self):
        await self.ui.aclose()
        await self.api.aclose()
        await manager.stop()
        await proxy._client.aclose()
        proxy._client = self.upstream

    async def up(self, name, **kw):
        p = make_preset(name, model(name), **kw)
        inst = await manager.start(p["model"], p["flags"], p["env"], preset=p, role=p["role"])
        await inst.wait_ready(10)
        return inst

    async def scrape(self) -> str:
        r = await self.ui.get("/metrics")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.headers["content-type"].startswith("text/plain; version=0.0.4"))
        for line in r.text.splitlines():
            self.assertRegex(line, LINE)
        types = [line.split()[2] for line in r.text.splitlines() if line.startswith("# TYPE ")]
        self.assertEqual(len(types), len(set(types)), "each family is declared once")
        return r.text

    async def test_requests_models_and_llama_server_metrics(self):
        await self.up("alpha", alias="a"), await self.up("beta")
        for body in ({"model": "a"}, {"model": "a", "stream": True}, {"model": "beta"}):
            r = await self.api.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], **body})
            self.assertEqual(r.status_code, 200)
        metrics.observe({"model": "beta", "status": 499, "total_ms": 1500})
        text = await self.scrape()

        self.assertEqual(samples(text, "kiln_requests_total"), {
            '{model="alpha",result="ok"}': 2, '{model="beta",result="aborted"}': 1, '{model="beta",result="ok"}': 1})
        self.assertEqual(samples(text, "kiln_generated_tokens_total"), {'{model="alpha"}': 6, '{model="beta"}': 3})
        decode = samples(text, "kiln_decode_timed_tokens_total")['{model="alpha"}'] / samples(text, "kiln_decode_seconds_total")['{model="alpha"}']
        self.assertAlmostEqual(decode, 100.0)  # the fake's 3 tokens in 30 ms
        ttft = samples(text, "kiln_ttft_seconds_count")
        self.assertEqual(ttft, {'{model="alpha"}': 2, '{model="beta"}': 1}, "the aborted request has no TTFT")
        self.assertEqual(samples(text, "kiln_request_duration_seconds_count")['{model="beta"}'], 2)
        self.assertEqual(samples(text, 'kiln_request_duration_seconds_bucket')['{model="beta",le="+Inf"}'], 2)

        self.assertEqual(samples(text, "kiln_model_state"), {
            '{model="alpha",name="a",role="chat",state="ready"}': 1, '{model="beta",name="beta",role="chat",state="ready"}': 1})
        self.assertEqual(samples(text, "kiln_model_gpu_memory_bytes")['{model="alpha",name="a",role="chat"}'], 6000 * 2**20)
        self.assertEqual(samples(text, "llamacpp:prompt_tokens_total"), {'{model="alpha",name="a"}': 5, '{model="beta",name="beta"}': 5})
        self.assertIn('kiln_info{llama_version=', text)

    async def test_counters_survive_deleted_history(self):
        await self.up("alpha")
        await self.api.post("/v1/chat/completions", json={"messages": []})
        r = await self.ui.post("/api/stats/reset", json={"confirm": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(samples(await self.scrape(), "kiln_requests_total"), {'{model="alpha",result="ok"}': 1})

    async def test_nothing_running(self):
        text = await self.scrape()
        self.assertEqual(samples(text, "kiln_model_state"), {})
        self.assertNotIn("llamacpp:", text)

    async def test_needs_the_password_or_token_when_one_is_set(self):
        with unittest.mock.patch.object(config, "UI_PASSWORD", "pw"), unittest.mock.patch.object(config, "API_TOKEN", "tok"):
            self.assertEqual((await self.ui.get("/metrics")).status_code, 401)
            r = await self.ui.get("/metrics", headers={"Authorization": "Bearer tok"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual((await self.ui.get("/metrics", auth=("kiln", "pw"))).status_code, 200)


class MergeTests(unittest.TestCase):
    def test_families_are_declared_once_and_samples_keep_their_labels(self):
        a = "# HELP x:c Count.\n# TYPE x:c counter\nx:c 1\n# TYPE x:g gauge\nx:g{slot=\"0\"} 2.5\n"
        b = "# HELP x:c Count.\n# TYPE x:c counter\nx:c 7\n"
        out = metrics.merge_upstream([({"model": "m1"}, a), ({"model": 'q"2'}, b)])
        self.assertEqual(out, [
            "# HELP x:c Count.", "# TYPE x:c counter", 'x:c{model="m1"} 1', 'x:c{model="q\\"2"} 7',
            "# TYPE x:g gauge", 'x:g{model="m1",slot="0"} 2.5',
        ])


class EnergyTests(unittest.TestCase):
    def test_power_is_integrated_between_samples_and_gaps_are_skipped(self):
        s = gpu.GpuSampler.__new__(gpu.GpuSampler)
        s.live, s.energy_j = [], 0.0
        step = config.GPU_SAMPLE_S
        for t, w in ((0, 100), (step, 200), (step * 2, None), (step * 3, 100), (step * 100, 100)):
            sample = {"t": t, "power_w": w}
            s._add_energy(sample)
            s.live.append(sample)
        self.assertAlmostEqual(s.energy_j, 150 * step)


if __name__ == "__main__":
    unittest.main()

import asyncio
import contextlib
import json
import time
import unittest
import unittest.mock

import httpx

from kiln import db, decider, proxy
from kiln.server import manager
from . import ROOT
from .helpers import decider_checkpoint, fake_gpu, llm, make_preset


def model(name, **kw):
    return llm(ROOT / "models" / f"{name}.gguf", **kw)


CHAT = "/v1/chat/completions"


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await manager.stop()
        manager.blocked = None
        db.execute("DELETE FROM presets")
        db.execute("DELETE FROM requests")
        db.set_settings({"swap_on_request": True, "max_instances": 4, "idle_timeout_min": 10})
        self.addCleanup(db.set_settings, {"idle_timeout_min": 120})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url="http://kiln", timeout=60)
        # every test has its own event loop, and a pooled connection cannot outlive the loop it was made in
        self.upstream, proxy._client = proxy._client, httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))

    async def asyncTearDown(self):
        await self.http.aclose()
        await manager.stop()
        await proxy._client.aclose()
        proxy._client = self.upstream

    def gpu(self, **kw):
        for p in fake_gpu(manager, **kw):
            self.stack.enter_context(p)

    async def up(self, name, **kw):
        p = make_preset(name, kw.pop("path", None) or model(name), **kw)
        inst = await manager.start(p["model"], p["flags"], p["env"], preset=p, role=p["role"])
        await inst.wait_ready(10)
        inst.ready_at -= 10  # has been running a while, so it may be stopped for another model
        return inst

    async def chat(self, model_name=None, **extra):
        body = {"messages": [{"role": "user", "content": "hi"}], **extra}
        if model_name:
            body["model"] = model_name
        return await self.http.post(CHAT, json=body)

    def rows(self, where="1=1"):
        return db.rows(f"SELECT * FROM requests WHERE {where} ORDER BY id")

    async def test_the_model_field_picks_the_server(self):
        await self.up("alpha"), await self.up("beta")
        r = await self.chat("beta")
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "hi from beta")
        self.assertEqual(r.headers["x-kiln-model"], "beta")
        r = await self.chat("ALPHA")
        self.assertEqual(r.headers["x-kiln-model"], "alpha")
        r = await self.http.post(CHAT, json={"messages": []}, headers={"X-Kiln-Model": "beta"})
        self.assertEqual(r.headers["x-kiln-model"], "beta")

    async def test_an_alias_names_the_model(self):
        await self.up("Qwen3.8 27B · 128K", alias="qwen")
        r = await self.chat("qwen")
        self.assertEqual(r.headers["x-kiln-model"], "qwen")
        self.assertEqual(r.json()["model"], "qwen")  # llama-server was started with --alias qwen

    async def test_requests_are_recorded_against_the_model_that_answered(self):
        a, b = await self.up("alpha"), await self.up("beta")
        await self.chat("beta")
        await self.chat("alpha")
        rows = self.rows()
        self.assertEqual([r["model"] for r in rows], ["beta", "alpha"])
        self.assertEqual([r["run_id"] for r in rows], [b.run_id, a.run_id])
        self.assertEqual(rows[0]["predicted_n"], 3)
        self.assertIsNone(rows[0]["swap_ms"])

    async def test_unknown_names_use_the_default_chat_model_and_embeddings_use_the_embedding_model(self):
        await self.up("alpha")
        emb = await self.up("embedder", role="embedding")
        r = await self.chat("gpt-4o")
        self.assertEqual(r.headers["x-kiln-model"], "alpha")
        r = await self.http.post("/v1/embeddings", json={"input": "x"})
        self.assertEqual(r.headers["x-kiln-model"], "embedder")
        self.assertEqual(r.json()["data"][0]["embedding"], [0.1, 0.2])
        self.assertEqual(emb.inflight, 0)

    async def test_model_list_shows_loaded_and_swappable_models(self):
        await self.up("alpha")
        make_preset("beta", model("beta"), alias="b")
        data = (await self.http.get("/v1/models")).json()["data"]
        by_id = {m["id"]: m for m in data}
        self.assertEqual(by_id["alpha"]["kiln"]["state"], "loaded")
        self.assertEqual(by_id["b"]["kiln"]["state"], "unloaded")
        db.set_settings({"swap_on_request": False})
        self.assertEqual([m["id"] for m in (await self.http.get("/v1/models")).json()["data"]], ["alpha"])

    async def test_asking_for_an_unloaded_preset_swaps_it_in(self):
        self.gpu(total=7000, per_model=6000)
        await self.up("alpha")
        make_preset("beta", model("beta"))
        r = await self.chat("beta")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["x-kiln-model"], "beta")
        self.assertEqual(sorted(i.name for i in manager.live()), ["beta"])
        row = self.rows()[-1]
        self.assertEqual(row["model"], "beta")
        self.assertGreater(row["swap_ms"], 100)
        self.assertLess(row["ttft_ms"] or 0, row["swap_ms"] + 5000)

    async def test_models_that_fit_are_loaded_next_to_the_running_one(self):
        self.gpu(total=24000, per_model=6000)
        await self.up("alpha")
        make_preset("beta", model("beta"))
        await self.chat("beta")
        self.assertEqual(sorted(i.name for i in manager.live()), ["alpha", "beta"])

    async def test_presets_with_an_api_key_are_only_loaded_for_requests_that_carry_it(self):
        make_preset("locked", model("locked"), alias="locked", flags=[{"flag": "--api-key", "value": "s3cret,other"}])
        make_preset("open", model("open"), alias="open")
        r = await self.chat("locked")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(manager.live(), [])  # nothing was loaded for a stranger
        ids = [m["id"] for m in (await self.http.get("/v1/models")).json()["data"]]
        self.assertEqual(ids, ["open"])  # and the preset is not advertised to them
        wrong = await self.http.post(CHAT, json={"model": "locked", "messages": []}, headers={"Authorization": "Bearer nope"})
        self.assertEqual(wrong.status_code, 401)
        ok = await self.http.post(CHAT, json={"model": "locked", "messages": []}, headers={"Authorization": "Bearer other"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual([i.name for i in manager.live()], ["locked"])
        listed = await self.http.get("/v1/models", headers={"X-Api-Key": "s3cret"})
        self.assertIn("open", [m["id"] for m in listed.json()["data"]])

    async def test_with_swapping_off_the_loaded_model_answers(self):
        db.set_settings({"swap_on_request": False})
        await self.up("alpha")
        make_preset("beta", model("beta"))
        r = await self.chat("beta")
        self.assertEqual(r.headers["x-kiln-model"], "alpha")
        self.assertEqual(len(manager.live()), 1)

    async def test_a_swap_that_cannot_happen_says_why_and_is_recorded(self):
        self.gpu(total=7000, per_model=6000)
        await self.up("alpha", pinned=1)
        make_preset("beta", model("beta"))
        r = await self.chat("beta")
        self.assertEqual(r.status_code, 503)
        self.assertIn("free", r.json()["error"]["message"])
        self.assertEqual([i.name for i in manager.live()], ["alpha"])
        row = self.rows()[-1]
        self.assertEqual((row["status"], row["model"]), (503, "beta"))
        self.assertIn("free", row["error"])

    async def test_a_missing_model_file_is_a_clean_503(self):
        make_preset("ghost", ROOT / "models" / "nope.gguf")
        r = await self.chat("ghost")
        self.assertEqual(r.status_code, 503)
        self.assertIn("not found", r.json()["error"]["message"])

    async def test_streaming_requests_get_keepalives_while_the_model_loads(self):
        make_preset("slow", model("slow"), env={"FAKE_LOAD_S": "1.5"})
        self.stack.enter_context(unittest.mock.patch.object(proxy, "KEEPALIVE_S", 0.3))
        r = await self.chat("slow", stream=True)
        text = r.text
        self.assertTrue(text.startswith(": kiln is loading slow"), text[:80])
        self.assertIn('"content": "hi"', text)
        self.assertIn("[DONE]", text)
        self.assertEqual(r.headers["content-type"], "text/event-stream; charset=utf-8")

    async def test_streaming_swap_failure_arrives_as_an_event(self):
        make_preset("ghost", ROOT / "models" / "nope.gguf")
        r = await self.chat("ghost", stream=True)
        self.assertIn('"unavailable_error"', r.text)
        self.assertIn("[DONE]", r.text)

    async def test_nothing_loaded_and_the_tuner_block(self):
        r = await self.chat()
        self.assertEqual(r.status_code, 503)
        await self.up("alpha")
        manager.blocked = "Kiln is tuning flags."
        r = await self.chat()
        self.assertIn("tuning", r.json()["error"]["message"])
        manager.blocked = None

    async def test_status_checks_do_not_wait_for_a_loading_model(self):
        make_preset("slow", model("slow"), env={"FAKE_LOAD_S": "3"})
        p = db.row("SELECT id FROM presets WHERE name='slow'")
        from kiln.server import get_preset
        sp = get_preset(p["id"])
        inst = await manager.start(sp["model"], sp["flags"], sp["env"], preset=sp)
        r = await asyncio.wait_for(self.http.get("/props"), 2)
        self.assertEqual(r.status_code, 503)
        self.assertIn("loading", r.json()["error"]["message"])
        await inst.wait_ready(10)

    async def test_decision_requests_are_scored_by_kiln_for_pplx_checkpoints(self):
        path = decider_checkpoint(ROOT / "models" / "dec.gguf")
        dec = await self.up("decider", path=path, role="decision")
        self.assertEqual(dec.adapter, "pplx-v3")
        self.assertIn("--pooling", dec.argv)
        await self.up("alpha")
        body = {"state": "x", "questions": {"q": {"type": "choice", "criteria": {"a": None, "b": None}}}}
        r = await self.http.post("/v1/systemone", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertAlmostEqual(sum(out["answers"]["q"]["probabilities"].values()), 1.0)
        self.assertEqual(out["usage"]["output_tokens"], 0)
        self.assertEqual(r.headers["x-kiln-model"], "decider")
        row = self.rows("endpoint='/v1/systemone'")[-1]
        self.assertEqual((row["status"], row["model"], row["predicted_n"]), (200, "dec", 0))
        self.assertGreater(row["prompt_n"], 10)
        bad = await self.http.post("/v1/systemone", json={"questions": {}})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.rows("endpoint='/v1/systemone'")[-1]["status"], 400)

    async def test_decision_requests_without_a_decision_model(self):
        await self.up("alpha")
        r = await self.http.post("/v1/systemone", json={"state": "x", "questions": {"q": {"type": "noul"}}})
        self.assertEqual(r.status_code, 503)
        self.assertIn("decision model", r.json()["error"]["message"])

    async def test_a_decision_request_swaps_the_decider_in(self):
        self.gpu(total=24000, per_model=6000)
        await self.up("alpha")
        make_preset("decider", decider_checkpoint(ROOT / "models" / "dec2.gguf"), role="decision")
        r = await self.http.post("/v1/systemone", json={"state": "x", "questions": {"q": {"type": "noul"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(i.name for i in manager.live()), ["alpha", "decider"])

    async def test_requests_in_flight_are_counted(self):
        a = await self.up("alpha")
        task = asyncio.create_task(self.chat("alpha", fake_delay=0.8))
        await asyncio.sleep(0.3)
        self.assertEqual(a.inflight, 1)
        await task
        self.assertEqual(a.inflight, 0)

    async def test_only_inference_requests_count_as_use(self):
        a = await self.up("alpha")
        a.last_used = 1.0
        self.assertEqual((await self.http.get("/props")).status_code, 200)
        self.assertEqual(a.last_used, 1.0)  # a health probe must not keep a model loaded forever
        await self.chat("alpha")
        self.assertGreater(a.last_used, 1.0)

    async def test_a_model_unloaded_for_being_idle_loads_again_on_the_next_request(self):
        a = await self.up("alpha")
        a.last_used = a.ready_at = time.time() - 3600
        self.assertEqual([i.name for i in await manager.unload_idle()], ["alpha"])
        self.assertEqual(manager.live(), [])
        r = await self.chat("alpha")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([i.name for i in manager.live()], ["alpha"])

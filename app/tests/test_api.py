import contextlib
import json
import unittest
import unittest.mock

import httpx

from kiln import api, db, hf
from kiln.server import manager
from . import ROOT
from .helpers import fake_gpu, llm, make_preset


def model(name, **kw):
    return llm(ROOT / "models" / f"{name}.gguf", **kw)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await manager.stop()
        manager.blocked = None
        db.execute("DELETE FROM presets")
        db.set_settings({"max_instances": 4, "swap_on_request": True, "autostart_preset_ids": []})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://kiln", timeout=60)

    async def asyncTearDown(self):
        await self.http.aclose()
        await manager.stop()

    def gpu(self, **kw):
        for p in fake_gpu(manager, **kw):
            self.stack.enter_context(p)

    async def preset(self, name, **kw):
        body = {"name": name, "model": model(name), "flags": [], **kw}
        r = await self.http.post("/api/presets", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def start(self, pid, **kw):
        return await self.http.post("/api/server/start", json={"preset_id": pid, **kw})

    async def wait(self, run_id):
        inst = manager.get(run_id)
        await inst.wait_ready(10)
        inst.ready_at -= 10  # has been running a while, so it may be stopped for another model

    # ------------------------------------------------------------ presets

    async def test_roles_aliases_and_pinning_round_trip(self):
        p = await self.preset("Embedder", role="embedding", alias="embed", pinned=True)
        self.assertEqual((p["role"], p["alias"], p["pinned"]), ("embedding", "embed", True))
        got = (await self.http.get("/api/presets")).json()[0]
        self.assertEqual((got["role"], got["alias"], got["pinned"]), ("embedding", "embed", True))
        r = await self.http.put(f"/api/presets/{p['id']}", json={"name": "Embedder", "model": p["model"], "flags": [], "role": "chat", "alias": "", "pinned": False})
        self.assertEqual((r.json()["role"], r.json()["pinned"]), ("chat", False))

    async def test_validation(self):
        base = {"name": "X", "model": "", "flags": []}
        self.assertEqual((await self.http.post("/api/presets", json={**base, "role": "poet"})).status_code, 400)
        self.assertEqual((await self.http.post("/api/presets", json={**base, "alias": "bad/\\name!"})).status_code, 400)
        await self.preset("Alpha", alias="a")
        clash = await self.http.post("/api/presets", json={**base, "name": "Other", "alias": "A"})  # case-insensitive
        self.assertEqual(clash.status_code, 409)
        self.assertIn("already used", clash.json()["detail"])
        clash = await self.http.post("/api/presets", json={**base, "name": "a"})  # a name that is another preset's alias
        self.assertEqual(clash.status_code, 409)
        clash = await self.http.post("/api/presets", json={**base, "name": "Third", "alias": "Alpha"})  # an alias that is another's name
        self.assertEqual(clash.status_code, 409)

    async def test_disabled_flag_rows_are_kept(self):
        rows = [{"flag": "--spec-type", "value": "draft-mtp", "enabled": False}, {"flag": "-ngl", "value": "99"}]
        p = await self.preset("Spec", flags=rows)
        self.assertEqual(p["flags"], rows)

    async def test_duplicate_keeps_the_role_but_not_the_alias(self):
        p = await self.preset("Dec", role="decision", alias="dec", pinned=True)
        d = (await self.http.post(f"/api/presets/{p['id']}/duplicate")).json()
        self.assertEqual((d["role"], d["alias"], d["pinned"]), ("decision", "", True))

    # ------------------------------------------------------------ starting and stopping

    async def test_start_two_stop_one(self):
        a, b = await self.preset("alpha"), await self.preset("beta", role="embedding")
        ra = await self.start(a["id"])
        self.assertEqual(ra.status_code, 200, ra.text)
        await self.wait(ra.json()["started"]["run_id"])
        rb = await self.start(b["id"])
        await self.wait(rb.json()["started"]["run_id"])
        st = (await self.http.get("/api/status")).json()
        self.assertEqual(sorted(i["name"] for i in st["instances"]), ["alpha", "beta"])
        self.assertEqual(st["model"], "alpha")
        self.assertIn("api_port", st)
        again = await self.start(a["id"])
        self.assertEqual(again.status_code, 409)
        self.assertIn("already running", again.json()["detail"])
        await self.http.post("/api/server/stop", json={"run_id": ra.json()["started"]["run_id"]})
        st = (await self.http.get("/api/status")).json()
        self.assertEqual([i["name"] for i in st["instances"]], ["beta"])
        await self.http.post("/api/server/stop", json={})
        self.assertEqual((await self.http.get("/api/status")).json()["state"], "stopped")

    async def test_a_refusal_carries_what_could_be_stopped(self):
        self.gpu(total=7000, per_model=6000)
        a, b = await self.preset("alpha"), await self.preset("beta")
        ra = (await self.start(a["id"])).json()["started"]
        await self.wait(ra["run_id"])
        r = await self.start(b["id"])
        self.assertEqual(r.status_code, 409)
        data = r.json()
        self.assertEqual(data["code"], "no_fit")
        self.assertEqual([e["name"] for e in data["evict"]], ["alpha"])
        self.assertIn("free", data["detail"])
        # accepting the suggestion
        ok = await self.start(b["id"], replace=[e["run_id"] for e in data["evict"]])
        self.assertEqual(ok.status_code, 200, ok.text)
        await self.wait(ok.json()["started"]["run_id"])
        self.assertEqual([i.name for i in manager.live()], ["beta"])

    async def test_plan_and_force(self):
        self.gpu(total=7000, per_model=6000)
        a, b = await self.preset("alpha"), await self.preset("beta")
        await self.wait((await self.start(a["id"])).json()["started"]["run_id"])
        plan = (await self.http.post("/api/server/plan", json={"preset_id": b["id"]})).json()
        self.assertFalse(plan["fits"])
        self.assertEqual(plan["need_mib"], 6000)
        auto = (await self.http.post("/api/server/plan", json={"preset_id": b["id"], "replace": "auto"})).json()
        self.assertTrue(auto["fits"])
        self.assertEqual([e["name"] for e in auto["evict"]], ["alpha"])
        forced = await self.start(b["id"], force=True)
        self.assertEqual(forced.status_code, 200)
        await self.wait(forced.json()["started"]["run_id"])
        self.assertEqual(len(manager.live()), 2)

    async def test_replace_true_switches_in_one_call_like_the_extension_does(self):
        a, b = await self.preset("alpha"), await self.preset("beta")
        ra = (await self.start(a["id"])).json()["started"]
        await self.wait(ra["run_id"])
        rb = await self.start(b["id"], replace=True)  # stops alpha, then starts beta
        self.assertEqual(rb.status_code, 200, rb.text)
        await self.wait(rb.json()["started"]["run_id"])
        self.assertEqual([i.name for i in manager.live()], ["beta"])
        again = await self.start(b["id"], replace=True)  # the running preset is replaced, not refused
        self.assertEqual(again.status_code, 200, again.text)
        await self.wait(again.json()["started"]["run_id"])
        self.assertNotEqual(again.json()["started"]["run_id"], rb.json()["started"]["run_id"])
        self.assertEqual([i.name for i in manager.live()], ["beta"])
        self.assertEqual((await self.start(b["id"], replace="sideways")).status_code, 400)

    async def test_quick_snapshot_for_the_extension(self):
        a, b = await self.preset("alpha"), await self.preset("beta", role="embedding")
        await self.wait((await self.start(a["id"])).json()["started"]["run_id"])
        await self.wait((await self.start(b["id"])).json()["started"]["run_id"])
        db.set_settings({"autostart_preset_ids": [b["id"]]})
        q = (await self.http.get("/api/quick")).json()
        self.assertEqual((q["server"]["state"], q["server"]["model"]), ("ready", "alpha"))
        self.assertEqual(sorted(i["name"] for i in q["instances"]), ["alpha", "beta"])
        by_name = {p["name"]: p for p in q["presets"]}
        self.assertTrue(by_name["alpha"]["active"] and by_name["beta"]["active"])
        self.assertEqual((by_name["alpha"]["autostart"], by_name["beta"]["autostart"]), (False, True))
        self.assertEqual(by_name["beta"]["role"], "embedding")

    async def test_auth_token_and_cors_for_extensions(self):
        from kiln import config
        self.stack.enter_context(unittest.mock.patch.object(config, "UI_PASSWORD", "pw"))
        self.stack.enter_context(unittest.mock.patch.object(config, "API_TOKEN", "tok"))
        ext = {"Origin": "moz-extension://1a2b-3c"}
        self.assertEqual((await self.http.get("/api/quick")).status_code, 401)
        self.assertEqual((await self.http.get("/api/quick", headers={"Authorization": "Bearer tok"})).status_code, 200)
        self.assertEqual((await self.http.get("/api/quick", headers={"Authorization": "Bearer nope"})).status_code, 401)
        self.assertEqual((await self.http.get("/api/quick", auth=("kiln", "pw"))).status_code, 200)
        self.assertEqual((await self.http.get("/healthz")).status_code, 200)
        pre = await self.http.options("/api/quick", headers={**ext, "Access-Control-Request-Method": "GET"})
        self.assertEqual((pre.status_code, pre.headers["access-control-allow-origin"]), (204, ext["Origin"]))
        denied = await self.http.get("/api/quick", headers=ext)  # an extension, no credentials: 401 it can read
        self.assertEqual((denied.status_code, denied.headers["access-control-allow-origin"]), (401, ext["Origin"]))
        self.assertNotIn("www-authenticate", denied.headers)
        site = await self.http.get("/api/quick", headers={"Origin": "https://evil.example", "Authorization": "Bearer tok"})
        self.assertNotIn("access-control-allow-origin", site.headers)

    async def test_custom_flags_and_a_role_on_the_request(self):
        r = await self.http.post("/api/server/start", json={"model": model("raw"), "flags": [{"flag": "-c", "value": "1024"}], "role": "embedding"})
        self.assertEqual(r.status_code, 200, r.text)
        started = r.json()["started"]
        await self.wait(started["run_id"])
        self.assertEqual((started["role"], started["preset"]), ("embedding", None))
        self.assertIn("--embeddings", started["argv"])

    async def test_missing_model_and_unknown_preset(self):
        self.assertEqual((await self.http.post("/api/server/start", json={"model": str(ROOT / "nope.gguf")})).status_code, 400)
        self.assertEqual((await self.http.post("/api/server/start", json={"preset_id": 9999})).status_code, 404)
        self.assertEqual((await self.http.post("/api/server/stop", json={"run_id": 424242})).status_code, 404)

    async def test_logs_and_runs_are_per_instance(self):
        a, b = await self.preset("alpha"), await self.preset("beta")
        ra = (await self.start(a["id"])).json()["started"]
        rb = (await self.start(b["id"])).json()["started"]
        await self.wait(ra["run_id"]), await self.wait(rb["run_id"])
        la = (await self.http.get(f"/api/server/logs?run_id={ra['run_id']}")).json()
        lb = (await self.http.get(f"/api/server/logs?run_id={rb['run_id']}")).json()
        self.assertEqual((la["run_id"], lb["run_id"]), (ra["run_id"], rb["run_id"]))
        self.assertTrue(any("alias=alpha" in l[1] for l in la["lines"]))
        self.assertTrue(any("alias=beta" in l[1] for l in lb["lines"]))
        runs = (await self.http.get("/api/runs?limit=2")).json()
        self.assertEqual({r["port"] for r in runs}, {ra["port"], rb["port"]})
        self.assertEqual({r["role"] for r in runs}, {"chat"})

    async def test_a_loaded_model_cannot_be_deleted(self):
        a = await self.preset("alpha")
        r = (await self.start(a["id"])).json()["started"]
        await self.wait(r["run_id"])
        d = await self.http.delete("/api/models", params={"path": a["model"]})
        self.assertEqual(d.status_code, 409)
        self.assertIn("loaded", d.json()["detail"])

    async def test_crashes_are_reported_and_dismissed(self):
        p = await self.preset("doomed", )
        db.update("presets", p["id"], {"model": model("crash-me")})
        r = await self.start(p["id"])
        self.assertEqual(r.status_code, 200)
        run_id = r.json()["started"]["run_id"]
        await manager.get(run_id).exit_evt.wait()
        st = (await self.http.get("/api/status")).json()
        self.assertEqual(st["state"], "crashed")
        self.assertIn("out of memory", st["error"])
        await self.http.post("/api/server/dismiss", json={"run_id": run_id})
        self.assertEqual((await self.http.get("/api/status")).json()["state"], "stopped")

    # ------------------------------------------------------------ settings

    async def test_settings(self):
        a = await self.preset("alpha")
        r = await self.http.put("/api/settings", json={"autostart_preset_ids": [a["id"]], "swap_on_request": False, "max_instances": 99})
        s = r.json()
        self.assertEqual((s["autostart_preset_ids"], s["swap_on_request"], s["max_instances"]), ([a["id"]], False, 16))
        self.assertEqual((await self.http.put("/api/settings", json={"max_instances": 0})).json()["max_instances"], 1)
        self.assertEqual(db.DEFAULT_SETTINGS["idle_timeout_min"], 120)
        put = lambda v: self.http.put("/api/settings", json={"idle_timeout_min": v})
        self.assertEqual((await put(30)).json()["idle_timeout_min"], 30)
        self.assertEqual((await put(-5)).json()["idle_timeout_min"], 0)
        self.assertEqual((await put(None)).json()["idle_timeout_min"], 0)
        self.assertEqual((await put(120)).json()["idle_timeout_min"], 120)
        await self.http.delete(f"/api/presets/{a['id']}")  # deleting a preset drops it from the boot list
        self.assertEqual((await self.http.get("/api/settings")).json()["autostart_preset_ids"], [])

    async def test_the_old_single_autostart_setting_becomes_a_list(self):
        db.execute("DELETE FROM settings WHERE key IN ('autostart_preset_ids', 'autostart_preset_id')")
        db.execute("INSERT INTO settings(key, value) VALUES('autostart_preset_id', '7')")
        db._migrate_autostart()
        self.assertEqual(db.get_settings()["autostart_preset_ids"], [7])
        self.assertNotIn("autostart_preset_id", db.get_settings())
        db.set_settings({"autostart_preset_ids": []})

    async def test_roles_endpoint(self):
        roles = (await self.http.get("/api/roles")).json()
        self.assertEqual(set(roles), {"chat", "embedding", "rerank", "decision"})
        self.assertIn("--embeddings", roles["embedding"]["adds"])


class DraftFileTests(unittest.TestCase):
    def test_kinds_of_files_in_a_repo(self):
        self.assertEqual(hf.variant_kind("mmproj-F16.gguf"), "projector")
        self.assertEqual(hf.variant_kind("sub/mmproj-decider-f16.gguf"), "projector")
        self.assertEqual(hf.variant_kind("dspark-MiniCPM-V-4_7-35B-A3B-F16.gguf"), "draft")
        self.assertEqual(hf.variant_kind("Qwen3-4B-DFlash.gguf"), "draft")
        self.assertEqual(hf.variant_kind("Qwen3-4B-eagle3-Q8_0.gguf"), "draft")
        self.assertEqual(hf.variant_kind("Qwen3-0.6B-draft-Q8_0.gguf"), "draft")
        self.assertEqual(hf.variant_kind("MiniCPM-V-4_7-35B-A3B-Q4_K_M.gguf"), "model")
        self.assertEqual(hf.variant_kind("Qwen3-8B-Q4_K_M.gguf"), "model")

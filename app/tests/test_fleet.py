import asyncio
import contextlib
import time
import unittest

from kiln import config, db, events, fit
from kiln.server import Fleet, NoFit
from . import ROOT
from .helpers import fake_gpu, llm, make_preset


def model(name, **kw):
    return llm(ROOT / "models" / f"{name}.gguf", **kw)


class FleetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.execute("DELETE FROM presets")
        db.set_settings({"max_instances": 4, "idle_timeout_min": 10})
        self.addCleanup(db.set_settings, {"idle_timeout_min": 120})
        self.fleet = Fleet()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    async def asyncTearDown(self):
        await self.fleet.shutdown()

    def gpu(self, **kw):
        for p in fake_gpu(self.fleet, **kw):
            self.stack.enter_context(p)

    async def up(self, name, **kw):
        p = make_preset(name, model(name), **kw)
        inst = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, role=p["role"])
        await inst.wait_ready(10)
        inst.ready_at -= 10  # has been running a while, so it may be stopped for another model
        return inst

    async def test_two_models_run_at_once_on_their_own_ports(self):
        a, b = await self.up("alpha"), await self.up("beta", role="embedding")
        self.assertNotEqual(a.port, b.port)
        self.assertNotIn(config.TUNER_PORT, (a.port, b.port))
        st = self.fleet.status()
        self.assertEqual(st["state"], "ready")
        self.assertEqual({i["name"] for i in st["instances"]}, {"alpha", "beta"})
        self.assertEqual(st["model"], "alpha")  # the legacy fields describe the chat model
        self.assertIn("--embeddings", b.argv)
        self.assertIn("--alias", a.argv)
        await self.fleet.stop(a.run_id)
        self.assertEqual([i.name for i in self.fleet.live()], ["beta"])
        await self.fleet.stop()
        self.assertEqual(self.fleet.state, "stopped")

    async def test_the_same_preset_cannot_start_twice(self):
        a = await self.up("alpha")
        with self.assertRaises(RuntimeError):
            await self.fleet.start(a.model_path, [], {}, preset=a.preset)

    async def test_refuses_what_does_not_fit_and_suggests_what_to_stop(self):
        self.gpu(total=19000, per_model=6000)  # three models fit (3 * 6000 + 512 margin <= 19000)
        a, b, c = await self.up("alpha"), await self.up("beta"), await self.up("gamma")
        a.last_used, b.last_used, c.last_used = 100, 300, 200
        p = make_preset("delta", model("delta"))
        with self.assertRaises(NoFit) as cm:
            await self.fleet.start(p["model"], p["flags"], p["env"], preset=p)
        self.assertIn("free", str(cm.exception))
        self.assertEqual([e["name"] for e in cm.exception.evict], ["alpha"])  # least recently used first
        d = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, replace="auto")
        await d.wait_ready(10)
        self.assertEqual(sorted(i.name for i in self.fleet.live()), ["beta", "delta", "gamma"])

    async def test_pinned_models_are_not_evicted(self):
        self.gpu(total=13000, per_model=6000)
        a = await self.up("alpha", pinned=1)
        b = await self.up("beta")
        a.last_used, b.last_used = 1, 999  # alpha is older, but pinned
        p = make_preset("delta", model("delta"))
        d = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, replace="auto")
        await d.wait_ready(10)
        self.assertEqual(sorted(i.name for i in self.fleet.live()), ["alpha", "delta"])

    async def test_nothing_to_evict_means_a_refusal_even_with_auto(self):
        self.gpu(total=7000, per_model=6000)
        await self.up("alpha", pinned=1)
        p = make_preset("delta", model("delta"))
        with self.assertRaises(NoFit):
            await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, replace="auto")

    async def test_force_starts_anyway(self):
        self.gpu(total=7000, per_model=6000)
        await self.up("alpha")
        p = make_preset("delta", model("delta"))
        d = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p, force=True)
        await d.wait_ready(10)
        self.assertEqual(len(self.fleet.live()), 2)

    async def test_instance_limit(self):
        db.set_settings({"max_instances": 2})
        await self.up("alpha"), await self.up("beta")
        p = make_preset("gamma", model("gamma"))
        with self.assertRaises(NoFit) as cm:
            await self.fleet.start(p["model"], p["flags"], p["env"], preset=p)
        self.assertIn("limit", str(cm.exception))

    async def test_memory_still_to_be_taken_by_a_loading_model_counts_as_taken(self):
        # Telemetry only sees models that finished loading, like a real GPU early in a load.
        from unittest import mock
        est = {"gpu_mib": 6000, "ram_mib": 0, "adaptive": False, "context": 4096, "parts": {}}

        def mem():
            used = 6000 * sum(1 for i in self.fleet.live() if i.state == "ready")
            return {"total_mib": 12000, "free_mib": 12000 - used, "used_mib": used}

        for p in (mock.patch.object(fit, "gpu_memory", side_effect=mem), mock.patch.object(fit, "estimate", return_value=est),
                  mock.patch.object(fit, "ram_available_mib", return_value=64000)):
            self.stack.enter_context(p)
        p1 = make_preset("slow", model("slow"), env={"FAKE_LOAD_S": "2"})
        first = await self.fleet.start(p1["model"], p1["flags"], p1["env"], preset=p1)
        self.assertEqual(first.state, "loading")
        self.assertEqual(self.fleet.outstanding_mib(), 6000)
        p2 = make_preset("second", model("second"))
        self.assertFalse(self.fleet.plan(p2["model"], p2["flags"], p2)["fits"])  # 12000 free on paper, 6000 really
        await first.wait_ready(10)
        self.assertEqual(self.fleet.outstanding_mib(), 0)
        self.assertFalse(self.fleet.plan(p2["model"], p2["flags"], p2)["fits"])  # 6000 free, 6512 needed

    async def test_a_crash_stays_listed_until_dismissed(self):
        p = make_preset("doomed", model("crash-model"))
        inst = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p)
        with self.assertRaises(RuntimeError) as cm:
            await inst.wait_ready(10)
        self.assertIn("out of memory", str(cm.exception))
        self.assertEqual(self.fleet.state, "crashed")
        self.assertIn("out of memory", self.fleet.status()["instances"][0]["error"])
        self.fleet.dismiss(inst.run_id)
        self.assertEqual(self.fleet.state, "stopped")

    async def test_a_crash_does_not_disturb_the_others(self):
        a = await self.up("alpha")
        p = make_preset("doomed", model("crash-model"))
        bad = await self.fleet.start(p["model"], p["flags"], p["env"], preset=p)
        await bad.exit_evt.wait()
        self.assertEqual(a.state, "ready")
        self.assertEqual(self.fleet.state, "ready")

    async def test_restart_picks_up_edited_flags(self):
        a = await self.up("alpha", flags=[{"flag": "-c", "value": "2048"}])
        self.assertEqual(a.props["n_ctx"], 2048)
        db.update("presets", a.preset["id"], {"flags": '[{"flag": "-c", "value": "8192"}]'})
        b = await self.fleet.restart(a.run_id)
        await b.wait_ready(10)
        self.assertEqual(b.props["n_ctx"], 8192)
        self.assertEqual([i.name for i in self.fleet.live()], ["alpha"])

    async def test_concurrent_swaps_for_one_preset_share_one_load(self):
        p = make_preset("lazy", model("lazy"))
        got = await asyncio.gather(*(self.fleet.ensure(p) for _ in range(4)))
        self.assertEqual(len({id(i) for i in got}), 1)
        self.assertEqual(len(self.fleet.live()), 1)

    async def test_swap_makes_room_by_stopping_the_least_recently_used(self):
        self.gpu(total=13000, per_model=6000)
        a, b = await self.up("alpha"), await self.up("beta")
        a.last_used, b.last_used = 5, 50
        p = make_preset("gamma", model("gamma"))
        inst = await self.fleet.ensure(p)
        self.assertEqual(inst.state, "ready")
        self.assertEqual(sorted(i.name for i in self.fleet.live()), ["beta", "gamma"])

    async def test_swap_waits_for_running_requests_to_finish(self):
        self.gpu(total=7000, per_model=6000)
        a = await self.up("alpha")
        a.begin()
        p = make_preset("gamma", model("gamma"))
        task = asyncio.create_task(self.fleet.ensure(p))
        await asyncio.sleep(0.7)
        self.assertEqual(a.state, "ready")  # still draining
        self.assertTrue(a.draining)
        a.end()
        inst = await asyncio.wait_for(task, 15)
        self.assertEqual(inst.name, "gamma")
        self.assertEqual(a.state, "stopped")

    async def test_a_model_that_is_still_loading_is_not_stopped_for_another_swap(self):
        self.gpu(total=7000, per_model=6000)  # room for one
        slow = make_preset("slow", model("slow"), env={"FAKE_LOAD_S": "1.5"})
        first = asyncio.create_task(self.fleet.ensure(slow))
        await asyncio.sleep(0.4)
        other = make_preset("second", model("second"))
        second = asyncio.create_task(self.fleet.ensure(other))
        await asyncio.sleep(0.6)
        loading = self.fleet.by_preset(slow["id"])
        self.assertEqual((loading.state, loading.protected), ("loading", True))
        self.assertEqual((await first).name, "slow")  # the request that wanted it gets it
        self.assertEqual(self.fleet.by_preset(slow["id"]).state, "ready")
        got = await asyncio.wait_for(second, 25)  # then the other swap goes ahead
        self.assertEqual([i.name for i in self.fleet.live()], ["second"])
        self.assertEqual(got.state, "ready")

    async def test_a_model_asked_for_while_it_drains_is_loaded_again(self):
        self.gpu(total=7000, per_model=6000)
        a = await self.up("alpha")
        a.begin()  # a request is still running on alpha
        p_beta = make_preset("beta", model("beta"))
        swap_to_beta = asyncio.create_task(self.fleet.ensure(p_beta))
        await asyncio.sleep(0.7)
        self.assertTrue(a.draining)
        again = asyncio.create_task(self.fleet.ensure(a.preset))  # someone wants alpha back while it is stopping
        await asyncio.sleep(0.2)
        a.end()
        beta = await asyncio.wait_for(swap_to_beta, 15)
        self.assertEqual(beta.name, "beta")
        alpha2 = await asyncio.wait_for(again, 40)  # beta is evicted in turn, after it has had its moment: only one fits
        self.assertEqual((alpha2.name, alpha2.state), ("alpha", "ready"))
        self.assertIsNot(alpha2, a)
        self.assertEqual([i.name for i in self.fleet.live()], ["alpha"])

    # ------------------------------------------------------------ idle timeout

    @staticmethod
    def idle(inst, minutes):
        inst.last_used = inst.ready_at = time.time() - minutes * 60

    async def test_models_idle_past_the_timeout_are_unloaded(self):
        a, b = await self.up("alpha"), await self.up("beta")
        self.idle(a, 11)
        self.idle(b, 9)
        q = events.subscribe()
        self.addCleanup(events.unsubscribe, q)
        self.assertEqual([i.name for i in await self.fleet.unload_idle()], ["alpha"])
        self.assertEqual((a.state, b.state), ("stopped", "ready"))
        self.assertEqual([i.name for i in self.fleet.live()], ["beta"])
        kinds = []
        while not q.empty():
            kinds.append(q.get_nowait().split("\n", 1)[0])
        self.assertIn("event: idle_unload", kinds)
        self.assertIn("Stopping after 11 min", "".join(l[1] for l in a.logs))

    async def test_a_timeout_of_zero_never_unloads(self):
        db.set_settings({"idle_timeout_min": 0})
        a = await self.up("alpha")
        self.idle(a, 100000)
        self.assertEqual(await self.fleet.unload_idle(), [])
        self.assertEqual(a.state, "ready")

    async def test_a_model_never_used_counts_idle_from_when_it_became_ready(self):
        a = await self.up("alpha")
        a.last_used = time.time() - 3600  # set when the load began, long before it finished
        a.ready_at = time.time() - 60
        self.assertEqual(await self.fleet.unload_idle(), [])
        self.assertAlmostEqual(a.idle_s(), 60, delta=2)

    async def test_pinned_busy_and_tuner_held_models_stay(self):
        pinned, busy, plain = await self.up("alpha", pinned=1), await self.up("beta"), await self.up("gamma")
        late_pin = await self.up("delta")
        for i in (pinned, busy, plain, late_pin):
            self.idle(i, 60)
        busy.begin()
        db.update("presets", late_pin.preset["id"], {"pinned": 1})  # pinned after it started
        self.fleet.blocked = "tuning"
        self.assertEqual(await self.fleet.unload_idle(), [])
        self.fleet.blocked = None
        self.assertEqual([i.name for i in await self.fleet.unload_idle()], ["gamma"])
        self.assertEqual(sorted(i.name for i in self.fleet.live()), ["alpha", "beta", "delta"])
        busy.end()

    async def test_status_checks_do_not_count_as_use(self):
        a = await self.up("alpha")
        self.idle(a, 11)
        before = a.last_used
        a.begin(touch=False)
        a.end(touch=False)
        self.assertEqual((a.last_used, a.inflight), (before, 0))
        a.begin()
        a.end()
        self.assertGreater(a.last_used, before)

    async def test_the_tuner_block_stops_everything_from_starting(self):
        self.fleet.blocked = "tuning"
        p = make_preset("alpha", model("alpha"))
        with self.assertRaises(RuntimeError):
            await self.fleet.start(p["model"], p["flags"], p["env"], preset=p)

    async def test_snapshot_and_restore(self):
        a = await self.up("alpha")
        await self.up("beta", role="embedding")
        snap = self.fleet.snapshot()
        await self.fleet.stop()
        self.assertEqual(self.fleet.state, "stopped")
        self.assertEqual(await self.fleet.restore(snap), [])
        for i in self.fleet.live():
            await i.wait_ready(10)
        self.assertEqual(sorted(i.name for i in self.fleet.live()), ["alpha", "beta"])

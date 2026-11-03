"""The tuner against the fake llama-server, whose /completion speeds depend on the command line:
decode is fastest at -t 8 (it reports 12 as its own default), prompt processing grows with -tb, and
speculative decoding helps most with a DSpark draft file at length 7 (see tests/fake_llama.py)."""
import asyncio
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import httpx

from kiln import api, db, tunespace, tuner as tuner_mod
from kiln.server import manager
from kiln.tuner import tuner, get_tune
from . import ROOT
from .helpers import llm, write_gguf

DIR = ROOT / "models" / "tuning"


def dspark(directory, name="dspark-Fake.gguf", block=15) -> str:
    return write_gguf(directory / name, {"general.architecture": "dflash", "general.name": "Fake DSpark",
                                         "dflash.has_confidence_head": True, "dflash.block_size": block})


class Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.execute("DELETE FROM tunes")
        db.execute("DELETE FROM presets")
        for p in (mock.patch.object(tuner, "_vram_free", return_value=None),   # the host's real GPU is not the fake's
                  mock.patch.object(tuner_mod, "_cpu_count", return_value=24),
                  mock.patch.object(tuner_mod, "POLL_S", 0.05),
                  mock.patch.dict(os.environ, {"FAKE_LOAD_S": "0"})):
            p.start()
            self.addCleanup(p.stop)
        tuner._paused = False
        tuner._batch = None
        manager.blocked = None

    async def tune(self, model, **params) -> dict:
        tid = await tuner.start({"ctx": 4096, "depth": 1024, **params, "model": model})
        await tuner.task
        return get_tune(tid)

    def preset_flags(self, tune: dict) -> list[list]:
        row = db.row("SELECT flags FROM presets WHERE id=?", (tune["preset_id"],))
        return [[f["flag"], f["value"]] for f in json.loads(row["flags"])]

    def labels(self, tune: dict, stage: str) -> list[str]:
        return [t["label"] for t in tune["trials"] if t["stage"] == stage]


class DraftFileTests(Case):
    async def test_a_draft_file_next_to_the_model_is_tuned_and_saved(self):
        d = DIR / "drafted"
        model = llm(d / "Fake-Q4.gguf")
        draft = dspark(d)
        t = await self.tune(model, mode="quick")
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual([(x["type"], x["path"]) for x in t["params"]["drafts"]], [("draft-dspark", draft)])
        spec = [x for x in t["trials"] if x["stage"] == "Speculative (DSpark)"]
        self.assertEqual([x["cfg"]["nmax"] for x in spec], ["3", "5", "7", "10"])
        self.assertTrue(all(x["cfg"]["draft"] == draft for x in spec))
        self.assertIsNone(t["trials"][0]["cfg"]["draft"])  # the first fit runs without it
        self.assertEqual((t["best"]["cfg"]["spec"], t["best"]["cfg"]["nmax"], t["best"]["cfg"]["draft"]), ("draft-dspark", "7", draft))
        flags_ = self.preset_flags(t)
        self.assertEqual([f for f in flags_ if f[0] in ("--spec-type", "-md", "--spec-draft-n-max")],
                         [["--spec-type", "draft-dspark"], ["-md", draft], ["--spec-draft-n-max", "7"]])
        self.assertGreater(t["best"]["draft"], 0)  # acceptance was measured

    async def test_mtp_and_a_draft_file_are_both_candidates_and_the_faster_wins(self):
        d = DIR / "both"
        model = llm(d / "Fake-Q4.gguf", arch="qwen35", **{"qwen35.nextn_predict_layers": 1})
        dspark(d)
        t = await self.tune(model, mode="quick")
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual(self.labels(t, "Speculative (MTP)")[0], "--spec-type draft-mtp --spec-draft-n-max 1")
        self.assertEqual(len(self.labels(t, "Speculative (MTP)")), 5)
        self.assertEqual(self.labels(t, "Speculative (DSpark)")[0], "--spec-type draft-dspark -md dspark-Fake.gguf --spec-draft-n-max 3")
        self.assertEqual(len(self.labels(t, "Speculative (DSpark)")), 4)
        self.assertEqual((t["best"]["cfg"]["spec"], t["best"]["cfg"]["nmax"]), ("draft-dspark", "7"))  # 2.4x against the head's 1.5x

    async def test_speculative_off_skips_the_head_and_the_files_unless_files_are_named(self):
        d = DIR / "off"
        model = llm(d / "Fake-Q4.gguf", arch="qwen35", **{"qwen35.nextn_predict_layers": 1})
        draft = dspark(d)
        t = await self.tune(model, mode="quick", speculative=False)
        self.assertEqual(t["params"]["drafts"], [])
        self.assertFalse([x for x in t["trials"] if x["stage"].startswith("Speculative")])
        self.assertIsNone(t["best"]["cfg"]["spec"])
        self.assertNotIn("--spec-type", [f[0] for f in self.preset_flags(t)])
        t = await self.tune(model, mode="quick", speculative=False, drafts=[draft])
        self.assertEqual(len(self.labels(t, "Speculative (DSpark)")), 4)
        self.assertFalse(self.labels(t, "Speculative (MTP)"))
        t = await self.tune(model, mode="quick", drafts=[])
        self.assertEqual(len(self.labels(t, "Speculative (MTP)")), 5)
        self.assertFalse(self.labels(t, "Speculative (DSpark)"))

    async def test_a_file_that_is_not_a_draft_of_this_model_is_refused(self):
        model = llm(DIR / "lonely" / "Fake-Q4.gguf")
        with self.assertRaisesRegex(ValueError, "not a draft file"):
            await tuner.start({"ctx": 4096, "model": model, "drafts": ["/etc/passwd"]})

    async def test_the_presets_own_draft_flags_do_not_leak_into_other_candidates(self):
        d = DIR / "carried"
        model = llm(d / "Fake-Q4.gguf")
        draft = dspark(d)
        base = db.insert("presets", {"name": "Hand-made", "model": model, "created_at": 0, "updated_at": 0, "env": "{}",
                                     "flags": json.dumps([{"flag": "--jinja", "value": None}, {"flag": "-md", "value": draft},
                                                          {"flag": "--spec-type", "value": "draft-dspark"}])})
        t = await self.tune(model, mode="quick", base_preset_id=base, speculative=False)
        self.assertEqual(t["params"]["base"]["flags"], [{"flag": "--jinja", "value": None}])  # -md and --spec-type are re-tuned
        self.assertNotIn("-md", [f[0] for f in self.preset_flags(t)])


class ThreadTests(Case):
    def moe(self, name="moe-Q4.gguf", directory="spill"):
        return llm(DIR / directory / name, arch="qwen35moe", layers=8, **{"qwen35moe.expert_count": 8})

    async def test_the_spill_search_sweeps_generation_then_batch_threads(self):
        t = await self.tune(self.moe(), objective="context")
        self.assertEqual(t["status"], "done", t.get("error"))
        sweep = [x for x in t["trials"] if x["stage"] == "Threads"]
        self.assertEqual([(x["cfg"]["threads"], x["cfg"]["threads_batch"]) for x in sweep],
                         [("6", None), ("9", None), ("24", None), ("9", "12"), ("9", "24")])
        self.assertEqual([x["threads_used"] for x in sweep], [6, 9, 24, 9, 9])  # read from the server's log
        self.assertEqual(t["trials"][[x["stage"] for x in t["trials"]].index("Spill")]["threads_used"], 12)  # its own choice
        self.assertIn("-t 6", sweep[0]["label"])
        self.assertIn("-t 9 -tb 24", sweep[-1]["label"])
        flags_ = self.preset_flags(t)
        self.assertIn(["-t", "9"], flags_)
        self.assertIn(["-tb", "24"], flags_)
        self.assertIn("-t 9 -tb 24", db.row("SELECT description FROM presets WHERE id=?", (t["preset_id"],))["description"])

    async def test_a_default_that_is_best_leaves_the_thread_flags_out(self):
        with mock.patch.dict(os.environ, {"FAKE_AUTO_THREADS": "8"}):
            t = await self.tune(self.moe(directory="default8"), objective="context")
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual([x["cfg"]["threads"] for x in t["trials"] if x["stage"] == "Threads"][:3], ["4", "6", "24"])
        names = [f[0] for f in self.preset_flags(t)]
        self.assertNotIn("-t", names)  # 4, 6 and 24 are all slower than 8; only -tb 24 helps prompt processing
        self.assertIn("-tb", names)

    async def test_the_thread_counts_a_preset_already_used_are_measured_and_re_tuned(self):
        model = self.moe(directory="known")
        base = db.insert("presets", {"name": "Hand-made", "model": model, "created_at": 0, "updated_at": 0, "env": "{}",
                                     "flags": json.dumps([{"flag": "--jinja", "value": None}, {"flag": "-t", "value": "8"},
                                                          {"flag": "--threads-batch", "value": "16"}])})
        t = await self.tune(model, objective="context", base_preset_id=base)
        self.assertEqual(t["params"]["base_threads"], [8, 16])
        self.assertEqual(t["params"]["base"]["flags"], [{"flag": "--jinja", "value": None}])
        tried = {x["cfg"]["threads"] for x in t["trials"] if x["stage"] == "Threads"} | \
                {x["cfg"]["threads_batch"] for x in t["trials"] if x["stage"] == "Threads"}
        self.assertTrue({"8", "16"} <= tried)
        flags_ = self.preset_flags(t)
        self.assertEqual([f for f in flags_ if f[0] in ("-t", "--threads", "-tb", "--threads-batch")].count(["-t", "8"]), 1)

    async def test_other_objectives_keep_the_presets_threads_and_do_not_sweep(self):
        model = llm(DIR / "decode" / "plain-Q4.gguf")
        base = db.insert("presets", {"name": "Hand-made", "model": model, "created_at": 0, "updated_at": 0, "env": "{}",
                                     "flags": json.dumps([{"flag": "-t", "value": "10"}])})
        t = await self.tune(model, mode="quick", base_preset_id=base)
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertFalse([x for x in t["trials"] if x["stage"] == "Threads"])
        self.assertIn(["-t", "10"], self.preset_flags(t))

    async def test_a_draft_file_is_tuned_on_the_spill_too(self):
        d = DIR / "spill-draft"
        model = self.moe("moe-Q4.gguf", "spill-draft")
        draft = dspark(d)
        t = await self.tune(model, objective="context")
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual(len(self.labels(t, "Speculative (DSpark)")), 4)
        self.assertEqual(t["best"]["cfg"]["draft"], draft)
        self.assertIn(["-md", draft], self.preset_flags(t))
        # speculative decoding is measured on the best thread settings, not on the first fit
        self.assertEqual(t["trials"][[x["stage"] for x in t["trials"]].index("Speculative (DSpark)")]["cfg"]["threads"], "9")


class UnitTests(unittest.TestCase):
    def test_draft_lengths_follow_the_kind_of_draft_and_its_block_size(self):
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-dspark", "block_size": 15}), ["3", "5", "7", "10"])
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-dspark", "block_size": 7}), ["3", "5", "7"])
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-dflash", "block_size": 16}), ["4", "8", "16"])
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-dflash"}), ["3", "7", "15"])
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-simple"}), ["4", "8", "16"])
        self.assertEqual(tuner_mod._draft_lengths({"type": "draft-eagle3"}), ["2", "3", "4", "5"])

    def test_thread_candidates(self):
        self.assertEqual(tuner_mod._thread_candidates(12, 24, set()), [6, 9, 24])
        self.assertEqual(tuner_mod._thread_candidates(12, 24, {8, 12, 99}), [6, 8, 9, 24])  # the default is the baseline
        self.assertEqual(tuner_mod._thread_candidates(4, 4, set()), [2, 3])
        self.assertEqual(tuner_mod._thread_candidates(1, 2, set()), [2])

    def test_the_nearest_draft_of_each_kind_is_the_default(self):
        near = {"path": "/m/a-dspark.gguf", "name": "a", "type": "draft-dspark", "block_size": 15, "size": 1}
        far = {**near, "path": "/m/z-dspark.gguf"}
        other = {"path": "/m/e.gguf", "name": "e", "type": "draft-eagle3", "size": 2}
        meta = {"drafts": [near, far, other]}
        self.assertEqual([d["path"] for d in tuner_mod._chosen_drafts(meta, None, True)], ["/m/a-dspark.gguf", "/m/e.gguf"])
        self.assertEqual(tuner_mod._chosen_drafts(meta, None, False), [])
        self.assertEqual([d["path"] for d in tuner_mod._chosen_drafts(meta, ["/m/z-dspark.gguf"], False)], ["/m/z-dspark.gguf"])
        with self.assertRaises(ValueError):
            tuner_mod._chosen_drafts(meta, ["/m/other.gguf"], True)

    def test_a_configuration_that_did_not_fit_prunes_only_what_needs_more_memory(self):
        base = {"ngl": "99", "ctk": "f16", "ctv": "f16", "fa": "on", "ub": "512", "b": "2048", "spec": None, "draft": None, "nmax": None}
        mtp3 = {**base, "spec": "draft-mtp", "nmax": "3"}
        dsp3 = {**base, "spec": "draft-dspark", "draft": "/m/d.gguf", "nmax": "3"}
        needs = tuner_mod._needs_more
        self.assertTrue(needs({**base, "ub": "1024"}, base))
        self.assertTrue(needs({**mtp3, "ub": "1024"}, base))                  # bigger, and speculation only adds
        self.assertTrue(needs({**dsp3, "nmax": "7"}, dsp3))                   # same draft, longer
        self.assertFalse(needs({**dsp3, "nmax": "5", "ub": "1024"}, {**dsp3, "nmax": "7"}))  # shorter than the one that failed
        self.assertFalse(needs(dsp3, mtp3))                                   # the head and a file take different room
        self.assertFalse(needs(mtp3, dsp3))
        self.assertFalse(needs(base, mtp3))                                   # without speculation it may well fit
        self.assertFalse(needs({**base, "ub": "256"}, base))
        self.assertFalse(needs({**base, "ctk": "q8_0"}, base))

    def test_the_arguments_carry_the_draft_file_and_threads(self):
        c = {"ngl": "99", "ctx": "4096", "np": "1", "fa": "on", "ctk": "f16", "ctv": "f16", "ub": "512", "b": "2048",
             "spec": "draft-dspark", "draft": "/m/d.gguf", "nmax": "7", "ncmoe": None, "threads": "9", "threads_batch": "24"}
        a = tuner_mod._tuned_args(c)
        self.assertEqual(a[a.index("--spec-type"):], ["--spec-type", "draft-dspark", "-md", "/m/d.gguf", "--spec-draft-n-max", "7",
                                                      "-t", "9", "-tb", "24"])
        self.assertEqual(tuner_mod._combo_label(c), "-ctk f16 -ctv f16 · -fa on · -ub 512 -b 2048 · -t 9 -tb 24 · DSpark 7")
        # old trials (saved before these keys existed) still print
        old = {k: v for k, v in c.items() if k not in ("draft", "threads", "threads_batch")}
        self.assertNotIn("-md", tuner_mod._tuned_args({**old, "spec": None}))
        self.assertIn("-md", tuner_mod._tuned_args({**old, "draft": "/m/d.gguf"}))


class PlanTests(Case):
    """The stage plan the Tuner page draws: what is left to run, with sizes that sharpen as the run learns them."""

    async def watch(self, model, **params):
        snaps = []
        with mock.patch.object(tuner, "_publish", side_effect=lambda: snaps.append((tuner.stage, [dict(e) for e in tuner.plan]))):
            t = await self.tune(model, **params)
        self.assertEqual(t["status"], "done", t.get("error"))
        return t, snaps

    @staticmethod
    def sizes(snaps, step_id):
        """The sizes a step went through, in order, without repeats."""
        out = []
        for _, plan in snaps:
            n = next((e["max"] for e in plan if e["id"] == step_id), "absent")
            if n != "absent" and (not out or out[-1] != n):
                out.append(n)
        return out

    async def test_the_grid_size_is_exact_once_the_kv_types_are_known(self):
        t, snaps = await self.watch(llm(DIR / "plan-grid" / "plain-Q4.gguf"), objective="decode")
        grid = [x for x in t["trials"] if x["stage"] == "Grid"]
        self.assertEqual(self.sizes(snaps, "grid"), [None, 11])  # 3 flash-attention x 4 -ub, less the first fit that already ran
        self.assertEqual(len(grid), 11)
        first_grid = next(i for i, (stage, _) in enumerate(snaps) if stage == "Grid")
        self.assertEqual(snaps[first_grid][1][1]["max"], 11)  # known before the first of them (plan[1] is the grid)
        self.assertEqual([e["id"] for e in snaps[0][1]], ["kv", "grid", "confirm"])

    async def test_a_quantized_v_cache_removes_the_flash_attention_stage(self):
        t, snaps = await self.watch(llm(DIR / "plan-quick" / "plain-Q4.gguf"), mode="quick")
        self.assertEqual(self.sizes(snaps, "fa"), [2])
        self.assertEqual(len([x for x in t["trials"] if x["stage"] == "Flash attention"]), 2)
        self.assertEqual(len([x for x in t["trials"] if x["stage"] == "Batch size"]), 3)  # the first fit already ran 512
        with mock.patch.object(tuner_mod, "KV_LADDER", [("q8_0", "q4_0")]):
            t, snaps = await self.watch(llm(DIR / "plan-quick" / "plain-Q4.gguf"), mode="quick")
        self.assertEqual(self.sizes(snaps, "fa"), [2, 0])  # nothing to try with a quantized V cache
        self.assertFalse([x for x in t["trials"] if x["stage"] == "Flash attention"])

    async def test_the_thread_stage_shrinks_to_what_is_left_after_the_generation_threads_are_chosen(self):
        with mock.patch.dict(os.environ, {"FAKE_AUTO_THREADS": "8"}):
            t, snaps = await self.watch(llm(DIR / "plan-threads" / "moe-Q4.gguf", arch="qwen35moe", layers=8,
                                            **{"qwen35moe.expert_count": 8}), objective="context")
        sweep = [x for x in t["trials"] if x["stage"] == "Threads"]
        self.assertEqual(self.sizes(snaps, "threads"), [None, 5, 4])  # 3 generation + 2 batch, then -t 8 stays and one batch is left
        self.assertEqual(len(sweep), 4)

    async def test_speculative_stages_are_planned_per_kind_and_the_plan_is_gone_afterwards(self):
        d = DIR / "plan-spec"
        model = llm(d / "Fake-Q4.gguf", arch="qwen35", **{"qwen35.nextn_predict_layers": 1})
        dspark(d)
        t, snaps = await self.watch(model, mode="quick")
        plan = snaps[0][1]
        self.assertEqual([(e["id"], e["max"]) for e in plan if e["id"].startswith("spec:")], [("spec:MTP", 5), ("spec:DSpark", 4)])
        self.assertEqual([e["stages"] for e in plan if e["id"].startswith("spec:")], [["Speculative (MTP)"], ["Speculative (DSpark)"]])
        self.assertEqual(tuner.plan, [])
        self.assertEqual(tuner.live()["plan"], [])
        self.assertEqual(set(tuner.live()), {"running", "tune_id", "stage", "trial", "plan", "queued"})
        self.assertNotIn("plan", tuner.status())  # /api/status stays as it was

    async def test_every_stage_a_run_reports_is_in_its_plan(self):
        for kwargs, directory in (({"objective": "decode"}, "grid"), ({"mode": "quick"}, "quick")):
            t, snaps = await self.watch(llm(DIR / "plan-cover" / directory / "plain-Q4.gguf"), **kwargs)
            planned = {name for _, plan in snaps[:1] for e in plan for name in e["stages"]}
            self.assertLessEqual({x["stage"] for x in t["trials"]}, planned)
        model = llm(DIR / "plan-cover" / "spill" / "moe-Q4.gguf", arch="qwen35moe", layers=8, **{"qwen35moe.expert_count": 8})
        t, snaps = await self.watch(model, objective="context")
        self.assertLessEqual({x["stage"] for x in t["trials"]}, {name for e in snaps[0][1] for name in e["stages"]})


class QueueTests(Case):
    """Runs started while one is going wait in a queue and run in order."""

    def params(self, name, **kw):
        return {"ctx": 4096, "depth": 1024, "mode": "quick", "speculative": False,
                "model": llm(DIR / "queue" / name / f"{name}-Q4.gguf"), **kw}

    async def test_a_run_started_while_one_is_going_is_queued_and_runs_after_it(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("running", "queued"))
        self.assertEqual(tuner.status()["queued"], 1)
        await tuner.wait_idle()
        ta, tb = get_tune(a), get_tune(b)
        self.assertEqual((ta["status"], tb["status"]), ("done", "done"), tb.get("error"))
        self.assertLessEqual(ta["finished_at"], tb["started_at"])
        self.assertEqual(tuner.status()["queued"], 0)
        self.assertIsNone(manager.blocked)

    async def test_two_starts_at_once_run_one_after_the_other(self):
        a, b = await asyncio.gather(tuner.start(self.params("a")), tuner.start(self.params("b")))
        self.assertEqual(sorted([get_tune(a)["status"], get_tune(b)["status"]]), ["queued", "running"])
        await tuner.wait_idle()
        self.assertEqual({get_tune(a)["status"], get_tune(b)["status"]}, {"done"})

    async def test_a_bad_request_is_refused_when_queued_not_later(self):
        await tuner.start(self.params("a"))
        with self.assertRaisesRegex(ValueError, "at least 512"):
            await tuner.start(self.params("b", ctx=100))
        self.assertEqual(tuner.status()["queued"], 0)
        await tuner.wait_idle()

    async def test_cancel_stops_the_running_run_and_the_queue_goes_on(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        await tuner.cancel()
        await tuner.wait_idle()
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("cancelled", "done"))

    async def test_queued_runs_can_be_removed_one_or_all(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        c = await tuner.start(self.params("c"))
        tuner.remove(b)
        self.assertIsNone(get_tune(b))
        with self.assertRaisesRegex(RuntimeError, "Only a queued run"):
            tuner.remove(a)
        with self.assertRaises(LookupError):
            tuner.remove(9999)
        self.assertEqual(tuner.clear_queue(), 1)
        self.assertIsNone(get_tune(c))
        await tuner.wait_idle()
        self.assertEqual(get_tune(a)["status"], "done")

    async def test_what_was_running_comes_back_once_after_the_whole_queue(self):
        snap = [{"model": "/m/chat.gguf", "rows": [], "env": {}, "preset_id": None, "role": "chat"}]
        with mock.patch.object(manager, "snapshot", return_value=snap), \
             mock.patch.object(manager, "stop", new=mock.AsyncMock()) as stop, \
             mock.patch.object(manager, "restore", new=mock.AsyncMock(return_value=[])) as restore:
            await tuner.start(self.params("a"))
            b = await tuner.start(self.params("b"))
            await tuner.wait_idle()
        stop.assert_awaited_once()
        restore.assert_awaited_once_with(snap)
        self.assertEqual(get_tune(b)["params"]["previous"], snap)

    async def test_each_winner_that_asked_to_start_starts_when_the_queue_empties(self):
        with mock.patch.object(manager, "start", new=mock.AsyncMock()) as start, \
             mock.patch.object(manager, "restore", new=mock.AsyncMock(return_value=[])):
            a = await tuner.start(self.params("a", start_after=True))
            await tuner.start(self.params("b"))  # didn't ask
            c = await tuner.start(self.params("c", start_after=True))
            await tuner.wait_idle()
        self.assertEqual([call.args[0] for call in start.await_args_list],
                         [get_tune(a)["model"], get_tune(c)["model"]])

    async def test_a_queued_run_whose_model_was_deleted_fails_and_the_queue_goes_on(self):
        await tuner.start(self.params("a"))
        gone = self.params("gone")
        b = await tuner.start(gone)
        c = await tuner.start(self.params("c"))
        os.remove(gone["model"])
        await tuner.wait_idle()
        self.assertEqual(get_tune(b)["status"], "failed")
        self.assertIn("Model file not found", get_tune(b)["error"])
        self.assertEqual(get_tune(c)["status"], "done")

    async def test_shutdown_leaves_the_queue_for_the_next_boot(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        await tuner.shutdown()
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("cancelled", "queued"))
        await tuner.resume()
        await tuner.wait_idle()
        self.assertEqual(get_tune(b)["status"], "done")


class QueueApiTests(Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://kiln", timeout=60)
        self.addAsyncCleanup(self.http.aclose)
        self.addAsyncCleanup(tuner.wait_idle)

    def body(self, name):
        return {"ctx": 4096, "depth": 1024, "mode": "quick", "speculative": False,
                "model": llm(DIR / "queue-api" / name / f"{name}-Q4.gguf")}

    async def test_queueing_listing_and_removing_over_http(self):
        r1 = (await self.http.post("/api/tune", json=self.body("a"))).json()
        r2 = (await self.http.post("/api/tune", json=self.body("b"))).json()
        r3 = (await self.http.post("/api/tune", json=self.body("c"))).json()
        self.assertEqual([(r["status"], r["position"]) for r in (r1, r2, r3)], [("running", 0), ("queued", 1), ("queued", 2)])
        listing = (await self.http.get("/api/tune")).json()
        self.assertEqual([t["id"] for t in listing["queue"]], [r2["id"], r3["id"]])
        self.assertNotIn(r2["id"], [t["id"] for t in listing["tunes"]])
        self.assertEqual(listing["live"]["queued"], 2)
        self.assertEqual((await self.http.delete(f"/api/tune/{r1['id']}")).status_code, 409)
        self.assertEqual((await self.http.delete("/api/tune/424242")).status_code, 404)
        self.assertEqual((await self.http.delete(f"/api/tune/{r2['id']}")).json(), {"ok": True})
        self.assertEqual((await self.http.delete("/api/tune/queue")).json(), {"removed": 1})
        self.assertEqual((await self.http.get("/api/tune")).json()["queue"], [])

    async def test_a_bad_request_is_a_400_and_queues_nothing(self):
        await self.http.post("/api/tune", json=self.body("a"))
        r = await self.http.post("/api/tune", json={**self.body("b"), "ctx": 100})
        self.assertEqual(r.status_code, 400)
        self.assertEqual((await self.http.get("/api/tune")).json()["queue"], [])


class SearchSpaceRunTests(Case):
    """Runs against the fake llama-server with a search space: what is fixed is in every trial, what is
    listed is searched, and nothing the space leaves on auto changes."""

    def plain(self, name="plain-Q4.gguf", directory="space"):
        return llm(DIR / directory / name)

    @staticmethod
    def sp(**dims) -> dict:
        return {k: (v if isinstance(v, dict) else {"mode": "list", "values": v}) for k, v in dims.items()}

    @staticmethod
    def fixed(v) -> dict:
        return {"mode": "fixed", "values": [v]}

    async def test_a_fixed_np_is_in_every_trial_and_the_preset(self):
        t = await self.tune(self.plain(), mode="quick", space=self.sp(np=self.fixed("2")))
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual({x["cfg"]["np"] for x in t["trials"]}, {"2"})
        self.assertEqual(t["params"]["space"], {"np": {"mode": "fixed", "values": ["2"]}})
        self.assertEqual([f for f in self.preset_flags(t) if f[0] == "-np"], [["-np", "2"]])
        self.assertIn("-np 2", t["trials"][0]["label"])

    async def test_a_ub_list_limits_the_grid_to_those_values(self):
        t = await self.tune(self.plain(directory="space-ub"), space=self.sp(ub=["256", "1024"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        grid = [x for x in t["trials"] if x["stage"] == "Grid"]
        self.assertEqual({x["cfg"]["ub"] for x in t["trials"] if x["stage"] in ("Grid", "KV cache")}, {"256", "1024"})
        self.assertEqual(t["trials"][0]["cfg"]["ub"], "256")  # the fit starts from the first listed value
        self.assertEqual(len(grid), 3 * 2 - 1)  # 3 flash attention x 2 -ub, less the first fit that already ran

    async def test_the_grid_size_in_the_plan_is_exact_with_a_space(self):
        snaps = []
        with mock.patch.object(tuner, "_publish", side_effect=lambda: snaps.append((tuner.stage, [dict(e) for e in tuner.plan]))):
            t = await self.tune(self.plain(directory="space-plan"), space=self.sp(ub=["256", "1024"], np=["1", "2"]))
        grid = [x for x in t["trials"] if x["stage"] == "Grid"]
        self.assertEqual(len(grid), 3 * 2 * 2 - 1)
        first = next(plan for stage, plan in snaps if stage == "Grid")
        self.assertEqual(next(e["max"] for e in first if e["id"] == "grid"), len(grid))

    async def test_fa_fixed_off_prunes_a_quantized_v_cache(self):
        t = await self.tune(self.plain(directory="space-fa"), kv_policy="all", space=self.sp(
            fa=self.fixed("off"), ctk=self.fixed("f16"), ctv=["f16", "q8_0"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        pruned = [x for x in t["trials"] if x.get("pruned") and x["cfg"]["ctv"] == "q8_0"]
        self.assertTrue(pruned)
        self.assertEqual({x["error"] for x in pruned}, {"a quantized V cache needs flash attention"})
        self.assertTrue(all(x["cfg"]["fa"] == "off" for x in t["trials"]))
        self.assertEqual(t["best"]["cfg"]["ctv"], "f16")

    async def test_ctk_list_with_ctv_fixed_fits_in_precision_order_and_precise_takes_the_first(self):
        space = self.sp(ctk=["q4_0", "f16", "q8_0"], ctv=self.fixed("q8_0"))
        t = await self.tune(self.plain(directory="space-kv"), mode="quick", space=space)
        fits = [x for x in t["trials"] if x["stage"] == "KV cache"]
        self.assertEqual([(x["cfg"]["ctk"], x["cfg"]["ctv"]) for x in fits], [("f16", "q8_0")])  # precise: the first fit
        t = await self.tune(self.plain(directory="space-kv"), mode="quick", kv_policy="all", space=space)
        fits = [x for x in t["trials"] if x["stage"] == "KV cache"]
        self.assertEqual([(x["cfg"]["ctk"], x["cfg"]["ctv"]) for x in fits],
                         [("f16", "q8_0"), ("q8_0", "q8_0"), ("q4_0", "q8_0")])

    async def test_np_list_without_unified_kv_prunes_the_slot_that_is_too_small(self):
        t = await self.tune(self.plain(directory="space-np"), space=self.sp(np=["1", "4"], kvu=self.fixed("off")))
        self.assertEqual(t["status"], "done", t.get("error"))
        pruned = [x for x in t["trials"] if x.get("pruned") and x["cfg"]["np"] == "4"]
        self.assertEqual(len(pruned), 3 * 4)  # every np 4 combination: 3 flash attention x 4 -ub
        self.assertEqual({x["error"] for x in pruned}, {"the deep prompt doesn't fit in one of 4 slots"})
        self.assertTrue(all(x["cfg"]["np"] == "1" for x in t["trials"] if x["stage"] in ("KV cache", "Confirm")))

    async def test_unified_kv_keeps_the_slots_whole(self):
        t = await self.tune(self.plain(directory="space-kvu"), mode="quick", space=self.sp(np=["4"], kvu=self.fixed("on")))
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertFalse([x for x in t["trials"] if x.get("pruned")])
        self.assertIn(["--kv-unified", None], self.preset_flags(t))  # a flag without a value is a row with value None

    async def test_quick_mode_sweeps_a_threads_list_as_its_own_stage(self):
        snaps = []
        with mock.patch.object(tuner, "_publish", side_effect=lambda: snaps.append([dict(e) for e in tuner.plan])):
            t = await self.tune(self.plain(directory="space-threads"), mode="quick", space=self.sp(threads=["4", "8", "12"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        step = next(e for e in snaps[0] if e["id"] == "dim:threads")
        self.assertEqual((step["label"], step["stages"], step["max"]), ("Threads", ["Threads"], 3))
        self.assertEqual({x["cfg"]["threads"] for x in t["trials"]}, {"4", "8", "12"})
        self.assertEqual([x["cfg"]["threads"] for x in t["trials"] if x["stage"] == "Threads"], ["8", "12"])  # 4 ran in the fit
        self.assertEqual(t["best"]["cfg"]["threads"], "8")  # the fake's fastest
        self.assertIn(["-t", "8"], self.preset_flags(t))
        planned = {n for e in snaps[0] for n in e["stages"]}
        self.assertLessEqual({x["stage"] for x in t["trials"]}, planned)

    async def test_the_context_objective_sweeps_exactly_the_listed_threads(self):
        model = llm(DIR / "space-ctx" / "moe-Q4.gguf", arch="qwen35moe", layers=8, **{"qwen35moe.expert_count": 8})
        t = await self.tune(model, objective="context", space=self.sp(threads=["5", "8"], threads_batch=self.fixed("16")))
        self.assertEqual(t["status"], "done", t.get("error"))
        sweep = [x for x in t["trials"] if x["stage"] == "Threads"]
        self.assertEqual([x["cfg"]["threads"] for x in sweep], ["5", "8"])
        self.assertEqual({x["cfg"]["threads_batch"] for x in t["trials"]}, {"16"})
        self.assertEqual(t["best"]["cfg"]["threads"], "8")

    async def test_nmax_replaces_the_draft_lengths_and_is_clamped_to_the_block_size(self):
        d = DIR / "space-nmax"
        model = llm(d / "Fake-Q4.gguf")
        dspark(d, block=7)
        t = await self.tune(model, mode="quick", space=self.sp(nmax=["2", "4", "16"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual([x["cfg"]["nmax"] for x in t["trials"] if x["stage"] == "Speculative (DSpark)"], ["2", "4", "7"])

    async def test_draft_gpu_layers_apply_to_draft_files_and_never_to_mtp(self):
        d = DIR / "space-ngld"
        model = llm(d / "Fake-Q4.gguf", arch="qwen35", **{"qwen35.nextn_predict_layers": 1})
        draft = dspark(d)
        t = await self.tune(model, mode="quick", space=self.sp(nmax=["3"], ngld=self.fixed("0")))
        self.assertEqual(t["status"], "done", t.get("error"))
        spec = [x for x in t["trials"] if x["stage"].startswith("Speculative")]
        self.assertEqual({x["cfg"].get("ngld") for x in spec if x["cfg"]["spec"] == "draft-mtp"}, {None})
        self.assertEqual({x["cfg"]["ngld"] for x in spec if x["cfg"]["draft"] == draft}, {"0"})
        self.assertEqual(len({x["cfg"]["ngld"] for x in t["trials"] if "ngld" in x["cfg"]}), 1)
        a = tuner_mod._tuned_args(next(x for x in spec if x["cfg"]["draft"])["cfg"])
        self.assertEqual(a[a.index("-ngld"):a.index("-ngld") + 2], ["-ngld", "0"])
        self.assertNotIn("-ngld", tuner_mod._tuned_args(next(x for x in spec if x["cfg"]["spec"] == "draft-mtp")["cfg"]))

    async def test_a_listed_draft_setting_is_a_stage_after_speculation_in_quick_mode(self):
        d = DIR / "space-pmin"
        model = llm(d / "Fake-Q4.gguf")
        dspark(d)
        t = await self.tune(model, mode="quick", space=self.sp(pmin=["0.1", "0.5"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual([x["cfg"]["pmin"] for x in t["trials"] if x["stage"] == "Draft p-min"], ["0.1", "0.5"])
        self.assertTrue(all("pmin" not in x["cfg"] for x in t["trials"] if x["stage"] != "Draft p-min"))

    async def test_a_space_too_big_is_refused_before_a_run_exists(self):
        big = self.sp(threads=[str(i) for i in range(1, 33)], np=[str(i) for i in range(1, 33)])
        with self.assertRaisesRegex(ValueError, "combinations; narrow it to at most 4096"):
            await tuner.start({"ctx": 4096, "model": self.plain(directory="space-big"), "space": big})
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM tunes"), 0)
        self.assertFalse(tuner.running)

    async def test_a_bad_space_value_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Micro-batch"):
            await tuner.start({"ctx": 4096, "model": self.plain(), "space": {"ub": {"mode": "list", "values": ["0"]}}})

    async def test_spec_off_removes_the_speculative_stages(self):
        d = DIR / "space-specoff"
        model = llm(d / "Fake-Q4.gguf", arch="qwen35", **{"qwen35.nextn_predict_layers": 1})
        dspark(d)
        snaps = []
        with mock.patch.object(tuner, "_publish", side_effect=lambda: snaps.append([dict(e) for e in tuner.plan])):
            t = await self.tune(model, mode="quick", space={"spec": {"mode": "off"}})
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertFalse([x for x in t["trials"] if x["stage"].startswith("Speculative")])
        self.assertFalse([e for e in snaps[0] if e["id"].startswith("spec:")])
        self.assertIsNone(t["best"]["cfg"]["spec"])

    async def test_the_base_presets_flags_are_stripped_only_for_dimensions_the_space_sets(self):
        model = self.plain(directory="space-strip")
        base = db.insert("presets", {"name": "Hand-made", "model": model, "created_at": 0, "updated_at": 0, "env": "{}",
                                     "flags": json.dumps([{"flag": "--jinja", "value": None}, {"flag": "-np", "value": "4"},
                                                          {"flag": "--kv-unified", "value": None},
                                                          {"flag": "--no-op-offload", "value": None}])})
        t = await self.tune(model, mode="quick", base_preset_id=base,
                            space=self.sp(np=self.fixed("2"), op_offload=self.fixed("on")))
        self.assertEqual(t["status"], "done", t.get("error"))
        flags_ = self.preset_flags(t)
        self.assertEqual([f for f in flags_ if f[0] == "-np"], [["-np", "2"]])
        self.assertEqual([f for f in flags_ if f[0] == "--kv-unified"], [["--kv-unified", None]])  # kvu is auto: kept
        self.assertNotIn(["--no-op-offload", None], flags_)  # op_offload is fixed: replaced
        self.assertIn(["--op-offload", None], flags_)

    async def test_a_context_list_depth_follows_the_smallest_and_the_fit_starts_from_the_largest(self):
        t = await self.tune(self.plain(directory="space-ctx-list"), mode="quick", depth=8192,
                            space=self.sp(ctx=["4096", "2048"]))
        self.assertEqual(t["status"], "done", t.get("error"))
        self.assertEqual(t["params"]["depth"], 2048 - 128)
        self.assertEqual(t["trials"][0]["cfg"]["ctx"], "4096")

    async def test_no_space_leaves_the_configurations_as_they_were(self):
        t = await self.tune(self.plain(directory="space-none"), mode="quick")
        self.assertEqual(t["params"]["space"], {})
        self.assertEqual(set(t["trials"][0]["cfg"]), {"ngl", "ctx", "np", "fa", "ctk", "ctv", "ub", "b", "spec", "draft",
                                                     "nmax", "ncmoe", "threads", "threads_batch"})


class SearchSpaceUnitTests(unittest.TestCase):
    base = {"ngl": "99", "ctx": "4096", "ctk": "f16", "ctv": "f16", "fa": "on", "ub": "512", "b": "2048",
            "spec": None, "draft": None, "nmax": None}

    def test_needs_more_grows_ub_b_and_ngld_and_ignores_what_costs_no_memory(self):
        needs = tuner_mod._needs_more
        b = self.base
        self.assertTrue(needs({**b, "ub": "1024"}, b))
        self.assertTrue(needs({**b, "threads": "4", "numa": "isolate", "poll": "0", "prio": "2", "cache_reuse": "64",
                               "load_mode": "mmap", "repack": "on"}, b))  # no VRAM effect
        d = {**b, "spec": "draft-dspark", "draft": "/m/d.gguf", "nmax": "3", "ngld": "10"}
        self.assertTrue(needs({**d, "ngld": "99", "nmin": "1", "pmin": "0.5"}, d))
        self.assertFalse(needs({**d, "ngld": "5"}, d))
        self.assertTrue(needs({**d, "ngld": "10", "nmax": "5"}, d))
        self.assertFalse(needs({**d, "ctkd": "q8_0"}, d))  # another draft cache type: not comparable
        self.assertFalse(needs({**b, "np": "2"}, b))        # a key one side lacks counts as None
        self.assertTrue(needs({**b, "np": "2", "ub": "1024"}, {**b, "np": "2"}))
        self.assertFalse(needs({**b, "kvu": "on"}, {**b, "kvu": "off"}))
        self.assertFalse(needs({**b, "ctx": "2048"}, b))
        self.assertTrue(needs({**b, "b": "4096"}, b))

    def test_rows_and_arguments_carry_valueless_flags_after_todays_flags(self):
        c = {**self.base, "np": "2", "kvu": "on", "swa_full": "off", "numa": "none", "poll": "0", "threads": "9"}
        rows = tuner_mod._tuned_rows(c)
        self.assertEqual(rows[-3:], [{"flag": "-t", "value": "9"}, {"flag": "--kv-unified", "value": None},
                                     {"flag": "--poll", "value": "0"}])
        a = tuner_mod._tuned_args(c)
        self.assertEqual(a[a.index("-t"):], ["-t", "9", "--kv-unified", "--poll", "0"])
        self.assertEqual(tuner_mod._combo_label(c), "-ctk f16 -ctv f16 · -fa on · -ub 512 -b 2048 · -t 9 · -np 2 · "
                                                    "--kv-unified · --poll 0 · no spec")
        self.assertEqual(tuner_mod._complexity(c), 1 + 1 + 1 + 1)  # threads, np, kvu, poll

    def test_ngl_auto_still_means_fit_on(self):
        a = tuner_mod._tuned_args({**self.base, "ngl": "auto", "np": "1"})
        self.assertEqual(a[:2], ["--fit", "on"])


class SpaceTests(unittest.TestCase):
    """The search space's schema: validation, flag rows, KV pairs and candidate counts (no run is started)."""

    def base(self, **kw) -> dict:
        return {"objective": "decode", "mode": "grid", "kv_policy": "precise", "vary_batch": False, "space": {},
                "spec_groups": [], "kv_ladder": tuner_mod.KV_LADDER, "ubatches": tuner_mod.UBATCHES, **kw}

    def test_no_space_is_the_default(self):
        self.assertEqual(tunespace.normalize(None), {})
        self.assertEqual(tunespace.normalize({"ub": {"mode": "auto"}}), {})

    def test_unknown_key_and_mode_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown search dimension: nope"):
            tunespace.normalize({"nope": {"mode": "auto"}})
        with self.assertRaisesRegex(ValueError, "Micro-batch: unknown mode 'some'"):
            tunespace.normalize({"ub": {"mode": "some"}})

    def test_bad_values_name_the_dimension(self):
        with self.assertRaisesRegex(ValueError, r"^Micro-batch: 70000 is above 65536\.$"):
            tunespace.normalize({"ub": {"mode": "list", "values": ["256", "70000"]}})
        with self.assertRaisesRegex(ValueError, "Flash attention: maybe is not one of on, off, auto"):
            tunespace.normalize({"fa": {"mode": "fixed", "values": ["maybe"]}})
        with self.assertRaisesRegex(ValueError, "Draft p-min: 1.5 is above 1"):
            tunespace.normalize({"pmin": {"mode": "fixed", "values": ["1.5"]}})
        with self.assertRaisesRegex(ValueError, "Tensor split: 'a,b' is not valid"):
            tunespace.normalize({"tensor_split": {"mode": "fixed", "values": ["a,b"]}})
        with self.assertRaisesRegex(ValueError, "Micro-batch: 'x' is not a whole number"):
            tunespace.normalize({"ub": {"mode": "fixed", "values": ["x"]}})
        with self.assertRaisesRegex(ValueError, "Micro-batch: .* is not a value"):
            tunespace.normalize({"ub": {"mode": "fixed", "values": [True]}})

    def test_fixed_needs_one_value_and_list_needs_one_to_32(self):
        with self.assertRaisesRegex(ValueError, "fixed needs exactly one value"):
            tunespace.normalize({"np": {"mode": "fixed", "values": ["1", "2"]}})
        with self.assertRaisesRegex(ValueError, "fixed needs exactly one value"):
            tunespace.normalize({"np": {"mode": "fixed", "values": []}})
        with self.assertRaisesRegex(ValueError, "Parallel slots: a list needs 1 to 32 values"):
            tunespace.normalize({"np": {"mode": "list", "values": []}})
        with self.assertRaisesRegex(ValueError, "a list needs 1 to 32 values"):
            tunespace.normalize({"np": {"mode": "list", "values": [str(i) for i in range(1, 34)]}})

    def test_numbers_become_strings_and_lists_are_deduplicated_in_order(self):
        out = tunespace.normalize({"ub": {"mode": "list", "values": [256, "512", "256", 1024.0]}})
        self.assertEqual(out, {"ub": {"mode": "list", "values": ["256", "512", "1024"]}})
        self.assertEqual(tunespace.normalize({"ub": {"mode": "fixed", "values": [512]}})["ub"]["values"], ["512"])

    def test_floats_and_ints_keep_their_range_and_format(self):
        out = tunespace.normalize({"pmin": {"mode": "list", "values": [0.5, "0.25", "1"]},
                                   "prio": {"mode": "fixed", "values": ["-1"]},
                                   "ngl": {"mode": "fixed", "values": ["auto"]}})
        self.assertEqual(out["pmin"]["values"], ["0.5", "0.25", "1"])
        self.assertEqual(out["prio"]["values"], ["-1"])
        self.assertEqual(out["ngl"]["values"], ["auto"])
        with self.assertRaisesRegex(ValueError, "Priority: -2 is below -1"):
            tunespace.normalize({"prio": {"mode": "fixed", "values": ["-2"]}})

    def test_off_stores_its_off_value_and_ignores_the_values_sent(self):
        out = tunespace.normalize({"ub": {"mode": "off", "values": ["1"]}, "kvu": {"mode": "off"},
                                   "ngl": {"mode": "off"}, "numa": {"mode": "off", "values": ["isolate"]}})
        self.assertEqual(out["ub"], {"mode": "off", "values": ["512"]})
        self.assertEqual(out["kvu"], {"mode": "off", "values": []})
        self.assertEqual(out["ngl"], {"mode": "off", "values": ["99"]})
        self.assertEqual(out["numa"], {"mode": "off", "values": []})

    def test_spec_allows_only_auto_and_off(self):
        self.assertEqual(tunespace.normalize({"spec": {"mode": "off"}}), {"spec": {"mode": "off", "values": []}})
        with self.assertRaisesRegex(ValueError, "Speculative decoding: only off or auto"):
            tunespace.normalize({"spec": {"mode": "list", "values": ["draft-mtp"]}})

    def test_emit_toggles_and_plain_flags(self):
        self.assertEqual(tunespace.emit("kvu", "on"), [{"flag": "--kv-unified", "value": None}])
        self.assertEqual(tunespace.emit("kvu", "off"), [{"flag": "--no-kv-unified", "value": None}])
        self.assertEqual(tunespace.emit("swa_full", "off"), [])
        self.assertEqual(tunespace.emit("swa_full", "on"), [{"flag": "--swa-full", "value": None}])
        self.assertEqual(tunespace.emit("kv_offload", "off"), [{"flag": "-nkvo", "value": None}])
        self.assertEqual(tunespace.emit("numa", "none"), [])
        self.assertEqual(tunespace.emit("numa", "isolate"), [{"flag": "--numa", "value": "isolate"}])
        self.assertEqual(tunespace.emit("ub", "512"), [{"flag": "-ub", "value": "512"}])
        self.assertEqual(tunespace.emit("ctv", "q8_0"), [{"flag": "-ctv", "value": "q8_0"}])

    def test_kv_pairs_with_both_auto_is_the_ladder(self):
        self.assertEqual(tunespace.kv_pairs({}, tuner_mod.KV_LADDER), tuner_mod.KV_LADDER)

    def test_kv_pairs_sort_most_precise_first(self):
        space = tunespace.normalize({"ctk": {"mode": "list", "values": ["q4_0", "f16", "q8_0"]},
                                     "ctv": {"mode": "fixed", "values": ["q8_0"]}})
        self.assertEqual(tunespace.kv_pairs(space, tuner_mod.KV_LADDER),
                         [("f16", "q8_0"), ("q8_0", "q8_0"), ("q4_0", "q8_0")])
        off_v = tunespace.normalize({"ctv": {"mode": "off"}})
        self.assertEqual(tunespace.kv_pairs(off_v, tuner_mod.KV_LADDER),
                         [("f16", "f16"), ("q8_0", "f16"), ("q4_0", "f16")])

    def test_stripped_flags_cover_every_spelling_of_each_non_auto_dimension(self):
        space = tunespace.normalize({"ub": {"mode": "list", "values": ["512"]}, "kvu": {"mode": "fixed", "values": ["on"]},
                                     "ctk": {"mode": "off"}, "np": {"mode": "auto"}})
        flags = tunespace.stripped_flags(space)
        self.assertTrue({"-ub", "--ubatch-size", "-kvu", "--kv-unified", "-no-kvu", "--no-kv-unified",
                         "-ctk", "--cache-type-k"} <= flags)
        self.assertFalse(flags & {"-np", "--parallel", "-fa"})

    def test_default_grid_count_matches_the_page_estimate(self):
        # tuner.js estimate(): kv * fa * batches * (1 + spec) + 3, with fa 3 and batches 4 (11 with vary_batch).
        self.assertEqual(tunespace.count(self.base()), 15)
        self.assertEqual(tunespace.count(self.base(vary_batch=True)), 1 * 3 * 11 + 3)  # 11 (ub, b) pairs
        mtp = [{"lengths": tuner_mod.MTP_NMAX, "file": False, "block": None}]
        self.assertEqual(tunespace.count(self.base(spec_groups=mtp)), 1 * 3 * 4 * (1 + 5) + 3)

    def test_grid_count_with_two_ub_and_two_np_values(self):
        space = tunespace.normalize({"ub": {"mode": "list", "values": ["256", "1024"]},
                                     "np": {"mode": "list", "values": ["1", "2"]}})
        # 1 KV pair x 3 fa x 2 (ub, b) pairs x 2 np x 1 + 3
        self.assertEqual(tunespace.count(self.base(space=space)), 3 * 2 * 2 + 3)

    def test_quick_count_with_three_thread_values(self):
        space = tunespace.normalize({"threads": {"mode": "list", "values": ["4", "8", "12"]}})
        # 4 fit + 2 flash attention + 4 batch sizes (the page's figure) + 2 more thread values
        self.assertEqual(tunespace.count(self.base(mode="quick", space=space)), 4 + 2 + 4 + 2)

    def test_context_count_with_thread_lists(self):
        space = tunespace.normalize({"threads": {"mode": "list", "values": ["4", "8"]},
                                     "threads_batch": {"mode": "list", "values": ["8", "12", "16"]}})
        self.assertEqual(tunespace.count(self.base(objective="context", space=space)), 21 + 5 + 3)
        self.assertEqual(tunespace.count(self.base(objective="context")), 21 + 5 + 3)

    def test_a_count_above_the_cap_is_refused(self):
        space = tunespace.normalize({"threads": {"mode": "list", "values": [str(i) for i in range(1, 33)]},
                                     "np": {"mode": "list", "values": [str(i) for i in range(1, 33)]}})
        n = tunespace.count(self.base(space=space))
        self.assertEqual(n, 3 * 4 * 32 * 32 + 3)
        self.assertGreater(n, tunespace.MAX_CANDIDATES)
        with self.assertRaisesRegex(ValueError, rf"This search has {n} combinations; narrow it to at most 4096\."):
            tunespace.check(self.base(space=space))
        self.assertEqual(tunespace.check(self.base()), 15)

    def test_a_draft_file_length_is_capped_by_its_block_size(self):
        space = tunespace.normalize({"nmax": {"mode": "list", "values": ["4", "8", "16"]}})
        groups = [{"lengths": ["3", "5", "7", "10"], "file": True, "block": 15}]
        # 4 -> 4, 8 -> 8, 16 -> 15: three distinct lengths
        self.assertEqual(tunespace.count(self.base(space=space, spec_groups=groups)), 1 * 3 * 4 * (1 + 3) + 3)


@unittest.skipUnless(shutil.which("node"), "needs node")
class CountParityTests(unittest.TestCase):
    """The Tuner page's live estimate (web/js/tunespace.js) and the server's count and cap agree."""
    CASES = [
        {},
        {"kv_policy": "all"},
        {"vary_batch": True},
        {"mode": "quick"},
        {"mode": "quick", "kv_policy": "all"},
        {"objective": "context"},
        {"objective": "context", "kv_policy": "all"},
        {"mtp": True},
        {"mtp": True, "drafts": True, "mode": "quick"},
        {"drafts": True, "space": {"nmax": {"mode": "list", "values": ["4", "8", "16"]},
                                   "pmin": {"mode": "list", "values": ["0", "0.5"]},
                                   "ngld": {"mode": "list", "values": ["0", "99"]}}},
        {"drafts": True, "mode": "quick", "space": {"pmin": {"mode": "list", "values": ["0", "0.5"]},
                                                    "ctkd": {"mode": "list", "values": ["f16", "q8_0"]}}},
        {"objective": "context", "drafts": True, "space": {"ngld": {"mode": "list", "values": ["0", "99"]}}},
        {"space": {"ub": {"mode": "list", "values": ["256", "4096"]}, "b": {"mode": "list", "values": ["2048", "4096"]},
                   "np": {"mode": "list", "values": ["1", "2"]}, "kvu": {"mode": "list", "values": ["on", "off"]}}},
        {"kv_policy": "all", "space": {"ctk": {"mode": "list", "values": ["f16", "q8_0"]}, "ctv": {"mode": "fixed", "values": ["q4_0"]},
                                       "fa": {"mode": "list", "values": ["on", "auto"]}}},
        {"mode": "quick", "space": {"fa": {"mode": "off"}, "b": {"mode": "list", "values": ["2048", "4096"]},
                                    "threads": {"mode": "list", "values": ["4", "8"]}}},
        {"objective": "context", "space": {"threads": {"mode": "fixed", "values": ["8"]}}},
        {"objective": "context", "space": {"threads": {"mode": "off"}, "threads_batch": {"mode": "list", "values": ["8", "16"]},
                                           "np": {"mode": "list", "values": ["1", "2", "4"]}}},
        {"mtp": True, "space": {"spec": {"mode": "off"}}},
    ]

    def test_the_page_and_the_server_count_the_same(self):
        draft = {"type": "draft-dspark", "block_size": 15, "path": "/m/d.gguf"}
        js, py = [], []
        for case in self.CASES:
            space = case.get("space", {})
            drafts = [draft] if case.get("drafts") else []
            prm = {"objective": case.get("objective", "decode"), "mode": case.get("mode", "grid"),
                   "kv_policy": case.get("kv_policy", "precise"), "vary_batch": case.get("vary_batch", False),
                   "speculative": True, "drafts": drafts, "space": space}
            js.append({"prm": prm, "model": {"nextn_layers": 1 if case.get("mtp") else 0}})
            groups = ([{"lengths": tuner_mod.MTP_NMAX, "file": False, "block": None}] if case.get("mtp") else []) + \
                [{"lengths": tuner_mod._draft_lengths(d), "file": True, "block": d["block_size"]} for d in drafts]
            py.append(tunespace.count({**prm, "space": tunespace.normalize(space), "spec_groups": groups,
                                       "kv_ladder": tuner_mod.KV_LADDER, "ubatches": tuner_mod.UBATCHES}))
        web = Path(__file__).resolve().parents[2] / "web" / "js" / "tunespace.js"
        script = (f"import({json.dumps(web.as_uri())}).then((m) => console.log(JSON.stringify("
                  f"{json.dumps(js)}.map((c) => m.countCandidates(c.prm, c.model)))))")
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), py)

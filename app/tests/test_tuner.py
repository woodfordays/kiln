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

from kiln import db, tunespace, tuner as tuner_mod
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

import unittest
from unittest import mock

from kiln import fit
from . import ROOT
from .helpers import llm, write_gguf

GIB = 1 << 30


def rows(**kw):
    names = {"ctx": "-c", "ngl": "-ngl", "ctk": "-ctk", "ctv": "-ctv", "ub": "-ub", "np": "-np", "fit": "--fit"}
    return [{"flag": names[k], "value": str(v)} for k, v in kw.items()]


class EstimateTests(unittest.TestCase):
    def setUp(self):
        # 32 layers, 8 KV heads of 128, no sliding window: 32 * 8 * 256 * 2 bytes = 128 KiB per token at f16
        self.path = llm(ROOT / "est.gguf", layers=32, embd=4096, heads=32, kv_heads=8, ctx=32768, vocab=32000)
        (ROOT / "est.gguf").write_bytes((ROOT / "est.gguf").read_bytes() + b"\0" * (4 * GIB // 1024))  # ~4 MiB file
        self.addCleanup(lambda: None)

    def test_kv_cache_scales_with_context_and_type(self):
        a = fit.estimate(self.path, rows(ctx=16384, ctk="f16", ctv="f16", ngl=99, fit="off"))["parts"]["kv"]
        b = fit.estimate(self.path, rows(ctx=32768, ctk="f16", ctv="f16", ngl=99, fit="off"))["parts"]["kv"]
        q = fit.estimate(self.path, rows(ctx=32768, ctk="q8_0", ctv="q8_0", ngl=99, fit="off"))["parts"]["kv"]
        self.assertAlmostEqual(b / a, 2, delta=0.01)
        self.assertAlmostEqual(b, 4096, delta=2)  # 32768 tokens * 128 KiB
        self.assertAlmostEqual(q / b, 34 / 32 / 2, delta=0.01)

    def test_partial_offload_and_cpu_only(self):
        full = fit.estimate(self.path, rows(ctx=8192, ngl=99, fit="off"))
        half = fit.estimate(self.path, rows(ctx=8192, ngl=16, fit="off"))
        cpu = fit.estimate(self.path, rows(ctx=8192, ngl=0, fit="off"))
        self.assertLess(half["gpu_mib"], full["gpu_mib"])
        self.assertEqual(cpu["gpu_mib"], 0)
        self.assertGreater(cpu["ram_mib"], 0)

    def test_sliding_window_layers_keep_only_their_window(self):
        swa = llm(ROOT / "swa.gguf", layers=32, embd=4096, heads=32, kv_heads=8, ctx=131072, vocab=32000,
                  **{"llama.attention.sliding_window": 1024, "llama.attention.sliding_window_pattern": 4})
        r = rows(ctx=131072, ngl=99, fit="off", ub=512)
        with_swa = fit.estimate(swa, r)["parts"]["kv"]
        full = fit.estimate(swa, r + [{"flag": "--swa-full"}])["parts"]["kv"]
        self.assertLess(with_swa, full * 0.30)

    def test_sliding_window_flags_per_layer(self):
        """Flint stores one flag per layer instead of 'every Nth layer is full attention'."""
        # 8 layers x 8 KV heads of 128 at f16 = 32 KiB per token; a window layer keeps ceil((1024 + 512) / 256) * 256 cells
        def kv(name, pattern):
            p = llm(ROOT / f"swa-{name}.gguf", layers=8, embd=4096, heads=32, kv_heads=8, ctx=131072, vocab=32000,
                    **{"llama.attention.sliding_window": 1024, "llama.attention.sliding_window_pattern": pattern})
            return fit.estimate(p, rows(ctx=131072, ngl=99, fit="off", ub=512))["parts"]["kv"]

        self.assertEqual(kv("every4", 4), 1060)                              # 2 full layers of 8
        self.assertEqual(kv("flags", [False, True, True, True] * 2), 1060)   # the same layout, flag by flag
        self.assertEqual(kv("irregular", [False] * 3 + [True] * 5), 1566)    # 3 full layers
        self.assertEqual(kv("windows", [True] * 8), 48)                      # no full layers at all

    def test_hybrid_models_cache_only_their_attention_layers(self):
        hyb = llm(ROOT / "hyb.gguf", arch="qwen35", layers=40, embd=2048, heads=16, kv_heads=2, ctx=65536,
                  **{"qwen35.full_attention_interval": 4, "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256})
        kv = fit.estimate(hyb, rows(ctx=65536, ngl=99, fit="off"))["parts"]["kv"]
        self.assertAlmostEqual(kv, 10 * 2 * 512 * 2 * 65536 / (1 << 20), delta=2)  # 10 attention layers

    def test_signature_changes_with_flags_but_not_disabled_rows(self):
        a = fit.signature("/m.gguf", rows(ctx=4096), "chat")
        self.assertNotEqual(a, fit.signature("/m.gguf", rows(ctx=8192), "chat"))
        self.assertEqual(a, fit.signature("/m.gguf", rows(ctx=4096) + [{"flag": "-fa", "value": "on", "enabled": False}], "chat"))
        self.assertNotEqual(a, fit.signature("/m.gguf", rows(ctx=4096), "embedding"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.path = llm(ROOT / "chk.gguf", layers=32, embd=4096, heads=32, kv_heads=8, ctx=32768, vocab=32000)

    def mem(self, free):
        return {"total_mib": 24000, "free_mib": free, "used_mib": 24000 - free}

    def test_fits_and_does_not_fit(self):
        r = rows(ctx=32768, ngl=99, fit="off")  # about 4.4 GiB
        with mock.patch.object(fit, "gpu_memory", return_value=self.mem(20000)), mock.patch.object(fit, "ram_available_mib", return_value=64000):
            self.assertTrue(fit.check(self.path, r, "chat")["fits"])
        with mock.patch.object(fit, "gpu_memory", return_value=self.mem(3000)), mock.patch.object(fit, "ram_available_mib", return_value=64000):
            out = fit.check(self.path, r, "chat")
            self.assertFalse(out["fits"])
            self.assertIn("free", out["reason"])
            # stopping a model that holds 6 GiB would make room
            self.assertTrue(fit.check(self.path, r, "chat", reclaim_mib=6144)["fits"])
            # but not if a model still loading has yet to take that memory
            self.assertFalse(fit.check(self.path, r, "chat", reclaim_mib=6144, reserved_mib=6000)["fits"])

    def test_unknown_without_gpu_telemetry(self):
        with mock.patch.object(fit, "gpu_memory", return_value=None), mock.patch.object(fit, "ram_available_mib", return_value=64000):
            self.assertIsNone(fit.check(self.path, rows(ctx=4096, ngl=99, fit="off"), "chat")["fits"])

    def test_default_fit_mode_shrinks_to_fit_instead_of_refusing(self):
        with mock.patch.object(fit, "gpu_memory", return_value=self.mem(1500)), mock.patch.object(fit, "ram_available_mib", return_value=64000):
            out = fit.check(self.path, [], "chat")  # no -c, no -ngl, --fit on by default
            self.assertTrue(out["adaptive"])
            self.assertTrue(out["fits"])

    def test_not_enough_system_ram_for_spilled_weights(self):
        with mock.patch.object(fit, "gpu_memory", return_value=self.mem(20000)), mock.patch.object(fit, "ram_available_mib", return_value=100):
            out = fit.check(self.path, rows(ctx=32768, ngl=0, fit="off"), "chat")
            self.assertFalse(out["fits"])
            self.assertIn("RAM", out["reason"])


def pad(path, mib):
    with open(path, "ab") as f:
        f.write(b"\0" * (mib << 20))
    return str(path)


class BreakdownTests(unittest.TestCase):
    def setUp(self):
        self.path = pad(llm(ROOT / "bd.gguf", layers=32, embd=4096, heads=32, kv_heads=8, ctx=32768, vocab=32000), 64)

    def seg(self, out, memory="gpu"):
        return {s["key"]: s["mib"] for s in out[memory]}

    def test_segments_sum_to_the_total_and_kv_matches_the_estimate(self):
        r = rows(ctx=8192, ngl=99, fit="off")
        out, est = fit.breakdown(self.path, r), fit.estimate(self.path, r)
        self.assertEqual([s["key"] for s in out["gpu"]], ["weights", "mtp", "draft", "projector", "kv", "compute", "overhead"])
        self.assertEqual([s["key"] for s in out["ram"]], ["weights", "draft", "kv"])
        self.assertEqual(out["gpu_mib"], sum(self.seg(out).values()))
        self.assertEqual(out["gpu_mib"], est["gpu_mib"])
        self.assertEqual(self.seg(out)["kv"], est["parts"]["kv"])
        self.assertEqual(self.seg(out)["overhead"], fit.PROCESS_MIB)
        self.assertEqual((out["context"], out["np"], out["ctk"], out["measured_mib"], out["actual"]), (8192, 1, "f16", None, None))

    def test_kv_scales_with_context_and_type(self):
        kv = lambda **kw: self.seg(fit.breakdown(self.path, rows(ngl=99, fit="off", **kw)))["kv"]
        self.assertAlmostEqual(kv(ctx=16384) / kv(ctx=8192), 2, delta=0.01)
        self.assertAlmostEqual(kv(ctx=8192, ctk="q8_0", ctv="q8_0") / kv(ctx=8192), 34 / 32 / 2, delta=0.01)

    def test_cpu_only_puts_everything_in_ram(self):
        out = fit.breakdown(self.path, rows(ctx=8192, ngl=0, fit="off"))
        self.assertEqual(out["gpu_mib"], 0)
        self.assertGreater(self.seg(out, "ram")["weights"], 0)
        self.assertEqual(out["ram_mib"], sum(self.seg(out, "ram").values()))

    def test_draft_model_and_its_context(self):
        draft = pad(llm(ROOT / "bd-draft.gguf", layers=8, embd=1024, heads=8, kv_heads=2, ctx=8192, vocab=32000), 16)
        base = rows(ctx=8192, ngl=99, fit="off")
        a = fit.breakdown(self.path, base + [{"flag": "-md", "value": draft}])
        b = fit.breakdown(self.path, base + [{"flag": "-md", "value": draft}, {"flag": "-cd", "value": "32768"}])
        self.assertEqual(self.seg(fit.breakdown(self.path, base))["draft"], 0)
        self.assertGreater(self.seg(a)["draft"], 16)
        self.assertGreater(self.seg(b)["draft"], self.seg(a)["draft"])
        self.assertEqual(a["gpu_mib"], sum(self.seg(a).values()))
        self.assertEqual(a["gpu_mib"] - self.seg(a)["draft"], fit.estimate(self.path, base)["gpu_mib"])

    def test_mtp_head_is_carved_out_of_the_weights(self):
        big = lambda name, **kw: pad(llm(ROOT / name, layers=32, embd=4096, heads=32, kv_heads=8, ctx=32768, vocab=32000, **kw), 320)
        plain = fit.breakdown(big("bd-big.gguf"), rows(ctx=4096, ngl=99, fit="off"))
        mtp_path = big("bd-mtp.gguf", **{"llama.nextn_predict_layers": 1})
        out = fit.breakdown(mtp_path, rows(ctx=4096, ngl=99, fit="off"))
        s, p = self.seg(out), self.seg(plain)
        self.assertGreater(s["mtp"], 0)
        self.assertEqual(s["mtp"] + s["weights"], p["weights"])
        self.assertEqual(out["gpu_mib"], sum(s.values()))

    def test_actual_groups_buffers_by_memory(self):
        buffers = {"Vulkan0 model": 1234.5, "Vulkan0 KV": 512.0, "CPU_Mapped model": 300.0, "Vulkan0 compute": 400.0,
                   "Vulkan1 RS": 10.2, "Vulkan0_Host output": 3.0}
        a = fit.breakdown(self.path, rows(ctx=4096), buffers, 2500)["actual"]
        self.assertEqual(a["gpu"], {"weights": 1234, "kv": 522, "compute": 400})
        self.assertEqual(a["ram"], {"weights": 300, "compute": 3})
        self.assertEqual(a["devices"], {"Vulkan0": {"weights": 1234, "kv": 512, "compute": 400}, "Vulkan1": {"kv": 10}})
        self.assertEqual(fit.breakdown(self.path, rows(ctx=4096), buffers, 2500)["measured_mib"], 2500)

    def test_gpu_devices_from_cards_or_the_combined_totals(self):
        m = 1 << 20
        cards = [{"name": "A", "vram_used": 1000 * m, "vram_total": 8000 * m}, {"name": "B", "vram_used": 0, "vram_total": 4000 * m}]
        with mock.patch.object(fit.gpu.sampler, "live", [{"vram_used": 1000 * m, "vram_total": 12000 * m, "cards": cards}]):
            self.assertEqual(fit.gpu_devices(), [{"name": "A", "total_mib": 8000, "used_mib": 1000, "free_mib": 7000},
                                                 {"name": "B", "total_mib": 4000, "used_mib": 0, "free_mib": 4000}])
            self.assertEqual(len(fit.breakdown(self.path, rows(ctx=4096))["devices"]), 2)
        with mock.patch.object(fit.gpu.sampler, "live", [{"vram_used": 1000 * m, "vram_total": 12000 * m}]):
            self.assertEqual(fit.gpu_devices()[0]["total_mib"], 12000)
        with mock.patch.object(fit.gpu.sampler, "live", []):
            self.assertEqual(fit.gpu_devices(), [])

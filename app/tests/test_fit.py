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

import os
import unittest

from kiln import gguf
from . import ROOT
from .helpers import f32_rows, llm, q8_0_row, write_gguf


class ReaderTests(unittest.TestCase):
    def test_arrays_are_counted_unless_asked_for(self):
        p = write_gguf(ROOT / "a.gguf", {"general.architecture": "x", "pplx_decider.codes": ["A", "B", "C"],
                                         "pplx_decider.token_ids": [32, 33, 34]})
        self.assertEqual(gguf.read_metadata(p)["pplx_decider.codes"], {"array": 3})
        m = gguf.read_metadata(p, arrays=("pplx_decider.codes", "pplx_decider.token_ids"))
        self.assertEqual(m["pplx_decider.codes"], ["A", "B", "C"])
        self.assertEqual(m["pplx_decider.token_ids"], [32, 33, 34])

    def test_suffix_arrays(self):
        p = write_gguf(ROOT / "b.gguf", {"general.architecture": "g", "g.attention.head_count_kv": [0, 4, 0, 4]})
        self.assertEqual(gguf.read_metadata(p, arrays=gguf.ARRAY_KEYS)["g.attention.head_count_kv"], [0, 4, 0, 4])

    def test_per_layer_sliding_window_flags_are_kept(self):
        flags = [False, True, True, False]
        p = write_gguf(ROOT / "swa.gguf", {"general.architecture": "g", "g.attention.sliding_window_pattern": flags})
        self.assertEqual(gguf.read_metadata(p, arrays=gguf.ARRAY_KEYS)["g.attention.sliding_window_pattern"], flags)

    def test_q8_0_rows_match_within_quantization(self):
        rows = [[(i * 7 + j) % 13 / 10 - 0.6 for j in range(64)] for i in range(5)]
        p = write_gguf(ROOT / "q.gguf", {"general.architecture": "x"},
                       [("output.weight", (64, 5), 8, b"".join(q8_0_row(r) for r in rows))])
        got = gguf.tensor_rows(p, "output.weight", [3, 0])
        for want, have in zip((rows[3], rows[0]), got):
            self.assertEqual(len(have), 64)
            self.assertTrue(all(abs(a - b) < 0.006 for a, b in zip(want, have)))

    def test_f32_rows_and_errors(self):
        rows = [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]
        p = write_gguf(ROOT / "f.gguf", {"general.architecture": "x"}, [("output.weight", (4, 2), 0, f32_rows(rows))])
        self.assertEqual(gguf.tensor_rows(p, "output.weight", [1]), [rows[1]])
        with self.assertRaises(ValueError):
            gguf.tensor_rows(p, "output.weight", [2])
        with self.assertRaises(ValueError):
            gguf.tensor_rows(p, "missing.weight", [0])

    def test_kinds_of_model(self):
        dec = write_gguf(ROOT / "d.gguf", {"general.architecture": "qwen35", "pplx_decider.protocol_version": 3,
                                           "pplx_decider.template": "joint-question-inline-readout-v1"})
        self.assertEqual(gguf.summarize(gguf.read_metadata(dec))["decision"]["kind"], "pplx-v3")
        old = write_gguf(ROOT / "d1.gguf", {"general.architecture": "qwen35", "pplx_decider.protocol_version": 1,
                                            "pplx_decider.template": "single"})
        self.assertEqual(gguf.summarize(gguf.read_metadata(old))["decision"]["kind"], "unsupported")
        native = write_gguf(ROOT / "n.gguf", {"general.architecture": "bert", "bert.decision.type": "lev"})
        s = gguf.summarize(gguf.read_metadata(native))
        self.assertEqual((s["decision"]["kind"], s["role_hint"]), ("native", "decision"))
        spark = write_gguf(ROOT / "s.gguf", {"general.architecture": "dflash", "general.name": "X DSpark",
                                             "dflash.has_confidence_head": True, "dflash.block_size": 15})
        self.assertEqual(gguf.summarize(gguf.read_metadata(spark))["draft"], {"type": "draft-dspark", "block_size": 15})
        flash = write_gguf(ROOT / "fl.gguf", {"general.architecture": "dflash", "general.name": "X DFlash"})
        self.assertEqual(gguf.summarize(gguf.read_metadata(flash))["draft"]["type"], "draft-dflash")
        emb = write_gguf(ROOT / "e.gguf", {"general.architecture": "bert", "bert.pooling_type": 2})
        self.assertEqual(gguf.summarize(gguf.read_metadata(emb))["role_hint"], "embedding")
        rr = write_gguf(ROOT / "bge-reranker-v2.gguf", {"general.architecture": "bert", "general.name": "bge-reranker-v2"})
        self.assertEqual(gguf.summarize(gguf.read_metadata(rr), "bge-reranker-v2.gguf")["role_hint"], "rerank")
        self.assertEqual(gguf.summarize(gguf.read_metadata(llm(ROOT / "chat.gguf")))["role_hint"], "chat")

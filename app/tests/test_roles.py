import unittest

from kiln import roles
from . import ROOT
from .helpers import llm, write_gguf


def flags_of(rows):
    return {(r["flag"], r.get("value")) for r in rows}


class RoleTests(unittest.TestCase):
    def test_chat_adds_nothing(self):
        rows = [{"flag": "-ngl", "value": "99"}]
        self.assertEqual(roles.apply("chat", str(ROOT / "none.gguf"), rows)[0], rows)

    def test_embedding_and_rerank(self):
        p = llm(ROOT / "e1.gguf")
        self.assertIn(("--embeddings", None), flags_of(roles.apply("embedding", p, [])[0]))
        self.assertEqual(sum(1 for r in roles.apply("embedding", p, [{"flag": "--embeddings"}])[0]), 1)
        self.assertIn(("--rerank", None), flags_of(roles.apply("rerank", p, [])[0]))

    def test_disabled_rows_do_not_count(self):
        p = llm(ROOT / "e2.gguf")
        rows = roles.apply("embedding", p, [{"flag": "--embeddings", "enabled": False}])[0]
        self.assertEqual(len([r for r in rows if r["flag"] == "--embeddings" and r.get("enabled") is not False]), 1)

    def test_pplx_decider_gets_the_whole_recipe(self):
        p = write_gguf(ROOT / "dec.gguf", {"general.architecture": "qwen35", "pplx_decider.protocol_version": 3,
                                           "pplx_decider.template": "joint-question-inline-readout-v1"})
        rows, notes = roles.apply("decision", p, [{"flag": "--mmproj", "value": "/x/mm.gguf"}, {"flag": "-ngl", "value": "99"}])
        f = flags_of(rows)
        for want in [("--embeddings", None), ("--pooling", "none"), ("--override-kv", "qwen35.attention.causal=bool:false"),
                     ("-c", "4096"), ("-b", "4096"), ("-ub", "4096"), ("-np", "1"), ("-ngl", "99")]:
            self.assertIn(want, f)
        self.assertNotIn("--mmproj", {r["flag"] for r in rows})
        self.assertTrue(any("mmproj" in n for n in notes))

    def test_decider_respects_the_users_context(self):
        p = write_gguf(ROOT / "dec2.gguf", {"general.architecture": "qwen35", "pplx_decider.protocol_version": 3,
                                            "pplx_decider.template": "joint-question-inline-readout-v1"})
        f = flags_of(roles.apply("decision", p, [{"flag": "--ctx-size", "value": "8192"}, {"flag": "-ub", "value": "2048"}])[0])
        self.assertIn(("-b", "8192"), f)
        self.assertIn(("-ub", "2048"), f)
        self.assertNotIn(("-c", "4096"), f)

    def test_unsupported_protocol_is_refused(self):
        p = write_gguf(ROOT / "dec3.gguf", {"general.architecture": "qwen35", "pplx_decider.protocol_version": 1,
                                            "pplx_decider.template": "x"})
        with self.assertRaises(ValueError):
            roles.apply("decision", p, [])

    def test_unknown_role(self):
        with self.assertRaises(ValueError):
            roles.valid("poet")

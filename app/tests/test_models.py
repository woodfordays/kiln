import unittest

from kiln import models
from . import ROOT
from .helpers import llm, write_gguf


class LibraryTests(unittest.TestCase):
    def test_companions_are_attached_to_their_model(self):
        d = ROOT / "models" / "gguf" / "org" / "repo"
        llm(d / "Big-Q4_K_M.gguf", arch="qwen35moe", layers=40, expert_count=8)
        write_gguf(d / "mmproj-Big-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        write_gguf(d / "mmproj-Other-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        write_gguf(d / "dspark-Big.gguf", {"general.architecture": "dflash", "general.name": "Big DSpark",
                                           "dflash.has_confidence_head": True})
        by_name = {m["name"]: m for m in models.scan()}
        big = by_name["Big-Q4_K_M"]
        self.assertFalse(big["is_projector"] or big["is_draft"])
        self.assertEqual([p.split("/")[-1] for p in big["projectors"]][0], "mmproj-Big-f16.gguf")  # best name match first
        self.assertEqual([(x["name"], x["type"]) for x in big["drafts"]], [("dspark-Big", "draft-dspark")])
        self.assertTrue(by_name["dspark-Big"]["is_draft"])
        self.assertTrue(by_name["mmproj-Big-f16"]["is_projector"])
        self.assertTrue(big["kv_cache"])  # the preset editor offers the unified-KV switch only where this holds
        self.assertFalse(by_name["mmproj-Big-f16"]["kv_cache"])

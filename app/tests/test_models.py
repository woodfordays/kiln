import unittest
from pathlib import Path

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

    def test_each_companion_names_the_model_it_belongs_to(self):
        d = ROOT / "models" / "gguf" / "org" / "nest"
        llm(d / "Alpha-8B-Q4_K_M.gguf")
        llm(d / "Beta-3B-Q8_0.gguf")
        write_gguf(d / "mmproj-Beta-3B-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        write_gguf(d / "dspark-Alpha-8B.gguf", {"general.architecture": "dflash", "general.name": "Alpha DSpark",
                                                "dflash.has_confidence_head": True})
        write_gguf(d / "mmproj-F16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        by = {m["name"]: m for m in models.scan() if Path(m["path"]).parent.name == "nest"}
        self.assertIsNone(by["Alpha-8B-Q4_K_M"]["companion_of"])
        self.assertIsNone(by["Beta-3B-Q8_0"]["companion_of"])
        self.assertEqual(by["mmproj-Beta-3B-f16"]["companion_of"], by["Beta-3B-Q8_0"]["path"])
        self.assertEqual(by["dspark-Alpha-8B"]["companion_of"], by["Alpha-8B-Q4_K_M"]["path"])  # "dspark-" ignored
        self.assertIsNone(by["mmproj-F16"]["companion_of"])  # no match and two unrelated models: stays top-level

    def test_a_companion_alone_in_its_folder_belongs_to_nothing(self):
        write_gguf(ROOT / "models" / "gguf" / "org" / "lonely" / "mmproj-Solo-f16.gguf",
                   {"general.architecture": "clip", "general.type": "mmproj"})
        solo = next(m for m in models.scan() if m["name"] == "mmproj-Solo-f16")
        self.assertTrue(solo["is_projector"])
        self.assertIsNone(solo["companion_of"])

    def test_a_no_match_projector_with_two_different_models_stays_top_level(self):
        d = ROOT / "models" / "gguf" / "org" / "unrelated"
        llm(d / "Llama-3-8B-Q4.gguf")
        llm(d / "Qwen2.5-VL-7B-Q4.gguf")
        write_gguf(d / "mmproj-model-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        by = {m["name"]: m for m in models.scan() if Path(m["path"]).parent.name == "unrelated"}
        self.assertIsNone(by["mmproj-model-f16"]["companion_of"])  # never guess between different models

    def test_a_no_match_companion_with_one_model_belongs_to_it(self):
        d = ROOT / "models" / "gguf" / "org" / "single"
        llm(d / "Llama-3-8B-Q4.gguf")
        write_gguf(d / "mmproj-model-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        by = {m["name"]: m for m in models.scan() if Path(m["path"]).parent.name == "single"}
        self.assertEqual(by["mmproj-model-f16"]["companion_of"], by["Llama-3-8B-Q4"]["path"])

    def test_the_stem_drops_only_a_leading_kind_word(self):
        self.assertEqual(models._companion_stem("dspark-Alpha-8B"), "Alpha-8B")
        self.assertEqual(models._companion_stem("MMPROJ_Beta"), "Beta")
        self.assertEqual(models._companion_stem("eagle3-Gamma"), "Gamma")
        self.assertEqual(models._companion_stem("Alpha-draft-Q4"), "Alpha-draft-Q4")
        self.assertEqual(models._companion_stem("mmproj"), "mmproj")  # nothing after it: unchanged

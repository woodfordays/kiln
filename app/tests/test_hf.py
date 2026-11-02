"""Unit tests for parse_ref, the download-box reference parser (kiln/hf.py)."""
import unittest

from kiln import hf


class ParseRefTests(unittest.TestCase):
    def assert_ref(self, text, repo, revision="main", quant="", file=""):
        self.assertEqual(hf.parse_ref(text), {"repo": repo, "revision": revision, "quant": quant, "file": file})

    def test_plain(self):
        self.assert_ref("unsloth/Qwen3-8B-GGUF", "unsloth/Qwen3-8B-GGUF")

    def test_plain_with_quant(self):
        self.assert_ref("unsloth/Qwen3-8B-GGUF:Q4_K_M", "unsloth/Qwen3-8B-GGUF", quant="Q4_K_M")

    def test_hf_co_short_link(self):
        self.assert_ref("hf.co/unsloth/Qwen3-8B-GGUF", "unsloth/Qwen3-8B-GGUF")

    def test_hf_co_short_link_with_quant(self):
        self.assert_ref("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M", "unsloth/Qwen3-8B-GGUF", quant="Q4_K_M")

    def test_hf_co_with_scheme(self):
        self.assert_ref("http://hf.co/unsloth/Qwen3-8B-GGUF:Q8_0", "unsloth/Qwen3-8B-GGUF", quant="Q8_0")

    def test_huggingface_co(self):
        self.assert_ref("https://huggingface.co/unsloth/Qwen3-8B-GGUF", "unsloth/Qwen3-8B-GGUF")

    def test_huggingface_co_with_quant(self):
        self.assert_ref("https://huggingface.co/unsloth/Qwen3-8B-GGUF:Q4_K_M", "unsloth/Qwen3-8B-GGUF", quant="Q4_K_M")

    def test_www_huggingface_co(self):
        self.assert_ref("https://www.huggingface.co/unsloth/Qwen3-8B-GGUF", "unsloth/Qwen3-8B-GGUF")

    def test_tree_link(self):
        self.assert_ref("https://huggingface.co/unsloth/Qwen3-8B-GGUF/tree/main", "unsloth/Qwen3-8B-GGUF", revision="main")

    def test_tree_link_with_quant(self):
        self.assert_ref("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M/tree/dev", "unsloth/Qwen3-8B-GGUF",
                        revision="dev", quant="Q4_K_M")

    def test_blob_link(self):
        self.assert_ref("https://huggingface.co/unsloth/Qwen3-8B-GGUF/blob/main/Qwen3-8B-GGUF-00001-of-00004.gguf",
                        "unsloth/Qwen3-8B-GGUF", revision="main", file="Qwen3-8B-GGUF-00001-of-00004.gguf")

    def test_resolve_link(self):
        self.assert_ref("https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/sub/dir/Qwen3-8B-GGUF.gguf",
                        "unsloth/Qwen3-8B-GGUF", revision="main", file="sub/dir/Qwen3-8B-GGUF.gguf")

    def test_link_without_repo_raises(self):
        with self.assertRaises(ValueError):
            hf.parse_ref("hf.co/unsloth")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            hf.parse_ref("not a ref")


if __name__ == "__main__":
    unittest.main()

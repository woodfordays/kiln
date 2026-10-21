import unittest
from types import SimpleNamespace as NS

from kiln import route


def inst(name, role="chat", state="ready", used=0.0, model="m.gguf", extra=(), run_id=1, draining=False):
    return NS(name=name, role=role, state=state, last_used=used, model_name=model[:-5], model_path=f"/models/{model}",
              draining=draining, run_id=run_id, aliases=lambda: [name, *extra])


def preset(pid, name, role="chat", alias="", model="/models/p.gguf", last=0):
    return {"id": pid, "name": name, "alias": alias, "role": role, "model": model, "last_started": last}


class RouteTests(unittest.TestCase):
    def test_model_name_picks_the_instance(self):
        a, b = inst("Qwen 27B", run_id=1), inst("nomic", role="embedding", model="nomic.gguf", run_id=2)
        r = route.resolve("NOMIC", "/v1/embeddings", [a, b], [], True)
        self.assertEqual((r.kind, r.instance), ("instance", b))

    def test_slug_and_model_file_names_match_after_exact_names(self):
        a = inst("Qwen3.8 27B · 128K", model="Qwen3.8-27B-UD-Q4_K_XL.gguf")
        self.assertIs(route.resolve("qwen3-8-27b-128k", "/v1/chat/completions", [a], [], False).instance, a)
        self.assertIs(route.resolve("Qwen3.8-27B-UD-Q4_K_XL", "/v1/chat/completions", [a], [], False).instance, a)

    def test_unknown_name_falls_back_to_the_default_chat_model(self):
        a, b = inst("A", used=1, run_id=1), inst("B", used=5, run_id=2)
        r = route.resolve("gpt-4o", "/v1/chat/completions", [a, b], [], True)
        self.assertIs(r.instance, b)  # most recently used

    def test_unloaded_preset_is_swapped_in_when_enabled(self):
        a = inst("A")
        p = preset(7, "Big", alias="big")
        r = route.resolve("big", "/v1/chat/completions", [a], [p], True)
        self.assertEqual((r.kind, r.preset["id"]), ("swap", 7))
        r = route.resolve("big", "/v1/chat/completions", [a], [p], False)
        self.assertEqual((r.kind, r.instance), ("instance", a))  # swapping off: answer from what is loaded

    def test_embeddings_go_to_the_embedding_model_even_without_a_name(self):
        chat, emb = inst("C", used=9, run_id=1), inst("E", role="embedding", run_id=2)
        self.assertIs(route.resolve(None, "/v1/embeddings", [chat, emb], [], False).instance, emb)

    def test_role_endpoint_swaps_in_the_newest_preset_of_that_role(self):
        chat = inst("C")
        ps = [preset(1, "e-old", role="embedding", last=1), preset(2, "e-new", role="embedding", last=9), preset(3, "chat2")]
        r = route.resolve(None, "/v1/embeddings", [chat], ps, True)
        self.assertEqual((r.kind, r.preset["id"]), ("swap", 2))

    def test_decision_endpoint_needs_a_decision_model(self):
        r = route.resolve(None, "/v1/systemone", [inst("C")], [], True)
        self.assertEqual(r.kind, "none")
        d = inst("D", role="decision")
        self.assertIs(route.resolve(None, "/v1/systemone", [inst("C"), d], [], True).instance, d)

    def test_draining_and_stopping_instances_are_not_chosen(self):
        a, b = inst("A", draining=True, run_id=1), inst("B", state="stopping", run_id=2)
        self.assertEqual(route.resolve("A", "/v1/chat/completions", [a, b], [], False).kind, "none")

    def test_a_loading_model_is_waited_for_not_swapped_again(self):
        a = inst("A", state="loading")
        r = route.resolve("A", "/v1/chat/completions", [a], [preset(1, "A")], True)
        self.assertEqual((r.kind, r.instance), ("instance", a))

    def test_embeddings_with_only_chat_loaded_still_reach_it(self):
        a = inst("A")
        self.assertIs(route.resolve(None, "/v1/embeddings", [a], [], False).instance, a)

    def test_nothing_loaded(self):
        self.assertEqual(route.resolve(None, "/v1/chat/completions", [], [], True).kind, "none")

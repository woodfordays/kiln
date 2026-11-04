"""Overview numbers worked out from the requests table (kiln/stats.py)."""
import time
import unittest

import httpx

from kiln import api, db, stats


def request(**kw) -> int:
    row = {"ts": time.time() - 60, "endpoint": "/v1/chat/completions", "status": 200, "model": "chat",
           "prompt_n": 0, "cache_n": 0, "predicted_n": 0, **kw}
    return db.insert("requests", row)


class TokensReadTests(unittest.TestCase):
    """Tokens read: every prompt token a model read, prefilled now or reused from the prompt cache."""

    def setUp(self):
        db.execute("DELETE FROM requests")

    def test_prefilled_and_cached_prompt_tokens_both_count(self):
        request(prompt_n=1000, cache_n=3000, predicted_n=200)
        request(prompt_n=500, cache_n=None, predicted_n=50)  # llama-server sent no cache count
        s = stats.summary("1h", None)
        self.assertEqual(s["current"]["read_n"], 4500)
        self.assertEqual(sum(t["read_n"] for t in s["trend"]), 4500)
        self.assertEqual(stats.lifetime()["read_n"], 4500)

    def test_the_previous_period_has_them_too_for_the_change(self):
        request(ts=time.time() - 90 * 60, prompt_n=10, cache_n=5)  # in the hour before the last one
        s = stats.summary("1h", None)
        self.assertEqual((s["current"]["read_n"], s["previous"]["read_n"]), (0, 15))

    def test_each_model_has_its_own(self):
        request(model="chat", prompt_n=100, cache_n=20)
        request(model="embed", prompt_n=7, cache_n=None)
        by = {r["model"]: r for r in stats.models_table("1h")}
        self.assertEqual((by["chat"]["read_n"], by["embed"]["read_n"]), (120, 7))
        self.assertEqual(stats.summary("1h", "embed")["current"]["read_n"], 7)

    def test_nothing_recorded_reads_zero(self):
        s = stats.summary("24h", None)
        self.assertEqual(s["current"]["read_n"], 0)
        self.assertTrue(all(t["read_n"] == 0 for t in s["trend"]))
        self.assertEqual(stats.lifetime()["read_n"], 0)


class RequestLogTests(unittest.IsolatedAsyncioTestCase):
    """The request log (/api/requests) gives each request its tokens read, for the Overview and Requests tables."""

    async def asyncSetUp(self):
        db.execute("DELETE FROM requests")
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://kiln")
        self.addAsyncCleanup(self.http.aclose)

    async def test_each_request_says_how_many_tokens_it_read(self):
        request(prompt_n=815, cache_n=3200)
        request(prompt_n=40, cache_n=None)  # llama-server sent no cache count
        request(status=502, prompt_n=None, cache_n=None, predicted_n=None, error="upstream closed")  # no timings at all
        rows = (await self.http.get("/api/requests")).json()["rows"]
        self.assertEqual([r["read_n"] for r in rows], [None, 40, 4015])  # newest first

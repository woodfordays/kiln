"""Aggregations over the requests table for the dashboard."""
from __future__ import annotations

import math
import time

from . import db, gpu

RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}
NICE_BUCKETS = [60, 120, 300, 600, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400]
SLOTS = 6
MIN_GEN_TOKENS = 8  # shorter generations make noisy per-request rates


def window(rng: str) -> tuple[float, float, bool]:
    """(start, end, has_previous_period)"""
    now = time.time()
    if rng in RANGES:
        return now - RANGES[rng], now, True
    first = db.scalar("SELECT MIN(ts) FROM requests")
    return (first or now - 86400) - 1, now, False


def pick_bucket(span: float, target: int = 40) -> int:
    for b in NICE_BUCKETS:
        if span / b <= target:
            return b
    return NICE_BUCKETS[-1]


def _where(t0: float, t1: float, model: str | None, extra: str = "") -> tuple[str, list]:
    sql = "ts >= ? AND ts < ?"
    params: list = [t0, t1]
    if model:
        sql += " AND model = ?"
        params.append(model)
    if extra:
        sql += " AND " + extra
    return sql, params


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def model_slots() -> dict[str, int]:
    """Stable color slot per model: all-time top six by requests, so a filter never repaints a series."""
    rows = db.rows(
        "SELECT model, COUNT(*) n, MIN(id) first FROM requests WHERE model IS NOT NULL AND model != '' "
        "GROUP BY model ORDER BY n DESC, first ASC"
    )
    top = sorted(rows[:SLOTS], key=lambda r: r["first"])
    return {r["model"]: i + 1 for i, r in enumerate(top)}


def _agg(t0: float, t1: float, model: str | None) -> dict:
    w, p = _where(t0, t1, model)
    r = db.row(
        f"""SELECT COUNT(*) requests,
            SUM(CASE WHEN status >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) errors,
            SUM(COALESCE(prompt_n,0)) prompt_n, SUM(COALESCE(cache_n,0)) cache_n,
            SUM(COALESCE(predicted_n,0)) predicted_n,
            SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_n END) gen_tok,
            SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_ms END) gen_ms,
            SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_n END) pp_tok,
            SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_ms END) pp_ms,
            SUM(draft_n) draft_n, SUM(draft_accepted) draft_accepted,
            AVG(power_w) power_w
        FROM requests WHERE {w}""", p)
    gen = [x["predicted_tps"] for x in db.rows(
        f"SELECT predicted_tps FROM requests WHERE {w} AND predicted_n >= ? AND predicted_tps > 0", [*p, MIN_GEN_TOKENS])]
    ttft = [x["ttft_ms"] for x in db.rows(
        f"SELECT ttft_ms FROM requests WHERE {w} AND ttft_ms IS NOT NULL AND status < 400", p)]
    return {
        "requests": r["requests"] or 0,
        "errors": r["errors"] or 0,
        "prompt_n": r["prompt_n"] or 0,
        "cache_n": r["cache_n"] or 0,
        "predicted_n": r["predicted_n"] or 0,
        "gen_tps": (r["gen_tok"] / r["gen_ms"] * 1000) if r["gen_ms"] else None,
        "pp_tps": (r["pp_tok"] / r["pp_ms"] * 1000) if r["pp_ms"] else None,
        "gen_ms": r["gen_ms"] or 0,
        "pp_ms": r["pp_ms"] or 0,
        "gen_p50": pct(gen, 0.5),
        "gen_p10": pct(gen, 0.1),
        "ttft_p50": pct(ttft, 0.5),
        "ttft_p95": pct(ttft, 0.95),
        "draft_n": r["draft_n"] or 0,
        "draft_rate": (r["draft_accepted"] / r["draft_n"]) if r["draft_n"] else None,
        "power_w": r["power_w"],
    }


def _buckets(t0: float, t1: float, model: str | None, bucket: int) -> tuple[list[dict], list[dict]]:
    start = math.floor(t0 / bucket) * bucket
    n = max(1, math.ceil((t1 - start) / bucket))
    w, p = _where(start, t1, model)
    rows = db.rows(
        f"""SELECT CAST((ts - ?) / ? AS INTEGER) i, model,
            COUNT(*) requests,
            SUM(CASE WHEN status >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) errors,
            SUM(COALESCE(prompt_n,0)) prompt_n, SUM(COALESCE(cache_n,0)) cache_n,
            SUM(COALESCE(predicted_n,0)) predicted_n,
            SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_n END) gen_tok,
            SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_ms END) gen_ms,
            SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_n END) pp_tok,
            SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_ms END) pp_ms
        FROM requests WHERE {w} GROUP BY i, model""", [start, bucket, *p])
    totals = [{"t": start + i * bucket, "requests": 0, "errors": 0, "prompt_n": 0, "cache_n": 0, "predicted_n": 0,
               "_gt": 0, "_gm": 0, "_pt": 0, "_pm": 0} for i in range(n)]
    per_model: dict[str, list] = {}
    for r in rows:
        i = r["i"]
        if not 0 <= i < n:
            continue
        b = totals[i]
        for k in ("requests", "errors", "prompt_n", "cache_n", "predicted_n"):
            b[k] += r[k] or 0
        b["_gt"] += r["gen_tok"] or 0
        b["_gm"] += r["gen_ms"] or 0
        b["_pt"] += r["pp_tok"] or 0
        b["_pm"] += r["pp_ms"] or 0
        m = per_model.setdefault(r["model"] or "", [[None, None] for _ in range(n)])
        m[i] = [
            (r["gen_tok"] / r["gen_ms"] * 1000) if r["gen_ms"] else None,
            (r["pp_tok"] / r["pp_ms"] * 1000) if r["pp_ms"] else None,
        ]
    for b in totals:
        b["gen_tps"] = b["_gt"] / b["_gm"] * 1000 if b["_gm"] else None
        b["pp_tps"] = b["_pt"] / b["_pm"] * 1000 if b["_pm"] else None
        for k in ("_gt", "_gm", "_pt", "_pm"):
            del b[k]
    series = [{"model": k, "gen_tps": [x[0] for x in v], "pp_tps": [x[1] for x in v]} for k, v in per_model.items()]
    return totals, series


def summary(rng: str, model: str | None) -> dict:
    t0, t1, has_prev = window(rng)
    cur = _agg(t0, t1, model)
    prev = _agg(t0 - (t1 - t0), t0, model) if has_prev else None
    trend_bucket = (t1 - t0) / 12
    trend = []
    for i in range(12):
        a = t0 + i * trend_bucket
        w, p = _where(a, a + trend_bucket, model)
        r = db.row(
            f"""SELECT COUNT(*) requests, SUM(COALESCE(predicted_n,0)) predicted_n,
                SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_n END) pp_tok,
                SUM(CASE WHEN prompt_n > 0 AND prompt_ms > 0 THEN prompt_ms END) pp_ms,
                SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_n END) gen_tok,
                SUM(CASE WHEN predicted_n > 0 AND predicted_ms > 0 THEN predicted_ms END) gen_ms
            FROM requests WHERE {w}""", p)
        ttft = [x["ttft_ms"] for x in db.rows(f"SELECT ttft_ms FROM requests WHERE {w} AND ttft_ms IS NOT NULL AND status < 400", p)]
        trend.append({
            "t": a,
            "requests": r["requests"] or 0,
            "predicted_n": r["predicted_n"] or 0,
            "pp_tps": (r["pp_tok"] / r["pp_ms"] * 1000) if r["pp_ms"] else None,
            "gen_tps": (r["gen_tok"] / r["gen_ms"] * 1000) if r["gen_ms"] else None,
            "ttft_p50": pct(ttft, 0.5),
        })
    return {"range": rng, "start": t0, "end": t1, "current": cur, "previous": prev, "trend": trend}


def timeseries(rng: str, model: str | None) -> dict:
    t0, t1, _ = window(rng)
    bucket = pick_bucket(t1 - t0)
    totals, series = _buckets(t0, t1, model, bucket)
    slots = model_slots()
    for s in series:
        s["slot"] = slots.get(s["model"])
    return {"range": rng, "bucket_s": bucket, "start": t0, "end": t1, "buckets": totals, "series": series}


def scatter(rng: str, model: str | None, limit: int = 3000) -> dict:
    t0, t1, _ = window(rng)
    w, p = _where(t0, t1, model)
    rows = db.rows(
        f"""SELECT id, ts, model, COALESCE(cache_n,0) + COALESCE(prompt_n,0) depth,
            predicted_tps, prompt_tps, predicted_n, prompt_n
        FROM requests WHERE {w} AND predicted_n >= ? AND predicted_tps > 0
        ORDER BY ts DESC LIMIT ?""", [*p, MIN_GEN_TOKENS, limit])
    slots = model_slots()
    for r in rows:
        r["slot"] = slots.get(r["model"])
    return {"points": rows, "slots": slots}


def models_table(rng: str) -> list[dict]:
    t0, t1, _ = window(rng)
    slots = model_slots()
    names = [r["model"] for r in db.rows(
        "SELECT model, MAX(ts) last FROM requests WHERE ts >= ? AND ts < ? GROUP BY model ORDER BY last DESC", (t0, t1))]
    out = []
    for name in names:
        a = _agg(t0, t1, name)
        a["model"] = name
        a["slot"] = slots.get(name)
        a["last_ts"] = db.scalar("SELECT MAX(ts) FROM requests WHERE model=?", (name,))
        out.append(a)
    return out


def lifetime() -> dict:
    r = db.row(
        """SELECT COUNT(*) requests, SUM(COALESCE(prompt_n,0)) prompt_n, SUM(COALESCE(cache_n,0)) cache_n,
            SUM(COALESCE(predicted_n,0)) predicted_n, SUM(COALESCE(predicted_ms,0)) gen_ms,
            SUM(COALESCE(prompt_ms,0)) pp_ms, MIN(ts) first_ts, COUNT(DISTINCT model) models,
            SUM(CASE WHEN status >= 400 OR error IS NOT NULL THEN 1 ELSE 0 END) errors
        FROM requests""")
    now = time.time()
    runs = db.row(
        """SELECT COUNT(*) runs, SUM(CASE WHEN ready_at IS NOT NULL THEN 1 ELSE 0 END) loads,
            AVG(load_ms) load_ms,
            SUM(CASE WHEN ready_at IS NOT NULL THEN COALESCE(stopped_at, ?) - ready_at ELSE 0 END) uptime_s
        FROM runs""", (now,))
    dl = db.row("SELECT COUNT(*) n, SUM(total_bytes) bytes FROM downloads WHERE status='done'")
    return {**{k: (v or 0) if k != "first_ts" else v for k, v in r.items()},
            "runs": runs["runs"] or 0, "loads": runs["loads"] or 0, "avg_load_ms": runs["load_ms"],
            "uptime_s": runs["uptime_s"] or 0, "downloads": dl["n"] or 0, "downloaded_bytes": dl["bytes"] or 0}


def gpu_history(rng: str) -> dict:
    t0, t1, _ = window(rng)
    bucket = max(60, pick_bucket(t1 - t0, target=240))
    rows = db.rows(
        """SELECT CAST(ts / ? AS INTEGER) * ? t, AVG(busy) busy, AVG(vram_used) vram_used, MAX(vram_total) vram_total,
            AVG(power_w) power_w, AVG(temp_c) temp_c FROM gpu_minutes WHERE ts >= ? AND ts < ?
        GROUP BY t ORDER BY t""", (bucket, bucket, t0, t1))
    return {"bucket_s": bucket, "points": rows, "name": gpu.sampler.name, "available": gpu.sampler.available}


def purge(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = time.time() - retention_days * 86400
    n = db.scalar("SELECT COUNT(*) FROM requests WHERE ts < ?", (cutoff,))
    db.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
    db.execute("DELETE FROM gpu_minutes WHERE ts < ?", (cutoff,))
    return n or 0


CSV_COLUMNS = ["id", "ts", "model", "preset_name", "endpoint", "status", "stream", "client", "prompt_n", "cache_n",
               "predicted_n", "prompt_ms", "predicted_ms", "prompt_tps", "predicted_tps", "ttft_ms", "ttfb_ms",
               "total_ms", "draft_n", "draft_accepted", "finish_reason", "power_w", "error", "run_id", "swap_ms"]


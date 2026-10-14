"""SQLite storage. One connection, used from the event-loop thread only."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable

from . import config

SCHEMA = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS presets (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        model       TEXT NOT NULL DEFAULT '',
        flags       TEXT NOT NULL DEFAULT '[]',
        env         TEXT NOT NULL DEFAULT '{}',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS runs (
        id          INTEGER PRIMARY KEY,
        preset_id   INTEGER,
        preset_name TEXT,
        model       TEXT NOT NULL,
        argv        TEXT NOT NULL,
        env         TEXT NOT NULL DEFAULT '{}',
        started_at  REAL NOT NULL,
        ready_at    REAL,
        stopped_at  REAL,
        load_ms     REAL,
        n_ctx       INTEGER,
        exit_code   INTEGER,
        status      TEXT NOT NULL,
        error       TEXT
    );
    CREATE TABLE IF NOT EXISTS requests (
        id             INTEGER PRIMARY KEY,
        ts             REAL NOT NULL,
        run_id         INTEGER,
        preset_id      INTEGER,
        preset_name    TEXT,
        model          TEXT,
        endpoint       TEXT NOT NULL,
        status         INTEGER NOT NULL,
        stream         INTEGER NOT NULL DEFAULT 0,
        client         TEXT,
        prompt_n       INTEGER,
        cache_n        INTEGER,
        predicted_n    INTEGER,
        prompt_ms      REAL,
        predicted_ms   REAL,
        prompt_tps     REAL,
        predicted_tps  REAL,
        ttfb_ms        REAL,
        ttft_ms        REAL,
        total_ms       REAL,
        draft_n        INTEGER,
        draft_accepted INTEGER,
        finish_reason  TEXT,
        power_w        REAL,
        error          TEXT
    );
    CREATE INDEX IF NOT EXISTS requests_ts ON requests(ts);
    CREATE INDEX IF NOT EXISTS requests_model_ts ON requests(model, ts);
    CREATE TABLE IF NOT EXISTS gpu_minutes (
        ts         INTEGER PRIMARY KEY,
        busy       REAL,
        vram_used  INTEGER,
        vram_total INTEGER,
        power_w    REAL,
        temp_c     REAL,
        sclk_mhz   REAL
    );
    CREATE TABLE IF NOT EXISTS downloads (
        id          INTEGER PRIMARY KEY,
        repo        TEXT NOT NULL,
        revision    TEXT NOT NULL,
        variant     TEXT NOT NULL,
        files       TEXT NOT NULL,
        dest        TEXT NOT NULL,
        total_bytes INTEGER NOT NULL DEFAULT 0,
        done_bytes  INTEGER NOT NULL DEFAULT 0,
        status      TEXT NOT NULL,
        error       TEXT,
        created_at  REAL NOT NULL,
        finished_at REAL
    );
    """,
    # v2
    """
    CREATE TABLE IF NOT EXISTS tunes (
        id          INTEGER PRIMARY KEY,
        created_at  REAL NOT NULL,
        finished_at REAL,
        status      TEXT NOT NULL,
        model       TEXT NOT NULL,
        params      TEXT NOT NULL,
        trials      TEXT NOT NULL DEFAULT '[]',
        best        TEXT,
        preset_id   INTEGER,
        error       TEXT
    );
    """,
]

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    assert _conn is not None, "db.init() not called"
    return _conn


def init() -> None:
    global _conn
    config.ensure_dirs()
    _conn = sqlite3.connect(config.DB_PATH, isolation_level=None, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    version = _conn.execute("PRAGMA user_version").fetchone()[0]
    for i, ddl in enumerate(SCHEMA[version:], start=version + 1):
        _conn.executescript("BEGIN;" + ddl + f"PRAGMA user_version={i};COMMIT;")
    # A run left 'running' means the container died under it.
    _conn.execute(
        "UPDATE runs SET status='lost', stopped_at=COALESCE(stopped_at, ?) "
        "WHERE status IN ('starting','loading','ready','stopping')",
        (time.time(),),
    )
    _conn.execute("UPDATE downloads SET status='queued' WHERE status='downloading'")
    _conn.execute("UPDATE tunes SET status='interrupted', finished_at=? WHERE status='running'", (time.time(),))


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def row(sql: str, params: Iterable[Any] = ()) -> dict | None:
    r = conn().execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    r = conn().execute(sql, tuple(params)).fetchone()
    return r[0] if r else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    cur = conn().execute(sql, tuple(params))
    return cur.lastrowid


def insert(table: str, row: dict) -> int:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row.values())


def update(table: str, row_id: int, fields: dict) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "hf_token": "",
    "autostart_preset_id": None,
    "retention_days": 0,  # 0 = keep forever
}


def get_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    for r in rows("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out


def set_settings(values: dict) -> dict:
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v)),
            )
    return get_settings()

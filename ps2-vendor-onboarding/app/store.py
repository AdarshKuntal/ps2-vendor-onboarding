"""SQLite persistence: runs (= submissions), their stage events (the audit trail), and the vendor master.

The business registry and the watchlist are read-only reference data (seed JSON = stand-ins for a
registry API and consolidated sanctions lists). The vendor master is mutable: approved submissions
create vendor records in it, which is what lets later submissions be caught as duplicates.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  case_id TEXT, previous_run TEXT, superseded_by TEXT,
  source TEXT, scenario TEXT, engine TEXT,
  status TEXT NOT NULL,            -- running | completed | failed
  decision TEXT,                   -- APPROVED | PENDING | REJECTED
  final_status TEXT,               -- after human review / resubmission
  pending_with TEXT,               -- vendor | internal
  route TEXT, legal_name TEXT, country TEXT, tax_id TEXT, category TEXT,
  risk_score INTEGER, risk_tier TEXT, vendor_id TEXT,
  duration_ms INTEGER, headline TEXT, fingerprint TEXT,
  submission_json TEXT, documents_json TEXT, reasons_json TEXT, result_json TEXT, review_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL, ts REAL NOT NULL, type TEXT NOT NULL, stage TEXT, payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS vendors (
  vendor_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, source_run TEXT, created_at REAL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init(reset: bool = False) -> None:
    config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as c:
        if reset:
            c.executescript("DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS vendors;")
            for f in config.FILES_DIR.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0:
            for v in json.loads((config.SEED_DIR / "vendor_master.json").read_text(encoding="utf-8")):
                c.execute("INSERT INTO vendors VALUES (?,?,?,?)", (v["vendor_id"], json.dumps(v), "seed", time.time()))


# ----------------------------------------------------------------- reference data
def registry() -> list[dict]:
    return json.loads((config.SEED_DIR / "registry.json").read_text(encoding="utf-8"))


def watchlist() -> list[dict]:
    return json.loads((config.SEED_DIR / "watchlist.json").read_text(encoding="utf-8"))


def vendors() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM vendors ORDER BY vendor_id").fetchall()
    return [{**json.loads(r["data_json"]), "source_run": r["source_run"]} for r in rows]


def create_vendor(data: dict, run_id: str) -> str:
    with _lock, _conn() as c:
        ids = [int(r[0].split("-")[1]) for r in c.execute("SELECT vendor_id FROM vendors").fetchall()]
        vid = f"V-{max(ids + [1000]) + 1}"
        data = {**data, "vendor_id": vid}
        c.execute("INSERT INTO vendors VALUES (?,?,?,?)", (vid, json.dumps(data), run_id, time.time()))
    return vid


# ----------------------------------------------------------------- runs & events
_JSON_COLS = ("submission", "documents", "reasons", "result", "review")


def create_run(source: str, scenario: str | None, engine: str, submission: dict, documents: list[dict]) -> str:
    run_id = time.strftime("S%m%d-") + uuid.uuid4().hex[:6].upper()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO runs (id, created_at, case_id, source, scenario, engine, status, legal_name, country, tax_id,"
            " category, submission_json, documents_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, time.time(), run_id, source, scenario, engine, "running", submission.get("legal_name"),
             submission.get("country"), submission.get("tax_id"), submission.get("category"),
             json.dumps(submission), json.dumps(documents)),
        )
    return run_id


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    for k in _JSON_COLS:
        if k in fields:
            fields[f"{k}_json"] = json.dumps(fields.pop(k), default=str)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def add_event(run_id: str, type_: str, stage: str | None = None, payload: dict | None = None) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO events (run_id, ts, type, stage, payload_json) VALUES (?,?,?,?,?)",
                  (run_id, time.time(), type_, stage, json.dumps(payload or {}, default=str)))


def events_after(run_id: str, after_id: int) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id", (run_id, after_id)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload_json"] or "{}")} for r in rows]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in _JSON_COLS:
        raw = d.pop(f"{k}_json", None)
        d[k] = json.loads(raw) if raw else None
    return d


def get_run(run_id: str) -> dict | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row(r) if r else None


def list_runs(limit: int = 300) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = _row(r)
        d.pop("result", None)
        d.pop("submission", None)
        out.append(d)
    return out


def open_cases(exclude_run: str) -> list[dict]:
    """Completed submissions still pending (not reviewed, not superseded): candidates for a resubmission link."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE id != ? AND status = 'completed' AND decision = 'PENDING'"
            " AND final_status IS NULL AND superseded_by IS NULL ORDER BY created_at DESC", (exclude_run,)).fetchall()
    return [_row(r) for r in rows]

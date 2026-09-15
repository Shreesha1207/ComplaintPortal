# -*- coding: utf-8 -*-
"""
SQLite persistence.

SQLite because a Digital Public Good has to be forkable and runnable by a
ministry with one laptop and no cloud budget, not only by whoever can stand up
a managed Postgres. The schema is plain SQL and the access layer is thin, so
moving to Postgres for national volume is a connection-string change plus a
migration, not a rewrite.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("AGORA_DB", Path(__file__).resolve().parent.parent / "agora.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id                   TEXT PRIMARY KEY,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  country              TEXT NOT NULL,
  region_code          TEXT NOT NULL,
  district_code        TEXT NOT NULL,
  channel              TEXT NOT NULL,
  language             TEXT NOT NULL,
  language_confidence  REAL NOT NULL,
  text_original        TEXT NOT NULL,
  text_redacted        TEXT NOT NULL,
  text_en              TEXT NOT NULL,
  sector               TEXT NOT NULL,
  sector_confidence    REAL NOT NULL,
  urgency              TEXT NOT NULL,
  urgency_score        REAL NOT NULL,
  affected_population  INTEGER NOT NULL,
  ai_confidence        REAL NOT NULL,
  ai_engine            TEXT NOT NULL,
  ai_rationale         TEXT NOT NULL,
  pii_types            TEXT NOT NULL DEFAULT '[]',
  entities             TEXT NOT NULL DEFAULT '[]',
  status               TEXT NOT NULL DEFAULT 'new',
  reviewer_note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_req_country  ON requests(country);
CREATE INDEX IF NOT EXISTS idx_req_district ON requests(district_code);
CREATE INDEX IF NOT EXISTS idx_req_sector   ON requests(sector);
CREATE INDEX IF NOT EXISTS idx_req_status   ON requests(status);

-- Append-only. Every AI decision and every human override is recorded, so a
-- recommendation can be reconstructed as of any past date and a disputed
-- classification can be traced to whoever changed it.
CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_req ON audit_log(request_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """One connection per thread; uvicorn's worker pool is threaded."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def audit(request_id: str, actor: str, action: str, detail: dict | None = None) -> None:
    conn = connect()
    conn.execute("INSERT INTO audit_log (at, request_id, actor, action, detail) "
                 "VALUES (?,?,?,?,?)",
                 (now(), request_id, actor, action, json.dumps(detail or {}, ensure_ascii=False)))
    conn.commit()


def insert_request(rec: dict, actor: str = "system") -> str:
    rec = dict(rec)
    rec.setdefault("id", f"REQ-{uuid.uuid4().hex[:12].upper()}")
    rec.setdefault("created_at", now())
    rec["updated_at"] = rec.get("created_at")
    rec["pii_types"] = json.dumps(rec.get("pii_types", []), ensure_ascii=False)
    rec["entities"] = json.dumps(rec.get("entities", []), ensure_ascii=False)

    cols = ["id", "created_at", "updated_at", "country", "region_code", "district_code",
            "channel", "language", "language_confidence", "text_original", "text_redacted",
            "text_en", "sector", "sector_confidence", "urgency", "urgency_score",
            "affected_population", "ai_confidence", "ai_engine", "ai_rationale",
            "pii_types", "entities", "status", "reviewer_note"]
    conn = connect()
    conn.execute(f"INSERT INTO requests ({','.join(cols)}) "
                 f"VALUES ({','.join('?' * len(cols))})",
                 [rec.get(c) for c in cols])
    conn.commit()
    audit(rec["id"], actor, "created",
          {"engine": rec.get("ai_engine"), "sector": rec.get("sector"),
           "confidence": rec.get("ai_confidence"), "status": rec.get("status")})
    return rec["id"]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["pii_types"] = json.loads(d.get("pii_types") or "[]")
    d["entities"] = json.loads(d.get("entities") or "[]")
    return d


def list_requests(country: str | None = None, status: str | None = None,
                  district: str | None = None, sector: str | None = None,
                  limit: int = 500, offset: int = 0) -> list[dict]:
    q = "SELECT * FROM requests WHERE 1=1"
    args: list = []
    for col, val in (("country", country), ("status", status),
                     ("district_code", district), ("sector", sector)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [_row_to_dict(r) for r in connect().execute(q, args)]


def count_requests(country: str | None = None, status: str | None = None) -> int:
    q, args = "SELECT COUNT(*) c FROM requests WHERE 1=1", []
    for col, val in (("country", country), ("status", status)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    return connect().execute(q, args).fetchone()["c"]


def get_request(rid: str) -> dict | None:
    row = connect().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    return _row_to_dict(row) if row else None


def update_review(rid: str, patch: dict, reviewer: str) -> dict | None:
    before = get_request(rid)
    if before is None:
        return None
    fields = {k: v for k, v in patch.items()
              if k in {"status", "sector", "urgency", "affected_population", "reviewer_note"}
              and v is not None}
    if not fields:
        return before
    if "urgency" in fields:
        fields["urgency_score"] = {"critical": 1.0, "high": 0.7,
                                   "medium": 0.4, "low": 0.15}[fields["urgency"]]
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    conn = connect()
    conn.execute(f"UPDATE requests SET {sets} WHERE id=?", [*fields.values(), rid])
    conn.commit()
    audit(rid, reviewer, "reviewed",
          {"changed": {k: [before.get(k), v] for k, v in fields.items()
                       if k != "updated_at" and before.get(k) != v}})
    return get_request(rid)


def get_audit(rid: str) -> list[dict]:
    rows = connect().execute("SELECT * FROM audit_log WHERE request_id=? ORDER BY id", (rid,))
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


def counts() -> dict:
    conn = connect()
    total = conn.execute("SELECT COUNT(*) c FROM requests").fetchone()["c"]
    by_status = {r["status"]: r["c"] for r in
                 conn.execute("SELECT status, COUNT(*) c FROM requests GROUP BY status")}
    by_country = {r["country"]: r["c"] for r in
                  conn.execute("SELECT country, COUNT(*) c FROM requests GROUP BY country")}
    by_language = {r["language"]: r["c"] for r in
                   conn.execute("SELECT language, COUNT(*) c FROM requests "
                                "GROUP BY language ORDER BY c DESC")}
    by_channel = {r["channel"]: r["c"] for r in
                  conn.execute("SELECT channel, COUNT(*) c FROM requests GROUP BY channel")}
    return {"total": total, "by_status": by_status, "by_country": by_country,
            "by_language": by_language, "by_channel": by_channel}

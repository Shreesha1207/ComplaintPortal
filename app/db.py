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

# The database lives in `var/`, not the project root.
#
# It used to be written as `data.db` beside README.md and run.sh, where it is
# the only non-source file in the listing and reads like something that belongs
# in the repository. It never was -- `.gitignore` has excluded `*.db` since the
# first commit that carried any code, and no database file has ever been
# committed on any branch, which was checked rather than assumed -- but a binary
# sitting among the source invites someone to commit it, and invites everyone
# else to wonder whether it already is.
#
# `var/` is the conventional place for state a program writes about itself, and
# it keeps the `-wal` and `-shm` sidecars together with it rather than
# scattering three files across the root. The directory is ignored whole.
#
# APP_DB still wins, absolutely: a deployment that wants the database on another
# volume sets it and nothing here interferes. Tests set it to a temporary file.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "var" / "data.db"

# APP_DB=:memory: runs the whole platform with nothing on disk at all.
#
# This is the deployment mode, and it exists because of a real constraint rather
# than a preference. Most places you can cheaply host a small Python service
# give you a filesystem that is read-only, or one that is wiped on every deploy
# and not shared between instances. A database file needs none of those things
# to be true, so it is the single thing that makes this app annoying to host.
#
# Worth being blunt about the alternative, because it is the obvious idea and it
# does not work: writing requests to a JSON file instead has *exactly* the same
# problem. It is still a file, it still needs a writable disk that survives a
# restart, and it additionally gives up the append-only audit log, the indexes
# the analytics run on, and safe concurrent writes. It would be more work and
# strictly worse.
#
# So the diskless mode keeps SQLite and every line of SQL above it, and moves
# the database into the process. Seeding on startup fills it, submissions work
# normally, the statistics and the map are fully live -- and nothing is written
# anywhere. The cost, stated plainly wherever it is offered: requests submitted
# after startup live until the process restarts. For a demonstration deployment
# of a platform whose stored corpus is synthetic anyway, that is the right
# trade. For a real deployment, give it a disk or a Postgres.
MEMORY = ":memory:"


def _configured_db() -> str:
    raw = (os.getenv("APP_DB") or "").strip()
    if raw.lower() in (":memory:", "memory"):
        return MEMORY
    return raw or str(DEFAULT_DB_PATH)


DB_PATH = _configured_db()
IN_MEMORY = DB_PATH == MEMORY

# A plain ":memory:" database is private to the connection that opened it, and
# this module opens one per thread, so every uvicorn worker would get its own
# empty database and a citizen's request would vanish between two requests of
# the same session. The shared-cache URI is what makes one in-memory database
# that all the threads see.
#
# It lives only as long as at least one connection to it is open, so a keepalive
# connection is held for the life of the process. Without it the database is
# destroyed the moment a worker thread happens to finish.
_MEMORY_URI = "file:complaintportal?mode=memory&cache=shared"
_keepalive: sqlite3.Connection | None = None
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
  text_local           TEXT NOT NULL DEFAULT '',
  translated           INTEGER NOT NULL DEFAULT 0,
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

-- Staff accounts. Citizens have no account at all: intake is deliberately
-- anonymous, because requiring a login to report a broken handpump would
-- silence exactly the people this platform exists to hear.
CREATE TABLE IF NOT EXISTS users (
  username       TEXT PRIMARY KEY,
  role           TEXT NOT NULL CHECK (role IN ('admin','reviewer')),
  password_hash  TEXT NOT NULL,      -- pbkdf2_sha256$iterations$salt$hash
  display_name   TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  last_login_at  TEXT,
  failed_logins  INTEGER NOT NULL DEFAULT 0,
  locked_until   TEXT
);

-- Server-side sessions. The cookie carries an opaque token; only its SHA-256
-- is stored, so a database leak does not hand over live sessions.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT PRIMARY KEY,
  username    TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open() -> sqlite3.Connection:
    """Open one connection to the configured database, on disk or in memory."""
    if IN_MEMORY:
        conn = sqlite3.connect(_MEMORY_URI, uri=True, check_same_thread=False)
    else:
        # sqlite3 creates the file but not the directory above it, and the
        # default path has one. Created here rather than at import, so importing
        # app.db never writes to disk as a side effect.
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL is a file-based journal and means nothing to an in-memory database;
    # SQLite silently keeps "memory" journalling there, so asking for it is a
    # no-op rather than an error, but there is no reason to ask.
    if not IN_MEMORY:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Shared-cache in-memory databases lock at the table level, so two threads
    # writing at once can meet SQLITE_LOCKED rather than waiting. A busy timeout
    # turns that into a short wait, which is what the caller expects.
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def connect() -> sqlite3.Connection:
    """One connection per thread; uvicorn's worker pool is threaded."""
    global _keepalive
    conn = getattr(_local, "conn", None)
    if conn is None:
        if IN_MEMORY and _keepalive is None:
            # Opened before any per-thread connection, and never closed: it is
            # what keeps the shared in-memory database alive between requests.
            _keepalive = _open()
        conn = _open()
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    # Additive migration for databases created before translation existed.
    # SQLite has no "ADD COLUMN IF NOT EXISTS", so check the table first.
    have = {r["name"] for r in conn.execute("PRAGMA table_info(requests)")}
    for col, ddl in (("text_local", "TEXT NOT NULL DEFAULT ''"),
                     ("translated", "INTEGER NOT NULL DEFAULT 0")):
        if col not in have:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {ddl}")
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
    rec.setdefault("text_local", "")
    rec.setdefault("translated", 0)
    rec["pii_types"] = json.dumps(rec.get("pii_types", []), ensure_ascii=False)
    rec["entities"] = json.dumps(rec.get("entities", []), ensure_ascii=False)

    cols = ["id", "created_at", "updated_at", "country", "region_code", "district_code",
            "channel", "language", "language_confidence", "text_original", "text_redacted",
            "text_en", "text_local", "translated",
            "sector", "sector_confidence", "urgency", "urgency_score",
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


def claim_first_user(username: str, role: str, password_hash: str,
                     display_name: str) -> bool:
    """Insert a user only while the table is still empty. True if this call won.

    The emptiness test and the insert are one statement on purpose. Doing them
    as two -- ask whether any account exists, then create one -- leaves a
    window between the question and the answer, and on a fresh install that
    window is wide: password hashing sits in the middle of it and takes
    hundreds of milliseconds. Concurrent callers all pass the check and all
    insert, so a fresh deployment can be handed several administrators, none
    of whom had to sign in. SQLite evaluates the NOT EXISTS under the write
    lock it already holds for the insert, so the loser here inserts nothing
    and finds out by the row count.
    """
    conn = connect()
    cur = conn.execute(
        "INSERT INTO users (username, role, password_hash, display_name, created_at) "
        "SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM users)",
        (username, role, password_hash, display_name, now()))
    conn.commit()
    return cur.rowcount == 1


def set_translation(rid: str, text_en: str, text_local: str, actor: str) -> dict | None:
    """Store a real translation over the offline gloss."""
    if get_request(rid) is None:
        return None
    conn = connect()
    conn.execute("UPDATE requests SET text_en=?, text_local=?, translated=1, updated_at=? "
                 "WHERE id=?", (text_en, text_local, now(), rid))
    conn.commit()
    audit(rid, actor, "translated", {"chars_en": len(text_en), "chars_local": len(text_local)})
    return get_request(rid)


def untranslated(country: str | None = None, limit: int = 100) -> list[dict]:
    q = "SELECT * FROM requests WHERE translated=0"
    args: list = []
    if country:
        q += " AND country=?"
        args.append(country)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [_row_to_dict(r) for r in connect().execute(q, args)]


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

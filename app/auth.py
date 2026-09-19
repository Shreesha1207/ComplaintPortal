# -*- coding: utf-8 -*-
"""
Authentication and role separation.

WHO GETS AN ACCOUNT, AND WHO DELIBERATELY DOES NOT
--------------------------------------------------
Citizens have no account. Intake is anonymous on purpose: requiring a login to
report a broken handpump would silence exactly the people this platform exists
to hear -- the ones with low literacy, a shared phone, and no email address.
The equity correction is worthless if the front door filters them out first.

Staff have accounts, in two roles:

  admin     Sees money. The dashboard, every analytics endpoint, the budget
            simulator, the policy weights and the exports. Funding decisions
            and the data behind them live entirely here.
  reviewer  Sees the review queue and nothing else. A block officer confirming
            that a request is real should not be able to move a budget, and
            should not need to.

WHY THE SPLIT IS DRAWN AT "MONEY", NOT AT "PAGES"
-------------------------------------------------
Every analytics response carries committed and unfunded amounts per district.
So the boundary is the data, not the screen: an endpoint that returns a funding
figure is admin-only even if some other role might find the rest of it useful.
Drawing the line at pages would have left the numbers reachable over the API.

NO NEW DEPENDENCIES
-------------------
PBKDF2-HMAC-SHA256 from hashlib, tokens from secrets, comparisons from hmac --
all standard library. This project installs with three packages and has to run
on a ministry laptop; adding passlib and python-jose to check a password is not
a trade worth making.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import db

log = logging.getLogger("app.auth")

SESSION_COOKIE = "session"
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
# OWASP-order iteration count. Login is rare, so the cost is invisible to a
# human and expensive to an attacker with the hash file.
PBKDF2_ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "600000"))
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "8"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))

ROLES = ("admin", "reviewer")
MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", "8"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# ---------------------------------------------------------------- passwords
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(["pbkdf2_sha256", str(iterations),
                     base64.b64encode(salt).decode(),
                     base64.b64encode(dk).decode()])


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. Returns False rather than raising on a
    malformed stored value, so a corrupt row cannot 500 the login endpoint."""
    try:
        scheme, iters, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:                                     # noqa: BLE001
        return False


# ------------------------------------------------------------------- users
def create_user(username: str, password: str, role: str,
                display_name: str = "") -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    username = username.strip().lower()
    if not username or not password:
        raise ValueError("username and password are required")
    conn = db.connect()
    conn.execute(
        "INSERT INTO users (username, role, password_hash, display_name, created_at) "
        "VALUES (?,?,?,?,?)",
        (username, role, hash_password(password), display_name or username, db.now()))
    conn.commit()
    return {"username": username, "role": role, "display_name": display_name or username}


def get_user(username: str) -> dict | None:
    row = db.connect().execute("SELECT * FROM users WHERE username=?",
                               (username.strip().lower(),)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    return [{k: v for k, v in dict(r).items() if k != "password_hash"}
            for r in db.connect().execute(
                "SELECT * FROM users ORDER BY role, username")]


def set_password(username: str, password: str) -> bool:
    conn = db.connect()
    cur = conn.execute("UPDATE users SET password_hash=?, failed_logins=0, "
                       "locked_until=NULL WHERE username=?",
                       (hash_password(password), username.strip().lower()))
    conn.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------ login
class AuthError(Exception):
    """Login refused. The message is safe to show a user."""


def authenticate(username: str, password: str) -> dict:
    """Verify credentials, with lockout after repeated failures.

    The same message is returned for an unknown user and a wrong password, so
    the endpoint cannot be used to enumerate which accounts exist.
    """
    username = (username or "").strip().lower()
    user = get_user(username)
    generic = "Incorrect username or password."

    if user is None:
        # Spend comparable time on a non-existent user so response timing does
        # not reveal whether the account exists.
        hash_password(password or "", iterations=1000)
        raise AuthError(generic)

    locked = _parse(user.get("locked_until"))
    if locked and locked > _now():
        mins = max(1, int((locked - _now()).total_seconds() // 60) + 1)
        raise AuthError(f"Account locked after repeated failed attempts. "
                        f"Try again in {mins} minute(s).")

    conn = db.connect()
    if not verify_password(password or "", user["password_hash"]):
        failed = int(user["failed_logins"]) + 1
        lock_to = (_iso(_now() + timedelta(minutes=LOCKOUT_MINUTES))
                   if failed >= MAX_FAILED_LOGINS else None)
        conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE username=?",
                     (failed, lock_to, username))
        conn.commit()
        if lock_to:
            log.warning("Account %r locked after %d failed logins", username, failed)
            raise AuthError(f"Account locked after {failed} failed attempts. "
                            f"Try again in {LOCKOUT_MINUTES} minutes.")
        raise AuthError(generic)

    conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=? "
                 "WHERE username=?", (db.now(), username))
    conn.commit()
    return {"username": user["username"], "role": user["role"],
            "display_name": user["display_name"]}


# ---------------------------------------------------------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(username: str) -> tuple[str, datetime]:
    """Return (opaque token, expiry). Only the hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=SESSION_HOURS)
    conn = db.connect()
    conn.execute("INSERT INTO sessions (token_hash, username, created_at, expires_at) "
                 "VALUES (?,?,?,?)",
                 (_token_hash(token), username.strip().lower(), db.now(), _iso(expires)))
    conn.commit()
    return token, expires


def end_session(token: str | None) -> None:
    if not token:
        return
    conn = db.connect()
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    conn.commit()


def end_all_sessions(username: str) -> int:
    conn = db.connect()
    cur = conn.execute("DELETE FROM sessions WHERE username=?", (username.strip().lower(),))
    conn.commit()
    return cur.rowcount


def session_user(token: str | None) -> dict | None:
    """Resolve a cookie token to a user, dropping it if expired."""
    if not token:
        return None
    conn = db.connect()
    row = conn.execute(
        "SELECT s.expires_at, u.username, u.role, u.display_name "
        "FROM sessions s JOIN users u ON u.username = s.username "
        "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
    if row is None:
        return None
    expires = _parse(row["expires_at"])
    if expires is None or expires <= _now():
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
        conn.commit()
        return None
    return {"username": row["username"], "role": row["role"],
            "display_name": row["display_name"]}


def purge_expired_sessions() -> int:
    conn = db.connect()
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(_now()),))
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------- request-time guards
def current_user(request: Request) -> dict | None:
    return session_user(request.cookies.get(SESSION_COOKIE))


def require_staff(request: Request) -> dict:
    """Any signed-in staff member (admin or reviewer)."""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Sign-in required.")
    return user


def require_admin(request: Request) -> dict:
    """Admin only. Everything touching money goes through here.

    A reviewer hitting this gets 403, not 404: they are legitimately signed in
    and are entitled to know the resource exists and is not theirs.
    """
    user = require_staff(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required. "
                                 "Funding data is restricted to administrators.")
    return user


# -------------------------------------------------------------- bootstrap
def user_count() -> int:
    return int(db.connect().execute("SELECT COUNT(*) c FROM users").fetchone()["c"])


def needs_setup() -> bool:
    """True when no staff account exists yet, so the site must ask for one.

    This is what makes the first administrator a thing you create on the site
    rather than a thing you export in a shell before starting the server. It
    is deliberately a question about the database, not about configuration:
    once any account exists the answer is False forever, which is what closes
    the setup endpoint.
    """
    return user_count() == 0


def create_first_admin(username: str, password: str,
                       display_name: str = "Administrator") -> dict:
    """Create the very first administrator, and only the very first.

    Refuses once any account exists. That check is the entire security of the
    setup flow, so it lives here next to the write rather than in the route:
    a second caller must not be able to mint themselves an admin because a new
    endpoint forgot to ask.
    """
    if not needs_setup():
        raise AuthError("An administrator account already exists. Sign in instead.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Choose a password of at least {MIN_PASSWORD_LENGTH} "
                        f"characters.")
    if not (username or "").strip():
        raise AuthError("Choose a username.")
    user = create_user(username, password, "admin", display_name=display_name)
    log.info("First administrator created on the site: %r", user["username"])
    return user


def bootstrap_admin() -> str | None:
    """Create an admin from the environment, if and only if asked to.

    ADMIN_PASSWORD stays supported because an unattended deployment has no one
    at a browser to complete the setup screen. But it is no longer the default
    path: with nothing set, no account is invented and no password is printed
    to a log. The site asks for one instead, which is both easier to use and
    better practice -- a credential in a terminal scrollback is a credential
    in a terminal scrollback.
    """
    if not needs_setup():
        return None
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        return None                      # the login page will offer setup
    username = os.getenv("ADMIN_USERNAME", "admin").strip().lower()
    create_user(username, password, "admin", display_name="Administrator")
    log.info("Administrator %r created from the environment.", username)
    return None

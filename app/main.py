# -*- coding: utf-8 -*-
"""
API server.

Every analytical result the dashboard shows is available over this documented
REST API at /api/docs. That is a deliberate Digital Public Good property, not a
convenience: a ministry must be able to consume the prioritisation engine from
its own systems without adopting this dashboard, and a civil-society group must
be able to audit the same numbers the government sees.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import auth, db
from .ai.groq_engine import get_engine
from .analysis.fusion import available_countries, build_matrix, load_pack
from .analysis.priority import (DEFAULT_WEIGHTS, FACTOR_LABELS, rollup_districts,
                                rollup_regions, score_cells)
from .ai.speech import MAX_AUDIO_BYTES as SPEECH_MAX_BYTES
from .schemas import LoginIn, ReviewIn, RequestIn, TranscribeIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

WEB_DIR = Path(__file__).resolve().parent / "web"
ENGINE = get_engine()

app = FastAPI(
    title="Citizen Development Priority API",
    description=(
        "Citizen feedback → where the need is greatest.\n\n"
        "A multilingual, multi-channel platform that turns fragmented citizen "
        "feedback into an explainable, auditable picture of unmet need. "
        "Built as a Digital Public Good: open API, pluggable country packs, "
        "no vendor lock-in.\n\n"
        "It measures need. It does not allocate money, and carries no budget "
        "or investment data of any kind.\n\n"
        "**All demographic and infrastructure figures in this deployment are "
        "synthetic demo data.** See `/api/countries/{code}` → `data_notice`."
    ),
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --------------------------------------------------------------------------
# Scoring cache. The matrix is deterministic given (country, weights, data
# version), so recomputing it per request is pure waste. The version counter is
# bumped on any write, which keeps the cache correct without a TTL.
# --------------------------------------------------------------------------
_CACHE: dict[tuple, list[dict]] = {}
_VERSION = 0


def bump_version() -> None:
    global _VERSION
    _VERSION += 1
    _CACHE.clear()


def get_scored(country: str, weights: dict | None = None) -> list[dict]:
    wkey = tuple(sorted((weights or {}).items()))
    key = (country.upper(), wkey, _VERSION)
    if key not in _CACHE:
        pack = load_pack(country)
        rows = db.list_requests(country=country.upper(), limit=1_000_000)
        cells = build_matrix(pack, rows)
        _CACHE[key] = score_cells(cells, weights)
        if len(_CACHE) > 24:                     # bounded; drop the oldest key
            _CACHE.pop(next(iter(_CACHE)))
    return _CACHE[key]


def _weights_from_query(demand, gap, people, severity, vulnerability) -> dict | None:
    supplied = {"demand": demand, "gap": gap, "people": people,
                "severity": severity, "vulnerability": vulnerability}
    supplied = {k: v for k, v in supplied.items() if v is not None}
    return supplied or None


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    auth.purge_expired_sessions()
    auth.bootstrap_admin()
    if auth.needs_setup():
        log.warning("=" * 68)
        log.warning("No staff account yet. Open /login to create the administrator.")
        log.warning("=" * 68)
    if db.counts()["total"] == 0 and os.getenv("APP_NO_SEED") != "1":
        seed_all()
    log.info("Ready — engine=%s, requests=%d, staff accounts=%d",
             ENGINE.name, db.counts()["total"], len(auth.list_users()))


def seed_all() -> None:
    """Run the synthetic corpus through the live intake path, so seeded and
    live requests are processed by exactly the same code."""
    from .seed import generate
    log.info("Empty database — seeding demo corpus…")
    for country, n in (("IN", 4200), ("BR", 1300), ("ZA", 750)):
        pack = load_pack(country)
        for sub in generate(country, n):
            _ingest(sub["text"], country, sub["district_code"], sub["channel"],
                    None, pack, actor="seed")
    bump_version()
    log.info("Seeded %d requests", db.counts()["total"])


def _ingest(text: str, country: str, district_code: str, channel: str,
            language: str | None, pack, actor: str = "citizen") -> dict:
    entry = pack.district_by_code.get(district_code)
    if entry is None:
        raise HTTPException(404, f"Unknown district '{district_code}' in {country}")
    region, district = entry
    a = ENGINE.analyse(text, hint_language=language, country=country)
    rec = {
        "country": country, "region_code": region["code"], "district_code": district["code"],
        "channel": channel, "language": a.language, "language_confidence": a.language_confidence,
        "text_original": a.text_original, "text_redacted": a.text_redacted,
        "text_en": a.text_en, "text_local": a.text_local,
        "translated": 1 if a.translated else 0,
        "sector": a.sector, "sector_confidence": a.sector_confidence,
        "urgency": a.urgency, "urgency_score": a.urgency_score,
        "affected_population": a.affected_population, "ai_confidence": a.confidence,
        "ai_engine": a.engine, "ai_rationale": a.rationale,
        "pii_types": a.pii_types, "entities": a.entities,
        # Low-confidence requests are held for a human rather than silently
        # counted toward a ranking.
        "status": "review" if a.needs_review else "new",
        "reviewer_note": None,
    }
    rid = db.insert_request(rec, actor=actor)
    out = db.get_request(rid)
    out["region_name"] = region["name"]
    out["district_name"] = district["name"]
    return out


# ==========================================================================
# Meta
# ==========================================================================
@app.post("/api/auth/login", tags=["auth"])
def login(body: LoginIn, response: JSONResponse = None, request: Request = None):
    """Sign in a staff member and set an httpOnly session cookie."""
    try:
        user = auth.authenticate(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    token, expires = auth.start_session(user["username"])
    out = JSONResponse({"user": user, "expires_at": expires.isoformat(timespec="seconds")})
    out.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=auth.SESSION_HOURS * 3600,
        httponly=True,                      # unreadable from JavaScript
        samesite="lax",                     # blocks cross-site form CSRF
        secure=bool(request and request.url.scheme == "https"),
        path="/",
    )
    log.info("Sign-in: %s (%s)", user["username"], user["role"])
    return out


@app.post("/api/auth/setup", tags=["auth"], status_code=201)
def setup(body: LoginIn, request: Request = None):
    """Create the first administrator, from the site rather than the shell.

    Open only while no account exists; `auth.create_first_admin` refuses once
    one does. The new administrator is signed in immediately, because sending
    someone to a login form to retype the password they just chose is a step
    that exists only to annoy them.
    """
    try:
        user = auth.create_first_admin(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(409, str(exc))
    token, expires = auth.start_session(user["username"])
    out = JSONResponse({"user": user,
                        "expires_at": expires.isoformat(timespec="seconds")},
                       status_code=201)
    out.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=auth.SESSION_HOURS * 3600,
        httponly=True, samesite="lax",
        secure=bool(request and request.url.scheme == "https"),
        path="/",
    )
    return out


@app.post("/api/auth/logout", tags=["auth"])
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.SESSION_COOKIE))
    out = JSONResponse({"ok": True})
    out.delete_cookie(auth.SESSION_COOKIE, path="/")
    return out


@app.get("/api/auth/me", tags=["auth"])
def me(request: Request):
    """Who am I, and what may I see? Drives the navigation in every page."""
    user = auth.current_user(request)
    return {
        "authenticated": user is not None,
        "user": user,
        # Drives the login page: with no account yet, it asks you to create one
        # instead of asking you to sign in to something that does not exist.
        "setup_required": auth.needs_setup(),
        "can": {
            # Capabilities, not roles: the UI should never hard-code the rule.
            "submit_requests": True,          # always public, by design
            "review_queue": bool(user),
            "view_analytics": bool(user and user["role"] == "admin"),
            "export_data": bool(user and user["role"] == "admin"),
        },
    }


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "ai_engine": ENGINE.health(),
            "data": db.counts(), "countries": available_countries()}


@app.post("/api/voice/transcribe", tags=["intake"])
def transcribe(body: TranscribeIn):
    """Turn recorded audio into text, server-side.

    Audio arrives base64-encoded in JSON rather than as a multipart upload so
    the project keeps its three-package dependency list; clips are seconds
    long, so the ~33% encoding overhead is irrelevant.

    Transcribing on the server rather than in the browser is the point: it
    works in every browser, over plain HTTP on a LAN, and on whatever model the
    deployment chooses — none of which is true of the browser's own engine.
    """
    import base64
    from .ai.speech import SpeechError, get_speech_provider

    provider = get_speech_provider()
    if not provider.available():
        raise HTTPException(503, "No server-side transcription is configured. "
                                 "Set XVOICE_STT_URL or GROQ_API_KEY.")
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except Exception:                                     # noqa: BLE001
        raise HTTPException(400, "audio_base64 is not valid base64.")
    if not audio:
        raise HTTPException(400, "Empty audio.")
    if len(audio) > SPEECH_MAX_BYTES:
        raise HTTPException(413, f"Audio exceeds {SPEECH_MAX_BYTES // (1024 * 1024)}MB.")
    try:
        result = provider.transcribe(audio, body.filename, body.language)
    except SpeechError as exc:
        # A failed transcription must not look like a crash to a citizen who
        # just spoke into their phone.
        raise HTTPException(502, str(exc))
    return {**result, "bytes": len(audio)}


@app.get("/api/voice/status", tags=["intake"])
def voice_status():
    """What the browser should do for voice input, decided server-side."""
    from .ai.speech import get_speech_provider
    provider = get_speech_provider()
    return {
        "server_transcription": provider.available(),
        "provider": provider.health(),
        "hint": ("Server-side transcription is active — the browser records "
                 "audio and uploads it, which works in every browser."
                 if provider.available() else
                 "No server transcription configured. The browser falls back to "
                 "its own speech engine, which needs Chrome/Safari on a secure "
                 "origin (https:// or localhost) and covers few Indic languages. "
                 "Set GROQ_API_KEY, or XVOICE_STT_URL for XVoice."),
    }


@app.post("/api/translate/backfill", tags=["intake"])
def translate_backfill(country: str | None = None, limit: int = Query(25, le=200)):
    """Translate stored requests that only carry the offline gloss.

    Requests classified offline have a category gloss in `text_en`, not a
    translation. This re-runs those through the configured model so a
    policymaker can read what the citizen actually said — without wiping and
    re-seeding the database.
    """
    from .ai.groq_engine import GroqEngine
    engine = ENGINE if isinstance(ENGINE, GroqEngine) else GroqEngine()
    if not engine.available():
        raise HTTPException(503, "No translation model configured. Set GROQ_API_KEY.")

    rows = db.untranslated(country=country.upper() if country else None, limit=limit)
    done, failed = [], []
    for row in rows:
        a = engine.analyse(row["text_redacted"], hint_language=row["language"],
                           country=row["country"])
        if not a.translated:
            failed.append(row["id"])
            continue
        db.set_translation(row["id"], a.text_en, a.text_local, actor="backfill")
        done.append(row["id"])
    if done:
        bump_version()
    return {"translated": len(done), "failed": len(failed),
            "remaining": len(db.untranslated(country=country.upper() if country else None,
                                             limit=100000)),
            "ids": done[:50]}


@app.get("/api/ai/models", tags=["meta"])
def ai_models():
    """Which models this deployment's Groq key can actually reach.

    Asked live rather than served from a hardcoded list, because a hosted
    provider's line-up changes and a stale constant is how a deployment gets
    wedged. With no key configured this returns an empty list and the offline
    engine stays in charge — which is a valid, fully functional state.
    """
    from .ai.groq_engine import GroqEngine
    engine = ENGINE if isinstance(ENGINE, GroqEngine) else GroqEngine()
    return {
        "provider": "groq",
        "configured": engine.available(),
        "active_engine": ENGINE.name,
        "selected_model": engine.model,
        "available_models": engine.list_models(),
        "hint": ("Set GROQ_API_KEY to enable, and GROQ_MODEL to pick a model "
                 "from available_models. Without a key the offline engine "
                 "handles every request on its own."),
    }


@app.get("/api/countries", tags=["meta"])
def countries():
    return available_countries()


@app.get("/api/countries/{code}", tags=["meta"])
def country_detail(code: str):
    try:
        pack = load_pack(code)
    except FileNotFoundError:
        raise HTTPException(404, f"No country pack for '{code}'")
    return {
        "code": pack.code, "name": pack.name,
        "admin_levels": pack.admin_levels, "languages": pack.languages,
        "sectors": list(pack.sectors.values()), "map": pack.map,
        "data_notice": pack.data_notice,
        "regions": [
            {"code": r["code"], "name": r["name"], "hex": r["hex"],
             "population": r["population"],
             "districts": [{"code": d["code"], "name": d["name"],
                            "population": d["population"]} for d in r["districts"]]}
            for r in pack.regions
        ],
        "weight_defaults": dict(DEFAULT_WEIGHTS),
        "factor_labels": FACTOR_LABELS,
    }


# ==========================================================================
# Citizen intake
# ==========================================================================
@app.post("/api/requests", tags=["intake"], status_code=201)
def submit_request(body: RequestIn):
    """Submit a citizen development request through any channel."""
    try:
        pack = load_pack(body.country)
    except FileNotFoundError:
        raise HTTPException(404, f"No country pack for '{body.country}'")
    out = _ingest(body.text, body.country.upper(), body.district_code,
                  body.channel, body.language, pack)
    bump_version()
    return out


@app.get("/api/requests", tags=["intake"])
def list_requests(country: str | None = None, status: str | None = None,
                  district: str | None = None, sector: str | None = None,
                  limit: int = Query(200, le=2000), offset: int = 0,
                  user: dict = Depends(auth.require_staff)):
    return db.list_requests(country=country, status=status, district=district,
                            sector=sector, limit=limit, offset=offset)


@app.get("/api/requests/public", tags=["intake"])
def public_requests(country: str = "IN", limit: int = Query(12, le=30)):
    """A deliberately narrow public feed of recent requests.

    Citizens seeing that other people's requests were actually recorded is what
    makes the channel feel worth using, so this stays public. But it is a
    hand-picked projection, not the stored row: redacted text, sector, urgency,
    language, channel and district name. No request id, no reviewer notes, no
    AI internals.

    Worth stating plainly: redaction catches patterns (phone numbers, IDs), not
    self-identification. "The house behind the temple" survives it. Before a
    real deployment this feed should be reviewed against the local privacy
    regime, and it is a single flag to turn off.
    """
    if os.getenv("PUBLIC_FEED", "1") != "1":
        return {"enabled": False, "items": []}
    try:
        pack = load_pack(country)
    except FileNotFoundError:
        raise HTTPException(404, f"No country pack for '{country}'")
    rows = db.list_requests(country=country.upper(), limit=limit)
    items = []
    for r in rows:
        entry = pack.district_by_code.get(r["district_code"])
        items.append({
            "text": r["text_redacted"],
            "text_en": r["text_en"] if r["translated"] else "",
            "translated": bool(r["translated"]),
            "language": r["language"],
            "sector": r["sector"],
            "urgency": r["urgency"],
            "channel": r["channel"],
            "district_name": entry[1]["name"] if entry else "",
            "created_at": r["created_at"],
        })
    return {"enabled": True, "count": len(items), "items": items}


@app.get("/api/requests/{rid}", tags=["intake"])
def get_request(rid: str, user: dict = Depends(auth.require_staff)):
    rec = db.get_request(rid)
    if rec is None:
        raise HTTPException(404, "No such request")
    rec["audit"] = db.get_audit(rid)
    return rec


# ==========================================================================
# Human review — the AI never gets the last word
# ==========================================================================
@app.get("/api/review/queue", tags=["review"])
def review_queue(country: str | None = None, limit: int = Query(100, le=1000),
                 user: dict = Depends(auth.require_staff)):
    """Requests the AI was not confident enough to act on alone."""
    rows = db.list_requests(country=country, status="review", limit=limit)
    return {"count": db.count_requests(country=country, status="review"),
            "returned": len(rows), "threshold": ENGINE.REVIEW_THRESHOLD, "items": rows}


@app.post("/api/requests/{rid}/review", tags=["review"])
def review_request(rid: str, body: ReviewIn,
                   user: dict = Depends(auth.require_staff)):
    # Audit the signed-in reviewer, never a client-supplied name: an audit log
    # you can forge is not an audit log.
    out = db.update_review(rid, body.model_dump(exclude={"reviewer"}), user["username"])
    if out is None:
        raise HTTPException(404, "No such request")
    bump_version()
    out["audit"] = db.get_audit(rid)
    return out


# ==========================================================================
# Analytics
# ==========================================================================
@app.get("/api/analytics/summary", tags=["analytics"])
def summary(country: str = "IN", user: dict = Depends(auth.require_admin)):
    scored = get_scored(country)
    districts = rollup_districts(scored)
    pack = load_pack(country)
    rows = db.list_requests(country=country.upper(), limit=1_000_000)

    by_lang, by_channel, by_sector, by_urgency = {}, {}, {}, {}
    for r in rows:
        by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
        by_channel[r["channel"]] = by_channel.get(r["channel"], 0) + 1
        by_sector[r["sector"]] = by_sector.get(r["sector"], 0) + 1
        by_urgency[r["urgency"]] = by_urgency.get(r["urgency"], 0) + 1

    unmet = [r for r in scored if r["unmet_need"]]
    silent = [r for r in scored if r["silent_district"]]

    # A request the classifier could not place lands in sector "other", which is
    # not one of the pack's sectors, so `build_matrix` has no cell for it and it
    # reaches no district or region count. It is still a request the platform
    # received, and `requests_total` counts it. Reporting the difference is the
    # only thing that stops `requests_total` and the sum of the published
    # district counts from disagreeing by a silent 10%.
    unclassified = sum(1 for r in rows if r["sector"] not in pack.sectors)

    return {
        "country": pack.code, "country_name": pack.name,
        "data_notice": pack.data_notice,
        "requests_total": len(rows),
        "requests_in_review": sum(1 for r in rows if r["status"] == "review"),
        "requests_unclassified": unclassified,
        "requests_counted_in_rollups": len(rows) - unclassified,
        "languages_seen": len(by_lang), "districts": len(pack.district_by_code),
        "regions": len(pack.regions),
        "unmet_needs": len(unmet), "silent_districts": len(silent),
        "mean_priority": round(sum(r["priority_index"] for r in scored) / max(len(scored), 1), 2),
        "top_district": districts[0] if districts else None,
        "by_language": dict(sorted(by_lang.items(), key=lambda kv: -kv[1])),
        "by_channel": dict(sorted(by_channel.items(), key=lambda kv: -kv[1])),
        "by_sector": dict(sorted(by_sector.items(), key=lambda kv: -kv[1])),
        "by_urgency": by_urgency,
        "sector_names": {c: s["name"] for c, s in pack.sectors.items()},
    }


@app.get("/api/analytics/priorities", tags=["analytics"])
def priorities(country: str = "IN", sector: str | None = None, region: str | None = None,
               district: str | None = None, unmet_needs_only: bool = False,
               silent_only: bool = False, limit: int = Query(60, le=2000),
               demand: float | None = None, gap: float | None = None,
               people: float | None = None, severity: float | None = None,
               vulnerability: float | None = None,
               user: dict = Depends(auth.require_admin)):
    """Ranked (district, sector) needs, each with its full derivation.

    Weights are query parameters so a reader can see how the ranking moves when
    the policy changes — and so the ranking can never be presented as an
    objective fact independent of the weights that produced it.
    """
    scored = get_scored(country, _weights_from_query(demand, gap, people, severity,
                                                     vulnerability))
    rows = scored
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    if region:
        rows = [r for r in rows if r["region_code"] == region]
    if district:
        rows = [r for r in rows if r["district_code"] == district]
    if unmet_needs_only:
        rows = [r for r in rows if r["unmet_need"]]
    if silent_only:
        rows = [r for r in rows if r["silent_district"]]
    return {"count": len(rows), "returned": min(len(rows), limit), "items": rows[:limit]}


@app.get("/api/analytics/districts", tags=["analytics"])
def districts(country: str = "IN", region: str | None = None,
              limit: int = Query(200, le=2000),
              user: dict = Depends(auth.require_admin)):
    rows = rollup_districts(get_scored(country))
    if region:
        rows = [r for r in rows if r["region_code"] == region]
    return {"count": len(rows), "items": rows[:limit]}


@app.get("/api/analytics/regions", tags=["analytics"])
def regions(country: str = "IN", user: dict = Depends(auth.require_admin)):
    rows = rollup_regions(rollup_districts(get_scored(country)))
    return {"count": len(rows), "items": rows}


@app.get("/api/analytics/cell/{district_code}/{sector}", tags=["analytics"])
def cell_detail(district_code: str, sector: str, country: str = "IN",
                user: dict = Depends(auth.require_admin)):
    """Full derivation for one ranked need, plus the citizen requests behind it."""
    match = next((r for r in get_scored(country)
                  if r["district_code"] == district_code and r["sector"] == sector), None)
    if match is None:
        raise HTTPException(404, "No such district/sector cell")
    return {
        **match,
        "requests": db.list_requests(district=district_code, sector=sector, limit=200),
        "factor_labels": FACTOR_LABELS,
    }


@app.get("/api/export/priorities.csv", tags=["analytics"])
def export_csv(country: str = "IN", limit: int = Query(2000, le=20000),
               user: dict = Depends(auth.require_admin)):
    rows = get_scored(country)[:limit]
    buf = io.StringIO()
    cols = ["region_name", "district_name", "sector_name", "priority_index", "confidence",
            "population", "request_count", "critical_count", "infra_index", "gap_pct",
            "unmet_need", "silent_district", "rationale"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="priorities-{country}.csv"'})


# ==========================================================================
# Web UI
# ==========================================================================
# The app opens in citizen mode. Someone who lands on this platform is far more
# likely to be a person with a problem than a member of staff, and asking them
# to pick a door first -- past two doors marked with roles they do not have --
# is a step that only ever costs the citizen something.
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "citizen.html")


@app.get("/citizen", include_in_schema=False)
def citizen():
    return FileResponse(WEB_DIR / "citizen.html")


@app.get("/about", include_in_schema=False)
def about():
    """The overview of the whole platform, including the staff entrances.

    This used to be the landing page. It is still the map of the project for
    anyone evaluating it, but it is no longer what a citizen is shown first.
    """
    return FileResponse(WEB_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(WEB_DIR / "login.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard(request: Request):
    """The national picture: demand map, rankings and intake statistics.

    Administrators only."""
    user = auth.current_user(request)
    if user is None:
        return RedirectResponse("/login?next=/dashboard", status_code=303)
    if user["role"] != "admin":
        return RedirectResponse("/review?denied=dashboard", status_code=303)
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/review", include_in_schema=False)
def review(request: Request):
    """Verification queue. Any signed-in staff member."""
    if auth.current_user(request) is None:
        return RedirectResponse("/login?next=/review", status_code=303)
    return FileResponse(WEB_DIR / "review.html")


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.exception_handler(FileNotFoundError)
def not_found(request: Request, exc: FileNotFoundError):
    return JSONResponse({"detail": str(exc)}, status_code=404)

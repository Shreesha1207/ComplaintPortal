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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .ai.groq_engine import get_engine
from .analysis.budget import STRATEGIES, allocate, compare_strategies
from .analysis.fusion import available_countries, build_matrix, load_pack
from .analysis.priority import (DEFAULT_LAMBDA, DEFAULT_WEIGHTS, FACTOR_LABELS,
                              rollup_districts, rollup_regions, score_cells)
from .ai.speech import MAX_AUDIO_BYTES as SPEECH_MAX_BYTES
from .schemas import ReviewIn, RequestIn, TranscribeIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

WEB_DIR = Path(__file__).resolve().parent / "web"
ENGINE = get_engine()

app = FastAPI(
    title="Citizen Development Priority API",
    description=(
        "Citizen development requests → national investment priorities.\n\n"
        "A multilingual, multi-channel platform that turns fragmented citizen "
        "feedback into explainable, budget-aware project recommendations. "
        "Built as a Digital Public Good: open API, pluggable country packs, "
        "no vendor lock-in.\n\n"
        "**All demographic, infrastructure and investment figures in this "
        "deployment are synthetic demo data.** See `/api/countries/{code}` → "
        "`data_notice`."
    ),
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# --------------------------------------------------------------------------
# Scoring cache. The matrix is deterministic given (country, weights, λ, data
# version), so recomputing it per request is pure waste. The version counter is
# bumped on any write, which keeps the cache correct without a TTL.
# --------------------------------------------------------------------------
_CACHE: dict[tuple, list[dict]] = {}
_VERSION = 0


def bump_version() -> None:
    global _VERSION
    _VERSION += 1
    _CACHE.clear()


def get_scored(country: str, weights: dict | None = None,
               lam: float = DEFAULT_LAMBDA) -> list[dict]:
    wkey = tuple(sorted((weights or {}).items()))
    key = (country.upper(), wkey, round(lam, 4), _VERSION)
    if key not in _CACHE:
        pack = load_pack(country)
        rows = db.list_requests(country=country.upper(), limit=1_000_000)
        cells = build_matrix(pack, rows)
        _CACHE[key] = score_cells(cells, pack, weights, lam)
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
    if db.counts()["total"] == 0 and os.getenv("APP_NO_SEED") != "1":
        seed_all()
    log.info("Ready — engine=%s, requests=%d", ENGINE.name, db.counts()["total"])


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
        "text_original": a.text_original, "text_redacted": a.text_redacted, "text_en": a.text_en,
        "sector": a.sector, "sector_confidence": a.sector_confidence,
        "urgency": a.urgency, "urgency_score": a.urgency_score,
        "affected_population": a.affected_population, "ai_confidence": a.confidence,
        "ai_engine": a.engine, "ai_rationale": a.rationale,
        "pii_types": a.pii_types, "entities": a.entities,
        # Low-confidence requests are held for a human rather than silently
        # counted toward a funding recommendation.
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
        "code": pack.code, "name": pack.name, "currency": pack.currency,
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
        "weight_defaults": {**DEFAULT_WEIGHTS, "discount_lambda": DEFAULT_LAMBDA},
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
                  limit: int = Query(200, le=2000), offset: int = 0):
    return db.list_requests(country=country, status=status, district=district,
                            sector=sector, limit=limit, offset=offset)


@app.get("/api/requests/{rid}", tags=["intake"])
def get_request(rid: str):
    rec = db.get_request(rid)
    if rec is None:
        raise HTTPException(404, "No such request")
    rec["audit"] = db.get_audit(rid)
    return rec


# ==========================================================================
# Human review — the AI never gets the last word
# ==========================================================================
@app.get("/api/review/queue", tags=["review"])
def review_queue(country: str | None = None, limit: int = Query(100, le=1000)):
    """Requests the AI was not confident enough to act on alone."""
    rows = db.list_requests(country=country, status="review", limit=limit)
    return {"count": db.count_requests(country=country, status="review"),
            "returned": len(rows), "threshold": ENGINE.REVIEW_THRESHOLD, "items": rows}


@app.post("/api/requests/{rid}/review", tags=["review"])
def review_request(rid: str, body: ReviewIn):
    out = db.update_review(rid, body.model_dump(exclude={"reviewer"}), body.reviewer)
    if out is None:
        raise HTTPException(404, "No such request")
    bump_version()
    out["audit"] = db.get_audit(rid)
    return out


# ==========================================================================
# Analytics
# ==========================================================================
@app.get("/api/analytics/summary", tags=["analytics"])
def summary(country: str = "IN"):
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

    blind = [r for r in scored if r["blind_spot"]]
    silent = [r for r in scored if r["silent_district"]]
    return {
        "country": pack.code, "country_name": pack.name, "currency": pack.currency,
        "data_notice": pack.data_notice,
        "requests_total": len(rows),
        "requests_in_review": sum(1 for r in rows if r["status"] == "review"),
        "languages_seen": len(by_lang), "districts": len(pack.district_by_code),
        "regions": len(pack.regions),
        "blind_spots": len(blind), "silent_districts": len(silent),
        "unfunded_total": round(sum(r["unfunded"] for r in scored), 2),
        "committed_total": round(sum(r["committed"] for r in scored), 2),
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
               district: str | None = None, blind_spots_only: bool = False,
               silent_only: bool = False, limit: int = Query(60, le=2000),
               demand: float | None = None, gap: float | None = None,
               people: float | None = None, severity: float | None = None,
               vulnerability: float | None = None,
               discount_lambda: float = DEFAULT_LAMBDA):
    """Ranked (district, sector) recommendations, each with its full derivation.

    Weights are query parameters so a policymaker can see how the ranking moves
    when the policy changes — and so the ranking can never be presented as an
    objective fact independent of the weights that produced it.
    """
    scored = get_scored(country, _weights_from_query(demand, gap, people, severity,
                                                     vulnerability), discount_lambda)
    rows = scored
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    if region:
        rows = [r for r in rows if r["region_code"] == region]
    if district:
        rows = [r for r in rows if r["district_code"] == district]
    if blind_spots_only:
        rows = [r for r in rows if r["blind_spot"]]
    if silent_only:
        rows = [r for r in rows if r["silent_district"]]
    return {"count": len(rows), "returned": min(len(rows), limit), "items": rows[:limit]}


@app.get("/api/analytics/districts", tags=["analytics"])
def districts(country: str = "IN", region: str | None = None,
              limit: int = Query(200, le=2000)):
    rows = rollup_districts(get_scored(country))
    if region:
        rows = [r for r in rows if r["region_code"] == region]
    return {"count": len(rows), "items": rows[:limit]}


@app.get("/api/analytics/regions", tags=["analytics"])
def regions(country: str = "IN"):
    rows = rollup_regions(rollup_districts(get_scored(country)))
    return {"count": len(rows), "items": rows}


@app.get("/api/analytics/cell/{district_code}/{sector}", tags=["analytics"])
def cell_detail(district_code: str, sector: str, country: str = "IN"):
    """Full derivation for one recommendation, plus the citizen requests behind
    it and the existing projects that discounted it."""
    match = next((r for r in get_scored(country)
                  if r["district_code"] == district_code and r["sector"] == sector), None)
    if match is None:
        raise HTTPException(404, "No such district/sector cell")
    pack = load_pack(country)
    return {
        **match,
        "requests": db.list_requests(district=district_code, sector=sector, limit=200),
        "projects": pack.projects_by_key.get((district_code, sector), []),
        "factor_labels": FACTOR_LABELS,
        "currency": pack.currency,
    }


@app.get("/api/analytics/budget", tags=["analytics"])
def budget(country: str = "IN", envelope: float = 50000,
           strategy: str = Query("priority", pattern="^(priority|value|blind_spots)$"),
           limit: int = Query(80, le=1000)):
    result = allocate(get_scored(country), envelope, strategy)
    result["funded"] = result["funded"][:limit]
    result["currency"] = load_pack(country).currency
    result["strategies"] = STRATEGIES
    return result


@app.get("/api/analytics/budget/compare", tags=["analytics"])
def budget_compare(country: str = "IN", envelope: float = 50000):
    return {"envelope": envelope, "currency": load_pack(country).currency,
            "results": compare_strategies(get_scored(country), envelope)}


@app.get("/api/export/priorities.csv", tags=["analytics"])
def export_csv(country: str = "IN", limit: int = Query(2000, le=20000)):
    rows = get_scored(country)[:limit]
    buf = io.StringIO()
    cols = ["region_name", "district_name", "sector_name", "priority_index", "confidence",
            "population", "request_count", "critical_count", "infra_index", "gap_pct",
            "committed", "required", "unfunded", "coverage", "blind_spot",
            "silent_district", "rationale"]
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
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/citizen", include_in_schema=False)
def citizen():
    return FileResponse(WEB_DIR / "citizen.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/review", include_in_schema=False)
def review():
    return FileResponse(WEB_DIR / "review.html")


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.exception_handler(FileNotFoundError)
def not_found(request: Request, exc: FileNotFoundError):
    return JSONResponse({"detail": str(exc)}, status_code=404)

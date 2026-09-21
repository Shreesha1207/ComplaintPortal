# -*- coding: utf-8 -*-
"""
Test suite.

Runs under pytest if installed, and standalone via `python3 tests/test_app.py`
otherwise — a prototype that has to be runnable on a fresh machine should not
require a test runner to prove it works.

The tests that matter most here are not the CRUD ones. They are the property
tests on the scoring engine: that contributions sum to the index, that the
equity correction actually inverts the participation bias, and that silence
does not read as absence of need. Those are the claims the project rests on.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path as pathlib_Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_DB", os.path.join(tempfile.gettempdir(), "app_test.db"))

from app.ai.heuristic import HeuristicEngine
from app.ai.groq_engine import GroqEngine, get_engine
from app.analysis.fusion import build_matrix, load_pack
from app.analysis.priority import (DEFAULT_WEIGHTS, rollup_districts,
                                   rollup_regions, score_cells)

ENGINE = HeuristicEngine()
PACK = load_pack("IN")


# ---------------------------------------------------------------- AI layer
def test_language_detection_across_scripts():
    cases = [
        ("हमारे गांव में पानी नहीं है", "hi"),
        ("আমাদের গ্রামে বিদ্যুৎ নেই", "bn"),
        ("எங்கள் கிராமத்தில் சாலை இல்லை", "ta"),
        ("ನಮ್ಮ ಗ್ರಾಮದಲ್ಲಿ ಶಾಲೆ ಇಲ್ಲ", "kn"),
        ("మా గ్రామంలో ఆసుపత్రి లేదు", "te"),
        ("A falta de água na comunidade", "pt"),
        ("В нашей деревне нет электричества", "ru"),
        ("Akukho amanzi ahlanzekile lapha", "zu"),
    ]
    for text, want in cases:
        got = ENGINE.analyse(text).language
        assert got == want, f"{text!r}: expected {want}, got {got}"


def test_devanagari_disambiguates_hindi_from_marathi():
    """Same script, different language — marker words must decide."""
    assert ENGINE.analyse("आमच्या गावात पाणी नाही आहे").language == "mr"
    assert ENGINE.analyse("हमारे गांव में पानी नहीं है").language == "hi"


def test_sector_classification():
    for text, want in [
        ("हमारे गांव में पीने का पानी नहीं है", "water"),
        ("The road has huge potholes and the bridge is broken", "roads"),
        ("ನಮ್ಮ ಶಾಲೆಯಲ್ಲಿ ಶಿಕ್ಷಕರಿಲ್ಲ", "education"),
        ("மருத்துவமனை இல்லை மருந்து இல்லை", "health"),
        ("В нашей деревне нет электричества", "power"),
    ]:
        assert ENGINE.analyse(text).sector == want, text


def test_inflected_forms_match_via_stemming():
    """'электричества' (genitive) must match the lexicon's 'электричество'."""
    assert ENGINE.analyse("В деревне нет электричества уже месяц").sector == "power"


def test_urgency_grading():
    assert ENGINE.analyse("एक मौत हो चुकी है, पानी नहीं है").urgency == "critical"
    assert ENGINE.analyse("The water supply has been broken for months").urgency == "high"
    assert ENGINE.analyse("A suggestion to improve water in the future").urgency == "low"


def test_affected_population_extraction():
    assert ENGINE.analyse("200 families have no water").affected_population == 1000
    # Unicode-aware digits: Bengali numerals must parse like ASCII ones.
    assert ENGINE.analyse("৩০০ পরিবার ক্ষতিগ্রস্ত, জল নেই").affected_population == 1500
    assert ENGINE.analyse("the entire village has no water").affected_population == 1800


def test_pii_is_always_redacted():
    a = ENGINE.analyse("No water here, call me on 9845012345 or ravi@example.com")
    assert "9845012345" not in a.text_redacted
    assert "ravi@example.com" not in a.text_redacted
    assert {"phone", "email"} <= set(a.pii_types)


def test_unclassifiable_text_is_routed_to_review():
    a = ENGINE.analyse("nice weather today")
    assert a.sector == "other" and a.needs_review


def test_platform_runs_fully_without_any_api_key():
    """The headline availability claim: no key, no network, still works.

    This is not a degraded mode with holes in it — language, sector, urgency,
    reach and PII redaction all resolve offline."""
    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        assert get_engine().name == "heuristic"
        a = GroqEngine().analyse("हमारे गांव में तीन महीने से पानी नहीं है, 200 परिवार")
        assert a.engine == "heuristic"          # degraded, not failed
        assert a.sector == "water"
        assert a.urgency in {"critical", "high", "medium", "low"}
        assert a.affected_population == 1000
        assert a.language == "hi"
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved


def test_groq_engine_reports_why_it_is_inactive():
    """Operators should never have to guess why the model isn't being used."""
    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        h = GroqEngine().health()
        assert h["available"] is False
        assert "GROQ_API_KEY" in h["reason"]
        assert h["model"]                        # a model is always named
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved


def test_groq_engine_validates_and_coerces_model_output():
    """A hosted model can return anything. Nothing reaches a funding
    recommendation without being checked against the allowed vocabularies."""
    engine = GroqEngine()
    os.environ["GROQ_API_KEY"] = "test-key-not-used"
    try:
        # Stand in for the HTTP call so this runs with no network and no key.
        engine._post = lambda path, payload: {"choices": [{"message": {"content": json.dumps({
            "language": "hi", "text_en": "No drinking water for three months",
            "sector": "NOT_A_REAL_SECTOR",        # must fall back to "other"
            "urgency": "catastrophic",            # must fall back to "medium"
            "affected_population": "not a number",  # must fall back to offline value
            "confidence": 5.0,                    # must clamp into 0..1
            "entities": ["Sitamarhi"], "rationale": "test",
        })}}]}
        a = engine.analyse("हमारे गांव में तीन महीने से पानी नहीं है")
        assert a.sector == "other"
        assert a.urgency == "medium"
        assert 0.0 <= a.confidence <= 1.0
        assert isinstance(a.affected_population, int)
        assert a.engine.startswith("groq:")
    finally:
        os.environ.pop("GROQ_API_KEY", None)


def test_groq_engine_never_sends_raw_pii_to_the_model():
    """The model must only ever see already-redacted text."""
    engine = GroqEngine()
    os.environ["GROQ_API_KEY"] = "test-key-not-used"
    sent = {}
    try:
        def capture(path, payload):
            sent["content"] = payload["messages"][-1]["content"]
            return {"choices": [{"message": {"content": json.dumps({
                "language": "en", "text_en": "x", "sector": "roads",
                "urgency": "medium", "affected_population": 250,
                "confidence": 0.9, "entities": [], "rationale": "x"})}}]}
        engine._post = capture
        engine.analyse("Bad road here, call me on 9845012345 or ravi@example.com")
        assert "9845012345" not in sent["content"]
        assert "ravi@example.com" not in sent["content"]
        assert "REDACTED" in sent["content"]
    finally:
        os.environ.pop("GROQ_API_KEY", None)


def test_engine_disagreement_escalates_to_human_review():
    """Two engines reaching different sectors is a better review trigger than
    either engine's own confidence."""
    engine = GroqEngine()
    os.environ["GROQ_API_KEY"] = "test-key-not-used"
    try:
        engine._post = lambda path, payload: {"choices": [{"message": {"content": json.dumps({
            "language": "en", "text_en": "The school has no teachers",
            "sector": "education",          # offline engine will say "water"
            "urgency": "high", "affected_population": 1800,
            "confidence": 0.95,             # high confidence, yet still escalated
            "entities": [], "rationale": "test",
        })}}]}
        a = engine.analyse("There is no drinking water and the borewell is broken")
        assert a.needs_review, "sector disagreement must escalate"
        assert "disagree" in a.rationale.lower()
    finally:
        os.environ.pop("GROQ_API_KEY", None)


# ------------------------------------------------------------ translation
def test_offline_engine_does_not_claim_to_translate():
    """The offline engine categorises; it does not translate. Saying otherwise
    would put a category label in front of a policymaker as if it were the
    citizen's words."""
    a = ENGINE.analyse("எங்கள் கிராமத்தில் மருத்துவமனை இல்லை")
    assert a.translated is False
    assert a.text_local == ""
    assert a.text_en.startswith("[ta]")        # a labelled gloss, not prose


def test_model_translates_into_english_and_the_country_link_language():
    """A Tamil request must be readable by an official who reads Hindi or
    English — translating only to English serves donors, not the ministry."""
    engine = GroqEngine()
    os.environ["GROQ_API_KEY"] = "test-key-not-used"
    try:
        engine._post = lambda path, payload: {"choices": [{"message": {"content": json.dumps({
            "language": "ta",
            "text_en": "There is no hospital in our village.",
            "text_local": "हमारे गाँव में कोई अस्पताल नहीं है।",
            "sector": "health", "urgency": "critical", "affected_population": 1800,
            "confidence": 0.93, "entities": [], "rationale": "r",
        })}}]}
        a = engine.analyse("எங்கள் கிராமத்தில் மருத்துவமனை இல்லை", country="IN")
        assert a.translated is True
        assert a.text_en == "There is no hospital in our village."
        assert a.text_local == "हमारे गाँव में कोई अस्पताल नहीं है।"
    finally:
        os.environ.pop("GROQ_API_KEY", None)


def test_link_language_is_per_country():
    from app.ai.groq_engine import link_language_for
    assert link_language_for("IN") == "hi"
    assert link_language_for("BR") == "pt"
    assert link_language_for("ZA") == "en"
    assert link_language_for("ZZ") == "en"     # unknown country must not crash


def test_translation_columns_migrate_onto_an_existing_database():
    """Adding translation must not require wiping a deployed database."""
    import sqlite3, tempfile, importlib
    from app import db as _db
    path = os.path.join(tempfile.gettempdir(), "migrate_check.db")
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE requests (id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, country TEXT NOT NULL, region_code TEXT NOT NULL,
      district_code TEXT NOT NULL, channel TEXT NOT NULL, language TEXT NOT NULL,
      language_confidence REAL NOT NULL, text_original TEXT NOT NULL,
      text_redacted TEXT NOT NULL, text_en TEXT NOT NULL, sector TEXT NOT NULL,
      sector_confidence REAL NOT NULL, urgency TEXT NOT NULL, urgency_score REAL NOT NULL,
      affected_population INTEGER NOT NULL, ai_confidence REAL NOT NULL,
      ai_engine TEXT NOT NULL, ai_rationale TEXT NOT NULL,
      pii_types TEXT NOT NULL DEFAULT '[]', entities TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL DEFAULT 'new', reviewer_note TEXT);
      CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
      request_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
      detail TEXT NOT NULL DEFAULT '{}');""")
    c.execute("INSERT INTO requests VALUES ('R1','t','t','IN','TN','TN-1','voice','ta',0.9,"
              "'x','x','[ta] gloss','water',0.8,'high',0.7,1800,0.8,'heuristic','r',"
              "'[]','[]','new',NULL)")
    c.commit(); c.close()

    saved = os.environ.get("APP_DB")
    os.environ["APP_DB"] = path
    try:
        importlib.reload(_db)
        _db.init_db()
        row = _db.get_request("R1")
        assert row is not None and row["text_original"] == "x"   # nothing lost
        assert row["text_local"] == "" and row["translated"] == 0
        assert len(_db.untranslated(country="IN")) == 1
    finally:
        if saved is not None:
            os.environ["APP_DB"] = saved
        else:
            os.environ.pop("APP_DB", None)
        importlib.reload(_db)


# ------------------------------------------------------------------ voice
def test_speech_provider_is_none_until_configured():
    """No STT config must mean a clear 'not configured', never a crash."""
    from app.ai.speech import get_speech_provider, SpeechError
    for var in ("XVOICE_STT_URL", "GROQ_API_KEY", "SPEECH_PROVIDER"):
        os.environ.pop(var, None)
    p = get_speech_provider()
    assert p.name == "none" and p.available() is False
    try:
        p.transcribe(b"x", "a.webm")
        assert False, "should have raised"
    except SpeechError:
        pass


def test_xvoice_wins_over_groq_when_both_configured():
    """XVoice is the intended production path; Groq is the stand-in."""
    from app.ai.speech import get_speech_provider
    os.environ["GROQ_API_KEY"] = "k"
    os.environ["XVOICE_STT_URL"] = "https://example.invalid/stt"
    try:
        assert get_speech_provider().name == "xvoice"
        os.environ.pop("XVOICE_STT_URL")
        assert get_speech_provider().name == "groq"
    finally:
        for var in ("GROQ_API_KEY", "XVOICE_STT_URL"):
            os.environ.pop(var, None)


def test_multipart_body_is_well_formed():
    """Hand-rolled because the project refuses a dependency for one upload —
    so it has to be tested rather than assumed."""
    from app.ai.speech import _multipart
    body, ctype = _multipart({"model": "m", "language": "ta", "skipme": None},
                             "audio.webm", b"AUDIOBYTES")
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1]
    assert body.count(boundary.encode()) >= 3     # two fields + file + closing
    assert b'name="model"' in body and b'name="language"' in body
    assert b'name="skipme"' not in body           # None fields are dropped
    assert b'filename="audio.webm"' in body
    assert b"Content-Type: video/webm" in body or b"Content-Type: audio/webm" in body
    assert b"AUDIOBYTES" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_transcription_failure_is_reported_not_swallowed():
    """A citizen who just spoke must get a real error, not silence."""
    from app.ai.speech import GroqWhisper, SpeechError
    os.environ["GROQ_API_KEY"] = "k"
    try:
        g = GroqWhisper()
        g.transcribe(b"audio", "a.webm")          # unreachable host in tests
        assert False, "should have raised"
    except SpeechError as exc:
        assert str(exc)                            # carries a showable message
    finally:
        os.environ.pop("GROQ_API_KEY", None)


# ------------------------------------------------------------- data packs
def test_every_country_pack_loads_and_is_well_formed():
    for code in ("IN", "BR", "ZA"):
        p = load_pack(code)
        assert p.regions and p.sectors and p.languages
        # No money lives in a pack. A reintroduced currency block or investment
        # pipeline is what this is here to catch.
        assert not hasattr(p, "currency")
        assert "investments" not in p.raw and "currency" not in p.raw
        for s_ in p.sectors.values():
            assert "cost_weight" not in s_
        for r in p.regions:
            assert len(r["hex"]) == 2
            for d in r["districts"]:
                assert set(d["infra"]) == set(p.sectors), f"{code}/{d['name']} sector mismatch"


def test_matrix_covers_every_district_sector_pair():
    """Built exhaustively, not from the requests — silence must not drop out."""
    cells = build_matrix(PACK, [])
    assert len(cells) == len(PACK.district_by_code) * len(PACK.sectors)


# ------------------------------------------------- the load-bearing claims
def test_contributions_sum_exactly_to_the_priority_index():
    """Explainability is only real if the decomposition is exact."""
    scored = score_cells(build_matrix(PACK, []))
    for r in scored[:250]:
        assert abs(sum(r["contributions"].values()) - r["priority_index"]) < 0.05, r["district_name"]


MONEY_KEYS = {"committed", "required", "unfunded", "coverage", "well_covered",
              "counterfactual", "project_count", "currency", "budget", "envelope",
              "allocated", "blind_spot"}


def test_no_scored_cell_carries_a_money_field():
    """The platform measures need and does not allocate money. An exact key
    check, not a spot check: a reintroduced funding figure must fail here rather
    than quietly reappear in an API response."""
    scored = score_cells(build_matrix(PACK, []))
    for r in scored[:200]:
        leaked = MONEY_KEYS & set(r)
        assert not leaked, f"{r['district_name']}/{r['sector']} carries {sorted(leaked)}"


def test_no_money_survives_anywhere_in_the_scored_payload():
    """Serialise a cell and grep it. Catches money smuggled inside `factors`,
    `contributions` or the rationale string, which a top-level key check misses."""
    scored = score_cells(build_matrix(PACK, []))
    blob = json.dumps(scored[:200]).lower()
    for word in ("committed", "unfunded", "coverage", "crore", "capex", "envelope",
                 "₹", "r$", "budget"):
        assert word not in blob, f"the scored payload still mentions {word!r}"


def _synthetic_corpus(n: int = 4200) -> list[dict]:
    """The demo corpus, classified offline, built in-process.

    This used to read whatever happened to be in the developer's database and
    `return` silently when it held fewer than 200 rows -- so on a fresh checkout
    the project's single load-bearing claim was asserting nothing at all. It
    takes about a second to build the corpus honestly.
    """
    from app.seed import generate
    rows = []
    for sub in generate("IN", n):
        a = ENGINE.analyse(sub["text"], country="IN")
        rows.append({"district_code": sub["district_code"], "sector": a.sector,
                     "urgency_score": a.urgency_score, "urgency": a.urgency,
                     "ai_confidence": a.confidence, "language": a.language,
                     "channel": sub["channel"],
                     "status": "review" if a.needs_review else "new"})
    return rows


def test_equity_correction_inverts_participation_bias():
    """The core claim. Raw demand favours districts that can complain; after
    correction that advantage must be removed, not merely reduced."""
    rows = _synthetic_corpus()
    assert len(rows) >= 200, "the corpus generator produced too little to test on"
    scored = score_cells(build_matrix(PACK, rows))
    pool = [r for r in scored if r["request_count"] >= 3]
    pool.sort(key=lambda r: r["participation_index"])
    n = max(1, len(pool) // 4)
    lo, hi = pool[:n], pool[-n:]
    raw = lambda g: sum(r["demand_density"] * r["participation_index"] for r in g) / len(g)
    cor = lambda g: sum(r["demand_density"] for r in g) / len(g)
    assert raw(hi) > raw(lo), "fixture should exhibit the participation bias"
    assert cor(hi) / cor(lo) < raw(hi) / raw(lo), "correction must shrink the advantage"


def test_silent_districts_still_surface_without_any_citizen_signal():
    """A district that sends nothing must still be scoreable and flagged."""
    scored = score_cells(build_matrix(PACK, []))   # zero requests
    assert all(r["request_count"] == 0 for r in scored)
    assert max(r["priority_index"] for r in scored) > 30, "admin data alone must rank"
    assert any(r["silent_district"] for r in scored), "silence must be flagged"


def test_unclassified_requests_are_accounted_for_not_silently_dropped():
    """A request the classifier cannot place gets sector "other", which is not
    one of the pack's ten sectors, so `build_matrix` has no cell for it and it
    reaches no district or region count.

    On the demo corpus that is 421 of 4,200 requests -- 10%. Published as-is, a
    statistics page would show "4,200 requests received" above district counts
    summing to 3,779, with nothing explaining the gap. The summary reports the
    difference, and this pins the arithmetic so the two can never drift apart
    unexplained again."""
    rows = _synthetic_corpus()
    unclassified = [r for r in rows if r["sector"] not in PACK.sectors]
    assert unclassified, "fixture should contain requests the classifier cannot place"
    assert all(r["sector"] == "other" for r in unclassified)

    counted = sum(d["request_count"] for d in
                  rollup_districts(score_cells(build_matrix(PACK, rows))))
    assert counted + len(unclassified) == len(rows), (
        f"{len(rows)} requests, {counted} reach a district count, "
        f"{len(unclassified)} unclassified -- the three must reconcile exactly")


def test_weights_actually_move_the_ranking():
    cells = build_matrix(PACK, [])
    heavy = lambda k: {kk: (0.9 if kk == k else 0.025) for kk in DEFAULT_WEIGHTS}
    gap_top = score_cells(cells, heavy("gap"))[0]
    ppl_top = score_cells(cells, heavy("people"))[0]
    assert gap_top["district_code"] != ppl_top["district_code"]
    # people-heavy must pick a far larger district than gap-heavy
    assert ppl_top["population"] > gap_top["population"]


def test_weights_are_renormalised():
    """Arbitrary weights must not inflate scores past 100."""
    cells = build_matrix(PACK, [])
    s = score_cells(cells, {k: 5.0 for k in DEFAULT_WEIGHTS})
    assert all(0 <= r["priority_index"] <= 100 for r in s)


def test_rollups_are_consistent():
    scored = score_cells(build_matrix(PACK, []))
    d = rollup_districts(scored)
    r = rollup_regions(d)
    assert len(d) == len(PACK.district_by_code)
    assert len(r) == len(PACK.regions)
    assert all(x["priority_index"] >= y["priority_index"]
               for x, y in zip(d, d[1:])), "districts must be sorted"


# ----------------------------------------------------------------- flags
def test_unmet_need_flag_still_fires_without_investment_data():
    """`blind_spot` used to mean "loud demand, severe deficit, and no money
    committed". With investment data out of the platform the money clause is
    gone, so the flag is `unmet_need` and says only what it can still see.

    The flag must not have been quietly emptied by the removal: on the demo
    corpus the old rule fired on 36 of 1,460 cells and this one fires on 38 --
    the two extra are cells the old rule suppressed because money was committed
    there."""
    scored = score_cells(build_matrix(PACK, _synthetic_corpus()))
    flagged = [r for r in scored if r["unmet_need"]]
    assert 25 <= len(flagged) <= 60, f"{len(flagged)} flagged — the rule has drifted"
    for r in flagged:
        assert r["factors"]["demand"] >= 0.60 and r["factors"]["gap"] >= 0.50


# ----------------------------------------------------------- app + guards
# These are the tests that were missing when a merge conflict resolution
# dropped `from . import auth` and the LoginIn model: every test below passed
# green while the server could not start at all, because nothing here imported
# app.main. Importing it is the point.
def _load_app():
    """Import the FastAPI app, with seeding off so this stays fast."""
    os.environ["APP_NO_SEED"] = "1"
    from app.main import app
    return app


def test_app_imports_and_starts():
    """The server module imports. A NameError here means the app cannot boot,
    which is invisible to every other test in this file."""
    app = _load_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/health" in paths, "health route missing from the route table"


def _guards(route) -> set[str]:
    """Names of the auth dependencies a route carries."""
    deps = getattr(getattr(route, "dependant", None), "dependencies", [])
    return {getattr(d.call, "__name__", "") for d in deps}


def _api_routes():
    for r in _load_app().routes:
        path = getattr(r, "path", "")
        if path.startswith("/api") and path not in ("/api/docs", "/api/openapi.json"):
            yield path, sorted(getattr(r, "methods", []) or []), _guards(r)


def test_analytics_routes_require_admin():
    """The boundary is the data, not the screen. It is no longer a boundary
    around money -- there is none -- but around the national aggregate and, in
    /api/analytics/cell, the stored request rows including `text_original`, the
    citizen's words before redaction.

    An exact set of (path, method) pairs, not a floor: a floor passes while a
    new endpoint goes unguarded."""
    expected = {("/api/analytics/summary", "GET"),
                ("/api/analytics/priorities", "GET"),
                ("/api/analytics/districts", "GET"),
                ("/api/analytics/regions", "GET"),
                ("/api/analytics/cell/{district_code}/{sector}", "GET"),
                ("/api/export/priorities.csv", "GET")}
    seen = set()
    for path, methods, guards in _api_routes():
        if path.startswith("/api/analytics") or path.startswith("/api/export"):
            for m in methods:
                seen.add((path, m))
                assert "require_admin" in guards, \
                    f"{m} {path} is on the analytics surface without require_admin"
    assert seen == expected, f"analytics surface changed: {seen ^ expected}"


def test_review_routes_require_a_signed_in_staff_member():
    """Named by (path, method), not by path alone: /api/requests is a staff read
    and an anonymous write on the same path, and the verification POST is the
    one that changes a request's status and writes the audit entry."""
    staff_routes = {("/api/requests", "GET"),
                    ("/api/requests/{rid}", "GET"),
                    ("/api/review/queue", "GET"),
                    ("/api/requests/{rid}/review", "POST")}
    seen = set()
    for path, methods, guards in _api_routes():
        for m in methods:
            if (path, m) in staff_routes:
                seen.add((path, m))
                assert guards & {"require_staff", "require_admin"}, \
                    f"{m} {path} is reachable without a sign-in guard"
    # An exact match, not a floor: a floor of 3 was satisfied by the three read
    # routes alone, which let the verification POST go unchecked.
    assert seen == staff_routes, f"routes missing from the table: {staff_routes - seen}"


def test_citizen_intake_stays_anonymous():
    """Requiring a login to report a broken handpump would silence exactly the
    people this platform exists to hear. Intake carries no guard, on purpose."""
    open_routes = {("/api/requests", "POST"), ("/api/requests/public", "GET"),
                   ("/api/countries", "GET"), ("/api/health", "GET")}
    seen = set()
    for path, methods, guards in _api_routes():
        for m in methods:
            if (path, m) in open_routes:
                seen.add((path, m))
                assert not guards & {"require_staff", "require_admin"}, \
                    f"{m} {path} must not require an account: {sorted(guards)}"
    assert seen == open_routes, f"routes missing from the table: {open_routes - seen}"


# --------------------------------------------------------- setup and .env
def test_dotenv_is_read_and_never_overrides_the_real_environment():
    """A .env value fills a gap; it does not replace what the shell exported."""
    import tempfile as _tf
    from app import load_env
    with _tf.TemporaryDirectory() as d:
        f = pathlib_Path(d) / ".env"
        f.write_text("# a comment\n\n"
                     "APP_TEST_PLAIN=one\n"
                     'APP_TEST_QUOTED="two words"\n'
                     "export APP_TEST_EXPORTED=three\n"
                     "APP_TEST_ALREADY_SET=from-file\n"
                     "not a pair\n", encoding="utf-8")
        os.environ["APP_TEST_ALREADY_SET"] = "from-shell"
        for k in ("APP_TEST_PLAIN", "APP_TEST_QUOTED", "APP_TEST_EXPORTED"):
            os.environ.pop(k, None)
        load_env(f)

    assert os.environ["APP_TEST_PLAIN"] == "one"
    assert os.environ["APP_TEST_QUOTED"] == "two words", "quotes should be stripped"
    assert os.environ["APP_TEST_EXPORTED"] == "three", "'export ' prefix should be tolerated"
    assert os.environ["APP_TEST_ALREADY_SET"] == "from-shell", \
        "a real environment variable must win over the file"


def test_a_missing_or_broken_env_file_does_not_stop_startup():
    from app import load_env
    assert load_env(pathlib_Path("/nonexistent/nowhere/.env")) == []


def test_first_admin_is_created_once_and_only_once():
    """The setup flow's whole security is that it closes after the first use.

    Runs against the real table and puts back whatever was there. Repointing
    db.DB_PATH would also work -- connect() reads the global each call, and a
    new thread has no cached connection -- but restoring the rows needs no
    such arrangement, and leaves nothing to unwind if an assertion fails.
    """
    from app import db as _db, auth as _auth
    _db.init_db()
    conn = _db.connect()
    saved = [dict(r) for r in conn.execute("SELECT * FROM users")]
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users")
    conn.commit()
    try:
        assert _auth.needs_setup(), "with no rows, the site must ask for an account"

        # Too short is refused, and a refused attempt must leave nothing behind.
        try:
            _auth.create_first_admin("admin", "short")
            raise AssertionError("a short password should have been refused")
        except _auth.AuthError:
            pass
        assert _auth.needs_setup(), "a refused attempt must not create an account"

        user = _auth.create_first_admin("admin", "a-real-password")
        assert user["role"] == "admin"
        assert not _auth.needs_setup()
        assert _auth.authenticate("admin", "a-real-password")["role"] == "admin"

        # The second caller must not be able to mint themselves an admin.
        try:
            _auth.create_first_admin("intruder", "another-password")
            raise AssertionError("setup should be closed once an account exists")
        except _auth.AuthError:
            pass
        assert [u["username"] for u in _auth.list_users()] == ["admin"]
    finally:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        for row in saved:
            cols = ",".join(row)
            marks = ",".join("?" * len(row))
            conn.execute(f"INSERT INTO users ({cols}) VALUES ({marks})",
                         tuple(row.values()))
        conn.commit()


def test_concurrent_setup_can_only_ever_create_one_administrator():
    """Six callers racing a fresh install must yield one admin, not six.

    This is the reason the emptiness test and the insert are a single
    statement. When they were two, every caller passed the check while the
    others were still hashing, and every caller then inserted: a fresh
    deployment handed out six administrator accounts, none of which had to
    sign in to anything. Threads, not coroutines, because the connection is
    thread-local and the real server is a threaded worker pool.
    """
    import threading
    from app import db as _db, auth as _auth
    _db.init_db()
    conn = _db.connect()
    saved = [dict(r) for r in conn.execute("SELECT * FROM users")]
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users")
    conn.commit()
    try:
        assert _auth.needs_setup()
        created, refused = [], []
        start = threading.Barrier(6)

        def attempt(i):
            start.wait()                      # all six leave the gate together
            try:
                created.append(_auth.create_first_admin(f"claimant{i}",
                                                        f"a-real-password-{i}"))
            except _auth.AuthError:
                refused.append(i)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        accounts = _auth.list_users()
        assert len(accounts) == 1, \
            f"expected exactly one administrator, got {[a['username'] for a in accounts]}"
        assert len(created) == 1, f"{len(created)} callers were told they succeeded"
        assert len(refused) == 5, f"{len(refused)} callers were refused, expected 5"
        # The winner must be a usable account, not a half-written row.
        winner = created[0]["username"]
        assert accounts[0]["username"] == winner
        assert _auth.authenticate(winner, f"a-real-password-{winner[-1]}")["role"] == "admin"
    finally:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        for row in saved:
            cols = ",".join(row)
            marks = ",".join("?" * len(row))
            conn.execute(f"INSERT INTO users ({cols}) VALUES ({marks})",
                         tuple(row.values()))
        conn.commit()


def test_setup_and_login_routes_need_no_account_to_reach():
    """Both are the way in, so neither can sit behind a sign-in guard."""
    open_auth = {("/api/auth/setup", "POST"), ("/api/auth/login", "POST")}
    seen = set()
    for path, methods, guards in _api_routes():
        for m in methods:
            if (path, m) in open_auth:
                seen.add((path, m))
                assert not guards & {"require_staff", "require_admin"}, \
                    f"{m} {path} must be reachable without an account"
    assert seen == open_auth, f"routes missing from the table: {open_auth - seen}"


# ------------------------------------------------------------------ main
def _run_standalone() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}\n          {e}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())

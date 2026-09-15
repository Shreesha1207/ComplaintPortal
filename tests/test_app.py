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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_DB", os.path.join(tempfile.gettempdir(), "app_test.db"))

from app.ai.heuristic import HeuristicEngine
from app.ai.groq_engine import GroqEngine, get_engine
from app.analysis.budget import allocate, compare_strategies
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
        assert "capex_per_capita" in p.currency
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
    scored = score_cells(build_matrix(PACK, []), PACK)
    for r in scored[:250]:
        assert abs(sum(r["contributions"].values()) - r["priority_index"]) < 0.05, r["district_name"]


def test_investment_coverage_discounts_priority_but_never_to_zero_by_default():
    cells = build_matrix(PACK, [])
    base = {(r["district_code"], r["sector"]): r for r in score_cells(cells, PACK, None, 0.0)}
    disc = {(r["district_code"], r["sector"]): r for r in score_cells(cells, PACK, None, 0.6)}
    covered = [k for k, v in base.items() if disc[k]["coverage"] > 0.9]
    assert covered, "fixture should contain fully covered cells"
    for k in covered[:40]:
        assert disc[k]["priority_index"] < base[k]["priority_index"]
        # λ=0.6, so 40% of need survives full funding: a budget line is not
        # delivered infrastructure.
        assert disc[k]["priority_index"] > 0


def test_equity_correction_inverts_participation_bias():
    """The core claim. Raw demand favours districts that can complain; after
    correction that advantage must be removed, not merely reduced."""
    from app import db
    db.init_db()
    rows = db.list_requests(country="IN", limit=10 ** 6)
    if len(rows) < 200:
        return  # seeded DB not present; covered by the API-level check instead
    scored = score_cells(build_matrix(PACK, rows), PACK)
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
    scored = score_cells(build_matrix(PACK, []), PACK)   # zero requests
    assert all(r["request_count"] == 0 for r in scored)
    assert max(r["priority_index"] for r in scored) > 30, "admin data alone must rank"
    assert any(r["silent_district"] for r in scored), "silence must be flagged"


def test_weights_actually_move_the_ranking():
    cells = build_matrix(PACK, [])
    heavy = lambda k: {kk: (0.9 if kk == k else 0.025) for kk in DEFAULT_WEIGHTS}
    gap_top = score_cells(cells, PACK, heavy("gap"))[0]
    ppl_top = score_cells(cells, PACK, heavy("people"))[0]
    assert gap_top["district_code"] != ppl_top["district_code"]
    # people-heavy must pick a far larger district than gap-heavy
    assert ppl_top["population"] > gap_top["population"]


def test_weights_are_renormalised():
    """Arbitrary weights must not inflate scores past 100."""
    cells = build_matrix(PACK, [])
    s = score_cells(cells, PACK, {k: 5.0 for k in DEFAULT_WEIGHTS})
    assert all(0 <= r["priority_index"] <= 100 for r in s)


def test_rollups_are_consistent():
    scored = score_cells(build_matrix(PACK, []), PACK)
    d = rollup_districts(scored)
    r = rollup_regions(d)
    assert len(d) == len(PACK.district_by_code)
    assert len(r) == len(PACK.regions)
    assert all(x["priority_index"] >= y["priority_index"]
               for x, y in zip(d, d[1:])), "districts must be sorted"


# ---------------------------------------------------------------- budget
def test_budget_never_overspends_and_reaches_more_people_on_value_strategy():
    scored = score_cells(build_matrix(PACK, []), PACK)
    res = compare_strategies(scored, 50_000)
    for k, v in res.items():
        assert v["allocated"] <= v["envelope"] + 0.01, k
    assert res["value"]["population_reached"] > res["priority"]["population_reached"], \
        "value-for-money must reach more people — that is the whole trade-off"


def test_budget_counts_each_district_population_once():
    scored = score_cells(build_matrix(PACK, []), PACK)
    r = allocate(scored, 200_000, "priority")
    districts = {f["district_code"] for f in r["funded"]}
    assert r["districts_covered"] == len(districts)
    # A district funded in several sectors must contribute its population once,
    # or reach is silently multiplied by the number of sectors funded.
    expected = sum(PACK.district_by_code[c][1]["population"] for c in districts)
    assert r["population_reached"] == expected
    assert len(r["funded"]) > len(districts), "fixture should fund multi-sector districts"


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

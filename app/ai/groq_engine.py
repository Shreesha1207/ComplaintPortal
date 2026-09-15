# -*- coding: utf-8 -*-
"""
Groq-backed request-understanding engine.

Active when `GROQ_API_KEY` is set. It adds what the offline engine genuinely
cannot do: real translation into the pivot language, and correct handling of
the messy middle -- code-mixed, misspelt, dictated, or idiomatic text where no
lexicon keyword fires.

WHY GROQ, AND WHY IT IS STILL OPTIONAL
--------------------------------------
Groq serves open-weight models (Llama, Qwen, GPT-OSS and others) behind an
OpenAI-compatible API at very low latency and cost, which matters when the
realistic volume is millions of citizen requests a year. Using open-weight
models is also the right posture for a Digital Public Good: a government can
eventually self-host the same model rather than depending on any vendor.

The adapter is still optional. The offline engine remains the default and is
sufficient on its own -- see `heuristic.py`. Nothing here is required for the
platform to run.

DESIGN DECISIONS WORTH DEFENDING IN REVIEW
------------------------------------------
1. NO NEW DEPENDENCY. This talks to Groq over `urllib` from the standard
   library rather than pulling in an SDK. The whole project installs with three
   packages and runs behind a restrictive government proxy; adding a transitive
   dependency tree to make one JSON POST is a bad trade.

2. PII REDACTION IS NEVER DELEGATED TO THE MODEL. The deterministic regex pass
   runs on every request regardless of engine, and the prompt below receives
   already-redacted text, so raw identifiers never leave the process. A
   probabilistic system is a fine second line of defence and an unacceptable
   only line of defence.

3. THE OFFLINE ENGINE STILL RUNS. Its result is the fallback on any error, and
   -- more useful -- a free second opinion: when the two engines disagree on
   sector, the request is flagged for human review even if both were
   individually confident. Cheap ensemble disagreement is a better review
   trigger than either engine's self-reported confidence.

4. TEMPERATURE 0 AND JSON MODE. A funding recommendation must be reproducible
   at appeal. Temperature 0 plus constrained JSON output is as close to
   deterministic as a hosted model gets, and every field is still validated and
   coerced on our side before it is trusted.

5. THE MODEL IS CONFIGURATION, NOT CODE. Model availability on any hosted
   provider changes. `GROQ_MODEL` selects it, and `list_models()` asks the API
   what this key can actually reach, so the deployment is never wedged by a
   hardcoded identifier going stale.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from .base import Analysis, AnalysisEngine
from .heuristic import HeuristicEngine

log = logging.getLogger("app.ai.groq")

BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# Override with GROQ_MODEL. Hosted model line-ups change; call list_models()
# (or GET /api/ai/models) to see what this key can actually reach.
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "20"))

SECTOR_CODES = ["water", "roads", "health", "education", "power", "transport",
                "housing", "digital", "agriculture", "jobs", "other"]
URGENCIES = ["critical", "high", "medium", "low"]
URGENCY_SCORE = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.15}

SYSTEM_PROMPT = """You classify citizen development requests for a national \
public-investment planning platform used across BRICS nations.

You will receive one citizen request in any language. Reply with JSON only.

SECTORS (choose exactly one):
- water: drinking water, piped supply, sewage, drainage, toilets, sanitation
- roads: roads, potholes, bridges, culverts, footpaths, physical connectivity
- health: hospitals, clinics, doctors, medicines, ambulances, maternal care
- education: schools, teachers, classrooms, colleges, early-childhood centres
- power: electricity supply, transformers, street lighting, outages
- transport: buses, trains, metro, ferries, public transport services
- housing: housing, shelter, informal settlements, tenure, roofing
- digital: internet, mobile network, broadband, telecom coverage
- agriculture: irrigation, canals, crops, farm inputs, drought, storage
- jobs: employment, wages, skills training, livelihoods
- other: none of the above, or not a development request at all

URGENCY:
- critical: loss of life, disease outbreak, structural collapse, imminent danger
- high: prolonged deprivation (months), service fully absent or broken, \
vulnerable groups directly affected
- medium: real but non-acute service degradation
- low: suggestion, preference, or future-facing proposal

Return exactly this JSON shape:
{"language": "ISO-639-1 code", "text_en": "faithful plain English translation",
 "sector": "one sector code", "urgency": "critical|high|medium|low",
 "affected_population": 0, "population_basis": "how you reached that number",
 "entities": ["place names, may be empty"], "confidence": 0.0,
 "rationale": "one sentence: why this classification"}

RULES:
- affected_population: use an explicit number if the citizen states one \
(multiply households/families by 5). Otherwise infer from stated scale: \
village 1800, block 9000, ward 900, street 300. If nothing is stated, use 250.
- text_en: faithful translation. Do not summarise, do not add detail the \
citizen did not give, do not editorialise.
- confidence: your genuine calibrated confidence that sector AND urgency are \
both correct. Be honest. A low score routes this to a human, which is the \
correct outcome when the request is ambiguous -- it is not a failure.
- Text may already contain [PHONE-REDACTED] style markers. Leave them as-is.
- Never infer caste, religion, ethnicity or political affiliation. Never let \
the perceived social group of the requester influence urgency or population."""


class GroqEngine(AnalysisEngine):
    """Groq adapter with automatic degradation to the offline engine."""

    name = "groq"

    def __init__(self, model: str | None = None, fallback: AnalysisEngine | None = None):
        self.model = model or DEFAULT_MODEL
        self.base_url = BASE_URL
        self.fallback = fallback or HeuristicEngine()
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        return os.getenv("GROQ_API_KEY") or None

    def available(self) -> bool:
        return bool(self.api_key)

    def health(self) -> dict:
        return {
            "engine": self.name,
            "available": self.available(),
            "model": self.model,
            "base_url": self.base_url,
            "reason": None if self.available() else "GROQ_API_KEY is not set",
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_models(self) -> list[str]:
        """What this key can actually reach. Beats trusting a hardcoded list."""
        if not self.available():
            return []
        req = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return sorted(m["id"] for m in data.get("data", []) if m.get("id"))
        except Exception as exc:                          # noqa: BLE001
            log.warning("Could not list Groq models: %s", exc)
            return []

    # ------------------------------------------------------------------
    def analyse(self, text: str, *, hint_language: str | None = None,
                country: str = "IN") -> Analysis:
        # Always compute the offline result: it is both the fallback and the
        # second opinion used for the disagreement check below.
        base = self.fallback.analyse(text, hint_language=hint_language, country=country)
        if not self.available() or not text.strip():
            return base

        try:
            data = self._post("/chat/completions", {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    # Send the redacted text, never the raw text.
                    {"role": "user",
                     "content": f"Citizen request:\n\n{base.text_redacted}"},
                ],
            })
            raw = data["choices"][0]["message"]["content"]
            r = json.loads(raw)
            self._last_error = None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            self._last_error = f"HTTP {exc.code}: {detail}"
            # A wrong model id is the single most likely misconfiguration, so
            # say so plainly instead of leaving a 404 to be decoded by hand.
            if exc.code in (400, 404) and "model" in detail.lower():
                names = self.list_models()
                log.error("Groq rejected model %r. Set GROQ_MODEL to one of: %s",
                          self.model, ", ".join(names) or "(could not list models)")
            else:
                log.warning("Groq request failed (%s) — using offline result", self._last_error)
            base.rationale += f" [Groq unavailable: HTTP {exc.code}]"
            return base
        except Exception as exc:                          # noqa: BLE001
            # Any failure -- network, timeout, malformed JSON -- degrades to the
            # offline result rather than dropping the citizen's request.
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Groq analysis failed, using offline result: %s", self._last_error)
            base.rationale += f" [Groq unavailable: {type(exc).__name__}]"
            return base

        # Every field is validated and coerced before it is trusted.
        sector = r.get("sector") if r.get("sector") in SECTOR_CODES else "other"
        urgency = r.get("urgency") if r.get("urgency") in URGENCIES else "medium"
        try:
            confidence = max(0.0, min(1.0, float(r.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            population = max(0, int(r.get("affected_population", 0)))
        except (TypeError, ValueError):
            population = base.affected_population

        a = Analysis(
            language=str(r.get("language") or base.language)[:8],
            language_confidence=0.95,
            text_original=text,
            text_en=str(r.get("text_en") or base.text_en),
            sector=sector,
            sector_confidence=round(confidence, 3),
            sector_scores=base.sector_scores,
            urgency=urgency,
            urgency_score=URGENCY_SCORE[urgency],
            affected_population=population,
            population_basis=str(r.get("population_basis") or "model estimate"),
            entities=[str(e) for e in (r.get("entities") or [])][:6],
            # Redaction is the deterministic engine's output, never the model's.
            text_redacted=base.text_redacted,
            pii_types=base.pii_types,
            confidence=round(confidence, 3),
            engine=f"{self.name}:{self.model}",
            rationale=str(r.get("rationale") or "").strip(),
        )

        # Ensemble disagreement: two independent engines reaching different
        # sectors is a stronger signal of genuine ambiguity than either one's
        # self-reported confidence.
        disagree = (base.sector != "other" and base.sector_confidence > 0.6
                    and base.sector != a.sector)
        a.needs_review = a.confidence < self.REVIEW_THRESHOLD or disagree
        if disagree:
            a.rationale += (f" [FLAGGED: offline engine classified this as "
                            f"'{base.sector}' — engines disagree, human review required]")
        return a


def get_engine() -> AnalysisEngine:
    """Engine selection. Offline unless a Groq key is actually present."""
    if os.getenv("GROQ_API_KEY"):
        engine = GroqEngine()
        log.info("Using Groq engine (model=%s)", engine.model)
        return engine
    log.info("No GROQ_API_KEY set — using the offline engine "
             "(fully functional; set GROQ_API_KEY to add translation)")
    return HeuristicEngine()

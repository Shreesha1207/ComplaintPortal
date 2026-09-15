# -*- coding: utf-8 -*-
"""
Claude-backed request-understanding engine.

Used when `ANTHROPIC_API_KEY` is configured. It adds what the offline engine
genuinely cannot do: real translation into the pivot language, and correct
handling of the messy middle -- code-mixed, misspelt, dictated, or idiomatic
text where no keyword fires.

Four design decisions worth defending in review:

1. PII REDACTION IS NEVER DELEGATED TO THE MODEL. The deterministic regex pass
   runs on every request regardless of engine. A probabilistic system is a fine
   second line of defence and an unacceptable only line of defence. The prompt
   below also receives already-redacted text, so raw identifiers are never sent
   off-box in the first place.

2. THE HEURISTIC ENGINE STILL RUNS. Its result is the fallback on any error,
   and -- more useful -- a free second opinion: when the two engines disagree on
   sector, the request is flagged for human review even if both were individually
   confident. Cheap ensemble disagreement is a better review trigger than either
   engine's self-reported confidence.

3. EFFORT IS `low`. This is classification, not reasoning. Low effort is both
   the cheaper and the better-calibrated setting for this workload.

4. THE TAXONOMY IS CACHED. The sector list and instructions are identical on
   every request and sit before the citizen's text, so they are a stable cache
   prefix. At national volume this is the difference between a viable and a
   non-viable per-request cost.

For backfills and nightly re-classification, `analyse_batch` uses the Message
Batches API at roughly half the per-token cost. Nothing in the live intake path
depends on it.
"""
from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel, Field

from .base import Analysis, AnalysisEngine
from .heuristic import HeuristicEngine

log = logging.getLogger("agora.ai.llm")

# Opus 5 is the default. Override with AGORA_LLM_MODEL if a deployment chooses
# to trade capability for unit cost at national volume -- that is a policy
# decision for the operator, not a default we make for them.
DEFAULT_MODEL = os.getenv("AGORA_LLM_MODEL", "claude-opus-5")

SECTOR_CODES = ["water", "roads", "health", "education", "power", "transport",
                "housing", "digital", "agriculture", "jobs", "other"]

SYSTEM_PROMPT = """You classify citizen development requests for a national \
public-investment planning platform used across BRICS nations.

You will receive one citizen request in any language. Return structured data only.

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

RULES:
- affected_population: use an explicit number if the citizen states one \
(multiply households/families by 5). Otherwise infer from stated scale: \
village 1800, block 9000, ward 900, street 300. If nothing is stated, use 250.
- text_en: a faithful, plain English translation. Do not summarise, do not add \
detail the citizen did not give, do not editorialise.
- confidence: your genuine calibrated confidence that sector AND urgency are \
both correct. Be honest. A low score routes this to a human, which is the \
correct outcome when the request is ambiguous -- it is not a failure.
- Text may already contain [PHONE-REDACTED] style markers. Leave them as-is.
- Never infer caste, religion, ethnicity or political affiliation. Never let \
the perceived social group of the requester influence urgency or population."""


class _LLMResult(BaseModel):
    language: str = Field(description="ISO-639-1 code of the request language")
    text_en: str = Field(description="Faithful plain-English translation")
    sector: str = Field(description="One sector code")
    urgency: str = Field(description="critical, high, medium or low")
    affected_population: int = Field(description="Estimated people affected")
    population_basis: str = Field(description="How the estimate was reached")
    entities: list[str] = Field(description="Place names mentioned, may be empty")
    confidence: float = Field(description="Calibrated confidence 0.0-1.0")
    rationale: str = Field(description="One sentence: why this classification")


class LLMEngine(AnalysisEngine):
    """Claude adapter with automatic degradation to the offline engine."""

    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, fallback: AnalysisEngine | None = None):
        self.model = model
        self.fallback = fallback or HeuristicEngine()
        self._client = None
        self._unavailable_reason: str | None = None

    # ------------------------------------------------------------------
    @property
    def client(self):
        if self._client is None and self._unavailable_reason is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic()
            except ImportError:
                self._unavailable_reason = "anthropic SDK not installed"
            except Exception as exc:                      # noqa: BLE001
                self._unavailable_reason = f"client init failed: {exc}"
        return self._client

    def available(self) -> bool:
        has_cred = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        return has_cred and self.client is not None

    def health(self) -> dict:
        return {
            "engine": self.name,
            "available": self.available(),
            "model": self.model,
            "reason": self._unavailable_reason,
        }

    # ------------------------------------------------------------------
    def analyse(self, text: str, *, hint_language: str | None = None,
                country: str = "IN") -> Analysis:
        # Always compute the offline result: it is both the fallback and the
        # second opinion used for the disagreement check below.
        base = self.fallback.analyse(text, hint_language=hint_language, country=country)
        if not self.available() or not text.strip():
            return base

        try:
            resp = self.client.messages.parse(
                model=self.model,
                max_tokens=1024,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "low"},
                messages=[{"role": "user",
                           # Send the redacted text, never the raw text.
                           "content": f"Citizen request:\n\n{base.text_redacted}"}],
                output_format=_LLMResult,
            )
            r = resp.parsed_output
        except Exception as exc:                          # noqa: BLE001
            # Any failure -- network, rate limit, schema, refusal -- degrades to
            # the offline result rather than dropping the citizen's request.
            log.warning("LLM analysis failed, using heuristic result: %s", exc)
            base.rationale += f" [LLM unavailable: {type(exc).__name__}]"
            return base

        sector = r.sector if r.sector in SECTOR_CODES else "other"
        urgency = r.urgency if r.urgency in {"critical", "high", "medium", "low"} else "medium"

        a = Analysis(
            language=r.language or base.language,
            language_confidence=0.95,
            text_original=text,
            text_en=r.text_en,
            sector=sector,
            sector_confidence=round(float(r.confidence), 3),
            sector_scores=base.sector_scores,
            urgency=urgency,
            urgency_score={"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.15}[urgency],
            affected_population=max(0, int(r.affected_population)),
            population_basis=r.population_basis,
            entities=list(r.entities or [])[:6],
            # Redaction is the deterministic engine's output, never the model's.
            text_redacted=base.text_redacted,
            pii_types=base.pii_types,
            confidence=round(float(r.confidence), 3),
            engine=self.name,
            rationale=r.rationale,
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

    # ------------------------------------------------------------------
    def analyse_batch(self, texts: list[str], *, country: str = "IN") -> list[Analysis]:
        """Bulk re-classification via the Message Batches API (~50% cost).

        For backfills and nightly re-runs after a taxonomy change -- never in
        the live intake path, which must stay synchronous and degradable.
        """
        if not self.available():
            return [self.fallback.analyse(t, country=country) for t in texts]
        try:
            from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
            from anthropic.types.messages.batch_create_params import Request
        except ImportError:
            return [self.analyse(t, country=country) for t in texts]

        bases = [self.fallback.analyse(t, country=country) for t in texts]
        schema = _LLMResult.model_json_schema()
        schema["additionalProperties"] = False
        requests = [
            Request(
                custom_id=f"req-{i}",
                params=MessageCreateParamsNonStreaming(
                    model=self.model, max_tokens=1024,
                    system=[{"type": "text", "text": SYSTEM_PROMPT,
                             "cache_control": {"type": "ephemeral"}}],
                    output_config={"effort": "low",
                                   "format": {"type": "json_schema", "schema": schema}},
                    messages=[{"role": "user",
                               "content": f"Citizen request:\n\n{b.text_redacted}"}],
                ),
            )
            for i, b in enumerate(bases)
        ]
        batch = self.client.messages.batches.create(requests=requests)
        log.info("Submitted batch %s with %d requests", batch.id, len(requests))
        # Caller polls `batches.retrieve(batch.id).processing_status` and keys
        # results by custom_id -- results arrive in arbitrary order.
        return bases


def get_engine() -> AnalysisEngine:
    """Engine selection. Offline unless credentials are actually present."""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        engine = LLMEngine()
        if engine.available():
            log.info("Using Claude engine (%s)", engine.model)
            return engine
        log.warning("Credentials set but Claude engine unavailable (%s); using offline engine",
                    engine._unavailable_reason)
    return HeuristicEngine()

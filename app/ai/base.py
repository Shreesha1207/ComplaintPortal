# -*- coding: utf-8 -*-
"""Adapter contract for the request-understanding layer.

Every engine -- offline heuristic, hosted LLM, or a national language lab's own
model -- returns the same `Analysis`. That is what lets a country swap the
intelligence layer without touching the prioritisation engine or the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Analysis:
    """Normalised output of request understanding."""

    language: str = "en"                 # ISO-639-1 detected
    language_confidence: float = 0.0
    text_original: str = ""
    text_en: str = ""                    # pivot-language rendering
    sector: str = "other"
    sector_confidence: float = 0.0
    sector_scores: dict[str, float] = field(default_factory=dict)
    urgency: str = "medium"              # critical | high | medium | low
    urgency_score: float = 0.4           # 0..1, feeds the priority engine
    affected_population: int = 0
    population_basis: str = "default"    # how the estimate was reached
    entities: list[str] = field(default_factory=list)
    text_redacted: str = ""
    pii_types: list[str] = field(default_factory=list)
    confidence: float = 0.0              # overall; gates the review queue
    needs_review: bool = False
    engine: str = "heuristic"
    rationale: str = ""                  # human-readable "why", for audit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnalysisEngine:
    """Base class. Subclasses implement `analyse`."""

    name = "base"

    # Below this, a request is held for human confirmation rather than being
    # allowed to influence a funding recommendation.
    REVIEW_THRESHOLD = 0.55

    def analyse(self, text: str, *, hint_language: str | None = None,
                country: str = "IN") -> Analysis:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {"engine": self.name, "available": True}

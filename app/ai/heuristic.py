# -*- coding: utf-8 -*-
"""
Offline, deterministic request-understanding engine.

Handles language identification, sector classification, urgency grading,
affected-population estimation and PII redaction with no network call and no
model weights. It is the default engine: the platform must work in a village
with a 2G uplink and in a demo room with no uplink at all.

Its honest limitation: it does keyword-level normalisation, not translation.
`text_en` is a structured gloss ("water supply / sanitation -- reported as
critical"), not a fluent rendering of the citizen's sentence. When the LLM
adapter is configured, real translation replaces the gloss. The classification
itself -- which is what actually drives funding priority -- is produced the
same way in both paths and stays auditable.
"""
from __future__ import annotations

import math
import re
import unicodedata

from .base import Analysis, AnalysisEngine
from .lexicon import (
    GLOSS, MARKERS, PII_PATTERNS, SCALE_LEXICON, SCRIPT_RANGES,
    SECTOR_LEXICON, URGENCY_LEXICON,
)

URGENCY_WEIGHT = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.15}

# Confidence shaping. Tuned so that one unambiguous keyword ("पानी", "amanzi")
# lands around 0.72 -- above the review threshold -- while a single weak match
# contested by another sector falls below it and is escalated to a human.
_EVIDENCE_SCALE = 0.85


def _stem(term: str) -> str:
    """Truncate an inflecting term to a safe stem.

    Substring matching alone misses 'электричества' for 'электричество' and
    'महीने' for 'महीनों'. Dropping the final ~25% of a sufficiently long term
    recovers those without a morphological analyser. Short terms are left
    intact -- truncating them would cause false positives."""
    if len(term) < 6:
        return term
    return term[:max(4, int(len(term) * 0.75))]


def _matches(term: str, haystack: str) -> bool:
    t = term.lower()
    if t in haystack:
        return True
    st = _stem(t)
    return st != t and st in haystack
_NUM_RE = re.compile(r"(\d[\d,]*)\s*(families|households|people|persons|परिवार|लोग|ಕುಟುಂಬ|"
                     r"குடும்ப|కుటుంబ|পরিবার|famílias|familias|imindeni|gesinne|семей|户|人)",
                     re.IGNORECASE)


class HeuristicEngine(AnalysisEngine):
    name = "heuristic"

    # ---------------- language -------------------------------------------
    def detect_language(self, text: str) -> tuple[str, float]:
        counts: dict[str, int] = {}
        letters = 0
        for ch in text:
            if not ch.isalpha():
                continue
            letters += 1
            cp = ord(ch)
            for (lo, hi), langs in SCRIPT_RANGES:
                if lo <= cp <= hi:
                    counts[langs[0]] = counts.get(langs[0], 0) + 1
                    break
            else:
                counts["_latin"] = counts.get("_latin", 0) + 1

        if not letters:
            return "en", 0.0

        top = max(counts, key=lambda k: counts[k])
        share = counts[top] / letters
        low = text.lower()

        def best_marker(cands: list[str], floor: float) -> tuple[str, float]:
            scores = {c: sum(1 for m in MARKERS.get(c, []) if m in low) for c in cands}
            win = max(scores, key=lambda k: scores[k])
            if scores[win] == 0:
                return cands[0], floor
            spread = scores[win] - sorted(scores.values())[-2] if len(scores) > 1 else scores[win]
            return win, min(0.97, floor + 0.12 * scores[win] + 0.05 * spread)

        if top == "_latin":
            return best_marker(["en", "pt", "es", "af", "zu", "xh", "st"], 0.42)
        # Scripts shared by more than one language need a marker pass too.
        for (_, langs) in SCRIPT_RANGES:
            if langs[0] == top and len(langs) > 1:
                return best_marker(langs, 0.70 + 0.2 * share)
        return top, min(0.99, 0.70 + 0.29 * share)

    # ---------------- sector ---------------------------------------------
    def classify_sector(self, text: str, lang: str) -> tuple[str, float, dict[str, float], list[str]]:
        low = text.lower()
        scores: dict[str, float] = {}
        hits: dict[str, list[str]] = {}
        # Always also scan English terms: code-mixing is the norm, not the
        # exception ("gaon me water supply nahi hai").
        langs = [lang] if lang == "en" else [lang, "en"]
        for sector, by_lang in SECTOR_LEXICON.items():
            s, h = 0.0, []
            for lg in langs:
                for term in by_lang.get(lg, []):
                    if _matches(term, low):
                        # Longer, more specific terms carry more evidence.
                        s += 1.0 + 0.08 * len(term.split())
                        h.append(term)
            if s:
                scores[sector] = round(s, 3)
                hits[sector] = h
        if not scores:
            return "other", 0.0, {}, []

        best = max(scores, key=lambda k: scores[k])
        total = sum(scores.values())
        margin = scores[best] / total
        # Confidence rewards both absolute evidence and separation from runners-up.
        conf = min(0.97, (1 - math.exp(-scores[best] / _EVIDENCE_SCALE)) * (0.55 + 0.45 * margin))
        return best, round(conf, 3), scores, hits[best]

    # ---------------- urgency --------------------------------------------
    def grade_urgency(self, text: str, lang: str) -> tuple[str, float, list[str]]:
        low = text.lower()
        langs = {lang, "en"}
        found: dict[str, list[str]] = {}
        for tier, by_lang in URGENCY_LEXICON.items():
            for lg in langs:
                for term in by_lang.get(lg, []):
                    if _matches(term, low):
                        found.setdefault(tier, []).append(term)
        if "critical" in found:
            return "critical", URGENCY_WEIGHT["critical"], found["critical"]
        if "high" in found:
            # Several independent "high" signals escalate.
            bump = min(0.22, 0.05 * (len(found["high"]) - 1))
            return "high", round(URGENCY_WEIGHT["high"] + bump, 3), found["high"]
        if "low" in found:
            return "low", URGENCY_WEIGHT["low"], found["low"]
        return "medium", URGENCY_WEIGHT["medium"], []

    # ---------------- affected population --------------------------------
    def estimate_population(self, text: str) -> tuple[int, str]:
        low = text.lower()
        m = _NUM_RE.search(low)
        if m:
            n = int(m.group(1).replace(",", ""))
            unit = m.group(2).lower()
            if unit.startswith(("famil", "household", "परिवार", "ಕುಟುಂಬ", "கு", "కు", "পরিবার", "семей", "户")):
                return n * 5, f"stated: {n} families x 5 persons"
            return n, f"stated: {n} people"
        # Fall back to the largest geographic scale the text implies.
        best, basis = 0, "default (no scale stated)"
        for scale, (terms, size) in SCALE_LEXICON.items():
            if any(_matches(t, low) for t in terms) and size > best:
                best, basis = size, f"inferred scale: {scale}"
        return (best, basis) if best else (250, "default (no scale stated)")

    # ---------------- PII -------------------------------------------------
    def redact(self, text: str) -> tuple[str, list[str]]:
        out, kinds = text, []
        for pattern, replacement in PII_PATTERNS:
            new = re.sub(pattern, replacement, out)
            if new != out:
                kinds.append(replacement.strip("[]").replace("-REDACTED", "").lower())
                out = new
        return out, kinds

    # ---------------- entities -------------------------------------------
    def extract_entities(self, text: str) -> list[str]:
        """Capitalised runs in Latin script + any token adjacent to a place
        preposition. Deliberately shallow -- the authoritative location comes
        from the citizen's own district selection, never from free text."""
        ents = re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", text)
        stop = {"The", "This", "We", "Our", "There", "Please", "It", "They", "No", "Since"}
        return [e for e in dict.fromkeys(ents) if e.split()[0] not in stop][:6]

    # ---------------- orchestration --------------------------------------
    def analyse(self, text: str, *, hint_language: str | None = None,
                country: str = "IN") -> Analysis:
        text = unicodedata.normalize("NFC", (text or "").strip())
        a = Analysis(engine=self.name, text_original=text)
        if not text:
            a.rationale = "Empty submission."
            a.needs_review = True
            return a

        if hint_language:
            a.language, a.language_confidence = hint_language, 0.99
        else:
            a.language, a.language_confidence = self.detect_language(text)

        sector, sconf, scores, hits = self.classify_sector(text, a.language)
        a.sector, a.sector_confidence, a.sector_scores = sector, sconf, scores

        a.urgency, a.urgency_score, ucues = self.grade_urgency(text, a.language)
        a.affected_population, a.population_basis = self.estimate_population(text)
        a.entities = self.extract_entities(text)
        a.text_redacted, a.pii_types = self.redact(text)

        gloss = GLOSS.get(sector, "unclassified request")
        a.text_en = (f"[{a.language}] {gloss} — reported as {a.urgency}"
                     f"; ~{a.affected_population:,} people affected.")

        # Overall confidence: classification strength, discounted when the
        # language itself is uncertain and when the text is too short to judge.
        brevity = min(1.0, len(text) / 60.0)
        a.confidence = round(sconf * (0.65 + 0.35 * a.language_confidence) * (0.55 + 0.45 * brevity), 3)
        a.needs_review = a.confidence < self.REVIEW_THRESHOLD

        parts = [f"Language '{a.language}' ({a.language_confidence:.0%} conf)"]
        parts.append(f"matched {sector} on: {', '.join(hits[:5])}" if hits
                     else "no sector keyword matched — unclassified")
        if ucues:
            parts.append(f"urgency '{a.urgency}' from: {', '.join(ucues[:4])}")
        else:
            parts.append("urgency defaulted to medium (no urgency cue)")
        parts.append(f"reach {a.affected_population:,} ({a.population_basis})")
        if a.pii_types:
            parts.append(f"redacted PII: {', '.join(a.pii_types)}")
        if a.needs_review:
            parts.append("BELOW CONFIDENCE THRESHOLD — queued for human review")
        a.rationale = "; ".join(parts) + "."
        return a

# -*- coding: utf-8 -*-
"""
Explainable prioritisation engine.

This is the part that makes this a decision-support system rather than a
complaint tracker, so the reasoning is written out in full.

THE SCORE
---------
For every (district, sector) cell:

    Index = 100 · (w_D·D + w_G·G + w_P·P + w_S·S + w_V·V)  (weights sum to 1)

    D  Demand      citizen request intensity per capita, equity-corrected,
                   rank-normalised across all cells
    G  Gap         (benchmark − infrastructure index) / benchmark
    P  People      log-scaled affected population
    S  Severity    mean urgency of the requests received
    V  Vulnerability  poverty, low literacy and digital exclusion

WHAT THE INDEX IS NOT
---------------------
It is a measure of unmet need, and nothing else. It carries no budget, no
committed investment and no cost, so it cannot be read as "what this would cost
to fix" or "what is already being spent here". Deciding what to do about a need
is a separate job that this platform deliberately does not do.

THE EQUITY CORRECTION -- the single most important design choice here
--------------------------------------------------------------------
Raw request counts measure the ability to complain, not the need to be served.
A district where 80% own smartphones and 85% are literate will out-file a
district at 25% and 55% many times over, at identical real need. Ranking on raw
demand therefore routes public money toward the already-served and calls it
evidence-based. That is worse than useless -- it launders inequity as data.

So demand is divided by an expected-participation index built from smartphone
penetration, literacy and urbanisation. Twenty requests from a low-reach
district count for more than twenty from a high-reach one, because they
represent a larger submerged population.

WHAT THE CORRECTION CANNOT FIX -- and how the design compensates
----------------------------------------------------------------
Dividing by reach does nothing when the numerator is zero. A district that
sends no requests stays at D = 0 no matter how it is weighted. This is a real
and unavoidable limit of any demand-driven system, and it is exactly why D is
only 30% of the score: G, P and V are computed from administrative data and
need no citizen to speak at all. A silent district with a severe deficit still
surfaces on the strength of those terms, and is explicitly flagged
`silent_district` so it reads as "we have not heard from here" rather than
"there is no need here". Outreach, not deprioritisation, is the correct
response to silence.

EVERY NUMBER IS ATTRIBUTABLE
----------------------------
Each cell carries per-factor contributions that sum exactly to its index and a
plain-language rationale. Anyone challenged on a ranking can say which term
produced it and what would change it.
"""
from __future__ import annotations

import math

# Default weights. Exposed through the API so a country can retune them to its
# own policy priorities without forking the code -- the transparency matters
# more than the specific numbers.
DEFAULT_WEIGHTS = {
    "demand": 0.30,
    "gap": 0.25,
    "people": 0.15,
    "severity": 0.15,
    "vulnerability": 0.15,
}

FACTOR_LABELS = {
    "demand": "Citizen demand (equity-corrected)",
    "gap": "Infrastructure deficit vs national benchmark",
    "people": "Population affected",
    "severity": "Severity of reported need",
    "vulnerability": "Socio-economic vulnerability",
}


def _rank_normalize(values: list[float]) -> list[float]:
    """Map values to 0..1 by rank. Robust to the heavy tails that raw request
    counts always have, where a handful of cells would otherwise compress
    everything else toward zero under min-max scaling."""
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        # Average rank across ties so equal inputs get equal scores.
        rank = (i + j) / 2.0
        norm = rank / (n - 1) if n > 1 else 0.0
        for k in range(i, j + 1):
            out[order[k]] = norm
        i = j + 1
    # Cells with no demand at all must score exactly 0, not a floor from ties.
    return [0.0 if values[i] == 0 else out[i] for i in range(n)]


def participation_index(cell) -> float:
    """Expected propensity to file a request, independent of actual need."""
    return max(0.12, min(1.0,
                         0.50 * cell.smartphone_pen / 100.0
                         + 0.30 * cell.literacy / 100.0
                         + 0.20 * cell.urbanization / 100.0))


def score_cells(cells: list, weights: dict | None = None) -> list[dict]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total_w = sum(w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}          # always renormalise

    # --- factor inputs -------------------------------------------------
    densities, max_pop, max_pov = [], 1, 1.0
    for c in cells:
        per_capita = c.demand_weight / max(c.population, 1) * 100_000
        densities.append(per_capita / participation_index(c))
        max_pop = max(max_pop, c.population)
        max_pov = max(max_pov, c.poverty_share)

    demand_norm = _rank_normalize(densities)
    log_max = math.log10(1 + max_pop)

    out = []
    for c, d_norm, raw_density in zip(cells, demand_norm, densities):
        gap = max(0.0, min(1.0, (c.benchmark - c.infra_index) / c.benchmark))
        people = math.log10(1 + c.population) / log_max
        severity = c.urgency_mean if c.request_count else 0.0
        vulnerability = min(1.0,
                            0.50 * (c.poverty_share / max_pov)
                            + 0.30 * (1 - c.literacy / 100.0)
                            + 0.20 * (1 - c.smartphone_pen / 100.0))

        factors = {"demand": d_norm, "gap": gap, "people": people,
                   "severity": severity, "vulnerability": vulnerability}
        need = sum(w[k] * factors[k] for k in w)
        index = 100.0 * need

        contributions = {k: round(100.0 * w[k] * factors[k], 2) for k in w}

        # --- flags -----------------------------------------------------
        # Loud and badly served. This used to be called a blind spot, when it
        # also required that no money was committed here; with investment data
        # out of the platform the money half of that test no longer exists, so
        # the flag says only what it can still actually see.
        unmet_need = d_norm >= 0.60 and gap >= 0.50
        silent = c.request_count == 0 and gap >= 0.50 and vulnerability >= 0.50

        # --- ranking confidence ----------------------------------------
        # Distinct from the AI's per-request confidence: this is how much a
        # reader should trust THIS cell's ranking.
        sample = min(1.0, c.request_count / 12.0)
        ai_conf = c.ai_confidence_mean if c.request_count else 0.0
        if c.request_count == 0:
            # Administrative data only. Genuinely usable, but weaker evidence
            # than the same conclusion corroborated by citizen reports.
            rec_conf = 0.45
        else:
            rec_conf = 0.40 * sample + 0.30 * ai_conf + 0.30
        rec_conf = round(min(0.97, rec_conf), 3)

        out.append({
            "country": c.country,
            "region_code": c.region_code, "region_name": c.region_name,
            "district_code": c.district_code, "district_name": c.district_name,
            "sector": c.sector, "sector_name": c.sector_name,
            "population": c.population,
            "priority_index": round(index, 2),
            "need_score": round(need, 4),
            "factors": {k: round(v, 4) for k, v in factors.items()},
            "contributions": contributions,
            "weights": {k: round(v, 4) for k, v in w.items()},
            "request_count": c.request_count,
            "critical_count": c.critical_count,
            "demand_density": round(raw_density, 3),
            "participation_index": round(participation_index(c), 3),
            "infra_index": c.infra_index,
            "benchmark": c.benchmark,
            "gap_pct": round(gap * 100, 1),
            "languages": sorted(c.languages),
            "channels": sorted(c.channels),
            "unmet_need": unmet_need,
            "silent_district": silent,
            "confidence": rec_conf,
            "rationale": _explain(c, factors, contributions, index,
                                  unmet_need, silent),
        })
    out.sort(key=lambda r: r["priority_index"], reverse=True)
    return out


def _explain(c, factors, contributions, index, unmet_need, silent) -> str:
    top = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)[:2]
    bits = [f"Priority {index:.0f}/100 for {c.sector_name.lower()} in {c.district_name}"
            f" ({c.region_name})."]
    bits.append("Driven mainly by " + " and ".join(
        f"{FACTOR_LABELS[k].lower()} ({v:.0f} pts)" for k, v in top) + ".")
    if c.request_count:
        n, nl = c.request_count, len(c.languages)
        langs = f" in {nl} language{'' if nl == 1 else 's'}" if c.languages else ""
        bits.append(f"{n} citizen request{'' if n == 1 else 's'}{langs}"
                    + (f", {c.critical_count} critical" if c.critical_count else "") + ".")
    bits.append(f"Infrastructure index {c.infra_index:.0f} against a benchmark of "
                f"{c.benchmark:.0f} — a {factors['gap'] * 100:.0f}% deficit"
                f" affecting {c.population:,} people.")
    if unmet_need:
        bits.append("FLAGGED UNMET NEED: strong citizen demand against a severe "
                    "infrastructure deficit.")
    if silent:
        bits.append("FLAGGED SILENT DISTRICT: severe deficit and high vulnerability but "
                    "no citizen requests received — treat as an outreach gap, not an "
                    "absence of need.")
    return " ".join(bits)


# ----------------------------------------------------------------------
def rollup_districts(scored: list[dict]) -> list[dict]:
    """District score = mean of its three highest sector priorities, i.e. how
    severe this district's worst unmet needs are. A plain sum would reward
    breadth over severity and let ten mild gaps outrank one crisis."""
    by_d: dict[str, list[dict]] = {}
    for r in scored:
        by_d.setdefault(r["district_code"], []).append(r)
    out = []
    for code, rows in by_d.items():
        rows.sort(key=lambda r: r["priority_index"], reverse=True)
        top = rows[:3]
        out.append({
            "district_code": code,
            "district_name": rows[0]["district_name"],
            "region_code": rows[0]["region_code"],
            "region_name": rows[0]["region_name"],
            "population": rows[0]["population"],
            "priority_index": round(sum(r["priority_index"] for r in top) / len(top), 2),
            "top_sectors": [{"sector": r["sector"], "sector_name": r["sector_name"],
                             "priority_index": r["priority_index"]} for r in top],
            "request_count": sum(r["request_count"] for r in rows),
            "unmet_needs": sum(1 for r in rows if r["unmet_need"]),
            "silent": sum(1 for r in rows if r["silent_district"]),
        })
    out.sort(key=lambda r: r["priority_index"], reverse=True)
    return out


def rollup_regions(districts: list[dict]) -> list[dict]:
    """Population-weighted, so a region is not dragged up by one small district."""
    by_r: dict[str, list[dict]] = {}
    for d in districts:
        by_r.setdefault(d["region_code"], []).append(d)
    out = []
    for code, rows in by_r.items():
        pop = sum(r["population"] for r in rows) or 1
        out.append({
            "region_code": code,
            "region_name": rows[0]["region_name"],
            "population": pop,
            "priority_index": round(
                sum(r["priority_index"] * r["population"] for r in rows) / pop, 2),
            "districts": len(rows),
            "request_count": sum(r["request_count"] for r in rows),
            "unmet_needs": sum(r["unmet_needs"] for r in rows),
            "silent": sum(r["silent"] for r in rows),
            "top_district": max(rows, key=lambda r: r["priority_index"])["district_name"],
        })
    out.sort(key=lambda r: r["priority_index"], reverse=True)
    return out

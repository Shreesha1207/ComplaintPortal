# -*- coding: utf-8 -*-
"""
Budget allocation simulator.

Turns a ranked list into an answerable question: given this envelope, what
would we actually fund, and what would it buy?

Two strategies are offered because they genuinely disagree, and the
disagreement is the point:

  `priority`  Fund strictly worst-first. Reaches the most acute need; costs
              more per person because the worst-off districts are often
              remote, small, and expensive to serve.

  `value`     Fund by priority per unit of capital. Reaches far more people per
              unit spent, and will systematically skip the hardest cases.

Neither is correct. Presenting both, with the tradeoff quantified, is more
honest than hard-coding one and calling the output "optimal". A third strategy,
`blind_spots`, restricts to cells with demand and deficit but no committed
money -- useful as a targeted corrective rather than a general allocation rule.
"""
from __future__ import annotations

STRATEGIES = {
    "priority": "Worst-first — maximise severity of need addressed",
    "value": "Value-for-money — maximise need addressed per unit of capital",
    "blind_spots": "Blind spots only — fund unmet demand with no committed money",
}


def allocate(scored: list[dict], envelope: float, strategy: str = "priority",
             min_ticket: float = 0.01) -> dict:
    """Greedily allocate `envelope` (in the country's budget unit).

    Greedy is the right algorithm here, not a placeholder: the ranking is the
    policy, so spending strictly down it is what a policymaker is actually
    asking to simulate. Partial funding is allowed on the last item rather than
    leaving the remainder unspent, matching how a real envelope is closed out.
    """
    if strategy not in STRATEGIES:
        strategy = "priority"

    pool = [r for r in scored if r["unfunded"] > min_ticket]
    if strategy == "blind_spots":
        pool = [r for r in pool if r["blind_spot"]]
        pool.sort(key=lambda r: r["priority_index"], reverse=True)
    elif strategy == "value":
        pool.sort(key=lambda r: r["priority_index"] / max(r["unfunded"], min_ticket),
                  reverse=True)
    else:
        pool.sort(key=lambda r: r["priority_index"], reverse=True)

    remaining = float(envelope)
    funded, districts, by_sector = [], {}, {}
    blind_resolved = silent_resolved = 0
    gap_closed_weighted = 0.0

    for r in pool:
        if remaining <= min_ticket:
            break
        ask = r["unfunded"]
        grant = min(ask, remaining)
        share = grant / ask if ask > 0 else 0.0
        remaining -= grant

        funded.append({
            "district_code": r["district_code"], "district_name": r["district_name"],
            "region_name": r["region_name"], "sector": r["sector"],
            "sector_name": r["sector_name"], "priority_index": r["priority_index"],
            "allocated": round(grant, 2), "required": r["unfunded"],
            "share_funded": round(share, 3), "population": r["population"],
            "gap_pct": r["gap_pct"], "blind_spot": r["blind_spot"],
            "silent_district": r["silent_district"],
        })
        # Count each district's population once, however many sectors it wins.
        districts[r["district_code"]] = r["population"]
        by_sector[r["sector"]] = by_sector.get(r["sector"], 0.0) + grant
        if r["blind_spot"]:
            blind_resolved += 1
        if r["silent_district"]:
            silent_resolved += 1
        gap_closed_weighted += r["gap_pct"] * share * r["population"]

    people = sum(districts.values())
    allocated = float(envelope) - remaining
    total_unfunded = sum(r["unfunded"] for r in scored)

    return {
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy],
        "envelope": round(float(envelope), 2),
        "allocated": round(allocated, 2),
        "unspent": round(remaining, 2),
        "projects_funded": len(funded),
        "districts_covered": len(districts),
        "population_reached": people,
        "cost_per_person": round(allocated * 1.0 / people, 6) if people else 0.0,
        "blind_spots_resolved": blind_resolved,
        "silent_districts_reached": silent_resolved,
        "mean_gap_closed_pct": round(gap_closed_weighted / people, 1) if people else 0.0,
        "national_unfunded": round(total_unfunded, 2),
        "envelope_share_of_need": round(allocated / total_unfunded, 4) if total_unfunded else 0.0,
        "by_sector": {k: round(v, 2) for k, v in
                      sorted(by_sector.items(), key=lambda kv: kv[1], reverse=True)},
        "funded": funded,
    }


def compare_strategies(scored: list[dict], envelope: float) -> dict:
    """Run every strategy on the same envelope so the tradeoff is visible."""
    return {s: {k: v for k, v in allocate(scored, envelope, s).items() if k != "funded"}
            for s in STRATEGIES}

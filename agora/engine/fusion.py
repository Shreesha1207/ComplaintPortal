# -*- coding: utf-8 -*-
"""
Data fusion: join citizen demand to demographic, infrastructure and investment
data, producing one row per (district, sector) -- the unit of analysis the
prioritisation engine scores and the unit a government actually funds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent / "packs"


@dataclass
class Cell:
    """One (district, sector) pair with every input the scorer needs."""
    country: str
    region_code: str
    region_name: str
    district_code: str
    district_name: str
    sector: str
    sector_name: str
    population: int

    # citizen demand
    request_count: int = 0
    demand_weight: float = 0.0        # Σ urgency × verification
    urgency_mean: float = 0.0
    critical_count: int = 0
    ai_confidence_mean: float = 0.0
    languages: set = field(default_factory=set)
    channels: set = field(default_factory=set)

    # context
    infra_index: float = 0.0          # 0..100, higher = better served
    benchmark: float = 80.0
    literacy: float = 0.0
    urbanization: float = 0.0
    poverty_share: float = 0.0
    smartphone_pen: float = 0.0

    # investment
    committed: float = 0.0            # currency units (crore / million)
    project_count: int = 0
    cost_weight: float = 1.0


class CountryPack:
    """Loads and indexes one country pack."""

    def __init__(self, path: Path):
        self.raw = json.loads(path.read_text(encoding="utf-8"))
        self.code = self.raw["code"]
        self.name = self.raw["name"]
        self.currency = self.raw["currency"]
        self.sectors = {s["code"]: s for s in self.raw["sectors"]}
        self.languages = self.raw["languages"]
        self.admin_levels = self.raw["admin_levels"]
        self.map = self.raw["map"]
        self.data_notice = self.raw["data_notice"]
        self.regions = self.raw["regions"]

        self.district_by_code, self.region_by_code = {}, {}
        for r in self.regions:
            self.region_by_code[r["code"]] = r
            for d in r["districts"]:
                self.district_by_code[d["code"]] = (r, d)

        # Only planned and ongoing money reduces an unmet gap. Completed
        # projects are already reflected in the infrastructure index -- counting
        # them again would discount the same spend twice.
        self.investments = self.raw["investments"]
        self.committed_by_key: dict[tuple[str, str], float] = {}
        self.projects_by_key: dict[tuple[str, str], list] = {}
        for inv in self.investments:
            if inv["status"] == "completed":
                continue
            key = (inv["district"], inv["sector"])
            self.committed_by_key[key] = self.committed_by_key.get(key, 0.0) + inv["budget"]
            self.projects_by_key.setdefault(key, []).append(inv)

    def districts(self):
        for r in self.regions:
            for d in r["districts"]:
                yield r, d


@lru_cache(maxsize=8)
def load_pack(code: str) -> CountryPack:
    path = PACK_DIR / f"{code.upper()}.json"
    if not path.exists():
        raise FileNotFoundError(f"No country pack for '{code}'")
    return CountryPack(path)


def available_countries() -> list[dict]:
    out = []
    for p in sorted(PACK_DIR.glob("*.json")):
        pack = load_pack(p.stem)
        out.append({"code": pack.code, "name": pack.name,
                    "regions": len(pack.regions),
                    "districts": len(pack.district_by_code),
                    "languages": len(pack.languages)})
    return out


def build_matrix(pack: CountryPack, requests: list[dict]) -> list[Cell]:
    """Join requests onto the full (district × sector) grid.

    The grid is built exhaustively rather than from the requests, so a district
    that has sent no requests at all still produces rows. That is deliberate:
    silence is a signal the platform must be able to see, not an absence that
    quietly drops out of the analysis.
    """
    cells: dict[tuple[str, str], Cell] = {}
    for region, district in pack.districts():
        for scode, sector in pack.sectors.items():
            cells[(district["code"], scode)] = Cell(
                country=pack.code,
                region_code=region["code"], region_name=region["name"],
                district_code=district["code"], district_name=district["name"],
                sector=scode, sector_name=sector["name"],
                population=district["population"],
                infra_index=district["infra"][scode],
                benchmark=sector["benchmark"],
                cost_weight=sector.get("cost_weight", 1.0),
                literacy=district["demographics"]["literacy"],
                urbanization=district["demographics"]["urbanization"],
                poverty_share=district["demographics"]["poverty_share"],
                smartphone_pen=district["demographics"]["smartphone_pen"],
                committed=pack.committed_by_key.get((district["code"], scode), 0.0),
                project_count=len(pack.projects_by_key.get((district["code"], scode), [])),
            )

    for r in requests:
        key = (r.get("district_code"), r.get("sector"))
        cell = cells.get(key)
        if cell is None:
            continue
        # A request that a human has confirmed counts fully; one the AI is
        # unsure about counts partially. Nothing is silently discarded.
        status = r.get("status", "new")
        verification = {"verified": 1.0, "new": 0.85, "review": 0.5, "rejected": 0.0}.get(status, 0.85)
        if verification == 0.0:
            continue
        cell.request_count += 1
        cell.demand_weight += float(r.get("urgency_score", 0.4)) * verification
        cell.urgency_mean += float(r.get("urgency_score", 0.4))
        cell.ai_confidence_mean += float(r.get("ai_confidence", 0.5))
        if r.get("urgency") == "critical":
            cell.critical_count += 1
        if r.get("language"):
            cell.languages.add(r["language"])
        if r.get("channel"):
            cell.channels.add(r["channel"])

    for cell in cells.values():
        if cell.request_count:
            cell.urgency_mean /= cell.request_count
            cell.ai_confidence_mean /= cell.request_count
    return list(cells.values())

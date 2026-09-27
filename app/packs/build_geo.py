# -*- coding: utf-8 -*-
"""
Build the map geometry files the choropleth draws: `app/packs/<CODE>.geo.json`.

WHY THE GEOMETRY IS NOT IN THE COUNTRY PACK
-------------------------------------------
Two reasons, and both matter.

The pack JSONs are hand-maintained and every stored request references a
district code inside one, so they are edited surgically and never regenerated
(see `build_packs.py`, which cannot reproduce the codes already committed).
Geometry is the opposite: it is bulk data derived from an external source and
regenerating it is the *normal* way to change it. Keeping the two in separate
files means running this script can never touch a district code.

And `GET /api/countries/{code}` is fetched by every page on load. Geometry is
two orders of magnitude larger than the rest of the pack, so it is served from
its own endpoint that a browser can cache independently.

SOURCE AND LICENCE
------------------
Natural Earth 1:10m Admin 1 -- States, Provinces, which is in the **public
domain**: "no permission needed", no attribution required, no share-alike
clause. For a Digital Public Good that is not a detail. The alternatives with
finer detail (Census/DataMeet derivatives) carry ODbL or CC-BY-SA terms that
would attach to this repository, and one of them is 34MB.

    https://github.com/nvkelso/natural-earth-vector
    geojson/ne_10m_admin_1_states_provinces.geojson

WHAT THIS PRODUCES, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------
One outline per *region* -- the pack's admin level 1, so states and union
territories for India, states for Brazil, provinces for South Africa. Rings are
lon/lat, simplified with Ramer-Douglas-Peucker and quantised to 1e-3 degrees
(~110m), which is far finer than a 900px map can draw.

There is **no district geometry**, and that is a decision rather than a gap. The
packs carry a sample of districts (146 of India's ~780), so district polygons
would draw a map with holes in it and invite the reader to conclude that the
missing districts reported nothing. Regions are complete in every pack, so the
choropleth is complete at the level it draws.

USAGE
-----
    python -m app.packs.build_geo /path/to/ne_10m_admin_1_states_provinces.geojson

Re-running it is safe and idempotent: it reads the pack only to learn which
region names and codes to emit, and writes nothing but the `.geo.json` files.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent

# ISO A3 in Natural Earth for each pack we ship.
ADM0 = {"IN": "IND", "BR": "BRA", "ZA": "ZAF"}

# Ramer-Douglas-Peucker tolerance in degrees, and the smallest ring we keep.
# 0.02deg is ~2km: invisible at any zoom this map offers, and it takes India's
# 36 outlines from 4.6MB of raw coordinates to roughly 120KB.
TOLERANCE = 0.02
MIN_RING_AREA = 0.004          # square degrees; drops specks, keeps small islands
MAX_RINGS_PER_REGION = 14      # archipelagos (Andaman, Lakshadweep) need several
PRECISION = 3

# The extent each map draws, as (west, south, east, north).
#
# A remote territory belongs to its country but ruins the map that shows it: the
# Prince Edward Islands are legally part of the Western Cape and sit 1,700km
# south-east of it, which stretches a nine-province choropleth to a third of its
# usable height to draw two specks. So each country states the extent it is
# drawn at, rings outside it are dropped, and the script prints every drop --
# because an outline quietly disappearing is exactly the kind of thing a map is
# good at hiding. India and Brazil need no clipping; theirs are their own
# extents, stated so a later pack change cannot silently start clipping them.
FOCUS = {
    "IN": (67.0, 6.0, 98.0, 37.5),    # includes Andaman & Nicobar, Lakshadweep
    "BR": (-75.0, -34.5, -34.0, 6.0),
    "ZA": (16.0, -35.5, 33.5, -22.0),  # mainland; excludes the Prince Edward Is.
}


def fold(name: str) -> str:
    """Normalise a region name for matching across the two sources.

    The packs write "Jammu & Kashmir" and "Sao Paulo"; Natural Earth writes
    "Jammu and Kashmir" and "Sao Paulo" with the tilde. Folding accents and
    ampersands makes the join exact in all three packs -- verified, because a
    fuzzy match here would silently colour the wrong state.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.replace("&", "and").lower().split())


def ring_area(ring: list[list[float]]) -> float:
    """Unsigned shoelace area in square degrees. Used only for ranking rings by
    size, so the crude planar approximation is fine."""
    a = 0.0
    for i in range(len(ring) - 1):
        a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(a) / 2.0


def rdp(points: list[list[float]], tol: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker, iterative so a 40,000-point coastline cannot blow
    the recursion limit."""
    n = len(points)
    if n < 3:
        return points[:]
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        worst, worst_i = -1.0, -1
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > worst:
                worst, worst_i = d, i
        if worst > tol:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring: list) -> list[list[float]] | None:
    pts = [[float(x), float(y)] for x, y in ring]
    pts = rdp(pts, TOLERANCE)
    # RDP on a closed ring can leave a degenerate sliver; a polygon needs three
    # distinct vertices to have an interior at all.
    if len(pts) < 4:
        return None
    pts = [[round(x, PRECISION), round(y, PRECISION)] for x, y in pts]
    if pts[0] != pts[-1]:
        pts.append(pts[0][:])
    dedup = [pts[0]]
    for p in pts[1:]:
        if p != dedup[-1]:
            dedup.append(p)
    return dedup if len(dedup) >= 4 else None


def outer_rings(geometry: dict) -> list[list]:
    """Outer rings only. Holes are dropped: the largest of them across all three
    packs is under a pixel on a 900px map, and carrying them would double the
    file to draw nothing."""
    t = geometry["type"]
    if t == "Polygon":
        return [geometry["coordinates"][0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    raise ValueError(f"unexpected geometry type {t!r}")


def in_focus(ring: list[list[float]], box: tuple) -> bool:
    w, s, e, n = box
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    return w <= cx <= e and s <= cy <= n


def build(country: str, features: list[dict], pack: dict) -> dict:
    by_name = {}
    for f in features:
        name = f["properties"].get("name")
        if name:
            by_name[fold(name)] = f

    box = FOCUS[country]
    regions, missing, clipped = [], [], []
    for r in pack["regions"]:
        f = by_name.get(fold(r["name"]))
        if f is None:
            missing.append(r["name"])
            continue
        rings = []
        for ring in outer_rings(f["geometry"]):
            s = simplify_ring(ring)
            if s is None or ring_area(s) < MIN_RING_AREA:
                continue
            if not in_focus(s, box):
                clipped.append(r["name"])
                continue
            rings.append(s)
        if not rings:                      # keep the biggest whatever its area
            cand = [s for s in (simplify_ring(x) for x in outer_rings(f["geometry"]))
                    if s is not None and in_focus(s, box)]
            rings = sorted(cand, key=ring_area, reverse=True)[:1]
        if not rings:
            missing.append(f"{r['name']} (entirely outside the drawn extent)")
            continue
        rings.sort(key=ring_area, reverse=True)
        regions.append({"code": r["code"], "name": r["name"],
                        "rings": rings[:MAX_RINGS_PER_REGION]})

    for name in sorted(set(clipped)):
        print(f"  {country}: clipped {clipped.count(name)} outlying "
              f"ring(s) of {name}")

    if missing:
        # Loud, because a half-drawn map is worse than no map: a missing state
        # renders as a hole a reader will read as "nothing reported here".
        raise SystemExit(f"{country}: no Natural Earth outline for {missing}")

    xs = [p[0] for reg in regions for ring in reg["rings"] for p in ring]
    ys = [p[1] for reg in regions for ring in reg["rings"] for p in ring]
    return {
        "country": country,
        "level": pack["admin_levels"][1],
        "source": "Natural Earth 1:10m Admin 1 (public domain)",
        "source_url": "https://www.naturalearthdata.com/",
        # The client projects with an equirectangular scaled by cos(lat) at this
        # parallel -- the standard cheap fix for the horizontal stretch a plain
        # lon/lat plot gives you. At one country's extent it is visually
        # indistinguishable from a conic projection.
        "projection": {"type": "equirectangular",
                       "parallel": round((min(ys) + max(ys)) / 2, 3)},
        "bounds": [round(min(xs), 3), round(min(ys), 3),
                   round(max(xs), 3), round(max(ys), 3)],
        "simplified_deg": TOLERANCE,
        "regions": regions,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    for country, iso in ADM0.items():
        pack_path = PACK_DIR / f"{country}.json"
        if not pack_path.exists():
            continue
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        feats = [f for f in src["features"]
                 if f["properties"].get("adm0_a3") == iso]
        geo = build(country, feats, pack)
        out = PACK_DIR / f"{country}.geo.json"
        out.write_text(json.dumps(geo, separators=(",", ":")) + "\n",
                       encoding="utf-8")
        pts = sum(len(r) for reg in geo["regions"] for r in reg["rings"])
        print(f"{country}: {len(geo['regions'])} outlines, {pts} points, "
              f"{out.stat().st_size / 1024:.0f}KB -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

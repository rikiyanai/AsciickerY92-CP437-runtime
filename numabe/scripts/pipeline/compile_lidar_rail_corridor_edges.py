#!/usr/bin/env python3
"""Compile TMG airborne LiDAR into measured rail-corridor edges (FL-4876 REG-EDGE-01).

OFFLINE EVIDENCE COMPILER, same division of authority as
``compile_lidar_roof_field.py``: OSM stays the horizontal authority for the
rail centrelines; LiDAR only measures where the corridor's edge structure
(fence, wall, railing, hedge) stands beside each OSM rail way.

Why: ``register_observations.py`` intersects each observation ray with the
bare OSM rail centreline, so a rail fence registers on the track (lateral
0.0). The TMG cloud shows the real fence band 2-8 m off the line
(artifacts/experiments/fl4876_fixture_visual/edge_offsets/).

Method, per OSM ``railway=rail`` way, one station every ``--step`` metres:

* take the above-ground points (class != 2) between ``--band-lo`` and
  ``--band-hi`` metres above local ground (median of the 8 nearest ground
  points), with points inside OSM building footprints removed;
* cut a cross-section ``+-(--half-along)`` metres along the way;
* per side, walk out from ``--min-lateral`` to ``--max-lateral`` metres in
  0.25 m bins and stop at the first bin whose along-occupancy (0.5 m along
  bins, +-1 lateral bin window) is >= ``--min-continuity`` in two consecutive
  bins. That bin is the side's corridor edge.
* smooth each side with a 5-station median over measured stations.

Output (one JSON): per way, the station list ``[s, E, N, tx, ty, left_m,
right_m]`` (None = not measured), left = along the left normal (-ty, tx).
Units are metres in EPSG:32654.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.pipeline.register_observations import lonlat_to_epsg32654  # noqa: E402

SCHEMA = "y92.fl4876.rail_corridor_edges.v1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_osm(osm: Path):
    root = ET.parse(osm).getroot()
    xy = {}
    for nd in root.iter("node"):
        try:
            xy[nd.get("id")] = lonlat_to_epsg32654(float(nd.get("lat")), float(nd.get("lon")))
        except (TypeError, ValueError):
            continue
    rails, buildings = {}, []
    for w in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        pts = [xy[n.get("ref")] for n in w.iter("nd") if n.get("ref") in xy]
        if len(pts) < 2:
            continue
        if tags.get("railway") == "rail":
            rails[int(w.get("id"))] = pts
        elif "building" in tags and len(pts) >= 3:
            buildings.append(pts)
    return rails, buildings


def edge_from_section(along, lat, sgn, half_along, lo_lat, hi_lat, min_cont):
    """First continuous band on one side (sgn=+1 left, -1 right), or None."""
    import numpy as np
    m = (np.abs(along) <= half_along) & (lat * sgn >= lo_lat - 0.25) & (lat * sgn <= hi_lat + 0.25)
    if not m.any():
        return None
    a, d = along[m], lat[m] * sgn
    nb = max(2, int(round(2 * half_along / 0.5)))
    ab = np.clip(((a + half_along) / (2 * half_along) * nb).astype(int), 0, nb - 1)
    edges = np.arange(lo_lat - 0.25, hi_lat + 0.25 + 1e-9, 0.25)
    lb = np.clip(np.digitize(d, edges) - 1, 0, len(edges) - 2)
    occ = np.zeros((len(edges) - 1, nb), bool)
    occ[lb, ab] = True
    occw = occ.copy()
    occw[1:] |= occ[:-1]
    occw[:-1] |= occ[1:]
    cont = occw.mean(axis=1)
    centres = (edges[:-1] + edges[1:]) / 2
    for i in range(len(cont) - 1):
        if centres[i] < lo_lat:
            continue
        if cont[i] >= min_cont and cont[i + 1] >= min_cont:
            return float(centres[i])
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--osm", required=True, type=Path)
    ap.add_argument("--las-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--half-along", type=float, default=2.0)
    ap.add_argument("--band-lo", type=float, default=0.4)
    ap.add_argument("--band-hi", type=float, default=2.2)
    ap.add_argument("--min-lateral", type=float, default=1.6,
                    help="no corridor edge stands closer to a track centre (train envelope)")
    ap.add_argument("--max-lateral", type=float, default=12.0)
    ap.add_argument("--min-continuity", type=float, default=0.4)
    args = ap.parse_args(argv)

    import laspy
    import numpy as np
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    from shapely import STRtree, points as shp_points
    from shapely.geometry import LineString, Polygon

    rails, buildings = parse_osm(args.osm)
    if not rails:
        print("no railway=rail ways in the OSM source", file=sys.stderr)
        return 2
    allx = [p[0] for pts in rails.values() for p in pts]
    ally = [p[1] for pts in rails.values() for p in pts]
    pad = args.max_lateral + 15.0
    e_lo, e_hi, n_lo, n_hi = min(allx) - pad, max(allx) + pad, min(ally) - pad, max(ally) + pad
    to6677 = Transformer.from_crs("EPSG:32654", "EPSG:6677", always_xy=True)
    to32654 = Transformer.from_crs("EPSG:6677", "EPSG:32654", always_xy=True)
    cx = [to6677.transform(e, n) for e in (e_lo, e_hi) for n in (n_lo, n_hi)]
    x_lo, x_hi = min(c[0] for c in cx), max(c[0] for c in cx)
    y_lo, y_hi = min(c[1] for c in cx), max(c[1] for c in cx)
    parts, las_used = [], []
    for las in sorted(args.las_dir.glob("*.las")):
        hd = laspy.open(str(las)).header
        if hd.maxs[0] < x_lo or hd.mins[0] > x_hi or hd.maxs[1] < y_lo or hd.mins[1] > y_hi:
            continue
        d = laspy.read(str(las))
        m = (d.x >= x_lo) & (d.x <= x_hi) & (d.y >= y_lo) & (d.y <= y_hi)
        parts.append((np.asarray(d.x[m]), np.asarray(d.y[m]), np.asarray(d.z[m]),
                      np.asarray(d.classification[m])))
        las_used.append({"file": las.name, "sha256": _sha256(las)})
    if not parts:
        print("no LAS tile overlaps the rail ways", file=sys.stderr)
        return 2
    X, Y, Z, C = (np.concatenate([p[i] for p in parts]) for i in range(4))
    E, N = (np.asarray(v) for v in to32654.transform(X, Y))
    G = C == 2
    _, gi = cKDTree(np.c_[E[G], N[G]]).query(np.c_[E, N], k=8)
    hag = Z - np.median(Z[G][gi], axis=1)
    keep = (~G) & (hag >= args.band_lo) & (hag <= args.band_hi)
    bE, bN = E[keep], N[keep]
    polys = [Polygon(b) for b in buildings]
    polys = [p for p in polys if p.is_valid and p.area > 1.0]
    inside = np.zeros(len(bE), bool)
    if polys and len(bE):
        hit = STRtree(polys).query(shp_points(np.c_[bE, bN]), predicate="within")
        inside[hit[0]] = True
    bE, bN = bE[~inside], bN[~inside]
    tree = cKDTree(np.c_[bE, bN])
    radius = math.hypot(args.half_along, args.max_lateral) + 0.5

    out_ways = {}
    measured = total = 0
    for wid, pts in sorted(rails.items()):
        line = LineString(pts)
        if line.length < 1.0:
            continue
        n_st = max(1, int(line.length // args.step) + 1)
        stations = []
        for k in range(n_st):
            s = min(line.length, k * args.step)
            p = line.interpolate(s)
            a, b = line.interpolate(max(0.0, s - 1.0)), line.interpolate(min(line.length, s + 1.0))
            L = math.hypot(b.x - a.x, b.y - a.y) or 1.0
            tx, ty = (b.x - a.x) / L, (b.y - a.y) / L
            idx = tree.query_ball_point([p.x, p.y], r=radius)
            if idx:
                de, dn = bE[idx] - p.x, bN[idx] - p.y
                along = de * tx + dn * ty
                lat = de * -ty + dn * tx  # + = left of the way's direction
                left = edge_from_section(along, lat, 1, args.half_along, args.min_lateral,
                                         args.max_lateral, args.min_continuity)
                right = edge_from_section(along, lat, -1, args.half_along, args.min_lateral,
                                          args.max_lateral, args.min_continuity)
            else:
                left = right = None
            stations.append([round(s, 2), round(p.x, 3), round(p.y, 3), round(tx, 5), round(ty, 5),
                             left, right])
        for col in (5, 6):
            raw = [st[col] for st in stations]
            for i, st in enumerate(stations):
                if raw[i] is None:
                    continue
                win = [v for v in raw[max(0, i - 2): i + 3] if v is not None]
                st[col] = round(float(np.median(win)), 2)
        measured += sum(1 for st in stations for v in st[5:] if v is not None)
        total += 2 * len(stations)
        out_ways[str(wid)] = {"length_m": round(line.length, 2), "stations": stations}

    doc = {
        "schema": SCHEMA,
        "producer": "scripts/pipeline/compile_lidar_rail_corridor_edges.py",
        "frame": "EPSG:32654",
        "station_fields": ["s_m", "E", "N", "tx", "ty", "left_m", "right_m"],
        "side_convention": "left_m along the left normal (-ty, tx); right_m along (ty, -tx); None = not measured",
        "parameters": {k: getattr(args, k) for k in ("step", "half_along", "band_lo", "band_hi",
                                                     "min_lateral", "max_lateral", "min_continuity")},
        "inputs": {"osm": {"path": str(args.osm.resolve().relative_to(PROJECT_ROOT)), "sha256": _sha256(args.osm)},
                   "las": las_used},
        "coverage": {"side_stations_measured": measured, "side_stations_total": total},
        "ways": out_ways,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"OK ways={len(out_ways)} side_stations_measured={measured}/{total} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

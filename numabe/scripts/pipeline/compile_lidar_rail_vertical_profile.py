#!/usr/bin/env python3
"""Measured track-bed elevation of every railway=rail way (FL-4876 RAIL-VERTICAL-01).

Producer of ``rail_vertical_profiles.v1.json`` (bundle artifact, like
compile_lidar_rail_corridor_edges.py's rail_corridor_edges.v1.json).

LiDAR authority (FL-4929/FL-4930): LiDAR supplies measured HEIGHTS. OSM keeps
the horizontal track centreline; this file only says how high the track bed
stands along it. That is the evidence the map lacked: elevated rail
(bridge=*/embankment=*/viaduct) was drawn on the DEM street surface because
the bare-earth DEM does not contain the structure.

Per station (every --step m along the OSM centreline), every LiDAR return
within --half-width m laterally is gathered; the track bed is their median
(catenary wires and masts are sparse and sit above it; at grade the ballast
returns dominate). The local ground is the 5th percentile of class-2 returns
within --ground-radius m. Stations with fewer than --min-returns returns are
null (not measured).

usage: compile_lidar_rail_vertical_profile.py --canonical CANON.json
           --las-dir DIR --out rail_vertical_profiles.v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

SCHEMA = "y92.fl4876.rail_vertical_profiles.v1"
LIDAR_CRS = "EPSG:6677"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--las-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--half-width", type=float, default=1.0)
    ap.add_argument("--ground-radius", type=float, default=12.0)
    ap.add_argument("--min-returns", type=int, default=8)
    a = ap.parse_args(argv)
    import numpy as np
    from pyproj import Transformer
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from compile_lidar_roof_field import _load_las_arrays
    from scripts.pipeline.compile_classic_train_routes import _tags

    las = sorted(os.path.join(a.las_dir, n) for n in os.listdir(a.las_dir)
                 if n.lower().endswith((".las", ".laz")))
    X, Y, Z, cls, *_ = _load_las_arrays(las)
    to_las = Transformer.from_crs("EPSG:32654", LIDAR_CRS, always_xy=True)
    canon = json.loads(Path(a.canonical).read_text(encoding="utf-8"))
    obs = {o["observation_id"]: o for o in canon.get("source_observations", [])}
    ways = {}
    for f in canon.get("canonical_features", []):
        if (f.get("semantic_class") != "railway_boundary"
                or f["geometry"]["kind"] != "polyline" or _tags(f, obs).get("railway") != "rail"):
            continue
        tags = _tags(f, obs)
        pts = [p[:2] for p in f["geometry"]["coordinates"]]
        stations, s = [], 0.0
        for p, q in zip(pts, pts[1:]):
            L = math.dist(p, q)
            n = max(1, int(L // a.step))
            for k in range(n):
                t = k / n
                e, nn = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
                x, y = to_las.transform(e, nn)
                r2 = (X - x) ** 2 + (Y - y) ** 2
                near = Z[r2 < a.half_width ** 2]
                g = Z[(r2 < a.ground_radius ** 2) & (cls == 2)]
                bed = float(np.median(near)) if near.size >= a.min_returns else None
                gnd = float(np.percentile(g, 5)) if g.size >= a.min_returns else None
                stations.append([round(s + L * t, 2), round(e, 3), round(nn, 3),
                                 None if bed is None else round(bed, 2),
                                 None if gnd is None else round(gnd, 2), int(near.size)])
            s += L
        wid = str(f["feature_id"]).rsplit("/", 1)[-1]
        ways[wid] = {"feature_id": f["feature_id"], "length_m": round(s, 2),
                     "tags": {k: tags[k] for k in ("bridge", "embankment", "layer", "tunnel", "name")
                              if k in tags},
                     "stations": stations}
    doc = {"schema": SCHEMA, "producer": "scripts/pipeline/compile_lidar_rail_vertical_profile.py",
           "frame": "EPSG:32654 horizontal; orthometric metres (LAS Z, TP) vertical",
           "station_fields": ["s_m", "E", "N", "track_bed_z_m", "ground_z_m", "returns"],
           "parameters": {"step": a.step, "half_width": a.half_width,
                          "ground_radius": a.ground_radius, "min_returns": a.min_returns},
           "inputs": {"canonical": hashlib.sha256(Path(a.canonical).read_bytes()).hexdigest(),
                      "las": [os.path.basename(p) for p in las]},
           "ways": ways}
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    measured = sum(1 for w in ways.values() for st in w["stations"] if st[3] is not None)
    total = sum(len(w["stations"]) for w in ways.values())
    print(f"rail vertical profiles: {len(ways)} ways, {measured}/{total} stations measured -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

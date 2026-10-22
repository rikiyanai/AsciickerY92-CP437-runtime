#!/usr/bin/env python3
"""FL-4876 DENSITY-SYNTH-01. Extract tree-crown candidates from TMG LiDAR.

Operator-authorized surrounding synthesis for route bins the photo corpus
never covered: street trees cannot be guessed from road geometry, so the
point cloud is the observation. A crown is a compact off-footprint cluster
of non-ground returns above local ground, near a road. Conservative by
construction (precision over recall): the gate needs one tree per bin, not
every tree.

Usage: compile_synthesis_crowns.py --route ROUTE_JSON --tiles A.las [B.las ...]
    --world-ir CANONICAL --out crowns.json [--only-north-of N]

--only-north-of restricts emission to route bins north of an N coordinate so
calibration runs (south, where dispatch trees give ground truth) never leak
synthesized crowns into evidenced territory.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))

CROWN_MIN_H_M = 2.0
CROWN_MAX_H_M = 15.0
CROWN_MIN_PTS = 25
CROWN_MIN_EXTENT_M = 3.0
CROWN_MAX_EXTENT_M = 12.0
CROWN_MAX_ROAD_DIST_M = 12.0
GROUND_CELL_M = 2.0
CLUSTER_CELL_M = 1.0
ROUTE_BUFFER_M = 40.0


def _load_points(tile_paths):
    import numpy as np
    from compile_lidar_roof_field import _load_las_arrays
    parts = []
    for path in tile_paths:
        X, Y, Z, cls, _ret, _nret, _n = _load_las_arrays(path)
        parts.append((X, Y, Z, cls))
    X = np.concatenate([p[0] for p in parts])
    Y = np.concatenate([p[1] for p in parts])
    Z = np.concatenate([p[2] for p in parts])
    C = np.concatenate([p[3] for p in parts])
    return X, Y, Z, C


def _route_utm6677(route_path):
    import numpy as np
    from geo_frames import utm_forward
    from world_surfaces import _decode_polyline6
    from pyproj import Transformer
    doc = json.loads(Path(route_path).read_text(encoding="utf-8"))
    tinv = Transformer.from_crs(32654, 6677, always_xy=True)
    pts = []
    for leg in doc["trip"]["legs"]:
        for lat, lon in _decode_polyline6(leg.get("shape", "")):
            E, N = utm_forward(lon, lat)
            x, y = tinv.transform(E, N)
            pts.append((x, y))
    return np.array(pts)


def _segments_near_route(pts, world_ir_path, buffer_m):
    """Road/footway segments within buffer_m of the route (6677 frame)."""
    import numpy as np
    from pyproj import Transformer
    world = json.loads(Path(world_ir_path).read_text(encoding="utf-8"))
    t = Transformer.from_crs(32654, 6677, always_xy=True)
    segs = []
    for f in world.get("canonical_features", []):
        if f.get("semantic_class") not in ("road", "footway"):
            continue
        g = f.get("geometry") or {}
        coords = g.get("coordinates") or []
        if g.get("kind") == "polygon" or len(coords) < 2:
            continue
        if (isinstance(coords[0], (list, tuple))
                and coords and isinstance(coords[0][0], (list, tuple))):
            coords = [p for seg in coords for p in seg]
        pts6677 = [t.transform(x, y) for x, y in coords]
        for a, b in zip(pts6677, pts6677[1:]):
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            if min(math.hypot(mx - rx, my - ry) for rx, ry in pts) <= buffer_m:
                segs.append((a, b, f["feature_id"]))
    return segs


def _building_rings_6677(world_ir_path):
    from pyproj import Transformer
    world = json.loads(Path(world_ir_path).read_text(encoding="utf-8"))
    t = Transformer.from_crs(32654, 6677, always_xy=True)
    rings = []
    for f in world.get("canonical_features", []):
        if f.get("semantic_class") != "building_footprint":
            continue
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 3:
            continue
        rings.append([t.transform(x, y) for x, y in coords])
    return rings


def _point_in_ring(px, py, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > py) != (y1 > py):
            xin = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if px < xin:
                inside = not inside
    return inside


def main() -> int:
    import numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", required=True)
    ap.add_argument("--tiles", nargs="+", required=True)
    ap.add_argument("--world-ir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only-north-of", type=float, default=None,
                    help="emit crowns north of this 6677 y only")
    args = ap.parse_args()

    route = _route_utm6677(args.route)
    print(f"route points: {len(route)}", flush=True)
    X, Y, Z, C = _load_points(args.tiles)
    print(f"points: {len(X)}", flush=True)

    # Restrict to the route corridor.
    rcell = 20.0
    rcells = {(int(x // rcell), int(y // rcell)) for x, y in route}
    keep = np.zeros(len(X), bool)
    qx = (X // rcell).astype(int)
    qy = (Y // rcell).astype(int)
    for cx, cy in rcells:
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                keep |= (qx == cx + dx) & (qy == cy + dy)
    # Exact 40 m check on survivors.
    idx = np.nonzero(keep)[0]
    Xi = X[idx][:, None]
    Yi = Y[idx][:, None]
    best = np.full(len(idx), 1e18)
    for s in range(0, len(route), 10):
        e = min(s + 10, len(route))
        d2 = np.min((Xi - route[s:e, 0][None, :]) ** 2
                    + (Yi - route[s:e, 1][None, :]) ** 2, axis=1)
        best = np.minimum(best, d2)
    keep2 = np.zeros(len(X), bool)
    keep2[idx[best <= ROUTE_BUFFER_M ** 2]] = True
    print(f"corridor points: {keep2.sum()}", flush=True)

    # Local ground from class-2 returns on a 2 m grid.
    gx = np.floor(X / GROUND_CELL_M).astype(int)
    gy = np.floor(Y / GROUND_CELL_M).astype(int)
    gkey = (gx.astype(np.int64) << 32) | (gy.astype(np.int64) & 0xFFFFFFFF)
    gmed: dict[int, float] = {}
    order = np.argsort(gkey[keep2 & (C == 2)])
    gk = gkey[keep2 & (C == 2)][order]
    gz = Z[keep2 & (C == 2)][order]
    if len(gk):
        bounds = np.nonzero(np.diff(gk, prepend=gk[0] - 1))[0]
        for a, b in zip(bounds, list(bounds[1:]) + [len(gk)]):
            gmed[int(gk[a])] = float(np.median(gz[a:b]))
    h = np.full(len(X), -1e18)
    has_g = np.array([gkey[i] in gmed for i in np.nonzero(keep2)[0]])
    ii = np.nonzero(keep2)[0][has_g]
    h[ii] = Z[ii] - np.array([gmed[gkey[i]] for i in ii])

    cand = keep2 & (C != 2) & (h >= CROWN_MIN_H_M) & (h <= CROWN_MAX_H_M)
    print(f"tall non-ground: {cand.sum()}", flush=True)

    # Rooftops merge crowns into building clusters at 1 m cells. Drop
    # non-ground points inside footprints or within 1.5 m of a footprint
    # edge (roof-edge bleed: mixed ground/roof cells give roof-edge points
    # full building height, forming dense false crowns along eaves).
    _mrings = _building_rings_6677(args.world_ir)
    _mboxes = [(
        min(p[0] for p in r), min(p[1] for p in r),
        max(p[0] for p in r), max(p[1] for p in r)) for r in _mrings]
    _ci = np.nonzero(cand)[0]
    _cX, _cY = X[_ci], Y[_ci]
    _inside = np.zeros(len(_ci), bool)
    for _bi, (_x0, _y0, _x1, _y1) in enumerate(_mboxes):
        _hit = ((_cX >= _x0 - 1.5) & (_cX <= _x1 + 1.5)
                & (_cY >= _y0 - 1.5) & (_cY <= _y1 + 1.5))
        _hi = np.nonzero(_hit & ~_inside)[0]
        if not len(_hi):
            continue
        _ring = np.array(_mrings[_bi])
        _ra, _rb = _ring[:-1], _ring[1:]
        _px = _cX[_hi][:, None]
        _py = _cY[_hi][:, None]
        _ex, _ey = _rb[:, 0] - _ra[:, 0], _rb[:, 1] - _ra[:, 1]
        _l2 = np.maximum(_ex * _ex + _ey * _ey, 1e-12)
        _tt = np.clip(((_px - _ra[:, 0]) * _ex + (_py - _ra[:, 1]) * _ey)
                      / _l2, 0.0, 1.0)
        _dd = np.hypot(_px - (_ra[:, 0] + _tt * _ex),
                       _py - (_ra[:, 1] + _tt * _ey))
        _near = _dd.min(axis=1) <= 1.5
        _far = ~_near
        if _far.any():
            _sub = _hi[_far]
            for _k in _sub:
                if _point_in_ring(float(_cX[_k]), float(_cY[_k]),
                                  _mrings[_bi]):
                    _inside[_k] = True
        _inside[_hi[_near]] = True
    _keep = np.zeros(len(X), bool)
    _keep[_ci[~_inside]] = True
    cand = _keep
    print(f"off-footprint tall: {cand.sum()}", flush=True)
    # Wires stitch the whole corridor into one component at 1 m cells, so
    # cluster per height band and keep only dense blobs (crowns hold dozens
    # of returns per square metre; a wire span holds a few).
    ci_all = np.nonzero(cand)[0]
    hh = h[ci_all]
    groups: dict[tuple[int, int], list[int]] = {}
    for _band, (_lo, _hi) in enumerate(((2.0, 4.5), (4.5, 8.0), (8.0, 15.0))):
        _sel = np.nonzero((hh >= _lo) & (hh < _hi))[0]
        if not len(_sel):
            continue
        _cx = np.floor(X[ci_all[_sel]] / CLUSTER_CELL_M).astype(int)
        _cy = np.floor(Y[ci_all[_sel]] / CLUSTER_CELL_M).astype(int)
        _cells: dict[tuple[int, int], list[int]] = {}
        for _k, (_ix, _iy) in enumerate(zip(_cx, _cy)):
            _cells.setdefault((_ix, _iy), []).append(_k)
        _parent = list(range(len(_sel)))

        def _find(_i, _p=_parent):
            while _p[_i] != _i:
                _p[_i] = _p[_p[_i]]
                _i = _p[_i]
            return _i

        for (_ix, _iy), _mm in _cells.items():
            for _jx in (_ix - 1, _ix, _ix + 1):
                for _jy in (_iy - 1, _iy, _iy + 1):
                    _o = _cells.get((_jx, _jy))
                    if not _o:
                        continue
                    _r0 = _find(_mm[0])
                    for _m in _o:
                        if _find(_m) != _r0:
                            _parent[_find(_m)] = _r0
        _seen: dict[int, list[int]] = {}
        for _k in range(len(_sel)):
            _seen.setdefault(_find(_k), []).append(_k)
        for _members in _seen.values():
            _ncells = len({(_cx[_m], _cy[_m]) for _m in _members})
            _density = len(_members) / max(1, _ncells)
            if len(_members) >= CROWN_MIN_PTS and _density >= 12.0:
                groups[(len(groups), _band)] = [int(_sel[_m]) for _m in _members]
    print(f"dense band groups: {len(groups)}", flush=True)

    segs = _segments_near_route(route, args.world_ir, ROUTE_BUFFER_M)
    print(f"near-route segments: {len(segs)}", flush=True)
    rings = _building_rings_6677(args.world_ir)
    print(f"building rings: {len(rings)}", flush=True)
    ring_boxes = [(
        min(p[0] for p in r), min(p[1] for p in r),
        max(p[0] for p in r), max(p[1] for p in r)) for r in rings]

    crowns = []
    ci = np.nonzero(cand)[0]
    for members in groups.values():
        if len(members) < CROWN_MIN_PTS:
            continue
        pts = ci[members]
        ex = float(X[pts].max() - X[pts].min())
        ey = float(Y[pts].max() - Y[pts].min())
        ext = max(ex, ey)
        if not (CROWN_MIN_EXTENT_M <= ext <= CROWN_MAX_EXTENT_M):
            continue
        mx, my = float(X[pts].mean()), float(Y[pts].mean())
        mh = float(h[pts].max())
        if args.only_north_of is not None and my < args.only_north_of:
            continue
        on_building = False
        for (x0, y0, x1, y1), ring in zip(ring_boxes, rings):
            if x0 <= mx <= x1 and y0 <= my <= y1 and _point_in_ring(mx, my, ring):
                on_building = True
                break
        if on_building:
            continue
        best_d = 1e18
        for a, b, _fid in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy or 1e-12
            tt = max(0.0, min(1.0, ((mx - a[0]) * dx + (my - a[1]) * dy) / L2))
            dd = math.hypot(mx - (a[0] + tt * dx), my - (a[1] + tt * dy))
            if dd < best_d:
                best_d = dd
        road_d = best_d
        if road_d > CROWN_MAX_ROAD_DIST_M:
            continue
        crowns.append({
            "E_6677": round(mx, 2), "N_6677": round(my, 2),
            "crown_extent_m": round(ext, 2),
            "max_height_m": round(mh, 2),
            "points": len(members),
            "road_dist_m": round(float(road_d), 2),
        })
    crowns.sort(key=lambda c: (c["N_6677"], c["E_6677"]))
    Path(args.out).write_text(
        json.dumps({"schema": "y92.fl4876.synthesis_crowns.v1",
                    "crowns": crowns}, indent=1) + "\n", encoding="utf-8")
    print(f"crowns: {len(crowns)} -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

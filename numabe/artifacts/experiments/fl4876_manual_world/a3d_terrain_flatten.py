#!/usr/bin/env python3
"""a3d_terrain_flatten.py -- lower the terrain plateau of a terrain-baked building to the surrounding ground (FL-4876 MAP-EDIT-01).

Why: 394 of the 715 Numabe buildings carry no mesh; scripts/pipeline/bake_osm_building_footprints.py raised the terrain
height vertices inside their footprint to roof height (a plateau with near-vertical sides that read as blank walls with spike
streaks) and overwrote the cells under the footprint with the roof/wall materials. A hand-authored mesh (author_building_mesh.py)
replaces such a building; this tool removes the plateau so the mesh stands on ordinary ground:

  flatten MAP.a3d (--polygon "E,N E,N ..." | --feature ID --instances building_instances.json)
          [--margin-m 0.5] [--ring-m 2.0] [--tol-m 0.25] [--exclude-polygon "E,N ..."]...
          [--paint-from M [M ...] --paint-material M|auto] --by WHO --basis TEXT [--journal FILE] [--dry-run]

  HEIGHT (engine/terrain.cpp:179 height[y][x], 5 x 5 vertices per patch, 2 world units apart, the edge vertices stored in both
  patches). T = vertices inside the polygon or within --margin-m metres of it (the bake also raised the nearest vertices of
  cells that only touch the footprint) and not strictly inside an excluded polygon. RING = vertices between --margin-m and
  --margin-m + --ring-m of the polygon that are neither inside nor within the margin of an excluded polygon (other baked
  buildings: with --feature every other footprint of the instances file is excluded, so a neighbour's plateau never
  contaminates the ground estimate and is itself left as it is). Target height at a T vertex = weighted least-squares plane through the ring
  (weights 1/d^2) + inverse-distance interpolation of the ring's residuals from the plane (16 nearest). A T vertex is
  lowered to that target only when it is higher than the target by more than --tol-m; nothing is ever raised.
  PAINT (optional): cells whose 1 x 1 world-unit square touches the polygon (the bake's own rule) and whose current material
  (low byte) is in --paint-from (or "all": the bake overwrote every cell it touched with a per-building roof and wall
  material, so "all" is right for a baked footprint), and whose centre is not inside an excluded polygon, take
  --paint-material; "auto" = the most common material of the cells 1.5 .. 1.5 + ring metres outside the polygon that are not
  near an excluded polygon. The bake overwrote the original material, so only the surroundings tell what was there. The high byte of a cell is kept; the material must exist in the material table.
  GUARD (checked on the result before writing): the file layout, patch coordinates, diag, the material table, the tail
  and every height vertex outside T are byte for byte as they were; visual changes only in painted cells; an edge vertex
  has the same value in both patches that store it.

Raw height = 128 + (elev_m - 6.5) * 82.112 (82.112 = 16 * content_scale per metre).
Previous map kept as <map>.pre_edit_<sha8>; exclusive lock <map>.lock shared with a3d_edit.py and the other terrain tools;
atomic save; one record (op "terrain_flatten", "edits": []) appended to map_edits/journal.jsonl.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from a3d_terrain_extend import HDR, JOURNAL, PATCH_BYTES, Projection, keep_pre_edit, read_terrain, save_atomic  # noqa: E402
from a3d_terrain_paint import inside, material_defined, parse_polygon  # noqa: E402

HEIGHT_STEP = 2.0          # world units between height vertices
RING_KNN = 16


def raw_to_elev_m(raw, cs):
    return 6.5 + (np.asarray(raw, float) - 128.0) / (16.0 * cs)


def poly_dist(poly, px, py):
    """Distance (world units) from points to the polygon boundary."""
    best = np.full(px.shape, np.inf)
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy
        t = np.zeros(px.shape) if ll == 0 else np.clip(((px - x0) * dx + (py - y0) * dy) / ll, 0, 1)
        best = np.minimum(best, np.hypot(px - (x0 + t * dx), py - (y0 + t * dy)))
    return best


def _seg_hit(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def cell_touches(poly, x0, y0):
    """True when the unit square [x0,x0+1] x [y0,y0+1] and the polygon overlap or touch (bake_osm_building_footprints rule)."""
    corners = [(x0, y0), (x0 + 1, y0), (x0 + 1, y0 + 1), (x0, y0 + 1)]
    cx = np.array([c[0] for c in corners]); cy = np.array([c[1] for c in corners])
    if inside(poly, cx, cy).any():
        return True
    if any(x0 <= p[0] <= x0 + 1 and y0 <= p[1] <= y0 + 1 for p in poly):
        return True
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for k in range(4):
            if _seg_hit(a, b, corners[k], corners[(k + 1) % 4]):
                return True
    return False


def _ground_model(rx, ry, rz, qx, qy):
    """Weighted LSQ plane through the ring + IDW of the residuals; -> heights (float raw) at (qx, qy)."""
    cx, cy = rx.mean(), ry.mean()
    d2 = (rx - qx.mean()) ** 2 + (ry - qy.mean()) ** 2 + 1.0
    w = 1.0 / d2
    A = np.stack([np.ones_like(rx), rx - cx, ry - cy], 1)
    sw = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(A * sw[:, None], rz * sw, rcond=None)
    plane = lambda x, y: coef[0] + coef[1] * (x - cx) + coef[2] * (y - cy)   # noqa: E731
    res = rz - plane(rx, ry)
    out = np.empty(qx.shape)
    for i in range(qx.size):
        d = np.hypot(rx - qx[i], ry - qy[i])
        idx = np.argsort(d)[:RING_KNN]
        ww = 1.0 / (d[idx] ** 2 + 1e-6)
        out[i] = plane(qx[i], qy[i]) + float((res[idx] * ww).sum() / ww.sum())
    return out


def flatten(data: bytes, proj: Projection, poly_world, excl_world=(), margin_m=0.5, ring_m=2.0, tol_m=0.25,
            paint_from=None, paint_material=None):
    """-> (new bytes, report dict). Pure: no file access. Polygons in world units."""
    n, _, ex = read_terrain(data)
    recs = np.array(ex)
    cs = proj.cs
    margin, ring = margin_m * cs, ring_m * cs
    tol_raw = tol_m * 16.0 * cs
    poly = [tuple(map(float, p)) for p in poly_world]
    excl = [[tuple(map(float, p)) for p in e] for e in excl_world]
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    reach = margin + 4 * ring + 4
    index = {(int(r["x"]), int(r["y"])): k for k, r in enumerate(recs)}
    pxs = range(int(math.floor((min(xs) - reach) / 8)), int(math.floor((max(xs) + reach) / 8)) + 1)
    pys = range(int(math.floor((min(ys) - reach) / 8)), int(math.floor((max(ys) + reach) / 8)) + 1)
    verts = {}                                    # (gx, gy) -> raw height
    for py in pys:
        for px in pxs:
            k = index.get((px, py))
            if k is None:
                continue
            for j in range(5):
                for i in range(5):
                    verts[(px * 4 + i, py * 4 + j)] = int(recs[k]["height"][j, i])
    keys = list(verts)
    gx = np.array([k[0] for k in keys]); gy = np.array([k[1] for k in keys])
    vx, vy = gx * HEIGHT_STEP, gy * HEIGHT_STEP
    vz = np.array([verts[k] for k in keys], float)
    d_poly = poly_dist(poly, vx, vy)
    in_poly = inside(poly, vx, vy)
    d_poly = np.where(in_poly, 0.0, d_poly)
    in_excl = np.zeros(vx.shape, bool)
    d_excl = np.full(vx.shape, np.inf)
    for e in excl:
        ie = inside(e, vx, vy)
        in_excl |= ie
        d_excl = np.minimum(d_excl, np.where(ie, 0.0, poly_dist(e, vx, vy)))
    T = (d_poly <= margin) & ~in_excl
    ring_sel = None
    for mult in (1, 2, 3, 4):
        sel = (d_poly > margin) & (d_poly <= margin + ring * mult) & (d_excl > margin)
        if sel.sum() >= 8:
            ring_sel = sel
            break
    if ring_sel is None or not T.any():
        raise SystemExit("no ground ring (or no vertex to flatten) around the polygon; map not written")
    target = _ground_model(vx[ring_sel], vy[ring_sel], vz[ring_sel], vx[T], vy[T])
    newv = {}
    lowered = []
    for key, t, old in zip([k for k, s in zip(keys, T) if s], target, vz[T]):
        if old > t + tol_raw:
            newv[key] = int(round(t))
            lowered.append((old, t))
    # write heights into every patch copy of a lowered vertex
    for (px, py), k in ((p, index[p]) for p in index if p[0] in pxs and p[1] in pys):
        h = recs[k]["height"]
        for j in range(5):
            for i in range(5):
                v = newv.get((px * 4 + i, py * 4 + j))
                if v is not None:
                    h[j, i] = v
    painted = {}
    material_used = None
    if paint_from:
        pf = None if list(paint_from) == ["all"] else set(int(m) for m in paint_from)   # None: every touching cell
        cells = []
        for py in pys:
            for px in pxs:
                k = index.get((px, py))
                if k is None:
                    continue
                for cj in range(8):
                    for ci in range(8):
                        cells.append((k, cj, ci, px * 8 + ci, py * 8 + cj))
        ccx = np.array([c[3] + 0.5 for c in cells]); ccy = np.array([c[4] + 0.5 for c in cells])
        dpc = poly_dist(poly, ccx, ccy)
        inpc = inside(poly, ccx, ccy)
        in_ex_c = np.zeros(ccx.shape, bool)
        d_ex_c = np.full(ccx.shape, np.inf)
        for e in excl:
            ie = inside(e, ccx, ccy)
            in_ex_c |= ie
            d_ex_c = np.minimum(d_ex_c, np.where(ie, 0.0, poly_dist(e, ccx, ccy)))
        low = np.array([int(recs[c[0]]["visual"][c[1], c[2]]) & 255 for c in cells])
        material_used = paint_material
        if paint_material == "auto":
            band = (~inpc) & (dpc > 1.5) & (dpc <= 1.5 + ring) & (d_ex_c > 1.5)
            if pf is not None:
                band &= ~np.isin(low, list(pf))
            if band.sum() < 8:
                raise SystemExit("auto material: fewer than 8 ring cells; give --paint-material")
            material_used = Counter(low[band].tolist()).most_common(1)[0][0]
        if not material_defined(data, n, int(material_used)):
            raise SystemExit(f"material {material_used} has an empty entry in the map's material table; nothing written")
        ok_mat = np.ones(ccx.shape, bool) if pf is None else np.isin(low, list(pf))
        cand = np.where((inpc | (dpc <= 2.0)) & ~in_ex_c & ok_mat)[0]
        for ci_ in cand:
            k, cj, ci, wx, wy = cells[ci_]
            if inpc[ci_] or cell_touches(poly, float(wx), float(wy)):
                old = int(recs[k]["visual"][cj, ci])
                if old & 255 == int(material_used):
                    continue                                   # already that material: not a change
                recs[k]["visual"][cj, ci] = (old & 0xFF00) | int(material_used)
                painted[old & 255] = painted.get(old & 255, 0) + 1
    out = data[:HDR] + recs.tobytes() + data[HDR + n * PATCH_BYTES:]
    # ---- guards ------------------------------------------------------------------------------------------------
    if len(out) != len(data) or out[:HDR] != data[:HDR] or out[HDR + n * PATCH_BYTES:] != data[HDR + n * PATCH_BYTES:]:
        raise SystemExit("guard failed: file layout, material table or tail changed; map not written")
    before = np.frombuffer(data, dtype=recs.dtype, count=n, offset=HDR)
    for f in ("x", "y", "diag"):
        if not np.array_equal(before[f], recs[f]):
            raise SystemExit(f"guard failed: field {f} changed; map not written")
    if not paint_from and not np.array_equal(before["visual"], recs["visual"]):
        raise SystemExit("guard failed: visual changed without --paint-from; map not written")
    changed = np.argwhere(before["height"] != recs["height"])
    seen = {}
    for k, j, i in changed:
        key = (int(recs[k]["x"]) * 4 + int(i), int(recs[k]["y"]) * 4 + int(j))
        if key not in newv:
            raise SystemExit(f"guard failed: height vertex {key} changed outside the flatten set; map not written")
        seen.setdefault(key, set()).add(int(recs[k]["height"][j, i]))
    if any(len(v) != 1 for v in seen.values()):
        raise SystemExit("guard failed: an edge vertex differs between the patches that store it; map not written")
    pvis = before["visual"] != recs["visual"]
    if paint_from and pvis.any():
        for k, j, i in np.argwhere(pvis):
            if pf is not None and int(before[k]["visual"][j, i]) & 255 not in pf:
                raise SystemExit("guard failed: a cell outside --paint-from changed; map not written")
            if (int(before[k]["visual"][j, i]) & 0xFF00) != (int(recs[k]["visual"][j, i]) & 0xFF00):
                raise SystemExit("guard failed: a high byte changed; map not written")
    drops = [(o - t) / (16.0 * cs) for o, t in lowered]
    rep = {"vertices_in_set": int(T.sum()), "vertices_lowered": len(newv), "ring_vertices": int(ring_sel.sum()),
           "drop_m_min": round(min(drops), 2) if drops else 0.0, "drop_m_max": round(max(drops), 2) if drops else 0.0,
           "target_elev_m_range": [round(float(raw_to_elev_m(min(newv.values()), cs)), 2),
                                   round(float(raw_to_elev_m(max(newv.values()), cs)), 2)] if newv else None,
           "cells_painted_by_old_material": painted, "paint_material": material_used,
           "patches_changed": int((np.any((before["height"] != recs["height"]).reshape(n, -1), 1)
                                   | np.any((before["visual"] != recs["visual"]).reshape(n, -1), 1)).sum())}
    return out, rep


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["flatten"])
    ap.add_argument("map")
    ap.add_argument("--polygon")
    ap.add_argument("--feature", help="feature_id (or its way/ tail) in --instances; polygon = its bake_footprint")
    ap.add_argument("--instances", help="building_instances.json: every other bake_footprint is excluded")
    ap.add_argument("--exclude-polygon", action="append", default=[])
    ap.add_argument("--margin-m", type=float, default=0.5)
    ap.add_argument("--ring-m", type=float, default=2.0)
    ap.add_argument("--tol-m", type=float, default=0.25)
    ap.add_argument("--paint-from", nargs="+", help="materials to overwrite, or the word all (every cell touching the polygon)")
    ap.add_argument("--paint-material")
    ap.add_argument("--by", required=True)
    ap.add_argument("--basis", required=True)
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if bool(a.paint_from) != bool(a.paint_material):
        raise SystemExit("--paint-from and --paint-material go together")
    pm = a.paint_material if a.paint_material in (None, "auto") else int(a.paint_material)
    map_path = Path(a.map).resolve()
    proj = Projection(map_path.parent / "terrain_metadata.json")
    excl = []
    if a.feature:
        if not a.instances:
            raise SystemExit("--feature needs --instances")
        rows = json.loads(Path(a.instances).read_text())
        mine = [r for r in rows if r["feature_id"] == a.feature or r["feature_id"].split(":")[-1] == a.feature]
        if len(mine) != 1:
            raise SystemExit(f"{a.feature}: {len(mine)} matches in {a.instances}")
        poly_w = [tuple(p) for p in mine[0]["bake_footprint"]]
        excl = [[tuple(p) for p in r["bake_footprint"]] for r in rows if r is not mine[0] and r.get("bake_footprint")]
        poly_en = [tuple(float(v) for v in proj.utm(*p)) for p in poly_w]
    else:
        if not a.polygon:
            raise SystemExit("--polygon or --feature required")
        poly_en = parse_polygon(a.polygon)
        poly_w = [tuple(float(v) for v in proj.world(e, nn)) for e, nn in poly_en]
    for t in a.exclude_polygon:
        excl.append([tuple(float(v) for v in proj.world(e, nn)) for e, nn in parse_polygon(t)])
    lock = open(str(map_path) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    data = map_path.read_bytes()
    out, rep = flatten(data, proj, poly_w, excl, a.margin_m, a.ring_m, a.tol_m, a.paint_from, pm)
    old_sha, new_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(out).hexdigest()
    print(json.dumps(rep), f"\nsha {old_sha[:12]} -> {new_sha[:12]}{' (dry run, not written)' if a.dry_run else ''}")
    if a.dry_run or out == data:
        return 0
    keep = keep_pre_edit(map_path, data, old_sha)
    save_atomic(map_path, out)
    rec = {"at": datetime.now(timezone.utc).isoformat(), "map": str(map_path), "op": "terrain_flatten",
           "sha256_before": old_sha, "sha256_after": new_sha, "kept": str(keep), "by": a.by, "basis": a.basis,
           "feature": a.feature, "polygon_en": poly_en, "margin_m": a.margin_m, "ring_m": a.ring_m, "tol_m": a.tol_m,
           "excluded_polygons": len(excl), "report": rep, "edits": []}
    jp = Path(a.journal)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("saved", map_path, "; journal", jp)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

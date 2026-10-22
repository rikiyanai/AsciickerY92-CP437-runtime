#!/usr/bin/env python3
"""a3d_terrain_clear_span.py -- turn the flat road causeway under a bridge back into river (FL-4876 MAP-EDIT-01). No rebuild.

The Tama terrain extension (a3d_terrain_extend.py) paints OSM roads, bridges included, as road cells on land-floored
terrain.  Where a road bridge crosses the river this leaves a flat road strip standing 1.6 m above the water bed, a
causeway.  A hand-authored bridge deck (author_tama_bridges.py) must stand over open water, so this tool removes the strip:

  clear  MAP.a3d --way WAYID --nodes I J --s0 M --s1 M --lat0 M --lat1 M --by WHO --basis TEXT [--dry-run]

  * the axis is OSM way WAYID, from node I to node J (UTM metres, projection of the map's terrain_metadata.json);
    s runs along it, lat is to the left of travel (metres); the window is s0..s1 and lat0..lat1;
  * a road or path cell in the window becomes water when, looking across the axis through the road/path strip, the nearer
    end of the strip borders water (judged on the original cells, so nothing creeps along the strip); the strip is
    removed where it lies in the river and left alone where it stands on a bank or the floodplain;
  * a height vertex whose four adjacent cells are all water is set to the median raw height of the existing all-water
    vertices around it (the extension flattens the river to one level); vertices on a patch edge are written in every patch
    that holds them; all other vertices and every other byte of the map stay as they were;
  * previous map kept as <map>.pre_edit_<sha8>; exclusive lock <map>.lock shared with a3d_edit.py; one record
    (op "terrain_clear_span", "edits": []) appended to map_edits/journal.jsonl.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from a3d_terrain_extend import HDR, JOURNAL, PATCH_BYTES, Projection, REC, keep_pre_edit, ll_to_utm, read_terrain, save_atomic  # noqa: E402

WATER, GRASS, ROAD, PATH = 34, 14, 28, 12


def way_axis(osm: Path, wid: str, i: int, j: int):
    root = ET.parse(osm).getroot()
    nodes = {n.get("id"): (float(n.get("lon")), float(n.get("lat"))) for n in root.iter("node")}
    for w in root.iter("way"):
        if w.get("id") == wid:
            pts = [tuple(float(v) for v in ll_to_utm(*nodes[r.get("ref")])) for r in w.findall("nd")]
            return pts[i], pts[j]
    raise SystemExit(f"way {wid} not in {osm}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["clear"])
    ap.add_argument("map")
    ap.add_argument("--osm", default=str(HERE.parents[2] / ".run/downloads/tama_osm/tama_west.osm"))
    ap.add_argument("--way", required=True)
    ap.add_argument("--nodes", nargs=2, type=int, required=True)
    for k in ("s0", "s1", "lat0", "lat1"):
        ap.add_argument("--" + k, type=float, required=True)
    ap.add_argument("--by", required=True)
    ap.add_argument("--basis", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    map_path = Path(a.map).resolve()
    proj = Projection(map_path.parent / "terrain_metadata.json")
    (e0, n0), (e1, n1) = way_axis(Path(a.osm), a.way, *a.nodes)
    L = math.hypot(e1 - e0, n1 - n0)
    dE, dN = (e1 - e0) / L, (n1 - n0) / L
    lock = open(str(map_path) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    data = map_path.read_bytes()
    n, reserved, ex = read_terrain(data)
    recs = np.array(ex)                                  # writable copy
    index = {(int(r["x"]), int(r["y"])): k for k, r in enumerate(recs)}
    cs = proj.cs

    def sl(x, y):                                        # world -> (s, lat) metres
        E, N = proj.utm(x, y)
        vE, vN = E - e0, N - n0
        return vE * dE + vN * dN, -vE * dN + vN * dE
    # window in world units: bounding box of the four corners, with a margin
    corners = []
    for s in (a.s0, a.s1):
        for lat in (a.lat0, a.lat1):
            corners.append(proj.world(e0 + s * dE - lat * dN, n0 + s * dN + lat * dE))
    xs = [float(c[0]) for c in corners]; ys = [float(c[1]) for c in corners]
    wx0, wx1 = int(math.floor(min(xs))) - 24, int(math.ceil(max(xs))) + 24
    wy0, wy1 = int(math.floor(min(ys))) - 24, int(math.ceil(max(ys))) + 24
    wx0 -= wx0 % 8; wy0 -= wy0 % 8
    nu, nv = wx1 - wx0, wy1 - wy0                         # cell raster
    mat = np.full((nv, nu), -1, dtype=np.int32)
    for px in range(wx0 // 8, (wx1 + 7) // 8):
        for py in range(wy0 // 8, (wy1 + 7) // 8):
            k = index.get((px, py))
            if k is None:
                continue
            u, v = px * 8 - wx0, py * 8 - wy0
            for cy in range(8):
                for cx in range(8):
                    if 0 <= u + cx < nu and 0 <= v + cy < nv:
                        mat[v + cy, u + cx] = int(recs[k]["visual"][cy, cx]) & 255
    # candidate cells
    gx, gy = np.meshgrid(wx0 + np.arange(nu) + 0.5, wy0 + np.arange(nv) + 0.5)
    E, N = proj.utm(gx, gy)
    S = (E - e0) * dE + (N - n0) * dN
    LAT = -(E - e0) * dN + (N - n0) * dE
    cand = np.isin(mat, (ROAD, PATH)) & (S >= a.s0) & (S <= a.s1) & (LAT >= a.lat0) & (LAT <= a.lat1)
    # Each candidate cell looks across the axis (half-cell steps, up to 60 m) in both directions through the road/path strip
    # to the first cell that is neither road nor path.  It becomes water when the NEARER of those two ends is water.  Rows
    # are judged on the original materials, so nothing creeps along the strip onto a bank.
    lx, ly = -dN, dE                                      # left of travel; (E, N) and (x, y) differ only by the scale
    strip = np.isin(mat, (ROAD, PATH))
    ci = np.argwhere(cand)
    cx0 = wx0 + ci[:, 1] + 0.5
    cy0 = wy0 + ci[:, 0] + 0.5
    ends = []
    for sgn in (1, -1):
        dist = np.full(len(ci), np.inf)
        wat = np.zeros(len(ci), bool)
        done = np.zeros(len(ci), bool)
        for k in range(1, int(60 * cs / 0.5) + 1):
            qu = np.floor(cx0 + sgn * k * 0.5 * lx - wx0).astype(int)
            qv = np.floor(cy0 + sgn * k * 0.5 * ly - wy0).astype(int)
            inb = (qu >= 0) & (qu < nu) & (qv >= 0) & (qv < nv)
            qu, qv = np.clip(qu, 0, nu - 1), np.clip(qv, 0, nv - 1)
            hit = ~done & inb & ~strip[qv, qu]
            dist[hit] = k * 0.5
            wat[hit] = mat[qv[hit], qu[hit]] == WATER
            done |= hit | ~inb
            if done.all():
                break
        ends.append((dist, wat))
    (d1, w1), (d2, w2) = ends
    to_water = np.where(d1 <= d2, w1, w2) & np.isfinite(np.minimum(d1, d2))
    changed = np.zeros_like(cand)
    changed[ci[to_water, 0], ci[to_water, 1]] = True
    water_now = (mat == WATER) | changed
    n_cells = int(changed.sum())
    # vertices: global vertex (gi, gj) at world (2 gi, 2 gj); its four cells are (2gi-1..2gi, 2gj-1..2gj)
    wcell = water_now
    gi0, gj0 = wx0 // 2, wy0 // 2
    nvx, nvy = nu // 2 + 1, nv // 2 + 1
    allw = np.zeros((nvy, nvx), bool)
    for j in range(nvy):
        for i in range(nvx):
            ok = True
            for dv in (-1, 0):
                for du in (-1, 0):
                    cu, cv = 2 * i + du, 2 * j + dv
                    if not (0 <= cu < nu and 0 <= cv < nv and wcell[cv, cu]):
                        ok = False
            allw[j, i] = ok

    def vget(gi, gj):
        px, hx = divmod(gi, 4); py, hy = divmod(gj, 4)
        k = index.get((px, py))
        return None if k is None else int(recs[k]["height"][hy, hx])
    # level: median raw height of existing all-water vertices around the window that were water before the edit
    before_water = (mat == WATER)
    pre = []
    for j in range(nvy):
        for i in range(nvx):
            if allw[j, i] and not changed[min(max(2 * j, 0), nv - 1), min(max(2 * i, 0), nu - 1)]:
                h = vget(gi0 + i, gj0 + j)
                if h is not None:
                    pre.append(h)
    level = int(np.median(pre)) if pre else 0
    vchanged = 0
    cleared_vertices = []
    for j in range(nvy):
        for i in range(nvx):
            if not allw[j, i]:
                continue
            # only vertices adjacent to a changed cell
            touch = False
            for dv in (-1, 0):
                for du in (-1, 0):
                    cu, cv = 2 * i + du, 2 * j + dv
                    if 0 <= cu < nu and 0 <= cv < nv and changed[cv, cu]:
                        touch = True
            if not touch:
                continue
            gi, gj = gi0 + i, gj0 + j
            px, hx = divmod(gi, 4); py, hy = divmod(gj, 4)
            for (ppx, phx) in ((px, hx),) + (((px - 1, 4),) if hx == 0 else ()):
                for (ppy, phy) in ((py, hy),) + (((py - 1, 4),) if hy == 0 else ()):
                    k = index.get((ppx, ppy))
                    if k is not None and int(recs[k]["height"][phy, phx]) != level:
                        recs[k]["height"][phy, phx] = level
            vchanged += 1
            cleared_vertices.append((gi, gj))
    # cells back into the patches
    for px in range(wx0 // 8, (wx1 + 7) // 8):
        for py in range(wy0 // 8, (wy1 + 7) // 8):
            k = index.get((px, py))
            if k is None:
                continue
            u, v = px * 8 - wx0, py * 8 - wy0
            for cy in range(8):
                for cx in range(8):
                    if 0 <= u + cx < nu and 0 <= v + cy < nv and changed[v + cy, u + cx]:
                        recs[k]["visual"][cy, cx] = WATER
    out = data[:HDR] + recs.tobytes() + data[HDR + n * PATCH_BYTES:]
    # guards: same size, only patch bytes differ, instance table untouched
    if len(out) != len(data) or out[:HDR] != data[:HDR] or out[HDR + n * PATCH_BYTES:] != data[HDR + n * PATCH_BYTES:]:
        raise SystemExit("guard failed: file layout changed; map not written")
    old_sha, new_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(out).hexdigest()
    print(f"cells -> water {n_cells}; vertices set to raw {level}: {vchanged}; sha {old_sha[:12]} -> {new_sha[:12]}"
          f"{' (dry run, not written)' if a.dry_run else ''}")
    if a.dry_run:
        return 0
    keep = keep_pre_edit(map_path, data, old_sha)
    save_atomic(map_path, out)
    rec = {"at": datetime.now(timezone.utc).isoformat(), "map": str(map_path), "op": "terrain_clear_span",
           "sha256_before": old_sha, "sha256_after": new_sha, "kept": str(keep), "by": a.by, "basis": a.basis,
           "axis_way": a.way, "axis_nodes": a.nodes, "window_m": {"s": [a.s0, a.s1], "lat": [a.lat0, a.lat1]},
           "cells_to_water": n_cells, "vertices_set": vchanged, "vertex_raw_level": level, "edits": []}
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("saved", map_path, "; journal", JOURNAL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

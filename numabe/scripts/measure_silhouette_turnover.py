#!/usr/bin/env python3
"""Silhouette edge turnover census (FF-ACQ-05).

Decides the contract-3 silhouette identity unit (FF-DPR-49): over the
baked edge table (fl4359.edge_rows.v1) of a static asset, for a sequence
of view directions, it computes per consecutive pair the shared
kind-1-edge fraction and the fitted-polyline distance. Kind 1 is the
FF-ACQ-02 admission: smooth edges (role 0) with
dot(n_a, d) * dot(n_b, d) < 0. Open edges (face_b sentinel) and
degenerate rows carry zero normals and never straddle.

Polyline rule: nodes are straddling edges joined by shared
(quantized, round-9, mirroring the
bake weld) endpoints; maximal walks between nodes of degree != 2, then
closed loops from unused edges.

View directions come from an F9 manifest: per-frame camera `pos=`
fields give eyes E_i; with the stated look-at L, view dir
v_i = normalize(L - E_i). Ortho mode uses v_i as parallel directions.
Perspective mode puts the eye at E_i - L with the asset recentred at
the origin (bbox centre at L). Angular steps are the true inter-frame
angles; no orientation field exists in the manifest.

A synthetic uniform-degree curve (--curve) separates step-size effects
from tessellation density (named unknown).

Output: one JSON object on stdout. Diagnostics on stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

SCHEMA = "fl4359.edge_rows.v2"
OPEN_SENTINEL = 0xFFFFFFFF


def load_table(path: Path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    floats = doc["edge_rows"]["floats"]
    uints = doc["edge_rows"]["uints"]
    count = doc["edge_table"]["count"]
    if doc["edge_table"]["schema"] != SCHEMA:
        raise SystemExit(f"error: {path} schema is not {SCHEMA}")
    if len(floats) != 16 * count or len(uints) != 4 * count:
        raise SystemExit(f"error: {path} row arrays do not match count")
    run_rows = doc["edge_rows"].get("run_uints", [])
    run_of = {}
    if len(run_rows) == 2 * count:
        for i in range(count):
            run_of[i] = run_rows[2 * i]
    edges = []
    for i in range(count):
        f = floats[16 * i:16 * i + 16]
        u = uints[4 * i:4 * i + 4]
        edges.append({
            "v0": (f[0], f[1], f[2]),
            "v1": (f[4], f[5], f[6]),
            "na": (f[8], f[9], f[10]),
            "nb": (f[12], f[13], f[14]),
            "face_a": u[0],
            "face_b": u[1],
            "role": u[2],
            "mat": u[3],
        })
    dims = doc.get("dimensions_xyz")
    return {"edges": edges, "count": count, "dims": dims,
            "face_count": doc.get("face_count"), "run_of": run_of}


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def norm(v):
    n = math.sqrt(dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n)


def angle_between(a, b):
    c = max(-1.0, min(1.0, dot(a, b)))
    return math.acos(c)


def rotate(vec, axis, angle):
    """Rodrigues rotation."""
    c = math.cos(angle)
    s = math.sin(angle)
    t = dot(axis, vec)
    cx = (axis[1] * vec[2] - axis[2] * vec[1],
          axis[2] * vec[0] - axis[0] * vec[2],
          axis[0] * vec[1] - axis[1] * vec[0])
    return (vec[0] * c + cx[0] * s + axis[0] * t * (1 - c),
            vec[1] * c + cx[1] * s + axis[1] * t * (1 - c),
            vec[2] * c + cx[2] * s + axis[2] * t * (1 - c))


def key(pt):
    return (round(pt[0], 9), round(pt[1], 9), round(pt[2], 9))


def silhouette(edges, direction):
    """Indices of kind-1 straddling edges for view direction."""
    out = []
    for i, e in enumerate(edges):
        if e["role"] != 0:
            continue
        if e["face_b"] == OPEN_SENTINEL:
            continue
        if dot(e["na"], direction) * dot(e["nb"], direction) < 0.0:
            out.append(i)
    return out


def walk_chains(edges, straddling):
    """Maximal endpoint walks; returns (chains, coverage)."""
    members = set(straddling)
    adj = {}
    for i in straddling:
        for p in (key(edges[i]["v0"]), key(edges[i]["v1"])):
            adj.setdefault(p, []).append(i)
    degree = {}
    for i in straddling:
        pts = [key(edges[i]["v0"]), key(edges[i]["v1"])]
        degree[i] = len(set(adj[pts[0]]) | set(adj[pts[1]])) - 1
    unused = set(straddling)
    chains = []
    # Open walks from degree != 2 termini.
    for i in sorted(straddling):
        if i not in unused:
            continue
        if degree.get(i, 0) == 2:
            continue
        chain = [i]
        unused.discard(i)
        for end in (0, 1):
            cur = i
            cur_pt = key(edges[i]["v0"]) if end == 0 else key(edges[i]["v1"])
            while True:
                nxt = [c for c in adj.get(cur_pt, [])
                       if c in unused and c != cur]
                if not nxt:
                    break
                nxt_e = nxt[0]
                chain.append(nxt_e)
                unused.discard(nxt_e)
                pts = [key(edges[nxt_e]["v0"]), key(edges[nxt_e]["v1"])]
                cur_pt = pts[1] if pts[0] == cur_pt else pts[0]
                cur = nxt_e
                if degree.get(cur, 0) != 2:
                    break
        chains.append(chain)
    # Closed loops from the rest.
    for i in sorted(unused):
        if i not in unused:
            continue
        chain = [i]
        unused.discard(i)
        cur = i
        cur_pt = key(edges[i]["v1"])
        while True:
            nxt = [c for c in adj.get(cur_pt, []) if c in unused]
            if not nxt:
                break
            nxt_e = nxt[0]
            chain.append(nxt_e)
            unused.discard(nxt_e)
            pts = [key(edges[nxt_e]["v0"]), key(edges[nxt_e]["v1"])]
            cur_pt = pts[1] if pts[0] == cur_pt else pts[0]
            cur = nxt_e
        chains.append(chain)
    used = sum(len(c) for c in chains)
    coverage = used / len(straddling) if straddling else 1.0
    return chains, coverage


def chain_points(edges, chains):
    pts = []
    for chain in chains:
        for i in chain:
            e = edges[i]
            pts.append(((e["v0"][0] + e["v1"][0]) / 2.0,
                        (e["v0"][1] + e["v1"][1]) / 2.0,
                        (e["v0"][2] + e["v1"][2]) / 2.0))
    return pts


def polyline_length(edges, chains):
    total = 0.0
    for chain in chains:
        for i in chain:
            e = edges[i]
            d = sub(e["v0"], e["v1"])
            total += math.sqrt(dot(d, d))
    return total


def symmetric_stats(p, q):
    """Mean (Chamfer) and max (Hausdorff) symmetric point distances."""
    if not p or not q:
        return (0.0, 0.0)
    def nearest(pt, cloud):
        best = None
        for c in cloud:
            d = sub(pt, c)
            v = dot(d, d)
            if best is None or v < best:
                best = v
        return math.sqrt(best)
    dpq = [nearest(pt, q) for pt in p]
    dqp = [nearest(pt, p) for pt in q]
    all_d = dpq + dqp
    return (sum(all_d) / len(all_d), max(all_d))


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def read_manifest_eyes(path: Path):
    eyes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "frame:":
            continue
        for token in parts[1:]:
            if token.startswith("pos="):
                eyes.append(tuple(float(x) for x in token[4:].split(",")))
                break
    if len(eyes) < 2:
        raise SystemExit(f"error: fewer than 2 pos= lines in {path}")
    return eyes


def view_dirs(eyes, lookat):
    dirs = []
    for e in eyes:
        v = (lookat[0] - e[0], lookat[1] - e[1], lookat[2] - e[2])
        dirs.append(norm(v))
    return dirs


def analyze_pair(edges, set_a, set_b, chains_a, chains_b, angle_deg,
                 cells, world_scale, run_of=None):
    pts_a = chain_points(edges, chains_a)
    pts_b = chain_points(edges, chains_b)
    mean_d, max_d = symmetric_stats(pts_a, pts_b)
    scale = world_scale if world_scale else 1.0
    out = {
        "deg": round(angle_deg, 4),
        "cells": round(angle_deg * math.pi / 180.0 * PX_PER_RAD / CELL_PX, 4),
        "jaccard": round(jaccard(set_a, set_b), 4),
        "n_a": len(set_a),
        "n_b": len(set_b),
        "mean_dist_obj": round(mean_d, 4),
        "max_dist_obj": round(max_d, 4),
        "mean_dist_cells": round(mean_d / scale / WORLD_PER_CELL, 4),
        "len_a": round(polyline_length(edges, chains_a), 3),
        "len_b": round(polyline_length(edges, chains_b), 3),
    }
    if run_of:
        runs_a = {run_of[i] for i in set_a if i in run_of}
        runs_b = {run_of[i] for i in set_b if i in run_of}
        out["run_jaccard"] = round(jaccard(runs_a, runs_b), 4)
        out["run_n_a"] = len(runs_a)
        out["run_n_b"] = len(runs_b)
    return out


PX_PER_RAD = 3247.0
CELL_PX = 16.0
WORLD_PER_CELL = 1.0


def parse_vec(text):
    return tuple(float(x) for x in text.split(","))


def main(argv=None):
    global PX_PER_RAD, CELL_PX, WORLD_PER_CELL
    parser = argparse.ArgumentParser(description="Silhouette turnover census.")
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lookat", default="0,0,0")
    parser.add_argument("--curve", default="0.05,0.1,0.3,1.0,3.0")
    parser.add_argument("--mode", choices=("ortho", "persp", "both"),
                        default="both")
    parser.add_argument("--px-per-rad", type=float, default=PX_PER_RAD)
    parser.add_argument("--cell-px", type=float, default=CELL_PX)
    parser.add_argument("--world-per-cell", type=float, default=1.0)
    parser.add_argument("--world-scale", type=float, default=1.0)
    args = parser.parse_args(argv)
    PX_PER_RAD = args.px_per_rad
    CELL_PX = args.cell_px
    WORLD_PER_CELL = args.world_per_cell

    lookat = parse_vec(args.lookat)
    eyes = read_manifest_eyes(args.manifest)
    dirs = view_dirs(eyes, lookat)
    steps = [angle_between(dirs[i], dirs[i + 1]) * 180.0 / math.pi
             for i in range(len(dirs) - 1)]
    curve = [float(x) for x in args.curve.split(",") if x.strip()]

    result = {
        "manifest": str(args.manifest),
        "frames": len(eyes),
        "lookat_assumed": list(lookat),
        "fields_used": ["pos=", "lookat assumption (no orientation field exists)"],
        "px_per_rad": PX_PER_RAD,
        "cell_px": CELL_PX,
        "world_per_cell_assumed": WORLD_PER_CELL,
        "assets": {},
    }
    for artifact in args.artifacts:
        table = load_table(artifact)
        edges = table["edges"]
        run_of = table["run_of"]
        entry = {"edges": table["count"], "faces": table["face_count"],
                 "dims": table["dims"], "modes": {}}
        sil_cache = {}

        def silhouettes(directions):
            out = []
            for d in directions:
                kd = (round(d[0], 12), round(d[1], 12), round(d[2], 12))
                if kd not in sil_cache:
                    s = silhouette(edges, d)
                    chains, coverage = walk_chains(edges, s)
                    sil_cache[kd] = (s, chains, coverage)
                out.append(sil_cache[kd])
            return out

        if args.mode in ("ortho", "both"):
            seq = silhouettes(dirs)
            pairs = []
            for i in range(len(seq) - 1):
                s_a, c_a, _ = seq[i]
                s_b, c_b, _ = seq[i + 1]
                pairs.append(analyze_pair(edges, s_a, s_b, c_a, c_b,
                                          steps[i], None, args.world_scale,
                                          run_of=run_of))
            coverages = [c for _, _, c in seq]
            entry["modes"]["ortho"] = {
                "step_deg": [round(s, 4) for s in steps],
                "pairs": pairs,
                "walk_coverage_min": round(min(coverages), 4),
            }
        if args.mode in ("persp", "both"):
            eye_rel = [sub(e, lookat) for e in eyes]
            dist = [math.sqrt(dot(e, e)) for e in eye_rel]
            pdirs = [norm(sub((0.0, 0.0, 0.0), e)) for e in eye_rel]
            seq = silhouettes(pdirs)
            pairs = []
            for i in range(len(seq) - 1):
                s_a, c_a, _ = seq[i]
                s_b, c_b, _ = seq[i + 1]
                ang = angle_between(pdirs[i], pdirs[i + 1]) * 180.0 / math.pi
                pairs.append(analyze_pair(edges, s_a, s_b, c_a, c_b,
                                          ang, None, args.world_scale,
                                          run_of=run_of))
            coverages = [c for _, _, c in seq]
            entry["modes"]["persp"] = {
                "eye_dist": [round(v, 2) for v in dist],
                "pairs": pairs,
                "walk_coverage_min": round(min(coverages), 4),
            }
        # Synthetic uniform-step curve about the first direction.
        base = dirs[0]
        axis = norm((0.0, 1.0, 0.0)) if abs(base[1]) < 0.99 else norm((1.0, 0.0, 0.0))
        axis = norm(sub(axis, (base[0] * dot(axis, base),
                               base[1] * dot(axis, base),
                               base[2] * dot(axis, base))))
        curve_dirs = [base]
        for step_deg in curve:
            curve_dirs.append(rotate(base, axis,
                                     float(step_deg) * math.pi / 180.0))
        seq = silhouettes(curve_dirs)
        curve_rows = []
        for i, step_deg in enumerate(curve):
            s_a, c_a, _ = seq[0]
            s_b, c_b, _ = seq[i + 1]
            curve_rows.append(analyze_pair(edges, s_a, s_b, c_a, c_b,
                                           float(step_deg), None,
                                           args.world_scale, run_of=run_of))
        entry["curve_uniform_deg"] = curve_rows
        # Verdict per FF-DPR-49 rule on the manifest pairs (ortho).
        seq_o = entry["modes"].get("ortho", {}).get("pairs", [])
        if seq_o:
            mean_j = sum(p["jaccard"] for p in seq_o) / len(seq_o)
            mean_c = sum(p["mean_dist_cells"] for p in seq_o) / len(seq_o)
            if mean_j < 0.2 and mean_c < 1.0:
                verdict = "arc-window"
            elif mean_j > 0.7:
                verdict = "edge-anchored-sufficient"
            else:
                verdict = "inconclusive"
            entry["verdict_manifest"] = {
                "rule": "jaccard<0.2 plus stable arc (<1 cell) => arc-window; jaccard>0.7 => edge-anchored",
                "mean_jaccard": round(mean_j, 4),
                "mean_dist_cells": round(mean_c, 4),
                "verdict": verdict,
            }
            run_js = [p["run_jaccard"] for p in seq_o if "run_jaccard" in p]
            if run_js:
                entry["verdict_manifest"]["mean_run_jaccard"] = round(
                    sum(run_js) / len(run_js), 4)
        result["assets"][artifact.stem] = entry
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

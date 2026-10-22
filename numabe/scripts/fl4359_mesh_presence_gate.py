#!/usr/bin/env python3
# =============================================================================
# fl4359_mesh_presence_gate.py — are the meshes that SHOULD be on screen there?
# =============================================================================
# WHY (operator, 2026-07-12): "WHY IS MESH VISIBILITY PASSING" — because the
# duel/funnel meters only grade mesh the probe ALREADY CAPTURED: a tree the
# capture never ingested contributes no rows, so vanished trees read as
# missing denominators, not failures. This gate builds the denominator from
# GROUND TRUTH instead: the AUTHORED static-mesh face bank that World.gd bakes
# (dumped as static_mesh_faces.bin, 4 vec4 corners + material id per face —
# the same records the capture shader rasterizes).
#
# METHOD
#   1. Project every authored face's corners through the dump manifest's
#      exact camera (basis + 58-degree pilot FOV).
#   2. Sample each on-screen face across its projected span; drop samples the
#      TERRAIN provably occludes (coarse ray march on the A3D heightfield —
#      mesh-vs-mesh occlusion is NOT tested because the union over all faces
#      of a tree covers the same screen cells either way).
#   3. The union of surviving sample cells = EXPECTED mesh subcells.
#   4. Compare with PAINTED mesh subcells (final diag owner == STATIC_MESH,
#      block-ordered index law).
#
# VERDICT: presence = painted-and-expected / expected. A tree that the
# pipeline never captured now shows up as MISSING CELLS, with its world
# position and distance printed, instead of silently shrinking the meter.
# =============================================================================

import argparse             # command-line flags
import importlib.util       # reuse the parity gate's Heightfield loader
import json                 # dump manifest
import math                 # projection trig
import os                   # paths
import struct               # binary records
import sys                  # exit code

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Human names for the static-mesh material ids in the authored bank.
MAT_NAMES = {1: "tree_leaf", 2: "tree_bark", 210: "rock", 211: "moss",
             203: "lighthouse_a", 204: "lighthouse_b", 250: "skip"}


def load_heightfield(a3d_path):
    """Borrow the parity gate's A3D heightfield reader (single source)."""
    spec = importlib.util.spec_from_file_location(
        "gate", os.path.join(REPO, "scripts/fl4359_geometry_parity_gate.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g.Heightfield(a3d_path)


def terrain_blocks(hf, origin, target, step=0.75):
    """True when the terrain heightfield rises above the camera->target ray
    strictly BEFORE the target (coarse march; 0.75u steps keep the whole
    face bank affordable while catching any real ridge)."""
    dx = [target[k] - origin[k] for k in range(3)]
    dist = math.sqrt(sum(d * d for d in dx))
    if dist < 1.0:
        return False
    n = max(int(dist / step), 2)
    for i in range(1, n):            # skip both endpoints
        t = i / n
        x = origin[0] + dx[0] * t
        y = origin[1] + dx[1] * t
        z = origin[2] + dx[2] * t
        h = hf.height(x, z)
        # 0.4u of slack so a face STANDING ON the terrain is not counted as
        # occluded by its own ground cell.
        if h is not None and h > y + 0.4 and t * dist < dist - 1.5:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="FL-4359 authored-mesh presence gate")
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--frame", default="frame_001")
    ap.add_argument("--a3d", default=os.path.join(
        REPO, "godot_project/assets/maps/game_map_y8.a3d"))
    ap.add_argument("--min-presence", type=float, default=60.0,
                    help="PASS threshold: %% of expected mesh cells painted")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    m = json.load(open(os.path.join(args.dump_dir, args.frame + ".json")))
    cam = m["camera_origin"]
    bx, by, bz = m["camera_basis_x"], m["camera_basis_y"], m["camera_basis_z"]
    ansi_w, ansi_h = m["ansi_w"], m["ansi_h"]
    sw, sh = ansi_w * 2, ansi_h * 2
    stride = m["diag_stride"]
    diag = open(os.path.join(args.dump_dir, args.frame + "_diag.bin"), "rb").read()
    faces_raw = open(os.path.join(args.dump_dir, "static_mesh_faces.bin"), "rb").read()
    hf = load_heightfield(args.a3d)

    # Pilot camera: 58-degree vertical FOV, aspect from the subcell grid.
    tanv = math.tan(math.radians(58.0) / 2.0)
    tanh_ = tanv * (float(sw) / float(sh))

    def project(p):
        """World point -> (subcell x, subcell y, camera depth) or None."""
        rel = [p[k] - cam[k] for k in range(3)]
        cx = sum(rel[k] * bx[k] for k in range(3))
        cy = sum(rel[k] * by[k] for k in range(3))
        cz = sum(rel[k] * bz[k] for k in range(3))
        depth = -cz
        if depth <= 0.01:
            return None
        nx, ny = cx / (depth * tanh_), cy / (depth * tanv)
        if abs(nx) > 1.02 or abs(ny) > 1.02:    # slight frustum slack
            return None
        sx = (nx * 0.5 + 0.5) * sw
        sy = (0.5 - ny * 0.5) * sh
        return sx, sy, depth

    # ---- expected cells from the authored bank -----------------------------
    expected = {}         # cell index -> (mat, world distance) sample record
    per_mat_expected = {}
    n_faces = len(faces_raw) // 64
    for f in range(n_faces):
        rows = [struct.unpack_from("<4f", faces_raw, f * 64 + 16 * k) for k in range(4)]
        mat = int(max(0.0, min(65535.0, rows[0][3] + 0.5)))
        if mat == 250:                          # authored skip material
            continue
        corners = [r[:3] for r in rows]
        pts = [project(c) for c in corners]
        if all(p is None for p in pts):
            continue
        # Sample the quad bilinearly. Density follows the projected span so a
        # big close face gets a dense grid and a far face a couple of taps.
        vis = [p for p in pts if p is not None]
        span = max(max(p[0] for p in vis) - min(p[0] for p in vis),
                   max(p[1] for p in vis) - min(p[1] for p in vis))
        steps = max(2, min(10, int(span) + 1))
        for iu in range(steps + 1):
            for iv in range(steps + 1):
                u, v = iu / steps, iv / steps
                # Bilinear world point on the quad (corner order 0-1-2-3
                # around the face, same as the bake emits them).
                a = [corners[0][k] * (1 - u) + corners[1][k] * u for k in range(3)]
                b = [corners[3][k] * (1 - u) + corners[2][k] * u for k in range(3)]
                w = [a[k] * (1 - v) + b[k] * v for k in range(3)]
                p = project(w)
                if p is None:
                    continue
                if terrain_blocks(hf, cam, w):
                    continue
                sx, sy = min(max(int(p[0]), 0), sw - 1), min(max(int(p[1]), 0), sh - 1)
                ci = sy * sw + sx
                if ci not in expected:
                    expected[ci] = (mat, round(p[2], 1))
                    per_mat_expected[mat] = per_mat_expected.get(mat, 0) + 1

    # ---- painted cells from the final diag owner ---------------------------
    painted = set()
    for sy in range(sh):
        for sx in range(sw):
            di = ((sy // 2) * ansi_w + (sx // 2)) * 4 + ((sx & 1) | ((sy & 1) << 1))
            kind = struct.unpack_from("<I", diag, di * stride + 8)[0] & 0xFF
            if kind == 2:                       # OWNER_STATIC_MESH
                painted.add(sy * sw + sx)

    hit = sum(1 for ci in expected if ci in painted)
    presence = 100.0 * hit / max(len(expected), 1)
    ghosts = len(painted - set(expected))
    verdict = "PASS" if presence >= args.min_presence else "FAIL"

    print(f"MESH PRESENCE  verdict={verdict}  painted {hit}/{len(expected)} expected cells "
          f"({presence:.1f}%, threshold {args.min_presence}%)  ghost_painted={ghosts}")
    print("  expected by material:")
    for mat, count in sorted(per_mat_expected.items(), key=lambda kv: -kv[1]):
        got = sum(1 for ci, (mm, _) in expected.items() if mm == mat and ci in painted)
        print(f"    {MAT_NAMES.get(mat, mat):>12}: {got}/{count} cells painted")
    # The WORST holes, with world evidence: farthest-missing cells per mat.
    missing = [(ci, mat, dist) for ci, (mat, dist) in expected.items() if ci not in painted]
    missing.sort(key=lambda x: x[2])
    for ci, mat, dist in missing[:6]:
        print(f"    MISSING cell {ci} ({MAT_NAMES.get(mat, mat)}) at ~{dist}u")
    if args.out:
        json.dump({"fl": "FL-4359", "kind": "mesh_presence_receipt",
                   "verdict": verdict, "presence_pct": round(presence, 2),
                   "expected_cells": len(expected), "painted_hits": hit,
                   "ghost_painted": ghosts}, open(args.out, "w"), indent=1)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bake thin-slab walkable collision for bridge meshes (FL-4790 round 3).

Operator requirement: bridges must be walkable decks with a thin-slab
collision box — not the full-mesh solid the fixture pipeline loads today
(every fixture face carries mat 240, so loadAuthoredCollision ingests
railings, posts and sides as walls).

Reads the COMPILED payload rows (final placement geometry) plus per-instance
transforms, extracts the deck-top walk surface per bridge occurrence, and
emits stepped axis boxes (top flush with the planks, 0.3 thick below).

Single source of truth, consumed by BOTH:
  - colyseus_server/src/sim/collision.ts (expands boxes to triangles,
    replaces full-mesh rows for bridge instances), and
  - Godot _spider_bridge_deck_y (point-in-box deck query for spider legs).

Rerun after any map/catalog bake; the header hashes fail closed on mismatch.

Usage:
  python3 scripts/bake_bridge_deck_slabs.py
  python3 scripts/bake_bridge_deck_slabs.py --catalog <saved-map-catalog>
      --payload <saved-map-payload> --output <saved-map-deck-product>
"""

import argparse
import json
import math
import re
import struct
import sys
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json"
PAYLOAD = ROOT / "godot_project/assets/world_partition/fl4789_chunk_assets.v1.bin"
LANDMARKS = ROOT / "godot_project/assets/world_mesh/authored_landmarks.json"
OUT = ROOT / "godot_project/assets/world_mesh/bridge_deck_slabs.v1.json"

BRIDGE_MESHES = {"bridge-1.akm", "bridge-2.akm"}
SLAB_THICKNESS = 0.3
SEGMENT_MAX_LEN = 4.0
MODE_BIN = 0.05
FOOTPRINT_SHRINK = 0.15
# A 4-unit deck cell carries tens of square units of plank; a lone beam or
# post cap carries a few. Below this the cell inherits its frame by
# interpolation — beams/posts are local, deck is continuous, so a kept beam
# top would strand a false step while an interpolated deck never can.
MIN_DECK_CELL_AREA = 4.0


def fail(msg):
    print(f"BAKE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def tri_normal(a, b, c):
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (ab[1] * ac[2] - ab[2] * ac[1],
         ab[2] * ac[0] - ab[0] * ac[2],
         ab[0] * ac[1] - ab[1] * ac[0])
    l = math.sqrt(sum(v * v for v in n))
    return n, l


def load_record_rows(payload, offset, length):
    n = length // 4
    vals = struct.unpack("<%df" % n, payload[offset:offset + n * 4])
    quads = []
    for f in range(0, len(vals), 16):
        quads.append([(vals[f + i * 4], vals[f + i * 4 + 1], vals[f + i * 4 + 2])
                      for i in range(4)])
    return quads


def deck_slabs_local(quads, occurrence_id):
    """Pitch-following thin slabs in the occurrence-local frame.

    Per 4-unit cell along the span, fit the deck plane to the up-facing
    plank faces (area-weighted; posts/rails lose by area) and emit one thin
    box aligned to that plane: top flush with the planks, 0.3 thick below.
    Returns [(top_quad, bottom_quad)] as 4-corner point lists. Sparse cells
    inherit their frame from deck neighbours — a post stands ON deck, so a
    hole would be the fabrication, not the fill.
    """
    up = []  # (tri, area)
    for q in quads:
        for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            n, l = tri_normal(*tri)
            if l < 1e-9 or n[1] / l < 0.5:
                continue
            up.append((tri, 0.5 * l))
    if not up:
        print(f"BAKE SKIP: {occurrence_id}: no up-facing deck faces")
        return None
    allx = [p[0] for t, _a in up for p in t]
    allz = [p[2] for t, _a in up for p in t]
    span_x = max(allx) - min(allx)
    span_z = max(allz) - min(allz)
    along_x = span_x >= span_z
    coords = allx if along_x else allz
    lo, hi = min(coords), max(coords)
    nseg = max(1, int(math.ceil((hi - lo) / SEGMENT_MAX_LEN)))
    span_dir = (1.0, 0.0, 0.0) if along_x else (0.0, 0.0, 1.0)
    frames: list = [None] * nseg  # (top_point, normal, e1, h1, h2)
    for s in range(nseg):
        a = lo + (hi - lo) * s / nseg
        b = lo + (hi - lo) * (s + 1) / nseg
        cell = [(t, ar) for t, ar in up
                if a - 1e-6 <= sum(p[0 if along_x else 2] for p in t) / 3.0 <= b + 1e-6]
        area = sum(ar for _t, ar in cell)
        if area < MIN_DECK_CELL_AREA:
            continue
        n = [0.0, 0.0, 0.0]
        for t, ar in cell:
            nn, ll = tri_normal(*t)
            n[0] += nn[0] / ll * ar
            n[1] += nn[1] / ll * ar
            n[2] += nn[2] / ll * ar
        nl = math.sqrt(sum(v * v for v in n))
        if nl < 1e-9 or n[1] <= 0:
            continue
        n = [v / nl for v in n]
        c = [sum(p[i] * ar for (t, ar) in cell for p in t) / (3.0 * area)
             for i in range(3)]
        dists = defaultdict(float)
        for t, ar in cell:
            for p in t:
                d = (p[0] - c[0]) * n[0] + (p[1] - c[1]) * n[1] + (p[2] - c[2]) * n[2]
                dists[round(d / MODE_BIN)] += ar / 3.0
        peak = max(dists.values())
        mode_d = min(k for k, v in dists.items() if v >= peak - 1e-6) * MODE_BIN
        top = [c[i] + n[i] * mode_d for i in range(3)]
        # Span axis projected onto the deck plane.
        dot = span_dir[0] * n[0] + span_dir[2] * n[2]
        e1 = [span_dir[0] - dot * n[0], -dot * n[1], span_dir[2] - dot * n[2]]
        e1l = math.sqrt(sum(v * v for v in e1))
        if e1l < 1e-6:
            continue
        e1 = [v / e1l for v in e1]
        e2 = [n[1] * e1[2] - n[2] * e1[1],
              n[2] * e1[0] - n[0] * e1[2],
              n[0] * e1[1] - n[1] * e1[0]]
        proj1 = sorted((p[0] * e1[0] + p[1] * e1[1] + p[2] * e1[2])
                       for t, _a in cell for p in t)
        proj2 = sorted((p[0] * e2[0] + p[1] * e2[1] + p[2] * e2[2])
                       for t, _a in cell for p in t)
        h1 = max((proj1[-1] - proj1[0]) / 2.0 - FOOTPRINT_SHRINK, 0.3)
        h2 = max((proj2[-1] - proj2[0]) / 2.0 - FOOTPRINT_SHRINK, 0.5)
        frames[s] = (top, n, e1, h1, h2)
    if all(f is None for f in frames):
        print(f"BAKE SKIP: {occurrence_id}: deck faces too sparse for slabs")
        return None
    for s in range(nseg):
        if frames[s] is not None:
            continue
        fill = None
        for d in range(1, nseg):
            cand = []
            if s - d >= 0 and frames[s - d] is not None:
                cand.append(frames[s - d])
            if s + d < nseg and frames[s + d] is not None:
                cand.append(frames[s + d])
            if cand:
                fill = cand[0] if len(cand) == 1 else _average_frames(cand[0], cand[1])
                break
        frames[s] = fill if fill is not None else next(f for f in frames if f is not None)
    boxes = []
    for top, n, e1, h1, h2 in frames:
        e2 = [n[1] * e1[2] - n[2] * e1[1],
              n[2] * e1[0] - n[0] * e1[2],
              n[0] * e1[1] - n[1] * e1[0]]
        tq = [[top[0] + e1[0] * dx + e2[0] * dz,
               top[1] + e1[1] * dx + e2[1] * dz,
               top[2] + e1[2] * dx + e2[2] * dz]
              for dx in (-h1, h1) for dz in (-h2, h2)]
        bq = [[p[0] - n[0] * SLAB_THICKNESS,
               p[1] - n[1] * SLAB_THICKNESS,
               p[2] - n[2] * SLAB_THICKNESS] for p in tq]
        boxes.append((tq, bq))
    return boxes


def _average_frames(a, b):
    top = [(a[0][i] + b[0][i]) / 2.0 for i in range(3)]
    n = [(a[1][i] + b[1][i]) / 2.0 for i in range(3)]
    nl = math.sqrt(sum(v * v for v in n)) or 1.0
    n = [v / nl for v in n]
    e1 = [(a[2][i] + b[2][i]) / 2.0 for i in range(3)]
    e1l = math.sqrt(sum(v * v for v in e1)) or 1.0
    return (top, n, [v / e1l for v in e1], (a[3] + b[3]) / 2.0, (a[4] + b[4]) / 2.0)


def yaw_transform_box(box, origin, yaw):
    """Record-local slab corners -> world (counter-clockwise yaw).

    FL-4790 round 3: yaw is the standard counter-clockwise rotation
    (x' = c*x - s*z, z' = s*x + c*z), matching the catalog compiler, the
    authored world_aabb (reproduced exactly), and the Godot client placement
    basis. The server addRows form was the mirrored one; fixed alongside.
    """
    top, bottom = box
    c, s = math.cos(yaw), math.sin(yaw)

    def xform(p):
        return [origin[0] + c * p[0] - s * p[2],
                origin[1] + p[1],
                origin[2] + s * p[0] + c * p[2]]

    return {"top": [xform(p) for p in top],
            "bottom": [xform(p) for p in bottom]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    catalog_path = args.catalog.resolve()
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    header = catalog["header"]
    source_map = ROOT / header["source_map_path"]
    map_id = source_map.stem
    if re.fullmatch(r"[a-z0-9_]+", map_id) is None:
        fail(f"unsupported runtime map identity: {map_id}")
    if sha256(source_map.read_bytes()).hexdigest() != header["source_map_sha256"]:
        fail("catalog does not match source map")
    payload_path = args.payload.resolve() if args.payload else ROOT / header["asset_payload"]["path"]
    payload = payload_path.read_bytes()
    payload_hash = sha256(payload).hexdigest()
    if payload_hash != header["asset_payload"]["sha256"]:
        fail("catalog does not match mesh payload")
    product_name = "bridge_deck_slabs.v1.json" if map_id == "game_map_y8" else f"{map_id}.bridge_deck_slabs.v1.json"
    output_path = args.output.resolve() if args.output else OUT.parent / product_name
    if output_path.parent != OUT.parent.resolve() or output_path.name != product_name:
        fail(f"deck product must be {OUT.parent / product_name}")
    records = {a["content_key"]: a for a in catalog["asset_records"]}
    slabs = []
    stats = defaultdict(int)
    skipped = []

    def emit(occurrence_id, quads, origin, yaw, scale):
        if scale != 1.0:
            fail(f"{occurrence_id}: non-unit scale {scale} unsupported")
        n = 0
        local = deck_slabs_local(quads, occurrence_id)
        if local is None:
            skipped.append(occurrence_id)
            return 0
        for i, box in enumerate(local):
            slab = yaw_transform_box(box, origin, yaw)
            slab["id"] = f"{occurrence_id}/s{i}"
            slabs.append(slab)
            n += 1
        stats["occurrences"] += 1
        stats["slabs"] += n
        return n

    for chunk in catalog["chunks"]:
        for inst in chunk["asset_instances"]:
            if inst.get("source_mesh_name") not in BRIDGE_MESHES:
                continue
            rec = records.get(inst.get("content_key"))
            if rec is None:
                fail(f"{inst.get('instance_identity')}: record missing")
            if rec["rows_length"] != rec["face_count"] * 64:
                fail(f"{inst.get('instance_identity')}: rows range invalid")
            if rec["rows_offset"] < 0 or rec["rows_offset"] + rec["rows_length"] > len(payload):
                fail(f"{inst.get('instance_identity')}: rows outside payload")
            quads = load_record_rows(payload, rec["rows_offset"], rec["rows_length"])
            origin = (inst["world_xz"][0], inst["authored_godot_y"], inst["world_xz"][1])
            emit(inst["instance_identity"], quads, origin,
                 inst.get("yaw_radians", 0.0), inst.get("procedural_scale", 1.0))
            # Validate against the authored world AABB.
            wmin, wmax = inst["world_aabb_min"], inst["world_aabb_max"]
            for q in quads:
                c, s = math.cos(inst.get("yaw_radians", 0.0)), math.sin(inst.get("yaw_radians", 0.0))
                for p in q:
                    wx = origin[0] + c * p[0] - s * p[2]
                    wz = origin[2] + (s * p[0] + c * p[2])
                    wy = origin[1] + p[1]
                    if not (wmin[0] - 0.05 <= wx <= wmax[0] + 0.05
                            and wmin[1] - 0.05 <= wy <= wmax[1] + 0.05
                            and wmin[2] - 0.05 <= wz <= wmax[2] + 0.05):
                        fail(f"{inst.get('instance_identity')}: transform mismatches world_aabb")

    landmarks = json.loads(LANDMARKS.read_text())
    for lid, entry in (landmarks.get("placements", {}) if landmarks.get("map_id") == map_id else {}).items():
        mesh = str(entry.get("mesh", ""))
        if "bridge" not in mesh:
            continue
        faces = json.loads((ROOT / "godot_project" / mesh).read_text())
        rows = faces["rows"]
        quads = []
        for f in range(0, len(rows), 16):
            quads.append([(rows[f + i * 4], rows[f + i * 4 + 1], rows[f + i * 4 + 2])
                          for i in range(4)])
        origin = (entry["xz"][0], float(entry.get("base_y", 0.0)), entry["xz"][1])
        emit(lid, quads, origin, 0.0, 1.0)

    # Pitch audit: a slab the walker cannot stand on (groundHeight needs
    # |n.y| >= 0.7) is a fabrication. Flag anything past 35 degrees.
    by_occ = defaultdict(list)
    for slab in slabs:
        by_occ[slab["id"].rsplit("/s", 1)[0]].append(slab)
    worst_pitch = 0.0
    worst_id = "none"
    for occ, group in by_occ.items():
        for slab in group:
            t = slab["top"]
            e1 = [t[1][0] - t[0][0], t[1][1] - t[0][1], t[1][2] - t[0][2]]
            e2 = [t[3][0] - t[0][0], t[3][1] - t[0][1], t[3][2] - t[0][2]]
            n = [e1[1] * e2[2] - e1[2] * e2[1],
                 e1[2] * e2[0] - e1[0] * e2[2],
                 e1[0] * e2[1] - e1[1] * e2[0]]
            nl = math.sqrt(sum(v * v for v in n))
            if nl < 1e-9:
                continue
            pitch = math.degrees(math.acos(max(-1.0, min(1.0, abs(n[1]) / nl))))
            if pitch > worst_pitch:
                worst_pitch = pitch
                worst_id = occ
    print(f"occurrences={stats['occurrences']} slabs={len(slabs)} worst_pitch={worst_pitch:.1f}deg ({worst_id})")
    if worst_pitch > 35.0:
        print("BAKE WARN: a deck slab is steeper than walkable; flagged occurrence needs stairs")
    if skipped:
        print(f"BAKE SKIP ({len(skipped)} unroutable, full-mesh wall kept): {', '.join(skipped)}")
    # FL-4839: an authored map may legitimately contain no bridges, including
    # after deleting its last bridge. Publish the empty current generation so
    # neither consumer can retain the previous map's slabs.

    doc = {
        "format": "bridge_deck_slabs.v1",
        "map_id": map_id,
        "provenance": {
            "catalog_path": catalog_path.relative_to(ROOT).as_posix(),
            "catalog_sha256": sha256(catalog_bytes).hexdigest(),
            "payload_sha256": payload_hash,
            "source_map_sha256": catalog["header"]["source_map_sha256"],
            "slab_thickness": SLAB_THICKNESS,
            "segment_max_len": SEGMENT_MAX_LEN,
            "skipped_unroutable": skipped,
        },
        "slabs": slabs,
    }
    output_path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {output_path} ({len(slabs)} slabs)")


if __name__ == "__main__":
    main()

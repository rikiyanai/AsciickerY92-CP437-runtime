#!/usr/bin/env python3
"""a3d_terrain_paint.py -- paint terrain visual cells of a built map inside a hand-drawn polygon (FL-4876 MAP-EDIT-01). No rebuild.

Some pavement reads grey in the map although the street photos show it red-brown (interlocking brick). The terrain patch
visual cell is a material index (engine/terrain.cpp:3430-3440: 8 x 8 u16 per patch, one world unit per cell; the engine
reads the low byte). This tool sets that index for the cells whose centre lies inside a polygon and whose current material
is one of --from, so a polygon that clips the carriageway or a building apron cannot recolour it:

  paint  MAP.a3d --polygon "E,N E,N E,N ..." --from 26 [28 ...] --material 18 --by WHO --basis TEXT [--journal FILE] [--dry-run]

  * polygon: UTM easting,northing metres (terrain_metadata.json projection of the map), at least 3 vertices, closed
    automatically; a cell is inside when its centre is (even-odd rule);
  * --from is required: only cells of those materials change; --material must be a material that exists in the map's
    material table (a non-empty entry) -- the table itself is never edited;
  * the high byte of a visual cell, heights, diag, every other patch, the material table, the instance table and
    the tail stay byte for byte as they were (guards below refuse to write otherwise);
  * previous map kept as <map>.pre_edit_<sha8>; exclusive lock <map>.lock shared with a3d_edit.py and the other terrain
    tools; atomic save (a3d_terrain_extend.save_atomic); one record (op "terrain_paint", "edits": []) appended to the
    journal (default map_edits/journal.jsonl).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from a3d_terrain_extend import HDR, JOURNAL, PATCH_BYTES, Projection, keep_pre_edit, read_terrain, save_atomic  # noqa: E402

MATERIAL_TABLE = 256 * 512


def parse_polygon(text: str):
    pts = []
    for tok in text.replace(";", " ").split():
        e, n = tok.split(",")
        pts.append((float(e), float(n)))
    if len(pts) < 3:
        raise SystemExit("--polygon needs at least 3 E,N vertices")
    return pts


def inside(poly_xy, px, py):
    """Even-odd point-in-polygon for arrays px, py."""
    res = np.zeros(px.shape, bool)
    n = len(poly_xy)
    for i in range(n):
        x0, y0 = poly_xy[i]
        x1, y1 = poly_xy[(i + 1) % n]
        if y0 == y1:
            continue
        cross = ((y0 > py) != (y1 > py)) & (px < (x1 - x0) * (py - y0) / (y1 - y0) + x0)
        res ^= cross
    return res


def material_defined(data: bytes, n_patches: int, m: int) -> bool:
    o = HDR + n_patches * PATCH_BYTES + m * 512
    return 0 <= m < 256 and any(data[o:o + 512])


def paint(data: bytes, proj: Projection, poly_en, from_mats, material: int):
    """-> (new bytes, {old material: cells painted}, patches touched). Pure: no file access."""
    n, _, ex = read_terrain(data)
    if not material_defined(data, n, material):
        raise SystemExit(f"material {material} has an empty entry in the map's material table; nothing written")
    recs = np.array(ex)
    poly_xy = [tuple(float(v) for v in proj.world(e, nn)) for e, nn in poly_en]
    xs = [p[0] for p in poly_xy]
    ys = [p[1] for p in poly_xy]
    index = {(int(r["x"]), int(r["y"])): k for k, r in enumerate(recs)}
    cu, cv = np.meshgrid(np.arange(8) + 0.5, np.arange(8) + 0.5)
    painted, touched = {}, 0
    for px in range(int(math.floor(min(xs) / 8)), int(math.floor(max(xs) / 8)) + 1):
        for py in range(int(math.floor(min(ys) / 8)), int(math.floor(max(ys) / 8)) + 1):
            k = index.get((px, py))
            if k is None:
                continue
            ins = inside(poly_xy, px * 8 + cu, py * 8 + cv)
            vis = recs[k]["visual"]
            low = vis & 255
            sel = ins & np.isin(low, list(from_mats))
            if not sel.any():
                continue
            for m in np.unique(low[sel]):
                painted[int(m)] = painted.get(int(m), 0) + int((sel & (low == m)).sum())
            recs[k]["visual"] = np.where(sel, (vis & 0xFF00) | material, vis)
            touched += 1
    out = data[:HDR] + recs.tobytes() + data[HDR + n * PATCH_BYTES:]
    if len(out) != len(data) or out[:HDR] != data[:HDR] or out[HDR + n * PATCH_BYTES:] != data[HDR + n * PATCH_BYTES:]:
        raise SystemExit("guard failed: file layout changed; map not written")
    before = np.frombuffer(data, dtype=recs.dtype, count=n, offset=HDR)
    for f in ("x", "y", "height", "diag"):
        if not np.array_equal(before[f], recs[f]):
            raise SystemExit(f"guard failed: field {f} changed; map not written")
    return out, painted, touched


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["paint"])
    ap.add_argument("map")
    ap.add_argument("--polygon", required=True)
    ap.add_argument("--from", dest="from_mats", type=int, nargs="+", required=True)
    ap.add_argument("--material", type=int, required=True)
    ap.add_argument("--by", required=True)
    ap.add_argument("--basis", required=True)
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    map_path = Path(a.map).resolve()
    proj = Projection(map_path.parent / "terrain_metadata.json")
    poly = parse_polygon(a.polygon)
    lock = open(str(map_path) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    data = map_path.read_bytes()
    out, painted, touched = paint(data, proj, poly, set(a.from_mats), a.material)
    total = sum(painted.values())
    old_sha, new_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(out).hexdigest()
    print(f"cells painted {total} {painted} -> material {a.material}; patches {touched}; sha {old_sha[:12]} -> {new_sha[:12]}"
          f"{' (dry run, not written)' if a.dry_run else ''}")
    if a.dry_run or total == 0:
        return 0
    keep = keep_pre_edit(map_path, data, old_sha)
    save_atomic(map_path, out)
    rec = {"at": datetime.now(timezone.utc).isoformat(), "map": str(map_path), "op": "terrain_paint",
           "sha256_before": old_sha, "sha256_after": new_sha, "kept": str(keep), "by": a.by, "basis": a.basis,
           "polygon_en": poly, "from_materials": sorted(a.from_mats), "material": a.material,
           "cells_painted_by_old_material": painted, "patches_touched": touched, "edits": []}
    jp = Path(a.journal)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("saved", map_path, "; journal", jp)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

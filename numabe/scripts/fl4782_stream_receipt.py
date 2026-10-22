#!/usr/bin/env python3
"""FL-4782 phase 2, step C2 receipt — does the EMERGENT STREAM appear on screen
exactly where the simulated field says water is?

The renderer paints inland field water with no authored feature behind it, so
the acceptance question is a correspondence question: project every cell of the
committed phase-1 field into the captured frame and ask the per-cell dump what
glyph the renderer actually chose there.

  WET  cells (depth > gate)                 must carry a water-family glyph
  DRY  cells above the analytic sea plane    must carry NO water-family glyph

Falsifier: a wet path that is not painted (the consumer is not reading the
field), or painted water where the field is dry and the sea cannot reach (the
consumer is inventing water).

Usage:
  python3 scripts/fl4782_stream_receipt.py <dump_dir> <pixel_camera_json> <out_json>
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
FIELD_NPZ = REPO_ROOT / ".run" / "fl4782_phase1" / "cpu_fields.npz"

GRID_ORIGIN = (70.0, -125.0)
CELL_SIZE = 1.0
# Mirrors WATER_FLOW_WET_DEPTH in cell_owner_product.glsl, which itself mirrors
# DOWNHILL_WET_DEPTH in scripts/check_fl4782_flow_field_static.py.
WET_DEPTH = 1.0e-3
# Mirrors cell_owner_product.glsl `const float WATER_SURFACE_Y = 3.0625;`.
SEA_SURFACE_Y = 3.0625
# The two glyphs the field-water branch may emit (0x7E ripple, 0xB7 spray dot),
# plus the wave glyphs the analytic sea owner uses, so a cell claimed by the sea
# is never scored as a stream failure.
STREAM_GLYPHS = {"~", "·", "╖", "÷"}


def load_dump(dump_dir: Path) -> dict:
    cells = {}
    with (dump_dir / "cells.jsonl").open() as f:
        for line in f:
            d = json.loads(line)
            sc = d["screen_cell"]
            cells[(sc["x"], sc["y"])] = d.get("glyph_char")
    return cells


def main() -> int:
    dump_dir = Path(sys.argv[1])
    cam_json = Path(sys.argv[2])
    out_path = Path(sys.argv[3])

    cam = json.loads(cam_json.read_text())
    o = np.array(cam["camera_origin"])
    yaw = float(cam["camera_yaw"])
    pitch = float(cam["camera_pitch"])
    fov = float(cam["camera_fov"])
    W, H = cam["presentation_size"]
    cw, chh = cam["cell_size"]

    Ry = np.array([[math.cos(yaw), 0, math.sin(yaw)],
                   [0, 1, 0],
                   [-math.sin(yaw), 0, math.cos(yaw)]])
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(pitch), -math.sin(pitch)],
                   [0, math.sin(pitch), math.cos(pitch)]])
    Binv = np.linalg.inv(Ry @ Rx)
    fpx = 1.0 / math.tan(math.radians(fov) / 2.0)

    field = np.load(FIELD_NPZ)
    h, b = field["depth"], field["terrain"]
    gh, gw = h.shape
    cells = load_dump(dump_dir)

    def project(p):
        v = Binv @ (np.asarray(p) - o)
        z = -v[2]
        if z <= 0.5:
            return None
        nx = v[0] / z * fpx / (W / H)
        ny = v[1] / z * fpx
        sx = (nx * 0.5 + 0.5) * W
        sy = (0.5 - ny * 0.5) * H
        if not (0 <= sx < W and 0 <= sy < H):
            return None
        return int(sx // cw), int(sy // chh)

    wet_mask = h > WET_DEPTH
    # A dry cell is only a fair test when the analytic sea cannot reach it and
    # it is not immediately beside the stream (one ANSI cell covers several
    # world cells at this range, so a rim cell is genuinely ambiguous).
    near_wet = np.zeros_like(wet_mask)
    for dj in (-2, -1, 0, 1, 2):
        for di in (-2, -1, 0, 1, 2):
            near_wet |= np.roll(np.roll(wet_mask, dj, 0), di, 1)

    # PASS 1 — wet cells. Collected FIRST so the dry pass below can exclude
    # every screen cell the stream already owns; testing both in one loop makes
    # the dry result depend on iteration order, which is a metric bug, not a
    # renderer fact.
    wet_hits, wet_total, wet_missing = 0, 0, []
    wet_cells_screen = set()
    occupancy: dict[tuple[int, int], int] = {}
    for j in range(gh):
        for i in range(gw):
            sc = project((GRID_ORIGIN[0] + i + 0.5, float(b[j, i]),
                          GRID_ORIGIN[1] + j + 0.5))
            if sc is None or sc not in cells:
                continue
            occupancy[sc] = occupancy.get(sc, 0) + 1
            if not wet_mask[j, i]:
                continue
            wet_total += 1
            wet_cells_screen.add(sc)
            if cells[sc] in STREAM_GLYPHS:
                wet_hits += 1
            else:
                wet_missing.append({"cell": [i, j], "screen": list(sc),
                                    "glyph": cells[sc],
                                    "depth": float(h[j, i])})

    # PASS 2 — dry cells. Restricted to the stream's own neighbourhood and to
    # screen cells that are actually resolvable:
    #   * far background cells are excluded because many world cells alias into
    #     one ANSI cell near the horizon, so the glyph there is not this cell's,
    #   * '~' is shared vocabulary (the analytic sea band uses it heavily), so
    #     an unrestricted count measures the sea, not this painter.
    # The unrestricted count is reported too, so the restriction is visible.
    wet_ij = np.argwhere(wet_mask)
    dry_painted = dry_total = 0
    dry_painted_unrestricted = dry_total_unrestricted = 0
    for j in range(gh):
        for i in range(gw):
            if wet_mask[j, i] or near_wet[j, i] or b[j, i] <= SEA_SURFACE_Y + 0.5:
                continue
            sc = project((GRID_ORIGIN[0] + i + 0.5, float(b[j, i]),
                          GRID_ORIGIN[1] + j + 0.5))
            if sc is None or sc not in cells or sc in wet_cells_screen:
                continue
            painted = cells[sc] in STREAM_GLYPHS
            dry_total_unrestricted += 1
            dry_painted_unrestricted += int(painted)
            distance = float(np.min(np.hypot(wet_ij[:, 1] - i,
                                             wet_ij[:, 0] - j)))
            if distance > 15.0 or occupancy.get(sc, 0) > 4:
                continue
            dry_total += 1
            dry_painted += int(painted)

    wet_frac = wet_hits / wet_total if wet_total else 0.0
    dry_frac = dry_painted / dry_total if dry_total else 0.0
    receipt = {
        "fl": "FL-4782",
        "phase": 2,
        "check": "C2_emergent_stream_correspondence",
        "dump": str(dump_dir),
        "camera_hash": cam.get("camera_hash"),
        "camera_origin": cam["camera_origin"],
        "field": str(FIELD_NPZ.relative_to(REPO_ROOT)),
        "wet_gate_depth": WET_DEPTH,
        "checks": [
            {
                "check": "C2a_wet_path_is_painted",
                "expected": ">= 80% of in-frame field-wet cells carry a water-family glyph",
                "falsifier": "an unpainted wet path means the consumer is not reading the field",
                "wet_cells_in_frame": wet_total,
                "wet_cells_painted": wet_hits,
                "fraction": wet_frac,
                "unpainted_examples": wet_missing[:10],
                "pass": bool(wet_total > 0 and wet_frac >= 0.80),
            },
            {
                "check": "C2b_no_invented_water",
                "expected": (
                    "0 water-family glyphs on in-frame field-DRY ground above "
                    "the sea plane, not adjacent to the stream, within 15 cells "
                    "of it, and on a resolvable (non-aliased) screen cell"
                ),
                "falsifier": "painted water where the field is dry means the consumer is inventing water",
                "dry_cells_tested": dry_total,
                "dry_cells_painted": dry_painted,
                "dry_painted_fraction": dry_frac,
                "dry_cells_tested_unrestricted": dry_total_unrestricted,
                "dry_cells_painted_unrestricted": dry_painted_unrestricted,
                "restriction_note": (
                    "'~' is shared vocabulary with the analytic sea band, and "
                    "many world cells alias into one ANSI cell near the horizon, "
                    "so the unrestricted count measures the sea and the horizon "
                    "rather than this painter. Both numbers are reported."
                ),
                "pass": bool(dry_painted == 0),
            },
        ],
    }
    receipt["pass"] = all(c["pass"] for c in receipt["checks"])
    out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    for c in receipt["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['check']}")
    print(f"  wet {wet_hits}/{wet_total} = {wet_frac:.3f}   "
          f"dry painted {dry_painted}/{dry_total}")
    print(f"receipt: {out_path}")
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""FL-4876 palette quantization. The Classic material table is fixed at 256
entries, so the hundreds of distinct hand-authored evidence colours must
share recipes while the authored source keeps its exact evidence RGB.

The snap picks the CIELAB-nearest of the 8 surrounding grid corners (not
per-channel rounding, which turned real Numabe greige and cream walls pink in
build_reg_62 headed classic). Roofs use grid 32, walls grid 16.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_source_palette import (  # noqa: E402
    FIRST_FREE_MATERIAL_ID,
    LAST_MATERIAL_ID,
    MANUAL_WALL_RGB_GRID,
    _quantize_manual_rgb,
    _srgb_to_lab,
)

BUNDLE = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"


def test_quantize_snaps_to_grid_corners():
    assert _quantize_manual_rgb((84, 96, 102)) == (96, 96, 96)
    assert _quantize_manual_rgb((0, 0, 0)) == (0, 0, 0)
    assert _quantize_manual_rgb((255, 255, 255)) == (255, 255, 255)
    for rgb in ((16, 47, 48), (213, 212, 204), (150, 121, 105)):
        for grid in (16, 32):
            assert all(v % grid == 0 or v == 255 for v in _quantize_manual_rgb(rgb, grid))


def test_near_duplicate_evidence_shares_a_cell():
    assert _quantize_manual_rgb((84, 96, 102)) == _quantize_manual_rgb((85, 96, 102))
    assert _quantize_manual_rgb([84, 96, 102]) == (96, 96, 96)


def test_neutral_and_cream_walls_do_not_turn_pink():
    # build_reg_62 regression: per-channel rounding gave (224,192,192) for both
    for rgb in ((208, 207, 199), (212, 203, 185)):
        q = _quantize_manual_rgb(rgb, MANUAL_WALL_RGB_GRID)
        assert q != (224, 192, 192)
        L, a, b = _srgb_to_lab(q)
        assert a < 4.0, (rgb, q, a)  # no red/pink cast
        assert math.dist(_srgb_to_lab(rgb), (L, a, b)) < 7.0


def test_authored_demand_fits_the_classic_table():
    doc = json.loads((BUNDLE / "authored_building_appearance.json").read_text())
    inferred = json.loads((BUNDLE / "inferred_building_palette.json").read_text())["buildings"]
    walls = {tuple(_quantize_manual_rgb(v["wall_rgb"], MANUAL_WALL_RGB_GRID))
             for v in list(doc["buildings"].values()) + list(inferred.values()) if v.get("wall_rgb")}
    roofs = {tuple(_quantize_manual_rgb(v["roof_rgb"]))
             for v in doc["buildings"].values() if v.get("roof_rgb")}
    # Observed in build_reg_62: walls take at most two levels glyphs (176/177),
    # roofs one recipe each, 30 role materials; 20 ids of further headroom.
    assert len(walls) * 2 + len(roofs) + 30 + 20 <= LAST_MATERIAL_ID - FIRST_FREE_MATERIAL_ID + 1

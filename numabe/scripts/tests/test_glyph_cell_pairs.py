# =============================================================================
# test_glyph_cell_pairs.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# scripts/glyph_cell_pairs.py builds the COMPOSITE raster of two (or more)
# cells at their real offsets and measures the seam: gap profile, port
# overlap, topology delta, apparent slope, and cross_cell_continuity as the
# xu-2017 Eq. 19 DSM against a reference stroke. Method owner is the FL-4512
# corpus audit §3.3-3.6; "step N" refers to that spec.
#
#   1. SYNTHETIC RASTERS pin the definitions (composite placement, gap
#      profile, DSM identity/symmetry, emptiness rule).
#   2. UNIFONT MEASUREMENTS pin the spec's acceptance steps that do not depend
#      on the U+0027/U+00B4 stair-step decision (steps 73-76 still open):
#      61 fan_open, 62 fan_collapsed, 64 boundary_line_pair, and the REAL form
#      of step 69 (| and ! each stacked over |).
#
# Steps 57-60 (stair-step / short-slope altitude ramps) are written against
# the dictionary AFTER the apostrophe-vs-acute discrepancy was resolved in
# favour of the plate (U+00B4 ACUTE ACCENT, mirrored by U+0060 GRAVE; operator
# ruling 2026-09-08). NOTHING HERE IS A RUNTIME CLAIM.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_cell_pairs.py -q
# =============================================================================
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pytest.importorskip("scipy")
pytest.importorskip("skimage")

import numpy as np  # noqa: E402

import glyph_cell_features as gcf  # noqa: E402
import glyph_cell_pairs as gcp  # noqa: E402


def R(*rows: str) -> np.ndarray:
    return gcf.raster_from_rows(list(rows))


# ---------------------------------------------------------------------------
# 1. Synthetic
# ---------------------------------------------------------------------------
def test_composite_places_b_below_or_right_of_a_step_37():
    a = R("#.", "..")
    b = R("..", ".#")
    assert gcp.composite(a, b, "stacked").tolist() == R("#.", "..", "..", ".#").tolist()
    assert gcp.composite(a, b, "beside").tolist() == R("#...", "...#").tolist()
    with pytest.raises(ValueError):
        gcp.composite(a, R("..."), "stacked")
    with pytest.raises(ValueError):
        gcp.composite(a, b, "diagonal")


def test_seam_gap_profile_counts_empty_pixels_between_ink_step_41_43():
    a = R("....", "#.#.", "....", "#...")     # col0 ink to row 3 (touches bottom); col2 ink ends row 1
    b = R("#.#.", "....", "..#.", "....")     # col0 ink at row 0; col2 ink at row 0
    prof = gcp.seam_gap_profile(a, b, "stacked")
    assert prof == [0, None, 2, None]
    s = gcp.seam_summary(prof)
    assert s["min"] == 0 and s["max"] == 2 and s["connected"] is True and s["lines_with_ink"] == 2
    assert gcp.seam_summary([None, None]) == {"min": None, "median": None, "max": None,
                                              "lines_with_ink": 0, "connected": False}
    # beside: per row, A's right extent to B's left extent
    a2 = R("..#", "...")
    b2 = R("#..", "..#")
    assert gcp.seam_gap_profile(a2, b2, "beside") == [0, None]


def test_port_overlap_is_jaccard_over_border_occupancy_step_40():
    a = R("....", "#.##")
    b = R("#..#", "....")
    assert gcp.port_overlap(a, b, "stacked") == pytest.approx(2 / 3)   # |{0,3}| / |{0,2,3}|
    assert gcp.port_overlap(R("....", "...."), b, "stacked") == 0.0


def test_dsm_identity_tolerance_radius_and_symmetry():
    """xu-2017 Eq. 19 is point-to-AREA within r = 5: a pure translation smaller
    than r costs nothing; one larger than r costs the unmatched magnitude."""
    line = np.zeros((16, 16), dtype=np.uint8)
    line[:, 2] = 1
    same = gcp.dsm(line, line)
    assert same["dsm"] == 0.0 and same["d_ab"] == 0.0
    d1 = gcp.dsm(line, gcf.shifted(line, 0, 1))
    d6 = gcp.dsm(line, gcf.shifted(line, 0, 6))
    assert d1["dsm"] == pytest.approx(0.0)
    assert d6["dsm"] > 0.0
    assert d6["dsm"] == pytest.approx(gcp.dsm(gcf.shifted(line, 0, 6), line)["dsm"])   # bidirectional
    # a missing stroke is the unmatched magnitude, not zero
    assert gcp.dsm(line, np.zeros_like(line))["dsm"] > 0.0
    with pytest.raises(ValueError):
        gcp.dsm(line, line[:8])


def test_straight_stroke_reference_and_default_reference():
    v = gcp.straight_stroke((16, 8), (7.5, 3.5), 90.0)
    assert v[:, 4].sum() == 16 and v.sum() == 16
    h = gcp.straight_stroke((16, 8), (7.5, 3.5), 0.0)
    assert h[8, :].sum() == 8 and h.sum() == 8
    line = np.zeros((32, 8), dtype=np.uint8)
    line[:, 4] = 1
    ref = gcp.default_reference(line)
    assert np.array_equal(ref, line)
    cont = gcp.cross_cell_continuity(line)
    assert cont["dsm"] == 0.0 and cont["reference_source"] == "synthetic_straight_stroke"
    assert gcp.cross_cell_continuity(np.zeros((4, 4), dtype=np.uint8))["dsm"] is None


def test_topology_delta_step_46():
    top = R("....", ".#..", ".#..", ".#..")
    bot = R(".#..", ".#..", "....", "....")
    comp = gcp.composite(top, bot, "stacked")
    td = gcp.topology_delta(top, bot, comp)
    assert td["parts"] == [2, 0] and td["composite"] == [1, 0] and td["delta"] == [-1, 0]


def test_emptiness_check_rejects_a_topology_change_step_48_50():
    bar = R(".#", ".#")
    cells = [(0, 0, bar), (0, 1, bar), (0, 2, bar)]
    res = gcp.emptiness_check(cells, 1, 3, 1)
    assert res["before"] == [1, 0] and res["after"] == [2, 0] and res["accepted"] is False
    # blanking an already-empty cell changes nothing and is accepted
    blank = R("..", "..")
    res2 = gcp.emptiness_check([(0, 0, bar), (0, 1, blank), (0, 2, bar)], 1, 3, 1)
    assert res2["accepted"] is True


# ---------------------------------------------------------------------------
# 2. Unifont measurements
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scorer():
    try:
        s = gcp.GlyphScorer()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"font chain unavailable: {e}")
    if not s.font_names or not s.font_names[0].startswith("unifont"):
        pytest.skip("unifont is not first in the chain")
    return s


@pytest.fixture(scope="module")
def combos(scorer):
    return {c["id"]: gcp.combo_features(scorer, c) for c in gcp.load_combos()}


def test_step_56_dictionary_has_the_nine_ids():
    ids = {c["id"] for c in gcp.load_combos()}
    assert ids == {"stair_step_rising", "stair_step_falling", "slope_rising_short",
                   "slope_falling_short", "fan_open", "fan_collapsed", "orb_bracketed",
                   "boundary_line_pair", "aa_dot_ladder"}


def test_step_69_real_form_bar_vs_bang_stacked_over_bar(scorer):
    """| over | must be MORE continuous than ! over | by cross_cell_continuity,
    against both the synthetic straight-stroke reference and the ││ reference."""
    bar_bar = gcp.pair_from_chars(scorer, "|", "|", "stacked")
    bang_bar = gcp.pair_from_chars(scorer, "!", "|", "stacked")
    assert bar_bar["continuity"]["dsm"] < bang_bar["continuity"]["dsm"]
    assert bar_bar["seam"]["min"] < bang_bar["seam"]["min"]          # 2 px vs 4 px in unifont
    box = gcf.cell_raster(scorer, ord("│")).grid
    ref = gcp.composite(box, box, "stacked")
    ga = gcf.cell_raster(scorer, ord("|")).grid
    gb = gcf.cell_raster(scorer, ord("!")).grid
    d_bar = gcp.dsm(gcp.composite(ga, ga, "stacked"), ref)["dsm"]
    d_bang = gcp.dsm(gcp.composite(gb, ga, "stacked"), ref)["dsm"]
    assert d_bar < d_bang
    # and the box-drawing bar over itself is the only truly connected seam
    box_box = gcp.pair_features(box, box, "stacked")
    assert box_box["seam"]["connected"] is True and box_box["port_overlap"] == 1.0
    assert box_box["topology"]["delta"] == [-1, 0]
    assert bar_bar["seam"]["connected"] is False


def _ink_tops(f: dict) -> list[int]:
    return [f["cells"][f"{i},0"]["ink_extents"]["ink_top"] for i in range(len(f["cells"]))]


def test_steps_73_76_stair_rows_use_the_plates_acute_not_the_apostrophe(combos):
    assert combos["stair_step_rising"]["chars"] == "_.-\u00b4"
    assert combos["stair_step_falling"]["chars"] == "\u0060-._"
    assert combos["slope_rising_short"]["chars"] == ".-\u00b4"
    assert combos["slope_falling_short"]["chars"] == "\u0060-."


def test_step_57_stair_step_rising_altitude_strictly_decreases(combos):
    tops = _ink_tops(combos["stair_step_rising"])
    assert tops == sorted(tops, reverse=True) and len(set(tops)) == 4, tops   # 14, 12, 9, 1 in unifont


def test_step_58_stair_step_falling_altitude_strictly_increases(combos):
    tops = _ink_tops(combos["stair_step_falling"])
    assert tops == sorted(tops) and len(set(tops)) == 4, tops


def test_step_59_short_slope_is_shallower_than_the_stair(combos):
    short = _ink_tops(combos["slope_rising_short"])
    stair = _ink_tops(combos["stair_step_rising"])
    assert short == sorted(short, reverse=True) and len(set(short)) == 3
    assert (short[0] - short[-1]) < (stair[0] - stair[-1])


def test_step_60_short_slope_falling_mirrors_rising(combos):
    rise = _ink_tops(combos["slope_rising_short"])
    fall = _ink_tops(combos["slope_falling_short"])
    assert fall == list(reversed(rise))


def test_step_61_fan_open_rotation_triple(combos):
    f = combos["fan_open"]
    slopes = [f["cells"][f"{i},0"]["dominant_orientation_deg"] for i in range(3)]
    assert slopes[0] < 80.0                         # backslash leans one way
    assert abs(slopes[1] - 90.0) < 2.0              # bar is the in-between
    assert slopes[2] > 100.0                        # slash leans the other way
    assert abs((slopes[0] + slopes[2]) - 180.0) < 3.0


def test_step_62_fan_collapsed_all_vertical(combos):
    f = combos["fan_collapsed"]
    for i in range(3):
        cell = f["cells"][f"{i},0"]
        assert abs(cell["dominant_orientation_deg"] - 90.0) < 2.0
        assert len(cell["components"]) == 1
        # Spec assumed through_v; unifont | is terminal (rows 2-15). Pinned as measured.
        assert cell["components"][0]["border_incidence"] == ["terminal"]
    assert abs(f["composite_slope_deg"] - 90.0) < 2.0


def test_step_64_boundary_line_pair_never_joins(combos):
    b = combos["boundary_line_pair"]
    assert len(b["pairs"]) == 1 and b["pairs"][0]["relation"] == "stacked"
    seam = b["pairs"][0]["seam"]
    assert seam["min"] is not None and seam["min"] > 0
    assert seam["connected"] is False
    assert b["pairs"][0]["topology"]["delta"] == [0, 0]


def test_step_63_orb_bracketed_composite_keeps_one_hole(combos):
    assert combos["orb_bracketed"]["composite_beta"] == [3, 1]


def test_fan_collapsed_is_the_most_continuous_authored_combo(combos):
    dsm = {k: v["composite_continuity"]["dsm"] for k, v in combos.items()}
    assert min(dsm, key=dsm.get) == "fan_collapsed"


def test_cli_runs(capsys):
    assert gcp.main(["--stack", "|", "|", "--raster"]) == 0
    out = capsys.readouterr().out
    assert "stacked" in out and "#" in out
    assert gcp.main(["--combos"]) == 0
    assert "fan_collapsed" in capsys.readouterr().out

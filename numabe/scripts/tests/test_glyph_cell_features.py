# =============================================================================
# test_glyph_cell_features.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# scripts/glyph_cell_features.py measures per-glyph features on the
# AS-POSITIONED cell raster (not on the crop-fit ``norm`` grid that
# glyph_features.py uses). The method owner is the FL-4512 corpus audit
# docs/audits/2026-09-08-glyph-feature-vocabulary-fl4512-corpus-audit.md §3;
# "step N" below refers to that spec's numbered instructions.
#
# Two kinds of test live here:
#
#   1. SYNTHETIC RASTERS (no font): the topology pair, border-incidence classes,
#      contact runs and shift helper are pinned on hand-drawn grids so the
#      definitions are checked independently of any font.
#   2. UNIFONT MEASUREMENTS: the assertions the spec asks for (steps 63, 65,
#      66, 67, 68, 69, 70, 71, 72) are pinned on the repository's unifont
#      raster. Where the measured raster contradicts the spec's assumption the
#      test pins the MEASURED fact and the docstring says which assumption
#      fell. Known: unifont ``|`` spans rows 2-15 (terminal, bottom only) — it
#      is NOT through_v; ``│`` U+2502 is. Unifont ``_`` sits on row 14, one
#      row above the cell floor, so it has no bottom contact.
#
# Scope: per-glyph features only. Pairwise composite scoring (spec §3.3) and the
# 9 authored combinations (steps 56-64) are a later slice. NOTHING HERE IS A
# RUNTIME CLAIM.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_cell_features.py -q
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
from glyph_features import crop_fit  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Synthetic rasters — definitions
# ---------------------------------------------------------------------------
def R(*rows: str) -> np.ndarray:
    return gcf.raster_from_rows(list(rows))


def test_topology_ring_has_one_hole_and_diagonal_leak_does_not_count():
    # Closed 4-ring: hole is a 4-connected background component not touching the border.
    ring = R("....", ".##.", "#..#", ".##.")
    assert gcf.topology(ring) == (1, 1)
    # Open the ring at one 4-adjacent pixel: the interior joins the border background.
    broken = R("....", ".##.", "#...", ".##.")
    assert gcf.topology(broken) == (1, 0)
    # Two 8-adjacent-only ink pixels are ONE component (ink is 8-connected).
    assert gcf.topology(R("#.", ".#")) == (1, 0)
    # ...while background across a diagonal ink pair does NOT connect (4-connected).
    diag_box = R("###", "#.#", "###")
    assert gcf.topology(diag_box) == (1, 1)


def test_border_incidence_classes_step_23_24():
    assert gcf.border_incidence(set()) == {"floating"}
    assert gcf.border_incidence({"bottom"}) == {"terminal"}
    assert gcf.border_incidence({"left", "bottom"}) == {"terminal"}
    assert gcf.border_incidence({"top", "bottom"}) == {"through_v"}
    assert gcf.border_incidence({"left", "right"}) == {"through_h"}
    assert gcf.border_incidence({"top", "bottom", "left", "right"}) == {"through_v", "through_h"}


def test_components_report_sides_and_are_ordered_top_first():
    g = R(".#..", ".#..", "....", "#..#")
    comps = gcf.components(g)
    assert [c["size"] for c in comps] == [2, 1, 1]
    assert comps[0]["sides_touched"] == ["top"]
    assert comps[0]["border_incidence"] == ["terminal"]
    assert comps[1]["sides_touched"] == ["bottom", "left"]
    assert comps[2]["sides_touched"] == ["bottom", "right"]
    assert comps[0]["row_centroid"] < comps[1]["row_centroid"]


def test_contact_runs_and_span_step_19_21():
    occ = gcf.contact_occupancy(R("##.#", "....", "#..#", "...."))
    assert occ["top"] == [True, True, False, True]
    assert occ["bottom"] == [False] * 4
    assert occ["left"] == [True, False, True, False]
    runs = gcf.contact_runs(occ)
    assert runs["top"] == [(0, 2), (3, 4)]
    assert runs["left"] == [(0, 1), (2, 3)]
    assert gcf.contact_span(runs)["top"] == (0, 4)
    assert gcf.contact_span(runs)["bottom"] is None


def test_shift_helper_drops_ink_pushed_over_the_border():
    g = R("#...", "....", "....", "...#")
    assert gcf.shifted(g, 1, 0).tolist() == R("....", "#...", "....", "....").tolist()
    assert gcf.shifted(g, 0, -1).tolist() == R("....", "....", "....", "..#.").tolist()


def test_aiss_descriptor_shape_and_identity():
    g = np.zeros((16, 8), dtype=np.uint8)
    g[2:16, 4] = 1
    h = gcf.aiss_descriptor(g, 8, 16)
    assert h.shape == (4 * 8, 5 * 12)           # (cell_w/2)*(cell_h/2) points x 5x12 bins
    assert gcf.aiss_distance(h, h) == 0.0
    with pytest.raises(ValueError):
        gcf.aiss_descriptor(g, 16, 16)


# ---------------------------------------------------------------------------
# 2. Unifont measurements
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scorer():
    try:
        s = gcf.GlyphScorer()
    except Exception as e:  # pragma: no cover - font chain missing
        pytest.skip(f"font chain unavailable: {e}")
    if not s.font_names or not s.font_names[0].startswith("unifont"):
        pytest.skip("unifont is not the first font in the chain; measurements are font-specific")
    return s


@pytest.fixture(scope="module")
def F(scorer):
    cache: dict[str, dict] = {}

    def get(ch: str) -> dict:
        if ch not in cache:
            cache[ch] = gcf.glyph_features(scorer, ord(ch))
        return cache[ch]

    return get


def test_half_width_cell_is_8x16_alpha_2(F):
    r = F("|")
    assert r["position_source"] == "native"
    assert (r["cell_w"], r["cell_h"]) == (8, 16)
    assert r["alpha"] == 2.0


def test_step_68_bar_and_bang_have_distinct_port_signatures(F):
    """Spec step 68 expected ``|`` through_v. Measured unifont ``|`` is rows 2-15:
    bottom contact only -> terminal. The class and occupancy still separate it
    from ``!`` (floating, both occupancies empty). ``│`` is the through_v exemplar."""
    bar, bang, box = F("|"), F("!"), F("│")
    assert bar["incidence_classes"] == ["terminal"]
    assert any(bar["contact_occupancy"]["bottom"])
    assert not any(bar["contact_occupancy"]["top"])
    assert bang["incidence_classes"] == ["floating"]
    assert not any(bang["contact_occupancy"]["top"])
    assert not any(bang["contact_occupancy"]["bottom"])
    assert box["incidence_classes"] == ["through_v"]
    assert any(box["contact_occupancy"]["top"]) and any(box["contact_occupancy"]["bottom"])
    assert bar["incidence_classes"] != bang["incidence_classes"]


def test_step_69_aiss_metric_separates_bar_from_bang_more_than_from_box_bar(F):
    """Narrow form of step 69 (single-cell descriptors; the stacked-over-``|``
    composite is a later slice)."""
    d_bar_bang = gcf.aiss_distance(F("|")["aiss"], F("!")["aiss"])
    d_bar_box = gcf.aiss_distance(F("|")["aiss"], F("│")["aiss"])
    assert d_bar_box > 0.0
    assert d_bar_bang > d_bar_box


def test_step_66_aa_family_altitudes_and_bang_polarity(F):
    dot, tick, bang, ibang = F("."), F("'"), F("!"), F("¡")
    # "." sits low, "'" sits high (authoring skill §4.6 rule 5).
    assert dot["ink_extents"]["ink_top"] >= 12
    assert tick["ink_extents"]["ink_bottom"] <= 5
    # ! and ¡ : same extents (rows 4-13 in unifont), opposite polarity —
    # the big component is ABOVE the small one for "!", BELOW it for "¡".
    assert bang["ink_extents"] == ibang["ink_extents"]
    b_sizes = [c["size"] for c in bang["components"]]
    i_sizes = [c["size"] for c in ibang["components"]]
    assert b_sizes == list(reversed(i_sizes)) and b_sizes[0] > b_sizes[1]
    assert bang["ink_centroid"][0] < ibang["ink_centroid"][0]
    # All four are one lateral-extent family: single column 4 (or 3-4 for the dot).
    for r in (dot, tick, bang, ibang):
        assert r["ink_extents"]["ink_right"] == 4
        assert r["ink_extents"]["ink_left"] in (3, 4)


def test_step_67_colon_sits_between_dot_and_tick_on_altitude(F):
    tick, colon, dot = F("'"), F(":"), F(".")
    assert tick["ink_centroid"][0] < colon["ink_centroid"][0] < dot["ink_centroid"][0]


def test_step_63_orb_bracketed_topology(F):
    assert F("o")["beta1"] == 1
    assert F("(")["beta1"] == 0
    assert F(")")["beta1"] == 0
    assert F("o")["beta0"] == 1


def test_step_65_aa_ladder_has_no_through_v_component(F):
    for ch in ".':¡!·":
        r = F(ch)
        for comp in r["components"]:
            assert "through_v" not in comp["border_incidence"], (ch, comp)
            assert set(comp["border_incidence"]) <= {"floating", "terminal"}, (ch, comp)


def test_step_71_underscore_vs_overline_altitude_polarity(F):
    """Spec step 71 expected equal contact widths. Measured: unifont ``_`` sits on
    row 14 (no bottom contact); ``‾`` sits on row 0 (top contact). The measurable
    signature is opposite altitude with near-equal horizontal extent."""
    us, ol = F("_"), F("‾")
    assert us["ink_extents"]["ink_top"] == us["ink_extents"]["ink_bottom"] == 14
    assert ol["ink_extents"]["ink_top"] == ol["ink_extents"]["ink_bottom"] == 0
    w_us = us["ink_extents"]["ink_right"] - us["ink_extents"]["ink_left"]
    w_ol = ol["ink_extents"]["ink_right"] - ol["ink_extents"]["ink_left"]
    assert abs(w_us - w_ol) <= 1
    assert any(ol["contact_occupancy"]["top"])
    assert not any(us["contact_occupancy"]["bottom"])


def test_step_70_hyphen_vs_colon_signature(F):
    """The corner-vs-curve falsifier needs the composite (later slice). Per glyph,
    ``:`` is two stacked floating components and ``-`` one horizontal component;
    the box-drawing ``─`` is the through_h exemplar, unifont ``-`` is not."""
    colon, hyphen, rule = F(":"), F("-"), F("─")
    assert colon["beta0"] == 2 and hyphen["beta0"] == 1
    assert abs(colon["dominant_orientation_deg"] - 90.0) < 5.0
    assert min(hyphen["dominant_orientation_deg"], 180.0 - hyphen["dominant_orientation_deg"]) < 5.0
    assert hyphen["incidence_classes"] == ["floating"]
    assert rule["incidence_classes"] == ["through_h"]


def test_orientation_field_on_canonical_strokes(F):
    assert abs(F("|")["dominant_orientation_deg"] - 90.0) < 2.0
    fwd = F("/")["dominant_orientation_deg"]
    back = F("\\")["dominant_orientation_deg"]
    assert 45.0 < back < 75.0                    # 1:2 cell diagonal, screen coords
    assert abs((fwd + back) - 180.0) < 3.0       # mirror pair


def test_step_72_features_are_computed_on_raw_not_norm(scorer, F):
    """``|`` and ``│`` crop-fit to the SAME norm grid; their raw features differ.
    If the module ever computed on norm, this test fails."""
    bar = gcf.cell_raster(scorer, ord("|")).grid
    box = gcf.cell_raster(scorer, ord("│")).grid
    assert np.array_equal(crop_fit(bar), crop_fit(box))
    assert F("|")["incidence_classes"] != F("│")["incidence_classes"]
    assert F("|")["ink_extents"] != F("│")["ink_extents"]


def test_stability_marks_step_52_54(F):
    # A floating glyph keeps its class under a 1 px shift; a border-touching one does not.
    assert "incidence_classes" not in F("!")["unstable"]
    assert "incidence_classes" in F("|")["unstable"]
    assert "beta1" not in F("o")["unstable"]
    # ink_extents always moves under a shift: it is a derived summary, never a match key.
    assert "ink_extents" in F(".")["unstable"]


def test_cli_table_and_json_run(capsys):
    assert gcf.main(["--chars", "|!"]) == 0
    out = capsys.readouterr().out
    assert "U+007C" in out and "U+0021" in out
    assert gcf.main(["--chars", "o", "--json"]) == 0
    import json
    recs = json.loads(capsys.readouterr().out)
    assert recs[0]["beta1"] == 1 and isinstance(recs[0]["aiss"], list)

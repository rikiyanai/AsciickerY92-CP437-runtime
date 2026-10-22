# =============================================================================
# test_glyph_combinations_dictionary.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# assets/glyphs/authored/glyph_combinations.v1.json is authored data: the
# recurring 2-3 cell glyph combinations named in the Stone Story RPG ASCII-art
# tutorials, stored as cell offsets plus codepoints. The tutorials describe the
# style as a vocabulary of combinations rather than of single glyphs -- "learning
# ASCII art is about learning these combinations", "what are the permutations of
# those symbols in two dimensions on the grid" (o5v-NS9o4yc 27:56-28:20) -- and
# name mirroring them as the exercise itself (o5v-NS9o4yc 31:12-31:40).
#
# Each combination stores its horizontal mirror. The mirror is GENERATED, not
# hand-typed: columns are reflected and each codepoint is mapped through the
# find_mirror chirality pairs that glyph_audit discovers from the font raster.
# These tests re-derive that mapping and assert the stored file still agrees, so
# the dictionary cannot drift away from the pairs the audit actually finds.
#
# The structural tests (offsets unique and in bounds, ids unique, mirror is a
# real column reflection, mirroring is an involution) need no font cache. The
# find_mirror agreement test skips when the regenerable .run/ cache is absent.
#
# The godot_project copy of the file checks that its combinations array
# still equals this copy's (so scripts/compile_native_terminal_atlas.py
# output is unaffected).
#
# NOTHING HERE IS A RUNTIME CLAIM. The file is offline authoring data for
# `glyph_families_viewer.py --mode combo`; no atlas, compiler or renderer surface
# reads it.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_combinations_dictionary.py -q
# =============================================================================
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/tests/<this file> -> scripts/tests -> scripts -> repository root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

COMBINATIONS = ROOT / "assets/glyphs/authored/glyph_combinations.v1.json"


@pytest.fixture(scope="module")
def doc():
    assert COMBINATIONS.exists(), f"missing authored dictionary: {COMBINATIONS}"
    return json.loads(COMBINATIONS.read_text())


@pytest.fixture(scope="module")
def combos(doc):
    cs = doc.get("combinations") or []
    assert cs, "the dictionary must not be empty"
    return cs


# ---------------------------------------------------------------------------
# document shape and provenance
# ---------------------------------------------------------------------------
def test_document_declares_schema_and_source(doc):
    assert doc["schema"] == "fl4512.glyph_combinations.v1"
    assert doc["version"] == 1
    # the cited tutorial passages must travel with the data
    assert "o5v-NS9o4yc" in doc["source"]
    assert "5sFVVQcUWVE" in doc["source"]
    assert doc["mirror_rule"]
    assert "find_mirror" in doc["mirror_rule"]


def test_every_combination_cites_its_source(combos):
    for cb in combos:
        assert cb["source"], f"{cb['id']} has no source"
        assert "o5v-NS9o4yc" in cb["source"] or "5sFVVQcUWVE" in cb["source"]


def test_combination_ids_are_unique(combos):
    ids = [cb["id"] for cb in combos]
    assert len(ids) == len(set(ids)), "duplicate combination id"
    assert all(i and i == i.strip() for i in ids)


def test_the_seeded_combinations_are_all_present(combos):
    """The set the design session named: stair steps rising and falling, the
    short slopes, the fan and its collapse, the bracketed orb, the boundary-line
    pair, and the AA dot set."""
    ids = {cb["id"] for cb in combos}
    for expected in ("stair_step_rising", "stair_step_falling",
                     "slope_rising_short", "slope_falling_short",
                     "fan_open", "fan_collapsed", "orb_bracketed",
                     "boundary_line_pair", "aa_dot_ladder"):
        assert expected in ids, f"missing seeded combination: {expected}"


# ---------------------------------------------------------------------------
# cell offsets
# ---------------------------------------------------------------------------
def _offsets(cells):
    return [(int(d["col"]), int(d["row"])) for d in cells]


def test_offsets_are_unique_within_each_combination(combos):
    """Two glyphs cannot occupy the same cell of the grid."""
    for cb in combos:
        offs = _offsets(cb["cells"])
        assert len(offs) == len(set(offs)), f"{cb['id']} reuses a cell offset"
        moffs = _offsets(cb["mirror"]["cells"])
        assert len(moffs) == len(set(moffs)), f"{cb['id']} mirror reuses a cell offset"


def test_offsets_are_in_bounds_and_the_grid_is_tight(combos):
    for cb in combos:
        cols, rows = int(cb["cols"]), int(cb["rows"])
        offs = _offsets(cb["cells"])
        assert all(0 <= c < cols and 0 <= r < rows for c, r in offs), cb["id"]
        # the declared grid must be exactly the extent used, not padded
        assert max(c for c, _r in offs) == cols - 1, cb["id"]
        assert max(r for _c, r in offs) == rows - 1, cb["id"]


def test_each_combination_has_at_least_two_cells(combos):
    """A combination is by definition more than one glyph."""
    for cb in combos:
        assert len(cb["cells"]) >= 2, cb["id"]
        assert len(cb["cells"]) == cb["size"] if "size" in cb else True


def test_cells_agree_with_their_char_field(combos):
    for cb in combos:
        for d in cb["cells"] + cb["mirror"]["cells"]:
            assert chr(int(d["cp"])) == d["char"], f"{cb['id']}: {d}"


# ---------------------------------------------------------------------------
# mirror geometry
# ---------------------------------------------------------------------------
def test_mirror_reflects_columns_and_preserves_rows(combos):
    for cb in combos:
        cols = int(cb["cols"])
        want = sorted((cols - 1 - c, r) for c, r in _offsets(cb["cells"]))
        got = sorted(_offsets(cb["mirror"]["cells"]))
        assert got == want, f"{cb['id']} mirror is not a column reflection"


def test_mirror_has_the_same_cell_count(combos):
    for cb in combos:
        assert len(cb["mirror"]["cells"]) == len(cb["cells"]), cb["id"]


def test_self_symmetric_flag_is_accurate(combos):
    for cb in combos:
        original = sorted((int(d["col"]), int(d["row"]), int(d["cp"]))
                          for d in cb["cells"])
        mirrored = sorted((int(d["col"]), int(d["row"]), int(d["cp"]))
                          for d in cb["mirror"]["cells"])
        assert cb["mirror"]["self_symmetric"] == (original == mirrored), cb["id"]


def test_mirroring_is_an_involution(combos):
    """Mirroring the mirror must give the original back — otherwise the stored
    pair is not a reflection of one another."""
    by_cp = {}
    for cb in combos:
        for a, b in zip(sorted(cb["cells"], key=lambda d: (d["row"], d["col"])),
                        sorted(cb["mirror"]["cells"],
                               key=lambda d: (d["row"], -d["col"]))):
            by_cp.setdefault(int(a["cp"]), int(b["cp"]))
    for cp, mcp in by_cp.items():
        assert by_cp.get(mcp, cp) == cp, (
            f"U+{cp:04X} -> U+{mcp:04X} does not mirror back")


# ---------------------------------------------------------------------------
# agreement with the chirality pairs the audit actually discovers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mirror_map():
    pytest.importorskip("numpy")
    ga = pytest.importorskip("glyph_audit")
    try:
        c = ga.Corpus()
    except SystemExit as exc:
        pytest.skip(f"glyph feature cache unavailable: {exc}")
    m: dict[int, int] = {}
    for a, b in ga.find_mirror(c):
        ca, cb = int(c.cps[a]), int(c.cps[b])
        m.setdefault(ca, cb)
        m.setdefault(cb, ca)
    return c, m


def test_mirror_codepoints_match_find_mirror_where_a_pair_exists(mirror_map, combos):
    """Where find_mirror discovers a chirality partner, the stored mirror MUST use
    it; where it discovers none the glyph is horizontally symmetric in the raster
    and must be carried through unchanged."""
    c, m = mirror_map
    checked = 0
    for cb in combos:
        cols = int(cb["cols"])
        mirror_by_offset = {(int(d["col"]), int(d["row"])): d
                            for d in cb["mirror"]["cells"]}
        for d in cb["cells"]:
            cp = int(d["cp"])
            if c.i(cp) is None:
                continue        # not in this cache build; nothing to compare against
            target = mirror_by_offset[(cols - 1 - int(d["col"]), int(d["row"]))]
            expected = m.get(cp, cp)
            assert int(target["cp"]) == expected, (
                f"{cb['id']}: U+{cp:04X} should mirror to U+{expected:04X}, "
                f"file says U+{int(target['cp']):04X}")
            assert target["cp_source"] == ("find_mirror" if cp in m else "identity")
            checked += 1
    assert checked, "no combination cells could be checked against the corpus"


def test_at_least_one_real_chirality_pair_is_exercised(mirror_map, combos):
    """The dictionary must actually use find_mirror, not only the identity path —
    otherwise the mirror rule would be untested by this data."""
    _c, m = mirror_map
    used = {int(d["cp"]) for cb in combos for d in cb["cells"]}
    assert used & set(m), "no combination uses a glyph with a chirality partner"


def test_combination_glyphs_are_renderable(mirror_map, combos):
    """Every codepoint the viewer will rasterize must exist in the corpus."""
    c, _m = mirror_map
    missing = sorted({int(d["cp"]) for cb in combos
                      for d in cb["cells"] + cb["mirror"]["cells"]
                      if c.i(int(d["cp"])) is None})
    assert not missing, ("combination glyphs missing from the feature cache: "
                         + " ".join(f"U+{cp:04X}" for cp in missing))


# ---------------------------------------------------------------------------
# the viewer's --mode combo surface
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def viewer_combos(mirror_map):
    c, _m = mirror_map
    pytest.importorskip("curses")
    gfv = pytest.importorskip("glyph_families_viewer")
    return c, gfv, gfv.load_combination_families(c)


def test_viewer_loads_every_combination_in_file_order(viewer_combos, combos):
    _c, _gfv, fams = viewer_combos
    assert [f["combo"]["id"] for f in fams] == [cb["id"] for cb in combos]
    assert all(f["mode"] == "combo" for f in fams)


def test_viewer_renders_the_real_cell_grid_beside_its_mirror(viewer_combos, combos):
    """The composite must be the true multi-cell raster: one 16x16 cell per
    column and row, for both the combination and its mirror."""
    _c, gfv, fams = viewer_combos
    for f, cb in zip(fams, combos):
        rows, mrows = f["combo"]["rows"], f["combo"]["mirror_rows"]
        assert len(rows) == int(cb["rows"]) * gfv.N, cb["id"]
        assert len(mrows) == len(rows), cb["id"]
        assert all(len(r) == int(cb["cols"]) * gfv.N for r in rows), cb["id"]
        assert all(len(r) == len(rows[0]) for r in mrows), cb["id"]
        assert any("█" in r for r in rows), f"{cb['id']} rendered blank"
        assert not f["combo"]["missing"], cb["id"]


def test_self_symmetric_combinations_render_identically_to_their_mirror(viewer_combos):
    _c, _gfv, fams = viewer_combos
    checked = 0
    for f in fams:
        if f["combo"]["self_symmetric"]:
            assert f["combo"]["rows"] == f["combo"]["mirror_rows"], f["combo"]["id"]
            checked += 1
    assert checked, "no self-symmetric combination to check"


def test_asymmetric_combinations_render_differently_from_their_mirror(viewer_combos):
    _c, _gfv, fams = viewer_combos
    checked = 0
    for f in fams:
        if not f["combo"]["self_symmetric"]:
            assert f["combo"]["rows"] != f["combo"]["mirror_rows"], f["combo"]["id"]
            checked += 1
    assert checked, "no asymmetric combination to check"


# ---------------------------------------------------------------------------
# FF copy of the dictionary (godot_project, FL-4870)
# ---------------------------------------------------------------------------
FF_COMBINATIONS = ROOT / "godot_project/assets/glyphs/authored/glyph_combinations.v1.json"
# ascii-art-authoring 4.1 (PAGE §5): basic row, extended row, whitelisted
# letters; plus the space of the anti-aliasing set (4.6).
ALPHABET = set("`~!^()-_+=;:'\",.\\/|<>[]{}") | set("´‾¡·") | set("oOvVTL7UcCxX") | {" "}
@pytest.fixture(scope="module")
def ff_doc():
    assert FF_COMBINATIONS.exists(), f"missing FF copy: {FF_COMBINATIONS}"
    return json.loads(FF_COMBINATIONS.read_text())


def test_ff_copy_keeps_the_shared_combinations_unchanged(doc, ff_doc):
    """The Product generator reads the root copy's combinations array; the FF
    field must not alter the shared entries."""
    assert ff_doc["combinations"] == doc["combinations"]
    assert ff_doc["schema"] == doc["schema"]


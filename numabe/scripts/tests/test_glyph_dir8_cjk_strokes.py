# =============================================================================
# test_glyph_dir8_cjk_strokes.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# scripts/glyph_audit.py discovers "dir8" families: groups of glyphs that are the
# same mark aimed at different compass headings. Candidacy for that mode is
# decided by `_dir8_simple`, which rejects dense ideograph blocks by NAME PREFIX
# and then caps ink density.
#
# The prefix list started with the bare string "CJK". That is correct for the
# dense ideograph blocks, but it also swallowed "CJK Strokes" (U+31C0-U+31EF),
# which encodes the individual single strokes an ideograph is built from — sparse,
# strongly directed marks, and the exact canopy vocabulary the runtime already
# elects by wind heading (glyph_ids 764-779, i.e. U+31C0..U+31CF). Every one of
# those glyphs was therefore invisible to dir8.
#
# These tests pin the fix in both directions:
#   * the "CJK Strokes" allowlist exception admits the canopy glyphs, including
#     the two whose ink density sits under the general sparsity floor;
#   * the dense CJK blocks are STILL rejected, so the exception did not reopen
#     the pollution the prefix rule exists to prevent.
#
# The block/density rule is pure logic over (block name, ink count), so most of
# this runs against a stub corpus and needs no font cache. The tests that do need
# the regenerable cache under .run/ skip when it is absent.
#
# NOTHING HERE IS A RUNTIME CLAIM. glyph_audit.py is offline authoring tooling;
# no atlas, compiler or renderer surface is read or written by these tests.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_dir8_cjk_strokes.py -q
# =============================================================================
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/tests/<this file> -> scripts/tests -> scripts -> repository root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

np = pytest.importorskip("numpy")
ga = pytest.importorskip("glyph_audit")

# glyph_ids 764-779 are the canopy leaf frames the runtime substitutes by wind
# heading; they resolve to this codepoint run in the CJK Strokes block.
CANOPY_CPS = tuple(range(0x31C0, 0x31D0))
# The wider run that owns the leaf sweep / hook / twig / fork / turn review sets
# registered in glyph_families_viewer.load_foliage_stroke_families.
STROKE_FAMILY_CPS = tuple(range(0x31C0, 0x31E4))


class _StubCorpus:
    """Minimal stand-in exposing only what `_dir8_simple` reads."""

    def __init__(self, rows: list[tuple[str, float]]):
        # rows are (block name, ink density); ink is stored as a pixel count so
        # the stub exercises the same arithmetic the real corpus does.
        area = ga.N * ga.N
        self.blocks = [b for b, _d in rows]
        self.ink = np.array([int(round(d * area)) for _b, d in rows], np.int32)
        self.count = len(rows)


def test_cjk_strokes_is_an_allowlist_exception_not_a_prefix():
    """The exception must match the block EXACTLY. A prefix would re-admit the
    dense blocks the rule exists to reject."""
    assert "CJK Strokes" in ga._DIR8_SPARSE_BLOCK_EXCEPTIONS
    assert "CJK" in ga._DIR8_DENSE_BLOCK_PREFIXES, (
        "the dense-prefix rule must stay in place; only named blocks are excepted")
    for name in ga._DIR8_SPARSE_BLOCK_EXCEPTIONS:
        assert name not in ("CJK", "CJK "), "an exception must name a whole block"


@pytest.mark.parametrize("density", [0.03, 0.05, 0.12, 0.30])
def test_cjk_strokes_admitted_across_the_sparse_window(density):
    c = _StubCorpus([("CJK Strokes", density)])
    assert ga._dir8_simple(c, 0) is True


@pytest.mark.parametrize("block", [
    "CJK Unified Ideographs",
    "CJK Unified Ideographs Extension A",
    "CJK Compatibility Ideographs",
    "CJK Radicals Supplement",
    "CJK Symbols and Punctuation",
    "Hangul Syllables",
    "Tangut",
    "Yi Syllables",
    "Egyptian Hieroglyphs",
])
def test_dense_blocks_are_still_rejected(block):
    """Regression guard: the allowlist must not reopen the dense blocks."""
    c = _StubCorpus([(block, 0.12)])
    assert ga._dir8_simple(c, 0) is False


def test_sparse_floor_is_lower_only_for_the_excepted_block():
    """U+31C0 measures 0.035 and U+31D4 measures 0.023 on the 16x16 raster —
    both under the general 0.04 floor. The lower floor exists for exactly that,
    and must not leak to other blocks."""
    assert ga._DIR8_INK_MIN_SPARSE_EXCEPTION < ga._DIR8_INK_MIN
    below_general = 0.5 * (ga._DIR8_INK_MIN_SPARSE_EXCEPTION + ga._DIR8_INK_MIN)
    assert ga._dir8_simple(_StubCorpus([("CJK Strokes", below_general)]), 0) is True
    assert ga._dir8_simple(_StubCorpus([("Arrows", below_general)]), 0) is False


def test_ceiling_is_unchanged_for_the_excepted_block():
    """Only the floor moved. A genuinely dense glyph stays out even in CJK Strokes."""
    over = ga._DIR8_INK_MAX + 0.05
    assert ga._dir8_simple(_StubCorpus([("CJK Strokes", over)]), 0) is False


# ---------------------------------------------------------------------------
# Cache-backed checks (the .run/ feature cache is regenerable and gitignored)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus():
    try:
        return ga.Corpus()
    except SystemExit as exc:
        pytest.skip(f"glyph feature cache unavailable: {exc}")


def _rows_for(c, cps):
    return [(cp, c.i(cp)) for cp in cps]


def test_canopy_glyph_ids_764_779_are_dir8_candidates(corpus):
    """The whole 764-779 canopy run must now pass dir8 candidacy. Before the
    allowlist exception every one of these was rejected on the block name alone."""
    present = [(cp, i) for cp, i in _rows_for(corpus, CANOPY_CPS) if i is not None]
    if not present:
        pytest.skip("CJK Strokes not in the built feature cache "
                    "(rebuild with: glyph_features.py --blocks \"CJK Strokes\")")
    assert len(present) == len(CANOPY_CPS), "the canopy run is only partly cached"
    rejected = [f"U+{cp:04X}" for cp, i in present if not ga._dir8_simple(corpus, i)]
    assert rejected == [], f"canopy glyphs still excluded from dir8: {rejected}"


def test_registered_stroke_review_families_are_dir8_candidates(corpus):
    """The leaf sweep / hook / twig / fork / turn sets registered in the viewer
    must be reachable on the dir8 axis, not only on the foliage-stroke screen."""
    present = [(cp, i) for cp, i in _rows_for(corpus, STROKE_FAMILY_CPS) if i is not None]
    if not present:
        pytest.skip("CJK Strokes not in the built feature cache")
    eligible = [cp for cp, i in present if ga._dir8_simple(corpus, i)]
    # U+31C0..U+31E3 are all single-stroke marks; the density ceiling should not
    # be excluding any of them.
    assert len(eligible) == len(present), (
        "some registered stroke glyphs are outside the dir8 density window: "
        + " ".join(f"U+{cp:04X}" for cp, i in present if not ga._dir8_simple(corpus, i)))


def test_find_directional_accepts_the_block_without_error(corpus):
    """dir8 discovery must run over a CJK Strokes scope rather than short-circuit
    on the block name."""
    allow = ga.block_allow(corpus, "CJK Strokes")
    if not allow:
        pytest.skip("CJK Strokes not in the built feature cache")
    fams = ga.find_directional(corpus, min_frames=4, allow=allow)
    assert isinstance(fams, list)
    for fam in fams:
        assert 4 <= len(fam) <= 8
        for i in fam:
            assert corpus.blocks[i] == "CJK Strokes"


def test_dense_cjk_block_in_the_real_cache_is_still_rejected(corpus):
    """Same guard as the stub test, but against real cached glyphs."""
    dense = [i for i in range(corpus.count)
             if corpus.blocks[i].startswith("CJK")
             and corpus.blocks[i] not in ga._DIR8_SPARSE_BLOCK_EXCEPTIONS]
    if not dense:
        pytest.skip("no dense CJK block in the built feature cache")
    assert all(not ga._dir8_simple(corpus, i) for i in dense)

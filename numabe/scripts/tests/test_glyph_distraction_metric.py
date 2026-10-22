# =============================================================================
# test_glyph_distraction_metric.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# glyph_features.distraction_components scores how strongly a glyph pulls a
# reader's eye out of the line art. The design source is quoted in that module's
# docstring: the Stone Story RPG ASCII-art tutorials archived under
# articles/2026-09-07-stone-story-video-transcripts-media/, where alphanumerics
# are described as activating "a different part of the brain that deals with
# language recognition" and as "usually an unwanted distraction".
#
# Two properties matter and are pinned here:
#
#   1. THE ALPHANUMERIC FLAG IS CATEGORICAL. The source treats a letter or digit
#      as a different KIND of mark, not a heavier one. So the flag must dominate:
#      any alphanumeric outranks any non-alphanumeric, including the whitelisted
#      ones the author still uses on purpose.
#   2. AMONG NON-ALPHANUMERICS, WEIGHT STILL ORDERS BY LOUDNESS. A hash outranks
#      a period. This is what stops the metric degenerating into the flag alone,
#      and it is the case the contrast term was reshaped to get right.
#
# Most tests here are pure arithmetic over (codepoint, ink, ncomp, family median)
# and need no font cache. The corpus-backed tests skip when .run/ is not built.
#
# NOTHING HERE IS A RUNTIME CLAIM. The metric is an offline authoring aid; no
# atlas, compiler or renderer surface consumes it.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_distraction_metric.py -q
# =============================================================================
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/tests/<this file> -> scripts/tests -> scripts -> repository root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("numpy")
gf = pytest.importorskip("glyph_features")
ga = pytest.importorskip("glyph_audit")

AREA = gf.N * gf.N
# Ink counts measured on the unifont-17.0.04 16x16 raster; used so the pure-unit
# tests exercise realistic operands without needing the cache.
INK_HASH = 28      # '#'  density ~0.109
INK_DOT = 4        # '.'  density ~0.016
MEDIAN_LATIN = 0.062


def w(cp, ink, ncomp=1, median=MEDIAN_LATIN):
    return gf.distraction_weight(cp, ink, ncomp, median)


# ---------------------------------------------------------------------------
# 1. the alphanumeric hard flag
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ch", list("abzABZ0159"))
def test_ascii_letters_and_digits_are_flagged(ch):
    assert gf.is_alphanumeric(ord(ch)) is True


@pytest.mark.parametrize("ch", list("#.-_|/\\()'\":!*+=~^,;<>[]{}"))
def test_punctuation_and_line_marks_are_not_flagged(ch):
    assert gf.is_alphanumeric(ord(ch)) is False


@pytest.mark.parametrize("cp", [
    0x00E9,   # LATIN SMALL LETTER E WITH ACUTE  (category Ll)
    0x03B1,   # GREEK SMALL LETTER ALPHA         (category Ll)
    0x0645,   # ARABIC LETTER MEEM               (category Lo)
    0x30A2,   # KATAKANA LETTER A                (category Lo)
    0x0660,   # ARABIC-INDIC DIGIT ZERO          (category Nd)
])
def test_non_ascii_letters_and_digits_are_flagged(cp):
    """The flag is about language recognition, not about ASCII."""
    assert gf.is_alphanumeric(cp) is True


@pytest.mark.parametrize("cp", [
    0x2500,   # BOX DRAWINGS LIGHT HORIZONTAL
    0x25B2,   # BLACK UP-POINTING TRIANGLE
    0x31C0,   # CJK STROKE T  (a stroke, not a letter)
    0x203E,   # OVERLINE
])
def test_marks_and_strokes_are_not_flagged(cp):
    assert gf.is_alphanumeric(cp) is False


def test_components_report_the_flag_and_the_authoring_sets():
    letter = gf.distraction_components(ord("B"), INK_HASH, 1, MEDIAN_LATIN)
    assert letter["alnum"] is True
    assert letter["whitelisted"] is False and letter["marginal"] is False

    kept = gf.distraction_components(ord("7"), INK_DOT, 1, MEDIAN_LATIN)
    assert kept["alnum"] is True and kept["whitelisted"] is True

    marginal = gf.distraction_components(ord("x"), INK_DOT, 1, MEDIAN_LATIN)
    assert marginal["alnum"] is True and marginal["marginal"] is True


def test_whitelist_and_marginal_sets_match_the_cited_source():
    """o5v-NS9o4yc 23:55 names the kept set; 24:37-24:52 names the marginal one."""
    assert set(gf.DISTRACT_WHITELIST) == set("oOvTl7")
    assert set(gf.DISTRACT_MARGINAL) == set("ucCx")
    assert not (gf.DISTRACT_WHITELIST & gf.DISTRACT_MARGINAL)


# ---------------------------------------------------------------------------
# 2. ordering
# ---------------------------------------------------------------------------
def test_hash_ranks_above_dot():
    """The named acceptance case: a heavy non-alphanumeric mark outranks a light
    one, so the metric is not just the alphanumeric flag wearing a number."""
    assert w(ord("#"), INK_HASH) > w(ord("."), INK_DOT)


def test_any_alphanumeric_outranks_any_non_alphanumeric():
    """The flag is categorical: even the quietest letter beats the loudest mark."""
    quietest_letter = w(ord("l"), 1)                 # one lit pixel
    loudest_mark = w(ord("#"), AREA, ncomp=8)        # fully inked, many parts
    assert quietest_letter > loudest_mark


def test_whitelisted_alphanumerics_are_still_flagged_and_still_rank_high():
    """The kept set is an authoring exemption, not a discount: the source says the
    kept glyphs 'still have that effect' (5sFVVQcUWVE 08:21)."""
    for ch in gf.DISTRACT_WHITELIST:
        row = gf.distraction_components(ord(ch), INK_DOT, 1, MEDIAN_LATIN)
        assert row["alnum"] is True
        assert row["weight"] > w(ord("#"), INK_HASH)


def test_weight_rises_with_ink_and_with_part_count():
    base = w(ord("#"), INK_HASH, ncomp=1)
    assert w(ord("#"), INK_HASH * 2, ncomp=1) > base
    assert w(ord("#"), INK_HASH, ncomp=4) > base


def test_contrast_is_one_sided_excess_over_the_family_median():
    """Heavier than the field advances; lighter recedes rather than competing.
    A symmetric |difference| would score a lone period nearly as high as a hash."""
    light = gf.distraction_components(ord("."), INK_DOT, 1, MEDIAN_LATIN)
    heavy = gf.distraction_components(ord("#"), INK_HASH, 1, MEDIAN_LATIN)
    assert light["contrast_term"] == 0.0
    assert heavy["contrast_term"] > 0.0


def test_weight_is_bounded_and_is_the_sum_of_its_terms():
    row = gf.distraction_components(ord("B"), AREA, 12, 0.05)
    total = (row["alnum_term"] + row["ink_term"]
             + row["ncomp_term"] + row["contrast_term"])
    assert row["weight"] == pytest.approx(total)
    assert 0.0 <= row["weight"] <= 1.0
    assert sum(gf.DISTRACT_WEIGHTS.values()) == pytest.approx(1.0)


def test_zero_family_median_does_not_divide_by_zero():
    row = gf.distraction_components(ord("#"), INK_HASH, 1, 0.0)
    assert 0.0 <= row["weight"] <= 1.0


# ---------------------------------------------------------------------------
# 3. the corpus-backed ranking surfaced by glyph_audit
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus():
    try:
        return ga.Corpus()
    except SystemExit as exc:
        pytest.skip(f"glyph feature cache unavailable: {exc}")


def _row(c, ch):
    i = c.i(ord(ch))
    if i is None:
        pytest.skip(f"{ch!r} is not in the built feature cache")
    return ga.distraction_row(c, i)


def test_real_corpus_ranks_hash_above_dot(corpus):
    assert _row(corpus, "#")["weight"] > _row(corpus, ".")["weight"]


def test_real_corpus_ranks_letters_above_line_marks(corpus):
    letters = [_row(corpus, ch)["weight"] for ch in "ABo7"]
    marks = [_row(corpus, ch)["weight"] for ch in "#.-_|/\\"]
    assert min(letters) > max(marks)


def test_ranking_is_sorted_and_complete(corpus):
    rows = ga.rank_distraction(corpus, ga.block_allow(corpus, "Basic Latin"))
    assert rows, "Basic Latin should be in the cache"
    weights = [r["weight"] for r in rows]
    assert weights == sorted(weights, reverse=True)
    # the top of a Basic Latin ranking must be alphanumeric
    assert rows[0]["alnum"] is True
    assert rows[-1]["alnum"] is False

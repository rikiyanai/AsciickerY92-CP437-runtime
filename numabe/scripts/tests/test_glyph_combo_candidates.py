from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import glyph_combo_candidates as gcc  # noqa: E402


def test_source_line_art_alphabet_covers_named_useful_marks():
    for ch in "_.-,':|/\\()!\u00a1\u00b7\u203e":
        assert ch in gcc.USEFUL_LINE_GLYPHS
    for ch in gcc.USEFUL_LINE_GLYPHS:
        assert not ch.isalnum(), ch


def test_exhaustive_no_cache_count_covers_all_connected_two_and_three_cell_orders():
    rows = gcc.enumerate_candidates(c=None, max_size=3, max_distraction=1.0)
    n = len(gcc.USEFUL_LINE_GLYPHS)
    shape_count = sum(len(v) for v in gcc.SHAPES.values())
    assert len(rows) == (len(gcc.SHAPES[2]) * n**2 + len(gcc.SHAPES[3]) * n**3)
    assert shape_count == 8


def test_seeded_short_combinations_are_present_in_the_exhaustive_surface():
    rows = gcc.enumerate_candidates(c=None, max_size=3, max_distraction=1.0)
    keyed = {(row["shape"], row["chars"]) for row in rows}
    assert ("line_h", ".-'") in keyed
    assert ("line_h", "'-.") in keyed
    assert ("line_h", "\\|/") in keyed
    assert ("line_h", "|||") in keyed
    assert ("domino_v", "_\u203e") in keyed


def test_horizontal_mirror_reflects_offsets_and_codepoints():
    rows = gcc.enumerate_candidates(c=None, max_size=3, max_distraction=1.0)
    # Skill §4.2: the acute accent mirrors the backtick; the apostrophe is its own mirror.
    row = next(r for r in rows if r["shape"] == "line_h" and r["chars"] == "/(`")
    assert row["mirror_chars"] == "\u00b4)\\"
    tick = next(r for r in rows if r["shape"] == "line_h" and r["chars"] == "/('")
    assert tick["mirror_chars"] == "')\\"
    assert [(c["col"], c["row"]) for c in row["mirror"]["cells"]] == [(0, 0), (1, 0), (2, 0)]


def test_source_alphanumerics_are_opt_in():
    without = gcc.enumerate_candidates(c=None, max_size=2, max_distraction=1.0)
    assert all(not any(ch.isalnum() for ch in row["chars"]) for row in without)
    with_alnum = gcc.enumerate_candidates(
        c=None,
        max_size=2,
        include_source_alnum=True,
        max_distraction=1.0,
    )
    assert any("7" in row["chars"] for row in with_alnum)

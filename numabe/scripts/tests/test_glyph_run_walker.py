# =============================================================================
# test_glyph_run_walker.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# scripts/glyph_run_walker.py generates coherent glyph RUNS from measured
# geometry only (contact / step / turn / end / repeat edge rules over the
# whole-repertoire cell-feature cache). The acceptance falsifier, agreed with
# the operator, is that the walker REDISCOVERS the author's named combinations
# from the seam graph without being told them:
#
#   the stair step  _.-´  and its mirror  `-._       (skill §4.4, mined 24x / 22x)
#   the rotation triple  \|/                          (skill §4.4)
#   the bracketed orb  (o)                            (authored dictionary)
#   the junction  _|_  and the brick cup  |__|        (mined 83x / 67x)
#   the arch  ( ‾ )  and  /‾\  (cap) and  \_/ (cup)   (operator, convexity)
#
# Each test walks a SMALL explicit glyph set (seconds, not minutes) so the
# suite stays fast; the full plate run is an offline command. The mined
# frequencies are never fed to the walker — they are only why these runs are
# the ones asserted. Skips when the v2 cell-feature cache is absent.
# NOTHING HERE IS A RUNTIME CLAIM.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_run_walker.py -q
# =============================================================================
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

pytest.importorskip("scipy")
pytest.importorskip("skimage")

import glyph_cell_features as gcf  # noqa: E402
import glyph_run_walker as grw  # noqa: E402


@pytest.fixture(scope="module")
def cache():
    if not gcf.CELL_CACHE_NPZ.exists() or not gcf.CELL_CACHE_META.exists():
        pytest.skip("cell-feature cache not built (python3 scripts/glyph_cell_features.py --build)")
    z, meta = gcf.load_cache()
    if int(meta.get("schema", 0)) < 2:
        pytest.skip("cell-feature cache schema < 2")
    return z, meta


def _walk(chars: str, length: int) -> dict[str, dict]:
    a = argparse.Namespace(width=8, relation="beside", plate=False, chars=chars, line_like=False, blocks=None, exclude_alnum=False,
                           length=length, jaccard=0.5, max_gap=4, step_h=5, step_dy=8.0, turn_max=45.0,
                           per_node=24, per_start=0, budget=200000, max_score=100000, no_dsm=True)
    rows = grw.run_walker(a)
    out = {}
    for r in rows:
        out.setdefault(r["chars"], r)
        for m in r["members"]:
            out.setdefault(m["chars"], r)
    return out


def test_stair_step_and_its_mirror_are_rediscovered(cache):
    runs = _walk("_.-\u00b4\u0060", 4)
    assert "_.-\u00b4" in runs and runs["_.-\u00b4"]["profile"] == "rising"
    assert runs["_.-\u00b4"]["rules"] == ["step", "step", "step"]
    assert "\u0060-._" in runs and runs["\u0060-._"]["profile"] == "falling"


def test_rotation_triple_is_rediscovered_by_turn_edges(cache):
    runs = _walk("\\|/", 3)
    assert "\\|/" in runs
    r = runs["\\|/"]
    assert r["rules"] == ["turn", "turn"] and r["profile"] == "flat"
    o = r["orientation"]
    assert o[0] < o[1] < o[2]                     # monotone orientation = rotation


def test_orb_and_junction_and_brick_cup(cache):
    runs = _walk("(o)_|", 4)
    assert "(o)" in runs and runs["(o)"]["rules"] == ["turn", "turn"]
    assert "_|_" in runs and runs["_|_"]["rules"] == ["contact", "contact"]
    assert "|__|" in runs and runs["|__|"]["profile"] == "cup"


def test_arches_cap_and_cup_via_end_rule(cache):
    runs = _walk("(\u203e)/\\_", 3)
    assert "(\u203e)" in runs and runs["(\u203e)"]["profile"] == "cap" and runs["(\u203e)"]["rules"] == ["end", "end"]
    assert "/\u203e\\" in runs and runs["/\u203e\\"]["profile"] == "cap"
    assert "\\_/" in runs and runs["\\_/"]["profile"] == "cup"
    # cap and cup families differ by profile, mirror keeps the profile
    assert runs["(\u203e)"]["family"] != runs["\\_/"]["family"]


def test_repeat_runs_and_collapse(cache):
    runs = _walk("|_", 3)
    assert "|||" in runs and runs["|||"]["rules"] == ["repeat", "repeat"]
    assert "___" in runs and runs["___"]["profile"] == "flat"

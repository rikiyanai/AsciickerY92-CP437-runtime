# =============================================================================
# test_glyph_consume_directed_export.py
# =============================================================================
# WHAT THIS TEST FILE CHECKS
# --------------------------
# `glyph_audit.py consume` is the ONLY family export addressed by atlas glyph_id
# rather than by Unicode codepoint — it is the surface a compiler or runtime side
# would read a discovered family back from. It used to run four axes only (ramp,
# cycle, spin, topo), all of them undirected. The two DIRECTED axes were missing:
#
#   dir8    8-way directed groups (N NE E SE S SW W NW), so a caller can ask for
#           "the glyph_id this family uses when the mark points south-east";
#   mirror  left/right chirality pairs, so a caller can ask for the reflection of
#           a glyph it already elected.
#
# Without them a directed group could be discovered by the audit and then never
# read back by glyph id, which is the whole point of the consume seam.
#
# These tests drive cmd_consume with the discovery operators stubbed out, so the
# assertions are about the EXPORT CONTRACT (which modes appear, which fields each
# carries, that headings are named and mirror roles are labelled) and not about
# how many families today's font corpus happens to yield.
#
# NOTHING HERE IS A RUNTIME CLAIM. consume writes a dry-run proposal under the
# gitignored .run/ cache; these tests redirect even that to a temporary directory.
#
# Run with:
#     python3 -m pytest scripts/tests/test_glyph_consume_directed_export.py -q
# =============================================================================
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

# scripts/tests/<this file> -> scripts/tests -> scripts -> repository root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("numpy")
ga = pytest.importorskip("glyph_audit")
gf = pytest.importorskip("glyph_features")

# A directed arrow set and a chirality pair, both certain to be in any corpus
# built over the Arrows / Basic Latin blocks.
DIR8_CPS = (0x2196, 0x2197, 0x2198, 0x2199)
MIRROR_CPS = (0x0028, 0x0029)


@pytest.fixture(scope="module")
def corpus():
    try:
        return ga.Corpus()
    except SystemExit as exc:
        pytest.skip(f"glyph feature cache unavailable: {exc}")


def _indices(c, cps):
    idx = [c.i(cp) for cp in cps]
    if any(i is None for i in idx):
        pytest.skip("required codepoints are not in the built feature cache")
    return idx


def _run_consume(monkeypatch, tmp_path, c, dir8_group, mirror_pair):
    """Run cmd_consume with discovery stubbed and the cache redirected."""
    gid_of = {}
    for n, i in enumerate(list(dir8_group) + list(mirror_pair)):
        gid_of[int(c.cps[i])] = 9000 + n

    monkeypatch.setattr(ga, "load_admitted", lambda: gid_of)
    monkeypatch.setattr(ga, "find_ramps", lambda *a, **k: [])
    monkeypatch.setattr(ga, "find_cycles", lambda *a, **k: [])
    monkeypatch.setattr(ga, "find_spins", lambda *a, **k: [])
    monkeypatch.setattr(ga, "find_topo_families", lambda *a, **k: [])
    monkeypatch.setattr(ga, "find_directional", lambda *a, **k: [list(dir8_group)])
    monkeypatch.setattr(ga, "find_mirror", lambda *a, **k: [list(mirror_pair)])
    monkeypatch.setattr(gf, "CACHE_DIR", tmp_path)

    args = argparse.Namespace(per_mode=10, limit=10, json=True)
    assert ga.cmd_consume(c, args) == 0
    return json.loads((tmp_path / "consume_proposal.json").read_text())


def test_consume_modes_include_the_directed_axes():
    assert "dir8" in ga.CONSUME_MODES
    assert "mirror" in ga.CONSUME_MODES
    # the pre-existing axes must not have been dropped in the process
    for mode in ("ramp", "cycle", "spin", "topo"):
        assert mode in ga.CONSUME_MODES


def test_consume_emits_dir8_and_mirror_families(monkeypatch, tmp_path, corpus):
    d8 = _indices(corpus, DIR8_CPS)
    mp = _indices(corpus, MIRROR_CPS)
    doc = _run_consume(monkeypatch, tmp_path, corpus, d8, mp)

    modes = {f["mode"] for f in doc["families"]}
    assert "dir8" in modes, "consume must export directed 8-way groups"
    assert "mirror" in modes, "consume must export chirality pairs"


def test_dir8_family_is_glyph_id_addressed_and_heading_named(monkeypatch, tmp_path, corpus):
    d8 = _indices(corpus, DIR8_CPS)
    mp = _indices(corpus, MIRROR_CPS)
    doc = _run_consume(monkeypatch, tmp_path, corpus, d8, mp)

    fam = next(f for f in doc["families"] if f["mode"] == "dir8")
    assert len(fam["glyph_ids"]) == len(fam["cps"]) == len(d8)
    assert all(isinstance(g, int) for g in fam["glyph_ids"]), (
        "the point of consume is glyph_id addressing")
    # every member carries a compass heading, drawn from the canonical name list
    assert len(fam["dir8"]) == len(fam["glyph_ids"])
    assert set(fam["dir8"]).issubset(set(ga._DIR8_NAMES))


def test_mirror_family_labels_its_left_and_right_members(monkeypatch, tmp_path, corpus):
    d8 = _indices(corpus, DIR8_CPS)
    mp = _indices(corpus, MIRROR_CPS)
    doc = _run_consume(monkeypatch, tmp_path, corpus, d8, mp)

    fam = next(f for f in doc["families"] if f["mode"] == "mirror")
    assert len(fam["glyph_ids"]) == 2
    assert fam["mirror"]["left"] == fam["glyph_ids"][0]
    assert fam["mirror"]["right"] == fam["glyph_ids"][1]
    assert fam["mirror"]["left"] != fam["mirror"]["right"]


def test_every_family_carries_the_distraction_column(monkeypatch, tmp_path, corpus):
    d8 = _indices(corpus, DIR8_CPS)
    mp = _indices(corpus, MIRROR_CPS)
    doc = _run_consume(monkeypatch, tmp_path, corpus, d8, mp)

    assert doc["schema"] == 2, "the added axes and column are a schema change"
    assert doc["distract_weights"] == gf.DISTRACT_WEIGHTS
    for fam in doc["families"]:
        n = len(fam["glyph_ids"])
        assert len(fam["distract"]) == n
        assert len(fam["distract_alnum"]) == n
        assert all(0.0 <= v <= 1.0 for v in fam["distract"])
        assert fam["distract_max"] == pytest.approx(max(fam["distract"]))

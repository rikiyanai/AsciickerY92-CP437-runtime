"""FL-4932: TMG roof-field height rung in compile_canonical_entities and its lowering.

Scope: the ladder order, the structure gate, dated contradictions, summary
refusals, byte identity without a summary, and the spec compiler's reading of
the TMG claim. Synthetic sources only; nothing here measures a real building.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.pipeline import compile_canonical_entities as C  # noqa: E402
from scripts.pipeline import compile_world_ir_building_specs as B  # noqa: E402


def _osm(tags=None):
    return {"feature_id": "f", "obs_id": "o", "observed_at": "2026-09-01", "tags": tags or {}}


def _plateau(h=None):
    attrs = {"measured_height_m": {"value": h}} if h is not None else {}
    return {"feature_id": "p", "obs_id": "po", "observed_at": "2026-03-16", "attributes": attrs}


def _row(source, median, p90, status="compiled"):
    if status != "compiled":
        return {"source": source, "status": status, "reason": "only 3 roof returns; insufficient evidence"}
    return {"source": source, "status": "compiled", "measured_roof_m": {"p10": 0.0, "median": median, "p90": p90}}


# --- ladder order ------------------------------------------------------------

def test_plateau_outranks_tmg():
    claim, _ = C._height_claims(_osm(), _plateau(12.0), 20.0)
    assert (claim["source"], claim["value"]) == ("plateau", 12.0)


def test_explicit_osm_height_outranks_tmg():
    claim, _ = C._height_claims(_osm({"height": "7"}), None, 20.0)
    assert (claim["source"], claim["value"]) == ("osm", 7.0)


def test_tmg_outranks_levels_and_keeps_the_levels_value():
    claim, _ = C._height_claims(_osm({"building:levels": "2"}), None, 8.4)
    assert (claim["source"], claim["value"], claim["evidence_state"]) == (C.TMG_SOURCE, 8.4, "directly_observed")
    assert claim["values"] == [{"value": 6.0, "source": "osm", "evidence_state": "derived"}]
    assert claim["temporal_validity"] == C.TMG_TEMPORAL


def test_tmg_replaces_the_synthetic_prior():
    claim, _ = C._height_claims(_osm(), None, 5.5)
    assert (claim["source"], claim["value"]) == (C.TMG_SOURCE, 5.5)


def test_no_tmg_value_leaves_the_ladder_unchanged():
    for osm, plateau in [(_osm(), None), (_osm({"building:levels": "3"}), None), (None, _plateau(9.0))]:
        assert C._height_claims(osm, plateau) == C._height_claims(osm, plateau, None)


# --- structure gate and contradictions ----------------------------------------

def test_structure_gate_uses_median_and_returns_p90():
    assert C._lidar_structure_height(_row("way/1", 6.0, 7.5)) == 7.5
    assert C._lidar_structure_height(_row("way/1", 1.9, 7.5)) is None
    assert C._lidar_structure_height(_row("way/1", None, None, status="skipped")) is None
    assert C._lidar_structure_height(None) is None


def test_contradiction_is_dated_and_keeps_the_footprint():
    c = C._lidar_contradiction(_row("way/9", 0.2, 0.9))
    assert c["temporal_validity"] == C.TMG_TEMPORAL and c["median_roof_m"] == 0.2
    assert "kept" in c["disposition"]
    assert C._lidar_contradiction(_row("way/9", 4.0, 5.0)) is None
    skipped = C._lidar_contradiction(_row("way/9", None, None, status="skipped"))
    assert "refused" in skipped["finding"]


def test_lidar_row_prefers_the_osm_ring_then_plateau():
    lidar = {"way/1": _row("way/1", 3, 4), "plateau/bldg_x": _row("plateau/bldg_x", 5, 6)}
    assert C._lidar_row(lidar, "way/1", "bldg_x")["source"] == "way/1"
    assert C._lidar_row(lidar, "way/2", "bldg_x")["source"] == "plateau/bldg_x"
    assert C._lidar_row(None, "way/1", None) is None


# --- summary refusals -----------------------------------------------------------

def _summary(tmp_path, **over):
    doc = {"schema": C.TMG_ROOF_SUMMARY_SCHEMA, "tiles": ["a.las"], "tile_sha256": ["0" * 64],
           "rows": [_row("way/1", 5, 6)]}
    doc.update(over)
    p = tmp_path / "roof_field_summary.json"
    p.write_text(json.dumps(doc))
    return p


def test_summary_refusals(tmp_path):
    with pytest.raises(ValueError):
        C.load_lidar_roofs(_summary(tmp_path, schema="other"))
    with pytest.raises(ValueError):
        C.load_lidar_roofs(_summary(tmp_path, tile_sha256=None))
    with pytest.raises(ValueError):
        C.load_lidar_roofs(_summary(tmp_path, rows=[_row("way/1", 5, 6), _row("way/1", 5, 6)]))
    rows, prov = C.load_lidar_roofs(_summary(tmp_path))
    assert set(rows) == {"way/1"} and len(prov["sha256"]) == 64


# --- build_entities end to end on synthetic sources -------------------------------

@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    osm = {"way/1": _osm({"building:levels": "2"}), "way/2": _osm(), "way/3": _osm({"height": "4"}),
           "way/4": _osm()}
    plateau = {"bldg_a": _plateau(10.0), "bldg_b": _plateau(3.0)}
    monkeypatch.setattr(C, "_osm_buildings", lambda path: osm)
    monkeypatch.setattr(C, "_plateau_buildings", lambda path: plateau)
    receipt = tmp_path / "match.json"
    receipt.write_text(json.dumps({"match": {"matches": [
        {"osm_source_ref": "way/4", "plateau_gml_id": "bldg_a", "iou": 0.9}]}}))
    summary = _summary(tmp_path, rows=[
        _row("way/1", 7.0, 8.4),          # levels -> TMG
        _row("way/2", 0.3, 0.8),          # no structure -> contradiction, prior kept
        _row("way/3", 9.0, 9.9),          # explicit tag wins
        _row("way/4", 2.5, 11.0),         # PLATEAU pair wins
        _row("plateau/bldg_b", 5.0, 5.2),  # PLATEAU-only wins
    ])
    return tmp_path, receipt, summary


def _by_source(doc):
    out = {}
    for e in doc["entities"]:
        for s in e["source_bridge"]:
            out[f"{s['source']}:{s['source_object_id']}"] = e
    return out


def test_build_entities_applies_the_rung_only_where_it_ranks(synthetic):
    tmp, receipt, summary = synthetic
    doc = C.build_entities(tmp / "o", tmp / "p", receipt, None, summary)
    ent = _by_source(doc)
    assert ent["osm:way/1"]["claims"]["height_m"]["value"] == 8.4
    assert ent["osm:way/2"]["claims"]["height_m"]["evidence_state"] == "synthetic"
    assert ent["osm:way/2"]["contradictions"][0]["median_roof_m"] == 0.3
    assert ent["osm:way/3"]["claims"]["height_m"]["value"] == 4.0
    assert ent["osm:way/4"]["claims"]["height_m"]["value"] == 10.0
    assert ent["plateau:bldg_b"]["claims"]["height_m"]["value"] == 3.0
    assert doc["counts"]["height_from_tmg"] == 1
    assert doc["counts"]["tmg_contradictions"] == 1
    assert len(doc["entities"]) == 5  # nothing deleted


def test_build_entities_without_summary_carries_no_new_keys(synthetic):
    tmp, receipt, summary = synthetic
    plain = C.build_entities(tmp / "o", tmp / "p", receipt, None)
    assert "lidar_roof_summary" not in plain["inputs"]
    assert "lidar_contradiction" not in plain["rules"]
    assert "height_from_tmg" not in plain["counts"] and "tmg_contradictions" not in plain
    assert all("contradictions" not in e for e in plain["entities"])
    with_summary = C.build_entities(tmp / "o", tmp / "p", receipt, None, summary)
    same = {e["canonical_id"]: e["claims"]["height_m"] for e in plain["entities"]}
    for e in with_summary["entities"]:
        if e["claims"]["height_m"].get("source") != C.TMG_SOURCE:
            assert e["claims"]["height_m"] == same[e["canonical_id"]]


# --- spec compiler lowering ------------------------------------------------------------

def test_spec_compiler_lowers_the_tmg_claim_as_measured():
    entity = {"canonical_id": "bldg_x", "claims": {"height_m": C._height_claims(_osm(), None, 8.4)[0]}}
    out = B._canonical_height_claim(entity)
    assert out["roof_height_m"] == 8.4
    assert out["height_source_key"] == B.TMG_HEIGHT_SOURCE_KEY
    assert out["height_provenance_class"] == "measured"


def test_spec_compiler_still_defers_osm_claims_to_its_ladder():
    entity = {"canonical_id": "bldg_y", "claims": {"height_m": C._height_claims(_osm({"height": "4"}), None, 8.4)[0]}}
    assert B._canonical_height_claim(entity) is None


def test_source_constants_agree():
    assert B.TMG_SOURCE == C.TMG_SOURCE

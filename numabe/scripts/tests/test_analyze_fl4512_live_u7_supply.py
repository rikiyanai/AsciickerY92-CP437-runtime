"""Pure contracts for the FL-4512 live U7 full-supply replay analyzer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "scripts/adhoc/2026-08-17-FL-4512-live-U7-supply-replay-analyzer.py"
SPEC = importlib.util.spec_from_file_location("fl4512_live_u7_supply", TOOL_PATH)
assert SPEC and SPEC.loader
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def _descriptor(glyph_id: int, mask8: int) -> dict:
    _skeleton, endpoints, junctions = TOOL.topology_counts(mask8)
    return {
        "glyph_id": glyph_id,
        "mask8": mask8,
        "endpoint_count": endpoints,
        "junction_count": junctions,
    }


def _cell(merged: int, silhouette: int, selected_row: list[dict], x: int) -> dict:
    winner = TOOL._nearest(merged, selected_row)
    winner_descriptor = next(item for item in selected_row if item["glyph_id"] == winner["glyph_id"])
    return {
        "x": x,
        "y": 9,
        "final_gid": winner["glyph_id"],
        "mesh_bundle_merged_mask_8x8": f"{merged:016x}",
        "mesh_feature_observer": {"mask8_by_kind": [f"{silhouette:016x}"]},
        "u7_mask": {
            "score_num": winner["score"],
            "winner_mask8": f"{winner_descriptor['mask8']:016x}",
        },
    }


def test_replay_uses_lowest_gid_for_full_supply_ties_and_keeps_cell_weighting() -> None:
    merged, silhouette = 0xFF, 0x0F
    selected_row = [_descriptor(20, silhouette), _descriptor(30, 0x01)]
    # Glyph ids 3 and 5 are exact full-supply ties; gid 3 must win.
    supply = selected_row + [_descriptor(5, merged), _descriptor(3, merged)]
    result = TOOL.analyze_live_cells(
        [_cell(merged, silhouette, selected_row, 1), _cell(merged, silhouette, selected_row, 2)],
        supply,
        selected_row,
        grammar_worst_score=660,
    )

    assert result["cell_count"] == 2
    assert result["unique_merged_mask_count"] == 1
    assert result["merged"]["full_supply"]["avg"] == 0
    assert result["merged"]["current_row"]["avg"] > 0
    row = result["per_unique_merged_mask"][0]
    assert row["cell_multiplicity"] == 2
    assert row["full_supply"] == {"glyph_id": 3, "score": 0}
    assert result["silhouette_only"]["current_winner_globally_optimal"] == {"count": 2, "ratio": 1.0}


def test_replay_rejects_a_live_winner_that_is_not_the_selected_row_minimum() -> None:
    selected_row = [_descriptor(20, 0x0F), _descriptor(30, 0x01)]
    cell = _cell(0xFF, 0x0F, selected_row, 1)
    cell["final_gid"] = 30
    with pytest.raises(TOOL.ReplayError, match="selected_row_winner_mismatch"):
        TOOL.analyze_live_cells([cell], selected_row, selected_row, grammar_worst_score=660)


def test_bridge_header_requires_valid_bridge_and_active_stroke() -> None:
    header = {
        "kind": "header",
        "schema": TOOL.BRIDGE_SCHEMA,
        "bridge_valid": 1,
        "active_stroke_observation_valid": True,
        "active_stroke_frame": {"failure": 0},
    }
    TOOL._validate_header(header)
    header["active_stroke_frame"]["failure"] = 1
    with pytest.raises(TOOL.ReplayError, match="active_stroke_frame_invalid"):
        TOOL._validate_header(header)


def test_live_validation_requires_the_complete_admitted_candidate_set() -> None:
    target = 0xFF
    selected_row = [_descriptor(20, 0x0F), _descriptor(30, 0x01)]
    winner = TOOL._nearest(target, selected_row)
    descriptor = next(item for item in selected_row if item["glyph_id"] == winner["glyph_id"])
    _skeleton, endpoints, junctions = TOOL.topology_counts(target)
    cell = {
        "x": 1, "y": 2, "mesh_bundle_valid": 1, "mesh_bundle_state": 1,
        "score_provenance_valid": 1, "scorer_id": TOOL.U7_SCORER_ID,
        "final_gid": winner["glyph_id"],
        "mesh_bundle_merged_mask_8x8": f"{target:016x}",
        "mesh_feature_observer": {
            "kind_order": ["silhouette"], "mask8_by_kind": [f"{target:016x}"],
            "ref_count_by_kind": [1],
        },
        "u7_mask": {
            "target_mask8": f"{target:016x}", "winner_mask8": f"{descriptor['mask8']:016x}",
            "target_endpoint_count": endpoints, "target_junction_count": junctions,
            "winner_endpoint_count": descriptor["endpoint_count"],
            "winner_junction_count": descriptor["junction_count"],
            "H": (target ^ descriptor["mask8"]).bit_count(),
            "dE": abs(endpoints - descriptor["endpoint_count"]),
            "dJ": abs(junctions - descriptor["junction_count"]),
            "score_num": winner["score"],
            "candidate_score_order": [{"glyph_id": winner["glyph_id"]}],
        },
    }
    with pytest.raises(TOOL.ReplayError, match="candidate_score_order_invalid"):
        TOOL._validate_live_cell(cell, {item["glyph_id"]: item for item in selected_row}, [20, 30])


def test_live_validation_rejects_a_winner_outside_the_admitted_row() -> None:
    selected_row = [_descriptor(20, 0x0F), _descriptor(30, 0x01)]
    cell = {
        "x": 1, "y": 2, "final_gid": 3, "mesh_bundle_valid": 1,
        "mesh_bundle_state": 1, "score_provenance_valid": 1,
        "scorer_id": TOOL.U7_SCORER_ID,
        "mesh_bundle_merged_mask_8x8": "000000000000000f",
        "mesh_feature_observer": {"kind_order": ["silhouette"], "mask8_by_kind": ["000000000000000f"], "ref_count_by_kind": [1]},
        "u7_mask": {"target_mask8": "000000000000000f", "winner_mask8": "000000000000000f"},
    }
    with pytest.raises(TOOL.ReplayError, match="winner_not_in_selected_row"):
        TOOL._validate_live_cell(cell, {item["glyph_id"]: item for item in selected_row}, [20, 30])

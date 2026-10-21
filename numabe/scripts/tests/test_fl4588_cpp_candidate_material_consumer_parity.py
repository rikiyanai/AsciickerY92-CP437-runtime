# =============================================================
# test_fl4588_cpp_candidate_material_consumer_parity.py
# =============================================================
# FL-4588 task 76 (U9): C++/Python parity for the Material Paint live-candidate
# branch. Feeds ONE synthetic live candidate snapshot (production_bridge_cells +
# candidate_material_paint) to BOTH independent evaluators and asserts canonical
# byte equality, commit_armed=true, and the exact delta [{x:0,y:0}].
#
# Root owns the Python evaluator branch + its focused Python test; this file is
# the C++ parity twin's focused test. Hidden/headless C++; no headed proof.
# =============================================================
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FL4588 = ROOT / "scripts/fl4588"
sys.path.insert(0, str(FL4588))
from asciiid_counterfactual import (  # noqa: E402
    canonical_bytes,
    evaluate,
    production_role_selection,
    value_hash,
)

TERRAIN_BRUSH_FIXTURE = (
    ROOT / "assets/asciiid/counterfactual/terrain_material_brush_v1.golden.json"
)


def _build_cpp(tmp_path: Path) -> Path:
    cjson_object = tmp_path / "cJSON.o"
    subprocess.run(
        ["cc", "-Iengine/third_party/cjson", "-c",
         "engine/third_party/cjson/cJSON.c", "-o", str(cjson_object)],
        cwd=ROOT, check=True,
    )
    binary = tmp_path / "asciiid_counterfactual_cpp"
    subprocess.run(
        ["c++", "-std=c++17", "-I.",
         "apps/asciicker-presenter/asciiid_counterfactual.cpp",
         "scripts/fl4588/asciiid_counterfactual_cli.cpp",
         str(cjson_object), "-o", str(binary)],
        cwd=ROOT, check=True,
    )
    return binary


def _synthetic_live_candidate_case():
    """Build one live-candidate snapshot exactly like root's Python test: strip
    the fixture material_scene_signatures, attach genuine production bridge cells
    and a candidate render whose descriptor paints material 4 -> 5 on cell (0,0)."""
    case = json.loads(TERRAIN_BRUSH_FIXTURE.read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(case["snapshot"])
    action = copy.deepcopy(case["action"])
    model = snapshot["document"]["model"]
    model.pop("material_scene_signatures")

    production_cells = []
    candidate_cells = []
    for index, fixture_cell in enumerate(snapshot["renderer"]["model"]["cells"]):
        terrain_cell = model["terrain_cells"][index]
        source_id = f"cell:{terrain_cell['world_x']}:{terrain_cell['world_y']}"
        terrain_cell["terrain_cell_id"] = source_id
        coord = copy.deepcopy(fixture_cell["cell"])
        production_cells.append({
            "cell": coord,
            "profile_namespace": 1,
            "material_id": 4,
            "dispatch_surface": 1,
            "resolve_elev": 0,
            "resolve_shade": 2,
            "eligible": 1,
            "selected_role": 6,
            "winner_gid": 627,
            "presented_gid": 627,
            "pool_address": {
                "profile_namespace": 1,
                "material_key": 4,
                "route_key": 6,
                "lane_id": 0,
                "secondary_lane_id": 1,
            },
            "result_kind": 1,
            "absence_reason": 0,
            "descriptor_id": "fl4260.global6.v1",
            "pool_revision": 18,
            "temporal": {"decision": "keep_current"},
            "ramp": 0,
            "density": 1,
            "fact_mask": 0,
            "vertical_relation": -1,
            "direction": -1,
            "flow": -1,
            "surface_id": {
                "world_generation": 9,
                "owner_kind": 0,
                "owner_x": terrain_cell["world_x"],
                "owner_y": terrain_cell["world_y"],
                "anchor_u_q": 0,
                "anchor_v_q": 0,
            },
            "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
            "terrain_source_cell_ids": [source_id],
            "screen": {"fg": 102, "bg": 16, "final_gid": 627},
        })
        candidate_cells.append({
            "cell": coord,
            "material_id": 5 if index == 0 else 4,
            "resolve_elev": 0,
            "resolve_shade": 2,
            "dispatch_surface": 1,
            "terrain_source_cell_id": source_id,
            "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
        })

    snapshot["document"]["sha256"] = value_hash(model)
    snapshot["renderer"]["production_bridge_cells"] = production_cells
    after_hash = case["expected_action_impact"]["expected_artifact_mutations"][0][
        "after_sha256"
    ]
    snapshot["candidate_material_paint"] = {
        "schema": "asciiid.material_paint_candidate.v1",
        "base_snapshot_sha256": snapshot["snapshot_sha256"],
        "candidate_document_sha256": after_hash,
        "changed_terrain_cell_count": 1,
        "cells": candidate_cells,
    }
    return snapshot, action


def test_cpp_python_parity_on_live_candidate_snapshot(tmp_path):
    snapshot, action = _synthetic_live_candidate_case()

    python_impact = evaluate(snapshot, action)
    assert python_impact["theory_status"] == "defined"
    assert python_impact["commit_armed"] is True
    assert python_impact["exact_visible_delta"] == [{"x": 0, "y": 0}]
    assert python_impact["per_cell"][0]["expected_after"]["material_owner"] == "terrain:5"

    binary = _build_cpp(tmp_path)
    snapshot_path = tmp_path / "snapshot.json"
    action_path = tmp_path / "action.json"
    snapshot_path.write_bytes(canonical_bytes(snapshot))
    action_path.write_bytes(canonical_bytes(action))
    cpp_impact = subprocess.run(
        [str(binary), str(snapshot_path), str(action_path)],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout

    # Byte-for-byte cross-evaluator parity on the live-candidate impact.
    assert json.loads(cpp_impact) == python_impact
    assert cpp_impact == canonical_bytes(python_impact)


def test_cpp_python_parity_names_mixed_material_boundary_theory_blocker(tmp_path):
    snapshot, action = _synthetic_live_candidate_case()
    snapshot["candidate_material_paint"]["cells"][0]["dispatch_surface"] = 2
    python_impact = evaluate(snapshot, action)
    assert python_impact["theory_status"] == "theory_mechanism_unspecified"
    assert python_impact["commit_armed"] is False
    assert (
        "mixed-material boundary dispatch is unspecified"
        in python_impact["theory_failure_reason"]
    )

    binary = _build_cpp(tmp_path)
    snapshot_path = tmp_path / "mixed_snapshot.json"
    action_path = tmp_path / "mixed_action.json"
    snapshot_path.write_bytes(canonical_bytes(snapshot))
    action_path.write_bytes(canonical_bytes(action))
    cpp_impact = subprocess.run(
        [str(binary), str(snapshot_path), str(action_path)],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    assert json.loads(cpp_impact) == python_impact
    assert cpp_impact == canonical_bytes(python_impact)


def test_cpp_python_parity_preserves_observed_edge_then_uses_target_tonal(tmp_path):
    snapshot, action = _synthetic_live_candidate_case()
    pool = snapshot["compiled_pool"]["production_compiled_pool"]
    full_pool = json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_text()
    )
    edge_row = copy.deepcopy(next(
        row for row in full_pool["rows"]
        if row.get("profile_namespace") == "terrain"
        and row.get("material_key") == 1
        and row.get("role") == "edge"
        and row.get("lane_name") == "edge[0]"
    ))
    edge_descriptor = [0, 0, 0, 0, 0.5, 0.5]
    edge_row["material_key"] = 4
    edge_row["row_sha256"] = "e" * 64
    for descriptor in edge_row["candidate_descriptors"]:
        descriptor["global6_norm"] = copy.deepcopy(edge_descriptor)
    pool["rows"].append(edge_row)
    edge_gid = production_role_selection(
        {
            "selected_role": 2,
            "pool_address": {"lane_id": 0},
            "descriptor": {"global6": edge_descriptor},
        },
        pool,
        "terrain",
        4,
        observed=True,
    )["gid"]
    for cell in snapshot["renderer"]["production_bridge_cells"]:
        cell["selected_role"] = 2
        cell["pool_address"]["route_key"] = 2
        cell["pool_address"]["lane_id"] = 0
        cell["shape6_global"] = copy.deepcopy(edge_descriptor)
        cell["winner_gid"] = edge_gid
        cell["presented_gid"] = edge_gid
        cell["screen"]["final_gid"] = edge_gid
    for cell in snapshot["candidate_material_paint"]["cells"]:
        cell["shape6_global"] = copy.deepcopy(edge_descriptor)

    python_impact = evaluate(snapshot, action)
    first = python_impact["per_cell"][0]
    assert first["before"]["selected_role"] == "edge"
    assert first["before"]["final_screen_gid"] == edge_gid
    assert first["expected_after"]["selected_role"] == "tonal_fill"

    binary = _build_cpp(tmp_path)
    snapshot_path = tmp_path / "edge_snapshot.json"
    action_path = tmp_path / "edge_action.json"
    snapshot_path.write_bytes(canonical_bytes(snapshot))
    action_path.write_bytes(canonical_bytes(action))
    cpp_impact = subprocess.run(
        [str(binary), str(snapshot_path), str(action_path)],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    assert json.loads(cpp_impact) == python_impact
    assert cpp_impact == canonical_bytes(python_impact)


def test_cpp_python_parity_fails_closed_on_unregistered_mesh_feature(tmp_path):
    snapshot, action = _synthetic_live_candidate_case()
    for cell in snapshot["renderer"]["production_bridge_cells"]:
        cell["selected_role"] = 1
        cell["fact_mask"] = 1 << 3

    python_impact = evaluate(snapshot, action)
    assert python_impact["theory_status"] == "theory_mechanism_unspecified"
    assert python_impact["commit_armed"] is False
    assert (
        python_impact["theory_failure_reason"]
        == "production mesh_feature descriptor prediction is not registered for terrain paint"
    )

    binary = _build_cpp(tmp_path)
    snapshot_path = tmp_path / "mesh_feature_snapshot.json"
    action_path = tmp_path / "mesh_feature_action.json"
    snapshot_path.write_bytes(canonical_bytes(snapshot))
    action_path.write_bytes(canonical_bytes(action))
    cpp_impact = subprocess.run(
        [str(binary), str(snapshot_path), str(action_path)],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    assert json.loads(cpp_impact) == python_impact
    assert cpp_impact == canonical_bytes(python_impact)

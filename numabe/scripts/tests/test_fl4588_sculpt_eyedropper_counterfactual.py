# =============================================================================
# test_fl4588_sculpt_eyedropper_counterfactual.py
# =============================================================================
# Focused FL-4588 tests for the material eyedropper counterfactual model and
# the preserved paint_terrain_sculpt theory fallthrough.
#
# WHAT THIS PROVES (structural / deterministic only — not headed proof)
# --------------------------------------------------------------------
# * sample_terrain_material_eyedropper dispatches through evaluate() with a
#   defined observation-only impact: exact tool-state write set, zero TERM++
#   visible delta, protected-set hash over every frozen cell, revision bump
#   matching CommitTerrainMaterialEyedropper.
# * The golden fixture validates against shipped schemas and is byte-stable
#   under the Python evaluator.
# * Stale base_state_revision and UI registry hash mismatches fail closed.
# * Active-tool sequence seal (Ctrl eyedropper / require_eyedropper_sequence)
#   seals the empty visible delta through evaluate_counterfactual — no flat
#   leaf-independent prediction path.
# * paint_terrain_sculpt still hits the generic theory_mechanism_unspecified
#   fallthrough (paper lacks a height-document F after StampCB). Seal through
#   the sculpt active-tool sequence returns PROOF_OPEN.
#
# Run:
#   python3 -m pytest scripts/tests/test_fl4588_sculpt_eyedropper_counterfactual.py -q
# =============================================================================

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
FL4588 = ROOT / "scripts/fl4588"
GOLDEN = ROOT / "assets/asciiid/counterfactual/terrain_material_eyedropper_v1.golden.json"
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"
PREDICTOR_PATH = ROOT / "scripts/fl4588/reachable_sequence_predictor.py"

sys.path.insert(0, str(FL4588))
from asciiid_counterfactual import (  # noqa: E402
    CounterfactualError,
    canonical_bytes,
    evaluate,
    value_hash,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_registry() -> Registry:
    paths = (
        ROOT / "schemas/asciiid.state_snapshot.v1.json",
        ROOT / "schemas/asciiid.action.v1.json",
        ROOT / "schemas/asciiid.action_impact.v1.json",
        ROOT / "schemas/asciiid.counterfactual_golden_case.v1.json",
    )
    resources = []
    for path in paths:
        schema = _load(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _load_predictor():
    spec = importlib.util.spec_from_file_location(
        "reachable_sequence_predictor", PREDICTOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen(edit_mode: int) -> dict:
    return {"editor_session": {"edit_mode": edit_mode}}


def test_eyedropper_golden_validates_and_is_python_deterministic():
    case = _load(GOLDEN)
    registry = _schema_registry()
    validator = Draft202012Validator(
        _load(ROOT / "schemas/asciiid.counterfactual_golden_case.v1.json"),
        registry=registry,
    )
    validator.validate(case)

    # Session owns selection; document.model must not carry active_material.
    assert "active_material" not in case["snapshot"]["document"]["model"]
    assert case["snapshot"]["editor_session"]["active_material"] == 7

    impact = evaluate(case["snapshot"], case["action"])
    assert impact["theory_status"] == "defined"
    assert impact["exact_visible_delta"] == []
    assert impact["ownership_envelope"] == []
    assert impact["exact_write_set"] == [
        "/editor/session/active_material",
        "/state_revision",
    ]
    assert impact["expected_revisions"]["state"] == (
        int(case["snapshot"]["state_revision"]) + 1
    )
    assert impact["expected_revisions"]["document"] == int(
        case["snapshot"]["document"]["revision"]
    )
    # Document revision and document model hash stay frozen under T_alpha.
    assert impact["expected_revisions"]["document"] == case["snapshot"]["document"][
        "revision"
    ]
    assert case["snapshot"]["document"]["sha256"] == value_hash(
        case["snapshot"]["document"]["model"]
    )
    assert impact["frame_schedule"] == []
    assert impact["per_cell"] == []
    assert impact["commit_armed"] is True
    assert impact["preview_token"]
    assert value_hash(impact) == case["expected_action_impact_sha256"]
    assert canonical_bytes(impact) == canonical_bytes(case["expected_action_impact"])


def test_eyedropper_rejects_document_model_active_material_owner():
    """Regression: document.model.active_material is a deleted dual owner."""
    case = _load(GOLDEN)
    snapshot = copy.deepcopy(case["snapshot"])
    snapshot["document"]["model"]["active_material"] = 7
    snapshot["document"]["sha256"] = value_hash(snapshot["document"]["model"])
    hash_input = copy.deepcopy(snapshot)
    hash_input.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = value_hash(hash_input)
    with pytest.raises(CounterfactualError, match="must not live in document.model"):
        evaluate(snapshot, case["action"])


def test_eyedropper_rejects_stale_revision_and_registry_mismatch():
    case = _load(GOLDEN)
    stale = copy.deepcopy(case["action"])
    stale["base_state_revision"] = int(case["snapshot"]["state_revision"]) + 9
    with pytest.raises(CounterfactualError, match="base state revision mismatch"):
        evaluate(case["snapshot"], stale)

    bad_graph = copy.deepcopy(case["action"])
    bad_graph["ui_registry_sha256"] = "a" * 64
    with pytest.raises(CounterfactualError, match="UI registry hash mismatch"):
        evaluate(case["snapshot"], bad_graph)


def test_eyedropper_unchanged_selection_keeps_state_revision():
    case = _load(GOLDEN)
    snapshot = copy.deepcopy(case["snapshot"])
    # Cell (0,0) samples material 4; freeze that as the prior session selection.
    snapshot["editor_session"]["active_material"] = 4
    assert "active_material" not in snapshot["document"]["model"]
    document_sha_before = snapshot["document"]["sha256"]
    hash_input = copy.deepcopy(snapshot)
    hash_input.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = value_hash(hash_input)
    action = copy.deepcopy(case["action"])
    action["base_state_revision"] = snapshot["state_revision"]
    impact = evaluate(snapshot, action)
    assert impact["theory_status"] == "defined"
    assert impact["expected_revisions"]["state"] == snapshot["state_revision"]
    assert impact["expected_revisions"]["document"] == snapshot["document"]["revision"]
    assert snapshot["document"]["sha256"] == document_sha_before
    assert impact["exact_visible_delta"] == []


def test_eyedropper_sequence_seals_empty_visible_delta():
    predictor = _load_predictor()
    registry = _load(REGISTRY_PATH)
    case = _load(GOLDEN)
    action = dict(case["action"])
    action["ui_registry_sha256"] = registry["registry_sha256"]
    snapshot = dict(case["snapshot"])
    snapshot["ui_registry_sha256"] = registry["registry_sha256"]
    # Re-hash after pinning the live registry so evaluate() accepts the seal.
    hash_input = copy.deepcopy(snapshot)
    hash_input.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = value_hash(hash_input)

    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=_frozen(1),  # material_paint
        require_eyedropper_sequence=True,
        counterfactual_snapshot=snapshot,
        action=action,
    )
    assert result["status"] == "SEALED"
    assert result["canvas_gesture_leaf_id"] == "scene.terrain.eyedropper.material"
    assert result["sealed_cells"] == []
    assert result["action_impact"]["theory_status"] == "defined"
    assert result["action_impact"]["exact_visible_delta"] == []
    assert result["predicted_before_physical_input"] is True
    assert result["mutates_backend"] is False


def test_sculpt_preserves_generic_theory_fallthrough_and_sequence_is_proof_open():
    """paint_terrain_sculpt has no complete paper F after StampCB — keep fallthrough."""
    case = _load(GOLDEN)
    snapshot = case["snapshot"]
    registry = _load(REGISTRY_PATH)
    action = {
        "schema": "asciiid.action.v1",
        "leaf_id": "scene.terrain.paint.sculpt",
        "control_uid": "asciiid_canvas_terrain_sculpt_brush",
        "action_kind": "paint_terrain_sculpt",
        "action_version": 1,
        "base_state_revision": snapshot["state_revision"],
        "ui_registry_sha256": snapshot["ui_registry_sha256"],
        "interaction_id": "sculpt-theory-fallthrough-1",
        "payload": {
            "brush_shape": "circle",
            "radius": 1.5,
            "alpha": 0.2,
            "height_limit_enabled": False,
            "height_threshold": 0.0,
            "samples": [{"x": 0.0, "y": 0.0}],
        },
    }
    impact = evaluate(snapshot, action)
    assert impact["theory_status"] == "theory_mechanism_unspecified"
    assert impact["theory_failure_reason"] == (
        "paper action model is not registered for this action_kind"
    )
    assert impact["commit_armed"] is False
    assert impact["preview_token"] is None

    predictor = _load_predictor()
    # Pin registry hashes so the sequence gate reaches the counterfactual step.
    action = dict(action)
    action["ui_registry_sha256"] = registry["registry_sha256"]
    snap = dict(snapshot)
    snap["ui_registry_sha256"] = registry["registry_sha256"]
    hash_input = copy.deepcopy(snap)
    hash_input.pop("snapshot_sha256")
    snap["snapshot_sha256"] = value_hash(hash_input)
    result = predictor.predict_reachable_sequence(
        registry=registry,
        frozen_backend=_frozen(0),  # sculpt
        requested_leaf_id="scene.terrain.paint.sculpt",
        counterfactual_snapshot=snap,
        action=action,
    )
    assert result["status"] == "PROOF_OPEN"
    assert result["sealed_cells"] is None
    assert any(
        r.startswith("proof_open:theory_mechanism_unspecified:")
        for r in result["reasons"]
    )


def test_no_flat_leaf_independent_prediction_owner():
    expected_cells = ROOT / "scripts/fl4548/fl4548_expected_cells.py"
    text = expected_cells.read_text(encoding="utf-8")
    assert "flat_leaf_independent_prediction_owner_deleted" in text
    spec = importlib.util.spec_from_file_location(
        "fl4548_expected_cells", expected_cells
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match="flat_leaf_independent_prediction_owner_deleted"):
        module.derive_expected_cells({"expected": {"affected_cells": {"kind": "none"}}})

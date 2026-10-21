"""FL-4359: required coverage cannot silently collapse to one route or grid."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("temporal_registry", ROOT / "scripts/validate_godot_proof_pose_registry.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DATA = json.loads((ROOT / "config/godot_proof_pose_registry.json").read_text())
IDS = {row["id"] for key in ("proof_scripts", "poses", "routes") for row in DATA[key]}


def test_current_requirement_is_structurally_valid_not_a_runtime_verdict():
    assert MODULE.validate_temporal_requirements(DATA, IDS) == []
    assert "not executed coverage" in DATA["temporal_regression_requirements"]["scope"]


@pytest.mark.parametrize("family", ["camera_turn_five_frame", "airplane", "strafe_movement"])
def test_no_route_family_can_be_removed(family):
    data = copy.deepcopy(DATA)
    del data["temporal_regression_requirements"]["route_families"][family]
    assert MODULE.validate_temporal_requirements(data, IDS)


@pytest.mark.parametrize("field", ["defect_classes", "presentation_modes", "sampling_axes", "required_witnesses", "sensitivity_controls", "acceptance_rules"])
def test_empty_requirement_fails(field):
    data = copy.deepcopy(DATA)
    data["temporal_regression_requirements"][field] = []
    assert MODULE.validate_temporal_requirements(data, IDS)


def test_unknown_proof_reference_fails():
    data = copy.deepcopy(DATA)
    data["temporal_regression_requirements"]["route_families"]["airplane"]["proof_refs"] = ["invented"]
    assert MODULE.validate_temporal_requirements(data, IDS)


def test_ff_alone_is_sufficient_but_smoothing_cannot_replace_it():
    data = copy.deepcopy(DATA)
    data["temporal_regression_requirements"]["presentation_modes"] = ["FF"]
    assert MODULE.validate_temporal_requirements(data, IDS) == []
    data["temporal_regression_requirements"]["presentation_modes"] = ["FC", "CF", "CC"]
    assert MODULE.validate_temporal_requirements(data, IDS)


def test_runtime_sampling_domain_records_supported_not_shimmer_safe_bounds():
    domain = DATA["temporal_regression_requirements"]["runtime_sampling_domain"]
    assert domain["matrix_cell_height_steps_px"] == [96, 64, 48, 32, 24, 20, 16, 12]
    assert domain["cell_aspects"]["terminal_1x2"].endswith("divided by two")
    assert domain["probe_face_resolution_px"] == [128, 256, 384]
    assert "not certified shimmer-safe" in domain["camera_zoom"]["restriction_boundary"]
    assert "fail or restart" in domain["capture_identity_rule"]


def test_runtime_sampling_domain_cannot_silently_drop_a_matrix_step():
    data = copy.deepcopy(DATA)
    data["temporal_regression_requirements"]["runtime_sampling_domain"][
        "matrix_cell_height_steps_px"
    ].remove(20)
    assert MODULE.validate_temporal_requirements(data, IDS)

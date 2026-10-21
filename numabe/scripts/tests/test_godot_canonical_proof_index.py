#!/usr/bin/env python3
"""FL-4781 canonical Godot proof index drift tests."""

from __future__ import annotations

import importlib.util
import json
import hashlib
import re
from pathlib import Path
import shutil
import subprocess
import pytest
from launcher_lib.godot_proofs import load_canonical_index
from run_godot_visual_owner_proof import SCENARIOS, FULL_GRID_ORACLE_DAY_CYCLE_TIME


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "godot_canonical_proof_index",
    ROOT / "scripts" / "build_godot_canonical_proof_index.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_generated_index_matches_registry() -> None:
    registry = json.loads((ROOT / "config" / "godot_proof_pose_registry.json").read_text())
    expected = MODULE._encoded_index(MODULE.build_index(registry))
    actual = (ROOT / "docs" / "proofs" / "index.json").read_text()
    assert actual == expected
    assert (ROOT / "docs/proofs/README.md").read_text() == MODULE.render_reader(MODULE.build_index(registry))


def test_index_preserves_single_owner_and_camera5_no_substitution() -> None:
    index = json.loads((ROOT / "docs" / "proofs" / "index.json").read_text())
    assert index["mutable_identity_owner"] == "config/godot_proof_pose_registry.json"
    assert index["reader"] == "docs/proofs/README.md"
    assert index["mode_policy"]["presentation_modes"] == ["FF"]
    assert index["mode_policy"]["source"].endswith(":temporal_regression_requirements")
    unresolved = {row["id"]: row for row in index["unresolved_retrieval"]}
    camera_gap = unresolved.get("camera_turn_five_frame_original_identity")
    if camera_gap is not None:
        assert camera_gap["status"] == "unresolved-retrieval-gap"
        assert ".run/fl4359_ground_turn_current_20260702-190000" in camera_gap["do_not_substitute"]
    else:
        camera_family = index["temporal_regression_requirements"]["route_families"]["camera_turn_five_frame"]
        route_refs = camera_family.get("route_refs", [])
        assert route_refs, "resolved camera-turn identity must bind at least one registry route"
        assert "route.r20_canonical_movement_grid" not in route_refs
        protocol = next(row["protocol"] for row in index["routes"] if row["id"] == route_refs[0])
        scenario = next(row for row in SCENARIOS if row.scenario_id == protocol["scenario_id"])
        args = dict(zip(scenario.runner_args[::2], scenario.runner_args[1::2]))
        assert protocol["move_frames"] == scenario.move_frames
        assert protocol["turn_frames"] == int(args["--turn-frames"]) == 8
        assert protocol["jitter_capture_frame_limit"] == int(args["--jitter-capture-frame-limit"]) == 5
        assert protocol["turn_action"] == args["--turn-action"]
        assert protocol["product_time"] == float(args["--product-time"])
        assert protocol["day_cycle_time"] == float(FULL_GRID_ORACLE_DAY_CYCLE_TIME)
        assert protocol["full_grid_contract_mode"] == "camera_turn"
        assert protocol["presentation_size"] == [3840, 2160]
        assert protocol["capture_start_frame"] == 0


def test_index_exposes_runtime_sampling_and_detector_contracts() -> None:
    index = json.loads((ROOT / "docs/proofs/index.json").read_text())
    temporal = index["temporal_regression_requirements"]
    domain = temporal["runtime_sampling_domain"]
    assert domain["matrix_cell_height_steps_px"] == [96, 64, 48, 32, 24, 20, 16, 12]
    assert domain["canonical_route_rasters"]["ground_turn_perspective"] == [3840, 2160]
    assert "need not divide" in domain["raster_admission"]
    assert temporal["required_witnesses"]
    assert temporal["sensitivity_controls"]
    reader = (ROOT / "docs/proofs/README.md").read_text()
    assert "## Runtime sampling domain" in reader
    assert "## Detectability and traceability" in reader


def test_all_registered_routes_are_indexed_with_status_and_distinction() -> None:
    registry = json.loads((ROOT / "config" / "godot_proof_pose_registry.json").read_text())
    index = json.loads((ROOT / "docs" / "proofs" / "index.json").read_text())
    registry_routes = {row["id"]: row for row in registry["routes"]}
    indexed_routes = {row["id"]: row for row in index["routes"]}
    assert set(indexed_routes) == set(registry_routes)
    assert indexed_routes["route.r24_operator_foliage_seed"]["status"] == "rejected-conflicted"
    assert "rejected" in indexed_routes["route.r24_operator_foliage_seed"]["distinction"]
    assert indexed_routes["route.r13_operator_site3_pieta_head_eyes"]["status"] == "missing-executable-pose"
    assert "missing executable" in indexed_routes["route.r13_operator_site3_pieta_head_eyes"]["distinction"]


def test_lookup_exposes_family_gaps_and_complete_registered_rows():
    registry = json.loads((ROOT / "config/godot_proof_pose_registry.json").read_text())
    index = MODULE.build_index(registry)
    for section in ("proof_scripts", "poses", "routes"):
        for row in registry[section]:
            found = next(item for item in MODULE.find_identities(index, row["id"]) if item["id"] == row["id"])
            assert all(found[key] == value for key, value in row.items())
    assert MODULE.find_identities(index, "camera_turn")
    assert MODULE.find_identities(index, "camera-turn") == MODULE.find_identities(index, "camera turn")
    assert MODULE.find_identities(index, "never_an_actual_proof_identity") == []


def test_recovered_airplane_recipe_keeps_receipt_identity_separate_from_acceptance():
    registry = json.loads((ROOT / "config/godot_proof_pose_registry.json").read_text())
    recipe = registry["temporal_regression_requirements"]["route_families"]["airplane"]["canonical_capture_recipe"]
    raw = (ROOT / recipe["identity_receipt"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recipe["identity_receipt_sha256"]
    receipt = json.loads(raw)
    assert recipe["camera_positions"] == [[row[axis] for axis in ("x", "y", "z")] for row in receipt["exact_pose_sweep_positions"]]
    assert len(recipe["camera_positions"]) == recipe["jitter_capture_frame_limit"] == 5
    assert recipe["camera_positions"][-1] == [450, 48, -340]
    assert recipe["identity_receipt_pass"] is receipt["pass"] is False
    assert recipe["turn_frames"] == 0


@pytest.mark.parametrize("condition", ["missing", "stale", "malformed"])
def test_launcher_refuses_unusable_generated_index(tmp_path, condition):
    registry_path = tmp_path / "config/godot_proof_pose_registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text('{"revision": 1}')
    path = tmp_path / "docs/proofs/index.json"
    path.parent.mkdir(parents=True)
    if condition == "stale":
        path.write_text(json.dumps({"generated_from": {"registry_sha256": "wrong"}}))
    elif condition == "malformed":
        path.write_text("[]")
    with pytest.raises((OSError, ValueError)):
        load_canonical_index(tmp_path)


def test_launcher_reads_current_index():
    assert load_canonical_index(ROOT)["mutable_identity_owner"] == "config/godot_proof_pose_registry.json"


def test_primary_discovery_entry_points_cannot_drop_index_route():
    for path in ("AGENTS.md", "CLAUDE.md", "docs/agent/agents.md",
                 "docs/agent/godot-proof-publication.md", "docs/agent/godot-proof-pose-registry.md",
                 "docs/plans/GODOT_GOAL.MD", "docs/plans/2026-03-22-multiplayer-canonical-spec.md",
                 "scripts/analyze_runs.py", "scripts/launcher_lib/godot_proofs.py"):
        text = (ROOT / path).read_text()
        assert "proofs/README.md" in text or '"proofs" / "README.md"' in text, path


def test_related_agent_docs_route_to_index_without_rewriting_archived_evidence():
    for path in (ROOT / "docs/agent").rglob("*.md"):
        if {"artifacts", "archive"}.intersection(path.parts) or path.name == "SOURCE_PROVENANCE.md":
            continue  # Preserved capture/provenance bytes are evidence, not mutable routers.
        text = path.read_text()
        if re.search(r"\bproofs?\b", text, re.I) and re.search(r"godot|FL-4359|FL-4431", text, re.I):
            assert "<!-- canonical-proof-discovery -->" in text, path
            target = re.search(r"\[shared proof index\]\(([^)]+)\)", text)
            assert target and (path.parent / target[1]).resolve() == (ROOT / "docs/proofs/README.md").resolve(), path


def test_godot_menu_summary_executes_all_registry_rows(tmp_path):
    """Execute the unchanged production method, isolated from game autoloads/UI."""
    godot = shutil.which("godot")
    if godot is None:
        pytest.skip("Godot executable unavailable")
    source = (ROOT / "godot_project/scripts/MainMenu.gd").read_text()
    method = "func _registry_summary_lines()" + source.split("func _registry_summary_lines()", 1)[1].split("\nfunc ", 1)[0]
    registry_path = str(ROOT / "config/godot_proof_pose_registry.json")
    harness = tmp_path / "menu_summary.gd"
    harness.write_text(
        'extends SceneTree\nconst _REGISTRY_PATH := ' + json.dumps(registry_path) + '\n'
        + method + '\n\nfunc _initialize() -> void:\n'
        + '\tvar lines := "\\n".join(_registry_summary_lines())\n'
        + '\tvar data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(_REGISTRY_PATH))\n'
        + '\tfor section in ["proof_scripts", "poses", "routes"]:\n'
        + '\t\tfor row in data[section]:\n'
        + '\t\t\tif not lines.contains(String(row["id"])):\n'
        + '\t\t\t\tprinterr("Missing registry identity: ", row["id"])\n'
        + '\t\t\t\tquit(1)\n\t\t\t\treturn\n'
        + '\tprint("GODOT_MENU_ALL_REGISTRY_ROWS_OK")\n\tquit(0)\n'
    )
    result = subprocess.run([godot, "--headless", "--path", str(tmp_path), "--script", str(harness)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GODOT_MENU_ALL_REGISTRY_ROWS_OK" in result.stdout

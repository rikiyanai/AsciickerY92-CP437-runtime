#!/usr/bin/env python3
"""Focused destructive contract for the FL-4739 water baseline owner."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "build_fl4737_water_baseline_receipt.py"
SPEC = importlib.util.spec_from_file_location("fl4739_water_receipt", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
receipt_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt_tool)


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _artifact_ref(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "sha256": receipt_tool._file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _pose(role: str) -> dict[str, object]:
    projection = receipt_tool.ROLE_PROJECTION[role]
    if role.startswith("p1_deep_reflection_"):
        lock = receipt_tool.LOCKED_ROLE_POSES["p1_deep_reflection"]
        player_position = list(lock["player_position"])
        camera_position = list(lock["camera_position"])
        camera_yaw = lock["camera_yaw"]
        camera_pitch = lock["camera_pitch"]
    elif role.startswith("shallow_contact_"):
        player_position = [80.0, 2.0, 70.0]
        camera_position = [74.0, 12.0, 82.0]
        camera_yaw = 0.25
        camera_pitch = -0.35
    else:
        lock = receipt_tool.LOCKED_ROLE_POSES["p2_night_suppression"]
        player_position = list(lock["player_position"])
        camera_position = list(lock["camera_position"])
        camera_yaw = lock["camera_yaw"]
        camera_pitch = lock["camera_pitch"]
    return {
        "player_position": player_position,
        "player_yaw": 0.0,
        "camera_position": camera_position,
        "camera_yaw": camera_yaw,
        "camera_pitch": camera_pitch,
        "camera_roll": 0.0,
        "projection": projection,
        "fov_degrees": 58.0 if projection == "perspective" else None,
        "orthographic_size": 34.0 if projection == "orthographic" else None,
        "day_cycle_value": 0.0 if role.startswith("p2_") else 0.5,
        "weather_kind": "clear",
        "weather_intensity": 0.0,
        "viewport_width": 1280,
        "viewport_height": 720,
    }


@pytest.fixture()
def receipt_fixture(tmp_path: Path) -> dict[str, object]:
    proof_root = tmp_path / "proof"
    tool_copy = proof_root / receipt_tool.PROOF_TOOL_PATH
    tool_copy.parent.mkdir(parents=True)
    shutil.copy2(MODULE_PATH, tool_copy)
    _git(proof_root, "init")
    _git(proof_root, "config", "user.email", "fixture@example.invalid")
    _git(proof_root, "config", "user.name", "FL4739 Fixture")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    godot_binary = evidence / "godot.bin"
    godot_binary.write_bytes(b"godot-fixture\n")
    project_manifest = evidence / "project-build-manifest.json"
    project_manifest.write_text('{"project":"fixture"}\n', encoding="utf-8")
    capture_tool = evidence / "capture-tool.py"
    capture_tool.write_text("# fixture capture tool\n", encoding="utf-8")
    transport_witness = evidence / "transport-witness.json"
    transport_witness.write_text('{"mounted":"read-only"}\n', encoding="utf-8")

    frames: list[dict[str, object]] = []
    for role in receipt_tool.FRAME_ROLES:
        artifacts: dict[str, object] = {}
        for artifact_role in receipt_tool.ARTIFACT_ROLES:
            artifact = evidence / role / f"{artifact_role}.artifact"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"{role}:{artifact_role}\n", encoding="utf-8")
            artifacts[artifact_role] = _artifact_ref(artifact)
        frames.append(
            {
                "role": role,
                "pose": _pose(role),
                "wrapper_identity": {
                    "commit": receipt_tool.SELECTED_SOURCE_COMMIT,
                    "tree": receipt_tool.SELECTED_SOURCE_TREE,
                    "dirty": False,
                },
                "runtime_identity": {
                    "commit": receipt_tool.SELECTED_SOURCE_COMMIT,
                    "tree": receipt_tool.SELECTED_SOURCE_TREE,
                    "dirty": False,
                },
                "run_identity": {
                    "capture_id": "fixture-water-baseline",
                    "process_id": f"fixture-{role}",
                },
                "artifacts": artifacts,
            }
        )

    manifest = {
        "schema": receipt_tool.CAPTURE_MANIFEST_SCHEMA,
        "capture_id": "fixture-water-baseline",
        "baseline_source": {
            "commit": receipt_tool.SELECTED_SOURCE_COMMIT,
            "tree": receipt_tool.SELECTED_SOURCE_TREE,
            "clean_required": True,
        },
        "transport_witness": {
            **_artifact_ref(transport_witness),
            "read_only": True,
        },
        "build_identity": {
            "godot_version": "4.6.2.stable.official",
            "godot_binary": _artifact_ref(godot_binary),
            "project_build_manifest": _artifact_ref(project_manifest),
            "capture_tool_commit": receipt_tool.SELECTED_SOURCE_COMMIT,
            "capture_tool_tree": receipt_tool.SELECTED_SOURCE_TREE,
            "capture_tool": _artifact_ref(capture_tool),
        },
        "launch_configuration": {
            "argument_vector": ["godot", "--path", "godot_project"],
            "environment": {"LANG": "C"},
            "renderer": "metal",
            "feature_flags": {"fl4216_reflection_ray": True},
        },
        "frame_bundles": frames,
    }
    manifest_path = evidence / "capture-manifest.json"
    _write_json(manifest_path, manifest)

    designation = {
        "schema": receipt_tool.DESIGNATION_SCHEMA,
        "baseline_name": receipt_tool.BASELINE_NAME,
        "receipt_schema": receipt_tool.SCHEMA,
        "source_commit": receipt_tool.SELECTED_SOURCE_COMMIT,
        "source_tree": receipt_tool.SELECTED_SOURCE_TREE,
        "operator_identity": "fixture-operator",
        "designated_at_utc": "2026-08-12T00:00:00Z",
        "statement": "Designate these six inspected water baseline frames.",
        "capture_manifest_sha256": receipt_tool._file_sha256(manifest_path),
        "frames": [
            {
                "role": frame["role"],
                "projection": frame["pose"]["projection"],
                "pose_sha256": receipt_tool._pose_digest(frame["pose"]),
            }
            for frame in frames
        ],
        "predecessor_receipt_sha256": None,
    }
    designation_path = (
        proof_root / receipt_tool.DESIGNATION_ROOT / "fixture-designation.json"
    )
    _write_json(designation_path, designation)
    _git(proof_root, "add", receipt_tool.PROOF_TOOL_PATH, receipt_tool.DESIGNATION_ROOT)
    _git(proof_root, "commit", "-m", "fixture proof owner")

    return {
        "proof_root": proof_root,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "designation": designation,
        "designation_path": designation_path,
        "output_root": proof_root / receipt_tool.RECEIPT_ROOT,
    }


def _create(fixture: dict[str, object]) -> tuple[Path, dict[str, object]]:
    return receipt_tool.create_receipt(
        output_root=fixture["output_root"],
        designation_path=fixture["designation_path"],
        manifest_path=fixture["manifest_path"],
        source_root=ROOT,
        proof_tool_root=fixture["proof_root"],
    )


def test_create_and_validate_content_addressed_receipt(receipt_fixture):
    path, receipt = _create(receipt_fixture)
    assert path == receipt_fixture["output_root"] / receipt["receipt_sha256"] / "receipt.json"
    assert path.read_bytes() == receipt_tool._canonical_json_bytes(receipt) + b"\n"
    validated = receipt_tool.validate_receipt(path, source_root=ROOT)
    assert validated == receipt
    assert [frame["role"] for frame in receipt["frame_bundles"]] == list(
        receipt_tool.FRAME_ROLES
    )


def test_exclusive_create_rejects_existing_digest(receipt_fixture):
    _create(receipt_fixture)
    with pytest.raises(receipt_tool.ReceiptValidationError, match="already exists"):
        _create(receipt_fixture)


def test_closed_schema_rejects_unknown_field(receipt_fixture):
    _, receipt = _create(receipt_fixture)
    receipt["unexpected"] = True
    with pytest.raises(receipt_tool.ReceiptValidationError, match="unknown"):
        receipt_tool.validate_receipt_object(receipt, source_root=ROOT)


def test_self_hash_tamper_is_rejected(receipt_fixture):
    _, receipt = _create(receipt_fixture)
    receipt["predecessor_receipt_sha256"] = "0" * 64
    with pytest.raises(receipt_tool.ReceiptValidationError, match="self-hash mismatch"):
        receipt_tool.validate_receipt_object(receipt, source_root=ROOT)


def test_artifact_tamper_is_rejected(receipt_fixture):
    path, _ = _create(receipt_fixture)
    manifest = receipt_fixture["manifest"]
    artifact_path = Path(
        manifest["frame_bundles"][0]["artifacts"]["viewport_png"]["path"]
    )
    artifact_path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(receipt_tool.ReceiptValidationError, match="mismatch"):
        receipt_tool.validate_receipt(path, source_root=ROOT)


def test_missing_frame_role_is_rejected(receipt_fixture):
    manifest = deepcopy(receipt_fixture["manifest"])
    manifest["frame_bundles"].pop()
    with pytest.raises(receipt_tool.ReceiptValidationError, match="exactly six"):
        receipt_tool._validate_manifest(manifest)


def test_dirty_runtime_identity_is_rejected(receipt_fixture):
    manifest = deepcopy(receipt_fixture["manifest"])
    manifest["frame_bundles"][2]["runtime_identity"]["dirty"] = True
    with pytest.raises(receipt_tool.ReceiptValidationError, match="dirty must be false"):
        receipt_tool._validate_manifest(manifest)


def test_designation_pose_digest_mismatch_is_rejected(receipt_fixture):
    designation = deepcopy(receipt_fixture["designation"])
    designation["frames"][2]["pose_sha256"] = "0" * 64
    with pytest.raises(receipt_tool.ReceiptValidationError, match="pose digest mismatch"):
        receipt_tool._validate_designation(
            designation,
            receipt_fixture["manifest"],
            designation["capture_manifest_sha256"],
        )


def test_banned_candidate_label_is_rejected(receipt_fixture):
    manifest = deepcopy(receipt_fixture["manifest"])
    manifest["capture_id"] = "fl4737_baseline_candidate_perspective"
    for frame in manifest["frame_bundles"]:
        frame["run_identity"]["capture_id"] = manifest["capture_id"]
    with pytest.raises(receipt_tool.ReceiptValidationError, match="banned authority token"):
        receipt_tool._validate_manifest(manifest)


def test_permanent_p2_pose_cannot_be_rounded_or_substituted(receipt_fixture):
    manifest = deepcopy(receipt_fixture["manifest"])
    manifest["frame_bundles"][4]["pose"]["camera_position"][0] = 267.39
    manifest["frame_bundles"][5]["pose"]["camera_position"][0] = 267.39
    with pytest.raises(receipt_tool.ReceiptValidationError, match="permanent p2"):
        receipt_tool._validate_manifest(manifest)


def test_capture_manifest_cannot_point_toward_future_receipt(receipt_fixture):
    manifest = deepcopy(receipt_fixture["manifest"])
    manifest["launch_configuration"]["environment"]["FUTURE_OWNER"] = (
        receipt_tool.RECEIPT_ROOT
    )
    with pytest.raises(receipt_tool.ReceiptValidationError, match="future baseline receipt"):
        receipt_tool._validate_manifest(manifest)


def test_historical_artifact_path_is_rejected(receipt_fixture, tmp_path):
    manifest = deepcopy(receipt_fixture["manifest"])
    historical = tmp_path / "historical-screenshot.png"
    historical.write_bytes(b"old screenshot\n")
    manifest["frame_bundles"][0]["artifacts"]["viewport_png"] = _artifact_ref(
        historical
    )
    with pytest.raises(receipt_tool.ReceiptValidationError, match="historical"):
        receipt_tool._validate_manifest(manifest)


def test_operator_prose_is_not_an_authority_token_scan(receipt_fixture):
    designation = deepcopy(receipt_fixture["designation"])
    designation["statement"] = "These inspected frames are not historical evidence."
    validated = receipt_tool._validate_designation(
        designation,
        receipt_fixture["manifest"],
        designation["capture_manifest_sha256"],
    )
    assert validated["statement"] == designation["statement"]


def test_uppercase_digest_is_rejected():
    with pytest.raises(receipt_tool.ReceiptValidationError, match="lowercase hex"):
        receipt_tool._require_hex("A" * 64, "digest", 64)


def test_duplicate_json_key_is_rejected(tmp_path):
    document = tmp_path / "duplicate.json"
    document.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
    with pytest.raises(receipt_tool.ReceiptValidationError, match="duplicate JSON key"):
        receipt_tool._load_json(document, "duplicate fixture")


def test_missing_predecessor_receipt_is_rejected(receipt_fixture):
    designation = deepcopy(receipt_fixture["designation"])
    designation["predecessor_receipt_sha256"] = "0" * 64
    _write_json(receipt_fixture["designation_path"], designation)
    _git(receipt_fixture["proof_root"], "add", receipt_tool.DESIGNATION_ROOT)
    _git(receipt_fixture["proof_root"], "commit", "-m", "name missing predecessor")
    with pytest.raises(receipt_tool.ReceiptValidationError, match="predecessor is not present"):
        _create(receipt_fixture)


def test_successor_designation_retains_valid_predecessor(receipt_fixture):
    _, first = _create(receipt_fixture)
    designation = deepcopy(receipt_fixture["designation"])
    designation["predecessor_receipt_sha256"] = first["receipt_sha256"]
    designation["designated_at_utc"] = "2026-08-12T01:00:00Z"
    designation["statement"] = "Designate the inspected successor package."
    _write_json(receipt_fixture["designation_path"], designation)
    _git(receipt_fixture["proof_root"], "add", receipt_tool.DESIGNATION_ROOT)
    _git(receipt_fixture["proof_root"], "commit", "-m", "designate successor")

    successor_path, successor = _create(receipt_fixture)
    assert successor["predecessor_receipt_sha256"] == first["receipt_sha256"]
    assert receipt_tool.validate_receipt(successor_path, source_root=ROOT) == successor


def test_dirty_proof_tool_is_rejected(receipt_fixture):
    tool_path = receipt_fixture["proof_root"] / receipt_tool.PROOF_TOOL_PATH
    tool_path.write_text(tool_path.read_text(encoding="utf-8") + "# dirty\n", encoding="utf-8")
    with pytest.raises(receipt_tool.ReceiptValidationError, match="dirty or untracked"):
        _create(receipt_fixture)


def test_cli_create_and_validate_commands(receipt_fixture, capsys):
    create_status = receipt_tool.main(
        [
            "create",
            "--output-root",
            str(receipt_fixture["output_root"]),
            "--designation",
            str(receipt_fixture["designation_path"]),
            "--capture-manifest",
            str(receipt_fixture["manifest_path"]),
            "--source-root",
            str(ROOT),
            "--proof-tool-root",
            str(receipt_fixture["proof_root"]),
        ]
    )
    assert create_status == 0
    created_line = capsys.readouterr().out.strip()
    assert created_line.startswith("FL4739_WATER_BASELINE_RECEIPT_CREATED ")
    receipt_path = next(receipt_fixture["output_root"].glob("*/receipt.json"))
    validate_status = receipt_tool.main(
        ["validate", str(receipt_path), "--source-root", str(ROOT)]
    )
    assert validate_status == 0
    assert capsys.readouterr().out.startswith("FL4739_WATER_BASELINE_RECEIPT_VALID ")


def test_later_proof_repo_commit_does_not_invalidate_recorded_tool(receipt_fixture):
    path, _ = _create(receipt_fixture)
    tool_path = receipt_fixture["proof_root"] / receipt_tool.PROOF_TOOL_PATH
    tool_path.write_text(tool_path.read_text(encoding="utf-8") + "# dirty\n", encoding="utf-8")
    _git(receipt_fixture["proof_root"], "add", receipt_tool.PROOF_TOOL_PATH)
    _git(receipt_fixture["proof_root"], "commit", "-m", "advance proof repository")
    validated = receipt_tool.validate_receipt(path, source_root=ROOT)
    assert validated["proof_tool"]["commit"] != _git(
        receipt_fixture["proof_root"], "rev-parse", "HEAD"
    )

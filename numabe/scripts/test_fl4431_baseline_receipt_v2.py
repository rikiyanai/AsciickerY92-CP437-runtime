#!/usr/bin/env python3
"""Destructively test the FL-4431 Baseline A v2 receipt boundary.

The tests build a complete temporary evidence package, create one successor
receipt, then damage one authority at a time.  A mutation is successful only
when the validator rejects it.  Git answers are supplied by a deterministic
read-only oracle so the suite never creates a repository, changes the shared
index, makes a commit, and creates a worktree.

Run with either command:

    python3 -m pytest scripts/test_fl4431_baseline_receipt_v2.py -q
    python3 scripts/test_fl4431_baseline_receipt_v2.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_fl4431_baseline_receipt_v2.py"
SPEC = importlib.util.spec_from_file_location("fl4431_baseline_receipt_v2", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

BASELINE_COMMIT = "a" * 40
BASELINE_TREE = "b" * 40
PROOF_COMMIT = "c" * 40
PROOF_TREE = "d" * 40
GIT_BLOB = "e" * 40
SERVER_COMMIT = "f" * 40
SERVER_FINGERPRINT = "1" * 64
CREATED_AT = "2026-07-26T12:00:00+00:00"
DESIGNATED_AT = "2026-07-26T11:59:00+00:00"


def sha256(path: Path) -> str:
    """Return the digest used to construct an independently valid fixture."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_ref(path: Path) -> dict[str, Any]:
    """Build the same closed attachment shape the production tool validates."""

    return {
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write stable fixture JSON so byte hashes stay deterministic."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@dataclass
class GitState:
    """Mutable read-only Git oracle state used by dirty-check mutations."""

    baseline_root: Path
    proof_root: Path
    baseline_dirty: bool = False
    proof_dirty: bool = False

    def answer(self, root: Path, *args: str) -> str:
        """Return the exact answer the validator expects from one Git query."""

        resolved = root.resolve()
        if args[0] == "status":
            if resolved == self.baseline_root and self.baseline_dirty:
                return " M godot_project/project.godot"
            if resolved == self.proof_root and self.proof_dirty:
                return " M scripts/run_fl4359_acceptance_package.py"
            return ""
        if args == ("rev-parse", "HEAD"):
            return BASELINE_COMMIT if resolved == self.baseline_root else PROOF_COMMIT
        if args == ("rev-parse", "HEAD^{tree}"):
            return BASELINE_TREE if resolved == self.baseline_root else PROOF_TREE
        if args == ("rev-parse", "--show-object-format"):
            return "sha1"
        if len(args) == 2 and args[0] == "rev-parse" and ":" in args[1]:
            return GIT_BLOB
        raise AssertionError(f"unexpected Git query: root={root}, args={args}")


@dataclass
class Fixture:
    """All paths plus mutable Git state for one complete successor package."""

    root: Path
    baseline_root: Path
    proof_root: Path
    server_root: Path
    output: Path
    predecessor: Path
    designation: Path
    capture: Path
    manifest: Path
    tool_paths: dict[str, Path]
    png_paths: list[Path]
    metadata_paths: list[Path]
    truth_paths: list[Path]
    git: GitState

    def create(self) -> dict[str, Any]:
        """Invoke the real builder with every authority present and consistent."""

        return module.create_receipt(
            output_path=self.output,
            designation_record_path=self.designation,
            capture_receipt_path=self.capture,
            lane_manifest_path=self.manifest,
            source_root=self.baseline_root,
            proof_tool_root=self.proof_root,
            created_at_utc=CREATED_AT,
        )


@pytest.fixture
def complete_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Create a complete on-disk capture package without mutating any Git repo."""

    baseline_root = (tmp_path / "baseline").resolve()
    proof_root = (tmp_path / "proof").resolve()
    server_root = (tmp_path / "server").resolve()
    (baseline_root / "godot_project").mkdir(parents=True)
    proof_root.mkdir(parents=True)
    server_root.mkdir(parents=True)

    project_file = baseline_root / "godot_project" / "project.godot"
    project_file.write_text("[application]\nconfig/name=\"Baseline\"\n", encoding="utf-8")
    import_marker = baseline_root / "godot_project" / ".godot" / "editor" / "project_metadata.cfg"
    import_marker.parent.mkdir(parents=True)
    import_marker.write_text("[editor_metadata]\nexecutable_path=\"Godot\"\n", encoding="utf-8")

    # The requested and resolved Godot paths are regular files in this fixture.
    # Production captures record the real requested path plus resolved binary.
    godot_binary = tmp_path / "Godot"
    godot_binary.write_bytes(b"fixture-godot-binary")

    tool_paths: dict[str, Path] = {}
    for role, relative in {
        **module.REQUIRED_TOOL_PATHS,
        **module.REQUIRED_ANALYZER_PATHS,
    }.items():
        tool = proof_root / relative
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text(f"# {role} fixture\n", encoding="utf-8")
        tool_paths[role] = tool

    manifest = proof_root / "proof" / "generated_night_lane.manifest.json"
    write_json(
        manifest,
        {
            "schema": "asciicker.godot_canonical_lanes.v1",
            "lanes": [{"id": "night_lighthouse_beam"}],
        },
    )

    predecessor = tmp_path / "baseline_v1.json"
    write_json(
        predecessor,
        {
            "schema": "asciicker.godot_baseline.v1",
            "source_root": str(baseline_root),
            "source_commit": BASELINE_COMMIT,
        },
    )
    output = tmp_path / "baseline_v2.json"
    designation = tmp_path / "designation.json"
    write_json(
        designation,
        {
            "schema": module.DESIGNATION_SCHEMA,
            "baseline_name": module.BASELINE_NAME,
            "successor_schema": module.SCHEMA,
            "successor_receipt_path": str(output),
            "operator_identity": "operator-current-thread",
            "designated_at_utc": DESIGNATED_AT,
            "statement": "Designate this complete successor as Baseline A for FL-4431.",
            "predecessor_receipt_path": str(predecessor),
            "predecessor_receipt_sha256": sha256(predecessor),
        },
    )

    labels = ["beam_t0", "beam_t1", "beam_t2"]
    ticks = [30, 90, 150]
    poses: list[dict[str, Any]] = []
    source_frames: list[dict[str, Any]] = []
    png_paths: list[Path] = []
    metadata_paths: list[Path] = []
    truth_paths: list[Path] = []
    for index, (label, tick) in enumerate(zip(labels, ticks)):
        poses.append(
            {
                "label": label,
                "route_tick": tick,
                "player_position": [79.1 + index, 0.1, 25.3],
                "player_yaw": 0.1 * index,
                "camera_position": [183.1 + index, 60.0, 25.3],
                "camera_yaw": 1.57,
                "camera_pitch": -0.52,
                "camera_roll": 0.0,
                "airplane_position": [260.0 + index, 96.0 - index, 120.0 - index],
                "airplane_yaw": 0.2 * index,
                "projection": "perspective",
                "fov_degrees": 60.0,
                "near": 0.05,
                "far": 750.0,
            }
        )
        png = tmp_path / "capture" / f"{label}.png"
        metadata = tmp_path / "capture" / f"{label}.metadata.json"
        truth = tmp_path / "capture" / f"{label}.runtime_truth.json"
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG\r\nfixture-" + label.encode("utf-8"))
        write_json(
            metadata,
            {
                "capture_id": "capture-20260726-a",
                "temporal_capture_id": "temporal-20260726-a",
                "process_identity": "godot-pid-4242",
                "label": label,
                "route_tick": tick,
                "width": 3840,
                "height": 2160,
            },
        )
        write_json(
            truth,
            {
                "label": label,
                "route_tick": tick,
                "authority_epoch": 7,
                "capture_id": "capture-20260726-a",
                "temporal_capture_id": "temporal-20260726-a",
                "process_identity": "godot-pid-4242",
            },
        )
        png_paths.append(png)
        metadata_paths.append(metadata)
        truth_paths.append(truth)
        source_frames.append(
            {
                "lane_id": "night_lighthouse_beam",
                "label": label,
                "arm": "baseline",
                "route_tick": tick,
                "capture_id": "capture-20260726-a",
                "temporal_capture_id": "temporal-20260726-a",
                "process_identity": "godot-pid-4242",
                "png": path_ref(png),
                "metadata": path_ref(metadata),
                "runtime_truth": path_ref(truth),
            }
        )

    capture = tmp_path / "capture_receipt.json"
    write_json(
        capture,
        {
            "schema": module.CAPTURE_SCHEMA,
            "capture_classification": "non_promotable_diagnostic",
            "capture_id": "capture-20260726-a",
            "temporal_capture_id": "temporal-20260726-a",
            "process_identity": "godot-pid-4242",
            "created_at_utc": "2026-07-26T11:30:00+00:00",
            "baseline_source_commit": BASELINE_COMMIT,
            "baseline_source_tree": BASELINE_TREE,
            "proof_tool_commit": PROOF_COMMIT,
            "lane_manifest_path": str(manifest),
            "lane_manifest_sha256": sha256(manifest),
            "argument_vectors": {
                "day": [
                    "--camera-mode",
                    "plane_air",
                    "--fl4408-day-cycle-time=0.5",
                ],
                "night": [
                    "--camera-mode",
                    "plane_air",
                    "--fl4408-day-cycle-time=0.9375",
                ],
            },
            "route": {
                "lane_id": "night_lighthouse_beam",
                "identity": "server_tick_constant_physical_flight_input",
                "physical_input_action": "move_right",
                "tick_zero_kind": "server_first_changed_admitted_flight_intent",
                "warmup_tick": 30,
                "target_ticks": ticks,
                "tick_stride": 60,
                "frame_labels": labels,
                "poses": poses,
            },
            "dimensions": {
                "requested_width": 3840,
                "requested_height": 2160,
                "actual_width": 3840,
                "actual_height": 2160,
                "ansi_width": 192,
                "ansi_height": 108,
                "fullscreen": True,
                "ascii": True,
            },
            "time_profile": {
                "day_cycle_value": 0.5,
                "night_cycle_value": 0.9375,
                "day_world_hour": 12.0,
                "night_world_hour": 22.5,
            },
            "server_identity": {
                "server_commit": SERVER_COMMIT,
                "server_source_root": str(server_root),
                "server_runtime_scope_fingerprint": SERVER_FINGERPRINT,
                "server_endpoint": "ws://127.0.0.1:2581",
                "server_process_identity": "colyseus-pid-31337",
            },
            "baseline_build_identity": {
                "execution_kind": "source_project_via_installed_godot_binary",
                "godot_requested_path": str(godot_binary),
                "godot_resolved_binary": path_ref(godot_binary),
                "godot_version": "4.6.2.stable.official.71f334935",
                "godot_binary_format": "fixture executable",
                "imported_source_commit": BASELINE_COMMIT,
                "imported_source_tree": BASELINE_TREE,
                "project_import_marker": path_ref(import_marker),
            },
            "features": [
                "perspective mode",
                "server-premounted airborne airplane",
                "terrain",
                "water",
                "trees",
                "contrail",
                "airplane damage smoke",
                "lighthouse beam",
                "bonfire",
                "tower lights",
            ],
            "source_frames": source_frames,
        },
    )

    git_state = GitState(
        baseline_root=baseline_root,
        proof_root=proof_root,
    )
    monkeypatch.setattr(module, "_run_git", git_state.answer)
    return Fixture(
        root=tmp_path,
        baseline_root=baseline_root,
        proof_root=proof_root,
        server_root=server_root,
        output=output,
        predecessor=predecessor,
        designation=designation,
        capture=capture,
        manifest=manifest,
        tool_paths=tool_paths,
        png_paths=png_paths,
        metadata_paths=metadata_paths,
        truth_paths=truth_paths,
        git=git_state,
    )


def read_receipt(path: Path) -> dict[str, Any]:
    """Load the generated receipt for one controlled destructive mutation."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_resigned_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Recompute the self-hash so a deeper validator branch sees the mutation."""

    receipt["self_hash"]["digest"] = module._receipt_digest(receipt)
    write_json(path, receipt)


def test_create_and_validate_complete_successor_without_mutating_v1(
    complete_fixture: Fixture,
) -> None:
    """The builder creates v2 once, keeps v1 byte-identical, and validates it."""

    predecessor_before = complete_fixture.predecessor.read_bytes()
    created = complete_fixture.create()
    validated = module.validate_receipt(complete_fixture.output)
    assert created["schema"] == module.SCHEMA
    assert validated["self_hash"]["digest"] == created["self_hash"]["digest"]
    assert complete_fixture.predecessor.read_bytes() == predecessor_before
    assert module.main(["validate", str(complete_fixture.output)]) == 0


def test_wrong_baseline_commit_is_rejected_after_valid_self_hash(
    complete_fixture: Fixture,
) -> None:
    """A self-consistent receipt cannot name a commit different from live HEAD."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    receipt["baseline_source"]["commit"] = "9" * 40
    write_resigned_receipt(complete_fixture.output, receipt)
    with pytest.raises(module.ReceiptValidationError, match="commit mismatches live HEAD"):
        module.validate_receipt(complete_fixture.output)


def test_wrong_proof_tool_commit_is_rejected_after_valid_self_hash(
    complete_fixture: Fixture,
) -> None:
    """A receipt cannot relabel current tool bytes as a different Git generation."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    receipt["proof_tool_source"]["commit"] = "8" * 40
    write_resigned_receipt(complete_fixture.output, receipt)
    with pytest.raises(
        module.ReceiptValidationError,
        match="proof_tool_source.commit mismatches live HEAD",
    ):
        module.validate_receipt(complete_fixture.output)


def test_dirty_baseline_clone_is_rejected(complete_fixture: Fixture) -> None:
    """A matching commit is insufficient when tracked or untracked bytes drift."""

    complete_fixture.create()
    complete_fixture.git.baseline_dirty = True
    with pytest.raises(module.ReceiptValidationError, match="checkout is dirty"):
        module.validate_receipt(complete_fixture.output)


def test_dirty_proof_tool_scope_is_rejected_before_creation(
    complete_fixture: Fixture,
) -> None:
    """Proof-tool files must come from their named commit, even in a dirty repo."""

    complete_fixture.git.proof_dirty = True
    with pytest.raises(module.ReceiptValidationError, match="scoped paths must be clean"):
        complete_fixture.create()


def test_changed_lane_manifest_bytes_are_rejected(complete_fixture: Fixture) -> None:
    """Changing the executable pose manifest invalidates the designated receipt."""

    complete_fixture.create()
    original = complete_fixture.manifest.read_bytes()
    complete_fixture.manifest.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(module.ReceiptValidationError, match="lane_manifest SHA-256 mismatch"):
        module.validate_receipt(complete_fixture.output)


@pytest.mark.parametrize(
    "role",
    [
        "acceptance_runner",
        "baseline_receipt_builder",
        "detached_launcher",
    ],
)
def test_changed_proof_tool_bytes_are_rejected(
    complete_fixture: Fixture,
    role: str,
) -> None:
    """Each receipt-defining front door is immutable after designation."""

    complete_fixture.create()
    complete_fixture.tool_paths[role].write_text(
        f"# changed {role}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        module.ReceiptValidationError,
        match=rf"proof_tools.{role} SHA-256 mismatch",
    ):
        module.validate_receipt(complete_fixture.output)


def test_changed_png_bytes_are_rejected(complete_fixture: Fixture) -> None:
    """Visible Baseline A pixels are immutable receipt inputs, not loose files."""

    complete_fixture.create()
    original = complete_fixture.png_paths[1].read_bytes()
    complete_fixture.png_paths[1].write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(module.ReceiptValidationError, match="png SHA-256 mismatch"):
        module.validate_receipt(complete_fixture.output)


def test_changed_metadata_bytes_are_rejected(complete_fixture: Fixture) -> None:
    """A frame with changed metadata cannot retain its previous visual identity."""

    complete_fixture.create()
    original = complete_fixture.metadata_paths[1].read_bytes()
    complete_fixture.metadata_paths[1].write_bytes(
        bytes([original[0] ^ 1]) + original[1:]
    )
    with pytest.raises(module.ReceiptValidationError, match="metadata SHA-256 mismatch"):
        module.validate_receipt(complete_fixture.output)


def test_changed_runtime_truth_bytes_are_rejected(complete_fixture: Fixture) -> None:
    """Same-process runtime truth is as immutable as the PNG it explains."""

    complete_fixture.create()
    original = complete_fixture.truth_paths[1].read_bytes()
    complete_fixture.truth_paths[1].write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(module.ReceiptValidationError, match="runtime_truth SHA-256 mismatch"):
        module.validate_receipt(complete_fixture.output)


def test_changed_recorded_frame_hash_is_rejected(complete_fixture: Fixture) -> None:
    """Re-signing JSON cannot make a fabricated frame digest match real bytes."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    receipt["source_frames"][0]["png"]["sha256"] = "2" * 64
    write_resigned_receipt(complete_fixture.output, receipt)
    with pytest.raises(module.ReceiptValidationError):
        module.validate_receipt(complete_fixture.output)


def test_capture_receipt_drift_is_rejected(complete_fixture: Fixture) -> None:
    """The copied facts remain bound to the exact non-promotable input bytes."""

    complete_fixture.create()
    original = complete_fixture.capture.read_bytes()
    complete_fixture.capture.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(module.ReceiptValidationError, match="capture_input SHA-256 mismatch"):
        module.validate_receipt(complete_fixture.output)


def test_missing_designation_is_rejected(complete_fixture: Fixture) -> None:
    """No structural capture package can promote itself without the operator act."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    del receipt["designation"]
    write_resigned_receipt(complete_fixture.output, receipt)
    with pytest.raises(module.ReceiptValidationError, match="missing fields: designation"):
        module.validate_receipt(complete_fixture.output)


def test_mutated_self_hash_is_rejected(complete_fixture: Fixture) -> None:
    """A changed self-hash cannot validate even when every attachment is intact."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    receipt["self_hash"]["digest"] = "3" * 64
    write_json(complete_fixture.output, receipt)
    with pytest.raises(module.ReceiptValidationError, match="self_hash.digest mismatch"):
        module.validate_receipt(complete_fixture.output)


def test_unknown_top_level_field_is_rejected(complete_fixture: Fixture) -> None:
    """The closed schema rejects plausible-looking fields no reader owns."""

    complete_fixture.create()
    receipt = read_receipt(complete_fixture.output)
    receipt["legacy_baseline_hint"] = "do not accept"
    write_resigned_receipt(complete_fixture.output, receipt)
    with pytest.raises(module.ReceiptValidationError, match="unknown fields"):
        module.validate_receipt(complete_fixture.output)


def test_create_refuses_to_overwrite_existing_receipt(complete_fixture: Fixture) -> None:
    """The builder cannot silently replace an already designated pathname."""

    complete_fixture.create()
    with pytest.raises(module.ReceiptValidationError, match="refusing to overwrite"):
        complete_fixture.create()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

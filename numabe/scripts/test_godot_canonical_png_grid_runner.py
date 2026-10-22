#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import importlib.util
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
RUNNER = ROOT / "scripts" / "build_godot_canonical_png_grid.py"
ADAPTER = ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py"
BASELINE_V2_BUILDER = ROOT / "scripts" / "build_fl4431_baseline_receipt_v2.py"
ORIGINAL_BUG_MANIFEST = ROOT / "docs" / "research" / "ascii" / "verification" / "fl4408" / "original-bug-lanes.manifest.json"
MOVEMENT_SEQUENCE_LANES = {
    "ortho_movement_jitter",
    "perspective_movement_jitter",
    "water_wake_path_alignment",
    "airplane_air_movement_perspective",
    "airplane_air_movement_ortho",
}
STATIC_TEMPORAL_SEQUENCE_LANES = {"night_lighthouse_beam"}

_RUNNER_SPEC = importlib.util.spec_from_file_location("build_godot_canonical_png_grid", RUNNER)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
GRID_RUNNER = importlib.util.module_from_spec(_RUNNER_SPEC)
sys.modules[_RUNNER_SPEC.name] = GRID_RUNNER
_RUNNER_SPEC.loader.exec_module(GRID_RUNNER)
from godot_proof_publication import load_manifest, validate_manifest
FL4431_RUNTIME_SOURCE_FILES = list(GRID_RUNNER.FL4431_RUNTIME_SOURCE_FILES)

_ADAPTER_SPEC = importlib.util.spec_from_file_location("fl4408_actual_godot_capture_adapter", ADAPTER)
assert _ADAPTER_SPEC is not None and _ADAPTER_SPEC.loader is not None
GRID_ADAPTER = importlib.util.module_from_spec(_ADAPTER_SPEC)
sys.modules[_ADAPTER_SPEC.name] = GRID_ADAPTER
_ADAPTER_SPEC.loader.exec_module(GRID_ADAPTER)

_BASELINE_V2_SPEC = importlib.util.spec_from_file_location(
    "fl4431_baseline_receipt_v2_grid_fixture",
    BASELINE_V2_BUILDER,
)
assert _BASELINE_V2_SPEC is not None and _BASELINE_V2_SPEC.loader is not None
BASELINE_V2 = importlib.util.module_from_spec(_BASELINE_V2_SPEC)
sys.modules[_BASELINE_V2_SPEC.name] = BASELINE_V2
_BASELINE_V2_SPEC.loader.exec_module(BASELINE_V2)


def _git_text(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _runtime_commit_and_file() -> tuple[str, str]:
    commit = _git_text("log", "-1", "--format=%H", "--", "godot_project/scripts", "godot_project/shaders")
    files = [
        line
        for line in _git_text("diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if line.startswith(("godot_project/scripts/", "godot_project/render/", "godot_project/visual/"))
    ]
    assert files, "test requires at least one recent runtime commit"
    return commit, files[0]


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Baseline A v2 admits only the real fullscreen capture class. Tiny legacy
    # placeholders could previously pass the v1 fixture while proving nothing
    # about the production grid's resolution boundary.
    Image.new("RGB", (1600, 900), color).save(path)


def _write_scene_sentinel_png(path: Path, seed: int) -> None:
    """Give adapter fixtures enough real variation to exercise its scene gate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (32, 24))
    for y in range(image.height):
        for x in range(image.width):
            image.putpixel(
                (x, y),
                (
                    (seed + x * 13 + y * 5) % 256,
                    (seed + x * 3 + y * 17) % 256,
                    (seed + x * 19 + y * 7) % 256,
                ),
            )
    image.save(path)


def _meta(*, beam: bool = False) -> dict:
    data = {
        "ascii_enabled": True,
        "fullscreen": True,
        "view_mode": "perspective",
        "camera_position": [10.0, 20.0, 30.0],
        "camera_yaw": 15.0,
        "camera_pitch": -10.0,
        "camera_roll": 0.0,
        "camera_fov": 60.0,
        "projection_params": {"near": 0.05, "far": 400.0},
        "player_position": [1.0, 2.0, 3.0],
        "player_yaw": 90.0,
        "npc_positions": [[5.0, 0.0, 5.0]],
        "airplane_position": [20.0, 40.0, 60.0],
        "airplane_yaw": 180.0,
        "world_time": 22.0,
        "night_state": True,
        "feature_flags": ["ascii", "foliage_cards", "water_current_package"],
    }
    return data


def _server_identity(
    root: str = "/fixture/current/colyseus_server",
) -> dict:
    """Return one complete shared-server identity for metadata unit tests."""
    return {
        "server_commit": "a" * 40,
        "server_requested_root": root,
        "server_source_root": root,
        "server_dirty_paths": [],
        "server_runtime_scope_fingerprint": "b" * 64,
        "proof_tool_sha256": "c" * 64,
    }


def _attach_server_identity(
    frame: GRID_RUNNER.FrameEvidence,
    side: str,
    *,
    root: str = "/fixture/current/colyseus_server",
) -> None:
    """Attach the duplicated nested/top-level receipt shape emitted by adapter."""
    identity = _server_identity(root)
    frame.metadata["renderer_arm_label"] = GRID_RUNNER.RENDERER_ARM_LABELS[side]
    frame.metadata["shared_current_server_identity"] = dict(identity)
    frame.metadata.update(identity)


def _create_shared_server_checkout(tmp_path: Path) -> Path:
    """Create a Git-backed current server accepted by the adapter front door."""
    checkout = tmp_path / "adapter_current_checkout"
    server = checkout / "colyseus_server"
    (server / "src").mkdir(parents=True)
    (server / "package.json").write_text('{"name":"fixture"}\n')
    (server / "src" / "index.ts").write_text("console.log('fixture');\n")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Grid Fixture"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "config",
            "user.email",
            "grid-fixture@example.invalid",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-q", "-m", "server fixture"],
        check=True,
    )
    return server


def _frame_entry(path: Path, metadata: dict) -> dict:
    return {"path": str(path), "metadata": metadata}


def _lane(tmp_path: Path, *, duplicate: bool = False, beam: bool = False, missing_meta: bool = False) -> tuple[dict, list[Path]]:
    current_paths: dict[str, dict] = {}
    baseline_paths: dict[str, dict] = {}
    written: list[Path] = []
    labels = ["00_before", "move_080", "99_after"]
    route_ticks = [30, 90, 150]
    for idx, label in enumerate(labels):
        current_png = tmp_path / "current" / f"{label}.png"
        baseline_png = tmp_path / "baseline" / f"{label}.png"
        if duplicate and idx == 2:
            current_png = tmp_path / "current" / "move_080.png"
        else:
            _write_png(current_png, (20 + idx * 30, 40, 60))
            written.append(current_png)
        _write_png(baseline_png, (80, 20 + idx * 30, 100))
        written.append(baseline_png)
        meta = {} if missing_meta else _meta(beam=beam)
        current_paths[label] = _frame_entry(current_png, meta)
        baseline_paths[label] = _frame_entry(baseline_png, meta)
    lane = {
        "id": "night_lighthouse_beam" if beam else "perspective_terrain_water_static",
        "bug_refs": ["FL-4360"] if beam else ["FL-4270", "FL-4359"],
        "baseline": "baseline_a",
        "view_mode": "perspective",
        "camera_position": [10.0, 20.0, 30.0],
        "camera_yaw": 15.0,
        "camera_pitch": -10.0,
        "camera_roll": 0.0,
        "camera_fov": 60.0,
        "projection_params": {"near": 0.05, "far": 400.0},
        "player_position": [1.0, 2.0, 3.0],
        "player_yaw": 90.0,
        "npc_positions": [[5.0, 0.0, 5.0]],
        "airplane_position": [20.0, 40.0, 60.0],
        "airplane_yaw": 180.0,
        "world_time": 22.0,
        "night_state": True,
        "feature_flags": ["ascii", "foliage_cards", "water_current_package"],
        # Baseline A v2 binds the ordered route, not merely the frame names.
        # Giving every fixture frame a real server tick makes route drift
        # observable before the grid can present mismatched poses as a pair.
        "frames": [
            {"label": label, "route_tick": route_tick}
            for label, route_tick in zip(labels, route_ticks)
        ],
        "required_current_frame_paths": current_paths,
        "required_baseline_frame_paths": baseline_paths,
        "expected_runtime_source_files": list(FL4431_RUNTIME_SOURCE_FILES),
        "why_fix_should_work": "test fixture validates fail-closed manifest pairing",
    }
    return lane, written


NIGHT_PRODUCT_COUNT_FIELDS = (
    "lighthouse_beam_contributed_subcell_count",
    "bonfire_flame_contributed_subcell_count",
    "bonfire_light_contributed_subcell_count",
    "bonfire_smoke_contributed_subcell_count",
    "lighthouse_tower_selected_subcell_count",
    "terrain_selected_subcell_count",
    "water_selected_subcell_count",
)


def _night_airborne_lane(
    tmp_path: Path,
) -> tuple[dict, dict, dict, dict]:
    labels = ("beam_t0", "beam_t1", "beam_t2")
    ticks = (30, 150, 270)
    phase_offsets = (0.0, 2.0943951023931953, 4.1887902047863905)
    night_features = [
        "ascii",
        "night",
        "lighthouse_beam",
        "bonfire_light",
        "terrain",
        "water_current_package",
        "foliage_cards",
    ]
    frame_specs = []
    current = {}
    baseline = {}
    for index, (label, route_tick, phase_offset) in enumerate(
        zip(labels, ticks, phase_offsets)
    ):
        airplane_position = [20.0 + index * 4.0, 40.0, 60.0 + index * 3.0]
        frame_specs.append(
            {
                "label": label,
                "route_tick": route_tick,
                "airplane_position": airplane_position,
                "beam_phase_offset_target": phase_offset,
            }
        )
        for side, frames, capture_id, base_azimuth, color in (
            ("current", current, "current-night-process", 1.1, (30 + index * 40, 70, 150)),
            ("baseline", baseline, "baseline-night-process", 2.2, (150, 50 + index * 40, 30)),
        ):
            png = tmp_path / side / f"{label}.png"
            _write_png(png, color)
            metadata = {
                **_meta(beam=True),
                "feature_flags": list(night_features),
                "airplane_position": airplane_position,
                "runtime_airplane_position": airplane_position,
                "canonical_review_capture": True,
                "temporal_continuous_run": True,
                "temporal_capture_id": capture_id,
                "temporal_frame_index": index,
                "temporal_elapsed_seconds": 1.0 + index * 2.0,
                "route_tick": route_tick,
                "authority_server_tick": route_tick,
                "png_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
                "png_captured_in_same_process": True,
                "source_frame": str(png),
                "lighthouse_record_published": True,
                "bonfire_record_published": True,
                "beam_phase_offset_target": phase_offset,
                "beam_phase_offset_measured": phase_offset,
                # The two arms intentionally start at different absolute
                # angles. Only their natural relative motion is comparable.
                "lighthouse_beam_azimuth": (
                    base_azimuth + phase_offset
                ) % (2.0 * 3.141592653589793),
            }
            if side == "current":
                metadata.update({key: index + 1 for key in NIGHT_PRODUCT_COUNT_FIELDS})
            else:
                metadata.update({key: None for key in NIGHT_PRODUCT_COUNT_FIELDS})
            frames[label] = GRID_RUNNER.FrameEvidence(
                label=label,
                path=png,
                metadata=metadata,
                sha256=GRID_RUNNER._sha256(png),
                size=(1600, 900),
            )
    lane = {
        "id": "night_lighthouse_beam",
        "bug_refs": ["FL-4431", "FL-4332"],
        "baseline": "baseline_a",
        "capture_sequence": "static_temporal_sequence",
        "view_mode": "perspective",
        "camera_position": [10.0, 20.0, 30.0],
        "camera_yaw": 15.0,
        "camera_pitch": -10.0,
        "camera_roll": 0.0,
        "camera_fov": 60.0,
        "projection_params": {"near": 0.05, "far": 400.0},
        "player_position": [1.0, 2.0, 3.0],
        "player_yaw": 90.0,
        "npc_positions": [[5.0, 0.0, 5.0]],
        "airplane_position": [20.0, 40.0, 60.0],
        "airplane_yaw": 180.0,
        "world_time": 22.0,
        "night_state": True,
        "feature_flags": night_features,
        "frames": frame_specs,
        "required_current_frame_paths": {
            label: _frame_entry(frame.path, frame.metadata)
            for label, frame in current.items()
        },
        "required_baseline_frame_paths": {
            label: _frame_entry(frame.path, frame.metadata)
            for label, frame in baseline.items()
        },
        "expected_runtime_source_files": list(FL4431_RUNTIME_SOURCE_FILES),
        "why_fix_should_work": (
            "same-process airborne route facts are paired with six PNG cells"
        ),
    }
    expected = {
        spec["label"]: GRID_RUNNER._frame_expected_values(lane, spec)
        for spec in frame_specs
    }
    return lane, current, baseline, expected


def _airplane_lane(tmp_path: Path, lane_id: str) -> dict:
    lane, _ = _lane(tmp_path)
    lane = json.loads(json.dumps(lane))
    lane["id"] = lane_id
    lane["bug_refs"] = ["FL-4388"]
    lane["view_mode"] = "perspective" if lane_id.endswith("perspective") else "ortho"
    for label, entry in lane["required_current_frame_paths"].items():
        entry["path"] = str(tmp_path / lane_id / "current" / f"{label}.png")
        entry["metadata"]["view_mode"] = lane["view_mode"]
        entry["metadata"]["airplane_dynamic_face_count"] = 272
        _write_png(Path(entry["path"]), (40, 80, 140))
    for label, entry in lane["required_baseline_frame_paths"].items():
        entry["path"] = str(tmp_path / lane_id / "baseline" / f"{label}.png")
        entry["metadata"]["view_mode"] = lane["view_mode"]
        entry["metadata"]["airplane_dynamic_face_count"] = 0
        _write_png(Path(entry["path"]), (140, 80, 40))
    return lane


def _file_sha256(path: Path) -> str:
    """Hash one fixture attachment exactly as the v2 validator does."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_ref(path: Path) -> dict[str, Any]:
    """Build the closed path, size, and digest record required by v2."""

    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": _file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Write stable JSON so every receipt attachment has reproducible bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _initialize_fixture_repo(root: Path, message: str) -> tuple[str, str]:
    """Commit one isolated fixture checkout without touching the shared index."""

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Grid Fixture"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "config",
            "user.email",
            "grid-fixture@example.invalid",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", message],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()
    return commit, tree


def _create_baseline_v2_fixture(
    *,
    tmp_path: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    baseline_source: Path,
    baseline_commit: str,
    baseline_tree: str,
    proof_source: Path,
    proof_commit: str,
    shared_current_server: Path,
    receipt_mutator: Callable[[dict[str, Any], Path], None] | None,
) -> Path:
    """Create a receipt through the real v2 builder, then apply one mutation.

    The grid's validator checks live Git identities and attachment bytes. These
    fixtures therefore use real temporary repositories instead of bypassing
    validation with a mock. A destructive test may mutate the completed
    receipt, while the ordinary path remains exactly what production accepts.
    """

    lane = manifest["lanes"][0]
    specs = GRID_RUNNER._frame_spec_map(lane)
    labels = list(specs)
    route_ticks = [int(specs[label]["route_tick"]) for label in labels]
    tick_stride = (
        route_ticks[1] - route_ticks[0] if len(route_ticks) > 1 else 60
    )
    poses: list[dict[str, Any]] = []
    for label, route_tick in zip(labels, route_ticks):
        expected = GRID_RUNNER._frame_expected_values(lane, specs[label])
        projection = str(expected.get("view_mode", "perspective"))
        if projection not in {"ortho", "perspective"}:
            # Keep the receipt valid so a malformed manifest reaches the grid's
            # own view-mode rejection instead of failing at fixture creation.
            projection = "perspective"
        projection_params = expected.get("projection_params", {})
        poses.append(
            {
                "label": label,
                "route_tick": route_tick,
                "player_position": list(expected["player_position"]),
                "player_yaw": float(expected["player_yaw"]),
                "camera_position": list(expected["camera_position"]),
                "camera_yaw": float(expected["camera_yaw"]),
                "camera_pitch": float(expected["camera_pitch"]),
                "camera_roll": float(expected["camera_roll"]),
                "airplane_position": list(expected["airplane_position"]),
                "airplane_yaw": float(expected["airplane_yaw"]),
                "projection": projection,
                "fov_degrees": float(expected["camera_fov"]),
                "near": float(projection_params["near"]),
                "far": float(projection_params["far"]),
            }
        )

    capture_id = "grid-fixture-capture-v2"
    temporal_capture_id = "grid-fixture-temporal-v2"
    process_identity = "grid-fixture-godot-pid"
    source_frames: list[dict[str, Any]] = []
    for label, route_tick in zip(labels, route_ticks):
        evidence_dir = tmp_path / "baseline_v2_evidence"
        png = evidence_dir / f"{label}.png"
        metadata = evidence_dir / f"{label}.metadata.json"
        runtime_truth = evidence_dir / f"{label}.runtime_truth.json"
        _write_png(png, (25 + route_tick % 180, 70, 120))
        common = {
            "capture_id": capture_id,
            "temporal_capture_id": temporal_capture_id,
            "process_identity": process_identity,
            "label": label,
            "route_tick": route_tick,
        }
        _write_json(metadata, common)
        _write_json(runtime_truth, common)
        source_frames.append(
            {
                "lane_id": lane["id"],
                "label": label,
                "arm": "baseline",
                "route_tick": route_tick,
                "capture_id": capture_id,
                "temporal_capture_id": temporal_capture_id,
                "process_identity": process_identity,
                "png": _path_ref(png),
                "metadata": _path_ref(metadata),
                "runtime_truth": _path_ref(runtime_truth),
            }
        )

    fake_godot = tmp_path / "Godot-fixture"
    fake_godot.write_bytes(b"fixture Godot executable identity")
    import_marker = (
        baseline_source
        / "godot_project"
        / ".godot"
        / "editor"
        / "project_metadata.cfg"
    )
    capture_path = tmp_path / "baseline_v2_capture.json"
    feature_values = lane.get("feature_flags", lane.get("required_features", []))
    features = list(dict.fromkeys(str(value) for value in feature_values))
    _write_json(
        capture_path,
        {
            "schema": BASELINE_V2.CAPTURE_SCHEMA,
            "capture_classification": "non_promotable_diagnostic",
            "capture_id": capture_id,
            "temporal_capture_id": temporal_capture_id,
            "process_identity": process_identity,
            "created_at_utc": "2026-07-26T12:00:00+00:00",
            "baseline_source_commit": baseline_commit,
            "baseline_source_tree": baseline_tree,
            "proof_tool_commit": proof_commit,
            "lane_manifest_path": str(manifest_path.resolve()),
            "lane_manifest_sha256": _file_sha256(manifest_path),
            "argument_vectors": {
                "day": ["--fl4408-day-cycle-time=0.5"],
                "night": ["--fl4408-day-cycle-time=0.9375"],
            },
            "route": {
                "lane_id": lane["id"],
                "identity": "server_tick_constant_physical_flight_input",
                "physical_input_action": "move_right",
                "tick_zero_kind": "server_first_changed_admitted_flight_intent",
                "warmup_tick": route_ticks[0],
                "target_ticks": route_ticks,
                "tick_stride": tick_stride,
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
                "night_world_hour": float(lane["world_time"]),
            },
            "server_identity": {
                "server_commit": "f" * 40,
                "server_source_root": str(shared_current_server.resolve()),
                "server_runtime_scope_fingerprint": "1" * 64,
                "server_endpoint": "ws://127.0.0.1:2581",
                "server_process_identity": "grid-fixture-server-pid",
            },
            "baseline_build_identity": {
                "execution_kind": "source_project_via_installed_godot_binary",
                "godot_requested_path": str(fake_godot.resolve()),
                "godot_resolved_binary": _path_ref(fake_godot),
                "godot_version": "4.6.fixture",
                "godot_binary_format": "fixture executable",
                "imported_source_commit": baseline_commit,
                "imported_source_tree": baseline_tree,
                "project_import_marker": _path_ref(import_marker),
            },
            "features": features,
            "source_frames": source_frames,
        },
    )

    predecessor = tmp_path / "baseline_receipt_v1_predecessor.json"
    _write_json(
        predecessor,
        {
            "schema": "asciicker.godot_baseline.v1",
            "source_root": str(baseline_source.resolve()),
            "source_commit": baseline_commit,
        },
    )
    receipt_path = tmp_path / "baseline_receipt_v2.json"
    designation = tmp_path / "baseline_designation.json"
    _write_json(
        designation,
        {
            "schema": BASELINE_V2.DESIGNATION_SCHEMA,
            "baseline_name": BASELINE_V2.BASELINE_NAME,
            "successor_schema": BASELINE_V2.SCHEMA,
            "successor_receipt_path": str(receipt_path.resolve()),
            "operator_identity": "grid-test-operator",
            "designated_at_utc": "2026-07-26T11:59:00+00:00",
            "statement": "Designate this complete successor as Baseline A for FL-4431.",
            "predecessor_receipt_path": str(predecessor.resolve()),
            "predecessor_receipt_sha256": _file_sha256(predecessor),
        },
    )
    BASELINE_V2.create_receipt(
        output_path=receipt_path,
        designation_record_path=designation,
        capture_receipt_path=capture_path,
        lane_manifest_path=manifest_path,
        source_root=baseline_source,
        proof_tool_root=proof_source,
        created_at_utc="2026-07-26T12:01:00+00:00",
    )
    if receipt_mutator is not None:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_mutator(receipt, receipt_path)
        receipt["self_hash"]["digest"] = BASELINE_V2._receipt_digest(receipt)
        _write_json(receipt_path, receipt)
    return receipt_path


def _run_runner(
    tmp_path: Path,
    manifest: dict,
    *,
    commit: str,
    runtime_file: str,
    require_canonical_lanes: bool = False,
    run_captures: bool = True,
    commit_variant: str = "exact",
    dirty_current_path: str = "",
    receipt_mutator: Callable[[dict[str, Any], Path], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    baseline_source = tmp_path / "baseline_source"
    baseline_project = baseline_source / "godot_project"
    baseline_project.mkdir(parents=True, exist_ok=True)
    (baseline_project / "project.godot").write_text("[application]\nconfig/name=\"Baseline fixture\"\n", encoding="utf-8")
    import_marker = (
        baseline_project / ".godot" / "editor" / "project_metadata.cfg"
    )
    import_marker.parent.mkdir(parents=True)
    import_marker.write_text("[editor_metadata]\nfixture=true\n", encoding="utf-8")
    baseline_commit, baseline_tree = _initialize_fixture_repo(
        baseline_source,
        "baseline fixture",
    )

    current_source = tmp_path / "current_source"
    current_project = current_source / "godot_project"
    shared_current_server = current_source / "colyseus_server"
    (shared_current_server / "src").mkdir(parents=True, exist_ok=True)
    (shared_current_server / "package.json").write_text(
        '{"scripts":{"start":"tsx src/index.ts"}}\n',
        encoding="utf-8",
    )
    (shared_current_server / "src" / "index.ts").write_text(
        "console.log('shared current server fixture');\n",
        encoding="utf-8",
    )
    for source_path in FL4431_RUNTIME_SOURCE_FILES:
        fixture_path = current_source / source_path
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(f"// fixture for {source_path}\n", encoding="utf-8")
    fixture_head, _ = _initialize_fixture_repo(current_source, "current fixture")

    # The production grid insists that the executing checkout is the exact
    # proof-tool checkout named by Baseline A v2. Copy the real tools into an
    # isolated clean repository, commit the effective manifest beside them,
    # then run that exact committed grid builder.
    proof_source = tmp_path / "proof_source"
    proof_source.mkdir(parents=True)
    required_proof_paths = {
        *BASELINE_V2.REQUIRED_TOOL_PATHS.values(),
        *BASELINE_V2.REQUIRED_ANALYZER_PATHS.values(),
        "scripts/godot_proof_publication.py",
    }
    for relative in sorted(required_proof_paths):
        source = ROOT / relative
        destination = proof_source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    manifest_path = proof_source / "proof" / "grid_fixture.manifest.json"
    _write_json(manifest_path, manifest)
    proof_commit, _ = _initialize_fixture_repo(proof_source, "proof fixture")
    baseline_receipt_path = _create_baseline_v2_fixture(
        tmp_path=tmp_path,
        manifest=manifest,
        manifest_path=manifest_path,
        baseline_source=baseline_source,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        proof_source=proof_source,
        proof_commit=proof_commit,
        shared_current_server=shared_current_server,
        receipt_mutator=receipt_mutator,
    )

    requested_commit = fixture_head
    if commit_variant == "ancestor":
        marker = current_source / "fixture_generation.txt"
        marker.write_text("second generation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(current_source), "add", "fixture_generation.txt"], check=True)
        subprocess.run(["git", "-C", str(current_source), "commit", "-q", "-m", "newer current fixture"], check=True)
    elif commit_variant == "abbreviated":
        requested_commit = fixture_head[:12]
    elif commit_variant != "exact":
        raise AssertionError(f"unknown commit variant: {commit_variant}")
    if dirty_current_path:
        dirty_path = current_source / dirty_current_path
        dirty_path.parent.mkdir(parents=True, exist_ok=True)
        dirty_path.write_text("uncommitted diagnostic dirt\n", encoding="utf-8")
    runtime_files_arg = runtime_file
    if runtime_file in FL4431_RUNTIME_SOURCE_FILES:
        runtime_files_arg = ",".join(FL4431_RUNTIME_SOURCE_FILES)
    return subprocess.run(
        [
            sys.executable,
            str(proof_source / "scripts" / RUNNER.name),
            "--current-project",
            str(current_project),
            "--baseline-receipt",
            str(baseline_receipt_path),
            "--shared-current-colyseus-dir",
            str(shared_current_server),
            "--required-current-commit",
            requested_commit,
            "--required-runtime-files",
            runtime_files_arg,
            "--code-refs",
            "scripts/build_godot_canonical_png_grid.py:1,scripts/fl4408_actual_godot_capture_adapter.py:1",
            "--fix-attempt-number",
            "1",
            "--fix-attempt-description",
            "fixture shared-server canonical grid attempt",
            "--ascii-required",
            "true",
            "--fullscreen-required",
            "true",
            "--lanes",
            str(manifest_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--run-captures",
            "true" if run_captures else "false",
            "--require-canonical-lanes",
            "true" if require_canonical_lanes else "false",
            "--require-actual-godot-capture",
            "false",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_canonical_grid_runner_builds_paired_grid(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr
    out_png = tmp_path / "out" / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.png"
    out_manifest = out_png.with_suffix(".manifest.json")
    assert out_png.exists()
    assert out_manifest.exists()
    publication_manifest = tmp_path / "out" / "godot_proof_publication.json"
    assert publication_manifest.exists()
    assert validate_manifest(publication_manifest, allow_incomplete=True) == []
    assert "INCOMPLETE_CURRENT_CAPTURE is diagnostic only, not a publishable proof" in validate_manifest(
        publication_manifest
    )
    publication = load_manifest(publication_manifest)
    grid_artifact = next(
        row for row in publication["artifacts"]
        if row["aliases"] == ["GODOT_CANONICAL_CURRENT_BASELINE_GRID.png"]
    )
    canonical_grid = tmp_path / "out" / grid_artifact["path"]
    assert canonical_grid.name == grid_artifact["expected_filename"]
    assert canonical_grid.stat().st_ino == out_png.stat().st_ino
    receipt = json.loads(out_manifest.read_text(encoding="utf-8"))
    assert len(receipt["panels"]) == 6
    assert receipt["baseline_receipt_schema"] == "asciicker.godot_baseline.v2"
    assert len(receipt["baseline_receipt_self_hash"]) == 64
    assert len(receipt["baseline_source_commit"]) == 40
    assert len(receipt["baseline_source_tree"]) == 40
    assert receipt["baseline_capture_id"] == "grid-fixture-capture-v2"
    assert receipt["baseline_temporal_capture_id"] == "grid-fixture-temporal-v2"
    assert len(receipt["baseline_proof_tool_commit"]) == 40
    assert receipt["head"] == receipt["required_current_commit"]
    assert receipt["required_runtime_files"] == FL4431_RUNTIME_SOURCE_FILES
    assert receipt["operator_signoff"]["required"] is True
    assert receipt["operator_signoff"]["status"] == (
        "missing_until_analyze_failure_log_signoff_verify_passes"
    )
    assert receipt["godot_proof_publication_manifest"] == str(publication_manifest)
    assert GRID_RUNNER._operator_signoff_errors(
        receipt["operator_signoff"],
        artifact=out_png,
        commit=receipt["required_current_commit"],
    ) == []


def test_canonical_grid_runner_rejects_v2_baseline_commit_relabel(
    tmp_path: Path,
) -> None:
    """A re-signed receipt still cannot relabel the live Baseline checkout."""

    lane, _ = _lane(tmp_path)

    def relabel_baseline(receipt: dict[str, Any], _path: Path) -> None:
        receipt["baseline_source"]["commit"] = "9" * 40

    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit="unused",
        runtime_file=lane["expected_runtime_source_files"][0],
        receipt_mutator=relabel_baseline,
    )
    assert result.returncode == 2
    assert "commit mismatches live HEAD" in result.stderr


def test_canonical_grid_runner_rejects_v2_route_drift(
    tmp_path: Path,
) -> None:
    """A valid v2 receipt for a different tick route cannot pair these frames."""

    lane, _ = _lane(tmp_path)

    def shift_receipted_route(receipt: dict[str, Any], _path: Path) -> None:
        capture_path = Path(receipt["capture_input"]["path"])
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        route = capture["route"]
        route["warmup_tick"] += 1
        route["target_ticks"] = [tick + 1 for tick in route["target_ticks"]]
        for pose in route["poses"]:
            pose["route_tick"] += 1
        for frame in capture["source_frames"]:
            frame["route_tick"] += 1
        _write_json(capture_path, capture)
        receipt["capture_input"] = _path_ref(capture_path)
        receipt["capture_contract"] = BASELINE_V2._capture_contract(capture)
        receipt["source_frames"] = capture["source_frames"]

    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit="unused",
        runtime_file=lane["expected_runtime_source_files"][0],
        receipt_mutator=shift_receipted_route,
    )
    assert result.returncode == 2
    assert "manifest route ticks differ from Baseline receipt route" in result.stderr


def test_canonical_grid_runner_rejects_omitted_fl4431_runtime_source(
    tmp_path: Path,
) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    incomplete = FL4431_RUNTIME_SOURCE_FILES[:-1]
    lane["expected_runtime_source_files"] = list(incomplete)
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=",".join(incomplete),
    )
    assert result.returncode == 2
    assert "--required-runtime-files omits FL-4431 runtime sources" in result.stderr


def test_canonical_grid_runner_rejects_unrelated_dirty_current_path(
    tmp_path: Path,
) -> None:
    lane, _ = _lane(tmp_path)
    commit, runtime_file = _runtime_commit_and_file()
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=runtime_file,
        dirty_current_path="unrelated/operator_notes.txt",
    )
    assert result.returncode == 2
    assert "current source tree must be fully clean" in result.stderr
    assert "unrelated/operator_notes.txt" in result.stderr


def test_canonical_grid_runner_rejects_abbreviated_current_commit(
    tmp_path: Path,
) -> None:
    lane, _ = _lane(tmp_path)
    commit, runtime_file = _runtime_commit_and_file()
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=runtime_file,
        commit_variant="abbreviated",
    )
    assert result.returncode == 2
    assert "full lowercase 40-character Git commit" in result.stderr


def test_canonical_grid_runner_rejects_ancestor_instead_of_exact_head(
    tmp_path: Path,
) -> None:
    lane, _ = _lane(tmp_path)
    commit, runtime_file = _runtime_commit_and_file()
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=runtime_file,
        commit_variant="ancestor",
    )
    assert result.returncode == 2
    assert "must equal current HEAD exactly" in result.stderr


def test_canonical_grid_runner_rejects_missing_signoff_command(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "grid.png"
    complete = GRID_RUNNER._operator_signoff_contract(
        artifact=artifact,
        commit="a" * 40,
        timestamp="2026-07-25T12:00:00+09:00",
    )
    expected_error = {
        "verify_command": "operator_signoff verify command is missing or incoherent",
        "query_command": "operator_signoff query command is missing or incoherent",
        "record_pass_command": "operator_signoff record command is missing",
    }
    for command_key, expected in expected_error.items():
        damaged = json.loads(json.dumps(complete))
        del damaged[command_key]
        errors = GRID_RUNNER._operator_signoff_errors(
            damaged,
            artifact=artifact,
            commit="a" * 40,
        )
        assert expected in errors
    wrong_commit = json.loads(json.dumps(complete))
    record_command = wrong_commit["record_pass_command"]
    commit_index = record_command.index("--commit") + 1
    record_command[commit_index] = "b" * 40
    assert (
        "operator_signoff record command commit differs from captured source"
        in GRID_RUNNER._operator_signoff_errors(
            wrong_commit,
            artifact=artifact,
            commit="a" * 40,
        )
    )


def test_canonical_grid_runner_rejects_duplicate_placeholder_frame(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path, duplicate=True)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "frame reused as placeholder" in result.stderr


def test_canonical_grid_runner_rejects_reused_runtime_capture_receipt(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    shared_receipt = tmp_path / "captures" / "perspective_terrain_water_static" / "current" / "complete_capture.json"
    for label, entry in lane["required_current_frame_paths"].items():
        entry["metadata"]["complete_capture_path"] = str(shared_receipt)
        entry["metadata"]["capture_signature_hash"] = "same-runtime-capture"
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "runtime capture reused across frame labels" in result.stderr


def test_canonical_grid_runner_rejects_missing_frame_metadata(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path, missing_meta=True)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "missing frame metadata" in result.stderr


def test_night_grid_accepts_three_airborne_rows_with_direct_runtime_truth(
    tmp_path: Path,
) -> None:
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    assert GRID_RUNNER._validate_beam_lane(lane, current, baseline, expected) == []


def test_night_grid_builds_six_labeled_airborne_panels(tmp_path: Path) -> None:
    lane, _, _, _ = _night_airborne_lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=lane["expected_runtime_source_files"][0],
    )
    assert result.returncode == 0, result.stderr
    receipt_path = (
        tmp_path / "out" / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.manifest.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    night_panels = [
        panel
        for panel in receipt["panels"]
        if panel["lane"] == "night_lighthouse_beam"
    ]
    assert len(night_panels) == 6
    assert {panel["side"] for panel in night_panels} == {"baseline", "current"}
    assert {panel["frame"] for panel in night_panels} == {
        "beam_t0",
        "beam_t1",
        "beam_t2",
    }
    for panel in night_panels:
        runtime_truth = panel["runtime_truth"]
        assert isinstance(runtime_truth["route_tick"], int)
        assert isinstance(runtime_truth["requested_phase_offset"], float)
        assert isinstance(runtime_truth["measured_phase_offset"], float)
        assert isinstance(runtime_truth["measured_azimuth"], float)
        visible = panel["visible_label_fields"]
        assert tuple(visible) == GRID_RUNNER.MANDATORY_PANEL_LABEL_FIELDS
        assert visible["timestamp"]
        assert visible["failure_log_refs"] == "FL-4431,FL-4332"
        assert "scripts/build_godot_canonical_png_grid.py:1" in visible["code_refs"]
        assert visible["fix_attempt_number"] == "1"
        assert (
            visible["fix_attempt_description"]
            == "fixture shared-server canonical grid attempt"
        )
        assert visible["rationale"]
        assert len(visible["commit"]) == 40
        assert visible["dirty_state"] == "clean"
        assert (
            visible["baseline_panel_identity"]
            == "Baseline A renderer + shared current server"
        )
        assert (
            visible["current_panel_identity"]
            == "current renderer + shared current server"
        )
        assert visible["panel_identity"] == GRID_RUNNER.RENDERER_ARM_LABELS[
            panel["side"]
        ]


def test_each_missing_mandatory_visible_panel_label_fails() -> None:
    """Deleting any review field must stop grid composition."""
    complete = {
        field: f"value-{index}"
        for index, field in enumerate(GRID_RUNNER.MANDATORY_PANEL_LABEL_FIELDS)
    }
    assert GRID_RUNNER._panel_label_text(complete)
    for field in GRID_RUNNER.MANDATORY_PANEL_LABEL_FIELDS:
        missing = dict(complete)
        del missing[field]
        with pytest.raises(GRID_RUNNER.GridInputError) as exc:
            GRID_RUNNER._panel_label_text(missing)
        assert field in str(exc.value)


def test_builder_requires_absolute_existing_shared_current_server(
    tmp_path: Path,
) -> None:
    """The top-level runner cannot defer shared-server selection to an arm."""
    assert GRID_RUNNER._validate_shared_current_colyseus_dir("")
    assert GRID_RUNNER._validate_shared_current_colyseus_dir("colyseus_server")
    assert GRID_RUNNER._validate_shared_current_colyseus_dir(
        str(tmp_path / "missing" / "colyseus_server")
    )
    existing = tmp_path / "colyseus_server"
    existing.mkdir()
    assert GRID_RUNNER._validate_shared_current_colyseus_dir(str(existing)) == []
    current_project = tmp_path / "current" / "godot_project"
    expected_server = tmp_path / "current" / "colyseus_server"
    current_project.mkdir(parents=True)
    expected_server.mkdir()
    assert (
        GRID_RUNNER._validate_shared_current_colyseus_dir(
            str(expected_server),
            str(current_project),
        )
        == []
    )
    errors = GRID_RUNNER._validate_shared_current_colyseus_dir(
        str(existing),
        str(current_project),
    )
    assert any("current renderer checkout's server root" in error for error in errors)


def test_grid_rejects_wrong_shared_server_on_one_renderer_arm(
    tmp_path: Path,
) -> None:
    """Baseline A cannot smuggle its historical server into one grid row."""
    lane, current, baseline, _ = _night_airborne_lane(tmp_path)
    expected_root = "/fixture/current/colyseus_server"
    for frame in current.values():
        _attach_server_identity(frame, "current", root=expected_root)
    for frame in baseline.values():
        _attach_server_identity(frame, "baseline", root=expected_root)
    assert (
        GRID_RUNNER._validate_shared_server_pairing(lane, current, baseline)
        == []
    )

    wrong_root = "/fixture/baseline/colyseus_server"
    _attach_server_identity(baseline["beam_t1"], "baseline", root=wrong_root)
    errors = GRID_RUNNER._validate_shared_server_pairing(
        lane, current, baseline
    )
    assert any("shared server identity differs" in error for error in errors)


def test_grid_rejects_absent_shared_server_frame_identity(
    tmp_path: Path,
) -> None:
    """A visually plausible frame with no server provenance fails closed."""
    lane, current, _, _ = _night_airborne_lane(tmp_path)
    errors = GRID_RUNNER._validate_shared_server_frame(
        lane["id"],
        "current",
        current["beam_t0"],
        "/fixture/current/colyseus_server",
        "a" * 40,
    )
    assert errors == [
        "night_lighthouse_beam:current:beam_t0: shared current server identity is absent"
    ]


def test_grid_accepts_exact_arm_labels_and_complete_shared_server_identity(
    tmp_path: Path,
) -> None:
    """Both immutable renderer labels remain exact while server truth is shared."""
    lane, current, baseline, _ = _night_airborne_lane(tmp_path)
    expected_root = "/fixture/current/colyseus_server"
    for side, frames in (("current", current), ("baseline", baseline)):
        for frame in frames.values():
            _attach_server_identity(frame, side, root=expected_root)
            assert (
                GRID_RUNNER._validate_shared_server_frame(
                    lane["id"], side, frame, expected_root, "a" * 40
                )
                == []
            )


def test_grid_rejects_server_commit_that_is_not_current_renderer_commit(
    tmp_path: Path,
) -> None:
    """A shared historical server is still the wrong server for both arms."""
    lane, current, _, _ = _night_airborne_lane(tmp_path)
    expected_root = "/fixture/current/colyseus_server"
    frame = current["beam_t0"]
    _attach_server_identity(frame, "current", root=expected_root)
    errors = GRID_RUNNER._validate_shared_server_frame(
        lane["id"],
        "current",
        frame,
        expected_root,
        "d" * 40,
    )
    assert any("differs from current renderer commit" in error for error in errors)


def test_night_grid_rejects_each_missing_current_product_count(
    tmp_path: Path,
) -> None:
    for count_field in NIGHT_PRODUCT_COUNT_FIELDS:
        lane, current, baseline, expected = _night_airborne_lane(
            tmp_path / count_field
        )
        current["beam_t1"].metadata[count_field] = 0
        errors = GRID_RUNNER._validate_beam_lane(
            lane, current, baseline, expected
        )
        assert any(count_field in error for error in errors), count_field


def test_night_grid_rejects_repeated_png_frame(tmp_path: Path) -> None:
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    repeated = current["beam_t1"]
    current["beam_t2"] = GRID_RUNNER.FrameEvidence(
        label="beam_t2",
        path=repeated.path,
        metadata=current["beam_t2"].metadata,
        sha256=repeated.sha256,
        size=repeated.size,
    )
    errors = GRID_RUNNER._validate_lane_pairing(
        lane, current, baseline, expected
    )
    assert any("frame reused as placeholder" in error for error in errors)


def test_night_grid_rejects_mixed_capture_process(tmp_path: Path) -> None:
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    current["beam_t1"].metadata["temporal_capture_id"] = "detached-process"
    current["beam_t2"].metadata["png_capture_owner"] = "static_capture.gd"
    errors = GRID_RUNNER._validate_beam_lane(
        lane, current, baseline, expected
    )
    assert any("one continuous airborne capture" in error for error in errors)
    assert any("dogfight process" in error for error in errors)


def test_night_grid_rejects_stale_one_shot_shape(tmp_path: Path) -> None:
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    lane["capture_sequence"] = "single_frame"
    errors = GRID_RUNNER._validate_beam_lane(
        lane, current, baseline, expected
    )
    assert any("airborne temporal sequence" in error for error in errors)

    lane["capture_sequence"] = "static_temporal_sequence"
    lane["frames"] = [{"label": "beam_expected_in_png"}]
    errors = GRID_RUNNER._validate_beam_lane(
        lane,
        {"beam_expected_in_png": current["beam_t0"]},
        {"beam_expected_in_png": baseline["beam_t0"]},
        {"beam_expected_in_png": expected["beam_t0"]},
    )
    assert any("beam_t0, beam_t1, beam_t2" in error for error in errors)


def test_night_grid_requires_explicit_baseline_abi_null_evidence(
    tmp_path: Path,
) -> None:
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    del baseline["beam_t1"].metadata["water_selected_subcell_count"]
    baseline["beam_t2"].metadata["terrain_selected_subcell_count"] = 1
    errors = GRID_RUNNER._validate_beam_lane(
        lane, current, baseline, expected
    )
    assert sum(
        "Baseline A product ABI absence must be explicit null" in error
        for error in errors
    ) == 2


def test_night_grid_panel_label_separates_requested_offset_and_measured_azimuth(
    tmp_path: Path,
) -> None:
    _, current, _, _ = _night_airborne_lane(tmp_path)
    label = GRID_RUNNER._night_panel_runtime_label(current["beam_t1"].metadata)
    assert "route_tick=150" in label
    assert "requested_phase_offset=2.094395" in label
    assert "measured_azimuth=3.194395" in label


def test_night_grid_rejects_cross_arm_measured_offset_drift(
    tmp_path: Path,
) -> None:
    """Two renderers may start at different angles, but their offsets must match."""
    lane, current, baseline, expected = _night_airborne_lane(tmp_path)
    baseline["beam_t1"].metadata["beam_phase_offset_measured"] += 0.2
    errors = GRID_RUNNER._validate_beam_lane(
        lane,
        current,
        baseline,
        expected,
    )
    assert any(
        "baseline and current measured beam offsets are not matched" in error
        for error in errors
    )


def test_canonical_grid_runner_rejects_missing_canonical_lanes(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=runtime_file,
        require_canonical_lanes=True,
    )
    assert result.returncode == 2
    assert "manifest missing canonical lanes" in result.stderr


def test_canonical_grid_runner_rejects_extra_canonical_lane_ids(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    lane["id"] = "not_a_required_bug_lane"
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=runtime_file,
        require_canonical_lanes=True,
    )
    assert result.returncode == 2
    assert "manifest has non-canonical lanes" in result.stderr


def test_original_bug_manifest_declares_full_canonical_lane_matrix() -> None:
    manifest = json.loads(ORIGINAL_BUG_MANIFEST.read_text(encoding="utf-8"))
    lanes = manifest["lanes"]
    lane_ids = {lane["id"] for lane in lanes}
    required_files = lanes[0]["expected_runtime_source_files"]
    assert lane_ids == GRID_RUNNER.REQUIRED_CANONICAL_LANE_IDS
    assert required_files == FL4431_RUNTIME_SOURCE_FILES
    assert all(path.startswith(("godot_project/scripts/", "godot_project/render/", "godot_project/visual/")) for path in required_files)
    for lane in lanes:
        assert "expected_runtime_commit" not in lane
        assert lane["baseline"] == "baseline_a"
        if lane["id"] in MOVEMENT_SEQUENCE_LANES:
            expected_sequence = "movement_sequence"
        elif lane["id"] in STATIC_TEMPORAL_SEQUENCE_LANES:
            expected_sequence = "static_temporal_sequence"
        else:
            expected_sequence = "single_frame"
        assert lane["capture_sequence"] == expected_sequence
        assert lane["expected_runtime_source_files"] == required_files
        for entries in (lane["required_current_frame_paths"], lane["required_baseline_frame_paths"]):
            for entry in entries.values():
                assert str(entry["path"]).startswith("{out_dir}/frames/")
    night_lane = next(
        lane for lane in lanes if lane["id"] == "night_lighthouse_beam"
    )
    # The tracked lane manifest is the v2-receipted authority. It must encode
    # the same natural-time route as the generated package instead of reviving
    # the deleted absolute-phase target contract.
    assert [frame["route_tick"] for frame in night_lane["frames"]] == [30, 150, 270]
    assert [
        frame["beam_phase_offset_target"] for frame in night_lane["frames"]
    ] == list(GRID_ADAPTER.NIGHT_AIRBORNE_PHASE_OFFSETS)
    assert all("beam_phase_target" not in frame for frame in night_lane["frames"])


def test_canonical_grid_runner_rejects_runtime_files_outside_fl4431_set(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    missing_runtime_file = "godot_project/scripts/not_changed_by_this_commit.gd"
    lane["expected_runtime_source_files"] = [runtime_file, missing_runtime_file]
    result = _run_runner(
        tmp_path,
        {"lanes": [lane]},
        commit=commit,
        runtime_file=f"{runtime_file},{missing_runtime_file}",
    )
    assert result.returncode == 2
    assert "files outside the FL-4431 runtime source set" in result.stderr


def test_canonical_grid_runner_rejects_manifest_runtime_file_drift(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    lane["expected_runtime_source_files"] = [runtime_file, "godot_project/shaders/not_in_command.glsl"]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "expected_runtime_source_files has files not declared by --required-runtime-files" in result.stderr


def test_canonical_grid_runner_rejects_invalid_view_mode(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    lane["view_mode"] = "sideways"
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "view_mode must be ortho or perspective" in result.stderr


def test_default_actual_godot_command_uses_manifest_capture_sequence() -> None:
    manifest = json.loads(ORIGINAL_BUG_MANIFEST.read_text(encoding="utf-8"))
    lanes = {lane["id"]: lane for lane in manifest["lanes"]}
    movement_command = GRID_RUNNER._default_actual_godot_capture_command(lanes["perspective_movement_jitter"], "move_080")
    static_command = GRID_RUNNER._default_actual_godot_capture_command(lanes["night_lighthouse_beam"], "beam_t0")
    assert "--capture-sequence" in movement_command
    assert movement_command[movement_command.index("--capture-sequence") + 1] == "movement_sequence"
    assert "--capture-sequence" in static_command
    assert static_command[static_command.index("--capture-sequence") + 1] == "static_temporal_sequence"
    for command in (movement_command, static_command):
        assert command[command.index("--colyseus-dir") + 1] == (
            "{shared_current_colyseus_dir}"
        )


def test_builder_renders_one_shared_server_path_for_both_arms() -> None:
    """Template expansion cannot substitute a renderer-specific server root."""
    manifest = json.loads(ORIGINAL_BUG_MANIFEST.read_text(encoding="utf-8"))
    lane = next(
        value
        for value in manifest["lanes"]
        if value["id"] == "night_lighthouse_beam"
    )
    template = GRID_RUNNER._default_actual_godot_capture_command(
        lane, "beam_t0"
    )
    shared = "/absolute/current/colyseus_server"
    rendered = []
    for side in ("baseline", "current"):
        rendered.append(
            GRID_RUNNER._command_from_template(
                template,
                {
                    "side": side,
                    "shared_current_colyseus_dir": shared,
                },
            )
        )
    assert [
        command[command.index("--colyseus-dir") + 1] for command in rendered
    ] == [shared, shared]


def test_actual_godot_adapter_writes_all_movement_sequence_outputs_from_one_cached_run(tmp_path: Path) -> None:
    shared_server = _create_shared_server_checkout(tmp_path)
    args = Namespace(
        lane_id="perspective_movement_jitter",
        side="current",
        frame="00_before",
        capture_sequence="movement_sequence",
        project=str(tmp_path / "current" / "godot_project"),
        camera_mode="perspective_movement_jitter",
        view_mode="perspective",
        camera_position=json.dumps([10.0, 20.0, 30.0]),
        camera_yaw="15.0",
        camera_pitch="-10.0",
        camera_roll="0.0",
        camera_fov="60.0",
        projection_params=json.dumps({"near": 0.05, "far": 400.0}),
        player_position=json.dumps([1.0, 2.0, 3.0]),
        player_yaw="90.0",
        npc_positions=json.dumps([[5.0, 0.0, 5.0]]),
        airplane_position=json.dumps([20.0, 40.0, 60.0]),
        airplane_yaw="180.0",
        world_time="12.0",
        night_state=False,
        feature_flags=json.dumps(["ascii", "water_current_package"]),
        beam_expected_in_png=False,
        fullscreen=True,
        resolution="3840x2160",
        move_frames=120,
        settle_frames=45,
        after_frames=6,
        plane_circle_frames=240,
        colyseus_dir=str(shared_server),
    )
    out_png = tmp_path / "out" / "current" / "00_before.png"
    receipt = {
        "pass": True,
        "single_frame": False,
        "player_before": {"x": 1.0, "y": 2.0, "z": 3.0},
        "player_move_080": {"x": 1.0, "y": 2.0, "z": 13.0},
        "player_after": {"x": 1.0, "y": 2.0, "z": 23.0},
        "camera_before": {"x": 10.0, "y": 20.0, "z": 30.0},
        "camera_move_080": {"x": 10.0, "y": 20.0, "z": 40.0},
        "camera_after": {"x": 10.0, "y": 20.0, "z": 50.0},
        "airplane_before": {"x": 20.0, "y": 40.0, "z": 60.0},
        "airplane_move_080": {"x": 20.0, "y": 40.0, "z": 70.0},
        "airplane_after": {"x": 20.0, "y": 40.0, "z": 80.0},
    }
    old_run_capture = GRID_ADAPTER._run_capture
    def fake_run_capture(_args: Namespace, run_dir: Path, _signature: dict, signature_hash: str) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        for idx, label in enumerate(("00_before", "move_080", "99_after")):
            _write_scene_sentinel_png(run_dir / f"{label}.png", 40 + idx * 30)
        (run_dir / "receipt.json").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        (run_dir / "complete_capture.json").write_text(json.dumps({"capture_signature_hash": signature_hash}) + "\n", encoding="utf-8")

    old_argv = sys.argv[:]
    try:
        GRID_ADAPTER._run_capture = fake_run_capture
        sys.argv = [
            str(ADAPTER),
            "--out-png",
            str(out_png),
            "--out-metadata",
            str(out_png.with_suffix(out_png.suffix + ".json")),
            "--lane-id",
            args.lane_id,
            "--side",
            args.side,
            "--frame",
            args.frame,
            "--project",
            args.project,
            "--colyseus-dir",
            args.colyseus_dir,
            "--capture-root",
            str(tmp_path / "captures"),
            "--capture-sequence",
            args.capture_sequence,
            "--camera-mode",
            args.camera_mode,
            "--view-mode",
            args.view_mode,
            "--camera-position",
            args.camera_position,
            "--camera-yaw",
            args.camera_yaw,
            "--camera-pitch",
            args.camera_pitch,
            "--camera-roll",
            args.camera_roll,
            "--camera-fov",
            args.camera_fov,
            "--projection-params",
            args.projection_params,
            "--player-position",
            args.player_position,
            "--player-yaw",
            args.player_yaw,
            "--npc-positions",
            args.npc_positions,
            "--airplane-position",
            args.airplane_position,
            "--airplane-yaw",
            args.airplane_yaw,
            "--world-time",
            args.world_time,
            "--feature-flags",
            args.feature_flags,
            "--fullscreen",
        ]
        assert GRID_ADAPTER.main() == 0
    finally:
        GRID_ADAPTER._run_capture = old_run_capture
        sys.argv = old_argv
    for label in ("00_before", "move_080", "99_after"):
        frame = tmp_path / "out" / "current" / f"{label}.png"
        meta = json.loads(frame.with_suffix(frame.suffix + ".json").read_text(encoding="utf-8"))
        assert frame.exists()
        assert meta["capture_sequence"] == "movement_sequence"
        assert meta["source_frame"].endswith(f"{label}.png")
        assert (
            meta["renderer_arm_label"]
            == "current renderer + shared current server"
        )
        assert meta["server_source_root"] == str(shared_server)
        assert len(meta["server_commit"]) == 40
        assert len(meta["server_runtime_scope_fingerprint"]) == 64
        assert len(meta["proof_tool_sha256"]) == 64
    move_meta = json.loads((tmp_path / "out" / "current" / "move_080.png.json").read_text(encoding="utf-8"))
    assert move_meta["runtime_player_position"] == [1.0, 2.0, 13.0]


def test_canonical_grid_runner_rejects_checkpoint_airplane_lane_with_zero_faces(tmp_path: Path) -> None:
    lane = _airplane_lane(tmp_path, "airplane_air_movement_perspective")
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 2
    assert "checkpoint baseline cannot produce the lane" in result.stderr


def test_canonical_grid_runner_runs_capture_commands_before_stitch(tmp_path: Path) -> None:
    capture_script = tmp_path / "fake_capture.py"
    capture_script.write_text(
        "\n".join(
            [
                "import argparse, json",
                "from pathlib import Path",
                "from PIL import Image",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--out-png', required=True)",
                "parser.add_argument('--out-metadata', required=True)",
                "parser.add_argument('--side', required=True)",
                "parser.add_argument('--frame', required=True)",
                "args = parser.parse_args()",
                "meta = {",
                "  'ascii_enabled': True, 'fullscreen': True, 'view_mode': 'perspective',",
                "  'camera_position': [10.0, 20.0, 30.0], 'camera_yaw': 15.0,",
                "  'camera_pitch': -10.0, 'camera_roll': 0.0, 'camera_fov': 60.0,",
                "  'projection_params': {'near': 0.05, 'far': 400.0},",
                "  'player_position': [1.0, 2.0, 3.0], 'player_yaw': 90.0,",
                "  'npc_positions': [[5.0, 0.0, 5.0]],",
                "  'airplane_position': [20.0, 40.0, 60.0], 'airplane_yaw': 180.0,",
                "  'world_time': 22.0, 'night_state': True,",
                "  'feature_flags': ['ascii', 'foliage_cards', 'water_current_package']",
                "}",
                "Path(args.out_png).parent.mkdir(parents=True, exist_ok=True)",
                "for label in ('00_before', 'move_080', '99_after'):",
                "    out_png = Path(args.out_png).with_name(label + '.png')",
                "    out_meta = out_png.with_suffix(out_png.suffix + '.json')",
                "    base = sum(ord(ch) for ch in args.side + label) % 200",
                "    Image.new('RGB', (1600, 900), (base, (base + 40) % 255, (base + 80) % 255)).save(out_png)",
                "    out_meta.write_text(json.dumps(meta) + '\\n')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    lane, _ = _lane(tmp_path)
    lane["capture_sequence"] = "movement_sequence"
    for frame_spec in lane["frames"]:
        frame_label = frame_spec["label"]
        lane["required_current_frame_paths"][frame_label] = {"path": str(tmp_path / "generated" / "current" / f"{frame_label}.png")}
        lane["required_baseline_frame_paths"][frame_label] = {"path": str(tmp_path / "generated" / "baseline" / f"{frame_label}.png")}
    lane["capture_command"] = [
        sys.executable,
        str(capture_script),
        "--out-png",
        "{out_png}",
        "--out-metadata",
        "{out_metadata}",
        "--side",
        "{side}",
        "--frame",
        "{frame}",
    ]
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("GODOT_CANONICAL_GRID_CAPTURE") == 2
    assert (tmp_path / "generated" / "current" / "00_before.png").exists()
    assert (tmp_path / "generated" / "current" / "move_080.png").exists()
    assert (tmp_path / "generated" / "current" / "99_after.png").exists()
    assert (tmp_path / "out" / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.png").exists()


def test_canonical_grid_runner_treats_frame_paths_as_destinations_when_running_captures(tmp_path: Path) -> None:
    capture_script = tmp_path / "fake_capture_destination_writer.py"
    capture_script.write_text(
        "\n".join(
            [
                "import argparse, json",
                "from pathlib import Path",
                "from PIL import Image",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--out-png', required=True)",
                "parser.add_argument('--out-metadata', required=True)",
                "parser.add_argument('--side', required=True)",
                "parser.add_argument('--frame', required=True)",
                "args, _ = parser.parse_known_args()",
                "Path(args.out_png).parent.mkdir(parents=True, exist_ok=True)",
                "base = sum(ord(ch) for ch in args.side + args.frame) % 200",
                    "Image.new('RGB', (1600, 900), (base, (base + 60) % 255, (base + 120) % 255)).save(args.out_png)",
                "meta = {",
                    "  'ascii_enabled': True, 'fullscreen': True, 'view_mode': 'perspective',",
                "  'camera_position': [10.0, 20.0, 30.0], 'camera_yaw': 15.0,",
                "  'camera_pitch': -10.0, 'camera_roll': 0.0, 'camera_fov': 60.0,",
                "  'projection_params': {'near': 0.05, 'far': 400.0},",
                "  'player_position': [1.0, 2.0, 3.0], 'player_yaw': 90.0,",
                "  'npc_positions': [[5.0, 0.0, 5.0]],",
                "  'airplane_position': [20.0, 40.0, 60.0], 'airplane_yaw': 180.0,",
                "  'world_time': 22.0, 'night_state': True,",
                "  'feature_flags': ['ascii', 'foliage_cards', 'water_current_package']",
                "}",
                "Path(args.out_metadata).write_text(json.dumps(meta) + '\\n')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    lane, _ = _lane(tmp_path)
    for frame_spec in lane["frames"]:
        frame_label = frame_spec["label"]
        lane["required_current_frame_paths"][frame_label] = {"path": str(tmp_path / "destinations" / "current" / f"{frame_label}.png")}
        lane["required_baseline_frame_paths"][frame_label] = {"path": str(tmp_path / "destinations" / "baseline" / f"{frame_label}.png")}
    lane["capture_command"] = [
        sys.executable,
            str(capture_script),
            "--out-png",
            "{out_png}",
            "--out-metadata",
            "{out_metadata}",
            "--side",
            "{side}",
            "--frame",
            "{frame}",
        ]
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "destinations" / "current" / "00_before.png").exists()
    assert (tmp_path / "destinations" / "baseline" / "99_after.png").exists()
    assert (tmp_path / "out" / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.png").exists()


def test_canonical_grid_runner_expands_out_dir_frame_destinations(tmp_path: Path) -> None:
    capture_script = tmp_path / "fake_capture_out_dir_writer.py"
    capture_script.write_text(
        "\n".join(
            [
                "import argparse, json",
                "from pathlib import Path",
                "from PIL import Image",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--out-png', required=True)",
                "parser.add_argument('--out-metadata', required=True)",
                "parser.add_argument('--side', required=True)",
                "parser.add_argument('--frame', required=True)",
                "args, _ = parser.parse_known_args()",
                "Path(args.out_png).parent.mkdir(parents=True, exist_ok=True)",
                "base = sum(ord(ch) for ch in args.side + args.frame) % 200",
                    "Image.new('RGB', (1600, 900), (base, (base + 70) % 255, (base + 130) % 255)).save(args.out_png)",
                "meta = {",
                    "  'ascii_enabled': True, 'fullscreen': True, 'view_mode': 'perspective',",
                "  'camera_position': [10.0, 20.0, 30.0], 'camera_yaw': 15.0,",
                "  'camera_pitch': -10.0, 'camera_roll': 0.0, 'camera_fov': 60.0,",
                "  'projection_params': {'near': 0.05, 'far': 400.0},",
                "  'player_position': [1.0, 2.0, 3.0], 'player_yaw': 90.0,",
                "  'npc_positions': [[5.0, 0.0, 5.0]],",
                "  'airplane_position': [20.0, 40.0, 60.0], 'airplane_yaw': 180.0,",
                "  'world_time': 22.0, 'night_state': True,",
                "  'feature_flags': ['ascii', 'foliage_cards', 'water_current_package']",
                "}",
                "Path(args.out_metadata).write_text(json.dumps(meta) + '\\n')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    lane, _ = _lane(tmp_path)
    for frame_spec in lane["frames"]:
        frame_label = frame_spec["label"]
        lane["required_current_frame_paths"][frame_label] = {"path": "{out_dir}/frames/current/" + frame_label + ".png"}
        lane["required_baseline_frame_paths"][frame_label] = {"path": "{out_dir}/frames/baseline/" + frame_label + ".png"}
    lane["capture_command"] = [
        sys.executable,
        str(capture_script),
        "--out-png",
        "{out_png}",
        "--out-metadata",
        "{out_metadata}",
        "--side",
        "{side}",
        "--frame",
        "{frame}",
    ]
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "frames" / "current" / "00_before.png").exists()
    assert (tmp_path / "out" / "frames" / "baseline" / "99_after.png").exists()


def test_canonical_grid_runner_requires_preexisting_inputs_when_capture_disabled(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    lane["required_current_frame_paths"]["00_before"] = {"path": str(tmp_path / "missing" / "current_00.png")}
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file, run_captures=False)
    assert result.returncode == 2
    assert "pre-existing frame input missing" in result.stderr


def test_canonical_grid_runner_derives_actual_godot_capture_command_from_manifest(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    lane.pop("capture_command", None)
    command = GRID_RUNNER._capture_command_for_frame(lane, "current", "00_before", lane["required_current_frame_paths"]["00_before"])
    assert str(ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py") in command
    assert "--camera-position" in command
    assert command[command.index("--camera-position") + 1] == json.dumps(lane["camera_position"])
    assert "--player-position" in command
    assert command[command.index("--player-position") + 1] == json.dumps(lane["player_position"])
    assert "--feature-flags" in command
    assert command[command.index("--feature-flags") + 1] == json.dumps(lane["feature_flags"])
    assert "--fullscreen" in command
    assert "--capture-root" in command
    assert command[command.index("--capture-root") + 1] == "{out_dir}/captures"
    assert "--colyseus-dir" in command
    assert (
        command[command.index("--colyseus-dir") + 1]
        == "{shared_current_colyseus_dir}"
    )


def test_canonical_grid_runner_requires_actual_readback_metadata_for_actual_godot() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    # A valid RD handle never proves compositor output. The gate requires the
    # actual PNG and its source hash instead of the old false readback label.
    assert "final_cell_paint_state is not readback_ok" not in source
    assert "output PNG is not the recorded runtime source frame" in source
    assert "ansi_size readback metadata is missing" in source
    assert "cell_dims readback metadata is missing" in source
    assert "viewport_size readback metadata is missing" in source
    assert "display readback metadata is missing" in source
    assert "foliage_mask_state is not sampled" in source


def test_actual_godot_adapter_forwards_manifest_pose_to_headed_driver() -> None:
    adapter_source = (ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py").read_text(encoding="utf-8")
    assert '"--exact-player-position",' in adapter_source
    assert '"--exact-camera-position",' in adapter_source
    assert '"--exact-camera-yaw",' in adapter_source
    assert '"--exact-camera-pitch",' in adapter_source
    assert '"--exact-camera-projection",' in adapter_source
    assert '"--exact-camera-fov",' in adapter_source
    assert '"--single-frame",' in adapter_source
    assert '"--single-frame-label",' in adapter_source
    assert "return (_frame_file(args.frame),)" in adapter_source
    assert "MOVEMENT_SEQUENCE_FRAMES" in adapter_source
    assert "FRAME_LABEL_RE" in adapter_source
    assert 'choices=sorted(FRAME_FILE)' not in adapter_source
    assert '"final_cell_paint_state": frame_final_meta.get(' in adapter_source
    assert 'receipt.get("final_cell_paint_state")' in adapter_source
    assert '"foliage_mask_state": frame_final_meta.get(' in adapter_source
    assert '"ansi_size": frame_final_meta.get(' in adapter_source
    assert 'camera_mode = args.view_mode' in adapter_source
    assert 'capture_leaf = args.frame if args.capture_sequence == "single_frame" else args.capture_sequence' in adapter_source
    assert "/ args.side / capture_leaf / signature_hash[:16]" in adapter_source
    assert 'if frame_label == "move_080":' in adapter_source
    headed_source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text(encoding="utf-8")
    assert "--single-frame-label" in headed_source
    assert "--fl4408-single-frame=true" in headed_source
    assert 'default=str(ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd")' in headed_source
    assert "--fl4359-headed-movement-run" not in headed_source
    main_source = (ROOT / "godot_project" / "scripts" / "Main.gd").read_text(encoding="utf-8")
    assert "--fl4359-headed-movement-run" not in main_source
    assert "func _run_fl4359_headed_movement_run" not in main_source
    assert 'choices=["00_before", "move_080", "99_after"]' not in headed_source
    driver_source = (ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd").read_text(encoding="utf-8")
    assert "--fl4408-exact-player-position=" in driver_source
    assert "--fl4408-exact-camera-position=" in driver_source
    assert "--fl4408-single-frame=" in driver_source
    assert "func _capture_single_frame" in driver_source
    assert "camera.global_position = _exact_camera_position" in driver_source
    assert "player.global_position = _exact_player_position" in driver_source
    assert "func _exact_render_pose_check(final_meta: Dictionary) -> Dictionary:" in driver_source
    assert 'final_meta.get("pixel_camera_capture_contract", {})' in driver_source
    assert '"exact_render_pose_mismatch"' in driver_source
    assert '"exact_render_pose": exact_render_pose' in driver_source
    assert "position_error <= 0.002" in driver_source
    assert "yaw_error <= 0.002" in driver_source
    assert "pitch_error <= 0.002" in driver_source
    assert "camera if _exact_camera_position_set else null" in driver_source
    assert '"player_move_080": _vec3(player_move_080)' in driver_source
    assert '"camera_move_080": _vec3(camera_move_080)' in driver_source
    assert '"airplane_move_080": _vec3(airplane_move_080)' in driver_source


def test_actual_godot_adapter_accepts_manifest_owned_static_frame_labels() -> None:
    adapter = GRID_RUNNER.ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py"
    spec = importlib.util.spec_from_file_location("fl4408_actual_godot_capture_adapter", adapter)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._frame_file("beam_expected_in_png") == "beam_expected_in_png.png"
    assert module._frame_file("foliage_canopy_wind_120") == "foliage_canopy_wind_120.png"


def test_actual_godot_adapter_uses_night_state_as_single_runtime_owner() -> None:
    features = json.dumps(["ascii", "night", "water_current_package"])
    assert GRID_ADAPTER._runtime_feature_csv(features, True) == "water_current_package,night"
    assert GRID_ADAPTER._runtime_feature_csv(features, False) == "water_current_package"


def test_baseline_legacy_capture_does_not_flip_readback() -> None:
    driver = (ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd").read_text(encoding="utf-8")
    legacy_body = driver.split("func _legacy_baseline_capture_image() -> Image:", 1)[1].split("\nfunc ", 1)[0]
    assert "root.get_texture()" in legacy_body
    assert ".get_image()" in legacy_body
    assert "flip_y" not in legacy_body
    current_body = driver.split("func _save_screenshot(", 1)[1].split("\nfunc ", 1)[0]
    assert 'effect.has_method("capture_pixel_camera_viewport_image")' in current_body
    assert 'if _capture_side == "current":' in current_body
    assert "PixelCamera capture owner missing" in current_body
    effect = (
        ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
    ).read_text(encoding="utf-8")
    capture_body = effect.split(
        "func capture_pixel_camera_viewport_image(viewport: Viewport) -> Image:", 1
    )[1].split("\nfunc ", 1)[0]
    normalize_body = effect.split(
        "func normalize_pixel_camera_capture_image(image: Image) -> Image:", 1
    )[1].split("\nfunc ", 1)[0]
    assert "viewport.get_texture()" in capture_body
    assert "texture.get_image()" in capture_body
    assert "normalize_pixel_camera_capture_image(raw_image)" in capture_body
    assert "image.duplicate()" in normalize_body
    assert "flip_y" not in capture_body
    assert "flip_y" not in normalize_body


def test_canonical_grid_runner_accepts_nested_pose_manifest_shape(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    nested = {
        key: lane[key]
        for key in (
            "id",
            "bug_refs",
            "baseline",
            "world_time",
            "night_state",
            "frames",
            "required_current_frame_paths",
            "required_baseline_frame_paths",
            "expected_runtime_source_files",
        )
    }
    nested["camera_pose"] = {
        "projection": lane["view_mode"],
        "position": lane["camera_position"],
        "yaw": lane["camera_yaw"],
        "pitch": lane["camera_pitch"],
        "roll": lane["camera_roll"],
        "fov": lane["camera_fov"],
        "projection_params": lane["projection_params"],
    }
    nested["instance_pose"] = {
        "player_position": lane["player_position"],
        "player_yaw": lane["player_yaw"],
        "npc_positions": lane["npc_positions"],
        "airplane_position": lane["airplane_position"],
        "airplane_yaw": lane["airplane_yaw"],
    }
    nested["required_features"] = lane["feature_flags"]
    # The nested pose alias remains accepted, while Baseline A v2 deliberately
    # keeps the route's feature admission as a top-level receipt-bound list.
    nested["feature_flags"] = lane["feature_flags"]
    result = _run_runner(tmp_path, {"lanes": [nested]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr


def test_canonical_grid_runner_rejects_shared_pose_package_in_canonical_matrix(tmp_path: Path) -> None:
    lane_a, _ = _lane(tmp_path)
    lane_b, _ = _lane(tmp_path)
    lane_b = json.loads(json.dumps(lane_b))
    lane_b["id"] = "blood_decal_alignment"
    lane_b["bug_refs"] = ["FL-4269"]
    for frame_label, entry in lane_b["required_current_frame_paths"].items():
        entry["path"] = str(tmp_path / "blood" / "current" / f"{frame_label}.png")
        _write_png(Path(entry["path"]), (120, 10, 10))
    for frame_label, entry in lane_b["required_baseline_frame_paths"].items():
        entry["path"] = str(tmp_path / "blood" / "baseline" / f"{frame_label}.png")
        _write_png(Path(entry["path"]), (10, 120, 10))
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane_a["expected_runtime_source_files"][0]
    result = _run_runner(
        tmp_path,
        {"lanes": [lane_a, lane_b]},
        commit=commit,
        runtime_file=runtime_file,
        require_canonical_lanes=True,
    )
    assert result.returncode == 2
    assert "canonical frames share the same pose package" in result.stderr


def test_canonical_grid_runner_accepts_frame_level_pair_specs(tmp_path: Path) -> None:
    lane, _ = _lane(tmp_path)
    custom_label = "foliage_canopy_wind_120"
    current_png = tmp_path / "current" / f"{custom_label}.png"
    baseline_png = tmp_path / "baseline_a" / f"{custom_label}.png"
    _write_png(current_png, (5, 90, 20))
    _write_png(baseline_png, (90, 5, 20))
    custom_meta = _meta()
    custom_meta["camera_position"] = [value + 7.5 for value in lane["camera_position"]]
    custom_meta["camera_yaw"] = lane["camera_yaw"] + 95.0
    custom_meta["player_position"] = [value + 6.0 for value in lane["player_position"]]
    lane["frames"] = [
        {
            "label": "00_before",
            "route_tick": 30,
            "baseline_project": "baseline_a",
            "camera_pose": {"position": lane["camera_position"], "yaw": lane["camera_yaw"]},
            "instance_pose": {"player_position": lane["player_position"]},
        },
        {
            "label": custom_label,
            "route_tick": 90,
            "baseline_project": "baseline_a",
            "camera_pose": {"position": custom_meta["camera_position"], "yaw": custom_meta["camera_yaw"]},
            "instance_pose": {"player_position": custom_meta["player_position"]},
        },
    ]
    lane["required_current_frame_paths"] = {
        "00_before": lane["required_current_frame_paths"]["00_before"],
        custom_label: _frame_entry(current_png, custom_meta),
    }
    lane["required_baseline_frame_paths"] = {
        "00_before": lane["required_baseline_frame_paths"]["00_before"],
        custom_label: _frame_entry(baseline_png, custom_meta),
    }
    commit, _ = _runtime_commit_and_file()
    runtime_file = lane["expected_runtime_source_files"][0]
    result = _run_runner(tmp_path, {"lanes": [lane]}, commit=commit, runtime_file=runtime_file)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((tmp_path / "out" / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.manifest.json").read_text(encoding="utf-8"))
    custom_panels = [panel for panel in receipt["panels"] if panel["frame"] == custom_label]
    assert {panel["baseline"] for panel in custom_panels} == {"baseline_a"}
    assert custom_panels[0]["expected_state"]["camera_position"] == custom_meta["camera_position"]

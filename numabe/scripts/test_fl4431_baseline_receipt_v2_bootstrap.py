#!/usr/bin/env python3
"""Destructive tests for the FL-4431 Baseline receipt-v2 bootstrap.

The suite does not launch Godot.  It replaces the existing capture owners at
their process boundaries, makes them write realistic receipts and PNG files,
then proves the bootstrap invokes those owners, derives its facts from their
outputs, and refuses the defect shapes that could fabricate a promotable
receipt.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "run_fl4431_baseline_receipt_v2_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("fl4431_baseline_v2_bootstrap", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BOOTSTRAP
SPEC.loader.exec_module(BOOTSTRAP)

BUILDER_PATH = ROOT / "scripts" / "build_fl4431_baseline_receipt_v2.py"
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "fl4431_baseline_v2_builder_for_bootstrap_tests",
    BUILDER_PATH,
)
assert BUILDER_SPEC is not None and BUILDER_SPEC.loader is not None
REAL_BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
sys.modules[BUILDER_SPEC.name] = REAL_BUILDER
BUILDER_SPEC.loader.exec_module(REAL_BUILDER)


PNG_BYTES = BOOTSTRAP.PNG_SIGNATURE + b"bootstrap-fixture"
SERVER_COMMIT = "b" * 40
SERVER_FINGERPRINT = "f" * 64
BASELINE_COMMIT = "a" * 40
BASELINE_TREE = "c" * 40
PROOF_COMMIT = "d" * 40
PROOF_TREE = "e" * 40
FLIGHT_INTENT = {
    "flight": {"throttle": 0.37, "pitch": 0.0, "roll": 0.35, "yaw": 0.0},
    "fire": False,
}


def _write_json(path: Path, value: Any) -> None:
    """Write one test-owned source receipt used by the real normalizer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_png(path: Path) -> None:
    """Write enough real PNG signature bytes to defeat extension-only checks."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES)


def _preflight(tmp_path: Path) -> Any:
    """Create the immutable identities injected after the real preflight seam."""

    baseline = tmp_path / "baseline"
    (baseline / "godot_project").mkdir(parents=True)
    manifest = json.loads(BOOTSTRAP.LANE_MANIFEST.read_text(encoding="utf-8"))
    lane = BOOTSTRAP._find_night_lane(manifest)
    return BOOTSTRAP.Preflight(
        predecessor={
            "schema": BOOTSTRAP.PREDECESSOR_SCHEMA,
            "source_root": str(baseline),
            "source_commit": BASELINE_COMMIT,
            "features": [
                "perspective mode",
                "server-premounted airborne airplane",
                "terrain",
                "water",
            ],
        },
        baseline_root=baseline,
        baseline_commit=BASELINE_COMMIT,
        baseline_tree=BASELINE_TREE,
        proof_commit=PROOF_COMMIT,
        proof_tree=PROOF_TREE,
        manifest=manifest,
        night_lane=lane,
    )


class FakePackage(ModuleType):
    """Existing moving capture owner double that writes a real receipt surface."""

    def __init__(self, *, receipt_nonce: str = "first") -> None:
        super().__init__("fake_acceptance_package")
        self.calls: list[dict[str, Any]] = []
        self.receipt_nonce = receipt_nonce

    def run_capture_arm(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        out_dir = Path(kwargs["out_dir"])
        samples: list[dict[str, Any]] = []
        for index in range(int(kwargs["visual_limit"])):
            tick = 30 + index * int(kwargs["visual_stride"])
            samples.append(
                {
                    "route_tick": tick,
                    "authority_server_tick": 1000 + tick,
                    "last_applied_flight_intent_tick": 1000,
                    "flight_intent_epoch": 4,
                    "active_media_frame_index": index,
                    "player_position": {"x": 10.0 + index, "y": 80.0, "z": 20.0},
                    "player_yaw": 0.2 + index * 0.01,
                    "camera_position": {"x": 4.0 + index, "y": 96.0, "z": 12.0},
                    "camera_yaw": 0.3 + index * 0.01,
                    "airplane_position": {"x": 11.0 + index, "y": 80.0, "z": 21.0},
                    "airplane_yaw": 0.4 + index * 0.01,
                }
            )
            _write_png(out_dir / f"fl4359_perspective_frame_{tick:04d}.png")
        receipt = {
            "pass": True,
            "receipt_nonce": self.receipt_nonce,
            "route_identity": BOOTSTRAP.NIGHT_ROUTE_IDENTITY,
            "route_tick_zero_kind": BOOTSTRAP.NIGHT_TICK_ZERO_KIND,
            "perspective_forced": True,
            "first_active_flight_intent": copy.deepcopy(FLIGHT_INTENT),
            "visual_frame_samples": samples,
            "server_source_root": str(ROOT / "colyseus_server"),
            "server_source_dir": str(ROOT / "colyseus_server"),
            "server_git_head": SERVER_COMMIT,
            "server_runtime_scope_fingerprint": SERVER_FINGERPRINT,
        }
        _write_json(out_dir / "receipt.json", receipt)
        command = [
            sys.executable,
            "scripts/run_fl4265_airplane_proof.py",
            "--godot-project",
            str(kwargs["project"]),
            "--port",
            str(kwargs["port"]),
            "--extra-user-arg=--fl4408-day-cycle-time=0.5",
        ]
        return {"returncode": 0, "cmd": command}


class FakeGrid(ModuleType):
    """Tracked grid owner double that proves both command functions were called."""

    def __init__(self) -> None:
        super().__init__("fake_grid")
        self.default_calls: list[tuple[str, str]] = []
        self.template_calls: list[dict[str, str]] = []

    def _default_actual_godot_capture_command(
        self,
        lane: dict[str, Any],
        label: str,
    ) -> list[str]:
        self.default_calls.append((str(lane["id"]), label))
        return [
            "night-adapter",
            "--out-png",
            "{out_png}",
            "--out-metadata",
            "{out_metadata}",
            "--project",
            "{project}",
        ]

    def _command_from_template(
        self,
        template: list[str],
        values: dict[str, str],
    ) -> list[str]:
        self.template_calls.append(dict(values))
        return [part.format(**values) for part in template]


class FakeRunner:
    """Process boundary double for the night adapter and build identity reads."""

    def __init__(self, *, fail_night: bool = False, omit_label: str = "") -> None:
        self.commands: list[list[str]] = []
        self.fail_night = fail_night
        self.omit_label = omit_label

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command and command[0] == "night-adapter":
            if self.fail_night:
                return subprocess.CompletedProcess(command, 23, "", "night adapter failed")
            out_png = Path(command[command.index("--out-png") + 1])
            out_dir = out_png.parent
            run_dir = out_dir / "captures" / "night-process"
            temporal_id = "4567-987654321"
            rows: list[dict[str, Any]] = []
            for index, label in enumerate(BOOTSTRAP.NIGHT_LABELS):
                row = {
                    "label": label,
                    "frame_index": index,
                    "route_tick": BOOTSTRAP.NIGHT_TICKS[index],
                    "authority_server_tick": 2000 + BOOTSTRAP.NIGHT_TICKS[index],
                    "last_applied_flight_intent_tick": 2000,
                    "flight_intent_epoch": 7,
                    "temporal_capture_id": temporal_id,
                    "png_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
                    "png_captured_in_same_process": True,
                    "beam_phase_offset_target": BOOTSTRAP.NIGHT_OFFSETS[index],
                    "beam_phase_offset_measured": BOOTSTRAP.NIGHT_OFFSETS[index],
                    "player_position": {"x": 70.0 + index, "y": 81.0, "z": 25.0},
                    "player_yaw": 0.1 + index * 0.01,
                    "camera_position": {"x": 72.0 + index, "y": 95.0, "z": 20.0},
                    "camera_yaw": 0.2 + index * 0.01,
                    "airplane_position": {"x": 71.0 + index, "y": 82.0, "z": 24.0},
                    "airplane_yaw": 0.3 + index * 0.01,
                }
                rows.append(row)
            receipt = {
                "pass": True,
                "night_airborne_temporal_sequence": True,
                "route_identity": BOOTSTRAP.NIGHT_ROUTE_IDENTITY,
                "first_active_flight_intent": copy.deepcopy(FLIGHT_INTENT),
                "visual_route_target_ticks": list(BOOTSTRAP.NIGHT_TICKS),
                "temporal_capture_id": temporal_id,
                "visual_frame_samples": rows,
            }
            receipt_path = run_dir / "receipt.json"
            _write_json(receipt_path, receipt)
            _write_json(
                run_dir / "adapter_command.json",
                {
                    "command": [
                        sys.executable,
                        "scripts/run_fl4359_headed_movement.py",
                        "--port",
                        "3581",
                    ],
                    "returncode": 0,
                    "stdout_tail": "",
                    "stderr_tail": "",
                },
            )
            for index, label in enumerate(BOOTSTRAP.NIGHT_LABELS):
                if label == self.omit_label:
                    continue
                png = out_dir / f"{label}.png"
                metadata_path = png.with_suffix(".png.json")
                _write_png(png)
                _write_json(
                    metadata_path,
                    {
                        "receipt_pass": True,
                        "temporal_continuous_run": True,
                        "temporal_capture_id": temporal_id,
                        "route_tick": BOOTSTRAP.NIGHT_TICKS[index],
                        "beam_phase_offset_target": BOOTSTRAP.NIGHT_OFFSETS[index],
                        "receipt_path": str(receipt_path),
                        "view_mode": "perspective",
                        "camera_fov": 60.0,
                        "camera_pitch": -0.52,
                        "camera_roll": 0.0,
                        "projection_params": {"near": 0.05, "far": 750.0},
                        "player_yaw": rows[index]["player_yaw"],
                        "camera_yaw": rows[index]["camera_yaw"],
                        "airplane_yaw": rows[index]["airplane_yaw"],
                        "requested_capture_resolution": "3840x2160",
                        "capture_resolution": "3600x2252",
                        "ansi_size": {"x": 450, "y": 140},
                        "fullscreen": True,
                        "ascii_enabled": True,
                        "world_time": 22.5,
                        "server_commit": SERVER_COMMIT,
                        "server_source_root": str(ROOT / "colyseus_server"),
                        "server_runtime_scope_fingerprint": SERVER_FINGERPRINT,
                    },
                )
            return subprocess.CompletedProcess(command, 0, "night complete", "")
        return subprocess.CompletedProcess(command, 0, "fixture", "")


class FakeBuilder(ModuleType):
    """Builder double that runs the real capture validator before promotion."""

    SCHEMA = REAL_BUILDER.SCHEMA

    def __init__(self) -> None:
        super().__init__("fake_builder")
        self.calls: list[dict[str, Any]] = []

    def create_receipt(self, **kwargs: Any) -> dict[str, Any]:
        capture = json.loads(Path(kwargs["capture_receipt_path"]).read_text())
        REAL_BUILDER._validate_capture_input(capture, "capture_input_document")
        self.calls.append(dict(kwargs))
        receipt = {"schema": self.SCHEMA, "capture_id": capture["capture_id"]}
        _write_json(Path(kwargs["output_path"]), receipt)
        return receipt


def _config(tmp_path: Path, suffix: str = "") -> Any:
    """Create nonexisting output paths for one isolated bootstrap."""

    root = tmp_path / f"run{suffix}"
    root.mkdir()
    predecessor = root / "baseline-v1.json"
    designation = root / "designation.json"
    _write_json(predecessor, {"schema": BOOTSTRAP.PREDECESSOR_SCHEMA})
    _write_json(designation, {"fixture": True})
    return BOOTSTRAP.BootstrapConfig(
        predecessor_receipt=predecessor,
        designation_record=designation,
        output_receipt=root / "baseline-v2.json",
        capture_receipt=root / "capture.json",
        capture_root=root / "capture-files",
        moving_frames=240,
        moving_visual_stride=30,
        moving_visual_limit=3,
    )


def _build_identity(tmp_path: Path) -> dict[str, Any]:
    """Create live files required by the real builder's identity validator."""

    binary = tmp_path / "godot"
    marker = tmp_path / "project_metadata.cfg"
    binary.write_bytes(b"godot-fixture")
    marker.write_bytes(b"import-fixture")
    return {
        "execution_kind": "native-godot-baseline-capture",
        "godot_requested_path": str(binary.resolve()),
        "godot_resolved_binary": BOOTSTRAP._path_ref(binary),
        "godot_version": "4.4.fixture",
        "godot_binary_format": "Mach-O fixture",
        "imported_source_commit": BASELINE_COMMIT,
        "imported_source_tree": BASELINE_TREE,
        "project_import_marker": BOOTSTRAP._path_ref(marker),
    }


def _install_safe_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Any:
    """Replace only Git and Godot binary probes; capture owners remain exercised."""

    preflight = _preflight(tmp_path)
    monkeypatch.setattr(BOOTSTRAP, "_preflight", lambda _config, _runner: preflight)
    monkeypatch.setattr(
        BOOTSTRAP,
        "_resolve_godot_identity",
        lambda _preflight, _runner: _build_identity(tmp_path),
    )
    monkeypatch.setattr(BOOTSTRAP, "_assert_clean_checkout", lambda *_args: None)
    monkeypatch.setattr(BOOTSTRAP, "_assert_tool_bytes_match_head", lambda *_args: None)
    return preflight


def test_bootstrap_invokes_both_real_owner_seams_and_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green bootstrap must execute moving, night, normalization, and v2 creation."""

    preflight = _install_safe_boundaries(monkeypatch, tmp_path)
    config = _config(tmp_path)
    package = FakePackage()
    grid = FakeGrid()
    runner = FakeRunner()
    builder = FakeBuilder()

    receipt = BOOTSTRAP.bootstrap(
        config,
        runner=runner,
        package_module=package,
        grid_module=grid,
        builder_module=builder,
        created_at_utc="2026-07-26T12:00:00+00:00",
    )

    assert receipt["schema"] == REAL_BUILDER.SCHEMA
    assert len(package.calls) == 1
    assert package.calls[0]["project"] == preflight.baseline_root / "godot_project"
    assert grid.default_calls == [(BOOTSTRAP.NIGHT_LANE_ID, "beam_t0")]
    assert len(grid.template_calls) == 1
    assert [command[0] for command in runner.commands] == ["night-adapter"]
    assert len(builder.calls) == 1

    capture = json.loads(config.capture_receipt.read_text())
    assert capture["schema"] == BOOTSTRAP.CAPTURE_SCHEMA
    assert capture["capture_classification"] == "non_promotable_diagnostic"
    assert capture["route"]["target_ticks"] == [30, 150, 270]
    assert capture["route"]["tick_stride"] == 120
    assert capture["route"]["physical_input_action"] == json.dumps(
        FLIGHT_INTENT,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert capture["argument_vectors"]["day"][:2] == [
        sys.executable,
        "scripts/run_fl4265_airplane_proof.py",
    ]
    assert capture["argument_vectors"]["night"][0] == "night-adapter"


def test_bootstrap_command_vectors_and_frames_derive_from_owner_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commands, identities, poses, and frame attachments must not be invented."""

    _install_safe_boundaries(monkeypatch, tmp_path)
    config = _config(tmp_path)
    package = FakePackage()
    grid = FakeGrid()
    runner = FakeRunner()
    builder = FakeBuilder()
    BOOTSTRAP.bootstrap(
        config,
        runner=runner,
        package_module=package,
        grid_module=grid,
        builder_module=builder,
        created_at_utc="2026-07-26T12:00:00+00:00",
    )
    capture = json.loads(config.capture_receipt.read_text())

    assert capture["argument_vectors"]["day"][1] == "scripts/run_fl4265_airplane_proof.py"
    assert capture["argument_vectors"]["night"][0] == "night-adapter"
    assert capture["route"]["poses"][0]["player_position"] == [70.0, 81.0, 25.0]
    assert capture["route"]["poses"][2]["airplane_position"] == [73.0, 82.0, 24.0]
    assert capture["dimensions"] == {
        "requested_width": 3840,
        "requested_height": 2160,
        "actual_width": 3600,
        "actual_height": 2252,
        "ansi_width": 450,
        "ansi_height": 140,
        "fullscreen": True,
        "ascii": True,
    }
    night_frames = [
        frame
        for frame in capture["source_frames"]
        if frame["lane_id"] == BOOTSTRAP.NIGHT_LANE_ID
    ]
    assert [frame["label"] for frame in night_frames] == list(BOOTSTRAP.NIGHT_LABELS)
    for frame in capture["source_frames"]:
        metadata = json.loads(Path(frame["metadata"]["path"]).read_text())
        truth = json.loads(Path(frame["runtime_truth"]["path"]).read_text())
        assert metadata["capture_id"] == capture["capture_id"]
        assert truth["process_identity"] == frame["process_identity"]
        assert Path(frame["png"]["path"]).read_bytes().startswith(BOOTSTRAP.PNG_SIGNATURE)


def test_night_subprocess_failure_prevents_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonzero real adapter command can never reach the v2 builder."""

    _install_safe_boundaries(monkeypatch, tmp_path)
    config = _config(tmp_path)
    builder = FakeBuilder()
    with pytest.raises(BOOTSTRAP.BootstrapError, match="subprocess failed with rc=23"):
        BOOTSTRAP.bootstrap(
            config,
            runner=FakeRunner(fail_night=True),
            package_module=FakePackage(),
            grid_module=FakeGrid(),
            builder_module=builder,
        )
    assert builder.calls == []
    assert not config.capture_receipt.exists()
    assert not config.output_receipt.exists()


def test_missing_night_png_prevents_capture_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt without every real PNG attachment remains non-promotable."""

    _install_safe_boundaries(monkeypatch, tmp_path)
    config = _config(tmp_path)
    builder = FakeBuilder()
    with pytest.raises(BOOTSTRAP.BootstrapError, match="beam_t1 PNG is missing"):
        BOOTSTRAP.bootstrap(
            config,
            runner=FakeRunner(omit_label="beam_t1"),
            package_module=FakePackage(),
            grid_module=FakeGrid(),
            builder_module=builder,
        )
    assert builder.calls == []


def test_stale_absolute_phase_key_is_rejected() -> None:
    """The retired beam_phase_target spelling must fail at any nesting depth."""

    with pytest.raises(BOOTSTRAP.BootstrapError, match="retired beam_phase_target"):
        BOOTSTRAP._scan_stale_phase_key(
            {"lanes": [{"frames": [{"beam_phase_target": 1.0}]}]}
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("route_tick", 90, "route ticks"),
        ("beam_phase_offset_target", 1.0, "phase offset"),
    ],
)
def test_night_manifest_tick_and_offset_mutations_fail(
    field: str,
    replacement: Any,
    message: str,
) -> None:
    """The three exact route ticks and 120-degree phase spacing are load-bearing."""

    manifest = json.loads(BOOTSTRAP.LANE_MANIFEST.read_text(encoding="utf-8"))
    lane = BOOTSTRAP._find_night_lane(manifest)
    lane["frames"][1][field] = replacement
    with pytest.raises(BOOTSTRAP.BootstrapError, match=message):
        BOOTSTRAP._validate_night_lane(lane)


def test_night_runtime_phase_mutation_fails() -> None:
    """A real receipt with a wrong measured phase cannot inherit manifest trust."""

    rows = [
        {
            "label": label,
            "route_tick": BOOTSTRAP.NIGHT_TICKS[index],
            "beam_phase_offset_target": BOOTSTRAP.NIGHT_OFFSETS[index],
            "beam_phase_offset_measured": BOOTSTRAP.NIGHT_OFFSETS[index],
        }
        for index, label in enumerate(BOOTSTRAP.NIGHT_LABELS)
    ]
    rows[2]["beam_phase_offset_measured"] = 0.25
    with pytest.raises(BOOTSTRAP.BootstrapError, match="runtime beam_phase_offset_measured"):
        BOOTSTRAP._validate_night_runtime_rows({"visual_frame_samples": rows})


def test_dirty_checkout_and_changed_tool_each_fail_closed(tmp_path: Path) -> None:
    """Both uncommitted source and a changed executable tool stop prelaunch."""

    class ScriptedRunner:
        def __init__(self, outputs: list[str]) -> None:
            self.outputs = outputs

        def __call__(self, command: list[str], **_kwargs: Any) -> Any:
            return subprocess.CompletedProcess(command, 0, self.outputs.pop(0), "")

    with pytest.raises(BOOTSTRAP.BootstrapError, match="must be completely clean"):
        BOOTSTRAP._assert_clean_checkout(
            ROOT,
            "proof-tool",
            ScriptedRunner([" M scripts/tool.py\n"]),
        )

    relative = "scripts/run_fl4431_baseline_receipt_v2_bootstrap.py"
    with pytest.raises(BOOTSTRAP.BootstrapError, match="differs from HEAD"):
        BOOTSTRAP._assert_tool_bytes_match_head(
            ROOT,
            relative,
            ScriptedRunner(["", "1" * 40, "2" * 40]),
        )


def test_capture_identity_changes_when_a_source_receipt_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copied identifier is forbidden; receipt bytes must own capture identity."""

    _install_safe_boundaries(monkeypatch, tmp_path)
    captures: list[dict[str, Any]] = []
    for index, nonce in enumerate(("alpha", "beta")):
        config = _config(tmp_path, suffix=str(index))
        BOOTSTRAP.bootstrap(
            config,
            runner=FakeRunner(),
            package_module=FakePackage(receipt_nonce=nonce),
            grid_module=FakeGrid(),
            builder_module=FakeBuilder(),
            created_at_utc="2026-07-26T12:00:00+00:00",
        )
        captures.append(json.loads(config.capture_receipt.read_text()))

    assert captures[0]["capture_id"] != captures[1]["capture_id"]
    assert (
        captures[0]["server_identity"]["server_process_identity"]
        != captures[1]["server_identity"]["server_process_identity"]
    )


def test_existing_outputs_and_capture_root_are_never_reused(
    tmp_path: Path,
) -> None:
    """A prior artifact directory cannot be mixed into a later designated receipt."""

    config = _config(tmp_path)
    config.capture_root.mkdir()
    with pytest.raises(BOOTSTRAP.BootstrapError, match="capture root must not exist"):
        BOOTSTRAP.bootstrap(config)


if __name__ == "__main__":
    # Running this file directly must execute the tests and preserve pytest's
    # nonzero exit status. This prevents the false-green empty-script failure
    # that previously affected source contracts in this repository.
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "pytest", str(Path(__file__)), "-q"])
    )

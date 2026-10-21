#!/usr/bin/env python3
"""Collect the current FL-4431 visual and clean-play runtime surfaces.

FL-4431 no longer depends on a historical renderer comparison.  This front
door therefore launches only the current checkout.  It keeps screen capture,
the nonvisual Product contribution attachment, and diagnostics-free frame-time
collection separate so a readback cannot contaminate the FPS result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GODOT_DEFAULT = "/opt/homebrew/bin/godot"
MOVING_CAPTURE_HARNESS = (
    ROOT / "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
)
FULL_GRID_ORACLE_RUNNER = ROOT / "scripts/run_fl4359_headed_movement.py"
FULL_GRID_ORACLE_PROFILE = "full_grid_temporal_oracle_v1"
SECONDARY_EFFECT_EVIDENCE_ARG = "--fl4431-secondary-effect-evidence"
PROBE_GEOMETRY_CUTOVER_DISABLE_ARG = "--no-fl4057-probe-geometry-cutover"
CURRENT_MOVING_FLIGHT_RECEIPT_KIND = "moving_current_flight_capture"
CURRENT_VISUAL_RUNTIME_ARGS = {
    "perspective": ("--fl4270-force-perspective",),
    "ortho": ("--fl4270-force-ortho",),
}
CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS = (
    "--fl4270-force-perspective",
    "--manual-environment-night",
    "--force-weather=clear",
    "--force-baseline-feature=night,lighthouse_light,bonfire_light,firefly_light",
    SECONDARY_EFFECT_EVIDENCE_ARG,
    "--fl4431-night-airborne-temporal-sequence",
    "--fl4431-night-temporal-labels=beam_t0,beam_t1,beam_t2",
    "--fl4431-night-temporal-phase-offsets=0.0,2.0943951023931953,4.1887902047863905",
)
CURRENT_NIGHT_TEMPORAL_TARGET_TICKS = (30, 150, 270)
CURRENT_NIGHT_TEMPORAL_LABELS = ("beam_t0", "beam_t1", "beam_t2")
CURRENT_SECONDARY_EFFECT_RUNTIME_ARGS = (
    "--fl4270-force-perspective",
    SECONDARY_EFFECT_EVIDENCE_ARG,
)
CURRENT_CLEAN_PLAY_RUNTIME_ARGS = {
    "perspective": ("--fl4270-force-perspective",),
    "ortho": ("--fl4270-force-ortho",),
}
CURRENT_PROJECTION_RECEIPT_MODES = {
    "perspective": ("true_perspective", 0),
    "ortho": ("ortho", 1),
}
MIN_CLEAN_PLAY_FPS = 60.0
CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES = 30
CLEAN_PLAY_MAX_WINDOW_MEAN_MS = 1000.0 / MIN_CLEAN_PLAY_FPS
CLEAN_PLAY_MAX_SINGLE_FRAME_MS = 33.333
CLEAN_PLAY_FLOAT_REL_TOLERANCE = 1.0e-6
CLEAN_PLAY_FLOAT_ABS_TOLERANCE = 1.0e-6
DEFAULT_ACCEPTANCE_MOVING_FRAMES = 600
DEFAULT_CLEAN_PLAY_ROUTE_TICKS = 240
DEFAULT_ACCEPTANCE_VISUAL_STRIDE = 60
DEFAULT_ACCEPTANCE_VISUAL_LIMIT = 10
DEFAULT_ACCEPTANCE_VISUAL_START = 0
DEFAULT_ACCEPTANCE_GODOT_TIMEOUT_SECONDS = 120
CANONICAL_PRESENTATION_SIZE = (3600, 2252)
CANONICAL_TARGET_SIZE = (3600, 2260)
CANONICAL_ANSI_SIZE = (180, 113)

REQUIRED_STATIC_CHECK_MARKERS = (
    "scripts/test_fl4359_acceptance_package.py",
    "scripts/test_fl4359_moving_camera_analyzer.py",
    "scripts/tests/test_fl4359_same_frame_oracle_contract.py",
    "scripts/test_fl4431_airborne_latch_deleted_contract.py",
    "scripts/test_fl4359_exact_key_analyzer.py",
    "scripts/test_fl4431_actor_quad_selection_contract.py",
    "scripts/test_fl4431_visual_effect_reachability_contract.py",
    "res://scripts/tests/test_fl4431_night_null_product_tuple.gd",
    "scripts/run_fl4265_airplane_proof.py",
    "res://render/api/ASCIICompositorEffect.gd",
    "res://tools/proofs/run_fl4265_airplane_dogfight_proof.gd",
    "git diff --check",
)


def rel(path: Path) -> str:
    """Return a repository-relative label when the path belongs to this tree."""

    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Run one bounded command and retain the tail needed for diagnosis."""

    started = time.time()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        stdout_tail = log_path.read_text(
            encoding="utf-8", errors="replace"
        )[-4000:]
    else:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        stdout_tail = proc.stdout[-4000:]
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "duration_seconds": round(time.time() - started, 3),
        "log": rel(log_path) if log_path is not None else "",
        "stdout_tail": stdout_tail,
    }


def capture_git_provenance() -> tuple[str, list[str]]:
    """Capture complete source identity without diagnostic-tail truncation."""

    head_process = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if head_process.returncode != 0:
        raise RuntimeError("git HEAD provenance capture failed")
    head_lines = head_process.stdout.splitlines()
    if len(head_lines) != 1 or not head_lines[0]:
        raise RuntimeError("git HEAD provenance was not exactly one line")

    status_process = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if status_process.returncode != 0:
        raise RuntimeError("git dirty-path provenance capture failed")
    return head_lines[0], status_process.stdout.splitlines()


def capture_runtime_scope_fingerprint(scope_path: Path) -> str:
    """Hash the exact tracked and untracked bytes one runtime arm launches.

    The child runner records this same digest in every arm receipt. Capturing
    it before launch binds all six arms to one source state, so an edit made
    between arms cannot produce a mixed package that appears self-consistent.
    """

    root_process = subprocess.run(
        ["git", "-C", str(scope_path), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if root_process.returncode != 0:
        return ""
    source_root = Path(root_process.stdout.strip()).resolve()
    try:
        scope = scope_path.resolve().relative_to(source_root)
    except ValueError:
        return ""
    diff_process = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "diff",
            "--binary",
            "HEAD",
            "--",
            str(scope),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    untracked_process = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            str(scope),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if diff_process.returncode != 0 or untracked_process.returncode != 0:
        return ""
    digest = hashlib.sha256()
    digest.update(diff_process.stdout)
    for relative in sorted(
        line for line in untracked_process.stdout.splitlines() if line
    ):
        if any(
            part in {".godot", ".run", "dist"}
            for part in Path(relative).parts
        ):
            continue
        path = source_root / relative
        if path.is_file():
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def expected_visual_route_ticks(
    *,
    visual_start: int,
    visual_stride: int,
    visual_limit: int,
) -> list[int]:
    """Return the exact authoritative route ticks that must own PNG bytes."""

    return [
        30 + max(0, visual_start) + index * visual_stride
        for index in range(visual_limit)
    ]


def arm_source_identity_ok(
    result: dict[str, Any],
    *,
    expected_head: str,
    expected_client_fingerprint: str,
    expected_server_fingerprint: str,
) -> bool:
    """Reject an arm launched from another source generation."""

    receipt = result.get("receipt", {})
    if not isinstance(receipt, dict):
        return False
    expected_values = (
        expected_head,
        expected_client_fingerprint,
        expected_server_fingerprint,
    )
    if any(
        not isinstance(value, str) or not value
        for value in expected_values
    ):
        return False
    return (
        receipt.get("git_head") == expected_head
        and receipt.get("client_git_head") == expected_head
        and receipt.get("server_git_head") == expected_head
        and receipt.get("git_identity_valid") is True
        and receipt.get("client_identity_valid") is True
        and receipt.get("server_identity_valid") is True
        and receipt.get("git_dirty_paths") == []
        and receipt.get("client_dirty_paths") == []
        and receipt.get("server_dirty_paths") == []
        and receipt.get("client_runtime_scope_fingerprint")
        == expected_client_fingerprint
        and receipt.get("server_runtime_scope_fingerprint")
        == expected_server_fingerprint
    )


def load_json(path: Path) -> dict[str, Any]:
    """Load a receipt, treating an absent receipt as explicit empty evidence."""

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def write_manifest(path: Path, results: dict[str, Any]) -> None:
    """Write the durable current-run receipt after every state transition."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


def static_checks(out_dir: Path, godot: str) -> list[dict[str, Any]]:
    """Run only checks that guard the retained current renderer and launcher."""

    checks = [
        [sys.executable, "scripts/test_fl4359_acceptance_package.py"],
        [sys.executable, "scripts/test_fl4359_moving_camera_analyzer.py"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "scripts/tests/test_fl4359_same_frame_oracle_contract.py",
        ],
        [
            sys.executable,
            "scripts/test_fl4431_airborne_latch_deleted_contract.py",
        ],
        [sys.executable, "scripts/test_fl4359_exact_key_analyzer.py"],
        [sys.executable, "scripts/test_fl4431_actor_quad_selection_contract.py"],
        [
            sys.executable,
            "scripts/test_fl4431_visual_effect_reachability_contract.py",
        ],
        [
            sys.executable,
            "-m",
            "py_compile",
            "scripts/run_fl4359_acceptance_package.py",
            "scripts/run_fl4265_airplane_proof.py",
        ],
        [
            godot,
            "--headless",
            "--path",
            "godot_project",
            # Keep the check inside this run's durable artifact namespace.
            # Godot's default user:// rotating logger can be unwritable in a
            # managed launcher even when the project itself parses correctly.
            "--log-file",
            str(out_dir / "logs" / "godot_static_check.log"),
            "--check-only",
            "--script",
            "res://render/api/ASCIICompositorEffect.gd",
        ],
        [
            godot,
            "--headless",
            "--path",
            "godot_project",
            # Parse the actual runtime harness through the same bounded logger.
            # A compositor-only check cannot catch a GDScript regression in the
            # script that owns projection selection and clean timing receipts.
            "--log-file",
            str(out_dir / "logs" / "godot_dogfight_check.log"),
            "--check-only",
            "--script",
            "res://tools/proofs/run_fl4265_airplane_dogfight_proof.gd",
        ],
        [
            godot,
            "--headless",
            "--path",
            "godot_project",
            "--log-file",
            str(out_dir / "logs" / "godot_night_tuple_check.log"),
            "--script",
            "res://scripts/tests/test_fl4431_night_null_product_tuple.gd",
        ],
        [
            "git",
            "diff",
            "--check",
            "--",
            "scripts/run_fl4359_acceptance_package.py",
            "scripts/test_fl4359_acceptance_package.py",
            "scripts/test_fl4359_moving_camera_analyzer.py",
            "scripts/tests/test_fl4359_same_frame_oracle_contract.py",
            "scripts/test_fl4431_airborne_latch_deleted_contract.py",
        ],
    ]
    results: list[dict[str, Any]] = []
    for index, command in enumerate(checks, start=1):
        results.append(
            run_cmd(
                command,
                log_path=out_dir / "logs" / f"static_{index:02d}.log",
            )
        )
    return results


def static_checks_ok(results: list[dict[str, Any]]) -> bool:
    """Require every retained check to execute and return success."""

    return bool(results) and all(row.get("returncode") == 0 for row in results)


def _append_owned_runtime_args(
    command: list[str],
    values: tuple[str, ...],
) -> None:
    """Append the immutable runtime arguments owned by one package arm."""

    for value in values:
        command.append(f"--extra-user-arg={value}")


def _runtime_args_from_command(command: list[str]) -> list[str]:
    """Recover the actual child user arguments for admission checks."""

    prefix = "--extra-user-arg="
    return [
        token.removeprefix(prefix)
        for token in command
        if token.startswith(prefix)
    ]


def run_current_visual_arm(
    *,
    project: Path,
    colyseus_dir: Path,
    out_dir: Path,
    port: int,
    frames: int,
    visual_stride: int,
    visual_limit: int,
    visual_start: int,
    godot_timeout: int,
    server_timeout: int,
    view_mode: str,
) -> dict[str, Any]:
    """Capture one explicit current projection without Product readback."""

    if view_mode not in CURRENT_VISUAL_RUNTIME_ARGS:
        raise ValueError(f"unknown visual view mode: {view_mode}")
    runtime_args = CURRENT_VISUAL_RUNTIME_ARGS[view_mode]

    command = [
        sys.executable,
        "scripts/run_fl4265_airplane_proof.py",
        "--godot-project",
        str(project),
        "--colyseus-dir",
        str(colyseus_dir),
        "--godot-script",
        str(MOVING_CAPTURE_HARNESS),
        "--out",
        str(out_dir),
        "--port",
        str(port),
        "--godot-timeout",
        str(godot_timeout),
        "--server-boot-timeout",
        str(server_timeout),
        "--fl4359-jitter-hold-frames",
        str(frames),
        "--fl4359-visual-capture-stride",
        str(visual_stride),
        "--fl4359-visual-capture-limit",
        str(visual_limit),
        "--fl4359-visual-capture-start-frame",
        str(visual_start),
    ]
    _append_owned_runtime_args(command, runtime_args)

    result = run_cmd(command, log_path=out_dir / "wrapper.log")
    receipt_path = out_dir / "receipt.json"
    frame_paths = sorted(
        str(path)
        for path in out_dir.glob("fl4359_perspective_frame_*.png")
    )
    result.update(
        {
            "arm": f"current_visual_{view_mode}",
            "renderer": "current",
            "view_mode": view_mode,
            "visual_evidence": True,
            "performance_evidence": False,
            "receipt_path": rel(receipt_path),
            "receipt": load_json(receipt_path),
            "frame_count": len(frame_paths),
            "frame_paths": frame_paths,
            "runtime_args": _runtime_args_from_command(command),
        }
    )
    return result


def run_current_night_temporal_arm(
    *,
    project: Path,
    colyseus_dir: Path,
    out_dir: Path,
    port: int,
    godot_timeout: int,
    server_timeout: int,
) -> dict[str, Any]:
    """Capture three exact current-night frames with direct effect facts.

    This arm is deliberately separate from clean-play timing. Product b62
    readback supplies the matching beam, bonfire, tower, terrain, water, and
    tree counts for each captured frame; that synchronization would stall the
    GPU queue and invalidate an FPS sample.
    """

    command = [
        sys.executable,
        "scripts/run_fl4265_airplane_proof.py",
        "--godot-project",
        str(project),
        "--colyseus-dir",
        str(colyseus_dir),
        "--godot-script",
        str(MOVING_CAPTURE_HARNESS),
        "--out",
        str(out_dir),
        "--port",
        str(port),
        "--godot-timeout",
        str(godot_timeout),
        "--server-boot-timeout",
        str(server_timeout),
        "--fl4359-visual-capture-stride",
        "120",
        "--fl4359-visual-capture-limit",
        "3",
        "--fl4359-visual-capture-start-frame",
        "0",
    ]
    _append_owned_runtime_args(
        command,
        CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS,
    )
    result = run_cmd(command, log_path=out_dir / "wrapper.log")
    receipt_path = out_dir / "receipt.json"
    frame_paths = sorted(
        str(path)
        for path in out_dir.glob("fl4359_perspective_frame_*.png")
    )
    result.update(
        {
            "arm": "current_night_temporal",
            "renderer": "current",
            "view_mode": "perspective",
            "visual_evidence": True,
            "temporal_evidence": True,
            "performance_evidence": False,
            "receipt_path": rel(receipt_path),
            "receipt": load_json(receipt_path),
            "frame_count": len(frame_paths),
            "frame_paths": frame_paths,
            "runtime_args": _runtime_args_from_command(command),
        }
    )
    return result


def run_current_clean_play_arm(
    *,
    project: Path,
    colyseus_dir: Path,
    out_dir: Path,
    port: int,
    route_ticks: int,
    godot_timeout: int,
    server_timeout: int,
    view_mode: str,
) -> dict[str, Any]:
    """Measure one explicit current projection without capture and readback."""

    if view_mode not in CURRENT_CLEAN_PLAY_RUNTIME_ARGS:
        raise ValueError(f"unknown clean-play view mode: {view_mode}")
    runtime_args = CURRENT_CLEAN_PLAY_RUNTIME_ARGS[view_mode]

    command = [
        sys.executable,
        "scripts/run_fl4265_airplane_proof.py",
        "--godot-project",
        str(project),
        "--colyseus-dir",
        str(colyseus_dir),
        "--godot-script",
        str(MOVING_CAPTURE_HARNESS),
        "--out",
        str(out_dir),
        "--port",
        str(port),
        "--godot-timeout",
        str(godot_timeout),
        "--server-boot-timeout",
        str(server_timeout),
        "--fl4359-clean-play-frames",
        str(route_ticks),
        "--fl4359-clean-play-no-capture",
    ]
    _append_owned_runtime_args(command, runtime_args)
    result = run_cmd(command, log_path=out_dir / "wrapper.log")
    receipt_path = out_dir / "receipt.json"
    result.update(
        {
            "arm": f"current_clean_play_{view_mode}",
            "renderer": "current",
            "view_mode": view_mode,
            "visual_evidence": False,
            "performance_evidence": True,
            "receipt_path": rel(receipt_path),
            "receipt": load_json(receipt_path),
            "runtime_args": _runtime_args_from_command(command),
        }
    )
    return result


def run_current_secondary_effect_arm(
    *,
    project: Path,
    colyseus_dir: Path,
    out_dir: Path,
    port: int,
    godot_timeout: int,
    server_timeout: int,
) -> dict[str, Any]:
    """Collect Product contribution in a separate nonvisual diagnostic arm."""

    command = [
        sys.executable,
        "scripts/run_fl4265_airplane_proof.py",
        "--godot-project",
        str(project),
        "--colyseus-dir",
        str(colyseus_dir),
        "--godot-script",
        str(MOVING_CAPTURE_HARNESS),
        "--out",
        str(out_dir),
        "--port",
        str(port),
        "--godot-timeout",
        str(godot_timeout),
        "--server-boot-timeout",
        str(server_timeout),
        "--fl4359-jitter-hold-frames",
        "30",
        "--fl4431-secondary-effect-diagnostic-only",
    ]
    _append_owned_runtime_args(
        command,
        CURRENT_SECONDARY_EFFECT_RUNTIME_ARGS,
    )
    result = run_cmd(command, log_path=out_dir / "wrapper.log")
    receipt_path = out_dir / "receipt.json"
    result.update(
        {
            "arm": "current_secondary_effect",
            "renderer": "current",
            "visual_evidence": False,
            "performance_evidence": False,
            "diagnostic_attachment": True,
            "receipt_path": rel(receipt_path),
            "receipt": load_json(receipt_path),
            "runtime_args": _runtime_args_from_command(command),
        }
    )
    return result


def run_current_full_grid_oracle_arm(
    *,
    project: Path,
    colyseus_dir: Path,
    out_dir: Path,
    port: int,
    godot_timeout: int,
    server_timeout: int,
) -> dict[str, Any]:
    """Grade every perspective subcell across one uninterrupted flight."""

    command = [
        sys.executable,
        str(FULL_GRID_ORACLE_RUNNER),
        "--godot-project-dir",
        str(project),
        "--colyseus-dir",
        str(colyseus_dir),
        "--out",
        str(out_dir),
        "--port",
        str(port),
        "--godot-timeout",
        str(godot_timeout),
        "--server-boot-timeout",
        str(server_timeout),
        "--camera-mode",
        "plane_air",
        "--analyzer-proof-profile",
        FULL_GRID_ORACLE_PROFILE,
        "--product-time",
        "17.0",
        "--day-cycle-time",
        "0.5",
        "--allow-large-jitter-capture",
    ]
    result = run_cmd(command, log_path=out_dir / "wrapper.log")
    receipt_path = out_dir / "receipt.json"
    summary_path = out_dir / "analysis_summary.json"
    result.update(
        {
            "arm": "current_full_grid_temporal_oracle",
            "renderer": "current",
            "visual_evidence": False,
            "performance_evidence": False,
            "temporal_geometry_evidence": True,
            "proof_profile": FULL_GRID_ORACLE_PROFILE,
            "receipt_path": rel(receipt_path),
            "receipt": load_json(receipt_path),
            "analysis_summary_path": rel(summary_path),
            "analysis_summary": load_json(summary_path),
        }
    )
    return result


def current_visual_arm_ok(
    result: dict[str, Any],
    *,
    expected_view_mode: str,
    expected_frame_count: int,
    expected_route_ticks: list[int],
    expected_head: str,
    expected_client_fingerprint: str,
    expected_server_fingerprint: str,
) -> bool:
    """Admit one explicit current projection from the real airborne route."""

    receipt = result.get("receipt", {})
    if (
        not isinstance(receipt, dict)
        or expected_view_mode not in CURRENT_VISUAL_RUNTIME_ARGS
    ):
        return False
    try:
        frame_count = int(result.get("frame_count", 0))
        visual_frame_count = int(receipt.get("visual_frame_count", 0))
        visual_capture_limit = int(receipt.get("visual_capture_limit", 0))
        airplane_count = int(receipt.get("max_airplane_world_item_count", 0))
        camera_projection = int(receipt.get("camera_projection", -1))
    except (TypeError, ValueError):
        return False
    result_frame_paths = result.get("frame_paths")
    receipt_frame_paths = receipt.get("visual_frame_paths")
    samples = receipt.get("visual_frame_samples")
    if (
        not isinstance(result_frame_paths, list)
        or not isinstance(receipt_frame_paths, list)
        or not isinstance(samples, list)
    ):
        return False
    try:
        sample_ticks = [
            int(sample["route_tick"])
            for sample in samples
            if isinstance(sample, dict)
        ]
        sample_paths = [
            str(sample["path"])
            for sample in samples
            if isinstance(sample, dict)
        ]
    except (KeyError, TypeError, ValueError):
        return False
    expected_label, expected_projection = CURRENT_PROJECTION_RECEIPT_MODES[
        expected_view_mode
    ]
    return (
        result.get("returncode") == 0
        and result.get("renderer") == "current"
        and result.get("view_mode") == expected_view_mode
        and result.get("visual_evidence") is True
        and result.get("performance_evidence") is False
        and result.get("runtime_args")
        == list(CURRENT_VISUAL_RUNTIME_ARGS[expected_view_mode])
        and receipt.get("proof") == CURRENT_MOVING_FLIGHT_RECEIPT_KIND
        and receipt.get("pass") is True
        and receipt.get("frame_captured") is True
        and receipt.get("clean_play_no_capture") is False
        and receipt.get("secondary_effect_diagnostic_only") is False
        and receipt.get("diagnostic_nonvisual") is False
        and receipt.get("jitter_capture_requested") is False
        and receipt.get("capture_reason") == ""
        and receipt.get("server_premounted_primary_airplane_observed") is True
        and receipt.get("airborne_airplane_ready") is True
        and receipt.get("local_airplane_pilot") is True
        and receipt.get("airplane_world_item_count_exact") is True
        and receipt.get("camera_view_mode_label") == expected_label
        and camera_projection == expected_projection
        and airplane_count == 2
        and expected_frame_count > 0
        and len(expected_route_ticks) == expected_frame_count
        and frame_count == expected_frame_count
        and visual_frame_count == frame_count
        and visual_capture_limit == expected_frame_count
        and receipt.get("visual_route_target_ticks") == expected_route_ticks
        and receipt.get("missing_visual_route_ticks") == []
        and receipt.get("visual_route_ticks_exact") is True
        and sample_ticks == expected_route_ticks
        and len(sample_ticks) == len(samples)
        and len(set(sample_ticks)) == len(sample_ticks)
        and receipt_frame_paths == result_frame_paths
        and sample_paths == result_frame_paths
        and arm_source_identity_ok(
            result,
            expected_head=expected_head,
            expected_client_fingerprint=expected_client_fingerprint,
            expected_server_fingerprint=expected_server_fingerprint,
        )
    )


def current_night_temporal_arm_ok(
    result: dict[str, Any],
    *,
    expected_head: str,
    expected_client_fingerprint: str,
    expected_server_fingerprint: str,
) -> bool:
    """Admit the exact current-night sequence without treating it as FPS."""

    receipt = result.get("receipt", {})
    if not isinstance(receipt, dict):
        return False
    result_frame_paths = result.get("frame_paths")
    receipt_frame_paths = receipt.get("visual_frame_paths")
    samples = receipt.get("visual_frame_samples")
    if (
        not isinstance(result_frame_paths, list)
        or not isinstance(receipt_frame_paths, list)
        or not isinstance(samples, list)
        or len(samples) != len(CURRENT_NIGHT_TEMPORAL_TARGET_TICKS)
    ):
        return False
    capture_id = receipt.get("temporal_capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        return False
    required_positive_counts = (
        "lighthouse_beam_contributed_subcell_count",
        "bonfire_flame_contributed_subcell_count",
        "bonfire_light_contributed_subcell_count",
        "bonfire_smoke_contributed_subcell_count",
        "lighthouse_tower_selected_subcell_count",
        "terrain_selected_subcell_count",
        "water_selected_subcell_count",
    )
    sample_paths: list[str] = []
    prior_product_frame_time: float | None = None
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            return False
        try:
            route_tick = int(sample.get("route_tick", -1))
            frame_index = int(sample.get("frame_index", -1))
            target_offset = float(
                sample.get("beam_phase_offset_target", math.nan)
            )
            measured_offset = float(
                sample.get("beam_phase_offset_measured", math.nan)
            )
            beam_azimuth = float(
                sample.get("lighthouse_beam_azimuth", math.nan)
            )
        except (TypeError, ValueError):
            return False
        expected_offset = (2.0 * math.pi / 3.0) * index
        if (
            route_tick != CURRENT_NIGHT_TEMPORAL_TARGET_TICKS[index]
            or frame_index != index
            or sample.get("label") != CURRENT_NIGHT_TEMPORAL_LABELS[index]
            or sample.get("temporal_capture_id") != capture_id
            or sample.get("png_capture_owner")
            != "run_fl4265_airplane_dogfight_proof.gd"
            or sample.get("png_captured_in_same_process") is not True
            or sample.get("airborne_airplane_ready") is not True
            or sample.get("lighthouse_record_published") is not True
            or sample.get("bonfire_record_published") is not True
            or sample.get("firefly_record_published") is not True
            or not math.isfinite(target_offset)
            or not math.isfinite(measured_offset)
            or not math.isfinite(beam_azimuth)
            or not math.isclose(
                target_offset,
                expected_offset,
                rel_tol=CLEAN_PLAY_FLOAT_REL_TOLERANCE,
                abs_tol=CLEAN_PLAY_FLOAT_ABS_TOLERANCE,
            )
            or abs(
                math.atan2(
                    math.sin(measured_offset - expected_offset),
                    math.cos(measured_offset - expected_offset),
                )
            )
            > 0.08
        ):
            return False
        raw_product_frame_time = sample.get("product_frame_time_seconds")
        if (
            isinstance(raw_product_frame_time, bool)
            or not isinstance(raw_product_frame_time, (int, float))
        ):
            return False
        product_frame_time = float(raw_product_frame_time)
        if not math.isfinite(product_frame_time):
            return False
        if (
            prior_product_frame_time is not None
            and product_frame_time <= prior_product_frame_time
        ):
            return False
        prior_product_frame_time = product_frame_time
        firefly_record_count = sample.get("firefly_record_count")
        if (
            isinstance(firefly_record_count, bool)
            or not isinstance(firefly_record_count, int)
            or firefly_record_count <= 0
        ):
            return False
        for field in required_positive_counts:
            value = sample.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                return False
        path = sample.get("path")
        if not isinstance(path, str) or not path:
            return False
        sample_paths.append(path)
    try:
        frame_count = int(result.get("frame_count", 0))
        receipt_frame_count = int(receipt.get("visual_frame_count", 0))
        camera_projection = int(receipt.get("camera_projection", -1))
    except (TypeError, ValueError):
        return False
    return (
        result.get("returncode") == 0
        and result.get("renderer") == "current"
        and result.get("view_mode") == "perspective"
        and result.get("visual_evidence") is True
        and result.get("temporal_evidence") is True
        and result.get("performance_evidence") is False
        and result.get("runtime_args")
        == list(CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS)
        and receipt.get("proof") == CURRENT_MOVING_FLIGHT_RECEIPT_KIND
        and receipt.get("pass") is True
        and receipt.get("night_airborne_temporal_sequence") is True
        and receipt.get("visual_capture_owner")
        == "run_fl4265_airplane_dogfight_proof.gd"
        and receipt.get("frame_captured") is True
        and receipt.get("clean_play_no_capture") is False
        and receipt.get("secondary_effect_diagnostic_only") is False
        and receipt.get("diagnostic_nonvisual") is False
        and receipt.get("jitter_capture_requested") is False
        and receipt.get("capture_reason") == ""
        and receipt.get("server_premounted_primary_airplane_observed") is True
        and receipt.get("airborne_airplane_ready") is True
        and receipt.get("local_airplane_pilot") is True
        and receipt.get("camera_view_mode_label") == "true_perspective"
        and camera_projection == 0
        and frame_count == len(CURRENT_NIGHT_TEMPORAL_TARGET_TICKS)
        and receipt_frame_count == frame_count
        and receipt.get("visual_route_target_ticks")
        == list(CURRENT_NIGHT_TEMPORAL_TARGET_TICKS)
        and receipt.get("missing_visual_route_ticks") == []
        and receipt.get("visual_route_ticks_exact") is True
        and receipt_frame_paths == result_frame_paths
        and sample_paths == result_frame_paths
        and arm_source_identity_ok(
            result,
            expected_head=expected_head,
            expected_client_fingerprint=expected_client_fingerprint,
            expected_server_fingerprint=expected_server_fingerprint,
        )
    )


def current_clean_play_arm_ok(
    result: dict[str, Any],
    *,
    expected_route_ticks: int,
    expected_view_mode: str,
    expected_head: str,
    expected_client_fingerprint: str,
    expected_server_fingerprint: str,
) -> bool:
    """Admit FPS from one explicit capture-free current projection."""

    receipt = result.get("receipt", {})
    if (
        not isinstance(receipt, dict)
        or expected_view_mode not in CURRENT_CLEAN_PLAY_RUNTIME_ARGS
    ):
        return False
    try:
        requested_route_ticks = int(
            receipt.get("requested_route_tick_duration", 0)
        )
        clean_route_start_tick = int(
            receipt.get("clean_route_start_tick", -1)
        )
        clean_route_start_tick_overshoot = int(
            receipt.get("clean_route_start_tick_overshoot", -1)
        )
        clean_route_end_tick = int(receipt.get("clean_route_end_tick", -1))
        server_route_end_tick = int(
            receipt.get("server_route_end_tick_observed", -1)
        )
        clean_route_end_tick_overshoot = int(
            receipt.get("clean_route_end_tick_overshoot", -1)
        )
        fps_sample_count = int(receipt.get("fps_sample_count", 0))
        clean_play_measured_frame_count = int(
            receipt.get("clean_play_measured_frame_count", 0)
        )
        total_frames = int(receipt.get("total_frames", 0))
        measured_fps = float(receipt.get("measured_fps", 0.0))
        frame_time_ms = float(receipt.get("fps_frame_time_ms", 0.0))
        camera_projection = int(receipt.get("camera_projection", -1))
        hitch_count = int(receipt.get("hitch_count_33_333ms", -1))
    except (TypeError, ValueError):
        return False
    raw_frame_times = receipt.get("clean_play_frame_times_ms")
    if (
        not isinstance(raw_frame_times, list)
        or len(raw_frame_times) < CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES
    ):
        return False
    frame_times: list[float] = []
    for raw_frame_time in raw_frame_times:
        if isinstance(raw_frame_time, bool):
            return False
        try:
            parsed_frame_time = float(raw_frame_time)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(parsed_frame_time) or parsed_frame_time <= 0.0:
            return False
        if parsed_frame_time > CLEAN_PLAY_MAX_SINGLE_FRAME_MS:
            return False
        frame_times.append(parsed_frame_time)

    window_sum = sum(frame_times[:CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES])
    if (
        window_sum / CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES
        > CLEAN_PLAY_MAX_WINDOW_MEAN_MS
        + CLEAN_PLAY_FLOAT_ABS_TOLERANCE
    ):
        return False
    for index in range(
        CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES,
        len(frame_times),
    ):
        window_sum += frame_times[index]
        window_sum -= frame_times[
            index - CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES
        ]
        if (
            window_sum / CLEAN_PLAY_SUSTAINED_WINDOW_SAMPLES
            > CLEAN_PLAY_MAX_WINDOW_MEAN_MS
            + CLEAN_PLAY_FLOAT_ABS_TOLERANCE
        ):
            return False

    recomputed_frame_time_ms = sum(frame_times) / len(frame_times)
    recomputed_fps = 1000.0 / recomputed_frame_time_ms
    scalar_timing_matches = (
        math.isfinite(measured_fps)
        and math.isfinite(frame_time_ms)
        and math.isclose(
            measured_fps,
            recomputed_fps,
            rel_tol=CLEAN_PLAY_FLOAT_REL_TOLERANCE,
            abs_tol=CLEAN_PLAY_FLOAT_ABS_TOLERANCE,
        )
        and math.isclose(
            frame_time_ms,
            recomputed_frame_time_ms,
            rel_tol=CLEAN_PLAY_FLOAT_REL_TOLERANCE,
            abs_tol=CLEAN_PLAY_FLOAT_ABS_TOLERANCE,
        )
    )
    expected_label, expected_projection = CURRENT_PROJECTION_RECEIPT_MODES[
        expected_view_mode
    ]
    return (
        result.get("returncode") == 0
        and result.get("renderer") == "current"
        and result.get("view_mode") == expected_view_mode
        and result.get("runtime_args")
        == list(CURRENT_CLEAN_PLAY_RUNTIME_ARGS[expected_view_mode])
        and result.get("visual_evidence") is False
        and result.get("performance_evidence") is True
        and receipt.get("proof") == CURRENT_MOVING_FLIGHT_RECEIPT_KIND
        and receipt.get("pass") is True
        and receipt.get("clean_play_no_capture") is True
        and receipt.get("clean_play_timing_uncontaminated") is True
        and receipt.get("secondary_effect_diagnostic_only") is False
        and receipt.get("diagnostic_nonvisual") is False
        and receipt.get("frame_captured") is False
        and receipt.get("jitter_capture_requested") is False
        and receipt.get("visual_frame_count") == 0
        and receipt.get("camera_view_mode_label") == expected_label
        and camera_projection == expected_projection
        and expected_route_ticks > 0
        and requested_route_ticks == expected_route_ticks
        and 30 <= clean_route_start_tick <= 31
        and clean_route_start_tick_overshoot == clean_route_start_tick - 30
        and clean_route_end_tick - clean_route_start_tick
        == expected_route_ticks
        and clean_route_end_tick <= server_route_end_tick <= clean_route_end_tick + 1
        and clean_route_end_tick_overshoot
        == server_route_end_tick - clean_route_end_tick
        and fps_sample_count > 0
        and fps_sample_count == clean_play_measured_frame_count
        and fps_sample_count == total_frames
        and fps_sample_count == len(frame_times)
        and hitch_count == 0
        and scalar_timing_matches
        and measured_fps >= MIN_CLEAN_PLAY_FPS
        and frame_time_ms > 0.0
        and arm_source_identity_ok(
            result,
            expected_head=expected_head,
            expected_client_fingerprint=expected_client_fingerprint,
            expected_server_fingerprint=expected_server_fingerprint,
        )
    )


def current_secondary_effect_arm_ok(
    result: dict[str, Any],
    *,
    expected_head: str,
    expected_client_fingerprint: str,
    expected_server_fingerprint: str,
) -> bool:
    """Admit the diagnostic join without treating it as pixels and timing."""

    receipt = result.get("receipt", {})
    if not isinstance(receipt, dict):
        return False
    try:
        host_count = int(receipt.get("host_vapor_sample_count", 0))
        retained_count = int(receipt.get("retained_mat207_face_count", 0))
        product_count = int(
            receipt.get("product_mat207_contributed_subcell_count", 0)
        )
    except (TypeError, ValueError):
        return False
    return (
        result.get("returncode") == 0
        and result.get("renderer") == "current"
        and result.get("diagnostic_attachment") is True
        and result.get("visual_evidence") is False
        and result.get("performance_evidence") is False
        and receipt.get("proof") == CURRENT_MOVING_FLIGHT_RECEIPT_KIND
        and receipt.get("pass") is True
        and receipt.get("secondary_effect_diagnostic_only") is True
        and receipt.get("diagnostic_nonvisual") is True
        and receipt.get("frame_captured") is False
        and receipt.get("product_mat207_contribution_receipt_available") is True
        and host_count > 0
        and retained_count > 0
        and product_count > 0
        and result.get("runtime_args")
        == list(CURRENT_SECONDARY_EFFECT_RUNTIME_ARGS)
        and arm_source_identity_ok(
            result,
            expected_head=expected_head,
            expected_client_fingerprint=expected_client_fingerprint,
            expected_server_fingerprint=expected_server_fingerprint,
        )
    )


def capture_blocker_from_port(port: int) -> str:
    """Reject a port already owned by another local run."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return "capture_blocked_port_in_use"
    return ""


def _product_admission_package_contract_ok(summary: dict[str, Any]) -> bool:
    """Require both structural witness validity and exact Product admission.

    The analyzer's structural gate proves that every captured row is accounted
    for. Its gameplay gate reports whether the observed authored-to-b55-to-b43
    chain is exact. Promotion requires both, while a chain mismatch remains a
    valid diagnostic RED result with its first-collapse category preserved.
    """

    admission = summary.get("product_admission_contingency")
    gates = summary.get("gates")
    if not isinstance(admission, dict) or not isinstance(gates, dict):
        return False
    for gate_name in (
        "evidence_product_admission_contingency_valid",
        "gameplay_product_admission_chain_exact",
    ):
        gate = gates.get(gate_name)
        if not isinstance(gate, dict) or gate.get("pass") is not True:
            return False
    strict_true_fields = (
        "product_admission_contingency_valid",
        "product_admission_evidence_valid",
        "product_admission_contingency_total_matches_input",
        "product_admission_flow_histogram_total_matches_valid_witness_rows",
        "product_admission_gameplay_chain_valid",
    )
    if any(admission.get(field) is not True for field in strict_true_fields):
        return False
    counts = admission.get("product_admission_contingency_counts")
    if not isinstance(counts, dict):
        return False
    required_count_fields = (
        "nonterrain_product_rows",
        "terrain_admission_witness_missing",
        "terrain_admission_witness_invalid",
        "terrain_authored_admission_to_b55_mismatch",
        "terrain_classifier_to_b55_mismatch",
        "terrain_b55_to_b43_actor_overlay",
        "terrain_b55_to_b43_missing",
        "terrain_b55_to_b43_mismatch",
        "terrain_full_chain_valid",
    )
    if any(type(counts.get(field)) is not int or counts[field] < 0
           for field in required_count_fields):
        return False
    try:
        row_count = admission["product_admission_contingency_row_count"]
        total = admission["product_admission_contingency_total"]
        terrain_rows = admission["product_admission_terrain_rows"]
        valid_witness_rows = admission["product_admission_flow_valid_witness_rows"]
        flow_total = admission["product_admission_flow_histogram_total"]
        mismatch_rows = admission[
            "product_admission_gameplay_chain_mismatch_rows"
        ]
    except (KeyError, TypeError):
        return False
    if any(
        type(value) is not int or value < 0
        for value in (
            row_count,
            total,
            terrain_rows,
            valid_witness_rows,
            flow_total,
            mismatch_rows,
        )
    ):
        return False
    return bool(
        row_count > 0
        and total == row_count
        and sum(counts[field] for field in required_count_fields) == total
        and terrain_rows > 0
        and terrain_rows == total - counts["nonterrain_product_rows"]
        and counts["terrain_admission_witness_missing"] == 0
        and counts["terrain_admission_witness_invalid"] == 0
        and flow_total == valid_witness_rows
        and mismatch_rows == 0
    )


def current_full_grid_oracle_arm_ok(
    result: dict[str, Any], *, expected_head: str
) -> bool:
    """Require the exhaustive geometry oracle before package promotion.

    A screenshot package can hide displaced, duplicated, or temporally bouncing
    geometry. Exact current-ray primitive disagreement remains diagnostic for a
    finite texel splat; acceptance instead requires every such row to satisfy
    the source, projected-coverage, depth, and exact-contender predicates. The
    arm fails closed on missing decomposition, unknown first divergence, or a
    nonzero structural/admissibility defect.
    """

    receipt = result.get("receipt", {})
    summary = result.get("analysis_summary", {})
    if not isinstance(receipt, dict) or not isinstance(summary, dict):
        return False
    required_zero_fields = (
        "full_grid_temporal_oracle_structural_geometry_error_rows",
        "full_grid_temporal_oracle_finite_splat_inadmissible_rows",
        "full_grid_temporal_oracle_changing_reference_key_state_pairs",
        # A stable reference anchor may not silently change visibility banks.
        # Capture-generation advancement is a separate diagnostic because it
        # can occur while CURRENT remains the sole source_probe family.
        "full_grid_temporal_oracle_source_probe_set_changes",
        "full_grid_temporal_oracle_product_translation_error_rows",
        "full_grid_temporal_oracle_multi_glyph_mask_cells",
        "full_grid_temporal_oracle_incomplete_glyph_mask_rows",
        "full_grid_temporal_oracle_structural_duplicate_components",
        "full_grid_temporal_oracle_structural_out_of_place_identities",
        "full_grid_temporal_oracle_candidate_tuple_duplicate_components",
        "full_grid_temporal_oracle_first_divergence_unknown",
        "full_grid_temporal_oracle_unclassified_subcells",
        "full_grid_temporal_oracle_normalization_invalid_rows",
    )
    try:
        zero_counts = all(int(summary[field]) == 0 for field in required_zero_fields)
    except (KeyError, TypeError, ValueError):
        return False
    mask_owner_stability = summary.get("cell_mask_owner_stability", {})
    mask_owner_counts = (
        mask_owner_stability.get("counts", {})
        if isinstance(mask_owner_stability, dict)
        else {}
    )
    try:
        mask_owner_zero_error = (
            int(mask_owner_stability["invalid_cell_count"]) == 0
            and int(mask_owner_stability["comparable_pair_count"]) > 0
            and int(mask_owner_counts["comparable_cells"]) > 0
            and int(mask_owner_counts["unstable_cells"]) == 0
        )
    except (KeyError, TypeError, ValueError):
        return False
    analysis_gates = summary.get("gates", {})
    required_temporal_presentation_gates = (
        "gameplay_moving_camera_jitter_bounded",
        "evidence_presentation_observer_population_accounted",
        "evidence_presentation_observer_sensitivity_valid",
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "gameplay_cell_mask_owner_stability_bounded",
        # A legacy jitter pass does not establish terminal expected
        # reprojection. Require the complete evidence contract and exact
        # expected ANSI-cell address. Pixel-centroid R_AB remains diagnostic
        # until canon defines a residual calibration law.
        "evidence_celestial_time_fixed",
        "evidence_terminal_expected_reprojection_valid",
        "gameplay_terminal_expected_cell_address_exact",
    )
    temporal_presentation_ok = (
        isinstance(analysis_gates, dict)
        and all(
            isinstance(analysis_gates.get(gate_name), dict)
            and analysis_gates[gate_name].get("pass") is True
            for gate_name in required_temporal_presentation_gates
        )
    )
    product_admission_ok = _product_admission_package_contract_ok(summary)
    try:
        terrain_rows = int(summary["terrain_product_homogeneity_rows"])
        terrain_paint_eligible_rows = int(
            summary["terrain_product_final_paint_eligible_rows"]
        )
        terrain_paint_actor_rows = int(
            summary["terrain_product_final_paint_actor_overlay_rows"]
        )
        terrain_homogeneity_ok = (
            summary["terrain_product_homogeneity_source"] == "final_product_b55"
            and terrain_rows > 0
            and int(summary["terrain_product_homogeneity_invalid_rows"]) == 0
            and float(summary["terrain_product_dominant_material_ratio"]) <= 0.65
            and summary["terrain_product_homogeneity_baseline_calibrated"] is True
        )
        # Promotion requires a complete same-row b55 Product to b43 native-paint
        # witness for every terrain slot not covered by the separate actor owner.
        terrain_final_paint_join_ok = (
            terrain_paint_eligible_rows > 0
            and terrain_paint_actor_rows >= 0
            and terrain_paint_eligible_rows + terrain_paint_actor_rows
            == terrain_rows
            and int(summary["terrain_product_final_paint_missing_slot_rows"]) == 0
            and int(summary["terrain_product_final_paint_missing_colour_rows"]) == 0
            and int(summary["terrain_product_final_paint_glyph_join_rows"])
            == terrain_paint_eligible_rows
            and int(summary["terrain_product_final_paint_colour_join_rows"])
            == terrain_paint_eligible_rows
        )
    except (KeyError, TypeError, ValueError):
        return False
    continuity = receipt.get("authority_tick_continuity", {})
    return (
        result.get("returncode") == 0
        and result.get("renderer") == "current"
        and result.get("proof_profile") == FULL_GRID_ORACLE_PROFILE
        and result.get("temporal_geometry_evidence") is True
        and receipt.get("wrapper_git_head") == expected_head
        and receipt.get("proof_profile") == FULL_GRID_ORACLE_PROFILE
        and receipt.get("pass") is True
        and receipt.get("analyzer_pass") is True
        and isinstance(continuity, dict)
        and continuity.get("temporal_claim_valid") is True
        and summary.get("proof_profile") == FULL_GRID_ORACLE_PROFILE
        and summary.get("same_frame_oracle_exhaustive_frame_coverage") is True
        and bool(summary.get(
            "same_frame_oracle_exact_sample_index_set_per_frame", {}
        ))
        and all(
            value is True
            for value in summary.get(
                "same_frame_oracle_exact_sample_index_set_per_frame", {}
            ).values()
        )
        and summary.get("same_frame_oracle_authority_queue_window_valid") is True
        and summary.get("same_frame_oracle_authority_tick_identity_valid") is True
        and summary.get("same_frame_oracle_temporal_sequence_valid") is True
        and summary.get("full_grid_temporal_oracle_valid") is True
        and summary.get(
            "full_grid_temporal_oracle_mismatch_decomposition_valid"
        ) is True
        and summary.get(
            "full_grid_temporal_oracle_geometry_decomposition_valid"
        ) is True
        and summary.get(
            "full_grid_temporal_oracle_finite_splat_decomposition_valid"
        ) is True
        and summary.get(
            "full_grid_temporal_oracle_product_exact_identity_available"
        ) is True
        and summary.get(
            "full_grid_temporal_oracle_complete_glyph_mask_hash_available"
        ) is True
        and summary.get("full_grid_temporal_oracle_defect_map_complete") is True
        and isinstance(
            summary.get("full_grid_temporal_oracle_defect_map_sha256"), str
        )
        and len(summary["full_grid_temporal_oracle_defect_map_sha256"]) == 64
        and mask_owner_zero_error
        and temporal_presentation_ok
        and product_admission_ok
        and terrain_homogeneity_ok
        and terrain_final_paint_join_ok
        and zero_counts
    )


def package_status(results: dict[str, Any]) -> str:
    """Classify only the retained current-renderer surfaces."""

    checks = results.get("static_checks", [])
    if checks and not static_checks_ok(checks):
        return "current_static_checks_failed"
    if not results.get("capture_requested"):
        return "dry_run_no_capture" if results.get("dry_run") else "capture_not_requested"
    try:
        expected_frame_count = int(
            results.get("visual_frame_target_count", 0)
        )
        raw_expected_route_ticks = results.get(
            "visual_route_target_ticks",
            [],
        )
        if not isinstance(raw_expected_route_ticks, list):
            return "current_visual_target_contract_failed"
        expected_route_ticks = [
            int(value) for value in raw_expected_route_ticks
        ]
    except (TypeError, ValueError):
        return "current_visual_target_contract_failed"
    canonical_route_ticks = expected_visual_route_ticks(
        visual_start=DEFAULT_ACCEPTANCE_VISUAL_START,
        visual_stride=DEFAULT_ACCEPTANCE_VISUAL_STRIDE,
        visual_limit=DEFAULT_ACCEPTANCE_VISUAL_LIMIT,
    )
    if (
        expected_frame_count != DEFAULT_ACCEPTANCE_VISUAL_LIMIT
        or expected_route_ticks != canonical_route_ticks
    ):
        return "current_visual_target_contract_failed"
    expected_head = results.get("head", "")
    expected_client_fingerprint = results.get(
        "client_runtime_scope_fingerprint",
        "",
    )
    expected_server_fingerprint = results.get(
        "server_runtime_scope_fingerprint",
        "",
    )
    if not current_visual_arm_ok(
        results.get("current_visual_perspective", {}),
        expected_view_mode="perspective",
        expected_frame_count=expected_frame_count,
        expected_route_ticks=expected_route_ticks,
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_visual_perspective_failed"
    if not current_visual_arm_ok(
        results.get("current_visual_ortho", {}),
        expected_view_mode="ortho",
        expected_frame_count=expected_frame_count,
        expected_route_ticks=expected_route_ticks,
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_visual_ortho_failed"
    if not current_night_temporal_arm_ok(
        results.get("current_night_temporal", {}),
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_night_temporal_failed"
    if not current_secondary_effect_arm_ok(
        results.get("current_secondary_effect", {}),
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_secondary_effect_failed"
    if not current_clean_play_arm_ok(
        results.get("current_clean_play_perspective", {}),
        expected_route_ticks=int(results.get("clean_play_route_ticks", 0)),
        expected_view_mode="perspective",
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_clean_play_perspective_failed"
    if not current_clean_play_arm_ok(
        results.get("current_clean_play_ortho", {}),
        expected_route_ticks=int(results.get("clean_play_route_ticks", 0)),
        expected_view_mode="ortho",
        expected_head=expected_head,
        expected_client_fingerprint=expected_client_fingerprint,
        expected_server_fingerprint=expected_server_fingerprint,
    ):
        return "current_clean_play_ortho_failed"
    if not current_full_grid_oracle_arm_ok(
        results.get("current_full_grid_temporal_oracle", {}),
        expected_head=expected_head,
    ):
        return "current_full_grid_temporal_oracle_failed"
    if results.get("static_checks_skipped") is True:
        return "current_package_nonpromotable_static_checks_skipped"
    if results.get("dirty_diagnostic_capture_allowed") is True:
        return "current_package_nonpromotable_dirty_diagnostic_capture"
    return "current_package_ready_for_runtime_review"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect the current FL-4431 visual and clean-play package"
    )
    parser.add_argument(
        "--out",
        default=".run/fl4431_current_package/latest",
    )
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    parser.add_argument("--port", type=int, default=2610)
    parser.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_ACCEPTANCE_MOVING_FRAMES,
    )
    parser.add_argument(
        "--clean-play-frames",
        type=int,
        default=DEFAULT_CLEAN_PLAY_ROUTE_TICKS,
        help=(
            "Authoritative server-route duration in ticks for each clean-play "
            "projection arm."
        ),
    )
    parser.add_argument(
        "--godot-timeout",
        type=int,
        default=DEFAULT_ACCEPTANCE_GODOT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--server-timeout", type=int, default=45)
    parser.add_argument("--skip-static", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-dirty-diagnostic-capture",
        action="store_true",
        help=(
            "Allow a current-only diagnostic launch from a dirty tree. "
            "The manifest records the dirty paths."
        ),
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "current_package_manifest.json"

    head, dirty_paths = capture_git_provenance()
    client_runtime_scope_fingerprint = capture_runtime_scope_fingerprint(
        ROOT / "godot_project"
    )
    server_runtime_scope_fingerprint = capture_runtime_scope_fingerprint(
        ROOT / "colyseus_server"
    )
    visual_route_target_ticks = expected_visual_route_ticks(
        visual_start=DEFAULT_ACCEPTANCE_VISUAL_START,
        visual_stride=DEFAULT_ACCEPTANCE_VISUAL_STRIDE,
        visual_limit=DEFAULT_ACCEPTANCE_VISUAL_LIMIT,
    )
    results: dict[str, Any] = {
        "schema": "asciicker.fl4431.current_package.v1",
        "fl_refs": ["FL-4431", "FL-4359", "FL-4265"],
        "timestamp": datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds"),
        "head": head,
        "dirty_paths": dirty_paths,
        "client_runtime_scope_fingerprint":
            client_runtime_scope_fingerprint,
        "server_runtime_scope_fingerprint":
            server_runtime_scope_fingerprint,
        "out_dir": rel(out_dir),
        "capture_requested": bool(args.capture),
        "dry_run": bool(args.dry_run),
        "static_checks_skipped": bool(args.skip_static),
        "dirty_diagnostic_capture_allowed": bool(
            args.allow_dirty_diagnostic_capture
        ),
        "clean_play_route_ticks": int(args.clean_play_frames),
        "clean_play_minimum_fps": MIN_CLEAN_PLAY_FPS,
        "visual_frame_target_count": DEFAULT_ACCEPTANCE_VISUAL_LIMIT,
        "visual_route_target_ticks": visual_route_target_ticks,
        "night_temporal_frame_target_count":
            len(CURRENT_NIGHT_TEMPORAL_TARGET_TICKS),
        "night_temporal_route_target_ticks":
            list(CURRENT_NIGHT_TEMPORAL_TARGET_TICKS),
        "visual_view_modes": list(CURRENT_VISUAL_RUNTIME_ARGS),
        "clean_play_view_modes": list(CURRENT_CLEAN_PLAY_RUNTIME_ARGS),
        "presentation_size": list(CANONICAL_PRESENTATION_SIZE),
        "target_size": list(CANONICAL_TARGET_SIZE),
        "ansi_size": list(CANONICAL_ANSI_SIZE),
        "owned_runtime_args": {
            **{
                f"current_visual_{view_mode}": list(runtime_args)
                for view_mode, runtime_args in (
                    CURRENT_VISUAL_RUNTIME_ARGS.items()
                )
            },
            "current_secondary_effect": list(
                CURRENT_SECONDARY_EFFECT_RUNTIME_ARGS
            ),
            "current_night_temporal": list(
                CURRENT_NIGHT_TEMPORAL_RUNTIME_ARGS
            ),
            **{
                f"current_clean_play_{view_mode}": list(runtime_args)
                for view_mode, runtime_args in (
                    CURRENT_CLEAN_PLAY_RUNTIME_ARGS.items()
                )
            },
        },
        "static_checks": [],
        "status": "initializing",
    }
    write_manifest(manifest_path, results)

    if not args.skip_static:
        results["static_checks"] = static_checks(out_dir, args.godot)
        if not static_checks_ok(results["static_checks"]):
            results["status"] = "current_static_checks_failed"
            write_manifest(manifest_path, results)
            print(manifest_path)
            print(results["status"])
            return 1

    if args.capture and not args.dry_run:
        if dirty_paths and not args.allow_dirty_diagnostic_capture:
            results["status"] = "capture_blocked_dirty_tree"
            write_manifest(manifest_path, results)
            print(manifest_path)
            print(results["status"])
            return 1
        blocker = capture_blocker_from_port(args.port)
        if blocker:
            results["status"] = blocker
            write_manifest(manifest_path, results)
            print(manifest_path)
            print(results["status"])
            return 1

        results["current_visual_perspective"] = run_current_visual_arm(
            project=ROOT / "godot_project",
            colyseus_dir=ROOT / "colyseus_server",
            out_dir=out_dir / "current_visual_perspective",
            port=args.port,
            frames=args.frames,
            visual_stride=DEFAULT_ACCEPTANCE_VISUAL_STRIDE,
            visual_limit=DEFAULT_ACCEPTANCE_VISUAL_LIMIT,
            visual_start=DEFAULT_ACCEPTANCE_VISUAL_START,
            godot_timeout=args.godot_timeout,
            server_timeout=args.server_timeout,
            view_mode="perspective",
        )
        results["current_visual_ortho"] = run_current_visual_arm(
            project=ROOT / "godot_project",
            colyseus_dir=ROOT / "colyseus_server",
            out_dir=out_dir / "current_visual_ortho",
            port=args.port,
            frames=args.frames,
            visual_stride=DEFAULT_ACCEPTANCE_VISUAL_STRIDE,
            visual_limit=DEFAULT_ACCEPTANCE_VISUAL_LIMIT,
            visual_start=DEFAULT_ACCEPTANCE_VISUAL_START,
            godot_timeout=args.godot_timeout,
            server_timeout=args.server_timeout,
            view_mode="ortho",
        )
        results["current_night_temporal"] = (
            run_current_night_temporal_arm(
                project=ROOT / "godot_project",
                colyseus_dir=ROOT / "colyseus_server",
                out_dir=out_dir / "current_night_temporal",
                port=args.port,
                godot_timeout=args.godot_timeout,
                server_timeout=args.server_timeout,
            )
        )
        results["current_secondary_effect"] = run_current_secondary_effect_arm(
            project=ROOT / "godot_project",
            colyseus_dir=ROOT / "colyseus_server",
            out_dir=out_dir / "current_secondary_effect",
            port=args.port,
            godot_timeout=args.godot_timeout,
            server_timeout=args.server_timeout,
        )
        results["current_clean_play_perspective"] = (
            run_current_clean_play_arm(
                project=ROOT / "godot_project",
                colyseus_dir=ROOT / "colyseus_server",
                out_dir=out_dir / "current_clean_play_perspective",
                port=args.port,
                route_ticks=args.clean_play_frames,
                godot_timeout=args.godot_timeout,
                server_timeout=args.server_timeout,
                view_mode="perspective",
            )
        )
        results["current_clean_play_ortho"] = run_current_clean_play_arm(
            project=ROOT / "godot_project",
            colyseus_dir=ROOT / "colyseus_server",
            out_dir=out_dir / "current_clean_play_ortho",
            port=args.port,
            route_ticks=args.clean_play_frames,
            godot_timeout=args.godot_timeout,
            server_timeout=args.server_timeout,
            view_mode="ortho",
        )
        results["current_full_grid_temporal_oracle"] = (
            run_current_full_grid_oracle_arm(
                project=ROOT / "godot_project",
                colyseus_dir=ROOT / "colyseus_server",
                out_dir=out_dir / "current_full_grid_temporal_oracle",
                port=args.port,
                godot_timeout=args.godot_timeout,
                server_timeout=args.server_timeout,
            )
        )

    results["status"] = package_status(results)
    if not args.capture:
        results["run_command"] = [
            sys.executable,
            "scripts/run_fl4359_acceptance_package.py",
            "--capture",
            "--out",
            rel(out_dir),
        ]
    write_manifest(manifest_path, results)
    print(manifest_path)
    print(results["status"])
    # A dry run exits zero only to confirm that its nonpromotable manifest was
    # written. Capture modes that bypass static checks and dirty-tree blocking
    # exit nonzero even when their diagnostic arms completed successfully.
    return (
        0
        if results["status"]
        in {
            "current_package_ready_for_runtime_review",
            "capture_not_requested",
            "dry_run_no_capture",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

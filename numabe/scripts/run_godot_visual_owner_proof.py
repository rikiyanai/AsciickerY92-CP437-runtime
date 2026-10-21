#!/usr/bin/env python3
"""Current Godot visual owner proof runner.

Runs headed Colyseus-client captures for the four active visual owner lanes:
wake alignment, falling-leaf density, world-owned birds, and airplane mesh
visibility. The runner reuses scripts/run_fl4359_headed_movement.py so the
server/client launch path stays identical to the manual client path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_fl4359_headed_movement.py"
DEFAULT_FEATURES = ",".join(
    [
        "foliage_cards",
        "water_current_package",
        "ascii_birds",
        "wind_gameidea",
        "wind_victorkarp",
        "material_ramp_steps",
        "surface_stable_dither",
        "cloud_shadow_deep",
        "lighthouse_light",
        "local_light_volume",
    ]
)
DYNAMIC_RECORD_RE = re.compile(r"WORLD_MESH_DYNAMIC_RECORDS bird_faces=(\d+) airplane_faces=(\d+)")


# FL-4359 full-grid contract: the runner rejects the exhaustive oracle unless
# the celestial phase is pinned to exactly midday.
FULL_GRID_ORACLE_PROFILE = "full_grid_temporal_oracle_v1"
FULL_GRID_ORACLE_PRESENTATION_MODE = "FC"
FULL_GRID_ORACLE_DAY_CYCLE_TIME = "0.5"

# FL-4359 B6 exact translation-pair pose. Reused VERBATIM from the recorded
# `pose.tower_strafe_f3_route` identity; a different pose is a different
# experiment and must not be substituted here.
TRANSLATION_PAIR_P0 = "95.586,35.394,59.003"
TRANSLATION_PAIR_P2 = "95.636,35.394,59.003"  # P0 + 0.05 on x
# The operator's second controlled translation pair. Same B6 pose, same single
# variable (world x), five times the P0->P2 separation. It exists so a difference
# that is invisible at 0.05 can be read at 0.25 without changing anything else.
TRANSLATION_PAIR_P1 = "95.836,35.394,59.003"  # P0 + 0.25 on x
# The same P0->P2 x-delta, applied once per captured sequence frame by the pinned
# sweep route so a five-frame oracle window walks P0, P1, ... P4.
TRANSLATION_SWEEP_DELTA = "0.05,0,0"
TRANSLATION_SWEEP_POSITIONS = ";".join(
    [
        TRANSLATION_PAIR_P0,
        TRANSLATION_PAIR_P1,
        "96.086,35.394,59.003",
        "96.336,35.394,59.003",
        "96.586,35.394,59.003",
    ]
)
TRANSLATION_PAIR_YAW = "63.198115"
TRANSLATION_PAIR_PITCH = "-25.159299"
TRANSLATION_PAIR_FOV = "58"
TRANSLATION_PAIR_PRODUCT_TIME = "0.35"
TRANSLATION_PAIR_DAY_CYCLE_TIME = "0.5"


def _translation_pair_args(position: str, label: str) -> tuple[str, ...]:
    """One `--single-frame` capture of the B6 pose at the given camera position.

    The runner has no native translation-pair route, so the pair is expressed as
    two single-frame invocations that differ ONLY in `--exact-camera-position`.
    Every other pose component is held constant so the x-delta is the sole
    variable of the experiment.
    """
    return (
        "--single-frame",
        "--single-frame-label",
        label,
        "--exact-camera-position",
        position,
        "--exact-camera-yaw",
        TRANSLATION_PAIR_YAW,
        "--exact-camera-pitch",
        TRANSLATION_PAIR_PITCH,
        "--exact-camera-projection",
        "perspective",
        "--exact-camera-fov",
        TRANSLATION_PAIR_FOV,
        "--product-time",
        TRANSLATION_PAIR_PRODUCT_TIME,
        "--day-cycle-time",
        TRANSLATION_PAIR_DAY_CYCLE_TIME,
    )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    label: str
    camera_mode: str
    fl_refs: str
    fix_attempt: str
    move_frames: int
    settle_frames: int
    after_frames: int
    extra_user_args: tuple[str, ...] = ()
    # FL-4359 C7: runner-level flags for run_fl4359_headed_movement.py, appended
    # verbatim after the shared command. Empty for every pre-C7 scenario, so
    # their delegated command lines stay byte-identical to the previous wrapper.
    runner_args: tuple[str, ...] = ()
    # FL-4359 C7: a scenario that needs more than one runner invocation (the
    # translation pair needs two single-frame captures at two camera positions)
    # declares each invocation as (sub_label, extra_runner_args). Empty means
    # exactly one invocation writing directly into the scenario directory.
    runner_arg_sets: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # FL-4359 C9: analyzer proof profile requested from the runner. Empty keeps
    # the runner's own auto-selection (`general` / `airplane_temporal_v1`), which
    # is what every pre-C9 scenario used. Only a scenario that names
    # FULL_GRID_ORACLE_PROFILE participates in the C8 zero-defect roll-up.
    analyzer_proof_profile: str = ""
    # Empty inherits the wrapper-level resolution. Canonical protocols with a
    # recovered raster identity pin it here so a global default cannot drift it.
    resolution: str = ""


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="wake_leaf_bird_ortho",
        label="Ortho wake + leaves + birds",
        camera_mode="ortho",
        fl_refs="FL-4129 FL-4187 FL-4357 FL-4431",
        fix_attempt="wake uses actor_wake_world_position; leaves reduced; birds use bird_face world facts",
        move_frames=150,
        settle_frames=75,
        after_frames=18,
        extra_user_args=("--fl4359-press-b-birds",),
    ),
    Scenario(
        scenario_id="wake_leaf_bird_perspective",
        label="Perspective wake + leaves + birds",
        camera_mode="perspective",
        fl_refs="FL-4129 FL-4187 FL-4357 FL-4431",
        fix_attempt="same owners under perspective PixelCamera product path",
        move_frames=150,
        settle_frames=75,
        after_frames=18,
        extra_user_args=("--fl4359-press-b-birds",),
    ),
    Scenario(
        scenario_id="airplane_mesh_ortho",
        label="Ortho airplane mesh",
        camera_mode="plane_air_ortho",
        fl_refs="FL-4265 FL-4431",
        fix_attempt="airplane source facts publish parked and mounted server airplane world items",
        move_frames=120,
        settle_frames=60,
        after_frames=12,
    ),
    Scenario(
        scenario_id="airplane_mesh_perspective",
        label="Perspective airplane mesh",
        camera_mode="plane_air",
        fl_refs="FL-4265 FL-4431",
        fix_attempt="airplane dynamic-mover faces visible through perspective PixelCamera",
        move_frames=120,
        settle_frames=60,
        after_frames=12,
    ),
    # FL-4359 C7 REWORK: the full-grid oracle may only be attached to a route
    # that actually arms the jitter-capture writer at HEAD. The producer chain is
    # the DEFAULT route in fl4359_external_movement_driver.gd `_run()`:
    #   _reset_jitter_capture_before_active_route (driver:524 -> :2233, opens the
    #   JSONL writer at :2394) and _arm_sparse_capture_before_active_route
    #   (driver:555 -> :2268).
    # `_run()` reaches those two lines only when NO early-return route is chosen.
    # A ghost replay returns at driver:415 into _run_ghost_replay (:1859), which
    # never calls either, so it can never write jitter_capture.jsonl.
    Scenario(
        scenario_id="airplane_full_grid_oracle",
        label="Airplane full-grid temporal oracle (r21 identity)",
        camera_mode="plane_air",
        fl_refs="FL-4359 FL-4431",
        fix_attempt="exhaustive full-grid temporal oracle on the proven scripted_airborne_airplane_movement route",
        # plane_air takes its active window from --plane-circle-frames
        # (DEFAULT_AIRPLANE_MOVEMENT_FRAMES = 12 at run_fl4359_headed_movement.py:45),
        # which is the r21 active_frames=12. --move-frames is unused on this route.
        move_frames=120,
        settle_frames=2,
        # The full-grid profile requires exactly 2 (run_fl4359_headed_movement.py:3128).
        after_frames=2,
        # Reused from the recorded r21 command, which is the only identity known to
        # have produced a full-grid capture (5 frames x 32,092 subcells = 160,460 rows).
        runner_args=(
            "--exact-camera-projection",
            "perspective",
            "--jitter-capture-frame-limit",
            "5",
            "--product-time",
            "17",
            "--glyph-matrix-cell-px",
            "32",
        ),
        analyzer_proof_profile=FULL_GRID_ORACLE_PROFILE,
    ),
    Scenario(
        scenario_id="ground_turn_perspective",
        label="Perspective ground camera turn (full-grid oracle)",
        camera_mode="perspective",
        fl_refs="FL-4359 FL-4431",
        fix_attempt="ground camera rotation on the same default capture route, perspective variant of the r21 envelope",
        move_frames=120,
        settle_frames=2,
        after_frames=2,
        # DEFAULT_INITIAL_TURN_FRAMES in the runner is 8; the turn count is stated
        # explicitly here so the scenario identity does not drift with a default.
        # The capture envelope matches r21: 5 active frames + 1 release + 1 settle.
        runner_args=(
            "--full-grid-contract-mode",
            "camera_turn",
            "--exact-camera-projection",
            "perspective",
            "--turn-frames",
            "8",
            "--turn-action",
            "rotate_right",
            "--jitter-capture-frame-limit",
            "5",
            "--product-time",
            "17",
        ),
        analyzer_proof_profile=FULL_GRID_ORACLE_PROFILE,
        resolution="3840x2160",
    ),
    Scenario(
        scenario_id="tower_strafe_pose_envelope",
        label="Tower strafe recorded-pose envelope (validate-only)",
        camera_mode="perspective",
        fl_refs="FL-4359",
        fix_attempt="recorded tower waypoint envelope check; NOT a shimmer metric route",
        move_frames=120,
        settle_frames=2,
        after_frames=2,
        # Left-first strafe avoids the cliff fall at the tower.
        extra_user_args=("--fl4359-replay-strafe-start-left",),
        # --ghost-replay-validate-only is REQUIRED at HEAD. Without it the runner
        # force-appends --fl4359-moving-cell-dump (run_fl4359_headed_movement.py:2030),
        # whose driver handler (fl4359_external_movement_driver.gd:3709-3714) calls
        # ASCIICompositorEffect.fl4359_arm_moving_cell_dump — a method DELETED by
        # commit b9b8556e3 — and push_errors. validate-only skips that arm at
        # driver:1913. This scenario carries NO analyzer_proof_profile because the
        # ghost-replay route cannot arm the capture writer at all.
        runner_args=(
            "--ghost-replay-segment",
            "tower_strafe",
            "--ghost-replay-validate-only",
        ),
    ),
    # FL-4359 exact-translation oracle. The command declares five camera
    # positions, one yaw, and one pitch. `turn_frames=0` forbids rotation only.
    Scenario(
        scenario_id="pinned_translation_sweep_perspective",
        label="Pinned exact-pose translation sweep (full-grid oracle)",
        camera_mode="plane_air",
        fl_refs="FL-4359 FL-4216",
        fix_attempt="pose-deterministic full-grid temporal oracle for the OFF/ON retained-owner A/B",
        move_frames=120,
        settle_frames=2,
        after_frames=2,
        runner_args=(
            "--full-grid-contract-mode",
            "exact_translation",
            "--exact-pose-sweep",
            "--exact-pose-sweep-positions",
            TRANSLATION_SWEEP_POSITIONS,
            "--exact-camera-position",
            TRANSLATION_PAIR_P0,
            "--exact-camera-yaw",
            TRANSLATION_PAIR_YAW,
            "--exact-camera-pitch",
            TRANSLATION_PAIR_PITCH,
            "--exact-camera-projection",
            "perspective",
            "--exact-camera-fov",
            TRANSLATION_PAIR_FOV,
            # The sweep owns the camera, so no turn input participates.
            "--turn-frames",
            "0",
            "--jitter-capture-frame-limit",
            "5",
            "--product-time",
            "17",
        ),
        analyzer_proof_profile=FULL_GRID_ORACLE_PROFILE,
    ),
    Scenario(
        scenario_id="translation_pair_p0_p2",
        label="Exact-pose translation pair P0/P2 (x-delta 0.05)",
        camera_mode="perspective",
        fl_refs="FL-4359 FL-4216",
        fix_attempt="two single-frame captures of the B6 pose separated by 0.05 world units on x",
        move_frames=120,
        settle_frames=2,
        after_frames=2,
        runner_arg_sets=(
            ("p0", _translation_pair_args(TRANSLATION_PAIR_P0, "fl4359_translation_pair_p0")),
            ("p2", _translation_pair_args(TRANSLATION_PAIR_P2, "fl4359_translation_pair_p2")),
        ),
    ),
    # Identical experiment to `translation_pair_p0_p2` except for the second
    # camera position: the same recorded B6 pose, the same helper, the same
    # every-other-component-held-constant rule, with the x-delta widened from
    # 0.05 to 0.25. Anything else differing between the two scenarios would make
    # them different experiments instead of two points on one axis.
    Scenario(
        scenario_id="translation_pair_p0_p1",
        label="Exact-pose translation pair P0/P1 (x-delta 0.25)",
        camera_mode="perspective",
        fl_refs="FL-4359 FL-4216",
        fix_attempt="two single-frame captures of the B6 pose separated by 0.25 world units on x",
        move_frames=120,
        settle_frames=2,
        after_frames=2,
        runner_arg_sets=(
            ("p0", _translation_pair_args(TRANSLATION_PAIR_P0, "fl4359_translation_pair_p0")),
            ("p1", _translation_pair_args(TRANSLATION_PAIR_P1, "fl4359_translation_pair_p1")),
        ),
    ),
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git_text(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        errors="replace",
    )
    return result.stdout.strip()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _free_port(start: int) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scenario_invocations(scenario: Scenario) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return (sub_label, extra_runner_args) for every runner call this scenario needs.

    A scenario with no `runner_arg_sets` produces exactly one invocation, which
    keeps the pre-C7 one-command-per-scenario shape intact.
    """
    if scenario.runner_arg_sets:
        return tuple(
            (sub_label, tuple(scenario.runner_args) + tuple(extra))
            for sub_label, extra in scenario.runner_arg_sets
        )
    return (("", tuple(scenario.runner_args)),)


def _build_command(
    args: argparse.Namespace,
    scenario: Scenario,
    invocation_dir: Path,
    port: int,
    runner_args: tuple[str, ...],
) -> list[str]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--port",
        str(port),
        "--out",
        str(invocation_dir),
        "--godot-window-mode",
        "fullscreen" if args.fullscreen else "windowed",
        "--resolution",
        scenario.resolution or args.resolution,
        "--godot-timeout",
        str(args.godot_timeout),
        "--server-boot-timeout",
        str(args.server_boot_timeout),
        "--camera-mode",
        scenario.camera_mode,
        "--move-frames",
        str(scenario.move_frames),
        "--settle-frames",
        str(scenario.settle_frames),
        "--after-frames",
        str(scenario.after_frames),
        "--max-jitter-pct",
        str(args.max_jitter_pct),
        "--force-baseline-features",
        args.features,
    ]
    # FL-4359 C9: an oracle scenario must keep the analyzer armed. Skipping the
    # analysis or classifying the run diagnostic-only would drop both the defect
    # map and the runner's own `--fail-on-gate`, leaving nothing for C8 to read.
    if not args.with_jitter_analysis and not scenario.analyzer_proof_profile:
        cmd.extend([
            "--skip-jitter-analysis",
            "--run-classification",
            "diagnostic-only",
        ])
    if scenario.analyzer_proof_profile:
        cmd.extend(["--analyzer-proof-profile", scenario.analyzer_proof_profile])
        if scenario.analyzer_proof_profile == FULL_GRID_ORACLE_PROFILE:
            cmd.extend(["--day-cycle-time", FULL_GRID_ORACLE_DAY_CYCLE_TIME])
            # FL-4896 (operator 2026-09-28): the full-grid oracle is a fixed-cell
            # instrument and True FF (the default mode) refuses it. Oracle
            # scenarios run FC: flat FG, continuous BG. True FF temporal claims
            # use the text-frame flip route instead.
            cmd.extend(["--presentation-mode", FULL_GRID_ORACLE_PRESENTATION_MODE])
            # Full-grid proof requires the actual native/source and Canvas join.
            # Keep the allocation explicit: a missing budget is rejected before launch.
            cmd.extend([
                "--presentation-field-capture", "--canvas-provenance-capture",
                "--presentation-field-capture-budget-bytes",
                str(getattr(args, "presentation_field_capture_budget_bytes", 0)),
            ])
            # Stride-1 world and screen sampling is what makes the map exhaustive;
            # the resulting capture is large by construction, not by accident.
            cmd.append("--allow-large-jitter-capture")
    cmd.extend(runner_args)
    for extra_arg in scenario.extra_user_args:
        cmd.append(f"--extra-user-arg={extra_arg}")
    return cmd


def _run_invocation(
    args: argparse.Namespace,
    scenario: Scenario,
    invocation_dir: Path,
    port: int,
    runner_args: tuple[str, ...],
) -> dict:
    invocation_dir.mkdir(parents=True, exist_ok=True)
    cmd = _build_command(args, scenario, invocation_dir, port, runner_args)
    if args.dry_run:
        return {
            "scenario_id": scenario.scenario_id,
            "command": cmd,
            "returncode": None,
            "out_dir": str(invocation_dir),
            "dry_run": True,
        }
    scenario_dir = invocation_dir
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, errors="replace")
    (scenario_dir / "runner_stdout.txt").write_text(proc.stdout)
    (scenario_dir / "runner_stderr.txt").write_text(proc.stderr)
    receipt = _load_json(scenario_dir / "receipt.json")
    analysis = _load_json(scenario_dir / "analysis_summary.json")
    godot_log = scenario_dir / "godot_client.log"
    dynamic_records: list[dict[str, int]] = []
    if godot_log.exists():
        for line in godot_log.read_text(errors="replace").splitlines():
            match = DYNAMIC_RECORD_RE.search(line)
            if match:
                dynamic_records.append({
                    "bird_faces": int(match.group(1)),
                    "airplane_faces": int(match.group(2)),
                })
    return {
        "scenario_id": scenario.scenario_id,
        "label": scenario.label,
        "camera_mode": scenario.camera_mode,
        "fl_refs": scenario.fl_refs,
        "fix_attempt": scenario.fix_attempt,
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "out_dir": str(scenario_dir),
        "receipt": receipt,
        "analysis": analysis,
        "dynamic_records": dynamic_records,
        "max_bird_faces": max((row["bird_faces"] for row in dynamic_records), default=0),
        "max_airplane_faces_logged": max((row["airplane_faces"] for row in dynamic_records), default=0),
        "screenshots": receipt.get("screenshots", []),
    }


def _run_scenario(args: argparse.Namespace, scenario: Scenario, out_root: Path, port: int) -> dict:
    scenario_dir = out_root / scenario.scenario_id
    invocations = _scenario_invocations(scenario)
    if len(invocations) == 1:
        # Single-invocation scenarios keep the exact pre-C7 result shape so the
        # grid stitcher and the dry-run manifest are unchanged.
        return _run_invocation(args, scenario, scenario_dir, port, invocations[0][1])
    results = [
        _run_invocation(
            args,
            scenario,
            scenario_dir / sub_label,
            # Each sub-invocation launches its own Colyseus server, so it needs a
            # port of its own; the caller reserved a 10-wide band per scenario.
            port if idx == 0 else _free_port(port + idx),
            runner_args,
        )
        for idx, (sub_label, runner_args) in enumerate(invocations)
    ]
    returncodes = [row.get("returncode") for row in results]
    merged_rc: int | None
    if all(rc is None for rc in returncodes):
        merged_rc = None
    else:
        merged_rc = next((rc for rc in returncodes if rc not in (0, None)), 0)
    merged = dict(results[0])
    merged.update({
        "scenario_id": scenario.scenario_id,
        "label": scenario.label,
        "camera_mode": scenario.camera_mode,
        "fl_refs": scenario.fl_refs,
        "fix_attempt": scenario.fix_attempt,
        "out_dir": str(scenario_dir),
        "returncode": merged_rc,
        "commands": [row["command"] for row in results],
        "invocations": [
            {"sub_label": sub_label, **row}
            for (sub_label, _), row in zip(invocations, results)
        ],
    })
    return merged


# FL-4359 C8: the operator's acceptance surface is "every invalid splat quad is
# machine-detected", which reduces to an empty blocking-gate list and a
# zero-row full-grid defect map on EVERY oracle scenario. This roll-up is the
# single machine front door for that claim. Absent evidence is treated as a
# gate failure, never as a silent skip.
DEFECT_MAP_ROWS_KEY = "full_grid_temporal_oracle_defect_map_rows"
ADMISSIBILITY_REASONS_KEY = (
    "full_grid_temporal_oracle_finite_splat_admissibility_reasons"
)
STRUCTURAL_GEOMETRY_GATE = "gameplay_full_grid_zero_structural_geometry_errors"


def _admissibility_reasons(analysis: dict) -> dict:
    reasons = analysis.get(ADMISSIBILITY_REASONS_KEY)
    if isinstance(reasons, dict):
        return reasons
    gate = (analysis.get("gates") or {}).get(STRUCTURAL_GEOMETRY_GATE)
    if isinstance(gate, dict):
        nested = gate.get("finite_splat_admissibility_reasons")
        if isinstance(nested, dict):
            return nested
    return {}


def _oracle_analyses(scenario: Scenario, result: dict) -> list[tuple[str, dict]]:
    if not scenario.analyzer_proof_profile:
        return []
    rows = result.get("invocations")
    if isinstance(rows, list) and rows:
        return [
            (f"{scenario.scenario_id}/{row.get('sub_label') or '?'}", row.get("analysis") or {})
            for row in rows
        ]
    return [(scenario.scenario_id, result.get("analysis") or {})]


def _oracle_gate_rollup(manifest: dict) -> dict:
    by_id = {scenario.scenario_id: scenario for scenario in SCENARIOS}
    rows: list[dict] = []
    for result in manifest["scenarios"]:
        scenario = by_id.get(result.get("scenario_id", ""))
        if scenario is None:
            continue
        for name, analysis in _oracle_analyses(scenario, result):
            gates = analysis.get("gates")
            blocking = gates.get("blocking_failed_gates") if isinstance(gates, dict) else None
            defect_rows = analysis.get(DEFECT_MAP_ROWS_KEY)
            reasons = _admissibility_reasons(analysis)
            missing: list[str] = []
            if not analysis:
                missing.append("analysis_summary.json")
            if blocking is None:
                missing.append("gates.blocking_failed_gates")
            if not isinstance(defect_rows, int):
                missing.append(DEFECT_MAP_ROWS_KEY)
            gate_ok = (
                not missing
                and list(blocking) == []
                and int(defect_rows) == 0
            )
            rows.append({
                "name": name,
                "profile": scenario.analyzer_proof_profile,
                "blocking_failed_gates": blocking,
                "defect_map_rows": defect_rows,
                "missing_evidence": missing,
                "finite_splat_admissibility_reasons": reasons,
                "gate_ok": gate_ok,
            })
    for row in rows:
        print(
            f"[visual-owner-proof] oracle {row['name']}: gate_ok={row['gate_ok']} "
            f"defect_map_rows={row['defect_map_rows']} "
            f"blocking_failed_gates={row['blocking_failed_gates']} "
            f"missing_evidence={row['missing_evidence']}",
            flush=True,
        )
        reasons = row["finite_splat_admissibility_reasons"]
        if reasons:
            for reason, count in sorted(reasons.items(), key=lambda kv: (-int(kv[1]), kv[0])):
                print(
                    f"[visual-owner-proof]   finite_splat_admissibility_reason "
                    f"{reason}={count}",
                    flush=True,
                )
        else:
            print(
                "[visual-owner-proof]   finite_splat_admissibility_reasons: (none reported)",
                flush=True,
            )
    return {
        "scenarios": rows,
        "oracle_scenario_count": len(rows),
        "gate_ok": bool(rows) and all(row["gate_ok"] for row in rows),
    }


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        current = ""
        for word in raw.split(" "):
            candidate = word if not current else f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
        if current:
            lines.append(current)
    return lines


def _fit_image(path: Path, width: int, height: int):
    from PIL import Image

    image = Image.open(path).convert("RGB")
    scale = min(width / image.width, height / image.height)
    rw = max(1, int(round(image.width * scale)))
    rh = max(1, int(round(image.height * scale)))
    resized = image.resize((rw, rh), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (width, height), (9, 10, 12))
    panel.paste(resized, ((width - rw) // 2, (height - rh) // 2))
    return panel


def _stitch_grid(out_root: Path, manifest: dict) -> str | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    scenario_results = manifest["scenarios"]
    frame_names = ["00_before.png", "move_080.png", "99_after.png"]
    panel_w = 520
    image_h = 320
    label_h = 116
    header_h = 190
    cols = len(frame_names)
    rows = len(scenario_results)
    gutter = 12
    margin = 18
    width = margin * 2 + cols * panel_w + (cols - 1) * gutter
    height = header_h + margin + rows * (label_h + image_h) + (rows - 1) * gutter + margin
    canvas = Image.new("RGB", (width, height), (13, 15, 20))
    draw = ImageDraw.Draw(canvas)
    header_font = _font(18)
    label_font = _font(13)
    header = (
        "Godot visual owner runner | wake offset, falling leaves, world birds, airplane mesh | "
        f"HEAD {manifest['git']['head_short']} | dirty_count={manifest['git']['dirty_count']} | "
        f"{manifest['created_utc']}\n"
        "This is a generated visual evidence grid. Operator acceptance still requires visual signoff."
    )
    draw.rectangle((0, 0, width, header_h), fill=(28, 32, 42))
    y = 14
    for line in _wrap_text(draw, header, header_font, width - 28):
        draw.text((14, y), line, font=header_font, fill=(244, 244, 238))
        y += 26
    code_refs = (
        "CodeRefs: World.gd wake/airplane source facts; BirdFlock3D.gd bird_face facts; "
        "ASCIICompositorEffect.gd dynamic bird faces; cell_owner_product.glsl falling leaf product."
    )
    for line in _wrap_text(draw, code_refs, label_font, width - 28):
        draw.text((14, y + 8), line, font=label_font, fill=(210, 225, 245))
        y += 18

    for row_idx, result in enumerate(scenario_results):
        scenario_dir = Path(result["out_dir"])
        receipt = result.get("receipt", {})
        analysis = result.get("analysis", {})
        row_y = header_h + margin + row_idx * (label_h + image_h + gutter)
        for col_idx, frame in enumerate(frame_names):
            x = margin + col_idx * (panel_w + gutter)
            label_box = (x, row_y, x + panel_w, row_y + label_h)
            image_box_y = row_y + label_h
            draw.rectangle(label_box, fill=(24, 28, 38))
            frame_path = scenario_dir / frame
            if frame_path.exists():
                canvas.paste(_fit_image(frame_path, panel_w, image_h), (x, image_box_y))
            else:
                draw.rectangle((x, image_box_y, x + panel_w, image_box_y + image_h), fill=(70, 22, 25))
                draw.text((x + 10, image_box_y + 12), f"missing {frame}", font=label_font, fill=(255, 230, 230))
            label = (
                f"{result['label']} | {frame} | {result['fl_refs']}\n"
                f"rc={result['returncode']} receipt_pass={receipt.get('pass')} "
                f"jitter={float(analysis.get('max_pair_jitter_pct', 0.0) or 0.0):.2f}% "
                f"actor_rows={receipt.get('actor_frame_rect_row_count', 0)} "
                f"airplane_faces={receipt.get('airplane_dynamic_face_count', 0)} "
                f"bird_faces={result.get('max_bird_faces', 0)}\n"
                f"{result['fix_attempt']}"
            )
            ty = row_y + 8
            for line in _wrap_text(draw, label, label_font, panel_w - 16):
                if ty > row_y + label_h - 18:
                    break
                draw.text((x + 8, ty), line, font=label_font, fill=(242, 242, 236))
                ty += 16
    grid_path = out_root / "godot_visual_owner_proof_grid.png"
    canvas.save(grid_path)
    return str(grid_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run headed visual owner proof captures for active Godot fixes.")
    parser.add_argument("--out", default="")
    parser.add_argument("--port-start", type=int, default=2689)
    parser.add_argument("--resolution", default="1920x1080")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--godot-timeout", type=float, default=300.0)
    parser.add_argument("--server-boot-timeout", type=float, default=30.0)
    parser.add_argument("--features", default=DEFAULT_FEATURES)
    parser.add_argument("--max-jitter-pct", type=float, default=999.0)
    parser.add_argument(
        "--with-jitter-analysis",
        action="store_true",
        help="Also run FL-4359 jitter/content-shimmer analysis. Disabled by default for this visual grid runner.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--presentation-field-capture-budget-bytes", type=int, default=0,
                        help="Explicit observer allocation budget for full-grid proofs; requires a Canvas tracing engine.")
    args = parser.parse_args()
    args.fullscreen = not args.windowed

    out_root = Path(args.out) if args.out else ROOT / ".run" / "godot_visual_owner_proof" / _timestamp()
    if not out_root.is_absolute():
        out_root = (ROOT / out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_utc": _timestamp(),
        "runner": "scripts/run_godot_visual_owner_proof.py",
        "delegated_runner": "scripts/run_fl4359_headed_movement.py",
        "proof_state": "visual_evidence_generated_not_operator_accepted",
        "features": args.features,
        "jitter_analysis": "enabled" if args.with_jitter_analysis else "skipped_for_visual_grid",
        "git": {
            "head": _git_text("rev-parse", "HEAD"),
            "head_short": _git_text("rev-parse", "--short", "HEAD"),
            "dirty_count": len([line for line in _git_text("status", "--porcelain").splitlines() if line.strip()]),
        },
        "scenarios": [],
    }
    for idx, scenario in enumerate(SCENARIOS):
        print(f"[visual-owner-proof] running {scenario.scenario_id}", flush=True)
        port = _free_port(args.port_start + idx * 10)
        manifest["scenarios"].append(_run_scenario(args, scenario, out_root, port))
    grid = None if args.dry_run else _stitch_grid(out_root, manifest)
    manifest["grid"] = grid
    # FL-4359 C8: the cross-scenario oracle roll-up. Dry runs launch nothing, so
    # they have no analyzer summaries to read and no gate verdict to publish.
    rollup = None if args.dry_run else _oracle_gate_rollup(manifest)
    manifest["full_grid_oracle_rollup"] = rollup
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[visual-owner-proof] manifest={manifest_path}", flush=True)
    if grid:
        print(f"[visual-owner-proof] grid={grid}", flush=True)
    # Non-oracle scenarios keep their pre-C8 criterion: a clean runner exit.
    failed = [s for s in manifest["scenarios"] if s.get("returncode") not in (0, None)]
    if failed:
        return 1
    if rollup is not None and not rollup["gate_ok"]:
        print(
            "[visual-owner-proof] FAILED: not every oracle scenario reported "
            "blocking_failed_gates == [] and defect_map_rows == 0",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

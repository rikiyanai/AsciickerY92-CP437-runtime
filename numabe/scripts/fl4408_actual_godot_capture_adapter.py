#!/usr/bin/env python3
"""Adapter from canonical grid frame capture to the real Godot headed capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parents[1]
FRAME_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
MOVEMENT_SEQUENCE_FRAMES = ("00_before", "move_080", "99_after")
# The manifest wire value predates the airborne cut. Keeping the serialized
# value avoids making the manifest another ownership migration, while every
# executable branch below now requires the dogfight driver's airborne route.
NIGHT_AIRBORNE_SEQUENCE = "static_temporal_sequence"
NIGHT_AIRBORNE_SEQUENCE_FRAMES = ("beam_t0", "beam_t1", "beam_t2")
NIGHT_AIRBORNE_PHASE_OFFSETS = (0.0, 2.0943951023931953, 4.1887902047863905)
# Compatibility names are data-schema aliases only. They do not select the
# retired grounded driver and cannot relax the airborne receipt validator.
STATIC_TEMPORAL_SEQUENCE = NIGHT_AIRBORNE_SEQUENCE
STATIC_TEMPORAL_SEQUENCE_FRAMES = NIGHT_AIRBORNE_SEQUENCE_FRAMES
STATIC_TEMPORAL_PHASE_OFFSETS = NIGHT_AIRBORNE_PHASE_OFFSETS
DOGFIGHT_DRIVER = ROOT / "godot_project" / "tools" / "proofs" / "run_fl4265_airplane_dogfight_proof.gd"
NIGHT_CAPTURE_OWNER = "run_fl4265_airplane_dogfight_proof.gd"
NIGHT_ROUTE_IDENTITY = "server_tick_constant_physical_flight_input"
# The lighthouse rotates at pi/6 radians per second while the authoritative
# server advances at 30 ticks per second. A 120-tick interval is four seconds,
# which naturally advances the beam by 2pi/3 without any proof-owned mutation.
NIGHT_ROUTE_TICK_STRIDE = 120
NIGHT_ROUTE_TARGET_TICKS = (30, 150, 270)
NIGHT_SERVER_TICK_RATE_HZ = 30.0
# These seven fields are emitted beside each PNG by the airborne driver. The
# current runtime owns this readback ABI and must report positive counts.
# Immutable Baseline A predates the ABI, so it must report each key with JSON
# null instead of inventing a zero that could be mistaken for measured truth.
NIGHT_PRODUCT_COUNT_FIELDS = (
    "lighthouse_beam_contributed_subcell_count",
    "bonfire_flame_contributed_subcell_count",
    "bonfire_light_contributed_subcell_count",
    "bonfire_smoke_contributed_subcell_count",
    "lighthouse_tower_selected_subcell_count",
    "terrain_selected_subcell_count",
    "water_selected_subcell_count",
)
NIGHT_CAPTURE_SIDES = {"baseline", "current"}
RENDERER_ARM_LABELS = {
    "baseline": "Baseline A renderer + shared current server",
    "current": "current renderer + shared current server",
}
# A cached PNG can only be reused when the exact capture machinery is also
# unchanged. This aggregate deliberately includes the adapter, the process
# launcher, and the in-process driver because each file can change which bytes
# reach a canonical grid cell.
PROOF_TOOL_PATHS = (
    ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py",
    ROOT / "scripts" / "run_fl4359_headed_movement.py",
    DOGFIGHT_DRIVER,
)


def _reserve_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _git_output(source_root: Path, *arguments: str) -> str:
    """Run one read-only Git query and return its standard output.

    `source_root` is the checkout whose identity is being measured.
    `arguments` are passed directly to Git. A failed query is fatal because a
    canonical capture must never replace missing provenance with a guess.
    """
    result = subprocess.run(
        ["git", "-C", str(source_root), *arguments],
        text=True,
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git query failed"
        raise SystemExit(f"shared current Colyseus identity failed: {detail}")
    return result.stdout


def _aggregate_file_hash(paths: tuple[Path, ...]) -> str:
    """Hash ordered path names plus bytes into one proof-tool identity.

    Including names prevents two files with swapped contents from producing
    the same aggregate. Missing tools fail closed because a different driver
    set cannot be treated as the requested capture path.
    """
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_file():
            raise SystemExit(f"required proof tool is missing: {resolved}")
        digest.update(str(resolved).encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_scope_fingerprint(server_root: Path, git_root: Path) -> str:
    """Hash every tracked plus untracked file inside the selected server tree.

    The server commit alone cannot identify dirty runtime bytes. This scope
    fingerprint includes each repository-relative name, file contents, missing
    tracked-file markers, and symbolic-link targets so cache reuse changes when
    any server input changes.
    """
    scope = server_root.relative_to(git_root).as_posix()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(git_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            scope,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SystemExit(
            f"shared current Colyseus runtime scope could not be listed: {detail}"
        )
    names = sorted(
        name.decode("utf-8", errors="surrogateescape")
        for name in result.stdout.split(b"\0")
        if name
    )
    if not names:
        raise SystemExit("shared current Colyseus runtime scope is empty")
    digest = hashlib.sha256()
    for name in names:
        candidate = git_root / name
        digest.update(name.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if candidate.is_symlink():
            digest.update(b"SYMLINK\0")
            digest.update(candidate.readlink().as_posix().encode("utf-8"))
        elif candidate.is_file():
            digest.update(candidate.read_bytes())
        else:
            digest.update(b"MISSING")
        digest.update(b"\0")
    return digest.hexdigest()


def _shared_current_server_identity(value: str) -> dict[str, Any]:
    """Validate one explicit Colyseus source tree and fingerprint its bytes.

    `value` must be the absolute top-level `colyseus_server` directory inside
    one readable Git checkout. Project roots, relative aliases, missing paths,
    and nested lookalike directories are rejected because they leave the server
    owner ambiguous.
    """
    if not value:
        raise SystemExit("--colyseus-dir is required for canonical capture")
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise SystemExit("--colyseus-dir must be an absolute path")
    if ".." in requested.parts or requested.is_symlink():
        raise SystemExit("--colyseus-dir is an ambiguous path alias")
    if not requested.exists():
        raise SystemExit(f"--colyseus-dir does not exist: {requested}")
    if not requested.is_dir():
        raise SystemExit(f"--colyseus-dir is not a directory: {requested}")
    server_root = requested.resolve()
    if server_root.name != "colyseus_server":
        raise SystemExit(
            "--colyseus-dir must name the top-level colyseus_server directory"
        )
    for marker in ("package.json", "src/index.ts"):
        if not (server_root / marker).is_file():
            raise SystemExit(
                f"--colyseus-dir is not an executable Colyseus root; missing {marker}"
            )
    git_root_text = _git_output(server_root, "rev-parse", "--show-toplevel").strip()
    git_root = Path(git_root_text).resolve()
    expected_server_root = git_root / "colyseus_server"
    if server_root != expected_server_root:
        raise SystemExit(
            "--colyseus-dir is ambiguous; expected the checkout's one top-level "
            f"server root {expected_server_root}"
        )
    commit = _git_output(git_root, "rev-parse", "HEAD").strip()
    if (
        len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise SystemExit("shared current Colyseus commit is not a full Git identity")
    scope = server_root.relative_to(git_root).as_posix()
    dirty_text = _git_output(
        git_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        scope,
    )
    dirty_paths = [line.rstrip() for line in dirty_text.splitlines() if line.strip()]
    return {
        "server_commit": commit,
        "server_requested_root": value,
        "server_source_root": str(server_root),
        "server_dirty_paths": dirty_paths,
        "server_runtime_scope_fingerprint": _runtime_scope_fingerprint(
            server_root, git_root
        ),
        "proof_tool_sha256": _aggregate_file_hash(PROOF_TOOL_PATHS),
    }


def _capture_side_for_driver(side: str) -> str:
    return "current" if side == "current" else "baseline"


def _capture_signature(args: argparse.Namespace) -> dict[str, Any]:
    server_identity = getattr(args, "shared_current_server_identity", None)
    if not isinstance(server_identity, dict) or not server_identity:
        raise SystemExit(
            "shared current server identity is required before cache selection"
        )
    return {
        "lane_id": args.lane_id,
        "side": args.side,
        "frame": args.frame if args.capture_sequence == "single_frame" else None,
        "capture_sequence": args.capture_sequence,
        "project": str(Path(args.project).resolve()),
        "camera_mode": args.camera_mode,
        "view_mode": args.view_mode,
        "camera_position": json.loads(args.camera_position),
        "camera_yaw": float(args.camera_yaw),
        "camera_pitch": float(args.camera_pitch),
        "camera_roll": float(args.camera_roll),
        "camera_fov": float(args.camera_fov),
        "projection_params": json.loads(args.projection_params),
        "player_position": json.loads(args.player_position),
        "player_yaw": float(args.player_yaw),
        "npc_positions": json.loads(args.npc_positions),
        "airplane_position": json.loads(args.airplane_position),
        "airplane_yaw": float(args.airplane_yaw),
        "world_time": float(args.world_time),
        "night_state": bool(args.night_state),
        "feature_flags": json.loads(args.feature_flags),
        "fullscreen": bool(args.fullscreen),
        "resolution": args.resolution,
        "move_frames": int(args.move_frames),
        "settle_frames": int(args.settle_frames),
        "after_frames": int(args.after_frames),
        "plane_circle_frames": int(args.plane_circle_frames),
        # Cache reuse is legal only when the selected current server and every
        # capture tool byte are identical. This deletes the old renderer-only
        # cache identity that could silently reuse a historical-server frame.
        "shared_current_server_identity": dict(server_identity),
    }


def _normalized_day_cycle_time(world_time: str) -> float:
    """Convert a manifest hour to the renderer's normalized day-cycle clock."""
    hour = float(world_time)
    if not math.isfinite(hour) or hour < 0.0 or hour >= 24.0:
        raise SystemExit(f"world_time must be a finite hour in [0, 24): {world_time!r}")
    return hour / 24.0


def _feature_names(feature_flags: str) -> set[str]:
    """Parse the manifest feature list without inventing defaults for proof gates."""
    try:
        values = json.loads(feature_flags)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"feature_flags must be valid JSON: {exc}") from exc
    if not isinstance(values, list) or not values:
        raise SystemExit("feature_flags must be a non-empty JSON string list")
    if not all(isinstance(value, str) and value for value in values):
        raise SystemExit("feature_flags must contain only non-empty strings")
    return set(values)


def _signature_hash(signature: dict[str, Any]) -> str:
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_feature_csv(feature_flags: str, night_state: bool) -> str:
    """Translate manifest facts into the one runtime baseline-feature list."""
    try:
        values = json.loads(feature_flags)
    except json.JSONDecodeError:
        values = ["foliage_cards", "water_current_package", "material_ramp_steps", "cloud_shadow_deep"]
    if not isinstance(values, list):
        values = ["foliage_cards", "water_current_package", "material_ramp_steps", "cloud_shadow_deep"]
    # `night_state` is the manifest's sole night owner. Remove a redundant
    # feature-list copy first, then derive exactly one runtime selector from
    # the boolean fact so metadata and renderer state cannot disagree.
    features = [str(value) for value in values if str(value) not in {"ascii", "night"}]
    if night_state:
        features.append("night")
    return ",".join(features)


def _exact_camera_size(projection_params: str) -> str:
    try:
        values = json.loads(projection_params)
    except json.JSONDecodeError:
        return ""
    if not isinstance(values, dict):
        return ""
    for key in ("size", "camera_size", "ortho_size"):
        if key in values:
            return str(values[key])
    return ""


def _frame_file(frame_label: str) -> str:
    if not FRAME_LABEL_RE.match(frame_label):
        raise SystemExit(f"invalid frame label for capture filename: {frame_label!r}")
    return f"{frame_label}.png"


def _resolution_tuple(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip())
    if not match:
        raise SystemExit(f"invalid capture resolution: {value!r}")
    return int(match.group(1)), int(match.group(2))


def _scene_content_sentinel(image: Image.Image) -> dict[str, Any]:
    rgb = image.convert("RGB")
    sample_w = max(1, min(320, rgb.width))
    sample_h = max(1, min(180, rgb.height))
    sampled = rgb.resize((sample_w, sample_h), Image.Resampling.BILINEAR)
    stat = ImageStat.Stat(sampled)
    channel_ranges = [int(hi - lo) for lo, hi in stat.extrema]
    raw_pixels = sampled.tobytes()
    pixels = [
        (raw_pixels[i], raw_pixels[i + 1], raw_pixels[i + 2])
        for i in range(0, len(raw_pixels), 3)
    ]
    luma_values = [
        int(round(0.2126 * r + 0.7152 * g + 0.0722 * b))
        for r, g, b in pixels
    ]
    luma_min = min(luma_values) if luma_values else 0
    luma_max = max(luma_values) if luma_values else 0
    luma_range = int(luma_max - luma_min)
    luma_mean = sum(luma_values) / max(1, len(luma_values))
    luma_var = sum((v - luma_mean) ** 2 for v in luma_values) / max(1, len(luma_values))
    luma_stddev = luma_var ** 0.5
    unique_sampled_colors = len(set(pixels))
    passed = (
        unique_sampled_colors >= 24
        and luma_range >= 24
        and luma_stddev >= 6.0
        and max(channel_ranges) >= 18
    )
    return {
        "passed": passed,
        "unique_sampled_colors": unique_sampled_colors,
        "luma_range": luma_range,
        "luma_stddev": round(luma_stddev, 3),
        "channel_ranges": channel_ranges,
        "sample_size": [sample_w, sample_h],
        "thresholds": {
            "unique_sampled_colors_min": 24,
            "luma_range_min": 24,
            "luma_stddev_min": 6.0,
            "max_channel_range_min": 18,
        },
    }


def _required_frame_files(args: argparse.Namespace) -> tuple[str, ...]:
    """Return files whose names are owned directly by the selected driver.

    The airborne night driver names files by authoritative route tick. Its
    receipt maps those paths to the three operator-facing beam labels, so the
    adapter must not revive grounded `beam_t*.png` files as a second owner.
    """
    if args.capture_sequence == "movement_sequence":
        return tuple(_frame_file(label) for label in MOVEMENT_SEQUENCE_FRAMES)
    if args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE:
        return ()
    return (_frame_file(args.frame),)


def _temporal_frame_receipt(receipt: dict[str, Any], frame_label: str) -> dict[str, Any]:
    """Resolve one operator label to the same-process dogfight frame record."""
    frames = receipt.get("visual_frame_samples")
    if not isinstance(frames, list):
        return {}
    for frame in frames:
        if isinstance(frame, dict) and frame.get("label") == frame_label:
            return frame
    return {}


def _numeric_vector(value: Any) -> tuple[float, float, float] | None:
    """Decode one runtime position without accepting stringified placeholders."""
    if not isinstance(value, dict):
        return None
    coords = tuple(value.get(axis) for axis in ("x", "y", "z"))
    if not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in coords):
        return None
    return float(coords[0]), float(coords[1]), float(coords[2])


def _positive_count(frame: dict[str, Any], key: str) -> bool:
    """Return true only when current reports a measured structural count."""
    value = frame.get(key)
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _wrapped_positive_angle_delta(first: float, current: float) -> float:
    """Measure `current` counter-clockwise from `first` in the 0..2pi range.

    Lighthouse azimuth wraps from 2pi back to zero. A plain subtraction would
    turn that ordinary wrap into a large negative jump, so modulo expresses the
    directed difference in the positive one-turn interval used by the requested
    offsets. This function observes renderer output only; it never changes beam
    state.
    """
    return (current - first) % math.tau


def _validate_airborne_night_temporal_receipt(
    receipt: dict[str, Any],
    capture_root: Path | None = None,
    *,
    expected_capture_side: str,
) -> list[str]:
    """Validate one authoritative airborne route plus matching frame truth.

    The validator is intentionally strict. A PNG path, a server route row, and
    the lighting/environment product facts must all live in each frame sample.
    This prevents the deleted grounded capture plus a later diagnostic process
    from being stitched into a plausible package.
    """
    errors: list[str] = []
    receipt_capture_side = receipt.get("capture_side")
    count_contract_side: str | None = None
    # The caller's manifest-selected side is authoritative. The driver must
    # echo that side into the same-process receipt before this validator chooses
    # the Baseline A null contract or the current positive-count contract.
    # A receipt cannot gain the weaker Baseline A rule by relabeling itself.
    if expected_capture_side not in NIGHT_CAPTURE_SIDES:
        errors.append(f"expected capture side is invalid: {expected_capture_side}")
    elif receipt_capture_side != expected_capture_side:
        errors.append(
            f"receipt capture_side={receipt_capture_side} does not match "
            f"expected {expected_capture_side}"
        )
    else:
        count_contract_side = expected_capture_side
    if receipt.get("pass") is not True:
        errors.append("receipt pass is not true")
    if receipt.get("night_airborne_temporal_sequence") is not True:
        errors.append("night_airborne_temporal_sequence is not true")
    if receipt.get("route_identity") != NIGHT_ROUTE_IDENTITY:
        errors.append("route_identity is not the server-tick physical-flight route")
    if receipt.get("visual_capture_owner") != NIGHT_CAPTURE_OWNER:
        errors.append("visual_capture_owner is not the dogfight driver")
    if receipt.get("airborne_airplane_ready") is not True:
        errors.append("airborne airplane readiness is unproven")
    if receipt.get("local_airplane_pilot") is not True:
        errors.append("local airplane pilot ownership is unproven")
    if receipt.get("airplane_world_item_count_exact") is not True:
        errors.append("authoritative airplane world-item count is unproven")
    if receipt.get("clean_play_no_capture") is not False:
        errors.append("visual receipt is incorrectly marked clean_play_no_capture")
    if receipt.get("visual_route_ticks_exact") is not True:
        errors.append("visual route ticks are not exact")
    if receipt.get("missing_visual_route_ticks") != []:
        errors.append("visual route reports missing target ticks")
    capture_id = receipt.get("temporal_capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        errors.append("temporal_capture_id is missing")
    server_tick_rate = receipt.get("server_tick_rate_hz")
    if (
        not isinstance(server_tick_rate, (int, float))
        or isinstance(server_tick_rate, bool)
        or not math.isfinite(float(server_tick_rate))
        or abs(float(server_tick_rate) - NIGHT_SERVER_TICK_RATE_HZ) > 0.000001
    ):
        errors.append("server_tick_rate_hz must be exactly 30 for phase spacing")
    target_ticks = receipt.get("visual_route_target_ticks")
    if (
        not isinstance(target_ticks, list)
        or len(target_ticks) != len(NIGHT_AIRBORNE_SEQUENCE_FRAMES)
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in target_ticks)
        or tuple(target_ticks) != NIGHT_ROUTE_TARGET_TICKS
    ):
        errors.append(
            "visual_route_target_ticks must be exactly 30, 150, 270"
        )
        target_ticks = []
    frames = receipt.get("visual_frame_samples")
    if not isinstance(frames, list) or len(frames) != len(NIGHT_AIRBORNE_SEQUENCE_FRAMES):
        errors.append("visual_frame_samples must contain exactly three rows")
        return errors
    if receipt.get("visual_frame_count") != len(NIGHT_AIRBORNE_SEQUENCE_FRAMES):
        errors.append("visual_frame_count is not exactly three")
    declared_paths = receipt.get("visual_frame_paths")
    sample_paths = [
        frame.get("path") if isinstance(frame, dict) else None
        for frame in frames
    ]
    if declared_paths != sample_paths:
        errors.append("visual_frame_paths do not match the same-process frame rows")
    observed_azimuths: list[float] = []
    observed_offsets: list[float] = []
    observed_product_frame_times: list[float] = []
    for index, label in enumerate(NIGHT_AIRBORNE_SEQUENCE_FRAMES):
        frame = frames[index]
        if not isinstance(frame, dict):
            errors.append(f"{label} visual row is not an object")
            continue
        if frame.get("label") != label or frame.get("frame_index") != index:
            errors.append(f"{label} visual row order is wrong")
        route_tick = frame.get("route_tick")
        if not target_ticks or route_tick != target_ticks[index]:
            errors.append(f"{label} route_tick does not match the declared target")
        server_tick = frame.get("authority_server_tick")
        if (
            not isinstance(server_tick, int)
            or isinstance(server_tick, bool)
            or not isinstance(route_tick, int)
        ):
            errors.append(f"{label} authority_server_tick is missing")
        if frame.get("temporal_capture_id") != capture_id:
            errors.append(f"{label} temporal_capture_id differs from the run")
        if frame.get("png_capture_owner") != NIGHT_CAPTURE_OWNER:
            errors.append(f"{label} PNG owner is not the dogfight process")
        if frame.get("png_captured_in_same_process") is not True:
            errors.append(f"{label} same-process PNG truth is unproven")
        frame_path = frame.get("path")
        if not isinstance(frame_path, str) or not frame_path:
            errors.append(f"{label} PNG path is missing")
        elif capture_root is not None:
            resolved_path = Path(frame_path)
            if not resolved_path.is_absolute():
                resolved_path = capture_root / resolved_path
            if not resolved_path.is_file():
                errors.append(f"{label} same-process PNG is absent: {resolved_path}")
        if frame.get("airborne_airplane_ready") is not True:
            errors.append(f"{label} airborne readiness is unproven")
        if frame.get("lighthouse_record_published") is not True:
            errors.append(f"{label} lighthouse kind-4 record is unproven")
        if frame.get("bonfire_record_published") is not True:
            errors.append(f"{label} bonfire kind-1 record is unproven")
        if frame.get("firefly_record_published") is not True:
            errors.append(f"{label} firefly kind-2 record is unproven")
        firefly_record_count = frame.get("firefly_record_count")
        if (
            not isinstance(firefly_record_count, int)
            or isinstance(firefly_record_count, bool)
            or firefly_record_count <= 0
        ):
            errors.append(
                f"{label} firefly_record_count is absent or nonpositive"
            )
        product_frame_time = frame.get("product_frame_time_seconds")
        if (
            not isinstance(product_frame_time, (int, float))
            or isinstance(product_frame_time, bool)
            or not math.isfinite(float(product_frame_time))
        ):
            errors.append(
                f"{label} product_frame_time_seconds is absent or nonfinite"
            )
        else:
            product_frame_time_value = float(product_frame_time)
            if (
                observed_product_frame_times
                and product_frame_time_value <= observed_product_frame_times[-1]
            ):
                errors.append(
                    f"{label} product_frame_time_seconds is not strictly increasing"
                )
            observed_product_frame_times.append(product_frame_time_value)
        azimuth = frame.get("lighthouse_beam_azimuth")
        if not isinstance(azimuth, (int, float)) or not math.isfinite(float(azimuth)):
            errors.append(f"{label} lighthouse azimuth is missing")
        else:
            observed_azimuths.append(float(azimuth))
        requested_offset = frame.get("beam_phase_offset_target")
        measured_offset = frame.get("beam_phase_offset_measured")
        if (
            not isinstance(requested_offset, (int, float))
            or not math.isfinite(float(requested_offset))
            or abs(
                _wrapped_positive_angle_delta(
                    0.0,
                    float(requested_offset),
                )
                - NIGHT_AIRBORNE_PHASE_OFFSETS[index]
            )
            > 0.000001
        ):
            errors.append(f"{label} requested phase offset is wrong")
        if (
            not isinstance(measured_offset, (int, float))
            or not math.isfinite(float(measured_offset))
        ):
            errors.append(f"{label} measured phase offset is missing")
        else:
            observed_offsets.append(float(measured_offset))
        for pose_key in ("player_position", "camera_position", "airplane_position"):
            if _numeric_vector(frame.get(pose_key)) is None:
                errors.append(f"{label} {pose_key} is missing")
        airplane_position = _numeric_vector(frame.get("airplane_position"))
        if airplane_position is not None and (
            airplane_position[1] <= 0.0
            or math.sqrt(sum(component * component for component in airplane_position)) <= 0.0
        ):
            errors.append(f"{label} airborne airplane position is not nonzero above terrain")
        # Count interpretation happens only after the receipt has proven the
        # same side selected by the manifest caller. Missing side identity has
        # already failed above and cannot silently select either ABI contract.
        if count_contract_side == "current":
            for count_key in NIGHT_PRODUCT_COUNT_FIELDS:
                if not _positive_count(frame, count_key):
                    errors.append(
                        f"{label} {count_key} is absent or zero for current"
                    )
        elif count_contract_side == "baseline":
            for count_key in NIGHT_PRODUCT_COUNT_FIELDS:
                if count_key not in frame:
                    errors.append(
                        f"{label} {count_key} is missing for Baseline A"
                    )
                elif frame[count_key] is not None:
                    errors.append(
                        f"{label} {count_key} must be explicit null for Baseline A"
                    )
    if len(observed_azimuths) == len(NIGHT_AIRBORNE_SEQUENCE_FRAMES):
        first_azimuth = observed_azimuths[0]
        recomputed_offsets = [
            _wrapped_positive_angle_delta(first_azimuth, value)
            for value in observed_azimuths
        ]
        if len(observed_offsets) == len(NIGHT_AIRBORNE_SEQUENCE_FRAMES):
            for index, measured_offset in enumerate(observed_offsets):
                if abs(measured_offset - recomputed_offsets[index]) > 0.000001:
                    errors.append(
                        f"{NIGHT_AIRBORNE_SEQUENCE_FRAMES[index]} measured "
                        "phase offset does not match the observed azimuths"
                    )
                phase_error = abs(
                    math.atan2(
                        math.sin(
                            measured_offset
                            - NIGHT_AIRBORNE_PHASE_OFFSETS[index]
                        ),
                        math.cos(
                            measured_offset
                            - NIGHT_AIRBORNE_PHASE_OFFSETS[index]
                        ),
                    )
                )
                if phase_error > 0.08:
                    errors.append(
                        f"{NIGHT_AIRBORNE_SEQUENCE_FRAMES[index]} natural "
                        "phase offset missed its requested interval"
                    )
    return errors


def _run_capture(args: argparse.Namespace, run_dir: Path, signature: dict[str, Any], signature_hash: str) -> None:
    """Run the single capture owner selected by the manifest lane.

    Night temporal evidence is forced onto the server-authoritative airplane
    route. Other lanes retain their existing driver because this ownership cut
    is intentionally limited to FL-4431 night evidence.
    """
    receipt = run_dir / "receipt.json"
    complete_marker = run_dir / "complete_capture.json"
    required_frames = _required_frame_files(args)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    camera_mode = args.camera_mode
    driver_script = ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"
    night_airborne_capture = (
        args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE
        and args.camera_mode == "night_lighthouse_beam"
    )
    if args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE and not night_airborne_capture:
        raise SystemExit(
            "the temporal night sequence is owned only by "
            "camera_mode=night_lighthouse_beam on the airborne dogfight route"
        )
    exact_camera_projection = args.view_mode
    if night_airborne_capture:
        camera_mode = "plane_air"
        driver_script = DOGFIGHT_DRIVER
    elif camera_mode == "airplane_air_movement_perspective":
        camera_mode = "plane_air"
    elif camera_mode == "airplane_air_movement_ortho":
        camera_mode = "plane_air_ortho"
    elif camera_mode not in {"ortho", "perspective", "plane_air", "plane_air_ortho"}:
        camera_mode = args.view_mode
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_fl4359_headed_movement.py"),
        "--out",
        str(run_dir),
        "--godot-project-dir",
        args.project,
        "--colyseus-dir",
        # The adapter validated this one absolute current-server root before
        # selecting either renderer arm. Forward the caller's exact spelling so
        # both arms receive one byte-identical command-line owner.
        args.colyseus_dir,
        "--camera-mode",
        camera_mode,
        "--driver-script",
        str(driver_script),
        "--godot-window-mode",
        "fullscreen" if args.fullscreen else "windowed",
        "--resolution",
        args.resolution,
        "--force-baseline-features",
        _runtime_feature_csv(args.feature_flags, args.night_state),
    ]
    # Exact grounded transforms belong to static content comparisons. The
    # airborne night route consumes the server's airplane pose and camera pose,
    # so forwarding these manifest coordinates would revive a second owner.
    if not night_airborne_capture:
        command.extend(
            [
                "--exact-player-position",
                args.player_position,
                "--exact-camera-position",
                args.camera_position,
                "--exact-camera-yaw",
                args.camera_yaw,
                "--exact-camera-pitch",
                args.camera_pitch,
                "--exact-camera-projection",
                exact_camera_projection,
                "--exact-camera-fov",
                args.camera_fov,
            ]
        )
        exact_camera_size = _exact_camera_size(args.projection_params)
        if exact_camera_size:
            command.extend(["--exact-camera-size", exact_camera_size])
    command.extend([
        "--port",
        str(_reserve_free_port()),
        "--move-frames",
        str(args.move_frames),
        "--settle-frames",
        str(args.settle_frames),
        "--after-frames",
        str(args.after_frames),
        "--plane-circle-frames",
        str(args.plane_circle_frames),
        "--godot-timeout",
        str(args.godot_timeout),
        "--max-jitter-pct",
        str(args.max_jitter_pct),
        "--capture-side",
        _capture_side_for_driver(args.side),
        "--skip-jitter-analysis",
        "--run-classification",
        "diagnostic-only",
    ])
    # The manifest expresses clock time in hours. The renderer consumes one
    # normalized 0..1 cycle, so forward the conversion into the real Godot
    # process instead of merely repeating the requested hour in metadata.
    command.extend(["--day-cycle-time", str(_normalized_day_cycle_time(args.world_time))])
    if args.capture_sequence == "single_frame":
        command.extend(["--single-frame", "--single-frame-label", args.frame])
    elif night_airborne_capture:
        command.extend(
            [
                "--extra-user-arg=--fl4431-night-airborne-temporal-sequence",
                "--extra-user-arg=--fl4431-night-temporal-labels="
                + ",".join(NIGHT_AIRBORNE_SEQUENCE_FRAMES),
                "--extra-user-arg=--fl4431-night-temporal-phase-offsets="
                + ",".join(
                    f"{offset:.9f}"
                    for offset in NIGHT_AIRBORNE_PHASE_OFFSETS
                ),
                f"--extra-user-arg=--fl4359-visual-capture-stride={NIGHT_ROUTE_TICK_STRIDE}",
                "--extra-user-arg=--fl4359-visual-capture-limit=3",
                "--extra-user-arg=--fl4359-visual-capture-start-frame=0",
            ]
        )
        # Baseline A predates the normal-world bonfire owner. Its historical
        # runtime needs this retired launch switch to reproduce the immutable
        # receipt. Current is never given the switch, so the old owner cannot
        # leak into present-day behavior.
        if args.side != "current" and args.night_state and "bonfire_light" in _feature_names(args.feature_flags):
            command.append("--extra-user-arg=--fl4203-night-bonfire")
    result = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False)
    (run_dir / "adapter_command.json").write_text(
        json.dumps(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    missing = [name for name in required_frames if not (run_dir / name).exists()]
    receipt_data = _load_json(receipt)
    temporal_errors = (
        _validate_airborne_night_temporal_receipt(
            receipt_data,
            run_dir,
            expected_capture_side=_capture_side_for_driver(args.side),
        )
        if night_airborne_capture and receipt.exists()
        else []
    )
    if result.returncode != 0 or not receipt.exists() or missing or receipt_data.get("pass") is not True or temporal_errors:
        raise SystemExit(
            "actual Godot capture did not complete the required manifest frame "
            f"for {args.lane_id}:{args.side}; missing={missing}; receipt={receipt.exists()}; "
            f"temporal_errors={temporal_errors}; rc={result.returncode}; "
            f"stdout={result.stdout[-1200:]}; stderr={result.stderr[-1200:]}"
        )
    complete_marker.write_text(
        json.dumps(
            {
                "lane_id": args.lane_id,
                "side": args.side,
                "frame": args.frame,
                "capture_sequence": args.capture_sequence,
                "frames": (
                    list(NIGHT_AIRBORNE_SEQUENCE_FRAMES)
                    if night_airborne_capture
                    else list(required_frames)
                ),
                "receipt": str(receipt),
                "command_returncode": result.returncode,
                "capture_signature_hash": signature_hash,
                "capture_signature": signature,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _vector_from_receipt(value: Any) -> list[float] | None:
    if not isinstance(value, dict):
        return None
    coords = []
    for key in ("x", "y", "z"):
        item = value.get(key)
        if not isinstance(item, (int, float)):
            return None
        coords.append(float(item))
    return coords


def _receipt_vector_for_frame(receipt: dict[str, Any], frame_label: str, before_key: str, after_key: str) -> list[float] | None:
    temporal_frame = _temporal_frame_receipt(receipt, frame_label)
    if temporal_frame:
        temporal_key = {
            "player_before": "player_position",
            "camera_before": "camera_position",
            "airplane_before": "airplane_position",
        }.get(before_key)
        if temporal_key is not None:
            return _vector_from_receipt(temporal_frame.get(temporal_key))
    if receipt.get("single_frame") is True:
        return _vector_from_receipt(receipt.get(before_key))
    if frame_label == "00_before":
        return _vector_from_receipt(receipt.get(before_key))
    if frame_label == "move_080":
        midpoint_key = before_key.replace("_before", "_move_080")
        return _vector_from_receipt(receipt.get(midpoint_key))
    if frame_label == "99_after":
        return _vector_from_receipt(receipt.get(after_key))
    return None


def _metadata(
    args: argparse.Namespace,
    run_dir: Path,
    src: Path,
    actual_size: tuple[int, int],
    signature_hash: str,
    frame_label: str,
    scene_content_sentinel: dict[str, Any],
) -> dict[str, Any]:
    receipt = _load_json(run_dir / "receipt.json")
    analysis = _load_json(run_dir / "analysis_summary.json")
    complete_path = run_dir / "complete_capture.json"
    temporal_frame = _temporal_frame_receipt(receipt, frame_label)
    runtime_player = _receipt_vector_for_frame(receipt, frame_label, "player_before", "player_after")
    runtime_camera = _receipt_vector_for_frame(receipt, frame_label, "camera_before", "camera_after")
    runtime_airplane = _receipt_vector_for_frame(receipt, frame_label, "airplane_before", "airplane_after")
    frame_final_meta = temporal_frame.get("final_render_metadata")
    if not isinstance(frame_final_meta, dict):
        frame_final_meta = receipt.get("final_render_metadata")
    if not isinstance(frame_final_meta, dict):
        frame_final_meta = {}
    server_identity = dict(args.shared_current_server_identity)
    renderer_arm_label = RENDERER_ARM_LABELS.get(args.side)
    if renderer_arm_label is None:
        raise SystemExit(f"canonical capture side has no renderer label: {args.side}")
    return {
        "ascii_enabled": True,
        "fullscreen": bool(args.fullscreen),
        "capture_mode": args.camera_mode,
        "capture_sequence": args.capture_sequence,
        "view_mode": args.view_mode,
        "camera_position": runtime_camera or json.loads(args.camera_position),
        "requested_camera_position": json.loads(args.camera_position),
        "camera_yaw": float(args.camera_yaw),
        "camera_pitch": float(args.camera_pitch),
        "camera_roll": float(args.camera_roll),
        "camera_fov": float(args.camera_fov),
        "projection_params": json.loads(args.projection_params),
        "player_position": runtime_player or json.loads(args.player_position),
        "requested_player_position": json.loads(args.player_position),
        "player_yaw": float(args.player_yaw),
        "npc_positions": json.loads(args.npc_positions),
        "airplane_position": runtime_airplane or json.loads(args.airplane_position),
        "requested_airplane_position": json.loads(args.airplane_position),
        "airplane_yaw": float(args.airplane_yaw),
        "world_time": float(args.world_time),
        "night_state": bool(args.night_state),
        "feature_flags": json.loads(args.feature_flags),
        # Every value below was sampled in the process that saved `src`.
        # Positive counts are structural product truth; the PNG grid plus
        # operator inspection remains the sole visible acceptance surface.
        "lighthouse_beam_contributed_subcell_count": temporal_frame.get(
            "lighthouse_beam_contributed_subcell_count"
        ),
        "bonfire_flame_contributed_subcell_count": temporal_frame.get(
            "bonfire_flame_contributed_subcell_count"
        ),
        "bonfire_light_contributed_subcell_count": temporal_frame.get(
            "bonfire_light_contributed_subcell_count"
        ),
        "bonfire_smoke_contributed_subcell_count": temporal_frame.get(
            "bonfire_smoke_contributed_subcell_count"
        ),
        "lighthouse_tower_selected_subcell_count": temporal_frame.get(
            "lighthouse_tower_selected_subcell_count"
        ),
        "terrain_selected_subcell_count": temporal_frame.get(
            "terrain_selected_subcell_count"
        ),
        "water_selected_subcell_count": temporal_frame.get(
            "water_selected_subcell_count"
        ),
        "lighthouse_record_published": temporal_frame.get("lighthouse_record_published"),
        "bonfire_record_published": temporal_frame.get("bonfire_record_published"),
        "lighthouse_beam_azimuth": temporal_frame.get("lighthouse_beam_azimuth"),
        "lighthouse_beam_start_world": temporal_frame.get("lighthouse_beam_start_world"),
        "lighthouse_beam_end_world": temporal_frame.get("lighthouse_beam_end_world"),
        "beam_phase_offset_target": temporal_frame.get(
            "beam_phase_offset_target"
        ),
        "beam_phase_offset_measured": temporal_frame.get(
            "beam_phase_offset_measured"
        ),
        "route_tick": temporal_frame.get("route_tick"),
        "authority_server_tick": temporal_frame.get("authority_server_tick"),
        "server_tick_rate_hz": receipt.get("server_tick_rate_hz"),
        "png_capture_owner": temporal_frame.get("png_capture_owner"),
        "png_captured_in_same_process": temporal_frame.get(
            "png_captured_in_same_process"
        ),
        "temporal_capture_id": receipt.get("temporal_capture_id"),
        "temporal_frame_index": temporal_frame.get("frame_index"),
        "temporal_elapsed_seconds": temporal_frame.get(
            "elapsed_since_measured_start_seconds"
        ),
        "temporal_continuous_run": (
            receipt.get("night_airborne_temporal_sequence") is True
        ),
        "canonical_review_capture": (
            args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE
        ),
        "actual_godot_capture": True,
        "capture_resolution": f"{actual_size[0]}x{actual_size[1]}",
        "requested_capture_resolution": args.resolution,
        "source_frame": str(src),
        "receipt_path": str(run_dir / "receipt.json"),
        "complete_capture_path": str(complete_path),
        "capture_signature_hash": signature_hash,
        "receipt_pass": bool(receipt.get("pass", False)),
        "receipt_reason": receipt.get("reason", ""),
        "runtime_player_position": runtime_player,
        "runtime_camera_position": runtime_camera,
        "runtime_airplane_position": runtime_airplane,
        "movement_delta": receipt.get("movement_delta"),
        "camera_delta": receipt.get("camera_delta"),
        "airplane_dynamic_face_count": receipt.get("airplane_dynamic_face_count"),
        "max_pair_jitter_pct": analysis.get("max_pair_jitter_pct"),
        "final_cell_paint_state": frame_final_meta.get("final_cell_paint_state", receipt.get("final_cell_paint_state")),
        "foliage_mask_state": frame_final_meta.get("foliage_mask_state", receipt.get("foliage_mask_state")),
        "ansi_size": frame_final_meta.get("ansi_size", receipt.get("ansi_size")),
        "cell_dims": frame_final_meta.get("cell_dims", receipt.get("cell_dims")),
        "viewport_size": frame_final_meta.get("viewport_size", receipt.get("viewport_size")),
        "display": frame_final_meta.get("display", receipt.get("display")),
        "final_render_metadata": frame_final_meta,
        "proof_state": (
            "canonical_review_capture_partial"
            if args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE
            else "actual_godot_capture"
        ),
        "scene_content_sentinel": scene_content_sentinel,
        # These fields bind every operator-facing PNG to the same explicit
        # current Colyseus tree. The nested object is convenient for equality
        # checks; duplicated top-level fields keep receipts easy to inspect.
        "renderer_arm_label": renderer_arm_label,
        "shared_current_server_identity": server_identity,
        **server_identity,
    }


def _write_output_frame(args: argparse.Namespace, run_dir: Path, signature_hash: str, frame_label: str, out_png: Path, out_meta: Path) -> None:
    """Copy one real driver frame into the operator-facing labeled package."""
    if args.capture_sequence == NIGHT_AIRBORNE_SEQUENCE:
        receipt = _load_json(run_dir / "receipt.json")
        temporal_frame = _temporal_frame_receipt(receipt, frame_label)
        source_value = temporal_frame.get("path")
        source = Path(source_value) if isinstance(source_value, str) else Path()
        if source_value and not source.is_absolute():
            source = run_dir / source
    else:
        source = run_dir / _frame_file(frame_label)
    if not source.exists():
        raise SystemExit(f"actual Godot capture missing source frame for {args.lane_id}:{args.side}:{frame_label}: {source}")
    with Image.open(source) as image:
        actual_size = image.size
        scene_content_sentinel = _scene_content_sentinel(image)
    expected_size = _resolution_tuple(args.resolution)
    if actual_size != expected_size and not args.fullscreen:
        if args.side != "current" and actual_size[0] >= expected_size[0] and actual_size[1] >= expected_size[1]:
            pass
        else:
            raise SystemExit(
                "actual Godot capture source size mismatch "
                f"for {args.lane_id}:{args.side}:{frame_label}: "
                f"source={actual_size[0]}x{actual_size[1]} expected={expected_size[0]}x{expected_size[1]} path={source}"
            )
    if not scene_content_sentinel.get("passed", False):
        raise SystemExit(
            "actual Godot capture failed scene-content sentinel "
            f"for {args.lane_id}:{args.side}:{frame_label}: "
            f"{json.dumps(scene_content_sentinel, sort_keys=True)} path={source}"
        )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, out_png)
    out_meta.write_text(
        json.dumps(
            _metadata(args, run_dir, source, actual_size, signature_hash, frame_label, scene_content_sentinel),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-png", required=True)
    parser.add_argument("--out-metadata", required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--side", required=True)
    parser.add_argument("--frame", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--colyseus-dir",
        required=True,
        help=(
            "Absolute current-checkout colyseus_server directory shared by the "
            "Baseline A and current renderer arms."
        ),
    )
    parser.add_argument("--capture-root", required=True)
    parser.add_argument(
        "--capture-sequence",
        choices=("single_frame", "movement_sequence", STATIC_TEMPORAL_SEQUENCE),
        default="single_frame",
    )
    parser.add_argument("--camera-mode", required=True)
    parser.add_argument("--view-mode", required=True)
    parser.add_argument("--camera-position", required=True)
    parser.add_argument("--camera-yaw", required=True)
    parser.add_argument("--camera-pitch", required=True)
    parser.add_argument("--camera-roll", required=True)
    parser.add_argument("--camera-fov", required=True)
    parser.add_argument("--projection-params", required=True)
    parser.add_argument("--player-position", required=True)
    parser.add_argument("--player-yaw", required=True)
    parser.add_argument("--npc-positions", required=True)
    parser.add_argument("--airplane-position", required=True)
    parser.add_argument("--airplane-yaw", required=True)
    parser.add_argument("--world-time", required=True)
    parser.add_argument("--night-state", action="store_true")
    parser.add_argument("--feature-flags", required=True)
    # This flag carries the manifest's expected visual content into the capture
    # signature. It never becomes runtime evidence; only direct PNG review can
    # decide whether the beam is actually visible.
    parser.add_argument("--beam-expected-in-png", action="store_true")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--resolution", default="1920x1080")
    parser.add_argument("--move-frames", type=int, default=120)
    parser.add_argument("--settle-frames", type=int, default=45)
    parser.add_argument("--after-frames", type=int, default=6)
    parser.add_argument("--plane-circle-frames", type=int, default=240)
    parser.add_argument("--godot-timeout", type=float, default=300.0)
    parser.add_argument("--max-jitter-pct", type=float, default=5.0)
    args = parser.parse_args()

    # Validate and fingerprint the shared server before deriving the cache key.
    # A missing, relative, stale, and project-derived server cannot enter the
    # capture directory namespace.
    args.shared_current_server_identity = _shared_current_server_identity(
        args.colyseus_dir
    )
    signature = _capture_signature(args)
    signature_hash = _signature_hash(signature)
    capture_leaf = args.frame if args.capture_sequence == "single_frame" else args.capture_sequence
    run_dir = Path(args.capture_root).resolve() / args.lane_id / args.side / capture_leaf / signature_hash[:16]
    _run_capture(args, run_dir, signature, signature_hash)
    out_png = Path(args.out_png)
    out_meta = Path(args.out_metadata)
    if args.capture_sequence in {"movement_sequence", STATIC_TEMPORAL_SEQUENCE}:
        frame_labels = (
            MOVEMENT_SEQUENCE_FRAMES
            if args.capture_sequence == "movement_sequence"
            else STATIC_TEMPORAL_SEQUENCE_FRAMES
        )
        for frame_label in frame_labels:
            sibling_png = out_png.with_name(_frame_file(frame_label))
            sibling_meta = sibling_png.with_suffix(sibling_png.suffix + ".json")
            _write_output_frame(args, run_dir, signature_hash, frame_label, sibling_png, sibling_meta)
    else:
        _write_output_frame(args, run_dir, signature_hash, args.frame, out_png, out_meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

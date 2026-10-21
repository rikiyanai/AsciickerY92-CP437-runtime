#!/usr/bin/env python3
"""Capture isolated FL-4346 water Q/E, zoom, and performance evidence."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy
from PIL import Image, ImageDraw, ImageFont
from inspect_a3d import A3DHeader, A3DPatch

ROOT = Path(__file__).resolve().parents[1]
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
PROJECT = ROOT / "godot_project"
COLYSEUS_DIR = ROOT / "colyseus_server"
DRIVER = ROOT / "scripts" / "fl4346_actor_presentation_driver.gd"
RESOLUTION = (3600, 2252)
TARGET_SIZE = (3600, 2260)
ANSI_SIZE = (180, 113)
WATER_MAP = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
WATER_HEIGHT_MAX = 0.0
WATER_CAMERA_ORBIT_RADIUS = 33.0
WATER_CAMERA_RAY_HALF_WIDTH = 1.0
WATER_PERSPECTIVE_CAMERA_HEIGHT = 16.5
WATER_ACTOR_FOOT_Y = -0.38
WATER_ACTOR_TERRAIN_CLEARANCE = 0.55
PHASE_SUFFIXES = (
    "00_entry",
    "01_q_45",
    "02_q_90",
    "03_e_return",
    "04_zoom_in",
    "05_zoom_return",
    "06_swing_start",
    "07_swing_return",
)
PERF_BASELINES = {
    "orthographic": {"avg_fps": 89.983, "p95_delta_ms": 11.408},
    "perspective": {"avg_fps": 103.838, "p95_delta_ms": 10.434},
}


def _find_all_yaw_water_pose() -> dict:
    """Find an authored-water spawn with four unobstructed camera rays."""
    with WATER_MAP.open("rb") as stream:
        header = A3DHeader.from_file(stream)
        patches = [A3DPatch.from_file(stream) for _ in range(header.num_patches)]
    visual_span = 8.0
    height_scale = 16.0
    min_patch_x = min(patch.x for patch in patches)
    max_patch_x = max(patch.x for patch in patches)
    min_patch_z = min(patch.y for patch in patches)
    max_patch_z = max(patch.y for patch in patches)
    center_x = (min_patch_x + (max_patch_x - min_patch_x + 1) / 2.0) * visual_span
    center_z = (min_patch_z + (max_patch_z - min_patch_z + 1) / 2.0) * visual_span
    by_patch = {(patch.x, patch.y): patch for patch in patches}

    def height_at(world_x: float, world_z: float) -> float | None:
        source_x = world_x + center_x
        source_z = world_z + center_z
        patch_x = int(source_x // visual_span)
        patch_z = int(source_z // visual_span)
        patch = by_patch.get((patch_x, patch_z))
        if patch is None:
            return None
        local_x = source_x - patch_x * visual_span
        local_z = source_z - patch_z * visual_span
        grid_x = max(0.0, min(4.0, local_x / 2.0))
        grid_z = max(0.0, min(4.0, local_z / 2.0))
        x0 = int(grid_x)
        z0 = int(grid_z)
        x1 = min(4, x0 + 1)
        z1 = min(4, z0 + 1)
        tx = grid_x - x0
        tz = grid_z - z0

        def sample(ix: int, iz: int) -> float:
            return patch.height[iz][ix] / height_scale

        low = sample(x0, z0) * (1.0 - tx) + sample(x1, z0) * tx
        high = sample(x0, z1) * (1.0 - tx) + sample(x1, z1) * tx
        return low * (1.0 - tz) + high * tz

    def ray_clear_distance(world_x: float, world_z: float, dx: float, dz: float) -> float:
        lateral_x = -dz
        lateral_z = dx
        for step in range(int(WATER_CAMERA_ORBIT_RADIUS * 2.0) + 1):
            distance = step * 0.5
            for lateral in (-WATER_CAMERA_RAY_HALF_WIDTH, 0.0, WATER_CAMERA_RAY_HALF_WIDTH):
                height = height_at(
                    world_x + dx * distance + lateral_x * lateral,
                    world_z + dz * distance + lateral_z * lateral,
                )
                sightline_y = WATER_ACTOR_FOOT_Y + (
                    WATER_PERSPECTIVE_CAMERA_HEIGHT - WATER_ACTOR_FOOT_Y
                ) * distance / WATER_CAMERA_ORBIT_RADIUS
                maximum_receiver_y = max(
                    WATER_ACTOR_FOOT_Y + WATER_ACTOR_TERRAIN_CLEARANCE,
                    sightline_y,
                )
                if height is None or height > maximum_receiver_y:
                    return max(0.0, distance - 0.5)
        return WATER_CAMERA_ORBIT_RADIUS

    candidates: list[tuple[float, float]] = []
    for patch in patches:
        visual = patch.visual if hasattr(patch, "visual") else []
        for local_z in range(8):
            for local_x in range(8):
                material = (
                    visual[local_z][local_x]
                    if visual and isinstance(visual[local_z], list)
                    else visual[local_z * 8 + local_x]
                )
                if (int(material) & 0xFF) not in (0, 140):
                    continue
                world_x = patch.x * visual_span + local_x + 0.5 - center_x
                world_z = patch.y * visual_span + local_z + 0.5 - center_z
                corners = (
                    height_at(world_x - 0.5, world_z - 0.5),
                    height_at(world_x + 0.5, world_z - 0.5),
                    height_at(world_x - 0.5, world_z + 0.5),
                    height_at(world_x + 0.5, world_z + 0.5),
                )
                if all(height is not None and height <= WATER_HEIGHT_MAX for height in corners):
                    candidates.append((world_x, world_z))
    best_pose: tuple[float, float] | None = None
    best_clearance = -1.0
    for world_x, world_z in sorted(candidates, key=lambda point: point[0] ** 2 + point[1] ** 2):
        clearance = min(
            ray_clear_distance(world_x, world_z, dx, dz)
            for dx, dz in ((0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0))
        )
        if clearance > best_clearance:
            best_pose = (world_x, world_z)
            best_clearance = clearance
        if clearance >= WATER_CAMERA_ORBIT_RADIUS:
            return {
                "x": world_x,
                "z": world_z,
                "pose_source": (
                    "authored-map all-yaw finder: water footprint plus four "
                    "clear 33-unit camera-to-foot rays at 1-unit width"
                ),
            }
    raise RuntimeError(
        "FL-4346 has no authored all-yaw water pose; "
        f"best={best_pose} clearance={best_clearance:.1f}"
    )


def _scenarios() -> tuple[dict, ...]:
    water_pose = _find_all_yaw_water_pose()
    return (
        {
            "surface": "water",
            "projection": "orthographic",
            **water_pose,
        },
        {
            "surface": "water",
            "projection": "perspective",
            **water_pose,
        },
    )


def _projection_slug(projection: str) -> str:
    return "ortho" if projection == "orthographic" else "perspective"


def _case_name(scenario: dict) -> str:
    return f"{scenario['surface']}_{scenario['projection']}"


def _expected_phases(scenario: dict) -> tuple[str, ...]:
    prefix = f"{scenario['surface']}_{_projection_slug(scenario['projection'])}"
    return tuple(f"{prefix}_{suffix}" for suffix in PHASE_SUFFIXES)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_server(log_path: Path, process: subprocess.Popen[str], timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            if "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _provenance() -> dict:
    paths = [line for line in _git("status", "--short").splitlines() if line.strip()]
    return {
        "root": str(ROOT),
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": bool(paths),
        "git_dirty_paths": paths,
    }


def _start_server(
    port: int, scenario: dict, log_path: Path
) -> tuple[subprocess.Popen[str], object]:
    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(port)
    env["PLAYER_SPAWN_X"] = str(scenario["x"])
    env["PLAYER_SPAWN_Z"] = str(scenario["z"])
    for key in (
        "AIRPLANE_SPAWN_AIRBORNE",
        "AIRPLANE_SPAWN_ALTITUDE",
        "AIRPLANE_SPAWN_THROTTLE",
    ):
        env.pop(key, None)
    stream = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=COLYSEUS_DIR,
        env=env,
        text=True,
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    return process, stream


def _stop_process(process: subprocess.Popen[str], stream: object) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=4)
    stream.close()


# ---------------------------------------------------------------------------
# FL-4346 FRAMEBUFFER FALSIFIER (v4: footprint vs analytic anchor pole)
# ---------------------------------------------------------------------------
# Every prior acceptance gate in this lane compared two numbers that the
# runtime derived from the SAME projection call (actor anchor px vs pole px),
# so the gate could never fail even while the operator watched the sprite
# orbit the wake. The functions below measure registration from the RENDERED
# PIXELS of each capture instead. Rendered pixels are the operator's evidence
# surface, so this comparison can actually fail.
#
# THE INVARIANT UNDER TEST is billboarding, and it is content-agnostic
# (operator correction 2026-08-04): the billboard plane stays parallel to
# the camera plane, its world pivot stays fixed at the wake root, and its
# projected alpha footprint must not translate around that pivot during a
# pure Q/E camera turn — no matter what the sprite pixels depict (a
# character, a blob, a particle, anything). Earlier revisions of this gate
# segmented the player art by clothing color bands; that recognized ONE
# actor skin instead of testing billboarding, and was rejected. No garment
# recognition, palette inference, or assumed anatomy is allowed here.
#
# ACTOR IDENTITY comes from the compositor's diagnostic FOOTPRINT TINT
# channel (actor cell col15 bit 25, keyed to ONE server actor id): every
# pixel the billboard intersection ACCEPTS for the subject actor (same ray
# math, same alpha rule) renders as pure magenta vec3(1,0,1) with lighting
# bypassed, and F10 renders a pure cyan pole rooted at the plane pivot. The
# sentinel blob in a mask capture therefore IS the subject's alpha footprint
# and the cyan pole base IS the projected pivot, exactly as placed by the
# code under test. The DRIVER toggles the channel around each phase's mask
# capture, so the untinted presentation frame the operator reviews and the
# mask frame the gate measures come from one live process and pose, with a
# receipt-level pair-identity check between them.
#
# ANCHOR IDENTITY is analytic. F10 draws no world geometry. The
# compositor draws a pure-cyan world-vertical pole whose bottom is the exact
# packed actor root consumed by the billboard intersection. The grader uses
# that bottom band as the rendered presentation anchor. Runtime receipts must
# independently show that the visible wake endpoint consumes the same XZ.
#
# If any needed surface is missing from any capture the arm FAILS CLOSED —
# an unmeasurable frame is a failed frame, never a skipped one.

# One ANSI cell is 20 native pixels at the pinned 3600x2260 target. Sprite
# footprint and pole share the native-pixel actor rasterizer. One cell remains
# the maximum accepted visible attachment change during the temporal sequence.
FRAMEBUFFER_REGISTRATION_TOLERANCE_PX = 20.0
# Zoom intentionally changes pixels-per-world, so any constant sub-cell
# world offset between the footprint bottom and anchor rescales in
# pixels across the zoomed phase. That phase is measured and recorded for
# humans but excluded from the median-stability gate.
FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES = ("04_zoom_in",)
# Cluster admission floors: fewer pixels than this means the surface is not
# actually visible (occluded, missing, or wrong palette) and the measurement
# must fail closed rather than gate on noise.
# FL-4346 sentinel footprint channel. The compositor's diagnostic tint
# (actor cell col15 bit 25, keyed to ONE server actor id and toggled by the
# proof driver around each mask capture) paints every ACCEPTED pixel of THAT
# actor exactly vec3(1,0,1) with lighting bypassed, so the mask below is an
# equality test up to 8-bit rounding, not a palette guess.
FRAMEBUFFER_SENTINEL_MIN_CHANNEL = 250
FRAMEBUFFER_SENTINEL_MAX_GREEN = 5
# FL-4346 explicit rendered anchor pole. F10 paints pure cyan upward from the
# packed actor root through the same camera ray and plane used by actor paint.
# The bottom band, not the component centroid, is the anchor measurement.
FRAMEBUFFER_PIVOT_MAX_RED = 5
# Three pixels is the bare visibility floor for the pole.
FRAMEBUFFER_MIN_PIVOT_PIXELS = 3
# The footprint blob must clear this floor before its foot band means
# anything. The smallest real actor render measured (water ortho, ~4 cells
# tall) covered ~1,200 px; 150 keeps swing frames measurable while rejecting
# stray rounding noise.
# Perspective's valid close-water footprint reaches 62 pixels in real captures.
# Thirty still rejects isolated diagnostic noise while retaining that real actor.
FRAMEBUFFER_MIN_FOOTPRINT_PIXELS = 30
# FL-4346: half-size of the measurement window centered on the sentinel
# blob's median pixel. The zoomed-in sprite plus anchor pole stays well
# inside +/- 500 px; anything outside this window is by definition not the
# proof actor's registration evidence.
FRAMEBUFFER_ACTOR_WINDOW_HALF_PX = 500
# Bottom-band thickness for the footprint and cyan pole base.
FRAMEBUFFER_FOOT_BAND_PX = 14.0
# A footprint blob wider/taller than this cannot be one billboarded actor
# at proof camera distances; it means the sentinel channel is corrupted
# (or a second tinted source leaked in). The measurement fails closed
# instead of gating on it (FL-4346).
FRAMEBUFFER_MAX_SILHOUETTE_SPAN_PX = 700
# The endpoint and decoded actor-row root are unquantized values copied from
# one frozen receipt. Final-cell anchors are separately quantized to 1/64 and
# cannot be used for exact root identity.
FRAMEBUFFER_WAKE_ANCHOR_EPSILON_WORLD = 0.000001


def _measure_capture_registration(png_path: Path) -> dict:
    """FL-4346: measure billboard footprint against its analytic anchor pole.

    The billboard invariant under test is content-agnostic: a camera-
    parallel plane whose world pivot stays fixed at the wake root, carrying
    ARBITRARY sprite pixels (a character, a blob, anything). Nothing here
    may recognize garments, infer a palette, or gate on any geometry of the
    sprite's alpha boundary. The actor's identity in a MASK frame is the
    pure-magenta SENTINEL painted by the compositor's per-actor diagnostic
    footprint channel (col15 bit 25, driver-toggled), which reuses the exact
    billboard intersection and alpha-rejection rule and changes only the
    output color. F10 supplies the pure-cyan anchor pole. Pipeline:

      1. Every sentinel pixel is subject-actor-owned by construction. No
         sentinel, no measurable actor: fail closed (FL-4346 fail-shut
         rule) — which also makes untinted presentation frames
         unmeasurable on purpose.
      2. Cut a measurement window around the sentinel blob; guard its
         pixel count and span so a corrupted channel cannot gate.
      3. The FOOT (bottom-band centroid of the blob) is recorded as
         attachment telemetry only — it is content-dependent and NOT the
         gated point.
      4. The anchor is the bottom-band centroid of the rendered cyan pole.
         The pole begins at the packed actor root and crosses the water surface.
      5. The gated offset is footprint bottom band minus pole base. Runtime
         receipts independently require the visible wake endpoint to equal
         the actor root in world XZ.
    """
    frame = numpy.asarray(Image.open(png_path).convert("RGB")).astype(numpy.int32)
    red = frame[..., 0]
    green = frame[..., 1]
    blue = frame[..., 2]
    # -- step 1: sentinel footprint ----------------------------------------
    # The tint paints exactly vec3(1,0,1) with no lighting; thresholds only
    # forgive 8-bit rounding. The F10 card border (g and b crushed, r high) and
    # every authored sprite/terrain ink fail the simultaneous r+b floor.
    sentinel_mask = (
        (red >= FRAMEBUFFER_SENTINEL_MIN_CHANNEL)
        & (blue >= FRAMEBUFFER_SENTINEL_MIN_CHANNEL)
        & (green <= FRAMEBUFFER_SENTINEL_MAX_GREEN)
    )
    sentinel_ys, sentinel_xs = numpy.where(sentinel_mask)
    measurement: dict = {"capture": png_path.name}
    footprint: dict = {"pixel_count": int(sentinel_ys.size)}
    if sentinel_ys.size < FRAMEBUFFER_MIN_FOOTPRINT_PIXELS:
        footprint["found"] = False
        measurement.update(
            {"footprint": footprint, "measured": False,
             "reason": "footprint_not_found"}
        )
        return measurement
    # -- step 2: window rooted on the footprint itself ---------------------
    height, width = frame.shape[0], frame.shape[1]
    center_y = int(numpy.median(sentinel_ys))
    center_x = int(numpy.median(sentinel_xs))
    window_y0 = max(0, center_y - FRAMEBUFFER_ACTOR_WINDOW_HALF_PX)
    window_y1 = min(height, center_y + FRAMEBUFFER_ACTOR_WINDOW_HALF_PX)
    window_x0 = max(0, center_x - FRAMEBUFFER_ACTOR_WINDOW_HALF_PX)
    window_x1 = min(width, center_x + FRAMEBUFFER_ACTOR_WINDOW_HALF_PX)
    window = frame[window_y0:window_y1, window_x0:window_x1]
    measurement["window_px"] = {
        "x0": window_x0, "y0": window_y0, "x1": window_x1, "y1": window_y1,
    }
    inside = (
        (sentinel_ys >= window_y0) & (sentinel_ys < window_y1)
        & (sentinel_xs >= window_x0) & (sentinel_xs < window_x1)
    )
    outside_count = int(sentinel_ys.size - numpy.count_nonzero(inside))
    footprint["outside_window_pixel_count"] = outside_count
    if outside_count > 0:
        # Sentinel ink outside the actor's own window means a second tinted
        # source exists in frame; the channel's single-actor premise is
        # broken and gating on either blob would be a guess (FL-4346).
        footprint["found"] = False
        measurement.update(
            {"footprint": footprint, "measured": False,
             "reason": "footprint_split_across_window"}
        )
        return measurement
    blob_ys = sentinel_ys[inside]
    blob_xs = sentinel_xs[inside]
    span_x = float(blob_xs.max() - blob_xs.min())
    span_y = float(blob_ys.max() - blob_ys.min())
    footprint["bbox_span_px"] = {"w": span_x, "h": span_y}
    if max(span_x, span_y) > FRAMEBUFFER_MAX_SILHOUETTE_SPAN_PX:
        footprint["found"] = False
        measurement.update(
            {"footprint": footprint, "measured": False,
             "reason": "footprint_overgrown"}
        )
        return measurement
    footprint["found"] = True
    # -- step 3: foot = bottom-band centroid of the footprint --------------
    # Recorded for humans and attachment telemetry ONLY. The gate does not
    # use it: bottom-band geometry depends on the sprite's alpha content
    # (an offset bottom lobe or transparent padding legitimately moves it),
    # so gating on it would fail correct billboards on legal content
    # (reviewer round 4, finding 2). The gate uses the rendered pivot below.
    foot_band = blob_ys >= (blob_ys.max() - FRAMEBUFFER_FOOT_BAND_PX)
    foot = {
        "x": float(numpy.mean(blob_xs[foot_band])),
        "y": float(numpy.mean(blob_ys[foot_band])),
        "band_pixel_count": int(numpy.count_nonzero(foot_band)),
    }
    footprint["foot_px"] = foot
    measurement["footprint"] = footprint
    # -- step 4: analytic anchor = bottom band of the cyan pole --------------
    # F10 paints the pole from the exact packed actor root upward along world
    # Y. In both normal projections, world up projects toward decreasing
    # framebuffer Y, so the pole's greatest Y is its root endpoint.
    pivot_mask = (
        (red <= FRAMEBUFFER_PIVOT_MAX_RED)
        & (green >= FRAMEBUFFER_SENTINEL_MIN_CHANNEL)
        & (blue >= FRAMEBUFFER_SENTINEL_MIN_CHANNEL)
    )
    pivot_ys, pivot_xs = numpy.where(pivot_mask)
    pivot_info: dict = {"pixel_count": int(pivot_ys.size)}
    if pivot_ys.size < FRAMEBUFFER_MIN_PIVOT_PIXELS:
        measurement.update(
            {"pivot": pivot_info, "measured": False,
             "reason": "pivot_not_found"}
        )
        return measurement
    pivot_inside = (
        (pivot_ys >= window_y0) & (pivot_ys < window_y1)
        & (pivot_xs >= window_x0) & (pivot_xs < window_x1)
    )
    pivot_outside_count = int(
        pivot_ys.size - numpy.count_nonzero(pivot_inside)
    )
    pivot_info["outside_window_pixel_count"] = pivot_outside_count
    if pivot_outside_count > 0:
        # Cyan outside the subject's window means a second pole source is
        # rendering; gating on either source would be a guess.
        measurement.update(
            {"pivot": pivot_info, "measured": False,
             "reason": "pivot_outside_window"}
        )
        return measurement
    pivot_base_band = pivot_ys[pivot_inside] >= (
        pivot_ys[pivot_inside].max() - FRAMEBUFFER_FOOT_BAND_PX
    )
    pivot = {
        "x": float(numpy.mean(pivot_xs[pivot_inside][pivot_base_band])),
        "y": float(numpy.mean(pivot_ys[pivot_inside][pivot_base_band])),
        "band_pixel_count": int(numpy.count_nonzero(pivot_base_band)),
    }
    pivot_info["anchor_base_px"] = pivot
    measurement["pivot"] = pivot_info
    # -- step 5: body footprint registration to the analytic anchor --------
    measurement["measured"] = True
    measurement["offset_px"] = {
        "dx": foot["x"] - pivot["x"],
        "dy": foot["y"] - pivot["y"],
    }
    measurement["foot_to_anchor_pole_px"] = {
        "dx": foot["x"] - pivot["x"],
        "dy": foot["y"] - pivot["y"],
    }
    return measurement


def _wake_endpoint_identity(receipt: dict) -> dict:
    """Check that the visible wake endpoint consumes the actor root XZ.

    Wake history remains server-authoritative and may intentionally lag the
    rendered actor through interpolation. The attachment requirement concerns
    the visible endpoint, so this check must not equate history state with the
    current presentation sample.
    """
    captures = receipt.get("captures", [])
    if not isinstance(captures, list) or not captures:
        return {"checked": True, "holds": False, "reason": "no_captures"}
    if receipt.get("wake_endpoint_matches_root") is not True:
        return {
            "checked": True, "holds": False,
            "reason": "wake_endpoint_matches_root_not_true",
        }
    max_delta = 0.0
    for row in captures:
        if not isinstance(row, dict):
            return {"checked": True, "holds": False, "reason": "malformed_capture_row"}
        root = row.get("actor_root_world")
        endpoint = row.get("wake_presentation_endpoint_xz")
        if not isinstance(root, dict) or endpoint is None:
            return {
                "checked": True, "holds": False,
                "reason": "actor_or_wake_endpoint_missing",
                "phase": row.get("phase"),
            }
        delta = max(
            abs(float(root["x"]) - float(endpoint[0])),
            abs(float(root["z"]) - float(endpoint[1])),
        )
        max_delta = max(max_delta, delta)
    holds = max_delta <= FRAMEBUFFER_WAKE_ANCHOR_EPSILON_WORLD
    result = {"checked": True, "holds": holds, "max_delta_world": max_delta}
    if not holds:
        result["reason"] = "wake_endpoint_not_actor_root"
    return result


def _framebuffer_registration_summary(
    case_dir: Path, phase_prefix: str, receipt: dict | None = None
) -> dict:
    """FL-4346: gate one arm on rendered body-to-anchor stability.

    Three independent requirements, all mandatory:
      1. Rendered surface: the footprint bottom must hold one offset against
         the analytic cyan pole base across every gated Q/E phase
         (deviation from the arm's median <= one ANSI cell), measured on
         the per-phase MASK captures. Unmeasurable frames fail closed
         (FL-4346 fail-shut rule).
      2. Pair identity: every gated phase's mask capture must be written
         from the SAME live process state as its presentation capture —
         the driver publishes a footprint_pair block with receipt-level
         state deltas, and a broken or missing pair fails closed
         (reviewer round 4, finding 3: a second-process presentation run
         proved nothing about the measured state).
      3. Runtime truth: the receipt must prove the visible wake endpoint equals
         the actor root XZ. The temporal grid then exposes wake ink where the
         cyan pole for direct operator review.
    """
    measurements: list[dict] = []
    gated_offsets: list[tuple[float, float]] = []
    gated_pairs_ok = 0
    presentation_missing: list[str] = []
    # Index receipt rows by phase for same-state pair validation.
    receipt_rows_by_phase: dict = {}
    if isinstance(receipt, dict):
        for row in receipt.get("captures", []) or []:
            if isinstance(row, dict) and row.get("phase"):
                receipt_rows_by_phase[str(row["phase"])] = row
    for suffix in PHASE_SUFFIXES:
        # FL-4346 grader repair (attempt 91): the driver names every capture
        # with the SLUGGED prefix ("ground_ortho_00_entry", built at
        # fl4346_actor_presentation_driver.gd:184-185 from surface plus the
        # short projection slug). This loop used to build names from the
        # UNSLUGGED case name ("ground_orthographic_00_entry"), so every PNG
        # lookup and every receipt-row linkage below searched for files and
        # phases that never existed — the whole arm graded as "missing" and
        # the run could never grade its own captures. The caller now passes
        # the same slugged prefix the driver used.
        phase_name = f"{phase_prefix}_{suffix}"
        # The measured surface is the MASK capture; the presentation frame
        # of the same phase is the operator surface and must exist too.
        png_path = case_dir / f"{phase_name}_mask.png"
        if not (case_dir / f"{phase_name}.png").exists():
            presentation_missing.append(phase_name)
        if not png_path.exists():
            measurements.append(
                {"capture": png_path.name, "measured": False, "missing": True}
            )
            continue
        measurement = _measure_capture_registration(png_path)
        measurement["phase_suffix"] = suffix
        measurement["gated"] = suffix not in FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES
        capture_row = receipt_rows_by_phase.get(phase_name)
        # Pair identity (reviewer round 4, finding 3): the driver records
        # whether this phase's mask was written from the same live state as
        # the presentation frame. Missing block or drift fails the pair.
        pair = (
            capture_row.get("footprint_pair")
            if isinstance(capture_row, dict) else None
        )
        pair_ok = (
            isinstance(pair, dict)
            and bool(pair.get("mask_written"))
            and bool(pair.get("state_identical"))
        )
        measurement["pair_state_identical"] = pair_ok
        measurements.append(measurement)
        if measurement["measured"] and measurement["gated"]:
            gated_offsets.append(
                (
                    measurement["offset_px"]["dx"],
                    measurement["offset_px"]["dy"],
                )
            )
            if pair_ok:
                gated_pairs_ok += 1
    gated_expected = len(
        [s for s in PHASE_SUFFIXES if s not in FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES]
    )
    all_gated_measured = len(gated_offsets) == gated_expected
    max_dx_deviation = 0.0
    max_dy_deviation = 0.0
    if gated_offsets:
        dxs = numpy.array([offset[0] for offset in gated_offsets])
        dys = numpy.array([offset[1] for offset in gated_offsets])
        # Deviation from the arm median preserves authored transparent padding.
        # Only change during a pure camera turn is the attachment defect.
        max_dx_deviation = float(numpy.max(numpy.abs(dxs - numpy.median(dxs))))
        max_dy_deviation = float(numpy.max(numpy.abs(dys - numpy.median(dys))))
    stable = (
        all_gated_measured
        and max_dx_deviation <= FRAMEBUFFER_REGISTRATION_TOLERANCE_PX
        and max_dy_deviation <= FRAMEBUFFER_REGISTRATION_TOLERANCE_PX
    )
    # Requirement 2: every gated phase's mask must be a same-state pair of
    # its presentation frame, and every presentation frame must exist (the
    # operator cannot sign off an arm with no reviewable frames).
    all_gated_pairs_state_identical = gated_pairs_ok == gated_expected
    presentation_frames_complete = not presentation_missing
    wake_endpoint = _wake_endpoint_identity(
        receipt if isinstance(receipt, dict) else {}
    )
    return {
        "surface": "rendered_framebuffer_color_masks",
        "gate_measures": "billboard_footprint_vs_actor_presentation_pole",
        "tolerance_px": FRAMEBUFFER_REGISTRATION_TOLERANCE_PX,
        "gate_excluded_suffixes": list(FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES),
        "measurements": measurements,
        "all_gated_captures_measured": all_gated_measured,
        "all_gated_pairs_state_identical": all_gated_pairs_state_identical,
        "presentation_frames_complete": presentation_frames_complete,
        "presentation_frames_missing": presentation_missing,
        "max_dx_deviation_px": max_dx_deviation,
        "max_dy_deviation_px": max_dy_deviation,
        "wake_endpoint_identity": wake_endpoint,
        "pass": (
            stable
            and all_gated_pairs_state_identical
            and presentation_frames_complete
            and bool(wake_endpoint.get("holds"))
        ),
    }


def _run_case(
    scenario: dict, port: int, out_dir: Path, timeout: float
) -> dict:
    name = _case_name(scenario)
    case_dir = out_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = case_dir / "receipt.json"
    godot_log = case_dir / "godot.log"
    server_log = case_dir / "colyseus.log"
    server, server_stream = _start_server(port, scenario, server_log)
    try:
        if not _wait_for_server(server_log, server, 30.0):
            return {"pass": False, "reason": "colyseus_start_failed", "case": name}
        command = [
            GODOT_BIN,
            "--windowed",
            "--resolution",
            f"{RESOLUTION[0]}x{RESOLUTION[1]}",
            "--path",
            str(PROJECT),
            "--script",
            str(DRIVER),
            "--",
            "--colyseus-client",
            f"--port={port}",
            f"--fl4346-proof-out={case_dir}",
            f"--fl4346-proof-receipt={receipt_path}",
            f"--fl4346-proof-arm={name}",
            f"--fl4346-proof-surface={scenario['surface']}",
            f"--fl4346-proof-projection={scenario['projection']}",
            f"--fl4346-proof-resolution={RESOLUTION[0]}x{RESOLUTION[1]}",
            "--force-weather=clear",
            "--fl4408-day-cycle-time=0.5",
            "--colyseus-skip-room-leave",
            # FL-4346: no launch-time tint flag here on purpose. The DRIVER
            # toggles the per-actor sentinel channel around each phase's
            # mask capture, so every phase yields an untinted presentation
            # PNG and a tinted mask PNG from the SAME live process and pose
            # (reviewer round 4, finding 3: a second untinted Godot run was
            # not the same state as the measured one).
        ]
        with godot_log.open("w", encoding="utf-8") as godot_stream:
            godot = subprocess.Popen(
                command,
                cwd=ROOT,
                text=True,
                stdout=godot_stream,
                stderr=subprocess.STDOUT,
            )
            try:
                return_code = godot.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                godot.send_signal(signal.SIGINT)
                try:
                    godot.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    godot.kill()
                    godot.wait(timeout=4)
                return {
                    "pass": False,
                    "reason": "godot_timeout",
                    "case": name,
                    "command": shlex.join(command),
                }
        if not receipt_path.exists():
            return {
                "pass": False,
                "reason": "receipt_missing",
                "case": name,
                "godot_rc": return_code,
                "command": shlex.join(command),
            }
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["case"] = name
        receipt["scenario"] = scenario
        receipt["godot_rc"] = return_code
        receipt["command"] = shlex.join(command)
        expected = set(_expected_phases(scenario))
        captures = receipt.get("captures", [])
        actual = {
            row.get("phase") for row in captures if isinstance(row, dict)
        }
        receipt["capture_set_complete"] = actual == expected
        # FL-4346: the framebuffer falsifier grades the RENDERED pixels of
        # this arm's captures. It is a hard gate: runtime-side metadata can
        # no longer pass an arm whose visible body moved against the analytic
        # actor-presentation pole. The receipt separately requires that the
        # visible wake endpoint equals the actor root in world XZ.
        # FL-4346 grader repair (attempt 91): pass the SLUGGED phase prefix
        # ("ground_ortho"), not the case name ("ground_orthographic"). The
        # driver writes every capture under the slugged prefix, so the
        # summary must search under the same one or every arm grades as
        # missing (the defect that stopped the previous run after one arm).
        framebuffer = _framebuffer_registration_summary(
            case_dir,
            f"{scenario['surface']}_{_projection_slug(scenario['projection'])}",
            receipt,
        )
        receipt["framebuffer_registration"] = framebuffer
        # FL-4346: the second-process "presentation pass" that used to run
        # here is DELETED, not simplified away. It launched another Godot
        # instance whose receipt was never parsed and whose return code was
        # never required, and its frames came from a DIFFERENT live state
        # than the measured ones (reviewer round 4, finding 3). Presentation
        # frames now come from the same driver process as the masks — one
        # untinted PNG plus one mask PNG per phase, pair-identity-checked in
        # the framebuffer summary above — so there is nothing left to launch.
        receipt["pass"] = (
            bool(receipt.get("pass"))
            and actual == expected
            and bool(framebuffer.get("pass"))
        )
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt
    finally:
        _stop_process(server, server_stream)


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _annotate(image: Image.Image, capture: dict) -> Image.Image:
    # FL-4346: no post-capture drawing. Presentation frames are actual gameplay
    # pixels with F10 disabled; diagnostic mask frames remain separate files.
    return image.copy()


def _actor_wake_crop(image: Image.Image, capture: dict) -> Image.Image:
    anchor = capture.get("local_actor_body_anchor_screen_px", {})
    if not isinstance(anchor, dict) or "x" not in anchor or "y" not in anchor:
        return image.copy()
    # Keep enough water around the actor to judge the wake contact while making
    # the body, submerged edge, and swing readable in the review grid.
    crop_width = min(360, image.width)
    crop_height = min(280, image.height)
    center_x = int(round(float(anchor["x"])))
    center_y = int(round(float(anchor["y"])))
    x0 = max(0, min(center_x - crop_width // 2, image.width - crop_width))
    y0 = max(0, min(center_y - crop_height // 2, image.height - crop_height))
    return image.crop((x0, y0, x0 + crop_width, y0 + crop_height))


def _panel(image: Image.Image, lines: list[str], width: int = 900) -> Image.Image:
    scale = width / image.width
    resized = image.resize(
        (width, int(round(image.height * scale))), Image.Resampling.LANCZOS
    )
    label_height = 210
    result = Image.new("RGB", (width, resized.height + label_height), (16, 16, 18))
    result.paste(resized, (0, label_height))
    draw = ImageDraw.Draw(result)
    font = _font(16)
    y = 8
    for line in lines:
        draw.text((10, y), line, fill=(238, 240, 244), font=font)
        y += 20
    return result


def _capture_lines(case: dict, capture: dict, provenance: dict) -> list[str]:
    return [
        f"Current | {case['case']} | {capture.get('phase')} | {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "FL-4346 | water Q/E plus zoom | actor presentation attachment",
        "Actual gameplay frame: F10 off; no diagnostic overlay",
        "Attempt 134: exact per-hit water clip; Product-owned ASCII wake unchanged",
        f"commit={provenance['git_head'][:12]} dirty={str(provenance['git_dirty']).lower()}",
        f"player={capture.get('player_world')} yaw={float(capture.get('camera_yaw', 0.0)):.4f}",
        f"root={capture.get('local_actor_body_anchor_screen_px')} rect={capture.get('local_actor_screen_rect')}",
        f"post_shift={capture.get('pixel_camera_style_post_shift_px')}",
        f"actor_rows={capture.get('actor_row_count')}",
        f"actor_row={case.get('actor_row_summary', {})}",
    ]


def _motion_capture_lines(case: dict, capture: dict, provenance: dict) -> list[str]:
    input_state = capture.get("input_state", {})
    return [
        f"Current | {case['case']} | {capture.get('phase')} | {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        "FL-4346 | water held-Q/E and active-swing temporal attachment",
        "Untinted live presentation frame plus same-frame runtime receipt",
        "scripts/fl4346_actor_presentation_driver.gd:_capture_presentation_motion_frame",
        f"commit={provenance['git_head'][:12]} dirty={str(provenance['git_dirty']).lower()}",
        f"kind={capture.get('motion_kind')} presentation={capture.get('local_actor_presentation_kind')}",
        f"input_q={float(input_state.get('rotate_left', 0.0)):.2f} input_e={float(input_state.get('rotate_right', 0.0)):.2f}",
        f"player={capture.get('player_world')} yaw={float(capture.get('camera_yaw', 0.0)):.4f}",
        f"root={capture.get('local_actor_body_anchor_screen_px')} rect={capture.get('local_actor_screen_rect')}",
        f"wake_root_delta={capture.get('wake_endpoint_to_root_world')}",
    ]


def _save_panel_sequence(
    panels: list[Image.Image], grid_path: Path, gif_path: Path, duration_ms: int
) -> None:
    columns = 2
    rows = (len(panels) + columns - 1) // columns
    grid = Image.new(
        "RGB", (panels[0].width * columns, panels[0].height * rows), (24, 24, 24)
    )
    for index, panel in enumerate(panels):
        grid.paste(
            panel,
            ((index % columns) * panel.width, (index // columns) * panel.height),
        )
    grid.save(grid_path)
    panels[0].save(
        gif_path,
        save_all=True,
        append_images=panels[1:],
        duration=duration_ms,
        loop=0,
        optimize=False,
    )


def _build_surfaces(
    out_dir: Path, cases: list[dict], provenance: dict, scenario_count: int
) -> dict:
    outputs: dict[str, dict[str, str]] = {}
    overview_rows: list[list[Image.Image]] = []
    for case in cases:
        panels: list[Image.Image] = []
        for capture in case.get("captures", []):
            png = Path(str(capture.get("png", "")))
            if not png.exists():
                continue
            source = _annotate(Image.open(png).convert("RGB"), capture)
            panels.append(_panel(source, _capture_lines(case, capture, provenance)))
        if len(panels) != len(PHASE_SUFFIXES):
            continue
        case_dir = out_dir / case["case"]
        grid_path = case_dir / "temporal_grid.png"
        gif_path = case_dir / "temporal.gif"
        _save_panel_sequence(panels, grid_path, gif_path, 750)
        outputs[case["case"]] = {"grid": str(grid_path), "gif": str(gif_path)}
        close_panels: list[Image.Image] = []
        for capture in case.get("captures", []):
            png = Path(str(capture.get("png", "")))
            if not png.exists():
                continue
            source = _actor_wake_crop(Image.open(png).convert("RGB"), capture)
            close_panels.append(
                _panel(source, _capture_lines(case, capture, provenance))
            )
        if len(close_panels) == len(PHASE_SUFFIXES):
            close_grid_path = case_dir / "temporal_close_grid.png"
            close_gif_path = case_dir / "temporal_close.gif"
            _save_panel_sequence(
                close_panels, close_grid_path, close_gif_path, 750
            )
            outputs[case["case"]]["close_grid"] = str(close_grid_path)
            outputs[case["case"]]["close_gif"] = str(close_gif_path)
        motion_panels: list[Image.Image] = []
        motion_close_panels: list[Image.Image] = []
        for capture in case.get("motion_captures", []):
            png = Path(str(capture.get("png", "")))
            if not png.exists():
                continue
            source = _annotate(Image.open(png).convert("RGB"), capture)
            motion_panels.append(
                _panel(source, _motion_capture_lines(case, capture, provenance))
            )
            motion_close_panels.append(
                _panel(
                    _actor_wake_crop(
                        Image.open(png).convert("RGB"), capture
                    ),
                    _motion_capture_lines(case, capture, provenance),
                )
            )
        if motion_panels:
            motion_grid_path = case_dir / "motion_grid.png"
            motion_gif_path = case_dir / "motion.gif"
            _save_panel_sequence(
                motion_panels, motion_grid_path, motion_gif_path, 220
            )
            outputs[case["case"]]["motion_grid"] = str(motion_grid_path)
            outputs[case["case"]]["motion_gif"] = str(motion_gif_path)
            motion_close_grid_path = case_dir / "motion_close_grid.png"
            motion_close_gif_path = case_dir / "motion_close.gif"
            _save_panel_sequence(
                motion_close_panels,
                motion_close_grid_path,
                motion_close_gif_path,
                220,
            )
            outputs[case["case"]]["motion_close_grid"] = str(
                motion_close_grid_path
            )
            outputs[case["case"]]["motion_close_gif"] = str(
                motion_close_gif_path
            )
        overview_indices = tuple(
            round(index * (len(panels) - 1) / 4) for index in range(5)
        )
        overview_rows.append(
            [
                panels[index].resize((450, panels[index].height // 2))
                for index in overview_indices
            ]
        )
    if len(overview_rows) == scenario_count:
        cell_w = overview_rows[0][0].width
        cell_h = overview_rows[0][0].height
        overview = Image.new(
            "RGB", (cell_w * 5, cell_h * scenario_count), (24, 24, 24)
        )
        for row_index, row in enumerate(overview_rows):
            for column_index, panel in enumerate(row):
                overview.paste(panel, (column_index * cell_w, row_index * cell_h))
        overview_path = out_dir / "fl4346_water_overview.png"
        overview.save(overview_path)
        outputs["overview"] = {"grid": str(overview_path)}
    return outputs


def _run_perf(out_dir: Path, timeout: float) -> dict:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_godot_camera_turn_perf.py"),
        "--variants",
        "default",
        "--sample-frames",
        "240",
        "--warmup-frames",
        "90",
        "--uncapped",
        "--projection-modes",
        "orthographic,perspective",
        "--expect-presentation-size",
        f"{RESOLUTION[0]}x{RESOLUTION[1]}",
        "--expect-target-size",
        f"{TARGET_SIZE[0]}x{TARGET_SIZE[1]}",
        "--expect-ansi-size",
        f"{ANSI_SIZE[0]}x{ANSI_SIZE[1]}",
        "--runtime-source-ref",
        _git("rev-parse", "HEAD"),
        "--day-cycle-time",
        "0.5",
    ]
    log_path = out_dir / "canonical_perf.log"
    with log_path.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    source = ROOT / ".run" / "godot_camera_turn_perf" / "default.json"
    receipt = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    target = out_dir / "canonical_perf.json"
    target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    modes = receipt.get("mode_results", [])
    pass_modes = []
    for mode in modes:
        projection = str(mode.get("projection_mode", ""))
        baseline = PERF_BASELINES.get(projection)
        if baseline is None:
            continue
        pass_modes.append(
            float(mode.get("avg_fps", 0.0)) >= baseline["avg_fps"]
            and float(mode.get("p95_delta_ms", float("inf")))
            <= baseline["p95_delta_ms"]
        )
    return {
        "pass": result.returncode == 0 and len(pass_modes) == 2 and all(pass_modes),
        "rc": result.returncode,
        "command": shlex.join(command),
        "log": str(log_path),
        "receipt": str(target),
        "result": receipt,
        "required_baselines": PERF_BASELINES,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=2614)
    parser.add_argument("--godot-timeout", type=float, default=420.0)
    parser.add_argument("--perf-timeout", type=float, default=600.0)
    parser.add_argument("--skip-perf", action="store_true")
    parser.add_argument("--print-water-pose", action="store_true")
    args = parser.parse_args()
    if not PROJECT.exists() or not DRIVER.exists():
        print("FL-4346 current project/driver missing", file=sys.stderr)
        return 2
    try:
        scenarios = _scenarios()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FL-4346 water pose unavailable: {exc}", file=sys.stderr)
        return 2
    if args.print_water_pose:
        print(json.dumps(scenarios[0], sort_keys=True))
        return 0
    blocked_ports = [args.port + index for index in range(len(scenarios))]
    if any(_port_open(port) for port in blocked_ports):
        print(f"FL-4346 ports already in use: {blocked_ports}", file=sys.stderr)
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else ROOT / ".run" / f"fl4346_actor_presentation_proof_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    provenance = _provenance()
    cases: list[dict] = []
    for index, scenario in enumerate(scenarios):
        case = _run_case(scenario, args.port + index, out_dir, args.godot_timeout)
        cases.append(case)
    visual_runtime_pass = len(cases) == len(scenarios) and all(
        bool(case.get("pass")) for case in cases
    )
    surfaces = _build_surfaces(out_dir, cases, provenance, len(scenarios))
    if args.skip_perf:
        performance = {"pass": False, "skipped": True, "reason": "operator_skip"}
    elif visual_runtime_pass:
        performance = _run_perf(out_dir, args.perf_timeout)
    else:
        performance = {
            "pass": False,
            "skipped": True,
            "reason": "visual_runtime_case_failed",
        }
    manifest = {
        "schema": "asciicker.fl4346.actor_presentation_package.v3",
        "fl": "FL-4346",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "PARTIAL/MONITORING pending operator visual signoff",
        "provenance": provenance,
        "scenarios": scenarios,
        "cases": cases,
        "visual_runtime_pass": visual_runtime_pass,
        "surfaces": surfaces,
        "performance": performance,
        "structural_gates_pass": visual_runtime_pass and bool(performance.get("pass")),
        "operator_accepted": False,
        "acceptance_rule": "This current-only review package must show the untinted gameplay body, Product-owned ASCII wake contact, and swing attached through stationary Q/E rotation; zoom changes size without moving the anchor. Matching runtime rows and comparable uncapped timing are required here. Formal multi-panel comparison and operator signoff remain external acceptance requirements.",
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), **manifest}, indent=2))
    return 0 if bool(manifest["structural_gates_pass"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""FL-4359 Phase-2a camera/airplane pose-unification analyzer (governing-order step 8).

Post-run interpretation of the per-frame Phase-2a receipts emitted by World.gd and player.gd
when a capture is launched with --fl4359-phase2a-receipts. It is the camera-only jitter +
pose-coherence analyzer for the perspective FLIGHT camera, NOT the per-cell GPU shimmer
analyzer (that is analyze_fl4359_moving_camera.py over the jitter_capture JSONL).

Input is the capture stdout LOG (or any text file / stdin) containing:
  FL4359_PHASE2A_WINDOW_START frame=<N>            (measured-window marker; warmup excluded)
  FL4359_PHASE2A_WORLD_RECEIPT   {json...}         (World, process_priority 0, one per frame)
  FL4359_PHASE2A_CAMERA_RECEIPT  {json...}         (Player, priority -100, pilot-perspective only)

World receipt fields:  process_frame, prepared_frame, mesh_pose_frame, compositor_camera_frame,
                       is_pilot, airplane_position
Camera receipt fields: process_frame, camera_pose_frame, camera_anchor_minus_airplane_position,
                       raw_airplane_yaw, camera_yaw

Receipts are joined by process_frame (Engine.get_process_frames() is constant within a frame, so
Player's -100 emit and World's 0 emit share it). Gates (Law 13 classes):

  evidence_phase2a_receipts_present   >= N joined pilot frames inside the window (fail closed)
  scenario_pilot_perspective_window   window marker found AND the camera actually moved
  evidence_pose_frame_coherent        prepared == camera == mesh == compositor frame, every frame
                                      (binding requirement 7a)
  gameplay_camera_anchor_bounded      max camera_anchor_minus_airplane_position <= epsilon
                                      (binding requirement 7b)
  gameplay_camera_yaw_jitter_bounded  camera_yaw delta sign-flip rate <= threshold (no thrash;
                                      direct yaw updates do not oscillate)

Fail-closed: no receipts / no window / no camera motion -> the gameplay gates cannot pass.

Usage:
  python3 scripts/analyze_fl4359_phase2a_pose.py <capture.log> --summary-json out.json --fail-on-gate
"""
from __future__ import annotations

import argparse
import json
import math
import sys

WORLD_PREFIX = "FL4359_PHASE2A_WORLD_RECEIPT "
CAMERA_PREFIX = "FL4359_PHASE2A_CAMERA_RECEIPT "
WINDOW_PREFIX = "FL4359_PHASE2A_WINDOW_START "
PHASE_PREFIX = "FL4359_PHASE2A_PHASE "    # step 10: straight/turn/stop phase markers
LERP_PREFIX = "FL4359_LERP_RECEIPT "  # step 10: local-player presentation second-lerp instrumentation
PERSP_PREFIX = "FL4359_PHASE2A_PERSP "  # finding 7: per-phase perspective-ownership probe
# Mirrors Main.gd LOCAL_PLAYER_PRESENTATION_FOLLOW_RATE (second-lerp one-pole rate) for offline re-sim.
PRESENTATION_FOLLOW_RATE = 9.0
# FL-4359 reviewer rework #7 (NI-1): the authoritative server velocity field is WORLD UNITS PER SERVER
# TICK, not per second. Source: colyseus_server/src/sim/world.ts -- MOVE_SPEED_PER_TICK = 0.26 u/tick
# (documented "0.26 u/tick -> 7.80 u/s @ 30 Hz"); the server integrates position += velocity ONCE per
# tick. The analyzer works in world u/s, so every authoritative segment velocity is converted ONCE at
# ingestion: v_per_second = v_per_tick * tick_rate_hz. The earlier "96.8% velocity disagreement" that
# blocked C1 was THIS unit mismatch (|0.26 - 7.80| / 7.80 = 0.967), NOT stale/untrustworthy server
# data. Hermite param-space tangents are therefore m = v_per_second * dt_seconds, which is identically
# v_per_tick * tick_span (NI-3); both forms are unit-correct once velocity is in u/s.
SERVER_VELOCITY_UNIT = "world_units_per_server_tick"
# Chord-aligned corridor (NI-8): max tangent perpendicular component = CORRIDOR_FRAC * chord_len.
# A cubic Hermite's Bezier controls are B1=p0+m0/3, B2=p1-m1/3, so the control-point perpendicular
# offset is at most CORRIDOR_FRAC/3 of the chord; the convex-hull property then bounds the whole
# curve's lateral deviation by that same fraction.
CORRIDOR_FRAC = 0.5
CORRIDOR_PERP_FRAC_LIMIT = CORRIDOR_FRAC / 3.0  # normalized (per chord length) curve bow ceiling
# reviewer rework #7 NI-7: corridor safety is PRIMARILY defined in PROJECTED ANSI-cell displacement,
# because the perceptible quantity is screen deviation, not world deviation -- 0.5 world units can be
# many cells near a perspective camera and sub-cell when far. The lateral budget is therefore a
# sub-cell screen tolerance; a bow that projects to < PROJECTED_CORRIDOR_CELL_TOLERANCE of one ANSI
# cell is below glyph-quantization and imperceptible. Projection requires the camera focal length (in
# cells) and the camera-to-player distance per frame; those are NOT in the pre-rework capture receipt,
# so when they are absent the analyzer marks the projected bound unavailable (FL-4359 fail-shut;
# projected_corridor_available = False) and falls back to the world tolerance below, flagged
# provenance-limited. The world tolerance is retained ONLY as that explicit fallback (and as the
# chord-fraction floor), no longer as the primary justification. Half a cell = sub-glyph; conservative.
PROJECTED_CORRIDOR_CELL_TOLERANCE = 0.5  # max lateral bow in ANSI cells (projected); sub-glyph
# Fallback world tolerance (used ONLY when projection data is absent -- see NI-7 fail-shut note). The
# visible presentation actor has collision DISABLED at runtime (Main.gd:1897 sets collision_layer/mask
# = 0), so any world bound here is a VISUAL-deviation bound, not a physics one. Kept conservative at 5%
# of the documented warp/snap threshold so the fallback never exceeds the snap budget.
PRESENTATION_WARP_DISTANCE_WORLD = 10.0  # mirrors Main.gd LOCAL_PLAYER_PRESENTATION_WARP_DISTANCE
PRESENTATION_LATERAL_TOLERANCE_WORLD = 0.05 * PRESENTATION_WARP_DISTANCE_WORLD  # 0.5 world units (fallback)
# reviewer NI-4 (rework #6): a boundary "improvement" must be MATERIAL, not numerical noise. Require
# both a >=50% reduction vs linear AND an absolute normalized residual ceiling (mismatch / secant
# speed). A C1 scheme should drive the boundary velocity discontinuity to ~0; 0.1 leaves headroom.
BOUNDARY_REDUCTION_RATIO_MAX = 0.5
BOUNDARY_NORM_RESIDUAL_LIMIT = 0.1


def _corridor_world(L: float) -> float:
    """Absolute lateral corridor (world units) for a segment of chord length L: the tighter of the
    chord-fraction limit and the absolute world FALLBACK tolerance (NI-5/NI-7). This is the bound used
    only when projected-cell data is unavailable; the primary corridor is the projected one below."""
    return min(CORRIDOR_PERP_FRAC_LIMIT * L, PRESENTATION_LATERAL_TOLERANCE_WORLD)


def _project_world_perp_to_cells(world_perp: float, distance: float, focal_cells: float) -> float:
    """reviewer rework #7 NI-7: project a lateral WORLD displacement to ANSI-cell screen displacement
    under a perspective camera. Small-angle pinhole: a perpendicular world offset d at camera distance
    D subtends angle ~ d/D; with the camera's focal length expressed in CELLS (focal_cells = the
    half-viewport cell count / tan(half_fov)), the screen displacement in cells is
        cells = world_perp * focal_cells / D.
    Returns +inf if the projection inputs are non-positive (cannot certify -> fail-shut upstream)."""
    if distance <= 0.0 or focal_cells <= 0.0:
        return math.inf
    return world_perp * focal_cells / distance


def _camera_projection(rows: list) -> dict:
    """NI-7: recover camera focal length (cells) + representative camera->player distance from the
    receipt, if present. The pre-rework capture lacks these fields, so this returns available=False and
    the projected corridor fails shut (FL-4359). Fields, when added by the instrumented receipt:
    'camera_focal_cells' (half-viewport cells / tan(half_fov)) and 'camera_player_distance' (world)."""
    focal = [float(r["camera_focal_cells"]) for r in rows
             if r.get("camera_focal_cells") not in (None, "") and float(r.get("camera_focal_cells", 0)) > 0.0]
    dist = [float(r["camera_player_distance"]) for r in rows
            if r.get("camera_player_distance") not in (None, "") and float(r.get("camera_player_distance", 0)) > 0.0]
    if not focal or not dist:
        return {"available": False, "focal_cells": None, "min_distance": None,
                "reason": "receipt lacks camera_focal_cells / camera_player_distance (pre-rework#7 capture)"}
    # worst case = nearest distance (largest projected displacement per world unit).
    return {"available": True, "focal_cells": min(focal), "min_distance": min(dist), "reason": None}


def _parse_json_suffix(line: str, prefix: str):
    try:
        return json.loads(line[len(prefix):].strip())
    except (ValueError, IndexError):
        return None


def parse_log(text: str) -> dict:
    """Return {window_start, world:{frame:row}, camera:{frame:row}}."""
    window_start = None
    world: dict[int, dict] = {}
    camera: dict[int, dict] = {}
    lerp: dict[int, dict] = {}
    phases: list[tuple[int, str]] = []  # (frame, phase_name)
    persp: list[dict] = []  # finding 7: perspective-ownership probes (window_start/straight/turn/stop/end)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(PERSP_PREFIX):
            row = _parse_json_suffix(line, PERSP_PREFIX)
            if isinstance(row, dict):
                persp.append(row)
        elif line.startswith(PHASE_PREFIX):
            # "phase=<name> frame=<N>"
            toks = dict(t.split("=", 1) for t in line[len(PHASE_PREFIX):].split() if "=" in t)
            if "phase" in toks and "frame" in toks:
                try:
                    phases.append((int(toks["frame"]), toks["phase"]))
                except ValueError:
                    pass
        elif line.startswith(LERP_PREFIX):
            row = _parse_json_suffix(line, LERP_PREFIX)
            if isinstance(row, dict) and "process_frame" in row:
                lerp[int(row["process_frame"])] = row
        elif line.startswith(WINDOW_PREFIX):
            tok = line[len(WINDOW_PREFIX):].strip()
            # "frame=<N>"
            if tok.startswith("frame="):
                try:
                    window_start = int(tok[len("frame="):])
                except ValueError:
                    pass
        elif line.startswith(WORLD_PREFIX):
            row = _parse_json_suffix(line, WORLD_PREFIX)
            if isinstance(row, dict) and "process_frame" in row:
                world[int(row["process_frame"])] = row
        elif line.startswith(CAMERA_PREFIX):
            row = _parse_json_suffix(line, CAMERA_PREFIX)
            if isinstance(row, dict) and "process_frame" in row:
                camera[int(row["process_frame"])] = row
    return {"window_start": window_start, "world": world, "camera": camera, "lerp": lerp,
            "phases": sorted(phases), "persp": persp}


def _as_vec3(v):
    # Godot JSON serializes Vector3 as "(x, y, z)".
    if isinstance(v, str):
        s = v.strip().lstrip("(").rstrip(")")
        parts = [p for p in s.replace(",", " ").split() if p]
        if len(parts) == 3:
            try:
                return tuple(float(p) for p in parts)
            except ValueError:
                return None
        return None
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(float(p) for p in v)
    return None


def join_frames(parsed: dict) -> list[dict]:
    """Joined per-frame rows (pilot-perspective frames have both world+camera) inside window."""
    window_start = parsed["window_start"]
    world, camera = parsed["world"], parsed["camera"]
    joined = []
    for frame in sorted(set(world) & set(camera)):
        if window_start is not None and frame < window_start:
            continue
        w, c = world[frame], camera[frame]
        joined.append({
            "process_frame": frame,
            "prepared_frame": int(w.get("prepared_frame", -1)),
            "mesh_pose_frame": int(w.get("mesh_pose_frame", -1)),
            "compositor_camera_frame": int(w.get("compositor_camera_frame", -1)),
            "camera_pose_frame": int(c.get("camera_pose_frame", -1)),
            "anchor_delta": float(c.get("camera_anchor_minus_airplane_position", math.inf)),
            "raw_airplane_yaw": float(c.get("raw_airplane_yaw", 0.0)),
            "camera_yaw": float(c.get("camera_yaw", 0.0)),
            "airplane_position": _as_vec3(w.get("airplane_position")),
            "is_pilot": bool(w.get("is_pilot", False)),
        })
    return joined


def _angle_diff(a: float, b: float) -> float:
    d = (a - b) % (2.0 * math.pi)
    if d > math.pi:
        d -= 2.0 * math.pi
    return d


def analyze(joined: list[dict], args: argparse.Namespace) -> dict:
    n = len(joined)
    # requirement 7a: prepared == camera == mesh == compositor, per frame.
    incoherent = []
    for r in joined:
        frames = {r["prepared_frame"], r["camera_pose_frame"], r["mesh_pose_frame"],
                  r["compositor_camera_frame"], r["process_frame"]}
        if len(frames) != 1:
            incoherent.append(r["process_frame"])
    # requirement 7b: anchor delta bounded.
    max_anchor_delta = max((r["anchor_delta"] for r in joined), default=math.inf)
    anchor_violations = [r["process_frame"] for r in joined if r["anchor_delta"] > args.anchor_epsilon]
    # camera-only jitter: sign-flip rate of consecutive camera_yaw deltas + motion presence.
    yaw_deltas = [_angle_diff(joined[i]["camera_yaw"], joined[i - 1]["camera_yaw"]) for i in range(1, n)]
    anchor_motion = 0.0
    for i in range(1, n):
        a, b = joined[i]["airplane_position"], joined[i - 1]["airplane_position"]
        if a is not None and b is not None:
            anchor_motion += math.dist(a, b)
    yaw_motion = sum(abs(d) for d in yaw_deltas)
    eps_motion = 1e-4
    sig = [d for d in yaw_deltas if abs(d) > eps_motion]
    flips = sum(1 for i in range(1, len(sig)) if (sig[i] > 0) != (sig[i - 1] > 0))
    comparable = max(len(sig) - 1, 0)
    yaw_sign_flip_rate = (flips / comparable) if comparable > 0 else 0.0
    camera_moved = (yaw_motion > args.min_yaw_motion) or (anchor_motion > args.min_anchor_motion)
    return {
        "joined_frames": n,
        "window_start": None,  # filled by caller
        "incoherent_frames": incoherent,
        "max_anchor_delta": max_anchor_delta,
        "anchor_violation_frames": anchor_violations,
        "yaw_sign_flip_rate": yaw_sign_flip_rate,
        "yaw_motion_rad": yaw_motion,
        "anchor_motion_world": anchor_motion,
        "camera_moved": camera_moved,
        "comparable_yaw_pairs": comparable,
    }


def build_gates(stats: dict, parsed: dict, args: argparse.Namespace) -> dict:
    n = stats["joined_frames"]
    have_receipts = n >= args.min_joined_frames
    window_present = parsed["window_start"] is not None
    gates = {
        "evidence_phase2a_receipts_present": {
            "pass": have_receipts,
            "joined_frames": n,
            "required": args.min_joined_frames,
        },
        "scenario_pilot_perspective_window": {
            # the camera receipt only emits in the pilot-perspective branch, so its presence
            # proves the right scenario; require the window marker + real camera motion so a
            # frozen/non-pilot capture cannot satisfy the jitter gate vacuously.
            "pass": have_receipts and window_present and stats["camera_moved"],
            "window_present": window_present,
            "camera_moved": stats["camera_moved"],
            "yaw_motion_rad": stats["yaw_motion_rad"],
            "anchor_motion_world": stats["anchor_motion_world"],
        },
        "evidence_pose_frame_coherent": {
            # binding requirement 7a
            "pass": have_receipts and len(stats["incoherent_frames"]) == 0,
            "incoherent_frames": stats["incoherent_frames"][:20],
            "incoherent_count": len(stats["incoherent_frames"]),
        },
        "gameplay_camera_anchor_bounded": {
            # binding requirement 7b
            "pass": have_receipts and stats["max_anchor_delta"] <= args.anchor_epsilon,
            "max_anchor_delta": stats["max_anchor_delta"],
            "epsilon": args.anchor_epsilon,
            "violation_frames": stats["anchor_violation_frames"][:20],
        },
        "gameplay_camera_yaw_jitter_bounded": {
            "pass": (have_receipts and stats["camera_moved"]
                     and stats["yaw_sign_flip_rate"] <= args.max_yaw_sign_flip_rate),
            "yaw_sign_flip_rate": stats["yaw_sign_flip_rate"],
            "limit": args.max_yaw_sign_flip_rate,
            "comparable_yaw_pairs": stats["comparable_yaw_pairs"],
        },
    }
    gates["all_pass"] = all(bool(v.get("pass")) for v in gates.values() if isinstance(v, dict))
    return gates


def _vmag(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _row_dt(r) -> float:
    """dt in seconds from the SAMPLER's own clock (reviewer finding 2): render_tick advances on
    Time.get_ticks_msec(), so measure target motion against render_tick, not _process(delta).
    dt = render_tick_delta / tick_rate_hz. Returns 0 when the monotonic clamp froze render_tick
    (no target advance is expected then) or the fields are absent."""
    rtd = float(r.get("render_tick_delta", 0.0))
    hz = float(r.get("tick_rate_hz", 0.0))
    if rtd <= 0.0 or hz <= 0.0:
        return 0.0
    return rtd / hz


def _row_dt_process(r) -> float:
    """dt in seconds from the PROCESS clock (reviewer finding 1): the smoothed presentation is
    advanced by the SECOND lerp on _process(delta), so its kinematics must be measured in process
    time (delta_seconds), NOT the sampler's render_tick clock. Returns 0 if the field is absent or
    non-positive (frozen frame)."""
    d = float(r.get("delta_seconds", 0.0))
    return d if d > 0.0 else 0.0


def _kinematics(rows: list[dict], pos_key: str, dt_fn=_row_dt) -> dict:
    """VECTOR kinematics over an epoch-contiguous frame run, measured with dt_fn's clock.

    reviewer finding 1: the RAW target follows render_tick (dt_fn=_row_dt), the SMOOTHED presentation
    follows the process clock (dt_fn=_row_dt_process). Pass the matching clock per trajectory.

    For each step (i-1 -> i) with the SAME presentation_epoch, consecutive process_frame, and
    dt>0: velocity v_i = (p_i - p_{i-1}) / dt_i. Acceleration a_i = (v_i - v_{i-1})/dt_i.
    PREDICTION ERROR pe_i = | p_i - (p_{i-1} + v_{i-1} * dt_i) | (world units, visual significance),
    and pe_per_dt2 = pe_i / dt_i^2 (acceleration-like, comparable across frame rates -- reviewer
    finding 5; pe alone scales ~dt^2 under smooth acceleration). Direction reversals spike pe (unlike
    the discarded scalar jerk). Returns aggregates + per-step (frame, pe, pe_per_dt2)."""
    speeds, accels, pe_world, pe_norm = [], [], [], []
    pred_errors = []   # (frame, pe_world, pe_per_dt2)
    prev = None        # (frame, epoch, pos, v)
    for r in rows:
        frame = int(r["process_frame"])
        epoch = int(r.get("presentation_epoch", 0))
        dt = dt_fn(r)
        pos = _as_vec3(r.get(pos_key))
        if pos is None:
            prev = None
            continue
        if prev is None or prev[1] != epoch or frame != prev[0] + 1 or dt <= 0.0:
            prev = (frame, epoch, pos, None)
            continue
        v = tuple(c / dt for c in _vsub(pos, prev[2]))
        speeds.append(_vmag(v))
        if prev[3] is not None:
            a = tuple(c / dt for c in _vsub(v, prev[3]))
            accels.append(_vmag(a))
            predicted = tuple(prev[2][k] + prev[3][k] * dt for k in range(3))
            pe = _vmag(_vsub(pos, predicted))
            pe_world.append(pe)
            pen = pe / (dt * dt) if dt > 0 else 0.0
            pe_norm.append(pen)
            pred_errors.append((frame, pe, pen))
        prev = (frame, epoch, pos, v)
    return {
        "steps": len(speeds),
        "max_speed": max(speeds, default=0.0),
        "mean_speed": (sum(speeds) / len(speeds)) if speeds else 0.0,
        "max_accel": max(accels, default=0.0),
        "mean_accel": (sum(accels) / len(accels)) if accels else 0.0,
        "max_prediction_error": max(pe_world, default=0.0),
        "mean_prediction_error": (sum(pe_world) / len(pe_world)) if pe_world else 0.0,
        "max_prediction_error_per_dt2": max(pe_norm, default=0.0),
        "mean_prediction_error_per_dt2": (sum(pe_norm) / len(pe_norm)) if pe_norm else 0.0,
        "prediction_errors": pred_errors,
    }


def _phase_for_frame(frame: int, phases: list) -> str:
    name = "unknown"
    for f, p in phases:
        if frame >= f:
            name = p
        else:
            break
    return name


def _phase_block(rows: list, phases: list) -> dict:
    """Kinematics for a phase-tagged row subset (used for the straight-only continuity gate and the
    per-phase breakdown). Returns raw vs smoothed aggregates + introduced lag for these rows."""
    if not rows:
        return {"frames": 0}
    # reviewer finding 1: raw target = sampler clock; smoothed presentation = process clock.
    raw = _kinematics(rows, "target_position", _row_dt)
    smoothed = _kinematics(rows, "presentation_position", _row_dt_process)
    corrections = [float(r.get("correction_error", 0.0)) for r in rows]
    raw.pop("prediction_errors", None)
    smoothed.pop("prediction_errors", None)
    return {
        "frames": len(rows),
        "raw_target": raw,
        "smoothed_presentation": smoothed,
        "introduced_lag": {
            "max_correction_error": max(corrections, default=0.0),
            "mean_correction_error": sum(corrections) / len(corrections) if corrections else 0.0,
        },
        "smoothing_reduces_acceleration": smoothed["max_accel"] < raw["max_accel"],
        "smoothing_reduces_prediction_error": smoothed["max_prediction_error"] < raw["max_prediction_error"],
    }


def _contiguous(rows: list) -> list[list]:
    """Split rows into runs of consecutive process_frame within the same presentation_epoch (no
    metric may cross a frame gap or an epoch reset)."""
    runs, cur = [], []
    prev = None  # (frame, epoch)
    for r in rows:
        frame = int(r["process_frame"])
        epoch = int(r.get("presentation_epoch", 0))
        if prev is not None and (frame == prev[0] + 1) and (epoch == prev[1]):
            cur.append(r)
        else:
            if cur:
                runs.append(cur)
            cur = [r]
        prev = (frame, epoch)
    if cur:
        runs.append(cur)
    return runs


def _overshoot(rows: list) -> dict:
    """reviewer finding 2: prediction error is NOT overshoot. Measure SIGNED along-motion overshoot of
    the smoothed presentation: project residual (presentation_i - target_i) onto the travel direction
    (target_i - presentation_{i-1}). Positive => presentation advanced PAST the target along the
    direction it was being pulled (genuine overshoot). Negative => it lags behind (expected low-pass
    behavior, NOT overshoot). A single-pole lerp cannot overshoot a receding target in one step; this
    exists to distinguish real overshoot (e.g. a Hermite replay) from mere deceleration lag."""
    max_over, sum_over, over_frames, n = 0.0, 0.0, 0, 0
    max_signed = -math.inf
    eps = 1e-6
    for run in _contiguous(rows):
        for i in range(1, len(run)):
            pres_prev = _as_vec3(run[i - 1].get("presentation_position"))
            tgt = _as_vec3(run[i].get("target_position"))
            pres = _as_vec3(run[i].get("presentation_position"))
            if pres_prev is None or tgt is None or pres is None:
                continue
            travel = _vsub(tgt, pres_prev)
            tmag = _vmag(travel)
            if tmag < eps:
                continue
            d = tuple(c / tmag for c in travel)
            residual = _vsub(pres, tgt)
            signed = sum(residual[k] * d[k] for k in range(3))
            n += 1
            max_signed = max(max_signed, signed)
            if signed > 0.0:
                over_frames += 1
                sum_over += signed
                max_over = max(max_over, signed)
    return {
        "frames_evaluated": n,
        "max_positive_overshoot": max_over,
        "mean_positive_overshoot": (sum_over / over_frames) if over_frames else 0.0,
        "overshoot_frames": over_frames,
        "max_signed_along_motion": (max_signed if max_signed > -math.inf else 0.0),
        # interpretation aid, not a gate: a one-pole lerp normally shows 0 overshoot frames (pure lag).
        "is_pure_lag": over_frames == 0 and n > 0,
        # reviewer finding 5: for the OUTER one-pole lerp this is tautological -- blend in [0,1] always
        # places the new presentation between its previous pose and the target, so the residual along
        # the pulled direction is non-positive by construction. Path safety of a CANDIDATE scheme is
        # judged by the replay's authority_bow_max + along_chord_overshoot_max, NOT by this metric.
        "tautological_for_one_pole_lerp": True,
    }


def _perspective_ownership(parsed: dict) -> dict:
    """reviewer finding 7 / NI-6 (FL-4359): confirm perspective ownership held THROUGHOUT and fail
    closed. The expected probe tags are derived from the phases actually present (window_start + each
    phase + window_end). gate_pass requires EVERY expected probe to be present AND perspective AND
    on-foot -- missing probes or a mid-run ownership flip cannot coexist with a pass."""
    probes = parsed.get("persp", [])
    phases = [p for _, p in parsed.get("phases", [])]
    expected = ["window_start"] + phases + ["window_end"]
    seen = {str(p.get("tag")): p for p in probes}
    missing = [t for t in expected if t not in seen]
    held = bool(probes) and all(bool(p.get("is_perspective")) for p in probes)
    on_foot = bool(probes) and all(not bool(p.get("is_pilot")) for p in probes)
    return {
        "probes": len(probes),
        "tags": [str(p.get("tag", "?")) for p in probes],
        "expected_tags": expected,
        "missing_tags": missing,
        "perspective_held_throughout": held,
        "on_foot_throughout": on_foot,
        "first_non_perspective_tag": next((str(p.get("tag")) for p in probes
                                           if not bool(p.get("is_perspective"))), None),
        # rework#6 NI-5: this proves ownership AT the expected probes (phase boundaries), not on every
        # measured frame. Honest label = all_expected_probes_pass. Per-frame proof would require
        # emitting ownership in each LERP receipt; phase-boundary sampling is adequate for this harness.
        "sampling": "phase_boundary_probes",
        "all_expected_probes_pass": bool(len(probes) > 0 and not missing and held and on_foot),
        # gate_pass retained as an alias for the orchestrator/step11 (same value).
        "gate_pass": bool(len(probes) > 0 and not missing and held and on_foot),
    }


def _lerp(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def _hermite(p0, p1, m0, m1, t):
    """Cubic Hermite on param t in [0,1] with tangents m0,m1 (already scaled to the segment)."""
    t2 = t * t
    t3 = t2 * t
    h00 = 2 * t3 - 3 * t2 + 1
    h10 = t3 - 2 * t2 + t
    h01 = -2 * t3 + 3 * t2
    h11 = t3 - t2
    return tuple(h00 * p0[k] + h10 * m0[k] + h01 * p1[k] + h11 * m1[k] for k in range(3))


def _hermite_deriv(p0, p1, m0, m1, t):
    """ANALYTIC dP/dt (param space) of the Hermite (reviewer NI-4): exact, NOT a finite difference of
    rendered samples. At t=1 this is m1 (=v_end*dt_seg); at t=0 it is m0 (=v_start*dt_seg)."""
    t2 = t * t
    dh00 = 6 * t2 - 6 * t
    dh10 = 3 * t2 - 4 * t + 1
    dh01 = -6 * t2 + 6 * t
    dh11 = 3 * t2 - 2 * t
    return tuple(dh00 * p0[k] + dh10 * m0[k] + dh01 * p1[k] + dh11 * m1[k] for k in range(3))


def _chord_aligned_tangents(p0, p1, m0, m1, corridor_frac=CORRIDOR_FRAC):
    """reviewer NI-8: replace per-axis Fritsch-Carlson (which only bounds a per-coordinate AABB and
    can still bow far off the 3D chord) with a CHORD-ALIGNED limiter. Decompose each tangent into a
    component ALONG the chord (clamped to [0, 3L] so it cannot overshoot the endpoints along travel)
    and a PERPENDICULAR component (capped at corridor_frac*L so lateral bow stays inside a bounded
    corridor around the chord).

    reviewer NI-1 (CRIT): the perpendicular part MUST be taken from the RAW projection, not the
    clamped one -- otherwise a negative (backward) tangent's clamped-out longitudinal part reappears
    as a chord-parallel 'perpendicular' vector and defeats the overshoot clamp."""
    chord = _vsub(p1, p0)
    L = _vmag(chord)
    if L < 1e-9:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    u = tuple(c / L for c in chord)
    # control-point perp = tangent_perp / 3, so to keep the control points within the absolute world
    # corridor _corridor_world(L), the tangent perp cap is 3x that (NI-5). Also bounded by the chord
    # fraction. cap = 3 * min(CORRIDOR_PERP_FRAC_LIMIT*L, tol_world) = min(corridor_frac*L, 3*tol_world).
    cap = 3.0 * _corridor_world(L)

    def limit(m):
        raw_along = sum(m[k] * u[k] for k in range(3))
        perp = tuple(m[k] - raw_along * u[k] for k in range(3))   # RAW projection (NI-1)
        along = max(0.0, min(raw_along, 3.0 * L))                 # clamp longitudinal separately
        pmag = _vmag(perp)
        if pmag > cap and pmag > 0.0:
            perp = tuple(c * (cap / pmag) for c in perp)
        return tuple(along * u[k] + perp[k] for k in range(3))

    return limit(m0), limit(m1)


def _hermite_bezier_controls(p0, p1, m0, m1):
    """Cubic Hermite -> Bezier control points (NI-2/finding 7): B0=p0, B1=p0+m0/3, B2=p1-m1/3, B3=p1.
    The convex hull of these four points contains the ENTIRE curve, so bounding the control points
    inside the chord-aligned corridor is an ACTUAL safety proof, not a 21-sample diagnostic."""
    b1 = tuple(p0[k] + m0[k] / 3.0 for k in range(3))
    b2 = tuple(p1[k] - m1[k] / 3.0 for k in range(3))
    return (p0, b1, b2, p1)


def _scheme_tangents(seg: dict, scheme: str):
    """Param-space tangents for a scheme on a segment ('linear' -> chord-secant tangents that make the
    Hermite reproduce the straight chord; hermite variants -> velocity*dt, bounded -> chord-limited).

    NI-3 (rework #7): seg['v0']/seg['v1'] are already in WORLD u/s (converted from the per-tick receipt
    at ingestion), so the param-space tangent m = v_per_second * dt_seconds, which equals
    v_per_tick * tick_span. The earlier code multiplied the RAW per-tick velocity by dt_seconds, making
    every tangent tick_rate_hz x too small (a flat ~chord/30 tangent that collapsed the Hermite toward
    a straight line and corrupted the bow/corridor/continuity verdict)."""
    p0, p1 = seg["p0"], seg["p1"]
    if scheme == "linear":
        chord = _vsub(p1, p0)
        return chord, chord  # Hermite with m0=m1=chord == straight line
    # v is u/s; m = v_us * dt_seconds == v_per_tick * tick_span (NI-3, unit-correct).
    m0 = tuple(seg["v0"][k] * seg["dt"] for k in range(3))
    m1 = tuple(seg["v1"][k] * seg["dt"] for k in range(3))
    if scheme == "bounded_hermite":
        m0, m1 = _chord_aligned_tangents(p0, p1, m0, m1)
    return m0, m1


def _corridor_certificate(seg: dict, scheme: str) -> dict:
    """NI-2/finding 7: bound the curve via its Bezier control points. along-chord coordinate of every
    control point must lie in [0, L] (no longitudinal overshoot) and the perpendicular offset must be
    <= the ABSOLUTE world corridor _corridor_world(L) (NI-5). Convex hull => proves the whole curve."""
    p0, p1 = seg["p0"], seg["p1"]
    chord = _vsub(p1, p0)
    L = _vmag(chord)
    if L < 1e-9:
        return {"in_corridor": True, "max_control_perp_world": 0.0,
                "max_control_perp_over_chord": 0.0, "corridor_world": 0.0, "along_in_bounds": True}
    u = tuple(c / L for c in chord)
    m0, m1 = _scheme_tangents(seg, scheme)
    controls = _hermite_bezier_controls(p0, p1, m0, m1)
    max_perp_world, along_ok = 0.0, True
    for c in controls:
        rel = _vsub(c, p0)
        s = sum(rel[k] * u[k] for k in range(3))
        perp = _vmag(tuple(rel[k] - s * u[k] for k in range(3)))
        max_perp_world = max(max_perp_world, perp)
        if s < -1e-9 or s > L + 1e-9:
            along_ok = False
    corridor_world = _corridor_world(L)
    return {
        "in_corridor": bool(along_ok and max_perp_world <= corridor_world + 1e-9),
        "max_control_perp_world": max_perp_world,
        "max_control_perp_over_chord": max_perp_world / L,
        "corridor_world": corridor_world,
        "along_in_bounds": along_ok,
    }


def _build_segment_table(rows: list) -> list[dict]:
    """Reconstruct ordered, VALID interpolation segments from interpolate-mode frames (reviewer NI-7;
    FL-4359: excludes segments lacking seg_endpoints_valid so absent authority fails closed rather
    than defaulting to zero). One entry per distinct (epoch, seg_prev_tick, seg_next_tick); endpoints
    are constant within a segment. Each: {epoch, t0, t1, p0, p1, v0, v1, hz, dt}. Ordered by (epoch, t0)."""
    segs: dict[tuple, dict] = {}
    for r in rows:
        if str(r.get("interp_mode")) != "interpolate":
            continue
        if not bool(r.get("seg_endpoints_valid", False)):
            continue
        hz = float(r.get("tick_rate_hz", 0.0))
        t0 = float(r.get("seg_prev_tick", -1.0))
        t1 = float(r.get("seg_next_tick", -1.0))
        p0 = _as_vec3(r.get("seg_prev_position"))
        p1 = _as_vec3(r.get("seg_next_position"))
        v0 = _as_vec3(r.get("seg_prev_velocity"))
        v1 = _as_vec3(r.get("seg_next_velocity"))
        if hz <= 0.0 or t1 <= t0 or None in (p0, p1, v0, v1):
            continue
        ep = int(r.get("presentation_epoch", 0))
        key = (ep, t0, t1)
        if key not in segs:
            # NI-1/NI-2 (rework #7): the receipt velocity is WORLD UNITS PER TICK (SERVER_VELOCITY_UNIT).
            # Convert to u/s ONCE here so every downstream comparison (boundary derivative, secants,
            # tangents) is homogeneous in u/s. tick_span = t1 - t0 (ticks); tangent m = v_us*dt seconds
            # = v_pt*tick_span (NI-3).
            v0ps = tuple(c * hz for c in v0)
            v1ps = tuple(c * hz for c in v1)
            segs[key] = {"epoch": ep, "t0": t0, "t1": t1, "p0": p0, "p1": p1,
                         "v0": v0ps, "v1": v1ps, "v0_per_tick": v0, "v1_per_tick": v1,
                         "hz": hz, "tick_span": (t1 - t0), "dt": (t1 - t0) / hz}
    return sorted(segs.values(), key=lambda s: (s["epoch"], s["t0"]))


def _seg_secant(s: dict) -> tuple:
    """Secant velocity (world u/s) of a segment-table entry: (p1 - p0) / dt."""
    return tuple((s["p1"][k] - s["p0"][k]) / s["dt"] for k in range(3))


def _adjacent_boundaries(table: list[dict]):
    """Yield (segN, segN1) for genuinely adjacent segments (same epoch, segN.t1 == segN1.t0)."""
    for i in range(len(table) - 1):
        a, b = table[i], table[i + 1]
        if a["epoch"] == b["epoch"] and abs(a["t1"] - b["t0"]) < 1e-9:
            yield a, b


def _segment_continuity(rows: list) -> dict:
    """reviewer NI-3 (rework #6): the MEANINGFUL reliability check is the authoritative boundary
    velocity vs the NONUNIFORM three-point derivative at p1 (exact for a quadratic; correct for
    unequal segment durations, unlike the interval-average (p2-p0)/(h1+h2)):
        f'(p1) = -h2/[h1(h1+h2)] p0 + (h2-h1)/(h1 h2) p1 + h1/[h2(h1+h2)] p2
    with h1=segN.dt, h2=segN1.dt (each segment's OWN seconds, not one tick rate across both),
    p0=segN.p0, p1=segN.p1=segN1.p0, p2=segN1.p1. Shared-boundary consistency |v1_N - v0_N1| is
    reported but is ~tautological for correct receipts (same stored snapshot); it only flags receipt
    corruption. Per-endpoint residual (each endpoint velocity vs its own segment secant) is reported
    but EXPLICITLY NOT gated: within a single segment it cannot separate legitimate intra-segment
    acceleration from bad tangents; the cross-boundary derivative is the discriminator. Trustworthy iff
    the three-point-derivative residual is small AND consistency confirms receipt integrity.

    rework #7 (NI-1/NI-2): all velocities here are u/s (the per-tick receipt field is converted at
    ingestion). The boundary velocity vs three-point-derivative comparison is now homogeneous; the prior
    96.8% 'disagreement' was the per-tick vs u/s unit mismatch, not real data drift.

    HIGH#4 relabel: 'max_linear_boundary_mismatch' is |right_secant - left_secant|, a derivative
    discontinuity in WORLD UNITS PER SECOND (a C1/velocity discontinuity) -- NOT a position jump.
    Linear interpolation is position-continuous (C0); what changes at a snapshot boundary is the slope."""
    table = _build_segment_table(rows)
    linear_mismatch, consistency, central_rel, per_endpoint = [], [], [], []
    boundaries = 0
    for a, b in _adjacent_boundaries(table):
        boundaries += 1
        left = _seg_secant(a)
        right = _seg_secant(b)
        linear_mismatch.append(_vmag(_vsub(right, left)))
        consistency.append(_vmag(_vsub(a["v1"], b["v0"])))
        # nonuniform three-point derivative at the shared snapshot (NI-3, asymmetric durations OK).
        h1, h2 = a["dt"], b["dt"]
        p0v, p1v, p2v = a["p0"], a["p1"], b["p1"]
        if h1 > 0 and h2 > 0:
            c0 = -h2 / (h1 * (h1 + h2))
            c1 = (h2 - h1) / (h1 * h2)
            c2 = h1 / (h2 * (h1 + h2))
            central = tuple(c0 * p0v[k] + c1 * p1v[k] + c2 * p2v[k] for k in range(3))
        else:
            central = (0.0, 0.0, 0.0)
        v_boundary = a["v1"]
        cmag = _vmag(central)
        central_rel.append(_vmag(_vsub(v_boundary, central)) / cmag if cmag > 1e-6 else
                           _vmag(_vsub(v_boundary, central)))
        per_endpoint.append(max(_vmag(_vsub(a["v1"], left)), _vmag(_vsub(b["v0"], right))))
    max_cons = max(consistency, default=0.0)
    mean_central_rel = (sum(central_rel) / len(central_rel)) if central_rel else 0.0
    max_central_rel = max(central_rel, default=0.0)
    return {
        "valid_segments": len(table),
        "segment_boundaries": boundaries,
        # HIGH#4: this is a VELOCITY (slope) discontinuity in u/s, not a position jump. Renamed-in-place
        # via the units annotation; the curve is C0 (position-continuous) but C1-discontinuous.
        "max_linear_boundary_mismatch": max(linear_mismatch, default=0.0),
        "mean_linear_boundary_mismatch": (sum(linear_mismatch) / len(linear_mismatch)) if linear_mismatch else 0.0,
        "linear_boundary_mismatch_units": "world_units_per_second",
        "linear_boundary_mismatch_is": "velocity_slope_discontinuity_not_position_jump",
        "endpoint_velocity_reliability": {
            "boundaries": boundaries,
            "max_shared_boundary_inconsistency": max_cons,   # reported; ~tautological for clean receipts
            "max_per_endpoint_secant_residual": max(per_endpoint, default=0.0),  # reported, NOT gated
            "per_endpoint_gated": False,
            "mean_central_difference_rel_residual": mean_central_rel,
            "max_central_difference_rel_residual": max_central_rel,
            "boundary_derivative_method": "nonuniform_three_point",
            "central_difference_duration_aware": True,
            # gate on the nonuniform three-point derivative (discriminator) + receipt integrity (NI-3).
            "trustworthy": (boundaries > 0 and max_cons < 0.05 and max_central_rel < 0.25),
        },
    }


def _scheme_seg_target(seg: dict, scheme: str, t: float):
    """Recompute one segment's curve position at param t under a scheme (uses table endpoints)."""
    m0, m1 = _scheme_tangents(seg, scheme)
    return _hermite(seg["p0"], seg["p1"], m0, m1, t)


def _scheme_seg_deriv(seg: dict, scheme: str, t: float):
    """ANALYTIC world-space derivative (u/s) of a segment's curve at param t under a scheme."""
    m0, m1 = _scheme_tangents(seg, scheme)
    dparam = _hermite_deriv(seg["p0"], seg["p1"], m0, m1, t)
    return tuple(c / seg["dt"] for c in dparam)


def _scheme_target(r, scheme: str):
    """Recompute the interpolation TARGET for a rendered interpolate-mode frame (used by the
    faithfulness control + second-lerp re-sim). None unless interpolate mode with VALID endpoints."""
    if str(r.get("interp_mode")) != "interpolate" or not bool(r.get("seg_endpoints_valid", False)):
        return None
    p0 = _as_vec3(r.get("seg_prev_position"))
    p1 = _as_vec3(r.get("seg_next_position"))
    if p0 is None or p1 is None:
        return None
    t = float(r.get("alpha", 0.0))
    if scheme == "linear":
        return _lerp(p0, p1, t)
    hz = float(r.get("tick_rate_hz", 0.0))
    t0 = float(r.get("seg_prev_tick", -1.0))
    t1 = float(r.get("seg_next_tick", -1.0))
    v0 = _as_vec3(r.get("seg_prev_velocity"))
    v1 = _as_vec3(r.get("seg_next_velocity"))
    if hz <= 0.0 or t1 <= t0 or v0 is None or v1 is None:
        return None
    # NI-1/NI-2 (rework #7): convert per-tick receipt velocity to u/s, identical to _build_segment_table.
    seg = {"p0": p0, "p1": p1,
           "v0": tuple(c * hz for c in v0), "v1": tuple(c * hz for c in v1),
           "tick_span": (t1 - t0), "dt": (t1 - t0) / hz}
    return _scheme_seg_target(seg, scheme, t)


def _replay_schemes(rows: list, follow_rate: float) -> dict:
    """Next-Instruction items 5/6/8: OFFLINE replay through linear, velocity Hermite, and bounded
    (chord-aligned) Hermite. Boundary continuity is computed ANALYTICALLY from the segment table
    (NI-4, not finite differences of rendered samples). Authority bow + along-chord overshoot are
    measured by DENSE analytic sampling of each segment's curve (NI-8 gates on lateral bow too). Lag
    re-simulates the second lerp over rendered frames (process clock); stop lag is PHASE-EXACT
    (_phase == 'stop', NI-5). 'linear' is the faithfulness control. NOTHING here authorizes a change."""
    schemes = ("linear", "velocity_hermite", "bounded_hermite")
    table = _build_segment_table(rows)
    cam = _camera_projection(rows)  # NI-7: projected-cell corridor inputs (fails shut if absent)
    DENSE = 21  # analytic samples per segment for bow/overshoot (cross-check; the proof is the cert)
    # NI-5: candidate coverage -- fraction of interpolate frames with VALID endpoints. Authorization
    # requires 100%; replay must never silently mix Hermite + linear-substituted frames.
    interp_total = sum(1 for r in rows if str(r.get("interp_mode")) == "interpolate")
    interp_valid = sum(1 for r in rows if str(r.get("interp_mode")) == "interpolate"
                       and bool(r.get("seg_endpoints_valid", False)))
    candidate_coverage = (interp_valid / interp_total) if interp_total else 0.0
    # faithfulness control: recomputed linear target at rendered alpha vs emitted target_position.
    faith = 0.0
    for r in rows:
        rt = _scheme_target(r, "linear")
        tgt = _as_vec3(r.get("target_position"))
        if rt is not None and tgt is not None:
            faith = max(faith, _vmag(_vsub(rt, tgt)))
    out: dict[str, dict] = {}
    for scheme in schemes:
        # analytic boundary continuity: |start_deriv(N+1) - end_deriv(N)| per genuine boundary, plus a
        # NORMALIZED variant (mismatch / local secant speed) for an absolute, scale-free gate (NI-4).
        boundary_mis, boundary_mis_norm = [], []
        for a, b in _adjacent_boundaries(table):
            end_n = _scheme_seg_deriv(a, scheme, 1.0)
            start_n1 = _scheme_seg_deriv(b, scheme, 0.0)
            mm = _vmag(_vsub(start_n1, end_n))
            boundary_mis.append(mm)
            ref = max(_vmag(_seg_secant(a)), _vmag(_seg_secant(b)), 1e-6)
            boundary_mis_norm.append(mm / ref)
        # Bezier corridor SAFETY PROOF (NI-2/7) + dense bow/overshoot CROSS-CHECK (normalized, NI-3)
        bow_over_chord_max, overshoot_max = 0.0, 0.0
        cert_perp_max, cert_perp_world_max, cert_safe, cert_along_ok = 0.0, 0.0, True, True
        substituted_interp = 0
        for seg in table:
            chord = _vsub(seg["p1"], seg["p0"])
            clen = _vmag(chord)
            if clen <= 1e-9:
                continue
            cert = _corridor_certificate(seg, scheme)
            cert_perp_max = max(cert_perp_max, cert["max_control_perp_over_chord"])
            cert_perp_world_max = max(cert_perp_world_max, cert["max_control_perp_world"])
            cert_safe = cert_safe and cert["in_corridor"]
            cert_along_ok = cert_along_ok and cert["along_in_bounds"]
            u = tuple(c / clen for c in chord)
            for j in range(DENSE):
                t = j / (DENSE - 1)
                pos = _scheme_seg_target(seg, scheme, t)
                rel = _vsub(pos, seg["p0"])
                s = sum(rel[k] * u[k] for k in range(3))  # along-chord distance (world u)
                perp = tuple(rel[k] - s * u[k] for k in range(3))
                bow_over_chord_max = max(bow_over_chord_max, _vmag(perp) / clen)  # normalized
                overshoot_max = max(overshoot_max, max(0.0, s - clen), max(0.0, -s))
        # re-simulate the SECOND lerp over rendered frames (process clock); phase-exact stop lag.
        # NI-6: initialize from the REAL recorded presentation state at window start, not the target.
        pres_pe_max, lags, stop_lags = 0.0, [], []
        for run in _contiguous(rows):
            seq = []  # (r, scheme_target, is_substituted_interp)
            ok = True
            for r in run:
                st = _scheme_target(r, scheme)
                is_interp = str(r.get("interp_mode")) == "interpolate"
                sub = (st is None and is_interp)  # invalid interpolate frame -> would be substituted
                if sub:
                    substituted_interp += 1
                base = st if st is not None else _as_vec3(r.get("target_position"))
                if base is None:
                    ok = False
                    break
                seq.append((r, base))
            if not ok or len(seq) < 2:
                continue
            # NI-6 + rework#6 NI-2: start from the REAL recorded presentation state at row 0 and do
            # NOT re-apply row 0's lerp (that double-counted the first frame). Row 0 contributes its
            # recorded state directly; simulation advances from row 1.
            pres = _as_vec3(seq[0][0].get("presentation_position")) or seq[0][1]
            r0, base0 = seq[0]
            sim = [(r0, base0, pres)]
            lags.append(_vmag(_vsub(pres, base0)))
            if str(r0.get("_phase")) == "stop":
                stop_lags.append(_vmag(_vsub(pres, base0)))
            for r, base in seq[1:]:
                d = _row_dt_process(r)
                blend = max(0.0, min(1.0, 1.0 - math.exp(-follow_rate * d))) if d > 0.0 else 0.0
                pres = _lerp(pres, base, blend)
                sim.append((r, base, pres))
                lags.append(_vmag(_vsub(pres, base)))
                if str(r.get("_phase")) == "stop":          # NI-5: phase-exact, not last-25%
                    stop_lags.append(_vmag(_vsub(pres, base)))
            pprev = None
            for r, base, pres in sim:
                d = _row_dt_process(r)
                if d > 0.0 and pprev is not None:
                    v = tuple((pres[k] - pprev[0][k]) / d for k in range(3))
                    if pprev[1] is not None:
                        pred = tuple(pprev[0][k] + pprev[1][k] * d for k in range(3))
                        pres_pe_max = max(pres_pe_max, _vmag(_vsub(pres, pred)))
                    pprev = (pres, v)
                elif d > 0.0:
                    pprev = (pres, None)
        # NI-7: project the worst-case Bezier control-point lateral offset (world) to ANSI cells using
        # the nearest camera distance (largest projection). Fails shut (None) when camera data absent.
        if cam.get("available"):
            perp_cells = _project_world_perp_to_cells(cert_perp_world_max, cam["min_distance"], cam["focal_cells"])
            cells_within_tol = bool(perp_cells <= PROJECTED_CORRIDOR_CELL_TOLERANCE + 1e-9)
        else:
            perp_cells = None
            cells_within_tol = None
        out[scheme] = {
            "analytic_max_boundary_mismatch": max(boundary_mis, default=0.0),
            "analytic_mean_boundary_mismatch": (sum(boundary_mis) / len(boundary_mis)) if boundary_mis else 0.0,
            "analytic_max_boundary_mismatch_normalized": max(boundary_mis_norm, default=0.0),
            "analytic_boundary_mismatch_units": "world_units_per_second",  # HIGH#4: slope, not position
            # NI-2/7 corridor SAFETY PROOF via Bezier convex hull (world units + normalized, NI-5):
            "bezier_corridor_safe": bool(cert_safe),
            "bezier_max_control_perp_world": cert_perp_world_max,
            "bezier_max_control_perp_over_chord": cert_perp_max,
            "bezier_along_in_bounds": bool(cert_along_ok),
            # NI-7 PRIMARY corridor: projected lateral bow in ANSI cells (None => fail-shut, no cam data).
            "bezier_max_control_perp_cells": perp_cells,
            "projected_corridor_within_cell_tolerance": cells_within_tol,
            # NI-3 normalized bow + along-chord overshoot (dense cross-check of the proof):
            "max_bow_over_chord": bow_over_chord_max,
            "along_chord_overshoot_max": overshoot_max,
            "resim_presentation_max_prediction_error": pres_pe_max,
            "resim_lag_mean": (sum(lags) / len(lags)) if lags else 0.0,
            "resim_lag_max": max(lags, default=0.0),  # row-0 lag = real recorded offset (no re-lerp)
            # NI-5: None (not a fabricated 0) when the capture has no stop phase.
            "resim_stop_lag_mean": (sum(stop_lags) / len(stop_lags)) if stop_lags else None,
            "resim_stop_frames": len(stop_lags),
            "substituted_interp_frames": substituted_interp,
        }
    return {
        "valid_segments": len(table),
        "boundaries_analyzed": sum(1 for _ in _adjacent_boundaries(table)),
        "candidate_coverage": candidate_coverage,                 # NI-5: must be 1.0 to authorize
        "interpolate_frames_total": interp_total,
        "interpolate_frames_valid": interp_valid,
        "corridor_perp_frac_limit": CORRIDOR_PERP_FRAC_LIMIT,
        "corridor_lateral_tolerance_world": PRESENTATION_LATERAL_TOLERANCE_WORLD,
        # NI-7: projected-cell corridor is the PRIMARY safety definition; world tolerance is fallback.
        "projected_corridor": {
            "cell_tolerance": PROJECTED_CORRIDOR_CELL_TOLERANCE,
            "available": bool(cam.get("available")),
            "focal_cells": cam.get("focal_cells"),
            "min_camera_distance": cam.get("min_distance"),
            "fallback_world_tolerance": PRESENTATION_LATERAL_TOLERANCE_WORLD,
            "unavailable_reason": cam.get("reason"),
            "instrumentation_gap": (None if cam.get("available") else
                                    "add camera_focal_cells + camera_player_distance to FL4359_LERP_RECEIPT"),
        },
        "linear_reproduces_emitted_target_max_residual": faith,   # faithfulness control (~0 expected)
        "schemes": out,
        "note": ("Offline replay only. boundary mismatch is ANALYTIC; corridor safety is PROVEN via "
                 "Bezier convex hull (bow/overshoot dense values are a cross-check). stop lag is "
                 "phase-exact; re-sim starts from the real recorded presentation state. 'linear' is "
                 "the faithfulness control; velocity_hermite is unconstrained; bounded_hermite is the "
                 "chord-aligned corridor candidate. No scheme adopted -- reviewer-gated (NI-9)."),
    }


def analyze_lerp_continuity(parsed: dict, args: argparse.Namespace) -> dict:
    """Step-10 report on the local-player presentation SECOND lerp. INFORMATIONAL (not in pose
    all_pass). Measures in the SAMPLER's render-tick clock, separates straight/turn/stop phases (the
    continuity gate uses STRAIGHT only -- turn/stop accel is legitimate, reviewer findings 3-4), and
    correlates the biggest RAW discontinuities with segment change / alpha jump / monotonic-clamp /
    snapshot arrival so a root cause can be assigned to segment-internal vs boundary (finding 5).
    It does NOT assert a root cause."""
    window_start = parsed["window_start"]
    phases = parsed.get("phases", [])
    rows = [parsed["lerp"][f] for f in sorted(parsed["lerp"])
            if window_start is None or f >= window_start]
    n = len(rows)
    if n == 0:
        return {"present": False, "frames": 0}
    for r in rows:
        r["_phase"] = _phase_for_frame(int(r["process_frame"]), phases)
    mode_counts: dict[str, int] = {}
    for r in rows:
        m = str(r.get("interp_mode", "unknown"))
        mode_counts[m] = mode_counts.get(m, 0) + 1
    epochs = sorted({int(r.get("presentation_epoch", 0)) for r in rows})
    sampler_dts = [_row_dt(r) for r in rows if _row_dt(r) > 0.0]

    raw = _kinematics(rows, "target_position", _row_dt)  # raw target: SAMPLER clock (finding 1)

    # Correlate the biggest raw discontinuities with segment/alpha/clamp/snapshot (finding 5).
    by_frame = {int(r["process_frame"]): r for r in rows}
    top = sorted(raw["prediction_errors"], key=lambda x: x[1], reverse=True)[:8]
    top_discontinuities = []
    for frame, pe, pe_norm in top:
        r = by_frame.get(frame, {})
        prev = by_frame.get(frame - 1, {})
        seg_now = (r.get("seg_prev_tick"), r.get("seg_next_tick"))
        seg_prev = (prev.get("seg_prev_tick"), prev.get("seg_next_tick"))
        segment_changed = bool(prev) and seg_now != seg_prev
        alpha_now = float(r.get("alpha", -1.0))
        alpha_prev = float(prev.get("alpha", -1.0)) if prev else -1.0
        # alpha "wrap": dropped sharply (segment advanced) rather than progressing upward.
        alpha_dropped = bool(prev) and alpha_now >= 0 and alpha_prev >= 0 and (alpha_now - alpha_prev) < -0.25
        snap_arrived = (bool(prev) and r.get("newest_tick") is not None
                        and float(r.get("newest_tick", -1)) > float(prev.get("newest_tick", -1)))
        top_discontinuities.append({
            "frame": frame,
            "phase": r.get("_phase", "unknown"),
            "prediction_error": pe,
            "prediction_error_per_dt2": pe_norm,
            "interp_mode": str(r.get("interp_mode", "unknown")),
            "segment_changed": segment_changed,
            "alpha": alpha_now,
            "alpha_dropped": alpha_dropped,
            "monotonic_clamp_active": bool(r.get("monotonic_clamp_active", False)),
            "snapshot_arrived": bool(snap_arrived),
            "buffer_size": int(r.get("buffer_size", -1)),
        })
    raw.pop("prediction_errors", None)

    # Per-phase breakdown; the continuity GATE uses STRAIGHT only (steps 3-4).
    by_phase = {}
    for ph in sorted({r["_phase"] for r in rows}):
        by_phase[ph] = _phase_block([r for r in rows if r["_phase"] == ph], phases)
    straight = by_phase.get("straight", {})

    # New evidence surfaces (reviewer findings 2/4/7 + Next-Instruction items 3/4/5/6).
    overshoot = _overshoot(rows)                       # finding 2/3: real signed overshoot vs lag
    segment_continuity = _segment_continuity(rows)     # finding 4: direct C0 boundary mismatch
    replay = _replay_schemes(rows, PRESENTATION_FOLLOW_RATE)  # items 5/6: offline 3-scheme replay
    perspective = _perspective_ownership(parsed)        # finding 7: ownership held throughout

    # step11_decision: evidence summary ONLY. Adoption is reviewer-gated (NI-9: NOT authorized from
    # this commit). Records the item-7 pre-conditions, now using ANALYTIC boundary mismatch and gating
    # bounded Hermite on BOTH along-chord overshoot AND lateral authority bow (NI-8). Bow is bounded
    # relative to the segment's own scale via the analyzer's corridor cap; reported absolute here.
    rel = segment_continuity["endpoint_velocity_reliability"]
    schemes = replay["schemes"]
    lin_s = schemes.get("linear", {})
    bh_s = schemes.get("bounded_hermite", {})
    vh_s = schemes.get("velocity_hermite", {})
    # NI-4: material boundary improvement -- both a >=50% reduction vs linear AND an absolute
    # normalized residual ceiling. A 1% / numerical-noise reduction no longer counts.
    lin_mm = lin_s.get("analytic_max_boundary_mismatch", 0.0)
    bh_mm = bh_s.get("analytic_max_boundary_mismatch", math.inf)
    if lin_mm > 1e-12:
        reduction_ratio = bh_mm / lin_mm
    else:
        reduction_ratio = 0.0 if bh_mm <= 1e-12 else math.inf
    bounded_improves_boundary = (
        reduction_ratio <= BOUNDARY_REDUCTION_RATIO_MAX
        and bh_s.get("analytic_max_boundary_mismatch_normalized", math.inf) <= BOUNDARY_NORM_RESIDUAL_LIMIT
    )
    # NI-2/7: world Bezier convex-hull proof is the FALLBACK corridor; projected-cell is primary.
    bounded_corridor_safe = bool(bh_s.get("bezier_corridor_safe", False))
    # NI-5: gate NORMALIZED per-segment bow against the corridor fraction limit, not equality with 0.
    bounded_bow_within_corridor = bh_s.get("max_bow_over_chord", math.inf) <= CORRIDOR_PERP_FRAC_LIMIT + 1e-9
    bounded_overshoot_bounded = (bh_s.get("along_chord_overshoot_max", math.inf) <= 1e-6)
    bounded_no_fabricated_motion = bool(
        bounded_corridor_safe and bounded_bow_within_corridor and bounded_overshoot_bounded
    )
    # NI-7: the PRIMARY corridor is projected ANSI cells. It is proven only when the capture carries
    # camera projection data AND the bounded curve's projected bow is within the sub-cell tolerance.
    # Absent that data it is UNPROVEN (fail-shut) -- C1 cannot be authorized on the world fallback alone.
    proj = replay.get("projected_corridor", {})
    projected_corridor_available = bool(proj.get("available", False))
    bounded_projected_within_tol = bh_s.get("projected_corridor_within_cell_tolerance", None)
    projected_corridor_proven = bool(projected_corridor_available and bounded_projected_within_tol is True)
    # NI-5: never authorize from a candidate metric mixed with linear-substituted frames.
    full_coverage = (replay.get("candidate_coverage", 0.0) >= 1.0
                     and bh_s.get("substituted_interp_frames", 1) == 0)
    step11_decision = {
        "status": "pending_reviewer_authorization",
        "endpoint_velocities_trustworthy": bool(rel.get("trustworthy", False)),
        "bounded_hermite_improves_boundary_continuity": bool(bounded_improves_boundary),
        "boundary_reduction_ratio": reduction_ratio,
        "boundary_reduction_ratio_max": BOUNDARY_REDUCTION_RATIO_MAX,
        "bounded_hermite_corridor_safe_bezier_proof": bounded_corridor_safe,
        "bounded_hermite_normalized_bow_within_corridor": bool(bounded_bow_within_corridor),
        "bounded_hermite_overshoot_bounded": bool(bounded_overshoot_bounded),
        "bounded_hermite_no_fabricated_motion": bounded_no_fabricated_motion,
        # NI-7: projected-cell corridor (primary). Unproven => fail-shut, blocks authorization.
        "projected_corridor_available": projected_corridor_available,
        "bounded_hermite_projected_within_cell_tolerance": bounded_projected_within_tol,
        "projected_corridor_proven": projected_corridor_proven,
        "velocity_hermite_overshoots": bool(vh_s.get("along_chord_overshoot_max", 0.0) > 1e-6),
        "velocity_hermite_max_bow_over_chord": vh_s.get("max_bow_over_chord", 0.0),
        "candidate_full_valid_coverage": bool(full_coverage),
        "replay_faithful": bool(replay["linear_reproduces_emitted_target_max_residual"] < 1e-6),
        "perspective_gate_pass": bool(perspective.get("gate_pass", False)),
        "preconditions_for_item7_met": bool(
            rel.get("trustworthy", False) and bounded_improves_boundary
            and bounded_no_fabricated_motion and projected_corridor_proven and full_coverage
            and perspective.get("gate_pass", False)
            and replay["linear_reproduces_emitted_target_max_residual"] < 1e-6
        ),
        "note": ("Item 7 adoption requires reviewer sign-off even when preconditions are met; "
                 "unconstrained Catmull-Rom/velocity_hermite is explicitly disallowed for "
                 "authoritative player presentation (NI-9: not authorized from this commit). The "
                 "projected-cell corridor (NI-7) is the primary safety gate and is UNPROVEN until the "
                 "receipt carries camera_focal_cells + camera_player_distance."),
    }

    return {
        "present": n >= args.min_joined_frames,
        "frames": n,
        "epochs": epochs,
        "phases_seen": [p for _, p in phases],
        "interp_mode_counts": mode_counts,
        "extrapolate_fraction": mode_counts.get("extrapolate", 0) / n if n else 0.0,
        "monotonic_clamp_frames": sum(1 for r in rows if bool(r.get("monotonic_clamp_active", False))),
        "sampler_dt_seconds": {
            "min": min(sampler_dts, default=0.0),
            "max": max(sampler_dts, default=0.0),
            "mean": (sum(sampler_dts) / len(sampler_dts)) if sampler_dts else 0.0,
            "frozen_clock_frames": sum(1 for r in rows if _row_dt(r) <= 0.0),
        },
        "all_phases_raw_target": raw,
        "by_phase": by_phase,
        "straight_only_gate": {
            # the continuity gate is the STRAIGHT window only (steps 3-4). Reported, not thresholded.
            "frames": straight.get("frames", 0),
            "raw_max_prediction_error": straight.get("raw_target", {}).get("max_prediction_error", 0.0),
            "raw_max_prediction_error_per_dt2": straight.get("raw_target", {}).get("max_prediction_error_per_dt2", 0.0),
            "smoothed_max_prediction_error": straight.get("smoothed_presentation", {}).get("max_prediction_error", 0.0),
            "introduced_lag": straight.get("introduced_lag", {}),
        },
        "top_discontinuities": top_discontinuities,
        # HIGH#5 (rework #7): clarify the spike metric. 'prediction_error' is a WORLD-UNIT POSITION
        # prediction error |p_i - (p_{i-1} + v_{i-1}*dt)| with v position-derived (u/s); it is NOT the
        # u/s slope discontinuity (max_linear_boundary_mismatch) and the two are not interchangeable.
        # 'prediction_error_per_dt2' is the acceleration-like (u/s^2) normalization. A large position
        # spike at an extrapolate->interpolate transition was partly an ARTIFACT of the runtime
        # extrapolation unit bug (Main.gd velocity*elapsed_s, ~tick_rate_hz x too small) feeding a
        # discontinuous target_position; that runtime owner is corrected in FL-4359 rework #7 (re-capture
        # to confirm the spike shrinks). Segment-table C1 evaluation excludes non-interpolate frames, so
        # the bow/corridor/continuity verdict is unaffected by the extrapolation-arm contamination.
        "discontinuity_metric_units": {
            "prediction_error": "world_units_position",
            "prediction_error_per_dt2": "world_units_per_second_squared",
            "max_linear_boundary_mismatch": "world_units_per_second_slope_discontinuity",
        },
        "overshoot": overshoot,
        "segment_continuity": segment_continuity,
        "interpolation_replay": replay,
        "perspective_ownership": perspective,
        # explicit: root cause is NOT asserted (reviewer findings 3/6); adoption gated on reviewer.
        "step11_decision": step11_decision,
    }


def run(text: str, args: argparse.Namespace) -> dict:
    parsed = parse_log(text)
    joined = join_frames(parsed)
    stats = analyze(joined, args)
    stats["window_start"] = parsed["window_start"]
    gates = build_gates(stats, parsed, args)
    lerp_continuity = analyze_lerp_continuity(parsed, args)
    return {"stats": stats, "gates": gates, "lerp_continuity": lerp_continuity}


def main() -> int:
    p = argparse.ArgumentParser(description="FL-4359 Phase-2a pose-unification analyzer")
    p.add_argument("path", nargs="?", default="-", help="Capture stdout log (or - for stdin)")
    p.add_argument("--anchor-epsilon", type=float, default=1e-3,
                   help="Max camera_anchor_minus_airplane_position (requirement 7b)")
    p.add_argument("--max-yaw-sign-flip-rate", type=float, default=0.05,
                   help="Max camera_yaw delta sign-flip rate (jitter thrash limit)")
    p.add_argument("--min-joined-frames", type=int, default=8,
                   help="Min joined pilot-perspective frames in window (fail closed below)")
    p.add_argument("--min-yaw-motion", type=float, default=1e-3,
                   help="Min total |yaw delta| (rad) to count the camera as moved")
    p.add_argument("--min-anchor-motion", type=float, default=0.5,
                   help="Min total anchor travel (world units) to count the camera as moved")
    # Step 10 (reviewer): no arbitrary magnitude/jerk threshold. The lerp report is a comparison of
    # raw vs smoothed time-normalized vector kinematics + introduced lag, correlated with snapshot/
    # mode transitions; the step-11 decision is made from that comparison and an on-foot run.
    p.add_argument("--summary-json", default="", help="Write the gate+stat summary JSON here")
    p.add_argument("--fail-on-gate", action="store_true", help="Exit non-zero if any gate fails")
    args = p.parse_args()

    text = sys.stdin.read() if args.path == "-" else open(args.path, "r").read()
    result = run(text, args)

    print(json.dumps(result, indent=2, default=str))
    if args.summary_json:
        with open(args.summary_json, "w") as f:
            json.dump(result, f, indent=2, default=str)

    if args.fail_on_gate and not result["gates"]["all_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

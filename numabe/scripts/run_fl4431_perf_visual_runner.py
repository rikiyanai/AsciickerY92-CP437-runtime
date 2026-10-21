#!/usr/bin/env python3
# =============================================================================
# run_fl4431_perf_visual_runner.py
# =============================================================================
# WHAT THIS IS
# ------------
# The FL-4431 performance work has ONE acceptance rule per patch (operator, 2026-07-15):
#
#     measurable FPS gain  AND  no visible worsening  ->  keep;  else immediate revert.
#
# "No visible worsening" is a DIFFERENTIAL VISUAL REGRESSION check, decided by the
# operator's eye, not by pixel math:
#   * BASELINE  = the current committed build with the candidate gate(s) unset,
#                 including its existing known bugs.
#   * CANDIDATE = the same build with ONE optimization's runtime gate(s) set via
#                 --candidate-env (e.g. FL4431_DISABLE_POSRECON=1 to compare the
#                 record-layout control arm).
#   * Same exact poses / movement routes for both.
#   * Retain only if nothing visible disappears, shifts, stalls, flickers more, or
#     changes lighting / water / airplane presentation / mesh coverage.
#
# HISTORY: this runner originally hardcoded one experiment — native Forward+ raster
# ON vs OFF via FL4431_NATIVE_RASTER_OFF. That experiment is FINISHED: the native
# raster path was permanently deleted at commit 723729a0b (World.gd pins the camera
# cull mask to 0; test_fl4431_native_raster_deleted_contract.py guards the deletion),
# so an env-gated OFF arm no longer exists to compare against. The runner now takes
# the candidate gate from the command line instead of baking in a dead experiment.
# With NO --candidate-env it runs a single "current" arm — a plain measurement pass
# (FPS + motion frames) of the committed renderer, which is the reprofiling step.
#
# This runner produces that comparison for the two motion routes the operator named:
#   * walk_strafe : on-foot walk + strafe at the tower/water perspective (the operator's
#                   recorded 2026-07-13 "tower_strafe" F3 route), FULLSCREEN.
#   * airplane    : airborne scripted circle in the plane_air chase camera, FULLSCREEN.
#
# It does NOT re-implement capture. It ORCHESTRATES the existing, fully-featured motion
# capture engine `run_fl4359_headed_movement.py` (which already boots the real Colyseus
# client/server path, drives the recorded route or the plane circle, runs fullscreen,
# measures FPS, and saves motion-frame screenshots). For each route it runs that engine
# once per arm at the SAME route, then stitches a frame-paired baseline-vs-candidate
# regression grid and tabulates the FPS delta so the operator can make the keep/revert
# call.
#
# WHY FULLSCREEN
# --------------
# Operator mandate 2026-07-14: FPS is measured fullscreen only (the ASCII grid follows the
# real window size), and the airplane proof capture path fail-closes unless fullscreen.
# Both routes therefore run fullscreen here; there is no windowed option on purpose.
#
# HOW TO RUN
# ----------
#   # Reprofile the committed renderer (single "current" arm, both routes):
#   python3 scripts/run_fl4431_perf_visual_runner.py
#
#   # A/B a live optimization gate (baseline = gate unset, candidate = gate set):
#   python3 scripts/run_fl4431_perf_visual_runner.py \
#       --candidate-env FL4431_DISABLE_POSRECON=1 --routes walk_strafe
#
# Outputs (under --out, default .run/fl4431_perf_visual/<route>/):
#   <route>/current/                       : single-arm measurement output, OR
#   <route>/baseline/ , <route>/candidate/ : the raw engine output per A/B arm
#   <route>/regression_grid.png            : frame-paired panels (operator review, A/B only)
#   summary.json                           : per-route FPS (+delta in A/B mode) + arm status
# =============================================================================
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# --- Repo anchors ------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
# The motion capture engine we orchestrate. It owns Colyseus boot, the recorded/plane
# routes, fullscreen, FPS measurement, and motion-frame screenshots.
ENGINE = ROOT / "scripts" / "run_fl4359_headed_movement.py"
OUT_ROOT_DEFAULT = ROOT / ".run" / "fl4431_perf_visual"

# --- Route definitions -------------------------------------------------------
# Each route maps to a set of extra CLI flags for run_fl4359_headed_movement.py. The
# routes are deliberately DETERMINISTIC input (a recorded replay, or a scripted plane
# circle) so the same route replays across the ON and OFF arms — that is what makes an
# ON-vs-OFF frame comparison a fair "same route" regression check.
ROUTES = {
    # On-foot walk + strafe at the cliff-edge tower, perspective camera. This is the
    # operator's recorded 2026-07-13 tower_strafe F3 route; the driver replays it and
    # saves motion-frame screenshots. Perspective = the tower/water subject.
    #
    # "fixed_route": the recorded replay owns its own length and waypoints; the engine
    # validates the actual camera/player path against the recording (a pose envelope). We
    # must NOT pass --move-frames — truncating the replay makes it miss its recorded
    # waypoints and fail with ghost_replay_pose_envelope_mismatch. The engine auto-sets the
    # tower spawn (66.131, 44.123) for this segment, but we pass it explicitly too, exactly
    # like the proven ghost-pulse-gate invocation, so the intent is legible.
    "walk_strafe": {
        "flags": [
            "--camera-mode", "perspective",
            "--ghost-replay-segment", "tower_strafe",
            "--player-spawn-x", "66.131",
            "--player-spawn-z", "44.123",
            # The tower_strafe replay path is wired for the ghost meter and does NOT save
            # the 00/move/after screenshots by default. --fl4359-replay-gif makes the driver
            # save one gif_NNN.png per route frame — those are our walk/strafe motion frames.
            # NOTE the '=' form is REQUIRED: the value itself starts with '--', so argparse
            # would otherwise treat it as a separate flag and reject "expected one argument".
            "--extra-user-arg=--fl4359-replay-gif",
        ],
        "fixed_route": True,
        # The tower_strafe replay path is owned by the ghost meter and does not
        # publish an FPS number (the orbit harness owns FPS for that route), so
        # the validity gate requires receipt+frames but not measured_fps here.
        "measures_fps": False,
    },
    # Airborne scripted circle in the plane_air chase camera. The engine spawns the
    # player already airborne for this mode and flies a repeatable circle whose length is
    # controlled by --move-frames / the plane-circle window.
    "airplane": {
        "flags": [
            "--camera-mode", "plane_air",
        ],
        "fixed_route": False,
        "measures_fps": True,
    },
}

# Every runtime gate family that can silently change what this runner measures.
# Inherited values from the calling shell are REJECTED (decision-grade rule):
# a "current" arm with a leftover FL4431_DISABLE_* in the environment would
# measure the control build while claiming to measure the committed renderer.
GATE_PREFIXES = ("FL4431_", "FL4408_", "FL4359_")


def _inherited_gates() -> dict[str, str]:
    """Return every gate-family env var inherited from the calling shell."""
    return {k: v for k, v in os.environ.items() if k.startswith(GATE_PREFIXES)}


def _arm_local_env(arm_env: dict[str, str]) -> dict[str, str]:
    """
    Build the full environment for one arm: the caller's env with every
    gate-family variable scrubbed, plus exactly this arm's gate values. Handed
    to the engine via subprocess env= — this process's os.environ is never
    mutated, so arms cannot leak into each other or into the calling shell.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(GATE_PREFIXES)}
    env.update(arm_env)
    return env


# --- Arm definitions ---------------------------------------------------------
# An "arm" is (name, env_dict): the environment gate values the Godot client must
# see for that run. The baseline/current arm is always the EMPTY dict (all candidate
# gates unset = the committed renderer as-is); the candidate arm's dict comes from
# the --candidate-env command line, so this runner never hardcodes an experiment
# that a later commit can quietly delete (that is exactly what happened with the
# retired FL4431_NATIVE_RASTER_OFF arm — deleted at 723729a0b).


def _parse_env_specs(specs: list[str]) -> dict[str, str]:
    """
    Turn repeated --candidate-env NAME=VALUE strings into an env dict.
    NAME alone (no '=') means NAME=1, the conventional "gate on" value.
    Exits with a clear message on a malformed spec instead of half-applying it.
    """
    env: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not name or any(c.islower() for c in name):
            print(f"[perf-visual] bad --candidate-env '{spec}' (expected UPPER_SNAKE[=value])",
                  file=sys.stderr)
            raise SystemExit(2)
        env[name] = value if sep else "1"
    return env


def _run_engine(route: str, arm_name: str, arm_env: dict[str, str],
                out_dir: Path, port: int, resolution: str,
                move_frames: int, timeout: float, day_cycle_time: float) -> dict:
    """
    Invoke run_fl4359_headed_movement.py once for a (route, arm) and return a small
    result dict: {ok, measured_fps, fps_frame_time_ms, screenshots[], receipt_path, rc}.

    arm_env        : gate values THIS arm sets (empty for the baseline/current arm)
    day_cycle_time : deterministic time-of-day pin in [0,1) forwarded to the engine
                     (which forwards --fl4408-day-cycle-time= to Godot). The cycle
                     is default-ON in World.gd, so WITHOUT this pin sequential arms
                     would capture different lighting phases and the frame pairs
                     would differ for celestial reasons, not renderer reasons.

    The engine (and the Godot child it launches) receive an ARM-LOCAL environment
    via subprocess env= — built by _arm_local_env with every gate-family variable
    scrubbed first — so neither the calling shell's leftovers nor a previous arm
    can contaminate this measurement.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    engine_log = out_dir / "engine.log"

    route_cfg = ROUTES[route]
    cmd = [
        sys.executable, str(ENGINE),
        "--out", str(out_dir),
        "--port", str(port),
        "--resolution", resolution,
        # Performance receipts remain comparable on the canonical gameplay grid.
        # FL-4359 visual diagnostics default to the operator-selected 32px surface,
        # but fewer cells would silently change this runner's workload.
        "--glyph-matrix-cell-px", "20",
        "--godot-window-mode", "fullscreen",   # operator mandate: fullscreen only
        "--day-cycle-time", str(day_cycle_time),  # deterministic celestial phase
        # FL-4330 2026-07-31: this caller was stale and every route failed for
        # every caller, not just this lane. The capture engine retired its
        # in-process jitter analysis -- the external recorder is now the sole
        # owner of that data -- and it refuses to start at all unless the caller
        # acknowledges that by passing this flag
        # (run_fl4359_headed_movement.py:1286-1292, reason
        # external_recorder_owns_fl4359_jitter_capture). Without it the engine
        # exits rc=2 before creating an output directory, so the runner reported
        # fps=None frames=0 for both arms and no FPS could be measured here at
        # all. This runner only ever wanted the FPS number and the motion frames,
        # both of which survive the skip; it never consumed the jitter rows.
        "--skip-jitter-analysis",
        "--run-classification", "diagnostic-only",
    ]
    # Only scripted routes take a --move-frames length. A recorded replay route
    # (walk_strafe / tower_strafe) owns its own length and waypoints; passing --move-frames
    # would truncate it and trip the pose-envelope check.
    if not route_cfg["fixed_route"]:
        cmd += ["--move-frames", str(move_frames)]
    cmd += route_cfg["flags"]

    with open(engine_log, "w") as log:
        try:
            rc = subprocess.run(cmd, cwd=str(ROOT), stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                env=_arm_local_env(arm_env)).returncode
        except subprocess.TimeoutExpired:
            rc = 124

    # The engine writes receipt.json into --out. Parse the FPS + screenshot evidence.
    receipt_path = out_dir / "receipt.json"
    receipt = {}
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text())
        except json.JSONDecodeError:
            receipt = {}
    # Prefer the receipt's screenshot list; fall back to globbing known frame patterns
    # (the tower_strafe replay writes gif_NNN.png; the plane/walk path writes the named
    # 00_before/move_080/99_after). Sorted so frame order is chronological.
    screenshots = [p for p in receipt.get("screenshots", []) if Path(p).exists()]
    if not screenshots:
        found = sorted(out_dir.glob("gif_*.png")) or [
            out_dir / n for n in ("00_before.png", "move_080.png", "99_after.png")
            if (out_dir / n).exists()]
        screenshots = [str(p) for p in found]
    # DECISION-GRADE VALIDITY (reviewer blocker 4): frames alone are NOT success.
    # An arm is valid only when the engine finished (rc 0), wrote a parseable
    # receipt, produced motion frames, AND — on routes that measure FPS — the
    # receipt actually carries the FPS + frame-time numbers the perf decision
    # needs. The tower_strafe replay route is receipt+frames only by design
    # (see ROUTES["walk_strafe"]["measures_fps"]).
    fps = receipt.get("measured_fps")
    frame_ms = receipt.get("fps_frame_time_ms")
    ok = bool(receipt) and bool(screenshots) and rc == 0
    if route_cfg.get("measures_fps", True):
        ok = ok and fps is not None and frame_ms is not None
    return {
        "ok": ok,
        "rc": rc,
        "measured_fps": fps,
        "fps_frame_time_ms": frame_ms,
        "screenshots": screenshots,
        "receipt_path": str(receipt_path) if receipt_path.exists() else "",
        "engine_log": str(engine_log),
    }


def _build_regression_grid(route: str, on_shots: list[str], off_shots: list[str],
                           out_path: Path, fps_on, fps_off) -> str | None:
    """
    Stitch a frame-paired baseline-vs-candidate grid: one row per motion frame,
    baseline (gates unset) on the left, candidate (gates set) on the right. This is
    the artifact the operator eyeballs for visual regression. Frames are paired by
    position in the screenshot list (both arms replay the same route, so index i is
    the same route moment).
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    if not on_shots or not off_shots:
        return None

    def sample(shots: list[str], k: int = 4) -> list[str]:
        # Evenly sample up to k frames so a long gif_NNN route stays a readable grid.
        if len(shots) <= k:
            return shots
        idx = [round(i * (len(shots) - 1) / (k - 1)) for i in range(k)]
        return [shots[i] for i in idx]

    on_shots, off_shots = sample(on_shots), sample(off_shots)
    n = min(len(on_shots), len(off_shots))
    W = 520

    def label(img: "Image.Image", text: str) -> "Image.Image":
        img = img.copy()
        img.thumbnail((W, W))
        band = Image.new("RGB", (img.width, 18), (0, 0, 0))
        ImageDraw.Draw(band).text((3, 3), text, fill=(0, 255, 120))
        out = Image.new("RGB", (img.width, img.height + 18), (0, 0, 0))
        out.paste(band, (0, 0))
        out.paste(img, (0, 18))
        return out

    rows = []
    for i in range(n):
        a = label(Image.open(on_shots[i]).convert("RGB"),
                  f"{route} f{i}  baseline (gates unset)  fps={fps_on}")
        b = label(Image.open(off_shots[i]).convert("RGB"),
                  f"{route} f{i}  candidate (gates set)  fps={fps_off}")
        row = Image.new("RGB", (a.width + b.width + 8, max(a.height, b.height)), (20, 20, 20))
        row.paste(a, (0, 0))
        row.paste(b, (a.width + 8, 0))
        rows.append(row)
    width = max(r.width for r in rows)
    height = sum(r.height for r in rows) + 8 * (len(rows) - 1)
    canvas = Image.new("RGB", (width, height), (20, 20, 20))
    y = 0
    for r in rows:
        canvas.paste(r, (0, y))
        y += r.height + 8
    canvas.save(out_path)
    return str(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="FL-4431 perf visual regression runner: walk/strafe + airplane, fullscreen. "
                    "No --candidate-env: single-arm reprofile of the committed renderer. "
                    "With --candidate-env: baseline (gates unset) vs candidate (gates set) "
                    "per-route regression grid + FPS delta.")
    ap.add_argument("--routes", default="walk_strafe,airplane",
                    help="comma list from: %s" % ",".join(ROUTES))
    ap.add_argument("--candidate-env", action="append", default=[],
                    metavar="NAME[=VALUE]",
                    help="runtime gate the CANDIDATE arm sets (repeatable; bare NAME means "
                         "NAME=1). Example live gates: FL4431_DISABLE_POSRECON, "
                         "FL4431_DISABLE_EYE_GRID_DEDUP, FL4431_DISABLE_QUAD4. Omit for a "
                         "single-arm measurement run.")
    ap.add_argument("--out", default=str(OUT_ROOT_DEFAULT))
    ap.add_argument("--port", type=int, default=2600, help="base Colyseus port; incremented per run")
    ap.add_argument("--resolution", default="1920x1200",
                    help="capture resolution WxH (fullscreen still measured at real window size)")
    ap.add_argument("--move-frames", type=int, default=120)
    ap.add_argument("--godot-timeout", type=float, default=420.0)
    ap.add_argument("--day-cycle-time", type=float, default=0.5,
                    help="deterministic time-of-day pin in [0,1) (0.5 = noon) applied to "
                         "EVERY arm — the cycle is default-on, so unpinned sequential arms "
                         "would capture different lighting phases")
    ap.add_argument("--allow-inherited-env", action="store_true",
                    help="proceed despite gate-family env vars in the calling shell "
                         "(they are STILL scrubbed from every arm; this only silences "
                         "the fail-closed refusal)")
    args = ap.parse_args()

    routes = [r for r in args.routes.split(",") if r]
    for r in routes:
        if r not in ROUTES:
            print(f"[perf-visual] unknown route '{r}' (valid: {','.join(ROUTES)})", file=sys.stderr)
            return 2

    # DECISION-GRADE ENV RULE (reviewer blocker 4): refuse to measure when the
    # calling shell carries gate-family variables — a leftover FL4431_DISABLE_*
    # would silently change what "current" means. Every arm env is scrubbed
    # regardless; --allow-inherited-env only converts the refusal to a notice.
    inherited = _inherited_gates()
    if inherited:
        msg = " ".join(f"{k}={v}" for k, v in sorted(inherited.items()))
        if args.allow_inherited_env:
            print(f"[perf-visual] NOTICE: inherited gates scrubbed from all arms: {msg}")
        else:
            print(f"[perf-visual] REFUSING: gate-family env inherited from shell: {msg}\n"
                  f"[perf-visual] unset them or pass --allow-inherited-env (arms are "
                  f"scrubbed either way).", file=sys.stderr)
            return 2

    # Build the arm list from the command line. A/B mode needs a candidate gate;
    # without one there is nothing to differ, so we run one honest "current" arm.
    candidate_env = _parse_env_specs(args.candidate_env)
    if candidate_env:
        arm_specs = [("baseline", {}), ("candidate", candidate_env)]
    else:
        arm_specs = [("current", {})]
    out_root = Path(args.out)
    summary = {
        "candidate_env": candidate_env,
        "routes": {},
    }
    port = args.port
    for route in routes:
        route_dir = out_root / route
        arm_results = {}
        for arm_name, arm_env in arm_specs:
            gates = " ".join(f"{k}={v}" for k, v in arm_env.items()) or "(all gates unset)"
            print(f"\n[perf-visual] === route={route} arm={arm_name} {gates} fullscreen ===",
                  flush=True)
            res = _run_engine(route, arm_name, arm_env, route_dir / arm_name,
                              port, args.resolution, args.move_frames,
                              args.godot_timeout, args.day_cycle_time)
            arm_results[arm_name] = res
            status = "PASS" if res["ok"] else f"FAIL(rc={res['rc']})"
            print(f"[perf-visual] {arm_name}: {status} fps={res['measured_fps']} "
                  f"frames={len(res['screenshots'])} log={res['engine_log']}", flush=True)
            port += 1  # fresh port per run so a lingering server never blocks the next

        # Build the baseline-vs-candidate regression grid + FPS delta when BOTH
        # arms produced frames (single-arm runs have nothing to pair).
        base, cand = arm_results.get("baseline"), arm_results.get("candidate")
        grid = None
        fps_delta = None
        if base and cand and base["ok"] and cand["ok"]:
            grid = _build_regression_grid(
                route, base["screenshots"], cand["screenshots"],
                route_dir / "regression_grid.png", base["measured_fps"], cand["measured_fps"])
            try:
                fps_delta = float(cand["measured_fps"]) - float(base["measured_fps"])
            except (TypeError, ValueError):
                fps_delta = None
        summary["routes"][route] = {
            "arms": {k: {"ok": v["ok"], "measured_fps": v["measured_fps"],
                         "fps_frame_time_ms": v["fps_frame_time_ms"],
                         "frames": len(v["screenshots"])}
                     for k, v in arm_results.items()},
            "regression_grid": grid,
            "fps_delta_candidate_minus_baseline": fps_delta,
        }
        if grid:
            print(f"[perf-visual] {route}: regression grid -> {grid}  "
                  f"fps_delta(candidate-baseline)={fps_delta}", flush=True)

    out_root.mkdir(parents=True, exist_ok=True)
    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n[perf-visual] summary -> {summary_path}", flush=True)
    print("[perf-visual] ACCEPTANCE RULE: keep the optimization only on measurable FPS gain "
          "AND operator-confirmed no visual regression in the grid. Else revert.", flush=True)
    # This runner never decides VISUAL acceptance (the operator's eye does), but
    # per the FL-4431 decision-grade rule it refuses to exit 0 on measurement
    # validity gaps: any arm without a receipt / frames / required FPS numbers
    # makes the whole run nonzero so a broken capture can never be mistaken for
    # a measurement.
    all_arms_ok = all(
        arm["ok"]
        for route_summary in summary["routes"].values()
        for arm in route_summary["arms"].values()
    ) and bool(summary["routes"])
    return 0 if all_arms_ok else 1


if __name__ == "__main__":
    sys.exit(main())

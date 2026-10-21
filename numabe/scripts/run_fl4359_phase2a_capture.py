#!/usr/bin/env python3
"""FL-4359 Phase-2a camera-pose-unification capture (governing-order step 8).

Boots a fresh NO-BOT Colyseus server with the player spawned AIRBORNE in the airplane, runs the
external movement driver with --fl4359-phase2a-receipts,
then runs analyze_fl4359_phase2a_pose.py on the captured godot stdout (which carries the
FL4359_PHASE2A_*_RECEIPT lines). This is the headed proof for binding requirement 7:

  prepared_frame == camera_pose_frame == mesh_pose_frame  (evidence_pose_frame_coherent)
  camera_anchor_minus_airplane_position <= epsilon         (gameplay_camera_anchor_bounded)
  + camera-only yaw jitter bounded                         (gameplay_camera_yaw_jitter_bounded)

It is NOT a closure surface (Law 16): a pass is one observed run on the perspective flight path.
This orchestrator only DRIVES the run; gameplay proof lives in the analyzer gates (Law 13).

Requires a headed GPU + node/tsx (Colyseus). It starts a no-bot server-owned
airborne spawn directly; the retired exact-key jitter front door is not part of
this route.

  python3 scripts/run_fl4359_phase2a_capture.py --out .run/fl4359_phase2a --port 2582
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_DIR = ROOT / "colyseus_server"
GODOT_PROJECT = ROOT / "godot_project"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
ANALYZER = ROOT / "scripts" / "analyze_fl4359_phase2a_pose.py"
AIRPLANE_HARNESS = str(ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd")
WALK_HARNESS = "res://tools/proofs/run_fl4359_walk_capture.gd"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_capture(out_dir: Path, port: int, hold: int, godot_timeout: float,
                boot_timeout: float, windowed: bool = False, mode: str = "airplane",
                straight_only: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    server_log = out_dir / "colyseus_server.log"
    godot_log = out_dir / "godot_client.log"  # <-- carries the Phase-2a receipts
    if _port_open(port):
        return {"ok": False, "error": f"port {port} already in use"}

    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(port)
    env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)  # NO bot (single-owner clean capture)
    env["ASCIICKER_DISABLE_HUNTER_BOT"] = "1"  # NO hunter either, same reason
    if mode == "airplane":
        env["PLAYER_SPAWN_X"], env["PLAYER_SPAWN_Z"] = "86", "93"
        env["AIRPLANE_SPAWN_AIRBORNE"] = "1"
        env["AIRPLANE_SPAWN_ALTITUDE"] = "45"
    # walk mode: default ground spawn, on-foot — the actual perspective camera subject (finding 6).

    slog = open(server_log, "w")
    server = subprocess.Popen(["node", "--import", "tsx", "src/index.ts"],
                              cwd=str(COLYSEUS_DIR), env=env, stdout=slog,
                              stderr=subprocess.STDOUT)

    def _listening() -> bool:
        if server.poll() is not None:
            return False
        try:
            return "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text()
        except OSError:
            return False

    rc = None
    try:
        deadline = time.time() + boot_timeout
        while time.time() < deadline and not _listening():
            time.sleep(0.25)
        if not _listening():
            return {"ok": False, "error": "server did not report LISTENING"}
        cmd = [GODOT_BIN, "--path", str(GODOT_PROJECT)]
        # Pose coherence is resolution-independent, so a windowed run is a non-disruptive way to
        # verify requirement 7 without taking over the display. The operator shimmer capture uses
        # --fullscreen (worst-case readability); for pose/lerp receipts it does not matter.
        if not windowed:
            cmd.append("--fullscreen")
        harness = WALK_HARNESS if mode == "walk" else AIRPLANE_HARNESS
        cmd += [
            "--script", harness, "--",
            "--colyseus-client", f"--port={port}",
            f"--fl4359-capture-output-dir={out_dir}",
            "--fl4359-phase2a-receipts",  # World/Player/Main emit per-frame receipts to stdout
        ]
        if mode == "airplane":
            cmd += [f"--fl4359-jitter-hold-frames={hold}", "--fl4359-headed-camera-mode=plane_air"]
        else:
            cmd += [f"--fl4359-walk-phase-frames={max(hold // 3, 10)}"]
            if straight_only:
                cmd.append("--fl4359-walk-straight-only")  # reviewer step 4: clean continuity window
        with open(godot_log, "w") as glog:
            try:
                rc = subprocess.run(cmd, cwd=str(ROOT), stdout=glog,
                                    stderr=subprocess.STDOUT, timeout=godot_timeout).returncode
            except subprocess.TimeoutExpired:
                rc = 124
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        slog.close()

    # FL-4359 reviewer finding 1 (fail-closed): Godot must exit 0 AND the harness receipt (parsed
    # from STDOUT, the robust source -- Godot's relative-path file write is unreliable) must report
    # pass (walk mode requires the perspective camera subject + on-foot).
    receipt = {}
    if godot_log.exists():
        for line in godot_log.read_text(errors="replace").splitlines():
            for marker in ("FL4359_WALK_CAPTURE_RECEIPT ", "FL4359_AIRPLANE_CAPTURE_RECEIPT "):
                if line.strip().startswith(marker):
                    try:
                        receipt = json.loads(line.strip()[len(marker):])
                    except json.JSONDecodeError:
                        pass
    ok = (rc == 0) and godot_log.exists() and bool(receipt.get("pass"))
    err = None
    if rc != 0:
        err = f"godot exit rc={rc}"
    elif not bool(receipt.get("pass")):
        err = f"harness receipt not pass: {receipt or 'missing'}"
    return {"ok": ok, "error": err, "godot_rc": rc, "godot_log": str(godot_log), "receipt": receipt}


def analyze(godot_log: str, summary_json: str, anchor_epsilon: float, fail_on_gate: bool) -> tuple[int, dict]:
    cmd = [sys.executable, str(ANALYZER), godot_log, "--summary-json", summary_json,
           "--anchor-epsilon", str(anchor_epsilon)]
    if fail_on_gate:
        cmd.append("--fail-on-gate")  # walk mode has no airplane pose receipts -> don't gate on them
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError:
        result = {"error": "analyzer produced no JSON", "stderr": p.stderr[-400:]}
    return p.returncode, result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".run" / "fl4359_phase2a"))
    ap.add_argument("--port", type=int, default=2582)
    ap.add_argument("--hold-frames", type=int, default=90)
    ap.add_argument("--godot-timeout", type=float, default=360.0)
    ap.add_argument("--server-boot-timeout", type=float, default=30.0)
    ap.add_argument("--anchor-epsilon", type=float, default=1e-3)
    ap.add_argument("--windowed", action="store_true",
                    help="Run windowed instead of --fullscreen (non-disruptive pose check)")
    ap.add_argument("--mode", choices=["airplane", "walk"], default="airplane",
                    help="airplane = pose-coherence (steps 7-8); walk = on-foot second-lerp "
                         "continuity (step 10, the actual perspective camera subject)")
    ap.add_argument("--straight-only", action="store_true",
                    help="walk mode: a single steady straight window (reviewer step 4 continuity gate)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    print(f"[fl4359-phase2a] mode={args.mode} straight_only={args.straight_only} -> {out_dir}", flush=True)
    cap = run_capture(out_dir, args.port, args.hold_frames, args.godot_timeout,
                      args.server_boot_timeout, windowed=args.windowed, mode=args.mode,
                      straight_only=args.straight_only)
    if not cap.get("ok"):
        print(f"[fl4359-phase2a] CAPTURE FAILED — {cap.get('error', 'no godot log')}", flush=True)
        return 2

    summary_json = str(out_dir / "phase2a_pose_summary.json")
    rc, result = analyze(cap["godot_log"], summary_json, args.anchor_epsilon,
                         fail_on_gate=(args.mode == "airplane"))
    print(json.dumps(result, indent=2, default=str), flush=True)
    if args.mode == "airplane":
        gates = result.get("gates", {})
        print("\n========= FL-4359 PHASE-2A POSE SUMMARY (steps 7-8) =========", flush=True)
        for name, g in gates.items():
            if isinstance(g, dict):
                print(f"  {name}: {'PASS' if g.get('pass') else 'FAIL'}", flush=True)
        print(f"  all_pass: {gates.get('all_pass')}", flush=True)
    else:
        lc = result.get("lerp_continuity", {})
        gate = lc.get("straight_only_gate", {})
        print("\n===== FL-4359 STEP-10 SECOND-LERP CONTINUITY (on-foot) =====", flush=True)
        print(f"  frames={lc.get('frames')} epochs={lc.get('epochs')} "
              f"monotonic_clamp_frames={lc.get('monotonic_clamp_frames')}", flush=True)
        print(f"  STRAIGHT-only gate: frames={gate.get('frames')} "
              f"raw_max_pred_err={gate.get('raw_max_prediction_error')} "
              f"smoothed={gate.get('smoothed_max_prediction_error')} "
              f"lag={gate.get('introduced_lag')}", flush=True)
        print(f"  top discontinuities (segment/alpha/clamp/snapshot correlation):", flush=True)
        for d in lc.get("top_discontinuities", [])[:5]:
            print(f"    {d}", flush=True)
        ov = lc.get("overshoot", {})
        print(f"  overshoot (finding 2): pure_lag={ov.get('is_pure_lag')} "
              f"frames={ov.get('overshoot_frames')} max_pos={ov.get('max_positive_overshoot')} "
              f"max_signed={ov.get('max_signed_along_motion')}", flush=True)
        sc = lc.get("segment_continuity", {})
        rel = sc.get("endpoint_velocity_reliability", {})
        print(f"  segment continuity (finding 4): valid_segments={sc.get('valid_segments')} "
              f"boundaries={sc.get('segment_boundaries')} "
              f"linear_boundary_mismatch={sc.get('max_linear_boundary_mismatch')} "
              f"endpoint_vel_trustworthy={rel.get('trustworthy')} "
              f"(inconsistency={rel.get('max_shared_boundary_inconsistency')}, "
              f"central_rel={rel.get('mean_central_difference_rel_residual')})", flush=True)
        rep = lc.get("interpolation_replay", {})
        print(f"  offline replay (items 5/6/8): linear_faithful_residual="
              f"{rep.get('linear_reproduces_emitted_target_max_residual')} "
              f"valid_segments={rep.get('valid_segments')} boundaries={rep.get('boundaries_analyzed')} "
              f"candidate_coverage={rep.get('candidate_coverage')} "
              f"corridor_lateral_tol_world={rep.get('corridor_lateral_tolerance_world')}", flush=True)
        for sname, s in rep.get("schemes", {}).items():
            print(f"    {sname}: analytic_boundary_mismatch={s.get('analytic_max_boundary_mismatch')} "
                  f"bezier_corridor_safe={s.get('bezier_corridor_safe')} "
                  f"bow/chord={s.get('max_bow_over_chord')} overshoot={s.get('along_chord_overshoot_max')} "
                  f"lag={s.get('resim_lag_mean')} stop_lag={s.get('resim_stop_lag_mean')} "
                  f"(stop_frames={s.get('resim_stop_frames')} subst={s.get('substituted_interp_frames')})", flush=True)
        po = lc.get("perspective_ownership", {})
        print(f"  perspective ownership (finding 7/NI-6): gate_pass={po.get('gate_pass')} "
              f"held_throughout={po.get('perspective_held_throughout')} "
              f"missing_tags={po.get('missing_tags')} tags={po.get('tags')}", flush=True)
        print(f"  step11_decision: {lc.get('step11_decision')}", flush=True)
        # FL-4359 finding 1: fail closed if the straight-only continuity gate lacks evidence.
        if not lc.get("present") or gate.get("frames", 0) < 8:
            print(f"  ANALYZER FAIL-CLOSED: insufficient straight-window receipts "
                  f"({gate.get('frames', 0)} < 8)", flush=True)
            return 1
        # FL-4359 NI-6: fail closed if perspective ownership was not proven throughout the window.
        po = lc.get("perspective_ownership", {})
        if not po.get("gate_pass"):
            print(f"  ANALYZER FAIL-CLOSED: perspective ownership gate failed "
                  f"(missing_tags={po.get('missing_tags')} held={po.get('perspective_held_throughout')} "
                  f"on_foot={po.get('on_foot_throughout')})", flush=True)
            return 1
    print(f"  godot log: {cap['godot_log']}", flush=True)
    print(f"  summary:   {summary_json}", flush=True)
    print("Gate pass is one observed run, NOT closure (Law 16); FL-4359 stays OPEN.", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())

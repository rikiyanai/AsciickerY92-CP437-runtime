#!/usr/bin/env python3
"""Run the canonical Godot capture at eight approved Cresta view directions.

Declared outputs: .run/fl4831_cresta_multiview_*/sample_NNN/ through the
existing run_fl4359_headed_movement.py publisher, plus coverage.json. This is
static view coverage, NOT continuous-orbit, driving, temporal or acceptance
evidence. A failed/missing angle remains failed; never infer unseen coverage.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "godot_project/assets/world_mesh/foreground/cresta_gx71.native.json"
SPAWNS = ROOT / "godot_project/assets/world_spawns/race_circuit_y8.fl4798_map_spawns.v1.json"
ANCHOR_WORLD = (-14.228121783, 5.904296875, 46.663201235)
ANCHOR_LOCAL = (5.20882415771484, 2.10000038146973, -3.71862506866455)
SCALE = .9
SAMPLES = tuple(range(0, 96, 12))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rotate(point, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return [c*point[0]+s*point[2], point[1], -s*point[0]+c*point[2]]


def build_poses(bank, spawns, samples=SAMPLES):
    markers = {m["name"]: m for m in spawns["minimap_markers"]}
    grid = markers["race_grid_1"]["world_xz"]
    gate = markers["race_gate_0"]["world_xz"]
    yaw = math.atan2(gate[0]-grid[0], gate[1]-grid[1])-math.pi/2
    offset = rotate(ANCHOR_LOCAL, yaw)
    root = [ANCHOR_WORLD[j]-SCALE*offset[j] for j in range(3)]
    # This is a calibrated stationary diagnostic, not a new pose authority.
    # Each execution must echo matching native local-eye facts to validate it.
    if math.hypot(root[0]-grid[0], root[2]-grid[1]) > .003:
        raise ValueError("calibrated Cresta root no longer matches the authored grid")
    rows = []
    for sample in samples:
        view = bank["views"][sample]
        eye_offset = rotate(view["eye"], yaw)
        target_offset = rotate(view["target"], yaw)
        eye = [root[j]+SCALE*eye_offset[j] for j in range(3)]
        target = [root[j]+SCALE*target_offset[j] for j in range(3)]
        direction = [target[j]-eye[j] for j in range(3)]
        rows.append({"sample": sample, "eye": eye, "local_eye": view["eye"], "target": target,
                     "yaw": math.atan2(-direction[0], -direction[2]),
                     "pitch": math.atan2(direction[1], math.hypot(direction[0], direction[2]))})
    return rows


def command(out, port, pose):
    return [sys.executable, "scripts/run_fl4359_headed_movement.py", "--out", str(out),
            "--port", str(port), "--camera-mode", "perspective", "--godot-window-mode", "fullscreen",
            "--single-frame", "--single-frame-label", "cresta_native", "--settle-frames", "30",
            "--run-classification", "diagnostic-only", "--skip-jitter-analysis",
            "--presentation-mode", "FF_SHARED", "--resolution", "3600x2338",
            "--day-cycle-time", "0.5", "--force-baseline-features", "day_night_cycle,tilt_shift_off",
            "--product-time", "0", "--player-spawn-x=-16.182", "--player-spawn-z=61.636",
            "--exact-camera-position="+",".join(f"{v:.12f}" for v in pose["eye"]),
            f'--exact-camera-yaw={pose["yaw"]:.12f}', f'--exact-camera-pitch={pose["pitch"]:.12f}',
            "--exact-camera-projection", "perspective", "--exact-camera-fov", "44.184740277",
            "--extra-user-arg=--map-id=race_circuit_y8", "--extra-user-arg=--car-race",
            "--extra-user-arg=--fl4831-cresta-native-review", "--extra-user-arg=--fl4831-cresta-native-audit",
            "--extra-user-arg=--fl4831-cresta-native-audit-after-frame=30", "--godot-timeout", "900",
            "--keep-forever", "--proof-id", "proof.fl4359_headed_movement"]


def inspect_capture(out, pose, returncode):
    result = {"sample": pose["sample"], "requested_pose": pose, "runner_rc": returncode,
              "observed": False, "accepted": False, "path": str(out)}
    receipt_path, source_path = out/"receipt.json", out/"run_source_provenance.json"
    if not receipt_path.is_file() or not source_path.is_file() or not (out/"godot_client.log").is_file():
        return {**result, "reason": "missing_receipt_or_source_provenance"}
    receipt, source = json.loads(receipt_path.read_text()), json.loads(source_path.read_text())
    audits = []
    for line in (out/"godot_client.log").read_text().splitlines():
        if line.startswith("CRESTA_NATIVE_GPU_REVIEW "):
            audits.append(json.loads(line.split(" ", 1)[1]))
    result.update({"runner_reason": receipt.get("reason"), "source_stable": source.get("source_state_stable"),
                   "render_pose": receipt.get("exact_render_pose"), "native_audits": audits,
                   "image": str(out/"cresta_native.png")})
    if len(audits) != 1:
        return {**result, "reason": "missing_or_ambiguous_native_view_audit"}
    actual = audits[0]
    camera_ok = len(actual.get("local_cameras", [])) == 1 and all(
        abs(a-b) <= .003 for a, b in zip(actual["local_cameras"][0], pose["local_eye"]))
    result["observed"] = bool(source.get("source_state_stable") is True and camera_ok
        and actual.get("samples") == [pose["sample"]] and (out/"cresta_native.png").is_file()
        and receipt.get("exact_render_pose", {}).get("pass") is True)
    result["reason"] = "static_view_executed_visual_review_pending" if result["observed"] else "native_view_identity_not_observed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT/".run/fl4831_cresta_multiview_20261021_01")
    parser.add_argument("--port", type=int, default=2600)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    bank = json.loads(BANK.read_text())
    poses = build_poses(bank, json.loads(SPAWNS.read_text()))
    if not args.execute:
        print(json.dumps({"scope": "eight_static_views_NOT_motion_proof", "poses": poses}, indent=2))
        return 0
    if args.out.exists():
        parser.error("output exists; preserve previous evidence and select a new directory")
    args.out.mkdir(parents=True)
    coverage = {"schema": "fl4831.cresta.static_view_coverage.v1", "accepted": False,
                "scope": "eight_static_views_NOT_continuous_orbit_or_driving",
                "bank_sha256": digest(BANK), "spawns_sha256": digest(SPAWNS),
                "required_samples": list(SAMPLES), "views": [],
                "unrun": ["continuous_orbit", "forward", "reverse", "steering", "stop", "pan_zoom", "occlusion_reveal"]}
    for index, pose in enumerate(poses):
        out = args.out/f'sample_{pose["sample"]:03}'
        print("CRESTA_VIEW_BEGIN", pose["sample"], flush=True)
        run = subprocess.run(command(out, args.port+index, pose), cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        row = inspect_capture(out, pose, run.returncode)
        coverage["views"].append(row)
        (args.out/"coverage.json").write_text(json.dumps(coverage, indent=2)+"\n")
        print("CRESTA_VIEW_RESULT", pose["sample"], row["reason"], "runner_rc", run.returncode, flush=True)
        if not row["observed"]:
            print(run.stdout[-1200:], flush=True)
    print("CRESTA_MULTIVIEW", "executed", sum(r["observed"] for r in coverage["views"]),
          "required", len(SAMPLES), "visual_acceptance=OPEN motion=UNRUN", flush=True)
    return 0 if all(r["observed"] for r in coverage["views"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

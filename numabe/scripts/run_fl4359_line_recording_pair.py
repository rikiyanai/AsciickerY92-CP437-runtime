#!/usr/bin/env python3
"""Line-style recording pair runner (FF-DPR-56A, FL-4359).

Launches the ortho arm then the perspective arm (never concurrently) by
reusing scripts/run_fl4359_headed_movement.py with the FF-DPR-54 arm
shape. Both arms fly the same manifest-step flyby (FF-ACQ-05 steps, total
sweep ~3.6 degrees): the perspective arm on the plane_air vehicle, the
ortho arm on plane_air_ortho (both overridable: the ortho vehicle choice
is an assumption, stated here, not a measured equivalence).

Presentation mode FF is the production True-FF cutover. No bridge flag or
compatibility switch is forwarded; both arms must reach the direct renderer
through the normal presentation-mode contract.

Out dirs: .run/ff-dpr-20260920-56-ortho and -persp (overridable).
Gates per arm: pgrep empty (poll 20 s up to 10 min, never kill), 1-min
load below 11, memory free above 25 percent, 90 s zero-CPU abort, fence
hard stop (own Godot killed on the first timeout-waiting-for-fence line).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "run_fl4359_headed_movement.py"

FENCE_TOKEN = "timeout waiting for fence"


def godot_pids() -> list[int]:
    """Pids of live Godot processes, excluding analyst shell wrappers."""
    try:
        proc = subprocess.run(["pgrep", "-fl", "godot"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        cmdline = parts[1]
        if "godot_client.log" in cmdline or "tmux_lanes" in cmdline:
            continue
        if "bin/godot" in cmdline or "Godot" in cmdline:
            pids.append(int(parts[0]))
    return pids


def load_average() -> float:
    """1-minute load average (os.getloadavg is portable; no /proc)."""
    import os
    return float(os.getloadavg()[0])


def memory_free_percent() -> float:
    """System-wide memory free percentage via memory_pressure."""
    proc = subprocess.run(["memory_pressure"], capture_output=True,
                          text=True, timeout=30)
    for line in proc.stdout.splitlines():
        if "free percentage" in line:
            return float(line.split(":")[-1].strip().rstrip("%"))
    raise RuntimeError("memory_pressure free percentage not found")


def process_cputime(pid: int) -> str:
    """Cumulative CPU time string for pid, or '' when not visible."""
    proc = subprocess.run(["ps", "aux"], capture_output=True, text=True,
                          timeout=10)
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) > 10 and fields[1] == str(pid):
            return fields[9]
    return ""


def wait_for_gate(samples: list) -> bool:
    """Poll pgrep-empty plus load below 11. Records samples."""
    deadline = time.time() + 600
    while time.time() < deadline:
        pids = godot_pids()
        load = load_average()
        samples.append({"t": time.time(), "godot_pids": pids, "load": load})
        if not pids and load < 11.0:
            return True
        time.sleep(20)
    return False


def run_arm(cmd: list[str], out_dir: Path, log: dict) -> dict:
    """Launch one arm under the fence/CPU monitor. Returns the arm result."""
    godot_log = out_dir / "godot_client.log"
    arm_log_path = out_dir / "line_pair_arm.log"
    with arm_log_path.open("w", encoding="utf-8") as arm_log:
        proc = subprocess.Popen([sys.executable, str(RUNNER)] + cmd,
                                stdout=arm_log, stderr=subprocess.STDOUT,
                                cwd=REPO_ROOT)
        zero_streak = 0
        last_cpu = "@"
        outcome = "unknown"
        while True:
            time.sleep(15)
            if proc.poll() is not None:
                outcome = f"exit_rc={proc.returncode}"
                break
            if godot_log.is_file():
                try:
                    text = godot_log.read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if FENCE_TOKEN in text:
                    proc.kill()
                    outcome = "fence_tripwire_kill"
                    break
            pids = godot_pids()
            cpu = process_cputime(pids[0]) if len(pids) == 1 else ""
            if cpu == last_cpu:
                zero_streak += 1
            else:
                zero_streak = 0
                last_cpu = cpu
            if zero_streak >= 6:
                proc.kill()
                try:
                    for pid in godot_pids():
                        subprocess.run(["kill", "-9", str(pid)],
                                       capture_output=True, timeout=10)
                except (OSError, subprocess.SubprocessError):
                    pass
                outcome = "stall_abort_90s_zero_cpu"
                break
    result = {"cmd": cmd, "outcome": outcome,
              "arm_log": str(arm_log_path)}
    log["arms"].append(result)
    return result


def build_arm_args(args: argparse.Namespace, *, projection: str,
                   camera_mode: str, out_dir: Path, port: int,
                   title: str) -> list[str]:
    """One arm's argv for the shared movement runner (FF-DPR-54 shape)."""
    argv = [
        "--camera-mode", camera_mode,
        "--exact-camera-projection", projection,
        "--godot-window-mode", "windowed",
        "--allow-windowed-diagnostic",
        "--presentation-mode", "FF",
        "--cell-aspect", "terminal",
        "--glyph-matrix-cell-px", "32",
        "--resolution", args.resolution,
        "--run-classification", "diagnostic-only",
        "--skip-jitter-analysis",
        "--jitter-capture-frame-limit", "0",
        "--record-flight-video",
        "--move-frames", str(args.move_frames),
        "--turn-frames", str(args.turn_frames),
        "--after-frames", str(args.after_frames),
        "--port", str(port),
        "--out", str(out_dir),
        "--recording-title", title,
    ]
    if args.godot_timeout and args.godot_timeout > 0:
        argv += ["--godot-timeout", str(args.godot_timeout)]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Line-style ortho+persp recording pair.")
    parser.add_argument("--ortho-out", type=Path,
                        default=REPO_ROOT / ".run" / "ff-dpr-20260920-56-ortho")
    parser.add_argument("--persp-out", type=Path,
                        default=REPO_ROOT / ".run" / "ff-dpr-20260920-56-persp")
    parser.add_argument("--port-ortho", type=int, default=2598)
    parser.add_argument("--port-persp", type=int, default=2599)
    parser.add_argument("--camera-mode-ortho", default="plane_air_ortho",
                        help="Ortho vehicle assumption (override if the "
                        "bridge pins another).")
    parser.add_argument("--camera-mode-persp", default="plane_air")
    parser.add_argument("--move-frames", type=int, default=120)
    parser.add_argument("--turn-frames", type=int, default=8)
    parser.add_argument("--after-frames", type=int, default=2)
    parser.add_argument("--resolution", default="1280x800")
    parser.add_argument("--title-ortho", default="line-ortho")
    parser.add_argument("--title-persp", default="line-persp")
    parser.add_argument("--godot-timeout", type=int, default=0)
    parser.add_argument("--pair-log", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print both arm commands; launch nothing.")
    parser.add_argument("--skip-ortho", action="store_true")
    parser.add_argument("--skip-persp", action="store_true")
    args = parser.parse_args(argv)

    arms = []
    if not args.skip_ortho:
        arms.append(("ortho", args.camera_mode_ortho, args.ortho_out,
                     args.port_ortho, args.title_ortho, "ortho"))
    if not args.skip_persp:
        arms.append(("persp", args.camera_mode_persp, args.persp_out,
                     args.port_persp, args.title_persp, "perspective"))
    if not arms:
        print("nothing to run: both arms skipped", file=sys.stderr)
        return 2

    planned = [(name, build_arm_args(
        args, projection=projection, camera_mode=camera_mode,
        out_dir=out_dir, port=port, title=title))
        for name, camera_mode, out_dir, port, title, projection in arms]
    if args.dry_run:
        for name, argv in planned:
            print(f"[{name}] python3 scripts/run_fl4359_headed_movement.py "
                  + " ".join(argv))
        return 0

    log: dict = {"presentation_mode": "FF", "gates": [], "arms": []}
    for (name, _camera_mode, out_dir, _port, _title, _projection), \
            (_pname, argv) in zip(arms, planned):
        out_dir.mkdir(parents=True, exist_ok=True)
        samples: list = []
        gate = {"arm": name, "pgrep_empty": not godot_pids()}
        try:
            mem = memory_free_percent()
        except (OSError, RuntimeError, ValueError) as exc:
            gate["memory_ok"] = False
            gate["memory_detail"] = f"{type(exc).__name__}: {exc}"
            log["gates"].append(gate)
            print(f"[{name}] BLOCKED: memory gate unreadable", flush=True)
            break
        gate["memory_free_percent"] = mem
        gate["memory_ok"] = mem > 25.0
        gate["gate_open"] = (gate["pgrep_empty"] and gate["memory_ok"]
                             and wait_for_gate(samples))
        gate["gate_samples"] = samples[-3:]
        log["gates"].append(gate)
        if not gate["gate_open"]:
            print(f"[{name}] BLOCKED: gate never opened", flush=True)
            break
        print(f"[{name}] gate open; launching", flush=True)
        run_arm(argv, out_dir, log)

    pair_log = args.pair_log or (REPO_ROOT / ".run" /
                                 "ff-dpr-20260920-56-pair.json")
    pair_log.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(json.dumps({"pair_log": str(pair_log),
                      "arms": [arm["outcome"] for arm in log["arms"]]},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

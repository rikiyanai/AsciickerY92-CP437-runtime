#!/usr/bin/env python3
"""Record the complete authored three-lap bot race in at most 30 wall seconds."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "colyseus_server"
PROJECT = ROOT / "godot_project"
GODOT = Path("/Applications/Godot.app/Contents/MacOS/Godot")
DRIVER = "res://scripts/proofs/fl4880_30s_full_race_recording.gd"
FINALIZER = ROOT / "scripts/recording/f9_finalize_bundle.py"
sys.path.insert(0, str(ROOT / "scripts/recording"))
from f9_source_identity import launch_source_identity  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_launch_guard as launch_guard  # noqa: E402


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_server(port: int, process: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Colyseus exited before listening (rc={process.returncode})")
        if _port_open(port):
            return
        time.sleep(0.05)
    raise TimeoutError(f"Colyseus did not listen on port {port}")


def _load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _wait_bundle(recording_path: Path, timeout: float = 240.0) -> dict:
    completion_path = recording_path.parent / "completion.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if completion_path.is_file():
            completion = _load_object(completion_path)
            if completion.get("status") in {"complete", "incomplete"}:
                return completion
        time.sleep(0.25)
    raise TimeoutError(f"recording finalizer did not publish {completion_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2591)
    parser.add_argument("--tick-hz", type=int, default=240, choices=range(30, 241))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--godot-timeout", type=float, default=90.0,
                        help="FL-4880: seconds before the Godot client is killed; raise it when a "
                             "changed RD shader must compile uncached.")
    parser.add_argument("--resolution", default="800x500",
                        help="FL-4880: Godot window size in points (default 800x500, the recording size).")
    parser.add_argument("--render-distance", type=float, default=-1.0,
                        help="FL-4880 measurement: target render distance via the player steps.")
    parser.add_argument("--no-capture", action="store_true",
                        help="FL-4880 measurement mode: drive the race without F9 capture; never passes.")
    args = parser.parse_args()

    if not GODOT.is_file():
        raise FileNotFoundError(GODOT)
    if _port_open(args.port):
        raise RuntimeError(f"port {args.port} is already in use")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = (args.out or ROOT / ".run/fl4880_30s_full_race" / stamp).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    driver_receipt = out_dir / "driver_receipt.json"
    server_log = out_dir / "colyseus_server.log"
    godot_log = out_dir / "godot_client.log"
    admission_path = out_dir / "guard_admission.json"

    admission_rc = launch_guard.admit(
        "scripts/run_fl4880_30s_full_race.py",
        out_dir,
        project_dir=PROJECT,
        server_dir=SERVER,
        presentation_mode="FC",
    )
    if admission_rc != 0:
        return admission_rc

    env = dict(os.environ)
    env.update(
        {
            "COLYSEUS_PORT": str(args.port),
            "ASCIICKER_RACE_RECORDING_TICK_HZ": str(args.tick_hz),
            "ASCIICKER_SCREEN_RECORDING_ALLOWED": "1",
            "ASCIICKER_SCREEN_RECORDING_MAX_FRAMES": "1800",
            "ASCIICKER_SCREEN_RECORDING_MIN_FREE_BYTES": str(8 * 1024**3),
            "ASCIICKER_SCREEN_RECORDING_TITLE": "full authored race under 30 seconds",
            "ASCIICKER_PROOF_ID": "fl4880_full_race_30s",
            "ASCIICKER_F9_FINALIZER": str(FINALIZER),
            "ASCIICKER_LAUNCH_SOURCE_IDENTITY": json.dumps(
                launch_source_identity("scripts/run_fl4880_30s_full_race.py"),
                sort_keys=True,
            ),
        }
    )
    server_command = ["node", "--import", "tsx", "src/index.ts"]
    godot_command = [
        str(GODOT),
        "--path",
        str(PROJECT),
        "--windowed",
        "--resolution",
        args.resolution,
        "--script",
        DRIVER,
        "--",
        "--colyseus-client",
        "--car-race",
        "--map-id=race_circuit_y8",
        "--host=127.0.0.1",
        f"--port={args.port}",
        "--presentation-mode=FC",
        f"--fl4880-30s-out={driver_receipt}",
        *(["--fl4880-no-capture"] if args.no_capture else []),
        *([f"--fl4880-render-distance={args.render_distance}"] if args.render_distance > 0 else []),
        f"--true-ff-admission={admission_path}",
    ]
    server_process: subprocess.Popen | None = None
    godot_rc = -1
    started = time.monotonic()
    try:
        with server_log.open("wb") as server_stream:
            server_process = subprocess.Popen(
                server_command,
                cwd=SERVER,
                env=env,
                stdout=server_stream,
                stderr=subprocess.STDOUT,
            )
            _wait_server(args.port, server_process)
            before_spawn_rc = launch_guard.before_spawn(out_dir, PROJECT, SERVER)
            if before_spawn_rc != 0:
                return before_spawn_rc
            with godot_log.open("wb") as godot_stream:
                completed = subprocess.run(
                    godot_command,
                    cwd=ROOT,
                    env=env,
                    stdout=godot_stream,
                    stderr=subprocess.STDOUT,
                    timeout=args.godot_timeout,
                    check=False,
                )
                godot_rc = completed.returncode
    finally:
        if server_process is not None and server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=5)
        launch_guard.record_completion(out_dir, godot_rc if godot_rc >= 0 else None)

    if not driver_receipt.is_file():
        raise RuntimeError(f"Godot rc={godot_rc}; driver receipt missing; inspect {godot_log}")
    driver = _load_object(driver_receipt)
    recording_raw = str(driver.get("facts", {}).get("recording_path", ""))
    if not recording_raw:
        raise RuntimeError(f"driver did not name a recording: {driver}")
    recording_path = Path(recording_raw).resolve()
    completion = _wait_bundle(recording_path)
    mov_info = completion.get("outputs", {}).get("mov", {})
    mov_path = recording_path.parent / str(mov_info.get("path", recording_path.name))
    summary = {
        "schema": "fl4880.full_race_recording_30s.run.v1",
        "passed": bool(driver.get("passed"))
        and godot_rc == 0
        and completion.get("status") == "complete"
        and mov_info.get("status") == "ok"
        and mov_path.is_file()
        and float(mov_info.get("duration_s", 31.0)) <= 30.0,
        "wall_seconds_total": round(time.monotonic() - started, 3),
        "race_record_wall_ms": driver.get("facts", {}).get("record_wall_ms"),
        "video_duration_s": mov_info.get("duration_s"),
        "tick_hz": args.tick_hz,
        "godot_rc": godot_rc,
        "driver_receipt": str(driver_receipt),
        "recording": str(mov_path),
        "bundle_completion": str(recording_path.parent / "completion.json"),
        "completion_status": completion.get("status"),
        "completion_problems": completion.get("problems", []),
        "server_log": str(server_log),
        "godot_log": str(godot_log),
    }
    summary_path = out_dir / "run_receipt.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

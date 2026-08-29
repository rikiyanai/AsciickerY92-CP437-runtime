# Ad hoc script: FL-4208 N0a native observe-render byte identity driver
# Created: 2026-06-29
# Canonical gap: native observe-render lacks a reusable FL-4208 N0a byte-identity proof driver.

#!/usr/bin/env python3
"""FL-4208 N0a native observe-render byte-identity driver.

Runs .run/game twice through the map-backed observe-render path:
  off: FL4208 trace disabled
  on:  FL4208 trace enabled with a receipt path

The N0a acceptance surface is source-resolved-cells.jsonl byte identity between
those two runs plus a structurally valid trace receipt for the ON run. This is a
local native prerequisite artifact, not closure-grade Law 15 evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GAME = ROOT / ".run" / "game"
SERVER = ROOT / ".run" / "server"
DEFAULT_OUT = ROOT / "docs" / "research" / "ascii" / "verification" / "fl4208" / "2026-06-29-n0a-native-byte-identity"
DEFAULT_MAP = ROOT / "assets" / "a3d" / "game_map_y8_original_game_map.a3d"
SCHEMA = "fl4208_n0a_native_observe_byte_identity_v1"
CAPTURE_SPEC = "fl4208-n0a-native-observe-map-y8-original-pos64-64-57-yaw45-v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_tuple(path: Path) -> None:
    obj = {
        "camera": {
            "pos": [64.0, 64.0, 57.0],
            "yaw": 45.0,
            "zoom": 1.0,
            "perspective": True,
            "scene_shift": 0,
        },
        "light": {"dir": [1.0, 1.0, 1.0], "ambience": 1.0},
        "water": 55,
    }
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def terminate_pids(stdout_text: str) -> list[int]:
    pids = [int(x) for x in re.findall(r"LOCAL AUTH: connected to owned local server on port \d+ \(pid=(\d+)\)", stdout_text)]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return pids


def run_capture(label: str, out_root: Path, view_tuple: Path, map_path: Path, timeout_s: int, trace: bool, bins: int) -> dict[str, Any]:
    out_dir = out_root / label
    out_dir.mkdir(parents=True, exist_ok=True)
    for child in out_dir.iterdir():
        if child.is_file():
            child.unlink()
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    trace_path = out_dir / "fl4208_trace.bin"

    env = os.environ.copy()
    env["ASCIICKER_FL4131_SKIP_EXTENDED_DEMO_PROBE"] = "1"
    if trace:
        env["FL4208_TRACE"] = "1"
        env["FL4208_TRACE_PATH"] = str(trace_path)
        env["FL4208_CAPTURE_SPEC"] = CAPTURE_SPEC
    else:
        for key in ("FL4208_TRACE", "FL4208_TRACE_PATH", "FL4208_CAPTURE_SPEC", "FL4208_ANCHOR_BINS"):
            env.pop(key, None)

    cmd = [
        str(GAME),
        "--observe-render",
        str(out_dir),
        "--view-tuple",
        str(view_tuple),
        "--schema-version",
        SCHEMA,
        "--map",
        str(map_path.relative_to(ROOT)),
    ]

    started = time.time()
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
        deadline = time.time() + timeout_s
        resolved = out_dir / "source-resolved-cells.jsonl"
        shot = out_dir / "source-shot.xp"
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if resolved.exists() and shot.exists():
                break
            time.sleep(0.25)
        if proc.poll() is None:
            if not (resolved.exists() and shot.exists()):
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            else:
                proc.wait(timeout=15)
        returncode = proc.returncode

    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    stopped = terminate_pids(stdout_text)
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    resolved = out_dir / "source-resolved-cells.jsonl"
    shot = out_dir / "source-shot.xp"
    meta = out_dir / "source-shot.json"
    return {
        "label": label,
        "command": cmd,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_sec": round(time.time() - started, 3),
        "output_dir": str(out_dir.relative_to(ROOT)),
        "stdout": str(stdout_path.relative_to(ROOT)),
        "stderr": str(stderr_path.relative_to(ROOT)),
        "observe_render_ok_seen": "OBSERVE_RENDER_OK" in stdout_text,
        "local_auth_seen": "LOCAL AUTH: connected" in stdout_text,
        "local_server_pids_terminated": stopped,
        "local_auth_failed": "LOCAL AUTH: could not start" in stdout_text,
        "trace_enabled": trace,
        "trace_path": str(trace_path.relative_to(ROOT)) if trace_path.exists() else None,
        "resolved_cells": str(resolved.relative_to(ROOT)) if resolved.exists() else None,
        "resolved_cells_sha256": sha256_file(resolved) if resolved.exists() else None,
        "source_shot_xp": str(shot.relative_to(ROOT)) if shot.exists() else None,
        "source_shot_xp_sha256": sha256_file(shot) if shot.exists() else None,
        "source_shot_json": str(meta.relative_to(ROOT)) if meta.exists() else None,
        "stderr_tail": stderr_text[-1200:],
    }


def validate_receipt(path: Path, expect: int | None) -> dict[str, Any]:
    cmd = [sys.executable, str(ROOT / "scripts" / "fl4208_trace_receipt.py"), str(path), "--json"]
    if expect is not None:
        cmd.extend(["--expect", str(expect)])
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return {"command": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--bins", type=int, default=4, help="Frozen N0b value; values other than 4 fail closed")
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP)
    args = ap.parse_args()

    out_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    map_path = args.map if args.map.is_absolute() else ROOT / args.map
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    view_tuple = out_root / "observe_render_view_tuple.json"
    write_tuple(view_tuple)

    failures: list[str] = []
    if not GAME.exists():
        failures.append(f"missing game binary: {GAME}")
    if not SERVER.exists():
        failures.append(f"missing server binary: {SERVER}")
    if not map_path.exists():
        failures.append(f"missing map: {map_path}")
    if args.bins != 4:
        failures.append(f"ANCHOR_BINS is frozen at 4; refusing requested bins={args.bins}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 2

    off = run_capture("trace_off", out_root, view_tuple, map_path, args.timeout, False, args.bins)
    on = run_capture("trace_on", out_root, view_tuple, map_path, args.timeout, True, args.bins)

    if not off.get("observe_render_ok_seen"):
        failures.append("trace_off did not emit OBSERVE_RENDER_OK")
    if not on.get("observe_render_ok_seen"):
        failures.append("trace_on did not emit OBSERVE_RENDER_OK")
    if not off.get("resolved_cells"):
        failures.append("trace_off missing source-resolved-cells.jsonl")
    if not on.get("resolved_cells"):
        failures.append("trace_on missing source-resolved-cells.jsonl")
    if off.get("resolved_cells_sha256") and on.get("resolved_cells_sha256"):
        if off["resolved_cells_sha256"] != on["resolved_cells_sha256"]:
            failures.append("resolved-cell byte identity failed")
    trace_validation = None
    if on.get("trace_path"):
        trace_validation = validate_receipt(ROOT / on["trace_path"], None)
        if trace_validation["returncode"] != 0:
            failures.append("trace receipt validation failed")
    else:
        failures.append("trace_on missing FL4208 receipt")

    summary = {
        "schema": SCHEMA,
        "capture_spec": CAPTURE_SPEC,
        "map": str(map_path.relative_to(ROOT)),
        "result": "PASS" if not failures else "FAIL",
        "failures": failures,
        "trace_off": off,
        "trace_on": on,
        "byte_identity": {
            "resolved_cells_sha256_equal": bool(off.get("resolved_cells_sha256") and off.get("resolved_cells_sha256") == on.get("resolved_cells_sha256")),
            "off_sha256": off.get("resolved_cells_sha256"),
            "on_sha256": on.get("resolved_cells_sha256"),
        },
        "trace_validation": trace_validation,
        "note": "Local native N0a prerequisite artifact only; not Law 15 closure.",
    }
    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {summary_path.relative_to(ROOT)}")
    print(f"result={summary['result']}")
    for failure in failures:
        print(f"failure: {failure}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare FL-4057 splat winner churn with hysteresis off vs on."""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run" / "godot_splat_churn_compare"
GODOT_PROJECT = ROOT / "godot_project"
GODOT = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
COLYSEUS_PORT = 2567


def run_variant(label: str, hysteresis: bool, *, headless: bool, warmup_frames: int, sample_frames: int) -> dict:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    churn_path = RUN_DIR / f"{label}.jsonl"
    perf_path = RUN_DIR / f"{label}.perf.json"
    log_path = RUN_DIR / f"{label}.log"
    cmd = [
        GODOT,
        *(["--headless"] if headless else []),
        "--path",
        str(GODOT_PROJECT),
        "--",
        "--colyseus-client",
        "--probe-splat-churn-camera-turn",
        f"--probe-splat-churn-sample-frames={sample_frames}",
        f"--probe-splat-churn-warmup-frames={warmup_frames}",
        "--no-holland-style-post",
        f"--splat-hysteresis={'1' if hysteresis else '0'}",
        f"--probe-splat-churn-jsonl={churn_path}",
        f"--splat-compare-title=FL-4057 {label} hysteresis={'ON' if hysteresis else 'OFF'}",
    ]
    timed_out = False
    returncode = 0
    with log_path.open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env={**os.environ, "ASCIICKER_HUD": "1"},
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            log.write(f"\nTIMEOUT after {exc.timeout} seconds\n")
    churn = read_churn(churn_path, warmup_frames=warmup_frames)
    perf = json.loads(perf_path.read_text(encoding="utf-8")) if perf_path.exists() else {}
    return {
        "label": label,
        "hysteresis": hysteresis,
        "returncode": returncode,
        "timed_out": timed_out,
        "ok": returncode == 0 and bool(churn.get("ok")),
        "churn": churn,
        "perf": {
            "ok": perf.get("ok", False),
            "avg_fps": perf.get("avg_fps"),
            "min_fps": perf.get("min_fps"),
            "achieved_yaw_deg_per_sec": perf.get("achieved_yaw_deg_per_sec"),
        },
        "paths": {
            "jsonl": str(churn_path.relative_to(ROOT)),
            "perf": str(perf_path.relative_to(ROOT)),
            "log": str(log_path.relative_to(ROOT)),
        },
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def read_churn(path: Path, *, warmup_frames: int) -> dict:
    if not path.exists():
        return {"ok": False, "reason": "missing_jsonl"}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("row_type") == "splat_churn_frame":
            rows.append(row)
    kept = [r for r in rows if int(r.get("frame", 0)) > warmup_frames]
    ratios = [float(r.get("changed_owner_ratio", 0.0)) for r in kept]
    cells = [int(r.get("changed_owner_cells", 0)) for r in kept]
    key_ratios = [float(r.get("changed_key_ratio", 0.0)) for r in kept]
    return {
        "ok": bool(ratios),
        "frames_total": len(rows),
        "frames_after_warmup": len(kept),
        "changed_owner_ratio_avg": statistics.fmean(ratios) if ratios else 0.0,
        "changed_owner_ratio_p50": statistics.median(ratios) if ratios else 0.0,
        "changed_owner_ratio_p95": percentile(ratios, 0.95),
        "changed_owner_ratio_max": max(ratios) if ratios else 0.0,
        "changed_owner_cells_avg": statistics.fmean(cells) if cells else 0.0,
        "changed_owner_cells_max": max(cells) if cells else 0,
        "changed_key_ratio_avg": statistics.fmean(key_ratios) if key_ratios else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--warmup-frames", type=int, default=90)
    parser.add_argument("--sample-frames", type=int, default=240)
    return parser.parse_args()


def colyseus_is_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", COLYSEUS_PORT), timeout=0.25):
            return True
    except OSError:
        return False


def start_colyseus_if_needed() -> subprocess.Popen | None:
    if colyseus_is_listening():
        return None
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUN_DIR / "colyseus.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["npm", "start"],
        cwd=ROOT / "colyseus_server",
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(80):
        if colyseus_is_listening():
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"Colyseus exited early; see {log_path.relative_to(ROOT)}")
        time.sleep(0.1)
    proc.terminate()
    raise RuntimeError(f"Colyseus did not listen on {COLYSEUS_PORT}; see {log_path.relative_to(ROOT)}")


def main() -> int:
    args = parse_args()
    colyseus_proc = start_colyseus_if_needed()
    try:
        before = run_variant(
            "before",
            False,
            headless=args.headless,
            warmup_frames=args.warmup_frames,
            sample_frames=args.sample_frames,
        )
        after = run_variant(
            "after",
            True,
            headless=args.headless,
            warmup_frames=args.warmup_frames,
            sample_frames=args.sample_frames,
        )
    finally:
        if colyseus_proc is not None and colyseus_proc.poll() is None:
            colyseus_proc.terminate()
    before_avg = float(before["churn"].get("changed_owner_ratio_avg", 0.0))
    after_avg = float(after["churn"].get("changed_owner_ratio_avg", 0.0))
    summary = {
        "ok": bool(before.get("ok") and after.get("ok")),
        "warmup_frames": args.warmup_frames,
        "sample_frames": args.sample_frames,
        "before": before,
        "after": after,
        "delta": {
            "changed_owner_ratio_avg": after_avg - before_avg,
            "changed_owner_ratio_avg_pct": ((after_avg / before_avg) - 1.0) if before_avg > 0 else None,
        },
    }
    summary_path = RUN_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"summary={summary_path.relative_to(ROOT)}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

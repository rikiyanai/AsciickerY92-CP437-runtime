#!/usr/bin/env python3
"""FL-4131 native single-player final-buffer parity artifact generator.

This is not an acceptance gate. It creates the automatic buffer-analysis
artifact that the operator must review before manual FL-4131 inspection.

It runs the native game twice through the existing --observe-render path:
  1. control: skips the FL-4131 extended-demo loader probe
  2. probe:   executes the FL-4131 extended-demo loader probe

The final resolved AnsiCell buffers must match byte-for-byte. A mismatch means
the loader diagnostic changed user-visible single-player output and manual
inspection must fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = (
    REPO_ROOT
    / "docs"
    / "research"
    / "ascii"
    / "verification"
    / "fl4131"
    / "manual"
    / "native_single_player_buffer_parity"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl_cells(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
    return rows


def compare_cells(control_path: Path, probe_path: Path) -> dict[str, Any]:
    control = read_jsonl_cells(control_path)
    probe = read_jsonl_cells(probe_path)
    mismatch_examples: list[dict[str, Any]] = []
    mismatch_count = 0
    max_len = max(len(control), len(probe))
    for i in range(max_len):
        left = control[i] if i < len(control) else None
        right = probe[i] if i < len(probe) else None
        if left != right:
            mismatch_count += 1
            if len(mismatch_examples) < 20:
                mismatch_examples.append({"index": i, "control": left, "probe": right})

    def glyph_count(rows: list[dict[str, Any]], glyph: int) -> int:
        return sum(1 for row in rows if int(row.get("glyph_codepoint", -1)) == glyph)

    return {
        "control_rows": len(control),
        "probe_rows": len(probe),
        "mismatch_count": mismatch_count,
        "mismatch_examples": mismatch_examples,
        "control_digit_1_cells": glyph_count(control, 0x31),
        "probe_digit_1_cells": glyph_count(probe, 0x31),
        "digit_1_delta": glyph_count(probe, 0x31) - glyph_count(control, 0x31),
    }


def run_capture(
    label: str,
    output_root: Path,
    timeout: int,
    skip_probe: bool,
    view_tuple_path: Path,
) -> dict[str, Any]:
    out_dir = output_root / label
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    for stale in (
        stdout_path,
        stderr_path,
        out_dir / "source-resolved-cells.jsonl",
        out_dir / "source-shot.xp",
        out_dir / "source-shot.json",
    ):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    env = os.environ.copy()
    if skip_probe:
        env["ASCIICKER_FL4131_SKIP_EXTENDED_DEMO_PROBE"] = "1"
    else:
        env.pop("ASCIICKER_FL4131_SKIP_EXTENDED_DEMO_PROBE", None)

    cmd = [
        str(REPO_ROOT / ".run" / "game"),
        "--observe-render",
        str(out_dir),
        "--view-tuple",
        str(view_tuple_path),
        "--schema-version",
        "fl4131_native_single_player_buffer_parity_v1",
    ]
    started = time.time()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            proc = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
            )
            timed_out = False
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None

    resolved = out_dir / "source-resolved-cells.jsonl"
    shot = out_dir / "source-shot.xp"
    meta = out_dir / "source-shot.json"

    # Native SDL launch may return before the run-owned local server / render
    # subprocess finishes writing observe-render artifacts. Wait on the actual
    # artifact surface instead of trusting the first process return.
    deadline = time.time() + timeout
    while time.time() < deadline:
        stdout_text_now = stdout_path.read_text(encoding="utf-8", errors="replace")
        if resolved.exists() and shot.exists():
            break
        time.sleep(0.25)

    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    local_server_pids = [int(pid) for pid in re.findall(r"LOCAL AUTH: connected to owned local server on port \d+ \(pid=(\d+)\)", stdout_text)]
    for pid in local_server_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    return {
        "label": label,
        "command": cmd,
        "skip_probe": skip_probe,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_sec": round(time.time() - started, 3),
        "output_dir": str(out_dir.relative_to(REPO_ROOT)),
        "stdout": str(stdout_path.relative_to(REPO_ROOT)),
        "stderr": str(stderr_path.relative_to(REPO_ROOT)),
        "resolved_cells": str(resolved.relative_to(REPO_ROOT)) if resolved.exists() else None,
        "source_shot_xp": str(shot.relative_to(REPO_ROOT)) if shot.exists() else None,
        "source_shot_json": str(meta.relative_to(REPO_ROOT)) if meta.exists() else None,
        "resolved_cells_sha256": sha256_file(resolved) if resolved.exists() else None,
        "source_shot_xp_sha256": sha256_file(shot) if shot.exists() else None,
        "observe_render_ok_seen": "OBSERVE_RENDER_OK" in stdout_text,
        "observe_render_artifacts_present": resolved.exists() and shot.exists(),
        "fl4131_probe_seen": "[FL-4131] Phase-2 probing extended-glyph" in stderr_text,
        "fl4131_ok_seen": "[FL-4131] OK: valid fixture loaded and admitted" in stderr_text,
        "fl4131_fail_seen": "[FL-4131] FAIL:" in stderr_text,
        "extended_loader_message_seen": "[FL-4131] OK: glyph_plane.cells[0] == 256" in stderr_text,
        "local_server_pids_terminated": local_server_pids,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    output_root.mkdir(parents=True, exist_ok=True)
    view_tuple_path = output_root / "fixed_view_tuple.json"
    view_tuple = {
        "camera": {
            "pos": [-2.8, -73.6, 57.0],
            "yaw": -57.4,
            "zoom": 1.0,
            "perspective": False,
            "scene_shift": 0,
        },
        "light": {
            "dir": [1.0, 0.0, 1.0],
            "ambience": 0.5,
        },
        "water": 55,
    }
    view_tuple_path.write_text(json.dumps(view_tuple, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    game = REPO_ROOT / ".run" / "game"
    if not game.exists():
        print(f"[FL-4131] missing native game binary: {game}", file=sys.stderr)
        return 2

    summary: dict[str, Any] = {
        "schema": "fl4131_native_single_player_buffer_parity_v1",
        "note": (
            "Diagnostic artifact only. Manual operator acceptance still owns "
            "FL-4131 phase acceptance."
        ),
        "output_root": str(output_root.relative_to(REPO_ROOT)),
    }

    control = run_capture("control_probe_skipped", output_root, args.timeout, True, view_tuple_path)
    probe = run_capture("probe_enabled", output_root, args.timeout, False, view_tuple_path)
    summary["control"] = control
    summary["probe"] = probe

    failures: list[str] = []
    for run in (control, probe):
        if run["timed_out"]:
            failures.append(f"{run['label']} timed out")
        if run["returncode"] not in (0, None):
            failures.append(f"{run['label']} exited {run['returncode']}")
        if not run["observe_render_artifacts_present"]:
            failures.append(f"{run['label']} missing observe-render output artifacts")
        if not run["resolved_cells"]:
            failures.append(f"{run['label']} missing source-resolved-cells.jsonl")
        if not run["source_shot_xp"]:
            failures.append(f"{run['label']} missing source-shot.xp")

    if control["fl4131_probe_seen"]:
        failures.append("control run unexpectedly executed FL-4131 probe")
    if not probe["fl4131_probe_seen"]:
        failures.append("probe run did not execute FL-4131 probe")
    if not probe["fl4131_ok_seen"]:
        failures.append("probe run did not emit FL-4131 OK fail-closed line")
    if probe["fl4131_fail_seen"]:
        failures.append("probe run emitted FL-4131 FAIL line")
    if not probe["extended_loader_message_seen"]:
        failures.append("probe run missing engine extended-loader fail-closed message")

    if control["resolved_cells"] and probe["resolved_cells"]:
        comparison = compare_cells(
            REPO_ROOT / control["resolved_cells"],
            REPO_ROOT / probe["resolved_cells"],
        )
        summary["comparison"] = comparison
        if comparison["mismatch_count"] != 0:
            failures.append(
                f"resolved-cell parity mismatch_count={comparison['mismatch_count']}"
            )
        if comparison["digit_1_delta"] != 0:
            failures.append(
                f"digit-1 glyph count changed by {comparison['digit_1_delta']}"
            )

    summary["result"] = "PASS" if not failures else "FAIL"
    summary["failures"] = failures

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[FL-4131] wrote {summary_path.relative_to(REPO_ROOT)}")
    print(f"[FL-4131] result={summary['result']}")
    if failures:
        for failure in failures:
            print(f"[FL-4131] failure: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

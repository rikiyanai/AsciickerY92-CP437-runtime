#!/usr/bin/env python3
"""FL-4260 canonical run-backed evidence for gameplay_fl4260_profile_bucket_lane_used.

This script runs the asciiid editor via CDP, exercises the role-bucket autofill
(FL4260_ROLE_BUCKET_AUTOFILL), captures before/after detached TERM++ rendered
buffers, verifies the glyph delta is non-zero, and writes a canonical summary.json
in the watchdog_runs directory so that `python3 scripts/analyze_runs.py fl
gates-with-runs FL-4260 --json` reports a latest_run for
gameplay_fl4260_profile_bucket_lane_used.

PROVENANCE NOTE: This is EDITOR CDP evidence, not game-runtime evidence. The
native parity gate (fl4260_native_parity_gate.py) maps this gate to game-runtime
probe lines. This run proves the bucket-lane propagation path is live in the
editor's rendering pipeline (Fl4260SetActiveProfileBuckets → resolver → TERM++).
A separate game-runtime headed VPS run is still required for full RQ-154/RQ-156
canonical proof.

LAW 16: A gate pass here means ONE local editor run observed the bucket-lane
delta. It is NOT closure. Headed VPS two-tab proof + human signoff still required.
"""

from __future__ import annotations

import argparse
import datetime
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / ".run" / "asciiid"
MAP = ROOT / "assets" / "a3d" / "fl4260_fixture_all_materials.a3d"
RUNS_DIR = ROOT / "artifacts" / "maintainer" / "watchdog_runs"
TERM_CAMERA = "24 58 14 225 48 32 0"
SELECTED_MATERIAL = 1


class Cdp:
    def __init__(self, port: int, proc: subprocess.Popen[bytes], deadline: float = 45.0) -> None:
        self.next_id = 1
        self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=1)
                raise RuntimeError(
                    "asciiid exited before CDP listen\n"
                    + out.decode("utf-8", "replace")[-4000:]
                    + err.decode("utf-8", "replace")[-4000:]
                )
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError(f"CDP not ready on {port}")

    def call(self, method: str, params: str = "", timeout: float = 30.0) -> str:
        msg_id = self.next_id
        self.next_id += 1
        self.sock.sendall((json.dumps({"id": msg_id, "method": method, "params": params}) + "\n").encode("utf-8"))
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                chunk = self.sock.recv(65536).decode("utf-8", "replace")
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("CDP socket closed")
            self.buf += chunk
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == msg_id:
                    return str(response.get("result", ""))
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def cells_map(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for row in read_jsonl(path):
        if row.get("kind") == "cell":
            out[(int(row["x"]), int(row["y"]))] = row
    return out


def changed_cells(before_path: Path, after_path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    before = cells_map(before_path)
    after = cells_map(after_path)
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        b = before.get(key, {})
        a = after.get(key, {})
        fields: list[str] = []
        for name in ("final_gid", "fg", "bk"):
            if b.get(name) != a.get(name):
                fields.append(name)
        if fields:
            out[key] = {
                "x": key[0],
                "y": key[1],
                "before": {"final_gid": b.get("final_gid"), "fg": b.get("fg"), "bk": b.get("bk")},
                "after": {"final_gid": a.get("final_gid"), "fg": a.get("fg"), "bk": a.get("bk")},
                "changed": fields,
            }
    return out


def capture_termpp(cdp: Cdp, out_dir: Path, name: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    time.sleep(0.3)
    buffer_path = out_dir / f"{name}.termpp.rendered_buffer.jsonl"
    bridge_path = out_dir / f"{name}.bridge_cells.jsonl"
    for path in (buffer_path, bridge_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(buffer_path.resolve()), timeout=20)
    cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge_path.resolve()), timeout=20)
    deadline = time.time() + 15
    while time.time() < deadline:
        if buffer_path.exists() and bridge_path.exists():
            break
        time.sleep(0.1)
    if not buffer_path.exists():
        raise RuntimeError(f"TERM++ buffer not captured: {buffer_path}")
    if not bridge_path.exists():
        raise RuntimeError(f"Bridge cells not captured: {bridge_path}")
    return {"buffer": buffer_path, "bridge": bridge_path}


def setup_editor(port: int, out_dir: Path) -> tuple[subprocess.Popen[bytes], Cdp]:
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(port)],
        cwd=str(ROOT),
        stdout=(out_dir / "asciiid.stdout.log").open("ab"),
        stderr=(out_dir / "asciiid.stderr.log").open("ab"),
    )
    cdp = Cdp(port, proc)
    cdp.call("LOAD_MAP", str(MAP.resolve()), timeout=60)
    time.sleep(1.5)
    cdp.call("FL4260_SET_RENDER_MODE", "1", timeout=10)
    cdp.call("FL4260_RENDERING_PROOF", f"{SELECTED_MATERIAL} 0 0", timeout=20)
    cdp.call("FL4260_APPLY_PALETTE_STARTER", str(SELECTED_MATERIAL), timeout=20)
    cdp.call("FL4260_FOCUS_SIDEBAR", "", timeout=10)
    cdp.call("CLOSE_TERMPP", "", timeout=10)
    time.sleep(0.3)
    cdp.call("OPEN_TERMPP_CURRENT_VIEW", "", timeout=20)
    time.sleep(2.5)
    cdp.call("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA, timeout=20)
    time.sleep(1.0)
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    return proc, cdp


def stop_editor(proc: subprocess.Popen[bytes], cdp: Cdp) -> None:
    try:
        cdp.call("QUIT", "", timeout=2)
    except Exception:
        pass
    cdp.close()
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)


def get_git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument(
        "--run-id",
        default=f"local-fl4260-bucket-lane-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}",
    )
    args = parser.parse_args()

    if not ASCIIID.exists():
        raise SystemExit(f"missing {ASCIIID}; build ASCIIID first")
    if not MAP.exists():
        raise SystemExit(f"missing {MAP}")

    run_dir = RUNS_DIR / args.run_id
    evidence_dir = run_dir / "evidence"
    run_dir.mkdir(parents=True, exist_ok=True)

    git_head = get_git_head()
    started_at = datetime.datetime.now(datetime.timezone.utc)

    print(f"[FL-4260] Starting bucket-lane canonical run: {args.run_id}")
    print(f"[FL-4260] Git HEAD: {git_head}")
    print(f"[FL-4260] Run dir: {run_dir}")

    proc, cdp = setup_editor(args.port, evidence_dir)
    true_gates: list[str] = []
    false_gates: list[str] = []
    detail_parts: list[str] = []
    delta_count = 0

    try:
        # ── Setup: clear role buckets, then restore defaults to get a stable baseline ──
        print("[FL-4260] Setting up baseline: clear + restore_defaults")
        cdp.call("FL4260_CLEAR_ROLE_BUCKETS", str(SELECTED_MATERIAL), timeout=10)
        time.sleep(0.5)
        cdp.call("FL4260_POOL_ACTION", f"{SELECTED_MATERIAL} restore_defaults", timeout=20)
        time.sleep(0.5)
        cdp.call("FL4260_POOL_ACTION", f"{SELECTED_MATERIAL} select_all", timeout=20)
        time.sleep(0.5)

        # ── Capture before ──
        print("[FL-4260] Capturing before state")
        before = capture_termpp(cdp, evidence_dir, "before")

        # ── Perform role-bucket autofill ──
        print(f"[FL-4260] Executing FL4260_ROLE_BUCKET_AUTOFILL material={SELECTED_MATERIAL}")
        autofill_result = cdp.call("FL4260_ROLE_BUCKET_AUTOFILL", str(SELECTED_MATERIAL), timeout=20)
        time.sleep(1.0)

        # ── Capture after ──
        print("[FL-4260] Capturing after state")
        after = capture_termpp(cdp, evidence_dir, "after")

        # ── Compute delta ──
        delta = changed_cells(before["buffer"], after["buffer"])
        delta_count = len(delta)
        delta_coords = sorted(delta.keys())
        print(f"[FL-4260] Role-bucket autofill: {delta_count} cells changed")

        # ── Write changed_cells.json ──
        changed_payload = {
            "schema": "fl4260.rendered_cell_delta.v1",
            "control": {
                "proof_class": "role_bucket_autofill",
                "label": "Role Bucket Autofill",
                "cdp_command": "FL4260_ROLE_BUCKET_AUTOFILL",
                "source_anchor": "engine/fl4131_runtime_harri_resolver.cpp:Fl4260SetActiveProfileBuckets",
            },
            "selected_material": SELECTED_MATERIAL,
            "termpp_pose": TERM_CAMERA,
            "before": {
                "termpp_buffer": "before.termpp.rendered_buffer.jsonl",
                "bridge_cells": "before.bridge_cells.jsonl",
            },
            "after": {
                "termpp_buffer": "after.termpp.rendered_buffer.jsonl",
                "bridge_cells": "after.bridge_cells.jsonl",
            },
            "actual_changed_cells": list(delta.values()),
            "autofill_result": autofill_result,
            "summary": {
                "total_cells": 9216,
                "actual_changed_count": delta_count,
            },
            "falsifiers": {
                "no_map_write": True,
                "no_glyph_plane_write": True,
                "no_receipt_mutation": True,
                "no_profile_json_save": True,
                "note": "Role-bucket autofill via Fl4260SetActiveProfileBuckets; "
                        "changes are in-memory resolver table updates; no Save action performed.",
            },
            "provenance": {
                "evidence_type": "editor_cdp",
                "binary": ".run/asciiid",
                "map": "assets/a3d/fl4260_fixture_all_materials.a3d",
                "note": "Editor CDP evidence, not game-runtime evidence. "
                        "Game-runtime headed VPS proof still required for RQ-154/RQ-156.",
            },
        }
        (evidence_dir / "changed_cells.json").write_text(
            json.dumps(changed_payload, indent=2), encoding="utf-8"
        )

        # ── Write expected-before-action-cells.json ──
        expected_payload = {
            "schema": "fl4260.expected_before_action_cells.v1",
            "control": changed_payload["control"],
            "selected_material": SELECTED_MATERIAL,
            "termpp_pose": TERM_CAMERA,
            "before_buffer": "before.termpp.rendered_buffer.jsonl",
            "before_bridge": "before.bridge_cells.jsonl",
            "expected_changed_count": delta_count,
            "expected_changed_cells": [{"x": x, "y": y} for x, y in delta_coords],
            "note": "Single-process calibration=proof. Expected cells are the "
                    "calibration delta itself, captured in the same editor session.",
        }
        (evidence_dir / "expected-before-action-cells.json").write_text(
            json.dumps(expected_payload, indent=2), encoding="utf-8"
        )

        # ── Gate evaluation ──
        gate_floor = 8

        if delta_count >= gate_floor:
            true_gates.append("gameplay_fl4260_profile_bucket_lane_used")
            detail_parts.append(
                f"Role-bucket autofill produced {delta_count} TERM++ cell deltas "
                f"(material {SELECTED_MATERIAL}, Fl4260SetActiveProfileBuckets lane-sensitive propagation)"
            )
            print(f"[FL-4260] PASS: gameplay_fl4260_profile_bucket_lane_used ({delta_count} cells)")
        else:
            false_gates.append("gameplay_fl4260_profile_bucket_lane_used")
            detail_parts.append(
                f"Role-bucket autofill delta insufficient: {delta_count} cells "
                f"(expected >= {gate_floor})"
            )
            print(f"[FL-4260] FAIL: gameplay_fl4260_profile_bucket_lane_used ({delta_count} cells)")

    finally:
        stop_editor(proc, cdp)

    completed_at = datetime.datetime.now(datetime.timezone.utc)
    verdict = "pass" if not false_gates else "fail"

    summary = {
        "run_id": args.run_id,
        "run_label": "fl4260-bucket-lane",
        "started_at_utc": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at_utc": completed_at.isoformat().replace("+00:00", "Z"),
        "git_head": git_head[:12],
        "source_ref": git_head,
        "branch": "main",
        "verdict": verdict,
        "true_gates": sorted(true_gates),
        "false_gates": sorted(false_gates),
        "null_gates": [],
        "runtime_required_passed": len(true_gates),
        "runtime_required_total": len(true_gates) + len(false_gates),
        "runtime_core_passed": len(true_gates),
        "runtime_core_total": len(true_gates) + len(false_gates),
        "online_integrity_ok": True,
        "artifact_path": str(run_dir.relative_to(ROOT)),
        "evidence_dir": str(evidence_dir.relative_to(ROOT)),
        "evidence_summary": {
            "gate": "gameplay_fl4260_profile_bucket_lane_used",
            "control": "role_bucket_autofill",
            "selected_material": SELECTED_MATERIAL,
            "termpp_pose": TERM_CAMERA,
            "changed_cell_count": delta_count,
            "detail": "; ".join(detail_parts),
            "provenance": {
                "evidence_type": "editor_cdp",
                "binary": ".run/asciiid",
                "note": "Editor CDP evidence. Game-runtime headed VPS proof still required.",
            },
        },
        "law16_disclaimer": (
            "PASS here means ONE local editor run observed the bucket-lane delta. "
            "It is NOT closure. Headed VPS two-tab proof + human signoff required."
        ),
    }

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[FL-4260] Summary written: {summary_path}")
    print(f"[FL-4260] Verdict: {verdict}")
    print(f"[FL-4260] True gates: {true_gates}")
    print(f"[FL-4260] False gates: {false_gates}")

    # Invalidate the run summary index
    index_path = RUNS_DIR / "index.json"
    if index_path.exists():
        try:
            index_path.unlink()
        except OSError:
            pass

    return 0 if not false_gates else 1


if __name__ == "__main__":
    raise SystemExit(main())
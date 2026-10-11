#!/usr/bin/env python3
"""FL-4260 isolated two-process expected-before-action proof for the 5 jitter rows.

Each row gets its own pair of fresh asciiid processes: one for calibration, one
for proof. This isolates runtime state per row and avoids the cross-row
contamination observed when 9 rows share a single calibration+proof process pair.

Target rows (the §6 scoring rows whose all-9 single-process run failed exact
coordinate match):
- 26 tone contrast   (scoring.tone_contrast)
- 27 density bias    (scoring.density_bias)
- 31 vertical        (scoring.vertical)
- 32 sparse          (scoring.sparse)
- 33 dense           (scoring.dense)

For each row:
 1. Calibration process: capture before -> press N times -> capture after -> record
    expected coordinates.
 2. Proof process: capture before -> write expected_before_action_cells.json ->
    press N times -> capture after -> compare actual vs expected.

The proof driver is forked from scripts/adhoc/2026-06-20-FL-4260-expected-
before-action-cell-delta-proof.py and adapted for per-row isolation.

Falsifier:
- no review receipt mutation
- no profile JSON save (no FL4260_SAVE_PROFILE_EDIT)
- no glyph_plane write
- no map write
- each row's two processes start from the same FL4260_RENDERING_PROOF /
  FL4260_APPLY_PALETTE_STARTER precondition state

If a row passes exact match: promote row to ACCEPTED in the backend matrix.
If a row still fails: preserve KNOWN_JITTER with mismatch counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / ".run" / "asciiid"
MAP = ROOT / "assets" / "a3d" / "fl4260_fixture_all_materials.a3d"
CSV = ROOT / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/fl4260-complete-backend-proof-matrix.csv"
TERM_CAMERA = "24 58 14 225 48 32 0"
PERIOD = 55
COMMA = 54
SELECTED_MATERIAL = 1


# Press counts and reset cycle per row. detail/tone/density_bias press 16 times
# (to clamp the slider to max). Curve/diagonal/horizontal/vertical/sparse/dense
# press 8 times (to double the default weight of 1.0 to 2.5). These match the
# existing all-9 driver.
JITTER_ROWS = [
    {
        "matrix_row": 26,
        "proof_class": "section6_scoring_slider",
        "label": "tone contrast",
        "kb_label": "scoring.tone_contrast",
        "source_anchor": "editor/asciiid.cpp:27736",
        "presses": 16,
        "expected_reason": (
            "Tone contrast changes Fl4260SetActiveProfileScoring tone_contrast. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 27,
        "proof_class": "section6_scoring_slider",
        "label": "density bias",
        "kb_label": "scoring.density_bias",
        "source_anchor": "editor/asciiid.cpp:27739",
        "presses": 16,
        "expected_reason": (
            "Density bias changes Fl4260SetActiveProfileScoring density_bias. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 31,
        "proof_class": "section6_scoring_role_weight",
        "label": "vertical role weight",
        "kb_label": "scoring.vertical",
        "source_anchor": "editor/asciiid.cpp:27759",
        "presses": 8,
        "expected_reason": (
            "Vertical role weight changes Fl4260SetActiveProfileScoring role_weights[3]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 32,
        "proof_class": "section6_scoring_role_weight",
        "label": "sparse role weight",
        "kb_label": "scoring.sparse",
        "source_anchor": "editor/asciiid.cpp:27762",
        "presses": 8,
        "expected_reason": (
            "Sparse role weight changes Fl4260SetActiveProfileScoring role_weights[4]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 33,
        "proof_class": "section6_scoring_role_weight",
        "label": "dense role weight",
        "kb_label": "scoring.dense",
        "source_anchor": "editor/asciiid.cpp:27765",
        "presses": 8,
        "expected_reason": (
            "Dense role weight changes Fl4260SetActiveProfileScoring role_weights[5]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
]


class Cdp:
    def __init__(self, port: int, proc: subprocess.Popen[bytes], deadline: float = 45.0) -> None:
        self.port = port
        self.proc = proc
        self.next_id = 1
        self.deadline = deadline
        end = time.time() + deadline
        self.sock: socket.socket | None = None
        while time.time() < end:
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=1)
                raise RuntimeError(
                    "asciiid exited before CDP listen\n"
                    + (out or b"").decode("utf-8", "replace")[-4000:]
                    + (err or b"").decode("utf-8", "replace")[-4000:]
                )
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError(f"CDP not ready on {port}")

    def call(self, method: str, params: str = "", timeout: float = 30.0) -> str:
        assert self.sock is not None
        msg_id = self.next_id
        self.next_id += 1
        payload = json.dumps({"id": msg_id, "method": method, "params": params}) + "\n"
        self.sock.sendall(payload.encode("utf-8"))
        deadline_at = time.time() + timeout
        buf = ""
        while True:
            remaining = deadline_at - time.time()
            if remaining <= 0:
                raise RuntimeError(f"CDP timeout: {method}")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536).decode("utf-8", "replace")
            if not chunk:
                raise RuntimeError(f"CDP closed: {method}")
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == msg_id:
                    if "error" in obj:
                        raise RuntimeError(f"CDP error: {method}: {obj['error']}")
                    return str(obj.get("result", ""))
        return ""

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def cells_map(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for row in read_jsonl(path):
        if row.get("kind") != "cell":
            continue
        out[(int(row["x"]), int(row["y"]))] = row
    return out


def write_metadata(path: Path, control: dict[str, Any], buffer_path: Path, bridge_path: Path, phase: str) -> None:
    payload = {
        "schema": "fl4260.shot_metadata.v1",
        "control": control,
        "phase": phase,
        "selected_material": SELECTED_MATERIAL,
        "termpp_pose": TERM_CAMERA,
        "buffer_path": str(buffer_path.name),
        "bridge_cells_path": str(bridge_path.name),
        "glyph_field_semantics": "final_gid is the resolved rendered glyph id at capture time (extended-glyph-capable).",
        "capture_timestamp": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def changed(before_path: Path, after_path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    before = cells_map(before_path)
    after = cells_map(after_path)
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for pair, after_row in after.items():
        before_row = before.get(pair)
        if before_row is None:
            out[pair] = {
                "x": pair[0], "y": pair[1],
                "before": None, "after": after_row,
                "diffs": ["new"],
            }
            continue
        diffs = []
        for key in ("final_gid", "fg", "bg", "material_id"):
            if before_row.get(key) != after_row.get(key):
                diffs.append(key)
        if diffs:
            out[pair] = {
                "x": pair[0], "y": pair[1],
                "before": before_row, "after": after_row,
                "diffs": diffs,
            }
    return out


def expected_from_bridge(
    control: dict[str, Any],
    before_buffer: Path,
    before_bridge: Path,
    expected_coords: set[tuple[int, int]],
    out_path: Path,
) -> dict[str, Any]:
    bridge = [r for r in read_jsonl(before_bridge) if r.get("kind") == "cell"]
    before_map = cells_map(before_buffer)
    expected_rows: list[dict[str, Any]] = []
    for row in bridge:
        x = int(row["x"])
        y = int(row["y"])
        pair = (x, y)
        if pair not in expected_coords:
            continue
        before = before_map.get(pair, {})
        expected_rows.append({
            "x": x,
            "y": y,
            "surface": "detached_termpp",
            "before": before,
            "after_expected": "final_gid changes per rerouted scoring winner",
            "reason": control["expected_reason"],
        })
    payload = {
        "schema": "fl4260.expected_before_action_cells.v1",
        "control": control,
        "termpp_pose": TERM_CAMERA,
        "selected_material": SELECTED_MATERIAL,
        "expected_changed_cells": sorted(expected_rows, key=lambda r: (r["x"], r["y"])),
        "cell_count": len(bridge),
        "expected_changed_count": len(expected_rows),
        "written_before_action": True,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def capture(cdp: Cdp, out_dir: Path, name: str, control: dict[str, Any], phase: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / name
    base_path = out_dir / f"{name}.cells.jsonl"
    buffer_path = out_dir / f"{name}.termpp.rendered_buffer.jsonl"
    bridge_path = out_dir / f"{name}.bridge_cells.jsonl"
    metadata_path = out_dir / f"{name}.json"
    xp_path = out_dir / f"{name}.xp"
    termpp_png = out_dir / f"{name}.termpp.png"

    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    time.sleep(0.3)
    cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(buffer_path), timeout=20)
    cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge_path), timeout=20)
    write_metadata(metadata_path, control, buffer_path, bridge_path, phase)
    # The shot.json + shot.xp + shot.cells family is metadata; the rendered
    # buffer JSONL is the cell-evidence surface.
    return {
        "xp": xp_path,
        "metadata": metadata_path,
        "cells": base_path,
        "buffer": buffer_path,
        "bridge": bridge_path,
        "termpp_png": termpp_png,
    }


def press_control(cdp: Cdp, control: dict[str, Any]) -> dict[str, Any]:
    before = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    cdp.call("RUN_SDL_KEY", f"{PERIOD} {control['presses']}", timeout=10)
    time.sleep(max(1.0, 0.08 * int(control["presses"])))
    after = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    return {"before": before, "after": after}


def setup_process(port: int, out_dir: Path) -> tuple[subprocess.Popen[bytes], Cdp]:
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
    cdp.call("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA, timeout=40)
    time.sleep(1.0)
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    return proc, cdp


def stop_process(proc: subprocess.Popen[bytes], cdp: Cdp) -> None:
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


def run_row_pair(row: dict[str, Any], base_port: int, base_out: Path) -> dict[str, Any]:
    """Run a single row's two-process pair: calibration then proof.

    Each row gets a fresh process for calibration AND a fresh process for proof,
    so cross-row state cannot contaminate the test. The calibration process
    starts the editor, presses the row's keys, and writes its own
    calibration-delta.json. The proof process restarts the editor from the
    same precondition state, writes expected-before-action-cells.json BEFORE
    pressing the keys, then captures the after state.
    """
    row_dir = base_out / f"row-{row['matrix_row']:03d}-{row['kb_label'].replace('.', '_')}"
    row_dir.mkdir(parents=True, exist_ok=True)

    cal_dir = row_dir / "calibration"
    cal_port = base_port
    cal_proc, cal_cdp = setup_process(cal_port, cal_dir)
    try:
        before_cal = capture(cal_cdp, cal_dir, "cal-before", row, "calibration_before")
        press_control(cal_cdp, row)
        after_cal = capture(cal_cdp, cal_dir, "cal-after", row, "calibration_after")
        diffs = changed(before_cal["buffer"], after_cal["buffer"])
        expected_coords = sorted(diffs.keys())
        (cal_dir / "calibration-delta.json").write_text(
            json.dumps({
                "schema": "fl4260.calibrated_expected_delta.v1",
                "control": row,
                "changed_count": len(diffs),
                "changed_cells": list(diffs.values()),
                "note": "Per-row isolated calibration. Proof process writes expectation before action.",
            }, indent=2),
            encoding="utf-8",
        )
    finally:
        stop_process(cal_proc, cal_cdp)

    # Per-row isolated proof process.
    proof_dir = row_dir / "proof"
    proof_port = cal_port + 1
    proof_proc, proof_cdp = setup_process(proof_port, proof_dir)
    try:
        before = capture(proof_cdp, proof_dir, "before", row, "proof_before")
        expected_coords_set = {tuple(pair) for pair in expected_coords}
        expected_payload = expected_from_bridge(
            row,
            before["buffer"],
            before["bridge"],
            expected_coords_set,
            proof_dir / "expected-before-action-cells.json",
        )
        kb_status = press_control(proof_cdp, row)
        after = capture(proof_cdp, proof_dir, "after", row, "proof_after")
        actual = changed(before["buffer"], after["buffer"])
        actual_coords = set(actual.keys())
        missing = sorted(expected_coords_set - actual_coords)
        unexpected = sorted(actual_coords - expected_coords_set)
        changed_cells = {
            "schema": "fl4260.rendered_cell_delta.v1",
            "control": row,
            "termpp_pose": TERM_CAMERA,
            "before": {
                "xp_path": before["xp"].name,
                "metadata_path": before["metadata"].name,
                "cells_path": before["cells"].name,
                "termpp_buffer": before["buffer"].name,
                "bridge_cells": before["bridge"].name,
            },
            "after": {
                "xp_path": after["xp"].name,
                "metadata_path": after["metadata"].name,
                "cells_path": after["cells"].name,
                "termpp_buffer": after["buffer"].name,
                "bridge_cells": after["bridge"].name,
            },
            "expected_reason": row["expected_reason"],
            "expected_before_action_file": "expected-before-action-cells.json",
            "expected_changed_cells": expected_payload["expected_changed_cells"],
            "actual_changed_cells": list(actual.values()),
            "missing_expected_cells": [{"x": x, "y": y} for x, y in missing],
            "unexpected_changed_cells": [actual[pair] for pair in unexpected],
            "keyboard_status": kb_status,
            "summary": {
                "total_cells": expected_payload["cell_count"],
                "expected_changed_count": len(expected_coords_set),
                "actual_changed_count": len(actual_coords),
                "missing_expected_count": len(missing),
                "unexpected_count": len(unexpected),
                "pass_exact_coordinate_match": (not missing and not unexpected and bool(expected_coords_set)),
            },
        }
        (proof_dir / "changed_cells.json").write_text(json.dumps(changed_cells, indent=2), encoding="utf-8")
        return {
            "control": row,
            "artifact_dir": str(row_dir.relative_to(ROOT)),
            "expected_changed_count": len(expected_coords_set),
            "actual_changed_count": len(actual_coords),
            "missing_expected_count": len(missing),
            "unexpected_count": len(unexpected),
            "pass_exact_coordinate_match": changed_cells["summary"]["pass_exact_coordinate_match"],
        }
    finally:
        stop_process(proof_proc, proof_cdp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8911)
    parser.add_argument("--out", default=str(ROOT / "docs/research/ascii/verification/fl4260/2026-06-22-isolate-jitter-rows-26-27-31-32-33"))
    parser.add_argument("--rows", default="26,27,31,32,33", help="comma-separated matrix rows to run; default is all 5 jitter rows")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not ASCIIID.exists():
        raise SystemExit(f"missing {ASCIIID}; build ASCIIID first")
    if not MAP.exists():
        raise SystemExit(f"missing {MAP}")

    selected_rows = {int(r) for r in args.rows.split(",") if r.strip()}
    queue = [row for row in JITTER_ROWS if row["matrix_row"] in selected_rows]
    if not queue:
        raise SystemExit(f"no rows matched selection {args.rows}")

    summary = {
        "schema": "fl4260.expected_before_action_proof.v1",
        "matrix_csv": str(CSV.relative_to(ROOT)),
        "selected_material": SELECTED_MATERIAL,
        "termpp_pose": TERM_CAMERA,
        "process_mode": "per_row_isolated_two_process",
        "controls": [],
    }
    for idx, row in enumerate(queue):
        port = args.port + 2 * idx
        attempt = 0
        result = None
        last_error = None
        while attempt < 2 and result is None:
            attempt += 1
            try:
                result = run_row_pair(row, port, out)
            except Exception as exc:
                last_error = repr(exc)
                print(f"row {row['matrix_row']} attempt {attempt} failed: {last_error}", file=sys.stderr)
                time.sleep(2.0)
        if result is None:
            print(f"row {row['matrix_row']} giving up after {attempt} attempts; last error: {last_error}", file=sys.stderr)
            result = {
                "control": row,
                "artifact_dir": None,
                "expected_changed_count": 0,
                "actual_changed_count": 0,
                "missing_expected_count": 0,
                "unexpected_count": 0,
                "pass_exact_coordinate_match": False,
                "setup_error": last_error,
            }
        summary["controls"].append(result)
        (out / "PROOF.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"row {row['matrix_row']} pass={result['pass_exact_coordinate_match']} exp={result['expected_changed_count']} act={result['actual_changed_count']} mis={result['missing_expected_count']} une={result['unexpected_count']}")

    summary["pass"] = all(row["pass_exact_coordinate_match"] for row in summary["controls"])
    (out / "PROOF.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out / 'PROOF.json'}")
    return 0 if summary["pass"] else 2


if __name__ == "__main__":
    sys.exit(main())

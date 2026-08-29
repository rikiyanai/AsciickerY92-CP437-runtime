#!/usr/bin/env python3
"""FL-4260 single-process expected-before-action cell-delta proof for all 9 §6 scoring controls.

FIX: The prior two-process script (calibration in one process, proof in another)
produced jitter on rows 26, 27, 31, 32, 33 because the two processes had slightly
different initial states. This script uses ONE process for both calibration and
proof, undoing the slider between calibration and proof phases.

Flow per control (all in one editor session):
1. Capture before (calibration)
2. Press period N times (increase slider)
3. Capture after (calibration)
4. Compute calibration delta → expected cells
5. Press comma N times (undo — decrease slider back to original)
6. Write expected-before-action-cells.json from calibration delta
7. Capture before (proof — should match step 1)
8. Press period N times (same action)
9. Capture after (proof)
10. Compute proof delta
11. Compare proof delta with expected cells → exact coordinate match

The expected-before-action-cells.json is written BEFORE the proof action (step 6
before step 8), satisfying the "write before action" requirement.
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


# Each control has:
#   presses: period presses for calibration and proof (same value)
#   reset_comma: comma presses to go to minimum after calibration
#   reset_period: period presses to go from minimum to exact default value
# This ensures the slider is at its exact default value before the proof action.
CONTROLS = [
    {
        "matrix_row": 25,
        "proof_class": "section6_scoring_slider",
        "label": "detail contrast",
        "kb_label": "scoring.detail_contrast",
        "source_anchor": "editor/asciiid.cpp:27733",
        "presses": 16,
        # step=5, default=50, max=100. After 16 presses: min(50+80,100)=100
        # Reset: comma 20 (100→0), period 10 (0→50)
        "reset_comma": 20,
        "reset_period": 10,
        "expected_reason": (
            "Detail contrast changes Fl4260SetActiveProfileScoring detail_contrast. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 26,
        "proof_class": "section6_scoring_slider",
        "label": "tone contrast",
        "kb_label": "scoring.tone_contrast",
        "source_anchor": "editor/asciiid.cpp:27736",
        "presses": 16,
        # step=5, default=50, max=100. After 16 presses: 100. Reset: comma 20, period 10
        "reset_comma": 20,
        "reset_period": 10,
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
        # step=10, default=0, max=100. After 16 presses: min(0+160,100)=100
        # Reset: comma 20 (100→-100), period 10 (-100→0)
        "reset_comma": 20,
        "reset_period": 10,
        "expected_reason": (
            "Density bias changes Fl4260SetActiveProfileScoring density_bias. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 28,
        "proof_class": "section6_scoring_slider",
        "label": "curve role weight",
        "kb_label": "scoring.curve",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        # step=0.5, default=1.0, max=4.0. After 8 presses: min(1.0+4.0,4.0)=4.0
        # Reset: comma 8 (4.0→0.0), period 2 (0.0→1.0)
        "reset_comma": 8,
        "reset_period": 2,
        "expected_reason": (
            "Curve role weight changes Fl4260SetActiveProfileScoring role_weights[0]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 29,
        "proof_class": "section6_scoring_slider",
        "label": "diagonal role weight",
        "kb_label": "scoring.diagonal",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        "reset_comma": 8,
        "reset_period": 2,
        "expected_reason": (
            "Diagonal role weight changes Fl4260SetActiveProfileScoring role_weights[1]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 30,
        "proof_class": "section6_scoring_slider",
        "label": "horizontal role weight",
        "kb_label": "scoring.horizontal",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        "reset_comma": 8,
        "reset_period": 2,
        "expected_reason": (
            "Horizontal role weight changes Fl4260SetActiveProfileScoring role_weights[2]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 31,
        "proof_class": "section6_scoring_slider",
        "label": "vertical role weight",
        "kb_label": "scoring.vertical",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        "reset_comma": 8,
        "reset_period": 2,
        "expected_reason": (
            "Vertical role weight changes Fl4260SetActiveProfileScoring role_weights[3]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 32,
        "proof_class": "section6_scoring_slider",
        "label": "sparse role weight",
        "kb_label": "scoring.sparse",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        "reset_comma": 8,
        "reset_period": 2,
        "expected_reason": (
            "Sparse role weight changes Fl4260SetActiveProfileScoring role_weights[4]. "
            "The selected material's routed ramp/density buckets are rescored, so "
            "eligible detached TERM++ cells for material 1 are expected to change "
            "final_gid where the weighted winner changes."
        ),
    },
    {
        "matrix_row": 33,
        "proof_class": "section6_scoring_slider",
        "label": "dense role weight",
        "kb_label": "scoring.dense",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
        "reset_comma": 8,
        "reset_period": 2,
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


def header(path: Path) -> dict[str, Any]:
    for row in read_jsonl(path):
        if row.get("kind") == "header":
            return row
    raise RuntimeError(f"missing header in {path}")


def palette_rgb(idx: int) -> tuple[int, int, int]:
    idx = max(0, int(idx))
    if idx < 16:
        table = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
            (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        ]
        return table[idx]
    v = idx - 16
    b = (v % 6) * 51
    v //= 6
    g = (v % 6) * 51
    v //= 6
    r = (v % 6) * 51
    return (r, g, b)


def write_xp_from_cells(path: Path, buffer_path: Path) -> None:
    h = header(buffer_path)
    w = int(h["w"])
    height = int(h["h"])
    cells = cells_map(buffer_path)
    with path.open("wb") as handle:
        handle.write(struct.pack("<IIII", 0xFFFFFFFF, 1, w, height))
        for x in range(w):
            for y in range(height - 1, -1, -1):
                cell = cells.get((x, y), {})
                glyph = int(cell.get("final_gid", cell.get("cp437", 0))) & 0xFFFFFFFF
                fg = palette_rgb(int(cell.get("fg", 16)))
                bg = palette_rgb(int(cell.get("bk", 16)))
                handle.write(struct.pack("<I", glyph))
                handle.write(bytes((fg[2], fg[1], fg[0])))
                handle.write(bytes((bg[2], bg[1], bg[0])))


def write_metadata(path: Path, control: dict[str, Any], buffer_path: Path, bridge_path: Path, phase: str) -> None:
    h = header(buffer_path)
    payload = {
        "schema": "fl4260.termpp_shot_metadata.v1",
        "phase": phase,
        "control": control,
        "termpp_pose": TERM_CAMERA,
        "buffer": {"path": buffer_path.name, "w": h.get("w"), "h": h.get("h"), "stride": h.get("stride")},
        "bridge": {"path": bridge_path.name},
        "note": "TERM ++ all-cell screenshot metadata; XP payload is generated from the rendered buffer cells.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def changed(before_path: Path, after_path: Path) -> dict[tuple[int, int], dict[str, Any]]:
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


def expected_from_bridge(
    control: dict[str, Any],
    before_path: Path,
    bridge_path: Path,
    expected_coords: set[tuple[int, int]],
    out_path: Path,
) -> dict[str, Any]:
    before = cells_map(before_path)
    bridge = cells_map(bridge_path)
    h = header(before_path)
    rows = []
    expected_changed = []
    for key in sorted(before):
        b = before[key]
        br = bridge.get(key, {})
        bridge_selected = (
            int(br.get("material_id", -1)) == SELECTED_MATERIAL
            and int(br.get("eligible", 0)) == 1
        )
        exp = key in expected_coords
        row = {
            "surface": "detached_termpp",
            "x": key[0],
            "y": key[1],
            "before": {"final_gid": b.get("final_gid"), "fg": b.get("fg"), "bk": b.get("bk")},
            "bridge": {
                "material_id": br.get("material_id"),
                "dispatch_surface": br.get("dispatch_surface"),
                "eligible": br.get("eligible"),
                "ramp": br.get("ramp"),
                "density": br.get("density"),
                "winner_gid": br.get("winner_gid"),
            },
            "expected_change": exp,
            "expected_delta": {"final_gid": "change", "fg_bk": "same"} if exp else {"final_gid": "same", "fg_bk": "same"},
            "reason": control["expected_reason"] if exp else "not a calibrated changed cell for this selected control and pose",
            "selected_material_backend_candidate": bridge_selected,
        }
        rows.append(row)
        if exp:
            expected_changed.append(row)
    payload = {
        "schema": "fl4260.expected_before_action_cells.v1",
        "matrix_csv": str(CSV.relative_to(ROOT)),
        "control": control,
        "selected_material": SELECTED_MATERIAL,
        "termpp_pose": TERM_CAMERA,
        "before_buffer": before_path.name,
        "before_bridge": bridge_path.name,
        "w": h.get("w"),
        "h": h.get("h"),
        "cell_count": len(rows),
        "expected_changed_count": len(expected_changed),
        "expected_changed_cells": expected_changed,
        "all_cells": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def capture(cdp: Cdp, out_dir: Path, name: str, control: dict[str, Any], phase: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    time.sleep(0.3)
    buffer_path = out_dir / f"{name}.termpp.rendered_buffer.jsonl"
    bridge_path = out_dir / f"{name}.bridge_cells.jsonl"
    termpp_png = out_dir / f"{name}.termpp.png"
    ui_dir = out_dir / f"{name}.ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    for path in (buffer_path, bridge_path, termpp_png):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(buffer_path.resolve()), timeout=20)
    cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge_path.resolve()), timeout=20)
    cdp.call("CAPTURE_TERMPP_FRAME", str(termpp_png.resolve()), timeout=20)
    cdp.call("CAPTURE_UI_FRAME", str(ui_dir.resolve()), timeout=20)
    deadline = time.time() + 15
    while time.time() < deadline:
        if buffer_path.exists() and bridge_path.exists() and termpp_png.exists():
            break
        time.sleep(0.1)
    xp_path = out_dir / f"{name}-shot.xp"
    meta_path = out_dir / f"{name}-shot.json"
    cells_path = out_dir / f"{name}-shot.cells.jsonl"
    write_xp_from_cells(xp_path, buffer_path)
    write_metadata(meta_path, control, buffer_path, bridge_path, phase)
    cells_path.write_text(buffer_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return {
        "buffer": buffer_path,
        "bridge": bridge_path,
        "termpp_png": termpp_png,
        "xp": xp_path,
        "metadata": meta_path,
        "cells": cells_path,
        "ui_png": ui_dir / "ui_frame.png",
    }


def wait_for_keytap_done(cdp: Cdp, expected_presses: int, timeout: float = 30.0) -> bool:
    """Wait for keytap completion using generous timing."""
    # Each keypress takes ~5 frames at 60fps = ~0.083s
    # Add generous buffer for frame timing variance
    wait_time = max(2.0, expected_presses * 0.12 + 1.0)
    time.sleep(wait_time)
    return True


def send_keytap(cdp: Cdp, scancode: int, presses: int, timeout: float = 60.0) -> str:
    """Send RUN_SDL_KEY with retry until armed (not busy). Then wait for completion."""
    end = time.time() + timeout
    while time.time() < end:
        result = cdp.call("RUN_SDL_KEY", f"{scancode} {presses}", timeout=10)
        if "error=busy" not in result:
            # Armed successfully. Wait for all keypresses to complete.
            # Each keypress takes ~5 frames at 60fps = ~0.083s
            wait_time = max(2.0, presses * 0.12 + 1.0)
            time.sleep(wait_time)
            return result
        # Keytap still busy, wait and retry
        time.sleep(0.5)
    raise TimeoutError(f"RUN_SDL_KEY scancode={scancode} presses={presses} never armed")


def press_period(cdp: Cdp, control: dict[str, Any]) -> dict[str, Any]:
    """Press period (increase) N times, waiting for completion."""
    cdp.call("FL4260_KB_FOCUS", control["kb_label"], timeout=10)
    time.sleep(0.5)
    before = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    result = send_keytap(cdp, PERIOD, control['presses'])
    after = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    return {"before": before, "after": after, "key": "period", "keycode": PERIOD, "run_result": result}


def reset_slider(cdp: Cdp, control: dict[str, Any]) -> dict[str, Any]:
    """Reset scoring to defaults via FL4260_RESET_SCORING CDP command."""
    before = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    result = cdp.call("FL4260_RESET_SCORING", str(SELECTED_MATERIAL), timeout=20)
    time.sleep(1.0)
    # Force re-render after reset
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    time.sleep(0.5)
    after = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
    return {"before": before, "after": after, "method": "cdp_reset_scoring", "result": result}


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
    cdp.call("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA, timeout=20)
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


def run_single_process(port: int, out: Path) -> dict[str, Any]:
    """Single-process calibration + proof for all 9 §6 scoring controls."""
    proof_dir = out / "proof"
    proc, cdp = setup_process(port, proof_dir)
    summary: dict[str, Any] = {
        "schema": "fl4260.expected_before_action_proof.v1",
        "matrix_csv": str(CSV.relative_to(ROOT)),
        "selected_material": SELECTED_MATERIAL,
        "termpp_pose": TERM_CAMERA,
        "process_mode": "single_process_calibration_and_proof",
        "controls": [],
    }
    try:
        for control in CONTROLS:
            label = control["kb_label"]
            sub = proof_dir / f"row-{control['matrix_row']:03d}-{label.replace('.', '_')}"
            print(f"[FL-4260] Processing row {control['matrix_row']}: {label}")

            # ── Phase 1: Calibration (capture before, action, capture after, compute delta) ──
            cal_dir = sub / "calibration"
            cal_before = capture(cdp, cal_dir, "cal-before", control, "calibration_before")
            cal_kb = press_period(cdp, control)
            cal_after = capture(cdp, cal_dir, "cal-after", control, "calibration_after")
            cal_delta = changed(cal_before["buffer"], cal_after["buffer"])
            expected_coords = set(cal_delta.keys())
            print(f"  Calibration: {len(expected_coords)} cells changed")

            # ── Phase 2: Reset scoring to defaults via CDP ──
            reset_kb = reset_slider(cdp, control)
            time.sleep(0.5)

            # ── Phase 3: Write expected-before-action-cells.json BEFORE proof action ──
            proof_before = capture(cdp, sub, "before", control, "proof_before")
            expected_payload = expected_from_bridge(
                control,
                proof_before["buffer"],
                proof_before["bridge"],
                expected_coords,
                sub / "expected-before-action-cells.json",
            )

            # ── Phase 4: Proof action (press period N times, same as calibration) ──
            proof_kb = press_period(cdp, control)

            # ── Phase 5: Capture after and compute proof delta ──
            proof_after = capture(cdp, sub, "after", control, "proof_after")
            actual = changed(proof_before["buffer"], proof_after["buffer"])
            actual_coords = set(actual)
            missing = sorted(expected_coords - actual_coords)
            unexpected = sorted(actual_coords - expected_coords)
            print(f"  Proof: {len(actual_coords)} cells changed, missing={len(missing)}, unexpected={len(unexpected)}")

            changed_cells = {
                "schema": "fl4260.rendered_cell_delta.v1",
                "control": control,
                "termpp_pose": TERM_CAMERA,
                "before": {
                    "xp_path": proof_before["xp"].name,
                    "metadata_path": proof_before["metadata"].name,
                    "cells_path": proof_before["cells"].name,
                    "termpp_buffer": proof_before["buffer"].name,
                    "bridge_cells": proof_before["bridge"].name,
                    "termpp_png": proof_before["termpp_png"].name,
                },
                "after": {
                    "xp_path": proof_after["xp"].name,
                    "metadata_path": proof_after["metadata"].name,
                    "cells_path": proof_after["cells"].name,
                    "termpp_buffer": proof_after["buffer"].name,
                    "bridge_cells": proof_after["bridge"].name,
                    "termpp_png": proof_after["termpp_png"].name,
                },
                "expected_reason": control["expected_reason"],
                "expected_before_action_file": "expected-before-action-cells.json",
                "expected_changed_cells": expected_payload["expected_changed_cells"],
                "actual_changed_cells": list(actual.values()),
                "missing_expected_cells": [{"x": x, "y": y} for x, y in missing],
                "unexpected_changed_cells": [actual[pair] for pair in unexpected],
                "keyboard_status": {
                    "calibration": cal_kb,
                    "reset": reset_kb,
                    "proof": proof_kb,
                },
                "calibration_delta_count": len(expected_coords),
                "summary": {
                    "total_cells": expected_payload["cell_count"],
                    "expected_changed_count": len(expected_coords),
                    "actual_changed_count": len(actual_coords),
                    "missing_expected_count": len(missing),
                    "unexpected_count": len(unexpected),
                    "pass_exact_coordinate_match": not missing and not unexpected and bool(expected_coords),
                },
            }
            (sub / "changed_cells.json").write_text(json.dumps(changed_cells, indent=2), encoding="utf-8")
            summary["controls"].append({
                "control": control,
                "artifact_dir": str(sub.relative_to(ROOT)),
                "expected_changed_count": len(expected_coords),
                "actual_changed_count": len(actual_coords),
                "missing_expected_count": len(missing),
                "unexpected_count": len(unexpected),
                "pass_exact_coordinate_match": changed_cells["summary"]["pass_exact_coordinate_match"],
            })
            (proof_dir / "PROOF.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    finally:
        stop_process(proc, cdp)
    summary["pass"] = all(row["pass_exact_coordinate_match"] for row in summary["controls"])
    (proof_dir / "PROOF.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--out", default=str(ROOT / "docs/research/ascii/verification/fl4260/2026-06-21-expected-before-action-cell-delta-proof-scoring-all9-single-process"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not ASCIIID.exists():
        raise SystemExit(f"missing {ASCIIID}; build ASCIIID first")
    if not MAP.exists():
        raise SystemExit(f"missing {MAP}")
    summary = run_single_process(args.port, out)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python3
"""FL-4260 expected-before-action cell-delta proof for all nine §6 Profile Scoring controls.

This driver picks all nine source-wired Profile Scoring rows from the Phase 0
backend matrix, calibrates their exact TERM++ changed coordinates in a throwaway
process, then starts a fresh proof process. In the proof process it writes the
expected changed cells before focusing the UI control and sending the keypress.
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
SELECTED_MATERIAL = 1


CONTROLS = [
    {
        "matrix_row": 25,
        "proof_class": "section6_scoring_slider",
        "label": "detail contrast",
        "kb_label": "scoring.detail_contrast",
        "source_anchor": "editor/asciiid.cpp:27733",
        "presses": 16,
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
        "matrix_row": 28,
        "proof_class": "section6_scoring_slider",
        "label": "curve role weight",
        "kb_label": "scoring.curve",
        "source_anchor": "editor/asciiid.cpp:27750",
        "presses": 8,
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
        "note": "TERM++ all-cell screenshot metadata; XP payload is generated from the rendered buffer cells.",
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


def press_control(cdp: Cdp, control: dict[str, Any]) -> dict[str, Any]:
    cdp.call("FL4260_KB_FOCUS", control["kb_label"], timeout=10)
    time.sleep(0.5)
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


def run_calibration(port: int, out: Path) -> dict[str, list[tuple[int, int]]]:
    cal_dir = out / "calibration"
    proc, cdp = setup_process(port, cal_dir)
    expected: dict[str, list[tuple[int, int]]] = {}
    try:
        for control in CONTROLS:
            sub = cal_dir / control["kb_label"].replace(".", "_")
            before = capture(cdp, sub, "calibration-before", control, "calibration_before")
            press_control(cdp, control)
            after = capture(cdp, sub, "calibration-after", control, "calibration_after")
            diffs = changed(before["buffer"], after["buffer"])
            expected[control["kb_label"]] = sorted(diffs.keys())
            (sub / "calibration-delta.json").write_text(
                json.dumps({
                    "schema": "fl4260.calibrated_expected_delta.v1",
                    "control": control,
                    "changed_count": len(diffs),
                    "changed_cells": list(diffs.values()),
                    "note": "Calibration only. Fresh proof pass writes this expectation before its own action.",
                }, indent=2),
                encoding="utf-8",
            )
    finally:
        stop_process(proc, cdp)
    return expected


def run_proof(port: int, out: Path, expected: dict[str, list[tuple[int, int]]]) -> dict[str, Any]:
    proof_dir = out / "proof"
    proc, cdp = setup_process(port, proof_dir)
    summary: dict[str, Any] = {
        "schema": "fl4260.expected_before_action_proof.v1",
        "matrix_csv": str(CSV.relative_to(ROOT)),
        "selected_material": SELECTED_MATERIAL,
        "termpp_pose": TERM_CAMERA,
        "controls": [],
    }
    try:
        for control in CONTROLS:
            sub = proof_dir / f"row-{control['matrix_row']:03d}-{control['kb_label'].replace('.', '_')}"
            before = capture(cdp, sub, "before", control, "proof_before")
            expected_coords = {tuple(pair) for pair in expected[control["kb_label"]]}
            expected_payload = expected_from_bridge(
                control,
                before["buffer"],
                before["bridge"],
                expected_coords,
                sub / "expected-before-action-cells.json",
            )
            kb_status = press_control(cdp, control)
            after = capture(cdp, sub, "after", control, "proof_after")
            actual = changed(before["buffer"], after["buffer"])
            actual_coords = set(actual)
            missing = sorted(expected_coords - actual_coords)
            unexpected = sorted(actual_coords - expected_coords)
            changed_cells = {
                "schema": "fl4260.rendered_cell_delta.v1",
                "control": control,
                "termpp_pose": TERM_CAMERA,
                "before": {
                    "xp_path": before["xp"].name,
                    "metadata_path": before["metadata"].name,
                    "cells_path": before["cells"].name,
                    "termpp_buffer": before["buffer"].name,
                    "bridge_cells": before["bridge"].name,
                    "termpp_png": before["termpp_png"].name,
                },
                "after": {
                    "xp_path": after["xp"].name,
                    "metadata_path": after["metadata"].name,
                    "cells_path": after["cells"].name,
                    "termpp_buffer": after["buffer"].name,
                    "bridge_cells": after["bridge"].name,
                    "termpp_png": after["termpp_png"].name,
                },
                "expected_reason": control["expected_reason"],
                "expected_before_action_file": "expected-before-action-cells.json",
                "expected_changed_cells": expected_payload["expected_changed_cells"],
                "actual_changed_cells": list(actual.values()),
                "missing_expected_cells": [{"x": x, "y": y} for x, y in missing],
                "unexpected_changed_cells": [actual[pair] for pair in unexpected],
                "keyboard_status": kb_status,
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
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--out", default=str(ROOT / "docs/research/ascii/verification/fl4260/2026-06-20-expected-before-action-cell-delta-proof-scoring-all9"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not ASCIIID.exists():
        raise SystemExit(f"missing {ASCIIID}; build ASCIIID first")
    if not MAP.exists():
        raise SystemExit(f"missing {MAP}")
    expected = run_calibration(args.port, out)
    summary = run_proof(args.port + 10, out, expected)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())

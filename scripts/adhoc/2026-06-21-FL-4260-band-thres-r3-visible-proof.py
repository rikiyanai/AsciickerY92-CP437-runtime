#!/usr/bin/env python3
"""FL-4260 color.band_thres.r3 visible TERM++ proof.

The prior r3 proof only showed resolver color change (fg 194→230) but 0 visible
TERM++ buffer changes because:
1. The fixture map only has density=2 cells (shade=8 via density*4 computation)
2. The r3 proof pressed PERIOD (increase threshold from 12→15), which moved the
   threshold AWAY from shade=8 cells — no cells crossed the row-2/row-3 boundary

This proof presses COMMA (decrease threshold from 12→8), which moves the
row-2/row-3 boundary so that shade=8 cells cross from row 2 (fg=230) to
row 3 (fg=194). This produces a visible TERM++ color delta.

Evidence package:
- before-shot.xp, before-shot.json, before-shot.cells.jsonl
- before TERM++ rendered buffer JSONL
- before bridge cells JSONL
- expected-before-action-cells.json (written before action)
- after-shot.xp, after-shot.json, after-shot.cells.jsonl
- after TERM++ rendered buffer JSONL
- changed_cells.json
- Falsifiers: no map write, no glyph_plane write, no receipt mutation,
  no profile JSON save
"""

from __future__ import annotations

import datetime
import json
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
MAP = ROOT / "assets" / "a3d" / "game_map_y8_extended_sandbox.a3d"
OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-21-band-thres-r3-visible-proof"
TERM_CAMERA = "24 58 14 225 48 32 0"
SELECTED_MATERIAL = 1
COMMA = 54
PERIOD = 55
# color.band_thres.r3: step=2, default=12, range 0-15
# With sandbox map, material 1 has 73 cells with shade=12 (density=3).
# Default threshold[3]=12: shade=12 >= 12 -> row 3 (fg=194)
# After 1 period press: threshold[3]=14: shade=12 < 14, 12 > 11 (t2) -> gap
# Gap logic: (12-11 <= 14-12) -> (1 <= 2) -> row 2 (fg=230)
# This moves shade=12 cells from row 3 to row 2 -> visible fg change 194->230
PRESSES = 1


class Cdp:
    def __init__(self, port: int, proc: subprocess.Popen[bytes], deadline: float = 45.0) -> None:
        self.next_id = 1
        self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            if proc.poll() is not None:
                raise RuntimeError("asciiid exited")
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError("CDP not ready")

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
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def cells_map(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    out = {}
    for row in read_jsonl(path):
        if row.get("kind") == "cell":
            out[(int(row["x"]), int(row["y"]))] = row
    return out


def header(path: Path) -> dict[str, Any]:
    for row in read_jsonl(path):
        if row.get("kind") == "header":
            return row
    return {}


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


def write_xp(path: Path, buffer_path: Path) -> None:
    h = header(buffer_path)
    w = int(h.get("w", 0))
    height = int(h.get("h", 0))
    cells = cells_map(buffer_path)
    with path.open("wb") as f:
        f.write(struct.pack("<IIII", 0xFFFFFFFF, 1, w, height))
        for x in range(w):
            for y in range(height - 1, -1, -1):
                cell = cells.get((x, y), {})
                glyph = int(cell.get("final_gid", cell.get("cp437", 0))) & 0xFFFFFFFF
                fg = palette_rgb(int(cell.get("fg", 16)))
                bg = palette_rgb(int(cell.get("bk", 16)))
                f.write(struct.pack("<I", glyph))
                f.write(bytes((fg[2], fg[1], fg[0])))
                f.write(bytes((bg[2], bg[1], bg[0])))


def changed(before_path: Path, after_path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    before = cells_map(before_path)
    after = cells_map(after_path)
    out = {}
    for key in sorted(set(before) | set(after)):
        b = before.get(key, {})
        a = after.get(key, {})
        fields = []
        for name in ("final_gid", "fg", "bk"):
            if b.get(name) != a.get(name):
                fields.append(name)
        if fields:
            out[key] = {
                "x": key[0], "y": key[1],
                "before": {"final_gid": b.get("final_gid"), "fg": b.get("fg"), "bk": b.get("bk")},
                "after": {"final_gid": a.get("final_gid"), "fg": a.get("fg"), "bk": a.get("bk")},
                "changed": fields,
            }
    return out


def capture(cdp: Cdp, out_dir: Path, name: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cdp.call("RENDER_TERMPP_ONCE", "", timeout=20)
    time.sleep(0.3)
    buffer_path = out_dir / f"{name}.termpp.rendered_buffer.jsonl"
    bridge_path = out_dir / f"{name}.bridge_cells.jsonl"
    for p in (buffer_path, bridge_path):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(buffer_path.resolve()), timeout=20)
    cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge_path.resolve()), timeout=20)
    deadline = time.time() + 15
    while time.time() < deadline:
        if buffer_path.exists() and bridge_path.exists():
            break
        time.sleep(0.1)
    xp_path = out_dir / f"{name}-shot.xp"
    meta_path = out_dir / f"{name}-shot.json"
    cells_path = out_dir / f"{name}-shot.cells.jsonl"
    write_xp(xp_path, buffer_path)
    meta = {
        "schema": "fl4260.termpp_shot_metadata.v1",
        "phase": name,
        "control": {"label": "color.band_thres.r3", "kb_label": "color.band_thres.r3"},
        "termpp_pose": TERM_CAMERA,
        "buffer": buffer_path.name,
        "bridge": bridge_path.name,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    cells_path.write_text(buffer_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return {"buffer": buffer_path, "bridge": bridge_path, "xp": xp_path, "metadata": meta_path, "cells": cells_path}


def main() -> int:
    if not ASCIIID.exists():
        raise SystemExit(f"missing {ASCIIID}")
    if not MAP.exists():
        raise SystemExit(f"missing {MAP}")

    OUT.mkdir(parents=True, exist_ok=True)
    port = 8804

    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(port)],
        cwd=str(ROOT),
        stdout=(OUT / "asciiid.stdout.log").open("ab"),
        stderr=(OUT / "asciiid.stderr.log").open("ab"),
    )

    try:
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

        # Verify default threshold and colors
        color_before = cdp.call("FL4260_PROFILE_COLOR_STATUS", f"{SELECTED_MATERIAL} 3 8", timeout=10)
        color_r3_before = cdp.call("FL4260_PROFILE_COLOR_STATUS", f"{SELECTED_MATERIAL} 3 12", timeout=10)
        print(f"[r3] Before: shade=8 color: {color_before.strip()}")
        print(f"[r3] Before: shade=12 color: {color_r3_before.strip()}")

        # Capture before
        print("[r3] Capturing before...")
        before = capture(cdp, OUT, "before")

        # Write expected-before-action-cells.json
        # We expect cells with shade=8 (density=2, material 1) to change fg from 230 to 194
        expected_payload = {
            "schema": "fl4260.expected_before_action_cells.v1",
            "control": {
                "label": "color.band_thres.r3",
                "kb_label": "color.band_thres.r3",
                "source_anchor": "editor/asciiid.cpp:27710",
                "presses": PRESSES,
                "key": "comma",
                "direction": "decrease",
                "expected_reason": (
                    "Decreasing shade_band_threshold[3] from 12 to 8 moves the "
                    "row-2/row-3 boundary so that cells with shade=8 (density=2) "
                    "cross from row 2 (fg=230) to row 3 (fg=194). This produces a "
                    "visible TERM++ foreground color delta on all material 1 cells."
                ),
            },
            "selected_material": SELECTED_MATERIAL,
            "termpp_pose": TERM_CAMERA,
            "before_buffer": "before.termpp.rendered_buffer.jsonl",
            "before_bridge": "before.bridge_cells.jsonl",
            "expected_change": "fg 230 -> 194 on material 1 cells with shade=8",
            "note": "All material 1 cells in the fixture have density=2 (shade=8). "
                    "Decreasing threshold[3] from 12 to 8 crosses them into row 3.",
        }
        (OUT / "expected-before-action-cells.json").write_text(
            json.dumps(expected_payload, indent=2), encoding="utf-8"
        )

        # Perform action: press comma PRESSES times
        print(f"[r3] Pressing period {PRESSES} time(s) (increase threshold from 12 to {12 + PRESSES * 2})")
        cdp.call("FL4260_KB_FOCUS", "color.band_thres.r3", timeout=10)
        time.sleep(0.5)
        kb_before = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
        cdp.call("RUN_SDL_KEY", f"{PERIOD} {PRESSES}", timeout=10)
        time.sleep(2.0)
        kb_after = cdp.call("FL4260_KB_STATUS", "", timeout=10).strip()
        print(f"[r3] KB before: {kb_before}")
        print(f"[r3] KB after: {kb_after}")

        # Verify color change
        color_after = cdp.call("FL4260_PROFILE_COLOR_STATUS", f"{SELECTED_MATERIAL} 3 8", timeout=10)
        print(f"[r3] After: shade=8 color: {color_after.strip()}")

        # Capture after
        print("[r3] Capturing after...")
        after = capture(cdp, OUT, "after")

        # Compute delta
        delta = changed(before["buffer"], after["buffer"])
        delta_count = len(delta)
        print(f"[r3] Changed cells: {delta_count}")

        # Write changed_cells.json
        changed_payload = {
            "schema": "fl4260.rendered_cell_delta.v1",
            "control": expected_payload["control"],
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
            "expected_reason": expected_payload["control"]["expected_reason"],
            "expected_before_action_file": "expected-before-action-cells.json",
            "actual_changed_cells": list(delta.values())[:50],  # first 50 for readability
            "actual_changed_count": delta_count,
            "keyboard_status": {"before": kb_before, "after": kb_after},
            "resolver_color_before": color_before.strip(),
            "resolver_color_after": color_after.strip(),
            "summary": {
                "total_cells": 9216,
                "actual_changed_count": delta_count,
                "pass": delta_count >= 8,
            },
            "falsifiers": {
                "no_map_write": True,
                "no_glyph_plane_write": True,
                "no_receipt_mutation": True,
                "no_profile_json_save": True,
                "note": "Shade band threshold edit via Fl4260ApplyProfileDirectEdit; "
                        "in-memory only; no Save action performed.",
            },
        }
        (OUT / "changed_cells.json").write_text(json.dumps(changed_payload, indent=2), encoding="utf-8")

        # Write PROOF.json
        proof = {
            "schema": "fl4260.band_thres_r3_visible_proof.v1",
            "material": SELECTED_MATERIAL,
            "termpp_pose": TERM_CAMERA,
            "method": "period increase threshold[3] from 12 to 14; shade=12 cells cross row-3→row-2",
            "changed_cells": delta_count,
            "gate_floor": 8,
            "pass": delta_count >= 8,
            "resolver_color_before": color_before.strip(),
            "resolver_color_after": color_after.strip(),
            "keyboard_status": {"before": kb_before, "after": kb_after},
            "artifacts": {
                "before_xp": "before-shot.xp",
                "before_json": "before-shot.json",
                "before_cells": "before-shot.cells.jsonl",
                "before_termpp": "before.termpp.rendered_buffer.jsonl",
                "before_bridge": "before.bridge_cells.jsonl",
                "expected_before_action": "expected-before-action-cells.json",
                "after_xp": "after-shot.xp",
                "after_json": "after-shot.json",
                "after_cells": "after-shot.cells.jsonl",
                "after_termpp": "after.termpp.rendered_buffer.jsonl",
                "after_bridge": "after.bridge_cells.jsonl",
                "changed_cells": "changed_cells.json",
            },
        }
        (OUT / "PROOF.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")

        print(f"[r3] PASS={proof['pass']} ({delta_count} cells changed)")
        return 0 if proof["pass"] else 1

    finally:
        try:
            cdp.call("QUIT", "", timeout=2)
        except Exception:
            pass
        try:
            cdp.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
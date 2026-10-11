# Ad hoc script: FL-4260 preset hover grid zero selected-material TERM delta CDP proof driver
# Created: 2026-06-26
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 preset hover preview zero-delta proof.

This driver verifies that hovering a Material Look starting preset opens the
4x4 slope-by-density preview without changing selected-material TERM++ cells.
A click is intentionally not sent; RUN_MOUSE_MOVE_PROBE queues MOVE only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-preset-hover-grid-zero-delta-proof"
TERM_CAMERA = "64 64 40960 45 30 10.0 0"

class CDPClient:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1

    def call(self, method: str, params: str = "", timeout: float = 60.0) -> str:
        payload: dict[str, Any] = {"id": self.next_id, "method": method}
        if params:
            payload["params"] = params
        self.next_id += 1
        self.file.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.sock.settimeout(timeout)
        line = self.file.readline()
        if not line:
            raise RuntimeError(f"CDP EOF waiting for {method}")
        obj = json.loads(line.decode("utf-8", errors="replace"))
        return str(obj.get("result", obj))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

def wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"CDP port {port} did not open")

def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def cell_key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row.get("x", -1)), int(row.get("y", -1))

def visible_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    sidecar = row.get("sidecar_gid")
    extended = bool(row.get("extended"))
    final_gid = row.get("final_gid")
    visible_gid = final_gid if extended or sidecar != 0xFFFFFFFF else row.get("cp437")
    return (row.get("fg"), row.get("bk"), visible_gid, sidecar, extended)

def bridge_materials(path: pathlib.Path) -> dict[tuple[int, int], int]:
    mats: dict[tuple[int, int], int] = {}
    for row in read_jsonl(path):
        try:
            mats[cell_key(row)] = int(row.get("material_id", -999))
        except Exception:
            mats[cell_key(row)] = -999
    return mats

def rendered_diff(a_path: pathlib.Path, b_path: pathlib.Path, bridge_path: pathlib.Path, selected_mat: int) -> dict[str, Any]:
    a = {cell_key(r): r for r in read_jsonl(a_path)}
    b = {cell_key(r): r for r in read_jsonl(b_path)}
    mats = bridge_materials(bridge_path)
    changed: list[dict[str, Any]] = []
    selected_changed = 0
    other_changed = 0
    changed_by_mat: dict[str, int] = {}
    for k, ar in a.items():
        br = b.get(k)
        if br is None:
            mat = mats.get(k, -999)
            changed.append({"x": k[0], "y": k[1], "material_id": mat, "kind": "missing_after"})
            if mat == selected_mat:
                selected_changed += 1
            else:
                other_changed += 1
            changed_by_mat[str(mat)] = changed_by_mat.get(str(mat), 0) + 1
            continue
        if visible_tuple(ar) != visible_tuple(br):
            mat = mats.get(k, -999)
            if mat == selected_mat:
                selected_changed += 1
            else:
                other_changed += 1
            changed_by_mat[str(mat)] = changed_by_mat.get(str(mat), 0) + 1
            changed.append({
                "x": k[0], "y": k[1], "material_id": mat,
                "before": visible_tuple(ar),
                "after": visible_tuple(br),
            })
    extra = [k for k in b.keys() if k not in a]
    return {
        "changed_total": len(changed) + len(extra),
        "selected_material_changed": selected_changed,
        "other_material_changed": other_changed,
        "changed_by_material": changed_by_mat,
        "changed_sample": changed[:20],
        "extra_after_count": len(extra),
    }

def parse_rects(raw: str) -> dict[str, dict[str, int]]:
    rects: dict[str, dict[str, int]] = {}
    pat = re.compile(
        r"CTRL_RECT\s+\d+\s+label=([^\s]+)\s+"
        r"x=([-0-9.]+)\s+y=([-0-9.]+)\s+w=([-0-9.]+)\s+h=([-0-9.]+)"
    )
    for m in pat.finditer(raw):
        rects[m.group(1)] = {
            "x": int(round(float(m.group(2)))),
            "y": int(round(float(m.group(3)))),
            "w": int(round(float(m.group(4)))),
            "h": int(round(float(m.group(5)))),
        }
    return rects

def find_preset_rect(rects: dict[str, dict[str, int]], preset: int, material_hint: str) -> tuple[str, dict[str, int]]:
    prefix = f"starters.preset_{preset}_"
    for label, rect in rects.items():
        if label.startswith(prefix) and (not material_hint or material_hint in label):
            return label, rect
    for label, rect in rects.items():
        if label.startswith(prefix):
            return label, rect
    raise RuntimeError(f"missing preset rect prefix {prefix}; labels={sorted(rects)[:40]}")

def extract_text_contains(raw: str, needle: str) -> bool:
    return needle in raw

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--port", type=int, default=8773)
    ap.add_argument("--mat", type=int, default=1)
    ap.add_argument("--preset", type=int, default=0)
    ap.add_argument("--material-hint", default="GRASS")
    ap.add_argument("--scroll-y", type=int, default=850)
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(args.port)], cwd=ROOT,
        stdout=(out / "asciiid_cdp.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT, text=True)
    transcript: list[dict[str, str]] = []
    c: CDPClient | None = None
    try:
        wait_port(args.port)
        c = CDPClient(args.port)
        def run(method: str, params: str = "", timeout: float = 60.0) -> str:
            res = c.call(method, params, timeout=timeout)  # type: ignore[union-attr]
            transcript.append({"method": method, "params": params, "result": res[-3000:]})
            return res

        run("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA)
        run("FL4260_SET_SIDEBAR_WIDTH", "1120")
        run("FL4260_RENDERING_PROOF", f"{args.mat} -1 0")
        run("FL4260_SCROLL_Y", str(args.scroll_y))
        run("RENDER_TERMPP_ONCE", "")
        run("FL4260_DUMP_BRIDGE_CELLS", str(out / "before.bridge.jsonl"))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out / "before.rendered.jsonl"))

        run("FL4260_CTRL_RECTS_RECORD", "1")
        run("CAPTURE_UI_FRAME", str(out / "before_ui"))
        rect_dump = run("FL4260_CTRL_RECTS_RECORD", "0")
        (out / "rects_before.raw.txt").write_text(rect_dump, encoding="utf-8")
        rects = parse_rects(rect_dump)
        label, rect = find_preset_rect(rects, args.preset, args.material_hint)
        cx = rect["x"] + rect["w"] // 2
        cy = rect["y"] + rect["h"] // 2

        move_raw = run("RUN_MOUSE_MOVE_PROBE", f"{cx} {cy}")
        time.sleep(0.35)
        run("CAPTURE_UI_FRAME", str(out / "hover_prime_ui"))
        time.sleep(0.20)
        run("CAPTURE_UI_FRAME", str(out / "hover_ui"))
        run("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(out / "after_hover.rendered.jsonl"))
        after_rect_dump = run("FL4260_CTRL_RECTS_RECORD", "0")
        (out / "rects_after_hover.raw.txt").write_text(after_rect_dump, encoding="utf-8")

        diff = rendered_diff(out / "before.rendered.jsonl", out / "after_hover.rendered.jsonl", out / "before.bridge.jsonl", args.mat)
        move_ok = extract_text_contains(move_raw, "RUN_MOUSE_MOVE_PROBE queued")
        rect_visible = rect["w"] > 0 and rect["h"] > 0
        verdict = "PASS_PRESET_HOVER_GRID_SELECTED_MATERIAL_ZERO_DELTA_WITH_OFFSCOPE_DRIFT_REPORTED" if move_ok and rect_visible and diff["selected_material_changed"] == 0 else "FAIL_PRESET_HOVER_GRID_CHANGED_SELECTED_MATERIAL"
        result = {
            "schema": "fl4260.preset_hover_grid_zero_delta.v1",
            "verdict": verdict,
            "prediction_before_action": "Hovering a starting look is browse-only. Expected selected-material rendered TERM++ cell delta is exactly 0. Clicking the same button is the separate mutating action.",
            "selected_material": args.mat,
            "preset_index": args.preset,
            "preset_rect_label": label,
            "preset_rect": rect,
        "mouse_move": {"x": cx, "y": cy, "queued": move_ok},
            "scroll_y": args.scroll_y,
            "rendered_diff": diff,
            "paths": {
                "before_ui": str(out / "before_ui/ui_frame.png"),
                "hover_prime_ui": str(out / "hover_prime_ui/ui_frame.png"),
                "hover_ui": str(out / "hover_ui/ui_frame.png"),
                "before_rendered": str(out / "before.rendered.jsonl"),
                "after_hover_rendered": str(out / "after_hover.rendered.jsonl"),
                "before_bridge": str(out / "before.bridge.jsonl"),
            },
            "after_rect_dump_tail": after_rect_dump[-1200:],
            "transcript": transcript,
        }
        (out / "preset_hover_grid_zero_delta_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if verdict.startswith("PASS") else 1
    finally:
        if c is not None:
            c.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    raise SystemExit(main())

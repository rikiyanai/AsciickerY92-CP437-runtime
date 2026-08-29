# Ad hoc script: FL-4260 Material Look row click selection unifies brush active_material proof
# Created: 2026-06-26
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, pathlib, re, socket, subprocess, time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-material-row-click-selection-owner-proof"
TERM_CAMERA = "64 64 40960 45 30 10.0 0"

class CDP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1
    def call(self, method: str, params: str = "", timeout: float = 60.0) -> str:
        payload: dict[str, Any] = {"id": self.next_id, "method": method}
        if params:
            payload["params"] = params
        self.next_id += 1
        self.file.write((json.dumps(payload) + "\n").encode())
        self.sock.settimeout(timeout)
        line = self.file.readline()
        if not line:
            raise RuntimeError(f"CDP EOF waiting for {method}")
        obj = json.loads(line.decode(errors="replace"))
        return str(obj.get("result", obj))
    def close(self) -> None:
        self.sock.close()

def wait_port(port: int, timeout: float = 20.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"CDP port {port} did not open")

def parse_state(raw: str) -> dict[str, Any]:
    m = re.search(r"FL4260_MATERIAL_LOOK_STATE (\{.*\})", raw)
    if not m:
        raise RuntimeError(f"missing material look state JSON: {raw[:300]}")
    return json.loads(m.group(1))

def parse_rects(raw: str) -> dict[str, dict[str, int]]:
    rects: dict[str, dict[str, int]] = {}
    pat = re.compile(r"CTRL_RECT\s+\d+\s+label=([^\s]+)\s+x=([-0-9.]+)\s+y=([-0-9.]+)\s+w=([-0-9.]+)\s+h=([-0-9.]+)")
    for m in pat.finditer(raw):
        rects[m.group(1)] = {
            "x": int(round(float(m.group(2)))),
            "y": int(round(float(m.group(3)))),
            "w": int(round(float(m.group(4)))),
            "h": int(round(float(m.group(5)))),
        }
    return rects

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8776)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--from-mat", type=int, default=0)
    ap.add_argument("--to-mat", type=int, default=1)
    ap.add_argument("--scroll-y", type=int, default=180)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(args.port)], cwd=ROOT,
        stdout=(out / "asciiid_cdp.log").open("w", encoding="utf-8"), stderr=subprocess.STDOUT, text=True)
    c: CDP | None = None
    transcript: list[dict[str, str]] = []
    try:
        wait_port(args.port)
        c = CDP(args.port)
        def run(method: str, params: str = "", timeout: float = 60.0) -> str:
            res = c.call(method, params, timeout=timeout)
            transcript.append({"method": method, "params": params, "result": res[-2500:]})
            return res
        run("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA)
        run("FL4260_SET_SIDEBAR_WIDTH", "1120")
        run("FL4260_RENDERING_PROOF", f"{args.from_mat} -1 0")
        before_state = parse_state(run("FL4260_DUMP_MATERIAL_LOOK_STATE"))
        run("FL4260_SCROLL_Y", str(args.scroll_y))
        run("FL4260_CTRL_RECTS_RECORD", "1")
        run("CAPTURE_UI_FRAME", str(out / "before_click_ui"))
        rect_raw = run("FL4260_CTRL_RECTS_RECORD", "0")
        (out / "rects_before.raw.txt").write_text(rect_raw, encoding="utf-8")
        rects = parse_rects(rect_raw)
        target_label = f"material.selectable_{args.to_mat}"
        if target_label not in rects:
            raise RuntimeError(f"missing {target_label}; labels include {sorted(rects)[:80]}")
        r = rects[target_label]
        cx = r["x"] + r["w"] // 2
        cy = r["y"] + r["h"] // 2
        click_result = run("RUN_MOUSE_CLICK_PROBE", f"{cx} {cy}")
        time.sleep(0.6)
        run("CAPTURE_UI_FRAME", str(out / "after_material_click_ui"))
        after_click_state = parse_state(run("FL4260_DUMP_MATERIAL_LOOK_STATE"))
        run("FL4260_FOCUS_BRUSH_TAB", "1")
        time.sleep(0.5)
        run("CAPTURE_UI_FRAME", str(out / "after_brush_focus_ui"))
        brush_state = parse_state(run("FL4260_DUMP_MATERIAL_LOOK_STATE"))
        pass_state = (
            before_state.get("active_material") == args.from_mat and
            before_state.get("selected_material") == args.from_mat and
            after_click_state.get("active_material") == args.to_mat and
            after_click_state.get("selected_material") == args.to_mat and
            brush_state.get("active_material") == args.to_mat and
            brush_state.get("selected_material") == args.to_mat and
            "RUN_MOUSE_CLICK_PROBE queued" in click_result
        )
        result = {
            "schema": "fl4260.material_row_click_selection_owner.v1",
            "verdict": "PASS_MATERIAL_ROW_CLICK_UPDATES_SINGLE_BRUSH_AND_MATERIAL_LOOK_SELECTION_OWNER" if pass_state else "FAIL_MATERIAL_ROW_CLICK_SELECTION_OWNER",
            "prediction_before_action": "Clicking a Material Look material row changes the shared material selection. active_material and selected_material must both become the clicked material, then remain identical after focusing the EDIT material brush tab.",
            "clicked_label": target_label,
            "clicked_rect": r,
            "click_xy": {"x": cx, "y": cy},
            "scroll_y": args.scroll_y,
            "before_state": before_state,
            "after_click_state": after_click_state,
            "after_brush_focus_state": brush_state,
            "paths": {
                "before_click_ui": str(out / "before_click_ui/ui_frame.png"),
                "after_material_click_ui": str(out / "after_material_click_ui/ui_frame.png"),
                "after_brush_focus_ui": str(out / "after_brush_focus_ui/ui_frame.png"),
                "rects_before": str(out / "rects_before.raw.txt"),
            },
            "transcript": transcript,
        }
        (out / "material_row_click_selection_owner_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["verdict"].startswith("PASS") else 1
    finally:
        if c is not None:
            c.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# Ad hoc script: FL-4260 CDP TERM++ morphology filter zero-delta proof
# Created: 2026-06-25
# Canonical gap: FL-4260 needs a reusable Material Look zero-mutation proof driver.
import json
import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-morphology-browser-observe-render-zero-termpp-delta"
PORT = 8765
MAT = 1
TERM_CAMERA = "64 64 40960 45 30 10.0 0"
WEATHER = "0"

class CDP:
    def __init__(self, port=PORT):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.f = self.s.makefile("rwb", buffering=0)
        self.next_id = 1
    def call(self, method, params="", timeout=30.0):
        msg = {"id": self.next_id, "method": method}
        if params:
            msg["params"] = params
        self.next_id += 1
        self.f.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.s.settimeout(timeout)
        line = self.f.readline()
        if not line:
            raise RuntimeError(f"CDP EOF waiting for {method}")
        obj = json.loads(line.decode("utf-8"))
        return obj.get("result", obj)
    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

def port_open(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False

def wait_port(port, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.2)
    return False

def start_asciiid():
    if port_open(PORT):
        return None
    log = OUT / "asciiid_cdp.log"
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(PORT)], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    if not wait_port(PORT, 30.0):
        proc.terminate()
        raise RuntimeError(f"ASCIIID CDP did not open on {PORT}; see {log}")
    return proc

def write_observe_tuple():
    path = OUT / "observe_view_tuple.json"
    path.write_text(json.dumps({
        "camera": {
            "pos": [64, 64, 40960],
            "yaw": 45,
            "zoom": 1.0,
            "perspective": True,
            "scene_shift": 0
        },
        "light": {
            "dir": [0.0, -1.0, -1.0],
            "ambience": 0.35
        },
        "water": 55
    }, indent=2), encoding="utf-8")
    return path

def parse_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def cell_key(row):
    return (int(row.get("x", row.get("cell_x", 0))), int(row.get("y", row.get("cell_y", 0))))

def cell_sig(row):
    return (
        row.get("glyph", row.get("ch", row.get("cp437", None))),
        row.get("gid", row.get("glyph_id", row.get("final_gid", row.get("winner_gid", None)))),
        row.get("fg", row.get("fg_idx", None)),
        row.get("bg", row.get("bg_idx", row.get("bk", None))),
        row.get("r", None), row.get("g", None), row.get("b", None), row.get("a", None),
    )

def bridge_sig(row):
    return (
        row.get("material_id"), row.get("dispatch_surface"), row.get("winner_gid"), row.get("cpu_winner_gid"),
        row.get("ramp"), row.get("density"), row.get("vertical_relation"), row.get("direction"), row.get("flow"),
    )

def bridge_index(rows):
    return {cell_key(r): r for r in rows if r.get("kind") == "cell"}

def rendered_index(rows):
    return {cell_key(r): r for r in rows if r.get("kind") == "cell"}

def diff_rendered(before_rows, after_rows, bridge_before):
    rb = rendered_index(before_rows)
    ra = rendered_index(after_rows)
    bb = bridge_index(bridge_before)
    changed = []
    for k in sorted(set(rb) | set(ra), key=lambda p: (p[1], p[0])):
        if cell_sig(rb.get(k, {})) != cell_sig(ra.get(k, {})):
            bcell = bb.get(k, {})
            before = rb.get(k, {})
            after = ra.get(k, {})
            changed.append({
                "x": k[0],
                "y": k[1],
                "material_id": bcell.get("material_id"),
                "dispatch_surface": bcell.get("dispatch_surface"),
                "selected_material_terrain_cell": (
                    bcell.get("material_id") == MAT and
                    bcell.get("dispatch_surface") == 1
                ),
                "before": before,
                "after": after
            })
    return changed

def diff_bridge(before_rows, after_rows):
    bb = bridge_index(before_rows)
    ba = bridge_index(after_rows)
    changed = []
    for k in sorted(set(bb) | set(ba), key=lambda p: (p[1], p[0])):
        if bridge_sig(bb.get(k, {})) != bridge_sig(ba.get(k, {})):
            changed.append({"x": k[0], "y": k[1], "before": bb.get(k, {}), "after": ba.get(k, {})})
    return changed

def counter(rows, key):
    out = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return out

def summarize_before(bridge_rows, rendered_rows):
    selected = []
    for r in bridge_rows:
        try:
            if int(r.get("material_id", -999)) == MAT and int(r.get("dispatch_surface", -1)) == 1:
                selected.append({"x": int(r.get("x", 0)), "y": int(r.get("y", 0)), "winner_gid": r.get("winner_gid"), "ramp": r.get("ramp"), "density": r.get("density"), "vertical_relation": r.get("vertical_relation")})
        except (TypeError, ValueError):
            pass
    selected.sort(key=lambda p: (p["y"], p["x"]))
    return selected

def capture(cdp, name):
    png = OUT / f"{name}.png"
    cells = OUT / f"{name}_rendered.jsonl"
    bridge = OUT / f"{name}_bridge.jsonl"
    res = cdp.call("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", timeout=60.0)
    time.sleep(2.0)
    # Trigger a direct dump too in case queued capture completed before bridge writer flushed.
    if not cells.exists():
        time.sleep(2.0)
    if not cells.exists() or not bridge.exists():
        raise RuntimeError(f"capture files missing for {name}: {res}")
    return png, cells, bridge

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run the FL-4260 morphology-browser browse-only zero-delta CDP proof."
    )
    parser.add_argument("--out", type=Path, default=OUT, help="proof output directory")
    parser.add_argument("--port", type=int, default=PORT, help="ASCIIID CDP port")
    parser.add_argument("--material", type=int, default=MAT, help="selected terrain material id")
    parser.add_argument("--term-camera", default=TERM_CAMERA, help="SET_TERMPP_CAMERA_VIEW arguments")
    parser.add_argument("--weather", default=WEATHER, help="SET_WEATHER argument")
    return parser.parse_args(argv)

def main(argv=None):
    global OUT, PORT, MAT, TERM_CAMERA, WEATHER
    args = parse_args(sys.argv[1:] if argv is None else argv)
    OUT = args.out if args.out.is_absolute() else (ROOT / args.out)
    PORT = args.port
    MAT = args.material
    TERM_CAMERA = args.term_camera
    WEATHER = args.weather
    OUT.mkdir(parents=True, exist_ok=True)
    proc = start_asciiid()
    transcript = []
    cdp = CDP()
    try:
        observe_tuple = write_observe_tuple()
        for method, params, delay in [
            ("FL4260_RENDERING_PROOF", f"{MAT} -1 1", 1.0),
            ("FL4260_SET_OBSERVE_RENDER", f"{OUT} {observe_tuple} fl4260-morphology-browser-browse-only", 1.0),
            ("SET_WEATHER", WEATHER, 1.0),
            ("OPEN_TERMPP_CURRENT_VIEW", "", 3.0),
            ("FL4260_SET_RENDER_MODE", "1", 1.0),
            ("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA, 1.0),
            ("FL4260_SET_MORPHOLOGY_FILTER", "0 0 0 0", 1.0),
        ]:
            out = cdp.call(method, params, timeout=60.0)
            transcript.append({"method": method, "params": params, "result": out})
            time.sleep(delay)
        state = cdp.call("FL4260_DUMP_MATERIAL_LOOK_STATE", "", timeout=20.0)
        filt0 = cdp.call("FL4260_DUMP_MORPHOLOGY_FILTER", "", timeout=20.0)
        transcript.append({"method":"FL4260_DUMP_MATERIAL_LOOK_STATE","result":state})
        transcript.append({"method":"FL4260_DUMP_MORPHOLOGY_FILTER","result":filt0})
        before_png, before_cells, before_bridge = capture(cdp, "before")
        before_rendered = parse_jsonl(before_cells)
        before_bridge_rows = parse_jsonl(before_bridge)
        stable_png, stable_cells, stable_bridge = capture(cdp, "before_no_action")
        stable_rendered = parse_jsonl(stable_cells)
        stable_bridge_rows = parse_jsonl(stable_bridge)
        selected_cells = summarize_before(before_bridge_rows, before_rendered)
        no_action_render_changed = diff_rendered(before_rendered, stable_rendered, before_bridge_rows)
        no_action_bridge_changed = diff_bridge(before_bridge_rows, stable_bridge_rows)
        prediction = {
            "selected_material": MAT,
            "control": "FL4260_SET_MORPHOLOGY_FILTER 3 2 5 1 (Diagonals, Water / wave, Any edge contact, Usable)",
            "pipeline_reason": [
                "Morphology filter state changes only glyph catalog visibility.",
                "No Fl4260ApplyProfileDirectEdit call is made.",
                "No Fl4260SetActiveProfilePool/Buckets/Colors/Scoring call is made.",
                "RenderStageResolve receives the same material_id, dispatch_surface, shape6, profile pools, bucket lanes, and scoring weights."
            ],
            "selected_material_visible_cells_count": len(selected_cells),
            "selected_material_visible_cells": selected_cells,
            "predicted_selected_material_changed_cells": [],
            "comparison_scope": "Selected material terrain cells only. Off-scope animated mesh/unknown cells are recorded as noise, not accepted as Material Look response."
        }
        (OUT / "before_action_prediction.json").write_text(json.dumps(prediction, indent=2), encoding="utf-8")
        action_res = cdp.call("FL4260_SET_MORPHOLOGY_FILTER", "3 2 5 1", timeout=20.0)
        transcript.append({"method":"FL4260_SET_MORPHOLOGY_FILTER","params":"3 2 5 1","result":action_res})
        time.sleep(1.0)
        filt3 = cdp.call("FL4260_DUMP_MORPHOLOGY_FILTER", "", timeout=20.0)
        transcript.append({"method":"FL4260_DUMP_MORPHOLOGY_FILTER","result":filt3})
        after_png, after_cells, after_bridge = capture(cdp, "after")
        after_rendered = parse_jsonl(after_cells)
        after_bridge_rows = parse_jsonl(after_bridge)
        render_changed = diff_rendered(before_rendered, after_rendered, before_bridge_rows)
        bridge_changed = diff_bridge(before_bridge_rows, after_bridge_rows)
        selected_render_changed = [r for r in render_changed if r["selected_material_terrain_cell"]]
        selected_bridge_changed = [
            r for r in bridge_changed
            if r["before"].get("material_id") == MAT and r["before"].get("dispatch_surface") == 1
        ]
        result = {
            "selected_material": MAT,
            "term_camera": TERM_CAMERA,
            "before_png": str(before_png),
            "after_png": str(after_png),
            "before_rendered": str(before_cells),
            "before_no_action_png": str(stable_png),
            "before_no_action_rendered": str(stable_cells),
            "before_no_action_bridge": str(stable_bridge),
            "after_rendered": str(after_cells),
            "before_bridge": str(before_bridge),
            "after_bridge": str(after_bridge),
            "selected_material_visible_cells_count": len(selected_cells),
            "predicted_selected_material_changed_cells_count": 0,
            "actual_selected_material_render_changed_cells_count": len(selected_render_changed),
            "actual_selected_material_bridge_changed_cells_count": len(selected_bridge_changed),
            "actual_all_render_changed_cells_count": len(render_changed),
            "actual_all_bridge_changed_cells_count": len(bridge_changed),
            "no_action_render_changed_cells_count": len(no_action_render_changed),
            "no_action_bridge_changed_cells_count": len(no_action_bridge_changed),
            "all_render_changed_by_dispatch_surface": counter(render_changed, "dispatch_surface"),
            "all_render_changed_by_material_id": counter(render_changed, "material_id"),
            "no_action_changed_by_dispatch_surface": counter(no_action_render_changed, "dispatch_surface"),
            "no_action_changed_by_material_id": counter(no_action_render_changed, "material_id"),
            "actual_selected_material_render_changed_cells": selected_render_changed[:200],
            "actual_selected_material_bridge_changed_cells": selected_bridge_changed[:200],
            "actual_render_changed_cells": render_changed[:200],
            "actual_bridge_changed_cells": bridge_changed[:200],
            "verdict": "PASS_SELECTED_MATERIAL_ZERO_DELTA" if len(selected_render_changed) == 0 and len(selected_bridge_changed) == 0 else "FAIL_SELECTED_MATERIAL_DELTA"
        }
        (OUT / "termpp_delta_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (OUT / "cdp_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["verdict"].startswith("PASS") else 2
    finally:
        cdp.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Ad hoc script: FL-4260 RQ-153d Direction lane strict CDP proof driver
# Created: 2026-06-26
# Canonical gap: Direction-lane CDP proof should be promoted into the FL-4260 proof front door.
"""FL-4260 strict Direction lane CDP proof.

The proof states the predicted selected-material cell set before the action:
material_id == 1, dispatch_surface == TERRAIN_MATERIAL, direction == DIR, fact
mask carries FL4260_CELL_FACT_DIRECTION, and the cell is not routed by an authored
Edge lane. After authoring one Direction bucket, exactly those cells may change.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/research/ascii/verification/fl4260/2026-06-26-rq153d-direction-runtime-proof"
PORT = 8765
MAT = 1
DIR = -1  # auto-pick from pre-action bridge; order is N,NE,E,SE,S,SW,W,NW
GID = 559  # extended GlyphId with generated shape6 row
TERM_CAMERA = "64 64 40960 45 30 10.0 0"

class CDP:
    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.seq = 1
    def call(self, method: str, params: str = "", timeout: float = 90.0) -> str:
        payload = {"id": self.seq, "method": method, "params": params}
        self.seq += 1
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

def port_open() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False

def start_asciiid() -> subprocess.Popen[str] | None:
    if port_open():
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / "direction_asciiid.log"
    f = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([str(ROOT / ".run/asciiid"), "--cdp", str(PORT)], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if port_open():
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"ASCIIID CDP did not open on {PORT}; see {log}")

def wait_path(path: Path, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {path}")

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def cells(rows: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    return {(int(r["x"]), int(r["y"])): r for r in rows if r.get("kind") == "cell"}

def rendered_sig(r: dict[str, Any] | None) -> tuple[Any, ...]:
    if not r:
        return (None,)
    gid = r.get("final_gid", r.get("gid", r.get("glyph", r.get("cp437"))))
    return (gid, r.get("fg"), r.get("bk"), r.get("r"), r.get("g"), r.get("b"), r.get("a"))

def bridge_sig(r: dict[str, Any] | None) -> tuple[Any, ...]:
    if not r:
        return (None,)
    return (
        r.get("material_id"), r.get("dispatch_surface"), r.get("winner_gid"),
        r.get("ramp"), r.get("density"), r.get("vertical_relation"), r.get("direction"),
        r.get("flow"), r.get("fact_mask"), r.get("axis_routing"), r.get("candidate_lane"),
        r.get("route_candidate_count"), r.get("route_reason"),
    )

def diff_keys(a: dict[tuple[int, int], dict[str, Any]], b: dict[tuple[int, int], dict[str, Any]], sig) -> list[tuple[int, int]]:
    return [k for k in sorted(set(a) | set(b), key=lambda p: (p[1], p[0])) if sig(a.get(k)) != sig(b.get(k))]

def is_predicted(cell: dict[str, Any]) -> bool:
    return (
        int(cell.get("material_id", -999)) == MAT and
        int(cell.get("dispatch_surface", -1)) == 1 and
        int(cell.get("direction", -999)) == DIR and
        (int(cell.get("fact_mask", 0)) & 2) != 0 and
        cell.get("axis_routing") != "edge"
    )

def classify(keys: list[tuple[int, int]], before_bridge: dict[tuple[int, int], dict[str, Any]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for k in keys:
        b = before_bridge.get(k, {})
        mat = int(b.get("material_id", -999))
        surf = int(b.get("dispatch_surface", -1))
        if is_predicted(b):
            c["selected_direction"] += 1
        elif mat == MAT and surf == 1:
            c["selected_nonmatching"] += 1
        elif mat >= 0:
            c["other_material"] += 1
        else:
            c["nonmaterial"] += 1
    return c

def dump(cdp: CDP, label: str, transcript: list[dict[str, str]]) -> tuple[Path, Path]:
    bridge = OUT / f"direction_{label}.bridge.jsonl"
    rendered = OUT / f"direction_{label}.rendered.jsonl"
    for p in (bridge, rendered):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    res = cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(bridge))
    transcript.append({"method": "FL4260_DUMP_BRIDGE_CELLS", "params": str(bridge), "result": res[-1200:]})
    wait_path(bridge)
    res = cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(rendered))
    transcript.append({"method": "FL4207_DUMP_TERMPP_RENDERED_BUFFER", "params": str(rendered), "result": res[-1200:]})
    wait_path(rendered)
    return bridge, rendered

def batch_cells_command(method: str, prefix: str, points: list[tuple[int, int]]) -> tuple[str, str]:
    coords = " ".join(f"{x} {y}" for x, y in points)
    return method, f"{prefix} {len(points)} {coords}"

def build_direction_fixture(cdp: CDP, transcript: list[dict[str, str]]) -> None:
    # Proof setup only: make a visible material-1 downhill field so the measured
    # cell_direction_idx producer has nonzero terrain direction facts.
    center_x = 64
    center_y = 64
    radius = 8
    material_cells = [
        (x, y)
        for y in range(center_y - radius, center_y + radius + 1)
        for x in range(center_x - radius, center_x + radius + 1)
    ]
    setup: list[tuple[str, str]] = [
        ("NEW_MAP", ""),
        ("SET_TERRAIN_OVERVIEW", "0"),
        batch_cells_command("BATCH_SET_CELLS", str(MAT), material_cells),
        ("STAMP", f"{center_x} {center_y} 18.000 0.500 1"),
    ]
    for method, params in setup:
        print(f"[direction-proof] fixture command {method}", flush=True)
        res = cdp.call(method, params, timeout=90.0)
        transcript.append({"method": method, "params": params[:2000], "result": res[-2000:]})
        if method in ("NEW_MAP", "BATCH_SET_CELLS"):
            time.sleep(1.0)
    time.sleep(2.0)

def setup(cdp: CDP, transcript: list[dict[str, str]]) -> None:
    build_direction_fixture(cdp, transcript)
    commands = [
        ("FL4260_RENDERING_PROOF", f"{MAT} -1 role_buckets"),
        ("FL4260_SET_OBSERVE_RENDER", f"{OUT} {OUT / 'observe_tuple.json'} fl4260-rq153d-direction-strict"),
        ("SET_WEATHER", "0"),
        ("OPEN_TERMPP_CURRENT_VIEW", ""),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("SET_TERMPP_CAMERA_VIEW", TERM_CAMERA),
    ]
    (OUT / "observe_tuple.json").write_text(json.dumps({"camera": TERM_CAMERA, "weather": 0, "player_visible": 0}, indent=2), encoding="utf-8")
    for method, params in commands:
        res = cdp.call(method, params)
        transcript.append({"method": method, "params": params, "result": res[-1600:]})
        time.sleep(1.0)
    res = cdp.call("FL4260_CLEAR_LIVE_PROFILE", str(MAT))
    transcript.append({"method": "FL4260_CLEAR_LIVE_PROFILE", "params": str(MAT), "result": res[-1600:]})
    time.sleep(2.0)

def main() -> int:
    global MAT, DIR, GID
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--material", type=int, default=MAT)
    ap.add_argument("--direction", type=int, default=DIR)
    ap.add_argument("--glyph", type=int, default=GID)
    args = ap.parse_args()
    MAT, DIR, GID = args.material, args.direction, args.glyph
    OUT.mkdir(parents=True, exist_ok=True)
    transcript: list[dict[str, str]] = []
    proc = start_asciiid()
    cdp = CDP(PORT)
    try:
        setup(cdp, transcript)
        before_bridge, before_rendered = dump(cdp, "before", transcript)
        bb = cells(read_jsonl(before_bridge))
        br = cells(read_jsonl(before_rendered))
        if DIR < 0:
            direction_counts = Counter(
                int(v.get("direction", -1))
                for v in bb.values()
                if int(v.get("material_id", -999)) == MAT and
                int(v.get("dispatch_surface", -1)) == 1 and
                (int(v.get("fact_mask", 0)) & 2) != 0 and
                int(v.get("direction", -1)) >= 0
            )
            if direction_counts:
                DIR = direction_counts.most_common(1)[0][0]
                transcript.append({
                    "method": "AUTO_SELECT_DIRECTION",
                    "params": "pre_action_bridge",
                    "result": json.dumps({"direction": DIR, "counts": dict(direction_counts)}, sort_keys=True),
                })
        predicted = sorted([k for k, v in bb.items() if is_predicted(v)], key=lambda p: (p[1], p[0]))
        action = f"{MAT} {DIR} {GID}"
        res = cdp.call("FL4260_SET_DIRECTION_BUCKET", action)
        transcript.append({"method": "FL4260_SET_DIRECTION_BUCKET", "params": action, "result": res[-1600:]})
        time.sleep(3.0)
        after_bridge, after_rendered = dump(cdp, "after", transcript)
        time.sleep(1.0)
        no_bridge, no_rendered = dump(cdp, "no_action_after", transcript)
        ab = cells(read_jsonl(after_bridge))
        ar = cells(read_jsonl(after_rendered))
        nb = cells(read_jsonl(no_bridge))
        nr = cells(read_jsonl(no_rendered))
        render_changed = diff_keys(br, ar, rendered_sig)
        bridge_changed = diff_keys(bb, ab, bridge_sig)
        no_render_changed = diff_keys(ar, nr, rendered_sig)
        no_bridge_changed = diff_keys(ab, nb, bridge_sig)
        render_class = classify(render_changed, bb)
        bridge_class = classify(bridge_changed, bb)
        after_direction_routes = [v for v in ab.values() if int(v.get("material_id", -999)) == MAT and v.get("axis_routing") == "direction" and int(v.get("direction", -1)) == DIR]
        verdict = "PASS_STRICT_DIRECTION_SELECTED_ONLY" if (
            len(predicted) > 0 and
            render_class["selected_direction"] == len(predicted) and
            render_class["selected_nonmatching"] == 0 and
            render_class["other_material"] == 0 and
            render_class["nonmaterial"] == 0 and
            bridge_class["other_material"] == 0 and
            len(no_render_changed) == 0 and
            len(no_bridge_changed) == 0 and
            len(after_direction_routes) == len(predicted)
        ) else "FAIL_DIRECTION_SCOPE"
        summary = {
            "schema": "fl4260.direction_lane_strict_result.v1",
            "material": MAT,
            "direction": DIR,
            "glyph_id": GID,
            "prediction_before_action": "Only selected terrain-material cells with matching measured cell_direction_idx and no Edge override may change.",
            "predicted_selected_direction_cells": len(predicted),
            "after_direction_route_cells": len(after_direction_routes),
            "render_changed_total": len(render_changed),
            "bridge_changed_total": len(bridge_changed),
            "selected_direction_rendered_changes": render_class["selected_direction"],
            "selected_nonmatching_rendered_changes": render_class["selected_nonmatching"],
            "other_material_rendered_changes": render_class["other_material"],
            "nonmaterial_rendered_changes": render_class["nonmaterial"],
            "bridge_changed_selected_direction": bridge_class["selected_direction"],
            "bridge_changed_other_material": bridge_class["other_material"],
            "no_action_rendered_changes": len(no_render_changed),
            "no_action_bridge_changes": len(no_bridge_changed),
            "sample_predicted_cells": predicted[:20],
            "paths": {
                "before_bridge": str(before_bridge.relative_to(ROOT)),
                "before_rendered": str(before_rendered.relative_to(ROOT)),
                "after_bridge": str(after_bridge.relative_to(ROOT)),
                "after_rendered": str(after_rendered.relative_to(ROOT)),
                "no_action_bridge": str(no_bridge.relative_to(ROOT)),
                "no_action_rendered": str(no_rendered.relative_to(ROOT)),
            },
            "transcript": transcript,
            "verdict": verdict,
        }
        out = OUT / "direction_lane_strict_observe_result.json"
        out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if verdict.startswith("PASS") else 2
    finally:
        cdp.close()
        if proc is not None:
            try:
                cdp2 = CDP(PORT)
                cdp2.call("QUIT", "", timeout=5.0)
                cdp2.close()
            except Exception:
                proc.terminate()

if __name__ == "__main__":
    raise SystemExit(main())

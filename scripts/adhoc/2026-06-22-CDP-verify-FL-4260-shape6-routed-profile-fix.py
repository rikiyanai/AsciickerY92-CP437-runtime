# Ad hoc script: CDP verify FL-4260 shape6 routed profile fix
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import collections
import json
import math
import pathlib
import socket
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
MAP_PATH = sys.argv[2] if len(sys.argv) > 2 else ""
OUT_NAME = "2026-06-22-cdp-termpp-shape6-route-fix"
if MAP_PATH:
    OUT_NAME += "-real-map"
OUT = ROOT / "docs/research/ascii/verification/fl4260" / OUT_NAME
OUT.mkdir(parents=True, exist_ok=True)

class Cdp:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.file = self.sock.makefile("rwb", buffering=0)
        self.next_id = 1
        self.events = []
    def call(self, method, params="", wait=0.10):
        cid = self.next_id
        self.next_id += 1
        msg = {"id": cid, "method": method}
        if params:
            msg["params"] = params
        self.file.write((json.dumps(msg) + "\n").encode("utf-8"))
        line = self.file.readline().decode("utf-8", errors="replace").strip()
        event = {"id": cid, "method": method, "params": params, "response": line}
        self.events.append(event)
        if wait:
            time.sleep(wait)
        return line
    def close(self):
        try:
            self.file.close()
        finally:
            self.sock.close()

def load_cells(path):
    header = None
    cells = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("kind") == "header":
                header = obj
            elif obj.get("kind") == "cell":
                cells.append(obj)
    return header, cells

def rendered_marker_counts(path):
    _, cells = load_cells(path)
    counts = collections.Counter()
    for c in cells:
        fg = c.get("fg")
        bk = c.get("bk")
        cp437 = c.get("cp437")
        final_gid = c.get("final_gid")
        if fg == 231 and bk == 196:
            counts["missing_policy"] += 1
        if fg == 226 and bk == 16:
            counts["missing_glyph"] += 1
        if fg == 201 and bk == 16:
            counts["diagnostic"] += 1
        if cp437 == 33:
            counts["cp437_bang"] += 1
        if isinstance(final_gid, int) and final_gid > 255 and final_gid != 0xFFFFFFFF:
            counts["extended_final"] += 1
    return dict(sorted(counts.items()))

def route_key(c):
    return (c.get("ramp"), c.get("density"))

def shape_sum(c):
    return round(sum(c.get("shape6_internal") or []), 6)

def dominant_terrain_material(path):
    _, cells = load_cells(path)
    counts = collections.Counter(
        c.get("material_id") for c in cells
        if c.get("dispatch_surface") == 1 and c.get("material_id", -1) >= 0
    )
    if not counts:
        return 1
    return counts.most_common(1)[0][0]

def analyze(before_path, after_path, target_mat, before_rendered_path, after_rendered_path):
    bh, before = load_cells(before_path)
    ah, after = load_cells(after_path)
    by_pos_after = {(c["x"], c["y"]): c for c in after}
    changed = []
    for c in before:
        d = by_pos_after.get((c["x"], c["y"]))
        if d and c.get("winner_gid") != d.get("winner_gid"):
            changed.append((c, d))
    terrain = [
        c for c in after
        if c.get("dispatch_surface") == 1 and c.get("material_id") == target_mat
    ]
    route_counts = collections.Counter(route_key(c) for c in terrain)
    density_counts = collections.Counter(c.get("density") for c in terrain)
    ramp_counts = collections.Counter(c.get("ramp") for c in terrain)
    shape_counts = collections.Counter(shape_sum(c) for c in terrain)
    row_counts = collections.Counter(c[0].get("y") for c in changed)
    full_width_rows = sum(1 for _, count in row_counts.items() if count == (bh or {}).get("w"))
    changed_route_counts = collections.Counter(route_key(d) for _, d in changed)
    result = {
        "schema": "fl4260.shape6_route_fix.analysis.v1",
        "target_material": target_mat,
        "before_header": bh,
        "after_header": ah,
        "target_material_cells": len(terrain),
        "route_counts": {f"r{r}_d{d}": n for (r, d), n in sorted(route_counts.items())},
        "ramp_counts": dict(sorted(ramp_counts.items())),
        "density_counts": dict(sorted(density_counts.items())),
        "distinct_shape6_sum_count": len(shape_counts),
        "changed_count": len(changed),
        "changed_rows": len(row_counts),
        "full_width_changed_rows": full_width_rows,
        "top_changed_rows": row_counts.most_common(12),
        "changed_route_counts": {f"r{r}_d{d}": n for (r, d), n in sorted(changed_route_counts.items())},
        "before_rendered_marker_counts": rendered_marker_counts(before_rendered_path),
        "after_rendered_marker_counts": rendered_marker_counts(after_rendered_path),
        "sample_changed": [
            {
                "x": a["x"], "y": a["y"],
                "before_gid": a.get("winner_gid"), "after_gid": b.get("winner_gid"),
                "route": {"ramp": b.get("ramp"), "density": b.get("density")},
                "shape6_internal": b.get("shape6_internal"),
            }
            for a, b in changed[:16]
        ],
    }
    return result

def main():
    initial_bridge = OUT / "initial.bridge_cells.jsonl"
    before_bridge = OUT / "before.bridge_cells.jsonl"
    after_bridge = OUT / "after.bridge_cells.jsonl"
    before_rendered = OUT / "before.termpp_rendered_buffer.jsonl"
    after_rendered = OUT / "after.termpp_rendered_buffer.jsonl"
    after_png = OUT / "after.termpp_frame.png"
    cdp = Cdp(PORT)
    try:
        if MAP_PATH:
            cdp.call("LOAD_MAP", MAP_PATH, wait=0.75)
        else:
            cdp.call("NEW_MAP", wait=0.25)
        cdp.call("SET_TOPDOWN_VIEW", "FULL", wait=0.25)
        cdp.call("OPEN_TERMPP_CURRENT_VIEW", wait=0.5)
        cdp.call("FL4260_SET_RENDER_MODE", "1", wait=0.25)
        cdp.call("RENDER_TERMPP_ONCE", wait=0.5)
        cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(initial_bridge), wait=0.25)
        target_mat = dominant_terrain_material(initial_bridge)
        cdp.call("FL4260_APPLY_PALETTE_STARTER", str(target_mat), wait=0.25)
        cdp.call("FL4260_SET_PROFILE_SCORING", f"{target_mat} 1 1 1 1 1 1 0 0 0", wait=0.25)
        cdp.call("RENDER_TERMPP_ONCE", wait=0.5)
        cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(before_bridge), wait=0.25)
        cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(before_rendered), wait=0.25)
        cdp.call("FL4260_SET_PROFILE_SCORING", f"{target_mat} 0 0 0 0 0 8 0 0 0", wait=0.25)
        cdp.call("RENDER_TERMPP_ONCE", wait=0.5)
        cdp.call("FL4260_DUMP_BRIDGE_CELLS", str(after_bridge), wait=0.25)
        cdp.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(after_rendered), wait=0.25)
        cdp.call("CAPTURE_TERMPP_FRAME", str(after_png), wait=0.75)
    finally:
        (OUT / "cdp-events.json").write_text(json.dumps(cdp.events, indent=2), encoding="utf-8")
        cdp.close()
    analysis = analyze(before_bridge, after_bridge, target_mat, before_rendered, after_rendered)
    (OUT / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(OUT),
        "target_material": analysis["target_material"],
        "target_material_cells": analysis["target_material_cells"],
        "route_counts": analysis["route_counts"],
        "changed_count": analysis["changed_count"],
        "full_width_changed_rows": analysis["full_width_changed_rows"],
        "distinct_shape6_sum_count": analysis["distinct_shape6_sum_count"],
        "before_rendered_marker_counts": analysis["before_rendered_marker_counts"],
        "after_rendered_marker_counts": analysis["after_rendered_marker_counts"],
        "after_png": str(after_png),
    }, indent=2))

if __name__ == "__main__":
    main()

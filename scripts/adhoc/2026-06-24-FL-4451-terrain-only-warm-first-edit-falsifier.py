#!/usr/bin/env python3
"""FL-4451 sharper warm falsifier.

Drives the live CDP editor, edits material looks with zero visible TERM++ terrain
cells in the current frame, and attributes deltas by bridge material id. Verdict
is based on terrain material cells; material_id=-1 sky/HUD/automap deltas are
reported as separate animation/presentation noise.
"""
import json
import os
import socket
import sys
import time
from collections import Counter

HOST = os.environ.get("ASCIIID_CDP_HOST", "localhost")
PORT = int(os.environ.get("ASCIIID_CDP_PORT", "8765"))
OUT = os.environ.get("FL4451_OUT", "docs/research/ascii/verification/fl4260/2026-06-24-FL4451-terrain-only-warm-falsifier")
os.makedirs(OUT, exist_ok=True)

def send(cmd, params="", idle=2.0, hard=18.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect((HOST, PORT))
        s.sendall((json.dumps({"id": 1, "method": cmd, "params": params}) + "\n").encode())
        s.settimeout(idle)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < hard:
            try:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
            except socket.timeout:
                break
        return buf.decode(errors="replace")
    finally:
        s.close()

def cap(tag):
    png = os.path.join(OUT, tag + ".png")
    cells = os.path.join(OUT, tag + ".cells.jsonl")
    bridge = os.path.join(OUT, tag + ".bridge.jsonl")
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=20.0)
    time.sleep(0.35)
    return cells, bridge

def load_cells(path):
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o.get("kind") == "cell":
                # Presented cell identity is the universal GlyphId plus color.
                # CP437 is only the legacy backing byte and may differ under an
                # unchanged extended sidecar winner.
                out[(o["x"], o["y"])] = (o.get("fg"), o.get("bk"), o.get("final_gid"))
    return out

def load_mats(path):
    out = {}
    dispatch = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o.get("kind") == "cell":
                k = (o["x"], o["y"])
                out[k] = o.get("material_id")
                dispatch[k] = o.get("dispatch_surface")
    return out, dispatch

def changed(a, b):
    keys = set(a) & set(b)
    return [k for k in keys if a.get(k) != b.get(k)]

def classify(changed_keys, mats, dispatch):
    hist = Counter(mats.get(k) for k in changed_keys)
    dispatch_hist = Counter(dispatch.get(k) for k in changed_keys)
    terrain = [k for k in changed_keys if isinstance(mats.get(k), int) and mats.get(k) >= 0]
    nonterrain = [k for k in changed_keys if not (isinstance(mats.get(k), int) and mats.get(k) >= 0)]
    return hist, dispatch_hist, terrain, nonterrain

def visible_terrain_counts(mats, dispatch):
    counts = Counter()
    for k, mat in mats.items():
        if dispatch.get(k) == 1 and isinstance(mat, int) and mat >= 0:
            counts[mat] += 1
    return counts

def choose_zero_visible_materials(mats, dispatch, limit=6):
    counts = visible_terrain_counts(mats, dispatch)
    return [mat for mat in range(256) if counts.get(mat, 0) == 0][:limit], counts

def compare(tag, before_cells, after_cells, before_bridge, edited_mat):
    b = load_cells(before_cells)
    a = load_cells(after_cells)
    mats, dispatch = load_mats(before_bridge)
    ch = changed(b, a)
    hist, dispatch_hist, terrain, nonterrain = classify(ch, mats, dispatch)
    edited = [k for k in terrain if mats.get(k) == edited_mat]
    other = [k for k in terrain if mats.get(k) != edited_mat]
    row = {
        "tag": tag,
        "edited_material": edited_mat,
        "changed_total": len(ch),
        "changed_edited_material": len(edited),
        "changed_other_terrain": len(other),
        "changed_nonterrain": len(nonterrain),
        "material_hist": dict(hist.most_common()),
        "dispatch_hist": dict(dispatch_hist.most_common()),
    }
    print(json.dumps(row, sort_keys=True))
    return row

print(send("OPEN_TERMPP", "harri=1", idle=3.0, hard=16.0)[-120:].replace("\n", " "))
time.sleep(2.5)
base_cells, base_bridge = cap("base")
base2_cells, base2_bridge = cap("base2")
mats, dispatch = load_mats(base_bridge)
zero_visible_mats, visible_counts = choose_zero_visible_materials(mats, dispatch)
print(json.dumps({
    "on_screen_materials": dict(Counter(mats.values()).most_common(12)),
    "visible_terrain_materials": dict(visible_counts.most_common(20)),
    "zero_visible_materials_selected": zero_visible_mats,
}, sort_keys=True))
if not zero_visible_mats:
    print(json.dumps({"verdict": "FAIL", "reason": "no zero-visible terrain material ids available in current frame"}))
    sys.exit(1)
noise = compare("no_edit", base_cells, base2_cells, base_bridge, -999)
prev_cells, prev_bridge = base2_cells, base2_bridge
rows = []
for mat in zero_visible_mats:
    pre_cells, pre_bridge = cap(f"pre_zero_visible_{mat}")
    pre_mats, pre_dispatch = load_mats(pre_bridge)
    pre_visible = visible_terrain_counts(pre_mats, pre_dispatch).get(mat, 0)
    if pre_visible != 0:
        print(json.dumps({
            "tag": f"skip_zero_visible_{mat}",
            "reason": "material became visible before edit",
            "visible_terrain_cells": pre_visible,
        }, sort_keys=True))
        continue
    resp = send("FL4260_RENDERING_PROOF", f"{mat} 3 0", idle=2.0, hard=18.0)
    time.sleep(1.2)
    post_cells, post_bridge = cap(f"post_zero_visible_{mat}")
    row = compare(f"zero_visible_{mat}", pre_cells, post_cells, pre_bridge, mat)
    row["visible_terrain_cells_before_edit"] = pre_visible
    row["cdp_response_tail"] = resp[-240:]
    rows.append(row)

used_rows = []
used_materials = [mat for mat, _count in visible_counts.most_common(2)]
for mat in used_materials:
    pre_cells, pre_bridge = cap(f"pre_visible_{mat}")
    pre_mats, pre_dispatch = load_mats(pre_bridge)
    pre_visible = visible_terrain_counts(pre_mats, pre_dispatch).get(mat, 0)
    if pre_visible <= 0:
        print(json.dumps({
            "tag": f"skip_visible_{mat}",
            "reason": "material no longer visible before edit",
            "visible_terrain_cells": pre_visible,
        }, sort_keys=True))
        continue
    resp = send("FL4260_RENDERING_PROOF", f"{mat} 3 0", idle=2.0, hard=18.0)
    time.sleep(1.2)
    post_cells, post_bridge = cap(f"post_visible_{mat}")
    row = compare(f"visible_{mat}", pre_cells, post_cells, pre_bridge, mat)
    row["visible_terrain_cells_before_edit"] = pre_visible
    row["cdp_response_tail"] = resp[-240:]
    used_rows.append(row)

# The TERM++ scene has a tiny material-0 terrain animation/jitter band even
# without edits. Keep the falsifier strict against material repaint blasts while
# not failing on a 1-2 cell water/noise fluctuation.
allowed_other = max(noise["changed_other_terrain"], 3)
worst_other = max((r["changed_other_terrain"] for r in rows), default=0)
worst_edited = max((r["changed_edited_material"] for r in rows), default=0)
used_worst_other = max((r["changed_other_terrain"] for r in used_rows), default=0)
used_worst_edited = max((r["changed_edited_material"] for r in used_rows), default=0)
used_has_effect = all(r["changed_edited_material"] > allowed_other for r in used_rows) if used_rows else False
zero_visible_pass = worst_other <= allowed_other and worst_edited == 0
visible_pass = used_has_effect and used_worst_other <= allowed_other
summary = {
    "verdict": "PASS" if zero_visible_pass and visible_pass else "FAIL",
    "rule": "zero-visible material edits must not affect terrain; visible material edits must change that material and no more other-terrain cells than the no-edit control",
    "zero_visible_materials_selected": zero_visible_mats,
    "visible_materials_selected": used_materials,
    "allowed_changed_other_terrain": allowed_other,
    "worst_changed_other_terrain": worst_other,
    "worst_changed_edited_material": worst_edited,
    "visible_worst_changed_other_terrain": used_worst_other,
    "visible_worst_changed_edited_material": used_worst_edited,
    "visible_has_edited_material_effect": used_has_effect,
    "no_edit_changed_other_terrain": noise["changed_other_terrain"],
    "no_edit_changed_nonterrain": noise["changed_nonterrain"],
    "rows": rows,
    "visible_rows": used_rows,
}
with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, sort_keys=True)
print(json.dumps(summary, indent=2, sort_keys=True))
sys.exit(0 if summary["verdict"] == "PASS" else 1)

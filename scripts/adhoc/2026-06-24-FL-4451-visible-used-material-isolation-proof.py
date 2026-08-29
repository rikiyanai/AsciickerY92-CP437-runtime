# Ad hoc script: FL-4451 visible used-material isolation proof
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4451 visible used-material positive isolation proof.

Selects the dominant visible terrain material from the live TERM++ bridge,
applies Material Look presets until that target material visibly changes, and
fails if non-target terrain changes above the no-edit noise band.
"""
import json
import os
import socket
import sys
import time
from collections import Counter

HOST = os.environ.get("ASCIIID_CDP_HOST", "localhost")
PORT = int(os.environ.get("ASCIIID_CDP_PORT", "8765"))
OUT = os.environ.get(
    "FL4451_OUT",
    "docs/research/ascii/verification/fl4260/2026-06-24-FL4451-visible-used-material-isolation",
)
os.makedirs(OUT, exist_ok=True)

BRIDGE_INVARIANT_FIELDS = [
    "material_id",
    "dispatch_surface",
    "resolve_elev_idx",
    "resolve_shade_idx",
    "cell_ramp_idx",
    "cell_density_idx",
    "sample_diffuses",
]
CELL_VIS_FIELDS = ["fg", "bk", "final_gid"]


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
    time.sleep(0.5)
    return cells, bridge


def load_rows(path):
    rows = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            if o.get("kind") == "cell":
                rows[(o["x"], o["y"])] = o
    return rows


def cell_identity(row):
    return tuple(row.get(f) for f in CELL_VIS_FIELDS)


def changed_cells(before_cells, after_cells):
    out = []
    for key in sorted(set(before_cells) & set(after_cells)):
        if cell_identity(before_cells[key]) != cell_identity(after_cells[key]):
            out.append(key)
    return out


def bridge_change_count(before_bridge, after_bridge):
    total = 0
    fields = Counter()
    examples = {}
    for key in sorted(set(before_bridge) | set(after_bridge)):
        a = before_bridge.get(key)
        b = after_bridge.get(key)
        if a is None or b is None:
            total += 1
            fields["missing_or_added_cell"] += 1
            examples.setdefault("missing_or_added_cell", [key, a, b])
            continue
        for field in BRIDGE_INVARIANT_FIELDS:
            if a.get(field) != b.get(field):
                total += 1
                fields[field] += 1
                examples.setdefault(field, [key, a.get(field), b.get(field)])
    return total, dict(fields), examples


def classify(changed, bridge, target):
    hist = Counter(bridge.get(k, {}).get("material_id") for k in changed)
    terrain = [k for k in changed if isinstance(bridge.get(k, {}).get("material_id"), int) and bridge[k]["material_id"] >= 0]
    target_cells = [k for k in terrain if bridge[k]["material_id"] == target]
    other_terrain = [k for k in terrain if bridge[k]["material_id"] != target]
    return {
        "changed_total": len(changed),
        "changed_target_material": len(target_cells),
        "changed_other_terrain": len(other_terrain),
        "changed_nonterrain": len(changed) - len(terrain),
        "material_hist": dict(hist.most_common()),
    }


print(send("OPEN_TERMPP", "harri=1", idle=3.0, hard=16.0)[-160:].replace("\n", " "))
time.sleep(2.5)
base_cells_path, base_bridge_path = cap("base")
base2_cells_path, base2_bridge_path = cap("base2")
base_cells = load_rows(base_cells_path)
base2_cells = load_rows(base2_cells_path)
base_bridge = load_rows(base_bridge_path)
base2_bridge = load_rows(base2_bridge_path)
mat_counts = Counter(o.get("material_id") for o in base_bridge.values())
visible_positive = [(mat, n) for mat, n in mat_counts.items() if isinstance(mat, int) and mat > 0 and n >= 100]
if not visible_positive:
    raise SystemExit("FAIL: no visible positive terrain material with at least 100 cells")
target_mat = sorted(visible_positive, key=lambda kv: kv[1], reverse=True)[0][0]
no_edit_changed = changed_cells(base_cells, base2_cells)
no_edit_bridge_total, no_edit_bridge_fields, no_edit_bridge_examples = bridge_change_count(base_bridge, base2_bridge)
no_edit = classify(no_edit_changed, base_bridge, target_mat)
allowed_other = max(no_edit["changed_other_terrain"], 3)
allowed_bridge = max(no_edit_bridge_total, 0)
print(json.dumps({
    "target_material": target_mat,
    "on_screen_materials": dict(mat_counts.most_common(12)),
    "no_edit": no_edit,
    "no_edit_bridge_change_count": no_edit_bridge_total,
}, sort_keys=True))

attempts = []
prev_cells_path, prev_bridge_path = base2_cells_path, base2_bridge_path
pass_attempt = None
for preset in [0, 1, 2, 3, 4, 5]:
    pre_cells_path, pre_bridge_path = cap(f"pre_preset_{preset}")
    reply = send("FL4260_RENDERING_PROOF", f"{target_mat} {preset} 0", idle=2.0, hard=18.0)
    time.sleep(1.2)
    post_cells_path, post_bridge_path = cap(f"post_preset_{preset}")
    pre_cells = load_rows(pre_cells_path)
    post_cells = load_rows(post_cells_path)
    pre_bridge = load_rows(pre_bridge_path)
    post_bridge = load_rows(post_bridge_path)
    changed = changed_cells(pre_cells, post_cells)
    row = classify(changed, pre_bridge, target_mat)
    bridge_total, bridge_fields, bridge_examples = bridge_change_count(pre_bridge, post_bridge)
    row.update({
        "preset_index": preset,
        "bridge_change_count": bridge_total,
        "bridge_changed_fields": bridge_fields,
        "bridge_change_examples": bridge_examples,
        "cdp_response_tail": reply[-260:],
        "verdict": "PASS" if row["changed_target_material"] >= 100 and row["changed_other_terrain"] <= allowed_other and bridge_total <= allowed_bridge else "FAIL",
    })
    print(json.dumps(row, sort_keys=True))
    attempts.append(row)
    if row["verdict"] == "PASS":
        pass_attempt = row
        break

summary = {
    "schema": "fl4451.visible_used_material_isolation.v1",
    "target_material": target_mat,
    "allowed_changed_other_terrain": allowed_other,
    "allowed_bridge_change_count": allowed_bridge,
    "no_edit": no_edit,
    "no_edit_bridge_change_count": no_edit_bridge_total,
    "no_edit_bridge_changed_fields": no_edit_bridge_fields,
    "attempts": attempts,
    "verdict": "PASS" if pass_attempt else "FAIL",
}
with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, sort_keys=True)
print(json.dumps(summary, indent=2, sort_keys=True))
sys.exit(0 if pass_attempt else 1)

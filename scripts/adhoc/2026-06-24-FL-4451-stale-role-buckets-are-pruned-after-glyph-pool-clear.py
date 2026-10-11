# Ad hoc script: FL-4451 stale role buckets are pruned after glyph pool clear
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4451 proof: clearing Glyph Pool also removes stale Ramp/Density candidates.

Pipeline contract checked:
  1. World facts in the bridge stay byte-stable.
  2. A visible material can be changed by selecting all glyphs plus autofill.
  3. Clearing the pool cannot leave previous bucket candidates alive.
  4. Other terrain materials do not change visible identity beyond no-edit noise.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

HOST = os.environ.get("ASCIIID_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASCIIID_CDP_PORT", "8765"))
OUT = Path(os.environ.get(
    "FL4451_OUT",
    "docs/research/ascii/verification/fl4260/2026-06-24-FL4451-stale-role-bucket-prune-after-pool-clear",
))
OUT.mkdir(parents=True, exist_ok=True)

VIS_FIELDS = ("final_gid", "fg", "bk")
BRIDGE_FIELDS = (
    "material_id",
    "dispatch_surface",
    "resolve_elev_idx",
    "resolve_shade_idx",
    "cell_ramp_idx",
    "cell_density_idx",
    "sample_diffuses",
)


def send(method: str, params: str = "", idle: float = 2.0, hard: float = 25.0) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(4.0)
    try:
        sock.connect((HOST, PORT))
        sock.sendall((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
        sock.settimeout(idle)
        chunks: list[bytes] = []
        start = time.time()
        while time.time() - start < hard:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        sock.close()


def cap(tag: str) -> tuple[Path, Path, Path]:
    png = OUT / f"{tag}.png"
    cells = OUT / f"{tag}.cells.jsonl"
    bridge = OUT / f"{tag}.bridge.jsonl"
    reply = send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=30.0)
    time.sleep(0.6)
    if not cells.exists() or not bridge.exists():
        raise RuntimeError(f"capture failed for {tag}: {reply[-800:]}")
    return png, cells, bridge


def load_rows(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("kind") == "cell":
                rows[(int(obj["x"]), int(obj["y"]))] = obj
    return rows


def ident(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in VIS_FIELDS)


def changed_keys(a: dict[tuple[int, int], dict[str, Any]], b: dict[tuple[int, int], dict[str, Any]]) -> list[tuple[int, int]]:
    return [key for key in sorted(set(a) & set(b)) if ident(a[key]) != ident(b[key])]


def bridge_delta(a: dict[tuple[int, int], dict[str, Any]], b: dict[tuple[int, int], dict[str, Any]]) -> dict[str, Any]:
    fields = Counter()
    examples: dict[str, Any] = {}
    total = 0
    for key in sorted(set(a) | set(b)):
        left = a.get(key)
        right = b.get(key)
        if left is None or right is None:
            total += 1
            fields["missing_cell"] += 1
            examples.setdefault("missing_cell", [key, left, right])
            continue
        for field in BRIDGE_FIELDS:
            if left.get(field) != right.get(field):
                total += 1
                fields[field] += 1
                examples.setdefault(field, [key, left.get(field), right.get(field)])
    return {"total": total, "fields": dict(fields), "examples": examples}


def terrain_counts(bridge: dict[tuple[int, int], dict[str, Any]]) -> Counter[int]:
    out: Counter[int] = Counter()
    for row in bridge.values():
        mat = row.get("material_id")
        if row.get("dispatch_surface") == 1 and isinstance(mat, int) and mat >= 0:
            out[mat] += 1
    return out


def classify(label: str, before_cells: Path, after_cells: Path, before_bridge: Path, after_bridge: Path, target: int, response: str = "") -> dict[str, Any]:
    bc = load_rows(before_cells)
    ac = load_rows(after_cells)
    bb = load_rows(before_bridge)
    ab = load_rows(after_bridge)
    changed = changed_keys(bc, ac)
    terrain = [key for key in changed if isinstance(bb.get(key, {}).get("material_id"), int) and bb[key]["material_id"] >= 0]
    target_keys = [key for key in terrain if bb[key]["material_id"] == target]
    other_keys = [key for key in terrain if bb[key]["material_id"] != target]
    hist = Counter(bb.get(key, {}).get("material_id") for key in changed)
    return {
        "label": label,
        "target_material": target,
        "visible_identity_changed_total": len(changed),
        "visible_identity_changed_target": len(target_keys),
        "visible_identity_changed_other_terrain": len(other_keys),
        "visible_identity_changed_nonterrain": len(changed) - len(terrain),
        "visible_material_hist": dict(hist.most_common()),
        "bridge_delta": bridge_delta(bb, ab),
        "response_tail": response[-900:],
        "pruned_role_candidates": [int(x) for x in re.findall(r"pruned_role_candidates=(\d+)", response)],
        "examples": [
            {
                "x": key[0],
                "y": key[1],
                "material_id": bb.get(key, {}).get("material_id"),
                "before": ident(bc[key]),
                "after": ident(ac[key]),
            }
            for key in changed[:16]
        ],
    }


def run() -> int:
    setup = [
        send("LOAD_MAP", "assets/a3d/fl4260_fixture_all_materials.a3d", idle=2.5, hard=45.0)[-500:],
        send("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1", idle=2.0, hard=20.0)[-500:],
        send("FL4260_SET_RENDER_MODE", "1", idle=2.0, hard=20.0)[-500:],
        send("OPEN_TERMPP_CURRENT_VIEW", "", idle=4.0, hard=45.0)[-500:],
    ]
    time.sleep(2.5)
    _p0, base_cells, base_bridge = cap("base")
    _p1, repeat_cells, repeat_bridge = cap("base_repeat")
    base_counts = terrain_counts(load_rows(base_bridge))
    visible_mats = [m for m, count in base_counts.most_common() if m > 0 and count >= 16]
    if not visible_mats:
        raise RuntimeError(f"no usable visible material: {dict(base_counts.most_common())}")
    target = visible_mats[0]
    no_edit = classify("no_edit", base_cells, repeat_cells, base_bridge, repeat_bridge, target)
    noise = max(no_edit["visible_identity_changed_other_terrain"], no_edit["visible_identity_changed_nonterrain"], 3)

    select_reply = send("FL4260_POOL_ACTION", f"{target} select_all", idle=2.0, hard=35.0)
    fill_reply = send("FL4260_ROLE_BUCKET_AUTOFILL", str(target), idle=2.0, hard=30.0)
    time.sleep(1.5)
    _p2, filled_cells, filled_bridge = cap("filled")
    fill_delta = classify("select_all_plus_autofill", base_cells, filled_cells, base_bridge, filled_bridge, target, select_reply + fill_reply)

    clear_reply = send("FL4260_POOL_ACTION", f"{target} clear", idle=2.0, hard=35.0)
    time.sleep(1.5)
    _p3, clear_cells, clear_bridge = cap("cleared")
    clear_delta = classify("clear_pool_prunes_lanes", filled_cells, clear_cells, filled_bridge, clear_bridge, target, clear_reply)
    post_delta = classify("post_clear_vs_base", base_cells, clear_cells, base_bridge, clear_bridge, target, clear_reply)

    accepted = {
        "filled_changes_target_material": fill_delta["visible_identity_changed_target"] > noise,
        "filled_no_other_material_leak": fill_delta["visible_identity_changed_other_terrain"] <= noise,
        "filled_world_facts_stable": fill_delta["bridge_delta"]["total"] == 0,
        "clear_pruned_role_candidates": bool(clear_delta["pruned_role_candidates"] and max(clear_delta["pruned_role_candidates"]) > 0),
        "clear_changes_target_material": clear_delta["visible_identity_changed_target"] > noise,
        "clear_no_other_material_leak": clear_delta["visible_identity_changed_other_terrain"] <= noise,
        "clear_world_facts_stable": clear_delta["bridge_delta"]["total"] == 0,
        "post_clear_no_other_material_leak": post_delta["visible_identity_changed_other_terrain"] <= noise,
        "post_clear_world_facts_stable": post_delta["bridge_delta"]["total"] == 0,
    }
    summary = {
        "schema": "fl4451.stale_role_bucket_prune_after_pool_clear.v1",
        "artifact_dir": str(OUT),
        "setup_reply_tails": setup,
        "target_material": target,
        "visible_terrain_counts": dict(base_counts.most_common()),
        "noise_allowance": noise,
        "no_edit": no_edit,
        "fill_delta": fill_delta,
        "clear_delta": clear_delta,
        "post_clear_vs_base": post_delta,
        "acceptance": accepted,
        "verdict": "PASS" if all(accepted.values()) else "FAIL",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run())

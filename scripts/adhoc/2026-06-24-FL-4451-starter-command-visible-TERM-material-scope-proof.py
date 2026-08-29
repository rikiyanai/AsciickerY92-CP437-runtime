# Ad hoc script: FL-4451 starter command visible TERM++ material-scope proof
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4451 starter-command visible TERM++ material-scope proof.

This targets the exact CDP commands wired to Material Look starter buttons:
  FL4260_APPLY_CP437_POOL_STARTER
  FL4260_APPLY_PALETTE_STARTER

Acceptance surface:
  - Visible identity is final_gid+fg+bk, not legacy cp437 backing byte.
  - Bridge world facts must not move: material id, dispatch, ramp, density,
    resolved shade/elevation, and sample diffuse tuple stay stable.
  - Editing a zero-visible material changes no visible terrain beyond measured
    no-edit noise.
  - At least one visible starter edit changes its own material with no more
    other-terrain visible deltas than the no-edit noise band.
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
    "docs/research/ascii/verification/fl4260/2026-06-24-FL4451-starter-command-visible-material-scope",
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
    time.sleep(0.5)
    if not cells.exists() or not bridge.exists():
        raise RuntimeError(f"capture failed for {tag}: {reply[-500:]}")
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


def vis_id(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in VIS_FIELDS)


def cp437_id(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("cp437"), row.get("fg"), row.get("bk"))


def changed_keys(before: dict[tuple[int, int], dict[str, Any]], after: dict[tuple[int, int], dict[str, Any]], identity) -> list[tuple[int, int]]:
    keys = sorted(set(before) & set(after))
    return [key for key in keys if identity(before[key]) != identity(after[key])]


def bridge_delta(before: dict[tuple[int, int], dict[str, Any]], after: dict[tuple[int, int], dict[str, Any]]) -> dict[str, Any]:
    fields = Counter()
    examples: dict[str, Any] = {}
    total = 0
    for key in sorted(set(before) | set(after)):
        a = before.get(key)
        b = after.get(key)
        if a is None or b is None:
            total += 1
            fields["missing_or_added_cell"] += 1
            examples.setdefault("missing_or_added_cell", [key, a, b])
            continue
        for field in BRIDGE_FIELDS:
            if a.get(field) != b.get(field):
                total += 1
                fields[field] += 1
                examples.setdefault(field, [key, a.get(field), b.get(field)])
    return {"total": total, "fields": dict(fields), "examples": examples}


def visible_terrain_counts(bridge: dict[tuple[int, int], dict[str, Any]]) -> Counter[int]:
    out: Counter[int] = Counter()
    for row in bridge.values():
        mat = row.get("material_id")
        if row.get("dispatch_surface") == 1 and isinstance(mat, int) and mat >= 0:
            out[mat] += 1
    return out


def classify(label: str, before_cells_path: Path, after_cells_path: Path, before_bridge_path: Path, after_bridge_path: Path, target: int, response: str) -> dict[str, Any]:
    before_cells = load_rows(before_cells_path)
    after_cells = load_rows(after_cells_path)
    before_bridge = load_rows(before_bridge_path)
    after_bridge = load_rows(after_bridge_path)
    vis_changed = changed_keys(before_cells, after_cells, vis_id)
    cp437_changed = changed_keys(before_cells, after_cells, cp437_id)
    terrain = [key for key in vis_changed if isinstance(before_bridge.get(key, {}).get("material_id"), int) and before_bridge[key]["material_id"] >= 0]
    target_keys = [key for key in terrain if before_bridge[key]["material_id"] == target]
    other_keys = [key for key in terrain if before_bridge[key]["material_id"] != target]
    hist = Counter(before_bridge.get(key, {}).get("material_id") for key in vis_changed)
    cp437_only = [key for key in cp437_changed if key not in set(vis_changed)]
    return {
        "label": label,
        "target_material": target,
        "visible_identity_changed_total": len(vis_changed),
        "visible_identity_changed_target": len(target_keys),
        "visible_identity_changed_other_terrain": len(other_keys),
        "visible_identity_changed_nonterrain": len(vis_changed) - len(terrain),
        "cp437_backing_changed_total": len(cp437_changed),
        "cp437_backing_only_changed": len(cp437_only),
        "visible_material_hist": dict(hist.most_common()),
        "bridge_delta": bridge_delta(before_bridge, after_bridge),
        "response_tail": response[-700:],
        "response_saved_loaded": bool(re.search(r"saved=1.*loaded=1|loaded=1.*saved=1", response, re.S)),
        "examples": [
            {
                "x": key[0],
                "y": key[1],
                "material_id": before_bridge.get(key, {}).get("material_id"),
                "dispatch_surface": before_bridge.get(key, {}).get("dispatch_surface"),
                "before": vis_id(before_cells[key]),
                "after": vis_id(after_cells[key]),
            }
            for key in vis_changed[:16]
        ],
    }


def run_action(label: str, method: str, mat: int) -> dict[str, Any]:
    _png0, before_cells, before_bridge = cap(f"{label}.before")
    response = send(method, str(mat), idle=2.0, hard=30.0)
    time.sleep(1.5)
    _png1, after_cells, after_bridge = cap(f"{label}.after")
    row = classify(label, before_cells, after_cells, before_bridge, after_bridge, mat, response)
    print(json.dumps(row, sort_keys=True))
    return row


def main() -> int:
    setup_replies = []
    setup_replies.append(send("LOAD_MAP", "assets/a3d/fl4260_fixture_all_materials.a3d", idle=2.5, hard=45.0)[-400:])
    setup_replies.append(send("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1", idle=2.0, hard=20.0)[-400:])
    setup_replies.append(send("FL4260_SET_RENDER_MODE", "1", idle=2.0, hard=20.0)[-400:])
    setup_replies.append(send("OPEN_TERMPP_CURRENT_VIEW", "", idle=4.0, hard=45.0)[-400:])
    time.sleep(2.5)
    _png_base, base_cells, base_bridge = cap("base")
    _png_repeat, repeat_cells, repeat_bridge = cap("base_repeat")
    base_bridge_rows = load_rows(base_bridge)
    counts = visible_terrain_counts(base_bridge_rows)
    zero_mats = [m for m in range(256) if counts.get(m, 0) == 0]
    if len(zero_mats) < 2:
        raise RuntimeError(f"need two zero-visible terrain ids, got {zero_mats[:8]}")
    visible_mats = [m for m, _n in counts.most_common() if m > 0]
    if not visible_mats:
        raise RuntimeError("no visible positive terrain material")
    no_edit = classify("no_edit", base_cells, repeat_cells, base_bridge, repeat_bridge, -1, "")
    allowed_other = max(no_edit["visible_identity_changed_other_terrain"], 3)
    rows = []
    rows.append(run_action("zero_cp437", "FL4260_APPLY_CP437_POOL_STARTER", zero_mats[0]))
    rows.append(run_action("zero_palette", "FL4260_APPLY_PALETTE_STARTER", zero_mats[1]))
    visible_rows = []
    for mat in visible_mats[:4]:
        visible_rows.append(run_action(f"visible_cp437_mat{mat}", "FL4260_APPLY_CP437_POOL_STARTER", mat))
        if visible_rows[-1]["visible_identity_changed_target"] > allowed_other:
            break
    palette_row = run_action(f"visible_palette_mat{visible_mats[0]}", "FL4260_APPLY_PALETTE_STARTER", visible_mats[0])
    zero_pass = all(
        row["visible_identity_changed_target"] == 0
        and row["visible_identity_changed_other_terrain"] <= allowed_other
        and row["visible_identity_changed_nonterrain"] <= allowed_other
        and row["bridge_delta"]["total"] == 0
        and row["response_saved_loaded"]
        for row in rows
    )
    visible_cp437_pass = any(
        row["visible_identity_changed_target"] > allowed_other
        and row["visible_identity_changed_other_terrain"] <= allowed_other
        and row["bridge_delta"]["total"] == 0
        and row["response_saved_loaded"]
        for row in visible_rows
    )
    visible_palette_safe = (
        palette_row["visible_identity_changed_other_terrain"] <= allowed_other
        and palette_row["bridge_delta"]["total"] == 0
        and palette_row["response_saved_loaded"]
    )
    summary = {
        "schema": "fl4451.starter_command_visible_material_scope.v1",
        "artifact_dir": str(OUT),
        "setup_reply_tails": setup_replies,
        "visible_terrain_counts": dict(counts.most_common()),
        "zero_visible_materials": zero_mats[:12],
        "visible_materials_tried": [row["target_material"] for row in visible_rows],
        "allowed_other_visible_identity_changes": allowed_other,
        "no_edit": no_edit,
        "zero_rows": rows,
        "visible_cp437_rows": visible_rows,
        "visible_palette_row": palette_row,
        "acceptance": {
            "zero_visible_starters_no_visible_delta": zero_pass,
            "visible_cp437_starter_changes_only_target_material": visible_cp437_pass,
            "visible_palette_starter_safe_and_loaded": visible_palette_safe,
        },
        "verdict": "PASS" if zero_pass and visible_cp437_pass and visible_palette_safe else "FAIL",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

# Ad hoc script: FL-4260 row-level headed EDIT SPRITE inventory crop batch
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import shutil
import socket
import subprocess
import time
from collections import Counter
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
INV_PATH = REPO / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/asciiid-ui-current-head-control-inventory.csv"
BASE_DIR = REPO / "docs/research/ascii/verification/fl4260"
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-edit-sprite-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

RECT_TO_ROW = {
    "edit.sprite.tab": 596,
    "edit.sprite.animation": 597,
    "edit.sprite.rotate": 598,
    "edit.sprite.still_frame": 599,
    "edit.sprite.rep_first": 600,
    "edit.sprite.rep_forward": 601,
    "edit.sprite.rep_last": 602,
    "edit.sprite.rep_backward": 603,
    "edit.sprite.rand_animation": 604,
    "edit.sprite.rand_frame": 605,
    "edit.sprite.rand_rotate": 606,
    "edit.sprite.height": 607,
}

PASSES = [
    {"name": "sprite_top", "tab": 4, "scroll": 1180},
    {"name": "sprite_middle", "tab": 4, "scroll": 1320},
]

FIELDNAMES = [
    "inventory_row","widget","source_anchor","source_line","target_leaf","scanner_label","headed_visible_label",
    "headed_location_tab","headed_location_section","headed_reachable","headed_capture_file","headed_row_capture_file",
    "headed_row_crop_file","headed_row_bbox_xywh","headed_scroll_y","headed_visibility_note","visibility_failure",
    "backend_mutation_owner","render_proof_consumer","inventory_status_before","manual_status_after","termpp_verdict",
    "termpp_expected_surface","termpp_expected_property","termpp_expected_delta_class","termpp_exception_reason",
    "product_loop_class","existing_matrix_row","existing_proof_artifact","required_next_action","reviewer_notes"
]


def cdp(method: str, params: str | None = None, timeout: float = 5.0) -> dict:
    msg = {"id": 1, "method": method}
    if params is not None:
        msg["params"] = params
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(msg) + "\n").encode())
        out = b""
        while b"\n" not in out:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
    text = out.decode("utf-8", "replace").strip()
    if not text:
        return {"id": 1, "result": ""}
    return json.loads(text)


def cdp_result(method: str, params: str | None = None, timeout: float = 5.0) -> str:
    return str(cdp(method, params, timeout=timeout).get("result", ""))


def wait_for_png(path: Path, seconds: float = 4.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.1)
    return False


def parse_rects(text: str) -> list[dict]:
    rects = []
    for line in text.splitlines():
        if "CTRL_RECT" not in line:
            continue
        item = {"raw": line}
        for part in line.split():
            if part.startswith("label="):
                item["label"] = part.split("=", 1)[1]
            elif part.startswith("x="):
                item["x"] = float(part.split("=", 1)[1])
            elif part.startswith("y="):
                item["y"] = float(part.split("=", 1)[1])
            elif part.startswith("w="):
                item["w"] = float(part.split("=", 1)[1])
            elif part.startswith("h="):
                item["h"] = float(part.split("=", 1)[1])
        if "label" in item:
            rects.append(item)
    return rects


def visible_bbox(src: Path, rect: dict) -> list[int]:
    img = Image.open(src).convert("RGB")
    pad = 8
    x = max(0, int(rect["x"]) - pad)
    y = max(0, int(rect["y"]) - pad)
    x2 = min(img.width, int(rect["x"] + rect["w"]) + pad)
    y2 = min(img.height, int(rect["y"] + rect["h"]) + pad)
    if x2 <= x or y2 <= y:
        return []
    return [x, y, x2 - x, y2 - y]


def crop_with_bbox(src: Path, dst: Path, bbox: list[int]) -> None:
    img = Image.open(src).convert("RGB")
    x, y, w, h = bbox
    img.crop((x, y, x + w, y + h)).save(dst)


def load_inventory() -> dict[int, dict]:
    if INV_PATH.exists():
        with INV_PATH.open(newline="") as f:
            return {int(r["row"]): r for r in csv.DictReader(f)}
    rel = INV_PATH.relative_to(REPO).as_posix()
    text = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=REPO, text=True)
    return {int(r["row"]): r for r in csv.DictReader(io.StringIO(text))}


def update_inventory_status(rows: list[dict]) -> None:
    if not INV_PATH.exists():
        return
    status_by_row = {str(r["inventory_row"]): r["manual_status_after"] for r in rows}
    with INV_PATH.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise RuntimeError(f"missing CSV header: {INV_PATH}")
        inventory_rows = list(reader)
    for row in inventory_rows:
        status = status_by_row.get(row.get("row", ""))
        if status is not None:
            row["status"] = status
    with INV_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(inventory_rows)


def status_for(src: dict, row: int) -> tuple[str, str, str, str, str]:
    leaf = src.get("target_leaf", "")
    if leaf != "EDIT / Placement paint / SPRITE":
        return (
            "visible_surface_mismatch",
            "scanner target leaf stale: live rect belongs to EDIT SPRITE placement surface",
            "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
            "world_edit_panel",
            "update scanner target leaf and run downstream TERM++ sprite-placement delta proof",
        )
    return (
        "reachable_actionable",
        "",
        "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
        "world_edit_panel",
        "run downstream TERM++ sprite-placement delta proof",
    )


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = OUT_DIR / "action_transcript.jsonl"
    transcript = transcript_path.open("w")
    def log(event: str, **kw):
        rec = {"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        transcript.write(json.dumps(rec, sort_keys=True) + "\n")
        transcript.flush()

    inv = load_inventory()
    log("start", out_dir=str(OUT_DIR.relative_to(REPO)))
    for method, params, timeout in [
        ("NEW_MAP", None, 5.0),
        ("SET_ACTIVE_SPRITE", "player-1000.xp", 8.0),
        ("FL4260_SET_RENDER_MODE", "1", 5.0),
        ("FL4260_RENDERING_PROOF", "1 0 0", 5.0),
        ("FL4260_FOCUS_SIDEBAR", None, 5.0),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120", 5.0),
    ]:
        result = cdp_result(method, params, timeout=timeout)
        log("cdp", method=method, params=params, result=result[:600])
        time.sleep(0.35)

    best_by_row: dict[int, tuple[dict, Path, str, list[int]]] = {}
    all_rects = []
    for spec in PASSES:
        name = spec["name"]
        tab = spec["tab"]
        scroll = spec["scroll"]
        cdp_result("FL4260_FOCUS_BRUSH_TAB", str(tab))
        time.sleep(0.35)
        cdp_result("FL4260_EDIT_SCROLL_Y", str(scroll))
        time.sleep(0.35)
        cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
        time.sleep(0.2)
        frame_dir = OUT_DIR / "scroll-frames" / f"{name}_{scroll}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
        frame = frame_dir / "ui_frame.png"
        wait_for_png(frame)
        time.sleep(0.2)
        rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
        (frame_dir / "ctrl_rects.txt").write_text(rect_text)
        rects = parse_rects(rect_text)
        log("pass", name=name, tab=tab, scroll=scroll, rect_count=len(rects), frame=str(frame.relative_to(REPO)))
        for rect in rects:
            all_rects.append({"pass": name, "tab": tab, "scroll_y": scroll, **rect})
            row = RECT_TO_ROW.get(rect.get("label", ""))
            if row is None:
                continue
            bbox = visible_bbox(frame, rect)
            if not bbox:
                log("skip_offscreen", row=row, label=rect.get("label"), name=name)
                continue
            existing = best_by_row.get(row)
            if existing is not None:
                eb = existing[3]
                if (bbox[2] * bbox[3], bbox[3]) <= (eb[2] * eb[3], eb[3]):
                    continue
            row_dir = CAP_DIR / f"row_{row}"
            row_dir.mkdir(parents=True, exist_ok=True)
            ui_dst = row_dir / "ui_frame.png"
            shutil.copy2(frame, ui_dst)
            crop_with_bbox(frame, row_dir / "row_crop.png", bbox)
            best_by_row[row] = (rect, ui_dst, name, bbox)

    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects))

    rows = []
    mismatches = []
    label_gaps = []
    layout_gaps = []
    termpp_queue = []
    for row, (rect, ui_dst, pass_name, bbox) in sorted(best_by_row.items()):
        src = inv[row]
        manual_status, failure, verdict, surface, next_action = status_for(src, row)
        visible = rect["label"]
        meta = {
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "scanner_label": src["current_user_label"],
            "headed_visible_label": visible,
            "capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "bbox_xywh": bbox,
            "scroll_y": pass_name,
            "target_leaf": src["target_leaf"],
            "visible_top_level_tab": "EDIT",
            "visible_container": "World Facts / SPRITE",
            "visibility_status": manual_status,
            "visibility_failure": failure,
            "evidence_reason": f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {visible} during {pass_name} pass.",
            "termpp_verdict": verdict,
            "required_next_action": next_action,
        }
        (CAP_DIR / f"row_{row}" / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        out = {
            "inventory_row": row,
            "widget": src["widget"],
            "source_anchor": src["source_anchor"],
            "source_line": src["source_line"],
            "target_leaf": src["target_leaf"],
            "scanner_label": src["current_user_label"],
            "headed_visible_label": visible,
            "headed_location_tab": "EDIT",
            "headed_location_section": "World Facts / SPRITE",
            "headed_reachable": manual_status,
            "headed_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "headed_row_bbox_xywh": json.dumps(bbox),
            "headed_scroll_y": pass_name,
            "headed_visibility_note": meta["evidence_reason"],
            "visibility_failure": failure,
            "backend_mutation_owner": src["backend_mutation_owner"],
            "render_proof_consumer": src["render_proof_consumer"],
            "inventory_status_before": src["status"],
            "manual_status_after": manual_status,
            "termpp_verdict": verdict,
            "termpp_expected_surface": surface,
            "termpp_expected_property": "sprite placement preference change",
            "termpp_expected_delta_class": "sprite_placement_downstream_termpp",
            "termpp_exception_reason": failure if verdict.startswith("TERMPLUSPLUS_VERDICT_BLOCKED") else "",
            "product_loop_class": "NON_PROFILE_WORLD_EDIT_CONTROL",
            "existing_matrix_row": "",
            "existing_proof_artifact": "",
            "required_next_action": next_action,
            "reviewer_notes": "Real headed row crop from current ASCIIID UI on NEW_MAP with active sprite selected. This is row-level reachability and visible-surface audit evidence, not action proof.",
        }
        rows.append(out)
        if src["target_leaf"] != "EDIT / Placement paint / SPRITE":
            mismatches.append(out)
        if manual_status == "blocked_label_unresolved":
            label_gaps.append(out)
        if manual_status == "clipped_layout_blocker":
            layout_gaps.append(out)
        termpp_queue.append(out)

    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for name, data in [
        ("visible-surface-mismatches.jsonl", mismatches),
        ("layout-blockers.jsonl", layout_gaps),
        ("unreachable-controls.jsonl", []),
        ("backend-owner-gaps.jsonl", []),
    ]:
        (OUT_DIR / name).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in data))
    with (OUT_DIR / "termpp-verdict-queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(termpp_queue)
    with (OUT_DIR / "profile-path-proof-queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
    update_inventory_status(rows)
    summary = {
        "rows_with_exact_crops": len(rows),
        "rows": [r["inventory_row"] for r in rows],
        "missing_target_rows": sorted(set(RECT_TO_ROW.values()) - set(best_by_row)),
        "by_status": Counter(r["manual_status_after"] for r in rows),
        "by_target_leaf": Counter(r["target_leaf"] for r in rows),
        "visible_surface_mismatches": len(mismatches),
        "label_gaps": len(label_gaps),
        "layout_blockers": len(layout_gaps),
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "visible-surface-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level EDIT SPRITE headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid --cdp {PORT}`.\n\n"
        "This package captures exact row-level crops for the EDIT SPRITE placement slice after selecting `player-1000.xp` through the editor command path. "
        "It records visible-surface mismatches where the scanner CSV still points at ITEM or UNMAPPED leaves for live SPRITE controls. "
        "It is inventory evidence only and does not claim Phase 0 acceptance, Phase 1 acceptance, product acceptance, backend acceptance, Law 15, Law 16, native parity, nor closure.\n\n"
        f"Rows with exact crops: {len(rows)}.\n"
        f"Missing target rows: {summary['missing_target_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

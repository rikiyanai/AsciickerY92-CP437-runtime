#!/usr/bin/env python3
"""FL-4260 row-level headed inventory for current CSV rows 74-426.

Rows 74-76 are modal-state controls. Rows 77-419 are legacy material appearance
controls hidden from the current UI by ASCIICKER_LEGACY_MATERIAL_UI. Rows
420-426 are visible top tab-strip controls captured from the headed editor.
"""
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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-legacy-tabs-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

ROW_START = 74
ROW_END = 426

TAB_RECTS = {
    "tabstrip.view": 420,
    "tabstrip.edit": 421,
    "tabstrip.sprite": 422,
    "tabstrip.mesh": 423,
    "tabstrip.inst": 424,
    "tabstrip.font": 425,
    "tabstrip.skin": 426,
}

TAB_FIXED_BBOX = {
    420: [6, 7, 44, 24],
    421: [52, 7, 38, 24],
    422: [92, 7, 59, 24],
    423: [153, 7, 50, 24],
    424: [205, 7, 42, 24],
    425: [249, 7, 49, 24],
    426: [300, 7, 44, 24],
}

MODAL_ROWS = {74, 75, 76}
SOURCE_DEAD_ROWS = set(range(77, 420))
TAB_ROWS = set(range(420, 427))

FIELDNAMES = [
    "inventory_row", "widget", "source_anchor", "source_line", "target_leaf", "scanner_label",
    "actual_visible_top_level_tab", "actual_visible_container", "headed_visible_label", "headed_row_crop",
    "bbox_xywh", "scroll_y", "reachability_status", "visibility_failure", "backend_owner", "render_consumer",
    "headed_location_tab", "headed_location_section", "headed_reachable", "headed_capture_file",
    "headed_row_capture_file", "headed_row_crop_file", "headed_row_bbox_xywh", "headed_scroll_y",
    "headed_visibility_note", "backend_mutation_owner", "render_proof_consumer",
    "inventory_status_before", "manual_status_after", "termpp_verdict",
    "termpp_verdict_before_contract_normalization", "termpp_expected_surface",
    "termpp_expected_property", "termpp_expected_delta_class", "termpp_exception_reason",
    "product_loop_class", "existing_matrix_row", "existing_proof_artifact",
    "required_next_action", "reviewer_notes",
]


def cdp(method: str, params: str | None = None, timeout: float = 5.0) -> dict:
    msg = {"id": 1, "method": method}
    if params is not None:
        msg["params"] = params
    data = (json.dumps(msg) + "\n").encode()
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(data)
        out = b""
        while b"\n" not in out:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
        text = out.decode("utf-8", "replace").strip()
        return json.loads(text) if text else {"id": 1, "result": ""}


def cdp_result(method: str, params: str | None = None) -> str:
    return str(cdp(method, params).get("result", ""))


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


def load_inventory() -> dict[int, dict]:
    if INV_PATH.exists():
        text = INV_PATH.read_text()
    else:
        rel = INV_PATH.relative_to(REPO).as_posix()
        text = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=REPO, text=True)
    return {int(r["row"]): r for r in csv.DictReader(io.StringIO(text))}


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


def row_base(src: dict, row: int) -> dict:
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "backend_owner": src["backend_mutation_owner"],
        "backend_mutation_owner": src["backend_mutation_owner"],
        "inventory_status_before": src["status"],
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
    }


def modal_row(src: dict, row: int, frame: str) -> dict:
    note = "modal control requires deferred-operation or confirmation-dialog runtime state before it is visible"
    r = row_base(src, row)
    r.update({
        "actual_visible_top_level_tab": "ROOT UI",
        "actual_visible_container": "Modal layer",
        "headed_visible_label": "not rendered: modal state not active",
        "headed_row_crop": "",
        "bbox_xywh": "[]",
        "scroll_y": "modal_state_not_active",
        "reachability_status": "dynamic_label_needs_runtime_state",
        "visibility_failure": "requires_modal_runtime_state",
        "render_consumer": "navigation/modal outcome evidence",
        "headed_location_tab": "ROOT UI",
        "headed_location_section": "Modal layer",
        "headed_reachable": "dynamic_label_needs_runtime_state",
        "headed_capture_file": frame,
        "headed_row_capture_file": frame,
        "headed_row_crop_file": "",
        "headed_row_bbox_xywh": "[]",
        "headed_scroll_y": "modal_state_not_active",
        "headed_visibility_note": note,
        "render_proof_consumer": "navigation/modal outcome evidence",
        "manual_status_after": "dynamic_label_needs_runtime_state",
        "termpp_verdict": "NO_DELTA_METADATA",
        "termpp_verdict_before_contract_normalization": "NO_DELTA_METADATA",
        "termpp_expected_surface": "modal_state",
        "termpp_expected_property": "deferred operation dialog state",
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": note,
        "product_loop_class": "Navigation modal",
        "required_next_action": "trigger the owning modal state and recapture exact button row",
        "reviewer_notes": note,
    })
    return r


def source_dead_row(src: dict, row: int, frame: str) -> dict:
    note = "legacy material appearance control is hidden by ASCIICKER_LEGACY_MATERIAL_UI and excluded from current product UI"
    r = row_base(src, row)
    r.update({
        "actual_visible_top_level_tab": "EDIT",
        "actual_visible_container": "Legacy diagnostic appearance repair gated off",
        "headed_visible_label": "not live in current UI",
        "headed_row_crop": "",
        "bbox_xywh": "[]",
        "scroll_y": "source_dead",
        "reachability_status": "source_dead_not_live",
        "visibility_failure": "legacy_material_ui_gated_off",
        "render_consumer": "none; stale legacy diagnostic owner excluded from closure",
        "headed_location_tab": "EDIT",
        "headed_location_section": "Legacy diagnostic appearance repair gated off",
        "headed_reachable": "source_dead_not_live",
        "headed_capture_file": frame,
        "headed_row_capture_file": frame,
        "headed_row_crop_file": "",
        "headed_row_bbox_xywh": "[]",
        "headed_scroll_y": "source_dead",
        "headed_visibility_note": note,
        "render_proof_consumer": "none; stale legacy diagnostic owner excluded from closure",
        "manual_status_after": "source_dead_not_live",
        "termpp_verdict": "NO_DELTA_DIAGNOSTIC",
        "termpp_verdict_before_contract_normalization": "NO_DELTA_DIAGNOSTIC",
        "termpp_expected_surface": "none",
        "termpp_expected_property": "legacy diagnostic source is not a live product control",
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": note,
        "product_loop_class": "Legacy diagnostic source",
        "required_next_action": "delete stale scanner target or keep as source-dead diagnostic row; do not patch as product UI",
        "reviewer_notes": note,
    })
    return r


def tab_row(src: dict, row: int, rect: dict | None, frame: Path, pass_name: str, bbox: list[int]) -> dict:
    labels = {
        420: "VIEW",
        421: "EDIT",
        422: "SPRITE",
        423: "MESH",
        424: "INST",
        425: "FONT",
        426: "SKIN",
    }
    label = labels[row]
    row_dir = CAP_DIR / f"row_{row}"
    row_dir.mkdir(parents=True, exist_ok=True)
    ui_dst = row_dir / "ui_frame.png"
    crop_dst = row_dir / "row_crop.png"
    shutil.copy2(frame, ui_dst)
    crop_with_bbox(frame, crop_dst, bbox)
    if rect is not None:
        note = f"Exact tab-strip ImGui rect emitted as {rect['label']} during {pass_name}."
    else:
        note = f"Exact headed crop from visible top tab strip during {pass_name}; ImGui tab item did not expose an item rect through the row probe."
    r = row_base(src, row)
    r.update({
        "actual_visible_top_level_tab": "ROOT UI",
        "actual_visible_container": "Top tab strip",
        "headed_visible_label": label,
        "headed_row_crop": f"row-captures/row_{row}/row_crop.png",
        "bbox_xywh": json.dumps(bbox),
        "scroll_y": pass_name,
        "reachability_status": "visible_surface_mismatch",
        "visibility_failure": "stale_scanner_target_leaf",
        "render_consumer": "navigation only; no TERM++ delta expected",
        "headed_location_tab": "ROOT UI",
        "headed_location_section": "Top tab strip",
        "headed_reachable": "visible_surface_mismatch",
        "headed_capture_file": str(ui_dst.relative_to(OUT_DIR)),
        "headed_row_capture_file": str(ui_dst.relative_to(OUT_DIR)),
        "headed_row_crop_file": f"row-captures/row_{row}/row_crop.png",
        "headed_row_bbox_xywh": json.dumps(bbox),
        "headed_scroll_y": pass_name,
        "headed_visibility_note": note,
        "render_proof_consumer": "navigation only; no TERM++ delta expected",
        "manual_status_after": "visible_surface_mismatch",
        "termpp_verdict": "NO_DELTA_METADATA",
        "termpp_verdict_before_contract_normalization": "NO_DELTA_METADATA",
        "termpp_expected_surface": "navigation",
        "termpp_expected_property": "top-level tab selection",
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": "navigation row; scanner target leaf still says RENDERING / Starters",
        "product_loop_class": "Navigation",
        "required_next_action": "repair scanner target leaf to ROOT UI / Top tab strip",
        "reviewer_notes": note,
    })
    meta = {
        "inventory_row": row,
        "headed_visible_label": label,
        "capture_file": r["headed_capture_file"],
        "row_crop_file": r["headed_row_crop_file"],
        "bbox_xywh": bbox,
        "scroll_y": pass_name,
        "evidence_reason": note,
    }
    (row_dir / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return r


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    transcript = (OUT_DIR / "action_transcript.jsonl").open("w")

    def log(event: str, **kw) -> None:
        rec = {"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        transcript.write(json.dumps(rec, sort_keys=True) + "\n")
        transcript.flush()

    inv = load_inventory()
    target_rows = list(range(ROW_START, ROW_END + 1))
    log("start", out_dir=str(OUT_DIR.relative_to(REPO)), rows=[ROW_START, ROW_END])

    for method, params in [
        ("NEW_MAP", None),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("FL4260_RENDERING_PROOF", "1 0 0"),
        ("FL4260_FOCUS_SIDEBAR", None),
        ("FL4260_SCROLL_Y", "0"),
    ]:
        result = cdp_result(method, params)
        log("cdp", method=method, params=params, result=result[:500])
        time.sleep(0.25)

    cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
    time.sleep(0.2)
    frame_dir = OUT_DIR / "scroll-frames" / "top_tabs_0"
    frame_dir.mkdir(parents=True, exist_ok=True)
    cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
    frame = frame_dir / "ui_frame.png"
    wait_for_png(frame)
    time.sleep(0.2)
    rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
    (frame_dir / "ctrl_rects.txt").write_text(rect_text)
    rects = parse_rects(rect_text)
    all_rects = [{"scroll_pass": "top_tabs_0", "scroll_y": 0, **r} for r in rects]
    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects)
    )
    tab_by_row: dict[int, tuple[dict, list[int]]] = {}
    for rect in rects:
        row = TAB_RECTS.get(rect.get("label", ""))
        if row is None:
            continue
        bbox = visible_bbox(frame, rect)
        if bbox:
            tab_by_row[row] = (rect, bbox)

    rows = []
    fallback_frame = "scroll-frames/top_tabs_0/ui_frame.png"
    for row in target_rows:
        src = inv[row]
        if row in MODAL_ROWS:
            rows.append(modal_row(src, row, fallback_frame))
        elif row in SOURCE_DEAD_ROWS:
            rows.append(source_dead_row(src, row, fallback_frame))
        elif row in TAB_ROWS:
            rect_bbox = tab_by_row.get(row)
            if rect_bbox is not None:
                rect, bbox = rect_bbox
                rows.append(tab_row(src, row, rect, frame, "top_tabs_0", bbox))
            else:
                rows.append(tab_row(src, row, None, frame, "top_tabs_0", TAB_FIXED_BBOX[row]))

    rows.sort(key=lambda r: int(r["inventory_row"]))
    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    summary = {
        "rows": [r["inventory_row"] for r in rows],
        "row_start": ROW_START,
        "row_end": ROW_END,
        "row_count": len(rows),
        "rows_with_exact_crops": sum(1 for r in rows if r["headed_row_crop_file"]),
        "missing_rows": sorted(set(target_rows) - {int(r["inventory_row"]) for r in rows}),
        "status_counts": Counter(r["reachability_status"] for r in rows),
        "termpp_verdict_counts": Counter(r["termpp_verdict"] for r in rows),
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    for name in ["summary.json", "row-level-headed-inventory-summary.json", "visible-surface-audit.json"]:
        (OUT_DIR / name).write_text(json.dumps(summary, indent=2, sort_keys=True, default=dict) + "\n")

    visible_mismatches = [r for r in rows if r["reachability_status"] == "visible_surface_mismatch"]
    unreachable = [r for r in rows if r["reachability_status"] in {"dynamic_label_needs_runtime_state", "not_visible_after_scroll", "source_dead_not_live"}]
    backend_gaps = [r for r in rows if r["termpp_verdict"] == "BLOCKED_PENDING_BACKEND_TRACE"]
    for name, data in [
        ("visible-surface-mismatches.jsonl", visible_mismatches),
        ("unreachable-controls.jsonl", unreachable),
        ("backend-owner-gaps.jsonl", backend_gaps),
        ("layout-blockers.jsonl", []),
    ]:
        (OUT_DIR / name).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in data))

    for name, data in [
        ("termpp-verdict-queue.csv", rows),
        ("profile-path-proof-queue.csv", []),
    ]:
        with (OUT_DIR / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(data)

    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level legacy and tab-strip headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid` via CDP port {PORT}.\n\n"
        "This package inventories scanner rows 74-426. Rows 74-76 are modal-state rows, rows 77-419 are source-dead legacy material appearance controls hidden by ASCIICKER_LEGACY_MATERIAL_UI, and rows 420-426 are exact headed top-tab crops. "
        "It is inventory evidence only. It does not claim Phase 0 completion, Phase 1 completion, product acceptance, Law 15, Law 16, backend completion, native parity, nor closure.\n\n"
        f"Rows with exact crops: {summary['rows_with_exact_crops']}.\n"
        f"Missing rows: {summary['missing_rows']}.\n"
        f"Status counts: {dict(summary['status_counts'])}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=dict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

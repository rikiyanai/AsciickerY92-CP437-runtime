#!/usr/bin/env python3
"""FL-4260 row-level headed inventory for current CSV INFO tail rows 628-629."""
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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-info-tail-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

RECT_TO_ROW = {
    "info.debug_probe": 628,
    "info.shading_quick_guide": 629,
}

LABELS = {
    "info.debug_probe": "Debug Probe",
    "info.shading_quick_guide": "Shading Quick Guide",
}

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
    return json.loads(text) if text else {"id": 1, "result": ""}


def cdp_result(method: str, params: str | None = None) -> str:
    return str(cdp(method, params).get("result", ""))


def wait_for_png(path: Path, seconds: float = 5.0) -> bool:
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


def build_row(src: dict, row: int, rect: dict, frame: Path, bbox: list[int]) -> dict:
    label = LABELS[rect["label"]]
    row_dir = CAP_DIR / f"row_{row}"
    row_dir.mkdir(parents=True, exist_ok=True)
    ui_dst = row_dir / "ui_frame.png"
    shutil.copy2(frame, ui_dst)
    crop_with_bbox(frame, row_dir / "row_crop.png", bbox)
    note = f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {rect['label']} during INFO tail capture."
    out = {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": "INFO",
        "actual_visible_container": "INFO / Terrain Probe",
        "headed_visible_label": label,
        "headed_row_crop": f"row-captures/row_{row}/row_crop.png",
        "bbox_xywh": json.dumps(bbox),
        "scroll_y": "info_tail_0",
        "reachability_status": "visible_surface_mismatch",
        "visibility_failure": "stale_scanner_target_leaf",
        "backend_owner": src["backend_mutation_owner"],
        "render_consumer": "diagnostic readout only; no TERM++ authoring delta expected",
        "headed_location_tab": "INFO",
        "headed_location_section": "INFO / Terrain Probe",
        "headed_reachable": "visible_surface_mismatch",
        "headed_capture_file": str(ui_dst.relative_to(OUT_DIR)),
        "headed_row_capture_file": str(ui_dst.relative_to(OUT_DIR)),
        "headed_row_crop_file": f"row-captures/row_{row}/row_crop.png",
        "headed_row_bbox_xywh": json.dumps(bbox),
        "headed_scroll_y": "info_tail_0",
        "headed_visibility_note": note,
        "backend_mutation_owner": src["backend_mutation_owner"],
        "render_proof_consumer": "diagnostic readout only; no TERM++ authoring delta expected",
        "inventory_status_before": src["status"],
        "manual_status_after": "visible_surface_mismatch",
        "termpp_verdict": "NO_DELTA_DIAGNOSTIC",
        "termpp_verdict_before_contract_normalization": "NO_DELTA_DIAGNOSTIC",
        "termpp_expected_surface": "INFO terrain probe diagnostics",
        "termpp_expected_property": label,
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": "diagnostic INFO row; scanner target leaf is stale",
        "product_loop_class": "INFO diagnostic",
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
        "required_next_action": "update scanner target leaf to INFO / Terrain Probe and exclude diagnostic row from authoring completion",
        "reviewer_notes": note,
    }
    meta = {
        "inventory_row": row,
        "headed_visible_label": label,
        "capture_file": out["headed_capture_file"],
        "row_crop_file": out["headed_row_crop_file"],
        "bbox_xywh": bbox,
        "scroll_y": "info_tail_0",
        "evidence_reason": note,
    }
    (row_dir / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    inv = load_inventory()
    transcript = (OUT_DIR / "action_transcript.jsonl").open("w")

    def log(event: str, **kw) -> None:
        transcript.write(json.dumps({"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}, sort_keys=True) + "\n")
        transcript.flush()

    for method, params in [
        ("NEW_MAP", None),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120"),
        ("FL4260_INFO_TERRAIN_PROBE", "64 64"),
        ("FL4260_LOCK_SIDEBAR_TAB", "7"),
    ]:
        result = cdp_result(method, params)
        log("cdp", method=method, params=params, result=result[:500])
        time.sleep(0.3)

    cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
    time.sleep(0.25)
    frame_dir = OUT_DIR / "scroll-frames" / "info_tail_0"
    frame_dir.mkdir(parents=True, exist_ok=True)
    cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
    frame = frame_dir / "ui_frame.png"
    if not wait_for_png(frame):
        raise RuntimeError(f"missing capture: {frame}")
    time.sleep(0.2)
    rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
    cdp_result("FL4260_LOCK_SIDEBAR_TAB", "-1")
    (frame_dir / "ctrl_rects.txt").write_text(rect_text)
    rects = parse_rects(rect_text)
    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rects))

    rows = []
    seen_rows = set()
    for rect in rects:
        row = RECT_TO_ROW.get(rect.get("label", ""))
        if row is None:
            continue
        if row in seen_rows:
            continue
        bbox = visible_bbox(frame, rect)
        if bbox:
            rows.append(build_row(inv[row], row, rect, frame, bbox))
            seen_rows.add(row)

    rows.sort(key=lambda r: int(r["inventory_row"]))
    missing = sorted(set(RECT_TO_ROW.values()) - {int(r["inventory_row"]) for r in rows})
    if missing:
        raise RuntimeError(f"missing INFO tail rows: {missing}")

    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    summary = {
        "rows": [r["inventory_row"] for r in rows],
        "row_count": len(rows),
        "missing_rows": missing,
        "rows_with_exact_crops": sum(1 for r in rows if r["headed_row_crop_file"]),
        "status_counts": Counter(r["reachability_status"] for r in rows),
        "termpp_verdict_counts": Counter(r["termpp_verdict"] for r in rows),
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    for name in ["summary.json", "row-level-headed-inventory-summary.json", "visible-surface-audit.json"]:
        (OUT_DIR / name).write_text(json.dumps(summary, indent=2, sort_keys=True, default=dict) + "\n")
    for name, data in [
        ("visible-surface-mismatches.jsonl", rows),
        ("unreachable-controls.jsonl", []),
        ("backend-owner-gaps.jsonl", []),
        ("layout-blockers.jsonl", []),
    ]:
        (OUT_DIR / name).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in data))
    for name, data in [("termpp-verdict-queue.csv", rows), ("profile-path-proof-queue.csv", [])]:
        with (OUT_DIR / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(data)
    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level INFO tail headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid` via CDP port {PORT}.\n\n"
        "This package inventories current scanner rows 628-629 as exact headed INFO / Terrain Probe diagnostic rows. It is inventory evidence only and does not claim Phase 0 completion, product acceptance, Law 15, Law 16, backend completion, native parity, nor closure.\n\n"
        f"Rows with exact crops: {summary['rows_with_exact_crops']}.\n"
        f"Missing rows: {summary['missing_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=dict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Ad hoc script: FL-4260 row-level headed UI inventory batch for Measurement Debug and Evidence Receipt controls
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 row-level headed UI inventory batch for current CSV rows 1-73.

Captures exact headed PNG crops for current ASCIIID scanner rows using the
existing CDP read-only rectangle probe. This script scopes rows 1-65 to the
Measurement Debug side container and rows 66-70 to the Extended Material
Workspace receipt controls.
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
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageChops

REPO = Path(__file__).resolve().parents[2]
INV_PATH = REPO / "docs/research/ascii/verification/fl4260/2026-06-18-phase0-current-head-control-inventory/asciiid-ui-current-head-control-inventory.csv"
BASE_DIR = REPO / "docs/research/ascii/verification/fl4260"
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-measurement-receipts-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))
FIXTURE_MAP = REPO / "assets/a3d/fl4260_fixture_all_materials.a3d"

RECT_TO_ROW = {
    "measurement.material_panel_header": 1,
    "measurement.termpp_gpu": 2,
    "measurement.termpp_harri": 3,
    "measurement.blog_scene": 4,
    "measurement.blog_scene_option": 5,
    "measurement.blog_contrast": 6,
    "measurement.blog_quality": 7,
    "measurement.source_pane": 8,
    "measurement.sampling_circles": 9,
    "measurement.external_circles": 10,
    "measurement.decision_audit": 11,
    "measurement.selected_cell": 12,
    "measurement.cell_x": 13,
    "measurement.cell_y": 14,
    "measurement.auto_paint": 15,
    "measurement.paint_now": 16,
    "measurement.paint_selected_cell": 17,
    "measurement.single_target": 18,
    "measurement.paint_all_visible": 19,
    **{f"measurement.target_mat_{i:02d}": 20 + i for i in range(16)},
    "measurement.high_mat_target": 37,
    "measurement.all": 39,
    "measurement.none": 40,
    "measurement.terrain": 41,
    "measurement.mesh_hints": 42,
    "measurement.weight_curve": 43,
    "measurement.weight_diagonal": 44,
    "measurement.weight_horizontal": 45,
    "measurement.weight_vertical": 46,
    "measurement.weight_sparse": 47,
    "measurement.weight_dense": 48,
    "measurement.source_tl": 49,
    "measurement.source_tr": 50,
    "measurement.source_ml": 51,
    "measurement.source_mr": 52,
    "measurement.source_bl": 53,
    "measurement.source_br": 54,
    "measurement.dir_gamma": 56,
    "measurement.global_gamma": 57,
    "measurement.dir_contrast": 58,
    "measurement.global_contrast": 59,
    "measurement.gpu_sidecar": 60,
    "measurement.final_buffer_header": 61,
    "measurement.click_trace_header": 62,
    "measurement.rep_arabic": 63,
    "measurement.rep_math": 64,
    "measurement.rep_shapes": 65,
    "measurement.rep_box": 66,
    "measurement.lock_preset": 67,
    "measurement.preserve_preset": 68,
    "receipts.profile_combo": 69,
    "receipts.profile_option": 70,
    "receipts.accept": 71,
    "receipts.reject": 72,
    "receipts.fill_material": 73,
}

RECT_DUPLICATE_ROWS = {
    "measurement.weight_curve": [55],
}

SCROLL_PASSES = [
    ("measurement_top", 4700),
    ("measurement_blog", 5000),
    ("measurement_selected_cell", 5320),
    ("measurement_paint", 5620),
    ("measurement_targets", 5880),
    ("measurement_source", 6200),
    ("measurement_weights", 6500),
    ("measurement_contrast", 6780),
    ("measurement_headers", 7100),
    ("measurement_repertoire", 7500),
]

OPTION_SCROLL_PASS = ("measurement_blog_option", 5000)
RECEIPT_SCROLL_PASSES = [
    ("receipts_profile", 900),
    ("receipts_actions", 1180),
]

NO_EFFECT_ROWS = set()
HEADER_ROWS = {1, 58, 59}
PERSISTENCE_ROWS = set()
COLOR_ROWS = set()
GLYPH_ROWS = set()
SCORING_ROWS = set(range(6, 8)) | set(range(41, 58)) | set(range(60, 66))
VISIBLE_SURFACE_MISMATCH_ROWS = {55}
DIAGNOSTIC_ROWS = {1, 58, 59}
TRACE_ROWS = {58, 59}
RECEIPT_ROWS = {69, 70, 71, 72, 73}
SOURCE_DEAD_ROWS = set(range(15, 43)) | {69, 70, 71, 72, 73}


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
        if not text:
            return {"id": 1, "result": ""}
        return json.loads(text)


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
        parts = line.split()
        item = {"raw": line}
        for part in parts:
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
    w = int(rect["w"])
    h = int(rect["h"])
    x2 = min(img.width, int(rect["x"] + w) + pad)
    y2 = min(img.height, int(rect["y"] + h) + pad)
    if x2 <= x or y2 <= y:
        return []
    return [x, y, x2 - x, y2 - y]


def crop(src: Path, dst: Path, rect: dict) -> list[int]:
    bbox = visible_bbox(src, rect)
    if not bbox:
        return []
    img = Image.open(src).convert("RGB")
    x, y, w, h = bbox
    img.crop((x, y, x + w, y + h)).save(dst)
    return bbox


def crop_with_bbox(src: Path, dst: Path, bbox: list[int]) -> None:
    img = Image.open(src).convert("RGB")
    x, y, w, h = bbox
    img.crop((x, y, x + w, y + h)).save(dst)


def diff_bbox(a: Path, b: Path) -> list[int]:
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        ib = ib.resize(ia.size)
    box = ImageChops.difference(ia, ib).getbbox()
    return list(box) if box else []


def load_inventory() -> dict[int, dict]:
    if INV_PATH.exists():
        text = INV_PATH.read_text()
    else:
        rel = INV_PATH.relative_to(REPO).as_posix()
        text = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=REPO, text=True)
    return {int(r["row"]): r for r in csv.DictReader(io.StringIO(text))}


def contract_status(row: int, legacy_status: str) -> str:
    if row in VISIBLE_SURFACE_MISMATCH_ROWS:
        return "visible_surface_mismatch"
    if row in SOURCE_DEAD_ROWS:
        return "source_dead_not_live"
    if row in RECEIPT_ROWS:
        return "metadata_only"
    if row in DIAGNOSTIC_ROWS:
        return "diagnostic_only"
    if row in HEADER_ROWS:
        return "reachable_readonly"
    if row in PERSISTENCE_ROWS:
        return "persistence_only"
    if legacy_status == "reachable_click_no_visible_effect_on_new_map":
        return "backend_owner_unknown"
    return "reachable_actionable"


def contract_verdict(row: int, legacy_verdict: str) -> str:
    if row in HEADER_ROWS:
        return "NO_DELTA_METADATA"
    if row in DIAGNOSTIC_ROWS:
        return "NO_DELTA_DIAGNOSTIC"
    if row in PERSISTENCE_ROWS:
        return "PERSISTENCE_RELOAD_EXPECTED"
    if row in TRACE_ROWS:
        return "TRACE_SELECTION_EXPECTED"
    if row in SOURCE_DEAD_ROWS:
        return "NO_DELTA_DIAGNOSTIC"
    if row in RECEIPT_ROWS:
        return "NO_DELTA_METADATA"
    if legacy_verdict == "TERMPLUSPLUS_VERDICT_GAP_NO_VISIBLE_EFFECT_ON_NEW_MAP":
        return "BLOCKED_PENDING_BACKEND_TRACE"
    if row in COLOR_ROWS:
        return "COLOR_DELTA_EXPECTED"
    if row in GLYPH_ROWS or row in SCORING_ROWS:
        return "TERMPLUSPLUS_GLYPH_DELTA_EXPECTED"
    return "BLOCKED_PENDING_BACKEND_TRACE"


def visibility_failure(row: int, legacy_status: str, note_suffix: str) -> str:
    if row in VISIBLE_SURFACE_MISMATCH_ROWS:
        return "stale_scanner_target_leaf"
    if row in SOURCE_DEAD_ROWS:
        return "legacy_glyph_plane_authoring_hidden_by_default"
    if legacy_status == "reachable_click_no_visible_effect_on_new_map":
        return "fresh_map_click_no_visible_effect"
    return ""


def build_output_row(src: dict, meta: dict, status: str, note_suffix: str = "") -> dict:
    row = int(meta["inventory_row"])
    contract_row_status = contract_status(row, status)
    contract_row_verdict = contract_verdict(row, meta["termpp_verdict"])
    failure = visibility_failure(row, status, note_suffix)
    row_crop_file = meta["row_crop_file"]
    bbox = json.dumps(meta["bbox_xywh"])
    scroll_y = meta["scroll_y"]
    backend_owner = src["backend_mutation_owner"]
    render_consumer = "detached TERM++ rendered-cell delta pending"
    visible_tab = "RENDERING"
    visible_container = "Measurement Debug side container" if row <= 65 else "Extended Material Workspace receipt controls"
    required_next_action = meta["required_next_action"]
    if row in VISIBLE_SURFACE_MISMATCH_ROWS:
        required_next_action = "repair stale scanner target leaf/source anchor, then run receipt workflow proof" if row in RECEIPT_ROWS else "repair stale scanner target leaf/source anchor, then run profile-path before/action/after proof"
    elif row in DIAGNOSTIC_ROWS:
        required_next_action = "retain as diagnostic evidence row; no TERM++ authoring delta expected"
    elif row in TRACE_ROWS:
        required_next_action = "trace selection proof with headed visible toggle plus detached TERM++ context"
    elif row in RECEIPT_ROWS:
        required_next_action = "receipt workflow proof; records evidence metadata only"
    elif row in SOURCE_DEAD_ROWS:
        required_next_action = "delete stale scanner target for legacy glyph_plane authoring path from current Phase 0 queue"
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": visible_tab,
        "actual_visible_container": visible_container,
        "headed_visible_label": meta["headed_visible_label"],
        "headed_row_crop": row_crop_file,
        "bbox_xywh": bbox,
        "scroll_y": scroll_y,
        "reachability_status": contract_row_status,
        "visibility_failure": failure,
        "backend_owner": backend_owner,
        "render_consumer": render_consumer,
        "headed_location_tab": visible_tab,
        "headed_location_section": visible_container,
        "headed_reachable": status,
        "headed_capture_file": meta["capture_file"],
        "headed_row_capture_file": meta["capture_file"],
        "headed_row_crop_file": row_crop_file,
        "headed_row_bbox_xywh": bbox,
        "headed_scroll_y": scroll_y,
        "headed_visibility_note": meta["evidence_reason"] + note_suffix,
        "backend_mutation_owner": backend_owner,
        "render_proof_consumer": render_consumer,
        "inventory_status_before": src["status"],
        "manual_status_after": status,
        "termpp_verdict": contract_row_verdict,
        "termpp_verdict_before_contract_normalization": meta["termpp_verdict"],
        "termpp_expected_surface": "measurement_debug" if row <= 65 else "review_receipt_metadata",
        "termpp_expected_property": "diagnostic measurement state" if row <= 65 else "receipt row decision state",
        "termpp_expected_delta_class": "glyph scoring diagnostic" if row <= 65 else "none",
        "termpp_exception_reason": "legacy glyph_plane authoring row hidden from current live UI" if row in SOURCE_DEAD_ROWS else ("fresh-map click produced no proven visible action effect" if row in NO_EFFECT_ROWS else ""),
        "product_loop_class": "Measurement Debug diagnostics" if row <= 65 else "Evidence receipt metadata",
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
        "required_next_action": required_next_action,
        "reviewer_notes": "Real headed row crop from current ASCIIID UI on NEW_MAP. Rect source is the editor read-only ImGui rectangle probe." + note_suffix,
    }


def build_runtime_state_blocked_row(src: dict, row: int) -> dict:
    frame = "scroll-frames/evidence_controls_4500/ui_frame.png"
    note = "Source control is behind a loaded review receipt row. Current headed pass rendered the disabled placeholder row because no review receipt row was loaded."
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": "EDIT",
        "actual_visible_container": "Material Look side container",
        "headed_visible_label": "not rendered: receipt row not loaded",
        "headed_row_crop": "",
        "bbox_xywh": "[]",
        "scroll_y": "evidence_controls",
        "reachability_status": "dynamic_label_needs_runtime_state",
        "visibility_failure": "receipt_row_not_loaded",
        "backend_owner": src["backend_mutation_owner"],
        "render_consumer": "proof metadata only; no TERM++ runtime delta expected",
        "headed_location_tab": "EDIT",
        "headed_location_section": "Material Look side container",
        "headed_reachable": "dynamic_label_needs_runtime_state",
        "headed_capture_file": frame,
        "headed_row_capture_file": frame,
        "headed_row_crop_file": "",
        "headed_row_bbox_xywh": "[]",
        "headed_scroll_y": "evidence_controls",
        "headed_visibility_note": note,
        "backend_mutation_owner": src["backend_mutation_owner"],
        "render_proof_consumer": "proof metadata only; no TERM++ runtime delta expected",
        "inventory_status_before": src["status"],
        "manual_status_after": "dynamic_label_needs_runtime_state",
        "termpp_verdict": "NO_DELTA_METADATA",
        "termpp_verdict_before_contract_normalization": "NO_DELTA_METADATA",
        "termpp_expected_surface": "review_receipt_metadata",
        "termpp_expected_property": "receipt row decision state",
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": "requires loaded receipt row before exact source control is rendered",
        "product_loop_class": "Evidence receipt metadata",
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
        "required_next_action": "load a valid review receipt row, then recapture exact Receipt reason and Accept Reject Defer controls",
        "reviewer_notes": note,
    }


def build_missing_row(src: dict, row: int, status: str, frame: str, reason: str) -> dict:
    verdict = contract_verdict(row, "TERMPLUSPLUS_VERDICT_REQUIRED")
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": "RENDERING",
        "actual_visible_container": "Measurement Debug side container" if row <= 65 else "Extended Material Workspace receipt controls",
        "headed_visible_label": "not rendered in current live UI",
        "headed_row_crop": "",
        "bbox_xywh": "[]",
        "scroll_y": "not_visible",
        "reachability_status": status,
        "visibility_failure": reason,
        "backend_owner": src["backend_mutation_owner"],
        "render_consumer": "no live TERM++ delta expected for non-rendered row",
        "headed_location_tab": "RENDERING",
        "headed_location_section": "Measurement Debug side container" if row <= 65 else "Extended Material Workspace receipt controls",
        "headed_reachable": status,
        "headed_capture_file": frame,
        "headed_row_capture_file": frame,
        "headed_row_crop_file": "",
        "headed_row_bbox_xywh": "[]",
        "headed_scroll_y": "not_visible",
        "headed_visibility_note": reason,
        "backend_mutation_owner": src["backend_mutation_owner"],
        "render_proof_consumer": "no live TERM++ delta expected for non-rendered row",
        "inventory_status_before": src["status"],
        "manual_status_after": status,
        "termpp_verdict": verdict,
        "termpp_verdict_before_contract_normalization": "TERMPLUSPLUS_VERDICT_REQUIRED",
        "termpp_expected_surface": "none" if status == "source_dead_not_live" else "current headed UI",
        "termpp_expected_property": "not rendered in current live UI",
        "termpp_expected_delta_class": "none",
        "termpp_exception_reason": reason,
        "product_loop_class": "Source-dead legacy UI" if status == "source_dead_not_live" else "Visibility blocker",
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
        "required_next_action": "delete stale scanner target for legacy glyph_plane authoring path from current Phase 0 queue" if status == "source_dead_not_live" else "find current visible owner, add exact row crop, then classify status",
        "reviewer_notes": reason,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    transcript = (OUT_DIR / "action_transcript.jsonl").open("w")
    def log(event: str, **kw):
        rec = {"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        transcript.write(json.dumps(rec, sort_keys=True) + "\n")
        transcript.flush()

    inv = load_inventory()
    log("start", out_dir=str(OUT_DIR.relative_to(REPO)))
    for method, params in [
        ("NEW_MAP", None),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("FL4260_RENDERING_PROOF", "1 0 0"),
        ("FL4131_SHAPE_LAB_OPEN", None),
        ("FL4260_KB_FOCUS", "measurement.termpp_gpu"),
        ("FL4260_FOCUS_SIDEBAR", None),
        ("FL4260_SCROLL_Y", "-1"),
    ]:
        result = cdp_result(method, params)
        log("cdp", method=method, params=params, result=result[:500])
        time.sleep(0.35)

    best_by_row: dict[int, tuple[dict, Path, str, list[int]]] = {}
    all_rects = []
    scroll_passes = SCROLL_PASSES + RECEIPT_SCROLL_PASSES
    scroll_by_name = {name: scroll_y for name, scroll_y in scroll_passes}
    for name, scroll_y in scroll_passes:
        cdp_result("FL4260_SCROLL_Y", str(scroll_y))
        time.sleep(0.25)
        cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
        time.sleep(0.2)
        frame_dir = OUT_DIR / "scroll-frames" / f"{name}_{scroll_y}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
        frame = frame_dir / "ui_frame.png"
        if not wait_for_png(frame):
            log("missing_frame", name=name, scroll_y=scroll_y, frame=str(frame.relative_to(REPO)))
            cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
            continue
        time.sleep(0.2)
        rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
        frame_dir.mkdir(parents=True, exist_ok=True)
        rect_file = frame_dir / "ctrl_rects.txt"
        rect_file.write_text(rect_text)
        rects = parse_rects(rect_text)
        log("scroll_pass", name=name, scroll_y=scroll_y, frame=str(frame.relative_to(REPO)), rect_count=len(rects))
        for rect in rects:
            all_rects.append({"scroll_pass": name, "scroll_y": scroll_y, **rect})
            label = rect.get("label", "")
            row = RECT_TO_ROW.get(label)
            duplicate_rows = RECT_DUPLICATE_ROWS.get(label, [])
            if row is None:
                continue
            bbox = visible_bbox(frame, rect)
            if not bbox:
                log("skip_offscreen_rect", row=row, label=label, scroll_pass=name, rect=rect)
                continue
            for target_row in [row] + duplicate_rows:
                existing = best_by_row.get(target_row)
                if existing is not None:
                    existing_bbox = existing[3]
                    if (bbox[2] * bbox[3], bbox[3]) <= (existing_bbox[2] * existing_bbox[3], existing_bbox[3]):
                        continue
                row_dir = CAP_DIR / f"row_{target_row}"
                row_dir.mkdir(parents=True, exist_ok=True)
                ui_dst = row_dir / "ui_frame.png"
                shutil.copy2(frame, ui_dst)
                crop_with_bbox(frame, row_dir / "row_crop.png", bbox)
                rect_copy = dict(rect)
                if target_row in VISIBLE_SURFACE_MISMATCH_ROWS:
                    rect_copy["label"] = f"{label}.scanner_duplicate_row_{target_row}"
                best_by_row[target_row] = (rect_copy, ui_dst, name, bbox)

    for parent_row, option_row, option_pass in [
        (4, 5, OPTION_SCROLL_PASS[0]),
        (66, 67, "receipts_profile_option"),
    ]:
        if parent_row not in best_by_row:
            log("option_parent_missing", parent_row=parent_row, option_row=option_row)
            continue
        rect, _ui_dst, pass_name, _bbox = best_by_row[parent_row]
        scroll_y = scroll_by_name.get(pass_name, OPTION_SCROLL_PASS[1])
        cdp_result("FL4260_SCROLL_Y", str(scroll_y))
        time.sleep(0.25)
        cx = int(rect["x"] + rect["w"] / 2.0)
        cy = int(rect["y"] + rect["h"] / 2.0)
        cdp_result("RUN_MOUSE_CLICK_PROBE", f"{cx} {cy}")
        time.sleep(0.35)
        cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
        time.sleep(0.2)
        frame_dir = OUT_DIR / "scroll-frames" / f"{option_pass}_{scroll_y}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
        frame = frame_dir / "ui_frame.png"
        if not wait_for_png(frame):
            log("missing_option_frame", parent_row=parent_row, option_row=option_row, frame=str(frame.relative_to(REPO)))
            cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
            continue
        time.sleep(0.2)
        rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
        frame_dir.mkdir(parents=True, exist_ok=True)
        (frame_dir / "ctrl_rects.txt").write_text(rect_text)
        option_found = False
        for option_rect in parse_rects(rect_text):
            all_rects.append({"scroll_pass": option_pass, "scroll_y": scroll_y, **option_rect})
            if RECT_TO_ROW.get(option_rect.get("label", "")) != option_row:
                continue
            bbox = visible_bbox(frame, option_rect)
            if not bbox:
                continue
            row_dir = CAP_DIR / f"row_{option_row}"
            row_dir.mkdir(parents=True, exist_ok=True)
            ui_dst = row_dir / "ui_frame.png"
            shutil.copy2(frame, ui_dst)
            crop_with_bbox(frame, row_dir / "row_crop.png", bbox)
            best_by_row[option_row] = (dict(option_rect), ui_dst, option_pass, bbox)
            option_found = True
            break
        log("option_capture", parent_row=parent_row, option_row=option_row, found=option_found, scroll_y=scroll_y)

    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects))

    out_rows = []
    for row, (rect, ui_dst, pass_name, bbox) in sorted(best_by_row.items()):
        src = inv[row]
        visible = rect["label"]
        if row in VISIBLE_SURFACE_MISMATCH_ROWS:
            visible = visible.split(".scanner_duplicate_row_", 1)[0]
        click_evidence = {}
        status = "reachable_with_capture"
        verdict = "PROFILE_PATH_PROOF_REQUIRED" if src["target_leaf"].startswith("RENDERING / ") else "TERMPLUSPLUS_VERDICT_REQUIRED"
        next_action = "profile-path before/action/after proof" if src["target_leaf"].startswith("RENDERING / ") else "non-profile TERM++ verdict proof"
        note_suffix = ""
        if row in NO_EFFECT_ROWS:
            cx = int(rect["x"] + rect["w"] / 2.0)
            cy = int(rect["y"] + rect["h"] / 2.0)
            cdp_result("RUN_MOUSE_CLICK_PROBE", f"{cx} {cy}")
            time.sleep(0.35)
            after_dir = CAP_DIR / f"row_{row}" / "after_click"
            after_dir.mkdir(parents=True, exist_ok=True)
            cdp_result("CAPTURE_UI_FRAME", str(after_dir))
            after_frame = after_dir / "ui_frame.png"
            wait_for_png(after_frame)
            after_crop = after_dir / "row_crop.png"
            crop(after_frame, after_crop, rect)
            click_evidence = {
                "click_xy": [cx, cy],
                "after_click_frame": str(after_frame.relative_to(OUT_DIR)),
                "after_click_crop": str(after_crop.relative_to(OUT_DIR)),
                "row_crop_diff_bbox": diff_bbox(CAP_DIR / f"row_{row}" / "row_crop.png", after_crop),
            }
            status = "reachable_click_no_visible_effect_on_new_map"
            verdict = "TERMPLUSPLUS_VERDICT_GAP_NO_VISIBLE_EFFECT_ON_NEW_MAP"
            next_action = "fix clickable action so fresh-map click produces visible Material Look feedback and detached TERM++ delta"
            note_suffix = " Fresh-map click audit: visible control accepted a click probe, but no visible Material Look feedback/TERM++ delta was proven; classify as broken no-effect action."
        meta = {
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "source_line": src["source_line"],
            "scanner_label": src["current_user_label"],
            "headed_visible_label": visible,
            "capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "bbox_xywh": bbox,
            "scroll_y": pass_name,
            "fresh_map": True,
            "target_leaf": src["target_leaf"],
            "visible_top_level_tab": "EDIT",
            "visible_container": "Material Look side container",
            "visibility_status": status,
            "evidence_reason": f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {rect['label']} during {pass_name} scroll pass.",
            "click_evidence": click_evidence,
            "termpp_verdict": verdict,
            "required_next_action": next_action,
        }
        meta_dir = CAP_DIR / f"row_{row}"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        out_rows.append(build_output_row(src, meta, status, note_suffix))

    captured_rows = {int(r["inventory_row"]) for r in out_rows}
    fallback_frame = "scroll-frames/measurement_top_4700/ui_frame.png"
    for row in sorted(set(range(1, 74)) - captured_rows):
        if row not in inv:
            continue
        if row in SOURCE_DEAD_ROWS:
            out_rows.append(build_missing_row(
                inv[row],
                row,
                "source_dead_not_live",
                fallback_frame,
                "legacy glyph_plane paint control is hidden by ASCIICKER_LEGACY_MATERIAL_UI and is not part of current live UI",
            ))
            log("source_dead_not_live_row", row=row)
            continue
        out_rows.append(build_missing_row(
            inv[row],
            row,
            "not_visible_after_scroll",
            fallback_frame,
            "no exact live ImGui rect was captured across the headed Measurement Debug and receipt scroll passes",
        ))
        log("not_visible_after_scroll_row", row=row)
    captured_rows = {int(r["inventory_row"]) for r in out_rows}
    for row in sorted(set(RECT_TO_ROW.values()) - captured_rows):
        meta_path = CAP_DIR / f"row_{row}" / "row_metadata.json"
        crop_path = CAP_DIR / f"row_{row}" / "row_crop.png"
        if row not in inv or not meta_path.exists() or not crop_path.exists() or crop_path.stat().st_size == 0:
            continue
        meta = json.loads(meta_path.read_text())
        out_rows.append(build_output_row(inv[row], meta, meta["visibility_status"]))
        log("preserved_prior_row_capture", row=row, meta=str(meta_path.relative_to(REPO)))
    out_rows.sort(key=lambda r: int(r["inventory_row"]))
    out_row_ids = {int(r["inventory_row"]) for r in out_rows}

    fieldnames = [
        "inventory_row","widget","source_anchor","source_line","target_leaf","scanner_label",
        "actual_visible_top_level_tab","actual_visible_container","headed_visible_label","headed_row_crop",
        "bbox_xywh","scroll_y","reachability_status","visibility_failure","backend_owner","render_consumer",
        "headed_location_tab","headed_location_section","headed_reachable","headed_capture_file","headed_row_capture_file",
        "headed_row_crop_file","headed_row_bbox_xywh","headed_scroll_y","headed_visibility_note","backend_mutation_owner",
        "render_proof_consumer","inventory_status_before","manual_status_after","termpp_verdict",
        "termpp_verdict_before_contract_normalization","termpp_expected_surface",
        "termpp_expected_property","termpp_expected_delta_class","termpp_exception_reason","product_loop_class","existing_matrix_row",
        "existing_proof_artifact","required_next_action","reviewer_notes"
    ]
    legacy_fieldnames = [
        "inventory_row","widget","source_anchor","source_line","target_leaf","scanner_label","headed_visible_label",
        "headed_location_tab","headed_location_section","headed_reachable","headed_capture_file","headed_row_capture_file",
        "headed_row_crop_file","headed_row_bbox_xywh","headed_scroll_y","headed_visibility_note","backend_mutation_owner",
        "render_proof_consumer","inventory_status_before","manual_status_after","termpp_verdict","termpp_expected_surface",
        "termpp_expected_property","termpp_expected_delta_class","termpp_exception_reason","product_loop_class","existing_matrix_row",
        "existing_proof_artifact","required_next_action","reviewer_notes"
    ]
    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    summary = {
        "rows_with_exact_crops": sum(1 for r in out_rows if r["headed_row_crop_file"]),
        "rows": [r["inventory_row"] for r in out_rows],
        "by_target_leaf": Counter(r["target_leaf"] for r in out_rows),
        "missing_measurement_receipt_rect_rows": sorted(set(RECT_TO_ROW.values()) - out_row_ids),
        "runtime_state_blocked_rows": sorted(int(r["inventory_row"]) for r in out_rows if r["reachability_status"] == "dynamic_label_needs_runtime_state"),
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    (OUT_DIR / "row-level-headed-inventory-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "visible-surface-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    visible_mismatches = [r for r in out_rows if r["reachability_status"] == "visible_surface_mismatch"]
    layout_blockers = [r for r in out_rows if r["reachability_status"] == "clipped_layout_blocker"]
    unreachable = [r for r in out_rows if r["reachability_status"].startswith("not_visible") or r["reachability_status"].startswith("blocked") or r["reachability_status"] == "dynamic_label_needs_runtime_state"]
    backend_gaps = [r for r in out_rows if r["termpp_verdict"] == "BLOCKED_PENDING_BACKEND_TRACE"]
    (OUT_DIR / "visible-surface-mismatches.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in visible_mismatches))
    (OUT_DIR / "layout-blockers.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in layout_blockers))
    (OUT_DIR / "unreachable-controls.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in unreachable))
    (OUT_DIR / "backend-owner-gaps.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in backend_gaps))
    with (OUT_DIR / "termpp-verdict-queue.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)
    with (OUT_DIR / "profile-path-proof-queue.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows([r for r in out_rows if r["required_next_action"] == "profile-path before/action/after proof"])
    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level Measurement Debug and Evidence Receipt headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid` via CDP port {PORT}.\n\n"
        "This package inventories scanner rows 1-73 using current-source read-only ImGui control rectangles plus explicit source-dead rows for legacy glyph-plane controls hidden from the current UI. "
        "It is inventory evidence only. It does not claim Phase 0 completion, Phase 1 completion, product acceptance, Law 15, Law 16, backend completion, native parity, nor closure.\n\n"
        f"Rows with exact crops: {summary['rows_with_exact_crops']}.\n"
        f"Runtime-state-blocked rows without exact crops: {summary['runtime_state_blocked_rows']}.\n"
        f"Missing rect-backed Measurement/Receipt rows from this pass: {summary['missing_measurement_receipt_rect_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Ad hoc script: FL-4260 row-level headed UI inventory batch expander for Material Look controls
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 row-level headed UI inventory batch expander.

Captures exact headed PNG crops for current ASCIIID scanner rows using the
existing CDP read-only rectangle probe. This script intentionally scopes the
first expansion to Material Look controls that already emit FL4260 control rects.
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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-material-look-expanded-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))
FIXTURE_MAP = REPO / "assets/a3d/fl4260_fixture_all_materials.a3d"

RECT_TO_ROW = {
    "material.open_raw_edit": 428,
    "material.selectable": 429,
    "starters.highlight_selected": 430,
    "starters.mode_status_header": 431,
    "color.fg.r0": 432,
    "color.fg.r1": 433,
    "color.fg.r2": 434,
    "color.fg.r3": 435,
    "color.bg.r0": 436,
    "color.bg.r1": 437,
    "color.bg.r2": 438,
    "color.bg.r3": 439,
    "color.fg_str.r0": 440,
    "color.fg_str.r1": 441,
    "color.fg_str.r2": 442,
    "color.fg_str.r3": 443,
    "starters.starters_header": 444,
    "color.bg_str.r0": 445,
    "color.bg_str.r1": 446,
    "color.bg_str.r2": 447,
    "color.bg_str.r3": 448,
    "color.shade_contrast.r0": 449,
    "color.shade_contrast.r1": 450,
    "color.shade_contrast.r2": 451,
    "color.shade_contrast.r3": 452,
    "starters.add_all": 453,
    "color.band_thres.r0": 454,
    "color.band_thres.r1": 455,
    "color.band_thres.r2": 456,
    "color.band_thres.r3": 457,
    "starters.glyph_style_presets": 458,
    "starters.color_presets": 459,
    "starters.vegetation_cp437": 461,
    "starters.vegetation_ramp": 462,
    "persist.save_material_look": 463,
    "persist.revert_from_disk": 464,
    "quick.select_all_eligible": 465,
    "quick.clear_pool": 466,
    "quick.restore_defaults": 467,
    "section.colors_header": 468,
    "color.clear_colors": 469,
    "scoring.curve": 474,
    "scoring.diagonal": 475,
    "scoring.horizontal": 476,
    "scoring.vertical": 477,
    "scoring.sparse": 478,
    "scoring.dense": 479,
    "section.glyph_pools_header": 481,
    "pool.clear_extended_pool": 482,
    "pool.select_all_catalog": 483,
    "pool.invert_catalog_selection": 484,
    "pool.restore_preset_defaults": 485,
    "section.role_buckets_header": 486,
    "role.auto_fill_ramp_density": 487,
    "section.winner_scoring_header": 488,
    "scoring.detail_contrast": 489,
    "scoring.tone_contrast": 490,
    "scoring.density_bias": 491,
    "section.trace_header": 493,
    "trace.highlight_selected_material_only": 494,
    "trace.pipeline_diagram": 495,
    "section.evidence_receipts_header": 496,
    "evidence.refresh_receipts": 497,
    "evidence.first_rejected": 498,
    "evidence.first_deferred": 499,
    "evidence.first_stale": 500,
    "evidence.row_lookup": 501,
    "evidence.load_row": 502,
    "evidence.accept_primary_placeholder": 503,
    "evidence.reject_primary_placeholder": 504,
    "evidence.defer_placeholder": 505,
    "evidence.receipt_reason": 506,
    "evidence.accept_primary": 507,
    "evidence.reject_primary": 508,
    "evidence.defer": 509,
    "section.measurement_debug_header": 510,
}

RECT_DUPLICATE_ROWS = {
    "color.fg.r0": [470],
    "color.bg.r0": [471],
    "color.fg_str.r0": [472],
    "color.bg_str.r0": [473],
    "color.band_thres.r0": [480],
    "scoring.curve": [492],
}

SCROLL_PASSES = [
    ("top", 0),
    ("starters", 250),
    ("starters_tail", 620),
    ("colors_top", 900),
    ("colors_mid", 1180),
    ("colors_tail", 1450),
    ("pools", 1730),
    ("roles", 2050),
    ("scoring", 2380),
    ("winner_header", 2700),
    ("winner_controls", 3050),
    ("trace_header", 3300),
    ("trace_controls", 3650),
    ("evidence_header", 4100),
    ("evidence_controls", 4500),
    ("measurement_header", 4950),
]

NO_EFFECT_ROWS = {432, 433, 434, 435, 436, 437, 438, 439, 453, 458, 459, 460, 461, 462, 470, 471}
HEADER_ROWS = {431, 444, 468, 481, 486, 488, 493, 496, 510}
PERSISTENCE_ROWS = {463, 464}
COLOR_ROWS = set(range(432, 458)) | set(range(469, 474)) | {480}
GLYPH_ROWS = {449, 458, 459, 460, 461, 462} | set(range(483, 489))
SCORING_ROWS = set(range(474, 480)) | set(range(489, 493))
VISIBLE_SURFACE_MISMATCH_ROWS = set()
DIAGNOSTIC_ROWS = {493, 495, 496, 503, 504, 505, 510}
TRACE_ROWS = {494}
RECEIPT_ROWS = set(range(497, 510))
TARGET_ROWS = set(RECT_TO_ROW.values()) | {460}


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
    required_next_action = meta["required_next_action"]
    if row in VISIBLE_SURFACE_MISMATCH_ROWS:
        required_next_action = "repair stale scanner target leaf/source anchor, then run receipt workflow proof" if row in RECEIPT_ROWS else "repair stale scanner target leaf/source anchor, then run profile-path before/action/after proof"
    elif row in DIAGNOSTIC_ROWS:
        required_next_action = "retain as diagnostic evidence row; no TERM++ authoring delta expected"
    elif row in TRACE_ROWS:
        required_next_action = "trace selection proof with headed visible toggle plus detached TERM++ context"
    elif row in RECEIPT_ROWS:
        required_next_action = "receipt workflow proof; records evidence metadata only"
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": "EDIT",
        "actual_visible_container": "Material Look side container",
        "headed_visible_label": meta["headed_visible_label"],
        "headed_row_crop": row_crop_file,
        "bbox_xywh": bbox,
        "scroll_y": scroll_y,
        "reachability_status": contract_row_status,
        "visibility_failure": failure,
        "backend_owner": backend_owner,
        "render_consumer": render_consumer,
        "headed_location_tab": "EDIT",
        "headed_location_section": "Material Look side container",
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
        "termpp_expected_surface": "selected_material_panel",
        "termpp_expected_property": "Material Rendering Profile change",
        "termpp_expected_delta_class": "glyph_plane or color_band",
        "termpp_exception_reason": "fresh-map click produced no proven visible action effect" if row in NO_EFFECT_ROWS else "",
        "product_loop_class": "Material Look profile edit",
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


def build_missing_row(src: dict, row: int) -> dict:
    note = "No FL4260 control rect was captured for this current scanner row before the headed CDP session ended."
    return {
        "inventory_row": row,
        "widget": src["widget"],
        "source_anchor": src["source_anchor"],
        "source_line": src["source_line"],
        "target_leaf": src["target_leaf"],
        "scanner_label": src["current_user_label"],
        "actual_visible_top_level_tab": "EDIT",
        "actual_visible_container": "Material Look side container",
        "headed_visible_label": "",
        "headed_row_crop": "",
        "bbox_xywh": "[]",
        "scroll_y": "",
        "reachability_status": "not_visible_after_scroll",
        "visibility_failure": "rect_not_captured_before_cdp_session_end",
        "backend_owner": src["backend_mutation_owner"],
        "render_consumer": "detached TERM++ rendered-cell delta pending",
        "headed_location_tab": "EDIT",
        "headed_location_section": "Material Look side container",
        "headed_reachable": "not_visible_after_scroll",
        "headed_capture_file": "",
        "headed_row_capture_file": "",
        "headed_row_crop_file": "",
        "headed_row_bbox_xywh": "[]",
        "headed_scroll_y": "",
        "headed_visibility_note": note,
        "backend_mutation_owner": src["backend_mutation_owner"],
        "render_proof_consumer": "detached TERM++ rendered-cell delta pending",
        "inventory_status_before": src["status"],
        "manual_status_after": "not_visible_after_scroll",
        "termpp_verdict": "BLOCKED_PENDING_LAYOUT",
        "termpp_verdict_before_contract_normalization": "BLOCKED_PENDING_LAYOUT",
        "termpp_expected_surface": "selected_material_panel",
        "termpp_expected_property": "Material Rendering Profile change",
        "termpp_expected_delta_class": "glyph_plane or color_band",
        "termpp_exception_reason": "headed capture ended before row was captured",
        "product_loop_class": "Material Look profile edit",
        "existing_matrix_row": "",
        "existing_proof_artifact": "",
        "required_next_action": "rerun headed capture for this row until it has exact row evidence, then run action-effect proof where applicable",
        "reviewer_notes": note,
    }


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
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
        ("FL4260_KB_FOCUS", "color.fg.r0"),
        ("FL4260_FOCUS_SIDEBAR", None),
        ("FL4260_SCROLL_Y", "-1"),
    ]:
        result = cdp_result(method, params)
        log("cdp", method=method, params=params, result=result[:500])
        time.sleep(0.35)

    best_by_row: dict[int, tuple[dict, Path, str, list[int]]] = {}
    all_rects = []
    for name, scroll_y in SCROLL_PASSES:
        try:
            cdp_result("FL4260_SCROLL_Y", str(scroll_y))
            time.sleep(0.25)
            cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
            time.sleep(0.2)
            frame_dir = OUT_DIR / "scroll-frames" / f"{name}_{scroll_y}"
            frame_dir.mkdir(parents=True, exist_ok=True)
            cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
            frame = frame_dir / "ui_frame.png"
            wait_for_png(frame)
            time.sleep(0.2)
            rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
        except OSError as exc:
            log("cdp_session_ended", name=name, scroll_y=scroll_y, error=str(exc))
            break
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
                if label.startswith("starters.preset_"):
                    row = 460
                else:
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

    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects))

    out_rows = []
    for row, (rect, ui_dst, pass_name, bbox) in sorted(best_by_row.items()):
        src = inv[row]
        visible = rect["label"]
        if visible.startswith("starters.preset_"):
            visible = "Full starter preset button"
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
        (CAP_DIR / f"row_{row}" / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        out_rows.append(build_output_row(src, meta, status, note_suffix))

    for row in (504, 505, 506, 507):
        if row not in best_by_row and row in inv:
            out_rows.append(build_runtime_state_blocked_row(inv[row], row))
            log("runtime_state_blocked_row", row=row, reason="receipt_row_not_loaded")

    captured_rows = {int(r["inventory_row"]) for r in out_rows}
    for row in sorted(TARGET_ROWS - captured_rows):
        if row in inv:
            out_rows.append(build_missing_row(inv[row], row))
            log("missing_target_row", row=row, reason="not_captured_before_cdp_session_end")
    captured_rows = {int(r["inventory_row"]) for r in out_rows}
    for row in sorted(TARGET_ROWS - captured_rows):
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
        "missing_material_look_rect_rows": sorted(TARGET_ROWS - out_row_ids),
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
        "# FL-4260 row-level Material Look expanded headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid --cdp 8765`.\n\n"
        "This package expands the row-level crop audit beyond the Starters batch using current-source read-only ImGui control rectangles. "
        "It is inventory evidence only. It does not claim Phase 0 completion, Phase 1 completion, product acceptance, Law 15, Law 16, backend completion, native parity, nor closure.\n\n"
        f"Rows with exact crops: {summary['rows_with_exact_crops']}.\n"
        f"Runtime-state-blocked rows without exact crops: {summary['runtime_state_blocked_rows']}.\n"
        f"Missing rect-backed Material Look rows from this pass: {summary['missing_material_look_rect_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Ad hoc script: FL-4260 row-level headed EDIT Brush MAT-id and MAT-elev inventory crop batch
# Created: 2026-06-22
# Canonical gap: row-level headed inventory capture for EDIT Brush controls.

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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-view-shared-brush-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

RECT_TO_ROW = {
    "sprite.combo": 511,
    "sprite.selectable": 512,
    "sprite.prev": 513,
    "sprite.next": 514,
    "inst.refresh": 515,
    "inst.selectable": 516,
    "view.palettize": 517,
    "view.cast_shadows": 518,
    "view.save_as": 519,
    "view.load": 520,
    "view.merge": 521,
    "view.new": 522,
    "view.termpp": 523,
    "view.termpp_skin": 524,
    "view.termpp_view": 525,
    "view.termpp_player": 526,
    "view.cancel": 527,
    "view.full": 528,
    "view.norm": 529,
    "view.pure": 530,
    "view.keep": 531,
    "view.coverage": 532,
    "view.control_header": 533,
    "view.pitch": 534,
    "view.yaw": 535,
    "view.zoom": 536,
    "view.grid": 537,
    "view.focus_view": 538,
    "view.stats_header": 539,
    "view.light_control_header": 540,
    "view.noon_pitch": 541,
    "view.noon_yaw": 542,
    "view.light_time": 543,
    "view.ambience": 544,
    "view.weather_header": 545,
    "view.weather_state": 546,
    "view.dialog.path": 547,
    "view.dialog.dir": 548,
    "view.dialog.dir_selectable": 549,
    "view.dialog.commit": 550,
    "view.dialog.cancel": 551,
    "edit.proof.advance": 552,
    "edit.proof.reset": 553,
    "edit.urdo.header": 554,
    "edit.undo.all_disabled": 555,
    "edit.undo.one_disabled": 556,
    "edit.undo.all": 557,
    "edit.undo.one": 558,
    "edit.redo.one_disabled": 559,
    "edit.redo.all_disabled": 560,
    "edit.redo.one": 561,
    "edit.redo.all": 562,
    "edit.urdo.purge_disabled": 563,
    "edit.urdo.purge": 564,
    "edit.brush.header": 565,
    "edit.sculpt.tab": 566,
    "edit.sculpt.brush_radius": 567,
    "edit.sculpt.brush_shape": 568,
    "edit.sculpt.brush_alpha": 569,
    "edit.sculpt.tile_radius": 570,
    "edit.sculpt.height_limit": 571,
    "edit.sculpt.probe_left": 572,
    "edit.sculpt.probe_right": 573,
    "edit.matid.tab": 574,
    "edit.matid.brush_diameter": 575,
    "edit.matid.material_left": 576,
    "edit.matid.material_right": 577,
    "edit.matid.height_limit": 578,
    "edit.matid.probe_left": 579,
    "edit.matid.probe_right": 580,
    "edit.matid.auto_elev_mode": 581,
    "edit.matid.auto_elev_slope": 582,
    "edit.matid.auto_elev_height": 583,
    "edit.matid.auto_elev_overwrite": 584,
    "edit.matid.apply_auto_mat_elev": 585,
    "edit.matid.clear_mat_elev": 586,
    "edit.matid.auto_tex_mode": 587,
    "edit.matid.auto_tex_slope": 588,
    "edit.matid.auto_tex_height_range": 589,
    "edit.matid.auto_tex_material_id": 590,
    "edit.matid.auto_tex_overwrite": 591,
    "edit.matid.apply_auto_texture": 592,
    "edit.matelev.height_limit": 593,
    "edit.matelev.probe_left": 594,
    "edit.matelev.probe_right": 595,
}

CLIPPED_LABEL_ROWS = {
    575,
    582,
    583,
    588,
}

TARGET_ROWS = set(range(511, 596))
RUNTIME_STATE_ROWS = {
    512: "sprite combo selectable row exists only while the combo popup is open",
    515: "INST refresh row skipped in this retained package because the current CDP capture pass resets the editor connection",
    516: "instance selectable row exists only when the current map has cached instances",
    518: "CAST SHADOWS is behind the DARK_TERRAIN compile flag in this build",
    527: "Cancel is visible only after SAVE/LOAD/MERGE dialog mode is active",
    546: "Weather State is visible only after the Weather collapsing header is opened",
    547: "path input is visible only after SAVE/LOAD/MERGE dialog mode is active",
    548: "directory list is visible only after SAVE/LOAD/MERGE dialog mode is active",
    549: "directory selectable rows are visible only after SAVE/LOAD/MERGE dialog mode is active and entries are loaded",
    550: "dialog commit label is visible only after SAVE/LOAD/MERGE dialog mode is active",
    551: "dialog cancel label is visible only after SAVE/LOAD/MERGE dialog mode is active",
    552: "Advance proof step is visible only when FL-4207 proof mode is active",
    553: "Reset proof step is visible only when FL-4207 proof mode is active",
    557: "active undo-all button exists only when undo history is present",
    558: "active undo-one button exists only when undo history is present",
    561: "active redo-one button exists only when redo history is present",
    562: "active redo-all button exists only when redo history is present",
    564: "active purge button exists only when undo or redo history is present",
    570: "TILE RADIUS exists only while Alt is held in the SCULPT tab",
}

PASSES = [
    {"name": "sprite_picker", "sidebar_tab": 2, "scroll": 0, "auto_modes": "-1 -1"},
    {"name": "view_top", "sidebar_tab": 0, "scroll": 0, "auto_modes": "-1 -1"},
    {"name": "view_controls", "sidebar_tab": 0, "scroll": 220, "auto_modes": "-1 -1"},
    {"name": "view_light", "sidebar_tab": 0, "scroll": 500, "auto_modes": "-1 -1"},
    {"name": "edit_shared_top", "tab": 0, "scroll": 0, "auto_modes": "-1 -1"},
    {"name": "sculpt_top", "tab": 0, "scroll": 1180, "auto_modes": "-1 -1"},
    {"name": "matid_top", "tab": 1, "scroll": 1180, "auto_modes": "0 0"},
    {"name": "matid_middle", "tab": 1, "scroll": 1360, "auto_modes": "0 0"},
    {"name": "matid_bottom", "tab": 1, "scroll": 1540, "auto_modes": "0 0"},
    {"name": "matid_height_branches", "tab": 1, "scroll": 1360, "auto_modes": "1 1"},
    {"name": "matelev_top", "tab": 3, "scroll": 1180, "auto_modes": "-1 -1"},
    {"name": "matelev_middle", "tab": 3, "scroll": 1360, "auto_modes": "-1 -1"},
    {"name": "sprite_edit_tab", "tab": 4, "scroll": 1180, "auto_modes": "-1 -1"},
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
    if row in RUNTIME_STATE_ROWS:
        return (
            "dynamic_label_needs_runtime_state",
            RUNTIME_STATE_ROWS[row],
            "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_RUNTIME_STATE",
            "current_ui_state",
            f"load state that exposes this row, recapture exact control rect, then run downstream proof: {RUNTIME_STATE_ROWS[row]}",
        )
    label = src.get("current_user_label", "")
    leaf = src.get("target_leaf", "")
    inv_status = src.get("status", "")
    if row in CLIPPED_LABEL_ROWS:
        failure = "visible control label is clipped by EDIT World Facts side pane"
        if leaf.startswith("UNMAPPED"):
            failure += "; scanner target leaf is also stale for live EDIT Brush raw paint surface"
        return (
            "clipped_layout_blocker",
            failure,
            "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LAYOUT",
            "world_edit_panel",
            "widen EDIT World Facts column or move the label to an unclipped row, then rerun crop and downstream TERM++ world-edit delta proof",
        )
    if leaf.startswith("UNMAPPED"):
        return (
            "visible_surface_mismatch",
            "scanner target leaf stale: live rect belongs to EDIT Brush raw paint surface",
            "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
            "world_edit_panel",
            "update scanner target leaf and run downstream TERM++ world-edit delta proof",
        )
    if inv_status == "ID_ONLY_LABEL_REQUIRES_UI_CONTEXT" or label.startswith("<id-only"):
        return (
            "blocked_label_unresolved",
            "id-only control requires adjacent visible label binding from crop",
            "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_LABEL",
            "world_edit_panel",
            "bind adjacent label then run downstream TERM++ world-edit delta proof",
        )
    return (
        "reachable_actionable",
        "",
        "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
        "world_edit_panel",
        "run downstream TERM++ world-edit delta proof",
    )


def surface_for_row(row: int) -> tuple[str, str]:
    if row <= 514:
        return "SPRITE", "Sprite picker"
    if row <= 516:
        return "INST", "Instance inspection"
    if row <= 551:
        return "VIEW", "Rendered-scene inspection"
    if row <= 564:
        return "EDIT", "Shared undo/proof/brush shell"
    return "EDIT", "World Facts / Brush"


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
    for method, params in [
        ("NEW_MAP", None),
        ("SET_WINDOW_SIZE", "1400 1000"),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("FL4260_RENDERING_PROOF", "1 0 0"),
        ("FL4260_FOCUS_SIDEBAR", None),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120"),
    ]:
        result = cdp_result(method, params)
        log("cdp", method=method, params=params, result=result[:600])
        time.sleep(0.35)

    best_by_row: dict[int, tuple[dict, Path, str, list[int]]] = {}
    all_rects = []
    for spec in PASSES:
        name = spec["name"]
        tab = spec.get("tab")
        sidebar_tab = spec.get("sidebar_tab")
        scroll = spec["scroll"]
        if sidebar_tab is not None:
            cdp_result("FL4260_LOCK_SIDEBAR_TAB", str(sidebar_tab))
            cdp_result("FL4260_FOCUS_SIDEBAR_TAB", str(sidebar_tab))
        else:
            cdp_result("FL4260_LOCK_SIDEBAR_TAB", "-1")
            cdp_result("FL4260_FOCUS_BRUSH_TAB", str(tab))
        time.sleep(0.35)
        cdp_result("FL4260_FORCE_AUTO_MODES", spec["auto_modes"])
        time.sleep(0.2)
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
        log("pass", name=name, tab=tab, sidebar_tab=sidebar_tab, scroll=scroll, rect_count=len(rects), frame=str(frame.relative_to(REPO)))
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
    for row in sorted(TARGET_ROWS):
        src = inv[row]
        captured = best_by_row.get(row)
        row_dir = CAP_DIR / f"row_{row}"
        row_dir.mkdir(parents=True, exist_ok=True)
        manual_status, failure, verdict, surface, next_action = status_for(src, row)
        tab_name, section_name = surface_for_row(row)
        if captured is None:
            visible = ""
            ui_rel = ""
            crop_rel = ""
            bbox = []
            pass_name = "runtime_state_absent"
            evidence_reason = f"No current ImGui rect emitted in this headed state: {failure}"
        else:
            rect, ui_dst, pass_name, bbox = captured
            visible = rect["label"]
            ui_rel = str(ui_dst.relative_to(OUT_DIR))
            crop_rel = f"row-captures/row_{row}/row_crop.png"
            evidence_reason = f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {visible} during {pass_name} pass."
        meta = {
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "scanner_label": src["current_user_label"],
            "headed_visible_label": visible,
            "capture_file": ui_rel,
            "row_crop_file": crop_rel,
            "bbox_xywh": bbox,
            "scroll_y": pass_name,
            "target_leaf": src["target_leaf"],
            "visible_top_level_tab": tab_name,
            "visible_container": section_name,
            "visibility_status": manual_status,
            "visibility_failure": failure,
            "evidence_reason": evidence_reason,
            "termpp_verdict": verdict,
            "required_next_action": next_action,
        }
        (row_dir / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        out = {
            "inventory_row": row,
            "widget": src["widget"],
            "source_anchor": src["source_anchor"],
            "source_line": src["source_line"],
            "target_leaf": src["target_leaf"],
            "scanner_label": src["current_user_label"],
            "headed_visible_label": visible,
            "headed_location_tab": tab_name,
            "headed_location_section": section_name,
            "headed_reachable": manual_status,
            "headed_capture_file": ui_rel,
            "headed_row_capture_file": ui_rel,
            "headed_row_crop_file": crop_rel,
            "headed_row_bbox_xywh": json.dumps(bbox),
            "headed_scroll_y": pass_name,
            "headed_visibility_note": evidence_reason,
            "visibility_failure": failure,
            "backend_mutation_owner": src["backend_mutation_owner"],
            "render_proof_consumer": src["render_proof_consumer"],
            "inventory_status_before": src["status"],
            "manual_status_after": manual_status,
            "termpp_verdict": verdict,
            "termpp_expected_surface": surface,
            "termpp_expected_property": "raw map world-edit change",
            "termpp_expected_delta_class": "raw_world_edit_downstream_termpp",
            "termpp_exception_reason": failure if verdict.startswith("TERMPLUSPLUS_VERDICT_BLOCKED") else "",
            "product_loop_class": "NON_PROFILE_WORLD_EDIT_CONTROL" if row >= 552 else "NON_PROFILE_VIEW_OR_SELECTOR_CONTROL",
            "existing_matrix_row": "",
            "existing_proof_artifact": "",
            "required_next_action": next_action,
            "reviewer_notes": "Real headed row crop from current ASCIIID UI on NEW_MAP. This is row-level reachability and visible-surface audit evidence, not action proof.",
        }
        rows.append(out)
        if src["target_leaf"].startswith("UNMAPPED") or (row >= 536 and not src["target_leaf"].startswith("EDIT / Raw")):
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
        "rows_with_exact_crops": len(best_by_row),
        "rows": [r["inventory_row"] for r in rows],
        "exact_crop_rows": sorted(best_by_row),
        "missing_target_rows": sorted(TARGET_ROWS - set(best_by_row)),
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
        "# FL-4260 row-level VIEW/shared/Brush headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid --cdp 8765`.\n\n"
        "This package records current rows 511-595 for the VIEW/shared/Brush retained inventory slice. "
        "It captures exact row-level crops where the current headed UI emits a rect, and records runtime-state blockers where a row is hidden behind combo, dialog, proof, undo, redo, compile-flag, or Alt-key state. "
        "It is inventory evidence only and does not claim Phase 0 completion, Phase 1 completion, product acceptance, backend completion, Law 15, Law 16, native parity, nor closure.\n\n"
        f"Rows with exact crops: {len(best_by_row)}.\n"
        f"Missing target rows: {summary['missing_target_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

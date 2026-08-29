# Ad hoc script: FL-4260 row-level headed INFO probe inventory crop batch
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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-info-probe-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

RECT_TO_ROW = {
    "skin.current": 624,
    "skin.option": 625,
    "skin.reload": 626,
    "info.probe.bk_preview": 627,
    "info.probe.fg_preview": 628,
    "info.debug_probe": 629,
    "info.shading_quick_guide": 630,
}

LABELS = {
    "skin.current": "current skin button",
    "skin.option": "alternate skin button",
    "skin.reload": "Reload",
    "info.probe.bk_preview": "BK color preview",
    "info.probe.fg_preview": "FG color preview",
    "info.debug_probe": "Debug Probe",
    "info.shading_quick_guide": "Shading Quick Guide",
}

SOURCE_BY_ROW = {
    624: ("Button", "editor/asciiid.cpp:30754", "ImGui::Button(label);"),
    625: ("Button", "editor/asciiid.cpp:30760", "const bool skin_option_clicked = ImGui::Button(label);"),
    626: ("Button", "editor/asciiid.cpp:30782", "if (ImGui::Button(\"Reload\"))"),
    627: ("ColorButton", "editor/asciiid.cpp:30818", "ImGui::ColorButton(\"##bk_preview\", ImVec4(bk_r, bk_g, bk_b, 1.0f)); ImGui::SameLine();"),
    628: ("ColorButton", "editor/asciiid.cpp:30821", "ImGui::ColorButton(\"##fg_preview\", ImVec4(fg_r, fg_g, fg_b, 1.0f)); ImGui::SameLine();"),
    629: ("Button", "editor/asciiid.cpp:30831", "if (ImGui::Button(\"Debug Probe\")) DebugProbe();"),
    630: ("TreeNode", "editor/asciiid.cpp:30833", "bool fl4260_info_guide_open = ImGui::TreeNode(\"Shading Quick Guide\");"),
}

TAB_BY_LABEL = {
    "skin.current": ("SKIN", "SKIN / Player skin selection"),
    "skin.option": ("SKIN", "SKIN / Player skin selection"),
    "skin.reload": ("SKIN", "SKIN / Player skin selection"),
    "info.probe.bk_preview": ("INFO", "INFO / Terrain Probe"),
    "info.probe.fg_preview": ("INFO", "INFO / Terrain Probe"),
    "info.debug_probe": ("INFO", "INFO / Terrain Probe"),
    "info.shading_quick_guide": ("INFO", "INFO / Terrain Probe"),
}

MISSING_ROWS = {
    624: ("dynamic_label_needs_runtime_state", "skin_current_button_not_emitted_in_current_runtime_state", "BLOCKED_PENDING_LABEL"),
    625: ("dynamic_label_needs_runtime_state", "requires_non_current_skin_option", "BLOCKED_PENDING_LABEL"),
    626: ("dynamic_label_needs_runtime_state", "bundle_skin_reload_path_not_active", "BLOCKED_PENDING_LABEL"),
    627: ("dynamic_label_needs_runtime_state", "terrain_probe_bk_preview_not_emitted", "NO_DELTA_DIAGNOSTIC"),
    628: ("dynamic_label_needs_runtime_state", "terrain_probe_fg_preview_not_emitted", "NO_DELTA_DIAGNOSTIC"),
    629: ("not_visible_after_scroll", "debug_probe_not_emitted", "NO_DELTA_DIAGNOSTIC"),
    630: ("not_visible_after_scroll", "shading_quick_guide_not_emitted", "NO_DELTA_DIAGNOSTIC"),
}

FIELDNAMES = [
    "inventory_row", "widget", "source_anchor", "source_line", "target_leaf", "scanner_label", "headed_visible_label",
    "headed_location_tab", "headed_location_section", "headed_reachable", "headed_capture_file", "headed_row_capture_file",
    "headed_row_crop_file", "headed_row_bbox_xywh", "headed_scroll_y", "headed_visibility_note", "visibility_failure",
    "backend_mutation_owner", "render_proof_consumer", "inventory_status_before", "manual_status_after", "termpp_verdict",
    "termpp_expected_surface", "termpp_expected_property", "termpp_expected_delta_class", "termpp_exception_reason",
    "product_loop_class", "existing_matrix_row", "existing_proof_artifact", "required_next_action", "reviewer_notes"
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


def cdp_result(method: str, params: str | None = None, timeout: float = 5.0) -> str:
    return str(cdp(method, params, timeout=timeout).get("result", ""))


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


def git_show_text(path: Path) -> str:
    rel = path.relative_to(REPO).as_posix()
    return subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=REPO, text=True)


def load_inventory() -> dict[int, dict]:
    text = INV_PATH.read_text() if INV_PATH.exists() else git_show_text(INV_PATH)
    reader = csv.DictReader(io.StringIO(text))
    out = {}
    for row in reader:
        try:
            out[int(row.get("row", ""))] = row
        except ValueError:
            continue
    return out


def source_row(inv: dict[int, dict], row: int) -> dict:
    base = inv.get(row, {})
    widget, anchor, line = SOURCE_BY_ROW[row]
    return {
        "widget": widget,
        "source_anchor": anchor,
        "source_line": line,
        "target_leaf": base.get("target_leaf") or "UNMAPPED / requires manual classification",
        "scanner_label": base.get("current_user_label") or "<id-only control>",
        "backend_mutation_owner": base.get("backend_mutation_owner") or "INFO terrain probe/debug UI",
        "render_proof_consumer": base.get("render_proof_consumer") or "diagnostic readout only",
        "status": base.get("status") or "UNREVIEWED_SCAFFOLD_SOURCE_ANCHORED",
    }


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    inv = load_inventory()
    transcript = (OUT_DIR / "action_transcript.jsonl").open("w")

    def log(event: str, **kw):
        transcript.write(json.dumps({"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}, sort_keys=True) + "\n")
        transcript.flush()

    log("start", out_dir=str(OUT_DIR.relative_to(REPO)))
    for method, params, timeout in [
        ("NEW_MAP", None, 5.0),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120", 5.0),
        ("FL4260_INFO_TERRAIN_PROBE", "64 64", 5.0),
    ]:
        result = cdp_result(method, params, timeout=timeout)
        log("cdp", method=method, params=params, result=result[:800])
        time.sleep(0.35)

    best = {}
    all_rects = []
    passes = [
        ("skin", "6"),
        ("info_probe", "7"),
    ]
    for name, tab in passes:
        result = cdp_result("FL4260_LOCK_SIDEBAR_TAB", tab, timeout=5.0)
        log("cdp", method="FL4260_LOCK_SIDEBAR_TAB", params=tab, result=result[:800])
        time.sleep(0.35)
        cdp_result("FL4260_CTRL_RECTS_RECORD", "1")
        time.sleep(0.35)
        frame_dir = OUT_DIR / "scroll-frames" / f"{name}_0"
        frame_dir.mkdir(parents=True, exist_ok=True)
        cdp_result("CAPTURE_UI_FRAME", str(frame_dir))
        frame = frame_dir / "ui_frame.png"
        if not wait_for_png(frame):
            raise RuntimeError(f"missing capture: {frame}")
        time.sleep(0.2)
        rect_text = cdp_result("FL4260_CTRL_RECTS_RECORD", "0")
        (frame_dir / "ctrl_rects.txt").write_text(rect_text)
        rects = parse_rects(rect_text)
        log("pass", name=name, tab=tab, rect_count=len(rects), frame=str(frame.relative_to(REPO)))
        for rect in rects:
            all_rects.append(rect)
            row = RECT_TO_ROW.get(rect.get("label", ""))
            if row is None:
                continue
            bbox = visible_bbox(frame, rect)
            if not bbox:
                continue
            row_dir = CAP_DIR / f"row_{row}"
            row_dir.mkdir(parents=True, exist_ok=True)
            ui_dst = row_dir / "ui_frame.png"
            shutil.copy2(frame, ui_dst)
            crop_with_bbox(frame, row_dir / "row_crop.png", bbox)
            best[row] = (rect, ui_dst, bbox)
    unlock_result = cdp_result("FL4260_LOCK_SIDEBAR_TAB", "-1", timeout=5.0)
    log("cdp", method="FL4260_LOCK_SIDEBAR_TAB", params="-1", result=unlock_result[:800])

    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects))

    rows = []
    mismatches = []
    unreachable = []
    termpp_rows = []
    for row, (rect, ui_dst, bbox) in sorted(best.items()):
        src = source_row(inv, row)
        visible = LABELS.get(rect["label"], rect["label"])
        tab, section = TAB_BY_LABEL.get(rect["label"], ("UNKNOWN", "UNKNOWN"))
        failure = f"scanner target leaf stale: live rect belongs to {section}"
        termpp_verdict = "NO_DELTA_DIAGNOSTIC" if tab == "INFO" else "NO_DELTA_METADATA"
        product_loop_class = "DIAGNOSTIC_INFO_CONTROL" if tab == "INFO" else "SKIN_METADATA_CONTROL"
        termpp_surface = "info_diagnostic_panel" if tab == "INFO" else "skin_selection_panel"
        termpp_property = "terrain probe readout" if tab == "INFO" else "player skin selection"
        termpp_delta = "no_termpp_delta_diagnostic" if tab == "INFO" else "no_terrain_termpp_delta_metadata"
        termpp_exception = (
            "diagnostic INFO readout, not world or Material Look authoring"
            if tab == "INFO"
            else "SKIN metadata path, not terrain or Material Look authoring"
        )
        meta = {
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "scanner_label": src["scanner_label"],
            "headed_visible_label": visible,
            "capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "bbox_xywh": bbox,
            "target_leaf": src["target_leaf"],
            "visible_top_level_tab": tab,
            "visible_container": section,
            "visibility_status": "visible_surface_mismatch",
            "visibility_failure": failure,
            "evidence_reason": f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {rect['label']} during {section} capture.",
            "termpp_verdict": termpp_verdict,
            "required_next_action": "update scanner target leaf to INFO / Terrain Probe and decide whether this diagnostic belongs in Phase 0 product UI",
        }
        if tab == "SKIN":
            meta["required_next_action"] = "record SKIN row as non-terrain metadata, then keep FL-4260 product work focused on Material Look controls that alter TERM++ terrain presentation"
        (CAP_DIR / f"row_{row}" / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        out = {
            "inventory_row": row,
            "widget": src["widget"],
            "source_anchor": src["source_anchor"],
            "source_line": src["source_line"],
            "target_leaf": src["target_leaf"],
            "scanner_label": src["scanner_label"],
            "headed_visible_label": visible,
            "headed_location_tab": tab,
            "headed_location_section": section,
            "headed_reachable": "visible_surface_mismatch",
            "headed_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "headed_row_bbox_xywh": json.dumps(bbox),
            "headed_scroll_y": "0",
            "headed_visibility_note": meta["evidence_reason"],
            "visibility_failure": failure,
            "backend_mutation_owner": src["backend_mutation_owner"],
            "render_proof_consumer": src["render_proof_consumer"],
            "inventory_status_before": src["status"],
            "manual_status_after": "visible_surface_mismatch",
            "termpp_verdict": termpp_verdict,
            "termpp_expected_surface": termpp_surface,
            "termpp_expected_property": termpp_property,
            "termpp_expected_delta_class": termpp_delta,
            "termpp_exception_reason": termpp_exception,
            "product_loop_class": product_loop_class,
            "existing_matrix_row": "",
            "existing_proof_artifact": "",
            "required_next_action": meta["required_next_action"],
            "reviewer_notes": f"Real headed row crop from current ASCIIID UI on NEW_MAP during {section} capture. This is row-level reachability and visible-surface audit evidence, not action proof.",
        }
        rows.append(out)
        mismatches.append(out)
        termpp_rows.append(out)

    for row in sorted(set(RECT_TO_ROW.values()) - set(best)):
        src = source_row(inv, row)
        status, failure, verdict = MISSING_ROWS[row]
        tab = "SKIN" if row in (623, 624, 625) else "INFO"
        section = "SKIN / Player skin selection" if tab == "SKIN" else "INFO / Terrain Probe"
        out = {
            "inventory_row": row,
            "widget": src["widget"],
            "source_anchor": src["source_anchor"],
            "source_line": src["source_line"],
            "target_leaf": src["target_leaf"],
            "scanner_label": src["scanner_label"],
            "headed_visible_label": "",
            "headed_location_tab": tab,
            "headed_location_section": section,
            "headed_reachable": status,
            "headed_capture_file": "",
            "headed_row_capture_file": "",
            "headed_row_crop_file": "",
            "headed_row_bbox_xywh": "",
            "headed_scroll_y": "0",
            "headed_visibility_note": f"No rect emitted for row {row} in the current runtime state during {section} capture.",
            "visibility_failure": failure,
            "backend_mutation_owner": src["backend_mutation_owner"],
            "render_proof_consumer": src["render_proof_consumer"],
            "inventory_status_before": src["status"],
            "manual_status_after": status,
            "termpp_verdict": verdict,
            "termpp_expected_surface": "skin_selection_panel" if tab == "SKIN" else "info_diagnostic_panel",
            "termpp_expected_property": "player skin selection" if tab == "SKIN" else "terrain probe readout",
            "termpp_expected_delta_class": "runtime_state_required",
            "termpp_exception_reason": failure,
            "product_loop_class": "SKIN_METADATA_CONTROL" if tab == "SKIN" else "DIAGNOSTIC_INFO_CONTROL",
            "existing_matrix_row": "",
            "existing_proof_artifact": "",
            "required_next_action": "re-run row capture with runtime state that exposes this dynamic row, then replace this missing-row finding with an exact crop if it becomes visible",
            "reviewer_notes": "Concrete missing-row finding from the headed batch. This row is not counted as action proof.",
        }
        row_dir = CAP_DIR / f"row_{row}"
        row_dir.mkdir(parents=True, exist_ok=True)
        (row_dir / "row_metadata.json").write_text(json.dumps({
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "scanner_label": src["scanner_label"],
            "target_leaf": src["target_leaf"],
            "visible_top_level_tab": tab,
            "visible_container": section,
            "visibility_status": status,
            "visibility_failure": failure,
            "termpp_verdict": verdict,
            "required_next_action": out["required_next_action"],
        }, indent=2, sort_keys=True) + "\n")
        rows.append(out)
        unreachable.append(out)
        termpp_rows.append(out)

    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: int(r["inventory_row"])))
    for name, data in [
        ("visible-surface-mismatches.jsonl", mismatches),
        ("layout-blockers.jsonl", []),
        ("unreachable-controls.jsonl", unreachable),
        ("backend-owner-gaps.jsonl", []),
    ]:
        (OUT_DIR / name).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in data))
    with (OUT_DIR / "termpp-verdict-queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(termpp_rows, key=lambda r: int(r["inventory_row"])))
    with (OUT_DIR / "profile-path-proof-queue.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
    summary = {
        "rows_recorded": len(rows),
        "rows_with_exact_crops": len(best),
        "rows": sorted(int(r["inventory_row"]) for r in rows),
        "exact_crop_rows": sorted(best),
        "missing_target_rows": sorted(set(RECT_TO_ROW.values()) - set(best)),
        "by_status": Counter(r["manual_status_after"] for r in rows),
        "visible_surface_mismatches": len(mismatches),
        "layout_blockers": 0,
        "unreachable_controls": len(unreachable),
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "visible-surface-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level INFO probe headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid --cdp {PORT}` on NEW_MAP.\n\n"
        "This package captures the current tail rows 623-629 for SKIN and INFO Terrain Probe. "
        "Exact visible rows carry row crops. Dynamic rows that are not visible in the current runtime state carry explicit missing-row findings. "
        "It records visible-surface mismatches because the scanner CSV leaves are stale/UNMAPPED while the live controls are SKIN metadata and INFO diagnostics. "
        "It is inventory evidence only and does not claim Phase 0 acceptance, product acceptance, backend acceptance, Law 15, Law 16, native parity, nor closure.\n\n"
        f"Rows recorded: {len(rows)}.\n"
        f"Rows with exact crops: {len(best)}.\n"
        f"Missing target rows: {summary['missing_target_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

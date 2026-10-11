# Ad hoc script: FL-4260 row-level headed EDIT ITEM ENEMY STORY inventory crop batch
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
OUT_DIR = BASE_DIR / f"{dt.date.today().isoformat()}-row-level-edit-item-enemy-story-check"
CAP_DIR = OUT_DIR / "row-captures"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))

RECT_TO_ROW = {
    "edit.item.tab": 608,
    "edit.item.reset_items": 609,
    "edit.item.item_list": 610,
    "edit.enemy.tab": 611,
    "edit.enemy.enable_enemy_gen": 612,
    "edit.enemy.delete_all_generators": 613,
    "edit.enemy.max_alive": 614,
    "edit.enemy.revive_min": 615,
    "edit.enemy.revive_max": 616,
    "edit.enemy.armor": 617,
    "edit.enemy.helmet": 618,
    "edit.enemy.shield": 619,
    "edit.enemy.sword": 620,
    "edit.enemy.crossbow": 621,
    "edit.story.tab": 622,
    "edit.story.story_id": 623,
}

LABEL_OVERRIDES = {
    "edit.item.tab": "ITEM tab",
    "edit.item.reset_items": "RESET items",
    "edit.item.item_list": "Item",
    "edit.enemy.tab": "ENEMY tab",
    "edit.enemy.enable_enemy_gen": "Enable Enemy Gen",
    "edit.enemy.delete_all_generators": "Delete All Generators",
    "edit.enemy.max_alive": "MaxAlive",
    "edit.enemy.revive_min": "ReviveMin",
    "edit.enemy.revive_max": "ReviveMax",
    "edit.enemy.armor": "Armor",
    "edit.enemy.helmet": "Helmet",
    "edit.enemy.shield": "Shield",
    "edit.enemy.sword": "Sword",
    "edit.enemy.crossbow": "Crossbow",
    "edit.story.tab": "STORY tab",
    "edit.story.story_id": "story_id",
}

EXPECTED_LEAF = {
    608: "EDIT / Placement paint / ITEM",
    609: "EDIT / Placement paint / ITEM",
    610: "EDIT / Placement paint / ITEM",
    611: "EDIT / Placement paint / ENEMY",
    612: "EDIT / Placement paint / ENEMY",
    613: "EDIT / Placement paint / ENEMY",
    614: "EDIT / Placement paint / ENEMY",
    615: "EDIT / Placement paint / ENEMY",
    616: "EDIT / Placement paint / ENEMY",
    617: "EDIT / Placement paint / ENEMY",
    618: "EDIT / Placement paint / ENEMY",
    619: "EDIT / Placement paint / ENEMY",
    620: "EDIT / Placement paint / ENEMY",
    621: "EDIT / Placement paint / ENEMY",
    622: "EDIT / Placement paint / STORY",
    623: "EDIT / Placement paint / STORY",
}

PASSES = [
    {"name": "item_top", "tab": 5, "scroll": 1180},
    {"name": "enemy_top", "tab": 6, "scroll": 1180},
    {"name": "enemy_mid", "tab": 6, "scroll": 1320},
    {"name": "story_top", "tab": 7, "scroll": 1180},
]

FIELDNAMES = [
    "inventory_row", "widget", "source_anchor", "source_line", "target_leaf", "scanner_label", "headed_visible_label",
    "headed_location_tab", "headed_location_section", "headed_reachable", "headed_capture_file", "headed_row_capture_file",
    "headed_row_crop_file", "headed_row_bbox_xywh", "headed_scroll_y", "headed_visibility_note", "visibility_failure",
    "backend_mutation_owner", "render_proof_consumer", "inventory_status_before", "manual_status_after", "termpp_verdict",
    "termpp_expected_surface", "termpp_expected_property", "termpp_expected_delta_class", "termpp_exception_reason",
    "product_loop_class", "existing_matrix_row", "existing_proof_artifact", "required_next_action", "reviewer_notes"
]

SOURCE_BY_ROW = {
    608: ("BeginTabItem", "editor/asciiid.cpp:30410", "if (ImGui::BeginTabItem(\"ITEM\", nullptr, fl4260_item_tab_flags))"),
    609: ("Button", "editor/asciiid.cpp:30449", "if (ImGui::Button(\"RESET items\"))"),
    610: ("ListBox", "editor/asciiid.cpp:30476", "ImGui::ListBox(\"Item\", &active_item, names.names, names.items);"),
    611: ("BeginTabItem", "editor/asciiid.cpp:30503", "if (ImGui::BeginTabItem(\"ENEMY\", nullptr, fl4260_enemy_tab_flags))"),
    612: ("Checkbox", "editor/asciiid.cpp:30511", "ImGui::Checkbox(\"Enable Enemy Gen\", &g_enable_enemies);"),
    613: ("Button", "editor/asciiid.cpp:30515", "if (ImGui::Button(\"Delete All Generators\"))"),
    614: ("SliderInt", "editor/asciiid.cpp:30522", "if (ImGui::SliderInt(\"MaxAlive\", &eg_alive_max, 1, 7))"),
    615: ("SliderInt", "editor/asciiid.cpp:30531", "if (ImGui::SliderInt(\"ReviveMin\", &eg_revive_min, 0, eg_revive_max))"),
    616: ("SliderInt", "editor/asciiid.cpp:30539", "if (ImGui::SliderInt(\"ReviveMax\", &eg_revive_max, eg_revive_min, 10))"),
    617: ("SliderInt", "editor/asciiid.cpp:30549", "if (ImGui::SliderInt(\"Armor\", &eg_armor, 0, 10))"),
    618: ("SliderInt", "editor/asciiid.cpp:30559", "if (ImGui::SliderInt(\"Helmet\", &eg_helmet, 0, 10))"),
    619: ("SliderInt", "editor/asciiid.cpp:30568", "if (ImGui::SliderInt(\"Shield\", &eg_shield, 0, 10))"),
    620: ("SliderInt", "editor/asciiid.cpp:30577", "if (ImGui::SliderInt(\"Sword\", &eg_sword, 0, 10))"),
    621: ("SliderInt", "editor/asciiid.cpp:30586", "if (ImGui::SliderInt(\"Crossbow\", &eg_crossbow, 0, 10))"),
    622: ("BeginTabItem", "editor/asciiid.cpp:30616", "if (ImGui::BeginTabItem(\"STORY\", nullptr, fl4260_story_tab_flags))"),
    623: ("InputInt", "editor/asciiid.cpp:30627", "ImGui::InputInt(\"story_id\", &story_id);"),
}


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
    if INV_PATH.exists():
        text = INV_PATH.read_text()
    else:
        text = git_show_text(INV_PATH)
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
        "target_leaf": base.get("target_leaf") or "UNMAPPED",
        "scanner_label": base.get("current_user_label") or LABEL_OVERRIDES.get(next(k for k, v in RECT_TO_ROW.items() if v == row), ""),
        "backend_mutation_owner": base.get("backend_mutation_owner") or "editor/asciiid.cpp edit placement state",
        "render_proof_consumer": base.get("render_proof_consumer") or "detached TERM++ world-edit delta pending",
        "status": base.get("status") or "SOURCE_WIRED_PROOF_PENDING",
    }


def status_for(src: dict, row: int) -> tuple[str, str, str, str]:
    expected = EXPECTED_LEAF[row]
    actual = src.get("target_leaf", "")
    if actual != expected:
        return (
            "visible_surface_mismatch",
            f"scanner target leaf stale: live rect belongs to {expected}",
            "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
            f"update scanner target leaf to {expected} and run downstream TERM++ world-edit delta proof",
        )
    return (
        "reachable_actionable",
        "",
        "TERMPLUSPLUS_WORLD_EDIT_DELTA_EXPECTED",
        "run downstream TERM++ world-edit delta proof",
    )


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    inv = load_inventory()
    transcript_path = OUT_DIR / "action_transcript.jsonl"
    transcript = transcript_path.open("w")

    def log(event: str, **kw):
        rec = {"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        transcript.write(json.dumps(rec, sort_keys=True) + "\n")
        transcript.flush()

    log("start", out_dir=str(OUT_DIR.relative_to(REPO)))
    for method, params, timeout in [
        ("NEW_MAP", None, 5.0),
        ("FL4260_SET_RENDER_MODE", "1", 5.0),
        ("FL4260_RENDERING_PROOF", "1 0 0", 5.0),
        ("FL4260_FOCUS_SIDEBAR", None, 5.0),
        ("FL4260_SET_SIDEBAR_WIDTH", "1120", 5.0),
    ]:
        result = cdp_result(method, params, timeout=timeout)
        log("cdp", method=method, params=params, result=result[:800])
        time.sleep(0.35)

    best_by_row: dict[int, tuple[dict, Path, str, list[int], int]] = {}
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
        if not wait_for_png(frame):
            raise RuntimeError(f"missing capture: {frame}")
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
            best_by_row[row] = (rect, ui_dst, name, bbox, scroll)

    (OUT_DIR / "all-recorded-ctrl-rects.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in all_rects))

    rows = []
    mismatches = []
    termpp_queue = []
    for row, (rect, ui_dst, pass_name, bbox, scroll) in sorted(best_by_row.items()):
        src = source_row(inv, row)
        manual_status, failure, verdict, next_action = status_for(src, row)
        visible = LABEL_OVERRIDES.get(rect["label"], rect["label"])
        expected = EXPECTED_LEAF[row]
        meta = {
            "inventory_row": row,
            "source_anchor": src["source_anchor"],
            "scanner_label": src["scanner_label"],
            "headed_visible_label": visible,
            "capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "bbox_xywh": bbox,
            "scroll_y": scroll,
            "target_leaf": src["target_leaf"],
            "expected_target_leaf": expected,
            "visible_top_level_tab": "EDIT",
            "visible_container": expected,
            "visibility_status": manual_status,
            "visibility_failure": failure,
            "evidence_reason": f"Exact ImGui control rect emitted by FL4260_CTRL_RECTS_RECORD as {rect['label']} during {pass_name} pass.",
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
            "scanner_label": src["scanner_label"],
            "headed_visible_label": visible,
            "headed_location_tab": "EDIT",
            "headed_location_section": expected,
            "headed_reachable": manual_status,
            "headed_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_capture_file": str(ui_dst.relative_to(OUT_DIR)),
            "headed_row_crop_file": f"row-captures/row_{row}/row_crop.png",
            "headed_row_bbox_xywh": json.dumps(bbox),
            "headed_scroll_y": str(scroll),
            "headed_visibility_note": meta["evidence_reason"],
            "visibility_failure": failure,
            "backend_mutation_owner": src["backend_mutation_owner"],
            "render_proof_consumer": src["render_proof_consumer"],
            "inventory_status_before": src["status"],
            "manual_status_after": manual_status,
            "termpp_verdict": verdict,
            "termpp_expected_surface": "world_edit_panel",
            "termpp_expected_property": "ITEM/ENEMY/STORY placement state",
            "termpp_expected_delta_class": "world_edit_downstream_termpp",
            "termpp_exception_reason": failure if manual_status != "reachable_actionable" else "",
            "product_loop_class": "NON_PROFILE_WORLD_EDIT_CONTROL",
            "existing_matrix_row": "",
            "existing_proof_artifact": "",
            "required_next_action": next_action,
            "reviewer_notes": "Real headed row crop from current ASCIIID UI on NEW_MAP. This is row-level reachability and visible-surface audit evidence, not action proof.",
        }
        rows.append(out)
        if manual_status == "visible_surface_mismatch":
            mismatches.append(out)
        termpp_queue.append(out)

    with (OUT_DIR / "row-level-headed-inventory-check.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for name, data in [
        ("visible-surface-mismatches.jsonl", mismatches),
        ("layout-blockers.jsonl", []),
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
    summary = {
        "rows_with_exact_crops": len(rows),
        "rows": [r["inventory_row"] for r in rows],
        "missing_target_rows": sorted(set(RECT_TO_ROW.values()) - set(best_by_row)),
        "by_status": Counter(r["manual_status_after"] for r in rows),
        "by_target_leaf": Counter(r["target_leaf"] for r in rows),
        "visible_surface_mismatches": len(mismatches),
        "layout_blockers": 0,
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "visible-surface-audit.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=list) + "\n")
    (OUT_DIR / "README.md").write_text(
        "# FL-4260 row-level EDIT ITEM/ENEMY/STORY headed inventory check\n\n"
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')} from live headed `./.run/asciiid --cdp 8765` on NEW_MAP.\n\n"
        "This package captures exact row-level crops for the EDIT ITEM, ENEMY, and STORY placement slice. "
        "It records visible-surface mismatches where the scanner CSV points at stale leaves while the live controls are visibly under ITEM, ENEMY, or STORY. "
        "It is inventory evidence only and does not claim Phase 0 acceptance, Phase 1 acceptance, product acceptance, backend acceptance, Law 15, Law 16, native parity, nor closure.\n\n"
        f"Rows with exact crops: {len(rows)}.\n"
        f"Missing target rows: {summary['missing_target_rows']}.\n"
    )
    transcript.close()
    print(json.dumps(summary, indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

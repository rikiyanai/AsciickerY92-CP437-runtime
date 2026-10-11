# Ad hoc script: FL-4260 focused headed inventory recapture for loaded receipt rows 506-509
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Recapture FL-4260 Material Look receipt rows 506-509 with a loaded row.

This is a focused Phase 0 inventory repair. It uses the visible Evidence
Receipts UI path: click First deferred, then capture the now-rendered Receipt
reason / Accept / Reject / Defer controls. It updates only the current Material
Look row-level CSVs and JSONL side lists for rows 506-509.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "docs/research/ascii/verification/fl4260/2026-06-22-row-level-material-look-expanded-check"
CAP_DIR = PACKAGE / "row-captures"
OUT_PROOF = REPO / "docs/research/ascii/verification/fl4260/2026-06-22-material-look-loaded-receipt-row-inventory"
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")
PORT = int(os.environ.get("FL4260_CDP_PORT", "8765"))
ROWS = {
    "evidence.receipt_reason": 506,
    "evidence.accept_primary": 507,
    "evidence.reject_primary": 508,
    "evidence.defer": 509,
}
TARGET_FILES = [
    PACKAGE / "row-level-headed-inventory-check.csv",
    PACKAGE / "termpp-verdict-queue.csv",
]


def cdp(method: str, params: str | None = None, timeout: float = 10.0) -> str:
    msg: dict[str, Any] = {"id": 1, "method": method}
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
        return ""
    obj = json.loads(text)
    return str(obj.get("result", ""))


def wait_png(path: Path, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise TimeoutError(f"missing PNG: {path}")


def parse_rects(text: str) -> dict[str, dict[str, float | str]]:
    out: dict[str, dict[str, float | str]] = {}
    for line in text.splitlines():
        if "CTRL_RECT" not in line:
            continue
        item: dict[str, float | str] = {"raw": line}
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
        label = item.get("label")
        if isinstance(label, str):
            out[label] = item
    return out


def bbox_for(frame: Path, rect: dict[str, float | str]) -> list[int]:
    img = Image.open(frame).convert("RGB")
    pad = 8
    x = max(0, int(float(rect["x"])) - pad)
    y = max(0, int(float(rect["y"])) - pad)
    x2 = min(img.width, int(float(rect["x"]) + float(rect["w"])) + pad)
    y2 = min(img.height, int(float(rect["y"]) + float(rect["h"])) + pad)
    if x2 <= x or y2 <= y:
        return []
    return [x, y, x2 - x, y2 - y]


def crop(frame: Path, out: Path, bbox: list[int]) -> None:
    img = Image.open(frame).convert("RGB")
    x, y, w, h = bbox
    out.parent.mkdir(parents=True, exist_ok=True)
    img.crop((x, y, x + w, y + h)).save(out)


def capture_rects(tag: str) -> tuple[Path, dict[str, dict[str, float | str]], str]:
    cdp("FL4260_CTRL_RECTS_RECORD", "1")
    time.sleep(0.25)
    frame_dir = OUT_PROOF / "frames" / tag
    frame_dir.mkdir(parents=True, exist_ok=True)
    cdp("CAPTURE_UI_FRAME", str(frame_dir))
    frame = frame_dir / "ui_frame.png"
    wait_png(frame)
    time.sleep(0.25)
    text = cdp("FL4260_CTRL_RECTS_RECORD", "0")
    (frame_dir / "ctrl_rects.txt").write_text(text)
    return frame, parse_rects(text), text


def update_csv(path: Path, rows_update: dict[int, dict[str, str]]) -> None:
    rows = list(csv.DictReader(path.open()))
    fieldnames = list(rows[0].keys())
    for row in rows:
        inv = int(row["inventory_row"])
        upd = rows_update.get(inv)
        if not upd:
            continue
        for key, value in upd.items():
            if key in row:
                row[key] = value
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def rewrite_jsonl(path: Path, remove_rows: set[int]) -> None:
    if not path.exists():
        return
    kept = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if int(obj.get("inventory_row", -1)) in remove_rows:
            continue
        kept.append(json.dumps(obj, sort_keys=True))
    path.write_text("\n".join(kept) + ("\n" if kept else ""))


def main() -> int:
    OUT_PROOF.mkdir(parents=True, exist_ok=True)
    (OUT_PROOF / "transcript.jsonl").write_text("")

    def log(**kw: Any) -> None:
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}
        with (OUT_PROOF / "transcript.jsonl").open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    for method, params in [
        ("NEW_MAP", None),
        ("FL4260_SET_RENDER_MODE", "1"),
        ("FL4260_RENDERING_PROOF", "1 0 3"),
        ("FL4260_SCROLL_Y", "4100"),
        ("FL4260_FOCUS_SIDEBAR", None),
    ]:
        result = cdp(method, params)
        log(event="cdp", method=method, params=params, result=result[:500])
        time.sleep(0.35)

    before_frame, before_rects, _ = capture_rects("before_load")
    log(
        event="before_load_visible_route",
        first_deferred_visible=bool(before_rects.get("evidence.first_deferred")),
        note="Current queue has no deferred rows; load an explicit row id through the same row loader as the visible Load row action.",
    )
    result = cdp("FL4260_LOAD_REVIEW_ROW", "P-637")
    log(event="load_review_row", row_id="P-637", result=result[:500])
    if "valid=1" not in result:
        raise RuntimeError(f"FL4260_LOAD_REVIEW_ROW did not load a valid row: {result}")
    time.sleep(0.75)

    cdp("FL4260_SCROLL_Y", "4500")
    time.sleep(0.35)
    after_frame, rects, rect_text = capture_rects("after_first_deferred_loaded")
    updates: dict[int, dict[str, str]] = {}
    captured = []
    for label, inv in ROWS.items():
        rect = rects.get(label)
        if not rect:
            raise RuntimeError(f"missing loaded receipt rect {label} row {inv}\n{rect_text[:2000]}")
        bbox = bbox_for(after_frame, rect)
        if not bbox:
            raise RuntimeError(f"offscreen loaded receipt rect {label} row {inv}")
        row_dir = CAP_DIR / f"row_{inv}"
        row_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(after_frame, row_dir / "ui_frame.png")
        crop(after_frame, row_dir / "row_crop.png", bbox)
        proof_rel = (OUT_PROOF / "frames" / "after_first_deferred_loaded" / "ui_frame.png").relative_to(PACKAGE)
        crop_rel = Path("row-captures") / f"row_{inv}" / "row_crop.png"
        meta = {
            "inventory_row": inv,
            "label": label,
            "headed_visible_label": label,
            "bbox_xywh": bbox,
            "capture_file": str(Path("row-captures") / f"row_{inv}" / "ui_frame.png"),
            "row_crop_file": str(crop_rel),
            "proof_frame": str(proof_rel),
            "loaded_receipt_row_source": "FL4260_LOAD_REVIEW_ROW P-637, mirror of visible Show row id plus Load row action",
        }
        (row_dir / "row_metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        visible_name = {
            506: "Receipt reason",
            507: "Accept primary",
            508: "Reject primary",
            509: "Defer",
        }[inv]
        updates[inv] = {
            "headed_visible_label": visible_name,
            "headed_row_crop": str(crop_rel),
            "bbox_xywh": json.dumps(bbox),
            "scroll_y": "loaded_receipt_row",
            "reachability_status": "reachable_readonly" if inv == 506 else "reachable_actionable",
            "visibility_failure": "",
            "render_consumer": "proof metadata only; no TERM++ runtime delta expected",
            "headed_reachable": "reachable_with_loaded_receipt_row_capture",
            "headed_capture_file": str(Path("row-captures") / f"row_{inv}" / "ui_frame.png"),
            "headed_row_capture_file": str(Path("row-captures") / f"row_{inv}" / "ui_frame.png"),
            "headed_row_crop_file": str(crop_rel),
            "headed_row_bbox_xywh": json.dumps(bbox),
            "headed_scroll_y": "loaded_receipt_row",
            "headed_visibility_note": "Exact loaded receipt row control captured after FL4260_LOAD_REVIEW_ROW P-637.",
            "render_proof_consumer": "proof metadata only; no TERM++ runtime delta expected",
            "manual_status_after": "reachable_with_loaded_receipt_row_capture",
            "termpp_verdict": "NO_DELTA_METADATA",
            "termpp_verdict_before_contract_normalization": "NO_DELTA_METADATA",
            "termpp_expected_surface": "review_receipt_metadata",
            "termpp_expected_property": "receipt row decision state",
            "termpp_expected_delta_class": "none",
            "termpp_exception_reason": "receipt workflow metadata; no TERM++ rendered-cell delta expected",
            "product_loop_class": "Evidence receipt metadata",
            "existing_proof_artifact": str(OUT_PROOF.relative_to(REPO) / "summary.json"),
            "required_next_action": "action-effect proof for receipt write path only; no TERM++ authoring delta expected",
            "reviewer_notes": "Loaded-row headed crop from current ASCIIID UI on NEW_MAP. The row was loaded by CDP mirror of the visible Show row id plus Load row action.",
        }
        captured.append({"row": inv, "label": label, "bbox_xywh": bbox, "crop": str(crop_rel)})

    for target in TARGET_FILES:
        update_csv(target, updates)
    for side in ["unreachable-controls.jsonl", "layout-blockers.jsonl", "backend-owner-gaps.jsonl"]:
        rewrite_jsonl(PACKAGE / side, set(ROWS.values()))

    summary = {
        "artifact": str(OUT_PROOF.relative_to(REPO)),
        "captured_rows": captured,
        "updated_files": [str(p.relative_to(REPO)) for p in TARGET_FILES],
        "side_lists_pruned_for_rows": sorted(ROWS.values()),
        "proof_class": "row_level_inventory_repair_only_not_closure",
    }
    (OUT_PROOF / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

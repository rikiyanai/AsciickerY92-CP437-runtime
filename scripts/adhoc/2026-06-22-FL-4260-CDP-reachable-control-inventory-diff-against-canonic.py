# Ad hoc script: FL-4260 CDP reachable-control inventory diff against canonical CURRENT_UI_INVENTORY.csv
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import socket
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ASCIIID = ROOT / ".run" / "asciiid"
FL_DIR = ROOT / "docs/research/ascii/verification/fl4260"
CSV_PATH = FL_DIR / "CURRENT_UI_INVENTORY.csv"
OUT_DIR = FL_DIR / f"{dt.date.today().isoformat()}-cdp-current-ui-inventory-diff"
PORT = int(os.environ.get("FL4260_CDP_PORT", "8766"))
HOST = os.environ.get("FL4260_CDP_HOST", "127.0.0.1")

PASS_COMMANDS: list[dict[str, Any]] = []

# Current visible top-level and shared tabs. These are CDP-drivable focus paths
# already used by the retained row-level inventory packages.
for tab in range(0, 10):
    PASS_COMMANDS.append({"name": f"sidebar_tab_{tab}", "commands": [["FL4260_FOCUS_SIDEBAR_TAB", str(tab)], ["FL4260_SCROLL_Y", "-1"], ["FL4260_EDIT_SCROLL_Y", "-1"]]})

for tab in range(0, 8):
    for scroll in [0, 260, 520, 780, 1040, 1300, 1560]:
        PASS_COMMANDS.append({"name": f"edit_brush_tab_{tab}_scroll_{scroll}", "commands": [["FL4260_FOCUS_BRUSH_TAB", str(tab)], ["FL4260_EDIT_SCROLL_Y", str(scroll)]]})

# Material Look has its own scroll owner and previously failed around sticky scroll.
for focus in [0, 1, 2, 3, 4, 5, 6]:
    for scroll in [0, 220, 440, 660, 880, 1100, 1320, 1540, 1760, 1980]:
        PASS_COMMANDS.append({"name": f"material_look_focus_{focus}_scroll_{scroll}", "commands": [["FL4260_RENDERING_PROOF", f"1 0 {focus}"], ["FL4260_SCROLL_Y", str(scroll)]]})

# INFO probe/tail captures are represented via focus commands and root sidebar passes.
PASS_COMMANDS.append({"name": "info_probe_setup", "commands": [["FL4260_INFO_TERRAIN_PROBE", "64 64"], ["FL4260_FOCUS_SIDEBAR_TAB", "8"]]})


def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("++", "plusplus")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def cdp_call(method: str, params: str = "", timeout: float = 10.0) -> str:
    msg = {"id": 1, "method": method}
    if params:
        msg["params"] = params
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(msg) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    text = data.decode("utf-8", "replace").strip()
    if not text:
        return ""
    return str(json.loads(text).get("result", ""))


def parse_rects(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if "CTRL_RECT" not in line:
            continue
        rec: dict[str, Any] = {"raw": line}
        for part in line.split():
            if part.startswith("label="):
                rec["label"] = part.split("=", 1)[1]
            elif part.startswith("x="):
                rec["x"] = float(part.split("=", 1)[1])
            elif part.startswith("y="):
                rec["y"] = float(part.split("=", 1)[1])
            elif part.startswith("w="):
                rec["w"] = float(part.split("=", 1)[1])
            elif part.startswith("h="):
                rec["h"] = float(part.split("=", 1)[1])
        if "label" in rec:
            rows.append(rec)
    return rows


def load_csv() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    transcript = (OUT_DIR / "cdp-transcript.jsonl").open("w", encoding="utf-8")

    def log(event: str, **kw: Any) -> None:
        transcript.write(json.dumps({"event": event, "ts": dt.datetime.now().isoformat(timespec="seconds"), **kw}, sort_keys=True) + "\n")
        transcript.flush()

    csv_rows = load_csv()
    csv_by_norm: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv_rows:
        csv_by_norm[normalize(row.get("visible_label", ""))].append(row)
        cdp_label = normalize(row.get("cdp_control_label", ""))
        if cdp_label:
            csv_by_norm[cdp_label].append(row)

    log("start", csv=str(CSV_PATH.relative_to(ROOT)), csv_rows=len(csv_rows), port=PORT)
    for method, params in [["NEW_MAP", ""], ["FL4260_SET_RENDER_MODE", "1"], ["FL4260_SCROLL_Y", "-1"], ["FL4260_EDIT_SCROLL_Y", "-1"]]:
        try:
            res = cdp_call(method, params)
            log("setup", method=method, params=params, result=res[:800])
            time.sleep(0.25)
        except Exception as exc:
            log("setup_error", method=method, params=params, error=str(exc))

    live_rows: list[dict[str, Any]] = []
    command_error_count = 0
    rect_error_count = 0
    for spec in PASS_COMMANDS:
        name = spec["name"]
        for method, params in spec["commands"]:
            try:
                res = cdp_call(method, params)
                log("command", pass_name=name, method=method, params=params, result=res[:800])
                time.sleep(0.18)
            except Exception as exc:
                command_error_count += 1
                log("command_error", pass_name=name, method=method, params=params, error=str(exc))
        try:
            cdp_call("FL4260_CTRL_RECTS_RECORD", "1")
            time.sleep(0.18)
            text = cdp_call("FL4260_CTRL_RECTS_RECORD", "0", timeout=15.0)
            rects = parse_rects(text)
            log("rect_pass", pass_name=name, rect_count=len(rects))
            for rect in rects:
                label = str(rect.get("label", ""))
                norm = normalize(label)
                matches = csv_by_norm.get(norm, [])
                live_rows.append({"pass_name": name, "label": label, "norm": norm, "matched_csv_rows": [m.get("inventory_row", "") for m in matches], "matched_count": len(matches), **{k: rect.get(k) for k in ["x", "y", "w", "h"]}})
        except Exception as exc:
            rect_error_count += 1
            log("rect_error", pass_name=name, error=str(exc))

    unique_live: dict[str, dict[str, Any]] = {}
    for row in live_rows:
        unique_live.setdefault(row["norm"], row)

    missing_live = [row for row in unique_live.values() if row["matched_count"] == 0 and row["norm"]]
    matched_norms = {row["norm"] for row in unique_live.values() if row["matched_count"] > 0}
    csv_missing_from_live = [row for row in csv_rows if normalize(row.get("visible_label", "")) not in matched_norms]

    with (OUT_DIR / "live-cdp-ctrl-rects.jsonl").open("w", encoding="utf-8") as f:
        for row in live_rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    with (OUT_DIR / "live-labels-missing-from-current-csv.jsonl").open("w", encoding="utf-8") as f:
        for row in sorted(missing_live, key=lambda r: r["label"]):
            f.write(json.dumps(row, sort_keys=True) + "\n")
    with (OUT_DIR / "current-csv-rows-not-seen-in-live-cdp.jsonl").open("w", encoding="utf-8") as f:
        for row in csv_missing_from_live:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    label_counter = Counter(row["label"] for row in live_rows)
    if command_error_count or rect_error_count:
        verdict = "FAIL_CDP_SESSION_ERRORS_DURING_AUDIT"
    elif not live_rows:
        verdict = "FAIL_NO_LIVE_CDP_RECTS_RECORDED"
    elif missing_live:
        verdict = "FAIL_CSV_INCOMPLETE_FOR_LIVE_CDP_REACHABLE_CONTROLS"
    else:
        verdict = "PASS_NO_LIVE_CDP_LABEL_GAPS_FOUND"

    summary = {
        "schema": "fl4260.cdp_current_ui_inventory_diff.v1",
        "csv": str(CSV_PATH.relative_to(ROOT)),
        "out_dir": str(OUT_DIR.relative_to(ROOT)),
        "cdp_port": PORT,
        "pass_count": len(PASS_COMMANDS),
        "live_rect_observation_count": len(live_rows),
        "live_unique_label_count": len(unique_live),
        "live_unique_labels_missing_from_current_csv_count": len(missing_live),
        "command_error_count": command_error_count,
        "rect_error_count": rect_error_count,
        "current_csv_rows_not_seen_in_live_cdp_count": len(csv_missing_from_live),
        "current_csv_rows": len(csv_rows),
        "top_missing_live_labels": [row["label"] for row in sorted(missing_live, key=lambda r: r["label"])[:100]],
        "most_observed_live_labels": label_counter.most_common(40),
        "verdict": verdict,
    }
    (OUT_DIR / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if verdict.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())

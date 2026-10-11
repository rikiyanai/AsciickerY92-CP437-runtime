# Ad hoc script: FL-4260 mark starter and fg bg row action-effect no-op reports in current UI inventory CSVs
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path

STAMP = "2026-06-22"
REPORTED_ROWS = {426, 430, 431, 432, 433, 434, 435, 467, 468}
STARTER_ROWS = {426, 430, 431, 432, 433, 434, 435}
COLOR_ROWS = {467, 468}
STATUS = "REACHABLE_ACTION_EFFECT_REPORTED_NOOP_PENDING_ROW_PROOF"
VERDICT = "TERMPLUSPLUS_VERDICT_BLOCKED_PENDING_ACTION_EFFECT"
DELTA = "reported_no_visible_effect_pending_before_action_after_capture"
NOTE = (
    "Operator report 2026-06-22: control is visible/reachable, but clicking it appears to do nothing. "
    "Reachability is not accepted as action proof; require row-level before/action/after detached TERM++ capture."
)
NEXT = (
    "Run row-level visible click proof with before/action/after sidebar PNG, detached TERM++ PNG, native .xp, "
    "all-cell JSONL, expected_before_action.json, changed_cells.json. If changed_cells=0, fix UI/backend action path."
)
ROOTS = [
    Path("docs/research/ascii/verification/fl4260/2026-06-22-row-level-starters-check"),
    Path("docs/research/ascii/verification/fl4260/2026-06-22-current-ui-inventory-manual-check"),
    Path("docs/research/ascii/verification/fl4260/2026-06-22-current-ui-inventory-headed-check"),
]
CSV_NAMES = [
    "row-level-headed-inventory-check.csv",
    "manual-current-ui-inventory-check.csv",
    "headed-current-ui-inventory-check.csv",
    "profile-path-proof-queue.csv",
    "termpp-verdict-queue.csv",
]

def row_id(row):
    raw = row.get("inventory_row") if "inventory_row" in row else row.get("row", "")
    try:
        return int(raw)
    except ValueError:
        return None

def append_note(old, addition):
    old = (old or "").strip()
    if addition in old:
        return old
    return addition if not old else old + " | " + addition

def update_csv(path):
    if not path.exists():
        return 0
    lineterminator = "\n"
    try:
        head_bytes = subprocess.check_output(["git", "show", f"HEAD:{path}"])
        if b"\r\n" in head_bytes[:4096]:
            lineterminator = "\r\n"
    except subprocess.CalledProcessError:
        pass
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    changed = 0
    for row in rows:
        rid = row_id(row)
        if rid not in REPORTED_ROWS:
            continue
        changed += 1
        if "manual_status_after" in row:
            row["manual_status_after"] = STATUS
        if "status" in row:
            row["status"] = STATUS
        if "termpp_verdict" in row:
            row["termpp_verdict"] = VERDICT
        if "termpp_verdict_override" in row:
            row["termpp_verdict_override"] = VERDICT
        if "termpp_expected_delta_class" in row:
            row["termpp_expected_delta_class"] = DELTA
        if "termpp_exception_reason" in row:
            row["termpp_exception_reason"] = NOTE
        if "required_next_action" in row:
            row["required_next_action"] = NEXT
        if "reviewer_notes" in row:
            row["reviewer_notes"] = append_note(row.get("reviewer_notes"), NOTE)
        if "existing_proof_artifact" in row:
            row["existing_proof_artifact"] = append_note(row.get("existing_proof_artifact"), "Reachability artifact only; no action-effect proof accepted.")
    if changed:
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator=lineterminator)
            writer.writeheader()
            writer.writerows(rows)
    return changed

def update_summary(path):
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    data["operator_reported_no_visible_effect_rows"] = sorted(REPORTED_ROWS)
    data["operator_reported_no_visible_effect_starter_rows"] = sorted(STARTER_ROWS)
    data["operator_reported_no_visible_effect_color_editor_rows"] = sorted(COLOR_ROWS)
    data["action_effect_status"] = STATUS
    data["action_effect_verdict"] = VERDICT
    data["action_effect_note"] = NOTE
    data["reachability_is_not_action_proof"] = True
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return True

def update_readme(path):
    if not path.exists():
        return False
    text = path.read_text()
    block = f"""

## Operator No-Effect Finding - {STAMP}

Rows 426, 430, 431, 432, 433, 434, 435, 467, and 468 are now explicitly flagged as `{STATUS}`. The operator reported that the visible Starters controls, `Glyph Style Presets`, `Color Presets`, `Vegetation CP437 glyph starter`, `Vegetation ramp color starter`, and the `fg`/`bg` color editors appear to do nothing when clicked.

This package may prove row visibility for those rows. It does not prove action effect. Each row still needs a before/action/after package with detached TERM++ cells, native `.xp`, all-cell JSONL, expected_before_action.json, changed_cells.json, and a row-level UI transcript. A zero changed-cell result is a UI/backend bug, not an acceptable proof gap.
"""
    marker = "## Operator No-Effect Finding - 2026-06-22"
    if marker not in text:
        path.write_text(text.rstrip() + block + "\n")
        return True
    return False

changed = {}
for root in ROOTS:
    for name in CSV_NAMES:
        path = root / name
        count = update_csv(path)
        if count:
            changed[str(path)] = count
    for name in ["row-level-headed-inventory-summary.json", "manual-current-ui-inventory-summary.json", "headed-current-ui-inventory-summary.json"]:
        path = root / name
        if update_summary(path):
            changed[str(path)] = changed.get(str(path), 0) + 1
    if update_readme(root / "README.md"):
        changed[str(root / "README.md")] = changed.get(str(root / "README.md"), 0) + 1

failure_log = Path("docs/FAILURE_LOG.md")
entry = f"""

#### Fix attempt - 2026-06-22 @ `main`

- **Finding:** Operator rejected the FL-4260 current UI inventory CSV framing because it still treated reachable Material Look controls as generic proof backlog after the operator reported direct clicks with no visible effect. A row-level inventory audit must flag nonworking controls, not merely record that the row is visible.
- **Rows flagged:** 426 (`[GRASS] soft_top_curve starter` dynamic full starter), 430 (`Add all eligible extended glyphs to this material`), 431 (`Glyph Style Presets`), 432 (`Color Presets`), 433 (`[WATER] curve_curlicue starter` dynamic full starter), 434 (`Vegetation CP437 glyph starter`), 435 (`Vegetation ramp color starter`), 467 (`fg` ColorEdit3), and 468 (`bg` ColorEdit3).
- **Approach:** Updated current FL-4260 UI inventory CSVs under `2026-06-22-row-level-starters-check/`, `2026-06-22-current-ui-inventory-manual-check/`, and `2026-06-22-current-ui-inventory-headed-check/` so those rows carry `{STATUS}` plus `{VERDICT}`. Existing visibility captures remain reachability evidence only. They are not action-effect proof.
- **Proof state:** CSV/status correction only. These rows now require row-level visible click before/action/after packages. If detached TERM++ changed cells remain zero, the next patch must fix the UI/backend action path. No Phase 0 completion claim, no Phase 1 completion claim, no backend-complete claim, no product-complete claim, no Law 15 claim, no Law 16 claim, no closure claim.
"""
if entry.strip() not in failure_log.read_text():
    with failure_log.open("a", newline="") as f:
        f.write(entry)
    changed[str(failure_log)] = changed.get(str(failure_log), 0) + 1

print(json.dumps({"changed": changed, "rows": sorted(REPORTED_ROWS), "status": STATUS, "verdict": VERDICT}, indent=2, sort_keys=True))

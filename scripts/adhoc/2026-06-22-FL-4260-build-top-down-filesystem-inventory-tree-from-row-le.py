# Ad hoc script: FL-4260 build top-down filesystem inventory tree from row-level CSVs
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FL_DIR = ROOT / "docs/research/ascii/verification/fl4260"
OUT = FL_DIR / "2026-06-22-current-ui-inventory-tree"
ROOT_TREE_MD = FL_DIR / "CURRENT_UI_INVENTORY_TREE.md"
ROOT_CURRENT_CSV = FL_DIR / "CURRENT_UI_INVENTORY.csv"
CSV_GLOB = "2026-06-22-row-level-*/row-level-headed-inventory-check.csv"
TREE_COLUMNS = [
    "inventory_tree_current_visible",
    "inventory_tree_tab",
    "inventory_tree_container",
    "inventory_tree_section",
    "inventory_tree_leaf_path",
    "inventory_tree_pipeline_status",
    "inventory_tree_pipeline_gap_reason",
]

BAD_TOKENS = [
    "unclassified current source state",
    "backend_owner_unknown",
    "blocked_pending_backend_trace",
    "unmapped",
    "stale_scanner_target_leaf",
    "blocked_pending_label",
    "termplusplus_verdict_blocked_pending_runtime_state",
    "termplusplus_verdict_blocked_pending_layout",
]

FULL_STARTER_PRESET_LABELS = [
    ("starters.preset_0_GRASS", "Full starter row 0 [GRASS]"),
    ("starters.preset_1_WATER", "Full starter row 1 [WATER]"),
    ("starters.preset_2_GRASS", "Full starter row 2 [GRASS]"),
    ("starters.preset_3_STONE", "Full starter row 3 [STONE]"),
    ("starters.preset_4_DIRT", "Full starter row 4 [DIRT]"),
    ("starters.preset_5_STONE", "Full starter row 5 [STONE]"),
    ("starters.preset_6_GRAVEL", "Full starter row 6 [GRAVEL]"),
]

PACKAGE_ORDER = {
    "2026-06-22-row-level-legacy-character-tabstrip-check": 10,
    "2026-06-22-row-level-view-shared-brush-check": 20,
    "2026-06-22-row-level-edit-brush-check": 30,
    "2026-06-22-row-level-edit-sprite-check": 40,
    "2026-06-22-row-level-edit-item-enemy-story-check": 50,
    "2026-06-22-row-level-material-look-expanded-check": 60,
    "2026-06-22-row-level-measurement-receipts-check": 70,
    "2026-06-22-row-level-info-probe-check": 80,
    "2026-06-22-row-level-info-tail-check": 90,
}


def clean_text(value: Any, fallback: str) -> str:
    value = "" if value is None else str(value).strip()
    return value if value else fallback


def slug(value: Any, fallback: str = "unnamed") -> str:
    value = clean_text(value, fallback)
    value = value.replace("++", "PLUSPLUS")
    value = value.replace("/", "_")
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", value)
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"_+", "_", value)
    value = value.strip("._-")
    return value[:96] if value else fallback


def row_int(row: dict[str, str]) -> int:
    raw = clean_text(row.get("inventory_row"), "999999")
    try:
        return int(float(raw))
    except ValueError:
        return 999999


def package_name(path: Path) -> str:
    return path.parent.name


def canonical_tab(row: dict[str, str]) -> str:
    tab = clean_text(row.get("actual_visible_top_level_tab"), "")
    if not tab:
        tab = clean_text(row.get("headed_location_tab"), "UNKNOWN_TAB")
    if tab == "EDIT" and "Material Look" in clean_text(row.get("actual_visible_container"), ""):
        return "EDIT_WITH_MATERIAL_LOOK"
    return tab


def canonical_container(row: dict[str, str], pkg: str) -> str:
    container = clean_text(row.get("actual_visible_container"), "")
    if not container:
        container = clean_text(row.get("headed_location_section"), "")
    if not container:
        container = pkg.replace("2026-06-22-row-level-", "")
    return container


def canonical_section(row: dict[str, str]) -> str:
    section = clean_text(row.get("target_leaf"), "")
    if section and " / " in section:
        return section
    section = clean_text(row.get("headed_location_section"), "")
    if section:
        return section
    return clean_text(row.get("target_leaf"), "UNMAPPED / requires manual classification")


def visible_label(row: dict[str, str]) -> str:
    for key in ("headed_visible_label", "scanner_label", "widget", "target_leaf"):
        val = clean_text(row.get(key), "")
        if val:
            return val
    return "unnamed control"


def cdp_control_label(row: dict[str, str]) -> str:
    override = clean_text(row.get("_cdp_control_label"), "")
    if override:
        return override
    note = clean_text(row.get("headed_visibility_note"), "")
    match = re.search(r"\bas ([A-Za-z0-9_.-]+) during\b", note)
    if match:
        return match.group(1)
    for key in ("source_anchor", "headed_visible_label", "scanner_label", "widget"):
        val = clean_text(row.get(key), "")
        if "." in val and not val.startswith("editor/"):
            return val
    return ""


def expand_current_visible_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    expanded: list[dict[str, str]] = []
    for row in rows:
        if row_int(row) == 460 and row.get("_visible_label") == "Full starter preset button":
            for index, (cdp_label, visible) in enumerate(FULL_STARTER_PRESET_LABELS):
                derived = dict(row)
                derived["_visible_label"] = visible
                derived["_cdp_control_label"] = cdp_label
                derived["_inventory_row_display"] = f"460.{index}"
                expanded.append(derived)
            continue
        if row_int(row) == 592 and cdp_control_label(row) == "edit.matelev.brush_diameter":
            tab = dict(row)
            tab["_visible_label"] = "MAT-elev tab"
            tab["_cdp_control_label"] = "edit.matelev.tab"
            tab["_inventory_row_display"] = "591"
            tab["widget"] = "BeginTabItem"
            tab["source_anchor"] = "editor/asciiid.cpp:30301"
            tab["source_line"] = 'if (ImGui::BeginTabItem("MAT-elev", nullptr, fl4260_matelev_tab_flags))'
            tab["scanner_label"] = "MAT-elev"
            tab["headed_visible_label"] = "MAT-elev tab"
            tab["headed_visibility_note"] = "Derived from live CDP reachable-control diff: FL4260_CTRL_RECTS_RECORD emitted edit.matelev.tab during edit_brush_tab_3_scroll_0."
            tab["visibility_failure"] = "derived_from_live_cdp_gap"
            tab["required_next_action"] = "replace derived row with exact row crop and action-effect proof for MAT-elev tab reachability"
            tab["_pipeline_status"] = "PIPELINE_GAP"
            tab["_pipeline_gap_reason"] = "derived_from_live_cdp_gap;exact_row_crop_pending"
            expanded.append(tab)
        row["_inventory_row_display"] = str(row_int(row))
        expanded.append(row)
    return expanded


def pipeline_status(row: dict[str, str]) -> str:
    text = " | ".join([
        clean_text(row.get("backend_mutation_owner"), ""),
        clean_text(row.get("backend_owner"), ""),
        clean_text(row.get("target_leaf"), ""),
        clean_text(row.get("reachability_status"), ""),
        clean_text(row.get("termpp_verdict"), ""),
        clean_text(row.get("visibility_failure"), ""),
    ]).lower()
    return "PIPELINE_GAP" if any(tok in text for tok in BAD_TOKENS) else "PIPELINE_LISTED"


def pipeline_gap_reason(row: dict[str, str]) -> str:
    reasons = []
    if "unclassified current source state" in clean_text(row.get("backend_mutation_owner"), "").lower():
        reasons.append("backend_mutation_owner_unclassified")
    if "unclassified current source state" in clean_text(row.get("backend_owner"), "").lower():
        reasons.append("backend_owner_unclassified")
    if clean_text(row.get("reachability_status"), "").lower() == "backend_owner_unknown":
        reasons.append("backend_owner_unknown")
    if "UNMAPPED" in clean_text(row.get("target_leaf"), ""):
        reasons.append("target_leaf_unmapped")
    if "BLOCKED_PENDING_BACKEND_TRACE" in clean_text(row.get("termpp_verdict"), ""):
        reasons.append("backend_trace_pending")
    if "BLOCKED_PENDING_RUNTIME_STATE" in clean_text(row.get("termpp_verdict"), ""):
        reasons.append("runtime_state_pending")
    if "BLOCKED_PENDING_LAYOUT" in clean_text(row.get("termpp_verdict"), ""):
        reasons.append("layout_pending")
    if "BLOCKED_PENDING_LABEL" in clean_text(row.get("termpp_verdict"), ""):
        reasons.append("label_binding_pending")
    if "stale_scanner_target_leaf" in clean_text(row.get("visibility_failure"), ""):
        reasons.append("stale_scanner_target_leaf")
    if not reasons:
        reasons.append("pipeline_listed")
    return ";".join(reasons)


def is_current_visible(row: dict[str, str]) -> bool:
    label = visible_label(row).lower()
    visibility = clean_text(row.get("visibility_failure"), "").lower()
    reachable = clean_text(row.get("headed_reachable"), clean_text(row.get("reachability_status"), "")).lower()
    if label.startswith("not rendered"):
        return False
    if "hidden_by_" in visibility:
        return False
    if "modal_not_active" in visibility or "confirm_modal_not_active" in visibility:
        return False
    if "not_active" in reachable:
        return False
    return True


def write_leaf(path: Path, row: dict[str, str], pkg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "inventory_row": row_int(row),
        "visible_label": visible_label(row),
        "widget": clean_text(row.get("widget"), ""),
        "scanner_label": clean_text(row.get("scanner_label"), ""),
        "source_anchor": clean_text(row.get("source_anchor"), ""),
        "source_line": clean_text(row.get("source_line"), ""),
        "target_leaf": clean_text(row.get("target_leaf"), ""),
        "tab": canonical_tab(row),
        "container": canonical_container(row, pkg),
        "section": canonical_section(row),
        "backend_owner": clean_text(row.get("backend_owner"), ""),
        "backend_mutation_owner": clean_text(row.get("backend_mutation_owner"), ""),
        "render_consumer": clean_text(row.get("render_consumer"), ""),
        "render_proof_consumer": clean_text(row.get("render_proof_consumer"), ""),
        "termpp_verdict": clean_text(row.get("termpp_verdict"), ""),
        "termpp_expected_surface": clean_text(row.get("termpp_expected_surface"), ""),
        "termpp_expected_property": clean_text(row.get("termpp_expected_property"), ""),
        "termpp_expected_delta_class": clean_text(row.get("termpp_expected_delta_class"), ""),
        "reachability_status": clean_text(row.get("reachability_status"), clean_text(row.get("headed_reachable"), "")),
        "visibility_failure": clean_text(row.get("visibility_failure"), ""),
        "required_next_action": clean_text(row.get("required_next_action"), ""),
        "headed_row_crop_file": clean_text(row.get("headed_row_crop_file"), clean_text(row.get("headed_row_crop"), "")),
        "source_package": pkg,
        "pipeline_status": pipeline_status(row),
        "pipeline_gap_reason": pipeline_gap_reason(row),
    }
    lines = ["# " + fields["visible_label"], ""]
    for key, value in fields.items():
        lines.append(f"- {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_augmented_source_csvs(csv_paths: list[Path], rows: list[dict[str, str]]) -> None:
    rows_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_source[row["_source_csv"]].append(row)

    for path in csv_paths:
        rel = str(path.relative_to(ROOT))
        source_rows = rows_by_source.get(rel, [])
        if not source_rows:
            continue
        original_fields = [
            field
            for field in source_rows[0].keys()
            if not field.startswith("_") and field not in TREE_COLUMNS
        ]
        fieldnames = original_fields + TREE_COLUMNS
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in source_rows:
                row["inventory_tree_current_visible"] = "yes" if is_current_visible(row) else "no"
                row["inventory_tree_tab"] = row["_tab"] if is_current_visible(row) else ""
                row["inventory_tree_container"] = row["_container"] if is_current_visible(row) else ""
                row["inventory_tree_section"] = row["_section"] if is_current_visible(row) else ""
                row["inventory_tree_leaf_path"] = row.get("_leaf_path", "")
                row["inventory_tree_pipeline_status"] = row["_pipeline_status"] if is_current_visible(row) else ""
                row["inventory_tree_pipeline_gap_reason"] = row["_pipeline_gap_reason"] if is_current_visible(row) else ""
                writer.writerow(row)


def render_root_tree_markdown(leaf_records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    grouped: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in leaf_records:
        grouped[row["tab"]][row["container"]][row["section"]].append(row)

    lines = [
        "# FL-4260 Current UI Inventory Tree",
        "",
        "This is the current visible-control inventory tree for FL-4260.",
        "",
        f"Canonical single CSV: `{ROOT_CURRENT_CSV.relative_to(ROOT)}`.",
        "",
        "The same tree fields are written back into every retained `row-level-headed-inventory-check.csv` as `inventory_tree_*` columns.",
        "",
        f"- Source CSV rows: {summary['input_row_count']}",
        f"- Current visible leaves: {summary['leaf_count']}",
        f"- Excluded hidden/not-current-visible rows: {summary['excluded_not_current_visible_count']}",
        f"- Missing intended backend pipeline leaves: {summary['pipeline_gap_count']}",
        "",
        "Browseable leaf files:",
        "",
        f"    eza --tree --level=4 {OUT.relative_to(ROOT)}",
        "",
        "## Tree",
        "",
    ]
    for tab in sorted(grouped):
        lines.append(f"- {tab}")
        for container in sorted(grouped[tab]):
            lines.append(f"  - {container}")
            for section in sorted(grouped[tab][container]):
                leaves = sorted(grouped[tab][container][section], key=lambda r: (r["inventory_row"], r["visible_label"]))
                gap_count = sum(1 for leaf in leaves if leaf["pipeline_status"] == "PIPELINE_GAP")
                suffix = f" ({len(leaves)} leaves, {gap_count} pipeline gaps)" if gap_count else f" ({len(leaves)} leaves)"
                lines.append(f"    - {section}{suffix}")
                for leaf in leaves:
                    label = leaf["visible_label"]
                    status = leaf["pipeline_status"]
                    path = leaf["leaf_path"]
                    lines.append(f"      - row {leaf['inventory_row']}: {label} [{status}] -> `{path}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    csv_paths = sorted(FL_DIR.glob(CSV_GLOB), key=lambda p: (PACKAGE_ORDER.get(package_name(p), 999), str(p)))
    rows: list[dict[str, str]] = []
    for path in csv_paths:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row = dict(row)
                row["_source_csv"] = str(path.relative_to(ROOT))
                row["_source_package"] = package_name(path)
                row["_tab"] = canonical_tab(row)
                row["_container"] = canonical_container(row, package_name(path))
                row["_section"] = canonical_section(row)
                row["_visible_label"] = visible_label(row)
                row["_pipeline_status"] = pipeline_status(row)
                row["_pipeline_gap_reason"] = pipeline_gap_reason(row)
                rows.append(row)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    visible_rows = expand_current_visible_rows([row for row in rows if is_current_visible(row)])
    excluded_rows = [row for row in rows if not is_current_visible(row)]
    leaf_records = []
    collisions: Counter[str] = Counter()
    for row in sorted(visible_rows, key=lambda r: (slug(r["_tab"]), slug(r["_container"]), slug(r["_section"]), row_int(r), r["_visible_label"])):
        leaf_base = f"{row_int(row):04d}__{slug(row['_visible_label'])}"
        rel_parent = Path(slug(row["_tab"], "UNKNOWN_TAB")) / slug(row["_container"], "UNKNOWN_CONTAINER") / slug(row["_section"], "UNMAPPED")
        key = str(rel_parent / leaf_base)
        collisions[key] += 1
        suffix = f"__{collisions[key]}" if collisions[key] > 1 else ""
        leaf_rel = rel_parent / f"{leaf_base}{suffix}.md"
        row["_leaf_path"] = str(leaf_rel)
        write_leaf(OUT / leaf_rel, row, row["_source_package"])
        leaf_records.append({
            "inventory_row": clean_text(row.get("_inventory_row_display"), str(row_int(row))),
            "tab": row["_tab"],
            "container": row["_container"],
            "section": row["_section"],
            "visible_label": row["_visible_label"],
            "cdp_control_label": cdp_control_label(row),
            "pipeline_status": row["_pipeline_status"],
            "pipeline_gap_reason": row["_pipeline_gap_reason"],
            "backend_owner": clean_text(row.get("backend_owner"), ""),
            "backend_mutation_owner": clean_text(row.get("backend_mutation_owner"), ""),
            "termpp_verdict": clean_text(row.get("termpp_verdict"), ""),
            "visibility_failure": clean_text(row.get("visibility_failure"), ""),
            "source_csv": row["_source_csv"],
            "leaf_path": str(leaf_rel),
        })

    fieldnames = list(leaf_records[0].keys()) if leaf_records else []
    with (OUT / "ALL_LEAVES.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leaf_records)
    with ROOT_CURRENT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leaf_records)
    missing = [r for r in leaf_records if r["pipeline_status"] == "PIPELINE_GAP"]
    with (OUT / "MISSING_PIPELINE.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(missing)
    excluded_records = []
    for row in excluded_rows:
        excluded_records.append({
            "inventory_row": row_int(row),
            "tab": row["_tab"],
            "container": row["_container"],
            "section": row["_section"],
            "visible_label": row["_visible_label"],
            "visibility_failure": clean_text(row.get("visibility_failure"), ""),
            "reachability_status": clean_text(row.get("reachability_status"), clean_text(row.get("headed_reachable"), "")),
            "source_csv": row["_source_csv"],
        })
    with (OUT / "EXCLUDED_NOT_CURRENT_VISIBLE.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(excluded_records[0].keys()) if excluded_records else [
            "inventory_row", "tab", "container", "section", "visible_label",
            "visibility_failure", "reachability_status", "source_csv",
        ])
        writer.writeheader()
        writer.writerows(excluded_records)

    by_tab = Counter(r["tab"] for r in leaf_records)
    missing_by_tab = Counter(r["tab"] for r in missing)
    summary = {
        "schema": "fl4260.current_ui_inventory_tree.v1",
        "source_csv_count": len(csv_paths),
        "input_row_count": len(rows),
        "excluded_not_current_visible_count": len(excluded_rows),
        "leaf_count": len(leaf_records),
        "pipeline_gap_count": len(missing),
        "tabs": dict(sorted(by_tab.items())),
        "pipeline_gaps_by_tab": dict(sorted(missing_by_tab.items())),
        "source_csvs": [str(p.relative_to(ROOT)) for p in csv_paths],
        "tree_command": f"eza --tree --level=4 {OUT.relative_to(ROOT)}",
    }
    (OUT / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_augmented_source_csvs(csv_paths, rows)
    ROOT_TREE_MD.write_text(render_root_tree_markdown(leaf_records, summary), encoding="utf-8")

    readme = [
        "# FL-4260 Current UI Inventory Tree",
        "",
        "Filesystem tree generated from current row-level headed inventory CSVs.",
        "",
        "Run:",
        "",
        f"    eza --tree --level=4 {OUT.relative_to(ROOT)}",
        "",
        "Hierarchy:",
        "",
        "    top-level tab / visible container / intended section / leaf-control.md",
        "",
        "Each leaf records source anchor, visible label, backend owner, intended backend mutation owner, render consumer, TERM++ verdict, crop reference, and missing-pipeline status.",
        "",
        f"Total leaves: {len(leaf_records)}",
        f"Excluded not-current-visible rows: {len(excluded_rows)}",
        f"Pipeline gaps: {len(missing)}",
        "",
        f"Root FL-4260 tree doc: {ROOT_TREE_MD.relative_to(ROOT)}",
        f"Canonical single current inventory CSV: {ROOT_CURRENT_CSV.relative_to(ROOT)}",
        "",
        "Current CSV integration:",
        "",
        "Each retained row-level headed CSV now has inventory_tree_* columns carrying the same tab/container/section/leaf and pipeline status.",
        "",
        "Primary indexes:",
        "",
        "- ALL_LEAVES.csv",
        "- MISSING_PIPELINE.csv",
        "- SUMMARY.json",
        "",
    ]
    (OUT / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

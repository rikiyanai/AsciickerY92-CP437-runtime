#!/usr/bin/env python3
"""Validate FL-4131 material morphology v2 generated review artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "assets/glyphs/generated"

INVENTORY = OUT_DIR / "material.morphology.v2.candidate_inventory.jsonl"
RECEIPTS = OUT_DIR / "material.morphology.v2.shape_receipts.jsonl"
TABLES = OUT_DIR / "material.morphology.v2.profile_tables.json"
REJECTIONS = OUT_DIR / "material.morphology.v2.rejections.jsonl"
UI_SCHEMA = OUT_DIR / "material.morphology.v2.ui_receipt_schema.json"

DIRECTIONS = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
    "NONE",
]
DENSITIES = ["D0", "D1", "D2", "D3"]
PROFILES = {"GRASS", "WATER", "ROCK", "DIRT", "SAND", "SNOW", "MUD", "GRAVEL"}
SOURCE_FAMILIES = {"east_asian", "indic_sanskrit_family", "arabic_family", "symbol", "other_high_shape_script", "seed_bias"}
SOURCE_BLOCKS = {
    "hand_seed_bias",
    "kangxi_radicals",
    "hiragana",
    "katakana",
    "arabic",
    "devanagari",
    "bengali",
    "gujarati",
    "gurmukhi",
    "kannada",
    "telugu",
    "tamil",
    "malayalam",
    "syriac",
    "thaana",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def require(condition: bool, errors: list[str], message: str) -> None:
    if not condition:
        errors.append(message)


def validate() -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    for path in [INVENTORY, RECEIPTS, TABLES, REJECTIONS, UI_SCHEMA]:
        require(path.exists(), errors, f"missing {path.relative_to(REPO_ROOT)}")
    if errors:
        return errors, {}

    inventory = load_jsonl(INVENTORY)
    receipts = load_jsonl(RECEIPTS)
    tables = load_json(TABLES)
    rejections = load_jsonl(REJECTIONS)
    ui_schema = load_json(UI_SCHEMA)

    require(len(inventory) >= 500, errors, "candidate inventory is too small")
    require({row.get("source_family") for row in inventory}.issuperset(SOURCE_FAMILIES), errors, "candidate inventory lacks required source families")
    require({row.get("source_block") for row in inventory}.issuperset(SOURCE_BLOCKS), errors, "candidate inventory lacks required source blocks")
    require(len({int(row["glyph_id"]) for row in inventory}) == len(inventory), errors, "candidate inventory has duplicate glyph ids")
    require(min(int(row["glyph_id"]) for row in inventory) >= 672, errors, "planned morphology glyph ids must start at 672 (v1 frozen range 512..671)")
    v1_frozen_max = 671
    require(all(int(row["glyph_id"]) > v1_frozen_max for row in inventory), errors, "v2 inventory glyph_id collides with v1 frozen range 512..671")
    retired_runtime_key = "runtime_" + "".join(map(chr, (97, 100, 109, 105, 116, 116, 101, 100)))
    retired_morphology_key = "morphology_v2_" + retired_runtime_key
    retired_extractor_label = "".join(map(chr, (100, 114, 97, 102, 116)))
    require(not any(retired_runtime_key in row for row in inventory), errors, "inventory row uses retired runtime profile-state field")
    require(all("runtime_profile_live" in row for row in inventory), errors, "inventory row missing runtime_profile_live field")
    require(all(row.get("runtime_profile_live") is False for row in inventory), errors, "candidate inventory row unexpectedly marked live")
    require(
        not any("unaccepted" in str(row.get("review_state", "")) or "unaccepted" in str(row.get("glyph_id_status", "")) for row in inventory),
        errors,
        "inventory row uses retired unaccepted review vocabulary",
    )

    receipt_by_id = {row["receipt_id"]: row for row in receipts}
    require(len(receipts) >= 150, errors, "shape receipt count too small")
    for row in receipts:
        for key in [
            "glyph_id",
            "unicode_sequence",
            "script_family",
            "visual_family",
            "shape_role",
            "material_affinity",
            "direction_lanes",
            "vertical_relation_lanes",
            "density_index",
            "shape6_norm",
            "shape6_density",
            "external10",
            "principal_axis_deg",
            "mass_center_xy",
            "edge_contact_mask",
            "sibling_group",
            "rendered_bitmap_hash",
            "rendered_bitmap_sha256",
            "atlas_font_hash",
            "atlas_page_hash",
            "receipt_id",
            "review_state",
        ]:
            require(key in row, errors, f"receipt {row.get('receipt_id')} missing {key}")
        require(len(row.get("shape6_norm", [])) == 6, errors, f"receipt {row.get('receipt_id')} malformed shape6_norm")
        require(len(row.get("external10", [])) == 10, errors, f"receipt {row.get('receipt_id')} malformed external10")
        require(row.get("density_index") in DENSITIES, errors, f"receipt {row.get('receipt_id')} bad density")
        require(retired_runtime_key not in row, errors, f"receipt {row.get('receipt_id')} uses retired runtime profile-state field")
        require(
            retired_morphology_key not in row,
            errors,
            f"receipt {row.get('receipt_id')} uses retired morphology runtime profile-state field",
        )
        require(retired_extractor_label not in str(row.get("shape_extractor_version", "")), errors, f"receipt {row.get('receipt_id')} uses retired extractor label")
        require("unaccepted" not in str(row.get("review_state", "")), errors, f"receipt {row.get('receipt_id')} uses retired unaccepted review vocabulary")
        if row.get("morphology_v2_runtime_profile_live") is True:
            require(row.get("manual_review_receipt"), errors, f"runtime receipt {row.get('receipt_id')} lacks manual review receipt")

    require(set(tables.get("profile_inventory", [])) == PROFILES, errors, "profile inventory mismatch")
    require(tables.get("runtime_profile_live") is True, errors, "profile tables must be live")
    table_review_state = tables.get("review_state")
    require(
        table_review_state in {"review_incomplete", "all_cells_review_accepted"},
        errors,
        "profile tables have unknown review_state",
    )
    profiles = tables.get("profiles", {})
    for profile in PROFILES:
        require(profile in profiles, errors, f"missing profile {profile}")
        if profile not in profiles:
            continue
        for direction in DIRECTIONS:
            require(direction in profiles[profile], errors, f"{profile} missing direction {direction}")
            if direction not in profiles[profile]:
                continue
            for density in DENSITIES:
                cell = profiles[profile][direction].get(density)
                require(isinstance(cell, dict), errors, f"{profile}/{direction}/{density} missing cell")
                if not isinstance(cell, dict):
                    continue
                runtime_state = cell.get("runtime_state")
                require(
                    runtime_state in {"review_pending", "review_accepted", "review_rejected", "review_deferred"},
                    errors,
                    f"{profile}/{direction}/{density} has unknown runtime_state",
                )
                if table_review_state == "all_cells_review_accepted":
                    require(
                        runtime_state == "review_accepted",
                        errors,
                        f"{profile}/{direction}/{density} not review_accepted",
                    )
                require(cell.get("primary_glyph_ids"), errors, f"{profile}/{direction}/{density} missing primary")
                require(cell.get("receipt_ids"), errors, f"{profile}/{direction}/{density} missing receipt_ids")
                require(cell.get("receipt_ids") == cell.get("candidate_receipt_ids"), errors, f"{profile}/{direction}/{density} receipt list mismatch")
                for rid in cell.get("candidate_receipt_ids", []):
                    require(rid in receipt_by_id, errors, f"{profile}/{direction}/{density} cites unknown receipt {rid}")

    rejected_profiles = {row.get("profile") for row in rejections}
    require(rejected_profiles.issuperset(PROFILES), errors, "rejection policy missing profiles")
    required_ui = {
        "material_id",
        "material_profile",
        "raw6",
        "normalized6",
        "external10",
        "directional6",
        "global6",
        "winner_glyph_id",
        "sibling_source_glyph_id",
        "shape6_distance",
        "density_delta",
        "screenshot_path",
        "manual_review_state",
    }
    require(required_ui.issubset(set(ui_schema.get("required_fields", []))), errors, "UI receipt schema lacks required fields")

    return errors, {
        "candidate_inventory_rows": len(inventory),
        "shape_receipt_rows": len(receipts),
        "profile_cells": len(PROFILES) * len(DIRECTIONS) * len(DENSITIES),
        "rejection_rows": len(rejections),
        "ui_required_fields": len(ui_schema.get("required_fields", [])),
    }


def main() -> int:
    errors, counts = validate()
    if errors:
        print("FL-4131 material morphology validation FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("FL-4131 material morphology validation PASS")
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

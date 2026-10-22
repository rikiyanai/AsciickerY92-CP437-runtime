#!/usr/bin/env python3
"""Triage agent-corrected crop labels into review-only population lanes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "y92.manual_label_population_triage.v1"
MORPHOLOGY_RULES = (
    ("roadside_drain_channel", ("drain", "gutter")),
    ("exterior_air_conditioner", ("air_condition", "hvac", "bins_ac")),
    ("ivy_wall", ("ivy_wall",)),
    ("property_gate", ("wall_gate", "property_gate")),
    ("property_boundary_block_wall", ("concrete_block_wall", "stone_wall")),
    ("property_boundary_hedge", ("vegetation_hedge", "hedge_beside")),
    ("property_boundary_fence", ("lattice_fence", "fenced_industrial", "fenced_utility")),
    ("sidewalk_raised_curb", ("curb_sidewalk", "sidewalk_edge")),
    ("painted_road_shoulder", ("road_edge_line", "white_road_paint", "bike_lane")),
    ("frontage_planting_strip", ("potted_plants", "flower_planter", "roadside_bush")),
)
CUSTOM_TERMS = (
    "numabe", "mikasa", "miyoshi", "respa", "station_sign", "station_roof",
    "station_entrance", "storefront_sign", "store_sign", "shop_sign",
    "sign_store", "sign_shop", "building_sign", "japanese_sign", "japanese_text",
    "advertising_banner", "notice_board", "doorway_poster", "sign_poster",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(label: str) -> dict[str, Any]:
    normalized = label.strip().casefold()
    if "too_small_discard" in normalized:
        return {
            "proposed_semantic_label": None,
            "population_role": "discard",
            "custom_extraction_required": False,
            "reason": "agent marked the crop too small to use",
        }
    for semantic_class, terms in MORPHOLOGY_RULES:
        if any(term in normalized for term in terms):
            return {
                "proposed_semantic_label": semantic_class,
                "population_role": "generic_morphology",
                "custom_extraction_required": False,
                "reason": "transparent label-keyword morphology proposal",
            }
    if any(term in normalized for term in CUSTOM_TERMS):
        return {
            "proposed_semantic_label": label,
            "population_role": "custom_identity",
            "custom_extraction_required": True,
            "reason": "location-specific identity, text, or facade candidate",
        }
    return {
        "proposed_semantic_label": label,
        "population_role": "generic_fixture",
        "custom_extraction_required": False,
        "reason": "repeatable object or surface candidate",
    }


def compile_triage(csv_paths: list[Path]) -> dict[str, Any]:
    rows = []
    inputs = []
    seen = set()
    for path in sorted(path.resolve() for path in csv_paths):
        inputs.append({"path": str(path), "sha256": _sha256(path)})
        with path.open(newline="", encoding="utf-8") as stream:
            for source in csv.DictReader(stream):
                annotation_id = str(source.get("annotation_id") or "").strip()
                label = str(source.get("correct_label") or "").strip()
                if not annotation_id or not label:
                    raise ValueError(f"{path} contains a row without annotation_id/correct_label")
                if annotation_id in seen:
                    raise ValueError(f"duplicate annotation_id across label CSVs: {annotation_id}")
                seen.add(annotation_id)
                rows.append({
                    "annotation_id": annotation_id,
                    "agent_corrected_label": label,
                    "agent_corrected_label_authoritative": False,
                    **classify(label),
                    "operator_review_required": True,
                })
    counts: dict[str, int] = {}
    for row in rows:
        role = row["population_role"]
        counts[role] = counts.get(role, 0) + 1
    return {
        "schema": SCHEMA,
        "stage": "pre_operator_review_population_triage",
        "inputs": inputs,
        "policy": {
            "agent_labels_authoritative": False,
            "triage_is_acceptance": False,
            "custom_identity_requires_metadata_after_acceptance": True,
            "generic_morphology_is_locale_grammar_evidence_not_world_position": True,
        },
        "counts": {"annotations": len(rows), **dict(sorted(counts.items()))},
        "proposals": sorted(rows, key=lambda row: row["annotation_id"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = compile_triage(args.labels)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[manual-label-triage] {document['counts']}")
    print(f"[manual-label-triage] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

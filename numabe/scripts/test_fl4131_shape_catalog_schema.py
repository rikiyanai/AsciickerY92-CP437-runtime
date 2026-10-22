#!/usr/bin/env python3
"""FL-4131 shape catalog status/metric-label contract."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
PLAN = REPO_ROOT / "docs/plans/2026-06-01-fl4131-glyph-shape-lab-plan.md"
EXPECTED_SHAPE6_MODEL = "harri6_internal6_regions_v1"
EXPECTED_SHAPE6_CENTERS = [
    [0.25, 0.25],
    [0.75, 0.25],
    [0.25, 0.50],
    [0.75, 0.50],
    [0.25, 0.75],
    [0.75, 0.75],
]
EXPECTED_SHAPE6_RADIUS = {
    "x_cell_fraction": 0.22,
    "y_cell_fraction": 0.18,
}


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    plan_text = PLAN.read_text(encoding="utf-8")
    errors: list[str] = []

    if catalog.get("metric_model") != "atlas_summary_metrics_v1":
        errors.append(
            f"metric_model is {catalog.get('metric_model')!r}, expected atlas_summary_metrics_v1"
        )
    if catalog.get("metric_model") == "harri_style_regions_v1":
        errors.append("stale harri_style_regions_v1 label remains")
    if catalog.get("shape6_region_model") != EXPECTED_SHAPE6_MODEL:
        errors.append(
            f"shape6_region_model is {catalog.get('shape6_region_model')!r}, "
            f"expected {EXPECTED_SHAPE6_MODEL}"
        )
    if catalog.get("shape6_region_centers") != EXPECTED_SHAPE6_CENTERS:
        errors.append(
            f"shape6_region_centers is {catalog.get('shape6_region_centers')!r}, "
            f"expected {EXPECTED_SHAPE6_CENTERS}"
        )
    if catalog.get("shape6_region_radius") != EXPECTED_SHAPE6_RADIUS:
        errors.append(
            f"shape6_region_radius is {catalog.get('shape6_region_radius')!r}, "
            f"expected {EXPECTED_SHAPE6_RADIUS}"
        )

    entries = catalog.get("entries")
    if not isinstance(entries, list) or len(entries) != 136:
        errors.append(
            f"entries length is {len(entries) if isinstance(entries, list) else 'missing'}, expected 136"
        )

    required_shape6 = {
        "shape6",
        "shape6_norm",
        "shape6_density",
        "shape6_asymmetry_lr",
        "shape6_asymmetry_tb",
        "shape6_diag_ne_sw",
        "shape6_diag_nw_se",
    }
    for index, entry in enumerate(entries if isinstance(entries, list) else []):
        missing = sorted(required_shape6 - set(entry))
        if missing:
            errors.append(f"entry {index} missing shape6 fields: {missing}")
            break
        if len(entry["shape6"]) != 6 or len(entry["shape6_norm"]) != 6:
            errors.append(f"entry {index} shape6 vectors must have 6 components")
            break
        if any(not (0.0 <= float(v) <= 1.0) for v in entry["shape6_norm"]):
            errors.append(f"entry {index} shape6_norm values must be 0..1")
            break

    if "current generated checkpoint baseline" not in plan_text:
        errors.append("shape-lab plan must call the seven presets a checkpoint baseline")
    if "the 7 generated shape presets are canonical" in plan_text:
        errors.append("shape-lab plan must not call the seven presets canonical quality truth")
    if "Default/origin display remains normal CP437" not in plan_text:
        errors.append("shape-lab plan must keep normal CP437 as the default/origin baseline")
    if "rather than CP437 approximations" in plan_text:
        errors.append("shape-lab plan must not frame extended GlyphIds as replacing CP437 by default")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print("PASS: FL-4131 shape catalog uses truthful summary metric/status labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

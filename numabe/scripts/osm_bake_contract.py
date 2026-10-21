"""Canonical OSM mesh-bake coverage contract.

One owner for parsing and evaluating asciiid ``BakeCoverage`` output across
all OSM front doors. Callers remain adapters: they run asciiid, then delegate
the stuck-at-baseline decision to this module.
"""

from __future__ import annotations

import re


# WHY 128: terrain height bake quantizes to multiples of _TERRAIN_HEIGHT_QUANTIZATION_STEP.
# An off-grid baseline (old value 120, 120 % 16 = 8) caused edge samples to round below
# baseline, fail the overwrite-height gate, and leave cells at baseline in raised terrain —
# terrain holes (FL-1181, FL-2546, FL-2573).  128 = 8 × 16 is the nearest on-grid value.
# INVARIANT: BAKE_COVERAGE_BASELINE % _TERRAIN_HEIGHT_QUANTIZATION_STEP == 0.
# (mirrors HEIGHT_SCALE in terrain.h / a3d_format.py; keep in sync if that constant changes)
_TERRAIN_HEIGHT_QUANTIZATION_STEP = 16
BAKE_COVERAGE_BASELINE = 128
assert BAKE_COVERAGE_BASELINE % _TERRAIN_HEIGHT_QUANTIZATION_STEP == 0, (
    f"BAKE_COVERAGE_BASELINE={BAKE_COVERAGE_BASELINE} is not on the "
    f"{_TERRAIN_HEIGHT_QUANTIZATION_STEP}-step quantization grid "
    f"({BAKE_COVERAGE_BASELINE % _TERRAIN_HEIGHT_QUANTIZATION_STEP} remainder). "
    "Edge samples will round below baseline → overwrite-height gate skips writes → terrain holes. "
    f"Change BAKE_COVERAGE_BASELINE to the nearest multiple of {_TERRAIN_HEIGHT_QUANTIZATION_STEP}."
)
TOPOLOGY_BAKE_MAX_STUCK_PCT = 30.0
BUILDING_BAKE_MAX_STUCK_PCT = 20.0

_BAKE_COVERAGE_PATTERN = re.compile(
    r"\[MCP\] BakeCoverage: name=(\S+) footprint=(\d+) above_baseline=(\d+) at_baseline=(\d+)"
)


def parse_bake_coverage(stdout: str | None) -> list[dict]:
    """Parse per-instance BakeCoverage rows emitted by asciiid."""
    results = []
    for line in (stdout or "").splitlines():
        match = _BAKE_COVERAGE_PATTERN.search(line)
        if not match:
            continue
        results.append({
            "name": match.group(1),
            "footprint_cells": int(match.group(2)),
            "above_baseline": int(match.group(3)),
            "at_baseline": int(match.group(4)),
        })
    return results


def evaluate_bake_coverage(coverage: list[dict], max_stuck_pct: float) -> dict:
    """Evaluate whether any baked instance footprint stays too long at baseline.

    Returns a pure data verdict so each front door can print/log/raise in its
    own style without re-owning the hole-detection algorithm.
    """
    if not coverage:
        return {
            "ok": True,
            "has_data": False,
            "total_instances": 0,
            "stuck_instances": [],
            "instances": [],
        }

    stuck = []
    instances = []
    for cov in coverage:
        footprint_cells = int(cov.get("footprint_cells", 0))
        at_baseline = int(cov.get("at_baseline", 0))
        above_baseline = int(cov.get("above_baseline", 0))
        entry = {
            "name": cov.get("name", "(unknown)"),
            "footprint_cells": footprint_cells,
            "at_baseline": at_baseline,
            "above_baseline": above_baseline,
        }
        if footprint_cells > 0:
            entry["stuck_pct"] = round(at_baseline / footprint_cells * 100.0, 1)
            if entry["stuck_pct"] > max_stuck_pct:
                stuck.append(entry)
        instances.append(entry)

    return {
        "ok": len(stuck) == 0,
        "has_data": True,
        "total_instances": len(coverage),
        "stuck_instances": stuck,
        "instances": instances,
    }

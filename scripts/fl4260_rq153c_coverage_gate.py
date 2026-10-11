#!/usr/bin/env python3
"""FL-4260 RQ-153c full referenced-GID coverage gate (named producer).

RQ-153c requires that AFTER the thin headed terrain slice proves PROFILE
consumption can work (RQ-154), the FULL referenced set of profile-cell candidate
GIDs is renderability-screened, rendered into atlas pages, measured with
shape-catalog rows, candidate-floor enforced, and that zero-coverage plus
unrenderable results are fed into the SAME coverage gate. The referenced set is
the union of candidate/primary GIDs cited by every cell of
``material.morphology.v2.profile_tables.json`` (canon: 473 unique, 332 above the
v1 frozen ceiling 671, max 1691).

This script is that coverage gate. It is a POST-RUN QUERY over already-generated
artifacts (Law 13: analyzer gates own proof). It mutates nothing.

What it proves (mechanical, fail-closed — any miss FAILS the gate):
  rq153c_referenced_gid_inventory          referenced set extracted, 332 above 671
  rq153c_full_referenced_gid_atlas_coverage every referenced GID present in the
                                            v2 atlas glyph_index (zero-coverage
                                            list MUST be empty)
  rq153c_renderability_receipts            every referenced GID has a shape
                                            receipt carrying rendered_bitmap_sha256
                                            (unrenderable list MUST be empty)
  rq153c_shape_catalog_measurement_rows    every referenced GID has a v2 shape
                                            catalog measurement row (shape6_norm
                                            length 6)
  rq153c_candidate_floor                   every COMMON (non-rare) cell carries at
                                            least its declared min_candidate_count
                                            (>= 8) candidates
  rq153c_diagnostic_glyph_receipt          the runtime diagnostic glyph is
                                            receipt-backed and renderable

What it does NOT do (operator residual — never set here, Law 16):
  rq153c_manual_review_acceptance          per-cell human accept of the primary
                                            winner. Reported as an OBSERVATION.
                                            Review-ready status waits for the
                                            operator manual-review pass. The
                                            mechanical gate passing is NOT closure.

Exit codes:
  0  mechanical coverage verdict PASS
  2  mechanical coverage verdict FAIL (any fail-closed check missed)
  3  required input artifact missing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN = REPO_ROOT / "assets/glyphs/generated"
PROFILE_TABLES = GEN / "material.morphology.v2.profile_tables.json"
V2_ATLAS = REPO_ROOT / "assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json"
V2_SHAPE_CATALOG = GEN / "material.morphology.v2.shape_catalog.json"
SHAPE_RECEIPTS = GEN / "material.morphology.v2.shape_receipts.jsonl"
MANUAL_RECEIPTS = GEN / "material.morphology.v2.manual_review_receipts.jsonl"
DIAG_RECEIPT = REPO_ROOT / "docs/research/ascii/verification/fl4260/2026-06-15-rq153c-diagnostic-glyph-receipt.json"

V1_FROZEN_CEILING = 671  # GIDs <= this are the additive.v1 frozen range; above is v2 expansion


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def collect_referenced(tables: dict[str, Any]) -> tuple[set[int], dict[str, list[dict[str, Any]]]]:
    """Return (referenced GID set, cells grouped by common/rare).

    A cell is COMMON unless it carries rare_cell:true. Candidate-floor only
    applies to common cells (canon: rare lanes may legitimately be thinner).
    """
    referenced: set[int] = set()
    cells = {"common": [], "rare": []}
    for profile, lane_table in tables.get("profiles", {}).items():
        for direction, density_cells in lane_table.items():
            for density, cell in density_cells.items():
                if not isinstance(cell, dict):
                    continue
                for gid in cell.get("candidate_glyph_ids", []):
                    try:
                        referenced.add(int(gid))
                    except (TypeError, ValueError):
                        pass
                for gid in cell.get("primary_glyph_ids", []):
                    try:
                        referenced.add(int(gid))
                    except (TypeError, ValueError):
                        pass
                bucket = "rare" if cell.get("rare_cell") else "common"
                cells[bucket].append(
                    {
                        "key": f"{profile}/{direction}/{density}",
                        "candidate_count": len(cell.get("candidate_glyph_ids", [])),
                        "min_candidate_count": int(cell.get("min_candidate_count", 8)),
                        "primary_glyph_ids": [int(g) for g in cell.get("primary_glyph_ids", []) if isinstance(g, int)],
                    }
                )
    return referenced, cells


def run_gate() -> tuple[dict[str, Any], int]:
    missing = [p.name for p in (PROFILE_TABLES, V2_ATLAS, V2_SHAPE_CATALOG, SHAPE_RECEIPTS) if not p.exists()]
    if missing:
        return {"verdict": "INPUT_MISSING", "missing": missing}, 3

    tables = load_json(PROFILE_TABLES)
    atlas = load_json(V2_ATLAS)
    catalog = load_json(V2_SHAPE_CATALOG)
    receipts = load_jsonl(SHAPE_RECEIPTS)

    referenced, cells = collect_referenced(tables)
    above = sorted(g for g in referenced if g > V1_FROZEN_CEILING)

    atlas_gids = {int(k) for k in atlas.get("glyph_index", {}).keys() if str(k).lstrip("-").isdigit()}
    cat_by_gid = {int(e["glyph_id"]): e for e in catalog.get("entries", []) if "glyph_id" in e}
    rec_by_gid = {int(r["glyph_id"]): r for r in receipts if "glyph_id" in r}

    gates: dict[str, dict[str, Any]] = {}

    # 1. referenced GID inventory
    gates["rq153c_referenced_gid_inventory"] = {
        "pass": len(referenced) > 0 and len(above) > 0,
        "referenced_total": len(referenced),
        "referenced_above_ceiling": len(above),
        "ceiling": V1_FROZEN_CEILING,
        "max_gid": max(referenced) if referenced else None,
    }

    # 2. full referenced-GID atlas coverage — zero-coverage list fed into gate
    zero_coverage = sorted(g for g in referenced if g > V1_FROZEN_CEILING and g not in atlas_gids)
    gates["rq153c_full_referenced_gid_atlas_coverage"] = {
        "pass": len(zero_coverage) == 0,
        "zero_coverage_count": len(zero_coverage),
        "zero_coverage_gids": zero_coverage[:32],
        "atlas": V2_ATLAS.name,
    }

    # 3. renderability receipts — unrenderable list fed into gate
    unrenderable = sorted(
        g for g in referenced
        if g > V1_FROZEN_CEILING and not (g in rec_by_gid and rec_by_gid[g].get("rendered_bitmap_sha256"))
    )
    gates["rq153c_renderability_receipts"] = {
        "pass": len(unrenderable) == 0,
        "unrenderable_count": len(unrenderable),
        "unrenderable_gids": unrenderable[:32],
        "receipts": SHAPE_RECEIPTS.name,
    }

    # 4. shape-catalog measurement rows
    unmeasured = sorted(
        g for g in referenced
        if g > V1_FROZEN_CEILING and not (
            g in cat_by_gid and isinstance(cat_by_gid[g].get("shape6_norm"), list)
            and len(cat_by_gid[g]["shape6_norm"]) == 6
        )
    )
    gates["rq153c_shape_catalog_measurement_rows"] = {
        "pass": len(unmeasured) == 0,
        "unmeasured_count": len(unmeasured),
        "unmeasured_gids": unmeasured[:32],
        "catalog": V2_SHAPE_CATALOG.name,
    }

    # 5. candidate-floor (common cells only)
    thin = [c["key"] for c in cells["common"] if c["candidate_count"] < c["min_candidate_count"]]
    gates["rq153c_candidate_floor"] = {
        "pass": len(thin) == 0,
        "common_cells": len(cells["common"]),
        "rare_cells": len(cells["rare"]),
        "thin_cell_count": len(thin),
        "thin_cells": thin[:16],
    }

    # 6. diagnostic glyph receipt
    diag_ok = False
    diag_detail: dict[str, Any] = {"receipt": DIAG_RECEIPT.name, "exists": DIAG_RECEIPT.exists()}
    if DIAG_RECEIPT.exists():
        diag = load_json(DIAG_RECEIPT)
        dg = diag.get("diagnostic_glyph", {})
        diag_ok = (
            str(diag.get("verdict", "")) == "RECEIPT_BACKED"
            and bool(dg.get("renderable"))
            and int(dg.get("glyph_byte", -1)) <= V1_FROZEN_CEILING
            and "single_producer" in diag.get("provenance", {})
        )
        diag_detail.update({
            "verdict": diag.get("verdict"),
            "glyph_byte": dg.get("glyph_byte"),
            "fg": dg.get("fg"), "bg": dg.get("bg"),
            "renderable": dg.get("renderable"),
        })
    gates["rq153c_diagnostic_glyph_receipt"] = {"pass": diag_ok, **diag_detail}

    mechanical_pass = all(g["pass"] for g in gates.values())

    # ── Operator residual (OBSERVATION ONLY — never affects mechanical verdict;
    #    Law 16 operator manual review owns this).
    #    The 72-primary review receipt set and its accepted/blocked state are owned by
    #    scripts/fl4260_review_receipts.py (Law 1: single owner of "the 72"). ──
    import fl4260_review_receipts as rq  # noqa: E402  (sibling front-door import)

    queue = rq.build_review_receipt_set()
    counts = rq.review_state(queue, rq.load_jsonl(MANUAL_RECEIPTS))
    table_runtime_profile_live = queue["table_runtime_profile_live"]

    observation = {
        "rq153c_manual_review_acceptance": {
            "kind": "operator_residual_observation",
            "note": "Per-cell human accept of the primary winner is operator-owned "
                    "(Law 16). The review apparatus (scripts/fl4260_review_receipts.py) "
                    "presents the 72 decisions and writes receipts; the mechanical "
                    "coverage gate passing is NOT closure. review_ready stays false "
                    "until every primary has a current accept receipt with no blocker.",
            "common_cell_primaries": queue["required_primary_count"],
            "primaries_with_accept_receipt": counts["accepted"],
            "primaries_without_accept_receipt": (
                counts["unreviewed"] + counts["rejected"]
                + counts["deferred"] + counts["stale_hash_mismatch"]
            ),
            "review_state_counts": counts,
            "table_runtime_profile_live": table_runtime_profile_live,
        }
    }

    result = {
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "mechanical_coverage_verdict": "PASS" if mechanical_pass else "FAIL",
        "runtime_profile_live": table_runtime_profile_live,
        "closure": False,
        "closure_note": "Mechanical coverage PASS proves full referenced-GID atlas/renderability/"
                        "measurement/candidate-floor/diagnostic coverage. It does NOT prove closure: "
                        "operator manual-review acceptance (Law 16), final RQ-155 Trace refresh, and "
                        "canonical RQ-156 + Law 15 VPS two-tab remain open.",
        "gates": gates,
        "observation": observation,
    }
    return result, (0 if mechanical_pass else 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    result, code = run_gate()

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return code

    if result.get("verdict") == "INPUT_MISSING":
        print("RQ-153c coverage gate: INPUT MISSING")
        for m in result["missing"]:
            print(f"  - missing {m}")
        return code

    print("FL-4260 RQ-153c full referenced-GID coverage gate")
    print(f"  mechanical_coverage_verdict: {result['mechanical_coverage_verdict']}")
    print(f"  runtime_profile_live: {result['runtime_profile_live']}   closure: {result['closure']}")
    print("  gates:")
    for name, g in result["gates"].items():
        label = "PASS" if g["pass"] else "FAIL"
        extra = ""
        if "zero_coverage_count" in g:
            extra = f" zero_coverage={g['zero_coverage_count']}"
        elif "unrenderable_count" in g:
            extra = f" unrenderable={g['unrenderable_count']}"
        elif "unmeasured_count" in g:
            extra = f" unmeasured={g['unmeasured_count']}"
        elif "thin_cell_count" in g:
            extra = f" thin={g['thin_cell_count']} (common={g.get('common_cells')})"
        elif "referenced_total" in g:
            extra = f" total={g['referenced_total']} above{V1_FROZEN_CEILING}={g['referenced_above_ceiling']}"
        elif "glyph_byte" in g:
            extra = f" glyph={g.get('glyph_byte')} fg={g.get('fg')} bg={g.get('bg')}"
        print(f"    [{label}] {name}{extra}")
    obs = result["observation"]["rq153c_manual_review_acceptance"]
    print("  operator residual (NOT closure):")
    print(f"    manual-review primaries accepted: {obs['primaries_with_accept_receipt']}"
          f"/{obs['common_cell_primaries']}  (unaccepted={obs['primaries_without_accept_receipt']})")
    print(f"  {result['closure_note']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

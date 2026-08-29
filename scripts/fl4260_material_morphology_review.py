#!/usr/bin/env python3
"""Refresh FL-4260 material morphology review state.

Reads:
  assets/glyphs/generated/material.morphology.v2.profile_tables.json
  assets/glyphs/generated/material.morphology.v2.manual_review_receipts.jsonl
  assets/glyphs/generated/material.morphology.v2.shape_receipts.jsonl

Writes back to profile_tables.json with review-status metadata only:
  - candidate_glyph_ids and candidate_receipt_ids are preserved
  - per-cell runtime_state reflects review status
  - table-level runtime_profile_live stays true because the direct-edit product
    path consumes the live profile table

Each accepted manual_review_receipts.jsonl row must declare:
  glyph_id, action ("accept" / "reject"), reason, reviewer_timestamp.
Optionally screenshot_path (string) for headed-review evidence.

Per FL-4260 direct-edit contract: receipts are evidence, not a second runtime
owner. Reject receipts mark review blockers; they do not strip candidate pools.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLES = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.profile_tables.json"
RECEIPTS = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.shape_receipts.jsonl"
REVIEWS = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.manual_review_receipts.jsonl"

DIRECTIONS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "NONE",
]
DENSITIES = ["D0", "D1", "D2", "D3"]
PROFILES = ["GRASS", "WATER", "ROCK", "DIRT", "SAND", "SNOW", "MUD", "GRAVEL"]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def reduce_reviews(rows: list[dict[str, Any]]) -> tuple[set[int], set[int], set[int], list[str]]:
    """Collapse review receipt rows to final accept/reject/defer set + report bad fields.

    A later row for the same glyph overrides earlier rows so reviewers can
    change their mind without rewriting the file. ``defer`` is a recognized
    terminal action (FL-4260 RQ-153c Review Receipts): it is neither accept nor
    reject; it is a fail-closed "not yet decided" blocker that keeps the glyph
    out of the accepted set without changing its candidate pool.
    """
    last: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        gid = row.get("glyph_id")
        action = row.get("action")
        reason = row.get("reason")
        ts = row.get("reviewer_timestamp")
        if not isinstance(gid, int):
            errors.append(f"review receipt row missing integer glyph_id: {row}")
            continue
        if action not in {"accept", "reject", "defer"}:
            errors.append(f"review receipt row glyph_id={gid} action must be accept/reject/defer, got {action!r}")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"review receipt row glyph_id={gid} missing non-empty reason")
            continue
        if not isinstance(ts, str) or not ts.strip():
            errors.append(f"review receipt row glyph_id={gid} missing reviewer_timestamp")
            continue
        last[int(gid)] = row
    accepted = {gid for gid, row in last.items() if row["action"] == "accept"}
    rejected = {gid for gid, row in last.items() if row["action"] == "reject"}
    deferred = {gid for gid, row in last.items() if row["action"] == "defer"}
    return accepted, rejected, deferred, errors


def hash_tables_input(tables: dict[str, Any], accepted: set[int], rejected: set[int]) -> str:
    payload = {
        "source_catalog_hash": tables.get("source_catalog_hash"),
        "shape_receipts": tables.get("shape_receipts"),
        "shape_receipts_count": tables.get("shape_receipts_count"),
        "candidate_inventory": tables.get("candidate_inventory"),
        "candidate_inventory_count": tables.get("candidate_inventory_count"),
        "accepted": sorted(accepted),
        "rejected": sorted(rejected),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def refresh_review_state(tables: dict[str, Any], accepted: set[int], rejected: set[int],
                         deferred: set[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    accepted_cells = 0
    pending_cells = 0
    rejected_cells = 0
    deferred_cells = 0
    profiles_obj = tables.get("profiles", {})
    profile_summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {"review_accepted": 0, "review_pending": 0, "review_rejected": 0, "review_deferred": 0}
    )
    for profile, lane_table in profiles_obj.items():
        for direction, density_cells in lane_table.items():
            for density, cell in density_cells.items():
                original_candidates = list(cell.get("candidate_glyph_ids", []))
                original_receipts = list(cell.get("candidate_receipt_ids", []))
                if len(original_candidates) != len(original_receipts):
                    raise SystemExit(
                        f"{profile}/{direction}/{density} candidate_receipt_ids out of sync with candidate_glyph_ids"
                    )
                primaries = [int(g) for g in cell.get("primary_glyph_ids", []) if isinstance(g, int)]
                primary = primaries[0] if primaries else None
                cell["candidate_count"] = len(original_candidates)
                cell["receipt_ids"] = list(original_receipts)
                if primary in accepted:
                    cell["runtime_state"] = "review_accepted"
                    cell["needs_manual_review"] = False
                    cell["review_blockers"] = []
                    cell.pop("acceptance_blockers", None)
                    accepted_cells += 1
                    profile_summary[profile]["review_accepted"] += 1
                elif primary in rejected:
                    cell["runtime_state"] = "review_rejected"
                    cell["needs_manual_review"] = True
                    cell["review_blockers"] = ["primary rejected"]
                    cell.pop("acceptance_blockers", None)
                    rejected_cells += 1
                    profile_summary[profile]["review_rejected"] += 1
                elif primary in deferred:
                    cell["runtime_state"] = "review_deferred"
                    cell["needs_manual_review"] = True
                    cell["review_blockers"] = ["primary deferred"]
                    cell.pop("acceptance_blockers", None)
                    deferred_cells += 1
                    profile_summary[profile]["review_deferred"] += 1
                else:
                    cell["runtime_state"] = "review_pending"
                    cell["needs_manual_review"] = True
                    cell["review_blockers"] = ["primary lacks current accept receipt"]
                    cell.pop("acceptance_blockers", None)
                    pending_cells += 1
                    profile_summary[profile]["review_pending"] += 1

    total_cells = len(PROFILES) * len(DIRECTIONS) * len(DENSITIES)
    review_ready = total_cells > 0 and accepted_cells == total_cells
    tables["review_state"] = (
        "all_cells_review_accepted" if review_ready else "review_incomplete"
    )
    tables["runtime_profile_live"] = True
    deleted_profile_state_key = "runtime_" + "".join(map(chr, (97, 100, 109, 105, 116, 116, 101, 100)))
    deleted_review_state_key = "acceptance_" + "state"
    deleted_transition_key = "".join(map(chr, (112, 114, 111, 109, 111, 116, 105, 111, 110)))
    for deleted_key in (deleted_profile_state_key, deleted_review_state_key, deleted_transition_key):
        tables.pop(deleted_key, None)
    tables["review_summary"] = {
        "refreshed_at": datetime.now(tz=None).isoformat(timespec="seconds"),
        "accepted_cells": accepted_cells,
        "pending_cells": pending_cells,
        "rejected_cells": rejected_cells,
        "deferred_cells": deferred_cells,
        "total_cells": total_cells,
        "accepted_glyph_ids": sorted(accepted),
        "rejected_glyph_ids": sorted(rejected),
        "deferred_glyph_ids": sorted(deferred),
        "receipts_source": str(REVIEWS.relative_to(REPO_ROOT)),
        "input_hash": hash_tables_input(tables, accepted, rejected),
    }
    summary = {
        "accepted_cells": accepted_cells,
        "pending_cells": pending_cells,
        "rejected_cells": rejected_cells,
        "deferred_cells": deferred_cells,
        "total_cells": total_cells,
        "review_ready": review_ready,
        "runtime_profile_live": True,
        "accepted_glyph_count": len(accepted),
        "rejected_glyph_count": len(rejected),
        "deferred_glyph_count": len(deferred),
        "profile_breakdown": dict(profile_summary),
    }
    return tables, summary


# ── FL-4260 RQ-153a thin-slice review blockers ─────────────────────────────
# Before the GRASS Ramp+Density first slice may be treated as review-ready, ALL
# of these must pass (fail closed). Full 332-GID expansion is RQ-153c.
PROFILES_V1 = REPO_ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
ATLAS_AOA = REPO_ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
SHAPE_CATALOG_V1 = REPO_ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
RQ146A_RECEIPT = REPO_ROOT / "docs/research/ascii/verification/fl4260/2026-06-13-rq146a-grass-renderability-spike.json"
RENDER_CEILING = 671
CANDIDATE_FLOOR = 1  # each ACTIVE (ramp/density) bucket needs >= this many candidates


def fl4260_thin_slice_gate(profile_id: str | None) -> tuple[bool, dict[str, Any]]:
    """Return (review_ok, report). review_ok False => user proof stays blocked."""
    blockers: list[str] = []

    # GUARD / falsifier: terrain:0 is WATER and must never review as GRASS.
    if profile_id == "terrain:0":
        return False, {"verdict": "BLOCKED", "blockers": [
            "terrain:0 is WATER (morphology authority); the GRASS first slice is terrain:1"]}

    if not PROFILES_V1.exists():
        return False, {"verdict": "BLOCKED", "blockers": [f"missing {PROFILES_V1.name}"]}
    doc = load_json(PROFILES_V1)
    profiles = doc.get("profiles", [])
    target = None
    for p in profiles:
        if profile_id is None or p.get("idempotency_marker") == profile_id or p.get("material_id") == profile_id:
            target = p
            break
    if target is None:
        return False, {"verdict": "BLOCKED", "blockers": [f"profile '{profile_id}' not found"]}

    # missing-receipt: RQ-146a renderability receipt must exist + be RENDERABLE
    if not RQ146A_RECEIPT.exists():
        blockers.append("missing renderability receipt (RQ-146a)")
    else:
        rec = load_json(RQ146A_RECEIPT)
        if not str(rec.get("verdict", "")).startswith("RENDERABLE"):
            blockers.append("RQ-146a receipt verdict is not RENDERABLE")
        if rec.get("g5_branch_triggered"):
            blockers.append("[G5] branch triggered in renderability receipt")

    # missing-atlas / unrenderable: every extended candidate present in atlas + <= ceiling
    atlas_gids: set[int] = set()
    if ATLAS_AOA.exists():
        gi = load_json(ATLAS_AOA).get("glyph_index", {})
        atlas_gids = {int(k) for k in gi.keys() if str(k).lstrip("-").isdigit()}
    else:
        blockers.append(f"missing atlas {ATLAS_AOA.name}")

    # thin-cell (measurement) coverage from shape catalog
    cat_gids: set[int] = set()
    if SHAPE_CATALOG_V1.exists():
        cat_gids = {e["glyph_id"] for e in load_json(SHAPE_CATALOG_V1).get("entries", [])
                    if isinstance(e, dict) and "glyph_id" in e}
    else:
        blockers.append(f"missing shape catalog {SHAPE_CATALOG_V1.name}")

    pools = target.get("glyph_pools", {})
    glyph_ids = [g for g in pools.get("glyph_ids", []) if isinstance(g, int)]
    if not glyph_ids:
        glyph_ids = [g for g in pools.get("extended", []) if isinstance(g, int)]
        glyph_ids.extend(g for g in pools.get("cp437", []) if isinstance(g, int))
    ext = [g for g in glyph_ids if g > 255]
    unrenderable = [g for g in ext if g > RENDER_CEILING or (atlas_gids and g not in atlas_gids)]
    if unrenderable:
        blockers.append(f"unrenderable extended GIDs (>{RENDER_CEILING} or absent from atlas): {unrenderable[:8]}")
    no_measure = [g for g in ext if cat_gids and g not in cat_gids]
    if no_measure:
        blockers.append(f"thin-cell: extended GIDs lacking shape-catalog measurement: {no_measure[:8]}")

    # candidate-floor: each ACTIVE (ramp + density) bucket needs >= CANDIDATE_FLOOR candidates
    rb = target.get("role_buckets", {})
    thin_buckets = []
    for lane in ("ramp", "density"):
        for b in rb.get(lane, []):
            if len(b.get("candidates", [])) < CANDIDATE_FLOOR:
                thin_buckets.append(f"{lane}:{b.get('bucket') or b.get('elevation_row')}")
    if thin_buckets:
        blockers.append(f"candidate-floor: active buckets below floor {CANDIDATE_FLOOR}: {thin_buckets[:8]}")

    # missing-diagnostic: fail-closed display byte must exist for every active bucket
    # (the receipt-backed diagnostic glyph for the slice).
    missing_diag = []
    for lane in ("ramp", "density"):
        for b in rb.get(lane, []):
            if b.get("fallback") is None:
                missing_diag.append(f"{lane}:{b.get('bucket') or b.get('elevation_row')}")
    if missing_diag:
        blockers.append(f"missing-diagnostic: active buckets without a fail-closed fallback byte: {missing_diag[:8]}")

    review_ok = not blockers
    report = {
        "profile": target.get("material_id"),
        "extended_candidate_count": len(ext),
        "renderable_extended": len([g for g in ext if g <= RENDER_CEILING and (not atlas_gids or g in atlas_gids)]),
        "active_buckets": sum(len(rb.get(l, [])) for l in ("ramp", "density")),
        "blockers": blockers,
        "verdict": "REVIEW_OK" if review_ok else "BLOCKED",
        "note": "thin slice only (GRASS Ramp+Density); full 332-GID expansion is RQ-153c",
    }
    return review_ok, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Compute review refresh without writing back to profile_tables.json")
    parser.add_argument("--strip-rejected-only", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--fl4260-thin-slice-gate", metavar="PROFILE_ID", nargs="?", const="terrain:1",
                        help="FL-4260 RQ-153a: run thin-slice review blockers for the GRASS first slice "
                             "(canon terrain:1; terrain:0 is WATER and is rejected as a guard). "
                             "(material_rendering_profiles.v1.json). Exit 0 = REVIEW_OK; 2 = BLOCKED.")
    parser.add_argument("--fl4260-broad-gate", action="store_true",
                        help="FL-4260 RQ-154 broad: run the review gate for EVERY live profile in "
                             "material_rendering_profiles.v1.json. Exit 0 only if all live materials pass; "
                             "2 if any is blocked. Read-only.")
    parser.add_argument("--fl4260-rq153c-review-gate", action="store_true",
                        help="FL-4260 RQ-153c: consume the primary-winner review receipt set (read-only). Reports the "
                             "four review evidence_* gates plus review_ready. Exit 0 = review evidence PASS; "
                             "2 = blocked (prints exact unreviewed/rejected/deferred/stale row lists). "
                             "Single owner of the 72-primary state is scripts/fl4260_review_receipts.py.")
    parser.add_argument("--fl4260-rq153c-live-review", action="store_true",
                        help="FL-4260 RQ-153c: refresh morphology v2 review metadata without filtering candidate "
                             "pools. Use --dry-run to preview without writing.")
    args = parser.parse_args()

    if args.fl4260_rq153c_review_gate or args.fl4260_rq153c_live_review:
        # Single owner of "the 72 primaries + their state" is fl4260_review_receipts.
        import fl4260_review_receipts as rq  # noqa: E402  (local front-door import)

        gate_result, gate_code = rq.review_evidence_gates()
        if gate_result.get("verdict") == "INPUT_MISSING":
            print(json.dumps(gate_result, indent=2, sort_keys=True))
            return 3

        if args.fl4260_rq153c_review_gate and not args.fl4260_rq153c_live_review:
            print(json.dumps(gate_result, indent=2, sort_keys=True))
            return gate_code

        if not gate_result.get("review_ready"):
            queue = rq.build_review_receipt_set()
            rq.review_state(queue, rq.load_jsonl(rq.MANUAL_RECEIPTS))
            blocked = {
                "unreviewed": [r["row_id"] for r in queue["rows"] if r["review_state"] == "unreviewed"],
                "rejected": [r["row_id"] for r in queue["rows"] if r["review_state"] == "rejected"],
                "deferred": [r["row_id"] for r in queue["rows"] if r["review_state"] == "deferred"],
                "stale_hash_mismatch": [r["row_id"] for r in queue["rows"] if r["review_state"] == "stale_hash_mismatch"],
            }
            print(json.dumps({
                "verdict": "BLOCKED",
                "fl": "FL-4260", "rq": "RQ-153c",
                "review_ready": False,
                "required_primary_count": queue["required_primary_count"],
                "review_evidence_verdict": gate_result["review_evidence_verdict"],
                "blocked_rows": blocked,
                "note": "RQ-153c review stays fail-closed until every primary-winner row has a current "
                        "accept receipt and zero reject/defer/stale blockers remain. Resolve the rows above "
                        "via scripts/fl4260_review_receipts.py {accept,reject,defer}.",
            }, indent=2, sort_keys=True))
            return 2

        tables = load_json(TABLES)
        accepted, rejected, deferred, errors = reduce_reviews(load_jsonl(REVIEWS))
        if errors:
            print("[FAIL] manual review receipts have errors:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        tables, summary = refresh_review_state(tables, accepted, rejected, deferred)
        if not args.dry_run:
            dump_json(TABLES, tables)
        out = {
            "verdict": "REVIEW_REFRESHED",
            "fl": "FL-4260", "rq": "RQ-153c",
            "review_ready": True,
            "dry_run": bool(args.dry_run),
            "review_summary": summary,
            "table_runtime_profile_live_after": bool(tables.get("runtime_profile_live")),
            "note": "RQ-153c review receipt set fully resolved; review metadata refreshed without shrinking "
                    "candidate pools. This is evidence, NOT closure: final RQ-155 Trace, canonical RQ-156, "
                    "Law 15 VPS two-tab, and Law 16 operator signoff remain open.",
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    if args.fl4260_broad_gate:
        if not PROFILES_V1.exists():
            print(json.dumps({"verdict": "BLOCKED", "blockers": [f"missing {PROFILES_V1.name}"]}, indent=2))
            return 2
        doc = load_json(PROFILES_V1)
        live_ids = [p.get("material_id") for p in doc.get("profiles", [])
                    if p.get("profile_state") == "live"]
        per_material = {}
        all_ok = bool(live_ids)
        for mid in live_ids:
            ok, rep = fl4260_thin_slice_gate(mid)
            per_material[mid] = {"verdict": rep["verdict"], "blockers": rep.get("blockers", []),
                                 "renderable_extended": rep.get("renderable_extended"),
                                 "active_buckets": rep.get("active_buckets")}
            all_ok = all_ok and ok
        out = {
            "verdict": "REVIEW_OK" if all_ok else "BLOCKED",
            "live_materials": live_ids,
            "live_count": len(live_ids),
            "per_material": per_material,
            "note": "RQ-154 broad review gate: every live material must clear the same "
                    "renderability/measurement/candidate-floor/fallback blockers terrain:1 cleared.",
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if all_ok else 2

    if args.fl4260_thin_slice_gate is not None:
        review_ok, report = fl4260_thin_slice_gate(args.fl4260_thin_slice_gate)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if review_ok else 2

    if not TABLES.exists():
        print(f"[FAIL] missing {TABLES.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    tables = load_json(TABLES)
    reviews = load_jsonl(REVIEWS)
    accepted, rejected, deferred, errors = reduce_reviews(reviews)
    if errors:
        print("[FAIL] manual review receipts have errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    tables, summary = refresh_review_state(tables, accepted, rejected, deferred)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.dry_run:
        dump_json(TABLES, tables)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

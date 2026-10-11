#!/usr/bin/env python3
"""FL-4260 RQ-153c primary-winner review receipt apparatus (single owner).

The RQ-153c mechanical coverage gate (``scripts/fl4260_rq153c_coverage_gate.py``)
proves the FULL referenced-GID set is rendered, measured, and atlas-covered. It
deliberately stops short of closure: each COMMON profile cell elects a *primary
winner* glyph, and a human must accept/reject that taste call. There are 544
common cells electing 72 DISTINCT primary winners.

The wrong frame is "ask the user to go do a vague manual pass". The correct
executor deliverable is an OWNED review surface: every one of the 72 primary
decisions is presented with full context, every accept/reject/defer action has a
defined backend effect, receipts are written with a rich auditable schema, and
review-ready status fails closed until the receipt set resolves. A human still makes the taste
call; this apparatus makes that call actionable, traceable, and mechanically
consumable. It does NOT fabricate acceptance.

This module is the SINGLE OWNER of:
  * the derivation of the 72 review receipt rows (``build_review_receipt_set``)
  * the per-row state machine over manual review receipts (``review_state``)
  * the accept / reject / defer receipt writer (rich schema v2)
  * the four RQ-153c review ``evidence_*`` gates (``review_evidence_gates``)

Both ``fl4260_rq153c_coverage_gate.py`` (operator residual observation) and
``fl4260_material_morphology_review.py`` (review status refresh) import the
derivation from here so "what are the 72 and which are accepted" has one owner
(Law 1).

Subcommands:
  list [--json] [--state STATE]   show the 72-row receipt set (optionally filtered)
  show ROW_ID [--json]            full detail for one row incl. rendered glyph
  next [--json]                   next row with no terminal (accept/reject) receipt
  accept ROW_ID --reason ...      write an accept receipt (does NOT change runtime)
  reject ROW_ID --reason ...      write a reject receipt (blocks review-ready status)
  defer  ROW_ID --reason ...      write a defer receipt (blocks review-ready status)
  gate [--json]                   the four RQ-153c review evidence_* gates

Receipts are appended to
``assets/glyphs/generated/material.morphology.v2.manual_review_receipts.jsonl``
(the same file the coverage gate observes and the review-refresh script consumes).

A review ROW_ID is ``P-<glyph_id>``: the primary winner glyph, which may win
several profile/bucket contexts (all enumerated in the row). Accepting the row
accepts that primary winner across every listed context; rejecting/deferring
blocks review-ready status until resolved.

Exit codes: 0 success / gate PASS; 2 gate FAIL; 3 input missing; 4 bad row/args.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
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

V1_FROZEN_CEILING = 671
RECEIPT_SCHEMA = "fl4260_rq153c_review_receipt.v2"
TOOL_VERSION = "fl4260_review_receipts/1.0"
RUNTIME_RESOLVER = "engine/fl4131_runtime_harri_resolver.cpp"
TERMINAL_ACTIONS = {"accept", "reject", "defer"}


# ── loaders ──────────────────────────────────────────────────────────────────

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


def _missing_inputs() -> list[str]:
    return [p.name for p in (PROFILE_TABLES, V2_ATLAS, V2_SHAPE_CATALOG, SHAPE_RECEIPTS)
            if not p.exists()]


# ── current artifact provenance (for stale-receipt fail-closed) ──────────────

def current_hashes(tables: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_table_hash": tables.get("source_catalog_hash"),
        "shape_catalog_manifest_hash": catalog.get("manifest_hash"),
        "shape_catalog_page_hash": catalog.get("page_hash"),
    }


# ── queue derivation (SINGLE OWNER of "the 72") ──────────────────────────────

def build_review_receipt_set() -> dict[str, Any]:
    """Derive the canonical RQ-153c primary-winner review receipt set.

    Returns a dict with current artifact hashes and one row per distinct COMMON
    primary winner glyph. Each row carries: assigned profiles, every bucket
    context it wins, candidate context, coverage/shape metrics, a renderable
    glyph visual (unicode char + atlas page/cell + rendered bitmap sha), and a
    runtime-decision trace pointer.
    """
    tables = load_json(PROFILE_TABLES)
    atlas = load_json(V2_ATLAS)
    catalog = load_json(V2_SHAPE_CATALOG)
    receipts = load_jsonl(SHAPE_RECEIPTS)

    cat_by_gid = {int(e["glyph_id"]): e for e in catalog.get("entries", []) if "glyph_id" in e}
    rec_by_gid = {int(r["glyph_id"]): r for r in receipts if "glyph_id" in r}
    glyph_index = atlas.get("glyph_index", {})

    # primary glyph -> aggregated context
    rows: dict[int, dict[str, Any]] = {}
    for profile, lane_table in tables.get("profiles", {}).items():
        for direction, density_cells in lane_table.items():
            for density, cell in density_cells.items():
                if not isinstance(cell, dict) or cell.get("rare_cell"):
                    continue
                prims = [int(g) for g in cell.get("primary_glyph_ids", []) if isinstance(g, int)]
                cand_ct = len(cell.get("candidate_glyph_ids", []))
                min_ct = int(cell.get("min_candidate_count", 8))
                for gid in prims:
                    row = rows.setdefault(gid, {
                        "row_id": f"P-{gid}",
                        "glyph_id": gid,
                        "profiles": set(),
                        "bucket_keys": [],
                        "roles": set(),
                        "candidate_count_min": cand_ct,
                        "min_candidate_count": min_ct,
                    })
                    row["profiles"].add(profile)
                    role = cell.get("role")
                    if role:
                        row["roles"].add(role)
                    row["bucket_keys"].append({
                        "key": f"{profile}/{direction}/{density}",
                        "profile": profile,
                        "direction": direction,
                        "density": density,
                        "role": role,
                        "candidate_count": cand_ct,
                        "min_candidate_count": min_ct,
                        "tie_rule": cell.get("tie_rule"),
                        "candidate_receipt_ids": cell.get("candidate_receipt_ids", []),
                    })
                    row["candidate_count_min"] = min(row["candidate_count_min"], cand_ct)

    out_rows: list[dict[str, Any]] = []
    for gid in sorted(rows):
        row = rows[gid]
        rec = rec_by_gid.get(gid, {})
        cat = cat_by_gid.get(gid, {})
        # rendered glyph visual (not only GlyphId): real char + atlas raster pointer
        gidx = glyph_index.get(str(gid))
        atlas_ref = None
        if isinstance(gidx, list) and len(gidx) == 5:
            # gidx = [page_ordinal, x, y, w, h]; resolve page by ordinal into pages list
            page_list = atlas.get("pages", [])
            page = page_list[gidx[0]] if 0 <= gidx[0] < len(page_list) else None
            atlas_ref = {
                "page_id": page.get("page_id") if page else None,
                "page_url": page.get("url") if page else None,
                "page_hash": page.get("page_hash") if page else None,
                "rect_xywh": gidx[1:5],
            }
        visual = {
            "glyph_char": rec.get("unicode_sequence") or cat.get("unicode"),
            "unicode_scalar": rec.get("unicode_scalar") or cat.get("unicode_scalar"),
            "label": cat.get("label"),
            "repertoire": cat.get("repertoire"),
            "stroke_class": cat.get("stroke_class") or rec.get("shape_role"),
            "visual_family": rec.get("visual_family"),
            "rendered_bitmap_sha256": rec.get("rendered_bitmap_sha256"),
            "atlas_ref": atlas_ref,
            "baseline_glyph": gid <= V1_FROZEN_CEILING,
        }
        metrics = {
            "shape6_norm": rec.get("shape6_norm") or cat.get("shape6_norm"),
            "shape6_density": rec.get("shape6_density") or cat.get("shape6_density"),
            "principal_axis_deg": rec.get("principal_axis_deg"),
            "void_count": rec.get("void_count"),
            "symmetry_class": rec.get("symmetry_class"),
        }
        trace = {
            "receipt_id": rec.get("receipt_id") or f"FL4131-M2-{gid}",
            "runtime_resolver": RUNTIME_RESOLVER,
            "tie_rule": row["bucket_keys"][0].get("tie_rule") if row["bucket_keys"] else None,
            "rejection_reason": rec.get("rejection_reason"),
            "review_state_source": rec.get("review_state"),
            "note": "RQ-155 Trace pointer: candidate->primary election path for this "
                    "glyph; full runtime decision trace refresh is the RQ-155 follow-on.",
        }
        out_rows.append({
            "row_id": row["row_id"],
            "glyph_id": gid,
            "profiles": sorted(row["profiles"]),
            "roles": sorted(row["roles"]),
            "context_count": len(row["bucket_keys"]),
            "bucket_keys": sorted(row["bucket_keys"], key=lambda b: b["key"]),
            "min_candidate_count": row["min_candidate_count"],
            "candidate_count_min_observed": row["candidate_count_min"],
            "visual": visual,
            "metrics": metrics,
            "trace": trace,
        })

    return {
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "required_primary_count": len(out_rows),
        "common_cell_count": sum(
            1
            for _p, lt in tables.get("profiles", {}).items()
            for _d, dc in lt.items()
            for _dn, c in dc.items()
            if isinstance(c, dict) and not c.get("rare_cell")
        ),
        "current_hashes": current_hashes(tables, catalog),
        "table_runtime_profile_live": bool(tables.get("runtime_profile_live", True)),
        "table_review_state": tables.get("review_state"),
        "rows": out_rows,
    }


# ── receipt state machine ────────────────────────────────────────────────────

def _latest_terminal_by_glyph(receipts: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Last-write-wins terminal (accept/reject/defer) receipt per glyph_id.

    Mirrors review-refresh semantics (later row overrides earlier) so a
    reviewer can change their mind. Non-terminal / malformed rows are ignored
    here; the review-refresh consumer is the authority on field-level validation.
    """
    latest: dict[int, dict[str, Any]] = {}
    for r in receipts:
        gid = r.get("glyph_id")
        action = r.get("action")
        if isinstance(gid, int) and action in TERMINAL_ACTIONS:
            latest[int(gid)] = r
    return latest


def review_state(queue: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Annotate each queue row with its current review state + receipt and
    return aggregate counts. State is one of:
    unreviewed | accepted | rejected | deferred | stale_hash_mismatch.

    An accept receipt whose recorded hashes no longer match the current artifact
    hashes is downgraded to ``stale_hash_mismatch`` (fail-closed: a regenerated
    catalog/table invalidates prior taste calls)."""
    latest = _latest_terminal_by_glyph(receipts)
    cur = queue["current_hashes"]
    counts = {"unreviewed": 0, "accepted": 0, "rejected": 0, "deferred": 0, "stale_hash_mismatch": 0}
    for row in queue["rows"]:
        gid = row["glyph_id"]
        rec = latest.get(gid)
        if rec is None:
            state = "unreviewed"
        else:
            action = rec.get("action")
            if action == "accept":
                if _receipt_hashes_match(rec, cur):
                    state = "accepted"
                else:
                    state = "stale_hash_mismatch"
            elif action == "reject":
                state = "rejected"
            else:
                state = "deferred"
        row["review_state"] = state
        row["review_receipt"] = rec
        counts[state] += 1
    return counts


def _receipt_hashes_match(rec: dict[str, Any], cur: dict[str, Any]) -> bool:
    """True only when the receipt recorded the current artifact hashes. A
    receipt that predates the hash convention (no recorded hashes) fails closed."""
    rh = rec.get("provenance_hashes")
    if not isinstance(rh, dict):
        return False
    for key in ("source_table_hash", "shape_catalog_manifest_hash"):
        if cur.get(key) is None or rh.get(key) != cur.get(key):
            return False
    return True


# ── evidence gates (RQ-153c review completeness) ─────────────────────────────

def review_evidence_gates() -> tuple[dict[str, Any], int]:
    missing = _missing_inputs()
    if missing:
        return {"verdict": "INPUT_MISSING", "missing": missing}, 3

    queue = build_review_receipt_set()
    receipts = load_jsonl(MANUAL_RECEIPTS)
    counts = review_state(queue, receipts)
    required = queue["required_primary_count"]

    unreviewed = [r["row_id"] for r in queue["rows"] if r["review_state"] == "unreviewed"]
    rejected = [r["row_id"] for r in queue["rows"] if r["review_state"] == "rejected"]
    deferred = [r["row_id"] for r in queue["rows"] if r["review_state"] == "deferred"]
    stale = [r["row_id"] for r in queue["rows"] if r["review_state"] == "stale_hash_mismatch"]
    accepted = [r["row_id"] for r in queue["rows"] if r["review_state"] == "accepted"]

    gates: dict[str, dict[str, Any]] = {}

    # 1. receipt-set completeness — every required primary maps to exactly one row with
    #    a resolvable glyph visual + >=1 bucket context.
    malformed = [r["row_id"] for r in queue["rows"]
                 if not r["bucket_keys"] or r["visual"].get("glyph_char") in (None, "")]
    gates["evidence_fl4260_rq153c_review_receipts_complete"] = {
        "pass": required == 72 and len(queue["rows"]) == required and not malformed,
        "required_primary_count": required,
        "rows": len(queue["rows"]),
        "malformed_rows": malformed[:16],
    }

    # 2. acceptance receipts present — every required primary is accepted.
    gates["evidence_fl4260_primary_acceptance_receipts_present"] = {
        "pass": len(accepted) == required,
        "accepted": len(accepted),
        "required": required,
        "unreviewed_rows": unreviewed[:32],
    }

    # 3. rejections fail closed — any reject/defer/stale blocks review-ready state.
    blockers = rejected + deferred + stale
    review_consistent = not (queue.get("table_review_state") == "all_cells_review_accepted" and blockers)
    gates["evidence_fl4260_primary_rejections_fail_closed"] = {
        "pass": review_consistent,
        "blocker_count": len(blockers),
        "rejected_rows": rejected[:16],
        "deferred_rows": deferred[:16],
        "table_review_state": queue.get("table_review_state"),
        "note": "fail-closed: all-cells review-ready state requires zero reject/defer/stale blockers.",
    }

    # 4. receipts match current hashes — no accept receipt may be stale.
    gates["evidence_fl4260_receipts_match_current_hashes"] = {
        "pass": len(stale) == 0,
        "stale_count": len(stale),
        "stale_rows": stale[:16],
        "current_hashes": queue["current_hashes"],
    }

    review_complete = all(g["pass"] for g in gates.values())
    review_ready = (
        review_complete
        and len(accepted) == required
        and not blockers
    )
    result = {
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "review_evidence_verdict": "PASS" if review_complete else "FAIL",
        "review_ready": review_ready,
        "closure": False,
        "counts": counts,
        "required_primary_count": required,
        "gates": gates,
        "note": "Review-evidence PASS means the 72-row receipt set is structurally complete, "
                "every primary has a current accept receipt, no reject/defer/stale blocker "
                "remains, and review state is consistent. review_ready is the precondition "
                "for refreshing review metadata without changing candidate pools. "
                "It is NOT closure: final RQ-155 Trace, canonical RQ-156, Law 15 VPS two-tab, "
                "and Law 16 operator signoff remain open.",
    }
    return result, (0 if review_complete else 2)


# ── receipt writer ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_receipt(row_id: str, action: str, reason: str, reviewer: str,
                  screenshot_path: str, capture_id: str, termpp_context: str) -> dict[str, Any]:
    if action not in TERMINAL_ACTIONS:
        raise ValueError(f"action must be one of {sorted(TERMINAL_ACTIONS)}, got {action!r}")
    if not reason or not reason.strip():
        raise ValueError("a non-empty --reason is required for every review action")

    queue = build_review_receipt_set()
    row = next((r for r in queue["rows"] if r["row_id"] == row_id), None)
    if row is None:
        raise KeyError(row_id)

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "fl": "FL-4260",
        "rq": "RQ-153c",
        "row_id": row_id,
        "glyph_id": row["glyph_id"],
        "action": action,
        "assigned_profiles": row["profiles"],
        "bucket_keys": [b["key"] for b in row["bucket_keys"]],
        "covers_contexts": row["context_count"],
        "primary_glyph_id": row["glyph_id"],
        "glyph_char": row["visual"].get("glyph_char"),
        "unicode_scalar": row["visual"].get("unicode_scalar"),
        "rendered_bitmap_sha256": row["visual"].get("rendered_bitmap_sha256"),
        "provenance_hashes": queue["current_hashes"],
        "screenshot_path": screenshot_path or "",
        "capture_id": capture_id or "",
        "termpp_context": termpp_context or "",
        "reviewer": reviewer or "operator",
        "reason": reason.strip(),
        "reviewer_timestamp": _now_iso(),
        "tool_version": TOOL_VERSION,
    }
    with MANUAL_RECEIPTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return receipt


# ── rendering helpers ────────────────────────────────────────────────────────

def _fmt_row_line(row: dict[str, Any]) -> str:
    v = row["visual"]
    ch = v.get("glyph_char") or "?"
    state = row.get("review_state", "?")
    profs = ",".join(row["profiles"])
    return (f"  [{state:>17}] {row['row_id']:>7}  glyph={row['glyph_id']:>4} '{ch}' "
            f"ctx={row['context_count']:>2}  {profs}")


def cmd_list(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    counts = review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    rows = queue["rows"]
    if args.state:
        rows = [r for r in rows if r["review_state"] == args.state]
    if args.json:
        print(json.dumps({"counts": counts, "required_primary_count": queue["required_primary_count"],
                          "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"FL-4260 RQ-153c primary-winner review receipts  ({queue['required_primary_count']} required primaries)")
    print(f"  table runtime_profile_live={queue['table_runtime_profile_live']}  "
          f"review_state={queue['table_review_state']}")
    print(f"  state counts: {counts}")
    for r in rows:
        print(_fmt_row_line(r))
    return 0


def cmd_show(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    row = next((r for r in queue["rows"] if r["row_id"] == args.row_id), None)
    if row is None:
        print(f"no such review receipt row: {args.row_id}", file=sys.stderr)
        return 4
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    v, m, t = row["visual"], row["metrics"], row["trace"]
    print(f"FL-4260 RQ-153c review receipt row {row['row_id']}  (state: {row['review_state']})")
    print(f"  primary glyph_id : {row['glyph_id']}")
    print(f"  rendered glyph   : '{v.get('glyph_char')}'  U+{(v.get('unicode_scalar') or 0):04X}  "
          f"label={v.get('label')}  repertoire={v.get('repertoire')}")
    print(f"  baseline(<=671)  : {v.get('baseline_glyph')}   stroke_class={v.get('stroke_class')}  "
          f"visual_family={v.get('visual_family')}")
    print(f"  rendered bmp sha : {v.get('rendered_bitmap_sha256')}")
    if v.get("atlas_ref"):
        a = v["atlas_ref"]
        print(f"  atlas raster     : page={a.get('page_id')} rect_xywh={a.get('rect_xywh')} url={a.get('page_url')}")
    print(f"  shape6_norm      : {m.get('shape6_norm')}")
    print(f"  profiles         : {', '.join(row['profiles'])}   roles: {', '.join(row['roles'])}")
    print(f"  wins {row['context_count']} bucket contexts (min candidate floor {row['min_candidate_count']}):")
    for b in row["bucket_keys"]:
        print(f"      {b['key']:<18} role={b.get('role')} candidates={b.get('candidate_count')} tie={b.get('tie_rule')}")
    print(f"  trace            : receipt_id={t.get('receipt_id')} resolver={t.get('runtime_resolver')} "
          f"tie_rule={t.get('tie_rule')}")
    if row.get("review_receipt"):
        rr = row["review_receipt"]
        print(f"  current receipt  : action={rr.get('action')} reviewer={rr.get('reviewer')} "
              f"at={rr.get('reviewer_timestamp')} reason={rr.get('reason')!r}")
    return 0


def cmd_next(args) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    queue = build_review_receipt_set()
    review_state(queue, load_jsonl(MANUAL_RECEIPTS))
    nxt = next((r for r in queue["rows"] if r["review_state"] in ("unreviewed", "stale_hash_mismatch")), None)
    if nxt is None:
        print("no unreviewed rows remain (all 72 primaries have a terminal receipt).")
        return 0
    if args.json:
        print(json.dumps(nxt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(_fmt_row_line(nxt).strip())
    print(f"  -> review with: fl4260_review_receipts.py show {nxt['row_id']}")
    return 0


def cmd_action(args, action: str) -> int:
    missing = _missing_inputs()
    if missing:
        print(json.dumps({"verdict": "INPUT_MISSING", "missing": missing}))
        return 3
    try:
        receipt = write_receipt(
            args.row_id, action, args.reason, args.reviewer,
            args.screenshot, args.capture_id, args.termpp_context,
        )
    except KeyError:
        print(f"no such review receipt row: {args.row_id}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    print(f"wrote {action} receipt for {receipt['row_id']} (glyph {receipt['glyph_id']} "
          f"'{receipt['glyph_char']}', covers {receipt['covers_contexts']} contexts)")
    print("  NOTE: this receipt does not change runtime. Run "
          "fl4260_material_morphology_review.py --fl4260-rq153c-live-review to refresh review metadata "
          "(fails closed until all 72 primaries are accepted with zero reject/defer/stale).")
    return 0


def cmd_gate(args) -> int:
    result, code = review_evidence_gates()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    if result.get("verdict") == "INPUT_MISSING":
        print("RQ-153c review evidence gate: INPUT MISSING")
        for m in result["missing"]:
            print(f"  - missing {m}")
        return code
    print("FL-4260 RQ-153c review evidence gates")
    print(f"  review_evidence_verdict: {result['review_evidence_verdict']}   "
          f"review_ready: {result['review_ready']}   closure: {result['closure']}")
    print(f"  counts: {result['counts']}")
    for name, g in result["gates"].items():
        label = "PASS" if g["pass"] else "FAIL"
        print(f"    [{label}] {name}")
    print(f"  {result['note']}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show the review receipt set")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--state", choices=["unreviewed", "accepted", "rejected", "deferred", "stale_hash_mismatch"])
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="full detail for one row")
    p_show.add_argument("row_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_next = sub.add_parser("next", help="next unreviewed row")
    p_next.add_argument("--json", action="store_true")
    p_next.set_defaults(func=cmd_next)

    for act in ("accept", "reject", "defer"):
        pa = sub.add_parser(act, help=f"write a {act} receipt")
        pa.add_argument("row_id")
        pa.add_argument("--reason", required=True, help="non-empty justification (required)")
        pa.add_argument("--reviewer", default="operator")
        pa.add_argument("--screenshot", default="", help="headed-review screenshot path")
        pa.add_argument("--capture-id", default="", help="capture id when mounted")
        pa.add_argument("--termpp-context", default="", help="TERM++ runtime context if mounted")
        pa.set_defaults(func=lambda a, _act=act: cmd_action(a, _act))

    p_gate = sub.add_parser("gate", help="RQ-153c review evidence gates")
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

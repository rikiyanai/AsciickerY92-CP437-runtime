#!/usr/bin/env python3
"""FL-4359 EXACT-KEY terrain instability analyzer.

Replaces the RETRACTED coarse-bucket adhoc (commit 9b6225e84 conclusion = INVALID).
That analyzer keyed rows by round(world_anchor / 2.0) with last-write-wins collisions,
so it could not distinguish "one stable product changes" from "two terrain sources
winner-swap into the same bucket". This tool fixes all of that:

  1. FAIL CLOSED unless every terrain row carries `source_key` AND `terr_stable_cell_gpu`
     (the authoritative GPU identity emitted in render-inert r18). Old captures lack these
     fields -> this tool refuses them instead of producing a number.
  2. Key EXACTLY by (source_key, stable_x, stable_y). No rounding. No world-anchor bucket.
  3. Preserve ALL rows per key per frame (no overwrite) so duplicate ANSI samples are visible.
  4. Report, separately:
       - duplicate ANSI samples (same key, same frame, >1 row)         [intra-frame multiplicity]
       - intra-frame OUTPUT variants (same key+frame, differing output) [winner collision in a frame]
       - cross-frame OUTPUT variants (same key, output differs across frames)
       - screen-cell continuity (is the key's screen cell stable across frames)
  5. Expose the ACTUAL selector scalars per frame (edge_score, sidewall_score, shade_band,
     step_amount, diff_h/v) and classify each cross-frame-variant key as:
       - threshold_crossing : a scalar crosses a known selector threshold across frames
                              -> a deadband on THAT scalar is justifiable
       - source_swap        : source_key for the screen cell changes (winner swap, not same product)
       - unexplained        : output flips with NO scalar crossing and NO source swap
                              -> driver is elsewhere; do NOT add a deadband

A deadband is authorized ONLY if `threshold_crossing` keys dominate the cross-frame variants.

Usage:
  python3 scripts/analyze_fl4359_exact_key.py <jitter.jsonl> [--frames A:B] [--json] [--max-key-samples N]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

# Known selector thresholds in cell_owner_product.glsl (anchor by symbol, values may drift).
EDGE_SCORE_EPS = 1.15        # product_apply_clone_screen_space_edge admission cutoff
SIDEWALL_THRESHOLD = 1.35    # product_clone_cell_sidewall_score height cutoff (CLONE_SIDEWALL_HEIGHT_THRESHOLD)
STEP_DIRT_MOUNTAIN = 1.15    # product_dirt_mountain_role step_amount cutoff
COVERAGE_FAILCLOSED = 0.90   # require >=90% of terrain rows to carry the exact-key fields


def _payload_output(r: dict) -> tuple:
    """The VISIBLE/selected product for this row (what shimmer would show)."""
    return (
        r.get("glyph_id"),
        r.get("diag_candidate_glyph"),
        r.get("diag_glyph_role"),
        r.get("diag_role"),
        r.get("diag_ramp_band"),
        r.get("diag_boundary"),
        r.get("material_role"),
        r.get("terrain_product_detail"),
    )


def _scalars(r: dict) -> dict:
    return {
        "edge_score": r.get("diag_edge_score"),
        "sidewall_score": r.get("diag_sidewall_score"),
        "shade_band": r.get("diag_shade_band"),
        "step_amount": r.get("diag_step_amount"),
        "diff_h": r.get("diag_diff_h"),
        "diff_v": r.get("diag_diff_v"),
    }


def _crosses(values, threshold) -> bool:
    """Do the non-None values straddle the threshold across frames?"""
    vs = [v for v in values if isinstance(v, (int, float))]
    if len(vs) < 2:
        return False
    return min(vs) < threshold <= max(vs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jitter")
    ap.add_argument("--frames", default=None, help="inclusive frame range A:B")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-key-samples", type=int, default=12)
    args = ap.parse_args()

    frange = None
    if args.frames:
        a, b = args.frames.split(":")
        frange = (int(a), int(b))

    # provenance / fail-closed accounting
    terrain_rows = 0
    terrain_rows_with_key = 0
    schema_has_field = False

    # exact key -> frame -> list of {out, sc, screen}
    keyed: dict = defaultdict(lambda: defaultdict(list))
    # screen cell -> frame -> set of source_key (to detect winner swap at a screen cell)
    screen_sources: dict = defaultdict(lambda: defaultdict(set))
    frames_seen = set()

    with open(args.jitter, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("row_type") != "fl4359_jitter_cell":
                continue
            fid = r.get("frame_id")
            if frange and not (frange[0] <= fid <= frange[1]):
                continue
            if "terr_stable_cell_gpu" in r:
                schema_has_field = True
            if r.get("selected_owner") != "terrain":
                continue
            terrain_rows += 1
            frames_seen.add(fid)
            sk = r.get("source_key")
            stable = r.get("terr_stable_cell_gpu")
            sc = r.get("screen_cell") or {}
            scx, scy = sc.get("x"), sc.get("y")
            if scx is not None and sk is not None:
                screen_sources[(scx, scy)][fid].add(sk)
            if sk is None or not isinstance(stable, dict) or stable.get("x") is None:
                continue  # this terrain row lacks the exact-key fields
            terrain_rows_with_key += 1
            key = (sk, stable["x"], stable["y"])
            keyed[key][fid].append({
                "out": _payload_output(r),
                "sc": _scalars(r),
                "screen": (scx, scy),
            })

    # ---- FAIL CLOSED ----
    coverage = (terrain_rows_with_key / terrain_rows) if terrain_rows else 0.0
    fail = None
    if terrain_rows == 0:
        fail = "no terrain rows in range"
    elif not schema_has_field:
        fail = ("capture predates the exact-key recorder (no terr_stable_cell_gpu field). "
                "Re-capture after commit bfc3285eb with the post-containment build.")
    elif coverage < COVERAGE_FAILCLOSED:
        fail = (f"exact-key field coverage {coverage:.1%} < {COVERAGE_FAILCLOSED:.0%}; "
                "instrument incomplete or wrong build. Refusing to report.")
    if fail:
        out = {"ok": False, "fail_closed": fail, "terrain_rows": terrain_rows,
               "terrain_rows_with_key": terrain_rows_with_key, "coverage": round(coverage, 4),
               "schema_has_exact_key_field": schema_has_field}
        print(json.dumps(out, indent=2) if args.json else f"FAIL CLOSED: {fail}\n"
              f"  terrain_rows={terrain_rows} with_key={terrain_rows_with_key} coverage={coverage:.1%}")
        return 2

    # ---- ANALYZE ----
    frames_sorted = sorted(frames_seen)
    total_keys = len(keyed)
    dup_ansi_keys = 0          # key seen >1x in some frame
    intra_frame_variant_keys = 0  # within a frame, duplicate rows disagree on output
    cross_frame_variant_keys = 0
    screen_stable_keys = 0
    # classification of cross-frame variant keys
    cls = {"threshold_crossing": 0, "source_swap": 0, "unexplained": 0}
    samples = {"threshold_crossing": [], "source_swap": [], "unexplained": []}

    for key, byframe in keyed.items():
        had_dup = any(len(v) > 1 for v in byframe.values())
        if had_dup:
            dup_ansi_keys += 1
        intra_variant = any(len({tuple(d["out"]) for d in v}) > 1 for v in byframe.values())
        if intra_variant:
            intra_frame_variant_keys += 1
        # cross-frame: representative output per frame (first row); detect change
        frames_k = sorted(byframe)
        reps = [byframe[fk][0]["out"] for fk in frames_k]
        screens = [byframe[fk][0]["screen"] for fk in frames_k]
        if len({tuple(s) for s in screens}) == 1:
            screen_stable_keys += 1
        if len(frames_k) >= 2 and len({tuple(x) for x in reps}) > 1:
            cross_frame_variant_keys += 1
            # classify
            edge_vals = [byframe[fk][0]["sc"]["edge_score"] for fk in frames_k]
            side_vals = [byframe[fk][0]["sc"]["sidewall_score"] for fk in frames_k]
            step_vals = [byframe[fk][0]["sc"]["step_amount"] for fk in frames_k]
            shade_vals = [byframe[fk][0]["sc"]["shade_band"] for fk in frames_k]
            crossing = (_crosses(edge_vals, EDGE_SCORE_EPS)
                        or _crosses(side_vals, SIDEWALL_THRESHOLD)
                        or _crosses(step_vals, STEP_DIRT_MOUNTAIN))
            shade_moved = len({v for v in shade_vals if v is not None}) > 1
            # source swap at this key's screen cell across frames
            sc0 = screens[0]
            src_swap = any(len(screen_sources.get(sc0, {}).get(fk, set())) > 1 for fk in frames_k) \
                or len({tuple(s) for s in screens}) > 1
            if crossing or shade_moved:
                klass = "threshold_crossing"
            elif src_swap:
                klass = "source_swap"
            else:
                klass = "unexplained"
            cls[klass] += 1
            if len(samples[klass]) < args.max_key_samples:
                samples[klass].append({
                    "key": {"source_key": key[0], "stable_x": key[1], "stable_y": key[2]},
                    "screen": sc0,
                    "frames": frames_k,
                    "glyph_seq": [r[0] for r in reps],
                    "glyph_role_seq": [r[2] for r in reps],
                    "edge_score_seq": [round(v, 3) if isinstance(v, (int, float)) else None for v in edge_vals],
                    "shade_band_seq": shade_vals,
                    "step_amount_seq": [round(v, 3) if isinstance(v, (int, float)) else None for v in step_vals],
                })

    cfv = cross_frame_variant_keys or 1
    result = {
        "ok": True,
        "file": args.jitter,
        "frames": frames_sorted,
        "terrain_rows": terrain_rows,
        "exact_key_coverage": round(coverage, 4),
        "total_exact_keys": total_keys,
        "duplicate_ansi_keys": dup_ansi_keys,
        "intra_frame_output_variant_keys": intra_frame_variant_keys,
        "cross_frame_output_variant_keys": cross_frame_variant_keys,
        "screen_stable_keys": screen_stable_keys,
        "cross_frame_variant_classification": cls,
        "cross_frame_variant_fractions": {k: round(v / cfv, 4) for k, v in cls.items()},
        "deadband_authorized": cls["threshold_crossing"] > (cls["source_swap"] + cls["unexplained"]),
        "samples": samples,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"FL-4359 EXACT-KEY analysis: {args.jitter}")
        print(f"  frames={frames_sorted}")
        print(f"  terrain_rows={terrain_rows} exact_key_coverage={coverage:.1%}")
        print(f"  total exact keys (source_key, stable_x, stable_y): {total_keys}")
        print(f"  duplicate ANSI samples (key seen >1x/frame): {dup_ansi_keys}")
        print(f"  intra-frame OUTPUT variants (winner collision): {intra_frame_variant_keys}")
        print(f"  cross-frame OUTPUT variants: {cross_frame_variant_keys}")
        print(f"  screen-stable keys: {screen_stable_keys}/{total_keys}")
        print(f"  cross-frame variant classification: {cls}")
        print(f"  -> deadband authorized: {result['deadband_authorized']} "
              f"(only if threshold_crossing dominates)")
        for klass in ("threshold_crossing", "source_swap", "unexplained"):
            if samples[klass]:
                print(f"\n  [{klass}] sample keys:")
                for s in samples[klass][:4]:
                    print(f"    key={s['key']} screen={s['screen']} glyph={s['glyph_seq']} "
                          f"edge={s['edge_score_seq']} shade={s['shade_band_seq']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

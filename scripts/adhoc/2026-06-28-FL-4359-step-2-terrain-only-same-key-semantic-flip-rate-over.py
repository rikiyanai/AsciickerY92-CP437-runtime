# Ad hoc script: FL-4359 step-2: terrain-only same-key semantic flip rate over airplane-flight OFF jitter — proves whether a stable terrain world-cell flips glyph/material/detail frame-to-frame (hysteresis warranted) or is pure resample/snap (item 5 wrong frame)
# Created: 2026-06-28
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Stream a huge fl4359 jitter.jsonl, frame-by-frame, and report the TERRAIN-only
same-key semantic flip rate keyed by world anchor bucket. Decision gate for FL-4359
item 5 (terrain semantic-history hysteresis)."""
import json, sys, math
PATH = sys.argv[1] if len(sys.argv) > 1 else None
BUCKET = 2.0  # world units, matches ADR world-anchor matching scale

def bkey(ax, az):
    return (round(ax / BUCKET), round(az / BUCKET))

prev = {}            # anchor -> (owner,glyph,role,detail)
cur = {}
cur_fid = None
comparable = 0
swaps = 0
swap_glyph = swaps_role = swaps_detail = 0
# correlate swaps with snap error magnitude
snap_err_swap = []
snap_err_stable = []
frames_seen = 0

def flush(prev, cur):
    global comparable, swaps, swap_glyph, swaps_role, swaps_detail
    for k, (pa, sea) in cur.items():
        if k not in prev:
            continue
        pb, seb = prev[k]
        comparable += 1
        if pa != pb:
            swaps += 1
            if pa[1] != pb[1]: swap_glyph += 1
            if pa[2] != pb[2]: swaps_role += 1
            if pa[3] != pb[3]: swaps_detail += 1
            snap_err_swap.append(sea)
        else:
            snap_err_stable.append(sea)

with open(PATH, encoding="utf-8") as f:
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
        if fid != cur_fid:
            # frame boundary
            if cur_fid is not None:
                flush(prev, cur)
                prev = cur
                frames_seen += 1
            cur = {}
            cur_fid = fid
        if r.get("selected_owner") != "TERRAIN":
            continue
        anc = r.get("ansi_cell_world_anchor") or {}
        try:
            ax = anc["x"]; az = anc["z"]
        except (KeyError, TypeError):
            continue
        if not (math.isfinite(ax) and math.isfinite(az)):
            continue
        sep = r.get("camera_snap_error_px") or {}
        se = math.hypot(float(sep.get("x", 0.0) or 0.0), float(sep.get("y", 0.0) or 0.0))
        payload = (r.get("selected_owner"), r.get("glyph_id"),
                   r.get("material_role"), r.get("terrain_product_detail"))
        cur[bkey(ax, az)] = (payload, se)
    if cur_fid is not None:
        flush(prev, cur)
        frames_seen += 1

def avg(xs):
    return sum(xs)/len(xs) if xs else None

print(json.dumps({
    "file": PATH,
    "frames_seen": frames_seen,
    "comparable_terrain_pairs": comparable,
    "terrain_same_key_swaps": swaps,
    "terrain_swap_rate": (swaps/comparable) if comparable else None,
    "swap_breakdown": {"glyph": swap_glyph, "material_role": swaps_role, "detail": swaps_detail},
    "avg_snap_err_px_on_swap": avg(snap_err_swap),
    "avg_snap_err_px_on_stable": avg(snap_err_stable),
}, indent=2))

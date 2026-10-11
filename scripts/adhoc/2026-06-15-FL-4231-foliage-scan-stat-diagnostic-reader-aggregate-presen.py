# Ad hoc script: FL-4231 foliage scan-stat diagnostic reader: aggregate present/footprint counts + terr_cell range for foliage_class=1 cells
# Created: 2026-06-15
# Canonical gap: <describe what tool should own this>

import json, sys, collections
path = "/Users/r/Downloads/asciicker-Y9-2/.run/final_render_cell_dump/1781567679_12332/cells.jsonl"
n_fol = 0
n_present = 0
combos = collections.Counter()
present_counts = collections.Counter()
footprint_counts = collections.Counter()
txmin=txmax=tymin=tymax=None
minrx = collections.Counter()
minry = collections.Counter()
glyph_ids = collections.Counter()
sample = []
with open(path) as f:
    for line in f:
        line=line.strip()
        if not line: continue
        try: c=json.loads(line)
        except: continue
        if c.get("cell_owner_foliage_class") != 1: continue
        n_fol += 1
        present = c.get("cell_owner_foliage_pa_present")
        rr = c.get("cell_owner_foliage_pa_reject_reason")
        pc = c.get("cell_owner_foliage_pa_blade_glyph")   # diag_present_count when absent
        fc = c.get("cell_owner_foliage_pa_anchor_stem_h") # diag_footprint_count when absent
        tx = c.get("cell_owner_foliage_pa_effective_y")   # terr_cell.x when absent
        ty = c.get("cell_owner_foliage_pa_coverage_f")    # terr_cell.y when absent
        rx = c.get("cell_owner_foliage_pa_wind_sample")   # min_abs_rx when absent
        ry = c.get("cell_owner_foliage_pa_wind_dir")      # min_ry when absent
        gid = c.get("cell_owner_glyph_id")
        glyph_ids[gid]+=1
        if present:
            n_present += 1
            continue
        combos[(rr,)]+=1
        present_counts[pc]+=1
        footprint_counts[fc]+=1
        try:
            txi=int(round(tx)); tyi=int(round(ty))
            txmin = txi if txmin is None else min(txmin,txi)
            txmax = txi if txmax is None else max(txmax,txi)
            tymin = tyi if tymin is None else min(tymin,tyi)
            tymax = tymax if tymax is not None else tyi
            tymax = max(tymax,tyi)
        except: pass
        minrx[round(rx)]+=1
        minry[round(ry)]+=1
        if len(sample)<8:
            sample.append((tx,ty,pc,fc,rx,ry))
print("foliage_class=1 cells:", n_fol)
print("pa_present True:", n_present, " False:", n_fol-n_present)
print("reject_reason dist:", dict(combos))
print("diag_present_count dist (anchors present in 3x3):", dict(present_counts))
print("diag_footprint_count dist (footprint-valid):", dict(footprint_counts))
print("terr_cell.x range:", txmin, "..", txmax)
print("terr_cell.y range:", tymin, "..", tymax)
print("min_abs_stamp_rx dist:", dict(minrx))
print("min_stamp_ry dist:", dict(minry))
print("glyph_id dist (top):", glyph_ids.most_common(8))
print("samples (tx,ty,present_cnt,footprint_cnt,min_rx,min_ry):")
for s in sample: print("  ", s)

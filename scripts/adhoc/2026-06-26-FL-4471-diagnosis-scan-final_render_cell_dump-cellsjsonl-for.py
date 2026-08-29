# Ad hoc script: FL-4471 diagnosis: scan final_render_cell_dump cells.jsonl for green-vs-% terrain cells, histogram material_id->material_family->glyph_char, and find color/family disagreements
# Created: 2026-06-26
# Canonical gap: <describe what tool should own this>

import json, sys, collections
path = sys.argv[1] if len(sys.argv) > 1 else ".run/final_render_cell_dump/1782375213_154271/cells.jsonl"
mat_fam = collections.Counter()          # (material_id, material_family) -> count
fam_glyph = collections.Counter()        # (family, glyph_char) -> count
green_fam_glyph = collections.Counter()  # green cells: (family, glyph_char)
pct_cells = collections.Counter()        # '%' cells: (material_id, family, color_bucket)
green_total = 0; terrain_total = 0
def colorbucket(r,g,b):
    if g > r+6 and g > b+6: return "GREEN"
    if abs(r-g)<12 and abs(g-b)<12: return "GREY"
    if r>g and r>b: return "RED/BROWN"
    if b>r and b>g: return "BLUE"
    return "OTHER"
with open(path) as f:
    for line in f:
        try: r = json.loads(line)
        except: continue
        if r.get("cell_owner_kind") != "terrain": continue
        terrain_total += 1
        mid = r.get("material_id"); fam = r.get("material_family")
        gch = r.get("glyph_char"); gid = r.get("glyph_id")
        avg = r.get("avg_rgb") or {}
        cr,cg,cb = avg.get("r",0),avg.get("g",0),avg.get("b",0)
        cb_name = colorbucket(cr,cg,cb)
        mat_fam[(mid,fam)] += 1
        fam_glyph[(fam,gch)] += 1
        if cb_name == "GREEN":
            green_total += 1
            green_fam_glyph[(fam,gch)] += 1
        if gid == 37:  # '%'
            pct_cells[(mid,fam,cb_name)] += 1
print("=== terrain_total=%d  green_total=%d ===" % (terrain_total, green_total))
print("\n=== material_id -> material_family histogram ===")
for k,v in sorted(mat_fam.items(), key=lambda x:-x[1]):
    print("  mat=%s fam=%s : %d" % (k[0],k[1],v))
print("\n=== GREEN cells: (family, glyph) ===")
for k,v in sorted(green_fam_glyph.items(), key=lambda x:-x[1])[:20]:
    print("  fam=%s glyph=%r : %d" % (k[0],k[1],v))
print("\n=== '%%' (glyph 37) cells: (material_id, family, color) ===")
if not pct_cells: print("  NONE — no '%' glyph in this dump")
for k,v in sorted(pct_cells.items(), key=lambda x:-x[1]):
    print("  mat=%s fam=%s color=%s : %d" % (k[0],k[1],k[2],v))
print("\n=== all glyphs seen on GREEN cells (char:count) ===")
gc = collections.Counter()
for (fam,gch),v in green_fam_glyph.items(): gc[gch]+=v
print("  " + "  ".join("%r:%d"%(c,n) for c,n in sorted(gc.items(),key=lambda x:-x[1])[:20]))

# Ad hoc script: FL-4231 C1-C3 proof: scan final_render_cell_dump cells.jsonl for populated typed SECONDARY product facts (r18-r21). Counts cells by sec_blend_role + sec_kind, prints exemplars with full sec_* fields, proves beam/firefly/vapor secondary facts are produced and fail-closed elsewhere.
# Created: 2026-06-16
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4231 secondary-fact dump analyzer.

Usage: python3 scripts/adhoc/<this>.py <cells.jsonl> [--examples N]

Streams a final_render_cell_dump cells.jsonl and reports, per the base-plus-secondary
architecture, how many cells carry a populated typed secondary effect fact (r18-r21):
  - blend role distribution (0=NONE,1=ADDITIVE,2=ALPHA,3=GLYPH)
  - sec_kind distribution (beam/particle/vapor/...)
  - exemplar cells with full sec_* payload (kind/source/depth/coverage/alpha/glyph/fg/intensity/world)
Fail-closed expectation: the vast majority of cells have blend_role==0 / kind=="none".
"""
import json, sys, collections

path = sys.argv[1]
n_examples = 5
if "--examples" in sys.argv:
    n_examples = int(sys.argv[sys.argv.index("--examples") + 1])

total = 0
populated = 0
blend = collections.Counter()
kinds = collections.Counter()
examples = []
SECF = ["cell_owner_sec_kind","cell_owner_sec_source_id","cell_owner_sec_reject_bits",
        "cell_owner_sec_glyph","cell_owner_sec_depth","cell_owner_sec_coverage",
        "cell_owner_sec_alpha","cell_owner_sec_blend_role","cell_owner_sec_fg_rgb",
        "cell_owner_sec_intensity","cell_owner_sec_world"]

with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except Exception:
            continue
        total += 1
        role = c.get("cell_owner_sec_blend_role", 0)
        kind = c.get("cell_owner_sec_kind", "none")
        blend[role] += 1
        if role and role != 0:
            populated += 1
            kinds[kind] += 1
            if len(examples) < n_examples:
                ax = c.get("ansi_x", c.get("x", "?"))
                ay = c.get("ansi_y", c.get("y", "?"))
                base = c.get("cell_owner_kind", c.get("owner_kind", "?"))
                examples.append((ax, ay, base, {k: c.get(k) for k in SECF}))

print(f"cells_total={total}")
print(f"cells_with_populated_secondary_fact={populated}")
print(f"blend_role_distribution={dict(blend)}")
print(f"populated_sec_kind_distribution={dict(kinds)}")
print("--- exemplars (populated secondary cells) ---")
for ax, ay, base, sec in examples:
    print(f"cell({ax},{ay}) base_owner={base}")
    for k in SECF:
        print(f"    {k} = {sec.get(k)}")

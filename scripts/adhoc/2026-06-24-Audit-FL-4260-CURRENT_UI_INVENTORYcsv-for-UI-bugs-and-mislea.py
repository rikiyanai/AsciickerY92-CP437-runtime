# Ad hoc script: Audit FL-4260 CURRENT_UI_INVENTORY.csv for UI bugs and misleading labels: distributions of pipeline_status/termpp_verdict/expectation_status, visibility failures, jargon labels, and Material Look(RENDERING) tab control health
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import csv, os, re
from collections import Counter
CSV=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..',
    'docs/research/ascii/verification/fl4260/CURRENT_UI_INVENTORY.csv')
rows=list(csv.DictReader(open(os.path.abspath(CSV))))
print(f"TOTAL controls: {len(rows)}\n")
def dist(col):
    c=Counter((r.get(col) or '').strip() or '(empty)' for r in rows)
    return c
for col in ['pipeline_status','termpp_verdict','expectation_status']:
    print(f"== {col} ==")
    for k,n in dist(col).most_common():
        print(f"   {n:4d}  {k[:80]}")
    print()
# visibility failures (non-empty)
visf=[r for r in rows if (r.get('visibility_failure') or '').strip()]
print(f"== controls with visibility_failure: {len(visf)} ==")
vc=Counter(re.split(r'[:;]',(r['visibility_failure']))[0].strip() for r in visf)
for k,n in vc.most_common(8): print(f"   {n:4d}  {k[:70]}")
print()
# tab distribution
print("== controls per tab ==")
for k,n in dist('tab').most_common(): print(f"   {n:4d}  {k}")
print()
# jargon the operator demanded removed
jargon=['profile mode','starter','role bucket','winner scoring','mode & status','mode and status','active material']
print("== JARGON / misleading labels still present (visible_label or section) ==")
for r in rows:
    lab=(r.get('visible_label') or '')+' | '+(r.get('section') or '')+' | '+(r.get('container') or '')
    low=lab.lower()
    for j in jargon:
        if j in low:
            print(f"   row {r['inventory_row']:>4} [{r['tab']}] '{r.get('visible_label')}'  <= matches '{j}'")
            break
print()
# Material Look / RENDERING tab controls and their health
ml=[r for r in rows if 'RENDER' in (r.get('tab') or '').upper() or 'material look' in (r.get('container') or '').lower() or 'rendering' in (r.get('container') or '').lower()]
print(f"== Material Look / RENDERING controls: {len(ml)} ==")
mlbad=[r for r in ml if (r.get('pipeline_status') or '')!='WIRED' and 'PROVEN' not in (r.get('expectation_status') or '').upper()]
print(f"   of those, not WIRED/PROVEN: {len(mlbad)}")
for r in ml[:0]: pass

# Ad hoc script: Union-append FL-3838 to FL-4254 ComplaintRefs and fill Area for FL-4253/FL-4254
# Created: 2026-06-08
# Canonical gap: <describe what tool should own this>

"""
Fix FL-4253 and FL-4254 overlay rows:
  - Add Area='pipeline' to both (was empty)
  - Union-append FL-3838 to FL-4254 ComplaintRefs (missed in original fl add call)
"""
import json, sys
from pathlib import Path

root = Path('.')
sys.path.insert(0, str(root / 'scripts'))

from analyze_runs import canonical_failure_log_path
from scripts.maintainer.lib.fl_overlay import read_overlay

def write_overlay_row(root, row):
    fl_path = canonical_failure_log_path(root)
    content = fl_path.read_text(encoding='utf-8')
    import re
    pattern = re.compile(r'(## FL Metadata Overlay.*?```jsonl\n)(.*?)(```)', re.DOTALL)
    m = pattern.search(content)
    if not m:
        print("ERROR: no overlay block found")
        sys.exit(1)
    existing = m.group(2)
    new_line = json.dumps(row, separators=(',', ':'))
    new_content = content[:m.start(2)] + existing + new_line + '\n' + content[m.end(2):]
    fl_path.write_text(new_content, encoding='utf-8')
    print(f"Appended overlay row for {row['fl']}")

rows = read_overlay()
by_id = {r['fl']: r for r in rows}

fl4253 = dict(by_id.get('FL-4253', {'fl': 'FL-4253'}))
fl4254 = dict(by_id.get('FL-4254', {'fl': 'FL-4254'}))

# Fix Area for both
fl4253['Area'] = 'pipeline'
fl4254['Area'] = 'pipeline'

# Union-append FL-3838 to FL-4254 ComplaintRefs
refs4254 = list(fl4254.get('ComplaintRefs', []))
if 'FL-3838' not in refs4254:
    refs4254.append('FL-3838')
fl4254['ComplaintRefs'] = refs4254

write_overlay_row(root, fl4253)
write_overlay_row(root, fl4254)

print("Done. Verify with: python3 scripts/analyze_failure_log.py overlay --fl FL-4253")
print("             and: python3 scripts/analyze_failure_log.py overlay --fl FL-4254")

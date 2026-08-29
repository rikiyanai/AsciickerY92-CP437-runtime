# Ad hoc script: Aggregate FL-4260 row-level retained package coverage against current scanner rows
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

from __future__ import annotations
import csv, json
from pathlib import Path
repo = Path(__file__).resolve().parents[2]
base = repo / 'docs/research/ascii/verification/fl4260'
inv = base / '2026-06-18-phase0-current-head-control-inventory/asciiid-ui-current-head-control-inventory.csv'
rows = []
with inv.open() as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(int(r['row']))
packages = [
    '2026-06-22-row-level-measurement-receipts-check',
    '2026-06-22-row-level-legacy-character-tabstrip-check',
    '2026-06-22-row-level-material-look-expanded-check',
    '2026-06-22-row-level-edit-brush-check',
    '2026-06-22-row-level-edit-sprite-check',
    '2026-06-22-row-level-view-shared-brush-check',
    '2026-06-22-row-level-edit-item-enemy-story-check',
    '2026-06-22-row-level-info-probe-check',
]
seen = {}
missing_packages = []
for pkg in packages:
    path = base / pkg / 'row-level-headed-inventory-check.csv'
    if not path.exists():
        missing_packages.append(pkg)
        continue
    with path.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            raw = r.get('inventory_row') or r.get('row') or ''
            try:
                row = int(raw)
            except ValueError:
                continue
            seen.setdefault(row, []).append(pkg)
missing_rows = [r for r in rows if r not in seen]
duplicates = {str(r): pkgs for r, pkgs in sorted(seen.items()) if len(pkgs) > 1}
current_only = {str(r): seen[r] for r in sorted(seen) if r in rows}
out = {
    'current_scanner_rows': len(rows),
    'current_min_row': min(rows),
    'current_max_row': max(rows),
    'covered_current_rows': len([r for r in rows if r in seen]),
    'missing_current_rows': missing_rows,
    'duplicate_current_rows': {k:v for k,v in duplicates.items() if int(k) in rows},
    'package_count': len(packages),
    'missing_packages': missing_packages,
    'package_rows': current_only,
}
path = base / '2026-06-22-row-level-current-coverage-audit.json'
path.write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
print(json.dumps({k: out[k] for k in ['current_scanner_rows','covered_current_rows','missing_current_rows','duplicate_current_rows','missing_packages']}, indent=2, sort_keys=True))

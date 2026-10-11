# Ad hoc script: Reconcile duplicate FL ID counts between validate and manual grep
# Created: 2026-05-04
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""
Reconcile duplicate FL ID counts between validate output and manual grep.
Legacy baseline has 8 duplicates (FL-053, FL-058, FL-178, FL-306, FL-311, FL-320, FL-528, FL-2464).
Validate surfaces 18 more. The remaining 9 (including FL-2622) are gaps not caught by current validator.
"""
import re

with open('docs/FAILURE_LOG.md') as f:
    content = f.read()

# Find legacy baseline IDs
baseline = re.search(r'"duplicate_ids":\s*\[(.*?)\]', content, re.DOTALL)
legacy_ids = re.findall(r'"(FL-\d+)"', baseline.group(1)) if baseline else []
print(f"Legacy baseline duplicate IDs: {len(legacy_ids)}")
for fid in legacy_ids:
    print(f"  {fid}")

# Find current duplicates by regex (FL-NNNN appearing more than once)
fid_counter = {}
for m in re.finditer(r'^###\s+(FL-\d+)', content, re.MULTILINE):
    fid = m.group(1)
    fid_counter[fid] = fid_counter.get(fid, 0) + 1

current_dups = [fid for fid, count in sorted(fid_counter.items()) if count > 1]
print(f"\nMarkdown-header duplicate IDs: {len(current_dups)}")
for fid in current_dups:
    print(f"  {fid}: {fid_counter[fid]}x")

# Show the gap: current duplicates not in legacy baseline
gap = [fid for fid in current_dups if fid not in legacy_ids]
print(f"\nNew duplicates not in legacy baseline: {len(gap)}")
for fid in gap:
    print(f"  {fid} ({fid_counter[fid]}x)")

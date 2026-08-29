# Ad hoc script: FL-4451 geometry invariance audit for Material Look edits
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Audit FL-4451 saved TERM++ dumps for geometry invariance.

Material Look may change final glyph/color policy for target material cells.
It must not change cell coordinates, material attribution, dispatch surface,
ramp/density route, shade/elevation provenance, or sample diffuse inputs.
"""
import json
import os
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BASE_DIR = os.path.join(ROOT, 'docs/research/ascii/verification/fl4260/2026-06-24-FL4451-per-material-isolation')
OUT_DIR = os.path.join(ROOT, 'docs/research/ascii/verification/fl4260/2026-06-24-FL4451-per-material-profile-textures')
OUT = os.path.join(OUT_DIR, 'geometry_invariance_summary.json')

PAIRS = [
    ('unused7', 'clean00_before', 'clean01_after_unused7', 7),
    ('used4_clear', 'clean01_after_unused7', 'clean02a_after_used4_clear', 4),
    ('used4_fill', 'clean02a_after_used4_clear', 'clean02b_after_used4_fill', 4),
]

BRIDGE_FIELDS = [
    'material_id', 'dispatch_surface', 'resolve_elev_idx', 'resolve_shade_idx',
    'cell_ramp_idx', 'cell_density_idx', 'sample_diffuses'
]
CELL_VIS_FIELDS = ['fg', 'bk', 'final_gid']

def load_rows(path):
    rows = {}
    with open(path) as fh:
        for line in fh:
            obj = json.loads(line)
            if obj.get('kind') == 'cell':
                rows[(obj['x'], obj['y'])] = obj
    return rows

def compare_bridge(a, b):
    changed = Counter()
    examples = {}
    for key in sorted(set(a) | set(b)):
        ao = a.get(key)
        bo = b.get(key)
        if ao is None or bo is None:
            changed['missing_or_added_cell'] += 1
            examples.setdefault('missing_or_added_cell', [key, ao, bo])
            continue
        for f in BRIDGE_FIELDS:
            if ao.get(f) != bo.get(f):
                changed[f] += 1
                examples.setdefault(f, [key, ao.get(f), bo.get(f)])
    return changed, examples

def compare_cells(a, b):
    changed = []
    for key in sorted(set(a) & set(b)):
        ao = a[key]
        bo = b[key]
        if any(ao.get(f) != bo.get(f) for f in CELL_VIS_FIELDS):
            changed.append(key)
    return changed

summary = {'schema': 'fl4451.geometry_invariance.v2', 'pairs': []}
for name, before, after, target_mat in PAIRS:
    before_bridge = load_rows(os.path.join(BASE_DIR, before, 'bridge.jsonl'))
    after_bridge = load_rows(os.path.join(BASE_DIR, after, 'bridge.jsonl'))
    before_cells = load_rows(os.path.join(BASE_DIR, before, 'cells.jsonl'))
    after_cells = load_rows(os.path.join(BASE_DIR, after, 'cells.jsonl'))
    bridge_changed, examples = compare_bridge(before_bridge, after_bridge)
    visual_changed = compare_cells(before_cells, after_cells)
    mat_hist = Counter(after_bridge.get(k, {}).get('material_id') for k in visual_changed)
    off_target = sum(n for mat, n in mat_hist.items() if mat != target_mat)
    target_changed = mat_hist.get(target_mat, 0)
    is_unused_pair = target_changed == 0 and target_mat not in set(after_bridge.get(k, {}).get('material_id') for k in after_bridge)
    pass_visual_scope = off_target == 0 and (is_unused_pair or target_changed == len(visual_changed))
    summary['pairs'].append({
        'name': name,
        'before': before,
        'after': after,
        'target_material': target_mat,
        'bridge_invariant_changed_counts': dict(bridge_changed),
        'bridge_invariant_examples': examples,
        'visual_changed_cells': len(visual_changed),
        'visual_changed_material_histogram': dict(mat_hist),
        'target_visual_changed_cells': target_changed,
        'off_target_visual_changed_cells': off_target,
        'verdict': 'PASS' if not bridge_changed and pass_visual_scope else 'FAIL',
    })

summary['verdict'] = 'PASS' if all(p.get('verdict') == 'PASS' for p in summary['pairs']) else 'FAIL'

os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT, 'w') as fh:
    json.dump(summary, fh, indent=2, sort_keys=True)
    fh.write('\n')
print(json.dumps(summary, indent=2, sort_keys=True))

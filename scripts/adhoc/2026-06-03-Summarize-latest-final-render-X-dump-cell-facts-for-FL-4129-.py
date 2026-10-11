# Ad hoc script: Summarize latest final-render X dump cell facts for FL-4129 water report
# Created: 2026-06-03
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.run/final_render_cell_dump')
metadata_paths = sorted(root.glob('*/metadata.json'))
if not metadata_paths:
    raise SystemExit('no final-render dump metadata found')
meta_path = metadata_paths[-1]
run_dir = meta_path.parent
meta = json.loads(meta_path.read_text())
rows = []
with (run_dir / 'cells.jsonl').open() as fh:
    for line in fh:
        rows.append(json.loads(line))
print('dump_dir', run_dir)
print('git_head', meta.get('git_head'))
print('dirty', meta.get('dirty'))
print('camera_world', meta.get('camera_world'))
print('player_world', meta.get('player_world'))
print('feature_state', meta.get('feature_state'))
print('fact_readback_state', meta.get('fact_readback_state'))
print('rows', len(rows))
print('glyph_chars', Counter(r.get('glyph_char') for r in rows).most_common(12))
print('source_class', Counter(r.get('source_class') for r in rows).most_common(12))
print('water_state_nonnull', sum(1 for r in rows if r.get('water_state') is not None))
print('actor_rows', sum(1 for r in rows if r.get('actor_id') is not None))
print('top_fg_rgb', Counter(tuple((r.get('fg_rgb') or {}).get(k) for k in ('r','g','b')) for r in rows).most_common(12))

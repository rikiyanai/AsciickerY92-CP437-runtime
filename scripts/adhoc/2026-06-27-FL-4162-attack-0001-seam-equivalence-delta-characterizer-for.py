# Ad hoc script: FL-4162 attack-0001 seam-equivalence delta characterizer: for the first server-reachable key resolving to attack-0001, compile reviewed-owner (cache=None) vs legacy-merge-owner (cache={}), report cell-count each, count differing positions, classify new-only vs legacy-only vs value-diff cells, and dump the legacy profile layer shape vs reviewed layer shape; authority:false NOT closure (Law16)
# Created: 2026-06-27
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Characterize WHY attack-0001 reviewed-owner != legacy-merge-owner compiled cells."""
from __future__ import annotations
import json, sys
from pathlib import Path
REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(REPO / "scripts"))
import compile_actor_visual_profiles as c

def source_id_for(key):
    fam, ahsw, _ = c._resolve_source_xp(key)
    return f"{fam}-{ahsw}"

def cells_for(key):
    profile, _ = c._synthetic_profile_for_key(key)
    _, cells, meta = c._compile_profile_cells(profile, {})
    return profile, cells, meta

c._LAYER_ROLES_CACHE = None
keys = list(c._enumerate_server_reachable_keys())
k = next(x for x in keys if source_id_for(x) == "attack-0001")

c._LAYER_ROLES_CACHE = None
prof_new, cells_new, meta_new = cells_for(k)
c._LAYER_ROLES_CACHE = {}
prof_legacy, cells_legacy, meta_legacy = cells_for(k)
c._LAYER_ROLES_CACHE = None

def index_cells(cells):
    # cells may be list of dict-like; try to key by (x,y) if present, else by position
    out = {}
    for i, cell in enumerate(cells):
        if isinstance(cell, dict) and "x" in cell and "y" in cell:
            out[(cell["x"], cell["y"])] = cell
        else:
            out[i] = cell
    return out

ni, li = index_cells(cells_new), index_cells(cells_legacy)
new_only = sorted(set(ni) - set(li), key=str)
legacy_only = sorted(set(li) - set(ni), key=str)
common = set(ni) & set(li)
val_diff = [p for p in common if ni[p] != li[p]]

print(json.dumps({
    "key": k,
    "cell_count_new": len(cells_new),
    "cell_count_legacy": len(cells_legacy),
    "frame_meta_identical": meta_new == meta_legacy,
    "new_only_positions": len(new_only),
    "legacy_only_positions": len(legacy_only),
    "value_diff_positions": len(val_diff),
    "new_only_sample": [str(p) for p in new_only[:8]],
    "legacy_only_sample": [str(p) for p in legacy_only[:8]],
    "value_diff_sample": [{"pos": str(p), "new": str(ni[p])[:120], "legacy": str(li[p])[:120]} for p in val_diff[:6]],
    "new_layer_shape": [(l.get("role"), l.get("layer_index"), bool(l.get("merge_extra_layers"))) for l in prof_new["layers"]],
    "legacy_layer_shape": [(l.get("role"), l.get("layer_index"), bool(l.get("merge_extra_layers"))) for l in prof_legacy["layers"]],
}, indent=2))

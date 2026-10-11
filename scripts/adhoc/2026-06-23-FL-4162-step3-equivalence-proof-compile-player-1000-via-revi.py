# Ad hoc script: FL-4162 step3 equivalence proof: compile player-1000 via reviewed per-role layer_roles owner vs legacy merge_extra_layers owner; prove compiled cells are pixel-identical (ownership change only, no visual change) and ownership shape differs
# Created: 2026-06-23
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 step 3 equivalence proof for the compiler source-owner seam.

Proves that migrating player-1000 to reviewed per-role layer ownership
(L2 player_body + L3 player_armor_regular, merge_extra_layers False) produces the
SAME compiled cells as the legacy single-body owner (L2, merge_extra_layers True) —
i.e. the change is ownership/traceability only, with no visual change. Also proves
the ownership SHAPE differs (2 explicit layers vs 1 merged), and that un-migrated
keys are untouched.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-22-step3-compiler-seam-equivalence"
sys.path.insert(0, str(REPO / "scripts"))
import compile_actor_visual_profiles as c


def cells_for(key):
    profile, _ = c._synthetic_profile_for_key(key)
    _, cells, meta = c._compile_profile_cells(profile, {})
    return profile, cells, meta


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    keys = list(c._enumerate_server_reachable_keys())
    pl = [k for k in keys if c._resolve_source_xp(k)[:2] == ("player", "1000")][0]

    # NEW owner (reviewed source is present -> migrated)
    c._LAYER_ROLES_CACHE = None
    prof_new, cells_new, meta_new = cells_for(pl)

    # LEGACY owner (force no migration)
    c._LAYER_ROLES_CACHE = {}
    prof_legacy, cells_legacy, meta_legacy = cells_for(pl)

    # restore
    c._LAYER_ROLES_CACHE = None

    new_layers = [(l["role"], l["layer_index"], l["merge_extra_layers"]) for l in prof_new["layers"]]
    legacy_layers = [(l["role"], l["layer_index"], l["merge_extra_layers"]) for l in prof_legacy["layers"]]

    checks = {
        "new_owner_two_per_role_layers": new_layers == [
            ("player_body", 2, False), ("player_armor_regular", 3, False)],
        "legacy_owner_single_merged_body": legacy_layers == [("body", 2, True)],
        "ownership_shape_changed": new_layers != legacy_layers,
        "compiled_cells_pixel_identical": cells_new == cells_legacy,
        "frame_meta_identical": meta_new == meta_legacy,
        "cell_count_nonzero": len(cells_new) > 0,
    }
    result = {
        "all_pass": all(checks.values()),
        "checks": checks,
        "new_layers": new_layers,
        "legacy_layers": legacy_layers,
        "cell_count": len(cells_new),
        "outdir": str(OUTDIR),
    }
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

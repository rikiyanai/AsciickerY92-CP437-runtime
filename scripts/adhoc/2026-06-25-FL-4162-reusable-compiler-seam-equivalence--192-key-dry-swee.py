# Ad hoc script: FL-4162 reusable compiler-seam equivalence + 192-key dry sweep: reads migrated_source_ids from assets/actor_visual_profiles/source/layer_roles.json (works for any promoted tier); proves each migrated id pixel-identical reviewed-role vs legacy merge owner, ownership shape differs, migrated keys never hit merge_extra_layers, sweep errors=0; authority:false NOT closure (Law16)
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 reusable seam-equivalence + 192-key dry sweep.

Reads migrated_source_ids straight from layer_roles.json, so it proves whatever
tier is currently promoted. For each migrated source_id: compiling via the
reviewed per-role owner must yield byte-identical compiled cells + frame meta as
the legacy single-body merge owner (ownership change only, no visual change),
with a different ownership shape and merge_extra_layers=false. Then sweeps all
server-reachable keys: migrated-resolved keys must never use merge_extra_layers.

authority:false -- a clean run is NOT closure (Canon Law 16).
"""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
LAYER_ROLES = REPO / "assets/actor_visual_profiles/source/layer_roles.json"
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-25-seam-equivalence-current"
sys.path.insert(0, str(REPO / "scripts"))
import compile_actor_visual_profiles as c

MIGRATED = set(json.loads(LAYER_ROLES.read_text())["migrated_source_ids"])


def source_id_for(key):
    fam, ahsw, _ = c._resolve_source_xp(key)
    return f"{fam}-{ahsw}"


def cells_for(key):
    profile, _ = c._synthetic_profile_for_key(key)
    _, cells, meta = c._compile_profile_cells(profile, {})
    return profile, cells, meta


def first_key_for(keys, sid):
    for k in keys:
        if source_id_for(k) == sid:
            return k
    return None


def equiv_for(keys, sid):
    k = first_key_for(keys, sid)
    if k is None:
        return {"source_id": sid, "all_pass": False, "error": "no server-reachable key resolves to this source_id"}
    c._LAYER_ROLES_CACHE = None
    prof_new, cells_new, meta_new = cells_for(k)
    c._LAYER_ROLES_CACHE = {}
    prof_legacy, cells_legacy, meta_legacy = cells_for(k)
    c._LAYER_ROLES_CACHE = None
    new_layers = [(l["role"], l["layer_index"], bool(l.get("merge_extra_layers"))) for l in prof_new["layers"]]
    legacy_layers = [(l["role"], l["layer_index"], bool(l.get("merge_extra_layers"))) for l in prof_legacy["layers"]]
    checks = {
        "new_owner_no_merge_extra": all(not m for _, _, m in new_layers),
        "new_owner_explicit_reviewed_roles": all(role != "body" for role, _, _ in new_layers),
        "legacy_owner_uses_merge_extra": any(m for _, _, m in legacy_layers),
        "ownership_shape_changed": new_layers != legacy_layers,
        "compiled_cells_pixel_identical": cells_new == cells_legacy,
        "frame_meta_identical": meta_new == meta_legacy,
        "cell_count_nonzero": len(cells_new) > 0,
    }
    return {"source_id": sid, "all_pass": all(checks.values()), "checks": checks,
            "new_layers": new_layers, "legacy_layers": legacy_layers, "cell_count": len(cells_new)}


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    c._LAYER_ROLES_CACHE = None
    keys = list(c._enumerate_server_reachable_keys())
    equiv = [equiv_for(keys, sid) for sid in sorted(MIGRATED)]

    c._LAYER_ROLES_CACHE = None
    migrated_keys = legacy_keys = 0
    seen = set()
    errors = []
    for k in keys:
        try:
            sid = source_id_for(k)
            prof, _ = c._synthetic_profile_for_key(k)
            uses_merge = any(l.get("merge_extra_layers") for l in prof["layers"])
            if sid in MIGRATED:
                migrated_keys += 1
                seen.add(sid)
                if uses_merge:
                    errors.append({"source_id": sid, "why": "migrated key still uses merge_extra_layers"})
            else:
                legacy_keys += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"error": repr(exc)})

    result = {
        "migrated_ids": sorted(MIGRATED),
        "equivalence_all_pass": all(e["all_pass"] for e in equiv),
        "equivalence": equiv,
        "sweep": {
            "total_keys": len(keys),
            "migrated_keys": migrated_keys,
            "legacy_keys": legacy_keys,
            "distinct_migrated_count": len(seen),
            "errors": errors,
        },
        "invariants_hold": (len(keys) == 192 and not errors and len(seen) == len(MIGRATED)),
        "all_pass": all(e["all_pass"] for e in equiv) and len(keys) == 192 and not errors and len(seen) == len(MIGRATED),
        "note": "authority:false; a clean run is NOT closure (Law 16)",
    }
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

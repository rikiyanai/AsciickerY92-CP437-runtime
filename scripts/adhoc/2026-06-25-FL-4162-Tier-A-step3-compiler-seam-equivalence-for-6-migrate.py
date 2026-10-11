# Ad hoc script: FL-4162 Tier-A step3: compiler-seam equivalence for 6 migrated source_ids (player-1000 + Tier A) + 192-key dry sweep; pixel-identical reviewed-role vs legacy merge owner, ownership shape differs, migrated keys never hit merge_extra_layers, errors=0; authority:false NOT closure (Law16)
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 Tier-A step3 equivalence proof + 192-key dry sweep.

Migrated source_ids under test (6): player-1000 (already) + Tier A
(player-0000, player-0001, player-0010, plydie-0000, wolfie-0001).

Proves, per migrated source_id, that compiling via reviewed per-role layer
ownership (merge_extra_layers False, explicit reviewed roles) yields the SAME
compiled cells + frame meta as the legacy single-body merge owner -> ownership
change only, no visual change; and the ownership shape differs. Then sweeps all
server-reachable keys: classifies migrated vs legacy, asserts migrated-resolved
keys never use merge_extra_layers, counts errors.

authority:false -- a clean run here is NOT closure (Canon Law 16). No runtime or
semantic-map promotion claims.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-24-tierA-step3-seam-equivalence"
sys.path.insert(0, str(REPO / "scripts"))
import compile_actor_visual_profiles as c

MIGRATED = {"player-0000", "player-0001", "player-0010", "player-1000", "plydie-0000", "wolfie-0001"}


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
    c._LAYER_ROLES_CACHE = None          # read the promoted file -> migrated owner
    prof_new, cells_new, meta_new = cells_for(k)
    c._LAYER_ROLES_CACHE = {}            # force legacy owner
    prof_legacy, cells_legacy, meta_legacy = cells_for(k)
    c._LAYER_ROLES_CACHE = None          # restore
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
    return {
        "source_id": sid, "all_pass": all(checks.values()), "checks": checks,
        "new_layers": new_layers, "legacy_layers": legacy_layers, "cell_count": len(cells_new),
    }


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    c._LAYER_ROLES_CACHE = None
    keys = list(c._enumerate_server_reachable_keys())

    equiv = [equiv_for(keys, sid) for sid in sorted(MIGRATED)]

    # 192-key dry sweep
    c._LAYER_ROLES_CACHE = None
    migrated_keys = 0
    legacy_keys = 0
    migrated_sids_seen = set()
    errors = []
    for k in keys:
        try:
            sid = source_id_for(k)
            prof, _ = c._synthetic_profile_for_key(k)
            uses_merge = any(l.get("merge_extra_layers") for l in prof["layers"])
            if sid in MIGRATED:
                migrated_keys += 1
                migrated_sids_seen.add(sid)
                if uses_merge:
                    errors.append({"source_id": sid, "why": "migrated key still uses merge_extra_layers"})
            else:
                legacy_keys += 1
        except Exception as exc:  # noqa: BLE001 -- sweep must count, not abort
            errors.append({"error": repr(exc)})

    sweep = {
        "total_keys": len(keys),
        "migrated_keys": migrated_keys,
        "legacy_keys": legacy_keys,
        "distinct_migrated_source_ids_seen": sorted(migrated_sids_seen),
        "distinct_migrated_count": len(migrated_sids_seen),
        "errors": errors,
        "user_predicted": {"total": 192, "migrated": 6, "legacy": 186, "errors": 0},
    }
    invariants_hold = (
        len(keys) == 192
        and len(errors) == 0
        and migrated_keys + legacy_keys == len(keys)
        and len(migrated_sids_seen) == 6
    )
    result = {
        "migrated_ids": sorted(MIGRATED),
        "equivalence_all_pass": all(e["all_pass"] for e in equiv),
        "equivalence": equiv,
        "sweep": sweep,
        "invariants_hold": invariants_hold,
        "all_pass": all(e["all_pass"] for e in equiv) and invariants_hold,
        "note": "authority:false; a clean run is NOT closure (Law 16); no runtime/semantic-map claims",
    }
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

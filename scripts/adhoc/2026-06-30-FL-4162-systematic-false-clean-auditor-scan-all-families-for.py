# Ad hoc script: FL-4162 systematic false-clean auditor: scan all families for single-role weapon labels (sword/crossbow) sitting on dense body-fill layers (the body+weapon composite mislabeled as a standalone weapon, the false-clean gate gap). Excludes weapon_swoosh (legit single-role overlay). Flags promoted vs unpromoted. Read-only evidence surface for contract-honesty (Law 5).
# Created: 2026-06-30
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 systematic false-clean auditor (READ-ONLY).

The classifier treats any single hand role as 'owned'. A dense body+weapon layer
hand-labeled only '<fam>_weapon_sword' therefore passes as a clean standalone-weapon
owner while it is really a body+weapon composite -- the false-clean gate gap first
seen at wolack-0001-L3, which this auditor generalizes across families.

A card is a SUSPECT when: exactly one proposed role naming a weapon (sword/crossbow,
NOT swoosh), occupancy > 800 cells, and body-fill block/shade glyphs dominate (>0.7).
weapon_swoosh is excluded (it is a legitimate single-role cyan overlay that folds via
the engine special-case). Read-only; the hand corpus is never edited.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BODY_FILL = {176, 177, 178, 219, 220, 221, 222, 223}  # shade + half/full block (body/fur)
OCC_MIN = 800
BODY_FRAC_MIN = 0.7


def _compiler():
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cavp", REPO / "scripts" / "compile_actor_visual_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_single_weapon(roles):
    if len(roles) != 1:
        return False
    r = roles[0]
    if "swoosh" in r:
        return False
    return "sword" in r or "crossbow" in r or "weapon" in r


def main():
    m = _compiler()
    pkt = {r["card_id"]: r for r in json.loads(
        (REPO / "docs/research/ascii/semantic_maps/manual_candidate_review.json").read_text())["reviewed"]}
    mig = set(json.loads(
        (REPO / "assets/actor_visual_profiles/source/layer_roles.json").read_text())["migrated_source_ids"])
    suspects = []
    for cid, r in pkt.items():
        roles = (r.get("agent_verdict") or {}).get("proposed_roles") or []
        if not _is_single_weapon(roles):
            continue
        fam, ahsw = cid.split("-")[0], cid.split("-")[1]
        li = int(cid.rsplit("-L", 1)[1])
        try:
            xp = m._load_xp(REPO / f"assets/sprites/{fam}-{ahsw}.xp")
        except Exception:
            continue
        if li >= len(xp.layers):
            continue
        cells = xp.layers[li].cells
        occ = sum(1 for c in cells if c[0] != 32)
        fill = sum(1 for c in cells if c[0] in BODY_FILL)
        body_frac = fill / occ if occ else 0.0
        if occ > OCC_MIN and body_frac > BODY_FRAC_MIN:
            suspects.append({
                "card_id": cid, "role": roles[0], "hand_status": r.get("hand_status"),
                "occ": occ, "body_fill_frac": round(body_frac, 2),
                "promoted": f"{fam}-{ahsw}" in mig,
                "single_visible_layer": len(xp.layers) == li + 1 and li == 2,
            })
    suspects.sort(key=lambda s: (-s["promoted"], -s["occ"]))
    promoted = [s["card_id"] for s in suspects if s["promoted"]]
    print(json.dumps({
        "fl": "FL-4162", "audit": "single_weapon_role_false_clean",
        "suspect_count": len(suspects),
        "promoted_suspects": promoted,
        "suspects": suspects,
    }, indent=2))


if __name__ == "__main__":
    main()

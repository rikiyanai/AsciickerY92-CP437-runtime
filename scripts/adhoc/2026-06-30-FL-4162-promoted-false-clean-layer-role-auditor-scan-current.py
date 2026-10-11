# Ad hoc script: FL-4162 promoted false-clean layer-role auditor: scan currently migrated layer_roles rows for single equipment labels on dense body/rider composite cells; report exact/near matches and unpromote candidates
# Created: 2026-06-30
# Canonical gap: RQ-200 needs a first-class promoted-layer contract-honesty gate
# that rejects clean equipment labels on base/rider composite source layers before
# they enter layer_roles.json.

#!/usr/bin/env python3
"""FL-4162 promoted false-clean layer-role auditor (READ-ONLY).

Scans the currently migrated source layer_roles rows and flags rows whose
contract role is a single clean equipment label while the raw XP layer looks
like a dense body/rider composite. This audits the promoted surface only; it
never edits state_FINAL, decisions, layer_roles, profiles, maps, or XP assets.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BODY_FILL = {176, 177, 178, 219, 220, 221, 222, 223}
EQUIP_TOKENS = ("weapon_sword", "weapon_crossbow", "shield", "armor", "helmet", "sword", "crossbow")
BODY_COMPOSITE_LABEL_TOKENS = ("weapon_sword", "weapon_crossbow", "shield", "sword", "crossbow")
LEGIT_SINGLE_TOKENS = ("weapon_swoosh",)
DENSE_OCC_MIN = 800
BODY_FRAC_MIN = 0.70


def _load_compiler():
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "compile_actor_visual_profiles",
        REPO / "scripts" / "compile_actor_visual_profiles.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _read_json(path: Path):
    return json.loads(path.read_text())


def _review_by_card():
    data = _read_json(REPO / "docs/research/ascii/semantic_maps/manual_candidate_review.json")
    return {row["card_id"]: row for row in data["reviewed"]}


def _evidence_by_card():
    out = {}
    p = REPO / "docs/research/ascii/semantic_maps/layer_evidence_cards.jsonl"
    for line in p.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["card_id"]] = row
    return out


def _is_single_clean_equipment(role: str) -> bool:
    if ";" in role:
        return False
    if any(tok in role for tok in LEGIT_SINGLE_TOKENS):
        return False
    return any(tok in role for tok in EQUIP_TOKENS)


def _layer_metrics(xp, layer_index: int):
    cells = xp.layers[layer_index].cells
    occ = sum(1 for c in cells if c[0] != 32)
    body = sum(1 for c in cells if c[0] in BODY_FILL)
    glyphs = Counter(c[0] for c in cells if c[0] != 32)
    positions = [
        {"x": i % xp.width, "y": i // xp.width, "glyph": c[0], "fg": c[1], "bg": c[2]}
        for i, c in enumerate(cells)
        if c[0] != 32
    ]
    return {
        "occupancy": occ,
        "body_fill_count": body,
        "body_fill_fraction": round(body / occ, 4) if occ else 0.0,
        "visible_glyph_set": sorted(glyphs),
        "top_glyphs": [{"glyph": glyph, "count": count} for glyph, count in glyphs.most_common(8)],
        "sample_positions": positions[:24],
    }


def _reason(role: str, layer_index: int, metrics: dict, review: dict | None) -> list[str]:
    reasons = []
    if _is_single_clean_equipment(role):
        reasons.append("single_clean_equipment_role")
    if layer_index == 2 and any(tok in role for tok in ("weapon", "shield", "sword", "crossbow")):
        reasons.append("L2_base_accumulator_named_as_equipment")
    if metrics["occupancy"] >= DENSE_OCC_MIN:
        reasons.append("dense_layer")
    if metrics["body_fill_fraction"] >= BODY_FRAC_MIN:
        reasons.append("body_fill_dominates")
    hand_label = (review or {}).get("hand_corrected_label", "")
    if hand_label == role:
        reasons.append("manual_label_is_false_clean_surface")
    return reasons


def _is_false_clean_composite(role: str, layer_index: int, metrics: dict) -> bool:
    if not _is_single_clean_equipment(role):
        return False
    names_body_composite_feature = any(tok in role for tok in BODY_COMPOSITE_LABEL_TOKENS)
    if not names_body_composite_feature:
        return False
    # L2 is the engine base accumulator. A promoted L2 named only as sword,
    # crossbow, or shield is a body/rider composite mislabeled as equipment.
    if layer_index == 2:
        return True
    # Above L2, only weapon labels with dense body-fill are suspicious. Armor,
    # helmet, and shield overlays often legitimately use block glyphs.
    if ("sword" in role or "crossbow" in role or "weapon" in role) and metrics["occupancy"] >= DENSE_OCC_MIN and metrics["body_fill_fraction"] >= BODY_FRAC_MIN:
        return True
    return False


def main():
    compiler = _load_compiler()
    layer_roles = _read_json(REPO / "assets/actor_visual_profiles/source/layer_roles.json")
    review = _review_by_card()
    evidence = _evidence_by_card()

    suspects = []
    promoted_rows = []
    for source_id in layer_roles["migrated_source_ids"]:
        profile = layer_roles["profiles"][source_id]
        for layer in profile["layers"]:
            role = layer["role"]
            source_key = layer["source_key"]
            layer_index = layer["layer_index"]
            xp_path = REPO / layer["source_xp"]
            xp = compiler._load_xp(xp_path)
            metrics = _layer_metrics(xp, layer_index)
            row = review.get(source_key)
            card = evidence.get(source_key)
            promoted_rows.append({
                "source_id": source_id,
                "source_key": source_key,
                "role": role,
                "occupancy": metrics["occupancy"],
                "body_fill_fraction": metrics["body_fill_fraction"],
            })
            if _is_false_clean_composite(role, layer_index, metrics):
                suggested_contract_role = role
                if layer_index == 2 and source_id.startswith("player-") and "weapon_sword" in role:
                    suggested_contract_role = "player_body;player_weapon_sword"
                elif layer_index == 2 and source_id.startswith("player-") and "shield" in role:
                    suggested_contract_role = "player_body;player_shield_regular"
                elif layer_index == 2 and source_id.startswith("wolfie-") and "weapon_sword" in role:
                    suggested_contract_role = "mount_body_wolf;rider_torso;sword"
                elif source_key == "wolack-0001-L3":
                    suggested_contract_role = "mount_body_wolf;rider_torso;sword"
                suspects.append({
                    "source_id": source_id,
                    "source_key": source_key,
                    "current_role": role,
                    "suggested_contract_role": suggested_contract_role,
                    "action": "unpromote_until_contract_role_is_reconciled" if suggested_contract_role != role else "review_required_before_promotion",
                    "hand_status": (row or {}).get("hand_status"),
                    "hand_corrected_label": (row or {}).get("hand_corrected_label"),
                    "hand_note": (row or {}).get("hand_note"),
                    "queue_class": (row or {}).get("queue_class"),
                    "raw_layer_index": layer_index,
                    "source_xp": layer["source_xp"],
                    "metrics": metrics,
                    "exact_matches": (row or {}).get("glyph_exact_matches", []),
                    "near_matches": (row or {}).get("glyph_near_matches", []),
                    "evidence_atlas_visible_count": (card or {}).get("cells", {}).get("atlas_visible_count", (card or {}).get("atlas_visible_count")),
                    "reason": _reason(role, layer_index, metrics, row),
                })

    print(json.dumps({
        "fl": "FL-4162",
        "audit": "promoted_false_clean_layer_roles",
        "authority": False,
        "is_proposal": True,
        "migrated_source_ids_count": len(layer_roles["migrated_source_ids"]),
        "promoted_layer_rows_count": len(promoted_rows),
        "false_clean_promoted_count": len(suspects),
        "false_clean_source_keys": [s["source_key"] for s in suspects],
        "suspects": suspects,
        "notes": [
            "This audit checks the promoted layer_roles surface only.",
            "A pixel-identical seam proof does not prove the semantic ownership label is correct.",
            "Rows listed here should be removed from promoted layer_roles until a fingerprint-bound contract reconciliation promotes the composite role."
        ],
    }, indent=2))


if __name__ == "__main__":
    main()

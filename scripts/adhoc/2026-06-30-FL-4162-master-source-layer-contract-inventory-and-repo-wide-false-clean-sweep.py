# Ad hoc script: FL-4162 master source-layer contract inventory + repo-wide false-clean sweep.
# Builds ONE read-only evidence row per upstream XP layer (343 cards) joining the hand corpus,
# the engine fold behavior, the promoted layer_roles surface, the authorability report, and the
# fingerprint-bound composite-ownership decisions; then derives an engine-GROUNDED false-clean
# classification across EVERY equipment class (not just sword, not just promoted rows). Authority
# false. Edits nothing (state_FINAL / hand corpus untouched).
# Created: 2026-06-30
# Canonical gap: a single source_layer_master index does not yet exist; this is the first cut.

#!/usr/bin/env python3
"""FL-4162 master source-layer contract inventory + repo-wide false-clean sweep (READ-ONLY).

WHAT THIS FILE IS FOR
=====================
The bundle refactor is migrating asciicker's sprite layers from "implicit ownership"
(the engine just folds raw layers in a fixed order) to an *explicit* source-layer
contract (every visible layer has a reviewed, traceable role). To do that safely we
first need ONE place that lists every upstream XP layer with all of its evidence, so a
human can review the contract without re-deriving facts each time. This script builds
that index. It is EVIDENCE, never authority -- nothing here promotes or demotes anything.

KEY VOCABULARY (for a reader new to this codebase)
--------------------------------------------------
* "XP"      : an REXPaint sprite file. One actor pose = one .xp file with several LAYERS.
* "layer"   : one image plane inside the .xp. The engine stacks them to draw the actor.
* "L0..LN"  : layer indices. The engine assigns each a FIXED job (see ENGINE FOLD below).
* "card"    : one (xp-file, layer) pair under manual review. card_id e.g. "player-0001-L2".
* "AHSW"    : the 4 filename bits Armor/Helmet/Shield/Weapon -- they predict which equipment
              STATE a pose has, but they do NOT tell you a raw layer's role.
* "role"    : a human label for what a layer draws, e.g. "player_body", "player_weapon_sword".
* "false-clean": a COMPOSITE layer (body, or rider+equipment) wearing a single bare-equipment
              label, as if it were a clean standalone equipment mask. The defect we hunt.

ENGINE FOLD (the ground truth, from upstream sprite.cpp @ 8ff75d0c)
------------------------------------------------------------------
    L0            = color key / metadata          (sprite.cpp:350)
    L1            = height                          (sprite.cpp:351)
    L2            = image BASE accumulator (body)   (sprite.cpp:352)
    L3..final     = folded into L2 in order         (sprite.cpp:354-360)
    final cyan-fg = swoosh special-case             (sprite.cpp:361)

The corpus already records this per card as engine_is_overlay / engine_fixed_role, so the
false-clean test for the BASE layer (L2) is EXACT, not a pixel heuristic: if a layer the
engine treats as the L2 base accumulator (engine_is_overlay == False) carries a single bare
equipment role, it is structurally a body mislabeled as equipment -- a false-clean, period.

For OVERLAY layers (L3+) a single equipment role MIGHT be a clean mask OR a rider+equipment
composite (families like bigbee/wolfie bake the rider together with the gear). Pure structured
data cannot always tell those apart -- that needs pixels -- so this sweep is HONEST about it:
overlay equipment cards are bucketed as "needs render" unless a fingerprint-bound reconciliation
already resolved them. We never guess "clean" for an overlay we have not seen.

OUTPUTS (written under docs/research/ascii/verification/fl4162/<dated dir>/)
---------------------------------------------------------------------------
    MASTER_SOURCE_LAYER_CONTRACT_INVENTORY.json  -- 343 rows, every field the contract review needs
    FALSE_CLEAN_SWEEP.json                        -- derived classification + suspect roster
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths. This script lives in scripts/adhoc/, so the repo root is two parents up.
# (parents[0]=adhoc, parents[1]=scripts, parents[2]=repo root.)
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[2]
SEM = REPO / "docs" / "research" / "ascii" / "semantic_maps"
SPRITES = REPO / "assets" / "sprites"
OUT_DIR = (REPO / "docs" / "research" / "ascii" / "verification" / "fl4162"
           / "2026-06-30-master-source-layer-contract-inventory")

# A space glyph (32) marks an EMPTY cell in an .xp layer; everything else is "occupied".
SPACE_GLYPH = 32

# Role tokens that mean "this layer is a body / rider / mount base", i.e. legitimately NOT
# a clean equipment mask. A single role from this set is never a false-clean.
BODY_ROLE_TOKENS = ("_body", "mount_body", "rider_torso")

# The final cyan-foreground motion streak. It IS a legitimate single-role overlay (the engine
# special-cases it at the last layer), so it must be excluded from the equipment sweep.
SWOOSH_TOKEN = "weapon_swoosh"

# Bare equipment role tails (after stripping any family prefix). A single role ending in one of
# these -- and not a body/swoosh role -- is an "equipment" claim we must scrutinise.
EQUIPMENT_TAILS = (
    "weapon_sword", "weapon_crossbow", "weapon_bow",
    "shield_regular", "armor_regular", "helmet_regular",
    "shield", "armor", "helmet", "sword", "crossbow", "bow",
)


def _compiler():
    """Import the production compiler module so we reuse its EXACT .xp reader.

    We deliberately do not re-implement .xp parsing: the contract must be measured with the
    same loader the compiler/engine path uses, or the evidence would not be trustworthy.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cavp", REPO / "scripts" / "compile_actor_visual_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raw_layer_occupancy(m, source_key, layer_index):
    """Return the number of OCCUPIED (non-space) cells in one raw .xp layer.

    This is the authoritative per-layer density the renderer also used (occ). The corpus's
    glyph_count/atlas_visible_count are atlas-frame metrics, not raw-layer occupancy, so we
    measure it straight from the file. Returns None if the file/layer is missing.
    """
    xp_path = SPRITES / f"{source_key}.xp"
    if not xp_path.exists():
        return None
    xp = m._load_xp(xp_path)
    if layer_index is None or layer_index >= len(xp.layers):
        return None
    cells = xp.layers[layer_index].cells
    # Count every cell whose glyph is not the empty-space glyph.
    return sum(1 for c in cells if c[0] != SPACE_GLYPH)


def _single_role(proposed_roles):
    """If the hand corpus proposed EXACTLY one role, return it; else None.

    A single role is the precondition for a false-clean: a multi-role label like
    "player_body;player_weapon_sword" is already an honest composite, not a suspect.
    """
    if isinstance(proposed_roles, list) and len(proposed_roles) == 1:
        return proposed_roles[0]
    return None


def _is_body_role(role):
    """True when the role names a body/rider/mount base (never a false-clean)."""
    return any(tok in role for tok in BODY_ROLE_TOKENS)


def _is_equipment_role(role):
    """True when the single role is a bare equipment claim (the thing we audit).

    Excludes the swoosh (legit single overlay) and body roles. We match on the tail so the
    family prefix (player_/wolack_/...) does not matter: 'player_weapon_sword' and bare 'sword'
    both count.
    """
    if SWOOSH_TOKEN in role:
        return False
    if _is_body_role(role):
        return False
    return any(role == tail or role.endswith("_" + tail) or role == tail for tail in EQUIPMENT_TAILS) \
        or any(role.endswith(tail) for tail in EQUIPMENT_TAILS)


def main():
    m = _compiler()

    # --- Load the four evidence surfaces -----------------------------------
    # 1) The hand corpus: the human-reviewed cards (the primary evidence; never edited here).
    corpus = json.loads((SEM / "manual_candidate_review.json").read_text())["reviewed"]
    corpus_by_id = {r["card_id"]: r for r in corpus}

    # 2) The promoted surface: which source_ids are migrated, and the EFFECTIVE (possibly
    #    reconciled) role per layer. This is where a false-clean would have been "promoted".
    lr = json.loads((REPO / "assets/actor_visual_profiles/source/layer_roles.json").read_text())
    migrated_ids = set(lr.get("migrated_source_ids", []))
    promoted_role_by_card = {}
    for src_key, prof in lr.get("profiles", {}).items():
        for layer in prof.get("layers", []):
            idx = layer.get("layer_index")
            if idx is None:
                continue
            promoted_role_by_card[f"{src_key}-L{idx}"] = {
                "effective_role": layer.get("role"),
                "original_composite_roles": layer.get("original_composite_roles"),
            }

    # 3) The authorability report: per-card blocker/classification/content status.
    auth = json.loads((SEM / "compiler_authorability_report.json").read_text())
    auth_by_id = {r["card_id"]: r for r in auth.get("layers", [])}

    # 4) The fingerprint-bound composite-ownership decisions: the only surface that turns a
    #    false-clean single role into an honest composite WITHOUT editing the hand corpus.
    decisions = json.loads((SEM / "composite_ownership_decisions.json").read_text())["decisions"]
    decision_fps = {d.get("whole_atlas_fingerprint") for d in decisions if d.get("whole_atlas_fingerprint")}
    decision_keys = {d.get("source_key") for d in decisions if d.get("source_key")}

    # --- Build the master inventory: one row per card ----------------------
    inventory = []
    for cid, r in corpus_by_id.items():
        # IMPORTANT: in the corpus, "source_key" is actually the full card_id (e.g.
        # "player-0001-L2"). The bare XP stem (e.g. "player-0001") -- needed to open the .xp
        # file and to test promotion membership (migrated_source_ids holds stems) -- lives in
        # source_xp_path. The fingerprint-bound decisions, however, key by card_id, so we keep
        # src_key (card_id) for that match.
        src_key = r.get("source_key")  # == card_id; matches composite_ownership_decisions
        stem = Path(r.get("source_xp_path", "")).stem  # bare xp stem; matches migrated_source_ids
        layer_idx = r.get("raw_layer_index")
        av = r.get("agent_verdict") or {}
        proposed = av.get("proposed_roles") or []
        single = _single_role(proposed)
        promoted = promoted_role_by_card.get(cid)
        auth_row = auth_by_id.get(cid, {})
        fp = r.get("whole_atlas_fingerprint")

        # Reconciled = a fingerprint-bound decision covers this exact card (by fingerprint or
        # source_key), OR the promoted surface already carries a composite role for it.
        reconciled = bool(
            (fp and fp in decision_fps)
            or (src_key in decision_keys)
            or (promoted and promoted.get("effective_role")
                and ";" in (promoted.get("effective_role") or ""))
        )

        row = {
            "card_id": cid,
            "source_key": src_key,
            "family": r.get("family"),
            "raw_layer_index": layer_idx,
            "ahsw": (r.get("ahsw") or {}).get("raw"),
            "ahsw_weapon_name": (r.get("ahsw") or {}).get("weapon_name"),
            # --- engine fold behavior (already computed in the corpus) ---
            "engine_fixed_role": r.get("engine_fixed_role"),
            "engine_is_overlay": r.get("engine_is_overlay"),
            "engine_overlay_ordinal": r.get("engine_overlay_ordinal"),
            "engine_swoosh_cyan_fg": r.get("engine_swoosh_cyan_fg"),
            "engine_family_layer_count": r.get("engine_family_layer_count"),
            # --- hand evidence ---
            "hand_status": r.get("hand_status"),
            "hand_corrected_label": r.get("hand_corrected_label"),
            "hand_note": (r.get("hand_note") or "")[:200],
            "proposed_roles": proposed,
            "single_role": single,
            # --- glyph / cell evidence ---
            "visible_glyph_set": r.get("visible_glyph_set"),
            "glyph_exact_matches": r.get("glyph_exact_matches"),
            "glyph_near_matches": r.get("glyph_near_matches"),
            "raw_layer_occupancy": _raw_layer_occupancy(m, stem, layer_idx),
            "whole_atlas_fingerprint": fp,
            # --- contract / promotion / blocker state ---
            "classification": auth_row.get("classification"),
            "content_status": auth_row.get("content_status"),
            "content_blockers": auth_row.get("content_blockers"),
            "phase_gated": auth_row.get("phase_gated"),
            "promoted": stem in migrated_ids,
            "promoted_effective_role": (promoted or {}).get("effective_role"),
            "reconciled": reconciled,
        }
        inventory.append(row)

    inventory.sort(key=lambda x: (x["family"] or "", x["source_key"] or "", x["raw_layer_index"] or 0))

    # --- Derive the repo-wide false-clean sweep ----------------------------
    # Bucket every card that carries a SINGLE BARE EQUIPMENT role by the engine-grounded test.
    sweep = defaultdict(list)
    for row in inventory:
        role = row["single_role"]
        if not role or not _is_equipment_role(role):
            continue  # body roles, composites, swoosh, and no-role cards are not suspects

        is_base = row["engine_is_overlay"] is False  # L2 base accumulator (engine-fixed)
        reconciled = row["reconciled"]

        if is_base and not reconciled:
            # EXACT false-clean: the engine treats this as the body base, yet it claims a bare
            # equipment role and nothing has reconciled it. This MUST be reconciled before promotion.
            bucket = "engine_base_false_clean"
        elif is_base and reconciled:
            bucket = "engine_base_reconciled"            # body base, already made honest-composite
        elif (not is_base) and reconciled:
            bucket = "overlay_reconciled"                # overlay, already reconciled
        else:
            # Overlay with a single equipment role and no reconciliation: cannot be classified
            # clean-vs-rider-composite from structured data alone -- needs pixels.
            bucket = "overlay_equipment_needs_render"

        sweep[bucket].append({
            "card_id": row["card_id"],
            "single_role": role,
            "hand_status": row["hand_status"],
            "raw_layer_occupancy": row["raw_layer_occupancy"],
            "engine_fixed_role": row["engine_fixed_role"],
            "promoted": row["promoted"],
            "promoted_effective_role": row["promoted_effective_role"],
            "whole_atlas_fingerprint": row["whole_atlas_fingerprint"],
        })

    # The headline safety number: any L2-base false-clean that is BOTH promoted AND unreconciled
    # is a live ownership defect. (The promoted surface should already report 0 of these.)
    base_fc = sweep.get("engine_base_false_clean", [])
    promoted_base_fc = [s for s in base_fc if s["promoted"]]

    sweep_summary = {
        "fl": "FL-4162",
        "audit": "repo_wide_false_clean_sweep_engine_grounded",
        "authority": False,
        "is_proposal": True,
        "method": (
            "Engine-grounded: a single bare-equipment role on an L2 base accumulator "
            "(engine_is_overlay==False) is an EXACT false-clean; overlay equipment roles need "
            "pixels and are bucketed as needs-render unless fingerprint-reconciled. Excludes "
            "weapon_swoosh and multi-role composites."
        ),
        "totals": {k: len(v) for k, v in sorted(sweep.items())},
        "promoted_base_false_clean_count": len(promoted_base_fc),
        "promoted_base_false_clean": [s["card_id"] for s in promoted_base_fc],
        "buckets": dict(sweep),
        "law16_note": "authority:false; a clean sweep is NOT runtime closure (Canon Law 16).",
    }

    # --- Write the evidence artifacts --------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = {
        "fl": "FL-4162",
        "artifact": "master_source_layer_contract_inventory",
        "authority": False,
        "is_proposal": True,
        "recorded_at": "2026-06-30",
        "row_count": len(inventory),
        "engine_fold_reference": "upstream sprite.cpp @ 8ff75d0c: L0 colorkey/L1 height/L2 base/L3..final fold/final cyan swoosh",
        "note": "READ-ONLY evidence join of corpus + engine fold + promoted surface + authorability + reconciliations. state_FINAL untouched.",
        "rows": inventory,
    }
    (OUT_DIR / "MASTER_SOURCE_LAYER_CONTRACT_INVENTORY.json").write_text(
        json.dumps(master, indent=2))
    (OUT_DIR / "FALSE_CLEAN_SWEEP.json").write_text(json.dumps(sweep_summary, indent=2))

    # --- Console summary (compact) -----------------------------------------
    fam_counts = Counter(r["family"] for r in inventory)
    print(json.dumps({
        "inventory_rows": len(inventory),
        "families": dict(fam_counts),
        "sweep_totals": sweep_summary["totals"],
        "promoted_base_false_clean_count": sweep_summary["promoted_base_false_clean_count"],
        "promoted_base_false_clean": sweep_summary["promoted_base_false_clean"],
        "out_dir": str(OUT_DIR.relative_to(REPO)),
    }, indent=2))


if __name__ == "__main__":
    main()

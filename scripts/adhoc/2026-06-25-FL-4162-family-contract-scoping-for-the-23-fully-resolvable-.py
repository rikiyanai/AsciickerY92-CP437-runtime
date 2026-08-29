# Ad hoc script: FL-4162 family-contract scoping: for the 23 fully-resolvable blocked variants, define the topology-group surface (composite layers grouped by family + raw_layer_index + composite_roles) and extract the per-layer normalization sub-table WITH context evidence (family, approved_role, composite_roles, hand_status) so each bare->family-normalized rename is evidence-proven, NOT label-only. Read-only proposal artifact.
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 family-contract scoping (read-only).

Two proposal artifacts for the family-contract lane:
  1. topology_group_surface: the composite layers (reason composite_needs_family_
     contract) needed by the 23 fully-resolvable variants, grouped by
     (family, raw_layer_index) with the composite_roles seen. This is the surface
     a reviewed family topology contract must cover.
  2. normalization_subtable: every role_name_conflict layer on those variants,
     with its CONTEXT EVIDENCE (family, approved_role, composite_roles,
     hand_status). proposed_normalized_role is a SUGGESTION for review only --
     each must be confirmed against the layer's actual family/context, never
     applied from the label string alone (user constraint 2026-06-25).

Nothing here promotes or edits evidence. authority:false unchanged; NOT closure.
"""
from __future__ import annotations
import json, collections
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
SM = REPO / "docs/research/ascii/semantic_maps"
TRIAGE = REPO / "docs/research/ascii/verification/fl4162/2026-06-25-blocked-layer-triage"
OUT = REPO / "docs/research/ascii/verification/fl4162/2026-06-25-family-contract-scoping"

rows = json.loads((TRIAGE / "triage_rows.json").read_text())
req = {r["requirement_id"].replace("avp_req:", ""): r
       for r in json.loads((SM / "actor_visual_profile_requirements.json").read_text())["requirements"]}

# Fully-resolvable variants: no STRUCTURAL blocked layer among their blocked rows.
by_variant = collections.defaultdict(list)
for r in rows:
    by_variant[r["card_id"].rsplit("-L", 1)[0]].append(r)
fully_resolvable = {v for v, rs in by_variant.items()
                    if rs and not any(x["resolvability"].startswith("STRUCTURAL") for x in rs)}

# Suggestion map (REVIEW ONLY): bare label -> family-qualified pattern. Applied
# only after per-layer family/context confirmation.
BARE_TO_PATTERN = {
    "armor": "{family}_armor_regular",
    "helmet": "{family}_helmet_regular",
    "shield": "{family}_shield_regular",
    "crossbow": "{family}_weapon_crossbow",
    "sword": "{family}_weapon_sword",
    "weapon_crossbow": "{family}_weapon_crossbow",
    "weapon_sword": "{family}_weapon_sword",
}


def ctx(card_id):
    r = req.get(card_id, {})
    return {
        "approved_role": r.get("approved_role"),
        "composite_roles": r.get("composite_roles"),
        "hand_status": r.get("evidence_card_ref", {}).get("hand_status"),
        "machine_guess": r.get("evidence_card_ref", {}).get("machine_guess"),
    }


topo_groups = collections.defaultdict(lambda: {"variants": set(), "composite_roles_seen": set(), "cards": []})
norm_subtable = []
for r in rows:
    if r["card_id"].rsplit("-L", 1)[0] not in fully_resolvable:
        continue
    c = ctx(r["card_id"])
    raw_idx = r["card_id"].rsplit("-L", 1)[1]
    if "composite_needs_family_contract" in r["reasons"]:
        key = f"{r['family']}-L{raw_idx}"
        g = topo_groups[key]
        g["variants"].add(r["card_id"].rsplit("-L", 1)[0])
        for cr in (c["composite_roles"] or []):
            g["composite_roles_seen"].add(cr)
        g["cards"].append(r["card_id"])
    if "role_name_conflict" in r["reasons"]:
        bare = c["approved_role"]
        suggested = None
        if isinstance(bare, str) and bare in BARE_TO_PATTERN:
            suggested = BARE_TO_PATTERN[bare].format(family=r["family"])
        norm_subtable.append({
            "card_id": r["card_id"],
            "family": r["family"],
            "original_reviewed_role": bare,
            "proposed_normalized_role_FOR_REVIEW": suggested,
            "needs_context_confirmation": suggested is None or (c["composite_roles"] and len(c["composite_roles"]) > 1),
            "context_evidence": c,
        })

topo_out = {k: {"variants": sorted(v["variants"]),
                "composite_roles_seen": sorted(v["composite_roles_seen"]),
                "cards": sorted(v["cards"])}
            for k, v in sorted(topo_groups.items())}

summary = {
    "fully_resolvable_variants": sorted(fully_resolvable),
    "fully_resolvable_count": len(fully_resolvable),
    "topology_group_count": len(topo_out),
    "topology_groups": topo_out,
    "normalization_subtable_count": len(norm_subtable),
    "normalization_needs_context_confirmation": sum(1 for n in norm_subtable if n["needs_context_confirmation"]),
    "normalization_subtable": sorted(norm_subtable, key=lambda n: n["card_id"]),
    "guardrails": [
        "proposed_normalized_role_FOR_REVIEW is a suggestion; confirm each against the layer's actual family/context before use.",
        "Do not apply normalization from label strings alone (user constraint 2026-06-25).",
        "rider/mount/attack/plydie roles stay family-qualified.",
        "Preserve original_reviewed_role on every normalized promoted row (promoter already does this).",
    ],
    "note": "Read-only scoping proposal. No promotion, no evidence edits. authority:false; NOT closure (Law 16).",
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "family_contract_scoping.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({k: summary[k] for k in (
    "fully_resolvable_count", "topology_group_count",
    "normalization_subtable_count", "normalization_needs_context_confirmation")}, indent=2))
print("topology groups:", list(topo_out.keys()))

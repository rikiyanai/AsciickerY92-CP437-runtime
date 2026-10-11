# Ad hoc script: FL-4162 blocked-variant unlock report: per blocked variant, list exact blocking layers with classification + content_status + hand_status, split blockers into HARD (rejected/unowned/hand-reject) vs SOFT (composite_needs_family_contract / non-accept-partial / role_name_conflict), name the required composite contract group(s) per (family,layer), and the full set of resolution actions each variant needs. Does NOT predict unlock counts. Read-only.
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 blocked-variant unlock report (read-only).

Per blocked variant, enumerate every blocked layer with its classification,
content_status, hand_status, and blocker reasons; split blockers into HARD
(structurally unresolvable without overturning a reject / no clean owner) vs
SOFT (resolvable: composite needs a family contract, partial needs re-review,
role-name needs normalization); name the required composite contract group(s).

Does NOT predict which variants become profile_complete -- that is decided by
re-running authorability AFTER contracts/normalization/re-review (reviewer rule).
"""
from __future__ import annotations
import json, collections
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
SM = REPO / "docs/research/ascii/semantic_maps"
OUT = REPO / "docs/research/ascii/verification/fl4162/2026-06-25-blocked-variant-unlock-report"

rep = json.loads((SM / "compiler_authorability_report.json").read_text())
req = {r["requirement_id"].replace("avp_req:", ""): r
       for r in json.loads((SM / "actor_visual_profile_requirements.json").read_text())["requirements"]}

HARD_REASONS = {"unowned_or_unresolved_layer", "rejected_fragment_not_owner"}


def hand_status(card_id):
    return req.get(card_id, {}).get("evidence_card_ref", {}).get("hand_status")


def approved_role(card_id):
    return req.get(card_id, {}).get("approved_role")


by_variant = collections.defaultdict(list)
for l in rep["layers"]:
    if l.get("content_status") == "content_blocked":
        by_variant[l["card_id"].rsplit("-L", 1)[0]].append(l)

variants = {}
for variant, layers in sorted(by_variant.items()):
    family = variant.split("-")[0]
    layer_rows = []
    hard = []
    contract_groups = set()
    rereview = []
    normalize = []
    for l in sorted(layers, key=lambda x: x["card_id"]):
        cid = l["card_id"]
        idx = int(cid.rsplit("-L", 1)[1])
        hs = hand_status(cid)
        reasons = [b.get("reason") for b in (l.get("content_blockers") or [])]
        layer_hard = []
        for r in reasons:
            if r in HARD_REASONS:
                layer_hard.append(r)
            elif r == "proposal_from_non_accept_hand_status":
                if hs == "reject":
                    layer_hard.append("hand_status_reject")
                else:  # partial
                    rereview.append(cid)
            elif r == "composite_needs_family_contract":
                contract_groups.add(f"{family}-L{idx}")
            elif r == "role_name_conflict":
                normalize.append({"card_id": cid, "original_reviewed_role": approved_role(cid)})
        hard.extend(layer_hard)
        layer_rows.append({
            "card_id": cid, "layer_index": idx,
            "classification": l.get("classification"),
            "hand_status": hs, "approved_role": approved_role(cid),
            "blockers": reasons,
            "hard_blockers": layer_hard,
        })
    variants[variant] = {
        "family": family,
        "blocked_layers": layer_rows,
        "has_hard_blocker": bool(hard),
        "hard_blockers": sorted(set(hard)),
        "required_contract_groups": sorted(contract_groups),
        "required_rereview_layers": sorted(set(rereview)),
        "required_normalization_layers": normalize,
    }

# Group-level rollup (how many variants touch each contract group)
group_touch = collections.Counter()
for v in variants.values():
    for g in v["required_contract_groups"]:
        group_touch[g] += 1

summary = {
    "blocked_variant_count": len(variants),
    "variants_with_hard_blocker": sorted(v for v, d in variants.items() if d["has_hard_blocker"]),
    "variants_no_hard_blocker": sorted(v for v, d in variants.items() if not d["has_hard_blocker"]),
    "contract_group_variant_touch": dict(group_touch.most_common()),
    "rule": "A variant can only become profile_complete if it has NO hard blocker. Even then, ALL its soft axes (contract + re-review + normalization) must be cleared. Actual promotability is decided by re-running authorability, not predicted here.",
    "variants": variants,
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "unlock_report.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({
    "blocked_variant_count": summary["blocked_variant_count"],
    "variants_no_hard_blocker_count": len(summary["variants_no_hard_blocker"]),
    "contract_group_variant_touch": summary["contract_group_variant_touch"],
}, indent=2))

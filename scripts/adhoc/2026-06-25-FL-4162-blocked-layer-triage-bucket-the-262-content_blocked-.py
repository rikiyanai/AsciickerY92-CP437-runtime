# Ad hoc script: FL-4162 blocked-layer triage: bucket the 262 content_blocked layers by reviewer priority (role_name_conflict -> unowned/unresolved/rejected -> composite -> non-accept) and by resolvability (normalization / family-contract / re-review / structural); answers whether runtime authority can ever flip by counting resolvable vs structurally-blocked. Read-only.
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 blocked-layer triage (read-only).

Buckets the 262 content_blocked source layers by the reviewer's priority order
and by resolvability, to size the path past the exhausted profile-complete set.

Primary bucket priority (first match wins):
  1 role_name_conflict        2 unowned/unresolved/rejected_fragment
  3 composite_needs_family_contract   4 non_accept_hand_status

Resolvability (needs ALL axes resolvable):
  - hand_status reject                     -> STRUCTURAL (hand-rejected, Law 5)
  - unowned/unresolved/rejected_fragment   -> STRUCTURAL (no clean owner)
  - composite_needs_family_contract        -> RESOLVABLE via family topology contract
  - role_name_conflict & accept            -> RESOLVABLE via promotion-boundary normalization
  - hand_status partial (soft only)        -> RESOLVABLE via re-review to accept
"""
from __future__ import annotations
import json, collections
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
SM = REPO / "docs/research/ascii/semantic_maps"
OUT = REPO / "docs/research/ascii/verification/fl4162/2026-06-25-blocked-layer-triage"

rep = json.loads((SM / "compiler_authorability_report.json").read_text())
req = {r["requirement_id"].replace("avp_req:", ""): r
       for r in json.loads((SM / "actor_visual_profile_requirements.json").read_text())["requirements"]}

blocked = [l for l in rep["layers"] if l.get("content_status") == "content_blocked"]


def reasons(l):
    return {b.get("reason") for b in (l.get("content_blockers") or [])}


def hand_status(card_id):
    return req.get(card_id, {}).get("evidence_card_ref", {}).get("hand_status")


PRIORITY = [
    ("role_name_conflict", lambda rs: "role_name_conflict" in rs),
    ("unowned_unresolved_rejected", lambda rs: bool(rs & {"unowned_or_unresolved_layer", "rejected_fragment_not_owner"})),
    ("composite", lambda rs: "composite_needs_family_contract" in rs),
    ("non_accept", lambda rs: "proposal_from_non_accept_hand_status" in rs),
]


def primary_bucket(rs):
    for name, pred in PRIORITY:
        if pred(rs):
            return name
    return "other"


def resolvability(l):
    rs = reasons(l)
    hs = hand_status(l["card_id"])
    if hs == "reject":
        return "STRUCTURAL_hand_rejected"
    if rs & {"unowned_or_unresolved_layer", "rejected_fragment_not_owner"}:
        return "STRUCTURAL_no_clean_owner"
    if "composite_needs_family_contract" in rs:
        return "RESOLVABLE_family_contract"
    if "role_name_conflict" in rs and hs == "accept":
        return "RESOLVABLE_normalization"
    if hs == "partial":
        return "RESOLVABLE_rereview"
    return "OTHER_review"


prim = collections.Counter()
resolv = collections.Counter()
by_family = collections.Counter()
rows = []
for l in blocked:
    rs = reasons(l)
    pb = primary_bucket(rs)
    rv = resolvability(l)
    prim[pb] += 1
    resolv[rv] += 1
    by_family[l["family"]] += 1
    rows.append({"card_id": l["card_id"], "family": l["family"],
                 "hand_status": hand_status(l["card_id"]),
                 "reasons": sorted(rs), "primary_bucket": pb, "resolvability": rv})

resolvable = sum(v for k, v in resolv.items() if k.startswith("RESOLVABLE"))
structural = sum(v for k, v in resolv.items() if k.startswith("STRUCTURAL"))
other = sum(v for k, v in resolv.items() if k.startswith("OTHER"))

# normalization candidates: role_name_conflict, accept, no composite/unowned overlap
norm_candidates = [r for r in rows
                   if "role_name_conflict" in r["reasons"]
                   and r["hand_status"] == "accept"
                   and not (set(r["reasons"]) & {"composite_needs_family_contract", "unowned_or_unresolved_layer", "rejected_fragment_not_owner"})]

# Realistic step-5 yield: a variant becomes profile_complete after normalization
# ONLY if every one of its currently-blocked layers is normalization-resolvable
# (no composite/reject/partial sibling left over).
blocked_by_variant = collections.defaultdict(list)
for r in rows:
    blocked_by_variant[r["card_id"].rsplit("-L", 1)[0]].append(r)
newly_complete_after_norm = sorted(
    stem for stem, rs in blocked_by_variant.items()
    if rs and all(x["resolvability"] == "RESOLVABLE_normalization" for x in rs)
)

# Genuine promotion gate: a variant can EVER become profile_complete only if none of
# its blocked layers is STRUCTURAL. For those, record the mix of resolution types
# its blocked layers need (so step 5 knows what work unlocks it).
fully_resolvable_variants = {}
structural_gated_variants = []
for stem, rs in blocked_by_variant.items():
    if any(x["resolvability"].startswith("STRUCTURAL") for x in rs):
        structural_gated_variants.append(stem)
    else:
        fully_resolvable_variants[stem] = sorted({x["resolvability"] for x in rs})

summary = {
    "total_blocked": len(blocked),
    "blocked_variants_total": len(blocked_by_variant),
    "fully_resolvable_variants_count": len(fully_resolvable_variants),
    "fully_resolvable_variants": fully_resolvable_variants,
    "structural_gated_variants_count": len(structural_gated_variants),
    "newly_profile_complete_if_normalized_count": len(newly_complete_after_norm),
    "newly_profile_complete_if_normalized": newly_complete_after_norm,
    "primary_bucket_counts": dict(prim),
    "resolvability_counts": dict(resolv),
    "resolvable_total": resolvable,
    "structural_total": structural,
    "other_total": other,
    "by_family": dict(by_family),
    "normalization_candidates_count": len(norm_candidates),
    "normalization_candidate_ids": sorted(r["card_id"] for r in norm_candidates),
    "frame_answer": (
        f"{resolvable} of {len(blocked)} blocked layers are resolvable (normalization/"
        f"family-contract/re-review); {structural} are structurally blocked (hand-reject or "
        f"no clean owner). Runtime authority can only flip if the resolvable set, once cleared, "
        f"makes enough variants profile_complete to fully replace the legacy merge owner."
    ),
    "note": "Read-only triage. authority:false unchanged. NOT closure (Law 16).",
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "triage_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
(OUT / "triage_rows.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))

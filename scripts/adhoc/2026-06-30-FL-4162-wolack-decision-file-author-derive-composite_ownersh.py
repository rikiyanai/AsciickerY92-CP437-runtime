# Ad hoc script: FL-4162 wolack decision-file author: derive composite_ownership_decisions.json (8 wolack L3) + hand_status_reconciliations.json (8 L2 + 7 non-accept L3) ONLY from recorded contract evidence (WOLACK_CONTRACT decisions + packet fingerprints/statuses). Idempotent; preserves existing attack decisions. Authority:false; no state_FINAL edit.
# Created: 2026-06-30
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162: author the wolack decision files from recorded contract evidence.

Derives, never invents: every fingerprint and status comes straight from the
review packet (manual_candidate_review.json); the decision intent comes from the
recorded WOLACK_CONTRACT.json (L2 accept-narrow, wolack-0001-L3 is the composite,
L3 corrected label authoritative). Writes:
  * composite_ownership_decisions.json -- appends 8 wolack L3 composite->owned
    decisions (preserving the existing attack entries);
  * hand_status_reconciliations.json   -- 8 L2 + 7 non-accept L3 reconciliations.
Idempotent: re-running replaces the wolack rows, never duplicates. authority:false;
the hand corpus is never edited.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SM = REPO / "docs/research/ascii/semantic_maps"
PACKET = SM / "manual_candidate_review.json"
COMP = SM / "composite_ownership_decisions.json"
HS = SM / "hand_status_reconciliations.json"
COMPOSITE_ROLES = ["mount_body_wolf", "rider_torso", "sword"]
OWNED_ROLE = "mount_body_wolf;rider_torso;sword"


def _cards():
    rows = json.loads(PACKET.read_text())["reviewed"]
    return {r["card_id"]: r for r in rows if str(r.get("card_id", "")).startswith("wolack-")}


def main():
    cards = _cards()
    l2 = sorted(k for k in cards if k.endswith("-L2"))
    l3 = sorted(k for k in cards if k.endswith("-L3"))

    # --- composite_ownership_decisions.json (append wolack, keep attack) ---
    doc = json.loads(COMP.read_text())
    doc["decisions"] = [d for d in doc["decisions"]
                        if not str(d["source_key"]).startswith("wolack-")]
    for cid in l3:
        r = cards[cid]
        doc["decisions"].append({
            "source_key": cid,
            "whole_atlas_fingerprint": r["whole_atlas_fingerprint"],
            "asserted_original_roles": COMPOSITE_ROLES,
            "owned_role": OWNED_ROLE,
            "hand_status_at_decision": r.get("hand_status"),
            "rationale": ("WOLACK_CONTRACT.json: L3 is the dense mount_body_wolf+rider_torso+sword "
                          "composite (engine L2-base + L3..N fold, sprite.cpp:354-361). "
                          + ("wolack-0001-L3 reaches composite via owned->composite reconciliation "
                             "(false-clean single-role slip)." if cid == "wolack-0001-L3"
                             else "partial/reject hand status is guesser failure; corrected label authoritative.")),
        })
    COMP.write_text(json.dumps(doc, indent=2) + "\n")

    # --- hand_status_reconciliations.json (L2 all + non-accept L3) ---
    recon = []
    for cid in l2:
        r = cards[cid]
        recon.append({
            "source_key": cid,
            "whole_atlas_fingerprint": r["whole_atlas_fingerprint"],
            "asserted_original_status": r.get("hand_status"),
            "reconciled_status": "accept",
            "rationale": ("WOLACK_CONTRACT.json decision (1): L2 is the invariant mount_body_wolf "
                          "base/rear fragment (all 8 byte-identical, 1584/1584); the 'ears' note is "
                          "evidence the wolf is split across layers, not that L2 is invalid."),
        })
    for cid in l3:
        r = cards[cid]
        if r.get("hand_status") == "accept":
            continue
        recon.append({
            "source_key": cid,
            "whole_atlas_fingerprint": r["whole_atlas_fingerprint"],
            "asserted_original_status": r.get("hand_status"),
            "reconciled_status": "accept",
            "rationale": ("WOLACK_CONTRACT.json decision (3): the L3 composite corrected label is "
                          "authoritative; partial/reject reflects the guesser, not the layer."),
        })
    hs_doc = {
        "schema": "hand_status_reconciliations/v1",
        "authority": False,
        "runtime_authoritative": False,
        "is_proposal": True,
        "surface_kind": "hand_status_reconciliations",
        "recorded_at": "2026-06-30",
        "scope": "wolack L2 + L3 composite (FL-4162)",
        "source_packet": "docs/research/ascii/semantic_maps/manual_candidate_review.json",
        "non_authority_boundary": [
            "Reviewed contract-boundary hand-status reconciliations ONLY. authority:false.",
            "Do NOT edit the hand evidence (manual_candidate_review.json) or state_FINAL.",
            "Clears proposal_from_non_accept_hand_status for the matched card at the contract boundary; original status kept as provenance.",
            "Consumed by build_compiler_authorability_report.py only; fail-closed on fingerprint/status drift; not closure (Law 16).",
        ],
        "binding": "Each reconciliation is keyed by source_key and bound to the card's whole_atlas_fingerprint and asserted original hand status.",
        "reconciliations": recon,
    }
    HS.write_text(json.dumps(hs_doc, indent=2) + "\n")
    print(f"composite_ownership_decisions: {len([d for d in doc['decisions'] if str(d['source_key']).startswith('wolack-')])} wolack L3 (total {len(doc['decisions'])})")
    print(f"hand_status_reconciliations: {len(recon)} entries ({len(l2)} L2 + {len(recon)-len(l2)} L3)")


if __name__ == "__main__":
    main()

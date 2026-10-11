# Ad hoc script: FL-4162 wolack mounted-composite layer-structure characterizer: per-variant layer count, per-layer occupancy, final-layer cyan-fg weapon_swoosh detection, and L3 rider-composite density vs thin-overlay classification. Read-only; feeds the wolack-L3 mounted-composite format proposal.
# Created: 2026-06-29
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 wolack mounted-composite layer-structure characterizer (READ-ONLY).

Decodes every wolack-*.xp variant and reports, per layer:
  - occupancy (non-key cells, key glyph == 32)
  - cyan-fg occupancy (fg == CYAN -> weapon_swoosh special-case signal)
  - final-layer swoosh classification (YES/PARTIAL/NO)
  - L3 density vs thin-overlay threshold

Purpose: establish the seam-equivalence-critical facts for the wolack-L3
mounted-composite proposal WITHOUT promoting anything. Two facts drive the
proposal:
  1. The final layer of every wolack variant is a cyan-fg weapon_swoosh
     (same upstream special-case attack-0001 hit; ~13 cells carry body-context
     color, not raw cyan -> the merge fold must reproduce that propagation).
  2. L3 is a DENSE composite (~4800 occupied cells, near full-frame, zero cyan),
     i.e. a rider-on-mount composite (mount_body_wolf + rider_torso + sword),
     NOT a thin equipment overlay. That density is why the authorability report
     blocks every wolack-*-L3 with 4_topology_mismatch / composite_needs_family_contract.

Nothing here writes layer_roles.json. Promotion stays gated on (a) the L3
family-topology contract decision, (b) the L2 proposal_from_non_accept_hand_status
hand-review escalation, and (c) seam equivalence proving byte-identity.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # scripts/adhoc/<this> -> repo root
SPRITES = REPO_ROOT / "assets" / "sprites"
THIN_OVERLAY_MAX = 600  # cells; equipment/swoosh overlays observed <=480, L3 ~4800


def _load_compiler():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cavp", REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def characterize():
    m = _load_compiler()
    CYAN = m.CYAN
    rows = []
    for xp_path in sorted(SPRITES.glob("wolack-[01]*.xp")):
        xp = m._load_xp(xp_path)
        nlayers = len(xp.layers)
        final = nlayers - 1

        def occ(li):
            return sum(1 for c in xp.layers[li].cells if c[0] != 32)

        def cyan(li):
            return sum(1 for c in xp.layers[li].cells if c[0] != 32 and c[1] == CYAN)

        final_occ, final_cyan = occ(final), cyan(final)
        if final_cyan == 0:
            final_class = "NO"
        elif final_cyan == final_occ:
            final_class = "YES"
        else:
            final_class = "PARTIAL"  # body-context cells propagated (the swoosh merge)
        l3_occ = occ(3) if nlayers > 3 else 0
        rows.append({
            "variant": xp_path.stem,
            "layers": nlayers,
            "final_index": final,
            "final_occ": final_occ,
            "final_cyan": final_cyan,
            "final_swoosh": final_class,
            "l3_occ": l3_occ,
            "l3_is_dense": l3_occ > THIN_OVERLAY_MAX,
        })
    return rows


def main():
    rows = characterize()
    hdr = f"{'variant':14} {'L':>2} {'finalL':>6} {'fOcc':>5} {'fCyan':>5} {'swoosh':>7} {'L3occ':>6} {'L3dense':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['variant']:14} {r['layers']:>2} {r['final_index']:>6} "
              f"{r['final_occ']:>5} {r['final_cyan']:>5} {r['final_swoosh']:>7} "
              f"{r['l3_occ']:>6} {str(r['l3_is_dense']):>7}")
    all_final_swoosh = all(r["final_swoosh"] in ("YES", "PARTIAL") for r in rows)
    all_l3_dense = all(r["l3_is_dense"] for r in rows)
    print()
    print(f"every final layer is a weapon_swoosh: {all_final_swoosh}")
    print(f"every L3 is a dense composite (not thin overlay): {all_l3_dense}")


if __name__ == "__main__":
    main()

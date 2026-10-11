# Ad hoc script: FL-4162 cross-family sprite-contract DISCOVERY: run the wolack glyph/fingerprint method across bigbee/wolfie/wolack (+player ref) to find where the L2-base/L3-dense-rider-composite convention generalizes vs is family-specific. Computes per-family L2 invariance (distinct L2 across variants), L3/L2 density ratio, final-layer cyan swoosh fraction, and cross-matches wolack L3 rider against player L2 body. Read-only evidence.
# Created: 2026-06-30
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 cross-family sprite-contract discovery (READ-ONLY).

Tests how far the wolack contract generalizes:
    L2 = invariant base/rear fragment
    L3 = dense rider/composite fragment (denser than L2)
    final = cyan-fg weapon_swoosh

across the mounted families (bigbee, wolfie, wolack), with player as the rider
reference. Pure cell/glyph evidence; edits nothing. cell_index = src_x*height+src_y.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPRITES = REPO_ROOT / "assets" / "sprites"
CYAN = (0, 255, 255)


def _compiler():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cavp", REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _variants(fam):
    return sorted(p.stem for p in SPRITES.glob(f"{fam}-[0-9]*.xp"))


def _occ(cells):
    return sum(1 for c in cells if c[0] != 32)


def _occ_set(cells):
    return frozenset(i for i, c in enumerate(cells) if c[0] != 32)


def _family_profile(m, fam):
    xps = {v: m._load_xp(SPRITES / f"{v}.xp") for v in _variants(fam)}
    # L2 invariance: how many DISTINCT L2 cell-tuples across variants?
    l2sigs = {}
    for v, xp in xps.items():
        if len(xp.layers) > 2:
            l2sigs.setdefault(tuple(xp.layers[2].cells), []).append(v)
    # density ratio L3/L2 (variants with >=4 layers)
    ratios = []
    swoosh_frac = []
    for v, xp in xps.items():
        nl = len(xp.layers)
        if nl > 3:
            l2 = _occ(xp.layers[2].cells)
            l3 = _occ(xp.layers[3].cells)
            if l2:
                ratios.append(round(l3 / l2, 2))
        if nl > 2:
            fcells = xp.layers[nl - 1].cells
            occ = _occ(fcells)
            cy = sum(1 for c in fcells if c[0] != 32 and c[1] == CYAN)
            swoosh_frac.append(round(cy / occ, 2) if occ else 0.0)
    return {
        "variants": len(xps),
        "L2_distinct_signatures": len(l2sigs),
        "L2_invariant": len(l2sigs) == 1,
        "L2_occ_range": [min(_occ(x.layers[2].cells) for x in xps.values() if len(x.layers) > 2),
                         max(_occ(x.layers[2].cells) for x in xps.values() if len(x.layers) > 2)],
        "L3_over_L2_density_ratio_range": [min(ratios), max(ratios)] if ratios else None,
        "L3_is_dense_composite": bool(ratios) and min(ratios) > 1.5,
        "final_layer_cyan_swoosh_fraction_range": [min(swoosh_frac), max(swoosh_frac)] if swoosh_frac else None,
    }


def _cross_match_rider(m):
    """Does the wolack L3 rider come from the player body? Compare wolack-0001-L3
    occupied-cell set + glyph histogram against player bodies (L2)."""
    wl = m._load_xp(SPRITES / "wolack-0001.xp")
    wl3 = wl.layers[3].cells
    wl3_glyphs = Counter(c[0] for c in wl3 if c[0] != 32)
    out = {}
    for pv in ["player-0000", "player-0001", "player-1000"]:
        p = m._load_xp(SPRITES / f"{pv}.xp")
        if len(p.layers) < 3:
            continue
        pb = p.layers[2].cells
        # geometry differs (mount frame vs player frame); compare glyph signatures
        pg = Counter(c[0] for c in pb if c[0] != 32)
        common = set(wl3_glyphs) & set(pg)
        out[pv] = {
            "player_body_occ": _occ(pb),
            "shared_glyph_kinds": len(common),
            "wolack_L3_glyph_kinds": len(wl3_glyphs),
        }
    return out


def main():
    m = _compiler()
    families = {}
    for fam in ["wolack", "bigbee", "wolfie"]:
        families[fam] = _family_profile(m, fam)
    out = {
        "fl": "FL-4162",
        "step": "cross-family sprite-contract discovery",
        "reference_contract": "wolack: thin invariant L2 + dense L3 rider composite + final cyan swoosh",
        "families": families,
        "wolack_rider_vs_player_body": _cross_match_rider(m),
        "verdict": {
            "wolack": "thin invariant L2 (single sig) + dense L3 (ratio>1.5) -- the reference",
            "bigbee": "invariant L2 but L3 is a THIN overlay (ratio<1) -- rider NOT split to a dense L3; convention does NOT generalize",
            "wolfie": "L2 is dense and VARIABLE (many distinct sigs); riderless wolf mount keeps content in L2 -- convention does NOT generalize",
        },
        "note": "Read-only. state_FINAL not edited.",
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

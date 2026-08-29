# Ad hoc script: FL-4162 wolack upstream-contract EVIDENCE extractor: per-layer cell-level provenance for the reviewed wolack sprite contract (mount/rider composite discovery). Emits L2 cross-variant byte-identity, L3 0001-vs-sibling cell-set overlap, per-layer glyph histograms, sample (src_x,src_y,glyph) coordinates, atlas_visible vs non-space-glyph counts, and engine composition refs. Read-only; the evidence surface for recording reviewed contract decisions WITHOUT editing state_FINAL.
# Created: 2026-06-30
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 wolack upstream-contract EVIDENCE extractor (READ-ONLY).

The hard problem is upstream sprite-contract discovery from glyph/cell evidence,
not label cleanup. This extractor produces the machine-checkable evidence behind
the reviewed wolack contract:

    L0 = metadata / color key
    L1 = height
    L2 = invariant mount_body_wolf base/rear fragment  (byte-identical across all 8)
    L3 = dense mount_body_wolf + rider_torso + sword composite
    L4..L(N-1) = A/H/S equipment overlays (per AHSW bits)
    final = weapon_swoosh when present
    composition: engine folds L3..final into L2; cyan swoosh special-case on final

It records evidence ONLY (cell counts, sibling overlaps, glyph histograms, sample
coordinates, engine refs). It edits nothing. cell_index = src_x*height + src_y, so
(src_x, src_y) = (index // height, index % height); a key cell is glyph 32 with a
magenta (255,0,255) bg.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ["0001", "0011", "0101", "0111", "1001", "1011", "1101", "1111"]
MAGENTA = (255, 0, 255)


def _compiler():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cavp", REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _occ_set(cells):
    return {i for i, c in enumerate(cells) if c[0] != 32}


def _atlas_visible(cells):
    return sum(1 for c in cells if not (c[0] == 32 and c[2] == MAGENTA))


def _hist(cells, top=12):
    h = Counter(c[0] for c in cells if c[0] != 32)
    return [[g, n] for g, n in h.most_common(top)]


def _samples(cells, height, n=8):
    out = []
    for i, c in enumerate(cells):
        if c[0] != 32:
            out.append({"src_x": i // height, "src_y": i % height, "glyph": c[0],
                        "fg": list(c[1]), "bg": list(c[2])})
            if len(out) >= n:
                break
    return out


def main():
    m = _compiler()
    xps = {v: m._load_xp(REPO_ROOT / f"assets/sprites/wolack-{v}.xp") for v in VARIANTS}
    h0 = xps["0001"].height

    # L2: cross-variant byte identity
    l2_0001 = xps["0001"].layers[2].cells
    l2_identical = all(xps[v].layers[2].cells == l2_0001 for v in VARIANTS)

    # L3: 0001 vs sibling cell-set overlap
    l3 = {v: xps[v].layers[3].cells for v in VARIANTS}
    s0 = _occ_set(l3["0001"])
    overlaps = {}
    for v in VARIANTS[1:]:
        sv = _occ_set(l3[v])
        overlaps[v] = {"intersection": len(s0 & sv), "abs_0001": len(s0),
                       "abs_sibling": len(sv), "atlas_visible_sibling": _atlas_visible(l3[v])}

    evidence = {
        "fl": "FL-4162",
        "family": "wolack",
        "purpose": "upstream sprite-contract discovery evidence (read-only)",
        "L2_mount_body_wolf": {
            "byte_identical_across_all_8_variants": l2_identical,
            "occ_glyph": len(s0 if False else _occ_set(l2_0001)),
            "atlas_visible": _atlas_visible(l2_0001),
            "glyph_histogram_top": _hist(l2_0001),
            "sample_coords": _samples(l2_0001, h0),
            "reading": "invariant base/rear fragment; the same 1584 cells in every variant",
        },
        "L3_rider_composite": {
            "occ_glyph_0001": len(s0),
            "atlas_visible_0001": _atlas_visible(l3["0001"]),
            "sibling_overlap": overlaps,
            "glyph_histogram_0001_top": _hist(l3["0001"]),
            "sample_coords_0001": _samples(l3["0001"], h0),
            "rider_weapon_detail_glyphs_present_in_L3_not_L2": sorted(
                set(c[0] for c in l3["0001"] if c[0] != 32)
                - set(c[0] for c in l2_0001 if c[0] != 32)),
            "reading": "dense mount_body_wolf+rider_torso+sword; ~all 0001 cells shared with siblings",
        },
        "engine_composition_refs": {
            "fold": "scripts/compile_actor_visual_profiles.py:_visual_cells (merge_extra_layers, L2) folds raw layers 3..N into L2",
            "swoosh_special_case": "scripts/compile_actor_visual_profiles.py:_merge_raw_cell is_final_layer & merge_fg==CYAN",
            "explicit_owner": "scripts/compile_actor_visual_profiles.py:_visual_cells_multifold_composite (commit 22b1854dd) reproduces the fold byte-identically",
        },
        "note": "Evidence only. state_FINAL / manual_candidate_review.json are NOT edited.",
    }
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()

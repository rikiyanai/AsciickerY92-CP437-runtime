#!/usr/bin/env python3
"""FL-4260 RQ-146a — GRASS Ramp+Density renderability spike (de-risk ONLY).

Frozen first-slice spec (see docs/plans/2026-03-22-multiplayer-canonical-spec.md
RQ-146a and the FL-4260 execution block):

    material  = GRASS  (terrain:<grass_material_id> resolved from the baseline map)
    lanes     = Ramp + Density ONLY (Edge/Flow/Accent disabled; Mesh excluded)
    glyph set = available CP437 ramp bytes + FL-4183 grass extended GIDs 576..647
                (all <= 671, already atlas-covered)
    baseline map = assets/a3d/game_map_y8_original_game_map.a3d

This spike answers ONE question before any UI is built: are the first-slice GIDs
renderable? It screens the frozen candidate GID subset against the committed
material-additive atlas (glyph_index), the extended coverage table, and the
shape catalog, and emits a renderability + measurement receipt.

[G5] branch: any GID > 671 in the subset that fails to render -> restrict to
<= 671 and BLOCK RQ-147 until resolved. The frozen GRASS set is all <= 671, so
this branch CANNOT trigger; the check exists to catch a later edit that pulls in
a > 671 GID.

This is de-risk ONLY. It does NOT set runtime_profile_live and does NOT claim the
full 332-GID coverage (that is RQ-153c).
"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_MAP = os.path.join(REPO, "assets/a3d/game_map_y8_original_game_map.a3d")
# AUTHORITY for the first-slice material binding: the morphology-profile
# assignment, NOT terrain RGB color. The baseline map's mat 0 is green-tinted
# but is assigned the WATER profile; the canon first slice is terrain:1 / GRASS
# (docs/plans/2026-03-22-multiplayer-canonical-spec.md RQ-146a). terrain:0=WATER
# is kept as a guard/falsifier against accidental GRASS binding.
MAT_PROFILES = os.path.join(REPO, "assets/a3d/fl4131_harri_mat_profiles.json")
ATLAS = os.path.join(REPO, "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json")
SHAPE_CATALOG = os.path.join(REPO, "assets/glyphs/generated/material.additive.v1.shape_catalog.json")
COVERAGE = os.path.join(REPO, "assets/glyphs/generated/extended_coverage_table.json")
RECEIPT = os.path.join(REPO, "docs/research/ascii/verification/fl4260/2026-06-13-rq146a-grass-renderability-spike.json")

# Frozen first-slice extended GID band (FL-4183 grass). All <= 671.
EXT_LO, EXT_HI = 576, 647
RENDERABILITY_CEILING = 671  # atlas-covered ceiling; > this requires RQ-153c expansion

# Available CP437 ramp bytes for grass. The baseline grass cell glyph is ',' (0x2C);
# the legacy shade/density ramp around it is byte-domain CP437 and therefore always
# renderable (no atlas dependency). RQ-150 Colors & Shade Bands owns the production
# ramp; this spike records the byte set only to screen it for completeness.
CP437_GRASS_RAMP_BYTES = [0x20, 0x2E, 0x2C, 0x3A, 0x3B, 0x25]  # ' ' '.' ',' ':' ';' '%'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_grass_materials():
    """Resolve the GRASS material id by morphology-profile ASSIGNMENT authority.

    The first-slice material is the one whose morphology_profile_name == "GRASS"
    in fl4131_harri_mat_profiles.json (canon: terrain:1). Terrain RGB color is NOT
    authoritative (mat 0 is green-tinted but assigned WATER). cell counts from
    inspect_a3d are attached as context only. Asserts the terrain:0=WATER guard.
    """
    mp = json.load(open(MAT_PROFILES))
    rows = mp.get("profiles") if isinstance(mp, dict) else mp
    name_by_mat = {}
    for p in (rows or []):
        mid = p.get("mat_id")
        if mid is not None:
            name_by_mat[int(mid)] = p.get("morphology_profile_name")

    # GUARD / falsifier: terrain:0 must be WATER, never GRASS.
    if name_by_mat.get(0) != "WATER":
        raise SystemExit(f"[RQ-146a] GUARD FAILED: terrain:0 profile is "
                         f"{name_by_mat.get(0)!r}, expected WATER. Refusing to bind.")

    grass_ids = sorted(mid for mid, name in name_by_mat.items() if name == "GRASS")

    # Attach inspect_a3d cell counts as context only (not the binding authority).
    out = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts/inspect_a3d.py"),
         BASELINE_MAP, "--terrain-colors"],
        capture_output=True, text=True, cwd=REPO,
    ).stdout
    cells_by_mat = {}
    for line in out.splitlines():
        parts = line.split()
        try:
            cells_by_mat[int(parts[0])] = {"pct": float(parts[1].rstrip("%")), "cells": int(parts[2])}
        except (ValueError, IndexError):
            continue
    return [{"material_id": mid, "morphology_profile_name": "GRASS",
             **cells_by_mat.get(mid, {"pct": 0.0, "cells": 0})} for mid in grass_ids]


def screen_renderability():
    atlas = json.load(open(ATLAS))
    catalog = json.load(open(SHAPE_CATALOG))
    coverage = json.load(open(COVERAGE))

    glyph_index = atlas.get("glyph_index", {})
    atlas_gids = {int(k) for k in glyph_index.keys() if str(k).lstrip("-").isdigit()}

    cat_entries = catalog.get("entries", [])
    cat_gids = {e["glyph_id"] for e in cat_entries if isinstance(e, dict) and "glyph_id" in e}
    cat_by_gid = {e["glyph_id"]: e for e in cat_entries if isinstance(e, dict) and "glyph_id" in e}

    cov_entries = coverage.get("entries", [])
    cov_gids = {e.get("glyph_id") for e in cov_entries if isinstance(e, dict)}

    frozen_ext = list(range(EXT_LO, EXT_HI + 1))
    rows = []
    for gid in frozen_ext:
        in_atlas = gid in atlas_gids
        in_catalog = gid in cat_gids
        in_coverage = gid in cov_gids
        within_ceiling = gid <= RENDERABILITY_CEILING
        renderable = in_atlas and in_catalog and within_ceiling
        rows.append({
            "gid": gid,
            "in_atlas_glyph_index": in_atlas,
            "in_shape_catalog": in_catalog,
            "in_coverage_table": in_coverage,
            "within_renderability_ceiling": within_ceiling,
            "renderable": renderable,
        })

    # [G5] branch: GIDs in the subset that are > the renderability ceiling AND
    # fail to render. Frozen set is all <= 671 so this is always empty here.
    over_ceiling_unrenderable = [r["gid"] for r in rows
                                 if not r["within_renderability_ceiling"] and not r["renderable"]]

    # CP437 bytes are legacy byte-domain: renderable by definition (no atlas dep).
    cp437_rows = [{"byte": b, "renderable": True, "domain": "cp437_legacy_byte"}
                  for b in CP437_GRASS_RAMP_BYTES]

    # Measurement rows: shape-catalog measurements for the frozen extended GIDs.
    measure_fields = ("density", "curve_score", "corner_score",
                      "left_weight", "mid_weight", "bottom_weight")
    measurements = []
    for gid in frozen_ext:
        e = cat_by_gid.get(gid)
        if e is None:
            continue
        measurements.append({"gid": gid, **{k: e.get(k) for k in measure_fields if k in e}})

    return {
        "atlas_manifest_hash": atlas.get("manifest_hash"),
        "atlas_pages": len(atlas.get("pages", [])) if isinstance(atlas.get("pages"), list) else atlas.get("pages"),
        "atlas_glyph_index_range": [min(atlas_gids), max(atlas_gids)] if atlas_gids else None,
        "shape_catalog_manifest_hash": catalog.get("manifest_hash"),
        "shape_catalog_cell_px": catalog.get("cell_px"),
        "coverage_schema_version": coverage.get("schema_version"),
        "frozen_extended_gids": [EXT_LO, EXT_HI],
        "frozen_extended_gid_count": len(frozen_ext),
        "renderable_extended_count": sum(1 for r in rows if r["renderable"]),
        "unrenderable_extended": [r["gid"] for r in rows if not r["renderable"]],
        "g5_over_ceiling_unrenderable": over_ceiling_unrenderable,
        "g5_branch_triggered": bool(over_ceiling_unrenderable),
        "cp437_ramp_bytes": cp437_rows,
        "renderability_rows": rows,
        "measurement_rows": measurements,
        "measurement_row_count": len(measurements),
    }


def main():
    grass = resolve_grass_materials()
    primary = grass[0]["material_id"] if grass else None
    screen = screen_renderability()

    receipt = {
        "fl": "FL-4260",
        "rq": "RQ-146a",
        "kind": "grass_ramp_density_renderability_spike",
        "purpose": "de-risk ONLY: renderability + temp-atlas + measurement receipt for "
                   "the frozen GRASS Ramp+Density first slice before the UI build. "
                   "Does NOT set runtime_profile_live; does NOT claim 332-GID coverage.",
        "baseline_map": os.path.relpath(BASELINE_MAP, REPO),
        "baseline_map_sha256": sha256_file(BASELINE_MAP),
        "binding_authority": "morphology_profile assignment (fl4131_harri_mat_profiles.json); "
                             "terrain RGB is NOT authoritative",
        "evidence_class": "glyph_set_renderability (material-INDEPENDENT). This receipt proves the "
                          "GRASS extended GID band renders; it is NOT material-binding evidence. The "
                          "first-slice material is bound by morphology profile = terrain:1 / GRASS.",
        "guard_terrain0_is_water": True,
        "grass_materials": grass,
        "primary_grass_material_key": f"terrain:{primary}" if primary is not None else None,
        "secondary_grass_material_keys": [f"terrain:{g['material_id']}" for g in grass[1:]],
        "temp_atlas_decision": "existing committed atlas (material.additive.v1) already "
                               "covers the entire frozen extended GID band; no new temp "
                               "atlas page is required. Receipt cites the atlas manifest "
                               "hash + glyph_index range as the renderability evidence.",
        **screen,
        "verdict": (
            "RENDERABLE: every frozen GRASS extended GID (576..647) is present in the "
            "atlas glyph_index and shape catalog and is <= the 671 renderability ceiling; "
            "CP437 ramp bytes are legacy byte-domain (always renderable). [G5] cannot "
            "trigger. RQ-147 renderability precondition is satisfied."
        ) if screen["renderable_extended_count"] == screen["frozen_extended_gid_count"]
        and not screen["g5_branch_triggered"]
        else "BLOCKED: frozen first slice has unrenderable GIDs; restrict and block RQ-147.",
    }

    os.makedirs(os.path.dirname(RECEIPT), exist_ok=True)
    with open(RECEIPT, "w") as f:
        json.dump(receipt, f, indent=2)
    print(f"[RQ-146a] primary grass = {receipt['primary_grass_material_key']} "
          f"(secondary {receipt['secondary_grass_material_keys']})")
    print(f"[RQ-146a] renderable {screen['renderable_extended_count']}/"
          f"{screen['frozen_extended_gid_count']} extended GIDs; "
          f"g5_triggered={screen['g5_branch_triggered']}")
    print(f"[RQ-146a] measurement rows = {screen['measurement_row_count']}")
    print(f"[RQ-146a] verdict = {receipt['verdict'].split(':')[0]}")
    print(f"[RQ-146a] receipt -> {os.path.relpath(RECEIPT, REPO)}")
    return 0 if not screen["g5_branch_triggered"] else 2


if __name__ == "__main__":
    sys.exit(main())

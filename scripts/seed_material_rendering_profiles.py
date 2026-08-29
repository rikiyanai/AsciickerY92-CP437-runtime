#!/usr/bin/env python3
"""FL-4260 RQ-147 — seed the Material Rendering Profile data model + assignment sidecar.

CANON (FL-4260 execution block, SCHEMA SKELETON):
  - assets/glyphs/profiles/material_rendering_profiles.v1.json and the per-map
    assignment sidecar are the DURABLE Law-1 owner of PROFILE colors/shade and
    per-map assignment.
  - Generated morphology/profile tables are SEED + COMPILE OUTPUT ONLY.
  - .a3d mat[id].shade stays CP437-mode data (never the PROFILE color owner).
  - Generator reruns must NEVER overwrite operator-authored profile edits: this
    seeder guards on idempotency_marker + seed_provenance and refuses to rewrite
    an existing profile whose idempotency_marker is already set unless --reseed is
    explicitly passed.

This is the RQ-147 data MODEL only: one record represents the FULL Rendering
policy for one material; every RQ-150/151/152/153 field is
reserved now. NO headed proof; first slice is GRASS Ramp+Density only with
Edge/Flow/Accent disabled / blocked.
"""
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAPE_CATALOG = os.path.join(REPO, "assets/glyphs/generated/material.additive.v1.shape_catalog.json")
RQ146A_RECEIPT = os.path.join(REPO, "docs/research/ascii/verification/fl4260/2026-06-13-rq146a-grass-renderability-spike.json")
PROFILES_OUT = os.path.join(REPO, "assets/glyphs/profiles/material_rendering_profiles.v1.json")
SIDECAR_OUT = os.path.join(REPO, "assets/a3d/game_map_y8_original_game_map.material_profile_assignments.v1.json")
BASELINE_MAP = os.path.join(REPO, "assets/a3d/game_map_y8_original_game_map.a3d")

# First-slice frozen GRASS extended band and CP437 ramp bytes (see RQ-146a).
EXT_LO, EXT_HI = 576, 647
CP437_GRASS_RAMP_BYTES = [0x20, 0x2E, 0x2C, 0x3A, 0x3B, 0x25]  # ' ' '.' ',' ':' ';' '%'
# Canonical ramp axis == the live render_resolve elv index
# (engine/render/render_resolve.cpp:495-513) and editor kFl4260RampRowLabel:
#   elv0 fall/lower, elv1 high, elv2 rise, elv3 flat/low.
# Per canon RQ-152 the Ramp lane is these 4 elv rows ONLY. Shade bands are a
# Colors concept (RQ-150 shade_band_thresholds), NOT a ramp sub-axis. The prior
# "flat/gentle/slope/steep" x shade_band 16-cell shape was a non-canonical second
# ramp owner and is removed here so the JSON Law-1 owner matches the editor axis.
ELEVATION_ROWS = ["fall_lower", "high", "rise", "flat_low"]
DENSITY_BUCKETS = ["D0", "D1", "D2", "D3"]
EDGE_FACTS = ["ridge", "valley", "wall", "overhang"]  # [G8] canonical vocab
DIRECTION_LANE_COUNT = 17  # 0..16 (RQ-152/154 direction axis); reserved this slice

# Stable seed identity. idempotency_marker is set ONCE here at first seed; opens
# (later generator runs) must not overwrite operator edits. This is a fixed value,
# not random, so the seed is reproducible.
GRASS_IDEMPOTENCY_MARKER = "fl4260-rq147-grass-tops-0001"

# ── FL-4260 RQ-154 broad material live profiles (added 2026-06-15) ──────────
# The GRASS first slice (terrain:1) is the frozen live thin slice above and
# is NEVER regenerated here. --broad ADDS live profiles for the other non-WATER
# terrain materials present in the baseline map, each seeded from ITS OWN
# renderable (<=671, atlas+catalog-covered) candidate GIDs in the v2 morphology
# profile tables — material-specific differentiation, not GRASS glyphs reused.
# The >671 candidates per material are honestly deferred to the RQ-153c atlas
# page-bake. terrain:0 = WATER stays guarded and is never seeded.
PROFILE_TABLES_V2 = os.path.join(REPO, "assets/glyphs/generated/material.morphology.v2.profile_tables.json")
# material_id -> (morphology_profile_name, display_name, idempotency_marker, cp437 ramp bytes)
BROAD_MATERIALS = {
    2: ("DIRT",   "Dirt",   "fl4260-rq154-dirt-0001",   [0x20, 0x2E, 0x3A, 0x6F, 0x4F, 0x40]),
    3: ("ROCK",   "Rock",   "fl4260-rq154-rock-0001",   [0x20, 0x2E, 0x3D, 0x23, 0x25, 0x40]),
    4: ("SAND",   "Sand",   "fl4260-rq154-sand-0001",   [0x20, 0x2E, 0x2C, 0x3A, 0x73, 0x53]),
    5: ("SNOW",   "Snow",   "fl4260-rq154-snow-0001",   [0x20, 0x2E, 0x2A, 0x2B, 0x23, 0x40]),
    6: ("MUD",    "Mud",    "fl4260-rq154-mud-0001",    [0x20, 0x2E, 0x3A, 0x26, 0x25, 0x40]),
    8: ("GRAVEL", "Gravel", "fl4260-rq154-gravel-0001", [0x20, 0x2E, 0x6F, 0x6F, 0x23, 0x40]),
}


def renderable_candidates_for(name, by_gid):
    """Return this material's v2 candidate GIDs that are renderable now: present in
    the v1 shape catalog (== atlas-covered <=671 set). >671 candidates are dropped
    (RQ-153c follow-on). Deterministic, sorted."""
    pt = json.load(open(PROFILE_TABLES_V2))
    prof = pt.get("profiles", {}).get(name, {})
    acc = []
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("candidate_glyph_ids", "primary_glyph_ids") and isinstance(v, list):
                    acc.extend(g for g in v if isinstance(g, int))
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(prof)
    return sorted({g for g in acc if g in by_gid})


def build_broad_profile(mat_id, name, display_name, idem, cp437_bytes, by_gid, catalog_hash):
    """Generic terrain:N profile seeded from the material's renderable v2 candidates.
    Same Law-1 shape as GRASS (build_grass_profile); Ramp+Density active, the rest
    blocked/disabled per canon first-slice scope."""
    cands = renderable_candidates_for(name, by_gid)
    density_bins = bin_by_density(cands, by_gid)
    src_blob = json.dumps({
        "catalog_manifest_hash": catalog_hash,
        "material": name,
        "renderable_candidates": cands,
        "cp437_ramp_bytes": cp437_bytes,
    }, sort_keys=True).encode()
    source_table_hash = sha256_bytes(src_blob)

    ramp = []
    for ei, row in enumerate(ELEVATION_ROWS):
        ramp.append({
            "elv": ei, "elevation_row": row, "candidates": density_bins[ei],
            "fallback": cp437_bytes[min(len(cp437_bytes) - 1, ei + 1)],
            "reviewed": False, "reject": None,
        })
    density = []
    for bi, bucket in enumerate(DENSITY_BUCKETS):
        density.append({
            "bucket": bucket, "candidates": density_bins[bi],
            "fallback": cp437_bytes[min(len(cp437_bytes) - 1, bi + 1)],
            "reviewed": False, "reject": None,
        })
    direction = [{"lane": lane, "candidates": [], "fallback": 0x2C, "reviewed": False,
                  "reject": "direction lane disabled in first slice (Ramp+Density only)"}
                 for lane in range(DIRECTION_LANE_COUNT)]
    edge = [{"fact": fact, "candidates": [], "reviewed": False,
             "blocked": "no vertical_relation fact producer"} for fact in EDGE_FACTS]
    flow = [{"class": cls, "dir_sensitivity": 0, "candidates": [], "reviewed": False,
             "reject": "flow lane disabled in first slice"}
            for cls in ["wave", "wind", "grass_stroke"]]
    accent = [{"candidates": [], "frequency": 0, "seed": 0, "reviewed": False,
               "reject": "accent lane disabled in first slice"}]
    return {
        "material_id": f"terrain:{mat_id}",
        "source_class": "terrain",
        "display_name": display_name,
        "preset_seed_id": name.lower(),
        "seed_provenance": source_table_hash,
        "idempotency_marker": idem,
        "profile_state": "live",
        "source_table_hash": source_table_hash,
        "review_receipt_id": None,
        "colors": {
            "fg_palette": [], "bg_palette": [], "shade_band_thresholds": [],
            "elevation_rows": {r: {} for r in ELEVATION_ROWS},
            "per_row": [{"fg_strength": 0, "bg_strength": 0, "shade_contrast": 0} for _ in range(4)],
            "fallback_bytes": {f"ramp:{r}": 0x2C for r in ELEVATION_ROWS},
        },
        "glyph_pools": {"glyph_ids": list(dict.fromkeys([*cp437_bytes, *cands]))},
        "role_buckets": {"ramp": ramp, "density": density, "direction": direction,
                         "edge": edge, "flow": flow, "accent": accent},
        "scoring": {"detail_contrast": 0, "tone_contrast": 0, "density_bias": 0, "role_weights": {}},
    }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def load_catalog_measurements():
    cat = json.load(open(SHAPE_CATALOG))
    by_gid = {e["glyph_id"]: e for e in cat.get("entries", [])
              if isinstance(e, dict) and "glyph_id" in e}
    return cat.get("manifest_hash"), by_gid


def bin_by_density(gids, by_gid, nbins=4):
    """Partition GIDs into nbins by shape_catalog density quantiles."""
    scored = [(g, by_gid[g].get("density", 0.0)) for g in gids if g in by_gid]
    scored.sort(key=lambda x: x[1])
    bins = [[] for _ in range(nbins)]
    n = len(scored)
    for i, (g, _d) in enumerate(scored):
        bins[min(nbins - 1, i * nbins // max(1, n))].append(g)
    return bins


def build_grass_profile(catalog_hash, by_gid):
    frozen_ext = list(range(EXT_LO, EXT_HI + 1))
    density_bins = bin_by_density(frozen_ext, by_gid)

    # source_table_hash = hash over (catalog manifest hash + frozen band + ramp bytes)
    src_blob = json.dumps({
        "catalog_manifest_hash": catalog_hash,
        "frozen_extended": [EXT_LO, EXT_HI],
        "cp437_ramp_bytes": CP437_GRASS_RAMP_BYTES,
    }, sort_keys=True).encode()
    source_table_hash = sha256_bytes(src_blob)

    # FL-4260 RQ-152: Ramp lane = the 4 canonical elv rows ONLY. Each row carries
    # an explicit `elv` index so the editor LOAD/SAVE binds ramp[elv].candidates
    # to the in-memory ramp_candidates[elv] with no axis guess. Shade bands are a
    # Colors field, not a ramp sub-axis. fallback is a CP437 byte (Law 6 fail-
    # safe display). Bucket-level reviewed flags start false.
    ramp = []
    for ei, row in enumerate(ELEVATION_ROWS):
        ramp.append({
            "elv": ei,
            "elevation_row": row,
            "candidates": density_bins[ei],
            "fallback": CP437_GRASS_RAMP_BYTES[min(len(CP437_GRASS_RAMP_BYTES) - 1, ei + 1)],
            "reviewed": False,
            "reject": None,
        })

    density = []
    for bi, bucket in enumerate(DENSITY_BUCKETS):
        density.append({
            "bucket": bucket,
            "candidates": density_bins[bi],
            "fallback": CP437_GRASS_RAMP_BYTES[min(len(CP437_GRASS_RAMP_BYTES) - 1, bi + 1)],
            "reviewed": False,
            "reject": None,
        })

    # Direction reserved (17 lanes) but UNused in the first slice (Ramp+Density only).
    direction = [{
        "lane": lane,
        "candidates": [],
        "fallback": 0x2C,  # ','
        "reviewed": False,
        "reject": "direction lane disabled in first slice (Ramp+Density only)",
    } for lane in range(DIRECTION_LANE_COUNT)]

    # Edge blocked: empty-by-data until a vertical_relation fact producer exists
    # (RQ-153b). [G8] canonical vocab ridge/valley/wall/overhang.
    edge = [{
        "fact": fact,
        "candidates": [],
        "reviewed": False,
        "blocked": "no vertical_relation fact producer",
    } for fact in EDGE_FACTS]

    # Flow / Accent disabled with blocked Trace reason (first slice excludes them).
    flow = [{"class": cls, "dir_sensitivity": 0, "candidates": [],
             "reviewed": False, "reject": "flow lane disabled in first slice"}
            for cls in ["wave", "wind", "grass_stroke"]]
    accent = [{"candidates": [], "frequency": 0, "seed": 0,
               "reviewed": False, "reject": "accent lane disabled in first slice"}]

    return {
        # Canon first slice: terrain:1 / GRASS (morphology-profile authority,
        # NOT terrain RGB). terrain:0 is WATER and must never bind GRASS.
        "material_id": "terrain:1",
        "source_class": "terrain",
        "display_name": "Grass Tops",
        "preset_seed_id": "grass_tops",
        "seed_provenance": source_table_hash,
        "idempotency_marker": GRASS_IDEMPOTENCY_MARKER,
        "profile_state": "live",
        "source_table_hash": source_table_hash,
        "review_receipt_id": None,
        "colors": {
            "fg_palette": [],                # RQ-150 owns production palette
            "bg_palette": [],
            "shade_band_thresholds": [],
            "elevation_rows": {r: {} for r in ELEVATION_ROWS},
            "per_row": [{"fg_strength": 0, "bg_strength": 0, "shade_contrast": 0}
                        for _ in range(4)],
            "fallback_bytes": {f"ramp:{r}": 0x2C for r in ELEVATION_ROWS},
        },
        "glyph_pools": {
            "glyph_ids": list(dict.fromkeys([*CP437_GRASS_RAMP_BYTES, *frozen_ext])),
        },
        "role_buckets": {
            "ramp": ramp,
            "density": density,
            "direction": direction,
            "edge": edge,
            "flow": flow,
            "accent": accent,
        },
        "scoring": {                          # RQ-153 reserved; neutral seed defaults
            "detail_contrast": 0,
            "tone_contrast": 0,
            "density_bias": 0,
            "role_weights": {},
        },
    }


def load_existing(path):
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except json.JSONDecodeError:
            return None
    return None


def water_guard():
    """terrain:0 must be WATER and terrain:1 must be GRASS (morphology authority)."""
    mp_path = os.path.join(REPO, "assets/a3d/fl4131_harri_mat_profiles.json")
    mp = json.load(open(mp_path))
    rows = mp.get("profiles") if isinstance(mp, dict) else mp
    name_by_mat = {int(p["mat_id"]): p.get("morphology_profile_name")
                   for p in (rows or []) if p.get("mat_id") is not None}
    if name_by_mat.get(0) != "WATER":
        raise SystemExit(f"[RQ-147] GUARD FAILED: terrain:0 is {name_by_mat.get(0)!r}, expected WATER.")
    if name_by_mat.get(1) != "GRASS":
        raise SystemExit(f"[RQ-147] GUARD FAILED: terrain:1 is {name_by_mat.get(1)!r}, expected GRASS.")


def _sidecar_assignment(profile):
    return {
        "key": profile["material_id"],
        "profile_id": profile["idempotency_marker"],
        "profile_hash": profile["source_table_hash"],
        "review_receipt_id": None,
    }


def main():
    reseed = "--reseed" in sys.argv
    broad = "--broad" in sys.argv
    water_guard()
    catalog_hash, by_gid = load_catalog_measurements()
    grass = build_grass_profile(catalog_hash, by_gid)

    existing = load_existing(PROFILES_OUT)

    if broad:
        # RQ-154 broad material profiles. PRESERVE every existing profile as-is
        # (the live GRASS thin slice is never regenerated) and ADD live profiles
        # for the broad non-WATER materials. A material already present is left
        # untouched unless --reseed is given.
        profiles = list(existing.get("profiles", [])) if existing else []
        by_mid = {p.get("material_id"): p for p in profiles}
        if "terrain:1" not in by_mid:               # ensure GRASS exists
            profiles.append(grass); by_mid["terrain:1"] = grass
        added, skipped = [], []
        for mat_id, (name, disp, idem, cp437) in sorted(BROAD_MATERIALS.items()):
            key = f"terrain:{mat_id}"
            prior = by_mid.get(key)
            if prior is not None:
                if not reseed:
                    skipped.append(f"{key}({prior.get('profile_state')})")
                    continue
                profiles = [p for p in profiles if p.get("material_id") != key]
            prof = build_broad_profile(mat_id, name, disp, idem, cp437, by_gid, catalog_hash)
            profiles.append(prof); added.append(f"{key}={len(prof['glyph_pools']['glyph_ids'])}gids")
        doc = {
            "schema": "material_rendering_profiles.v1",
            "generated_by": "scripts/seed_material_rendering_profiles.py",
            "note": "DURABLE Law-1 owner of PROFILE colors/shade. "
                    "RQ-154 broad live profile set: terrain:1 GRASS is the frozen live thin "
                    "slice; other non-WATER materials seeded from their own "
                    "renderable (<=671) v2 candidates. >671 candidates deferred to RQ-153c.",
            "profiles": profiles,
        }
        with open(PROFILES_OUT, "w") as f:
            json.dump(doc, f, indent=2)
        sidecar = load_existing(SIDECAR_OUT) or {
            "schema": "material_profile_assignments.v1",
            "map_path": os.path.relpath(BASELINE_MAP, REPO),
            "map_hash": sha256_file(BASELINE_MAP),
            "assignments": [],
        }
        keyed = {a.get("key"): a for a in sidecar.get("assignments", [])}
        for p in profiles:
            keyed[p["material_id"]] = keyed.get(p["material_id"]) or _sidecar_assignment(p)
        sidecar["assignments"] = [keyed[k] for k in sorted(keyed)]
        with open(SIDECAR_OUT, "w") as f:
            json.dump(sidecar, f, indent=2)
        print(f"[RQ-154] wrote {os.path.relpath(PROFILES_OUT, REPO)} "
              f"({len(profiles)} profiles)")
        print(f"[RQ-154] added profiles: {added or 'none'}")
        print(f"[RQ-154] skipped: {skipped or 'none'}")
        print(f"[RQ-154] sidecar assignments: {len(sidecar['assignments'])}")
        return 0

    # ── default GRASS-only seed (RQ-147), unchanged ──
    if existing:
        prior = next((p for p in existing.get("profiles", [])
                      if p.get("idempotency_marker") == grass["idempotency_marker"]), None)
        if prior is not None:
            if not reseed:
                print(f"[RQ-147] profile {grass['material_id']} already seeded "
                      f"(idempotency_marker set). Pass --reseed to refresh. No-op.")
                return 0

    doc = {
        "schema": "material_rendering_profiles.v1",
        "generated_by": "scripts/seed_material_rendering_profiles.py",
        "note": "DURABLE Law-1 owner of PROFILE colors/shade. .a3d mat[id].shade stays "
                "CP437 data. Edge/Flow/Accent "
                "blocked/disabled in the first slice; Direction reserved but unused.",
        "profiles": [grass],
    }
    os.makedirs(os.path.dirname(PROFILES_OUT), exist_ok=True)
    with open(PROFILES_OUT, "w") as f:
        json.dump(doc, f, indent=2)

    sidecar = {
        "schema": "material_profile_assignments.v1",
        "map_path": os.path.relpath(BASELINE_MAP, REPO),
        "map_hash": sha256_file(BASELINE_MAP),
        "assignments": [{
            "key": "terrain:1",  # GRASS first slice (morphology authority); terrain:0=WATER guard
            "profile_id": grass["idempotency_marker"],
            "profile_hash": grass["source_table_hash"],
            "review_receipt_id": None,
        }],
    }
    with open(SIDECAR_OUT, "w") as f:
        json.dump(sidecar, f, indent=2)

    print(f"[RQ-147] wrote {os.path.relpath(PROFILES_OUT, REPO)} "
          f"(1 profile, profile_state=live)")
    print(f"[RQ-147] wrote {os.path.relpath(SIDECAR_OUT, REPO)} "
          f"(1 assignment {sidecar['assignments'][0]['key']})")
    print(f"[RQ-147] glyph_pool extended={EXT_LO}..{EXT_HI} ({EXT_HI-EXT_LO+1}), "
          f"cp437 bytes={len(CP437_GRASS_RAMP_BYTES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

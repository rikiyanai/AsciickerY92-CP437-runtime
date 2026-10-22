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
from pathlib import Path as _Path

# Admission — which glyphs the atlas can actually draw — has exactly one
# owner, shared with the review surface so promotion and review can never
# disagree about what is drawable (FL-4482).
sys.path.insert(0, str(_Path(__file__).resolve().parent))
import fl4482_native_admission as ADMISSION  # noqa: E402

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

# ── FL-4512 direction lane: RUNTIME-NATIVE 8-bin axis ────────────────────────
# The live engine resolves the terrain "direction" role from a per-cell
# steepest-descent fact (world-grid gradient argmax) into exactly EIGHT discrete
# compass bins: N, NE, E, SE, S, SW, W, NW. See
#   engine/fl4131_runtime_harri_resolver.cpp:315  Fl4260DirectionFromFactName()
# which maps each `fact` STRING below to the direction index 0..7 and looks the
# candidate pool up at bucket address  mat_id*31 + (15 + dir).
#
# WHY this list (and not the old 17-lane array): the previous seeder wrote 17
# entries of the shape {"lane": N, "candidates": [], "reject": "disabled"} with
# NO `fact` key. The runtime parser keys direction buckets purely by the `fact`
# string, so every one of those 17 lanes decoded to UNKNOWN and was dropped — the
# axis was inert. Worse, regenerating the profile REPLACED any operator-authored
# 8-bin direction pools with those 17 empty lanes (a silent clobber). The list
# below is the exact vocabulary the runtime accepts, in bin order 0..7, so the
# seed axis is both live-consumable AND preservation-safe (see build_direction_axis).
DIRECTION_FACTS = [
    "north",       # dir 0  FL4260_DIRECTION_N
    "north_east",  # dir 1  FL4260_DIRECTION_NE
    "east",        # dir 2  FL4260_DIRECTION_E
    "south_east",  # dir 3  FL4260_DIRECTION_SE
    "south",       # dir 4  FL4260_DIRECTION_S
    "south_west",  # dir 5  FL4260_DIRECTION_SW
    "west",        # dir 6  FL4260_DIRECTION_W
    "north_west",  # dir 7  FL4260_DIRECTION_NW
]

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


def _preserved_direction_bins(prior_profile):
    """Read an existing profile's authored direction candidates, keyed by the
    runtime `fact` name (north … north_west).

    WHY: the seeder has no morphology producer for the direction axis (unlike
    ramp/density, which it bins from shape-catalog density). Direction pools are
    authored live in the editor / by hand. So on any regeneration the ONLY safe
    behavior is to carry the already-authored pools forward unchanged. This
    function extracts them so build_direction_axis can re-emit them verbatim.

    prior_profile: the previous JSON profile dict for this material (or None on a
                   truly first seed). Returns {fact_name: [glyph_id, ...]}; a
                   direction bin with no authored candidates is simply absent.
    """
    out = {}
    if not prior_profile:
        return out
    # role_buckets.direction may be the new 8-bin fact-keyed axis OR the legacy
    # 17-lane array. We only harvest entries that carry a runtime-valid `fact`
    # name AND a non-empty candidate list — the legacy empty lanes contribute
    # nothing, which is exactly the behavior we want (nothing to preserve there).
    for entry in prior_profile.get("role_buckets", {}).get("direction", []):
        fact = entry.get("fact")
        cands = entry.get("candidates")
        if fact in DIRECTION_FACTS and isinstance(cands, list):
            keep = [g for g in cands if isinstance(g, int)]
            if keep:
                out[fact] = keep
    return out


def build_direction_axis(prior_profile):
    """Build the runtime-native 8-bin direction axis for one material.

    Emits exactly 8 entries in bin order 0..7 (N..NW). Each entry is keyed by the
    `fact` string the runtime parser consumes (Fl4260DirectionFromFactName), so
    the axis is live-loadable with no translation. Any candidates already
    authored in `prior_profile` for a given bin are carried forward verbatim;
    bins with nothing authored stay empty (but STILL present and fact-keyed, so a
    later editor SAVE can populate them in place).

    This replaces the old 17-lane disabled array. The old array both (a) never
    loaded (no `fact` key) and (b) clobbered authored pools on regeneration; this
    builder fixes both — it is inert-safe when empty and preservation-safe when
    the material already owns authored direction pools.
    """
    preserved = _preserved_direction_bins(prior_profile)
    axis = []
    for dir_idx, fact in enumerate(DIRECTION_FACTS):
        axis.append({
            "lane": dir_idx,                       # 0..7 == FL4260_DIRECTION_N..NW
            "fact": fact,                          # runtime bucket key (see line ~315 resolver)
            "candidates": preserved.get(fact, []), # carried forward, never clobbered
            "fallback": 0x2C,                      # ',' Law-6 fail-safe display byte
            "reviewed": False,
        })
    return axis


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


def build_broad_profile(mat_id, name, display_name, idem, cp437_bytes, by_gid,
                        catalog_hash, prior_profile=None):
    """Generic terrain:N profile seeded from the material's renderable v2 candidates.
    Same Law-1 shape as GRASS (build_grass_profile); Ramp+Density active, the rest
    blocked/disabled per canon first-slice scope.

    prior_profile: the previous JSON profile for this material (or None). Threaded
    into build_direction_axis so a --reseed carries any authored direction pools
    forward instead of wiping them."""
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
    # Runtime-native 8-bin direction axis; preserves any authored pools in prior.
    direction = build_direction_axis(prior_profile)
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


def build_grass_profile(catalog_hash, by_gid, prior_profile=None):
    # prior_profile: previous terrain:1 JSON (or None). Threaded into
    # build_direction_axis so a --reseed of GRASS keeps its authored direction
    # pools instead of resetting them to empty.
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

    # Runtime-native 8-bin direction axis (N..NW). Authored pools in prior_profile
    # are carried forward; empty bins stay fact-keyed so a later editor SAVE fills
    # them in place. Replaces the old inert-and-clobbering 17-lane array.
    direction = build_direction_axis(prior_profile)

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



# =============================================================================
# FL-4512 — REVIEWED-LANE PROMOTION TRANSACTION
# =============================================================================
# WHAT THIS DOES, IN PLAIN TERMS
# ------------------------------
# There are two separate files at play here.
#
#   1. material.morphology.v2.profile_tables.json — a GENERATED table. An
#      offline tool measured every glyph's shape (where its ink sits: top-heavy,
#      right-heavy, and so on) and worked out which compass direction each glyph
#      "points". A human then reviewed all 544 resulting cells and accepted them.
#
#   2. material_rendering_profiles.v1.json — the AUTHORED profile. This is the
#      durable owner of what the renderer actually uses.
#
# The generated table is evidence. The authored profile is authority. Nothing
# generated may become a runtime owner by itself; it has to be PROMOTED into the
# authored profile in an explicit, recorded transaction. That is this function.
#
# WHY A TRANSACTION RATHER THAN A COPY
# ------------------------------------
# Promotion has to record WHERE the candidates came from and WHICH review
# accepted them, so that months later anyone can ask "why does grass point
# north-east with these glyphs?" and get an answer. Every promoted lane
# therefore carries the source hash and the review receipt alongside its
# candidates.
#
# THE SHAPE MISMATCH THIS FUNCTION RESOLVES
# -----------------------------------------
# The generated table and the runtime disagree about vocabulary, on purpose:
#
#   * The table uses SIXTEEN compass labels (N, NNE, NE, ENE, E, ...) plus a
#     seventeenth, "NONE", for glyphs with no directional bias. That fine
#     resolution is useful for measurement.
#
#   * The runtime has exactly EIGHT direction facts (N, NE, E, SE, S, SW, W, NW)
#     because that is what the steepest-descent producer can report.
#
# Only the eight exact matches are promoted. The eight in-between labels and
# NONE are deliberately dropped: inventing a runtime lane for "north-north-east"
# would be manufacturing a fact the engine never produces.
#
#   * The table also splits every direction across FOUR density cells (D0..D3).
#     Density is a separate, independent coordinate in the runtime — the tonal
#     ramp owns it. A direction lane must not smuggle density in through the
#     back door, so the four cells are COLLAPSED into one candidate list by a
#     deterministic rule (see collapse_reviewed_density_cells).
# =============================================================================

# The eight runtime direction facts, paired with the generated table's label for
# the same bearing. Anything not in this mapping is not promoted.
D8_TABLE_LABEL_BY_FACT = {
    "north": "N",
    "north_east": "NE",
    "east": "E",
    "south_east": "SE",
    "south": "S",
    "south_west": "SW",
    "west": "W",
    "north_west": "NW",
}

# The generated table's finer labels, listed explicitly so the refusal to import
# them is visible in the source rather than implied by omission.
NON_RUNTIME_TABLE_LABELS = (
    "NNE", "ENE", "ESE", "SSE", "SSW", "WSW", "WNW", "NNW", "NONE",
)

# The material each terrain profile draws its morphology from. terrain:0 is
# WATER by morphology authority — see water_guard() above, which fails the build
# if that ever stops being true.
MORPHOLOGY_NAME_BY_MATERIAL = {
    0: "WATER", 1: "GRASS", 2: "DIRT", 3: "ROCK",
    4: "SAND", 5: "SNOW", 6: "MUD", 8: "GRAVEL",
}

# A promoted lane may hold at most this many candidates, matching the compiled
# pool's per-row capacity. Exceeding it would be silently truncated downstream.
PROMOTION_CAPACITY = 32


def validate_morphology_tables(tables, catalog_hash):
    """Refuse to promote from a table that is not fully reviewed and current.

    Promotion copies human-accepted decisions into the runtime's authority file.
    If the table is stale, half-reviewed, or measured against a different glyph
    catalog than the one on disk, those decisions are not the ones a human
    actually made. Every check below is a reason to stop rather than guess.

    Returns the review receipt identity to stamp on each promoted lane.
    """
    problems = []
    if tables.get("schema_version") != "fl4131_material_morphology_profile_tables.v2.review":
        problems.append(f"unexpected schema_version {tables.get('schema_version')!r}")
    if tables.get("review_state") != "all_cells_review_accepted":
        problems.append(f"review_state is {tables.get('review_state')!r}")
    if tables.get("runtime_profile_live") is not True:
        problems.append("runtime_profile_live is not true")

    # The table records the hash of the shape catalog it measured. If the catalog
    # on disk has moved on, those measurements describe different glyphs.
    #
    # The hash must be computed the SAME way the morphology generator computes it
    # — over the canonicalised JSON (sorted keys), not over the raw file bytes.
    # Hashing the bytes instead produces a mismatch on every cosmetic reformat and
    # would make this gate cry wolf.
    with open(SHAPE_CATALOG, "r", encoding="utf-8") as catalog_file:
        catalog_on_disk = json.load(catalog_file)
    on_disk = hashlib.sha256(
        json.dumps(catalog_on_disk, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if tables.get("source_catalog_hash") != on_disk:
        problems.append(
            f"source_catalog_hash {tables.get('source_catalog_hash')} does not match "
            f"the shape catalog on disk {on_disk} — the catalog was regenerated "
            f"after this table was measured, so its reviewed decisions may be "
            f"about glyphs that have changed or no longer exist")

    summary = tables.get("review_summary") or {}
    for field in ("pending_cells", "rejected_cells", "deferred_cells"):
        if summary.get(field):
            problems.append(f"{summary.get(field)} {field} — review is not settled")
    if not summary.get("accepted_cells"):
        problems.append("review_summary reports no accepted cells")
    receipt_hash = summary.get("input_hash")
    if not receipt_hash:
        problems.append("review_summary carries no input_hash to cite")

    if problems:
        raise SystemExit(
            "[FL-4512] PROMOTION REFUSED — the reviewed morphology table is not "
            "promotable:\n  - " + "\n  - ".join(problems))

    return {
        "review_receipt_input_hash": receipt_hash,
        "review_receipts_source": summary.get("receipts_source"),
        "accepted_cells": summary.get("accepted_cells"),
        "source_catalog_hash": tables.get("source_catalog_hash"),
        "source_table": os.path.relpath(PROFILE_TABLES_V2, REPO),
    }


def collapse_reviewed_density_cells(direction_row, admitted):
    """Collapse one direction's four reviewed density cells into one lane.

    A direction in the generated table is split across four density cells
    (D0..D3), each with its own reviewed candidate list. The runtime direction
    lane has no density axis — density is an independent tonal coordinate — so
    the four lists must become one, deterministically, with no human in the loop
    and no dependence on dictionary ordering.

    The ranking rule, in strict priority order:

      1. ACCEPTED PRIMARY MEMBERSHIP FIRST. A glyph the review named as the
         primary winner of any cell outranks one that was merely an alternate.
      2. DENSITY-CELL OCCURRENCE COUNT, DESCENDING. A glyph the review kept in
         all four density cells is a stronger fit for this direction than one
         that only survived in a single cell.
      3. BEST REVIEWED RANK, ASCENDING. Each cell's candidate list is already in
         reviewed order, so a glyph's index is its rank. Its best (lowest) rank
         across the four cells breaks the previous tie.
      4. SUMMED REVIEWED RANK, ASCENDING. Consistently high placement beats one
         lucky cell.
      5. GLYPH ID, ASCENDING. The final tie-break, so the output is total and
         reproducible byte-for-byte.

    Then a hard cap of PROMOTION_CAPACITY entries.

    `admitted` is the set of glyph ids the atlas can actually draw. The
    generated table contains ids beyond the rendered range (it was measured over
    a planned inventory, not the shipped atlas), and promoting one of those
    would produce a lane the compiler rejects outright. They are filtered here.
    """
    occurrences = {}   # glyph id -> how many density cells kept it
    best_rank = {}     # glyph id -> best (lowest) reviewed position seen
    summed_rank = {}   # glyph id -> sum of reviewed positions
    primaries = set()  # glyph ids the review named as a cell's primary winner

    for density_key in ("D0", "D1", "D2", "D3"):
        cell = direction_row.get(density_key)
        if not isinstance(cell, dict):
            continue
        # Only promote from cells the review actually accepted.
        if cell.get("runtime_state") != "review_accepted":
            continue
        for glyph_id in cell.get("primary_glyph_ids") or []:
            if glyph_id in admitted:
                primaries.add(glyph_id)
        for rank, glyph_id in enumerate(cell.get("candidate_glyph_ids") or []):
            if glyph_id not in admitted:
                continue
            occurrences[glyph_id] = occurrences.get(glyph_id, 0) + 1
            summed_rank[glyph_id] = summed_rank.get(glyph_id, 0) + rank
            if glyph_id not in best_rank or rank < best_rank[glyph_id]:
                best_rank[glyph_id] = rank

    ordered = sorted(
        occurrences,
        key=lambda gid: (
            0 if gid in primaries else 1,   # 1. primaries first
            -occurrences[gid],              # 2. more density cells first
            best_rank[gid],                 # 3. best reviewed rank
            summed_rank[gid],               # 4. summed reviewed rank
            gid,                            # 5. glyph id
        ),
    )
    return ordered[:PROMOTION_CAPACITY]


def promote_reviewed_direction_lanes(profiles_doc, tables, admitted, provenance):
    """Write reviewed D8 candidates into every terrain profile's direction lanes.

    Only lanes that are currently EMPTY are filled. A lane a human already
    authored is left exactly as it is — promotion adds evidence-backed defaults,
    it never overwrites authored intent.

    Returns a list of human-readable lines describing what changed.
    """
    changed = []
    for profile in profiles_doc.get("profiles", []):
        material_id = profile.get("material_id", "")
        if not material_id.startswith("terrain:"):
            continue
        material_number = int(material_id.split(":")[1])
        morphology_name = MORPHOLOGY_NAME_BY_MATERIAL.get(material_number)
        table = (tables.get("profiles") or {}).get(morphology_name)
        if not table:
            continue

        lanes = profile.setdefault("role_buckets", {}).get("direction")
        if not isinstance(lanes, list):
            continue
        for lane in lanes:
            fact = lane.get("fact")
            label = D8_TABLE_LABEL_BY_FACT.get(fact)
            if label is None or lane.get("candidates"):
                continue  # not a D8 fact, or already authored — leave it alone
            direction_row = table.get(label)
            if not isinstance(direction_row, dict):
                continue
            promoted = collapse_reviewed_density_cells(direction_row, admitted)
            if not promoted:
                continue
            lane["candidates"] = promoted
            lane["promoted_from"] = dict(provenance, table_label=label,
                                         morphology_profile=morphology_name)
            changed.append(f"  {material_id:<12} direction {fact:<11} <- "
                           f"{morphology_name}/{label}  {len(promoted)} candidates")
    return changed



# CP437 ramp bytes for WATER, following the same light-to-heavy progression the
# other broad materials use: space, then increasingly dense wave/ripple marks.
CP437_WATER_RAMP_BYTES = [0x20, 0x2E, 0x7E, 0x3D, 0x7B, 0x40]


def promote_water_flow_lanes(water_profile, tables, admitted, provenance):
    """Give terrain:0 its eight flow lanes from the reviewed WATER direction rows.

    The flow fact is a compass bearing pointing from a water cell toward the
    nearest shore (engine/render/render_resolve.cpp:445-476). It fires only when
    the cell's material is water, so terrain:0 is the sole profile that can ever
    hold a flow lane — which is why flow authoring belongs here and nowhere else.

    The reviewed WATER direction rows are the right source: they are exactly the
    glyphs a human accepted as pointing each way for water. They are promoted
    into the flow lanes under their own receipt, kept separate from the direction
    receipt so the two promotions can be audited and revoked independently even
    though they share a source table.

    The retired wave/wind/grass_stroke vocabulary is NOT reconstructed. Those
    labels addressed no runtime lane at all; flow is directional or it is
    nothing.
    """
    table = (tables.get("profiles") or {}).get("WATER")
    if not table:
        raise SystemExit("[FL-4512] PROMOTION REFUSED — no reviewed WATER profile table")

    flow_lanes = []
    promoted_count = 0
    for lane_index, fact in enumerate(DIRECTION_FACTS):
        label = D8_TABLE_LABEL_BY_FACT[fact]
        direction_row = table.get(label)
        candidates = (collapse_reviewed_density_cells(direction_row, admitted)
                      if isinstance(direction_row, dict) else [])
        lane = {
            "lane": lane_index,
            "fact": fact,
            "candidates": candidates,
            "fallback": 0x7E,   # '~' Law-6 fail-safe display byte for water
            "reviewed": False,
        }
        if candidates:
            promoted_count += 1
            lane["promoted_from"] = dict(
                provenance,
                table_label=label,
                morphology_profile="WATER",
                promotion_role="flow",
            )
        flow_lanes.append(lane)

    water_profile.setdefault("role_buckets", {})["flow"] = flow_lanes
    return promoted_count


def build_water_profile(by_gid, catalog_hash, prior_profile=None):
    """Build the terrain:0 WATER profile.

    terrain:0 is WATER by morphology authority — water_guard() fails the build if
    that ever stops being true — but until now no authored profile existed for
    it. That absence meant the flow role had no authoring owner anywhere, even
    though the engine produces flow facts every frame along every shoreline.

    The profile is built exactly like the other broad materials so it carries the
    same Law-1 shape; its flow lanes are then populated by
    promote_water_flow_lanes.
    """
    return build_broad_profile(
        0, "WATER", "Water", "fl4512-terrain0-water-0001",
        CP437_WATER_RAMP_BYTES, by_gid, catalog_hash, prior_profile,
    )


def run_promotion_transaction():
    """CLI entry for --promote-reviewed-lanes.

    Promotes reviewed morphology output into the authored profile and creates
    terrain:0 if it does not exist. Never overwrites a lane a human authored.
    Returns a process exit status.
    """
    water_guard()
    catalog_hash, by_gid = load_catalog_measurements()

    tables = json.load(open(PROFILE_TABLES_V2))
    provenance = validate_morphology_tables(tables, catalog_hash)

    # THE ATLAS-DRAWABLE GLYPH SET.
    #
    # Derived from the native atlas registry, which records the real tile
    # bindings, so this answer moves when the atlas moves.
    #
    # It used to filter by a hard-coded id ceiling of 671. That was a freeze
    # from an era when the highest drawn glyph really was that number; the
    # registry now binds 111,040 glyphs, and the stale ceiling was excluding 22
    # currently reviewable glyphs that have real tiles on
    # material.morphology.v2.page16.png. A number frozen into source cannot
    # notice the atlas grew (FL-4482 / FL-4512).
    admitted = ADMISSION.admitted_glyph_ids()

    profiles_doc = load_existing(PROFILES_OUT)
    if not profiles_doc:
        raise SystemExit("[FL-4512] PROMOTION REFUSED — no authored profile file to promote into")

    existing_ids = {p.get("material_id") for p in profiles_doc.get("profiles", [])}
    created_water = False
    if "terrain:0" not in existing_ids:
        water = build_water_profile(by_gid, catalog_hash)
        profiles_doc["profiles"].append(water)
        profiles_doc["profiles"].sort(key=lambda p: (
            p.get("material_id", "").split(":")[0],
            int(p.get("material_id", "x:0").split(":")[1]),
        ))
        created_water = True
    water = next(p for p in profiles_doc["profiles"] if p.get("material_id") == "terrain:0")

    direction_changes = promote_reviewed_direction_lanes(
        profiles_doc, tables, admitted, provenance)
    flow_promoted = promote_water_flow_lanes(water, tables, admitted, provenance)

    with open(PROFILES_OUT, "w") as f:
        json.dump(profiles_doc, f, indent="\t", ensure_ascii=False)
        f.write("\n")

    print("[FL-4512] reviewed-lane promotion transaction")
    print(f"  source table   : {provenance['source_table']}")
    print(f"  catalog hash   : {provenance['source_catalog_hash'][:16]}...")
    print(f"  review receipt : {provenance['review_receipt_input_hash'][:16]}... "
          f"({provenance['accepted_cells']} accepted cells)")
    print(f"  refused labels : {', '.join(NON_RUNTIME_TABLE_LABELS)}")
    if created_water:
        print("  created terrain:0 (WATER) — flow had no authoring owner before this")
    print(f"  flow lanes promoted for terrain:0: {flow_promoted}/8")
    print(f"  direction lanes promoted: {len(direction_changes)}")
    for line in direction_changes:
        print(line)
    return 0


def main():
    if "--promote-reviewed-lanes" in sys.argv:
        return run_promotion_transaction()
    reseed = "--reseed" in sys.argv
    broad = "--broad" in sys.argv
    water_guard()
    catalog_hash, by_gid = load_catalog_measurements()

    # Load the existing profile FIRST so builders can preserve authored direction
    # pools across a regeneration (build_direction_axis reads the prior material).
    existing = load_existing(PROFILES_OUT)
    prior_grass = None
    if existing:
        prior_grass = next((p for p in existing.get("profiles", [])
                            if p.get("material_id") == "terrain:1"), None)
    grass = build_grass_profile(catalog_hash, by_gid, prior_grass)

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
            # prior (the pre-reseed profile for this material) is threaded in so
            # its authored direction pools survive the regeneration.
            prof = build_broad_profile(mat_id, name, disp, idem, cp437, by_gid,
                                       catalog_hash, prior)
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

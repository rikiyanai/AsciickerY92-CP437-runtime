#!/usr/bin/env python3
"""Compile FF's sole runtime vocabulary from the pinned AAHub TRAIN summary.

Reads no archive pages, split members, held-out slugs, images or runtime data.
Authored geometry thresholds/material bindings stay in outline_authoring;
sequence membership, cross-row observations and gap spellings come from TRAIN.
"""
import argparse
import hashlib
import io
import itertools
import json
import math
import re
import unicodedata
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "godot_project/assets/glyphs/true_ff"
# FL-4902: shader owners of the role ids and the run row flags.
ROLES_GLSL = ROOT / "godot_project/render/ascii/composition/true_ff_roles.glslinc"
RUN_SELECT_GLSL = ROOT / "godot_project/render/ascii/composition/true_ff_run_select.glslinc"
SOURCE_COMMIT = "661b2205829e"
SOURCE_SHA = "61b0537df3a37cd08904f9be09bd0d48a5e78e6a7a57ff2fbd412590289f8258"
SOURCE_PATH = "assets/glyphs/corpus/aahub_aa003_train.sjis_combos.v1.json"
SOURCE_REPO = "https://github.com/rikiyanai/unicode-glyph-morphology-explorer"


# FL-4512 G2/G3: the renderer acquires each subject from exactly one existing
# world owner. Effect facts are the producer kinds the compositor already
# receives; clouds come from the FF cloud field and the moon from the
# published celestial moon-phase fact. No subject owns a simulation.
# FL-4917: E11 requires weather collision children, not the identically named
# impact_sample kind used by A22 projectile disappearance. Its table remains
# required without claiming that a live collision-child producer exists.
SUBJECT_SOURCES = {"effect_fact": ("age_over_fade", "world_period"),
                   "weather_collision_child": ("age_over_fade",),
                   "cloud_field": ("static",), "moon": ("moon_phase",)}
EFFECT_KINDS = {"smoke_sample", "vapor_sample", "impact_sample", "explosion_sample",
                "bonfire_flame"}
SHARD_KINDS = {"explosion_sample", "impact_sample"}
# Authored topology/key limits per subject; fact-placed shard counts remain
# source-owned and are not limited by the number of authored part definitions.
MAX_SUBJECT_PARTS = 16
MAX_PART_KEYS = 8
MAX_PART_LIFETIMES = 2
# FL-4512 G4: structure support kinds (true_ff_frame_params.glslinc TFF_KIND_*)
# and material sides (MaterialRule.placement.y, true_ff_material.glslinc).
SUPPORT_KINDS = {1: "silhouette", 2: "crease", 3: "boundary", 4: "shadow"}
SHADOW_SUPPORT_KIND = 4
MATERIAL_SIDES = {"face": 0, "shadowed": 1}
# FL-4512 junctions: arm directions are 8 sectors of the snapped
# representation frame; a junction has three or four distinct arms.
JUNCTION_SECTORS = 8
JUNCTION_ARMS = (3, 4)
WATER_ROLE_NAMES = ("shoreline", "crest", "ripple", "spray", "shore_break",
                    "waterfall_ledge", "waterfall_strand", "waterfall_impact")
WATER_TRAIN_SECTIONS = ("top_glyphs", "idioms", "bigrams_outline", "trigrams_outline")
WATER_REQUIRED_FACTS = {
    "shoreline": {"world_anchored_water_feature", "shore_intersection",
                   "foreground_depth", "projected_extent"},
    "crest": {"world_anchored_water_feature", "crest_direction", "crest_curvature",
              "foreground_depth", "projected_extent"},
    "ripple": {"world_anchored_water_feature", "ripple_anchor", "foreground_depth",
               "projected_extent"},
    "spray": {"world_anchored_water_feature", "crest_or_impact_anchor",
              "spray_extent", "foreground_depth"},
    "shore_break": {"world_anchored_water_feature", "shore_intersection", "break_front",
                    "foreground_depth", "projected_extent"},
    "waterfall_ledge": {"world_anchored_water_feature", "fall_lip", "foreground_depth", "projected_extent"},
    "waterfall_strand": {"world_anchored_water_feature", "fall_strand", "flow_direction",
                         "foreground_depth", "projected_extent"},
    "waterfall_impact": {"world_anchored_water_feature", "impact_region", "foreground_depth", "projected_extent"},
}
WATER_SELECTION_POLICY = {
    "policy": "first_matching_geometry_interval",
    "interval_policy": "lower_inclusive_upper_exclusive",
    "partition_key": "selector_key",
    "tie_policy": "reject_duplicate_selector_key",
    "hold_policy": "none",
    "fallback": "no_candidate_admission",
}
WATER_INPUT_KEYS = {
    "direction": "direction",
    "tangent_angle_deg": "angle",
    "signed_bow_ratio": "bow_ratio",
    "source_extent_px": "extent_px",
    "in_row_height_px": "height_px",
    "bank_side": "bank_side",
    "curvature_shape": "curvature_shape",
    "turn_direction": "turn_direction",
    "ripple_shape": "ripple_shape",
    "anchor_distance_px": "anchor_distance_px",
    "spray_height_px": "spray_height_px",
    "impact_side": "impact_side",
    "foreground_depth": "foreground_depth",
}
WATER_REQUIRED_INPUTS = {
    "shoreline": ("direction", "tangent_angle_deg", "signed_bow_ratio", "source_extent_px",
                   "bank_side", "foreground_depth"),
    "crest": ("direction", "tangent_angle_deg", "signed_bow_ratio", "source_extent_px",
              "curvature_shape", "turn_direction", "foreground_depth"),
    "ripple": ("direction", "tangent_angle_deg", "signed_bow_ratio", "source_extent_px",
               "ripple_shape", "anchor_distance_px", "foreground_depth"),
    "spray": ("direction", "tangent_angle_deg", "source_extent_px", "anchor_distance_px",
              "spray_height_px", "impact_side", "foreground_depth"),
    "shore_break": ("direction", "tangent_angle_deg", "signed_bow_ratio", "source_extent_px", "in_row_height_px", "foreground_depth"),
    "waterfall_ledge": ("direction", "tangent_angle_deg", "source_extent_px", "in_row_height_px", "foreground_depth"),
    "waterfall_strand": ("direction", "tangent_angle_deg", "source_extent_px", "in_row_height_px", "foreground_depth"),
    "waterfall_impact": ("direction", "tangent_angle_deg", "source_extent_px", "in_row_height_px", "foreground_depth"),
}
WATER_DIRECTION_VALUES = {
    "level", "rising", "falling", "shallow_turn",
    "downward_local", "radial_local",
}
WATER_DISCRETE_VALUES = {
    "bank_side": {"lower", "upper", "horizon"},
    "curvature_shape": {"straight", "convex", "convex_turn", "concave", "concave_turn"},
    "turn_direction": {"none", "left", "right"},
    "ripple_shape": {"shallow_turn", "flat_run"},
    "impact_side": {"crest_or_impact"},
}
WATER_INTERVAL_KEYS = {"angle", "bow_ratio", "extent_px", "height_px", "anchor_distance_px", "spray_height_px"}


def scenery_alphanumeric(text):
    """D7 applies to production scenery runs, not only registry drawings.

    Native font membership and TRAIN frequency are not permission to make a
    letter into scenery. Face gaze marks and native semantic UI have separate
    authored owners; they do not enter the contour run table.
    """
    return re.search(r"[A-Za-z0-9]", unicodedata.normalize("NFKC", text)) is not None


def _water_geometry_overlaps(left, right):
    """Return whether two authored geometry keys could select together.

    Numeric intervals use the existing lower-inclusive/upper-exclusive
    convention; distinct discrete keys partition the authored cases.
    """
    for key in set(left) & set(right):
        a, b = left[key], right[key]
        if isinstance(a, list) and isinstance(b, list):
            if a[1] <= b[0] or b[1] <= a[0]:
                return False
        elif a != b:
            return False
    return True


def _unit_rgb(value):
    return (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1
                    for v in value))


def validate_water_role_table(policy, source, glyphs, add):
    """FL-4512: validate and admit surface-water and waterfall authoring.

    The table is typed authoring metadata, not a second vocabulary. Every
    candidate must be named by the policy and present in the pinned TRAIN
    aggregate or carry an explicit operator semantic decision, then be
    admitted through the same mode-qualified `admitted`
    map used by contour and material sequences. Native advances come only
    from the Saitamaar catalog; no corpus count ranks or selects a candidate.
    """
    subjects = policy.get("depiction_grammar", {}).get("subjects", {})
    water = subjects.get("water_surface", {})
    table = water.get("role_table")
    if not isinstance(table, dict):
        raise ValueError("Water authoring lacks its typed role table")
    if table.get("candidate_policy") != (
            "explicit pinned TRAIN candidates or cited operator semantic decisions; preserve role identity and authored order; "
            "never rank by corpus frequency"):
        raise ValueError("Water candidates must name TRAIN evidence or an operator semantic decision")
    if table.get("material_identity") != (
            "Each role is a distinct semantic water material/feature. A candidate is admitted "
            "into the existing mode-qualified vocabulary and does not create a second sequence list."):
        raise ValueError("Water roles must declare the existing vocabulary boundary")
    if table.get("paint_contract") != (
            "flat_glyph_ink_only; no tone fill, dither, gradient or blended water material"):
        raise ValueError("Water roles must use flat glyph paint")

    space = table.get("space_contract")
    if not isinstance(space, dict) or space.get("codepoints") != [32, 12288] \
            or space.get("advance_px") != {"32": 5, "12288": 11} \
            or space.get("max_adjacent_half") != 2 \
            or space.get("no_leading_half") is not True \
            or space.get("no_trailing_fill") is not True:
        raise ValueError("Water roles must use the native real-space contract")
    for codepoint, advance in ((32, 5), (12288, 11)):
        catalog_glyph = glyphs.get(chr(codepoint))
        if catalog_glyph is None or catalog_glyph["advance_px"] != advance \
                or catalog_glyph["has_ink"]:
            raise ValueError(f"Water space metric is not native Saitamaar: U+{codepoint:04X}")
    anti_blanket = table.get("anti_blanket")
    if not isinstance(anti_blanket, dict) \
            or anti_blanket.get("interior") != "predominantly empty" \
            or anti_blanket.get("distribution") != "feature_relative_sparse" \
            or set(anti_blanket.get("forbid", [])) != {
                "parallel_line_blanket", "screen_row_periodic_fill", "terrain_edge_sweep"}:
        raise ValueError("Water roles must retain the sparse, non-periodic material law")

    train_index = {}
    for section in WATER_TRAIN_SECTIONS:
        for row in source.get(section, []):
            text = row.get("g")
            if text:
                train_index.setdefault(text, set()).add(section)
    roles = table.get("roles")
    if not isinstance(roles, list) or [row.get("role") for row in roles] != list(WATER_ROLE_NAMES):
        raise ValueError(f"Water role table order must be {WATER_ROLE_NAMES}")
    compiled = []
    tracks = {track["name"]: track for track in policy["geometry_keyframes"]["tracks"]}
    for role in roles:
        name = role.get("role")
        # Explicit reuse of existing geometry tracks stays inside this role's
        # compiled rows. It is not a shader fallback or a second vocabulary.
        orientation_tracks = role.get("orientation_tracks", [])
        if not isinstance(orientation_tracks, list) \
                or any(not isinstance(track, str) for track in orientation_tracks) \
                or len(set(orientation_tracks)) != len(orientation_tracks) \
                or (orientation_tracks and role.get("mode") != "empty") \
                or any(track not in tracks
                       or "water" not in tracks[track]["languages"]
                       or "bow" in tracks[track] or "bow_ratio" in tracks[track]
                       for track in orientation_tracks):
            raise ValueError(f"Water role has invalid orientation tracks: {name}")
        if role.get("mode") not in ("empty", "sparse_scatter") \
                or not role.get("semantic_material") \
                or role.get("paint") != "flat_glyph_ink" \
                or not isinstance(role.get("placement"), str) \
                or role.get("interior") != "empty":
            raise ValueError(f"Water role lacks typed empty/material identity: {name}")
        required_facts = set(role.get("required_feature_facts", []))
        if not WATER_REQUIRED_FACTS[name] <= required_facts:
            raise ValueError(f"Water role lacks required feature facts: {name}")
        selection = role.get("selection")
        if not isinstance(selection, dict) \
                or any(selection.get(key) != value for key, value in WATER_SELECTION_POLICY.items()):
            raise ValueError(f"Water role lacks deterministic geometry selection policy: {name}")
        required_inputs = selection.get("required_inputs")
        if not isinstance(required_inputs, list) or len(required_inputs) != len(set(required_inputs)) \
                or tuple(required_inputs) != WATER_REQUIRED_INPUTS[name]:
            raise ValueError(f"Water role has unknown geometry inputs: {name}")
        if "foreground_depth" not in role.get("required_feature_facts", []):
            raise ValueError(f"Water role lacks foreground depth input: {name}")
        geometry_keys = {WATER_INPUT_KEYS[key] for key in required_inputs
                         if key != "foreground_depth"}
        candidates = role.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"Water role lacks explicit TRAIN candidates: {name}")
        compiled_candidates = []
        selector_keys = set()
        geometries = []
        for candidate in candidates:
            text = candidate.get("sequence")
            sections = candidate.get("source_sections")
            expected_advance = candidate.get("native_advance_px")
            selector_key = candidate.get("selector_key")
            geometry = candidate.get("geometry")
            decision = candidate.get("operator_decision")
            operator_admission = sections == ["operator_semantic_admission"]
            if not isinstance(text, str) or not text \
                    or not isinstance(sections, list) or not sections \
                    or not isinstance(candidate.get("kind"), str) or not candidate["kind"] \
                    or not isinstance(selector_key, str) or not selector_key \
                    or selector_key in selector_keys \
                    or not isinstance(geometry, dict) \
                    or (not operator_admission and any(section not in WATER_TRAIN_SECTIONS for section in sections)):
                raise ValueError(f"Water candidate identity or source is invalid: {name}")
            if operator_admission:
                if not isinstance(decision, dict) or decision.get("form") != text \
                        or not all(isinstance(decision.get(key), str) and decision[key].strip()
                                   for key in ("ref", "quote")):
                    raise ValueError(f"Water operator admission lacks its exact form and decision: {name}: {text}")
            elif decision is not None:
                raise ValueError(f"Water candidate mixes operator and TRAIN admission: {name}: {text}")
            selector_keys.add(selector_key)
            if set(geometry) != geometry_keys:
                raise ValueError(f"Water candidate geometry keys differ from its declared inputs: {name}: {text}")
            if geometry.get("direction") not in WATER_DIRECTION_VALUES:
                raise ValueError(f"Water candidate has unknown direction key: {name}: {text}")
            if any(key not in geometry or geometry[key] not in values
                   for key, values in WATER_DISCRETE_VALUES.items()
                   if key in geometry_keys):
                raise ValueError(f"Water candidate has unknown shape key: {name}: {text}")
            for key in WATER_INTERVAL_KEYS & set(geometry):
                interval = geometry[key]
                if not isinstance(interval, list) or len(interval) != 2 \
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in interval) or interval[0] >= interval[1]:
                    raise ValueError(f"Water candidate has invalid geometry interval: {name}: {text}")
            if any(_water_geometry_overlaps(geometry, previous) for previous in geometries):
                raise ValueError(f"Water candidate interval ties another selector: {name}: {text}")
            if "height_px" in geometry and not 0 <= geometry["height_px"][0] < geometry["height_px"][1] <= 17:
                raise ValueError(f"Water candidate height leaves its native row: {name}: {text}")
            geometries.append(geometry)
            available_sections = train_index.get(text, set())
            if not operator_admission and not set(sections) <= available_sections:
                raise ValueError(f"Water candidate is not in the pinned TRAIN sections: {name}: {text}")
            if any(char not in glyphs or not glyphs[char]["has_ink"] for char in text):
                raise ValueError(f"Water candidate lacks native Saitamaar ink: {name}: {text}")
            native_advance = sum(glyphs[char]["advance_px"] for char in text)
            if type(expected_advance) is not int or expected_advance != native_advance:
                raise ValueError(f"Water candidate advance is not native: {name}: {text}")
            provenance = {"operator_decision": decision} if operator_admission else {}
            add(text, role["mode"], "water_role_table", {
                "role": name, "semantic_material": role["semantic_material"],
                "train_sections": sorted(available_sections),
                "native_advance_px": native_advance, **provenance})
            compiled_candidates.append({
                "sequence": text,
                "kind": candidate.get("kind"),
                "selector_key": selector_key,
                "geometry": geometry,
                "mode": role["mode"],
                "semantic_material": role["semantic_material"],
                "train_sections": sorted(available_sections),
                "native_advance_px": native_advance,
                **provenance,
            })
        compiled.append({
            "role": name,
            "semantic_material": role["semantic_material"],
            "mode": role["mode"],
            "required_feature_facts": sorted(required_facts),
            "placement": role["placement"],
            "interior": role["interior"],
            "selection": selection,
            "orientation_tracks": orientation_tracks,
            "candidates": compiled_candidates,
        })
    return compiled


def _validate_spellings(label, mode, tiers, birth, key_sets, alive, limit, policy, admitted):
    """One tier/key/admission rule for parts and fact-placed shard marks."""
    if len(tiers) != 4 or any(tiers[t] is not None for t in range(birth)):
        raise ValueError(f"Part exists before birth tier: {label}")
    end = 0.0
    for lo, hi in alive:
        if not (end <= lo < hi <= limit):
            raise ValueError(f"Invalid part lifetime: {label}")
        end = hi
    if not alive or len(alive) > MAX_PART_LIFETIMES:
        raise ValueError(f"Missing part lifetime or too many intervals: {label}")
    sequences = list(tiers[birth:])
    for keys in key_sets:
        if len(keys) > MAX_PART_KEYS:
            raise ValueError(f"Too many directed part keys: {label}")
        previous = -1.0
        for at, spelling in keys:
            if not previous < at < limit or not any(lo <= at < hi for lo, hi in alive):
                raise ValueError(f"Invalid directed part key: {label}")
            previous = at
            sequences.append(spelling)
    for spelling in sequences:
        if not isinstance(spelling, list) or not spelling:
            raise ValueError(f"Part removed by growing tier: {label}")
        if any((mode, text) not in admitted for text in spelling):
            raise ValueError(f"Unadmitted {mode} subject sequence: {label}: {spelling}")
        if sum(len(text) for text in spelling) > policy["budgets"]["horizontal_run_max"]:
            raise ValueError(f"Subject run exceeds native run budget: {label}")


def validate_subject_keyframes(policy, admitted):
    """Validate authored parts against the ONE mode-qualified vocabulary.

    Data admission does not claim renderer connectivity or budget enforcement.
    optional_shares/budget_class retain the operator's allocation requirements;
    they do not implement the deleted frame-wide class cutoff. There is no
    screen fit, history, second glyph list or generated animation here.
    """
    subjects = set()
    claimed_kinds = set()
    shares = policy["budgets"].get("optional_shares", {})
    for subject in policy.get("subject_keyframes", []):
        name = subject["subject"]
        if name in subjects or subject["language"] not in policy["languages"]:
            raise ValueError(f"Duplicate subject or unknown language: {name}")
        subjects.add(name)
        clock = subject["clock"]
        source = subject.get("source")
        if source not in SUBJECT_SOURCES or clock not in SUBJECT_SOURCES[source]:
            raise ValueError(f"Unsupported subject source or clock: {name}")
        kinds = subject.get("dynamic_kinds", [])
        if source == "effect_fact":
            if not kinds or not set(kinds) <= EFFECT_KINDS or set(kinds) & claimed_kinds:
                raise ValueError(f"Effect subject kinds unknown or claimed twice: {name}")
            claimed_kinds |= set(kinds)
        elif kinds:
            raise ValueError(f"Non-effect subject cannot claim effect facts: {name}")
        if subject.get("fact_filter", "flame_body") != "flame_body" or (
                "fact_filter" in subject and kinds != ["bonfire_flame"]):
            raise ValueError(f"Unknown effect fact filter: {name}")
        if subject.get("budget_class") not in shares:
            raise ValueError(f"Subject lacks an optional ink share: {name}")
        if not _unit_rgb(subject.get("ink_rgb")):
            raise ValueError(f"Subject ink colour must be three unit values: {name}")
        if clock == "world_period" and subject.get("phase_identity") != "source_id":
            raise ValueError(f"Periodic subject lacks source identity: {name}")
        if subject["tiers_rows"] != policy["budgets"]["tier_rows"]:
            raise ValueError(f"Subject tier thresholds differ: {name}")
        if len(subject["parts"]) > MAX_SUBJECT_PARTS:
            raise ValueError(f"Subject exceeds the GPU part slots: {name}")
        parts = {part["id"]: part for part in subject["parts"]}
        if len(parts) != len(subject["parts"]):
            raise ValueError(f"Duplicate part identity: {name}")
        collision_children = subject["source"] == "weather_collision_child"
        if collision_children and (len(parts) != 3 or
                {part.get("source_child_ordinal") for part in parts.values()} != {0, 1, 2}):
            raise ValueError(f"Weather contact must bind all three source children: {name}")
        shards = subject.get("shards")
        if shards is not None:
            label = f"{name}/shards"
            birth = shards.get("birth_tier")
            if not set(kinds) <= SHARD_KINDS or shards.get("role") != "sparse" \
                    or shards.get("count") != "source_outer_shards" or "max_count" in shards \
                    or type(birth) is not int or not 0 <= birth <= 3:
                raise ValueError(f"Invalid fact-placed shard marks: {label}")
            _validate_spellings(label, "sparse_scatter", shards["idiom_by_tier"], birth, [],
                                shards["alive"], 1.0, policy, admitted)
        for part in parts.values():
            label = f"{name}/{part['id']}"
            period = part.get("period_seconds", subject.get("period_seconds"))
            limit = period if clock == "world_period" else 1.0
            if clock == "world_period" and (not isinstance(limit, (int, float))
                    or not math.isfinite(limit) or limit <= 0):
                raise ValueError(f"Invalid authored world period: {label}")
            if clock != "world_period" and period is not None:
                raise ValueError(f"Age subject cannot override a world period: {label}")
            if clock == "static" and (part["alive"] != [[0.0, 1.0]] or "keys" in part
                                      or "keys_by_tier" in part):
                raise ValueError(f"Static subject part cannot change: {label}")
            if not isinstance(part.get("requires_surface_contact", False), bool):
                raise ValueError(f"Invalid surface-contact flag: {label}")
            salt = part.get("phase_salt", 0)
            if type(salt) is not int or not 0 <= salt <= 0xffffffff:
                raise ValueError(f"Invalid source-relative phase salt: {label}")
            mode = {"contour": "empty", "sparse": "sparse_scatter"}.get(part["role"])
            if mode is None:
                raise ValueError(f"Effects and sky forbid fill parts: {label}")
            birth = part["birth_tier"]
            if type(birth) is not int or not 0 <= birth <= 3:
                raise ValueError(f"Invalid part birth tier: {label}")
            tiers = part["idiom_by_tier"]
            offset = part["offset_r"]
            if len(offset) != 2 or any(not math.isfinite(v) for v in offset):
                raise ValueError(f"Invalid feature-relative part offset: {label}")
            if "source_child_ordinal" in part:
                if not collision_children or type(part["source_child_ordinal"]) is not int \
                        or offset != [0.0, 0.0] or "attach" in part \
                        or part.get("requires_surface_contact", False):
                    raise ValueError(f"Invalid source-placed collision child: {label}")
            alive = part["alive"]
            tier_keys = part.get("keys_by_tier")
            if tier_keys is not None:
                if "keys" in part or len(tier_keys) != 4 \
                        or any(tier_keys[t] is not None for t in range(birth)) \
                        or any(not tier_keys[t] for t in range(birth, 4)):
                    raise ValueError(f"Invalid tier-directed part keys: {label}")
                key_sets = tier_keys[birth:]
            else:
                key_sets = [part.get("keys", [])]
            _validate_spellings(label, mode, tiers, birth, key_sets, alive, limit,
                                policy, admitted)
            # A sourced child is itself the world anchor. Other sparse recipe
            # marks still require their bounded contour attachment; this is
            # not an exemption for decorative or unsourced scatter.
            if mode == "sparse_scatter" and not collision_children:
                anchor = parts.get(part.get("attach"))
                if anchor is None or anchor["role"] != "contour" \
                        or anchor["birth_tier"] > birth \
                        or not 0 < part.get("max_distance_px", 0) <= 11:
                    raise ValueError(f"Sparse part lacks bounded contour attachment: {label}")
                anchor_limit = anchor.get("period_seconds", subject.get("period_seconds"))
                same_phase = (clock != "world_period" or
                              (anchor_limit == limit and anchor.get("phase_salt", 0) == salt))
                # Independent clocks can only attach to an always-present
                # contour. Comparing seconds from unrelated phases would
                # falsely admit a spark while its contour is absent.
                always = clock == "world_period" and anchor["alive"] == [[0.0, anchor_limit]]
                if not always and (not same_phase or any(
                        not any(a <= lo and hi <= b for a, b in anchor["alive"])
                        for lo, hi in alive)):
                    raise ValueError(f"Sparse part outlives its contour: {label}")


def validate_face_authoring(policy, admitted):
    """FL-4902 lane FA: authored face marks and drapery-fold strokes.

    Every spelling is 1..4 [mode, text] members; each member must be an
    admitted TRAIN entry of its own mode (a face mark mixes contour and
    sparse members, as the archive spells an eye as lid plus pupil ticks).
    The run stays within the native run budget. Feature anchors come from
    per-asset sidecars under res://assets/; this validates their wiring, not
    their geometry (scripts/derive_true_ff_face_anchors.py owns that).
    """
    face = policy.get("face_authoring")
    if face is None:
        return
    budget = policy["budgets"]["horizontal_run_max"]

    def spelling(value, label, optional=False):
        if value is None and optional:
            return
        if not isinstance(value, list) or not 1 <= len(value) <= 4:
            raise ValueError(f"Face spelling must be 1..4 members: {label}")
        for member in value:
            if (not isinstance(member, list) or len(member) != 2
                    or tuple(member) not in admitted):
                raise ValueError(f"Unadmitted face sequence: {label}: {member}")
        if sum(len(text) for _, text in value) > budget:
            raise ValueError(f"Face run exceeds native run budget: {label}")

    if not -1.0 <= float(face["facing_min_cos"]) <= 1.0 or float(face["contact_eye_distances"]) <= 0.0:
        raise ValueError("Invalid face facing or contact bound")
    if len(face.get("ink_rgb", [])) != 3:
        raise ValueError("Face ink must be rgb")
    rows = []
    for tier in face["tiers"]:
        name = tier["name"]
        for key in ("eye_left", "eye_right", "mouth"):
            spelling(tier[key], f"{name}.{key}")
        spelling(tier.get("nose"), f"{name}.nose", optional=True)
        rows.append(int(tier["min_rows"]))
    if not rows or rows != sorted(set(rows)) or rows[0] < 1:
        raise ValueError("Face tiers must grow strictly in min_rows from 1")
    assets = set()
    for asset in face["assets"]:
        key, path = asset["asset_key"], asset["features"]
        if key in assets or not path.startswith("res://assets/") or not path.endswith(".v1.json"):
            raise ValueError(f"Invalid face asset wiring: {key}")
        assets.add(key)
    fold = face["fold"]
    for key in ("steep", "falling", "rising"):
        spelling(fold[key], f"fold.{key}")
    if not 0.0 < float(fold["steep_max_lean"]) < float(fold["shallow_min_lean"]) \
            or int(fold["min_rows"]) < 1 or int(fold["max_rows_per_segment"]) < 1:
        raise ValueError("Invalid fold lean or row bounds")


def validate_sky_authoring(policy, admitted):
    """Validate the operator sky decisions and the authored star catalogue.

    Stars are fixed world directions from this catalogue, never a hash field
    or a twinkle clock. Classes are nested by night_min so dusk only adds
    stars and dawn only removes them.
    """
    sky = policy.get("sky_authoring", {})
    if sky.get("star_twinkle") is not False \
            or sky.get("precipitation_owner") != "existing_weather_sites" \
            or sky.get("moon_requires_phase_fact") is not True:
        raise ValueError("Sky authoring contradicts the operator decisions")
    moon = sky.get("moon_phase", {})
    if moon.get("fact") != "celestial.frame_moon_phase" \
            or not 0.0 <= moon.get("night_min", -1.0) <= 1.0:
        raise ValueError("Moon phase must come from the published celestial fact")
    classes = {}
    for row in sky.get("star_classes", []):
        klass = row.get("class")
        if klass in classes or row.get("mode") not in ("empty", "sparse_scatter") \
                or not row.get("idioms"):
            raise ValueError(f"Invalid star class: {klass}")
        for spelling in row["idioms"]:
            if not spelling or any((row["mode"], text) not in admitted for text in spelling) \
                    or sum(len(text) for text in spelling) > 1:
                raise ValueError(f"Star class spelling is not one admitted mark: {klass}")
        classes[klass] = row
    if sorted(classes) != [1, 2, 3]:
        raise ValueError("Star classes must be 1, 2 and 3")
    if sky.get("star_fields") != ["id", "azimuth_deg", "elevation_deg", "class", "night_min"]:
        raise ValueError("Star catalogue field order changed")
    stars = sky.get("stars", [])
    cap = sky.get("star_cap")
    if type(cap) is not int or not 0 < len(stars) <= cap <= 64:
        raise ValueError("Star catalogue exceeds its cap")
    if not _unit_rgb(sky.get("star_ink_rgb")):
        raise ValueError("Star ink colour must be three unit values")
    ids = set()
    ranges = {1: [], 2: [], 3: []}
    for star_id, azimuth, elevation, klass, night_min in stars:
        if star_id in ids or klass not in classes or not 0.0 <= azimuth < 360.0 \
                or not 0.0 < elevation < 90.0 or not 0.0 < night_min <= 1.0:
            raise ValueError(f"Invalid catalogue star: {star_id}")
        ids.add(star_id)
        ranges[klass].append(night_min)
    if len(ranges[1]) > 6:
        raise ValueError("At most six class-1 stars")
    for low, high in ((1, 2), (2, 3)):
        if ranges[low] and ranges[high] and max(ranges[low]) >= min(ranges[high]):
            raise ValueError(f"Star classes {low} and {high} are not nested by night_min")
    field = sky.get("cloud_field", {})
    radius = field.get("radius_world", [])
    sites = field.get("sites_per_side")
    if not field.get("lattice_world", 0) > 0 or type(sites) is not int or not 4 <= sites <= 96 \
            or not 0.0 < field.get("cover_min", 0.0) < 1.0 \
            or len(radius) != 2 or not 0.0 < radius[0] < radius[1]:
        raise ValueError("Invalid cloud-field subject lattice")


def validate_interior_placement(rule):
    """FL-4512 G4: which structure supports a material rule decorates, and where.

    support_kinds are structure support kinds (TFF_KIND_*): a cast-shadow
    terminator is its own support kind, so a terrain or object rule can never
    decorate it and a shadow rule can decorate nothing else. A shadow rule's
    marks sit on the shadowed receiver texels only, and are sparse marks,
    never a fill (operator decision OPERATOR_DECISIONS_G4_G7_G8_20260928).
    """
    name = rule.get("name")
    kinds = rule.get("support_kinds")
    if not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds) \
            or any(type(kind) is not int or kind not in SUPPORT_KINDS for kind in kinds):
        raise ValueError(f"Material rule lacks valid support kinds: {name}")
    side = rule.get("side")
    if side not in MATERIAL_SIDES:
        raise ValueError(f"Material rule has an unknown side: {name}")
    shadow = SHADOW_SUPPORT_KIND in kinds
    if shadow != (side == "shadowed") or (shadow and kinds != [SHADOW_SUPPORT_KIND]):
        raise ValueError(f"Shadow marks need a shadow-only rule on the shadowed side: {name}")
    if shadow and rule.get("mode") != "sparse_scatter":
        raise ValueError(f"Shadow interiors carry sparse marks only: {name}")


# FL-4902 lane WC: spray is sparse_scatter marks the contour fitter cannot
# draw, so it is never a contour role or a run row. It is the water_spray
# material: its glyph class is the water role table's spray candidates (TRAIN
# admitted by validate_water_role_table) and its rule names the parent roles
# it decorates.
SPRAY_ROLE = "spray"
SPRAY_CLASS = "water_spray"
MATERIAL_EVIDENCE_SECTIONS = ("bands", "idioms", "bigrams_outline", "trigrams_outline")


def compile_material_interiors(policy, admitted, glyphs, shapes, classes, water_role_admissions):
    """FL-4512: one material idiom per authored semantic rule.

    Counts measure native ink, never rank corpus frequency. Membership stays
    in the same mode-qualified TRAIN vocabulary used by the contour table.
    FL-4902 lane WC: a rule that names depiction roles ("roles") decorates only
    contours of those roles: role_mask (MaterialRule.selection.y, bit r =
    TFF_ROLE id r); a rule without roles keeps every role. A sparse_scatter
    water role (spray) is a material, never a parent role.
    """
    ids = _glsl_uint_constants(ROLES_GLSL, "TFF_ROLE_")
    every_role = (1 << ids.pop("TFF_ROLE_COUNT")) - 1
    scatter_roles = {role["role"] for role in water_role_admissions
                     if role["mode"] == "sparse_scatter"}
    classes = dict(classes)
    classes[SPRAY_CLASS] = {char for role in water_role_admissions if role["role"] == SPRAY_ROLE
                            for candidate in role["candidates"] for char in candidate["sequence"]}
    interiors = []
    for rule in policy.get("interior_authoring", []):
        validate_interior_placement(rule)
        role_mask = every_role
        if "roles" in rule:
            names = rule["roles"]
            if not isinstance(names, list) or not names or len(set(names)) != len(names) \
                    or any(not isinstance(name, str) or name in scatter_roles or name == "generic"
                           or "TFF_ROLE_" + name.upper() not in ids for name in names):
                raise ValueError(f"Material rule names an unknown or non-contour role: {rule.get('name')}")
            role_mask = sum(1 << ids["TFF_ROLE_" + name.upper()] for name in names)
        sections = MATERIAL_EVIDENCE_SECTIONS + (
            ("water_role_table",) if rule["class"] == SPRAY_CLASS else ())
        candidates = []
        for (mode, text), evidence in admitted.items():
            if mode != rule["mode"] or len(text) > rule["max_glyphs"]:
                continue
            if scenery_alphanumeric(text):
                continue
            if not set(text) <= classes[rule["class"]]:
                continue
            if rule.get("periodic_only", False) and len(set(text)) != 1:
                continue
            if not any(e["section"] in sections for e in evidence["evidence"]):
                continue
            count = sum(len(shapes[c][0]) for c in text)
            width = sum(glyphs[c]["advance_px"] for c in text)
            candidates.append((abs(count - rule["target_ink_pixels"]),
                               abs(width - rule["target_advance_px"]), text, count, width))
        if not candidates:
            raise ValueError(f"No TRAIN material idiom for {rule['name']}")
        _, _, text, count, width = min(candidates)
        interiors.append({**rule, "sequence": text, "native_ink_pixels": count,
                          "native_advance_px": width, "role_mask": role_mask,
                          "placement": "contour_attached_legal_gap; " + (
                              "shadowed receiver texels of the terminator's entity only"
                              if rule["side"] == "shadowed" else "whole visible source region only")})
    return interiors


def admit_junctions(policy, measured, admit):
    """FL-4512 junctions: validate the authored junction table and admit its
    glyphs into the contour vocabulary from TRAIN evidence.

    Each row names one single native glyph, which must be a TRAIN-measured
    glyph (top_glyphs, with its count as evidence), and a distinct set of three
    or four arm sectors per language. `admit(text, observation)` adds the
    glyph to the empty-mode vocabulary and returns whether it was admitted.
    """
    table = policy["geometry_keyframes"].get("junctions", [])
    names = set()
    claimed = {}
    for row in table:
        name = row.get("name")
        if not name or name in names:
            raise ValueError(f"Duplicate or unnamed junction: {name}")
        names.add(name)
        text = row.get("sequence")
        if not isinstance(text, str) or len(text) != 1 or text not in measured:
            raise ValueError(f"Junction glyph is not one TRAIN-measured glyph: {name}")
        if scenery_alphanumeric(text):
            raise ValueError(f"D7 forbids an alphanumeric scenery junction: {name}")
        arms = row.get("arms")
        if not isinstance(arms, list) or len(arms) not in JUNCTION_ARMS \
                or len(set(arms)) != len(arms) \
                or any(type(arm) is not int or not 0 <= arm < JUNCTION_SECTORS for arm in arms):
            raise ValueError(f"Junction needs three or four distinct arm sectors: {name}")
        languages = row.get("languages")
        if not languages or any(language not in policy["languages"] for language in languages):
            raise ValueError(f"Junction has an unknown language: {name}")
        for language in languages:
            key = (language, frozenset(arms))
            if key in claimed:
                raise ValueError(f"Junction arms claimed twice: {name} and {claimed[key]}")
            claimed[key] = name
        if not admit(text, {"n": measured[text]["n"], "junction": name}):
            raise ValueError(f"Junction glyph lacks native ink or is forbidden: {name}")


def _glsl_uint_constants(path, prefix):
    """`const uint <prefix>NAME=<value>u;` constants of one shader owner."""
    pattern = r"const uint (" + prefix + r"\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)u;"
    return {name: int(value, 0) for name, value in re.findall(pattern, path.read_text())}


# Water candidate geometry keys the run row carries as intervals or flags; every other
# authored key (direction label, curvature_shape, anchor distance, ...) is not a
# tff_select_run input yet and is recorded on the row as unselected.
RUN_ROW_WATER_KEYS = {"angle", "bow_ratio", "extent_px", "bank_side", "height_px"}
RUN_OPEN = 1000000


def _run_row_unreachable(row, earlier, flag):
    """FL-4902 lane WD: whether first-match selection can never reach `row`.

    tff_select_run tests only the row's joint flag, bank and its half-open
    (angle, extent, bow, height) box; authored labels such as curvature_shape
    or direction are not inputs. A row whose box is covered by the union of
    earlier rows of the same role with the same joint and bow-ratio flags (and
    an unconstrained or equal bank) is unreachable: the shadowed crest crown
    turn (⌒ヽ under ⌒) was one. Exact on the grid of interval endpoints.
    """
    keep = flag["TFF_RUN_JOINT"] | flag["TFF_RUN_BOW_RATIO"]
    bank_mask = flag["TFF_RUN_BANK_MASK"] << flag["TFF_RUN_BANK_SHIFT"]
    bank = row["flags"] & bank_mask
    boxes = [(other["lo"], other["hi"]) for other in earlier
             if (other["flags"] & keep) == (row["flags"] & keep)
             and (other["flags"] & bank_mask) in (0, bank)]
    if not boxes:
        return False
    axes = []
    for axis in range(4):
        lo, hi = row["lo"][axis], row["hi"][axis]
        cuts = sorted({lo, hi} | {value for box in boxes for value in (box[0][axis], box[1][axis])
                                  if lo < value < hi})
        axes.append([(a + b) * 0.5 for a, b in zip(cuts, cuts[1:])])
    for point in itertools.product(*axes):
        if not any(all(box[0][axis] <= point[axis] < box[1][axis] for axis in range(4))
                   for box in boxes):
            return False
    return True


def compile_run_table(policy, admitted, glyphs, water_role_admissions, interiors):
    """FL-4902: the ONE run table tff_select_run reads (true_ff_run_select.glslinc).

    Generic rows (role TFF_ROLE_GENERIC) are the authored geometry tracks and
    junctions, one row per language bit, in authored order; tracks with a bow
    interval are joint rows. Role rows are the water role candidates in authored
    order (detail = candidate ordinal); a candidate whose bow interval excludes 0
    can only be a bent joint, so it is a joint row. Explicit orientation tracks
    follow that role's candidates, reusing the geometry-track compiler under
    the same role id rather than falling through to a generic runtime role.
    Body-outline rows duplicate the complete authored hard generic partition
    under BODY_OUTLINE and VEHICLE_BODY so semantic role selection cannot
    turn those connected contours into an empty non-fallback span.
    Limb rows (FL-4902 lane SB)
    follow: the authored straight tracks and joint rows of
    limb_stroke_authoring under TFF_ROLE_LIMB_STROKE. Vehicle part rows (FL-4902
    lane VP) follow: per vehicle_part_authoring role, the generic straight
    tracks, connected turn/arc joint rows and junction copies. Every primary and substitute
    sequence must be in the mode-qualified TRAIN admission map `admitted`; there
    is no second vocabulary. Decorations name the material rules of the row's
    language; a water role row carries only the rules that name its role
    (FL-4902 lane WC: water_spray on crest rows). A sparse_scatter water role
    (spray) emits no row: its idioms cannot pass the contour fitter.
    Tree rows (FL-4902 lane TB): for each role of tree_authoring (trunk,
    branch, canopy, tuft), its own straight tracks, the named generic straight
    tracks, the authored role joints and the named generic junctions (fork
    glyphs), all under that role's id and with the language's decorations.
    Relief rows (FL-4902 lane R): for each role of relief_authoring (skyline,
    ridge, gully), the named directional tracks, generic straight tracks and
    generic junctions are emitted under the world-fact role id. Both lanes
    still use this function's one TRAIN admission map and one row vocabulary.
    """
    roles = _glsl_uint_constants(ROLES_GLSL, "TFF_ROLE_")
    flag = _glsl_uint_constants(RUN_SELECT_GLSL, "TFF_RUN_")
    banks = _glsl_uint_constants(ROLES_GLSL, "TFF_BANK_")
    languages = policy["languages"]
    keyframes = policy["geometry_keyframes"]
    split = float(keyframes["short_extent_max_px"])
    rows = []

    def advance(text):
        return sum(glyphs[char]["advance_px"] for char in text)

    def decorations(language):
        return [rule["name"] for rule in interiors if language in rule["languages"]]

    def role_decorations(language, role_name):
        return [rule["name"] for rule in interiors
                if language in rule["languages"] and role_name in rule.get("roles", [])]

    def emit(row):
        for text in (row["sequence"], row["substitute"]):
            if scenery_alphanumeric(text):
                raise ValueError(f"D7 forbids an alphanumeric scenery run: {row['track']}: {text}")
            if text and (row["mode"], text) not in admitted:
                raise ValueError(f"Run row sequence not admitted by TRAIN: {row['track']}: {text}")
        if row["language"] not in languages:
            raise ValueError(f"Run row has an unknown language: {row['track']}")
        rows.append(row)

    def track_rows(track, language, role, role_id, row_decorations, candidate_ordinal=None):
        flags = 0
        if "bow" in track or "bow_ratio" in track:
            flags |= flag["TFF_RUN_JOINT"]
        if "bow_ratio" in track:
            flags |= flag["TFF_RUN_BOW_RATIO"]
        bow = track.get("bow_ratio", track.get("bow", [-RUN_OPEN, RUN_OPEN]))
        phase = max(advance(text) for height in track["heights"] for text in height[1:])
        height_lo = 0.0
        for height in track["heights"]:
            for detail in range(2):
                extent_lo = max(0.0 if detail == 0 else split, float(track.get("min_extent_px", 0.0)))
                extent_hi = split if detail == 0 else float(RUN_OPEN)
                if extent_lo >= extent_hi:
                    continue
                sequence, other = height[detail + 1], height[2 - detail]
                emit({"role": role, "role_id": role_id,
                      "language": language, "track": track["name"], "flags": flags,
                      "detail": detail if candidate_ordinal is None else candidate_ordinal,
                      "mode": "empty", "sequence": sequence,
                      "substitute": other if other != sequence else "",
                      "lo": [float(track["angle"][0]), extent_lo, float(bow[0]), height_lo],
                      "hi": [float(track["angle"][1]), extent_hi, float(bow[1]), float(height[0])],
                      "phase_advance_px": phase, "decorations": row_decorations})
            height_lo = float(height[0])

    def straight_track(name, language, tracks):
        track = tracks.get(name)
        if track is None or "bow" in track or "bow_ratio" in track \
                or language not in track["languages"]:
            raise ValueError(f"Role straight track is not a straight generic track: {name}")
        return track

    def joint_row(role, role_id, language, ordinal, joint, row_decorations):
        # FL-4512: an already-authored near-straight joint uses the same
        # native height ladder as its stroke. Its role/angle/bow still select
        # the joint; pixel fitting never chooses the representation.
        if "height_track" in joint:
            if "sequence" in joint:
                raise ValueError(f"Joint has competing sequence and height track: {joint['name']}")
            tracks = {track["name"]: track for track in keyframes["tracks"]}
            track = straight_track(joint["height_track"], language, tracks)
            if track["angle"] != joint["angle"]:
                raise ValueError(f"Joint height track angle differs: {joint['name']}")
            variant = {"short": 1, "long": 2}.get(joint.get("height_variant"))
            if variant is None:
                raise ValueError(f"Joint height track needs an authored width variant: {joint['name']}")
            # Keep one native width/ink family over the vertical ladder. The
            # former all-height joint used this same width at every extent.
            heights = [[height[0], height[variant], height[variant]] for height in track["heights"]]
            thresholds = track.get("joint_height_thresholds_px")
            if thresholds is not None:
                if (len(thresholds) != len(heights) or thresholds[-1] != 17
                        or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                               for value in thresholds)
                        or any(a >= b for a, b in zip([0] + thresholds, thresholds))):
                    raise ValueError(f"Invalid joint height thresholds: {track['name']}")
                heights = [[threshold, *height[1:]] for threshold, height in zip(thresholds, heights)]
            track_rows({**track, "name": joint["name"], "bow_ratio": joint["bow_ratio"], "heights": heights},
                       language, role, role_id, row_decorations, candidate_ordinal=ordinal)
            return
        bow_key = "bow_ratio" if "bow_ratio" in joint else "bow"
        bow = joint[bow_key]
        # Validate admission before looking up native metrics, including for
        # uncatalogued characters; all joint consumers use this one owner.
        if ("empty", joint["sequence"]) not in admitted:
            raise ValueError(f"Run row sequence not admitted by TRAIN: {joint['name']}: {joint['sequence']}")
        joint_flags = flag["TFF_RUN_JOINT"]
        if bow_key == "bow_ratio":
            joint_flags |= flag["TFF_RUN_BOW_RATIO"]
        emit({"role": role, "role_id": role_id, "language": language,
              "track": joint["name"],
              "flags": joint_flags,
              "detail": ordinal, "mode": "empty", "sequence": joint["sequence"],
              "substitute": "",
              "lo": [float(joint["angle"][0]), 0.0, float(bow[0]), 0.0],
              "hi": [float(joint["angle"][1]), float(RUN_OPEN), float(bow[1]), 17.0],
              "phase_advance_px": advance(joint["sequence"]), "decorations": row_decorations})

    for track in keyframes["tracks"]:
        for language in track["languages"]:
            track_rows(track, language, "generic", roles["TFF_ROLE_GENERIC"], decorations(language))
    for junction in keyframes.get("junctions", []):
        sectors = sum(1 << int(arm) for arm in junction["arms"])
        for language in junction["languages"]:
            emit({"role": "generic", "role_id": roles["TFF_ROLE_GENERIC"],
                  "language": language, "track": junction["name"],
                  "flags": flag["TFF_RUN_JUNCTION"], "detail": 0, "mode": "empty",
                  "sequence": junction["sequence"], "substitute": "",
                  "lo": [float(sectors), 0.0, 0.0, 0.0], "hi": [0.0, 0.0, 0.0, 0.0],
                  "phase_advance_px": 0, "decorations": decorations(language)})
    bodies = policy.get("body_outline_authoring")
    if bodies is not None:
        body_roles = bodies.get("roles", [])
        if body_roles != ["body_outline", "vehicle_body"]:
            raise ValueError(f"Body outline authoring must own body_outline and vehicle_body: {body_roles}")
        generic_rows = list(rows)
        for role in body_roles:
            role_name = "TFF_ROLE_" + role.upper()
            if role_name not in roles:
                raise ValueError(f"Body outline role has no shader role id: {role}")
            for language in bodies.get("languages", []):
                if language not in languages:
                    raise ValueError(f"Body outline has an unknown language: {language}")
                for row in generic_rows:
                    if row["role"] == "generic" and row["language"] == language:
                        emit({**row, "role": role, "role_id": roles[role_name], "decorations": []})
    for role in water_role_admissions:
        first_row = len(rows)
        if role["mode"] == "sparse_scatter":
            if role.get("orientation_tracks"):
                raise ValueError("Water scatter cannot own contour orientation tracks")
            continue
        role_name = "TFF_ROLE_" + role["role"].upper()
        if role_name not in roles:
            raise ValueError(f"Water role has no shader role id: {role['role']}")
        for ordinal, candidate in enumerate(role["candidates"]):
            geometry = candidate.get("geometry")
            if not isinstance(geometry, dict) or "angle" not in geometry or "extent_px" not in geometry:
                raise ValueError(f"Water run row lacks authored geometry: {role['role']}: {candidate['sequence']}")
            flags = 0
            if "bank_side" in geometry:
                bank = "TFF_BANK_" + geometry["bank_side"].upper()
                if bank not in banks or banks[bank] == 0:
                    raise ValueError(f"Water run row has unknown bank side: {geometry['bank_side']}")
                flags |= banks[bank] << flag["TFF_RUN_BANK_SHIFT"]
            bow = [-RUN_OPEN, RUN_OPEN]
            height = geometry.get("height_px", [0.0, 17.0])
            if "bow_ratio" in geometry:
                bow = geometry["bow_ratio"]
                flags |= flag["TFF_RUN_BOW_RATIO"]
                if not bow[0] <= 0.0 < bow[1]:
                    flags |= flag["TFF_RUN_JOINT"]
            emit({"role": role["role"], "role_id": roles[role_name], "language": "water",
                  "track": candidate["selector_key"], "flags": flags, "detail": ordinal,
                  "mode": candidate["mode"], "sequence": candidate["sequence"], "substitute": "",
                  "lo": [float(geometry["angle"][0]), float(geometry["extent_px"][0]),
                         float(bow[0]), float(height[0])],
                  "hi": [float(geometry["angle"][1]), float(geometry["extent_px"][1]),
                         float(bow[1]), float(height[1])],
                  "phase_advance_px": advance(candidate["sequence"]),
                  "decorations": role_decorations("water", role["role"]),
                  "unselected_inputs": {key: value for key, value in sorted(geometry.items())
                                        if key not in RUN_ROW_WATER_KEYS}})
            # Every authored candidate must be selectable. Orientation tracks
            # follow the candidates on purpose and stay partly shadowed.
            if _run_row_unreachable(rows[-1], rows[first_row:-1], flag):
                raise ValueError(f"Water candidate row is shadowed by earlier candidates: "
                                 f"{role['role']}: {candidate['selector_key']}")
        for name in role.get("orientation_tracks", []):
            track = next((t for t in keyframes["tracks"] if t["name"] == name), None)
            if track is None or "water" not in track["languages"] \
                    or "bow" in track or "bow_ratio" in track:
                raise ValueError(f"Water orientation track is not a water straight track: {name}")
            track_rows(track, "water", role["role"], roles[role_name],
                       role_decorations("water", role["role"]))
    # FL-4902 lane SB (OPERATOR_DECISION_SPIDER_BIRD_STICK_MOTIF_20260928): a
    # limb is one stroke along its axis. Its rows are the generic straight
    # tracks (one centred stroke glyph per row) and authored joint rows that
    # turn a knee or a wing at one glyph. No corner, shoulder or heel track:
    # those are outline idioms. Limb interiors stay empty (no decorations).
    limbs = policy.get("limb_stroke_authoring")
    if limbs is not None:
        role_name = "TFF_ROLE_" + limbs["role"].upper()
        if role_name not in roles:
            raise ValueError(f"Limb role has no shader role id: {limbs['role']}")
        tracks = {track["name"]: track for track in keyframes["tracks"]}
        for language in limbs["languages"]:
            for name in limbs["straight_tracks"]:
                track_rows(straight_track(name, language, tracks), language, limbs["role"],
                           roles[role_name], [])
            for ordinal, joint in enumerate(limbs["joints"]):
                joint_row(limbs["role"], roles[role_name], language, ordinal, joint, [])
    # FL-4902 lane TB (TREE_BIT_AND_VEHICLE_PARTS_BEGIN_20260929): retained
    # tree bark (material 240 with the tree bit) is TRUNK or BRANCH by its
    # world direction (true_ff_role_vegetation.glslinc). A trunk side is one
    # thin bar per row, never a post bar or box corner; a branch is one stroke
    # per row. Both bend at bracket joints and fork at the named junction
    # glyphs. A role with rows never falls back, so the straight tracks cover
    # every angle. Decorations stay the language's rules, as on generic rows.
    trees = policy.get("tree_authoring")
    if trees is not None:
        tracks = {track["name"]: track for track in keyframes["tracks"]}
        junctions = {row["name"]: row for row in keyframes.get("junctions", [])}
        names = [block.get("role") for block in trees["roles"]]
        expected = {"branch", "canopy", "trunk", "tuft"}
        if (len(names) != len(set(names)) or set(names) != expected):
            raise ValueError(f"Tree authoring must author trunk, branch, canopy and tuft once: {names}")
        for block in trees["roles"]:
            role_id = roles["TFF_ROLE_" + block["role"].upper()]
            block_languages = block.get("languages", trees["languages"])
            block_joints = block.get("joints", trees["joints"])
            block_junctions = block.get("junctions", trees["junctions"])
            for language in block_languages:
                row_decorations = decorations(language)
                for authored_track in block["tracks"]:
                    if isinstance(authored_track, str):
                        track = tracks.get(authored_track)
                        if track is None or language not in track.get("languages", []):
                            raise ValueError(f"Tree track is not a {language} geometry track: {authored_track}")
                    else:
                        track = authored_track
                        if "bow" in track or "bow_ratio" in track or not track.get("heights"):
                            raise ValueError(f"Tree track is not a straight authored track: {track.get('name')}")
                    track_rows(track, language, block["role"], role_id, row_decorations)
                for name in block["straight_tracks"]:
                    track_rows(straight_track(name, language, tracks), language, block["role"],
                               role_id, row_decorations)
                for ordinal, joint in enumerate(block_joints):
                    joint_row(block["role"], role_id, language, ordinal, joint, row_decorations)
                for name in block_junctions:
                    junction = junctions.get(name)
                    if junction is None or language not in junction["languages"]:
                        raise ValueError(f"Tree junction is not a generic junction of {language}: {name}")
                    emit({"role": block["role"], "role_id": role_id, "language": language,
                          "track": junction["name"], "flags": flag["TFF_RUN_JUNCTION"],
                          "detail": 0, "mode": "empty", "sequence": junction["sequence"],
                          "substitute": "",
                          "lo": [float(sum(1 << int(arm) for arm in junction["arms"])), 0.0, 0.0, 0.0],
                          "hi": [0.0, 0.0, 0.0, 0.0], "phase_advance_px": 0,
                          "decorations": row_decorations})
    # FL-4902 lane R (terrain relief): the world producer has already made
    # the semantic decision from terrain source kind and world relief sign.
    # SKYLINE, RIDGE and GULLY therefore receive separate authored rows. A
    # skyline may use connected shoulders/heels; a ridge only convex shoulders
    # and a gully only concave heels. Generic straight tracks close the angle
    # partition without raster fitting or a role-wide fallback.
    relief = policy.get("relief_authoring")
    if relief is not None:
        tracks = {track["name"]: track for track in keyframes["tracks"]}
        junctions = {row["name"]: row for row in keyframes.get("junctions", [])}
        names = [block.get("role") for block in relief["roles"]]
        expected = {"skyline", "ridge", "gully"}
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError(f"Relief authoring must author skyline, ridge and gully once: {names}")
        for block in relief["roles"]:
            role_name = block["role"]
            role_id = roles["TFF_ROLE_" + role_name.upper()]
            block_languages = block.get("languages", relief["languages"])
            block_joints = block.get("joints", relief.get("joints", []))
            block_junctions = block.get("junctions", relief.get("junctions", []))
            for language in block_languages:
                row_decorations = decorations(language)
                for name in block["tracks"]:
                    track = tracks.get(name)
                    if track is None or language not in track.get("languages", []):
                        raise ValueError(f"Relief track is not a {language} geometry track: {name}")
                    track_rows(track, language, role_name, role_id, row_decorations)
                for name in block["straight_tracks"]:
                    track_rows(straight_track(name, language, tracks), language, role_name,
                               role_id, row_decorations)
                for ordinal, joint in enumerate(block_joints):
                    joint_row(role_name, role_id, language, ordinal, joint, row_decorations)
                for name in block_junctions:
                    junction = junctions.get(name)
                    if junction is None or language not in junction["languages"]:
                        raise ValueError(f"Relief junction is not a generic junction of {language}: {name}")
                    emit({"role": role_name, "role_id": role_id, "language": language,
                          "track": junction["name"], "flags": flag["TFF_RUN_JUNCTION"],
                          "detail": 0, "mode": "empty", "sequence": junction["sequence"],
                          "substitute": "",
                          "lo": [float(sum(1 << int(arm) for arm in junction["arms"])), 0.0, 0.0, 0.0],
                          "hi": [0.0, 0.0, 0.0, 0.0], "phase_advance_px": 0,
                          "decorations": row_decorations})
    # FL-4902 lane VP: vehicle parts (TREE_BIT_AND_VEHICLE_PARTS_BEGIN_20260929).
    # A wheel is a closed small ring spelled by its arcs; a wing is straight
    # strokes closed at the tip by one turn glyph. Wheel arcs and wing turns
    # belong to the existing two-source-arm joint owner. Plain source edges
    # have no bow fact and take the generic straight tracks verbatim. No dead
    # plain-arc rows or new curvature/selector owner; interiors stay empty.
    vehicles = policy.get("vehicle_part_authoring")

    if vehicles is not None:
        tracks = {track["name"]: track for track in keyframes["tracks"]}
        junctions = {junction["name"]: junction for junction in keyframes.get("junctions", [])}
        for part in vehicles["roles"]:
            if "arcs" in part:
                raise ValueError("Vehicle plain-arc rows were removed; use source-joint keyframes")
            role_name = "TFF_ROLE_" + part["role"].upper()
            if role_name not in roles:
                raise ValueError(f"Vehicle part role has no shader role id: {part['role']}")
            for language in part["languages"]:
                for name in part["straight_tracks"]:
                    track = tracks.get(name)
                    if track is None or "bow" in track or "bow_ratio" in track \
                            or language not in track["languages"]:
                        raise ValueError(f"Vehicle straight track is not a straight generic track: {name}")
                    track_rows(track, language, part["role"], roles[role_name], [])
                for ordinal, joint in enumerate(part["joints"]):
                    joint_row(part["role"], roles[role_name], language, ordinal, joint, [])
                for name in part["junctions"]:
                    junction = junctions.get(name)
                    if junction is None or language not in junction["languages"]:
                        raise ValueError(f"Vehicle junction is not a generic junction of {language}: {name}")
                    sectors = sum(1 << int(arm) for arm in junction["arms"])
                    emit({"role": part["role"], "role_id": roles[role_name], "language": language,
                          "track": junction["name"], "flags": flag["TFF_RUN_JUNCTION"], "detail": 0,
                          "mode": "empty", "sequence": junction["sequence"], "substitute": "",
                          "lo": [float(sectors), 0.0, 0.0, 0.0], "hi": [0.0, 0.0, 0.0, 0.0],
                          "phase_advance_px": 0, "decorations": []})
    return {
        "selector": "tff_select_run (godot_project/render/ascii/composition/true_ff_run_select.glslinc)",
        "row_layout": "TFFRunRow 64 bytes: key uvec4 (role id, language bit, flags, detail), "
                      "lo vec4 and hi vec4 (angle, extent, signed bow, in-row height; junction: "
                      "lo.x sector mask), run ivec4 (primary idiom, substitute, phase advance px, "
                      "decoration rule mask)",
        "selection": "rows of the contour's role win; a role with rows for the language never "
                     "falls back; otherwise the language's generic rows; first matching interval",
        "rows": rows,
    }


def compile_terminal_axes(policy, run_table, admitted, glyphs):
    """Retain explicit compact straight-axis alternatives, not clock substitutes.

    A pair must already be the same track's long/short native realization.
    Joint/junction rows and typed subject roles cannot acquire this permission.
    The GPU still proves coverage; font advances never prove semantic coverage.
    """
    authored = policy.get("terminal_axes", {})
    if authored.get("schema") != "true_ff.terminal_axes.v1" or set(authored.get("preserves", [])) != {
            "axis_direction", "source_anchor", "paint_owner", "motion_owner"}:
        raise ValueError("Terminal axes require the retained straight-axis contract")
    result = {}
    for pair in authored.get("pairs", []):
        source, compact = pair.get("sequence"), pair.get("compact")
        if not isinstance(source, str) or not isinstance(compact, str) or source in result \
                or len(source) != 1 or len(compact) != 1 \
                or ("empty", source) not in admitted or ("empty", compact) not in admitted \
                or scenery_alphanumeric(source + compact):
            raise ValueError(f"Terminal axis spelling is not uniquely admitted: {pair}")
        if glyphs[compact]["advance_px"] >= glyphs[source]["advance_px"]:
            raise ValueError(f"Terminal axis must strictly reduce its native advance: {pair}")
        rows = [row for row in run_table["rows"] if row["role"] == "generic"
                and row["flags"] == 0 and row["sequence"] == source
                and row["substitute"] == compact and row["mode"] == "empty"]
        if not rows:
            raise ValueError(f"Terminal axis has no existing straight long/short track: {pair}")
        language_mask = 0
        for row in rows:
            language_mask |= policy["languages"][row["language"]]
        result[source] = {"compact": compact, "language_mask": language_mask}
    if not result or any(row["compact"] in result for row in result.values()):
        raise ValueError("Terminal axis alternatives must be a nonempty single-step cut")
    return {"schema": "true_ff.compiled_terminal_axes.v1", "by_sequence": result}


def compile_terminal_parts(policy, admitted, glyphs, shapes):
    """Compile explicitly authored part-local terminal motifs and fixed anchors.

    The anchor is a named, unchanged glyph, not the surviving ink centroid.
    A terminal spelling may only remove ink at that anchor; it cannot close
    an opening or subsume another part's paint/motion/coverage obligation.
    Runtime still proves the complete row and cross-row attachments.
    """
    result = {}
    for subject in policy.get("subject_keyframes", []):
        for part in subject["parts"]:
            terminal = part.get("terminal")
            if terminal is None:
                continue
            label = f"{subject['subject']}/{part['id']}"
            anchor = terminal.get("anchor")
            members = terminal.get("idiom")
            if subject.get("source") != "effect_fact" or part.get("role") != "contour" \
                    or terminal.get("scope") != "this_part_only" \
                    or part.get("arc") not in terminal.get("preserves", []) \
                    or not isinstance(anchor, str) or len(anchor) != 1 or anchor not in glyphs \
                    or not isinstance(members, list) or not members \
                    or any(("empty", text) not in admitted for text in members):
                raise ValueError(f"Terminal part lacks an admitted retained anchor: {label}")
            target = "".join(members)
            variants = [members for members in part["idiom_by_tier"] if members is not None]
            variants += [key[1] for key in part.get("keys", [])]
            variants += [key[1] for keys in part.get("keys_by_tier", []) if keys for key in keys]
            spellings = {"".join(members) for members in variants} | {target}

            def anchored(text):
                if text.count(anchor) != 1 or not 1 <= len(text) <= policy["budgets"]["horizontal_run_max"]:
                    raise ValueError(f"Terminal part anchor is not unique and whole: {label}: {text}")
                anchor_x2 = 2 * sum(glyphs[c]["advance_px"] for c in text[:text.index(anchor)]) \
                    + glyphs[anchor]["advance_px"]
                pen, ink = 0, set()
                for char in text:
                    ink.update((2 * (pen + x) - anchor_x2, y) for x, y in shapes[char][0])
                    pen += glyphs[char]["advance_px"]
                if not ink or not 0 <= anchor_x2 < 65535:
                    raise ValueError(f"Terminal part anchor is empty or out of range: {label}")
                return anchor_x2, pen, ink

            _, target_width, target_ink = anchored(target)
            for text in sorted(spellings):
                anchor_x2, width, ink = anchored(text)
                if not target_ink <= ink or (text != target and target_width >= width):
                    raise ValueError(f"Terminal part must remove only ink at its retained anchor: {label}: {text}")
                record = {"compact": target if text != target else "", "anchor_x2": anchor_x2}
                if text in result and result[text] != record:
                    raise ValueError(f"Conflicting terminal part spelling/anchor: {label}: {text}")
                result[text] = record
    for record in result.values():
        if record["compact"] and result[record["compact"]]["compact"]:
            raise ValueError("Terminal part alternatives cannot form chains")
    return {"schema": "true_ff.compiled_terminal_parts.v1", "by_sequence": result}


def native_shapes(glyphs, atlas_bytes):
    """One native mask measurement owner for ladders and material recipes."""
    atlas = Image.open(io.BytesIO(atlas_bytes)).convert("RGB")
    shapes = {}
    for char, glyph in glyphs.items():
        ax, ay, bx, by = glyph["atlas_rect"]
        ix, iy, _, _ = glyph["ink_bounds"]
        points = [(x - ax + ix, y - ay + iy) for y in range(ay, by) for x in range(ax, bx)
                  if atlas.getpixel((x, y))[0] == 0]
        if not points:
            continue
        mx = sum(x for x, y in points) / len(points)
        my = sum(y for x, y in points) / len(points)
        lean = sum((x - mx) * (y - my) for x, y in points)
        shapes[char] = (points, lean)
    return shapes


def measured_ladders(policy, admitted, glyphs, shapes, stack_chars):
    """Author thin, same-lean ladders from TRAIN candidates, not pixel-fit at runtime."""
    measurements = {}
    # Extending material band fragments must not silently re-author contour
    # ladders. A ladder candidate needs the original TRAIN-combo admission.
    sequences = sorted({text for (mode, text), record in admitted.items()
                        if any("fragment_of" not in e
                               and e["section"] != "operator_semantic_admission"
                               and e["section"] != "water_role_table"
                               for e in record["evidence"])})
    for track in policy["geometry_keyframes"]["tracks"]:
        profile = policy.get("ladder_profiles", {}).get(track["name"])
        if profile is None:
            continue
        candidates = []
        angle = math.radians(profile["fit_angle_degrees"])
        for text in sequences:
            if len(text) > profile["max_glyphs"]:
                continue
            if scenery_alphanumeric(text):
                continue
            if any(c not in shapes or shapes[c][1] * profile["lean"] < -1e-6 for c in text):
                continue
            points, pen = [], 0
            for char in text:
                points.extend((x + pen, y) for x, y in shapes[char][0])
                pen += glyphs[char]["advance_px"]
            count = len(points)
            if not profile["ink_pixels"][0] <= count <= profile["ink_pixels"][1]:
                continue
            mx = sum(x for x, y in points) / count
            my = sum(y for x, y in points) / count
            sequence_lean = sum((x - mx) * (y - my) for x, y in points)
            if sequence_lean * profile["lean"] < -1e-6:
                continue
            error = max(abs(-(x - mx) * math.sin(angle) + (y - my) * math.cos(angle))
                        for x, y in points)
            if error > profile["max_line_error_px"]:
                continue
            candidates.append({"text": text, "ink_pixels": count, "height_px": my,
                               "line_error_px": error, "stack_member": text in stack_chars})
        if not candidates:
            raise ValueError(f"No TRAIN equal-ink ladder for {track['name']}")
        selected = {}
        for target in profile["mean_heights_px"]:
            choice = min(candidates, key=lambda c: (abs(c["height_px"] - target),
                         abs(c["ink_pixels"] - sum(profile["ink_pixels"]) / 2),
                         not c["stack_member"], c["text"]))
            selected[choice["text"]] = choice
        levels = sorted(selected.values(), key=lambda c: (c["height_px"], c["text"]))
        separated = []
        for level in levels:
            if not separated or level["height_px"] - separated[-1]["height_px"] >= 1.0:
                separated.append(level)
        levels = separated
        if len(levels) < 4:
            raise ValueError(f"TRAIN ladder has fewer than four height levels: {track['name']}")
        track["heights"] = []
        for index, level in enumerate(levels):
            threshold = 17 if index + 1 == len(levels) else (
                level["height_px"] + levels[index + 1]["height_px"]) * 0.5
            # Same ink/shape at both extent levels. Subject LOD is a separate
            # authored feature addition, not a doubled-ink height substitute.
            track["heights"].append([threshold, level["text"], level["text"]])
            key = ("empty", level["text"])
            admitted.setdefault(key, {"text": level["text"], "mode": "empty", "evidence": []})[
                "evidence"].append({"section": "measured_ladder", "track": track["name"]})
        measurements[track["name"]] = {"levels": levels, "profile": profile,
            "selection": "offline TRAIN/atlas authoring; no per-frame candidate election"}
    return measurements


def compile_feature_depictions(registry_bytes, language, catalog):
    """FL-4917: compile selected cards onto the production vocabulary.

    The registry owns semantic obligations and whole authoring alternatives.
    This table contains only the selected alternative and exact existing run
    indices. It adds no competing glyph inventory, selector or renderer.
    An authoring connection does not settle any retained source/integration
    obligation and does not assert that a live producer emits the feature.
    """
    registry = json.loads(registry_bytes)
    features = registry.get("features", [])
    if len(features) != 237 or len({row.get("id") for row in features}) != 237:
        raise ValueError("The True FF feature census must retain 237 distinct REQUIRED entries")
    glyphs = {row["char"]: row["advance_px"] for row in catalog["glyphs"]}
    admitted = {(group["mode"], sequence) for group in language["groups"]
                for sequence in group["sequences"]}
    row_indices = {}
    for index, row in enumerate(language["run_table"]["rows"]):
        row_indices.setdefault((row["role"], row["language"], row["track"]), []).append(index)

    def authored_reference(track):
        """Point into a real table in this output, never accept a name alone."""
        components = track.split(".")
        if len(components) == 3 and components[:2] == ["authored", "subject"]:
            for ordinal, subject in enumerate(language["subject_keyframes"]):
                if subject["subject"] == components[2]:
                    return {"definition_path": ["subject_keyframes", ordinal],
                            "part_ids": [part["id"] for part in subject["parts"]],
                            "clock": subject["clock"]}
        elif components[:2] == ["authored", "sky"]:
            path = ["sky_authoring", *components[2:]]
            if components[2:3] == ["rain"] and len(components) == 4:
                path.insert(2, "events")
            value = language
            for component in path:
                if not isinstance(value, dict) or component not in value:
                    break
                value = value[component]
            else:
                reference = {"definition_path": path}
                if isinstance(value, dict) and "parts" in value:
                    reference["part_ids"] = [part["id"] for part in value["parts"]]
                    reference["clock"] = value["clock"]
                return reference
        raise ValueError(f"Required depiction names no compiled authored table: {track}")

    direct, folds, no_depiction = {}, {}, {}
    for feature in registry["features"]:
        feature_id, mapping = feature["id"], feature["true_ff"]
        if mapping["state"] != "REQUIRED":
            raise ValueError(f"A depiction obligation must remain REQUIRED: {feature_id}")
        retained = {key: mapping[key] for key in (
            "source_gap", "integration_gap", "implementation_gap", "current_implementation_gap")
            if key in mapping}
        obligation = {"meaning": mapping["meaning"], "meaning_refs": mapping["meaning_refs"],
                      "world_facts": mapping["world_facts"],
                      "required": mapping["required"], "open_work": mapping["open_work"],
                      # Missing is deliberately explicit. Legacy ADAPT/FOLD
                      # labels do not establish the operator's manual review.
                      "manual_audit": mapping.get("manual_audit"),
                      "visual_eligible": mapping.get("manual_audit", {}).get("disposition") not in (
                          "DROP_VISUAL_TREATMENT", "RETAIN_NONVISUAL_BEHAVIOR"),
                      "retained_obligations": retained}
        if mapping["mapping"] == "FOLD":
            folds[feature_id] = {**obligation, "targets": mapping["fold_into"]}
            continue
        if mapping["mapping"] == "NO_TRUE_FF_DEPICTION":
            no_depiction[feature_id] = obligation
            continue
        card = mapping["authoring_card"]
        selected = next(candidate for candidate in card["candidates"]
                        if candidate["id"] == card["selected"])
        native_text = all(run["role"] == "text" and run["language"] == "native_text"
                          for run in mapping["required"]["runs"])
        bindings = selected.get("sequence_bindings", [])
        sequence_owner = "sky_authoring.star_classes" if any(
            run["role"] == "star" and "authored.sky.star_classes" in run["tracks"]
            for run in mapping["required"]["runs"]) else None
        for binding in bindings:
            key = (binding["mode"], binding["sequence"])
            if key not in admitted or scenery_alphanumeric(binding["sequence"]):
                raise ValueError(f"Selected card is outside its production scenery vocabulary: {feature_id}: {key}")
            if "former_table" in binding or binding.get("active") is False:
                raise ValueError(f"Selected card cannot use an inactive/former binding: {feature_id}: {key}")
            table = binding.get("table")
            if sequence_owner is not None and table != sequence_owner:
                raise ValueError(f"Selected card must retain its required authored owner: {feature_id}: {sequence_owner}")
            run_keys = ("role", "language", "track")
            if any(field in binding for field in run_keys) and (
                    not all(isinstance(binding.get(field), str) and binding[field].strip()
                            for field in run_keys)
                    or not any(all(row.get(field) == binding[field] for field in run_keys)
                               and row.get("mode") == key[0] and row.get("sequence") == key[1]
                               for row in language["run_table"]["rows"])):
                raise ValueError(f"Named run does not bind this exact sequence/mode: {feature_id}: {key}")
            if table is not None:
                if table != "sky_authoring.star_classes":
                    raise ValueError(f"Unknown authored sequence table: {feature_id}: {table}")
                star_class = binding.get("class")
                matching = [entry for entry in language["sky_authoring"]["star_classes"]
                            if entry["class"] == star_class and not isinstance(star_class, bool)]
                if (len(matching) != 1 or matching[0]["mode"] != key[0]
                        or [key[1]] not in matching[0]["idioms"]):
                    raise ValueError(f"Sequence does not belong to its named class/mode: {feature_id}: {key}")
        recipe = []
        for run in mapping["required"]["runs"]:
            for track in run["tracks"]:
                key = (run["role"], run["language"], track)
                indices = row_indices.get(key, [])
                if indices:
                    recipe.append({"role": run["role"], "language": run["language"],
                                   "track": track, "run_indices": indices})
                elif track.startswith("authored."):
                    # Dereference this output's actual subject/sky data. A
                    # registry spelling is not evidence of an existing table.
                    recipe.append({"role": run["role"], "language": run["language"],
                                   "authored_table": track, **authored_reference(track)})
                elif run["role"] == "text" and run["language"] == "native_text" \
                        and track == "native_character_sequence":
                    recipe.append({"role": "text", "language": "native_text",
                                   "native_catalog": "saitamaar/catalog.json"})
                else:
                    raise ValueError(f"Selected depiction names no production binding: {feature_id}: {key}")
        demo = selected["original_text_demo"]
        if not native_text and scenery_alphanumeric(demo):
            raise ValueError(f"D7 forbids a selected scenery card alphabet: {feature_id}")
        text_rows = []
        for ordinal, line in enumerate(demo.splitlines()):
            pen, characters = 0, []
            for char in line:
                if char not in glyphs:
                    raise ValueError(f"Selected depiction lacks native metrics: {feature_id}: {char!r}")
                characters.append({"codepoint": ord(char), "advance_px": glyphs[char],
                                   "origin_px": pen})
                pen += glyphs[char]
            text_rows.append({"row": ordinal, "origin_y_px": ordinal * 17,
                              "characters": characters, "advance_px": pen})
        direct[feature_id] = {**obligation, "selected": card["selected"],
                              "recipe": recipe, "sequence_bindings": bindings,
                              "comparison_text": demo, "comparison_rows": text_rows,
                              "native_text_layers": selected.get("native_text_layers", []),
                              "source_text_refs": selected.get("source_text_refs", []),
                              "source_bindings": selected.get("source_bindings", []),
                              "selection_rule_refs": card["selection_rule_refs"]}
    for feature_id, fold in folds.items():
        if not fold["targets"] or any(target not in direct for target in fold["targets"]):
            raise ValueError(f"Fold lacks a direct meaning-carrying authored target: {feature_id}")
        # Carry the folded row's own facts and open work beside references to
        # the concrete direct recipes. A fold never erases a producer gap or
        # invents a generic absence representation.
        fold["carriers"] = [{"feature_id": target,
                             "definition_path": ["feature_depictions", "direct", target, "recipe"]}
                            for target in fold["targets"]]
    # The production uploader consumes these groups, not a second glyph
    # selector. Recipe indices nominate each role/language's semantic
    # carriers. The same authored role needs its complete directional and
    # junction continuum during camera/object motion, so its remaining rows
    # are explicit continuations, never an arrival-order or generic fallback.
    groups = {}
    authored_tables = {}
    for feature_id, feature in direct.items():
        # Keep historical cards and unresolved obligations in the census, but
        # an audited nonvisual/drop decision cannot nominate active artwork.
        if not feature["visual_eligible"]:
            continue
        for recipe in feature["recipe"]:
            if "run_indices" in recipe:
                pair = (recipe["role"], recipe["language"])
                group = groups.setdefault(pair, {"role": pair[0], "language": pair[1],
                    "role_id": language["run_table"]["rows"][recipe["run_indices"][0]]["role_id"],
                    "language_id": language["languages"][pair[1]],
                    "feature_ids": [], "required_run_indices": []})
                if feature_id not in group["feature_ids"]:
                    group["feature_ids"].append(feature_id)
                group["required_run_indices"].extend(recipe["run_indices"])
            elif "authored_table" in recipe:
                name = recipe["authored_table"]
                binding = authored_tables.setdefault(name, {
                    "definition_path": recipe["definition_path"], "feature_ids": []})
                if binding["definition_path"] != recipe["definition_path"]:
                    raise ValueError(f"Authored table has two definition owners: {name}")
                if feature_id not in binding["feature_ids"]:
                    binding["feature_ids"].append(feature_id)
    for index, row in enumerate(language["run_table"]["rows"]):
        pair = (row["role"], row["language"])
        if pair not in groups:
            raise ValueError(f"Production run has no REQUIRED depiction carrier: {pair}: {row['track']}")
        groups[pair].setdefault("run_indices", []).append(index)
    ordered_groups = sorted(groups.values(), key=lambda group: (
        group["role_id"], group["language_id"]))
    for group in ordered_groups:
        required_indices = sorted(set(group["required_run_indices"]))
        group["required_run_indices"] = required_indices
        group["continuation_run_indices"] = [index for index in group["run_indices"]
                                             if index not in required_indices]
    runtime = {"schema": "true_ff.depiction_bindings.v1",
        "owner": "TrueFFPartRepresentation.production; TrueFFFeatureRenderer P5 upload",
        "group_address": "role_id * 16 + language_id; each group retains original run precedence",
        "continuation_rule": "D5/D9: retain the existing authored role-language's complete orientation, height, joint and junction continuum; no role fallback and no row clipping",
        "role_groups": ordered_groups, "authored_tables": authored_tables,
        "visual_feature_ids": sorted(feature_id for feature_id, feature in direct.items()
                                     if feature["visual_eligible"]),
        "required_feature_count": len(registry["features"])}
    return {"schema": "true_ff.feature_depictions.v1",
            "registry_path": "config/godot_feature_regressions.json",
            "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
            "contract": "REQUIRED keeps every census entry accountable; it does not require an independent drawing for infrastructure or nonvisual behavior. manual_audit records individual source/style decisions, not runtime or visual acceptance; null means unaudited. Legacy recipes remain vocabulary references, never permission to spawn a depiction contrary to its audited disposition. Exact run indices reference this output's sole run_table. Comparison text is authoring data, never a screen-space sprite or a fallback for missing world facts.",
            "direct": direct, "folds": folds, "no_depiction": no_depiction,
            "runtime": runtime}


def compile_language(source_bytes, policy_bytes, catalog_bytes, atlas_bytes, registry_bytes=None):
    digest = hashlib.sha256(source_bytes).hexdigest()
    if digest != SOURCE_SHA:
        raise ValueError("AAHub train summary does not match the pinned source SHA")
    source = json.loads(source_bytes)
    policy = json.loads(policy_bytes)
    catalog = json.loads(catalog_bytes)
    if (source["schema"], source["partition"], source["px"], source["line_pitch_px"],
            source["pages"], source["lines"], source["held_out_slugs_excluded"]) != (
            "sjis_corpus_combos.v1", "train", 16, 17, 23363, 457065, 57):
        raise ValueError("AAHub train/font/layout contract changed")
    if "groups" in policy:
        raise ValueError("Authoring policy must not own a second sequence vocabulary")
    for track in policy["geometry_keyframes"]["tracks"]:
        if "bow" in track and "bow_ratio" in track:
            raise ValueError(f"Ambiguous source-bow units: {track['name']}")
    glyphs = {g["char"]: g for g in catalog["glyphs"]}
    measured = {g["g"]: g for g in source["top_glyphs"]}
    for char, glyph in measured.items():
        if char in glyphs and glyph["px"] != glyphs[char]["advance_px"]:
            raise ValueError(f"Native advance differs from corpus: U+{ord(char):04X}")
    # These semantic exceptions are operator requirements, not inferred from
    # raw frequency. The dataset classifies these kana/kanji as text otherwise.
    hatching = set("ﾆ二ニ")
    debris = set("彡从")
    fine = {g for g, row in measured.items() if row["class"] == "fine_texture"}
    shapes = native_shapes(glyphs, atlas_bytes)
    water_curves = set()
    for char, (points, _) in shapes.items():
        if measured.get(char, {}).get("class") != "fullwidth_stroke":
            continue
        columns = sorted({x for x, y in points})
        ys = [sum(y for x, y in points if x == col) / sum(x == col for x, y in points)
              for col in columns]
        signs = [1 if b > a else -1 for a, b in zip(ys, ys[1:]) if abs(b - a) > 0.25]
        turns = sum(a != b for a, b in zip(signs, signs[1:]))
        if columns[-1] - columns[0] >= 10 and max(ys) - min(ys) <= 5 and turns >= 2:
            water_curves.add(char)
    required = {g for track in policy["geometry_keyframes"]["tracks"]
                for row in track.get("heights", []) for g in row[1:]}
    forbidden = set(policy["forbidden_codepoints"])
    admitted = {}
    rejected = []

    def add(text, mode, section, observation):
        if not text or len(text) > 4 or any(c.isspace() or ord(c) in forbidden for c in text):
            rejected.append({"text": text, "source": section, "reason": "native_sequence_law"})
            return
        if any(c not in glyphs or not glyphs[c]["has_ink"] for c in text):
            rejected.append({"text": text, "source": section, "reason": "native_catalog_missing_ink"})
            return
        key = (mode, text)
        record = admitted.setdefault(key, {"text": text, "mode": mode, "evidence": []})
        record["evidence"].append({"section": section, **observation})

    def mode_for(text):
        if text in required:
            return "empty"
        if any(c in hatching for c in text) or set(text) <= fine or set(text) <= debris:
            return "bounded_fill"
        return "empty"

    # No raw bigram list and no count-based top-N competition. Outline
    # combinations cannot be displaced by the corpus's dominant :: field.
    for section in ("idioms", "bigrams_outline", "trigrams_outline"):
        for row in source[section]:
            text = row["g"]
            add(text, mode_for(text), section, {"n": row["n"]})
            if set(text) <= water_curves:
                add(text, "sparse_scatter", section, {"n": row["n"], "shape_class": "water_curve"})
            for char in sorted(set(text)):
                add(char, "empty" if char in required else mode_for(char),
                    section, {"member_of": text})
                if char in water_curves:
                    add(char, "sparse_scatter", section,
                        {"member_of": text, "shape_class": "water_curve"})
            # Rare opposite-lean strokes can be below the stored top-N list.
            # Admit a mirror only when this aggregate records observations,
            # not merely because a geometric mirror is imaginable.
            mirror_n = row.get("mirror_n")
            if mirror_n is None:
                mirror_n = row.get("pooled_n", row["n"]) - row["n"]
            if row.get("mirror_exact") and mirror_n > 0 and row["mirror"] != text:
                mirror = row["mirror"]
                add(mirror, mode_for(mirror), section, {"n": mirror_n, "mirror_of": text})
                for char in sorted(set(mirror)):
                    add(char, mode_for(char), section, {"member_of": mirror, "mirror_of": text})

    stacks = []
    for row in source["stacks_outline"]:
        upper, lower = row["upper"], row["lower"]
        for char in (upper, lower):
            add(char, "empty" if char in required else mode_for(char),
                "stacks_outline", {"upper": upper, "lower": lower, "dx_px": row["dx_px"]})
        if upper not in glyphs or lower not in glyphs or any(ord(c) in forbidden for c in upper + lower):
            rejected.append({"upper": upper, "lower": lower, "dx_px": row["dx_px"],
                             "source": "stacks_outline", "reason": "native_catalog_or_forbidden"})
            continue
        stacks.append({**row, "row_step_px": 17,
                       "meaning": "observed_alignment_not_automatic_join"})

    for row in source["bands"]:
        period = row["period"]
        if period == "(aperiodic)":
            for example in row["examples"]:
                if len(example) <= 4:
                    add(example, "bounded_fill", "bands", {"n": row["n"], "example": example})
            continue
        # Bounded fragment of the measured periodic band; not permission to
        # tile a surface. Its region must touch a contour or have a taper.
        text = period * (4 // len(period))
        add(text, "bounded_fill", "bands", {"n": row["n"], "period": period})
        for size in range(1, len(text)):
            add(text[:size], "bounded_fill", "bands",
                {"n": row["n"], "period": period, "fragment_of": text})
        if (set(period) <= fine and ":" not in period) or set(period) <= water_curves:
            add(period, "sparse_scatter", "bands", {"n": row["n"], "period": period})
            for size in range(1, len(text)):
                add(text[:size], "sparse_scatter", "bands",
                    {"n": row["n"], "period": period, "fragment_of": text})

    # Explicit operator semantic exceptions are authored data, not an
    # invented TRAIN observation. Keep absent aggregate counts absent (star).
    semantic = policy.get("semantic_admissions", {})
    if semantic:
        if not semantic.get("decision") or set(semantic) - {"decision", "empty", "sparse_scatter"}:
            raise ValueError("Invalid operator semantic admission")
        for mode in ("empty", "sparse_scatter"):
            for char in semantic.get(mode, []):
                if len(char) != 1 or char not in shapes or ord(char) in forbidden:
                    raise ValueError(f"Invalid operator-admitted glyph: {char!r}")
                observation = {"decision": semantic["decision"]}
                if char in measured:
                    observation["train_glyph_count"] = measured[char]["n"]
                add(char, mode, "operator_semantic_admission", observation)
    validate_subject_keyframes(policy, admitted)
    validate_sky_authoring(policy, admitted)
    water_role_admissions = validate_water_role_table(
        policy, source, glyphs, add)
    ladders = measured_ladders(policy, admitted, glyphs, shapes,
                              {row[k] for row in source["stacks_outline"] for k in ("upper", "lower")})
    required = {g for track in policy["geometry_keyframes"]["tracks"]
                for row in track["heights"] for g in row[1:]}
    absent = required - {text for mode, text in admitted if mode == "empty"}
    if absent:
        raise ValueError(f"Authored keyframe not supported by TRAIN outline evidence: {sorted(absent)}")
    # After the ladders, so junction glyphs never become ladder candidates.
    def admit_junction(text, observation):
        add(text, "empty", "junction_top_glyphs", observation)
        return ("empty", text) in admitted
    admit_junctions(policy, measured, admit_junction)
    # After every admission: face marks may use any admitted entry.
    validate_face_authoring(policy, admitted)

    groups = []
    all_languages = list(policy["languages"])
    for mode in ("empty", "bounded_fill", "sparse_scatter"):
        sequences = sorted(text for admitted_mode, text in admitted if admitted_mode == mode)
        group = {"name": "train_" + mode, "mode": mode,
                 "languages": all_languages, "sequences": sequences}
        if mode == "bounded_fill":
            group["boundary"] = "touch_contour_or_taper"
        if mode == "sparse_scatter":
            group["distribution"] = "feature_relative_even"
        groups.append(group)

    gaps = []
    for row in source["space_spellings"]:
        spelling = row["spelling"]
        if set(spelling) - set("Fh"):
            raise ValueError("Unknown corpus spacing symbol")
        width = 11 * spelling.count("F") + 5 * spelling.count("h")
        if width != row["px"]:
            raise ValueError("Corpus gap metric mismatch")
        gaps.append({**row, "text": spelling.replace("F", "\u3000").replace("h", " "),
                     "strict_interior": "hhh" not in spelling,
                     "strict_line_start": "hhh" not in spelling and not spelling.startswith("h")})

    output = dict(policy)
    output["schema"] = "true_ff.outline_language.v1"
    output["generated_by"] = "scripts/compile_true_ff_outline_language.py"
    output["provenance"] = {
        "repository": SOURCE_REPO, "commit": SOURCE_COMMIT, "path": SOURCE_PATH,
        "sha256": digest, "partition": "train", "pages": source["pages"],
        "lines": source["lines"], "held_out_slugs_excluded": 57,
        "aggregate_scope": "full TRAIN extraction; stored combo lists are bounded summaries, not every observed combination",
        "split_sha256": source["split_sha256"], "input_set_sha256": source["input_set_sha256"],
        "corpus_font_sha256": source["font_sha256"],
        "native_font_sha256": catalog["source_font_sha256"],
        "font_comparison": "shared measured advances agree; mask equivalence unverified",
        "authoring_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest()}
    output["provenance"]["native_atlas_sha256"] = hashlib.sha256(atlas_bytes).hexdigest()
    output["ladder_measurements"] = ladders
    output["water_role_admissions"] = water_role_admissions
    classes = {"hatching": hatching, "debris": debris, "fine_texture": fine,
               "water_curve": water_curves}
    interiors = compile_material_interiors(policy, admitted, glyphs, shapes, classes,
                                           water_role_admissions)
    output["material_interiors"] = interiors
    output["run_table"] = compile_run_table(policy, admitted, glyphs,
                                            water_role_admissions, interiors)
    output["terminal_axes"] = compile_terminal_axes(policy, output["run_table"], admitted, glyphs)
    output["terminal_parts"] = compile_terminal_parts(policy, admitted, glyphs, shapes)
    metrics = []
    for mode, text in sorted(admitted):
        points, pen = [], 0
        for char in text:
            points.extend((x + pen, y) for x, y in shapes[char][0])
            pen += glyphs[char]["advance_px"]
        occupied = max(len({((x + sx) // 8, (y + sy) // 8) for x, y in points})
                       for sy in range(8) for sx in range(8))
        metrics.append({"mode": mode, "text": text, "ink_pixels": len(points),
                        "occupied_8px_bins_upper_bound": occupied})
    output["sequence_metrics"] = metrics
    output["space_advances_px"] = {"32": 5, "12288": 11}
    output["spacing_policy"] = {"strict": True, "max_adjacent_half": 2,
                                "decision": "SPACE_RULE_DOUBLE_HALF_MIDROW_20260928",
                                "no_leading_half": True, "no_trailing_fill": True,
                                "observed_exceptions": source["whitespace_law"]}
    output["groups"] = groups
    output["sequence_evidence"] = [admitted[key] for key in sorted(admitted)]
    output["stacks_outline"] = stacks
    output["space_spellings"] = gaps
    output["excluded"] = rejected
    if registry_bytes is not None:
        output["feature_depictions"] = compile_feature_depictions(registry_bytes, output, catalog)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Pinned TRAIN aggregate JSON; never archive pages")
    parser.add_argument("--output", type=Path, default=ASSETS / "outline_language.v1.json")
    args = parser.parse_args()
    output = compile_language(args.source.read_bytes(),
                              (ASSETS / "outline_authoring.v1.json").read_bytes(),
                              (ASSETS / "saitamaar/catalog.json").read_bytes(),
                              (ASSETS / "saitamaar/atlas.png").read_bytes(),
                              (ROOT / "config/godot_feature_regressions.json").read_bytes())
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(f"Generated {sum(len(g['sequences']) for g in output['groups'])} mode-qualified sequences; "
          f"{len(output['stacks_outline'])} measured stacks; {len(output['space_spellings'])} gap spellings")


if __name__ == "__main__":
    main()

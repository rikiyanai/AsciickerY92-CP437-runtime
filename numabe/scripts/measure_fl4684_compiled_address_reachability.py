#!/usr/bin/env python3
# =============================================================================
# scripts/measure_fl4684_compiled_address_reachability.py
# =============================================================================
# WHAT THIS SCRIPT MEASURES
# -------------------------
# The offline compiler produces 99 "compiled addresses". Each one is a labelled
# slot that answers "when the renderer reports a cell like THIS, here are the
# legal character shapes". A compiled address is only useful if the live
# renderer can actually produce a cell description that lands on it.
#
# The renderer's side of that is the "scene-fact producer" in
# engine/render/render_resolve.cpp. Once per drawn cell it fills in a small
# fixed record (a SceneFactV1) describing what the cell is: which material,
# which role, which tonal step, which direction, and so on.
#
# This script compares the two sides and writes down, per domain, HOW MANY
# compiled addresses the producer can currently land on and how many it cannot.
# It deliberately does NOT claim coverage. It counts, names the gaps, and says
# which questions only a live run can answer.
#
# WHAT IT DOES NOT DO
# -------------------
# It does not run the game. Everything here is read from two files on disk: the
# compiled artifact and the producer source. So its findings are about what the
# producer is CAPABLE of emitting, not about what any particular play session
# actually emitted. Every count below that depends on a real run is reported
# under "requires_live_evidence" instead of being guessed.
#
# WHY THAT DISTINCTION MATTERS
# ----------------------------
# A previous lane recorded a failure where a chain of stages each looked correct
# in isolation while the combination was never reachable in practice. Counting
# structural capability and calling it coverage is exactly how that happens, so
# the two are kept apart here by construction.
#
# USAGE
# -----
#     python3 scripts/measure_fl4684_compiled_address_reachability.py --write
#     python3 scripts/measure_fl4684_compiled_address_reachability.py --check
# =============================================================================

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

# The compiled artifact that owns the 99 addresses.
GLYPH_FACT_ARTIFACT = Path("assets/glyphs/generated/fl4676.glyph_fact.v1.json")
# The renderer source that fills in one scene-fact record per drawn cell.
PRODUCER_SOURCE = Path("engine/render/render_resolve.cpp")
# The typed record definition, used to enumerate every field a scene fact has.
SCENE_FACT_RECORD = Path("platform/fl4683_scene_fact_record.h")
# Where the measurement lands.
OUTPUT = Path("assets/glyphs/generated/fl4684.compiled_address_reachability.v1.json")
COMPILER_SCRIPT = ROOT / "scripts/compile_fl4683_glyph_program.py"


def static_loader_reachability() -> Dict[str, Any]:
    """Delegate call-site ownership to the FL-4683 compiler audit.

    This source/build measurement is deliberately not a runtime receipt. It
    proves only that one gate-enabled product call is reachable in tracked
    source and names the ceiling that still requires a live execution receipt.
    """
    spec = importlib.util.spec_from_file_location(
        "fl4683_compiler_for_fl4684_measurement", COMPILER_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise MeasureError("cannot load the canonical FL-4683 call-site audit")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit_loader_call_sites()

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
# The compiler labels each address with a namespace in words ("terrain",
# "mesh"). The renderer emits a number. This is the agreed mapping, taken from
# FL4512_PROFILE_NAMESPACE_* in engine/render/render_frame_input.h.
NAMESPACE_CODE = {"terrain": 1, "mesh": 2}

# The compiler labels each address with a role in words. The renderer emits one
# of the FL4683_SCENE_FACT_ROLE_* numbers from
# platform/fl4683_scene_fact_record.h. This mapping follows the classifier
# Fl4683RoleFromFactsV1 in the producer source, which since the FL-4683
# role-byte correction mirrors the live owner's branch order exactly:
#
#     missing mesh fact      -> NONE                (0)   fail closed
#     mesh feature present   -> MESH_FEATURE        (4)
#     vertical relation      -> MATERIAL_BOUNDARY   (3)
#     direction              -> STRUCTURAL_FEATURE  (2)
#     flow                   -> STRUCTURAL_FEATURE  (2)
#     filled primitive       -> STRUCTURAL_FEATURE  (2)
#     otherwise              -> TONAL_FILL          (1)
#
# Note that TWO compiler roles -- "filled" and "direction" -- both arrive as
# STRUCTURAL_FEATURE. That collision is one of the findings below, not an
# oversight in this table. The FL-4683 route-family mask, not this role code,
# is what tells those two apart.
COMPILER_ROLE_TO_SCENE_FACT_ROLE = {
    "tonal": 1,          # FL4683_SCENE_FACT_ROLE_TONAL_FILL
    "filled": 2,         # FL4683_SCENE_FACT_ROLE_STRUCTURAL_FEATURE
    "direction": 2,      # FL4683_SCENE_FACT_ROLE_STRUCTURAL_FEATURE
    "edge": 3,           # FL4683_SCENE_FACT_ROLE_MATERIAL_BOUNDARY
    "mesh_feature": 4,   # FL4683_SCENE_FACT_ROLE_MESH_FEATURE
}

SCENE_FACT_ROLE_NAMES = {
    0: "none",
    1: "tonal_fill",
    2: "structural_feature",
    3: "material_boundary",
    4: "mesh_feature",
    5: "authored_text",
}

# FL-4683 tonal-discriminator amendment.
#
# Each compiled route family now has a matching bit in the renderer-owned
# `route_family_mask` byte. The renderer states, per cell, which family (or, for
# a tonal cell, which PAIR of families) that cell's address belongs to. Before
# this field existed, ramp.v1 and density.v1 were indistinguishable to a
# selector because both arrived under the single role code `tonal_fill`.
#
# This table maps the compiler's route key to the C++ enum name the renderer
# uses. The measurement reads the producer source to confirm the producer can
# actually emit each one, rather than assuming it.
ROUTE_KEY_TO_ROUTE_FAMILY_ENUM = {
    "ramp.v1": "FL4683_ROUTE_FAMILY_RAMP_V1",
    "density.v1": "FL4683_ROUTE_FAMILY_DENSITY_V1",
    "edge.v1": "FL4683_ROUTE_FAMILY_EDGE_V1",
    "direction.v1": "FL4683_ROUTE_FAMILY_DIRECTION_V1",
    "flow.v1": "FL4683_ROUTE_FAMILY_FLOW_V1",
    "filled.field.v1": "FL4683_ROUTE_FAMILY_FILLED_FIELD_V1",
    "mesh_feature.bundle.v1": "FL4683_ROUTE_FAMILY_MESH_FEATURE_BUNDLE_V1",
}

# The one multi-family combination the producer is allowed to emit. A tonal cell
# addresses ramp and density together because the existing live owner
# (Fl4260ProfileSelectGlyph) scores both pools in one pass.
TONAL_PAIR_ENUM = "FL4683_ROUTE_FAMILY_TONAL_PAIR_V1"

# Which renderer-owned field would have to select the lane, for each compiled
# route family. A "lane" is the numbered slot inside a route family, such as the
# 2 in "ramp[2]". `None` means the route family has exactly one lane, so no
# field has to choose.
ROUTE_KEY_TO_SCENE_FACT_FIELD = {
    "ramp.v1": "ramp_bin",
    "density.v1": "density_bin",
    "direction.v1": "direction_bin",
    "edge.v1": "edge_bin",
    "filled.field.v1": None,
    "mesh_feature.bundle.v1": None,
}

# Pattern that pulls "ramp[2]" apart into its family name and its lane number.
LANE_PATTERN = re.compile(r"^(?P<name>[a-z_]+)\[(?P<index>\d+)\]$")


class MeasureError(RuntimeError):
    """Raised when an input is missing or does not have the expected shape."""


def load_json(relative: Path) -> Any:
    absolute = ROOT / relative
    if not absolute.is_file():
        raise MeasureError(f"required input is missing: {relative}")
    return json.loads(absolute.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Reading what the producer can emit
# ---------------------------------------------------------------------------
def read_producer_capability() -> Dict[str, Any]:
    """Read the renderer source and work out what values it can put in a scene fact.

    Returns a dictionary describing:
      clamp_ceilings    -- for each binned field, the largest value the producer
                           can write. The source clamps each one with
                           `Fl4683ClampBinV1(value, ceiling)`, so the field can
                           hold 0 through `ceiling` inclusive.
      emitted_roles     -- the set of role numbers the classifier can return.
      constant_fields   -- fields the producer always writes as the same value,
                           which means they cannot select between anything.
      namespaces        -- the namespace numbers the producer can emit.

    WHY READ THE SOURCE INSTEAD OF HARD-CODING: if the producer changes, this
    measurement must change with it. Reading the ceilings out of the source
    means a later edit shows up here rather than silently invalidating the
    recorded counts.
    """
    source = (ROOT / PRODUCER_SOURCE).read_text(encoding="utf-8")

    # Each binned field is written as, for example:
    #     out.ramp_bin = Fl4683ClampBinV1(in.elev_quantized, 3);
    # The trailing number is the ceiling.
    clamp_ceilings: Dict[str, int] = {}
    for match in re.finditer(
        r"out\.(?P<field>[a-z0-9_]+)\s*=\s*Fl4683ClampBinV1\([^,]+,\s*(?P<ceiling>\d+)\s*\)",
        source,
    ):
        clamp_ceilings[match.group("field")] = int(match.group("ceiling"))
    if not clamp_ceilings:
        raise MeasureError(
            "no Fl4683ClampBinV1 assignments found; the producer source moved or changed shape"
        )

    # Every role constant the classifier can return.
    #
    # Pin the declarator, never the parameter list. FL-4762 widened this
    # classifier to accept vertical_relation, direction, and flow so it could
    # share the route predicates with the producer; the old full-signature pin
    # made this measurement raise ValueError instead of producing a number, and
    # nothing downstream distinguished "instrument broken" from "no result".
    # The prefix keeps this anchored to the DEFINITION rather than a call site.
    classifier_start = source.index("static inline uint8_t Fl4683RoleFromFactsV1(")
    classifier_end = source.index("\nstatic inline", classifier_start + 1)
    classifier = source[classifier_start:classifier_end]
    emitted_role_names = sorted(
        set(re.findall(r"FL4683_SCENE_FACT_ROLE_([A-Z_]+)", classifier))
    )
    name_to_code = {
        # FL-4683 role-byte correction (ruling 4): the classifier now has a
        # fail-closed first branch that reports "no role claimed" (0) for a cell
        # whose mesh claim could not be built, mirroring the live owner's
        # blocked-missing branch. Before that branch existed the classifier could
        # never return 0, so this table did not need the entry.
        "NONE": 0,
        "TONAL_FILL": 1,
        "STRUCTURAL_FEATURE": 2,
        "MATERIAL_BOUNDARY": 3,
        "MESH_FEATURE": 4,
        "AUTHORED_TEXT": 5,
    }
    emitted_roles = sorted(name_to_code[name] for name in emitted_role_names)

    # Fields the producer assigns a fixed literal to. A field that is always the
    # same value carries no information and therefore cannot pick out one
    # compiled address from another.
    constant_fields: Dict[str, str] = {}
    for field, literal in re.findall(
        r"out\.([a-z0-9_]+)\s*=\s*(0|FL4683_TOPOLOGY_EVENT_NONE)\s*;", source
    ):
        constant_fields[field] = literal

    # The namespace comes from Fl4512ProfileNamespaceForDispatch, which can
    # return NONE (0), TERRAIN (1), or MESH (2). Compiled addresses only ever
    # use TERRAIN and MESH.
    namespaces = [0, 1, 2]

    # -------------------------------------------------------------------
    # FL-4683 route-family discriminator
    # -------------------------------------------------------------------
    # Read the producer's route-family classifier and record which families it
    # can name. Reading it, rather than assuming it, means a later edit to the
    # classifier changes this measurement instead of silently invalidating it.
    classifier_name = "Fl4683RouteFamilyMaskFromFactsV1"
    if classifier_name not in source:
        raise MeasureError(
            f"{classifier_name} not found in the producer source; the "
            "route-family discriminator moved or was removed"
        )
    family_start = source.index(f"static inline uint8_t {classifier_name}(")
    family_end = source.index("\nstatic inline", family_start + 1)
    family_body = source[family_start:family_end]
    named_enums = set(re.findall(r"FL4683_ROUTE_FAMILY_[A-Z_0-9]+", family_body))
    # A classifier branch says which route a fact bit would name; it does not by
    # itself prove that the frame path can produce that bit's required operand.
    # Filled selection additionally requires a live primitive producer. The
    # former raster-occupancy proxy was deleted, so until a replacement assigns
    # `in.filled_primitive_valid` the compiled filled address is not reachable.
    dispatch_start = source.index("static inline bool DispatchShape6ResolverHook(")
    dispatch_end = source.index("\nstatic inline", dispatch_start + 1)
    dispatch_body = source[dispatch_start:dispatch_end]
    filled_primitive_producer_present = "in.filled_primitive_valid =" in dispatch_body
    if not filled_primitive_producer_present:
        named_enums.discard("FL4683_ROUTE_FAMILY_FILLED_FIELD_V1")
    # The tonal case returns the ramp+density PAIR constant rather than the two
    # bits separately, so expand it here to the two families it stands for.
    if TONAL_PAIR_ENUM in named_enums:
        named_enums.discard(TONAL_PAIR_ENUM)
        named_enums.add("FL4683_ROUTE_FAMILY_RAMP_V1")
        named_enums.add("FL4683_ROUTE_FAMILY_DENSITY_V1")
    emitted_route_keys = sorted(
        route_key
        for route_key, enum_name in ROUTE_KEY_TO_ROUTE_FAMILY_ENUM.items()
        if enum_name in named_enums
    )
    # Does the producer emit the tonal case as a PAIR? That is what allows one
    # scene fact to address both ramp[N] and density[M] the way the live owner
    # already does.
    emits_tonal_pair = TONAL_PAIR_ENUM in family_body

    return {
        "source_path": PRODUCER_SOURCE.as_posix(),
        "clamp_ceilings": dict(sorted(clamp_ceilings.items())),
        "emitted_role_codes": emitted_roles,
        "emitted_role_names": [SCENE_FACT_ROLE_NAMES[code] for code in emitted_roles],
        "constant_fields": dict(sorted(constant_fields.items())),
        "namespace_codes": namespaces,
        "route_family_classifier": classifier_name,
        "emitted_route_keys": emitted_route_keys,
        "emits_tonal_ramp_density_pair": emits_tonal_pair,
        "filled_primitive_producer_present": filled_primitive_producer_present,
    }


def read_scene_fact_fields() -> List[str]:
    """List every field name declared inside `struct SceneFactV1`.

    Used to report which renderer-owned fields exist but carry no compiled
    meaning yet -- that is, fields no compiled address is keyed on.
    """
    text = (ROOT / SCENE_FACT_RECORD).read_text(encoding="utf-8")
    start = text.index("struct SceneFactV1")
    end = text.index("};", start)
    body = text[start:end]
    # Each declaration looks like "uint8_t ramp_bin;" or
    # "uint8_t payload_sha256[32];".
    return [
        match.group("name")
        for match in re.finditer(
            r"^\s*(?:u?int\d+_t)\s+(?P<name>[a-z0-9_]+)\s*(?:\[\d+\])?\s*;", body, re.M
        )
    ]


# ---------------------------------------------------------------------------
# Classifying each compiled address
# ---------------------------------------------------------------------------
def parse_lane(lane_name: str) -> Tuple[str, Optional[int]]:
    """Split a lane label into its family name and its numeric slot.

    "ramp[2]"     -> ("ramp", 2)
    "filled_field" -> ("filled_field", None)
    """
    match = LANE_PATTERN.match(lane_name)
    if match is None:
        return lane_name, None
    return match.group("name"), int(match.group("index"))


def classify_addresses(artifact: Dict[str, Any], capability: Dict[str, Any]) -> Dict[str, Any]:
    """Decide, for every compiled address, whether the producer can land on it.

    Parameters:
      artifact   -- the parsed compiled artifact.
      capability -- what `read_producer_capability` found.

    Each address ends up in exactly one class:

      uniquely_addressable
        The producer can emit a scene fact whose namespace, material, declared
        route family, and lane-selecting field pick out this address and no
        other. Whether any real play session DOES emit it is a separate,
        live-only question.

      lane_outside_producer_range
        The address sits in a lane number the producer's clamp cannot reach.

      route_family_not_emitted_by_producer
        The producer's route-family classifier never names the compiled route
        family this address belongs to, so nothing can address it.

    HOW THE CLASSIFICATION CHANGED, AND WHY
    ---------------------------------------
    An earlier version of this measurement also had a class called
    `role_collision_not_uniquely_addressable`, which counted 64 of 99 addresses.
    That gap was real: ramp.v1 and density.v1 are two different compiled route
    families, both arrived under the single role code `tonal_fill`, and the
    scene fact carried no field saying which family a cell meant.

    The FL-4683 contract amendment added exactly that field --
    `route_family_mask` -- so the class no longer has anything to count. The
    address key used below is now the family the RENDERER declares, not the role
    it happens to share with other families. Note what this does and does not
    say: it says the address can now be NAMED without ambiguity. It says nothing
    about whether any play session reaches it. Those live questions stay listed,
    unanswered, under `requires_live_evidence`.
    """
    facts = artifact["facts"]

    # Kept for reporting only. It records which compiled route families share a
    # role code, which is the collision the discriminator was added to resolve.
    # It no longer decides any classification.
    families_per_role: Dict[Tuple[int, int], set] = collections.defaultdict(set)
    for fact in facts:
        key = fact["scene_membership_key"]
        namespace_code = NAMESPACE_CODE.get(key["profile_namespace"])
        role_code = COMPILER_ROLE_TO_SCENE_FACT_ROLE.get(key["role"])
        if namespace_code is None or role_code is None:
            continue
        families_per_role[(namespace_code, role_code)].add(key["route_key"])

    emitted_route_keys = set(capability["emitted_route_keys"])

    rows: List[Dict[str, Any]] = []
    for fact in facts:
        key = fact["scene_membership_key"]
        namespace_name = key["profile_namespace"]
        namespace_code = NAMESPACE_CODE.get(namespace_name)
        compiler_role = key["role"]
        role_code = COMPILER_ROLE_TO_SCENE_FACT_ROLE.get(compiler_role)
        route_key = key["route_key"]
        lane_family, lane_index = parse_lane(key["lane_name"])
        selecting_field = ROUTE_KEY_TO_SCENE_FACT_FIELD.get(route_key, "unmapped")

        # Can the producer name this address's route family at all?
        if route_key not in emitted_route_keys:
            classification = "route_family_not_emitted_by_producer"
        # Can the producer's clamp reach this lane number?
        elif (
            lane_index is not None
            and selecting_field in capability["clamp_ceilings"]
            and lane_index > capability["clamp_ceilings"][selecting_field]
        ):
            classification = "lane_outside_producer_range"
        else:
            classification = "uniquely_addressable"

        rows.append(
            {
                "fact_id": fact["fact_id"],
                "compiled_address_sha256": key["row_sha256"],
                "profile_namespace": namespace_name,
                "profile_namespace_code": namespace_code,
                "material_key": key["material_key"],
                "compiler_role": compiler_role,
                "scene_fact_role_code": role_code,
                "scene_fact_role_name": SCENE_FACT_ROLE_NAMES.get(role_code, "unknown"),
                "route_key": route_key,
                "lane_name": key["lane_name"],
                "lane_family": lane_family,
                "lane_index": lane_index,
                "lane_selecting_scene_fact_field": selecting_field,
                # The renderer-owned discriminator bit that names this address's
                # route family. This is the field the FL-4683 amendment added.
                "route_family_enum": ROUTE_KEY_TO_ROUTE_FAMILY_ENUM.get(
                    route_key, "unmapped"
                ),
                "shares_role_code_with_route_keys": sorted(
                    other
                    for other in families_per_role[(namespace_code, role_code)]
                    if other != route_key
                ),
                "scorer_id": key["scorer_id"],
                "glyph_member_count": len(fact["glyph_members"]),
                "relation_edge_count": len(fact["relation_projection"]["edges"]),
                "classification": classification,
            }
        )

    return {"rows": rows, "families_per_role": families_per_role}


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn the per-address rows into the counts a reader actually needs."""
    by_class = collections.Counter(row["classification"] for row in rows)
    by_namespace = collections.Counter(row["profile_namespace"] for row in rows)
    by_role = collections.Counter(row["compiler_role"] for row in rows)
    by_route = collections.Counter(row["route_key"] for row in rows)

    # Per-domain breakdown: how many addresses in each namespace and role fall
    # into each class. This is the "counts per domain" the measurement is for.
    per_domain: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int)
    )
    for row in rows:
        domain = f"{row['profile_namespace']}/{row['compiler_role']}"
        per_domain[domain][row["classification"]] += 1
        per_domain[domain]["total"] += 1

    return {
        "compiled_address_total": len(rows),
        "by_classification": dict(sorted(by_class.items())),
        "by_profile_namespace": dict(sorted(by_namespace.items())),
        "by_compiler_role": dict(sorted(by_role.items())),
        "by_route_key": dict(sorted(by_route.items())),
        "per_domain": {
            domain: dict(sorted(counts.items()))
            for domain, counts in sorted(per_domain.items())
        },
        "material_keys_referenced": sorted(
            {row["material_key"] for row in rows}
        ),
    }


def find_producer_values_without_a_compiled_address(
    rows: List[Dict[str, Any]], capability: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """List binned values the producer can emit that no compiled address claims.

    Example: the producer clamps `edge_bin` to 0..6, so it can emit a 6, but the
    compiled artifact only defines edge lanes 0..5. A cell that produced a 6
    would have no address to land on, which the handshake names
    `missing_compiled_address`.
    """
    # Which lane numbers each selecting field actually has addresses for.
    lanes_per_field: Dict[str, set] = collections.defaultdict(set)
    for row in rows:
        field = row["lane_selecting_scene_fact_field"]
        if field and field != "unmapped" and row["lane_index"] is not None:
            lanes_per_field[field].add(row["lane_index"])

    gaps: List[Dict[str, Any]] = []
    for field, ceiling in capability["clamp_ceilings"].items():
        covered = lanes_per_field.get(field, set())
        uncovered = [value for value in range(0, ceiling + 1) if value not in covered]
        gaps.append(
            {
                "scene_fact_field": field,
                "producer_value_range": [0, ceiling],
                "compiled_lane_values": sorted(covered),
                "producer_values_without_compiled_address": uncovered,
                "producer_values_without_compiled_address_count": len(uncovered),
            }
        )
    return sorted(gaps, key=lambda entry: entry["scene_fact_field"])


def read_role_priority_orders() -> Dict[str, Any]:
    """Compare the three places that decide "what kind of cell is this?".

    Three orderings in the source answer that question, and after the FL-4683
    role-byte correction all three follow the same branch order:

      * `Fl4260ProfileSelectGlyph` in engine/fl4131_runtime_harri_resolver.cpp
        is the EXISTING LIVE OWNER -- the function that actually picks the glyph
        a player sees. It checks a missing mesh fact (and fails closed), then
        mesh feature, then vertical relation, then direction, then flow, then
        filled, then falls through to the tonal ramp/density pair.

      * `Fl4683RoleFromFactsV1` in the producer source fills in the scene fact's
        `role` byte.

      * `Fl4683RouteFamilyMaskFromFactsV1` in the producer source fills in the
        scene fact's `route_family_mask` byte.

    HISTORY, kept so the receipt does not read as if this was always true. The
    role byte previously tested the filled fact SECOND, before vertical
    relation, while the live owner tests it FIFTH, after flow. For a cell
    carrying both a filled bit and a vertical-relation bit the two classified
    the cell differently. The prior measurement recorded that divergence under
    `divergence_open: true` instead of fixing it. Operator ruling 4 (2026-08-06)
    directed the fix in the role byte. A later canonical Pieta runtime receipt
    then showed that the unchanged route-family helper omitted the live owner's
    leading missing-mesh fence and fell blocked cells through to the tonal pair.
    The route helper now carries that same first branch and returns mask zero for
    missing authority.

    WHAT AGREEMENT DOES AND DOES NOT MEAN. It means these three functions would
    label the same cell the same way. It does NOT mean the role byte is consumed
    anywhere at runtime -- `runtime_consumption_state` in this same receipt still
    records zero compiled addresses consumed and zero loader call sites in the
    live frame path. Structural agreement is not coverage.
    """
    live_order = [
        "missing_mesh_fact_fail_closed",
        "mesh_feature",
        "vertical_relation",
        "direction",
        "flow",
        "filled",
        "tonal_ramp_density_pair",
    ]
    return {
        "live_owner": {
            "function": "Fl4260ProfileSelectGlyph",
            "source": "engine/fl4131_runtime_harri_resolver.cpp",
            "source_line": 2922,
            "order": live_order,
        },
        "scene_fact_role_byte": {
            "function": "Fl4683RoleFromFactsV1",
            "source": PRODUCER_SOURCE.as_posix(),
            "order": list(live_order),
            "follows": "live_owner",
        },
        "route_family_mask": {
            "function": "Fl4683RouteFamilyMaskFromFactsV1",
            "source": PRODUCER_SOURCE.as_posix(),
            "order": list(live_order),
            "follows": "live_owner",
            "changed_by_the_role_byte_correction": False,
            "missing_authority_fence": True,
            "changed_by_missing_authority_correction": True,
        },
        "divergence": "",
        "divergence_open": False,
        "corrected_by": (
            "FL-4683 role-byte correction, operator ruling 4 of 2026-08-06: "
            "Fl4683RoleFromFactsV1 was moved onto the live owner's branch order "
            "(filled fifth, after flow) and given the live owner's fail-closed "
            "missing-mesh-fact first branch. The subsequent runtime correction "
            "added the same leading fence to Fl4683RouteFamilyMaskFromFactsV1 "
            "so blocked mesh claims return route mask zero instead of tonal."
        ),
        "prior_divergence_for_the_record": (
            "the role byte tested the filled fact before vertical relation while "
            "the live owner tests it after flow; a cell carrying both bits was "
            "classified differently by the two"
        ),
    }


def build_discriminator_block(
    rows: List[Dict[str, Any]], capability: Dict[str, Any]
) -> Dict[str, Any]:
    """Record what the route-family discriminator is and what it resolved.

    Parameters:
      rows       -- the classified per-address rows.
      capability -- what `read_producer_capability` found in the source.

    This block exists so a reader of the receipt alone can see WHY the
    ambiguity count is what it is, without having to diff two versions of the
    file.
    """
    # How many addresses share their role code with at least one other route
    # family. Before the discriminator existed, every one of these was
    # unresolvable; now each is named by its family bit instead.
    sharing_role_code = [row for row in rows if row["shares_role_code_with_route_keys"]]
    return {
        "scene_fact_field": "route_family_mask",
        "record_header": SCENE_FACT_RECORD.as_posix(),
        "classifier": capability["route_family_classifier"],
        "emitted_route_keys": capability["emitted_route_keys"],
        "emits_tonal_ramp_density_pair": capability["emits_tonal_ramp_density_pair"],
        "addresses_sharing_a_role_code_with_another_route_family": len(
            sharing_role_code
        ),
        "resolved_by_the_discriminator": (
            "each of those addresses is now named by its own route-family bit "
            "plus its lane field, so no selector has to guess which family a "
            "tonal cell meant"
        ),
        "role_priority_orders": read_role_priority_orders(),
    }


def build_report() -> Dict[str, Any]:
    """Assemble the whole measurement."""
    artifact = load_json(GLYPH_FACT_ARTIFACT)
    capability = read_producer_capability()
    classified = classify_addresses(artifact, capability)
    rows = classified["rows"]
    scene_fact_fields = read_scene_fact_fields()
    loader_audit = static_loader_reachability()

    # Which renderer-owned fields does any compiled address depend on?
    fields_used = sorted(
        {
            row["lane_selecting_scene_fact_field"]
            for row in rows
            if row["lane_selecting_scene_fact_field"]
            and row["lane_selecting_scene_fact_field"] != "unmapped"
        }
        | {"material_key", "material_namespace", "role", "route_family_mask"}
    )
    fields_unused = [
        name
        for name in scene_fact_fields
        if name not in fields_used
        and name not in ("schema_version", "valid", "payload_sha256")
    ]

    return {
        "schema": "fl4684.compiled_address_reachability.v1",
        "version": 2,
        "artifact_kind": "compiled_address_producer_reachability_measurement",
        "generated_by": "scripts/measure_fl4684_compiled_address_reachability.py",
        "evidence_kind": "static_source_and_artifact_measurement",
        "evidence_scope": (
            "Compares the compiled address labels against what the renderer's "
            "scene-fact producer is capable of emitting. Reads the compiled "
            "artifact and producer/record sources, then performs the bounded "
            "tracked engine/platform and root build-configuration audit owned "
            "by the canonical FL-4683 compiler. Runs no game, opens no window, "
            "and draws no pixel."
        ),
        "sources": {
            "compiled_artifact": GLYPH_FACT_ARTIFACT.as_posix(),
            "producer_source": PRODUCER_SOURCE.as_posix(),
            "scene_fact_record": SCENE_FACT_RECORD.as_posix(),
            "loader_audit": "scripts/compile_fl4683_glyph_program.py",
            "loader_audit_source_scope": ["engine/", "platform/"],
            "loader_audit_build_scope": [
                "makefile*",
                "Makefile*",
                "build*.sh",
            ],
        },
        "producer_capability": capability,
        "route_family_discriminator": build_discriminator_block(rows, capability),
        "summary": summarise(rows),
        "producer_value_gaps": find_producer_values_without_a_compiled_address(
            rows, capability
        ),
        "scene_fact_fields_no_compiled_address_depends_on": fields_unused,
        "requires_live_evidence": [
            # Everything a static read genuinely cannot settle. Naming these
            # keeps the counts above from being mistaken for coverage.
            "whether any normal play session emits a scene fact for each "
            "material key the compiled addresses reference",
            "whether the producer's per-cell role classification lands on the "
            "distribution the compiled addresses assume",
            "whether native product startup reaches "
            "Fl4693ProductGlyphProgramStartV1 and promotes the validated view",
            "whether native and Web gameplay reach "
            "Fl4693ProductGlyphSelectV1 and Fl4683ExactCellSelectV1",
            "whether same-run runtime prediction rows and final tuples prove "
            "the selected compiled address was presented as the visible glyph",
        ],
        "runtime_consumption_state": {
            # These are three different evidence planes. The serialized byte
            # remains blocked even though source/build reachability is present.
            "serialized_sidecar": {
                "runtime_consumption_allowed": False,
                "header_byte_offset": 160,
                "header_byte_value": 0,
                "evidence_kind": "compiled_header_contract",
            },
            "static_loader_reachability": {
                "entry_symbol": loader_audit["entry_symbol"],
                "measured_call_site_count": loader_audit[
                    "measured_call_site_count"
                ],
                "call_sites": loader_audit["call_sites"],
                "scope_ceiling": loader_audit["scope_ceiling"],
            },
            "runtime_execution": {
                "compiled_addresses_consumed_at_runtime": None,
                "evidence_kind": "not_observed_by_static_measurement",
                "scope_ceiling": "requires_live_runtime_receipt",
            },
        },
        "addresses": rows,
    }


def serialize(report: Dict[str, Any]) -> bytes:
    """Render the report as canonical JSON so repeated runs are byte-identical."""
    return json.dumps(report, sort_keys=True, indent=1).encode("utf-8") + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure how many compiled addresses the scene-fact producer can land on."
    )
    parser.add_argument("--write", action="store_true", help="write the measurement to disk")
    parser.add_argument(
        "--check", action="store_true", help="exit non-zero if the file on disk is stale"
    )
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write and --check")

    try:
        payload = serialize(build_report())
    except MeasureError as exc:
        print(f"FL-4684 reachability measurement failed: {exc}", file=sys.stderr)
        return 2

    path = ROOT / OUTPUT
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"wrote {OUTPUT} bytes={len(payload)}")
        return 0

    if not path.is_file():
        print(f"{OUTPUT} is missing", file=sys.stderr)
        return 1
    if path.read_bytes() != payload:
        print(f"{OUTPUT} is stale", file=sys.stderr)
        return 1
    print(f"ok {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

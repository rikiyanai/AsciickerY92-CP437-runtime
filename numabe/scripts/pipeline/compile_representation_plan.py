#!/usr/bin/env python3
"""Compile the representation plan ABOVE the lowerers (FL-4809 PLAN-02).

Every disposition derives from upstream authority only: the canonical World
IR, canonical entities, observation debt, the triage receipt, the rules
representation_policy DEFAULT fact, and the regenerated OSM IR (elevation
join). Lowerer emissions (fixture instances, front receipts, surface
receipts, ledgers) are never read: a semantic place keeps its site identity
without claiming a physical sign exists, and any physical stand-in is an
explicit synthetic_identity_proxy with asserts_physical_presence=false.

Schema v2 row: feature_id, canonical_id, semantic_class,
reconstruction_class, importance, evidence_state, disposition,
representation_kind, asserts_physical_presence, evidence_complete,
policy_rule, representation_id, owner_stage, reason, source_bridge, primary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
import sys as _sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from scripts.pipeline.canonical_attachment import (  # noqa: E402
    CanonicalIndex, attach, attach_building_facade, attach_freestanding, face_heading,
    polygons_overlap, reconcile_mesh_fixture_footprint,
    reconcile_surface_decal)
from scripts.pipeline.plan_spatial_provenance import stamp as stamp_provenance  # noqa: E402
from scripts.pipeline.authored_waivers import (  # noqa: E402
    SCHEMA as AUTHORED_WAIVER_SCHEMA,
    # One hashing law for what an approval is bound to, shared by the producer
    # that writes a waiver row and this consumer that honours it.
    condition_digest,
    # Re-exported so the orchestrator catches a bad approval document without
    # having to know which module owns the waiver schema.
    AuthoredWaiverInvalid,  # noqa: F401
    assert_all_exercised,
    load_authored_waivers,
)
from scripts.pipeline.authored_mapillary_decisions import (  # noqa: E402
    MapillaryDecisionInvalid,  # noqa: F401  (re-exported for the orchestrator)
    load_mapillary_decisions,
)
from scripts.pipeline.compile_route_importance import (  # noqa: E402
    # Re-exported so the orchestrator catches the planner's refusal without
    # having to know which module owns the route-importance schema.
    RouteImportanceInvalid,  # noqa: F401
    load_route_importance,
)

SCHEMA = "y92.representation_plan.v2"
PLANNER_VERSION = (
    SCHEMA + ":R-STREET-TYPED-JEV:R-STREET-MARKING:R-SPATIAL-OWNER-V3")

DISPOSITIONS = (
    "terrain_mass", "surface_decal", "mesh_fixture", "mesh_facade",
    "minimap_only", "intentional_omission", "observation_owed",
)

KINDS = (
    "observed_physical", "synthetic_identity_proxy", "terrain", "surface",
    "label", "map_only", "omitted", "owed",
)

# FL-4809 R8: how the bake stage realizes a building's mass. This is a field
# on the terrain_mass row rather than a disposition of its own, deliberately.
# The disposition names the OWNER of a representation, and both realizations
# are owned by bake against the same building spec; promoting the realization
# to a disposition would instead force every disposition consumer to relearn
# the vocabulary -- the preassembly gate fails closed on an unknown value, and
# three consumers index the plan last-wins on a sort that includes the
# disposition string. A first-class mesh_building_shell disposition is still
# the right end state; it needs those five consumers changed together, which
# is a different repair from removing the authority inversion.
# FL-4876. A triage-unmapped source_ref is not a canonical feature, and this
# planner mints a primary row for one anyway -- deliberately, so that a thing
# triage could not map is recorded rather than silently absent. See
# expected_primary below: it is len(by_fid) + len(unmapped).
#
# The preassembly gate independently required every primary row's feature to
# exist in the canonical world, so those rows read as dangling references and
# blocked assembly. Two owners, two contracts, no shared statement of the
# fact. The gate imports the contract below instead of guessing which
# omissions are legitimate, and every field must match: a forged "unmapped:"
# id that does not carry the whole shape is still an identity error.
# FL-4876. Which owner_stage values actually reach the population
# compiler. A mesh row owned by anything else is emitted by a different
# stage, so asking population for its placement produces a true refusal with
# a false reason -- "no matching dispatched logical population placement"
# sends a reader hunting through population for a row population was never
# given. place_fronts is the live case: it is not one of
# dispatch_population_plan.OWNERS, zero of 2761 placements carry a
# place_front representation id, and its lowerer is never invoked by the
# production runner.
POPULATION_OWNER_STAGES = frozenset({
    "fixtures", "population_fixtures", "population_linear",
})
# Owner stages that emit outside population, with the module that owns each.
# Recorded so the gate can name the real owner instead of guessing.
NON_POPULATION_OWNER_STAGES = {
    "place_fronts": "scripts.pipeline.compile_world_ir_fixtures."
                    "compile_world_ir_place_fronts",
    # FL-4876 INFRA-01. A railway grade crossing is not a street fixture.
    # Its asset is an authored ``railway_crossing_profiles`` entry
    # (jp.urban.railway_grade_crossing), and the only code that can build one
    # is compile_world_ir_infrastructure, which consumes plan rows directly
    # through ``plan_crossing_rows``. Stamping these rows "fixtures" sent
    # them to population, which has no crossing module, so four fully
    # resolved crossings became population_owner blockers reading "no
    # matching logical population placement" -- a true refusal with a false
    # owner.
    "infrastructure": "scripts.pipeline.compile_world_ir_fixtures."
                      "compile_world_ir_infrastructure",
}
# FL-4876 PLACE-FRONT-01. Naming an owner is not connecting one. These are
# the non-population owner stages whose emitter the production runner
# actually invokes, with the token that proves it; a source test asserts each
# token is still present in scripts/sbu_e2e_run.py, so deleting the call site
# fails a test instead of silently re-opening the hole. The gate may treat a
# stage as satisfied only when it appears here AND the owner's plan slice
# carries the row.
WIRED_NON_POPULATION_OWNERS = {
    "place_fronts": "compile_world_ir_place_fronts(",
    "infrastructure": "compile_world_ir_infrastructure(",
}
# The plan-by-owner slice each wired stage is handed, relative to the
# directory the planner writes.
NON_POPULATION_OWNER_SLICES = {
    "place_fronts": "place_fronts.json",
    "infrastructure": "infrastructure.json",
}

# FL-4876. The declared product scope. aoi.direct.geojson's "aoi" feature
# carries route_lateral_buffer_m 55.0 and route_endpoint_buffer_m 75.0, and
# other roles in that file describe themselves as "verified inside the
# connected AOI ring", so the ring is the product boundary, written down
# rather than inferred.
#
# Canonical ingest keeps features on both sides of it -- osm_ingest_receipt
# counts anchor_count_inside_aoi and anchor_count_outside_aoi separately --
# and this planner admitted them all. The population compiler applies the
# ring to poles and only to poles (compile_population_plan.py:4092), so 21
# canonical poles reached the gate promised and unplaceable, along with 87
# street fixtures whose absence had a different cause. Measured outside
# distances for those poles: median 43.3 m, max 197.4 m. They are not
# boundary noise.
#
# A street feature outside the declared scope is omitted here, with its
# measured distance, instead of being promised to a stage that will not
# build it.
AOI_SCOPE_ROLE = "aoi"
AOI_SCOPE_POLICY = "representation_policy:outside_declared_aoi"

# FL-4876 STATION-DUP-01. Two Mapillary detections of the same class closer
# than this are one object seen twice (two frames, two panoptic segments), so
# they become one fixture. Measured on the Numabe lane: the second head of
# signal 549495626481668 (549495629815001) sits 0.9 m from it, and was built
# 8.2 m away on the other side of the track.
MAPILLARY_SAME_CLASS_MERGE_M = 1.5
# An OSM level crossing whose authored level_crossing_equipment fixtures stand
# within this distance is already drawn by them; the infrastructure stage's
# own crossing proposal would be a third set of the same device.
AUTHORED_CROSSING_GATE_RADIUS_M = 15.0


def mapillary_class_key(feature: dict) -> tuple[str, str]:
    """Class for the same-object merge: semantic class plus object value, with
    the variant suffix of a traffic light or traffic sign dropped (front/back
    and head variants of one post are one device)."""
    raw = _claim_value(feature, "object_value")
    value = raw if isinstance(raw, str) else ""
    for family in ("object--traffic-light", "object--traffic-sign"):
        if value.startswith(family):
            value = family
    return (str(feature.get("semantic_class")), value)


def _ring_from_aoi(aoi_document, transform):
    for feature in (aoi_document or {}).get("features", []):
        if (feature.get("properties") or {}).get("role") != AOI_SCOPE_ROLE:
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Polygon":
            continue
        return [transform(x, y) for x, y in geometry["coordinates"][0]]
    return None


def point_outside_ring_m(point, ring):
    """Distance outside ``ring``, or 0.0 when inside. None without a ring."""
    if not ring or point is None:
        return None
    east, north = point
    inside = False
    count = len(ring)
    for index in range(count):
        ax, ay = ring[index]
        bx, by = ring[(index + 1) % count]
        if (ay > north) != (by > north):
            crossing = ax + (north - ay) * (bx - ax) / ((by - ay) or 1e-12)
            if crossing > east:
                inside = not inside
    if inside:
        return 0.0
    best = float("inf")
    for index in range(count - 1):
        ax, ay = ring[index]
        bx, by = ring[index + 1]
        dx, dy = bx - ax, by - ay
        squared = dx * dx + dy * dy
        t = 0.0 if squared <= 0 else max(0.0, min(
            1.0, ((east - ax) * dx + (north - ay) * dy) / squared))
        best = min(best, math.hypot(
            east - (ax + t * dx), north - (ay + t * dy)))
    return best


UNMAPPED_FEATURE_PREFIX = "unmapped:"

# FL-4876. Exempting every triage-unmapped row from the identity check was
# too broad, and manual inspection of this lane's five is what showed it:
#   node/10669964996  junction=yes              topology annotation
#   node/297026604    junction=yes + name       topology annotation
#   way/1315640319    boundary=administrative   a legal line
#   way/1304205921    man_made=bridge, layer=1  a BRIDGE
#   way/264846635     landuse=brownfield        a land parcel
 # Three are genuinely not physical world features. Two are, and exempting
 # them silenced the only complaint the pipeline was making about a bridge
 # missing from a reconstructed neighbourhood.
 #
 # FL-4876 BRIDGE-DECK-01 resolves the bridge row, not by exemption but by
 # classification: closed man_made=bridge outlines ingest as bridge_deck and
 # route with the ground-surface classes. With layer=1 the at-grade stamper
 # omits the deck by stated rule (R-OMIT) while the corridor rails, which
 # carry their own bridge=yes claims, reconstruct above. Deck sides and
 # parapets remain unmodeled: that residual is documented here and in the
 # delivery record, not discovered later as a silent absence.
 #
 # So classify by the tags triage already records. Fail closed: a defining key
# this list has never heard of is treated as physical, because the cost of a
# wrong "nothing here" is an object silently absent from the world.
UNMAPPED_METADATA_TAG_KEYS = frozenset({
    "name", "source", "note", "ref", "description", "wikidata", "wikipedia",
    "fixme", "created_by", "layer",
})
NON_PHYSICAL_UNMAPPED_TAG_KEYS = frozenset({
    "junction", "boundary", "admin_level",
})
UNMAPPED_NON_PHYSICAL = "non_physical_annotation"
UNMAPPED_PHYSICAL = "physical_without_registry_class"


def classify_unmapped(tags):
    """Whether a triage-unmapped record describes a physical thing."""
    defining = {
        key for key in (tags or {})
        if key not in UNMAPPED_METADATA_TAG_KEYS
        and not key.startswith("name:")
        and not key.startswith("source:")
    }
    if not defining or defining <= NON_PHYSICAL_UNMAPPED_TAG_KEYS:
        return UNMAPPED_NON_PHYSICAL
    return UNMAPPED_PHYSICAL
UNMAPPED_ROW_CONTRACT = {
    "semantic_class": "unmapped",
    "evidence_state": "unmapped",
    "disposition": "intentional_omission",
    "representation_kind": "omitted",
    "owner_stage": "none",
    "policy_rule": "representation_policy:unmapped_rule",
    "source_bridge": "triage:triage01:unmapped_with_reason",
}

MASS_REALIZATIONS = ("terrain_lattice", "mesh_shell")

# A lattice cannot carry a facade, so a building the player walks past needs
# real geometry. Importance is the level-of-information-need axis and is owned
# by this planner; nothing downstream may re-derive it.
MESH_SHELL_IMPORTANCE = ("route_visible", "landmark", "gameplay_critical")

# Existing lowerer ownership for observed fixture classes. infrastructure
# and vegetation own no canonical class today; the mapping stands so a
# future utility_pole/tree class routes without a rule change.
FIXTURE_OWNER = {
    "fixture": "fixtures", "sign": "fixtures", "crossing": "fixtures",
    "utility_pole": "infrastructure", "tree": "vegetation",
}

_MISSING = object()
_FALSY_TAGS = {"no", "false", "0", None}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_value(feature: dict, dotted: str):
    """A canonical claim VALUE by dotted property path; _MISSING if absent."""
    node = feature.get("properties", {})
    for part in dotted.split("."):
        if isinstance(node, dict) and "value" in node and part not in node:
            node = node["value"]
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _match_proxy(row: dict, feature: dict, triage_group: str | None) -> bool:
    """Every condition in the policy match block must hold (claim values)."""
    match = row["match"]
    if match.get("semantic_class") != feature.get("semantic_class"):
        return False
    if "geometry_kind" in match and match["geometry_kind"] != \
            feature.get("geometry", {}).get("kind"):
        return False
    if "triage_group" in match and match["triage_group"] != triage_group:
        return False
    prop = match.get("claim_property")
    if prop is not None:
        value = _claim_value(feature, prop)
        if match.get("claim_absent") and value is not _MISSING:
            return False
        if "claim_value" in match and value != match["claim_value"]:
            return False
        prefix = match.get("claim_value_prefix")
        if prefix is not None and not (
                isinstance(value, str) and value.startswith(prefix)):
            return False
        if "claim_null" in match and (value is None) != match["claim_null"]:
            return False
    return True


def _grade(elevation: dict | None) -> str:
    """above | below | grade from a verbatim elevation_relation value."""
    if not elevation:
        return "grade"
    bridge = elevation.get("bridge")
    layer = elevation.get("layer")
    tunnel = elevation.get("tunnel")
    try:
        level = None if layer is None else float(layer)
    except (TypeError, ValueError):
        level = None
    if (bridge not in _FALSY_TAGS) or (level is not None and level > 0):
        return "above"
    if (level is not None and level < 0) or \
            (tunnel not in _FALSY_TAGS and not (
                level is not None and level > 0)):
        return "below"
    return "grade"

# PLAN-02 amendment: masses are carriers with faces, not bespoke modelling
# tasks. Asset selection is claim-based and validated against the authored
# vocabulary; anything unjustifiable is new_asset_required naming the gap.
SHOP_USE_PREFIX = ("shop=",)
COMMERCIAL_MODULE = "narrow_mixed_use"


def load_asset_vocabulary(catalog_path: Path, pack_path: Path) -> dict:
    """Authored-asset denominator: modules, pack templates/profiles/rail."""
    catalog = _load(catalog_path)
    modules = catalog.get("modules", [])
    by_domain: Counter = Counter()
    module_ids = set()
    for module in modules:
        by_domain[module.get("owning_domain", "?")] += 1
        module_ids.add(module["module_id"])
    templates: set[str] = set()
    profiles: set[str] = set()
    rail: set[str] = set()
    footprints: dict[str, tuple[float, float]] = {}
    pack_files = []
    queue = [pack_path]
    seen = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        pack = _load(current)
        pack_files.append(str(current))
        for template in pack.get("templates", []):
            templates.add(template["template_id"])
            # FL-4876 FOOTPRINT-01. The declared ground footprint, kept
            # beside the id so the planner can reconcile a fixture's real
            # extent the same way it already reconciles a decal's.
            _physical = template.get("physical") or {}
            _bbox = _physical.get("native_bbox_m")
            if isinstance(_bbox, list) and len(_bbox) == 3:
                footprints.setdefault(
                    template["template_id"],
                    (float(_bbox[0]), float(_bbox[1])))
        for profile in pack.get("place_front_profiles", []):
            profiles.add(profile["id"])
        for profile in pack.get("railway_crossing_profiles", []):
            rail.add(profile["id"])
        for parent in pack.get("parents", []):
            candidate = current.parent.parent / parent / "pack.json"
            if candidate.is_file():
                queue.append(candidate)
    return {"module_ids": module_ids, "by_domain": dict(by_domain),
            "templates": templates, "profiles": profiles, "rail": rail,
            "template_footprints_m": footprints,
            "pack_files": pack_files}




def row_is_street_match(feature: dict) -> bool:
    """Fused Mapillary sightings match templates, never generic fixtures."""
    return (feature.get("feature_id", "") or "").startswith(
        "feature:mapillary_object/")


def street_template_match(feature: dict, template_rows) -> tuple[str, str | None]:
    """Pack template by the unique longest matching rules token."""
    if feature.get("semantic_class") == "road_marking":
        return ("none", None)
    oval = str(_claim_value(feature, "object_value") or "")
    matches = [(token, template_id) for token, template_id in template_rows
               if token in oval]
    if matches:
        longest = max(len(token) for token, _template_id in matches)
        candidates = sorted({template_id for token, template_id in matches
                             if len(token) == longest})
        if len(candidates) == 1:
            return ("catalog:" + candidates[0], None)
        return ("new_asset_required",
                "ambiguous longest token match for %s: %s" % (
                    oval, candidates))
    return ("new_asset_required", "template for %s" % oval)


def match_asset(row: dict, feature: dict, ctx=None) -> tuple[str, str | None]:
    """(asset_match, asset_missing_claim) for one plan row, claim-based.

    ctx carries rules tables plus the OSM-IR claim overlay
    {fid: {fixture_archetype, use_class, religion}}; every branch reads
    claims, never tags."""
    if ctx is None:
        raise ValueError(
            "asset context missing from rules; regenerate "
            "rules_resolved.json with compile_rules_resolved.py")
    disp, kind = row["disposition"], row["representation_kind"]
    jev = row.get("jev_decision") or {}
    jev_representation = jev.get("representation")
    jev_asset_choice = jev.get("asset_choice")
    if jev_representation == "existing_asset":
        if not jev_asset_choice or jev_asset_choice == "none":
            return ("new_asset_required",
                    "Jev existing_asset decision has no asset_choice")
        return ("catalog:" + str(jev_asset_choice), None)
    if jev_representation == "new_asset_required":
        return ("new_asset_required", "Jev reconstruction decision")
    if jev_representation == "observation_debt":
        return ("observation_debt", "Jev reconstruction decision")
    if jev_representation == "procedural_template":
        if row.get("disposition") == "surface_decal":
            return ("procedural:surface_decal", None)
        return ("new_asset_required",
                "Jev procedural_template has no typed consumer")
    overlay = (ctx.get("overlay") or {}).get(row["feature_id"], {})
    if disp == "mesh_facade":
        category = _claim_value(feature, "place_category")
        religion = overlay.get("religion")
        if category == "amenity=place_of_worship" and religion in (
                None, "buddhist"):
            return ("catalog:jp.urban.buddhist_place_entry_gate", None)
        use = overlay.get("use_class")
        if use is None:
            claimed_use = _claim_value(feature, "use_class")
            if claimed_use is not _MISSING:
                use = claimed_use
        if use is None:
            if isinstance(category, str) and \
                    category.startswith(SHOP_USE_PREFIX):
                return ("catalog:" + COMMERCIAL_MODULE, None)
            # The population planner already resolves buildings without a
            # specific use claim through this exact rules-owned default.  The
            # representation planner must consume the same fact rather than
            # silently denying those buildings a facade.
            default = (ctx.get("facade_by_use") or {}).get("residential", {})
            if feature.get("semantic_class") == "building_footprint" \
                    and default.get("value"):
                return ("catalog:" + str(default["value"]), None)
            return ("new_asset_required",
                    "use-class determination for %s" % category)
        module = (ctx.get("facade_by_use") or {}).get(use, {}).get("value")
        if module:
            return ("catalog:" + module, None)
        return ("new_asset_required",
                "facade table row for use class %s" % use)
    if disp == "mesh_fixture" and kind == "synthetic_identity_proxy":
        target = row["representation_id"].split(":")[1] \
            if row["representation_id"].startswith("proxy:") else ""
        if target == "station_stop":
            return ("catalog:jp.urban.generic_sign_plate_v1", None)
        if target == "pedestrian_gate":
            return ("catalog:jp.urban.pedestrian_gate_v1", None)
        if target == "station_platform":
            # FL-4876 STATION-STOP-MAP-01: built by the infrastructure owner from the locale's
            # station_composition_profiles (compile_world_ir_fixtures resolve_data_profile), a pack chain this
            # plan's catalog vocabulary does not walk; named by its own prefix, not as a catalog asset.
            return ("station_composition:jp.tokyo.small_station_side_platform", None)
        return ("catalog:" + {
            "information_board": "jp.urban.information_board_v1",
            "crossing_marker": "jp.urban.railway_grade_crossing",
        }.get(target, "unknown_proxy_target"), None)
    # Fused street sightings match pack templates by rules substring rows;
    # paint has no asset; anything else names its gap. Ordered first hit.
    if disp in ("mesh_fixture", "observation_owed", "surface_decal") and \
            row_is_street_match(feature):
        return street_template_match(
            feature, (ctx.get("template_rows") or []))
    if disp == "mesh_fixture" and kind == "observed_physical":
        if feature.get("semantic_class") == "crossing" and \
                _claim_value(feature, "cross_section.highway") is not None \
                and _claim_value(feature, "cross_section.highway") \
                is not _MISSING:
            return ("catalog:jp.urban.pedestrian_crossing_marker_v1", None)
        arch = overlay.get("fixture_archetype")
        if arch is not None:
            template = (ctx.get("archetype_rows") or {}).get(arch)
            if template:
                return ("catalog:" + template, None)
            return ("new_asset_required",
                    "template row for archetype %s" % arch)
        return ("new_asset_required",
                "fixture archetype key claim (tag-to-archetype mapping "
                "not emitted as a canonical claim)")
    return ("none", None)




def _load_route_xy(route_path) -> list | None:
    """Pedestrian route polyline in EPSG:32654, same source the inventory
    uses (Valhalla shape via geo_frames). None when no route is given:
    containing-ring faces then fall back to carrier proximity."""
    if route_path is None:
        return None
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "plan_route_inventory_mod",
        str(Path(__file__).resolve().parent
            / "compile_route_streetscape_inventory.py"))
    _inv = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_inv)
    from scripts.pipeline.geo_frames import utm_forward as _fwd
    route_doc = json.loads(Path(route_path).read_text(encoding="utf-8"))
    shape = route_doc["trip"]["legs"][0]["shape"]
    return [_fwd(lon, lat) for lon, lat in _inv._decode_polyline6(shape)]


def resolve_importance(route_table: dict, debt_by_fid: dict, fid: str) -> str:
    """Level of information need for one feature. One owner, one order.

    The route receipt is consulted first because it scores EVERY canonical
    feature; the debt row second because it scores only the 17 B/C triage
    features; ``background`` is the floor. Supplying an empty ``route_table``
    -- which is what an absent receipt produces -- yields exactly the answer
    this function gave before FL-4809 R1, so the no-receipt path is the old
    path rather than a new approximation of it.

    Module level, not a closure inside ``build()``, so a test can exercise
    the shipped resolution instead of a copy of it.
    """
    routed = route_table.get(fid)
    if routed is not None:
        return routed
    debt_row = debt_by_fid.get(fid)
    if debt_row is not None:
        return debt_row.get("importance", "background")
    return "background"


def debt_policy_key_for(importance: str, group: str | None) -> str:
    """Select the debt policy for an importance/triage-group pair.

    HAZARD, pinned in scripts/tests/test_fl4809_plan_importance_source.py:
    this has no ``route_visible_A``. Every building is triage group A, so a
    building promoted to ``route_visible`` lands on ``route_visible_C``,
    whose policy is "observation_owed blocks evidence_complete", and
    ``compile_preassembly_candidate`` refuses a run on
    ``evidence_complete: false``. Whoever routes buildings through this must
    give them an evidence disposition in the same change.
    """
    if importance == "background":
        return "background_A"
    if group == "B":
        return "route_visible_B"
    return "route_visible_C"


def build(canonical_path: Path, entities_path: Path, debt_path: Path,
          triage_receipt_path: Path, rules_path: Path,
          osm_ir_path: Path, catalog_path: Path,
          pack_path: Path, jev_path=None, route_path=None,
          fusion_path=None, terrain_metadata_path=None,
          route_importance_path=None,
          authored_waivers_path=None,
          aoi_path=None, authored_tenancy_path=None,
          authored_mapillary_decisions_path=None,
          authored_fixtures_path=None) -> tuple[dict, dict]:
    canon = _load(canonical_path)
    entities = _load(entities_path)
    debt = _load(debt_path)
    triage = _load(triage_receipt_path)
    rules = _load(rules_path)
    osm_ir = _load(osm_ir_path)

    try:
        policy = rules["representation_policy"]
        proxy_table = policy["semantic_identity_proxy"]
        debt_policy = policy["debt_policy_by_importance"]["value"]
        omit_rule = policy["elevated_surface_omission"]["value"]
        observed = policy["observed_defaults"]["value"]
        marking_fact = policy["street_marking"]
        marking_policy = marking_fact["value"]
        for required in ("below_grade_omission", "unmapped_rule"):
            if required not in policy:
                raise ValueError(
                    "representation_policy DEFAULT fact missing "
                    f"'{required}' from rules; regenerate "
                    "rules_resolved.json with compile_rules_resolved.py")
    except KeyError as exc:
        raise ValueError(
            "representation_policy DEFAULT fact missing from rules "
            f"(absent key {exc}); regenerate rules_resolved.json with "
            "compile_rules_resolved.py") from exc
    ground_classes = observed["ground_surface_classes"]
    fixture_classes = observed["physical_fixture_classes"]
    mass_class = observed["mass_class"]

    features = canon["canonical_features"]
    by_fid = {f["feature_id"]: f for f in features}
    osm_by_fid = {f["feature_id"]: f for f in osm_ir["canonical_features"]}
    observations = {o["observation_id"]: o
                    for o in canon["source_observations"]}

    # FL-4876 STATION-DUP-01: authored per-object Mapillary decisions
    # (reject / minimap_only / move_to). A move rewrites the feature's point
    # for every later step of this planner; the canonical document on disk is
    # not touched.
    mapillary_decisions = load_mapillary_decisions(
        authored_mapillary_decisions_path, known_feature_ids=by_fid.keys())
    mapillary_decisions_applied: list[dict] = []
    for _mfid, _mrow in mapillary_decisions.items():
        if _mrow["action"] == "move_to":
            _moved = dict(by_fid[_mfid])
            _moved["geometry"] = dict(
                _moved.get("geometry") or {}, kind="point",
                coordinates=[float(_mrow["move_to"]["E"]),
                             float(_mrow["move_to"]["N"])])
            by_fid[_mfid] = _moved
    # Authored level-crossing equipment that stands in the world (accepted
    # rows only; a rejected row draws nothing).
    authored_gate_points: list[tuple[str, float, float]] = []
    if authored_fixtures_path is not None and Path(
            authored_fixtures_path).is_file():
        for _fx in _load(Path(authored_fixtures_path)).get("fixtures", []):
            if (_fx.get("module_id") == "level_crossing_equipment"
                    and (_fx.get("manual_review") or {}).get(
                        "status") == "accepted"):
                authored_gate_points.append(
                    (str(_fx["fixture_id"]), float(_fx["E"]),
                     float(_fx["N"])))
    crossing_suppressed: list[dict] = []
    # Same-class Mapillary detections already built as one fixture, for the
    # within-1.5 m merge: class key -> [(feature_id, E, N)].
    mapillary_built: dict[tuple, list] = {}
    mapillary_merges: list[dict] = []

    def _evidence_year(feature: dict) -> int | None:
        """Newest explicit source year, then dataset id, then observation date."""
        source_years = []
        dataset_years = []
        observed_years = []
        for oid in feature.get("supporting_observation_ids", []):
            obs = observations.get(oid) or {}
            attrs = obs.get("attributes") or {}
            source_years.extend(int(year) for year in re.findall(
                r"(?:19|20)\d{2}", str((attrs.get("tags") or {}).get("source", ""))))
            dataset_years.extend(int(year) for year in re.findall(
                r"(?:19|20)\d{2}", str(obs.get("source_id") or "")))
            observed_years.extend(int(year) for year in re.findall(
                r"(?:19|20)\d{2}", str(obs.get("observed_at") or "")))
        years = source_years or dataset_years or observed_years
        return max(years) if years else None

    def _source_tags(feature: dict) -> dict:
        tags = {}
        for oid in feature.get("supporting_observation_ids", []):
            tags.update((observations.get(oid, {}).get("attributes") or {})
                        .get("tags") or {})
        return tags

    # FL-4876 SLOT-OWNER-01: storey of a tenancy. OSM level tag first; else an
    # operator-approved row of authored_place_tenancy.json (verbatim decision
    # record required). Unknown -> None (never guessed).
    _authored_tenancy = {}
    if authored_tenancy_path is not None and Path(authored_tenancy_path).is_file():
        for _t in _load(authored_tenancy_path).get("tenancies", []):
            if (_t.get("status") == "approved" and _t.get("decided_by") == "operator"
                    and _t.get("decision_record") and _t.get("place_feature_id")):
                _authored_tenancy[_t["place_feature_id"]] = int(_t["level"])

    def _tenancy_level(feature: dict):
        raw = str(_source_tags(feature).get("level") or "").split(";")[0].strip()
        try:
            return ("osm_level_tag", max(0, int(float(raw))))
        except ValueError:
            pass
        if feature["feature_id"] in _authored_tenancy:
            return ("operator_authored_tenancy", _authored_tenancy[feature["feature_id"]])
        return None

    canon_by_ref = {}
    for disp in entities.get("dispositions", []):
        canon_by_ref[disp["source_object_id"]] = disp["canonical_id"]
    foot_state = {}
    for entity in entities.get("entities", []):
        claims = entity.get("claims", {})
        if "footprint" in claims:
            for ref in [d["source_object_id"] for d in
                        entities.get("dispositions", [])
                        if d["canonical_id"] == entity["canonical_id"]]:
                foot_state[ref] = claims["footprint"]["evidence_state"]

    debt_by_fid = {f["feature_id"]: f for f in debt.get("features", [])}
    group_of = {row["feature_id"]: row["group"]
                for row in triage["triage"]["by_group"]}
    unmapped = triage["triage"]["unmapped_with_reason"]
    aoi_ring = None
    if aoi_path is not None:
        from scripts.pipeline.geo_frames import utm_forward as _aoi_fwd
        aoi_ring = _ring_from_aoi(
            json.loads(Path(aoi_path).read_text(encoding="utf-8")),
            _aoi_fwd)
    jev_by_id = {}
    if jev_path is not None:
        jev_doc = _load(jev_path)
        jev_by_id = {str(r["candidate_id"]): r
                     for r in jev_doc.get("rows", [])}
    fusion_reviews = {}
    if fusion_path is not None:
        fusion_doc = _load(fusion_path)
        fusion_reviews = (fusion_doc.get("observations", {})
                          if isinstance(fusion_doc, dict) else {})
    fusion_cleared = {fid.rsplit("/", 1)[-1] for fid, rev in
                      fusion_reviews.items()
                      if isinstance(rev, dict)
                      and rev.get("same_object") is False}
    fused_debt = {}
    for disp in entities.get("dispositions", []):
        if disp.get("disposition") == "fused_debt":
            fused_debt[disp.get("source_object_id")] = disp
    try:
        street_policy = rules["street_fusion_policy"]
        sight_min = int(street_policy["sighting_frames_min"]["value"])
    except KeyError as exc:
        raise ValueError(
            "street_fusion_policy DEFAULT fact missing from rules "
            f"(absent key {exc}); regenerate rules_resolved.json with "
            "compile_rules_resolved.py") from exc

    try:
        template_block = rules["street_template_families"]
        template_rows = [(r["object_token"], r["template_id"])
                         for r in template_block["families"]]
        cone_min = int(
            template_block["cone_evidence_frames_min"]["value"])
    except KeyError as exc:
        raise ValueError(
            "street_template_families DEFAULT fact missing from rules "
            f"(absent key {exc}); regenerate rules_resolved.json with "
            "compile_rules_resolved.py") from exc
    try:
        arch_block = rules["fixture_archetype_templates"]
        archetype_rows = {r["archetype"]: r["template_id"]
                          for r in arch_block["rows"]}
    except KeyError as exc:
        raise ValueError(
            "fixture_archetype_templates DEFAULT fact missing from rules "
            f"(absent key {exc}); regenerate rules_resolved.json with "
            "compile_rules_resolved.py") from exc
    facade_by_use = ((rules.get("facade_archetype_defaults_by_use") or {})
                     .get("by_use", {}))
    overlay = {}
    for _fid, _feat in osm_by_fid.items():
        _props = _feat.get("properties", {})
        overlay[_fid] = {
            "fixture_archetype": (_props.get("fixture_archetype") or {})
            .get("value"),
            "use_class": (_props.get("use_class") or {}).get("value"),
            "religion": (_props.get("religion") or {}).get("value"),
        }
    asset_ctx = {"template_rows": template_rows,
                 "archetype_rows": archetype_rows,
                 "facade_by_use": facade_by_use, "overlay": overlay}
    rules_applied: Counter = Counter()
    rows: list[dict] = []

    STREET_CLASSES = ("utility_pole", "sign", "fixture", "road_marking",
                      "vegetation")
    def emit(feature_id, canonical_id, semantic_class, recon, importance,
             evidence, disposition, kind, asserts, complete, policy_rule,
             representation_id, owner, reason, bridge, primary=True,
             jev_route=None, jev_decision=None):
        assert disposition in DISPOSITIONS, disposition
        assert kind in KINDS, kind
        assert reason, feature_id
        if kind == "synthetic_identity_proxy" and disposition == "mesh_fixture":
            assert asserts is False, feature_id
        rules_applied[":".join(policy_rule.split(":")[1:])] += 1
        rows.append({
            "feature_id": feature_id, "canonical_id": canonical_id,
            "semantic_class": semantic_class, "reconstruction_class": recon,
            "importance": importance, "evidence_state": evidence,
            "disposition": disposition, "representation_kind": kind,
            "evidence_complete": complete, "policy_rule": policy_rule,
            "representation_id": representation_id, "owner_stage": owner,
            "reason": reason, "source_bridge": bridge, "primary": primary,
            "jev_route": jev_route,
            "jev_decision": jev_decision,
        })

    # FL-4809: importance comes from the route when a route-importance
    # receipt is supplied, and from the debt row otherwise.
    #
    # importance {background, route_visible, landmark, gameplay_critical} is a
    # level-of-information-need statement. It was a FIELD OF THE DEBT ROW, and
    # debt scopes itself to the 17 B/C triage features ("A features are out of
    # scope"), and triage assigns group A to every class that is not place and
    # not fixture. So all 733 buildings fell to the default below.
    #
    # An earlier revision of this comment said two things it should not have.
    # It said importance "should drive target reconstruction class": it does
    # not, and the same lane inputs planned both ways give the identical split,
    # A 650 / B 83. Nothing here consults importance when assigning a class.
    # It also said "no importance-driven rule could ever fire" for a building:
    # one always did. debt_policy_key_for is importance-driven and ran for all
    # 733, selecting background_A every time. The rule was firing; it was being
    # fed a constant. That is the defect, and it is a narrower one than either
    # sentence claimed.
    #
    # What the receipt changes, measured rather than projected: the importance
    # field on each row, the debt policy those rows select, and -- through the
    # background-only branch of R-STREET-OMIT below -- one street sighting that
    # may no longer be silently omitted. Not eligibility, not class, not asset
    # selection.
    #
    # compile_route_importance answers the same question for every canonical
    # feature using the same 30 m test the debt stage already implements, so
    # the two cannot disagree on the law, only on coverage. Where both have an
    # answer they agree exactly, verified on all 17 shared features including
    # the 30.054 m boundary case. Supplying no receipt keeps the previous
    # behaviour byte for byte.
    #
    # The receipt is validated against THIS run's canonical feature set, not
    # merely parsed. A receipt compiled from a different canonical IR would
    # otherwise answer background by omission for the features only this run
    # knows about -- the same silent default the stage exists to remove --
    # and the content-hash pin cannot catch that on its own, because a caller
    # that forgets to pin route_importance in verify() never compares it.
    route_importance_by_fid: dict[str, str] = {}
    if route_importance_path is not None:
        route_importance_by_fid = load_route_importance(
            route_importance_path, required_feature_ids=by_fid.keys())

    # FL-4809 R2: the third lawful exit from route_visible_C. A waiver is
    # checked against this run's canonical set on load, so an approval written
    # against a different world refuses the run instead of quietly covering
    # nothing; the exercised set below closes the other direction.
    authored_waivers: dict[str, dict] = {}
    if authored_waivers_path is not None:
        authored_waivers = load_authored_waivers(
            authored_waivers_path, known_feature_ids=by_fid.keys())
    waivers_exercised: set[str] = set()

    def waiver_conditions(feat, debt_decision):
        """The four things an approver is agreeing about, and nothing else.

        Scoped deliberately.  An approval to omit one camera must not expire
        because an unrelated building gained a facade, so the digest covers
        this feature, its own observations, the presentation law that applies
        to it, and the verdict that made it owe anything -- not the world.
        """
        return {
            "feature": feat,
            "observations": [
                observations.get(oid)
                for oid in feat.get("supporting_observation_ids") or []],
            "requirements": {
                "presentation_requirements":
                    feat.get("presentation_requirements"),
                "gameplay_authority": feat.get("gameplay_authority"),
            },
            "debt_decision": debt_decision,
        }

    def emit_waived(fid, cid, cls, recon, importance, evidence, waiver,
                    feat, debt_decision, jev_route=None, jev_decision=None):
        """An approved omission: same disposition as the default, attributed.

        The row is deliberately shaped like the one R-STREET-OMIT produces --
        intentional_omission, asserts nothing, evidence_complete true -- and
        differs in exactly one place that matters. policy_rule names the
        approval record rather than naming a default, so a reader can ask who
        decided and on what basis and get an answer. The waiver adds no
        evidence and takes none away; the feature keeps the observation it
        always had.

        Before it fires, the approval is checked against the world it was
        given for.  An approver agreed to omit THIS feature, with THESE
        observations, under THESE presentation requirements, given THIS debt
        verdict.  If any of them moved, the thing in front of the planner is
        not the thing that was approved, and the run stops rather than
        applying yesterday's judgement to today's evidence.
        """
        digest = condition_digest(**waiver_conditions(feat, debt_decision))
        if digest != waiver["conditions_sha256"]:
            raise AuthoredWaiverInvalid(
                "authored waiver %s for %s was approved against conditions "
                "%s, but this run presents %s; the feature, its observations, "
                "its presentation requirements, or its debt verdict changed "
                "since the approval, so the approval must be reviewed again "
                "rather than re-applied" % (
                    waiver["waiver_id"], fid,
                    waiver["conditions_sha256"], digest))
        waivers_exercised.add(fid)
        emit(fid, cid, cls, recon, importance, evidence,
             "intentional_omission", "omitted", False, True,
             "authored_waiver:" + waiver["approval_record"],
             "authored_waiver:" + fid, "none",
             "R-WAIVER: approved omission, %s on %s (%s); %s" % (
                 waiver["approved_by"], waiver["approved_on"],
                 waiver["approval_record"], waiver["reason"]),
             "authored_waivers:" + AUTHORED_WAIVER_SCHEMA, True,
             jev_route=jev_route, jev_decision=jev_decision)
        rows[-1]["waiver_conditions_sha256"] = digest

    def importance_of(fid):
        return resolve_importance(route_importance_by_fid, debt_by_fid, fid)

    def debt_policy_key(fid):
        key = debt_policy_key_for(importance_of(fid), group_of.get(fid))
        if key not in debt_policy:
            raise ValueError(
                "representation_policy debt_policy_by_importance lacks "
                f"'{key}' for {fid}")
        return key

    def proxy_consumed(feature, group):
        """A place_category the proxy table already speaks for."""
        for prow in proxy_table:
            match = prow["match"]
            if match.get("semantic_class") != "place":
                continue
            prop = match.get("claim_property")
            value = _claim_value(feature, prop) if prop else _MISSING
            if match.get("claim_absent"):
                if value is _MISSING:
                    return True
                continue
            if "claim_value" in match and value == match["claim_value"]:
                return True
            prefix = match.get("claim_value_prefix")
            if prefix is not None and isinstance(value, str) \
                    and value.startswith(prefix):
                return True
        return False

    def has_place_facade_profile(feature):
        """A commercial/site place the proxy table does not consume."""
        if feature.get("semantic_class") != "place":
            return False
        if _claim_value(feature, "place_category") is _MISSING:
            return False
        return not proxy_consumed(feature, group_of.get(
            feature["feature_id"]))

    for fid in sorted(by_fid):
        feat = by_fid[fid]
        cls = feat["semantic_class"]
        kind_geo = feat.get("geometry", {}).get("kind")
        recon = group_of.get(fid, "B")
        importance = importance_of(fid)
        cid = canon_by_ref.get(fid.rsplit(":", 1)[-1], fid)
        debt_row = debt_by_fid.get(fid)
        debt_state = (debt_row or {}).get("observation_state")

        # R-AUTHORED-MLY: a reviewed per-object decision outranks every
        # automatic rule below. move_to was applied to by_fid above and
        # falls through to the normal lane at its new point.
        _mdec = mapillary_decisions.get(fid)
        if _mdec is not None:
            mapillary_decisions_applied.append({
                "decision_id": _mdec["decision_id"], "feature_id": fid,
                "action": _mdec["action"]})
            if _mdec["action"] == "reject":
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     "authored_mapillary_decision:" + _mdec["decision_id"],
                     "intentional_omission:" + fid, "none",
                     "R-AUTHORED-REJECT: %s (%s, %s)" % (
                         _mdec["basis"], _mdec["reviewed_by"],
                         _mdec["reviewed_on"]),
                     "authored_mapillary_decisions:" + _mdec["decision_id"],
                     True)
                continue
            if _mdec["action"] == "minimap_only":
                emit(fid, cid, cls, recon, importance, "derived",
                     "minimap_only", "map_only", False, True,
                     "authored_mapillary_decision:" + _mdec["decision_id"],
                     "minimap_only:" + fid, "minimap",
                     "R-AUTHORED-MINIMAP: %s (%s, %s)" % (
                         _mdec["basis"], _mdec["reviewed_by"],
                         _mdec["reviewed_on"]),
                     "authored_mapillary_decisions:" + _mdec["decision_id"],
                     True)
                continue

        # R-PROXY: an explicit synthetic stand-in from the policy table.
        hit = next(
            (p for p in proxy_table
             if _match_proxy(p, feat, group_of.get(fid))), None)
        if hit is not None:
            evidence = debt_state if debt_state else "derived"
            # FL-4876 STATION-DUP-01: the crossing marker proxy is built by
            # the infrastructure stage as a full barrier/signal set. Where
            # authored level_crossing_equipment already stands at this
            # crossing the proxy would be a third copy of the same device, so
            # the crossing stays on the minimap and the authored gates remain
            # the only drawn equipment.
            if hit["proxy_target"] == "crossing_marker" and \
                    hit["disposition"] == "mesh_fixture":
                _cg = (feat.get("geometry") or {}).get("coordinates")
                _near = []
                if isinstance(_cg, list) and len(_cg) >= 2:
                    _near = sorted(
                        (math.hypot(gx - float(_cg[0]), gy - float(_cg[1])),
                         gid) for gid, gx, gy in authored_gate_points
                        if math.hypot(gx - float(_cg[0]),
                                      gy - float(_cg[1]))
                        <= AUTHORED_CROSSING_GATE_RADIUS_M)
                if _near:
                    crossing_suppressed.append({
                        "feature_id": fid,
                        "authored_gates": [g for _d, g in _near],
                        "nearest_authored_gate_m": round(_near[0][0], 2),
                        "radius_m": AUTHORED_CROSSING_GATE_RADIUS_M})
                    emit(fid, cid, cls, recon, importance, evidence,
                         "minimap_only", "map_only", False, True,
                         "representation_policy:semantic_identity_proxy:"
                         + hit["id"] + ":authored_gates_present",
                         "minimap_only:" + fid, "minimap",
                         "R-CROSSING-AUTHORED: %d authored "
                         "level_crossing_equipment fixtures within %.0f m "
                         "(nearest %.1f m, %s) already draw this crossing; "
                         "the infrastructure proposal would duplicate them"
                         % (len(_near), AUTHORED_CROSSING_GATE_RADIUS_M,
                            _near[0][0], _near[0][1]),
                         "rules:representation_policy+authored_scene_fixtures",
                         True)
                    continue
            emit(fid, cid, cls, recon, importance, evidence,
                 hit["disposition"], hit["representation_kind"], False,
                 True, "representation_policy:semantic_identity_proxy:"
                 + hit["id"],
                 "proxy:" + hit["proxy_target"] + ":" + fid
                 if hit["proxy_target"] else "minimap_only:" + fid,
                 # FL-4876 INFRA-01. The crossing proxy's asset is a
                 # railway_crossing_profiles entry, built by
                 # compile_world_ir_infrastructure, not by population.
                 # FL-4876 STATION-STOP-MAP-01: the station node's platforms are built there too.
                 ("infrastructure"
                  if hit["proxy_target"] in ("crossing_marker", "station_platform")
                  else "fixtures") if hit["disposition"] == "mesh_fixture"
                 else "minimap",
                 "R-PROXY: explicit synthetic proxy "
                 f"{hit['proxy_target'] or 'map_only'}; a site identity, "
                 "never a physical claim",
                 "rules:representation_policy+canonical:claims", True)
            continue
        # FL-4876 SLOT-OWNER-01 / R-UPPER-STOREY. A tenancy whose storey is
        # known to be above the street (OSM level tag, or an operator-authored
        # tenancy record) has no street storefront: the street slot belongs to
        # the ground-floor tenancy. Terminal omission, not a guess.
        if has_place_facade_profile(feat):
            _lvl = _tenancy_level(feat)
            if _lvl is not None and _lvl[1] > 0:
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     "representation_policy:upper_storey_tenancy",
                     "intentional_omission:" + fid, "none",
                     "R-UPPER-STOREY: tenancy on storey level %d (%s); a street "
                     "storefront would claim the ground-floor tenancy's slot"
                     % (_lvl[1], _lvl[0]),
                     "tenancy_level:" + _lvl[0], True)
                continue
        # A known place may be reconstructed from canonical category and the
        # locale pack without claiming its facade form was observed. The old
        # law emitted a synthetic front and blocking debt for the same place.
        if (debt_state == "observation_owed"
                and has_place_facade_profile(feat)):
            emit(fid, cid, cls, recon, importance,
                 "rule_backed_reconstruction", "mesh_facade",
                 "synthetic_identity_proxy", False, True,
                 "representation_policy:debt_policy_by_importance:"
                 "route_visible_C_rule_backed_facade",
                 "place_front:" + fid, "place_fronts",
                 "R-FACADE-SYNTHETIC: canonical place identity and locale "
                 "profile authorize a marked reconstruction; no claim of "
                 "observed facade form",
                 "rules:representation_policy+canonical:claims", True)
            continue
        # Remaining route-visible C debt blocks evidence_complete -- unless
        # an authored waiver approves the omission.
        if debt_state == "observation_owed":
            waiver = authored_waivers.get(fid)
            if waiver is not None:
                emit_waived(fid, cid, cls, recon, importance,
                            "observation_owed", waiver, feat,
                            {"lane": "debt", "debt_state": debt_state,
                             "debt_policy_key": debt_policy_key(fid),
                             "debt_row": debt_row})
                continue
            emit(fid, cid, cls, recon, importance, "observation_owed",
                 "observation_owed", "owed", True, False,
                 "representation_policy:debt_policy_by_importance:"
                 "route_visible_C",
                 "observation_owed:" + fid, "place_fronts",
                 "R-OWED: observation owed; future owner place_fronts once "
                 "a source sign replaces the locale proposal",
                 "debt:infra01:observation_state", True)
            # A blocking row publishes the digest of exactly what an approver
            # would be agreeing to. Without this the only way to learn the
            # value is to guess it, and an approval nobody can compute is an
            # approval nobody can check.
            rows[-1]["waiver_conditions_sha256"] = condition_digest(
                **waiver_conditions(feat, {
                    "lane": "debt", "debt_state": debt_state,
                    "debt_policy_key": debt_policy_key(fid),
                    "debt_row": debt_row}))
            continue
        # R-FACADE: a site place with a front profile outside the proxy table.
        if has_place_facade_profile(feat):
            observed = debt_state in ("observed_boxed",
                                      "frame_available_unboxed")
            emit(fid, cid, cls, recon, importance,
                 debt_state if debt_state else "derived",
                 "mesh_facade",
                 "observed_physical" if observed
                 else "synthetic_identity_proxy",
                 observed, observed,
                 "representation_policy:debt_policy_by_importance:"
                 + debt_policy_key(fid),
                 "place_front:" + fid, "place_fronts",
                 "R-FACADE: site place with a locale front profile; form "
                 "is proposal until boxed",
                 "rules:representation_policy+debt:infra01", True)
            continue
        # R-SURFACE / R-OMIT: ground classes read the elevation claim.
        if cls in ground_classes and not (cls == "crossing"
                                          and kind_geo == "point"):
            counterpart = osm_by_fid.get(fid)
            if counterpart is None:
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     "representation_policy:missing_claim",
                     "intentional_omission:" + fid, "none",
                     "R-FALLBACK: missing_claim:elevation_basis; no OSM "
                     "counterpart carries the elevation claim",
                     "plan:compile_representation_plan", True)
                continue
            elevation = (counterpart.get("properties", {}).get(
                "elevation_relation") or {}).get("value")
            grade = _grade(elevation)
            if grade == "above":
                emit(fid, cid, cls, recon, importance, "derived",
                     omit_rule["disposition"], "omitted", False, True,
                     "representation_policy:elevated_surface_omission",
                     "intentional_omission:" + fid, omit_rule["owner_stage"],
                     "R-OMIT: bridge_deck; elevation claim above grade; "
                     "the at-grade stamper owns paint only",
                     "osm:plan02:elevation_relation", True)
                continue
            if grade == "below":
                emit(fid, cid, cls, recon, importance, "derived",
                     omit_rule["disposition"], "omitted", False, True,
                     "representation_policy:below_grade_omission",
                     "intentional_omission:" + fid, omit_rule["owner_stage"],
                     "R-OMIT: below_grade; elevation claim below grade "
                     "(culvert/tunnel); the at-grade stamper owns paint only",
                     "osm:plan02:elevation_relation", True)
                continue
            emit(fid, cid, cls, recon, importance, "derived",
                 "surface_decal", "surface", True, True,
                 "representation_policy:observed_defaults:"
                 "ground_surface_classes",
                 "surface_decal:" + fid, "stamp",
                 "R-SURFACE: at-grade ground class stamped from measured "
                 "geometry",
                 "canonical:geometry+osm:plan02:elevation_relation", True)
            continue
        # R-MASS: conflated footprint bakes as terrain mass.
        #
        # FL-4809 R8. The disposition says who owns the building's mass: the
        # bake stage, against a building spec. It does not say how bake
        # realizes it, and those are different questions. A lattice extrusion
        # raises terrain vertices and has no vertical face, so no window bay,
        # awning, shutter, plus no sign band can attach to it; a mesh shell is
        # real geometry with outward normals. The declared target forbids a
        # terrain interpolation sheet crossing a street-facing facade, so on a
        # route-visible building the lattice is the wrong realization.
        #
        # That decision is made HERE because importance lives here. An earlier
        # revision put it in bake_osm_building_footprints.bake(), which read
        # the route-importance receipt directly and promoted buildings the
        # plan had recorded as terrain_mass. Two owners answered one question
        # and only one of them wrote it down, so the plan stopped being the
        # record of what the world contains. The realization is now a field on
        # the row that already owns the mass, and bake obeys it.
        #
        # With no route-importance receipt every building reads background and
        # every building stays a lattice, which is exactly the behaviour
        # before this rule existed.
        if cls == mass_class:
            shell = importance in MESH_SHELL_IMPORTANCE
            emit(fid, cid, cls, recon, importance,
                 foot_state.get(fid.rsplit(":", 1)[-1], "derived"),
                 "terrain_mass", "terrain", True, True,
                 "representation_policy:observed_defaults:mass_class"
                 + (":mesh_shell" if shell else ""),
                 "terrain_mass:" + fid, "bake",
                 ("R-MASS-SHELL: route-visible mass is realized as a whole "
                  "building shell; a terrain lattice has no vertical face to "
                  "carry a facade" if shell else
                  "R-MASS: conflated footprint bakes as terrain mass"),
                 "entities:auditfix01:claims", True)
            rows[-1]["mass_realization"] = (
                "mesh_shell" if shell else "terrain_lattice")
            continue
        # R-STREET: Jev owns the typed reconstruction decision, not merely a
        # review-routing bit.  Representation and asset-choice answers cross
        # this boundary verbatim; this planner may attach and assign an engine
        # owner, but may not replace Jev's answer with an independent guess.
        if cls in STREET_CLASSES and fid in jev_by_id:
            jev_row = jev_by_id[fid]
            route = str(jev_row.get("routing") or "observation_debt")
            answers = jev_row.get("answers") or {}
            jev_representation = str(
                answers.get("representation") or "observation_debt")
            jev_asset_choice = str(answers.get("asset_choice") or "none")
            jev_decision = {
                "routing": route,
                "representation": jev_representation,
                "asset_choice": jev_asset_choice,
                "semantic_family": answers.get("semantic_family"),
                "permanence": answers.get("permanence"),
                "decision_authority": jev_row.get("decision_authority"),
                "policy": jev_row.get("policy"),
            }
            frames = _claim_value(feat, "frames_seen")
            nframes = len(frames) if isinstance(frames, list) else 0
            oval = str(_claim_value(feat, "object_value") or "")
            owner = "population_linear" if cls == "utility_pole" \
                else "fixtures"
            # FL-4876. Outside the declared product scope, so nothing will
            # build it and promising it only produces an unsatisfiable row.
            _geom = (feat.get("geometry") or {}).get("coordinates")
            _point = ((float(_geom[0]), float(_geom[1]))
                      if isinstance(_geom, list) and len(_geom) == 2
                      and isinstance(_geom[0], (int, float)) else None)
            _outside = point_outside_ring_m(_point, aoi_ring)
            if _outside is not None and _outside > 0.0:
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     AOI_SCOPE_POLICY,
                     "intentional_omission:" + fid, "none",
                     "R-OUT-OF-SCOPE: %.1f m outside the declared AOI ring "
                     "(route lateral buffer 55 m); the product boundary is "
                     "declared in aoi.direct.geojson, so this observation "
                     "stays evidence and is not promised as world content"
                     % _outside,
                     "aoi:connected_scope:declared_ring", True)
                rows[-1]["outside_declared_aoi_m"] = round(_outside, 3)
                continue
            review = fusion_reviews.get(fid, {})
            if review.get("same_object") is True:
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     "street_fusion_policy:fusion_merge",
                     "intentional_omission:" + fid, "none",
                     "R-STREET-MERGED: same object as %s; OSM "
                     "position wins with Mapillary presence claim" % (
                         review.get("osm_candidate"),),
                     "fusion:observe05:reviews+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                continue
            if "traffic-cone" in oval and nframes < cone_min:
                emit(fid, cid, cls, recon, importance, "derived",
                     "intentional_omission", "omitted", False, True,
                     "street_template_families:cone_evidence_frames_min",
                     "intentional_omission:" + fid, "none",
                     "R-STREET-CONE: transient cone, frames %d below "
                     "evidence minimum %d" % (nframes, cone_min),
                     "rules:street_template_families+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                continue
            requires_frame = row_is_street_match(feat)
            if (route != "decided" or (requires_frame and nframes < sight_min)
                    or jev_representation == "observation_debt"):
                # RETRACTED -- this check used to run BEFORE the background
                # default, on the reasoning that an explicit approval outranks
                # an implicit one. That made a waiver able to overwrite a row
                # that was never blocking, which is a different power from the
                # one the policy grants. debt_policy_by_importance says the
                # waiver discharges a debt that "blocks evidence_complete";
                # a background sighting the default already omits blocks
                # nothing, so there is no debt there to discharge.
                #
                # Placed here, the waiver applies only where the row would
                # otherwise block, matching the debt lane below, and making
                # assert_all_exercised's guarantee true: every exercised
                # approval really did discharge something. An approval can no
                # longer silently cover a case nobody was asked about.
                if (importance == "background"
                        and jev_representation == "observation_debt"):
                    emit(fid, cid, cls, recon, importance,
                         "directly_observed", "intentional_omission",
                         "omitted", False, True,
                         "street_fusion_policy:background_unresolved_omission",
                         "intentional_omission:" + fid, "none",
                         "R-STREET-OMIT: background sighting remains explicit "
                         "evidence but has no defensible authored "
                         "representation",
                         "jev:jev01:decisions+canonical", True,
                         jev_route=route, jev_decision=jev_decision)
                    continue
                waiver = authored_waivers.get(fid)
                if waiver is not None:
                    emit_waived(fid, cid, cls, recon, importance,
                                "directly_observed", waiver, feat,
                                {"lane": "street", "route": route,
                                 "jev_representation": jev_representation,
                                 "jev_decision": jev_decision},
                                jev_route=route, jev_decision=jev_decision)
                    continue
                emit(fid, cid, cls, recon, importance, "observation_owed",
                     "observation_owed", "owed", True, False,
                     "street_fusion_policy:jev_decision:" + route + ":"
                     + jev_representation,
                     "observation_owed:" + fid, owner,
                     "R-STREET-OWED: jev route %s representation %s, "
                     "frames %d/%d" % (
                         route, jev_representation, nframes, sight_min),
                     "jev:jev01:decisions+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                rows[-1]["waiver_conditions_sha256"] = condition_digest(
                    **waiver_conditions(feat, {
                        "lane": "street", "route": route,
                        "jev_representation": jev_representation,
                        "jev_decision": jev_decision}))
                continue
            if jev_representation == "new_asset_required":
                emit(fid, cid, cls, recon, importance, "directly_observed",
                     "observation_owed", "owed", True, False,
                     "street_fusion_policy:jev_new_asset_required",
                     "new_asset_required:" + fid, "asset_authoring",
                     "R-STREET-ASSET-DEBT: Jev requires a new authored asset; "
                     "no generic fixture may replace it",
                     "jev:jev01:decisions+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                continue
            if (jev_representation == "existing_asset"
                    and jev_asset_choice == "none"):
                emit(fid, cid, cls, recon, importance, "directly_observed",
                     "observation_owed", "owed", True, False,
                     "street_fusion_policy:jev_missing_asset_choice",
                     "observation_owed:" + fid, "asset_authoring",
                     "R-STREET-ASSET-DEBT: Jev selected existing_asset but "
                     "did not name an authored asset",
                     "jev:jev01:decisions+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                continue
            if "driveway" in oval:
                if jev_representation != "procedural_template":
                    emit(fid, cid, cls, recon, importance,
                         "directly_observed", "observation_owed", "owed",
                         True, False,
                         "street_fusion_policy:driveway_requires_procedural",
                         "observation_owed:" + fid, "asset_authoring",
                         "R-STREET-DRIVEWAY-DEBT: driveway surface requires "
                         "Jev procedural_template representation",
                         "jev:jev01:decisions+canonical", True,
                         jev_route=route, jev_decision=jev_decision)
                    continue
                emit(fid, cid, cls, recon, importance, "directly_observed",
                     "surface_decal", "surface", True, True,
                     "street_fusion_policy:sighting",
                     "surface_decal:" + fid, "stamp",
                     "R-STREET-DRIVEWAY: apron is surface, frames %d" % (
                         nframes,),
                     "jev:jev01:decisions+canonical", True, jev_route=route,
                     jev_decision=jev_decision)
                continue
            if (jev_representation == "procedural_template"
                    and cls != "road_marking"):
                emit(fid, cid, cls, recon, importance, "directly_observed",
                     "observation_owed", "owed", True, False,
                     "street_fusion_policy:jev_procedural_contract_missing",
                     "new_asset_required:" + fid, "asset_authoring",
                     "R-STREET-ASSET-DEBT: Jev selected procedural_template "
                     "but no typed procedural contract owns this semantic class",
                     "jev:jev01:decisions+canonical", True,
                     jev_route=route, jev_decision=jev_decision)
                continue
            # FL-4876 STATION-DUP-01: two detections of the same class within
            # MAPILLARY_SAME_CLASS_MERGE_M are one object. Rows are visited in
            # feature-id order, so the first-built detection is the survivor.
            if fid.startswith("feature:mapillary_object/") and _point:
                _ckey = mapillary_class_key(feat)
                _twin = next(
                    ((tfid, math.hypot(tx - _point[0], ty - _point[1]))
                     for tfid, tx, ty in mapillary_built.get(_ckey, [])
                     if math.hypot(tx - _point[0], ty - _point[1])
                     <= MAPILLARY_SAME_CLASS_MERGE_M), None)
                if _twin is not None:
                    mapillary_merges.append({
                        "kept": _twin[0], "merged": fid,
                        "class": list(_ckey),
                        "distance_m": round(_twin[1], 3),
                        "threshold_m": MAPILLARY_SAME_CLASS_MERGE_M})
                    emit(fid, cid, cls, recon, importance, "derived",
                         "intentional_omission", "omitted", False, True,
                         "street_fusion_policy:same_class_merge",
                         "intentional_omission:" + fid, "none",
                         "R-STREET-DUP-MERGE: same class %s as %s, %.2f m "
                         "apart (merge radius %.1f m); one object seen "
                         "twice, one fixture" % (
                             "/".join(_ckey), _twin[0], _twin[1],
                             MAPILLARY_SAME_CLASS_MERGE_M),
                         "mapillary:same_class_merge", True,
                         jev_route=route, jev_decision=jev_decision)
                    continue
                mapillary_built.setdefault(_ckey, []).append(
                    (fid, _point[0], _point[1]))
            emit(fid, cid, cls, recon, importance, "directly_observed",
                 "mesh_fixture", "observed_physical", True, True,
                 "street_fusion_policy:sighting",
                 "mesh_fixture:" + fid, owner,
                 "R-STREET: Mapillary sighting, frames %d, jev decided" % (
                     nframes,),
                 "jev:jev01:decisions+canonical", True, jev_route=route,
                 jev_decision=jev_decision)
            continue
        # R-FIXTURE: observed physical fixture classes (zebra = highway set).
        zebra = cls == "crossing" and kind_geo == "point" and \
            _claim_value(feat, "cross_section.highway") is not None
        if cls in fixture_classes or zebra:
            emit(fid, cid, cls, recon, importance, "directly_observed",
                 "mesh_fixture", "observed_physical", True, True,
                 "representation_policy:observed_defaults:"
                 "physical_fixture_classes",
                 "mesh_fixture:" + fid, FIXTURE_OWNER.get(cls, "fixtures"),
                 "R-FIXTURE: surveyed physical fixture class",
                 "canonical:claims", True)
            continue
        emit(fid, cid, cls, recon, importance, "derived",
             "intentional_omission", "omitted", False, True,
             "representation_policy:missing_claim",
             "intentional_omission:" + fid, "none",
             f"R-FALLBACK: missing_claim:{cls}; no rule owns this class "
             "and no claim was read to invent one",
             "plan:compile_representation_plan", True)

    for u in sorted(unmapped, key=lambda r: r["source_ref"]):
        fid = UNMAPPED_FEATURE_PREFIX + u["source_ref"]
        emit(fid, fid, "unmapped", "B", "background", "unmapped",
             UNMAPPED_ROW_CONTRACT["disposition"], "omitted", False, True,
             UNMAPPED_ROW_CONTRACT["policy_rule"],
             "intentional_omission:" + fid,
             UNMAPPED_ROW_CONTRACT["owner_stage"],
             "R-UNMAPPED: triage unmapped (%s)" % u.get("reason", ""),
             UNMAPPED_ROW_CONTRACT["source_bridge"], True)
        rows[-1]["unmapped_kind"] = classify_unmapped(u.get("tags"))
        rows[-1]["unmapped_tags"] = dict(u.get("tags") or {})

    # Every canonical building mass gets one secondary rule-backed facade on
    # an exposed face. Place fronts are already primary decisions above; the
    # former second place row duplicated observed fronts and contradicted
    # observation-debt rows for unobserved places.
    for fid in sorted(by_fid):
        feat = by_fid[fid]
        is_building_facade = feat.get("semantic_class") == mass_class
        if not is_building_facade:
            continue
        emit(fid, canon_by_ref.get(fid.rsplit(":", 1)[-1], fid),
             feat["semantic_class"], group_of.get(fid, "B"),
             importance_of(fid),
             foot_state.get(fid.rsplit(":", 1)[-1], "derived"),
             "mesh_facade", "observed_physical", True, True,
             "facade_archetype_defaults_by_use:building_facade",
             "building_facade:" + fid, "population_fixtures",
             "R-BUILDING-FACADE: canonical mass retains its primary "
             "terrain representation and receives one rules-selected "
             "facade on an exposed route/carrier-facing face",
             "canonical:footprint+rules:facade_archetype_defaults_by_use",
             False)

    vocab = load_asset_vocabulary(catalog_path, pack_path)
    allowed_assets = set(vocab["module_ids"]) | vocab["templates"] | \
        vocab["profiles"] | vocab["rail"]
    for _sub, _tid in template_rows:
        if _tid not in allowed_assets:
            raise ValueError(
                "street_template_families names unknown authored asset "
                "'%s'; extend the vocabulary, do not invent assets" % _tid)
    for _arch, _tid in asset_ctx["archetype_rows"].items():
        if _tid not in allowed_assets:
            raise ValueError(
                "fixture_archetype_templates names unknown authored asset "
                "'%s'; extend the vocabulary, do not invent assets" % _tid)
    _template_footprints = vocab.get("template_footprints_m") or {}
    index = CanonicalIndex(canon, _load_route_xy(route_path))
    by_asset: Counter = Counter()
    by_anchor: Counter = Counter()
    by_attach_state: Counter = Counter()
    for row in rows:
        feature = by_fid.get(row["feature_id"], {})
        asset, missing = match_asset(row, feature, asset_ctx)
        if asset.startswith("catalog:"):
            asset_id = asset.split(":", 1)[1]
            if asset_id not in allowed_assets:
                raise ValueError(
                    "asset_match names unknown authored asset "
                    f"'{asset_id}' for {row['feature_id']}; extend the "
                    "vocabulary, do not invent assets")
        row["asset_match"] = asset
        row["asset_missing_claim"] = missing
        # Attachment contract: every emitting row carries a typed record.
        # intentional_omission rows emit nothing and stay unattached.
        disp = row["disposition"]
        fused_key = row["feature_id"].rsplit("/", 1)[-1]
        street = row_is_street_match(feature)
        if disp == "intentional_omission" or not feature:
            row["attachment"] = None
            row["attachment_state"] = "unattached"
            row["host_face"] = None
        elif disp == "minimap_only":
            record = attach_freestanding(feature, index)
            row["attachment"] = record
            row["attachment_state"] = "resolved"
            row["host_face"] = None
        elif disp == "surface_decal":
            record = attach_freestanding(feature, index,
                                         self_grounded=True)
            row["attachment"] = record
            row["attachment_state"] = "resolved"
            row["host_face"] = None
        # Fused-identity debt overrides placement: the sighting is real but
        # its canonical identity is contested (residual vs budget recorded
        # in merge01 entities). Street sightings past the carrier budget
        # stand freestanding per street_fusion_policy, never debt.
        elif street and fused_key in fused_debt and row["disposition"] != \
                "intentional_omission" and fused_key not in fusion_cleared:
            fused = fused_debt[fused_key]
            record = {"record_type": "spatial_reconciliation_debt",
                      "anchor_kind": "spatial_reconciliation_debt",
                      "reason": "fused_debt:residual_%sm_vs_budget_%sm" % (
                          fused.get("residual_m"), fused.get("budget_m")),
                      "residual_m": fused.get("residual_m")}
            row["attachment"] = record
            row["attachment_state"] = "spatial_reconciliation_debt"
            row["evidence_complete"] = False
            row["reason"] = row["reason"] + "; attachment_debt:" + \
                record["reason"]
            row["host_face"] = None
        else:
            if (disp == "mesh_facade" and not row.get("primary")
                    and feature.get("semantic_class") == mass_class):
                record = attach_building_facade(feature, index, rules)
            else:
                record = attach(feature, index, rules)
            if record.get("record_type") == "spatial_reconciliation_debt" \
                    and street and record.get("reason") == \
                    "carrier_beyond_tolerance":
                _coords = (feature.get("geometry") or {}).get("coordinates")
                _buried = (isinstance(_coords, list) and len(_coords) >= 2
                           and index.containing_building(
                               float(_coords[0]), float(_coords[1])) is not None)
                if _buried:
                    # The street-fusion fallback may preserve a genuinely
                    # isolated sighting as freestanding, but it cannot turn an
                    # impossible point inside a canonical building into an
                    # accepted world pose.  No lawful carrier-relative move
                    # exists within the association budget, so preserve debt.
                    record = dict(record)
                    record["reason"] = \
                        "carrier_beyond_tolerance_and_inside_mass"
                    row["attachment"] = record
                    row["attachment_state"] = \
                        "spatial_reconciliation_debt"
                    row["evidence_complete"] = False
                    row["reason"] = row["reason"] + \
                        "; attachment_debt:" + record["reason"]
                    row["host_face"] = None
                else:
                    record = attach_freestanding(feature, index)
                    row["attachment"] = record
                    row["attachment_state"] = "resolved"
                    row["reason"] = row["reason"] + \
                        "; carrier_beyond_budget_freestanding"
                    row["host_face"] = None
            elif record.get("record_type") == "spatial_reconciliation_debt":
                row["attachment"] = record
                row["attachment_state"] = "spatial_reconciliation_debt"
                row["evidence_complete"] = False
                row["reason"] = row["reason"] + "; attachment_debt:" + \
                    record["reason"]
                row["host_face"] = None
            else:
                row["attachment"] = record
                row["attachment_state"] = "resolved"
                if record["anchor_kind"] == "building_face":
                    row["host_face"] = {
                        "parent": record["canonical_building_id"],
                        "face_index": record["face_index"],
                        "heading_deg": face_heading(
                            index, record["canonical_building_id"],
                            record["face_index"]),
                        "face_selection_reason": record.get(
                            "face_selection_reason"),
                        "route_distance_m": record.get("route_distance_m")}
                else:
                    row["host_face"] = None
        # FL-4809 NUMABE-PLAN-08: resolve first, then change only the
        # representation. Never reattach paint or promote an owed sighting.
        if (street and row["semantic_class"] == marking_policy["semantic_class"]
                and row["disposition"] == "mesh_fixture"
                and row["attachment_state"] == marking_policy["attachment_state"]
                and (row["attachment"] or {}).get("anchor_kind")
                == marking_policy["anchor_kind"]):
            claim = feature.get("properties", {}).get(
                marking_policy["claim_property"], {})
            class_value = claim.get("value")
            observation_ids = claim.get("observation_ids", [])
            if (not isinstance(class_value, str)
                    or not class_value.startswith("marking--")
                    or not observation_ids
                    or any(observations.get(oid, {}).get("attributes", {}).get(
                        "object_value") != class_value for oid in observation_ids)):
                raise ValueError(
                    f"R-STREET-MARKING missing observation class: {row['feature_id']}")
            families = {
                marking_policy["family_by_class_token"][token]
                for token in class_value.split("--")
                if token in marking_policy["family_by_class_token"]}
            if len(families) > 1:
                raise ValueError(
                    f"R-STREET-MARKING conflicting class: {row['feature_id']}")
            rules_applied[":".join(row["policy_rule"].split(":")[1:])] -= 1
            row.update({
                "disposition": marking_policy["disposition"],
                "representation_kind": marking_policy["representation_kind"],
                "owner_stage": marking_policy["owner_stage"],
                "policy_rule": "representation_policy:street_marking:"
                               + marking_policy["id"],
                "representation_id": "surface_decal:" + row["feature_id"],
                "decal_family": next(iter(families), marking_policy["other_family"]),
                "marking_class_value": class_value,
                "decal_observation_ids": list(observation_ids),
                "reason": "R-STREET-MARKING: observed " + class_value
                          + "; resolved carrier attachment retained",
            })
            rules_applied["street_marking:" + marking_policy["id"]] += 1
        # A point decal is a physical painted disk, not a zero-area centre.
        # Reconcile the complete footprint after its family/diameter is known.
        # If no mass-clear pose exists inside the observation's own uncertainty
        # budget, the row becomes explicit debt and the stamper must skip it.
        if (row["disposition"] == "surface_decal"
                and feature.get("geometry", {}).get("kind") == "point"
                and isinstance(row.get("attachment"), dict)
                and row["attachment"].get("record_type") == "attachment"):
            _decal_record = reconcile_surface_decal(
                feature, row["attachment"], index, row.get("decal_family"))
            row["attachment"] = _decal_record
            if _decal_record.get("record_type") == \
                    "spatial_reconciliation_debt":
                row["attachment_state"] = "spatial_reconciliation_debt"
                row["evidence_complete"] = False
                row["reason"] = row["reason"] + \
                    "; attachment_debt:" + _decal_record["reason"]
        # FL-4876 FOOTPRINT-01. The fixture sibling of the decal reconcile
        # directly above, for the same stated reason: an asset is not a
        # zero-area centre. attach() placed this row with a clearance of 0.0
        # because it does not know the asset; row["asset_match"] was set 45
        # lines before attach() was called, so the extent was in scope the
        # whole time. Measured before this ran: 38 placements whose declared
        # footprint overlapped a mass that was not their host.
        if (row["disposition"] == "mesh_fixture"
                and isinstance(row.get("attachment"), dict)
                and row["attachment"].get("record_type") == "attachment"):
            _fp = _template_footprints.get(
                str(row.get("asset_match") or "").split(":", 1)[-1])
            if _fp is not None:
                _fixture_record = reconcile_mesh_fixture_footprint(
                    feature, row["attachment"], index, _fp[0], _fp[1])
                row["attachment"] = _fixture_record
                if _fixture_record.get("record_type") == \
                        "spatial_reconciliation_debt":
                    row["attachment_state"] = "spatial_reconciliation_debt"
                    row["evidence_complete"] = False
                    row["reason"] = row["reason"] + "; attachment_debt:" \
                        + _fixture_record["reason"]
        _polygon_tags = _source_tags(feature)
        if (row["disposition"] == "surface_decal"
                and feature.get("geometry", {}).get("kind") == "polygon"
                and feature.get("semantic_class") == "road"
                and _polygon_tags.get("amenity") == "parking"
                and _polygon_tags.get("parking") == "surface"):
            _surface_ring = feature["geometry"].get("coordinates") or []
            _overlapping_masses = [
                (mass_id, by_fid.get(mass_id) or index.by_id.get(mass_id) or {})
                for mass_id, mass_ring in index.buildings
                if polygons_overlap(_surface_ring, mass_ring)
            ]
            if _overlapping_masses:
                _surface_year = _evidence_year(feature)
                _newer = [
                    (mass_id, _evidence_year(mass))
                    for mass_id, mass in _overlapping_masses
                    if (_surface_year is not None
                        and _evidence_year(mass) is not None
                        and _evidence_year(mass) > _surface_year)
                ]
                if _newer:
                    _mass_id, _mass_year = sorted(
                        _newer, key=lambda item: (-item[1], item[0]))[0]
                    rules_applied[":".join(
                        row["policy_rule"].split(":")[1:])] -= 1
                    rules_applied["temporal_surface_mass_conflict"] += 1
                    row.update({
                        "disposition": "intentional_omission",
                        "representation_kind": "omitted",
                        "asserts_physical_presence": False,
                        "owner_stage": "none",
                        "policy_rule":
                            "representation_policy:temporal_surface_mass_conflict",
                        "representation_id":
                            "intentional_omission:" + row["feature_id"],
                        "attachment": None,
                        "attachment_state": "unattached",
                        "host_face": None,
                        "reason": (
                            "R-SURFACE-TEMPORAL: omitted stale surface "
                            f"evidence year {_surface_year}; overlaps newer "
                            f"canonical mass {_mass_id} evidence year {_mass_year}"),
                        "source_bridge":
                            "canonical:geometry+source:date+mass:source_id",
                    })
                else:
                    _mass_ids = sorted(mass_id for mass_id, _ in
                                       _overlapping_masses)
                    _debt = {
                        "record_type": "spatial_reconciliation_debt",
                        "anchor_kind": "freestanding",
                        "reason":
                            "surface_polygon_overlaps_building_mass_without_newer_authority",
                        "residual_m": None,
                        "conflicting_mass_ids": _mass_ids,
                    }
                    row["attachment"] = _debt
                    row["attachment_state"] = "spatial_reconciliation_debt"
                    row["evidence_complete"] = False
                    row["reason"] = row["reason"] + \
                        "; attachment_debt:" + _debt["reason"]
        if row["attachment"] is not None:
            if row["attachment"].get("record_type") == \
                    "spatial_reconciliation_debt":
                by_anchor["spatial_reconciliation_debt"] += 1
            else:
                by_anchor[row["attachment"]["anchor_kind"]] += 1
        by_attach_state[row["attachment_state"]] += 1
        by_asset[row["asset_match"]] += 1
    # Facade proposals mount on walls: a facade-having row without a
    # building_face record is attachment debt, never a carrier fallback.
    facade_set = set(r["feature_id"] for r in rows
                     if not r["primary"]
                     and r["disposition"] == "mesh_facade")
    for row in rows:
        if row["feature_id"] not in facade_set or row["disposition"] not in (
                "mesh_facade", "observation_owed"):
            continue
        current = row["attachment"] or {}
        if current.get("anchor_kind") == "building_face":
            continue
        feature = by_fid.get(row["feature_id"], {})
        coords = feature.get("geometry", {}).get("coordinates")
        face = None
        if isinstance(coords, list) and len(coords) >= 2:
            face = index.nearest_face(float(coords[0]),
                                      float(coords[1]))
        if face is None:
            debt = {"record_type": "spatial_reconciliation_debt",
                    "anchor_kind": "building_face",
                    "reason": "missing_host:no_face",
                    "residual_m": None}
        else:
            debt = {"record_type": "spatial_reconciliation_debt",
                    "anchor_kind": "building_face",
                    "reason": "face_beyond_tolerance",
                    "residual_m": round(face[0], 3)}
        old_state = row["attachment_state"]
        old_attachment = row["attachment"] or {}
        old_kind = "spatial_reconciliation_debt" if old_attachment.get(
            "record_type") == "spatial_reconciliation_debt" \
            else old_attachment.get("anchor_kind",
                                    "spatial_reconciliation_debt")
        row["attachment"] = debt
        row["attachment_state"] = "spatial_reconciliation_debt"
        row["evidence_complete"] = False
        row["reason"] = row["reason"] + "; attachment_debt:" + \
            debt["reason"]
        row["host_face"] = None
        by_anchor[old_kind] -= 1
        by_anchor["spatial_reconciliation_debt"] = \
            by_anchor.get("spatial_reconciliation_debt", 0) + 1
        by_attach_state[old_state] -= 1
        by_attach_state["spatial_reconciliation_debt"] = \
            by_attach_state.get("spatial_reconciliation_debt", 0) + 1

    # OSM presence claims: merged sightings attest the winner's presence.
    by_fid_row = {}
    for row in rows:
        if row["primary"]:
            by_fid_row[row["feature_id"]] = row
    for fid, rev in fusion_reviews.items():
        if not isinstance(rev, dict) or rev.get("same_object") is not True:
            continue
        winner = by_fid_row.get(rev.get("osm_candidate", ""))
        if winner is not None and "presence_attested_by" not in winner.get(
                "reason", ""):
            winner["reason"] = winner["reason"] + \
                "; presence_attested_by:" + fid

    rows.sort(key=lambda r: (r["feature_id"], r["disposition"]))
    primaries = [r for r in rows if r["primary"]]
    # Every emit has run, so the exercised set is final. An approval the run
    # never needed is refused here rather than ignored: the feature stopped
    # owing an observation, and a waiver carried past its condition reads as
    # considered when nobody has looked at it since.
    if authored_waivers:
        assert_all_exercised(authored_waivers, waivers_exercised)

    plan = {"schema": SCHEMA, "rows": rows, "counts": {
        "rows": len(rows), "primary": len(primaries),
        "facade_rows": len(rows) - len(primaries)}}
    metadata = _load(terrain_metadata_path) if terrain_metadata_path else None
    metric_frame = canon["coordinate_frames"]["epsg32654"]
    plan["spatial_provenance"] = stamp_provenance(
        canonical_world_ir_path=canonical_path, entities_path=entities_path,
        terrain_metadata_path=terrain_metadata_path, rules_path=rules_path,
        catalog_path=catalog_path,
        coordinate_frame={"name": metric_frame["crs"],
                          "units": metric_frame["units"]},
        world_units_per_metre=metadata["content_scale"] if metadata else None,
        planner_version=PLANNER_VERSION)

    by_disp: Counter = Counter()
    by_kind: Counter = Counter()
    by_owner: Counter = Counter()
    cross: Counter = Counter()
    complete_by_class: Counter = Counter()
    incomplete_by_class: Counter = Counter()
    norule = [r["feature_id"] for r in rows if not r["reason"]]
    seen_primary: Counter = Counter(r["feature_id"] for r in primaries)
    dupes = sorted(f for f, n in seen_primary.items() if n > 1)
    for r in rows:
        by_disp[r["disposition"]] += 1
        by_kind[r["representation_kind"]] += 1
        by_owner[r["owner_stage"]] += 1
        cross[(r["disposition"], r["reconstruction_class"])] += 1
        if r["evidence_complete"]:
            complete_by_class[r["semantic_class"]] += 1
        else:
            incomplete_by_class[r["semantic_class"]] += 1
    import hashlib as _hl
    policy_hash = _hl.sha256(json.dumps(
        policy, sort_keys=True).encode()).hexdigest()
    # PLANORDER-04: the receipt pins the complete spatial-generation tuple.
    # Route, terrain metadata, fusion reviews, catalog, and pack are pinned
    # by content hash alongside the lattice inputs; scale, planner version,
    # and planner context are pinned by value under "provenance" so an
    # override compiled from stale inputs fails verify() naming the field.
    world_units = float(metadata["content_scale"]) if metadata else None
    planner_version = PLANNER_VERSION
    planner_context = {"coordinate_frame": {"name": metric_frame["crs"],
                                             "units": metric_frame["units"]},
                       "content_scale_source": "terrain_metadata.content_scale"}
    receipt = {
        "schema": "y92.representation_plan_receipt.v2",
        "inputs": {name: _sha256(p) for name, p in (
            [("canonical", canonical_path), ("entities", entities_path),
             ("debt", debt_path), ("triage_receipt", triage_receipt_path),
             ("rules", rules_path), ("osm_ir", osm_ir_path),
             ("module_catalog", catalog_path), ("pack", pack_path)] +
            ([("terrain_metadata", terrain_metadata_path)]
             if terrain_metadata_path else []) +
            ([("jev_decisions", jev_path)] if jev_path else []) +
            ([("route_importance", route_importance_path)]
             if route_importance_path else []) +
            ([("authored_waivers", authored_waivers_path)]
             if authored_waivers_path else []) +
            ([("route", route_path)] if route_path else []) +
            ([("fusion_reviews", fusion_path)] if fusion_path else []))},
        "provenance": {
            "world_units_per_metre": world_units,
            "planner_version": planner_version,
            "planner_context": planner_context,
        },
        "histogram_disposition": dict(sorted(by_disp.items())),
        "histogram_kind": dict(sorted(by_kind.items())),
        "histogram_owner": dict(sorted(by_owner.items())),
        "crosstab_disposition_x_reconstruction": {
            "%sx%s" % (d, g): cross[(d, g)] for d in DISPOSITIONS
            for g in ("A", "B", "C") if cross[(d, g)]},
        "evidence_complete_by_class": {
            "complete": dict(sorted(complete_by_class.items())),
            "incomplete": dict(sorted(incomplete_by_class.items()))},
        "policy_fact_hash": policy_hash,
        # FL-4876 STATION-DUP-01. Pinned beside, not inside, "inputs": the
        # override-plan provenance tuple is a closed set owned elsewhere.
        "authored_mapillary_decisions": {
            "path": (str(authored_mapillary_decisions_path)
                     if authored_mapillary_decisions_path else None),
            "sha256": (_sha256(Path(authored_mapillary_decisions_path))
                       if authored_mapillary_decisions_path else None),
            "applied": sorted(mapillary_decisions_applied,
                              key=lambda r: r["feature_id"]),
            "unapplied": sorted(set(mapillary_decisions)
                                - {a["feature_id"]
                                   for a in mapillary_decisions_applied}),
        },
        "mapillary_same_class_merges": {
            "threshold_m": MAPILLARY_SAME_CLASS_MERGE_M,
            "count": len(mapillary_merges),
            "merges": mapillary_merges,
        },
        "crossing_proxy_suppressed_by_authored_gates": {
            "radius_m": AUTHORED_CROSSING_GATE_RADIUS_M,
            "authored_fixtures": (str(authored_fixtures_path)
                                  if authored_fixtures_path else None),
            "count": len(crossing_suppressed),
            "crossings": crossing_suppressed,
        },
        "asset_vocabulary": {
            "module_catalog": str(catalog_path),
            "modules_by_domain": {k: vocab["by_domain"][k]
                                  for k in sorted(vocab["by_domain"])},
            "module_count": len(vocab["module_ids"]),
            "pack_files": vocab["pack_files"],
            "pack_templates": len(vocab["templates"]),
            "place_front_profiles": len(vocab["profiles"]),
            "railway_crossing_profiles": len(vocab["rail"]),
            "fixture_archetypes": 6,
            "facade_archetype_modules":
                vocab["by_domain"].get("facade_archetype", 0),
        },
        "histogram_asset_match": dict(sorted(by_asset.items())),
        "histogram_anchor_kind": dict(sorted(by_anchor.items())),
        "histogram_attachment_state": dict(sorted(by_attach_state.items())),
        "histogram_jev_route": dict(sorted(Counter(
            r["jev_route"] or "none" for r in rows).items())),
        "rules_applied": dict(sorted(rules_applied.items())),
        "reconciliation": {
            "primary_rows": len(primaries),
            "expected_primary": len(by_fid) + len(unmapped),
            "facade_rows": len(rows) - len(primaries),
            "duplicate_primary": dupes,
            "rows_without_reason": norule,
        },
    }
    return plan, receipt


def verify(plan_path, receipt_path, *, canonical_path, entities_path,
           terrain_metadata_path, rules_path, catalog_path, pack_path,
           route_path, fusion_reviews_path, jev_path=None,
           route_importance_path=None, authored_waivers_path=None,
           aoi_path=None,
           debt_path=None,
           triage_receipt_path=None, osm_ir_path=None,
           world_units_per_metre=None, planner_version=None,
           planner_context=None) -> dict:
    """Override verification contract: the override receipt must prove it
    was compiled from this run's complete spatial-generation tuple.
    Fails closed with PlanWorldMismatch naming the mismatched field.
    Returns the receipt inputs on success."""
    from scripts.pipeline.representation_plan_provenance import (
        PlanWorldMismatch as _Mismatch, current_hashes, verify as _verify)
    _plan = _load(Path(plan_path))
    if not isinstance(_plan.get("spatial_provenance"), dict):
        raise _Mismatch(
            "PLAN_WORLD_MISMATCH: spatial_provenance missing from "
            f"override plan {plan_path}")
    current = current_hashes(
        canonical=canonical_path, entities=entities_path,
        terrain_metadata=terrain_metadata_path, rules=rules_path,
        module_catalog=catalog_path, pack=pack_path, route=route_path,
        fusion_reviews=fusion_reviews_path, jev_decisions=jev_path,
        route_importance=route_importance_path,
        authored_waivers=authored_waivers_path,
        aoi=aoi_path,
        debt=debt_path, triage_receipt=triage_receipt_path, osm_ir=osm_ir_path)
    scalars = {"world_units_per_metre":
               (float(world_units_per_metre)
                if world_units_per_metre is not None else None),
               "planner_version": planner_version,
               "planner_context": planner_context}
    return _verify(receipt_path, current, current_scalars=scalars)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", type=Path, required=True)
    ap.add_argument("--entities", type=Path, required=True)
    ap.add_argument("--debt", type=Path, required=True)
    ap.add_argument("--triage-receipt", type=Path, required=True)
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--osm-ir", type=Path, required=True)
    ap.add_argument("--module-catalog", type=Path, required=True,
                    help="generic module catalog: validates catalog:<module> "
                         "matches and enumerates the denominator")
    ap.add_argument("--pack", type=Path, required=True,
                    help="locale pack.json: parents are walked on disk to "
                         "collect template/profile ids for validation")
    ap.add_argument("--jev-decisions", type=Path, required=True,
                    help="jev01 decisions.json: id-only routing join for "
                         "fused street sightings")
    ap.add_argument("--fusion-reviews", type=Path, required=True,
                    help="observe05 fusion reviews: same-object verdicts "
                         "merge sightings into OSM winners")
    ap.add_argument("--route", type=Path, required=True,
                    help="pedestrian route bundle file (Valhalla shape): "
                         "containing-ring faces resolve to the route-nearest "
                         "face; without it they fall back to carriers")
    ap.add_argument("--terrain-metadata", type=Path, required=True,
                    help="lane terrain metadata for the spatial provenance pin")
    ap.add_argument("--route-importance", type=Path, default=None,
                    help="compile_route_importance receipt; when absent, "
                         "importance falls back to the observation-debt row")
    ap.add_argument("--authored-waivers", type=Path, default=None,
                    help="authored representation waivers; the third lawful "
                         "exit from route_visible_C. A waiver naming a "
                         "feature this run does not carry, or one the run "
                         "never exercises, refuses the run")
    ap.add_argument("--aoi", type=Path, default=None,
                    help="aoi.direct.geojson; its \"aoi\" polygon is the "
                         "declared product scope. A street feature outside "
                         "that ring is omitted with its measured distance "
                         "rather than promised to a stage that will not "
                         "build it")
    ap.add_argument("--authored-tenancy", type=Path, default=None,
                    help="FL-4876 SLOT-OWNER-01: operator-approved tenancy "
                         "storeys (authored_place_tenancy.json)")
    ap.add_argument("--authored-mapillary-decisions", type=Path, default=None,
                    help="FL-4876 STATION-DUP-01: reviewed per-object "
                         "decisions for Mapillary detections "
                         "(authored_mapillary_object_decisions.json)")
    ap.add_argument("--authored-fixtures", type=Path, default=None,
                    help="authored_scene_fixtures.json: accepted "
                         "level_crossing_equipment rows suppress the "
                         "infrastructure crossing proposal at that crossing")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--owner-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    plan, receipt = build(
        args.canonical, args.entities, args.debt, args.triage_receipt,
        args.rules, args.osm_ir, args.module_catalog, args.pack,
        args.jev_decisions, args.route, args.fusion_reviews,
        args.terrain_metadata, args.route_importance,
        args.authored_waivers,
        args.aoi, authored_tenancy_path=args.authored_tenancy,
        authored_mapillary_decisions_path=args.authored_mapillary_decisions,
        authored_fixtures_path=args.authored_fixtures)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, indent=1, sort_keys=True)
                                + "\n", encoding="utf-8")
    by_owner: dict[str, list] = {}
    for row in plan["rows"]:
        if row["owner_stage"] == "none":
            continue
        by_owner.setdefault(row["owner_stage"], []).append(row)
    args.owner_dir.mkdir(parents=True, exist_ok=True)
    for owner, orows in sorted(by_owner.items()):
        (args.owner_dir / ("%s.json" % owner)).write_text(
            json.dumps({"schema": SCHEMA, "owner_stage": owner,
                        "spatial_provenance": plan["spatial_provenance"],
                        "rows": orows}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    rec = receipt["reconciliation"]
    print("[rep-plan] rows=%d primary=%d/%d facade=%d dupes=%d noreason=%d"
          % (plan["counts"]["rows"], rec["primary_rows"],
             rec["expected_primary"],
             rec["facade_rows"], len(rec["duplicate_primary"]),
             len(rec["rows_without_reason"])))
    print("[rep-plan] wrote %s %s %s/<owner>.json"
          % (args.out, args.receipt_out, args.owner_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

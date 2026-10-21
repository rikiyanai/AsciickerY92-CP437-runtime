#!/usr/bin/env python3
"""Generate population placements for representation rows population never made.

FL-4876. The representation plan decides *what* a canonical feature becomes:
it picks the catalog template, resolves the attachment onto a canonical
carrier with a chainage, a lateral offset and a side, and marks the row
``attachment_state: resolved``. The population compiler decides *where*
things go, but it builds its placements from ``registered_instances.json``
alone and binds a placement to a canonical identity for exactly one semantic
class, ``utility_pole`` (``compile_population_plan.py:4085-4086``). Measured
on this lane: 81 of 2761 placements carry ``canonical_feature_id`` and every
one is a pole.

So 425 primary ``mesh_fixture`` rows -- signs, lamps, manhole covers,
crossings -- reached the preassembly gate with no placement at all, and the
gate refused them as ``population_owner``. Nothing about them was
under-authored. 417 of the 425 already resolve, through the template the
plan chose, to an ``archetype_subtype`` that ``_build_archetype`` can build
today. The missing step was never a decision. It was this file.

What this does NOT do: choose an asset, choose a position, invent a shape, or
widen a tolerance. Every value it writes is copied from a decision the
representation plan already recorded. A row whose template does not resolve,
or whose attachment is not resolved, is left alone and keeps refusing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from scripts.pipeline.classic_procedural_mesh import ARCHETYPE_PHYSICAL
from scripts.pipeline.canonical_attachment import (
    CanonicalIndex, footprint_mass_conflicts, resolve as resolve_anchor)

SCHEMA = "y92.representation_fixture_placements.v1"

# The dispatcher already accepts a catalog-domain literal as a module id and
# gives it one explicit owner (dispatch_population_plan.py:73-75,
# "planner_domain_literal"). These placements carry their asset choice as a
# resolved archetype rather than a module, because the plan chose a template,
# not a module, and inventing eighteen module ids to launder that choice
# would be a second asset vocabulary.
PLACEMENT_MODULE_ID = "street_fixture"
PLACEMENT_PREFIX = "rep-fixture-"

GENERATED_OWNER_STAGE = "fixtures"
GENERATED_DISPOSITION = "mesh_fixture"


def load_templates(pack_root: Path) -> dict[str, dict]:
    """Every template across the pack tree, keyed by template_id."""
    templates: dict[str, dict] = {}
    for pack_path in sorted(Path(pack_root).glob("**/pack.json")):
        document = json.loads(pack_path.read_text(encoding="utf-8"))
        for template in document.get("templates", []):
            templates.setdefault(str(template["template_id"]), template)
    return templates


def resolve_archetype(row: dict, templates: dict[str, dict]) -> dict | None:
    """The archetype this row's already-chosen template names, if buildable."""
    asset_match = str(row.get("asset_match") or "")
    if not asset_match.startswith("catalog:"):
        return None
    template_id = asset_match.split(":", 1)[1]
    template = templates.get(template_id)
    if template is None:
        return None
    physical = template.get("physical") or {}
    subtype = physical.get("archetype_subtype")
    if not subtype or subtype not in ARCHETYPE_PHYSICAL:
        return None
    return {
        "archetype_subtype": str(subtype),
        "template_id": template_id,
        "native_bbox_m": physical.get("native_bbox_m"),
        "semantic_family": physical.get("semantic_family"),
        "basis": f"representation_plan.asset_match:{template_id}",
    }


def _carrier_polylines(canonical: dict) -> dict[str, list[tuple[float, float]]]:
    lines: dict[str, list[tuple[float, float]]] = {}
    for feature in canonical.get("canonical_features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if (isinstance(coordinates, list) and len(coordinates) >= 2
                and isinstance(coordinates[0], list)
                and len(coordinates[0]) == 2
                and isinstance(coordinates[0][0], (int, float))):
            lines[str(feature["feature_id"])] = [
                (float(x), float(y)) for x, y in coordinates]
    return lines


def pose_on_carrier(points: list[tuple[float, float]], chainage_m: float,
                    lateral_offset_m: float, side: str) -> dict | None:
    """Resolve the plan's chainage and offset into a world pose.

    This is arithmetic on values the representation plan already chose, not a
    placement decision. ``side`` follows the same right-of-travel convention
    the population attachment records use.
    """
    if len(points) < 2:
        return None
    travelled = 0.0
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 0.0:
            continue
        if travelled + length >= chainage_m or end is points[-1]:
            remainder = min(max(chainage_m - travelled, 0.0), length)
            ux, uy = dx / length, dy / length
            east = start[0] + ux * remainder
            north = start[1] + uy * remainder
            # Right of travel is the clockwise normal.
            nx, ny = (uy, -ux) if str(side).upper() == "R" else (-uy, ux)
            offset = abs(float(lateral_offset_m or 0.0))
            return {
                "E": east + nx * offset,
                "N": north + ny * offset,
                "yaw_deg": math.degrees(math.atan2(uy, ux)),
            }
        travelled += length
    return None


def _rings(canonical: dict) -> dict[str, list[tuple[float, float]]]:
    """Outer ring per canonical feature that has one."""
    rings: dict[str, list[tuple[float, float]]] = {}
    for feature in canonical.get("canonical_features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        ring = None
        if (isinstance(coordinates, list) and coordinates
                and isinstance(coordinates[0], list) and coordinates[0]
                and isinstance(coordinates[0][0], list)):
            ring = coordinates[0]
        elif (isinstance(coordinates, list) and len(coordinates) >= 3
              and isinstance(coordinates[0], list)
              and len(coordinates[0]) == 2):
            ring = coordinates
        if ring and len(ring) >= 3:
            rings[str(feature["feature_id"])] = [
                (float(x), float(y)) for x, y in ring]
    return rings


def pose_on_face(ring: list[tuple[float, float]], face_index: int,
                 along_face_fraction: float,
                 outward_offset_m: float) -> dict | None:
    """Resolve the plan's face, fraction and offset into a world pose.

    Face i is the edge from ring vertex i to vertex i+1, the same indexing
    the attachment's canonical_face_id uses. The outward normal is taken
    against the ring's own winding so the fixture ends up outside the wall
    rather than inside the building.
    """
    if len(ring) < 3:
        return None
    closed = ring[:-1] if ring[0] == ring[-1] else ring
    if not closed:
        return None
    index = int(face_index) % len(closed)
    start = closed[index]
    end = closed[(index + 1) % len(closed)]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return None
    ux, uy = dx / length, dy / length
    fraction = min(max(float(along_face_fraction or 0.0), 0.0), 1.0)
    east = start[0] + dx * fraction
    north = start[1] + dy * fraction
    area = 0.0
    for first, second in zip(closed, closed[1:] + closed[:1]):
        area += first[0] * second[1] - second[0] * first[1]
    # Counter-clockwise rings (positive area) put the exterior on the
    # clockwise normal, and vice versa.
    nx, ny = (uy, -ux) if area > 0 else (-uy, ux)
    offset = abs(float(outward_offset_m or 0.0))
    return {
        "E": east + nx * offset,
        "N": north + ny * offset,
        "yaw_deg": math.degrees(math.atan2(ny, nx)),
    }


def _envelope(archetype: dict) -> dict:
    bbox = archetype.get("native_bbox_m")
    if isinstance(bbox, list) and len(bbox) == 3:
        longitudinal, lateral, vertical = (float(v) for v in bbox)
    else:
        physical = ARCHETYPE_PHYSICAL[archetype["archetype_subtype"]]
        longitudinal, lateral, vertical = 0.6, 0.6, float(physical[1])
    return {
        "anchor": "placement_anchor",
        "max_longitudinal_overhang_m": 0.0,
        "fit_policy": "declared_asset_bounds",
        "longitudinal_extent_m": longitudinal,
        "lateral_extent_m": lateral,
        "vertical_extent_m": vertical,
        "extent_basis": "world_template_pack.physical.native_bbox_m",
        "selection_stage": "population_before_dispatch",
    }


def _footprint_extents_m(archetype: dict) -> tuple[float, float]:
    """Declared (longitudinal, lateral) ground footprint of an archetype."""
    envelope = _envelope(archetype)
    return (float(envelope["longitudinal_extent_m"]),
            float(envelope["lateral_extent_m"]))


# FL-4876 ROADSIDE-SNAP-01 (deleted) -> ROADSIDE-CLEAR-01. The per-carrier
# median painted-edge snap that lived here is replaced by
# scripts/pipeline/roadside_clearance.py, which tests the resolved pose
# against the painted surface union for every road-attached standing
# placement in the merged plan, not just these rows: the median edge left 43
# representation fixtures on the carriageway (build_reg_22), up to 4.2 m in.


def build(*, representation_plan: dict, canonical: dict,
          templates: dict[str, dict]) -> tuple[list[dict], dict]:
    """Return (placements, receipt) for every row population left uncovered."""
    polylines = _carrier_polylines(canonical)
    rings = _rings(canonical)
    index = CanonicalIndex(canonical)
    placements: list[dict] = []
    skipped: dict[str, list[str]] = {
        "no_resolvable_template": [],
        "attachment_not_resolved": [],
        "unsupported_anchor_kind": [],
        "carrier_geometry_absent": [],
        "chainage_off_carrier": [],
        "building_ring_absent": [],
        "face_unresolvable": [],
        "freestanding_unresolvable": [],
        "envelope_conflicts_mass": [],
    }
    for row in representation_plan.get("rows", []):
        if not row.get("primary"):
            continue
        if row.get("owner_stage") != GENERATED_OWNER_STAGE:
            continue
        if row.get("disposition") != GENERATED_DISPOSITION:
            continue
        feature_id = str(row.get("feature_id") or "")
        attachment = row.get("attachment") or {}
        if row.get("attachment_state") != "resolved":
            skipped["attachment_not_resolved"].append(feature_id)
            continue
        anchor_kind = attachment.get("anchor_kind")
        if anchor_kind not in ("carrier_chainage", "building_face",
                               "freestanding"):
            skipped["unsupported_anchor_kind"].append(feature_id)
            continue
        archetype = resolve_archetype(row, templates)
        if archetype is None:
            skipped["no_resolvable_template"].append(feature_id)
            continue
        if anchor_kind == "carrier_chainage":
            carrier_id = str(attachment.get("canonical_carrier_id") or "")
            points = polylines.get(carrier_id)
            if not points:
                skipped["carrier_geometry_absent"].append(feature_id)
                continue
            pose = pose_on_carrier(
                points, float(attachment.get("chainage_m") or 0.0),
                float(attachment.get("lateral_offset_m") or 0.0),
                str(attachment.get("side") or "R"))
            if pose is None:
                skipped["chainage_off_carrier"].append(feature_id)
                continue
        elif anchor_kind == "freestanding":
            # FL-4876 C-3. The attachment stage resolves some rows to
            # freestanding -- an area fixture with no carrier, and a point
            # whose nearest carrier is outside the association tolerance but
            # which stands clear of every mass. This file refused them as an
            # unsupported kind, so a resolved attachment had no compatible
            # consumer, which is a blocked row dressed up as a skip.
            #
            # The pose comes from canonical_attachment.resolve(), the one
            # owner of anchor-record-to-transform. It is not reimplemented
            # here: the area-to-point reduction (ring centroid) and the yaw
            # source (nearest canonical carrier's bearing, 0.0 with none) are
            # written down there, and a second copy is how one of them
            # silently stops agreeing with the other.
            carrier_id = str(attachment.get("nearest_carrier_id") or "")
            try:
                pose = resolve_anchor(attachment, index)
            except (KeyError, ValueError, IndexError, TypeError):
                skipped["freestanding_unresolvable"].append(feature_id)
                continue
            # The position was chosen by a clearance search that could only
            # test a point, because the attachment stage does not know which
            # asset will land there. Here the archetype is resolved and the
            # pose carries a yaw, so the real oriented footprint is testable.
            # A freestanding object has no host mass by definition, so any
            # mass its footprint overlaps is an unhosted collision, and the
            # row is refused by identity rather than emitted into a wall.
            longitudinal, lateral = _footprint_extents_m(archetype)
            conflicts = footprint_mass_conflicts(
                pose["E"], pose["N"], pose["yaw_deg"],
                longitudinal, lateral, index)
            if conflicts:
                skipped["envelope_conflicts_mass"].append(feature_id)
                continue
        else:
            carrier_id = str(attachment.get("canonical_building_id") or "")
            ring = rings.get(carrier_id)
            if not ring:
                skipped["building_ring_absent"].append(feature_id)
                continue
            pose = pose_on_face(
                ring, int(attachment.get("face_index") or 0),
                float(attachment.get("along_face_fraction") or 0.0),
                float(attachment.get("outward_offset_m") or 0.0))
            if pose is None:
                skipped["face_unresolvable"].append(feature_id)
                continue
        placement_id = PLACEMENT_PREFIX + feature_id.replace(":", "_")
        placements.append({
            "placement_id": placement_id,
            "module_id": PLACEMENT_MODULE_ID,
            "variant": archetype["archetype_subtype"],
            # The identity the population compiler never propagated. This is
            # the whole point: dispatch resolves a placement to its
            # representation row through this field first
            # (dispatch_population_plan.py:118-125), so without it a fixture
            # binds to its carrier road instead of to itself.
            "canonical_feature_id": feature_id,
            "E": pose["E"], "N": pose["N"], "yaw_deg": pose["yaw_deg"],
            "height_m": 0.0,
            "carrier_id": carrier_id,
            "evidence": {
                "kind": str(row.get("evidence_state") or "derived"),
                "canonical_feature_id": feature_id,
                "representation_id": row.get("representation_id"),
            },
            "evidence_kind": str(row.get("evidence_state") or "derived"),
            "resolved_archetype": archetype,
            "asset_physical_envelope": _envelope(archetype),
            "attachment": dict(attachment, record_type="attachment",
                               attachment_id="att-" + placement_id),
            "attachment_state": "attached",
            "attachment_id": "att-" + placement_id,
            "resolved_pose": {"E": pose["E"], "N": pose["N"],
                              "yaw_deg": pose["yaw_deg"]},
            "generated_by": SCHEMA,
        })
    receipt = {
        "schema": SCHEMA,
        "counts": {
            "placements": len(placements),
            **{reason: len(ids) for reason, ids in skipped.items()},
        },
        "skipped": {reason: sorted(ids) for reason, ids in skipped.items()
                    if ids},
        "archetypes": sorted({p["variant"] for p in placements}),
    }
    return placements, receipt


def merge_into_population_plan(plan: dict, placements: list[dict]) -> dict:
    """Append generated placements to their carriers, creating as needed."""
    by_carrier: dict[str, dict] = {
        str(carrier.get("carrier_id")): carrier
        for carrier in plan.get("carriers", [])}
    existing = {
        str(placement.get("placement_id"))
        for carrier in plan.get("carriers", [])
        for placement in carrier.get("placements", [])}
    for placement in placements:
        placement_id = str(placement["placement_id"])
        if placement_id in existing:
            raise ValueError(
                f"generated placement collides with an existing one: "
                f"{placement_id}")
        carrier_key = str(placement.get("carrier_id") or "")
        carrier = by_carrier.get(carrier_key)
        if carrier is None:
            carrier = {"carrier_id": carrier_key,
                       "carrier_class": "representation_generated",
                       "placements": []}
            plan.setdefault("carriers", []).append(carrier)
            by_carrier[carrier_key] = carrier
        carrier.setdefault("placements", []).append(placement)
        existing.add(placement_id)
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--representation-plan", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path,
                        default=Path("assets/world_templates"))
    parser.add_argument("--population-plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)

    representation_plan = json.loads(
        args.representation_plan.read_text(encoding="utf-8"))
    canonical = json.loads(args.canonical.read_text(encoding="utf-8"))
    plan = json.loads(args.population_plan.read_text(encoding="utf-8"))
    templates = load_templates(args.pack_root)

    placements, receipt = build(
        representation_plan=representation_plan, canonical=canonical,
        templates=templates)
    merge_into_population_plan(plan, placements)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, sort_keys=True) + "\n",
                        encoding="utf-8")
    args.receipt_out.write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print("[rep-fixtures] generated %d placements; skipped %s"
          % (receipt["counts"]["placements"],
             {k: v for k, v in receipt["counts"].items()
              if k != "placements" and v}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

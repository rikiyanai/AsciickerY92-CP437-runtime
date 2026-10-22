#!/usr/bin/env python3
"""Conflate OSM and PLATEAU buildings into canonical entities with claims.

FL-4809 CONFLATE-01.  The canonical middle for buildings: one entity per
building, per-attribute claims with evidence states, and a disposition for
every source object.  A second stage (compile_world_ir_building_specs.py
--canonical-entities) resolves heights from these claims.

Inputs: triage01/osm.world_ir.json (OSM buildings with triage claims),
source01/plateau.world_ir.json (PLATEAU buildings with measuredHeight,
storeys, gml:id), source01/plateau_ingest_receipt.json (IoU>=0.5 pairs).

Resolution rules (footprint authority switch is a LATER lane; this stage only
records which source is authoritative):
- footprint: PLATEAU authoritative when matched (IoU >= 0.5), else the single
  source observed it.
- height ladder: PLATEAU measuredHeight (authoritative_observed) > OSM height
  tag (directly_observed) > OSM building:levels x 3 m (derived) > locale
  prior, applied by the consumer (synthetic).
- storeys from PLATEAU when present, else unknown.
- height disagreement over 15 percent (or pair IoU < 0.5) marks the claim
  `conflicting` and keeps both values.

canonical_id is stable: sha1 of the sorted contributing source refs, never
list order.  No conflation of names/usages across vocabularies: both values
are kept where both exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.world_ir import WorldIRStore  # noqa: E402

EVIDENCE_STATES = (
    "authoritative_observed",
    "directly_observed",
    "derived",
    "inferred",
    "synthetic",
    "unknown",
    "conflicting",
)

HEIGHT_CONFLICT_FRACTION = 0.15
MATCH_IOU_THRESHOLD_CONFLICT = 0.5
LEVEL_HEIGHT_M = 3.0
# PLATEAU missing-value sentinel for bldg:storeysAboveGround (cf. the
# -9999 height sentinels the height ladder already drops via
# _parse_positive_number). A sentinel is no measurement: no storeys claim.
STOREYS_SENTINEL_MISSING = 9999

# FL-4932 TMG-HEIGHT-01. The TMG 2023 airborne point cloud (FL-4929) measured
# through compile_lidar_roof_field.py --all (FL-4930) is a fourth height
# source. It ranks below PLATEAU measuredHeight and an explicit OSM height tag
# and above OSM building:levels: a measurement beats a storey count, but this
# lane does not demote an existing authoritative or explicit value. The claim
# uses the p90 measured roof because FL-4930 found PLATEAU measuredHeight
# agrees with it at a median of +0.07 m over 545 paired buildings.
TMG_SOURCE = "tmg_pointcloud"
TMG_TEMPORAL = "2023-03/2023-04"
TMG_ROOF_SUMMARY_SCHEMA = "asciicker.roof_field_summary/v1"
# FL-4930 verdict rule (inferred working threshold): a roofed structure has a
# median measured roof at least this far above its local class-2 ground ring.
TMG_STRUCTURE_MIN_MEDIAN_M = 2.0


def canonical_id_for(source_refs: list[str]) -> str:
    digest = hashlib.sha1("|".join(sorted(source_refs)).encode("utf-8")).hexdigest()
    return f"bldg_{digest[:12]}"


def _claim(value: Any, source: str | None, method: str, evidence_state: str,
           confidence: float, temporal_validity: str | None,
           values: list | None = None) -> dict:
    assert evidence_state in EVIDENCE_STATES, evidence_state
    claim: dict[str, Any] = {
        "value": value,
        "source": source,
        "method": method,
        "evidence_state": evidence_state,
        "confidence": confidence,
        "temporal_validity": temporal_validity,
    }
    if values is not None:
        claim["values"] = values
    return claim


def _parse_positive_number(value: Any) -> float | None:
    if value is None:
        return None
    import math
    text = str(value).strip().lower().removesuffix("m").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _osm_buildings(osm_world_ir: Path) -> dict[str, dict]:
    """source_ref (way/...) -> {feature_id, obs_id, observed_at, tags}."""
    store = WorldIRStore.load(osm_world_ir)
    store.assert_valid()
    observations = {str(o["observation_id"]): o for o in store.observations}
    out: dict[str, dict] = {}
    for feature in store.features:
        if feature.get("semantic_class") != "building_footprint":
            continue
        supporting = list(feature.get("supporting_observation_ids", []))
        obs = observations.get(supporting[0], {}) if supporting else {}
        attributes = (obs.get("attributes") or {})
        ref = attributes.get("source_ref")
        if not ref:
            marker = "feature:building_footprint:"
            feature_id = str(feature.get("feature_id") or "")
            ref = feature_id[len(marker):] if feature_id.startswith(marker) else feature_id
        out[str(ref)] = {
            "feature_id": str(feature.get("feature_id")),
            "obs_id": supporting[0] if supporting else None,
            "observed_at": obs.get("observed_at"),
            "tags": dict(attributes.get("tags") or {}),
        }
    return out


def _plateau_buildings(plateau_world_ir: Path) -> dict[str, dict]:
    """gml:id -> {feature_id, obs_id, observed_at, attributes}."""
    store = WorldIRStore.load(plateau_world_ir)
    store.assert_valid()
    observations = {str(o["observation_id"]): o for o in store.observations}
    out: dict[str, dict] = {}
    for feature in store.features:
        if feature.get("semantic_class") != "building_footprint":
            continue
        supporting = list(feature.get("supporting_observation_ids", []))
        obs = observations.get(supporting[0], {}) if supporting else {}
        attributes = dict(obs.get("attributes") or {})
        gml_id = attributes.get("source_ref")
        out[str(gml_id)] = {
            "feature_id": str(feature.get("feature_id")),
            "obs_id": supporting[0] if supporting else None,
            "observed_at": obs.get("observed_at"),
            "attributes": attributes,
        }
    return out


def load_lidar_roofs(path: Path) -> tuple[dict[str, dict], dict]:
    """source ref (way/... or plateau/bldg_...) -> roof-field summary row.

    Returns the rows plus a provenance record for the document inputs. The
    summary must come from compile_lidar_roof_field.py --all and must carry
    its tile hashes, so every height claim traces to frozen sheets.
    """
    text = Path(path).read_text(encoding="utf-8")
    summary = json.loads(text)
    if summary.get("schema") != TMG_ROOF_SUMMARY_SCHEMA:
        raise ValueError(f"{path}: schema {summary.get('schema')!r} is not {TMG_ROOF_SUMMARY_SCHEMA}")
    if not summary.get("tile_sha256"):
        raise ValueError(f"{path}: roof-field summary carries no tile hashes; refusing unprovenanced heights")
    rows: dict[str, dict] = {}
    for row in summary.get("rows", []):
        source = str(row["source"])
        if source in rows:
            raise ValueError(f"{path}: two roof-field rows for {source}")
        rows[source] = row
    provenance = {
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "tiles": summary.get("tiles"),
        "tile_sha256": summary.get("tile_sha256"),
        "horizontal_crs": summary.get("horizontal_crs"),
    }
    return rows, provenance


def _lidar_row(lidar: dict[str, dict] | None, osm_ref: str | None,
               plateau_gml_id: str | None) -> dict | None:
    """The roof-field row for this entity: the OSM ring first, else PLATEAU."""
    if not lidar:
        return None
    if osm_ref is not None and osm_ref in lidar:
        return lidar[osm_ref]
    if plateau_gml_id is not None:
        return lidar.get(f"plateau/{plateau_gml_id}")
    return None


def _lidar_structure_height(row: dict | None) -> float | None:
    """p90 measured roof when the roof field shows a structure, else None."""
    if not row or row.get("status") != "compiled":
        return None
    roof = row.get("measured_roof_m") or {}
    median, p90 = roof.get("median"), roof.get("p90")
    if median is None or p90 is None or float(median) < TMG_STRUCTURE_MIN_MEDIAN_M:
        return None
    return float(p90)


def _lidar_contradiction(row: dict | None) -> dict | None:
    """A dated observation that the 2023 survey shows no raised structure."""
    if not row:
        return None
    if row.get("status") != "compiled":
        finding = f"roof-field evidence gate refused: {row.get('reason')}"
        median = None
    else:
        median = (row.get("measured_roof_m") or {}).get("median")
        if median is None or float(median) >= TMG_STRUCTURE_MIN_MEDIAN_M:
            return None
        finding = (f"no raised structure: median measured roof {float(median):.3f} m "
                   f"< {TMG_STRUCTURE_MIN_MEDIAN_M:g} m above the local ground ring")
    return {
        "source": TMG_SOURCE,
        "temporal_validity": TMG_TEMPORAL,
        "finding": finding,
        "median_roof_m": median,
        "roof_field_source": row.get("source"),
        "disposition": "recorded; the footprint is kept until a decision is made",
    }


def _height_claims(osm: dict | None, plateau: dict | None,
                   lidar_h: float | None = None) -> tuple[dict, float | None]:
    """Resolved height claim plus the fractional disagreement (or None)."""
    plateau_h = None
    if plateau is not None:
        entry = plateau["attributes"].get("measured_height_m") or {}
        plateau_h = _parse_positive_number(entry.get("value"))
    osm_explicit = None
    osm_levels_h = None
    if osm is not None:
        osm_explicit = _parse_positive_number(osm["tags"].get("height"))
        levels = _parse_positive_number(osm["tags"].get("building:levels"))
        if levels is not None:
            osm_levels_h = levels * LEVEL_HEIGHT_M
    disagreement = None
    osm_h = osm_explicit if osm_explicit is not None else osm_levels_h
    if plateau_h is not None and osm_h is not None:
        disagreement = abs(plateau_h - osm_h) / max(plateau_h, osm_h)
    plateau_temporal = (plateau or {}).get("observed_at")
    osm_temporal = (osm or {}).get("observed_at")
    if plateau_h is not None:
        if disagreement is not None and disagreement > HEIGHT_CONFLICT_FRACTION:
            return _claim(
                plateau_h, "plateau",
                "PLATEAU bldg:measuredHeight wins the ladder but OSM disagrees "
                f"by {disagreement * 100:.1f}% (> 15%), so both values are kept",
                "conflicting", 0.5, plateau_temporal,
                values=[
                    {"value": plateau_h, "source": "plateau",
                     "evidence_state": "authoritative_observed"},
                    {"value": osm_h, "source": "osm",
                     "evidence_state": "directly_observed"
                     if osm_explicit is not None else "derived"},
                ],
            ), disagreement
        return _claim(
            plateau_h, "plateau", "PLATEAU bldg:measuredHeight, verbatim (uom m)",
            "authoritative_observed", 0.9, plateau_temporal,
        ), disagreement
    if osm_explicit is not None:
        return _claim(
            osm_explicit, "osm", "OSM height tag interpreted as metres",
            "directly_observed", 0.8, osm_temporal,
        ), disagreement
    if lidar_h is not None:
        # FL-4932 TMG-HEIGHT-01: measured roof outranks a storey count. The
        # levels-derived value, when present, is kept alongside for review.
        return _claim(
            lidar_h, TMG_SOURCE,
            "TMG 2023 airborne LiDAR: p90 measured roof above the local class-2 "
            "ground ring (compile_lidar_roof_field.py)",
            "directly_observed", 0.8, TMG_TEMPORAL,
            values=([{"value": osm_levels_h, "source": "osm", "evidence_state": "derived"}]
                    if osm_levels_h is not None else None),
        ), disagreement
    if osm_levels_h is not None:
        return _claim(
            osm_levels_h, "osm",
            f"OSM building:levels x {LEVEL_HEIGHT_M:g} m/storey",
            "derived", 0.6, osm_temporal,
        ), disagreement
    return _claim(
        None, None, "no source height; the consumer applies the locale prior",
        "synthetic", 0.3, None,
    ), disagreement



def street_id_for(source_refs: list[str]) -> str:
    digest = hashlib.sha1("|".join(sorted(source_refs)).encode("utf-8")).hexdigest()
    return f"str_{digest[:12]}"


def _mapillary_sightings(mapillary_world_ir: Path) -> list[dict]:
    """One row per Mapillary object feature: id, class, UTM, images, group."""
    import math as _math
    store = WorldIRStore.load(mapillary_world_ir)
    store.assert_valid()
    observations = {str(o["observation_id"]): o for o in store.observations}
    rows = []
    for feature in store.features:
        fid = str(feature.get("feature_id"))
        if not fid.startswith("feature:mapillary_object/"):
            continue
        props = feature.get("properties", {})
        group = (props.get("dedup_group_id") or {}).get("value") or fid
        frames = list((props.get("frames_seen") or {}).get("value") or [])
        x, y = feature["geometry"]["coordinates"][:2]
        rows.append({
            "feature_id": fid,
            "mid": fid.split("/")[-1],
            "semantic_class": str(feature.get("semantic_class")),
            "object_value": str((feature.get("labels") or {}).get("object_value")),
            "utm": (float(x), float(y)),
            "frames_seen": sorted({str(i) for i in frames}),
            "dedup_group": str(group),
            "obs_ids": sorted(feature.get("supporting_observation_ids", [])),
        })
    rows.sort(key=lambda r: r["feature_id"])
    return rows


def _osm_points(osm_world_ir: Path, classes: set[str]) -> dict[str, dict]:
    """OSM point feature_id -> {class, UTM} for merge-candidate classes."""
    store = WorldIRStore.load(osm_world_ir)
    store.assert_valid()
    out = {}
    for feature in store.features:
        if feature.get("geometry", {}).get("kind") != "point":
            continue
        cls = str(feature.get("semantic_class"))
        if cls not in classes:
            continue
        x, y = feature["geometry"]["coordinates"][:2]
        out[str(feature.get("feature_id"))] = {"class": cls, "utm": (float(x), float(y))}
    return out


def build_street_entities(osm_world_ir: Path, mapillary_world_ir: Path,
                          budgets: dict[str, float]) -> tuple[list[dict], list[dict], dict]:
    """Fuse Mapillary sightings into street entities (FL-4809 MERGE-01).

    Per-attribute authority: OSM owns semantics/topology, Mapillary owns
    presence/appearance/class for street objects. Dedup groups merge into
    one entity with frames_seen unioned. An OSM point of the same class
    within the class budget lends its position (Mapillary sighting becomes
    a claim); a candidate past the budget becomes a
    spatial_reconciliation_debt claim, never a blended point; sightings
    past every budget stay separate entities.
    """
    import math as _math
    from scripts.pipeline.compile_rules_resolved import _AUTH_BASIS_MERGE  # noqa: E402
    sightings = _mapillary_sightings(mapillary_world_ir)
    classes = {s["semantic_class"] for s in sightings} & set(budgets)
    points = _osm_points(osm_world_ir, classes)
    groups: dict[str, list[dict]] = {}
    for sighting in sightings:
        groups.setdefault(sighting["dedup_group"], []).append(sighting)

    entities: list[dict] = []
    dispositions: list[dict] = []
    merged = 0
    debt = 0

    def nearest_osm(cls: str, utm: tuple[float, float],
                    exclude: set[str] = frozenset()
                    ) -> tuple[str | None, float | None]:
        best = (None, None)
        for fid, point in points.items():
            if point["class"] != cls or fid in exclude:
                continue
            dist = _math.dist(utm, point["utm"])
            if best[1] is None or dist < best[1]:
                best = (fid, dist)
        return best

    # FL-4876 SPATIAL-F1.  One OSM node is one object.
    #
    # ``nearest_osm`` used to return the globally nearest node of the class
    # with no check that the node had already lent its position to another
    # dedup group.  On this lane one traffic-signal node,
    # ``feature:fixture:node/442165549``, was claimed by 16 distinct groups:
    # it lent its exact position to 4 canonical entities and generated
    # spatial reconciliation debt against 12 more.  Measured across the
    # lane, 9 canonical street objects sat at just 3 coordinates, stacked
    # exactly on top of one another -- each of which would serialize as a
    # separate mesh instance at an identical transform.
    #
    # Those sightings are different signal heads at one intersection that
    # OSM does not map individually.  A residual measured against a node
    # another object has already consumed is not the displacement of this
    # object, so it is not evidence of anything and must not become debt.
    #
    # The assignment is a one-to-one matching, taken nearest-first so it does
    # not depend on group id order: the closest observation to a node wins
    # it, and a group whose nearest node is taken falls through to its
    # nearest *unconsumed* node.  Nothing here changes a budget -- a pair
    # still has to be inside the class budget to merge at all.
    pair_candidates: list[tuple[float, str, str]] = []
    group_class: dict[str, str] = {}
    for group_id, members in groups.items():
        rep_member = sorted(members, key=lambda s: s["feature_id"])[0]
        cls = rep_member["semantic_class"]
        group_class[group_id] = cls
        if cls not in budgets:
            continue
        for fid, point in points.items():
            if point["class"] != cls:
                continue
            dist = _math.dist(rep_member["utm"], point["utm"])
            if dist <= float(budgets[cls]):
                pair_candidates.append((dist, group_id, fid))
    assigned_node: dict[str, tuple[str, float]] = {}
    consumed_nodes: set[str] = set()
    for dist, group_id, fid in sorted(pair_candidates):
        if group_id in assigned_node or fid in consumed_nodes:
            continue
        assigned_node[group_id] = (fid, dist)
        consumed_nodes.add(fid)

    for group_id in sorted(groups):
        members = sorted(groups[group_id], key=lambda s: s["feature_id"])
        rep = members[0]
        cls = rep["semantic_class"]
        budget = float(budgets[cls])
        frames = sorted({frame for member in members for frame in member["frames_seen"]})
        obs_ids = sorted({oid for member in members for oid in member["obs_ids"]})
        mids = [member["mid"] for member in members]
        object_values = {member["object_value"] for member in members}
        if len(object_values) != 1:
            raise ValueError(
                f"Mapillary dedup group {group_id} mixes object values: "
                f"{sorted(object_values)}")
        object_value = next(iter(object_values))
        # FL-4876 SPATIAL-F1.  Take this group's exclusively matched node if
        # it won one; otherwise look only at nodes no other group consumed,
        # so a debt is never measured against a node that already belongs to
        # a different object.
        if group_id in assigned_node:
            osm_fid, residual = assigned_node[group_id]
        else:
            osm_fid, residual = nearest_osm(cls, rep["utm"], consumed_nodes)
        search_radius = 2.0 * budget
        if osm_fid is not None and residual <= budget:
            cid = street_id_for([f"osm:{osm_fid}"] + [f"mapillary:{mid}" for mid in mids])
            merged += 1
            disposition = "merged_into_osm"
            position = _claim(
                list(points[osm_fid]["utm"]), "osm",
                "OSM point lends the position: Mapillary sighting within "
                f"the {cls} budget ({budget:g} m); residual "
                f"{residual:.3f} m recorded, never blended",
                "directly_observed", 0.8, None)
            debt_claim = _claim(
                None, None, "residual within budget; no reconciliation debt",
                "unknown", 0.0, None)
        else:
            cid = street_id_for([f"mapillary:{mid}" for mid in mids])
            position = _claim(
                list(rep["utm"]), "mapillary",
                "Mapillary point with declared sigma prior; no OSM point "
                f"of class {cls} within the {budget:g} m budget",
                "directly_observed", 0.7, None)
            if osm_fid is not None and residual <= search_radius:
                debt += 1
                disposition = "fused_debt"
                debt_claim = _claim(
                    {"osm_candidate": osm_fid, "residual_m": round(residual, 3),
                     "budget_m": budget},
                    "mapillary",
                    "candidate past the class budget: kept separate with a "
                    "spatial_reconciliation_debt claim, never a blended point",
                    "conflicting", 0.5, None)
            else:
                disposition = "fused_separate"
                debt_claim = _claim(
                    None, None, "no OSM candidate in search radius; separate entity",
                    "unknown", 0.0, None)
        sources = [{
            "source": "mapillary",
            "source_object_id": mid,
            "observation_id": next((oid for oid in obs_ids if mid in oid), obs_ids[0] if obs_ids else None),
            "release": "observe03 Mapillary object ingest",
            "frame": "epsg4326",
            "transform": "identity UTM (stated datum WGS84)",
            "uncertainty_m": 5.0,
            "licence_category": 4,
            "capture_ids": sorted({frame for member in members if member["mid"] == mid for frame in member["frames_seen"]}),
        } for mid in mids]
        if osm_fid is not None and disposition == "merged_into_osm":
            sources.append({
                "source": "osm",
                "source_object_id": osm_fid,
                "observation_id": None,
                "release": "triage01 OSM ingest",
                "frame": "epsg32654",
                "transform": "identity UTM",
                "uncertainty_m": None,
                "licence_category": None,
                "capture_ids": [],
            })
        entities.append({
            "canonical_id": cid,
            "kind": "street_object",
            "source_bridge": sources,
            "claims": {
                "position": position,
                "semantic_class": _claim(
                    cls, "mapillary",
                    "OBSERVE-03 OBJECT_CLASS normalisation; OSM owns "
                    "semantics where merged (same class required)",
                    "derived", 0.7, None),
                "object_value": _claim(
                    object_value, "mapillary",
                    "verbatim object token shared by the deduplicated sightings",
                    "directly_observed", 0.9, None),
                "observed_by": _claim(
                    [{"observation_id": oid, "source": "mapillary"} for oid in obs_ids],
                    "mapillary", "sighting observations, verbatim ids",
                    "directly_observed", 0.9, None),
                "frames_seen": _claim(
                    frames, "mapillary",
                    "union of member frames_seen for the downstream chain",
                    "directly_observed", 0.9, None),
                "dedup_group": _claim(
                    group_id, "mapillary",
                    "OBSERVE-03 candidate-level dedup group (1.0 m same class/value)",
                    "derived", 0.7, None),
                "spatial_reconciliation_debt": debt_claim,
            },
        })
        for mid in mids:
            dispositions.append({
                "source": "mapillary", "source_object_id": mid,
                "disposition": disposition, "canonical_id": cid,
                "residual_m": round(residual, 3) if osm_fid is not None else None,
                "budget_m": budget,
                "osm_candidate": osm_fid,
                "reason": (f"residual {residual:.3f} m vs {cls} budget {budget:g} m"
                           if osm_fid is not None else "no OSM candidate in search radius")
                if disposition != "fused_separate" else
                "no OSM candidate in search radius",
            })
    entities.sort(key=lambda e: e["canonical_id"])
    dispositions.sort(key=lambda d: (d["source"], d["source_object_id"]))
    report = {
        "street_entities": len(entities),
        "sightings": len(sightings),
        "sightings_merged_into_osm": merged,
        "entities_debt": debt,
        "budgets": {cls: float(budgets[cls]) for cls in sorted(budgets)},
        "budget_provenance": _AUTH_BASIS_MERGE,
    }
    return entities, dispositions, report
def build_entities(osm_world_ir: Path, plateau_world_ir: Path,
                   match_receipt_path: Path,
                   mapillary_world_ir: Path | None = None,
                   lidar_roof_summary: Path | None = None) -> dict:
    osm = _osm_buildings(osm_world_ir)
    plateau = _plateau_buildings(plateau_world_ir)
    lidar, lidar_provenance = (load_lidar_roofs(lidar_roof_summary)
                               if lidar_roof_summary is not None else (None, None))
    lidar_contradictions: list[dict] = []

    def _with_lidar(entity: dict, row: dict | None) -> dict:
        contradiction = _lidar_contradiction(row)
        if contradiction is not None:
            entity["contradictions"] = [contradiction]
            lidar_contradictions.append({"canonical_id": entity["canonical_id"], **contradiction})
        return entity
    match_doc = json.loads(match_receipt_path.read_text(encoding="utf-8"))
    match = match_doc["match"]
    pairs = match["matches"]

    osm_refs = set(osm)
    plateau_ids = set(plateau)
    for pair in pairs:
        if pair["osm_source_ref"] not in osm_refs:
            raise ValueError(f"match pair references unknown OSM {pair['osm_source_ref']}")
        if pair["plateau_gml_id"] not in plateau_ids:
            raise ValueError(f"match pair references unknown PLATEAU {pair['plateau_gml_id']}")
    if len({p["osm_source_ref"] for p in pairs}) != len(pairs):
        raise ValueError("match pairs are not one-to-one on the OSM side")
    if len({p["plateau_gml_id"] for p in pairs}) != len(pairs):
        raise ValueError("match pairs are not one-to-one on the PLATEAU side")

    matched_osm = {p["osm_source_ref"]: p for p in pairs}
    matched_plateau = {p["plateau_gml_id"]: p for p in pairs}
    storeys_sentinels_dropped = sum(
        1 for obj in plateau.values()
        if ((obj.get("attributes", {}).get("storeys_above_ground") or {})
            .get("value")) == STOREYS_SENTINEL_MISSING)

    entities: list[dict] = []
    dispositions: list[dict] = []

    # -- conflated pairs --------------------------------------------------
    for pair in sorted(pairs, key=lambda p: p["osm_source_ref"]):
        ref, gml_id, iou = pair["osm_source_ref"], pair["plateau_gml_id"], pair["iou"]
        osm_obj = {"source_object_id": ref, **osm[ref]}
        plateau_obj = {"source_object_id": gml_id, **plateau[gml_id]}
        cid = canonical_id_for([f"osm:{ref}", f"plateau:{gml_id}"])
        row = _lidar_row(lidar, ref, gml_id)
        height_claim, disagreement = _height_claims(
            osm[ref], plateau[gml_id], _lidar_structure_height(row))
        entities.append(_with_lidar(_entity(
            cid, osm_obj, plateau_obj, iou, height_claim, disagreement), row))
        dispositions.append({
            "source": "osm", "source_object_id": ref,
            "disposition": "conflated", "canonical_id": cid, "reason": None,
        })
        dispositions.append({
            "source": "plateau", "source_object_id": gml_id,
            "disposition": "conflated", "canonical_id": cid, "reason": None,
        })

    # -- single-source entities --------------------------------------------
    for ref in sorted(osm_refs - set(matched_osm)):
        osm_obj = {"source_object_id": ref, **osm[ref]}
        cid = canonical_id_for([f"osm:{ref}"])
        row = _lidar_row(lidar, ref, None)
        height_claim, _ = _height_claims(osm[ref], None, _lidar_structure_height(row))
        entities.append(_with_lidar(_entity(cid, osm_obj, None, None, height_claim, None), row))
        dispositions.append({
            "source": "osm", "source_object_id": ref,
            "disposition": "retained_unmatched", "canonical_id": cid,
            "reason": "osm_only: OSM building with no PLATEAU counterpart in "
                      "these tiles; stays OSM",
        })
    for gml_id in sorted(plateau_ids - set(matched_plateau)):
        plateau_obj = {"source_object_id": gml_id, **plateau[gml_id]}
        cid = canonical_id_for([f"plateau:{gml_id}"])
        row = _lidar_row(lidar, None, gml_id)
        height_claim, _ = _height_claims(None, plateau[gml_id], _lidar_structure_height(row))
        entities.append(_with_lidar(_entity(cid, None, plateau_obj, None, height_claim, None), row))
        dispositions.append({
            "source": "plateau", "source_object_id": gml_id,
            "disposition": "retained_unmatched", "canonical_id": cid,
            "reason": "plateau_only: real building OSM lacks; new entity with "
                      "the PLATEAU footprint",
        })

    entities.sort(key=lambda e: e["canonical_id"])
    dispositions.sort(key=lambda d: (d["source"], d["source_object_id"]))
    if len(dispositions) != len(osm_refs) + len(plateau_ids):
        raise ValueError("disposition coverage gap")
    street_report: dict | None = None
    if mapillary_world_ir is not None:
        from scripts.pipeline.compile_rules_resolved import MERGE_BUDGET_M  # noqa: E402
        budgets = {cls: float(val[0]) for cls, val in MERGE_BUDGET_M.items()}
        street_entities, street_dispositions, street_report = build_street_entities(
            osm_world_ir, mapillary_world_ir, budgets)
        entities.extend(street_entities)
        dispositions.extend(street_dispositions)
        entities.sort(key=lambda e: e["canonical_id"])
        dispositions.sort(key=lambda d: (d["source"], d["source_object_id"]))
        seen = [(d["source"], d["source_object_id"]) for d in dispositions]
        if len(set(seen)) != len(seen):
            raise ValueError("a source object is dispositioned twice")
    seen = [(d["source"], d["source_object_id"]) for d in dispositions]
    if len(set(seen)) != len(seen):
        raise ValueError("a source object is dispositioned twice")

    by_state: Counter = Counter()
    conflicting: list[dict] = []
    for entity in entities:
        if entity.get("kind") == "street_object":
            continue
        state = entity["claims"]["height_m"]["evidence_state"]
        by_state[state] += 1
        if state == "conflicting":
            values = {v["source"]: v["value"]
                      for v in entity["claims"]["height_m"]["values"]}
            plateau_h = values.get("plateau")
            osm_h = values.get("osm")
            pct = (abs(plateau_h - osm_h) / max(plateau_h, osm_h)
                   if plateau_h and osm_h else None)
            conflicting.append({
                "canonical_id": entity["canonical_id"],
                "plateau_height_m": plateau_h,
                "osm_height_m": osm_h,
                "disagreement_fraction": round(pct, 4) if pct is not None else None,
            })
    conflicting.sort(
        key=lambda r: -(r["disagreement_fraction"] or 0.0))

    doc = {
        "schema": "y92.canonical_building_entities.v1",
        "inputs": {
            "osm_world_ir": str(osm_world_ir),
            "plateau_world_ir": str(plateau_world_ir),
            "match_receipt": str(match_receipt_path),
            "mapillary_objects": str(mapillary_world_ir) if mapillary_world_ir is not None else None,
        },
        "rules": {
            "footprint": "PLATEAU authoritative when matched (IoU >= 0.5), else single source",
            "height_ladder": ("PLATEAU measuredHeight (authoritative_observed) > "
                              "OSM height tag (directly_observed) > OSM "
                              "building:levels x 3 m (derived) > locale prior "
                              "(synthetic)"),
            "conflict": "height disagreement > 15% or pair IoU < 0.5 keeps both values",
            "street": ("OSM owns position when a same-class OSM point lies within "
                       "the rules merge budget, else the Mapillary point with "
                       "sigma prior; past-budget candidates keep a "
                       "spatial_reconciliation_debt claim, never a blend"),
        },
        "counts": {
            "entities": len(entities),
            "conflated_pairs": len(pairs),
            "osm_only": len(osm_refs - set(matched_osm)),
            "plateau_only": len(plateau_ids - set(matched_plateau)),
            "dispositions": len(dispositions),
            "height_by_evidence_state": dict(sorted(by_state.items())),
            "storeys_sentinels_dropped": storeys_sentinels_dropped,
            "conflicting_heights": len(conflicting),
            "street": street_report,
        },
        "entities": entities,
        "dispositions": dispositions,
        "conflicting_heights": conflicting,
    }
    if mapillary_world_ir is None:
        # No third source: the document is byte-identical to the pre-street
        # schema so existing fusion stays provably unchanged.
        del doc["inputs"]["mapillary_objects"]
        del doc["rules"]["street"]
        del doc["counts"]["street"]
    if lidar is not None:
        # FL-4932: the new keys exist only with a roof-field summary, so the
        # document without one stays byte-identical.
        doc["inputs"]["lidar_roof_summary"] = lidar_provenance
        doc["rules"]["height_ladder"] = (
            "PLATEAU measuredHeight (authoritative_observed) > OSM height tag "
            "(directly_observed) > TMG 2023 LiDAR p90 roof when the roof field "
            f"shows a structure (median >= {TMG_STRUCTURE_MIN_MEDIAN_M:g} m; "
            "directly_observed) > OSM building:levels x 3 m (derived) > locale "
            "prior (synthetic)")
        doc["rules"]["lidar_contradiction"] = (
            "a footprint whose 2023 roof field shows no raised structure keeps "
            "its geometry and carries a dated contradiction; nothing is deleted")
        doc["counts"]["height_from_tmg"] = sum(
            1 for e in entities if e.get("kind") != "street_object"
            and e["claims"]["height_m"].get("source") == TMG_SOURCE)
        doc["counts"]["tmg_contradictions"] = len(lidar_contradictions)
        doc["tmg_contradictions"] = sorted(lidar_contradictions, key=lambda r: r["canonical_id"])
    return doc


def _entity(cid: str, osm_obj: dict | None, plateau_obj: dict | None,
            iou: float | None, height_claim: dict,
            disagreement: float | None) -> dict:
    sources = []
    if osm_obj is not None:
        sources.append({
            "source": "osm",
            "source_object_id": osm_obj["source_object_id"],
            "observation_id": osm_obj.get("obs_id"),
            "release": "triage01 OSM ingest",
            "iou_with_primary": iou,
        })
    if plateau_obj is not None:
        sources.append({
            "source": "plateau",
            "source_object_id": plateau_obj["source_object_id"],
            "observation_id": plateau_obj.get("obs_id"),
            "release": "plateau-13111-ota-ku-2025 (spec V5)",
            "iou_with_primary": iou,
        })
    sources.sort(key=lambda s: s["source"])

    if osm_obj is not None and plateau_obj is not None:
        if iou is not None and iou < MATCH_IOU_THRESHOLD_CONFLICT:
            footprint = _claim(
                "plateau", "plateau",
                f"PLATEAU footprint authoritative but pair IoU {iou} < 0.5; "
                "both geometries kept",
                "conflicting", 0.5, plateau_obj.get("observed_at"),
                values=[
                    {"value": "plateau", "source": "plateau",
                     "evidence_state": "authoritative_observed"},
                    {"value": "osm", "source": "osm",
                     "evidence_state": "directly_observed"},
                ],
            )
        else:
            footprint = _claim(
                "plateau", "plateau",
                f"PLATEAU footprint authoritative for geometry (pair IoU {iou})",
                "authoritative_observed", 0.9, plateau_obj.get("observed_at"),
            )
    elif plateau_obj is not None:
        footprint = _claim(
            "plateau", "plateau",
            "single source: the PLATEAU ring is the footprint",
            "authoritative_observed", 0.9, plateau_obj.get("observed_at"),
        )
    else:
        assert osm_obj is not None
        footprint = _claim(
            "osm", "osm", "single source: the OSM ring is the footprint",
            "directly_observed", 0.8, osm_obj.get("observed_at"),
        )

    plateau_attrs = (plateau_obj or {}).get("attributes", {}) or {}
    osm_tags = (osm_obj or {}).get("tags", {}) or {}
    plateau_temporal = (plateau_obj or {}).get("observed_at")
    osm_temporal = (osm_obj or {}).get("observed_at")

    storeys_entry = plateau_attrs.get("storeys_above_ground") or {}
    storeys_raw = storeys_entry.get("value")
    if storeys_raw is not None and storeys_raw != STOREYS_SENTINEL_MISSING:
        storeys = _claim(
            int(storeys_raw), "plateau",
            "PLATEAU bldg:storeysAboveGround, verbatim",
            "authoritative_observed", 0.9, plateau_temporal,
        )
    else:
        storeys = _claim(
            None, None, "no source storeys; storeys from PLATEAU when present",
            "unknown", 0.0, None,
        )

    usage_entry = plateau_attrs.get("usage") or {}
    osm_building = osm_tags.get("building")
    if usage_entry.get("value") is not None:
        usage_values = [
            {"value": usage_entry["value"], "source": "plateau",
             "evidence_state": "authoritative_observed"},
        ]
        if osm_building:
            usage_values.append({"value": f"building={osm_building}",
                                 "source": "osm",
                                 "evidence_state": "directly_observed"})
        usage = _claim(
            usage_entry["value"], "plateau",
            "PLATEAU bldg:usage code, verbatim; OSM building tag kept alongside "
            "(vocabularies are not conflated)",
            "authoritative_observed", 0.9, plateau_temporal,
            values=usage_values if len(usage_values) > 1 else None,
        )
    elif osm_building:
        usage = _claim(
            f"building={osm_building}", "osm", "OSM building tag, verbatim",
            "directly_observed", 0.8, osm_temporal,
        )
    else:
        usage = _claim(None, None, "no source usage", "unknown", 0.0, None)

    osm_name = osm_tags.get("name:en") or osm_tags.get("name")
    if osm_name:
        name = _claim(osm_name, "osm", "OSM name tag, verbatim",
                      "directly_observed", 0.8, osm_temporal)
    else:
        name = _claim(None, None, "no source name", "unknown", 0.0, None)

    return {
        "canonical_id": cid,
        "source_bridge": sources,
        "claims": {
            "footprint": footprint,
            "height_m": height_claim,
            "storeys": storeys,
            "usage": usage,
            "name": name,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--osm-world-ir", required=True, type=Path)
    ap.add_argument("--plateau-world-ir", required=True, type=Path)
    ap.add_argument("--match-receipt", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mapillary-objects", type=Path, default=None,
                    help="Mapillary object IR (OBSERVE-03) as third evidence source")
    ap.add_argument("--lidar-roof-summary", type=Path, default=None,
                    help="compile_lidar_roof_field.py --all roof_field_summary.json "
                         "(FL-4932): TMG height rung and dated contradictions")
    args = ap.parse_args(argv)

    doc = build_entities(args.osm_world_ir, args.plateau_world_ir,
                         args.match_receipt, args.mapillary_objects,
                         args.lidar_roof_summary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    counts = doc["counts"]
    print(f"[conflate] entities={counts['entities']} "
          f"pairs={counts['conflated_pairs']} "
          f"osm_only={counts['osm_only']} "
          f"plateau_only={counts['plateau_only']} "
          f"dispositions={counts['dispositions']}")
    print(f"[conflate] height by evidence_state: "
          f"{counts['height_by_evidence_state']}")
    print(f"[conflate] conflicting={counts['conflicting_heights']}")
    if counts.get("street") is not None:
        print(f"[conflate] street={counts['street']}")
    if "height_from_tmg" in counts:
        print(f"[conflate] tmg heights={counts['height_from_tmg']} "
              f"tmg contradictions={counts['tmg_contradictions']}")
    print(f"[conflate] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

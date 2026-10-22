#!/usr/bin/env python3
"""Rewrite triage building footprints from canonical footprint claims.

FL-4809 FOOTPRINT-01.  Stage-only footprint-authority switch, no consumer
wiring.  Input is the triage World IR; output is the same document with
building_footprint features rewritten per conflate01/canonical_entities.json:

1. A conflated entity whose footprint claim resolves to PLATEAU gets the
   PLATEAU ring (EPSG:6697 observation -> EPSG:32654 metres through the shared
   geo_frames Kruger path), keeping the OSM feature_id and every non-geometry
   claim, plus footprint_source/footprint_iou/footprint_prev_source claims.
2. Retained-unmatched plateau_only entities become new building_footprint
   features (with copied PLATEAU observations).
3. osm_only stays as is.

Guard rails: a rewritten ring must be a valid simple polygon whose area is
within 0.5x-2x of the OSM ring, else OSM is kept with reason=area_band.  A new
plateau_only ring overlapping any existing footprint by more than 10 percent
of its own area is skipped with reason=overlap.  Also emits a lon/lat diff
geojson for audit viewing.

USAGE
=====
    python3 scripts/pipeline/compile_canonical_footprints.py \
        --osm-world-ir .run/numabe_connected/triage01/osm.world_ir.json \
        --plateau-world-ir .run/numabe_connected/source01/plateau.world_ir.json \
        --canonical-entities .run/numabe_connected/conflate01/canonical_entities.json \
        --out .run/numabe_connected/footprint01/canonical.world_ir.json \
        --receipt .run/numabe_connected/footprint01/canonical_footprints_receipt.json \
        --diff .run/numabe_connected/footprint01/footprint_diff.geojson \
        --validate
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shapely.geometry import Point, Polygon  # noqa: E402

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

FRAME_PROJECTED = "epsg32654"
FRAME_PLATEAU_GEO = "plateau_source_geographic"

AREA_BAND_LO = 0.5
AREA_BAND_HI = 2.0
OVERLAP_FRACTION = 0.10
# PLATEAU Building_class.xml: 3003 普通無壁舎, 3004 堅ろう無壁舎 (no side walls).
WALL_LESS_CLASS_CODES = frozenset({"3003", "3004"})
# PLATEAU missing-value sentinel for bldg:storeysAboveGround; a sentinel
# is no measurement, so the added feature carries no storeys claim.
STOREYS_SENTINEL_MISSING = 9999


def _utm_ring(lonlat: list[list[float]]) -> list[list[float]]:
    return [[round(v, 3) for v in GF.utm_forward(lon, lat)] for lon, lat in lonlat]


def _lonlat_ring(utm: list[list[float]]) -> list[list[float]]:
    return [[round(v, 9) for v in GF.utm_inverse(x, y)] for x, y in utm]


def _close(ring: list[list[float]]) -> list[list[float]]:
    if len(ring) > 1 and ring[0] != ring[-1]:
        return [*ring, list(ring[0])]
    return [list(p) for p in ring]


def _area(ring: list[list[float]]) -> float:
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return abs(sum(
        pts[i][0] * pts[(i + 1) % len(pts)][1]
        - pts[(i + 1) % len(pts)][0] * pts[i][1]
        for i in range(len(pts))
    )) / 2.0


def _observed_points(mapillary_world_ir: Path | None) -> list[tuple]:
    """Street objects that were observed, as projected metre points.

    FL-4876 FOOTPRINT-GUARD-01. The rewrite guard needs to know where the
    observed street furniture is. The document is already an input to this
    stage; it was only being read further down, after the building loop.
    """
    if mapillary_world_ir is None:
        return []
    doc = json.loads(Path(mapillary_world_ir).read_text(encoding="utf-8"))
    out: list[tuple] = []
    for feature in doc.get("canonical_features", []):
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        out.append((str(feature.get("feature_id")),
                    str(feature.get("semantic_class")),
                    float(coords[0]), float(coords[1])))
    return out


def _newly_enclosed(new_poly: Polygon, old_ring: list[list[float]],
                    observed: list[tuple]) -> list[dict]:
    """Observed street objects the rewritten ring encloses and the old did not.

    FL-4876 FOOTPRINT-GUARD-01. An object already inside the OSM ring is
    source overlap; the rewrite did not cause it and this guard does not own
    it. Only the difference is rewrite damage.
    """
    if not observed:
        return []
    try:
        old_poly = Polygon(old_ring)
    except (ValueError, TypeError):
        return []
    if old_poly.is_empty or not old_poly.is_valid:
        return []
    minx, miny, maxx, maxy = new_poly.bounds
    swallowed: list[dict] = []
    for fid, cls, px, py in observed:
        if px < minx or px > maxx or py < miny or py > maxy:
            continue
        point = Point(px, py)
        if not new_poly.contains(point):
            continue
        if old_poly.contains(point):
            continue
        swallowed.append({"feature_id": fid, "semantic_class": cls,
                          "E": round(px, 3), "N": round(py, 3)})
    return swallowed


def _swallows_other_building(new_poly: Polygon, cid_feature_id: str,
                             osm_buildings: list[tuple]) -> dict | None:
    """The neighbour a rewritten ring would grow over, if any.

    FL-4876 SUBDIVISION-01. Buildings do not interpenetrate. The OSM document
    for this AOI has zero overlapping building pairs; applying the PLATEAU
    rewrite produces 25, totalling 423 square metres.

    Measured per pair, it is not a swapped ring. In every case one building is
    untouched and its neighbour is rewritten and grows over it, because PLATEAU
    models a block as a single footprint where OSM maps adjacent row houses
    separately. The rewrite then takes PLATEAU geometry for one house and keeps
    OSM geometry for the house next door, so the two occupy the same ground.

    Neither source is wrong. PLATEAU is not wrong to model the block, and OSM
    is not wrong to map the houses. What is wrong is applying one source to a
    building and the other to its neighbour, which yields geometry that is true
    in neither. So this refuses the rewrite, keeps both OSM rings, and records
    the disagreement rather than silently choosing a winner.

    Overlap is measured as a fraction of the OTHER building's area, which is
    how the added_plateau_only guard beside this one already reads
    OVERLAP_FRACTION.
    """
    worst: dict | None = None
    for other_id, other_poly in osm_buildings:
        if other_id == cid_feature_id:
            continue
        if not new_poly.intersects(other_poly):
            continue
        inter = new_poly.intersection(other_poly).area
        if other_poly.area <= 0:
            continue
        fraction = inter / other_poly.area
        if fraction > (worst or {}).get("fraction", 0.0):
            worst = {"feature_id": other_id,
                     "fraction": round(fraction, 4),
                     "intersection_m2": round(inter, 3),
                     "other_area_m2": round(other_poly.area, 3)}
    if worst is not None and worst["fraction"] > OVERLAP_FRACTION:
        return worst
    return None


def _claim(value: Any, method: str, obs_id: str,
           provenance: str = "derived") -> dict:
    return S.property_claim(
        value=value, provenance=provenance, method=method,
        observation_ids=[obs_id],
    )


def build(osm_world_ir: Path, plateau_world_ir: Path,
          canonical_entities_path: Path, generated_at: str,
          mapillary_world_ir: Path | None = None) -> dict:
    osm_doc = json.loads(osm_world_ir.read_text(encoding="utf-8"))
    plateau_doc = json.loads(plateau_world_ir.read_text(encoding="utf-8"))
    entities_doc = json.loads(canonical_entities_path.read_text(encoding="utf-8"))

    plateau_obs = {
        str(o["observation_id"]): o
        for o in plateau_doc.get("source_observations", [])
    }
    if FRAME_PLATEAU_GEO not in osm_doc.get("coordinate_frames", {}):
        osm_doc["coordinate_frames"][FRAME_PLATEAU_GEO] = (
            plateau_doc["coordinate_frames"][FRAME_PLATEAU_GEO])

    osm_by_ref: dict[str, dict] = {}
    for feature in osm_doc.get("canonical_features", []):
        if feature.get("semantic_class") != "building_footprint":
            continue
        for obs_id in feature.get("supporting_observation_ids", []):
            ref = None
            for doc_obs in osm_doc.get("source_observations", []):
                if doc_obs.get("observation_id") == obs_id:
                    ref = (doc_obs.get("attributes") or {}).get("source_ref")
                    break
            if ref:
                osm_by_ref[str(ref)] = feature
                break

    counts: Counter = Counter()
    area_band_reports: list[dict] = []
    overlap_reports: list[dict] = []
    # FL-4876 FOOTPRINT-GUARD-01. Read once, before the building loop.
    observed_points = _observed_points(mapillary_world_ir)
    swallow_reports: list[dict] = []
    # FL-4876 SUBDIVISION-01. The OSM buildings as they stand, so a candidate
    # rewrite can be tested against its neighbours. Built once, like the
    # points above.
    osm_buildings: list[tuple] = []
    for _bf in osm_doc.get("canonical_features", []):
        if _bf.get("semantic_class") != "building_footprint":
            continue
        try:
            _bp = Polygon([list(q) for q in
                           (_bf.get("geometry") or {}).get("coordinates", [])])
        except (ValueError, TypeError):
            continue
        if _bp.is_empty or not _bp.is_valid or _bp.area <= 0:
            continue
        osm_buildings.append((str(_bf.get("feature_id")), _bp))
    subdivision_reports: list[dict] = []
    area_changes: list[dict] = []
    diff_features: list[dict] = []
    entities = sorted(entities_doc["entities"],
                      key=lambda e: e["canonical_id"])

    input_ids = [f.get("feature_id") for f in osm_doc["canonical_features"]]
    input_building_area = {
        f.get("feature_id"): _area([list(p) for p in
                                    (f.get("geometry") or {})
                                    .get("coordinates", [])])
        for f in osm_doc["canonical_features"]
        if f.get("semantic_class") == "building_footprint"
    }
    linked_obs: set[str] = set()

    def ensure_obs(obs_id: str, feature: dict) -> None:
        """Copy the PLATEAU observation in and list it as supporting."""
        if obs_id not in linked_obs:
            linked_obs.add(obs_id)
            osm_doc["source_observations"].append(
                copy.deepcopy(plateau_obs[obs_id]))
        supporting = feature.setdefault("supporting_observation_ids", [])
        if obs_id not in supporting:
            supporting.append(obs_id)

    for entity in entities:
        if entity.get("kind") == "street_object":
            continue
        cid = entity["canonical_id"]
        bridge = {s["source"]: s for s in entity["source_bridge"]}
        footprint_claim = entity["claims"]["footprint"]
        if "osm" in bridge and "plateau" in bridge:
            ref = bridge["osm"]["source_object_id"]
            feature = osm_by_ref.get(ref)
            if feature is None:
                raise ValueError(f"{cid}: OSM {ref} not in the triage document")
            obs_id = bridge["plateau"]["observation_id"]
            obs = plateau_obs.get(obs_id or "")
            if obs is None:
                raise ValueError(f"{cid}: {obs_id} not in the PLATEAU document")
            lonlat = [list(p) for p in
                      (obs.get("geometry") or {}).get("coordinates", [])]
            new_ring = _close(_utm_ring(lonlat))
            old_ring = [list(p) for p in
                        (feature.get("geometry") or {}).get("coordinates", [])]
            old_area = _area(old_ring)
            poly = Polygon(new_ring)
            if (not poly.is_valid or poly.is_empty or poly.area <= 0
                    or not (AREA_BAND_LO * old_area
                            <= poly.area <= AREA_BAND_HI * old_area)):
                counts["kept_osm_area_band"] += 1
                reason = ("invalid_ring" if poly.is_empty or poly.area <= 0
                          or not poly.is_valid else "area_band")
                feature["properties"]["footprint_source"] = _claim(
                    "osm", "guard rail: PLATEAU ring failed validity/area-band; "
                           "OSM ring kept", obs_id)
                feature["properties"]["footprint_guard_reason"] = _claim(
                    reason, "guard rail outcome", obs_id)
                ensure_obs(obs_id, feature)
                area_band_reports.append({
                    "canonical_id": cid, "osm_source_ref": ref,
                    "plateau_gml_id": bridge["plateau"]["source_object_id"],
                    "reason": reason,
                    "osm_area_m2": round(old_area, 3),
                    "plateau_area_m2": round(poly.area, 3),
                })
                diff_features.append({
                    "type": "Feature",
                    "properties": {"change": "kept_osm_area_band",
                                   "canonical_id": cid, "reason": reason},
                    "geometry": {"type": "Polygon",
                                 "coordinates": [_lonlat_ring(old_ring)]},
                })
                continue
            # FL-4876 FOOTPRINT-GUARD-01. A rewrite may not grow a building
            # over something that was observed in the street outside it.
            #
            # Measured on this lane before the guard existed: of 17 placements
            # whose run lies 2 m or deeper inside a building, 11 were not
            # placement errors at all. The wall was correctly outside the
            # building as mapped, and the rewritten building came out over it.
            # The barrier measurement was never at fault; the building moved
            # underneath it.
            #
            # The added_plateau_only branch below has carried an overlap guard
            # all along, and it fired 71 times on this lane. This branch had
            # none. Same shape, same receipt, different question: that one asks
            # whether a NEW ring lands on an existing building, this one asks
            # whether a REWRITTEN ring swallows an observed object.
            #
            # An object already inside the OSM ring is source overlap and is
            # left alone -- 97 of them here. Only what the rewrite newly
            # encloses is its own damage. Measured cost of the guard: 6 of 556
            # rewrites refused, 1.1 percent, protecting 7 observed objects.
            # The rewrite stays a net gain on this lane, 23 penetrations fixed
            # against 11 created, so this is a guard and never a rollback.
            neighbour = _swallows_other_building(
                poly, str(feature.get("feature_id")), osm_buildings)
            if neighbour is not None:
                counts["kept_osm_subdivision_disagreement"] += 1
                feature["properties"]["footprint_source"] = _claim(
                    "osm", "guard rail: PLATEAU ring would cover a separately "
                           "mapped neighbouring building; sources disagree on "
                           "subdivision and neither is authoritative here; "
                           "OSM rings kept for both", obs_id)
                feature["properties"]["footprint_guard_reason"] = _claim(
                    "subdivision_disagreement", "guard rail outcome", obs_id)
                ensure_obs(obs_id, feature)
                subdivision_reports.append({
                    "canonical_id": cid, "osm_source_ref": ref,
                    "plateau_gml_id": bridge["plateau"]["source_object_id"],
                    "reason": "subdivision_disagreement",
                    "resolution": "unresolved",
                    "osm_area_m2": round(old_area, 3),
                    "plateau_area_m2": round(poly.area, 3),
                    "covers_neighbour": neighbour,
                    "overlap_fraction_threshold": OVERLAP_FRACTION,
                })
                diff_features.append({
                    "type": "Feature",
                    "properties": {
                        "change": "kept_osm_subdivision_disagreement",
                        "canonical_id": cid,
                        "covers_neighbour": neighbour["feature_id"],
                        "fraction_of_neighbour": neighbour["fraction"]},
                    "geometry": {"type": "Polygon",
                                 "coordinates": [_lonlat_ring(old_ring)]},
                })
                continue
            swallowed = _newly_enclosed(poly, old_ring, observed_points)
            if swallowed:
                counts["kept_osm_swallows_observation"] += 1
                feature["properties"]["footprint_source"] = _claim(
                    "osm", "guard rail: PLATEAU ring would enclose street "
                           "objects observed outside the OSM ring; OSM ring "
                           "kept", obs_id)
                feature["properties"]["footprint_guard_reason"] = _claim(
                    "swallows_observation", "guard rail outcome", obs_id)
                ensure_obs(obs_id, feature)
                swallow_reports.append({
                    "canonical_id": cid, "osm_source_ref": ref,
                    "plateau_gml_id": bridge["plateau"]["source_object_id"],
                    "reason": "swallows_observation",
                    "osm_area_m2": round(old_area, 3),
                    "plateau_area_m2": round(poly.area, 3),
                    "swallowed": swallowed,
                })
                diff_features.append({
                    "type": "Feature",
                    "properties": {"change": "kept_osm_swallows_observation",
                                   "canonical_id": cid,
                                   "swallowed_count": len(swallowed)},
                    "geometry": {"type": "Polygon",
                                 "coordinates": [_lonlat_ring(old_ring)]},
                })
                continue
            counts["rewritten_to_plateau"] += 1
            iou = bridge["plateau"].get("iou_with_primary")
            feature["geometry"] = S.geometry_polygon(FRAME_PROJECTED, new_ring)
            feature["properties"]["footprint_source"] = _claim(
                "plateau", "canonical footprint claim: PLATEAU authoritative "
                           "for geometry when matched (IoU >= 0.5)", obs_id)
            feature["properties"]["footprint_iou"] = _claim(
                iou, "pair IoU from the SOURCE-01 match table", obs_id)
            feature["properties"]["footprint_prev_source"] = _claim(
                "osm", "geometry source before the authority switch", obs_id)
            ensure_obs(obs_id, feature)
            area_changes.append({
                "canonical_id": cid,
                "feature_id": feature["feature_id"],
                "osm_source_ref": ref,
                "plateau_gml_id": bridge["plateau"]["source_object_id"],
                "iou": iou,
                "area_before_m2": round(old_area, 3),
                "area_after_m2": round(poly.area, 3),
                "area_delta_m2": round(poly.area - old_area, 3),
            })
            diff_features.append({
                "type": "Feature",
                "properties": {"change": "rewritten_to_plateau",
                               "canonical_id": cid, "iou": iou,
                               "area_before_m2": round(old_area, 3),
                               "area_after_m2": round(poly.area, 3)},
                "geometry": {"type": "Polygon",
                             "coordinates": [_lonlat_ring(new_ring)]},
            })
        elif "plateau" in bridge:
            gml_id = bridge["plateau"]["source_object_id"]
            obs_id = bridge["plateau"]["observation_id"]
            obs = plateau_obs.get(obs_id or "")
            if obs is None:
                raise ValueError(f"{cid}: {obs_id} not in the PLATEAU document")
            lonlat = [list(p) for p in
                      (obs.get("geometry") or {}).get("coordinates", [])]
            new_ring = _close(_utm_ring(lonlat))
            poly = Polygon(new_ring)
            if poly.is_empty or poly.area <= 0 or not poly.is_valid:
                counts["skipped_overlap"] += 1
                overlap_reports.append({
                    "canonical_id": cid, "plateau_gml_id": gml_id,
                    "reason": "invalid_ring", "area_m2": 0.0,
                })
                continue
            worst: dict | None = None
            for feature in osm_doc["canonical_features"]:
                if feature.get("semantic_class") != "building_footprint":
                    continue
                other = Polygon([list(p) for p in
                                 (feature.get("geometry") or {})
                                 .get("coordinates", [])])
                if other.is_empty or other.area <= 0:
                    continue
                inter = poly.intersection(other).area
                fraction = inter / poly.area
                if fraction > (worst or {}).get("fraction", 0.0):
                    worst = {"feature_id": feature.get("feature_id"),
                             "fraction": fraction, "inter_m2": inter}
            if worst is not None and worst["fraction"] > OVERLAP_FRACTION:
                counts["skipped_overlap"] += 1
                overlap_reports.append({
                    "canonical_id": cid, "plateau_gml_id": gml_id,
                    "reason": "overlap",
                    "overlap_fraction": round(worst["fraction"], 4),
                    "overlap_with": worst["feature_id"],
                    "area_m2": round(poly.area, 3),
                })
                diff_features.append({
                    "type": "Feature",
                    "properties": {"change": "skipped_overlap",
                                   "canonical_id": cid,
                                   "overlap_fraction": round(worst["fraction"], 4)},
                    "geometry": {"type": "Polygon",
                                 "coordinates": [_lonlat_ring(new_ring)]},
                })
                continue
            # FL-4876 WALL-LESS-01: PLATEAU bldg:class 3003 普通無壁舎 / 3004
            # 堅ろう無壁舎 (Building_class.xml) is a structure without side
            # walls (platform shelter, carport, open shed). Its ring does not
            # bound a volume that blocks movement, and the terrain bake can only
            # extrude a solid block to the ground, so a canopy became a wall:
            # the Numabe platform shelter bldg_65fc97db stood as a 6.6 m solid
            # 1.01 m from the track centreline, inside the stopped train's
            # envelope. A wall-less PLATEAU-only object is not admitted as a
            # building mass; it is reported, never silently dropped.
            _class = ((obs.get("attributes") or {}).get("class") or {})
            _class_code = str(_class.get("value") if isinstance(_class, dict) else _class or "")
            if _class_code in WALL_LESS_CLASS_CODES:
                counts["skipped_wall_less"] = counts.get("skipped_wall_less", 0) + 1
                overlap_reports.append({
                    "canonical_id": cid, "plateau_gml_id": gml_id,
                    "reason": "wall_less_structure",
                    "plateau_class_code": _class_code,
                    "area_m2": round(poly.area, 3),
                })
                diff_features.append({
                    "type": "Feature",
                    "properties": {"change": "skipped_wall_less",
                                   "canonical_id": cid,
                                   "plateau_class_code": _class_code},
                    "geometry": {"type": "Polygon",
                                 "coordinates": [_lonlat_ring(new_ring)]},
                })
                continue
            counts["added_plateau_only"] += 1
            new_obs = copy.deepcopy(obs)
            attrs = (new_obs.get("attributes") or {})
            height_entry = attrs.get("measured_height_m") or {}
            storeys_entry = attrs.get("storeys_above_ground") or {}
            feature_id = f"feature:building_footprint:plateau/{gml_id}"
            properties = {
                "geometry": S.property_claim(
                    value=None, provenance=S.PROVENANCE_MEASURED,
                    method=("PLATEAU lod1Solid footprint ring, projected "
                            "JGD2011 -> EPSG:32654 by geo_frames Kruger; "
                            "re-expresses the measured position"),
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_isotropic(1.0, "metre"),
                ),
                "semantic_class": S.property_claim(
                    value="building_footprint",
                    provenance=S.PROVENANCE_DERIVED,
                    method=("PLATEAU mapping rule: bldg:Building LOD solid is "
                            "a measured building volume"),
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_categorical(
                        {"building_footprint": 1.0}),
                ),
                "gameplay_authority_basis": S.property_claim(
                    value=S.AUTHORITY_COLLISION,
                    provenance=S.PROVENANCE_DERIVED,
                    method=("closed measured ring bounds a volume that "
                            "blocks movement"),
                    observation_ids=[obs_id],
                ),
                "triage_group": S.property_claim(
                    value="A", provenance=S.PROVENANCE_DERIVED,
                    method=("triage authoring group: PLATEAU-observed "
                            "building; no site-specific authoring owed"),
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_categorical({"A": 1.0}),
                ),
                "evidence_state": S.property_claim(
                    value="authoritative_observed",
                    provenance=S.PROVENANCE_DERIVED,
                    method="single source: PLATEAU is the footprint authority",
                    observation_ids=[obs_id],
                ),
                "footprint_source": S.property_claim(
                    value="plateau", provenance=S.PROVENANCE_DERIVED,
                    method="new entity from a retained_unmatched plateau_only "
                           "source object",
                    observation_ids=[obs_id],
                ),
            }
            if height_entry.get("value") is not None:
                properties["height_m"] = S.property_claim(
                    value=float(height_entry["value"]),
                    provenance=S.PROVENANCE_MEASURED,
                    method="PLATEAU bldg:measuredHeight, verbatim (uom m)",
                    observation_ids=[obs_id],
                )
            storeys_raw = storeys_entry.get("value")
            if storeys_raw is None:
                pass  # no source storeys; the added feature carries no claim
            elif storeys_raw == STOREYS_SENTINEL_MISSING:
                counts["storeys_sentinels_dropped"] += 1
            else:
                properties["storeys"] = S.property_claim(
                    value=int(storeys_raw),
                    provenance=S.PROVENANCE_MEASURED,
                    method="PLATEAU bldg:storeysAboveGround, verbatim",
                    observation_ids=[obs_id],
                )
            osm_doc["source_observations"].append(new_obs)
            osm_doc["canonical_features"].append(S.canonical_feature(
                feature_id=feature_id,
                semantic_class="building_footprint",
                geometry=S.geometry_polygon(FRAME_PROJECTED, new_ring),
                properties=properties,
                confidence=0.9,
                gameplay_authority=S.AUTHORITY_COLLISION,
                presentation=S.presentation_requirements(
                    visible=True, silhouette_required=True,
                    must_not_imply_collision=False,
                    legibility_class="landmark",
                ),
                authority_supporting_properties=["geometry", "semantic_class"],
                supporting_observation_ids=[obs_id],
            ))
            diff_features.append({
                "type": "Feature",
                "properties": {"change": "added_plateau_only",
                               "canonical_id": cid, "gml_id": gml_id,
                               "area_m2": round(poly.area, 3)},
                "geometry": {"type": "Polygon",
                             "coordinates": [_lonlat_ring(new_ring)]},
            })
        else:
            counts["osm_only"] += 1
    # FL-4809 NUMABE-MERGE-01: Mapillary street entities pass through into
    # canonical features. Merged sightings become claims on the existing OSM
    # point feature (OSM keeps the position); separate/debt groups become
    # new features copied from the Mapillary IR with canonical claims.
    # Nothing is dropped without a disposition state from the entities stage.
    street_entities = [e for e in entities if e.get("kind") == "street_object"]
    if street_entities:
        if mapillary_world_ir is None:
            raise ValueError("street entities without mapillary objects input")
        mapillary_doc = json.loads(Path(mapillary_world_ir).read_text(encoding="utf-8"))
        mapillary_obs = {str(o["observation_id"]): o
                         for o in mapillary_doc.get("source_observations", [])}
        mapillary_feats = {str(f["feature_id"]): f
                           for f in mapillary_doc.get("canonical_features", [])}
        osm_by_fid = {str(f.get("feature_id")): f
                      for f in osm_doc.get("canonical_features", [])}
        linked_mapillary_obs: set[str] = set()

        def ensure_mapillary_obs(obs_id: str, feature: dict) -> None:
            if obs_id not in linked_mapillary_obs:
                linked_mapillary_obs.add(obs_id)
                obs = mapillary_obs.get(obs_id)
                if obs is None:
                    raise ValueError(f"{obs_id} not in the Mapillary document")
                osm_doc["source_observations"].append(copy.deepcopy(obs))
            supporting = feature.setdefault("supporting_observation_ids", [])
            if obs_id not in supporting:
                supporting.append(obs_id)

        for entity in sorted(street_entities, key=lambda e: e["canonical_id"]):
            cid = entity["canonical_id"]
            observed_by = list((entity["claims"].get("observed_by") or {}).get("value") or [])
            member_oids = [str(row.get("observation_id")) for row in observed_by
                           if row.get("observation_id")]
            osm_src = next((s for s in entity["source_bridge"] if s["source"] == "osm"), None)
            if osm_src is not None:
                target = osm_by_fid.get(str(osm_src["source_object_id"]))
                if target is None:
                    raise ValueError(f"{cid}: OSM {osm_src['source_object_id']} not in output")
                for oid in member_oids:
                    ensure_mapillary_obs(oid, target)
                claims = target.setdefault("properties", {})
                claims["observed_by"] = S.property_claim(
                    value=[{"observation_id": oid, "source": "mapillary"} for oid in member_oids],
                    provenance=S.PROVENANCE_MEASURED,
                    method="Mapillary sightings fused onto the OSM point; OSM keeps the position",
                    observation_ids=member_oids)
                object_value = (entity["claims"].get("object_value") or {}).get(
                    "value")
                if not object_value:
                    raise ValueError(
                        f"{cid}: merged Mapillary entity has no object token")
                claims["object_value"] = S.property_claim(
                    value=object_value,
                    provenance=S.PROVENANCE_MEASURED,
                    method=("Mapillary object token retained while OSM owns "
                            "the merged point position"),
                    observation_ids=member_oids)
                claims["mapillary_merge"] = S.property_claim(
                    value={"canonical_id": cid,
                           "dedup_group": (entity["claims"].get("dedup_group") or {}).get("value"),
                           "frames_seen": (entity["claims"].get("frames_seen") or {}).get("value", [])},
                    provenance=S.PROVENANCE_DERIVED,
                    method="street-entity merge record; residual lives on the entity position claim",
                    observation_ids=member_oids)
                counts["merged_mapillary_claims"] += 1
                continue
            rep_mid = sorted(s["source_object_id"] for s in entity["source_bridge"]
                             if s["source"] == "mapillary")[0]
            source_feat = mapillary_feats.get(f"feature:mapillary_object/{rep_mid}")
            if source_feat is None:
                raise ValueError(f"{cid}: {rep_mid} not in the Mapillary document")
            new_feature = copy.deepcopy(source_feat)
            props = new_feature.setdefault("properties", {})
            props["canonical_entity"] = S.property_claim(
                value=cid, provenance=S.PROVENANCE_DERIVED,
                method="street-entity canonical id for this fused group",
                observation_ids=list(member_oids))
            props["observed_by"] = S.property_claim(
                value=[{"observation_id": oid, "source": "mapillary"} for oid in member_oids],
                provenance=S.PROVENANCE_MEASURED,
                method="member sighting observations, verbatim ids",
                observation_ids=list(member_oids))
            debt = (entity["claims"].get("spatial_reconciliation_debt") or {}).get("value")
            if debt:
                props["spatial_reconciliation_debt"] = S.property_claim(
                    value=debt, provenance=S.PROVENANCE_DERIVED,
                    method="candidate past the class budget; kept separate, never blended",
                    observation_ids=list(member_oids))
            for oid in member_oids:
                if oid not in linked_mapillary_obs:
                    linked_mapillary_obs.add(oid)
                    obs = mapillary_obs.get(oid)
                    if obs is None:
                        raise ValueError(f"{oid} not in the Mapillary document")
                    osm_doc["source_observations"].append(copy.deepcopy(obs))
            new_feature["supporting_observation_ids"] = list(member_oids)
            osm_doc["canonical_features"].append(new_feature)
            counts["added_mapillary"] += 1

    output_ids = [f.get("feature_id") for f in osm_doc["canonical_features"]]
    if any(fid not in output_ids for fid in input_ids):
        raise ValueError("an input feature_id did not survive")
    osm_doc["generated_at"] = generated_at
    store = WorldIRStore(doc=osm_doc)
    findings = store.validate()

    buildings = [f for f in osm_doc["canonical_features"]
                 if f.get("semantic_class") == "building_footprint"]
    area_changes.sort(key=lambda r: -abs(r["area_delta_m2"]))
    area_band_reports.sort(key=lambda r: r["canonical_id"])
    overlap_reports.sort(key=lambda r: r["canonical_id"])
    swallow_reports.sort(key=lambda r: r["canonical_id"])
    subdivision_reports.sort(key=lambda r: r["canonical_id"])
    diff_features.sort(key=lambda f: (
        f["properties"].get("canonical_id", ""),
        f["properties"].get("change", "")))

    receipt = {
        "schema": "y92.canonical_footprints.v1",
        "inputs": {
            "osm_world_ir": str(osm_world_ir),
            "plateau_world_ir": str(plateau_world_ir),
            "canonical_entities": str(canonical_entities_path),
        },
        "counts": {
            "rewritten_to_plateau": counts["rewritten_to_plateau"],
            "kept_osm_area_band": counts["kept_osm_area_band"],
            "added_plateau_only": counts["added_plateau_only"],
            "added_mapillary": counts.get("added_mapillary", 0),
            "merged_mapillary_claims": counts.get("merged_mapillary_claims", 0),
            "skipped_overlap": counts["skipped_overlap"],
            # FL-4876 FOOTPRINT-GUARD-01. A refusal nobody can read is
            # a refusal nobody can check.
            "kept_osm_swallows_observation":
                counts["kept_osm_swallows_observation"],
            "swallow_guard_ran": bool(observed_points),
            "kept_osm_subdivision_disagreement":
                counts["kept_osm_subdivision_disagreement"],
            "osm_only": counts["osm_only"],
            "storeys_sentinels_dropped": counts["storeys_sentinels_dropped"],
            "features_before": len(input_ids),
            "building_features_after": len(buildings),
        },
        "total_footprint_area_m2": {
            "before": round(sum(input_building_area.values()), 3),
            "after": round(sum(
                _area([list(p) for p in
                       (f.get("geometry") or {}).get("coordinates", [])])
                for f in buildings), 3),
        },
        "mean_iou_rewritten": (
            round(sum(r["iou"] for r in area_changes
                      if r["iou"] is not None) / len(area_changes), 4)
            if area_changes else None
        ),
        "largest_area_changes": area_changes[:10],
        "area_band_reports": area_band_reports,
        "overlap_reports": overlap_reports,
        "swallow_reports": swallow_reports,
        "subdivision_reports": subdivision_reports,
        "validation_findings": len(findings),
        "validation": [str(f) for f in findings[:20]],
    }
    diff = {
        "type": "FeatureCollection",
        "name": "numabe_footprint01_diff",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": diff_features,
    }
    return osm_doc, receipt, diff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--osm-world-ir", required=True, type=Path)
    ap.add_argument("--plateau-world-ir", required=True, type=Path)
    ap.add_argument("--canonical-entities", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mapillary-objects", type=Path, default=None,
                    help="Mapillary object IR (OBSERVE-03) for street pass-through")
    ap.add_argument("--receipt", required=True, type=Path)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    ap.add_argument("--diff", required=True, type=Path)
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    args = ap.parse_args(argv)

    doc, receipt, diff = build(
        args.osm_world_ir, args.plateau_world_ir, args.canonical_entities,
        args.generated_at, args.mapillary_objects,
    )
    findings = receipt["validation_findings"]
    if args.validate and findings:
        for line in receipt["validation"][:20]:
            print(f"  FAIL {line}", file=sys.stderr)
        print(f"[footprints] {findings} finding(s); nothing written",
              file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(S.canonical_bytes(doc))
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    args.diff.parent.mkdir(parents=True, exist_ok=True)
    args.diff.write_text(
        json.dumps(diff, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    counts = receipt["counts"]
    print(f"[footprints] rewritten={counts['rewritten_to_plateau']} "
          f"kept_band={counts['kept_osm_area_band']} "
          f"added={counts['added_plateau_only']} "
          f"skipped_overlap={counts['skipped_overlap']} "
          f"osm_only={counts['osm_only']} findings={findings}")
    print(f"[footprints] mean_iou={receipt['mean_iou_rewritten']} "
          f"area={receipt['total_footprint_area_m2']}")
    print(f"[footprints] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

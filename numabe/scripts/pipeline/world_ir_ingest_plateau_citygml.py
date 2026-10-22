#!/usr/bin/env python3
"""Ingest PLATEAU CityGML bldg tiles into a Y92 World IR document.

FL-4809 SOURCE-01, sibling of ``world_ir_ingest_osm_extract.py``.  Ingests the
**raw frozen source**: Ota-ku FY2025 (spec V5) CityGML 2.0 + uro 3.2
``bldg`` tiles in EPSG:6697 (JGD2011), before any bake exists.  tran/dem tiles
are fetched as raw files for later lanes; this owner parses bldg only.

WHAT THIS OWNER DOES
====================
For every ``bldg:Building`` whose footprint touches the AOI it emits one
SourceObservation (JGD2011 lon/lat geometry, verbatim attributes) and one
``building_footprint`` CanonicalFeature (footprint reprojected to the run
projection).  No conflation, no claim resolution, no height change to the
build: the lane only makes the source exist as observations with a coverage
number.

PROJECTION AND DATUM
====================
Source geometry stays in EPSG:6697 on the observation - what PLATEAU said,
unaltered - and the canonical feature carries the same footprint re-expressed
in the run projection (``terrain_metadata.json`` origin_easting/northing,
content_scale) through the same UTM zone 54N Kruger code path the OSM ingest
uses (``scripts/pipeline/geo_frames.py``).  Approximation, stated once:
JGD2011 geographic coordinates are fed numerically as the Kruger input, so the
static-vs-dynamic datum difference between JGD2011 and WGS84 (decimetre order
in 2026) survives as a rigid shift.  The IoU>=0.5 match threshold tolerates it;
a later lane that fuses heights must solve the datum properly.

FOOTPRINT
=========
Ota 2025 v5 bldg carries LOD1 solids (no LOD2, no lod0FootPrint in the AOI
tiles).  The footprint is the lod1Solid exterior LinearRing with the lowest
mean z, falling back to the first lod0RoofEdge exterior ring.  Which source
won is recorded per feature as ``footprint_source``.

USAGE
=====
    python3 scripts/pipeline/world_ir_ingest_plateau_citygml.py \
        --plateau-dir assets/source_bundles/.../plateau \
        --aoi assets/source_bundles/.../connected_scope.geojson \
        --terrain-metadata .run/numabe_connected/build_final/terrain_metadata.json \
        --osm-world-ir .run/numabe_connected/osm.world_ir.json \
        --out .run/numabe_connected/source01/plateau.world_ir.json \
        --summary .run/numabe_connected/source01/plateau_ingest_receipt.json \
        --validate
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shapely.geometry import Polygon  # noqa: E402
from shapely.prepared import prep  # noqa: E402

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "uro": "https://www.geospatial.jp/iur/uro/3.2",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
}
GML_ID = "{http://www.opengis.net/gml}id"

SOURCE_ID = "plateau_ota_ku_2025_citygml"
SOURCE_KIND = "vector_map"
OBSERVED_AT = "2026-03-16"
FRAME_SOURCE_GEO = "plateau_source_geographic"
FRAME_RUN = "plateau_run_projection"

# Authoring working tolerance, not a surveyed RMSE: the dataset record claims
# map-information-level 2500 equivalence and publishes no per-vertex RMSE.
PLATEAU_POSITION_SIGMA_M = 1.0
POSITION_SIGMA_BASIS = (
    "authoring working tolerance bounded by the dataset quality statement "
    "(map-information-level 2500 equivalence); PLATEAU publishes no per-vertex "
    "RMSE, so this is not a surveyed value"
)

MATCH_IOU_THRESHOLD = 0.5


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _poslist_triples(element: ET.Element) -> list[tuple[float, float, float]]:
    """GML posList in EPSG:6697 axis order (lat, lon, z) -> (lon, lat, z)."""
    words = element.text.split()
    triples = []
    for i in range(0, len(words) - 2, 3):
        lat, lon, z = float(words[i]), float(words[i + 1]), float(words[i + 2])
        triples.append((lon, lat, z))
    return triples


def _exterior_rings(solid_or_surface: ET.Element) -> list[list[tuple[float, float, float]]]:
    rings = []
    for polygon in solid_or_surface.findall(".//{http://www.opengis.net/gml}Polygon"):
        exterior = polygon.find("{http://www.opengis.net/gml}exterior")
        if exterior is None:
            continue
        ring = exterior.find("{http://www.opengis.net/gml}LinearRing")
        if ring is None:
            continue
        poslist = ring.find("{http://www.opengis.net/gml}posList")
        if poslist is None or not poslist.text:
            continue
        triples = _poslist_triples(poslist)
        if len(triples) >= 4:
            rings.append(triples)
    return rings


def _ring_key(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop the closing duplicate and collinear-free dedupe, order preserved."""
    closed = list(points)
    if len(closed) > 1 and closed[0] == closed[-1]:
        closed.pop()
    out: list[tuple[float, float]] = []
    for point in closed:
        if not out or out[-1] != point:
            out.append(point)
    return out


def _footprint_from_building(building: ET.Element) -> tuple[list[tuple[float, float]] | None, str]:
    lod1 = building.find("bldg:lod1Solid", NS)
    if lod1 is not None:
        rings = _exterior_rings(lod1)
        if rings:
            scored = []
            for triples in rings:
                lonlat = [(lon, lat) for lon, lat, _ in triples]
                mean_z = sum(z for _, _, z in triples) / len(triples)
                scored.append((mean_z, lonlat))
            scored.sort(key=lambda row: (row[0], -Polygon(row[1]).area if len(row[1]) > 2 else 0.0))
            return _ring_key(scored[0][1]), "lod1Solid_lowest_ring"
    roof = building.find("bldg:lod0RoofEdge", NS)
    if roof is not None:
        rings = _exterior_rings(roof)
        if rings:
            lonlat = [(lon, lat) for lon, lat, _ in rings[0]]
            return _ring_key(lonlat), "lod0RoofEdge_first_ring"
    return None, "no_footprint_ring"


def _child_text(building: ET.Element, name: str) -> tuple[str | None, str | None]:
    element = building.find(f"bldg:{name}", NS)
    if element is None:
        return None, None
    codespace = element.get("codeSpace")
    text = (element.text or "").strip() or None
    return text, codespace


def _address_text(building: ET.Element) -> str | None:
    address = building.find("bldg:address", NS)
    if address is None:
        return None
    leaves = [text.strip() for text in address.itertext() if text.strip()]
    seen: list[str] = []
    for leaf in leaves:
        if leaf not in seen:
            seen.append(leaf)
    return " / ".join(seen) if seen else None


def _lod_inventory(building: ET.Element) -> list[str]:
    return sorted(
        name for name in (
            "lod0FootPrint", "lod0RoofEdge", "lod1Solid",
            "lod2Solid", "lod2MultiSurface",
        )
        if building.find(f"bldg:{name}", NS) is not None
    )


def _member_gml_id(member: ET.Element) -> str:
    building = member.find("bldg:Building", NS)
    target = building if building is not None else member
    return target.get(GML_ID) or ""


class PlateauIngest:
    """AOI bldg tiles -> World IR observations plus footprint features."""

    def __init__(self, plateau_dir: Path, aoi_path: Path, terrain_metadata_path: Path,
                 osm_world_ir_path: Path | None, *, ir_id: str, generated_at: str) -> None:
        self.plateau_dir = Path(plateau_dir)
        self.metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
        self.scale = float(self.metadata["content_scale"])
        self.origin_easting = float(self.metadata["origin_easting"])
        self.origin_northing = float(self.metadata["origin_northing"])
        self.offset_x = float(self.metadata.get("offset_x", 0.0))
        self.offset_y = float(self.metadata.get("offset_y", 0.0))
        geojson = json.loads(aoi_path.read_text(encoding="utf-8"))
        ring = None
        for feat in geojson["features"]:
            if feat.get("properties", {}).get("role") == "aoi":
                ring = feat["geometry"]["coordinates"][0]
                break
        if ring is None:
            raise SystemExit(f"[plateau-ingest] {aoi_path} has no role=aoi feature")
        self.aoi_ring_lonlat = [(point[0], point[1]) for point in ring]
        self.aoi_polygon = Polygon(self.aoi_ring_lonlat)
        self.aoi_prepared = prep(self.aoi_polygon)
        self.osm_world_ir_path = Path(osm_world_ir_path) if osm_world_ir_path else None

        self.store = WorldIRStore.new(ir_id, generated_at=generated_at)
        self.store.add_frame(S.coordinate_frame(
            frame_id=FRAME_SOURCE_GEO, crs="EPSG:6697", units="degree",
            axis_order=["lon", "lat"],
            note="JGD2011 geographic, stored lon/lat; what PLATEAU said, unaltered",
        ))
        self.store.add_frame(S.coordinate_frame(
            frame_id=FRAME_RUN, crs="local:run_projection", units="world",
            note=(f"run projection from {terrain_metadata_path.name}: "
                  f"EPSG:32654 zone 54N easting/northing via geo_frames Kruger, "
                  f"world = (utm - origin) * {self.scale} + offset "
                  f"(origin {self.origin_easting},{self.origin_northing})"),
        ))

        self.tile_files = sorted(
            glob.glob(str(self.plateau_dir / "udx" / "bldg" / "*_bldg_6697_op.gml"))
        )
        if not self.tile_files:
            raise SystemExit(f"[plateau-ingest] no bldg tiles in {self.plateau_dir}/udx/bldg/")
        self.counts: Counter = Counter()
        self.skipped: list[dict] = []

    # -- projection ------------------------------------------------------

    def run_project(self, lon: float, lat: float) -> tuple[float, float]:
        easting, northing = GF.utm_forward(lon, lat)
        return (
            (easting - self.origin_easting) * self.scale + self.offset_x,
            (northing - self.origin_northing) * self.scale + self.offset_y,
        )

    # -- records ----------------------------------------------------------

    def _observation(self, gml_id: str, tile_sha: str, attributes: dict,
                     footprint_lonlat: list[tuple[float, float]]) -> str:
        obs_id = f"obs:plateau:{gml_id}"
        self.store.add_observation(S.source_observation(
            observation_id=obs_id,
            source_id=SOURCE_ID,
            source_kind=SOURCE_KIND,
            observed_at=OBSERVED_AT,
            attributes=attributes,
            geometry=S.geometry_polygon(
                FRAME_SOURCE_GEO,
                [[round(lon, 9), round(lat, 9)] for lon, lat in footprint_lonlat],
            ),
            quality={
                "position_sigma_m": PLATEAU_POSITION_SIGMA_M,
                "position_sigma_basis": POSITION_SIGMA_BASIS,
            },
            digest=tile_sha,
        ))
        return obs_id

    def _feature(self, gml_id: str, footprint_lonlat: list[tuple[float, float]],
                 footprint_source: str, obs_id: str, attributes: dict) -> dict:
        projected = [self.run_project(lon, lat) for lon, lat in footprint_lonlat]
        rounded = [[round(x, 6), round(y, 6)] for x, y in projected]
        uncertainty = S.uncertainty_isotropic(PLATEAU_POSITION_SIGMA_M, "metre")
        properties = {
            "geometry": S.property_claim(
                value=None,
                provenance=S.PROVENANCE_MEASURED,
                method=("PLATEAU lod1Solid footprint ring, projected JGD2011 -> "
                        "EPSG:32654 by scripts/pipeline/geo_frames.py (Kruger "
                        "n-series, 6th order) then the run-projection affine; the "
                        "projection re-expresses the measured position, it does "
                        "not derive a new one. JGD2011 coordinates are fed "
                        "numerically, so the static-vs-dynamic datum shift "
                        "survives as a rigid offset"),
                observation_ids=[obs_id],
                uncertainty=uncertainty,
            ),
            "semantic_class": S.property_claim(
                value="building_footprint",
                provenance=S.PROVENANCE_DERIVED,
                method="PLATEAU mapping rule: bldg:Building LOD solid is a "
                       "measured building volume, so its ground ring is a "
                       "building_footprint",
                observation_ids=[obs_id],
                uncertainty=S.uncertainty_categorical({"building_footprint": 1.0}),
            ),
            "gameplay_authority_basis": S.property_claim(
                value=S.AUTHORITY_COLLISION,
                provenance=S.PROVENANCE_DERIVED,
                method="closed measured ring bounds a volume that blocks movement",
                observation_ids=[obs_id],
            ),
            "footprint_source": S.property_claim(
                value=footprint_source,
                provenance=S.PROVENANCE_DERIVED,
                method="ingest ring-selection rule: lod1Solid lowest-mean-z "
                       "exterior ring, else first lod0RoofEdge exterior ring",
                observation_ids=[obs_id],
            ),
        }
        measured = {
            "measured_height_m": ("measuredHeight", "bldg:measuredHeight, verbatim (uom m)"),
            "storeys_above_ground": ("storeysAboveGround", "bldg:storeysAboveGround, verbatim"),
            "storeys_below_ground": ("storeysBelowGround", "bldg:storeysBelowGround, verbatim"),
            "usage_code": ("usage", "bldg:usage code, verbatim with its codeSpace"),
            "class_code": ("class", "bldg:class code, verbatim with its codeSpace"),
            "source_notes": ("source_notes",
                             "PLATEAU gen:stringAttribute notes, verbatim"),
            "address_text": ("address", "bldg:address xAL leaves flattened, verbatim"),
            "plateau_building_id": ("building_id", "uro BuildingIDAttribute buildingID, verbatim"),
        }
        for claim_name, (attr_key, method) in measured.items():
            entry = attributes.get(attr_key)
            if entry is None:
                continue
            properties[claim_name] = S.property_claim(
                value=entry["value"],
                provenance=S.PROVENANCE_MEASURED,
                method=f"PLATEAU {method}",
                observation_ids=[obs_id],
            )
        properties["lod_inventory"] = S.property_claim(
            value=attributes["lod_inventory"],
            provenance=S.PROVENANCE_DERIVED,
            method="structural inventory of the source Building element",
            observation_ids=[obs_id],
        )
        building_id = attributes.get("building_id", {}).get("value")
        feature = self.store.add_feature(S.canonical_feature(
            feature_id=f"feature:building_footprint:plateau/{gml_id}",
            semantic_class="building_footprint",
            geometry=S.geometry_polygon(FRAME_RUN, rounded),
            properties=properties,
            confidence=0.9,
            gameplay_authority=S.AUTHORITY_COLLISION,
            presentation=S.presentation_requirements(
                visible=True,
                silhouette_required=True,
                must_not_imply_collision=False,
                legibility_class="landmark",
            ),
            authority_supporting_properties=["geometry", "semantic_class"],
            labels={"plateau_building_id": building_id} if building_id else None,
        ))
        return feature

    # -- ingest ------------------------------------------------------------

    @staticmethod
    def _sha256(path: str) -> str:
        import hashlib
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def ingest_tiles(self) -> list[dict]:
        kept: list[dict] = []
        for tile_path in self.tile_files:
            tile_sha = self._sha256(tile_path)
            root = ET.parse(tile_path).getroot()
            members = root.findall("core:cityObjectMember", NS)
            members.sort(key=lambda m: _member_gml_id(m))
            for member in members:
                building = member.find("bldg:Building", NS)
                if building is None:
                    self.counts["non_building_members"] += 1
                    continue
                gml_id = building.get(GML_ID)
                self.counts["buildings_read"] += 1
                footprint, source = _footprint_from_building(building)
                if footprint is None or len(footprint) < 3:
                    self.counts["skipped_no_footprint_ring"] += 1
                    self.skipped.append({"gml_id": gml_id, "reason": source})
                    continue
                poly = Polygon(footprint)
                if not poly.is_valid or poly.is_empty:
                    poly = poly.buffer(0)
                    self.counts["rings_repaired"] += 1
                if poly.is_empty or poly.area <= 0:
                    self.counts["skipped_degenerate"] += 1
                    self.skipped.append({"gml_id": gml_id, "reason": "degenerate ring"})
                    continue
                if not self.aoi_prepared.intersects(poly):
                    self.counts["buildings_clipped_away"] += 1
                    continue
                self.counts["buildings_in_aoi"] += 1
                footprint_lonlat = _ring_key([(float(x), float(y)) for x, y in poly.exterior.coords])
                measured_height, _ = _child_text(building, "measuredHeight")
                storeys_above, _ = _child_text(building, "storeysAboveGround")
                storeys_below, _ = _child_text(building, "storeysBelowGround")
                usage, usage_cs = _child_text(building, "usage")
                klass, klass_cs = _child_text(building, "class")
                bid_attr = building.find("uro:buildingIDAttribute", NS)
                bid = None
                if bid_attr is not None:
                    bid_el = bid_attr.find("uro:BuildingIDAttribute/uro:buildingID", NS)
                    if bid_el is not None and (bid_el.text or "").strip():
                        bid = bid_el.text.strip()
                lod_inventory = _lod_inventory(building)
                if any(name in ("lod2Solid", "lod2MultiSurface") for name in lod_inventory):
                    self.counts["with_lod2"] += 1
                if measured_height is not None:
                    self.counts["with_measured_height"] += 1
                if storeys_above is not None:
                    self.counts["with_storeys"] += 1
                attributes: dict[str, Any] = {
                    "source_ref": gml_id,
                    "tile": Path(tile_path).name,
                    "lod_inventory": lod_inventory,
                }
                if measured_height is not None:
                    attributes["measured_height_m"] = {
                        "value": float(measured_height), "uom": "m"}
                if storeys_above is not None:
                    attributes["storeys_above_ground"] = {"value": int(storeys_above)}
                if storeys_below is not None:
                    attributes["storeys_below_ground"] = {"value": int(storeys_below)}
                if usage is not None:
                    attributes["usage"] = {"value": usage, "codeSpace": usage_cs}
                if klass is not None:
                    attributes["class"] = {"value": klass, "codeSpace": klass_cs}
                # FL-4876 USECLASS-01. PLATEAU states a building's kind twice:
                # once as bldg:usage, a numeric code whose codelist this
                # bundle does not ship, and once as a free-text
                # gen:stringAttribute note. The note is plain Japanese --
                # measured on this tile: 車庫 x212 (garage), 物置 x240
                # (storage shed), 自転車置場 x61 (bicycle shed), 倉庫 x28
                # (warehouse), 駅舎 x10 (station building), 売店 x3 (kiosk).
                # It was read by nothing, so a shed and a station both
                # arrived downstream as an untyped building and were given
                # the same residential facade. Carried verbatim; no code
                # here decides what any of it means.
                notes = {}
                for gen_attr in building.iter():
                    if not str(gen_attr.tag).endswith("stringAttribute"):
                        continue
                    name = gen_attr.get("name")
                    value_el = next(
                        (child for child in gen_attr
                         if str(child.tag).endswith("value")), None)
                    if name and value_el is not None \
                            and (value_el.text or "").strip():
                        notes[str(name)] = value_el.text.strip()
                if notes:
                    attributes["source_notes"] = {"value": notes}
                address = _address_text(building)
                if address is not None:
                    attributes["address"] = {"value": address}
                if bid is not None:
                    attributes["building_id"] = {"value": bid}
                obs_id = self._observation(gml_id, tile_sha, attributes, footprint_lonlat)
                feature = self._feature(gml_id, footprint_lonlat, source, obs_id, attributes)
                kept.append({
                    "gml_id": gml_id,
                    "feature_id": feature["feature_id"],
                    "footprint_lonlat": footprint_lonlat,
                    "area_m2": round(Polygon(
                        [GF.utm_forward(lon, lat) for lon, lat in footprint_lonlat]
                    ).area, 3),
                })
        kept.sort(key=lambda row: row["gml_id"])
        return kept

    # -- match table ---------------------------------------------------------

    def match_against_osm(self, kept: list[dict]) -> dict:
        table: dict[str, Any] = {
            "predicate": ("greedy one-to-one IoU in the UTM plane; "
                          f"IoU >= {MATCH_IOU_THRESHOLD} is matched"),
            "osm_world_ir": str(self.osm_world_ir_path) if self.osm_world_ir_path else None,
            "matched": 0,
            "plateau_only": 0,
            "osm_only": 0,
            "mean_matched_iou": None,
            "largest_unmatched": [],
        }
        if self.osm_world_ir_path is None:
            table["predicate"] += "; skipped (no --osm-world-ir)"
            return table
        osm_doc = json.loads(self.osm_world_ir_path.read_text(encoding="utf-8"))
        osm_polys: list[dict] = []
        for obs in osm_doc.get("source_observations", []):
            attrs = obs.get("attributes", {}) or {}
            if not isinstance(attrs.get("tags"), dict):
                continue
            tags = attrs["tags"]
            if tags.get("building") is None:
                continue
            geom = obs.get("geometry") or {}
            if geom.get("kind") != "polygon":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 3:
                continue
            try:
                poly = Polygon([GF.utm_forward(lon, lat) for lon, lat in coords])
            except (TypeError, ValueError):
                continue
            if poly.is_empty or poly.area <= 0:
                continue
            if not self.aoi_prepared.intersects(Polygon(coords)):
                continue
            osm_polys.append({
                "source_ref": attrs.get("source_ref"),
                "area_m2": round(poly.area, 3),
                "_poly": poly,
            })
        plateau_polys = []
        for row in kept:
            poly = Polygon([GF.utm_forward(lon, lat) for lon, lat in row["footprint_lonlat"]])
            plateau_polys.append({**row, "_poly": poly})
        pairs = []
        for pi, prow in enumerate(plateau_polys):
            for oi, orow in enumerate(osm_polys):
                if not prow["_poly"].bounds or not orow["_poly"].bounds:
                    continue
                pb, ob = prow["_poly"].bounds, orow["_poly"].bounds
                if pb[2] < ob[0] or ob[2] < pb[0] or pb[3] < ob[1] or ob[3] < pb[1]:
                    continue
                inter = prow["_poly"].intersection(orow["_poly"]).area
                if inter <= 0:
                    continue
                union = prow["_poly"].union(orow["_poly"]).area
                iou = inter / union if union > 0 else 0.0
                if iou >= MATCH_IOU_THRESHOLD:
                    pairs.append((iou, pi, oi))
        pairs.sort(reverse=True)
        used_p, used_o = set(), set()
        matched_rows = []
        for iou, pi, oi in pairs:
            if pi in used_p or oi in used_o:
                continue
            used_p.add(pi)
            used_o.add(oi)
            matched_rows.append({
                "plateau_gml_id": plateau_polys[pi]["gml_id"],
                "osm_source_ref": osm_polys[oi]["source_ref"],
                "iou": round(iou, 4),
            })
        matched_rows.sort(key=lambda row: row["plateau_gml_id"])
        unmatched = []
        for pi, prow in enumerate(plateau_polys):
            if pi not in used_p:
                unmatched.append({
                    "side": "plateau_only",
                    "ref": prow["gml_id"],
                    "area_m2": prow["area_m2"],
                })
        for oi, orow in enumerate(osm_polys):
            if oi not in used_o:
                unmatched.append({
                    "side": "osm_only",
                    "ref": orow["source_ref"],
                    "area_m2": orow["area_m2"],
                })
        unmatched.sort(key=lambda row: -row["area_m2"])
        table.update({
            "osm_buildings_in_aoi": len(osm_polys),
            "plateau_buildings_in_aoi": len(plateau_polys),
            "matched": len(matched_rows),
            "plateau_only": len(plateau_polys) - len(used_p),
            "osm_only": len(osm_polys) - len(used_o),
            "mean_matched_iou": (
                round(sum(r["iou"] for r in matched_rows) / len(matched_rows), 4)
                if matched_rows else None
            ),
            "matches": matched_rows,
            "largest_unmatched": unmatched[:20],
        })
        return table

    # -- run -------------------------------------------------------------------

    def run(self, generated_at: str) -> tuple[WorldIRStore, dict]:
        kept = self.ingest_tiles()
        match = self.match_against_osm(kept)
        tile_records = []
        for tile_path in self.tile_files:
            tile_records.append({
                "path": str(Path(tile_path).relative_to(REPO_ROOT))
                if Path(tile_path).is_absolute() else str(tile_path),
                "mesh": Path(tile_path).name.split("_")[0],
                "kind": "bldg",
                "size_bytes": Path(tile_path).stat().st_size,
                "sha256": self._sha256(tile_path),
            })
        receipt = {
            "schema": "y92.plateau_ingest.v1",
            "ir_id": self.store.to_dict().get("ir_id"),
            "generated_at": generated_at,
            "release": {
                "ckan_id": "plateau-13111-ota-ku-2025",
                "title": "3D都市モデル（Project PLATEAU）大田区（2025年度）",
                "spec": "3D都市モデル標準製品仕様書第5.0版 (CityGML 2.0 + uro 3.2)",
                "dataset_created": "2026-03-16",
                "dataset_modified": "2026-04-03",
                "url": "https://www.geospatial.jp/ckan/dataset/plateau-13111-ota-ku-2025",
            },
            "licence": {
                "title": "PLATEAU Site Policy (public-data licence PDL1.0 compatible, "
                         "CC BY 4.0 compatible; attribution required)",
                "url": "https://www.mlit.go.jp/plateau/site-policy/",
                "copyright": "Ota City (3D city-model open data copyright belongs "
                             "to the local government)",
                "attribution": "出典：大田区 3D都市モデル（Project PLATEAU）大田区（2025年度）",
            },
            "crs": {
                "source": "EPSG:6697 (JGD2011 geographic, lat/lon order in GML)",
                "heights": "metres (bldg:measuredHeight uom=m)",
                "projected": "EPSG:32654 zone 54N via scripts/pipeline/geo_frames.py "
                             "Kruger n-series, then the run-projection affine",
                "datum_note": ("JGD2011 coordinates are fed numerically to the shared "
                               "Kruger path; the static-vs-dynamic datum shift "
                               "survives as a rigid offset, inside the IoU margin"),
            },
            "tiles": tile_records,
            "counts": {
                "tiles_read": len(self.tile_files),
                "buildings_read": self.counts["buildings_read"],
                "buildings_in_aoi": self.counts["buildings_in_aoi"],
                "buildings_clipped_away": self.counts["buildings_clipped_away"],
                "with_lod2": self.counts["with_lod2"],
                "with_measured_height": self.counts["with_measured_height"],
                "with_storeys": self.counts["with_storeys"],
                "skipped_no_footprint_ring": self.counts["skipped_no_footprint_ring"],
                "skipped_degenerate": self.counts["skipped_degenerate"],
                "rings_repaired": self.counts["rings_repaired"],
                "non_building_members": self.counts["non_building_members"],
                "observations_emitted": len(self.store.observations),
                "features_emitted": len(self.store.features),
            },
            "coverage": {
                "buildings_in_aoi": self.counts["buildings_in_aoi"],
                "with_lod2": self.counts["with_lod2"],
                "with_measured_height": self.counts["with_measured_height"],
                "with_storeys": self.counts["with_storeys"],
            },
            "match": match,
            "skipped": sorted(self.skipped, key=lambda row: row["gml_id"]),
            "projection": {
                "origin_easting": self.origin_easting,
                "origin_northing": self.origin_northing,
                "content_scale": self.scale,
                "offset": [self.offset_x, self.offset_y],
            },
            "validation_findings": len(self.store.validate()),
        }
        return self.store, receipt


def build(plateau_dir: Path, aoi_path: Path, terrain_metadata_path: Path,
          osm_world_ir_path: Path | None, *, ir_id: str = "numabe_plateau",
          generated_at: str = "2026-09-19T00:00:00Z") -> tuple[WorldIRStore, dict]:
    return PlateauIngest(
        plateau_dir, aoi_path, terrain_metadata_path, osm_world_ir_path,
        ir_id=ir_id, generated_at=generated_at,
    ).run(generated_at)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plateau-dir", type=Path, required=True,
                    help="plateau/ tile directory holding udx/bldg/*.gml")
    ap.add_argument("--aoi", type=Path, required=True,
                    help="AOI geojson with a role=aoi polygon")
    ap.add_argument("--terrain-metadata", type=Path, required=True,
                    help="run projection (origin_easting/northing, content_scale)")
    ap.add_argument("--osm-world-ir", type=Path, default=None,
                    help="OSM World IR for the footprint match table")
    ap.add_argument("--out", type=Path, required=True,
                    help="World IR document to write")
    ap.add_argument("--summary", type=Path, help="write the ingest receipt here")
    ap.add_argument("--ir-id", default="numabe_plateau")
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    args = ap.parse_args(argv)

    store, receipt = build(
        args.plateau_dir, args.aoi, args.terrain_metadata, args.osm_world_ir,
        ir_id=args.ir_id, generated_at=args.generated_at,
    )

    findings = store.validate()
    assert receipt["validation_findings"] == len(findings)
    if args.validate and findings:
        for finding in findings[:20]:
            print(f"  FAIL {finding}", file=sys.stderr)
        print(f"[plateau-ingest] {len(findings)} finding(s); nothing written",
              file=sys.stderr)
        return 1

    store.save(args.out)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(f"[plateau-ingest] tiles={receipt['counts']['tiles_read']} "
          f"read={receipt['counts']['buildings_read']} "
          f"in_aoi={receipt['counts']['buildings_in_aoi']} "
          f"features={receipt['counts']['features_emitted']} "
          f"findings={len(findings)}")
    print(f"[plateau-ingest] coverage: {receipt['coverage']}")
    match = receipt["match"]
    print(f"[plateau-ingest] match: matched={match['matched']} "
          f"plateau_only={match['plateau_only']} osm_only={match['osm_only']} "
          f"mean_iou={match['mean_matched_iou']}")
    print(f"[plateau-ingest] wrote {args.out}"
          + (f" and {args.summary}" if args.summary else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

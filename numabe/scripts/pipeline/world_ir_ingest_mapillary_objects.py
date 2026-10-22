#!/usr/bin/env python3
"""Ingest Mapillary object layers into a canonical World IR document.

Reads raw map_features responses (AOI bbox sweep plus the sign-filtered
view) and per-image detection chunks, and writes
.run/numabe_connected/observe03/mapillary_objects.world_ir.json plus an
ingest receipt.

Mapping rule: object_value -> closed-registry semantic class via
OBJECT_CLASS below. The registry (world_ir.schema.SEMANTIC_CLASSES) has no
bench, street_light, manhole, or traffic-device classes, so those map to
the generic `fixture` (or `sign` for sign faces) with the original
object_value preserved in a claim and in the receipt mapping table. Values
with no table entry fall back to `fixture` and are listed in the receipt
unmapped table with counts; nothing is dropped silently.

Detections carry no world position, so they become per-image observations
(pixel-frame geometry) referencing the existing survey picture_id. No
features are invented from detections.

Merge candidates (receipt only): a mapped feature within 3 m of an OSM
point feature of the same mapped class is a conflation candidate for a
later lane. This script never edits canonical entities.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema  # noqa: E402

# object_value -> (registry semantic_class, note). Only exact values match;
# anything else is an unmapped fallback (fixture + receipt row).
OBJECT_CLASS: dict[str, tuple[str, str]] = {
    "object--support--utility-pole": ("utility_pole", "pole support"),
    "object--support--pole": ("utility_pole", "generic support pole"),
    "object--catenary": ("utility_wire", "overhead contact system point"),
    "object--support--traffic-sign-frame": ("sign", "sign support structure"),
    "object--street-light": ("fixture", "registry has no street_light class"),
    "object--bench": ("fixture", "registry has no bench class"),
    "object--bike-rack": ("fixture", "registry has no bike_rack class"),
    "object--trash-can": ("fixture", "registry has no trash_can class"),
    "object--fire-hydrant": ("fixture", "registry has no hydrant class"),
    "object--mailbox": ("fixture", "registry has no mailbox class"),
    "object--phone-booth": ("fixture", "registry has no phone_booth class"),
    "object--junction-box": ("fixture", "roadside cabinet"),
    "object--catch-basin": ("fixture", "drain grate furniture"),
    "object--water-valve": ("fixture", "valve cover furniture"),
    "object--cctv-camera": ("fixture", "pole camera"),
    "object--traffic-cone": ("fixture", "movable; position is capture-time only"),
    "object--manhole": ("fixture", "registry has no manhole class; flush road furniture"),
    "object--sign--store": ("sign", "storefront sign face"),
    "object--sign--advertisement": ("sign", "advertising face"),
    "object--sign--information": ("sign", "information face"),
    "object--sign--other": ("sign", "unclassified sign face"),
    "object--sign--back": ("sign", "sign reverse face"),
    "object--banner": ("sign", "banner face"),
    "object--traffic-sign--front": ("sign", "sign front face"),
    "object--traffic-sign--back": ("sign", "sign reverse face"),
    "object--traffic-sign--direction-front": ("sign", "direction sign face"),
    "object--traffic-sign--direction-back": ("sign", "direction sign face"),

    "object--traffic-light--general-horizontal": ("fixture", "signal device; registry has no traffic_light class"),
    "object--traffic-light--general-horizontal-side": ("fixture", "signal device"),
    "object--traffic-light--general-horizontal-back": ("fixture", "signal device"),
    "object--traffic-light--general-horizontal-front": ("fixture", "signal device"),
    "object--traffic-light--general-upright": ("fixture", "signal device"),
    "object--traffic-light--general-upright-front": ("fixture", "signal device"),
    "object--traffic-light--other": ("fixture", "signal device"),
    "object--traffic-light--pedestrians": ("fixture", "pedestrian signal device"),
    "object--traffic-light--pedestrians-side": ("fixture", "pedestrian signal device"),
    "object--traffic-light--pedestrians-front": ("fixture", "pedestrian signal device"),
    "object--traffic-light--pedestrians-back": ("fixture", "pedestrian signal device"),
    "marking--discrete--crosswalk-zebra": ("road_marking", "paint"),
    "marking--discrete--other-marking": ("road_marking", "paint"),
    "marking--discrete--text--other": ("road_marking", "paint"),
    "marking--discrete--text": ("road_marking", "paint"),
    "marking--discrete--stop-line": ("road_marking", "paint"),
    "marking--discrete--symbol--bicycle": ("road_marking", "paint"),
    "marking--discrete--arrow--straight": ("road_marking", "paint"),
    "marking--discrete--arrow--split-left-or-straight": ("road_marking", "paint"),
    "construction--flat--driveway": ("fixture", "flat construction feature; point position only, least-wrong generic class"),
}

SIGN_PREFIXES = ("object--traffic-sign--", "object--sign--", "regulatory--", "warning--", "information--")

ATTRIBUTION = "Mapillary contributors"
LICENCE = "CC BY-SA 4.0"
DEDUP_RADIUS_M = 1.0
# Declared prior: Mapillary serves no per-feature sigma. Consumer-GPS plus
# structure-from-motion positions are metre-scale; 5 m is an explicit prior,
# not a measurement. Recorded in the geometry claim and the receipt.
MAPILLARY_POSITION_SIGMA_M = 5.0


def _dedup_groups(items: list[tuple[str, str, str, float, float]]) -> dict[str, str]:
    """Greedy same-class same-value clustering within DEDUP_RADIUS_M.

    Items are (feature_id, semantic_class, object_value, x, y). Returns
    feature_id -> dedup group id. Groups are candidate-level only: nothing
    is merged here; the later chain decides. Singletons keep their own id
    as the group id so every feature carries one.
    """
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for fid, _cls, _val, _x, _y in items:
        parent[fid] = fid
    ordered = sorted(items, key=lambda row: row[0])
    for pos, (fid, cls, val, x, y) in enumerate(ordered):
        for other, ocls, oval, ox, oy in ordered[:pos]:
            if ocls == cls and oval == val and math.dist((x, y), (ox, oy)) <= DEDUP_RADIUS_M:
                root_a, root_b = find(fid), find(other)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)
    groups: dict[str, list[str]] = {}
    for fid, _cls, _val, _x, _y in ordered:
        groups.setdefault(find(fid), []).append(fid)
    names = {root: f"mapillary_dedup/{num:04d}" for num, root in enumerate(sorted(groups), 1)}
    out: dict[str, str] = {}
    for root, members in groups.items():
        if len(members) == 1:
            out[members[0]] = members[0]
        else:
            for member in members:
                out[member] = names[root]
    return out
LICENCE_URI = "https://creativecommons.org/licenses/by-sa/4.0/"


def _iso_ms(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        stamp = datetime.datetime.fromtimestamp(float(ms) / 1000.0, datetime.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_raw(raw_dir: Path) -> tuple[list[dict], list[str]]:
    """All map features, deduplicated by Mapillary id across raw files."""
    seen: dict[str, dict] = {}
    dupes: list[str] = []
    for path in sorted(raw_dir.glob("map_features*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc.get("data", []) or []:
            mid = str(row.get("id"))
            if mid in seen:
                dupes.append(mid)
                continue
            seen[mid] = row
    return list(seen.values()), dupes


def _load_detections(raw_dir: Path) -> tuple[list[dict], Counter, set[str]]:
    rows: list[dict] = []
    for path in sorted(raw_dir.glob("detections_images_*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc.get("data", []) or []:
            if not row.get("error") and row.get("value"):
                rows.append(row)
    hist = Counter(r.get("value") for r in rows)
    images = {r["image_id"] for r in rows}
    return rows, hist, images


def build(raw_dir: Path, osm_world_ir: Path, generated_at: str) -> tuple[dict, dict]:
    features_raw, dupes = _load_raw(raw_dir)
    det_rows, det_hist, det_images = _load_detections(raw_dir)
    # ROOT AMENDMENT (licensing): per-record categories, not one licence.
    # 1 JPEG imagery CC BY-SA 4.0; 2 image metadata platform terms; 3 image
    # detections platform terms (+ODbL guidance if republished); 4 map
    # features/traffic signs same as 3. This ingest reads categories 3+4.
    licence_files = [
        {"file": path.name, "licence_category": 4,
         "licence_name": "Mapillary Platform Terms (derived metadata)",
         "terms_reference": "Mapillary Platform Terms; ODbL guidance applies if republished",
         "attribution": ATTRIBUTION}
        for path in sorted(raw_dir.glob("map_features*.json"))
    ] + [
        {"file": path.name, "licence_category": 3,
         "licence_name": "Mapillary Platform Terms (derived metadata)",
         "terms_reference": "Mapillary Platform Terms; ODbL guidance applies if republished",
         "attribution": ATTRIBUTION}
        for path in sorted(raw_dir.glob("detections_images_*.json"))
    ]
    licence_histogram = dict(sorted(Counter(f["licence_category"] for f in licence_files).items()))

    by_value = Counter(r.get("object_value") for r in features_raw)
    store = WorldIRStore()
    store.add_frame(schema.coordinate_frame(
        "epsg4326", "EPSG:4326", "degree",
        note="WGS84 lon/lat exactly as served by Mapillary; unaltered",
    ))
    store.add_frame(schema.coordinate_frame(
        "epsg32654", "EPSG:32654", "metre",
        note="shared Kruger projection used by the OSM ingest (geo_frames)",
    ))
    store.add_frame(schema.coordinate_frame(
        "mapillary_image_pixels", "local:image-pixels", "pixel",
        note="detection bounding geometry in source image pixels; no world position implied",
    ))

    by_class: Counter = Counter()
    unmapped: Counter = Counter()
    mapped_table: dict[str, dict] = {}
    for value in sorted(by_value):
        if value in OBJECT_CLASS:
            cls, note = OBJECT_CLASS[value]
            mapped_table[value] = {"semantic_class": cls, "note": note,
                                   "count": by_value[value], "mapped": True}
        else:
            cls = "fixture"
            unmapped[value] += by_value[value]
            mapped_table[value] = {
                "semantic_class": cls, "mapped": False,
                "note": "no mapping-table entry; generic fixture fallback, original value kept in claim",
                "count": by_value[value],
            }

    for row in sorted(features_raw, key=lambda r: str(r.get("id"))):
        mid = str(row.get("id"))
        value = row.get("object_value")
        cls = mapped_table[value]["semantic_class"]
        by_class[cls] += 1
        geom = row.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        lon, lat = float(coords[0]), float(coords[1])
        easting, northing = GF.utm_forward(lon, lat)
        observed_at = _iso_ms(row.get("first_seen_at")) or generated_at
        obs = schema.source_observation(
            observation_id=f"obs:mapillary:mf/{mid}",
            source_kind="vector_map",
            source_id="mapillary",
            observed_at=observed_at,
            attributes={
                "object_value": value,
                "object_type": row.get("object_type"),
                "first_seen_at": _iso_ms(row.get("first_seen_at")),
                "last_seen_at": _iso_ms(row.get("last_seen_at")),
                "image_ids": [str(i) for i in (row.get("images") or [])],
                "aligned_direction_deg": row.get("aligned_direction"),
                "licence": LICENCE,
                "attribution": ATTRIBUTION,
            },
            geometry=schema.geometry_point("epsg4326", [lon, lat]),
        )
        store.add_observation(obs)
        props: dict[str, dict] = {
            "semantic_class": schema.property_claim(
                cls, "derived",
                "OBJECT_CLASS mapping table; original object_value kept in object_value claim",
                [obs["observation_id"]], confidence=0.7,
            ),
            "object_value": schema.property_claim(
                value, "measured", "Mapillary map_features object_value verbatim",
                [obs["observation_id"]], confidence=0.9,
            ),
            "geometry": schema.property_claim(
                [easting, northing], "measured",
                "shared Kruger projection of served WGS84 lon/lat",
                [obs["observation_id"]], confidence=0.6,
                uncertainty=schema.uncertainty_isotropic(MAPILLARY_POSITION_SIGMA_M, "metre"),
            ),
        }
        if row.get("aligned_direction") is not None:
            props["yaw_deg"] = schema.property_claim(
                float(row["aligned_direction"]), "measured",
                "Mapillary aligned_direction verbatim", [obs["observation_id"]],
            )
        # Chain candidacy: the frames this candidate was seen in travel with
        # the feature for detection/normalisation/dedup/match/association.
        props["frames_seen"] = schema.property_claim(
            sorted({str(i) for i in (row.get("images") or [])}), "measured",
            "Mapillary map_features images verbatim", [obs["observation_id"]],
        )
        store.add_feature(schema.canonical_feature(
            feature_id=f"feature:mapillary_object/{mid}",
            semantic_class=cls,
            geometry=schema.geometry_point("epsg32654", [easting, northing]),
            properties=props,
            confidence=0.7,
            gameplay_authority=schema.AUTHORITY_PRESENTATION_ONLY,
            presentation=schema.presentation_requirements(
                visible=True, must_not_imply_collision=True, legibility_class="secondary",
                notes=f"Mapillary {value}",
            ),
            labels={"object_value": str(value)},
            authority_supporting_properties=["geometry", "semantic_class"],
        ))

    map_feats = [f for f in store.features
                 if f["feature_id"].startswith("feature:mapillary_object/")]
    group_of = _dedup_groups([
        (f["feature_id"], f["semantic_class"],
         (f.get("labels") or {}).get("object_value", ""),
         f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1])
        for f in map_feats
    ])
    dedup_multi: dict[str, list[str]] = {}
    for feat in map_feats:
        gid = group_of[feat["feature_id"]]
        feat["properties"]["dedup_group_id"] = schema.property_claim(
            gid, "derived",
            "candidate-level cross-view dedup group; singletons carry their own feature id",
            list(feat.get("supporting_observation_ids", [])),
        )
        if gid != feat["feature_id"]:
            dedup_multi.setdefault(gid, []).append(feat["feature_id"])
    for members in dedup_multi.values():
        members.sort()
    det_count = 0
    for idx, row in enumerate(sorted(det_rows, key=lambda r: (r["image_id"], str(r.get("value"))))):
        iid = row["image_id"]
        geom = row.get("geometry") or {}
        coords = geom.get("coordinates")
        pixel_geom = None
        if isinstance(coords, list) and coords:
            flat = coords[0] if coords and isinstance(coords[0], list) else coords
            try:
                poly = [[float(p[0]), float(p[1])] for p in flat
                        if isinstance(p, (list, tuple)) and len(p) >= 2]
            except (TypeError, ValueError):
                poly = []
            if len(poly) >= 3:
                pixel_geom = {"kind": "polygon", "frame_id": "mapillary_image_pixels",
                              "coordinates": poly}
        store.add_observation(schema.source_observation(
            observation_id=f"obs:mapillary:det/{iid}/{idx:05d}",
            source_id="mapillary",
            source_kind="imagery",
            observed_at=generated_at,
            attributes={
                "image_id": iid,
                "detection_value": row.get("value"),
                "licence": LICENCE,
                "attribution": ATTRIBUTION,
            },
            geometry=pixel_geom,
        ))
        det_count += 1

    # Merge candidates against OSM point features of the same mapped class.
    osm = json.loads(osm_world_ir.read_text(encoding="utf-8"))
    osm_points: dict[str, list[tuple[float, float, str]]] = {}
    for feat in osm["canonical_features"]:
        if feat["geometry"]["kind"] != "point":
            continue
        cls = feat["semantic_class"]
        x, y = feat["geometry"]["coordinates"][:2]
        osm_points.setdefault(cls, []).append((float(x), float(y), feat["feature_id"]))
    merge_candidates: dict[str, list[dict]] = {}
    for feat in store.features:
        if not feat["feature_id"].startswith("feature:mapillary_object/"):
            continue
        cls = feat["semantic_class"]
        x, y = feat["geometry"]["coordinates"][:2]
        for ox, oy, oid in osm_points.get(cls, []):
            if math.dist((x, y), (ox, oy)) <= 3.0:
                merge_candidates.setdefault(cls, []).append({
                    "mapillary_id": feat["feature_id"],
                    "osm_id": oid,
                    "distance_m": round(math.dist((x, y), (ox, oy)), 3),
                })
    for rows in merge_candidates.values():
        rows.sort(key=lambda r: (r["mapillary_id"], r["osm_id"]))

    doc = {
        "schema_version": store.schema_version if hasattr(store, "schema_version") else None,
        "ir_id": "mapillary_objects",
        "coordinate_frames": dict(store.frames) if hasattr(store, "frames") else {},
        "source_observations": list(store.observations),
        "canonical_features": list(store.features),
        "generated_representations": [],
        "generated_at": generated_at,
    }
    # Drop None schema_version; WorldIRStore.save path uses schema.document.
    doc = {k: v for k, v in doc.items() if v is not None}
    receipt = {
        "schema": "y92.mapillary_objects_ingest.v1",
        "generated_at": generated_at,
        "licence": LICENCE,
        "licence_uri": LICENCE_URI,
        "attribution": ATTRIBUTION,
        "counts": {
            "map_features_raw": len(features_raw),
            "dedup_merges": len(dupes),
            "features_emitted": sum(1 for f in store.features
                                    if f["feature_id"].startswith("feature:mapillary_object/")),
            "feature_observations": sum(1 for o in store.observations
                                        if o["observation_id"].startswith("obs:mapillary:mf/")),
            "detection_observations": det_count,
            "images_with_detections": len(det_images),
        },
        "counts_by_object_value": dict(sorted(by_value.items())),
        "counts_by_mapped_class": dict(sorted(by_class.items())),
        "mapping_table": mapped_table,
        "unmapped_values": dict(sorted(unmapped.items())),
        "dedup_merged_ids": sorted(set(dupes)),
        "detection_value_histogram": dict(sorted(det_hist.items())),
        "merge_candidates_per_class": {
            cls: len(rows) for cls, rows in sorted(merge_candidates.items())
        },
        "merge_candidates": merge_candidates,
        "dedup_groups_multi": {gid: members for gid, members in sorted(dedup_multi.items())},
        "dedup_group_counts": {
            "multi_member_groups": len(dedup_multi),
            "singleton_features": len(map_feats) - sum(len(m) for m in dedup_multi.values()),
        },
        "licence_categories": {
            "1": {"scope": "Mapillary JPEG imagery", "licence_name": "CC BY-SA 4.0",
                  "terms_reference": "https://creativecommons.org/licenses/by-sa/4.0/",
                  "attribution": ATTRIBUTION},
            "2": {"scope": "image metadata (pose, capture time, camera params)",
                  "licence_name": "Mapillary Platform Terms (derived metadata)",
                  "terms_reference": "Mapillary Platform Terms",
                  "attribution": ATTRIBUTION},
            "3": {"scope": "per-image detections",
                  "licence_name": "Mapillary Platform Terms (derived metadata)",
                  "terms_reference": "Mapillary Platform Terms; ODbL guidance applies if republished",
                  "attribution": ATTRIBUTION},
            "4": {"scope": "map features and traffic signs",
                  "licence_name": "Mapillary Platform Terms (derived metadata)",
                  "terms_reference": "Mapillary Platform Terms; ODbL guidance applies if republished",
                  "attribution": ATTRIBUTION},
        },
        "licence_histogram": licence_histogram,
        "licence_files": licence_files,
        "amendments": [
            "ROOT AMENDMENT (licensing): per-record licence categories 1-4 replace the blanket CC BY-SA line; "
            "this ingest reads category 3 (detections) and 4 (map features); histogram counts input files by category.",
            "ROOT AMENDMENT (chain candidacy): features carry frames_seen, normalised semantic_class, and "
            "dedup_group_id for the downstream detection/normalisation/dedup/match/association/decision chain; "
            "groups are candidate-level only, nothing merged here.",
            "Registry deviation recorded: SEMANTIC_CLASSES has no bench/street_light/manhole/traffic-device "
            "classes, so those map to fixture/sign with the verbatim object_value kept in a claim and in mapping_table.",
        ],
    }
    return doc, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--raw-dir", type=Path, required=True)
    ap.add_argument("--osm-world-ir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)
    doc, receipt = build(args.raw_dir, args.osm_world_ir, args.generated_at)
    # Validate through the owned store before writing.
    store = WorldIRStore.loads(json.dumps(doc))
    store.assert_valid()
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    args.receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[mapillary-ingest] features={receipt['counts']['features_emitted']} "
          f"detections={receipt['counts']['detection_observations']} "
          f"unmapped_values={len(receipt['unmapped_values'])}")
    print(f"[mapillary-ingest] wrote {args.out} and {args.receipt_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

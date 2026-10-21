#!/usr/bin/env python3
"""Ingest an OSM/carto bake run into a Y92 World IR document.

FL-4811 ingest owner. Sits DELIBERATELY OUTSIDE ``scripts/pipeline/world_ir/``.

WHY THE SPLIT
=============
The IR must name no source: ``fixture`` stays ``fixture`` whether the evidence
came from an OSM tag, a triangulated street observation, or a reviewed
annotation. So the class registry and the validator live in the ``world_ir``
package and know nothing about carto runs, while every mapping from one
source's private vocabulary onto the registry lives here, in a per-source
ingest owner. A second source (street imagery, DEM tiles, reviewed controls)
gets its own sibling ingest owner and never edits the IR.

WHY THIS IS A MODULE AND NOT TEST CODE
======================================
This mapping was first written inside the FL-4811 test, which made pytest the
only way to produce a World IR document from a real run. That is the same
defect FL-4787 hit when its appearance compilers were reachable only by a
human running seven scripts in a private order. Ingest is a pipeline product,
so it gets a CLI and a real owner:

    python3 scripts/pipeline/world_ir_ingest_carto.py \
        --features <run>/output_terrain_only.a3d.carto_features.jsonl \
        --building-instances <run>/building_instances.json \
        --out world_ir.json --validate

LEGACY SIDECAR HANDLING (not a permanent accommodation)
=======================================================
``osm_carto_stamper.py`` USED to write

    "world_vertices": world_pts if len(world_pts) <= 40
                      else world_pts[:40] + ["...truncated"]

which put a STRING inside a coordinate array and dropped vertices: 6 of the 391
rows in the shipped SBU run are lossy that way and ``way/55446707`` lost 33 of
its 73 points. **That producer is now fixed** -- it emits the complete ring plus
``world_vertex_count``, guarded by
``scripts/tests/test_fl4811_carto_feature_geometry_is_complete.py``.

The repair path below therefore exists ONLY to read sidecars written by the old
producer, such as the already-shipped SBU run whose artifacts are historical
evidence and are not regenerated. It is a defensive importer for legacy files,
not a contract the IR is expected to honour forever: geometry comes from the
complete source where one exists (``building_instances.json``), and otherwise
from the row's own complete ``world_bounds`` as an explicitly DERIVED ring
carrying bounds-sized uncertainty, never silently as measured truth. A freshly
stamped run should exercise neither branch, and the ingest receipt reports the
counts so that stays visible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

# The OSM vector frame the run bakes into: a project-local planar frame, not an
# authority CRS. Declaring it explicitly is what keeps the IR locale-neutral.
A3D_FRAME = S.coordinate_frame(
    frame_id="a3d_world",
    crs="local:a3d_world",
    units="a3d_world_units",
    axis_order=["x", "y", "z"],
    vertical_datum="local:terrain_ground",
    note="planar frame of one OSM bake run; origin is the run bbox anchor",
)

# OSM node positions are a surveyed vector source, not a sensor: the spread is
# the digitising tolerance, expressed once here instead of guessed per record.
OSM_POSITION_SIGMA = 1.5

DEFAULT_OBSERVED_AT = "2026-06-01T03:39:00Z"

TRUNCATION_MARKER = "...truncated"

CARTO_KIND_TO_SEMANTIC_CLASS = {
    "building": "building_footprint",
    "footway": "sidewalk",
    "path": "sidewalk",
    "steps": "sidewalk",
    "pedestrian_area": "sidewalk",
    "road_residential": "road",
    "cycleway": "road",
    "parking": "road",
    "grass": "terrain",
    "garden": "terrain",
    "wood": "terrain",
    "water": "terrain",
    "tree": "tree",
    "hedge": "hedge",
    "bus_stop": "fixture",
    "bicycle_parking": "fixture",
    "memorial": "fixture",
    "monument_artwork": "fixture",
}

SEMANTIC_CLASS_AUTHORITY = {
    "building_footprint": S.AUTHORITY_COLLISION,
    "road": S.AUTHORITY_WALKABILITY,
    "sidewalk": S.AUTHORITY_WALKABILITY,
    "terrain": S.AUTHORITY_WALKABILITY,
    "tree": S.AUTHORITY_PRESENTATION_ONLY,
    "hedge": S.AUTHORITY_PRESENTATION_ONLY,
    "fixture": S.AUTHORITY_PRESENTATION_ONLY,
}


def is_truncated(row: dict) -> bool:
    """True for a LEGACY row whose coordinate array holds a marker string.

    Tests the shape (a non-coordinate element) rather than one spelling of the
    marker, so a differently-worded truncation is still caught. Fresh runs from
    the repaired producer return False for every row; when ``world_vertex_count``
    is present it is also cross-checked, which catches silent loss that leaves
    no marker at all.
    """
    verts = row["world_vertices"]
    if any(not isinstance(pt, (list, tuple)) for pt in verts):
        return True
    declared = row.get("world_vertex_count")
    return declared is not None and int(declared) != len(verts)


def bounds_ring(bounds: dict) -> list[list[float]]:
    return [
        [bounds["min_x"], bounds["min_y"]],
        [bounds["max_x"], bounds["min_y"]],
        [bounds["max_x"], bounds["max_y"]],
        [bounds["min_x"], bounds["max_y"]],
        [bounds["min_x"], bounds["min_y"]],
    ]


def load_carto_features(features_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(features_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_building_footprints(building_instances_path: Path | None) -> dict[str, list]:
    """Complete footprints keyed by OSM ref, used to repair truncated rows."""
    if not building_instances_path:
        return {}
    path = Path(building_instances_path)
    if not path.is_file():
        return {}
    specs = json.loads(path.read_text(encoding="utf-8"))
    return {s["source"]: s["footprint"] for s in specs if s.get("source") and s.get("footprint")}


def carto_geometry(row: dict, footprints: dict | None = None) -> tuple[dict, str, dict]:
    """Return (geometry, provenance, uncertainty) for one real carto row."""
    ref = f"{row['osm_type']}/{row['osm_id']}"
    measured = S.uncertainty_isotropic(OSM_POSITION_SIGMA, "a3d_world_units")
    if is_truncated(row):
        complete = (footprints or {}).get(ref)
        if complete:
            return (
                S.geometry_polygon("a3d_world", complete),
                S.PROVENANCE_MEASURED,
                measured,
            )
        bounds = row["world_bounds"]
        span = max(bounds["max_x"] - bounds["min_x"], bounds["max_y"] - bounds["min_y"])
        return (
            S.geometry_polygon("a3d_world", bounds_ring(bounds)),
            S.PROVENANCE_DERIVED,
            S.uncertainty_bounds(0.0, float(span), "a3d_world_units"),
        )
    verts = row["world_vertices"]
    if row.get("is_polygon") and len(verts) >= 3:
        return S.geometry_polygon("a3d_world", verts), S.PROVENANCE_MEASURED, measured
    if len(verts) >= 2:
        return S.geometry_polyline("a3d_world", verts), S.PROVENANCE_MEASURED, measured
    return S.geometry_point("a3d_world", verts[0]), S.PROVENANCE_MEASURED, measured


def ingest_carto_row(
    store: WorldIRStore,
    row: dict,
    footprints: dict | None = None,
    observed_at: str = DEFAULT_OBSERVED_AT,
) -> tuple[dict, dict]:
    """Turn one real carto feature into (SourceObservation, CanonicalFeature)."""
    ref = f"{row['osm_type']}/{row['osm_id']}"
    obs_id = f"obs:vector:{ref}#{row['feature_id']}"
    semantic_class = CARTO_KIND_TO_SEMANTIC_CLASS[row["feature_kind"]]
    geometry, geometry_provenance, geometry_uncertainty = carto_geometry(row, footprints)
    obs = store.add_observation(
        S.source_observation(
            observation_id=obs_id,
            source_id="osm_vector_extract",
            source_kind="vector_map",
            observed_at=observed_at,
            attributes={
                "source_ref": ref,
                "source_kind_token": row["feature_kind"],
                "tags": row["source_tags"],
                "bounds": row["world_bounds"],
                "centroid": row["world_centroid"],
                "vertices_truncated_by_source": is_truncated(row),
            },
            geometry=geometry,
        )
    )
    feature = store.add_feature(
        S.canonical_feature(
            feature_id=f"feature:{semantic_class}:{ref}#{row['feature_id']}",
            semantic_class=semantic_class,
            geometry=geometry,
            properties={
                "geometry": S.property_claim(
                    value=None,
                    provenance=geometry_provenance,
                    method="OSM way/node coordinates projected into the run frame"
                    if geometry_provenance == S.PROVENANCE_MEASURED
                    else "row vertices truncated by the source; ring reconstructed "
                    "from the row's complete world_bounds",
                    observation_ids=[obs_id],
                    uncertainty=geometry_uncertainty,
                ),
                "semantic_class": S.property_claim(
                    value=semantic_class,
                    provenance=S.PROVENANCE_DERIVED,
                    method="source token mapped onto the IR class registry",
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_categorical({semantic_class: 1.0}),
                ),
            },
            confidence=0.9,
            gameplay_authority=SEMANTIC_CLASS_AUTHORITY[semantic_class],
            presentation=S.presentation_requirements(
                visible=True,
                silhouette_required=semantic_class in {"tree", "building_footprint"},
                legibility_class="landmark"
                if semantic_class == "building_footprint"
                else "secondary",
            ),
        )
    )
    return obs, feature


def build_document(
    features_path: Path,
    building_instances_path: Path | None = None,
    *,
    ir_id: str = "carto_run",
    generated_at: str = "2026-06-01T03:39:00Z",
    observed_at: str = DEFAULT_OBSERVED_AT,
) -> tuple[WorldIRStore, dict]:
    """Ingest a whole carto run. Returns (store, receipt)."""
    rows = load_carto_features(features_path)
    footprints = load_building_footprints(building_instances_path)

    store = WorldIRStore.new(ir_id, generated_at=generated_at)
    store.add_frame(A3D_FRAME)

    per_class: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    truncated = 0
    repaired = 0
    for row in rows:
        kind = row.get("feature_kind")
        if kind not in CARTO_KIND_TO_SEMANTIC_CLASS:
            unmapped[kind] = unmapped.get(kind, 0) + 1
            continue
        if is_truncated(row):
            truncated += 1
            if f"{row['osm_type']}/{row['osm_id']}" in footprints:
                repaired += 1
        _obs, feature = ingest_carto_row(store, row, footprints, observed_at=observed_at)
        cls = feature["semantic_class"]
        per_class[cls] = per_class.get(cls, 0) + 1

    receipt = {
        "features_path": str(features_path),
        "building_instances_path": str(building_instances_path) if building_instances_path else None,
        "rows_read": len(rows),
        "features_emitted": len(store.features),
        "observations_emitted": len(store.observations),
        "features_by_class": dict(sorted(per_class.items())),
        "unmapped_source_kinds": dict(sorted(unmapped.items())),
        "rows_truncated_by_source": truncated,
        "truncated_rows_repaired_from_complete_footprint": repaired,
        "truncated_rows_lowered_to_derived_bounds_ring": truncated - repaired,
    }
    return store, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--features", type=Path, required=True,
                    help="*.carto_features.jsonl from an OSM bake run")
    ap.add_argument("--building-instances", type=Path,
                    help="building_instances.json; repairs source-truncated footprints")
    ap.add_argument("--out", type=Path, required=True, help="World IR document to write")
    ap.add_argument("--ir-id", default="carto_run")
    ap.add_argument("--summary", type=Path, help="write the ingest receipt here")
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    args = ap.parse_args(argv)

    store, receipt = build_document(args.features, args.building_instances, ir_id=args.ir_id)

    findings = store.validate()
    receipt["validation_findings"] = len(findings)
    if args.validate and findings:
        for f in findings[:20]:
            print(f"  FAIL {f}", file=sys.stderr)
        print(f"[world-ir-ingest] {len(findings)} finding(s); nothing written", file=sys.stderr)
        return 1

    store.save(args.out)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")

    print(f"[world-ir-ingest] rows={receipt['rows_read']} "
          f"features={receipt['features_emitted']} "
          f"observations={receipt['observations_emitted']} "
          f"findings={len(findings)}")
    print(f"[world-ir-ingest] by class: {receipt['features_by_class']}")
    if receipt["unmapped_source_kinds"]:
        print(f"[world-ir-ingest] UNMAPPED source kinds: {receipt['unmapped_source_kinds']}")
    print(f"[world-ir-ingest] source-truncated rows={receipt['rows_truncated_by_source']} "
          f"repaired={receipt['truncated_rows_repaired_from_complete_footprint']} "
          f"lowered_to_bounds_ring={receipt['truncated_rows_lowered_to_derived_bounds_ring']}")
    print(f"[world-ir-ingest] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

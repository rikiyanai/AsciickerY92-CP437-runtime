#!/usr/bin/env python3
"""Ingest a solved street-imagery corridor into a Y92 World IR document.

FL-4812 ingest owner. A sibling of ``world_ir_ingest_carto.py``, and like it,
DELIBERATELY OUTSIDE ``scripts/pipeline/world_ir/``.

WHY A SECOND INGEST OWNER AND NOT A BRANCH IN THE FIRST
=======================================================
The IR names no source: a ``fixture`` is a ``fixture`` whether a mapper tagged
it or a solve triangulated it. So the class registry and the validator stay in
``world_ir`` knowing nothing about cameras, the carto owner keeps its own
vocabulary mapping, and this file owns exactly one thing - how a camera solve's
private vocabulary (captures, tracks, reprojection residuals, gate decisions)
lands on the registry. Adding a third source adds a third sibling and edits
neither of the other two.

    python3 scripts/pipeline/world_ir_ingest_cameras.py \
        --items <items.json> --frames <dir> --osm <control.osm> \
        --out world_ir.json --receipt receipt.json --validate

WHAT THIS OWNER REFUSES TO DO
=============================
1.  It never promotes reconstructed geometry to physical authority.  Every
    feature it emits is ``presentation_only`` or ``none``.  A triangulated
    point is a good enough reason to DRAW something and not a good enough
    reason to let a player stand on it, and the gate that would change that is
    a separate fusion decision this file does not make.
2.  It never promotes a candidate its gates refused.  A refused candidate is
    still emitted - the observations are real evidence and deleting them would
    lose the refusal too - but as a feature whose conflict state is
    ``unresolved`` and whose gameplay authority is ``none``.
3.  It never launders a missing intrinsic.  Where the source stated no focal
    length, the emitted intrinsics claim is ``derived`` with the solved value
    and its posterior sigma, never ``measured``.

CONTROL VS TARGET
=================
Every record this owner writes carries ``evidence_role`` /
``evidence_role_note`` naming the corridor as CONTROL / METHOD PROOF.  The
corridor proves the machinery. It is not acceptance evidence for any other
corridor, and a document that forgot to say so would be read as if it were.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve import receipts  # noqa: E402
from scripts.pipeline.camera_solve.capture import (  # noqa: E402
    INTRINSICS_MEASURED,
    Capture,
    horizontal_fov_deg,
)
from scripts.pipeline.camera_solve.geodesy import EnuFrame  # noqa: E402
from scripts.pipeline.camera_solve.georegister import GeoSolution  # noqa: E402
from scripts.pipeline.camera_solve.solve import CorridorInputs, run  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

EVIDENCE_ROLE = "geometric_registration_evidence"
EVIDENCE_ROLE_NOTE = (
    "Geometric registration evidence. Records the observation-to-world solve and its "
    "map constraints. NOT acceptance evidence for a complete playable corridor."
)

# Reconstructed geometry is presentation evidence.  Promoting it to walkability
# or collision is a separate fusion decision with its own gate, and this owner
# does not hold that gate.  Stated once, here, rather than per call site.
RECONSTRUCTED_AUTHORITY = S.AUTHORITY_PRESENTATION_ONLY
REFUSED_AUTHORITY = S.AUTHORITY_NONE


def coordinate_frame_for(frame: EnuFrame) -> dict:
    return S.coordinate_frame(
        frame_id=frame.frame_id,
        crs=frame.crs,
        units="m",
        axis_order=["east", "north", "up"],
        vertical_datum="wgs84_ellipsoid_offset_from_frame_origin",
        note=frame.origin_note,
    )


def camera_observation(capture: Capture, solution: GeoSolution, frame_index: int,
                       enu_frame: EnuFrame) -> dict:
    """One capture, as the source stated it plus what the solve made of it."""
    center = solution.centers[frame_index]
    cov = solution.center_covariance.get(frame_index)
    lon, lat, alt = enu_frame.to_geodetic(*center)
    return S.source_observation(
        observation_id=f"obs:imagery:{capture.source_id}/{capture.capture_id}",
        source_id=capture.source_id,
        source_kind="imagery",
        observed_at=capture.captured_at,
        attributes={
            "evidence_role": EVIDENCE_ROLE,
            "evidence_role_note": EVIDENCE_ROLE_NOTE,
            "capture_id": capture.capture_id,
            "rank_in_sequence": capture.rank,
            "licence": capture.licence,
            "attribution": capture.attribution,
            "producer": capture.producer,
            "source_provider": capture.source_provider,
            "source_url": capture.source_url,
            "source_image_dimensions_px": list(
                capture.source_dimensions_px or (capture.width, capture.height)
            ),
            "source_image_sha256": capture.digest,
            "working_image_width_px": capture.width,
            "working_image_height_px": capture.height,
            "working_image_sha256": capture.working_image_digest or capture.digest,
            "working_image_policy": capture.working_image_policy,
            "stated_by_source": {
                "gps_lon": capture.lon,
                "gps_lat": capture.lat,
                "gps_altitude_m": capture.alt,
                "view_azimuth_deg": capture.azimuth_deg,
                "horizontal_accuracy_m": capture.horizontal_accuracy_m,
                "focal_length_state": capture.intrinsics.state,
                "focal_length_basis": capture.intrinsics.basis,
            },
            "solved_by_this_ingest": {
                "center_enu_m": [float(v) for v in center],
                "center_lon": lon,
                "center_lat": lat,
                "center_ellipsoidal_height_m": alt,
                "rotation_rodrigues_world_to_camera": [float(v) for v in solution.rvecs[frame_index]],
                "azimuth_deg": solution.azimuth_deg(frame_index),
                "focal_px": solution.focal_px,
                # NaN GUARD: GeoSolution.focal_sigma_px defaults to NaN and is
                # only assigned when the focal was FREE in the solve
                # (georegister.py:393). When EXIF supplies a focal length the
                # solve does not estimate it, the field stays NaN, and writing
                # it here aborted the whole CLI with
                # ``CanonicalizationError: non-finite float nan`` -- so the
                # ingest worked only on corpora whose cameras report no focal
                # length, and failed on every corpus that does. Emit the solved
                # sigma only when it exists, and say which case this is.
                "focal_sigma_px": (
                    float(solution.focal_sigma_px)
                    if np.isfinite(solution.focal_sigma_px) else None
                ),
                "focal_length_solved": bool(np.isfinite(solution.focal_sigma_px)),
                "horizontal_fov_deg": horizontal_fov_deg(solution.focal_px, capture.width),
                "principal_point_px": [solution.cx, solution.cy],
                "distortion_model": "none",
                "center_covariance_m2": (
                    [[float(v) for v in row] for row in cov] if cov is not None else None
                ),
            },
        },
        geometry=S.geometry_point(enu_frame.frame_id, [float(v) for v in center]),
        quality={
            "stated_horizontal_accuracy_m": capture.horizontal_accuracy_m,
            "solved_center_sigma_m": (
                [float(np.sqrt(max(cov[i, i], 0.0))) for i in range(3)]
                if cov is not None else None
            ),
        },
        digest=capture.digest,
    )


def camera_feature(capture: Capture, solution: GeoSolution, frame_index: int,
                   enu_frame: EnuFrame, observation_id: str) -> dict:
    """A solved camera station.

    Emitted as ``camera_station``, a class FL-4812 added to the registry. The
    first version borrowed ``spawn`` to avoid widening a closed set, which was
    the wrong trade: ``spawn`` carries gameplay meaning, so anything querying
    spawn points would have received every camera pose. A camera station is
    world truth about where an observation was made, and it claims no gameplay
    authority.
    """
    center = solution.centers[frame_index]
    cov = solution.center_covariance.get(frame_index)
    uncertainty = (
        S.uncertainty_covariance([[float(v) for v in row] for row in cov], "m")
        if cov is not None
        else S.uncertainty_isotropic(capture.horizontal_accuracy_m, "m")
    )
    intrinsics_provenance = (
        S.PROVENANCE_MEASURED
        if capture.intrinsics.state == INTRINSICS_MEASURED
        else S.PROVENANCE_DERIVED
    )
    return S.canonical_feature(
        feature_id=f"feature:camera_station/{capture.capture_id}",
        semantic_class="camera_station",
        geometry=S.geometry_point(enu_frame.frame_id, [float(v) for v in center]),
        properties={
            "geometry": S.property_claim(
                value=None,
                provenance=S.PROVENANCE_DERIVED,
                method=(
                    "structure-from-motion pose followed by weighted absolute "
                    "registration against capture GPS, compass, OSM anchors and "
                    "reviewed controls as available"
                ),
                observation_ids=[observation_id],
                uncertainty=uncertainty,
            ),
            "orientation_azimuth_deg": S.property_claim(
                value=solution.azimuth_deg(frame_index),
                provenance=S.PROVENANCE_DERIVED,
                method="solved optical axis bearing, compass azimuth used as a weighted prior",
                observation_ids=[observation_id],
                uncertainty=S.uncertainty_isotropic(
                    float(np.sqrt(max(solution.rotation_covariance[frame_index][2, 2], 0.0)))
                    if frame_index in solution.rotation_covariance
                    else 15.0,
                    "rad",
                ),
            ),
            "focal_length_px": S.property_claim(
                value=solution.focal_px,
                provenance=intrinsics_provenance,
                method=capture.intrinsics.basis,
                observation_ids=[observation_id],
                uncertainty=S.uncertainty_isotropic(
                    solution.focal_sigma_px
                    if np.isfinite(solution.focal_sigma_px)
                    else capture.intrinsics.sigma_px,
                    "px",
                ),
            ),
            "evidence_role": S.property_claim(
                value=EVIDENCE_ROLE,
                provenance=S.PROVENANCE_DERIVED,
                method=EVIDENCE_ROLE_NOTE,
                observation_ids=[observation_id],
            ),
        },
        confidence=0.9,
        gameplay_authority=S.AUTHORITY_NONE,
        presentation=S.presentation_requirements(
            visible=False,
            must_not_imply_collision=True,
            legibility_class="background",
            notes="solved camera station; evidence, not scenery",
        ),
        labels={"licence": capture.licence, "attribution": capture.attribution},
    )


def anchor_observation(record: dict, enu_frame: EnuFrame) -> dict:
    """The OSM wall a facade anchor was tied to, as its own evidence record."""
    return S.source_observation(
        observation_id=f"obs:vector:{record['osm_ref']}#facade_anchor",
        source_id="osm_vector_extract",
        source_kind="vector_map",
        observed_at="2026-09-11T00:00:00Z",
        attributes={
            "evidence_role": EVIDENCE_ROLE,
            "evidence_role_note": EVIDENCE_ROLE_NOTE,
            "source_ref": record["osm_ref"],
            "tags": record["osm_tags"],
            "role": "absolute georegistration anchor",
            "selection_evidence": record["selection_evidence"],
            "horizontal_distance_to_osm_wall_m": record["horizontal_distance_to_osm_wall_m"],
            "overpass_request_shape": (
                "bbox node/way/relation body query, the shape used by "
                "scripts/sbu_e2e_run.py:_fetch_overpass_osm_file"
            ),
        },
        geometry=S.geometry_point(
            enu_frame.frame_id, [float(v) for v in record["position_enu_m"]]
        ),
        quality={"digitising_sigma_m": 1.5},
    )


def candidate_feature(record: dict, enu_frame: EnuFrame,
                      observation_ids: list[str]) -> dict:
    """One gated candidate as world truth - promoted, or preserved as unresolved."""
    promoted = record["gate"]["decision"] == "promote"
    covariance = record.get("covariance_absolute")
    if covariance and covariance.get("matrix"):
        uncertainty = S.uncertainty_covariance(covariance["matrix"], "m")
    else:
        uncertainty = S.uncertainty_isotropic(1e3, "m")

    failed = record["gate"]["failed"]
    conflict = (
        S.conflict_none()
        if promoted
        else S.conflict_unresolved([
            S.contradiction(
                property_name="geometry",
                observation_ids=observation_ids,
                values=[
                    {"claim": "triangulated position", "value": record["position_enu_m"]},
                    {"claim": "promotion gates refused", "failed_criteria": failed},
                ],
            )
        ])
    )
    residual = record["reprojection"]["matched_residual_px"]
    return S.canonical_feature(
        feature_id=f"feature:{record['semantic_class']}:{record['candidate_id']}",
        semantic_class=record["semantic_class"],
        geometry=S.geometry_point(
            enu_frame.frame_id, [float(v) for v in record["position_enu_m"]]
        ),
        properties={
            "geometry": S.property_claim(
                value=None,
                provenance=S.PROVENANCE_MEASURED,
                method=(
                    f"multi-view triangulation from {record['viewpoints']} georegistered "
                    f"viewpoints, widest subtended angle "
                    f"{record['triangulation_angle_deg']:.2f} deg"
                ),
                observation_ids=observation_ids,
                uncertainty=uncertainty,
                confidence=0.8 if promoted else 0.3,
            ),
            "semantic_class": S.property_claim(
                value=record["semantic_class"],
                provenance=S.PROVENANCE_DERIVED,
                method="per-viewpoint evidence vote; every viewpoint must agree on the class",
                observation_ids=observation_ids,
                uncertainty=S.uncertainty_categorical({record["semantic_class"]: 1.0}),
            ),
            "promotion_gate": S.property_claim(
                value=record["gate"],
                provenance=S.PROVENANCE_DERIVED,
                method="scripts.pipeline.camera_solve.gates.evaluate",
                observation_ids=observation_ids,
            ),
            "reprojection_residual_px": S.property_claim(
                value={
                    "median": residual["median"],
                    "max": residual["max"],
                    "matched_frames": residual["count"],
                    "frames_reprojected": record["reprojection"]["frames_reprojected"],
                    "status_counts": record["reprojection"]["status_counts"],
                },
                provenance=S.PROVENANCE_DERIVED,
                method="reprojected into EVERY solved source frame, not only the matched ones",
                observation_ids=observation_ids,
            ),
            "evidence_role": S.property_claim(
                value=EVIDENCE_ROLE,
                provenance=S.PROVENANCE_DERIVED,
                method=EVIDENCE_ROLE_NOTE,
                observation_ids=observation_ids,
            ),
        },
        confidence=0.8 if promoted else 0.3,
        # Reconstructed geometry never becomes collision or walkability truth
        # here.  A refused candidate drops further still, to no authority at all.
        gameplay_authority=RECONSTRUCTED_AUTHORITY if promoted else REFUSED_AUTHORITY,
        conflict=conflict,
        presentation=S.presentation_requirements(
            visible=promoted,
            silhouette_required=promoted and record["semantic_class"] == "fixture",
            must_not_imply_collision=True,
            legibility_class="secondary",
            notes=(
                "triangulated from street imagery; presentation only"
                if promoted
                else f"promotion refused: {', '.join(failed)}"
            ),
        ),
        authority_supporting_properties=["geometry"],
    )


def build_document(inputs: CorridorInputs, *, ir_id: str = "fl4812_control_corridor",
                   seed: int = 4812, generated_at: str = "2026-09-11T00:00:00Z",
                   ) -> tuple[WorldIRStore, dict]:
    """Run the solve and lower the whole result into one World IR document."""
    result = run(inputs, seed=seed)
    enu_frame: EnuFrame = result["frame"]
    solution: GeoSolution = result["solution"]
    captures: dict[int, Capture] = result["captures"]

    store = WorldIRStore.new(ir_id, generated_at=generated_at, notes=EVIDENCE_ROLE_NOTE)
    store.add_frame(coordinate_frame_for(enu_frame))

    camera_observation_ids: list[str] = []
    for frame_index in solution.frames:
        capture = captures[frame_index]
        observation = store.add_observation(
            camera_observation(capture, solution, frame_index, enu_frame)
        )
        camera_observation_ids.append(observation["observation_id"])
        store.add_feature(
            camera_feature(capture, solution, frame_index, enu_frame,
                           observation["observation_id"])
        )

    for record in result["candidates"]:
        supporting = list(camera_observation_ids)
        if record["candidate_id"] == "facade_anchor":
            anchor = store.add_observation(anchor_observation(record, enu_frame))
            supporting.append(anchor["observation_id"])
        store.add_feature(candidate_feature(record, enu_frame, supporting))

    receipt = receipts.build(
        corridor={
            "items_path": str(inputs.items_path),
            "frames_dir": str(inputs.frames_dir),
            "osm_path": str(inputs.osm_path),
            "anchors_path": str(inputs.anchors_path) if inputs.anchors_path else None,
            "control_points_path": (
                str(inputs.control_points_path) if inputs.control_points_path else None
            ),
            "frames_solved": len(solution.frames),
            "frame_ranks": [captures[f].rank for f in solution.frames],
            "licence": sorted({captures[f].licence for f in solution.frames}),
            "attribution": sorted({captures[f].attribution for f in solution.frames}),
            "coordinate_frame": coordinate_frame_for(enu_frame),
            "enu_origin_wgs84": {
                "longitude": enu_frame.origin_lon,
                "latitude": enu_frame.origin_lat,
                "ellipsoidal_height_m": enu_frame.origin_alt,
            },
            "frame_digests": {
                captures[f].capture_id: captures[f].digest for f in solution.frames
            },
        },
        captures=captures,
        solution=solution,
        stages=result["stages"],
        per_observation_px=result["per_observation_px"],
        per_camera_px=result["per_camera_px"],
        candidates=result["candidates"],
        parameters=result["parameters"],
        graph=result["graph"],
    )
    receipt["world_ir"] = {
        "ir_id": ir_id,
        "coordinate_frames": 1,
        "source_observations": len(store.observations),
        "canonical_features": len(store.features),
        "generated_representations": len(store.representations),
        "gameplay_authorities": sorted({f["gameplay_authority"] for f in store.features}),
    }
    return store, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--items", type=Path, required=True,
                    help="STAC items JSON (list, or a FeatureCollection) for the corridor")
    ap.add_argument("--frames", type=Path, required=True,
                    help="directory of downloaded frames named <anything>_<capture id>.jpg")
    ap.add_argument("--osm", type=Path, required=True,
                    help="Overpass bbox .osm for the same area")
    ap.add_argument("--anchors", type=Path,
                    help="versioned OSM anchor set emitted by the cartographic ingest")
    ap.add_argument("--control-points", type=Path,
                    help="reviewed image-pixel to OSM-anchor correspondences")
    ap.add_argument("--out", type=Path, required=True, help="World IR document to write")
    ap.add_argument("--receipt", type=Path, help="write the residual/covariance receipt here")
    ap.add_argument(
        "--sparse-reconstruction", type=Path,
        help="freeze accepted SfM points/tracks separately and retain its digest in the receipt",
    )
    ap.add_argument("--ir-id", default="fl4812_control_corridor")
    ap.add_argument("--seed", type=int, default=4812)
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    args = ap.parse_args(argv)

    store, receipt = build_document(
        CorridorInputs(
            args.items, args.frames, args.osm,
            anchors_path=args.anchors,
            control_points_path=args.control_points,
        ),
        ir_id=args.ir_id,
        seed=args.seed,
    )

    findings = store.validate()
    receipt["validation_findings"] = len(findings)
    if args.validate and findings:
        for finding in findings[:20]:
            print(f"  FAIL {finding}", file=sys.stderr)
        print(f"[world-ir-ingest-cameras] {len(findings)} finding(s); nothing written",
              file=sys.stderr)
        return 1

    store.save(args.out)
    if args.sparse_reconstruction:
        sparse = receipt.pop("sparse_reconstruction")
        args.sparse_reconstruction.parent.mkdir(parents=True, exist_ok=True)
        args.sparse_reconstruction.write_text(
            json.dumps(sparse, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        receipt["sparse_reconstruction"] = {
            "schema": sparse["schema"],
            "path": str(args.sparse_reconstruction),
            "sha256": receipts.file_digest(args.sparse_reconstruction),
            "counts": sparse["counts"],
            "evidence_state": sparse["evidence_state"],
        }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(receipt, indent=2, sort_keys=True, default=float) + "\n",
            encoding="utf-8",
        )

    ir = receipt["world_ir"]
    print(f"[world-ir-ingest-cameras] EVIDENCE ROLE: {EVIDENCE_ROLE_NOTE}")
    print(f"[world-ir-ingest-cameras] frames={receipt['corridor']['frames_solved']} "
          f"observations={ir['source_observations']} features={ir['canonical_features']} "
          f"findings={len(findings)}")
    sigma = receipt["intrinsics"]["focal_sigma_px"]
    sigma_text = f"+/- {sigma:.1f}" if sigma is not None else "(not solved; from source)"
    print(f"[world-ir-ingest-cameras] focal={receipt['intrinsics']['focal_px_solved']:.1f} px "
          f"{sigma_text} ({receipt['intrinsics']['focal_state']})")
    residual = receipt["residuals"]["all_visual_observations_px"]
    print(f"[world-ir-ingest-cameras] reprojection px: median={residual['median']:.3f} "
          f"p90={residual['p90']:.3f} max={residual['max']:.3f} n={residual['count']}")
    for candidate in receipt["candidates"]:
        gate = candidate["gate"]
        print(f"[world-ir-ingest-cameras] {candidate['candidate_id']}: "
              f"{gate['decision'].upper()} views={candidate['viewpoints']} "
              f"angle={candidate['triangulation_angle_deg']:.2f}deg "
              f"median_px={candidate['reprojection']['matched_residual_px']['median']:.3f} "
              f"failed={gate['failed'] or 'none'}")
    print(f"[world-ir-ingest-cameras] authorities: {ir['gameplay_authorities']}")
    print(f"[world-ir-ingest-cameras] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

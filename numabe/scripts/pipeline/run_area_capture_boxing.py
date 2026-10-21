#!/usr/bin/env python3
"""Survey an AOI street network, retain licensed frames, and open box review."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.capture_review_server import CaptureReviewStore, build_server
from scripts.pipeline.route_capture_manifest import compile_manifest
from scripts.pipeline.street_imagery_probe import STATE_PRESENT, load_probe_spec, run_survey
from scripts.pipeline.street_network_capture_index import acquire_area


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(
    *, probe_spec_path: Path, aoi_path: Path, osm_path: Path, route_path: Path,
    product_scope_path: Path, workspace: Path, max_street_lateral_m: float,
    min_frame_spacing_m: float, max_frames: int, workers: int,
    reuse_survey: bool = False,
) -> dict:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    survey_path = workspace / "imagery_survey.json"
    capture_index_path = workspace / "capture_source_index.json"
    route_manifest_path = workspace / "route_capture_manifest.json"
    annotations_path = workspace / "annotations.json"
    if not reuse_survey or not survey_path.is_file():
        survey = run_survey(load_probe_spec(probe_spec_path), ["mapillary"])
        _write_json(survey_path, survey)
    else:
        survey = json.loads(survey_path.read_text(encoding="utf-8"))
    mapillary = survey.get("providers", {}).get("mapillary") or {}
    if mapillary.get("state") != STATE_PRESENT:
        refusal = (mapillary.get("access") or {}).get("refusal") or "coverage was not returned"
        raise RuntimeError(f"Mapillary AOI survey did not return coverage: {refusal}")
    capture_index = acquire_area(
        survey_path=survey_path,
        aoi_geojson_path=aoi_path.resolve(),
        osm_path=osm_path.resolve(),
        route_path=route_path.resolve(),
        output_dir=workspace / "captures",
        output_index=capture_index_path,
        max_street_lateral_m=max_street_lateral_m,
        min_frame_spacing_m=min_frame_spacing_m,
        max_frames=max_frames,
        workers=workers,
    )
    route_manifest = compile_manifest(
        route_path.resolve(), [capture_index_path], route_manifest_path,
        max_lateral_m=25.0,
    )
    return {
        "survey_path": survey_path,
        "capture_index_path": capture_index_path,
        "route_manifest_path": route_manifest_path,
        "annotations_path": annotations_path,
        "product_scope_path": product_scope_path.resolve(),
        "capture_index": capture_index,
        "route_manifest": route_manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-spec", type=Path, required=True)
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--product-scope", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--max-street-lateral-m", type=float, default=22.0)
    parser.add_argument("--min-frame-spacing-m", type=float, default=8.0)
    parser.add_argument("--max-frames", type=int, default=400)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--reuse-survey", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(argv)
    try:
        prepared = prepare(
            probe_spec_path=args.probe_spec, aoi_path=args.aoi,
            osm_path=args.osm, route_path=args.route,
            product_scope_path=args.product_scope, workspace=args.workspace,
            max_street_lateral_m=args.max_street_lateral_m,
            min_frame_spacing_m=args.min_frame_spacing_m,
            max_frames=args.max_frames, workers=args.workers,
            reuse_survey=args.reuse_survey,
        )
    except RuntimeError as error:
        print(f"AREA CAPTURE BOXING BLOCKED: {error}", file=sys.stderr)
        return 2
    counts = prepared["capture_index"]["counts"]
    area = prepared["capture_index"]["area_coverage"]
    route_coverage = prepared["route_manifest"]["coverage"]
    print(
        "AREA CAPTURE BOXING PREPARED: "
        f"frames={counts['eligible_area_frames']} ways={counts['street_ways']} "
        f"network_fraction={area['camera_proximity_coverage_fraction']:.3f} "
        f"route_fraction={route_coverage['aggregate_coverage_fraction']:.3f}"
    )
    if args.prepare_only:
        return 0
    store = CaptureReviewStore(
        prepared["capture_index_path"], prepared["annotations_path"],
        route_manifest_path=prepared["route_manifest_path"],
        product_scope_path=prepared["product_scope_path"],
    )
    server = build_server(store, args.bind, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"AREA CAPTURE BOXING READY: {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Survey, retain, register, and open one route segment for operator boxing.

This is the acquisition front door before semantic grouping/review. It keeps a
segment corpus separate from earlier annotations while measuring every retained
camera against the complete product route. Mapillary credentials are read only
from the environment named by the probe spec and are never written to receipts.
"""

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
from scripts.pipeline.mapillary_capture_source_index import acquire
from scripts.pipeline.route_capture_manifest import compile_manifest
from scripts.pipeline.street_imagery_probe import STATE_PRESENT, load_probe_spec, run_survey


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(
    *,
    probe_spec_path: Path,
    route_path: Path,
    product_scope_path: Path,
    workspace: Path,
    min_chainage_m: float | None,
    max_chainage_m: float | None,
    max_lateral_m: float,
    workers: int,
) -> dict:
    probe_spec_path = probe_spec_path.resolve()
    route_path = route_path.resolve()
    product_scope_path = product_scope_path.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    survey_path = workspace / "imagery_survey.json"
    capture_index_path = workspace / "capture_source_index.json"
    capture_dir = workspace / "captures"
    route_manifest_path = workspace / "route_capture_manifest.json"
    annotations_path = workspace / "annotations.json"

    spec = load_probe_spec(probe_spec_path)
    survey = run_survey(spec, ["mapillary"])
    _write_json(survey_path, survey)
    mapillary = survey["providers"]["mapillary"]
    if mapillary["state"] != STATE_PRESENT:
        access = mapillary.get("access") or {}
        refusal = access.get("refusal") or "coverage was not returned"
        token_env = access.get("access_token_env") or "MAPILLARY_ACCESS_TOKEN"
        raise RuntimeError(
            f"Mapillary acquisition blocked: {refusal}. Set {token_env} in the "
            "launch environment and rerun; the token will not be persisted."
        )

    capture_index = acquire(
        survey_path=survey_path,
        route_path=route_path,
        output_dir=capture_dir,
        output_index=capture_index_path,
        max_lateral_m=max_lateral_m,
        min_chainage_m=min_chainage_m,
        max_chainage_m=max_chainage_m,
        workers=workers,
    )
    route_manifest = compile_manifest(
        route_path,
        [capture_index_path],
        route_manifest_path,
        max_lateral_m=max_lateral_m,
    )
    return {
        "survey_path": survey_path,
        "capture_index_path": capture_index_path,
        "route_manifest_path": route_manifest_path,
        "annotations_path": annotations_path,
        "product_scope_path": product_scope_path,
        "capture_index": capture_index,
        "route_manifest": route_manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probe-spec", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--product-scope", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--min-chainage-m", type=float)
    parser.add_argument("--max-chainage-m", type=float)
    parser.add_argument("--max-lateral-m", type=float, default=25.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        prepared = prepare(
            probe_spec_path=args.probe_spec,
            route_path=args.route,
            product_scope_path=args.product_scope,
            workspace=args.workspace,
            min_chainage_m=args.min_chainage_m,
            max_chainage_m=args.max_chainage_m,
            max_lateral_m=args.max_lateral_m,
            workers=args.workers,
        )
    except RuntimeError as error:
        print(f"ROUTE CAPTURE BOXING BLOCKED: {error}", file=sys.stderr)
        return 2
    counts = prepared["capture_index"]["counts"]
    coverage = prepared["route_manifest"]["coverage"]
    print(
        "ROUTE CAPTURE BOXING PREPARED: "
        f"frames={counts['eligible_route_frames']} "
        f"sequences={counts['sequences']} "
        f"full_route_coverage={coverage['full_route_reconstruction_ready']}"
    )
    print(f"  survey:      {prepared['survey_path']}")
    print(f"  capture index: {prepared['capture_index_path']}")
    print(f"  route manifest: {prepared['route_manifest_path']}")
    print(f"  annotations: {prepared['annotations_path']}")
    if args.prepare_only:
        return 0

    store = CaptureReviewStore(
        prepared["capture_index_path"],
        prepared["annotations_path"],
        route_manifest_path=prepared["route_manifest_path"],
        product_scope_path=prepared["product_scope_path"],
    )
    server = build_server(store, args.bind, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"ROUTE CAPTURE BOXING READY: {url}", flush=True)
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

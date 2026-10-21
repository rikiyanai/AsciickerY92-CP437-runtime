#!/usr/bin/env python3
"""Prepare and optionally serve the complete capture-review subprocess."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.capture_review_server import CaptureReviewStore, build_server
from scripts.pipeline.compile_capture_review_routing import (
    canonical_bytes,
    empty_decisions,
    validate_decisions,
    write_routing,
)
from scripts.pipeline.group_capture_review_crops import (
    build_apple_vision_neighbors,
    build_document as build_groups,
    resolve_crop_paths,
    portable_path,
    sha256,
)


def prepare(
    annotations_path: Path,
    workspace: Path,
    *,
    threshold: float,
    semantic_threshold: float,
    grouping_backend: str,
    label_triage_path: Path | None = None,
    existing_semantic_neighbors_path: Path | None = None,
    reset_decisions: bool = False,
) -> dict:
    annotations_path = annotations_path.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    groups_path = workspace / "similarity_groups.json"
    decisions_path = workspace / "semantic_review.json"
    routing_path = workspace / "authoring_routing.json"
    semantic_neighbors_path = (
        existing_semantic_neighbors_path.resolve()
        if existing_semantic_neighbors_path is not None
        else workspace / "semantic_neighbors.json"
    )
    use_vision = grouping_backend == "apple_vision" or (
        grouping_backend == "auto" and platform.system() == "Darwin"
    )
    if use_vision and existing_semantic_neighbors_path is None:
        build_apple_vision_neighbors(
            resolve_crop_paths(annotations_path), semantic_neighbors_path
        )
    groups = build_groups(
        annotations_path,
        threshold=threshold,
        semantic_neighbors_path=semantic_neighbors_path if use_vision else None,
        semantic_threshold=semantic_threshold,
        label_triage_path=label_triage_path,
    )
    new_group_bytes = canonical_bytes(groups)
    changed = not groups_path.is_file() or groups_path.read_bytes() != new_group_bytes
    if changed and decisions_path.is_file() and not reset_decisions:
        old = json.loads(decisions_path.read_text(encoding="utf-8"))
        if old.get("group_reviews") or old.get("annotation_reviews"):
            raise ValueError(
                "annotations/grouping changed after semantic review; rerun with "
                "--reset-decisions only after preserving or reconciling the old review"
            )
    groups_path.write_bytes(new_group_bytes)
    if reset_decisions or not decisions_path.is_file() or changed:
        decisions_path.write_bytes(canonical_bytes(empty_decisions(groups_path)))
    else:
        validate_decisions(
            json.loads(decisions_path.read_text(encoding="utf-8")), groups, groups_path
        )
    routing = write_routing(annotations_path, groups_path, decisions_path, routing_path)
    receipt_path = workspace / "pipeline_receipt.json"
    receipt = {
        "schema": "y92.capture_review_pipeline_receipt.v1",
        "stage": "pre_agent_review_prepared",
        "input": {"path": portable_path(annotations_path), "sha256": sha256(annotations_path)},
        "outputs": {
            "semantic_neighbors": (
                {"path": portable_path(semantic_neighbors_path), "sha256": sha256(semantic_neighbors_path)}
                if use_vision else None
            ),
            "similarity_groups": {"path": portable_path(groups_path), "sha256": sha256(groups_path)},
            "semantic_review": {"path": portable_path(decisions_path), "sha256": sha256(decisions_path)},
            "authoring_routing": {"path": portable_path(routing_path), "sha256": sha256(routing_path)},
        },
        "counts": {**groups["counts"], **routing["counts"]},
        "execution": {
            "grouping_backend": groups["algorithm"]["id"],
            "network_used": False,
            "cloud_compute_used": False,
            "paid_service_used": False,
        },
        "promotion": routing["promotion"],
    }
    receipt_path.write_bytes(canonical_bytes(receipt))
    return {
        "groups_path": groups_path,
        "decisions_path": decisions_path,
        "routing_path": routing_path,
        "receipt_path": receipt_path,
        "semantic_neighbors_path": semantic_neighbors_path if use_vision else None,
        "groups": groups,
        "routing": routing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture-index", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--route-manifest", type=Path)
    parser.add_argument(
        "--product-scope",
        type=Path,
        help="Expanded connected-product scope_spec.json shown separately from capture coverage",
    )
    parser.add_argument("--threshold", type=float, default=0.16)
    parser.add_argument("--semantic-threshold", type=float, default=0.62)
    parser.add_argument(
        "--grouping-backend",
        choices=("auto", "apple_vision", "classical"),
        default="auto",
        help="auto uses Apple's local Vision feature print on macOS; no cloud is used",
    )
    parser.add_argument("--reset-decisions", action="store_true")
    parser.add_argument(
        "--label-triage", type=Path,
        help="Review-only agent label/population proposals; never auto-accepted",
    )
    parser.add_argument(
        "--semantic-neighbors", type=Path,
        help="Reuse a validated local Apple Vision neighbor artifact",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(argv)
    prepared = prepare(
        args.annotations,
        args.workspace,
        threshold=args.threshold,
        semantic_threshold=args.semantic_threshold,
        grouping_backend=args.grouping_backend,
        label_triage_path=args.label_triage,
        existing_semantic_neighbors_path=args.semantic_neighbors,
        reset_decisions=args.reset_decisions,
    )
    print(
        "CAPTURE REVIEW PREPARED: "
        f"annotations={prepared['groups']['counts']['annotations']} "
        f"candidate_groups={prepared['groups']['counts']['groups']} "
        f"unresolved={prepared['routing']['counts']['unresolved_review']}"
    )
    print(f"  groups:    {prepared['groups_path']}")
    if prepared["semantic_neighbors_path"] is not None:
        print(f"  neighbors: {prepared['semantic_neighbors_path']}")
    print(f"  decisions: {prepared['decisions_path']}")
    print(f"  routing:   {prepared['routing_path']}")
    print(f"  receipt:   {prepared['receipt_path']}")
    print(f"  e2e input: --capture-review-routing {prepared['routing_path']}")
    if args.prepare_only:
        return 0
    store = CaptureReviewStore(
        args.capture_index,
        args.annotations,
        route_manifest_path=args.route_manifest,
        product_scope_path=args.product_scope,
        groups_path=prepared["groups_path"],
        decisions_path=prepared["decisions_path"],
        routing_path=prepared["routing_path"],
    )
    server = build_server(store, args.bind, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/review"
    print(f"GROUP REVIEW READY: {url}", flush=True)
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

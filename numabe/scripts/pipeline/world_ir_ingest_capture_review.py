#!/usr/bin/env python3
"""Ingest reviewed capture routing as non-promoted World IR observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_capture_review_routing import SCHEMA as ROUTING_SCHEMA
from scripts.pipeline.compile_capture_review_routing import portable_path
from scripts.pipeline.world_ir import WorldIRStore, schema as S


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_document(routing_path: Path, *, ir_id: str = "capture_review_observations") -> WorldIRStore:
    routing_path = routing_path.resolve()
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    if routing.get("schema") != ROUTING_SCHEMA:
        raise ValueError(f"routing is not {ROUTING_SCHEMA}")
    store = WorldIRStore.new(
        ir_id,
        notes=(
            "Operator/agent reviewed image-space observations. No world position, "
            "physical identity, collision authority, or runtime representation is asserted."
        ),
    )
    routing_digest = sha256(routing_path)
    for row in routing["all_annotations"]:
        camera = row.get("camera") or {}
        source_digest = (row.get("crop") or {}).get("sha256")
        if not isinstance(source_digest, str):
            raise ValueError(f"annotation {row.get('annotation_id')} lacks crop digest")
        store.add_observation(S.source_observation(
            observation_id="capture-review:" + row["annotation_id"],
            source_id=row["capture_id"],
            source_kind="imagery",
            observed_at=str(camera.get("timestamp") or "unknown"),
            digest="sha256:" + source_digest,
            geometry=S.geometry_none(),
            attributes={
                "capture_review": row,
                "routing_artifact_sha256": routing_digest,
                "world_localization_state": "unresolved",
            },
            quality={
                "semantic_reviewed": row["review_state"] == "reviewed",
                "physical_identity_reviewed": False,
            },
        ))
    findings = store.validate()
    if findings:
        raise ValueError(f"capture review World IR validation failed: {findings[:3]}")
    return store


def ingest(routing_path: Path, out_path: Path) -> dict:
    store = build_document(routing_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    store.save(out_path)
    reloaded = WorldIRStore.load(out_path)
    if reloaded.validate() or reloaded.features or reloaded.representations:
        raise ValueError("capture review ingest promoted image evidence")
    return {
        "schema": "y92.capture_review_world_ir_ingest_receipt.v1",
        "routing": {"path": portable_path(routing_path), "sha256": sha256(routing_path)},
        "world_ir": {"path": portable_path(out_path), "sha256": sha256(out_path)},
        "source_observations": len(reloaded.observations),
        "canonical_features": 0,
        "generated_representations": 0,
        "world_ir_promotion_ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path)
    args = parser.parse_args()
    receipt = ingest(args.routing, args.out)
    if args.receipt_out is not None:
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

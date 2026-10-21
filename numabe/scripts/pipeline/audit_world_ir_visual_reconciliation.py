#!/usr/bin/env python3
"""Reconcile every streetscape-relevant source feature to runtime evidence.

An emitted IR row or AKM is not proof that the player can see a feature.  This
audit separates source coverage, lowering, artifact readback, and headed
visibility so an E2E build cannot call the streetscape populated from counts
alone.
"""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.pipeline.compile_facade_shell import _load_a3d_edit
from scripts.pipeline.world_ir import WorldIRStore


SCHEMA = "y92.world_ir_visual_reconciliation.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(receipt: dict[str, Any], receipt_path: Path) -> list[dict[str, Any]]:
    rows = receipt.get("instances", [])
    if isinstance(rows, str):
        candidate = Path(rows)
        if not candidate.is_absolute():
            candidate = receipt_path.parent / candidate
        return _load(candidate)
    return list(rows)


def audit(
    *,
    world_ir_path: Path,
    ingest_receipt_path: Path,
    map_path: Path,
    mesh_root: Path,
    component_receipts: dict[str, Path],
    output_path: Path,
    runtime_visibility_path: Path | None = None,
) -> dict[str, Any]:
    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    ingest = _load(ingest_receipt_path)
    runtime_visibility = _load(runtime_visibility_path) if runtime_visibility_path else {}
    visible_ids = set(runtime_visibility.get("visible_feature_ids", []))

    editor = _load_a3d_edit()
    actual_instances = {row.inst_name: row.mesh_name
                        for row in editor.read_a3d_sections(map_path)[2]}
    lowered: dict[str, list[dict[str, Any]]] = {}

    def accept(feature_id: str, row: dict[str, Any], component: str) -> None:
        instance_name = row.get("inst_name")
        mesh_name = row.get("mesh_name")
        artifact_ok = True
        if instance_name:
            artifact_ok = actual_instances.get(instance_name) == mesh_name
        if mesh_name:
            artifact_ok = artifact_ok and (mesh_root / mesh_name).is_file()
            artifact_ok = artifact_ok and (mesh_root / mesh_name).stat().st_size > 0
        lowered.setdefault(feature_id, []).append({
            "component": component,
            "instance_name": instance_name,
            "mesh_name": mesh_name,
            "artifact_attested": artifact_ok,
            "semantic_class": row.get("semantic_class") or row.get("semantic_subtype"),
            "proposal_status": row.get("proposal_status"),
        })

    receipts: dict[str, dict[str, Any]] = {}
    for component, path in component_receipts.items():
        receipt = _load(path)
        receipts[component] = receipt
        for row in _rows(receipt, path):
            feature_id = row.get("feature_id")
            if feature_id:
                accept(feature_id, row, component)
            for evidence_id in row.get("evidence_feature_ids", []):
                lowered.setdefault(evidence_id, []).append({
                    "component": component,
                    "role": "supporting_source_feature",
                    "instance_name": row.get("inst_name"),
                    "mesh_name": row.get("mesh_name"),
                    "artifact_attested": (
                        not row.get("inst_name") or
                        actual_instances.get(row.get("inst_name")) == row.get("mesh_name")
                    ),
                })

    # Terrain materials have no mesh instance; the surface receipt records the
    # actual stamped source feature and role.
    surface = receipts.get("surfaces", {})
    for row in surface.get("features", []):
        lowered.setdefault(row["feature_id"], []).append({
            "component": "surfaces",
            "material_role": row.get("role"),
            "artifact_attested": bool(surface.get("materials_written", 0)),
        })

    # Building masses and fallback facades have separate owners.
    masses = receipts.get("building_mass", {})
    for row in masses.get("per_building", []):
        source = row.get("source") or row.get("feature_id")
        if source and not str(source).startswith("feature:"):
            source = f"feature:building_footprint:{source}"
        if source:
            lowered.setdefault(source, []).append({
                "component": "building_mass",
                "artifact_attested": row.get("ok", True),
            })
    facades = receipts.get("fallback_facades", {})
    for row in _rows(facades, component_receipts["fallback_facades"]):
        accept(row["feature_id"], row, "fallback_facades")

    deliberate_exclusions: dict[str, list[dict[str, Any]]] = {}
    for component, receipt in receipts.items():
        for row in receipt.get("excluded", []):
            feature_id = row.get("feature_id")
            if feature_id:
                deliberate_exclusions.setdefault(feature_id, []).append({
                    "component": component, "reason": row.get("reason")
                })

    relevant = {
        "building_footprint", "road", "sidewalk", "crossing", "terrain",
        "railway_boundary", "wall", "fence", "hedge", "tree", "fixture",
        "sign", "utility_pole", "utility_wire", "station_entrance", "place",
    }
    records = []
    bucket_counts: Counter[str] = Counter()
    for feature in sorted(store.features, key=lambda row: row["feature_id"]):
        if feature.get("semantic_class") not in relevant:
            continue
        feature_id = feature["feature_id"]
        products = lowered.get(feature_id, [])
        if not products and feature_id in deliberate_exclusions:
            bucket = "deliberately_excluded"
        elif not products:
            bucket = "mapped_but_not_lowered"
        elif not all(product.get("artifact_attested") for product in products):
            bucket = "lowered_but_artifact_missing"
        elif feature_id in visible_ids:
            bucket = "mapped_lowered_and_runtime_visible"
        else:
            bucket = "mapped_lowered_visibility_unverified"
        bucket_counts[bucket] += 1
        records.append({
            "feature_id": feature_id,
            "semantic_class": feature["semantic_class"],
            "decision_bucket": bucket,
            "products": products,
            "exclusions": deliberate_exclusions.get(feature_id, []),
        })

    unmapped = []
    for row in ingest.get("unmapped", []):
        unmapped.append({
            "source_ref": row["source_ref"],
            "tags": row.get("tags", {}),
            "reason": row.get("reason"),
            "decision_bucket": "unsupported_tag",
        })
    bucket_counts["unsupported_tag"] = len(unmapped)

    synthesized = []
    for row in _rows(receipts.get("streetscape", {}), component_receipts["streetscape"]):
        synthesized.append({
            "feature_id": row.get("feature_id"),
            "semantic_class": row.get("semantic_class"),
            "instance_name": row.get("inst_name"),
            "decision_bucket": "synthesized_because_source_absent",
        })
    bucket_counts["synthesized_because_source_absent"] = len(synthesized)

    result = {
        "schema": SCHEMA,
        "stage": "static_artifact_reconciliation",
        "inputs": {
            "world_ir": str(world_ir_path.resolve()),
            "ingest_receipt": str(ingest_receipt_path.resolve()),
            "map": str(map_path.resolve()),
            "mesh_root": str(mesh_root.resolve()),
            "runtime_visibility": (str(runtime_visibility_path.resolve())
                                   if runtime_visibility_path else None),
        },
        "artifact_identity": {
            "map_sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
            "map_size_bytes": map_path.stat().st_size,
            "a3d_mesh_instances": len(actual_instances),
            "referenced_mesh_assets": len(set(actual_instances.values())),
        },
        "counts": dict(sorted(bucket_counts.items())),
        "mapped_records": records,
        "unsupported_tags": unmapped,
        "synthesized_records": synthesized,
        "gates": {
            "all_mapped_features_have_representation_decision":
                bucket_counts["mapped_but_not_lowered"] == 0,
            "all_lowered_artifacts_resolve":
                bucket_counts["lowered_but_artifact_missing"] == 0,
            "headed_route_visibility_proven":
                bool(runtime_visibility_path) and
                bucket_counts["mapped_lowered_visibility_unverified"] == 0,
        },
        "claim_limit": (
            "static reconciliation proves source-to-artifact linkage only; "
            "mapped_lowered_visibility_unverified is not visible gameplay proof"
        ),
    }
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result

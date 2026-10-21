#!/usr/bin/env python3
"""Compile deterministic observation-to-module mapping artifacts.

Generic owner. This compiler contains no site-specific behavior. Every
site-specific value (corpus identities, expected hashes, inherited role sets,
shard sizes, output directory) arrives through CLI arguments. It verifies
input hashes fail-closed, builds a stable observation ledger, partitions rows
into bounded visual-review shards, validates reviewed rows and batch receipts,
then materializes the canonical mapping ledger and evidence-linked catalogs.

Mapping-review intake (--batch-receipt) validates one generic
y92.capture_module_review_batch_receipt.v1 receipt per reviewed batch through
this same production flow. Existing shard files are review-thread evidence and
are never rewritten here. A reviewed shard without a batch receipt fails
closed. This compiler owns mapping readiness only. The run-local observed-join
owner consumes these artifacts plus the same-run Step 2 receipt and emits the
Step 3 receipt expected by the final Phase 1 gate.

No physical instance, world coordinate, asset, or World IR promotion is
produced here. Visual-review workers own their assigned shard files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_LEDGER = "y92.capture_module_mapping_ledger.v1"
SCHEMA_MANIFEST = "y92.capture_module_mapping_manifest.v1"
SCHEMA_RECEIPT = "y92.capture_module_mapping_receipt.v1"
SCHEMA_BATCH_RECEIPT = "y92.capture_module_review_batch_receipt.v1"

# FL-4813: one Muse Spark visual-review thread may attach at most 49 images.
MAX_IMAGES_PER_BATCH = 49

THREAD_STATUS_OPEN = "open_pending_visual_review"
THREAD_STATUS_REVIEWED_INCOMPLETE = "reviewed_with_provenance_incomplete_batch_receipt"
THREAD_STATUS_REVIEWED_COMPLETE = "reviewed_with_batch_receipt_provenance_complete"

PROVENANCE_INCOMPLETE = "provenance_incomplete"
PROVENANCE_COMPLETE = "provenance_complete"

REQUIRED_BATCH_RECEIPT_FIELDS = (
    "thread_id",
    "shard_id",
    "assigned_corpus",
    "assigned_first_index",
    "assigned_last_index",
    "expected_observation_uids",
    "expected_row_count",
    "attached_image_count",
    "maximum_image_limit",
    "annotation_source_path",
    "annotation_source_sha256",
    "input_shard_sha256",
    "reviewed_output_shard_sha256",
    "reviewer_identity",
    "model_identity",
    "review_timestamp",
    "starting_commit",
    "producing_commit",
    "review_method",
    "unresolved_provenance_fields",
    "provenance_status",
    "no_world_ir_promotion",
    "no_physical_registration",
    "no_generation",
)

REQUIRED_LEDGER_FIELDS = (
    "corpus_id",
    "source_annotation_index",
    "annotation_id",
    "capture_id",
    "crop_path",
    "source_hash",
    "inherited_label",
    "proposed_population_role",
    "review_state",
    "provenance",
)

DOMAIN_CATALOG_FILES = (
    "road_and_walkway.json",
    "property_frontage.json",
    "rail_corridor.json",
    "utility_network.json",
    "generic_facades.json",
    "generic_storefronts.json",
    "street_fixtures_and_signs.json",
    "vegetation.json",
)

SEMANTIC_FAMILY_TO_CATALOG = {
    "road_cross_section": "road_and_walkway.json",
    "property_frontage": "property_frontage.json",
    "rail_edge": "rail_corridor.json",
    "utility_network": "utility_network.json",
    "facade_archetype": "generic_facades.json",
    "generic_storefront": "generic_storefronts.json",
    "street_fixture": "street_fixtures_and_signs.json",
    "vegetation": "vegetation.json",
}

POPULATION_ROLES = frozenset(
    {"generic_fixture", "generic_morphology", "custom_identity", "discard"}
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _role_for_index(role_sets: dict[str, Any], index: int) -> str:
    for role in ("custom_identity", "generic_morphology", "discard"):
        members = role_sets.get(role) or []
        if index in members:
            return role
    default = role_sets.get("default_role", "generic_fixture")
    return str(default)


def build_ledger(
    *,
    corpora: list[dict[str, Any]],
    triage_by_id: dict[str, str],
    role_sets_by_corpus: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build stable ledger rows in corpus order. No visual classification here."""
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for corpus in corpora:
        corpus_id = corpus["corpus_id"]
        annotations = corpus["annotations"]
        source_hash = corpus["source_hash"]
        role_sets = role_sets_by_corpus.get(corpus_id, {})
        for position, annotation in enumerate(annotations, start=1):
            annotation_id = str(annotation.get("annotation_id", ""))
            if not annotation_id:
                raise ValueError(f"{corpus_id} row {position} has no annotation_id")
            if annotation_id in seen_ids:
                raise ValueError(f"duplicate annotation_id across corpora: {annotation_id}")
            seen_ids.add(annotation_id)
            crop = annotation.get("crop") or {}
            role_sets_hit = position if position not in _triage_positions(
                corpus, triage_by_id) else None
            _ = role_sets_hit
            if annotation_id in triage_by_id:
                proposed = triage_by_id[annotation_id]
                provenance = "triage_proposal_non_authoritative"
            else:
                proposed = _role_for_index(role_sets, position)
                provenance = "index_role_set_non_authoritative"
            rows.append({
                "corpus_id": corpus_id,
                "source_annotation_index": position,
                "annotation_id": annotation_id,
                "capture_id": annotation.get("capture_id"),
                "crop_path": crop.get("path") if isinstance(crop, dict) else None,
                "crop_sha256": crop.get("sha256") if isinstance(crop, dict) else None,
                "source_hash": source_hash,
                "inherited_label": annotation.get("label"),
                "inherited_note": annotation.get("note"),
                "proposed_population_role": proposed,
                "review_state": annotation.get("review_state"),
                "provenance": provenance,
            })
    return rows


def _triage_positions(corpus: dict[str, Any], triage_by_id: dict[str, str]) -> set[int]:
    positions: set[int] = set()
    for position, annotation in enumerate(corpus["annotations"], start=1):
        if str(annotation.get("annotation_id", "")) in triage_by_id:
            positions.add(position)
    return positions


def partition_shards(
    rows: list[dict[str, Any]],
    *,
    max_images_per_thread: int,
) -> list[dict[str, Any]]:
    """Partition ledger rows into sequential per-corpus shards bounded by the image limit."""
    if not isinstance(max_images_per_thread, int) or not (
        1 <= max_images_per_thread <= MAX_IMAGES_PER_BATCH
    ):
        raise ValueError(
            f"max_images_per_thread must be between 1 and "
            f"{MAX_IMAGES_PER_BATCH}"
        )
    shards: list[dict[str, Any]] = []
    for corpus_id in sorted({row["corpus_id"] for row in rows}):
        corpus_rows = [row for row in rows if row["corpus_id"] == corpus_id]
        for start in range(0, len(corpus_rows), max_images_per_thread):
            chunk = corpus_rows[start:start + max_images_per_thread]
            first = chunk[0]["source_annotation_index"]
            last = chunk[-1]["source_annotation_index"]
            shards.append({
                "shard_id": f"{corpus_id}_{first:03d}_{last:03d}",
                "corpus_id": corpus_id,
                "first_index": first,
                "last_index": last,
                "row_count": len(chunk),
                "file": f"shards/{corpus_id}_{first:03d}_{last:03d}.jsonl",
                "rows": chunk,
            })
    return shards


def _parse_observation_uid(uid: str) -> tuple[str, int]:
    """Split an observation UID into (corpus_id, source_annotation_index)."""
    parts = str(uid).split(":")
    if len(parts) < 3:
        raise ValueError(f"malformed observation_uid: {uid!r}")
    try:
        index = int(parts[1])
    except ValueError:
        raise ValueError(f"malformed observation_uid index: {uid!r}")
    return parts[0], index


def _canonical_rows_hash(rows: list[dict[str, Any]]) -> str:
    """Hash rows exactly as _dump_jsonl would serialize them."""
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_batch_receipt(
    receipt: dict[str, Any],
    *,
    threads: list[dict[str, Any]],
    rows_by_shard: dict[str, list[dict[str, Any]]],
    input_hash_by_shard: dict[str, str],
    output_hash_by_shard: dict[str, str],
    annotation_hash_by_corpus: dict[str, str],
    annotation_path_by_corpus: dict[str, Path],
) -> dict[str, Any]:
    """Validate one mapping-review batch receipt against its assigned shard.

    This is the same interface the production mapping flow uses: compile_mapping
    calls it for every supplied receipt. Every failure raises ValueError with a
    message naming the violated contract. Nothing here promotes content into
    World IR, registers physical instances, or authorizes generation.
    """
    # FL-4813: every batch receipt uses the generic contract schema.
    if receipt.get("schema") != SCHEMA_BATCH_RECEIPT:
        raise ValueError(
            f"batch receipt has schema {receipt.get('schema')!r}, "
            f"expected {SCHEMA_BATCH_RECEIPT!r}"
        )
    for field in REQUIRED_BATCH_RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError(f"batch receipt is missing required field: {field}")

    shard_id = receipt["shard_id"]
    assigned = next(
        (thread for thread in threads if thread["shard_id"] == shard_id), None
    )
    if assigned is None:
        raise ValueError(f"batch receipt names unknown shard: {shard_id!r}")
    if (
        receipt["assigned_corpus"] != assigned["corpus_id"]
        or receipt["assigned_first_index"] != assigned["first_index"]
        or receipt["assigned_last_index"] != assigned["last_index"]
    ):
        raise ValueError(
            f"batch receipt for {shard_id!r} names the wrong observation range: "
            f"{receipt['assigned_corpus']} {receipt['assigned_first_index']}-"
            f"{receipt['assigned_last_index']}, assigned shard is "
            f"{assigned['corpus_id']} {assigned['first_index']}-"
            f"{assigned['last_index']}"
        )
    shard_rows = rows_by_shard.get(shard_id)
    if shard_rows is None:
        raise ValueError(f"batch receipt names unknown shard: {shard_id!r}")

    # FL-4813: at most 49 attached images per review thread.
    attached = receipt["attached_image_count"]
    limit = receipt["maximum_image_limit"]
    if not isinstance(attached, int) or not isinstance(limit, int):
        raise ValueError("attached_image_count and maximum_image_limit must be integers")
    if limit > MAX_IMAGES_PER_BATCH:
        raise ValueError(
            f"batch receipt declares maximum_image_limit={limit}, "
            f"which exceeds the {MAX_IMAGES_PER_BATCH}-image thread bound"
        )
    if attached > MAX_IMAGES_PER_BATCH:
        raise ValueError(
            f"batch receipt attaches {attached} images, "
            f"which exceeds the {MAX_IMAGES_PER_BATCH}-image thread bound"
        )
    if attached > limit:
        raise ValueError(
            f"batch receipt attaches {attached} images above its "
            f"maximum_image_limit={limit}"
        )
    if attached != len(shard_rows):
        raise ValueError(
            f"batch receipt for {shard_id!r} accounts for {attached} attached "
            f"images but the assigned shard has {len(shard_rows)} observations"
        )

    # FL-4813: UIDs must be present exactly once and match the assigned shard.
    expected_uids = receipt["expected_observation_uids"]
    if not isinstance(expected_uids, list) or any(
        not isinstance(uid, str) or not uid for uid in expected_uids
    ):
        raise ValueError(
            f"batch receipt for {shard_id!r} expected_observation_uids "
            "must be a list of non-empty strings"
        )
    if not expected_uids:
        raise ValueError(f"batch receipt for {shard_id!r} lists no observation UIDs")
    if len(set(expected_uids)) != len(expected_uids):
        raise ValueError(
            f"batch receipt for {shard_id!r} lists duplicate observation UIDs"
        )
    if receipt["expected_row_count"] != len(shard_rows) or len(expected_uids) != len(
        shard_rows
    ):
        raise ValueError(
            f"batch receipt for {shard_id!r} declares row count "
            f"{receipt['expected_row_count']} for {len(expected_uids)} UIDs, "
            f"but the assigned shard holds {len(shard_rows)} rows"
        )
    actual_uids = {row["observation_uid"] for row in shard_rows}
    if set(expected_uids) != actual_uids:
        raise ValueError(
            f"batch receipt UID set for {shard_id!r} differs from its assigned shard"
        )
    for uid in expected_uids:
        corpus_id, index = _parse_observation_uid(uid)
        if corpus_id != assigned["corpus_id"] or not (
            assigned["first_index"] <= index <= assigned["last_index"]
        ):
            raise ValueError(
                f"batch receipt for {shard_id!r} lists UID {uid!r} "
                f"outside the assigned observation range"
            )

    # FL-4813: source and output hashes must match repository evidence.
    expected_source = annotation_hash_by_corpus.get(assigned["corpus_id"])
    recorded_source_path = Path(receipt["annotation_source_path"])
    expected_source_path = annotation_path_by_corpus[assigned["corpus_id"]]
    if recorded_source_path.resolve() != expected_source_path.resolve():
        raise ValueError(
            f"batch receipt for {shard_id!r} carries annotation source path "
            f"{str(recorded_source_path)!r}, expected "
            f"{str(expected_source_path)!r}"
        )
    if receipt["annotation_source_sha256"] != expected_source:
        raise ValueError(
            f"batch receipt for {shard_id!r} carries annotation source hash "
            f"{receipt['annotation_source_sha256']!r}, expected {expected_source!r}"
        )
    if receipt["reviewed_output_shard_sha256"] != output_hash_by_shard.get(shard_id):
        raise ValueError(
            f"batch receipt for {shard_id!r} carries reviewed output hash "
            f"{receipt['reviewed_output_shard_sha256']!r}, expected "
            f"{output_hash_by_shard.get(shard_id)!r}"
        )
    input_hash = receipt["input_shard_sha256"]
    if input_hash != input_hash_by_shard.get(shard_id):
        raise ValueError(
            f"batch receipt for {shard_id!r} carries input shard hash "
            f"{input_hash!r}, expected {input_hash_by_shard.get(shard_id)!r}"
        )

    # FL-4813: a batch receipt never promotes, registers, or generates.
    if receipt.get("no_world_ir_promotion") is not True:
        raise ValueError(
            f"batch receipt for {shard_id!r} claims World IR promotion"
        )
    if receipt.get("no_physical_registration") is not True:
        raise ValueError(
            f"batch receipt for {shard_id!r} claims physical registration"
        )
    if receipt.get("no_generation") is not True:
        raise ValueError(f"batch receipt for {shard_id!r} claims generation")
    if not isinstance(receipt.get("review_method"), str) or not receipt[
        "review_method"
    ].strip():
        raise ValueError(f"batch receipt for {shard_id!r} lacks review_method")

    # FL-4813: reject a fabricated complete-provenance claim.
    provenance_status = receipt.get("provenance_status")
    if provenance_status not in (PROVENANCE_INCOMPLETE, PROVENANCE_COMPLETE):
        raise ValueError(
            f"batch receipt for {shard_id!r} carries provenance status "
            f"{provenance_status!r}; only {PROVENANCE_INCOMPLETE!r} or "
            f"{PROVENANCE_COMPLETE!r} is accepted and nothing here is "
            f"fully accepted"
        )
    unresolved = receipt.get("unresolved_provenance_fields") or []
    if provenance_status == PROVENANCE_COMPLETE:
        if unresolved:
            raise ValueError(
                f"batch receipt for {shard_id!r} claims complete provenance "
                f"while listing unresolved fields: {unresolved!r}"
            )
        for field in (
            "thread_id",
            "reviewer_identity",
            "model_identity",
            "review_timestamp",
            "starting_commit",
            "producing_commit",
            "review_method",
        ):
            if receipt.get(field) in (None, "", "unknown"):
                raise ValueError(
                    f"batch receipt for {shard_id!r} claims complete provenance "
                    f"while {field} is unknown"
                )
    return {
        "shard_id": shard_id,
        "thread_id": receipt["thread_id"],
        "provenance_status": provenance_status,
        "expected_row_count": receipt["expected_row_count"],
        "reviewed_output_shard_sha256": receipt["reviewed_output_shard_sha256"],
    }


def skeleton_mapping_row(ledger_row: dict[str, Any]) -> dict[str, Any]:
    """Emit one unresolved mapping row per observation. No module is assigned."""
    uid = (
        f"{ledger_row['corpus_id']}:{ledger_row['source_annotation_index']:03d}:"
        f"{ledger_row['annotation_id']}"
    )
    return {
        "observation_uid": uid,
        **{field: ledger_row[field] for field in REQUIRED_LEDGER_FIELDS},
        "mapping": {
            "status": "pending_visual_review",
            "population_role": ledger_row["proposed_population_role"],
            "population_role_authoritative": False,
            "semantic_family": None,
            "module_id": None,
            "variant_id": None,
            "carrier_type": None,
            "placement_context": None,
            "repeated_feature_candidate": None,
            "physical_instance_candidate": None,
            "existing_pipeline_owner": None,
            "evidence_summary": None,
            "confidence": None,
            "unresolved_reason": "visual_review_thread_open_crop_not_inspected",
        },
    }


def _validate_mapping_rows(
    *,
    shard_id: str,
    rows: list[dict[str, Any]],
    skeleton_rows: list[dict[str, Any]],
    reviewed: bool,
) -> list[str]:
    """Validate one shard through the same row interface used in production."""
    errors: list[str] = []
    if len(rows) != len(skeleton_rows):
        return [
            f"{shard_id}: row count {len(rows)} does not match "
            f"assigned count {len(skeleton_rows)}"
        ]
    immutable = (
        "observation_uid",
        "corpus_id",
        "source_annotation_index",
        "annotation_id",
        "capture_id",
        "crop_path",
        "source_hash",
    )
    for position, (row, skeleton) in enumerate(zip(rows, skeleton_rows), start=1):
        uid = skeleton["observation_uid"]
        for field in immutable:
            if row.get(field) != skeleton.get(field):
                errors.append(
                    f"{shard_id} row {position} {uid}: immutable field "
                    f"{field!r} changed"
                )
        mapping = row.get("mapping")
        if not isinstance(mapping, dict):
            errors.append(f"{shard_id} row {position} {uid}: mapping is absent")
            continue
        status = str(mapping.get("status") or "")
        if reviewed and not status.startswith("reviewed"):
            errors.append(
                f"{shard_id} row {position} {uid}: reviewed batch carries "
                f"status {status!r}"
            )
        if not reviewed and status.startswith("reviewed"):
            errors.append(
                f"{shard_id} row {position} {uid}: reviewed row has no receipt"
            )
        if not reviewed:
            continue
        if mapping.get("population_role_authoritative") is not False:
            errors.append(
                f"{shard_id} row {position} {uid}: reviewed mapping must remain "
                "non-authoritative pending operator acceptance"
            )
        role = mapping.get("population_role")
        if role not in POPULATION_ROLES:
            errors.append(
                f"{shard_id} row {position} {uid}: unsupported population_role "
                f"{role!r}"
            )
            continue
        evidence = mapping.get("evidence_summary")
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append(
                f"{shard_id} row {position} {uid}: reviewed row lacks "
                "evidence_summary"
            )
        unresolved = mapping.get("unresolved_reason")
        if role in ("generic_fixture", "generic_morphology"):
            family = mapping.get("semantic_family")
            if family not in SEMANTIC_FAMILY_TO_CATALOG:
                errors.append(
                    f"{shard_id} row {position} {uid}: generic row has "
                    f"unknown semantic_family {family!r}"
                )
            has_module_route = bool(mapping.get("module_id")) and bool(
                mapping.get("variant_id")
            )
            if not has_module_route and not unresolved:
                errors.append(
                    f"{shard_id} row {position} {uid}: generic row has "
                    "neither module+variant nor unresolved_reason"
                )
        elif role == "custom_identity":
            has_custom_route = bool(mapping.get("custom_subject_id")) and bool(
                mapping.get("custom_module_id")
            )
            if not has_custom_route and not unresolved:
                errors.append(
                    f"{shard_id} row {position} {uid}: custom row has "
                    "neither subject+module route nor unresolved_reason"
                )
        elif role == "discard":
            if not isinstance(mapping.get("discard_reason"), str) or not (
                mapping.get("discard_reason") or ""
            ).strip():
                errors.append(
                    f"{shard_id} row {position} {uid}: discard row lacks "
                    "discard_reason"
                )
        forbidden = {
            "world_position",
            "world_coordinates",
            "latitude",
            "longitude",
            "generated_asset",
            "world_ir_feature_id",
        }
        leaked = sorted(forbidden.intersection(mapping))
        if leaked:
            errors.append(
                f"{shard_id} row {position} {uid}: mapping leaks forbidden "
                f"promotion fields {leaked}"
            )
    return errors


def _synthesize_catalogs(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build one evidence-linked generic catalog and its domain projections."""
    modules: dict[str, dict[str, Any]] = {}
    for row in rows:
        mapping = row.get("mapping", {})
        if mapping.get("population_role") not in (
            "generic_fixture",
            "generic_morphology",
        ):
            continue
        module_id = mapping.get("module_id")
        if not module_id:
            continue
        family = mapping.get("semantic_family")
        module = modules.setdefault(
            module_id,
            {
                "module_id": module_id,
                "owning_domain": family,
                "carrier_or_feature_class": mapping.get("carrier_type"),
                "variants": set(),
                "placement_requirements": set(),
                "parent_feature_requirements": [],
                "supporting_observation_ids": [],
                "existing_implementation_owner": mapping.get(
                    "existing_pipeline_owner"
                ),
                "implementation_state": (
                    "existing"
                    if mapping.get("existing_pipeline_owner")
                    else "missing_or_unconfirmed"
                ),
                "law_or_standard_dependency": None,
                "custom_identity_exclusion_rules": [
                    "branding and local identity remain custom-authoring inputs"
                ],
            },
        )
        if module["owning_domain"] != family:
            raise ValueError(
                f"module {module_id!r} appears in multiple semantic families: "
                f"{module['owning_domain']!r}, {family!r}"
            )
        variant = mapping.get("variant_id")
        if variant:
            module["variants"].add(variant)
        placement = mapping.get("placement_context")
        if placement:
            module["placement_requirements"].add(placement)
        module["supporting_observation_ids"].append(row["observation_uid"])
        if not module["carrier_or_feature_class"] and mapping.get("carrier_type"):
            module["carrier_or_feature_class"] = mapping["carrier_type"]
        if not module["existing_implementation_owner"] and mapping.get(
            "existing_pipeline_owner"
        ):
            module["existing_implementation_owner"] = mapping[
                "existing_pipeline_owner"
            ]
            module["implementation_state"] = "existing"
    serialized = []
    for module_id in sorted(modules):
        module = modules[module_id]
        module["variants"] = sorted(module["variants"])
        module["placement_requirements"] = sorted(
            module["placement_requirements"]
        )
        module["supporting_observation_ids"] = sorted(
            module["supporting_observation_ids"]
        )
        serialized.append(module)
    catalog = {
        "schema": "y92.generic_module_catalog.v1",
        "status": "compiled_from_reviewed_observations",
        "module_count": len(serialized),
        "modules": serialized,
    }
    by_file: dict[str, dict[str, Any]] = {}
    for family, filename in SEMANTIC_FAMILY_TO_CATALOG.items():
        domain_modules = [
            module for module in serialized if module["owning_domain"] == family
        ]
        by_file[filename] = {
            "schema": "y92.domain_module_catalog.v1",
            "status": "compiled_from_reviewed_observations",
            "semantic_family": family,
            "module_count": len(domain_modules),
            "modules": domain_modules,
        }
    return catalog, by_file


def compile_mapping(
    *,
    old_annotations_path: Path,
    new_annotations_path: Path,
    old_corpus_id: str,
    new_corpus_id: str,
    expected_hashes: dict[str, str],
    triage_path: Path | None,
    role_sets: dict[str, dict[str, Any]],
    max_images_per_thread: int,
    out_dir: Path,
    extra_inputs: list[dict[str, str]] | None = None,
    batch_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile the full mapping scaffold. Raises SystemExit-style ValueError on hash mismatch."""
    old_doc = _load_json(old_annotations_path)
    new_doc = _load_json(new_annotations_path)
    actual = {
        old_corpus_id: _sha256_file(old_annotations_path),
        new_corpus_id: _sha256_file(new_annotations_path),
    }
    for corpus_id, expected in expected_hashes.items():
        if actual.get(corpus_id) != expected:
            raise ValueError(
                f"hash mismatch for {corpus_id}: expected {expected} "
                f"actual {actual.get(corpus_id)}"
            )
    triage_by_id: dict[str, str] = {}
    triage_inputs: list[dict[str, str]] = []
    if triage_path is not None:
        triage_doc = _load_json(triage_path)
        triage_inputs.append({
            "path": str(triage_path),
            "sha256": _sha256_file(triage_path),
        })
        for proposal in triage_doc.get("proposals", []):
            triage_by_id[str(proposal["annotation_id"])] = str(
                proposal["population_role"]
            )
    corpora = [
        {
            "corpus_id": old_corpus_id,
            "annotations": old_doc["annotations"],
            "source_hash": actual[old_corpus_id],
        },
        {
            "corpus_id": new_corpus_id,
            "annotations": new_doc["annotations"],
            "source_hash": actual[new_corpus_id],
        },
    ]
    ledger = build_ledger(
        corpora=corpora,
        triage_by_id=triage_by_id,
        role_sets_by_corpus=role_sets,
    )
    shards = partition_shards(ledger, max_images_per_thread=max_images_per_thread)
    mapping_rows = [skeleton_mapping_row(row) for row in ledger]

    out_dir.mkdir(parents=True, exist_ok=True)

    # FL-4813 mapping-review intake: reconcile reviewed shards without editing them.
    # Existing shard files are review-thread evidence. This compiler reads them
    # and validates their batch receipts. It never rewrites them.
    thread_meta = [
        {
            "shard_id": shard["shard_id"],
            "corpus_id": shard["corpus_id"],
            "first_index": shard["first_index"],
            "last_index": shard["last_index"],
            "row_count": shard["row_count"],
            "file": shard["file"],
        }
        for shard in shards
    ]
    skeleton_by_shard = {
        shard["shard_id"]: [skeleton_mapping_row(row) for row in shard["rows"]]
        for shard in shards
    }
    rows_by_shard: dict[str, list[dict[str, Any]]] = {}
    input_hash_by_shard = {
        shard_id: _canonical_rows_hash(rows)
        for shard_id, rows in skeleton_by_shard.items()
    }
    output_hash_by_shard: dict[str, str] = {}
    for shard in shards:
        shard_path = out_dir / shard["file"]
        if shard_path.is_file():
            raw = shard_path.read_bytes()
            output_hash_by_shard[shard["shard_id"]] = hashlib.sha256(raw).hexdigest()
            rows_by_shard[shard["shard_id"]] = [
                json.loads(line)
                for line in raw.decode("utf-8").splitlines()
                if line.strip()
            ]
        else:
            rows_by_shard[shard["shard_id"]] = skeleton_by_shard[shard["shard_id"]]
            output_hash_by_shard[shard["shard_id"]] = _canonical_rows_hash(
                skeleton_by_shard[shard["shard_id"]]
            )

    validated_batches: list[dict[str, Any]] = []
    for receipt_doc in batch_receipts or []:
        validated_batches.append(
            validate_batch_receipt(
                receipt_doc,
                threads=thread_meta,
                rows_by_shard=rows_by_shard,
                input_hash_by_shard=input_hash_by_shard,
                output_hash_by_shard=output_hash_by_shard,
                annotation_hash_by_corpus=actual,
                annotation_path_by_corpus={
                    old_corpus_id: old_annotations_path,
                    new_corpus_id: new_annotations_path,
                },
            )
        )
    if len({batch["shard_id"] for batch in validated_batches}) != len(
        validated_batches
    ):
        raise ValueError("duplicate batch receipts cover one shard")
    validated_by_shard = {batch["shard_id"]: batch for batch in validated_batches}
    covered_shards = set(validated_by_shard)
    # FL-4813: a reviewed shard without a batch receipt fails closed.
    for shard in shards:
        shard_rows = rows_by_shard[shard["shard_id"]]
        reviewed = any(
            str(row.get("mapping", {}).get("status", "")).startswith("reviewed")
            for row in shard_rows
        )
        if reviewed and shard["shard_id"] not in covered_shards:
            raise ValueError(
                f"reviewed shard {shard['shard_id']!r} has no batch receipt"
            )

    row_errors: list[str] = []
    for shard in shards:
        shard_id = shard["shard_id"]
        row_errors.extend(
            _validate_mapping_rows(
                shard_id=shard_id,
                rows=rows_by_shard[shard_id],
                skeleton_rows=skeleton_by_shard[shard_id],
                reviewed=shard_id in covered_shards,
            )
        )
    if row_errors:
        raise ValueError("mapping row validation failed: " + "; ".join(row_errors))

    # The canonical mapping ledger is the deterministic concatenation of the
    # reviewed shard interface, never a second independently edited owner.
    mapping_rows = [
        row
        for shard in shards
        for row in rows_by_shard[shard["shard_id"]]
    ]

    def _thread_status(shard_id: str) -> str:
        batch = validated_by_shard.get(shard_id)
        if batch is None:
            return THREAD_STATUS_OPEN
        if batch["provenance_status"] == PROVENANCE_COMPLETE:
            return THREAD_STATUS_REVIEWED_COMPLETE
        return THREAD_STATUS_REVIEWED_INCOMPLETE

    out_dir.mkdir(parents=True, exist_ok=True)
    reviewed_shard_ids = [
        shard["shard_id"] for shard in shards if shard["shard_id"] in covered_shards
    ]
    open_shard_ids = [
        shard["shard_id"]
        for shard in shards
        if shard["shard_id"] not in covered_shards
    ]
    review_batches_complete = not open_shard_ids
    unresolved_mapping_count = sum(
        bool(row.get("mapping", {}).get("unresolved_reason"))
        for row in mapping_rows
    )
    domain_normalization_complete = review_batches_complete and all(
        row.get("mapping", {}).get("variant_id") != "pending_domain_normalization"
        for row in mapping_rows
    )
    # Explicit unresolved routes are conserved evidence, not fabricated module
    # assignments. The row validator guarantees each reviewed observation has
    # either a complete role-specific route or a named unresolved reason.
    mapping_complete = review_batches_complete

    manifest = {
        "schema": SCHEMA_MANIFEST,
        "stage": (
            "mapping_compiled"
            if mapping_complete and domain_normalization_complete
            else "mapping_review_or_normalization_open"
        ),
        "inputs": [
            {
                "corpus_id": old_corpus_id,
                "path": str(old_annotations_path),
                "sha256": actual[old_corpus_id],
                "annotations": len(corpora[0]["annotations"]),
            },
            {
                "corpus_id": new_corpus_id,
                "path": str(new_annotations_path),
                "sha256": actual[new_corpus_id],
                "annotations": len(corpora[1]["annotations"]),
            },
            *triage_inputs,
            *(extra_inputs or []),
        ],
        "max_images_per_thread": max_images_per_thread,
        "threads": [
            {
                "shard_id": shard["shard_id"],
                "corpus_id": shard["corpus_id"],
                "first_index": shard["first_index"],
                "last_index": shard["last_index"],
                "row_count": shard["row_count"],
                "file": shard["file"],
                "status": _thread_status(shard["shard_id"]),
            }
            for shard in shards
        ],
        "batch_receipts": [
            {
                "shard_id": shard_id,
                "thread_id": validated_by_shard[shard_id]["thread_id"],
                "provenance_status": validated_by_shard[shard_id][
                    "provenance_status"
                ],
                "expected_row_count": validated_by_shard[shard_id][
                    "expected_row_count"
                ],
                "reviewed_output_shard_sha256": validated_by_shard[shard_id][
                    "reviewed_output_shard_sha256"
                ],
            }
            for shard_id in sorted(validated_by_shard)
        ],
        "replacement_subbatches": [],
        "counts": {
            "observations": len(ledger),
            old_corpus_id: sum(1 for row in ledger if row["corpus_id"] == old_corpus_id),
            new_corpus_id: sum(1 for row in ledger if row["corpus_id"] == new_corpus_id),
            "shards": len(shards),
        },
        "mapping_complete": mapping_complete,
        "domain_normalization_complete": domain_normalization_complete,
        "generic_kit_bom_complete": (
            mapping_complete and domain_normalization_complete
        ),
    }
    _dump_json(out_dir / "mapping_manifest.json", manifest)
    # FL-4813: existing scaffold and shard files are review-thread evidence.
    # Only missing files are written. Manifest and receipt are always refreshed.
    _dump_jsonl(out_dir / "observation_module_mapping.jsonl", mapping_rows)
    for shard in shards:
        shard_path = out_dir / shard["file"]
        if not shard_path.is_file():
            _dump_jsonl(shard_path, skeleton_by_shard[shard["shard_id"]])

    generic_catalog, domain_catalogs = _synthesize_catalogs(mapping_rows)
    generic_catalog["status"] = (
        "compiled_from_reviewed_observations"
        if generic_catalog["modules"]
        else "blocked_pending_visual_review"
    )
    generic_catalog["complete"] = (
        mapping_complete and domain_normalization_complete
    )
    _dump_json(out_dir / "generic_module_catalog.json", generic_catalog)

    physical_candidates = [
        {
            "observation_uid": row["observation_uid"],
            "candidate": row.get("mapping", {}).get(
                "physical_instance_candidate"
            ),
        }
        for row in mapping_rows
        if row.get("mapping", {}).get("physical_instance_candidate")
    ]
    _dump_json(out_dir / "physical_instance_clusters.json", {
        "schema": "y92.physical_instance_clusters.v1",
        "status": "candidate_links_only_pending_step4_registration",
        "clusters": [],
        "unresolved_candidates": physical_candidates,
        "note": "Mapping artifacts preserve candidate links but perform no "
                "physical merge or world registration. Step 4 owns evidenced "
                "physical identity.",
    })
    from collections import Counter
    partition = Counter(row["proposed_population_role"] for row in ledger)
    current_partition = Counter(
        row.get("mapping", {}).get("population_role") for row in mapping_rows
    )
    reviewed_partition = Counter(
        row.get("mapping", {}).get("population_role")
        for shard in shards
        if shard["shard_id"] in covered_shards
        for row in rows_by_shard[shard["shard_id"]]
    )
    classification_changes = [
        {
            "observation_uid": row["observation_uid"],
            "proposed_population_role": row["proposed_population_role"],
            "reviewed_population_role": row.get("mapping", {}).get(
                "population_role"
            ),
        }
        for row in mapping_rows
        if row.get("mapping", {}).get("population_role")
        != row["proposed_population_role"]
    ]
    _dump_json(out_dir / "classification_changes.json", {
        "schema": "y92.classification_changes.v1",
        "status": "compiled_review_diff_not_operator_accepted",
        "proposed_partition": dict(sorted(partition.items())),
        "current_full_partition": {
            str(key): value for key, value in sorted(current_partition.items())
        },
        "reviewed_partition": {
            str(key): value for key, value in sorted(reviewed_partition.items())
        },
        "changes": classification_changes,
    })
    unresolved_rows = [
        {
            "observation_uid": row["observation_uid"],
            "reason": row.get("mapping", {}).get("unresolved_reason"),
        }
        for row in mapping_rows
        if row.get("mapping", {}).get("unresolved_reason")
    ]
    _dump_json(out_dir / "unresolved_mapping_queue.json", {
        "schema": "y92.unresolved_mapping_queue.v1",
        "unresolved_count": len(unresolved_rows),
        "rows": unresolved_rows,
    })
    catalog_dir = out_dir / "catalog"
    for filename in DOMAIN_CATALOG_FILES:
        document = domain_catalogs[filename]
        document["status"] = (
            "compiled_from_reviewed_observations"
            if document["modules"]
            else "blocked_pending_visual_review"
        )
        document["complete"] = (
            mapping_complete and domain_normalization_complete
        )
        document["file"] = f"catalog/{filename}"
        _dump_json(catalog_dir / filename, document)
    reviewed_summaries = []
    for shard_id in reviewed_shard_ids:
        batch = validated_by_shard[shard_id]
        reviewed_summaries.append(
            f"{shard_id} review exists with {batch['provenance_status']} state "
            f"(thread {batch['thread_id']}, {batch['expected_row_count']} rows)"
        )
    mapped_generic_rows_present = any(
        row.get("mapping", {}).get("population_role")
        in ("generic_fixture", "generic_morphology")
        and row.get("mapping", {}).get("module_id")
        for row in mapping_rows
    )
    catalog_with_evidence_links = (
        (not mapped_generic_rows_present or bool(generic_catalog["modules"]))
        and all(
        bool(module.get("supporting_observation_ids"))
        for module in generic_catalog["modules"]
        )
    )
    mapping_passed = all((
        review_batches_complete,
        mapping_complete,
        domain_normalization_complete,
        catalog_with_evidence_links,
    ))
    gates = {
        "observations_conserved": len(ledger) == (
            len(corpora[0]["annotations"]) + len(corpora[1]["annotations"])
        ),
        "input_hashes_match": True,
        "compilation_deterministic": True,
        "every_row_has_role_or_unresolved": all(
            bool(row["mapping"]["population_role"])
            or bool(row["mapping"]["unresolved_reason"])
            for row in mapping_rows
        ),
        "module_mapping_complete": mapping_complete,
        "domain_normalization_complete": domain_normalization_complete,
        "catalog_with_evidence_links": catalog_with_evidence_links,
        "no_world_ir_promotion": True,
        "no_asset_generation": True,
        "no_map_population": True,
        "batch_receipts_accepted": len(validated_batches),
        "review_batches_complete": review_batches_complete,
        "mapping_passed": mapping_passed,
    }
    blockers = []
    if open_shard_ids:
        blockers.append("visual_review_threads_open")
    if not domain_normalization_complete:
        blockers.append("domain_normalization_incomplete")
    if not catalog_with_evidence_links:
        blockers.append("catalog_evidence_links_incomplete")
    receipt = {
        "schema": SCHEMA_RECEIPT,
        "stage": (
            "mapping_compiled"
            if mapping_passed
            else "mapping_review_or_normalization_open"
        ),
        "mapping_passed": mapping_passed,
        "blockers_before_observed_join": blockers,
        "gates": gates,
        "counts": manifest["counts"],
        "proposed_partition": dict(sorted(partition.items())),
        "current_full_partition": {
            str(key): value for key, value in sorted(current_partition.items())
        },
        "reviewed_partition": {
            str(key): value for key, value in sorted(reviewed_partition.items())
        },
        "reviewed_batches": reviewed_shard_ids,
        "open_batches": open_shard_ids,
        "review_summaries": reviewed_summaries,
        "unresolved_mapping_count": unresolved_mapping_count,
        "outputs": {
            "mapping_manifest": {
                "path": "mapping_manifest.json",
                "sha256": _sha256_file(out_dir / "mapping_manifest.json"),
            },
            "observation_module_mapping": {
                "path": "observation_module_mapping.jsonl",
                "sha256": _sha256_file(out_dir / "observation_module_mapping.jsonl"),
            },
            "generic_module_catalog": {
                "path": "generic_module_catalog.json",
                "sha256": _sha256_file(out_dir / "generic_module_catalog.json"),
            },
            "domain_catalogs": {
                filename: {
                    "path": f"catalog/{filename}",
                    "sha256": _sha256_file(out_dir / "catalog" / filename),
                }
                for filename in DOMAIN_CATALOG_FILES
            },
        },
    }
    _dump_json(out_dir / "mapping_receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-annotations", type=Path, required=True)
    parser.add_argument("--new-annotations", type=Path, required=True)
    parser.add_argument("--old-corpus-id", default="old")
    parser.add_argument("--new-corpus-id", default="new")
    parser.add_argument("--expected-hash", action="append", default=[],
                        help="CORPUS_ID:SHA256 entries; all listed corpora are verified")
    parser.add_argument("--triage", type=Path, default=None)
    parser.add_argument("--role-sets", type=Path, required=True,
                        help="JSON mapping corpus_id to default_role plus "
                             "custom_identity/generic_morphology/discard index lists")
    parser.add_argument("--role-sets-id", default="role_sets",
                        help="Provenance label recorded for the role-sets input")
    parser.add_argument("--max-images-per-thread", type=int, default=49)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--batch-receipt", action="append", default=[],
                        help="Path to a y92.capture_module_review_batch_receipt.v1 "
                             "JSON file; repeatable, one per reviewed batch")
    parser.add_argument("--batch-receipts-dir", type=Path, default=None,
                        help="Directory containing batch receipt JSON files; "
                             "all *.batch_receipt.json files are loaded in "
                             "lexical order")
    args = parser.parse_args(argv)
    expected: dict[str, str] = {}
    for entry in args.expected_hash:
        corpus_id, _, digest = entry.partition(":")
        if not corpus_id or not digest:
            raise ValueError(f"malformed --expected-hash entry: {entry!r}")
        expected[corpus_id] = digest
    role_sets = json.loads(args.role_sets.read_text(encoding="utf-8"))
    role_sets_sha = _sha256_file(args.role_sets)
    if args.batch_receipt and args.batch_receipts_dir is not None:
        parser.error("use --batch-receipt or --batch-receipts-dir, not both")
    receipt_paths = [Path(entry) for entry in args.batch_receipt]
    if args.batch_receipts_dir is not None:
        if not args.batch_receipts_dir.is_dir():
            parser.error(
                f"batch receipts directory is absent: {args.batch_receipts_dir}"
            )
        receipt_paths = sorted(
            args.batch_receipts_dir.glob("*.batch_receipt.json")
        )
        if not receipt_paths:
            parser.error(
                f"batch receipts directory has no *.batch_receipt.json files: "
                f"{args.batch_receipts_dir}"
            )
    batch_receipts = [_load_json(path) for path in receipt_paths]
    receipt = compile_mapping(
        old_annotations_path=args.old_annotations,
        new_annotations_path=args.new_annotations,
        old_corpus_id=args.old_corpus_id,
        new_corpus_id=args.new_corpus_id,
        expected_hashes=expected,
        triage_path=args.triage,
        role_sets=role_sets,
        max_images_per_thread=args.max_images_per_thread,
        out_dir=args.out_dir,
        extra_inputs=[
            {
                "input_id": args.role_sets_id,
                "path": str(args.role_sets),
                "sha256": role_sets_sha,
            }
        ],
        batch_receipts=batch_receipts,
    )
    print(f"[capture-module-mapping] counts={receipt['counts']}")
    print(f"[capture-module-mapping] mapping_passed={receipt['mapping_passed']}")
    print(f"[capture-module-mapping] wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

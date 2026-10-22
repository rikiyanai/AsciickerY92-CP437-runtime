#!/usr/bin/env python3
"""Compile reviewed crop batches into generic-instance and custom-authoring lanes.

First-pass box labels and visual-similarity groups are deliberately non-
authoritative.  Only an explicit group or per-annotation semantic review can
route an annotation.  The output remains image-space evidence until a separate
registration/localization stage establishes a world position.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MORPHOLOGY_VOCABULARY_PATH = (
    PROJECT_ROOT / "assets/world_templates/japan_urban/generic_morphology.json"
)


ANNOTATIONS_SCHEMA = "y92.capture_review.v1"
GROUPS_SCHEMA = "y92.capture_similarity_groups.v1"
DECISIONS_SCHEMA = "y92.capture_semantic_review.v1"
SCHEMA = "y92.capture_authoring_routing.v1"
IDENTITY_SENSITIVE_CLASSES = {
    "station_identity_sign",
    "station_sign",
    "storefront_sign",
    "store_sign",
    "business_identity_sign",
    "specific_store_sign",
    "building_name_sign",
    "specific_notice_panel",
}
REVIEW_ACTIONS = {"accept", "correct", "reject_similarity", "discard"}


def _morphology_classes() -> dict[str, dict[str, Any]]:
    if not MORPHOLOGY_VOCABULARY_PATH.is_file():
        return {}
    document = json.loads(MORPHOLOGY_VOCABULARY_PATH.read_text(encoding="utf-8"))
    if document.get("schema") != "y92.generic_morphology_vocabulary.v1":
        raise ValueError("generic morphology vocabulary schema is unsupported")
    return {row["semantic_class"]: row for row in document.get("classes", [])}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def empty_decisions(groups_path: Path) -> dict[str, Any]:
    groups_path = groups_path.resolve()
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema": DECISIONS_SCHEMA,
        "stage": "semantic_review_in_progress",
        "groups": {"path": portable_path(groups_path), "sha256": sha256(groups_path)},
        "created_at": now,
        "updated_at": now,
        "group_reviews": [],
        "annotation_reviews": [],
    }


def _validate_custom_meta(
    decision: dict[str, Any], semantic_label: str, subject: str,
) -> tuple[bool, dict[str, Any] | None]:
    custom = decision.get("custom_extraction_required")
    if custom is None:
        custom = semantic_label in IDENTITY_SENSITIVE_CLASSES
    if not isinstance(custom, bool):
        raise ValueError(f"{subject}.custom_extraction_required must be boolean")
    if semantic_label in IDENTITY_SENSITIVE_CLASSES and not custom:
        raise ValueError(f"{subject} class {semantic_label!r} requires custom extraction")
    meta = decision.get("custom_meta")
    if custom:
        if not isinstance(meta, dict):
            raise ValueError(f"{subject}.custom_meta is required for custom extraction")
        required = ("asset_key", "authoring_kind", "reason")
        missing = [key for key in required if not str(meta.get(key) or "").strip()]
        if missing:
            raise ValueError(f"{subject}.custom_meta lacks {', '.join(missing)}")
        meta = {
            "asset_key": str(meta["asset_key"]).strip(),
            "authoring_kind": str(meta["authoring_kind"]).strip(),
            "reason": str(meta["reason"]).strip(),
            "generic_carrier_class": (
                str(meta.get("generic_carrier_class") or "").strip() or None
            ),
            "source_text_hint": str(meta.get("source_text_hint") or "").strip() or None,
        }
    elif meta not in (None, {}):
        raise ValueError(f"{subject}.custom_meta must be empty when custom extraction is false")
    return custom, meta


def _normalize_review(row: Any, *, key_name: str, subject: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"{subject} must be an object")
    key = row.get(key_name)
    action = row.get("action")
    if not isinstance(key, str) or not key:
        raise ValueError(f"{subject}.{key_name} is required")
    if action not in REVIEW_ACTIONS:
        raise ValueError(f"{subject}.action is unsupported")
    note = row.get("correction_note") or ""
    if not isinstance(note, str):
        raise ValueError(f"{subject}.correction_note must be text")
    normalized = {key_name: key, "action": action, "correction_note": note}
    if action in {"accept", "correct"}:
        label = str(row.get("semantic_label") or "").strip()
        if not label:
            raise ValueError(f"{subject}.semantic_label is required")
        custom, meta = _validate_custom_meta(row, label, subject)
        normalized.update({
            "semantic_label": label,
            "custom_extraction_required": custom,
            "custom_meta": meta,
        })
    else:
        normalized.update({
            "semantic_label": None,
            "custom_extraction_required": None,
            "custom_meta": None,
        })
    return normalized


def validate_decisions(
    payload: Any, groups: dict[str, Any], groups_path: Path,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != DECISIONS_SCHEMA:
        raise ValueError(f"decisions are not {DECISIONS_SCHEMA}")
    expected_sha = sha256(groups_path)
    if (payload.get("groups") or {}).get("sha256") != expected_sha:
        raise ValueError("semantic decisions refer to different similarity groups")
    group_ids = {row["group_id"] for row in groups["groups"]}
    annotation_ids = {row["annotation_id"] for row in groups["membership"]}
    group_reviews = []
    seen_groups = set()
    for index, row in enumerate(payload.get("group_reviews") or []):
        review = _normalize_review(row, key_name="group_id", subject=f"group_reviews[{index}]")
        if review["group_id"] not in group_ids:
            raise ValueError(f"unknown group_id: {review['group_id']}")
        if review["group_id"] in seen_groups:
            raise ValueError(f"duplicate group review: {review['group_id']}")
        seen_groups.add(review["group_id"])
        group_reviews.append(review)
    annotation_reviews = []
    seen_annotations = set()
    for index, row in enumerate(payload.get("annotation_reviews") or []):
        review = _normalize_review(
            row, key_name="annotation_id", subject=f"annotation_reviews[{index}]"
        )
        if review["annotation_id"] not in annotation_ids:
            raise ValueError(f"unknown annotation_id: {review['annotation_id']}")
        if review["annotation_id"] in seen_annotations:
            raise ValueError(f"duplicate annotation review: {review['annotation_id']}")
        seen_annotations.add(review["annotation_id"])
        annotation_reviews.append(review)
    return {
        "schema": DECISIONS_SCHEMA,
        "stage": "semantic_review_in_progress",
        "groups": {"path": portable_path(groups_path), "sha256": expected_sha},
        "created_at": str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "updated_at": str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        "group_reviews": sorted(group_reviews, key=lambda row: row["group_id"]),
        "annotation_reviews": sorted(annotation_reviews, key=lambda row: row["annotation_id"]),
    }


def build_document(
    annotations_path: Path, groups_path: Path, decisions_path: Path,
) -> dict[str, Any]:
    annotations_path = annotations_path.resolve()
    groups_path = groups_path.resolve()
    decisions_path = decisions_path.resolve()
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    decisions_raw = json.loads(decisions_path.read_text(encoding="utf-8"))
    if annotations.get("schema") != ANNOTATIONS_SCHEMA:
        raise ValueError(f"annotations are not {ANNOTATIONS_SCHEMA}")
    if groups.get("schema") != GROUPS_SCHEMA:
        raise ValueError(f"groups are not {GROUPS_SCHEMA}")
    if (groups.get("input") or {}).get("sha256") != sha256(annotations_path):
        raise ValueError("similarity groups refer to different annotations")
    decisions = validate_decisions(decisions_raw, groups, groups_path)
    annotations_by_id = {row["annotation_id"]: row for row in annotations["annotations"]}
    membership = {row["annotation_id"]: row["group_id"] for row in groups["membership"]}
    if set(annotations_by_id) != set(membership):
        raise ValueError("similarity membership must cover every annotation exactly once")
    group_reviews = {row["group_id"]: row for row in decisions["group_reviews"]}
    annotation_reviews = {
        row["annotation_id"]: row for row in decisions["annotation_reviews"]
    }
    morphology_classes = _morphology_classes()
    routed = []
    for annotation_id in sorted(annotations_by_id):
        source = annotations_by_id[annotation_id]
        group_id = membership[annotation_id]
        decision = annotation_reviews.get(annotation_id) or group_reviews.get(group_id)
        decision_scope = (
            "annotation_override" if annotation_id in annotation_reviews
            else "group_review" if decision is not None else None
        )
        base = {
            "annotation_id": annotation_id,
            "group_id": group_id,
            "capture_id": source["capture_id"],
            "box_norm": source["box_norm"],
            "crop": deepcopy(source["crop"]),
            "camera": deepcopy(source.get("capture_snapshot")),
            "first_pass_label": source.get("label"),
            "first_pass_label_authoritative": False,
            "decision_scope": decision_scope,
        }
        if decision is None or decision["action"] == "reject_similarity":
            base.update({
                "review_state": "unresolved",
                "semantic_label": None,
                "delivery_route": "unresolved_review",
                "custom_extraction_required": None,
                "custom_meta": None,
                "correction_note": (
                    decision["correction_note"] if decision is not None else ""
                ),
            })
        elif decision["action"] == "discard":
            base.update({
                "review_state": "discarded",
                "semantic_label": None,
                "delivery_route": "discard",
                "custom_extraction_required": False,
                "custom_meta": None,
                "correction_note": decision["correction_note"],
            })
        else:
            custom = decision["custom_extraction_required"]
            morphology = morphology_classes.get(decision["semantic_label"])
            base.update({
                "review_state": "reviewed",
                "semantic_label": decision["semantic_label"],
                "delivery_route": (
                    "custom_authoring" if custom
                    else "generic_population_profile" if morphology
                    else "generic_template_instance"
                ),
                "population_role": (
                    "custom_identity" if custom
                    else "generic_morphology" if morphology
                    else "generic_fixture"
                ),
                "generic_meta": (
                    {
                        "carrier": morphology["carrier"],
                        "placement_rule": morphology["placement_rule"],
                        "repeatable": morphology["repeatable"],
                        "implementation_state": morphology["implementation_state"],
                    }
                    if morphology else None
                ),
                "custom_extraction_required": custom,
                "custom_meta": deepcopy(decision["custom_meta"]),
                "correction_note": decision["correction_note"],
            })
        routed.append(base)
    generic = [row for row in routed if row["delivery_route"] == "generic_template_instance"]
    morphology = [
        row for row in routed if row["delivery_route"] == "generic_population_profile"
    ]
    custom = [row for row in routed if row["delivery_route"] == "custom_authoring"]
    discarded = [row for row in routed if row["delivery_route"] == "discard"]
    unresolved = [row for row in routed if row["delivery_route"] == "unresolved_review"]
    return {
        "schema": SCHEMA,
        "stage": "review_routed_image_observations",
        "inputs": {
            "annotations": {"path": portable_path(annotations_path), "sha256": sha256(annotations_path)},
            "groups": {"path": portable_path(groups_path), "sha256": sha256(groups_path)},
            "decisions": {"path": portable_path(decisions_path), "sha256": sha256(decisions_path)},
        },
        "policy": {
            "first_pass_labels_authoritative": False,
            "similarity_groups_are_semantic_labels": False,
            "identity_sensitive_classes": sorted(IDENTITY_SENSITIVE_CLASSES),
            "custom_metadata_required_fields": ["asset_key", "authoring_kind", "reason"],
            "generic_morphology_vocabulary": {
                "path": portable_path(MORPHOLOGY_VOCABULARY_PATH),
                "sha256": sha256(MORPHOLOGY_VOCABULARY_PATH),
            },
            "world_localization_state": "unresolved",
        },
        "counts": {
            "annotations": len(routed),
            "generic_template_instances": len(generic),
            "generic_population_profiles": len(morphology),
            "custom_authoring": len(custom),
            "discarded": len(discarded),
            "unresolved_review": len(unresolved),
        },
        "generic_template_instances": generic,
        "generic_population_profiles": morphology,
        "custom_authoring_queue": custom,
        "discarded": discarded,
        "unresolved_review_queue": unresolved,
        "all_annotations": routed,
        "promotion": {
            "semantic_review_complete": not unresolved,
            "camera_registration_accepted": False,
            "world_ir_promotion_ready": False,
        },
    }


def write_routing(
    annotations_path: Path, groups_path: Path, decisions_path: Path, out_path: Path,
) -> dict[str, Any]:
    document = build_document(annotations_path, groups_path, decisions_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(canonical_bytes(document))
        temporary.replace(out_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--init-decisions", action="store_true")
    args = parser.parse_args(argv)
    if args.init_decisions and not args.decisions.exists():
        args.decisions.parent.mkdir(parents=True, exist_ok=True)
        args.decisions.write_bytes(canonical_bytes(empty_decisions(args.groups)))
    document = write_routing(args.annotations, args.groups, args.decisions, args.out)
    print(
        f"[capture-routing] generic={document['counts']['generic_template_instances']} "
        f"morphology={document['counts']['generic_population_profiles']} "
        f"custom={document['counts']['custom_authoring']} "
        f"discarded={document['counts']['discarded']} "
        f"unresolved={document['counts']['unresolved_review']}"
    )
    print(f"[capture-routing] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""End-to-end contracts for grouped capture review and authoring routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.pipeline.compile_capture_review_routing import (
    DECISIONS_SCHEMA,
    build_document as build_routing,
    canonical_bytes,
    empty_decisions,
)
from scripts.pipeline.compile_manual_label_population_triage import classify
from scripts.pipeline.group_capture_review_crops import build_document as build_groups
from scripts.pipeline.world_ir_ingest_capture_review import ingest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(tmp_path: Path) -> Path:
    crops = tmp_path / "crops"
    crops.mkdir()
    colors = ((180, 20, 20), (180, 20, 20), (20, 180, 20), (20, 20, 180))
    rows = []
    for index, color in enumerate(colors):
        path = crops / f"ann-{index}.png"
        Image.new("RGB", (40, 60), color).save(path)
        rows.append({
            "annotation_id": f"ann-{index}",
            "capture_id": f"capture-{index}",
            "box_norm": [0.1, 0.2, 0.3, 0.4],
            "label": "VENDING MACHIEN" if index < 3 else "unclassified_fixture",
            "crop": {
                "path": f"crops/{path.name}",
                "sha256": _sha(path),
                "width": 40,
                "height": 60,
            },
            "capture_snapshot": {"timestamp": "2026-01-01T00:00:00Z"},
        })
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps({"schema": "y92.capture_review.v1", "annotations": rows}))
    return path


def _prepared(tmp_path: Path) -> tuple[Path, Path, Path]:
    annotations = _source(tmp_path)
    groups_path = tmp_path / "groups.json"
    groups_path.write_bytes(canonical_bytes(build_groups(annotations, threshold=0.01)))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_bytes(canonical_bytes(empty_decisions(groups_path)))
    return annotations, groups_path, decisions_path


def test_grouping_is_deterministic_and_never_merges_annotations(tmp_path: Path):
    annotations = _source(tmp_path)
    first = build_groups(annotations, threshold=0.01)
    second = build_groups(annotations, threshold=0.01)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["counts"]["annotations"] == 4
    assert sorted(row["annotation_id"] for row in first["membership"]) == [
        "ann-0", "ann-1", "ann-2", "ann-3"
    ]
    assert max(group["member_count"] for group in first["groups"]) == 2
    assert first["promotion"]["world_ir_promotion_ready"] is False
    assert all(group["semantic_label"] is None for group in first["groups"])


def test_grouping_refuses_changed_crop_pixels(tmp_path: Path):
    annotations = _source(tmp_path)
    Image.new("RGB", (40, 60), "black").save(tmp_path / "crops" / "ann-0.png")
    with pytest.raises(ValueError, match="crop digest mismatch"):
        build_groups(annotations)


def test_agent_triage_proposes_review_labels_without_accepting_them(tmp_path: Path):
    annotations = _source(tmp_path)
    triage = tmp_path / "triage.json"
    triage.write_text(json.dumps({
        "schema": "y92.manual_label_population_triage.v1",
        "proposals": [
            {
                "annotation_id": f"ann-{index}",
                "proposed_semantic_label": "property_boundary_hedge",
                "population_role": "generic_morphology",
            }
            for index in range(4)
        ],
    }), encoding="utf-8")
    grouped = build_groups(
        annotations, threshold=0.01, label_triage_path=triage,
    )
    assert grouped["counts"]["agent_triaged_annotations"] == 4
    assert grouped["counts"]["agent_triage_batches"] == grouped["counts"]["groups"]
    assert all(
        row["state"] == "candidate_agent_triage"
        and row["proposed_semantic_label"] == "property_boundary_hedge"
        for row in grouped["groups"]
    )
    assert grouped["promotion"]["semantic_labels_accepted"] == 0


def test_review_routing_fans_groups_out_and_requires_custom_metadata(tmp_path: Path):
    annotations, groups_path, decisions_path = _prepared(tmp_path)
    groups = json.loads(groups_path.read_text())
    repeated_group = next(group for group in groups["groups"] if group["member_count"] == 2)
    singleton = next(group for group in groups["groups"] if group["member_count"] == 1)
    decisions = json.loads(decisions_path.read_text())
    decisions["group_reviews"] = [{
        "group_id": repeated_group["group_id"],
        "action": "correct",
        "semantic_label": "exterior_air_conditioner",
        "custom_extraction_required": False,
        "custom_meta": None,
        "correction_note": "same reusable fixture class",
    }]
    decisions["annotation_reviews"] = [{
        "annotation_id": singleton["members"][0]["annotation_id"],
        "action": "correct",
        "semantic_label": "storefront_sign",
        "custom_extraction_required": True,
        "custom_meta": {
            "asset_key": "specific_shop_sign",
            "authoring_kind": "storefront_identity",
            "reason": "location-specific lettering",
            "generic_carrier_class": "sign_panel",
        },
        "correction_note": "retain exact identity",
    }]
    decisions_path.write_bytes(canonical_bytes(decisions))
    routing = build_routing(annotations, groups_path, decisions_path)
    assert routing["counts"]["generic_template_instances"] == 0
    assert routing["counts"]["generic_population_profiles"] == 2
    assert len({row["annotation_id"] for row in routing["generic_population_profiles"]}) == 2
    assert all(
        row["population_role"] == "generic_morphology"
        and row["generic_meta"]["carrier"] == "building_facade"
        for row in routing["generic_population_profiles"]
    )
    assert routing["counts"]["custom_authoring"] == 1
    custom = routing["custom_authoring_queue"][0]
    assert custom["custom_extraction_required"] is True
    assert custom["custom_meta"]["asset_key"] == "specific_shop_sign"
    assert custom["first_pass_label_authoritative"] is False
    assert routing["counts"]["unresolved_review"] == 1


def test_identity_sign_cannot_be_routed_as_generic(tmp_path: Path):
    annotations, groups_path, decisions_path = _prepared(tmp_path)
    groups = json.loads(groups_path.read_text())
    decisions = json.loads(decisions_path.read_text())
    decisions["group_reviews"] = [{
        "group_id": groups["groups"][0]["group_id"],
        "action": "accept",
        "semantic_label": "station_identity_sign",
        "custom_extraction_required": False,
        "custom_meta": None,
        "correction_note": "",
    }]
    decisions_path.write_bytes(canonical_bytes(decisions))
    with pytest.raises(ValueError, match="requires custom extraction"):
        build_routing(annotations, groups_path, decisions_path)


def test_routing_ingests_as_observations_without_world_content(tmp_path: Path):
    annotations, groups_path, decisions_path = _prepared(tmp_path)
    routing_path = tmp_path / "routing.json"
    routing_path.write_bytes(
        canonical_bytes(build_routing(annotations, groups_path, decisions_path))
    )
    receipt = ingest(routing_path, tmp_path / "review.world_ir.json")
    assert receipt["source_observations"] == 4
    assert receipt["canonical_features"] == 0
    assert receipt["generated_representations"] == 0
    assert receipt["world_ir_promotion_ready"] is False


def test_decision_schema_is_explicit():
    assert DECISIONS_SCHEMA == "y92.capture_semantic_review.v1"


def test_agent_label_triage_separates_morphology_identity_and_discard():
    assert classify("ivy_wall_with_gate")["population_role"] == "generic_morphology"
    identity = classify("storefront_sign_mikasa")
    assert identity["population_role"] == "custom_identity"
    assert identity["custom_extraction_required"] is True
    assert classify("traffic_signal_horizontal")["population_role"] == "generic_fixture"
    assert classify("too_small_discard")["population_role"] == "discard"

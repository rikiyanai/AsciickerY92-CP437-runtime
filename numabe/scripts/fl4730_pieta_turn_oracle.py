#!/usr/bin/env python3
"""Author and verify the Pieta camera-turn exact-glyph oracle.

The oracle is authored from one prior headed run, then supplied before a fresh
verification run. Expected GlyphIds are recomputed independently from the JSON
compiler artifacts through the FL-4678 Python predictor; runtime C++ decisions
and final presented tuples are observations, never expectation owners.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PREDICTOR_PATH = ROOT / "scripts/predict_fl4678_exact_cell_glyph.py"
GLYPH_FACT = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
ADDRESS_SPACE = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
GLYPH_PIN = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"
COMPLETE_SCHEMA = ROOT / "assets/glyphs/schema/fl4730.pieta_complete_oracle.v1.schema.json"
PIETA_MATERIAL_NAMESPACE = 2
PIETA_MATERIAL_KEY = 200
MOVEMENT_TIMEPOINT_LABELS = [
    "baseline_hold",
    "turn_right",
    "post_turn_settle",
    "walk_forward",
    "post_walk_settle",
]
STATIC_TIMEPOINT_LABELS = [f"tuple_hold_{index}" for index in range(5)]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def xterm_rgba(index: int) -> list[int]:
    """Mirror the product's 6x6x6 xterm colour-cube shader exactly."""
    if index < 16 or index > 231:
        raise ValueError(f"FL-4730 corpus colour outside product cube: {index}")
    cube = index - 16
    red = cube // 36
    cube -= red * 36
    green = cube // 6
    blue = cube - green * 6
    return [red * 51, green * 51, blue * 51, 255]


def source_revision(receipt: dict[str, Any]) -> str:
    manifest = receipt.get("web_build_manifest") or {}
    revision = receipt.get("revision") or {}
    value = manifest.get("source_ref") or manifest.get("git_head") or revision.get("head")
    if not isinstance(value, str) or len(value) < 7:
        raise ValueError("receipt has no source revision")
    return value


def complete_sources() -> dict[str, dict[str, str]]:
    paths = {
        "schema": COMPLETE_SCHEMA,
        "predictor": PREDICTOR_PATH,
        "glyph_fact": GLYPH_FACT,
        "address_space": ADDRESS_SPACE,
        "glyph_program_pin": GLYPH_PIN,
    }
    return {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
        for name, path in paths.items()
    }


def glyph_record_index() -> dict[int, dict[str, Any]]:
    artifact = load_json(GLYPH_FACT)
    return {int(row["glyph_record_id"]): row for row in artifact["glyph_records"]}


def load_predictor():
    spec = importlib.util.spec_from_file_location("fl4678_predictor", PREDICTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load independent FL-4678 predictor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row_object(snapshot: dict[str, Any], row: list[Any]) -> dict[str, Any]:
    fields = snapshot.get("row_fields", [])
    if len(fields) != len(row):
        raise ValueError("runtime prediction row/field length mismatch")
    return dict(zip(fields, row, strict=True))


def scene_row(runtime: dict[str, Any], name: str) -> dict[str, Any]:
    return {
        "name": name,
        "schema_version": 2,
        "valid": 1,
        "material_namespace": int(runtime["material_namespace"]),
        "material_key": int(runtime["material_key"]),
        "route_family_mask": int(runtime["route_family_mask"]),
        "ramp_bin": int(runtime["ramp_bin"]),
        "density_bin": int(runtime["density_bin"]),
        "direction_bin": int(runtime["direction_bin"]),
        "flow_bin": int(runtime["flow_bin"]),
        "edge_bin": int(runtime["edge_bin"]),
        "harri_shape6_valid": int(runtime.get("harri_shape6_valid", 0)),
        "harri_shape6_q0_16": [
            int(runtime.get(f"harri_shape6_q{axis}", 0)) for axis in range(6)
        ],
    }


def prediction_owner():
    predictor = load_predictor()
    artifact = load_json(GLYPH_FACT)
    pool = load_json(ADDRESS_SPACE)
    index = predictor.build_address_index(artifact, pool)
    admitted = {int(row["glyph_record_id"]) for row in artifact["glyph_records"]}
    return predictor, index, admitted


def expected_timepoints(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    predictor, index, admitted = prediction_owner()
    timepoints: list[dict[str, Any]] = []
    for sample in receipt.get("samples", []):
        snapshot = sample.get("runtime_prediction_snapshot") or {}
        if not snapshot.get("plane_valid") or snapshot.get("truncated"):
            raise ValueError(f"invalid runtime prediction snapshot at sample {sample.get('index')}")
        rows = []
        for raw in snapshot.get("rows", []):
            runtime = row_object(snapshot, raw)
            name = f"{sample.get('leg')}:{runtime['x']}:{runtime['y']}"
            predicted = predictor.predict_row(scene_row(runtime, name), index, admitted)
            if predicted.get("outcome") != "selected":
                raise ValueError(f"oracle row did not select: {name}: {predicted}")
            rows.append(
                {
                    "x": int(runtime["x"]),
                    "y": int(runtime["y"]),
                    "scene_fact": scene_row(runtime, name),
                    "role": int(runtime["role"]),
                    "feature_ref": str(runtime["feature_ref"]),
                    "expected_glyph_id": int(predicted["winning_glyph_record_id"]),
                    "runtime_exact_glyph_id": int(runtime["glyph_id"]),
                    "runtime_presented_glyph_id": int(runtime["presented_glyph_id"]),
                    "fg_palette_index": int(runtime["fg_palette_index"]),
                    "bg_palette_index": int(runtime["bg_palette_index"]),
                    "topology_event": (
                        int(runtime["topology_event"])
                        if "topology_event" in runtime else None
                    ),
                }
            )
        timepoints.append(
            {
                "sample_index": int(sample["index"]),
                "label": str(sample["leg"]),
                "camera_yaw": sample.get("runtime_state", {}).get("camera_yaw"),
                "bridge_frame": snapshot.get("bridge_frame"),
                "performance": sample.get("performance"),
                "rows": rows,
            }
        )
    return timepoints


def transition_rows(timepoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions = []
    for before, after in zip(timepoints, timepoints[1:]):
        prior = {(r["x"], r["y"]): r for r in before["rows"]}
        rows = []
        for current in after["rows"]:
            key = (current["x"], current["y"])
            old = prior.get(key)
            if old is None:
                continue
            predicted_changed = old["expected_glyph_id"] != current["expected_glyph_id"]
            if not predicted_changed:
                continue
            rows.append(
                {
                    "x": key[0],
                    "y": key[1],
                    "expected_before_glyph_id": old["expected_glyph_id"],
                    "expected_after_glyph_id": current["expected_glyph_id"],
                }
            )
        transitions.append(
            {
                "from": before["label"],
                "to": after["label"],
                "predicted_changed_cell_count": len(rows),
                "changed_rows": rows,
            }
        )
    return transitions


def _selected_complete_timepoints(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    by_label = {point["label"]: point for point in expected_timepoints(receipt)}
    if all(label in by_label for label in STATIC_TIMEPOINT_LABELS):
        labels = STATIC_TIMEPOINT_LABELS
    else:
        labels = MOVEMENT_TIMEPOINT_LABELS
    missing = [label for label in labels if label not in by_label]
    if missing:
        raise ValueError(f"complete Pieta corpus missing timepoints: {missing}")
    selected = [by_label[label] for label in labels]
    for point in selected:
        point["rows"] = [
            row for row in point["rows"]
            if row["scene_fact"]["material_namespace"] == PIETA_MATERIAL_NAMESPACE
            and row["scene_fact"]["material_key"] == PIETA_MATERIAL_KEY
        ]
        if not point["rows"]:
            raise ValueError(f"no Pieta rows at timepoint {point['label']}")
    return selected


def _persistent_feature_refs(timepoints: list[dict[str, Any]]) -> list[str]:
    ref_sets = [{row["feature_ref"] for row in point["rows"]} for point in timepoints]
    persistent = sorted(set.intersection(*ref_sets), key=int)
    if not persistent:
        raise ValueError("Pieta corpus has no FeatureRef present at every timepoint")
    return persistent


def _expected_structure_membership(record: dict[str, Any]) -> list[str]:
    prefix = f"mesh:{PIETA_MATERIAL_KEY}:"
    memberships = sorted(
        value for value in record.get("profile_memberships", [])
        if isinstance(value, str) and value.startswith(prefix)
    )
    if not memberships:
        raise ValueError(
            f"glyph {record.get('glyph_record_id')} has no Pieta mesh membership")
    return memberships


def _scene_fact_corpus_payload(timepoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame_timepoint": point["frame_timepoint"],
            "rows": [
                {
                    "cell_coordinate": row["cell_coordinate"],
                    "expected_feature_ref": row["expected_feature_ref"],
                    "scene_fact": row["scene_fact"],
                }
                for row in point["rows"]
            ],
        }
        for point in timepoints
    ]


def _complete_row_signature(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "cell_coordinate": {"x": row["x"], "y": row["y"]},
        "scene_fact": row["scene_fact"],
        "expected_glyph_id": row["expected_glyph_id"],
        "expected_palette_indices": {
            "foreground": row["fg_palette_index"],
            "background": row["bg_palette_index"],
        },
        "expected_role": row["role"],
        "expected_feature_ref": row["feature_ref"],
        "expected_topology_transition": row["topology_event"],
    }


def author_complete(
    receipt_path: Path, reference_receipt_paths: list[Path] | None = None
) -> dict[str, Any]:
    """Freeze independently reviewed expectations before a successor run.

    Two prior headed receipts select only FeatureRefs whose complete expected
    rows agree across all five user-reachable timepoints. The emitted artifact
    then owns those values; verification reads a third receipt and may not
    rewrite them.
    """
    reference_receipt_paths = reference_receipt_paths or []
    if not reference_receipt_paths:
        raise ValueError("complete oracle authoring requires a second reference receipt")
    receipt_paths = [receipt_path, *reference_receipt_paths]
    receipts = [load_json(path) for path in receipt_paths]
    revisions = {source_revision(value) for value in receipts}
    if len(revisions) != 1:
        raise ValueError(f"authoring receipts disagree on source revision: {sorted(revisions)}")
    selected_sets = [_selected_complete_timepoints(value) for value in receipts]
    label_sets = [[point["label"] for point in points] for points in selected_sets]
    if any(labels != label_sets[0] for labels in label_sets[1:]):
        raise ValueError(f"authoring receipt choreography mismatch: {label_sets}")
    persistent_sets = [set(_persistent_feature_refs(points)) for points in selected_sets]
    persistent_refs = sorted(set.intersection(*persistent_sets), key=int)
    if not persistent_refs:
        raise ValueError("authoring receipts have no shared persistent Pieta FeatureRef")
    timepoints = selected_sets[0]
    records = glyph_record_index()
    authored_timepoints: list[dict[str, Any]] = []
    join_keys: set[str] = set()
    for ordinal, point in enumerate(timepoints):
        rows_by_ref: dict[str, list[dict[str, Any]]] = {}
        for row in point["rows"]:
            rows_by_ref.setdefault(row["feature_ref"], []).append(row)
        authored_rows = []
        for feature_ref in persistent_refs:
            candidates = rows_by_ref.get(feature_ref, [])
            if len(candidates) != 1:
                raise ValueError(
                    f"{point['label']} FeatureRef {feature_ref} has {len(candidates)} rows")
            row = candidates[0]
            primary_signature = _complete_row_signature(row)
            for reference_points in selected_sets[1:]:
                reference_point = next(
                    value for value in reference_points if value["label"] == point["label"])
                reference_rows = [
                    value for value in reference_point["rows"]
                    if value["feature_ref"] == feature_ref
                ]
                if len(reference_rows) != 1:
                    raise ValueError(
                        f"reference {point['label']} FeatureRef {feature_ref} "
                        f"has {len(reference_rows)} rows")
                if _complete_row_signature(reference_rows[0]) != primary_signature:
                    raise ValueError(
                        f"reference expectation drift at {point['label']} FeatureRef {feature_ref}")
            if row["role"] != 1:
                raise ValueError("complete Pieta corpus currently owns tonal-fill rows only")
            if row["scene_fact"]["route_family_mask"] != 3:
                raise ValueError("Pieta tonal row lacks the ramp+density route pair")
            if row["topology_event"] not in (None, 0):
                raise ValueError("stable Pieta authoring corpus observed a topology event")
            glyph_id = int(row["expected_glyph_id"])
            record = records.get(glyph_id)
            if record is None:
                raise ValueError(f"missing GlyphFact record {glyph_id}")
            identity = record.get("text_identity") or {}
            if identity.get("kind") != "unicode_scalar":
                raise ValueError(f"glyph {glyph_id} lacks Unicode scalar identity")
            join_key = f"pieta:{point['label']}:{feature_ref}"
            if join_key in join_keys:
                raise ValueError(f"duplicate runtime join key: {join_key}")
            join_keys.add(join_key)
            fg = int(row["fg_palette_index"])
            bg = int(row["bg_palette_index"])
            authored_rows.append(
                {
                    "cell_coordinate": {"x": int(row["x"]), "y": int(row["y"])},
                    "runtime_join_key": join_key,
                    "scene_fact": row["scene_fact"],
                    "expected_glyph_id": glyph_id,
                    "expected_unicode_scalar": int(identity["unicode_scalar"]),
                    "expected_palette_indices": {"foreground": fg, "background": bg},
                    "expected_colour_rgba": {
                        "foreground": xterm_rgba(fg),
                        "background": xterm_rgba(bg),
                    },
                    "expected_role": {"code": 1, "name": "tonal_fill"},
                    "expected_structure_membership": _expected_structure_membership(record),
                    "expected_feature_ref": feature_ref,
                    "expected_structural_sequence": {
                        "kind": "declared_absent",
                        "reason": "tonal_fill_is_not_a_structural_sequence_participant",
                    },
                    "expected_topology_transition": {"code": 0, "name": "none"},
                }
            )
        authored_timepoints.append(
            {
                "frame_timepoint": {"ordinal": ordinal, "label": point["label"]},
                "authoring_scene_frame": point["bridge_frame"],
                "rows": authored_rows,
            }
        )
    scene_fact_payload = _scene_fact_corpus_payload(authored_timepoints)
    sources = complete_sources()
    return {
        "schema": "fl4730.pieta_complete_oracle.v1",
        "oracle_artifact_id": "fl4730://pieta/persistent-feature-corpus/v1",
        "oracle_schema_version": 1,
        "oracle_source_revision": next(iter(revisions)),
        "predicted_before_verification_run": True,
        "status_scope": (
            "all persistent Pieta FeatureRefs across five explicit headed timepoints"
        ),
        "sources": sources,
        "compiled_artifact_hashes": {
            name: sources[name]["sha256"]
            for name in ("glyph_fact", "address_space", "glyph_program_pin")
        },
        "authoring_receipts": [
            {"path": str(path), "sha256": sha256(path)} for path in receipt_paths
        ],
        "pieta_scene_fact_hash": canonical_sha256(scene_fact_payload),
        "corpus_definition": {
            "material_namespace": PIETA_MATERIAL_NAMESPACE,
            "material_key": PIETA_MATERIAL_KEY,
            "timepoint_labels": label_sets[0],
            "persistent_feature_refs": persistent_refs,
            "authoring_receipt_count": len(receipt_paths),
            "expected_timepoint_count": len(authored_timepoints),
            "expected_row_count": sum(len(point["rows"]) for point in authored_timepoints),
        },
        "timepoints": authored_timepoints,
    }


def validate_complete_schema(oracle: dict[str, Any]) -> list[str]:
    schema = load_json(COMPLETE_SCHEMA)
    errors = sorted(Draft202012Validator(schema).iter_errors(oracle), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, error.path))}: {error.message}" for error in errors]


def verify_complete(oracle_path: Path, receipt_path: Path) -> dict[str, Any]:
    oracle = load_json(oracle_path)
    receipt = load_json(receipt_path)
    schema_errors = validate_complete_schema(oracle)
    sources_match = oracle.get("sources") == complete_sources()
    revision_match = oracle.get("oracle_source_revision") == source_revision(receipt)
    scene_fact_hash_match = oracle.get("pieta_scene_fact_hash") == canonical_sha256(
        _scene_fact_corpus_payload(oracle.get("timepoints", [])))
    actual_points = {
        point["label"]: point for point in _selected_complete_timepoints(receipt)
    }
    records = glyph_record_index()
    predictor, index, admitted = prediction_owner()
    row_total = 0
    matched_rows = 0
    deterministic_rows = 0
    matched_join_keys: set[str] = set()
    expected_join_keys: set[str] = set()
    mismatches: list[str] = []
    for expected_point in oracle.get("timepoints", []):
        timepoint = expected_point.get("frame_timepoint") or {}
        label = str(timepoint.get("label"))
        actual_point = actual_points.get(label)
        actual_by_ref: dict[str, list[dict[str, Any]]] = {}
        if actual_point:
            for row in actual_point["rows"]:
                actual_by_ref.setdefault(row["feature_ref"], []).append(row)
        for expected in expected_point.get("rows", []):
            row_total += 1
            join_key = str(expected.get("runtime_join_key"))
            if join_key in expected_join_keys:
                mismatches.append(f"duplicate expected join key {join_key}")
            expected_join_keys.add(join_key)
            feature_ref = str(expected.get("expected_feature_ref"))
            candidates = actual_by_ref.get(feature_ref, [])
            if len(candidates) != 1:
                mismatches.append(
                    f"{join_key}: expected one runtime row, observed {len(candidates)}")
                continue
            actual = candidates[0]
            predicted_a = predictor.predict_row(expected["scene_fact"], index, admitted)
            predicted_b = predictor.predict_row(expected["scene_fact"], index, admitted)
            expected_glyph = int(expected["expected_glyph_id"])
            deterministic = predicted_a == predicted_b and (
                predicted_a.get("outcome") == "selected"
                and int(predicted_a.get("winning_glyph_record_id", -1)) == expected_glyph
            )
            if deterministic:
                deterministic_rows += 1
            record = records.get(expected_glyph) or {}
            identity = record.get("text_identity") or {}
            expected_palette = expected["expected_palette_indices"]
            expected_rgba = expected["expected_colour_rgba"]
            checks = {
                "cell_coordinate": expected["cell_coordinate"] == {
                    "x": actual["x"], "y": actual["y"]},
                "scene_fact": expected["scene_fact"] == actual["scene_fact"],
                "glyph": actual["runtime_exact_glyph_id"] == expected_glyph,
                "presented_glyph": actual["runtime_presented_glyph_id"] == expected_glyph,
                "unicode_scalar": (
                    identity.get("kind") == "unicode_scalar"
                    and int(identity.get("unicode_scalar", -1))
                    == int(expected["expected_unicode_scalar"])
                ),
                "palette": (
                    actual["fg_palette_index"] == expected_palette["foreground"]
                    and actual["bg_palette_index"] == expected_palette["background"]
                ),
                "rgba": (
                    expected_rgba["foreground"]
                    == xterm_rgba(int(expected_palette["foreground"]))
                    and expected_rgba["background"]
                    == xterm_rgba(int(expected_palette["background"]))
                ),
                "role": actual["role"] == expected["expected_role"]["code"],
                "structure": set(expected["expected_structure_membership"]).issubset(
                    set(record.get("profile_memberships", []))),
                "feature_ref": actual["feature_ref"] == feature_ref,
                "sequence": (
                    expected["expected_structural_sequence"]["kind"] == "declared_absent"
                    and actual["role"] == 1
                ),
                "topology": (
                    actual["topology_event"]
                    == expected["expected_topology_transition"]["code"]
                ),
                "deterministic": deterministic,
            }
            failed = [name for name, passed in checks.items() if not passed]
            if failed:
                mismatches.append(f"{join_key}: {','.join(failed)}")
                continue
            matched_rows += 1
            matched_join_keys.add(join_key)
    corpus = oracle.get("corpus_definition") or {}
    corpus_complete = (
        row_total == int(corpus.get("expected_row_count", -1))
        and len(oracle.get("timepoints", [])) == int(corpus.get("expected_timepoint_count", -1))
        and matched_rows == row_total and row_total > 0
    )
    gates = {
        "fl4730_oracle_schema_valid": (
            not schema_errors and sources_match and revision_match and scene_fact_hash_match
        ),
        "fl4730_pieta_corpus_complete": corpus_complete,
        "fl4730_prediction_deterministic": deterministic_rows == row_total and row_total > 0,
        "fl4730_runtime_join_keys_complete": (
            len(expected_join_keys) == row_total
            and matched_join_keys == expected_join_keys
        ),
    }
    return {
        "schema": "fl4730.pieta_complete_oracle_verification.v1",
        "oracle": str(oracle_path),
        "oracle_sha256": sha256(oracle_path),
        "receipt": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "oracle_source_revision": oracle.get("oracle_source_revision"),
        "runtime_source_revision": source_revision(receipt),
        "predicted_before_verification_run": bool(
            oracle.get("predicted_before_verification_run")),
        "compiler_sources_match_frozen_oracle": sources_match,
        "pieta_scene_fact_hash_match": scene_fact_hash_match,
        "schema_errors": schema_errors,
        "expected_row_count": row_total,
        "matched_complete_row_count": matched_rows,
        "deterministic_prediction_row_count": deterministic_rows,
        "runtime_join_key_count": len(matched_join_keys),
        "mismatches": mismatches[:20],
        "gates": gates,
        "passed": bool(oracle.get("predicted_before_verification_run")) and all(gates.values()),
    }


def author(receipt_path: Path) -> dict[str, Any]:
    receipt = load_json(receipt_path)
    timepoints = expected_timepoints(receipt)
    selector_mismatches = sum(
        row["runtime_exact_glyph_id"] != row["expected_glyph_id"]
        for point in timepoints for row in point["rows"]
    )
    presentation_mismatches = sum(
        row["runtime_presented_glyph_id"] != row["expected_glyph_id"]
        for point in timepoints for row in point["rows"]
    )
    if selector_mismatches or presentation_mismatches:
        raise ValueError(
            f"authoring run disagrees with independent prediction: "
            f"selector={selector_mismatches} presentation={presentation_mismatches}"
        )
    return {
        "schema": "fl4730.pieta_turn_exact_glyph_oracle.v1",
        "status_scope": "exact-glyph camera-turn oracle; colour/structure/topology corpus remains open",
        "predicted_before_verification_run": True,
        "sources": {
            "predictor": {
                "path": str(PREDICTOR_PATH.relative_to(ROOT)), "sha256": sha256(PREDICTOR_PATH)
            },
            "glyph_fact": {"path": str(GLYPH_FACT.relative_to(ROOT)), "sha256": sha256(GLYPH_FACT)},
            "address_space": {"path": str(ADDRESS_SPACE.relative_to(ROOT)), "sha256": sha256(ADDRESS_SPACE)},
            "glyph_program_pin": {"path": str(GLYPH_PIN.relative_to(ROOT)), "sha256": sha256(GLYPH_PIN)},
            "authoring_receipt": str(receipt_path),
            "authoring_receipt_sha256": sha256(receipt_path),
        },
        "choreography": [point["label"] for point in timepoints],
        "timepoints": timepoints,
        "transitions": transition_rows(timepoints),
    }


def freeze() -> dict[str, Any]:
    """Freeze the independent prediction function before the headed run.

    Camera and movement input intentionally produce live state, so a legitimate
    oracle cannot assume post-input SceneFacts in advance. It can freeze the
    complete deterministic function that maps every possible observed fact to
    one exact GlyphId, then evaluate that function independently for every cell
    in the later receipt.
    """
    return {
        "schema": "fl4730.universal_exact_glyph_oracle.v1",
        "status_scope": "all exact-glyph cells in one later movement/camera run",
        "predicted_before_verification_run": True,
        "prediction_rule": "FL-4678 independent Python predictor over frozen compiler artifacts",
        "sources": {
            "predictor": {
                "path": str(PREDICTOR_PATH.relative_to(ROOT)), "sha256": sha256(PREDICTOR_PATH)
            },
            "glyph_fact": {"path": str(GLYPH_FACT.relative_to(ROOT)), "sha256": sha256(GLYPH_FACT)},
            "address_space": {
                "path": str(ADDRESS_SPACE.relative_to(ROOT)), "sha256": sha256(ADDRESS_SPACE)
            },
            "glyph_program_pin": {
                "path": str(GLYPH_PIN.relative_to(ROOT)), "sha256": sha256(GLYPH_PIN)
            },
        },
    }


def verify_universal(oracle_path: Path, receipt_path: Path) -> dict[str, Any]:
    oracle = load_json(oracle_path)
    receipt = load_json(receipt_path)
    expected_sources = freeze()["sources"]
    sources_match = oracle.get("sources") == expected_sources
    timepoints = expected_timepoints(receipt)
    row_total = sum(len(point["rows"]) for point in timepoints)
    exact_matches = sum(
        row["runtime_exact_glyph_id"] == row["expected_glyph_id"]
        for point in timepoints for row in point["rows"]
    )
    presented_matches = sum(
        row["runtime_presented_glyph_id"] == row["expected_glyph_id"]
        for point in timepoints for row in point["rows"]
    )
    transitions = transition_rows(timepoints)
    predicted_changed = sum(t["predicted_changed_cell_count"] for t in transitions)
    observed_changed = 0
    for before, after in zip(timepoints, timepoints[1:]):
        prior = {(r["x"], r["y"]): r for r in before["rows"]}
        for current in after["rows"]:
            old = prior.get((current["x"], current["y"]))
            if (old and old["expected_glyph_id"] != current["expected_glyph_id"] and
                    old["runtime_presented_glyph_id"] != current["runtime_presented_glyph_id"]):
                observed_changed += 1
    passed = (
        sources_match and bool(oracle.get("predicted_before_verification_run"))
        and row_total > 0 and exact_matches == row_total
        and presented_matches == row_total and predicted_changed > 0
        and observed_changed == predicted_changed
    )
    return {
        "schema": "fl4730.universal_exact_glyph_verification.v1",
        "oracle": str(oracle_path),
        "oracle_sha256": sha256(oracle_path),
        "receipt": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "predicted_before_verification_run": bool(
            oracle.get("predicted_before_verification_run")),
        "compiler_sources_match_frozen_oracle": sources_match,
        "expected_row_count": row_total,
        "exact_glyph_matches": exact_matches,
        "presented_glyph_matches": presented_matches,
        "predicted_changed_cell_count": predicted_changed,
        "observed_predicted_changed_cell_count": observed_changed,
        "transitions": transitions,
        "passed": passed,
    }


def verify_fixed_checkpoint(oracle_path: Path, checkpoint_path: Path) -> dict[str, Any]:
    """Verify the no-input Pieta checkpoint before the live turn continues.

    The browser driver writes this checkpoint and then blocks on stdin. That
    makes the verification a gate inside one continuous product session rather
    than a later interpretation of an already-completed camera run.
    """
    oracle = load_json(oracle_path)
    checkpoint = load_json(checkpoint_path)
    if oracle.get("schema") != "fl4730.universal_exact_glyph_oracle.v1":
        raise ValueError("fixed checkpoint requires the frozen universal oracle")
    sample = checkpoint.get("sample")
    if not isinstance(sample, dict):
        raise ValueError("fixed checkpoint has no sample")
    timepoints = expected_timepoints({"samples": [sample]})
    if len(timepoints) != 1 or timepoints[0]["label"] != "authored_view_hold":
        raise ValueError("fixed checkpoint is not the authored_view_hold sample")
    pieta_rows = [
        row for row in timepoints[0]["rows"]
        if row["scene_fact"]["material_namespace"] == PIETA_MATERIAL_NAMESPACE
        and row["scene_fact"]["material_key"] == PIETA_MATERIAL_KEY
    ]
    exact_matches = sum(
        row["runtime_exact_glyph_id"] == row["expected_glyph_id"]
        for row in pieta_rows
    )
    presented_matches = sum(
        row["runtime_presented_glyph_id"] == row["expected_glyph_id"]
        for row in pieta_rows
    )
    exact_to_final_transitions = sum(
        row["runtime_presented_glyph_id"] != row["runtime_exact_glyph_id"]
        for row in timepoints[0]["rows"]
    )
    feature_refs = {row["feature_ref"] for row in pieta_rows if row["feature_ref"]}
    snapshot = sample.get("runtime_prediction_snapshot") or {}
    tuple_capture = sample.get("full_tuple_capture") or {}
    structural_ref = sample.get("structural_turn_snapshot") or {}
    structural_errors: list[str] = []
    structural_state: dict[str, Any] | None = None
    structural_path: Path | None = None
    structural_hash_matches = False
    structural_summary_matches = False
    raw_structural_path = structural_ref.get("path")
    if not isinstance(raw_structural_path, str) or not raw_structural_path:
        structural_errors.append("fixed checkpoint has no full structural snapshot path")
    else:
        structural_path = Path(raw_structural_path)
        if not structural_path.is_absolute():
            structural_path = ROOT / structural_path
        if not structural_path.is_file():
            structural_errors.append(
                f"fixed checkpoint structural snapshot is missing: {structural_path}"
            )
        else:
            expected_structural_hash = structural_ref.get("sha256")
            actual_structural_hash = sha256(structural_path)
            structural_hash_matches = (
                isinstance(expected_structural_hash, str)
                and expected_structural_hash == actual_structural_hash
            )
            if not structural_hash_matches:
                structural_errors.append(
                    "fixed checkpoint structural snapshot hash disagrees"
                )
            try:
                structural_frame = load_json(structural_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                structural_errors.append(
                    f"fixed checkpoint structural snapshot is unreadable: {exc}"
                )
            else:
                structural_state = _frame_state(structural_frame, structural_errors)
                structural_frame_id = structural_state["frame_id"]
                checkpoint_frame = _integer(snapshot.get("bridge_frame"))
                tuple_frame = _integer(tuple_capture.get("scene_frame"))
                summary_frame = _integer(structural_ref.get("bridge_frame"))
                summary_counts = {
                    "claim_count": len(structural_frame.get("claims") or []),
                    "structural_cell_count": len(
                        structural_frame.get("structural_cells") or []
                    ),
                    "contributor_node_count": len(
                        structural_frame.get("node_contributors") or []
                    ),
                    "event_count": len(structural_frame.get("events") or []),
                }
                structural_summary_matches = (
                    structural_ref.get("carrier_valid")
                    is structural_frame.get("carrier_valid")
                    and all(
                        _integer(structural_ref.get(field)) == count
                        for field, count in summary_counts.items()
                    )
                    and structural_frame_id == checkpoint_frame
                    and structural_frame_id == tuple_frame
                    and structural_frame_id == summary_frame
                )
                if not structural_summary_matches:
                    structural_errors.append(
                        "fixed checkpoint structural summary or frame identity disagrees"
                    )
    structural_claim_total = (
        structural_state["claim_total"] if structural_state is not None else 0
    )
    structural_contributor_total = (
        structural_state["contributor_total"] if structural_state is not None else 0
    )
    gates = {
        "predicted_before_live_run": bool(oracle.get("predicted_before_verification_run")),
        "compiler_sources_match_frozen_oracle": oracle.get("sources") == freeze()["sources"],
        "authored_view_named": timepoints[0]["label"] == "authored_view_hold",
        "pieta_named_cells_present": bool(pieta_rows),
        "pieta_feature_refs_present": bool(feature_refs),
        "scene_fact_to_exact_prediction_join_complete": exact_matches == len(pieta_rows),
        # The offline exact selector must match its frozen prediction. The
        # authored structural/temporal stages may then choose a different final
        # glyph; the full carrier below proves that transition cell by cell.
        "runtime_exact_to_final_transition_census_matches":
            _integer(snapshot.get("exact_to_final_transition_count"))
            == exact_to_final_transitions,
        "complete_tuple_plane_gpu_byte_identical":
            bool(tuple_capture.get("gpu_readback_byte_identical")),
        "full_structural_snapshot_hash_matches": structural_hash_matches,
        "full_structural_snapshot_summary_and_frame_match": structural_summary_matches,
        "full_structural_carrier_and_joins_valid": not structural_errors,
        "structural_claims_present": structural_claim_total > 0,
        "structural_contributors_present": structural_contributor_total > 0,
        "selected_claims_have_contributors":
            structural_claim_total == structural_contributor_total,
    }
    return {
        "schema": "fl4731.fixed_pose_checkpoint_verification.v1",
        "oracle": str(oracle_path),
        "oracle_sha256": sha256(oracle_path),
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256(checkpoint_path),
        "label": timepoints[0]["label"],
        "bridge_frame": snapshot.get("bridge_frame"),
        "tuple_plane_bridge_frame": tuple_capture.get("scene_frame"),
        "expected_row_count": len(pieta_rows),
        "exact_glyph_matches": exact_matches,
        "presented_glyph_matches": presented_matches,
        "exact_to_final_transition_count": exact_to_final_transitions,
        "persistent_feature_refs": sorted(feature_refs),
        "structural_snapshot": (
            str(structural_path) if structural_path is not None else None
        ),
        "structural_snapshot_sha256": (
            sha256(structural_path)
            if structural_path is not None and structural_path.is_file()
            else None
        ),
        "structural_claim_count": structural_claim_total,
        "structural_contributor_count": structural_contributor_total,
        "structural_errors": structural_errors,
        "gates": gates,
        "passed": all(gates.values()),
    }


def _feature_ref_key(ref: dict[str, Any]) -> str:
    # Match Fl4512FeatureRefEqualV1: producer provenance is evidence, not
    # semantic identity. Historical flat mesh fields have the same exclusion.
    return json.dumps({field: ref.get(field) for field in (
        "surface", "semantic_chain_id", "material_key", "dispatch_surface",
        "role", "feature_kind", "aspect_region_id",
    )}, sort_keys=True, separators=(",", ":"))


def _identity_number(value: Any) -> int:
    token = _id_token(value, allow_zero=True)
    return -1 if token is None else int(token, 16)


def _feature_ref_sort_key(ref: dict[str, Any]) -> tuple[Any, ...]:
    surface = ref.get("surface") or {}
    return (
        _identity_number(ref.get("world_generation")),
        _identity_number(surface.get("world_generation")),
        _integer(surface.get("owner_kind")) or -1,
        _integer(surface.get("owner_x")) or 0,
        _integer(surface.get("owner_y")) or 0,
        _integer(surface.get("anchor_u_q")) or 0,
        _integer(surface.get("anchor_v_q")) or 0,
        _identity_number(ref.get("semantic_chain_id")),
        _integer(ref.get("material_key")) or 0,
        _integer(ref.get("dispatch_surface")) or -1,
        _integer(ref.get("role")) or -1,
        _integer(ref.get("feature_kind")) or -1,
        _integer(ref.get("aspect_region_id")) or 0,
    )


_SNAKE_EVENT_NAMES = {
    0: "none",
    1: "birth",
    2: "continue",
    3: "trim",
    4: "extend",
    5: "split_retained",
    6: "split_branch",
    7: "merge_retained",
    8: "retire",
}
_TOPOLOGY_SPLIT = 5
_TOPOLOGY_MERGE = 6
_RETIRED_EVENT = 8


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str):
            try:
                return int(value, 0)
            except ValueError:
                return int(value, 10)
        if isinstance(value, float) and not value.is_integer():
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _id_token(value: Any, *, allow_zero: bool = False) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 16 and all(character in "0123456789abcdefABCDEF" for character in text):
            number = int(text, 16)
        else:
            number = _integer(value)
    else:
        number = _integer(value)
    if number is None or number < 0 or (number == 0 and not allow_zero):
        return None
    return f"{number:016x}"


def _complete_feature_ref(ref: Any) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if not isinstance(ref, dict):
        return None, ["FeatureRef is not an object"]
    required = (
        "world_generation", "surface", "material_key",
        "dispatch_surface", "role", "feature_kind",
        "aspect_region_id", "semantic_chain_id",
    )
    missing = [name for name in required if name not in ref]
    if missing:
        errors.append(f"FeatureRef missing fields {missing}")
    surface = ref.get("surface")
    surface_fields = (
        "world_generation", "owner_kind", "owner_x", "owner_y", "anchor_u_q", "anchor_v_q"
    )
    if not isinstance(surface, dict):
        errors.append("FeatureRef surface is not an object")
    else:
        missing_surface = [name for name in surface_fields if name not in surface]
        if missing_surface:
            errors.append(f"FeatureRef surface missing fields {missing_surface}")
    def validate_number(value: Any, field: str, *, signed: bool = False, identity: bool = False) -> None:
        if identity:
            token = _id_token(value, allow_zero=False)
            if token is None:
                errors.append(f"FeatureRef {field} is not a nonzero integer identity")
            return
        number = _integer(value)
        if number is None or (not signed and number < 0):
            errors.append(f"FeatureRef {field} is not a valid {'signed' if signed else 'non-negative'} integer")

    if not missing:
        validate_number(ref.get("world_generation"), "world_generation", identity=True)
        validate_number(ref.get("material_key"), "material_key", signed=True)
        for field in ("dispatch_surface", "role", "feature_kind", "aspect_region_id"):
            validate_number(ref.get(field), field)
    provenance = ref.get("provenance")
    if "provenance" in ref:
        if not isinstance(provenance, dict):
            errors.append("FeatureRef provenance is not an object")
        else:
            producer = _integer(provenance.get("producer_kind"))
            if producer not in (1, 2):
                errors.append("FeatureRef provenance producer_kind is unknown")
            for field in ("mesh_instance_uid", "depth_owner"):
                token = _id_token(provenance.get(field), allow_zero=producer == 2)
                if token is None or int(token, 16) > 0xffffffffffffffff:
                    errors.append(f"FeatureRef provenance {field} is invalid")
                if field in ref and _id_token(ref[field], allow_zero=True) != token:
                    errors.append(f"FeatureRef conflicting legacy {field} provenance")
    else:
        # Historical recorder schema. Do not manufacture missing provenance.
        for field in ("mesh_instance_uid", "depth_owner"):
            validate_number(ref.get(field), field, identity=True)
    if isinstance(surface, dict) and not missing_surface:
        validate_number(surface.get("world_generation"), "surface.world_generation", identity=True)
        validate_number(surface.get("owner_kind"), "surface.owner_kind")
        validate_number(surface.get("owner_x"), "surface.owner_x", signed=True)
        validate_number(surface.get("owner_y"), "surface.owner_y", signed=True)
        validate_number(surface.get("anchor_u_q"), "surface.anchor_u_q")
        validate_number(surface.get("anchor_v_q"), "surface.anchor_v_q")
        if _id_token(ref.get("world_generation"), allow_zero=True) != _id_token(
            surface.get("world_generation"), allow_zero=True
        ):
            errors.append("FeatureRef world_generation disagrees with surface.world_generation")
    chain_id = _id_token(ref.get("semantic_chain_id"))
    if chain_id is None:
        errors.append("FeatureRef semantic_chain_id is missing or zero")
    if errors:
        return None, errors
    return _feature_ref_key(ref), []


def _endpoint_pair(row: dict[str, Any], context: str) -> tuple[tuple[int, ...] | None, list[str]]:
    value = row.get("object_local_endpoints_q24_8")
    if not isinstance(value, list) or len(value) != 6:
        return None, [f"{context}: missing object_local_endpoints_q24_8"]
    numbers = [_integer(item) for item in value]
    if any(item is None for item in numbers):
        return None, [f"{context}: object endpoints are not Q24.8 integers"]
    endpoints = tuple(int(item) for item in numbers if item is not None)
    first, second = endpoints[:3], endpoints[3:]
    if first == second:
        return None, [f"{context}: zero-length Q24.8 segment"]
    return endpoints, []


def _child_surface_key(row: dict[str, Any], context: str) -> tuple[Any, ...] | None | list[str]:
    """Validate the optional child surface carried by full claim rows.

    The compact snake carrier intentionally omits child_surface because its
    claim/contributor hashes are the bounded census.  Sampled full snapshots
    carry it, and their claim-to-contributor join must retain every numeric
    owner fact rather than silently dropping this part of the key.
    """
    if "child_surface" not in row:
        return None
    value = row.get("child_surface")
    if not isinstance(value, dict):
        return [f"{context}: child_surface is not an object"]
    required = (
        "world_generation", "owner_kind", "owner_x", "owner_y",
        "anchor_u_q", "anchor_v_q",
    )
    missing = [field for field in required if field not in value]
    if missing:
        return [f"{context}: child_surface missing fields {missing}"]
    world = _id_token(value.get("world_generation"), allow_zero=False)
    owner_kind = _integer(value.get("owner_kind"))
    owner_x = _integer(value.get("owner_x"))
    owner_y = _integer(value.get("owner_y"))
    anchor_u = _integer(value.get("anchor_u_q"))
    anchor_v = _integer(value.get("anchor_v_q"))
    if world is None:
        return [f"{context}: child_surface.world_generation is not a nonzero integer identity"]
    if owner_kind is None or owner_kind < 0:
        return [f"{context}: child_surface.owner_kind is not non-negative integer"]
    if owner_x is None or owner_y is None:
        return [f"{context}: child_surface owner coordinates are not signed integers"]
    if anchor_u is None or anchor_u < 0 or anchor_v is None or anchor_v < 0:
        return [f"{context}: child_surface anchors are not non-negative integers"]
    return (
        int(world, 16), owner_kind, owner_x, owner_y, anchor_u, anchor_v,
    )


def _validate_transform_facts(value: Any, context: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{context}: glyph_transform_facts is not an object"]
    errors: list[str] = []
    if "feature_ref_complete" in value and value.get("feature_ref_complete") is not True:
        errors.append(f"{context}: glyph transform reports incomplete FeatureRef")
    for field in (
        "aspect_address", "visible_support", "expected_support", "pose_relation",
    ):
        if field in value and (_integer(value.get(field)) is None or _integer(value.get(field)) < 0):
            errors.append(f"{context}: glyph transform {field} is malformed")
    for field in ("projected_tangent_present", "observed_projected_offset_present"):
        if field in value and not isinstance(value.get(field), bool):
            errors.append(f"{context}: glyph transform {field} is not boolean")
    for field in ("projected_tangent_q1_15", "prior_projected_anchor_q16_16", "observed_projected_offset_q16_16"):
        if field not in value:
            continue
        vector = value.get(field)
        if not isinstance(vector, list) or len(vector) != 2 or any(_integer(item) is None for item in vector):
            errors.append(f"{context}: glyph transform {field} is malformed")
    return errors


def _segment_record(row: Any, context: str, cell_index: int | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return None, [f"{context}: contributor is not an object"]
    ref_key, ref_errors = _complete_feature_ref(row.get("feature_ref"))
    errors.extend(f"{context}: {error}" for error in ref_errors)
    segment_id = _id_token(row.get("semantic_segment_id"))
    if segment_id is None:
        errors.append(f"{context}: missing or zero semantic_segment_id")
    ordinal_value = row.get("ordinal", row.get("child_local_feature_id"))
    ordinal = _integer(ordinal_value)
    if ordinal is None or ordinal < 0:
        errors.append(f"{context}: missing non-negative segment ordinal")
    local_id = _integer(row.get("child_local_feature_id"))
    if local_id is None or local_id < 0:
        errors.append(f"{context}: missing child_local_feature_id")
    elif ordinal is not None and local_id != ordinal:
        errors.append(f"{context}: ordinal disagrees with child_local_feature_id")
    primitive_id = _integer(row.get("child_primitive_id"))
    if primitive_id is None or primitive_id < 0:
        errors.append(f"{context}: missing child_primitive_id")
    endpoints, endpoint_errors = _endpoint_pair(row, context)
    errors.extend(endpoint_errors)
    projected = row.get("projected_endpoints_q24_8")
    if projected is not None:
        if not isinstance(projected, list) or len(projected) != 4 or any(
            _integer(item) is None for item in projected
        ):
            errors.append(f"{context}: projected_endpoints_q24_8 is malformed")
    for field in (
        "chain_phase0_q16", "chain_phase1_q16",
        "semantic_chain_projected_extent_q16_16", "visibility_sheet",
        "path_closure_class",
    ):
        if field in row and (_integer(row.get(field)) is None or _integer(row.get(field)) < 0):
            errors.append(f"{context}: {field} is malformed")
    if "clipped_mask8" in row and _id_token(row.get("clipped_mask8"), allow_zero=True) is None:
        errors.append(f"{context}: clipped_mask8 is malformed")
    child_surface = _child_surface_key(row, context)
    if isinstance(child_surface, list):
        errors.extend(child_surface)
        child_surface = None
    transform_facts = row.get("glyph_transform_facts")
    if transform_facts is not None:
        errors.extend(_validate_transform_facts(transform_facts, context))
    if errors:
        return None, errors
    assert ref_key is not None and segment_id is not None and ordinal is not None
    chain_id = _id_token((row.get("feature_ref") or {}).get("semantic_chain_id"))
    assert chain_id is not None
    return {
        "feature_ref": row["feature_ref"],
        "feature_ref_key": ref_key,
        "chain_id": chain_id,
        "segment_id": segment_id,
        "ordinal": ordinal,
        "child_primitive_id": primitive_id,
        "child_surface": child_surface,
        "endpoints": endpoints,
        "projected_endpoints": tuple(_integer(item) for item in projected)
        if isinstance(projected, list) else None,
        "cell_index": cell_index,
        "row": row,
    }, []


def _segment_identity(segment: dict[str, Any]) -> tuple[str, int]:
    return segment["segment_id"], int(segment["ordinal"])


def _segment_signature(segment: dict[str, Any]) -> tuple[Any, ...]:
    return (
        segment["segment_id"], segment["ordinal"], segment["child_primitive_id"],
        segment["child_surface"],
        segment["endpoints"], segment["projected_endpoints"],
    )


def _ordered_segments(segments: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(segments.values(), key=lambda segment: (segment["ordinal"], segment["segment_id"]))


def _sequence_ids(sequence: list[dict[str, Any]]) -> list[str]:
    return [segment["segment_id"] for segment in sequence]


def _sequence_signature(sequence: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (segment["segment_id"], segment["ordinal"])
        for segment in sequence
    ]


def _claim_key(row: dict[str, Any], cell_index: int | None = None) -> tuple[Any, ...]:
    segment, errors = _segment_record(
        row, "claim", cell_index if cell_index is not None else row.get("cell_index")
    )
    if errors or segment is None:
        return ("invalid", json.dumps(row, sort_keys=True, separators=(",", ":")))
    claim_cell = _integer(row.get("cell_index") if cell_index is None else cell_index)
    return (
        segment["feature_ref_key"],
        -1 if claim_cell is None else claim_cell,
        segment["child_surface"], segment["child_primitive_id"],
        segment["segment_id"], segment["ordinal"],
        _integer(row.get("chain_phase0_q16")) if "chain_phase0_q16" in row else ("missing",),
        _integer(row.get("chain_phase1_q16")) if "chain_phase1_q16" in row else ("missing",),
        _integer(row.get("semantic_chain_projected_extent_q16_16"))
        if "semantic_chain_projected_extent_q16_16" in row else ("missing",),
        _integer(row.get("visibility_sheet")) if "visibility_sheet" in row else ("missing",),
        _integer(row.get("path_closure_class")) if "path_closure_class" in row else ("missing",),
        str(row.get("clipped_mask8", "<missing>")), segment["endpoints"],
        segment["projected_endpoints"],
        json.dumps(row.get("glyph_transform_facts") or {}, sort_keys=True, separators=(",", ":")),
    )


def _segment_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Stable producer identity, independent of raster cell and projection."""
    segment, errors = _segment_record(row, "segment")
    if errors or segment is None:
        return ("invalid", json.dumps(row, sort_keys=True, separators=(",", ":")))
    return (
        segment["feature_ref_key"], segment["segment_id"], segment["ordinal"],
        segment["endpoints"],
    )


def _normalize_compact_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Expand the bounded conservation surface into the verifier's row model.

    The compact writer owns one ordered segment list per snake and scalar
    claim/final-join witnesses.  Full sampled snapshots remain independent
    cross-checks and are not required for every rendered bridge frame.
    """
    snake_rows = frame.get("snakes") or frame.get("snake_rows")
    if not isinstance(snake_rows, list):
        return frame
    normalized = dict(frame)
    events = list(frame.get("events") or [])
    nodes: list[dict[str, Any]] = []
    for snake_index, snake in enumerate(snake_rows):
        if not isinstance(snake, dict):
            nodes.append(snake)
            continue
        ref = snake.get("feature_ref")
        current = snake.get("ordered_segments") or snake.get("segments") or []
        prior = snake.get("prior_ordered_segments") or snake.get("prior_segments") or []
        if isinstance(ref, dict):
            def add_ref(rows: Any) -> list[Any]:
                if not isinstance(rows, list):
                    return rows
                result = []
                for row in rows:
                    if not isinstance(row, dict):
                        result.append(row)
                        continue
                    value = dict(row)
                    value.setdefault("feature_ref", ref)
                    if "child_local_feature_id" not in value and "ordinal" in value:
                        value["child_local_feature_id"] = value["ordinal"]
                    if "object_local_endpoints_q24_8" not in value:
                        value["object_local_endpoints_q24_8"] = value.get(
                            "object_endpoints_q24_8")
                    result.append(value)
                return result
            current = add_ref(current)
            prior = add_ref(prior)
        node = {
            "cell_index": snake.get("cell_index", snake_index),
            "chain_id": snake.get("semantic_chain_id") or (
                ref or {}
            ).get("semantic_chain_id", 0),
            "phase": snake.get("phase", 2),
            "node_index": snake.get("node_index", snake_index),
            "current": current,
            "prior": prior,
        }
        correspondence = snake.get("correspondence") or snake.get("current_to_prior")
        if isinstance(correspondence, list) and isinstance(prior, list):
            for current_index, prior_index in enumerate(correspondence):
                if current_index >= len(current) or _integer(prior_index) is None:
                    continue
                prior_index_int = int(prior_index)
                if 0 <= prior_index_int < len(prior):
                    current[current_index] = dict(current[current_index])
                    current[current_index]["prior_segment_id"] = prior[prior_index_int].get(
                        "semantic_segment_id")
                elif prior_index_int == -1:
                    current[current_index] = dict(current[current_index])
                    current[current_index]["prior_segment_id"] = None
        nodes.append(node)
        event = snake.get("event")
        if isinstance(event, dict):
            event_value = dict(event)
            event_value.setdefault("feature_ref", ref)
            event_value.setdefault(
                "semantic_chain_id", snake.get("semantic_chain_id") or (ref or {}).get("semantic_chain_id")
            )
            events.append(event_value)
    normalized["events"] = events
    normalized["node_contributors"] = nodes
    claims = frame.get("claims")
    claim_meta = frame.get("claim_contributor_join") or frame.get("claim_counts")
    if isinstance(claims, dict):
        claim_meta = claims
        normalized["claims"] = []
    if isinstance(claim_meta, dict):
        normalized["_compact_claims"] = claim_meta
        normalized["claims"] = []
        normalized["claim_dispositions"] = []
    final_join = frame.get("final_join") or frame.get("final_join_counts")
    if isinstance(final_join, dict):
        normalized["_compact_final_join"] = final_join
    return normalized


def _read_structural_frames(receipt: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    measurement = receipt.get("structural_turn_measurement") or {}
    if measurement.get("export_error"):
        raise ValueError(f"structural measurement export failed: {measurement['export_error']}")
    if measurement.get("exists") is False:
        raise ValueError("structural measurement export reports missing output")
    relative = measurement.get("path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("receipt has no structural turn measurement path")
    path = ROOT / relative
    if not path.is_file():
        raise ValueError(f"structural turn measurement missing: {path}")
    digest_match = measurement.get("sha256") == sha256(path)
    frames: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = _normalize_compact_frame(json.loads(line))
        if not isinstance(value, dict):
            raise ValueError(f"structural frame {line_number} is not an object")
        schema = value.get("schema")
        if schema not in (
            "fl4512.structural_turn_snapshot.v1",
            "fl4512.structural_turn_frame.compact.v1",
        ):
            raise ValueError(
                f"structural frame {line_number} has stale or unknown schema: {schema!r}"
            )
        frames.append(value)
    return frames, {
        "path": str(path),
        "sha256": sha256(path),
        "receipt_sha256_match": digest_match,
        "receipt_frame_count": measurement.get("frame_count"),
        "io_error": measurement.get("io_error") or measurement.get("failure"),
    }


_EDIT_OPERATION_NAMES = {
    1: "birth",
    2: "resample",
    3: "resample",
    4: "delete",
    5: "trim",
    6: "extend",
    7: "split",
    8: "merge",
    9: "retire",
}
_EDIT_MEASUREMENT_KINDS = {
    "birth": {2},          # SUPPORT_COUNT
    "resample": {1},       # SPACING_Q16_16
    "trim": {3, 4},        # TANGENT_Q1_15 or DISTANCE_Q16_16
    "extend": {3, 4},      # TANGENT_Q1_15 or DISTANCE_Q16_16
    "split": {2, 3},       # unsupported SUPPORT_COUNT or TANGENT_Q1_15
    "merge": {3, 4},       # TANGENT_Q1_15 or DISTANCE_Q16_16
    "retire": {2},         # SUPPORT_COUNT
}


def _witnesses(event: dict[str, Any]) -> list[dict[str, Any]]:
    value = event.get("edit_witnesses")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    value = event.get("witness") or event.get("edit_witness")
    if value is None:
        value = (event.get("topology_event") or {}).get("witness")
    return [value] if isinstance(value, dict) else []


def _reassemble_witness_events(events: list[Any], frame_id: int, errors: list[str]) -> list[Any]:
    """Validate transport chunks before interpreting one semantic event.

    Historical unchunked rows have neither offset nor total. New rows must
    carry both; missing, reordered, duplicated, or mixed-identity chunks fail.
    """
    result: list[Any] = []
    cursor = 0
    chunk_fields = {"edit_witness_offset", "edit_witness_total", "edit_witness_count", "edit_witnesses"}
    while cursor < len(events):
        first = events[cursor]
        if not isinstance(first, dict) or not ({"edit_witness_offset", "edit_witness_total"} & first.keys()):
            result.append(first)
            cursor += 1
            continue
        total = _integer(first.get("edit_witness_total"))
        if (_integer(first.get("edit_witness_offset")) != 0
                or total is None or not 0 <= total <= 0xffffffff):
            errors.append(f"frame {frame_id}: invalid witness chunk head")
            cursor += 1
            continue
        identity = {key: value for key, value in first.items() if key not in chunk_fields}
        witnesses: list[dict[str, Any]] = []
        while True:
            if cursor >= len(events):
                errors.append(f"frame {frame_id}: incomplete witness chunks")
                break
            row = events[cursor]
            if not isinstance(row, dict):
                errors.append(f"frame {frame_id}: invalid witness chunk")
                cursor += 1
                break
            count = _integer(row.get("edit_witness_count"))
            items = row.get("edit_witnesses")
            expected_count = min(32, total - len(witnesses))
            if (_integer(row.get("edit_witness_offset")) != len(witnesses)
                    or _integer(row.get("edit_witness_total")) != total
                    or count != expected_count or not isinstance(items, list)
                    or len(items) != count or not all(isinstance(item, dict) for item in items)
                    or {key: value for key, value in row.items() if key not in chunk_fields} != identity):
                errors.append(f"frame {frame_id}: witness chunk order, count, or identity mismatch")
                cursor += 1
                break
            witnesses.extend(items)
            cursor += 1
            if len(witnesses) == total:
                result.append({**identity, "edit_witness_count": total, "edit_witnesses": witnesses})
                break
    return result


def _ordered_hash(event: dict[str, Any], field: str, frame_id: int, errors: list[str]) -> int | None:
    if field not in event:
        errors.append(f"frame {frame_id}: event lacks {field}")
        return None
    token = _id_token(event.get(field), allow_zero=True)
    value = int(token, 16) if token is not None else None
    if value is None:
        errors.append(f"frame {frame_id}: event has invalid {field}")
        return None
    return value


def _witness_ids(witness: dict[str, Any], field: str) -> list[str] | None:
    value = witness.get(field)
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        token = _id_token(item)
        if token is None:
            return None
        result.append(token)
    if len(set(result)) != len(result):
        return None
    return result


def _witness_counts(witness: dict[str, Any]) -> tuple[int, int] | None:
    before = _integer(witness.get("count_before"))
    after = _integer(witness.get("count_after"))
    if before is None or after is None or before < 0 or after < 0:
        return None
    return before, after


def _measured_witness(
    event: dict[str, Any], expected_type: str, frame_id: int, errors: list[str]
) -> dict[str, Any] | None:
    values = _witnesses(event)
    matching = [
        value for value in values
        if value.get("type") == expected_type
        or _EDIT_OPERATION_NAMES.get(_integer(value.get("operation"))) == expected_type
    ]
    # DELETE_UNSUPPORTED is a preparatory SUPPORT_COUNT witness that can be
    # copied onto a retirement row.  Prefer the explicit RETIRE witness when
    # present because only it proves the final count_after == 0 transition.
    if not matching and expected_type == "retire":
        matching = [
            value for value in values
            if _EDIT_OPERATION_NAMES.get(_integer(value.get("operation"))) == "delete"
        ]
    value = matching[0] if matching else None
    if value is None:
        errors.append(f"frame {frame_id}: {expected_type} lacks typed witness")
        return None
    operation = _integer(value.get("operation"))
    numeric_witness = operation in _EDIT_OPERATION_NAMES
    if numeric_witness:
        if value.get("valid") is not True:
            errors.append(f"frame {frame_id}: {expected_type} witness is invalid")
            return None
        operation_name = _EDIT_OPERATION_NAMES.get(operation)
        if operation_name != expected_type and not (
            expected_type == "retire" and operation_name == "delete"
        ):
            errors.append(f"frame {frame_id}: {expected_type} witness has wrong operation")
            return None
        measurement_kind = _integer(value.get("measurement_kind"))
        if measurement_kind in (None, 0):
            errors.append(f"frame {frame_id}: {expected_type} witness lacks measurement kind")
            return None
        expected_measurements = _EDIT_MEASUREMENT_KINDS.get(expected_type)
        if expected_measurements is not None and measurement_kind not in expected_measurements:
            errors.append(
                f"frame {frame_id}: {expected_type} witness has wrong measurement kind"
            )
            return None
    elif expected_type in {"split", "merge"}:
        # Topology edits cannot be admitted by a label-only witness.  Their
        # partition must carry the same numeric operation and measurement kind
        # that the runtime used to justify the edit magnitude.
        errors.append(f"frame {frame_id}: {expected_type} witness lacks numeric measured operation")
        return None
    elif value.get("type") != expected_type or value.get("measured") is not True:
        errors.append(f"frame {frame_id}: {expected_type} witness is unmeasured or mistyped")
        return None
    return value


def _numeric_witnesses(event: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    return [
        witness for witness in _witnesses(event)
        if _EDIT_OPERATION_NAMES.get(_integer(witness.get("operation"))) == operation
    ]


def _resample_sequence(
    prior_ids: list[str], current_ids: list[str], event: dict[str, Any], frame_id: int,
    errors: list[str],
) -> bool:
    """Validate scalar INSERT/REMOVE witnesses as local ordered edits."""
    working = list(prior_ids)
    witnesses = [
        witness for witness in _witnesses(event)
        if _EDIT_OPERATION_NAMES.get(_integer(witness.get("operation"))) == "resample"
    ]
    if not witnesses:
        errors.append(f"frame {frame_id}: CONTINUE identity change lacks resample witness")
        return False
    for witness in witnesses:
        if witness.get("valid") is not True:
            errors.append(f"frame {frame_id}: resample witness is invalid")
            return False
        operation = _integer(witness.get("operation"))
        counts = _witness_counts(witness)
        first = _integer(witness.get("first_ordinal"))
        if counts is None or first is None or counts[0] != len(working) + 1:
            errors.append(f"frame {frame_id}: resample witness counts do not match local sequence")
            return False
        if operation == 2:
            if counts[1] != counts[0] + 1 or first < 0 or first >= len(working):
                errors.append(f"frame {frame_id}: RESAMPLE_INSERT witness is not local")
                return False
            # Inserting a control replaces one segment with the two adjacent
            # segments named by the resulting ordered sequence.
            insertion_index = first
            if insertion_index + 2 > len(current_ids):
                errors.append(f"frame {frame_id}: RESAMPLE_INSERT exceeds current order")
                return False
            working[insertion_index:insertion_index + 1] = current_ids[
                insertion_index:insertion_index + 2
            ]
        elif operation == 3:
            if counts[1] != counts[0] - 1 or first < 1 or first >= len(working):
                errors.append(f"frame {frame_id}: RESAMPLE_REMOVE witness is not local")
                return False
            # Removing an interior control collapses the two adjacent
            # segments into the one segment at the local predecessor slot.
            removal_index = first - 1
            if removal_index + 2 > len(working) or removal_index >= len(current_ids):
                errors.append(f"frame {frame_id}: RESAMPLE_REMOVE exceeds current order")
                return False
            working[removal_index:removal_index + 2] = [current_ids[removal_index]]
        else:
            errors.append(f"frame {frame_id}: unsupported resample operation")
            return False
    if working != current_ids:
        errors.append(f"frame {frame_id}: resample witnesses do not conserve ordered IDs")
        return False
    return True


def _topology_partition(
    event: dict[str, Any], current: dict[str, dict[str, Any]], prior: dict[str, dict[str, Any]],
    frame_id: int, errors: list[str], kind: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Resolve scalar split/merge events without carrier-owned ID arrays."""
    owner_key, owner_errors = _complete_feature_ref(event.get("feature_ref"))
    errors.extend(f"frame {frame_id} topology {kind}: {error}" for error in owner_errors)
    if owner_key is None:
        return None
    owner_current = current.get(owner_key)
    if owner_current is None:
        errors.append(f"frame {frame_id} topology {kind}: owner chain is absent")
        return None
    owner_chain_id = owner_current["chain_id"]
    related_id = _id_token(event.get("related_semantic_chain_id"))
    if related_id is None:
        if kind == "merge" and _integer(event.get("related_semantic_chain_id", 0)) == 0:
            left_prior = prior.get(owner_key)
            if left_prior is None:
                errors.append(f"frame {frame_id} MERGE source chain is absent")
                return None
            return left_prior, None, owner_current, owner_current
        errors.append(f"frame {frame_id} topology {kind}: related chain is missing")
        return None
    related_current = next(
        (chain for chain in current.values() if chain["chain_id"] == related_id), None
    )
    related_prior = next(
        (chain for chain in prior.values() if chain["chain_id"] == related_id), None
    )
    owner_prior = prior.get(owner_key)
    if kind == "split":
        # Retained events own the parent chain; branch events own the new
        # chain. Resolve the parent from the related ID for the branch row.
        if _integer(event.get("kind")) == 6:
            parent_prior = next(
                (chain for chain in prior.values() if chain["chain_id"] == related_id), None
            )
            retained_current = related_current
            branch_current = owner_current
        else:
            parent_prior = owner_prior
            retained_current = owner_current
            branch_current = related_current
        if parent_prior is None or retained_current is None or branch_current is None:
            errors.append(f"frame {frame_id} SPLIT parent or child chain is absent")
            return None
        return parent_prior, retained_current, branch_current, owner_current
    # Merge events own the retained destination. The related ID names the
    # absorbed source. The second source is the owner chain's prior row.
    left_prior = owner_prior
    right_prior = related_prior
    if left_prior is None or right_prior is None:
        errors.append(f"frame {frame_id} MERGE source chain is absent")
        return None
    return left_prior, right_prior, owner_current, owner_current


def _chain_lookup(
    token: Any, chains: dict[str, dict[str, Any]], errors: list[str], context: str
) -> dict[str, Any] | None:
    if isinstance(token, dict):
        key, ref_errors = _complete_feature_ref(token)
        errors.extend(f"{context}: {error}" for error in ref_errors)
        if key is None:
            return None
        value = chains.get(key)
        if value is None:
            errors.append(f"{context}: referenced chain is absent")
        return value
    chain_id = _id_token(token)
    if chain_id is None:
        errors.append(f"{context}: missing chain identity")
        return None
    matches = [value for value in chains.values() if value["chain_id"] == chain_id]
    if len(matches) != 1:
        errors.append(f"{context}: chain identity resolves to {len(matches)} chains")
        return None
    return matches[0]


def _validate_node_correspondence(
    frame_id: int, node: dict[str, Any], errors: list[str]
) -> None:
    current_rows = node.get("current") or []
    prior_rows = node.get("prior") or []
    current: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    prior_ids: set[str] = set()
    for side, rows, output, seen in (
        ("current", current_rows, current, current_ids),
        ("prior", prior_rows, prior, prior_ids),
    ):
        if not isinstance(rows, list):
            errors.append(f"frame {frame_id}: node {side} contributors are not a list")
            continue
        previous_ordinal: int | None = None
        seen_ordinals: set[int] = set()
        for index, row in enumerate(rows):
            segment, row_errors = _segment_record(
                row, f"frame {frame_id} node {side}[{index}]", node.get("cell_index")
            )
            errors.extend(row_errors)
            if segment is None:
                continue
            if segment["ordinal"] in seen_ordinals:
                errors.append(f"frame {frame_id}: node {side} duplicates segment ordinal")
            seen_ordinals.add(segment["ordinal"])
            if previous_ordinal is not None and segment["ordinal"] <= previous_ordinal:
                errors.append(f"frame {frame_id}: node {side} rows are not in ordinal order")
            previous_ordinal = segment["ordinal"]
            if segment["segment_id"] in seen:
                errors.append(
                    f"frame {frame_id}: node {side} duplicates segment {segment['segment_id']}"
                )
            seen.add(segment["segment_id"])
            output.append(segment)
    prior_by_id = {segment["segment_id"]: segment for segment in prior}
    mapped_prior: set[str] = set()
    mapped_pairs: list[tuple[int, int]] = []
    for current_segment in current:
        has_explicit = "prior_segment_id" in current_segment["row"]
        explicit = current_segment["row"].get("prior_segment_id")
        if has_explicit and explicit is None:
            continue
        if explicit is not None and _id_token(explicit) is None:
            errors.append(f"frame {frame_id}: node correspondence has invalid prior segment ID")
            continue
        prior_id = _id_token(explicit) if has_explicit else current_segment["segment_id"]
        if prior_id not in prior_by_id:
            if prior_rows:
                errors.append(
                    f"frame {frame_id}: node correspondence lacks prior segment {prior_id}"
                )
            continue
        if prior_id in mapped_prior:
            errors.append(
                f"frame {frame_id}: node maps multiple current segments to prior {prior_id}"
            )
            continue
        mapped_prior.add(prior_id)
        mapped_pairs.append((current_segment["ordinal"], prior_by_id[prior_id]["ordinal"]))
    ordered_pairs = sorted(mapped_pairs)
    if any(prior_ordinal <= previous for (_, previous), (_, prior_ordinal) in zip(
        ordered_pairs, ordered_pairs[1:]
    )):
        errors.append(f"frame {frame_id}: node correspondence crosses or reuses prior order")


def _frame_state(frame: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    frame_id = _integer(frame.get("bridge_frame"))
    if frame_id is None:
        errors.append("frame has missing bridge_frame")
        frame_id = 0
    if frame.get("carrier_valid") is not True:
        errors.append(f"frame {frame_id}: structural carrier invalid")
    if frame.get("active_stroke_event_overflow") is True:
        errors.append(f"frame {frame_id}: active-stroke event overflow")
    if frame.get("sequence_cell_claim_overflow") is True:
        errors.append(f"frame {frame_id}: sequence claim overflow")
    if frame.get("observation_rejection_overflow") is not False:
        errors.append(f"frame {frame_id}: observation rejection overflow status is not false")
    if frame.get("spacetime_node_order_invalid") is not False:
        errors.append(f"frame {frame_id}: spacetime node order status is not false")
    lifecycle = frame.get("lifecycle") or {}
    for field in ("active_snake_count", "retired_snake_count", "event_count", "reset_reason"):
        if field not in lifecycle:
            errors.append(f"frame {frame_id}: lifecycle lacks {field}")
    if "active_stroke_event_count" not in frame:
        errors.append(f"frame {frame_id}: active-stroke event count is missing")
    active_frame_fields = frame.get("active_frame") or {}
    for field in ("published_snake_count", "published_segment_count"):
        if field not in active_frame_fields:
            errors.append(f"frame {frame_id}: active frame lacks {field}")
    if "failure" in active_frame_fields and _integer(active_frame_fields.get("failure")) != 0:
        errors.append(f"frame {frame_id}: active frame reports failure")
    if "capacity_failure" in lifecycle and lifecycle.get("capacity_failure") is not False:
        errors.append(f"frame {frame_id}: lifecycle reports capacity failure")
    reset_reason = _integer(lifecycle.get("reset_reason", 0))
    if reset_reason is None or reset_reason != 0:
        errors.append(f"frame {frame_id}: lifecycle reset reason is not NONE")
    if "lifecycle_reset_reason" in frame and _integer(frame.get("lifecycle_reset_reason")) != 0:
        errors.append(f"frame {frame_id}: lifecycle_reset_reason is not NONE")
    compact_frame = isinstance(frame.get("_compact_claims"), dict)
    if not compact_frame:
        # The full snapshot is the only surface that carries the individual
        # claim, cell, node, and final-tuple rows.  Missing one of those rows
        # cannot be treated as an empty census: it erases the join needed to
        # distinguish a moved silhouette from a conserved structural snake.
        for field in ("claims", "claim_dispositions", "structural_cells", "node_contributors"):
            if field not in frame or not isinstance(frame.get(field), list):
                errors.append(f"frame {frame_id}: full snapshot lacks {field} join rows")
    if compact_frame:
        if not isinstance(frame.get("_compact_final_join"), dict):
            errors.append(f"frame {frame_id}: compact final-join census is missing")
        for field in (
            "active_stroke_observation_valid", "materialization_attempted",
            "materialization_complete", "materialization_failure",
            "active_stroke_event_overflow", "sequence_cell_claim_overflow",
            "observation_rejection_overflow", "spacetime_node_order_invalid",
        ):
            if field not in frame:
                errors.append(f"frame {frame_id}: compact frame lacks {field}")
        if frame.get("active_stroke_observation_valid") is not True:
            errors.append(f"frame {frame_id}: compact active-stroke observation is invalid")
        if frame.get("materialization_attempted") is not True:
            errors.append(f"frame {frame_id}: compact materialization was not attempted")
        if frame.get("materialization_complete") is not True:
            errors.append(f"frame {frame_id}: compact materialization is incomplete")
        if _integer(frame.get("materialization_failure")) != 0:
            errors.append(f"frame {frame_id}: compact materialization reports failure")
        if frame.get("active_stroke_event_overflow") is not False:
            errors.append(f"frame {frame_id}: compact event overflow status is not false")
        if frame.get("sequence_cell_claim_overflow") is not False:
            errors.append(f"frame {frame_id}: compact claim overflow status is not false")
        if "failure" not in active_frame_fields or _integer(active_frame_fields.get("failure")) != 0:
            errors.append(f"frame {frame_id}: compact active frame failure is not zero")
        if "capacity_failure" not in lifecycle or lifecycle.get("capacity_failure") is not False:
            errors.append(f"frame {frame_id}: compact lifecycle capacity failure is not false")

    chains: dict[str, dict[str, Any]] = {}
    claims = frame.get("claims") or []
    dispositions = frame.get("claim_dispositions") or []
    if not isinstance(claims, list) or not isinstance(dispositions, list):
        errors.append(f"frame {frame_id}: claims or dispositions are not lists")
        claims = claims if isinstance(claims, list) else []
        dispositions = dispositions if isinstance(dispositions, list) else []
    if len(dispositions) != len(claims):
        errors.append(f"frame {frame_id}: claim disposition census disagrees")
    by_claim_index: dict[int, dict[str, Any]] = {}
    for disposition in dispositions:
        claim_index = _integer(disposition.get("claim_index")) if isinstance(disposition, dict) else None
        if claim_index is None or claim_index < 0 or claim_index in by_claim_index:
            errors.append(f"frame {frame_id}: duplicate or invalid claim disposition")
            continue
        by_claim_index[claim_index] = disposition
        if _integer(disposition.get("disposition")) not in (1, 2, 3):
            errors.append(f"frame {frame_id}: claim {claim_index} has no typed disposition")
    expected_claims: Counter[tuple[Any, ...]] = Counter()
    selected_claim_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for claim_index, claim in enumerate(claims):
        segment, claim_errors = _segment_record(claim, f"frame {frame_id} claim {claim_index}", claim.get("cell_index") if isinstance(claim, dict) else None)
        errors.extend(claim_errors)
        disposition = by_claim_index.get(claim_index)
        if disposition is None:
            continue
        for field in ("cell_index", "semantic_segment_id", "feature_ref"):
            if field not in disposition:
                errors.append(f"frame {frame_id}: claim {claim_index} disposition lacks {field}")
        if _integer(disposition.get("cell_index")) != _integer(
            claim.get("cell_index") if isinstance(claim, dict) else None
        ):
            errors.append(f"frame {frame_id}: claim {claim_index} disposition cell disagrees")
        if _id_token(disposition.get("semantic_segment_id")) != _id_token(
            claim.get("semantic_segment_id") if isinstance(claim, dict) else None
        ):
            errors.append(f"frame {frame_id}: claim {claim_index} disposition segment disagrees")
        disposition_ref, disposition_ref_errors = _complete_feature_ref(
            disposition.get("feature_ref")
        )
        errors.extend(
            f"frame {frame_id} claim {claim_index} disposition: {error}"
            for error in disposition_ref_errors
        )
        claim_ref = claim.get("feature_ref") if isinstance(claim, dict) else None
        claim_ref_key, _ = _complete_feature_ref(claim_ref)
        if disposition_ref is not None and claim_ref_key is not None and disposition_ref != claim_ref_key:
            errors.append(f"frame {frame_id}: claim {claim_index} disposition FeatureRef disagrees")
        if _integer(disposition.get("disposition")) == 1 and segment is not None:
            expected_claims[_claim_key(claim)] += 1
            if expected_claims[_claim_key(claim)] > 1:
                errors.append(f"frame {frame_id}: duplicate selected claim key")
            selected_claim_rows.append((claim, segment))

    cells: dict[int, dict[str, Any]] = {}
    for index, cell in enumerate(frame.get("structural_cells") or []):
        cell_index = _integer(cell.get("cell_index")) if isinstance(cell, dict) else None
        if cell_index is None or cell_index in cells:
            errors.append(f"frame {frame_id}: duplicate or invalid structural cell")
            continue
        cells[cell_index] = cell
        if not isinstance(cell, dict):
            continue
        final_result = cell.get("final_result") or {}
        final_tuple = cell.get("final_tuple") or {}
        selected_role = _integer(final_result.get("selected_role", 0))
        anchor_refs = cell.get("anchor_feature_refs")
        if anchor_refs is None:
            anchor_refs = (cell.get("node") or {}).get("anchor_feature_refs")
        if anchor_refs is not None:
            if not isinstance(anchor_refs, list) or not anchor_refs:
                errors.append(f"frame {frame_id} cell {cell_index}: anchor FeatureRefs are empty")
                anchor_refs = []
            for anchor_index, anchor in enumerate(anchor_refs):
                _, anchor_errors = _complete_feature_ref(anchor)
                errors.extend(
                    f"frame {frame_id} cell {cell_index} anchor {anchor_index}: {error}"
                    for error in anchor_errors
                )
        anchor_kind = _integer(cell.get("anchor_kind", 0)) or 0
        is_anchor_cell = anchor_kind != 0 or (
            isinstance(anchor_refs, list) and bool(anchor_refs) and
            _integer(cell.get("spacetime_phase")) != 2
        )
        if is_anchor_cell:
            if not isinstance(anchor_refs, list) or not anchor_refs:
                errors.append(f"frame {frame_id} cell {cell_index}: anchor lacks complete anchor FeatureRefs")
            if final_result.get("valid") is not True or final_tuple.get("valid") is not True:
                errors.append(f"frame {frame_id} cell {cell_index}: anchor final join invalid")
            if _integer(final_result.get("final_gid", -1)) != _integer(final_tuple.get("final_gid", -2)):
                errors.append(f"frame {frame_id} cell {cell_index}: anchor tuple diverges from owner")
            continue
        is_chain_cell = selected_role == 1 or (
            _integer(cell.get("spacetime_phase")) == 2
            or (_integer(cell.get("chain_id", 0) or 0) not in (None, 0) and not anchor_refs)
        )
        if is_chain_cell:
            if final_result.get("valid") is not True or final_tuple.get("valid") is not True:
                errors.append(f"frame {frame_id} cell {cell_index}: final join invalid")
            if "feature_ref" not in cell:
                errors.append(f"frame {frame_id} cell {cell_index}: final cell lacks FeatureRef")
            else:
                _, cell_ref_errors = _complete_feature_ref(cell.get("feature_ref"))
                errors.extend(f"frame {frame_id} cell {cell_index}: {error}" for error in cell_ref_errors)
            if _integer(final_result.get("final_gid", -1)) != _integer(final_tuple.get("final_gid", -2)):
                errors.append(f"frame {frame_id} cell {cell_index}: final tuple diverges from owner")
            sequence_chain = _id_token(final_result.get("sequence_chain_id"))
            cell_chain = _id_token(cell.get("chain_id"))
            if sequence_chain is None or cell_chain is None or sequence_chain != cell_chain:
                errors.append(f"frame {frame_id} cell {cell_index}: final chain identity is incomplete")
        elif final_result or final_tuple:
            if final_result.get("valid") is not True or final_tuple.get("valid") is not True:
                errors.append(f"frame {frame_id} cell {cell_index}: anchor final join invalid")
            if _integer(final_result.get("final_gid", -1)) != _integer(final_tuple.get("final_gid", -2)):
                errors.append(f"frame {frame_id} cell {cell_index}: anchor tuple diverges from owner")
        if cell.get("spacetime_valid"):
            node_glyph = _integer((cell.get("node") or {}).get("glyph_id"))
            if node_glyph is None or node_glyph <= 0:
                errors.append(f"frame {frame_id} cell {cell_index}: spacetime node glyph is invalid")
            elif node_glyph != _integer(final_result.get("final_gid")):
                errors.append(f"frame {frame_id} cell {cell_index}: node glyph diverges from final owner")

    observed_claims: Counter[tuple[Any, ...]] = Counter()
    global_node_pairs: dict[str, set[tuple[int, int]]] = {}
    node_stats: dict[str, dict[str, int]] = {}
    node_rows = frame.get("node_contributors") or []
    if not isinstance(node_rows, list):
        errors.append(f"frame {frame_id}: node_contributors is not a list")
        node_rows = []
    for node_index, node in enumerate(node_rows):
        if not isinstance(node, dict):
            errors.append(f"frame {frame_id}: node {node_index} is not an object")
            continue
        _validate_node_correspondence(frame_id, node, errors)
        node_chain = _id_token(node.get("chain_id"), allow_zero=True)
        current_records: list[dict[str, Any]] = []
        prior_records: dict[str, dict[str, Any]] = {}
        for row in node.get("prior") or []:
            segment, _ = _segment_record(row, f"frame {frame_id} node prior", node.get("cell_index"))
            if segment is not None:
                prior_records[segment["segment_id"]] = segment
        for contributor_index, contributor in enumerate(node.get("current") or []):
            segment, contributor_errors = _segment_record(
                contributor,
                f"frame {frame_id} node current[{node_index}:{contributor_index}]",
                node.get("cell_index"),
            )
            errors.extend(contributor_errors)
            if segment is None:
                continue
            current_records.append(segment)
            node_stats.setdefault(segment["feature_ref_key"], {
                "current": 0, "prior": 0, "mapped": 0,
            })["current"] += 1
            if node_chain not in (None, "0000000000000000") and segment["chain_id"] != node_chain:
                errors.append(f"frame {frame_id}: node chain differs from contributor FeatureRef")
            observed_claims[_claim_key(contributor, node.get("cell_index"))] += 1
            if observed_claims[_claim_key(contributor, node.get("cell_index"))] > 1:
                errors.append(f"frame {frame_id}: duplicate contributor claim key")
            chain = chains.setdefault(
                segment["feature_ref_key"],
                {
                    "feature_ref": segment["feature_ref"],
                    "feature_ref_key": segment["feature_ref_key"],
                    "chain_id": segment["chain_id"],
                    "segments": {},
                    "segment_row_ids": set(),
                    "segment_row_ordinals": [],
                },
            )
            prior_segment = chain["segments"].get(segment["segment_id"])
            if prior_segment is not None:
                if _segment_signature(prior_segment) != _segment_signature(segment):
                    errors.append(f"frame {frame_id}: duplicate segment ID has conflicting identity")
            else:
                chain["segment_row_ids"].add(segment["segment_id"])
                chain["segment_row_ordinals"].append(segment["ordinal"])
                chain["segments"][segment["segment_id"]] = segment
        for current_segment in current_records:
            prior_id = _id_token(
                current_segment["row"].get("prior_segment_id")
            ) if current_segment["row"].get("prior_segment_id") is not None else current_segment["segment_id"]
            prior_segment = prior_records.get(prior_id)
            if prior_segment is not None:
                node_stats[current_segment["feature_ref_key"]]["mapped"] += 1
                global_node_pairs.setdefault(current_segment["chain_id"], set()).add(
                    (current_segment["ordinal"], prior_segment["ordinal"])
                )
        for prior_segment in prior_records.values():
            node_stats.setdefault(prior_segment["feature_ref_key"], {
                "current": 0, "prior": 0, "mapped": 0,
            })["prior"] += 1
        for anchor in node.get("anchor_feature_refs") or []:
            _, anchor_errors = _complete_feature_ref(anchor)
            errors.extend(f"frame {frame_id} node anchor: {error}" for error in anchor_errors)

    if not compact_frame:
        # Every selected claim must reach one structural cell owned by the same
        # FeatureRef and one valid final tuple.  This closes the false-positive
        # path where camera/silhouette motion survives after cells or nodes are
        # silently omitted from the full snapshot.
        selected_cell_indices = {
            _integer(claim.get("cell_index")) for claim, _ in selected_claim_rows
        }
        selected_cell_indices.discard(None)
        selected_cell_indices = {
            cell_index for cell_index in selected_cell_indices
            if not isinstance(cells.get(cell_index), dict)
            or (_integer(cells[cell_index].get("anchor_kind", 0)) or 0) == 0
        }
        chain_cell_indices: set[int] = set()
        for cell_index, cell in cells.items():
            if not isinstance(cell, dict):
                errors.append(f"frame {frame_id} cell {cell_index}: cell is not an object")
                continue
            final_result = cell.get("final_result") or {}
            chain_id = _id_token(cell.get("chain_id"), allow_zero=True)
            is_anchor = (_integer(cell.get("anchor_kind", 0)) or 0) != 0
            if not is_anchor and (
                _integer(final_result.get("selected_role")) == 1
                or chain_id not in (None, "0000000000000000")
            ):
                chain_cell_indices.add(cell_index)
        if selected_cell_indices != chain_cell_indices:
            errors.append(
                f"frame {frame_id}: claim/cell join census disagrees "
                f"({len(selected_cell_indices)} selected claims vs {len(chain_cell_indices)} chain cells)"
            )
        cell_claims: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for claim, segment in selected_claim_rows:
            cell_index = _integer(claim.get("cell_index"))
            if cell_index is not None:
                cell_claims.setdefault(cell_index, []).append((claim, segment))
        for cell_index, claim_rows in cell_claims.items():
            cell = cells.get(cell_index)
            if not isinstance(cell, dict):
                errors.append(f"frame {frame_id} claim cell {cell_index}: structural cell is missing")
                continue
            claim_ref_key, _ = _complete_feature_ref(claim_rows[0][0].get("feature_ref"))
            cell_ref_key, cell_ref_errors = _complete_feature_ref(cell.get("feature_ref"))
            is_anchor = (_integer(cell.get("anchor_kind", 0)) or 0) != 0
            anchor_keys = {
                key for key, anchor in (
                    (_complete_feature_ref(value)[0], value)
                    for value in (cell.get("anchor_feature_refs") or [])
                ) if key is not None
            }
            if is_anchor:
                cell_ref_errors = []
                cell_ref_key = None
            errors.extend(
                f"frame {frame_id} claim cell {cell_index}: {error}"
                for error in cell_ref_errors
            )
            if is_anchor:
                if claim_ref_key is None or claim_ref_key not in anchor_keys:
                    errors.append(
                        f"frame {frame_id} claim cell {cell_index}: anchor FeatureRef join disagrees"
                    )
            elif claim_ref_key is None or cell_ref_key is None or claim_ref_key != cell_ref_key:
                errors.append(f"frame {frame_id} claim cell {cell_index}: FeatureRef join disagrees")
            final_result = cell.get("final_result")
            final_tuple = cell.get("final_tuple")
            node = cell.get("node")
            if not isinstance(final_result, dict) or final_result.get("valid") is not True:
                errors.append(f"frame {frame_id} claim cell {cell_index}: final result join is missing or invalid")
            if not isinstance(final_tuple, dict) or final_tuple.get("valid") is not True:
                errors.append(f"frame {frame_id} claim cell {cell_index}: final tuple join is missing or invalid")
            if not isinstance(node, dict):
                errors.append(f"frame {frame_id} claim cell {cell_index}: node join is missing")
            if isinstance(final_result, dict) and isinstance(final_tuple, dict):
                final_gid = _integer(final_result.get("final_gid"))
                tuple_gid = _integer(final_tuple.get("final_gid"))
                if final_gid is None or final_gid <= 0 or tuple_gid != final_gid:
                    errors.append(f"frame {frame_id} claim cell {cell_index}: final tuple glyph join disagrees")
                if _integer(final_result.get("selected_role")) != 1:
                    errors.append(f"frame {frame_id} claim cell {cell_index}: final result is not structural")
            if isinstance(node, dict) and isinstance(final_result, dict):
                if _integer(node.get("glyph_id")) != _integer(final_result.get("final_gid")):
                    errors.append(f"frame {frame_id} claim cell {cell_index}: node/final tuple join disagrees")
            if cell.get("spacetime_valid") is not True:
                errors.append(f"frame {frame_id} claim cell {cell_index}: spacetime node is not valid")

    missing = expected_claims - observed_claims
    unexpected = observed_claims - expected_claims
    compact_claims = frame.get("_compact_claims") or {}
    if compact_claims and "failure" not in active_frame_fields:
        errors.append(f"frame {frame_id}: compact active frame failure is missing")
    if compact_claims and "capacity_failure" not in lifecycle:
        errors.append(f"frame {frame_id}: compact lifecycle capacity failure is missing")
    if not compact_claims:
        if missing:
            errors.append(f"frame {frame_id}: {sum(missing.values())} selected claims orphaned")
        if unexpected:
            errors.append(f"frame {frame_id}: {sum(unexpected.values())} contributors lack claims")
    compact_claim_total = None
    compact_contributor_total = None
    if compact_claims:
        compact_claim_total = _integer(
            compact_claims.get("claim_count", compact_claims.get("count"))
        )
        compact_contributor_total = _integer(
            compact_claims.get("contributor_count", compact_claims.get("segment_count"))
        )
        claim_hash = compact_claims.get("claim_hash") or compact_claims.get("claims_hash")
        contributor_hash = compact_claims.get("contributor_hash") or compact_claims.get(
            "contributors_hash"
        )
        if compact_claim_total is None or compact_contributor_total is None:
            errors.append(f"frame {frame_id}: compact claim/contributor counts are missing")
        if not isinstance(claim_hash, str) or not claim_hash:
            errors.append(f"frame {frame_id}: compact claim hash is missing")
        if not isinstance(contributor_hash, str) or not contributor_hash:
            errors.append(f"frame {frame_id}: compact contributor hash is missing")
        for field in ("orphan_count", "duplicate_count", "mismatch_count"):
            if field not in compact_claims:
                errors.append(f"frame {frame_id}: compact claim {field} is missing")
                continue
            value = compact_claims.get(field)
            if _integer(value) != 0:
                errors.append(f"frame {frame_id}: compact claim {field} is nonzero")
        if compact_claim_total is not None and compact_contributor_total is not None:
            if compact_claim_total < 0 or compact_contributor_total < 0:
                errors.append(f"frame {frame_id}: compact claim counts are negative")
    for chain in chains.values():
        sequence = _ordered_segments(chain["segments"])
        ordinals = [segment["ordinal"] for segment in sequence]
        if len(ordinals) != len(set(ordinals)):
            errors.append(f"frame {frame_id}: chain has duplicate segment ordinals")
        if chain["segment_row_ordinals"] != sorted(chain["segment_row_ordinals"]):
            errors.append(f"frame {frame_id}: snake rows are not in ordinal order")
        chain["sequence"] = sequence

    spacetime_nodes = frame.get("spacetime_nodes")
    if spacetime_nodes is not None:
        if not isinstance(spacetime_nodes, list):
            errors.append(f"frame {frame_id}: spacetime_nodes is not a list")
            spacetime_nodes = []
        node_orders: dict[str, list[tuple[int, int | None, int, int | None]]] = {}
        global_node_order: list[tuple[tuple[Any, ...], int]] = []
        seen_node_cells: set[int] = set()
        for node_index, node in enumerate(spacetime_nodes):
            if not isinstance(node, dict):
                errors.append(f"frame {frame_id}: spacetime node {node_index} is not an object")
                continue
            ref_key, ref_errors = _complete_feature_ref(node.get("feature_ref"))
            errors.extend(
                f"frame {frame_id} spacetime node {node_index}: {error}"
                for error in ref_errors
            )
            if ref_key is None:
                continue
            semantic_chain_id = _id_token(node.get("semantic_chain_id"))
            feature_chain_id = _id_token(node["feature_ref"].get("semantic_chain_id"))
            if semantic_chain_id is None or semantic_chain_id != feature_chain_id:
                errors.append(f"frame {frame_id}: spacetime node chain identity disagrees")
            if ref_key not in chains:
                errors.append(f"frame {frame_id}: spacetime node references absent snake")
            cell_index = _integer(node.get("cell_index"))
            if cell_index is None or cell_index < 0:
                errors.append(f"frame {frame_id}: spacetime node has invalid cell_index")
            elif cell_index in seen_node_cells:
                errors.append(f"frame {frame_id}: spacetime node cell_index is duplicated")
            else:
                seen_node_cells.add(cell_index)
            current_node_index = _integer(node.get("node_index"))
            if current_node_index is None or current_node_index < 0:
                errors.append(f"frame {frame_id}: spacetime node has invalid node_index")
                continue
            current_u = _integer(node.get("u_q16"))
            if current_u is None or current_u < 0:
                errors.append(f"frame {frame_id}: spacetime node has invalid u_q16")
                current_u = 0
            temporal = node.get("temporal_corresponded")
            if not isinstance(temporal, bool):
                errors.append(f"frame {frame_id}: spacetime node lacks temporal correspondence flag")
            prior_node_index = _integer(node.get("prior_node_index"))
            if temporal is True and (prior_node_index is None or prior_node_index < 0):
                errors.append(f"frame {frame_id}: spacetime node has invalid prior_node_index")
            prior_u = _integer(node.get("prior_u_q16"))
            if temporal is True and (prior_u is None or prior_u < 0):
                errors.append(f"frame {frame_id}: spacetime node has invalid prior_u_q16")
            mapped_prior_index = prior_node_index if temporal is True else None
            mapped_prior_u = prior_u if temporal is True else None
            glyph_id = _integer(node.get("glyph_id"))
            final_result = node.get("final_result") or {}
            final_gid = _integer(node.get("final_gid", final_result.get("final_gid")))
            final_tuple = node.get("final_tuple") or {}
            tuple_gid = _integer(
                node.get("tuple_final_gid", node.get("final_tuple_gid", final_tuple.get("final_gid")))
            )
            if (
                glyph_id is None or final_gid is None or tuple_gid is None
                or glyph_id <= 0 or final_gid <= 0 or tuple_gid <= 0
            ):
                errors.append(f"frame {frame_id}: spacetime node final glyph fields are incomplete")
            elif glyph_id != final_gid or glyph_id != tuple_gid:
                errors.append(f"frame {frame_id}: spacetime node glyph/final tuple diverges")
            tuple_valid = node.get("tuple_valid", final_tuple.get("valid"))
            decision_valid = node.get("tuple_decision_valid", final_tuple.get("decision_valid"))
            if tuple_valid is not True or decision_valid is not True:
                errors.append(f"frame {frame_id}: spacetime node tuple is not valid")
            contributor_count = _integer(node.get("contributor_count"))
            if contributor_count is None or contributor_count <= 0:
                errors.append(f"frame {frame_id}: spacetime node has no contributors")
            node_orders.setdefault(ref_key, []).append(
                (current_node_index, mapped_prior_index, current_u, mapped_prior_u)
            )
            global_node_order.append((_feature_ref_sort_key(node["feature_ref"]), current_node_index))
        if global_node_order != sorted(global_node_order):
            errors.append(f"frame {frame_id}: global spacetime node order is not monotone")
        for pairs in node_orders.values():
            current_indices = [pair[0] for pair in pairs]
            current_u_values = [pair[2] for pair in pairs]
            if current_indices != sorted(current_indices) or len(current_indices) != len(set(current_indices)):
                errors.append(f"frame {frame_id}: spacetime node indices are not ordered or unique")
            if current_u_values != sorted(current_u_values):
                errors.append(f"frame {frame_id}: spacetime node u order is not monotone")
            valid_pairs = sorted(pair for pair in pairs if pair[1] is not None)
            if any(after[1] <= before[1] for before, after in zip(valid_pairs, valid_pairs[1:])):
                errors.append(f"frame {frame_id}: spacetime node correspondence crosses or reuses prior order")
    for chain_id, pairs in global_node_pairs.items():
        current_targets: dict[int, set[int]] = {}
        prior_targets: dict[int, set[int]] = {}
        for current_ordinal, prior_ordinal in pairs:
            current_targets.setdefault(current_ordinal, set()).add(prior_ordinal)
            prior_targets.setdefault(prior_ordinal, set()).add(current_ordinal)
        if any(len(values) != 1 for values in current_targets.values()) or any(
            len(values) != 1 for values in prior_targets.values()
        ):
            errors.append(f"frame {frame_id}: node correspondence is not one-to-one")
        ordered_pairs = sorted(pairs)
        if any(after[1] <= before[1] for before, after in zip(ordered_pairs, ordered_pairs[1:])):
            errors.append(f"frame {frame_id}: node correspondence crosses or reuses prior order")

    events = frame.get("events") or []
    if not isinstance(events, list):
        errors.append(f"frame {frame_id}: events is not a list")
        events = []
    event_count = _integer(frame.get("active_stroke_event_count"))
    lifecycle_count = _integer(lifecycle.get("event_count"))
    if event_count is not None and event_count != len(events):
        errors.append(f"frame {frame_id}: active-stroke event count disagrees")
    if lifecycle_count is not None and lifecycle_count != len(events):
        errors.append(f"frame {frame_id}: lifecycle event count disagrees")
    events = _reassemble_witness_events(events, frame_id, errors)
    active_events: dict[str, dict[str, Any]] = {}
    retired_events: dict[str, dict[str, Any]] = {}
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"frame {frame_id}: event {event_index} is not an object")
            continue
        event_name = _SNAKE_EVENT_NAMES.get(_integer(event.get("kind")), "unknown")
        if event_name == "unknown" or event_name == "none":
            errors.append(f"frame {frame_id}: event {event_index} has invalid kind")
        if event.get("feature_ref_complete") is False:
            errors.append(f"frame {frame_id} event {event_index}: FeatureRef is incomplete")
        _ordered_hash(event, "ordered_segment_hash_before", frame_id, errors)
        _ordered_hash(event, "ordered_segment_hash_after", frame_id, errors)
        ref_key, ref_errors = _complete_feature_ref(event.get("feature_ref"))
        errors.extend(f"frame {frame_id} event {event_index}: {error}" for error in ref_errors)
        if ref_key is None:
            continue
        event_chain_id = _id_token(event.get("semantic_chain_id"))
        ref_chain_id = _id_token((event.get("feature_ref") or {}).get("semantic_chain_id"))
        if event_chain_id is None or event_chain_id != ref_chain_id:
            errors.append(f"frame {frame_id} event {event_index}: event chain identity disagrees")
        participants = event.get("participants")
        if not isinstance(participants, list):
            errors.append(f"frame {frame_id} event {event_index}: participants missing")
            participants = []
        participant_count = _integer(event.get("participant_count"))
        if participant_count is not None and participant_count != len(participants):
            errors.append(f"frame {frame_id} event {event_index}: participant count disagrees")
        for participant in participants:
            _, participant_errors = _complete_feature_ref(participant)
            errors.extend(f"frame {frame_id} event {event_index}: {error}" for error in participant_errors)
        if participants and _feature_ref_key(participants[0]) != ref_key:
            errors.append(f"frame {frame_id} event {event_index}: first participant is not event owner")
        topology_kind = _integer((event.get("topology_event") or {}).get("kind", 0)) or 0
        if event_name in {"split_retained", "split_branch"} and topology_kind not in (0, _TOPOLOGY_SPLIT):
            errors.append(f"frame {frame_id} event {event_index}: split topology kind disagrees")
        if event_name == "merge_retained" and topology_kind not in (0, _TOPOLOGY_MERGE):
            errors.append(f"frame {frame_id} event {event_index}: merge topology kind disagrees")
        if topology_kind in (_TOPOLOGY_SPLIT, _TOPOLOGY_MERGE):
            if (event.get("topology_event") or {}).get("magnitude_measured") is not True:
                errors.append(
                    f"frame {frame_id} event {event_index}: topology edit magnitude is unmeasured"
                )
        if topology_kind in (_TOPOLOGY_SPLIT, _TOPOLOGY_MERGE):
            if len(participants) < 2:
                errors.append(f"frame {frame_id} event {event_index}: topology edit lacks both participants")
        edit_witnesses = _witnesses(event)
        witness_count = _integer(event.get("edit_witness_count"))
        if witness_count is not None and witness_count != len(edit_witnesses):
            errors.append(f"frame {frame_id} event {event_index}: edit witness count disagrees")
        if event_name not in {"none", "continue"} and not edit_witnesses:
            errors.append(f"frame {frame_id} event {event_index}: edit event lacks witness rows")
        for witness_index, witness in enumerate(edit_witnesses):
            operation = _integer(witness.get("operation"))
            if operation is not None:
                if operation not in _EDIT_OPERATION_NAMES:
                    errors.append(
                        f"frame {frame_id} event {event_index}: witness {witness_index} has invalid operation"
                    )
                if witness.get("valid") is not True:
                    errors.append(
                        f"frame {frame_id} event {event_index}: witness {witness_index} is invalid"
                    )
                if operation != 1 and _integer(witness.get("measurement_kind")) in (None, 0):
                    errors.append(
                        f"frame {frame_id} event {event_index}: witness {witness_index} lacks measurement kind"
                    )
                counts = _witness_counts(witness)
                if counts is None:
                    errors.append(
                        f"frame {frame_id} event {event_index}: witness {witness_index} lacks non-negative counts"
                    )
        if event_name == "retire":
            if ref_key in retired_events:
                errors.append(f"frame {frame_id}: duplicate retirement event")
            retired_events[ref_key] = event
        elif ref_key in chains:
            if ref_key in active_events:
                topology_kind = _integer((event.get("topology_event") or {}).get("kind", 0))
                if topology_kind in (None, 0):
                    errors.append(f"frame {frame_id}: duplicate active event for chain")
            else:
                active_events[ref_key] = event

    active_count = _integer(lifecycle.get("active_snake_count"))
    retired_count = _integer(lifecycle.get("retired_snake_count"))
    if active_count is not None and active_count != len(active_events):
        errors.append(f"frame {frame_id}: active lifecycle census disagrees")
    if retired_count is not None and retired_count != len(retired_events):
        errors.append(f"frame {frame_id}: retirement lifecycle census disagrees")
    active_frame = frame.get("active_frame") or {}
    published_snakes = _integer(active_frame.get("published_snake_count"))
    if published_snakes is not None and published_snakes != len(chains):
        errors.append(f"frame {frame_id}: published snake census disagrees")
    published_segments = _integer(active_frame.get("published_segment_count"))
    if published_segments is not None and published_segments != sum(len(chain["segments"]) for chain in chains.values()):
        errors.append(f"frame {frame_id}: published segment census disagrees")
    compact_final_join = frame.get("_compact_final_join") or {}
    if compact_final_join:
        final_fields = (
            "structural_cell_count", "final_tuple_count", "tuple_match_count",
            "node_match_count", "contributor_match_count", "orphan_count",
            "mismatch_count",
        )
        final_counts = {
            name: _integer(compact_final_join.get(name)) for name in final_fields
        }
        if any(name not in compact_final_join for name in final_fields):
            errors.append(f"frame {frame_id}: compact final-join census is incomplete")
        if any(value is None or value < 0 for value in final_counts.values()):
            errors.append(f"frame {frame_id}: compact final-join counts are malformed")
        for field in ("orphan_count", "mismatch_count"):
            if final_counts.get(field, 0) != 0:
                errors.append(f"frame {frame_id}: compact final-join {field} is nonzero")
        comparable = [
            final_counts.get(field)
            for field in (
                "structural_cell_count", "final_tuple_count", "tuple_match_count",
                "node_match_count", "contributor_match_count",
            )
            if final_counts.get(field) is not None
        ]
        if comparable and any(value != comparable[0] for value in comparable[1:]):
            errors.append(f"frame {frame_id}: compact final-join counts disagree")
    return {
        "frame": frame,
        "frame_id": frame_id,
        "chains": chains,
        "events": events,
        "active_events": active_events,
        "retired_events": retired_events,
        "node_stats": node_stats,
        "errors": errors,
        "claim_total": compact_claim_total if compact_claim_total is not None else sum(expected_claims.values()),
        "contributor_total": compact_contributor_total if compact_contributor_total is not None else sum(observed_claims.values()),
    }


def _validate_structural_receipt_identity(
    receipt: dict[str, Any], errors: list[str]
) -> bool:
    """Reject a structurally plausible receipt from a stale target/build.

    Structural conservation is meaningful only when the receipt names the
    exact headed target and its build manifest.  A matching measurement hash
    alone proves only that the bytes were copied consistently; it does not
    prove that those bytes came from the current Pieta Web build.
    """
    run = receipt.get("run")
    revision = receipt.get("revision")
    manifest = receipt.get("web_build_manifest")
    valid = True
    if not isinstance(run, dict):
        errors.append("receipt lacks run target identity")
        valid = False
    if not isinstance(revision, dict):
        errors.append("receipt lacks source revision identity")
        valid = False
    if not isinstance(manifest, dict):
        errors.append("receipt lacks web build manifest identity")
        valid = False
    if not isinstance(run, dict) or not isinstance(revision, dict) or not isinstance(manifest, dict):
        return False

    if run.get("target") != "web_wasm":
        errors.append("receipt target is not the headed web_wasm product")
        valid = False
    if run.get("mode") != "fixed_position_pieta_camera_turn":
        errors.append("receipt mode is not the fixed-position Pieta camera turn")
        valid = False
    if run.get("map") != "assets/a3d/frog_pieta_4x4.a3d":
        errors.append("receipt map is not the canonical frog_pieta_4x4.a3d target")
        valid = False
    if run.get("expected_mesh") != "pieta.akm":
        errors.append("receipt expected_mesh is not pieta.akm")
        valid = False
    if _integer(run.get("expected_material_namespace")) != PIETA_MATERIAL_NAMESPACE:
        errors.append("receipt material namespace is not Pieta namespace 2")
        valid = False
    if _integer(run.get("expected_material_key")) != PIETA_MATERIAL_KEY:
        errors.append("receipt material key is not Pieta key 200")
        valid = False

    head = revision.get("head")
    dirty_paths = revision.get("dirty_paths")
    if not isinstance(dirty_paths, list) or any(
        not isinstance(path, str) or not path for path in dirty_paths
    ):
        errors.append("receipt revision.dirty_paths is malformed")
        valid = False
    source_ref = manifest.get("source_ref")
    git_head = manifest.get("git_head")
    if not isinstance(head, str) or len(head) < 7:
        errors.append("receipt revision.head is missing")
        valid = False
    for field, value in (("source_ref", source_ref), ("git_head", git_head)):
        if not isinstance(value, str) or len(value) < 7:
            errors.append(f"web build manifest {field} is missing")
            valid = False
        elif isinstance(head, str) and value != head:
            errors.append(f"web build manifest {field} disagrees with revision.head")
            valid = False
    wasm_sha = manifest.get("wasm_sha256")
    if not isinstance(wasm_sha, str) or len(wasm_sha) != 64:
        errors.append("web build manifest wasm_sha256 is missing")
        valid = False
    server_sha = manifest.get("server_sha256")
    if not isinstance(server_sha, str) or len(server_sha) != 64:
        errors.append("web build manifest server_sha256 is missing")
        valid = False
    receipt_git_dirty = manifest.get("git_dirty")
    if not isinstance(receipt_git_dirty, bool):
        errors.append("web build manifest git_dirty is malformed")
        valid = False
    elif isinstance(dirty_paths, list) and bool(dirty_paths) != receipt_git_dirty:
        errors.append("receipt dirty_paths disagrees with web build manifest git_dirty")
        valid = False

    manifest_path = manifest.get("path")
    manifest_sha = manifest.get("sha256")
    if not isinstance(manifest_path, str) or not manifest_path:
        errors.append("web build manifest path is missing")
        valid = False
    elif not isinstance(manifest_sha, str) or len(manifest_sha) != 64:
        errors.append("web build manifest sha256 is missing")
        valid = False
    manifest_artifact: dict[str, Any] | None = None
    path: Path | None = None
    if isinstance(manifest_path, str) and manifest_path and isinstance(manifest_sha, str):
        path = ROOT / manifest_path
        if not path.is_file():
            errors.append(f"web build manifest artifact is missing: {path}")
            valid = False
        elif sha256(path) != manifest_sha:
            errors.append("web build manifest sha256 disagrees with artifact")
            valid = False
        else:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"web build manifest artifact is not valid JSON: {exc}")
                valid = False
            else:
                if not isinstance(parsed, dict):
                    errors.append("web build manifest artifact is not a JSON object")
                    valid = False
                else:
                    manifest_artifact = parsed

    if manifest_artifact is not None:
        parsed_source_ref = manifest_artifact.get("source_ref")
        parsed_git_head = manifest_artifact.get("git_head")
        parsed_git_dirty = manifest_artifact.get("git_dirty")
        parsed_web = manifest_artifact.get("web")
        parsed_server = manifest_artifact.get("server")
        parsed_wasm_sha = (
            parsed_web.get("index_wasm_sha256")
            if isinstance(parsed_web, dict)
            else None
        )
        parsed_server_sha = (
            parsed_server.get("sha256")
            if isinstance(parsed_server, dict)
            else None
        )
        parsed_fields = (
            ("source_ref", source_ref, parsed_source_ref),
            ("git_head", git_head, parsed_git_head),
            ("wasm_sha256", wasm_sha, parsed_wasm_sha),
            ("server_sha256", server_sha, parsed_server_sha),
        )
        for field, receipt_value, parsed_value in parsed_fields:
            if not isinstance(parsed_value, str) or not parsed_value:
                errors.append(f"web build manifest artifact {field} is malformed")
                valid = False
            elif receipt_value != parsed_value:
                errors.append(f"receipt {field} disagrees with parsed web build manifest")
                valid = False
        if not isinstance(parsed_git_dirty, bool):
            errors.append("web build manifest artifact git_dirty is malformed")
            valid = False
        else:
            if receipt_git_dirty != parsed_git_dirty:
                errors.append("receipt git_dirty disagrees with parsed web build manifest")
                valid = False
            if isinstance(dirty_paths, list) and bool(dirty_paths) != parsed_git_dirty:
                errors.append("revision dirty_paths disagrees with parsed web build manifest")
                valid = False
    return valid


def verify_structural_turn(receipt_path: Path) -> dict[str, Any]:
    """Prove ordered snake and segment conservation over every measured frame."""
    receipt = load_json(receipt_path)
    try:
        frames, measurement = _read_structural_frames(receipt)
    except (OSError, ValueError, KeyError) as exc:
        return {
            "schema": "fl4512.pieta_structural_turn_verification.v2",
            "receipt": str(receipt_path),
            "receipt_sha256": sha256(receipt_path),
            "measurement": receipt.get("structural_turn_measurement") or {},
            "frame_count": 0,
            "first_bridge_frame": None,
            "last_bridge_frame": None,
            "selected_claim_count": 0,
            "segment_contributor_count": 0,
            "explained_segment_change_count": 0,
            "typed_edit_witness_count": 0,
            "one_to_one_correspondence_pair_count": 0,
            "structural_to_temporal_glyph_transition_count": 0,
            "errors": [str(exc)],
            "diagnostics": {
                "continuous_runtime_session": False,
                "continuous_structural_frame_sequence": False,
                "sample_frames_join_continuous_measurement": False,
                "measurement_hash_matches_receipt": False,
                "build_artifact_identity_matches_receipt": False,
                "normal_runtime_pieta_presentation": False,
                "every_selected_claim_has_one_contributor": False,
                "ordered_snake_segment_conservation": False,
                "gpu_final_tuple_byte_identity": False,
            },
            "gates": {"gameplay_whole_engine_structural_pieta_path": False},
            "passed": False,
        }
    errors: list[str] = []
    build_identity_valid = _validate_structural_receipt_identity(receipt, errors)
    if measurement.get("io_error"):
        errors.append(f"structural measurement I/O failure: {measurement['io_error']}")
    pieta_runtime_rows = 0
    structural_line_rows = 0
    frames_by_bridge = {
        _integer(frame.get("bridge_frame")): frame
        for frame in frames
        if _integer(frame.get("bridge_frame")) is not None
    }
    for sample_index, sample in enumerate(receipt.get("samples", [])):
        snapshot = (sample or {}).get("runtime_prediction_snapshot") or {}
        sample_frame = frames_by_bridge.get(_integer(snapshot.get("bridge_frame")))
        compact_frame = (sample_frame or {}).get("schema") == \
            "fl4512.structural_turn_frame.compact.v1"
        claimed_cells = {
            _integer(cell.get("cell_index"))
            for cell in (sample_frame or {}).get("structural_cells", [])
            if isinstance(cell, dict) and _integer(cell.get("cell_index")) is not None
        }
        snapshot_width = _integer(snapshot.get("width"))
        if not compact_frame:
            for cell in (sample_frame or {}).get("structural_cells", []):
                if not isinstance(cell, dict):
                    continue
                structural_line_rows += 1
                final_tuple = cell.get("final_tuple") or {}
                if _integer(final_tuple.get("final_gid")) == 32:
                    errors.append(
                        f"sample {sample_index}: structural Pieta claim presented as space"
                    )
        for raw_row in snapshot.get("rows", []):
            row = row_object(snapshot, raw_row) if isinstance(raw_row, list) else raw_row
            if not isinstance(row, dict) or _integer(row.get("material_namespace")) != 2 \
                    or _integer(row.get("material_key")) != 200:
                continue
            pieta_runtime_rows += 1
            x = _integer(row.get("x"))
            y = _integer(row.get("y"))
            cell_index = (
                y * snapshot_width + x
                if x is not None and y is not None and snapshot_width is not None
                else _integer(row.get("cell_index"))
            )
            # A mesh-feature route is only a prediction. Structural ownership is
            # established by the same-frame carrier after claim arbitration.
            if cell_index in claimed_cells or (
                    compact_frame and _integer(row.get("role")) == 1):
                if compact_frame:
                    structural_line_rows += 1
                if compact_frame and _integer(row.get("presented_glyph_id")) == 32:
                    errors.append(
                        f"sample {sample_index}: structural Pieta claim presented as space"
                    )
    if pieta_runtime_rows == 0:
        errors.append("live samples contain no Pieta runtime rows")
    if structural_line_rows == 0:
        errors.append("live samples contain no visible Pieta structural line rows")
    bridge_frames = [_integer(frame.get("bridge_frame")) for frame in frames]
    if not frames:
        errors.append("measurement contains no structural frames")
    if any(frame_id is None for frame_id in bridge_frames):
        errors.append("measurement contains an invalid bridge frame")
    clean_bridge_frames = [frame_id for frame_id in bridge_frames if frame_id is not None]
    for before, after in zip(clean_bridge_frames, clean_bridge_frames[1:]):
        if after != before + 1:
            errors.append(f"bridge-frame gap {before}->{after}")
    if measurement.get("receipt_frame_count") is not None and _integer(
        measurement["receipt_frame_count"]
    ) != len(frames):
        errors.append("receipt frame_count disagrees with structural measurement")

    sample_bridge_frames = {
        _integer(sample.get("runtime_state", {}).get("bridge_frame"))
        for sample in receipt.get("samples", [])
    }
    sample_bridge_frames.discard(None)
    missing_sample_frames = sorted(sample_bridge_frames - set(clean_bridge_frames))
    if missing_sample_frames:
        errors.append(f"sample frames absent from continuous measurement: {missing_sample_frames}")

    # A sampled full snapshot is an independent same-run cross-check.  Its
    # bridge identity and exported census must agree with the continuous frame
    # at that identity; it cannot substitute for per-frame conservation.
    frame_by_bridge = {
        frame_id: frame for frame_id, frame in zip(bridge_frames, frames)
        if frame_id is not None
    }
    for sample_index, sample in enumerate(receipt.get("samples", [])):
        if not isinstance(sample, dict):
            continue
        runtime_frame = _integer((sample.get("runtime_state") or {}).get("bridge_frame"))
        snapshot = sample.get("structural_turn_snapshot")
        if not isinstance(snapshot, dict):
            continue
        snapshot_frame = _integer(snapshot.get("bridge_frame"))
        if snapshot_frame is None:
            errors.append(f"sample {sample_index}: structural snapshot lacks bridge_frame")
            continue
        if runtime_frame is not None and snapshot_frame != runtime_frame:
            errors.append(
                f"sample {sample_index}: structural snapshot bridge frame {snapshot_frame} "
                f"differs from runtime frame {runtime_frame}"
            )
        frame = frame_by_bridge.get(snapshot_frame)
        if frame is None:
            errors.append(
                f"sample {sample_index}: structural snapshot frame {snapshot_frame} "
                "is absent from continuous measurement"
            )
            continue
        if "carrier_valid" in snapshot and snapshot.get("carrier_valid") is not frame.get("carrier_valid"):
            errors.append(f"sample {sample_index}: sampled carrier validity disagrees")
        sampled_anchors = snapshot.get("anchor_feature_refs")
        if sampled_anchors is not None:
            if not isinstance(sampled_anchors, list):
                errors.append(f"sample {sample_index}: sampled anchor FeatureRefs are not a list")
            else:
                for anchor_index, anchor in enumerate(sampled_anchors):
                    _, anchor_errors = _complete_feature_ref(anchor)
                    errors.extend(
                        f"sample {sample_index} anchor {anchor_index}: {error}"
                        for error in anchor_errors
                    )
        expected_counts = {
            "event_count": len(frame.get("events") or []),
            "claim_count": len(frame.get("claims") or [])
            if isinstance(frame.get("claims"), list) else None,
            "structural_cell_count": len(frame.get("structural_cells") or [])
            if isinstance(frame.get("structural_cells"), list) else None,
            "contributor_node_count": len(frame.get("node_contributors") or [])
            if isinstance(frame.get("node_contributors"), list) else None,
        }
        compact_claims = frame.get("_compact_claims") or {}
        if expected_counts["claim_count"] is None and compact_claims:
            expected_counts["claim_count"] = _integer(
                compact_claims.get("claim_count", compact_claims.get("count"))
            )
        compact_final_join = frame.get("_compact_final_join") or {}
        if expected_counts["structural_cell_count"] is None and compact_final_join:
            expected_counts["structural_cell_count"] = _integer(
                compact_final_join.get("structural_cell_count")
            )
        if isinstance(frame.get("spacetime_nodes"), list):
            expected_counts["contributor_node_count"] = len(frame["spacetime_nodes"])
        for field, expected in expected_counts.items():
            actual = _integer(snapshot.get(field))
            if actual is not None and expected is not None and actual != expected:
                errors.append(
                    f"sample {sample_index}: {field} {actual} disagrees with "
                    f"continuous frame {expected}"
                )

    states: list[dict[str, Any]] = []
    for frame in frames:
        frame_errors: list[str] = []
        state = _frame_state(frame, frame_errors)
        errors.extend(frame_errors)
        states.append(state)

    claim_total = sum(state["claim_total"] for state in states)
    contributor_total = sum(state["contributor_total"] for state in states)
    explained_segment_change_total = 0
    correspondence_pair_total = 0
    typed_edit_witness_total = 0
    exact_to_final_transitions = 0
    for state in states:
        for cell in state["frame"].get("structural_cells") or []:
            node = cell.get("node") or {}
            if cell.get("spacetime_valid") and _integer(node.get("glyph_id", 0)):
                if _integer(node.get("glyph_id")) != _integer(
                    (cell.get("final_result") or {}).get("final_gid")
                ):
                    exact_to_final_transitions += 1

    def chain_for_id(token: Any, source: dict[str, dict[str, Any]], context: str) -> dict[str, Any] | None:
        return _chain_lookup(token, source, errors, context)

    def require_witness(event: dict[str, Any], kind: str, frame_id: int) -> dict[str, Any] | None:
        nonlocal typed_edit_witness_total
        value = _measured_witness(event, kind, frame_id, errors)
        if value is not None:
            typed_edit_witness_total += 1
        return value

    for index, state in enumerate(states):
        frame_id = state["frame_id"]
        current = state["chains"]
        prior = states[index - 1]["chains"] if index else {}
        active_events = state["active_events"]
        retired_events = state["retired_events"]
        accounted_prior: set[str] = set()
        accounted_current: set[str] = set()
        operation_keys: set[tuple[Any, ...]] = set()

        previous_active = states[index - 1]["active_events"] if index else {}
        previous_retired = states[index - 1]["retired_events"] if index else {}
        for ref_key, event in active_events.items():
            kind = _integer(event.get("kind"))
            previous_event = previous_active.get(ref_key)
            if previous_event is None and kind == 6:
                related_id = _id_token(event.get("related_semantic_chain_id"))
                previous_event = next(
                    (
                        candidate for candidate in previous_active.values()
                        if _id_token(candidate.get("semantic_chain_id")) == related_id
                    ),
                    None,
                )
            current_before = _integer(event.get("ordered_segment_hash_before"))
            if previous_event is not None:
                previous_after = _integer(previous_event.get("ordered_segment_hash_after"))
                if current_before is not None and previous_after is not None and current_before != previous_after:
                    errors.append(f"frame {frame_id}: ordered segment hash continuity diverges")
            elif kind == 1 and current_before not in (None, 0):
                errors.append(f"frame {frame_id}: BIRTH has non-empty prior ordered hash")
        for ref_key, event in retired_events.items():
            previous_event = previous_active.get(ref_key) or previous_retired.get(ref_key)
            current_before = _integer(event.get("ordered_segment_hash_before"))
            if previous_event is not None:
                previous_after = _integer(previous_event.get("ordered_segment_hash_after"))
                if current_before is not None and previous_after is not None and current_before != previous_after:
                    errors.append(f"frame {frame_id}: RETIRE ordered segment hash diverges")

        for ref_key, event in active_events.items():
            kind = _integer(event.get("kind"))
            event_name = _SNAKE_EVENT_NAMES.get(kind, "unknown")
            current_chain = current.get(ref_key)
            if current_chain is None:
                errors.append(f"frame {frame_id}: active event has no current chain")
                continue
            sequence = current_chain["sequence"]
            if kind == 1:
                if ref_key in prior:
                    errors.append(f"frame {frame_id}: BIRTH chain already existed")
                witness = require_witness(event, "birth", frame_id)
                if witness is not None:
                    witness_ids = _witness_ids(witness, "segment_ids")
                    counts = _witness_counts(witness)
                    if witness_ids is not None:
                        if witness_ids != _sequence_ids(sequence):
                            errors.append(f"frame {frame_id}: BIRTH witness does not enumerate current order")
                    elif counts != (0, len(sequence) + 1):
                        errors.append(f"frame {frame_id}: BIRTH witness counts disagree with current order")
                    if _EDIT_OPERATION_NAMES.get(_integer(witness.get("operation"))) == "birth" and _integer(
                        witness.get("measurement_kind")
                    ) != 2:
                        errors.append(f"frame {frame_id}: BIRTH witness is not SUPPORT_COUNT measured")
                accounted_current.add(ref_key)
            elif kind == 2:
                old_chain = prior.get(ref_key)
                if old_chain is None and not (index == 0 and kind == 2):
                    errors.append(f"frame {frame_id}: CONTINUE chain has no prior owner")
                elif old_chain is not None:
                    node_stat = state.get("node_stats", {}).get(ref_key) or {}
                    if (
                        node_stat.get("current", 0) <= 0
                        or node_stat.get("prior", 0) <= 0
                        or node_stat.get("mapped", 0) <= 0
                    ):
                        errors.append(
                            f"frame {frame_id}: CONTINUE node correspondence is missing"
                        )
                    old_sequence = old_chain["sequence"]
                    current_signature = _sequence_signature(sequence)
                    prior_signature = _sequence_signature(old_sequence)
                    current_ids = _sequence_ids(sequence)
                    prior_ids = _sequence_ids(old_sequence)
                    if current_ids != prior_ids:
                        if not _resample_sequence(
                            prior_ids, current_ids, event, frame_id, errors
                        ):
                            errors.append(f"frame {frame_id}: CONTINUE changed ordered segment IDs")
                    elif current_signature != prior_signature:
                        # Camera reprojection, relaxation, and source lifting
                        # may move endpoints. Stable segment ID plus ordinal
                        # remains the cross-frame identity; only an ordered
                        # ID/count edit requires a resample witness.
                        errors.append(f"frame {frame_id}: CONTINUE changed ordered segment ordinals")
                    accounted_prior.add(ref_key)
                    accounted_current.add(ref_key)
            elif kind in (3, 4):
                edit_name = "trim" if kind == 3 else "extend"
                old_chain = prior.get(ref_key)
                if old_chain is None:
                    errors.append(f"frame {frame_id}: {edit_name.upper()} chain has no prior owner")
                witness = require_witness(event, edit_name, frame_id)
                old_ids = _sequence_ids(old_chain["sequence"]) if old_chain else []
                new_ids = _sequence_ids(sequence)
                if witness is not None:
                    retained = _witness_ids(witness, "retained_ids")
                    prefix = _witness_ids(witness, "added_prefix_ids" if edit_name == "extend" else "removed_prefix_ids")
                    suffix = _witness_ids(witness, "added_suffix_ids" if edit_name == "extend" else "removed_suffix_ids")
                    prefix = prefix or []
                    suffix = suffix or []
                    if retained is None:
                        counts = _witness_counts(witness)
                        first = _integer(witness.get("first_ordinal"))
                        if counts is None or first is None or counts[0] != len(old_ids) + 1 or counts[1] != len(new_ids) + 1:
                            errors.append(f"frame {frame_id}: {edit_name.upper()} witness lacks ordered counts")
                        elif edit_name == "trim":
                            removed = len(old_ids) - len(new_ids)
                            valid_prefix = removed > 0 and new_ids == old_ids[removed:] and first == 0
                            valid_suffix = removed > 0 and new_ids == old_ids[:-removed] and first == len(old_ids)
                            if not (valid_prefix or valid_suffix):
                                errors.append(f"frame {frame_id}: TRIM is not an allowed prefix/suffix removal")
                        else:
                            added = len(new_ids) - len(old_ids)
                            valid_prefix = added > 0 and new_ids[added:] == old_ids and first == 0
                            valid_suffix = added > 0 and new_ids[:-added] == old_ids and first >= len(old_ids) + 1
                            if not (valid_prefix or valid_suffix):
                                errors.append(f"frame {frame_id}: EXTEND is not an allowed prefix/suffix addition")
                    elif edit_name == "trim":
                        if not retained or not (prefix or suffix) or old_ids != prefix + retained + suffix or new_ids != retained:
                            errors.append(f"frame {frame_id}: TRIM is not an allowed prefix/suffix removal")
                    elif not retained or not (prefix or suffix) or new_ids != prefix + retained + suffix or old_ids != retained:
                        errors.append(f"frame {frame_id}: EXTEND is not an allowed prefix/suffix addition")
                accounted_prior.add(ref_key)
                accounted_current.add(ref_key)
            elif kind in (5, 6):
                witness = require_witness(event, "split", frame_id)
                if witness is None:
                    continue
                partition = _topology_partition(
                    event, current, prior, frame_id, errors, "split"
                )
                if partition is None:
                    continue
                parent, retained, branch, _ = partition
                operation_key = ("split", tuple(sorted((parent["feature_ref_key"], retained["feature_ref_key"], branch["feature_ref_key"]))))
                if operation_key in operation_keys:
                    continue
                operation_keys.add(operation_key)
                parent_ids = _sequence_ids(parent["sequence"])
                retained_ids = _sequence_ids(retained["sequence"])
                branch_ids = _sequence_ids(branch["sequence"])
                witness_retained_ids = _witness_ids(witness, "retained_ids")
                witness_branch_ids = _witness_ids(witness, "branch_ids")
                if not retained_ids or not branch_ids:
                    errors.append(f"frame {frame_id}: SPLIT children are empty")
                elif parent_ids != retained_ids + branch_ids:
                    errors.append(f"frame {frame_id}: SPLIT partition does not conserve ordered parent")
                elif witness_retained_ids is not None and witness_retained_ids != retained_ids:
                    errors.append(f"frame {frame_id}: SPLIT retained witness disagrees with sequence")
                elif witness_branch_ids is not None and witness_branch_ids != branch_ids:
                    errors.append(f"frame {frame_id}: SPLIT branch witness disagrees with sequence")
                counts = _witness_counts(witness)
                first = _integer(witness.get("first_ordinal"))
                if counts is not None and (
                    counts[0] != len(parent_ids) + 1
                    or counts[1] not in (len(retained_ids) + 1, len(branch_ids) + 1)
                    or first != len(retained_ids)
                ):
                    errors.append(f"frame {frame_id}: SPLIT scalar witness disagrees with ordered partition")
                accounted_prior.add(parent["feature_ref_key"])
                accounted_current.update((retained["feature_ref_key"], branch["feature_ref_key"]))
            elif kind == 7:
                witness = require_witness(event, "merge", frame_id)
                if witness is None:
                    continue
                partition = _topology_partition(
                    event, current, prior, frame_id, errors, "merge"
                )
                if partition is None:
                    continue
                left, right, result, _ = partition
                if not left or not result:
                    continue
                if right is None:
                    old_ids = _sequence_ids(left["sequence"])
                    result_ids = _sequence_ids(result["sequence"])
                    counts = _witness_counts(witness)
                    if result_ids != old_ids and not (
                        len(result_ids) == len(old_ids) + 1
                        and result_ids[:-1] == old_ids
                    ):
                        errors.append(f"frame {frame_id}: MERGE closure is not an ordered extension")
                    if counts is not None and counts[1] != len(result_ids) + 1:
                        errors.append(f"frame {frame_id}: MERGE closure witness counts disagree")
                    accounted_prior.add(left["feature_ref_key"])
                    accounted_current.add(result["feature_ref_key"])
                    continue
                if left["feature_ref_key"] == right["feature_ref_key"]:
                    errors.append(f"frame {frame_id}: MERGE sources are not distinct")
                    continue
                operation_key = ("merge", left["feature_ref_key"], right["feature_ref_key"], result["feature_ref_key"])
                if operation_key in operation_keys:
                    continue
                operation_keys.add(operation_key)
                left_ids = _sequence_ids(left["sequence"])
                right_ids = _sequence_ids(right["sequence"])
                result_ids = _sequence_ids(result["sequence"])
                candidates = []
                for left_order in (left_ids, list(reversed(left_ids))):
                    for right_order in (right_ids, list(reversed(right_ids))):
                        candidates.append(left_order + right_order)
                if result_ids not in candidates:
                    errors.append(f"frame {frame_id}: MERGE is not an ordered concatenation")
                witness_left_ids = _witness_ids(witness, "left_ids")
                witness_right_ids = _witness_ids(witness, "right_ids")
                witness_result_ids = _witness_ids(witness, "result_ids")
                if witness_left_ids is not None and witness_left_ids not in (left_ids, list(reversed(left_ids))):
                    errors.append(f"frame {frame_id}: MERGE left witness disagrees with sequence")
                if witness_right_ids is not None and witness_right_ids not in (right_ids, list(reversed(right_ids))):
                    errors.append(f"frame {frame_id}: MERGE right witness disagrees with sequence")
                if witness_result_ids is not None and witness_result_ids != result_ids:
                    errors.append(f"frame {frame_id}: MERGE result witness disagrees with sequence")
                counts = _witness_counts(witness)
                if counts is not None and counts[1] != len(result_ids) + 1:
                    errors.append(f"frame {frame_id}: MERGE scalar witness count disagrees with result")
                accounted_prior.update((left["feature_ref_key"], right["feature_ref_key"]))
                accounted_current.add(result["feature_ref_key"])
            else:
                errors.append(f"frame {frame_id}: unsupported event kind {event_name}")

        for ref_key, event in retired_events.items():
            old_chain = prior.get(ref_key)
            if old_chain is None:
                errors.append(f"frame {frame_id}: RETIRE has no prior chain")
                continue
            witness = require_witness(event, "retire", frame_id)
            if witness is not None:
                witness_ids = _witness_ids(witness, "segment_ids")
                counts = _witness_counts(witness)
                if witness_ids is not None:
                    if witness_ids != _sequence_ids(old_chain["sequence"]):
                        errors.append(f"frame {frame_id}: RETIRE witness does not enumerate prior order")
                elif counts != (len(old_chain["sequence"]) + 1, 0):
                    errors.append(f"frame {frame_id}: RETIRE witness counts disagree with prior order")
                if _EDIT_OPERATION_NAMES.get(_integer(witness.get("operation"))) == "retire" and _integer(
                    witness.get("measurement_kind")
                ) != 2:
                    errors.append(f"frame {frame_id}: RETIRE witness is not SUPPORT_COUNT measured")
            accounted_prior.add(ref_key)

        if index == 0:
            for ref_key in current:
                initial_event = active_events.get(ref_key)
                initial_kind = _integer(initial_event.get("kind")) if initial_event else None
                if ref_key not in accounted_current and initial_kind != 2:
                    errors.append(f"frame {frame_id}: initial chain lacks BIRTH witness")
        else:
            for ref_key in current:
                if ref_key not in accounted_current:
                    errors.append(f"frame {frame_id}: current chain lacks typed conservation event")
            for ref_key in prior:
                if ref_key not in accounted_prior:
                    errors.append(f"frame {frame_id}: prior chain disappeared without typed event")

        for node in state["frame"].get("node_contributors") or []:
            current_rows = node.get("current") or []
            prior_rows = node.get("prior") or []
            current_by_id = {_id_token(row.get("semantic_segment_id")): row for row in current_rows if isinstance(row, dict)}
            prior_by_id = {_id_token(row.get("semantic_segment_id")): row for row in prior_rows if isinstance(row, dict)}
            correspondence_pair_total += max(
                0, len(set(current_by_id) & set(prior_by_id)) - (1 if None in current_by_id else 0)
            )

    behavior = receipt.get("fl4693_runtime_behavior_gate") or {}
    diagnostics = {
        "continuous_runtime_session": bool(behavior.get("continuous_session")),
        "continuous_structural_frame_sequence": bool(frames) and not any(
            message.startswith("bridge-frame gap") for message in errors
        ),
        "sample_frames_join_continuous_measurement": not missing_sample_frames,
        "measurement_hash_matches_receipt": bool(measurement["receipt_sha256_match"]),
        "build_artifact_identity_matches_receipt": build_identity_valid,
        "normal_runtime_pieta_presentation": pieta_runtime_rows > 0 and
            structural_line_rows > 0 and not any("Pieta" in message for message in errors),
        "every_selected_claim_has_one_contributor": claim_total > 0 and claim_total == contributor_total,
        "ordered_snake_segment_conservation": not errors,
        "gpu_final_tuple_byte_identity": behavior.get("final_tuple_gpu_readback_mismatch_bytes") == 0,
    }
    whole_engine_gate = all(diagnostics.values())
    return {
        "schema": "fl4512.pieta_structural_turn_verification.v2",
        "receipt": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "measurement": measurement,
        "frame_count": len(frames),
        "first_bridge_frame": clean_bridge_frames[0] if clean_bridge_frames else None,
        "last_bridge_frame": clean_bridge_frames[-1] if clean_bridge_frames else None,
        "selected_claim_count": claim_total,
        "segment_contributor_count": contributor_total,
        "explained_segment_change_count": explained_segment_change_total,
        "typed_edit_witness_count": typed_edit_witness_total,
        "one_to_one_correspondence_pair_count": correspondence_pair_total,
        "structural_to_temporal_glyph_transition_count": exact_to_final_transitions,
        "pieta_runtime_row_count": pieta_runtime_rows,
        "structural_line_row_count": structural_line_rows,
        "errors": errors[:100],
        "diagnostics": diagnostics,
        "gates": {"gameplay_whole_engine_structural_pieta_path": whole_engine_gate},
        "passed": whole_engine_gate,
    }


def verify(oracle_path: Path, receipt_path: Path) -> dict[str, Any]:
    oracle = load_json(oracle_path)
    if oracle.get("schema") == "fl4730.universal_exact_glyph_oracle.v1":
        return verify_universal(oracle_path, receipt_path)
    receipt = load_json(receipt_path)
    actual = expected_timepoints(receipt)
    expected = oracle.get("timepoints", [])
    labels_match = [p.get("label") for p in expected] == [p.get("label") for p in actual]
    row_total = 0
    scene_fact_matches = 0
    exact_matches = 0
    presented_matches = 0
    transition_expected = sum(
        int(t.get("predicted_changed_cell_count", 0)) for t in oracle.get("transitions", [])
    )
    transition_observed = 0
    for expected_point, actual_point in zip(expected, actual):
        actual_rows = {(r["x"], r["y"]): r for r in actual_point["rows"]}
        for row in expected_point.get("rows", []):
            row_total += 1
            observed = actual_rows.get((row["x"], row["y"]))
            if observed is None:
                continue
            if observed["scene_fact"] == row["scene_fact"]:
                scene_fact_matches += 1
            if observed["runtime_exact_glyph_id"] == row["expected_glyph_id"]:
                exact_matches += 1
            if observed["runtime_presented_glyph_id"] == row["expected_glyph_id"]:
                presented_matches += 1
    for transition in oracle.get("transitions", []):
        before_index = oracle["choreography"].index(transition["from"])
        after_index = oracle["choreography"].index(transition["to"])
        if before_index >= len(actual) or after_index >= len(actual):
            continue
        before_rows = {(r["x"], r["y"]): r for r in actual[before_index]["rows"]}
        after_rows = {(r["x"], r["y"]): r for r in actual[after_index]["rows"]}
        for predicted in transition.get("changed_rows", []):
            key = (predicted["x"], predicted["y"])
            old = before_rows.get(key)
            new = after_rows.get(key)
            if old and new and old["runtime_presented_glyph_id"] != new["runtime_presented_glyph_id"]:
                transition_observed += 1
    passed = (
        labels_match and row_total > 0 and scene_fact_matches == row_total
        and exact_matches == row_total and presented_matches == row_total
        and transition_expected > 0 and transition_observed == transition_expected
    )
    return {
        "schema": "fl4730.pieta_turn_exact_glyph_verification.v1",
        "oracle": str(oracle_path),
        "oracle_sha256": sha256(oracle_path),
        "receipt": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "predicted_before_verification_run": bool(oracle.get("predicted_before_verification_run")),
        "labels_match": labels_match,
        "expected_row_count": row_total,
        "scene_fact_matches": scene_fact_matches,
        "exact_glyph_matches": exact_matches,
        "presented_glyph_matches": presented_matches,
        "predicted_changed_cell_count": transition_expected,
        "observed_predicted_changed_cell_count": transition_observed,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--author", action="store_true")
    mode.add_argument("--author-complete", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--verify-fixed", action="store_true")
    mode.add_argument("--verify-complete", action="store_true")
    mode.add_argument("--verify-structural-turn", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--reference-receipt", action="append", type=Path, default=[])
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.freeze:
            result = freeze()
        elif args.author:
            if args.receipt is None:
                parser.error("--author requires --receipt")
            result = author(args.receipt)
        elif args.author_complete:
            if args.receipt is None:
                parser.error("--author-complete requires --receipt")
            result = author_complete(args.receipt, args.reference_receipt)
        elif args.verify_complete:
            if args.receipt is None or args.oracle is None:
                parser.error("--verify-complete requires --oracle and --receipt")
            result = verify_complete(args.oracle, args.receipt)
        elif args.verify_fixed:
            if args.receipt is None or args.oracle is None:
                parser.error("--verify-fixed requires --oracle and --receipt")
            result = verify_fixed_checkpoint(args.oracle, args.receipt)
        elif args.verify_structural_turn:
            if args.receipt is None:
                parser.error("--verify-structural-turn requires --receipt")
            result = verify_structural_turn(args.receipt)
        else:
            if args.receipt is None or args.oracle is None:
                parser.error("--verify requires --oracle and --receipt")
            result = verify(args.oracle, args.receipt)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"FL-4730 oracle failure: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in result if k in {
        "schema", "expected_row_count", "predicted_changed_cell_count",
        "observed_predicted_changed_cell_count", "matched_complete_row_count",
        "deterministic_prediction_row_count", "runtime_join_key_count", "gates", "passed"
    }}, indent=1, sort_keys=True))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

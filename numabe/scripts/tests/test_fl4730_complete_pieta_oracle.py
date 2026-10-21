from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ORACLE_SCRIPT = ROOT / "scripts/fl4730_pieta_turn_oracle.py"


def load_oracle_module():
    spec = importlib.util.spec_from_file_location("fl4730_oracle", ORACLE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def witness_chunk_fixture():
    rows = []
    for offset in (0, 32, 64):
        rows.append({"kind": 2, "semantic_chain_id": "000000000000002a",
                     "edit_witness_offset": offset, "edit_witness_total": 65,
                     "edit_witness_count": min(32, 65 - offset),
                     "edit_witnesses": [{"ordinal": i} for i in range(offset, min(offset + 32, 65))]})
    return rows


def test_witness_chunks_reassemble_once_without_dropping_order():
    oracle = load_oracle_module()
    errors = []
    result = oracle._reassemble_witness_events(witness_chunk_fixture(), 1, errors)
    assert not errors
    assert len(result) == 1
    assert result[0]["edit_witness_count"] == 65
    assert result[0]["edit_witnesses"] == [{"ordinal": i} for i in range(65)]
    legacy = {"kind": 2, "edit_witness_count": 0, "edit_witnesses": []}
    assert oracle._reassemble_witness_events([legacy], 1, errors) == [legacy]


def test_witness_chunks_fail_closed_on_transport_corruption():
    oracle = load_oracle_module()
    cases = []
    original = witness_chunk_fixture()
    cases.extend([original[:-1], original[1:], [original[0], original[0], *original[1:]],
                  [original[0], original[2], original[1]]])
    for field, value in (("semantic_chain_id", "different"), ("edit_witness_offset", 31),
                         ("edit_witness_total", 64), ("edit_witness_count", 31),
                         ("edit_witnesses", [])):
        changed = deepcopy(original)
        changed[1][field] = value
        cases.append(changed)
    missing = deepcopy(original)
    del missing[0]["edit_witness_total"]
    cases.append(missing)
    for rows in cases:
        errors = []
        oracle._reassemble_witness_events(rows, 1, errors)
        assert errors, rows


def synthetic_receipt() -> dict:
    fields = [
        "x",
        "y",
        "scene_frame",
        "material_namespace",
        "material_key",
        "role",
        "feature_ref",
        "route_family_mask",
        "ramp_bin",
        "density_bin",
        "direction_bin",
        "flow_bin",
        "edge_bin",
        "harri_shape6_valid",
        "harri_shape6_q0",
        "harri_shape6_q1",
        "harri_shape6_q2",
        "harri_shape6_q3",
        "harri_shape6_q4",
        "harri_shape6_q5",
        "glyph_id",
        "outcome",
        "rejection_reason",
        "topology_event",
        "presented_glyph_id",
        "fg_palette_index",
        "bg_palette_index",
    ]
    labels = [
        "baseline_hold",
        "turn_right",
        "post_turn_settle",
        "walk_forward",
        "post_walk_settle",
    ]
    samples = []
    for ordinal, label in enumerate(labels):
        row = [
            20 + ordinal,
            30,
            100 + ordinal,
            2,
            200,
            1,
            "9407495938802041138",
            3,
            1,
            0,
            255,
            255,
            255,
            1,
            32476,
            28396,
            57196,
            37745,
            20768,
            13596,
            631,
            1,
            0,
            0,
            631,
            96,
            101,
        ]
        samples.append(
            {
                "index": ordinal,
                "leg": label,
                "runtime_prediction_snapshot": {
                    "plane_valid": True,
                    "truncated": False,
                    "bridge_frame": 100 + ordinal,
                    "row_fields": fields,
                    "rows": [row],
                },
            }
        )
    return {
        "revision": {"head": "0123456789abcdef"},
        "web_build_manifest": {"source_ref": "0123456789abcdef"},
        "samples": samples,
    }


def test_complete_oracle_passes_all_four_owned_gates(tmp_path: Path) -> None:
    module = load_oracle_module()
    receipt_path = tmp_path / "authoring-receipt.json"
    receipt_path.write_text(json.dumps(synthetic_receipt()), encoding="utf-8")
    reference_path = tmp_path / "reference-receipt.json"
    reference_path.write_text(json.dumps(synthetic_receipt()), encoding="utf-8")

    oracle = module.author_complete(receipt_path, [reference_path])
    assert module.validate_complete_schema(oracle) == []
    assert oracle["corpus_definition"]["expected_timepoint_count"] == 5
    assert oracle["corpus_definition"]["expected_row_count"] == 5
    assert oracle["corpus_definition"]["authoring_receipt_count"] == 2
    assert oracle["pieta_scene_fact_hash"]

    oracle_path = tmp_path / "oracle.json"
    oracle_path.write_text(json.dumps(oracle, sort_keys=True), encoding="utf-8")
    verification = module.verify_complete(oracle_path, receipt_path)

    assert verification["passed"] is True, json.dumps(verification, indent=2)
    assert verification["matched_complete_row_count"] == 5
    assert verification["runtime_join_key_count"] == 5
    assert verification["gates"]["fl4730_oracle_schema_valid"] is True
    assert all(verification["gates"].values())
    assert verification["mismatches"] == []


def test_fixed_checkpoint_joins_prediction_and_tuple_before_turn(tmp_path: Path) -> None:
    module = load_oracle_module()
    sample = synthetic_receipt()["samples"][0]
    sample["leg"] = "authored_view_hold"
    sample["runtime_prediction_snapshot"]["exact_to_final_transition_count"] = 0
    sample["full_tuple_capture"] = {
        "scene_frame": sample["runtime_prediction_snapshot"]["bridge_frame"],
        "gpu_readback_byte_identical": True,
    }
    structural_frame = _structural_frame(
        sample["runtime_prediction_snapshot"]["bridge_frame"], 2
    )
    structural_path = tmp_path / "structural.sample-000.json"
    structural_path.write_text(json.dumps(structural_frame), encoding="utf-8")
    sample["structural_turn_snapshot"] = {
        "path": str(structural_path),
        "sha256": module.sha256(structural_path),
        "bridge_frame": structural_frame["bridge_frame"],
        "carrier_valid": True,
        "event_count": len(structural_frame["events"]),
        "claim_count": len(structural_frame["claims"]),
        "structural_cell_count": len(structural_frame["structural_cells"]),
        "contributor_node_count": len(structural_frame["node_contributors"]),
    }
    checkpoint_path = tmp_path / "fixed-pose-checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({
            "schema": "fl4731.fixed_pose_checkpoint.v1",
            "sample": sample,
        }),
        encoding="utf-8",
    )
    oracle_path = tmp_path / "frozen-oracle.json"
    oracle_path.write_text(json.dumps(module.freeze()), encoding="utf-8")

    verification = module.verify_fixed_checkpoint(oracle_path, checkpoint_path)

    assert verification["passed"] is True, json.dumps(verification, indent=2)
    assert verification["label"] == "authored_view_hold"
    assert verification["expected_row_count"] == 1
    assert verification["exact_glyph_matches"] == 1
    assert verification["presented_glyph_matches"] == 1
    assert verification["structural_claim_count"] == 2
    assert verification["structural_contributor_count"] == 2
    assert verification["structural_errors"] == []
    assert all(verification["gates"].values())

    sample["structural_turn_snapshot"]["sha256"] = "0" * 64
    checkpoint_path.write_text(
        json.dumps({
            "schema": "fl4731.fixed_pose_checkpoint.v1",
            "sample": sample,
        }),
        encoding="utf-8",
    )
    rejected = module.verify_fixed_checkpoint(oracle_path, checkpoint_path)
    assert rejected["passed"] is False
    assert rejected["gates"]["full_structural_snapshot_hash_matches"] is False


def test_fixed_checkpoint_rejects_incomplete_full_structural_carrier(
    tmp_path: Path,
) -> None:
    module = load_oracle_module()
    sample = synthetic_receipt()["samples"][0]
    sample["leg"] = "authored_view_hold"
    frame = sample["runtime_prediction_snapshot"]["bridge_frame"]
    sample["runtime_prediction_snapshot"]["exact_to_final_transition_count"] = 0
    sample["full_tuple_capture"] = {
        "scene_frame": frame,
        "gpu_readback_byte_identical": True,
    }
    structural_frame = _structural_frame(frame, 2)
    structural_frame.pop("observation_rejection_overflow")
    structural_path = tmp_path / "structural.incomplete.json"
    structural_path.write_text(json.dumps(structural_frame), encoding="utf-8")
    sample["structural_turn_snapshot"] = {
        "path": str(structural_path),
        "sha256": module.sha256(structural_path),
        "bridge_frame": frame,
        "carrier_valid": True,
        "event_count": len(structural_frame["events"]),
        "claim_count": len(structural_frame["claims"]),
        "structural_cell_count": len(structural_frame["structural_cells"]),
        "contributor_node_count": len(structural_frame["node_contributors"]),
    }
    checkpoint_path = tmp_path / "fixed-pose-checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({
            "schema": "fl4731.fixed_pose_checkpoint.v1",
            "sample": sample,
        }),
        encoding="utf-8",
    )
    oracle_path = tmp_path / "frozen-oracle.json"
    oracle_path.write_text(json.dumps(module.freeze()), encoding="utf-8")

    rejected = module.verify_fixed_checkpoint(oracle_path, checkpoint_path)

    assert rejected["passed"] is False
    assert rejected["gates"]["full_structural_carrier_and_joins_valid"] is False
    assert any(
        "observation rejection overflow status is not false" in error
        for error in rejected["structural_errors"]
    )


def test_runtime_snapshot_exports_topology_event_after_stable_indices() -> None:
    source = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(
        encoding="utf-8"
    )
    assert '"\\"topology_event\\",\\"policy_abstained_family_count\\"' in source
    assert "(unsigned int)fact.topology_event" in source


def test_runtime_snapshot_appends_policy_count_and_mask_without_shifting_rows() -> None:
    """Phase-2 abstention fields append after topology_event in both snapshot surfaces."""
    source = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(
        encoding="utf-8"
    )
    # This literal is the established suffix through FL-4730. Requiring the
    # old fields immediately before the abstention pair catches an insertion
    # or reorder that would shift every downstream row index.
    row_fields_markers = (
        '"\\"outcome\\",\\"rejection_reason\\",',
        '"\\"topology_event\\",\\"policy_abstained_family_count\\",',
        '"\\"policy_abstained_family_mask\\"],',
    )
    positions = [source.index(marker) for marker in row_fields_markers]
    assert positions == sorted(positions)
    assert all(source.count(marker) == 1 for marker in row_fields_markers)

    topology_arg = "(unsigned int)fact.topology_event,"
    count_arg = (
        "(unsigned int)g_fl4131_harri_gpu_bridge."
        "fl4693_exact_cell_policy_abstained_count[idx],"
    )
    mask_arg = (
        "(unsigned int)g_fl4131_harri_gpu_bridge."
        "fl4693_exact_cell_policy_abstained_mask[idx]))"
    )
    topology_index = source.index(topology_arg)
    count_index = source.index(count_arg, topology_index)
    mask_index = source.index(mask_arg, count_index)
    assert topology_index < count_index < mask_index


def test_xterm_rgba_matches_product_shader_examples() -> None:
    module = load_oracle_module()
    assert module.xterm_rgba(196) == [255, 0, 0, 255]
    assert module.xterm_rgba(46) == [0, 255, 0, 255]
    assert module.xterm_rgba(21) == [0, 0, 255, 255]


def _feature_ref(chain_id: str = "0000000000000005") -> dict:
    return {
        "world_generation": 1,
        "surface": {
            "world_generation": 1,
            "owner_kind": 1,
            "owner_x": 0,
            "owner_y": 0,
            "anchor_u_q": 1,
            "anchor_v_q": 2,
        },
        "mesh_instance_uid": "0000000000000003",
        "material_key": 200,
        "depth_owner": "0000000000000004",
        "dispatch_surface": 1,
        "role": 1,
        "feature_kind": 1,
        "aspect_region_id": 1,
        "semantic_chain_id": chain_id,
    }


def _segment(feature_ref: dict, ordinal: int, segment_id: str, start: int, end: int) -> dict:
    return {
        "feature_ref": feature_ref,
        "child_surface": feature_ref["surface"],
        "child_primitive_id": 7,
        "child_local_feature_id": ordinal,
        "semantic_segment_id": segment_id,
        "chain_phase0_q16": ordinal * 32767,
        "chain_phase1_q16": (ordinal + 1) * 32767,
        "clipped_mask8": "0000000000000003",
        "object_local_endpoints_q24_8": [start, 0, 0, end, 0, 0],
        "projected_endpoints_q24_8": [start, 0, end, 0],
    }


def test_producer_provenance_is_validated_but_does_not_split_feature_identity():
    oracle = load_oracle_module()
    legacy = _feature_ref()
    current = deepcopy(legacy)
    current["provenance"] = {"producer_kind": 1,
        "mesh_instance_uid": current.pop("mesh_instance_uid"),
        "depth_owner": current.pop("depth_owner")}
    changed_producer = deepcopy(current)
    changed_producer["provenance"] = {"producer_kind": 2,
        "mesh_instance_uid": "0000000000000000", "depth_owner": "0000000000000000"}
    for ref in (legacy, current, changed_producer):
        key, errors = oracle._complete_feature_ref(ref)
        assert not errors
        assert key == oracle._feature_ref_key(legacy)
        assert oracle._feature_ref_sort_key(ref) == oracle._feature_ref_sort_key(legacy)
    for field in ("owner_x", "owner_y", "anchor_u_q", "anchor_v_q"):
        other_surface = deepcopy(current)
        other_surface["surface"][field] += 1
        assert oracle._feature_ref_key(other_surface) != oracle._feature_ref_key(current)
    for field, value in (("producer_kind", 0), ("mesh_instance_uid", "0"),
                         ("depth_owner", None)):
        malformed = deepcopy(current)
        malformed["provenance"][field] = value
        assert oracle._complete_feature_ref(malformed)[1]
    conflicting = deepcopy(current)
    conflicting["mesh_instance_uid"] = "0000000000000099"
    assert oracle._complete_feature_ref(conflicting)[1]


def test_feature_sort_uses_native_chain_before_material_order():
    oracle = load_oracle_module()
    first = _feature_ref("0000000000000005")
    second = _feature_ref("0000000000000006")
    first["material_key"] = 300
    second["material_key"] = 100
    assert oracle._feature_ref_sort_key(first) < oracle._feature_ref_sort_key(second)


def _event(feature_ref: dict, kind: int, witness: dict | None = None) -> dict:
    value = {
        "kind": kind,
        "feature_ref_complete": True,
        "semantic_chain_id": feature_ref["semantic_chain_id"],
        "related_semantic_chain_id": "0000000000000000",
        "ordered_segment_hash_before": "0000000000000022",
        "ordered_segment_hash_after": "0000000000000022",
        "feature_ref": feature_ref,
        "participant_count": 1,
        "participants": [feature_ref],
    }
    if witness is not None:
        value["witness"] = witness
        value["edit_witness_count"] = 1
    return value


def _structural_frame(
    frame: int,
    event_kind: int,
    segments: list[dict] | None = None,
    prior_segments: list[dict] | None = None,
) -> dict:
    feature_ref = {
        "world_generation": 1,
        "surface": {
            "world_generation": 1,
            "owner_kind": 1,
            "owner_x": 0,
            "owner_y": 0,
            "anchor_u_q": 1,
            "anchor_v_q": 2,
        },
        "mesh_instance_uid": "0000000000000003",
        "material_key": 200,
        "depth_owner": "0000000000000004",
        "dispatch_surface": 1,
        "role": 1,
        "feature_kind": 1,
        "aspect_region_id": 1,
        "semantic_chain_id": "0000000000000005",
    }
    feature_ref = _feature_ref()
    if segments is None:
        segments = [
            _segment(feature_ref, 0, "0000000000000006", 0, 256),
            _segment(feature_ref, 1, "0000000000000007", 256, 512),
        ]
    if segments:
        feature_ref = segments[0]["feature_ref"]
    claims = [{"cell_index": 10 + index, **segment} for index, segment in enumerate(segments)]
    prior = [] if frame == 1 else [
        dict(segment) for segment in (prior_segments if prior_segments is not None else segments)
    ]
    birth_witness = {
        "operation": 1, "measurement_kind": 2, "valid": True,
        "first_ordinal": 0, "count_before": 0,
        "count_after": len(segments) + 1,
        "measured_value": len(segments), "threshold_value": 1,
    }
    event = _event(
        feature_ref,
        event_kind,
        birth_witness if frame == 1 and event_kind == 1 else None,
    )
    if frame == 1 and event_kind == 1:
        event["ordered_segment_hash_before"] = "0000000000000000"
    return {
        "schema": "fl4512.structural_turn_snapshot.v1",
        "bridge_frame": frame,
        "carrier_valid": True,
        "active_stroke_event_count": 1,
        "active_stroke_event_overflow": False,
        "sequence_cell_claim_overflow": False,
        "observation_rejection_overflow": False,
        "spacetime_node_order_invalid": False,
        "claims": claims,
        "conflicts": [],
        "claim_dispositions": [{
            "claim_index": index,
            "cell_index": 10 + index,
            "semantic_segment_id": segment["semantic_segment_id"],
            "disposition": 1,
            "feature_ref": feature_ref,
        } for index, segment in enumerate(segments)],
        "events": [event],
        "lifecycle": {"active_snake_count": 1, "retired_snake_count": 0, "event_count": 1, "reset_reason": 0},
        "active_frame": {"published_snake_count": 1, "published_segment_count": len(segments)},
        "structural_cells": [{
            "cell_index": 10 + index,
            "spacetime_valid": True,
            "chain_id": feature_ref["semantic_chain_id"],
            "feature_ref": feature_ref,
            "node": {"glyph_id": 631},
            "final_result": {
                "valid": True, "selected_role": 1, "final_gid": 631,
                "sequence_chain_id": feature_ref["semantic_chain_id"],
            },
            "final_tuple": {"valid": True, "final_gid": 631},
        } for index in range(len(segments))],
        "node_contributors": [{
            "cell_index": 10 + index,
            "chain_id": feature_ref["semantic_chain_id"],
            "phase": 2,
            "node_index": index,
            "current": [segment],
            "prior": [
                prior_segment for prior_segment in prior
                if prior_segment.get("semantic_segment_id") == segment.get("semantic_segment_id")
            ],
        } for index, segment in enumerate(segments)],
    }


def test_structural_turn_requires_every_adjacent_frame_and_segment(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    frames = [_structural_frame(1, 1), _structural_frame(2, 2)]
    measurement = tmp_path / "structural.jsonl"
    measurement.write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8"
    )
    receipt_path = _receipt_for_frames(module, tmp_path, frames)

    verification = module.verify_structural_turn(receipt_path)

    assert verification["passed"] is True
    assert verification["gates"] == {
        "gameplay_whole_engine_structural_pieta_path": True
    }
    assert verification["diagnostics"]["normal_runtime_pieta_presentation"] is True

    for field, expected_error in (
        ("observation_rejection_overflow", "observation rejection overflow"),
        ("spacetime_node_order_invalid", "spacetime node order status"),
    ):
        mutant = deepcopy(frames)
        mutant[0].pop(field)
        result = module.verify_structural_turn(
            _receipt_for_frames(module, tmp_path, mutant)
        )
        assert result["passed"] is False
        assert any(expected_error in error for error in result["errors"])
    assert verification["selected_claim_count"] == 4
    assert verification["segment_contributor_count"] == 4
    assert verification["errors"] == []

    frames[1]["node_contributors"][0]["current"][0]["semantic_segment_id"] = "0000000000000007"
    frames[1]["node_contributors"][1]["current"][0]["semantic_segment_id"] = "0000000000000006"
    frames[1]["claims"][0]["semantic_segment_id"] = "0000000000000007"
    frames[1]["claims"][1]["semantic_segment_id"] = "0000000000000006"
    measurement.write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["structural_turn_measurement"]["sha256"] = module.sha256(measurement)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    rejected = module.verify_structural_turn(receipt_path)
    assert rejected["passed"] is False
    assert any("CONTINUE changed ordered segment IDs" in error for error in rejected["errors"])


def test_structural_turn_rejects_missing_joins_tonal_io_and_stale_identity(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    base = [_structural_frame(1, 1), _structural_frame(2, 2)]

    missing_cells = deepcopy(base)
    for frame in missing_cells:
        frame.pop("structural_cells")
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, missing_cells))
    assert result["passed"] is False
    assert any("lacks structural_cells join rows" in error for error in result["errors"])

    missing_node = deepcopy(base)
    for frame in missing_node:
        frame["structural_cells"][0].pop("node")
        frame["structural_cells"][0]["spacetime_valid"] = False
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, missing_node))
    assert result["passed"] is False
    assert any("node join is missing" in error for error in result["errors"])

    missing_tuple = deepcopy(base)
    for frame in missing_tuple:
        frame["structural_cells"][0].pop("final_tuple")
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, missing_tuple))
    assert result["passed"] is False
    assert any("final tuple join is missing" in error for error in result["errors"])

    ordinary_pieta = _receipt_for_frames(module, tmp_path, deepcopy(base))
    result = module.verify_structural_turn(ordinary_pieta)
    assert result["passed"] is True

    carrier_space = deepcopy(base)
    for frame in carrier_space:
        frame["structural_cells"][0]["final_tuple"]["final_gid"] = 32
    result = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, carrier_space)
    )
    assert result["passed"] is False
    assert result["gates"] == {
        "gameplay_whole_engine_structural_pieta_path": False
    }
    assert result["diagnostics"]["normal_runtime_pieta_presentation"] is False
    assert any("structural Pieta claim presented as space" in error for error in result["errors"])

    io_failed = _receipt_for_frames(module, tmp_path, deepcopy(base))
    io_receipt = json.loads(io_failed.read_text(encoding="utf-8"))
    io_receipt["structural_turn_measurement"]["io_error"] = "export failed"
    io_failed.write_text(json.dumps(io_receipt), encoding="utf-8")
    result = module.verify_structural_turn(io_failed)
    assert result["passed"] is False
    assert any("measurement I/O failure" in error for error in result["errors"])

    stale = _receipt_for_frames(module, tmp_path, deepcopy(base))
    stale_manifest = tmp_path / ".web" / "slot_manifest.json"
    stale_manifest.write_text(json.dumps({"source_ref": "stale-build"}), encoding="utf-8")
    result = module.verify_structural_turn(stale)
    assert result["passed"] is False
    assert any("manifest sha256 disagrees" in error for error in result["errors"])


def test_structural_receipt_identity_rejects_receipt_and_manifest_payload_mutants(
    tmp_path: Path,
) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    base = [_structural_frame(1, 1), _structural_frame(2, 2)]

    def fresh() -> tuple[Path, dict, Path]:
        receipt_path = _receipt_for_frames(module, tmp_path, deepcopy(base))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest_path = tmp_path / ".web" / "slot_manifest.json"
        return receipt_path, receipt, manifest_path

    receipt_mutants = (
        ("mode", lambda receipt, _manifest: receipt["run"].update(
            mode="camera_motion_attempted"
        )),
        ("source_ref", lambda receipt, _manifest: receipt["web_build_manifest"].update(
            source_ref="fedcba9876543210"
        )),
        ("git_head", lambda receipt, _manifest: receipt["web_build_manifest"].update(
            git_head="fedcba9876543210"
        )),
        ("wasm_sha256", lambda receipt, _manifest: receipt["web_build_manifest"].update(
            wasm_sha256="d" * 64
        )),
        ("server_sha256", lambda receipt, _manifest: receipt["web_build_manifest"].update(
            server_sha256="e" * 64
        )),
        ("git_dirty", lambda receipt, _manifest: receipt["web_build_manifest"].update(
            git_dirty=True
        )),
        ("dirty_paths", lambda receipt, _manifest: receipt["revision"].update(
            dirty_paths=["foreign-change.txt"]
        )),
        ("malformed_dirty_paths", lambda receipt, _manifest: receipt["revision"].update(
            dirty_paths="foreign-change.txt"
        )),
    )
    for field, mutate in receipt_mutants:
        receipt_path, receipt, manifest_path = fresh()
        mutate(receipt, manifest_path)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = module.verify_structural_turn(receipt_path)
        assert result["passed"] is False, field

    manifest_mutants = (
        ("source_ref", lambda payload: payload.update(source_ref="fedcba9876543210")),
        ("git_head", lambda payload: payload.update(git_head="fedcba9876543210")),
        ("wasm_sha256", lambda payload: payload["web"].update(index_wasm_sha256="d" * 64)),
        ("server_sha256", lambda payload: payload["server"].update(sha256="e" * 64)),
        ("git_dirty", lambda payload: payload.update(git_dirty="false")),
    )
    for field, mutate in manifest_mutants:
        receipt_path, receipt, manifest_path = fresh()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(payload)
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        receipt["web_build_manifest"]["sha256"] = module.sha256(manifest_path)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = module.verify_structural_turn(receipt_path)
        assert result["passed"] is False, field


def test_structural_receipt_identity_accepts_consistently_dirty_candidate(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    frames = [_structural_frame(1, 1), _structural_frame(2, 2)]
    receipt_path = _receipt_for_frames(module, tmp_path, frames)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest_path = tmp_path / ".web" / "slot_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["git_dirty"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    receipt["revision"]["dirty_paths"] = ["scripts/fl4730_pieta_turn_oracle.py"]
    receipt["web_build_manifest"]["git_dirty"] = True
    receipt["web_build_manifest"]["sha256"] = module.sha256(manifest_path)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert module.verify_structural_turn(receipt_path)["passed"] is True


def test_first_measured_continue_is_a_complete_baseline(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    frames = [_structural_frame(1, 2), _structural_frame(2, 2)]
    receipt = _receipt_for_frames(module, tmp_path, frames)
    assert module.verify_structural_turn(receipt)["passed"] is True

    frames[1]["node_contributors"][0]["current"][0]["semantic_segment_id"] = "0000000000000008"
    frames[1]["claims"][0]["semantic_segment_id"] = "0000000000000008"
    rejected = _receipt_for_frames(module, tmp_path, frames)
    result = module.verify_structural_turn(rejected)
    assert result["passed"] is False
    assert any("CONTINUE changed ordered segment IDs" in error for error in result["errors"])

    ordinal_mutant = [_structural_frame(1, 1), _structural_frame(2, 2)]
    ordinal_mutant[1]["node_contributors"][1]["current"][0]["ordinal"] = 3
    ordinal_mutant[1]["node_contributors"][1]["current"][0]["child_local_feature_id"] = 3
    ordinal_mutant[1]["claims"][1]["ordinal"] = 3
    ordinal_mutant[1]["claims"][1]["child_local_feature_id"] = 3
    ordinal_result = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, ordinal_mutant)
    )
    assert ordinal_result["passed"] is False
    assert any("CONTINUE changed ordered segment ordinals" in error for error in ordinal_result["errors"])


def test_scalar_runtime_witnesses_prove_resample_trim_and_extend(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref()
    original = [
        _segment(feature_ref, 0, "0000000000000006", 0, 256),
        _segment(feature_ref, 1, "0000000000000007", 256, 512),
    ]
    moved = [dict(original[0]), dict(original[1])]
    moved[0]["object_local_endpoints_q24_8"] = [0, 0, 0, 300, 0, 0]
    moving = [_structural_frame(1, 1, original), _structural_frame(2, 2, moved, original)]
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, moving))["passed"] is True

    inserted = original[:1] + [_segment(feature_ref, 1, "0000000000000008", 128, 192)] + [
        dict(original[1], ordinal=2, child_local_feature_id=2)
    ]
    resample = [_structural_frame(1, 1, original), _structural_frame(2, 2, inserted, original)]
    resample[1]["events"][0]["edit_witness_count"] = 1
    resample[1]["events"][0]["edit_witnesses"] = [{
        "operation": 2, "measurement_kind": 1, "valid": True,
        "first_ordinal": 0, "count_before": 3, "count_after": 4,
        "measured_value": 1, "threshold_value": 1,
    }]
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, resample))["passed"] is True

    bad_resample = deepcopy(resample)
    bad_resample[1]["events"][0]["edit_witnesses"] = []
    bad_resample[1]["events"][0]["edit_witness_count"] = 0
    rejected = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, bad_resample))
    assert rejected["passed"] is False
    assert any("resample witness" in error for error in rejected["errors"])

    trimmed = [dict(original[1])]
    trim = [_structural_frame(1, 1, original), _structural_frame(2, 3, trimmed, original)]
    trim[1]["events"][0]["edit_witness_count"] = 1
    trim[1]["events"][0]["edit_witnesses"] = [{
        "operation": 5, "measurement_kind": 4, "valid": True,
        "first_ordinal": 0, "count_before": 3, "count_after": 2,
        "measured_value": 1, "threshold_value": 1,
    }]
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, trim))["passed"] is True

    extended = original + [_segment(feature_ref, 2, "0000000000000008", 512, 768)]
    extend = [_structural_frame(1, 1, original), _structural_frame(2, 4, extended, original)]
    extend[1]["events"][0]["edit_witness_count"] = 1
    extend[1]["events"][0]["edit_witnesses"] = [{
        "operation": 6, "measurement_kind": 4, "valid": True,
        "first_ordinal": 3, "count_before": 3, "count_after": 4,
        "measured_value": 1, "threshold_value": 1,
    }]
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, extend))["passed"] is True


def _runtime_pieta_snapshot() -> dict:
    return {
        "bridge_frame": 1,
        "width": 160,
        "rows": [
            {
                "x": 10, "y": 0,
                "material_namespace": 2, "material_key": 200, "role": 1,
                "presented_glyph_id": 631, "bg_palette_index": 0,
            },
            {
                "x": 20, "y": 0,
                "material_namespace": 2, "material_key": 200, "role": 6,
                "presented_glyph_id": 631, "bg_palette_index": 0,
            },
        ]
    }


def _receipt_for_frames(module, tmp_path: Path, frames: list[dict]) -> Path:
    measurement = tmp_path / "structural.jsonl"
    measurement.write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8"
    )
    manifest_path = tmp_path / ".web" / "slot_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({
            "source_ref": "0123456789abcdef",
            "git_head": "0123456789abcdef",
            "git_dirty": False,
            "web": {"index_wasm_sha256": "b" * 64},
            "server": {"sha256": "c" * 64},
        }),
        encoding="utf-8",
    )
    receipt = {
        "run": {
            "target": "web_wasm",
            "mode": "fixed_position_pieta_camera_turn",
            "map": "assets/a3d/frog_pieta_4x4.a3d",
            "expected_mesh": "pieta.akm",
            "expected_material_namespace": 2,
            "expected_material_key": 200,
        },
        "revision": {"head": "0123456789abcdef", "dirty_paths": []},
        "web_build_manifest": {
            "path": ".web/slot_manifest.json",
            "sha256": module.sha256(manifest_path),
            "source_ref": "0123456789abcdef",
            "git_head": "0123456789abcdef",
            "git_dirty": False,
            "wasm_sha256": "b" * 64,
            "server_sha256": "c" * 64,
        },
        "samples": [],
        "structural_turn_measurement": {
            "path": "structural.jsonl",
            "sha256": module.sha256(measurement),
            "frame_count": len(frames),
        },
        "fl4693_runtime_behavior_gate": {
            "continuous_session": True,
            "final_tuple_gpu_readback_mismatch_bytes": 0,
        },
    }
    for frame in frames:
        snapshot = _runtime_pieta_snapshot()
        snapshot["bridge_frame"] = frame["bridge_frame"]
        if frame.get("schema") != "fl4512.structural_turn_frame.compact.v1" and not any(
                cell.get("cell_index") == 10
                for cell in frame.get("structural_cells", [])
                if isinstance(cell, dict)):
            snapshot["rows"][0]["presented_glyph_id"] = 32
        receipt["samples"].append({
            "runtime_state": {"bridge_frame": frame["bridge_frame"]},
            "runtime_prediction_snapshot": snapshot,
        })
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


def test_structural_turn_rejects_crossing_missing_witness_and_degenerate_rows(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    base = [_structural_frame(1, 1), _structural_frame(2, 2)]

    prior_rows = [
        dict(base[1]["node_contributors"][0]["prior"][0]),
        dict(base[1]["node_contributors"][1]["prior"][0]),
    ]
    base[1]["node_contributors"][0]["prior"] = [dict(row) for row in prior_rows]
    base[1]["node_contributors"][1]["prior"] = [dict(row) for row in prior_rows]
    base[1]["node_contributors"][0]["current"][0]["prior_segment_id"] = "0000000000000007"
    base[1]["node_contributors"][1]["current"][0]["prior_segment_id"] = "0000000000000006"
    crossing = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, base))
    assert crossing["passed"] is False
    assert any("crosses" in error for error in crossing["errors"])

    missing = [_structural_frame(1, 1), _structural_frame(2, 2)]
    missing[0]["events"][0].pop("witness")
    missing[0]["events"][0].pop("edit_witness_count")
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, missing))
    assert result["passed"] is False
    assert any("lacks typed witness" in error for error in result["errors"])

    duplicate = [_structural_frame(1, 1), _structural_frame(2, 2)]
    duplicate[1]["node_contributors"][1]["current"][0]["semantic_segment_id"] = "0000000000000006"
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, duplicate))
    assert result["passed"] is False
    assert any("duplicate segment" in error for error in result["errors"])

    degenerate = [_structural_frame(1, 1), _structural_frame(2, 2)]
    degenerate[1]["node_contributors"][0]["current"][0]["object_local_endpoints_q24_8"] = [0, 0, 0, 0, 0, 0]
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, degenerate))
    assert result["passed"] is False
    assert any("zero-length" in error for error in result["errors"])

    missing_endpoint = [_structural_frame(1, 1), _structural_frame(2, 2)]
    missing_endpoint[1]["node_contributors"][0]["current"][0].pop("object_local_endpoints_q24_8")
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, missing_endpoint))
    assert result["passed"] is False
    assert any("missing object_local_endpoints" in error for error in result["errors"])

    malformed_ref = [_structural_frame(1, 1), _structural_frame(2, 2)]
    malformed_ref[1]["node_contributors"][0]["current"][0]["feature_ref"]["material_key"] = "garbage"
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, malformed_ref))
    assert result["passed"] is False
    assert any("material_key" in error for error in result["errors"])


def test_hex_serialized_semantic_ids_are_joined_as_fixed_width_hex(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref("000000000000000a")
    segments = [
        _segment(feature_ref, 0, "000000000000000b", 0, 256),
        _segment(feature_ref, 1, "000000000000000c", 256, 512),
    ]
    frames = [_structural_frame(1, 1, segments), _structural_frame(2, 2, segments, segments)]
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, frames))
    assert result["passed"] is True, result["errors"]


def test_hex_ordered_hashes_child_surface_and_missing_correspondence_fail_closed(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref("000000000000000a")
    segments = [
        _segment(feature_ref, 0, "000000000000000b", 0, 256),
        _segment(feature_ref, 1, "000000000000000c", 256, 512),
    ]
    frames = [_structural_frame(1, 1, segments), _structural_frame(2, 2, segments, segments)]
    frames[0]["events"][0]["ordered_segment_hash_after"] = "000000000000000a"
    frames[1]["events"][0]["ordered_segment_hash_before"] = "000000000000000a"
    frames[1]["events"][0]["ordered_segment_hash_after"] = "000000000000000a"
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, frames))["passed"] is True

    surface_mutant = deepcopy(frames)
    surface_mutant[1]["node_contributors"][0]["current"][0]["child_surface"] = {
        **surface_mutant[1]["node_contributors"][0]["current"][0]["child_surface"],
        "owner_x": 9,
    }
    rejected = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, surface_mutant))
    assert rejected["passed"] is False
    assert any("orphaned" in error or "lack claims" in error for error in rejected["errors"])

    correspondence_mutant = deepcopy(frames)
    for node in correspondence_mutant[1]["node_contributors"]:
        node["prior"] = []
    rejected = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, correspondence_mutant)
    )
    assert rejected["passed"] is False
    assert any("correspondence is missing" in error for error in rejected["errors"])


def test_compact_spacetime_nodes_require_monotone_final_join(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref()
    segments = [
        _segment(feature_ref, 0, "0000000000000006", 0, 256),
        _segment(feature_ref, 1, "0000000000000007", 256, 512),
    ]
    frames = [_structural_frame(1, 1, segments), _structural_frame(2, 2, segments, segments)]
    for frame_index, frame in enumerate(frames):
        frame["spacetime_nodes"] = [{
            "semantic_chain_id": feature_ref["semantic_chain_id"],
            "cell_index": 10 + index,
            "node_index": index,
            "u_q16": index * 100,
            "temporal_corresponded": frame_index > 0,
            "prior_node_index": index if frame_index > 0 else None,
            "prior_u_q16": index * 100 if frame_index > 0 else None,
            "feature_ref": feature_ref,
            "glyph_id": 631,
            "final_gid": 631,
            "tuple_final_gid": 631,
            "tuple_valid": True,
            "tuple_decision_valid": True,
            "contributor_count": 1,
        } for index in range(2)]
    receipt = _receipt_for_frames(module, tmp_path, frames)
    assert module.verify_structural_turn(receipt)["passed"] is True

    frames[1]["spacetime_nodes"][1]["prior_node_index"] = 0
    rejected = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, frames))
    assert rejected["passed"] is False
    assert any("crosses or reuses" in error for error in rejected["errors"])


def test_full_snapshot_preserves_multi_ref_anchor_without_singular_owner(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    frames = [_structural_frame(1, 1), _structural_frame(2, 2)]
    anchor_refs = [_feature_ref("0000000000000005"), _feature_ref("0000000000000009")]
    for frame in frames:
        cell = frame["structural_cells"][0]
        cell.pop("feature_ref")
        cell["anchor_kind"] = 1
        cell["spacetime_phase"] = 1
        cell["chain_id"] = "0000000000000000"
        cell["anchor_feature_refs"] = anchor_refs
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, frames))
    assert result["passed"] is True, result["errors"]


def test_structural_turn_rejects_unmeasured_edit_and_census_mutants(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    mutants = []

    unmeasured_split = [_structural_frame(1, 1), _structural_frame(2, 5)]
    unmeasured_split[1]["events"][0]["topology_event"] = {
        "kind": 5, "magnitude_measured": False,
    }
    unmeasured_split[1]["events"][0]["witness"] = {
        "type": "split", "measured": True,
    }
    mutants.append(unmeasured_split)

    bad_trim = [_structural_frame(1, 1), _structural_frame(2, 3)]
    bad_trim[1]["events"][0]["witness"] = {
        "type": "trim", "measured": True,
        "retained_ids": ["0000000000000006"],
    }
    mutants.append(bad_trim)

    bad_extend = [_structural_frame(1, 1), _structural_frame(2, 4)]
    bad_extend[1]["events"][0]["witness"] = {
        "type": "extend", "measured": True,
        "retained_ids": ["0000000000000007"],
    }
    mutants.append(bad_extend)

    bad_merge = [_structural_frame(1, 1), _structural_frame(2, 7)]
    bad_merge[1]["events"][0]["witness"] = {
        "type": "merge", "measured": True,
        "source_chain_ids": ["0000000000000005"],
    }
    mutants.append(bad_merge)

    bad_retire = [_structural_frame(1, 1), _structural_frame(2, 8)]
    bad_retire[1]["events"][0]["witness"] = {
        "type": "retire", "measured": True,
        "segment_ids": ["0000000000000006"],
    }
    mutants.append(bad_retire)

    for frames in mutants:
        result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, frames))
        assert result["passed"] is False

    event_count = [_structural_frame(1, 1), _structural_frame(2, 2)]
    event_count[1]["lifecycle"]["event_count"] = 2
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, event_count))
    assert result["passed"] is False
    assert any("event count disagrees" in error for error in result["errors"])

    reset = [_structural_frame(1, 1), _structural_frame(2, 2)]
    reset[1]["lifecycle"]["reset_reason"] = 1
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, reset))
    assert result["passed"] is False
    assert any("reset reason" in error for error in result["errors"])

    retirement = [_structural_frame(1, 1), _structural_frame(2, 2)]
    retirement[1]["lifecycle"]["retired_snake_count"] = 1
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, retirement))
    assert result["passed"] is False
    assert any("retirement lifecycle census" in error for error in result["errors"])

    collapse = [_structural_frame(1, 1), _structural_frame(2, 2)]
    collapse[1]["node_contributors"][0]["current"].pop()
    collapse[1]["claims"].pop()
    collapse[1]["claim_dispositions"].pop()
    collapse[1]["structural_cells"].pop()
    result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, collapse))
    assert result["passed"] is False
    assert any("CONTINUE changed ordered segment IDs" in error for error in result["errors"])


def test_structural_turn_accepts_measured_trim_and_extend(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref()
    original = [
        _segment(feature_ref, 0, "0000000000000006", 0, 256),
        _segment(feature_ref, 1, "0000000000000007", 256, 512),
    ]
    trimmed = [_segment(feature_ref, 0, "0000000000000006", 0, 256)]
    trim = [_structural_frame(1, 1, original), _structural_frame(2, 3, trimmed, original)]
    trim[1]["events"][0]["witness"] = {
        "type": "trim", "measured": True,
        "removed_prefix_ids": [], "retained_ids": ["0000000000000006"],
        "removed_suffix_ids": ["0000000000000007"],
    }
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, trim))["passed"] is True

    extended = original + [_segment(feature_ref, 2, "0000000000000008", 512, 768)]
    extend = [_structural_frame(1, 1, original), _structural_frame(2, 4, extended, original)]
    extend[1]["events"][0]["witness"] = {
        "type": "extend", "measured": True,
        "added_prefix_ids": [], "retained_ids": [
            "0000000000000006", "0000000000000007"
        ], "added_suffix_ids": ["0000000000000008"],
    }
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, extend))["passed"] is True


def test_structural_turn_accepts_measured_split_merge_and_retire(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    parent_ref = _feature_ref("0000000000000005")
    branch_ref = _feature_ref("0000000000000009")
    parent_segments = [
        _segment(parent_ref, 0, "0000000000000006", 0, 256),
        _segment(parent_ref, 1, "0000000000000007", 256, 512),
    ]
    retained = _structural_frame(1, 1, parent_segments)
    split = _structural_frame(
        2, 5, [_segment(parent_ref, 0, "0000000000000006", 0, 256)], parent_segments
    )
    branch_segment = _segment(branch_ref, 0, "0000000000000007", 256, 512)
    split["claims"].append({"cell_index": 12, **branch_segment})
    split["claim_dispositions"].append({
        "claim_index": 1, "cell_index": 12,
        "semantic_segment_id": branch_segment["semantic_segment_id"],
        "disposition": 1, "feature_ref": branch_ref,
    })
    split["node_contributors"].append({
        "cell_index": 12, "chain_id": branch_ref["semantic_chain_id"],
        "phase": 2, "node_index": 1, "current": [branch_segment],
        "prior": [parent_segments[1]],
    })
    split["structural_cells"].append({
        "cell_index": 12, "spacetime_valid": True,
        "chain_id": branch_ref["semantic_chain_id"], "feature_ref": branch_ref,
        "node": {"glyph_id": 631},
        "final_result": {
            "valid": True, "selected_role": 1, "final_gid": 631,
            "sequence_chain_id": branch_ref["semantic_chain_id"],
        },
        "final_tuple": {"valid": True, "final_gid": 631},
    })
    split_witness = {
        "operation": 7, "measurement_kind": 3, "valid": True,
        "first_ordinal": 1, "count_before": 3, "count_after": 2,
        "measured_value": 1, "threshold_value": 1,
    }
    split["events"] = [
        _event(parent_ref, 5, split_witness), _event(branch_ref, 6, split_witness)
    ]
    split["events"][0]["related_semantic_chain_id"] = branch_ref["semantic_chain_id"]
    split["events"][1]["related_semantic_chain_id"] = parent_ref["semantic_chain_id"]
    split["events"][0]["participants"] = [parent_ref, branch_ref]
    split["events"][1]["participants"] = [branch_ref, parent_ref]
    split["events"][0]["participant_count"] = split["events"][1]["participant_count"] = 2
    for event in split["events"]:
        event["topology_event"] = {"kind": 5, "magnitude_measured": True}
    split["active_stroke_event_count"] = split["lifecycle"]["event_count"] = 2
    split["lifecycle"]["active_snake_count"] = 2
    split["active_frame"] = {"published_snake_count": 2, "published_segment_count": 2}
    assert module.verify_structural_turn(_receipt_for_frames(module, tmp_path, [retained, split]))["passed"] is True

    left = _structural_frame(1, 1, [_segment(parent_ref, 0, "0000000000000006", 0, 256)])
    right = _structural_frame(1, 1, [branch_segment])
    right["claims"][0]["cell_index"] = 12
    right["claim_dispositions"][0]["cell_index"] = 12
    right["node_contributors"][0]["cell_index"] = 12
    right["structural_cells"][0]["cell_index"] = 12
    merged_before = deepcopy(left)
    merged_before["claims"].extend(right["claims"])
    merged_before["claim_dispositions"].extend([
        {**row, "claim_index": row["claim_index"] + 1} for row in right["claim_dispositions"]
    ])
    merged_before["node_contributors"].extend(right["node_contributors"])
    merged_before["structural_cells"].extend(right["structural_cells"])
    merged_before["events"].extend(right["events"])
    merged_before["active_stroke_event_count"] = merged_before["lifecycle"]["event_count"] = 2
    merged_before["lifecycle"]["active_snake_count"] = 2
    merged_before["active_frame"] = {"published_snake_count": 2, "published_segment_count": 2}
    merged = _structural_frame(2, 7, parent_segments, parent_segments)
    merge_witness = {
        "operation": 8, "measurement_kind": 4, "valid": True,
        "first_ordinal": 1, "count_before": 3, "count_after": 3,
        "measured_value": 1, "threshold_value": 1,
    }
    merged["events"][0] = _event(parent_ref, 7, merge_witness)
    merged["events"][0]["related_semantic_chain_id"] = branch_ref["semantic_chain_id"]
    merged["events"][0]["participants"] = [parent_ref, branch_ref]
    merged["events"][0]["participant_count"] = 2
    merged["events"][0]["topology_event"] = {"kind": 6, "magnitude_measured": True}
    merge_result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, [merged_before, merged]))
    assert merge_result["passed"] is True, merge_result["errors"]

    retired = _structural_frame(1, 1, parent_segments)
    retired_after = _structural_frame(2, 8, [], parent_segments)
    retired_after["events"][0]["witness"] = {
        "operation": 9, "measurement_kind": 2, "valid": True,
        "first_ordinal": 0, "count_before": 3, "count_after": 0,
        "measured_value": 2, "threshold_value": 1,
    }
    retired_after["lifecycle"]["active_snake_count"] = 0
    retired_after["lifecycle"]["retired_snake_count"] = 1
    retired_after["active_frame"] = {"published_snake_count": 0, "published_segment_count": 0}
    retire_result = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, [retired, retired_after]))
    assert retire_result["passed"] is True, retire_result["errors"]


def test_compact_conservation_surface_uses_counts_hashes_and_ordered_snakes(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    feature_ref = _feature_ref()
    segments = [
        _segment(feature_ref, 0, "0000000000000006", 0, 256),
        _segment(feature_ref, 1, "0000000000000007", 256, 512),
    ]
    compact = []
    for frame, kind in ((1, 1), (2, 2)):
        event = _event(
            feature_ref, kind,
            {"type": "birth", "measured": True,
             "segment_ids": [item["semantic_segment_id"] for item in segments]}
            if frame == 1 else None,
        )
        event["ordered_segment_hash_before"] = (
            "0000000000000000" if frame == 1 else "0000000000000022"
        )
        if frame == 1:
            event.pop("witness")
            event.pop("edit_witness_count")
            event["edit_witness_count"] = 1
            event["edit_witnesses"] = [{
                "operation": 1, "measurement_kind": 2, "valid": True,
                "first_ordinal": 0, "count_before": 0,
                    "count_after": len(segments) + 1, "measured_value": 0,
                "threshold_value": 0,
            }]
        compact.append({
                "schema": "fl4512.structural_turn_frame.compact.v1",
            "bridge_frame": frame,
            "carrier_valid": True,
            "active_stroke_observation_valid": True,
            "materialization_attempted": True,
            "materialization_complete": True,
            "materialization_failure": 0,
            "active_stroke_event_count": 1,
            "active_stroke_event_overflow": False,
            "sequence_cell_claim_overflow": False,
            "observation_rejection_overflow": False,
            "spacetime_node_order_invalid": False,
                "lifecycle": {
                    "active_snake_count": 1, "retired_snake_count": 0,
                    "event_count": 1, "reset_reason": 0, "capacity_failure": False,
            },
            "snakes": [{
                "feature_ref": feature_ref,
                "semantic_chain_id": feature_ref["semantic_chain_id"],
                "ordered_segments": segments,
                "prior_ordered_segments": [] if frame == 1 else segments,
                "event": event,
            }],
            "claim_contributor_join": {
                "claim_count": 2, "contributor_count": 2,
                "claim_hash": "claim-hash", "contributor_hash": "contributor-hash",
                "orphan_count": 0, "duplicate_count": 0, "mismatch_count": 0,
            },
            "final_join": {
                "structural_cell_count": 2, "final_tuple_count": 2,
                "tuple_match_count": 2, "node_match_count": 2,
                "contributor_match_count": 2, "orphan_count": 0,
                "mismatch_count": 0,
            },
                "active_frame": {
                    "published_snake_count": 1,
                    "published_segment_count": len(segments),
                    "failure": 0,
                },
        })
    receipt_path = _receipt_for_frames(module, tmp_path, compact)
    verification = module.verify_structural_turn(receipt_path)
    assert verification["passed"] is True, verification["errors"]
    assert verification["selected_claim_count"] == 4

    compact[1]["claim_contributor_join"]["contributor_hash"] = ""
    rejected = module.verify_structural_turn(_receipt_for_frames(module, tmp_path, compact))
    assert rejected["passed"] is False
    assert any("contributor hash is missing" in error for error in rejected["errors"])

    missing_overflow = deepcopy(compact)
    missing_overflow[0].pop("observation_rejection_overflow")
    rejected = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, missing_overflow)
    )
    assert rejected["passed"] is False
    assert any("observation rejection overflow status is not false" in error
               for error in rejected["errors"])

    missing_node_order = deepcopy(compact)
    missing_node_order[0].pop("spacetime_node_order_invalid")
    rejected = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, missing_node_order)
    )
    assert rejected["passed"] is False
    assert any("spacetime node order status is not false" in error
               for error in rejected["errors"])

    invalid_node_order = deepcopy(compact)
    invalid_node_order[0]["spacetime_node_order_invalid"] = True
    rejected = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, invalid_node_order)
    )
    assert rejected["passed"] is False
    assert any("spacetime node order status is not false" in error
               for error in rejected["errors"])

    true_overflow = deepcopy(compact)
    true_overflow[0]["observation_rejection_overflow"] = True
    rejected = module.verify_structural_turn(
        _receipt_for_frames(module, tmp_path, true_overflow)
    )
    assert rejected["passed"] is False
    assert any("observation rejection overflow status is not false" in error
               for error in rejected["errors"])


def test_sampled_full_snapshot_is_joined_to_continuous_frame_census(tmp_path: Path) -> None:
    module = load_oracle_module()
    module.ROOT = tmp_path
    frames = [_structural_frame(1, 1), _structural_frame(2, 2)]
    receipt_path = _receipt_for_frames(module, tmp_path, frames)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["samples"] = [{
        "runtime_state": {"bridge_frame": 1},
        "runtime_prediction_snapshot": _runtime_pieta_snapshot(),
        "structural_turn_snapshot": {
            "bridge_frame": 1,
            "carrier_valid": True,
            "event_count": 1,
            "claim_count": 2,
            "structural_cell_count": 2,
            "contributor_node_count": 2,
        },
    }]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert module.verify_structural_turn(receipt_path)["passed"] is True

    receipt["samples"][0]["structural_turn_snapshot"]["bridge_frame"] = 2
    rejected = tmp_path / "sample-mismatch.json"
    rejected.write_text(json.dumps(receipt), encoding="utf-8")
    result = module.verify_structural_turn(rejected)
    assert result["passed"] is False
    assert any("differs from runtime frame" in error for error in result["errors"])

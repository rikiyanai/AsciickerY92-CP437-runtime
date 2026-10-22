"""Focused FL-4359 profile/manifest contract tests.

These tests exercise runner contracts only. They do not launch Godot, Colyseus,
or a rasterizer, and they do not claim a renderer result.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_fl4359_headed_movement.py"


def _load_runner():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    name = "fl4359_profile_manifest_runner"
    spec = importlib.util.spec_from_file_location(name, RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _profile_manifest(
    *,
    profile_id: str = "rect_16x29_ortho",
    projection: str = "ortho",
    native=(16, 29),
    presentation=(640, 360),
    ansi=(40, 20),
    ortho_scale=7.5,
) -> dict:
    hashes = [hashlib.sha256(f"atlas-{index}".encode()).hexdigest() for index in range(3)]
    return {
        "schema": "fl4359.presentation_profile.v1",
        "profile_id": profile_id,
        "projection": projection,
        "native_cell_size": {"w": native[0], "h": native[1]},
        "presentation_size": {"w": presentation[0], "h": presentation[1]},
        "ansi_size": {"w": ansi[0], "h": ansi[1]},
        "atlas_identity": {
            "schema": "fl4359.native_atlas_identity.v1",
            "sources": [
                {
                    "path": f"res://atlas-{index}.png",
                    "hash_kind": "decoded_rgba8",
                    "dimensions": {"w": 2, "h": 2},
                    "sha256": digest,
                }
                for index, digest in enumerate(hashes)
            ],
        },
        "ortho_scale": ortho_scale,
    }


def _frame_trace(
    manifest: dict,
    *,
    field_sha256: str | None = None,
    ortho_scale=7.5,
    frame_index: int = 0,
) -> dict:
    projection = manifest["projection"]
    is_ortho = projection == "ortho"
    field_hash = field_sha256 or "f" * 64
    return {
        "final_paint_field": {
            "schema": "fl4359.final_paint_field.v1",
            "status": "valid",
            "raw_path": "final_paint_field/frame_00000_request_00000001.bin",
            "raw_sha256": field_hash,
            "frame_index": frame_index,
            "sequence_frame": frame_index,
            "actual_image_size": list(manifest["presentation_size"].values()),
            "cell_size": list(manifest["native_cell_size"].values()),
            "presentation_size": manifest["presentation_size"],
            "semantic_ansi_size": manifest["ansi_size"],
            "atlas_identity": manifest["atlas_identity"],
            "projection_kind": 0 if is_ortho else 1,
            "projection_mode": 1 if is_ortho else 2,
        },
        "frame_index": frame_index,
        "sequence_frame": frame_index,
        "camera_projection_mode": 1 if is_ortho else 0,
        "projection_mode": 1 if is_ortho else 2,
        "ortho_scale": ortho_scale if projection == "ortho" else None,
    }


def _receipt(manifest: dict, **overrides) -> dict:
    value = {"frame_trace": _frame_trace(manifest)}
    value.update(overrides)
    return value


def _profile_args(manifest: dict) -> argparse.Namespace:
    return argparse.Namespace(
        presentation_profile=manifest["profile_id"],
        presentation_profile_manifest=manifest,
        exact_pose_sweep=False,
        camera_mode="plane_air_ortho",
        glyph_matrix_cell_px=16,
    )


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arm(tmp_path: Path, *, label: str = "single", policy: dict | None = None) -> dict:
    frame_ids = [0, 1, 2, 3, 4]
    poses = [{"x": index, "y": 48, "z": -336 - index} for index in frame_ids]
    field_sha256 = [hashlib.sha256(f"field-{index}".encode()).hexdigest() for index in frame_ids]
    artifacts = []
    for index, role in enumerate(
        (
            "native",
            "full",
            "bg_only",
            "fg_only",
            "fg_highlight",
            "glyph_change_highlight",
            "fg_glyph_change_highlight",
        )
    ):
        path = tmp_path / f"{label}-{role}.gif"
        frames = [Image.new("RGBA", (8, 8), (index * 20, 0, 0, 255)) for index in range(5)]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=60, loop=0)
        artifacts.append(
            {
                "role": role,
                "path": str(path),
                "sha256": _hash(path),
                "frame_count": 5,
                "dimensions": {"w": 8, "h": 8},
                "frame_ids": frame_ids,
                "poses": poses,
                "capture_id": f"capture-{label}",
                "profile_id": "profile-square",
                "mode_id": f"mode-{label}",
                "field_sha256": field_sha256,
            }
        )
    arm = {
        "capture_id": f"capture-{label}",
        "frame_ids": frame_ids,
        "poses": poses,
        "profile_id": "profile-square",
        "mode_id": f"mode-{label}",
        "field_sha256": field_sha256,
        "artifacts": artifacts,
    }
    if policy is not None:
        arm["applied_policy"] = policy
    return arm


def _manifest(arm: dict, *, eligibility="actual_field", arm_kind="single_arm") -> dict:
    return {
        "schema": "fl4359.presentation_artifact_manifest.v1",
        "eligibility": eligibility,
        "arm_kind": arm_kind,
        "arms": {"single": arm},
        "comparisons": [],
    }


def _ab_manifest(tmp_path: Path) -> dict:
    arm_a = _arm(tmp_path, label="A")
    arm_b = _arm(tmp_path, label="B")
    # A/B source fields are allowed to differ: the comparison is precisely
    # where a mode change may alter field bytes. Their poses/profile/grid must
    # still match.
    arm_b["profile_id"] = arm_a["profile_id"]
    for artifact in arm_b["artifacts"]:
        artifact["profile_id"] = arm_a["profile_id"]
    comparisons = []
    for index, role in enumerate(("ab_sidebyside", "ab_fg_highlight")):
        path = tmp_path / f"{role}.gif"
        frames = [Image.new("RGBA", (8, 8), (index * 40, frame * 20, 0, 255)) for frame in range(5)]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=60, loop=0)
        comparisons.append(
            {
                "role": role,
                "path": str(path),
                "sha256": _hash(path),
                "frame_count": 5,
                "dimensions": {"w": 8, "h": 8},
                "frame_ids": arm_a["frame_ids"],
                "poses": arm_a["poses"],
                "profile_id": arm_a["profile_id"],
                "source_arms": ["A", "B"],
                "source_capture_ids": [arm_a["capture_id"], arm_b["capture_id"]],
                "source_modes": [arm_a["mode_id"], arm_b["mode_id"]],
                "source_field_sha256": [arm_a["field_sha256"], arm_b["field_sha256"]],
            }
        )
    policy = {
        "status": "applied",
        "mode": "CC",
        "fg_sampling": "continuous",
        "bg_sampling": "continuous",
        "bg_owner": "water",
    }
    for arm in (arm_a, arm_b):
        arm["native_viewport_source"] = {
            "status": "available",
            "owner": "same_frame_viewport_capture",
            "frame_ids": [0, 1, 2, 3, 4],
            "artifact_path": next(
                row["path"] for row in arm["artifacts"] if row["role"] == "native"
            ),
        }
    arm_a["applied_policy"] = policy
    arm_b["applied_policy"] = policy
    return {
        "schema": "fl4359.presentation_artifact_manifest.v1",
        "eligibility": "actual_field",
        "arm_kind": "ab",
        "arms": {"A": arm_a, "B": arm_b},
        "comparisons": comparisons,
    }


def test_profile_manifest_accepts_rectangular_orthographic_tuple() -> None:
    module = _load_runner()
    manifest = _profile_manifest()
    spec, reason = module._presentation_profile_parse(_profile_args(manifest))
    assert reason == ""
    assert spec is not None
    assert spec.native_cell_size == (16, 29)
    assert spec.projection == "ortho"
    receipt = _receipt(manifest)
    contract = module._presentation_profile_receipt_contract(receipt, _profile_args(manifest))
    assert contract["pass"] is True, contract


def test_profile_receipt_requires_actual_ortho_scale_and_ignores_expected_aliases() -> None:
    module = _load_runner()
    manifest = _profile_manifest()
    receipt = _receipt(manifest, expected_projection="ortho", presentation_profile=manifest["profile_id"])
    receipt["frame_trace"].pop("ortho_scale")
    contract = module._presentation_profile_receipt_contract(receipt, _profile_args(manifest))
    assert contract["pass"] is False
    assert "missing_frame_trace_camera_contract" not in contract["actual_errors"]
    assert any("ortho_scale" in value for value in contract["actual_errors"])


def test_profile_receipt_rejects_incompatible_native_dimensions() -> None:
    module = _load_runner()
    manifest = _profile_manifest()
    manifest["native_cell_size"] = {"w": 32, "h": 32}
    receipt = _receipt(_profile_manifest())
    contract = module._presentation_profile_receipt_contract(receipt, _profile_args(manifest))
    assert contract["pass"] is False
    assert "runtime_native_cell_size" in contract["failed_checks"]


def test_profile_contract_reads_all_five_production_jitter_frame_headers(tmp_path) -> None:
    module = _load_runner()
    manifest = _profile_manifest()
    capture = tmp_path / "jitter_capture.jsonl"
    rows = [
        {
            "row_type": "fl4359_jitter_frame",
            "frame_id": frame_id,
            "frame_trace": _frame_trace(
                manifest,
                field_sha256=hashlib.sha256(f"field-{frame_id}".encode()).hexdigest(),
                frame_index=frame_id,
            ),
        }
        for frame_id in range(5)
    ]
    capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    contract = module._presentation_profile_receipt_contract(
        {}, _profile_args(manifest), capture_path=capture
    )
    assert contract["pass"] is True, contract
    assert sorted(row["frame_id"] for row in contract["actual_rows"]) == list(range(5))


def test_profile_contract_rejects_missing_production_jitter_frame_header(tmp_path) -> None:
    module = _load_runner()
    manifest = _profile_manifest()
    capture = tmp_path / "jitter_capture.jsonl"
    rows = [
        {
            "row_type": "fl4359_jitter_frame",
            "frame_id": frame_id,
            "frame_trace": _frame_trace(manifest),
        }
        for frame_id in range(4)
    ]
    capture.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    contract = module._presentation_profile_receipt_contract(
        {}, _profile_args(manifest), capture_path=capture
    )
    assert contract["pass"] is False
    assert "actual_profile_frames_must_be_exactly_0_to_4" in contract["actual_errors"]


def test_profile_preflight_rejects_rectangular_application_until_owner_exists() -> None:
    module = _load_runner()
    # Importing the pinned fixture keeps the canonical five pose/time guards in
    # one owner while this test changes only the expected profile declaration.
    test_path = ROOT / "scripts" / "tests" / "test_fl4359_pinned_exact_pose_sweep_runner.py"
    spec = importlib.util.spec_from_file_location("pinned_fixture", test_path)
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    sys.modules["pinned_fixture"] = fixture
    spec.loader.exec_module(fixture)
    manifest = _profile_manifest()
    args = fixture._sweep_args(
        presentation_profile=manifest["profile_id"],
        presentation_profile_manifest=manifest,
        camera_mode="plane_air_ortho",
        exact_camera_projection="ortho",
        glyph_matrix_cell_px=16,
    )
    result = module._exact_pose_sweep_preflight(
        args, effective_jitter_capture_frame_limit=5, godot_timeout_seconds=300.0
    )
    assert result["ok"] is False
    assert "presentation_profile_application_supported" in result["failed_checks"]


def test_named_manifest_accepts_single_actual_field_arm_without_comparison_roles(tmp_path) -> None:
    module = _load_runner()
    arm = _arm(tmp_path)
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is True, gate
    assert gate["actual_field_eligible"] is True
    assert gate["mode_comparison_eligible"] is False


def test_named_manifest_requires_applied_policy_for_mode_comparison(tmp_path) -> None:
    module = _load_runner()
    policy = {
        "status": "applied",
        "mode": "CC",
        "fg_sampling": "continuous",
        "bg_sampling": "continuous",
        "bg_owner": "water",
    }
    arm = _arm(tmp_path, policy=policy)
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is True, gate
    assert gate["native_artifact_ready"] is False
    assert gate["mode_comparison_eligible"] is False


def test_named_manifest_ab_requires_both_comparison_roles_and_allows_distinct_fields(tmp_path) -> None:
    module = _load_runner()
    manifest = _ab_manifest(tmp_path)
    for arm in manifest["arms"].values():
        arm["field_observations_mode_eligible"] = True  # synthetic validated-field fixture
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": manifest}, {}
    )
    assert gate["pass"] is True, gate
    assert gate["mode_comparison_eligible"] is True
    manifest["comparisons"] = manifest["comparisons"][:-1]
    rejected = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": manifest}, {}
    )
    assert rejected["pass"] is False
    assert "ab_comparison_roles_incomplete" in rejected["failed_checks"]


def test_named_manifest_rejects_missing_role_and_duplicate_real_path(tmp_path) -> None:
    module = _load_runner()
    arm = _arm(tmp_path)
    arm["artifacts"] = arm["artifacts"][:-1]
    arm["artifacts"][1]["path"] = arm["artifacts"][0]["path"]
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is False
    assert any("missing_role_fg_glyph_change_highlight" in value for value in gate["failed_checks"])
    assert "single_duplicate_real_paths" in gate["failed_checks"]


def test_named_manifest_rejects_mutated_artifact_hash_and_field_hash(tmp_path) -> None:
    module = _load_runner()
    arm = _arm(tmp_path)
    arm["artifacts"][0]["sha256"] = "0" * 64
    arm["artifacts"][1]["field_sha256"] = list(arm["artifacts"][1]["field_sha256"])
    arm["artifacts"][1]["field_sha256"][2] = "1" * 64
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is False
    assert "single_artifact_0_sha256_mismatch" in gate["failed_checks"]
    assert "single_artifact_1_field_sha256_mismatch" in gate["failed_checks"]


def test_named_manifest_rejects_hash_correct_non_gif_artifact(tmp_path) -> None:
    module = _load_runner()
    arm = _arm(tmp_path)
    path = Path(arm["artifacts"][0]["path"])
    path.write_bytes(b"this is not a GIF")
    arm["artifacts"][0]["sha256"] = _hash(path)
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is False
    assert "single_artifact_0_artifact_gif_decode_failed" in gate["failed_checks"]


def test_named_manifest_keeps_native_readiness_pending_without_viewport_witness(tmp_path) -> None:
    module = _load_runner()
    arm = _arm(tmp_path)
    gate = module._presentation_artifact_manifest_gate(
        {"presentation_artifact_manifest": _manifest(arm)}, {}
    )
    assert gate["pass"] is True, gate
    assert gate["actual_field_eligible"] is True
    assert gate["native_artifact_ready"] is False
    assert gate["mode_comparison_eligible"] is False


def test_legacy_manifest_remains_diagnostic_and_never_field_eligible() -> None:
    module = _load_runner()
    gate = module._presentation_artifact_manifest_gate(
        {
            "presentation_artifact_manifest": {
                "schema": "fl4359.presentation_artifact_manifest.v1",
                "eligibility": "legacy_pair_diagnostic",
            }
        },
        {},
    )
    assert gate["pass"] is True, gate
    assert gate["legacy_diagnostic"] is True
    assert gate["actual_field_eligible"] is False
    assert gate["mode_comparison_eligible"] is False


def test_capture_budget_is_explicit_and_nonpositive_budget_fails_preflight() -> None:
    module = _load_runner()
    # This is a pure preflight namespace test; no producer or runtime is started.
    args = argparse.Namespace(
        presentation_field_capture=True,
        presentation_field_capture_budget_bytes=0,
    )
    assert module._presentation_field_capture_budget_valid(args) is False
    args.presentation_field_capture_budget_bytes = module.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES
    assert module._presentation_field_capture_budget_valid(args) is True


def _complete_transport_receipt(module, *, compact=False):
    # Reuse the complete reflection/source fixture so this transport test stays
    # aligned with the native source reader's independently validated ABI.
    fixture_path = ROOT / "scripts/tests/test_fl4691_native_reflection_abi.py"
    spec = importlib.util.spec_from_file_location("native_reflection_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fields, sources = [], []
    for frame_id in module.PRESENTATION_CAPTURE_FRAME_IDS:
        field = {"status": "valid", "sequence_frame": frame_id, "raw_sha256": "a" * 64}
        if compact:
            field.update(schema=module.COMPACT_PRESENTATION_FIELD_SCHEMA,
                         applied_policy={"status": "applied", "mode": "FF", "projection": "perspective"},
                         witness_sha256="b" * 64, witness_path="witness.bin",
                         fixed_mask=238136, compact_pixel_stride_words=14,
                         record_layout="v3_header_slot_table_prefix_dynamic_fields")
        fields.append(field)
        source = fixture._metadata_v3(fixture._water_record(), b"", "reflection.bin")
        source["reflection_lineage_table_bytes"] = module.REFLECTION_LINEAGE_TABLE_BYTES
        source["reflection_lineage_map_bytes"] = source["probe_size"] ** 2 * 18 * 4
        source["sequence_frame"] = frame_id
        source["join"]["sequence_frame"] = frame_id
        source["sky_context"]["sequence_frame"] = frame_id
        sources.append(source)
    return {
        "camera_mode": "plane_air",
        "presentation_field_capture_requested": True,
        "presentation_field_capture_ok": True,
        "presentation_field_required_peak_bytes": (
            module.COMPACT_PRESENTATION_FIELD_REQUIRED_PEAK_BYTES if compact
            else module.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES),
        "presentation_field_native_source_content_required_peak_bytes": module.NATIVE_SOURCE_CONTENT_REQUIRED_PEAK_BYTES,
        "presentation_field_reflection_lineage_required_peak_bytes": module.REFLECTION_LINEAGE_REQUIRED_PEAK_BYTES,
        "presentation_field_native_sky_context_history_required_peak_bytes": module.NATIVE_SKY_CONTEXT_HISTORY_REQUIRED_PEAK_BYTES,
        "presentation_field_generation_observer_required_peak_bytes": module.GENERATION_OBSERVER_REQUIRED_PEAK_BYTES,
        "presentation_field_postprocess_capture_required_peak_bytes": module.POSTPROCESS_TRACE_PEAK_BYTES,
        "presentation_field_sidecars": fields,
        "native_source_content_sidecars": sources,
    }


def test_field_capture_receipt_gate_checks_complete_runtime_transport() -> None:
    module = _load_runner()
    for compact in (False, True):
        receipt = _complete_transport_receipt(module, compact=compact)
        assert module._presentation_field_capture_receipt_accepts(receipt) is True
        receipt["presentation_field_sidecars"].pop()
        assert module._presentation_field_capture_receipt_accepts(receipt) is False
    assert not module._presentation_field_capture_receipt_accepts({
        "presentation_field_capture_requested": True, "presentation_field_capture_ok": True,
        "presentation_field_sidecars": [{"status": "valid", "sequence_frame": i,
                                         "raw_sha256": "a" * 64} for i in range(5)],
    })


def test_compact_transport_refuses_bad_policy_witness_and_budget() -> None:
    module = _load_runner()
    for key, value in (("witness_sha256", ""), ("witness_path", ""),
                       ("fixed_mask", 0), ("compact_pixel_stride_words", 13),
                       ("record_layout", "unknown"), ("schema", "unknown"),
                       ("applied_policy", {"status": "applied", "mode": "FC", "projection": "perspective"})):
        receipt = _complete_transport_receipt(module, compact=True)
        receipt["presentation_field_sidecars"][0][key] = value
        assert not module._presentation_field_capture_receipt_accepts(receipt), key
    receipt = _complete_transport_receipt(module, compact=True)
    receipt["presentation_field_required_peak_bytes"] = module.PRESENTATION_FIELD_REQUIRED_PEAK_BYTES
    assert not module._presentation_field_capture_receipt_accepts(receipt)


def test_native_terminal_profile_requires_actual_tall_grid_and_native_atlas():
    runner = _load_runner()
    native = runner._canonical_profile_spec(terminal=True)
    square = runner._canonical_profile_spec()
    args = argparse.Namespace(camera_mode="plane_air", glyph_matrix_cell_px=32,
                              cell_aspect="terminal", exact_camera_projection="perspective")
    assert native.native_cell_size == (16, 32)
    assert native.ansi_size == (225, 71)
    assert runner._profile_application_supported(args, native)
    assert not runner._profile_application_supported(args, square)
    assert native.atlas_identity() != square.atlas_identity()

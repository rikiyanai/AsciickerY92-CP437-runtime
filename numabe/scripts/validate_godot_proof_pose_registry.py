#!/usr/bin/env python3
"""Validate the single Godot proof/pose registry.

FL-4781: this validator keeps the launcher, Godot menu, and legacy pose helper
from silently drifting back to separate hard-coded proof inventories.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_godot_canonical_proof_index import (
    INDEX_RELATIVE_PATH,
    build_index,
    _encoded_index,
    render_reader,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "godot_proof_pose_registry.json"
CANONICAL_INDEX_READER_PATH = ROOT / "docs" / "proofs" / "README.md"

REQUIRED_PROOF_SOURCE_PATHS = {
    "godot_project/scripts/proofs/FL4350ProofGridOverlay.gd",
    "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd",
    "godot_project/tools/proofs/run_fl4270_view_mode_capture.gd",
    "godot_project/tools/proofs/run_fl4359_static_capture.gd",
    "godot_project/tools/proofs/run_fl4359_walk_capture.gd",
    "godot_project/scripts/tests/test_fl4359_o1_capture_lifecycle_invariants.gd",
    "godot_project/scripts/tests/test_fl4359_product_mask_capture_abi.gd",
    "godot_project/scripts/tests/test_fl4359_sparse_capture_lifecycle.gd",
    "godot_project/scripts/tests/test_scene_geometry_probe_draw_receipt.gd",
    "scripts/run_fl4265_airplane_proof.py",
    "scripts/run_fl4346_actor_presentation_proof.py",
    "scripts/run_fl4359_acceptance_package.py",
    "scripts/run_fl4359_card_chroma_pose_capture.py",
    "scripts/run_fl4359_headed_movement.py",
    "scripts/build_godot_canonical_png_grid.py",
    "scripts/run_fl4359_phase2a_capture.py",
    "scripts/run_fl4431_baseline_receipt_v2_bootstrap.py",
    "scripts/run_fl4431_perf_visual_runner.py",
    "scripts/run_godot_visual_owner_proof.py",
    "scripts/check_fl4216_shoreline_break_behavior.py",
    "scripts/check_fl4743_waterfall_behavior.py",
    "scripts/check_fl4782_flow_field_static.py",
    "scripts/fl4359_geometry_parity_gate.py",
    "scripts/fl4359_mesh_presence_gate.py",
    "scripts/fl4359_owner_churn_gate.py",
    "scripts/fl4359_presence_proof_gate.py",
    "scripts/fl4359_product_identity_churn_gate.py",
    "scripts/measure_true_ff_text_flips.py",
    "scripts/fl4359_proof_sensitivity_gate.py",
    "scripts/godot_feature_regression_gate.py",
    "scripts/godot_proof_poses.py",
    "scripts/test_fl4216_temporal_capture_contract.py",
    "scripts/test_fl4231_f13_capture_only_contract.py",
    "scripts/test_fl4231_moving_capture_attack_contract.py",
    "scripts/test_fl4332_lighthouse_beam_night_gate.py",
    "scripts/test_fl4346_actor_presentation_proof_contract.py",
    "scripts/test_fl4737_exact_pose_runner_contract.py",
    "scripts/test_godot_canonical_png_grid_runner.py",
    "scripts/tests/test_fl4216_settled_p1_capture_contract.py",
    "scripts/tests/test_fl4731_pieta_proof_launcher.py",
    "scripts/tests/test_fl4739_water_baseline_receipt.py",
}

REQUIRED_POSE_IDS = {
    "pose.flat_default",
    "pose.elevated_grass",
    "pose.just_grass",
    "pose.legacy_elevated_grass",
    "pose.shoreline",
    "pose.just_rocks_mountain",
    "pose.shoreline_real",
    "pose.shore_grass_dead_pose",
    "pose.shoreline_water_south",
    "pose.deep_water_only",
    "pose.firefly_proven_grass",
    "pose.mossy_rocks",
    "pose.tower_strafe_f3_route",
    "pose.fl4359_default_airplane_spawn",
    "pose.fl4265_canonical_airplane_spawn",
    "pose.fl4265_npc_crash_spawn",
    "pose.p1_reflection_submerged_hand",
    "pose.female_hand_mesh_anchor",
    "pose.pieta_operator_hud_mark",
    "pose.water_shore_flat_line",
    "pose.water_actor_sink_deep",
    "pose.water_shore_grass_context",
    "pose.water_open_reflection_band",
    "pose.water_open_reflection_source_backed",
    "pose.water_wave_band_sweep",
}

REQUIRED_ROUTE_NUMBERS = {f"R{index:02d}" for index in range(1, 27)}

ALLOWED_PROOF_KINDS = {
    "godot_runtime_proof",
    "godot_launcher_proof",
    "godot_static_contract",
    "static_contract",
    "receipt_builder",
    "gate",
    "compatibility_reader",
}

PUBLICATION_PRODUCER_KINDS = {
    "godot_runtime_proof",
    "godot_launcher_proof",
    "receipt_builder",
    "gate",
}
PUBLICATION_STATES = {
    "SHARED_PUBLICATION_INTEGRATED",
    "SHARED_PUBLICATION_DIAGNOSTIC_ONLY",
    "PENDING_SHARED_PUBLICATION",
}

ALLOWED_POSE_KINDS = {
    "diagnostic_location",
    "operator_pose",
    "operator_marker_pose",
    "route_pose",
    "spawn",
    "non_pose_anchor",
    "non_pose_marker",
}


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: registry root must be an object")
    return data


def _as_rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    if not isinstance(rows, list):
        raise SystemExit(f"registry.{key} must be a list")
    if not all(isinstance(row, dict) for row in rows):
        raise SystemExit(f"registry.{key} rows must be objects")
    return rows


def _position_is_triplet(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(part, (int, float)) and not isinstance(part, bool) for part in value)
    )


def validate_registry(path: Path = REGISTRY_PATH) -> list[str]:
    data = _load_registry(path)
    failures: list[str] = []
    if data.get("version") != "godot-proof-pose-registry.v1":
        failures.append("version must be godot-proof-pose-registry.v1")
    if data.get("owner") != "config/godot_proof_pose_registry.json":
        failures.append("owner must name config/godot_proof_pose_registry.json")

    proof_rows = _as_rows(data, "proof_scripts")
    pose_rows = _as_rows(data, "poses")
    route_rows = _as_rows(data, "routes")

    all_ids: set[str] = set()
    for section_name, rows in (("proof_scripts", proof_rows), ("poses", pose_rows), ("routes", route_rows)):
        for row in rows:
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                failures.append(f"{section_name}: row missing string id")
                continue
            if row_id in all_ids:
                failures.append(f"duplicate id: {row_id}")
            all_ids.add(row_id)

    proof_paths = {row.get("source_path") for row in proof_rows if isinstance(row.get("source_path"), str)}
    missing_registered_paths = sorted(REQUIRED_PROOF_SOURCE_PATHS - proof_paths)
    if missing_registered_paths:
        failures.append("required proof source paths missing from registry: " + ", ".join(missing_registered_paths))
    for rel_path in sorted(proof_paths):
        if not (ROOT / rel_path).exists():
            failures.append(f"registered proof source path does not exist: {rel_path}")

    pose_ids = {row.get("id") for row in pose_rows}
    missing_pose_ids = sorted(REQUIRED_POSE_IDS - pose_ids)
    if missing_pose_ids:
        failures.append("required pose ids missing from registry: " + ", ".join(missing_pose_ids))
    for row in pose_rows:
        row_id = str(row.get("id", "<?>"))
        if row.get("kind") not in ALLOWED_POSE_KINDS:
            failures.append(f"{row_id}: invalid pose kind {row.get('kind')!r}")
        if row.get("kind") in {"operator_pose", "route_pose", "diagnostic_location", "operator_marker_pose"}:
            if "player_position" in row and row.get("player_position") is not None and not _position_is_triplet(row["player_position"]):
                failures.append(f"{row_id}: player_position must be a numeric [x,y,z] triplet or null")
        if row.get("kind") == "spawn":
            spawn = row.get("spawn")
            if not isinstance(spawn, dict) or "x" not in spawn or "z" not in spawn:
                failures.append(f"{row_id}: spawn rows must include spawn.x and spawn.z")
        if row_id == "pose.pieta_operator_hud_mark":
            if row.get("kind") != "non_pose_marker":
                failures.append("pose.pieta_operator_hud_mark must remain kind=non_pose_marker")
            if "player_position" in row:
                failures.append("pose.pieta_operator_hud_mark must not masquerade as player_position")

    route_numbers = {row.get("route_number") for row in route_rows}
    missing_route_numbers = sorted(REQUIRED_ROUTE_NUMBERS - route_numbers)
    if missing_route_numbers:
        failures.append("required R01-R26 routes missing from registry: " + ", ".join(missing_route_numbers))
    for row in route_rows:
        row_id = str(row.get("id", "<?>"))
        for pose_ref in row.get("pose_refs", []):
            if pose_ref not in pose_ids:
                failures.append(f"{row_id}: unknown pose_ref {pose_ref}")
    for row in proof_rows:
        row_id = str(row.get("id", "<?>"))
        if row.get("kind") not in ALLOWED_PROOF_KINDS:
            failures.append(f"{row_id}: invalid proof kind {row.get('kind')!r}")
        for pose_ref in row.get("pose_refs", []):
            if pose_ref not in pose_ids and not str(pose_ref).startswith("route."):
                failures.append(f"{row_id}: unknown pose_ref {pose_ref}")

    failures.extend(validate_temporal_requirements(data, all_ids))

    publication = data.get("publication_classifications")
    if not isinstance(publication, dict):
        failures.append("publication_classifications must be an object")
    else:
        classifications = publication.get("by_source_path")
        if not isinstance(classifications, dict):
            failures.append("publication_classifications.by_source_path must be an object")
        else:
            active_paths = {
                row["source_path"]
                for row in proof_rows
                if row.get("kind") in PUBLICATION_PRODUCER_KINDS
                and isinstance(row.get("source_path"), str)
            }
            classified_paths = set(classifications)
            missing_classifications = sorted(active_paths - classified_paths)
            stale_classifications = sorted(classified_paths - active_paths)
            if missing_classifications:
                failures.append(
                    "registered publication producers missing classification: "
                    + ", ".join(missing_classifications)
                )
            if stale_classifications:
                failures.append(
                    "publication classifications not registered as producers: "
                    + ", ".join(stale_classifications)
                )
            for source_path in sorted(active_paths & classified_paths):
                state = classifications[source_path]
                if state not in PUBLICATION_STATES:
                    failures.append(
                        f"{source_path}: invalid publication classification {state!r}"
                    )

    feature_family = data.get("feature_route_family", {})
    feature_path = feature_family.get("source_path") if isinstance(feature_family, dict) else None
    if feature_path and not (ROOT / feature_path).exists():
        failures.append(f"feature_route_family source_path does not exist: {feature_path}")

    index_meta = data.get("canonical_proof_index")
    if not isinstance(index_meta, dict):
        failures.append("canonical_proof_index must be an object")
    else:
        if index_meta.get("owner") != "config/godot_proof_pose_registry.json":
            failures.append("canonical_proof_index.owner must name config/godot_proof_pose_registry.json")
        if index_meta.get("reader") != "docs/proofs/README.md":
            failures.append("canonical_proof_index.reader must be docs/proofs/README.md")
        if index_meta.get("generated_json") != str(INDEX_RELATIVE_PATH):
            failures.append(f"canonical_proof_index.generated_json must be {INDEX_RELATIVE_PATH}")
        if "mode_policy" in index_meta:
            failures.append(
                "canonical_proof_index.mode_policy must not duplicate temporal_regression_requirements; "
                "docs/proofs/index.json derives mode policy from the temporal owner"
            )
    docs = data.get("docs", {})
    if isinstance(docs, dict):
        if docs.get("canonical_proof_index") != "docs/proofs/README.md":
            failures.append("docs.canonical_proof_index must be docs/proofs/README.md")
        if docs.get("canonical_proof_index_json") != str(INDEX_RELATIVE_PATH):
            failures.append(f"docs.canonical_proof_index_json must be {INDEX_RELATIVE_PATH}")
    else:
        failures.append("docs must be an object")
    if not CANONICAL_INDEX_READER_PATH.exists():
        failures.append("docs/proofs/README.md missing canonical proof index reader")
    else:
        reader_text = CANONICAL_INDEX_READER_PATH.read_text(encoding="utf-8")
        for required_text in (
            "config/godot_proof_pose_registry.json",
            "docs/proofs/index.json",
            "generated",
        ):
            if required_text not in reader_text:
                failures.append(f"docs/proofs/README.md missing {required_text!r}")
    index_path = ROOT / INDEX_RELATIVE_PATH
    if not index_path.exists():
        failures.append(f"{INDEX_RELATIVE_PATH} missing generated canonical proof index")
    else:
        expected = _encoded_index(build_index(data, path))
        actual = index_path.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(f"{INDEX_RELATIVE_PATH} is stale; run scripts/build_godot_canonical_proof_index.py")
        if CANONICAL_INDEX_READER_PATH.exists() and CANONICAL_INDEX_READER_PATH.read_text(encoding="utf-8") != render_reader(build_index(data, path)):
            failures.append("docs/proofs/README.md is stale; regenerate the canonical index")

    compat = ROOT / "scripts" / "godot_proof_poses.py"
    compat_source = compat.read_text(encoding="utf-8")
    if "godot_proof_pose_registry.json" not in compat_source:
        failures.append("scripts/godot_proof_poses.py must read config/godot_proof_pose_registry.json")
    return failures


def validate_temporal_requirements(data: dict[str, Any], all_ids: set[str]) -> list[str]:
    """FL-4359: prevent single-route contract collapse, not certify runtime coverage."""
    contract = data.get("temporal_regression_requirements")
    if not isinstance(contract, dict):
        return ["temporal_regression_requirements missing; runtime coverage is not established"]
    failures: list[str] = []
    families = contract.get("route_families", {})
    if not isinstance(families, dict):
        return ["temporal_regression_requirements.route_families must be an object"]
    for name in ("camera_turn_five_frame", "airplane", "strafe_movement"):
        family = families.get(name)
        if not isinstance(family, dict):
            failures.append(f"temporal coverage requirement missing route family: {name}")
            continue
        for field in ("proof_refs", "required_events"):
            values = family.get(field)
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                failures.append(f"{name}: {field} must be a nonempty string list")
        for field in ("proof_refs", "route_refs"):
            values = family.get(field)
            if not isinstance(values, list):
                failures.append(f"{name}: {field} must be a list")
                continue
            for ref in values:
                if not isinstance(ref, str) or ref not in all_ids:
                    failures.append(f"{name}: unknown {field} reference: {ref}")
    required_sets = {
        "defect_classes": {"jitter", "shimmer", "parallax_error", "ghost_mesh", "omission", "duplicate_out_of_place"},
        "presentation_modes": {"FF"},
        "sampling_axes": {"matrix_columns_rows", "cell_width_height", "cell_aspect_square_terminal", "raster_target_width_height", "presentation_width_height_scale_crop", "glyph_source_width_height_hash", "probe_face_resolution", "projection_fov_or_ortho_size", "zoom_and_actual_camera_pose", "foreground_background_mode"},
    }
    for field, required in required_sets.items():
        values = contract.get(field)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            failures.append(f"temporal {field} must be a string list")
        elif not required.issubset(values):
            failures.append(f"temporal {field} missing: {sorted(required - set(values))}")
    for field in ("required_witnesses", "sensitivity_controls", "acceptance_rules"):
        values = contract.get(field)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            failures.append(f"temporal {field} must be a nonempty string list")
    runtime_domain = contract.get("runtime_sampling_domain")
    if not isinstance(runtime_domain, dict):
        failures.append("temporal runtime_sampling_domain must be an object")
    else:
        if runtime_domain.get("matrix_cell_height_steps_px") != [96, 64, 48, 32, 24, 20, 16, 12]:
            failures.append("runtime sampling domain must retain all supported matrix steps")
        aspects = runtime_domain.get("cell_aspects")
        if not isinstance(aspects, dict) or set(aspects) != {"square_1x1", "terminal_1x2"}:
            failures.append("runtime sampling domain must retain square_1x1 and terminal_1x2")
        if runtime_domain.get("probe_face_resolution_px") != [128, 256, 384]:
            failures.append("runtime sampling domain must retain probe sizes 128/256/384")
        zoom = runtime_domain.get("camera_zoom")
        if not isinstance(zoom, dict) or zoom.get("matrix_control_is_not_camera_zoom") is not True:
            failures.append("runtime sampling domain must separate matrix control from camera zoom")
        for field in ("raster_admission", "capture_identity_rule"):
            if not isinstance(runtime_domain.get(field), str) or not runtime_domain[field]:
                failures.append(f"runtime sampling domain {field} must be nonempty")
    return failures


def _summary(data: dict[str, Any]) -> str:
    proof_rows = _as_rows(data, "proof_scripts")
    pose_rows = _as_rows(data, "poses")
    route_rows = _as_rows(data, "routes")
    groups: dict[str, int] = {}
    for row in proof_rows:
        group = str(row.get("menu_group") or "ungrouped")
        groups[group] = groups.get(group, 0) + 1
    group_text = ", ".join(f"{key}={groups[key]}" for key in sorted(groups))
    return (
        f"proof_scripts={len(proof_rows)} poses={len(pose_rows)} "
        f"routes={len(route_rows)} groups=[{group_text}]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    failures = validate_registry(args.registry)
    data = _load_registry(args.registry)
    if args.print_summary:
        print(_summary(data))
    if failures:
        print("GODOT_PROOF_POSE_REGISTRY_INVALID")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("GODOT_PROOF_POSE_REGISTRY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

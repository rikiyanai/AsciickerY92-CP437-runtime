#!/usr/bin/env python3
"""Fail-closed validator for the unmeasured FL-4512 G9 preregistration.

This is an offline contract validator.  It owns no renderer state, execution,
participant recruitment, capture, metric measurement, or paper result claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO / "docs/research/ascii/verification/fl4512/evaluation/preregistration.v1.json"
TOGGLES = {
    "tonal_baseline", "descriptor_owned_route", "topology_pools",
    "structural_scorer", "primitive_field_scorer", "temporal_owner",
}
METRICS = {
    "continuity_break_count", "topology_violation_count", "mean_tone_error",
    "changed_cell_containment", "frame_time_median_ms", "frame_time_p95_ms",
    "frame_time_p99_ms",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(failures: list[str], message: str) -> None:
    failures.append(message)


def validate(data: dict[str, Any], repo: Path) -> list[str]:
    failures: list[str] = []
    if data.get("schema") != "fl4512.g9_preregistration.v1":
        fail(failures, "schema")
    if data.get("status") != "NOT_RUN":
        fail(failures, "status_not_not_run")

    registration = data.get("evaluation_registration")
    if not isinstance(registration, dict):
        return [*failures, "evaluation_registration"]
    registration_path = repo / str(registration.get("path", ""))
    if not registration_path.is_file():
        fail(failures, "evaluation_registration_missing")
    elif sha256(registration_path) != registration.get("sha256"):
        fail(failures, "evaluation_registration_hash")
    if registration.get("reference") != "all_on":
        fail(failures, "reference")
    if registration.get("one_toggle_at_a_time") is not True:
        fail(failures, "one_toggle_at_a_time")
    toggles = registration.get("toggles")
    if not isinstance(toggles, list) or set(toggles) != TOGGLES or len(toggles) != len(TOGGLES):
        fail(failures, "toggle_registry")

    manifest = data.get("scene_manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != "fl4512.g9_scene_manifest.v1":
        fail(failures, "scene_manifest_schema")
    else:
        seen_scenes: set[str] = set()
        scenes = manifest.get("static_scenes")
        if not isinstance(scenes, list) or not scenes:
            fail(failures, "static_scenes")
        else:
            for scene in scenes:
                if not isinstance(scene, dict):
                    fail(failures, "scene_record")
                    continue
                scene_id = scene.get("scene_id")
                scene_path = repo / str(scene.get("path", ""))
                views = scene.get("camera_views")
                if not isinstance(scene_id, str) or scene_id in seen_scenes:
                    fail(failures, "scene_id")
                seen_scenes.add(str(scene_id))
                if not scene_path.is_file():
                    fail(failures, f"scene_missing:{scene_id}")
                elif sha256(scene_path) != scene.get("sha256"):
                    fail(failures, f"scene_hash:{scene_id}")
                if not isinstance(views, list) or not views:
                    fail(failures, f"camera_views:{scene_id}")
                elif any(not isinstance(view, list) or len(view) != 6 for view in views):
                    fail(failures, f"camera_view_tuple:{scene_id}")
        trajectory = manifest.get("temporal_trajectory")
        if not isinstance(trajectory, dict) or trajectory.get("status") != "UNAUTHORED":
            fail(failures, "temporal_trajectory_status")
        elif trajectory.get("required_before_measurement") is not True:
            fail(failures, "temporal_trajectory_gate")

    metric_registry = data.get("metric_registry")
    if not isinstance(metric_registry, dict) or metric_registry.get("schema") != "fl4512.evaluation_metrics.v1":
        fail(failures, "metric_registry_schema")
    else:
        metrics = metric_registry.get("metrics")
        if not isinstance(metrics, list) or {row.get("metric_id") for row in metrics if isinstance(row, dict)} != METRICS:
            fail(failures, "metric_registry")
        elif any(row.get("status") != "UNMEASURED" for row in metrics if isinstance(row, dict)):
            fail(failures, "metric_measured")
        if metric_registry.get("result_rows") != []:
            fail(failures, "result_rows_present")

    performance = data.get("performance_materials")
    if not isinstance(performance, dict) or performance.get("schema") != "fl4512.g9_performance_materials.v1":
        fail(failures, "performance_schema")
    else:
        if performance.get("status") != "MATERIALS_UNAUTHORED":
            fail(failures, "performance_status")
        if performance.get("warmup_frames") is not None or performance.get("sample_frames") is not None:
            fail(failures, "performance_window_authored")
        if performance.get("raw_sample_schema") is not None or performance.get("samples") != []:
            fail(failures, "performance_samples_present")
        required = performance.get("required_runtime_tuple")
        if not isinstance(required, list) or len(set(required)) != len(required) or not required:
            fail(failures, "performance_runtime_tuple")

    study = data.get("perceptual_study_materials")
    if not isinstance(study, dict) or study.get("schema") != "fl4512.g9_perceptual_materials.v1":
        fail(failures, "study_schema")
    else:
        if study.get("status") != "MATERIALS_UNAUTHORED" or study.get("recruitment_status") != "NOT_STARTED":
            fail(failures, "study_status")
        if study.get("participant_records") != [] or study.get("stimulus_manifest") != []:
            fail(failures, "study_materials_present")
        plan = study.get("analysis_plan")
        if not isinstance(plan, dict) or plan.get("left_right_randomization") is not True:
            fail(failures, "study_analysis_plan")
        elif plan.get("multiple_comparison_correction") != "Holm" or plan.get("alpha") != 0.05:
            fail(failures, "study_correction")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {"schema": "fl4512.g9_preregistration_validation.v1", "verdict": "FAIL", "failures": [f"input:{exc}"]}
    else:
        failures = validate(data, args.repo)
        result = {
            "schema": "fl4512.g9_preregistration_validation.v1",
            "input": str(args.input),
            "input_sha256": sha256(args.input),
            "verdict": "PASS" if not failures else "FAIL",
            "measurement_state": "NOT_RUN",
            "failures": failures,
        }
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

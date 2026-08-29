#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYZE_RUNS = REPO_ROOT / "scripts" / "analyze_runs.py"


def test_fl4257_maps_curvature_gates_through_front_door() -> None:
    proc = subprocess.run(
        [sys.executable, str(ANALYZE_RUNS), "fl", "gates", "FL-4257"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "No explicit gate mapping" not in proc.stdout
    for gate in [
        "evidence_curvature_field_present",
        "gameplay_curvature_server_published",
        "gameplay_client_curvature_matches_server",
        "evidence_curvature_intake_from_a3d",
        "gameplay_euclidean_mode_regression_guard",
        "gameplay_geodesic_movement_observed",
        "gameplay_curved_distance_metrics_observed",
        "evidence_shared_curved_render_owner",
        "evidence_shared_curved_water_model",
        "evidence_no_euclidean_fallback_under_curved_mode",
    ]:
        assert gate in proc.stdout


def test_fl4257_gates_with_runs_handles_missing_run_artifacts() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ANALYZE_RUNS),
            "fl",
            "gates-with-runs",
            "FL-4257",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["fl"] == "FL-4257"
    assert payload["has_gates"] is True
    assert {row["gate"] for row in payload["gates"]} == {
        "evidence_curvature_field_present",
        "gameplay_curvature_server_published",
        "gameplay_client_curvature_matches_server",
        "evidence_curvature_intake_from_a3d",
        "gameplay_euclidean_mode_regression_guard",
        "gameplay_geodesic_movement_observed",
        "gameplay_curved_distance_metrics_observed",
        "evidence_shared_curved_render_owner",
        "evidence_shared_curved_water_model",
        "evidence_no_euclidean_fallback_under_curved_mode",
    }

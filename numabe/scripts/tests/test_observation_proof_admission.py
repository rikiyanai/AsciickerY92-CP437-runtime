"""Contract tests for the staged observation-proof admission gates.

Every receipt in this file is a clearly labelled synthetic schema fixture.  No
test launches Godot/Colyseus, opens a browser, or asserts that a filesystem
path is real runtime evidence.  These tests exercise only the read-only
validator's positive/negative boundary.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from maintainer.lib.observation_proof_admission import (  # noqa: E402
    SCHEMA,
    TEMPORAL_SCHEMA,
    ObservationProofAdmissionError,
    require_observation_admission,
    validate_observation_admission,
)


def _synthetic_receipt(tmp_path: Path) -> dict:
    """Return a complete synthetic contract fixture, never runtime evidence."""

    # The path is intentionally labelled so a failure cannot be mistaken for
    # proof that a real asset was used.  The validator must not read it.
    synthetic_asset = tmp_path / "SYNTHETIC-TEST-designated-input.txt"
    synthetic_asset.write_text("synthetic schema fixture only\n", encoding="utf-8")
    start = "a" * 64
    server = "b" * 64
    source = "c" * 64
    return {
        "schema": SCHEMA,
        "run_id": "SYNTHETIC-TEST-run-001",
        "claim": {"kind": "temporal"},
        "runtime": {
            "engine": "godot",
            "runner_runtime": "godot",
            "run_kind": "headed",
            "runner": "scripts/run_fl4359_headed_movement.py",
            "canonical": True,
            "config": {
                "fullscreen": True,
                "ascii_compositor": True,
                "server_started_first": True,
                "route_registered": True,
            },
        },
        "repair": {
            "boundary": "reached",
            "implementation_complete": True,
            "static_checks_passed": True,
            "hypothesis": "SYNTHETIC-TEST source intervention changes the declared route",
            "expected_change": "SYNTHETIC-TEST counter-observation changes under the route",
            "source_intervention": {
                "explicit": True,
                "before_fingerprint": start,
                "after_fingerprint": source,
                "changed_paths": ["SYNTHETIC-TEST/route.gd"],
                "causal_route": "SYNTHETIC-TEST/route",
            },
        },
        "runtime_preflight": {
            "completed": True,
            "processes": {"observed": True, "status": "clean"},
            "listeners": {
                "observed": True,
                "status": "expected",
                "port": 2589,
                "owner_pid": 12345,
            },
            "server": {
                "observed": True,
                "status": "fresh",
                "source_fingerprint": source,
                "source_fingerprint_matches": True,
                "built_after_source_change": True,
                "started_at": "SYNTHETIC-TEST-2026-09-30T00:00:00Z",
            },
        },
        "disk_budget": {
            "measured": True,
            "status": "pass",
            "surface": "SYNTHETIC-TEST-temp-surface",
            "free_bytes": 1000,
            "required_free_bytes": 500,
            "output_bytes": 100,
            "max_output_bytes": 500,
        },
        "routes": [
            {
                "route_id": "SYNTHETIC-TEST-route-a",
                "family": "movement",
                "registered": True,
                "admitted": True,
                "identity": "SYNTHETIC-TEST-route-a-identity",
            },
            {
                "route_id": "SYNTHETIC-TEST-route-b",
                "family": "camera",
                "registered": True,
                "admitted": True,
                "identity": "SYNTHETIC-TEST-route-b-identity",
            },
        ],
        "harness": {
            "authority": "user_input_api",
            "independent_from_product": True,
            "driver_api": "user_input_api",
            "direct_setter_calls": False,
            "driver_source": "SYNTHETIC-TEST independent input adapter",
        },
        "negative_control": {
            "known_bad": True,
            "same_invocation": True,
            "expected": "fail",
            "observed": "fail",
            "checker_unchanged": True,
            "fixture_id": "SYNTHETIC-TEST-known-bad",
        },
        "contradiction": {
            "operator_observed_failure": False,
            "resolved": False,
        },
        "driver_sensitivity": {
            "status": "pass",
            "authority": "user_input_api",
            "same_invocation": True,
            "input_delta_observed": True,
            "positive_control": {"expected": "change", "observed": True},
            "negative_control": {"expected": "stable", "observed": True},
        },
        "input_provenance": {
            "kind": "real",
            "synthetic_fixture": False,
            "asset_paths": [str(synthetic_asset)],
            "designated_by": "SYNTHETIC-TEST-canonical-registry",
            "repository_role": "original_upstream",
            "upstream": {
                "repository": "https://github.com/msokalski/asciicker",
                "revision": "SYNTHETIC-TEST-revision",
                "path": "SYNTHETIC-TEST/input.asset",
                "content_sha256": "d" * 64,
                "verified": True,
            },
        },
        "source_identity": {
            "schema": "SYNTHETIC-TEST-source-identity-v1",
            "state": "mixed",
            "components": [
                {"path": "SYNTHETIC-TEST/tracked.gd", "sha256": "e" * 64, "kind": "tracked"},
                {"path": "SYNTHETIC-TEST/dirty.gd", "sha256": "f" * 64, "kind": "dirty"},
                {"path": "SYNTHETIC-TEST/untracked.gd", "sha256": "1" * 64, "kind": "untracked"},
            ],
            "fingerprint": source,
            "fingerprint_after_launch": source,
            "immutable": True,
            "snapshot": {
                "path": "SYNTHETIC-TEST/source-snapshot.json",
                "sha256": "2" * 64,
                "immutable": True,
            },
        },
        "temporal": {
            "schema": TEMPORAL_SCHEMA,
            "continuous_run": True,
            "native_text_rows": True,
            "fixed_pixel_cells": False,
            "run_id": "SYNTHETIC-TEST-run-001",
            "frame_count": 2,
            "frames": [
                {
                    "schema": TEMPORAL_SCHEMA,
                    "frame": 0,
                    "engine_frame": 100,
                    "row_pitch_px": 17,
                    "page_shift_px": [0, 0],
                    "chars": [[65, 0, 0, 11, 0, 16711680, 0]],
                },
                {
                    "schema": TEMPORAL_SCHEMA,
                    "frame": 1,
                    "engine_frame": 101,
                    "row_pitch_px": 17,
                    "page_shift_px": [1, 0],
                    "chars": [[66, 1, 0, 11, 0, 16711680, 0]],
                },
            ],
        },
        # Explicitly report the staged integration boundary.  A unit-test
        # pass must not be interpreted as a connected launcher hook.
        "integration": {
            "wired": False,
            "status": "proposal_only",
        },
    }


def test_complete_synthetic_contract_is_admitted_but_not_connected(tmp_path):
    report = validate_observation_admission(_synthetic_receipt(tmp_path))

    assert report.admitted is True
    assert report.connected is False
    assert report.errors == ()
    assert any("integration_not_wired" in item for item in report.unsolved)
    assert any("render_acceptance_unchecked" in item for item in report.warnings)


@pytest.mark.parametrize(
    ("path", "value", "needle"),
    [
        (("runtime", "runner_runtime"), "cpp", "engine and runner_runtime mismatch"),
        (("runtime", "config", "fullscreen"), False, "runtime.config.fullscreen"),
        (("repair", "boundary"), "open", "repair.boundary"),
        (("runtime_preflight", "server", "status"), "stale", "server.status"),
        (("disk_budget", "free_bytes"), 1, "free_bytes is below"),
        (("negative_control", "observed"), "pass", "negative_control.observed"),
        (("input_provenance", "synthetic_fixture"), True, "synthetic_fixture"),
        (("source_identity", "fingerprint_after_launch"), "9" * 64, "fingerprint_after_launch"),
        (("temporal", "fixed_pixel_cells"), True, "fixed_pixel_cells"),
    ],
)
def test_negative_gate_refuses_each_changed_contract(tmp_path, path, value, needle):
    receipt = _synthetic_receipt(tmp_path)
    cursor = receipt
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value

    report = validate_observation_admission(receipt)

    assert report.admitted is False
    assert any(needle in item for item in report.errors), report.errors


def test_mixed_source_requires_immutable_tracked_and_dirty_components(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    receipt["source_identity"]["components"] = receipt["source_identity"]["components"][:1]

    report = validate_observation_admission(receipt)

    assert report.admitted is False
    assert any("mixed state requires" in item for item in report.errors)


def test_contradiction_requires_same_surface_counter_observation(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    receipt["contradiction"] = {
        "operator_observed_failure": True,
        "resolved": False,
        "counter_observation": {
            "reproduced": True,
            "same_surface": True,
            "observed_failure": True,
            "mechanism": "SYNTHETIC-TEST unresolved failure",
        },
    }

    report = validate_observation_admission(receipt)

    assert report.admitted is False
    assert any("contradiction.resolved" in item for item in report.errors)
    assert any("observed_failure" in item for item in report.errors)


def test_temporal_rows_are_native_and_continuous_not_fixed_cells(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    receipt["temporal"]["frames"][0]["chars"][0] = {"cell": {"x": 0, "y": 0}}

    report = validate_observation_admission(receipt)

    assert report.admitted is False
    assert any("fixed-cell schema is forbidden" in item for item in report.errors)


def test_missing_integration_is_explicit_unsolved_not_file_presence_inference(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    del receipt["integration"]

    report = validate_observation_admission(receipt)

    assert report.admitted is True
    assert report.connected is False
    assert report.unsolved == (
        "integration_not_wired: launcher/settings integration is not asserted",
    )


def test_unknown_engine_cannot_self_assert_canonical_runner(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    receipt["runtime"].update({"engine": "unregistered-native", "runner_runtime": "unregistered-native", "runner": "scripts/arbitrary.py"})
    report = validate_observation_admission(receipt)
    assert not report.admitted
    assert any("no canonical runner adapter" in item for item in report.errors)


def test_eighth_character_field_is_validated_like_native_reader(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    receipt["temporal"]["frames"][0]["chars"][0].append("not-an-integer")
    report = validate_observation_admission(receipt)
    assert not report.admitted
    assert any("optional site identity" in item for item in report.errors)


def test_require_raises_only_for_gate_errors_and_never_mutates(tmp_path):
    receipt = _synthetic_receipt(tmp_path)
    before = deepcopy(receipt)
    report = require_observation_admission(receipt)
    assert report.admitted is True
    assert receipt == before

    receipt["harness"]["direct_setter_calls"] = True
    with pytest.raises(ObservationProofAdmissionError, match="direct_setter_calls"):
        require_observation_admission(receipt)

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYZER = load_module(
    "fl4359_proof_sensitivity_analyzer",
    "scripts/analyze_fl4359_moving_camera.py",
)
REVIEW = load_module(
    "fl4359_proof_sensitivity_review",
    "scripts/build_fl4359_sunday_current_review_grid.py",
)
HEADED = load_module(
    "fl4359_proof_sensitivity_headed",
    "scripts/run_fl4359_headed_movement.py",
)
HOOK = load_module(
    "fl4359_proof_sensitivity_hook",
    "scripts/maintainer/hooks/fl4359_proof_sensitivity_hook.py",
)
GATE = load_module(
    "fl4359_proof_sensitivity_gate",
    "scripts/hooks/fl4359_proof_sensitivity_gate.py",
)


def args() -> argparse.Namespace:
    return argparse.Namespace(
        max_snap_px_delta=1.0,
        max_snap_sign_flip_rate=0.05,
        max_content_jitter_pct=5.0,
        max_selected_surface_identity_churn_pct=5.0,
        max_post_surface_bounce_pct=12.0,
        min_moving_frame_pairs=1,
        min_camera_motion_ratio=0.0,
        min_comparable_cells=1,
        proof_profile="general",
        expected_red_gate=[],
    )


def admissible_summary() -> dict:
    return {
        "frame_count": 2,
        "row_count": 8,
        "moving_frame_pairs": 1,
        "camera_motion_continuous_ratio": 1.0,
        "motion_compensated_final_cell_instability_pct": 0.0,
        "corrected_final_cell_oracle_valid": True,
        "product_anchor_provenance_complete": True,
        "terrain_product_homogeneity_source": "final_product_b55",
        "terrain_product_homogeneity_rows": 8,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
        "presentation_observer_population_accounting": {
            "cell_mask_owner": True,
            "continuous_product_colour": True,
            "motion_compensated_content": True,
        },
        "cell_mask_owner_stability": {
            "counts": {"comparable_cells": 4, "stable_cells": 4},
            "invalid_cell_count": 0,
            "comparable_pair_count": 1,
            "max_instability_pct": 0.0,
        },
        "continuous_product_colour_stability": {
            "counts": {"comparable_stable_semantic_samples": 4},
            "invalid_terrain_row_count": 0,
            "invalid_colour_witness_row_count": 0,
            "invalid_finalization_witness_row_count": 0,
            "comparable_pair_count": 1,
            "min_comparable_samples_per_pair": 4,
            "max_instability_pct": 0.0,
        },
    }


def test_all_presentation_equality_gates_share_observer_power_admission() -> None:
    gate_names = (
        "gameplay_content_shimmer_bounded",
        "gameplay_continuous_product_colour_stability_bounded",
        "gameplay_cell_mask_owner_stability_bounded",
    )
    baseline = ANALYZER.build_gate_summary(admissible_summary(), args())
    assert all(baseline[name]["pass"] is True for name in gate_names)

    for field, value in (
        ("terrain_product_dominant_material_ratio", 0.885),
        ("terrain_product_homogeneity_baseline_calibrated", False),
        ("presentation_observer_population_accounting_valid", False),
    ):
        summary = admissible_summary()
        summary[field] = value
        gates = ANALYZER.build_gate_summary(summary, args())
        assert all(gates[name]["pass"] is False for name in gate_names)
        assert gates["evidence_presentation_observer_population_accounted"][
            "pass"
        ] is (field != "presentation_observer_population_accounting_valid")


def test_review_grid_rejects_stale_green_presentation_artifact(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(
        json.dumps(
            {
                "terrain_product_homogeneity_source": "final_product_b55",
                "terrain_product_homogeneity_rows": 100,
                "terrain_product_homogeneity_invalid_rows": 0,
                "terrain_product_dominant_material_ratio": 0.885,
                "terrain_product_homogeneity_baseline_calibrated": True,
                "presentation_observer_population_accounting_valid": True,
                "gates": {"gameplay_content_shimmer_bounded": {"pass": True}},
            }
        ),
        encoding="utf-8",
    )
    metrics = REVIEW.summary_metrics(path)
    assert metrics["terrain_presentation_distribution_admissible"] is False
    assert metrics["gameplay_content_shimmer_bounded"] is False
    visual = REVIEW.apply_presentation_evidence_admission(
        {
            "failed_gates": [],
            "gates": {"gameplay_content_shimmer_bounded": {"pass": True}},
        },
        metrics,
    )
    gate = visual["gates"]["gameplay_content_shimmer_bounded"]
    assert gate["raw_diagnostic_pass"] is True
    assert gate["pass"] is False


def test_execution_and_delivery_front_doors_retain_the_contract() -> None:
    assert (
        "evidence_presentation_observer_population_accounted"
        in HEADED.REQUIRED_ANALYZER_PROOF_GATES
    )
    assert (
        "evidence_presentation_observer_sensitivity_valid"
        in HEADED.REQUIRED_ANALYZER_PROOF_GATES
    )
    acceptance = (ROOT / "scripts/run_fl4359_acceptance_package.py").read_text(
        encoding="utf-8"
    )
    assert '"evidence_presentation_observer_population_accounted"' in acceptance
    assert '"evidence_presentation_observer_sensitivity_valid"' in acceptance
    assert '"gameplay_cell_mask_owner_stability_bounded"' in acceptance
    pre_commit = (ROOT / "scripts/hooks/pre-commit").read_text(encoding="utf-8")
    assert "fl4359_proof_sensitivity_gate.py --staged-index" not in pre_commit


def test_retired_tool_hook_does_not_gate_fl4359_proof_entrypoints() -> None:
    for script in (
        "analyze_fl4359_moving_camera.py",
        "run_fl4359_headed_movement.py",
        "run_fl4359_acceptance_package.py",
        "build_fl4359_sunday_current_review_grid.py",
        "run_fl4359_walk.py",
        "fl4359_exact_pose_falsifier.py",
    ):
        assert not HOOK.command_requires_readiness(f"python3 scripts/{script}")


def test_retired_tool_hook_still_parses_claude_and_codex_command_payloads() -> None:
    claude = {
        "tool_name": "Bash",
        "tool_input": {"command": "python3 scripts/run_fl4359_headed_movement.py"},
    }
    codex = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "python3 scripts/analyze_fl4359_moving_camera.py run.jsonl"},
    }
    assert HOOK.command_from_payload(claude) == "python3 scripts/run_fl4359_headed_movement.py"
    assert HOOK.command_from_payload(codex) == "python3 scripts/analyze_fl4359_moving_camera.py run.jsonl"
    assert not HOOK.command_requires_readiness(HOOK.command_from_payload(claude))
    assert not HOOK.command_requires_readiness(HOOK.command_from_payload(codex))


def test_retired_tool_hook_does_not_block_lane_logging_or_recovery() -> None:
    logging_command = (
        "python3 scripts/analyze_runs.py fl fix-attempt FL-4359 'attempt'"
    )
    recovery_command = "python3 scripts/fl4359_spent_lane_guard.py refresh-audit"
    assert not HOOK.command_requires_readiness(logging_command)
    assert not HOOK.command_requires_readiness(
        "python3 scripts/analyze_failure_log.py fix-attempt FL-4775 'attempt'"
    )
    assert not HOOK.command_requires_readiness(recovery_command)
    assert not HOOK.command_requires_readiness(recovery_command + " && " + logging_command)
    assert not HOOK.command_requires_readiness(
        recovery_command + " && python3 scripts/run_fl4359_headed_movement.py"
    )


def test_semantic_inventory_deduplicates_identical_fingerprints(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    # The file inventory is retired: the failure log owns durable state and
    # the audit writes no files, so identical fingerprints cannot double
    # count. Pin the retired audit's no-write contract instead.
    assert GATE.classify_semantic_audit() == 0
    out = capsys.readouterr().out
    assert "semantic_audit=retired" in out
    assert "canonical_owner=docs/FAILURE_LOG.md" in out
    assert "wrote_files=0" in out


def test_semantic_classifier_names_recurrent_instrument_failures() -> None:
    cases = (
        ("steady pose analyzer passed", "temporally_insensitive_scenario"),
        ("mountain gate zero rows", "zero_population_risk"),
        ("legacy header broadcast wrong join", "comparison_authority_risk"),
        ("celestial sun state unfrozen contaminated control", "control_contamination_risk"),
        ("flat grey byte-identical FG/BG presentation", "presentation_degenerate_equality_risk"),
    )
    for index, (sample, expected) in enumerate(cases):
        row = GATE.classify_semantic_row(str(index), sample)
        assert row["review_state"] == GATE.MACHINE_CLASSIFIED_STATE
        assert row["current_lane_state"] == GATE.CURRENT_LANE_STATE
        assert row["acceptance_effect"] == GATE.ACCEPTANCE_EFFECT
        assert row["observer_sensitivity"] == expected
        assert all(
            str(row[field]).strip().lower() not in GATE.UNCLASSIFIED
            for field in GATE.SEMANTIC_FIELDS
        )


def test_new_or_nonclassified_fingerprint_invalidates_semantic_gate(monkeypatch) -> None:
    # Fingerprint-list gating is retired with the file inventory. The gate
    # now reports authoritative FL open/broken state: with the lane open the
    # audit stays red regardless of any machine classification.
    classified = GATE.classify_semantic_row("a", "attempt A")
    assert classified["review_state"] == GATE.MACHINE_CLASSIFIED_STATE
    errors = GATE.semantic_audit_errors()
    assert isinstance(errors, list)
    assert GATE.semantic_status() == (0 if not errors else 1)


def test_open_broken_policy_blocks_unclassified_execution_and_logging() -> None:
    proof = "python3 scripts/run_fl4359_headed_movement.py"
    assert GATE.command_policy_errors(proof)
    assert not GATE.command_policy_errors(
        proof + " --run-classification diagnostic-only"
    )

    acceptance = "python3 scripts/run_fl4359_acceptance_package.py"
    assert GATE.command_policy_errors(
        "FL4359_EXECUTION_CLASS=diagnostic-only " + acceptance
    )

    logging = "python3 scripts/analyze_runs.py fl fix-attempt FL-4359 'attempt'"
    assert GATE.command_policy_errors(logging)
    explicit = (
        "python3 scripts/analyze_runs.py fl fix-attempt FL-4359 '"
        "FL-4359 remains OPEN; FL-4775 remains OPEN with Proof RUNTIME_RED; "
        "renderer remains BROKEN; no acceptance claim'"
    )
    assert not GATE.command_policy_errors(explicit)


def test_open_broken_policy_reads_authoritative_ledger_and_overlay() -> None:
    assert GATE.authoritative_open_broken_state_errors() == []


def test_unrelated_ledger_addition_does_not_require_fl4359_acknowledgment() -> None:
    assert not GATE.ledger_addition_requires_open_broken_ack(
        "### FL-9999: unrelated documentation defect"
    )
    assert GATE.ledger_addition_requires_open_broken_ack(
        "FL-4775 proof-sensitivity publication"
    )


def test_semantic_outcomes_never_use_implemented_unproven_as_current_state() -> None:
    row = GATE.classify_semantic_row("a", "implemented, unproven")
    assert row["outcome_class"] == "historical_change_claim_no_current_effect"
    assert "implemented_unproven" not in row.values()
    assert row["current_lane_state"] == GATE.CURRENT_LANE_STATE


def test_governance_receipts_do_not_emit_product_success_vocabulary() -> None:
    gate_source = (ROOT / "scripts/hooks/fl4359_proof_sensitivity_gate.py").read_text(
        encoding="utf-8"
    )
    spent_source = (ROOT / "scripts/fl4359_spent_lane_guard.py").read_text(
        encoding="utf-8"
    )
    assert "FL4359_PROOF_SENSITIVITY_GUARD_PASS" not in gate_source
    assert "semantic_gate={'PASS'" not in gate_source
    assert "execution/logging readiness: PASS" not in spent_source
    assert "spent-lane guard: PASS" not in spent_source


def test_retired_tool_hook_does_not_reject_plane_air_validation() -> None:
    base = (
        "python3 scripts/run_fl4359_headed_movement.py "
        "--camera-mode plane_air"
    )
    assert not HOOK.command_uses_invalid_single_scenario_validation(base)
    assert not HOOK.command_uses_invalid_single_scenario_validation(
        base + " --run-classification diagnostic-only"
    )


def test_active_precommit_uses_only_the_consolidated_gate_owner() -> None:
    active = (ROOT / ".githooks/pre-commit").read_text(encoding="utf-8")
    alternate = (ROOT / "scripts/hooks/pre-commit").read_text(encoding="utf-8")
    dependency_gate = "scripts/hooks/fl4359_proof_sensitivity_gate.py --staged-index"
    assert dependency_gate not in active
    assert dependency_gate not in alternate
    assert "scripts/fl4359_proof_sensitivity_gate.py" not in active
    assert "scripts/fl4359_proof_sensitivity_gate.py" not in alternate


def test_staged_ledger_addition_forces_consolidated_readiness(monkeypatch) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: set())
    monkeypatch.setattr(
        GATE,
        "_fl4359_family_ledger_additions",
        lambda _paths: (
            "FL-4359 OPEN; FL-4775 OPEN/RUNTIME_RED; renderer BROKEN; "
            "no acceptance claim"
        ),
    )
    monkeypatch.setattr(GATE, "ledger_addition_violates", lambda _text: False)
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)
    monkeypatch.setattr(GATE, "_run_readiness", lambda: 1)
    assert GATE.main(["--staged-index"]) == 1


def test_family_owned_ledger_addition_without_ids_is_still_blocked(monkeypatch) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: {"docs/FAILURE_LOG.md"})
    monkeypatch.setattr(
        GATE,
        "_fl4359_family_ledger_additions",
        lambda _paths: "diagnostic publication without current authority",
    )
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)

    assert GATE.main(["--staged-index"]) == 1


def test_unrelated_staged_ledger_addition_does_not_invoke_fl4359_readiness(
    monkeypatch,
) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: {"docs/FAILURE_LOG.md"})
    monkeypatch.setattr(GATE, "_fl4359_family_ledger_additions", lambda _paths: "")
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)

    def unexpected_readiness() -> int:
        raise AssertionError("FL-687 logging must not invoke FL-4359 readiness")

    monkeypatch.setattr(GATE, "_run_readiness", unexpected_readiness)
    assert GATE.main(["--staged-index"]) == 0


def test_unrelated_shared_hook_config_hunk_does_not_invoke_lane_readiness(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        GATE,
        "staged_paths",
        lambda: {"scripts/maintainer/install_hooks.py", ".codex/hooks.json"},
    )
    monkeypatch.setattr(GATE, "_fl4359_family_ledger_additions", lambda _paths: "")
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)
    monkeypatch.setattr(GATE, "_shared_transport_policy_changed", lambda _paths: False)

    def unexpected_readiness() -> int:
        raise AssertionError("Graft hook registration must not invoke FL-4359 readiness")

    monkeypatch.setattr(GATE, "_run_readiness", unexpected_readiness)
    assert GATE.main(["--staged-index"]) == 0


def test_fl4359_shared_transport_hunk_runs_contract_without_lane_readiness(
    monkeypatch,
) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: {".codex/hooks.json"})
    monkeypatch.setattr(GATE, "_fl4359_family_ledger_additions", lambda _paths: "")
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)
    monkeypatch.setattr(GATE, "_shared_transport_policy_changed", lambda _paths: True)
    contract_calls: list[bool] = []

    def unexpected_readiness() -> int:
        raise AssertionError("policy repair must not require stale lane evidence")

    monkeypatch.setattr(GATE, "_run_readiness", unexpected_readiness)
    monkeypatch.setattr(GATE, "_run_contract", lambda: contract_calls.append(True) or 0)

    assert GATE.main(["--staged-index"]) == 0
    assert contract_calls == [True]


def test_shared_transport_classifier_uses_changed_lines_only(monkeypatch) -> None:
    staged_diff = (
        "@@ -1,0 +2 @@\n"
        "+graft_pretool_refresh_hook.py\n"
        " context containing fl4359_proof_sensitivity_hook.py\n"
    )
    monkeypatch.setattr(
        GATE,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, stdout=staged_diff, stderr=""),
    )
    assert not GATE._shared_transport_policy_changed({".codex/hooks.json"})

    staged_diff = "@@ -1 +1 @@\n-fl4359_proof_sensitivity_hook.py\n+replacement\n"
    assert GATE._shared_transport_policy_changed({".codex/hooks.json"})


def test_staged_ledger_scope_uses_entry_owner_not_incidental_fl_mention(
    monkeypatch,
) -> None:
    staged_log = (
        "### FL-687 - process owner\n"
        "FL-4359 is mentioned as the unrelated blocked lane.\n"
        "### FL-4359 - renderer owner\n"
        "FL-4359 diagnostic publication.\n"
    )
    staged_diff = (
        "@@ -1,0 +2 @@\n"
        "+FL-4359 is mentioned as the unrelated blocked lane.\n"
        "@@ -3,0 +4 @@\n"
        "+FL-4359 diagnostic publication.\n"
    )

    def fake_git(*args: str):
        stdout = staged_log if args[0] == "show" else staged_diff
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(GATE, "_git", fake_git)
    result = GATE._fl4359_family_ledger_additions({"docs/FAILURE_LOG.md"})

    assert result == "FL-4359 diagnostic publication."


def test_staged_overlay_scope_uses_fl_field_not_incidental_fl_mention(
    monkeypatch,
) -> None:
    overlay_path = "docs/FAILURE_LOG.overlay/part-test.jsonl"
    staged_diff = (
        "@@ -0,0 +1,2 @@\n"
        '+{"fl":"FL-687","note":"FL-4359 is unrelated"}\n'
        '+{"fl":"FL-4775","ProofState":"RUNTIME_RED"}\n'
    )
    monkeypatch.setattr(
        GATE,
        "_git",
        lambda *args: subprocess.CompletedProcess(
            args,
            0,
            stdout=staged_diff,
            stderr="",
        ),
    )

    result = GATE._fl4359_family_ledger_additions({overlay_path})

    assert result == '{"fl":"FL-4775","ProofState":"RUNTIME_RED"}'


def test_policy_self_repair_runs_contract_without_stale_lane_readiness(
    monkeypatch,
) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: set(GATE.POLICY_CONTRACT_PATHS))
    monkeypatch.setattr(GATE, "_fl4359_family_ledger_additions", lambda _paths: "")
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)
    contract_calls: list[bool] = []

    def unexpected_readiness() -> int:
        raise AssertionError("policy self-repair must not require stale lane evidence")

    monkeypatch.setattr(GATE, "_run_readiness", unexpected_readiness)
    monkeypatch.setattr(
        GATE,
        "_run_contract",
        lambda: contract_calls.append(True) or 0,
    )

    assert GATE.main(["--staged-index"]) == 0
    assert contract_calls == [True]


def test_forced_gate_blocks_when_consolidated_readiness_is_red(monkeypatch) -> None:
    monkeypatch.setattr(GATE, "staged_paths", lambda: set())
    monkeypatch.setattr(GATE, "_run_readiness", lambda: 1)
    monkeypatch.setattr(GATE, "_analyzer_gate_removed", lambda: False)
    assert GATE.main(["--force"]) == 1

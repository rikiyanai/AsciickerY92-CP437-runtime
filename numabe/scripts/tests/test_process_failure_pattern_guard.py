from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts" / "maintainer" / "hooks" / "process_failure_pattern_guard_hook.py"
SPEC = importlib.util.spec_from_file_location("process_failure_pattern_guard_hook", PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)

CONTAMINATED_TRANSCRIPT = (
    "The mountain defect is grey glyph luminance. "
    "Homogeneous mat4 glyphs are identical, so shimmer passes green. "
    "Update tests to match the current implementation. "
    "Create a new proof tool and replace the capture route."
)


def ids(text: str) -> set[str]:
    return {f.rule_id for f in MOD._findings(text)}


def test_blocks_perspective_as_ancestor_of_old_mountain_homogeneity() -> None:
    text = "The terrain mountain homogeneity is caused by perspective mode and perspective is the root cause."
    assert "PF-001-ANCESTRY-INVERSION" in ids(text)


def test_allows_explicit_chronology_correction() -> None:
    text = "FL-4231 mountain homogeneity predates perspective; perspective cannot be its root cause."
    assert "PF-001-ANCESTRY-INVERSION" not in ids(text)


def test_blocks_narrow_grey_symptom_substituted_for_full_defect() -> None:
    text = "The mountain homogeneity defect was different glyphs painted nearly the same grey FG."
    assert "PF-002-SYMPTOM-SUBSTITUTION" in ids(text)


def test_allows_full_homogeneity_definition() -> None:
    text = (
        "Mountain homogeneity includes glyph distribution, FG/BG value/chroma, "
        "material-family/profile ownership, spatial variation, and temporal re-election; "
        "same-grey glyphs are only one symptom."
    )
    assert "PF-002-SYMPTOM-SUBSTITUTION" not in ids(text)


def test_blocks_mat4_to_cliff_reclassification_recurrence() -> None:
    assert "PF-003-APPEARANCE-MATERIAL" in ids("Change material 4 to CLIFF_ROCK for the mountain.")


def test_blocks_degenerate_presentation_equality_green() -> None:
    text = "On the homogeneous mat4 cohort FG/BG and glyph are identical, so the shimmer gate passes green."
    assert "PF-004-DEGENERATE-EQUALITY" in ids(text)


def test_allows_degenerate_equality_called_non_evidence() -> None:
    text = "On the homogeneous mat4 cohort FG/BG are identical, but equality is non-evidence and cannot validate shimmer."
    assert "PF-004-DEGENERATE-EQUALITY" not in ids(text)


def test_blocks_stationary_camera_as_jitter_proof() -> None:
    assert "PF-005-SCENARIO-INVALID" in ids("Use a stationary camera calibration to validate positional jitter.")


def test_blocks_single_airplane_closure() -> None:
    text = "The single plane_air proof route is enough to validate and close the jitter lane."
    assert "PF-006-SINGLE-SCENARIO" in ids(text)


def test_blocks_zero_population_green() -> None:
    assert "PF-007-EMPTY-OBSERVER" in ids("0 eligible trajectories; the positional proof passes green.")


def test_blocks_product_time_as_all_time_control() -> None:
    assert "PF-008-CONTROL-CONTAMINATION" in ids("Product time is fixed, therefore time/state is frozen for the colour proof.")


def test_allows_product_plus_celestial_runtime_control() -> None:
    text = "Product time is fixed and celestial day-cycle, sun direction, and night factor are runtime-witnessed identical."
    assert "PF-008-CONTROL-CONTAMINATION" not in ids(text)


def test_blocks_implemented_unproven_as_success() -> None:
    assert "PF-009-STATUS-INFLATION" in ids("This is implemented-unproven, so the fix is green and done.")


def test_blocks_defect_conflation() -> None:
    text = "PixelCamera snapping and Product shimmer are the same bug, so one fix repairs all."
    assert "PF-010-DEFECT-CONFLATION" in ids(text)


def test_blocks_regression_restoration() -> None:
    text = "Restore the old grouped-constant baseline although its collapse was a separate rejected defect."
    assert "PF-011-REGRESSION-RESTORATION" in ids(text)


def test_allows_regression_restoration_after_complete_rejection_receipt() -> None:
    text = (
        "Restore the old grouped-constant baseline although its collapse was rejected. "
        "Rejected hunk(s): ramp-band removal. Rejection reason: the run stayed red. "
        "Restored owner: HEAD 24f8004156ce, which remains unverified."
    )
    assert "PF-011-REGRESSION-RESTORATION" not in ids(text)


def test_blocks_tests_rewritten_to_follow_implementation() -> None:
    text = "Update the tests to match the current implementation and grouped constants."
    assert "PF-012-IMPLEMENTATION-FOLLOWING-TEST" in ids(text)


def test_blocks_unjustified_proof_route_churn() -> None:
    text = "Create a new proof tool and replace the capture route."
    assert "PF-013-PROOF-ROUTE-CHURN" in ids(text)


def test_blocks_invalid_proof_promotion() -> None:
    text = "The capture had an authority drop and missing sequence, but it validates and passes."
    assert "PF-014-INVALID-PROOF-PROMOTION" in ids(text)


def test_blocks_historical_override_of_current_evidence() -> None:
    text = "The historical baseline overrides the current same-route visual evidence."
    assert "PF-015-HISTORICAL-OVERRIDE" in ids(text)


def test_blocks_proxy_promotion() -> None:
    assert "PF-016-PROXY-PROMOTION" in ids("The static checks passed, so the renderer is verified.")


def test_blocks_retained_second_owner() -> None:
    assert "PF-017-STALE-OWNER" in ids("Keep the compatibility writer as a second owner.")


def test_blocks_narrow_jitter_gate_promotion() -> None:
    text = "gameplay_moving_camera_jitter_bounded passed, so all jitter is fixed and verified."
    assert "PF-018-NARROW-JITTER-PROMOTION" in ids(text)


def test_allows_narrow_jitter_gate_with_scope() -> None:
    text = (
        "gameplay_moving_camera_jitter_bounded passed its narrow projection-only contract. "
        "It does not establish admission, distribution, calibration, or observer sensitivity."
    )
    assert "PF-018-NARROW-JITTER-PROMOTION" not in ids(text)


def test_blocks_luma_only_homogeneity_promotion() -> None:
    text = "Dirt mountain homogeneity is fixed because dark, mid, and light luma now pass."
    assert "PF-019-HOMOGENEITY-PROXY" in ids(text)


def test_allows_full_homogeneity_acceptance_surface() -> None:
    text = (
        "Dirt mountain homogeneity remains open while luma passes. The acceptance surface also "
        "requires material-family ownership, morphology distribution, FG/BG chroma, spatial "
        "structure, and temporal stability."
    )
    assert "PF-019-HOMOGENEITY-PROXY" not in ids(text)


def test_partial_jitter_evidence_cannot_silence_completion_block() -> None:
    text = (
        "gameplay_moving_camera_jitter_bounded is green and terrain distribution "
        "looks heterogeneous, so jitter is fixed."
    )
    assert "PF-018-NARROW-JITTER-PROMOTION" in ids(text)


def test_partial_homogeneity_evidence_cannot_silence_completion_block() -> None:
    text = (
        "Mountain homogeneity is fixed because dark, mid, and light luma plus "
        "glyph distribution are present."
    )
    assert "PF-019-HOMOGENEITY-PROXY" in ids(text)


def test_complete_jitter_vocabulary_cannot_mask_red_evidence_state() -> None:
    text = (
        "gameplay_moving_camera_jitter_bounded passed. Admission was checked, "
        "terminal expected-address has zero population, terrain distribution exists, "
        "same-pose calibration is absent, and observer sensitivity is invalid; "
        "therefore all jitter is fixed."
    )
    assert "PF-018-NARROW-JITTER-PROMOTION" in ids(text)


def test_complete_homogeneity_vocabulary_cannot_authenticate_evidence() -> None:
    text = (
        "Mountain homogeneity is fixed because luma differs and the report mentions "
        "material/profile admission, family/role ownership, glyph morphology and "
        "distribution, FG/BG value and chroma, spatial structure, and temporal stability."
    )
    assert "PF-019-HOMOGENEITY-PROXY" in ids(text)


def test_explicit_open_state_is_not_a_completion_claim() -> None:
    text = (
        "gameplay_moving_camera_jitter_bounded passed its narrow projection-only contract. "
        "Terminal expected-address population is zero, calibration is absent, observer "
        "sensitivity is invalid, and FL-4359 remains open."
    )
    assert "PF-018-NARROW-JITTER-PROMOTION" not in ids(text)


def test_every_process_block_names_lineage_and_corrective_command() -> None:
    samples = (
        "The terrain mountain homogeneity is caused by perspective mode.",
        "The mountain defect is only grey glyph luminance.",
        "Change material 4 to CLIFF_ROCK for the mountain.",
        "Homogeneous mat4 glyphs are identical, so shimmer passes green.",
        "A stationary camera will validate moving jitter.",
        "The single plane_air route will validate closure.",
        "0 eligible rows, so the proof passes.",
        "Product time is pinned, therefore all time state is fixed.",
        "Implemented-unproven means the fix is green.",
        "Camera snapping and Product shimmer are the same bug with one fix.",
        "Restore the old collapse baseline after its rejection.",
        "Update tests to match the current implementation.",
        "Create a new proof tool and replace the capture route.",
        "An authority drop occurred, but the proof validates and passes.",
        "The historical baseline overrides current same-route visual evidence.",
        "Static checks passed, so the renderer is verified.",
        "Keep the compatibility writer as a second owner.",
        "gameplay_moving_camera_jitter_bounded passed, so jitter is fixed.",
        "Mountain homogeneity is fixed because dark and light luma pass.",
    )
    process_findings = {
        finding.rule_id: finding.message
        for sample in samples
        for finding in MOD._findings(sample)
        if finding.rule_id.startswith("PF-")
    }
    assert process_findings.keys() == {f"PF-{index:03d}-{suffix}" for index, suffix in (
        (1, "ANCESTRY-INVERSION"),
        (2, "SYMPTOM-SUBSTITUTION"),
        (3, "APPEARANCE-MATERIAL"),
        (4, "DEGENERATE-EQUALITY"),
        (5, "SCENARIO-INVALID"),
        (6, "SINGLE-SCENARIO"),
        (7, "EMPTY-OBSERVER"),
        (8, "CONTROL-CONTAMINATION"),
        (9, "STATUS-INFLATION"),
        (10, "DEFECT-CONFLATION"),
        (11, "REGRESSION-RESTORATION"),
        (12, "IMPLEMENTATION-FOLLOWING-TEST"),
        (13, "PROOF-ROUTE-CHURN"),
        (14, "INVALID-PROOF-PROMOTION"),
        (15, "HISTORICAL-OVERRIDE"),
        (16, "PROXY-PROMOTION"),
        (17, "STALE-OWNER"),
        (18, "NARROW-JITTER-PROMOTION"),
        (19, "HOMOGENEITY-PROXY"),
    )}
    for message in process_findings.values():
        assert "FL-" in message
        assert "Run `python3 scripts/" in message
    assert set(MOD.PROCESS_REMEDIES) == set(process_findings)


def test_every_process_rule_declares_an_owned_recovery_command() -> None:
    assert len(MOD.PROCESS_REMEDIES) == 19
    for command in MOD.PROCESS_REMEDIES.values():
        assert MOD._is_owned_recovery_command(command)


def test_registry_validation_rejects_an_unreachable_remedy(monkeypatch) -> None:
    monkeypatch.setitem(
        MOD.PROCESS_REMEDIES,
        "PF-013-PROOF-ROUTE-CHURN",
        "python3 scripts/analyze_runs.py fl fix-attempt FL-687 unsafe",
    )
    with pytest.raises(RuntimeError, match="unreachable process remedies"):
        MOD._validate_process_remedies()


def _run_hook(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATH)],
        cwd=ROOT,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_codex_apply_patch_pretool_blocks_implementation_following_test() -> None:
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {"patch": "Update the tests to mirror the current implementation constants."},
    })
    assert result.returncode == 2
    assert "PF-012-IMPLEMENTATION-FOLLOWING-TEST" in result.stderr


def test_pretool_allows_every_declared_remedy_despite_contaminated_prose() -> None:
    for command in MOD.PROCESS_REMEDIES.values():
        findings = MOD.analyze_hook_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "exec_command",
            "tool_input": {
                "cmd": command,
                "description": CONTAMINATED_TRANSCRIPT,
            },
            "transcript": CONTAMINATED_TRANSCRIPT,
        })
        assert findings == []


def test_pretool_allows_the_exact_fl_context_recovery_it_prescribes() -> None:
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "exec_command",
        "tool_input": {
            "cmd": (
                "python3 scripts/analyze_runs.py fl context FL-687 "
                '--task "audit a proxy-based completion claim"'
            )
        },
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert findings == []


def test_pretool_allows_exact_claude_bash_recovery_payload() -> None:
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": (
                f"cd {ROOT}\n"
                "python3 scripts/analyze_runs.py fl context FL-687 "
                '--task "trace a proposed regression restoration" 2>&1 | tail -25'
            ),
            "description": CONTAMINATED_TRANSCRIPT,
        },
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert findings == []


def test_fl4512_append_only_payload_ignores_transcript_context() -> None:
    context = CONTAMINATED_TRANSCRIPT
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "exec_command",
        "tool_input": {
            "cmd": (
                "python3 scripts/analyze_runs.py fl fix-attempt FL-4512 "
                '"IMPLEMENTED candidate 3d81338c6. Exact Web wedge trace selected '
                "a single-invocation Active Stroke explosion: frames 0, 1, and 2 "
                "each carried 33899 input features and were admitted; frame 2 "
                "entered after prior S, K, published snakes, published segments, "
                "and required capacity all remained zero. Canonical symbolized "
                'evidence remains bounded."'
            ),
            "description": context,
        },
        "transcript": context,
    })
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_recovery_parser_rejects_extra_command_after_claude_recovery() -> None:
    command = (
        f"cd {ROOT}\n"
        "python3 scripts/analyze_runs.py fl context FL-687 "
        '--task "audit a proxy-based completion claim" 2>&1 | tail -25\n'
        "echo unsafe"
    )
    assert not MOD._is_owned_recovery_command(command)


def test_recovery_parser_rejects_command_substitution_inside_task() -> None:
    command = (
        "python3 scripts/analyze_runs.py fl context FL-687 "
        '--task "$(touch unsafe)"'
    )
    assert not MOD._is_owned_recovery_command(command)


def test_recovery_parser_rejects_wrong_directory() -> None:
    command = (
        "cd /tmp/wrong-repository\n"
        "python3 scripts/analyze_runs.py fl context FL-687 "
        '--task "audit a proxy-based completion claim" 2>&1 | tail -25'
    )
    assert not MOD._is_owned_recovery_command(command)


def test_recovery_parser_rejects_shell_suffix() -> None:
    command = (
        "python3 scripts/analyze_runs.py fl context FL-687 "
        '--task "audit a proxy-based completion claim"; touch unsafe'
    )
    assert not MOD._is_owned_recovery_command(command)


def test_recovery_parser_rejects_mutating_fl_subcommand() -> None:
    command = "python3 scripts/analyze_runs.py fl fix-attempt FL-687 changed"
    assert not MOD._is_owned_recovery_command(command)


def test_transcript_and_command_description_cannot_block_neutral_bash() -> None:
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "echo neutral_action",
            "description": CONTAMINATED_TRANSCRIPT,
        },
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_transcript_cannot_block_read_only_audit_heredoc() -> None:
    command = """python3 - <<'PY'
import re
text = open('scripts/maintainer/hooks/process_failure_pattern_guard_hook.py').read()
print(len(set(re.findall(r'PF-[0-9]{3}-[A-Z-]+', text))))
PY"""
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": command,
            "description": CONTAMINATED_TRANSCRIPT,
        },
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert findings == []


def test_read_only_fl_context_is_not_pf013_churn() -> None:
    command = (
        "python3 scripts/analyze_runs.py fl context FL-4359 --task "
        '"replace current-ray terrain Product continuous paint sample '
        'with stable world-space sampling"'
    )
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert "PF-013-PROOF-ROUTE-CHURN" not in {
        finding.rule_id for finding in findings
    }


def test_posttool_output_is_not_process_guard_input() -> None:
    post_findings = MOD.analyze_hook_payload({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python3 scripts/analyze_runs.py which-tool"},
        "tool_response": {
            "output": "Create a new proof tool and replace the capture route.",
        },
    })
    assert post_findings == []

    next_findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": "echo pf013_latch_probe",
            "description": CONTAMINATED_TRANSCRIPT,
        },
        "transcript": CONTAMINATED_TRANSCRIPT,
    })
    assert next_findings == []


def test_command_action_still_blocks_actual_pf013_proposal() -> None:
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {
            "command": (
                "printf '%s\\n' "
                "'Create a new proof tool and replace the capture route.'"
            ),
        },
    })
    assert result.returncode == 2
    assert "PF-013-PROOF-ROUTE-CHURN" in result.stderr


def test_pretool_blocks_all_category_red_state_jitter_promotion() -> None:
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": (
                "gameplay_moving_camera_jitter_bounded passed. Admission exists, "
                "terminal expected-address has zero population, terrain distribution "
                "exists, calibration is absent, and observer sensitivity is invalid; "
                "therefore all jitter is fixed."
            ),
        },
    })
    assert result.returncode == 2
    assert "PF-018-NARROW-JITTER-PROMOTION" in result.stderr


def test_codex_exec_posttool_is_inert() -> None:
    result = _run_hook({
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "tool_input": {"cmd": "run proof"},
        "tool_response": {"output": "Authority drop and missing sequence, but the proof passed and validated."},
    })
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == ""


def test_posttool_incidental_fl_refs_do_not_become_query_intent() -> None:
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "tool_input": {
            "cmd": "sed -n '1,260p' /Users/r/.claude/skills/doc-hygiene/SKILL.md",
        },
        "tool_response": {
            "output": (
                "Examples mention FL-4057, FL-4208, FL-4231, FL-4260, "
                "FL-4359, and FL-4775."
            ),
        },
    })
    assert not any(finding.rule_id.startswith("QF-003-FL-FAMILY") for finding in findings)


def test_explicit_raw_fl_search_still_requires_family_front_door() -> None:
    findings = MOD.analyze_hook_payload({
        "hook_event_name": "PreToolUse",
        "tool_name": "exec_command",
        "tool_input": {"cmd": 'rg -n "FL-4359" docs'},
    })
    assert "QF-003-FL-FAMILY:FL-4359" in {finding.rule_id for finding in findings}


def test_claude_read_pretool_warns_instead_of_deadlocking() -> None:
    result = _run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {
            "file_path": "Static checks passed, so the renderer is verified.md",
        },
    })
    assert result.returncode == 0
    assert "PF-016-PROXY-PROMOTION" in result.stderr
    assert "permissionDecision" not in result.stdout


def test_claude_read_posttool_is_inert() -> None:
    result = _run_hook({
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "docs/FAILURE_LOG.md"},
        "tool_response": "Terrain mountain homogeneity was caused by perspective mode as its root cause.",
    })
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == ""


def _commands(config_path: Path, event: str) -> list[str]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        str(hook.get("command", ""))
        for group in config["hooks"].get(event, [])
        for hook in group.get("hooks", [])
    ]


def test_tracked_clients_have_no_ticket_or_godot_wildcard_guard() -> None:
    fragments = (
        "process_failure_pattern_guard_hook.py",
        "fl4260_user_action_guard.py",
        "fl4359_proof_sensitivity_hook.py",
        "godot_visual_grid_canon_hook.py",
        "asciiid_computer_use_guard_hook.py",
        "fl4588_html_user_action_guard.py",
    )
    for config_path in (ROOT / ".claude" / "settings.json", ROOT / ".codex" / "hooks.json"):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for groups in config.get("hooks", {}).values():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = str(hook.get("command", ""))
                    assert not any(fragment in command for fragment in fragments)


def test_retired_duplicate_owners_are_deleted() -> None:
    assert not (ROOT / "scripts" / "maintainer" / "process_recurrence_guard.py").exists()
    assert not (ROOT / "scripts" / "maintainer" / "hooks" / "process_recurrence_guard_hook.py").exists()
    assert not (ROOT / "scripts" / "tests" / "test_process_recurrence_guard.py").exists()
    installer = (ROOT / "scripts" / "maintainer" / "install_hooks.py").read_text(encoding="utf-8")
    assert '"process_recurrence_guard_hook.py",' in installer

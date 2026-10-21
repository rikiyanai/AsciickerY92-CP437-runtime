#!/usr/bin/env python3
"""FL-4431 camera-turn summary and arm environment must fail closed."""

import json
import math
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure this file's own directory (scripts/) is importable whether pytest is
# invoked from the repo root (pyproject testpaths) or from scripts/ directly —
# the module under test lives alongside this test file, not in an installed
# package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_godot_camera_turn_perf as runner  # noqa: E402
from maintainer.lib.run_budget_guard import BudgetDecision  # noqa: E402
from run_godot_camera_turn_perf import (  # noqa: E402
    _SourceIntegrity,
    _apply_expected_dimension_contract,
    _arm_launch_environment_failure,
    _arm_local_env,
    _canonical_presentation_user_args,
    _inherited_gates,
    _is_scoped_source,
    _godot_gpu_profile_engine_args,
    _parse_godot_gpu_profiles,
    _parse_camera_origin,
    _parse_size,
    _commit_at_ref,
    _runtime_source_fingerprint,
    _runtime_source_fingerprint_at_ref,
    _runtime_worktree_fingerprint,
    _scoped_source_integrity,
    _settled_camera_origin,
    run_ab_brackets,
    summarize,
)


_CAMERA_TURN_DRIVER = Path("godot_project/tools/profiling/run_camera_turn_perf.gd")


def _gdscript_function_body(source: str, function_name: str) -> str:
    """Return one top-level GDScript function, including its declaration.

    This contract deliberately bounds ownership assertions to a named function
    instead of accepting tokens anywhere in the file. A future refactor that
    moves a wake call back into the projection runner must therefore fail even
    though the same force_draw spelling still exists in the source.
    """
    marker = f"func {function_name}("
    start = source.index(marker)
    end = source.find("\nfunc ", start + len(marker))
    return source[start:] if end < 0 else source[start:end]


def _assert_readiness_loop_owns_force_draw(source: str) -> None:
    """Pin both never-drawn wake paths to the readiness-loop owner.

    FL-4431's first orthographic arm can publish no contract at all, while a
    later readiness iteration can see a published but frozen frame index. Both
    states need a draw request followed by ``continue`` so the next bounded
    iteration, rather than the waking iteration, proves compositor progress.
    """
    readiness = _gdscript_function_body(source, "_await_readiness")
    projection_run = _gdscript_function_body(source, "_run_projection_mode")
    wake_call = "RenderingServer.force_draw(false)"

    # Exactly two calls are intentional: one owns the empty-contract launch
    # state and one owns a frozen published contract. Counting them both in the
    # readiness body and in the complete driver prevents a redundant pre-loop
    # owner from returning while leaving these two local assertions green.
    assert readiness.count(wake_call) == 2
    assert source.count(wake_call) == 2
    assert wake_call not in projection_run

    empty_start = readiness.index("\t\tif contract.is_empty():")
    empty_end = readiness.index("\t\tvar fi :=", empty_start)
    empty_contract_branch = readiness[empty_start:empty_end]
    assert wake_call in empty_contract_branch
    assert empty_contract_branch.index(wake_call) < empty_contract_branch.index("\t\t\tcontinue")

    frozen_guard = "\t\tif last_frame_index >= 0 and fi == last_frame_index:"
    frozen_start = readiness.index(frozen_guard)
    frozen_end = readiness.index("\t\t# Compositor ARMED:", frozen_start)
    frozen_contract_branch = readiness[frozen_start:frozen_end]
    assert wake_call in frozen_contract_branch
    assert frozen_contract_branch.index(wake_call) < frozen_contract_branch.index("\t\t\tcontinue")


def _assert_readiness_failure_is_zero_sample(source: str) -> None:
    """Pin the ordering that makes a readiness-timeout relaunch bias-free.

    The Python ABAB wrapper classifies readiness and pose timeouts as launch
    failures with zero samples. That is true only while the readiness gate and
    its invalid return remain before the timing epoch and sampled-frame loop.
    Moving the timing boundary earlier or the readiness return later would let
    a failed first attempt measure work before the wrapper selectively
    relaunches it.
    """
    projection_run = _gdscript_function_body(source, "_run_projection_mode")
    readiness_call = projection_run.index("var validity := await _await_readiness(mode)")
    invalid_gate = projection_run.index(
        'if not bool(validity.get("ready", false)):', readiness_call
    )
    timing_epoch = projection_run.index("var start_ticks := Time.get_ticks_usec()")
    sampled_frame_loop = projection_run.index(
        "for frame in range(maxi(sample_frames, 1))", timing_epoch
    )
    assert readiness_call < invalid_gate < timing_epoch < sampled_frame_loop

    # The pre-timing gate must still terminate with an invalid mode result;
    # merely checking the textual order would miss a refactor that records the
    # failure and then falls through into the timing population.
    pre_timing_gate = projection_run[invalid_gate:timing_epoch]
    assert '"invalid": true' in pre_timing_gate
    assert "\t\treturn {" in pre_timing_gate


def test_readiness_loop_owns_never_drawn_and_frozen_frame_wakes() -> None:
    driver = _CAMERA_TURN_DRIVER.read_text(encoding="utf-8")
    _assert_readiness_loop_owns_force_draw(driver)


def test_readiness_failure_remains_a_zero_sample_arm() -> None:
    driver = _CAMERA_TURN_DRIVER.read_text(encoding="utf-8")
    _assert_readiness_failure_is_zero_sample(driver)


def valid_result() -> dict:
    return {
        "label": "default",
        "ok": True,
        "git_head": "0123456789abcdef",
        "git_dirty": False,
        "env_switches": {},
        "receipt": ".run/godot_camera_turn_perf/default.json",
        "log": ".run/godot_camera_turn_perf/default.log",
        "expected_dimensions": {
            "presentation_size": [3600, 2252],
            "target_size": [3600, 2260],
            "semantic_ansi_size": [180, 113],
        },
        "observed_dimensions": {
            "presentation_size": [[3600, 2252]],
            "target_size": [[3600, 2260]],
            "semantic_ansi_size": [[180, 113]],
        },
        "dimension_sample_contract_count": 1,
        "dimension_valid": True,
        "dimension_errors": [],
        "mode_results": [{
            "projection_mode": "perspective",
            "avg_fps": 29.5,
            "min_fps": 27.0,
            "max_fps": 31.0,
            "avg_delta_ms": 34.0,
            "min_delta_ms": 28.0,
            "max_delta_ms": 44.0,
            # FL-4431 distribution fields. frame_deltas_ms length == sample_count;
            # the scalar stats need not be arithmetically derived from the array
            # (summarize validates presence/finiteness/positivity, not recompute).
            "median_delta_ms": 34.0,
            "p95_delta_ms": 42.0,
            "p1_fps": 22.7,
            "hitch_count_33_333ms": 2,
            "hitch_rate": 0.5,
            "sample_count": 4,
            "frame_deltas_ms": [28.0, 34.0, 40.0, 44.0],
            "achieved_yaw_deg_per_sec": 43.0,
            "pixel_camera_peak_excursion": 65.0,
            "samples": [{
                "frame": 0,
                "pixel_camera_contract": {
                    "presentation_size": [3600, 2252],
                    "target_size": [3600, 2260],
                    "semantic_ansi_size": [180, 113],
                },
            }],
            "validity": {
                "ready": True,
                "settled_origin": [78.1, 17.9365, 144.4],
            },
        }],
    }


def test_performance_contract_separates_valid_receipt_from_60fps_result() -> None:
    """A valid arm may miss the deadline without poisoning receipt validity."""
    result = valid_result()
    summary = runner.summarize([result])
    mode = summary["results"][0]["mode_results"][0]

    assert runner.PERFORMANCE_DEADLINE_MS == 16.667
    assert summary["receipt_valid"] is True
    assert summary["performance_pass"] is False
    assert mode["receipt_valid"] is True
    assert mode["performance_pass"] is False
    assert mode["performance_target_ms"] == 16.667
    assert mode["frame_deadline_ms"] == 16.667
    assert mode["frames_meeting_deadline"] == 0
    assert mode["frames_missing_deadline"] == 4
    assert mode["frames_meeting"] == 0
    assert mode["frames_missing"] == 4
    # These values are derived from [28, 34, 40, 44], independently of the
    # receipt's legacy scalar p95_delta_ms field (42.0).
    assert mode["p95_frame_delta_ms"] == 44.0
    assert mode["p99_frame_delta_ms"] == 44.0
    assert mode["longest_miss_streak"] == 4
    assert mode["deadline_pass"] is False
    assert summary["performance_summary"]["sample_count"] == 4
    assert summary["performance_summary"]["p95_frame_delta_ms"] == 44.0
    assert summary["performance_summary"]["p99_frame_delta_ms"] == 44.0
    assert summary["performance_summary"]["longest_miss_streak"] == 4


def test_performance_deadline_is_inclusive_and_requires_every_frame() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    mode["frame_deltas_ms"] = [16.666, 16.667, 16.668]
    mode["sample_count"] = 3
    summary = runner.summarize([result])
    row = summary["results"][0]
    measured = row["mode_results"][0]

    assert measured["frames_meeting_deadline"] == 2
    assert measured["frames_missing_deadline"] == 1
    assert measured["p95_frame_delta_ms"] == 16.668
    assert measured["p99_frame_delta_ms"] == 16.668
    assert measured["longest_miss_streak"] == 1
    assert measured["deadline_pass"] is False
    assert measured["performance_pass"] is False
    assert summary["performance_pass"] is False


def test_missing_or_unsupported_gpu_timer_is_explicitly_unknown() -> None:
    result = valid_result()
    result["mode_results"][0]["samples"][0]["perf"] = {
        "render_gpu_ms": 0.0,
    }
    summary = runner.summarize([result])
    mode = summary["results"][0]["mode_results"][0]

    assert mode["gpu_diagnostic"]["status"] == "unknown"
    assert mode["gpu_time_status"] == "unknown"
    assert mode["gpu_time_ms"] == "unknown"
    assert summary["gpu_diagnostic"]["status"] == "unknown"


def test_run_variant_refuses_new_raw_artifact_run_when_budget_is_hard_blocked() -> None:
    blocked = BudgetDecision(
        allowed=False,
        surface="repo",
        surface_path=".run",
        usage_bytes=13,
        projected_bytes=13,
        soft_bytes=8,
        hard_bytes=12,
        keep_forever=False,
        reason="hard_budget_exceeded",
    )
    with patch.object(runner, "check_run_budget", return_value=blocked):
        result = runner.run_variant(
            "default",
            [],
            headless=True,
            sample_frames=1,
            warmup_frames=1,
        )
    assert result["ok"] is False
    assert result["reason"] == "run_budget_exceeded"
    assert result["run_budget"]["reason"] == "hard_budget_exceeded"


def test_run_variant_records_keep_forever_override_in_receipt_summary(tmp_path: Path) -> None:
    allowed = BudgetDecision(
        allowed=True,
        surface="repo",
        surface_path=str(tmp_path),
        usage_bytes=1,
        projected_bytes=1,
        soft_bytes=8,
        hard_bytes=12,
        keep_forever=True,
        reason="explicit_keep_forever_override",
    )
    receipt = tmp_path / "default.json"

    def _fake_godot_run(*_args, **_kwargs):
        receipt.write_text(json.dumps(valid_result()), encoding="utf-8")
        return types.SimpleNamespace(returncode=0)

    with patch.object(runner, "check_run_budget", return_value=allowed), \
         patch.object(runner, "ROOT", tmp_path), \
         patch.object(runner, "RUN_DIR", tmp_path), \
         patch.object(runner, "_port_open", return_value=False), \
         patch.object(runner, "_start_colyseus_server", return_value=MagicMock(terminate=lambda: None, wait=lambda timeout=10: None)), \
         patch.object(runner.subprocess, "run", side_effect=_fake_godot_run), \
         patch.object(runner, "_git", return_value="0123456789abcdef"), \
         patch.object(runner, "_scoped_source_integrity", return_value=_SourceIntegrity(ok=True, scoped_clean=True, global_dirty=False, scoped_contamination=[])), \
         patch.object(runner, "_parse_godot_gpu_profiles", return_value=([], [], [])):
        result = runner.run_variant(
            "default",
            [],
            headless=True,
            sample_frames=1,
            warmup_frames=1,
            keep_forever=True,
        )
    assert result["ok"] is True
    assert result["run_budget"]["keep_forever"] is True
    assert result["run_budget"]["reason"] == "explicit_keep_forever_override"


def test_run_variant_rejects_shader_failure_despite_success_receipt(tmp_path: Path) -> None:
    allowed = BudgetDecision(
        allowed=True, surface="repo", surface_path=str(tmp_path),
        usage_bytes=1, projected_bytes=1, soft_bytes=8, hard_bytes=12,
        keep_forever=True, reason="explicit_keep_forever_override",
    )

    def fake_run(*_args, **kwargs):
        (tmp_path / "default.json").write_text(json.dumps(valid_result()))
        kwargs["stdout"].write(
            "ERROR: ASCIICompositorEffect: compute shader compile error "
            "(res://render/product/cell_owner_product.glsl):\n"
        )
        kwargs["stdout"].flush()
        return types.SimpleNamespace(returncode=0)

    with patch.object(runner, "check_run_budget", return_value=allowed), \
         patch.object(runner, "ROOT", tmp_path), \
         patch.object(runner, "RUN_DIR", tmp_path), \
         patch.object(runner, "_port_open", return_value=False), \
         patch.object(runner, "_start_colyseus_server", return_value=MagicMock()), \
         patch.object(runner.subprocess, "run", side_effect=fake_run), \
         patch.object(runner, "_git", return_value="0123456789abcdef"), \
         patch.object(runner, "_scoped_source_integrity", return_value=_SourceIntegrity(
             ok=True, scoped_clean=True, global_dirty=False, scoped_contamination=[])):
        result = runner.run_variant("default", [], headless=True,
                                    sample_frames=1, warmup_frames=1, keep_forever=True)
    assert result["returncode"] == 0
    assert result["ok"] is False
    assert result["reason"] == "renderer_failed"
    assert "cell_owner_product.glsl" in result["render_failures"][0]


def test_main_writes_summary_when_budget_refusal_skips_run_dir_creation(tmp_path: Path) -> None:
    blocked = BudgetDecision(
        allowed=False,
        surface="repo",
        surface_path=str(tmp_path / ".run"),
        usage_bytes=13,
        projected_bytes=13,
        soft_bytes=8,
        hard_bytes=12,
        keep_forever=False,
        reason="hard_budget_exceeded",
    )
    run_dir = tmp_path / ".run" / "godot_camera_turn_perf"
    args = types.SimpleNamespace(
        allow_inherited_env=True,
        day_cycle_time=0.5,
        ab_brackets=0,
        ab_control_env="",
        headless=True,
        expect_presentation_size=None,
        expect_target_size=None,
        expect_ansi_size=None,
        variants="default",
        disable_eye_reuse_control=False,
        sample_frames=1,
        warmup_frames=1,
        perf_trace=False,
        godot_gpu_profile=False,
        uncapped=False,
        projection_modes="",
        expect_camera_origin=None,
        stationary=False,
        stage_timing_gate=False,
        keep_forever=False,
    )
    with patch.object(runner, "parse_args", return_value=args), \
         patch.object(runner, "ROOT", tmp_path), \
         patch.object(runner, "RUN_DIR", run_dir), \
         patch.object(runner, "check_run_budget", return_value=blocked):
        rc = runner.main()
    assert rc == 1
    summary_path = run_dir / "summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["ok"] is False
    assert summary["results"][0]["reason"] == "run_budget_exceeded"


# ---------------------------------------------------------------------------
# FL4431-CLEAN-GATE-20260717-10: AB clean-tree eligibility (pytest-collectable).
# Each ABAB arm reloads renderer source from disk, so a dirty tree lets arms
# execute different code — the contamination that inflated cf0000b00. These
# tests pin the single eligibility owner: dirty refusal, clean admission, zero
# Godot launches on refusal, and independence from the inherited-gate scrub.
# ---------------------------------------------------------------------------

def _ab_args(brackets: int = 3) -> types.SimpleNamespace:
    """A minimal argparse-shaped namespace carrying exactly the fields
    run_ab_brackets reads. headless=True skips the real 8s inter-arm sleeps."""
    return types.SimpleNamespace(
        ab_brackets=brackets,
        ab_control_env="FL4431_DISABLE_EYE_GRID_DEDUP=1",
        ab_shared_env="",
        runtime_source_ref="origin/main",
        allow_stable_dirty_runtime=False,
        expect_camera_origin=(78.0999984741211, 17.9365615844727, 144.399993896484),
        stage_timing_gate=False,
        headless=True,
        sample_frames=240,
        warmup_frames=90,
        perf_trace=False,
        godot_gpu_profile=False,
        projection_modes="perspective",
        day_cycle_time=0.5,
        stationary=True,
        expect_presentation_size=(3600, 2252),
        expect_target_size=(3600, 2260),
        expect_ansi_size=(180, 113),
    )


def test_godot_gpu_profile_flag_is_an_engine_argument():
    assert _godot_gpu_profile_engine_args(False) == []
    assert _godot_gpu_profile_engine_args(True) == ["--gpu-profile"]


def test_godot_gpu_profile_parser_keeps_each_native_metal_block():
    profiles, errors, header_totals = _parse_godot_gpu_profiles(
        "Metal 3.2 - Forward+ - Using Device #0: Apple - Apple M3 Pro (Apple9)\n"
        "GPU PROFILE (total 11.25ms): \n"
        "\t-Render 3D: 4.5ms\n"
        "\t-Post-Transparent: 2.25ms\n"
        "unrelated line\n"
        "GPU PROFILE (total 9.5ms): \n"
        "\t-Render 3D: 3.75ms\n"
    )
    assert errors == []
    assert header_totals == [11.25, 9.5]
    assert [row["total_gpu_ms"] for row in profiles] == [11.25, 9.5]
    assert profiles[0]["tasks"] == {
        "Render 3D": 4.5,
        "Post-Transparent": 2.25,
    }


def test_godot_gpu_profile_parser_fails_closed_on_empty_and_duplicate_blocks():
    profiles, errors, header_totals = _parse_godot_gpu_profiles(
        "GPU PROFILE (total 4.0ms): \n"
        "not a task\n"
        "GPU PROFILE (total 5.0ms): \n"
        "\t-Render 3D: 2.0ms\n"
        "\t-Render 3D: 1.0ms\n"
    )
    assert len(profiles) == 1
    assert header_totals == [4.0, 5.0]
    assert any("gpu_profile_block_has_no_tasks" in error for error in errors)
    assert any("duplicate_gpu_profile_task" in error for error in errors)


def test_godot_gpu_profile_parser_preserves_native_metal_zero_header_falsifier():
    profiles, errors, header_totals = _parse_godot_gpu_profiles(
        "GPU PROFILE (total 0.0ms): \n"
        "PERF_FRAME_TRACE frames=30 internal=3600x2252 avg_wall_ms=0.9\n"
        "GPU PROFILE (total 0.0ms): \n"
    )
    assert profiles == []
    assert header_totals == [0.0, 0.0]
    assert len(errors) == 2


# --- scoped source-integrity gate (FL4431-SCOPED-SOURCE-GATE-20260717-01) ----

def _clean_integ(global_dirty=False):
    return _SourceIntegrity(
        ok=True, scoped_clean=True, global_dirty=global_dirty, scoped_contamination=[]
    )


def _scoped_dirty_integ(paths):
    return _SourceIntegrity(
        ok=True, scoped_clean=False, global_dirty=False, scoped_contamination=list(paths)
    )


def _git_fail_integ():
    return _SourceIntegrity(
        ok=False, scoped_clean=False, global_dirty=False, scoped_contamination=[]
    )


def _dimension_checked_result(mismatch_key: str = "") -> dict:
    result = valid_result()
    if mismatch_key:
        result["mode_results"][0]["samples"][0]["pixel_camera_contract"][mismatch_key] = [1280, 800]
    _apply_expected_dimension_contract(
        result,
        expected_presentation_size=(3600, 2252),
        expected_target_size=(3600, 2260),
        expected_ansi_size=(180, 113),
    )
    return result


def _ab_gate_patches(tmp_path):
    return (
        patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()),
        patch.object(runner, "_current_head", return_value="H0"),
        patch.object(runner, "_commit_at_ref", return_value="H0"),
        patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"),
        patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"),
        patch.object(
            runner,
            "_matrix_source_gate",
            return_value=runner._MatrixGate(True, "", "H0", "FP0"),
        ),
        patch.object(runner, "RUN_DIR", tmp_path),
        patch.object(runner, "ROOT", tmp_path),
        patch.object(runner.time, "sleep", lambda *args, **kwargs: None),
    )


def test_dimension_contract_exact_pass_and_records_observed_values():
    result = _dimension_checked_result()
    assert result["ok"] is True
    assert result["dimension_valid"] is True
    assert result["dimension_sample_contract_count"] == 1
    assert result["expected_dimensions"]["presentation_size"] == [3600, 2252]
    assert result["observed_dimensions"]["target_size"] == [[3600, 2260]]
    assert _parse_size("180x113") == (180, 113)


def test_dimension_contract_each_size_mismatch_fails_closed():
    for key in ("presentation_size", "target_size", "semantic_ansi_size"):
        result = _dimension_checked_result(key)
        assert result["ok"] is False
        assert result["dimension_valid"] is False
        assert key in result["reason"]
        assert _arm_launch_environment_failure(result) is True


def test_headed_ab_refuses_absent_expected_sizes():
    args = _ab_args(brackets=1)
    args.headless = False
    args.expect_presentation_size = None
    args.expect_target_size = None
    args.expect_ansi_size = None
    launch = MagicMock()
    with patch.object(runner, "run_variant", launch):
        assert run_ab_brackets(args) == 2
    assert launch.call_count == 0


def test_dimension_mismatch_relaunches_once_then_accepts(tmp_path):
    bad = _dimension_checked_result("presentation_size")
    launch = MagicMock(side_effect=[bad, _dimension_checked_result(), _dimension_checked_result()])
    patches = _ab_gate_patches(tmp_path)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=1))
    assert rc == 0
    assert launch.call_count == 3
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["dimension_valid"] is True
    assert summary["expected_dimensions"]["semantic_ansi_size"] == [180, 113]


def test_persistent_dimension_mismatch_relaunches_once_and_stays_invalid(tmp_path):
    launch = MagicMock(side_effect=lambda *args, **kwargs: _dimension_checked_result("target_size"))
    patches = _ab_gate_patches(tmp_path)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=1))
    assert rc == 1
    assert launch.call_count == 4
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["dimension_valid"] is False
    assert summary["ok"] is False


def test_is_scoped_source_recognizes_exactly_the_runtime_roots():
    assert _is_scoped_source("godot_project/render/product/cell_owner_product.glsl")
    assert _is_scoped_source("colyseus_server/src/index.ts")
    assert _is_scoped_source("scripts/run_godot_camera_turn_perf.py")
    # Unrelated lanes are NOT scoped source.
    assert not _is_scoped_source("docs/FAILURE_LOG.md")
    assert not _is_scoped_source("scripts/some_other_tool.py")
    assert not _is_scoped_source("assets/x.json")
    assert not _is_scoped_source("README.md")


def test_scoped_integrity_classifies_scoped_vs_unrelated():
    # The Y column owns worktree equality. X-only rows are expected when an
    # isolated index pins the selected commit while local HEAD belongs to a foreign
    # lane. The tracked global result stays informational.
    scoped_porcelain = (
        " M godot_project/render/product/cell_owner_product.glsl\n"
        "A  scripts/run_godot_camera_turn_perf.py\n"
        " M colyseus_server/src/index.ts\n"
        "R  godot_project/a.gd -> godot_project/b.gd\n"
    )
    scoped_out = types.SimpleNamespace(returncode=0, stdout=scoped_porcelain)
    global_out = types.SimpleNamespace(returncode=0, stdout=" M docs/note.md\n")
    with patch.object(runner.subprocess, "run", side_effect=[scoped_out, global_out]) as run:
        state = _scoped_source_integrity()
    assert state.ok is True
    assert state.scoped_clean is False
    assert state.global_dirty is True           # docs/note.md admitted-but-flagged
    # First line not corrupted, and only scoped paths in contamination.
    assert state.scoped_contamination[0] == "godot_project/render/product/cell_owner_product.glsl"
    assert "colyseus_server/src/index.ts" in state.scoped_contamination
    assert "scripts/run_godot_camera_turn_perf.py" not in state.scoped_contamination
    assert "godot_project/a.gd -> godot_project/b.gd" not in state.scoped_contamination
    assert not any("docs/note.md" in p for p in state.scoped_contamination)
    first_cmd = run.call_args_list[0].args[0]
    assert "--untracked-files=all" in first_cmd
    assert "godot_project/" in first_cmd
    assert "scripts/run_godot_camera_turn_perf.py" in first_cmd
    assert run.call_args_list[1].args[0][-1] == "--untracked-files=no"


def test_scoped_integrity_admits_isolated_index_x_column_differences():
    scoped_out = types.SimpleNamespace(
        returncode=0,
        stdout=(
            "M  godot_project/scripts/World.gd\n"
            "M  scripts/run_godot_camera_turn_perf.py\n"
            "R  colyseus_server/old.ts -> colyseus_server/new.ts\n"
        ),
    )
    global_out = types.SimpleNamespace(returncode=0, stdout="M  godot_project/scripts/World.gd\n")
    with patch.object(runner.subprocess, "run", side_effect=[scoped_out, global_out]):
        state = _scoped_source_integrity()
    assert state.ok is True
    assert state.scoped_clean is True
    assert state.global_dirty is True
    assert state.scoped_contamination == []


def test_scoped_integrity_admits_unrelated_only_dirt():
    scoped_out = types.SimpleNamespace(returncode=0, stdout="")
    global_out = types.SimpleNamespace(
        returncode=0,
        stdout=" M docs/FAILURE_LOG.md\nA  assets/x.json\n",
    )
    with patch.object(runner.subprocess, "run", side_effect=[scoped_out, global_out]):
        state = _scoped_source_integrity()
    assert state.ok is True
    assert state.scoped_clean is True     # nothing scoped is dirty
    assert state.global_dirty is True     # but unrelated dirt exists
    assert state.scoped_contamination == []


def test_scoped_integrity_detects_untracked_when_tracked_tree_is_clean():
    clean = types.SimpleNamespace(returncode=0, stdout="")
    untracked = types.SimpleNamespace(returncode=0, stdout="scratch.tmp\n")
    with patch.object(runner.subprocess, "run", side_effect=[clean, clean, untracked]):
        state = _scoped_source_integrity()
    assert state.ok is True
    assert state.scoped_clean is True
    assert state.global_dirty is True


def test_scoped_integrity_global_probe_failure_is_informational():
    clean = types.SimpleNamespace(returncode=0, stdout="")
    with patch.object(
        runner.subprocess,
        "run",
        side_effect=[clean, runner.subprocess.TimeoutExpired(cmd=["git"], timeout=10)],
    ):
        state = _scoped_source_integrity()
    assert state.ok is True
    assert state.scoped_clean is True
    assert state.global_dirty is True


def test_run_variant_git_dirty_uses_scoped_integrity_owner():
    source = (Path(__file__).resolve().parent / "run_godot_camera_turn_perf.py").read_text(
        encoding="utf-8"
    )
    assert '_git(["status", "--porcelain"])' not in source
    assert "receipt_integrity = _scoped_source_integrity()" in source
    assert "or receipt_integrity.global_dirty" in source


def test_bounded_native_hud_runtime_owner_deleted():
    root = Path(__file__).resolve().parents[1]
    runtime_paths = (
        root / "scripts" / "run_godot_camera_turn_perf.py",
        root / "godot_project" / "tools" / "profiling" / "run_camera_turn_perf.gd",
    )
    banned = (
        "metal" + "_hud",
        "METAL" + "_HUD",
        "MTL" + "_HUD",
        "metal" + "-HUD",
        "--metal" + "-hud",
    )
    for path in runtime_paths:
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source
    assert not (root / "scripts" / ("fl4431_" + "metal_hud_receipt.py")).exists()
    assert not (root / "scripts" / ("test_fl4431_" + "metal_hud_receipt.py")).exists()


def test_scoped_integrity_git_failures_are_not_clean():
    for effect in (
        OSError("git gone"),
        runner.subprocess.TimeoutExpired(cmd=["git"], timeout=10),
    ):
        with patch.object(runner.subprocess, "run", side_effect=effect):
            state = _scoped_source_integrity()
        assert state.ok is False and state.scoped_clean is False
    nonzero = types.SimpleNamespace(returncode=128, stdout="")
    with patch.object(runner.subprocess, "run", return_value=nonzero):
        state = _scoped_source_integrity()
    assert state.ok is False and state.scoped_clean is False


def test_scoped_dirt_refuses_and_launches_zero_arms():
    launch = MagicMock()
    for root_path in (
        "godot_project/render/product/cell_owner_product.glsl",
        "colyseus_server/src/index.ts",
        "scripts/run_godot_camera_turn_perf.py",
    ):
        with patch.object(runner, "_scoped_source_integrity",
                          return_value=_scoped_dirty_integ([root_path])), \
                patch.object(runner, "run_variant", launch):
            rc = run_ab_brackets(_ab_args(brackets=3))
        assert rc == 2, f"scoped dirt under {root_path} must refuse"
    assert launch.call_count == 0     # zero arms across every scoped root


def test_git_failure_refuses_and_launches_zero_arms():
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_git_fail_integ()), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_unresolved_head_refuses_and_launches_zero_arms():
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value=None), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_head_divergence_admitted_when_committed_runtime_fingerprints_match(tmp_path):
    """Shared-branch divergence is metadata when committed bytes match."""
    launch = MagicMock(side_effect=lambda *args, **kwargs: valid_result())
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H9"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *args, **kwargs: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 0
    assert launch.call_count == 6
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["baseline_head"] == "H0"
    assert summary["runtime_source_ref"] == "origin/main"
    assert summary["pinned_runtime_source_commit"] == "H9"
    assert summary["runtime_source_ref_kind"] == "remote_tracking"
    assert summary["committed_runtime_source_sha256"] == "FP0"


def test_local_committed_runtime_ref_is_admitted_and_recorded(tmp_path):
    launch = MagicMock(side_effect=lambda *args, **kwargs: valid_result())
    args = _ab_args(brackets=1)
    args.runtime_source_ref = "fl4431-local-checkpoint"
    commit = "a" * 40
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value=commit), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *args, **kwargs: None):
        rc = run_ab_brackets(args)
    assert rc == 0
    assert launch.call_count == 2
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["runtime_source_ref"] == "fl4431-local-checkpoint"
    assert summary["pinned_runtime_source_commit"] == commit
    assert summary["runtime_source_ref_kind"] == "local_committed"


def test_stable_dirty_runtime_is_admitted_and_byte_pinned(tmp_path):
    launch = MagicMock(side_effect=lambda *args, **kwargs: valid_result())
    args = _ab_args(brackets=1)
    args.allow_stable_dirty_runtime = True
    dirty = _scoped_dirty_integ(["godot_project/scenes/PlayableMap.tscn"])
    with patch.object(runner, "_scoped_source_integrity", return_value=dirty), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_runtime_worktree_fingerprint", return_value="FPW"), \
            patch.object(runner, "_commit_at_ref") as commit_ref, \
            patch.object(runner, "_runtime_source_fingerprint") as index_fp, \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *args, **kwargs: None):
        rc = run_ab_brackets(args)
    assert rc == 0
    assert launch.call_count == 2
    commit_ref.assert_not_called()
    index_fp.assert_not_called()
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["runtime_source_ref"] == ""
    assert summary["pinned_runtime_source_commit"] is None
    assert summary["runtime_source_ref_kind"] == "stable_worktree"
    assert summary["committed_runtime_source_sha256"] is None
    assert summary["baseline_runtime_source_sha256"] == "FPW"
    assert summary["stable_dirty_runtime"] is True
    assert summary["scoped_clean"] is False


def test_stable_dirty_runtime_fingerprint_change_aborts(tmp_path):
    launch = MagicMock(side_effect=lambda *args, **kwargs: valid_result())
    args = _ab_args(brackets=2)
    args.allow_stable_dirty_runtime = True
    dirty = _scoped_dirty_integ(["godot_project/scenes/PlayableMap.tscn"])
    fingerprints = ["FPW", "FPW", "CHANGED"]
    with patch.object(runner, "_scoped_source_integrity", return_value=dirty), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(
                runner,
                "_runtime_worktree_fingerprint",
                side_effect=fingerprints,
            ), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *args, **kwargs: None):
        rc = run_ab_brackets(args)
    assert rc == 2
    assert launch.call_count == 1
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["aborted"] is True
    assert "runtime_source_changed" in summary["abort_reason"]


def test_commit_at_ref_requires_a_commit_sha():
    completed = types.SimpleNamespace(returncode=0, stdout=("b" * 40) + "\n")
    with patch.object(runner.subprocess, "run", return_value=completed) as run:
        assert _commit_at_ref("local-tag") == "b" * 40
    assert run.call_args.args[0][0:3] == ["git", "rev-parse", "--verify"]
    assert run.call_args.args[0][3] == "local-tag^{commit}"
    assert _commit_at_ref("") is None


def test_missing_committed_ref_refuses_and_launches_zero_arms():
    """A missing selected ref cannot establish committed runtime provenance."""
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value=None), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_fingerprint_failure_at_start_refuses_and_launches_zero_arms():
    """A runtime-source fingerprint failure at matrix start refuses."""
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value=None), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_committed_fingerprint_failure_at_start_refuses_and_launches_zero_arms():
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H9"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value=None), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_active_index_runtime_mismatch_refuses_and_launches_zero_arms():
    launch = MagicMock()
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H9"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP9"), \
            patch.object(runner, "run_variant", launch):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert rc == 2
    assert launch.call_count == 0


def test_unrelated_dirt_admitted_all_arms_launch(tmp_path):
    """Scoped source clean plus matching committed bytes admits unrelated dirt."""
    launch = MagicMock(side_effect=lambda *a, **k: valid_result())
    with patch.object(runner, "_scoped_source_integrity",
                      return_value=_clean_integ(global_dirty=True)), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *a, **k: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert launch.call_count == 6      # 3 brackets * 2 arms, none refused
    assert rc == 0
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["baseline_head"] == "H0"
    assert summary["global_dirty"] is True
    assert summary["scoped_clean"] is True
    assert summary["aborted"] is False
    assert summary["runtime_source_stable"] is True
    assert summary["head_drift_observed"] is False
    assert summary["baseline_runtime_source_sha256"] == "FP0"


def test_unrelated_head_drift_admitted_when_fingerprint_stable(tmp_path):
    """FL4431-SCOPED-FINGERPRINT: an unrelated HEAD move mid-matrix is ADMITTED
    (not aborted) as long as the runtime-source fingerprint holds. All arms run;
    head_drift_observed is recorded True and runtime_source_stable stays True."""
    launch = MagicMock(side_effect=lambda *a, **k: valid_result())
    # HEAD drifts after arm1 but the fingerprint never changes.
    head_seq = ["H0", "H0", "H0", "H1", "H1", "H1", "H1", "H1", "H1", "H1",
                "H1", "H1", "H1", "H1", "H1"]
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", side_effect=head_seq), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *a, **k: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert launch.call_count == 6      # all arms ran despite HEAD drift
    assert rc == 0
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["aborted"] is False
    assert summary["head_drift_observed"] is True
    assert summary["runtime_source_stable"] is True
    assert summary["baseline_head"] == "H0"
    assert summary["final_head"] == "H1"


def test_runtime_fingerprint_change_between_arms_aborts(tmp_path):
    """A CHANGED runtime fingerprint mid-matrix aborts before the next arm
    (exit 2); no further launches."""
    launch = MagicMock(side_effect=lambda *a, **k: valid_result())
    # fingerprint calls: 1=baseline, 2=arm1 pre, 3=arm1 post, 4=arm2 pre (changed).
    fp_seq = ["FP0", "FP0", "FP0", "FPX", "FPX"]
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", side_effect=fp_seq), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *a, **k: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert launch.call_count == 1      # only arm1 ran; zero further launches
    assert rc == 2
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["aborted"] is True
    assert "runtime_source_changed" in summary["abort_reason"]
    assert summary["runtime_source_stable"] is False
    assert summary["final_runtime_source_sha256"] == "FPX"


def test_head_read_failure_between_arms_aborts(tmp_path):
    """HEAD movement is informational, but losing provenance entirely is a
    git failure and must abort after the current arm with zero further launches."""
    launch = MagicMock(side_effect=lambda *a, **k: valid_result())
    with patch.object(runner, "_scoped_source_integrity", return_value=_clean_integ()), \
            patch.object(runner, "_current_head", side_effect=["H0", "H0", None]), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *a, **k: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert launch.call_count == 1
    assert rc == 2
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["aborted"] is True
    assert "git_failure_head" in summary["abort_reason"]
    assert summary["runtime_source_stable"] is False


def test_runtime_source_fingerprint_deterministic_and_fails_soft():
    """Index and committed-tree records normalize to the same runtime hash."""
    ls = "100644 aaa 0\tgodot_project/x.gd\n100644 bbb 0\tcolyseus_server/y.ts\n"
    out = types.SimpleNamespace(returncode=0, stdout=ls)
    with patch.object(runner.subprocess, "run", return_value=out):
        a = _runtime_source_fingerprint()
        b = _runtime_source_fingerprint()
    assert a is not None and a == b            # deterministic
    tree = types.SimpleNamespace(
        returncode=0,
        stdout=(
            "100644 blob aaa\tgodot_project/x.gd\n"
            "100644 blob bbb\tcolyseus_server/y.ts\n"
        ),
    )
    with patch.object(runner.subprocess, "run", return_value=tree) as run:
        assert _runtime_source_fingerprint_at_ref("H9") == a
    assert run.call_args.args[0][:4] == ["git", "ls-tree", "-r", "H9"]
    # Line order must not matter (sorted internally).
    out2 = types.SimpleNamespace(
        returncode=0,
        stdout="100644 bbb 0\tcolyseus_server/y.ts\n100644 aaa 0\tgodot_project/x.gd\n",
    )
    with patch.object(runner.subprocess, "run", return_value=out2):
        assert _runtime_source_fingerprint() == a
    # A content change flips the hash.
    out3 = types.SimpleNamespace(returncode=0, stdout=ls.replace("aaa", "ccc"))
    with patch.object(runner.subprocess, "run", return_value=out3):
        assert _runtime_source_fingerprint() != a
    # git failure / empty -> None (fingerprint failure).
    for eff in (OSError("x"), runner.subprocess.TimeoutExpired(cmd=["git"], timeout=15)):
        with patch.object(runner.subprocess, "run", side_effect=eff):
            assert _runtime_source_fingerprint() is None
    with patch.object(runner.subprocess, "run",
                      return_value=types.SimpleNamespace(returncode=0, stdout="")):
        assert _runtime_source_fingerprint() is None
    conflicted = types.SimpleNamespace(
        returncode=0,
        stdout="100644 aaa 2\tgodot_project/x.gd\n",
    )
    with patch.object(runner.subprocess, "run", return_value=conflicted):
        assert _runtime_source_fingerprint() is None


def test_scoped_dirt_between_arms_aborts_zero_further_launches(tmp_path):
    """Scoped source going dirty mid-matrix aborts immediately (exit 2)."""
    launch = MagicMock(side_effect=lambda *a, **k: valid_result())
    # _scoped_source_integrity calls: 1=start, 2=arm1 pre, 3=arm1 post,
    # 4=arm2 pre (now dirty).
    integ_seq = [
        _clean_integ(), _clean_integ(), _clean_integ(),
        _scoped_dirty_integ(["godot_project/shaders/x.glsl"]),
        _scoped_dirty_integ(["godot_project/shaders/x.glsl"]),
    ]
    with patch.object(runner, "_scoped_source_integrity", side_effect=integ_seq), \
            patch.object(runner, "_current_head", return_value="H0"), \
            patch.object(runner, "_commit_at_ref", return_value="H0"), \
            patch.object(runner, "_runtime_source_fingerprint", return_value="FP0"), \
            patch.object(runner, "_runtime_source_fingerprint_at_ref", return_value="FP0"), \
            patch.object(runner, "run_variant", launch), \
            patch.object(runner, "RUN_DIR", tmp_path), \
            patch.object(runner, "ROOT", tmp_path), \
            patch.object(runner.time, "sleep", lambda *a, **k: None):
        rc = run_ab_brackets(_ab_args(brackets=3))
    assert launch.call_count == 1      # only arm1 ran; zero further launches
    assert rc == 2                     # mid-matrix abort is a contamination exit
    summary = json.loads((tmp_path / "ab_summary.json").read_text())
    assert summary["aborted"] is True
    assert "scoped_drift" in summary["abort_reason"]
    assert "scoped_drift" in summary["abort_reason"]


def test_inherited_gate_scrub_unaffected_by_clean_gate():
    """No regression: the per-arm inherited-gate scrub stays independent of the
    new clean-tree owner."""
    with patch.dict(
        os.environ,
        {"FL4431_STALE": "1", "FL4408_STALE": "1", "KEEP_ME": "value"},
        clear=True,
    ):
        assert _inherited_gates() == {"FL4431_STALE": "1", "FL4408_STALE": "1"}
        arm_env = _arm_local_env({"FL4431_DISABLE_EYE_GRID_DEDUP": "1"})
    assert "FL4431_STALE" not in arm_env
    assert "FL4408_STALE" not in arm_env
    assert arm_env["KEEP_ME"] == "value"
    assert arm_env["FL4431_DISABLE_EYE_GRID_DEDUP"] == "1"


# ---------------------------------------------------------------------------
# FL4431-DISTRIBUTION-*-20260717: distribution-metrics propagation + fail-shut.
# summarize() must propagate median/p95/p1_fps/hitch/sample_count and refuse any
# arm whose distribution data is absent, nonnumeric, nonfinite, zero-sample, or
# whose raw frame_deltas_ms is missing/empty/malformed/length-mismatched.
# ---------------------------------------------------------------------------

def _mode(result: dict) -> dict:
    return result["mode_results"][0]


def test_distribution_fields_propagated_when_valid():
    summary = summarize([valid_result()])
    assert summary["ok"] is True
    row = summary["results"][0]
    assert row["ok"] is True
    m = row["mode_results"][0]
    assert m["median_delta_ms"] == 34.0
    assert m["p95_delta_ms"] == 42.0
    assert m["p1_fps"] == 22.7
    assert m["hitch_count_33_333ms"] == 2.0
    assert m["hitch_rate"] == 0.5
    assert m["sample_count"] == 4
    # One-mode receipt mirrors the distribution fields to the row top level.
    assert row["median_delta_ms"] == 34.0
    assert row["p95_delta_ms"] == 42.0
    assert row["sample_count"] == 4


def test_distribution_fail_shut_absent_scalar():
    for field in ("median_delta_ms", "p95_delta_ms", "p1_fps",
                  "hitch_count_33_333ms", "hitch_rate"):
        r = valid_result()
        del _mode(r)[field]
        assert summarize([r])["ok"] is False, f"missing {field} must fail shut"


def test_distribution_fail_shut_nonnumeric_and_nonfinite():
    for bad in ("x", None, float("inf"), float("-inf"), float("nan"), True):
        r = valid_result()
        _mode(r)["p95_delta_ms"] = bad
        assert summarize([r])["ok"] is False, f"p95={bad!r} must fail shut"


def test_distribution_fail_shut_zero_or_bad_sample_count():
    for bad in (0, -4, 4.0, "4", None, True):
        r = valid_result()
        _mode(r)["sample_count"] = bad
        assert summarize([r])["ok"] is False, f"sample_count={bad!r} must fail shut"


def test_distribution_fail_shut_frame_deltas_integrity():
    # missing
    r = valid_result(); del _mode(r)["frame_deltas_ms"]
    assert summarize([r])["ok"] is False
    # empty
    r = valid_result(); _mode(r)["frame_deltas_ms"] = []
    assert summarize([r])["ok"] is False
    # length mismatch vs sample_count (4)
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, 34.0, 40.0]
    assert summarize([r])["ok"] is False
    # non-positive element
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, 0.0, 40.0, 44.0]
    assert summarize([r])["ok"] is False
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, -1.0, 40.0, 44.0]
    assert summarize([r])["ok"] is False
    # nonfinite element
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, float("nan"), 40.0, 44.0]
    assert summarize([r])["ok"] is False
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, float("inf"), 40.0, 44.0]
    assert summarize([r])["ok"] is False
    # nonnumeric element
    r = valid_result(); _mode(r)["frame_deltas_ms"] = [28.0, "x", 40.0, 44.0]
    assert summarize([r])["ok"] is False
    # not a list
    r = valid_result(); _mode(r)["frame_deltas_ms"] = "28,34,40,44"
    assert summarize([r])["ok"] is False


def test_distribution_fail_shut_nonpositive_percentiles():
    # median/p95/p1_fps must be STRICTLY positive (a zero or negative frame-time
    # percentile or FPS is impossible).
    for field in ("median_delta_ms", "p95_delta_ms", "p1_fps"):
        for bad in (0.0, -1.0):
            r = valid_result()
            _mode(r)[field] = bad
            assert summarize([r])["ok"] is False, f"{field}={bad} must fail shut"


def test_distribution_fail_shut_hitch_count_bounds_and_type():
    # non-int (float), negative, and > sample_count all fail shut.
    for bad in (2.0, -1, 5, "2", None):
        r = valid_result()
        _mode(r)["hitch_count_33_333ms"] = bad   # sample_count is 4
        assert summarize([r])["ok"] is False, f"hitch_count={bad!r} must fail shut"
    # A valid int within [0, sample_count] is preserved as an int in the row.
    r = valid_result()
    _mode(r)["hitch_count_33_333ms"] = 4          # == sample_count boundary ok
    row = summarize([r])["results"][0]
    assert row["ok"] is True
    assert row["mode_results"][0]["hitch_count_33_333ms"] == 4
    assert isinstance(row["mode_results"][0]["hitch_count_33_333ms"], int)


def test_distribution_fail_shut_hitch_rate_range():
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        r = valid_result()
        _mode(r)["hitch_rate"] = bad
        assert summarize([r])["ok"] is False, f"hitch_rate={bad!r} must fail shut"
    # 0.0 and 1.0 are the inclusive bounds — both valid.
    for good in (0.0, 1.0):
        r = valid_result()
        _mode(r)["hitch_rate"] = good
        assert summarize([r])["results"][0]["ok"] is True


def test_producer_starvation_profiler_deleted():
    deleted_starvation_tokens = (
        "PROFILE_SKIP_PASSES",
        "--profile-skip-passes",
        "profile_skip_passes",
        "FL4431_PROFILE_SKIP",
        "FL4431_PROFILE_QUARTER_TERRAIN",
        "_fl4431_skip",
        "capture_terrain_draw",
    )
    deleted_starvation_sources = (
        Path("scripts/run_godot_camera_turn_perf.py").read_text(),
        Path("scripts/run_fl4431_perf_visual_runner.py").read_text(),
        Path("godot_project/render/api/ASCIICompositorEffect.gd").read_text(),
    )
    for token in deleted_starvation_tokens:
        assert all(token not in source for source in deleted_starvation_sources)


def test_direct_variant_forwards_expected_dimension_contract():
    source = Path("scripts/run_godot_camera_turn_perf.py").read_text(encoding="utf-8")
    direct_loop = source.split("for label, variant_args, arm_env in selected:", 1)[1]
    direct_call = direct_loop.split("results.append(result)", 1)[0]
    assert "expected_presentation_size=args.expect_presentation_size" in direct_call
    assert "expected_target_size=args.expect_target_size" in direct_call
    assert "expected_ansi_size=args.expect_ansi_size" in direct_call
    assert "stage_timing_gate=args.stage_timing_gate" in direct_call
    assert "--fl4359-force-fullscreen" not in source


def _stage_timing_block() -> dict:
    stage_ms = {
        "surface_facts_compile_upload_cpu_ms": 0.0,
            "frame_setup_cpu_ms": 0.15,
        "dynamic_mover_publish_cpu_ms": 0.20,
        "vsb_view_build_cpu_ms": 0.30,
        "product_prepare_cpu_ms": 0.40,
        "vsb_product_submit_cpu_ms": 0.10,
        "final_paint_submit_cpu_ms": 0.80,
    }
    return {
        "samples": [{
            "schema": "fl4691.stage_timing.v1",
            "frame_index": 42,
            "total_callback_cpu_ms": 2.1,
            "stage_ms": dict(stage_ms),
            "dominant_stage": "final_paint_submit_cpu_ms",
            "dominant_stage_ms": 0.80,
            "dynamic_mover_publication": {
                "schema": "fl4691.dynamic_mover_publication_timing.v1",
                "frame_index": 42,
                "total_ms": 0.20,
                "build_ms": 0.14,
                "validate_ms": 0.02,
                "publish_ms": 0.04,
                "published": True,
                "valid": True,
                "descriptor_count": 39,
                "face_count": 582,
            },
        }],
        "summary": {
            "valid": True,
            "errors": [],
            "sample_count": 1,
            "avg_stage_ms": dict(stage_ms),
            "max_stage_ms": dict(stage_ms),
            "dominant_stage": "final_paint_submit_cpu_ms",
            "dominant_stage_avg_ms": 0.80,
        },
    }


def test_stage_timing_gate_names_dominant_stage() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    timing = _stage_timing_block()
    mode["stage_timing_gate"] = True
    mode["stage_timing_valid"] = True
    mode["stage_timing_samples"] = timing["samples"]
    mode["stage_timing_summary"] = timing["summary"]

    summary = summarize([result])
    row = summary["results"][0]
    assert summary["ok"] is True
    assert row["stage_timing_gate"] is True
    assert row["stage_timing_valid"] is True
    assert row["dominant_stage"] == "final_paint_submit_cpu_ms"
    assert row["dominant_stage_avg_ms"] == 0.8
    matrix = row["suspected_owner_matrix"]
    assert matrix["schema"] == "fl4691.suspected_owner_matrix.v2"
    assert matrix["valid"] is True
    assert matrix["measurement_scope"] == "cpu_callback_stage"
    assert matrix["attribution_scope"] == "stage_bucket_not_regression_owner"
    assert matrix["diagnostic_only"] is True
    assert matrix["gpu_execution_measured"] is False
    owners = matrix["owners"]
    assert [entry["owner"] for entry in owners] == [
        "final_painter_submit",
        "cell_owner_product_prepare",
        "visible_surface_builder_view_build",
        "dynamic_mover_publication",
        "frame_setup_residual",
        "vsb_product_dispatch_submit",
        "surface_facts_compile_upload",
    ]
    assert [entry["rank"] for entry in owners] == [1, 2, 3, 4, 5, 6, 7]
    assert len({entry["stage"] for entry in owners}) == len(owners)
    assert matrix["dominant_measured_cpu_stage"] == "final_painter_submit"
    assert matrix["unmeasured_original_suspects"] == [
        "actor_foliage_product_gpu_work"
    ]
    assert matrix["unmeasured_hypotheses"] == [{
        "hypothesis": "actor_foliage_product_gpu_work",
        "source": "cell_owner_product.glsl actor cells plus foliage leaf bank",
        "reason": "CPU Product preparation does not isolate GPU shader execution; a Product GPU substage probe is required",
        "original_suspect": True,
    }]
    dynamic = matrix["dynamic_mover_breakdown"]
    assert dynamic["valid"] is True
    assert dynamic["sample_count"] == 1
    assert dynamic["valid_sample_count"] == 1
    assert dynamic["avg_ms"] == {
        "build_ms": 0.14,
        "validate_ms": 0.02,
        "publish_ms": 0.04,
        "total_ms": 0.20,
    }
    assert dynamic["max_ms"] == dynamic["avg_ms"]
    assert dynamic["dominant_component"] == "build_ms"
    assert dynamic["dominant_component_avg_ms"] == 0.14
    assert math.isclose(dynamic["dominant_component_share"], 0.7)
    assert dynamic["errors"] == []


def test_stage_timing_gate_fails_closed_without_dynamic_breakdown() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    timing = _stage_timing_block()
    del timing["samples"][0]["dynamic_mover_publication"]["validate_ms"]
    mode["stage_timing_gate"] = True
    mode["stage_timing_valid"] = True
    mode["stage_timing_samples"] = timing["samples"]
    mode["stage_timing_summary"] = timing["summary"]

    summary = summarize([result])
    row = summary["results"][0]
    matrix = row["suspected_owner_matrix"]
    assert summary["ok"] is False
    assert row["ok"] is False
    assert row["stage_timing_valid"] is True
    assert matrix["valid"] is False
    assert matrix["dominant_measured_cpu_stage"] == ""
    assert matrix["dynamic_mover_breakdown"]["valid"] is False
    assert matrix["dynamic_mover_breakdown"]["valid_sample_count"] == 0
    assert matrix["dynamic_mover_breakdown"]["errors"] == [
        "sample[0] invalid dynamic mover fields: validate_ms"
    ]


def test_fl4691_aug4_diagnostic_identifies_measured_cpu_stage() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    raw_samples = [
        (52, 1.352, 0.049, 0.497, 1.899, 0.992, 0.943, 0.014, 0.094, 0.001),
        (67, 1.460, 0.051, 0.508, 2.019, 1.052, 1.034, 0.013, 0.106, 0.001),
        (82, 1.487, 0.047, 0.485, 2.020, 1.049, 0.985, 0.013, 0.104, 0.002),
        (97, 1.476, 0.050, 0.486, 2.012, 0.939, 0.934, 0.012, 0.085, 0.001),
    ]
    samples = []
    for (
        frame_index,
        build_ms,
        validate_ms,
        publish_ms,
        total_ms,
        frame_setup_ms,
        product_prepare_ms,
        submit_ms,
        view_build_ms,
        final_paint_ms,
    ) in raw_samples:
        samples.append({
            "schema": "fl4691.stage_timing.v1",
            "frame_index": frame_index,
            "stage_ms": {
                "dynamic_mover_publish_cpu_ms": total_ms,
                "surface_facts_compile_upload_cpu_ms": 0.0,
            "frame_setup_cpu_ms": frame_setup_ms,
                "product_prepare_cpu_ms": product_prepare_ms,
                "vsb_product_submit_cpu_ms": submit_ms,
                "vsb_view_build_cpu_ms": view_build_ms,
                "final_paint_submit_cpu_ms": final_paint_ms,
            },
            "dynamic_mover_publication": {
                "schema": "fl4691.dynamic_mover_publication_timing.v1",
                "frame_index": frame_index,
                "valid": True,
                "published": True,
                "build_ms": build_ms,
                "validate_ms": validate_ms,
                "publish_ms": publish_ms,
                "total_ms": total_ms,
            },
        })
    mode["stage_timing_gate"] = True
    mode["stage_timing_valid"] = True
    mode["stage_timing_samples"] = samples
    mode["stage_timing_summary"] = {
        "valid": True,
        "errors": [],
        "sample_count": 4,
        "avg_stage_ms": {
            "dynamic_mover_publish_cpu_ms": 1.9875,
            "surface_facts_compile_upload_cpu_ms": 0.0,
            "frame_setup_cpu_ms": 1.008,
            "product_prepare_cpu_ms": 0.974,
            "vsb_product_submit_cpu_ms": 0.013,
            "vsb_view_build_cpu_ms": 0.09725,
            "final_paint_submit_cpu_ms": 0.00125,
        },
        "max_stage_ms": {
            "dynamic_mover_publish_cpu_ms": 2.020,
            "surface_facts_compile_upload_cpu_ms": 0.0,
            "frame_setup_cpu_ms": 1.052,
            "product_prepare_cpu_ms": 1.034,
            "vsb_product_submit_cpu_ms": 0.014,
            "vsb_view_build_cpu_ms": 0.106,
            "final_paint_submit_cpu_ms": 0.002,
        },
        "dominant_stage": "dynamic_mover_publish_cpu_ms",
        "dominant_stage_avg_ms": 1.9875,
    }

    matrix = summarize([result])["results"][0]["suspected_owner_matrix"]
    assert matrix["valid"] is True
    assert matrix["dominant_measured_cpu_stage"] == "dynamic_mover_publication"
    assert [entry["owner"] for entry in matrix["owners"]] == [
        "dynamic_mover_publication",
        "frame_setup_residual",
        "cell_owner_product_prepare",
        "visible_surface_builder_view_build",
        "vsb_product_dispatch_submit",
        "final_painter_submit",
        "surface_facts_compile_upload",
    ]
    dynamic = matrix["dynamic_mover_breakdown"]
    assert dynamic["valid"] is True
    assert dynamic["sample_count"] == 4
    assert dynamic["valid_sample_count"] == 4
    assert dynamic["avg_ms"] == {
        "build_ms": 1.44375,
        "validate_ms": 0.04925,
        "publish_ms": 0.49399999999999994,
        "total_ms": 1.9875000000000003,
    }
    assert dynamic["dominant_component"] == "build_ms"
    assert dynamic["dominant_component_avg_ms"] == 1.44375
    assert math.isclose(
        dynamic["dominant_component_share"],
        1.44375 / 1.9875000000000003,
    )
    assert matrix["unmeasured_original_suspects"] == [
        "actor_foliage_product_gpu_work"
    ]


def test_stage_timing_gate_fails_closed_without_required_stage() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    timing = _stage_timing_block()
    del timing["samples"][0]["stage_ms"]["product_prepare_cpu_ms"]
    mode["stage_timing_gate"] = True
    mode["stage_timing_valid"] = True
    mode["stage_timing_samples"] = timing["samples"]
    mode["stage_timing_summary"] = timing["summary"]

    summary = summarize([result])
    row = summary["results"][0]
    assert summary["ok"] is False
    assert row["ok"] is False
    assert row["stage_timing_valid"] is False


def test_stage_timing_gate_fails_closed_on_summary_mismatch() -> None:
    result = valid_result()
    mode = result["mode_results"][0]
    timing = _stage_timing_block()
    timing["summary"]["avg_stage_ms"]["product_prepare_cpu_ms"] = 4.0
    mode["stage_timing_gate"] = True
    mode["stage_timing_valid"] = True
    mode["stage_timing_samples"] = timing["samples"]
    mode["stage_timing_summary"] = timing["summary"]

    summary = summarize([result])
    row = summary["results"][0]
    assert summary["ok"] is False
    assert row["ok"] is False
    assert row["stage_timing_valid"] is False
    assert row["suspected_owner_matrix"]["valid"] is False


def test_canonical_presentation_has_one_geometry_owner():
    runner_source = Path("scripts/run_godot_camera_turn_perf.py").read_text(encoding="utf-8")
    run_variant_source = runner_source.split("def run_variant(", 1)[1].split(
        "def run_ab_brackets(", 1
    )[0]
    engine_side, project_side = run_variant_source.split('        "--",', 1)
    assert '"--fullscreen"' not in engine_side
    assert '"--resolution"' not in run_variant_source
    assert "_canonical_presentation_user_args" in project_side
    assert _canonical_presentation_user_args(True, None) == []
    assert _canonical_presentation_user_args(False, (3600, 2252)) == [
        "--fl4431-canonical-presentation=3600x2252"
    ]
    try:
        _canonical_presentation_user_args(False, None)
    except ValueError:
        pass
    else:
        raise AssertionError("headed run accepted missing presentation geometry")

    world_source = Path("godot_project/scripts/World.gd").read_text(encoding="utf-8")
    assert "func _force_native_fullscreen_presentation(" not in world_source
    assert "func _apply_owned_presentation_surface(" in world_source
    assert '_PRESENTATION_SURFACE_CANONICAL_USABLE := "canonical_usable"' not in world_source
    assert '_PRESENTATION_SURFACE_CANONICAL_LOCKED := "canonical_locked"' in world_source
    assert "physical_rect.end.y - requested_size.y" in world_source
    assert "DisplayServer.WINDOW_MODE_WINDOWED" in world_source
    assert "DisplayServer.WINDOW_FLAG_BORDERLESS" in world_source
    assert "DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN" in world_source
    assert "conflicting presentation owners" in world_source


def test_perf_driver_locks_external_input() -> None:
    driver = Path("godot_project/tools/profiling/run_camera_turn_perf.gd").read_text()
    assert "func _lock_external_input(node: Node) -> void:" in driver
    assert "_lock_external_input(main)" in driver
    run_start = driver.index("func _run() -> void:")
    first_process_frame = driver.index("await process_frame", run_start)
    assert driver.index("_lock_external_input(main)", run_start) < first_process_frame
    assert driver.index("main.set_fl4431_external_input_locked(true)", run_start) < first_process_frame
    assert driver.index("world.set_fl4431_external_input_locked(true)", run_start) < first_process_frame
    assert "player.set_physics_process(false)" in driver

    main_source = Path("godot_project/scripts/Main.gd").read_text()
    assert "func set_fl4431_external_input_locked(locked: bool) -> void:" in main_source
    gameplay_block = main_source.split("func _process_local_gameplay_actions() -> void:", 1)[1].split(
        "\nfunc ", 1
    )[0]
    assert "if _fl4431_external_input_locked:" in gameplay_block
    assert "if not _fl4431_external_input_locked:" in main_source

    world_source = Path("godot_project/scripts/World.gd").read_text()
    assert "func set_fl4431_external_input_locked(locked: bool) -> void:" in world_source
    bird_block = world_source.split("func _poll_manual_bird_trigger_key() -> void:", 1)[1].split(
        "\nfunc ", 1
    )[0]
    assert "if _fl4431_external_input_locked:" in bird_block


def test_f9_recording_toggle_remains_reachable_during_external_input_lock() -> None:
    """The perf quarantine may block gameplay, but must not block operator F9."""
    main_source = Path("godot_project/scripts/Main.gd").read_text()
    input_block = _gdscript_function_body(main_source, "_input")
    assert 'InputMap.has_action("screen_record_toggle")' in input_block
    assert 'InputMap.event_is_action(key_event, "screen_record_toggle")' in input_block
    assert 'world.call("toggle_screen_recording")' in input_block

    # Main._input is the sole owner in both states. The gameplay quarantine
    # remains elsewhere, while _process cannot toggle F9 a second time.
    assert "get_viewport().set_input_as_handled()" in input_block
    process_block = _gdscript_function_body(main_source, "_process")
    assert "if not _fl4431_external_input_locked:" in process_block
    assert 'Input.is_action_just_pressed("screen_record_toggle")' not in process_block


def main() -> int:
    test_readiness_loop_owns_never_drawn_and_frozen_frame_wakes()
    test_readiness_failure_remains_a_zero_sample_arm()
    summary = summarize([valid_result()])
    row = summary["results"][0]
    assert summary["ok"] is True
    assert row["ok"] is True
    assert row["projection_mode"] == "perspective"
    assert row["avg_fps"] == 29.5
    assert row["avg_delta_ms"] == 34.0
    assert row["git_head"] == "0123456789abcdef"
    assert row["fps_vs_default"] == 1.0
    assert _settled_camera_origin(valid_result()) == (78.1, 17.9365, 144.4)
    assert _parse_camera_origin("78.1,17.9365,144.4") == (78.1, 17.9365, 144.4)
    test_stage_timing_gate_names_dominant_stage()
    test_stage_timing_gate_fails_closed_without_dynamic_breakdown()
    test_fl4691_aug4_diagnostic_identifies_measured_cpu_stage()
    test_stage_timing_gate_fails_closed_without_required_stage()
    test_stage_timing_gate_fails_closed_on_summary_mismatch()
    test_f9_recording_toggle_remains_reachable_during_external_input_lock()

    missing = valid_result()
    missing["mode_results"] = []
    assert summarize([missing])["ok"] is False

    zero = valid_result()
    zero["mode_results"][0]["avg_fps"] = 0.0
    assert summarize([zero])["ok"] is False

    invalid = valid_result()
    invalid["mode_results"][0]["validity"] = {"ready": False}
    assert summarize([invalid])["ok"] is False
    assert _settled_camera_origin(invalid) is None

    with patch.dict(
        os.environ,
        {"FL4431_STALE": "1", "FL4408_STALE": "1", "SAFE_VALUE": "kept"},
        clear=True,
    ):
        arm_env = _arm_local_env({"FL4431_DISABLE_EYE_GRID_DEDUP": "1"})
    assert "FL4431_STALE" not in arm_env
    assert "FL4408_STALE" not in arm_env
    assert arm_env["SAFE_VALUE"] == "kept"
    assert arm_env["FL4431_DISABLE_EYE_GRID_DEDUP"] == "1"

    runner_source = Path("godot_project/tools/profiling/run_camera_turn_perf.gd").read_text()
    mismatch_branch = runner_source.split(
        "if has_expected_camera_origin and origin.distance_to(expect_camera_origin) > EXPECT_CAMERA_ORIGIN_TOLERANCE:",
        1,
    )[1].split('return {\n\t\t\t\t\t"ready": true', 1)[0]
    assert "continue" in mismatch_branch
    assert '"ready": false' not in mismatch_branch
    assert "pose_mismatch_timeout" in runner_source

    wrapper_source = Path("scripts/run_godot_camera_turn_perf.py").read_text()
    assert '"--camera-turn-perf-stationary"' in wrapper_source
    assert '"FL4431_DISABLE_EYE_REUSE": "1"' in wrapper_source
    assert "PIXEL_CAMERA_STATIONARY_MAX := 0.05" in runner_source
    assert '"motion_mode": "stationary" if stationary_mode else "camera_turn"' in runner_source
    assert "pixel_camera_peak_excursion <= PIXEL_CAMERA_STATIONARY_MAX" in runner_source
    test_producer_starvation_profiler_deleted()

    print("PASS: FL-4431 perf summary and isolation-arm environment fail closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_dynamic_mover_v2_includes_wrapper_and_rigid_sync():
    block = _stage_timing_block()
    sample = block["samples"][0]
    dyn = sample["dynamic_mover_publication"]
    dyn.update(schema="fl4691.dynamic_mover_publication_timing.v2",
               publication_wrapper_ms=0.03, rigid_sync_ms=0.27, total_ms=0.5)
    sample["stage_ms"]["dynamic_mover_publish_cpu_ms"] = 0.5
    result = runner._dynamic_mover_breakdown({"stage_timing_samples": [sample]})
    assert result["valid"], result
    assert result["dominant_component"] == "rigid_sync_ms"
    for key, invalid in (("publication_wrapper_ms", -0.01), ("rigid_sync_ms", None),
                         ("total_ms", 0.2), ("schema", "unknown"), ("frame_index", -1)):
        previous = dyn[key]
        dyn[key] = invalid
        assert not runner._dynamic_mover_breakdown({"stage_timing_samples": [sample]})["valid"], key
        dyn[key] = previous
    sample["stage_ms"]["dynamic_mover_publish_cpu_ms"] = 0.6
    assert not runner._dynamic_mover_breakdown({"stage_timing_samples": [sample]})["valid"]

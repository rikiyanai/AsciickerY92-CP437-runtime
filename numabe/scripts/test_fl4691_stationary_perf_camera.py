"""FL-4691: fixed-view measurements must not follow authority actor drift."""
from pathlib import Path


SOURCE = Path("godot_project/tools/profiling/run_camera_turn_perf.gd")


def body(name: str) -> str:
    return SOURCE.read_text().split(f"func {name}(", 1)[1].split("\nfunc ", 1)[0]


def test_stationary_lock_follows_readiness_and_precedes_sampling():
    run = body("_run_projection_mode")
    assert run.index("await _await_readiness(mode)") < run.index("_lock_stationary_camera()")
    assert run.index("_lock_stationary_camera()") < run.index("var start_ticks")
    assert "if stationary_mode and not _lock_stationary_camera():" in run
    lock = body("_lock_stationary_camera")
    assert 'player.set("external_exact_camera_active", true)' in lock
    assert "stationary_camera.top_level = true" in lock
    assert "stationary_camera.global_transform = settled_transform" in lock
    assert "set_process(false)" not in lock


def test_camera_lock_is_released_after_each_mode_including_invalid_modes():
    run = body("_run")
    assert run.index("await _run_projection_mode(mode)") < run.index("_release_stationary_camera()")
    assert run.index("_release_stationary_camera()") < run.index('mode_result.get("invalid"')
    release = body("_release_stationary_camera")
    assert 'player.set("external_exact_camera_active", false)' in release
    assert "stationary_camera.top_level = stationary_camera_was_top_level" in release


def test_failed_motion_check_keeps_all_measured_evidence():
    run = body("_run_projection_mode")
    validation = run.split("if not pixel_camera_motion_valid:", 1)[1]
    assert "validity = {" in validation
    assert validation.count("return {") == 1
    assert '"invalid": not pixel_camera_motion_valid' in validation
    for field in ("frame_timing_records", "frame_deltas_ms", "samples",
                  "authority_queue_before_measurement", "authority_queue_after_measurement"):
        assert f'"{field}": {field}' in validation
    assert "for s in frame_timing_records:" in run
    assert "pixel_camera_peak_excursion >= 0.0 and" in run


def test_blur_intervention_does_not_select_other_startup_features():
    run = body("_run")
    assert '--camera-turn-perf-blur-off' in run
    assert 'effect.call("set_baseline_feature_enabled", "tilt_shift", false)' in run
    assert '--baseline-feature=' not in run
    assert '"environment_features"' in body("_frame_timing_record")
    assert 'get_environment_feature_state' in body("_stage_timing_snapshot")

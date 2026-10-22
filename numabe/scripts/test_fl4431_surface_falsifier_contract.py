from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_fl4265_airplane_proof.py"
HARNESS = (
    ROOT
    / "godot_project"
    / "tools"
    / "proofs"
    / "run_fl4265_airplane_dogfight_proof.gd"
)


def test_surface_falsifier_has_observational_and_canonical_profiles() -> None:
    runner = RUNNER.read_text()
    harness = HARNESS.read_text()

    assert 'choices=("bypass_observe", "canonical_3600x2252")' in runner
    assert '"--fl4431-surface-falsifier-profile="' in runner
    assert 'fl4431_surface_falsifier_profile == "bypass_observe"' in harness
    assert "await _await_canonical_fullscreen_surface()" in harness
    assert '["--resolution", "1280x800"]' not in runner


def test_surface_falsifier_records_real_pixel_camera_dimensions() -> None:
    runner = RUNNER.read_text()
    harness = HARNESS.read_text()

    assert '"get_last_pixel_camera_capture_contract"' in harness
    assert '"pixel_camera_surface_signature_counts"' in harness
    assert '"pixel_camera_surface_sample_count"' in harness
    assert '"surface_falsifier_post_measurement_capture"' in harness
    assert "surface_falsifier_signature_count_mismatch" in runner
    assert "surface_falsifier_timing_coverage_mismatch" in runner
    assert "surface_falsifier_presentation_window_mismatch" in runner
    assert "surface_falsifier_canonical_signature_mismatch" in runner


def test_surface_falsifier_capture_is_outside_timing_window() -> None:
    harness = HARNESS.read_text()

    timing_end = harness.index("var measured_end_usec := Time.get_ticks_usec()")
    falsifier_capture = harness.index(
        '"surface_falsifier_%s.png" % fl4431_surface_falsifier_profile'
    )
    assert timing_end < falsifier_capture

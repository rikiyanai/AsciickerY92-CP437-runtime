"""FL-4359 pinned exact-pose sweep: runner argument and preflight contract.

Static surface only. These tests observe the command the runner would issue and
the named checks it applies; they make no claim about the rendered result.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_fl4359_headed_movement.py"
WRAPPER = ROOT / "scripts" / "run_godot_visual_owner_proof.py"
DRIVER = ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclasses` resolves annotations through sys.modules, so a file-loaded
    # module must be registered before it executes.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _runner_module():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    return _load("fl4359_pinned_sweep_runner", RUNNER)


def _wrapper_module():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    return _load("fl4359_pinned_sweep_wrapper", WRAPPER)


def test_density_override_is_bounded_to_diagnostic_presentation_flight():
    module = _runner_module()
    options = dict(presentation_observer_only=True, presentation_field_capture=True,
                   move_frames=5, jitter_capture_frame_limit=5)
    assert module._presentation_density_diagnostic_enabled(_sweep_args(**options))
    for key, value in [("run_classification", "acceptance"),
                       ("presentation_observer_only", False),
                       ("presentation_field_capture", False), ("move_frames", 6),
                       ("exact_pose_sweep", False), ("exact_camera_projection", "orthographic"),
                       ("analyzer_proof_profile", "legacy"),
                       ("full_grid_contract_mode", "stationary")]:
        assert not module._presentation_density_diagnostic_enabled(_sweep_args(**{**options, key: value}))


def _sweep_args(**overrides) -> argparse.Namespace:
    """The scenario identity, expressed as the namespace the preflight reads."""
    base = dict(
        run_classification="diagnostic-only",
        # FL-4896: the oracle scenarios run FC (operator decision); strict True
        # FF, the default mode, measures its sweep with the text flip route.
        presentation_mode="FC",
        camera_mode="plane_air",
        godot_window_mode="fullscreen",
        analyzer_proof_profile="full_grid_temporal_oracle_v1",
        full_grid_contract_mode="exact_translation",
        exact_pose_sweep=True,
        exact_pose_sweep_delta="0.05,0,0",
        exact_camera_position="95.586,35.394,59.003",
        exact_camera_yaw="63.198115",
        exact_camera_pitch="-25.159299",
        exact_camera_projection="perspective",
        exact_camera_fov="58",
        exact_camera_size="",
        exact_pose_sweep_positions=(
            "95.586,35.394,59.003;"
            "95.836,35.394,59.003;"
            "96.086,35.394,59.003;"
            "96.336,35.394,59.003;"
            "96.586,35.394,59.003"
        ),
        move_frames=120,
        after_frames=2,
        turn_frames=0,
        jitter_capture_frame_limit=5,
        jitter_capture_world_sample_stride=1,
        glyph_matrix_cell_px=32,
        day_cycle_time=0.5,
        product_time=17.0,
        skip_jitter_analysis=False,
        allow_large_jitter_capture=True,
        single_frame=False,
        fl4216_temporal_capture=False,
        ghost_replay_segment="",
        clean_play_no_capture=False,
        fl4359_terminal_reprojection_stationary_calibration=False,
        force_baseline_features="foliage_cards,ascii_birds",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _preflight(module, args: argparse.Namespace) -> dict:
    return module._exact_pose_sweep_preflight(
        args,
        effective_jitter_capture_frame_limit=5,
        godot_timeout_seconds=300.0,
    )


def test_sweep_positions_are_the_declared_origin_plus_delta_per_frame() -> None:
    module = _runner_module()
    positions = module._exact_pose_sweep_positions(
        _sweep_args(exact_pose_sweep_positions=""), 5
    )
    assert len(positions) == 5
    for index, position in enumerate(positions):
        assert position[0] == 95.586 + 0.05 * index
        assert position[1] == 35.394
        assert position[2] == 59.003


def test_oracle_profile_forces_the_day_night_cycle_feature() -> None:
    module = _runner_module()
    forced = module._forced_baseline_features(_sweep_args())
    assert forced.split(",")[-1] == "day_night_cycle"
    # The celestial pin is only observable when the feature reaches the effect.
    assert "foliage_cards" in forced
    assert "tilt_shift_off" in forced


def test_non_oracle_profiles_also_force_tilt_shift_off() -> None:
    module = _runner_module()
    args = _sweep_args(analyzer_proof_profile="auto", camera_mode="plane_air")
    assert module._forced_baseline_features(args) == "foliage_cards,ascii_birds,tilt_shift_off,day_night_cycle"


def test_empty_feature_list_is_never_replaced_by_a_single_forced_feature() -> None:
    """A pinned day-cycle time must force the feature even from an empty list."""
    module = _runner_module()
    args = _sweep_args(force_baseline_features="")
    assert module._forced_baseline_features(args) == "tilt_shift_off,day_night_cycle"
    result = _preflight(module, args)
    assert result["ok"] is True, result["failed_checks"]


def test_sweep_preflight_passes_on_the_declared_scenario_identity() -> None:
    module = _runner_module()
    result = _preflight(module, _sweep_args())
    assert result["ok"] is True, result["failed_checks"]
    assert result["route_identity"] == module.EXACT_POSE_SWEEP_ROUTE_IDENTITY


def test_sweep_preflight_rejects_routes_that_never_arm_the_capture_writer() -> None:
    module = _runner_module()
    for override in (
        {"single_frame": True},
        {"fl4216_temporal_capture": True},
        {"ghost_replay_segment": "tower_strafe"},
        {"clean_play_no_capture": True},
        {"fl4359_terminal_reprojection_stationary_calibration": True},
    ):
        result = _preflight(module, _sweep_args(**override))
        assert result["ok"] is False, override
        assert "default_capture_route_selected" in result["failed_checks"], override


def test_sweep_preflight_rejects_missing_pose_and_capture_identity() -> None:
    module = _runner_module()
    cases = {
        "exact_camera_position": {"exact_camera_position": ""},
        "exact_camera_yaw": {"exact_camera_yaw": ""},
        "exact_camera_pitch": {"exact_camera_pitch": ""},
        "projection_parameter_present": {"exact_camera_fov": ""},
        "full_grid_pose_contract": {"exact_pose_sweep_positions": "nope"},
        "world_stride_one": {"jitter_capture_world_sample_stride": 64},
        "maximum_glyph_grid": {"glyph_matrix_cell_px": 20},
        "turn_frames_zero": {"turn_frames": 1},
        "tilt_shift_off": {"extra_user_arg": ["--tilt-shift=on"]},
        "presentation_identity_valid": {"godot_window_mode": "windowed"},
        "release_and_settle_frames": {"after_frames": 3},
        "jitter_metadata_enabled": {"skip_jitter_analysis": True},
        "large_capture_acknowledged": {"allow_large_jitter_capture": False},
        "product_time_pin_present": {"product_time": None},
        "full_grid_oracle_profile_selected": {"analyzer_proof_profile": "auto"},
    }
    for expected_check, override in cases.items():
        result = _preflight(module, _sweep_args(**override))
        assert result["ok"] is False, expected_check
        assert expected_check in result["failed_checks"], (expected_check, result)


def test_day_cycle_pin_stays_required_for_the_oracle_profile() -> None:
    module = _runner_module()
    result = _preflight(module, _sweep_args(day_cycle_time=0.75))
    assert "fixed_day_cycle_time" in result["failed_checks"]


def test_exact_translation_rejects_repeated_supplied_positions() -> None:
    module = _runner_module()
    result = _preflight(
        module,
        _sweep_args(
            exact_pose_sweep_positions=(
                "95.586,35.394,59.003;"
                "95.586,35.394,59.003;"
                "96.086,35.394,59.003;"
                "96.336,35.394,59.003;"
                "96.586,35.394,59.003"
            )
        ),
    )
    assert result["ok"] is False
    assert "full_grid_pose_contract" in result["failed_checks"]


def test_stationary_mode_requires_five_equal_positions() -> None:
    module = _runner_module()
    args = _sweep_args(
        full_grid_contract_mode="stationary",
        exact_pose_sweep_positions=(
            "95.586,35.394,59.003;"
            "95.586,35.394,59.003;"
            "95.586,35.394,59.003;"
            "95.586,35.394,59.003;"
            "95.586,35.394,59.003"
        ),
    )
    assert _preflight(module, args)["ok"] is True
    rejected = _preflight(
        module,
        _sweep_args(full_grid_contract_mode="stationary"),
    )
    assert rejected["ok"] is False
    assert "full_grid_pose_contract" in rejected["failed_checks"]


def test_existing_exact_pose_capture_preflight_keeps_its_single_frame_contract() -> None:
    """Guard: the new route must not have relaxed the pre-existing check-set."""
    module = _runner_module()
    args = _sweep_args(
        exact_pose_sweep=False,
        skip_jitter_analysis=False,
        extra_user_arg=[],
        probe_owner_isolation="",
        exact_player_position="95.586,35.394,59.003",
        fl4216_temporal_capture_interval_seconds=0.0,
        fl4216_temporal_capture_count=0,
    )
    result = module._exact_pose_capture_preflight(args, godot_timeout_seconds=300.0)
    assert result["ok"] is False
    assert "single_frame_or_temporal_capture" in result["failed_checks"]
    assert "jitter_analysis_retired" in result["failed_checks"]


def test_runner_command_carries_the_sweep_and_the_forced_celestial_feature() -> None:
    wrapper = _wrapper_module()
    scenario = next(
        row
        for row in wrapper.SCENARIOS
        if row.scenario_id == "pinned_translation_sweep_perspective"
    )
    command = [
        sys.executable,
        str(RUNNER),
        "--out",
        ".run/godot_visual_owner_proof/_pinned_sweep_contract_probe",
        "--godot-window-mode",
        "fullscreen",
        "--camera-mode",
        scenario.camera_mode,
        "--move-frames",
        str(scenario.move_frames),
        "--settle-frames",
        str(scenario.settle_frames),
        "--after-frames",
        str(scenario.after_frames),
        "--force-baseline-features",
        wrapper.DEFAULT_FEATURES,
        "--analyzer-proof-profile",
        scenario.analyzer_proof_profile,
        "--day-cycle-time",
        wrapper.FULL_GRID_ORACLE_DAY_CYCLE_TIME,
        "--allow-large-jitter-capture",
        *scenario.runner_args,
        "--run-classification",
        "diagnostic-only",
        # The shared .run budget is a repository-wide condition, not part of this
        # argument contract; the probe never launches or writes artifacts.
        "--keep-forever",
        # FL-4896: the full-grid oracle is a fixed-cell VSB instrument that
        # strict True FF (the default mode) refuses before launch.
        "--presentation-mode",
        "CC",
        "--preflight-only",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF_CHECK_PASS" in result.stdout
    godot_command = next(
        line.split("godot_command: ", 1)[1]
        for line in result.stdout.splitlines()
        if "godot_command: " in line
    )
    assert "--fl4408-exact-pose-sweep=true" in godot_command
    assert "--fl4408-exact-pose-sweep-positions=" in godot_command
    assert "--fl4408-exact-camera-position=95.586,35.394,59.003" in godot_command
    assert "--fl4408-day-cycle-time=0.5" in godot_command
    assert "--fl4359-full-grid-temporal-oracle=1" in godot_command
    assert "--fl4359-jitter-capture-frame-limit=5" in godot_command
    # Outcome B: without this feature the effect pins _celestial_frame_time to 0.5
    # and ignores the requested 0.75 hour.
    feature_arg = next(
        part
        for part in godot_command.split()
        if part.startswith("--force-baseline-feature=")
    )
    assert "day_night_cycle" in feature_arg


def test_scenario_reuses_the_recorded_b6_pose_verbatim() -> None:
    wrapper = _wrapper_module()
    scenario = next(
        row
        for row in wrapper.SCENARIOS
        if row.scenario_id == "pinned_translation_sweep_perspective"
    )
    assert scenario.analyzer_proof_profile == wrapper.FULL_GRID_ORACLE_PROFILE
    assert scenario.camera_mode == "plane_air"
    args = list(scenario.runner_args)
    for flag, value in (
        ("--exact-camera-position", wrapper.TRANSLATION_PAIR_P0),
        ("--exact-camera-yaw", wrapper.TRANSLATION_PAIR_YAW),
        ("--exact-camera-pitch", wrapper.TRANSLATION_PAIR_PITCH),
        ("--exact-camera-fov", wrapper.TRANSLATION_PAIR_FOV),
        ("--exact-pose-sweep-positions", wrapper.TRANSLATION_SWEEP_POSITIONS),
        ("--full-grid-contract-mode", "exact_translation"),
    ):
        assert args[args.index(flag) + 1] == value
    assert "--exact-pose-sweep" in args


def test_canonical_ground_turn_uses_a_distinct_first_five_turn_frame_adapter() -> None:
    module = _runner_module()
    wrapper = _wrapper_module()
    scenario = next(
        row for row in wrapper.SCENARIOS
        if row.scenario_id == "ground_turn_perspective"
    )
    args = list(scenario.runner_args)
    assert scenario.resolution == "3840x2160"
    assert args[args.index("--full-grid-contract-mode") + 1] == "camera_turn"
    assert args[args.index("--turn-frames") + 1] == "8"
    assert args[args.index("--jitter-capture-frame-limit") + 1] == "5"
    contract = module._full_grid_pose_contract(_sweep_args(
        camera_mode="perspective",
        full_grid_contract_mode="camera_turn",
        exact_pose_sweep=False,
        exact_pose_sweep_positions="",
        move_frames=120,
        turn_frames=8,
        turn_action="rotate_right",
    ))
    assert contract["pass"] is True
    assert contract["camera_turn_input_window_contains_capture"] is True


def test_camera_turn_command_pins_recovered_raster_and_capture_start() -> None:
    wrapper = _wrapper_module()
    scenario = next(
        row for row in wrapper.SCENARIOS
        if row.scenario_id == "ground_turn_perspective"
    )
    args = argparse.Namespace(
        fullscreen=True,
        resolution="1920x1080",
        godot_timeout=300.0,
        server_boot_timeout=30.0,
        max_jitter_pct=999.0,
        features=wrapper.DEFAULT_FEATURES,
        with_jitter_analysis=True,
        presentation_field_capture_budget_bytes=_runner_module().PRESENTATION_FIELD_REQUIRED_PEAK_BYTES,
    )
    invocation_dir = ROOT / ".run" / "_fl4359_camera_turn_adapter_contract"
    command = wrapper._build_command(args, scenario, invocation_dir, 2989, scenario.runner_args)
    assert command[command.index("--resolution") + 1] == "3840x2160"
    result = subprocess.run(
        # FL-4896: strict True FF (the default mode) refuses the full-grid oracle.
        [*command, "--presentation-mode", "CC", "--preflight-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF_CHECK_PASS" in result.stdout
    assert "--fl4359-full-grid-capture-start-frame=0" in result.stdout
    assert (
        "--fl4359-presentation-profile="
        "fl4359_ground_turn_terminal16x32_3840x2160_perspective_v1"
    ) in result.stdout


def _pinned_sweep_preflight_command(*extra: str) -> list[str]:
    """The pinned-sweep identity as a `--preflight-only` probe, plus `extra`.

    Built from the wrapper scenario so the probe cannot drift away from the
    command the wrapper actually issues. Nothing launches and nothing is written.
    """
    wrapper = _wrapper_module()
    scenario = next(
        row
        for row in wrapper.SCENARIOS
        if row.scenario_id == "pinned_translation_sweep_perspective"
    )
    return [
        sys.executable,
        str(RUNNER),
        "--out",
        ".run/godot_visual_owner_proof/_pinned_sweep_contract_probe",
        "--godot-window-mode",
        "fullscreen",
        "--camera-mode",
        scenario.camera_mode,
        "--move-frames",
        str(scenario.move_frames),
        "--settle-frames",
        str(scenario.settle_frames),
        "--after-frames",
        str(scenario.after_frames),
        "--force-baseline-features",
        wrapper.DEFAULT_FEATURES,
        "--analyzer-proof-profile",
        scenario.analyzer_proof_profile,
        "--day-cycle-time",
        wrapper.FULL_GRID_ORACLE_DAY_CYCLE_TIME,
        "--allow-large-jitter-capture",
        *scenario.runner_args,
        "--run-classification",
        "diagnostic-only",
        "--keep-forever",
        "--preflight-only",
        *extra,
    ]


def _preflight_godot_command(*extra: str) -> str:
    result = subprocess.run(
        _pinned_sweep_preflight_command(*extra),
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF_CHECK_PASS" in result.stdout
    return next(
        line.split("godot_command: ", 1)[1]
        for line in result.stdout.splitlines()
        if "godot_command: " in line
    )


def _user_args(godot_command: str) -> list[str]:
    """Only the tokens Godot exposes through OS.get_cmdline_user_args().

    Godot fills that list exclusively from the arguments after the bare `--`
    separator, so a flag placed before it would never reach
    ASCIICompositorEffect.gd's opt-in read.
    """
    parts = godot_command.split()
    assert "--" in parts, godot_command
    return parts[parts.index("--") + 1 :]


def test_translation_pair_p0_p1_differs_from_p0_p2_only_in_x_and_labels() -> None:
    """Outcome B: one variable, one axis. Everything else stays byte-identical."""
    wrapper = _wrapper_module()
    by_id = {row.scenario_id: row for row in wrapper.SCENARIOS}
    p0_p2 = by_id["translation_pair_p0_p2"]
    p0_p1 = by_id["translation_pair_p0_p1"]
    for field in ("camera_mode", "fl_refs", "move_frames", "settle_frames", "after_frames"):
        assert getattr(p0_p1, field) == getattr(p0_p2, field), field
    assert p0_p1.runner_args == p0_p2.runner_args == ()
    assert p0_p1.extra_user_args == p0_p2.extra_user_args == ()
    assert p0_p1.analyzer_proof_profile == p0_p2.analyzer_proof_profile == ""
    # The shared P0 reference capture must be the same invocation in both pairs.
    assert p0_p1.runner_arg_sets[0] == p0_p2.runner_arg_sets[0]
    (p1_label, p1_args) = p0_p1.runner_arg_sets[1]
    (p2_label, p2_args) = p0_p2.runner_arg_sets[1]
    assert (p1_label, p2_label) == ("p1", "p2")
    differences = [
        (left, right)
        for left, right in zip(p1_args, p2_args, strict=True)
        if left != right
    ]
    assert differences == [
        ("fl4359_translation_pair_p1", "fl4359_translation_pair_p2"),
        (wrapper.TRANSLATION_PAIR_P1, wrapper.TRANSLATION_PAIR_P2),
    ]


def test_p1_is_the_recorded_b6_pose_translated_by_a_quarter_unit_on_x() -> None:
    wrapper = _wrapper_module()
    p0 = [float(part) for part in wrapper.TRANSLATION_PAIR_P0.split(",")]
    p1 = [float(part) for part in wrapper.TRANSLATION_PAIR_P1.split(",")]
    assert p1[0] - p0[0] == 0.25
    assert p1[1:] == p0[1:]
    # The recorded pose constants are reused verbatim, not re-derived.
    args = wrapper._translation_pair_args(wrapper.TRANSLATION_PAIR_P1, "label")
    for flag, value in (
        ("--exact-camera-yaw", wrapper.TRANSLATION_PAIR_YAW),
        ("--exact-camera-pitch", wrapper.TRANSLATION_PAIR_PITCH),
        ("--exact-camera-fov", wrapper.TRANSLATION_PAIR_FOV),
        ("--exact-camera-projection", "perspective"),
    ):
        assert args[args.index(flag) + 1] == value


def test_driver_applies_the_sweep_before_each_captured_frame() -> None:
    """The pose must be written before the render, on the default capture route."""
    source = DRIVER.read_text()
    assert "func _apply_exact_pose_sweep(" in source
    assert '_cmd("--fl4408-exact-pose-sweep=", "false") == "true"' in source
    # Suppressing ground input is what makes the choreography command-declared.
    assert (
        "not is_plane and not _exact_pose_sweep and not (local_actor_id == 0"
        in source
    )
    # Two call sites: the active window and the release/settle window.
    assert (
        source.count("_apply_exact_pose_sweep(main, camera, sparse_sequence_frame)")
        == 2
    )
    # The Camera3D write alone leaves an unexpired compositor pin holding the
    # previous pose, so every applied sweep pose must also be re-published.
    assert "_apply_exact_camera(camera)\n\t# FL-4359 sweep-pin invariant" in source
    assert source.count("_apply_exact_pose_sweep(camera, sparse_sequence_frame)") == 0


def _synthetic_capture(tmp_path, camera_worlds: list[tuple[float, float, float]]):
    """A tiny stand-in for the multi-GB capture: only the frame rows matter."""
    path = tmp_path / "jitter_capture.jsonl"
    lines = ['{"row_type":"header","note":"ignored by the frame-pose grader"}']
    for frame_id, (x, y, z) in enumerate(camera_worlds):
        lines.append(
            '{"frame_id":%d,"row_type":"frame","frame_trace":{"camera_world":'
            '{"x":%r,"y":%r,"z":%r}}}' % (frame_id, x, y, z)
        )
    path.write_text("\n".join(lines) + "\n")
    return path


DECLARED_SWEEP = [
    [447.0, 48.0, -336.0],
    [448.0, 48.0, -338.0],
    [449.0, 48.0, -339.0],
    [450.0, 48.0, -341.0],
]


def test_sweep_frame_poses_match_when_each_frame_renders_its_declared_eye(
    tmp_path,
) -> None:
    """Frames past the declared list clamp to the final entry, like the driver."""
    module = _runner_module()
    capture = _synthetic_capture(
        tmp_path,
        [tuple(position) for position in DECLARED_SWEEP] + [(450.0, 48.0, -341.0)],
    )
    rows = module._exact_pose_sweep_frame_pose_rows(capture, DECLARED_SWEEP)
    assert [row["frame_id"] for row in rows] == [0, 1, 2, 3, 4]
    assert all(row["match"] for row in rows), rows
    assert rows[4]["expected"] == DECLARED_SWEEP[3]


def test_sweep_frame_poses_flag_a_held_pin_that_repeats_the_previous_eye(
    tmp_path,
) -> None:
    """The r9 shape: F1 rendered twice, so declared F2 never reached a frame."""
    module = _runner_module()
    capture = _synthetic_capture(
        tmp_path,
        [
            (447.0, 48.0, -336.0),
            (447.0, 48.0, -336.0),
            (449.0, 48.0, -339.0),
            (450.0, 48.0, -341.0),
        ],
    )
    rows = module._exact_pose_sweep_frame_pose_rows(capture, DECLARED_SWEEP)
    assert [row["match"] for row in rows] == [True, False, True, True]
    mismatched = [row["frame_id"] for row in rows if not row["match"]]
    assert mismatched == [1]
    assert rows[1]["expected"] == DECLARED_SWEEP[1]
    assert rows[1]["observed"] == [447.0, 48.0, -336.0]


def test_sweep_frame_pose_reads_orientation_from_nested_pose(tmp_path) -> None:
    module = _runner_module()
    capture = tmp_path / "jitter_capture.jsonl"
    capture.write_text(
        '{"frame_id":0,"frame_trace":{"camera_world":{"x":447,"y":48,"z":-336},'
        '"pose":{"camera_yaw":2.356194490192345,"camera_pitch":-0.3665191429188092}}}\n'
    )
    rows = module._exact_pose_sweep_frame_pose_rows(
        capture,
        [DECLARED_SWEEP[0]],
        requested_yaw=2.356194490192345,
        requested_pitch=-0.3665191429188092,
    )
    assert rows[0]["camera_yaw_match"] is True
    assert rows[0]["camera_pitch_match"] is True


def test_frame_png_route_replaces_the_oracle_profile_in_the_sweep_preflight() -> None:
    """The corrected full-grid contract does not accept the off-oracle PNG arm."""
    module = _runner_module()
    args = _sweep_args(analyzer_proof_profile="auto", frame_pngs=True)
    result = _preflight(module, args)
    assert result["ok"] is False
    assert "full_grid_oracle_profile_selected" in result["failed_checks"]


def test_frame_png_route_forces_the_celestial_feature_like_the_oracle() -> None:
    """Five pinned-eye PNGs are only comparable at a pinned day-cycle hour."""
    module = _runner_module()
    forced = module._forced_baseline_features(
        _sweep_args(analyzer_proof_profile="auto", frame_pngs=True)
    )
    assert forced.split(",")[-1] == "day_night_cycle"
    assert "foliage_cards" in forced


def test_frame_png_poses_are_graded_against_the_declared_sweep() -> None:
    """No capture exists off-oracle, so the driver receipt owns the poses."""
    module = _runner_module()
    receipt = {
        "frame_png_poses": [
            {
                "sequence_frame": index,
                "camera_position": {"x": x, "y": y, "z": z},
            }
            for index, (x, y, z) in enumerate(
                [tuple(position) for position in DECLARED_SWEEP]
                + [(450.0, 48.0, -341.0)]
            )
        ]
    }
    rows = module._frame_png_pose_rows(receipt, DECLARED_SWEEP)
    assert [row["frame_id"] for row in rows] == [0, 1, 2, 3, 4]
    assert all(row["match"] for row in rows), rows
    # Index 4 is the held pose: the driver clamps at the end of the list.
    assert rows[4]["expected"] == DECLARED_SWEEP[3]


def test_frame_png_poses_flag_a_pin_that_never_advanced() -> None:
    """The failure this route exists to catch: one pose rendered five times."""
    module = _runner_module()
    receipt = {
        "frame_png_poses": [
            {
                "sequence_frame": index,
                "camera_position": {"x": 447.0, "y": 48.0, "z": -336.0},
            }
            for index in range(5)
        ]
    }
    rows = module._frame_png_pose_rows(receipt, DECLARED_SWEEP)
    assert [row["frame_id"] for row in rows if not row["match"]] == [1, 2, 3, 4]


def test_driver_gates_the_frame_png_route_off_the_full_grid_oracle() -> None:
    """The mid-window PNG hazard applies only to the exhaustive capture."""
    source = DRIVER.read_text()
    assert '_frame_png_dir = _cmd("--fl4408-frame-png-dir=", "")' in source
    assert (
        'return _frame_png_dir != "" and not _full_grid_temporal_oracle'
        in source
    )
    # Two save sites: the active window and the release/settle window.
    assert source.count("await _save_frame_png(main, camera, sparse_sequence_frame)") == 2
    assert '"frame_pngs": _frame_png_paths,' in source
    assert '"frame_png_poses": _frame_png_poses_receipt(),' in source


def test_sweep_frame_pose_tolerance_admits_float_round_trip_only() -> None:
    module = _runner_module()
    assert module.EXACT_POSE_SWEEP_FRAME_POSE_TOLERANCE == 1e-4


def _gif_families(tmp_path: Path, count: int = 4) -> dict[str, str]:
    paths: dict[str, str] = {}
    for index in range(count):
        path = tmp_path / f"family_{index}.gif"
        path.write_bytes(b"GIF89a")
        paths[f"family_{index}"] = str(path)
    return paths


def _valid_full_grid_summary(tmp_path: Path) -> dict:
    return {
        "proof_profile": "full_grid_temporal_oracle_v1",
        "frame_count": 5,
        "exact_pose_sweep_frame_poses": [
            {
                "frame_id": index,
                "match": True,
                "camera_yaw_match": True,
                "camera_pitch_match": True,
            }
            for index in range(5)
        ],
        "gif_families": _gif_families(tmp_path),
    }


def _valid_full_grid_receipt() -> dict:
    return {
        "route_identity": "scripted_airborne_airplane_movement",
        "camera_mode": "plane_air",
        "local_actor_id": 1,
        "airplane_dynamic_face_count": 539,
        "airplane_before": {"x": 1.0, "y": 30.0, "z": 2.0},
        "airplane_after": {"x": 2.0, "y": 30.0, "z": 2.0},
        "airplane_yaw_turn_measured": True,
        "day_cycle_time_pin_valid": True,
        "day_cycle_time": 0.5,
        "celestial_runtime_witness_valid": True,
        "celestial_witness_invalid_count": 0,
        "celestial_witness_change_count": 0,
        "celestial_frame_witnesses": [
            {
                "celestial_witness_valid": True,
                "celestial_time": 0.5,
                "celestial_night_factor": 0.0,
                "celestial_sun_direction": [0.0, 1.0, 0.0],
            }
            for _index in range(5)
        ],
        "capture_schedule": {
            "effective_active_frames": 5,
            "effective_total_frames": 5,
            "observed_active_frames": 5,
            "schedule_complete": True,
        },
        "sparse_capture_drain_pass": True,
        "sparse_capture_pending_after_drain": False,
        "final_render_metadata": {"feature_state": {"tilt_shift": False}},
    }


def test_full_grid_receipt_contract_accepts_complete_runtime_witnesses(tmp_path) -> None:
    module = _runner_module()
    contract = module._full_grid_receipt_contract(
        _valid_full_grid_summary(tmp_path),
        _valid_full_grid_receipt(),
        _sweep_args(),
    )
    assert contract["pass"] is True, contract


def test_full_grid_receipt_contract_rejects_changed_yaw_or_pitch(tmp_path) -> None:
    module = _runner_module()
    summary = _valid_full_grid_summary(tmp_path)
    summary["exact_pose_sweep_frame_poses"][2]["camera_yaw_match"] = False
    contract = module._full_grid_receipt_contract(
        summary,
        _valid_full_grid_receipt(),
        _sweep_args(),
    )
    assert contract["pass"] is False
    assert "rendered_positions_and_orientation" in contract["failed_checks"]


def test_full_grid_receipt_contract_rejects_unforced_day_night_runtime(tmp_path) -> None:
    module = _runner_module()
    receipt = _valid_full_grid_receipt()
    receipt["celestial_runtime_witness_valid"] = False
    contract = module._full_grid_receipt_contract(
        _valid_full_grid_summary(tmp_path),
        receipt,
        _sweep_args(),
    )
    assert contract["pass"] is False
    assert "day_cycle_time" in contract["failed_checks"]


def test_full_grid_receipt_contract_rejects_wrong_actor(tmp_path) -> None:
    module = _runner_module()
    receipt = _valid_full_grid_receipt()
    receipt["local_actor_id"] = 0
    contract = module._full_grid_receipt_contract(
        _valid_full_grid_summary(tmp_path),
        receipt,
        _sweep_args(),
    )
    assert contract["pass"] is False
    assert "actual_airplane" in contract["failed_checks"]


def test_full_grid_receipt_contract_rejects_tilt_shift_on(tmp_path) -> None:
    module = _runner_module()
    receipt = _valid_full_grid_receipt()
    receipt["final_render_metadata"]["feature_state"]["tilt_shift"] = True
    contract = module._full_grid_receipt_contract(
        _valid_full_grid_summary(tmp_path),
        receipt,
        _sweep_args(),
    )
    assert contract["pass"] is False
    assert "tilt_shift_off" in contract["failed_checks"]


def test_full_grid_runtime_contract_reports_missing_post_run_gifs_without_failing_runtime(tmp_path) -> None:
    module = _runner_module()
    summary = _valid_full_grid_summary(tmp_path)
    summary["gif_families"] = _gif_families(tmp_path, count=3)
    contract = module._full_grid_receipt_contract(
        summary,
        _valid_full_grid_receipt(),
        _sweep_args(),
    )
    assert contract["pass"] is True
    assert contract["checks"]["all_four_gif_families"] is False
    assert contract["post_run_artifacts_complete"] is False
    assert contract["post_run_artifact_status"] == "pending_gif_generation"
    assert "all_four_gif_families" not in contract["failed_checks"]


def test_full_grid_post_run_contract_can_require_gif_families(tmp_path) -> None:
    module = _runner_module()
    summary = _valid_full_grid_summary(tmp_path)
    summary["gif_families"] = _gif_families(tmp_path, count=3)
    contract = module._full_grid_receipt_contract(
        summary,
        _valid_full_grid_receipt(),
        _sweep_args(),
        require_gif_families=True,
    )
    assert contract["pass"] is False
    assert "all_four_gif_families" in contract["failed_checks"]


def test_full_grid_runtime_contract_accepts_release_and_settle_pose_rows(tmp_path) -> None:
    module = _runner_module()
    summary = _valid_full_grid_summary(tmp_path)
    summary["exact_pose_sweep_frame_poses"].extend(
        [
            {
                "frame_id": index,
                "match": True,
                "camera_yaw_match": True,
                "camera_pitch_match": True,
            }
            for index in (5, 6)
        ]
    )
    receipt = _valid_full_grid_receipt()
    receipt["capture_schedule"].update(
        {
            "effective_release_frames": 1,
            "effective_settle_frames": 1,
            "effective_total_frames": 7,
            "observed_release_frames": 1,
            "observed_settle_frames": 1,
        }
    )
    receipt["celestial_frame_witnesses"].extend(
        [
            {
                "celestial_witness_valid": True,
                "celestial_time": 0.5,
                "celestial_night_factor": 0.0,
                "celestial_sun_direction": [0.0, 1.0, 0.0],
            }
            for _index in (5, 6)
        ]
    )
    contract = module._full_grid_receipt_contract(summary, receipt, _sweep_args())
    assert contract["pass"] is True, contract


def test_runner_marks_a_sweep_pose_mismatch_inadmissible() -> None:
    """The mismatch must reach the durable summary, not only the console."""
    source = RUNNER.read_text()
    assert 'summary["exact_pose_sweep_frame_poses"] = sweep_frame_poses' in source
    assert 'summary["reason"] = "exact_pose_sweep_frame_pose_mismatch"' in source
    assert (
        '"[fl4359-headed] exact_pose_sweep_frame_pose_mismatch "' in source
    )


def test_accumulated_b6_pitch_is_compared_modulo_one_turn() -> None:
    """The recorded pitch and Godot's wrapped pitch describe the same direction."""
    driver_source = DRIVER.read_text()
    runner_source = RUNNER.read_text()
    assert "angle_difference(rendered_pitch, _exact_camera_pitch)" in driver_source
    assert "rendered_pitch - expected_pitch" in runner_source
    assert "rendered_pitch - float(requested_pitch)" not in runner_source


def test_pinned_sweep_receipt_grades_camera_choreography() -> None:
    """The sweep suppresses player input, so camera displacement owns motion."""
    source = DRIVER.read_text()
    assert "else camera_after.distance_to(camera_before)" in source
    assert "if _exact_pose_sweep\n\t\telse first_measured_motion_frame" in source
    assert "elif _exact_pose_sweep:" in source
    assert "ok = ok and actor_evidence_ok" in source
    assert "actor_rows > 0" in source
    assert "local_pilot_body_hidden and airframe_faces > 0" in source

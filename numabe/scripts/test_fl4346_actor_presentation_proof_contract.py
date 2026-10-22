import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_snapshot_is_observational_and_reuses_existing_owners() -> None:
    source = text("godot_project/render/api/ASCIICompositorEffect.gd")
    body = source.split("func snapshot_fl4346_actor_presentation_receipt()", 1)[1]
    body = body.split("\nfunc ", 1)[0]
    assert "_pending_actor_cell_debug_rows" in body
    assert 'row.get("actor_root_world", {})' not in body
    assert "wake_trail.presentation_endpoint()" in body
    assert '"wake_endpoint_owner": "product_wake_clock_presented_actor_root"' in body
    assert "wake_trail.last_pushed_for_source(" in body
    assert "buffer_create" not in body
    assert "texture_create" not in body
    assert "dispatch" not in body
    assert '"pixel_camera_capture_contract"' in body


def test_driver_uses_real_input_and_never_writes_player_camera_pose() -> None:
    source = text("scripts/fl4346_actor_presentation_driver.gd")
    assert '"move_right"' not in source
    assert '"rotate_left"' in source
    assert '"rotate_right"' in source
    assert "Input.parse_input_event" in source
    assert "Input.flush_buffered_events" in source
    assert "Input.action_press" not in source
    assert "Input.action_release" not in source
    assert "player.global_position =" not in source
    assert "camera.global_position =" not in source
    assert "camera.global_transform =" not in source
    assert "_nearest_snapshot_actor_id(main, client)" in source
    assert 'hud.get("last_local_actor_id")' in source
    assert '"snapshot_count"' in source
    assert '"hud_local_actor_id"' in source
    assert '"actors"' in source
    assert 'snapshot.get("actors", {})' in source
    assert "Vector2(actor_position.x, actor_position.z).distance_to" in source
    assert "if value is Vector3:" in source
    wait_body = source.split("func _wait_for_server(main: Node) -> int:", 1)[1]
    wait_body = wait_body.split("\nfunc ", 1)[0]
    assert "is_connected_to_authority" not in wait_body
    assert 'int(client.get("snapshot_count")) <= 0' in wait_body
    assert "_snapshot_has_actor(client, local_actor_id)" in wait_body
    assert "main.call(\"apply_client_snapshot\", local_actor_id)" in wait_body
    assert "func _snapshot_has_actor(client: Variant, actor_id: int) -> bool:" in source
    assert 'KEY_W' in source
    assert '_lay_real_wake(world, player)' in source
    assert 'WAKE_LAY_MIN_DISTANCE_WORLD' in source
    assert '"real_wake_not_laid"' in source
    assert '"wake_lay": wake_lay' in source
    assert '_wait_for_presentation_root_settle(' in source
    assert '"presentation_root_did_not_settle"' in source
    assert '"presentation_settle": presentation_settle' in source


def test_on_foot_perspective_aims_at_the_shared_actor_root() -> None:
    source = text("godot_project/scripts/player.gd")
    assert "const PLAYER_CHASE_LOOK_Y" not in source
    assert "if piloting else anchor" in source
    assert "camera.look_at(look_target, Vector3.UP)" in source


def test_failed_behavior_keeps_all_temporal_captures() -> None:
    driver = text("scripts/fl4346_actor_presentation_driver.gd")
    runner = text("scripts/run_fl4346_actor_presentation_proof.py")
    assert "sequence_rejected" not in driver
    assert "const EXPECTED_CAPTURE_IMAGE_COUNT := 16" in driver
    assert "_screenshots.size() == EXPECTED_CAPTURE_IMAGE_COUNT" in driver
    assert "FRAMEBUFFER_MIN_FOOTPRINT_PIXELS = 30" in runner


def test_driver_captures_stationary_qe_temporal_phases_plus_runtime_facts() -> None:
    source = text("scripts/fl4346_actor_presentation_driver.gd")
    for phase in (
        "%s_00_entry",
        "%s_03_e_return",
        "%s_04_zoom_in",
        "%s_05_zoom_return",
        "%s_06_swing_start",
        "%s_07_swing_return",
    ):
        assert phase in source
    assert '_press_action(&"attack_primary", KEY_ENTER)' in source
    assert 'get("visual_presentation_kind", "")) == "attack"' in source
    assert '"swing_present": swing_present' in source
    assert '"swing_returned": swing_returned' in source
    assert '"%s_0%d_q_%d" % [' in source
    assert "next_q_phase_capture + 1" in source
    assert "snapshot_fl4346_actor_presentation_receipt" in source
    assert "build_final_render_cell_rows" not in source
    assert '"final_actor_owner_rows"' not in source
    assert '"final_actor_owner_count"' not in source
    assert '"wake_endpoint_to_root_world"' in source
    assert "TURN_SWEEP_RADIANS := PI * 0.5" in source
    assert "TURN_PHASE_CAPTURE_COUNT := 2" in source
    assert "q_sweep_complete" in source
    assert "e_return_complete" in source
    assert '"local_actor_alive"' in source
    assert '"local_actor_screen_center"' in source
    assert 'presentation_kind != "death"' in source
    assert "func _actor_row_contract_summary(captures: Array[Dictionary], local_actor_id: int)" in source
    assert '"compositor_actor_row"' in source
    assert '"every_capture_has_local_actor_row"' in source
    assert '"presentation_diagnostics_enabled": false' in source
    assert "final_actor_reference_marker_rows" not in source
    assert '"actor_anchor_pole_enabled"' not in source
    assert "func _pixel_camera_presentation_shift(receipt: Dictionary) -> Vector2:" in source
    assert '"style_post_shift_px"' in source
    assert "func _shift_screen_rect(rect_value: Variant, shift: Vector2) -> Dictionary:" in source
    assert '"local_actor_body_anchor_screen_px_logical"' in source
    assert '"pixel_camera_style_post_shift_px"' in source
    assert '"max_rect_anchor_offset_axis_delta_px"' in source
    assert '"max_rect_size_axis_delta_px"' in source
    assert '"yaw_return_error_radians"' in source
    assert '"yaw_returned"' in source
    assert "YAW_RETURN_MAX_ERROR_RADIANS" in source
    assert '"q_turn_incomplete"' in source
    assert '"e_turn_incomplete"' in source
    assert "func _drive_turn_sweep_with_trace(" in source
    assert '"rotate_right", KEY_E, "e", phase_prefix, q_turn_radians, false' in source
    assert "radians >= target_radians" in source
    assert "radians += absf(wrapf(current_yaw - prior_yaw, -PI, PI))" in source
    turn = source.split("func _drive_turn_sweep_with_trace(", 1)[1].split(
        "func _capture_active_swing_motion", 1
    )[0]
    repeat = source.split("func _repeat_held_key(keycode: Key) -> void:", 1)[1].split(
        "func _release_action", 1
    )[0]
    assert "_repeat_held_key(keycode)" in turn
    assert "event.echo = true" in repeat
    assert "Input.parse_input_event(event)" in repeat
    assert "Input.action_press" not in source
    assert "Input.action_release" not in source
    assert "func _capture_presentation_motion_frame(" in source
    assert '"motion_kind": motion_kind' in source
    assert 'float(input_state.get("rotate_left", 0.0)) > 0.0' in source
    assert 'float(input_state.get("rotate_right", 0.0)) > 0.0' in source
    assert "EXPECTED_Q_MOTION_CAPTURE_COUNT" in source
    assert "EXPECTED_E_MOTION_CAPTURE_COUNT" in source
    assert "func _capture_active_swing_motion(" in source
    assert 'local_actor_presentation_kind", "")) == "attack"' in source
    assert '"%s_06_swing_start" % phase_prefix, 0, 1' in source
    assert "SWING_MOTION_CAPTURE_COUNT := 1" in source
    axis_gate = source.split('"axis_aligned":', 1)[1].split(
        '"max_player_world_delta":', 1
    )[0]
    assert "max_rect_anchor_offset_axis_delta <= AXIS_MAX_ERROR_PX" not in axis_gate
    assert "max_rect_size_axis_delta <= AXIS_MAX_ERROR_PX" in axis_gate
    assert "max_rect_anchor_offset_delta <= AXIS_MAX_ERROR_PX" not in axis_gate
    assert "max_rect_size_delta <= AXIS_MAX_ERROR_PX" not in axis_gate
    assert "const START_ZOOM_TAPS := 5" in source
    assert "const ZOOM_TAPS := 1" in source
    assert "for _tap in range(START_ZOOM_TAPS):" in source
    assert '"turn_zoom_stable"' in source
    assert "max_zoom_scale_delta <= TURN_ZOOM_MAX_DELTA" in source
    assert '"anchor_fixed_during_zoom"' in source
    assert "zoom_anchor_delta <= AXIS_MAX_ERROR_PX" in source


def test_wrapper_owns_fl4346_package_without_fl4359_pose_gate() -> None:
    source = text("scripts/run_fl4346_actor_presentation_proof.py")
    assert "run_fl4359_headed_movement.py" not in source
    assert "ghost_replay_pose_envelope_mismatch" not in source
    assert "fl4346_water_overview.png" in source
    assert 'grid_path = case_dir / "temporal_grid.png"' in source
    assert 'gif_path = case_dir / "temporal.gif"' in source
    assert 'motion_grid_path = case_dir / "motion_grid.png"' in source
    assert 'motion_gif_path = case_dir / "motion.gif"' in source
    assert "round(index * (len(panels) - 1) / 4)" in source
    assert "for index in (0, 2, 4, 6, 8)" not in source
    assert "def _motion_capture_lines(" in source
    assert "PARTIAL/MONITORING pending operator visual signoff" in source
    assert "crop_width = min(360, image.width)" in source
    assert "crop_height = min(280, image.height)" in source
    assert "Actual gameplay frame: F10 off; no diagnostic overlay" in source
    assert "def _annotate(" in source
    annotate = source.split("def _annotate(", 1)[1].split("def _panel", 1)[0]
    assert "return image.copy()" in annotate
    assert "draw.rectangle" not in annotate
    assert "draw.line" not in annotate
    assert "Baseline A" not in source
    assert '"--current-only"' not in source
    assert '"water"' in source
    assert '"orthographic"' in source
    assert '"perspective"' in source
    for phase in (
        '"00_entry"',
        '"01_q_45"',
        '"02_q_90"',
        '"03_e_return"',
        '"04_zoom_in"',
        '"05_zoom_return"',
        '"06_swing_start"',
        '"07_swing_return"',
    ):
        assert phase in source
    for retired_phase in (
        '"01_q_25"',
        '"02_q_50"',
        '"03_q_75"',
        '"04_q_100"',
        '"05_e_full_return"',
        '"06_zoom_in"',
        '"07_zoom_return"',
        '"08_swing_start"',
        '"09_swing_return"',
    ):
        assert retired_phase not in source
    assert 'FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES = ("04_zoom_in",)' in source
    assert "visual_runtime_case_failed" in source
    case_loop = source.split("for index, scenario in enumerate(scenarios):", 1)[1]
    case_loop = case_loop.split("visual_runtime_pass =", 1)[0]
    assert "break" not in case_loop
    assert "zoom_width_delta >= 0.0 and zoom_height_delta >= 0.0" in text(
        "scripts/fl4346_actor_presentation_driver.gd"
    )


def test_wrapper_pins_isolated_poses_and_canonical_perf_dimensions() -> None:
    source = text("scripts/run_fl4346_actor_presentation_proof.py")
    assert "def _find_all_yaw_water_pose() -> dict:" in source
    assert "WATER_CAMERA_ORBIT_RADIUS = 33.0" in source
    assert "WATER_CAMERA_RAY_HALF_WIDTH = 1.0" in source
    assert "sightline_y" in source
    assert "237.5" not in source
    assert "-255.5" not in source
    assert 'env["PLAYER_SPAWN_X"]' in source
    assert 'env["PLAYER_SPAWN_Z"]' in source
    assert "3600, 2252" in source
    assert "3600, 2260" in source
    assert "180, 113" in source
    assert "run_godot_camera_turn_perf.py" in source
    assert '"--uncapped"' in source
    assert '"orthographic,perspective"' in source
    assert '"--runtime-source-ref"' in source
    assert '"--day-cycle-time"' in source
    assert '"0.5"' in source
    assert 'elif visual_runtime_pass:' in source
    assert '"structural_gates_pass": visual_runtime_pass and bool(performance.get("pass"))' in source


def test_wrapper_resolves_an_unoccluded_authored_water_pose() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_fl4346_actor_presentation_proof.py"), "--print-water-pose"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    pose = json.loads(result.stdout)
    assert pose["surface"] == "water"
    assert pose["x"] == 152.5
    assert pose["z"] == -5.5
    assert "clear 33-unit camera-to-foot rays" in pose["pose_source"]


def test_wrapper_runs_only_water_in_both_required_projections() -> None:
    source = text("scripts/run_fl4346_actor_presentation_proof.py")
    scenario_body = source.split("def _scenarios() -> tuple[dict, ...]:", 1)[1]
    scenario_body = scenario_body.split("\ndef ", 1)[0]
    assert scenario_body.count('"surface": "water"') == 2
    assert '"surface": "ground"' not in scenario_body
    assert scenario_body.count('"projection": "orthographic"') == 1
    assert scenario_body.count('"projection": "perspective"') == 1


def test_wake_endpoint_identity_uses_the_unquantized_actor_row_root() -> None:
    source = text("scripts/run_fl4346_actor_presentation_proof.py")
    body = source.split("def _wake_endpoint_identity(", 1)[1]
    body = body.split("\ndef ", 1)[0]
    assert 'root = row.get("actor_root_world")' in body
    assert 'row.get("final_actor_owner_rows")' not in body
    assert '"wake_endpoint_not_actor_root"' in body
    assert "FRAMEBUFFER_WAKE_ANCHOR_EPSILON_WORLD = 0.000001" in source


def test_grader_searches_captures_under_the_drivers_slugged_phase_prefix() -> None:
    """FL-4346 attempt-91 pin: the grader and the driver must agree on names.

    The driver names every capture PNG with a SLUGGED projection prefix
    ("water_ortho_00_entry.png"), while the runner's case directory uses the
    long projection word ("water_orthographic/"). A previous runner build fed
    the LONG case name into the framebuffer summary, so the grader searched
    for "water_orthographic_*_mask.png" files that were never written —
    every phase graded as missing and the whole run stopped after one arm
    without ever measuring its own captures. This test pins both halves of
    the repaired agreement so the two name builders cannot silently split
    again.
    """
    runner = text("scripts/run_fl4346_actor_presentation_proof.py")
    driver = text("scripts/fl4346_actor_presentation_driver.gd")
    # Half 1 — the summary loop must build phase names from the passed-in
    # slugged prefix, never from the long case name.
    summary_body = runner.split("def _framebuffer_registration_summary(", 1)[1]
    summary_body = summary_body.split("\ndef ", 1)[0]
    assert 'phase_name = f"{phase_prefix}_{suffix}"' in summary_body
    assert "case_name" not in summary_body
    # Half 2 — the call site must derive that prefix with the SAME slug rule
    # the driver uses, from the scenario's surface and projection fields.
    assert (
        "f\"{scenario['surface']}_{_projection_slug(scenario['projection'])}\""
        in runner
    )
    # Half 3 — the driver's own prefix construction is the source of truth
    # this test protects: surface plus the short slug ("ortho"/"perspective").
    assert 'var projection_slug := "ortho" if projection == "orthographic" else "perspective"' in driver
    assert 'var phase_prefix := "%s_%s" % [_surface, projection_slug]' in driver
    # And the runner's slug function must keep emitting the identical slugs.
    assert 'return "ortho" if projection == "orthographic" else "perspective"' in runner

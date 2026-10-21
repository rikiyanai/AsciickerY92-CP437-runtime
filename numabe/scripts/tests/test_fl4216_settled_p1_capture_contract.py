from pathlib import Path
import importlib.util
import json


ROOT = Path(__file__).resolve().parents[2]
DRIVER = (ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd").read_text()
RUNNER = (ROOT / "scripts/run_fl4359_headed_movement.py").read_text()
EFFECT = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
RUNNER_SPEC = importlib.util.spec_from_file_location("fl4359_runner", ROOT / "scripts/run_fl4359_headed_movement.py")
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
RUNNER_MODULE = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(RUNNER_MODULE)


def test_runner_exposes_bounded_explicit_settle_wait() -> None:
    assert "--fl4216-stability-wait-seconds" in RUNNER
    assert "must be between 1.5 and 2.0" in RUNNER
    assert "--fl4216-stability-wait-seconds=%.3f" in RUNNER
    assert "--keep-forever" in RUNNER
    assert "check_run_budget(ROOT, out_dir, keep_forever=args.keep_forever)" in RUNNER
    assert 'receipt["run_budget"] = run_budget.as_dict()' in RUNNER


def test_driver_uses_monotonic_wait_and_republishes_exact_pose() -> None:
    assert "Time.get_ticks_usec()" in DRIVER
    assert '"wait_start_ticks_usec"' in DRIVER
    assert '"wait_end_ticks_usec"' in DRIVER
    assert '"wait_elapsed_ms"' in DRIVER
    assert '"wait_frame_count"' in DRIVER
    assert '"pose_stable_samples"' in DRIVER
    assert '"last_player_server_presentation_y_offset"' in DRIVER
    settle = DRIVER[DRIVER.index("func _wait_for_fl4216_stable_publication"):]
    assert settle.count("_apply_exact_pose(player, camera)") >= 2
    assert settle.count("_publish_exact_camera_transform(main, camera)") >= 2
    assert "await process_frame" in settle
    assert '"exact_pose_republished_each_process_frame": true' in settle


def test_driver_requires_stable_actor_product_publication_before_capture() -> None:
    assert '"fl4216_exact_pose_or_actor_product_publication_not_stable"' in DRIVER
    assert '"fl4216_exact_single_frame_requires_1_5_to_2_0_second_settle"' in DRIVER
    assert '"stable_actor_product_publication_samples"' in DRIVER
    capture = DRIVER[DRIVER.index("func _capture_single_frame"):]
    assert "fl4216_stability_receipt" in capture


def test_slow_first_frame_gets_bounded_publication_grace_without_weaker_gate() -> None:
    settle = DRIVER[DRIVER.index("func _wait_for_fl4216_stable_publication"):
                    DRIVER.index("func ", DRIVER.index("func _wait_for_fl4216_stable_publication") + 5)]
    assert "publication_deadline_us := requested_us + 10000000" in settle
    assert "Time.get_ticks_usec() - started_us < publication_deadline_us" in settle
    assert "stable_samples < 2 or pose_stable_samples < 2" in settle
    assert "elapsed_us >= requested_us and stable_samples >= 2 and pose_stable_samples >= 2" in settle


def test_driver_classifies_existing_selected_surface_quarters() -> None:
    assert "func _classify_fl4216_selected_surface_dump" not in DRIVER
    assert "def _classify_fl4216_selected_surface_dump" in RUNNER
    classification = RUNNER[RUNNER.index("def _classify_fl4216_selected_surface_dump"):]
    for field in (
        "selected_surface_contact_quarters",
        "selected_surface_contact_static_source_quarters",
        "selected_surface_contact_dynamic_source_quarters",
        "selected_surface_static_quarters",
        "selected_surface_dynamic_quarters",
        "selected_surface_reflection_quarters",
        "selected_surface_submerged_source_quarters",
    ):
        assert field in classification
    assert "selected_surface_quarters" in classification
    assert "compute_list_dispatch" not in classification
    assert "storage_buffer_create" not in classification


def test_python_selected_surface_scan_handles_large_exponent_and_classifies_rows(tmp_path: Path) -> None:
    cells = tmp_path / "cells.jsonl"
    rows = [
        {"selected_surface_quarters": [{"valid": True, "owner_kind": 3, "direct_water_solid_contact": True, "submerged_source_owner_kind": 1, "submerged_source_present": True, "reflection_word": 7, "reflection_authored_shade_q": 2}]},
        {"selected_surface_quarters": [{"valid": True, "owner_kind": 1, "direct_water_solid_contact": False, "submerged_source_present": False, "reflection_word": 0, "reflection_authored_shade_q": 0}]},
        {"selected_surface_quarters": [{"valid": True, "owner_kind": 4, "direct_water_solid_contact": True, "submerged_source_owner_kind": 4, "submerged_source_present": True, "reflection_word": 0, "reflection_authored_shade_q": 0, "depth": 1e99999}]},
    ]
    raw_rows = [
        json.dumps(rows[0]),
        json.dumps(rows[1]),
        '{"selected_surface_quarters":[{"valid":true,"owner_kind":4,"direct_water_solid_contact":true,"submerged_source_owner_kind":4,"submerged_source_present":true,"reflection_word":0,"reflection_authored_shade_q":0,"depth":1e99999}]}',
    ]
    cells.write_text("\n".join(raw_rows) + "\n")
    result = RUNNER_MODULE._classify_fl4216_selected_surface_dump({"final_render_cell_dump": {"artifact_paths": {"cells_jsonl": str(cells)}}})
    assert result["readback_state"] == "selected_surface_classification_ok"
    assert result["json_parse_errors"] == 0
    assert result["cells_read"] == 3
    assert result["selected_surface_contact_quarters"] == 2
    assert result["selected_surface_contact_static_source_quarters"] == 1
    assert result["selected_surface_contact_dynamic_source_quarters"] == 1
    assert result["selected_surface_static_quarters"] == 1
    assert result["selected_surface_dynamic_quarters"] == 1
    assert result["selected_surface_water_quarters"] == 1
    assert result["selected_surface_reflection_quarters"] == 1
    assert result["selected_surface_submerged_source_quarters"] == 2


def test_python_selected_surface_scan_fails_closed_on_malformed_json(tmp_path: Path) -> None:
    cells = tmp_path / "malformed_cells.jsonl"
    cells.write_text('{"selected_surface_quarters":[]}\nnot-json\n')
    result = RUNNER_MODULE._classify_fl4216_selected_surface_dump({"final_render_cell_dump": {"artifact_paths": {"cells_jsonl": str(cells)}}})
    assert result["json_parse_errors"] == 1
    assert result["readback_state"] == "selected_surface_json_invalid"


def test_vsb_dump_decoder_uses_raster_subcell_law_not_cell_major() -> None:
    # VSB b64 has one record per 2xANSI subcell. For ANSI cell (90,57),
    # width=180 means the four records are raster indices 41220..41221 and
    # 41580..41581. The tempting Product cell-major sequence is 41400..41403
    # and must remain rejected for this upstream diagnostic buffer.
    ansi_width = 180
    cell_x, cell_y = 90, 57
    subcell_width = ansi_width * 2
    raster_indices = [
        ((cell_y * 2 + (slot >> 1)) * subcell_width)
        + (cell_x * 2 + (slot & 1))
        for slot in range(4)
    ]
    cell_major_indices = [((cell_y * ansi_width + cell_x) * 4) + slot for slot in range(4)]
    assert raster_indices == [41220, 41221, 41580, 41581]
    assert cell_major_indices == [41400, 41401, 41402, 41403]
    assert raster_indices != cell_major_indices
    assert "selected_vsb_record_index" in EFFECT
    assert "selected_subcell_y * selected_subcell_width" in EFFECT
    assert "selected_cell_index * 4 + selected_slot" not in EFFECT


def test_actor_rows_are_not_mislabeled_as_retained_hand_proof() -> None:
    assert "exact camera/hand pose" not in DRIVER
    assert "retained hand publication" not in DRIVER
    assert '"launcher_log_hand_facts"' in RUNNER
    assert '"female_hand_faces_expected": 8082' in RUNNER
    assert '"scene_geometry_db_parity_ok"' in RUNNER
    assert '"source": "godot_client.log"' in RUNNER

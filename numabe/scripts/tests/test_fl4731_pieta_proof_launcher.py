"""FL-4731 contracts for fail-closed Pieta product proof launches."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import struct
import pytest


ROOT = Path(__file__).resolve().parents[2]
NATIVE_DRIVER_PATH = (
    ROOT / "scripts/proofs/proof_fl4684_native_live_producer_motion.py"
)
NATIVE_DRIVER_TEXT = NATIVE_DRIVER_PATH.read_text()
WEB_DRIVER = (
    ROOT / "scripts/proofs/proof_fl4684_web_live_producer_motion.js"
).read_text()
PLAYWRIGHT_DRIVER = (ROOT / "scripts/proofs/proof_driver_playwright.js").read_text()
WEB_TEMPORAL_HELPER_PATH = (
    ROOT / "scripts/proofs/fl4208_web_temporal_measurement.js"
)
WEB_TEMPORAL_HELPER = WEB_TEMPORAL_HELPER_PATH.read_text()
GAME_APP = (ROOT / "engine/game_app.cpp").read_text()
MAINMENU = (ROOT / "engine/mainmenu.cpp").read_text()
GAME_INPUT = (ROOT / "engine/game_input.cpp").read_text()
GAME_RENDER_BRIDGE = (ROOT / "engine/game_render_bridge.cpp").read_text()
UI_STATE = (ROOT / "engine/ui_state.h").read_text()
SERVER_TICK = (ROOT / "server/server_tick.cpp").read_text()
EMISSION_CENSUS = (
    ROOT / "apps/fl4512-runtime/platform/fl4684_scene_fact_emission_census.h"
).read_text()
PIETA_MAP = ROOT / "assets/a3d/frog_pieta_4x4.a3d"

SPEC = importlib.util.spec_from_file_location("fl4731_native_driver", NATIVE_DRIVER_PATH)
assert SPEC and SPEC.loader
NATIVE_DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE_DRIVER)


def test_native_recorder_preserves_leading_git_status_column(monkeypatch):
    monkeypatch.setattr(NATIVE_DRIVER.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, " M AGENTS.md\n M path with spaces\n"))
    paths = [line[3:] for line in NATIVE_DRIVER.git_output("status", "--porcelain").splitlines()]
    assert paths == ["AGENTS.md", "path with spaces"]


def test_stationary_diagnostic_does_not_claim_operator_input(monkeypatch):
    waits = []
    monkeypatch.setattr(NATIVE_DRIVER.time, "sleep", waits.append)
    observed = NATIVE_DRIVER.wait_for_operator_play(12, 3, "none")
    assert waits == [12]
    assert observed["input_source"] == "none"
    assert observed["input_source_is_declared_not_verified"] is True
    assert observed["synthetic_input_sent"] is False
    assert "occurred while the camera was moving" not in NATIVE_DRIVER_TEXT
    assert "does not establish camera motion or acceptance" in NATIVE_DRIVER_TEXT


def test_native_driver_requires_local_authority_before_launch(monkeypatch, tmp_path, capsys):
    game = tmp_path / "game"
    game.touch()
    monkeypatch.setattr(NATIVE_DRIVER, "GAME", game)
    monkeypatch.setattr(NATIVE_DRIVER, "SERVER", tmp_path / "missing-server")
    monkeypatch.setattr(NATIVE_DRIVER.sys, "argv", ["native-driver"])
    assert NATIVE_DRIVER.main() == 2
    assert "build with: make server" in capsys.readouterr().err


def test_native_recorder_rejects_spawn_without_presentation():
    gate = NATIVE_DRIVER.presentation_evidence_gate({}, [], 0)
    assert gate["passed"] is False
    assert gate["final_tuple_frames_observed"] is None
    census = {"fl4693_final_tuple_frames_observed": 10,
              "fl4693_final_tuple_consumer_frames_observed": 10,
              "fl4693_final_tuple_consumer_mismatch_cells": 0}
    assert NATIVE_DRIVER.presentation_evidence_gate(census, [], 0)["passed"] is True
    assert NATIVE_DRIVER.presentation_evidence_gate(census, [], 1)["passed"] is False
    row = {"exists": True, "tuple_stride": 48, "actual_bytes": 96, "bytes": 96}
    assert NATIVE_DRIVER.presentation_evidence_gate(census, [row], 1)["passed"] is True
    row["actual_bytes"] = 48
    assert NATIVE_DRIVER.presentation_evidence_gate(census, [row], 1)["passed"] is False
    census["fl4693_final_tuple_consumer_mismatch_cells"] = 1
    assert NATIVE_DRIVER.presentation_evidence_gate(census, [], 0)["passed"] is False


def test_native_recorder_can_exclude_large_bridge_exports_from_timing():
    assert '"--bridge-facts", action=argparse.BooleanOptionalAction, default=True' in NATIVE_DRIVER_TEXT
    assert 'env.pop("FL4260_CAPTURE_BRIDGE", None)' in NATIVE_DRIVER_TEXT
    assert 'if args.bridge_facts:\n        env["FL4260_CAPTURE_BRIDGE"]' in NATIVE_DRIVER_TEXT
    assert '"bridge_fact_capture_enabled": args.bridge_facts' in NATIVE_DRIVER_TEXT


def test_native_structural_measurement_is_run_local_and_preserves_prior_output(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert NATIVE_DRIVER.prepare_structural_measurement("", run) is None
    target = run / "nested" / "structural.jsonl"
    assert NATIVE_DRIVER.prepare_structural_measurement(str(target), run) == target
    assert target.parent.is_dir()
    target.write_text("prior evidence")
    with pytest.raises(ValueError, match="already exists"):
        NATIVE_DRIVER.prepare_structural_measurement(str(target), run)
    assert target.read_text() == "prior evidence"
    with pytest.raises(ValueError, match="inside"):
        NATIVE_DRIVER.prepare_structural_measurement(str(tmp_path / "outside.jsonl"), run)
    escape = run / "escape"
    escape.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        NATIVE_DRIVER.prepare_structural_measurement(str(escape / "outside.jsonl"), run)


def test_native_structural_capture_is_explicit_observation_only():
    source = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp").read_text()
    assert 'env.pop("FL4512_STRUCTURAL_TURN_NATIVE_AUTO_ARM", None)' in NATIVE_DRIVER_TEXT
    assert 'env["FL4512_STRUCTURAL_TURN_NATIVE_AUTO_ARM"] = "1"' in NATIVE_DRIVER_TEXT
    assert 'bridge->fl4512_active_stroke_observation_valid' in source
    block = source.split('static bool native_arm_attempted = false;', 1)[1].split('#endif', 1)[0]
    assert 'Fl4512ArmStructuralTurnMeasurementV1();' in block
    assert 'native_arm_attempted = true;' in block


def test_native_driver_uses_explicit_map_start_instead_of_receipt_only_map_arg():
    assert 'DEFAULT_MAP = "assets/a3d/frog_pieta_4x4.a3d"' in NATIVE_DRIVER_TEXT
    assert '[str(GAME), "--auto-start-map", args.map]' in NATIVE_DRIVER_TEXT
    assert 'strcmp(argv[p], "--auto-start-map")' in GAME_APP
    assert "g_requested_a3d_auto_start = true" in GAME_APP
    assert "g_requested_a3d_auto_start || MainMenuAutoShotFlagPresent()" in MAINMENU


def test_pieta_acceptance_map_is_side_by_side_baked_terrain_and_live_mesh():
    raw = PIETA_MAP.read_bytes()
    magic, header_size, patch_count, _reserved = struct.unpack_from("<4sIII", raw, 0)
    assert magic == b"AS3D"
    assert header_size == 16
    assert patch_count == 192

    patch_coords = []
    relief_coords = []
    offset = 16
    for _ in range(patch_count):
        patch_x, patch_y = struct.unpack_from("<ii", raw, offset)
        patch_coords.append((patch_x, patch_y))
        visual = struct.unpack_from("<64H", raw, offset + 8)
        for row in range(8):
            for col in range(8):
                if visual[row * 8 + col] == 8:
                    relief_coords.append((patch_x * 8 + col, patch_y * 8 + row))
        offset += 188
    assert min(x for x, _ in patch_coords) == -8
    assert max(x for x, _ in patch_coords) == 7
    assert min(y for _, y in patch_coords) == -5
    assert max(y for _, y in patch_coords) == 6
    assert len(relief_coords) >= 600
    map_min_x = min(x for x, _ in patch_coords) * 8
    map_max_x = max(x for x, _ in patch_coords) * 8 + 7
    map_min_y = min(y for _, y in patch_coords) * 8
    map_max_y = max(y for _, y in patch_coords) * 8 + 7
    assert min(x for x, _ in relief_coords) >= map_min_x + 24
    assert max(x for x, _ in relief_coords) <= 0
    assert min(y for _, y in relief_coords) >= map_min_y + 16
    assert max(y for _, y in relief_coords) <= map_max_y - 24

    offset += 256 * 4 * 16 * 8
    world_version, instance_count = struct.unpack_from("<ii", raw, offset)
    offset += 8
    assert world_version == -6
    assert instance_count == 1
    (mesh_name_len,) = struct.unpack_from("<i", raw, offset)
    offset += 4
    mesh_name = raw[offset:offset + mesh_name_len]
    offset += mesh_name_len
    (instance_name_len,) = struct.unpack_from("<i", raw, offset)
    offset += 4 + instance_name_len
    transform = struct.unpack_from("<16d", raw, offset)
    assert mesh_name == b"pieta.akm"
    assert transform[12:15] == (8.0, 0.0, 40960.0)


def test_native_driver_binds_optional_temporal_corpus_to_same_run_receipt():
    assert '"--temporal-measurement-path"' in NATIVE_DRIVER_TEXT
    assert 'env["FL4208_TEMPORAL_MEASURE_PATH"]' in NATIVE_DRIVER_TEXT
    assert '"temporal_measurement": (' in NATIVE_DRIVER_TEXT
    assert "summarize_temporal_measurement(temporal_measurement_path)" in NATIVE_DRIVER_TEXT


def test_native_driver_wires_and_cleans_indexed_bridge_captures():
    assert 'bridge_path = out_dir / "bridge_cells.jsonl"' in NATIVE_DRIVER_TEXT
    assert 'env["FL4260_CAPTURE_BRIDGE"] = str(bridge_path)' in NATIVE_DRIVER_TEXT
    assert 'env["FL4260_CAPTURE_REQUIRE_ACTIVE_STROKE"] = "1"' in NATIVE_DRIVER_TEXT
    assert 'list(out_dir.glob("bridge_cells*.jsonl"))' in NATIVE_DRIVER_TEXT
    assert "bridge_capture ok=(\\d+) rc=(-?\\d+)" in NATIVE_DRIVER_TEXT


def test_native_driver_parses_bridge_capture_files_and_reports_identity(tmp_path: Path):
    bridge_path = tmp_path / "bridge_cells.0007.jsonl"
    bridge_path.write_text('{"schema":"fl4260.bridge_cells.v18"}\n')
    stdout = "\n".join(
        (
            f"[FL-4260] bridge_capture ok=1 rc=0 path={bridge_path} "
            "shot_index=7 shot_count=8 bridge_frame=1234",
            f"[FL-4260] bridge_capture ok=0 rc=-5 path={tmp_path / 'failed.jsonl'} "
            "shot_index=8 shot_count=8 bridge_frame=1235",
            "[FL-4260] bridge_capture ok=1 rc=0 path=missing.jsonl "
            "shot_index=9 shot_count=8 bridge_frame=1236",
        )
    )
    parsed = NATIVE_DRIVER.parse_bridge_captures(stdout)
    assert parsed == [
        {
            "ok": 1,
            "rc": 0,
            "path": str(bridge_path),
            "shot_index": 7,
            "shot_count": 8,
            "bridge_frame": 1234,
        },
        {
            "ok": 0,
            "rc": -5,
            "path": str(tmp_path / "failed.jsonl"),
            "shot_index": 8,
            "shot_count": 8,
            "bridge_frame": 1235,
        },
        {
            "ok": 1,
            "rc": 0,
            "path": "missing.jsonl",
            "shot_index": 9,
            "shot_count": 8,
            "bridge_frame": 1236,
        },
    ]
    captures = NATIVE_DRIVER.summarize_bridge_captures(stdout)
    assert captures[0] == {
        "ok": 1,
        "rc": 0,
        "path": str(bridge_path),
        "shot_index": 7,
        "shot_count": 8,
        "bridge_frame": 1234,
        "exists": True,
        "bytes": bridge_path.stat().st_size,
        "sha256": hashlib.sha256(bridge_path.read_bytes()).hexdigest(),
    }
    assert captures[1] == {
        "ok": 0,
        "rc": -5,
        "path": str(tmp_path / "failed.jsonl"),
        "shot_index": 8,
        "shot_count": 8,
        "bridge_frame": 1235,
        "exists": False,
        "bytes": None,
        "sha256": None,
    }
    assert captures[2] == {
        "ok": 1,
        "rc": 0,
        "path": "missing.jsonl",
        "shot_index": 9,
        "shot_count": 8,
        "bridge_frame": 1236,
        "exists": False,
        "bytes": None,
        "sha256": None,
    }


def test_native_driver_binds_bridge_fact_captures_to_receipt():
    assert "bridge_fact_captures = summarize_bridge_captures(stdout_text)" in NATIVE_DRIVER_TEXT
    assert '"bridge_fact_captures": bridge_fact_captures' in NATIVE_DRIVER_TEXT


def test_native_driver_summarizes_temporal_corpus_without_loading_it_whole(
    tmp_path: Path,
):
    corpus = tmp_path / "temporal_measurement.jsonl"
    corpus.write_text("".join(json.dumps(row) + "\n" for row in [
        {
            "schema": "fl4208.temporal_measurement.v2",
            "record_kind": "frame_summary",
            "frame": 1,
        },
        {
            "schema": "fl4208.temporal_measurement.v2",
            "record_kind": "cell",
            "frame": 1,
            "gate": "CALIBRATION_CANDIDATE",
            "scorer_kind_cur": 1,
            "scorer_version_cur": 2,
            "score_domain": "fl4512.shape6_f8_w4.v2.score_int.u32",
        },
    ]))
    summary = NATIVE_DRIVER.summarize_temporal_measurement(corpus)
    assert summary["exists"] is True
    assert summary["sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert summary["rows"] == 2
    assert summary["json_errors"] == 0
    assert summary["record_kind_counts"] == {"cell": 1, "frame_summary": 1}
    assert summary["gate_counts"] == {"CALIBRATION_CANDIDATE": 1}
    assert summary["scorer_identity_counts"] == {
        "1:2:fl4512.shape6_f8_w4.v2.score_int.u32": 1
    }


def test_web_pieta_motion_sets_measurement_env_before_main_and_exports_memfs():
    assert "installEmscriptenEnvironment" in PLAYWRIGHT_DRIVER
    assert "await page.addInitScript" in WEB_TEMPORAL_HELPER
    assert "moduleValue.preRun = preRun" in WEB_TEMPORAL_HELPER
    assert "phase: 'preRun_applied'" in WEB_TEMPORAL_HELPER
    assert "FS.readFile(sourcePath" in WEB_TEMPORAL_HELPER
    assert "page.waitForEvent('download'" in WEB_TEMPORAL_HELPER
    assert "FL4208_TEMPORAL_MEASURE_PATH" in WEB_DRIVER
    assert "temporal_measurement: temporalMeasurement" in WEB_DRIVER
    assert "FL-4208 Web temporal corpus receipt gate failed" in WEB_DRIVER


def test_web_temporal_summary_streams_typed_corpus(tmp_path: Path):
    corpus = tmp_path / "temporal_measurement.jsonl"
    corpus.write_text("".join(json.dumps(row) + "\n" for row in [
        {
            "schema": "fl4208.temporal_measurement.v2",
            "record_kind": "frame_summary",
            "frame": 1,
        },
        {
            "schema": "fl4208.temporal_measurement.v2",
            "record_kind": "cell",
            "frame": 1,
            "gate": "COLLISION",
            "scorer_kind_cur": 1,
            "scorer_version_cur": 2,
            "score_domain": "fl4512.shape6_f8_w4.v2.score_int.u32",
        },
    ]))
    program = """
const bridge = require(process.argv[1]);
bridge.summarizeTemporalMeasurement(process.argv[2], process.argv[3])
  .then((value) => process.stdout.write(JSON.stringify(value)))
  .catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", program, str(WEB_TEMPORAL_HELPER_PATH), str(corpus), str(ROOT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert summary["rows"] == 2
    assert summary["json_errors"] == 0
    assert summary["record_kind_counts"] == {"cell": 1, "frame_summary": 1}
    assert summary["gate_counts"] == {"COLLISION": 1}
    assert summary["scorer_identity_counts"] == {
        "1:2:fl4512.shape6_f8_w4.v2.score_int.u32": 1
    }


def test_runtime_marker_names_loaded_map_asset_and_concrete_mesh_instance():
    stdout = "\n".join(
        (
            "[FL-4731] mesh_instance_spawned index=0 "
            "mesh=pieta.akm faces=12064 uid=42",
            "[FL-4731] requested_map_loaded "
            "path=/repo/assets/a3d/frog_pieta_4x4.a3d world=1 "
            "mesh_assets=1 mesh_assets_loaded=1 mesh_assets_failed=0 "
            "mesh_instances=1",
        )
    )
    evidence = NATIVE_DRIVER.parse_fl4731_runtime_map_evidence(stdout)
    assert evidence["loaded_map"]["world_loaded"] is True
    assert evidence["loaded_map"]["mesh_assets_failed"] == 0
    assert evidence["mesh_instances"] == [
        {
            "index": 0,
            "mesh": "pieta.akm",
            "faces": 12064,
            "persistent_uid": 42,
        }
    ]


def test_mesh_render_evidence_requires_the_expected_live_profile_address():
    census = {
        "addresses": [
            {"material_namespace": 2, "material_key": 200, "count": 17},
            {"material_namespace": 1, "material_key": 200, "count": 999},
        ]
    }
    assert NATIVE_DRIVER.census_address_observations(census, 2, 200) == 17
    assert NATIVE_DRIVER.census_address_observations(census, 2, 201) == 0


def test_f9_is_the_single_clean_view_toggle_for_sprite_and_gameplay_ui():
    assert "bool clean_view;" in UI_STATE
    assert "ui.clean_view = !ui.clean_view;" in GAME_INPUT
    assert "RendererSetEditorHealthBarsHidden(ui.clean_view ? 1" in GAME_INPUT
    assert "SetInstFlags(player.player_inst, flags & ~INST_VISIBLE)" in GAME_INPUT
    assert "!ui.clean_view && ui.show_minimap" in GAME_RENDER_BRIDGE
    assert "if (!ui.clean_view)\n\t{" in GAME_RENDER_BRIDGE
    assert "!ui.clean_view && ui.show_cam_overlay" in GAME_RENDER_BRIDGE
    assert "player.player_inst && ui.clean_view" in GAME_RENDER_BRIDGE


def test_both_receipts_fail_closed_on_missing_runtime_mesh_evidence():
    for token in (
        "loaded_map_exact",
        "expected_mesh_instance_spawned",
        "expected_mesh_profile_address_observations",
        'runtime_spawn_gate["passed"]',
    ):
        assert token in NATIVE_DRIVER_TEXT
    for token in (
        "loaded_map_exact",
        "expected_mesh_instance_spawned",
        "expected_mesh_profile_address_observations",
        "fl4731RuntimeSpawnGate.passed",
        "FL-4731 loaded-map/mesh runtime spawn gate failed",
    ):
        assert token in WEB_DRIVER
    assert "fl4731RuntimeMessages" in PLAYWRIGHT_DRIVER
    assert "[FL-4731]" in PLAYWRIGHT_DRIVER


def test_web_pieta_proof_drives_the_product_f9_clean_view_control():
    assert "await page.keyboard.press('F9')" in WEB_DRIVER
    assert "clean_view_toggled_via_f9: cleanViewToggled" in WEB_DRIVER
    assert "{ keys: [], label: 'authored_view_hold' }" in WEB_DRIVER
    assert "[FL4731_FIXED_POSE_READY]" in WEB_DRIVER
    assert "awaitFixedPoseReview(checkpointPath)" in WEB_DRIVER
    assert "fl4731.fixed_pose_checkpoint.v1" in WEB_DRIVER


def test_web_pieta_performance_loop_does_not_export_full_runtime_observation():
    hold_keys = WEB_DRIVER.split("async function holdKeys", 1)[1].split(
        "function percentile", 1
    )[0]
    assert "Module.cwrap('ClientObservationJsonV1'" not in hold_keys
    assert "observation: {}" in hold_keys
    assert "readRuntimeState(page)" in WEB_DRIVER
    for phase in (
        "clean_view_key_begin",
        "scene_census_begin",
        "selector_census_begin",
        "structural_arm_begin",
        "hold_begin",
        "runtime_begin",
        "prediction_begin",
        "structural_snapshot_begin",
    ):
        assert phase in WEB_DRIVER
    assert "page.focus('canvas')" not in WEB_DRIVER
    assert "emscriptenEnvironment.FL4684_ACTIVE_STROKE_WEDGE_TRACE" in WEB_DRIVER


def test_web_pieta_structural_proof_turns_camera_without_translation():
    expected_mesh_legs = WEB_DRIVER.split(": EXPECTED_MESH ? [", 1)[1].split(
        "] : [", 1
    )[0]
    assert "{ keys: ['e'], label: 'turn_right'" in expected_mesh_legs
    assert "{ keys: ['q'], label: 'turn_left'" in expected_mesh_legs
    for movement_key in ("['w']", "['a']", "['s']", "['d']"):
        assert movement_key not in expected_mesh_legs
    assert "max_position_drift: maxPositionDrift" in WEB_DRIVER
    assert "Number(runtimeBehaviorGate.max_position_drift) <= 0.001" in WEB_DRIVER
    assert "return_turn_degrees: returnTurnDegrees" in WEB_DRIVER
    assert "'fixed_position_pieta_camera_turn'" in WEB_DRIVER


def test_web_prediction_reader_resolves_glyph_id_from_declared_row_schema():
    assert "rowFields.indexOf('glyph_id')" in WEB_DRIVER
    assert "runtime prediction snapshot row_fields lacks glyph_id" in WEB_DRIVER
    assert "row[glyphIdIndex]" in WEB_DRIVER
    assert "row[13]" not in WEB_DRIVER


def test_fixed_pose_checkpoint_uses_existing_oracle_before_turn_continuation():
    oracle = (ROOT / "scripts/fl4730_pieta_turn_oracle.py").read_text()
    assert "--verify-fixed" in oracle
    assert "verify_fixed_checkpoint" in oracle
    assert '"scene_fact_to_exact_prediction_join_complete"' in oracle
    assert '"runtime_exact_to_final_transition_census_matches"' in oracle
    assert '"complete_tuple_plane_gpu_byte_identical"' in oracle
    assert '"full_structural_carrier_and_joins_valid"' in oracle
    assert "_frame_state(structural_frame, structural_errors)" in oracle
    assert 'snapshot.get("exact_to_final_transition_count")' in oracle


def test_web_turn_fails_closed_on_per_frame_structural_conservation():
    assert "FL4512_STRUCTURAL_TURN_MEASURE_PATH" in WEB_DRIVER
    assert "Fl4512ArmStructuralTurnMeasurementV1" in WEB_DRIVER
    assert "structural turn measurement arm rejected" in WEB_DRIVER
    assert WEB_DRIVER.index("const selectorArmed") < WEB_DRIVER.index(
        "const structuralArm = await armStructuralTurnMeasurement(page)"
    ) < WEB_DRIVER.index("const legs = NO_INPUT_CONSUMPTION")
    assert "'--verify-structural-turn', '--receipt', receiptPath" in WEB_DRIVER
    assert "structural_turn_verification.json" in WEB_DRIVER
    assert "verificationExit !== 0 || !structuralVerification.passed" in WEB_DRIVER
    assert "FL-4512 per-frame snake/segment conservation gate failed" in WEB_DRIVER
    assert "server_sha256: manifest.server && manifest.server.sha256" in WEB_DRIVER


def test_structural_measurement_summary_streams_files_larger_than_node_strings():
    summary = WEB_DRIVER.split(
        "async function summarizeStructuralMeasurement", 1
    )[1].split("async function", 1)[0]
    assert "fs.createReadStream(pathname)" in summary
    assert "for await (const chunk of stream)" in summary
    assert "hash.update(chunk)" in summary
    assert "chunk[index] === 0x0a" in summary
    structural_receipt = WEB_DRIVER.split("let structuralMeasurement = null", 1)[1]
    assert "...await summarizeStructuralMeasurement(structuralMeasurementPath)" in structural_receipt
    assert "fs.readFileSync(structuralMeasurementPath)" not in structural_receipt


def test_server_bootstrap_uses_the_map_owned_player_start_tuple():
    assert "WorldGetPlayerStart(state->world, out_pos, &yaw, &dir)" in SERVER_TICK
    assert 'map_owned_spawn ? "a3d_player_start"' in SERVER_TICK
    assert "ps->spawn_pos, spawn_dir, spawn_yaw" in SERVER_TICK
    assert "PHYSICS_CREATE_EXACT_POS" in SERVER_TICK


def test_unrelated_maps_do_not_receive_fl4137_block_proof_seeds():
    seed_call = "SvrSeedLegacyBlockWorldItem(state, &cache)"
    seed_site = SERVER_TICK.index(seed_call, SERVER_TICK.index("void SvrInitWorldItems"))
    gate_site = SERVER_TICK.rindex(
        'getenv("ASCIICKER_SEED_TEST_BLOCKS")', 0, seed_site
    )
    assert gate_site < seed_site
    assert "env_seed_blocks[0] == '1'" in SERVER_TICK[gate_site:seed_site]


def test_web_census_can_arm_after_an_early_unarmed_render():
    arm = EMISSION_CENSUS.split(
        "static inline void Fl4684EmissionCensusArmV1(void)", 1
    )[1].split("// -----------------------------------------------------------------------------", 1)[0]
    assert "if (c->enabled)" in arm
    assert "if (c->armed_checked)" not in arm

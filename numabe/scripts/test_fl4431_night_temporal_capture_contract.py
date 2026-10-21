#!/usr/bin/env python3
"""Static and unit contracts for the FL-4431 airborne night review route.

WHAT THIS FILE DOES, AND WHAT IT DELIBERATELY DOES NOT
======================================================
It checks that the night "lighthouse beam" evidence contract agrees across
three files: the capture adapter that validates a run, the in-game driver that
writes the run's receipt, and the compositor host that supplies the underlying
numbers. Everything here is STATIC or UNIT level. No game runs, no picture is
taken, and no beam is ever looked at.

Two boundaries matter enough to state up front, because both have previously
been over-read:

1. REQUESTED OFFSET IS NOT MEASURED ORIENTATION.
   `beam_phase_offset_target` is the relative interval the analyzer expects
   after the first naturally rendered frame. `lighthouse_beam_azimuth` is the
   absolute orientation the runtime reported, while
   `beam_phase_offset_measured` is its wrapped difference from that arm's first
   observed azimuth. The proof never sets renderer phase. Nothing in this file
   may be quoted as proof that the beam visibly moved; only the PNG sequence can
   establish that.

2. WORLD TIME AND NIGHT STATE ARE NOT PROVEN HERE.
   `world_time` and `night_state` are COMMAND INPUTS the adapter passes down to
   the capture (see `_runtime_feature_csv` and the day-cycle argument). The
   driver never writes them back into its receipt, so the strict receipt
   validator never reads them. Night is therefore asserted by construction, not
   attested by the runtime. A run whose world clock drifted would not be caught
   by anything in this file.

Static agreement is not visual acceptance. Operator inspection of the real
frames remains the only acceptance surface.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py"
DRIVER_PATH = (
    ROOT
    / "godot_project"
    / "tools"
    / "proofs"
    / "run_fl4265_airplane_dogfight_proof.gd"
)
HOST_PATH = (
    ROOT
    / "godot_project"
    / "render" / "api" / "ASCIICompositorEffect.gd"
)
HEADED_WRAPPER_PATH = ROOT / "scripts" / "run_fl4359_headed_movement.py"
EXTERNAL_MOVEMENT_DRIVER_PATH = (
    ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"
)
MANIFEST_PATH = (
    ROOT
    / "docs"
    / "research"
    / "ascii"
    / "verification"
    / "fl4408"
    / "original-bug-lanes.manifest.json"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = _load_module("fl4431_night_adapter_contract", ADAPTER_PATH)
HEADED_WRAPPER = _load_module(
    "fl4431_night_headed_wrapper_contract",
    HEADED_WRAPPER_PATH,
)


def _gdscript_function_source(source: str, function_name: str) -> str:
    """Return exactly one top-level GDScript function for ownership assertions.

    Whole-file substring checks let a disconnected parser and receipt literal
    satisfy the same test. Keeping each assertion inside its owning function
    proves the command-line value follows the intended parser-to-receipt path.
    """
    marker = f"func {function_name}("
    start = source.index(marker)
    next_function = source.find("\nfunc ", start + len(marker))
    return source[start:] if next_function < 0 else source[start:next_function]


def _assert_tower_material_classifier_contract(source: str) -> None:
    """Pin the tower counter to b64's authored lighthouse material identity.

    ``source`` is the complete compositor GDScript. The helper extracts the
    sole Product evidence reader itself so constants elsewhere cannot satisfy
    the ownership check while the live counter still uses a position box.
    Assertions deliberately cover the numeric declarations, byte offset,
    static-owner gate, and final admission predicate. Mutation tests below
    damage each clause independently and require this helper to reject it.
    """
    readback_source = _gdscript_function_source(
        source,
        "_read_vsb_secondary_effect_contribution_receipt",
    )

    # The two numbers come from lighthouse_tower_faces.json's authored rows.
    # Naming each material prevents a bare numeric set from losing provenance.
    masonry_name = "FL4431_LIGHTHOUSE_TOWER_MASONRY_MATERIAL"
    lantern_name = "FL4431_LIGHTHOUSE_TOWER_LANTERN_FRAME_MATERIAL"
    assert f"const {masonry_name} := 203" in source
    assert f"const {lantern_name} := 204" in source
    assert masonry_name in readback_source
    assert lantern_name in readback_source

    # b64 material is the uint at byte 48. The tower increment must occur only
    # after the reader has admitted a static selected surface at that index.
    material_decode = "selected_surface_bytes.decode_u32(vsb_base + 48)"
    assert material_decode in readback_source
    static_admission_index = readback_source.index(
        "if owner_kind != VSB_OWNER_STATIC:"
    )
    material_decode_index = readback_source.index(material_decode)
    tower_increment_index = readback_source.index(
        "lighthouse_tower_selected += 1"
    )
    assert static_admission_index < material_decode_index < tower_increment_index

    # A retained material row is the whole classifier. Reintroducing position
    # bounds would let unrelated nearby static geometry claim tower identity.
    forbidden_box_path = (
        "FL4431_LIGHTHOUSE_TOWER_HALF_EXTENT_XZ",
        "FL4431_LIGHTHOUSE_TOWER_BELOW_LANTERN",
        "FL4431_LIGHTHOUSE_TOWER_ABOVE_LANTERN",
        "selected_world",
        "tower_offset",
        "_lighthouse_beam_anchor",
    )
    for forbidden in forbidden_box_path:
        assert forbidden not in readback_source


def _adapter_args(tmp_path: Path, side: str) -> Namespace:
    return Namespace(
        lane_id="night_lighthouse_beam",
        side=side,
        frame="beam_t0",
        capture_sequence=ADAPTER.STATIC_TEMPORAL_SEQUENCE,
        project=str(tmp_path / side / "godot_project"),
        colyseus_dir="",
        camera_mode="night_lighthouse_beam",
        view_mode="perspective",
        camera_position=json.dumps([183.1, 60.02, 25.35]),
        camera_yaw="1.5707959",
        camera_pitch="-0.5235997",
        camera_roll="0.0",
        camera_fov="60.0",
        projection_params=json.dumps({"near": 0.05, "far": 750.0}),
        player_position=json.dumps([79.1, 0.02, 25.35]),
        player_yaw="0.0",
        npc_positions="[]",
        airplane_position="[0.0, 0.0, 0.0]",
        airplane_yaw="0.0",
        world_time="22.5",
        night_state=True,
        feature_flags=json.dumps(
            ["ascii", "lighthouse_light", "local_light_volume", "bonfire_light"]
        ),
        beam_expected_in_png=True,
        fullscreen=True,
        resolution="3840x2160",
        move_frames=120,
        settle_frames=45,
        after_frames=6,
        plane_circle_frames=240,
        godot_timeout=900.0,
        max_jitter_pct=5.0,
        shared_current_server_identity={
            "server_commit": "1" * 40,
            "server_requested_root": str(tmp_path / "colyseus_server"),
            "server_source_root": str(tmp_path / "colyseus_server"),
            "server_dirty_paths": [],
            "server_runtime_scope_fingerprint": "2" * 64,
            "proof_tool_sha256": "3" * 64,
        },
    )


def test_tracked_manifest_matches_the_natural_time_phase_abi() -> None:
    """The v2-receipted lane authority uses route ticks and relative offsets."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    lane = next(item for item in manifest["lanes"] if item["id"] == "night_lighthouse_beam")
    assert lane["baseline"] == "baseline_a"
    assert lane["capture_sequence"] == ADAPTER.STATIC_TEMPORAL_SEQUENCE
    assert [frame["label"] for frame in lane["frames"]] == list(
        ADAPTER.STATIC_TEMPORAL_SEQUENCE_FRAMES
    )
    assert [frame["route_tick"] for frame in lane["frames"]] == list(
        ADAPTER.NIGHT_ROUTE_TARGET_TICKS
    )
    assert [frame["beam_phase_offset_target"] for frame in lane["frames"]] == list(
        ADAPTER.NIGHT_AIRBORNE_PHASE_OFFSETS
    )
    assert all("beam_phase_target" not in frame for frame in lane["frames"])
    assert "beam_phase_target" not in ADAPTER_PATH.read_text(encoding="utf-8")
    assert "bonfire_light" in lane["feature_flags"]
    assert all(frame["codebases"]["baseline"] == "baseline_a" for frame in lane["frames"])
    assert "source_commit" not in json.dumps(lane)


def test_adapter_converts_night_hour_and_limits_historical_bonfire_flag_to_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        run_dir = Path(command[command.index("--out") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        capture_side = command[command.index("--capture-side") + 1]
        receipt = _airborne_night_receipt(
            run_dir,
            capture_side=capture_side,
        )
        (run_dir / "receipt.json").write_text(
            json.dumps(receipt) + "\n",
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ADAPTER.subprocess, "run", fake_run)
    for side in ("baseline", "current"):
        args = _adapter_args(tmp_path, side)
        ADAPTER._run_capture(
            args,
            tmp_path / f"run-{side}",
            ADAPTER._capture_signature(args),
            f"signature-{side}",
        )
    baseline_command, current_command = commands
    assert baseline_command[baseline_command.index("--day-cycle-time") + 1] == "0.9375"
    assert current_command[current_command.index("--day-cycle-time") + 1] == "0.9375"
    assert baseline_command[baseline_command.index("--camera-mode") + 1] == "plane_air"
    assert current_command[current_command.index("--camera-mode") + 1] == "plane_air"
    assert str(ADAPTER.DOGFIGHT_DRIVER) in baseline_command
    assert str(ADAPTER.DOGFIGHT_DRIVER) in current_command
    assert "--exact-player-position" not in baseline_command
    assert "--exact-camera-position" not in current_command
    assert "--extra-user-arg=--fl4203-night-bonfire" in baseline_command
    assert "--extra-user-arg=--fl4203-night-bonfire" not in current_command
    assert not any("secondary-effect" in argument for argument in baseline_command)
    assert not any("secondary-effect" in argument for argument in current_command)


def test_temporal_receipt_rejects_stitched_and_missing_host_fact_rows(
    tmp_path: Path,
) -> None:
    valid = _airborne_night_receipt(tmp_path)
    assert (
        ADAPTER._validate_airborne_night_temporal_receipt(
            valid,
            tmp_path,
            expected_capture_side="current",
        )
        == []
    )
    stitched = json.loads(json.dumps(valid))
    stitched["visual_frame_samples"][1]["flight_intent_epoch"] = 18
    stitched["visual_frame_samples"][2]["lighthouse_record_published"] = False
    errors = ADAPTER._validate_airborne_night_temporal_receipt(
        stitched,
        tmp_path,
        expected_capture_side="current",
    )
    assert any("flight_intent_epoch differs" in error for error in errors)
    assert any("kind-4" in error for error in errors)


def test_adapter_never_promotes_manifest_expectation_to_runtime_evidence(tmp_path: Path) -> None:
    args = _adapter_args(tmp_path, "current")
    run_dir = tmp_path / "metadata-run"
    run_dir.mkdir()
    receipt = _airborne_night_receipt(run_dir)
    source = Path(receipt["visual_frame_samples"][0]["path"])
    (run_dir / "receipt.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )
    (run_dir / "complete_capture.json").write_text("{}\n", encoding="utf-8")
    metadata = ADAPTER._metadata(
        args,
        run_dir,
        source,
        (16, 16),
        "signature",
        "beam_t0",
        {"passed": True},
    )
    assert args.beam_expected_in_png is True
    assert metadata["lighthouse_beam_contributed_subcell_count"] == 7
    assert metadata["png_captured_in_same_process"] is True
    assert metadata["route_tick"] == 30
    assert metadata["lighthouse_record_published"] is True
    assert metadata["bonfire_record_published"] is True


def test_adapter_has_one_airborne_server_route_capture_path() -> None:
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert 'camera_mode == "night_lighthouse_beam"' in source
    assert 'camera_mode = "plane_air"' in source
    assert "run_fl4265_airplane_dogfight_proof.gd" in source
    assert "fl4359_external_movement_driver.gd" in source
    assert "if night_airborne_capture:" in source
    assert "--fl4408-static-temporal-sequence" not in source
    assert "--fl4431-secondary-effect-evidence" not in source


def test_capture_side_command_line_value_reaches_night_airborne_receipt() -> None:
    """The immutable side label is parsed, validated, then serialized unchanged."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    init_source = _gdscript_function_source(source, "_init")
    run_source = _gdscript_function_source(source, "_run")
    capture_source = _gdscript_function_source(
        source,
        "_capture_fl4359_perspective_flight",
    )

    assert 'arg.begins_with("--fl4408-capture-side=")' in init_source
    assert 'arg.trim_prefix("--fl4408-capture-side=")' in init_source
    assert '"baseline"' in init_source
    assert '"current"' in init_source
    assert "fl4408_capture_side_parse_error" in init_source
    assert "fl4408_capture_side_parse_error" in run_source
    assert '"capture_side": fl4408_capture_side' in capture_source
    assert (
        '"night_airborne_temporal_sequence": '
        "night_temporal_complete"
    ) in capture_source
    # Inside the runtime capture owner the side variable appears exactly once:
    # as the serialized value. Any conditional use here would let provenance
    # choose route, visuals, timing, evidence counts, or artifact paths.
    assert capture_source.count("fl4408_capture_side") == 1
    assert "if fl4408_capture_side" not in capture_source


def test_driver_parses_exact_night_labels_and_phase_offsets() -> None:
    """The adapter's three labels and relative offsets reach strict parsers."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    init_source = _gdscript_function_source(source, "_init")
    argument_check_source = _gdscript_function_source(
        source,
        "_fl4431_night_temporal_argument_error",
    )

    assert 'arg.begins_with("--fl4431-night-temporal-labels=")' in init_source
    assert 'arg.trim_prefix("--fl4431-night-temporal-labels=")' in init_source
    assert 'arg.begins_with("--fl4431-night-temporal-phase-offsets=")' in init_source
    assert '"--fl4431-night-temporal-phase-offsets="' in init_source
    assert "var raw_offsets := arg.trim_prefix(" in init_source
    assert "FL4431_NIGHT_TEMPORAL_LABELS" in argument_check_source
    assert "FL4431_NIGHT_TEMPORAL_PHASE_OFFSETS" in argument_check_source
    assert "fl4431_night_temporal_labels" in argument_check_source
    assert "fl4431_night_temporal_phase_offsets" in argument_check_source
    assert "beam_phase_target" not in source
    assert "night-temporal-beam-phases" not in source


def test_directed_phase_offset_wraps_across_zero_without_reversing_sign() -> None:
    """The executable analyzer law and GDScript source use current minus first.

    A first angle just below one turn followed by a small positive angle is the
    seam case that exposed signed-argument ambiguity. The result must be the
    small forward interval, while a genuine two-thirds-turn interval must stay
    two thirds of a turn rather than collapse to its shortest signed opposite.
    """
    tau = 2.0 * 3.141592653589793
    assert ADAPTER._wrapped_positive_angle_delta(tau - 0.1, 0.2) == pytest.approx(
        0.3
    )
    assert ADAPTER._wrapped_positive_angle_delta(1.25, 1.25 + tau * 2.0 / 3.0) == (
        pytest.approx(tau * 2.0 / 3.0)
    )
    assert ADAPTER._wrapped_positive_angle_delta(1.25, 1.25) == 0.0

    source = DRIVER_PATH.read_text(encoding="utf-8")
    helper_source = _gdscript_function_source(
        source,
        "_fl4431_positive_phase_offset",
    )
    assert "fposmod(observed_azimuth - first_azimuth, TAU)" in helper_source
    assert "angle_difference(" not in helper_source


def test_driver_enriches_each_captured_frame_before_receipt_admission() -> None:
    """Generic pose rows cannot substitute for direct same-frame night truth."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    capture_source = _gdscript_function_source(
        source,
        "_capture_fl4359_perspective_flight",
    )
    frame_source = _gdscript_function_source(
        source,
        "_fl4431_night_temporal_frame_fields",
    )
    receipt_check_source = _gdscript_function_source(
        source,
        "_fl4431_night_temporal_samples_complete",
    )

    assert "_fl4431_night_temporal_frame_fields(" in capture_source
    assert "visual_sample.merge(" in capture_source
    assert capture_source.index("visual_sample.merge(") < capture_source.index(
        "visual_frame_samples.append(visual_sample)"
    )
    assert (
        '"night_airborne_temporal_sequence": '
        "fl4431_night_airborne_temporal_sequence"
    ) not in capture_source
    assert (
        '"night_airborne_temporal_sequence": night_temporal_complete'
        in capture_source
    )
    assert "_fl4431_night_temporal_samples_complete(" in capture_source
    assert '"visual_capture_owner": (' in capture_source
    assert "FL4431_NIGHT_CAPTURE_OWNER" in capture_source
    assert '"temporal_capture_id": temporal_capture_id' in capture_source

    required_frame_fields = {
        '"label"',
        '"frame_index"',
        '"temporal_capture_id"',
        '"png_capture_owner"',
        '"png_captured_in_same_process"',
        '"airborne_airplane_ready"',
        '"beam_phase_offset_target"',
        '"beam_phase_offset_measured"',
        '"lighthouse_beam_azimuth"',
        '"lighthouse_record_published"',
        '"bonfire_record_published"',
        *{f'"{field}"' for field in ADAPTER.NIGHT_PRODUCT_COUNT_FIELDS},
    }
    for field_literal in required_frame_fields:
        assert field_literal in frame_source
        assert field_literal in receipt_check_source


def test_driver_reads_current_counts_from_one_product_snapshot() -> None:
    """Current count fields come from Product readback; Baseline A stays null."""
    source = DRIVER_PATH.read_text(encoding="utf-8")
    frame_source = _gdscript_function_source(
        source,
        "_fl4431_night_temporal_frame_fields",
    )

    assert 'effect_object.has_method("snapshot_fl4431_night_product_receipt")' in (
        frame_source
    )
    assert 'effect_object.call(' in frame_source
    assert '"snapshot_fl4431_night_product_receipt"' in frame_source
    assert "FL4431_NIGHT_PRODUCT_COUNT_SOURCE_FIELDS" in frame_source
    assert "null" in frame_source
    assert "Image" not in frame_source
    assert "get_pixel" not in frame_source
    assert "fl4408_capture_side" not in frame_source


def test_night_snapshot_owns_one_exact_b64_b55_selected_surface_join() -> None:
    """Night counts require matching VSB identity and admitted Product rows."""
    source = HOST_PATH.read_text(encoding="utf-8")
    snapshot_source = _gdscript_function_source(
        source,
        "snapshot_fl4431_night_product_receipt",
    )
    readback_source = _gdscript_function_source(
        source,
        "_read_vsb_secondary_effect_contribution_receipt",
    )

    assert "return snapshot_fl4431_vapor_product_receipt()" not in snapshot_source
    assert '"b64_rid"' in readback_source
    assert '"final_product_rid"' in readback_source
    assert "_vsb_view_receipt" in readback_source
    assert "VSB_VISIBLE_SURFACE_RECORD_BYTES" in readback_source
    assert "VSB_FINAL_PRODUCT_RECORD_BYTES" in readback_source
    assert "record_index" in readback_source
    assert "vsb_status" in readback_source
    assert "product_status" in readback_source
    assert "FINAL_PRODUCT_STATUS_VALID" in readback_source
    assert "VSB_STATUS_VALID" in readback_source
    assert "buffer_get_data" in readback_source
    assert "Image" not in readback_source
    assert "get_pixel" not in readback_source


def test_night_snapshot_classifies_selected_identity_without_population_substitute() -> None:
    """Tree and tower facts must use selected identity, never scene population."""
    source = HOST_PATH.read_text(encoding="utf-8")
    readback_source = _gdscript_function_source(
        source,
        "_read_vsb_secondary_effect_contribution_receipt",
    )
    snapshot_source = _gdscript_function_source(
        source,
        "snapshot_fl4431_night_product_receipt",
    )

    assert "object_id" in readback_source
    assert "primitive_id" in readback_source
    _assert_tower_material_classifier_contract(source)
    forbidden_substitutes = (
        "_static_world_mesh_face_rows",
        "retained_static_face_total",
        "face_count",
        "capture_pixel_camera_viewport_image",
        "get_pixel",
    )
    for forbidden in forbidden_substitutes:
        assert forbidden not in readback_source
        assert forbidden not in snapshot_source


def test_night_tower_material_classifier_rejects_identity_mutants() -> None:
    """Removing an authored id and reviving a position owner must fail red.

    These source mutants are intentionally never written to disk. Each string
    changes one ownership law while leaving the surrounding b64/b55 reader
    intact, then proves the focused contract detects that exact regression.
    """
    source = HOST_PATH.read_text(encoding="utf-8")
    masonry_name = "FL4431_LIGHTHOUSE_TOWER_MASONRY_MATERIAL"
    lantern_name = "FL4431_LIGHTHOUSE_TOWER_LANTERN_FRAME_MATERIAL"
    mutations = {
        "masonry_id_removed": source.replace(
            f"const {masonry_name} := 203",
            f"const {masonry_name} := 999",
            1,
        ),
        "lantern_id_removed": source.replace(
            f"const {lantern_name} := 204",
            f"const {lantern_name} := 999",
            1,
        ),
        "position_only_admission": source.replace(
            (
                "selected_material == "
                f"{masonry_name}\n"
                "\t\t\tor selected_material == "
                f"{lantern_name}"
            ),
            "selected_world.y > _lighthouse_beam_anchor.y",
            1,
        ),
    }
    for label, mutated_source in mutations.items():
        assert mutated_source != source, f"mutation did not alter source: {label}"
        with pytest.raises(AssertionError):
            _assert_tower_material_classifier_contract(mutated_source)


def test_night_snapshot_nulls_every_count_with_named_absence_reason() -> None:
    """Disabled evidence and invalid banks cannot fabricate measured zeros."""
    source = HOST_PATH.read_text(encoding="utf-8")
    snapshot_source = _gdscript_function_source(
        source,
        "snapshot_fl4431_night_product_receipt",
    )

    required_source_fields = tuple(
        f'"product_{field}"' for field in ADAPTER.NIGHT_PRODUCT_COUNT_FIELDS
    )
    for source_field in required_source_fields:
        assert source_field in snapshot_source
    assert '"absence_reason"' in snapshot_source
    assert "null" in snapshot_source
    assert "_fl4431_secondary_effect_evidence_enabled" in snapshot_source
    assert "_fl4431_product_evidence_receipts_by_view" in snapshot_source


def test_airborne_temporal_capture_only_skip_rejects_grounded_receipts(
    tmp_path: Path,
) -> None:
    summary = {
        "analysis_skipped": True,
        "reason": "canonical_png_grid_capture_only",
    }
    receipt = _airborne_night_receipt(tmp_path)
    assert HEADED_WRAPPER._night_airborne_temporal_capture_accepts_analysis_skip(
        summary,
        receipt,
        True,
    )
    grounded = json.loads(json.dumps(receipt))
    grounded["visual_frame_samples"][0]["airplane_position"]["y"] = 0.0
    assert not HEADED_WRAPPER._night_airborne_temporal_capture_accepts_analysis_skip(
        summary,
        grounded,
        True,
    )
    assert not HEADED_WRAPPER._night_airborne_temporal_capture_accepts_analysis_skip(
        {"analysis_skipped": True, "reason": "unrelated_skip"},
        receipt,
        True,
    )


def test_adapter_structural_counts_never_claim_visible_pixels() -> None:
    """The adapter reports structure while the PNG grid owns visual review."""
    sources = ADAPTER_PATH.read_text(encoding="utf-8")
    forbidden_runtime_claims = (
        "FINAL_EFFECT_PIXEL_EVIDENCE_SOURCE",
        '"beam_shaft_in_screen"',
        '"beam_pixel_evidence_source"',
        '"bonfire_pixels_in_screen"',
        '"bonfire_pixel_evidence_source"',
        '"final_effect_pixel_evidence_state"',
    )
    for forbidden in forbidden_runtime_claims:
        assert forbidden not in sources
    stale_receipt_claims = (
        '"secondary_effect_applied_receipt"',
        "fl4431.secondary_effect_product_snapshot.v1",
        "product_applied_bits_readback_ok",
        "_visible_receipt_available",
        "_applied_subcell_count",
    )
    for stale_claim in stale_receipt_claims:
        assert stale_claim not in sources
    assert "Positive counts are structural product truth" in sources
    assert "operator inspection remains the sole visible acceptance surface" in sources


def _airborne_night_receipt(
    tmp_path: Path,
    *,
    capture_side: str = "current",
) -> dict:
    """Build the strict server-route receipt expected from the dogfight driver.

    This fixture deliberately includes every ownership fact named by the
    operator. Removing any one field must make the adapter fail closed instead
    of quietly accepting a plausible-looking PNG sequence.
    """
    capture_id = "same-process-night-capture"
    route_ticks = [30, 150, 270]
    frames = []
    for index, (label, route_tick, phase) in enumerate(
        zip(
            ADAPTER.STATIC_TEMPORAL_SEQUENCE_FRAMES,
            route_ticks,
            ADAPTER.STATIC_TEMPORAL_PHASE_OFFSETS,
        )
    ):
        png_path = tmp_path / f"fl4359_perspective_frame_{route_tick:03d}.png"
        Image.new("RGB", (16, 16), (20 + index, 40, 60)).save(png_path)
        frame = {
            "label": label,
            "frame_index": index,
            "route_tick": route_tick,
            "authority_server_tick": 400 + route_tick,
            "flight_intent_epoch": 17,
            "last_applied_flight_intent_tick": 400,
            "temporal_capture_id": capture_id,
            "png_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
            "png_captured_in_same_process": True,
            "path": str(png_path),
            "airborne_airplane_ready": True,
            "player_position": {"x": 1.0, "y": 45.0, "z": 2.0},
            "camera_position": {"x": 4.0, "y": 52.0, "z": 8.0},
            "airplane_position": {"x": 3.0 + index, "y": 45.0, "z": 7.0},
            "airplane_yaw": 0.2 + index * 0.1,
            "beam_phase_offset_target": phase,
            "beam_phase_offset_measured": phase,
            # Give each arm an arbitrary absolute starting angle. Only the
            # wrapped offsets are cross-arm comparable.
            "lighthouse_beam_azimuth": (1.25 + phase) % (2.0 * 3.141592653589793),
            "lighthouse_record_published": True,
            "bonfire_record_published": True,
            "firefly_record_count": 2,
            "firefly_record_published": True,
            "product_frame_time_seconds": 120.0 + index,
        }
        current_counts = (7, 3, 5, 2, 11, 80, 24, 19)
        for count_key, current_count in zip(
            ADAPTER.NIGHT_PRODUCT_COUNT_FIELDS,
            current_counts,
        ):
            # Baseline A predates this readback ABI. Its fixture therefore
            # carries an explicit JSON null for every unavailable field.
            frame[count_key] = (
                current_count if capture_side == "current" else None
            )
        frames.append(frame)
    return {
        "pass": True,
        "capture_side": capture_side,
        "night_airborne_temporal_sequence": True,
        "temporal_capture_id": capture_id,
        "visual_capture_owner": "run_fl4265_airplane_dogfight_proof.gd",
        "route_identity": "server_tick_constant_physical_flight_input",
        "flight_intent_epoch": 17,
        "last_applied_flight_intent_tick": 400,
        "server_tick_rate_hz": 30.0,
        "airborne_airplane_ready": True,
        "local_airplane_pilot": True,
        "airplane_world_item_count_exact": True,
        "max_airplane_world_item_count": 2,
        "clean_play_no_capture": False,
        "visual_route_target_ticks": route_ticks,
        "visual_route_ticks_exact": True,
        "missing_visual_route_ticks": [],
        "visual_frame_count": 3,
        "visual_frame_paths": [frame["path"] for frame in frames],
        "visual_frame_samples": frames,
    }


def test_baseline_abi_red_first_explicit_null_counts_pass(tmp_path: Path) -> None:
    """Receipt-bound Baseline A may state ABI absence only with null values."""
    baseline = _airborne_night_receipt(tmp_path, capture_side="baseline")
    assert (
        ADAPTER._validate_airborne_night_temporal_receipt(
            baseline,
            tmp_path,
            expected_capture_side="baseline",
        )
        == []
    )


def test_baseline_abi_red_first_missing_count_key_fails(tmp_path: Path) -> None:
    """An absent key is ambiguous and cannot stand in for explicit null."""
    baseline = _airborne_night_receipt(tmp_path, capture_side="baseline")
    baseline["visual_frame_samples"][1].pop("water_selected_subcell_count")
    errors = ADAPTER._validate_airborne_night_temporal_receipt(
        baseline,
        tmp_path,
        expected_capture_side="baseline",
    )
    assert any(
        "water_selected_subcell_count is missing for Baseline A" in error
        for error in errors
    )


def test_baseline_abi_red_first_numeric_count_fails(tmp_path: Path) -> None:
    """Baseline A must not fabricate zero-valued or positive readback data."""
    baseline = _airborne_night_receipt(tmp_path, capture_side="baseline")
    baseline["visual_frame_samples"][0][
        "lighthouse_beam_contributed_subcell_count"
    ] = 0
    baseline["visual_frame_samples"][2]["terrain_selected_subcell_count"] = 9
    errors = ADAPTER._validate_airborne_night_temporal_receipt(
        baseline,
        tmp_path,
        expected_capture_side="baseline",
    )
    assert sum("must be explicit null for Baseline A" in error for error in errors) == 2


def test_baseline_abi_red_first_current_null_and_zero_fail(tmp_path: Path) -> None:
    """Current owns the live ABI and therefore needs positive direct counts."""
    current = _airborne_night_receipt(tmp_path, capture_side="current")
    current["visual_frame_samples"][0][
        "bonfire_smoke_contributed_subcell_count"
    ] = None
    current["visual_frame_samples"][1]["water_selected_subcell_count"] = 0
    errors = ADAPTER._validate_airborne_night_temporal_receipt(
        current,
        tmp_path,
        expected_capture_side="current",
    )
    assert any(
        "bonfire_smoke_contributed_subcell_count is absent or zero for current"
        in error
        for error in errors
    )
    assert any(
        "water_selected_subcell_count is absent or zero for current" in error
        for error in errors
    )


def test_baseline_abi_red_first_side_label_spoof_fails(tmp_path: Path) -> None:
    """Caller identity wins; a receipt label cannot select the weaker ABI rule."""
    spoofed = _airborne_night_receipt(tmp_path, capture_side="baseline")
    spoofed["capture_side"] = "current"
    errors = ADAPTER._validate_airborne_night_temporal_receipt(
        spoofed,
        tmp_path,
        expected_capture_side="baseline",
    )
    assert "receipt capture_side=current does not match expected baseline" in errors
    assert not any("absent or zero for current" in error for error in errors)


def test_red_first_airborne_receipt_rejects_grounded_and_missing_product_facts(
    tmp_path: Path,
) -> None:
    """The replacement validator must reject both stale-owner failure shapes."""
    valid = _airborne_night_receipt(tmp_path)
    assert (
        ADAPTER._validate_airborne_night_temporal_receipt(
            valid,
            tmp_path,
            expected_capture_side="current",
        )
        == []
    )

    grounded = json.loads(json.dumps(valid))
    grounded["visual_frame_samples"][1]["airplane_position"]["y"] = 0.0
    grounded_errors = ADAPTER._validate_airborne_night_temporal_receipt(
        grounded,
        tmp_path,
        expected_capture_side="current",
    )
    assert any("airborne airplane position" in error for error in grounded_errors)

    missing_count = json.loads(json.dumps(valid))
    missing_count["visual_frame_samples"][2].pop("terrain_selected_subcell_count")
    missing_count_errors = ADAPTER._validate_airborne_night_temporal_receipt(
        missing_count,
        tmp_path,
        expected_capture_side="current",
    )
    assert any("terrain_selected_subcell_count" in error for error in missing_count_errors)

    repeated_beam = json.loads(json.dumps(valid))
    repeated_beam["visual_frame_samples"][1]["lighthouse_beam_azimuth"] = (
        repeated_beam["visual_frame_samples"][0]["lighthouse_beam_azimuth"]
    )
    repeated_beam_errors = ADAPTER._validate_airborne_night_temporal_receipt(
        repeated_beam,
        tmp_path,
        expected_capture_side="current",
    )
    assert any(
        "measured phase offset does not match" in error
        for error in repeated_beam_errors
    )

    detached_png = json.loads(json.dumps(valid))
    detached_png["visual_frame_samples"][2]["png_captured_in_same_process"] = False
    detached_png_errors = ADAPTER._validate_airborne_night_temporal_receipt(
        detached_png,
        tmp_path,
        expected_capture_side="current",
    )
    assert any("same-process PNG truth" in error for error in detached_png_errors)


def test_temporal_receipt_requires_product_clock_and_firefly_facts(
    tmp_path: Path,
) -> None:
    """Each same-process night row must carry advancing Product/firefly truth."""

    mutations = (
        (
            "missing_product_time",
            lambda frames: frames[0].pop("product_frame_time_seconds"),
            "product_frame_time_seconds is absent or nonfinite",
        ),
        (
            "null_product_time",
            lambda frames: frames[0].__setitem__(
                "product_frame_time_seconds", None
            ),
            "product_frame_time_seconds is absent or nonfinite",
        ),
        (
            "nan_product_time",
            lambda frames: frames[0].__setitem__(
                "product_frame_time_seconds", float("nan")
            ),
            "product_frame_time_seconds is absent or nonfinite",
        ),
        (
            "infinite_product_time",
            lambda frames: frames[0].__setitem__(
                "product_frame_time_seconds", float("inf")
            ),
            "product_frame_time_seconds is absent or nonfinite",
        ),
        (
            "non_increasing_product_time",
            lambda frames: frames[1].__setitem__(
                "product_frame_time_seconds",
                frames[0]["product_frame_time_seconds"],
            ),
            "product_frame_time_seconds is not strictly increasing",
        ),
        (
            "missing_firefly_publication",
            lambda frames: frames[0].pop("firefly_record_published"),
            "firefly kind-2 record is unproven",
        ),
        (
            "false_firefly_publication",
            lambda frames: frames[0].__setitem__(
                "firefly_record_published", False
            ),
            "firefly kind-2 record is unproven",
        ),
        (
            "zero_firefly_count",
            lambda frames: frames[0].__setitem__("firefly_record_count", 0),
            "firefly_record_count is absent or nonpositive",
        ),
        (
            "negative_firefly_count",
            lambda frames: frames[0].__setitem__("firefly_record_count", -1),
            "firefly_record_count is absent or nonpositive",
        ),
        (
            "noninteger_firefly_count",
            lambda frames: frames[0].__setitem__("firefly_record_count", 1.5),
            "firefly_record_count is absent or nonpositive",
        ),
    )
    for _name, mutate, expected_error in mutations:
        damaged = _valid_receipt(tmp_path)
        mutate(damaged["visual_frame_samples"])
        errors = _errors_for(damaged, tmp_path)
        assert any(expected_error in error for error in errors), _name


def test_temporal_validator_source_names_live_product_and_firefly_fields() -> None:
    """The adapter source must own both new same-run receipt checks."""

    source = ADAPTER_PATH.read_text(encoding="utf-8")
    start = source.index("def _validate_airborne_night_temporal_receipt(")
    end = source.index("\ndef _run_capture(", start)
    validator_source = source[start:end]
    assert 'frame.get("firefly_record_published")' in validator_source
    assert 'frame.get("firefly_record_count")' in validator_source
    assert 'frame.get("product_frame_time_seconds")' in validator_source
    assert "observed_product_frame_times" in validator_source
    assert "product_frame_time_value <= observed_product_frame_times[-1]" in (
        validator_source
    )


# =============================================================================
# FL-4431 route-integrity clauses: seven checks that previously had no mutation
# =============================================================================
# The adapter's strict validator contains clauses that nothing exercised. A
# clause with no destructive test is decoration: it looks protective, and no one
# can tell whether it would actually catch the defect it names.
#
# Each test below does two things:
#   1. damages exactly one fact in an otherwise valid receipt and asserts the
#      adapter emits the SPECIFIC named rejection, and
#   2. re-runs the same damage against a copy of the adapter with that one
#      clause deleted, and asserts the damage then goes UNNOTICED.
#
# Step 2 is what proves the assertion has teeth. Without it, a test can pass
# because some unrelated clause happened to reject the receipt, and the clause
# under test could be dead code.


def _validator_without_clause(
    tmp_path: Path, clause_source: str, label: str
) -> object:
    """Import a private copy of the adapter with one rejection clause removed.

    `clause_source` is the exact `errors.append(...)` line to delete. It is
    replaced with `pass` so the surrounding `if` block stays syntactically
    valid while losing its ability to record a failure.

    The real repository file is never modified: the text is read, altered in
    memory, and written into the test's own temporary directory. `label` only
    keeps the temporary module names distinct.
    """
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert clause_source in source, (
        f"clause to weaken is not present verbatim in the adapter: {clause_source}"
    )
    weakened_source = source.replace(clause_source, "pass", 1)
    weakened_path = tmp_path / f"weakened_adapter_{label}.py"
    weakened_path.write_text(weakened_source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        f"fl4408_weakened_adapter_{label}", weakened_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _errors_for(receipt: dict, tmp_path: Path, module: object = None) -> list[str]:
    """Run the strict night validator over one receipt and return its errors."""
    validator = (module or ADAPTER)._validate_airborne_night_temporal_receipt
    return validator(receipt, tmp_path, expected_capture_side="current")


def _valid_receipt(tmp_path: Path) -> dict:
    """A receipt the strict validator accepts, as the baseline for damage."""
    receipt = _airborne_night_receipt(tmp_path)
    assert _errors_for(receipt, tmp_path) == [], (
        "the undamaged fixture must be accepted before any mutation is judged"
    )
    return json.loads(json.dumps(receipt))


def test_route_tick_law_rejects_a_server_tick_that_does_not_resolve(
    tmp_path: Path,
) -> None:
    """`authority_server_tick - applied_tick` must equal `route_tick`.

    This subtraction is what ties a picture to one authoritative server moment.
    If it does not hold, the frame was taken at a different instant than the one
    it claims, and two arms could be compared at mismatched times while every
    other field still looked correct.
    """
    damaged = _valid_receipt(tmp_path)
    # Shift only the absolute server tick. Route tick and applied tick are left
    # alone, so this isolates the subtraction law itself.
    damaged["visual_frame_samples"][1]["authority_server_tick"] += 1
    errors = _errors_for(damaged, tmp_path)
    assert "beam_t1 authority_server_tick does not resolve to route_tick" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(f"{label} authority_server_tick does not resolve to route_tick")',
        "route_tick_law",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the route-tick clause must let the damaged receipt through, "
        "proving that clause is what catches it"
    )


def test_declared_frame_paths_must_match_the_same_process_rows(
    tmp_path: Path,
) -> None:
    """`visual_frame_paths` must list exactly the per-row `path` values.

    The declared list is what downstream tooling reads to find the pictures. If
    it can disagree with the rows, a package could advertise one set of images
    while its provenance rows describe another.
    """
    damaged = _valid_receipt(tmp_path)
    damaged["visual_frame_paths"] = list(reversed(damaged["visual_frame_paths"]))
    errors = _errors_for(damaged, tmp_path)
    assert "visual_frame_paths do not match the same-process frame rows" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append("visual_frame_paths do not match the same-process frame rows")',
        "frame_paths",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the declared-path clause must let the reordered list through"
    )


def test_per_frame_capture_id_must_match_the_run(tmp_path: Path) -> None:
    """Every frame must carry the run's own `temporal_capture_id`.

    This is the anti-stitching field. Without it, three pictures from three
    different processes could be assembled into one plausible-looking sequence.
    """
    damaged = _valid_receipt(tmp_path)
    damaged["visual_frame_samples"][2]["temporal_capture_id"] = "a-different-run"
    errors = _errors_for(damaged, tmp_path)
    assert "beam_t2 temporal_capture_id differs from the run" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(f"{label} temporal_capture_id differs from the run")',
        "capture_id",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the capture-id clause must let a stitched frame through"
    )


def test_per_frame_applied_tick_must_match_the_receipt(tmp_path: Path) -> None:
    """Each frame's applied flight-intent tick must equal the route's.

    The applied tick is the server moment the route's flight intent was
    admitted. A frame reporting a different one belongs to a different route.

    Isolation note, worth stating because it is easy to get backwards: the
    route-tick subtraction law is anchored on the RECEIPT-level applied tick,
    not the per-frame one. So shifting only the frame's applied tick leaves the
    subtraction balanced and trips this clause alone. Shifting the frame's
    server tick to "compensate" would instead break the subtraction, because
    the subtraction never reads the frame's applied tick at all.
    """
    damaged = _valid_receipt(tmp_path)
    frame = damaged["visual_frame_samples"][0]
    frame["last_applied_flight_intent_tick"] += 1
    errors = _errors_for(damaged, tmp_path)
    assert "beam_t0 applied flight-intent tick differs from the route" in errors
    assert "beam_t0 authority_server_tick does not resolve to route_tick" not in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(f"{label} applied flight-intent tick differs from the route")',
        "applied_tick",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the applied-tick clause must let the off-route frame through"
    )


def test_frame_label_order_must_be_exact(tmp_path: Path) -> None:
    """Row N must carry label `beam_tN`.

    Labels select which grid cell a picture lands in. Swapped labels put the
    right images in the wrong places, which reads as a beam that jumped.
    """
    damaged = _valid_receipt(tmp_path)
    rows = damaged["visual_frame_samples"]
    rows[0]["label"], rows[1]["label"] = rows[1]["label"], rows[0]["label"]
    errors = _errors_for(damaged, tmp_path)
    assert "beam_t0 visual row order is wrong" in errors
    assert "beam_t1 visual row order is wrong" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(f"{label} visual row order is wrong")',
        "label_order",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the row-order clause must let swapped labels through"
    )


def test_frame_index_order_must_be_exact(tmp_path: Path) -> None:
    """Row N must carry `frame_index` N.

    `frame_index` is the second, independent statement of position. Checking it
    beside the label means a receipt cannot renumber itself into a different
    temporal order while keeping its labels tidy.
    """
    damaged = _valid_receipt(tmp_path)
    damaged["visual_frame_samples"][2]["frame_index"] = 0
    errors = _errors_for(damaged, tmp_path)
    assert "beam_t2 visual row order is wrong" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(f"{label} visual row order is wrong")',
        "frame_index_order",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the row-order clause must let a renumbered row through"
    )


def test_route_target_ticks_must_follow_the_120_tick_natural_phase_stride(
    tmp_path: Path,
) -> None:
    """The route must capture exactly ticks 30, 150, and 270.

    These are the server moments the run promised to photograph. A repeated or
    out-of-order value means the three pictures do not span three distinct
    moments, so the sequence cannot show the beam moving.

    Note on cascade: when this list is rejected the validator clears it, which
    then makes every per-frame `route_tick` comparison fail as well. That is the
    validator's own fail-closed design, not an artefact of this test, so the
    assertions below name the specific rejection rather than requiring it to be
    the only one.
    """
    duplicated = _valid_receipt(tmp_path)
    duplicated["visual_route_target_ticks"] = [30, 30, 270]
    # Make the second frame genuinely claim the repeated moment: both its route
    # tick and its absolute server tick move together, so the subtraction law
    # still balances and only the duplicate-tick clause can object. This is the
    # realistic shape of the defect — a run that photographed one instant twice
    # and still looks internally consistent everywhere else.
    duplicated["visual_frame_samples"][1]["route_tick"] = 30
    duplicated["visual_frame_samples"][1]["authority_server_tick"] = 430
    errors = _errors_for(duplicated, tmp_path)
    assert (
        "visual_route_target_ticks must be exactly 30, 150, 270"
        in errors
    )

    descending = _valid_receipt(tmp_path)
    descending["visual_route_target_ticks"] = [270, 150, 30]
    descending_errors = _errors_for(descending, tmp_path)
    assert (
        "visual_route_target_ticks must be exactly 30, 150, 270"
        in descending_errors
    )

    # Weakening this clause needs BOTH of its lines removed, not just the
    # error. The validator also clears `target_ticks` in the same block, and
    # that clearing alone would keep every per-frame comparison failing — so
    # deleting only the message would prove nothing about the clause.
    weakened = _validator_without_clause(
        tmp_path,
        'errors.append(\n'
        '            "visual_route_target_ticks must be exactly 30, 150, 270"\n'
        "        )\n"
        "        target_ticks = []",
        "target_ticks",
    )
    assert _errors_for(duplicated, tmp_path, weakened) == [], (
        "removing the target-tick clause must let two frames claim one moment"
    )


def test_server_tick_rate_must_match_the_natural_phase_stride(
    tmp_path: Path,
) -> None:
    """The 120-tick interval means four seconds only on the 30 Hz server."""
    damaged = _valid_receipt(tmp_path)
    damaged["server_tick_rate_hz"] = 60.0
    errors = _errors_for(damaged, tmp_path)
    assert "server_tick_rate_hz must be exactly 30 for phase spacing" in errors

    weakened = _validator_without_clause(
        tmp_path,
        'errors.append("server_tick_rate_hz must be exactly 30 for phase spacing")',
        "server_tick_rate",
    )
    assert _errors_for(damaged, tmp_path, weakened) == [], (
        "removing the tick-rate clause must admit an interval with half the "
        "declared natural-time duration"
    )


def test_proof_observes_natural_phase_without_mutating_renderer_state() -> None:
    """The proof may request intervals and observe angles, never set a beam."""
    adapter_source = ADAPTER_PATH.read_text(encoding="utf-8")
    external_driver_source = EXTERNAL_MOVEMENT_DRIVER_PATH.read_text(
        encoding="utf-8"
    )
    builder_source = (
        ROOT / "scripts" / "build_godot_canonical_png_grid.py"
    ).read_text(encoding="utf-8")

    assert "beam_phase_target" not in adapter_source
    assert "lighthouse_beam_azimuth_pin" not in adapter_source
    assert "lighthouse_beam_azimuth_pin" not in DRIVER_PATH.read_text(
        encoding="utf-8"
    )
    assert "static-temporal-beam-phases" not in external_driver_source
    assert "_wait_for_lighthouse_beam_phase" not in external_driver_source
    assert '"beam_phase_target"' not in external_driver_source
    assert '"beam_phase_offset_target": temporal_frame.get(' in adapter_source
    assert '"beam_phase_offset_measured": temporal_frame.get(' in adapter_source

    # The builder interprets measured offsets while keeping absolute azimuth as
    # a separately labeled observation.
    assert "naturally observed phase" in builder_source
    assert 'metadata.get("lighthouse_beam_azimuth")' in builder_source
    assert 'metadata.get("beam_phase_offset_target")' in builder_source
    assert 'metadata.get("beam_phase_offset_measured")' in builder_source


def test_red_first_night_mapping_uses_plane_air_dogfight_without_secondary_process() -> None:
    """The night lane must have one airborne capture owner."""
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert 'camera_mode == "night_lighthouse_beam"' in source
    assert 'camera_mode = "plane_air"' in source
    assert "run_fl4265_airplane_dogfight_proof.gd" in source
    assert "--fl4431-secondary-effect-evidence" not in source


def test_red_first_clean_fps_rejects_capture_and_readback_arguments() -> None:
    """The timing arm must fail before launch when capture work leaks into it."""
    assert HEADED_WRAPPER._clean_play_no_capture_args_are_isolated(
        ["--fl4359-clean-play-no-capture"]
    )
    assert not HEADED_WRAPPER._clean_play_no_capture_args_are_isolated(
        [
            "--fl4359-clean-play-no-capture",
            "--fl4359-visual-capture-limit=3",
        ]
    )
    assert not HEADED_WRAPPER._clean_play_no_capture_args_are_isolated(
        [
            "--fl4359-clean-play-no-capture",
            "--fl4431-secondary-effect-diagnostic-only",
        ]
    )
    assert not HEADED_WRAPPER._clean_play_no_capture_args_are_isolated(
        [
            "--fl4359-clean-play-no-capture",
            "--fl4431-night-airborne-temporal-sequence",
        ]
    )
    wrapper_source = HEADED_WRAPPER_PATH.read_text(encoding="utf-8")
    assert 'cmd.append("--fl4359-clean-play-no-capture")' in wrapper_source
    assert wrapper_source.index(
        "_clean_play_no_capture_args_are_isolated(clean_arguments)"
    ) < wrapper_source.index("out_dir.mkdir(parents=True, exist_ok=True)")


if __name__ == "__main__":
    # Direct execution must collect this module through pytest. Without this
    # runner, Python defined eleven night-evidence tests, exercised none, and
    # returned a false zero status to any front door invoking the file itself.
    raise SystemExit(pytest.main([__file__, "-q"]))

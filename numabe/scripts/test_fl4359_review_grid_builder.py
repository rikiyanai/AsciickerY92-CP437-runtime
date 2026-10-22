#!/usr/bin/env python3
"""Regression tests for the FL-4359 Sunday/current review-grid builder."""
from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_fl4359_sunday_current_review_grid.py"

_BUILDER_SPEC = importlib.util.spec_from_file_location("fl4359_review_builder", BUILDER)
assert _BUILDER_SPEC is not None and _BUILDER_SPEC.loader is not None
review_builder = importlib.util.module_from_spec(_BUILDER_SPEC)
_BUILDER_SPEC.loader.exec_module(review_builder)


def _write_frame(path: Path, color: tuple[int, int, int], marker: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (32, 24), color)
    for y in range(6, 12):
        for x in range(10, 16):
            img.putpixel((x, y), marker)
    img.save(path)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_structural_warp_pair(a_path: Path, b_path: Path) -> None:
    a = Image.new("RGB", (160, 96), (120, 170, 190))
    b = Image.new("RGB", (160, 96), (120, 170, 190))
    draw_a = ImageDraw.Draw(a)
    draw_b = ImageDraw.Draw(b)
    for y in range(10, 42, 6):
        for x in range(12, 148, 12):
            draw_a.rectangle((x, y, x + 5, y + 3), fill=(60, 80, 95))
            draw_b.rectangle((x + 5, y, x + 10, y + 3), fill=(60, 80, 95))
    for y in range(54, 88, 6):
        for x in range(12, 148, 12):
            draw_a.rectangle((x, y, x + 5, y + 3), fill=(180, 185, 175))
            draw_b.rectangle((x - 5, y, x, y + 3), fill=(180, 185, 175))
    a_path.parent.mkdir(parents=True, exist_ok=True)
    a.save(a_path)
    b.save(b_path)


def _write_sheet_shift_low_bounce_pair(a_path: Path, b_path: Path) -> None:
    a = Image.new("RGB", (192, 120), (88, 146, 156))
    b = Image.new("RGB", (192, 120), (88, 146, 156))
    draw_a = ImageDraw.Draw(a)
    draw_b = ImageDraw.Draw(b)
    water = (100, 145, 156)
    water_dark = (86, 134, 150)
    island = (124, 150, 104)
    island_dark = (108, 136, 94)
    for y in range(0, 58):
        draw_a.line((0, y, 191, y), fill=water)
    for y in range(0, 72):
        draw_b.line((0, y, 191, y), fill=water)
    for y in range(62, 120):
        draw_a.line((0, y, 191, y), fill=island)
    for y in range(76, 120):
        draw_b.line((0, y, 191, y), fill=island)
    for x in range(8, 184, 16):
        draw_a.rectangle((x, 16, x + 7, 20), fill=water_dark)
        draw_b.rectangle((x + 6, 16, x + 13, 20), fill=water_dark)
        draw_a.rectangle((x, 36, x + 7, 40), fill=water_dark)
        draw_b.rectangle((x + 6, 36, x + 13, 40), fill=water_dark)
        draw_a.rectangle((x, 78, x + 7, 82), fill=island_dark)
        draw_b.rectangle((x - 6, 78, x + 1, 82), fill=island_dark)
        draw_a.rectangle((x, 98, x + 7, 102), fill=island_dark)
        draw_b.rectangle((x - 6, 98, x + 1, 102), fill=island_dark)
    draw_a.rectangle((0, 58, 191, 63), fill=(176, 184, 126))
    draw_b.rectangle((0, 72, 191, 77), fill=(176, 184, 126))
    a_path.parent.mkdir(parents=True, exist_ok=True)
    a.save(a_path)
    b.save(b_path)


def test_visual_metrics_detect_structural_projection_incoherence() -> None:
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        frame_a = tmp / "fl4359_perspective_frame_001.png"
        frame_b = tmp / "fl4359_perspective_frame_002.png"
        _write_structural_warp_pair(frame_a, frame_b)
        metrics = review_builder.visual_temporal_metrics([frame_a, frame_b])
        assert metrics["measured"] is True
        assert metrics["max_structural_projection_local_shift_incoherence_px"] > 2.5
        assert "gameplay_structural_projection_incoherence_bounded" in metrics["failed_gates"]
        assert metrics["gates"]["gameplay_structural_projection_incoherence_bounded"]["pass"] is False


def test_structural_sheet_shift_fails_when_legacy_bounce_metric_passes() -> None:
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        frame_a = tmp / "fl4359_perspective_frame_001.png"
        frame_b = tmp / "fl4359_perspective_frame_002.png"
        _write_sheet_shift_low_bounce_pair(frame_a, frame_b)
        metrics = review_builder.visual_temporal_metrics([frame_a, frame_b])
        assert metrics["max_visual_bounce_block_p95_delta_pct"] <= 18.0
        assert metrics["raw_pair_count"] == 1
        assert metrics["measured_pair_count"] == 1
        assert metrics["raw_tile_count"] > metrics["measured_tile_count"]
        assert metrics["excluded_tile_count"] > 0
        assert metrics["raw_region_pixels"] > metrics["excluded_region_pixels"]
        assert "gameplay_post_probe_surface_bounce_bounded" not in metrics["failed_gates"]
        assert "gameplay_structural_projection_incoherence_bounded" in metrics["failed_gates"]


def _write_all_four_failure_pair(a_path: Path, b_path: Path) -> None:
    a = Image.new("RGB", (160, 96), (120, 170, 190))
    b = Image.new("RGB", (160, 96), (180, 120, 80))
    draw_a = ImageDraw.Draw(a)
    draw_b = ImageDraw.Draw(b)
    for y in range(8, 88, 7):
        for x in range(10, 150, 10):
            color_a = (20 + (x * 3) % 200, 40 + (y * 5) % 200, 80)
            color_b = (230 - color_a[0] // 2, 40, 160)
            dx = 6 if y < 48 else -6
            draw_a.rectangle((x, y, x + 4, y + 3), fill=color_a)
            draw_b.rectangle((x + dx, y, x + dx + 4, y + 3), fill=color_b)
    a_path.parent.mkdir(parents=True, exist_ok=True)
    a.save(a_path)
    b.save(b_path)


def test_visual_metrics_can_fail_all_four_operator_bug_classes() -> None:
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        frame_a = tmp / "fl4359_perspective_frame_001.png"
        frame_b = tmp / "fl4359_perspective_frame_002.png"
        _write_all_four_failure_pair(frame_a, frame_b)
        metrics = review_builder.visual_temporal_metrics([frame_a, frame_b])
        assert set(metrics["failed_gates"]) == {
            "gameplay_moving_camera_jitter_bounded",
            "gameplay_content_shimmer_bounded",
            "gameplay_post_probe_surface_bounce_bounded",
            "gameplay_structural_projection_incoherence_bounded",
        }
        assert metrics["max_visual_jitter_edge_churn_pct"] > 3.0
        assert metrics["max_visual_shimmer_changed_pixel_pct"] > 8.0
        assert metrics["max_visual_bounce_block_p95_delta_pct"] > 18.0
        assert metrics["max_structural_projection_local_shift_incoherence_px"] > 2.5
        assert metrics["max_structural_region_block_p95_delta_pct"] > 12.0


def _minimal_review_fixture(tmp: Path) -> dict[str, Path]:
    """Write the smallest moving-route inputs the grid builder will accept.

    The damage tests are about the damage section, not about the moving route,
    so this builds just enough route evidence for the builder to run and returns
    the four paths it needs.
    """
    sunday = tmp / "sunday"
    current = tmp / "current"
    frames = range(30, 34)
    for frame in frames:
        _write_frame(
            sunday / f"fl4359_perspective_frame_{frame:03d}.png",
            (30, 70, 120),
            (80, 130, 180),
        )
        _write_frame(
            current / f"fl4359_perspective_frame_{frame:03d}.png",
            (30, 70, 120),
            (180, 80, 40),
        )
    receipt_payload = {
        "pass": True,
        "moving_frames": len(frames),
        "visual_frame_count": len(frames),
        "visual_recorded_fps": 30.0,
        "visual_recorded_frame_time_ms": 33.333,
        "visual_media_frame_zero_moving": True,
        "first_visual_active_media_frame_index": 0,
        "last_visual_active_media_frame_index": 1,
        "visual_capture_stride": 1,
        "visual_route_target_ticks": list(frames),
        "missing_visual_route_ticks": [],
        "visual_route_ticks_exact": True,
        "route_identity": "server_tick_constant_physical_flight_input",
        "server_tick_rate_hz": 30.0,
        "server_git_head": "shared-current-server",
        "server_source_root": "/shared/current/colyseus_server",
        "server_source_scope": ["src", "package.json"],
        "server_source_dir": "/shared/current/colyseus_server",
        "server_runtime_scope_fingerprint": "a" * 64,
        "server_identity_valid": True,
        "straight_flight": True,
        "perspective_forced": True,
        "warmup_frames_discarded": 0,
        "git_head": "synthetic",
        "visual_frame_samples": [
            {
                "frame": frame,
                "route_tick": frame,
                "authority_server_tick": 100 + frame,
                "path": f"fl4359_perspective_frame_{frame:03d}.png",
                "player_position": {"x": float(frame), "y": 45.0, "z": 100.0},
                "camera_position": {"x": float(frame), "y": 51.0, "z": 112.0},
                "airplane_position": {"x": float(frame), "y": 45.0, "z": 100.0},
            }
            for frame in frames
        ],
    }
    summary_payload = {
        "max_pair_jitter_pct": 0.0,
        "max_snap_px_delta": 0.0,
        "avg_snap_px_delta": 0.0,
        "snap_sign_flip_rate": 0.0,
        "comparable_pair_count": 1,
        "pair_count": 1,
        "terrain_product_homogeneity_source": "final_product_b55",
        "terrain_product_homogeneity_rows": 100,
        "terrain_product_homogeneity_invalid_rows": 0,
        "terrain_product_dominant_material_ratio": 0.5,
        "terrain_product_homogeneity_baseline_calibrated": True,
        "presentation_observer_population_accounting_valid": True,
        "gates": {
            "gameplay_moving_camera_jitter_bounded": {"pass": True},
            "gameplay_content_shimmer_bounded": {"pass": True},
        },
    }
    paths = {
        "sunday_dir": sunday,
        "current_dir": current,
        "sunday_receipt": tmp / "sunday_receipt.json",
        "current_receipt": tmp / "current_receipt.json",
        "sunday_summary": tmp / "sunday_summary.json",
        "current_summary": tmp / "current_summary.json",
    }
    _write_json(paths["sunday_receipt"], receipt_payload)
    _write_json(paths["current_receipt"], receipt_payload)
    _write_json(paths["sunday_summary"], summary_payload)
    _write_json(paths["current_summary"], summary_payload)
    return paths


def _run_review_builder(
    fixture: dict[str, Path], out_dir: Path, extra: list[str]
) -> None:
    """Invoke the grid builder as a subprocess, exactly as the package does."""
    subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--out-dir",
            str(out_dir),
            "--sunday-dir",
            str(fixture["sunday_dir"]),
            "--current-dir",
            str(fixture["current_dir"]),
            "--sunday-receipt",
            str(fixture["sunday_receipt"]),
            "--current-receipt",
            str(fixture["current_receipt"]),
            "--sunday-summary",
            str(fixture["sunday_summary"]),
            "--current-summary",
            str(fixture["current_summary"]),
            "--limit",
            "2",
            *extra,
        ],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )


def _write_damage_arm(root: Path, renderer: str, tick_base: int) -> None:
    """Create one renderer's two material-211 damage frames plus its receipt.

    `tick_base` lets a caller give the two sides DIFFERENT server ticks, which
    is how the drift cases below are built. Matching sides pass the same value.
    """
    records = []
    for index in range(2):
        frame = root / f"fl4265_damage_frame_{index:02d}.png"
        # A blue-ish frame, matching the material-211 flame's blue ink ramp.
        _write_frame(frame, (18, 24, 40), (40, 130, 250))
        records.append({
            "frame_index": index,
            "authoritative_tick": tick_base + index * 36,
            "capture_path": str(frame),
            "plane_hp": 50 - index * 10,
            "plane_max_hp": 100,
            "plane_airborne": True,
            "pilot_mounted": True,
            "pilot_alive": True,
            "host_damage_smoke_sample_count": 4 + index,
            "retained_mat211_face_count": 3 + index,
            "product_mat211_contributed_subcell_count": 8 + index,
            "damage_flame_phase_step": 100 + index * 2,
            "ink_sample": {
                "blue_ramp_observed": True,
                "blue_ramp_pixel_count": 5 + index,
            },
            "fps_sample_eligible": False,
        })
    _write_json(root / "receipt.json", {
        "proof": "server_owned_material_211_damage_flight",
        "renderer": renderer,
        "damage_capture_records": records,
    })


def test_review_grid_carries_labeled_paired_damage_frames() -> None:
    """The grid must show BOTH renderers' material-211 damage frames.

    A grid that carried only the current renderer's flame would let a reviewer
    confirm current-only evidence and have that read as parity. This test proves
    the paired damage section reaches the manifest with both sides labelled, and
    that omitting the directories leaves the grid honestly damage-free rather
    than silently half-populated.
    """
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        fixture = _minimal_review_fixture(tmp)
        damage_baseline = tmp / "damage" / "baseline"
        damage_current = tmp / "damage" / "current"
        _write_damage_arm(damage_baseline, "baseline", 900)
        _write_damage_arm(damage_current, "current", 900)

        # --- with paired damage evidence -----------------------------------
        paired_out = tmp / "review_paired"
        _run_review_builder(
            fixture,
            paired_out,
            extra=[
                "--damage-baseline-dir",
                str(damage_baseline),
                "--damage-current-dir",
                str(damage_current),
            ],
        )
        manifest = json.loads(
            (
                paired_out
                / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["damage_pair_included"] is True
        assert manifest["damage_frames_are_fps_samples"] is False
        assert manifest["damage_pair_renderers"] == ["baseline", "current"]
        panels = manifest["damage_panels"]
        assert len(panels) == 4
        assert {panel["renderer"] for panel in panels} == {"baseline", "current"}
        for panel in panels:
            assert panel["fps_sample_eligible"] is False
            assert "[server-damage route]" in panel["side"]
            assert panel["authoritative_tick"] in (900, 936)
            assert panel["plane_hp"] in (50, 40)
            assert panel["sha256"]
        # Both renderers must appear at BOTH time-separated frame indices, or a
        # reviewer cannot compare the flicker between them.
        for frame_index in (0, 1):
            sides = {
                panel["renderer"]
                for panel in panels
                if panel["damage_frame_index"] == frame_index
            }
            assert sides == {"baseline", "current"}

        # --- with no damage evidence ---------------------------------------
        bare_out = tmp / "review_bare"
        _run_review_builder(fixture, bare_out, extra=[])
        bare_manifest = json.loads(
            (
                bare_out
                / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert bare_manifest["damage_panels"] == []
        assert bare_manifest["damage_pair_included"] is False

        # --- with only the current renderer's damage evidence ---------------
        # A one-sided directory must produce NO damage rows at all, because a
        # lone panel beside an empty slot is exactly the current-only reading
        # this pairing exists to prevent.
        one_sided_out = tmp / "review_one_sided"
        _run_review_builder(
            fixture,
            one_sided_out,
            extra=["--damage-current-dir", str(damage_current)],
        )
        one_sided_manifest = json.loads(
            (
                one_sided_out
                / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
            ).read_text(encoding="utf-8")
        )
        assert one_sided_manifest["damage_panels"] == []
        assert one_sided_manifest["damage_pair_included"] is False


def test_review_grid_checklist_asks_about_both_damage_renderers() -> None:
    """The operator checklist must carry one damage question per renderer."""
    source = BUILDER.read_text(encoding="utf-8")
    assert (
        "[server-damage route] Baseline A material-211 blue damage flame "
        "is visibly emitted from the airplane" in source
    )
    assert (
        "[server-damage route] current material-211 blue damage flame "
        "is visibly emitted from the airplane" in source
    )
    # The retired singular question must not survive anywhere in the builder.
    assert (
        '"[server-damage route] material-211 blue damage flame '
        'is visibly emitted from the airplane"' not in source
    )


def test_review_builder_emits_difference_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        sunday = tmp / "sunday"
        current = tmp / "current"
        for frame in range(30, 90):
            _write_frame(sunday / f"fl4359_perspective_frame_{frame:03d}.png", (30, 70, 120), (80, 130, 180))
            _write_frame(current / f"fl4359_perspective_frame_{frame:03d}.png", (30, 70, 120), (180, 80, 40))
        receipt_payload = {
            "pass": True,
            "moving_frames": 60,
            "visual_frame_count": 60,
            "visual_recorded_fps": 30.0,
            "visual_recorded_frame_time_ms": 33.333,
            "visual_media_frame_zero_moving": True,
            "first_visual_active_media_frame_index": 0,
            "last_visual_active_media_frame_index": 1,
            "visual_capture_stride": 1,
            "visual_route_target_ticks": list(range(30, 90)),
            "missing_visual_route_ticks": [],
            "visual_route_ticks_exact": True,
            "route_identity": "server_tick_constant_physical_flight_input",
            "server_tick_rate_hz": 30.0,
            "server_git_head": "shared-current-server",
            "server_source_root": "/shared/current/colyseus_server",
            "server_source_scope": ["src", "package.json"],
            "server_source_dir": "/shared/current/colyseus_server",
            "server_runtime_scope_fingerprint": "a" * 64,
            "server_identity_valid": True,
            "straight_flight": True,
            "perspective_forced": True,
            "warmup_frames_discarded": 0,
            "git_head": "synthetic",
            "visual_frame_samples": [
                {
                    "frame": frame,
                    "route_tick": frame,
                    "authority_server_tick": 100 + frame,
                    "path": f"fl4359_perspective_frame_{frame:03d}.png",
                    "player_position": {"x": float(frame), "y": 45.0, "z": 100.0},
                    "camera_position": {"x": float(frame), "y": 51.0, "z": 112.0},
                    "airplane_position": {"x": float(frame), "y": 45.0, "z": 100.0},
                }
                for frame in range(30, 90)
            ],
        }
        summary_payload = {
            "max_pair_jitter_pct": 0.0,
            "max_snap_px_delta": 0.0,
            "avg_snap_px_delta": 0.0,
            "snap_sign_flip_rate": 0.0,
            "comparable_pair_count": 1,
            "pair_count": 1,
            "terrain_product_homogeneity_source": "final_product_b55",
            "terrain_product_homogeneity_rows": 100,
            "terrain_product_homogeneity_invalid_rows": 0,
            "terrain_product_dominant_material_ratio": 0.5,
            "terrain_product_homogeneity_baseline_calibrated": True,
            "presentation_observer_population_accounting_valid": True,
            "gates": {
                "gameplay_moving_camera_jitter_bounded": {"pass": True},
                "gameplay_content_shimmer_bounded": {"pass": True},
            },
        }
        sunday_receipt = tmp / "sunday_receipt.json"
        current_receipt = tmp / "current_receipt.json"
        sunday_summary = tmp / "sunday_summary.json"
        current_summary = tmp / "current_summary.json"
        _write_json(sunday_receipt, receipt_payload)
        _write_json(current_receipt, receipt_payload)
        _write_json(sunday_summary, summary_payload)
        _write_json(current_summary, summary_payload)
        out_dir = tmp / "review"
        subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--out-dir",
                str(out_dir),
                "--sunday-dir",
                str(sunday),
                "--current-dir",
                str(current),
                "--sunday-receipt",
                str(sunday_receipt),
                "--current-receipt",
                str(current_receipt),
                "--sunday-summary",
                str(sunday_summary),
                "--current-summary",
                str(current_summary),
                "--limit",
                "2",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        manifest_path = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["architecture"] == (
            "probe texels may own candidate coverage/depth; "
            "CellOwnerProduct and ascii_compositor own semantic product and paint"
        )
        for key in (
            "artifact",
            "animated_artifact",
            "video_artifact",
            "difference_artifact",
            "difference_animated_artifact",
            "difference_video_artifact",
        ):
            artifact_path = ROOT / manifest[key] if not Path(manifest[key]).is_absolute() else Path(manifest[key])
            assert artifact_path.exists(), key
        assert manifest["video_encode"]["ok"] is True
        assert manifest["difference_video_encode"]["ok"] is True
        assert manifest["primary_evidence"] == "real_runtime_screen_motion_video"
        assert manifest["metrics_attachment"] == "optional_diagnostic_attachment"
        assert manifest["video_container"] == "mp4_h264"
        assert manifest["review_media_fps"] == 30.0
        assert manifest["review_media_fps_source"] == (
            "shared_server_tick_rate_divided_by_exact_route_tick_stride"
        )
        assert manifest["review_media_fps_gate"]["ok"] is True
        assert manifest["current_summary"]["visual_frame_metrics"]["structural_review_window_ok"] is True
        assert manifest["current_summary"]["visual_frame_metrics"]["structural_review_seconds"] == 2.0
        assert manifest["animated_frame_duration_ms"] == 33
        assert manifest["video_encode"]["fps"] == 30.0
        assert manifest["video_frame_intermediates"]["pruned"] is True
        assert manifest["difference_video_frame_intermediates"]["pruned"] is True
        assert not (out_dir / "_video_frames").exists()
        assert not (out_dir / "_diff_video_frames").exists()
        assert manifest["metric_definitions"]["jitter"].startswith("apparent positional")
        assert manifest["metric_definitions"]["shimmer"].startswith("visible cell")
        assert manifest["metric_definitions"]["bounce"].startswith("region-scale")
        assert manifest["metric_definitions"]["structural_projection"].startswith("large-scale perspective")
        assert set(manifest["metric_summary"]["current"]) >= {"jitter", "shimmer", "bounce", "structural_projection"}
        assert manifest["difference_layout"] == "baseline_a_current_diff_rows"
        signoff = manifest["operator_signoff"]
        assert signoff["required"] is True
        assert signoff["gate"] == "visual_sunday_grid_operator_review"
        assert manifest["same_pose_review_gate"]["ok"] is True
        assert manifest["same_pose_review_gate"]["max_camera_delta"] == 0.0
        assert signoff["verify_command"] == [
            "python3",
            "scripts/analyze_failure_log.py",
            "signoff",
            "verify",
            "FL-4359",
        ]
        assert signoff["record_pass_command"][0:5] == [
            "python3",
            "scripts/analyze_failure_log.py",
            "signoff",
            "record",
            "FL-4359",
        ]
        assert "local-headed-godot-fullscreen-default-probe-cutover" in signoff["record_pass_command"]
        signoff_path_arg = signoff["record_pass_command"][signoff["record_pass_command"].index("--path") + 1]
        assert manifest["difference_animated_artifact"] in signoff_path_arg
        assert manifest["video_artifact"] in signoff_path_arg
        assert manifest["difference_video_artifact"] in signoff_path_arg
        assert "motion is stable without visible shimmer" in signoff["checklist"]
        assert len(manifest["difference_panels"]) == 2
        metrics = manifest["difference_panels"][0]["metrics"]
        assert metrics["frame"] == 30
        assert metrics["source_size"] == [32, 24]
        assert metrics["size_mismatch"] is False
        assert metrics["mean_abs_diff_0_255"] > 0.0
        assert metrics["pct_channels_changed_gt12"] > 0.0

        red_summary = json.loads(json.dumps(summary_payload))
        red_summary["gates"]["all_pass"] = False
        red_summary["gates"]["failed_gates"] = ["gameplay_content_shimmer_bounded"]
        red_summary["gates"]["gameplay_content_shimmer_bounded"]["pass"] = False
        _write_json(current_summary, red_summary)
        red_out_dir = tmp / "review_red"
        subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--out-dir",
                str(red_out_dir),
                "--sunday-dir",
                str(sunday),
                "--current-dir",
                str(current),
                "--sunday-receipt",
                str(sunday_receipt),
                "--current-receipt",
                str(current_receipt),
                "--sunday-summary",
                str(sunday_summary),
                "--current-summary",
                str(current_summary),
                "--limit",
                "2",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        red_manifest_path = red_out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
        red_manifest = json.loads(red_manifest_path.read_text(encoding="utf-8"))
        assert red_manifest["proof_state"] == (
            "failure-confirming review package; current runtime gates failed; no acceptance claimed"
        )
        assert red_manifest["review_verdict"] == "failure_confirming_current_runtime_gates_failed"
        assert red_manifest["current_runtime_failed_gates"] == ["gameplay_content_shimmer_bounded"]
        assert red_manifest["operator_signoff"]["status"] == "blocked_by_current_runtime_gate_failures"
        assert red_manifest["operator_signoff"]["record_pass_command"] == []

        bad_receipt = dict(receipt_payload)
        bad_receipt["visual_frame_samples"] = [
            dict(sample, camera_position={"x": 99.0, "y": 99.0, "z": 99.0})
            for sample in receipt_payload["visual_frame_samples"]
        ]
        _write_json(current_receipt, bad_receipt)
        _write_json(current_summary, summary_payload)
        visual_red = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--out-dir",
                str(tmp / "review_bad_pose"),
                "--sunday-dir",
                str(sunday),
                "--current-dir",
                str(current),
                "--sunday-receipt",
                str(sunday_receipt),
                "--current-receipt",
                str(current_receipt),
                "--sunday-summary",
                str(sunday_summary),
                "--current-summary",
                str(current_summary),
                "--limit",
                "2",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert visual_red.returncode == 0
        visual_red_manifest_path = tmp / "review_bad_pose" / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json"
        visual_red_manifest = json.loads(visual_red_manifest_path.read_text(encoding="utf-8"))
        assert visual_red_manifest["same_pose_review_gate"]["ok"] is False
        assert visual_red_manifest["review_verdict"] == "visual_red_same_pose_review_gate_failed"
        assert visual_red_manifest["proof_state"] == (
            "visual-red evidence package; same-pose review gate failed; no acceptance claimed"
        )
        assert visual_red_manifest["video_encode"]["ok"] is True
        assert visual_red_manifest["difference_video_encode"]["ok"] is True
        assert (tmp / "review_bad_pose" / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW.gif").exists()
        assert (tmp / "review_bad_pose" / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW.mp4").exists()

        # Review playback cadence comes from the shared server tick clock and
        # exact route-tick stride, not the renderer HUD sample. Removing that
        # clock must make the media timing receipt fail closed.
        _write_json(current_receipt, dict(receipt_payload, server_tick_rate_hz=0.0))
        failed_fps = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--out-dir",
                str(tmp / "review_bad_fps"),
                "--sunday-dir",
                str(sunday),
                "--current-dir",
                str(current),
                "--sunday-receipt",
                str(sunday_receipt),
                "--current-receipt",
                str(current_receipt),
                "--sunday-summary",
                str(sunday_summary),
                "--current-summary",
                str(current_summary),
                "--limit",
                "2",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert failed_fps.returncode == 0, failed_fps.stdout
        bad_manifest = json.loads((tmp / "review_bad_fps" / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.manifest.json").read_text())
        assert bad_manifest["review_media_fps_gate"]["ok"] is False
        assert bad_manifest["review_verdict"] == "failure_confirming_current_runtime_gates_failed"
        assert "evidence_runtime_media_fps_comparable" in bad_manifest["current_runtime_failed_gates"]


if __name__ == "__main__":
    test_visual_metrics_detect_structural_projection_incoherence()
    test_structural_sheet_shift_fails_when_legacy_bounce_metric_passes()
    test_visual_metrics_can_fail_all_four_operator_bug_classes()
    test_review_builder_emits_difference_artifacts()
    print("FL-4359 review-grid builder tests passed")

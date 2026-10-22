#!/usr/bin/env python3
"""FL-4896: the headed runner's True FF temporal route.

Strict True FF produces none of the VSB sparse-capture records the full-grid
temporal oracle counts, so the canonical five-frame oracle run could only end
in missing_jitter_capture_jsonl. The runner now refuses FF + oracle before
launch, and every FF run arms the true_ff.text_frame.v1 export and measures it
with scripts/measure_true_ff_text_flips.py by default; a recorded F9 bundle
gets RED/YELLOW media drawn and published from the run's own receipt.
Offline: no Godot process starts; the instrument call is stubbed.

Run: python3 -m pytest scripts/tests/test_fl4896_true_ff_text_flips_route.py -q
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_fl4359_headed_movement as runner  # noqa: E402


def _args(**overrides) -> argparse.Namespace:
    base = dict(presentation_mode="FF", analyzer_proof_profile="auto", full_grid_contract_mode="",
                extra_user_arg=[], exact_camera_projection="perspective", camera_mode="plane_air",
                run_classification="diagnostic-only")
    base.update(overrides)
    return argparse.Namespace(**base)


def test_strict_ff_refuses_the_full_grid_oracle() -> None:
    error = runner._true_ff_temporal_route_error(_args(analyzer_proof_profile=runner.FULL_GRID_ORACLE_PROFILE))
    assert "full_grid_temporal_oracle_v1" in error and "drop the profile" in error
    assert "full-grid oracle" in runner._true_ff_temporal_route_error(
        _args(full_grid_contract_mode="exact_translation"))
    assert runner._true_ff_temporal_route_error(
        _args(presentation_mode="FC", analyzer_proof_profile=runner.FULL_GRID_ORACLE_PROFILE)) == ""


def test_ff_runs_pass_no_deleted_text_export_flag() -> None:
    # FL-4907 DEL_LANE_SCREEN_PIPELINE_DELETED_20260930 deleted the exporter and
    # its World.gd flag; the runner neither emits nor polices it.
    assert runner._true_ff_temporal_route_error(_args()) == ""
    assert not hasattr(runner, "TRUE_FF_TEXT_EXPORT_ARG")
    assert runner._full_grid_contract_mode(argparse.Namespace(
        full_grid_contract_mode="", presentation_mode="FF", exact_pose_sweep=True,
        fl4359_terminal_reprojection_stationary_calibration=False)) == ""


def test_runner_wires_detection_by_default() -> None:
    source = (ROOT / "scripts" / "run_fl4359_headed_movement.py").read_text(encoding="utf-8")
    assert "--true-ff-text-flips" not in source
    assert "--true-ff-text-export" not in source
    run_body = source.split("def _run(preserver", 1)[1].split("\ndef main()", 1)[0]
    refuse = run_body.index("true_ff_route_error = _true_ff_temporal_route_error(args)")
    launch = run_body.index("_launch_recorded_macos_godot(")
    measure = run_body.index("true_ff_text_flips = _true_ff_text_flips_after_run(")
    report = run_body.index("_print_true_ff_text_flips(true_ff_text_flips)")
    status = run_body.index('status = "DIAGNOSTIC_OBSERVED" if ok else "DIAGNOSTIC_INVALID"')
    assert refuse < launch < measure < report < status


def test_stems_satisfy_the_instrument(tmp_path: Path) -> None:
    import measure_true_ff_text_flips as flips

    for window in ("run", "f9-20260928-bundle"):
        stem = runner._true_ff_text_flips_stem(_args(), tmp_path / "run 1", "0123456789abcdef", window)
        assert flips.validate_stem(stem) == stem


def test_stem_names_the_rendered_projection(tmp_path: Path) -> None:
    # FL-4512: runs without --exact-camera-projection were all named "ortho",
    # so perspective airplane runs were published as FF-plane-air_ortho.
    cases = {("plane_air", ""): "_persp_", ("perspective", ""): "_persp_",
             ("ortho", ""): "_ortho_", ("plane_air_ortho", ""): "_ortho_",
             ("plane_air", "ortho"): "_ortho_", ("ortho", "perspective"): "_persp_"}
    for (mode, projection), token in cases.items():
        stem = runner._true_ff_text_flips_stem(
            _args(camera_mode=mode, exact_camera_projection=projection), tmp_path / "run", "0123456789", "run")
        assert token in stem, (mode, projection, stem)


def test_missing_export_is_a_failed_measurement(tmp_path: Path) -> None:
    report = runner._true_ff_text_flips_after_run(
        _args(), out_dir=tmp_path, receipt_path=tmp_path / "receipt.json",
        godot_log=tmp_path / "godot.log", repo_provenance={})
    assert report["rc"] == 1 and report["reason"] == "true_ff_text_export_missing"


def _export_line(frame: int, engine_frame: int) -> str:
    return json.dumps({"schema": "true_ff.text_frame.v1", "frame": frame, "engine_frame": engine_frame,
                       "row_pitch_px": 17, "page_shift_px": [0, 0], "chars": []})


def _bundle(tmp_path: Path, engine_frames_with_png: list[int], status: str = "complete") -> Path:
    bundle = tmp_path / "f9bundle"
    (bundle / "frames").mkdir(parents=True)
    rows = []
    for index, engine_frame in enumerate(engine_frames_with_png):
        name = f"frames/frame_{index:05d}.png"
        (bundle / name).write_bytes(b"png")
        rows.append({"engine_frame": engine_frame, "png": name})
    rows.append({"engine_frame": 999, "png": "frames/missing.png"})
    (bundle / "manifest.json").write_text(json.dumps({"frames": rows}))
    (bundle / "completion.json").write_text(json.dumps({"status": status}))
    return bundle


def test_f9_window_keeps_only_engine_frames_with_pngs(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, [11, 12, 13])
    assert runner._f9_png_engine_frames(bundle) == {11, 12, 13}
    export = tmp_path / "text_frames.jsonl"
    export.write_text("".join(_export_line(i, 9 + i) + "\n" for i in range(1, 7)))
    dest = tmp_path / "w" / "window.jsonl"
    assert runner._write_f9_window_export(export, {11, 12, 13, 999}, dest) == 3
    assert [json.loads(line)["engine_frame"] for line in dest.read_text().splitlines()] == [11, 12, 13]


def test_publication_metadata_comes_from_the_receipt(tmp_path: Path) -> None:
    export = tmp_path / "window.jsonl"
    export.write_text(_export_line(1, 11) + "\n")
    missing, reason = runner._true_ff_text_flip_publication_metadata(tmp_path / "receipt.json", export)
    assert missing is None and reason.startswith("publication_identity_unavailable:receipt")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"run_identity": {"build_dirty": True, "build_head": "a" * 40}}))
    metadata, reason = runner._true_ff_text_flip_publication_metadata(receipt, export)
    assert reason == "" and metadata is not None
    assert metadata["fl_id"] == "FL-4896"
    assert (metadata["foreground_mode"], metadata["background_mode"]) == ("FLAT", "FLAT")
    assert metadata["provenance_status"] == "INCOMPLETE_CURRENT_CAPTURE"
    assert metadata["capture_source_state"] == "DIRTY" and metadata["source_commit"] == "a" * 40
    assert metadata["cell_resolution"] == "UNKNOWN"
    assert metadata["run_id"].startswith("CAPTURE-") and "UNKNOWN" not in metadata["run_id"]
    import godot_proof_publication as publication
    publication._metadata_for_validation(metadata)
    export.write_text(json.dumps({"schema": "other"}) + "\n")
    assert runner._true_ff_text_flip_publication_metadata(receipt, export)[1] == \
        "publication_identity_unavailable:text_export_schema_mismatch"


def test_after_run_measures_the_run_and_the_f9_window(tmp_path: Path, monkeypatch) -> None:
    export = tmp_path / runner.TRUE_FF_TEXT_EXPORT_NAME
    export.write_text("".join(_export_line(i, 9 + i) + "\n" for i in range(1, 7)))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"run_identity": {"build_dirty": False}}))
    bundle = _bundle(tmp_path, [11, 12, 13])
    monkeypatch.setattr(runner, "_f9_bundles_from_log", lambda _log: [bundle])
    seen: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        media = ["x_textflips.gif", "x_textflips.html"] if "--f9-bundle" in cmd else []
        out = json.dumps({"frames": 3, "red": 2, "yellow": 1, "carried": 40,
                          "durable_receipt": "artifacts/run_receipts/fl-4896/x/m.json", "media": media})
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    report = runner._true_ff_text_flips_after_run(
        _args(), out_dir=tmp_path, receipt_path=receipt, godot_log=tmp_path / "godot.log",
        repo_provenance={"git_head": "abc"})
    assert report["rc"] == 0, report
    assert len(seen) == 2 and "--f9-bundle" not in seen[0]
    window_cmd = seen[1]
    assert window_cmd[window_cmd.index("--f9-bundle") + 1] == str(bundle)
    metadata_path = Path(window_cmd[window_cmd.index("--publication-metadata") + 1])
    assert json.loads(metadata_path.read_text())["fl_id"] == "FL-4896"
    window = report["f9_windows"][0]
    assert window["frames"] == 3 and len(window["media"]) == 2
    summary = runner._true_ff_text_flips_summary(report, args=_args(), godot_rc=0, analyzer_rc=0,
                                                 receipt_path=receipt, repo_provenance={})
    assert summary["frame_count"] == 3 and summary["pass"] is False


def test_unfinished_f9_bundle_is_a_named_failure(tmp_path: Path, monkeypatch) -> None:
    export = tmp_path / runner.TRUE_FF_TEXT_EXPORT_NAME
    export.write_text(_export_line(1, 11) + "\n" + _export_line(2, 12) + "\n")
    bundle = _bundle(tmp_path, [11, 12], status="incomplete")
    monkeypatch.setattr(runner, "_f9_bundles_from_log", lambda _log: [bundle])
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **_k: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps({"frames": 2, "durable_receipt": "r"}) + "\n", stderr=""))
    report = runner._true_ff_text_flips_after_run(
        _args(), out_dir=tmp_path, receipt_path=tmp_path / "receipt.json",
        godot_log=tmp_path / "godot.log", repo_provenance={})
    assert report["rc"] == 1 and report["reason"] == "f9_window_failed:f9_bundle_not_finalized:incomplete"


FF_SWEEP = [
    "--run-classification", "diagnostic-only", "--camera-mode", "plane_air",
    "--godot-window-mode", "fullscreen", "--exact-camera-projection", "perspective",
    "--exact-camera-fov", "58", "--exact-pose-sweep",
    "--exact-pose-sweep-positions", "447,48,-336;448,48,-337;448,48,-338;449,48,-339;450,48,-340",
    "--exact-camera-position", "447,48,-336", "--exact-player-position", "411,28,-297",
    "--exact-camera-yaw", "2.356194490192345", "--exact-camera-pitch", "-0.36651914291880922",
    "--move-frames", "5", "--turn-frames", "0", "--glyph-matrix-cell-px", "32",
    "--product-time", "17", "--day-cycle-time", "0.5", "--allow-large-jitter-capture",
    "--force-baseline-features", "tilt_shift_off,material_ramp_steps,day_night_cycle",
    "--presentation-mode", "FF", "--keep-forever",
    "--out", ".run/_fl4896_true_ff_text_route_contract_probe", "--preflight-only",
]


def _preflight(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "run_fl4359_headed_movement.py"),
                           *FF_SWEEP, *extra], cwd=ROOT, text=True, capture_output=True)


def test_five_frame_ff_sweep_passes_preflight_with_the_text_route() -> None:
    result = _preflight("--frame-pngs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF_CHECK_PASS" in result.stdout
    godot_command = next(line for line in result.stdout.splitlines() if "godot_command: " in line)
    assert "--true-ff-text-export" not in godot_command
    assert "--fl4359-full-grid-temporal-oracle=1" not in godot_command


def test_five_frame_ff_sweep_refuses_the_oracle() -> None:
    result = _preflight("--full-grid-contract-mode", "exact_translation",
                        "--analyzer-proof-profile", "full_grid_temporal_oracle_v1")
    assert result.returncode == 2
    assert "cannot run --analyzer-proof-profile full_grid_temporal_oracle_v1" in result.stdout

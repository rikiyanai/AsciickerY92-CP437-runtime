"""Tests for scripts/check_fl4359_line_recording.py.

Fixture arms are built in tmp_path: tiny PNG grids, a manifest, a codec-
encoded sidecar, a stub mov, and a godot client log. The real churn meter
runs as a subprocess on the tiny grids (fast); the unit rows call the
checker's pure functions directly.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "check_fl4359_line_recording.py"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(SCRIPT.parent / "recording"))

import check_fl4359_line_recording as checker  # noqa: E402
import f9_symbolic_codec as sym  # noqa: E402

COLS, ROWS = 4, 3
CELL_W, CELL_H = 16, 32


def make_frame(path: Path, white_cells) -> None:
    """Write one frame with the given grid cells inked white on black."""
    im = Image.new("RGB", (COLS * CELL_W, ROWS * CELL_H), (0, 0, 0))
    for cx, cy in white_cells:
        for dy in range(CELL_H):
            for dx in range(CELL_W):
                im.putpixel((cx * CELL_W + dx, cy * CELL_H + dy),
                             (255, 255, 255))
    im.save(path)


def make_manifest(frames_dir: Path, n: int, mov: Path) -> None:
    """Write a manifest with n frames, zero drops, and the grid matrix."""
    lines = ["# test manifest",
             "schema: asciicker.screen_recording_manifest.v1",
             f"mov: {mov}",
             f"frames: {n}", "dropped: 0", "measured_fps: 8.0",
             "frames:"]
    for seq in range(n):
        lines.append(
            f"frame: seq={seq} engine={seq} f10=0:NORMAL "
            f"matrix=[{COLS},{ROWS}] png=frame_{seq:05d}.png")
    (frames_dir / "manifest.txt").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")


def make_sidecar(path: Path, n: int) -> None:
    """Encode key + deltas over the 12-cell grid with f10 tags."""
    cells = [[0x20, 0xFFFFFF, 0x000000]] * (COLS * ROWS)
    frames = [{"seq": 0, "kind": "key", "mode": 0, "f10": 0,
               "cells": cells}]
    for seq in range(1, n):
        frames.append({"seq": seq, "kind": "delta", "mode": 0,
                       "f10": seq % 2,
                       "changes": [[0, 0x23, 0xFFFFFF, 0x000000]]})
    path.write_bytes(sym.encode({"cols": COLS, "rows": ROWS,
                                 "keyframe_every": 60, "frames": frames}))


def make_arm(root: Path, name: str, n: int, ink,
             fence: bool = False) -> tuple[Path, Path]:
    """Build (run_dir, frames_dir) fixture arm; ink selects white cells."""
    run_dir = root / f"run-{name}"
    frames_dir = root / f"frames-{name}"
    run_dir.mkdir()
    frames_dir.mkdir()
    log_lines = ["Godot Engine v4.6.2", "Metal ready"]
    if fence:
        log_lines.append("ERROR: timeout waiting for fence (test)")
    (run_dir / "godot_client.log").write_text("\n".join(log_lines) + "\n",
                                              encoding="utf-8")
    for seq in range(n):
        make_frame(frames_dir / f"frame_{seq:05d}.png", ink(seq))
    mov = frames_dir.with_suffix(".mov")
    mov.write_bytes(b"stub-mov-bytes")
    make_manifest(frames_dir, n, mov)
    make_sidecar(frames_dir / sym.SIDECAR_FILENAME, n)
    return run_dir, frames_dir


def test_manifest_parse_ok(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "m", 3, lambda seq: [(0, 0)])
    manifest = checker.parse_manifest(frames_dir / "manifest.txt")
    assert manifest["matrix"] == [COLS, ROWS]
    assert len(manifest["frames"]) == 3
    assert manifest["meta"]["dropped"] == "0"


def test_token_counts(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "t", 1, lambda seq: [],
                                   fence=True)
    log = run_dir / "godot_client.log"
    assert checker.count_token_lines(log, "timeout waiting for fence") == 1
    assert checker.count_token_lines(log, "SCRIPT ERROR") == 0
    assert checker.count_token_lines(run_dir / "absent.log", "x") == -1


def test_frames_match_manifest(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "f", 4, lambda seq: [(0, 0)])
    assert len(checker.frame_pngs(frames_dir)) == 4
    (frames_dir / "frame_00003.png").unlink()
    assert len(checker.frame_pngs(frames_dir)) == 3


def test_sidecar_join_and_stats(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "s", 6, lambda seq: [(0, 0)])
    stats = checker.sidecar_stats(frames_dir / sym.SIDECAR_FILENAME,
                                  list(range(6)))
    assert stats["absent"] == []
    assert stats["covered"] == 6
    assert stats["kinds"] == {"key": 1, "delta": 5}
    assert stats["header"]["keyframe_every"] == 60
    stats = checker.sidecar_stats(frames_dir / sym.SIDECAR_FILENAME,
                                  list(range(7)))
    assert stats["absent"] == [6]


def test_meter_gates_sparse_vs_dense(tmp_path):
    sparse = tmp_path / "sparse"
    sparse.mkdir()
    for seq in range(4):
        make_frame(sparse / f"frame_{seq:05d}.png", [(0, 0)])
    meter = checker.run_meter(sparse, COLS, ROWS)
    assert meter["density_median"] <= checker.LINE_DENSITY_MAX
    assert meter["flips_median"] < 0.05
    dense = tmp_path / "dense"
    dense.mkdir()
    all_cells = [(cx, cy) for cx in range(COLS) for cy in range(ROWS)]
    for seq in range(4):
        make_frame(dense / f"frame_{seq:05d}.png", all_cells)
    meter = checker.run_meter(dense, COLS, ROWS)
    assert meter["density_median"] > checker.LINE_DENSITY_MAX


def test_density_upper_bound_is_anchored_and_rejects_fgonly():
    assert checker.LINE_DENSITY_REFERENCE < checker.LINE_DENSITY_MAX
    assert checker.LINE_DENSITY_MAX < 0.5502
    assert checker.FILLED_BASELINE["density_median"] > 0.5502


def test_previews_created(tmp_path):
    run_dir, frames_dir = make_arm(
        tmp_path, "p", 30, lambda seq: [(seq % COLS, 0)])
    pngs = checker.frame_pngs(frames_dir)
    checker.write_contact_sheet(pngs, run_dir / "contact.png")
    checker.write_preview_gif(pngs, run_dir / "preview.gif", 8.0)
    assert (run_dir / "contact.png").is_file()
    gif_path = run_dir / "preview.gif"
    assert gif_path.is_file()
    with Image.open(gif_path) as gif:
        assert getattr(gif, "n_frames", 1) == 24


def run_checker(run_dir: Path, frames_dir: Path, name: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir),
         "--frames-dir", str(frames_dir), "--name", name,
         "--report", str(run_dir / "line_check_report.json")],
        capture_output=True, text=True, timeout=300)
    return proc


def test_end_to_end_pass_arm(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "pass", 8,
                                   lambda seq: [(0, 0)])
    proc = run_checker(run_dir, frames_dir, "pass")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((run_dir / "line_check_report.json").read_text())
    assert report["pass"] is True
    assert (run_dir / "line_contact_pass.png").is_file()
    assert (run_dir / "line_preview_pass.gif").is_file()


def test_end_to_end_fence_arm_fails(tmp_path):
    run_dir, frames_dir = make_arm(tmp_path, "fence", 8,
                                   lambda seq: [(0, 0)], fence=True)
    proc = run_checker(run_dir, frames_dir, "fence")
    assert proc.returncode == 1
    report = json.loads((run_dir / "line_check_report.json").read_text())
    assert report["pass"] is False
    assert report["arms"][0]["rows"]["fence_lines_zero"]["pass"] is False


def test_end_to_end_too_dense_arm_fails_density_upper_bound(tmp_path):
    all_cells = [(cx, cy) for cx in range(COLS) for cy in range(ROWS)]
    run_dir, frames_dir = make_arm(
        tmp_path, "dense", 8, lambda seq: all_cells)
    proc = run_checker(run_dir, frames_dir, "dense")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    report = json.loads((run_dir / "line_check_report.json").read_text())
    arm = report["arms"][0]
    assert arm["pass"] is False
    assert arm["meter"]["density_median"] > checker.LINE_DENSITY_MAX
    assert arm["rows"]["line_density_median"]["pass"] is False


def test_end_to_end_blank_arm_is_not_rejected_by_density(tmp_path):
    """A zero-ink meter result is not a density failure.

    This only exercises the meter gate. Visual/readability and symbolic
    provenance acceptance are separate checks owned outside this checker.
    """
    run_dir, frames_dir = make_arm(
        tmp_path, "blank", 8, lambda seq: [])
    proc = run_checker(run_dir, frames_dir, "blank")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((run_dir / "line_check_report.json").read_text())
    arm = report["arms"][0]
    assert arm["pass"] is True
    assert arm["meter"]["density_median"] == 0.0
    assert arm["rows"]["line_density_median"]["pass"] is True
    assert arm["rows"]["line_density_median"]["report_only"] is False
    assert arm["rows"]["line_changed_median"]["pass"] is True
    assert arm["rows"]["line_flips_median"]["pass"] is True

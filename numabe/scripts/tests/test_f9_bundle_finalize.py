#!/usr/bin/env python3
"""FL-4873 F9 bundle finalization and viewer cell geometry.

Scope: the out-of-process finalizer (scripts/recording/f9_finalize_bundle.py)
over synthetic bundles, and the viewer's raster cell geometry
(scripts/build_f10_frames_html.py, Python mirror plus the shipped JS run under
node). No Godot, no headed run. The GDScript side of the same finalization
owner is covered by godot_project/scripts/tests/test_f9_recorder_finalize.gd.

Run: python3 -m pytest scripts/tests/test_f9_bundle_finalize.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
FINALIZER = ROOT / "scripts" / "recording" / "f9_finalize_bundle.py"
BUILDER = ROOT / "scripts" / "build_f10_frames_html.py"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import build_f10_frames_html as builder  # noqa: E402
import f9_finalize_bundle as finalizer  # noqa: E402

W, H, CW, CH = 40, 20, 8, 16  # 5x2 grid, last row clipped to 4 px
STAMPS = (1000, 1050, 1130)


def _bundle(tmp_path: Path, missing_at_stop: int = 0, dropped: int = 0) -> Path:
    bundle = tmp_path / "FL4359_FF_1x2_5x2_8x16_persp_abc1234-dirty_20260923-000000"
    (bundle / "frames").mkdir(parents=True)
    lines = ["schema: asciicker.screen_recording_manifest.v1",
             f"missing_png_files: {missing_at_stop}", "frames:"]
    for seq, stamp in enumerate(STAMPS):
        png = f"frames/frame_{seq:05d}.png"
        Image.new("RGB", (W, H), (seq * 40, 0, 0)).save(bundle / png)
        lines.append(f"frame: seq={seq} engine={100 + seq} unix_ms={stamp} rendered={99 + seq} "
                     f"f10=0:NORMAL mode=FF camera=perspective pos=1.00,2.00,3.00 fov=58.00 "
                     f"cell={CW}x{CH}:terminal matrix=[5,2] image={W}x{H} png={png} capture_ms=0.10")
    (bundle / "manifest.txt").write_text("\n".join(lines) + "\n")
    identity = {"name": bundle.name, "proof_id": "proof.synthetic_f9", "mode": "FF", "cell_aspect": "1x2", "cols": 5, "rows": 2,
                "cell_w": CW, "cell_h": CH, "raster_w": W, "raster_h": H,
                "projection": "perspective", "git_identity": "abc1234-dirty",
                "stamp": "20260923-000000", "source": {"observed_at": "launch"}}
    manifest = {"schema": "asciicker.f9_recording_bundle.v1", "identity": identity,
                "counts": {"frames_listed": 3, "missing_png_files": missing_at_stop, "dropped": dropped},
                "segments": [{"first_seq": 0, "last_seq": 2, "frames": 3, "settings": {}}]}
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    (bundle / "completion.json").write_text(json.dumps({"status": "finalizing", "finalize_reason": "stop"}))
    replay = bundle / "replay"
    replay.mkdir()
    (replay / "recipe.json").write_text(json.dumps({"prerequisites": "recorded", "frame_rows": 3,
                                                    "frame_rows_joined_to_manifest": 3, "input_events": 0}))
    (replay / "checkpoint.json").write_text("{}")
    (replay / "frames.jsonl").write_text("".join(json.dumps({"seq": i, "engine_frame": 100 + i}) + "\n"
                                                 for i in range(3)))
    (replay / "inputs.jsonl").write_text("")
    return bundle


def _run(bundle: Path, *extra: str) -> tuple[int, dict]:
    # The finalizer preserves the bundle durably after finalizing; keep that
    # archive beside the test bundle, never in this repository.
    env = dict(os.environ, ASCIICKER_F9_PRESERVE_REPO_ROOT=str(bundle.parent / "preserve_repo"))
    proc = subprocess.run([sys.executable, str(FINALIZER), str(bundle), *extra],
                          capture_output=True, text=True, timeout=300, env=env)
    return proc.returncode, json.loads((bundle / "completion.json").read_text())


@pytest.mark.skipif(finalizer.resolve_ffmpeg("") == "", reason="ffmpeg unavailable")
def test_clean_bundle_is_complete_with_recorded_timing(tmp_path):
    bundle = _bundle(tmp_path)
    rc, doc = _run(bundle)
    assert rc == 0, doc
    assert doc["status"] == "complete" and doc["problems"] == []
    assert doc["finalize_reason"] == "stop"
    assert (bundle / "recording.mov").is_file() and (bundle / "frames.html").is_file()
    # FL-4873: "complete" now requires zero capture drops (a dropped frame is
    # a capture_dropped:N problem, scripts/tests/test_fl4873_finalize_gates.py).
    assert doc["frames"]["capture_dropped"] == 0
    assert doc["capture_drops"] == 0 and doc["every_engine_frame_captured"] is True
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "packet=pts_time", "-of", "csv=p=0",
                            str(bundle / "recording.mov")], capture_output=True, text=True, check=True)
    pts = sorted(float(v) for v in probe.stdout.split())
    # Recorded intervals 50 ms and 80 ms survive encoding (no 25 fps rounding).
    assert pts[:3] == pytest.approx([0.0, 0.05, 0.13], abs=1e-3)
    html = (bundle / "frames.html").read_text()
    assert bundle.name in html, "viewer header carries the identity record"


@pytest.mark.skipif(finalizer.resolve_ffmpeg("") == "", reason="ffmpeg unavailable")
def test_identity_named_movie_uses_manifest_provenance(tmp_path):
    bundle = _bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    movie = bundle.name + ".mov"
    manifest["bundle"] = {"mov": movie}
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    rc, doc = _run(bundle)
    assert rc == 0, doc
    assert doc["status"] == "complete", doc
    assert doc["outputs"]["mov"]["path"] == movie
    assert (bundle / movie).is_file()
    assert not (bundle / "recording.mov").exists()


def test_invalid_manifest_movie_name_falls_back_to_legacy_name():
    assert finalizer.movie_name_from_manifest({}) == "recording.mov"
    assert finalizer.movie_name_from_manifest({"bundle": {"mov": "nested/movie.mov"}}) == "recording.mov"
    assert finalizer.movie_name_from_manifest({"bundle": {"mov": "named.mov"}}) == "named.mov"


@pytest.mark.skipif(finalizer.resolve_ffmpeg("") == "", reason="ffmpeg unavailable")
def test_title_band_is_burned_into_recording_mov(tmp_path):
    bundle = _bundle(tmp_path)
    rc, doc = _run(bundle)
    assert rc == 0, doc
    band = doc["outputs"]["mov"]["title_band"]
    assert band["fields"]["proof_id"] == "proof.synthetic_f9"
    assert band["source_offset"] == [0, band["band_height"]] and band["band_height"] > 0
    assert any("F9 SEQ=0" in line for line in band["first_frame_lines"])
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                            "stream=width,height,nb_read_frames", "-of", "json",
                            str(bundle / "recording.mov")], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    # The concat demuxer lists the last image twice to hold its recorded
    # duration, so a decoder counts one extra (repeated) frame.
    assert int(stream["nb_read_frames"]) == 4
    assert [int(stream["width"]), int(stream["height"])] == band["size"]
    assert int(stream["height"]) >= H + band["band_height"]


def test_missing_title_field_refuses_the_movie(tmp_path):
    bundle = _bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    del manifest["identity"]["proof_id"]
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    rc, doc = _run(bundle)
    assert rc == 1 and doc["status"] == "incomplete"
    assert "title_field_missing:proof_id" in doc["problems"] and "mov_refused" in doc["problems"]
    assert doc["outputs"]["mov"] == {"status": "refused", "error": "title_field_missing:proof_id"}
    assert not (bundle / "recording.mov").exists()


def test_missing_png_is_incomplete_and_refuses_the_movie(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "frames" / "frame_00001.png").unlink()
    rc, doc = _run(bundle)
    assert rc == 1 and doc["status"] == "incomplete"
    assert "missing_png_files" in doc["problems"] and "mov_refused" in doc["problems"]
    assert not (bundle / "recording.mov").exists()


def test_rows_missing_at_stop_are_not_hidden(tmp_path):
    bundle = _bundle(tmp_path, missing_at_stop=1)
    rc, doc = _run(bundle)
    assert doc["status"] == "incomplete" and doc["frames"]["missing_png_at_stop"] == 1
    assert not (bundle / "recording.mov").exists()


def test_failed_encoder_is_never_reported_complete(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert doc["status"] == "incomplete"
    assert doc["outputs"]["mov"] == {"status": "failed", "error": "ffmpeg_unavailable"}
    assert "mov_failed" in doc["problems"]


def test_failed_html_is_never_reported_complete(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    monkeypatch.setattr(finalizer, "BUILDER", tmp_path / "missing_builder.py")
    doc = finalizer.finalize(bundle, "")
    assert doc["outputs"]["html"]["status"] == "failed"
    assert "html_failed" in doc["problems"] and doc["status"] == "incomplete"


def test_missing_replay_prerequisites_are_incomplete(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    (bundle / "replay" / "inputs.jsonl").unlink()
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert "replay_missing" in doc["problems"] and doc["status"] == "incomplete"


def test_missing_manifest_json_is_incomplete(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    (bundle / "manifest.json").unlink()
    monkeypatch.setattr(finalizer, "resolve_ffmpeg", lambda given: "")
    doc = finalizer.finalize(bundle, "")
    assert "manifest_json_missing" in doc["problems"] and doc["status"] == "incomplete"


def test_raster_geometry_matches_ff_raster_addressing():
    frame = {"matrix": "[225,71]", "image": "3600x2252", "cell": "16x32:terminal"}
    geo = builder.raster_geometry(frame)
    assert geo["mode"] == "raster"
    # The FF raster: cell = pixel // cell_px; the last row is the
    # 12 px strip the image edge leaves (2252 - 70 * 32).
    assert geo["last_cell"] == [3584, 2240, 3600, 2252]
    legacy = builder.raster_geometry({"matrix": "[4,3]", "image": "8x6", "cell": "32x64:terminal"})
    assert legacy["mode"] == "proportional", "a pitch that does not reproduce the matrix is not trusted"


def test_shipped_js_geometry_draws_true_cell_rectangles(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable; JS geometry execution unverified")
    bundle = _bundle(tmp_path)
    subprocess.run([sys.executable, str(BUILDER), str(bundle)], check=True, capture_output=True)
    html = (bundle / "frames.html").read_text()
    js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    helpers = js[js.index("function matrixOf(f)"):js.index("function rectCells(")]
    driver = tmp_path / "geometry_driver.js"
    driver.write_text(
        "const f = {matrix: '[225,71]', image: '3600x2252', cell: '16x32:terminal'};\n" + helpers +
        "const g = geomOf(f);\n"
        "console.log(JSON.stringify([g.mode, cellRect(224, 70, g), cellRect(122, 19, g),"
        " pixelCell(1955, 639, g), pixelCell(3599, 2251, g)]));\n")
    out = json.loads(subprocess.run([node, str(driver)], capture_output=True, text=True, check=True).stdout)
    assert out == ["raster", [3584, 2240, 3600, 2252], [1952, 608, 1968, 640], [122, 19], [224, 70]]


def _write_raw(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Recorder raw frame (ScreenRecordingController.write_raw_frame layout)."""
    from compression import zstd
    path.write_bytes(finalizer.RAW_FRAME_HEADER.pack(finalizer.RAW_FRAME_MAGIC, finalizer.RAW_FRAME_VERSION,
                                                     width, height) + zstd.compress(rgba))


def test_raw_frames_become_identical_pngs_before_assembly(tmp_path):
    """FL-4873: the recorder stores zstd raw frames; the finalizer writes the
    listed PNGs from them, pixel-identical, and removes each raw file."""
    bundle = _bundle(tmp_path)
    pixels = {}
    for seq in range(3):
        png = bundle / "frames" / f"frame_{seq:05d}.png"
        png.unlink()
        rgba = bytes((seq * 40 + i) % 256 for i in range(W * H * 4))
        pixels[seq] = rgba
        _write_raw(bundle / "frames" / f"frame_{seq:05d}.rgba.zst", W, H, rgba)
    rc, doc = _run(bundle)
    assert doc["outputs"]["png"]["status"] == "ok" and doc["outputs"]["png"]["converted"] == 3
    assert "missing_png_files" not in doc["problems"]
    for seq, rgba in pixels.items():
        assert not (bundle / "frames" / f"frame_{seq:05d}.rgba.zst").exists()
        image = Image.open(bundle / "frames" / f"frame_{seq:05d}.png")
        assert image.mode == "RGBA" and image.tobytes() == rgba
    if finalizer.resolve_ffmpeg(""):
        assert rc == 0 and doc["status"] == "complete", doc["problems"]


def test_corrupt_raw_frame_keeps_its_bytes_and_is_named(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "frames" / "frame_00001.png").unlink()
    raw = bundle / "frames" / "frame_00001.rgba.zst"
    raw.write_bytes(b"F9RW" + b"\x00" * 12)
    rc, doc = _run(bundle)
    assert rc == 1 and doc["status"] == "incomplete"
    assert "png_conversion_failed" in doc["problems"] and "missing_png_files" in doc["problems"]
    assert raw.exists(), "a frame that fails conversion keeps its raw file"

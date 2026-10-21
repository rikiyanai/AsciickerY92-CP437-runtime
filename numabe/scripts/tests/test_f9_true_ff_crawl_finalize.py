#!/usr/bin/env python3
"""FL-4907: every True FF F9 recording measures its own crawl.

The True FF driver writes one true_ff.text_frame.v1 line per rendered frame
into the F9 bundle (TrueFFFeatureRenderer._on_text_part); the finalizer runs
scripts/measure_true_ff_text_flips.py on it. This pins the wiring and the
record shape the writer emits. Offline only: no Godot process starts and no
durable receipt is written (the detector subprocess is replaced).

Run: python3 -m pytest scripts/tests/test_f9_true_ff_crawl_finalize.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
import f9_finalize_bundle as fin  # noqa: E402
import measure_true_ff_text_flips as flips  # noqa: E402

WRITER = ROOT / "godot_project/render/ascii/features/TrueFFFeatureRenderer.gd"


def _record(frame: int, chars: list) -> dict:
    # Exactly the keys TrueFFFeatureRenderer._on_text_part writes.
    return {"schema": "true_ff.text_frame.v1", "frame": frame, "engine_frame": 100 + frame,
            "row_pitch_px": 17, "page_shift_px": [0, 0],
            "raster": {"origin_px": [0, 0], "scale": 1, "size_px": [800, 600]},
            "chars": chars}


def test_writer_emits_the_fields_the_detector_reads() -> None:
    src = WRITER.read_text(encoding="utf-8")
    for key in ('"schema": "true_ff.text_frame.v1"', '"frame": frame', '"engine_frame"',
                '"row_pitch_px": ROW_PITCH', '"page_shift_px"', '"raster"',
                '"chars": text_page["chars"]', '"text_layout_valid"'):
        assert key in src, key


def test_writer_shaped_frames_classify_red_and_yellow(tmp_path: Path) -> None:
    fg, bg = 0xFF000000, 0xFFFFFFFF
    frames = tmp_path / fin.TEXT_FRAMES_NAME
    lines = [
        _record(0, [[ord("/"), 100.25, 34, 11, 1, fg, bg]]),
        # in-place swap at the same pen -> RED; ink from nothing far away -> YELLOW
        _record(1, [[ord("|"), 100.25, 34, 11, 1, fg, bg], [ord("_"), 600.0, 340, 11, 1, fg, bg]]),
    ]
    frames.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    loaded = flips.load_frames(frames)
    summary, _ = flips.measure(loaded, 6.0, 1, 11, 4)
    assert summary["red"] >= 1 and summary["yellow"] >= 1, summary


def test_finalizer_runs_the_detector_on_ff_bundles(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / fin.TEXT_FRAMES_NAME).write_text(json.dumps(_record(0, [])) + "\n", encoding="utf-8")
    seen = {}

    def fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"red": 3, "yellow": 1, "media": []}), "")

    monkeypatch.setattr(fin.subprocess, "run", fake_run)
    result = fin.measure_crawl(tmp_path, {"mode": "FF"})
    assert result["status"] == "ok", result
    assert result["summary"] == {"red": 3, "yellow": 1}
    cmd = seen["cmd"]
    assert cmd[1].endswith("measure_true_ff_text_flips.py") and cmd[2] == "run"
    assert "--f9-bundle" in cmd and "--fl" in cmd and cmd[cmd.index("--fl") + 1] == "FL-4907"
    assert (tmp_path / fin.CRAWL_DIR_NAME).is_dir()


def test_ff_bundle_without_text_frames_is_a_problem(tmp_path: Path) -> None:
    export = {"started": True, "frames": 0, "errors": 0}
    assert fin.measure_crawl(tmp_path, {"mode": "FF"}, export)["status"] == "failed"
    assert fin.measure_crawl(tmp_path, {"mode": "FC"}, export)["status"] == "not_applicable"
    # A bundle that declares no export predates it; it is not a crawl result.
    assert fin.measure_crawl(tmp_path, {"mode": "FF"})["status"] == "not_applicable"


def test_recorder_declares_the_export_in_manifest() -> None:
    src = (ROOT / "godot_project/scripts/recording/ScreenRecordingController.gd").read_text(encoding="utf-8")
    assert '"true_ff_text_export": _true_ff_text_export_result' in src
    assert "_begin_true_ff_text_export(dir_path)" in src
    assert "func _end_true_ff_text_export(force: bool = false) -> bool:" in src
    for caller in ("stop", "stop_on_exit"):
        body = src.split(f"func {caller}(", 1)[1].split("\nfunc ", 1)[0]
        assert "_end_true_ff_text_export()" in body
    pump = src.split("func _pump_finalize()", 1)[1].split("\nfunc ", 1)[0]
    assert "var text_quiet: bool = _end_true_ff_text_export(" in pump
    assert "Time.get_ticks_msec()) >= _finalize_deadline_msec" in pump
    assert "or not text_quiet:" in pump
    finalize = src.split("func _finalize_bundle(", 1)[1].split("\nfunc ", 1)[0]
    assert "_end_true_ff_text_export(true)" in finalize
    end = src.split("func _end_true_ff_text_export(", 1)[1].split("\nfunc ", 1)[0]
    assert end.index('result.get("pending", 0)') < end.index("_true_ff_text_export = {}")

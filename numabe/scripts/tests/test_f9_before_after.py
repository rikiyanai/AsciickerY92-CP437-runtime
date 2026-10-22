"""Unit tests for scripts/recording/f9_before_after.py on synthetic tiny bundles.

Grid 6x4 cells of 2x4 px over a 12x14 image (last row clipped, as in the
real 3600x2252 recordings). No real frames or GPU needed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
import f9_before_after as ba  # noqa: E402
import f9_session  # noqa: E402
import f9_symbolic_codec  # noqa: E402

COLS, ROWS, CW, CH, W, H = 6, 4, 2, 4, 12, 14
SEL = [(2, 1), (3, 1), (2, 2), (3, 2)]
PROOF_ID = "route.r07_operator_walk_strafe_tower"
# FL-4894 stem of bundle "B" (FF, F10 view, 2x4 cells = 1x2, 6x4 grid, perspective) and SEL.
STEM = f"B_FF-F10-1x2-{COLS}x{ROWS}-persp_sel{f9_session.selection_digest(SEL)}"


def _frame_image(seq: int, flicker: bool) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :, 0] = 40
    # cell (2,1) flickers on odd frames when flicker is set
    if flicker and seq % 2:
        img[1 * CH:2 * CH, 2 * CW:3 * CW] = (200, 200, 200)
    # cell (0,0) outside the selection always changes (must not be counted)
    img[0:CH, 0:CW] = (seq * 10 % 256, 0, 0)
    return img


def make_bundle(path: Path, name: str, seqs, views, engine0=100, flicker=True, symbolic=False,
                engine_gap_at=None, unix0=1000) -> Path:
    (path / "frames").mkdir(parents=True)
    frames, lines = [], []
    engine = engine0
    for i, seq in enumerate(seqs):
        if engine_gap_at is not None and seq == engine_gap_at:
            engine += 1
        png = f"frames/frame_{seq:05d}.png"
        Image.fromarray(_frame_image(seq, flicker)).save(path / png)
        view = views[i]
        row = {"seq": seq, "engine_frame": engine, "unix_ms": unix0 + 33 * i + (i % 2) * 4,
               "f10_view": view, "f10_view_name": {0: "NORMAL", 1: "FGONLY"}[view],
               "matrix": f"[{COLS},{ROWS}]", "cell": f"{CW}x{CH}:terminal", "image": f"{W}x{H}",
               "png": png, "hud": "0", "mode": "FF", "projection": "perspective"}
        frames.append(row)
        lines.append(f"frame: seq={seq} engine={engine} unix_ms={row['unix_ms']} f10={view}:x "
                     f"matrix=[{COLS},{ROWS}] image={W}x{H} png={png}")
        engine += 1
    bundle_meta = {"symbolic_sidecar": None}
    if symbolic:
        docs = []
        for i, seq in enumerate(seqs):
            cells = [(0x41, 0xFFFFFF, 0)] * (COLS * ROWS)
            cells = list(cells)
            if seq % 2:
                cells[1 * COLS + 2] = (0x42, 0xFFFFFF, 0)
            docs.append({"kind": "key", "seq": seq, "mode": 0, "f10": views[i], "cells": cells})
        f9_symbolic_codec.dumps(path / "symbolic.v1.sym",
                                {"cols": COLS, "rows": ROWS, "keyframe_every": 4, "frames": docs})
        bundle_meta = {"symbolic_sidecar": "symbolic.v1.sym"}
    identity = {"name": name, "git_identity": f"abc{name.lower()}123-dirty", "projection": "perspective",
                "proof_id": PROOF_ID,
                "source": {"run_identity": {"label": f"abc{name.lower()}123-dirty"}}}
    (path / "manifest.json").write_text(json.dumps({"identity": identity, "frames": frames,
                                                     "bundle": bundle_meta, "segments": []}))
    (path / "manifest.txt").write_text(f"recording_identity: {name}\nframes:\n" + "\n".join(lines) + "\n")
    return path


def write_session(bundle: Path, name: str, cells=SEL, referenced=(3, 4, 5, 6), bbox=None) -> None:
    ordered = sorted(cells, key=lambda c: (c[1], c[0]))
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    doc = {"schema": f9_session.SCHEMA,
           "capture": {"id": name, "manifest_sha256": hashlib.sha256((bundle / "manifest.txt").read_bytes()).hexdigest()},
           "grid": {"cols": COLS, "rows": ROWS},
           "selection": {"cells": [list(c) for c in ordered], "count": len(cells),
                         "bbox": bbox or [min(xs), min(ys), max(xs), max(ys)],
                         "digest": f9_session.selection_digest(cells)},
           "frames": {"referenced": list(referenced)}}
    (bundle / "session.json").write_text(json.dumps(doc))


def make_replay(out: Path, before: Path, rec_seqs, views, flicker=False, validation="valid",
                symbolic=True) -> Path:
    rec = make_bundle(out / "replay_recording" / "R", "R", list(range(len(rec_seqs))), views,
                      engine0=500, flicker=flicker, symbolic=symbolic, unix0=90000)
    (out / "replay").mkdir(parents=True)
    (out / "replay" / "replay_result.json").write_text(json.dumps(
        {"status": "replayed", "bundle": str(before), "replay_bundle": str(rec),
         "window": [rec_seqs[0], rec_seqs[-1]]}))
    (out / "replay" / "replay_witness.jsonl").write_text("".join(
        json.dumps({"recorded_seq": s, "engine_frame": 500 + i}) + "\n" for i, s in enumerate(rec_seqs)))
    (out / "replay_validation.json").write_text(json.dumps({"status": validation, "mode": "candidate",
                                                            "mismatches": {} if validation == "valid" else {"x": {}}}))
    return out


@pytest.fixture
def before(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    write_session(b, "B")
    return b


def run(argv):
    args = ba.build_parser().parse_args(argv)
    return ba.run(args, argv)


def base(before, tmp_path, *extra):
    # Every run archives its selection before use; keep that inside tmp_path.
    root = [] if "--repo-root" in extra else ["--repo-root", str(tmp_path)]
    return [str(before), "--out", str(tmp_path / "out"), "--no-video", *root, *extra]


def test_candidate_comparison_counts_and_mapping(before, tmp_path):
    after = make_replay(tmp_path / "A", before, list(range(2, 9)), [1] * 7)
    m = run(base(before, tmp_path, "--after", str(after), "--context-cells", "1", "--repo-root", str(tmp_path)))
    assert m["comparison"]["view"] == "F10"
    assert m["frames"]["window"] == [3, 6] and m["frames"]["count"] == 4
    assert [r["after_seq"] for r in m["frames"]["per_frame"]] == [1, 2, 3, 4]
    assert all(r["before_view"] == "FGONLY" and r["after_view"] == "FGONLY" for r in m["frames"]["per_frame"])
    # before flickers cell (2,1) every transition; after (no flicker) has none in pixels
    assert m["changes"]["before"]["pixel_changed_total"] == 3
    assert m["changes"]["after"]["pixel_changed_total"] == 0
    # after sidecar says glyph flips every frame at (2,1)
    assert m["changes"]["after"]["glyph_changed_total"] == 3
    assert "glyph_changed_total" not in m["changes"]["before"]
    # crop = bbox (2..3,1..2) + 1 context -> cells 1..4, 0..3, last row clipped by the image edge
    assert m["selection"]["crop_cells"] == [1, 0, 4, 3]
    assert m["selection"]["crop_pixels"] == [2, 0, 10, 14]
    durable = Path(m["_durable"])
    assert durable.parent == tmp_path / "artifacts" / "run_receipts" / "fl-4873"
    mbytes = (durable / ba.MANIFEST_NAME).read_bytes()
    assert durable.name == "before-after__" + hashlib.sha256(mbytes).hexdigest()[:16]
    for name, row in m["outputs"].items():
        assert ba.sha256_file(durable / name) == row["sha256"]


def test_gif_frames_timing_native_pixels(before, tmp_path):
    m = run(base(before, tmp_path, "--after-self", "--context-cells", "0", "--scale", "2"))
    out = tmp_path / "out"
    assert m["comparison"]["self_comparison"] and m["comparison"]["dry_run"] and m["_durable"] is None
    for name in (f"{STEM}_before.gif", f"{STEM}_after.gif", f"{STEM}_before_x2.gif",
                 f"{STEM}_side_by_side_x2.gif"):
        with Image.open(out / name) as im:
            assert im.n_frames == 4, name  # identical frames are not merged
    titles = json.loads((out / f"{STEM}_before.titles.json").read_text())
    x0, y0, x1, y1 = titles["frames"][0]["panes"][0]["source_box"]
    assert (x1 - x0, y1 - y0) == (4, 8)  # native crop 1:1 below the burned-in title band
    with Image.open(out / f"{STEM}_before.gif") as im:
        durations = []
        pixels = []
        for i in range(im.n_frames):
            im.seek(i)
            durations.append(im.info["duration"])
            pixels.append(np.asarray(im.convert("RGB"))[y0:y1, x0:x1])
    recorded = [r["recorded_ms"] for r in m["frames"]["per_frame"]]
    assert abs(sum(durations) - sum(recorded)) < 10
    assert all(abs(a - b) <= 5 for a, b in zip(np.cumsum(durations), np.cumsum(recorded)))
    src = np.asarray(Image.open(before / "frames" / "frame_00003.png").convert("RGB"))[4:12, 4:8]
    assert np.array_equal(pixels[0], src)
    assert m["outputs"][f"{STEM}_before.gif"]["palette_exact"]


def test_after_frames_unchanged_pixels_match_duplicate_frames(tmp_path):
    frames = [np.zeros((2, 2, 3), np.uint8)] * 3
    info = ba._encode_gif(frames, [3, 3, 3], tmp_path / "x.gif")
    with Image.open(tmp_path / "x.gif") as im:
        assert im.n_frames == 3 and info["frames"] == 3


def test_refuses_non_f10_frame(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1, 1, 1, 1, 0, 1, 1, 1, 1, 1])
    write_session(b, "B")
    with pytest.raises(ba.Refused) as exc:
        run(base(b, tmp_path, "--after-self", "--view", "F10"))
    assert exc.value.code == "view_not_f10"
    with pytest.raises(ba.Refused) as exc:
        run(base(b, tmp_path, "--after-self"))
    assert exc.value.code == "view_mixed"


def test_normal_comparison(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [0] * 10)
    write_session(b, "B")
    after = make_replay(tmp_path / "A", b, list(range(0, 10)), [0] * 10)
    m = run(base(b, tmp_path, "--after", str(after), "--dry-run"))
    assert m["comparison"]["view"] == "NORMAL" and m["_durable"] is None
    after2 = make_replay(tmp_path / "A2", b, list(range(0, 10)), [1] * 10)
    with pytest.raises(ba.Refused) as exc:
        run(base(b, tmp_path, "--after", str(after2), "--dry-run", "--view", "NORMAL"))
    assert exc.value.code == "view_not_normal"


def test_refuses_session_mismatch(before, tmp_path):
    doc = json.loads((before / "session.json").read_text())
    doc["capture"]["id"] = "other"
    (before / "session.json").write_text(json.dumps(doc))
    with pytest.raises(ba.Refused) as exc:
        run(base(before, tmp_path, "--after-self"))
    assert exc.value.code == "session_capture_id"


def test_refuses_bbox_mismatch(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    write_session(b, "B", bbox=[2, 1, 4, 2])
    with pytest.raises(ba.Refused) as exc:
        run(base(b, tmp_path, "--after-self"))
    assert exc.value.code == "selection_bbox"


def test_refuses_window_outside_recording(before, tmp_path):
    with pytest.raises(ba.Refused) as exc:
        run(base(before, tmp_path, "--after-self", "--last-seq", "12", "--first-seq", "8"))
    assert exc.value.code == "window_outside_recording"


def test_refuses_uncovered_replay_window(before, tmp_path):
    after = make_replay(tmp_path / "A", before, [4, 5, 6, 7], [1] * 4)
    with pytest.raises(ba.Refused) as exc:
        run(base(before, tmp_path, "--after", str(after), "--dry-run"))
    assert exc.value.code == "after_window_uncovered"


def test_refuses_other_bundle_replay(before, tmp_path):
    other = make_bundle(tmp_path / "O", "O", list(range(10)), [1] * 10)
    after = make_replay(tmp_path / "A", other, list(range(10)), [1] * 10)
    with pytest.raises(ba.Refused) as exc:
        run(base(before, tmp_path, "--after", str(after), "--dry-run"))
    assert exc.value.code == "after_other_bundle"


def test_invalid_replay_refused_unless_admitted(before, tmp_path):
    after = make_replay(tmp_path / "A", before, list(range(10)), [1] * 10, validation="invalid")
    with pytest.raises(ba.Refused) as exc:
        run(base(before, tmp_path, "--after", str(after), "--dry-run"))
    assert exc.value.code == "after_replay_not_valid"
    m = run(base(before, tmp_path, "--after", str(after), "--dry-run", "--allow-invalid-replay"))
    assert m["comparison"]["invalid_replay_admitted"]


def test_refuses_dropped_engine_frame(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10, engine_gap_at=5)
    write_session(b, "B")
    with pytest.raises(ba.Refused) as exc:
        run(base(b, tmp_path, "--after-self"))
    assert exc.value.code == "before_engine_gap"


def test_preservation_refuses_different_bytes(before, tmp_path):
    after = make_replay(tmp_path / "A", before, list(range(10)), [1] * 10)
    m = run(base(before, tmp_path, "--after", str(after), "--repo-root", str(tmp_path)))
    durable = Path(m["_durable"])
    (durable / f"{STEM}_before.gif").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="refusing to replace"):
        run(base(before, tmp_path, "--after", str(after), "--repo-root", str(tmp_path)))


def test_symbolic_selection_matches_codec_reconstruct(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(6)), [1] * 6, symbolic=True)
    doc = f9_symbolic_codec.load(b / "symbolic.v1.sym")
    got = ba.symbolic_selection(b / "symbolic.v1.sym", list(range(6)), SEL, COLS)
    for seq in range(6):
        assert got[seq] == [f9_symbolic_codec.reconstruct(doc, seq, x, y) for x, y in SEL]


def test_gif_delays_cumulative():
    delays = ba.gif_delays_cs([0.033, 0.037, 0.033, 0.037])
    assert sum(delays) == 14
    assert delays == [3, 4, 3, 4]


def test_main_reports_refusal_json(before, tmp_path, capsys):
    rc = ba.main([str(before), "--out", str(tmp_path / "o"), "--no-video"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["code"] == "after_missing"


@pytest.mark.skipif(not ba.f9_finalize_bundle.resolve_ffmpeg(""), reason="ffmpeg not installed")
def test_side_by_side_video_and_exact_label_palette(before, tmp_path):
    argv = [str(before), "--out", str(tmp_path / "out"), "--after-self", "--scale", "2",
            "--repo-root", str(tmp_path)]
    m = run(argv)
    assert m["video"]["status"] == "ok" and m["video"]["path"] == f"{STEM}_side_by_side_x2.mov"
    assert (tmp_path / "out" / f"{STEM}_side_by_side_x2.mov").is_file()
    assert not (tmp_path / "out" / "_sbs_frames").exists()
    assert m["outputs"][f"{STEM}_side_by_side_x2.gif"]["palette_exact"]


def test_every_visual_deliverable_carries_the_fl4894_stem(before, tmp_path):
    """FL-4894: bundle + view + grid + projection + selection digest in every visual filename."""
    m = run(base(before, tmp_path, "--after-self", "--scale", "2"))
    assert m["deliverable_stem"]["stem"] == STEM
    assert m["deliverable_stem"]["parts"] == {"bundle": "B", "view": "FF-F10", "grid": f"1x2-{COLS}x{ROWS}",
                                              "projection": "persp",
                                              "selection": "sel" + f9_session.selection_digest(SEL)}
    visual = [n for n in m["outputs"] if n.endswith((".gif", ".mov", ".png", ".html"))]
    assert visual and all(Path(n).name.startswith(STEM + "_") for n in visual), visual
    assert (tmp_path / "out" / f"{STEM}_before_after.html").is_file()


def test_stem_changes_with_the_selection(before, tmp_path):
    other = [(1, 1), (2, 1)]
    write_session(before, "B", cells=other)
    m = run(base(before, tmp_path, "--after-self"))
    assert m["deliverable_stem"]["stem"].endswith("_sel" + f9_session.selection_digest(other))
    assert m["deliverable_stem"]["stem"] != STEM


@pytest.mark.parametrize("drop, code", [("mode", "stem_mode_missing"), ("projection", "stem_projection_missing")])
def test_stem_refuses_missing_identity(tmp_path, drop, code):
    """FL-4894: a stem never carries a placeholder in place of missing identity."""
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    doc = json.loads((b / "manifest.json").read_text())
    for row in doc["frames"]:
        row.pop(drop)
    doc["identity"].pop(drop, None)
    (b / "manifest.json").write_text(json.dumps(doc))
    with pytest.raises(ba.Refused) as exc:
        ba.deliverable_stem(b, doc, doc["frames"][0], "F10", f9_session.selection_digest(SEL))
    assert exc.value.code == code

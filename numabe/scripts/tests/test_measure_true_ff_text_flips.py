"""FL-4896: the True FF text-row flip instrument classifies character changes.

Scope: synthetic `true_ff.text_frame.v1` frames. These tests prove only the
classifier and the receipt path, never a True FF temporal result (no Godot
run, no export, no visual claim).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import measure_true_ff_text_flips as m  # noqa: E402

PITCH = 17
STEM = "FL4359_synthetic_FF-1x1-10x4-ortho_selall"


def frame(seq, chars, shift=(0, 0), png=None):
    obj = {"schema": m.SCHEMA, "frame": seq, "row_pitch_px": PITCH, "page_shift_px": list(shift),
           "chars": [[cp, x, row * PITCH, w, 1, 0xFFFFFF, 0] for cp, x, row, w in chars]}
    if png:
        obj["png"] = png
    return obj


def run(tmp_path, frames, **kw):
    path = tmp_path / "frames.jsonl"
    path.write_text("".join(json.dumps(f) + "\n" for f in frames))
    parsed = m.load_frames(path)
    return m.measure(parsed, kw.get("reach_px", 11), kw.get("reach_rows", 1), 11, kw.get("window", 8))


def test_static_text_has_no_events(tmp_path):
    f = [("A", 0, 0, 5), ("ヽ", 5, 0, 16)]
    f = [(ord(c), x, r, w) for c, x, r, w in f]
    summary, events = run(tmp_path, [frame(i, f) for i in range(4)])
    assert summary["red"] == summary["yellow"] == 0 and summary["carried"] == 6


def test_sideways_move_and_page_scroll_are_motion(tmp_path):
    a = [(ord("⌒"), 20, 1, 16)]
    frames = [frame(0, a), frame(1, [(ord("⌒"), 25, 1, 16)]), frame(2, [(ord("⌒"), 25, 1, 16)], shift=(-11, 0))]
    summary, _ = run(tmp_path, frames)
    assert summary["red"] == summary["yellow"] == 0 and summary["carried"] == 2


def test_row_change_of_the_same_glyph_is_motion(tmp_path):
    summary, _ = run(tmp_path, [frame(0, [(ord("_"), 40, 2, 5)]), frame(1, [(ord("_"), 40, 3, 5)])])
    assert summary["red"] == 0 and summary["carried"] == 1


def test_substitution_in_place_is_red(tmp_path):
    summary, events = run(tmp_path, [frame(0, [(ord("/"), 40, 2, 5)]), frame(1, [(ord("|"), 40, 2, 5)])])
    assert summary["red"] == 1 and summary["kinds"] == {"substitute": 1}
    assert events[0]["colour"] == "red" and events[0]["cp"] == ord("|")


def test_vanish_is_red(tmp_path):
    summary, events = run(tmp_path, [frame(0, [(ord("/"), 40, 2, 5)]), frame(1, [])])
    assert summary["kinds"] == {"vanish": 1} and events[0]["x"] == 40


def test_isolated_appearance_is_yellow(tmp_path):
    summary, events = run(tmp_path, [frame(0, [(ord("/"), 0, 0, 5)]), frame(1, [(ord("/"), 0, 0, 5), (ord("x"), 200, 3, 5)])])
    assert summary["yellow"] == 1 and summary["red"] == 0 and events[0]["colour"] == "yellow"


def test_spacing_characters_are_not_ink(tmp_path):
    summary, _ = run(tmp_path, [frame(0, [(0x3000, 0, 0, 11)]), frame(1, [(0x20, 0, 0, 5)])])
    assert summary["red"] == summary["yellow"] == summary["carried"] == 0


def test_flip_back_is_returned_and_repeats_in_place(tmp_path):
    seq = ["/", "|", "/", "|", "/"]
    summary, events = run(tmp_path, [frame(i, [(ord(c), 40, 2, 5)]) for i, c in enumerate(seq)])
    assert summary["red"] == 4
    assert summary["returned"] >= 2
    assert summary["repeat_sites"] == 1


def test_one_threshold_crossing_does_not_repeat(tmp_path):
    seq = ["/", "/", "|", "|", "|"]
    summary, _ = run(tmp_path, [frame(i, [(ord(c), 40, 2, 5)]) for i, c in enumerate(seq)])
    assert summary["red"] == 1 and summary["returned"] == 0 and summary["repeat_sites"] == 0


def test_frames_must_be_frame_exact(tmp_path):
    path = tmp_path / "frames.jsonl"
    path.write_text(json.dumps(frame(0, [])) + "\n" + json.dumps(frame(2, [])) + "\n")
    with pytest.raises(ValueError, match="frame-exact"):
        m.load_frames(path)


def test_stem_must_carry_identity():
    with pytest.raises(ValueError, match="FL-4894"):
        m.validate_stem("before")
    assert m.validate_stem(STEM) == STEM


def test_same_state_compare(tmp_path):
    a = m.parse_frame(frame(7, [(ord("/"), 40, 2, 5)]), tmp_path, None)
    b = m.parse_frame(frame(9, [(ord("/"), 40, 2, 5), (ord("x"), 50, 2, 5)]), tmp_path, None)
    summary, events = m.compare_frames(a, a)
    assert summary["identical"] and summary["red"] == 0
    summary, events = m.compare_frames(a, b)
    assert not summary["identical"] and summary["red"] == 1 and events[0]["cp"] == ord("x")


def synthetic_metadata(tmp_path):
    """Synthetic ATTESTED capture identity: proves the publication path only."""
    h = "ab" * 32
    meta = {"provenance_status": "ATTESTED", "fl_id": "FL-4896", "run_id": "synthetic-text-flips",
            "proof_kind": "RENDER", "architecture": "TRUE-FF", "foreground_mode": "FLAT",
            "background_mode": "FLAT", "capture_source_state": "CLEAN", "source_commit": "a" * 40,
            "effective_tree_fingerprint": h, "cell_resolution": {"width": 11, "height": 17},
            "glyph_source_resolution": [{"width": 16, "height": 16}],
            "executed_sources": [{"path": "synthetic.gd", "sha256": h}],
            "build_identity": {"kind": "synthetic", "path": "synthetic", "sha256": h},
            "runtime_identity": {"target": "synthetic", "engine_version": "synthetic"},
            "workload_identity": {"runner": "pytest", "scene": "synthetic", "map": "synthetic",
                                  "pose": "synthetic", "camera": "synthetic", "timing": "3 frames"}}
    path = tmp_path / "publication_metadata.json"
    path.write_text(json.dumps(meta))
    return path


def write_run(tmp_path):
    from PIL import Image

    for i in range(3):
        Image.new("RGB", (120, 80)).save(tmp_path / f"f{i}.png")
    seq = ["/", "|", "/"]
    path = tmp_path / "frames.jsonl"
    path.write_text("".join(json.dumps(frame(i, [(ord(c), 40, 2, 5)], png=f"f{i}.png")) + "\n"
                            for i, c in enumerate(seq)))
    return path


def preserve_into(monkeypatch, receipts):
    real = m.preserve_file_set
    monkeypatch.setattr(m, "preserve_file_set", lambda **kw: real(**kw, destination_root=receipts))


def test_main_preserves_receipt_and_draws_boxes(tmp_path, monkeypatch):
    from PIL import Image

    receipts = tmp_path / "receipts"
    preserve_into(monkeypatch, receipts)
    path = write_run(tmp_path)
    out = tmp_path / "out"
    assert m.main(["run", str(path), "--stem", STEM, "--out", str(out),
                   "--publication-metadata", str(synthetic_metadata(tmp_path))]) == 0
    assert (out / f"{STEM}_textflips.gif").exists()
    page = (out / f"{STEM}_textflips.html").read_text()
    assert "RED" in page and "YELLOW" in page
    manifests = list(receipts.glob("fl-4896/true-ff-text-flips__*/preservation_manifest.json"))
    assert len(manifests) == 1
    names = {row["name"] for row in json.loads(manifests[0].read_text())["files"]}
    assert f"{STEM}_textflips.json" in names and "input_0_frames.jsonl" in names
    gif = Image.open(out / f"{STEM}_textflips.gif")
    gif.seek(1)
    pixel = gif.convert("RGB").getpixel((40, 22 + 2 * PITCH))
    assert pixel[0] > 200 and pixel[1] < 100  # red border on the substituted glyph


def test_media_are_published_with_manifest_and_finder_tags(tmp_path, monkeypatch):
    receipts = tmp_path / "receipts"
    preserve_into(monkeypatch, receipts)
    out = tmp_path / "out"
    assert m.main(["run", str(write_run(tmp_path)), "--stem", STEM, "--out", str(out),
                   "--publication-metadata", str(synthetic_metadata(tmp_path))]) == 0
    manifest_path = out / f"{STEM}_textflips.publication.json"
    doc = json.loads(manifest_path.read_text())
    assert doc["schema"] == "godot-proof-publication.v1"
    assert m.publication.validate_manifest(manifest_path) == []
    assert m.publication.verify_finder_tags(manifest_path) == []
    by_role = {a["role"]: a for a in doc["artifacts"]}
    assert set(by_role) == {"true-ff-text-flips", "true-ff-text-flips-legend"}
    gif = by_role["true-ff-text-flips"]
    assert gif["filename"] == gif["expected_filename"]
    assert gif["frame_count"] == m.publication.inspect_visual(out / gif["filename"])[2]
    assert gif["aliases"] == [f"{STEM}_textflips.gif"]  # the FL-4894 stem name, same bytes
    assert by_role["true-ff-text-flips-legend"]["aliases"] == [f"{STEM}_textflips.html"]
    assert "GODOT_PROOF:FL-4896" in m.publication.finder_tags(out / f"{STEM}_textflips.gif")
    summary = json.loads((out / f"{STEM}_textflips.json").read_text())
    assert summary["publication"]["manifest"] == manifest_path.name
    preserved = list(receipts.glob("fl-4896/true-ff-text-flips__*/preservation_manifest.json"))
    names = {row["name"] for row in json.loads(preserved[0].read_text())["files"]}
    assert manifest_path.name in names


def test_media_without_publication_metadata_exit_4_after_preservation(tmp_path, monkeypatch):
    receipts = tmp_path / "receipts"
    preserve_into(monkeypatch, receipts)
    out = tmp_path / "out"
    assert m.main(["run", str(write_run(tmp_path)), "--stem", STEM, "--out", str(out)]) == 4
    summary = json.loads((out / f"{STEM}_textflips.json").read_text())
    assert summary["publication"]["status"] == "NOT_PUBLISHED"
    assert list(receipts.glob("fl-4896/true-ff-text-flips__*/preservation_manifest.json"))
    assert not list(out.glob("*.publication.json"))


def test_no_media_needs_no_publication(tmp_path, monkeypatch):
    preserve_into(monkeypatch, tmp_path / "receipts")
    path = tmp_path / "frames.jsonl"
    path.write_text("".join(json.dumps(frame(i, [(ord("/"), 40, 2, 5)])) + "\n" for i in range(3)))
    assert m.main(["run", str(path), "--stem", STEM, "--out", str(tmp_path / "out")]) == 0


def test_gif_keeps_every_frame_when_nothing_changes(tmp_path):
    """Pillow merges identical consecutive GIF frames; the burned frame number
    keeps one GIF frame per rendered frame on a narrow image."""
    from PIL import Image

    for i in range(3):
        Image.new("RGB", (120, 80)).save(tmp_path / f"f{i}.png")
    frames = [m.Frame(i, PITCH, [m.Glyph(ord("/"), 40, 2 * PITCH, 5, 1, 0)], tmp_path / f"f{i}.png",
                      (0.0, 0.0), 1.0) for i in range(3)]
    m.annotate(frames, [], tmp_path, STEM, {"frames": 3}, "synthetic")
    assert Image.open(tmp_path / f"{STEM}_textflips.gif").n_frames == 3


def test_f9_bundle_pngs_pair_by_engine_frame(tmp_path):
    from PIL import Image

    bundle = tmp_path / "bundle"
    (bundle / "frames").mkdir(parents=True)
    rows = []
    for i in range(2):
        Image.new("RGB", (240, 160)).save(bundle / "frames" / f"frame_{i:05d}.png")
        rows.append({"seq": i, "engine_frame": 500 + i, "png": f"frames/frame_{i:05d}.png"})
    (bundle / "manifest.json").write_text(json.dumps({"frames": rows}))
    lines = []
    for i in range(3):
        obj = frame(10 + i, [(ord("/"), 40, 2, 5)])
        obj.update(engine_frame=500 + i, raster={"origin_px": [0, 0], "scale": 1.0, "size_px": [120, 80]})
        lines.append(json.dumps(obj))
    path = tmp_path / "frames.jsonl"
    path.write_text("\n".join(lines) + "\n")
    frames = m.load_frames(path)
    assert m.attach_f9_pngs(frames, bundle) == 2
    assert frames[0].scale == 2.0 and frames[0].png.name == "frame_00000.png"
    assert frames[2].png is None  # engine frame 502 has no recorded image: no media
    bad = [{"seq": 0, "engine_frame": 500, "png": "frames/frame_00000.png"}]
    Image.new("RGB", (240, 100)).save(bundle / "frames" / "frame_00000.png")
    (bundle / "manifest.json").write_text(json.dumps({"frames": bad}))
    with pytest.raises(ValueError, match="not the export raster"):
        m.attach_f9_pngs(m.load_frames(path), bundle)


def page_frame(seq, chars, rows, width=40, advance=11):
    obj = frame(seq, chars)
    obj["page"] = {"extent_px": [width, PITCH * len(rows)], "full_advance_px": advance,
                   "glyph_only": False, "rows": rows}
    return obj


def test_page_transport_is_validated_against_row_ends(tmp_path):
    # Row 0 ends at 45: cells cover [45, 60) on the 11 px lattice -> 2 cells.
    ok = m.parse_frame(page_frame(1, [(ord("/"), 40, 0, 5)], [[45, [1, 2]], [0, [3, 4, 5, 6, 7, 8]]],
                                  width=60), tmp_path, None)
    assert ok.page == ((45, (1, 2)), (0, (3, 4, 5, 6, 7, 8)))
    with pytest.raises(ValueError, match="starts at"):
        m.parse_frame(page_frame(1, [(ord("/"), 40, 0, 5)], [[0, [1] * 6], [0, [1] * 6]], width=60),
                      tmp_path, None)
    with pytest.raises(ValueError, match="cells"):
        m.parse_frame(page_frame(1, [(ord("/"), 40, 0, 5)], [[45, [1]], [0, [1] * 6]], width=60),
                      tmp_path, None)
    assert m.parse_frame(frame(1, [(ord("/"), 40, 0, 5)]), tmp_path, None).page is None


def test_same_state_compare_includes_page_paper(tmp_path):
    chars = [(ord("/"), 0, 0, 5)]
    a = m.parse_frame(page_frame(1, chars, [[5, [9, 9, 9]]], width=30), tmp_path, None)
    b = m.parse_frame(page_frame(2, chars, [[5, [9, 8, 9]]], width=30), tmp_path, None)
    summary, events = m.compare_frames(a, a)
    assert summary["identical"] and summary["page_compared"]
    summary, events = m.compare_frames(a, b)
    assert not summary["identical"] and summary["red"] == 0 and summary["page_rows_differ"] == 1
    assert events == [{"row": 0, "side": "page", "colour": "red", "a": [9, 9, 9], "b": [9, 8, 9]}]


def test_row_losses_carry_the_exported_omission_reasons(tmp_path):
    # FL-4512: the True FF export carries y totals, the w reason split and the
    # inexact-row count per frame; the summary totals them. Older frames
    # without loss facts keep row_losses None.
    chars = [(ord("|"), 11, 0, 4)]
    plain = [frame(i, chars) for i in range(3)]
    summary, _ = run(tmp_path, plain)
    assert summary["row_losses"] is None
    reported = []
    for i, (rep, fit, solve, inexact, flags) in enumerate([(1, 2, 3, 0, 0), (0, 0, 5, 2, 4), (4, 0, 0, 1, 5)]):
        obj = frame(i, chars)
        obj.update({"unscheduled": rep + fit + solve, "budget_flags": flags, "inexact_rows": inexact,
                    "omissions": {"no_representation": rep, "no_fitted_origin": fit, "solve": solve},
                    "requests": {"published": 10, "overflow": 0}})
        reported.append(obj)
    summary, _ = run(tmp_path, reported)
    losses = summary["row_losses"]
    assert losses["frames_reporting"] == 3 and losses["frames_with_reasons"] == 3
    assert losses["totals"] == {"unscheduled": 15, "no_representation": 5, "no_fitted_origin": 2,
                                "solve": 8, "inexact_rows": 3, "published": 30, "overflow": 0}
    assert losses["per_frame_mean"]["solve"] == 8 / 3
    assert losses["budget_flag_frames"] == {"ink": 1, "occupancy": 0, "run": 2}
    # Reason fields always sum to the omitted supports the exporter reports.
    assert sum(losses["totals"][r] for r in m.LOSS_REASONS) == losses["totals"]["unscheduled"]


def _site_frame(seq, entries):
    # entries: (site, cp, x, row_top)
    glyphs = [m.Glyph(cp, x, y, 8, 0, 0, site, False) for site, cp, x, y in entries]
    return m.Frame(seq, 17, glyphs, None, (0.0, 0.0), 1.0)


def test_site_identity_sorts_kept_changed_relabel_vanish():
    # FL-4512: attribution by exported site id across one frame pair.
    a = _site_frame(0, [(1, ord("_"), 10, 0), (2, ord("/"), 40, 0),
                        (3, ord("|"), 70, 17), (4, ord("-"), 100, 34)])
    b = _site_frame(1, [(1, ord("_"), 12, 0),      # kept (same id, same text)
                        (2, ord("|"), 40, 0),      # changed representation
                        (9, ord("|"), 75, 17),     # site 3 relabelled in place
                        (8, ord("-"), 300, 34)])   # site 4 gone; 8 far away
    summary = m.summarize_identity([a, b], 11)
    assert summary["totals"] == {"kept": 1, "changed": 1, "relabel": 1, "vanish": 1, "appeared": 2}
    assert summary["fractions"]["vanish"] == 0.25


def test_site_identity_absent_without_site_words():
    a = _site_frame(0, [(0, ord("_"), 10, 0)])
    b = _site_frame(1, [(0, ord("_"), 10, 0)])
    assert m.summarize_identity([a, b], 11) is None

"""FL-4873 OPERATOR_GIF_DELIVERABLE_SPEC_20260926: f9_before_after emits only titled media.

Every GIF frame (and every lossless frame PNG) must carry a burned-in title band
with proof, presentation mode, projection, commit, before|after and frame index,
taken from each side's own bundle manifest. Checked here by the pixels of the
title band and by the per-frame titles sidecar. Synthetic tiny bundles only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import f9_before_after as ba  # noqa: E402
from test_f9_before_after import PROOF_ID, STEM, make_bundle, make_replay, write_session  # noqa: E402

N_WINDOW = 4  # write_session references seqs 3..6


@pytest.fixture
def before(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [1] * 10)
    write_session(b, "B")
    return b


def argv(before, tmp_path, *extra):
    return [str(before), "--out", str(tmp_path / "out"), "--no-video", "--repo-root", str(tmp_path), *extra]


def run(args):
    return ba.run(ba.build_parser().parse_args(args), args)


def edit_manifest(bundle: Path, fn) -> None:
    doc = json.loads((bundle / "manifest.json").read_text())
    fn(doc)
    (bundle / "manifest.json").write_text(json.dumps(doc))


def band_is_title(frame: np.ndarray, box) -> bool:
    """Independent of the producer: band colour dominates and white text is present."""
    x0, y0, x1, y1 = box
    band = frame[y0:y1, x0:x1].astype(int)
    if band.size == 0 or y1 - y0 < 12:
        return False
    bg = np.all(np.abs(band - np.array(ba.TITLE_BAND_BG)) <= 6, axis=2).mean()
    text = np.all(band >= 200, axis=2).sum()
    return bg >= 0.5 and text >= 20


def gif_frames(path: Path) -> list[np.ndarray]:
    with Image.open(path) as im:
        out = []
        for i in range(im.n_frames):
            im.seek(i)
            out.append(np.asarray(im.convert("RGB")))
    return out


def assert_every_gif_titled(out: Path) -> list[Path]:
    gifs = sorted(out.glob("*.gif"))
    assert gifs
    for gif in gifs:
        sidecar_path = ba.titles_sidecar_path(gif)
        assert sidecar_path.is_file(), f"{gif.name} has no titles sidecar"
        sidecar = json.loads(sidecar_path.read_text())
        frames = gif_frames(gif)
        assert len(frames) == len(sidecar["frames"]) == N_WINDOW, gif.name
        for i, (frame, record) in enumerate(zip(frames, sidecar["frames"])):
            assert record["panes"], f"{gif.name} frame {i}: no pane"
            for pane in record["panes"]:
                assert set(pane["fields"]) == set(ba.TITLE_FIELDS)
                assert all(pane["fields"][f] for f in ba.TITLE_FIELDS)
                assert pane["fields"]["frame"] == f"{i + 1}/{N_WINDOW}"
                assert band_is_title(frame, pane["band_box"]), f"{gif.name} frame {i} lacks the title band"
    return gifs


def test_every_gif_frame_carries_the_six_field_title(before, tmp_path):
    after = make_replay(tmp_path / "A", before, list(range(2, 9)), [1] * 7)
    m = run(argv(before, tmp_path, "--after", str(after), "--context-cells", "1"))
    out = tmp_path / "out"
    gifs = {g.name for g in assert_every_gif_titled(out)}
    assert gifs == {f"{STEM}_{part}.gif" for part in ("before", "after", "side_by_side", "before_x3", "after_x3",
                                                       "side_by_side_x3")}
    sbs = json.loads((out / f"{STEM}_side_by_side.titles.json").read_text())
    b, a = sbs["frames"][0]["panes"]
    # Facts from each side's own manifest: the after commit is the replay recording's.
    assert b["fields"] == {"proof": PROOF_ID, "mode": "FGONLY", "projection": "perspective",
                           "commit": "abcb123-dirty", "role": "before", "frame": "1/4"}
    assert a["fields"]["commit"] == "abcr123-dirty" and a["fields"]["role"] == "after"
    assert b["sources"]["proof"] == "manifest.json:identity.proof_id"
    assert b["sources"]["commit"] == "manifest.json:identity.source.run_identity.label"
    assert b["sources"]["mode"] == "manifest.json:frames[].f10_view=1"
    assert b["seq"] == 3 and a["recorded_seq"] == 3 and a["seq"] == 1
    # side by side: before pane left of after pane, both titled
    assert b["band_box"][2] <= a["band_box"][0]
    # native crop kept 1:1 below the band (palette exact on these frames)
    x0, y0, x1, y1 = json.loads((out / f"{STEM}_before.titles.json").read_text())["frames"][0]["panes"][0]["source_box"]
    src = np.asarray(Image.open(before / "frames" / "frame_00003.png").convert("RGB"))[0:14, 2:10]
    assert np.array_equal(gif_frames(out / f"{STEM}_before.gif")[0][y0:y1, x0:x1], src)
    # manifest carries the per-frame title fields and names every sidecar
    assert [t["frame"] for t in m["titles"]["before"]] == ["1/4", "2/4", "3/4", "4/4"]
    assert f"{STEM}_side_by_side.titles.json" in m["titles"]["sidecars"]
    durable = Path(m["_durable"])
    assert (durable / f"{STEM}_side_by_side.gif").is_file()
    assert (durable / f"{STEM}_side_by_side.titles.json").is_file()


def test_write_titled_gif_refuses_a_missing_title_field(tmp_path):
    title = {"fields": {"proof": PROOF_ID, "mode": "FF", "projection": "perspective", "commit": "c",
                        "role": "before", "frame": "1/1"},
             "mode_base": "FF", "seq": 0, "recorded_seq": 0, "role_label": "BEFORE", "capture": "x",
             "git_head": None, "sources": {}}
    crop = np.full((40, 40, 3), 90, np.uint8)
    title["fields"]["commit"] = ""
    with pytest.raises(ba.Refused) as exc:
        ba.write_titled_gif(tmp_path / "y.gif", [[(crop, title)]], [3])
    assert exc.value.code == "title_field_missing" and not (tmp_path / "y.gif").exists()


@pytest.mark.parametrize("edit, extra, code", [
    (lambda d: d["identity"].pop("source"), [], "title_commit_missing"),
    (lambda d: d["identity"].pop("proof_id"), [], "title_proof_missing"),
    (lambda d: d["identity"].update(proof_id="route.not_in_registry"), [], "title_proof_unregistered"),
    (lambda d: [d["identity"].pop("projection")] + [f.pop("projection") for f in d["frames"]], [],
     "title_projection_missing"),
])
def test_missing_or_contradicted_title_field_refuses_before_any_gif(before, tmp_path, edit, extra, code):
    edit_manifest(before, edit)
    with pytest.raises(ba.Refused) as exc:
        run(argv(before, tmp_path, "--after-self", *extra))
    assert exc.value.code == code
    assert not list((tmp_path / "out").glob("**/*.gif")) and not list((tmp_path / "out").glob("**/*.png"))


def test_mode_missing_refuses(tmp_path):
    b = make_bundle(tmp_path / "B", "B", list(range(10)), [0] * 10)
    write_session(b, "B")
    edit_manifest(b, lambda d: [f.pop("mode") for f in d["frames"]])
    with pytest.raises(ba.Refused) as exc:
        run(argv(b, tmp_path, "--after-self"))
    assert exc.value.code == "title_mode_missing"


def test_normal_view_titles_the_presentation_mode(before, tmp_path):
    # NORMAL view frames title their FF/FC/CF presentation mode, not FGONLY
    b = make_bundle(tmp_path / "N", "N", list(range(10)), [0] * 10)
    write_session(b, "N")
    edit_manifest(b, lambda d: [f.update(mode="CF") for f in d["frames"]])
    m = run([str(b), "--out", str(tmp_path / "out2"), "--no-video", "--repo-root", str(tmp_path), "--after-self"])
    assert {t["mode"] for t in m["titles"]["before"]} == {"CF"}


def test_lossless_titled_frame_pngs_and_html(before, tmp_path):
    m = run(argv(before, tmp_path, "--after-self", "--context-cells", "0"))
    out = tmp_path / "out"
    for role in ("before", "after"):
        doc = json.loads((out / "frames" / role / "titles.json").read_text())
        assert len(doc["frames"]) == N_WINDOW
        for i, row in enumerate(doc["frames"]):
            pixels = np.asarray(Image.open(out / row["png"]).convert("RGB"))
            pane = row["panes"][0]
            assert pane["fields"]["role"] == role and pane["fields"]["frame"] == f"{i + 1}/{N_WINDOW}"
            assert band_is_title(pixels, pane["band_box"])
            x0, y0, x1, y1 = pane["source_box"]
            src = np.asarray(Image.open(before / "frames" / f"frame_{3 + i:05d}.png").convert("RGB"))[4:12, 4:8]
            assert np.array_equal(pixels[y0:y1, x0:x1], src)  # lossless, native 1:1
    html_name = f"{STEM}_{ba.HTML_NAME}"
    html = (out / html_name).read_text()
    for name in m["outputs"]:
        if name.endswith((".gif", ".titles.json", ".png")) or name.endswith("titles.json"):
            assert f"'{name}'" in html, name
    assert f"'{ba.MANIFEST_NAME}'" in html and html_name in m["outputs"]

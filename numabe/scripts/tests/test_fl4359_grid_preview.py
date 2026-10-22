"""FF-DPR-20260920-60B preview renderer tests.

Builds a synthetic 3-frame FFG1 grid directory through the documented
FFG1 writer semantics (magic, version, schemas, header, nine lanes),
renders it, and asserts PNGs, contact sheet, GIF, text JSON, and meter
numbers. No Godot launch; Pillow only.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_fl4359_grid_preview import (  # noqa: E402
    glyph_char,
    id_to_scalar,
    read_grid_frame,
    render_sequence,
)

LANE_ORDER = ["glyphs", "roles", "tags", "elected", "ink", "paper",
              "chains", "advances", "refuse"]
INT_LANES = {"glyphs", "roles", "chains", "advances", "refuse"}


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _str_bytes(text: str) -> bytes:
    raw = text.encode("ascii")
    return _u32(len(raw)) + raw


def write_ffg1(path: Path, frame: int, cols: int, rows: int,
               glyphs: list[int], roles: list[int]) -> None:
    """Minimal FFG1 writer following the -60A layout: white ink, black
    paper, chain 0, no refusals, keyframe tags on inked cells."""
    n = cols * rows
    out = bytearray()
    out += b"FFG1"
    out += _u32(1)
    out += _str_bytes("fl4359.final_glyph_result_v2")
    out += _u32(frame) + _u32(0) + _u32(cols) + _u32(rows)
    lanes: dict[str, bytes] = {}
    lanes["glyphs"] = struct.pack(f"<{n}i", *glyphs)
    lanes["roles"] = struct.pack(f"<{n}i", *roles)
    lanes["tags"] = bytes(1 if g != 32 else 0 for g in glyphs)
    lanes["elected"] = bytes([1]) * n
    lanes["ink"] = bytes([255, 255, 255, 255]) * n
    lanes["paper"] = bytes([0, 0, 0, 255]) * n
    lanes["chains"] = struct.pack(f"<{n}i", *([0] * n))
    lanes["advances"] = struct.pack(f"<{n}i", *([1] * n))
    lanes["refuse"] = struct.pack(f"<{n}i", *([0] * n))
    for lane in LANE_ORDER:
        out += _u32(len(lanes[lane])) + lanes[lane]
    path.write_bytes(bytes(out))


@pytest.fixture()
def three_frames(tmp_path: Path) -> Path:
    """3 frames on 8x4: a rising stroke sliding one cell per frame."""
    grids = tmp_path / "grids"
    grids.mkdir()
    rows = ["        "] * 4
    for f in range(3):
        glyphs = [32] * 32
        roles = [0] * 32
        for k in range(4):
            x = f + k
            y = 3 - k
            if 0 <= x < 8:
                glyphs[y * 8 + x] = 45 + (k % 2) * 50
                roles[y * 8 + x] = 2
        write_ffg1(grids / f"frame_{f:05d}.bin", f, 8, 4, glyphs, roles)
    return grids


def test_grid_roundtrip(three_frames: Path) -> None:
    decoded = read_grid_frame(three_frames / "frame_00001.bin")
    assert decoded["header"]["frame"] == 1
    assert decoded["header"]["cols"] == 8
    assert decoded["lanes"]["glyphs"][2 * 8 + 2] == 95


def test_glyph_char_mapping() -> None:
    table = {552: 180, 250: 183, 1686: 183}
    assert glyph_char(95, table) == "_"
    assert glyph_char(32, table) == " "
    assert glyph_char(552, table) == "\u00b4"
    assert glyph_char(250, table) == "\u00b7"
    assert id_to_scalar({"resolution": {"180": [552]}}) == {552: 180}


def test_render_sequence_outputs(three_frames: Path, tmp_path: Path) -> None:
    out = tmp_path / "preview"
    result = render_sequence(three_frames, out, "synth")
    pngs = sorted(out.glob("frame_*.png"))
    assert len(pngs) == 3
    with Image.open(pngs[1]) as image:
        assert image.size[0] <= 1920
        assert image.size[1] > 0
    assert Path(result["contact"]).exists()
    assert Path(result["gif"]).exists()
    text = json.loads(Path(result["text"]).read_text(encoding="utf-8"))
    assert text["cols"] == 8 and text["rows"] == 4
    assert len(text["frames"]) == 3
    assert all(len(row) == 8 for row in text["frames"][0])
    assert text["frames"][1][3][1] == "-"
    assert text["frames"][1][0][4] == "_"
    meter = result["meter"]
    assert set(meter) == {"density_median", "changed_median", "flips_median"}
    assert meter["density_median"] > 0.0


def test_preview_preserves_source_glyph_identity(tmp_path: Path) -> None:
    """The preview is an evidence surface, not a glyph repair pass."""
    grids = tmp_path / "identity-grids"
    grids.mkdir()
    write_ffg1(grids / "frame_00000.bin", 0, 3, 1,
               [58, 45, 32], [1, 1, 0])
    out = tmp_path / "identity-preview"
    result = render_sequence(grids, out, "identity")
    text = json.loads(Path(result["text"]).read_text(encoding="utf-8"))
    assert text["frames"] == [[":- "]]


def test_bad_grid_refused(tmp_path: Path) -> None:
    bad = tmp_path / "frame_00000.bin"
    bad.write_bytes(b"NOPE" + bytes(64))
    with pytest.raises(ValueError, match="bad grid magic"):
        read_grid_frame(bad)

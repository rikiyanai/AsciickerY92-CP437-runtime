"""FF-DPR-20260920-60B: compositor-independent preview of solved Line grids.

Reads an FFG1 grids directory (FF-DPR-60A), renders each frame with a
monospace Mac font (Menlo.ttc; line ink on paper per the grid lanes),
and writes frame PNGs, a first/middle/last contact sheet, a cropped GIF,
and a JSON array-of-strings text representation into the out dir. Meter
numbers come from the FF-DPR-56A checker path (measure_f10_churn.py over
the rendered PNGs). Contact-sheet and GIF helpers are reused from
scripts/check_fl4359_line_recording.py, never forked.

Usage:
    python3 scripts/render_fl4359_grid_preview.py \
        --grids-dir <grids> --out-dir <preview> [--name NAME]
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_fl4359_line_recording import (  # noqa: E402
    run_meter,
    write_contact_sheet,
    write_preview_gif,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    REPO_ROOT / "godot_project" / "render" / "ascii" / "glyphs"
    / "style_profile_default.json"
)

GRID_MAGIC = b"FFG1"
GRID_VERSION = 1
GRID_SCHEMA = "fl4359.final_glyph_result_v2"
LANE_ORDER = ["glyphs", "roles", "tags", "elected", "ink", "paper",
              "chains", "advances", "refuse"]
INT_LANES = {"glyphs", "roles", "chains", "advances", "refuse"}

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
FONT_INDEX = 0
MAX_IMAGE_WIDTH = 1920


def read_grid_frame(path: Path) -> dict:
    """Decode one FFG1 frame into header plus nine lanes."""
    raw = path.read_bytes()
    off = 0
    magic = raw[off:off + 4]
    off += 4
    if magic != GRID_MAGIC:
        raise ValueError(f"{path}: bad grid magic {magic!r}")
    (version,) = struct.unpack_from("<I", raw, off)
    off += 4
    if version != GRID_VERSION:
        raise ValueError(f"{path}: unknown grid version {version}")
    (slen,) = struct.unpack_from("<I", raw, off)
    off += 4
    schema = raw[off:off + slen].decode("ascii")
    off += slen
    if schema != GRID_SCHEMA:
        raise ValueError(f"{path}: unknown grid schema {schema}")
    frame, epoch, cols, rows = struct.unpack_from("<IIII", raw, off)
    off += 16
    lanes: dict[str, object] = {}
    for lane in LANE_ORDER:
        (nbytes,) = struct.unpack_from("<I", raw, off)
        off += 4
        payload = raw[off:off + nbytes]
        off += nbytes
        if len(payload) != nbytes:
            raise ValueError(f"{path}: short lane {lane}")
        if lane in INT_LANES:
            count = nbytes // 4
            lanes[lane] = list(struct.unpack(f"<{count}i", payload))
        else:
            lanes[lane] = list(payload)
    header = {"frame": frame, "epoch": epoch, "cols": cols, "rows": rows}
    want = cols * rows
    for lane in LANE_ORDER:
        expect = want * (4 if lane in ("ink", "paper") else 1)
        if len(lanes[lane]) != expect:  # type: ignore[arg-type]
            raise ValueError(f"{path}: lane {lane} size mismatch")
    return {"header": header, "lanes": lanes}


def id_to_scalar(profile: dict) -> dict[int, int]:
    """Atlas id to code point via the profile resolution table; identity
    below 256 (CP437 page 0), matching StyleProfileAdmission."""
    table: dict[int, int] = {}
    for scalar, ids in profile.get("resolution", {}).items():
        for atlas_id in ids:
            table[int(atlas_id)] = int(scalar)
    return table
def glyph_char(glyph: int, table: dict[int, int]) -> str:
    """Atlas id to one printable cell character."""
    scalar = table.get(glyph, glyph if 0 <= glyph < 256 else 0xFFFD)
    try:
        char = chr(scalar)
    except (ValueError, OverflowError):
        return "?"
    return char if char.isprintable() else "?"


def pick_cell_size(cols: int, rows: int) -> tuple[object, int, int]:
    """Largest Menlo size whose advance fits MAX_IMAGE_WIDTH / cols."""
    index = 0
    for size in range(28, 5, -1):
        font = ImageFont.truetype(FONT_PATH, size, index=FONT_INDEX)
        advance = font.getlength("M")
        ascent, descent = font.getmetrics()
        cell_w = max(1, int(round(advance)))
        cell_h = max(1, ascent + descent)
        if cols * cell_w <= MAX_IMAGE_WIDTH:
            return font, cell_w, cell_h
    font = ImageFont.truetype(FONT_PATH, 6, index=FONT_INDEX)
    return font, max(1, int(round(font.getlength("M")))), sum(font.getmetrics())


def render_frame(lanes: dict, cols: int, rows: int, table: dict[int, int],
                 font: object, cell_w: int, cell_h: int) -> tuple[Image.Image, list[str]]:
    """One frame image plus its row strings. Ink and paper come straight
    from the grid lanes (RGBA quads); glyphs resolve through the profile."""
    ink = lanes["ink"]
    paper = lanes["paper"]
    glyphs = lanes["glyphs"]
    image = Image.new("RGB", (cols * cell_w, rows * cell_h), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    text_rows: list[str] = []
    for y in range(rows):
        chars: list[str] = []
        for x in range(cols):
            i = y * cols + x
            bg = (paper[4 * i], paper[4 * i + 1], paper[4 * i + 2])
            fg = (ink[4 * i], ink[4 * i + 1], ink[4 * i + 2])
            draw.rectangle([x * cell_w, y * cell_h,
                            (x + 1) * cell_w - 1, (y + 1) * cell_h - 1],
                           fill=bg)
            char = glyph_char(int(glyphs[i]), table)
            chars.append(char)
            if char != " ":
                draw.text((x * cell_w + cell_w / 2, y * cell_h),
                          char, font=font, fill=fg, anchor="ma")
        text_rows.append("".join(chars))
    return image, text_rows


def render_sequence(grids_dir: Path, out_dir: Path, name: str) -> dict:
    """Render every frame_NNNNN.bin; write PNGs, contact sheet, GIF, text
    JSON; print the 56A meter numbers. Returns paths plus metrics."""
    paths = sorted(grids_dir.glob("frame_*.bin"))
    if not paths:
        raise SystemExit(f"error: no frame_*.bin in {grids_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    table = id_to_scalar(profile)
    first = read_grid_frame(paths[0])
    cols = int(first["header"]["cols"])
    rows = int(first["header"]["rows"])
    font, cell_w, cell_h = pick_cell_size(cols, rows)
    pngs: list[Path] = []
    text_frames: list[list[str]] = []
    for path in paths:
        decoded = read_grid_frame(path)
        header = decoded["header"]
        if int(header["cols"]) != cols or int(header["rows"]) != rows:
            raise SystemExit(f"error: grid change mid-sequence at {path}")
        image, text_rows = render_frame(decoded["lanes"], cols, rows,
                                        table, font, cell_w, cell_h)
        png_path = out_dir / f"{path.stem}.png"
        image.save(png_path)
        pngs.append(png_path)
        text_frames.append(text_rows)
    (out_dir / "frames.json").write_text(
        json.dumps({"cols": cols, "rows": rows, "frames": text_frames}),
        encoding="utf-8")
    contact_path = out_dir / f"contact_{name}.png"
    write_contact_sheet(pngs, contact_path)
    gif_path = out_dir / f"preview_{name}.gif"
    write_preview_gif(pngs, gif_path, fps=8.0)
    meter = run_meter(out_dir, cols, rows)
    print(f"PREVIEW frames={len(pngs)} grid={cols}x{rows} "
          f"cell={cell_w}x{cell_h} font=Menlo.ttc")
    print(f"PREVIEW density={meter['density_median']:.4f} "
          f"changed={meter['changed_median']:.4f} "
          f"flips={meter['flips_median']:.4f}")
    return {"pngs": [str(p) for p in pngs],
            "contact": str(contact_path), "gif": str(gif_path),
            "text": str(out_dir / "frames.json"),
            "meter": {k: meter[k] for k in
                      ("density_median", "changed_median", "flips_median")}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grids-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--name", default="grid")
    args = parser.parse_args(argv)
    result = render_sequence(Path(args.grids_dir), Path(args.out_dir),
                             args.name)
    print(f"PREVIEW contact={result['contact']} gif={result['gif']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

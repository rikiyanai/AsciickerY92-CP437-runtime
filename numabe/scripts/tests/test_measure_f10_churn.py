"""Tests for scripts/measure_f10_churn.py.

Synthetic fixtures are generated into tmp_path (no manifest.txt, so the
sorted frame_*.png fallback is exercised too):
- static/: two identical frames with one inked cell -> changed 0.
- aba/: A,A,B,A over one cell -> the A-B-A return counts one flip cell.
- blank/: one all-black frame -> density 0.
"""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "measure_f10_churn.py"
COLS, ROWS = 4, 3
CELL_W, CELL_H = 16, 32
N_CELLS = COLS * ROWS
ONE_CELL = round(1 / N_CELLS, 4)


def make_frame(path: Path, blocks) -> None:
    """Write a frame with the given (x, y, w, h) white blocks on black."""
    im = Image.new("RGB", (COLS * CELL_W, ROWS * CELL_H), (0, 0, 0))
    white = Image.new("RGB", (1, 1), (255, 255, 255))
    for x, y, w, h in blocks:
        im.paste(white.resize((w, h)), (x, y))
    im.save(path)


def run_tool(frames_dir: Path, *extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(frames_dir),
         "--cols", str(COLS), "--rows", str(ROWS),
         "--component-frame", "0", *extra],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_static_pair_has_zero_churn(tmp_path: Path) -> None:
    frames = tmp_path / "static"
    frames.mkdir()
    make_frame(frames / "frame_00000.png", [(0, 0, 2, 2)])
    make_frame(frames / "frame_00001.png", [(0, 0, 2, 2)])
    result = run_tool(frames)
    assert result["frames"] == 2
    assert result["changed"]["max"] == 0
    assert result["flip"]["max"] == 0


def test_aba_return_counts_one_flip_cell(tmp_path: Path) -> None:
    frames = tmp_path / "aba"
    frames.mkdir()
    pat_a = [(0, 0, 2, 2)]
    pat_b = [(2, 0, 2, 2)]
    make_frame(frames / "frame_00000.png", pat_a)
    make_frame(frames / "frame_00001.png", pat_a)
    make_frame(frames / "frame_00002.png", pat_b)
    make_frame(frames / "frame_00003.png", pat_a)
    result = run_tool(frames)
    assert result["frames"] == 4
    assert result["flip"]["max"] == ONE_CELL
    assert result["components"]["nonblank_cells"] == 1
    assert result["components"]["components"] == 1


def test_blank_frame_has_zero_density(tmp_path: Path) -> None:
    frames = tmp_path / "blank"
    frames.mkdir()
    make_frame(frames / "frame_00000.png", [])
    result = run_tool(frames)
    assert result["frames"] == 1
    assert result["density"]["min"] == 0
    assert result["density"]["max"] == 0


def test_raster_pitch_keeps_clipped_last_row_cells_in_place(tmp_path: Path) -> None:
    """FL-4873: a manifest pitch that reproduces the matrix is used as-is.

    Image 64x80 with 16x32 cells is a 4x3 grid whose last row is 16 px. Ink at
    y 34..40 and at y 54..60 both belong to raster row 1 (32..63), so moving
    between them changes one cell. The proportional partition (26.7 px rows)
    would put y 54..60 into row 2 and report two changed cells.
    """
    frames = tmp_path / "raster"
    frames.mkdir()
    for index, top in enumerate((34, 54)):
        im = Image.new("RGB", (64, 80), (0, 0, 0))
        im.paste(Image.new("RGB", (4, 6), (255, 255, 255)), (2, top))
        im.save(frames / f"frame_{index:05d}.png")
    lines = [f"frame: seq={i} cell=16x32:terminal matrix=[4,3] image=64x80 png=frame_{i:05d}.png"
             for i in range(2)]
    (frames / "manifest.txt").write_text("\n".join(lines) + "\n")
    result = run_tool(frames)
    assert result["changed"]["max"] == ONE_CELL

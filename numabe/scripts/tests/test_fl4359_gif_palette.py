"""GIF encoding must not repaint an unchanged RGB input across frames."""
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_stitch_sweep_gif import _write_gif


def test_shared_rgb_patch_keeps_its_encoded_colours_as_surroundings_change(tmp_path):
    rng = np.random.default_rng(4359)
    common = rng.integers(0, 256, (16, 64, 3), dtype=np.uint8)
    frames = []
    for index in range(3):
        pixels = rng.integers(0, 80, (64, 64, 3), dtype=np.uint8)
        pixels[..., index] += 170
        pixels[:16] = common
        frames.append(Image.fromarray(pixels))
    original = [frame.tobytes() for frame in frames]
    path = tmp_path / "motion.gif"
    _write_gif(frames, path, 60)
    decoded = []
    with Image.open(path) as gif:
        assert gif.n_frames == 3
        assert gif.size == (64, 64)
        for index in range(3):
            gif.seek(index)
            assert gif.info["duration"] == 60
            decoded.append(np.asarray(gif.convert("RGB")))
    for frame in decoded[1:]:
        np.testing.assert_array_equal(frame[:16], decoded[0][:16])
        assert np.any(frame[16:] != decoded[0][16:])
    assert [frame.tobytes() for frame in frames] == original


def test_colour_mapping_does_not_depend_on_pixel_position(tmp_path):
    pixels = np.random.default_rng(9).integers(0, 256, (128, 128, 3), dtype=np.uint8)
    frames = [Image.fromarray(pixels), Image.fromarray(pixels[::-1, ::-1])]
    path = tmp_path / "translated.gif"
    _write_gif(frames, path, [60, 120])
    with Image.open(path) as gif:
        before = np.asarray(gif.convert("RGB"))
        assert gif.info["duration"] == 60
        gif.seek(1)
        after = np.asarray(gif.convert("RGB"))
        assert gif.info["duration"] == 120
    np.testing.assert_array_equal(before, after[::-1, ::-1])


def test_rejects_empty_or_mismatched_frames(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        _write_gif([], tmp_path / "empty.gif", 60)
    with pytest.raises(ValueError, match="matching dimensions"):
        _write_gif([Image.new("RGB", (8, 8)), Image.new("RGB", (8, 9))],
                   tmp_path / "mismatch.gif", 60)

"""
angle_synthesis.py -- Generate missing angle variants via horizontal mirroring.

ARCHITECTURE:
    Produces a sprite sheet with more angle rows from a source sheet with fewer,
    using horizontal mirroring to synthesize the intermediate angles.  This is
    a pre-slicing stage: the sheet is still a raw pixel image, not yet broken
    into per-frame tiles.

    [FLOW:SYNTHESIS] Data flow:
        PNG sheet (N angles) --> synthesize_angles() --> PNG sheet (M angles)

KEY EXPORTS:
    - synthesize_angles: Main function -- doubles/multiplies angle count

ENGINE CONVENTION:
    Angles are uniformly spaced around 360 degrees.
    Engine angle index = floor((direction_degrees - camera_yaw) * angles / 360 + 0.5)

    For 4-to-8 synthesis (the primary use case):
        output[0] = input[0]          (0 deg, direct copy)
        output[1] = hmirror(input[3]) (45 deg, mirror=315, source=3 at 270 deg)
        output[2] = input[1]          (90 deg, direct copy)
        output[3] = hmirror(input[2]) (135 deg, mirror=225, source=2 at 180 deg)
        output[4] = input[2]          (180 deg, direct copy)
        output[5] = hmirror(input[1]) (225 deg, mirror=135, source=1 at 90 deg)
        output[6] = input[3]          (270 deg, direct copy)
        output[7] = hmirror(input[0]) (315 deg, mirror=45, source=0 at 0 deg)

    General formula for ratio R = target / source:
        Even output indices: output[i] = input[i // R]  (direct copy)
        Odd output indices:  output[i] = hmirror(input[(target - i) * source // target % source])

PIPELINE CONTEXT:
    [PIPELINE:SYNTHESIS] Runs BEFORE reflection handling and BEFORE slicing.
    The synthesize_angles flag must be explicitly set (never implicit).
    [DEPENDENCY:PIL] Requires PIL/Pillow for image operations.
"""

from typing import List

from PIL import Image
import numpy as np


def synthesize_angles(
    image: Image.Image,
    source_angles: int,
    target_angles: int,
    anim_frames: List[int],
) -> Image.Image:
    """Generate a sprite sheet with more angle rows via horizontal mirroring.

    [FLOW:SYNTHESIS] Takes a sheet with ``source_angles`` rows and produces a
    sheet with ``target_angles`` rows.  Original angles are kept at even
    indices; synthesized intermediate angles are placed at odd indices using
    horizontal mirror of the appropriate source row.

    Args:
        image: Source sprite sheet (PIL Image, RGB or RGBA).
        source_angles: Number of angle rows in the source sheet.
        target_angles: Desired number of angle rows in the output.
        anim_frames: Per-animation frame counts (e.g. [1, 4, 2]).

    Returns:
        New PIL Image with ``target_angles`` rows.  Width is preserved.

    Raises:
        ValueError: If source_angles <= 0, target < source, or target is
            not an integer multiple of source.
    """
    # --- Input validation --------------------------------------------------
    if source_angles <= 0:
        raise ValueError(
            f"source_angles must be positive, got {source_angles}"
        )

    if target_angles < source_angles:
        raise ValueError(
            f"target_angles ({target_angles}) cannot reduce below "
            f"source_angles ({source_angles})"
        )

    if target_angles % source_angles != 0:
        raise ValueError(
            f"target_angles ({target_angles}) must be an integer multiple "
            f"of source_angles ({source_angles}), but "
            f"{target_angles} % {source_angles} = {target_angles % source_angles}"
        )

    # --- No-op fast path ---------------------------------------------------
    if target_angles == source_angles:
        return image.copy()

    # --- Compute row mapping -----------------------------------------------
    w, h = image.size
    row_h = h // source_angles

    src_arr = np.array(image)
    channels = src_arr.shape[2] if src_arr.ndim == 3 else 1

    out_h = target_angles * row_h
    if channels > 1:
        out_arr = np.zeros((out_h, w, channels), dtype=np.uint8)
    else:
        out_arr = np.zeros((out_h, w), dtype=np.uint8)

    ratio = target_angles // source_angles

    for i in range(target_angles):
        y_start = i * row_h
        y_end = (i + 1) * row_h

        if i % ratio == 0:
            # Even output index: direct copy from input[i // ratio]
            src_idx = i // ratio
            src_y_start = src_idx * row_h
            src_y_end = (src_idx + 1) * row_h
            out_arr[y_start:y_end] = src_arr[src_y_start:src_y_end]
        else:
            # Odd output index: horizontal mirror of the corresponding
            # source row determined by the degree-based mapping.
            #
            # The supplementary angle in the target space is (target - i).
            # Map that back to source space: (target - i) * source // target
            # Then mod source to wrap around.
            mirror_src = (target_angles - i) * source_angles // target_angles % source_angles
            src_y_start = mirror_src * row_h
            src_y_end = (mirror_src + 1) * row_h
            # Horizontal flip: reverse the column axis
            out_arr[y_start:y_end] = src_arr[src_y_start:src_y_end, ::-1]

    mode = image.mode
    return Image.fromarray(out_arr, mode)

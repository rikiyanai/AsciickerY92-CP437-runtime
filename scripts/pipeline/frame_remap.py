"""Frame remap: animation-major to angle-major reorder.

ARCHITECTURE
------------
This module converts animation-major (row-major sheet extraction) frame
order to the engine's expected angle-major order.  It is the core
algorithmic component of Phase 5 (sheet orientation) and will be called
by the slicer after frame extraction (Plan 05-03).

Animation-major input layout (row-major extraction from sprite sheet)::

    Row 0: Anim0_Fr0 for Ang0, Ang1, ..., AngN   (cols = angles)
    Row 1: Anim1_Fr0 for Ang0, Ang1, ..., AngN
    Row 2: Anim1_Fr1 for Ang0, Ang1, ..., AngN
    ...

Angle-major output layout (engine expects)::

    Ang0: Anim0_Fr0, Anim1_Fr0, Anim1_Fr1, ...
    Ang1: Anim0_Fr0, Anim1_Fr0, Anim1_Fr1, ...
    ...

For ``input_idx = row * angles + col`` (row-major):
    ``output_idx = angle * sum(anim_counts) + cumulative_frame_offset``

KEY EXPORTS
~~~~~~~~~~~
- ``build_remap_table(angles, anim_counts)`` -- returns index mapping list
- ``remap_animation_major_to_angle_major(extracted, angles, anim_counts)``
  -- applies the table to reorder a frame list

Tags: [PIPELINE:SLICE] [FLOW:REMAP]
"""

from typing import List, TypeVar

T = TypeVar("T")


def build_remap_table(angles: int, anim_counts: List[int]) -> List[int]:
    """Build a remap table: ``result[output_idx] = input_idx``.

    Parameters
    ----------
    angles : int
        Number of rotation angles (columns in the sheet).  Must be >= 1.
    anim_counts : list of int
        Frame count per animation.  E.g. ``[1, 8]`` means idle (1 frame)
        + walk (8 frames).  Every element must be >= 1.

    Returns
    -------
    list of int
        A permutation table of length ``angles * sum(anim_counts)`` where
        ``table[output_idx]`` gives the input index to read from.

    Raises
    ------
    ValueError
        If ``angles < 1``, ``anim_counts`` is empty, or any count < 1.
    """
    if angles < 1:
        raise ValueError("animation_major requires angles >= 1")
    if not anim_counts or any(c < 1 for c in anim_counts):
        raise ValueError(
            "anim_counts must be non-empty with all elements >= 1, "
            f"got {anim_counts!r}"
        )

    total_frames_per_angle = sum(anim_counts)
    total_cells = angles * total_frames_per_angle
    remap = [0] * total_cells

    # Pre-compute cumulative offsets for each animation within one angle.
    # cum_offsets[i] = sum(anim_counts[:i])
    cum_offsets = []
    running = 0
    for count in anim_counts:
        cum_offsets.append(running)
        running += count

    for angle in range(angles):
        out_base = angle * total_frames_per_angle
        row_in_sheet = 0
        for anim_idx, count in enumerate(anim_counts):
            for frame_in_anim in range(count):
                input_idx = row_in_sheet * angles + angle
                output_idx = out_base + cum_offsets[anim_idx] + frame_in_anim
                remap[output_idx] = input_idx
                row_in_sheet += 1

    return remap


def remap_animation_major_to_angle_major(
    extracted: List[T],
    angles: int,
    anim_counts: List[int],
) -> List[T]:
    """Reorder frames from animation-major to angle-major order.

    Parameters
    ----------
    extracted : list
        Flat list of frame objects (PIL Images, strings, etc.) in
        animation-major (row-major sheet extraction) order.
    angles : int
        Number of rotation angles.
    anim_counts : list of int
        Frame count per animation.

    Returns
    -------
    list
        Frames reordered to angle-major layout.

    Raises
    ------
    ValueError
        If ``len(extracted) != angles * sum(anim_counts)``.
    """
    expected_len = angles * sum(anim_counts)
    if len(extracted) != expected_len:
        raise ValueError(
            f"extracted length {len(extracted)} does not match "
            f"angles({angles}) * sum(anim_counts)({expected_len // angles if angles else '?'}) "
            f"= {expected_len}"
        )

    table = build_remap_table(angles, anim_counts)
    return [extracted[table[i]] for i in range(len(table))]

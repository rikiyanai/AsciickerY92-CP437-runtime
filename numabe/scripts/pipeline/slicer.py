"""
ImageSlicer -- Animated multi-angle sprite sheet slicing.

ARCHITECTURE
------------
ImageSlicer is the Stage 2 component of the 4-stage sprite generation pipeline::

    [PIPELINE:GENERATE] -> **[PIPELINE:SLICE]** -> [PIPELINE:PROCESS] -> [PIPELINE:ASSEMBLE]

It receives a single composite PNG sprite sheet (all angles and all animation
frames packed into one image) and decomposes it into a flat list of individual
frame tiles.  Each tile is a PIL Image of exactly ``cell_width x cell_height``
pixels, ready for per-cell glyph matching in Stage 3 (processor.py).

The sheet layout is a uniform grid:
  - Rows    = angle views  (1, 4, or 8 as validated by AssetDef)
  - Columns = animation frames laid out sequentially across all animations

KEY EXPORTS
~~~~~~~~~~~
- ``ImageSlicer`` -- stateless slicer with two entry points:
    - ``slice()``       -- explicit angles/frames parameters
    - ``slice_image()`` -- backward-compatible wrapper with default 8-angle layout

PIPELINE CONTEXT
~~~~~~~~~~~~~~~~
- Consumed by: ``AssetPipeline.run()`` in pipeline.py after image generation/loading.
- Input:  A single PIL Image whose dimensions must be an exact integer multiple
  of (sum(frames) x angles).
- Output: ``List[Image]`` -- flat list of frame tiles in angle-major order.
  The ordering is critical: downstream XPAssembler expects frames grouped by
  angle, then by animation, then by frame index within each animation.

Data Contracts
~~~~~~~~~~~~~~
[DATA-CONTRACT:ASSET-DEF]  ``angles`` and ``frames`` come from AssetDef.
[DATA-CONTRACT:GRID]       Image dimensions must divide evenly into the grid;
                           fractional pixel sizes are rejected with ValueError.

Tags: [PIPELINE:SLICE] [DEPENDENCY:PIL]

Spec Compliance:
- Rows = angles (1, 4, or 8 from AssetDef.validate)
- Columns = animation-major ordering
- Total cells = angles x total_frames_per_angle
- Example: angles=8, frames=[1,8] -> 8 rows x 9 columns = 72 frames

Frame ordering:
- Loops: for each angle (row)
  - Loops: for each animation
    - Loops: for each frame

Result:
  Angle 0 x Anim 0 x Frame 0, Angle 0 x Anim 0 x Frame 1, ...
  Angle 1 x Anim 0 x Frame 0, Angle 1 x Anim 0 x Frame 1, ...
  ...
"""

import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image  # [DEPENDENCY:PIL] Pillow for image cropping operations

from scripts.pipeline.sprite_extract import segment_cell_region


def _spec_is_active(spec) -> bool:
    """A spec is active if ANY explicit field deviates from defaults.

    Used as the dispatch gate in slice(): when True, the spec-aware
    path (_slice_with_spec) is taken.  When False, the backward-
    compatible inference path (_slice_inferred) is used.
    """
    if spec is None:
        return False
    return (
        spec.cell_w_px is not None
        or spec.cell_h_px is not None
        or spec.cols is not None
        or spec.rows is not None
        or spec.margin_x_px > 0
        or spec.margin_y_px > 0
        or spec.spacing_x_px > 0
        or spec.spacing_y_px > 0
        or spec.origin != "top_left"
        or spec.order != "angle_major"
        or getattr(spec, "angle_row_map", None) is not None
    )


def _validate_angle_row_map(angle_row_map: List[int], calc_angles: int) -> None:
    """Validate that angle_row_map is a valid permutation of [0..calc_angles-1].

    Validation order (CRITICAL -- length check first, then single-angle guard):
    1. Length mismatch
    2. Single-angle guard (angles==1 and map!=[0])
    3. Negative values
    4. Out-of-range values
    5. Duplicate values
    6. Catch-all set mismatch

    Args:
        angle_row_map: The permutation to validate (never None when called).
        calc_angles: Expected angle count (always > 0).

    Raises:
        ValueError: With actionable message on any validation failure.
    """
    n = len(angle_row_map)
    expected = set(range(calc_angles))

    # 1. Length mismatch
    if n != calc_angles:
        raise ValueError(
            f"--angle-row-map length {n} != angle count {calc_angles}. "
            f"Expected permutation of [0..{calc_angles - 1}]."
        )

    # 2. Single-angle guard
    if calc_angles == 1 and angle_row_map != [0]:
        raise ValueError(
            "--angle-row-map has no effect for single-angle sheets. "
            "Only [0] is valid when angles=1."
        )

    # 3. Negative values
    for v in angle_row_map:
        if v < 0:
            raise ValueError(
                f"--angle-row-map contains negative index {v}. "
                f"All indices must be >= 0."
            )

    # 4. Out-of-range values
    for v in angle_row_map:
        if v >= calc_angles:
            raise ValueError(
                f"--angle-row-map index {v} out of range for {calc_angles} angles. "
                f"Valid range: [0..{calc_angles - 1}]."
            )

    # 5. Duplicate values
    seen = set()
    for v in angle_row_map:
        if v in seen:
            raise ValueError(
                f"--angle-row-map has duplicate index {v}. "
                f"Must be a permutation of [0..{calc_angles - 1}]."
            )
        seen.add(v)

    # 6. Catch-all set mismatch
    actual = set(angle_row_map)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"--angle-row-map must be permutation of [0..{calc_angles - 1}]. "
            f"missing: {missing}; extra: {extra}. Got: {angle_row_map}"
        )


class ImageSlicer:
    """
    Stateless sprite sheet slicer that decomposes a grid-based PNG into
    individual frame tiles using angle-major, animation-secondary ordering.

    The slicer does not hold any mutable state; all configuration is passed
    per-call via the ``slice()`` method parameters.

    [PIPELINE:SLICE]
    """

    def slice_image(self, image: Image.Image) -> List[Image.Image]:
        """
        Backward-compatible method for slicing images with default parameters.

        WHY default (angles=8, frames=[1, 8]): This matches the most common
        Asciicker character layout -- 8 directional views with 1 idle frame
        and 8 walk-cycle frames.  Kept for callers that predate the explicit
        ``slice()`` API.

        Args:
            image: Sprite sheet to slice

        Returns:
            List of frames (uses default angles=8, frames=[1, 8] for generic case)
        """
        return self.slice(image, angles=8, frames=[1, 8])

    def slice(
        self,
        image: Image.Image,
        angles: int,
        frames: List[int],
        slice_spec=None,
        slice_mode: str = "global_grid",
        bg_tolerance: float = 30.0,
        rowcol_fallback_to_grid: bool = True,
    ) -> List[Image.Image]:
        """
        Slices image into frames with angle-first, animation-major ordering.

        [PIPELINE:SLICE] This is the primary entry point for Stage 2 of the
        asset pipeline.

        Args:
            image: Sprite sheet PNG to slice
            angles: Number of angle views (1, 4, or 8)
            frames: List of frame counts per animation (e.g. [1, 8] for
                    1 idle frame + 8 walk frames)
            slice_spec: Optional SlicingSpec with explicit cell sizes, margins,
                    spacing, and ordering.  When None, dimensions are inferred
                    from image size (backward-compatible behavior).
            slice_mode: "global_grid" (default) or "rowcol".
            bg_tolerance: Foreground segmentation tolerance for rowcol mode.
            rowcol_fallback_to_grid: If True, row/column detection failures
                    fall back to inferred global-grid slicing.

        Returns:
            List of frames in order:
            angle 0 x anim 0 x frame 0, angle 0 x anim 0 x frame 1, ...
            angle 1 x anim 0 x frame 0, angle 1 x anim 0 x frame 1, ...
            ...

        Expected sheet layout (when no spec):
            - Width: sum(frames) x (cell_width)
            - Height: angles x (cell_height)
            - Cells: angles x sum(frames)

        Example (angles=8, frames=[1,8]):
            - 8 rows (angles)
            - 9 columns (1 idle + 8 walk)
            - 72 total cells (not 9)

        Raises:
            ValueError: If image dimensions do not divide evenly into the
                        expected grid (when no spec is provided).
        """
        if _spec_is_active(slice_spec):
            return self._slice_with_spec(image, angles, frames, slice_spec)

        mode = str(slice_mode or "global_grid").strip().lower()
        if mode == "global_grid":
            return self._slice_inferred(image, angles, frames)
        if mode == "rowcol":
            return self._slice_rowcol(
                image=image,
                angles=angles,
                frames=frames,
                bg_tolerance=bg_tolerance,
                fallback_to_grid=rowcol_fallback_to_grid,
            )
        raise ValueError(
            f"Unknown slice_mode '{slice_mode}'. "
            "Expected one of: global_grid, rowcol."
        )

    @staticmethod
    def _detect_axis_spans(energy: np.ndarray, expected: int) -> List[Tuple[int, int]]:
        """Detect active spans along one axis from foreground energy."""
        if expected <= 0:
            return []
        arr = np.asarray(energy, dtype=np.float32)
        if arr.size == 0 or float(np.max(arr)) <= 0.0:
            return []

        best: List[Tuple[int, int]] = []
        for ratio in (0.20, 0.15, 0.10, 0.07, 0.05, 0.03):
            threshold = max(1.0, float(np.max(arr)) * ratio)
            active = arr >= threshold
            if active.size >= 3:
                bridged = active.copy()
                for i in range(1, len(bridged) - 1):
                    if (not bridged[i]) and bridged[i - 1] and bridged[i + 1]:
                        bridged[i] = True
                active = bridged

            spans: List[Tuple[int, int]] = []
            start = None
            for i, on in enumerate(active):
                if on and start is None:
                    start = i
                elif (not on) and start is not None:
                    if i - start >= 2:
                        spans.append((start, i))
                    start = None
            if start is not None and len(active) - start >= 2:
                spans.append((start, len(active)))

            if len(spans) > len(best):
                best = spans
            if len(spans) >= expected:
                weighted = [
                    (float(np.sum(arr[s:e])), s, e)
                    for (s, e) in spans
                ]
                weighted.sort(key=lambda t: t[0], reverse=True)
                return sorted((s, e) for (_, s, e) in weighted[:expected])

        return sorted(best)

    @staticmethod
    def _centers_to_windows(
        centers: List[float],
        window_size: int,
        axis_len: int,
    ) -> List[Tuple[int, int]]:
        """Build fixed-size crop windows centered on detected span centers."""
        if axis_len <= 0:
            return []
        w = max(1, min(int(window_size), int(axis_len)))
        windows: List[Tuple[int, int]] = []
        for c in centers:
            start = int(round(float(c) - (w / 2.0)))
            if start < 0:
                start = 0
            if start + w > axis_len:
                start = max(0, axis_len - w)
            windows.append((start, start + w))
        return windows

    def _slice_rowcol(
        self,
        image: Image.Image,
        angles: int,
        frames: List[int],
        bg_tolerance: float,
        fallback_to_grid: bool,
    ) -> List[Image.Image]:
        """Two-stage slicing: detect row bands first, then columns per row."""
        total_frames_per_angle = sum(frames)
        if total_frames_per_angle <= 0:
            raise ValueError(
                f"Cannot slice: sum(frames)={total_frames_per_angle} "
                f"(frames={frames}). Need at least 1 frame per angle."
            )
        calc_angles = angles if angles > 0 else 1

        mask = segment_cell_region(
            np.array(image.convert("RGB")),
            bg_tolerance=float(bg_tolerance),
        )
        row_energy = np.sum(mask, axis=1)
        row_spans = self._detect_axis_spans(row_energy, expected=calc_angles)
        if len(row_spans) != calc_angles:
            msg = (
                "Row/column slicing could not detect expected row bands "
                f"(expected={calc_angles}, detected={len(row_spans)})."
            )
            if fallback_to_grid:
                print(f"Warning: {msg} Falling back to global grid.", file=sys.stderr)
                return self._slice_inferred(image, angles, frames)
            raise ValueError(msg)

        frame_px_h = max(1, int(round(image.height / max(1, calc_angles))))
        row_centers = [0.5 * (s + e - 1) for (s, e) in row_spans]
        row_bounds = self._centers_to_windows(row_centers, frame_px_h, image.height)

        frame_px_w = max(1, int(round(image.width / max(1, total_frames_per_angle))))
        result_frames: List[Image.Image] = []

        for angle_idx in range(calc_angles):
            y0, y1 = row_bounds[angle_idx]
            band_mask = mask[y0:y1, :]
            col_energy = np.sum(band_mask, axis=0)
            col_spans = self._detect_axis_spans(
                col_energy,
                expected=total_frames_per_angle,
            )
            if len(col_spans) != total_frames_per_angle:
                msg = (
                    "Row/column slicing could not detect expected columns "
                    f"for row={angle_idx} (expected={total_frames_per_angle}, "
                    f"detected={len(col_spans)})."
                )
                if fallback_to_grid:
                    print(f"Warning: {msg} Falling back to global grid.", file=sys.stderr)
                    return self._slice_inferred(image, angles, frames)
                raise ValueError(msg)

            col_centers = [0.5 * (s + e - 1) for (s, e) in col_spans]
            col_bounds = self._centers_to_windows(
                col_centers,
                frame_px_w,
                image.width,
            )

            col_offset = 0
            for num_frames in frames:
                for frame_idx in range(num_frames):
                    col = col_offset + frame_idx
                    x0, x1 = col_bounds[col]
                    result_frames.append(image.crop((x0, y0, x1, y1)))
                col_offset += num_frames

        return result_frames

    def _slice_inferred(
        self,
        image: Image.Image,
        angles: int,
        frames: List[int],
    ) -> List[Image.Image]:
        """Inference-based slicing with strict divisibility checks."""
        total_frames_per_angle = sum(frames)

        if total_frames_per_angle <= 0:
            raise ValueError(
                f"Cannot slice: sum(frames)={total_frames_per_angle} "
                f"(frames={frames}). Need at least 1 frame per angle."
            )

        # Handle angles=0 (signals 1 angle to engine)
        calc_angles = angles if angles > 0 else 1

        # [DATA-CONTRACT:GRID] Divisibility check with small-remainder trim.
        # Real sprite sheets often have 1-2px padding artifacts that cause
        # non-integer division (e.g. manul: 1536px / 9 = 170.67).  When the
        # remainder is small relative to the cell size, trim instead of crash.
        remainder_w = image.size[0] % total_frames_per_angle
        remainder_h = image.size[1] % calc_angles
        cell_w_approx = image.size[0] // total_frames_per_angle if total_frames_per_angle else 0
        cell_h_approx = image.size[1] // calc_angles if calc_angles else 0

        # Trim threshold: remainder must be < half a cell per axis.
        can_trim_w = remainder_w != 0 and cell_w_approx > 0 and remainder_w < cell_w_approx // 2
        can_trim_h = remainder_h != 0 and cell_h_approx > 0 and remainder_h < cell_h_approx // 2

        if (remainder_w != 0 and not can_trim_w) or (remainder_h != 0 and not can_trim_h):
            hints = []
            if remainder_w != 0 and not can_trim_w:
                hints.append(
                    f"width {image.size[0]} / {total_frames_per_angle} cols "
                    f"= {image.size[0] / total_frames_per_angle:.2f}px/cell "
                    f"(remainder {remainder_w}px, too large to trim)"
                )
            if remainder_h != 0 and not can_trim_h:
                hints.append(
                    f"height {image.size[1]} / {calc_angles} rows "
                    f"= {image.size[1] / calc_angles:.2f}px/cell "
                    f"(remainder {remainder_h}px, too large to trim)"
                )
            raise ValueError(
                f"Image {image.size[0]}x{image.size[1]} does not divide "
                f"evenly into {total_frames_per_angle} cols x {calc_angles} rows "
                f"(frames={frames}, angles={angles}).\n"
                f"  {'; '.join(hints)}\n"
                f"Fix: supply explicit --cell-w/--cell-h or --cols/--rows."
            )

        # Apply trim if needed (crop off trailing pixels).
        trim_w = image.size[0] - remainder_w if remainder_w != 0 else image.size[0]
        trim_h = image.size[1] - remainder_h if remainder_h != 0 else image.size[1]
        if trim_w != image.size[0] or trim_h != image.size[1]:
            import logging
            logging.getLogger(__name__).info(
                "Trimming %dx%d -> %dx%d (remainder %dpx x %dpx)",
                image.size[0], image.size[1], trim_w, trim_h,
                remainder_w, remainder_h,
            )
            image = image.crop((0, 0, trim_w, trim_h))

        frame_px_w = image.size[0] // total_frames_per_angle
        frame_px_h = image.size[1] // calc_angles

        if frame_px_w <= 0 or frame_px_h <= 0:
            raise ValueError(
                f"Computed cell size {frame_px_w}x{frame_px_h} is non-positive. "
                f"Image {image.size}, cols={total_frames_per_angle}, "
                f"rows={calc_angles}."
            )

        result_frames = []

        # WHY angle-first (row-major by angle): Downstream XPAssembler and the
        # .xp file format expect all frames for angle 0 first, then angle 1,
        # etc.  This matches the game engine's lookup pattern where angle is
        # the outermost index:  frame = frames[angle * frames_per_angle + idx].
        for angle_idx in range(calc_angles):
            row = angle_idx
            col_offset = 0

            for anim_idx, num_frames in enumerate(frames):
                for frame_idx in range(num_frames):
                    col = col_offset + frame_idx

                    left = col * frame_px_w
                    top = row * frame_px_h
                    right = left + frame_px_w
                    bottom = top + frame_px_h

                    frame = image.crop((left, top, right, bottom))
                    result_frames.append(frame)

                col_offset += num_frames

        return result_frames

    def _slice_with_spec(
        self,
        image: Image.Image,
        angles: int,
        frames: List[int],
        spec,
    ) -> List[Image.Image]:
        """Spec-based slicing with explicit cell sizes, margins, and spacing."""
        margin_x = spec.margin_x_px
        margin_y = spec.margin_y_px
        spacing_x = spec.spacing_x_px
        spacing_y = spec.spacing_y_px

        calc_angles = angles if angles > 0 else 1
        total_cols = sum(frames)

        if spec.order == "animation_major":
            if angles == 0:
                print(
                    "Warning: angles=0 coerced to 1 for animation_major ordering",
                    file=sys.stderr,
                )
            if spec.rows is None and spec.cols is None:
                # Animation-major: rows=sum(frames), cols=angles
                # (transpose of angle_major default)
                rows = total_cols  # sum(frames) rows
                cols = calc_angles  # angle columns (NOT * projs -- already in frames)
            else:
                # Explicit geometry: validate against animation-major expectations
                rows = spec.rows if spec.rows is not None else total_cols
                cols = spec.cols if spec.cols is not None else calc_angles
                expected_rows = total_cols  # sum(frames)
                expected_cols = calc_angles
                if spec.rows is not None and spec.rows != expected_rows:
                    raise ValueError(
                        f"animation_major explicit --rows={spec.rows} doesn't match "
                        f"expected sum(frames)={expected_rows}"
                    )
                if spec.cols is not None and spec.cols != expected_cols:
                    raise ValueError(
                        f"animation_major explicit --cols={spec.cols} doesn't match "
                        f"expected angles={expected_cols}"
                    )
        else:
            rows = spec.rows if spec.rows is not None else calc_angles
            cols = spec.cols if spec.cols is not None else total_cols

        if cols <= 0:
            raise ValueError(
                f"Cannot slice: cols={cols} (from frames={frames}). "
                f"Need at least 1 column."
            )
        if rows <= 0:
            raise ValueError(
                f"Cannot slice: rows={rows} (from angles={angles}). "
                f"Need at least 1 row."
            )

        # Infer cell dimensions when not explicitly provided.
        # This allows specs with only cols/rows/margins to work.
        cell_w = spec.cell_w_px
        cell_h = spec.cell_h_px
        if cell_w is None:
            content_w = image.width - 2 * margin_x - max(0, cols - 1) * spacing_x
            if content_w <= 0:
                raise ValueError(
                    f"Negative content width: image {image.width}px minus "
                    f"margins 2*{margin_x} and spacing "
                    f"{max(0, cols - 1)}*{spacing_x} = {content_w}px. "
                    f"Reduce margins/spacing or use a wider image."
                )
            remainder = content_w % cols
            if remainder != 0:
                raise ValueError(
                    f"Content width {content_w}px not divisible by {cols} cols "
                    f"(remainder {remainder}px). "
                    f"Specify --cell-w explicitly."
                )
            cell_w = content_w // cols
        if cell_h is None:
            content_h = image.height - 2 * margin_y - max(0, rows - 1) * spacing_y
            if content_h <= 0:
                raise ValueError(
                    f"Negative content height: image {image.height}px minus "
                    f"margins 2*{margin_y} and spacing "
                    f"{max(0, rows - 1)}*{spacing_y} = {content_h}px. "
                    f"Reduce margins/spacing or use a taller image."
                )
            remainder = content_h % rows
            if remainder != 0:
                raise ValueError(
                    f"Content height {content_h}px not divisible by {rows} rows "
                    f"(remainder {remainder}px). "
                    f"Specify --cell-h explicitly."
                )
            cell_h = content_h // rows

        if cell_w <= 0 or cell_h <= 0:
            raise ValueError(
                f"Computed cell size {cell_w}x{cell_h} is non-positive. "
                f"Image {image.width}x{image.height}, cols={cols}, rows={rows}, "
                f"margins=({margin_x},{margin_y}), "
                f"spacing=({spacing_x},{spacing_y})."
            )

        # Validate explicit cell dimensions fit the image when fully specified
        if spec.cell_w_px is not None and spec.cols is not None:
            expected_w = 2 * margin_x + cols * cell_w + max(0, cols - 1) * spacing_x
            if expected_w > image.width:
                raise ValueError(
                    f"Explicit grid exceeds image width: "
                    f"{cols} cols * {cell_w}px + margins/spacing = {expected_w}px, "
                    f"but image is only {image.width}px wide."
                )
        if spec.cell_h_px is not None and spec.rows is not None:
            expected_h = 2 * margin_y + rows * cell_h + max(0, rows - 1) * spacing_y
            if expected_h > image.height:
                raise ValueError(
                    f"Explicit grid exceeds image height: "
                    f"{rows} rows * {cell_h}px + margins/spacing = {expected_h}px, "
                    f"but image is only {image.height}px tall."
                )

        origin = getattr(spec, "origin", "top_left")

        def _row_to_y(logical_row):
            """Convert logical row index to pixel Y coordinate."""
            physical_row = (rows - 1 - logical_row) if origin == "bottom_left" else logical_row
            return margin_y + physical_row * (cell_h + spacing_y)

        result_frames = []

        if spec.order == "frame_major":
            # Column-major: iterate cols in outer loop, rows in inner
            for col in range(cols):
                for row in range(rows):
                    x = margin_x + col * (cell_w + spacing_x)
                    y = _row_to_y(row)
                    frame = image.crop((x, y, x + cell_w, y + cell_h))
                    result_frames.append(frame)
        elif spec.order == "animation_major":
            # Animation-major: extract row-major (rows=anim frames, cols=angles)
            # then remap to engine's angle-major order
            for row in range(rows):
                for col in range(cols):
                    x = margin_x + col * (cell_w + spacing_x)
                    y = _row_to_y(row)
                    frame = image.crop((x, y, x + cell_w, y + cell_h))
                    result_frames.append(frame)
            # Remap from animation-major to angle-major
            # Use calc_angles (true angle count), NOT cols (may be overridden)
            from scripts.pipeline.frame_remap import remap_animation_major_to_angle_major
            result_frames = remap_animation_major_to_angle_major(
                result_frames, calc_angles, frames
            )
        elif spec.cols is not None and spec.cols != total_cols:
            # Explicit cols override: simple grid, no animation grouping.
            # When the user sets cols to a value different from sum(frames),
            # the animation-aware loop doesn't apply — iterate a flat grid.
            for row in range(rows):
                for col in range(cols):
                    x = margin_x + col * (cell_w + spacing_x)
                    y = _row_to_y(row)
                    frame = image.crop((x, y, x + cell_w, y + cell_h))
                    result_frames.append(frame)
        else:
            # angle_major (default): row-major with animation grouping
            for row in range(rows):
                col_offset = 0
                for anim_idx, num_frames in enumerate(frames):
                    for frame_idx in range(num_frames):
                        col = col_offset + frame_idx
                        x = margin_x + col * (cell_w + spacing_x)
                        y = _row_to_y(row)
                        frame = image.crop((x, y, x + cell_w, y + cell_h))
                        result_frames.append(frame)
                    col_offset += num_frames

        # ---- Angle row-map remap ----
        angle_row_map = getattr(spec, "angle_row_map", None)
        if angle_row_map is not None:
            # Reject incompatible order
            if spec.order == "frame_major":
                raise ValueError(
                    "--angle-row-map cannot be used with --order frame_major "
                    "(frame_major output is column-contiguous, not angle-contiguous)"
                )
            # Hard invariant: output must be angle-divisible
            if len(result_frames) % calc_angles != 0:
                raise ValueError(
                    f"Internal error: {len(result_frames)} frames not divisible by "
                    f"{calc_angles} angles. Cannot apply angle_row_map."
                )
            _validate_angle_row_map(angle_row_map, calc_angles)
            fpa = len(result_frames) // calc_angles
            remapped = []
            for target_idx in range(calc_angles):
                src_idx = angle_row_map[target_idx]
                remapped.extend(result_frames[src_idx * fpa : (src_idx + 1) * fpa])
            result_frames = remapped

        return result_frames


# =============================================================================
# Content-Aware Correction (Track 3)
# =============================================================================


def analyze_frame_content(
    frame: Image.Image,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> Dict:
    """Analyze content within a single sliced frame.

    [PIPELINE:SLICE] Per-frame diagnostic for split/bleed detection.

    Args:
        frame: Cropped frame PIL Image.
        bg_color: Background color, or None for auto-detect.
        bg_tolerance: Color distance tolerance for segmentation.

    Returns:
        Dict with keys:
        - centroid: (cx, cy) in frame-local coords, or None if no content
        - fg_ratio: fraction of foreground pixels (0.0-1.0)
        - fg_count: absolute foreground pixel count
        - has_h_split: bool — content touches both left AND right edges
        - has_v_split: bool — content touches both top AND bottom edges
        - border_densities: dict with left/right/top/bottom fg density (0.0-1.0)
    """
    pixels = np.array(frame.convert("RGB"))
    h, w = pixels.shape[:2]
    if h == 0 or w == 0:
        return {
            "centroid": None, "fg_ratio": 0.0, "fg_count": 0,
            "has_h_split": False, "has_v_split": False,
            "border_densities": {"left": 0, "right": 0, "top": 0, "bottom": 0},
        }

    fg_mask = segment_cell_region(pixels, bg_color=bg_color, bg_tolerance=bg_tolerance)
    fg_count = int(np.sum(fg_mask))
    fg_ratio = fg_count / (h * w)

    # Centroid
    centroid = None
    if fg_count >= 4:
        ys, xs = np.where(fg_mask)
        centroid = (float(np.mean(xs)), float(np.mean(ys)))

    # Border densities (fraction of fg pixels along each edge)
    left_d = float(np.mean(fg_mask[:, 0])) if w > 0 else 0.0
    right_d = float(np.mean(fg_mask[:, -1])) if w > 0 else 0.0
    top_d = float(np.mean(fg_mask[0, :])) if h > 0 else 0.0
    bottom_d = float(np.mean(fg_mask[-1, :])) if h > 0 else 0.0

    border_threshold = 0.1
    has_h_split = left_d > border_threshold and right_d > border_threshold
    has_v_split = top_d > border_threshold and bottom_d > border_threshold

    return {
        "centroid": centroid,
        "fg_ratio": fg_ratio,
        "fg_count": fg_count,
        "has_h_split": has_h_split,
        "has_v_split": has_v_split,
        "border_densities": {
            "left": left_d, "right": right_d,
            "top": top_d, "bottom": bottom_d,
        },
    }


def detect_neighbor_bleed(
    analysis_a: Dict,
    analysis_b: Dict,
    edge_a: str,
    edge_b: str,
    threshold: float = 0.1,
) -> float:
    """Detect content bleed between two adjacent cells.

    Bleed = both cells have significant foreground at their shared border.

    Args:
        analysis_a: analyze_frame_content result for cell A.
        analysis_b: analyze_frame_content result for cell B.
        edge_a: Border of A facing B ('right' or 'bottom').
        edge_b: Matching border of B facing A ('left' or 'top').
        threshold: Minimum density to count as significant.

    Returns:
        Bleed score (0.0-1.0). Product of the two border densities,
        normalized. Returns 0.0 if either side is below threshold.
    """
    da = analysis_a["border_densities"].get(edge_a, 0.0)
    db = analysis_b["border_densities"].get(edge_b, 0.0)
    if da < threshold or db < threshold:
        return 0.0
    return da * db


def compute_slice_diagnostics(
    frames: List[Image.Image],
    grid_cols: int,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> Dict:
    """Compute split and bleed diagnostics for all sliced frames.

    [PIPELINE:SLICE] Called after slicer.slice() for quality assessment.

    Args:
        frames: List of sliced frame images.
        grid_cols: Number of columns in the grid (for neighbor detection).
        bg_color: Background color, or None for auto-detect per frame.
        bg_tolerance: Segmentation tolerance.

    Returns:
        Dict with:
        - per_frame: list of analyze_frame_content dicts
        - split_count: number of frames with any split
        - bleed_pairs: list of (idx_a, idx_b, score) for bleeding neighbors
        - split_ratio: fraction of frames with split
        - bleed_ratio: fraction of adjacent pairs with bleed
    """
    n = len(frames)
    per_frame = [
        analyze_frame_content(f, bg_color=bg_color, bg_tolerance=bg_tolerance)
        for f in frames
    ]

    split_count = sum(
        1 for a in per_frame if a["has_h_split"] or a["has_v_split"]
    )

    # Detect horizontal bleed (right neighbor in same row)
    bleed_pairs: List[Tuple[int, int, float]] = []
    for i in range(n):
        row = i // grid_cols if grid_cols > 0 else 0
        col = i % grid_cols if grid_cols > 0 else i
        # Right neighbor
        right_idx = i + 1
        if col + 1 < grid_cols and right_idx < n:
            score = detect_neighbor_bleed(
                per_frame[i], per_frame[right_idx], "right", "left"
            )
            if score > 0:
                bleed_pairs.append((i, right_idx, score))
        # Bottom neighbor
        bottom_idx = i + grid_cols
        if bottom_idx < n:
            score = detect_neighbor_bleed(
                per_frame[i], per_frame[bottom_idx], "bottom", "top"
            )
            if score > 0:
                bleed_pairs.append((i, bottom_idx, score))

    total_pairs = max(1, 2 * n - grid_cols - (n // max(1, grid_cols)))

    return {
        "per_frame": per_frame,
        "split_count": split_count,
        "split_ratio": split_count / max(1, n),
        "bleed_pairs": bleed_pairs,
        "bleed_ratio": len(bleed_pairs) / max(1, total_pairs),
    }


def content_correct_slice(
    source_image: Image.Image,
    angles: int,
    frames_list: List[int],
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
    max_shift_ratio: float = 0.25,
    margin_x: int = 0,
    margin_y: int = 0,
    spacing_x: int = 0,
    spacing_y: int = 0,
) -> Tuple[List[Image.Image], Dict]:
    """Re-slice source image with centroid-corrected crop positions.

    [PIPELINE:SLICE] Content-aware correction pass:
    1. Compute grid cell positions from parameters
    2. For each cell, find foreground centroid
    3. Shift crop toward centroid (bounded by max_shift_ratio)
    4. Crop at adjusted positions
    5. Compute diagnostics on corrected output

    Args:
        source_image: Full sprite sheet.
        angles: Number of angle rows.
        frames_list: Frame counts per animation.
        bg_color: Background color, or None for auto-detect.
        bg_tolerance: Segmentation tolerance.
        max_shift_ratio: Maximum shift as fraction of cell size (0.0-0.5).
        margin_x, margin_y: Margins in pixels.
        spacing_x, spacing_y: Inter-cell spacing in pixels.

    Returns:
        (corrected_frames, diagnostics) where diagnostics includes
        per-frame analysis and correction shift magnitudes.
    """
    calc_angles = angles if angles > 0 else 1
    total_cols = sum(frames_list)
    img_w, img_h = source_image.size

    if total_cols <= 0 or calc_angles <= 0:
        return [], {"per_frame": [], "split_count": 0, "corrections": []}

    # Compute cell dimensions
    content_w = img_w - 2 * margin_x - max(0, total_cols - 1) * spacing_x
    content_h = img_h - 2 * margin_y - max(0, calc_angles - 1) * spacing_y

    if content_w <= 0 or content_h <= 0:
        return [], {"per_frame": [], "split_count": 0, "corrections": []}

    cell_w = content_w // total_cols
    cell_h = content_h // calc_angles

    if cell_w <= 0 or cell_h <= 0:
        return [], {"per_frame": [], "split_count": 0, "corrections": []}

    pixels = np.array(source_image.convert("RGB"))
    max_dx = int(cell_w * max_shift_ratio)
    max_dy = int(cell_h * max_shift_ratio)

    corrected_frames: List[Image.Image] = []
    corrections: List[Dict] = []

    for row in range(calc_angles):
        col_offset = 0
        for anim_frames in frames_list:
            for frame_idx in range(anim_frames):
                col = col_offset + frame_idx
                base_x = margin_x + col * (cell_w + spacing_x)
                base_y = margin_y + row * (cell_h + spacing_y)

                # Extract cell region for analysis
                cell_region = pixels[base_y:base_y + cell_h, base_x:base_x + cell_w]
                if cell_region.size == 0:
                    corrected_frames.append(
                        source_image.crop((base_x, base_y,
                                           base_x + cell_w, base_y + cell_h))
                    )
                    corrections.append({"dx": 0, "dy": 0, "had_content": False})
                    continue

                fg_mask = segment_cell_region(
                    cell_region, bg_color=bg_color, bg_tolerance=bg_tolerance
                )
                fg_count = int(np.sum(fg_mask))

                if fg_count < 4:
                    # No significant content — use base position
                    corrected_frames.append(
                        source_image.crop((base_x, base_y,
                                           base_x + cell_w, base_y + cell_h))
                    )
                    corrections.append({"dx": 0, "dy": 0, "had_content": False})
                    continue

                # Compute centroid in cell-local coords
                ys, xs = np.where(fg_mask)
                cx = float(np.mean(xs))
                cy = float(np.mean(ys))

                # Offset from cell center
                cell_center_x = cell_w / 2.0
                cell_center_y = cell_h / 2.0
                dx = cx - cell_center_x
                dy = cy - cell_center_y

                # Bound the shift
                dx_clamped = max(-max_dx, min(max_dx, int(round(dx))))
                dy_clamped = max(-max_dy, min(max_dy, int(round(dy))))

                # Apply shift, clamping to image bounds
                new_x = max(0, min(img_w - cell_w, base_x + dx_clamped))
                new_y = max(0, min(img_h - cell_h, base_y + dy_clamped))

                corrected_frames.append(
                    source_image.crop((new_x, new_y,
                                       new_x + cell_w, new_y + cell_h))
                )
                corrections.append({
                    "dx": new_x - base_x,
                    "dy": new_y - base_y,
                    "had_content": True,
                })

            col_offset += anim_frames

    # Compute diagnostics on corrected output
    diagnostics = compute_slice_diagnostics(
        corrected_frames, grid_cols=total_cols,
        bg_color=bg_color, bg_tolerance=bg_tolerance,
    )
    diagnostics["corrections"] = corrections
    avg_shift = 0.0
    shift_frames = [c for c in corrections if c["had_content"]]
    if shift_frames:
        avg_shift = sum(
            abs(c["dx"]) + abs(c["dy"]) for c in shift_frames
        ) / len(shift_frames)
    diagnostics["avg_shift_px"] = avg_shift

    return corrected_frames, diagnostics

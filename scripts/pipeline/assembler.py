"""
XP sprite assembler for the Asciicker asset pipeline.

ARCHITECTURE
------------
This module sits at Stage 4 (final) of the 4-stage sprite generation pipeline:

    [PIPELINE:GENERATE] -> [PIPELINE:SLICE] -> [PIPELINE:PROCESS] -> **[PIPELINE:ASSEMBLE]**

XPAssembler receives processed glyph grids (produced by SpriteProcessor in
processor.py) together with sprite-sheet metadata (angle count, animation
frame counts), and writes the final multi-layered .xp binary file via
xp_core.XPFile.save().

The output .xp file has a fixed 4-layer structure consumed by the C++ engine
(sprite.cpp) and editable in REXPaint:

    Layer 0 -- Metadata: angle count + per-animation frame counts, encoded as
               CP437 digit glyphs at row 0.
    Layer 1 -- Depth map: hex-digit characters ('0'-'9','A'-'F') encoding
               per-cell height for the 2.5-D renderer.
    Layer 2 -- Main visuals: the actual glyph/color sprite sheet, laid out as
               a grid of (angles rows) x (sum-of-anim-frames columns).
    Layer 3 -- Detail overlay: reserved for future per-cell effects; currently
               filled with transparent (glyph 0, magenta BG).

KEY EXPORTS
-----------
- ``XPAssembler`` -- stateless assembler; call ``assemble()`` to write .xp.

PIPELINE CONTEXT
----------------
- [PIPELINE:ASSEMBLE] -- Final stage; produces the pipeline's deliverable.
- [DATA-CONTRACT:XP] -- Output conforms to the REXPaint .xp binary format
  as implemented in xp_core.py.
- [DATA-CONTRACT:CP437] -- Metadata digits and depth-map characters are
  encoded as CP437 code points via ``_encode_digit()``.
- [DATA-CONTRACT:PALETTE] -- Magenta (255,0,255) background signals
  transparency to the engine.
- [DEPENDENCY:XP_CORE] -- XPFile / XPLayer handle binary serialization.
"""

from typing import List, Tuple, Dict, Any
import logging
import sys
import os

# WHY: ensure the parent package (scripts/) is on sys.path so that
# relative imports within the asset_gen package resolve correctly when
# this module is executed or imported from varying working directories.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# [DEPENDENCY:XP_CORE] -- XPFile handles gzip-compressed .xp binary I/O;
# XPLayer is the per-layer data container (width x height grid of cells).
from .xp_core import XPFile, XPLayer

logger = logging.getLogger(__name__)


from scripts.pipeline.xp_core import encode_digit


# [PIPELINE:ASSEMBLE] -- Entry point for Stage 4 of the sprite pipeline.
class XPAssembler:
    """Converts processed glyph grids into multi-layered .xp sprite files.

    This is a stateless assembler: it holds no cached data between calls.
    Each ``assemble()`` invocation builds a fresh XPFile, populates its four
    canonical layers, and writes the gzip-compressed result to disk.

    The sprite sheet layout follows the Asciicker engine convention:
        - Rows correspond to viewing angles (0 = front, progressing clockwise).
        - Columns correspond to animation frames, concatenated across all
          animation sequences defined in ``metadata['anims']``.
    """

    def assemble(
        self,
        frames: List[List[List[Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]]]],
        metadata: Dict[str, Any],
        filename: str,
    ) -> None:
        """Assemble processed glyph data into a multi-layered .xp file.

        Layers:
            0: Metadata -- angle count and per-animation frame counts encoded
               as CP437 digit glyphs at row 0.
            1: Depth Map -- hex characters ('0'-'9', 'A'-'F') for 2.5-D height.
            2: Main Visuals -- the actual sprite glyph/color data.
            3: Detail Overlay -- reserved; currently transparent.

        Args:
            frames: Flat list of frame grids, ordered angle-major (all frames
                for angle 0 first, then angle 1, etc.). Each frame is a 2-D
                grid ``[y][x]`` of ``(glyph_idx, fg_rgb, bg_rgb)`` tuples as
                produced by SpriteProcessor.process_image().
            metadata: Dict with keys:
                - ``'angles'`` (int): number of viewing angles (rows).
                - ``'anims'`` (List[int]): frame counts per animation sequence;
                  ``sum(anims)`` = number of columns in the sheet.
            filename: Output .xp file path (will be gzip-compressed).

        Raises:
            ValueError: If ``len(frames)`` does not match
                ``angles * sum(anims)``, or if frame dimensions are invalid.
        """
        # WHY defaults: angles=1, anims=[1] matches the simplest sprite case
        # (single-angle static item). This lets callers omit metadata entirely
        # for trivial one-frame sprites.
        angles = metadata.get("angles", 1)
        anims = metadata.get("anims", [1])

        # [ENGINE-CONTRACT] Multi-angle sprites require reflections (projs=2).
        # The engine derives projs, but we can accept it explicitly so the
        # assembler can validate and lay out the correct number of columns.
        from scripts.pipeline.service.config_resolver import resolve_projs

        projs = resolve_projs(angles, explicit_projs=metadata.get("projs"))

        # Extract background key color from BackgroundSpec metadata (if provided)
        bg_config = metadata.get("background", {})
        bg_key_color = tuple(bg_config.get("key_color", (255, 0, 255)))

        # WHY total_cols = projs * sum(anims): projection + reflection halves.
        total_cols = sum(anims) * projs

        # Handle angles=0 (signals 1 angle to engine)
        calc_angles = angles if angles > 0 else 1
        total_frames_expected = calc_angles * total_cols

        if any(a < 1 for a in anims):
            raise ValueError(
                f"Animation frame counts must be >= 1, got: {anims}. "
                f"Zero-valued entries cause roundtrip data loss "
                f"(encoder accepts glyph '0', decoder truncates at first zero)."
            )

        if total_frames_expected <= 0:
            raise ValueError(
                f"Invalid frame geometry: total_frames_expected={total_frames_expected} "
                f"(angles={calc_angles}, anims={anims}, projs={projs}, "
                f"total_cols={total_cols}). "
                f"Check that anims contains positive values and projs > 0."
            )

        # [DATA-CONTRACT:XP] -- The frame list must be exactly angles * columns.
        # A mismatch means the upstream slicer/generator produced the wrong
        # number of frames for this metadata configuration.
        if len(frames) != total_frames_expected:
            diag = (
                f"Frame count mismatch:\n"
                f"  Expected: {total_frames_expected} "
                f"(angles={calc_angles}, anims={anims}, projs={projs}, "
                f"total_cols={total_cols})\n"
                f"  Got: {len(frames)} frames from slicer\n"
            )
            if len(frames) == total_frames_expected * 2:
                diag += (
                    "  Likely cause: double-reflection — reflections applied twice.\n"
                    "  Fix: set projs explicitly on AssetDef to skip pipeline reflection handler.\n"
                )
            elif len(frames) > total_frames_expected:
                ratio = len(frames) / total_frames_expected
                diag += (
                    f"  Ratio: {ratio:.1f}x expected. Check --cols, --frames, or --projs.\n"
                )
            raise ValueError(diag)

        xp_file = XPFile()
        # WHY -1: REXPaint v1.02 uses version -1 (0xFFFFFFFF as unsigned).
        # The C++ engine (sprite.cpp) checks for this exact value.
        xp_file.version = -1

        # GAP-11-09: Validate all frames share the same dimensions as
        # frame 0.  Mismatched frame sizes would silently corrupt the
        # sheet layout.
        first_frame = frames[0]
        if not first_frame or not first_frame[0]:
            raise ValueError("Frame data is empty or invalid dimensions")

        frame_height = len(first_frame)
        frame_width = len(first_frame[0])

        # Validate dimensions are sane (prevent 65280x0 issues)
        if frame_height <= 0 or frame_width <= 0:
             raise ValueError(f"Invalid frame dimensions: {frame_width}x{frame_height}")

        for i, frame_grid in enumerate(frames):
            fh = len(frame_grid)
            fw = len(frame_grid[0]) if frame_grid else 0
            if fh != frame_height or fw != frame_width:
                raise ValueError(
                    f"Frame {i} has dimensions {fw}x{fh}, but frame 0 has "
                    f"{frame_width}x{frame_height}. All frames must match."
                )

        # WHY sheet layout: the sprite sheet is a 2-D tiling of individual
        # frames -- columns = animation frames (summed across all anims),
        # rows = viewing angles. The engine reads it in this exact order.
        sheet_width = frame_width * total_cols
        sheet_height = frame_height * calc_angles

        # --- Layer 0: Metadata ---
        # [DATA-CONTRACT:XP] -- The engine (sprite.cpp) reads Layer 0 to
        # determine how to partition the visual sheet into angle rows and
        # animation columns. All layers must share the same dimensions.
        meta_layer = XPLayer(sheet_width, sheet_height)
        # WHY fill with spaces: the engine ignores cells beyond the metadata
        # header, but they must be valid XP cells. Glyph 32 (space) with
        # black fg/bg is the conventional "empty" cell.
        for y in range(sheet_height):
            for x in range(sheet_width):
                meta_layer.data[y][x] = (32, (0,0,0), (0,0,0))

        # WHY encoding scheme: cell (0,0) holds the angle count as a CP437
        # digit glyph; cells (1,0)..(N,0) hold per-animation frame counts.
        # The engine reads these via CP437 decode: '0'-'9' -> 0-9, 'A'-'Z' -> 10-35.
        # [DATA-CONTRACT:CP437] -- digits encoded via _encode_digit().
        # Guard: metadata header must fit in sheet width
        _meta_cells_needed = 1 + len(anims)  # 1 for angles + 1 per anim
        if _meta_cells_needed > sheet_width:
            raise ValueError(
                f"Metadata header needs {_meta_cells_needed} cells "
                f"(1 angles + {len(anims)} anims) but sheet is only "
                f"{sheet_width} cells wide. Reduce animation count or "
                f"increase frame width."
            )

        meta_layer.data[0][0] = (self._encode_digit(angles), (255, 255, 255), (0, 0, 0))
        for i, count in enumerate(anims):
            if i + 1 < sheet_width:
                meta_layer.data[0][i + 1] = (self._encode_digit(count), (255, 255, 255), (0, 0, 0))
        xp_file.layers.append(meta_layer)

        # --- Layer 1: Depth Map ---
        # [DATA-CONTRACT:XP] -- The engine's 2.5-D renderer uses Layer 1 to
        # determine per-cell height for parallax and collision. Characters
        # '0'-'9' map to depths 0-9; 'A'-'F' extend to 10-15.
        # WHY '9' everywhere: without actual depth data from the 3-D source,
        # a uniform mid-height avoids z-fighting and keeps sprites visible.
        # TODO(PIPELINE-FIX): When Blender is the source, real per-cell depth
        # could be extracted from the Z-buffer and encoded here. Currently
        # all pipeline-generated sprites are flat.
        depth_layer = XPLayer(sheet_width, sheet_height)
        for y in range(sheet_height):
            for x in range(sheet_width):
                depth_layer.data[y][x] = (ord('9'), (200, 200, 200), (0, 0, 0))
        xp_file.layers.append(depth_layer)

        # --- Layer 2: Main Visuals ---
        # [PIPELINE:ASSEMBLE] -- This is the core output layer: each cell
        # contains the (glyph, fg, bg) tuple from SpriteProcessor.
        # [DATA-CONTRACT:PALETTE] -- Magenta (255,0,255) BG = transparent cell.
        visual_layer = XPLayer(sheet_width, sheet_height)
        # WHY pre-fill with key color: the engine treats this BG color as
        # transparent. Pre-filling ensures any unfilled cells (e.g. from
        # rounding errors in frame pasting) render as transparent rather
        # than as black artifacts. Defaults to magenta (255,0,255).
        for y in range(sheet_height):
            for x in range(sheet_width):
                visual_layer.data[y][x] = (32, (0,0,0), bg_key_color)

        # Paste frames into Layer 2
        # WHY angle-major ordering: frames are stored in the flat list as
        #   [angle0_frame0, angle0_frame1, ..., angle1_frame0, ...].
        # row_idx = angle index, col_idx = animation frame index within
        # that angle's row. This matches the engine's read order.
        for idx, frame_grid in enumerate(frames):
            row_idx = idx // total_cols
            col_idx = idx % total_cols
            base_x = col_idx * frame_width
            base_y = row_idx * frame_height

            for fy, row_data in enumerate(frame_grid):
                for fx, cell in enumerate(row_data):
                    if cell:
                        visual_layer.data[base_y + fy][base_x + fx] = cell
        xp_file.layers.append(visual_layer)

        # --- Layer 3: Extra Detail (Transparent) ---
        # WHY glyph 0 (not 32): glyph 0 is the NUL character in CP437. Some
        # engine code paths treat glyph 0 differently from space (32) for
        # overlay compositing. Magenta BG marks the entire layer as transparent.
        # TODO(PIPELINE-FIX): This layer is always empty. Once the pipeline
        # supports detail overlays (glow, damage marks), this should be
        # populated from an additional processor pass.
        detail_layer = XPLayer(sheet_width, sheet_height)
        for y in range(sheet_height):
            for x in range(sheet_width):
                detail_layer.data[y][x] = (0, (0,0,0), bg_key_color)
        xp_file.layers.append(detail_layer)

        # [DATA-CONTRACT:XP] -- XPFile.save() handles gzip compression and
        # column-major cell serialization per the REXPaint binary spec.
        xp_file.save(filename)
        logger.info(f"Saved multi-layered XP file: {filename} ({len(xp_file.layers)} layers)")

    def _encode_digit(self, value: int) -> int:
        """Encode an integer 0-35 as a CP437 digit/letter code point.

        Delegates to module-level ``encode_digit()`` for shared use.
        Instance method preserved for backward compatibility with existing tests.

        [DATA-CONTRACT:CP437]
        """
        return encode_digit(value)

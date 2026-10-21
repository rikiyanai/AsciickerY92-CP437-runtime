"""
pipeline.py -- Pipeline orchestrator for the Asciicker sprite generation system.

ARCHITECTURE
============
This is the **central orchestrator** that ties together four independent stages
into a single, template-driven workflow.  All other asset_gen modules are
subordinate to this one: pipeline.py is the only module that imports *all four*
stage modules and sequences their execution.

4-Stage Pipeline
----------------
::

    Stage 1  [PIPELINE:GENERATE]  generator.py   Load/render source image
         |
    Stage 2  [PIPELINE:SLICE]     slicer.py      Decompose sheet into frame tiles
         |
    Stage 3  [PIPELINE:PROCESS]   processor.py   Convert each tile to a CP437 glyph grid
         |
    Stage 4  [PIPELINE:ASSEMBLE]  assembler.py   Pack grids into a 4-layer .xp file

Between Stages 1 and 2 two optional passes may run:
  - **Grid validation / downscaling** -- ensures the loaded image matches the
    template's expected (cols * cell_w, rows * cell_h) pixel dimensions.
    Oversized images are letterbox-resized; undersized images raise GridError.
  - **Normalization / padding** -- legacy logic ported from sprite_gen.py that
    rescales each per-angle strip to a target cell height, snaps dimensions to
    the 12-pixel grid, and inserts 2 px top/bottom padding per angle row.

Normalization Algorithm
-----------------------
When enabled (via AssetDef.normalization or template processing.normalization):

  1. Compute ``raw_pixels_per_angle = image.height / angles``.
  2. If an explicit ``target_cells_high`` is set, use it; otherwise auto-cap
     at 2 cells (24 px) when the raw height exceeds 48 px.
  3. Snap the per-angle pixel height to the nearest 12 px boundary.
  4. Subtract 4 px (2 px top + 2 px bottom padding) to get the "safe" content
     height, then scale each angle strip to fit within it.
  5. Snap the total width to a multiple of ``total_cols * 12`` so every frame
     cell aligns on the 12 px grid.
  6. Paste each rescaled strip into a magenta (255, 0, 255) canvas with the
     2 px padding gap, producing uniform dimensions across all angles.

Template Integration  [FLOW:TEMPLATE]
-------------------------------------
An optional ``Template`` object (loaded from JSON by templates/loader.py and
hydrated into templates/models.py dataclasses) can drive every stage:

  - ``template.angles`` / ``template.frames`` override AssetDef defaults.
  - ``template.processing`` sets flags: magenta_snap, palette_quantize,
    downscale algorithm, crop_center, normalization.
  - ``template.debug`` opts in to label sheets and intermediate PNG saves.
  - ``template.output`` overrides the default .xp destination path.

When no template is provided the pipeline falls back to the bare AssetDef
values supplied by the CLI.

Staging Directory Workflow
--------------------------
All intermediate and final artifacts land under ``STAGING_DIR``
(``scripts/pipeline/staging/``)::

    inputs/   -- copies of original source files (for reproducibility)
    renders/  -- Blender-rendered PNGs (populated by generator.py)
    sheets/   -- intermediate sprite sheet PNGs (debug opt-in)
    xp/       -- final .xp output files  [DATA-CONTRACT:XP]
    debug/    -- labeled debug sheets (opt-in via template.debug)

Error Handling Strategy
-----------------------
``run()`` wraps all four stages in a single try/except.  Validation failures
(GridError, ValueError) propagate immediately so the caller (cli.py) can print
a user-friendly diagnostic without a traceback.  Temporary files created
during Stage 3 processing are cleaned up inline via ``os.unlink()``.

KEY EXPORTS
-----------
- ``AssetPipeline`` -- instantiate with an AssetDef (+ optional image path),
  then call ``run(template, algorithm)`` to execute the full pipeline.

PIPELINE CONTEXT
----------------
[PIPELINE:ORCHESTRATOR] -- This module is the only one that touches all four
stages.  It is called exclusively by cli.py (interactive or batch mode).
"""

from collections import Counter
import json
import os
from PIL import Image, ImageChops, ImageDraw, ImageStat
import sys
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from scripts.pipeline.generator import ImageGenerator
from scripts.pipeline.slicer import ImageSlicer
from scripts.pipeline.assembler import XPAssembler
from scripts.pipeline.schemas import AssetDef
from scripts.pipeline.downscale import ImageResizer, resize_with_letterbox
from scripts.pipeline.templates.models import DownscaleType
from scripts.pipeline.debug_sheet import render_debug_sheet
from scripts.pipeline.staging import STAGING_DIR
from scripts.pipeline.color_correction import align_background_to_magenta
from scripts.pipeline.service.constants import CELL_SIZE

# [PIPELINE:PROCESS] Auto mode heuristic scores.  Quality (subcell dithering)
# generally produces better visual results than standard (12px glyph matching),
# but this is a placeholder until SSIM-based comparison is implemented.
AUTO_MODE_QUALITY_SCORE = 0.6
AUTO_MODE_STANDARD_SCORE = 0.5
from scripts.pipeline.dispatch import resolve_process_mode as _resolve_process_mode
# Soft warning threshold for pathological semantic sprite-section layouts.
# Used only for diagnostics in branch mode (does not hard-fail the run).
_GRID_BRANCH_MAX_FRAMES = 4096

# [PIPELINE:PROCESS] -- SpriteProcessor provides proper two-color decomposition.
# It analyzes each cell to find the two most frequent colors (fg/bg),
# then matches the best CP437 glyph that separates those colors spatially.
# This replaced processor_core.ImageProcessor which only extracted ONE color
# per cell, causing fg==bg and making all glyphs invisible.
from scripts.pipeline.processor import SpriteProcessor


def _env_enabled(name: str) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def _safe_slug(value: str) -> str:
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)
    slug = slug.strip("_")
    return slug or "asset"


def _cfg_section(config, key):
    """Read a pipeline-config section from dict or dataclass-like object."""
    if isinstance(config, dict):
        return config.get(key, {})
    return getattr(config, key, None)


def _cfg_get(section, key, default=None):
    """Read a config value from dict or dataclass-like object."""
    if section is None:
        return default
    if isinstance(section, dict):
        return section.get(key, default)
    return getattr(section, key, default)


def _load_human_stage_review(
    stage: str,
    review_path: str,
    request_id: str,
    artifact_path: str | None = None,
) -> dict:
    """Load and validate human stage-review JSON.

    Required JSON fields:
      - approved: true
      - inspector_type: "human"
      - reviewer: non-empty
      - notes: non-empty

    Optional fields:
      - stage: if present, must match requested stage
      - request_id: if present, must match current request_id or "*"
    """
    path = Path(review_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        raise ValueError(
            f"Stage review required for {stage} but signoff file is missing: {path}. "
            f"Inspect artifact={artifact_path or '(none)'} and write approved JSON."
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Stage review signoff JSON is invalid: {path} ({exc})"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Stage review signoff must be a JSON object: {path}")

    if payload.get("approved") is not True:
        raise ValueError(
            f"Stage review for {stage} is not approved in {path}. "
            f"Set approved=true after manual inspection."
        )

    inspector_type = str(payload.get("inspector_type", "")).strip().lower()
    if inspector_type != "human":
        raise ValueError(
            f"Stage review for {stage} must declare inspector_type='human' in {path}."
        )

    reviewer = str(payload.get("reviewer", "")).strip()
    if not reviewer:
        raise ValueError(f"Stage review for {stage} missing reviewer in {path}.")

    notes = str(payload.get("notes", "")).strip()
    if not notes:
        raise ValueError(f"Stage review for {stage} missing notes in {path}.")

    payload_stage = str(payload.get("stage", "")).strip().lower()
    if payload_stage and payload_stage != stage.strip().lower():
        raise ValueError(
            f"Stage review stage mismatch in {path}: expected '{stage}', got '{payload_stage}'."
        )

    payload_request_id = str(payload.get("request_id", "")).strip()
    if payload_request_id and payload_request_id not in {"*", request_id}:
        raise ValueError(
            f"Stage review request_id mismatch in {path}: expected '{request_id}' or '*', "
            f"got '{payload_request_id}'."
        )

    return {
        "reviewer": reviewer,
        "path": str(path),
        "notes": notes,
        "request_id": payload_request_id or "",
    }


class _PipelineDebugTrace:
    """Lightweight JSONL + artifact logger for pipeline diagnostics."""

    # Default max trace directories.  Override via ASSET_MAX_TRACES env var.
    MAX_DEBUG_TRACES = 50

    def __init__(self, asset_name: str, request_id: str = None, entry_point: str = "pipeline"):
        self.enabled = _env_enabled("ASSET_PIPELINE_DEBUG")
        self.jsonl_path: Path | None = None
        self.artifacts_dir: Path | None = None
        self.request_id = request_id or f"req_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
        self.entry_point = entry_point
        if not self.enabled:
            return

        # Rotate old traces before creating new ones.
        self._rotate_traces()

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        slug = _safe_slug(asset_name)
        self.jsonl_path = STAGING_DIR / "debug" / f"{slug}_{ts}_pipeline.jsonl"
        self.artifacts_dir = STAGING_DIR / "debug" / f"{slug}_{ts}_artifacts"
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _rotate_traces() -> int:
        """Delete oldest debug trace directories when count exceeds max.

        Counts directories (``*_artifacts``) and JSONL files (``*_pipeline.jsonl``)
        in ``staging/debug/``, treating each ``(dir, jsonl)`` pair as one trace.
        When the number of trace *pairs* exceeds MAX_DEBUG_TRACES (or the
        ``ASSET_MAX_TRACES`` env var), the oldest pairs are removed.

        Returns:
            Number of trace pairs deleted.
        """
        max_traces = int(os.environ.get("ASSET_MAX_TRACES", str(_PipelineDebugTrace.MAX_DEBUG_TRACES)))
        debug_dir = STAGING_DIR / "debug"
        if not debug_dir.is_dir():
            return 0

        # Collect artifact directories as the canonical list of traces.
        # Each trace has two filesystem entries:
        #   {slug}_{ts}_artifacts/         (directory)
        #   {slug}_{ts}_pipeline.jsonl     (file)
        trace_dirs = sorted(
            (d for d in debug_dir.iterdir() if d.is_dir() and d.name.endswith("_artifacts")),
            key=lambda p: p.stat().st_mtime,
        )

        if len(trace_dirs) <= max_traces:
            return 0

        to_delete = trace_dirs[: len(trace_dirs) - max_traces]
        deleted = 0
        for trace_dir in to_delete:
            # Derive the companion JSONL path from the directory name.
            # e.g. "foo_20260210_010028_872007_artifacts" -> "foo_20260210_010028_872007_pipeline.jsonl"
            stem = trace_dir.name.removesuffix("_artifacts")
            jsonl_path = debug_dir / f"{stem}_pipeline.jsonl"

            # Remove the artifact directory tree.
            try:
                shutil.rmtree(trace_dir)
            except OSError:
                pass

            # Remove the companion JSONL file.
            if jsonl_path.exists():
                try:
                    jsonl_path.unlink()
                except OSError:
                    pass

            deleted += 1

        if deleted:
            print(f"Rotated {deleted} old debug traces (max={max_traces})", file=sys.stderr)

        return deleted

    def log(self, event: str, **fields) -> None:
        if not self.enabled or self.jsonl_path is None:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "request_id": self.request_id,
            "entry_point": self.entry_point,
            **fields,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def save_image(self, label: str, img: Image.Image) -> str | None:
        if not self.enabled or self.artifacts_dir is None:
            return None
        safe_label = _safe_slug(label)
        out_path = self.artifacts_dir / f"{safe_label}.png"
        img.save(out_path, "PNG")
        return str(out_path)


def _compose_frame_sheet(frames: list, rows: int, cols: int) -> Image.Image | None:
    """Reassemble sliced frame tiles into a single debug sheet."""
    if not frames or rows <= 0 or cols <= 0:
        return None
    frame_w, frame_h = frames[0].size
    sheet = Image.new("RGB", (frame_w * cols, frame_h * rows), (0, 0, 0))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx >= len(frames):
                break
            sheet.paste(frames[idx].convert("RGB"), (c * frame_w, r * frame_h))
            idx += 1
    return sheet


def _summarize_processed_frames(processed_frames: list) -> dict:
    """Aggregate glyph/color stats for Stage 3 diagnostics."""
    glyph_counts: Counter = Counter()
    fg_counts: Counter = Counter()
    bg_counts: Counter = Counter()
    total_cells = 0
    empty_cells = 0

    for frame in processed_frames:
        for row in frame:
            for cell in row:
                glyph, fg, bg = cell
                g = int(glyph)
                fg_t = tuple(int(c) for c in fg)
                bg_t = tuple(int(c) for c in bg)
                glyph_counts[g] += 1
                fg_counts[fg_t] += 1
                bg_counts[bg_t] += 1
                total_cells += 1
                if g in (0, 32):
                    empty_cells += 1

    def _top_colors(counter: Counter, n: int = 8) -> list:
        return [
            {"rgb": [k[0], k[1], k[2]], "count": v}
            for k, v in counter.most_common(n)
        ]

    return {
        "total_cells": total_cells,
        "empty_cells": empty_cells,
        "empty_cell_ratio": (empty_cells / total_cells) if total_cells else 0.0,
        "top_glyphs": [{"glyph": k, "count": v} for k, v in glyph_counts.most_common(12)],
        "top_fg_colors": _top_colors(fg_counts),
        "top_bg_colors": _top_colors(bg_counts),
    }


def _render_processed_preview(processed_frames: list, rows: int, cols: int) -> Image.Image | None:
    """Render a compact visual preview from processed glyph-grid frames."""
    if not processed_frames or rows <= 0 or cols <= 0:
        return None

    first = processed_frames[0]
    if not first or not first[0]:
        return None

    frame_h_cells = len(first)
    frame_w_cells = len(first[0])
    cell_px = CELL_SIZE
    sheet = Image.new(
        "RGB",
        (cols * frame_w_cells * cell_px, rows * frame_h_cells * cell_px),
        (0, 0, 0),
    )
    draw = ImageDraw.Draw(sheet)

    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx >= len(processed_frames):
                break
            frame = processed_frames[idx]
            base_x = c * frame_w_cells * cell_px
            base_y = r * frame_h_cells * cell_px
            for gy, row in enumerate(frame):
                for gx, cell in enumerate(row):
                    glyph, fg, bg = cell
                    fg_t = tuple(int(ch) for ch in fg)
                    bg_t = tuple(int(ch) for ch in bg)
                    x0 = base_x + gx * cell_px
                    y0 = base_y + gy * cell_px
                    x1 = x0 + cell_px - 1
                    y1 = y0 + cell_px - 1
                    draw.rectangle([x0, y0, x1, y1], fill=bg_t)
                    if int(glyph) not in (0, 32):
                        draw.rectangle([x0 + 3, y0 + 3, x0 + 8, y0 + 8], fill=fg_t)
            idx += 1

    return sheet


def _auto_detect_bg_spec(image):
    """Auto-detect background mode when --bg-mode not specified.

    Priority:
    1. RGBA image -> mode="alpha" (with warning)
    2. RGB image, 4 corners match a known BG color -> mode="key_color" with sampled color
    3. RGB image, corners disagree or non-BG color -> mode="key_color" with magenta fallback

    Conservative: only uses corner-sampled colors that look like backgrounds
    (near-magenta or near-black).  Arbitrary content colors are NOT treated
    as backgrounds to prevent destroying solid-color sprites.
    """
    from scripts.pipeline.service.slicing import BackgroundSpec

    if image.mode == "RGBA":
        import numpy as np
        alpha = np.array(image)[:, :, 3]
        has_transparency = bool((alpha < 128).any())
        if has_transparency:
            print("   Note: Detected alpha channel, using --bg-mode alpha. "
                  "Override with --bg-mode key_color if needed.")
            return BackgroundSpec(mode="alpha", alpha_threshold=128)
        # RGBA but no actual transparency — fall through to corner sampling
        print("   Note: RGBA image but no transparent pixels (all alpha>=128). "
              "Falling through to corner sampling.")

    # Corner sampling for key color auto-detection
    key_color, bg_tolerance = _sample_corner_pixels(image)
    if key_color is not None:
        print(f"   Note: Auto-detected background color {key_color} from corners "
              f"(tolerance={bg_tolerance}). Override with --bg-color if incorrect.")
        return BackgroundSpec(mode="key_color", key_color=key_color, tolerance=bg_tolerance)

    # Default: magenta key (no-op for non-magenta images)
    return BackgroundSpec(mode="key_color", key_color=(255, 0, 255), tolerance=8)


def _sample_corner_pixels(image):
    """Sample 4 corner pixels; return (mean_color, tolerance) if all match.

    Uses L1 distance between corners. Tolerance is computed adaptively:
    max(L1 distance from mean to any corner) + 5, clamped to [8, 50].
    This handles both uniform backgrounds and checkerboard patterns
    where the two alternating colors are within L1=50 of each other.

    Returns (None, 0) if corners disagree too much.
    """
    import numpy as np

    rgb = image.convert("RGB") if image.mode != "RGB" else image
    arr = np.array(rgb)
    h, w = arr.shape[:2]
    if h < 1 or w < 1:
        return None, 0

    corners = [
        tuple(int(c) for c in arr[0, 0]),
        tuple(int(c) for c in arr[0, w - 1]),
        tuple(int(c) for c in arr[h - 1, 0]),
        tuple(int(c) for c in arr[h - 1, w - 1]),
    ]

    ref = corners[0]
    for c in corners[1:]:
        dist = abs(c[0] - ref[0]) + abs(c[1] - ref[1]) + abs(c[2] - ref[2])
        if dist > 50:
            return None, 0

    # Compute mean color and adaptive tolerance
    mean_r = sum(c[0] for c in corners) // 4
    mean_g = sum(c[1] for c in corners) // 4
    mean_b = sum(c[2] for c in corners) // 4
    key = (mean_r, mean_g, mean_b)

    max_dist = max(
        abs(c[0] - mean_r) + abs(c[1] - mean_g) + abs(c[2] - mean_b)
        for c in corners
    )
    tolerance = min(50, max(8, max_dist + 5))

    return key, tolerance


class AssetPipeline:
    """Orchestrates the 4-stage sprite generation pipeline.

    Instantiate with an ``AssetDef`` describing *what* to build, then call
    ``run()`` to execute all stages sequentially.  An optional ``Template``
    object can override defaults for every stage (see module-level docstring
    for the full template integration story).

    Typical usage from cli.py::

        pipeline = AssetPipeline(asset_def, image_path="input.png")
        pipeline.run(template=loaded_template, algorithm="nearest")

    The pipeline is **single-use**: create a new instance for each asset.

    [PIPELINE:ORCHESTRATOR]
    """

    def __init__(self, asset_def: AssetDef, image_path: str | None = None, request_id: str = None, entry_point: str = "pipeline"):
        """
        Initialize the asset generation pipeline.

        Args:
            asset_def: Asset definition with metadata (angles, frames, name, etc.)
            image_path: Optional explicit image path.  When provided this bypasses
                        the generator's AI/Blender source resolution and loads the
                        file directly.  [FLOW:CLI] -- set by ``--input`` flag.
            request_id: Optional request ID for trace correlation.
            entry_point: Entry point name for trace logging.
        """
        self.asset_def = asset_def
        self.image_path = image_path
        self.request_id = request_id
        self.entry_point = entry_point

    def _auto_downscale_enabled(self) -> bool:
        """Return True only when legacy auto-downscale is explicitly enabled."""
        return _env_enabled("ASSET_PIPELINE_ENABLE_AUTO_DOWNSCALE")

    def validate_and_downscale(
        self, image: Image.Image, template, algorithm: DownscaleType = "nearest"
    ) -> Image.Image:
        """
        Validate image dimensions against template and auto-downscale if needed.

        [PIPELINE:GENERATE] -- Runs between image loading and slicing to ensure
        the source image matches the template's expected grid dimensions exactly.

        Implementation flow (from phase 08-04 plan):
          1. Run GridValidator.validate_image() to check dimensions.
          2. If valid: return image unchanged.
          3. If too large: compute downscale factor, warn if >5x, letterbox-scale
             to preserve aspect ratio, then resize to exact expected dimensions.
             Re-validate after resize to catch rounding drift.
          4. If too small: raise GridError immediately -- the pipeline never
             upscales because it would introduce blurry artifacts in pixel art.
             (GRID-05 decision: no silent fallback on undersized input.)

        [FLOW:TEMPLATE] -- ``template.expected_dimensions()`` provides the target
        (width, height) derived from angles * cell_h / sum(frames) * cell_w.

        Args:
            image: PIL Image to validate and downscale.
            template: Template object with ``expected_dimensions()`` method.
            algorithm: Downscaling algorithm (nearest, box, area, block-majority).
                       Defaults to "nearest"; template processing section can
                       override via ``processing_config["downscale_algorithm"]``.

        Returns:
            PIL Image (original if valid, downscaled if too large).

        Raises:
            GridError: If image dimensions are smaller than expected (can't upscale).
        """
        from scripts.pipeline.grid_validator import (
            GridValidator,
            GridError,
        )

        validator = GridValidator(template)

        auto_downscale_enabled = self._auto_downscale_enabled()
        try:
            validator.validate_image(image)
            return image
        except GridError as e:
            expected_w, expected_h = template.expected_dimensions()
            actual_w, actual_h = image.width, image.height

            # WHY: The pipeline refuses to upscale because nearest-neighbor
            # upscaling introduces blocky artifacts and bilinear/bicubic
            # upscaling blurs pixel art.  Better to fail fast so the user
            # can supply a higher-resolution source.
            if actual_w < expected_w or actual_h < expected_h:
                raise GridError(
                    template_name=template.name,
                    actual_dimensions=(actual_w, actual_h),
                    expected_dimensions=(expected_w, expected_h),
                    template_metadata={
                        "angles": template.angles,
                        "frames": template.frames,
                        "rows": template.layout_rows(),
                        "cols": template.layout_cols(),
                    },
                )

            if not auto_downscale_enabled:
                print(
                    "   Skipping auto-downscale (ASSET_PIPELINE_ENABLE_AUTO_DOWNSCALE not set); "
                    f"keeping source size {actual_w}x{actual_h} over template {expected_w}x{expected_h}"
                )
                return image

            # WHY: We take max(w_scale, h_scale) so the letterbox step can
            # preserve aspect ratio by fitting the larger axis exactly and
            # padding the shorter axis with transparent/magenta fill.
            w_scale = actual_w / expected_w
            h_scale = actual_h / expected_h
            factor = max(w_scale, h_scale)

            # WHY: Beyond 5x downscale, fine details (1-2 px lines) are lost
            # entirely.  The warning nudges the user to provide a closer-to-
            # target resolution source instead of relying on aggressive shrink.
            if factor > 5:
                print(f"Warning: Downscaling by {factor:.1f}× (recommended max 5×)")

            # WHY: Two-step resize -- letterbox first (preserves aspect ratio
            # with padding), then exact resize (hits the grid dimensions).
            # This avoids stretching non-square source images.
            resized = resize_with_letterbox(image, (expected_w, expected_h))
            resizer = ImageResizer(algorithm)
            final_image = resizer.resize(resized, (expected_w, expected_h))

            # WHY: Re-validate after resize as a paranoia check -- rounding
            # in the letterbox or resize step could produce off-by-one
            # dimensions that would cause silent misalignment in slicing.
            try:
                validator.validate_image(final_image)
                return final_image
            except GridError as re_validate_error:
                raise GridError(
                    template_name=template.name,
                    actual_dimensions=(final_image.width, final_image.height),
                    expected_dimensions=(expected_w, expected_h),
                    template_metadata={
                        "angles": template.angles,
                        "frames": template.frames,
                        "rows": template.layout_rows(),
                        "cols": template.layout_cols(),
                    },
                ) from re_validate_error

    def normalize_and_pad(self, image: Image.Image, template) -> Image.Image:
        """
        Normalize image scale and add per-angle padding.

        [PIPELINE:GENERATE] -- Runs as a post-processing pass within Stage 1,
        after grid validation/downscaling but before slicing (Stage 2).

        Ported from legacy sprite_gen.py (Phase 2 Task B).  This is the only
        place where per-angle height normalization happens -- it ensures that
        every angle row in the sprite sheet occupies the same pixel height,
        even when the source render has varying proportions (e.g. a top-down
        view is shorter than a side view).

        Algorithm summary (see module docstring for the full description):
          1. Compute raw pixels per angle from the composite sheet.
          2. Determine target cell height (explicit, auto-capped, or passthrough).
          3. Snap to 12 px grid, subtract 4 px for padding headroom.
          4. Scale each angle strip, snap width to ``total_cols * 12``.
          5. Paste strips onto a magenta canvas with 2 px top/bottom padding.

        [FLOW:TEMPLATE] -- When a template enables ``processing.normalization``,
        the ``run()`` method calls this function after grid validation.

        Args:
            image: Source PIL Image (already composited sprite sheet).
            template: Template object for layout info (currently used only as a
                      guard; actual dimensions come from ``self.asset_def``).

        Returns:
            PIL.Image: Normalized and padded image with dimensions guaranteed
            to be divisible by 12.
        """
        if not template:
            # WHY: Even without a template, normalization can proceed using the
            # raw AssetDef values.  The guard is a no-op placeholder for future
            # template-specific overrides.
            pass
        print("DEBUG: Running Normalize Logic v2")

        angles = self.asset_def.angles

        # WHY: ``frames`` can be a single int (all angles share the same frame
        # count) or a list of ints (per-angle frame counts).  Normalizing to a
        # list here lets ``sum()`` work uniformly in both cases.
        # TODO(PIPELINE-FIX): AssetDef.frames type is not enforced as List[int]
        # at the schema level -- a bare int sneaking through would cause
        # ``sum(int)`` TypeError without this guard.
        frames_config = self.asset_def.frames
        if isinstance(frames_config, int):
             frames_config = [frames_config]
        total_cols = sum(frames_config)

        cell_size = CELL_SIZE
        calc_angles = angles if angles > 0 else 1
        raw_pixels_per_angle = image.height / calc_angles

        # --- Determine target height per angle strip ---
        # WHY: Three branches handle the priority cascade:
        #   1. Explicit ``target_cells_high`` on AssetDef (user override via CLI).
        #   2. Auto-cap at 2 cells (24 px) when raw height > 48 px -- prevents
        #      absurdly tall sprites that would dominate the game viewport.
        #   3. Passthrough: raw height is already reasonable, keep as-is.
        resize_needed = False
        target_cells_high = 0

        # TODO(PIPELINE-FIX): ``target_cells_high`` is checked via hasattr
        # because it is not yet a formal AssetDef field.  Once added to the
        # dataclass, this guard can become a simple attribute read.
        if hasattr(self.asset_def, "target_cells_high") and self.asset_def.target_cells_high > 0:
             target_cells_high = self.asset_def.target_cells_high

        auto_downscale_enabled = self._auto_downscale_enabled()

        if target_cells_high > 0:
            resize_needed = True
            target_px_per_angle = target_cells_high * cell_size
            print(f"   * Explicit resize request: {target_cells_high} cells/angle")
        elif raw_pixels_per_angle > 48 and auto_downscale_enabled:
            # WHY: 48 px = 4 cells high.  Anything taller than this is almost
            # certainly an un-downscaled AI render and would produce a sprite
            # that towers over the game's 1-2 cell characters.
            resize_needed = True
            target_cells_high = 2
            target_px_per_angle = target_cells_high * cell_size
            print(f"   * Auto-resizing huge image (>48px/angle) to {target_cells_high} cells/angle ({target_px_per_angle}px)")
        else:
            target_px_per_angle = raw_pixels_per_angle
            if raw_pixels_per_angle > 48 and not auto_downscale_enabled:
                print(
                    "   * Auto-resize disabled; keeping original per-angle height "
                    f"{raw_pixels_per_angle:.2f}px"
                )

        grid_h_per_angle = int(target_px_per_angle)

        # WHY: The CP437 cell grid requires all dimensions to be multiples of
        # CELL_SIZE px.  Snapping with a >half remainder rounds up, <=half
        # rounds down, so the visual distortion from rounding is minimized.
        rem_h = grid_h_per_angle % cell_size
        if rem_h != 0:
            if rem_h > cell_size // 2: grid_h_per_angle += (cell_size - rem_h)
            else: grid_h_per_angle -= rem_h
            if grid_h_per_angle < cell_size: grid_h_per_angle = cell_size
            print(f"   * Snapped height to {grid_h_per_angle}px (grid check)")

        # WHY: 4 px is subtracted to create a 2 px padding buffer on both the
        # top and bottom of each angle strip.  This padding prevents glyph
        # bleed between adjacent angle rows in the final .xp sprite sheet.
        safe_h_per_angle = grid_h_per_angle - 4

        if safe_h_per_angle <= 0:
             # TODO(PIPELINE-FIX): Silently returning the original image when
             # padding eats all available height is surprising.  Consider raising
             # a ValueError so the user knows normalization was a no-op.
             return image

        scale = safe_h_per_angle / raw_pixels_per_angle
        scaled_h = int(image.height * scale)
        scaled_w = int(image.width * scale)

        # WHY: Each frame cell must be exactly ``cell_size`` px wide.
        # With ``total_cols`` frames per row, the total image width must be
        # ``total_cols * cell_size``.  Snapping here (round-to-nearest with
        # a half-boundary threshold) avoids off-by-one misalignment during
        # slicing.
        target_alignment = total_cols * cell_size
        remainder_w = scaled_w % target_alignment

        if remainder_w != 0:
             if remainder_w > target_alignment / 2: scaled_w += (target_alignment - remainder_w)
             else: scaled_w -= remainder_w
             if scaled_w == 0: scaled_w = target_alignment

        final_h = grid_h_per_angle * calc_angles
        final_w = scaled_w

        print(f"   Resizing content: {image.width}x{image.height} -> {scaled_w}x{scaled_h}")
        # WHY: Magenta (255, 0, 255) is the engine's transparency key color.
        # Using it as the canvas fill means padding pixels are automatically
        # treated as transparent by the C++ renderer (sprite.cpp).
        new_img = Image.new("RGB", (final_w, final_h), (255, 0, 255))

        print(f"   Applying per-angle padding (2px top/bottom)")
        raw_angle_h = image.height // calc_angles
        for a in range(calc_angles):
             # WHY: min() guard prevents reading past the image boundary when
             # the source height is not perfectly divisible by the angle count.
             y_start = a * raw_angle_h
             y_end = min((a+1) * raw_angle_h, image.height)
             angle_slice = image.crop((0, y_start, image.width, y_end))

             # WHY: LANCZOS resampling is used here (not nearest-neighbor)
             # because normalization typically shrinks AI-generated images with
             # smooth gradients where LANCZOS produces fewer artifacts.
             slice_resized = angle_slice.resize((scaled_w, safe_h_per_angle), Image.Resampling.LANCZOS)
             # WHY: +2 offset is the top padding.  Bottom padding is implicit
             # (the remaining 2 px gap before the next angle row starts).
             paste_y = (a * grid_h_per_angle) + 2
             new_img.paste(slice_resized, (0, paste_y))

        return new_img

    def validate_template_layout(self, template) -> None:
        """
        Validate template layout matches rows=angles, cols=sum(frames).

        [FLOW:TEMPLATE] -- Called at the start of ``run()`` before any image
        processing.  This is a fast-fail guard that catches template authoring
        mistakes (e.g. defining 8 angles but only 4 layout rows) before the
        pipeline spends time loading or rendering images.

        WHY two separate checks:
          - ``angles == layout_rows()`` ensures the grid has one row per camera
            angle, which is the invariant the slicer depends on.
          - ``total_frames() == layout_cols()`` ensures the column count matches
            the sum of per-animation frame counts, so every frame gets a slot.

        Args:
            template: Template object with ``layout_rows()`` and ``layout_cols()``
                      methods (see templates/models.py LayoutSection).

        Raises:
            ValueError: If template layout doesn't match expected dimensions.
        """
        layout_rows = template.layout_rows()
        layout_cols = template.layout_cols()

        if template.angles != layout_rows:
            raise ValueError(
                f"Template '{template.name}' layout validation failed: "
                f"angles ({template.angles}) != layout_rows ({layout_rows})"
            )

        # Check: template.total_frames() == template.layout_cols()
        if template.total_frames() != layout_cols:
            raise ValueError(
                f"Template '{template.name}' layout validation failed: "
                f"total_frames ({template.total_frames()}) != layout_cols ({layout_cols})"
            )

        # Layout is valid: rows=angles, cols=sum(frames)
        print(
            f"   ✓ Template layout validated: rows={layout_rows} (angles), "
            f"cols={layout_cols} (sum(frames))"
        )

    def apply_template_processing(self, asset_def, template) -> dict:
        """
        Apply template processing settings to asset_def and return processing config.

        [FLOW:TEMPLATE] -- Translates the declarative ``ProcessingSection`` from
        the template into a runtime ``processing_config`` dict that downstream
        stages consume.  This is the single point where template processing flags
        are resolved; no other code reads ``template.processing`` directly.

        The returned dict may contain any combination of:
          - ``"magenta_snap": True`` -- enable transparency keying.
          - ``"palette_quantize": True`` -- enable color quantization.
          - ``"downscale_algorithm": str`` -- override for ``validate_and_downscale()``.
          - ``"crop_center": True`` -- center-crop before slicing.

        Args:
            asset_def: Asset definition to update with processing flags.
            template: Template object with ``processing`` section
                      (ProcessingSection dataclass).

        Returns:
            dict: Processing configuration for pipeline use.
        """
        processing_config = {}

        # WHY: Templates without a processing section are valid (e.g. simple
        # file-based imports that need no transformation).  Return early to
        # avoid AttributeError chains.
        if not hasattr(template, "processing") or not template.processing:
            return processing_config

        # WHY: magenta_snap enables the magenta-to-transparent conversion in
        # the generator for AI sources that use (255,0,255) as a background key.
        if (
            hasattr(template.processing, "magenta_snap")
            and template.processing.magenta_snap
        ):
            # If asset_def has transparency attribute, enable it
            if hasattr(asset_def, "transparency"):
                if not getattr(asset_def, "transparency", False):
                    print(f"   * Enabled transparency for magenta snap (from template)")
                    # Note: AssetDef.transparency is a flag, not stored in dataclass (Phase 10-01 decision)
                    # This setting is respected by generator.generate() for AI sources
            processing_config["magenta_snap"] = True

        # WHY: Palette quantization reduces the color count to the engine's
        # fixed CP437 palette.  Without it, the processor's nearest-color
        # matching in Stage 3 can produce noisy results on full-color images.
        if (
            hasattr(template.processing, "palette_quantize")
            and template.processing.palette_quantize
        ):
            processing_config["palette_quantize"] = True
            print(f"   * Enabled palette quantization (from template)")

        # WHY: The downscale algorithm choice matters -- "box" averages pixels
        # (good for smooth AI renders), "nearest" preserves hard edges (good
        # for pixel art / Blender renders).  Template override takes priority
        # over the source-type default set in cli.py.
        if hasattr(template.processing, "downscale") and template.processing.downscale:
            processing_config["downscale_algorithm"] = template.processing.downscale
            print(
                f"   * Downscale algorithm: {template.processing.downscale} (from template)"
            )

        # WHY: Center crop trims equal margins from all four sides, useful
        # when the source image has borders or padding that should not appear
        # in the final sprite.
        if (
            hasattr(template.processing, "crop_center")
            and template.processing.crop_center
        ):
            processing_config["crop_center"] = True
            print(f"   * Enabled center crop (from template)")

        return processing_config

    def should_generate_debug_sheet(self, template) -> bool:
        """
        Check if debug sheet should be generated (opt-in via template.debug.label_sheet).

        [FLOW:TEMPLATE] -- Debug sheets are **opt-in only** to avoid polluting
        the staging directory on every pipeline run.  The template author must
        explicitly set ``debug.label_sheet`` (a path string) or the legacy
        ``debug.labels: true`` flag.

        Args:
            template: Template object with optional ``debug`` section.

        Returns:
            bool: True if debug sheet should be generated.
        """
        # WHY: Opt-in, not opt-out.  Debug sheets are large PNGs that slow
        # down batch runs.  Only generate when the template author asks for it.
        if template and hasattr(template, "debug") and template.debug:
            if hasattr(template.debug, "label_sheet"):
                label_sheet = template.debug.label_sheet
                # TODO(PIPELINE-FIX): The model default for label_sheet is
                # "debug/labels.png", which is indistinguishable from an
                # explicit opt-in.  Consider using None as the default so
                # presence alone signals intent.
                if isinstance(label_sheet, str) and label_sheet:
                    return True

            # WHY: Legacy templates used a boolean ``labels`` flag instead of
            # a path.  This branch preserves backward compatibility.
            if hasattr(template.debug, "labels") and template.debug.labels:
                return True

        return False

    def get_debug_sheet_path(self, template, asset_name: str) -> Path:
        """
        Get debug sheet output path.

        [FLOW:TEMPLATE] -- Prefers the explicit ``template.debug.label_sheet``
        path when set; otherwise falls back to the conventional staging location
        ``staging/debug/{name}_debug.png``.

        Args:
            template: Template object with optional ``debug`` section.
            asset_name: Asset name used to build the default filename.

        Returns:
            Path: Output path for the debug PNG file.
        """
        if (
            template
            and hasattr(template, "debug")
            and template.debug
            and template.debug.label_sheet
        ):
            return Path(template.debug.label_sheet)

        # Default: staging/debug/{name}_debug.png
        return STAGING_DIR / "debug" / f"{asset_name}_debug.png"

    def save_intermediate_sheet_if_needed(
        self, template, frames, asset_name: str
    ) -> None:
        """
        Save intermediate sprite sheet to staging/sheets/ if enabled.

        [FLOW:TEMPLATE] -- Opt-in via ``template.debug.save_intermediate``.
        Reconstructs the sliced frames into a single composite PNG and writes
        it to ``staging/sheets/`` with a UTC timestamp suffix.  This is useful
        for visually inspecting the slicer output before it enters the
        irreversible glyph-matching stage.

        [PIPELINE:SLICE] -- Executes after Stage 3 (Processing) in ``run()``
        but captures the pre-glyph pixel state (the original ``frames`` list
        from Stage 2, not the processed glyph grids from Stage 3).

        Args:
            template: Template object with optional ``debug.save_intermediate`` flag.
            frames: List of sliced PIL Image frames (output of ImageSlicer).
            asset_name: Asset name used to build the output filename.
        """
        if not (
            template
            and hasattr(template, "debug")
            and template.debug
            and hasattr(template.debug, "save_intermediate")
            and template.debug.save_intermediate
        ):
            return

        if not frames:
            print("   Note: Intermediate sheet save requested but no frames available")
            return

        # WHY: Reconstruct the grid layout (rows=angles, cols=sum(frames)) so
        # the debug PNG mirrors what the slicer decomposed.  This makes it
        # trivial to visually verify that frame boundaries are correct.
        total_cols = sum(self.asset_def.frames)
        total_rows = self.asset_def.angles
        frame_w, frame_h = frames[0].size
        sheet_w = frame_w * total_cols
        sheet_h = frame_h * total_rows

        # WHY: Preserve the palette mode ("P") if frames were already quantized,
        # so the debug PNG accurately represents the color state at this stage.
        sheet_mode = frames[0].mode
        sheet = Image.new(sheet_mode, (sheet_w, sheet_h))
        if sheet_mode == "P" and frames[0].palette:
            sheet.putpalette(frames[0].palette)

        idx = 0
        for row in range(total_rows):
            for col in range(total_cols):
                if idx >= len(frames):
                    break
                sheet.paste(frames[idx], (col * frame_w, row * frame_h))
                idx += 1

        # WHY: UTC timestamp prevents filename collisions when the pipeline is
        # run multiple times on the same asset (e.g. during iterative tuning).
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = (
            STAGING_DIR / "sheets" / f"{asset_name}_intermediate_{timestamp}.png"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(str(output_path), "PNG")
        print(f"   ✓ Intermediate sheet: {output_path}")

    def generate_debug_sheet_if_needed(self, template, frames_info, asset_name: str):
        """
        Generate debug sheet if template configures it.

        Delegates to ``render_debug_sheet()`` (debug_sheet.py) which draws a
        labeled grid overlay showing angle/frame indices, animation names, and
        grid coordinates on top of the sprite sheet.

        [FLOW:TEMPLATE] -- Gated by ``should_generate_debug_sheet()``.
        Output path is determined by ``get_debug_sheet_path()``.

        Args:
            template: Template object with ``debug`` configuration section.
            frames_info: List of dicts with per-frame metadata (angle, frame
                         index, anim_name, source_path, col, row).
            asset_name: Asset name for default path generation.

        Returns:
            PIL.Image | None: Debug image if generated, None if opt-out.
        """
        # Check if debug sheet should be generated (opt-in via debug.label_sheet)
        if not self.should_generate_debug_sheet(template):
            return None

        # Get debug sheet output path
        debug_path = self.get_debug_sheet_path(template, asset_name)

        # Ensure parent directory exists
        debug_path.parent.mkdir(parents=True, exist_ok=True)

        # Extract label_format from template.debug if available
        label_format = None
        if template and hasattr(template, "debug") and template.debug:
            label_format = getattr(template.debug, "label_format", None)

        # Render debug sheet with optional label format
        debug_img = render_debug_sheet(template, frames_info, label_format=label_format)

        # Save to disk
        debug_img.save(str(debug_path), "PNG")
        # WHY: Use absolute path so user can easily open/copy the path
        abs_debug_path = debug_path.resolve() if hasattr(debug_path, 'resolve') else Path(debug_path).resolve()
        print(f"   ✓ Debug sheet: {abs_debug_path}")

        return debug_img

    def _run_pre_slice_check(self, image, debug_trace) -> dict | None:
        """Run the optional pre-slice grid check hook.

        Compares the detected grid (via ``grid_detect.detect_grid``) against
        any user-configured ``slice_spec``.  If content-based detection fails,
        falls back to ``infer_sheet_spec`` as a deterministic geometry-based
        baseline. Emits a warning on mismatch and optionally upgrades to a hard
        ``ValueError`` when strict mode is on.

        [PIPELINE:PRE-SLICE] -- Runs AFTER background alignment, BEFORE slicing.
        Gated by ``self.asset_def.pre_slice_check`` (default False).

        Args:
            image: PIL Image (post-background-alignment, pre-slicing).
            debug_trace: ``_PipelineDebugTrace`` instance for event logging.

        Returns:
            Report dict with detected/configured grids and match status,
            or None if the hook is disabled.
        """
        if not getattr(self.asset_def, "pre_slice_check", False):
            return None

        from scripts.pipeline.grid_detect import detect_grid
        from scripts.pipeline.service.slicing import infer_sheet_spec
        from scripts.pipeline.service.config_resolver import resolve_projs

        angles = self.asset_def.angles
        frames_list = (
            self.asset_def.frames
            if isinstance(self.asset_def.frames, list)
            else [self.asset_def.frames]
        )
        frames_tuple = tuple(frames_list)
        projs = resolve_projs(angles, explicit_projs=self.asset_def.projs)

        pipeline_cfg = getattr(self.asset_def, "pipeline_config", None)
        grid_cfg = (
            pipeline_cfg.get("grid_settings", {})
            if isinstance(pipeline_cfg, dict)
            else {}
        )

        try:
            min_grid_size = float(grid_cfg.get("min_grid_size", 4.0))
        except (TypeError, ValueError):
            min_grid_size = 4.0
        try:
            refine_intensity = float(grid_cfg.get("refine_intensity", 0.25))
        except (TypeError, ValueError):
            refine_intensity = 0.25

        # Detect grid from image content first.
        detected = None
        detect_method = "grid_detect"
        detect_confidence = 0.0
        detect_error = None

        try:
            grid_result = detect_grid(
                image,
                min_size=min_grid_size,
                refine_intensity=refine_intensity,
            )
            detect_confidence = float(grid_result.confidence)
            if grid_result.method != "none" and grid_result.grid_cols > 0 and grid_result.grid_rows > 0:
                detected = {
                    "cell_w": int(grid_result.cell_w),
                    "cell_h": int(grid_result.cell_h),
                    "cols": int(grid_result.grid_cols),
                    "rows": int(grid_result.grid_rows),
                }
                detect_method = f"grid_detect:{grid_result.method}"
        except Exception as exc:
            detect_error = str(exc)

        # Fallback: deterministic geometric inference when detection is unavailable.
        if detected is None:
            try:
                inferred = infer_sheet_spec(image.size, angles, frames_tuple, projs)
                detected = {
                    "cell_w": inferred.cell_w_px,
                    "cell_h": inferred.cell_h_px,
                    "cols": inferred.cols,
                    "rows": inferred.rows,
                }
                detect_method = "infer_sheet_spec_fallback"
            except ValueError as exc:
                err_msg = str(exc)
                if detect_error:
                    err_msg = f"grid_detect failed ({detect_error}); infer_sheet_spec failed ({err_msg})"
                report = {
                    "detected_grid": None,
                    "configured_grid": None,
                    "match": False,
                    "mismatches": [],
                    "method": detect_method,
                    "confidence": detect_confidence,
                    "error": err_msg,
                }
                debug_trace.log("pre_slice_check", **report)
                print(
                    f"   Pre-slice check: detection failed -- {err_msg}",
                    file=sys.stderr,
                )
                if getattr(self.asset_def, "pre_slice_check_strict", False):
                    raise ValueError(
                        f"Pre-slice grid check (strict): detection failed -- {err_msg}"
                    ) from exc
                return report

        # Compare against configured spec if present
        spec = getattr(self.asset_def, "slice_spec", None)
        if spec is not None and spec.cell_w_px is not None:
            configured = {
                "cell_w": spec.cell_w_px,
                "cell_h": spec.cell_h_px,
                "cols": spec.cols,
                "rows": spec.rows,
            }
            mismatches = [
                k for k in ("cell_w", "cell_h", "cols", "rows")
                if detected.get(k) != configured.get(k)
            ]
            match = len(mismatches) == 0
        else:
            configured = None
            mismatches = []
            match = True  # No explicit spec to mismatch against

        report = {
            "detected_grid": detected,
            "configured_grid": configured,
            "match": match,
            "mismatches": mismatches,
            "method": detect_method,
            "confidence": detect_confidence,
        }
        debug_trace.log("pre_slice_check", **report)

        if not match:
            msg = (
                f"Pre-slice grid check mismatch: "
                f"detected {detected}, configured {configured}, "
                f"mismatched fields: {mismatches} "
                f"(method={detect_method}, confidence={detect_confidence:.2f})"
            )
            print(f"   Warning: {msg}", file=sys.stderr)
            if getattr(self.asset_def, "pre_slice_check_strict", False):
                raise ValueError(msg)
        else:
            print(
                f"   Pre-slice check: grid OK (detected {detected}, "
                f"method={detect_method}, confidence={detect_confidence:.2f})"
            )

        return report

    def _run_pixel_perfect(self, image, debug_trace):
        """Run the optional pixel-perfect normalization hook.

        When ``pixel_perfect_mode`` is ``"auto_adjust"``, calls
        ``crop_and_center_frames()`` from ``auto_adjust.py`` to center
        sprite content within each 12x12 cell.  The hook is a no-op when
        the mode is ``"off"`` (default) or when image dimensions are not
        12px-aligned.

        [PIPELINE:PRE-SLICE] -- Runs AFTER the grid check hook, BEFORE slicing.
        Gated by ``self.asset_def.pixel_perfect_mode`` (default "off").

        Args:
            image: PIL Image (post-background-alignment, pre-slicing).
            debug_trace: ``_PipelineDebugTrace`` instance for event logging.

        Returns:
            PIL Image -- either the original (no-op) or the adjusted result.
        """
        mode = getattr(self.asset_def, "pixel_perfect_mode", "off")
        if mode == "off":
            return image

        if mode != "auto_adjust":
            print(
                f"   Warning: unknown pixel_perfect_mode '{mode}', skipping",
                file=sys.stderr,
            )
            debug_trace.log(
                "pixel_perfect",
                mode=mode,
                applied=False,
                reason="unknown_mode",
            )
            return image

        # Dimension guard: crop_and_center_frames uses hardcoded 12x12 cells
        if image.width % 12 != 0 or image.height % 12 != 0:
            print(
                "   Warning: pixel-perfect skipped: image dimensions "
                f"{image.width}x{image.height} not 12px-aligned",
                file=sys.stderr,
            )
            debug_trace.log(
                "pixel_perfect",
                mode="auto_adjust",
                applied=False,
                reason="not_12px_aligned",
                image_size=[image.width, image.height],
            )
            return image

        from scripts.pipeline.auto_adjust import crop_and_center_frames

        result = crop_and_center_frames(image)
        debug_trace.log(
            "pixel_perfect",
            mode="auto_adjust",
            applied=True,
            image_size=[result.width, result.height],
        )
        print("   Pre-slice: pixel-perfect normalization applied (auto_adjust)")
        return result

    def _fallback_slice_spec_from_content(self, image, angles, slice_frames, debug_trace):
        """Build a best-effort slicing spec for non-divisible sheets.

        When strict grid inference fails, use content extraction (connected
        components) to estimate cell size, then center a regular grid.
        """
        from scripts.pipeline.service.slicing import SlicingSpec

        cols = max(1, int(sum(slice_frames)))
        rows = max(1, int(angles))
        if image.width < cols or image.height < rows:
            return None

        est_cell_w = max(1, image.width // cols)
        est_cell_h = max(1, image.height // rows)
        source_method = "floor_division"

        try:
            from scripts.pipeline.web_api.workbench_session import extract_sprites_from_source

            extraction = extract_sprites_from_source(
                image,
                alpha_threshold=8,
                min_size=max(2, min(est_cell_w, est_cell_h) // 4),
            )
            sprites = extraction.get("sprites", [])
            widths = sorted(
                int(s.get("width", 0))
                for s in sprites
                if int(s.get("width", 0)) > 0
            )
            heights = sorted(
                int(s.get("height", 0))
                for s in sprites
                if int(s.get("height", 0)) > 0
            )
            if widths and heights:
                est_cell_w = widths[len(widths) // 2]
                est_cell_h = heights[len(heights) // 2]
                source_method = "content_extraction_median"
        except Exception as exc:
            debug_trace.log(
                "grid_fallback_extract_error",
                error=str(exc),
            )

        # Clamp so the requested grid always fits the image.
        est_cell_w = max(1, min(est_cell_w, image.width // cols))
        est_cell_h = max(1, min(est_cell_h, image.height // rows))

        rem_x = max(0, image.width - cols * est_cell_w)
        rem_y = max(0, image.height - rows * est_cell_h)

        spec = SlicingSpec(
            mode="grid",
            cell_w_px=est_cell_w,
            cell_h_px=est_cell_h,
            cols=cols,
            rows=rows,
            margin_x_px=rem_x // 2,
            margin_y_px=rem_y // 2,
            spacing_x_px=0,
            spacing_y_px=0,
        )
        debug_trace.log(
            "grid_fallback",
            applied=True,
            method=source_method,
            image_size=[int(image.width), int(image.height)],
            cols=int(cols),
            rows=int(rows),
            cell_w=int(est_cell_w),
            cell_h=int(est_cell_h),
            remainder_x=int(rem_x),
            remainder_y=int(rem_y),
        )
        return spec

    @staticmethod
    def _check_fullsheet_guard(frames_list, source_image, max_coverage):
        """Check if a single extracted sprite covers too much of the source.

        [FLOW:BRANCH-GUARD] Defense-in-depth: rejects extraction branches
        where background separation failed and the "sprite" IS the full sheet.

        Returns:
            (should_prune: bool, ratio: float)
        """
        if max_coverage >= 1.0 or len(frames_list) != 1:
            return False, 0.0
        _sp_area = frames_list[0].width * frames_list[0].height
        _src_area = source_image.width * source_image.height
        if _src_area <= 0:
            return False, 0.0
        ratio = _sp_area / _src_area
        return ratio > max_coverage, ratio

    def _enforce_stage5_review(self, debug_trace, output_path):
        """Enforce Stage 5 human review gate after quality gates (if enabled).

        Reads require_human_stage5_review from pipeline config grid_settings.
        Fails closed when the signoff JSON is missing or invalid.
        """
        _pipeline_config = getattr(self.asset_def, "pipeline_config", None)
        _grid_settings = _cfg_section(_pipeline_config, "grid_settings")
        _require = bool(
            _cfg_get(_grid_settings, "require_human_stage5_review", False)
        )
        if not _require:
            return
        _review_path_5 = str(
            _cfg_get(
                _grid_settings,
                "human_stage5_review_path",
                "docs/research/ascii/verification/manual-stage5-gates-review.json",
            )
        )
        _review_5 = _load_human_stage_review(
            stage="stage5_gates",
            review_path=_review_path_5,
            request_id=debug_trace.request_id,
            artifact_path=str(output_path),
        )
        print(
            f"   Stage 5 human review approved by {_review_5['reviewer']} "
            f"({_review_5['path']})"
        )
        debug_trace.log(
            "stage5_human_review",
            approved=True,
            reviewer=_review_5["reviewer"],
            path=_review_5["path"],
        )

    def _try_manifest_slice(self, image, debug_trace):
        """Load pre-extracted frames from a canonical manifest if available.

        Returns (frames, manifest) tuple where frames is a list of PIL Images
        and manifest is the CanonicalManifest, or (None, None) if no manifest
        is active.  This is the Phase 13.3 manifest-driven pipeline path.

        [FLOW:NORMALIZE] [PIPELINE:NORMALIZE]
        """
        _pipeline_config = getattr(self.asset_def, "pipeline_config", None)
        _norm = _cfg_section(_pipeline_config, "normalization_settings")
        _mode = str(_cfg_get(_norm, "normalize_input_mode", "auto")).strip().lower()

        if _mode == "off":
            return None, None

        # Check for pre-existing manifest path.
        manifest_path_str = str(
            _cfg_get(_norm, "normalized_manifest_path", "")
        ).strip()

        if not manifest_path_str:
            # Auto mode: try staging/normalized/<name>/manifest.json.
            staging_dir = Path(__file__).resolve().parent / "staging" / "normalized"
            manifest_path = staging_dir / self.asset_def.name / "manifest.json"
        else:
            manifest_path = Path(manifest_path_str)

        if not manifest_path.exists():
            if _mode == "required":
                raise FileNotFoundError(
                    f"normalize_input_mode=required but manifest not found: "
                    f"{manifest_path}. Run normalization first."
                )
            # Auto mode: manifest doesn't exist yet, fall through to slicer.
            return None, None

        from scripts.pipeline.canonical_manifest import CanonicalManifest

        manifest = CanonicalManifest.load(manifest_path)
        manifest_dir = manifest_path.parent

        print(
            f"   Manifest-driven slice: {manifest.name} "
            f"({manifest.grid.rows}x{manifest.grid.cols} grid, "
            f"{len(manifest.frame_map)} frames)"
        )
        debug_trace.log(
            "manifest_slice_mode",
            manifest_path=str(manifest_path),
            grid_rows=manifest.grid.rows,
            grid_cols=manifest.grid.cols,
            frame_count=len(manifest.frame_map),
            confidence=manifest.normalization.confidence,
        )

        # Load frames in frame_map order.
        frames = []
        for ref in manifest.frame_map:
            frame_path = manifest_dir / ref.file
            if not frame_path.exists():
                raise FileNotFoundError(
                    f"Manifest references missing frame: {ref.file} "
                    f"(manifest: {manifest_path})"
                )
            frames.append(Image.open(frame_path).convert("RGBA"))

        return frames, manifest

    def _run_normalization_stage(self, image, norm_cfg, mode, debug_trace):
        """Run Stage N0: normalize source sheet into canonical package.

        Produces a canonical manifest + frames directory. Subsequent calls to
        _try_manifest_slice() will load frames from this package.

        [FLOW:NORMALIZE] [PIPELINE:NORMALIZE]
        """
        from scripts.pipeline.normalize_sheet import normalize_sheet

        staging_dir = Path(__file__).resolve().parent / "staging" / "normalized"
        manifest_path = staging_dir / self.asset_def.name / "manifest.json"

        # Skip if manifest already exists (idempotent).
        if manifest_path.exists():
            print(f"   Stage N0: Using existing manifest: {manifest_path}")
            debug_trace.log("stage_n0_skip", reason="manifest_exists",
                            path=str(manifest_path))
            return

        print("Stage N0: Input normalization...")
        min_conf = float(_cfg_get(norm_cfg, "min_confidence", 0.5))

        # Extract geometry hints from asset_def.
        angles = int(getattr(self.asset_def, "angles", 1))
        frames_raw = getattr(self.asset_def, "frames", [1])
        anim_frames = (
            [int(x) for x in frames_raw]
            if isinstance(frames_raw, (list, tuple))
            else [int(frames_raw)]
        )
        source_projs = int(getattr(self.asset_def, "source_projs", 1))

        # Save source image to temp file for normalize_sheet.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.save(tmp.name)
            source_path = Path(tmp.name)

        try:
            result = normalize_sheet(
                source_path=source_path,
                name=self.asset_def.name,
                angles=angles,
                anim_frames=anim_frames,
                source_projs=source_projs,
                min_confidence=min_conf,
            )
            print(
                f"   \u2713 Normalized: {result.rows}x{result.cols}, "
                f"confidence={result.confidence:.2f}"
            )
            debug_trace.log(
                "stage_n0_complete",
                rows=result.rows,
                cols=result.cols,
                confidence=result.confidence,
                output_dir=str(result.output_dir),
            )

            # ── N0 human review gate ──
            _require_n0 = bool(_cfg_get(norm_cfg, "require_human_n0_review", False))
            if _require_n0:
                _n0_review_path = str(_cfg_get(
                    norm_cfg, "human_n0_review_path",
                    "docs/research/ascii/verification/manual-stageN0-normalize-review.json",
                ))
                _review_n0 = _load_human_stage_review(
                    "n0_normalize",
                    _n0_review_path,
                    self.request_id,
                    artifact_path=str(result.output_dir / "artifacts" / "normalize_preview.png"),
                )
                print(
                    f"   \u2713 Human N0 review approved by {_review_n0['reviewer']} "
                    f"({_review_n0['notes'][:60]})"
                )
                debug_trace.log(
                    "n0_human_review",
                    approved=True,
                    reviewer=_review_n0["reviewer"],
                    path=_review_n0["path"],
                )
        except ValueError as e:
            if mode == "required":
                raise
            # Auto mode: normalization failed, fall through to legacy path.
            print(f"   Stage N0: Normalization failed ({e}), using legacy path")
            debug_trace.log("stage_n0_failed", error=str(e), mode=mode)
        finally:
            source_path.unlink(missing_ok=True)

    def _run_stages_2_to_4(
        self,
        image,
        effective_angles,
        projs,
        reflections_pre_baked,
        template,
        processing_config,
        debug_trace,
        branch=None,
        frames_override=None,
        output_suffix="",
    ):
        """Run stages 2-4 for a single processing path.

        [PIPELINE:ORCHESTRATOR] -- Extracted from run() for branch-loop reuse.
        When ``branch`` is provided, branch-specific overrides apply:
          - ``frames_override``: skip Stage 2 slicing (extraction branches)
          - ``branch.source_cell_px``: inject into process config
          - ``output_suffix``: appended to output .xp filename

        Returns:
            Path to the output .xp file (not resolved).
        """
        # ================================================================
        # [PIPELINE:SLICE] Stage 2/4: Slicing
        # ================================================================
        # Extract pipeline config + grid settings at method level so human
        # review gates for stages 3/4/5 can access them regardless of the
        # frames_override branch taken below.
        _pipeline_config = getattr(self.asset_def, "pipeline_config", None)
        _grid_settings = _cfg_section(_pipeline_config, "grid_settings")

        # ── Manifest-driven path (Phase 13.3): bypass slicer ──
        _manifest_frames, _manifest = self._try_manifest_slice(image, debug_trace)
        _manifest_active = _manifest_frames is not None
        if _manifest_active:
            frames = _manifest_frames
            effective_angles = _manifest.angles

            # Scope: current normalizer produces len(anim_frames)==angles,
            # source_projs==1. Broader manifest shapes fail-closed.
            if _manifest.source_projs != 1:
                raise ValueError(
                    f"Manifest source_projs={_manifest.source_projs} not supported in "
                    f"manifest pipeline path (only source_projs=1)."
                )
            if len(_manifest.anim_frames) != effective_angles:
                raise ValueError(
                    f"Manifest anim_frames length {len(_manifest.anim_frames)} != "
                    f"angles {effective_angles}. Uniform spec not yet supported."
                )
            if len(set(_manifest.anim_frames)) != 1:
                raise ValueError(
                    f"Non-uniform anim_frames {_manifest.anim_frames} not supported."
                )

            # Validate frame count matches manifest shape
            _expected = sum(_manifest.anim_frames) * _manifest.source_projs
            if len(frames) != _expected:
                raise ValueError(
                    f"Manifest frame count {len(frames)} != expected {_expected}"
                )

            # RGBA -> magenta composite: normalize_sheet creates RGBA frames
            # with transparent padding (_save_frame). Pipeline processors
            # expect RGB with magenta (255,0,255) transparency key.
            def _rgba_to_magenta(f):
                if f.mode == 'RGBA':
                    bg = Image.new('RGB', f.size, (255, 0, 255))
                    bg.paste(f, mask=f.split()[3])
                    return bg
                return f.convert('RGB') if f.mode != 'RGB' else f
            frames = [_rgba_to_magenta(f) for f in frames]

            # projs: engine contract -- angles > 0 implies projs = 2.
            # Reflection generation already ran in run() pre-N0 (line 2826).
            # Manifest frames already contain both original + reflected data.
            projs = 2 if effective_angles > 0 else 1

            # slice_frames: anim_frames[0] = total frames per angle INCLUDING
            # reflections. This is the actual column count per angle row.
            slice_frames = [_manifest.anim_frames[0]]

            print(f"   Using {len(frames)} manifest-driven frames (reflections pre-baked)")
            debug_trace.log("stage2_skip", reason="manifest_slice", count=len(frames))
        elif frames_override is not None:
            # Extraction branch: sprites are already individual frames
            frames = list(frames_override)
            print(f"   Using {len(frames)} pre-extracted frames (skip slicing)")
            debug_trace.log("stage2_skip", reason="frames_override", count=len(frames))
        else:
            print("Stage 2/4: Slicing...")
            slicer = ImageSlicer()
            _extract_settings = _cfg_section(_pipeline_config, "extract_settings")
            _slice_mode = str(
                _cfg_get(_grid_settings, "slice_mode", "global_grid") or "global_grid"
            ).strip().lower()
            _rowcol_fallback_to_grid = bool(
                _cfg_get(_grid_settings, "rowcol_fallback_to_grid", True)
            )
            _bg_tol = float(_cfg_get(_extract_settings, "bg_tolerance", 30.0))
            # [PIPELINE:SLICE] Column semantics:
            # - frames_include_projs=False: AssetDef.frames is semantic per-proj
            #   counts (e.g. [8]); slicer needs frames*projs columns.
            # - frames_include_projs=True: AssetDef.frames already includes projs
            #   multiplication (reformatter compatibility path).
            frames_config = self.asset_def.frames if isinstance(self.asset_def.frames, list) else [self.asset_def.frames]
            frames_include_projs = bool(
                getattr(self.asset_def, "frames_include_projs", False)
            )
            # source_projs=2 implies frames already include projection columns
            _source_projs = getattr(self.asset_def, "source_projs", None)
            if _source_projs == 2:
                frames_include_projs = True
            if projs > 1 and not frames_include_projs:
                slice_frames = [f * int(projs) for f in frames_config]
            else:
                slice_frames = frames_config

            # ---- Grid decision event (emitted on every run, before any checks) ----
            from scripts.pipeline.slicer import _spec_is_active
            spec = getattr(self.asset_def, "slice_spec", None)
            from scripts.pipeline.service.slicing import compute_grid_diagnostics
            # slice_frames already includes projection expansion when projs>1.
            grid_diag_projs = 1 if int(projs) > 1 else int(projs)
            grid_diag = compute_grid_diagnostics(
                img_size=(image.width, image.height),
                angles=effective_angles,
                frames=tuple(slice_frames),
                projs=grid_diag_projs,
                spec=spec,
            )
            debug_trace.log(
                "grid_decision",
                mode=grid_diag.method,
                image_size=[image.width, image.height],
                resolved_cols=grid_diag.total_cols,
                resolved_rows=grid_diag.rows,
                resolved_cell_w=grid_diag.cell_w_px,
                resolved_cell_h=grid_diag.cell_h_px,
                remainder_x=grid_diag.remainder_x,
                remainder_y=grid_diag.remainder_y,
                divisible=grid_diag.divisible,
                confidence=grid_diag.confidence,
                error=grid_diag.error,
                spec_active=bool(_spec_is_active(spec)),
                angles=int(effective_angles),
                frames=list(slice_frames),
                projs=int(projs),
            )

            if not _spec_is_active(spec) and not grid_diag.divisible:
                fallback_spec = self._fallback_slice_spec_from_content(
                    image=image,
                    angles=effective_angles,
                    slice_frames=slice_frames,
                    debug_trace=debug_trace,
                )
                if fallback_spec is not None:
                    spec = fallback_spec
                    print(
                        "   Non-divisible grid detected: using content-aware "
                        "fallback slicing."
                    )

            # WHY: Final invariant check -- every downstream stage (slicer,
            # processor, assembler) assumes dimensions are divisible by
            # CELL_SIZE.  Catching violations here produces a clear error
            # instead of a cryptic off-by-one crash later.
            # When SlicingSpec provides explicit cell sizes, skip this check
            # since the slicer will use those dimensions directly.
            if not _spec_is_active(spec):
                if image.width % CELL_SIZE != 0 or image.height % CELL_SIZE != 0:
                    print(
                        "   Warning: non-divisible source image dimensions "
                        f"{image.size}; continuing with best-effort slicing.",
                        file=sys.stderr,
                    )

            if _spec_is_active(spec):
                frames = slicer.slice(
                    image,
                    effective_angles,
                    slice_frames,
                    slice_spec=spec,
                    slice_mode=_slice_mode,
                    bg_tolerance=_bg_tol,
                    rowcol_fallback_to_grid=_rowcol_fallback_to_grid,
                )
            else:
                frames = slicer.slice(
                    image,
                    effective_angles,
                    slice_frames,
                    slice_mode=_slice_mode,
                    bg_tolerance=_bg_tol,
                    rowcol_fallback_to_grid=_rowcol_fallback_to_grid,
                )
            print(f"   Sliced into {len(frames)} frames")
            expected_frames = int(effective_angles) * int(sum(slice_frames))
            unique_frame_sizes = sorted({(int(f.width), int(f.height)) for f in frames})
            debug_trace.log(
                "slicing",
                expected_frames=expected_frames,
                actual_frames=len(frames),
                angles=int(effective_angles),
                slice_frames=list(slice_frames),
                unique_frame_sizes=[[w, h] for w, h in unique_frame_sizes],
                spec_active=bool(_spec_is_active(spec)),
                slice_mode=_slice_mode,
            )

            # [PIPELINE:SLICE] Content-aware correction (Track 3, opt-in)
            _content_correction = bool(
                _cfg_get(_grid_settings, "content_correction", False)
            )
            if _content_correction:
                from scripts.pipeline.slicer import content_correct_slice
                _max_shift = _cfg_get(
                    _grid_settings, "content_correction_max_shift", 0.25
                )
                _spec_margins = (
                    (spec.margin_x_px, spec.margin_y_px,
                     spec.spacing_x_px, spec.spacing_y_px)
                    if _spec_is_active(spec)
                    else (0, 0, 0, 0)
                )
                corrected_frames, cc_diagnostics = content_correct_slice(
                    source_image=image,
                    angles=effective_angles,
                    frames_list=slice_frames,
                    bg_tolerance=float(_bg_tol),
                    max_shift_ratio=float(_max_shift),
                    margin_x=_spec_margins[0],
                    margin_y=_spec_margins[1],
                    spacing_x=_spec_margins[2],
                    spacing_y=_spec_margins[3],
                )
                if len(corrected_frames) == len(frames):
                    frames = corrected_frames
                    print(f"   Content correction applied (avg shift {cc_diagnostics.get('avg_shift_px', 0):.1f}px)")
                    debug_trace.log(
                        "content_correction",
                        applied=True,
                        avg_shift_px=cc_diagnostics.get("avg_shift_px", 0),
                        split_count=cc_diagnostics.get("split_count", 0),
                        split_ratio=cc_diagnostics.get("split_ratio", 0),
                        bleed_ratio=cc_diagnostics.get("bleed_ratio", 0),
                        bleed_pair_count=len(cc_diagnostics.get("bleed_pairs", [])),
                    )
                else:
                    print(
                        f"   Content correction frame count mismatch "
                        f"({len(corrected_frames)} vs {len(frames)}), skipping",
                        file=sys.stderr,
                    )
                    debug_trace.log("content_correction", applied=False,
                                    reason="frame_count_mismatch")

            stage2_path = None
            stage2_img = _compose_frame_sheet(
                frames,
                rows=int(effective_angles),
                cols=int(sum(slice_frames)),
            )
            if stage2_img is not None:
                stage2_path = debug_trace.save_image("stage2_slice_grid", stage2_img)
                if stage2_path:
                    debug_trace.log("artifact", label="stage2_slice_grid", path=stage2_path)

            # [PIPELINE:SLICE] Optional human checkpoint at Stage 2.
            # Use this to manually verify that grid lines do not intersect sprites.
            _require_human_stage2_review = bool(
                _cfg_get(_grid_settings, "require_human_stage2_review", False)
            )
            if _require_human_stage2_review:
                from scripts.pipeline.slicer import compute_slice_diagnostics

                _slice_diag = compute_slice_diagnostics(
                    frames=frames,
                    grid_cols=int(sum(slice_frames)),
                )
                debug_trace.log(
                    "slice_diagnostics",
                    split_ratio=float(_slice_diag.get("split_ratio", 0.0)),
                    bleed_ratio=float(_slice_diag.get("bleed_ratio", 0.0)),
                    split_count=int(_slice_diag.get("split_count", 0)),
                    bleed_pair_count=len(_slice_diag.get("bleed_pairs", [])),
                )

                _review_path = str(
                    _cfg_get(
                        _grid_settings,
                        "human_stage2_review_path",
                        "docs/research/ascii/verification/manual-stage2-slice-review.json",
                    )
                )
                _review = _load_human_stage_review(
                    stage="stage2_slice",
                    review_path=_review_path,
                    request_id=debug_trace.request_id,
                    artifact_path=stage2_path,
                )
                print(
                    f"   ✓ Human Stage 2 review approved by {_review['reviewer']} "
                    f"({_review['path']})"
                )
                debug_trace.log(
                    "stage2_human_review",
                    approved=True,
                    reviewer=_review["reviewer"],
                    path=_review["path"],
                )

        # [PIPELINE:SLICE] Hard-fail split/bleed guard (opt-in via config)
        # Only runs on the slice path — extraction branches (frames_override)
        # and manifest-driven path skip slicing entirely.
        if frames_override is None and not _manifest_active:
            _hf_split = float(_cfg_get(_grid_settings, "hard_fail_split_ratio", 0.0))
            _hf_bleed = float(_cfg_get(_grid_settings, "hard_fail_bleed_ratio", 0.0))
        else:
            _hf_split = 0.0
            _hf_bleed = 0.0
        if (_hf_split > 0.0 or _hf_bleed > 0.0) and frames and len(frames) > 0:
            from scripts.pipeline.slicer import compute_slice_diagnostics

            _hf_diag = compute_slice_diagnostics(
                frames=frames,
                grid_cols=int(sum(slice_frames)),
            )
            _hf_sr = float(_hf_diag.get("split_ratio", 0.0))
            _hf_br = float(_hf_diag.get("bleed_ratio", 0.0))
            debug_trace.log(
                "hard_fail_split_bleed_check",
                split_ratio=_hf_sr,
                split_threshold=_hf_split,
                bleed_ratio=_hf_br,
                bleed_threshold=_hf_bleed,
            )
            if _hf_split > 0.0 and _hf_sr > _hf_split:
                raise ValueError(
                    f"Hard fail: split_ratio {_hf_sr:.4f} exceeds threshold "
                    f"{_hf_split:.4f}. Grid lines intersect sprites."
                )
            if _hf_bleed > 0.0 and _hf_br > _hf_bleed:
                raise ValueError(
                    f"Hard fail: bleed_ratio {_hf_br:.4f} exceeds threshold "
                    f"{_hf_bleed:.4f}. Content bleeds across cell boundaries."
                )

        # ================================================================
        # [PIPELINE:PROCESS] Stage 3/4: Processing (pixel -> glyph grid)
        # ================================================================
        print("Stage 3/4: Processing...")
        pipeline_cfg = getattr(self.asset_def, "pipeline_config", None)
        process_cfg = (
            pipeline_cfg.get("process_settings", {})
            if isinstance(pipeline_cfg, dict)
            else {}
        )

        # Branch override: inject detected source_cell_px into process config
        if branch is not None and branch.source_cell_px is not None:
            process_cfg = dict(process_cfg)  # immutable copy
            process_cfg["source_cell_px"] = branch.source_cell_px

        # [FLOW:DISPATCH] Processor mode resolution — extracted to dispatch.py
        _mode_override = branch.process_mode if branch is not None else None
        process_mode = _resolve_process_mode(
            process_cfg, frames, _mode_override, debug_trace,
        )

        if process_mode == "literal":
            source_cell_px = process_cfg.get("source_cell_px", None)
            try:
                source_cell_px = int(source_cell_px) if source_cell_px is not None else None
            except (TypeError, ValueError):
                source_cell_px = None
            if source_cell_px != 1:
                raise ValueError(
                    "Literal processor mode requires "
                    "pipeline_config.process_settings.source_cell_px == 1."
                )
            print("   Processor mode: literal (1:1 pixel-to-cell)")
            from scripts.pipeline.processor_literal import process_literal
            processor = None
            quality_processor = None
        elif process_mode == "quality":
            quality_lut_step_raw = process_cfg.get("quality_lut_step", 17)
            try:
                quality_lut_step = int(quality_lut_step_raw)
            except (TypeError, ValueError):
                quality_lut_step = 17
            quality_error_diffusion_raw = process_cfg.get("error_diffusion", "intra_cell")
            quality_error_diffusion = str(quality_error_diffusion_raw).strip().lower() != "none"
            quality_align_policy = str(
                process_cfg.get("quality_align_policy", "pad_to_even")
            ).strip().lower()
            if quality_align_policy not in {"fail", "pad_to_even", "resize_even"}:
                quality_align_policy = "fail"
            print(
                f"   Processor mode: quality (subcell dithering, "
                f"lut_step={quality_lut_step}, error_diffusion={quality_error_diffusion}, "
                f"align_policy={quality_align_policy})"
            )
            from scripts.pipeline.processor_subcell import get_subcell_processor
            quality_processor = get_subcell_processor(
                lut_step=quality_lut_step,
                error_diffusion=quality_error_diffusion,
            )
            processor = None
        else:
            print("   Processor mode: standard (12px glyph matching)")

            # [PIPELINE:PROCESS] Fail-closed guard: block standard mode on
            # high-res frames that would collapse to too few glyphs.
            # Tightened to 2x CELL_SIZE (24px) per Phase 13.1 root fix.
            _max_cell_ratio = min(
                int(process_cfg.get("max_standard_cell_ratio", 2)),
                2,  # hard cap: never allow >24px in standard mode
            )
            _max_standard_dim = CELL_SIZE * _max_cell_ratio
            for _check_frame in frames:
                if _check_frame.width > _max_standard_dim or _check_frame.height > _max_standard_dim:
                    raise ValueError(
                        f"Standard processor blocked: frame {_check_frame.width}x"
                        f"{_check_frame.height} exceeds max {_max_standard_dim}px "
                        f"(CELL_SIZE={CELL_SIZE} * ratio={_max_cell_ratio}). "
                        f"Use process_mode='quality' or process_mode='auto'."
                    )

            # [PIPELINE:PROCESS] -- SpriteProcessor does proper two-color decomposition:
            # 1. For each 12x12 cell, finds the TWO most frequent palette colors
            # 2. Assigns them as foreground (most frequent) and background
            # 3. Matches the CP437 glyph that best separates those colors spatially
            # This fixes the fg==bg bug that made all glyphs invisible.
            processor = SpriteProcessor(cell_size=CELL_SIZE)
            quality_processor = None

        processed_frames = []

        for i, frame in enumerate(frames):
            if process_mode == "literal":
                grid_data = process_literal(frame)
            elif process_mode == "quality":
                if frame.width % 2 != 0 or frame.height % 2 != 0:
                    aligned_w = max(2, round(frame.width / 2) * 2)
                    aligned_h = max(2, round(frame.height / 2) * 2)
                    if quality_align_policy == "fail":
                        raise ValueError(
                            f"Frame {i} {frame.width}x{frame.height} is not 2px-aligned for "
                            "quality mode. Set even cell size/ROI or use "
                            "process_settings.quality_align_policy='pad_to_even'."
                        )
                    if quality_align_policy == "pad_to_even":
                        aligned = Image.new(frame.mode, (aligned_w, aligned_h))
                        aligned.paste(frame, (0, 0))
                    else:
                        aligned = frame.resize((aligned_w, aligned_h), Image.NEAREST)
                    debug_trace.log(
                        "frame_quality_align",
                        frame_index=int(i),
                        old_size=[int(frame.width), int(frame.height)],
                        new_size=[int(aligned_w), int(aligned_h)],
                        policy=quality_align_policy,
                    )
                    print(
                        f"   Warning: frame {i} {frame.width}x{frame.height} not "
                        f"2px-aligned for quality mode; aligned to "
                        f"{aligned_w}x{aligned_h}."
                    )
                    frame = aligned
                    frames[i] = aligned
                if quality_processor is None:
                    raise RuntimeError("quality processor unavailable")
                grid_data = quality_processor.process_frame(frame)
            else:
                # [PIPELINE:PROCESS] -- GAP-11-02 + Phase 13.1: Frame alignment guard.
                # Standard processor requires frames aligned to CELL_SIZE (12px).
                # Default: fail-closed (no silent magenta padding).
                # Opt-in 'pad' policy: pad with neutral fill (black/transparent).
                if frame.width % CELL_SIZE != 0 or frame.height % CELL_SIZE != 0:
                    _std_align_policy = str(
                        process_cfg.get("standard_align_policy", "fail")
                    ).strip().lower()

                    if _std_align_policy == "pad":
                        # Compatibility path: pad to next CELL_SIZE boundary
                        # with neutral fill (black or transparent — NOT magenta).
                        aligned_w = max(
                            CELL_SIZE,
                            ((int(frame.width) + CELL_SIZE - 1) // CELL_SIZE) * CELL_SIZE,
                        )
                        aligned_h = max(
                            CELL_SIZE,
                            ((int(frame.height) + CELL_SIZE - 1) // CELL_SIZE) * CELL_SIZE,
                        )
                        if "A" in frame.getbands():
                            fill = (0, 0, 0, 0)  # transparent
                        elif frame.mode in ("RGB", "P"):
                            fill = (0, 0, 0)  # black
                        else:
                            fill = 0
                        aligned = Image.new(frame.mode, (aligned_w, aligned_h), fill)
                        aligned.paste(frame, (0, 0))
                        debug_trace.log(
                            "frame_standard_align_compat",
                            frame_index=int(i),
                            old_size=[int(frame.width), int(frame.height)],
                            new_size=[int(aligned_w), int(aligned_h)],
                            fill_color=str(fill),
                        )
                        print(
                            f"   Warning: frame {i} {frame.width}x{frame.height} not "
                            f"{CELL_SIZE}px-aligned; padded to "
                            f"{aligned_w}x{aligned_h} (compat policy, neutral fill)."
                        )
                        frame = aligned
                        frames[i] = aligned
                    else:
                        # Default: fail-closed — no silent padding.
                        debug_trace.log(
                            "frame_standard_align_fail",
                            frame_index=int(i),
                            frame_size=[int(frame.width), int(frame.height)],
                            cell_size=int(CELL_SIZE),
                        )
                        raise ValueError(
                            f"Standard processor blocked: frame {i} "
                            f"{frame.width}x{frame.height} is not aligned to "
                            f"{CELL_SIZE}px grid.  Use process_mode='quality' "
                            f"(no alignment constraint), process_mode='auto' "
                            f"(auto-dispatch), or set "
                            f"standard_align_policy='pad' for explicit compatibility."
                        )

                # [PIPELINE:PROCESS] -- Create a temporary AssetDef for the processor.
                # SpriteProcessor.process_image() requires an AssetDef with a 'size'
                # attribute specifying dimensions in character cells (not pixels).
                frame_width_cells = frame.width // CELL_SIZE
                frame_height_cells = frame.height // CELL_SIZE

                # Create a minimal AssetDef for this frame
                frame_asset_def = AssetDef(
                    name=f"frame_{i}",
                    type="custom",
                    angles=1,
                    frames=[1],
                    source_type="file",
                    source_path="",
                    size=(frame_width_cells, frame_height_cells),
                    background=self.asset_def.background,
                )

                # [DATA-CONTRACT:XP] -- SpriteProcessor.process_image() returns a 2D grid
                # directly: grid_data[y][x] = (glyph_idx, fg_rgb, bg_rgb).
                # No post-processing needed - fg and bg are already distinct colors.
                grid_data = processor.process_image(frame, frame_asset_def)

            processed_frames.append(grid_data)

        print(f"   Processed {len(processed_frames)} frames")
        proc_stats = _summarize_processed_frames(processed_frames)
        debug_trace.log("processor_summary", **proc_stats)

        # Compute slice_frames for debug images (needed even with frames_override)
        if frames_override is not None:
            _slice_frames_for_debug = [len(frames)]
            _angles_for_debug = 1
        else:
            _slice_frames_for_debug = slice_frames
            _angles_for_debug = effective_angles

        stage3_img = _render_processed_preview(
            processed_frames,
            rows=int(_angles_for_debug),
            cols=int(sum(_slice_frames_for_debug)),
        )
        if stage3_img is not None:
            stage3_path = debug_trace.save_image("stage3_processed_preview", stage3_img)
            if stage3_path:
                debug_trace.log("artifact", label="stage3_processed_preview", path=stage3_path)

        # [PIPELINE:PROCESS] Optional human checkpoint after Stage 3.
        _require_stage3_review = bool(
            _cfg_get(_grid_settings, "require_human_stage3_review", False)
        )
        if _require_stage3_review:
            _review_path_3 = str(
                _cfg_get(
                    _grid_settings,
                    "human_stage3_review_path",
                    "docs/research/ascii/verification/manual-stage3-process-review.json",
                )
            )
            _review_3 = _load_human_stage_review(
                stage="stage3_process",
                review_path=_review_path_3,
                request_id=debug_trace.request_id,
                artifact_path=(
                    stage3_path if stage3_img is not None else None
                ),
            )
            print(
                f"   Stage 3 human review approved by {_review_3['reviewer']} "
                f"({_review_3['path']})"
            )
            debug_trace.log(
                "stage3_human_review",
                approved=True,
                reviewer=_review_3["reviewer"],
                path=_review_3["path"],
            )

        # [PIPELINE:SLICE] -- Optional intermediate sheet save.  Positioned
        # after Stage 3 but uses the original pixel ``frames`` (not glyph
        # grids) so the debug PNG shows the pre-processor pixel state.
        if template and frames_override is None and not _manifest_active:
            self.save_intermediate_sheet_if_needed(
                template, frames, self.asset_def.name
            )

        # ================================================================
        # [PIPELINE:ASSEMBLE] Stage 4/4: Assembly (.xp output)
        # ================================================================
        print("Stage 4/4: Assembly...")
        assembler = XPAssembler()

        # For extraction branches or manifest-driven path, override metadata
        # to match actual extracted frames (not the asset_def specification).
        if frames_override is not None:
            metadata = {
                "angles": 1,
                "anims": [len(frames)],
                "projs": 1,
            }
        elif _manifest_active:
            # anim_frames[0] = total per-angle frames including reflections.
            # Assembler anims = semantic frames per projection half.
            # Assembler resolves projs from angles (>0 -> 2).
            # Contract: anim_frames[0] // projs = semantic animation count.
            _per_angle_total = _manifest.anim_frames[0]
            if projs > 1 and _per_angle_total % projs != 0:
                raise ValueError(
                    f"anim_frames[0]={_per_angle_total} not divisible by "
                    f"projs={projs}. Manifest frame layout is inconsistent "
                    f"with reflection generation."
                )
            _semantic_anims = _per_angle_total // projs if projs > 1 else _per_angle_total
            metadata = {
                "angles": _manifest.angles,
                "anims": [_semantic_anims],
                # Omit projs -- assembler resolves from angles
            }
        else:
            # [DATA-CONTRACT:XP] -- The metadata dict is the contract between
            # the pipeline orchestrator and XPAssembler.assemble().  It must
            # contain "angles" (int) and "anims" (List[int]).
            anims_list = self.asset_def.frames if isinstance(self.asset_def.frames, list) else [self.asset_def.frames]
            metadata = {
                "angles": effective_angles,
                "anims": anims_list,
                "projs": projs,
            }
            bg = getattr(self.asset_def, "background", None)
            if bg is not None:
                metadata["background"] = {
                    "mode": bg.mode,
                    "key_color": bg.key_color,
                    "tolerance": bg.tolerance,
                    "alpha_threshold": bg.alpha_threshold,
                }
            if self.asset_def.projs is not None:
                metadata["projs"] = self.asset_def.projs
                # Reformatter compatibility: only divide when anim counts were
                # explicitly projection-multiplied upstream.
                if bool(getattr(self.asset_def, "frames_include_projs", False)):
                    if self.asset_def.projs > 1:
                        bad = [f for f in anims_list if f % self.asset_def.projs != 0]
                        if bad:
                            raise ValueError(
                                f"Pre-baked projs={self.asset_def.projs} requires "
                                f"frame counts divisible by {self.asset_def.projs}, "
                                f"but got indivisible counts {bad} in anims={anims_list}"
                            )
                        metadata["anims"] = [f // self.asset_def.projs for f in anims_list]

        debug_trace.log(
            "assembler_config",
            metadata=metadata,
            frame_count=len(processed_frames),
        )

        # [DATA-CONTRACT:XP] -- Final .xp output lands in staging/xp/.
        output_name = self.asset_def.name + output_suffix
        output_path = STAGING_DIR / "xp" / f"{output_name}.xp"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assembler.assemble(processed_frames, metadata, str(output_path))
        print(f"   Saved to {output_path}")
        debug_trace.log("assembly_done", output_path=str(output_path))

        if debug_trace.enabled:
            try:
                from scripts.xp_to_png import load_bdf, read_xp, render_layer

                _, layers = read_xp(str(output_path))
                if layers and len(layers) > 2:
                    glyphs, cell_w, cell_h, ascent, descent = load_bdf("assets/fonts/cp437_12x12.png.bdf")
                    width, height, rgb = render_layer(
                        layers[2], glyphs, cell_w, cell_h, ascent, descent, 1
                    )
                    roundtrip_img = Image.frombytes("RGB", (width, height), bytes(rgb))
                    rt_path = debug_trace.save_image("stage4_roundtrip_from_xp", roundtrip_img)
                    if rt_path:
                        debug_trace.log("artifact", label="stage4_roundtrip_from_xp", path=rt_path)

                    src_rgb = image.convert("RGB")
                    same_size = src_rgb.size == roundtrip_img.size
                    identical = False
                    mean_abs_diff = None
                    if same_size:
                        diff_img = ImageChops.difference(src_rgb, roundtrip_img)
                        identical = diff_img.getbbox() is None
                        channel_means = ImageStat.Stat(diff_img).mean
                        mean_abs_diff = float(sum(channel_means) / len(channel_means))

                    debug_trace.log(
                        "roundtrip_check",
                        source_size=[int(src_rgb.width), int(src_rgb.height)],
                        roundtrip_size=[int(roundtrip_img.width), int(roundtrip_img.height)],
                        same_size=same_size,
                        pixel_identical=identical,
                        mean_abs_diff=mean_abs_diff,
                    )
            except Exception as rt_err:
                debug_trace.log("roundtrip_check", status="error", error=str(rt_err))

        # [PIPELINE:ASSEMBLE] Optional human checkpoint after Stage 4.
        _require_stage4_review = bool(
            _cfg_get(_grid_settings, "require_human_stage4_review", False)
        )
        if _require_stage4_review:
            _review_path_4 = str(
                _cfg_get(
                    _grid_settings,
                    "human_stage4_review_path",
                    "docs/research/ascii/verification/manual-stage4-assemble-review.json",
                )
            )
            _review_4 = _load_human_stage_review(
                stage="stage4_assemble",
                review_path=_review_path_4,
                request_id=debug_trace.request_id,
                artifact_path=str(output_path),
            )
            print(
                f"   Stage 4 human review approved by {_review_4['reviewer']} "
                f"({_review_4['path']})"
            )
            debug_trace.log(
                "stage4_human_review",
                approved=True,
                reviewer=_review_4["reviewer"],
                path=_review_4["path"],
            )

        # --- Post-assembly: optional debug sheet generation ---
        # [FLOW:TEMPLATE] -- Debug sheet is opt-in via template.debug.
        if template and self.should_generate_debug_sheet(template) and frames_override is None and not _manifest_active:
            # WHY: Build frames_info from AssetDef rather than from the
            # processed_frames because the debug sheet labels need
            # logical metadata (angle, frame index, grid position), not
            # glyph data.
            # [DATA-CONTRACT:XP] -- frames_info is a list of dicts with
            # keys: angle, frame, anim_name, source_path, col, row.
            # This schema is consumed by render_debug_sheet() in
            # debug_sheet.py.
            frames_info = []
            col = 0
            for angle in range(effective_angles):
                # TODO(PIPELINE-FIX): Falls back to 1 frame when angle
                # index exceeds the frames list length.  This should
                # ideally raise an error rather than silently degrade.
                frames_per_angle = (
                    self.asset_def.frames[angle]
                    if angle < len(self.asset_def.frames)
                    else 1
                )
                for frame_idx in range(frames_per_angle):
                    frames_info.append(
                        {
                            "angle": angle,
                            "frame": frame_idx,
                            "anim_name": None,
                            "source_path": f"{self.asset_def.name}_{angle}_{frame_idx}.png",
                            "col": col % template.layout_cols(),
                            "row": angle,
                        }
                    )
                    col += 1

            debug_img = self.generate_debug_sheet_if_needed(
                template, frames_info, self.asset_def.name
            )
            if debug_img:
                print("   Debug sheet generated")

        return output_path

    def run(
        self,
        template=None,
        algorithm: str = "nearest",
    ) -> None:
        """
        Execute the 4-stage pipeline: Generate -> Slice -> Process -> Assemble.

        This is the primary entry point.  It runs all four stages sequentially
        inside a single try/except so that any failure (validation, I/O, glyph
        matching) is caught, logged to stderr, and re-raised for the caller
        (cli.py) to handle.

        Stage overview:
          1. [PIPELINE:GENERATE]  -- Load/render the source image via generator.py.
             Optional: grid validation + downscaling, normalization + padding.
          2. [PIPELINE:SLICE]     -- Decompose the sheet into per-frame tiles.
          3. [PIPELINE:PROCESS]   -- Convert each tile to a CP437 glyph grid.
          4. [PIPELINE:ASSEMBLE]  -- Pack all grids into a 4-layer .xp file.

        [FLOW:CLI] -- ``template`` and ``algorithm`` are resolved by cli.py from
        command-line flags (``--template``, ``--algorithm``) and passed here.

        Args:
            template: Optional Template object for grid validation, downscaling,
                      normalization, and debug sheet generation.  When None the
                      pipeline uses bare AssetDef values only.
            algorithm: Default downscaling algorithm (overridden by template
                       processing.downscale if set).  [FLOW:CLI]
        """
        from scripts.pipeline.grid_validator import GridError
        from scripts.pipeline.quality_gates import (
            clear_output_gate_cache,
            run_output_quality_gates,
        )
        clear_output_gate_cache()
        debug_trace = _PipelineDebugTrace(
            self.asset_def.name,
            request_id=self.request_id,
            entry_point=self.entry_point
        )
        start_frames = getattr(self.asset_def, "frames", [])
        if isinstance(start_frames, (list, tuple)):
            start_frames_list = [int(x) for x in start_frames]
        else:
            start_frames_list = [int(start_frames)]

        try:
            if debug_trace.enabled:
                print(f"   [DEBUG] Trace log: {debug_trace.jsonl_path}")
            debug_trace.log(
                "pipeline_start",
                asset_name=self.asset_def.name,
                image_path=self.image_path,
                source_type=getattr(self.asset_def, "source_type", None),
                template_name=getattr(template, "name", None),
                angles=int(getattr(self.asset_def, "angles", 1)),
                frames=start_frames_list,
                explicit_projs=getattr(self.asset_def, "projs", None),
                auto_downscale_enabled=self._auto_downscale_enabled(),
            )

            # ================================================================
            # [PIPELINE:GENERATE] Stage 1/4: Generation
            # ================================================================
            print("Stage 1/4: Generation...")

            # [FLOW:TEMPLATE] -- Pre-flight: validate template before doing any
            # expensive image I/O.  Fail fast on template authoring errors.
            processing_config = {}
            if template:
                print("   Validating template layout...")
                try:
                    self.validate_template_layout(template)
                except ValueError as layout_error:
                    print(
                        f"   Layout validation failed: {layout_error}", file=sys.stderr
                    )
                    raise

                # WHY: Processing settings must be resolved *before* image
                # generation because some flags (e.g. magenta_snap) affect how
                # the generator handles the source image.
                print("   Applying template processing settings...")
                processing_config = self.apply_template_processing(
                    self.asset_def, template
                )

                # WHY: Copying the input to staging/inputs/ preserves a record
                # of the exact source file used for this run, enabling
                # reproducible re-runs even after the original is modified.
                if self.image_path and Path(self.image_path).is_file():
                    inputs_path = STAGING_DIR / "inputs" / Path(self.image_path).name
                    inputs_path.parent.mkdir(parents=True, exist_ok=True)
                    # WHY: Skip copy if source already IS the destination to avoid
                    # shutil.SameFileError when input is already in staging/inputs/.
                    source_resolved = Path(self.image_path).resolve()
                    dest_resolved = inputs_path.resolve()
                    if source_resolved != dest_resolved:
                        shutil.copy(self.image_path, inputs_path)
                        print(f"   Copied input file to {inputs_path}")
                    else:
                        print(f"   Input file already in staging: {inputs_path}")

            generator = ImageGenerator()
            image = generator.generate(self.asset_def, self.image_path)
            print(f"   Loaded image: {image.size}")
            debug_trace.log(
                "input_loaded",
                image_size=[int(image.width), int(image.height)],
                image_mode=image.mode,
            )

            # WHY: Grid validation runs only when a template is present because
            # the template defines the expected dimensions.  Without a template,
            # the pipeline trusts that the source image is already correctly
            # sized (bare-AssetDef mode for advanced users).
            if template:
                print("   Validating dimensions...")
                try:
                    # WHY: Template's downscale algorithm takes priority over
                    # the CLI-level default so that per-asset tuning works
                    # without changing global flags.
                    downscale_algo = processing_config.get(
                        "downscale_algorithm", algorithm
                    )
                    image = self.validate_and_downscale(image, template, downscale_algo)
                    print(f"   Dimensions validated: {image.size}")
                    debug_trace.log(
                        "grid_validation",
                        status="ok",
                        downscale_algorithm=downscale_algo,
                        image_size=[int(image.width), int(image.height)],
                    )
                except GridError as e:
                    print(f"   Validation failed: {e}", file=sys.stderr)
                    debug_trace.log(
                        "grid_validation",
                        status="error",
                        error=str(e),
                    )
                    raise

            # WHY: Normalization is opt-in (not default) because most source
            # images already have consistent per-angle heights.  It is only
            # needed when raw Blender/AI renders have uneven strip heights.
            # Two sources can enable it: AssetDef.normalization (CLI override)
            # or template processing.normalization (template-level default).
            normalization_enabled = False
            if hasattr(self.asset_def, "normalization") and self.asset_def.normalization:
                normalization_enabled = True
            elif processing_config.get("normalization", False):
                normalization_enabled = True

            if normalization_enabled:
                 print("   Applying Normalization (DEBUG CHECK)...")
                 image = self.normalize_and_pad(image, template)
                 print(f"   ✓ Normalized image: {image.size}")
            debug_trace.log(
                "normalization",
                enabled=bool(normalization_enabled),
                image_size=[int(image.width), int(image.height)],
            )

            # ================================================================
            # [PIPELINE:SYNTHESIS] Angle Synthesis (explicit-only)
            # ================================================================
            # WHY: When --synthesize-angles N is set, the pipeline generates
            # missing angle rows from existing ones via horizontal mirroring
            # BEFORE reflection handling and slicing.  This is never implicit:
            # it requires the explicit flag to activate.
            #
            # HARD-14-07: Use local effective_angles instead of mutating
            # self.asset_def.angles.  All downstream stages use effective_angles
            # so the input AssetDef remains unchanged after pipeline.run().
            effective_angles = self.asset_def.angles
            synth_target = getattr(self.asset_def, "synthesize_angles", None)
            if synth_target is not None and synth_target > effective_angles:
                from scripts.pipeline.angle_synthesis import synthesize_angles as _synth

                anims_for_synth = (
                    self.asset_def.frames
                    if isinstance(self.asset_def.frames, list)
                    else [self.asset_def.frames]
                )
                source_angles = effective_angles
                print(
                    f"   Synthesizing angles: {source_angles} -> {synth_target}..."
                )
                image = _synth(image, source_angles, synth_target, anims_for_synth)
                # Update local effective_angles for downstream stages
                effective_angles = synth_target
                print(
                    f"   ✓ Angle synthesis complete (new size: {image.size})"
                )
                debug_trace.log(
                    "angle_synthesis",
                    source_angles=source_angles,
                    target_angles=synth_target,
                    image_size=[int(image.width), int(image.height)],
                )
            elif synth_target is not None:
                # Target <= current angles: no-op but log
                debug_trace.log(
                    "angle_synthesis",
                    source_angles=effective_angles,
                    target_angles=synth_target,
                    skipped=True,
                    reason="target <= source",
                )

            # ================================================================
            # [PIPELINE:REFLECTION] Reflection Handling
            # ================================================================
            # WHY: The engine (sprite.cpp) forces projs=2 for multi-angle sprites.
            # We must ensure the sheet has both projection and reflection halves.
            # Gated by reflection_policy: "none" | "generate" (default).
            # "detect" is kept for diagnostic use only; never the default.
            from scripts.pipeline.service.config_resolver import resolve_projs

            projs = resolve_projs(effective_angles, explicit_projs=self.asset_def.projs)
            source_projs = getattr(self.asset_def, "source_projs", None)
            requested_policy = getattr(self.asset_def, "reflection_policy", None)
            raw_policy = requested_policy or "generate"
            # Normalize legacy "explicit" (ImportRequest vocabulary) to "none"
            if raw_policy == "explicit":
                raw_policy = "none"
            # source_projs=2 implies pre-baked reflections — override policy to "none"
            if source_projs == 2:
                raw_policy = "none"
            # Fail-closed runtime contract: never run detection in production path.
            # If user asks for detect, coerce to generate unless source_projs=2.
            elif raw_policy == "detect":
                import sys as _sys_detect
                print(
                    "Warning: reflection_policy='detect' is deprecated for runtime use; "
                    "coercing to 'generate'. Use source_projs=2 + policy=none when "
                    "reflections are explicitly pre-baked in source.",
                    file=_sys_detect.stderr,
                )
                raw_policy = "generate"
            reflection_policy = raw_policy
            if reflection_policy not in ("none", "generate", "detect"):
                raise ValueError(
                    f"Unknown reflection_policy '{reflection_policy}'. "
                    f"Valid values: 'none', 'generate', 'detect'."
                )

            # GAP-11-11: RGBA + angles guard.  When bg_mode=="alpha" and
            # angles > 1 the reflection handler calls image.convert('RGB')
            # which strips the alpha channel.  Warn unless the user explicitly
            # opted out of reflections (--projs 1 --reflection-policy none).
            _bg_spec_early = getattr(self.asset_def, "background", None)
            _bg_mode_early = (
                _bg_spec_early.get("mode", "key_color")
                if isinstance(_bg_spec_early, dict)
                else getattr(_bg_spec_early, "mode", "key_color")
            ) if _bg_spec_early else "key_color"
            if _bg_mode_early == "alpha" and effective_angles > 1:
                _ep = getattr(self.asset_def, 'projs', None)
                _rp = reflection_policy
                if _ep != 1 or _rp not in ("none", None):
                    import sys as _sys
                    print(
                        "Warning: RGBA background with angles > 1 may corrupt "
                        "alpha channel during reflection handling. Consider "
                        "--projs 1 --reflection-policy none",
                        file=_sys.stderr,
                    )

            # Only source_projs=2 (explicit) triggers pre-baked path.
            # self.asset_def.projs is OUTPUT projs (derived from angles), NOT
            # source layout intent. Do not conflate the two.
            reflections_pre_baked = (source_projs == 2)
            anims_list = self.asset_def.frames if isinstance(self.asset_def.frames, list) else [self.asset_def.frames]

            # Mismatch diagnostic: only when policy="none" (user asserts pre-baked).
            # For "detect"/"generate", the pipeline expects projs=1 width and will
            # handle reflections itself, so width not being doubled is expected.
            if reflection_policy == "none" and projs >= 2:
                expected_cols = 2 * sum(anims_list)
                angles = max(effective_angles, 1)
                if image.width % expected_cols != 0:
                    raise ValueError(
                        f"reflection_policy='none' but sheet width {image.width} "
                        f"is not divisible by expected columns {expected_cols} "
                        f"(2 * sum(anim_frames)={sum(anims_list)}). "
                        f"The sheet does not appear to contain pre-baked reflections."
                    )
                # Also verify height yields valid cell dimensions
                if angles > 0 and image.height % angles != 0:
                    raise ValueError(
                        f"reflection_policy='none' but sheet height {image.height} "
                        f"is not divisible by angles={angles}. "
                        f"Expected {angles} equal-height rows for pre-baked sheet."
                    )
                cell_w = image.width // expected_cols
                cell_h = image.height // angles if angles > 0 else image.height
                if cell_w <= 0 or cell_h <= 0:
                    raise ValueError(
                        f"reflection_policy='none' yields invalid cell size "
                        f"{cell_w}x{cell_h} (sheet={image.width}x{image.height}, "
                        f"expected_cols={expected_cols}, angles={angles}). "
                        f"The sheet geometry is inconsistent with pre-baked reflections."
                    )

            # Log the resolved reflection mode for deterministic tracing
            debug_trace.log(
                "reflection_mode_resolved",
                source_projs=source_projs,
                requested_reflection_policy=requested_policy,
                reflection_policy=reflection_policy,
                projs=projs,
                pre_baked=reflections_pre_baked,
            )

            # ================================================================
            # [PIPELINE:BACKGROUND] Background alignment middleware
            # ================================================================
            # WHY: Must run BEFORE reflection generation. generate_reflections()
            # converts RGBA to RGB, destroying the alpha channel needed for
            # auto-detecting alpha backgrounds. Stage 3 transparency detection
            # is magenta-keyed, so we normalize all BG types to magenta here.
            bg_spec = getattr(self.asset_def, "background", None)
            if bg_spec is None:
                bg_spec = _auto_detect_bg_spec(image)

            if isinstance(bg_spec, dict):
                bg_mode = bg_spec.get("mode", "key_color")
                bg_key_color = tuple(bg_spec.get("key_color", (255, 0, 255)))
                bg_tolerance = int(bg_spec.get("tolerance", 8))
                bg_alpha_threshold = int(bg_spec.get("alpha_threshold", 128))
            else:
                bg_mode = getattr(bg_spec, "mode", "key_color")
                bg_key_color = tuple(getattr(bg_spec, "key_color", (255, 0, 255)))
                bg_tolerance = int(getattr(bg_spec, "tolerance", 8))
                bg_alpha_threshold = int(getattr(bg_spec, "alpha_threshold", 128))

            # Pre-call validation: --bg-mode alpha on non-alpha PNG
            if bg_mode == "alpha" and image.mode != "RGBA":
                raise ValueError(
                    f"--bg-mode alpha requires an RGBA PNG, but image is {image.mode}. "
                    f"Use --bg-mode key_color instead, or provide an RGBA PNG."
                )

            if debug_trace.enabled:
                image, bg_stats = align_background_to_magenta(
                    image,
                    mode=bg_mode,
                    key_color=bg_key_color,
                    tolerance=bg_tolerance,
                    alpha_threshold=bg_alpha_threshold,
                    return_stats=True,
                )
                debug_trace.log(
                    "background_pass",
                    **bg_stats,
                    key_color_input=[int(c) for c in bg_key_color],
                    tolerance_input=int(bg_tolerance),
                    alpha_threshold_input=int(bg_alpha_threshold),
                )
            else:
                image = align_background_to_magenta(
                    image,
                    mode=bg_mode,
                    key_color=bg_key_color,
                    tolerance=bg_tolerance,
                    alpha_threshold=bg_alpha_threshold,
                )
            if bg_mode != "none":
                print(
                    f"   \u2713 Background aligned to magenta "
                    f"(mode={bg_mode}, key={bg_key_color}, tol={bg_tolerance})"
                )

            # Post-alignment validation: image must be RGB
            if image.mode != "RGB":
                raise ValueError(
                    f"Image is still {image.mode} after background alignment. "
                    f"Background normalization failed -- this is a pipeline bug."
                )

            # Post-alignment validation: fully transparent image
            import numpy as np
            arr_check = np.array(image)
            magenta = np.array([255, 0, 255], dtype=np.uint8)
            all_magenta = np.all(arr_check == magenta, axis=2).all()
            if all_magenta:
                raise ValueError(
                    "All pixels are transparent after background alignment -- "
                    "nothing to convert. Check your alpha threshold (--alpha-threshold) "
                    "or verify the source image has visible content."
                )

            stage1_path = debug_trace.save_image("stage1_bg", image.convert("RGB"))
            if stage1_path:
                debug_trace.log("artifact", label="stage1_bg", path=stage1_path)

            # ================================================================
            # [PIPELINE:REFLECTION] Reflection generation
            # ================================================================
            if reflection_policy == "none":
                # User asserts reflections are pre-baked or not needed -- skip handler
                print(f"   ✓ Reflections skipped (policy=none, source_projs={source_projs})")
                debug_trace.log(
                    "reflection_pass",
                    projs=projs,
                    policy="none",
                    source_projs=source_projs,
                    pre_baked=reflections_pre_baked,
                    generated=False,
                    image_size=[int(image.width), int(image.height)],
                )
            elif reflections_pre_baked:
                print(f"   ✓ Reflections pre-baked (source_projs={source_projs}), skipping handler")
                debug_trace.log(
                    "reflection_pass",
                    projs=projs,
                    policy=reflection_policy,
                    pre_baked=True,
                    generated=False,
                    image_size=[int(image.width), int(image.height)],
                )
            elif projs == 2 and reflection_policy == "generate":
                # Skip detection, go straight to generation
                from scripts.pipeline.reflection_handler import generate_reflections
                print("   Generating reflections (policy=generate)...")
                image = generate_reflections(image, effective_angles, anims_list)
                print(f"   ✓ Reflections generated (new size: {image.size})")
                debug_trace.log(
                    "reflection_pass",
                    projs=projs,
                    policy="generate",
                    pre_baked=False,
                    generated=True,
                    image_size=[int(image.width), int(image.height)],
                )

            # [PIPELINE:PRE-SLICE] Optional grid check hook (Phase 12)
            pre_slice_report = self._run_pre_slice_check(image, debug_trace)

            # [PIPELINE:PRE-SLICE] Optional pixel-perfect normalization (Phase 12)
            image = self._run_pixel_perfect(image, debug_trace)

            # [PIPELINE:PRE-SLICE] Hook summary trace event
            debug_trace.log(
                "pre_slice_hooks_summary",
                grid_check_enabled=bool(getattr(self.asset_def, "pre_slice_check", False)),
                strict_mode=bool(getattr(self.asset_def, "pre_slice_check_strict", False)),
                pixel_perfect_mode=getattr(self.asset_def, "pixel_perfect_mode", "off"),
            )

            # ================================================================
            # [PIPELINE:NORMALIZE] Stage N0: Input normalization (default-on)
            # ================================================================
            pipeline_cfg = getattr(self.asset_def, "pipeline_config", None)
            _norm_cfg = _cfg_section(pipeline_cfg, "normalization_settings")
            _norm_mode = str(
                _cfg_get(_norm_cfg, "normalize_input_mode", "auto")
            ).strip().lower()

            if _norm_mode in ("auto", "required"):
                self._run_normalization_stage(
                    image, _norm_cfg, _norm_mode, debug_trace,
                )

            # ================================================================
            # [PIPELINE:DETECT] Stage 0: Multi-track detection (opt-in)
            # ================================================================
            # NOTE: Roadmap positions Stage 0 pre-BG, but detection needs the
            # fully prepared image (post-BG, post-reflection, post-hooks).
            branch_cfg = (
                pipeline_cfg.get("branch_settings", {})
                if isinstance(pipeline_cfg, dict)
                else {}
            )
            branching_enabled = bool(branch_cfg.get("enable_branch_loop", False))

            branch_tree = None
            _branch_sprite_cache = {}

            if branching_enabled:
                print("Stage 0: Multi-track detection...")
                from scripts.pipeline.branch_model import BranchNode, BranchTree, JobManifest
                from scripts.pipeline.branch_enums import BranchStage
                from scripts.pipeline.sprite_extract import (
                    extract_sprites,
                    extract_sprites_dual,
                )
                from scripts.pipeline.grid_detect import detect_grid
                import uuid

                grid_cfg = (
                    pipeline_cfg.get("grid_settings", {})
                    if isinstance(pipeline_cfg, dict)
                    else {}
                )
                extract_cfg = (
                    pipeline_cfg.get("extract_settings", {})
                    if isinstance(pipeline_cfg, dict)
                    else {}
                )

                try:
                    min_grid_size = float(grid_cfg.get("min_grid_size", 4.0))
                except (TypeError, ValueError):
                    min_grid_size = 4.0
                try:
                    refine_intensity = float(grid_cfg.get("refine_intensity", 0.25))
                except (TypeError, ValueError):
                    refine_intensity = 0.25
                detection_method = str(
                    grid_cfg.get("detection_method", "both")
                ).strip().lower()
                if detection_method == "fft":
                    grid_tracks = [("fft", "pp_fft")]
                elif detection_method == "gradient":
                    grid_tracks = [("gradient", "pp_gradient")]
                else:
                    grid_tracks = [("fft", "pp_fft"), ("gradient", "pp_gradient")]

                branch_tree = BranchTree()
                job_id = self.request_id or uuid.uuid4().hex[:12]

                # Root node (UPLOAD stage)
                root_id = f"root_{job_id}"
                _root_artifact = (
                    str(self.image_path) if self.image_path else "generated://source"
                )
                root = BranchNode(
                    id=root_id, parent_id=None, stage=BranchStage.UPLOAD,
                    track_id="input", settings_snapshot={},
                    artifact_kind="image", artifact_path=_root_artifact,
                    quality_score=1.0,
                )
                branch_tree.add_node(root)

                # --- Grid detection tracks ---
                for method_key, track_id in grid_tracks:
                    try:
                        result = detect_grid(
                            image,
                            method=method_key,
                            min_size=min_grid_size,
                            refine_intensity=refine_intensity,
                        )
                        _cell_px = (
                            int(result.source_cell_px)
                            if result.source_cell_px is not None
                            else 0
                        )
                        _gcols = int(getattr(result, "grid_cols", 0) or 0)
                        _grows = int(getattr(result, "grid_rows", 0) or 0)
                        _gconf = float(getattr(result, "confidence", 0.0) or 0.0)
                        _invalid_grid = (
                            _cell_px <= 0
                            or _gconf <= 0.0
                            or (_gcols <= 1 and _grows <= 1)
                        )
                        if _invalid_grid:
                            print(
                                f"   {track_id}: invalid grid detection "
                                f"(cell={result.source_cell_px}, cols={_gcols}, rows={_grows}, "
                                f"conf={_gconf:.2f}) -> skipped"
                            )
                            debug_trace.log(
                                "stage0_grid_skipped",
                                track_id=track_id,
                                method=method_key,
                                source_cell_px=result.source_cell_px,
                                grid_cols=_gcols,
                                grid_rows=_grows,
                                confidence=_gconf,
                                reason="invalid_grid_detection",
                            )
                            continue
                        node = BranchNode(
                            id=f"{track_id}_{job_id}", parent_id=root_id,
                            stage=BranchStage.EXTRACT, track_id=track_id,
                            settings_snapshot={
                                "method": method_key,
                                "cell_w": int(result.cell_w),
                                "cell_h": int(result.cell_h),
                                "grid_cols": int(result.grid_cols),
                                "grid_rows": int(result.grid_rows),
                            },
                            artifact_kind="grid_data",
                            artifact_path=f"detect/{track_id}",
                            quality_score=float(result.confidence),
                            source_cell_px=result.source_cell_px,
                            score_type="confidence",
                        )
                        branch_tree.add_node(node)
                        print(f"   {track_id}: cell={result.source_cell_px}px, conf={result.confidence:.2f}")
                    except Exception as gd_err:
                        print(f"   Warning: {track_id} detection failed: {gd_err}")
                        debug_trace.log("stage0_error", track=track_id, error=str(gd_err))

                # --- Extraction tracks (2 branches) ---
                # Post-alignment BG depends on bg_mode:
                # - bg_mode != "none": alignment converted to magenta -> extraction uses magenta
                # - bg_mode == "none": no alignment -> use resolved bg_key_color from BackgroundSpec
                if bg_mode != "none":
                    bg_colors_cfg = [(255, 0, 255)]
                else:
                    bg_colors_cfg = [bg_key_color]
                try:
                    _bg_tol = float(extract_cfg.get("bg_tolerance", 30))
                except (TypeError, ValueError):
                    _bg_tol = 30.0
                _alpha_raw = extract_cfg.get("alpha_threshold", 0.10)
                try:
                    _alpha_float = float(_alpha_raw)
                except (TypeError, ValueError):
                    _alpha_float = 0.10
                if _alpha_float <= 1.0:
                    _alpha_px = int(round(max(0.0, min(1.0, _alpha_float)) * 255.0))
                else:
                    _alpha_px = int(max(0.0, min(255.0, _alpha_float)))
                min_sprite_size = extract_cfg.get("min_sprite_size", (30, 30))
                min_size_val = (
                    min_sprite_size[0]
                    if isinstance(min_sprite_size, (list, tuple))
                    else int(min_sprite_size)
                )
                _max_cov = float(extract_cfg.get("max_coverage", 0.9))
                _extract_mode = str(extract_cfg.get("extraction_mode", "both")).strip().lower()
                if _extract_mode not in {"shape", "bbox", "both"}:
                    _extract_mode = "both"

                extract_image = image
                _roi = extract_cfg.get("selection_roi")
                _use_roi = bool(extract_cfg.get("use_selection_roi", False))
                _roi_applied = False
                if _use_roi and _roi is not None:
                    try:
                        if isinstance(_roi, dict):
                            _rx = int(_roi.get("x", 0))
                            _ry = int(_roi.get("y", 0))
                            _rw = int(_roi.get("w", 0))
                            _rh = int(_roi.get("h", 0))
                        else:
                            _rx, _ry, _rw, _rh = [int(v) for v in _roi]
                        _rx = max(0, _rx)
                        _ry = max(0, _ry)
                        _rw = max(0, _rw)
                        _rh = max(0, _rh)
                        _rw = min(_rw, max(0, image.width - _rx))
                        _rh = min(_rh, max(0, image.height - _ry))
                        if _rw > 0 and _rh > 0:
                            extract_image = image.crop((_rx, _ry, _rx + _rw, _ry + _rh))
                            _roi_applied = True
                            print(
                                f"   Extraction ROI enabled: {_rw}x{_rh}px at ({_rx},{_ry})"
                            )
                            debug_trace.log(
                                "extract_roi",
                                x=int(_rx),
                                y=int(_ry),
                                w=int(_rw),
                                h=int(_rh),
                            )
                    except Exception as roi_err:
                        print(f"   Warning: ignoring invalid selection_roi: {roi_err}")
                        debug_trace.log(
                            "stage0_error",
                            track="extractor_roi",
                            error=str(roi_err),
                        )
                _max_cov_effective = 1.0 if _roi_applied else _max_cov
                if _roi_applied and _max_cov_effective != _max_cov:
                    print(
                        "   Extraction ROI active: disabling max_coverage guard "
                        "for ROI extraction path"
                    )
                try:
                    _bg_colors = [tuple(c) for c in bg_colors_cfg] if bg_colors_cfg else None
                    if _extract_mode == "both":
                        shape_sprites, bbox_sprites = extract_sprites_dual(
                            extract_image,
                            alpha_threshold=_alpha_px,
                            bg_colors=_bg_colors,
                            color_tolerance=_bg_tol,
                            min_size=min_size_val,
                            max_coverage=_max_cov_effective,
                        )
                    else:
                        _mode = "shape" if _extract_mode == "shape" else "bbox"
                        _one = extract_sprites(
                            extract_image,
                            mode=_mode,
                            alpha_threshold=_alpha_px,
                            bg_colors=_bg_colors,
                            color_tolerance=_bg_tol,
                            min_size=min_size_val,
                            max_coverage=_max_cov_effective,
                        )
                        if _extract_mode == "shape":
                            shape_sprites, bbox_sprites = _one, []
                        else:
                            shape_sprites, bbox_sprites = [], _one
                except Exception as ex_err:
                    shape_sprites, bbox_sprites = [], []
                    print(f"   Warning: Sprite extraction failed: {ex_err}")
                    debug_trace.log("stage0_error", track="extractor", error=str(ex_err))

                # Save extracted sprites to staging + create branch nodes
                _sprite_staging = STAGING_DIR / "branches" / job_id
                _sprite_staging.mkdir(parents=True, exist_ok=True)

                for track_id, sprites in [("extractor_flood", shape_sprites),
                                           ("extractor_bbox", bbox_sprites)]:
                    sprite_paths = []
                    for idx, sprite in enumerate(sprites):
                        sp_path = _sprite_staging / f"{track_id}_{idx}.png"
                        sprite.image.save(sp_path)
                        sprite_paths.append(str(sp_path))
                    _branch_sprite_cache[track_id] = [s.image for s in sprites]

                    quality = min(len(sprites) / 10.0, 1.0) if sprites else 0.0
                    node = BranchNode(
                        id=f"{track_id}_{job_id}", parent_id=root_id,
                        stage=BranchStage.EXTRACT, track_id=track_id,
                        settings_snapshot={"mode": track_id.replace("extractor_", "")},
                        artifact_kind="sprite_list",
                        artifact_path=str(_sprite_staging / track_id),
                        artifact_refs=sprite_paths,
                        quality_score=quality,
                    )
                    branch_tree.add_node(node)
                    print(f"   {track_id}: {len(sprites)} sprites (score={quality:.2f})")

                # Cap enforcement after Stage 0
                per_stage_cap = int(branch_cfg.get("max_branches_per_stage", 2))
                global_cap = int(branch_cfg.get("max_global_branches", 8))
                tie_break = branch_cfg.get("tie_break_order", None)
                if isinstance(tie_break, (list, tuple)):
                    tie_break = list(tie_break)
                auto_prune = bool(branch_cfg.get("auto_prune", True))

                if auto_prune:
                    pruned = branch_tree.enforce_caps(
                        per_stage=per_stage_cap, global_max=global_cap,
                        tie_break_order=tie_break,
                    )
                    if pruned:
                        print(f"   Pruned {len(pruned)} branches after Stage 0")
                else:
                    pruned = []
                debug_trace.log(
                    "stage0_complete",
                    branches_total=len(branch_tree.nodes),
                    branches_active=len(branch_tree.get_active()),
                    branches_pruned=len(pruned),
                )

            # ================================================================
            # [PIPELINE:STAGES-2-4] Execute stages 2-4 (branched or linear)
            # ================================================================
            if branching_enabled and branch_tree:
                branch_outputs = {}
                active_branches = [
                    b for b in branch_tree.get_active()
                    if b.stage == BranchStage.EXTRACT
                ]

                # Check if auto mode is requested for branch forking
                _bpc = (
                    pipeline_cfg.get("process_settings", {})
                    if isinstance(pipeline_cfg, dict)
                    else {}
                )
                _branch_requested_mode = str(
                    _bpc.get("mode", "auto")
                ).strip().lower()
                _cfg_frames = (
                    self.asset_def.frames
                    if isinstance(self.asset_def.frames, list)
                    else [self.asset_def.frames]
                )
                _cfg_cols = sum(int(f) for f in _cfg_frames)
                if projs > 1 and not bool(getattr(self.asset_def, "frames_include_projs", False)):
                    _cfg_cols *= int(projs)
                _cfg_rows = max(int(effective_angles), 1)
                _cfg_expected_frames = max(1, _cfg_rows * max(int(_cfg_cols), 1))
                _grid_branch_limit = int(globals().get("_GRID_BRANCH_MAX_FRAMES", 4096))
                if _cfg_expected_frames > _grid_branch_limit:
                    print(
                        f"   Warning: configured semantic layout expects {_cfg_expected_frames} "
                        f"sprite frames (> {_grid_branch_limit}). Check angles/frames settings."
                    )
                    debug_trace.log(
                        "semantic_layout_warning",
                        expected_frames=int(_cfg_expected_frames),
                        limit=int(_grid_branch_limit),
                        angles=int(_cfg_rows),
                        cols=int(_cfg_cols),
                    )

                for branch in active_branches:
                    print(f"\n--- Branch: {branch.track_id} (score={branch.quality_score:.2f}) ---")
                    try:
                        if branch.artifact_kind == "sprite_list":
                            # Extraction tracks: sprites ARE the frames, skip Stage 2.
                            # Also skip auto forking — literal/standard more appropriate.
                            frames_list = _branch_sprite_cache.get(branch.track_id, [])
                            if not frames_list:
                                print(f"   Skipping {branch.track_id}: no sprites extracted")
                                branch_tree.prune(branch.id)
                                continue
                            # [FIX:FULL-SHEET] Pipeline guard via extracted helper.
                            # Disabled when max_coverage >= 1.0.
                            _should_prune, _cov_ratio = self._check_fullsheet_guard(
                                frames_list, image, _max_cov_effective
                            )
                            if _should_prune:
                                _warn_msg = (
                                    f"Pruned {branch.track_id}: single extracted sprite covers "
                                    f"{_cov_ratio:.0%} of source (full-sheet candidate)"
                                )
                                print(f"   {_warn_msg}")
                                debug_trace.log(
                                    "branch_pruned_fullsheet",
                                    track_id=branch.track_id,
                                    ratio=_cov_ratio,
                                )
                                branch_tree.prune(branch.id)
                                branch_tree.pruned_warnings.append(_warn_msg)
                                continue
                            # When auto is requested, resolve explicitly to standard
                            # for extraction tracks (avoids unresolved-auto warning).
                            if _branch_requested_mode == "auto":
                                branch.process_mode = "standard"
                            output = self._run_stages_2_to_4(
                                image, effective_angles=1, projs=1,
                                reflections_pre_baked=False,
                                template=template, processing_config=processing_config,
                                debug_trace=debug_trace, branch=branch,
                                frames_override=frames_list,
                                output_suffix=f"_{branch.track_id}",
                            )
                            branch_outputs[branch.id] = output

                            assemble_node = BranchNode(
                                id=f"asm_{branch.track_id}_{job_id}",
                                parent_id=branch.id,
                                stage=BranchStage.ASSEMBLE,
                                track_id=branch.track_id,
                                settings_snapshot=branch.settings_snapshot,
                                artifact_kind="xp_file",
                                artifact_path=str(output),
                                quality_score=branch.quality_score,
                                source_cell_px=branch.source_cell_px,
                            )
                            branch_tree.add_node(assemble_node)

                        else:
                            # Grid detection branches: use semantic layout
                            # (effective_angles + configured frames), not raw
                            # cell counts. Grid detection's value is cell size
                            # (source_cell_px) for literal mode, not slicer geometry.
                            snap = branch.settings_snapshot or {}
                            _det_cols = snap.get("grid_cols")
                            _det_rows = snap.get("grid_rows")
                            if _det_cols and _det_rows:
                                _sem_cols = sum(int(f) for f in _cfg_frames)
                                _sem_cols_adj = _sem_cols
                                if projs == 2 and not reflections_pre_baked:
                                    _sem_cols_adj *= 2
                                if int(_det_cols) < _sem_cols_adj or int(_det_rows) < effective_angles:
                                    print(f"   Warning: detected grid ({_det_cols}x{_det_rows}) smaller "
                                          f"than semantic layout ({_sem_cols_adj}x{effective_angles})")

                            if _branch_requested_mode == "auto":
                                # [PIPELINE:PROCESS] Auto mode: let _run_stages_2_to_4
                                # threshold dispatch decide standard vs quality based
                                # on max frame dimension vs 2x CELL_SIZE (24px).
                                # Do NOT force quality override — the threshold dispatch
                                # in _run_stages_2_to_4 handles this correctly.
                                fork_suffix = f"_{branch.track_id}_auto"
                                print(f"   Auto dispatch: {branch.track_id} -> threshold dispatch")
                                debug_trace.log(
                                    "auto_mode_branch_dispatch",
                                    requested_mode="auto",
                                    resolved_mode="threshold_dispatch",
                                    track_id=branch.track_id,
                                )
                                output = self._run_stages_2_to_4(
                                    image, effective_angles, projs,
                                    reflections_pre_baked, template,
                                    processing_config, debug_trace,
                                    branch=branch,
                                    output_suffix=fork_suffix,
                                )
                                # Measured scoring via output gates (non-raising)
                                try:
                                    _oqg = run_output_quality_gates(
                                        xp_path=str(output),
                                        source_image=image,
                                        artifact_dir=(
                                            debug_trace.artifacts_dir
                                            if debug_trace.enabled else None
                                        ),
                                        asset_name=f"{self.asset_def.name}_{branch.track_id}_auto",
                                        raise_on_fail=False,
                                    )
                                    _gate_scores = [
                                        g.score for g in _oqg.gates
                                        if g.details.get("status") != "skipped"
                                    ]
                                    score = (
                                        sum(_gate_scores) / len(_gate_scores)
                                        if _gate_scores else 0.0
                                    )
                                    _score_type = "measured"
                                except Exception as _oqg_exc:
                                    score = AUTO_MODE_QUALITY_SCORE
                                    _score_type = "heuristic"
                                    print(
                                        f"   Warning: output gate scoring failed "
                                        f"({_oqg_exc}), using heuristic",
                                        file=sys.stderr,
                                    )
                                fork_id = f"proc_{branch.track_id}_auto_{job_id}"
                                proc_node = BranchNode(
                                    id=fork_id,
                                    parent_id=branch.id,
                                    stage=BranchStage.PROCESS,
                                    track_id=f"{branch.track_id}_auto",
                                    settings_snapshot={"mode": "auto"},
                                    artifact_kind="xp_file",
                                    artifact_path=str(output),
                                    quality_score=score,
                                    source_cell_px=branch.source_cell_px,
                                    score_type=_score_type,
                                )
                                branch_tree.add_node(proc_node)
                                branch_outputs[fork_id] = output
                            else:
                                # Grid tracks: semantic layout via normal slicer.
                                output = self._run_stages_2_to_4(
                                    image, effective_angles, projs,
                                    reflections_pre_baked,
                                    template, processing_config, debug_trace,
                                    branch=branch,
                                    output_suffix=f"_{branch.track_id}",
                                )
                                branch_outputs[branch.id] = output

                                assemble_node = BranchNode(
                                    id=f"asm_{branch.track_id}_{job_id}",
                                    parent_id=branch.id,
                                    stage=BranchStage.ASSEMBLE,
                                    track_id=branch.track_id,
                                    settings_snapshot=branch.settings_snapshot,
                                    artifact_kind="xp_file",
                                    artifact_path=str(output),
                                    quality_score=branch.quality_score,
                                    source_cell_px=branch.source_cell_px,
                                )
                                branch_tree.add_node(assemble_node)

                    except Exception as exc:
                        print(f"   Branch {branch.track_id} failed: {exc}", file=sys.stderr)
                        branch_tree.prune(branch.id)
                        debug_trace.log(
                            "branch_failed",
                            track_id=branch.track_id,
                            error=str(exc),
                        )
                        continue

                # Post-process cap enforcement
                if auto_prune:
                    branch_tree.enforce_caps(
                        per_stage=per_stage_cap, global_max=global_cap,
                        tie_break_order=tie_break,
                    )

                # Promote best remaining output branch (ASSEMBLE or PROCESS)
                active_final = [
                    b for b in branch_tree.get_active()
                    if b.stage in (BranchStage.ASSEMBLE, BranchStage.PROCESS)
                ]
                promoted_output = None
                if active_final:
                    best = min(
                        active_final,
                        key=lambda n: branch_tree.deterministic_tiebreak_key(n, tie_break),
                    )
                    branch_tree.promote(best.id)
                    # ASSEMBLE nodes link to parent EXTRACT output;
                    # PROCESS nodes (from auto mode) link directly
                    promoted_output = branch_outputs.get(
                        best.id, branch_outputs.get(best.parent_id)
                    )

                # Persist manifest (routes.py expects staging/{job_id}/manifest.json)
                manifest = JobManifest(
                    job_id=job_id,
                    input_path=str(self.image_path or ""),
                    branch_tree=branch_tree,
                )
                manifest_dir = STAGING_DIR / job_id
                manifest_dir.mkdir(parents=True, exist_ok=True)
                manifest.save_json(manifest_dir / "manifest.json")
                self._manifest_job_id = job_id
                print(f"   Manifest saved: {manifest_dir / 'manifest.json'}")
                debug_trace.log(
                    "manifest_saved",
                    path=str(manifest_dir / "manifest.json"),
                    branches=len(branch_tree.nodes),
                )

                output_path = promoted_output or (
                    next(iter(branch_outputs.values())) if branch_outputs else None
                )
                if output_path is None:
                    raise RuntimeError("All branches failed - no XP output produced")

                # [FLOW:QUALITY_GATE] Output gates on promoted branch result
                _oqg_artifact_dir = debug_trace.artifacts_dir if debug_trace.enabled else None
                try:
                    _oqg_report = run_output_quality_gates(
                        xp_path=str(output_path),
                        source_image=image,
                        artifact_dir=_oqg_artifact_dir,
                        asset_name=self.asset_def.name,
                        raise_on_fail=True,
                    )
                    debug_trace.log(
                        "output_quality_gates",
                        all_thresholds_met=_oqg_report.all_passed,
                        gates=[
                            {"gate": g.gate, "verdict": g.verdict, "score": g.score}
                            for g in _oqg_report.gates
                        ],
                    )
                    # G8 warn-only: print if coherence is low
                    for _g in _oqg_report.gates:
                        if _g.gate == "G8_output_coherence" and not _g.passed:
                            if _g.details.get("status") != "skipped":
                                print(
                                    f"   Warning: G8 output coherence low "
                                    f"(correlation={_g.score:.3f})",
                                    file=sys.stderr,
                                )
                except ValueError as _oqg_err:
                    debug_trace.log(
                        "output_quality_gates_failed",
                        error=str(_oqg_err),
                    )
                    raise

                self._enforce_stage5_review(debug_trace, output_path)
                debug_trace.log("pipeline_end", status="ok", output_path=str(output_path))
                return output_path if isinstance(output_path, Path) else Path(output_path)

            else:
                # Determine requested mode from pipeline config
                _pc = (
                    pipeline_cfg.get("process_settings", {})
                    if isinstance(pipeline_cfg, dict)
                    else {}
                )
                _requested_mode = str(_pc.get("mode", "auto")).strip().lower()

                if _requested_mode == "auto":
                    # [PIPELINE:PROCESS] Auto mode: route directly to quality.
                    # Standard processor is deprecated.
                    from scripts.pipeline.branch_model import (
                        BranchNode, BranchTree, JobManifest,
                    )
                    from scripts.pipeline.branch_enums import BranchStage
                    import uuid

                    auto_job_id = str(uuid.uuid4())[:8]
                    auto_tree = BranchTree()
                    root = BranchNode(
                        id=f"root_{auto_job_id}",
                        parent_id=None,
                        stage=BranchStage.UPLOAD,
                        track_id="upload",
                        settings_snapshot={"mode": "auto"},
                        artifact_kind="image",
                        artifact_path=str(self.image_path or ""),
                    )
                    auto_tree.add_node(root)
                    auto_outputs = {}

                    # [PIPELINE:PROCESS] Auto mode: let _run_stages_2_to_4
                    # threshold dispatch decide standard vs quality based on
                    # max frame dimension vs 2x CELL_SIZE (24px).
                    # Do NOT force quality override — threshold dispatch handles it.
                    sub_mode = "auto"
                    print(f"\n--- Auto mode: threshold dispatch (>24px -> quality, <=24px -> standard) ---")
                    debug_trace.log(
                        "auto_mode_dispatch",
                        requested_mode="auto",
                        resolved_mode="threshold_dispatch",
                    )
                    sub_output = self._run_stages_2_to_4(
                        image, effective_angles, projs,
                        reflections_pre_baked, template,
                        processing_config, debug_trace,
                        output_suffix=f"_{sub_mode}",
                    )
                    # Measured scoring via output gates (non-raising)
                    try:
                        _oqg = run_output_quality_gates(
                            xp_path=str(sub_output),
                            source_image=image,
                            artifact_dir=(
                                debug_trace.artifacts_dir
                                if debug_trace.enabled else None
                            ),
                            asset_name=f"{self.asset_def.name}_{sub_mode}",
                            raise_on_fail=False,
                        )
                        _gate_scores = [
                            g.score for g in _oqg.gates
                            if g.details.get("status") != "skipped"
                        ]
                        score = (
                            sum(_gate_scores) / len(_gate_scores)
                            if _gate_scores else 0.0
                        )
                        _score_type = "measured"
                    except Exception as _oqg_exc:
                        score = AUTO_MODE_QUALITY_SCORE
                        _score_type = "heuristic"
                        print(
                            f"   Warning: output gate scoring failed "
                            f"({_oqg_exc}), using heuristic",
                            file=sys.stderr,
                        )
                    node = BranchNode(
                        id=f"proc_{sub_mode}_{auto_job_id}",
                        parent_id=root.id,
                        stage=BranchStage.PROCESS,
                        track_id=sub_mode,
                        settings_snapshot={"mode": sub_mode},
                        artifact_kind="xp_file",
                        artifact_path=str(sub_output),
                        quality_score=score,
                        score_type=_score_type,
                    )
                    auto_tree.add_node(node)
                    auto_outputs[node.id] = sub_output

                    # Single-path promotion (no fork comparison needed)
                    auto_tree.promote(node.id)
                    output_path = auto_outputs[node.id]

                    # Persist manifest
                    manifest = JobManifest(
                        job_id=auto_job_id,
                        input_path=str(self.image_path or ""),
                        branch_tree=auto_tree,
                    )
                    manifest_dir = STAGING_DIR / auto_job_id
                    manifest_dir.mkdir(parents=True, exist_ok=True)
                    manifest.save_json(manifest_dir / "manifest.json")
                    self._manifest_job_id = auto_job_id
                    print(f"   Auto mode manifest: {manifest_dir / 'manifest.json'}")
                    debug_trace.log(
                        "auto_mode_result",
                        promoted=node.track_id,
                        score=node.quality_score,
                        job_id=auto_job_id,
                    )

                    # [FLOW:QUALITY_GATE] Output gates on promoted auto-mode result
                    _oqg_artifact_dir = debug_trace.artifacts_dir if debug_trace.enabled else None
                    try:
                        _oqg_report = run_output_quality_gates(
                            xp_path=str(output_path),
                            source_image=image,
                            artifact_dir=_oqg_artifact_dir,
                            asset_name=self.asset_def.name,
                            raise_on_fail=True,
                        )
                        debug_trace.log(
                            "output_quality_gates",
                            all_thresholds_met=_oqg_report.all_passed,
                            gates=[
                                {"gate": g.gate, "verdict": g.verdict, "score": g.score}
                                for g in _oqg_report.gates
                            ],
                        )
                        for _g in _oqg_report.gates:
                            if _g.gate == "G8_output_coherence" and not _g.passed:
                                if _g.details.get("status") != "skipped":
                                    print(
                                        f"   Warning: G8 output coherence low "
                                        f"(correlation={_g.score:.3f})",
                                        file=sys.stderr,
                                    )
                    except ValueError as _oqg_err:
                        debug_trace.log(
                            "output_quality_gates_failed",
                            error=str(_oqg_err),
                        )
                        raise

                    self._enforce_stage5_review(debug_trace, output_path)
                    debug_trace.log(
                        "pipeline_end", status="ok",
                        output_path=str(output_path),
                    )
                    return (
                        output_path
                        if isinstance(output_path, Path)
                        else Path(output_path)
                    )
                else:
                    # Linear pipeline (unchanged default path)
                    output_path = self._run_stages_2_to_4(
                        image, effective_angles, projs,
                        reflections_pre_baked, template,
                        processing_config, debug_trace,
                    )

                    # [FLOW:QUALITY_GATE] Output gates on linear pipeline result
                    _oqg_artifact_dir = debug_trace.artifacts_dir if debug_trace.enabled else None
                    try:
                        _oqg_report = run_output_quality_gates(
                            xp_path=str(output_path),
                            source_image=image,
                            artifact_dir=_oqg_artifact_dir,
                            asset_name=self.asset_def.name,
                            raise_on_fail=True,
                        )
                        debug_trace.log(
                            "output_quality_gates",
                            all_thresholds_met=_oqg_report.all_passed,
                            gates=[
                                {"gate": g.gate, "verdict": g.verdict, "score": g.score}
                                for g in _oqg_report.gates
                            ],
                        )
                        for _g in _oqg_report.gates:
                            if _g.gate == "G8_output_coherence" and not _g.passed:
                                if _g.details.get("status") != "skipped":
                                    print(
                                        f"   Warning: G8 output coherence low "
                                        f"(correlation={_g.score:.3f})",
                                        file=sys.stderr,
                                    )
                    except ValueError as _oqg_err:
                        debug_trace.log(
                            "output_quality_gates_failed",
                            error=str(_oqg_err),
                        )
                        raise

                    # Return the output path for caller to use
                    self._enforce_stage5_review(debug_trace, output_path)
                    debug_trace.log(
                        "pipeline_end", status="ok",
                        output_path=str(output_path.resolve()),
                    )
                    return output_path.resolve()

        except Exception as e:
            # WHY: Log to stderr before re-raising so the user sees the error
            # even if the caller (cli.py) catches and reformats it.  Re-raising
            # preserves the original traceback for debugging.
            debug_trace.log(
                "pipeline_end",
                status="error",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            print(f"Pipeline failed: {e}", file=sys.stderr)
            raise

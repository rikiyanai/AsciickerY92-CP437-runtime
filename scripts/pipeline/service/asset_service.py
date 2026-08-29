"""
asset_service.py -- Single orchestration layer for all asset pipeline operations.

ALL callers should go through AssetService rather than constructing
AssetPipeline directly.  This ensures consistent behavior for projs
derivation, slicing, background handling, and job tracking.

Thread-safety: NOT thread-safe.  UUID4 job IDs avoid collision.
Job registry: In-memory only, same-process lifespan.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image

from .constants import CELL_SIZE, MAGENTA_RGB, BLENDER_MCP_PORT
from .config_resolver import resolve_projs, resolve_slicing
from .job import AssetJobConfig, AssetJobOutput
from .slicing import SlicingSpec, infer_sheet_spec

logger = logging.getLogger(__name__)


class AssetService:
    """Single orchestration layer.  ALL callers go through this.

    Methods:
        run()               -- Execute 4-stage pipeline.
        analyze()           -- Inspect image and suggest parameters.
        render_from_blender() -- Trigger Blender render and return sheet path.
        validate_output()   -- Validate a completed .xp file.
        place_in_editor()   -- Send PLACE_SPRITE command to editor stdin.
        status()            -- Look up a job by ID (in-memory only).
    """

    def __init__(self, editor_stdin=None):
        """Initialize the service.

        Args:
            editor_stdin: Optional writable file-like object connected to
                the asciiid editor's stdin.  Used by place_in_editor().
                None = editor integration disabled.
        """
        self._jobs: dict[str, AssetJobOutput] = {}
        self._editor_stdin = editor_stdin

    # ================================================================
    # resolve_grid: Pre-import grid analysis (shared by CLI + editor)
    # ================================================================

    def resolve_grid(
        self,
        image_path: str,
        angles: int = 1,
        frames: Optional[list] = None,
        explicit_projs: Optional[int] = None,
        slice_spec=None,
    ) -> dict:
        """Resolve grid geometry for an image without executing the pipeline.

        Uses the same grid resolution logic as the pipeline (infer_sheet_spec
        or resolve_slicing). Both CLI --analyze and editor import paths
        should call this to get consistent geometry predictions.

        Args:
            image_path: Path to the image to analyze.
            angles: Number of rotation angles.
            frames: Frame counts per animation (default: [1]).
            explicit_projs: Override for projs derivation.
            slice_spec: Optional explicit SlicingSpec.

        Returns:
            Dict with keys: cell_w, cell_h, cols, rows, method,
            divisible, remainder_x, remainder_y, error.
        """
        from .slicing import compute_grid_diagnostics

        frames = frames or [1]
        projs = resolve_projs(angles, explicit_projs)

        img = Image.open(image_path)
        diag = compute_grid_diagnostics(
            img_size=img.size,
            angles=angles,
            frames=tuple(frames),
            projs=projs,
            spec=slice_spec,
        )

        return {
            "cell_w": diag.cell_w_px,
            "cell_h": diag.cell_h_px,
            "cols": diag.total_cols,
            "rows": diag.rows,
            "method": diag.method,
            "divisible": diag.divisible,
            "remainder_x": diag.remainder_x,
            "remainder_y": diag.remainder_y,
            "confidence": diag.confidence,
            "error": diag.error,
            "image_size": diag.image_size,
        }

    # ================================================================
    # import_png_to_xp: Unified PNG import API
    # ================================================================

    def import_png_to_xp(self, request) -> AssetJobOutput:
        """Single backend method for PNG→XP imports.

        This is the ONLY method entry points should call for PNG imports.
        Validates request, resolves config, executes pipeline, returns
        output path + diagnostics metadata.

        Args:
            request: ImportRequest with all parameters explicit

        Returns:
            AssetJobOutput with xp_path, checksum, metadata, diagnostics

        Raises:
            ValueError: On invalid configuration
            FileNotFoundError: When source_path doesn't exist
        """
        from .adapters import build_job_from_import_request
        import uuid

        # Generate request ID for trace correlation
        request_id = str(uuid.uuid4())

        # Convert request → job config (single adapter path)
        job = build_job_from_import_request(request)

        # Execute pipeline via existing run() method
        # Note: run() will create its own job_id, but we pass request_id for trace
        output = self.run(job, request_id=request_id, entry_point='import_png_to_xp')

        # Attach diagnostics metadata for observability
        output.diagnostics['import_mode'] = request.import_mode
        output.diagnostics['reflection_policy'] = request.reflection_policy
        output.diagnostics['downscale_policy'] = request.downscale_policy
        output.diagnostics['entry_point'] = 'import_png_to_xp'
        output.diagnostics['request_id'] = request_id

        return output

    # ================================================================
    # run: Execute pipeline
    # ================================================================

    def run(self, job: AssetJobConfig, request_id: str = None, entry_point: str = "run") -> AssetJobOutput:
        """Execute the 4-stage pipeline.

        Converts AssetJobConfig → AssetDef, delegates to AssetPipeline.run(),
        and wraps the result in AssetJobOutput with checksum and resolved spec.

        If slice_spec is provided, it is validated and passed through to
        the slicer.  Otherwise, auto-inference from image dimensions is used.

        Args:
            job: Frozen job configuration.
            request_id: Optional request ID for trace correlation.
            entry_point: Entry point name for trace logging.

        Returns:
            AssetJobOutput with xp_path, checksum, and resolved slicing spec.

        Raises:
            ValueError: On invalid configuration.
            FileNotFoundError: When source_path doesn't exist.
        """
        from scripts.pipeline.schemas import AssetDef
        from scripts.pipeline.pipeline import AssetPipeline

        # Convert job config to AssetDef for backward compatibility
        asset_def = self._job_to_asset_def(job)

        # Create and run pipeline with trace metadata
        pipeline = AssetPipeline(asset_def, job.source_path, request_id=request_id, entry_point=entry_point)

        # Load template if specified
        template = None
        if job.template_name:
            template = self._load_template(job.template_name)

        output_path = pipeline.run(
            template=template,
            algorithm=job.downscale_algorithm,
        )

        # Compute checksum
        checksum = self._sha256(output_path)

        # Resolve the slicing spec that was actually used
        if job.source_path and Path(job.source_path).exists():
            try:
                img = Image.open(job.source_path)
                resolved_spec = resolve_slicing(job, img.size)
            except Exception:
                resolved_spec = SlicingSpec()
        else:
            resolved_spec = SlicingSpec()

        # Build metadata
        anims_list = list(job.frames)
        metadata = {
            "angles": job.angles,
            "projs": job.projs,
            "anims": anims_list,
            "render_resolution": job.render_resolution,
        }
        manifest_jid = getattr(pipeline, "_manifest_job_id", None)
        if manifest_jid:
            metadata["manifest_job_id"] = manifest_jid

        # Create output
        job_id = str(uuid.uuid4())
        output = AssetJobOutput(
            xp_path=Path(output_path),
            checksum_sha256=checksum,
            metadata=metadata,
            resolved_slice_spec=resolved_spec,
            created_at=datetime.now(timezone.utc).isoformat(),
            job_id=job_id,
        )

        self._jobs[job_id] = output
        return output

    # ================================================================
    # analyze: Inspect image and suggest parameters
    # ================================================================

    # Supported image formats for analyze() — checked before PIL.Image.open()
    # to provide actionable error messages instead of UnidentifiedImageError.
    SUPPORTED_IMAGE_EXTS = {
        '.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.gif', '.webp',
    }

    def analyze(
        self,
        image_path: str,
        hints: Optional[dict] = None,
    ) -> dict:
        """Inspect an image and suggest AssetJobConfig parameters.

        Eliminates guesswork -- run before converting to get suggested
        angles, cols, rows, cell size, frames, projs, and background mode.

        Args:
            image_path: Path to the image to analyze.
            hints: Optional dict with known parameters (e.g., {"angles": 8}).

        Returns:
            Dict with keys:
                dimensions: (width, height)
                suggested_angles: int
                suggested_cols: int
                suggested_rows: int
                suggested_cell_w: int
                suggested_cell_h: int
                suggested_frames: list[int]
                suggested_projs: int
                detected_background: str ("key_color", "alpha", "none")
                warnings: list[str]

        Raises:
            ValueError: When image_path has an unsupported file extension.
        """
        # Extension guard: reject non-image files before PIL sees them
        image_path = Path(image_path)
        ext = image_path.suffix.lower()
        if ext not in self.SUPPORTED_IMAGE_EXTS:
            supported = ", ".join(
                s.lstrip(".").upper() for s in sorted(self.SUPPORTED_IMAGE_EXTS)
            )
            raise ValueError(
                f"Cannot analyze '{ext}' files directly. "
                f"Supported image formats: {supported}. "
                f"For .blend files, use the Blender render pipeline first."
            )

        hints = hints or {}
        img = Image.open(image_path)
        width, height = img.size
        warnings = []

        # Detect background mode
        detected_bg = self._detect_background(img)

        # Try to infer angles from height
        suggested_angles = hints.get("angles", self._guess_angles(height))
        calc_angles = suggested_angles if suggested_angles > 0 else 1

        # Compute row height
        row_h = height // calc_angles if calc_angles > 0 else height
        if height % calc_angles != 0:
            warnings.append(
                f"Height {height} not evenly divisible by {calc_angles} angles"
            )

        # Infer column count from width using candidate cell widths.
        # Try multiple hypotheses and pick the one that is internally
        # consistent with grid_diagnostics (divisible, plausible).
        #
        # Candidate cell widths (in priority order):
        #   1. row_h (square frames -- common for isometric/RPG sheets)
        #   2. Largest divisor of width that is also a divisor of CELL_SIZE
        #      (for engine-aligned sprites)
        #   3. CELL_SIZE itself (fallback for engine-native sheets)
        #   4. width (single column -- always valid, lowest confidence)
        from .slicing import compute_grid_diagnostics

        suggested_projs = resolve_projs(suggested_angles)

        def _try_cols(candidate_cols: int) -> bool:
            """Return True if candidate is geometrically consistent."""
            if candidate_cols <= 0 or width % candidate_cols != 0:
                return False
            cw = width // candidate_cols
            if cw <= 0:
                return False
            # Must produce plausible frames (at least 1 per projection)
            if suggested_projs == 2 and candidate_cols % 2 != 0:
                return False
            fc = candidate_cols // suggested_projs if suggested_projs == 2 else candidate_cols
            if fc <= 0:
                return False
            gd = compute_grid_diagnostics(
                img_size=(width, height),
                angles=suggested_angles,
                frames=(fc,),
                projs=suggested_projs,
            )
            return gd.divisible

        # Build candidate total_cols (deduplicated, in priority order)
        candidate_cols_list = []
        # Square frames hypothesis
        if row_h > 0 and width % row_h == 0:
            candidate_cols_list.append(width // row_h)
        # CELL_SIZE hypothesis
        if width % CELL_SIZE == 0:
            candidate_cols_list.append(width // CELL_SIZE)
        # Single column (always valid)
        candidate_cols_list.append(1)

        # Deduplicate while preserving order
        seen = set()
        unique_candidates = []
        for c in candidate_cols_list:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        total_cols = 1  # safe default
        for c in unique_candidates:
            if _try_cols(c):
                total_cols = c
                break
        else:
            # None of the candidates produced a divisible grid
            warnings.append(
                f"Could not auto-detect column count from width {width}; "
                f"defaulting to 1 column. Use --cols to override."
            )

        if suggested_projs == 2 and total_cols % 2 == 0:
            frame_cols = total_cols // 2
        else:
            frame_cols = total_cols
            if suggested_projs == 2:
                warnings.append(
                    f"projs=2 expected but {total_cols} columns is odd -- "
                    f"reflections may not be present"
                )

        # Cell dimensions
        cell_w = width // total_cols if total_cols > 0 else width
        cell_h = row_h

        # Suggest frames as single animation
        suggested_frames = [frame_cols] if frame_cols > 0 else [1]

        # Grid diagnostics via the unified resolver
        grid_diag = compute_grid_diagnostics(
            img_size=(width, height),
            angles=suggested_angles,
            frames=tuple(suggested_frames),
            projs=suggested_projs,
        )
        if not grid_diag.divisible:
            warnings.append(
                f"Inferred grid is non-divisible: "
                f"{width}x{height} into {grid_diag.total_cols}x{grid_diag.rows} "
                f"(remainder {grid_diag.remainder_x}x{grid_diag.remainder_y}). "
                f"Use --cell-w/--cell-h or --cols/--rows to override."
            )

        # Suggest explicit flags when inference is low-confidence
        suggested_flags = []
        if cell_w != CELL_SIZE:
            suggested_flags.append(f"--cell-w {cell_w}")
        if cell_h != CELL_SIZE:
            suggested_flags.append(f"--cell-h {cell_h}")
        if total_cols != 1:
            suggested_flags.append(f"--cols {total_cols}")
        if calc_angles != 1:
            suggested_flags.append(f"--rows {calc_angles}")

        # Layout suggestions (suggest-only heuristic)
        layout_suggestions = self._suggest_layout(
            width, height, suggested_angles, suggested_frames, suggested_projs
        )

        return {
            "dimensions": (width, height),
            "suggested_angles": suggested_angles,
            "suggested_cols": total_cols,
            "suggested_rows": calc_angles,
            "suggested_cell_w": cell_w,
            "suggested_cell_h": cell_h,
            "suggested_frames": suggested_frames,
            "suggested_projs": suggested_projs,
            "detected_background": detected_bg,
            "warnings": warnings,
            "grid_diagnostics": {
                "method": grid_diag.method,
                "divisible": grid_diag.divisible,
                "remainder_x": grid_diag.remainder_x,
                "remainder_y": grid_diag.remainder_y,
                "confidence": grid_diag.confidence,
                "error": grid_diag.error,
            },
            "suggested_flags": suggested_flags,
            "layout_suggestions": layout_suggestions,
        }

    # ================================================================
    # render_from_blender
    # ================================================================

    def render_from_blender(self, job: AssetJobConfig) -> Path:
        """Trigger Blender render and return the sheet path.

        Args:
            job: Job config with blender_object set.

        Returns:
            Path to the rendered sprite sheet PNG.

        Raises:
            ConnectionError: If Blender MCP is not reachable.
        """
        from scripts.blender_client import BlenderMCPClient

        client = BlenderMCPClient(port=BLENDER_MCP_PORT)
        if not client.connect():
            raise ConnectionError(
                f"Cannot connect to Blender MCP on port {BLENDER_MCP_PORT}"
            )

        try:
            response = client.render_asset(
                object_name=job.blender_object,
                angles=job.angles,
                frames_per_angle=sum(job.frames),
                resolution=job.render_resolution,
            )
            return Path(response.output_path)
        finally:
            client.disconnect()

    # ================================================================
    # validate_output
    # ================================================================

    def validate_output(self, job: AssetJobConfig) -> list[str]:
        """Validate a completed job's .xp output.

        Args:
            job: Job config (used to locate the expected output path).

        Returns:
            List of validation error strings.  Empty = valid.
        """
        from scripts.pipeline.validator import validate_xp
        from scripts.pipeline.staging import STAGING_DIR

        xp_path = STAGING_DIR / "xp" / f"{job.name}.xp"
        if not xp_path.exists():
            return [f"Output file not found: {xp_path}"]

        result = validate_xp(xp_path)
        return result.get("errors", [])

    # ================================================================
    # place_in_editor
    # ================================================================

    def place_in_editor(
        self,
        xp_path: str,
        x: float,
        y: float,
        z: float,
        yaw: float = 0,
        anim: int = 0,
        frame: int = 0,
    ) -> str:
        """Send PLACE_SPRITE command to editor stdin.

        Non-blocking.  Returns error string if editor_stdin is None.

        Args:
            xp_path: Path to the .xp sprite file.
            x, y, z: World coordinates for placement.
            yaw: Rotation angle.
            anim: Animation index.
            frame: Frame index.

        Returns:
            Empty string on success, error message on failure.
        """
        if self._editor_stdin is None:
            return "Editor not connected (editor_stdin is None)"

        cmd = f"PLACE_SPRITE {xp_path} {x} {y} {z} {yaw} {anim} {frame}\n"
        try:
            self._editor_stdin.write(cmd)
            self._editor_stdin.flush()
            return ""
        except Exception as e:
            return f"Failed to write to editor stdin: {e}"

    # ================================================================
    # status
    # ================================================================

    def status(self, job_id: str) -> Optional[AssetJobOutput]:
        """Look up a job by ID.

        In-memory only -- returns None after restart.

        Args:
            job_id: UUID string from a previous run().

        Returns:
            AssetJobOutput if found, None otherwise.
        """
        return self._jobs.get(job_id)

    # ================================================================
    # Private helpers
    # ================================================================

    @staticmethod
    def _job_to_asset_def(job: AssetJobConfig):
        """Convert AssetJobConfig → AssetDef for pipeline backward compat.

        CRITICAL: Only pass explicit_projs to AssetDef.projs.  When projs
        was derived by resolve_config() (i.e. explicit_projs is None), the
        pipeline must derive projs itself so it generates reflections
        correctly.  Passing derived projs as if explicit causes the pipeline
        to skip reflection generation but still halve anims, producing
        [1//2]=[0] → ZeroDivisionError in the assembler.
        """
        from scripts.pipeline.schemas import AssetDef

        asset = AssetDef(
            name=job.name,
            type=job.asset_type,
            angles=job.angles,
            frames=list(job.frames),
            source_type=job.source_type,
            source_path=job.source_path,
            blender_object=job.blender_object,
            transparency=job.transparency,
            normalization=job.normalization,
            target_cells_high=job.target_cells_high,
            render_resolution=job.render_resolution,
            projs=job.explicit_projs,
            slice_spec=job.slice_spec,
            background=job.background,
            source_projs=getattr(job, "source_projs", None),
            reflection_policy=getattr(job, "reflection_policy", None),
            synthesize_angles=getattr(job, "synthesize_angles", None),
            pre_slice_check=getattr(job, "pre_slice_check", False),
            pre_slice_check_strict=getattr(job, "pre_slice_check_strict", False),
            pixel_perfect_mode=getattr(job, "pixel_perfect_mode", "off"),
            keyframe_ranges=getattr(job, "keyframe_ranges", None),
        )
        # Carry shared pipeline config through legacy AssetDef path.
        # AssetDef has no dedicated field yet, so attach dynamically.
        setattr(asset, "pipeline_config", getattr(job, "pipeline_config", None))
        setattr(
            asset,
            "frames_include_projs",
            bool(getattr(job, "frames_include_projs", False)),
        )
        return asset

    @staticmethod
    def _load_template(template_name: str):
        """Load a template by name.  Returns None if not found."""
        import json
        from pathlib import Path

        templates_dir = Path("scripts/pipeline/templates")
        for f in templates_dir.glob("*.json"):
            try:
                with open(f, "r") as file:
                    data = json.load(file)
                    if data.get("name") == template_name:
                        from scripts.pipeline.templates.loader import TemplateLoader
                        return TemplateLoader.from_file(f)
            except Exception:
                continue
        return None

    @staticmethod
    def _sha256(path) -> str:
        """Compute SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _detect_background(img: Image.Image) -> str:
        """Detect background mode from image content."""
        if img.mode == "RGBA":
            # Check alpha channel
            alpha = img.getchannel("A")
            # If >10% of pixels have alpha < 128, likely alpha-based
            import numpy as np
            alpha_arr = np.array(alpha)
            transparent_ratio = (alpha_arr < 128).sum() / alpha_arr.size
            if transparent_ratio > 0.10:
                return "alpha"

        # Check for magenta edges
        rgb = img.convert("RGB")
        import numpy as np
        arr = np.array(rgb)
        # Sample edges (top row, bottom row, left col, right col)
        edges = []
        if arr.shape[0] > 0:
            edges.append(arr[0, :])     # top
            edges.append(arr[-1, :])    # bottom
        if arr.shape[1] > 0:
            edges.append(arr[:, 0])     # left
            edges.append(arr[:, -1])    # right

        if edges:
            edge_pixels = np.concatenate(edges, axis=0)
            is_magenta = (
                (edge_pixels[:, 0] > 240)
                & (edge_pixels[:, 1] < 15)
                & (edge_pixels[:, 2] > 240)
            )
            magenta_ratio = is_magenta.sum() / len(is_magenta)
            if magenta_ratio > 0.5:
                return "key_color"

        return "none"

    @staticmethod
    def _suggest_layout(
        width: int,
        height: int,
        suggested_angles: int,
        suggested_frames: list,
        suggested_projs: int,
    ) -> list:
        """Suggest plausible sheet layout orderings with confidence and rationale.

        Heuristic only -- does NOT change behavior.  Returns a list of
        suggestion dicts sorted by confidence (high first).

        Each dict has keys:
            order       -- "angle_major" or "animation_major"
            label       -- User-facing name ("Row-per-angle", "Row-per-animation")
            confidence  -- "high", "medium", or "low"
            rationale   -- Human-readable explanation

        Args:
            width:            Sheet width in pixels.
            height:           Sheet height in pixels.
            suggested_angles: Number of rotation angles (0 for single-angle).
            suggested_frames: Frame counts per animation (e.g. [4] or [1,8]).
            suggested_projs:  Number of projections (1 or 2).

        Returns:
            List of suggestion dicts.  Empty when neither layout divides evenly.
        """
        total_frames = sum(suggested_frames)
        calc_angles = max(suggested_angles, 1)
        suggestions = []

        # Angle-major grid: cols = sum(frames) * projs, rows = angles
        am_cols = total_frames * suggested_projs
        am_rows = calc_angles

        # Animation-major grid: cols = angles, rows = sum(frames)
        anim_cols = calc_angles
        anim_rows = total_frames

        am_divides = (
            am_cols > 0
            and am_rows > 0
            and width % am_cols == 0
            and height % am_rows == 0
        )
        anim_divides = (
            anim_cols > 0
            and anim_rows > 0
            and width % anim_cols == 0
            and height % anim_rows == 0
        )

        # Skip animation_major when there is only one frame total
        skip_anim = total_frames <= 1

        if am_divides and (not anim_divides or skip_anim):
            # Only angle_major works
            suggestions.append({
                "order": "angle_major",
                "label": "Row-per-angle",
                "confidence": "high",
                "rationale": (
                    f"Grid {am_cols}x{am_rows} divides {width}x{height} evenly "
                    f"(cell {width // am_cols}x{height // am_rows}px)"
                ),
            })
        elif anim_divides and (not am_divides) and not skip_anim:
            # Only animation_major works
            suggestions.append({
                "order": "animation_major",
                "label": "Row-per-animation",
                "confidence": "high",
                "rationale": (
                    f"Grid {anim_cols}x{anim_rows} divides {width}x{height} evenly "
                    f"(cell {width // anim_cols}x{height // anim_rows}px)"
                ),
            })
        elif am_divides and anim_divides and not skip_anim:
            # Both divide -- use aspect ratio tiebreaker
            aspect = height / width if width > 0 else 1.0
            if aspect > 1.5:
                # Tall sheet -> animation_major primary
                suggestions.append({
                    "order": "animation_major",
                    "label": "Row-per-animation",
                    "confidence": "medium",
                    "rationale": (
                        f"Tall aspect ratio ({aspect:.1f}) suggests rows-per-animation. "
                        f"Grid {anim_cols}x{anim_rows} divides evenly."
                    ),
                })
                suggestions.append({
                    "order": "angle_major",
                    "label": "Row-per-angle",
                    "confidence": "low",
                    "rationale": (
                        f"Grid {am_cols}x{am_rows} also divides evenly "
                        f"but aspect ratio favors animation_major."
                    ),
                })
            else:
                # Wide or square sheet -> angle_major primary
                suggestions.append({
                    "order": "angle_major",
                    "label": "Row-per-angle",
                    "confidence": "medium",
                    "rationale": (
                        f"Wide/square aspect ratio ({aspect:.1f}) suggests rows-per-angle. "
                        f"Grid {am_cols}x{am_rows} divides evenly."
                    ),
                })
                suggestions.append({
                    "order": "animation_major",
                    "label": "Row-per-animation",
                    "confidence": "low",
                    "rationale": (
                        f"Grid {anim_cols}x{anim_rows} also divides evenly "
                        f"but aspect ratio favors angle_major."
                    ),
                })

        # Mirrored workflow hint when projs=2
        if suggested_projs == 2 and suggestions:
            suggestions.append({
                "order": "angle_major",
                "label": "Mirrored workflow",
                "confidence": "medium",
                "rationale": (
                    f"projs=2 detected: reflections double column count. "
                    f"Verify source sheet includes mirrored frames."
                ),
            })

        return suggestions

    @staticmethod
    def _guess_angles(height: int) -> int:
        """Guess angle count from image height."""
        for angles in (8, 4, 1):
            if height % angles == 0:
                row_h = height // angles
                if row_h % CELL_SIZE == 0:
                    return angles
        return 1

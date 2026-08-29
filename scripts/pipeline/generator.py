"""
Asset Generator -- Image acquisition and Blender render orchestration.

ARCHITECTURE
~~~~~~~~~~~~
This is the **[PIPELINE:GENERATE]** stage -- the very first step in the sprite
asset pipeline.  Its job is to produce a single PIL Image (either a raw photo/
drawing or a stitched sprite sheet from Blender) that downstream stages can
slice, quantize, and assemble into the engine's XP format.

Three source paths are supported, selected by ``AssetDef.source_type``:

  1. **"file"** -- Load a local PNG/JPEG from disk.  Transparency (alpha) is
     composited onto magenta (255,0,255) so the rest of the pipeline can use
     color-key transparency uniformly.

  2. **"blender" (MCP)** -- Talk to a *running* Blender instance via the MCP
     (Model Context Protocol) client.  This is the fast path for interactive
     development: no process startup overhead, and the artist can keep Blender
     open.

  3. **"blender" (subprocess)** -- Launch a headless ``blender -b`` process to
     execute ``scripts/blender/render_sprite.py``.  This is the robust/CI path:
     it works without a running Blender instance and is the automatic fallback
     when MCP fails.

The Blender paths produce one PNG per (angle, frame) pair into a temp directory,
then stitch them into a sprite sheet: rows = viewing angles, columns = animation
frames.

KEY EXPORTS
~~~~~~~~~~~
- ``ImageGenerator``  -- Stateless class whose ``.generate()`` method is the
  single entry point called by ``pipeline.py``.

PIPELINE CONTEXT
~~~~~~~~~~~~~~~~
- [PIPELINE:GENERATE]  Entry point: ``ImageGenerator.generate()``
- [DATA-CONTRACT:ASSET-DEF]  ``AssetDef`` drives source selection, angle count,
  frame counts, render resolution, and object name.
- [DEPENDENCY:PIL]  Pillow is used for all image I/O and compositing.
- [DEPENDENCY:BLENDER]  Optional; only imported/invoked when source_type="blender".
- [DEPENDENCY:MCP]  Optional; ``blender_client`` is lazy-imported only in MCP path.
- [FLOW:CLI]  Not a CLI itself, but the primary backend invoked by
  ``pipeline.py``'s CLI entry point.
"""

from PIL import Image  # [DEPENDENCY:PIL]
from .schemas import AssetDef, AnimationRange  # [DATA-CONTRACT:ASSET-DEF]
from .palette import is_transparent, COLOR_TRANSPARENT
from .color_correction import analyze_background, replace_background_color, snap_to_magenta
from scripts.pipeline.service.constants import DEFAULT_RENDER_RESOLUTION
from scripts.blender.blender_preflight import run_preflight
import json
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)


class ImageGenerator:
    """
    Stateless image acquisition facade for the sprite pipeline.

    All generation logic is routed through ``generate()``, which inspects the
    ``AssetDef`` to decide whether to load a file from disk or render from
    Blender.  The class carries no mutable state -- each call is independent.

    Blender integration uses a two-tier strategy:
      1. Try MCP (fast, requires running Blender) via ``_generate_from_blender_mcp``.
      2. Fall back to subprocess (slow, self-contained) via ``_generate_from_blender_subprocess``.

    [PIPELINE:GENERATE]
    """

    def generate(
        self, asset_def: AssetDef, image_path: str | None = None
    ) -> Image.Image:
        """
        Load or generate an image for asset generation.

        This is the [PIPELINE:GENERATE] entry point called by ``AssetPipeline``
        in pipeline.py.  It resolves the source path, dispatches to the correct
        backend (file / Blender MCP / Blender subprocess), and returns a PIL
        Image ready for the slicing and quantization stages.

        Args:
            asset_def: Asset definition containing name, source_type, and render
                metadata.  [DATA-CONTRACT:ASSET-DEF]
            image_path: Optional explicit image path.  If provided, overrides
                ``asset_def.source_path``.

        Returns:
            PIL.Image.Image: Loaded or generated image (single image or stitched
            sprite sheet from Blender).  Always RGB or RGBA mode.

        Raises:
            FileNotFoundError: If the resolved image file does not exist on disk.
            RuntimeError: If Blender rendering fails in both MCP and subprocess modes.
        """
        # [DATA-CONTRACT:ASSET-DEF] Explicit path takes precedence over AssetDef's source_path.
        path = image_path if image_path else asset_def.source_path

        # WHY: getattr with default -- source_type was added after the initial AssetDef
        # design, so older pickled/serialized defs may lack the field.  Defaulting to
        # "file" preserves backward compatibility with pre-Blender pipeline assets.
        source_type = getattr(asset_def, "source_type", "file")
        logger.info(f"Source type: {source_type}")

        # TODO(PIPELINE-FIX): The fallback path "scripts/{name}.png" is relative to CWD,
        # which breaks when the pipeline is invoked from a different working directory
        # (e.g. CI runners or Blender subprocess).  Should use an absolute path anchored
        # to the project root.
        if not path:
            path = f"scripts/{asset_def.name}.png"

        # --- Mesh import dispatch ---------------------------------------------
        # WHY: "mesh" source type auto-converts 3D model files (OBJ, STL, etc.)
        # to .blend via headless Blender, presents a user checkpoint, then
        # delegates to the existing Blender subprocess render path.
        # [FLOW:MESH-IMPORT] [DEPENDENCY:BLENDER]
        if source_type == "mesh":
            import sys
            from .mesh_importer import MeshImporter
            importer = MeshImporter(path)
            blend_path, obj_name = importer.run(interactive=sys.stdin.isatty())
            asset_def.source_path = blend_path
            asset_def.blender_object = obj_name
            asset_def.source_type = "blender"
            return self._generate_from_blender_subprocess(blend_path, asset_def)

        # --- Engine dispatch --------------------------------------------------
        if source_type == "engine":
            logger.info("Attempting render via C++ Engine MCP...")
            return self._generate_from_engine(asset_def)

        # --- Blender dispatch ------------------------------------------------
        # WHY: We check both the file extension AND source_type because the user
        # may provide a .blend path directly (via image_path) without setting
        # source_type="blender" in the AssetDef.  Either signal triggers the
        # Blender rendering path.
        # [DEPENDENCY:BLENDER]
        if path.endswith(".blend") or source_type == "blender":
            # WHY: MCP-first, subprocess-fallback strategy.  MCP is 5-10x faster
            # because it reuses an already-running Blender process and avoids the
            # ~3s startup cost.  But MCP requires the artist to have Blender open
            # with the MCP addon active -- which won't be the case in CI/CD or on
            # a fresh machine.  The subprocess fallback guarantees the pipeline
            # always works, just slower.
            # [DEPENDENCY:MCP] [DEPENDENCY:BLENDER]
            try:
                logger.info("Attempting Blender render via MCP mode...")
                return self._generate_from_blender_mcp(asset_def)
            except Exception as e:
                logger.warning(f"MCP mode failed: {e}")
                logger.info("Falling back to subprocess mode...")
                return self._generate_from_blender_subprocess(path, asset_def)

        # --- Local file loading -----------------------------------------------
        # [DEPENDENCY:PIL]
        try:
            img = Image.open(path)
            logger.info(f"Loaded image: {img.size}, Mode: {img.mode}")

            # WHY: The Asciicker engine uses magenta (255,0,255) as a color-key
            # for transparency.  For RGBA sources, we preserve the alpha channel
            # and let the downstream align_background_to_magenta() handle
            # conversion with the user's --alpha-threshold setting.
            # For non-alpha sources, we convert to RGB as before.
            # [DEPENDENCY:PIL]
            # CHANGED (Phase 4, BG-01): Preserve RGBA for downstream
            # align_background_to_magenta() which respects --alpha-threshold.
            # Do NOT composite onto magenta here -- that would flatten alpha
            # at threshold=0, ignoring the user's threshold setting.
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                if img.mode == 'P':
                    img = img.convert('RGBA')
                elif img.mode == 'LA':
                    img = img.convert('RGBA')
                logger.info(f"Preserved RGBA mode for downstream alignment pass")
            else:
                img = img.convert("RGB")

            # Background heuristics only apply to RGB images.
            # RGBA images defer to align_background_to_magenta(mode="alpha")
            # in the pipeline's background alignment pass.
            if img.mode == "RGB":
                # WHY: Source art (especially AI-generated) often has a background
                # that is *close* to magenta but not exact (e.g. (252, 3, 250)).
                # analyze_background detects the dominant background color and
                # measures its L1 distance from canonical magenta.  If it's far
                # from magenta (distance > 30), we attempt to replace it.
                analysis = analyze_background(img)
                logger.info(f"Background Analysis: Dominant={analysis['dominant_color']}, Dist={analysis['distance_from_magenta']}")

                if analysis["distance_from_magenta"] > 30:
                    # FIX(35-03): Bug - overzealous background replacement
                    # Root cause: Solid-color test images (e.g., solid orange for TDD)
                    # were being treated as "background to replace" because they're
                    # uniform and non-magenta. This broke PNG→XP conversion tests.
                    # Fix: Skip replacement if dominant color covers >=90% AND is not
                    # near-magenta (distance > 50). This preserves transparency key
                    # handling while preventing corruption of solid test images.
                    dominant_coverage = analysis.get('dominant_percentage', 0)
                    if dominant_coverage >= 90 and analysis["distance_from_magenta"] > 50:
                        logger.info(f"Dominant non-magenta color covers {dominant_coverage:.1f}% - skipping replacement (likely solid sprite/test)")
                    else:
                        # WHY: The top-left pixel is used as a heuristic for "what is
                        # the background color?" because sprite source images typically
                        # have empty background in the corners.  If top-left matches the
                        # statistically dominant color, we're confident it's background
                        # and replace it with canonical magenta.
                        # TODO(PIPELINE-FIX): This top-left heuristic fails for images
                        # where the sprite extends to the corner (e.g. full-bleed art).
                        # A more robust approach would sample multiple corner pixels.
                        tl_pixel = img.getpixel((0, 0))
                        logger.info(f"Non-magenta background. Top-Left: {tl_pixel}")

                        dom = analysis['dominant_color']
                        diff_tl = abs(tl_pixel[0]-dom[0]) + abs(tl_pixel[1]-dom[1]) + abs(tl_pixel[2]-dom[2])

                        if diff_tl < 30:
                             logger.info(f"Top-Left matches dominant background. Replacing with Magenta...")
                             img = replace_background_color(img, tl_pixel, tolerance=15)

            # WHY: Legacy transparency audit for AI-generated sources.  AI image
            # generators (source_type="ai") may or may not produce magenta
            # backgrounds.  This sampling pass counts magenta pixels to log
            # whether transparency keying will work.  The result is informational
            # only -- it does not alter the image.
            # TODO(PIPELINE-FIX): This block logs the count but never acts on it.
            # If magenta_count is zero for an AI source, the downstream pipeline
            # will treat the entire image as opaque, which is likely a bug.
            # Consider raising a warning or triggering auto_magenta_correction.
            if source_type == "ai" or getattr(asset_def, "transparency", False):
                pixels = []
                # WHY: Sampling every 1/10th pixel in each dimension (~1% of
                # total pixels) is a performance compromise -- checking every
                # pixel in a 2048x2048 AI-generated image is too slow for an
                # interactive pipeline, and 1% sampling is sufficient to detect
                # whether a magenta background exists.
                for x in range(0, img.width, max(1, img.width // 10)):
                    for y in range(0, img.height, max(1, img.height // 10)):
                        pixels.append(img.getpixel((x, y)))

                magenta_count = sum(1 for p in pixels if is_transparent(p, tolerance=5))
                total = len(pixels)

                logger.info(f"Magenta transparency check: {magenta_count}/{total} pixels")

            return img
        except FileNotFoundError:
            raise FileNotFoundError(f"Cannot load image: {path}")

    def _generate_from_engine(self, asset_def: AssetDef) -> Image.Image:
        """
        Generate an image by capturing the current state of the C++ engine.
        """
        from .engine_client import EngineClient
        
        with EngineClient() as client:
            img = client.render()
            logger.info(f"Captured {img.size} image from engine")
            return img

    # ------------------------------------------------------------------
    # Blender MCP rendering
    # ------------------------------------------------------------------

    def _generate_from_blender_mcp(self, asset_def: AssetDef) -> Image.Image:
        """
        Generate a sprite sheet by rendering via Blender MCP (Model Context Protocol).

        [DEPENDENCY:MCP] [DEPENDENCY:BLENDER] [DEPENDENCY:PIL]

        WHY MCP exists alongside subprocess: MCP talks to a *running* Blender
        instance over a local socket (port 9876).  This avoids the 3-5 second
        Blender startup cost and lets artists iterate in real time.  The trade-off
        is that it requires the Blender MCP addon to be installed and active.

        Args:
            asset_def: Asset definition.  Must have ``blender_object`` set.
                [DATA-CONTRACT:ASSET-DEF]

        Returns:
            PIL.Image.Image: Rendered sprite sheet loaded from the temp output path.

        Raises:
            ValueError: If ``asset_def.blender_object`` is not set.
            BlenderMCPError: If the MCP render call fails (caught by caller for fallback).
        """
        # [DEPENDENCY:MCP] Lazy import -- blender_client is an optional dependency
        # that only exists when the MCP addon ecosystem is installed.
        from blender_client import BlenderMCPClient, BlenderMCPError

        if not asset_def.blender_object:
            raise ValueError("AssetDef must specify 'blender_object' for MCP mode")

        logger.info(f"Connecting to Blender MCP on port 9876...")

        from .staging import STAGING_DIR, ensure_staging_structure

        ensure_staging_structure()
        temp_output = os.path.join(
            STAGING_DIR / "renders", f"{asset_def.name}.png"
        )

        try:
            with BlenderMCPClient() as client:
                # WHY: The MCP protocol uses a flat dict rather than the AssetDef
                # dataclass because the Blender-side addon has its own schema.
                # We translate only the fields the addon needs.
                # TODO(PIPELINE-FIX): "type" is hardcoded to "character".  Items
                # and custom assets will render with wrong camera framing.  Should
                # pass asset_def.type through.
                total_frames = sum(getattr(asset_def, "frames", [4]))
                width_px = asset_def.size[0] * getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)
                height_px = asset_def.size[1] * getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)

                mcp_asset_def = {
                    "asset_name": asset_def.name,
                    "object_name": asset_def.blender_object,
                    "angles": getattr(asset_def, "angles", 8),
                    "frames": total_frames,
                    "resolution": (width_px, height_px),
                    "transparent_bg": True,
                    "convert_to_magenta": True,
                    "frame_order": "angle-major",
                    "type": "character",
                }

                logger.info(f"Rendering {asset_def.blender_object} via MCP...")
                result = client.render_asset(mcp_asset_def, temp_output)

                logger.info(f"MCP render complete: {result}")

                # [DEPENDENCY:PIL]
                return Image.open(result)

        except BlenderMCPError as e:
            logger.error(f"MCP render failed: {e}")
            raise  # Re-raise to trigger subprocess fallback in generate()
        except Exception as e:
            logger.error(f"MCP error: {e}")
            raise

    # ------------------------------------------------------------------
    # Blender subprocess rendering
    # ------------------------------------------------------------------

    def _generate_from_blender_subprocess(
        self, blend_path: str, asset_def: AssetDef
    ) -> Image.Image:
        """
        Generate a sprite sheet by launching Blender as a headless subprocess.

        [DEPENDENCY:BLENDER] [DEPENDENCY:PIL]

        This is the robust fallback path.  It launches ``blender -b`` with the
        ``render_sprite.py`` script, which renders individual frames into a temp
        directory.  Those frames are then stitched into a sprite sheet.

        The method wraps ``_generate_from_blender_subprocess_inner`` and adds an
        optional agent-hook notification on failure (for CI observability).

        Args:
            blend_path: Path to the ``.blend`` file to render.
            asset_def: Asset definition with blender_object, angles, frames, etc.
                [DATA-CONTRACT:ASSET-DEF]

        Returns:
            PIL.Image.Image: Stitched sprite sheet (RGBA).

        Raises:
            RuntimeError: If the Blender process exits non-zero or produces no frames.
            ValueError: If ``asset_def.blender_object`` is not set.
        """
        import subprocess
        import tempfile
        import shutil
        import sys

        try:
            return self._generate_from_blender_subprocess_inner(blend_path, asset_def)
        except Exception as e:
            # WHY: The agent_hook.py script (if present) notifies external
            # monitoring systems (e.g. a Slack webhook or log aggregator) when
            # the pipeline fails.  It is fire-and-forget (check=False) so hook
            # failures never mask the real error.
            try:
                hook_path = "scripts/agent_hook.py"
                if os.path.exists(hook_path):
                    subprocess.run([sys.executable, hook_path, f"Pipeline Error: {e}"], check=False)
            except Exception:
                # WHY: Bare except (now narrowed to Exception) ensures a broken
                # agent_hook never masks the real pipeline error.  The hook is
                # fire-and-forget observability -- its failure is irrelevant.
                pass
            raise e

    def _generate_from_blender_subprocess_inner(
        self, blend_path: str, asset_def: AssetDef
    ) -> Image.Image:
        """
        Core subprocess rendering and sprite-sheet stitching logic.

        [DEPENDENCY:BLENDER] [DEPENDENCY:PIL]

        Workflow:
          1. Locate the Blender executable (PATH or macOS standard locations).
          2. Invoke ``blender -b <blend_path> -P render_sprite.py -- <args>``.
          3. Collect rendered PNGs named ``angle_N_frame_M.png`` from temp dir.
          4. Stitch into a sprite sheet: rows = angles, columns = frames.

        Args:
            blend_path: Path to the ``.blend`` file.
            asset_def: Asset definition.  [DATA-CONTRACT:ASSET-DEF]

        Returns:
            PIL.Image.Image: Stitched sprite sheet in RGBA mode.

        Raises:
            ValueError: If ``blender_object`` is unset.
            RuntimeError: If Blender is not found, exits non-zero, or produces no frames.
        """
        import subprocess
        import tempfile
        import shutil
        # WHY: Path is imported here (function scope) rather than at module level
        # because subprocess/tempfile/shutil are only needed for the Blender path.
        # NOTE: _get_blender_env() also uses Path but relies on THIS import being
        # in scope via the call chain.  See TODO(PIPELINE-FIX) in that method.
        from pathlib import Path
        if not asset_def.blender_object:
            raise ValueError(
                "AssetDef must specify 'blender_object' when using Blender source"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            # --- Run preflight validation -------------------------------------
            # WHY: Preflight checks fail-fast with clear error messages before
            # spawning Blender subprocess. Validates Blender binary exists,
            # .blend file exists, output dir is writable, and object exists in
            # scene (via Blender probe).
            # [DEPENDENCY:BLENDER]
            try:
                preflight_result = run_preflight(
                    blend_path=blend_path,
                    output_dir=temp_dir,
                    object_name=asset_def.blender_object
                )
                blender_cmd = preflight_result["blender_path"]
            except (FileNotFoundError, ValueError, PermissionError) as e:
                # Log structured error to staging/debug/
                debug_path = Path("scripts/pipeline/staging/debug/preflight_error.log")
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(f"Preflight failed: {e}\n")
                # Re-raise with clear message
                raise RuntimeError(f"Preflight validation failed: {e}") from e

            logger.info(f"Using Blender executable: {blender_cmd}")

            # --- Build command line -------------------------------------------
            # WHY: "-b" runs Blender in background (headless) mode.  "-P" runs
            # the Python script inside Blender's embedded interpreter.  The "--"
            # separator ensures arguments after it are passed to the script, not
            # to Blender itself.
            # [DATA-CONTRACT:ASSET-DEF] All render parameters are derived from
            # the AssetDef: object name, resolution, grid dimensions, angles.
            # --- Keyframe range validation ----------------------------------------
            # [DATA-CONTRACT:ASSET-DEF] When keyframe_ranges is provided, validate
            # that the sum of range counts matches sum(frames).
            keyframe_ranges = getattr(asset_def, "keyframe_ranges", None)
            if keyframe_ranges:
                range_total = sum(r.count for r in keyframe_ranges)
                frame_total = sum(getattr(asset_def, "frames", [1]))
                if range_total != frame_total:
                    raise ValueError(
                        f"Keyframe range count mismatch: sum of range counts ({range_total}) "
                        f"!= sum of frames ({frame_total}). "
                        f"Each AnimationRange.count must match its corresponding frames entry."
                    )
                logger.info(
                    f"Keyframe ranges: {len(keyframe_ranges)} ranges, "
                    f"total frames={range_total}"
                )
            else:
                logger.info("No keyframe_ranges — using sequential render fallback")

            cmd = [
                blender_cmd,
                "-b",
                "--factory-startup",
                blend_path,
                "-P",
                "scripts/blender/render_sprite.py",
                "--",
                "--output",
                temp_dir,
                "--object",
                asset_def.blender_object,
                "--resolution",
                str(getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)),
                "--grid-w",
                str(asset_def.size[0]),
                "--grid-h",
                str(asset_def.size[1]),
                "--angles",
                str(getattr(asset_def, "angles", 8)),
            ]

            # Pass keyframe ranges as JSON argument when provided
            if keyframe_ranges:
                ranges_json = json.dumps([
                    {
                        "start": r.keyframe_start,
                        "end": r.keyframe_end,
                        "count": r.count,
                        "name": r.name,
                    }
                    for r in keyframe_ranges
                ])
                cmd.extend(["--keyframe-ranges", ranges_json])

            logger.info(f"Running Blender subprocess: {blender_cmd}")

            env = self._get_blender_env()

            # WHY: Timeout scales with render complexity (resolution × angles) to
            # prevent zombie Blender processes while allowing heavy renders to finish.
            # Base: 120s. Scales up for high resolution or many angles.
            render_res = getattr(asset_def, "render_resolution", DEFAULT_RENDER_RESOLUTION)
            num_angles = getattr(asset_def, "angles", 8)
            timeout_secs = max(300, 60 * num_angles * max(1, render_res // 24))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_secs, env=env)

            if result.returncode != 0:
                logger.error(f"Blender output: {result.stdout}")
                logger.error(f"Blender stderr: {result.stderr}")
                raise RuntimeError(f"Blender failed with code {result.returncode}")

            # --- Stitch rendered frames into sprite sheet ---------------------
            # [DEPENDENCY:PIL]
            # WHY: Blender's render_sprite.py outputs one PNG per (angle, frame)
            # pair.  The game engine expects a single sprite sheet image where
            # rows = viewing angles (0..N-1 from front, clockwise) and columns =
            # animation frames.  We stitch here so downstream stages (slicer,
            # processor) receive a uniform single-image input regardless of
            # whether the source was a file or a Blender render.
            # WHY: getattr with defaults -- "frames" and "angles" were added to
            # AssetDef after the initial design.  Defaults ([4] frames, 8 angles)
            # match the most common character sprite configuration.
            # TODO(PIPELINE-FIX): Default of [4] means sum() = 4 columns.  If a
            # multi-animation asset (e.g. idle+walk = [4,8]) is rendered but the
            # AssetDef lacks the "frames" field, only 4 columns will be allocated
            # and walk frames will be silently clipped.
            total_frames = sum(getattr(asset_def, "frames", [4]))
            angles = getattr(asset_def, "angles", 8)

            files = sorted(os.listdir(temp_dir))
            if not files:
                raise RuntimeError("Blender produced no output frames.")

            sample = Image.open(os.path.join(temp_dir, files[0]))

            frame_w, frame_h = sample.size

            # WHY (FIXED BUG): The original code had sheet_w = frame_w * angles,
            # which put angles along the X axis.  The engine expects angles as
            # rows (Y) and frames as columns (X).  The fix: cols = total_frames,
            # rows = angles.
            cols = total_frames
            rows = angles
            sheet_w = cols * frame_w
            sheet_h = rows * frame_h

            sheet = Image.new("RGBA", (sheet_w, sheet_h))

            # WHY: Filenames follow the convention "angle_N_frame_M.png" from
            # render_sprite.py.  We parse them to determine grid placement.
            # Grouping by angle first ensures each row is a complete viewing
            # direction, sorted by frame index for correct animation order.
            by_angle = {}
            for fname in files:
                if not fname.endswith(".png"):
                    continue
                parts = fname.replace(".png", "").split("_")
                if len(parts) >= 4 and parts[0] == "angle" and parts[2] == "frame":
                    angle = int(parts[1])
                    if angle not in by_angle:
                        by_angle[angle] = []
                    by_angle[angle].append(fname)

            for angle in by_angle:
                by_angle[angle].sort()

                for frame_idx, fname in enumerate(by_angle[angle]):
                    img = Image.open(os.path.join(temp_dir, fname))

                    # Each angle is a row (y), each frame is a column (x).
                    x = frame_idx * frame_w
                    y = angle * frame_h

                    # WHY: Bounds check prevents paste failures when Blender
                    # produces more frames than expected (e.g. render script
                    # bug or mismatched angle/frame config).
                    if x + frame_w <= sheet_w and y + frame_h <= sheet_h:
                        sheet.paste(img, (x, y))

            # [PIPELINE:GENERATE] Exit point -- returns stitched sprite sheet.
            return sheet

    def _get_blender_env(self) -> dict:
        """
        Build an augmented environment dict for Blender subprocess execution.

        [DEPENDENCY:BLENDER]

        WHY: Blender runs its own embedded Python interpreter, which may lack
        packages installed in the project's MCP virtual environment.  By injecting
        the MCP venv's site-packages into PYTHONPATH, Blender scripts can import
        pipeline utilities (e.g. for custom render post-processing) without
        requiring a separate ``pip install`` inside Blender's Python.

        Returns:
            dict: A copy of ``os.environ`` with PYTHONPATH augmented if the MCP
            venv exists.

        Note:
            TODO(PIPELINE-FIX): ``Path`` is used here but imported only inside
            ``_generate_from_blender_subprocess_inner``.  This method will raise
            ``NameError: name 'Path' is not defined`` if called outside of that
            context.  Should add ``from pathlib import Path`` at the top of this
            method or at module level.
        """
        env = os.environ.copy()

        # Check for docs/agent/.mcp/venv
        mcp_venv = Path("docs/agent/.mcp/venv")
        if mcp_venv.exists():
            # Find site-packages
            site_packages = list(mcp_venv.glob("lib/python*/site-packages"))
            if site_packages:
                sp_path = str(site_packages[0].absolute())
                current_pythonpath = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = f"{sp_path}:{current_pythonpath}"
                logger.info(f"Injecting MCP venv into PYTHONPATH: {sp_path}")

        return env

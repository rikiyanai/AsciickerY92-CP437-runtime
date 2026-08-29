"""
Grid validation layer for enforcing 12px/cell sprite sheet rules.

Architecture
------------
This module sits between the downscale/resize stage and the processing
stage.  Its sole responsibility is to assert that an image's pixel
dimensions match what the template expects -- i.e., that the image is
an exact multiple of the 12px cell grid in both axes.

If the dimensions mismatch, the validator raises a ``GridError`` with
structured metadata (template name, expected vs. actual sizes, layout
details) so that upstream callers (``AssetPipeline.validate_and_downscale``)
can decide whether to auto-downscale or abort.

Key Exports
~~~~~~~~~~~
- ``GridError``             -- Exception with template-aware error formatting.
- ``GridValidator``         -- Validates image dimensions against a template.
- ``GridValidationResult``  -- Dataclass carrying the validation outcome.

Pipeline Context
~~~~~~~~~~~~~~~~
::

    [PIPELINE:GENERATE] -> [PIPELINE:SLICE / DOWNSCALE]
         -> **[PIPELINE:VALIDATE (this module)]**
              -> [PIPELINE:PROCESS] -> [PIPELINE:ASSEMBLE]

The validator is invoked by ``AssetPipeline.validate_and_downscale()`` in
pipeline.py.  If validation fails with ``can_downscale=True``, the pipeline
auto-resizes via ``ImageResizer`` and re-validates.  If the image is smaller
than expected, the error propagates (no silent upscaling per GRID-05).

Tags: [DATA-CONTRACT:XP] [DATA-CONTRACT:GRID] [FLOW:TEMPLATE] [PIPELINE:SLICE] [DEPENDENCY:PIL]
"""

from dataclasses import dataclass
from typing import Tuple, Dict, Optional
from PIL import Image  # [DEPENDENCY:PIL] -- Only used for type annotation on validate_image().


class GridError(Exception):
    """Exception raised when sprite sheet dimensions don't match template expectations.

    Carries structured metadata so that error handlers can programmatically
    inspect the mismatch (e.g., to compute a downscale factor) rather than
    parsing a free-text message.
    """

    def __init__(
        self,
        template_name: str,
        actual_dimensions: Tuple[int, int],
        expected_dimensions: Tuple[int, int],
        template_metadata: Optional[Dict] = None,
    ):
        """
        Initialize GridError with template-aware error messaging.

        Args:
            template_name: Name of the template being validated (e.g.,
                           "character_idle_walk").
            actual_dimensions: (width, height) of the actual image in pixels.
            expected_dimensions: (width, height) required by the template.
                                 Always a multiple of CELL_SIZE (12).
            template_metadata: Optional dict with layout details:
                               angles, frames, rows, cols.
        """
        self.template_name = template_name
        self.actual_dimensions = actual_dimensions
        self.expected_dimensions = expected_dimensions
        self.template_metadata = template_metadata or {}

        # Extract layout specs from metadata, with defaults
        self.rows = self.template_metadata.get("rows", "angles")
        self.cols = self.template_metadata.get("cols", "total_frames")
        self.angles = self.template_metadata.get("angles", "?")
        self.frames = self.template_metadata.get("frames", "?")

        super().__init__(self._format_error())

    def _format_error(self) -> str:
        """Format a human-readable error message with template metadata.

        The message includes the 12px/cell constraint explicitly so that
        users unfamiliar with the grid system can understand why their
        image was rejected.
        """
        actual_w, actual_h = self.actual_dimensions
        expected_w, expected_h = self.expected_dimensions

        # WHY: frames may arrive as either a list (e.g. [4, 6, 4]) or a scalar
        # from different template sources.  Both branches currently stringify the
        # same way, but the isinstance guard future-proofs the path for list-
        # specific formatting (e.g. comma-separated counts) without breaking
        # scalar callers.
        # TODO(PIPELINE-FIX): The two branches are currently identical; consider
        # formatting lists as "4, 6, 4" instead of "[4, 6, 4]" for readability.
        frames_str = (
            str(self.frames) if isinstance(self.frames, list) else str(self.frames)
        )

        # [DATA-CONTRACT:XP] -- The "12px/cell" reference documents the
        # fundamental grid constraint that the .xp format relies on.
        # Every cell in the output .xp file maps to exactly one 12x12
        # pixel block in the source image.
        return (
            f"Grid validation failed for template '{self.template_name}':\n"
            f"  Expected: {expected_w}x{expected_h}px (12px/cell, {self.rows} rows × {self.cols} cols)\n"
            f"  Actual: {actual_w}x{actual_h}px\n"
            f"  Layout: angles={self.angles}, frames={frames_str}\n"
            f"  Fix: Resize image to match template dimensions or downscale before validation"
        )

    def __str__(self) -> str:
        """Return formatted error message.

        # WHY: Delegates to the message stored by Exception.__init__ so that
        # both str(err) and logging formatters produce the same rich output
        # generated by _format_error().
        """
        return self.args[0]


@dataclass
class GridValidationResult:
    """Result of grid validation.  [DATA-CONTRACT:GRID]

    Attributes:
        valid: True if image dimensions match the template exactly.
        error: Human-readable error string (empty when valid).
        can_downscale: True when the image is *larger* than expected,
                       meaning automatic downscaling could fix the mismatch.
                       False when the image is smaller (upscaling is forbidden
                       per GRID-05).
    """

    valid: bool
    error: str = ""
    can_downscale: bool = False


class GridValidator:
    """Validates sprite sheet dimensions against template expectations.

    [FLOW:TEMPLATE] -- Receives its template from the pipeline's template
    loader; the template drives the expected dimensions.
    [DATA-CONTRACT:GRID] -- Enforces the 12px cell grid contract.

    The core invariant enforced here is:

        image.width  == template.expected_dimensions()[0]
        image.height == template.expected_dimensions()[1]

    where the template dimensions are always exact multiples of CELL_SIZE.
    This guarantees that the downstream slicer can partition the image into
    a clean grid of 12x12 cells with no leftover pixels.
    """

    # [DATA-CONTRACT:XP] -- 12 pixels per grid cell is the fundamental
    # spatial unit of the .xp format.  Changing this value would break
    # every template, the slicer, the processor, and the assembler.
    # TODO(PIPELINE-FIX): CELL_SIZE is also hardcoded in processor.py
    # and slicer.py.  Extract to a shared constant in schemas.py.
    CELL_SIZE = 12  # Per GRID-01: 12 pixels per grid cell

    def __init__(self, template) -> None:
        """
        Initialize GridValidator with a template.

        Args:
            template: Template object (from templates/models.py) that
                      exposes ``expected_dimensions() -> (int, int)``,
                      ``layout_rows() -> int``, ``layout_cols() -> int``,
                      as well as ``.angles``, ``.frames``, and ``.name``
                      attributes.
        """
        self.template = template

    # [PIPELINE:SLICE] -- This is the validation gate that determines
    # whether the image can proceed to the processing stage or must be
    # resized first.
    def validate_image(self, image: Image.Image) -> GridValidationResult:
        """
        Validate that image dimensions match template expectations exactly.

        The check is strict equality, not "close enough."  Even a 1px
        mismatch would cause the slicer to produce partial cells at the
        edges, corrupting the .xp output.

        Args:
            image: PIL Image to validate.

        Returns:
            GridValidationResult with ``valid=True`` if dimensions match.

        Raises:
            GridError: If dimensions don't match.  The error includes
                       ``can_downscale`` context so the caller can decide
                       whether to auto-resize.
        """
        # [DATA-CONTRACT:XP] -- expected dimensions are always multiples
        # of CELL_SIZE, enforced by the template's expected_dimensions().
        expected_w, expected_h = self.template.expected_dimensions()
        actual_w, actual_h = image.width, image.height

        if actual_w != expected_w or actual_h != expected_h:
            # WHY this heuristic: If *either* axis is larger than expected,
            # downscaling might fix the mismatch.  If both axes are smaller,
            # upscaling would introduce blurry artifacts and is forbidden
            # by design rule GRID-05.
            # Dimensions don't match - check if downscaling could fix it
            can_downscale = actual_w > expected_w or actual_h > expected_h

            # Build metadata dict for error message
            template_metadata = {
                "angles": self.template.angles,
                "frames": self.template.frames,
                "rows": self.template.layout_rows(),
                "cols": self.template.layout_cols(),
            }

            # Create and raise GridError with helpful message
            raise GridError(
                template_name=self.template.name,
                actual_dimensions=(actual_w, actual_h),
                expected_dimensions=(expected_w, expected_h),
                template_metadata=template_metadata,
            )

        # Dimensions match
        return GridValidationResult(valid=True, can_downscale=False)

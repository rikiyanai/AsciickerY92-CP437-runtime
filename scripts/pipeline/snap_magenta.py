"""
CLI tool for magenta background snapping.

Architecture
------------
This is a **standalone CLI wrapper** around the magenta-correction functions
in ``color_correction.py``.  It provides command-line access to three
operations:

1. **Analyze** (``--check``) -- Report the dominant background color and its
   distance from pure magenta without modifying the image.
2. **Auto-correct** (``--auto``) -- Detect the background color, compute an
   appropriate tolerance, and snap near-magenta pixels to exact (255,0,255).
3. **Manual correct** (positional ``output`` arg) -- Snap with an explicit
   tolerance and save to a specified output path.

Key Exports
~~~~~~~~~~~
- ``main()`` -- CLI entry point (also usable as ``python -m`` target).

Pipeline Context
~~~~~~~~~~~~~~~~
::

    [External render] -> **snap_magenta.py** -> [PIPELINE:GENERATE] -> ...

This tool is typically run *before* the main asset pipeline begins, as a
preprocessing step on raw renders from Blender or AI image generators.
Those tools often produce backgrounds that are *near*-magenta (e.g.,
(253, 2, 254) due to JPEG compression or color-space conversion) rather
than exact (255, 0, 255).  If these near-magenta pixels are not snapped,
the downstream processor's ``is_transparent()`` check (which uses a tight
tolerance of 5 by default) will misclassify them as opaque, leaving pink
fringes around sprites in the final .xp output.

Why Magenta (255, 0, 255)?
~~~~~~~~~~~~~~~~~~~~~~~~~~
The Asciicker engine uses "magic pink" / magenta as a color-key
transparency marker -- the same convention used by many DOS/early-Windows
game engines.  The .xp format has no alpha channel; transparency is
encoded by setting a cell's background to (255, 0, 255).  Every pipeline
stage (palette.py, processor.py, assembler.py) checks for this exact
RGB value.

Tags: [PIPELINE:PROCESS] [DATA-CONTRACT:PALETTE] [DEPENDENCY:PIL]

Usage:
    python scripts/pipeline/snap_magenta.py input.png output.png --tolerance 15
    python scripts/pipeline/snap_magenta.py input.png --auto
"""

import sys
import os

# WHY sys.path manipulation: This module can be invoked as a standalone script
# (python scripts/pipeline/snap_magenta.py ...) where the parent package is
# not on sys.path.  Adding the scripts/ directory allows the relative import
# from .color_correction to resolve correctly in both standalone and package
# invocation modes.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# [DATA-CONTRACT:PALETTE] -- All magenta-correction logic lives in
# color_correction.py.  This CLI is a thin wrapper.
from .color_correction import (
    snap_to_magenta,
    analyze_background,
    auto_magenta_correction,
)


def main():
    """CLI entry point for magenta background snapping.

    [PIPELINE:PROCESS] Parses command-line arguments and dispatches to one of
    three modes: check-only, auto-correct, or manual-correct.

    Args:
        None (reads from ``sys.argv`` via argparse).

    Returns:
        int: Exit code -- 0 on success, 1 on file-not-found.

    Raises:
        SystemExit: If argparse encounters invalid arguments.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Snap near-magenta colors to pure magenta (255,0,255) for transparency",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect and correct
  snap_magenta.py sprite.png --auto

  # Manual tolerance
  snap_magenta.py sprite.png sprite_corrected.png --tolerance 20

  # Quick check
  snap_magenta.py sprite.png --check
        """,
    )

    parser.add_argument("input", help="Input image path")
    parser.add_argument(
        "output", nargs="?", help="Output path (default: input_corrected.png)"
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=None,
        help="Snap tolerance (default: 15, or auto-detected)",
    )
    parser.add_argument(
        "--auto", action="store_true", help="Auto-detect and correct without prompting"
    )
    parser.add_argument(
        "--check", action="store_true", help="Only analyze, don't modify"
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        return 1

    print(f"Processing: {args.input}")

    # [DEPENDENCY:PIL] -- Load the source image for analysis.
    # Load image
    from PIL import Image

    img = Image.open(args.input)

    # [PIPELINE:PROCESS] -- Analyze the dominant background color and its
    # distance from the canonical magenta transparency key.
    # Analyze
    analysis = analyze_background(img)
    print()
    print("Analysis Results:")
    print(f"  Dominant color: RGB{analysis['dominant_color']}")
    print(f"  Distance from magenta (255,0,255): {analysis['distance_from_magenta']}")
    print(f"  Is magenta-like: {analysis['is_magenta_like']}")
    print(f"  Recommended tolerance: {analysis['recommended_correction']}")
    print()

    # WHY auto-detect tolerance: When no --tolerance is given, we derive
    # a tolerance from the actual distance.  The floor of 15 ensures that
    # even nearly-correct backgrounds get a small correction pass, catching
    # single-channel off-by-one errors from lossy compression.
    # TODO(PIPELINE-FIX): The "distance // 2" heuristic is ad-hoc.
    # Consider using a perceptual color-distance metric (e.g., CIE Delta-E)
    # instead of L1 divided by an arbitrary constant.
    # Auto-detect tolerance if not set
    tolerance = args.tolerance
    if tolerance is None and analysis["distance_from_magenta"] > 0:
        tolerance = max(15, analysis["distance_from_magenta"] // 2)

    # Check or correct
    if args.check:
        print("Checking only (--check flag set)")
        if analysis["is_magenta_like"]:
            print("✓ Image already has good magenta background")
        else:
            print(
                f"⚠ Image NOT magenta-like (distance {analysis['distance_from_magenta']})"
            )
            print(f"Recommended: snap_magenta.py {args.input} --auto")
        return 0

    # [DATA-CONTRACT:PALETTE] -- After correction, every pixel that was
    # within tolerance of magenta becomes exactly (255, 0, 255), matching
    # the canonical transparency key used by palette.is_transparent().
    # WHY fallback to auto when no output path: If the user provides neither
    # --auto nor an explicit output path, we treat it as auto-mode rather than
    # erroring out.  This makes the simplest invocation ("snap_magenta.py img.png")
    # do the most useful thing by default.
    # TODO(PIPELINE-FIX): This implicit fallback to auto mode when `not args.output`
    # is a fragile assumption -- a user who forgets the output arg may not expect
    # auto-correction.  Consider requiring either --auto or an explicit output path.
    if args.auto or not args.output:
        # Auto mode
        print(f"Auto-correcting with tolerance={tolerance}...")
        result = auto_magenta_correction(args.input, tolerance=tolerance)
        print(f"\n✓ Done! Result: {result}")
    else:
        # Manual mode
        print(f"Correcting with tolerance={tolerance}...")
        corrected = snap_to_magenta(img, tolerance=tolerance)
        corrected.save(args.output)
        print(f"✓ Saved: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

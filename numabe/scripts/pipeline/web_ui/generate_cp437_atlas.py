"""
generate_cp437_atlas.py -- Generate a CP437 font atlas PNG for web rendering.

Copies the engine's CP437 font atlas to the web_ui/assets directory,
optionally generating a scaled version for sharper web display.

The CP437 atlas is a 16x16 grid of glyphs. At 12x12 pixels per glyph,
the native atlas is 192x192 pixels. For web display, a 2x scaled version
(384x384) provides crisp rendering on retina screens.

Usage:
    python3 scripts/pipeline/web_ui/generate_cp437_atlas.py
    python3 scripts/pipeline/web_ui/generate_cp437_atlas.py --scale 2

Tags: [FLOW:WEB-UI] [DATA-CONTRACT:CP437] [DEPENDENCY:PIL]
"""

import argparse
import shutil
from pathlib import Path

from PIL import Image


# Engine font directory relative to repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_FONTS_DIR = _REPO_ROOT / "assets" / "fonts"
_ASSETS_DIR = Path(__file__).parent / "assets"

# Default font sizes available in the engine
_CELL_SIZE = 12
_FONT_FILENAME = f"cp437_{_CELL_SIZE}x{_CELL_SIZE}.png"


def generate_atlas(
    cell_size: int = _CELL_SIZE,
    scale: int = 1,
    output_dir: str = None,
) -> Path:
    """Generate (or copy) the CP437 web atlas.

    Args:
        cell_size: Source font cell size in pixels.
        scale: Scale factor for output (1 = native, 2 = retina).
        output_dir: Output directory. Defaults to web_ui/assets/.

    Returns:
        Path to the generated atlas PNG.

    Raises:
        FileNotFoundError: If source font not found.
    """
    font_name = f"cp437_{cell_size}x{cell_size}.png"
    source = _FONTS_DIR / font_name

    if not source.exists():
        raise FileNotFoundError(
            f"Source font not found: {source}. "
            f"Available fonts: {sorted(f.name for f in _FONTS_DIR.glob('cp437_*.png'))}"
        )

    out_dir = Path(output_dir) if output_dir else _ASSETS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if scale == 1:
        # Simple copy for native resolution
        dest = out_dir / "cp437_atlas.png"
        shutil.copy2(str(source), str(dest))
    else:
        # Scale up with nearest-neighbor (pixel art)
        img = Image.open(str(source))
        scaled = img.resize(
            (img.width * scale, img.height * scale),
            Image.NEAREST,
        )
        dest = out_dir / f"cp437_atlas_{scale}x.png"
        scaled.save(str(dest))

    return dest


def main():
    """CLI entry point for atlas generation."""
    parser = argparse.ArgumentParser(
        description="Generate CP437 font atlas for web rendering"
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help="Scale factor (1=native 192x192, 2=retina 384x384)",
    )
    parser.add_argument(
        "--cell-size",
        type=int,
        default=_CELL_SIZE,
        help=f"Source font cell size (default: {_CELL_SIZE})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: web_ui/assets/)",
    )
    args = parser.parse_args()

    path = generate_atlas(
        cell_size=args.cell_size,
        scale=args.scale,
        output_dir=args.output_dir,
    )
    print(f"Generated: {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

"""Thumbnail generation for branch artifacts.

Generates 128x128 PNG thumbnails for PIL images and .xp files.
Thumbnails are stored under staging/thumbnails/{job_id}/{branch_id}.png.

Tags: [FLOW:BRANCH] [DEPENDENCY:PIL]
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

THUMB_SIZE = (128, 128)


def generate_thumbnail_from_image(
    image: Image.Image,
    job_id: str,
    branch_id: str,
    staging_dir: str | Path,
) -> str:
    """Generate a 128x128 thumbnail from a PIL Image.

    Args:
        image: Source PIL Image (any mode/size).
        job_id: Job identifier for directory layout.
        branch_id: Branch identifier for filename.
        staging_dir: Base staging directory.

    Returns:
        Absolute path to the generated thumbnail PNG.
    """
    thumb_dir = Path(staging_dir) / "thumbnails" / job_id
    thumb_dir.mkdir(parents=True, exist_ok=True)

    thumb = image.copy()
    thumb.thumbnail(THUMB_SIZE, Image.LANCZOS)

    # Center on a transparent 128x128 canvas for uniform card sizes
    canvas = Image.new("RGBA", THUMB_SIZE, (0, 0, 0, 0))
    offset_x = (THUMB_SIZE[0] - thumb.width) // 2
    offset_y = (THUMB_SIZE[1] - thumb.height) // 2
    canvas.paste(thumb, (offset_x, offset_y))

    out_path = thumb_dir / f"{branch_id}.png"
    canvas.save(str(out_path), format="PNG")
    return str(out_path)


def generate_thumbnail_from_xp(
    xp_path: str | Path,
    job_id: str,
    branch_id: str,
    staging_dir: str | Path,
) -> str:
    """Generate a 128x128 thumbnail from an .xp sprite file.

    Uses export_xp_to_png() to render the visual layer, then thumbnails.

    Args:
        xp_path: Path to the .xp file.
        job_id: Job identifier for directory layout.
        branch_id: Branch identifier for filename.
        staging_dir: Base staging directory.

    Returns:
        Absolute path to the generated thumbnail PNG.

    Raises:
        FileNotFoundError: If xp_path does not exist.
    """
    from scripts.pipeline.export_service import export_xp_to_png

    rendered = export_xp_to_png(str(xp_path))
    return generate_thumbnail_from_image(rendered, job_id, branch_id, staging_dir)


def generate_thumbnail(
    source: Image.Image | str | Path,
    job_id: str,
    branch_id: str,
    staging_dir: str | Path,
) -> Optional[str]:
    """Generate a thumbnail from either a PIL Image or file path.

    Auto-detects source type:
    - PIL Image -> direct thumbnail
    - .xp path -> render then thumbnail
    - Image file path (.png, .jpg, etc.) -> open then thumbnail

    Args:
        source: PIL Image, .xp path, or image file path.
        job_id: Job identifier.
        branch_id: Branch identifier.
        staging_dir: Base staging directory.

    Returns:
        Path to thumbnail PNG, or None if generation failed.
    """
    try:
        if isinstance(source, Image.Image):
            return generate_thumbnail_from_image(
                source, job_id, branch_id, staging_dir
            )

        path = Path(source)
        if not path.exists():
            return None

        if path.suffix.lower() == ".xp":
            return generate_thumbnail_from_xp(
                path, job_id, branch_id, staging_dir
            )

        # Regular image file
        img = Image.open(str(path))
        img.load()
        return generate_thumbnail_from_image(img, job_id, branch_id, staging_dir)
    except Exception:
        return None

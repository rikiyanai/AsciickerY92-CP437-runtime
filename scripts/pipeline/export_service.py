"""
export_service.py -- XP-to-PNG/ZIP/GIF export backend.

Converts .xp sprite files to image formats for web display, download,
and sharing. Uses xp_viewer.py rendering for engine-aligned output.

EXPORTS:
    export_xp_to_png(xp_path, layer_idx) -> PIL.Image
    export_xp_to_frames(xp_path) -> List[PIL.Image]
    export_xp_to_zip(xp_path, output_path) -> Path
    export_xp_to_gif(xp_path, output_path, fps, loop) -> Path

Tags: [FLOW:EXPORT] [DATA-CONTRACT:XP] [DEPENDENCY:PIL]
"""

import io
import zipfile
from pathlib import Path
from typing import List, Optional

from PIL import Image

try:
    from scripts.pipeline.xp_viewer import XPViewer
    from scripts.pipeline.xp_core import XPFile
except ModuleNotFoundError:
    from xp_viewer import XPViewer
    from xp_core import XPFile


# Default font path relative to repo root
_REPO_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_FONT = _REPO_ROOT / "assets" / "fonts" / "cp437_12x12.png"

# Default cell size matches engine glyph grid
_CELL_SIZE = 12


def _resolve_font(font_path: Optional[str] = None) -> Optional[str]:
    """Resolve font path, falling back to project default.

    Delegates to _render_core.find_font_atlas() as final fallback.

    Args:
        font_path: Explicit font path, or None for default.

    Returns:
        Resolved font path string, or None if default not found.
    """
    if font_path is not None:
        p = Path(font_path)
        if p.exists():
            return str(p)
        return None

    if _DEFAULT_FONT.exists():
        return str(_DEFAULT_FONT)

    # Fallback: use _render_core's search logic
    from scripts.pipeline._render_core import find_font_atlas
    return find_font_atlas()


def export_xp_to_png(
    xp_path: str,
    layer_idx: int = 2,
    font_path: Optional[str] = None,
    char_w: int = _CELL_SIZE,
    char_h: int = _CELL_SIZE,
    scale: int = 1,
) -> Image.Image:
    """Render an XP file's visual layer to a full-sheet PNG.

    Renders the entire sprite atlas as a single image, using the CP437
    font atlas for glyph rendering. This is the simplest export path.

    Args:
        xp_path: Path to the .xp sprite file.
        layer_idx: Layer to render (default 2 = visual layer).
        font_path: Path to CP437 font PNG, or None for default.
        char_w: Character cell width in pixels.
        char_h: Character cell height in pixels.
        scale: Output scale factor (1 = native resolution).

    Returns:
        PIL Image (RGBA) of the rendered sprite sheet.

    Raises:
        FileNotFoundError: If xp_path does not exist.
        ValueError: If layer_idx is out of range.
    """
    xp_path = Path(xp_path)
    if not xp_path.exists():
        raise FileNotFoundError(f"XP file not found: {xp_path}")

    viewer = XPViewer(str(xp_path))

    if not viewer.xp.layers:
        raise ValueError(f"XP file has no layers: {xp_path}")

    if layer_idx >= len(viewer.xp.layers):
        # Fall back to last available layer (e.g. 1-layer files lack visual layer 2)
        layer_idx = len(viewer.xp.layers) - 1

    resolved_font = _resolve_font(font_path)
    return viewer.render_all_frames(
        font_path=resolved_font,
        char_w=char_w,
        char_h=char_h,
        scale=scale,
    )


def export_xp_to_frames(
    xp_path: str,
    font_path: Optional[str] = None,
    char_w: int = _CELL_SIZE,
    char_h: int = _CELL_SIZE,
    scale: int = 1,
) -> List[Image.Image]:
    """Parse XP metadata and render individual frames as separate images.

    Uses engine-aligned metadata parsing to split the sprite atlas into
    individual animation frames. Each frame is rendered with the CP437
    font atlas for visual fidelity.

    Args:
        xp_path: Path to the .xp sprite file.
        font_path: Path to CP437 font PNG, or None for default.
        char_w: Character cell width in pixels.
        char_h: Character cell height in pixels.
        scale: Output scale factor.

    Returns:
        List of PIL Images (RGBA), one per (angle, anim, frame) combination.
        Empty list if file has no renderable frames.

    Raises:
        FileNotFoundError: If xp_path does not exist.
    """
    xp_path = Path(xp_path)
    if not xp_path.exists():
        raise FileNotFoundError(f"XP file not found: {xp_path}")

    viewer = XPViewer(str(xp_path))
    if not viewer.meta or len(viewer.xp.layers) < 3:
        return []

    resolved_font = _resolve_font(font_path)
    frames = []

    angles = viewer.meta.get("angles", 1)
    anims = viewer.meta.get("anims", [1])
    projs = viewer.meta.get("projs", 1)

    for angle_idx in range(angles):
        yaw = (angle_idx * 360.0 / angles) if angles > 1 else 0.0
        for proj_idx in range(projs):
            for anim_idx, anim_len in enumerate(anims):
                for frame_idx in range(anim_len):
                    img = viewer.render_frame(
                        anim=anim_idx,
                        frame=frame_idx,
                        yaw=yaw,
                        proj=proj_idx,
                        font_path=resolved_font,
                        char_w=char_w,
                        char_h=char_h,
                        scale=scale,
                    )
                    frames.append(img)

    return frames


def export_xp_to_zip(
    xp_path: str,
    output_path: str,
    font_path: Optional[str] = None,
    char_w: int = _CELL_SIZE,
    char_h: int = _CELL_SIZE,
    scale: int = 1,
) -> Path:
    """Export all frames of an XP file to a ZIP archive of PNGs.

    Each frame is named with its angle, projection, animation, and
    frame indices for unambiguous identification.

    Args:
        xp_path: Path to the .xp sprite file.
        output_path: Destination path for the ZIP file.
        font_path: Path to CP437 font PNG, or None for default.
        char_w: Character cell width in pixels.
        char_h: Character cell height in pixels.
        scale: Output scale factor.

    Returns:
        Path to the created ZIP file.

    Raises:
        FileNotFoundError: If xp_path does not exist.
        ValueError: If no frames could be extracted.
    """
    xp_path_obj = Path(xp_path)
    if not xp_path_obj.exists():
        raise FileNotFoundError(f"XP file not found: {xp_path}")

    viewer = XPViewer(str(xp_path_obj))
    if not viewer.meta or len(viewer.xp.layers) < 3:
        raise ValueError(f"XP file has no renderable frames: {xp_path}")

    resolved_font = _resolve_font(font_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    angles = viewer.meta.get("angles", 1)
    anims = viewer.meta.get("anims", [1])
    projs = viewer.meta.get("projs", 1)
    stem = xp_path_obj.stem

    frame_count = 0
    with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as zf:
        for angle_idx in range(angles):
            yaw = (angle_idx * 360.0 / angles) if angles > 1 else 0.0
            for proj_idx in range(projs):
                for anim_idx, anim_len in enumerate(anims):
                    for frame_idx in range(anim_len):
                        img = viewer.render_frame(
                            anim=anim_idx,
                            frame=frame_idx,
                            yaw=yaw,
                            proj=proj_idx,
                            font_path=resolved_font,
                            char_w=char_w,
                            char_h=char_h,
                            scale=scale,
                        )
                        fname = (
                            f"{stem}_a{angle_idx}"
                            f"_p{proj_idx}"
                            f"_anim{anim_idx}"
                            f"_f{frame_idx}.png"
                        )
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        zf.writestr(fname, buf.getvalue())
                        frame_count += 1

    if frame_count == 0:
        raise ValueError(f"No frames exported from: {xp_path}")

    return output


def export_xp_to_gif(
    xp_path: str,
    output_path: str,
    fps: int = 8,
    loop: bool = True,
    anim: int = 0,
    angle: int = 0,
    proj: int = 0,
    font_path: Optional[str] = None,
    char_w: int = _CELL_SIZE,
    char_h: int = _CELL_SIZE,
    scale: int = 1,
) -> Path:
    """Export a single animation sequence from an XP file as animated GIF.

    Renders all frames of the specified animation at a given angle and
    projection, then combines them into an animated GIF.

    Args:
        xp_path: Path to the .xp sprite file.
        output_path: Destination path for the GIF file.
        fps: Frames per second for the animation.
        loop: Whether the GIF loops (True = infinite loop).
        anim: Animation index to export.
        angle: Angle index (0-based).
        proj: Projection index (0 = projection, 1 = reflection).
        font_path: Path to CP437 font PNG, or None for default.
        char_w: Character cell width in pixels.
        char_h: Character cell height in pixels.
        scale: Output scale factor.

    Returns:
        Path to the created GIF file.

    Raises:
        FileNotFoundError: If xp_path does not exist.
        ValueError: If animation index is out of range or no frames rendered.
    """
    xp_path_obj = Path(xp_path)
    if not xp_path_obj.exists():
        raise FileNotFoundError(f"XP file not found: {xp_path}")

    viewer = XPViewer(str(xp_path_obj))
    if not viewer.meta or len(viewer.xp.layers) < 3:
        raise ValueError(f"XP file has no renderable frames: {xp_path}")

    resolved_font = _resolve_font(font_path)
    anims = viewer.meta.get("anims", [1])
    angles_count = viewer.meta.get("angles", 1)

    if anim >= len(anims):
        raise ValueError(
            f"Animation index {anim} out of range "
            f"(file has {len(anims)} animations)"
        )

    if angle >= angles_count:
        raise ValueError(
            f"Angle index {angle} out of range "
            f"(file has {angles_count} angles)"
        )

    yaw = (angle * 360.0 / angles_count) if angles_count > 1 else 0.0
    anim_len = anims[anim]
    duration_ms = max(1, int(1000 / fps))

    gif_frames = []
    for frame_idx in range(anim_len):
        img = viewer.render_frame(
            anim=anim,
            frame=frame_idx,
            yaw=yaw,
            proj=proj,
            font_path=resolved_font,
            char_w=char_w,
            char_h=char_h,
            scale=scale,
        )
        # GIF requires palette mode; convert RGBA -> RGB with white bg
        rgb_frame = Image.new("RGB", img.size, (0, 0, 0))
        rgb_frame.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        gif_frames.append(rgb_frame)

    if not gif_frames:
        raise ValueError(f"No frames rendered for anim={anim} at angle={angle}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    loop_count = 0 if loop else 1
    if len(gif_frames) == 1:
        gif_frames[0].save(str(output), format="GIF")
    else:
        gif_frames[0].save(
            str(output),
            format="GIF",
            save_all=True,
            append_images=gif_frames[1:],
            duration=duration_ms,
            loop=loop_count,
        )

    return output

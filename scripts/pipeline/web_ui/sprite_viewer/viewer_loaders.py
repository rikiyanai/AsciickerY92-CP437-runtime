"""
viewer_loaders.py -- Backend logic for the sprite viewer load endpoints.

Extracts frames from PNG sheets and XP files, returning a shared
response dict matching the FrameSequence contract.

Called by:
  - web_api/routes.py (HTTP endpoints)
  - tests/test_viewer_loaders.py (direct Python tests)

Tags: [FLOW:VIEWER] [PIPELINE:SLICE] [DATA-CONTRACT:XP]
"""

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

try:
    from scripts.pipeline.slicer import ImageSlicer
    from scripts.pipeline.xp_viewer import XPViewer
    from scripts.pipeline.xp_core import XPFile
    from scripts.pipeline.service.slicing import SlicingSpec, infer_sheet_spec
except ModuleNotFoundError:
    from slicer import ImageSlicer
    from xp_viewer import XPViewer
    from xp_core import XPFile
    from service.slicing import SlicingSpec, infer_sheet_spec


# ============================================================================
# Payload limits
# ============================================================================

MAX_FRAMES_RETURNED = 256
MAX_RESPONSE_BYTES = 8_000_000  # 8 MB


# ============================================================================
# Helpers
# ============================================================================


def _image_to_base64(img: Image.Image) -> str:
    """Convert a PIL Image to a base64-encoded PNG string.

    Args:
        img: PIL Image (any mode).

    Returns:
        Base64-encoded PNG string (no data URI prefix).
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _apply_pagination(
    frame_dicts: List[Dict],
    offset: int,
    limit: int,
) -> Tuple[List[Dict], bool, Optional[int]]:
    """Apply offset/limit pagination to a frame list.

    Args:
        frame_dicts: Full list of frame dicts.
        offset: Number of frames to skip.
        limit: Max frames to return.

    Returns:
        Tuple of (paginated_frames, truncated, next_offset_or_None).
    """
    total = len(frame_dicts)
    capped_limit = min(limit, MAX_FRAMES_RETURNED)

    start = min(offset, total)
    end = min(start + capped_limit, total)

    paginated = frame_dicts[start:end]
    truncated = end < total
    next_offset = end if truncated else None

    return paginated, truncated, next_offset


def _check_response_size(frame_dicts: List[Dict]) -> List[Dict]:
    """Trim frames if total base64 payload exceeds MAX_RESPONSE_BYTES.

    Mutates nothing; returns a (possibly shorter) list.

    Args:
        frame_dicts: List of frame dicts with 'data' (base64 string).

    Returns:
        List of frame dicts within the byte budget.
    """
    total_bytes = 0
    result = []
    for fd in frame_dicts:
        frame_bytes = len(fd.get("data", ""))
        if total_bytes + frame_bytes > MAX_RESPONSE_BYTES and result:
            break
        result.append(fd)
        total_bytes += frame_bytes
    return result


# ============================================================================
# PNG loader
# ============================================================================


def load_png_frames(
    image_path: str,
    config: Optional[Dict[str, Any]] = None,
    offset: int = 0,
    limit: int = MAX_FRAMES_RETURNED,
) -> Dict[str, Any]:
    """Load a PNG sprite sheet and extract individual frames.

    Uses the slicer to split the sheet into frames based on config
    or grid inference. Returns the shared response shape.

    Args:
        image_path: Path to the PNG file.
        config: Optional dict with angles, frames, projs, cell_w, cell_h,
                order, origin, angle_row_map, max_frames, scale.
        offset: Pagination offset (0-based).
        limit: Max frames to return.

    Returns:
        Dict with shared response shape:
        {frames, angles, anims, projs, metadata, truncated,
         total_frames, returned_frames, next_offset}

    Raises:
        FileNotFoundError: If image_path does not exist.
        ValueError: If grid inference fails or config is invalid.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"PNG file not found: {image_path}")

    img = Image.open(str(path))
    cfg = config or {}

    # Extract config values
    angles = cfg.get("angles")
    frames_list = cfg.get("frames")
    projs = cfg.get("projs", 1)
    cell_w = cfg.get("cell_w")
    cell_h = cfg.get("cell_h")
    order = cfg.get("order", "angle_major")
    origin = cfg.get("origin", "top_left")
    angle_row_map = cfg.get("angle_row_map")
    scale = cfg.get("scale", 1)

    # If angles/frames not provided, attempt inference
    if angles is None or frames_list is None:
        inferred = _infer_png_params(img, cell_w, cell_h)
        if inferred is None:
            raise ValueError(
                "Cannot infer sprite layout from image dimensions. "
                "Provide 'angles' and 'frames' in config. "
                f"Image size: {img.width}x{img.height}"
            )
        if angles is None:
            angles = inferred["angles"]
        if frames_list is None:
            frames_list = inferred["frames"]
        metadata_source = "inference"
    else:
        metadata_source = "explicit"

    if isinstance(frames_list, int):
        frames_list = [frames_list]
    frames_tuple = tuple(frames_list)

    # Build SlicingSpec if explicit cell sizes provided
    slice_spec = None
    if cell_w is not None or cell_h is not None:
        slice_spec = SlicingSpec(
            cell_w_px=cell_w,
            cell_h_px=cell_h,
            order=order,
            origin=origin,
            angle_row_map=angle_row_map,
        )
    elif angle_row_map is not None or order != "angle_major":
        slice_spec = SlicingSpec(
            order=order,
            origin=origin,
            angle_row_map=angle_row_map,
        )

    # Slice the image
    slicer = ImageSlicer()
    frame_images = slicer.slice(
        img,
        angles=angles,
        frames=list(frames_tuple),
        slice_spec=slice_spec,
    )

    # Build frame dicts
    all_frames = []
    frame_counter = 0
    total_per_angle = sum(frames_tuple)

    for angle_idx in range(max(angles, 1)):
        anim_frame_offset = 0
        for anim_idx, anim_len in enumerate(frames_tuple):
            for fi in range(anim_len):
                flat_idx = angle_idx * total_per_angle + anim_frame_offset + fi
                if flat_idx < len(frame_images):
                    frame_img = frame_images[flat_idx]
                    if scale > 1:
                        frame_img = frame_img.resize(
                            (frame_img.width * scale, frame_img.height * scale),
                            Image.NEAREST,
                        )
                    all_frames.append({
                        "data": _image_to_base64(frame_img),
                        "width": frame_img.width,
                        "height": frame_img.height,
                        "angle_idx": angle_idx,
                        "anim_idx": anim_idx,
                        "frame_idx": fi,
                    })
                frame_counter += 1
            anim_frame_offset += anim_len

    total_frames = len(all_frames)

    # Apply pagination
    paginated, truncated, next_offset = _apply_pagination(
        all_frames, offset, limit,
    )

    # Apply size check
    size_checked = _check_response_size(paginated)
    if len(size_checked) < len(paginated):
        truncated = True
        next_offset = offset + len(size_checked)

    # Infer cell dimensions for metadata
    if cell_w and cell_h:
        meta_cell_w, meta_cell_h = cell_w, cell_h
    elif all_frames:
        first = all_frames[0]
        meta_cell_w = first["width"] // max(scale, 1)
        meta_cell_h = first["height"] // max(scale, 1)
    else:
        meta_cell_w = meta_cell_h = 0

    result = {
        "frames": size_checked,
        "angles": angles,
        "anims": list(frames_tuple),
        "projs": projs,
        "metadata": {
            "cell_w": meta_cell_w,
            "cell_h": meta_cell_h,
            "source": metadata_source,
            "image_width": img.width,
            "image_height": img.height,
        },
        "truncated": truncated,
        "total_frames": total_frames,
        "returned_frames": len(size_checked),
    }

    if next_offset is not None:
        result["next_offset"] = next_offset

    return result


def _infer_png_params(
    img: Image.Image,
    cell_w: Optional[int] = None,
    cell_h: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Attempt to infer angles and frames from image dimensions.

    Tries common sprite layouts: 1-angle, 4-angle, 8-angle.
    For each, checks if the image divides evenly.

    Args:
        img: PIL Image.
        cell_w: Optional explicit cell width.
        cell_h: Optional explicit cell height.

    Returns:
        Dict with 'angles' and 'frames' if unambiguous, else None.
    """
    w, h = img.width, img.height

    # If cell size is explicit, use it to determine grid
    if cell_w and cell_h:
        if w % cell_w != 0 or h % cell_h != 0:
            return None
        cols = w // cell_w
        rows = h // cell_h
        # Assume single animation with cols frames per angle
        return {"angles": rows, "frames": [cols]}

    # Try common angle counts
    candidates = []
    for angles in [1, 4, 8]:
        if h % angles != 0:
            continue
        row_h = h // angles
        # Try to find a valid column count
        # Try common total frame counts
        for total_cols in range(1, min(w + 1, 65)):
            if w % total_cols != 0:
                continue
            col_w = w // total_cols
            # Cell should be roughly square-ish (1:4 ratio max)
            if col_w > 0 and row_h > 0:
                ratio = max(col_w, row_h) / max(min(col_w, row_h), 1)
                if ratio <= 4:
                    candidates.append({
                        "angles": angles,
                        "frames": [total_cols],
                        "cell_w": col_w,
                        "cell_h": row_h,
                    })

    if len(candidates) == 1:
        return candidates[0]

    # Multiple candidates -- ambiguous, return None to force explicit config
    if len(candidates) > 1:
        return None

    # No candidates found
    return None


# ============================================================================
# XP loader
# ============================================================================


def load_xp_frames(
    xp_path: str,
    offset: int = 0,
    limit: int = MAX_FRAMES_RETURNED,
    font_path: Optional[str] = None,
    scale: int = 1,
) -> Dict[str, Any]:
    """Load an XP file and extract rendered frames.

    Uses XPViewer to parse metadata and render individual frames
    with the CP437 font atlas.

    Args:
        xp_path: Path to the .xp file.
        offset: Pagination offset (0-based).
        limit: Max frames to return.
        font_path: Optional path to CP437 font PNG.
        scale: Output scale factor.

    Returns:
        Dict with shared response shape.

    Raises:
        FileNotFoundError: If xp_path does not exist.
        ValueError: If XP file has no renderable frames.
    """
    path = Path(xp_path)
    if not path.exists():
        raise FileNotFoundError(f"XP file not found: {xp_path}")

    viewer = XPViewer(str(path))

    if not viewer.meta or len(viewer.xp.layers) < 3:
        raise ValueError(f"XP file has no renderable frames: {xp_path}")

    # Resolve font
    if font_path is None:
        repo_root = Path(__file__).parent.parent.parent.parent.parent
        default_font = repo_root / "fonts" / "cp437_12x12.png"
        if default_font.exists():
            font_path = str(default_font)

    angles = viewer.meta.get("angles", 1)
    anims = viewer.meta.get("anims", [1])
    projs = viewer.meta.get("projs", 1)

    # Build all frames
    all_frames = []
    for angle_idx in range(angles):
        yaw = (angle_idx * 360.0 / angles) if angles > 1 else 0.0
        for anim_idx, anim_len in enumerate(anims):
            for frame_idx in range(anim_len):
                frame_img = viewer.render_frame(
                    anim=anim_idx,
                    frame=frame_idx,
                    yaw=yaw,
                    proj=0,
                    font_path=font_path,
                    scale=scale,
                )
                all_frames.append({
                    "data": _image_to_base64(frame_img),
                    "width": frame_img.width,
                    "height": frame_img.height,
                    "angle_idx": angle_idx,
                    "anim_idx": anim_idx,
                    "frame_idx": frame_idx,
                })

    total_frames = len(all_frames)

    # Apply pagination
    paginated, truncated, next_offset = _apply_pagination(
        all_frames, offset, limit,
    )

    # Apply size check
    size_checked = _check_response_size(paginated)
    if len(size_checked) < len(paginated):
        truncated = True
        next_offset = offset + len(size_checked)

    # Cell dimensions from XP metadata.
    # The XP file stores fr_width/fr_height in logical cells (12px glyphs).
    # The actual cell_w/cell_h in pixels depends on the render_resolution
    # used during pipeline generation, which is NOT stored in XP metadata.
    # Return None to signal "unknown" rather than lying with a hardcoded 12.
    char_w = None
    char_h = None
    if viewer.meta:
        fr_w = viewer.meta.get("fr_width", 1)
        fr_h = viewer.meta.get("fr_height", 1)
        char_w_pixel = fr_w * 12 * scale
        char_h_pixel = fr_h * 12 * scale
    else:
        char_w_pixel = 12
        char_h_pixel = 12

    result = {
        "frames": size_checked,
        "angles": angles,
        "anims": list(anims),
        "projs": projs,
        "metadata": {
            "cell_w": char_w,
            "cell_h": char_h,
            "source": "xp_metadata",
            "fr_width_cells": viewer.meta.get("fr_width", 0),
            "fr_height_cells": viewer.meta.get("fr_height", 0),
        },
        "truncated": truncated,
        "total_frames": total_frames,
        "returned_frames": len(size_checked),
    }

    if next_offset is not None:
        result["next_offset"] = next_offset

    return result

"""
reflection_handler.py -- Reflection detection and generation for sprite sheets.

ARCHITECTURE:
    This module handles the reflection requirement for multi-angle sprites.
    The Asciicker engine (sprite.cpp:806-808) sets projs=2 when angles > 0,
    meaning it expects the sheet width to contain both projection and
    reflection halves: width = projs * anim_sum * frame_width

    [FLOW:REFLECTION] Data flow:
        PNG sheet --> detect_reflections() --> existing? skip : generate

KEY EXPORTS:
    - ReflectionHandler: Main class for detection + generation workflow
    - detect_reflections: Check if sheet already has reflection data
    - generate_reflections: Create reflection half from projection half
    - validate_reflection_geometry: Verify sheet meets engine contract

ENGINE CONTRACT (sprite.cpp):
    if (angles > 0) projs = 2;
    fr_num_x = projs * sum(anim_lengths);
    frame_width = width / fr_num_x;  // MUST be integer!

PIPELINE CONTEXT:
    [PIPELINE:REFLECTION] Runs after slicing, before XP assembly.
    Ensures all multi-angle sprites have valid reflection geometry.
"""

from dataclasses import dataclass
from typing import Tuple, List, Dict, Any, Optional

from PIL import Image
import numpy as np

from .palette import MAGENTA_RGB
from scripts.pipeline.service.constants import CELL_SIZE


# =============================================================================
# Geometry Validation
# =============================================================================

def validate_reflection_geometry(
    width: int,
    height: int,
    angles: int,
    anim_frames: List[int],
    cell_size: int = CELL_SIZE
) -> Tuple[bool, Dict[str, Any]]:
    """Validate that sheet geometry meets engine contract.

    [ENGINE CONTRACT] When angles > 0, engine sets projs=2, so:
        expected_width = projs * anim_sum * cell_size
        frame_width = width / (projs * anim_sum) must be integer

    Args:
        width: Sheet width in pixels
        height: Sheet height in pixels
        angles: Number of angle rows (1, 4, or 8)
        anim_frames: List of frame counts per animation
        cell_size: Expected cell size in pixels (default 12)

    Returns:
        (is_valid, info_dict) where info_dict contains diagnostic data
    """
    anim_sum = sum(anim_frames)

    # Determine expected projs based on engine behavior
    projs = 2 if angles > 0 else 1

    # Angles <= 0 are treated as single-angle (legacy encoding).
    if angles <= 0:
        projs = 1

    # Calculate expected dimensions
    expected_columns = projs * anim_sum
    frame_width = width / expected_columns if expected_columns > 0 else 0
    frame_height = height / angles if angles > 0 else height

    # Build info dict
    info = {
        'angles': angles,
        'projs': projs,
        'anim_sum': anim_sum,
        'expected_columns': expected_columns,
        'frame_width': frame_width,
        'frame_height': frame_height,
        'width': width,
        'height': height,
        'expected_width': expected_columns * cell_size,
    }

    # Validate
    is_valid = True
    issues = []

    # Frame width must be integer
    if frame_width != int(frame_width):
        is_valid = False
        issues.append(f"Frame width not integer: {frame_width}")

    # Frame width MUST match expected cell size for proper engine parsing
    # This catches the case where width isn't doubled for reflections
    if frame_width > 0 and abs(frame_width - cell_size) > 0.5:
        is_valid = False
        issues.append(f"Frame width {frame_width} doesn't match cell_size {cell_size}")
        info['frame_width_mismatch'] = True

    # Frame height should be integer and match cell size
    if frame_height != int(frame_height):
        is_valid = False
        issues.append(f"Frame height not integer: {frame_height}")

    info['is_valid'] = is_valid
    info['issues'] = issues

    return is_valid, info


# =============================================================================
# Reflection Detection
# =============================================================================

def _detect_background_color(rgb: np.ndarray) -> np.ndarray:
    """Detect dominant background color from corner samples.

    Samples the four corners of the image and returns the most common
    color as a (3,) array. Works for magenta, black, white, or any
    solid-color background.
    """
    h, w = rgb.shape[:2]
    sample_size = max(1, min(8, w // 8, h // 8))

    corners = [
        rgb[:sample_size, :sample_size],                    # top-left
        rgb[:sample_size, w - sample_size:],                # top-right
        rgb[h - sample_size:, :sample_size],                # bottom-left
        rgb[h - sample_size:, w - sample_size:],            # bottom-right
    ]

    all_pixels = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    # Use median for robustness against edge sprite pixels in corners
    return np.median(all_pixels, axis=0).astype(np.uint8)


def _make_content_mask(rgb: np.ndarray, bg_color: np.ndarray, tolerance: int = 30) -> np.ndarray:
    """Create boolean mask of content (non-background) pixels.

    Uses L1 distance from bg_color with configurable tolerance.
    Works for any background color, not just magenta.
    """
    diff = np.abs(rgb.astype(np.int16) - bg_color.astype(np.int16))
    dist = np.sum(diff, axis=-1)  # L1 per pixel
    return dist > tolerance


def _per_frame_mirror_confidence(
    proj_frame: np.ndarray,
    refl_frame: np.ndarray,
    content_mask: np.ndarray,
) -> float:
    """Compute mirror-similarity confidence for a single frame pair.

    Compares proj_frame to horizontally-flipped refl_frame on content
    pixels only. Returns confidence 0.0-1.0.
    """
    refl_flipped = refl_frame[:, ::-1, :]

    content_count = int(np.sum(content_mask))
    if content_count < 4:
        return 0.0  # Not enough content to judge

    proj_vals = proj_frame[content_mask].astype(np.float32)
    refl_vals = refl_flipped[content_mask].astype(np.float32)

    mae = float(np.mean(np.abs(proj_vals - refl_vals)) / 255.0)

    if mae <= 0.02:
        return 0.98
    elif mae <= 0.05:
        return 0.90
    elif mae <= 0.10:
        return 0.80
    elif mae <= 0.18:
        return 0.60
    elif mae <= 0.30:
        return 0.40
    else:
        return 0.15


def detect_reflections(
    image: Image.Image,
    angles: int,
    anim_frames: List[int],
    threshold: float = 0.85
) -> Tuple[bool, float]:
    """Detect if sheet already contains reflection data.

    [FLOW:REFLECTION] Per-frame mirror analysis with background normalization.

    Detection method:
    1. Check if width geometry allows doubled interpretation
    2. Detect background color from corners (not hardcoded magenta)
    3. Split into individual frame cells
    4. Compare each (proj_frame, refl_frame) pair via horizontal mirror MAE
    5. Require majority of frames to have strong mirror confidence
    6. Fail-closed: uncertainty => (False, confidence) => caller generates

    Args:
        image: Sprite sheet PNG
        angles: Number of angle rows
        anim_frames: Frame counts per animation
        threshold: Confidence threshold (default 0.85, raised from 0.7)

    Returns:
        (has_reflections, confidence) where confidence is 0.0-1.0
    """
    w, h = image.size
    anim_sum = sum(anim_frames)

    if angles <= 0:
        return True, 1.0  # Single-angle: no reflections needed

    if anim_sum <= 0:
        return False, 0.0

    # --- Geometry check ---
    # With reflections: width = 2 * anim_sum * cell_size
    # Without: width = anim_sum * cell_size
    possible_cell_doubled = w / (2 * anim_sum)
    possible_cell_single = w / anim_sum

    can_be_doubled = (
        possible_cell_doubled == int(possible_cell_doubled)
        and possible_cell_doubled > 0
    )
    can_be_single = (
        possible_cell_single == int(possible_cell_single)
        and possible_cell_single > 0
    )

    if not can_be_doubled:
        # Width cannot be split into 2*anim_sum integer-width frames
        return False, 0.95

    # If width CAN be doubled, proceed to content analysis.
    # Note: can_be_doubled implies can_be_single (mathematical tautology),
    # so we ALWAYS need content evidence — geometry alone proves nothing.

    cell_w = int(possible_cell_doubled)
    row_h = h // angles if angles > 0 else h

    if row_h <= 0 or cell_w <= 0:
        return False, 0.5

    # --- Background normalization ---
    rgb = np.array(image.convert('RGB'))
    bg_color = _detect_background_color(rgb)

    # --- Per-frame mirror analysis ---
    half_w = w // 2
    per_frame_confs: List[float] = []

    for angle_idx in range(min(angles, h // max(row_h, 1))):
        y0 = angle_idx * row_h
        y1 = y0 + row_h

        col_offset = 0
        for frame_count in anim_frames:
            for frame_idx in range(frame_count):
                x0 = (col_offset + frame_idx) * cell_w
                x1 = x0 + cell_w

                # Bounds check
                if x1 > half_w or y1 > h:
                    continue

                proj_frame = rgb[y0:y1, x0:x1]
                refl_frame = rgb[y0:y1, x0 + half_w:x1 + half_w]

                # Content mask on the projection frame
                frame_mask = _make_content_mask(proj_frame, bg_color)

                conf = _per_frame_mirror_confidence(
                    proj_frame, refl_frame, frame_mask
                )
                per_frame_confs.append(conf)

            col_offset += frame_count

    if not per_frame_confs:
        # No frames could be extracted — fail closed
        return False, 0.1

    # --- Majority vote ---
    strong_threshold = 0.80
    strong_count = sum(1 for c in per_frame_confs if c >= strong_threshold)
    total = len(per_frame_confs)

    # Require strict majority (>50%) of frames to show strong mirror match
    majority_ratio = strong_count / total
    avg_conf = sum(per_frame_confs) / total

    # Aggregate confidence: weighted combination of majority and average
    aggregate = 0.6 * majority_ratio + 0.4 * avg_conf

    # Fail-closed: require BOTH high aggregate AND majority
    if aggregate >= threshold and majority_ratio > 0.5:
        return True, aggregate

    # Not enough evidence — fail closed (generate reflections)
    return False, aggregate


# =============================================================================
# Reflection Generation
# =============================================================================

def generate_reflections(
    image: Image.Image,
    angles: int,
    anim_frames: List[int],
    dim_factor: float = 0.5
) -> Image.Image:
    """Generate reflection half from projection-only sheet.

    [FLOW:REFLECTION] Creates a new image with doubled width containing
    both projection (original) and reflection (dimmed copy) halves.

    Args:
        image: Projection-only sprite sheet
        angles: Number of angle rows
        anim_frames: Frame counts per animation
        dim_factor: Brightness multiplier for reflections (default 0.5)

    Returns:
        New image with reflections appended (doubled width)
    """
    # Angles <= 0 are treated as single-angle (legacy encoding)
    if angles <= 0:
        return image.copy()

    w, h = image.size
    rgb = np.array(image.convert('RGB'))

    # Create doubled-width canvas
    new_width = w * 2
    result = np.zeros((h, new_width, 3), dtype=np.uint8)

    # Copy projection half (original)
    result[:, :w] = rgb

    # Create reflection half (dimmed)
    # Preserve magenta transparency (don't dim magenta pixels)
    refl = rgb.copy()

    # Create mask for non-magenta pixels
    is_magenta = (
        (rgb[:, :, 0] > 240) &
        (rgb[:, :, 1] < 15) &
        (rgb[:, :, 2] > 240)
    )

    # Dim non-magenta pixels
    for c in range(3):
        channel = refl[:, :, c].astype(np.float32)
        channel[~is_magenta] *= dim_factor
        refl[:, :, c] = np.clip(channel, 0, 255).astype(np.uint8)

    # Restore magenta for transparency
    refl[is_magenta] = [255, 0, 255]

    # Copy reflection half
    result[:, w:] = refl

    return Image.fromarray(result, 'RGB')


# =============================================================================
# Reflection Handler
# =============================================================================

class ReflectionHandler:
    """Coordinates reflection detection and generation.

    [FLOW:REFLECTION] Main entry point for reflection handling in the pipeline.
    Detects existing reflections and generates missing ones.
    """

    def __init__(self, dim_factor: float = 0.5, detection_threshold: float = 0.85):
        """Initialize handler.

        Args:
            dim_factor: Brightness multiplier for generated reflections
            detection_threshold: Confidence threshold for detection (0.85 fail-closed)
        """
        self.dim_factor = dim_factor
        self.detection_threshold = detection_threshold

    def process(
        self,
        image: Image.Image,
        angles: int,
        anim_frames: List[int]
    ) -> Tuple[Image.Image, bool]:
        """Process sheet for reflections.

        Args:
            image: Source sprite sheet
            angles: Number of angle rows
            anim_frames: Frame counts per animation

        Returns:
            (processed_image, was_generated) tuple
        """
        if angles <= 0:
            return image.copy(), False

        has_refl, confidence = detect_reflections(
            image, angles, anim_frames, self.detection_threshold
        )

        if has_refl:
            return image.copy(), False

        result = generate_reflections(
            image, angles, anim_frames, self.dim_factor
        )

        return result, True

    def validate(
        self,
        image: Image.Image,
        angles: int,
        anim_frames: List[int],
        cell_size: int = CELL_SIZE
    ) -> Tuple[bool, Dict[str, Any]]:
        """Validate image geometry for engine compatibility.

        Args:
            image: Sprite sheet
            angles: Number of angle rows
            anim_frames: Frame counts per animation
            cell_size: Expected cell size

        Returns:
            (is_valid, info_dict)
        """
        return validate_reflection_geometry(
            width=image.size[0],
            height=image.size[1],
            angles=angles,
            anim_frames=anim_frames,
            cell_size=cell_size
        )

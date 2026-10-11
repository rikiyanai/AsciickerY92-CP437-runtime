"""Normalize arbitrary sprite sheets into canonical frame packages.

Stage N0 of the pipeline: runs before Stage 2 (slicing) to convert raw sheets
into a manifest-driven package with explicit geometry.  Downstream stages use
the manifest for deterministic slicing instead of heuristic grid inference.

Algorithm:
  1. Extract sprites from source sheet (reuses sprite_extract.py).
  2. Cluster sprites into row bands by Y-overlap.
  3. Sort within rows by X coordinate -> columns.
  4. Determine uniform frame size (max width/height per cluster).
  5. Build frame_map with neutral IDs (dir_0..dir_n, anim_0).
  6. Save individual frames + recompose canonical sheet.
  7. Write manifest.json + diagnostics.

[FLOW:NORMALIZE] [PIPELINE:NORMALIZE] [DEPENDENCY:PIL] [DEPENDENCY:NUMPY]
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from scripts.pipeline.canonical_manifest import (
    CanonicalManifest,
    FrameRef,
    GridInfo,
    NormalizationInfo,
    SCHEMA_VERSION,
)
from scripts.pipeline.sprite_extract import (
    ExtractedSprite,
    extract_sprites,
)

logger = logging.getLogger(__name__)

# Staging root for normalized packages.
_STAGING_DIR = Path(__file__).resolve().parent / "staging" / "normalized"


@dataclass
class NormalizationResult:
    """Result of normalizing a sprite sheet."""
    manifest: CanonicalManifest
    output_dir: Path
    confidence: float
    rows: int
    cols: int


def _cluster_sprites_into_rows(
    sprites: List[ExtractedSprite],
) -> List[List[ExtractedSprite]]:
    """Cluster sprites into row bands by vertical overlap.

    Two sprites belong to the same row if their Y ranges overlap by at least
    50% of the shorter sprite's height.
    """
    if not sprites:
        return []

    # Sort by Y midpoint first.
    sorted_sprites = sorted(sprites, key=lambda s: s.bbox[1] + s.bbox[3] / 2)

    rows: List[List[ExtractedSprite]] = []
    current_row: List[ExtractedSprite] = [sorted_sprites[0]]
    # Track the row's Y band as union of all members.
    row_y_min = sorted_sprites[0].bbox[1]
    row_y_max = sorted_sprites[0].bbox[1] + sorted_sprites[0].bbox[3]

    for sprite in sorted_sprites[1:]:
        sy = sprite.bbox[1]
        sh = sprite.bbox[3]
        sy_max = sy + sh

        # Overlap between sprite and current row band.
        overlap_start = max(row_y_min, sy)
        overlap_end = min(row_y_max, sy_max)
        overlap = max(0, overlap_end - overlap_start)

        min_h = min(row_y_max - row_y_min, sh)
        # Same row if overlap >= 50% of the shorter height.
        if min_h > 0 and overlap / min_h >= 0.5:
            current_row.append(sprite)
            # Expand row band.
            row_y_min = min(row_y_min, sy)
            row_y_max = max(row_y_max, sy_max)
        else:
            rows.append(current_row)
            current_row = [sprite]
            row_y_min = sy
            row_y_max = sy_max

    rows.append(current_row)

    # Sort sprites within each row by X.
    for row in rows:
        row.sort(key=lambda s: s.bbox[0])

    return rows


def _compute_uniform_frame_size(
    rows: List[List[ExtractedSprite]],
) -> Tuple[int, int]:
    """Compute uniform frame size from detected sprites.

    Uses the maximum width and height across all sprites to ensure
    no content is clipped.
    """
    max_w = 0
    max_h = 0
    for row in rows:
        for sprite in row:
            max_w = max(max_w, sprite.bbox[2])
            max_h = max(max_h, sprite.bbox[3])
    return max_w, max_h


def _compute_confidence(
    rows: List[List[ExtractedSprite]],
    source_width: int,
    source_height: int,
) -> float:
    """Compute normalization confidence score (0.0-1.0).

    Factors:
    - Row consistency: do all rows have the same number of columns?
    - Size consistency: are sprite sizes similar within each row?
    - Coverage: do sprites cover a reasonable fraction of the sheet?
    """
    if not rows:
        return 0.0

    scores = []

    # Factor 1: Column count consistency across rows.
    col_counts = [len(row) for row in rows]
    if len(set(col_counts)) == 1:
        scores.append(1.0)  # All rows same width — high confidence.
    else:
        # Ratio of most common count to total rows.
        from collections import Counter
        most_common = Counter(col_counts).most_common(1)[0][1]
        scores.append(most_common / len(rows))

    # Factor 2: Size consistency (coefficient of variation of areas).
    areas = [s.bbox[2] * s.bbox[3] for row in rows for s in row]
    if areas:
        mean_area = sum(areas) / len(areas)
        if mean_area > 0:
            variance = sum((a - mean_area) ** 2 for a in areas) / len(areas)
            cv = (variance ** 0.5) / mean_area
            # CV < 0.1 = very consistent, CV > 0.5 = very inconsistent.
            scores.append(max(0.0, 1.0 - cv * 2))
        else:
            scores.append(0.0)

    # Factor 3: Coverage — total sprite area vs sheet area.
    total_sprite_area = sum(areas)
    sheet_area = source_width * source_height
    if sheet_area > 0:
        coverage = total_sprite_area / sheet_area
        # Good sheets typically have 20-80% coverage.
        if 0.15 <= coverage <= 0.85:
            scores.append(1.0)
        elif coverage < 0.15:
            scores.append(coverage / 0.15)
        else:
            scores.append(max(0.0, 1.0 - (coverage - 0.85) / 0.15))

    return sum(scores) / len(scores) if scores else 0.0


def _save_frame(
    sprite: ExtractedSprite,
    target_w: int,
    target_h: int,
    output_path: Path,
) -> None:
    """Save a single frame, centered within uniform dimensions."""
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    # Center the sprite within the target frame.
    offset_x = (target_w - sprite.bbox[2]) // 2
    offset_y = (target_h - sprite.bbox[3]) // 2
    canvas.paste(sprite.image, (offset_x, offset_y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def _compose_canonical_sheet(
    rows: List[List[ExtractedSprite]],
    frame_w: int,
    frame_h: int,
) -> Image.Image:
    """Compose a canonical sprite sheet from clustered frames.

    Arranges frames in a regular grid: rows = directions, cols = max columns.
    """
    n_rows = len(rows)
    n_cols = max(len(row) for row in rows)
    sheet = Image.new("RGBA", (n_cols * frame_w, n_rows * frame_h), (0, 0, 0, 0))

    for r, row in enumerate(rows):
        for c, sprite in enumerate(row):
            # Center sprite within its cell.
            offset_x = c * frame_w + (frame_w - sprite.bbox[2]) // 2
            offset_y = r * frame_h + (frame_h - sprite.bbox[3]) // 2
            sheet.paste(sprite.image, (offset_x, offset_y))

    return sheet


def normalize_sheet(
    source_path: Path,
    name: str,
    angles: int = 1,
    anim_frames: Optional[List[int]] = None,
    source_projs: int = 1,
    min_confidence: float = 0.5,
    output_dir: Optional[Path] = None,
    bg_color: Optional[Tuple[int, int, int]] = None,
    alpha_threshold: int = 25,
    color_tolerance: float = 30.0,
    min_sprite_size: int = 8,
) -> NormalizationResult:
    """Normalize a sprite sheet into a canonical frame package.

    This is the Stage N0 entry point.

    Args:
        source_path: Path to the source sprite sheet image.
        name: Asset name for the output package.
        angles: Expected number of angle rows. If 0 or 1, auto-detect from row count.
        anim_frames: Expected animation frame counts per sequence.
            If None, auto-detect from column count.
        source_projs: Number of projections (1 or 2).
        min_confidence: Minimum confidence to proceed. Below this, fails closed.
        output_dir: Output directory. Defaults to staging/normalized/<name>/.
        bg_color: Background color override for extraction.
        alpha_threshold: Alpha threshold for extraction.
        color_tolerance: Color distance tolerance for extraction.
        min_sprite_size: Minimum sprite size for extraction.

    Returns:
        NormalizationResult with manifest and output paths.

    Raises:
        ValueError: If normalization confidence is below min_confidence.
        FileNotFoundError: If source_path doesn't exist.

    [FLOW:NORMALIZE] [PIPELINE:NORMALIZE]
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Source not found: {source_path}")

    out = output_dir or (_STAGING_DIR / name)

    logger.info("Normalizing sheet: %s -> %s", source_path, out)

    # Step 1: Load and extract sprites.
    source = Image.open(source_path)
    sprites = extract_sprites(
        source,
        mode="bbox",
        alpha_threshold=alpha_threshold,
        bg_color=bg_color,
        color_tolerance=color_tolerance,
        min_size=min_sprite_size,
    )

    # Fallback: if extraction finds nothing or just huge blobs,
    # retry with color-based segmentation using corner BG color.
    if not sprites:
        corner_bg = source.convert("RGB").getpixel((0, 0))
        logger.info(
            "No sprites found with default mode. "
            "Retrying with color-based segmentation (bg=%s)",
            corner_bg,
        )
        sprites = extract_sprites(
            source,
            mode="bbox",
            bg_color=corner_bg,
            color_tolerance=color_tolerance,
            min_size=min_sprite_size,
            force_segmentation="color",
        )

    if not sprites:
        raise ValueError(
            f"No sprites extracted from {source_path}. "
            "Sheet may be empty or min_sprite_size too large."
        )

    logger.info("Extracted %d sprites from source", len(sprites))

    # Step 2: Cluster into rows.
    rows = _cluster_sprites_into_rows(sprites)
    logger.info("Clustered into %d rows: %s", len(rows), [len(r) for r in rows])

    # Step 3: Compute confidence.
    confidence = _compute_confidence(rows, source.width, source.height)
    logger.info("Normalization confidence: %.2f (threshold: %.2f)", confidence, min_confidence)

    if confidence < min_confidence:
        # Write diagnostics even on failure so human can inspect.
        diag_dir = out / "artifacts"
        diag_dir.mkdir(parents=True, exist_ok=True)
        _write_diagnostics(
            diag_dir / "normalize_diagnostics.json",
            name, confidence, rows, source.width, source.height,
            "FAILED: below confidence threshold",
        )
        raise ValueError(
            f"Normalization confidence {confidence:.2f} is below threshold "
            f"{min_confidence:.2f}. Review artifacts at: {diag_dir}"
        )

    # Step 4: Determine geometry.
    n_rows = len(rows)
    n_cols = max(len(row) for row in rows)
    frame_w, frame_h = _compute_uniform_frame_size(rows)

    # Auto-detect angles and anim_frames if not provided.
    detected_angles = angles if angles > 1 else n_rows
    if anim_frames is None:
        # Per-angle frame counts: each row can have different column counts.
        detected_anim_frames = [len(row) for row in rows]
    else:
        detected_anim_frames = list(anim_frames)

    # Validate frame count consistency.
    if len(detected_anim_frames) == detected_angles:
        expected_total = sum(detected_anim_frames) * source_projs
    else:
        expected_total = detected_angles * sum(detected_anim_frames) * source_projs

    actual_total = sum(len(row) for row in rows)
    if actual_total != expected_total:
        logger.warning(
            "Frame count mismatch: detected %d sprites but geometry expects %d "
            "(angles=%d, anim_frames=%s, projs=%d). Using detected structure.",
            actual_total, expected_total, detected_angles,
            detected_anim_frames, source_projs,
        )
        # Adjust: use actual per-row structure.
        detected_angles = n_rows
        detected_anim_frames = [len(row) for row in rows]

    # Step 5: Save individual frames + build frame_map.
    frame_map: List[FrameRef] = []
    frames_dir = out / "frames"

    for r_idx, row in enumerate(rows):
        dir_id = f"dir_{r_idx}"
        for c_idx, sprite in enumerate(row):
            anim_id = "anim_0"
            frame_file = f"frames/{dir_id}/{anim_id}/frame_{c_idx:03d}.png"
            _save_frame(sprite, frame_w, frame_h, out / frame_file)
            frame_map.append(FrameRef(
                row=r_idx,
                col=c_idx,
                direction=dir_id,
                animation=anim_id,
                frame_idx=c_idx,
                file=frame_file,
            ))

    # Step 6: Compose canonical sheet.
    canonical_sheet = _compose_canonical_sheet(rows, frame_w, frame_h)
    canonical_path = out / "spritesheet_canonical.png"
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_sheet.save(canonical_path)

    # Step 7: Save BG-normalized source.
    source_copy_path = out / "source.png"
    source.save(source_copy_path)

    # Step 8: Build normalization info.
    has_alpha = False
    if source.mode == "RGBA":
        alpha = np.array(source)[:, :, 3]
        has_alpha = bool(alpha.min() < 255)
    bg_model = "alpha" if has_alpha else "color_corner"

    normalization = NormalizationInfo(
        method="extractor_cluster",
        confidence=confidence,
        bg_model=bg_model,
        bg_color=list(bg_color) if bg_color else None,
        sprite_count=len(sprites),
        cluster_params={
            "alpha_threshold": alpha_threshold,
            "color_tolerance": color_tolerance,
            "min_sprite_size": min_sprite_size,
            "overlap_threshold": 0.5,
        },
    )

    # Step 9: Build and validate manifest.
    # source_projs in the manifest is always 1 because the frame_map
    # describes the physical layout of the sheet.  If the source has
    # baked-in projections (source_projs=2), those are already included
    # in the detected frame counts.  The pipeline handles projection
    # semantics downstream.
    manifest = CanonicalManifest(
        name=name,
        schema_version=SCHEMA_VERSION,
        grid=GridInfo(
            rows=n_rows,
            cols=n_cols,
            frame_width=frame_w,
            frame_height=frame_h,
        ),
        angles=detected_angles,
        anim_frames=detected_anim_frames,
        source_projs=1,
        frame_map=frame_map,
        normalization=normalization,
        source_path=str(source_path),
        canonical_sheet_path=str(canonical_path),
    )

    # Save manifest.
    manifest_path = out / "manifest.json"
    manifest.save(manifest_path)

    # Step 10: Write diagnostics + preview.
    diag_dir = out / "artifacts"
    diag_dir.mkdir(parents=True, exist_ok=True)
    _write_diagnostics(
        diag_dir / "normalize_diagnostics.json",
        name, confidence, rows, source.width, source.height,
        "OK",
    )
    _write_preview(diag_dir / "normalize_preview.png", source, rows)

    logger.info(
        "Normalization complete: %d rows x %d cols, frames=%dx%d, confidence=%.2f",
        n_rows, n_cols, frame_w, frame_h, confidence,
    )

    return NormalizationResult(
        manifest=manifest,
        output_dir=out,
        confidence=confidence,
        rows=n_rows,
        cols=n_cols,
    )


def _write_diagnostics(
    path: Path,
    name: str,
    confidence: float,
    rows: List[List[ExtractedSprite]],
    source_w: int,
    source_h: int,
    status: str,
) -> None:
    """Write normalization diagnostics JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    diag = {
        "name": name,
        "status": status,
        "confidence": round(confidence, 4),
        "source_dimensions": [source_w, source_h],
        "detected_rows": len(rows),
        "row_col_counts": [len(r) for r in rows],
        "total_sprites": sum(len(r) for r in rows),
        "sprite_sizes": [
            [s.bbox[2], s.bbox[3]]
            for row in rows for s in row
        ],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)


def _write_preview(
    path: Path,
    source: Image.Image,
    rows: List[List[ExtractedSprite]],
) -> None:
    """Write a preview image showing detected sprite bounding boxes."""
    from PIL import ImageDraw

    preview = source.copy().convert("RGBA")
    draw = ImageDraw.Draw(preview)

    colors = [
        (255, 0, 0, 180),
        (0, 255, 0, 180),
        (0, 0, 255, 180),
        (255, 255, 0, 180),
        (255, 0, 255, 180),
        (0, 255, 255, 180),
    ]

    for r_idx, row in enumerate(rows):
        color = colors[r_idx % len(colors)]
        for sprite in row:
            x, y, w, h = sprite.bbox
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path)

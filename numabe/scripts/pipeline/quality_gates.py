"""Quality gates for pipeline output verification.

[FLOW:QUALITY_GATE] Hard-fail checks that run after pipeline conversion.

Preflight gates (run_quality_gates, gate_class="preflight"):
  G1 Geometry  — frame grid matches manifest (image divisible by cols x rows)
  G2 Activity  — no empty or flash frames (fg_ratio above threshold)
  G3 SplitBleed — border leakage below threshold
  G4 Centering — centroid jitter bounded across frames
  G5 Reflection — mirror confidence consistent with source_projs policy
  G6 Human     — manual signoff (checked separately via conftest/ci_policy)

Output gates (run_output_quality_gates, gate_class="output"):
  G7 Occupancy  — XP visual layer has sufficient non-transparent cells
  G8 Coherence  — rendered XP correlates with source image (warn-only, optional)
  G9 Degenerate — XP output is not dominated by a single glyph or color
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image


@dataclass
class GateResult:
    """Result of a single quality gate check.

    SEMANTICS: ``passed`` means "metric did not breach the extreme-failure
    threshold."  It does NOT mean the output is visually correct.
    Visual quality requires human inspection (see No-Vision-No-PASS policy).
    """

    gate: str
    passed: bool  # True = threshold met (NOT visual quality approval)
    score: float = 0.0
    threshold: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """Human-readable verdict for artifact reports."""
        if self.details.get("status") == "skipped":
            return "SKIPPED"
        return "THRESHOLD_MET" if self.passed else "THRESHOLD_BREACHED"


# Caveat text included in every gate artifact to prevent misinterpretation.
_GATE_CAVEAT = (
    "Threshold compliance does NOT indicate visual quality. "
    "These gates detect catastrophic failures (empty output, degenerate patterns). "
    "Visual correctness requires human inspection or image-view verification."
)


@dataclass
class QualityReport:
    """Aggregate report from all quality gates."""

    gates: List[GateResult] = field(default_factory=list)
    all_passed: bool = False  # True = all thresholds met (NOT quality approval)
    artifact_path: Optional[str] = None


def check_geometry(
    image: Image.Image,
    angles: int,
    frames_list: List[int],
) -> GateResult:
    """G1: Verify image dimensions divide evenly into the expected grid.

    [FLOW:QUALITY_GATE] Checks width / cols and height / rows for remainder.
    """
    cols = sum(frames_list)
    rows = angles
    w, h = image.size
    w_rem = w % cols if cols > 0 else w
    h_rem = h % rows if rows > 0 else h
    passed = w_rem == 0 and h_rem == 0
    return GateResult(
        gate="G1_geometry",
        passed=passed,
        score=1.0 if passed else 0.0,
        threshold=1.0,
        details={
            "width": w,
            "height": h,
            "cols": cols,
            "rows": rows,
            "width_remainder": w_rem,
            "height_remainder": h_rem,
        },
    )


def check_activity(
    frames: List[Image.Image],
    min_fg_ratio: float = 0.005,
    max_empty_fraction: float = 0.1,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> GateResult:
    """G2: Detect empty or near-empty (flash) frames.

    [FLOW:QUALITY_GATE] Each frame must have fg_ratio above min_fg_ratio.
    Allows up to max_empty_fraction of total frames to be empty (e.g. idle padding).
    """
    from scripts.pipeline.slicer import analyze_frame_content

    empty_indices: List[int] = []
    ratios: List[float] = []
    for i, frame in enumerate(frames):
        analysis = analyze_frame_content(
            frame, bg_color=bg_color, bg_tolerance=bg_tolerance
        )
        ratio = analysis["fg_ratio"]
        ratios.append(ratio)
        if ratio < min_fg_ratio:
            empty_indices.append(i)

    n = len(frames)
    empty_frac = len(empty_indices) / n if n > 0 else 0.0
    passed = empty_frac <= max_empty_fraction
    return GateResult(
        gate="G2_activity",
        passed=passed,
        score=1.0 - empty_frac,
        threshold=1.0 - max_empty_fraction,
        details={
            "total_frames": n,
            "empty_frames": len(empty_indices),
            "empty_indices": empty_indices[:20],  # cap detail size
            "empty_fraction": round(empty_frac, 4),
            "min_fg_ratio": min_fg_ratio,
        },
    )


def check_split_bleed(
    frames: List[Image.Image],
    grid_cols: int,
    max_split_ratio: float = 0.15,
    max_bleed_ratio: float = 0.10,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> GateResult:
    """G3: Border leakage must be below threshold.

    [FLOW:QUALITY_GATE] Uses Track 3 diagnostics for split and bleed ratios.
    """
    from scripts.pipeline.slicer import compute_slice_diagnostics

    diag = compute_slice_diagnostics(
        frames,
        grid_cols=grid_cols,
        bg_color=bg_color,
        bg_tolerance=bg_tolerance,
    )
    split_ratio = diag["split_ratio"]
    bleed_ratio = diag["bleed_ratio"]
    passed = split_ratio <= max_split_ratio and bleed_ratio <= max_bleed_ratio
    score = 1.0 - max(split_ratio / max_split_ratio, bleed_ratio / max_bleed_ratio)
    return GateResult(
        gate="G3_split_bleed",
        passed=passed,
        score=max(0.0, score),
        threshold=0.0,
        details={
            "split_ratio": round(split_ratio, 4),
            "split_threshold": max_split_ratio,
            "bleed_ratio": round(bleed_ratio, 4),
            "bleed_threshold": max_bleed_ratio,
            "split_count": diag["split_count"],
            "bleed_pairs": len(diag["bleed_pairs"]),
        },
    )


def check_centering(
    frames: List[Image.Image],
    max_jitter_ratio: float = 0.3,
    bg_color: Optional[Tuple[int, int, int]] = None,
    bg_tolerance: float = 30.0,
) -> GateResult:
    """G4: Centroid jitter across frames must be bounded.

    [FLOW:QUALITY_GATE] Computes centroid for each frame, measures std-dev
    of x and y centroids normalized by frame dimensions. Jitter = max(std_x, std_y).
    """
    from scripts.pipeline.slicer import analyze_frame_content

    centroids_x: List[float] = []
    centroids_y: List[float] = []
    frame_w = frames[0].width if frames else 1
    frame_h = frames[0].height if frames else 1

    for frame in frames:
        analysis = analyze_frame_content(
            frame, bg_color=bg_color, bg_tolerance=bg_tolerance
        )
        c = analysis["centroid"]
        if c is not None:
            centroids_x.append(c[0] / frame_w)
            centroids_y.append(c[1] / frame_h)

    if len(centroids_x) < 2:
        return GateResult(
            gate="G4_centering",
            passed=True,
            score=1.0,
            threshold=max_jitter_ratio,
            details={"note": "insufficient frames with content for jitter check"},
        )

    mean_x = sum(centroids_x) / len(centroids_x)
    mean_y = sum(centroids_y) / len(centroids_y)
    var_x = sum((x - mean_x) ** 2 for x in centroids_x) / len(centroids_x)
    var_y = sum((y - mean_y) ** 2 for y in centroids_y) / len(centroids_y)
    std_x = math.sqrt(var_x)
    std_y = math.sqrt(var_y)
    jitter = max(std_x, std_y)
    passed = jitter <= max_jitter_ratio
    return GateResult(
        gate="G4_centering",
        passed=passed,
        score=max(0.0, 1.0 - jitter / max_jitter_ratio) if max_jitter_ratio > 0 else 1.0,
        threshold=max_jitter_ratio,
        details={
            "jitter": round(jitter, 4),
            "std_x": round(std_x, 4),
            "std_y": round(std_y, 4),
            "mean_centroid_x": round(mean_x, 4),
            "mean_centroid_y": round(mean_y, 4),
            "frames_with_content": len(centroids_x),
        },
    )


def check_reflection(
    image: Image.Image,
    angles: int,
    frames_list: List[int],
    source_projs: int,
    reflection_policy: str = "generate",
    mirror_threshold: float = 0.85,
) -> GateResult:
    """G5: Reflection policy consistency.

    [FLOW:QUALITY_GATE] If source_projs=2, mirror confidence must exceed threshold.
    If source_projs=1 and policy=generate, reflections should NOT be detected
    (otherwise the generator would be confused by false-positive detection).
    """
    from scripts.pipeline.reflection_handler import detect_reflections

    has_refl, confidence = detect_reflections(
        image, angles, frames_list, threshold=mirror_threshold
    )

    if source_projs == 2:
        # Source claims to have reflections — verify they exist
        passed = has_refl and confidence >= mirror_threshold
        detail_note = "source_projs=2: verifying pre-baked reflections detected"
    elif source_projs == 1 and reflection_policy == "generate":
        # Source has no reflections — should NOT detect them (false positive = bad)
        passed = not has_refl
        detail_note = "source_projs=1+generate: verifying no false-positive reflection"
    else:
        # Other policies (skip, etc.) — just report
        passed = True
        detail_note = f"source_projs={source_projs}, policy={reflection_policy}: informational"

    return GateResult(
        gate="G5_reflection",
        passed=passed,
        score=confidence,
        threshold=mirror_threshold,
        details={
            "has_reflections": has_refl,
            "confidence": round(confidence, 4),
            "source_projs": source_projs,
            "reflection_policy": reflection_policy,
            "note": detail_note,
        },
    )


def run_quality_gates(
    source_image: Image.Image,
    angles: int,
    frames_list: List[int],
    source_projs: int = 1,
    reflection_policy: str = "generate",
    frames: Optional[List[Image.Image]] = None,
    artifact_dir: Optional[Path] = None,
    asset_name: str = "unknown",
) -> QualityReport:
    """Run all quality gates (G1-G5) on a pipeline input/output.

    [FLOW:QUALITY_GATE] Orchestrates all gates and produces a report.

    Args:
        source_image: The source sprite sheet PNG.
        angles: Number of angle rows.
        frames_list: Animation frame counts per sequence.
        source_projs: 1 (no reflections) or 2 (pre-baked reflections).
        reflection_policy: 'generate', 'skip', or 'mirror'.
        frames: Pre-sliced frames (if None, will slice internally).
        artifact_dir: Where to write the JSON report artifact.
        asset_name: Name for the artifact file.

    Returns:
        QualityReport with per-gate results and overall pass/fail.
    """
    results: List[GateResult] = []

    # G1: Geometry
    g1 = check_geometry(source_image, angles, frames_list)
    results.append(g1)

    # Slice frames if not provided (needed for G2-G4)
    if frames is None and g1.passed:
        from scripts.pipeline.slicer import ImageSlicer

        slicer = ImageSlicer()
        frames = slicer.slice(source_image, angles, frames_list)

    if frames is not None and len(frames) > 0:
        # G2: Activity
        results.append(check_activity(frames))

        # G3: Split/Bleed
        results.append(
            check_split_bleed(frames, grid_cols=sum(frames_list))
        )

        # G4: Centering
        results.append(check_centering(frames))
    else:
        # Geometry failed — skip frame-dependent gates as failed
        for gate_name in ("G2_activity", "G3_split_bleed", "G4_centering"):
            results.append(
                GateResult(
                    gate=gate_name,
                    passed=False,
                    details={"note": "skipped: geometry gate failed or no frames"},
                )
            )

    # G5: Reflection
    results.append(
        check_reflection(
            source_image, angles, frames_list,
            source_projs=source_projs,
            reflection_policy=reflection_policy,
        )
    )

    report = QualityReport(
        gates=results,
        all_passed=all(g.passed for g in results),
    )

    # Write artifact JSON if dir specified
    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{asset_name}_quality_report.json"
        artifact_data = {
            "asset_name": asset_name,
            "gate_class": "preflight",
            "all_thresholds_met": report.all_passed,
            "caveat": _GATE_CAVEAT,
            "gates": [
                {
                    "gate": g.gate,
                    "verdict": g.verdict,
                    "score": g.score,
                    "threshold": g.threshold,
                    "details": g.details,
                }
                for g in report.gates
            ],
        }
        artifact_path.write_text(
            json.dumps(artifact_data, indent=2, default=str),
            encoding="utf-8",
        )
        report.artifact_path = str(artifact_path)

    return report


# ================================================================
# Output Quality Gates (G7, G8, G9) — inspect final .xp output
# ================================================================

# Per-run cache: keyed by str(xp_path) -> QualityReport.
# Prevents re-running gates when promotion scoring and final validation
# inspect the same XP file. Cleared at pipeline run() start.
_OUTPUT_GATE_CACHE: Dict[str, QualityReport] = {}


def clear_output_gate_cache() -> None:
    """Reset per-run output gate cache. Call at pipeline run() start."""
    _OUTPUT_GATE_CACHE.clear()


def _load_xp_visual_layer(xp_path: str) -> List[List[Tuple[int, Tuple[int, int, int], Tuple[int, int, int]]]]:
    """Load layer 2 (visual) from an XP file.

    Returns 2D list of (glyph, fg_rgb, bg_rgb) tuples, row-major.
    """
    from scripts.pipeline.xp_core import XPFile

    xp = XPFile(xp_path)
    if len(xp.layers) < 3:
        raise ValueError(f"XP file has {len(xp.layers)} layers (need >= 3): {xp_path}")
    return xp.layers[2].data


def check_output_occupancy(
    xp_path: str,
    min_occupancy: float = 0.05,
) -> GateResult:
    """G7: Output occupancy — verify the XP visual layer has real content.

    [FLOW:QUALITY_GATE] Inspects layer 2 for transparent/space cells.
    A sprite with < min_occupancy non-transparent, non-space cells is
    considered empty/garbled output.

    Primary signals:
    - Transparent cell count (bg == MAGIC_PINK AND (glyph==0 or glyph==32))
    - Space flood ratio (glyph == 32 regardless of bg)
    - Unique glyph diversity (unique glyphs / total cells)
    - Non-transparent occupancy (the gate metric)
    """
    MAGIC_PINK = (255, 0, 255)
    layer = _load_xp_visual_layer(xp_path)
    total_cells = 0
    transparent_cells = 0
    space_cells = 0
    occupied_cells = 0
    glyph_set: set[int] = set()

    for row in layer:
        for cell in row:
            total_cells += 1
            glyph, fg, bg = cell
            bg_rgb = (bg[0], bg[1], bg[2]) if isinstance(bg, (list, tuple)) else bg
            is_magenta_bg = (bg_rgb == MAGIC_PINK)
            is_space_glyph = (glyph == 0 or glyph == 32)

            if is_magenta_bg and is_space_glyph:
                transparent_cells += 1
            elif is_space_glyph:
                space_cells += 1
            else:
                occupied_cells += 1
            glyph_set.add(glyph)

    if total_cells == 0:
        return GateResult(
            gate="G7_output_occupancy",
            passed=False,
            score=0.0,
            threshold=min_occupancy,
            details={"error": "empty XP layer (0 cells)"},
        )

    occupancy = occupied_cells / total_cells
    space_ratio = space_cells / total_cells
    transparent_ratio = transparent_cells / total_cells
    glyph_diversity = len(glyph_set) / 256.0
    passed = occupancy >= min_occupancy

    return GateResult(
        gate="G7_output_occupancy",
        passed=passed,
        score=round(min(1.0, occupancy / max(min_occupancy, 0.001)), 4),
        threshold=min_occupancy,
        details={
            "total_cells": total_cells,
            "occupied_cells": occupied_cells,
            "transparent_cells": transparent_cells,
            "space_cells": space_cells,
            "occupancy": round(occupancy, 4),
            "space_ratio": round(space_ratio, 4),
            "transparent_ratio": round(transparent_ratio, 4),
            "unique_glyphs": len(glyph_set),
            "glyph_diversity": round(glyph_diversity, 4),
        },
    )


def check_output_degenerate(
    xp_path: str,
    max_same_glyph: float = 0.90,
    max_single_color: float = 0.95,
) -> GateResult:
    """G9: Degenerate detection — catch uniform/monotone output.

    [FLOW:QUALITY_GATE] An output where 90%+ of non-transparent cells
    share the same glyph, or 95%+ share the same fg color, indicates
    the processor collapsed the input into noise.

    Metrics:
    - Most-frequent glyph ratio (excluding transparent cells)
    - Most-frequent fg color ratio (excluding transparent cells)
    - Unique 2x2 block pattern count (sampled at 25% for performance)
    """
    MAGIC_PINK = (255, 0, 255)
    layer = _load_xp_visual_layer(xp_path)
    glyph_counts: Counter[int] = Counter()
    fg_counts: Counter[Tuple[int, int, int]] = Counter()
    total_active = 0

    for row in layer:
        for cell in row:
            glyph, fg, bg = cell
            bg_rgb = tuple(bg) if isinstance(bg, (list, tuple)) else bg
            if bg_rgb == MAGIC_PINK and glyph in (0, 32):
                continue
            total_active += 1
            glyph_counts[glyph] += 1
            fg_rgb = tuple(fg) if isinstance(fg, (list, tuple)) else fg
            fg_counts[fg_rgb] += 1

    if total_active == 0:
        return GateResult(
            gate="G9_output_degenerate",
            passed=False,
            score=0.0,
            threshold=0.0,
            details={"error": "no active cells in output"},
        )

    top_glyph, top_glyph_count = glyph_counts.most_common(1)[0]
    glyph_ratio = top_glyph_count / total_active

    top_fg, top_fg_count = fg_counts.most_common(1)[0]
    fg_ratio = top_fg_count / total_active

    # 2x2 block diversity (sample 25% of positions)
    rows = len(layer)
    cols = len(layer[0]) if rows > 0 else 0
    block_patterns: set[Tuple[int, ...]] = set()
    step = max(1, 4)  # sample every 4th position = ~25%
    for r in range(0, rows - 1, step):
        for c in range(0, cols - 1, step):
            block = (
                layer[r][c][0], layer[r][c + 1][0],
                layer[r + 1][c][0], layer[r + 1][c + 1][0],
            )
            block_patterns.add(block)

    glyph_fail = glyph_ratio > max_same_glyph
    color_fail = fg_ratio > max_single_color
    passed = not glyph_fail and not color_fail

    # Score: inverse of worst metric (higher is better)
    worst_ratio = max(glyph_ratio / max_same_glyph, fg_ratio / max_single_color)
    score = max(0.0, 1.0 - (worst_ratio - 1.0)) if worst_ratio > 1.0 else 1.0

    return GateResult(
        gate="G9_output_degenerate",
        passed=passed,
        score=round(score, 4),
        threshold=0.0,
        details={
            "total_active_cells": total_active,
            "top_glyph": top_glyph,
            "top_glyph_count": top_glyph_count,
            "glyph_ratio": round(glyph_ratio, 4),
            "glyph_threshold": max_same_glyph,
            "top_fg_color": list(top_fg),
            "top_fg_count": top_fg_count,
            "fg_ratio": round(fg_ratio, 4),
            "fg_threshold": max_single_color,
            "unique_2x2_blocks": len(block_patterns),
            "unique_glyphs": len(glyph_counts),
            "unique_fg_colors": len(fg_counts),
        },
    )


def check_output_coherence(
    xp_path: str,
    source_image: Image.Image,
    min_similarity: float = 0.20,
    artifact_dir: Optional[Path] = None,
    asset_name: str = "unknown",
) -> GateResult:
    """G8: Output coherence — compare rendered XP against source image.

    [FLOW:QUALITY_GATE] Renders the XP visual layer to PNG using the
    CP437 font atlas, then computes block-based luminance correlation
    with the source image.

    This gate is WARN-ONLY (never hard-fails). Thresholds are uncalibrated.
    When the font atlas is unavailable, returns a skipped result.

    Saves a contact-sheet PNG (source | rendered) to artifact_dir if provided.
    """
    from scripts.pipeline._render_core import find_font_atlas, load_font_atlas, render_xp_layer_to_png

    atlas_path = find_font_atlas()
    if atlas_path is None:
        return GateResult(
            gate="G8_output_coherence",
            passed=False,
            score=0.0,
            threshold=min_similarity,
            details={
                "status": "skipped",
                "reason": "font atlas unavailable",
            },
        )

    layer = _load_xp_visual_layer(xp_path)
    glyphs = load_font_atlas(atlas_path)
    rendered = render_xp_layer_to_png(layer, glyphs)

    # Downscale source to rendered size for comparison
    src_rgb = source_image.convert("RGB")
    if src_rgb.size != rendered.size:
        src_rgb = src_rgb.resize(rendered.size, Image.LANCZOS)
    rendered_rgb = rendered.convert("RGB")

    # Block-based luminance correlation
    src_arr = np.array(src_rgb, dtype=np.float32)
    rnd_arr = np.array(rendered_rgb, dtype=np.float32)
    src_lum = 0.299 * src_arr[:, :, 0] + 0.587 * src_arr[:, :, 1] + 0.114 * src_arr[:, :, 2]
    rnd_lum = 0.299 * rnd_arr[:, :, 0] + 0.587 * rnd_arr[:, :, 1] + 0.114 * rnd_arr[:, :, 2]

    # Pearson correlation on luminance
    src_flat = src_lum.flatten()
    rnd_flat = rnd_lum.flatten()
    src_mean = np.mean(src_flat)
    rnd_mean = np.mean(rnd_flat)
    src_centered = src_flat - src_mean
    rnd_centered = rnd_flat - rnd_mean
    numerator = float(np.sum(src_centered * rnd_centered))
    denominator = float(
        np.sqrt(np.sum(src_centered ** 2)) * np.sqrt(np.sum(rnd_centered ** 2))
    )
    correlation = numerator / denominator if denominator > 0 else 0.0

    contact_sheet_path = None
    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        # Build side-by-side contact sheet: source | rendered
        h = max(src_rgb.height, rendered_rgb.height)
        contact = Image.new("RGB", (src_rgb.width + rendered_rgb.width + 4, h), (128, 128, 128))
        contact.paste(src_rgb, (0, 0))
        contact.paste(rendered_rgb, (src_rgb.width + 4, 0))
        contact_sheet_path = str(artifact_dir / f"{asset_name}_coherence_contact.png")
        contact.save(contact_sheet_path)

    passed = correlation >= min_similarity

    return GateResult(
        gate="G8_output_coherence",
        passed=passed,
        score=round(max(0.0, correlation), 4),
        threshold=min_similarity,
        details={
            "luminance_correlation": round(correlation, 4),
            "source_size": list(source_image.size),
            "rendered_size": list(rendered.size),
            "contact_sheet_path": contact_sheet_path,
        },
    )


def run_output_quality_gates(
    xp_path: str,
    source_image: Optional[Image.Image] = None,
    artifact_dir: Optional[Path] = None,
    asset_name: str = "unknown",
    raise_on_fail: bool = True,
) -> QualityReport:
    """Run output quality gates (G7, G8, G9) on a final .xp file.

    [FLOW:QUALITY_GATE] Two gate classes exist:
    - Preflight (run_quality_gates): gate_class="preflight", inspects source PNG
    - Output (this function): gate_class="output", inspects final XP

    Args:
        xp_path: Path to the .xp file to inspect.
        source_image: Optional source PNG for G8 coherence check.
        artifact_dir: Where to write JSON report and contact sheets.
        asset_name: Name prefix for artifact files.
        raise_on_fail: If True, raises ValueError on G7/G9 failure (pipeline mode).
            If False, returns report with low scores (scoring mode for Track 3).

    Returns:
        QualityReport with gate_class="output" in artifact.
    """
    xp_key = str(xp_path)

    # Per-run cache: reuse gate results (avoids re-running G7/G8/G9)
    # but ALWAYS check raise_on_fail — cached report may have been
    # created in scoring mode (raise_on_fail=False).
    cached = _OUTPUT_GATE_CACHE.get(xp_key)
    if cached is not None:
        if raise_on_fail and not cached.all_passed:
            _WARN_ONLY = {"G8_output_coherence"}
            hard_gates = [
                g for g in cached.gates
                if g.details.get("status") != "skipped" and g.gate not in _WARN_ONLY
            ]
            failed_gates = [g.gate for g in hard_gates if not g.passed]
            if failed_gates:
                raise ValueError(
                    f"Output quality gate(s) failed: {', '.join(failed_gates)}. "
                    f"XP file: {xp_path}"
                )
        return cached

    results: List[GateResult] = []

    # G7: Output Occupancy (hard-fail capable)
    g7 = check_output_occupancy(xp_key)
    results.append(g7)

    # G9: Degenerate Detection (hard-fail capable)
    g9 = check_output_degenerate(xp_key)
    results.append(g9)

    # G8: Output Coherence (optional, warn-only)
    if source_image is not None:
        g8 = check_output_coherence(
            xp_key, source_image,
            artifact_dir=artifact_dir,
            asset_name=asset_name,
        )
        results.append(g8)

    # G8 is warn-only (coherence); only G7 (occupancy) and G9 (degenerate) are hard gates.
    # Skipped gates (e.g. G8 with no font atlas) are also excluded.
    _WARN_ONLY_GATES = {"G8_output_coherence"}
    hard_gates = [
        g for g in results
        if g.details.get("status") != "skipped" and g.gate not in _WARN_ONLY_GATES
    ]
    all_passed = all(g.passed for g in hard_gates) if hard_gates else False

    report = QualityReport(
        gates=results,
        all_passed=all_passed,
    )

    # Write artifact JSON with gate_class="output"
    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{asset_name}_output_quality_report.json"
        artifact_data = {
            "asset_name": asset_name,
            "gate_class": "output",
            "all_thresholds_met": report.all_passed,
            "caveat": _GATE_CAVEAT,
            "gates": [
                {
                    "gate": g.gate,
                    "verdict": g.verdict,
                    "score": g.score,
                    "threshold": g.threshold,
                    "details": g.details,
                }
                for g in report.gates
            ],
        }
        artifact_path.write_text(
            json.dumps(artifact_data, indent=2, default=str),
            encoding="utf-8",
        )
        report.artifact_path = str(artifact_path)

    # Cache before potential raise
    _OUTPUT_GATE_CACHE[xp_key] = report

    # Hard-fail mode: raise on G7/G9 failure
    if raise_on_fail and not all_passed:
        failed_gates = [g.gate for g in hard_gates if not g.passed]
        raise ValueError(
            f"Output quality gate(s) failed: {', '.join(failed_gates)}. "
            f"XP file: {xp_path}"
        )

    return report

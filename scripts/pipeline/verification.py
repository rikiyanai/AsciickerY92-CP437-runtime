"""
verification.py -- 6-stage verification protocol for the asset pipeline.

ARCHITECTURE:
    Post-pipeline verification that checks the integrity and correctness of
    generated assets before they are committed. Each stage validates a different
    aspect of the pipeline output:

    Stage A: Structural Fidelity  -- all expected frame files exist
    Stage B: Identity Lock         -- visual identity preserved (stub)
    Stage C: Grid Compliance       -- frame dimensions are cell-aligned
    Stage D: Palette Hygiene       -- no near-magenta content pixels
    Stage E: Reflection Policy     -- sheet geometry matches engine contract
    Stage F: XP Smoke Test         -- XP file is readable and consistent

KEY EXPORTS:
    - VerificationResult: Frozen dataclass for stage results
    - verify_structural_fidelity: Stage A
    - verify_identity_lock: Stage B (stub)
    - verify_grid_compliance: Stage C
    - verify_palette_hygiene: Stage D
    - verify_reflection_policy: Stage E
    - verify_xp_smoke_test: Stage F
    - run_verification: Orchestrator for running selected stages

PIPELINE CONTEXT:
    [FLOW:VERIFY] Runs after ASSEMBLE, before final output is committed.
    See docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for the archived specification.

REUSE:
    - discover_frames(), validate_frame_set() from reformatter.py
    - validate_reflection_geometry() from reflection_handler.py
    - read_xp_file(), diagnose_projs_mismatch(), diagnose_transparency_threshold()
      from diagnose_xp.py
"""

import io
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationResult:
    """Result of a single verification stage.

    Frozen for immutability and hashability. Uses tuples instead of lists
    so instances can be stored in sets or used as dict keys.

    Attributes:
        stage: Single letter identifier ("A" through "F").
        stage_name: Human-readable stage name.
        passed: Whether the stage passed verification.
        errors: Tuple of error messages (empty if passed).
        warnings: Tuple of warning messages (non-fatal).
    """

    stage: str
    stage_name: str
    passed: bool
    errors: tuple = ()
    warnings: tuple = ()


# =============================================================================
# Stage A: Structural Fidelity
# =============================================================================


def verify_structural_fidelity(
    frame_dir: Path,
    angles: int,
    frames: List[int],
) -> VerificationResult:
    """Stage A: Check that all expected frame files exist.

    Reuses reformatter.discover_frames() and reformatter.validate_frame_set()
    to avoid duplicating frame discovery logic.

    Args:
        frame_dir: Directory containing frame PNGs (f_{angle}_{frame}.png).
        angles: Expected number of viewing angles.
        frames: Expected frame counts per animation.

    Returns:
        VerificationResult with stage="A".
    """
    from .reformatter import discover_frames, validate_frame_set

    try:
        frame_map = discover_frames(frame_dir)
    except (FileNotFoundError, ValueError) as e:
        return VerificationResult(
            stage="A",
            stage_name="Structural Fidelity",
            passed=False,
            errors=(str(e),),
        )

    errors = validate_frame_set(frame_map, angles, frames)
    if errors:
        return VerificationResult(
            stage="A",
            stage_name="Structural Fidelity",
            passed=False,
            errors=tuple(errors),
        )

    return VerificationResult(
        stage="A",
        stage_name="Structural Fidelity",
        passed=True,
    )


# =============================================================================
# Stage B: Identity Lock
# =============================================================================


def verify_identity_lock(frame_dir: Path) -> VerificationResult:
    """Stage B: Identity lock verification (stub).

    This stage is intended to verify that generated frames maintain the visual
    identity of the source asset. Currently returns pass with a warning since
    automated identity comparison requires perceptual hashing or AI-based
    comparison that is not yet implemented.

    Args:
        frame_dir: Directory containing frame PNGs (unused in stub).

    Returns:
        VerificationResult with stage="B", always passes with warning.
    """
    return VerificationResult(
        stage="B",
        stage_name="Identity Lock",
        passed=True,
        warnings=("Identity lock verification requires manual review",),
    )


# =============================================================================
# Stage C: Grid Compliance
# =============================================================================


def verify_grid_compliance(
    frame_dir: Path,
    angles: int,
    frames: List[int],
    cell_size: int = 12,
    normalization: bool = False,
) -> VerificationResult:
    """Stage C: Check frame dimensions are grid-aligned (divisible by cell_size).

    The Asciicker engine requires all frame dimensions to be exact multiples
    of the cell size (12px by default). Misaligned frames cause rendering
    artifacts or crash the C++ loader.

    When normalization=True, misalignment is a warning (the reformatter will
    fix it). When normalization=False, misalignment is an error.

    Args:
        frame_dir: Directory containing frame PNGs.
        angles: Expected number of viewing angles.
        frames: Expected frame counts per animation.
        cell_size: Cell size in pixels (default 12).
        normalization: Whether normalization is enabled downstream.

    Returns:
        VerificationResult with stage="C".
    """
    from .reformatter import discover_frames

    try:
        frame_map = discover_frames(frame_dir)
    except (FileNotFoundError, ValueError) as e:
        return VerificationResult(
            stage="C",
            stage_name="Grid Compliance",
            passed=False,
            errors=(str(e),),
        )

    misaligned = []
    for key, path in sorted(frame_map.items()):
        img = Image.open(path)
        w, h = img.size
        if w % cell_size != 0 or h % cell_size != 0:
            misaligned.append(
                f"f_{key[0]}_{key[1]}.png: {w}x{h} not aligned to {cell_size}px grid"
            )

    if misaligned:
        if normalization:
            return VerificationResult(
                stage="C",
                stage_name="Grid Compliance",
                passed=True,
                warnings=tuple(misaligned),
            )
        return VerificationResult(
            stage="C",
            stage_name="Grid Compliance",
            passed=False,
            errors=tuple(misaligned),
        )

    return VerificationResult(
        stage="C",
        stage_name="Grid Compliance",
        passed=True,
    )


# =============================================================================
# Stage D: Palette Hygiene
# =============================================================================


def verify_palette_hygiene(
    frame_dir: Path,
    magenta_l1_threshold: int = 30,
) -> VerificationResult:
    """Stage D: Check for near-magenta content pixels that could cause artifacts.

    Uses numpy L1 distance from exact magenta (255, 0, 255). Threshold=30
    per NANOBANANA_PROMPT_PACK.md spec (AD-8). This is intentionally LOOSER
    than snap_magenta's correction threshold of 15 -- this stage detects
    potential problems, while snap_magenta corrects them.

    For RGBA images: only checks opaque pixels (alpha >= 128). Transparent
    pixels are expected to be converted to magenta by the pipeline.

    Exact magenta (255, 0, 255) is NOT flagged -- it IS the intended
    transparency key color.

    Args:
        frame_dir: Directory containing frame PNGs.
        magenta_l1_threshold: L1 distance threshold for near-magenta detection.

    Returns:
        VerificationResult with stage="D".
    """
    from .reformatter import discover_frames

    try:
        frame_map = discover_frames(frame_dir)
    except (FileNotFoundError, ValueError) as e:
        return VerificationResult(
            stage="D",
            stage_name="Palette Hygiene",
            passed=False,
            errors=(str(e),),
        )

    magenta = np.array([255, 0, 255], dtype=np.int16)
    flagged = []

    for key, path in sorted(frame_map.items()):
        img = Image.open(path)
        arr = np.array(img)

        # Determine opacity mask
        if img.mode == "RGBA":
            alpha = arr[:, :, 3]
            opaque_mask = alpha >= 128
            rgb = arr[:, :, :3]
        else:
            opaque_mask = np.ones(arr.shape[:2], dtype=bool)
            rgb = arr[:, :, :3] if arr.ndim == 3 else arr

        if not np.any(opaque_mask):
            continue

        # Compute L1 distance from magenta for all pixels
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)

        distance = np.abs(r - 255) + np.abs(g - 0) + np.abs(b - 255)

        # Exact magenta has distance 0 and should NOT be flagged
        exact_magenta = (
            (rgb[:, :, 0] == 255) & (rgb[:, :, 1] == 0) & (rgb[:, :, 2] == 255)
        )

        # Near-magenta: 0 < L1 < threshold, opaque, not exact magenta
        near_magenta = (
            (distance < magenta_l1_threshold)
            & (distance > 0)
            & opaque_mask
            & ~exact_magenta
        )

        count = int(np.sum(near_magenta))
        if count > 0:
            flagged.append(
                f"f_{key[0]}_{key[1]}.png: {count} near-magenta content pixels "
                f"(L1<{magenta_l1_threshold})"
            )

    if flagged:
        return VerificationResult(
            stage="D",
            stage_name="Palette Hygiene",
            passed=False,
            errors=tuple(flagged),
        )

    return VerificationResult(
        stage="D",
        stage_name="Palette Hygiene",
        passed=True,
    )


# =============================================================================
# Stage E: Reflection Policy
# =============================================================================


def verify_reflection_policy(
    sheet_path: Path,
    angles: int,
    anim_frames: List[int],
    cell_size: int = 12,
) -> VerificationResult:
    """Stage E: Validate reflection geometry of assembled sheet.

    Reuses reflection_handler.validate_reflection_geometry() to check that
    the sheet width/height satisfy the engine contract:
        width = projs * sum(anim_frames) * cell_size (integer frame width)
        height = angles * cell_size (integer frame height)

    Args:
        sheet_path: Path to assembled sprite sheet PNG.
        angles: Number of viewing angles.
        anim_frames: Frame counts per animation.
        cell_size: Expected cell size in pixels.

    Returns:
        VerificationResult with stage="E".
    """
    from .reflection_handler import validate_reflection_geometry

    if not sheet_path.exists():
        return VerificationResult(
            stage="E",
            stage_name="Reflection Policy",
            passed=False,
            errors=(f"Sheet not found: {sheet_path}",),
        )

    img = Image.open(sheet_path)
    is_valid, info = validate_reflection_geometry(
        img.width, img.height, angles, anim_frames, cell_size,
    )

    if not is_valid:
        issues = info.get("issues", [])
        return VerificationResult(
            stage="E",
            stage_name="Reflection Policy",
            passed=False,
            errors=tuple(issues),
        )

    return VerificationResult(
        stage="E",
        stage_name="Reflection Policy",
        passed=True,
    )


# =============================================================================
# Stage F: XP Smoke Test
# =============================================================================


def verify_xp_smoke_test(xp_path: Path) -> VerificationResult:
    """Stage F: Read XP file and run diagnostic checks.

    Goes beyond just reading the file: calls diagnose_projs_mismatch() and
    diagnose_transparency_threshold() from diagnose_xp.py. If either returns
    True (indicating a problem), the stage fails.

    Suppresses stdout from diagnose_xp.py which prints verbose diagnostics.

    Args:
        xp_path: Path to .xp file to verify.

    Returns:
        VerificationResult with stage="F".
    """
    from .diagnose_xp import (
        diagnose_projs_mismatch,
        diagnose_transparency_threshold,
        read_xp_file,
    )

    if not xp_path.exists():
        return VerificationResult(
            stage="F",
            stage_name="XP Smoke Test",
            passed=False,
            errors=(f"XP file not found: {xp_path}",),
        )

    try:
        # Suppress diagnose_xp print output by redirecting stdout
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            layers = read_xp_file(xp_path)
            projs_issue = diagnose_projs_mismatch(layers)
            threshold_issue = diagnose_transparency_threshold(layers)
        finally:
            sys.stdout = old_stdout
    except Exception as e:
        return VerificationResult(
            stage="F",
            stage_name="XP Smoke Test",
            passed=False,
            errors=(f"Failed to read XP: {e}",),
        )

    errors = []
    if projs_issue:
        errors.append("Projs mismatch detected in XP file")
    if threshold_issue:
        errors.append("Transparency threshold artifacts detected in XP file")

    if errors:
        return VerificationResult(
            stage="F",
            stage_name="XP Smoke Test",
            passed=False,
            errors=tuple(errors),
        )

    return VerificationResult(
        stage="F",
        stage_name="XP Smoke Test",
        passed=True,
    )


# =============================================================================
# Orchestrator
# =============================================================================


def run_verification(
    stages: Set[str],
    frame_dir: Optional[Path] = None,
    angles: int = 8,
    frames: Optional[List[int]] = None,
    sheet_path: Optional[Path] = None,
    xp_path: Optional[Path] = None,
    cell_size: int = 12,
    magenta_l1_threshold: int = 30,
    normalization: bool = False,
) -> List[VerificationResult]:
    """Run selected verification stages and return results.

    Only stages whose prerequisites are met will be run. For example, Stage A
    requires frame_dir, Stage E requires sheet_path, Stage F requires xp_path.

    Args:
        stages: Set of stage letters to run (e.g., {"A", "C", "D"}).
        frame_dir: Directory of frame PNGs (for stages A, B, C, D).
        angles: Number of viewing angles.
        frames: Frame counts per animation. Defaults to [4].
        sheet_path: Path to assembled sheet (for stage E).
        xp_path: Path to XP file (for stage F).
        cell_size: Cell size in pixels (for stages C, E).
        magenta_l1_threshold: L1 threshold for stage D.
        normalization: Whether normalization is enabled (for stage C).

    Returns:
        List of VerificationResult, one per requested stage (in A-F order).
    """
    if frames is None:
        frames = [4]

    results: List[VerificationResult] = []

    if "A" in stages and frame_dir is not None:
        results.append(verify_structural_fidelity(frame_dir, angles, frames))

    if "B" in stages and frame_dir is not None:
        results.append(verify_identity_lock(frame_dir))

    if "C" in stages and frame_dir is not None:
        results.append(
            verify_grid_compliance(
                frame_dir, angles, frames, cell_size, normalization,
            )
        )

    if "D" in stages and frame_dir is not None:
        results.append(verify_palette_hygiene(frame_dir, magenta_l1_threshold))

    if "E" in stages and sheet_path is not None:
        results.append(
            verify_reflection_policy(sheet_path, angles, frames, cell_size)
        )

    if "F" in stages and xp_path is not None:
        results.append(verify_xp_smoke_test(xp_path))

    return results

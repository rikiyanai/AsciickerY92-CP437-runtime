"""
nanobanana_batch.py -- Core batch runner for AI sprite frame generation.

ARCHITECTURE:
    This is the CORE ORCHESTRATOR that ties together the prompt pack, AI
    provider, color correction, reformatter, verification, and XP pipeline
    into a single batch-generation workflow.

    The batch runner processes a guidance manifest + prompt pack pair,
    generating one AI image per expected frame, applying color corrections,
    and optionally reformatting into a sprite sheet and emitting XP output.

KEY EXPORTS:
    - FrameRecord: Frozen dataclass for per-frame generation results
    - RunReport: Dataclass for complete batch run results
    - resolve_prompt: Merge base + style + override + template variables
    - resolve_seed: Priority-based seed resolution (CLI > offset > default)
    - run_batch: Top-level batch orchestrator

PIPELINE CONTEXT:
    [FLOW:BATCH] Consumes guidance_manifest + prompt_pack, produces stylized
    frames + optional reformatted sheet + optional .xp output.
    See docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for the archived specification.

REUSE:
    - GuidanceManifest, load_manifest from guidance_manifest.py
    - PromptPack, FrameOverride, load_prompt_pack from prompt_pack.py
    - FrameRequest, get_provider from ai_provider.py
    - snap_to_magenta from color_correction.py
    - run_reformatter from reformatter.py
    - run_verification from verification.py
    - AssetDef from schemas.py
    - AssetPipeline from pipeline.py
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

import numpy as np
from PIL import Image

from .ai_provider import FrameRequest, get_provider
from .guidance_manifest import GuidanceManifest, load_manifest
from .prompt_pack import PromptPack, load_prompt_pack

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameRecord:
    """Immutable record of a single generated frame.

    Attributes:
        angle: Viewing angle index for this frame.
        frame: Animation frame index within the angle.
        prompt: The fully resolved prompt that was sent to the provider.
        seed_used: The seed value used for generation.
        output_path: Path where the stylized PNG was saved.
        elapsed_ms: Wall-clock time for this frame's generation in milliseconds.
    """

    angle: int
    frame: int
    prompt: str
    seed_used: int
    output_path: Path
    elapsed_ms: int


@dataclass
class RunReport:
    """Complete results of a batch generation run.

    Attributes:
        manifest_path: Path to the guidance manifest used.
        prompt_pack_path: Path to the prompt pack used.
        provider: Name of the AI provider used.
        total_frames: Number of frames expected.
        completed_frames: Number of frames successfully generated.
        failed_frames: Number of frames that failed generation.
        frame_records: Per-frame generation records.
        verification_results: Results from post-generation verification stages.
        elapsed_total_ms: Total wall-clock time for the entire batch in ms.
        reformat_result: Result from the reformatter (if do_reformat=True).
        xp_output_path: Path to the emitted .xp file (if emit_xp=True).
        errors: List of error messages encountered during the run.
    """

    manifest_path: Path
    prompt_pack_path: Path
    provider: str
    total_frames: int
    completed_frames: int
    failed_frames: int
    frame_records: List[FrameRecord] = field(default_factory=list)
    verification_results: list = field(default_factory=list)
    elapsed_total_ms: int = 0
    reformat_result: object = None
    xp_output_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt + seed resolution
# ---------------------------------------------------------------------------


def resolve_prompt(
    pack: PromptPack,
    angle: int,
    frame: int,
    manifest: Optional[GuidanceManifest] = None,
) -> str:
    """Merge base + style + per-frame override + template variable substitution.

    Resolution order:
    1. Start with pack.base_prompt
    2. Append pack.style_prompt if non-empty (space-separated)
    3. Find matching FrameOverride (by angle AND frame)
    4. If found, append override.prompt_suffix
    5. Replace {angle}, {frame}, {name} template variables

    Args:
        pack: PromptPack defining generation parameters.
        angle: Current viewing angle index.
        frame: Current animation frame index.
        manifest: Optional GuidanceManifest for {name} substitution.

    Returns:
        Fully resolved prompt string.
    """
    parts = [pack.base_prompt]

    if pack.style_prompt:
        parts.append(pack.style_prompt)

    # Find matching per-frame override
    for override in pack.frame_overrides:
        if override.angle == angle and override.frame == frame:
            if override.prompt_suffix:
                parts.append(override.prompt_suffix)
            break

    merged = " ".join(parts)

    # Template variable substitution
    name = manifest.name if manifest is not None else ""
    merged = merged.replace("{angle}", str(angle))
    merged = merged.replace("{frame}", str(frame))
    merged = merged.replace("{name}", name)

    return merged


def resolve_seed(
    pack: PromptPack,
    angle: int,
    frame: int,
    cli_seed: Optional[int] = None,
) -> int:
    """Resolve seed with priority: CLI > per-frame offset > pack default.

    Args:
        pack: PromptPack with default_seed and frame_overrides.
        angle: Current viewing angle index.
        frame: Current animation frame index.
        cli_seed: Optional CLI-provided seed (highest priority).

    Returns:
        Resolved seed value.
    """
    if cli_seed is not None:
        return cli_seed

    # Check for per-frame seed offset
    for override in pack.frame_overrides:
        if override.angle == angle and override.frame == frame:
            if override.seed_offset != 0:
                return pack.default_seed + override.seed_offset
            break

    return pack.default_seed


# ---------------------------------------------------------------------------
# Alpha-to-magenta conversion
# ---------------------------------------------------------------------------


def _alpha_to_magenta(img: Image.Image, threshold: int = 128) -> Image.Image:
    """Convert RGBA alpha transparency to magenta-keyed RGB.

    Pixels with alpha < threshold become magenta (255, 0, 255).
    Pixels with alpha >= threshold keep their RGB values.
    Non-RGBA images are converted to RGB and returned as-is.

    Args:
        img: Source PIL Image (any mode).
        threshold: Alpha value below which pixels become magenta.

    Returns:
        RGB PIL Image with magenta transparency keying.
    """
    if img.mode != "RGBA":
        return img.convert("RGB")

    arr = np.array(img)
    rgb = arr[:, :, :3].copy()
    alpha = arr[:, :, 3]

    transparent_mask = alpha < threshold
    rgb[transparent_mask] = [255, 0, 255]

    return Image.fromarray(rgb, "RGB")


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def run_batch(
    manifest_path: Path,
    prompt_pack_path: Path,
    output_dir: Path,
    provider_name: str = "stub",
    seed: Optional[int] = None,
    snap_magenta: bool = False,
    verify_stages: Optional[Set[str]] = None,
    do_reformat: bool = False,
    emit_xp: bool = False,
    asset_name: Optional[str] = None,
    reference_policy: Optional[str] = None,
) -> RunReport:
    """Run a complete batch generation from manifest + prompt pack.

    AD-7 ordering: RGBA -> _alpha_to_magenta(RGB) -> snap(RGB) -> save

    Args:
        manifest_path: Path to guidance manifest JSON.
        prompt_pack_path: Path to prompt pack JSON.
        output_dir: Base output directory for all artifacts.
        provider_name: AI provider name ('stub' or 'gemini').
        seed: Optional CLI seed override (highest priority).
        snap_magenta: Whether to apply snap_to_magenta color correction.
        verify_stages: Set of verification stage letters to run (e.g. {"A","C"}).
        do_reformat: Whether to run the reformatter on stylized frames.
        emit_xp: Whether to emit a .xp file from the reformatted sheet.
        asset_name: Override asset name (defaults to manifest.name).
        reference_policy: Override reference image policy. TODO: Wire through
            to FrameRequest.reference_images when reference image support is
            implemented for Gemini integration. Currently a placeholder.

    Returns:
        RunReport with complete batch results.

    Raises:
        FileNotFoundError: If manifest or prompt pack files are missing.
        ValueError: If provider_name is not recognized.
    """
    batch_start = time.monotonic()

    # 1. Load manifest + prompt pack (fail fast if missing)
    manifest_path = Path(manifest_path)
    prompt_pack_path = Path(prompt_pack_path)
    output_dir = Path(output_dir)

    manifest = load_manifest(manifest_path)
    pack = load_prompt_pack(prompt_pack_path)

    # 2. Get provider (fail fast if unknown)
    if reference_policy is not None:
        logger.warning("reference_policy is not yet implemented, ignoring: %s", reference_policy)

    # WHY: gemini-cli needs a workdir for output PNGs and a trusted project_root
    # as subprocess cwd (gemini loads extensions + grants YOLO trust by cwd).
    # Other providers are stateless and need no constructor args.
    if provider_name == "gemini-cli":
        from .ai_provider import GeminiCLIAdapter
        # Resolve project root: walk up from this file to find the repo root
        _this_dir = Path(__file__).resolve().parent
        _project_root = _this_dir.parent.parent  # scripts/pipeline -> scripts -> repo
        provider = GeminiCLIAdapter(
            workdir=output_dir / "cli_workdir",
            project_root=_project_root,
        )
    else:
        provider = get_provider(provider_name)

    # 3. Create output directories
    stylized_dir = output_dir / "stylized"
    stylized_dir.mkdir(parents=True, exist_ok=True)

    # 4. Generate frames
    records: List[FrameRecord] = []
    errors: List[str] = []
    completed = 0
    failed = 0

    for expected_file in manifest.expected_files:
        # Parse angle, frame from filename "f_{angle}_{frame}.png"
        parts = expected_file.replace(".png", "").split("_")
        angle = int(parts[1])
        frame_idx = int(parts[2])

        prompt = resolve_prompt(pack, angle, frame_idx, manifest)
        resolved_seed = resolve_seed(pack, angle, frame_idx, seed)

        frame_start = time.monotonic()
        try:
            request = FrameRequest(
                prompt=prompt,
                angle=angle,
                frame=frame_idx,
                width=manifest.frame_width_px,
                height=manifest.frame_height_px,
                seed=resolved_seed,
            )
            result = provider.generate_frame(request)

            # AD-7: RGBA -> _alpha_to_magenta(RGB) -> snap(RGB) -> save
            img = _alpha_to_magenta(result.image, threshold=128)

            if snap_magenta:
                from .color_correction import snap_to_magenta
                img = snap_to_magenta(img, tolerance=15)

            # Save RGB PNG
            out_path = stylized_dir / f"f_{angle}_{frame_idx}.png"
            img.save(str(out_path), "PNG")

            elapsed_ms = int((time.monotonic() - frame_start) * 1000)

            record = FrameRecord(
                angle=angle,
                frame=frame_idx,
                prompt=prompt,
                seed_used=resolved_seed,
                output_path=out_path,
                elapsed_ms=elapsed_ms,
            )
            records.append(record)
            completed += 1

        except Exception as e:
            elapsed_ms = int((time.monotonic() - frame_start) * 1000)
            failed += 1
            errors.append(f"Frame f_{angle}_{frame_idx}.png failed: {e}")
            logger.error("Frame f_%d_%d.png failed: %s", angle, frame_idx, e)

    # 5. Verification (optional)
    verification_results = []
    if verify_stages:
        from .verification import run_verification
        verification_results = run_verification(
            stages=verify_stages,
            frame_dir=stylized_dir,
            angles=manifest.angles,
            frames=manifest.frames,
        )

    # 6. Reformat (optional)
    reformat_result = None
    if do_reformat:
        from .reformatter import run_reformatter

        sheets_dir = output_dir / "sheets"
        sheets_dir.mkdir(parents=True, exist_ok=True)

        name = asset_name if asset_name else manifest.name
        sheet_output = sheets_dir / f"{name}_reformatted.png"

        reformat_result = run_reformatter(
            input_dir=stylized_dir,
            output=sheet_output,
            angles=manifest.angles,
            frames=manifest.frames,
            target_cells_high=manifest.target_cells_high,
        )

    # 7. Emit XP (optional)
    xp_output_path = None
    if emit_xp and reformat_result is not None:
        from .schemas import AssetDef
        from .pipeline import AssetPipeline

        # name already set in reformat block above
        frames_for_xp = list(manifest.frames)

        # Scale frames by projs for the slicer (e.g. projs=2 doubles columns)
        if reformat_result.projs > 1:
            frames_for_xp = [f * reformat_result.projs for f in frames_for_xp]

        asset_def = AssetDef(
            name=name,
            type="custom",
            angles=manifest.angles,
            frames=frames_for_xp,
            source_type="file",
            source_path=str(reformat_result.output_path),
            normalization=False,
            projs=reformat_result.projs,
        )

        try:
            pipeline = AssetPipeline(asset_def, str(reformat_result.output_path))
            pipeline.run()
            # The XP output goes to default staging path
            from .staging import STAGING_DIR
            xp_output_path = STAGING_DIR / "xp" / f"{name}.xp"
        except Exception as e:
            errors.append(f"XP emission failed: {e}")
            logger.error("XP emission failed: %s", e)

    # 8. Write report
    elapsed_total_ms = int((time.monotonic() - batch_start) * 1000)

    report = RunReport(
        manifest_path=manifest_path,
        prompt_pack_path=prompt_pack_path,
        provider=provider_name,
        total_frames=manifest.total_frames,
        completed_frames=completed,
        failed_frames=failed,
        frame_records=records,
        verification_results=verification_results,
        elapsed_total_ms=elapsed_total_ms,
        reformat_result=reformat_result,
        xp_output_path=xp_output_path,
        errors=errors,
    )

    # Write JSON report
    report_path = output_dir / "run_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_data = _report_to_dict(report)
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)

    logger.info(
        "Batch complete: %d/%d frames in %dms",
        completed, manifest.total_frames, elapsed_total_ms,
    )

    return report


def _report_to_dict(report: RunReport) -> dict:
    """Serialize a RunReport to a JSON-compatible dict.

    Handles Path objects and FrameRecord/VerificationResult conversion.
    """
    frame_dicts = []
    for rec in report.frame_records:
        frame_dicts.append({
            "angle": rec.angle,
            "frame": rec.frame,
            "prompt": rec.prompt,
            "seed_used": rec.seed_used,
            "output_path": str(rec.output_path),
            "elapsed_ms": rec.elapsed_ms,
        })

    verification_dicts = []
    for vr in report.verification_results:
        verification_dicts.append({
            "stage": vr.stage,
            "stage_name": vr.stage_name,
            "passed": vr.passed,
            "errors": list(vr.errors) if vr.errors else [],
            "warnings": list(vr.warnings) if vr.warnings else [],
        })

    return {
        "manifest_path": str(report.manifest_path),
        "prompt_pack_path": str(report.prompt_pack_path),
        "provider": report.provider,
        "total_frames": report.total_frames,
        "completed_frames": report.completed_frames,
        "failed_frames": report.failed_frames,
        "frame_records": frame_dicts,
        "verification_results": verification_dicts,
        "elapsed_total_ms": report.elapsed_total_ms,
        "xp_output_path": str(report.xp_output_path) if report.xp_output_path else None,
        "errors": list(report.errors),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Run AI sprite frame generation batch"
    )
    parser.add_argument(
        "--manifest", required=True, help="Path to guidance manifest JSON"
    )
    parser.add_argument(
        "--prompt-pack", required=True, help="Path to prompt pack JSON"
    )
    parser.add_argument(
        "--output-dir", required=True, help="Base output directory"
    )
    parser.add_argument(
        "--provider", default="stub", choices=["stub", "gemini", "gemini-cli"],
        help="AI provider to use (default: stub)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Global seed override (highest priority)"
    )
    parser.add_argument(
        "--snap-magenta", action="store_true",
        help="Apply snap_to_magenta color correction"
    )
    parser.add_argument(
        "--verify", type=str, default=None,
        help="Verification stages to run (comma-separated, e.g. A,B,C,D)"
    )
    parser.add_argument(
        "--reformat", action="store_true",
        help="Run reformatter on stylized frames"
    )
    parser.add_argument(
        "--emit-xp", action="store_true",
        help="Emit .xp file from reformatted sheet"
    )
    parser.add_argument(
        "--asset-name", type=str, default=None,
        help="Override asset name (defaults to manifest name)"
    )

    args = parser.parse_args()

    verify_stages = None
    if args.verify:
        verify_stages = set(s.strip().upper() for s in args.verify.split(","))

    logging.basicConfig(level=logging.INFO)

    report = run_batch(
        manifest_path=Path(args.manifest),
        prompt_pack_path=Path(args.prompt_pack),
        output_dir=Path(args.output_dir),
        provider_name=args.provider,
        seed=args.seed,
        snap_magenta=args.snap_magenta,
        verify_stages=verify_stages,
        do_reformat=args.reformat,
        emit_xp=args.emit_xp,
        asset_name=args.asset_name,
    )

    print(f"Batch complete: {report.completed_frames}/{report.total_frames} frames")
    print(f"Failed: {report.failed_frames}")
    print(f"Time: {report.elapsed_total_ms}ms")

    if report.errors:
        print(f"Errors: {len(report.errors)}")
        for err in report.errors:
            print(f"  - {err}")

    if report.reformat_result:
        print(f"Sheet: {report.reformat_result.output_path}")
        print(f"Projs: {report.reformat_result.projs}")

    if report.xp_output_path:
        print(f"XP: {report.xp_output_path}")

    sys.exit(1 if report.failed_frames > 0 else 0)

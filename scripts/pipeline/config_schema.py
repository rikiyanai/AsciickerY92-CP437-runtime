from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict, is_dataclass
from typing import Any, Optional, Mapping, get_type_hints


def _meta(
    cli_flag: str,
    description: str,
    ui_widget: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    choices: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "fm": FieldMeta(
            cli_flag=cli_flag,
            description=description,
            min_val=min_val,
            max_val=max_val,
            ui_widget=ui_widget,
            choices=choices or [],
        )
    }


@dataclass(frozen=True)
class FieldMeta:
    """Metadata shared across CLI and UI bindings for one config field."""

    cli_flag: str
    description: str
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    ui_widget: str = "text"
    choices: list[str] = field(default_factory=list)


@dataclass
class ExtractSettings:
    bg_colors: list[tuple[int, int, int]] = field(
        default_factory=lambda: [(255, 0, 255)],
        metadata=_meta(
            "--bg-colors",
            "Background colors used for opaque-sheet segmentation.",
            "color_list",
        ),
    )
    bg_tolerance: int = field(
        default=30,
        metadata=_meta(
            "--bg-tolerance",
            "Euclidean color-distance threshold for foreground segmentation.",
            "number",
            min_val=0,
            max_val=255,
        ),
    )
    alpha_threshold: float = field(
        default=0.10,
        metadata=_meta(
            "--alpha-threshold",
            "Alpha cutoff for transparent-sheet segmentation.",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    min_sprite_size: tuple[int, int] = field(
        default=(30, 30),
        metadata=_meta(
            "--min-sprite-size",
            "Minimum extracted sprite size as WIDTHxHEIGHT.",
            "text",
            min_val=1,
            max_val=8192,
        ),
    )
    extraction_mode: str = field(
        default="both",
        metadata=_meta(
            "--extraction-mode",
            "Extractor mode: shape, bbox, or both.",
            "select",
            choices=["shape", "bbox", "both"],
        ),
    )
    max_coverage: float = field(
        default=0.9,
        metadata=_meta(
            "--max-coverage",
            "Maximum fraction of source image area a single component may cover "
            "(rejects full-sheet blobs). Set to 1.0 to disable.",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    use_selection_roi: bool = False
    selection_roi: Optional[tuple[int, int, int, int]] = None


@dataclass
class GridSettings:
    slice_mode: str = field(
        default="rowcol",
        metadata=_meta(
            "--slice-mode",
            "Stage 2 slicing mode: row/column detection (default) or legacy global grid.",
            "select",
            choices=["global_grid", "rowcol"],
        ),
    )
    detection_method: str = field(
        default="both",
        metadata=_meta(
            "--grid-method",
            "Grid detection strategy.",
            "select",
            choices=["fft", "gradient", "both"],
        ),
    )
    sample_method: str = field(
        default="median",
        metadata=_meta(
            "--sample-method",
            "Pixel sampling method for detected grid cells.",
            "select",
            choices=["center", "median", "majority"],
        ),
    )
    min_grid_size: float = field(
        default=4.0,
        metadata=_meta(
            "--min-grid-size",
            "Smallest allowed detected grid size in pixels.",
            "number",
            min_val=1.0,
            max_val=1024.0,
        ),
    )
    refine_intensity: float = field(
        default=0.25,
        metadata=_meta(
            "--refine-intensity",
            "Grid refinement strength.",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    content_correction: bool = field(
        default=True,
        metadata=_meta(
            "--content-correction",
            "Enable content-aware crop correction after grid slicing.",
            "checkbox",
        ),
    )
    content_correction_max_shift: float = field(
        default=0.25,
        metadata=_meta(
            "--content-correction-max-shift",
            "Maximum crop shift as fraction of cell size (0.0-0.5).",
            "number",
            min_val=0.0,
            max_val=0.5,
        ),
    )
    hard_fail_split_ratio: float = field(
        default=0.0,
        metadata=_meta(
            "--hard-fail-split-ratio",
            "Hard-fail split ratio threshold after slicing. "
            "0.0 = disabled (default).",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    hard_fail_bleed_ratio: float = field(
        default=0.0,
        metadata=_meta(
            "--hard-fail-bleed-ratio",
            "Hard-fail bleed ratio threshold after slicing. "
            "0.0 = disabled (default).",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    require_human_stage2_review: bool = field(
        default=False,
        metadata=_meta(
            "--require-human-stage2-review",
            "Require human signoff after Stage 2 slicing before Stage 3 processing.",
            "checkbox",
        ),
    )
    human_stage2_review_path: str = field(
        default="docs/research/ascii/verification/manual-stage2-slice-review.json",
        metadata=_meta(
            "--human-stage2-review-path",
            "JSON signoff file path for Stage 2 human review.",
            "text",
        ),
    )
    rowcol_fallback_to_grid: bool = field(
        default=True,
        metadata=_meta(
            "--rowcol-fallback-to-grid",
            "When row/column slicing detection is weak, fallback to global-grid slicing.",
            "checkbox",
        ),
    )
    # ── Human review gates for pipeline stages 3-5 ──
    require_human_stage3_review: bool = field(
        default=False,
        metadata=_meta(
            "--require-human-stage3-review",
            "Require human signoff after Stage 3 processing before assembly.",
            "checkbox",
        ),
    )
    human_stage3_review_path: str = field(
        default="docs/research/ascii/verification/manual-stage3-process-review.json",
        metadata=_meta(
            "--human-stage3-review-path",
            "JSON signoff file path for Stage 3 human review.",
            "text",
        ),
    )
    require_human_stage4_review: bool = field(
        default=False,
        metadata=_meta(
            "--require-human-stage4-review",
            "Require human signoff after Stage 4 assembly before quality gates.",
            "checkbox",
        ),
    )
    human_stage4_review_path: str = field(
        default="docs/research/ascii/verification/manual-stage4-assemble-review.json",
        metadata=_meta(
            "--human-stage4-review-path",
            "JSON signoff file path for Stage 4 human review.",
            "text",
        ),
    )
    require_human_stage5_review: bool = field(
        default=False,
        metadata=_meta(
            "--require-human-stage5-review",
            "Require human signoff after quality gates before final output.",
            "checkbox",
        ),
    )
    human_stage5_review_path: str = field(
        default="docs/research/ascii/verification/manual-stage5-gates-review.json",
        metadata=_meta(
            "--human-stage5-review-path",
            "JSON signoff file path for Stage 5 quality-gates human review.",
            "text",
        ),
    )


@dataclass
class ProcessSettings:
    mode: str = field(
        default="auto",
        metadata=_meta(
            "--process-mode",
            "Stage 3 processor mode.",
            "select",
            choices=["literal", "standard-legacy", "quality", "auto"],
        ),
    )
    source_cell_px: Optional[int] = field(
        default=None,
        metadata=_meta(
            "--source-cell-px",
            "Detected/source pixel size per cell. Required for literal mode.",
            "number",
            min_val=1,
            max_val=1024,
        ),
    )
    target_cell_px: int = field(
        default=12,
        metadata=_meta(
            "--target-cell-px",
            "Target engine pixels per cell.",
            "number",
            min_val=1,
            max_val=1024,
        ),
    )
    error_diffusion: str = field(
        default="none",
        metadata=_meta(
            "--error-diffusion",
            "Error diffusion scope for quality mode.",
            "select",
            choices=["none", "intra_cell", "cross_cell"],
        ),
    )
    color_metric: str = field(
        default="euclidean",
        metadata=_meta(
            "--color-metric",
            "Color distance metric for quantization/matching.",
            "select",
            choices=["euclidean", "perceptual"],
        ),
    )
    max_standard_cell_ratio: int = field(
        default=2,
        metadata=_meta(
            "--max-standard-cell-ratio",
            "Max source-cell to CELL_SIZE ratio for standard mode. "
            "Frames exceeding this are blocked.  Tightened to 2 in Phase 13.1.",
            "number",
            min_val=1,
            max_val=32,
        ),
    )
    standard_align_policy: str = field(
        default="fail",
        metadata=_meta(
            "--standard-align-policy",
            "Policy for standard-mode frames not aligned to CELL_SIZE grid. "
            "'fail' = hard error (default); 'pad' = pad with transparent/black fill.",
            "select",
            choices=["fail", "pad"],
        ),
    )
    quality_align_policy: str = field(
        default="pad_to_even",
        metadata=_meta(
            "--quality-align-policy",
            "Alignment policy for quality processor when frame dimensions are odd. "
            "'pad_to_even' = pad to even dimensions (default); 'fail' = hard error.",
            "select",
            choices=["fail", "pad_to_even"],
        ),
    )


@dataclass
class NormalizationSettings:
    normalize_input_mode: str = field(
        default="auto",
        metadata=_meta(
            "--normalize-input-mode",
            "Input normalization mode: off (legacy path), auto (normalize if possible), "
            "required (fail if normalization fails).",
            "select",
            choices=["off", "auto", "required"],
        ),
    )
    min_confidence: float = field(
        default=0.5,
        metadata=_meta(
            "--normalize-min-confidence",
            "Minimum normalization confidence to proceed. "
            "Below this, normalization fails closed.",
            "number",
            min_val=0.0,
            max_val=1.0,
        ),
    )
    normalized_manifest_path: str = field(
        default="",
        metadata=_meta(
            "--normalized-manifest-path",
            "Path to pre-existing canonical manifest. "
            "If set, skip normalization and use this manifest directly.",
            "text",
        ),
    )
    require_human_n0_review: bool = field(
        default=False,
        metadata=_meta(
            "--require-human-n0-review",
            "Require human signoff after normalization (Stage N0) before downstream stages.",
            "checkbox",
        ),
    )
    human_n0_review_path: str = field(
        default="docs/research/ascii/verification/manual-stageN0-normalize-review.json",
        metadata=_meta(
            "--human-n0-review-path",
            "JSON signoff file path for N0 normalization human review.",
            "text",
        ),
    )


@dataclass
class BranchSettings:
    auto_prune: bool = field(
        default=False,
        metadata=_meta(
            "--auto-prune",
            "Enable automatic branch pruning by score/caps.",
            "checkbox",
        ),
    )
    max_branches_per_stage: int = field(
        default=8,
        metadata=_meta(
            "--max-branches-per-stage",
            "Max active branches advanced per stage.",
            "number",
            min_val=1,
            max_val=64,
        ),
    )
    max_global_branches: int = field(
        default=16,
        metadata=_meta(
            "--max-global-branches",
            "Max total active branches across the job.",
            "number",
            min_val=1,
            max_val=256,
        ),
    )
    expand_all: bool = field(
        default=True,
        metadata=_meta(
            "--expand-all",
            "Show pruned branches in the viewer.",
            "checkbox",
        ),
    )
    tie_break_order: tuple[str, ...] = field(
        default=(
            "pp_fft",
            "pp_gradient",
            "extractor_flood",
            "extractor_bbox",
            "literal",
            "standard",
            "quality",
        ),
        metadata=_meta(
            "--tie-break-order",
            "Comma-separated deterministic tie-break order for track IDs.",
            "text",
        ),
    )
    enable_branch_loop: bool = field(
        default=True,
        metadata=_meta(
            "--enable-branch-loop",
            "Enable multi-track branching in the pipeline.",
            "checkbox",
        ),
    )


@dataclass
class PipelineConfig:
    extract_settings: ExtractSettings = field(default_factory=ExtractSettings)
    grid_settings: GridSettings = field(default_factory=GridSettings)
    process_settings: ProcessSettings = field(default_factory=ProcessSettings)
    branch_settings: BranchSettings = field(default_factory=BranchSettings)
    normalization_settings: NormalizationSettings = field(default_factory=NormalizationSettings)

    def validate(self) -> None:
        ex = self.extract_settings
        gd = self.grid_settings
        pr = self.process_settings
        br = self.branch_settings
        nm = self.normalization_settings

        if nm.normalize_input_mode not in {"off", "auto", "required"}:
            raise ValueError("normalize_input_mode must be one of: off, auto, required")
        if not (0.0 <= nm.min_confidence <= 1.0):
            raise ValueError("min_confidence must be within [0.0, 1.0]")
        if nm.require_human_n0_review and not str(nm.human_n0_review_path).strip():
            raise ValueError(
                "human_n0_review_path must be set when require_human_n0_review=true"
            )

        if ex.extraction_mode not in {"shape", "bbox", "both"}:
            raise ValueError("extraction_mode must be one of: shape, bbox, both")
        if not (0.0 < ex.max_coverage <= 1.0):
            raise ValueError("max_coverage must be within (0.0, 1.0]")
        if gd.slice_mode not in {"global_grid", "rowcol"}:
            raise ValueError("slice_mode must be one of: global_grid, rowcol")
        if gd.detection_method not in {"fft", "gradient", "both"}:
            raise ValueError("detection_method must be one of: fft, gradient, both")
        if gd.sample_method not in {"center", "median", "majority"}:
            raise ValueError("sample_method must be one of: center, median, majority")
        if pr.mode not in {"literal", "standard", "standard-legacy", "quality", "auto"}:
            raise ValueError("mode must be one of: literal, standard-legacy, quality, auto")
        if pr.error_diffusion not in {"none", "intra_cell", "cross_cell"}:
            raise ValueError("error_diffusion must be one of: none, intra_cell, cross_cell")
        if pr.color_metric not in {"euclidean", "perceptual"}:
            raise ValueError("color_metric must be one of: euclidean, perceptual")

        if not (0.0 <= ex.alpha_threshold <= 1.0):
            raise ValueError("alpha_threshold must be within [0.0, 1.0]")
        if not (0 <= ex.bg_tolerance <= 255):
            raise ValueError("bg_tolerance must be within [0, 255]")
        if ex.min_sprite_size[0] < 1 or ex.min_sprite_size[1] < 1:
            raise ValueError("min_sprite_size dimensions must be >= 1")
        if ex.selection_roi is not None:
            if len(ex.selection_roi) != 4:
                raise ValueError("selection_roi must contain 4 integers: x,y,w,h")
            x, y, w, h = (int(v) for v in ex.selection_roi)
            if x < 0 or y < 0:
                raise ValueError("selection_roi x/y must be >= 0")
            if w <= 0 or h <= 0:
                raise ValueError("selection_roi w/h must be > 0")

        for color in ex.bg_colors:
            if len(color) != 3:
                raise ValueError("each bg color must contain exactly 3 channels")
            if any((channel < 0 or channel > 255) for channel in color):
                raise ValueError("bg color channels must be within [0, 255]")

        if gd.min_grid_size <= 0:
            raise ValueError("min_grid_size must be > 0")
        if not (0.0 <= gd.refine_intensity <= 1.0):
            raise ValueError("refine_intensity must be within [0.0, 1.0]")
        if not (0.0 <= gd.content_correction_max_shift <= 0.5):
            raise ValueError("content_correction_max_shift must be within [0.0, 0.5]")
        if not (0.0 <= gd.hard_fail_split_ratio <= 1.0):
            raise ValueError("hard_fail_split_ratio must be within [0.0, 1.0]")
        if not (0.0 <= gd.hard_fail_bleed_ratio <= 1.0):
            raise ValueError("hard_fail_bleed_ratio must be within [0.0, 1.0]")
        if gd.require_human_stage2_review and not str(gd.human_stage2_review_path).strip():
            raise ValueError(
                "human_stage2_review_path must be set when require_human_stage2_review=true"
            )

        if pr.source_cell_px is not None and pr.source_cell_px <= 0:
            raise ValueError("source_cell_px must be > 0 when provided")
        if pr.target_cell_px <= 0:
            raise ValueError("target_cell_px must be > 0")
        if pr.mode == "literal" and pr.source_cell_px != 1:
            raise ValueError("literal mode requires source_cell_px == 1")
        if not (1 <= pr.max_standard_cell_ratio <= 32):
            raise ValueError("max_standard_cell_ratio must be within [1, 32]")
        if pr.standard_align_policy not in {"fail", "pad"}:
            raise ValueError("standard_align_policy must be one of: fail, pad")
        if pr.quality_align_policy not in {"fail", "pad_to_even"}:
            raise ValueError("quality_align_policy must be one of: fail, pad_to_even")

        if br.max_branches_per_stage < 1:
            raise ValueError("max_branches_per_stage must be >= 1")
        if br.max_global_branches < 1:
            raise ValueError("max_global_branches must be >= 1")
        if br.max_branches_per_stage > br.max_global_branches:
            raise ValueError("max_branches_per_stage cannot exceed max_global_branches")
        if not br.tie_break_order:
            raise ValueError("tie_break_order must not be empty")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _serialize(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PipelineConfig":
        ex_raw = dict(data.get("extract_settings", {}))
        gd_raw = dict(data.get("grid_settings", {}))
        pr_raw = dict(data.get("process_settings", {}))
        br_raw = dict(data.get("branch_settings", {}))
        nm_raw = dict(data.get("normalization_settings", {}))

        config = cls(
            extract_settings=ExtractSettings(
                bg_colors=_parse_bg_colors(ex_raw.get("bg_colors", [(255, 0, 255)])),
                bg_tolerance=int(ex_raw["bg_tolerance"]) if ex_raw.get("bg_tolerance") is not None else 30,
                alpha_threshold=float(ex_raw["alpha_threshold"]) if ex_raw.get("alpha_threshold") is not None else 0.10,
                min_sprite_size=_parse_size(ex_raw.get("min_sprite_size", (30, 30))),
                extraction_mode=str(ex_raw.get("extraction_mode", "both")),
                max_coverage=float(ex_raw.get("max_coverage", 0.9)),
                use_selection_roi=bool(ex_raw.get("use_selection_roi", False)),
                selection_roi=_parse_selection_roi(ex_raw.get("selection_roi")),
            ),
            grid_settings=GridSettings(
                slice_mode=str(gd_raw.get("slice_mode", "global_grid")),
                detection_method=str(gd_raw.get("detection_method", "both")),
                sample_method=str(gd_raw.get("sample_method", "median")),
                min_grid_size=float(gd_raw["min_grid_size"]) if gd_raw.get("min_grid_size") is not None else 4.0,
                refine_intensity=float(gd_raw["refine_intensity"]) if gd_raw.get("refine_intensity") is not None else 0.25,
                content_correction=bool(gd_raw.get("content_correction", False)),
                content_correction_max_shift=(
                    float(gd_raw["content_correction_max_shift"])
                    if gd_raw.get("content_correction_max_shift") is not None
                    else 0.25
                ),
                hard_fail_split_ratio=(
                    float(gd_raw["hard_fail_split_ratio"])
                    if gd_raw.get("hard_fail_split_ratio") is not None
                    else 0.0
                ),
                hard_fail_bleed_ratio=(
                    float(gd_raw["hard_fail_bleed_ratio"])
                    if gd_raw.get("hard_fail_bleed_ratio") is not None
                    else 0.0
                ),
                require_human_stage2_review=bool(gd_raw.get("require_human_stage2_review", False)),
                human_stage2_review_path=str(
                    gd_raw.get(
                        "human_stage2_review_path",
                        "docs/research/ascii/verification/manual-stage2-slice-review.json",
                    )
                ),
                require_human_stage3_review=bool(gd_raw.get("require_human_stage3_review", False)),
                human_stage3_review_path=str(
                    gd_raw.get(
                        "human_stage3_review_path",
                        "docs/research/ascii/verification/manual-stage3-process-review.json",
                    )
                ),
                require_human_stage4_review=bool(gd_raw.get("require_human_stage4_review", False)),
                human_stage4_review_path=str(
                    gd_raw.get(
                        "human_stage4_review_path",
                        "docs/research/ascii/verification/manual-stage4-assemble-review.json",
                    )
                ),
                require_human_stage5_review=bool(gd_raw.get("require_human_stage5_review", False)),
                human_stage5_review_path=str(
                    gd_raw.get(
                        "human_stage5_review_path",
                        "docs/research/ascii/verification/manual-stage5-gates-review.json",
                    )
                ),
                rowcol_fallback_to_grid=bool(
                    gd_raw.get("rowcol_fallback_to_grid", True)
                ),
            ),
            process_settings=ProcessSettings(
                mode=str(pr_raw.get("mode", "auto")),
                source_cell_px=(
                    int(pr_raw["source_cell_px"])
                    if pr_raw.get("source_cell_px") is not None
                    else None
                ),
                target_cell_px=int(pr_raw["target_cell_px"]) if pr_raw.get("target_cell_px") is not None else 12,
                error_diffusion=str(pr_raw.get("error_diffusion", "none")),
                color_metric=str(pr_raw.get("color_metric", "euclidean")),
                max_standard_cell_ratio=(
                    int(pr_raw["max_standard_cell_ratio"])
                    if pr_raw.get("max_standard_cell_ratio") is not None
                    else 2
                ),
                standard_align_policy=str(
                    pr_raw.get("standard_align_policy", "fail")
                ).strip().lower(),
                quality_align_policy=str(
                    pr_raw.get("quality_align_policy", "pad_to_even")
                ).strip().lower(),
            ),
            branch_settings=BranchSettings(
                auto_prune=bool(br_raw.get("auto_prune", False)),
                max_branches_per_stage=int(br_raw["max_branches_per_stage"]) if br_raw.get("max_branches_per_stage") is not None else 8,
                max_global_branches=int(br_raw["max_global_branches"]) if br_raw.get("max_global_branches") is not None else 16,
                expand_all=bool(br_raw.get("expand_all", True)),
                tie_break_order=_parse_tie_break_order(
                    br_raw.get(
                        "tie_break_order",
                        (
                            "pp_fft",
                            "pp_gradient",
                            "extractor_flood",
                            "extractor_bbox",
                            "literal",
                            "standard",
                            "quality",
                        ),
                    )
                ),
                enable_branch_loop=bool(br_raw.get("enable_branch_loop", True)),
            ),
            normalization_settings=NormalizationSettings(
                normalize_input_mode=str(
                    nm_raw.get("normalize_input_mode", "auto")
                ).strip().lower(),
                min_confidence=float(
                    nm_raw.get("min_confidence", 0.5)
                ),
                normalized_manifest_path=str(
                    nm_raw.get("normalized_manifest_path", "")
                ),
                require_human_n0_review=bool(
                    nm_raw.get("require_human_n0_review", False)
                ),
                human_n0_review_path=str(
                    nm_raw.get(
                        "human_n0_review_path",
                        "docs/research/ascii/verification/manual-stageN0-normalize-review.json",
                    )
                ),
            ),
        )
        config.validate()
        return config

    def to_cli_args(self) -> list[str]:
        self.validate()
        ex = self.extract_settings
        gd = self.grid_settings
        pr = self.process_settings
        br = self.branch_settings

        args: list[str] = [
            "--bg-colors",
            _format_bg_colors(ex.bg_colors),
            "--bg-tolerance",
            str(ex.bg_tolerance),
            "--alpha-threshold",
            str(ex.alpha_threshold),
            "--min-sprite-size",
            f"{ex.min_sprite_size[0]}x{ex.min_sprite_size[1]}",
            "--extraction-mode",
            ex.extraction_mode,
            "--max-coverage",
            str(ex.max_coverage),
            "--grid-method",
            gd.detection_method,
            "--slice-mode",
            gd.slice_mode,
            "--sample-method",
            gd.sample_method,
            "--min-grid-size",
            str(gd.min_grid_size),
            "--refine-intensity",
            str(gd.refine_intensity),
            "--content-correction-max-shift",
            str(gd.content_correction_max_shift),
            "--hard-fail-split-ratio",
            str(gd.hard_fail_split_ratio),
            "--hard-fail-bleed-ratio",
            str(gd.hard_fail_bleed_ratio),
            "--process-mode",
            pr.mode,
            "--target-cell-px",
            str(pr.target_cell_px),
            "--error-diffusion",
            pr.error_diffusion,
            "--color-metric",
            pr.color_metric,
            "--max-standard-cell-ratio",
            str(pr.max_standard_cell_ratio),
            "--standard-align-policy",
            pr.standard_align_policy,
            "--quality-align-policy",
            pr.quality_align_policy,
            "--max-branches-per-stage",
            str(br.max_branches_per_stage),
            "--max-global-branches",
            str(br.max_global_branches),
            "--tie-break-order",
            ",".join(br.tie_break_order),
        ]

        if pr.source_cell_px is not None:
            args.extend(["--source-cell-px", str(pr.source_cell_px)])
        if gd.content_correction:
            args.append("--content-correction")
        if gd.rowcol_fallback_to_grid:
            args.append("--rowcol-fallback-to-grid")
        else:
            args.append("--no-rowcol-fallback-to-grid")
        if gd.require_human_stage2_review:
            args.append("--require-human-stage2-review")
            if gd.human_stage2_review_path:
                args.extend(["--human-stage2-review-path", gd.human_stage2_review_path])
        if gd.require_human_stage3_review:
            args.append("--require-human-stage3-review")
            if gd.human_stage3_review_path:
                args.extend(["--human-stage3-review-path", gd.human_stage3_review_path])
        if gd.require_human_stage4_review:
            args.append("--require-human-stage4-review")
            if gd.human_stage4_review_path:
                args.extend(["--human-stage4-review-path", gd.human_stage4_review_path])
        if gd.require_human_stage5_review:
            args.append("--require-human-stage5-review")
            if gd.human_stage5_review_path:
                args.extend(["--human-stage5-review-path", gd.human_stage5_review_path])
        args.append("--auto-prune" if br.auto_prune else "--no-auto-prune")
        if br.expand_all:
            args.append("--expand-all")
        if br.enable_branch_loop:
            args.append("--enable-branch-loop")

        nm = self.normalization_settings
        if nm.normalize_input_mode != "off":
            args.extend(["--normalize-input-mode", nm.normalize_input_mode])
            args.extend(["--normalize-min-confidence", str(nm.min_confidence)])
        if nm.normalized_manifest_path:
            args.extend(["--normalized-manifest-path", nm.normalized_manifest_path])
        if nm.require_human_n0_review:
            args.append("--require-human-n0-review")
            if nm.human_n0_review_path:
                args.extend(["--human-n0-review-path", nm.human_n0_review_path])

        return args

    @classmethod
    def from_cli_args(cls, args: Any) -> "PipelineConfig":
        src = vars(args) if hasattr(args, "__dict__") else dict(args)
        raw_alpha = src.get("alpha_threshold", None)
        raw_auto_prune = bool(src.get("auto_prune", False))
        raw_no_auto_prune = bool(src.get("no_auto_prune", False))
        auto_prune = False
        if raw_auto_prune:
            auto_prune = True
        elif raw_no_auto_prune:
            auto_prune = False
        raw_rowcol_fallback = src.get("rowcol_fallback_to_grid", None)
        rowcol_fallback = True if raw_rowcol_fallback is None else bool(raw_rowcol_fallback)

        data = {
            "extract_settings": {
                "bg_colors": _parse_bg_colors(src.get("bg_colors", None)),
                "bg_tolerance": src.get("bg_tolerance", 30),
                "alpha_threshold": _normalize_alpha_threshold(raw_alpha),
                "min_sprite_size": _parse_size(
                    src.get("min_sprite_size", (30, 30)) or (30, 30)
                ),
                "extraction_mode": src.get("extraction_mode", "both") or "both",
                "max_coverage": (
                    0.9
                    if src.get("max_coverage", None) is None
                    else float(src.get("max_coverage"))
                ),
            },
            "grid_settings": {
                "slice_mode": src.get("slice_mode", "global_grid") or "global_grid",
                "detection_method": src.get("grid_method", "both") or "both",
                "sample_method": src.get("sample_method", "median") or "median",
                "min_grid_size": (
                    4.0
                    if src.get("min_grid_size", None) is None
                    else src.get("min_grid_size")
                ),
                "refine_intensity": (
                    0.25
                    if src.get("refine_intensity", None) is None
                    else src.get("refine_intensity")
                ),
                "content_correction": bool(src.get("content_correction", False)),
                "content_correction_max_shift": (
                    0.25
                    if src.get("content_correction_max_shift", None) is None
                    else float(src.get("content_correction_max_shift"))
                ),
                "hard_fail_split_ratio": (
                    0.0
                    if src.get("hard_fail_split_ratio", None) is None
                    else float(src.get("hard_fail_split_ratio"))
                ),
                "hard_fail_bleed_ratio": (
                    0.0
                    if src.get("hard_fail_bleed_ratio", None) is None
                    else float(src.get("hard_fail_bleed_ratio"))
                ),
                "require_human_stage2_review": bool(
                    src.get("require_human_stage2_review", False)
                ),
                "human_stage2_review_path": (
                    src.get(
                        "human_stage2_review_path",
                        "docs/research/ascii/verification/manual-stage2-slice-review.json",
                    )
                    or "docs/research/ascii/verification/manual-stage2-slice-review.json"
                ),
                "require_human_stage3_review": bool(
                    src.get("require_human_stage3_review", False)
                ),
                "human_stage3_review_path": (
                    src.get(
                        "human_stage3_review_path",
                        "docs/research/ascii/verification/manual-stage3-process-review.json",
                    )
                    or "docs/research/ascii/verification/manual-stage3-process-review.json"
                ),
                "require_human_stage4_review": bool(
                    src.get("require_human_stage4_review", False)
                ),
                "human_stage4_review_path": (
                    src.get(
                        "human_stage4_review_path",
                        "docs/research/ascii/verification/manual-stage4-assemble-review.json",
                    )
                    or "docs/research/ascii/verification/manual-stage4-assemble-review.json"
                ),
                "require_human_stage5_review": bool(
                    src.get("require_human_stage5_review", False)
                ),
                "human_stage5_review_path": (
                    src.get(
                        "human_stage5_review_path",
                        "docs/research/ascii/verification/manual-stage5-gates-review.json",
                    )
                    or "docs/research/ascii/verification/manual-stage5-gates-review.json"
                ),
                "rowcol_fallback_to_grid": rowcol_fallback,
            },
            "process_settings": {
                "mode": src.get("process_mode", "auto") or "auto",
                "source_cell_px": src.get("source_cell_px"),
                "target_cell_px": (
                    12 if src.get("target_cell_px", None) is None else src.get("target_cell_px")
                ),
                "error_diffusion": src.get("error_diffusion", "none") or "none",
                "color_metric": src.get("color_metric", "euclidean") or "euclidean",
                "max_standard_cell_ratio": (
                    2
                    if src.get("max_standard_cell_ratio", None) is None
                    else int(src.get("max_standard_cell_ratio"))
                ),
                "standard_align_policy": (
                    src.get("standard_align_policy", "fail") or "fail"
                ),
                "quality_align_policy": (
                    src.get("quality_align_policy", "pad_to_even") or "pad_to_even"
                ),
            },
            "branch_settings": {
                "auto_prune": auto_prune,
                "max_branches_per_stage": (
                    8
                    if src.get("max_branches_per_stage", None) is None
                    else src.get("max_branches_per_stage")
                ),
                "max_global_branches": (
                    16
                    if src.get("max_global_branches", None) is None
                    else src.get("max_global_branches")
                ),
                "expand_all": bool(src.get("expand_all", True)),
                "tie_break_order": tuple(
                    str(v)
                    for v in str(
                        src.get("tie_break_order", None)
                        or "pp_fft,pp_gradient,extractor_flood,extractor_bbox,literal,standard,quality"
                    ).split(",")
                    if v
                ),
                "enable_branch_loop": bool(src.get("enable_branch_loop", True)),
            },
            "normalization_settings": {
                "normalize_input_mode": (
                    src.get("normalize_input_mode", "auto") or "auto"
                ),
                "min_confidence": (
                    0.5
                    if src.get("normalize_min_confidence", None) is None
                    else float(src.get("normalize_min_confidence"))
                ),
                "normalized_manifest_path": (
                    src.get("normalized_manifest_path", "") or ""
                ),
                "require_human_n0_review": bool(
                    src.get("require_human_n0_review", False)
                ),
                "human_n0_review_path": (
                    src.get(
                        "human_n0_review_path",
                        "docs/research/ascii/verification/manual-stageN0-normalize-review.json",
                    )
                    or "docs/research/ascii/verification/manual-stageN0-normalize-review.json"
                ),
            },
        }
        return cls.from_dict(data)


def generate_cli_flags(config_cls: type) -> list[dict[str, Any]]:
    """Introspect dataclass field metadata and return CLI flag definitions."""
    return _collect_field_meta(config_cls)


def generate_ui_form(config_cls: type) -> list[dict[str, Any]]:
    """Introspect dataclass field metadata and return UI field definitions."""
    specs = _collect_field_meta(config_cls)
    for spec in specs:
        spec["label"] = spec["field_name"].replace("_", " ").title()
    return specs


def _collect_field_meta(config_cls: type) -> list[dict[str, Any]]:
    if not is_dataclass(config_cls):
        raise TypeError("config_cls must be a dataclass type")

    specs: list[dict[str, Any]] = []
    section_hints = get_type_hints(config_cls)

    for section_field in fields(config_cls):
        section_type = section_hints.get(section_field.name)
        if not is_dataclass(section_type):
            continue
        for sf in fields(section_type):
            fm = sf.metadata.get("fm")
            if fm is None:
                continue
            specs.append(
                {
                    "section": section_field.name,
                    "field_name": sf.name,
                    "cli_flag": fm.cli_flag,
                    "description": fm.description,
                    "min_val": fm.min_val,
                    "max_val": fm.max_val,
                    "ui_widget": fm.ui_widget,
                    "choices": list(fm.choices),
                }
            )

    return specs


def _parse_size(value: Any) -> tuple[int, int]:
    if isinstance(value, tuple) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, list) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, str):
        text = value.strip().lower().replace("x", ",")
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    raise ValueError(f"invalid size value: {value!r}")


def _parse_bg_colors(value: Any) -> list[tuple[int, int, int]]:
    if value is None:
        return [(255, 0, 255)]

    if isinstance(value, str):
        colors: list[tuple[int, int, int]] = []
        chunks = [c.strip() for c in value.split(";") if c.strip()]
        for chunk in chunks:
            parts = [p.strip() for p in chunk.split(",") if p.strip()]
            if len(parts) != 3:
                raise ValueError(f"invalid bg color token: {chunk!r}")
            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
        return colors or [(255, 0, 255)]

    if isinstance(value, list):
        parsed: list[tuple[int, int, int]] = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                parsed.append((int(item[0]), int(item[1]), int(item[2])))
            else:
                raise ValueError(f"invalid bg color item: {item!r}")
        return parsed or [(255, 0, 255)]

    if isinstance(value, tuple) and len(value) == 3:
        return [(int(value[0]), int(value[1]), int(value[2]))]

    raise ValueError(f"invalid bg_colors value: {value!r}")


def _format_bg_colors(colors: list[tuple[int, int, int]]) -> str:
    return ";".join(f"{r},{g},{b}" for r, g, b in colors)


def _parse_tie_break_order(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(v) for v in value if str(v))
    if isinstance(value, list):
        return tuple(str(v) for v in value if str(v))
    if isinstance(value, str):
        if not value.strip():
            return tuple()
        return tuple(part.strip() for part in value.split(",") if part.strip())
    raise ValueError(f"invalid tie_break_order value: {value!r}")


def _parse_selection_roi(value: Any) -> Optional[tuple[int, int, int, int]]:
    if value is None:
        return None
    if isinstance(value, dict):
        keys = ("x", "y", "w", "h")
        if not all(k in value for k in keys):
            raise ValueError("selection_roi dict must contain x,y,w,h")
        return (
            int(value["x"]),
            int(value["y"]),
            int(value["w"]),
            int(value["h"]),
        )
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    raise ValueError(f"invalid selection_roi value: {value!r}")


def _normalize_alpha_threshold(value: Any) -> float:
    if value is None:
        return 0.10
    raw = float(value)
    if raw > 1.0:
        return max(0.0, min(1.0, raw / 255.0))
    return raw


def _serialize(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_serialize(v) for v in value]
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value

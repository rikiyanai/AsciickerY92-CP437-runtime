"""Processor mode dispatch for the asset pipeline.

[FLOW:DISPATCH] Resolves which processor (standard, quality, literal)
handles a given set of frames based on configuration, overrides, and
frame dimensions.

Extracted from pipeline.py to reduce merge conflict risk across tracks
and enable isolated testing of dispatch logic.
"""

import sys

from scripts.pipeline.service.constants import CELL_SIZE

VALID_PROCESS_MODES = frozenset({
    "standard", "standard-legacy", "literal", "quality",
})


def validate_process_mode(mode):
    """Raise ValueError if *mode* is not a recognised processor mode."""
    if mode not in VALID_PROCESS_MODES:
        raise ValueError(
            f"process_mode must be one of "
            f"{sorted(VALID_PROCESS_MODES)}, "
            f"got '{mode}'"
        )


def resolve_process_mode(process_cfg, frames, process_mode_override=None,
                         debug_trace=None):
    """Resolve the effective processor mode.

    [PIPELINE:PROCESS] Auto-dispatch routes to quality processor when any
    frame exceeds 2x CELL_SIZE (24px).  Standard processor uses 12px CP437
    grid -- frames >24px collapse to too few glyphs, causing catastrophic
    information loss.

    Args:
        process_cfg: dict with optional ``mode`` key (default ``"auto"``).
        frames: list of PIL Images (used for dimension checking).
        process_mode_override: explicit mode string that bypasses config
            and auto-dispatch.  ``None`` means use config / auto.
        debug_trace: optional object with ``.log()`` method for pipeline
            tracing.  ``None`` disables trace logging.

    Returns:
        str: resolved process mode (``"standard"``, ``"quality"``, or
        ``"literal"``).
    """
    requested_mode = str(process_cfg.get("mode", "auto")).strip().lower()

    # --- 1. Explicit override takes precedence --------------------------
    if process_mode_override is not None:
        validate_process_mode(process_mode_override)
        process_mode = process_mode_override

    # --- 2. Config-specified known mode ---------------------------------
    elif requested_mode in VALID_PROCESS_MODES:
        process_mode = requested_mode

    # --- 3. Auto-dispatch by frame dimension ----------------------------
    elif requested_mode == "auto":
        # [PIPELINE:PROCESS] Auto-dispatch: route to quality processor
        # when any frame exceeds 2x CELL_SIZE (24px) in either dimension.
        _auto_threshold = CELL_SIZE * 2
        _max_frame_dim = max(
            (max(f.width, f.height) for f in frames),
            default=0,
        )
        if _max_frame_dim > _auto_threshold:
            print(
                f"   Auto-upgrading to quality processor: "
                f"max frame dim {_max_frame_dim}px exceeds "
                f"2x CELL_SIZE ({_auto_threshold}px)"
            )
            if debug_trace is not None:
                debug_trace.log(
                    "processor_auto_upgrade",
                    max_frame_dim=int(_max_frame_dim),
                    threshold=int(_auto_threshold),
                    old_mode="standard",
                    new_mode="quality",
                )
            process_mode = "quality"
        else:
            print(
                f"   Auto mode: frames <= {_auto_threshold}px, "
                f"using standard processor"
            )
            process_mode = "standard"

    # --- 4. Unrecognised mode -> quality fallback -----------------------
    else:
        print(
            f"   Warning: process mode '{requested_mode}' is not recognised; "
            "falling back to 'quality'."
        )
        process_mode = "quality"

    # --- 5. Fail-closed guard for auto-resolved standard mode -----------
    # Frames exceeding 2x CELL_SIZE in standard mode produce catastrophic
    # info loss.  Only fires when standard was auto-resolved (not
    # explicitly forced via override).
    if process_mode == "standard" and process_mode_override is None:
        _std_threshold = CELL_SIZE * 2
        for _check_frame in frames:
            if (_check_frame.width > _std_threshold
                    or _check_frame.height > _std_threshold):
                raise ValueError(
                    f"Standard processor blocked: frame "
                    f"{_check_frame.width}x{_check_frame.height} exceeds "
                    f"{_std_threshold}px (2x CELL_SIZE={CELL_SIZE}). "
                    f"Use process_mode='quality' or process_mode='auto' "
                    f"for automatic dispatch."
                )

    # --- 6. Standard-legacy remap ---------------------------------------
    if process_mode == "standard-legacy":
        print(
            "   WARNING: standard-legacy is a temporary escape hatch. "
            "Standard processor will be removed in a future release.",
            file=sys.stderr,
        )
        process_mode = "standard"

    # --- 7. Trace log ---------------------------------------------------
    if debug_trace is not None:
        debug_trace.log(
            "processor_mode_resolution",
            requested_mode=requested_mode,
            resolved_mode=process_mode,
            override=process_mode_override,
        )

    return process_mode

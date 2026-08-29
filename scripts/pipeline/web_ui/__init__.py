"""
web_ui -- Web UI assets and utilities for the sprite viewer.

ASSETS:
    assets/cp437_atlas.png  -- 16x16 grid of CP437 glyphs for web rendering
    index.html             -- 6-step wizard shell (Upload/Analyze/Configure/Confirm/Run/Result)
    app.js                 -- Step progression, state management, AssetJobConfig serialization
    styles.css             -- Responsive dark-theme layout

Tags: [FLOW:WEB-UI] [DATA-CONTRACT:CP437] [DATA-CONTRACT:JOB-CONFIG]
"""

from pathlib import Path

# Directory containing web UI static files
WEB_UI_DIR = Path(__file__).parent

# 6-step flow definition (matches app.js STEPS constant)
WEB_STEPS = ("upload", "analyze", "configure", "confirm", "run", "result")


def web_default_config():
    """Return default web UI config dict matching AssetJobConfig schema.

    This function is the Python-side equivalent of app.js createInitialState().config.
    Used by contract parity tests to verify schema alignment.

    Returns:
        dict: Default config values (same keys as AssetJobConfig).
    """
    return {
        "name": "unnamed",
        "asset_type": "custom",
        "source_type": "file",
        "source_path": None,
        "blender_object": None,
        "angles": 1,
        "frames": (1,),
        "projs": 1,
        "transparency": False,
        "normalization": False,
        "target_cells_high": 0,
        "render_resolution": 24,
        "downscale_algorithm": None,
        "template_name": None,
        "slice_spec": None,
        "background": None,
        "slice_mode": "auto",
        "explicit_projs": None,
        "reflection_policy": None,
        "synthesize_angles": None,
        "pre_slice_check": False,
        "pre_slice_check_strict": False,
        "pixel_perfect_mode": "off",
        "keyframe_ranges": None,
    }

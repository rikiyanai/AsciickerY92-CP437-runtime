"""Shared constants for the asset generation service."""

# Default cell size in pixels (CP437 glyph dimensions).
CELL_SIZE = 12

# Canonical render resolution (2x cell size for quality).
DEFAULT_RENDER_RESOLUTION = 24

# Magenta key color used for transparency in sprite sheets.
MAGENTA_RGB = (255, 0, 255)

# Default port for Blender MCP integration.
BLENDER_MCP_PORT = 9876

# Downscaling algorithms for image resizing.
DOWNSCALE_ALGORITHMS = ["nearest", "box", "area", "block-majority"]

# Default downscale algorithm per source type.
DEFAULT_DOWNSCALE_BY_SOURCE = {
    "ai": "box",
    "blender": "nearest",
    "file": "nearest",
}

# ============================================================================
# Engine limits (from sprite.cpp)
# ============================================================================

# Common angle counts (tooling convenience, not hard engine limits).
# Engine can load any angle count that divides height evenly (ENG-06).
# Policy layers (character presets, wizard) may restrict to these values.
COMMON_ANGLES = [1, 4, 8]

# Character preset requirement: 8 angles for full rotation coverage.
# game.cpp rotates characters through 8 compass directions.
CHARACTER_REQUIRED_ANGLES = 8

# Maximum angle count ever observed in production sprites.
# desert_plants family uses 14 angles (blocked by policy, not engine).
MAX_OBSERVED_ANGLES = 14

# ============================================================================
# Tolerance defaults (three distinct purposes -- do NOT unify to one number)
# ============================================================================

# Palette classification: tight tolerance for deciding if a pixel is the
# magenta transparency key.  Used by palette.is_transparent() and
# palette.make_transparency_mask().  Value 5 allows at most 5 total L1
# deviation across RGB channels from (255, 0, 255).
DEFAULT_TRANSPARENCY_TOLERANCE = 5

# User-configurable background matching: moderate tolerance for the
# align_background_to_magenta() flood fill and processor transparency mask.
# Flows through BackgroundSpec.tolerance and --bg-tolerance CLI flag.
DEFAULT_BG_TOLERANCE = 8

# Color correction preprocessing: loose tolerance for snap_to_magenta()
# which runs before quantization.  Higher tolerance catches more near-magenta
# pixels from AI generators / Blender renders without risking false positives
# on actual sprite content (magenta sits at an RGB cube extreme).
DEFAULT_SNAP_TOLERANCE = 15

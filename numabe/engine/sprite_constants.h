// sprite_constants.h
// Sprite pipeline constants - see docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md for archived semantics references
//
// Consistency note: All uses of transparent index (255) across sprite.cpp and
// render.cpp follow the same pattern (palette index comparison). This header
// provides the single source of truth - SPRITE_TRANSPARENT_INDEX.
#pragma once

// ============================================================================
// Layer Indices
// ============================================================================
// These constants define the semantic meaning of XP file layers:
//   Layer 0: Colorkey - background/transparency specification
//   Layer 1: Height - z-height encoding for 2.5D projection
//   Layer 2: Visual - primary rendered appearance
//
// NOTE: These constants are defined for:
//   1. Python alignment - scripts/asset_gen/xp_core.py uses these semantics
//   2. Documentation - makes layer meaning explicit in the codebase
//   3. Future refactoring - could enable layers[SPRITE_LAYER_VISUAL] access
//
// C++ code currently uses pointer arithmetic (layer0, layer1, layer2) rather
// than array indexing, so these constants are not directly referenced in
// sprite.cpp. See [DATA-CONTRACT:SPRITE] comments for pointer arithmetic.

inline constexpr int SPRITE_LAYER_COLORKEY = 0;   // Background / transparency key
inline constexpr int SPRITE_LAYER_HEIGHT = 1;     // Z-height encoding
inline constexpr int SPRITE_LAYER_VISUAL = 2;     // Primary visual data
inline constexpr int SPRITE_MIN_LAYERS = 3;       // Minimum required layers

// ============================================================================
// Palette Sentinels (after RGB quantization)
// ============================================================================

inline constexpr int SPRITE_SWOOSH_INDEX = 254;       // Swoosh marker
inline constexpr int SPRITE_TRANSPARENT_INDEX = 255;  // Transparent

// ============================================================================
// Special Colors (RGB components)
// ============================================================================

// Cyan - swoosh indicator in .xp files
inline constexpr int SPRITE_CYAN_R = 0;
inline constexpr int SPRITE_CYAN_G = 255;
inline constexpr int SPRITE_CYAN_B = 255;

// Magenta - REXPaint transparency indicator
inline constexpr int SPRITE_MAGENTA_R = 255;
inline constexpr int SPRITE_MAGENTA_G = 0;
inline constexpr int SPRITE_MAGENTA_B = 255;

// ============================================================================
// CP437 Glyphs
// ============================================================================

inline constexpr int SPRITE_GLYPH_NULL = 0;
inline constexpr int SPRITE_GLYPH_SPACE = 32;
inline constexpr int SPRITE_GLYPH_FULL_BLOCK = 219;
inline constexpr int SPRITE_GLYPH_HALF_LOWER = 220;
inline constexpr int SPRITE_GLYPH_HALF_LEFT = 221;
inline constexpr int SPRITE_GLYPH_HALF_RIGHT = 222;
inline constexpr int SPRITE_GLYPH_HALF_UPPER = 223;

// ============================================================================
// Half-Block Quadrant Masks
// ============================================================================

inline constexpr int SPRITE_MASK_LOWER = 0x3;   // Bottom two quadrants (glyph 220)
inline constexpr int SPRITE_MASK_LEFT = 0x5;    // Left two quadrants (glyph 221)
inline constexpr int SPRITE_MASK_RIGHT = 0xA;   // Right two quadrants (glyph 222)
inline constexpr int SPRITE_MASK_UPPER = 0xC;   // Top two quadrants (glyph 223)
inline constexpr int SPRITE_MASK_FULL = 0xF;    // All four quadrants (glyph 219)

// ============================================================================
// Rendering Dimensions
// ============================================================================

inline constexpr float SPRITE_ZOOM = 1.0f;
inline constexpr float SPRITE_SCALE = 3.0f;

// ============================================================================
// Palette Quantization
// ============================================================================

inline constexpr int SPRITE_PALETTE_STEP = 51;      // 255/5 for 6-level RGB cube
inline constexpr int SPRITE_PALETTE_ROUND = 25;     // 51/2 for nearest-level rounding
inline constexpr int SPRITE_LIGHTEN_AMOUNT = 51;    // RGB increment for swoosh lightening

// ============================================================================
// Height Encoding
// ============================================================================

inline constexpr int SPRITE_HEIGHT_UNDEFINED = 0xFF;  // Invalid/undefined height marker

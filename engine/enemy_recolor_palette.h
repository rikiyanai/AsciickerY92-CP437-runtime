// enemy_recolor_palette.h — Enemy recolor palette management
//
// PURPOSE:
// Owns the enemy recolor palette data that was previously built inline in
// LoadSprites().
// The palette maps CPC-464-style recolor tables to projection/reflection
// palettes and glyph maps.

#pragma once

#include <stdint.h>

// Maximum palette entries (4-color recolor table).
static const int ENEMY_PALETTE_SIZE = 256;

struct EnemyRecolorPalette
{
    // ── Palette storage ──
    // These are the recolor source table and the derived palette maps.
    static uint8_t recolor[ENEMY_PALETTE_SIZE];
    static uint8_t palette_proj[ENEMY_PALETTE_SIZE];
    static uint8_t palette_refl[ENEMY_PALETTE_SIZE];
    static uint8_t glyph_map[ENEMY_PALETTE_SIZE];

    // ── Build ──
    // Builds all palette maps from the hardcoded recolor table and
    // stores them on this owner.
    static void Build();

    // ── Publish ──
    // Compatibility no-op after FL-4049 bundle-runtime deletion.
    static void PublishToBundle();
};

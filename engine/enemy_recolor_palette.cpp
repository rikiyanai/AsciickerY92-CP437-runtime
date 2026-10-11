// enemy_recolor_palette.cpp — Enemy recolor palette implementation
//
// Extracted from engine/game_render_bridge.cpp (LoadSprites inline).
// SEE ALSO: enemy_recolor_palette.h

#include "enemy_recolor_palette.h"

#include <string.h>

// ── Internal helpers ──

// Quantize an RGB triplet to a CPC-464-style palette index (16 + 36*r + 6*g + b).
// rgb_div controls the quantization range (255 for projection, 400 for reflection).
static uint8_t QuantizeSpritePaletteIndex(const uint8_t rgb[3], int rgb_div)
{
    int r = (rgb[0] * 5 + 128) / rgb_div;
    int g = (rgb[1] * 5 + 128) / rgb_div;
    int b = (rgb[2] * 5 + 128) / rgb_div;
    return (uint8_t)(16 + 36 * r + g * 6 + b);
}

// Build recolor palette maps from a CPC-464-style source table.
// This was previously a non-static function in game_appearance_client.cpp
// (called by both the old LoadSprites and the new palette module).
void BuildEnemyRecolorMaps(const uint8_t* recolor, uint8_t palette_map_proj[256], uint8_t palette_map_refl[256], uint8_t glyph_map[256])
{
    for (int i = 0; i < 256; i++)
    {
        palette_map_proj[i] = (uint8_t)i;
        palette_map_refl[i] = (uint8_t)i;
        glyph_map[i] = (uint8_t)i;
    }
    if (!recolor)
        return;

    for (int i = 0; i < recolor[0]; i++)
    {
        const uint8_t* re_src = recolor + 1 + 6 * i;
        const uint8_t* re_dst = re_src + 3;
        palette_map_proj[QuantizeSpritePaletteIndex(re_src, 255)] = QuantizeSpritePaletteIndex(re_dst, 255);
        palette_map_refl[QuantizeSpritePaletteIndex(re_src, 400)] = QuantizeSpritePaletteIndex(re_dst, 400);
    }

    for (int i = 1 + 6 * recolor[0]; recolor[i]; i += 2)
        glyph_map[recolor[i]] = recolor[i + 1];
}

// ── Static member definitions ──

uint8_t EnemyRecolorPalette::recolor[ENEMY_PALETTE_SIZE] = {};
uint8_t EnemyRecolorPalette::palette_proj[ENEMY_PALETTE_SIZE] = {};
uint8_t EnemyRecolorPalette::palette_refl[ENEMY_PALETTE_SIZE] = {};
uint8_t EnemyRecolorPalette::glyph_map[ENEMY_PALETTE_SIZE] = {};

// ── Build ──

void EnemyRecolorPalette::Build()
{
    // Hardcoded CPC-464-style 4-color recolor table.
    // Format: count(1) + RGB triplets(count*3) + glyph mapping pairs.
    const uint8_t src[] = {
        4,
        170,   0, 170,  153,   0,   0,    0,   0, 170,    0,   0,   0,
         85,  85, 255,   51,  51,  51,  255,  85,  85,  204, 102, 102,
        '@', '#', 'v', '^', '^', 'v', 191, 217, 217, 191, 192, 218, 218, 192, 0, 0
    };

    // Copy source recolor table.
    memcpy(recolor, src, sizeof(src));

    // Build derived maps.
    BuildEnemyRecolorMaps(recolor, palette_proj, palette_refl, glyph_map);

    // Keep palette data local until the ActorVisualProfile renderer owns a
    // concrete recolor consumer.
    PublishToBundle();
}

// ── PublishToBundle ──

void EnemyRecolorPalette::PublishToBundle()
{
}

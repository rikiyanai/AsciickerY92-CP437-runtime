// sprite_registry.cpp — UI sprite registry implementation
//
// Extracted from engine/game_render_bridge.cpp and engine/game.cpp.
// SEE ALSO: sprite_registry.h

#include "sprite_registry.h"
#include "sprite.h"
#include "font1.h"
#include "gamepad.h"
#include "mainmenu.h"
#include "actor_visual_profile_runtime.h"
#include "enemy_recolor_palette.h"
#include "game_input.h"  // keyb_sprite, caps_sprite externs
#include "a3d_load_context.h"

#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

// ── Static member definitions ──

Sprite* SpriteRegistry::character_button = nullptr;
Sprite* SpriteRegistry::inventory_sprite = nullptr;
Sprite* SpriteRegistry::fire_sprite = nullptr;
Sprite* SpriteRegistry::world_preview_sprite = nullptr;

// ── LoadSpriteBP ──

Sprite* SpriteRegistry::LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached)
{
    char path[1024];
    snprintf(path, sizeof(path), "%sassets/sprites/%s", base_path, name);
    return LoadSprite(path, name, recolor, detached);
}

// ── LoadSprites ──

void SpriteRegistry::LoadSprites()
{
#ifdef _WIN32
    _set_printf_count_output(1);
#endif

    LoadFont1();
    LoadGamePad();
    LoadMainMenuSprites(base_path);

    character_button = LoadSpriteBP("character.xp", nullptr, false);
    inventory_sprite = LoadSpriteBP("inventory.xp", nullptr, false);

    keyb_sprite[0] = LoadSpriteBP("keyb-07.xp", nullptr, false);
    keyb_sprite[1] = LoadSpriteBP("keyb-09.xp", nullptr, false);
    keyb_sprite[2] = LoadSpriteBP("keyb-11.xp", nullptr, false);
    keyb_sprite[3] = LoadSpriteBP("keyb-13.xp", nullptr, false);
    keyb_sprite[4] = LoadSpriteBP("keyb-15.xp", nullptr, false);

    caps_sprite[0] = LoadSpriteBP("keyb-caps-a.xp", nullptr, false);
    caps_sprite[1] = LoadSpriteBP("keyb-caps-b.xp", nullptr, false);
    caps_sprite[2] = LoadSpriteBP("keyb-caps-c.xp", nullptr, false);

    fire_sprite = LoadSpriteBP("fire.xp", nullptr, false);

    // FL-4131 Phase 2 backend admission probe.
    // Replaces the Phase 1.1 fail-closed probe with a dual-path diagnostic:
    //   A) fl4131_phase2_valid.xp  → must LOAD successfully (manifest+hash+admission pass)
    //   B) fl4131_extended_demo.xp → must FAIL closed (placeholder hash mismatch)
    //
    // ASCIICKER_FL4131_SKIP_EXTENDED_DEMO_PROBE is only for the paired
    // final-buffer diagnostic capture: control run skips this loader probe,
    {
        const char* skip_probe = getenv("ASCIICKER_FL4131_SKIP_EXTENDED_DEMO_PROBE");
        if (!skip_probe || !skip_probe[0] || strcmp(skip_probe, "0") == 0) {
            char path_a[1024];
            snprintf(path_a, sizeof(path_a),
                     "%sassets/glyphs/fixtures/fl4131_phase2_valid.xp", base_path);
            fprintf(stderr,
                    "[FL-4131] Phase-2 probing extended-glyph admission fixture: %s\n",
                    path_a);
            Sprite* valid = LoadSprite(path_a, "fl4131_phase2_valid",
                                             nullptr, false);
            if (valid != nullptr) {
                fprintf(stderr,
                        "[FL-4131] OK: valid fixture loaded and admitted into GlyphPlane.\n");
                // Verify glyph plane population
                // Verify glyph plane population in the first frame
                if (valid->atlas && valid->atlas[0].glyph_plane && valid->atlas[0].glyph_plane->cells) {
                    GlyphId first_glyph = valid->atlas[0].glyph_plane->cells[0];
                    if (first_glyph == 256) {
                        fprintf(stderr,
                                "[FL-4131] OK: glyph_plane.cells[0] == 256 (EXTENDED_ADMITTED).\n");
                    } else {
                        fprintf(stderr,
                                "[FL-4131] FAIL: glyph_plane.cells[0] expected 256, got %u.\n",
                                (unsigned)first_glyph);
                    }
                } else {
                    fprintf(stderr,
                            "[FL-4131] FAIL: valid fixture has no glyph_plane or empty cells.\n");
                }
            } else {
                fprintf(stderr,
                        "[FL-4131] FAIL: valid fixture did not load (Phase 2 admission broken).\n");
            }

            char path_b[1024];
            snprintf(path_b, sizeof(path_b),
                     "%sassets/glyphs/fixtures/fl4131_extended_demo.xp", base_path);
            fprintf(stderr,
                    "[FL-4131] Phase-2 probing extended-glyph fail-closed fixture: %s\n",
                    path_b);
            Sprite* demo = LoadSprite(path_b, "fl4131_extended_demo",
                                             nullptr, false);
            if (demo == nullptr) {
                fprintf(stderr,
                        "[FL-4131] OK: legacy demo fixture still fail-closed (hash mismatch).\n");
            } else {
                fprintf(stderr,
                        "[FL-4131] FAIL: legacy demo fixture loaded unexpectedly (hash mismatch should reject).\n");
            }
        }
    }

    // Build enemy recolor palette; ActorVisualProfile renderer hookup is future work.
    EnemyRecolorPalette::Build();
}

// ── FreeSprites ──

void SpriteRegistry::FreeSprites()
{
    FreeFont1();
    FreeGamePad();
    FreeMainMenuSprites();
    ClearActorVisualProfileRuntimeCache();

    while (Sprite* s = GetFirstSprite())
        FreeSprite(s);

    character_button = nullptr;
    inventory_sprite = nullptr;
    fire_sprite = nullptr;
    world_preview_sprite = nullptr;
}

// Free-function wrappers for callers that use LoadSprites()/FreeSprites()/LoadSpriteBP() directly
void LoadSprites() { SpriteRegistry::LoadSprites(); }
void FreeSprites() { SpriteRegistry::FreeSprites(); }
Sprite* LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached) { return SpriteRegistry::LoadSpriteBP(name, recolor, detached); }

// sprite_registry.h — UI sprite registry for the game engine
//
// PURPOSE:
// Single owner for game UI sprites that were previously global variables
// scattered across game.cpp, game_input.cpp, and game_render_bridge.cpp.
// LoadSprites()/FreeSprites() lifecycle and LoadSpriteBP() path helper live here.
//
// This is a singleton-style registry (not instantiable) because the sprites
// are loaded once at startup and freed at shutdown.
//
// NOTE: keyb_sprite[5] and caps_sprite[3] live in game_input.cpp because the
// keyboard module (Keyb) owns its own sprite state. They are not part of this
// registry.

#pragma once

#include <stdint.h>

struct Sprite;

struct SpriteRegistry
{
    // ── UI sprite pointers (previously global in game.cpp) ──
    static Sprite* character_button;
    static Sprite* inventory_sprite;
    static Sprite* fire_sprite;
    static Sprite* world_preview_sprite;

    // ── Lifecycle ──
    // Loads all UI sprites from assets/sprites/.
    // Must be called once after base_path is configured.
    static void LoadSprites();

    // Frees all UI sprites. Safe to call multiple times.
    static void FreeSprites();

    // ── Path helper ──
    // Loads a sprite from assets/sprites/<name> relative to base_path.
    static Sprite* LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached);
};

// Free-function aliases so existing callers (game_app.cpp, game_web.cpp,
// asciiid) continue to compile without changes.
void LoadSprites();
void FreeSprites();
Sprite* LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached);

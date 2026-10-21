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
#include <ctype.h>

// "List the files in a folder" support for the world-sprite registration
// pass at the bottom of LoadSprites().
//
// NOTE: we deliberately do NOT use the engine's own a3dListDir() helper
// here. That function is supplied by the windowed platform backends only,
// so calling it would fail to link the terminal build (.run/game_term),
// which has no window system. The plain operating-system calls below are
// available to every build.
#ifdef _WIN32
#include <windows.h>
#else
#include <dirent.h>
#endif

// ── Static member definitions ──

Sprite* SpriteRegistry::character_button = nullptr;
Sprite* SpriteRegistry::inventory_sprite = nullptr;
Sprite* SpriteRegistry::fire_sprite = nullptr;
Sprite* SpriteRegistry::world_preview_sprite = nullptr;
Sprite* SpriteRegistry::ufo_sprite = nullptr;

// ── LoadSpriteBP ──

Sprite* SpriteRegistry::LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached)
{
    char path[1024];
    snprintf(path, sizeof(path), "%sassets/sprites/%s", base_path, name);
    return LoadSprite(path, name, recolor, detached);
}

// ── World sprite registration ────────────────────────────────────────────
//
// WHY THIS EXISTS (FL-4588 regression fix)
// ----------------------------------------
// A saved map (a ".a3d" file) can place "sprite instances" into the world —
// flat billboard actors such as the wolf and the giant bee. Each of those
// records stores only the sprite's FILE NAME, for example "wolfie.xp". It
// does NOT store the picture itself.
//
// So when a map is loaded, the world loader
// (engine/world_serialization_a3d.cpp) has to translate that stored name
// into a real, already-loaded sprite. It does that by walking the list of
// sprites this program has loaded and comparing names.
//
// The MAP EDITOR has always scanned the whole assets/sprites/ folder at
// startup (see SpriteScan in editor/asciiid.cpp), so every name a map can
// mention is guaranteed to be in that list. The GAME never did — it only
// loaded the handful of user-interface sprites listed in LoadSprites()
// below (buttons, keyboard hints, the fire icon, ...).
//
// That difference was harmless for years, because the old world loader
// simply SKIPPED any sprite record whose name it could not resolve.
// Commit b9173d012 (FL-4588) deliberately turned that silent skip into a
// hard rejection — a good change on its own, because silently dropping
// records meant a loaded map quietly contained fewer objects than the file
// said it did. But nothing gave the game the sprite list it now needs.
//
// The consequence: the shipped map (assets/a3d/game_map_y8.a3d places
// "wolfie.xp" and "bigbee.xp") became impossible for the game to load. The
// loader returned NULL, and the caller then crashed dereferencing it.
//
// The fix below gives the game the same up-front registration the editor
// already performs, so a name written by the editor always resolves in the
// game. Both processes now build the same sprite list from the same folder.

// Does this file name end with ".xp" (the sprite image format)?
//
// The comparison ignores upper/lower case, so "WOLFIE.XP" counts too. This
// mirrors the editor's IsSpriteXPFile so the two programs admit exactly the
// same set of files.
//
//   name — a bare file name such as "wolfie.xp" (no folder part)
//   returns true when the name ends in ".xp"
static bool WorldSpriteNameIsXP(const char* name)
{
    if (!name)
        return false;

    const size_t len = strlen(name);
    const size_t suffix_len = 3;  // ".xp" is three characters
    if (len < suffix_len)
        return false;

    // Walk the last three characters and compare them case-insensitively.
    const char* tail = name + (len - suffix_len);
    return tail[0] == '.' &&
           tolower((unsigned char)tail[1]) == 'x' &&
           tolower((unsigned char)tail[2]) == 'p';
}

// Has a sprite under this exact name already been loaded?
//
// LoadSprites() explicitly loads some sprites BEFORE this scan runs, and
// some of them (for example "character.xp") live in the very folder we are
// about to walk. Loading them a second time would put two entries with the
// same name into the global list — wasted memory, and an ambiguous answer
// for the world loader's name lookup. So we skip anything already present.
//
//   name — the file name to look for, compared exactly
//   returns true when the global sprite list already holds that name
static bool WorldSpriteAlreadyRegistered(const char* name)
{
    if (!name)
        return true;  // treat "no name" as already handled: never load it

    for (Sprite* s = GetFirstSprite(); s; s = s->next)
    {
        if (s->name && strcmp(s->name, name) == 0)
            return true;
    }
    return false;
}

// Register one candidate file, if it qualifies.
//
// Shared by both the Windows and the POSIX directory walks below so the
// admission rules stay identical on every platform.
//
//   dirname  — folder the file lives in
//   name     — bare file name, e.g. "wolfie.xp"
//   loaded   — incremented when a sprite was added to the global list
//   failed   — incremented when the file looked like a sprite but not load
static void RegisterOneWorldSprite(const char* dirname, const char* name,
                                   int* loaded, int* failed)
{
    if (!WorldSpriteNameIsXP(name))
        return;

    // Already loaded by the explicit list in LoadSprites()? Leave it alone —
    // that copy may carry a recolour table this scan knows nothing about.
    if (WorldSpriteAlreadyRegistered(name))
        return;

    // Join folder + file name into a full path for the loader.
    char path[4096];
    snprintf(path, sizeof(path), "%s/%s", dirname, name);
    path[sizeof(path) - 1] = 0;

    // Register under the BARE FILE NAME. That is the exact string a map
    // stores for a sprite instance, so this is what makes the world
    // loader's lookup succeed.
    //
    // recolor        = nullptr → no palette substitution
    // detached       = false   → DO add it to the global list (the whole point)
    // quiet_failures = true    → a stray unreadable .xp must not spam the log;
    //                            it simply stays unregistered, exactly as before
    if (LoadSprite(path, name, nullptr, false, true))
        (*loaded)++;
    else
        (*failed)++;
}

// Load every sprite in assets/sprites/ that is not already registered.
//
// Runs once, at startup, from LoadSprites(). Cost is bounded by the folder
// contents (a few hundred small .xp files) and is paid before any map load,
// so it never stalls gameplay.
static void RegisterSpritesInDir(const char* sprite_dirname);

static void RegisterWorldSprites()
{
    char sprite_dirname[1024];
    snprintf(sprite_dirname, sizeof(sprite_dirname), "%sassets/sprites", base_path);
    sprite_dirname[sizeof(sprite_dirname) - 1] = 0;
    RegisterSpritesInDir(sprite_dirname);
}

// FL-4876 SIGN-XP-01: a build ships its own sprites (shop logos converted from
// the capture-review crops) in "<map stem>.sprites/" beside the .a3d. They are
// registered under their bare file names right before the map loads, so the
// map's sprite records resolve exactly like assets/sprites/ entries.
void RegisterMapSprites(const char* a3d_path)
{
    if (!a3d_path || !a3d_path[0])
        return;
    char dirname[1100];
    snprintf(dirname, sizeof(dirname), "%s", a3d_path);
    size_t len = strlen(dirname);
    if (len >= 4 && strcmp(dirname + len - 4, ".a3d") == 0)
        dirname[len - 4] = 0;
    size_t b = strlen(dirname);
    snprintf(dirname + b, sizeof(dirname) - b, "%s", ".sprites");
#ifndef _WIN32
    DIR* probe = opendir(dirname);
    if (!probe)
        return;
    closedir(probe);
#endif
    RegisterSpritesInDir(dirname);
}

static void RegisterSpritesInDir(const char* sprite_dirname)
{
    int loaded = 0;
    int failed = 0;

#ifdef _WIN32
    // Windows: FindFirstFile/FindNextFile want a wildcard pattern.
    char pattern[1100];
    snprintf(pattern, sizeof(pattern), "%s\\*", sprite_dirname);
    pattern[sizeof(pattern) - 1] = 0;

    WIN32_FIND_DATAA find_data;
    HANDLE find = FindFirstFileA(pattern, &find_data);
    if (find != INVALID_HANDLE_VALUE)
    {
        do
        {
            // Skip sub-folders; we only want plain files.
            if (find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
                continue;
            RegisterOneWorldSprite(sprite_dirname, find_data.cFileName,
                                   &loaded, &failed);
        }
        while (FindNextFileA(find, &find_data));
        FindClose(find);
    }
#else
    // macOS / Linux: opendir + readdir.
    DIR* dir = opendir(sprite_dirname);
    if (dir)
    {
        struct dirent* entry;
        while ((entry = readdir(dir)) != nullptr)
        {
            // Skip "." and ".." and any entry known to be a directory.
            // DT_UNKNOWN is possible on some filesystems; in that case we
            // let it through and LoadSprite simply fails on a folder.
            if (entry->d_name[0] == '.')
                continue;
#ifdef DT_DIR
            if (entry->d_type == DT_DIR)
                continue;
#endif
            RegisterOneWorldSprite(sprite_dirname, entry->d_name,
                                   &loaded, &failed);
        }
        closedir(dir);
    }
#endif

    // One summary line. Without this, a map that fails to load because its
    // sprite is missing gives no clue that the sprite list is the reason.
    printf("[SPRITE] world sprite registration: %d loaded, %d unreadable (dir=%s)\n",
           loaded, failed, sprite_dirname);
    fflush(stdout);
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
    ufo_sprite = LoadSpriteBP("ufo.xp", nullptr, false);

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

    // Register every remaining sprite in assets/sprites/ under its bare file
    // name, so sprite instances stored in a map (".a3d") can be resolved by
    // the world loader. See the long note above RegisterWorldSprites().
    //
    // Deliberately runs AFTER the explicit loads above: those set up recolour
    // tables and special names that this generic pass must not overwrite.
    RegisterWorldSprites();

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
    ufo_sprite = nullptr;
}

// Free-function wrappers for callers that use LoadSprites()/FreeSprites()/LoadSpriteBP() directly
void LoadSprites() { SpriteRegistry::LoadSprites(); }
void FreeSprites() { SpriteRegistry::FreeSprites(); }
Sprite* LoadSpriteBP(const char* name, const uint8_t* recolor, bool detached) { return SpriteRegistry::LoadSpriteBP(name, recolor, detached); }

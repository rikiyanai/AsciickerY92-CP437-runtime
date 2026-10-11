// a3d_load_context.cpp — .a3d world/terrain load context implementation
//
// Provides the canonical definitions for base_path, world, terrain, and
// loaded_a3d_path that were previously duplicated across multiple entry
// points (game_app.cpp, game_web.cpp, game.cpp, web_platform.cpp).
//
// SEE ALSO: a3d_load_context.h

#include "a3d_load_context.h"
#include <stdint.h>
#include <stdio.h>
#include "world.h"
#include "terrain.h"

// ── Global definitions ──
// base_path, world, terrain are defined in asciiid.cpp (editor) or game_app.cpp (game).
// Only g_loaded_a3d_path is owned by this TU.
extern char base_path[];
char g_loaded_a3d_path[1024] = "";
extern World* world;
extern Terrain* terrain;

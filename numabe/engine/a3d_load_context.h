// a3d_load_context.h — .a3d world/terrain load context
//
// PURPOSE:
// Canonical definition site for the global .a3d loading state (base_path,
// world, terrain, loaded_a3d_path) that was previously duplicated across
// multiple entry points (game_app.cpp, game_web.cpp, game.cpp, web_platform.cpp).
//
// Every TU that references these globals should include this header instead
// of declaring its own extern.

#pragma once

struct World;
struct Terrain;

// ── Globals ──

// Base path for asset resolution (points to the directory containing assets/).
// Set by the platform entry point; defaults to "./".
extern char base_path[1024];

// Resolved path of the last loaded .a3d file (for UI display, screenshot naming).
// Kept as g_loaded_a3d_path for compatibility with existing extern declarations.
extern char g_loaded_a3d_path[1024];

// Active world and terrain singletons.
extern World* world;
extern Terrain* terrain;

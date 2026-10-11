#pragma once

// =============================================================================
// Offscreen render harness — scriptable scenes rendered by the real engine
// =============================================================================
//
// WHAT THIS IS
//   A small native binary that links the engine's software renderer
//   (engine/render/*), sprite loader (engine/sprite.cpp), terrain, world/BSP
//   and the A3D loader, but NOT game_app / V8 / the server / networking.
//   Each frame is produced by the same ::Render() call the game makes in
//   engine/game_render_bridge.cpp, into the same AnsiCell buffer
//   (CP437 glyph + xterm-256 fg/bg per cell). The cells are then rasterised
//   to PNG with the repo's CP437 font atlas (assets/fonts/cp437_NxN.png)
//   using the same palette decode as the GL/web cell shaders.
//
// WHAT THIS IS NOT
//   Not gameplay. No physics, no server authority, no actor visual profile
//   resolution, no HUD. Sprites are loaded directly from .xp files with the
//   engine loader and placed as world sprite instances; the scene script
//   decides anim/frame per tick.
//
// SCENE MODEL (for reuse, e.g. a UFO rapture recording)
//   A scene is a HarnessScene registered by name (see HARNESS_REGISTER_SCENE).
//   It gets three callbacks:
//     setup(h, args)        load map/sprites, add actors and props
//     tick(h, tick)         per-frame script: move camera, set actor
//                           sprite/anim/frame/pos/dir, show/hide props
//     post_render(h, ...)   optional extra render passes over the final
//                           AnsiCell buffer (overlays, beams, text, ...)
//   Actors and props are the same thing: a world sprite instance at an
//   arbitrary world position (any Z), drawn by the engine's RenderSprite with
//   the engine's own direction selection (FacingSpriteAngleIndex).
// =============================================================================

#include <stdint.h>
#include <string>
#include <vector>
#include <map>
#include <functional>

#include "render_api.h"

struct Sprite;
struct Inst;
struct Terrain;
struct World;

// Camera tuple passed straight to ::Render(). Defaults mirror the game.
struct HarnessCamera
{
	float pos[3] = { 0, 0, 0 };     // world units; z in height units (game: player z)
	float yaw = -57.4f;             // GetDefaultGameStart() spawn yaw
	float zoom = 1.0f;              // game_render_bridge passes 1.0f to ::Render
	float water = 55.0f;            // GetDefaultGameStart() water level
	float light[4] = { 1, 0, 1, 0.5f }; // GetDefaultGameStart() light, normalised like Game::Render
	bool perspective = false;       // session.perspective default
	int scene_shift[2] = { 0, 0 };
};

// One world sprite instance (character or prop).
struct HarnessActor
{
	std::string name;
	Sprite* sprite = nullptr;
	Inst* inst = nullptr;
	float pos[3] = { 0, 0, 0 };
	float dir = 0.0f;   // legacy sprite-compensated facing (S=0, E=90, N=180, W=-90)
	int anim = 0;       // anim track (body sheets: 0 = idle, 1 = walk)
	int frame = 0;      // frame within the anim track
	bool visible = true;
};

typedef std::map<std::string, std::string> HarnessArgs;

struct Harness
{
	// configuration
	std::string repo_root;   // base path for relative asset paths (ends with '/')
	int width = 40;          // cells
	int height = 24;         // cells
	int fps = 30;
	int frames = 210;
	std::string font_path;   // CP437 atlas PNG
	std::string out_dir;     // per-frame PNGs are written here
	bool dump_cells = false; // also write raw AnsiCell dumps (.cells)

	// engine state
	Renderer* renderer = nullptr;
	HarnessCamera camera;
	std::vector<HarnessActor> actors;
	// Optional per-frame RenderFrameInput handed to ::Render (the same struct
	// game_render_bridge.cpp fills). Null = no game context (turntable).
	const RenderFrameInput* frame_input = nullptr;

	// current frame buffer (valid inside post_render)
	std::vector<AnsiCell> cells;

	// API for scenes ---------------------------------------------------------
	bool LoadMap(const std::string& a3d_path);
	Sprite* LoadSpriteXp(const std::string& xp_path, int layer = 2);
	int AddActor(const std::string& name, Sprite* s, const float pos[3], float dir,
		int anim = 0, int frame = 0);
	HarnessActor* Actor(const std::string& name);
	float GroundZ(float x, float y) const;
	std::string Path(const std::string& rel) const;

	// Render one frame through ::Render into `cells` (no file output).
	void RenderFrame(int tick);
};

struct HarnessScene
{
	const char* name;
	const char* help;
	std::function<bool(Harness*, const HarnessArgs&)> setup;
	std::function<void(Harness*, int tick)> tick;
	std::function<void(Harness*, AnsiCell* cells, int w, int h, int tick)> post_render;
};

std::vector<HarnessScene>& HarnessSceneRegistry();

struct HarnessSceneRegistrar
{
	HarnessSceneRegistrar(const HarnessScene& s) { HarnessSceneRegistry().push_back(s); }
};
#define HARNESS_REGISTER_SCENE(var, ...) static HarnessSceneRegistrar var##_registrar(HarnessScene{ __VA_ARGS__ })

// Arg helpers
std::string HarnessArg(const HarnessArgs& a, const char* key, const char* def);
float HarnessArgF(const HarnessArgs& a, const char* key, float def);
int HarnessArgI(const HarnessArgs& a, const char* key, int def);

// Cell rasteriser (CP437 atlas + xterm-256 cube, as the GL/web cell shaders)
struct HarnessFont
{
	int cell_w = 0, cell_h = 0;
	std::vector<uint8_t> mask; // 16x16 glyph grid, luminance as glyph alpha
	int atlas_w = 0;
	bool Load(const char* png_path);
};
void HarnessRasterize(const HarnessFont& font, const AnsiCell* cells, int w, int h, std::vector<uint8_t>& rgb);
bool HarnessWritePng(const char* path, const uint8_t* rgb, int w, int h);

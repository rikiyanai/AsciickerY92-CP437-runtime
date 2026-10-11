// game_web.cpp - WebAssembly/Emscripten Entry Point
//
// PURPOSE: WebAssembly platform implementation for browser deployment. Bridges C++ game engine
// to browser APIs via Emscripten, using virtual filesystem (IndexedDB), browser JavaScript engine,
// and cooperative main loop scheduling (requestAnimationFrame).
//
// PLATFORM-SPECIFIC FEATURES:
// - Browser JavaScript Engine: Uses browser's built-in JS engine (V8/SpiderMonkey/JavaScriptCore)
//   instead of embedded V8. NPC scripts run in same JS context as browser.
// - Virtual Filesystem: /data/ directory mounted as IDBFS (IndexedDB File System) for persistence.
//   FS.syncfs() required to flush writes to IndexedDB (asynchronous).
// - Cooperative Main Loop: emscripten_set_main_loop() schedules frame callbacks via requestAnimationFrame.
//   WHY: Browser event loop requires cooperative scheduling - cannot block in while(1).
// - WebGL Rendering: Browser's WebGL context instead of native OpenGL.
// - Browser Input: Keyboard/mouse/gamepad events from browser DOM.
// - Emscripten EM_ASM: Inline JavaScript code execution from C++ for browser API access.
//
// INITIALIZATION ORDER:
// 1. EM_ASM: Initialize browser-side state (canvas, input listeners, gamepad polling)
// 2. Mount IDBFS: FS.mount('/data', {}, IDBFS) → creates virtual filesystem backed by IndexedDB
// 3. Sync from IndexedDB: FS.syncfs(true) → loads saved config/worlds from browser storage
// 4. Load world data: Read .a3d/.xp/.akm files from /data/ virtual filesystem
// 5. Initialize rendering: WebGL context, shader compilation, texture upload
// 6. Initialize physics: Collision detection, character controller
// 7. Set main loop: emscripten_set_main_loop(frame_callback, 0, 1) → browser drives frame timing
//
// MAIN LOOP PATTERN:
// - emscripten_set_main_loop(callback, fps, simulate_infinite_loop)
// - Browser calls callback every frame via requestAnimationFrame
// - WHY COOPERATIVE: Browser must process events, garbage collect, update DOM between frames
// - CONTRAST NATIVE: Native platforms use while(1) blocking loop with explicit sleep
// - CONTRAST SERVER: Server uses tick-based network message loop
//
// VIRTUAL FILESYSTEM (IDBFS):
// - /data/ directory persists across browser sessions via IndexedDB
// - FS.syncfs(false, callback): Write virtual filesystem changes to IndexedDB (async)
// - FS.syncfs(true, callback): Load IndexedDB contents into virtual filesystem (async)
// - WHY ASYNC: IndexedDB API is asynchronous - must use callbacks
// - WHY /data/: Convention - Emscripten mounts persistent storage at /data/
//
// EMSCRIPTEN SPECIFICS:
// - EM_ASM({ js_code }): Inline JavaScript executed immediately
// - EM_ASM_INT({ return value; }): Inline JavaScript returning integer
// - EMSCRIPTEN_KEEPALIVE: Prevent function from being dead-code eliminated
// - Module.HEAPF32/HEAP32/HEAPU8: JavaScript views of WASM linear memory
// - Exported functions: C++ functions callable from JavaScript via Module._functionName
//
// PLATFORM COMPARISON:
// ┌─────────────────┬────────────────────┬────────────────────┬───────────────────────┐
// │ Feature         │ game_app.cpp       │ game_web.cpp       │ game_svr.cpp          │
// │                 │ (Native Desktop)   │ (WebAssembly)      │ (Headless Server)     │
// ├─────────────────┼────────────────────┼────────────────────┼───────────────────────┤
// │ JavaScript      │ V8 (embedded)      │ Browser JS engine  │ None (no scripting)   │
// │ Filesystem      │ Native OS          │ Virtual (IndexedDB)│ Native OS             │
// │ Rendering       │ Native OpenGL      │ WebGL              │ None (headless)       │
// │ Main Loop       │ while(1) blocking  │ emscripten_set_    │ tick-based network    │
// │                 │                    │ main_loop          │ message loop          │
// │ Input           │ SDL/X11/Win32      │ Browser events     │ Network only          │
// │ Networking      │ Native sockets     │ WebSocket          │ Native sockets        │
// │ Audio           │ Native/SDL         │ Web Audio API      │ None (headless)       │
// │ Haptics         │ SDL gamepad rumble │ Vibration API      │ None                  │
// │ Config Storage  │ ./asciicker.cfg    │ /data/asciicker.cfg│ ./asciicker.cfg       │
// │                 │ (native file)      │ (IndexedDB mount)  │ (native file)         │
// └─────────────────┴────────────────────┴────────────────────┴───────────────────────┘
//
// KEY FUNCTIONS:
// - GetTime(): High-resolution timestamp delegating to a3dGetTime() (platform.h contract)
// - SyncConf(): FS.syncfs(false) → flush config changes to IndexedDB
// - Buzz(): Vibration API for mobile haptics, gamepad vibration for desktop browsers
// - GetConfPath(): Returns /data/asciicker.cfg (virtual filesystem path)
// - Server::Send(): WebSocket send via EM_ASM JavaScript bridge
//
// KEY FILES:
// - game.cpp: Shared game logic (platform-independent)
// - game_api.cpp: JavaScript <-> C++ bridge (browser JS instead of V8)
// - game_web.html: HTML harness with canvas, JavaScript bootstrap, WASM loading
// - render.cpp: OpenGL rendering (compiled to WebGL via Emscripten)
// - game_app.cpp: Native desktop entry point (cross-reference for differences)
// - game_svr.cpp: Headless server entry point (cross-reference for differences)
//
// CROSS-REFERENCES:
// - See game_app.cpp header for native desktop platform differences (V8, blocking loop)
// - See game_svr.cpp header for headless server platform differences
// - See game_api.cpp header for JavaScript bridge architecture

#include <emscripten.h>
#include <math.h>
#include <stdint.h>

#include "terrain.h"
#include "game.h"
#include "game_combat_client.h"
#include "sprite_registry.h"
#include "enemygen.h"
#include "sprite.h"
#include "world.h"
#include "render.h"
#include "mainmenu.h"

#include "audio.h"
#include "fast_rand.h"
#include "web_recorder_bridge.h"
#include "web_filesystem.h"
#include "web_diagnostics.h"
#include "web_network_client.h"

#include "game_api.h"
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <math.h>

// base_path, Buzz, GetTime, a3dGetTime, MakeStamp are owned by web_platform.cpp
#include "web_platform.h"
#include "web_disconnect_witness.h"

// S2/FL-933: volatile is load-to-translate-origin caching fix (LTO). Do not remove.
// Bootstrap Server* pointer consolidation and join-active gate are the closed fix family.

// Web implementation of multiplayer server
// send_buf holds outgoing network messages (WEB_OUTBOUND_MSG_MAX + 2-byte length prefix)
// Global game state pointers
Game* game = 0;          // Main game logic controller
Terrain* terrain = 0;    // Terrain/heightmap data
World* world = 0;        // 3D world objects and entities
AnsiCell* render_buf = 0; // ASCII character buffer for rendering (width * height cells)
// FL-4131 PHASE_5_SIDECAR_ALLOC — extended GlyphId parallel buffer.
// Mirrors render_buf semantics exactly: 160*160 upper bound; the engine renderer
// (and any companion writer) populates the first render_w*render_h cells row-major,
// matching engine/render/render_rasterize.h `buf[w*y+x]` indexing. Per-cell byte
// layout in WASM memory (sampled as RGBA8 by the WebGL sidecar_tex):
//   byte0 = R = (GlyphId >> 8) & 0xFF   — high byte
//   byte1 = G =  GlyphId       & 0xFF   — low byte
//   byte2 = B = reserved (0)
//   byte3 = A = 0xFF for nonzero GlyphIds
// Zero = sentinel "no extended glyph; use CP437 byte path." AnsiCell.gl stays
// uint8 CP437 — the sidecar is the ONLY carrier for ids > 255 (FL-4131 hard rule).
uint32_t* glyph_sidecar = 0;
static const int GLYPH_SIDECAR_W = 160;
static const int GLYPH_SIDECAR_H = 160;

// FL-4131 Phase D recorder event surface. JS owns WebGL pixel/uniform
// observation; C owns the recorder JSON. This state is observational only and
// is updated after draw when the shader fallback path was actually sampled.
uint32_t g_fl4131_fallback_render_event_count = 0;
uint32_t g_fl4131_fallback_render_last_glyph_id = 0;
uint32_t g_fl4131_fallback_render_last_fallback_glyph_id = 0;
uint32_t g_fl4131_fallback_render_last_lut_width = 0;
uint32_t g_fl4131_fallback_render_last_red_pixels = 0;
uint32_t g_fl4131_fallback_render_last_black_pixels = 0;

// FL-4131 PHASE_5_SIDECAR_ALLOC — writer helper. Used by the engine renderer hook
// and (env-gated) Phase D test injection. (x,y) are in render-grid coordinates
// (0..render_w, 0..render_h). Out-of-range or sentinel-zero calls are no-ops.
static inline void GlyphSidecarWrite(int x, int y, int render_w, int render_h, uint32_t glyph_id)
{
    if (!glyph_sidecar) return;
    if (x < 0 || y < 0 || x >= render_w || y >= render_h) return;
    if (render_w <= 0 || render_w > GLYPH_SIDECAR_W) return;
    if (render_h <= 0 || render_h > GLYPH_SIDECAR_H) return;
    uint8_t* p = (uint8_t*)&glyph_sidecar[y * render_w + x];
    p[0] = (uint8_t)((glyph_id >> 8) & 0xFF); // R = hi
    p[1] = (uint8_t)( glyph_id       & 0xFF); // G = lo
    p[2] = 0;                                  // B reserved
    p[3] = glyph_id ? 0xFF : 0;                // A distinguishes real sidecar texels from sentinel zero
}

extern "C" void WebGlyphSidecarBeginFrame(int render_w, int render_h)
{
    if (!glyph_sidecar) return;
    if (render_w <= 0 || render_w > GLYPH_SIDECAR_W) return;
    if (render_h <= 0 || render_h > GLYPH_SIDECAR_H) return;
    memset(glyph_sidecar, 0, (size_t)render_w * (size_t)render_h * sizeof(uint32_t));
}

extern "C" void WebRenderGlyphSidecarWrite(int x, int y, int render_w, int render_h, uint32_t glyph_id)
{
    GlyphSidecarWrite(x, y, render_w, render_h, glyph_id);
}
extern Character* player_head; // from game.cpp character list (local player + NPCs)
extern Character* player_tail; // from game.cpp character list (local player + NPCs)
extern int g_web_render_stage_code; // from game.cpp render-stage instrumentation
extern Game* prime_game; // from game.cpp primary runtime pointer used by Server::Proc()
extern "C" uint32_t GameFL933RenderStageAddr32();
extern "C" uint32_t GameFL933CrossTuProbeAddr32();
extern "C" uint32_t GameFL933CrossTuProbe(uint32_t salt);
extern "C" void MainMenuResetWebLoadingState();
extern "C" int MainMenuWebGameLoadingState();
extern "C" int MainMenuWebProgressState();

// g_web_client_render_duration_us is defined in web_platform.cpp
extern uint32_t g_web_client_render_duration_us;







// StoreWebPlayerName is owned by web_platform.cpp

// WebDestroyGameServerAllocation is owned by web_network_client.cpp

// S2/FL-933: bootstrap/reconnect lifecycle was rebuilt over 36 iterations (Apr 21).
// game_loading stale-on-reconnect (FL-933) and hard_disconnect scope (FL-934) are fixed.
// Do NOT revive pre-Join pending-queue HOL as the current post-join lag owner —
// that hypothesis was ruled out (passive-20260421-050250). Post-join lag lives in U2.
// ResetWebRuntimeSession is owned by web_network_client.cpp

// WebLogServerNullAfterJoin is owned by web_network_client.cpp

// WebRuntimeCanApplyAuthoritativeWorldPackets / WebShouldDeferAuthoritativeWorldPacket are owned by web_network_client.cpp

// QueuePendingNetPacket is owned by web_network_client.cpp

// FlushPendingNetPacketsToServer / WebFlushPendingNetPacketsToServer are owned by web_network_client.cpp

// CountRemotePlayers is owned by web_network_client.cpp





// Material definitions (256 possible terrain materials)
Material mat[256];
void* GetMaterialArr()
{
    return mat;
}

// Initialize all 256 terrain material definitions with colors, glyphs, and shading
// Materials 0-8 are predefined (water, grass, dirt, stone, sand, snow, mud, cobblestone, gravel)
// Materials 9-255 are random placeholders for future use
void InitMaterials()
{
	// Fast random number generator for placeholder materials
	// Using LCG (Linear Congruential Generator) algorithm
	static uint32_t fast_rand_seed = 123456789;
	auto fast_rand = []() -> uint32_t {
		fast_rand_seed = (1103515245 * fast_rand_seed + 12345) & 0x7FFFFFFF;
		return fast_rand_seed;
	};

	// MATERIAL 0: WATER - animated water surface with waves
	// Uses 4 randomness levels (r) and 16 shade levels (s) for variety
	uint8_t water_glyphs[4] = {',',' ','!',' '};  // Different wave patterns
	uint8_t water_fg[4] = {0xFF,0xA0,0x64,0x00};  // Grayscale wave highlights
	for (int s=0; s<16; s++)  // s = shade level (0=bright, 15=dark)
	{
		for (int r=0; r<4; r++)  // r = randomness/pattern variant
		{
			mat[0].shade[r][s].fg[0]=water_fg[r];  // Foreground RGB (glyph color)
			mat[0].shade[r][s].fg[1]=water_fg[r];
			mat[0].shade[r][s].fg[2]=water_fg[r];
			mat[0].shade[r][s].gl = water_glyphs[r];  // ASCII glyph character
			mat[0].shade[r][s].bg[0]=0xCF;  // Background RGB (light blue/cyan)
			mat[0].shade[r][s].bg[1]=0xCF;
			mat[0].shade[r][s].bg[2]=0xCF;
			mat[0].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 1: GRASS - green terrain with grass blade glyphs
	uint8_t grass_bg_base[3] = {34, 139, 34};   // Forest green background
	uint8_t grass_fg_base[3] = {144, 238, 144}; // Light green foreground
	uint8_t grass_glyphs[4] = {'"', '\'', '"', '`'}; // Grass blade characters
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			// Darken as shade level increases (0=full bright, 15=40% brightness)
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[1].shade[r][s].bg[0] = (uint8_t)(grass_bg_base[0] * shade_factor);
			mat[1].shade[r][s].bg[1] = (uint8_t)(grass_bg_base[1] * shade_factor);
			mat[1].shade[r][s].bg[2] = (uint8_t)(grass_bg_base[2] * shade_factor);
			mat[1].shade[r][s].fg[0] = (uint8_t)(grass_fg_base[0] * shade_factor);
			mat[1].shade[r][s].fg[1] = (uint8_t)(grass_fg_base[1] * shade_factor);
			mat[1].shade[r][s].fg[2] = (uint8_t)(grass_fg_base[2] * shade_factor);
			mat[1].shade[r][s].gl = grass_glyphs[r];
			mat[1].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 2: DIRT - brown earthy terrain
	uint8_t dirt_bg_base[3] = {101, 67, 33};   // Dark brown
	uint8_t dirt_fg_base[3] = {160, 120, 80};  // Lighter brown
	uint8_t dirt_glyphs[4] = {'.', ':', ',', '\''}; // Small debris/particle glyphs
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[2].shade[r][s].bg[0] = (uint8_t)(dirt_bg_base[0] * shade_factor);
			mat[2].shade[r][s].bg[1] = (uint8_t)(dirt_bg_base[1] * shade_factor);
			mat[2].shade[r][s].bg[2] = (uint8_t)(dirt_bg_base[2] * shade_factor);
			mat[2].shade[r][s].fg[0] = (uint8_t)(dirt_fg_base[0] * shade_factor);
			mat[2].shade[r][s].fg[1] = (uint8_t)(dirt_fg_base[1] * shade_factor);
			mat[2].shade[r][s].fg[2] = (uint8_t)(dirt_fg_base[2] * shade_factor);
			mat[2].shade[r][s].gl = dirt_glyphs[r];
			mat[2].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 3: STONE - gray rocky terrain
	uint8_t stone_bg_base[3] = {105, 105, 105}; // Dim gray
	uint8_t stone_fg_base[3] = {169, 169, 169}; // Light gray
	uint8_t stone_glyphs[4] = {'#', 'O', '8', '@'}; // Dense rock texture characters
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[3].shade[r][s].bg[0] = (uint8_t)(stone_bg_base[0] * shade_factor);
			mat[3].shade[r][s].bg[1] = (uint8_t)(stone_bg_base[1] * shade_factor);
			mat[3].shade[r][s].bg[2] = (uint8_t)(stone_bg_base[2] * shade_factor);
			mat[3].shade[r][s].fg[0] = (uint8_t)(stone_fg_base[0] * shade_factor);
			mat[3].shade[r][s].fg[1] = (uint8_t)(stone_fg_base[1] * shade_factor);
			mat[3].shade[r][s].fg[2] = (uint8_t)(stone_fg_base[2] * shade_factor);
			mat[3].shade[r][s].gl = stone_glyphs[r];
			mat[3].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 4: SAND - desert/beach terrain
	uint8_t sand_bg_base[3] = {194, 178, 128}; // Tan/beige
	uint8_t sand_fg_base[3] = {238, 232, 170}; // Pale goldenrod
	uint8_t sand_glyphs[4] = {' ', '.', ':', ','}; // Fine grain textures
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[4].shade[r][s].bg[0] = (uint8_t)(sand_bg_base[0] * shade_factor);
			mat[4].shade[r][s].bg[1] = (uint8_t)(sand_bg_base[1] * shade_factor);
			mat[4].shade[r][s].bg[2] = (uint8_t)(sand_bg_base[2] * shade_factor);
			mat[4].shade[r][s].fg[0] = (uint8_t)(sand_fg_base[0] * shade_factor);
			mat[4].shade[r][s].fg[1] = (uint8_t)(sand_fg_base[1] * shade_factor);
			mat[4].shade[r][s].fg[2] = (uint8_t)(sand_fg_base[2] * shade_factor);
			mat[4].shade[r][s].gl = sand_glyphs[r];
			mat[4].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 5: SNOW - white/icy terrain
	uint8_t snow_bg_base[3] = {230, 240, 255}; // Light blue-white
	uint8_t snow_fg_base[3] = {255, 255, 255}; // Pure white
	uint8_t snow_glyphs[4] = {'*', '+', '.', ' '}; // Snowflake and frost patterns
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			// Snow stays brighter (only 50% darkening at max shade)
			float shade_factor = 1.0f - (s / 16.0f) * 0.5f;
			mat[5].shade[r][s].bg[0] = (uint8_t)(snow_bg_base[0] * shade_factor);
			mat[5].shade[r][s].bg[1] = (uint8_t)(snow_bg_base[1] * shade_factor);
			mat[5].shade[r][s].bg[2] = (uint8_t)(snow_bg_base[2] * shade_factor);
			mat[5].shade[r][s].fg[0] = (uint8_t)(snow_fg_base[0] * shade_factor);
			mat[5].shade[r][s].fg[1] = (uint8_t)(snow_fg_base[1] * shade_factor);
			mat[5].shade[r][s].fg[2] = (uint8_t)(snow_fg_base[2] * shade_factor);
			mat[5].shade[r][s].gl = snow_glyphs[r];
			mat[5].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 6: MUD - dark wet soil
	uint8_t mud_bg_base[3] = {64, 46, 30};  // Very dark brown
	uint8_t mud_fg_base[3] = {96, 70, 46};  // Slightly lighter brown
	uint8_t mud_glyphs[4] = {'~', '=', '-', '.'}; // Wavy/wet surface patterns
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			// Mud gets very dark (70% darkening at max shade)
			float shade_factor = 1.0f - (s / 16.0f) * 0.7f;
			mat[6].shade[r][s].bg[0] = (uint8_t)(mud_bg_base[0] * shade_factor);
			mat[6].shade[r][s].bg[1] = (uint8_t)(mud_bg_base[1] * shade_factor);
			mat[6].shade[r][s].bg[2] = (uint8_t)(mud_bg_base[2] * shade_factor);
			mat[6].shade[r][s].fg[0] = (uint8_t)(mud_fg_base[0] * shade_factor);
			mat[6].shade[r][s].fg[1] = (uint8_t)(mud_fg_base[1] * shade_factor);
			mat[6].shade[r][s].fg[2] = (uint8_t)(mud_fg_base[2] * shade_factor);
			mat[6].shade[r][s].gl = mud_glyphs[r];
			mat[6].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 7: COBBLESTONE - rounded stone pavement
	uint8_t cobble_bg_base[3] = {112, 128, 144}; // Slate gray
	uint8_t cobble_fg_base[3] = {176, 196, 222}; // Light steel blue
	uint8_t cobble_glyphs[4] = {'o', 'O', '0', '@'}; // Rounded stone shapes
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[7].shade[r][s].bg[0] = (uint8_t)(cobble_bg_base[0] * shade_factor);
			mat[7].shade[r][s].bg[1] = (uint8_t)(cobble_bg_base[1] * shade_factor);
			mat[7].shade[r][s].bg[2] = (uint8_t)(cobble_bg_base[2] * shade_factor);
			mat[7].shade[r][s].fg[0] = (uint8_t)(cobble_fg_base[0] * shade_factor);
			mat[7].shade[r][s].fg[1] = (uint8_t)(cobble_fg_base[1] * shade_factor);
			mat[7].shade[r][s].fg[2] = (uint8_t)(cobble_fg_base[2] * shade_factor);
			mat[7].shade[r][s].gl = cobble_glyphs[r];
			mat[7].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 8: GRAVEL - loose rocky aggregate
	uint8_t gravel_bg_base[3] = {150, 150, 150}; // Medium gray
	uint8_t gravel_fg_base[3] = {190, 190, 190}; // Light gray
	uint8_t gravel_glyphs[4] = {'.', ':', ';', ','}; // Small pebble textures
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
			float shade_factor = 1.0f - (s / 16.0f) * 0.6f;
			mat[8].shade[r][s].bg[0] = (uint8_t)(gravel_bg_base[0] * shade_factor);
			mat[8].shade[r][s].bg[1] = (uint8_t)(gravel_bg_base[1] * shade_factor);
			mat[8].shade[r][s].bg[2] = (uint8_t)(gravel_bg_base[2] * shade_factor);
			mat[8].shade[r][s].fg[0] = (uint8_t)(gravel_fg_base[0] * shade_factor);
			mat[8].shade[r][s].fg[1] = (uint8_t)(gravel_fg_base[1] * shade_factor);
			mat[8].shade[r][s].fg[2] = (uint8_t)(gravel_fg_base[2] * shade_factor);
			mat[8].shade[r][s].gl = gravel_glyphs[r];
			mat[8].shade[r][s].flags = 0;
		}
	}

	// MATERIALS 9-255: RANDOM PLACEHOLDERS
	// Fill remaining material slots with random colors/glyphs for future use
	for (int i = 9; i < 256; i++)
	{
		for (int r = 0; r < 4; r++)
		{
			for (int s = 0; s < 16; s++)
			{
				mat[i].shade[r][s].bg[0] = fast_rand() & 0xFF;  // Random RGB background
				mat[i].shade[r][s].bg[1] = fast_rand() & 0xFF;
				mat[i].shade[r][s].bg[2] = fast_rand() & 0xFF;
				mat[i].shade[r][s].fg[0] = fast_rand() & 0xFF;  // Random RGB foreground
				mat[i].shade[r][s].fg[1] = fast_rand() & 0xFF;
				mat[i].shade[r][s].fg[2] = fast_rand() & 0xFF;
				mat[i].shade[r][s].gl = fast_rand() & 0xFF;     // Random ASCII character
				mat[i].shade[r][s].flags = 0;
			}
		}
	}
}

// Decrease font size / zoom out
// Returns true if zoom level changed, false if already at minimum
bool PrevGLFont()
{
    return EM_ASM_INT({return ZoomOut();}) != 0;
}

// Increase font size / zoom in
// Returns true if zoom level changed, false if already at maximum
bool NextGLFont()
{
    return EM_ASM_INT({return ZoomIn();}) != 0;
}

// exit_handler is owned by web_disconnect_witness.cpp

// Toggle fullscreen mode (F11 key or fullscreen button)
// Enters fullscreen if windowed, exits if already fullscreen
void ToggleFullscreen(Game* g)
{
    (void)g;
    EM_ASM(
    {
        let elem = document.body;

        if (!document.fullscreenElement)
        {
            // Enter fullscreen mode
            if ("requestFullscreen" in elem)
                elem.requestFullscreen().catch(err => { });  // Silently ignore if denied
        }
        else
        {
            // Exit fullscreen mode
            if ("exitFullscreen" in document)
                document.exitFullscreen();
        }
    });
}

// Check if game is currently in fullscreen mode
bool IsFullscreen(Game* g)
{
    (void)g;
    int fs =
    EM_ASM_INT(
    {
        let elem = document.body;

        if (!document.fullscreenElement)
        {
            return 0;  // Windowed mode
        }
        else
        {
            return 1;  // Fullscreen mode
        }
    });

    return fs!=0;
}

void ToggleFullscreen()
{
    ToggleFullscreen(game);
}

bool IsFullscreen()
{
    return IsFullscreen(game);
}


// Entry point - not used in web build (Emscripten calls Load() directly)
int main(int argc, char* argv[])
{
    return 0;
}

// Execute JavaScript code from C++ (akAPI scripting system)
// If root=true: execute in global scope with full access
// If root=false: execute in sandboxed scope with limited global access
void akAPI_Exec(const char* str, int len, bool root)
{
    uint64_t t0 = GetTime();

    if (root)
    {
        // Root mode: full access to window globals
        EM_ASM(
        {
            try
            {
                let str = $1 < 0 ? UTF8ToString($0) : UTF8ToString($0,$1);
                Function("'use strict';\n" + str).apply(window);
            }
            catch(e)
            {
                console.log("Exception: "+e.name+" "+e.message+" "+$2);
            }
        }, str, len, root);
    }
    else
    {
        // Sandboxed mode: limited globals for security
        EM_ASM(
        {
            globalThis=window.akAPI_This;  // Temporarily redirect globalThis
            try
            {
                let str = $1 < 0 ? UTF8ToString($0) : UTF8ToString($0,$1);
                window.akAPI_Prot[akAPI_Prot.length-1] = "'use strict';\n" + str;
                Function.apply(this,akAPI_Prot).apply(window.akAPI_This,[ak]);
            }
            catch(e)
            {
                console.log("Exception: "+e.name+" "+e.message+" "+$2);
            }
            globalThis=window;  // Restore globalThis
        }, str, len, root);
    }

    uint64_t t1 = GetTime();
    printf("COMPILE+EXECUTE IN %dus\n",(int)(t1-t0));
}

// Trigger JavaScript callback by ID (for async operations)
void akAPI_CB(int id)
{
    EM_ASM(
    {
        akAPI_CB.apply(window,[$0]);
    },id);
}


// Main initialization function (called from JavaScript after Emscripten loads)
extern "C" int Main()
{
    // Allocate shared memory buffer for C++ <-> JavaScript communication
    akAPI_Buff = malloc(AKAPI_BUF_SIZE);
    memset(akAPI_Buff,0,AKAPI_BUF_SIZE);

    // Expose akAPI functions and buffer to JavaScript
    EM_ASM(
    {
        window.akAPI_Buff=$0;  // Share buffer address with JS
        window.akAPI_Call = Module.cwrap('akAPI_Call', null, ['number']);
        window.akAPI_This = {};  // Sandboxed execution context
        window.akPrint = function()
        {
            console.log.apply(this,arguments);
        };
    },akAPI_Buff);

    // Initialize akAPI scripting system
    akAPI_Init();

    // Set up sandboxing for user scripts
    // akAPI_Prot is an array of parameter names passed to Function constructor
    // All window properties except whitelisted "pub" ones are shadowed with undefined
    EM_ASM(
    {
        // prepare protection array
        window.akAPI_Prot = ["ak"];  // First param is always 'ak' (game API object)
        let all = Object.getOwnPropertyNames(window);

        // Whitelist of safe globals that user scripts can access
        let pub = new Array(
            "console","akPrint",
            "Object","Function","Array","Number","Boolean","String","Symbol","Date","Promise","RegExp",
            "ArrayBuffer","Uint8Array","Int8Array","Uint16Array","Int16Array","Uint32Array","Int32Array",
            "Float32Array","Float64Array","Uint8ClampedArray","BigUint64Array","BigInt64Array",
            "DataView","Map","BigInt","Set","WeakMap","WeakSet","Proxy","Reflect","FinalizationRegistry","WeakRef",
            "Error","AggregateError","EvalError","RangeError","ReferenceError","SyntaxError","TypeError","URIError",
            "JSON","Math","Intl",
            "decodeURI","decodeURIComponent","encodeURI","encodeURIComponent","escape","unescape",
            "eval","isFinite","isNaN",
            "parseFloat","parseInt",
            "Infinity","NaN","undefined",
            "globalThis"
        );

        // Shadow all non-whitelisted globals by adding them as undefined parameters
        for (const e of all)
        {
            if (e!='ak' && !pub.includes(e))
                akAPI_Prot.push(e);  // These will be set to undefined in Function constructor
        }

        // user source code place holder (filled in by akAPI_Exec)
        akAPI_Prot.push("");
    });


    InitAudio();

    // Initialize all 256 terrain material definitions
    InitMaterials();

    // Load sprite graphics (player, enemies, items)
    LoadSprites();


    // FL-4131 Phase 5 — web client cell-buffer / sidecar pin
    // ─────────────────────────────────────────────────────────────────────────
    // MODEL_PIN: web_extended_glyph_buffer
    //
    // PURPOSE: Pin the web client's buffer/texture model for extended GlyphIds.
    // The render_buf below stays a flat AnsiCell array — AnsiCell.gl remains
    // uint8 CP437 (FL-4131 hard rule: no widening of AnsiCell.gl). Extended
    // GlyphIds are carried by a parallel sidecar buffer, mirroring the engine
    // GlyphPlane carriage shipped in Phase 2 (engine/glyph_plane.h,
    // sprite.cpp:1456-1461). The companion uniform texture passed to the
    // WebGL1 fragment shader (web/game_web.html `tex` sampler) keeps
    // RGBA8 = (fg, bg, glyph_cp437, spare); the extended path adds a second
    // sampler bound to the sidecar atlas-of-atlases and a LUT texture keyed
    // by GlyphId → (page_index, atlas_x, atlas_y).
    //
    // SIDECAR DIMENSION CONTRACT (review H1):
    //   - The sidecar buffer's CELL COUNT must match the GPU upload's cell
    //     count, NOT the static render_buf allocation footprint. JS upload
    //     uses render_w * render_h (clamped to ak_max_width=160,
    //     ak_max_height=90, see game_web.html). The Phase 5 wire-up therefore
    //     allocates the sidecar as `calloc(160 * 160, sizeof(uint32_t))` (one
    //     GlyphId per cell, zero-initialized = sentinel "no extended glyph")
    //     and indexes it the same way Render() indexes render_buf:
    //         sidecar[ x * render_h + y ]   (column-major, matching render_buf)
    //     The JS upload reads `render_w * render_h` cells of both buffers, so
    //     the C++ writer and the JS uploader MUST agree on render_w/render_h
    //     for the same frame. A mid-frame resize is fail-closed: writer skips
    //     sidecar emission until next steady frame.
    //   - calloc (not malloc) is required: 0 = GLYPH_ID_NONE-as-uint32-low-32,
    //     so an unwritten cell decays to the fallback path rather than reading
    //     stale heap garbage.
    //
    // FAIL-CLOSED FALLBACK:
    //   - LUT miss / unbound manifest / sentinel GlyphId → substitute
    //     manifest.fallback_glyph_id. If the fallback id is itself extended
    //     (>255), the render still goes through the extended LUT (NOT the
    //     CP437 atlas); only the byte-domain AnsiCell.gl stub stays 0x3F per
    //     Phase 2 sprite.cpp:1451-1452. The CP437 atlas is never sampled with
    //     an extended id modulo 256. No silent glyph 0.
    //
    // COMPANION ANCHORS (review M5 — structured block, grep-stable):
    //   web/game_web.html (fragment shader)  : MODEL_PIN web_extended_glyph_buffer
    //   web/game_web.html (reject decoder)   : MODEL_PIN multiplayer_manifest_hash_match
    //   engine/glyph_plane.h                 : Phase 2 carrier (engine side)
    //   engine/glyph_manifest.h              : Phase 2 manifest + fallback contract
    //   engine/render/render.h               : MODEL_PIN shader_lookup_lut_model_pinned
    //   server/protocol/protocol_join.h      : MODEL_PIN multiplayer_manifest_hash_match
    // ─────────────────────────────────────────────────────────────────────────

    // Allocate ASCII render buffer (max 160x160 cells)
    // This buffer is filled by C++ and read by JavaScript for display
    render_buf = (AnsiCell*)malloc(sizeof(AnsiCell) * 160 * 160);
    if (!render_buf)
    {
        printf("failed to allocate render buffer\n");
        return -7;
    }

    // FL-4131 PHASE_5_SIDECAR_ALLOC — allocate parallel GlyphId sidecar.
    // calloc (not malloc): zero = sentinel "use CP437 path"; an unwritten cell
    // decays to the diagnostic fallback rather than reading stale heap bytes.
    glyph_sidecar = (uint32_t*)calloc(GLYPH_SIDECAR_W * GLYPH_SIDECAR_H, sizeof(uint32_t));
    if (!glyph_sidecar)
    {
        printf("failed to allocate glyph sidecar buffer\n");
        return -8;
    }

    // Create main game state object
    game = CreateGame();

    printf("all ok\n");
    return 0;
}

// Platform time stub for web builds. Other platforms define this in sdl.cpp/x11.cpp/mswin.cpp.
// Returns microseconds since page load (wraps every 584542 years).
// a3dGetTime, Load, SetNetSeed, Render, Size, Keyb, Mouse, Touch, GamePad, Focus are owned by web_platform.cpp

// Exported C functions callable from JavaScript
extern "C"
{

    // FL-4131 PHASE_5_SIDECAR_ALLOC — extended GlyphId sidecar exports.
    // JS reads the pointer once per upload and treats the bytes as RGBA8
    // (big-endian per-cell: R=hi, G=lo, B=spare, A=spare). The dimension
    // getters let the JS uploader size the source view to the texture's
    // ak_max_width * ak_max_height footprint without bypassing the WASM ABI.
    void* GetGlyphSidecar() { return (void*)glyph_sidecar; }
    int GetGlyphSidecarW() { return GLYPH_SIDECAR_W; }
    int GetGlyphSidecarH() { return GLYPH_SIDECAR_H; }

    // FL-4131 Phase D — env-gated test injection for the all-targets fallback
    // proof harness. Law 7 boundary: this is an OBSERVATIONAL helper that lets
    // a Phase D proof driver write a known GlyphId into a specific render cell
    // so the diagnostic fallback path can be observed visually. The cell is
    // written row-major to match the engine renderer's `buf[w*y+x]` layout.
    // Bounds-checked; out-of-range or null sidecar is a no-op. This must NOT
    // be used for gameplay-state mutation (no HP, no position, no inventory,
    // no NPC, no death). It exists solely to inject a sentinel GlyphId so the
    // shader's unknown-glyph diagnostic path renders an observable failure
    // marker (black '!' on red, per operator contract).
    void GlyphSidecarTestInject(int x, int y, int render_w, int render_h, uint32_t glyph_id)
    {
        GlyphSidecarWrite(x, y, render_w, render_h, glyph_id);
    }

    void FL4131RecordFallbackRenderEvent(uint32_t glyph_id,
                                         uint32_t fallback_glyph_id,
                                         uint32_t lut_width,
                                         uint32_t red_pixels,
                                         uint32_t black_pixels)
    {
        g_fl4131_fallback_render_event_count++;
        g_fl4131_fallback_render_last_glyph_id = glyph_id;
        g_fl4131_fallback_render_last_fallback_glyph_id = fallback_glyph_id;
        g_fl4131_fallback_render_last_lut_width = lut_width;
        g_fl4131_fallback_render_last_red_pixels = red_pixels;
        g_fl4131_fallback_render_last_black_pixels = black_pixels;
    }

    // Join is owned by web_network_client.cpp

    // WebAuthoritativeJoinActive is owned by web_network_client.cpp

    // Packet is owned by web_network_client.cpp

    // SetRespawnItemRefreshBatchMode is owned by web_network_client.cpp

#if 0
    // FL-1148 / R1-R9 disabled legacy verifier mutation exports.
    // Kept as commented reference only; build-web.sh no longer exports these.
    int VerifierPickupWorldItem(int index)
    {
        return VerifierPickupAuthoritativeWorldItem(game, index);
    }

    int VerifierUseItem(int index)
    {
        return VerifierUseAuthoritativeItem(game, index);
    }

    int VerifierDropItem(int index)
    {
        return VerifierDropAuthoritativeItem(game, index);
    }

    int VerifierAttackNearest(int target_kind)
    {
        return VerifierStartAttackNearest(game, target_kind);
    }

    int VerifierSetNearestNpcHealth(int hp)
    {
        return VerifierSetNearestNpcHp(game, hp);
    }

    int VerifierSetDebugDamageEnabled(int enabled)
    {
        return VerifierSetDebugDamage(game, enabled);
    }

    int VerifierTeleport(float x, float y, float z)
    {
        return VerifierSetCheckpointPosition(game, x, y, z);
    }
#endif

    // VerifierRespawn is owned by web_disconnect_witness.cpp

    // GameWorldReady is owned by web_network_client.cpp

    // GameAuthoritativeWorldReadyMissingMask / GameAuthoritativeWorldReady / CountEquippedLocalItems are owned by web_network_client.cpp

    // Return a printable roster dump from the same data the game loop uses for remote players.
    // Used by web UI for verifiable "other player exists" evidence in screenshots.
    // MultiplayerDiagJson is owned by web_network_client.cpp



    // RecorderStateJson / ClientObservationJsonV1 are owned by web_disconnect_witness.cpp
}

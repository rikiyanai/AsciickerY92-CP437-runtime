
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#define _USE_MATH_DEFINES
#include <math.h>
#include "term.h"
#include "window_backend.h"
#include "time_backend.h"
#include "image_backend.h"
#include "fast_rand.h"
#include "matrix.h"
#include "gl.h"
#include "gl45_emu.h"
// FL-4131 Phase 3: GlyphId type for native GL extended glyph sidecar
#include "../engine/glyph_id.h"
#include "third_party/cjson/cJSON.h"

// =============================================================================
// [FLOW:TERMINAL] OpenGL Terminal Emulator — AnsiCell Buffer to Screen Rendering
// =============================================================================
// platform/terminal_gl_present.cpp — OpenGL Terminal Presenter
// =============================================================================
//
// PURPOSE:
// Renders the game's AnsiCell buffer (fg, bk, gl, spare) to screen using modern
// OpenGL. Implements a virtual terminal emulator that displays ASCII art with
// 256-color xterm palette support and CP437 glyph rendering via GPU shaders.
//
// WHY OPENGL TERMINAL (not direct terminal output):
// - GPU acceleration: Fragment shader computes palette mapping + font sampling
//   in parallel for all pixels (millions per frame vs sequential CPU rendering)
// - Scalable fonts: Resize window without pixelation (font texture sampled with
//   bilinear filtering, not fixed-size bitmap)
// - Cross-platform: Same rendering path for Windows/Linux/macOS via platform.h
//   backends (SDL/X11/Win32 all use this OpenGL code)
// - Decoupled: Game renders to AnsiCell buffer (CPU), terminal_gl_present.cpp handles display
//   (GPU) — game logic doesn't know about OpenGL
//
// SIX-STAGE RENDERING PIPELINE:
//
// STAGE 1: CPU — Game Renders to AnsiCell Buffer
// - Game::Render() fills term->buf[width × height] with AnsiCell structs
// - AnsiCell format: {fg, bk, gl, spare} (render.h lines 37-45)
//   - fg/bk: 256-color xterm palette indices (0-15 system, 16-231 RGB cube,
//     232-255 grayscale ramp)
//   - gl: CP437 glyph code (0-255, maps to font texture via 16×16 grid)
//   - spare: reserved (0xFF = cell rendered, used for dirty rect optimization)
// - Example cell: {fg:196 (red), bk:16 (black), gl:65 ('A'), spare:0xFF}
//
// STAGE 2: CPU → GPU — Buffer Upload to GL_RGBA8 Texture
// - gl3TextureSubImage2D(term->tex, ..., GL_RGBA, GL_UNSIGNED_BYTE, term->buf)
// - Uploads AnsiCell buffer to GPU texture (term->tex)
// - Texture format: GL_RGBA8 (R=fg, G=bk, B=gl, A=spare)
// - Texture size: max_width × max_height (160×90 cells, constant — see line 59)
// - WHY texture upload: GPU can sample entire buffer in parallel (fragment
//   shader reads arbitrary cells), CPU upload is once per frame
//
// STAGE 3: GPU Vertex Shader — Fullscreen Quad
// - glDrawArrays(GL_TRIANGLE_FAN, 0, 4) renders quad covering viewport
// - Vertex shader (lines 311-320) transforms UV [0,1]² → cell coordinates
//   - gl_Position = vec4(2.0 * uv - 1.0, 0.0, 1.0) — NDC fullscreen quad
//   - cell_coord = uv * ansi_vp — cell-space coordinates for fragment shader
// - Example: UV (0.5, 0.5) → cell_coord (80, 45) for 160×90 viewport
//
// STAGE 4: GPU Fragment Shader — Sample AnsiCell + Font Textures
// - Fragment shader receives cell_coord (e.g., 42.7, 18.3 for cell 42, 70% across)
// - Sample AnsiCell texture at cell center: texture(ansi, (floor(cell_coord) + 0.5) / ansi_wh)
// - Extract cell data: fg = cell.r × 255, bk = cell.g × 255, gl = cell.b × 255
// - Compute font texture coordinates from glyph index:
//   - glyph_coord = (vec2(gl & 0xF, gl >> 4) + frac_cell) / 16.0
//   - gl & 0xF extracts column (0-15), gl >> 4 extracts row (0-15)
//   - frac_cell is fractional position within cell (0.0-1.0 for X/Y)
// - Sample font texture: glyph_alpha = texture(font, glyph_coord).a
// - WHY two textures: AnsiCell holds color indices (8-bit, 256 colors), font
//   texture holds glyph shapes (alpha channel, 0.0 = transparent/BG, 1.0 = opaque/FG)
//
// STAGE 5: GPU Fragment Shader — Palette Mapping (Pal Function)
// - Pal(float p): Maps 256-color xterm palette index → RGB [0,1]³
// - Input range: 0-15 (system colors), 16-231 (RGB cube), 232-255 (grayscale)
// - RGB cube formula (indices 16-231, 6×6×6 = 216 colors) — see lines 349-359:
//   1. p' = clamp(floor(p - 16 + 0.5), 0, 215)   // Normalize to [0, 215]
//   2. blue  = floor(p' / 36)                    // Extract blue [0-5] (36 = 6×6)
//   3. p' -= 36 × blue                           // Remove blue contribution
//   4. green = floor(p' / 6)                     // Extract green [0-5]
//   5. red   = p' - 6 × green                    // Extract red [0-5] (remainder)
//   6. return vec3(blue, green, red) × 0.2       // Scale to [0, 1]
// - WHY 0.2 scale factor: 6 discrete steps (0-5) need to map to [0, 1], so
//   step size = 1 / 5 = 0.2 (step 0 → 0.0, step 5 → 1.0)
// - Palette range examples:
//   - Index 16  (6×6×6 first)  → RGB(0,0,0) × 0.2 = RGB(0.0, 0.0, 0.0) (black)
//   - Index 231 (6×6×6 last)   → RGB(5,5,5) × 0.2 = RGB(1.0, 1.0, 1.0) (white)
//   - Index 196 (xterm red)    → RGB(5,0,0) × 0.2 = RGB(1.0, 0.0, 0.0) (red)
// - EDGE CASES: Indices 0-15 and 232-255 handled by clamping to [0, 215] after
//   subtracting 16 — they fall outside RGB cube and default to black in Pal()
//
// STAGE 6: GPU Fragment Shader — Blend FG/BG Colors
// - fg_color = Pal(cell.x × 255.0)  // Foreground from palette
// - bg_color = Pal(cell.y × 255.0)  // Background from palette
// - color = vec4(mix(bg_color, fg_color, glyph_alpha), 1.0)
// - WHY mix: glyph_alpha interpolates between BG and FG:
//   - glyph_alpha = 0.0 (transparent) → bg_color (show background)
//   - glyph_alpha = 1.0 (opaque)      → fg_color (show foreground/glyph)
//   - glyph_alpha = 0.5 (antialiased edge) → 50/50 blend (smooth edges)
// - Output: Final pixel color written to framebuffer (GL_COLOR_BUFFER_BIT)
//
// 256-COLOR XTERM PALETTE LAYOUT:
// - Indices 0-15:   System colors (black, red, green, yellow, blue, magenta,
//                   cyan, white, bright variants — not implemented in Pal(),
//                   would require lookup table)
// - Indices 16-231: RGB cube (6×6×6 = 216 colors, see STAGE 5)
// - Indices 232-255: Grayscale ramp (24 shades from dark to bright — not
//                    implemented in Pal(), defaults to black)
//
// KEY DATA STRUCTURES:
// - AnsiCell (render.h lines 37-45): {fg, bk, gl, spare} — 4-byte cell format
// - TERM_LIST (lines 40-73): Window state (OpenGL textures, shaders, VBO/VAO,
//   game instance, input callbacks)
// - term->buf[max_width × max_height] (line 61): AnsiCell buffer (CPU-side)
// - term->tex (line 62): GL_RGBA8 texture holding uploaded AnsiCell buffer (GPU)
// - term->prg (line 63): GLSL shader program (vertex + fragment shaders)
// - term->vao/vbo (lines 64-65): Vertex array/buffer for fullscreen quad
//
// KEY FUNCTIONS:
// - term_render() (lines 91-227): Main render loop (upload buffer, bind textures, draw quad)
// - term_init() (lines 279-489): Initialize OpenGL resources (textures, shaders, VAO/VBO)
// - term_close() (lines 537-568): Cleanup OpenGL resources (delete textures, shaders, buffers)
// - TermOpen() (lines 570-625): Create new terminal window (platform.h integration)
// - TermCloseAll() (lines 627-646): Close all terminal windows (linked list traversal)
//
// INTEGRATION POINTS:
// - render.h: Defines AnsiCell structure (lines 37-45)
// - game.cpp: Calls Game::Render(stamp, term->buf, width, height) to fill buffer
// - platform.h: PlatformInterface callbacks (render, resize, keyb, mouse) route
//   OS events to term_* functions
// - GetGLFont() (asciiid.cpp/game_app.cpp): Provides CP437 font texture ID and
//   dimensions (16×16 glyph grid, variable-width font support)
//
// PERFORMANCE PROFILING:
// - Environment variable ASCIICKER_PROFILE=1 enables timing (lines 119-226)
// - Measures:
//   - Render time: Game::Render() CPU work (fill AnsiCell buffer)
//   - Present time: OpenGL GPU work (upload, shader, draw)
//   - FPS: Frames per second over 1-second windows
// - Output: stderr or ASCIICKER_PROFILE_LOG file (lines 133-138)
// - Example: "[perf] render 2.34ms present 0.87ms fps 60.0 (160x90)"
//
// INPUT DISPATCH:
// - term_mouse() (lines 229-266): Routes MouseInfo events to Game::OnMouse()
// - term_keyb_key() (lines 497-529): Routes KeyInfo events to Game::OnKeyb()
// - term_keyb_char() (lines 491-495): Routes character input to Game::OnKeyb(KEYB_CHAR)
// - Special keys:
//   - F11: Toggle fullscreen (ToggleFullscreen, lines 661-678)
//   - Numpad +/-: Change font size (NextGLFont/PrevGLFont)
//   - F5-F8: Game-specific actions (passed to game)
//
// TERMINAL MANAGEMENT:
// - Linked list of TERM_LIST nodes (term_head, term_tail) supports multiple windows
// - Each window has independent OpenGL context, game instance, input state
// - TermCloseAll() traverses list and cleans up all windows on exit
// =============================================================================

#include "game.h"
#include "game_utility.h"

#define CODE(...) #__VA_ARGS__
#define DEFN(a, s) "#define " #a #s "\n"

void ToggleFullscreen(Game *g);
bool IsFullscreen(Game *g);
void EnsureLocalPlayerInst(Game* g, Sprite* sprite, const float pos[3], float yaw, int anim, int frame);

struct TERM_LIST
{
	TERM_LIST *prev;
	TERM_LIST *next;
	A3D_WND *wnd;

	void (*close)();

	Game *game;
	// Physics* phys;

	float yaw;

	uint8_t keys[32];
	bool IsKeyDown(int key)
	{
		return (keys[key >> 3] & (1 << (key & 0x7))) != 0;
	}

	// WHY 160×90 max dimensions: Typical retro terminal size (160 columns × 90 rows),
	// large enough for detailed ASCII art while maintaining ~60 FPS on integrated GPUs.
	// Constraint enforced at lines 110-113 (clamp viewport to max dimensions).
	// WHY 160×90 max dimensions: Typical retro terminal size (160 columns × 90 rows),
	// large enough for detailed ASCII art while maintaining ~60 FPS on integrated GPUs.
	// Constraint enforced at lines 110-113 (clamp viewport to max dimensions).
	static const int max_width = 160; // 160;
	static const int max_height = 90; // 90;
	// [DATA-CONTRACT:ANSI] AnsiCell buffer: {fg, bk, gl, spare} per cell (render.h lines 37-45).
	// fg/bk are xterm palette indices (0-255), gl is CP437 glyph code (0-255), spare is 0xFF if rendered.
	AnsiCell buf[max_width * max_height];
	GLuint tex;
	GLuint prg;
	GLuint vbo;
	GLuint vao;

	GLint uni_ansi_vp;
	GLint uni_ansi;
	GLint uni_font;
	GLint uni_ansi_wh;
	GLint att_uv;
	GLint out_color;
	char title_cache[1400];

	// FL-4131 Phase 3 — native GL extended glyph sidecar
	// MODEL_PIN: shader_lookup_lut_model_pinned (render.h)
	// CPU-side sidecar: one GlyphId per cell, GLYPH_ID_NONE = no extended glyph (CP437 path).
	// lut_tex/page_atlas are 1×1 stubs; lut_width=0 keeps CP437 path active for all cells
	// until a manifest is bound. Zero behavioral change when no manifest is loaded.
	GlyphId sidecar_buf[max_width * max_height];
	GLuint sidecar_tex;    // R32UI: GlyphId sidecar uploaded each frame
	GLuint lut_tex_id;     // RGBA8: GlyphId→(page,x,y) LUT (1×1 stub when no manifest)
	GLuint page_atlas_id;  // RGBA8: atlas-of-atlases page (1×1 stub when no manifest)
	float lut_width;
	float page_atlas_cols;
	float page_atlas_rows;
	GLint uni_sidecar;     // sidecar sampler (TEXTURE2)
	GLint uni_lut_tex;     // lut_tex sampler (TEXTURE3)
	GLint uni_page_atlas;  // page_atlas sampler (TEXTURE4)
	GLint uni_lut_width;   // 0.0 = no manifest bound; extended path disabled
	GLint uni_page_grid;   // atlas page dimensions in cells, e.g. 16x7
};

static const char* kTermCompiledGlyphAtlasPagePath = "assets/glyphs/atlases/material.additive.v1.page0_rgba8.json";
static const int kTermCompiledGlyphCellPx = 16;
// FL-4131 P1+P8: page0 is the 16px alias of the compiled multi-size ladder
// (compile_glyph_manifest.py --compile emits page<cell_px>_rgba8.json plus
// a page0 alias pointing at the canonical 16px page). Width is always
// ATLAS_COLS * cell_px = 16 * 16 = 256, but height depends on the number of
// admitted glyphs (currently 7 rows * 16 px = 112).
static const int kTermCompiledGlyphPageWidthPx = 16 * 16;
static const int kTermCompiledGlyphPageMaxHeightPx = 16 * 16; // upper bound for stack alloc
static const int kTermCompiledGlyphFirstId = 512;
static const int kTermCompiledGlyphLastId = 631;
static GlyphId* g_active_native_glyph_sidecar = NULL;
static int g_active_native_glyph_sidecar_w = 0;
static int g_active_native_glyph_sidecar_h = 0;

extern "C" void NativeRenderGlyphSidecarWrite(int x, int y, int render_w, int render_h, uint32_t glyph_id)
{
	if (!g_active_native_glyph_sidecar || render_w != g_active_native_glyph_sidecar_w || render_h != g_active_native_glyph_sidecar_h)
		return;
	if (x < 0 || y < 0 || x >= g_active_native_glyph_sidecar_w || y >= g_active_native_glyph_sidecar_h)
		return;
	g_active_native_glyph_sidecar[y * g_active_native_glyph_sidecar_w + x] = (GlyphId)glyph_id;
}

static bool TermReadWholeFile(const char* path, char** out_text)
{
	if (!path || !out_text)
		return false;
	*out_text = NULL;
	FILE* f = fopen(path, "rb");
	if (!f)
		return false;
	if (fseek(f, 0, SEEK_END) != 0)
	{
		fclose(f);
		return false;
	}
	long size = ftell(f);
	if (size < 0 || fseek(f, 0, SEEK_SET) != 0)
	{
		fclose(f);
		return false;
	}
	char* text = (char*)malloc((size_t)size + 1);
	if (!text)
	{
		fclose(f);
		return false;
	}
	size_t n = fread(text, 1, (size_t)size, f);
	fclose(f);
	if (n != (size_t)size)
	{
		free(text);
		return false;
	}
	text[n] = 0;
	*out_text = text;
	return true;
}

// FL-4131 P1+P8: page geometry is no longer hard-coded to 256x256. The
// compiled atlas now ships at the actual admitted-glyph extent
// (ATLAS_COLS * cell_px wide, ceil(N/ATLAS_COLS) * cell_px tall) and the
// loader allocates per the JSON-declared width/height. Returns the read
// dimensions and cell_px through out_*; the caller is responsible for
// uploading at those dimensions. Returns false if the file is missing,
// malformed, or larger than the static budget.
static bool TermLoadCompiledGlyphAtlasPage(
	uint8_t pixels[kTermCompiledGlyphPageWidthPx * kTermCompiledGlyphPageMaxHeightPx * 4],
	int* out_width, int* out_height, int* out_cell_px)
{
	if (out_width) *out_width = 0;
	if (out_height) *out_height = 0;
	if (out_cell_px) *out_cell_px = 0;
	char* text = NULL;
	if (!TermReadWholeFile(kTermCompiledGlyphAtlasPagePath, &text))
		return false;
	cJSON* root = cJSON_Parse(text);
	free(text);
	if (!root)
		return false;
	bool ok = false;
	const cJSON* width = cJSON_GetObjectItemCaseSensitive(root, "width");
	const cJSON* height = cJSON_GetObjectItemCaseSensitive(root, "height");
	const cJSON* cell_px = cJSON_GetObjectItemCaseSensitive(root, "cell_px");
	const cJSON* format = cJSON_GetObjectItemCaseSensitive(root, "format");
	const cJSON* rgba8 = cJSON_GetObjectItemCaseSensitive(root, "rgba8");
	const int max_pixels = kTermCompiledGlyphPageWidthPx * kTermCompiledGlyphPageMaxHeightPx * 4;
	if (cJSON_IsNumber(width) &&
		cJSON_IsNumber(height) &&
		cJSON_IsNumber(cell_px) &&
		cJSON_IsString(format) &&
		cJSON_IsArray(rgba8) &&
		width->valueint > 0 && width->valueint <= kTermCompiledGlyphPageWidthPx &&
		height->valueint > 0 && height->valueint <= kTermCompiledGlyphPageMaxHeightPx &&
		cell_px->valueint > 0 && cell_px->valueint <= 128 &&
		strcmp(format->valuestring, "rgba8") == 0)
	{
		const int expected_count = width->valueint * height->valueint * 4;
		if (cJSON_GetArraySize(rgba8) == expected_count && expected_count <= max_pixels)
		{
			ok = true;
			int i = 0;
			for (const cJSON* item = rgba8->child; item; item = item->next)
			{
				if (!cJSON_IsNumber(item) || item->valueint < 0 || item->valueint > 255 || i >= expected_count)
				{
					ok = false;
					break;
				}
				pixels[i++] = (uint8_t)item->valueint;
			}
			ok = ok && i == expected_count;
			if (ok)
			{
				if (out_width) *out_width = width->valueint;
				if (out_height) *out_height = height->valueint;
				if (out_cell_px) *out_cell_px = cell_px->valueint;
			}
		}
	}
	cJSON_Delete(root);
	return ok;
}

static void TermBuildCompiledGlyphLut(uint8_t* lut, int lut_width, int page_cell_px)
{
	memset(lut, 0, (size_t)lut_width * 4);
	const int cols_per_page = (page_cell_px > 0) ? (kTermCompiledGlyphPageWidthPx / page_cell_px) : 16;
	for (int glyph_id = kTermCompiledGlyphFirstId; glyph_id <= kTermCompiledGlyphLastId; glyph_id++)
	{
		const int glyph_index = glyph_id - kTermCompiledGlyphFirstId;
		lut[glyph_id * 4 + 0] = 0;
		lut[glyph_id * 4 + 1] = (uint8_t)(glyph_index % cols_per_page);
		lut[glyph_id * 4 + 2] = (uint8_t)(glyph_index / cols_per_page);
		lut[glyph_id * 4 + 3] = 255;
	}
}

// HACK: get it from editor
extern Terrain *terrain;
extern World *world;
int GetGLFont(int wh[2], const int wnd_wh[2], int* id = 0);
bool NextGLFont();
bool PrevGLFont();

TERM_LIST *term_head = 0;
TERM_LIST *term_tail = 0;

// SUPER_HACK LIVE VIEW
extern float pos_x, pos_y, pos_z;
extern float rot_yaw;
extern float global_lt[];
extern int probe_z;

#ifdef EDITOR
struct TERM_BOOTSTRAP_REQUEST
{
	bool valid;
	float pos[3];
	float yaw;
	int water;
};

static TERM_BOOTSTRAP_REQUEST g_term_bootstrap_request = {};

static void QueueTermBootstrapRequest(float yaw, const float pos[3], int water)
{
	g_term_bootstrap_request.valid = true;
	g_term_bootstrap_request.yaw = yaw;
	g_term_bootstrap_request.water = water;
	g_term_bootstrap_request.pos[0] = pos ? pos[0] : pos_x;
	g_term_bootstrap_request.pos[1] = pos ? pos[1] : pos_y;
	g_term_bootstrap_request.pos[2] = pos ? pos[2] : pos_z;
}

static void ConsumeTermBootstrapRequest(float pos[3], float* yaw, int* water)
{
	if (g_term_bootstrap_request.valid)
	{
		if (pos)
		{
			pos[0] = g_term_bootstrap_request.pos[0];
			pos[1] = g_term_bootstrap_request.pos[1];
			pos[2] = g_term_bootstrap_request.pos[2];
		}
		if (yaw)
			*yaw = g_term_bootstrap_request.yaw;
		if (water)
			*water = g_term_bootstrap_request.water;
		g_term_bootstrap_request.valid = false;
		return;
	}

	if (pos)
	{
		pos[0] = pos_x;
		pos[1] = pos_y;
		pos[2] = pos_z;
	}
	if (yaw)
		*yaw = rot_yaw;
	if (water)
		*water = 55;
}
#endif

void term_render(A3D_WND *wnd)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	// dispatch all queued messages to game
	// 1. flush list
	// 2. reverse order
	// 3. dispatch every message with term->game->OnMessage()

	int wnd_wh[2];

	a3dGetRect(wnd, 0, wnd_wh);

	int fnt_wh[2];
	int fnt_tex = GetGLFont(fnt_wh, wnd_wh);

	int width = wnd_wh[0] / (fnt_wh[0] >> 4);
	int height = wnd_wh[1] / (fnt_wh[1] >> 4);

	if (width > term->max_width)
		width = term->max_width;
	if (height > term->max_height)
		height = term->max_height;

	// RQ-11 Phase C: observe-render must emit the canonical 106x76 cell window
	// so Godot and native shot.xp comparisons are dimension-stable.
	if (ObserveRenderEnabled())
	{
		width = 106;
		height = 76;
	}

	if (server)
		server->Proc();

	uint64_t stamp = a3dGetTime();
	static bool perf_init = false;
	static bool perf_enabled = false;
	static FILE* perf_out = nullptr;
	static uint64_t perf_window_start = 0;
	static uint64_t perf_render_sum = 0;
	static uint64_t perf_present_sum = 0;
	static int perf_frames = 0;

	if (!perf_init)
	{
		perf_init = true;
		perf_enabled = getenv("ASCIICKER_PROFILE") != nullptr;
		if (perf_enabled)
		{
			const char* perf_path = getenv("ASCIICKER_PROFILE_LOG");
			if (perf_path && perf_path[0])
				perf_out = fopen(perf_path, "a");
			if (!perf_out)
				perf_out = stderr;
		}
	}

	uint64_t t0 = 0;
	uint64_t t1 = 0;
	if (perf_enabled)
		t0 = a3dGetTime();

	// [FLOW:TERMINAL] STAGE 1: Game renders to AnsiCell buffer (CPU).
	// Game::Render() fills term->buf[width × height] with {fg, bk, gl, spare} cells.
	memset(term->sidecar_buf, 0xFF, sizeof(term->sidecar_buf));
	g_active_native_glyph_sidecar = term->sidecar_buf;
	g_active_native_glyph_sidecar_w = width;
	g_active_native_glyph_sidecar_h = height;
	term->game->Render(stamp, term->buf, width, height);
	g_active_native_glyph_sidecar = NULL;
	g_active_native_glyph_sidecar_w = 0;
	g_active_native_glyph_sidecar_h = 0;

	if (perf_enabled)
		t1 = a3dGetTime();

	glClearColor(0, 0, 0, 0);
	glClear(GL_COLOR_BUFFER_BIT);

	char runtime_title[1200];
	char game_title[1024];
	BuildGameTermTitle(game_title, sizeof(game_title));
	// WARNING (FL-2541): term.cpp used to overwrite the runtime title every
	// frame with "ASCIIID Term WxH", which hid the actual game/map codestate and
	// made the window look editor-owned. The platform layer must display the
	// runtime-owned title, not invent one.
	snprintf(runtime_title, sizeof(runtime_title), "%s", game_title);
	if (strcmp(term->title_cache, runtime_title) != 0)
	{
		snprintf(term->title_cache, sizeof(term->title_cache), "%s", runtime_title);
		a3dSetTitle(wnd, term->title_cache);
	}

	int vp_wh[2] =
		{
			width * (fnt_wh[0] >> 4),
			height * (fnt_wh[1] >> 4)};

	int vp_xy[2] =
		{
			(wnd_wh[0] - vp_wh[0]) / 2,
			(wnd_wh[1] - vp_wh[1]) / 2};

	// [FLOW:TERMINAL] STAGE 2: Upload AnsiCell buffer to GPU texture (CPU → GPU).
	// GL_RGBA format maps AnsiCell fields: R=fg, G=bk, B=gl, A=spare.
	gl3TextureSubImage2D(term->tex, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, term->buf);

	// FL-4131 Phase 3: upload sidecar (R32UI, one GlyphId per cell).
	// sidecar_buf is GLYPH_ID_NONE (0xFFFFFFFF) for all cells; no extended glyphs in world yet.
	// The shader checks lut_width==0 first and skips the extended path entirely.
	glBindTexture(GL_TEXTURE_2D, term->sidecar_tex);
	glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RED_INTEGER, GL_UNSIGNED_INT, term->sidecar_buf);
	glBindTexture(GL_TEXTURE_2D, 0);

	glViewport(vp_xy[0], vp_xy[1], vp_wh[0], vp_wh[1]);

	glUseProgram(term->prg);

	glUniform2i(/*0*/ term->uni_ansi_vp, width, height);

	glUniform1i(/*1*/ term->uni_ansi, 0);
	// glBindTextureUnit(0, term->tex);
	glActiveTexture(GL_TEXTURE0);
	glBindTexture(GL_TEXTURE_2D, term->tex);

	glUniform1i(/*2*/ term->uni_font, 1);
	// [DATA-CONTRACT:FONT] CP437 font texture from GetGLFont() (asciiid.cpp/game_app.cpp).
	// 16×16 glyph grid (256 glyphs), alpha channel encodes glyph shape (0.0=BG, 1.0=FG).
	// glBindTextureUnit(1, fnt_tex);
	glActiveTexture(GL_TEXTURE1);
	glBindTexture(GL_TEXTURE_2D, fnt_tex);

	// FL-4131 Phase 3: bind sidecar/lut/page_atlas to TEXTURE2/3/4; lut_width=0 disables extended path.
	glUniform1i(term->uni_sidecar,    2);
	glActiveTexture(GL_TEXTURE2);
	glBindTexture(GL_TEXTURE_2D, term->sidecar_tex);

	glUniform1i(term->uni_lut_tex,    3);
	glActiveTexture(GL_TEXTURE3);
	glBindTexture(GL_TEXTURE_2D, term->lut_tex_id);

	glUniform1i(term->uni_page_atlas, 4);
	glActiveTexture(GL_TEXTURE4);
	glBindTexture(GL_TEXTURE_2D, term->page_atlas_id);

	glUniform1f(term->uni_lut_width, term->lut_width); // 0 = no manifest bound; CP437 path active
	glUniform2f(term->uni_page_grid, term->page_atlas_cols, term->page_atlas_rows);

	glUniform2i(/*3*/ term->uni_ansi_wh, term->max_width, term->max_height);

	glBindVertexArray(term->vao);

	// [FLOW:TERMINAL] STAGES 3-6: GPU renders fullscreen quad with palette mapping.
	// Vertex shader transforms UV → cell_coord, fragment shader samples AnsiCell + font
	// textures, applies Pal() palette mapping, blends FG/BG colors based on glyph alpha.
	glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
	glUseProgram(0);
	glBindVertexArray(0);

	glActiveTexture(GL_TEXTURE4);
	glBindTexture(GL_TEXTURE_2D, 0);
	glActiveTexture(GL_TEXTURE3);
	glBindTexture(GL_TEXTURE_2D, 0);
	glActiveTexture(GL_TEXTURE2);
	glBindTexture(GL_TEXTURE_2D, 0);
	glActiveTexture(GL_TEXTURE1);
	glBindTexture(GL_TEXTURE_2D, 0);
	glActiveTexture(GL_TEXTURE0);
	glBindTexture(GL_TEXTURE_2D, 0);
	/*
	glBindTextureUnit(0, 0);
	glBindTextureUnit(1, 0);
	*/

	if (perf_enabled)
	{
		uint64_t t2 = a3dGetTime();
		perf_render_sum += (t1 - t0);
		perf_present_sum += (t2 - t1);
		perf_frames++;
		if (perf_window_start == 0)
			perf_window_start = t2;
		if (t2 - perf_window_start >= 1000000)
		{
			double window_us = (double)(t2 - perf_window_start);
			double avg_render_ms = (double)perf_render_sum / (double)perf_frames / 1000.0;
			double avg_present_ms = (double)perf_present_sum / (double)perf_frames / 1000.0;
			double fps = (double)perf_frames * 1000000.0 / window_us;
			fprintf(perf_out, "[perf] render %.2fms present %.2fms fps %.1f (%dx%d)\n",
				avg_render_ms, avg_present_ms, fps, width, height);
			fflush(perf_out);
			perf_window_start = t2;
			perf_render_sum = 0;
			perf_present_sum = 0;
			perf_frames = 0;
		}
	}
}

void term_mouse(A3D_WND *wnd, int x, int y, MouseInfo mi)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	switch (mi & 0xF)
	{
	case MouseInfo::MIDDLE_DN:
	{
		int p[2] = {x, y};
		term->game->ScreenToCell(p);
		render_break_point[0] = p[0];
		render_break_point[1] = p[1];
		break;
	}

	case MouseInfo::MOVE:
		term->game->OnMouse(GAME_MOUSE::MOUSE_MOVE, x, y);
		break;
	case MouseInfo::LEFT_DN:
		term->game->OnMouse(GAME_MOUSE::MOUSE_LEFT_BUT_DOWN, x, y);
		break;
	case MouseInfo::LEFT_UP:
		term->game->OnMouse(GAME_MOUSE::MOUSE_LEFT_BUT_UP, x, y);
		break;
	case MouseInfo::RIGHT_DN:
		term->game->OnMouse(GAME_MOUSE::MOUSE_RIGHT_BUT_DOWN, x, y);
		break;
	case MouseInfo::RIGHT_UP:
		term->game->OnMouse(GAME_MOUSE::MOUSE_RIGHT_BUT_UP, x, y);
		break;
	case MouseInfo::WHEEL_UP:
		term->game->OnMouse(GAME_MOUSE::MOUSE_WHEEL_UP, x, y);
		break;
	case MouseInfo::WHEEL_DN:
		term->game->OnMouse(GAME_MOUSE::MOUSE_WHEEL_DOWN, x, y);
		break;
	}
}

void term_resize(A3D_WND *wnd, int w, int h)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	int fnt_wh[2] = {0, 0};
	int wnd_wh[2] = {w, h};
	int fnt_tex = GetGLFont(fnt_wh, wnd_wh);

	term->game->OnSize(w, h, fnt_wh[0] >> 4, fnt_wh[1] >> 4);
}

void term_init(A3D_WND *wnd)
{
	TERM_LIST *term = (TERM_LIST *)malloc(sizeof(TERM_LIST));
	memset(term, 0, sizeof(TERM_LIST));
	term->wnd = wnd;

	uint64_t stamp = a3dGetTime();

	term->game = CreateGame();
#ifdef EDITOR
	float pos[3] = {0, 0, 0};
	float yaw = 0;
	float dir = 0;
	int water = 55;
	ConsumeTermBootstrapRequest(pos, &yaw, &water);
	float lt[4] = {1, 0, 1, 0.5};
	InitGame(term->game, water, pos, yaw, dir, lt, stamp);
#endif

	int loglen = 999;
	char logstr[1000] = "";
	GLuint shader[2] = {0, 0};

	term->tex = 0;
	gl3CreateTextures(GL_TEXTURE_2D, 1, &term->tex);

	if (!term->tex)
	{
		printf("glCreateTextures failed\n");
		exit(-1);
	}

	// [DATA-CONTRACT:ANSI] GL_RGBA8 texture holds uploaded AnsiCell buffer (GPU-side).
	// Texture dimensions are constant max_width × max_height (160×90), viewport clips to actual size.
	gl3TextureStorage2D(term->tex, 1, GL_RGBA8, term->max_width, term->max_height);

	const char *term_vs_src =
		CODE(#version 330\n)
			CODE(
				/*layout(location = 0)*/ uniform ivec2 ansi_vp; // viewport size in cells
				/*layout(location = 0)*/ in vec2 uv;			// normalized to viewport size
				out vec2 cell_coord;
				void main() {
					gl_Position = vec4(2.0 * uv - vec2(1.0), 0.0, 1.0);
					cell_coord = uv * ansi_vp;
				});

	// =============================================================================
	// Fragment Shader: Pal() Function — 256-Color Xterm Palette Mapping (6×6×6 RGB Cube)
	// =============================================================================
	//
	// WHY 6×6×6 RGB cube: Xterm 256-color standard allocates indices 16-231 (216 colors)
	// to a discrete RGB color cube with 6 levels per channel (6³ = 216). This provides
	// a uniform distribution of colors across RGB space while fitting in 8-bit indices.
	//
	// UNPACKING FORMULA DERIVATION (maps palette index → RGB components):
	// - Input p: palette index in range [16, 231] (indices 0-15 are system colors, 232-255 grayscale)
	// - Step 1: Normalize to cube space: p' = p - 16 → [0, 215]
	//   - floor(p - 16.0 + 0.5) rounds float index to nearest integer (handles quantization)
	//   - clamp(p', 0, 215) ensures out-of-range indices (0-15, 232-255) default to black
	// - Step 2: Extract blue component (most significant): blue = floor(p' / 36)
	//   - 36 = 6 × 6 (red × green combinations per blue level)
	//   - blue ranges [0-5] (6 discrete levels)
	// - Step 3: Remove blue contribution: p' -= 36 × blue → [0, 35]
	// - Step 4: Extract green component: green = floor(p' / 6)
	//   - 6 = red combinations per green level
	//   - green ranges [0-5] (6 discrete levels)
	// - Step 5: Extract red component (least significant): red = p' - 6 × green
	//   - red is remainder after removing green contribution
	//   - red ranges [0-5] (6 discrete levels)
	// - Step 6: Scale to [0, 1]: RGB × 0.2
	//   - WHY 0.2 scale factor: 6 discrete steps (0-5) need to map to [0.0, 1.0]
	//     Step size = 1 / 5 = 0.2 (step 0 → 0.0, step 1 → 0.2, ..., step 5 → 1.0)
	//   - NOT 1/6: We want step 5 to map to 1.0 (pure color), so 5 × 0.2 = 1.0
	//
	// VERIFICATION EXAMPLES:
	// - Index 16  (first RGB cube entry, p'=0):   blue=0, green=0, red=0 → RGB(0,0,0)×0.2 = (0.0, 0.0, 0.0) ✓ black
	// - Index 231 (last RGB cube entry, p'=215):  blue=5, green=5, red=5 → RGB(5,5,5)×0.2 = (1.0, 1.0, 1.0) ✓ white
	// - Index 196 (xterm red, p'=180):            blue=5, green=0, red=0 → RGB(5,0,0)×0.2 = (1.0, 0.0, 0.0) ✓ red
	// - Index 46  (xterm green, p'=30):           blue=0, green=5, red=0 → RGB(0,5,0)×0.2 = (0.0, 1.0, 0.0) ✓ green
	// - Index 21  (xterm blue, p'=5):             blue=0, green=0, red=5 → RGB(0,0,5)×0.2 = (0.0, 0.0, 1.0) ✓ blue
	//
	// EDGE CASES:
	// - Indices 0-15 (system colors): p - 16 < 0 → clamp to 0 → RGB(0,0,0) (black)
	//   - NOTE: Should use lookup table for true system colors (not implemented)
	// - Indices 232-255 (grayscale ramp): p - 16 > 215 → clamp to 215 → RGB(5,5,5) (white)
	//   - NOTE: Should use linear grayscale ramp (not implemented, defaults to white)
	// =============================================================================
	const char *term_fs_src =
		CODE(#version 330\n)
			DEFN(P(r, g, b), vec3(r / 6., g / 7., b / 6.))
				CODE(

					/*layout(location = 0)*/ out vec4 color;
					/*layout(location = 1)*/ uniform sampler2D ansi;
					/*layout(location = 2)*/ uniform sampler2D font;
					/*layout(location = 3)*/ uniform ivec2 ansi_wh; // ansi texture size (in cells), constant = 160x90
					in vec2 cell_coord;

					// FL-4131 Phase 3 — native GL extended glyph uniforms
					// MODEL_PIN: shader_lookup_lut_model_pinned
					// TEXTURE2: R32UI GlyphId sidecar (one per cell; GLYPH_ID_NONE=0xFFFFFFFF = no extended glyph)
					// TEXTURE3: RGBA8 LUT: GlyphId -> (page_index r, atlas_x g, atlas_y b) each [0,15]
					// TEXTURE4: RGBA8 atlas-of-atlases page (16x16 glyph grid, same layout as font)
					// lut_width=0 disables extended path for all cells (CP437 path unchanged).
					uniform usampler2D sidecar;
					uniform sampler2D lut_tex;
					uniform sampler2D page_atlas;
					uniform float lut_width;
					uniform vec2 page_grid;
					const uint native_glyph_first_id = 512u;
					const uint native_glyph_last_id = 631u;

					/*
					vec3 XTermPal(int p)
					{
						p -= 16;
						if (p < 0 || p >= 216)
							return vec3(0, 0, 0);

						int r = p % 6;
						p = (p - r) / 6;
						int g = p % 6;
						p = (p - g) / 6;

						return vec3(p, g, r) * 0.2;
					}
					*/

					vec3 Pal(float p) {
						p = clamp(floor(p - 16.0 + 0.5), 0.0, 215.0);

						float blue = floor(p / 36.0);
						p -= 36.0 * blue;

						float green = floor(p / 6.0);
						float red = p - 6.0 * green;

						return vec3(blue, green, red) * 0.2;
					}

					void main() {
						// sample ansi buffer
						vec2 quot_cell = floor(cell_coord);
						vec2 frac_cell = fract(cell_coord);

						vec2 ansi_coord = (quot_cell + vec2(0.5)) / ansi_wh;

						vec4 cell = texture(ansi, ansi_coord);

						float glyph_alpha;
						bool diagnostic_failure = false;

						// FL-4131 Phase 3: extended glyph path (atlas-of-atlases)
						// lut_width==0 means no manifest bound; short-circuit to CP437 path.
						// sidecar_id==0xFFFFFFFFu is GLYPH_ID_NONE — no extended glyph for this cell.
						uint sidecar_id = texture(sidecar, ansi_coord).r;
						if (sidecar_id != 0xFFFFFFFFu && sidecar_id > 255u) {
							// Extended: look up LUT to get atlas coordinates.
							// LUT texel: r=page_index (unused; single page_atlas for Phase 3),
							//            g=atlas_x_cell [0,15], b=atlas_y_cell [0,15].
							if (lut_width > 0.5 && sidecar_id >= native_glyph_first_id && sidecar_id <= native_glyph_last_id) {
								float lut_u = (float(sidecar_id) + 0.5) / lut_width;
								vec4 lut_entry = texture(lut_tex, vec2(lut_u, 0.5));
								vec2 atlas_xy = vec2(floor(lut_entry.g * 255.0 + 0.5), floor(lut_entry.b * 255.0 + 0.5));
								vec2 ext_frac = frac_cell;
								ext_frac.y = 1.0 - ext_frac.y;
								vec2 glyph_coord = (atlas_xy + ext_frac) / page_grid;
								glyph_alpha = texture(page_atlas, glyph_coord).a;
							} else {
								diagnostic_failure = true;
								float stem_x = step(0.42, frac_cell.x) * step(frac_cell.x, 0.58);
								float stem_y = step(0.16, frac_cell.y) * step(frac_cell.y, 0.68);
								float dot_x = step(0.40, frac_cell.x) * step(frac_cell.x, 0.60);
								float dot_y = step(0.78, frac_cell.y) * step(frac_cell.y, 0.94);
								glyph_alpha = max(stem_x * stem_y, dot_x * dot_y);
							}
						} else {
							// CP437 path: unchanged from original.
							int glyph_idx = int(round(cell.b * 255.0));
							frac_cell.y = 1.0 - frac_cell.y;
							vec2 glyph_coord = (vec2(glyph_idx & 0xF, glyph_idx >> 4) + frac_cell) / vec2(16.0);
							glyph_alpha = texture(font, glyph_coord).a;
						}

						/*
						vec3 fg_color = XTermPal(int(round(cell.r * 255.0)));
						vec3 bg_color = XTermPal(int(round(cell.g * 255.0)));
						*/

						vec3 fg_color = diagnostic_failure ? vec3(0.0, 0.0, 0.0) : Pal(cell.x * 255.00);
						vec3 bg_color = diagnostic_failure ? vec3(1.0, 0.0, 0.0) : Pal(cell.y * 255.00);

						color = vec4(mix(bg_color, fg_color, glyph_alpha), 1.0);
					});

	GLenum term_st[3] = {GL_VERTEX_SHADER, GL_FRAGMENT_SHADER};
	const char *term_src[3] = {term_vs_src, term_fs_src};
	GLuint term_prg = glCreateProgram();
	if (!term_prg)
	{
		printf("glCreateProgram failed\n");
		exit(-1);
	}

	for (int i = 0; i < 2; i++)
	{
		shader[i] = glCreateShader(term_st[i]);
		if (!shader[i])
		{
			printf("glCreateShader failed\n");
			exit(-1);
		}

		GLint len = (GLint)strlen(term_src[i]);
		glShaderSource(shader[i], 1, &(term_src[i]), &len);
		glCompileShader(shader[i]);

		loglen = 999;
		glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		glAttachShader(term_prg, shader[i]);
	}

	glLinkProgram(term_prg);

	for (int i = 0; i < 2; i++)
		glDeleteShader(shader[i]);

	loglen = 999;
	glGetProgramInfoLog(term_prg, loglen, &loglen, logstr);
	logstr[loglen] = 0;

	if (loglen)
		printf("%s", logstr);

	term->prg = term_prg;

	// WHY runtime binding (glGetUniformLocation) instead of layout(location=N):
	// OpenGL 3.3 compatibility (layout locations require 4.3+). This code targets
	// OpenGL 3.3 core profile for maximum hardware compatibility (lines 590-596).
	term->uni_ansi_vp = glGetUniformLocation(term_prg, "ansi_vp");
	term->uni_ansi = glGetUniformLocation(term_prg, "ansi");
	term->uni_font = glGetUniformLocation(term_prg, "font");
	term->uni_ansi_wh = glGetUniformLocation(term_prg, "ansi_wh");
	term->att_uv = glGetAttribLocation(term_prg, "uv");
	term->out_color = glGetFragDataLocation(term_prg, "color");

	// FL-4131 Phase 3: extended glyph uniform locations
	term->uni_sidecar    = glGetUniformLocation(term_prg, "sidecar");
	term->uni_lut_tex    = glGetUniformLocation(term_prg, "lut_tex");
	term->uni_page_atlas = glGetUniformLocation(term_prg, "page_atlas");
	term->uni_lut_width  = glGetUniformLocation(term_prg, "lut_width");
	term->uni_page_grid  = glGetUniformLocation(term_prg, "page_grid");

	// FL-4131 Phase 3: sidecar texture (R32UI, max_width x max_height)
	// Cleared to GLYPH_ID_NONE (0xFFFFFFFF) = no extended glyph for any cell.
	{
		glGenTextures(1, &term->sidecar_tex);
		glBindTexture(GL_TEXTURE_2D, term->sidecar_tex);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		// Allocate storage; initial data is filled during first frame upload.
		glTexImage2D(GL_TEXTURE_2D, 0, GL_R32UI, TERM_LIST::max_width, TERM_LIST::max_height,
		             0, GL_RED_INTEGER, GL_UNSIGNED_INT, nullptr);
		glBindTexture(GL_TEXTURE_2D, 0);
		// Pre-fill CPU sidecar with GLYPH_ID_NONE (0xFFFFFFFF).
		memset(term->sidecar_buf, 0xFF, sizeof(term->sidecar_buf));
	}

	// FL-4131: bind the compiled material glyph page and GlyphId LUT for native GL.
	// P1+P8: page geometry comes from the file (width/height/cell_px) so the
	// 7-row aliased page0 (256x112) baked by compile_glyph_manifest.py is
	// accepted at its actual extent rather than failing the old strict
	// 256x256 equality check.
	{
		const int lut_width = kTermCompiledGlyphLastId + 1;
		uint8_t* lut_pixels = (uint8_t*)malloc((size_t)lut_width * 4);
		uint8_t* page_pixels = (uint8_t*)malloc((size_t)kTermCompiledGlyphPageWidthPx * kTermCompiledGlyphPageMaxHeightPx * 4);
		int page_w = 0, page_h = 0, page_cell_px = kTermCompiledGlyphCellPx;
		bool atlas_loaded = lut_pixels && page_pixels &&
			TermLoadCompiledGlyphAtlasPage(page_pixels, &page_w, &page_h, &page_cell_px);
		if (lut_pixels)
			TermBuildCompiledGlyphLut(lut_pixels, lut_width, page_cell_px);

		glGenTextures(1, &term->lut_tex_id);
		glBindTexture(GL_TEXTURE_2D, term->lut_tex_id);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		if (atlas_loaded)
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, lut_width, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, lut_pixels);
		else
		{
			static const uint8_t stub_pixel[4] = {0, 0, 0, 0};
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, stub_pixel);
		}
		glBindTexture(GL_TEXTURE_2D, 0);

		glGenTextures(1, &term->page_atlas_id);
		glBindTexture(GL_TEXTURE_2D, term->page_atlas_id);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		if (atlas_loaded)
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, page_w, page_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, page_pixels);
		else
		{
			static const uint8_t stub_pixel[4] = {0, 0, 0, 0};
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, stub_pixel);
		}
		glBindTexture(GL_TEXTURE_2D, 0);

		term->lut_width = atlas_loaded ? (float)lut_width : 0.0f;
		term->page_atlas_cols = atlas_loaded && page_cell_px > 0 ? (float)(page_w / page_cell_px) : 1.0f;
		term->page_atlas_rows = atlas_loaded && page_cell_px > 0 ? (float)(page_h / page_cell_px) : 1.0f;
		if (page_pixels)
			free(page_pixels);
		if (lut_pixels)
			free(lut_pixels);
	}

	float vbo_data[] = {0, 0, 1, 0, 1, 1, 0, 1};

	GLuint term_vbo = 0;
	// glCreateBuffers(1, &term_vbo);
	glGenBuffers(1, &term_vbo);

	if (!term_vbo)
	{
		printf("glCreateBuffers failed\n");
		exit(-1);
	}

	// glNamedBufferStorage(term_vbo, 4 * sizeof(float[2]), 0, GL_DYNAMIC_STORAGE_BIT);
	// glNamedBufferSubData(term_vbo, 0, 4 * sizeof(float[2]), vbo_data);
	glBindBuffer(GL_ARRAY_BUFFER, term_vbo);
	glBufferData(GL_ARRAY_BUFFER, 4 * sizeof(float[2]), vbo_data, GL_STATIC_DRAW);
	glBindBuffer(GL_ARRAY_BUFFER, 0);

	term->vbo = term_vbo;

	GLuint term_vao = 0;
	// glCreateVertexArrays(1, &term_vao);
	glGenVertexArrays(1, &term_vao);

	if (!term_vao)
	{
		printf("glCreateVertexArrays failed\n");
		exit(-1);
	}

	glBindVertexArray(term_vao);
	glBindBuffer(GL_ARRAY_BUFFER, term_vbo);
	glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(float[2]), (void *)0);
	glEnableVertexAttribArray(0);
	glBindVertexArray(0);

	term->vao = term_vao;

	term->prev = term_tail;
	term->next = 0;
	if (term_tail)
		term_tail->next = term;
	else
		term_head = term;
	term_tail = term;

	a3dSetCookie(wnd, term);
	a3dSetIcon(wnd, "./assets/icons/app.png");
	a3dSetVisible(wnd, true);
	a3dSetFocus(wnd);
}

void term_keyb_char(A3D_WND *wnd, wchar_t chr)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);
	term->game->OnKeyb(GAME_KEYB::KEYB_CHAR, (int)chr);
}

void term_keyb_key(A3D_WND *wnd, KeyInfo ki, bool down)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	// if (ki&A3D_AUTO_REPEAT)
	//	return;

	if (down)
	{
		if (ki == A3D_NUMPAD_ADD)
		{
			NextGLFont();
		}
		else if (ki == A3D_NUMPAD_SUBTRACT)
		{
			PrevGLFont();
		}
		else if (ki >= A3D_F5 && ki <= A3D_F8)
		{
			// send press
			if (!(ki & A3D_AUTO_REPEAT))
				term->game->OnKeyb(GAME_KEYB::KEYB_PRESS, ki);
		}
		else if (ki == A3D_F11)
		{
			ToggleFullscreen(term->game);
		}
		else
			term->game->OnKeyb(GAME_KEYB::KEYB_DOWN, ki);
	}
	else if (ki != A3D_F11)
		term->game->OnKeyb(GAME_KEYB::KEYB_UP, ki);
}

void term_keyb_focus(A3D_WND *wnd, bool set)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);
	term->game->OnFocus(set);
}

void term_close(A3D_WND *wnd)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	if (term->game)
	{
		FreeGame(term->game);
		DeleteGame(term->game);
	}

	glDeleteTextures(1, &term->tex);
	glDeleteVertexArrays(1, &term->vao);
	glDeleteBuffers(1, &term->vbo);
	glDeleteProgram(term->prg);

	if (term->close)
		term->close();

	a3dClose(wnd);

	if (term->prev)
		term->prev->next = term->next;
	else
		term_head = term->next;

	if (term->next)
		term->next->prev = term->prev;
	else
		term_tail = term->prev;

	free(term);
}

Game *TermOpen(A3D_WND *share, float yaw, float pos[3], void (*close)())
{
#ifdef EDITOR
	QueueTermBootstrapRequest(yaw, pos, 55);
#endif
	PlatformInterface pi;
	pi.close = term_close;
	pi.render = term_render;
	pi.resize = term_resize;
	pi.init = term_init;
	pi.keyb_char = term_keyb_char;
	pi.keyb_key = term_keyb_key;
	pi.keyb_focus = term_keyb_focus;
	pi.mouse = term_mouse;

	// pi.ptydata = my_ptydata;

	GraphicsDesc gd;
	gd.color_bits = 32;
	gd.alpha_bits = 0;
	gd.depth_bits = 0;
	gd.stencil_bits = 0;

#ifdef USE_GL3
	gd.version[0] = 3;
	gd.version[1] = 3;
#else
	gd.version[0] = 4;
	gd.version[1] = 5;
#endif

	gd.flags = (GraphicsDesc::FLAGS)(GraphicsDesc::DEBUG_CONTEXT | GraphicsDesc::DOUBLE_BUFFER);

	// TODO SAVE IN SETTINGS ON CLEAN EXIT
	int rc[] = {100, 100, 1280, 720};
	gd.wnd_mode = A3D_WND_NORMAL;
	gd.wnd_xywh = rc;

	A3D_WND *wnd = a3dOpen(&pi, &gd, share);

	if (!wnd)
	{
#ifdef EDITOR
		g_term_bootstrap_request.valid = false;
#endif
		return 0;
	}

	// a3dSetRect(wnd, 0, A3D_WND_FULLSCREEN);
	// a3dSetVisible(share, false);

	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);
	term->close = close;

	/*
	term->yaw = yaw;
	term->pos[0] = pos[0];
	term->pos[1] = pos[1];
	term->pos[2] = pos[2];
	term->water = 0;
	*/

	return term->game;
}

void TermCloseAll()
{
	A3D_PUSH_CONTEXT push;
	a3dPushContext(&push);

	TERM_LIST *term = term_head;
	while (term)
	{
		TERM_LIST *next = term->next;

		a3dSwitchContext(term->wnd);
		term_close(term->wnd);
		term = next;
	}

	a3dPopContext(&push);

	term_head = 0;
	term_tail = 0;
}

void TermResizeAll()
{
	int wh[2];
	TERM_LIST *term = term_head;
	while (term)
	{
		TERM_LIST *next = term->next;
		a3dGetRect(term->wnd, 0, wh);
		term_resize(term->wnd, wh[0], wh[1]);
		term = next;
	}
}

static int TermApplyPlayerSkinToGame(Game* g, uint16_t requested_skin_id)
{
	if (!g)
		return 0;

	uint16_t skin_id = requested_skin_id;
	if (skin_id == 0)
	{
		if (!EnsureLocalPlayerAppearance(g->player))
			return 0;
		skin_id = g->player.appearance_v2.skin_definition_id;
	}

	if (skin_id != 0)
	{
		World* w = g->player.player_inst ? GetInstWorld(g->player.player_inst) : world;
		if (!w) w = world;
		bool ok = ApplyLocalPlayerSkin(g->player, skin_id, g->stamp, w);
		if (ok)
		{
			if (!g->player.player_inst && world)
				EnsureLocalPlayerInst(g, g->player.sprite, g->player.pos, g->player.dir, g->player.anim, g->player.frame);
			return 1;
		}
	}

	return 0;
}

int TermApplyPlayerSkinId(uint16_t skin_id)
{
	int updated = 0;
	TERM_LIST* term = term_head;
	while (term)
	{
		updated += TermApplyPlayerSkinToGame(term->game, skin_id);
		term = term->next;
	}
	return updated;
}

int TermApplyPlayerSkin()
{
	return TermApplyPlayerSkinId(0);
}

void ToggleFullscreen(Game *g)
{
	TERM_LIST *term = term_head;
	while (term)
	{
		if (term->game == g)
			break;
		term = term->next;
	}

	if (!term)
		return;

	if (a3dGetRect(term->wnd, 0, 0) != A3D_WND_FULLSCREEN)
		a3dSetRect(term->wnd, 0, A3D_WND_FULLSCREEN);
	else
		a3dSetRect(term->wnd, 0, A3D_WND_NORMAL);
}

bool IsFullscreen(Game *g)
{
	TERM_LIST *term = term_head;
	while (term)
	{
		if (term->game == g)
			break;
		term = term->next;
	}

	if (!term)
		return false;

	if (a3dGetRect(term->wnd, 0, 0) == A3D_WND_FULLSCREEN)
		return true;
	return false;
}

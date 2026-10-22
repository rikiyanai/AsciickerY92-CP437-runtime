
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#define _USE_MATH_DEFINES
#include <math.h>
#include <algorithm>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
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
#include "../engine/render/fl4208_surface_identity.h"
// Product behavior routes through the neutral port tables below; shared
// keeps generic terminal/device ops. No product include lives here.
#include "../engine/render/renderer_ports_v1.h"
#include "../engine/render/render_stages.h"
#include "../engine/fl4482_native_atlas_lut.h"
#include "../engine/fl4482_atlas_png_loader.h"

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
// - term->buf[max_width × max_height] (line 61): AnsiCell storage capacity.
//   Game::Render fills the live viewport compactly as width × height.
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
#include "../engine/physics_commands.h"

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
	// FL-4876 CELL-RES-01: Classic now sizes cells like Godot (fixed cell px),
	// so the grid grows with the drawable. 480x270 covers a 3840x2160 drawable
	// at 8 px cells (320x180 at 12 px). The FL-4512 product keeps 160x90: its
	// bridge/transport planes are 160-strided (apps/fl4512-runtime).
#if defined(ASCIICKER_RENDERER_CLASSIC_CP437) && ASCIICKER_RENDERER_CLASSIC_CP437
	static const int max_width = 480;
	static const int max_height = 270;
#else
	static const int max_width = 160; // 160;
	static const int max_height = 90; // 90;
#endif
	// [DATA-CONTRACT:ANSI] AnsiCell buffer: {fg, bk, gl, spare} per cell (render.h lines 37-45).
	// fg/bk are xterm palette indices (0-255), gl is CP437 glyph code (0-255), spare is 0xFF if rendered.
	AnsiCell buf[max_width * max_height];
	int last_render_w;
	int last_render_h;
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

	// The neutral observation plane records what the selected presenter emitted.
	// Classic copies AnsiCell.gl; Unicode copies the committed final tuple.
	GlyphId final_glyph_buf[max_width * max_height];
	GLuint final_glyph_tex; // R32UI: final GlyphId for every presented cell
	GLuint lut_tex_id;     // RGBA8: GlyphId→(page,x,y) LUT (1×1 stub when no manifest)
	GLuint page_atlas_id;  // RGBA8 2D array: one compiler-owned layer per physical page
	int lut_columns;
	int lut_rows;
	float page_atlas_cols;
	float page_atlas_rows;
	GLint uni_final_glyph; // final GlyphId sampler (TEXTURE2)
	GLint uni_lut_tex;     // lut_tex sampler (TEXTURE3)
	GLint uni_page_atlas;  // page_atlas sampler (TEXTURE4)
	GLint uni_lut_grid;    // (0,0) = no compiler registry bound
	GLint uni_lut_low_limit;
	GLint uni_lut_universal_base;
	GLint uni_page_grid;   // atlas page dimensions in cells, e.g. 16x7

	};

static const int kTermCompiledGlyphCellPx = 16;
// FL-4131 P1+P8: page0 is the 16px alias of the compiled multi-size ladder
// (compile_glyph_manifest.py --compile emits page<cell_px>_rgba8.json plus
// a page0 alias pointing at the canonical 16px page). Width is always
// ATLAS_COLS * cell_px = 16 * 16 = 256, but height depends on the number of
// admitted glyphs (currently 7 rows * 16 px = 112).

static bool TermReadWholeFile(const char* path, char** out_text);

	// =============================================================================
	// Selected presentation adapter — exactly one final-cell writer per product.
	// =============================================================================
	// Classic copies the current CP437 source byte. Current commits through the
	// single product committer Fl4693NativeFinalCellCommitAndCopyV1 (native
	// adapter + Web remain its only callers); this TU keeps the Classic copy
	// and the shared GL upload only. No second writer is added here.
static void TermCommitPresentationCellsV1(TERM_LIST* term, int width, int height,
	int upload_w, int upload_h, int max_w)
{
	(void)upload_w;
	(void)upload_h;
	(void)max_w;
{
	const PresentationBackendV1* commit_nrt = AsciickerPresentation();
	if (commit_nrt && commit_nrt->commit_presentation_cells)
		commit_nrt->commit_presentation_cells(term->buf,
			term->final_glyph_buf, width, height);
}
}

static bool g_term_player_sprite_visible = true;
static int TermApplyPlayerSpriteVisibilityToGame(Game* g);
static bool g_term_environment_animation_enabled = true;
static uint64_t g_term_environment_animation_revision = 0;
static int TermApplyEnvironmentalAnimationToGame(Game* g);
static void TermApplyCleanInspectionUi(Game* g)
{
	if (!g)
		return;
	g->ui.show_minimap = false;
	g->ui.show_buts = false;
	g->ui.show_inventory = false;
	g->ui.keyb_hide = 1000;
}

// FL-4131 P3 (2026-06-03): TERM++ pixel capture path. The editor MCP issues
// CAPTURE_TERMPP_FRAME <path>; the next term_render finishes its glDrawArrays,
// then glReadPixels into a heap buffer, flips rows, and writes a PNG via
// stbi_write_png (provided by editor/asciiid.cpp's STB_IMAGE_WRITE_IMPLEMENTATION).
// Capture happens INSIDE the TERM++ window context, so pixels are TERM++ pixels,
// not editor viewport pixels.
#ifdef EDITOR
static char g_term_capture_path[2048] = {0};
static char g_term_capture_buffer_path[2048] = {0};
static char g_term_capture_bridge_path[2048] = {0};
// FL-4588: the native presenter owns the only live authoring-frame copy.
// The loopback transport can request and copy this observation, but it cannot
// invoke rendering, sample an atlas, reconstruct a palette, plus mutate term
// state. Both callbacks run on the platform render thread.
static bool g_term_authoring_frame_requested = false;
static std::vector<uint8_t> g_term_authoring_frame_rgba;
static int g_term_authoring_frame_w = 0;
static int g_term_authoring_frame_h = 0;
static uint64_t g_term_authoring_frame_id = 0;
// Native renderer frame whose final TERM++ pixels are held below. This is the
// same fl4208 bridge-frame identity serialized by rendered_buffer, bridge v16,
// and trace v6; the monotonic authoring_frame_id is transport liveness only.
static uint64_t g_term_authoring_frame_source_frame = 0;
static char g_term_authoring_frame_sha256[65] = {};
static char g_term_authoring_frame_view_state_sha256[65] = {};
static uint64_t g_term_authoring_frame_environment_presentation_packed = 0;
struct TermAuthoringInspectionSnapshotV1
{
	TermAuthoringInspectionFrameV1 identity = {};
	std::vector<TermCellInspectionV1> cells;
};
static const size_t kTermAuthoringInspectionHistory = 8;
static TermAuthoringInspectionSnapshotV1
	g_term_authoring_inspection_history[kTermAuthoringInspectionHistory];
static size_t g_term_authoring_inspection_cursor = 0;
extern "C" int AsciiidCopyCurrentAuthoringViewStateSha256(char out_sha256[65]);
extern "C" int stbi_write_png(const char* filename, int x, int y, int comp, const void* data, int stride_bytes);
extern "C" void TermRequestCaptureWithRenderedBufferAndBridge(const char* png_path, const char* rendered_buffer_path, const char* bridge_path);

extern "C" void TermRequestAuthoringFrameRgba()
{
	g_term_authoring_frame_requested = true;
}

extern "C" int TermCopyLatestAuthoringFrameRgba(
	void* destination, size_t capacity, size_t* out_size,
	int* out_w, int* out_h, uint64_t* out_frame_id,
	char out_sha256[65], char out_view_state_sha256[65],
	uint64_t* out_environment_presentation_packed)
{
	const size_t size = g_term_authoring_frame_rgba.size();
	if (out_size) *out_size = size;
	if (out_w) *out_w = g_term_authoring_frame_w;
	if (out_h) *out_h = g_term_authoring_frame_h;
	if (out_frame_id) *out_frame_id = g_term_authoring_frame_id;
	if (out_environment_presentation_packed)
		*out_environment_presentation_packed =
			g_term_authoring_frame_environment_presentation_packed;
	if (out_sha256)
	{
		memcpy(out_sha256, g_term_authoring_frame_sha256,
			sizeof(g_term_authoring_frame_sha256));
	}
	if (out_view_state_sha256)
	{
		memcpy(out_view_state_sha256, g_term_authoring_frame_view_state_sha256,
			sizeof(g_term_authoring_frame_view_state_sha256));
	}
	if (size == 0 || g_term_authoring_frame_w <= 0 ||
		g_term_authoring_frame_h <= 0 || g_term_authoring_frame_id == 0)
		return 1;
	if (!destination) return 0;
	if (capacity < size) return -1;
	memcpy(destination, g_term_authoring_frame_rgba.data(), size);
	return 0;
}

extern "C" void TermRequestCapture(const char* path)
{
	if (!path || !path[0])
	{
		g_term_capture_path[0] = 0;
		g_term_capture_buffer_path[0] = 0;
		g_term_capture_bridge_path[0] = 0;
		return;
	}
	size_t n = strlen(path);
	if (n >= sizeof(g_term_capture_path))
		n = sizeof(g_term_capture_path) - 1;
	memcpy(g_term_capture_path, path, n);
	g_term_capture_path[n] = 0;
	g_term_capture_buffer_path[0] = 0;
	g_term_capture_bridge_path[0] = 0;
}

extern "C" void TermRequestCaptureWithRenderedBuffer(const char* png_path, const char* rendered_buffer_path)
{
	TermRequestCaptureWithRenderedBufferAndBridge(png_path, rendered_buffer_path, NULL);
}

extern "C" void TermRequestCaptureWithRenderedBufferAndBridge(const char* png_path, const char* rendered_buffer_path, const char* bridge_path)
{
	TermRequestCapture(png_path);
	if (!rendered_buffer_path || !rendered_buffer_path[0])
	{
		g_term_capture_buffer_path[0] = 0;
		return;
	}
	size_t n = strlen(rendered_buffer_path);
	if (n >= sizeof(g_term_capture_buffer_path))
		n = sizeof(g_term_capture_buffer_path) - 1;
	memcpy(g_term_capture_buffer_path, rendered_buffer_path, n);
	g_term_capture_buffer_path[n] = 0;
	if (!bridge_path || !bridge_path[0])
	{
		g_term_capture_bridge_path[0] = 0;
		return;
	}
	n = strlen(bridge_path);
	if (n >= sizeof(g_term_capture_bridge_path))
		n = sizeof(g_term_capture_bridge_path) - 1;
	memcpy(g_term_capture_bridge_path, bridge_path, n);
	g_term_capture_bridge_path[n] = 0;
}
#endif

// FL-4131 P3: forward declared, defined after TermReadWholeFile.

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
static void TermSha256Hex(const void* data, size_t size, char out[65])
{
	uint8_t digest[32];
	Fl4208Sha256Bytes(data, size, digest);
	static const char* hex = "0123456789abcdef";
	for (int i = 0; i < 32; i++)
	{
		out[2 * i] = hex[digest[i] >> 4];
		out[2 * i + 1] = hex[digest[i] & 15];
	}
	out[64] = 0;
}

// FL-4482: the generated exact registry is the sole native atlas-address
// owner. Each physical (pack,page) is content-hash checked, then copied into
// its compiled texture-array layer. A missing, stale, malformed page rejects
// the whole transaction; pack-only layer inference and concatenated-page
// fallback are deleted.
static bool TermLoadCompiledGlyphAtlasLayers(
	uint8_t** out_pixels, int* out_width, int* out_height,
	int* out_layers, int* out_cell_px)
{
	if (out_pixels) *out_pixels = NULL;
	if (out_width) *out_width = 0;
	if (out_height) *out_height = 0;
	if (out_layers) *out_layers = 0;
	if (out_cell_px) *out_cell_px = 0;
	const int width = (int)FL4482_NATIVE_ATLAS_COLUMNS * kTermCompiledGlyphCellPx;
	int max_rows = 0;
	for (size_t layer = 0; layer < FL4482_NATIVE_ATLAS_PAGE_COUNT; layer++)
		max_rows = std::max(max_rows, (int)FL4482_NATIVE_ATLAS_PAGE_ROW_COUNTS[layer]);
	const int height = max_rows * kTermCompiledGlyphCellPx;
	const int layers = (int)FL4482_NATIVE_ATLAS_PAGE_COUNT;
	const size_t layer_bytes = (size_t)width * height * 4;
	uint8_t* pixels = (uint8_t*)calloc(layer_bytes * (size_t)layers, 1);
	if (!pixels)
		return false;
	for (size_t layer = 0; layer < FL4482_NATIVE_ATLAS_PAGE_COUNT; layer++)
	{
		// 4096: resolver record capacity (product-owned). Path resolution lives
		// product-side (Fl4512PresenterResolveAtlasPagePathV1); fail closed
		// when uninstalled.
		char page_path[4096];
		const PresentationBackendV1* atlas_nrt = AsciickerPresentation();
		const bool page_resolved = atlas_nrt &&
			atlas_nrt->resolve_atlas_page_path &&
			atlas_nrt->resolve_atlas_page_path(
				FL4482_NATIVE_ATLAS_PAGE16_PATHS[layer], page_path,
				(int)sizeof(page_path));
		if (!page_resolved)
		{
			free(pixels);
			return false;
		}
		const int expected_height = (int)FL4482_NATIVE_ATLAS_PAGE_ROW_COUNTS[layer] * kTermCompiledGlyphCellPx;
		const size_t expected_count = (size_t)width * (size_t)expected_height * 4u;
		uint8_t* source = NULL;
		size_t source_size = 0;
		bool ok = Fl4482LoadAtlasPngRgba8(
			page_path, width, expected_height, &source, &source_size) != 0 &&
			source_size == expected_count;
		char observed[65] = {};
		if (ok) TermSha256Hex(source, expected_count, observed);
		ok = ok && strcmp(observed, FL4482_NATIVE_ATLAS_PAGE16_SHA256[layer]) == 0;
		if (ok)
		{
			const size_t destination = layer * layer_bytes;
			memcpy(pixels + destination, source, expected_count);
		}
		free(source);
		if (!ok)
		{
			free(pixels);
			return false;
		}
	}
	if (out_pixels) *out_pixels = pixels;
	if (out_width) *out_width = width;
	if (out_height) *out_height = height;
	if (out_layers) *out_layers = layers;
	if (out_cell_px) *out_cell_px = kTermCompiledGlyphCellPx;
	return true;
}

static void TermBuildCompiledGlyphLut(uint8_t* lut)
{
	memset(lut, 0, (size_t)FL4482_NATIVE_ATLAS_LUT_COLUMNS *
		(size_t)FL4482_NATIVE_ATLAS_LUT_ROWS * 4u);
	for (size_t i = 0; i < FL4482_NATIVE_ATLAS_BINDING_COUNT; i++)
	{
		const Fl4482NativeAtlasBindingV1* binding = &FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[i];
		const uint32_t slot = Fl4482NativeAtlasLutSlot(binding->glyph_id);
		if (slot == UINT32_MAX || slot >= FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT)
			continue;
		lut[slot * 4 + 0] = binding->layer_index;
		lut[slot * 4 + 1] = (uint8_t)binding->source_column;
		lut[slot * 4 + 2] = (uint8_t)binding->source_row;
		lut[slot * 4 + 3] = 255;
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
static TERM_LIST* g_term_last_rendered = NULL;
static uint64_t TermPresentationFrameIdV1()
{
	// Final bridge-frame identity lives product-side. Fail closed (0) when
	// uninstalled.
	const PresentationBackendV1* frame_nrt = AsciickerPresentation();
	if (frame_nrt && frame_nrt->bridge_frame_id)
		return frame_nrt->bridge_frame_id();
	return 0;
}

	// FL-4131 item 4 step 4b (2026-06-07): Narrow offscreen-present API.
//
// The standalone TERM++ window keeps its existing render path. When ASCIIID
// wants to embed TERM++ inside its own window, it:
//   1. Calls TermAllocEmbedRenderTarget(w, h) once. That creates an FBO with
//      a color texture sized for the embed.
//   2. Each frame, BEFORE calling term_render, calls
//      TermSetRenderTargetOverride(fbo, w, h). The override redirects the
//      term_render present-stage glClear/glViewport/glDraw to the FBO and
//      suppresses the window-title sync (which is window-coupled).
//   3. Calls term_render(hidden_wnd) as usual.
//   4. After render, calls TermSetRenderTargetOverride(0, 0, 0) to restore.
//   5. Displays the color texture via ImGui::Image.
//
// Standalone TERM++ continues using the same code path with override=0. We do
// NOT fork rendering logic. This is the smallest real FBO-backed presenter
// mount that preserves the standalone path. FL-3955.
static GLuint g_term_render_target_fbo = 0;
static int g_term_render_target_w = 0;
static int g_term_render_target_h = 0;
// FL-4207 step 2/3 (2026-06-09): in-process accessors for the editor UI.
// Same identity rule as TermDumpRenderedBuffer: final_gid always comes from
// the one 32-bit presentation plane. These accessors never allocate, never
// block, and never mutate term state.
int TermGetRenderDims(int* out_w, int* out_h)
{
	TERM_LIST* term = g_term_last_rendered ? g_term_last_rendered : term_head;
	if (!term)
		return -1;
	int w = term->last_render_w > 0 ? term->last_render_w : TERM_LIST::max_width;
	int h = term->last_render_h > 0 ? term->last_render_h : TERM_LIST::max_height;
	if (w > TERM_LIST::max_width)  w = TERM_LIST::max_width;
	if (h > TERM_LIST::max_height) h = TERM_LIST::max_height;
	if (out_w) *out_w = w;
	if (out_h) *out_h = h;
	return 0;
}

int TermReadCell(int x, int y,
	uint32_t* out_final_gid, uint32_t* out_sidecar_gid,
	uint8_t* out_extended, uint8_t* out_cp437)
{
	TERM_LIST* term = g_term_last_rendered ? g_term_last_rendered : term_head;
	if (!term)
		return -1;
	int w = term->last_render_w > 0 ? term->last_render_w : TERM_LIST::max_width;
	int h = term->last_render_h > 0 ? term->last_render_h : TERM_LIST::max_height;
	if (w > TERM_LIST::max_width)  w = TERM_LIST::max_width;
	if (h > TERM_LIST::max_height) h = TERM_LIST::max_height;
	if (x < 0 || y < 0 || x >= w || y >= h)
		return -2;
	const int idx = y * w + x;
	const AnsiCell& c = term->buf[idx];
	const uint32_t final_gid = (uint32_t)term->final_glyph_buf[idx];
	const bool extended = final_gid > 255u && !glyph_id_is_sentinel((GlyphId)final_gid);
	const uint32_t sidecar = extended ? final_gid : (uint32_t)GLYPH_ID_NONE;
	if (out_final_gid)   *out_final_gid = final_gid;
	if (out_sidecar_gid) *out_sidecar_gid = sidecar;
	if (out_extended)    *out_extended = extended ? 1 : 0;
	if (out_cp437)       *out_cp437 = (uint8_t)c.gl;
	return 0;
}

int TermReadCellVisual(int x, int y,
	uint32_t* out_final_gid, uint32_t* out_sidecar_gid,
	uint8_t* out_extended, uint8_t* out_cp437,
	uint8_t* out_fg, uint8_t* out_bk)
{
	TERM_LIST* term = g_term_last_rendered ? g_term_last_rendered : term_head;
	if (!term)
		return -1;
	int w = term->last_render_w > 0 ? term->last_render_w : TERM_LIST::max_width;
	int h = term->last_render_h > 0 ? term->last_render_h : TERM_LIST::max_height;
	if (w > TERM_LIST::max_width)  w = TERM_LIST::max_width;
	if (h > TERM_LIST::max_height) h = TERM_LIST::max_height;
	if (x < 0 || y < 0 || x >= w || y >= h)
		return -2;
	const int idx = y * w + x;
	const AnsiCell& c = term->buf[idx];
	const uint32_t final_gid = (uint32_t)term->final_glyph_buf[idx];
	const bool extended = final_gid > 255u && !glyph_id_is_sentinel((GlyphId)final_gid);
	const uint32_t sidecar = extended ? final_gid : (uint32_t)GLYPH_ID_NONE;
	if (out_final_gid)   *out_final_gid = final_gid;
	if (out_sidecar_gid) *out_sidecar_gid = sidecar;
	if (out_extended)    *out_extended = extended ? 1 : 0;
	if (out_cp437)       *out_cp437 = (uint8_t)c.gl;
	if (out_fg)          *out_fg = c.fg;
	if (out_bk)          *out_bk = c.bk;
	return 0;
}

static int TermFillCellInspectionV1(
	TERM_LIST* term, int x, int y, TermCellInspectionV1* out)
{
	if (!out)
		return -1;
	// Product inspection lives product-side. This wrapper resolves the
	// presented dims/planes from TERM_LIST (the struct never crosses) and
	// routes; fail closed when uninstalled.
	if (!term)
	{
		memset(out, 0, sizeof(*out));
		return -2;
	}
	{
		int w = term->last_render_w > 0 ? term->last_render_w : TERM_LIST::max_width;
		int h = term->last_render_h > 0 ? term->last_render_h : TERM_LIST::max_height;
		if (w > TERM_LIST::max_width) w = TERM_LIST::max_width;
		if (h > TERM_LIST::max_height) h = TERM_LIST::max_height;
		{
			const InspectionPortV1* inspect_nrt = AsciickerInspection();
			if (inspect_nrt && inspect_nrt->inspect_cell)
				return inspect_nrt->inspect_cell(term->buf,
					term->final_glyph_buf, w, h, x, y, out);
		}
		memset(out, 0, sizeof(*out));
		return -2;
	}
}

int TermReadCellInspectionV1(int x, int y, TermCellInspectionV1* out)
{
	TERM_LIST* term = g_term_last_rendered ? g_term_last_rendered : term_head;
	return TermFillCellInspectionV1(term, x, y, out);
}

#ifdef EDITOR
int TermFindAuthoringInspectionFrameV1(
	uint64_t frame_id, const char* view_state_sha256,
	TermAuthoringInspectionFrameV1* out)
{
	if (!out || !view_state_sha256 || strlen(view_state_sha256) != 64)
		return -1;
	for (size_t age = 0; age < kTermAuthoringInspectionHistory; ++age)
	{
		const size_t slot =
			(g_term_authoring_inspection_cursor + kTermAuthoringInspectionHistory - 1 - age) %
			kTermAuthoringInspectionHistory;
		const TermAuthoringInspectionSnapshotV1& snapshot =
			g_term_authoring_inspection_history[slot];
		if (snapshot.identity.frame_id == 0)
			continue;
		if (frame_id != 0 && snapshot.identity.frame_id != frame_id)
			continue;
		if (strcmp(snapshot.identity.view_state_sha256, view_state_sha256) != 0)
			continue;
		*out = snapshot.identity;
		return 0;
	}
	return -2;
}

int TermReadAuthoringFrameCellInspectionV1(
	uint64_t frame_id, int x, int y, TermCellInspectionV1* out)
{
	if (!out || frame_id == 0)
		return -1;
	for (size_t age = 0; age < kTermAuthoringInspectionHistory; ++age)
	{
		const size_t slot =
			(g_term_authoring_inspection_cursor + kTermAuthoringInspectionHistory - 1 - age) %
			kTermAuthoringInspectionHistory;
		const TermAuthoringInspectionSnapshotV1& snapshot =
			g_term_authoring_inspection_history[slot];
		if (snapshot.identity.frame_id != frame_id)
			continue;
		if (x < 0 || y < 0 || x >= snapshot.identity.grid_w ||
			y >= snapshot.identity.grid_h)
			return -2;
		const size_t index = static_cast<size_t>(y) *
			static_cast<size_t>(snapshot.identity.grid_w) + static_cast<size_t>(x);
		if (index >= snapshot.cells.size() || !snapshot.cells[index].valid)
			return -3;
		*out = snapshot.cells[index];
		return 0;
	}
	return -4;
}
#endif

// =============================================================
// FL-4588 task 40A: one immutable in-memory frame-artifact record
// =============================================================
// The rendered-buffer and bridge-facts serializers below now build their JSONL
// into memory (open_memstream) ONCE per call. The file diagnostics derive from
// those exact same bytes (they just fwrite the captured buffer), and the
// authenticated artifact reads serve the same bytes without ever touching a
// temp file. Both artifacts are captured from the same final frame; a frame
// that advanced between the two builds is a mismatch and fails closed.
struct Fl4588FrameArtifactRecord
{
	bool rb_valid = false;
	unsigned long long rb_frame = 0;
	std::string rendered_buffer;
	bool bf_valid = false;
	unsigned long long bf_frame = 0;
	std::string bridge_facts;
	// FL-4588 task 53: the exact fl4548.termpp.cell_trace.v7 JSONL captured once
	// in memory for the LAST rendered frame. Unlike rendered_buffer / bridge_facts
	// (which the capture rebuilds on demand from persistent term state), the cell
	// trace is a per-frame DIFF stream that can only be observed during the frame
	// that produced it, so the render-thread emitter deposits it here each frame.
	bool ct_valid = false;
	unsigned long long ct_frame = 0;
	std::string cell_trace;
};
static Fl4588FrameArtifactRecord g_fl4588_frame_artifacts;

// FL-4588 task 53: once a caller has requested an artifact capture, the cell
// trace becomes an armed observation — the render-thread emitter builds its JSONL
// into memory every frame so a subsequent same-frame capture can freeze it. When
// unarmed (plain game with no capture consumer and no diagnostic file) the
// emitter stays on its cheap path and does no work.
static bool g_fl4588_cell_trace_capture_armed = false;

static void Fl4588SetRenderedBufferRecord(const char* data, size_t n,
	unsigned long long frame)
{
	g_fl4588_frame_artifacts.rendered_buffer.assign(data, n);
	g_fl4588_frame_artifacts.rb_frame = frame;
	g_fl4588_frame_artifacts.rb_valid = n > 0;
}
static void Fl4588SetBridgeFactsRecord(const char* data, size_t n,
	unsigned long long frame)
{
	g_fl4588_frame_artifacts.bridge_facts.assign(data, n);
	g_fl4588_frame_artifacts.bf_frame = frame;
	g_fl4588_frame_artifacts.bf_valid = n > 0;
}
static void Fl4588SetCellTraceRecord(const char* data, size_t n,
	unsigned long long frame)
{
	// ct_valid means "a trace was DEPOSITED for this frame", not "non-empty": a
	// frame with zero cell events is a legitimate empty trace (the cell_trace
	// schema requires one event PER LINE, so a no-change frame is genuinely zero
	// lines). Emptiness is distinguished from absence by this flag, not by size.
	g_fl4588_frame_artifacts.cell_trace.assign(data ? data : "", data ? n : 0);
	g_fl4588_frame_artifacts.ct_frame = frame;
	g_fl4588_frame_artifacts.ct_valid = true;
}

// FL-4588 task 40B: bounded ring of immutable per-phase artifact snapshots.
// DELETE FIRST: the implicit capture-on-first-GET plus forever cache is gone.
// An authenticated capture request builds both artifacts from ONE final frame
// into a NEW snapshot with a monotonic id and returns its frame identity; reads
// require that id and return its exact immutable bytes. A later capture (after a
// state-revision + presentation advance) yields a newer frame while the older
// snapshot stays immutable until evicted from the ring (bounded lifecycle);
// unknown/expired/evicted ids fail closed. g_fl4588_frame_artifacts above is the
// transient build buffer the serializers deposit into; a capture copies it into
// a ring slot.
struct Fl4588ArtifactSnapshot
{
	unsigned long long id = 0;
	unsigned long long frame = 0;
	std::string rendered_buffer;
	std::string bridge_facts;
	// FL-4588 task 53: the same-frame cell trace, frozen alongside the other two
	// artifacts under this snapshot id. cell_trace_valid is true when a trace was
	// frozen on THIS snapshot's frame (the trace itself may be legitimately empty
	// for a no-change frame); false when the observation was not yet armed or the
	// available trace was for a different frame (read fails closed).
	std::string cell_trace;
	bool cell_trace_valid = false;
	// FL-4588 task 56: genuine final-frame facts bound to THIS snapshot id so a
	// sealed transaction receipt can cite them (never the rendered-buffer hash as a
	// stand-in for the final frame). final_frame_sha256 is the actual presented
	// TERM++ RGBA frame hash; authoring_frame_id is that frame's monotonic id (used
	// to prove the receipt reads a POST-mutation, post-presentation frame);
	// cell_trace_sha256 is the hash of the frozen cell-trace bytes.
	std::string final_frame_sha256;
	unsigned long long authoring_frame_id = 0;
	std::string cell_trace_sha256;
};
static const int kFl4588ArtifactRingSize = 8;
static Fl4588ArtifactSnapshot g_fl4588_artifact_ring[kFl4588ArtifactRingSize];
static unsigned long long g_fl4588_artifact_next_id = 1;
// Render-thread-owned coherent source for immutable capture minting. It is
// replaced only after one requested TERM++ RGBA frame has also published its
// rendered buffer, bridge v16 facts, and trace v6 record on the same bridge
// frame. The HTTP capture endpoint may copy this record; it cannot rebuild a
// newer tuple plane beside older pixels.
static Fl4588ArtifactSnapshot
	g_fl4588_authoring_frame_artifacts[kFl4588ArtifactRingSize];

// FL-4588 task 53/53B: arm the cell-trace observation. NOT called at startup
// (task 53B delete-first): the normal editor must not serialize the trace every
// frame with no consumer. Native packaging binds this to the service armer, which
// the HTTP handshake handler invokes ONLY when an authenticated evidence-viewer
// session holding artifact.read connects. Once armed, the render-thread emitter
// deposits the fl4548.termpp.cell_trace.v7 JSONL into memory every frame, so the
// evidence viewer's first capture already carries all three artifacts on one
// frame with no warm-up. The authoring product session keeps the unarmed path.
extern "C" void TermArmCellTraceCapture(void)
{
	g_fl4588_cell_trace_capture_armed = true;
}

// FL-4588 task 67/67A: read-only SAME-FRAME rendered_buffer + bridge_facts
// observation for the UNAUTHENTICATED authoring state_snapshot. This is the
// smallest renderer-pair seam: it rebuilds rb + bf from ONE final frame using the
// SAME in-memory serializers TermCaptureArtifactSnapshotC uses (TermDumpRenderedBuffer
// /TermDumpBridgeCells with a NULL path => build the transient scratch record only),
// but it deliberately:
//   * mints NO immutable snapshot id (no proof artifact — the sole three-way
//     rb/bf/ct mint stays TermCaptureArtifactSnapshotC + /api/v1/artifacts/capture),
//   * requires NO cell_trace and NEVER arms the trace (so the authoring session
//     reads state_snapshot without an evidence viewer and without permanently
//     enabling cell-trace serialization),
//   * does NO blocking wait on the render thread (it reads the last presented
//     frame's persistent term state, exactly as the capture's rb/bf rebuild does).
// A bounded retry rides past a transient where a render advanced between the two
// reads; if a single frame cannot carry both, it returns 0 (fail closed) and the
// state-snapshot builder answers a named 503. Returned pointers live in the shared
// build buffer and are valid until the next observation/capture; the caller hashes
// them immediately.
extern "C" int TermObserveRenderedPairC(
	const char** rendered_buffer, size_t* rendered_buffer_len,
	const char** bridge_facts, size_t* bridge_facts_len,
	unsigned long long* frame_out)
{
	Fl4588FrameArtifactRecord& scratch = g_fl4588_frame_artifacts;
	for (int attempt = 0; attempt < 4; ++attempt)
	{
		scratch.rb_valid = scratch.bf_valid = false;
		(void)TermDumpRenderedBuffer(NULL);  // NULL => build scratch only
		(void)TermDumpBridgeCells(NULL);
		// Same-frame pair on one live frame. cell_trace is intentionally NOT part
		// of this observation — it is the evidence-viewer-armed proof artifact only.
		if (scratch.rb_valid && scratch.bf_valid &&
			scratch.rb_frame == scratch.bf_frame && scratch.rb_frame != 0 &&
			!scratch.rendered_buffer.empty() && !scratch.bridge_facts.empty())
		{
			if (rendered_buffer) *rendered_buffer = scratch.rendered_buffer.c_str();
			if (rendered_buffer_len) *rendered_buffer_len = scratch.rendered_buffer.size();
			if (bridge_facts) *bridge_facts = scratch.bridge_facts.c_str();
			if (bridge_facts_len) *bridge_facts_len = scratch.bridge_facts.size();
			if (frame_out) *frame_out = scratch.rb_frame;
			return 1;
		}
	}
	return 0;  // fail closed: no single live frame carries both rb + bf
}

extern "C" unsigned long long TermCaptureArtifactSnapshotC(
	unsigned long long expected_authoring_frame_id,
	const char* expected_final_frame_sha256,
	unsigned long long* frame_out)
{
	// FL-4588 task 53B: a capture does NOT self-arm. The sole arm is the
	// authenticated evidence-viewer artifact.read handshake (TermArmCellTraceCapture
	// via the service armer). If the trace was never armed, the cell trace is absent
	// and the three-way same-frame join below fails closed (no id) — which is the
	// correct outcome for an unarmed session, not a reason to silently self-arm.
	// DELETE FIRST (FL-4652): the endpoint no longer rebuilds rb/bf from the
	// latest terminal state beside an earlier pixel hash. Only the render-thread
	// record frozen with the requested RGBA frame is eligible to mint an id.
	if (expected_authoring_frame_id == 0 || !expected_final_frame_sha256 ||
		std::strlen(expected_final_frame_sha256) != 64)
		return 0;
	const Fl4588ArtifactSnapshot* source = nullptr;
	for (const Fl4588ArtifactSnapshot& retained :
		g_fl4588_authoring_frame_artifacts)
	{
		if (retained.authoring_frame_id == expected_authoring_frame_id &&
			retained.final_frame_sha256 == expected_final_frame_sha256)
		{
			source = &retained;
			break;
		}
	}
	if (!source || source->frame == 0 || source->rendered_buffer.empty() ||
		source->bridge_facts.empty() || !source->cell_trace_valid)
		return 0;
	const unsigned long long id = g_fl4588_artifact_next_id++;
	Fl4588ArtifactSnapshot& slot =
		g_fl4588_artifact_ring[id % kFl4588ArtifactRingSize];
	slot = *source;
	slot.id = id;
	if (frame_out) *frame_out = slot.frame;
	return id;
}

// FL-4588 task 56: read the immutable final-frame + trace metadata bound to a
// snapshot id, so a sealed transaction receipt can cite genuine facts. Returns 1
// on success (filling frame, authoring_frame_id, the 64-hex final_frame_sha256 and
// cell_trace_sha256); 0 on unknown/expired/evicted id (fail closed). The
// final-frame hash is the actual presented RGBA frame — never the rendered-buffer
// hash. Pointers valid until the slot is overwritten by a future capture.
extern "C" int TermGetArtifactSnapshotMetaC(
	unsigned long long snapshot_id, unsigned long long* frame_out,
	unsigned long long* authoring_frame_id_out,
	char final_frame_sha256_out[65], char cell_trace_sha256_out[65])
{
	if (snapshot_id == 0) return 0;
	const Fl4588ArtifactSnapshot& slot =
		g_fl4588_artifact_ring[snapshot_id % kFl4588ArtifactRingSize];
	if (slot.id != snapshot_id) return 0;  // evicted or never existed
	if (frame_out) *frame_out = slot.frame;
	if (authoring_frame_id_out) *authoring_frame_id_out = slot.authoring_frame_id;
	if (final_frame_sha256_out)
	{
		// A capture with no published authoring frame leaves this empty; copy what
		// exists (the receipt fails closed on an all-zero / short hash).
		std::snprintf(final_frame_sha256_out, 65, "%s",
			slot.final_frame_sha256.c_str());
	}
	if (cell_trace_sha256_out)
		std::snprintf(cell_trace_sha256_out, 65, "%s",
			slot.cell_trace_sha256.c_str());
	return 1;
}

// which: 0 = rendered_buffer, 1 = bridge_facts, 2 = cell_trace. Returns 1 on
// success; 0 on unknown/expired/evicted id OR an artifact that was not frozen on
// this snapshot's frame (fail closed). Pointer valid until the slot is
// overwritten by a future capture (bounded lifecycle).
extern "C" int TermGetArtifactSnapshotBytesC(
	unsigned long long snapshot_id, int which,
	const char** data, size_t* len, unsigned long long* frame_out)
{
	if (snapshot_id == 0) return 0;
	const Fl4588ArtifactSnapshot& slot =
		g_fl4588_artifact_ring[snapshot_id % kFl4588ArtifactRingSize];
	if (slot.id != snapshot_id) return 0;  // evicted or never existed
	if (which == 2)
	{
		// cell_trace: presence is decided by the frozen flag, NOT by size — an
		// empty (no-change) trace frozen on this frame is a valid read; a trace
		// that was never frozen on this frame fails closed.
		if (!slot.cell_trace_valid) return 0;
		if (data) *data = slot.cell_trace.c_str();
		if (len) *len = slot.cell_trace.size();
		if (frame_out) *frame_out = slot.frame;
		return 1;
	}
	const std::string& bytes =
		which == 1 ? slot.bridge_facts : slot.rendered_buffer;
	if (bytes.empty()) return 0;
	if (data) *data = bytes.c_str();
	if (len) *len = bytes.size();
	if (frame_out) *frame_out = slot.frame;
	return 1;
}

int TermDumpRenderedBuffer(const char* path)
{
	// path may be NULL: build the immutable in-memory record only. A non-NULL
	// path additionally writes the file diagnostic from the SAME bytes.
	TERM_LIST* term = g_term_last_rendered ? g_term_last_rendered : term_head;
	if (!term)
		return -2;
	const int width = term->last_render_w > 0 ? term->last_render_w : TERM_LIST::max_width;
	const int height = term->last_render_h > 0 ? term->last_render_h : TERM_LIST::max_height;
	if (width <= 0 || height <= 0 ||
		width > TERM_LIST::max_width || height > TERM_LIST::max_height)
		return -3;
	char* mem = NULL;
	size_t mem_size = 0;
	FILE* f = open_memstream(&mem, &mem_size);
	if (!f)
		return -4;
	const unsigned long long rendered_frame =
		(unsigned long long)TermPresentationFrameIdV1();
	fprintf(f,
		"{\"schema\":\"fl4207.termpp.rendered_buffer.v1\",\"kind\":\"header\","
		"\"w\":%d,\"h\":%d,\"stride\":%d,\"frame\":%llu}\n",
		width, height, width,
		rendered_frame);
	for (int y = 0; y < height; y++)
	{
		for (int x = 0; x < width; x++)
		{
			const int idx = y * width + x;
			const AnsiCell& c = term->buf[idx];
			const uint32_t final_gid = (uint32_t)term->final_glyph_buf[idx];
			const bool extended = final_gid > 255u && !glyph_id_is_sentinel((GlyphId)final_gid);
			const uint32_t sidecar = extended ? final_gid : (uint32_t)GLYPH_ID_NONE;
			fprintf(f,
				"{\"kind\":\"cell\",\"x\":%d,\"y\":%d,"
				"\"fg\":%u,\"bk\":%u,\"cp437\":%u,\"spare\":%u,"
				"\"sidecar_gid\":%u,\"extended\":%d,\"final_gid\":%u}\n",
				x, y,
				(unsigned)c.fg, (unsigned)c.bk, (unsigned)c.gl, (unsigned)c.spare,
				(unsigned)sidecar, extended ? 1 : 0, (unsigned)final_gid);
		}
	}
	int ok = ferror(f) ? -5 : 0;
	fclose(f);  // flush JSONL into mem
	if (ok == 0 && mem)
	{
		// Transient in-memory build buffer; the snapshot copies these bytes.
		Fl4588SetRenderedBufferRecord(mem, mem_size, rendered_frame);
		// File diagnostic derives from the SAME bytes when a path is given.
		// Open/write/close failures are reported, never silently succeeded.
		if (path && path[0])
		{
			FILE* out = fopen(path, "wb");
			if (!out)
				ok = -6;  // file open failure
			else
			{
				const size_t wrote = fwrite(mem, 1, mem_size, out);
				const int close_rc = fclose(out);
				if (wrote != mem_size || close_rc != 0)
					ok = -7;  // file write/close failure
			}
		}
	}
	free(mem);
	return ok;
}

// Fl4588 deposit shims: the product serializer reports its bytes through
// these callbacks so terminal file-statics never cross to product.
static void TermDepositBridgeFacts(const char* data, size_t len,
	unsigned long long frame, void* user)
{
	(void)user;
	Fl4588SetBridgeFactsRecord(data, len, frame);
}
static void TermDepositCellTrace(const char* data, size_t len,
	unsigned long long frame, void* user)
{
	(void)user;
	Fl4588SetCellTraceRecord(data, len, frame);
}
int TermDumpBridgeCells(const char* path)
{
	// Product serializer lives product-side. This wrapper supplies render dims
	// + the Fl4588 deposit callback; fail closed when uninstalled.
	{
		int term_w = 0, term_h = 0;
		(void)TermGetRenderDims(&term_w, &term_h);
		{
			const InspectionPortV1* dump_nrt = AsciickerInspection();
			if (dump_nrt && dump_nrt->dump_bridge_cells)
				return dump_nrt->dump_bridge_cells(path, term_w, term_h,
					TermDepositBridgeFacts, NULL);
		}
		return -2;
	}
}


#ifdef EDITOR
static void Fl4588FreezeAuthoringFrameArtifactV1()
{
	// This runs on the render thread immediately after trace v6 publication for
	// the draw whose RGBA bytes were requested. Build every full-plane observer
	// from that still-current draw, then publish the coherent record in one move.
	Fl4588FrameArtifactRecord& scratch = g_fl4588_frame_artifacts;
	scratch.rb_valid = scratch.bf_valid = false;
	(void)TermDumpRenderedBuffer(NULL);
	(void)TermDumpBridgeCells(NULL);
	if (!scratch.rb_valid || !scratch.bf_valid || !scratch.ct_valid ||
		scratch.rb_frame == 0 || scratch.rb_frame != scratch.bf_frame ||
		scratch.rb_frame != scratch.ct_frame ||
		scratch.rb_frame != g_term_authoring_frame_source_frame ||
		g_term_authoring_frame_id == 0 ||
		strlen(g_term_authoring_frame_sha256) != 64 ||
		g_term_authoring_frame_rgba.empty() ||
		scratch.rendered_buffer.empty() || scratch.bridge_facts.empty())
	{
		return;
	}

	Fl4588ArtifactSnapshot coherent;
	coherent.frame = scratch.rb_frame;
	coherent.rendered_buffer = scratch.rendered_buffer;
	coherent.bridge_facts = scratch.bridge_facts;
	coherent.cell_trace = scratch.cell_trace;
	coherent.cell_trace_valid = true;
	coherent.final_frame_sha256.assign(g_term_authoring_frame_sha256, 64);
	coherent.authoring_frame_id = g_term_authoring_frame_id;
	char ct_hex[65];
	TermSha256Hex(coherent.cell_trace.data(), coherent.cell_trace.size(), ct_hex);
	coherent.cell_trace_sha256.assign(ct_hex, 64);
	Fl4588ArtifactSnapshot& retained =
		g_fl4588_authoring_frame_artifacts[
			coherent.authoring_frame_id % kFl4588ArtifactRingSize];
	retained = std::move(coherent);
}
#endif

extern "C" void TermSetRenderTargetOverride(GLuint fbo, int w, int h)
{
	g_term_render_target_fbo = fbo;
	g_term_render_target_w = w;
	g_term_render_target_h = h;
}

extern "C" GLuint TermAllocEmbedRenderTarget(int w, int h, GLuint* out_color_tex)
{
	if (w <= 0 || h <= 0 || w > 8192 || h > 8192)
		return 0;
	GLuint fbo = 0;
	GLuint tex = 0;
	glGenFramebuffers(1, &fbo);
	glGenTextures(1, &tex);
	if (!fbo || !tex) {
		if (fbo) glDeleteFramebuffers(1, &fbo);
		if (tex) glDeleteTextures(1, &tex);
		return 0;
	}
	glBindTexture(GL_TEXTURE_2D, tex);
	glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	glBindTexture(GL_TEXTURE_2D, 0);
	glBindFramebuffer(GL_FRAMEBUFFER, fbo);
	glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
	GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
	glBindFramebuffer(GL_FRAMEBUFFER, 0);
	if (status != GL_FRAMEBUFFER_COMPLETE) {
		glDeleteFramebuffers(1, &fbo);
		glDeleteTextures(1, &tex);
		return 0;
	}
	if (out_color_tex) *out_color_tex = tex;
	return fbo;
}

extern "C" void TermFreeEmbedRenderTarget(GLuint fbo, GLuint color_tex)
{
	if (fbo) glDeleteFramebuffers(1, &fbo);
	if (color_tex) glDeleteTextures(1, &color_tex);
}

// Forward declarations for the embed constructor.
void term_init(A3D_WND *wnd);
void term_close(A3D_WND *wnd);
void term_render(A3D_WND *wnd);

// FL-4131 item 4 step 4b: real no-window embed constructor.
//
// Allocates a TERM_LIST in the caller's GL context (which must be current)
// and runs term_init() with wnd=NULL so the GL setup path executes but the
// window/cookie/linked-list path is skipped. The caller owns the returned
// pointer and must call TermCloseEmbedded() to tear it down.
//
// The standalone TERM++ window keeps using the same TERM_LIST renderer
// (via term_init(wnd)/term_render(wnd)); this function adds a second
// instance that lives entirely inside the editor's GL context. No SDL
// window, no a3dSetVisible(false) "hidden window" trick -- if the GL
// resources fail to allocate the function returns NULL and the caller
// must show "TERM++ embed not mounted" per the directive.
extern "C" struct TERM_LIST* TermOpenEmbedded(int max_w_unused, int max_h_unused)
{
	(void)max_w_unused;
	(void)max_h_unused;
	// term_init() honors wnd=NULL by skipping the cookie/linked-list/visible/
	// focus calls and leaving term->wnd at NULL, while still doing the full
	// Game + shader + texture + VAO setup. Caller must have the editor GL
	// context current. Allocation happens inside term_init.
	TERM_LIST* embed_term = NULL;
	{
		// term_init writes the new TERM_LIST to a local; we capture it by
		// holding term_tail before/after. With wnd=NULL the linked list is
		// NOT modified, so we need a different capture: temporarily set a
		// static pointer that term_init can write to.
		extern TERM_LIST* g_term_init_capture;
		g_term_init_capture = NULL;
		term_init(NULL);
		embed_term = g_term_init_capture;
		g_term_init_capture = NULL;
	}
	return embed_term;
}

// Render-into-FBO entry point used by the embed path. ASCIIID calls this
// each editor frame to push one TERM++ frame into the embed's color
// texture (returned via TermAllocEmbedRenderTarget).
extern "C" void TermRenderEmbedded(struct TERM_LIST* term, int target_w, int target_h, GLuint target_fbo)
{
	if (!term || target_w <= 0 || target_h <= 0 || target_fbo == 0)
		return;
	// term_render expects a wnd to fetch the cookie. We temporarily set
	// the embed-render handoff so term_render's local pulls it directly.
	extern struct TERM_LIST* g_term_render_embed_handoff;
	g_term_render_embed_handoff = term;
	TermSetRenderTargetOverride(target_fbo, target_w, target_h);
	// term_render reads g_term_render_embed_handoff when wnd is NULL.
	term_render(NULL);
	TermSetRenderTargetOverride(0, 0, 0);
	g_term_render_embed_handoff = NULL;
}

// FL-4131 audit goal item 1+3 (2026-06-07): the embedded TERM_LIST is OFF
// the term_head linked list, so TermSetCameraView (which walks the list) does
// not reach it. This narrow entry point lets the editor push its camera into
// the embed each frame so the FBO renders the editor view, not the embed's
// default spawn. Same field writes TermSetCameraView performs per node.
extern "C" void TermEmbedApplyCamera(struct TERM_LIST* term, float x, float y, float z, float yaw)
{
	if (!term || !term->game) return;
	Game* g = term->game;
	g->player.pos[0] = x;
	g->player.pos[1] = y;
	g->player.pos[2] = z;
	g->player.dir = yaw;
	g->player.prev_yaw = yaw;
	PhysicsTeleportCommand cmd = {};
	cmd.set_pos = true;
	cmd.pos[0] = x;
	cmd.pos[1] = y;
	cmd.pos[2] = z;
	cmd.set_yaw = true;
	cmd.yaw = yaw;
	cmd.set_dir = true;
	cmd.dir = yaw;
	PhysicsTeleport(g->physics, cmd);
	TermApplyPlayerSpriteVisibilityToGame(g);
}

extern "C" void TermEmbedApplyLight(struct TERM_LIST* term, float x, float y, float z, float ambience)
{
	if (!term || !term->game) return;
	Game* g = term->game;
	g->session.light[0] = x;
	g->session.light[1] = y;
	g->session.light[2] = z;
	g->session.light[3] = ambience;
}

extern "C" void TermCloseEmbedded(struct TERM_LIST* term)
{
	if (!term) return;
	// term_close expects a window cookie; the embed has no window. We
	// inline the equivalent cleanup: free game, then free the TERM_LIST.
	// term_close also walks the linked list; the embed is not on it (we
	// skipped insertion in term_init for wnd=NULL), so no list fixup
	// needed here either. GL textures/programs are leaked across embed
	// reopen for now -- the editor only opens/closes the embed once per
	// session, so this is bounded.
	// TODO: full teardown of term GL resources before free.
	if (term->game) {
		FreeGame(term->game);
		DeleteGame(term->game);
		term->game = NULL;
	}
	free(term);
}

// Capture targets set by TermOpenEmbedded / TermRenderEmbedded so the
// existing term_init / term_render code paths can hand off the TERM_LIST
// pointer without depending on a window cookie. Both default to NULL.
TERM_LIST* g_term_init_capture = NULL;
TERM_LIST* g_term_render_embed_handoff = NULL;

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

// =============================================================================
// FL-4665 — FRAME-SPAN INSTRUMENTATION
// Current scope: non-blocking frame and presentation timing.
// The retired glyph-selection GPU passes have no timing slots. The remaining
// query ring observes only the final user-visible presentation draw.
// =============================================================================

#define FL4665_QUERY_RING_DEPTH 4

// ---- CPU spans -------------------------------------------------------------
// These are wall-clock stretches of the RENDER THREAD, measured with
// a3dGetTime() (CLOCK_MONOTONIC, in microseconds). "Wall clock" matters here:
// the freeze is defined by the thread not returning, so thread time is exactly
// the quantity under investigation.
enum Fl4665CpuSpan
{
	FL4665_CPU_TERM_RENDER_TOTAL = 0, // whole term_render() call, end to end
	FL4665_CPU_FINAL_TUPLE_COMMIT,    // deep-owner commit plus native copy
	FL4665_CPU_PRESENT_UPLOAD,        // final AnsiCell + glyph-id texture upload
	FL4665_CPU_PRESENT_DRAW,          // TERM++ fullscreen-quad draw (CPU side)
	FL4665_CPU_SPAN_COUNT
};

// Human-readable names, emitted as JSON keys. Index order MUST match the enum
// above; they are read positionally.
// Commit-span receipt vocabulary follows the installed presentation
// backend: the Classic font pipeline keeps its historical name, the
// compiled-atlas pipeline keeps its own. Runtime query, never a guard.
static const char* Fl4665CommitSpanName(void)
{
	const PresentationBackendV1* span_nrt = AsciickerPresentation();
	if (span_nrt && span_nrt->presents_compiled_atlas &&
		span_nrt->presents_compiled_atlas())
		return "cell_commit";
	return "classic_cell_commit";
}

static const char* const kFl4665CpuSpanName[FL4665_CPU_SPAN_COUNT] =
{
	"term_render_total",
	"cell_commit",
	"present_upload",
	"present_draw",
};

// ---- GPU spans -------------------------------------------------------------
// This measures the final presentation draw without introducing a blocking
// synchronization point.
enum Fl4665GpuSpan
{
	FL4665_GPU_PRESENT_DRAW = 0, // TERM++ fullscreen-quad present draw
	FL4665_GPU_SPAN_COUNT
};

static const char* const kFl4665GpuSpanName[FL4665_GPU_SPAN_COUNT] =
{
	"present_draw",
};

// One CPU span's bookkeeping for the CURRENT frame.
struct Fl4665CpuSpanState
{
	uint64_t begin_us; // a3dGetTime() when the span opened; 0 = not open
	uint64_t total_us; // microseconds accumulated so far THIS frame
	int      hits;     // how many times the span opened this frame (0 = never
	                   // ran, which is itself a finding worth publishing)
};

// One rotating stopwatch: a GL timer-query object plus the bookkeeping needed
// to know whether it is currently in use and which frame issued it.
struct Fl4665GpuQuerySlot
{
	GLuint   id;          // GL query object name; 0 = not created yet
	uint64_t issue_frame; // module frame counter at the moment it was issued
	bool     in_flight;   // ended on the GPU, result not yet collected
};

// One GPU span's rotating ring plus the most recent resolved measurement.
struct Fl4665GpuSpanState
{
	Fl4665GpuQuerySlot ring[FL4665_QUERY_RING_DEPTH];
	uint64_t last_ns;    // most recent RESOLVED duration, in nanoseconds
	uint64_t last_frame; // the frame that produced last_ns
	uint32_t last_age;   // frames between issuing that sample and reading it
	uint32_t dropped;    // samples skipped because every ring slot was busy
	uint32_t resolved;   // how many samples have ever been read back
};

static bool     g_fl4665_timing_init    = false;   // has the env gate been read
static bool     g_fl4665_timing_enabled = false;   // is instrumentation on
static FILE*    g_fl4665_timing_out     = nullptr; // JSONL sink (file / stderr)
static uint64_t g_fl4665_timing_frame   = 0;       // monotonic frame counter
// GL allows only ONE GL_TIME_ELAPSED query to be active at a time — they cannot
// nest or interleave. This holds the currently open span (-1 = none) so a
// mis-paired begin is refused instead of corrupting GL state.
static int      g_fl4665_active_gpu_span = -1;
static Fl4665CpuSpanState g_fl4665_cpu[FL4665_CPU_SPAN_COUNT];
static Fl4665GpuSpanState g_fl4665_gpu[FL4665_GPU_SPAN_COUNT];
// ---- Host-loop swap accounting ---------------------------------------------
//
// THE JOIN PROBLEM THIS SOLVES. SDL_GL_SwapWindow runs in host_sdl2.cpp AFTER
// term_render has returned. An earlier version of this module simply carried
// the duration forward and printed it on the NEXT frame's line. That was wrong
// twice over:
//
//   1. LOST EVIDENCE. FL-4665 runs end in a force quit. If the freeze happens
//      inside the swap, there IS no next frame — so the single most important
//      measurement in the whole investigation was the one guaranteed to be
//      discarded.
//   2. WRONG OWNER. With more than one window mapped, the host loop renders
//      window A, swaps window A, renders window B, swaps window B. Carrying a
//      duration forward blindly can attach window A's swap to window B's
//      terminal, inventing a fact about a surface that did not produce it.
//
// The fix is to treat a completed swap as its OWN receipt: when the swap
// returns, publish the duration together with the render frame and window that
// actually preceded it, and flush that line at once. Nothing is carried across
// a frame boundary, so nothing can be lost or mis-joined.
//
// To name the right owner we need to know which terminal last rendered to a
// given window. This small table records that as each frame reports, keyed by
// window pointer. Eight entries is far more than any real session maps, and a
// swap for an unknown window is published with matched=false rather than being
// guessed at — an honest "unattributed" beats a confident wrong owner.
#define FL4665_WINDOW_TABLE_SIZE 8
struct Fl4665RenderedWindowRecord
{
	const void* wnd;     // host window pointer; nullptr = empty slot
	const void* term;    // the TERM_LIST that last rendered to that window
	uint64_t    frame;   // module frame counter of that render
	int         cells_w; // viewport in character cells, as rendered
	int         cells_h;
};
static Fl4665RenderedWindowRecord
	g_fl4665_windows[FL4665_WINDOW_TABLE_SIZE] = {};
// Open swap: which window is being presented, and when the present began.
static const void* g_fl4665_swap_wnd      = nullptr;
static uint64_t    g_fl4665_swap_begin_us = 0;

// Read the environment gate exactly once and open the output sink.
//
// Returns true when FL-4665 frame timing is switched on for this process. When
// it returns false every other entry point in this module returns immediately,
// so the cost of leaving the instrumentation compiled in is one predictable
// branch per span.
static bool Fl4665FrameTimingEnabled()
{
	if (!g_fl4665_timing_init)
	{
		g_fl4665_timing_init = true;
		// Either variable switches timing on; PATH additionally names a file.
		const char* path = getenv("FL4665_FRAME_TIMING_PATH");
		const char* flag = getenv("FL4665_FRAME_TIMING");
		g_fl4665_timing_enabled = (path && path[0]) || (flag && flag[0]);
		if (g_fl4665_timing_enabled)
		{
			if (path && path[0])
				g_fl4665_timing_out = fopen(path, "a"); // append: keep prior runs
			if (!g_fl4665_timing_out)
				g_fl4665_timing_out = stderr; // no path, or the open failed
		}
	}
	return g_fl4665_timing_enabled;
}

// Open a CPU span. Safe to call when timing is off (does nothing).
//
// `span` is one of Fl4665CpuSpan. A second begin without an intervening end is
// ignored rather than silently restarting the clock, so an unbalanced pair
// under-reports instead of reporting a wrong small number.
static void Fl4665CpuBegin(int span)
{
	if (!Fl4665FrameTimingEnabled()) return;
	if (span < 0 || span >= FL4665_CPU_SPAN_COUNT) return;
	if (g_fl4665_cpu[span].begin_us != 0) return; // already open
	g_fl4665_cpu[span].begin_us = a3dGetTime();
}

// Close a CPU span and add its microseconds to this frame's total for that span.
// Spans may open and close more than once per frame (the totals accumulate and
// `hits` counts the openings), which is how a per-row or per-pass helper called
// in a loop is attributed correctly.
static void Fl4665CpuEnd(int span)
{
	if (!Fl4665FrameTimingEnabled()) return;
	if (span < 0 || span >= FL4665_CPU_SPAN_COUNT) return;
	if (g_fl4665_cpu[span].begin_us == 0) return; // not open: nothing to close
	g_fl4665_cpu[span].total_us += a3dGetTime() - g_fl4665_cpu[span].begin_us;
	g_fl4665_cpu[span].begin_us = 0;
	g_fl4665_cpu[span].hits++;
}

// Start the GPU stopwatch for `span`, using the next free slot in its ring.
//
// Picks slot (frame % RING_DEPTH). If that slot's previous measurement has not
// come back yet, the sample is DROPPED (counted, not waited on) — never block
// the render thread to take a measurement of the render thread.
static void Fl4665GpuBegin(int span)
{
	if (!Fl4665FrameTimingEnabled()) return;
	if (span < 0 || span >= FL4665_GPU_SPAN_COUNT) return;
	// GL forbids a second active GL_TIME_ELAPSED query. Refuse rather than
	// issue an illegal command that would set a GL error for other code.
	if (g_fl4665_active_gpu_span >= 0) { g_fl4665_gpu[span].dropped++; return; }

	Fl4665GpuSpanState& st = g_fl4665_gpu[span];
	const int slot = (int)(g_fl4665_timing_frame % FL4665_QUERY_RING_DEPTH);
	if (st.ring[slot].id == 0)
		glGenQueries(1, &st.ring[slot].id); // create on first use, in-context
	if (st.ring[slot].id == 0) { st.dropped++; return; } // driver refused
	if (st.ring[slot].in_flight) { st.dropped++; return; } // stopwatch busy

	glBeginQuery(GL_TIME_ELAPSED, st.ring[slot].id);
	g_fl4665_active_gpu_span = span;
}

// Stop the GPU stopwatch opened by Fl4665GpuBegin and mark the slot in flight
// so a later frame can collect the result once the GPU reports it ready.
static void Fl4665GpuEnd(int span)
{
	if (!Fl4665FrameTimingEnabled()) return;
	if (span < 0 || span >= FL4665_GPU_SPAN_COUNT) return;
	if (g_fl4665_active_gpu_span != span) return; // begin was refused/dropped

	glEndQuery(GL_TIME_ELAPSED);
	g_fl4665_active_gpu_span = -1;
	Fl4665GpuSpanState& st = g_fl4665_gpu[span];
	const int slot = (int)(g_fl4665_timing_frame % FL4665_QUERY_RING_DEPTH);
	st.ring[slot].issue_frame = g_fl4665_timing_frame;
	st.ring[slot].in_flight   = true;
}

// Collect any GPU measurements that have finished, WITHOUT waiting for any.
//
// For every in-flight slot this asks the non-blocking question
// GL_QUERY_RESULT_AVAILABLE first. Only when the driver answers "yes" does it
// read the actual number, so this function can never stall the render thread.
static void Fl4665CollectGpuResults()
{
	if (!Fl4665FrameTimingEnabled()) return;
	for (int span = 0; span < FL4665_GPU_SPAN_COUNT; span++)
	{
		Fl4665GpuSpanState& st = g_fl4665_gpu[span];
		for (int slot = 0; slot < FL4665_QUERY_RING_DEPTH; slot++)
		{
			if (!st.ring[slot].in_flight || st.ring[slot].id == 0) continue;
			GLuint available = 0;
			glGetQueryObjectuiv(st.ring[slot].id, GL_QUERY_RESULT_AVAILABLE,
			                    &available);
			if (!available) continue; // still running: leave it, try next frame
			GLuint64 elapsed_ns = 0;
			// Safe now: the driver just said the result is ready, so this read
			// returns immediately instead of blocking.
			glGetQueryObjectui64v(st.ring[slot].id, GL_QUERY_RESULT, &elapsed_ns);
			st.last_ns    = (uint64_t)elapsed_ns;
			st.last_frame = st.ring[slot].issue_frame;
			// How stale this number is, in frames. Published so nobody mistakes
			// an old GPU sample for a measurement of the current frame.
			st.last_age   = (uint32_t)(g_fl4665_timing_frame -
			                           st.ring[slot].issue_frame);
			st.resolved++;
			st.ring[slot].in_flight = false; // stopwatch free to reuse
		}
	}
}

// Reset the per-frame CPU accumulators and advance the frame counter. Called
// once at the very top of term_render so every span below starts from zero.
static void Fl4665FrameBegin()
{
	if (!Fl4665FrameTimingEnabled()) return;
	g_fl4665_timing_frame++;
	for (int i = 0; i < FL4665_CPU_SPAN_COUNT; i++)
	{
		g_fl4665_cpu[i].begin_us = 0;
		g_fl4665_cpu[i].total_us = 0;
		g_fl4665_cpu[i].hits     = 0;
	}
}

// Emit one JSON line describing this frame, then clear the swap accumulator.
//
// Parameters carry the identity fields the FL-4665 contract requires, so a line
// is interpretable on its own without correlating against other logs:
//   wnd            — host window pointer, i.e. WHICH window this frame drew to
//   term           — TERM_LIST pointer, i.e. which terminal instance owned it
//   cells_w/h      — viewport in character cells
//   px_w/px_h      — viewport in device pixels (Retina makes these differ)
//   bridge_gen     — the render bridge's generation counter for this frame,
//                    which ties the line to the frame's other receipts
static void Fl4665FrameReport(const void* wnd, const void* term,
                              int cells_w, int cells_h,
                              int px_w, int px_h,
                              uint64_t bridge_gen)
{
	if (!Fl4665FrameTimingEnabled()) return;
	FILE* out = g_fl4665_timing_out;
	if (!out) return;

	// Remember which terminal rendered to this window, so the swap that follows
	// can name its true owner instead of inheriting whichever frame ran last.
	// Reuse an existing slot for the same window; otherwise claim a free one.
	{
		int slot = -1;
		for (int i = 0; i < FL4665_WINDOW_TABLE_SIZE; i++)
		{
			if (g_fl4665_windows[i].wnd == wnd) { slot = i; break; }
			if (slot < 0 && g_fl4665_windows[i].wnd == nullptr) slot = i;
		}
		// If every slot is taken by other windows, overwrite slot 0 rather than
		// silently skipping: a slightly stale entry is recoverable, a swap with
		// no owner at all is not.
		if (slot < 0) slot = 0;
		g_fl4665_windows[slot].wnd     = wnd;
		g_fl4665_windows[slot].term    = term;
		g_fl4665_windows[slot].frame   = g_fl4665_timing_frame;
		g_fl4665_windows[slot].cells_w = cells_w;
		g_fl4665_windows[slot].cells_h = cells_h;
	}

	// Frame identity and geometry first, so a truncated line is still useful.
	// "record" discriminates this from the swap receipt emitted by the host
	// hook, since the two line shapes share one output stream.
	fprintf(out,
		"{\"fl\":\"FL-4665\",\"record\":\"frame\","
		"\"frame\":%llu,\"bridge_gen\":%llu,"
		"\"wnd\":\"%p\",\"term\":\"%p\","
		"\"cells_w\":%d,\"cells_h\":%d,\"px_w\":%d,\"px_h\":%d",
		(unsigned long long)g_fl4665_timing_frame,
		(unsigned long long)bridge_gen,
		wnd, term, cells_w, cells_h, px_w, px_h);

	// CPU spans, in milliseconds. `_hits` distinguishes "ran and was fast" (a
	// small time with hits>0) from "never ran at all" (hits==0) — two very
	// different findings that a bare 0.000 would blur together.
	fprintf(out, ",\"cpu_ms\":{");
	for (int i = 0; i < FL4665_CPU_SPAN_COUNT; i++)
	{
		const char* span_name = (i == FL4665_CPU_FINAL_TUPLE_COMMIT)
			? Fl4665CommitSpanName()
			: kFl4665CpuSpanName[i];
		fprintf(out, "%s\"%s\":%.3f,\"%s_hits\":%d",
			i ? "," : "",
			span_name, (double)g_fl4665_cpu[i].total_us / 1000.0,
			span_name, g_fl4665_cpu[i].hits);
	}
	fprintf(out, "}");

	// NOTE: the host-loop present is deliberately NOT reported on this line.
	// It completes after term_render returns, so any value printed here would
	// belong to a different frame — and, with several windows mapped, possibly
	// to a different terminal. It gets its own self-contained receipt instead
	// (see Fl4665HostSwapEnd), emitted the moment the swap returns.

	// GPU spans. `ms` is the GPU's own elapsed time for that pass; `age` is how
	// many frames ago the sample was issued; `resolved`/`dropped` expose whether
	// the measurement channel is actually working on this driver at all.
	fprintf(out, ",\"gpu\":{");
	for (int i = 0; i < FL4665_GPU_SPAN_COUNT; i++)
	{
		const Fl4665GpuSpanState& st = g_fl4665_gpu[i];
		fprintf(out,
			"%s\"%s\":{\"ms\":%.3f,\"age\":%u,\"src_frame\":%llu,"
			"\"resolved\":%u,\"dropped\":%u}",
			i ? "," : "", kFl4665GpuSpanName[i],
			(double)st.last_ns / 1000000.0, st.last_age,
			(unsigned long long)st.last_frame, st.resolved, st.dropped);
	}
	fprintf(out, "}}\n");
	fflush(out); // a freeze may end in a force-quit; unflushed lines are lost
}

// ---- Host-loop present hooks ------------------------------------------------
// These two are called from platform/backend/host_sdl2.cpp around
// SDL_GL_SwapWindow. They are extern "C" so that file can declare them with a
// one-line prototype and no shared header. This is link-safe because every
// build target that compiles host_sdl2.cpp also compiles this file (all four
// SDL targets: makefile_game, makefile_game_mac, and both
// apps/asciicker-presenter/makefile_asciiid variants); no target contains
// one without the other.
// Mark the start of a window present.
//
// `window` is the host window actually being swapped. It is required, not
// optional: it is the key that lets the receipt below name the terminal that
// produced the pixels, instead of assuming the most recent frame owns them.
extern "C" void Fl4665HostSwapBegin(const void* window)
{
	if (!Fl4665FrameTimingEnabled()) return;
	g_fl4665_swap_wnd      = window;
	g_fl4665_swap_begin_us = a3dGetTime();
}

// Mark the end of a window present and publish its receipt IMMEDIATELY.
//
// Emitting here — rather than deferring to the next frame's line — is the whole
// point. If the process is force-quit while frozen in the present, this line is
// already on disk. And because the duration is joined to the window that was
// swapped and to the frame that rendered it, the receipt cannot drift onto a
// neighbouring frame or a different terminal.
extern "C" void Fl4665HostSwapEnd()
{
	if (!Fl4665FrameTimingEnabled()) return;
	if (g_fl4665_swap_begin_us == 0) return; // unbalanced: ignore, do not guess
	const uint64_t swap_us = a3dGetTime() - g_fl4665_swap_begin_us;
	const void* swapped = g_fl4665_swap_wnd;
	g_fl4665_swap_begin_us = 0;
	g_fl4665_swap_wnd      = nullptr;

	FILE* out = g_fl4665_timing_out;
	if (!out) return;

	// Find the terminal that last rendered to THIS window. A miss is reported
	// as matched=false with null identity rather than filled in from the most
	// recent frame, because a plausible wrong owner is worse than no owner.
	const Fl4665RenderedWindowRecord* rec = nullptr;
	for (int i = 0; i < FL4665_WINDOW_TABLE_SIZE; i++)
	{
		if (g_fl4665_windows[i].wnd && g_fl4665_windows[i].wnd == swapped)
		{
			rec = &g_fl4665_windows[i];
			break;
		}
	}

	// This is SUSPECT B: the boundary host_sdl2.cpp already measured at
	// 2,364 ms on the macOS Metal nextDrawable.
	fprintf(out,
		"{\"fl\":\"FL-4665\",\"record\":\"swap\",\"swap_ms\":%.3f,"
		"\"wnd\":\"%p\",\"matched\":%s,\"term\":\"%p\","
		"\"render_frame\":%llu,\"cells_w\":%d,\"cells_h\":%d}\n",
		(double)swap_us / 1000.0,
		swapped,
		rec ? "true" : "false",
		rec ? rec->term : nullptr,
		(unsigned long long)(rec ? rec->frame : 0),
		rec ? rec->cells_w : 0,
		rec ? rec->cells_h : 0);
	fflush(out); // the freeze may end here; this line must survive a force-quit
}

void term_render(A3D_WND *wnd)
{
	// FL-4131 item 4 step 4b: when called with wnd=NULL, the embed handoff
	// supplies the TERM_LIST. Otherwise (the standalone window path) we
	// pull it from the window cookie as before.
	TERM_LIST *term = wnd ? (TERM_LIST *)a3dGetCookie(wnd) : g_term_render_embed_handoff;
	if (!term)
		return;
	g_term_last_rendered = term;

	// FL-4665: open this frame's timing record and start the whole-frame span.
	// Placed AFTER the early-out above so a call that renders nothing is not
	// recorded as a zero-cost frame and does not dilute the receipt.
	Fl4665FrameBegin();
	Fl4665CpuBegin(FL4665_CPU_TERM_RENDER_TOTAL);
	// Harvest GPU stopwatch results that became ready since the previous frame.
	// This only reads queries the driver has already reported as available, so
	// it cannot block; it runs first so this frame's report can publish them.
	Fl4665CollectGpuResults();

	// dispatch all queued messages to game
	// 1. flush list
	// 2. reverse order
	// 3. dispatch every message with term->game->OnMessage()

	int wnd_wh[2];

	// FL-4131 item 4 step 4b: when an embed override is active, treat the
	// override (target_w, target_h) as the present-stage viewport instead of
	// the window's actual rect. The window may still be hidden / offscreen;
	// term_render must compute cell width/height from the embed dims, not
	// the host window. Standalone TERM++ keeps reading from a3dGetRect.
	if (g_term_render_target_fbo != 0 && g_term_render_target_w > 0 && g_term_render_target_h > 0)
	{
		wnd_wh[0] = g_term_render_target_w;
		wnd_wh[1] = g_term_render_target_h;
	}
	else
	{
		a3dGetRect(wnd, 0, wnd_wh);
	}

	int fnt_wh[2];
	int fnt_tex = GetGLFont(fnt_wh, wnd_wh);

	int width = wnd_wh[0] / (fnt_wh[0] >> 4);
	int height = wnd_wh[1] / (fnt_wh[1] >> 4);

	if (width > term->max_width)
		width = term->max_width;
	if (height > term->max_height)
		height = term->max_height;

	// FL-4260: observe-render is an evidence/artifact mode, not an interactive
	// TERM++ viewport owner. Forcing a canonical 106x76 cell grid here centers a
	// smaller render inside the real window, leaving a black top band and pushing
	// the HUD/menu rows down. TERM++ dimensions must come from the actual
	// window/FBO target; artifact comparators can crop or normalize after capture.
	term->last_render_w = width;
	term->last_render_h = height;

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
	// Game::Render opens the product presentation epoch for both native and
	// Web callers before populating this frame's compiler-owned scene facts.

	memset(term->final_glyph_buf, 0xFF, sizeof(term->final_glyph_buf));
	TermApplyPlayerSpriteVisibilityToGame(term->game);
	term->game->Render(stamp, term->buf, width, height);
	// Bridge publish (profile_load_failed + valid) runs inside the product
	// bridge-extent impl before the extent read below.
	// FL-4652 U6FINALIZER delete-first: the direct pre-pipeline promotion of
	// AnsiCell.gl into final_glyph_buf has been REMOVED. AnsiCell.gl is no longer
	// an early final-decision owner; it survives only as typed SOURCE content for
	// the non-profile domain, consumed later by RuntimeFinalTupleOwner. Ordinary
	// non-sprite cells therefore stay GLYPH_ID_NONE until the owner commits.

	if (perf_enabled)
		t1 = a3dGetTime();

	// FL-4131 item 4 step 4b: bind the embed FBO before the present-stage
	// glClear/glViewport/glDraw if an override is active. Standalone TERM++
	// keeps targeting the default framebuffer.
	if (g_term_render_target_fbo != 0)
	{
		glBindFramebuffer(GL_FRAMEBUFFER, g_term_render_target_fbo);
	}

	glClearColor(0, 0, 0, 0);
	glClear(GL_COLOR_BUFFER_BIT);

	// FL-4131 item 4 step 4b: title sync touches the host window. For embed
	// presents the host window is the editor's own window (or a hidden
	// helper); we must not stomp its title on every frame.
	if (g_term_render_target_fbo == 0)
	{
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
	}

	int vp_wh[2] =
		{
			width * (fnt_wh[0] >> 4),
			height * (fnt_wh[1] >> 4)};

	int vp_xy[2] =
		{
			0,
			wnd_wh[1] - vp_wh[1]};

	// FL-4652 U6FINALIZER: the SOLE ordinary-cell writer of final_glyph_buf runs
	// here, once, on the unconditional present path — AFTER every U6 owner has
	// produced its tuple (temporal select + structural optimizer above) and just
	// BEFORE the STAGE-2 uploads, so it fires for every frame that uploads ordinary
	// cells, including frames with zero profile cells. It writes profile cells from
	// the U6 tuple (or the named absent-missing-fact glyph + absence colors), and
	// non-profile authored text/HUD cells from AnsiCell.gl source content only.
	// Recompute the live bridge extent at this unconditional site. Passing the
	// full viewport would let stale bridge slots from a prior profile frame claim
	// authored cells when the current frame has no live bridge.
	// Product extent + post-render publish live product-side: the impl marks
	// profile_load_failed/valid, then clamps to the live bridge. Fail closed
	// (0x0) when uninstalled. The Classic adapter echoes the viewport.
	int fl4652_upload_w = 0;
	int fl4652_upload_h = 0;
	{
		const PresentationBackendV1* extent_nrt = AsciickerPresentation();
		if (extent_nrt && extent_nrt->bridge_upload_extent)
			extent_nrt->bridge_upload_extent(width, height,
				&fl4652_upload_w, &fl4652_upload_h);
	}
	// The selected presenter is the sole writer of the observation plane.
	Fl4665CpuBegin(FL4665_CPU_FINAL_TUPLE_COMMIT);
	TermCommitPresentationCellsV1(term, width, height,
		fl4652_upload_w, fl4652_upload_h,
		TERM_LIST::max_width);
	Fl4665CpuEnd(FL4665_CPU_FINAL_TUPLE_COMMIT);

	// [FLOW:TERMINAL] STAGE 2: Upload final presentation buffers to GPU textures.
	// GL_RGBA format maps AnsiCell fields: R=fg, G=bk, B=gl, A=spare.
	// Game::Render fills term->buf compactly as width × height, matching clean
	// upstream TERM++. Bridge/diagnostic arrays may be max_width-strided, but the
	// presented AnsiCell and sidecar buffers are compact live-viewport rows.
	// FL-4665: the two final CPU->GPU texture uploads. Uploads can stall the
	// render thread when the driver has to wait for the texture's previous
	// contents to stop being read, so they are measured rather than assumed free.
	Fl4665CpuBegin(FL4665_CPU_PRESENT_UPLOAD);
	glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
	gl3TextureSubImage2D(term->tex, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, term->buf);

	// Compiled-atlas products upload the committed final identity plane.
	// Classic samples its historical CP437 font directly from AnsiCell.gl
	// and owns no Unicode atlas. Runtime query, never a guard.
	const PresentationBackendV1* upload_nrt = AsciickerPresentation();
	const bool upload_atlas = upload_nrt &&
		upload_nrt->presents_compiled_atlas &&
		upload_nrt->presents_compiled_atlas();
	if (upload_atlas)
	{
		glBindTexture(GL_TEXTURE_2D, term->final_glyph_tex);
		glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RED_INTEGER, GL_UNSIGNED_INT, term->final_glyph_buf);
		glBindTexture(GL_TEXTURE_2D, 0);
	}
	Fl4665CpuEnd(FL4665_CPU_PRESENT_UPLOAD);

	glViewport(vp_xy[0], vp_xy[1], vp_wh[0], vp_wh[1]);

	glUseProgram(term->prg);

	glUniform2i(/*0*/ term->uni_ansi_vp, width, height);

	glUniform1i(/*1*/ term->uni_ansi, 0);
	// glBindTextureUnit(0, term->tex);
	glActiveTexture(GL_TEXTURE0);
	glBindTexture(GL_TEXTURE_2D, term->tex);

	if (!upload_atlas)
	{
		glUniform1i(term->uni_font, 1);
		glActiveTexture(GL_TEXTURE1);
		glBindTexture(GL_TEXTURE_2D, fnt_tex);
	}
	else
	{
	// Compiled-atlas products bind final identity, address LUT, and atlas.
	glUniform1i(term->uni_final_glyph,    2);
	glActiveTexture(GL_TEXTURE2);
	glBindTexture(GL_TEXTURE_2D, term->final_glyph_tex);

	glUniform1i(term->uni_lut_tex,    3);
	glActiveTexture(GL_TEXTURE3);
	glBindTexture(GL_TEXTURE_2D, term->lut_tex_id);

	glUniform1i(term->uni_page_atlas, 4);
	glActiveTexture(GL_TEXTURE4);
	glBindTexture(GL_TEXTURE_2D_ARRAY, term->page_atlas_id);

	glUniform2i(term->uni_lut_grid, term->lut_columns, term->lut_rows);
	glUniform1ui(term->uni_lut_low_limit, FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT);
	glUniform1ui(term->uni_lut_universal_base, FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE);
	glUniform2f(term->uni_page_grid, term->page_atlas_cols, term->page_atlas_rows);
	}

	glUniform2i(/*3*/ term->uni_ansi_wh, term->max_width, term->max_height);

	glBindVertexArray(term->vao);

	// [FLOW:TERMINAL] STAGES 3-6: GPU renders fullscreen quad with palette mapping.
	// Vertex shader transforms UV → cell_coord, fragment shader samples AnsiCell + font
	// textures, applies Pal() palette mapping, blends FG/BG colors based on glyph alpha.
	// FL-4665: timed on BOTH clocks. The CPU span is how long issuing the draw
	// costs the render thread; the GPU stopwatch is how long the GPU spends
	// actually shading it. A large CPU number with a small GPU number means the
	// thread is queued behind something else, not that this draw is expensive.
	Fl4665CpuBegin(FL4665_CPU_PRESENT_DRAW);
	Fl4665GpuBegin(FL4665_GPU_PRESENT_DRAW);
	glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
	Fl4665GpuEnd(FL4665_GPU_PRESENT_DRAW);
	Fl4665CpuEnd(FL4665_CPU_PRESENT_DRAW);
	glUseProgram(0);
	glBindVertexArray(0);

	// FL-4131 P3 (2026-06-03): TERM++ pixel capture. The editor MCP queues a
	// path via TermRequestCapture; we glReadPixels right after the draw so the
	// PNG captures the actual TERM++ window, not the editor viewport. Per the
	// CARDINAL RULE: this is observation, not gameplay/material/sidecar
	// ownership mutation. Capture runs at most once per path; the global is
	// cleared after the write so back-to-back captures stay deterministic.
#ifdef EDITOR
	bool fl4588_authoring_pixels_captured = false;
	if (g_term_capture_path[0])
	{
		const int cap_w = vp_wh[0];
		const int cap_h = vp_wh[1];
		if (cap_w > 0 && cap_h > 0 && cap_w <= 8192 && cap_h <= 8192)
		{
			uint8_t* px = (uint8_t*)malloc((size_t)cap_w * (size_t)cap_h * 4);
			if (px)
			{
				glPixelStorei(GL_PACK_ALIGNMENT, 1);
				glReadPixels(vp_xy[0], vp_xy[1], cap_w, cap_h, GL_RGBA, GL_UNSIGNED_BYTE, px);
				uint8_t* flipped = (uint8_t*)malloc((size_t)cap_w * (size_t)cap_h * 4);
				if (flipped)
				{
					for (int row = 0; row < cap_h; row++)
					{
						memcpy(flipped + (size_t)row * cap_w * 4,
						       px + (size_t)(cap_h - 1 - row) * cap_w * 4,
						       (size_t)cap_w * 4);
					}
					int ok = stbi_write_png(g_term_capture_path, cap_w, cap_h, 4, flipped, cap_w * 4);
					printf("[MCP] CAPTURE_TERMPP_FRAME ok=%d path=%s w=%d h=%d\n",
					       ok ? 1 : 0, g_term_capture_path, cap_w, cap_h);
					fflush(stdout);
					free(flipped);
				}
				else
				{
					printf("[MCP] CAPTURE_TERMPP_FRAME ok=0 path=%s reason=alloc_flipped\n", g_term_capture_path);
					fflush(stdout);
				}
				free(px);
			}
			else
			{
				printf("[MCP] CAPTURE_TERMPP_FRAME ok=0 path=%s reason=alloc_px\n", g_term_capture_path);
				fflush(stdout);
			}
		}
		else
		{
			printf("[MCP] CAPTURE_TERMPP_FRAME ok=0 path=%s reason=bad_viewport w=%d h=%d\n",
			       g_term_capture_path, cap_w, cap_h);
			fflush(stdout);
		}
		if (g_term_capture_buffer_path[0])
		{
			int buffer_rc = TermDumpRenderedBuffer(g_term_capture_buffer_path);
			printf("[MCP] CAPTURE_TERMPP_ALIGNED_BUFFER rc=%d path=%s png=%s\n",
			       buffer_rc, g_term_capture_buffer_path, g_term_capture_path);
			fflush(stdout);
		}
		if (g_term_capture_bridge_path[0])
		{
			int bridge_rc = TermDumpBridgeCells(g_term_capture_bridge_path);
			printf("[MCP] CAPTURE_TERMPP_ALIGNED_BRIDGE rc=%d path=%s png=%s\n",
			       bridge_rc, g_term_capture_bridge_path, g_term_capture_path);
			fflush(stdout);
		}
		g_term_capture_path[0] = 0;
		g_term_capture_buffer_path[0] = 0;
		g_term_capture_bridge_path[0] = 0;
	}

	// FL-4588 native authoring viewport: satisfy one pending read request from
	// the final standalone TERM++ draw. Embedded FBO output is intentionally
	// excluded: the browser must display the native presenter, not an editor
	// viewport plus a second glyph renderer. Store top-down RGBA8 and hash the
	// exact transmitted bytes before the loopback service can copy them.
	if (g_term_authoring_frame_requested && g_term_render_target_fbo == 0)
	{
		g_term_authoring_frame_requested = false;
		const int cap_w = vp_wh[0];
		const int cap_h = vp_wh[1];
		if (cap_w > 0 && cap_h > 0 && cap_w <= 8192 && cap_h <= 8192)
		{
			const size_t row_bytes = (size_t)cap_w * 4;
			const size_t byte_count = row_bytes * (size_t)cap_h;
			std::vector<uint8_t> bottom_up(byte_count);
			std::vector<uint8_t> top_down(byte_count);
			glPixelStorei(GL_PACK_ALIGNMENT, 1);
			glReadPixels(vp_xy[0], vp_xy[1], cap_w, cap_h,
				GL_RGBA, GL_UNSIGNED_BYTE, bottom_up.data());
			for (int row = 0; row < cap_h; ++row)
			{
				memcpy(top_down.data() + (size_t)row * row_bytes,
					bottom_up.data() + (size_t)(cap_h - 1 - row) * row_bytes,
					row_bytes);
			}
			g_term_authoring_frame_rgba.swap(top_down);
			g_term_authoring_frame_w = cap_w;
			g_term_authoring_frame_h = cap_h;
			++g_term_authoring_frame_id;
			g_term_authoring_frame_source_frame = TermPresentationFrameIdV1();
			g_term_authoring_frame_environment_presentation_packed =
				TermGetEnvironmentalAnimationPresentationPackedV1();
			fl4588_authoring_pixels_captured = true;
			TermSha256Hex(g_term_authoring_frame_rgba.data(),
				g_term_authoring_frame_rgba.size(), g_term_authoring_frame_sha256);
			if (AsciiidCopyCurrentAuthoringViewStateSha256(
					g_term_authoring_frame_view_state_sha256) != 0)
			{
				g_term_authoring_frame_view_state_sha256[0] = 0;
			}

			// FL-4588 exact-frame inspection: freeze the pipeline record with
			// the pixels. The browser can keep displaying this frame while newer
			// draws enter the ring; selecting it still reads these exact cells.
			TermAuthoringInspectionSnapshotV1& snapshot =
				g_term_authoring_inspection_history[g_term_authoring_inspection_cursor];
			snapshot = TermAuthoringInspectionSnapshotV1{};
			snapshot.identity.frame_id = g_term_authoring_frame_id;
			snapshot.identity.pixel_w = cap_w;
			snapshot.identity.pixel_h = cap_h;
			// Seam: term w/h locals (the reset hook derives bridge actuals from these
			// same values each frame; no slot returns raw actual dims).
			snapshot.identity.grid_w = width;
			snapshot.identity.grid_h = height;
			memcpy(snapshot.identity.view_state_sha256,
				g_term_authoring_frame_view_state_sha256,
				sizeof(snapshot.identity.view_state_sha256));
			if (snapshot.identity.grid_w > 0 && snapshot.identity.grid_h > 0 &&
				snapshot.identity.grid_w <= TERM_LIST::max_width &&
				snapshot.identity.grid_h <= TERM_LIST::max_height)
			{
				snapshot.cells.resize(
					static_cast<size_t>(snapshot.identity.grid_w) *
					static_cast<size_t>(snapshot.identity.grid_h));
				for (int y = 0; y < snapshot.identity.grid_h; ++y)
					for (int x = 0; x < snapshot.identity.grid_w; ++x)
					{
						TermCellInspectionV1& cell = snapshot.cells[
							static_cast<size_t>(y) * snapshot.identity.grid_w + x];
						if (TermFillCellInspectionV1(term, x, y, &cell) == 0)
							cell.frame_id = g_term_authoring_frame_id;
					}
			}
			g_term_authoring_inspection_cursor =
				(g_term_authoring_inspection_cursor + 1) %
				kTermAuthoringInspectionHistory;
		}
	}
#endif
	// FL-4548 per-cell pipeline event trace (read-only, env-gated). Runs
	// HERE — after the draw and the atomic capture block — because at this
	// point both the presented cell buffer and the engine-fact bridge are
	// final for this frame, so the differ sees exactly the state a paired
	// CAPTURE_TERMPP_FRAME_WITH_BUFFER capture would record.
	// Cell-trace emission lives product-side. TERM_LIST is resolved to raw
	// views here; the Fl4588 deposit travels by callback.
	{
		TERM_LIST* trace_term =
			g_term_last_rendered ? g_term_last_rendered : term_head;
		int trace_w = trace_term && trace_term->last_render_w > 0
			? trace_term->last_render_w : TERM_LIST::max_width;
		int trace_h = trace_term && trace_term->last_render_h > 0
			? trace_term->last_render_h : TERM_LIST::max_height;
		if (trace_w > TERM_LIST::max_width) trace_w = TERM_LIST::max_width;
		if (trace_h > TERM_LIST::max_height) trace_h = TERM_LIST::max_height;
		if (trace_term)
		{
			const InspectionPortV1* trace_nrt = AsciickerInspection();
			if (trace_nrt && trace_nrt->emit_cell_trace)
				trace_nrt->emit_cell_trace(trace_term->buf,
					trace_term->final_glyph_buf, trace_w, trace_h,
					g_fl4588_cell_trace_capture_armed,
					TermDepositCellTrace, NULL);
		}
	}
#ifdef EDITOR
	if (fl4588_authoring_pixels_captured)
		Fl4588FreezeAuthoringFrameArtifactV1();
#endif

	// FL-4260 RQ-156 native visual proof: PPM frame capture (Law 7 read-only;
	// observation, not mutation). The editor PNG path above is #ifdef EDITOR and
	// needs STB; the game build has no STB, so this writes a dependency-free
	// binary PPM (P6) of the actual TERM++ framebuffer. Env FL4260_CAPTURE_FRAME=
	// <path>. Captured once, only on a frame that has live PROFILE cells
	// (eligible_count>0), so the artifact shows the real PROFILE render.
	{
		static int s_fl4260_cap_init = 0;
		static const char* s_fl4260_cap_path = nullptr;
		static const char* s_fl4260_cap_bridge_path = nullptr;
		static int s_fl4260_cap_require_active_stroke = 0;
		static int s_fl4260_cap_done = 0;
		static int s_fl4260_cap_baseline = -1;
		// -----------------------------------------------------------------
		// FL-4684 / FL-4685 addition: capture a SEQUENCE, not just one frame.
		// -----------------------------------------------------------------
		// WHY THIS EXISTS. The project rule for any claim about MOTION -- a
		// rotating beam, a walking actor, a camera turning -- is that a single
		// still picture can never prove it. You need at least two pictures
		// separated in time, taken from ONE continuous run, so that what changed
		// between them belongs to the same live session.
		//
		// The original code above took exactly one picture per process and then
		// latched itself off. Two separate program launches would give two
		// pictures, but they would come from two different runs, which is
		// precisely what the rule forbids.
		//
		// So two optional extra environment variables are honoured:
		//   FL4260_CAPTURE_FRAME_COUNT  -- how many pictures to take (default 1)
		//   FL4260_CAPTURE_FRAME_STRIDE -- how many eligible frames to skip
		//                                  between pictures (default 1)
		// When the count is above 1 the file name gets a zero-padded number
		// glued on before its extension, so "shot.ppm" becomes "shot.000.ppm",
		// "shot.001.ppm" and so on. When the count is 1 the behaviour and the
		// file name are byte-for-byte what they were before, so every existing
		// caller is unaffected.
		//
		// This is still purely an observation: it reads pixels the renderer has
		// already drawn and writes them to a file. It changes nothing on screen.
		static int s_fl4260_cap_count = 1;    // how many shots were requested
		static int s_fl4260_cap_stride = 1;   // eligible frames between shots
		static int s_fl4260_cap_taken = 0;    // how many shots are on disk
		static int s_fl4260_cap_eligible_seen = 0;  // eligible frames so far
		if (!s_fl4260_cap_init)
		{
			s_fl4260_cap_path = getenv("FL4260_CAPTURE_FRAME");
			// FL-4512 decision-directed proof: when requested, serialize the
			// existing bridge-cell record immediately after the matching native
			// framebuffer shot. This is an observer of the same product frame;
			// it never selects a glyph or owns presentation.
			s_fl4260_cap_bridge_path = getenv("FL4260_CAPTURE_BRIDGE");
			s_fl4260_cap_require_active_stroke =
				getenv("FL4260_CAPTURE_REQUIRE_ACTIVE_STROKE") ? 1 : 0;
			// atoi returns 0 for a missing or non-numeric value, which is why
			// each result is checked before it replaces the default of 1.
			const char* count_env = getenv("FL4260_CAPTURE_FRAME_COUNT");
			const char* stride_env = getenv("FL4260_CAPTURE_FRAME_STRIDE");
			if (count_env && count_env[0])
			{
				const int requested = atoi(count_env);
				// Clamp to a sane band: at least one shot, at most 256 so a
				// mistyped value cannot fill the disk.
				if (requested > 0)
					s_fl4260_cap_count = requested > 256 ? 256 : requested;
			}
			if (stride_env && stride_env[0])
			{
				const int requested = atoi(stride_env);
				if (requested > 0)
					s_fl4260_cap_stride = requested > 4096 ? 4096 : requested;
			}
			s_fl4260_cap_init = 1;
		}
		if (s_fl4260_cap_baseline < 0)
			s_fl4260_cap_baseline = getenv("FL4260_CP437_BASELINE") ? 1 : 0;
		// FL-4260 RQ-146: CP437 baseline captures any rendered frame (mode is
		// CP437 default, so profile_routing==0 and there are no live profile cells —
		// the PROFILE gate below would never fire). Still Law-7 read-only.
		// Honest live readiness: baseline captures any frame; otherwise the
		// product reports live profile routing (never hooks-installed as a
		// proxy). Diagnostic-only env-gated path. The Classic adapter
		// reports ready for every frame.
		const InspectionPortV1* profile_nrt = AsciickerInspection();
		const bool fl4260_profile_ready = s_fl4260_cap_baseline
			? true
			: (profile_nrt && profile_nrt->profile_ready &&
				profile_nrt->profile_ready());
		// A joined FL-4512 cell-fact proof must not pair visible pixels with a
		// reset or failed Active Stroke record. Keep this requirement opt-in so
		// existing non-structural FL-4260 captures retain their historical gate.
		// Honest joined readiness: live profile routing AND, when the env
		// requires it, a certified Active Stroke observation. Uncertified or
		// absent stroke state reports unavailable (false), never a valid
		// joined proof. The active-stroke condition is never discarded. In
		// baseline mode the historical gate (profile_ready) is retained.
		const InspectionPortV1* capture_nrt = AsciickerInspection();
		const bool fl4260_cap_ready = s_fl4260_cap_baseline
			? fl4260_profile_ready
			: (capture_nrt && capture_nrt->capture_ready &&
				capture_nrt->capture_ready(
					s_fl4260_cap_require_active_stroke != 0));
		// Count every frame that PASSES the readiness gate above. The stride is
		// measured in eligible frames rather than wall-clock time so the spacing
		// between pictures is deterministic and reproducible.
		if (s_fl4260_cap_path && s_fl4260_cap_path[0] && !s_fl4260_cap_done &&
		    fl4260_cap_ready)
			s_fl4260_cap_eligible_seen++;
		// Take a picture on the first eligible frame, then on every stride-th
		// eligible frame after that, until the requested number of pictures has
		// been written.
		const bool fl4260_cap_on_this_frame =
			s_fl4260_cap_path && s_fl4260_cap_path[0] && !s_fl4260_cap_done &&
			fl4260_cap_ready &&
			(((s_fl4260_cap_eligible_seen - 1) % s_fl4260_cap_stride) == 0);
		if (fl4260_cap_on_this_frame)
		{
			const int cap_w = vp_wh[0];
			const int cap_h = vp_wh[1];
			// Keep every observation for one shot on the same index. The caller
			// can therefore join viewport pixels and bridge facts without relying
			// on timestamps or a second renderer.
			auto fl4260_indexed_capture_path = [](char* destination,
			                                           size_t destination_size,
			                                           const char* base_path,
			                                           int capture_count,
			                                           int capture_index) {
				if (capture_count <= 1)
				{
					snprintf(destination, destination_size, "%s", base_path);
					return;
				}
				const char* last_dot = strrchr(base_path, '.');
				const char* last_slash = strrchr(base_path, '/');
				if (last_dot && (!last_slash || last_dot > last_slash))
				{
					const int stem_len = (int)(last_dot - base_path);
					snprintf(destination, destination_size, "%.*s.%03d%s",
					         stem_len, base_path, capture_index, last_dot);
				}
				else
				{
					snprintf(destination, destination_size, "%s.%03d",
					         base_path, capture_index);
				}
			};
			// Build the file name for THIS picture. With a single requested shot
			// the caller's path is used unchanged, preserving old behaviour.
			// With several, a zero-padded index is inserted before the final
			// dot so the shots sort in capture order.
			char shot_path[1200];
			fl4260_indexed_capture_path(shot_path, sizeof(shot_path),
			                            s_fl4260_cap_path, s_fl4260_cap_count,
			                            s_fl4260_cap_taken);
			if (cap_w > 0 && cap_h > 0 && cap_w <= 8192 && cap_h <= 8192)
			{
				uint8_t* px = (uint8_t*)malloc((size_t)cap_w * (size_t)cap_h * 4);
				if (px)
				{
					glPixelStorei(GL_PACK_ALIGNMENT, 1);
					glReadPixels(vp_xy[0], vp_xy[1], cap_w, cap_h, GL_RGBA, GL_UNSIGNED_BYTE, px);
					FILE* pf = fopen(shot_path, "wb");
					if (pf)
					{
						fprintf(pf, "P6\n%d %d\n255\n", cap_w, cap_h);
						// glReadPixels is bottom-up; write top-down RGB
						for (int row = cap_h - 1; row >= 0; row--)
						{
							const uint8_t* src = px + (size_t)row * cap_w * 4;
							for (int col = 0; col < cap_w; col++)
								fwrite(src + (size_t)col * 4, 1, 3, pf);
						}
						fclose(pf);
						// The extra fields name WHICH picture in the sequence
						// this is and which live bridge frame produced it, so a
						// downstream receipt can prove the two stills came from
						// two different moments of the same continuous run.
						printf("[FL-4260] frame_capture ok=1 path=%s w=%d h=%d format=ppm"
						       " shot_index=%d shot_count=%d bridge_frame=%llu\n",
						       shot_path, cap_w, cap_h,
						       s_fl4260_cap_taken, s_fl4260_cap_count,
						       (unsigned long long)TermPresentationFrameIdV1());
						if (s_fl4260_cap_bridge_path && s_fl4260_cap_bridge_path[0])
						{
							char bridge_path[1200];
							fl4260_indexed_capture_path(
								bridge_path, sizeof(bridge_path),
								s_fl4260_cap_bridge_path, s_fl4260_cap_count,
								s_fl4260_cap_taken);
							const int bridge_rc = TermDumpBridgeCells(bridge_path);
							printf("[FL-4260] bridge_capture ok=%d rc=%d path=%s"
							       " shot_index=%d shot_count=%d bridge_frame=%llu\n",
							       bridge_rc == 0 ? 1 : 0, bridge_rc, bridge_path,
							       s_fl4260_cap_taken, s_fl4260_cap_count,
							       (unsigned long long)TermPresentationFrameIdV1());
						}
					}
					else
					{
						printf("[FL-4260] frame_capture ok=0 path=%s reason=fopen\n", shot_path);
					}
					fflush(stdout);
					free(px);
					s_fl4260_cap_taken++;
					// Latch off only once every requested picture is on disk.
					if (s_fl4260_cap_taken >= s_fl4260_cap_count)
						s_fl4260_cap_done = 1;
				}
			}
		}
	}

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

	// FL-4131 item 4 step 4b: unbind the embed FBO so subsequent passes (the
	// host window's normal compositing) target the default framebuffer again.
	if (g_term_render_target_fbo != 0)
	{
		glBindFramebuffer(GL_FRAMEBUFFER, 0);
	}

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

	// FL-4665: close the whole-frame span and publish this frame's receipt.
	// This is the LAST statement in term_render so the total span covers every
	// stage above it, and so a frame that freezes mid-way leaves the previous
	// frame's line already flushed to disk for post-mortem reading.
	//   width/height  — viewport in character cells
	//   wnd_wh[0..1]  — viewport in device pixels (differs from cells on Retina)
	//   gen_counter   — the render bridge's per-frame identity, which ties this
	//                   line to the frame's other receipts
	Fl4665CpuEnd(FL4665_CPU_TERM_RENDER_TOTAL);
	Fl4665FrameReport(wnd, term, width, height, wnd_wh[0], wnd_wh[1],
		TermPresentationFrameIdV1());
}

// FL-4257 — main window handle captured at open, used by SetMouseLook to
// toggle pointer-lock / relative-mouse for first-person mouse-look.
static A3D_WND* g_term_look_wnd = nullptr;

// FL-4257 — game->platform seam for first-person mouse-look (desktop). Mirrors
// the ToggleFullscreen(Game*) pattern: forward-declared at the call site
// (Game::ToggleS3FirstPerson), defined per backend. Enables SDL relative-mouse
// so motion arrives as deltas (term_mouse -> GAME_MOUSE::MOUSE_LOOK).
void SetMouseLook(Game* g, bool enable)
{
	(void)g;
	if (g_term_look_wnd)
		a3dSetRelativeMouse(g_term_look_wnd, enable);
}

void term_mouse(A3D_WND *wnd, int x, int y, MouseInfo mi)
{
	TERM_LIST *term = (TERM_LIST *)a3dGetCookie(wnd);

	// FL-4257 — relative-mouse / pointer-lock delivery: x,y are deltas, routed
	// to first-person mouse-look. Checked before the message switch because the
	// low nibble is still MOVE.
	if (mi & MouseInfo::RELATIVE)
	{
		term->game->OnMouse(GAME_MOUSE::MOUSE_LOOK, x, y);
		return;
	}

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
	float lt[4] = {global_lt[0], global_lt[1], global_lt[2], global_lt[3]};
	InitGame(term->game, water, pos, yaw, dir, lt, stamp);
	TermApplyCleanInspectionUi(term->game);
	TermApplyPlayerSpriteVisibilityToGame(term->game);
	TermApplyEnvironmentalAnimationToGame(term->game);
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
	// FL-4512 / FL-4548: this texture has exactly one allocated mip level. The
	// default GL_TEXTURE_MIN_FILTER requires a mip chain, which makes the
	// framebuffer sample incomplete while the evidence dump reads term->buf.
	// The terminal's final tuple is one cell per texel: nearest sampling and
	// clamp-to-edge are its sole presentation contract. Do not let a
	// driver-specific incomplete-texture fallback become a second owner of
	// proof-visible pixels. FL-3955.
	glBindTexture(GL_TEXTURE_2D, term->tex);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	glBindTexture(GL_TEXTURE_2D, 0);

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
	// CP437 font-sampler shader: reconstructed from the last pre-FL-4131
	// terminal owner (98d51c1f8), retaining the current engine and
	// normalized XP inputs. Both shader literals compile in every product;
	// the installed presentation backend selects at runtime.
	const char *term_fs_src_classic =
		CODE(#version 330\n)
			DEFN(P(r, g, b), vec3(r / 6., g / 7., b / 6.))
				CODE(
					/*layout(location = 0)*/ out vec4 color;
					/*layout(location = 1)*/ uniform sampler2D ansi;
					/*layout(location = 2)*/ uniform sampler2D font;
					/*layout(location = 3)*/ uniform ivec2 ansi_wh;
					in vec2 cell_coord;

					vec3 Pal(float p) {
						p = clamp(floor(p - 16.0 + 0.5), 0.0, 215.0);
						float blue = floor(p / 36.0);
						p -= 36.0 * blue;
						float green = floor(p / 6.0);
						float red = p - 6.0 * green;
						return vec3(blue, green, red) * 0.2;
					}

					void main() {
						vec2 quot_cell = floor(cell_coord);
						vec2 frac_cell = fract(cell_coord);
						vec2 ansi_coord = (quot_cell + vec2(0.5)) / ansi_wh;
						vec4 cell = texture(ansi, ansi_coord);
						int glyph_idx = int(round(cell.b * 255.0));
						frac_cell.y = 1.0 - frac_cell.y;
						vec2 glyph_coord =
							(vec2(glyph_idx & 0xF, glyph_idx >> 4) + frac_cell) /
							vec2(16.0);
						float glyph_alpha = texture(font, glyph_coord).a;
						vec3 fg_color = Pal(cell.x * 255.0);
						vec3 bg_color = Pal(cell.y * 255.0);
						color = vec4(mix(bg_color, fg_color, glyph_alpha), 1.0);
					});
	const char *term_fs_src_atlas =
		CODE(#version 330\n)
			DEFN(P(r, g, b), vec3(r / 6., g / 7., b / 6.))
				CODE(

					/*layout(location = 0)*/ out vec4 color;
					/*layout(location = 1)*/ uniform sampler2D ansi;
					/*layout(location = 3)*/ uniform ivec2 ansi_wh; // ansi texture size (in cells), constant = 160x90
					in vec2 cell_coord;

					// FL-4482 — one final GlyphId plane plus compiler-owned atlas layers
					// MODEL_PIN: shader_lookup_lut_model_pinned
					// TEXTURE2: R32UI final GlyphId (one authoritative value per cell)
					// TEXTURE3: RGBA8 LUT: GlyphId -> (compiled page layer r, atlas_x g, atlas_y b)
					// TEXTURE4: RGBA8 2D array, one physical (pack,page) per layer
					uniform usampler2D final_glyph;
					uniform sampler2D lut_tex;
					uniform sampler2DArray page_atlas;
					uniform ivec2 lut_grid;
					uniform uint lut_low_limit;
					uniform uint lut_universal_base;
					uniform vec2 page_grid;

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

					float FailureGlyph(vec2 f) {
						float stem_x = step(0.42, f.x) * step(f.x, 0.58);
						float stem_y = step(0.16, f.y) * step(f.y, 0.68);
						float dot_x = step(0.40, f.x) * step(f.x, 0.60);
						float dot_y = step(0.78, f.y) * step(f.y, 0.94);
						return max(stem_x * stem_y, dot_x * dot_y);
					}

					void main() {
						// sample ansi buffer
						vec2 quot_cell = floor(cell_coord);
						vec2 frac_cell = fract(cell_coord);

						vec2 ansi_coord = (quot_cell + vec2(0.5)) / ansi_wh;

						vec4 cell = texture(ansi, ansi_coord);

						float glyph_alpha;
						bool diagnostic_failure = false;

						uint final_gid = texture(final_glyph, ansi_coord).r;
						if (final_gid == 0xFFFFFFFDu || final_gid == 0xFFFFFFFFu || final_gid == 0xFFFFFFFEu) {
							diagnostic_failure = true;
							glyph_alpha = FailureGlyph(frac_cell);
						} else {
							uint lut_slot = 0u;
							bool lut_addressable = false;
							if (final_gid < lut_low_limit) {
								lut_slot = final_gid;
								lut_addressable = true;
							} else if (final_gid >= lut_universal_base && final_gid < lut_universal_base + 0x110000u) {
								lut_slot = lut_low_limit + final_gid - lut_universal_base;
								lut_addressable = true;
							}
							if (lut_addressable && lut_grid.x > 0 && lut_grid.y > 0) {
								ivec2 lut_xy = ivec2(int(lut_slot % uint(lut_grid.x)), int(lut_slot / uint(lut_grid.x)));
								vec4 lut_entry = texelFetch(lut_tex, lut_xy, 0);
								if (lut_entry.a > 0.5) {
									float atlas_layer = floor(lut_entry.r * 255.0 + 0.5);
									vec2 atlas_xy = vec2(floor(lut_entry.g * 255.0 + 0.5), floor(lut_entry.b * 255.0 + 0.5));
									vec2 ext_frac = frac_cell;
									ext_frac.y = 1.0 - ext_frac.y;
									vec2 glyph_coord = (atlas_xy + ext_frac) / page_grid;
									glyph_alpha = texture(page_atlas, vec3(glyph_coord, atlas_layer)).a;
								} else {
									diagnostic_failure = true;
									glyph_alpha = FailureGlyph(frac_cell);
								}
							} else {
								diagnostic_failure = true;
								glyph_alpha = FailureGlyph(frac_cell);
							}
						}

						/*
						vec3 fg_color = XTermPal(int(round(cell.r * 255.0)));
						vec3 bg_color = XTermPal(int(round(cell.g * 255.0)));
						*/

							vec3 fg_color = diagnostic_failure ? vec3(1.0, 1.0, 0.0) : Pal(cell.x * 255.00);
							vec3 bg_color = diagnostic_failure ? vec3(0.0, 0.0, 0.0) : Pal(cell.y * 255.00);

						color = vec4(mix(bg_color, fg_color, glyph_alpha), 1.0);
					});
	// Runtime pipeline selection: compiled-atlas products present through
	// the Unicode shader; Classic presents through the font shader.
	const PresentationBackendV1* shader_nrt = AsciickerPresentation();
	const char *term_fs_src = (shader_nrt &&
		shader_nrt->presents_compiled_atlas &&
		shader_nrt->presents_compiled_atlas())
		? term_fs_src_atlas
		: term_fs_src_classic;

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

	// Unicode final identity and atlas-address uniform locations.
	term->uni_final_glyph    = glGetUniformLocation(term_prg, "final_glyph");
	term->uni_lut_tex    = glGetUniformLocation(term_prg, "lut_tex");
	term->uni_page_atlas = glGetUniformLocation(term_prg, "page_atlas");
	term->uni_lut_grid  = glGetUniformLocation(term_prg, "lut_grid");
	term->uni_lut_low_limit = glGetUniformLocation(term_prg, "lut_low_limit");
	term->uni_lut_universal_base = glGetUniformLocation(term_prg, "lut_universal_base");
	term->uni_page_grid  = glGetUniformLocation(term_prg, "page_grid");

	// Game::Render promotes each live CP437 cell into this plane.
	memset(term->final_glyph_buf, 0xFF, sizeof(term->final_glyph_buf));
	// Compiled-atlas products allocate identity, LUT, and page textures
	// plus the compiler registry below. The Classic font pipeline skips
	// them and keeps zeroed atlas fields. Runtime gate, never a
	// preprocessor branch.
	const PresentationBackendV1* setup_nrt = AsciickerPresentation();
	const bool setup_atlas = setup_nrt &&
		setup_nrt->presents_compiled_atlas &&
		setup_nrt->presents_compiled_atlas();
	if (setup_atlas)
	{
	// FL-4482: sole final GlyphId texture (R32UI, max_width x max_height).
		glGenTextures(1, &term->final_glyph_tex);
		glBindTexture(GL_TEXTURE_2D, term->final_glyph_tex);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		// Allocate storage; initial data is filled during first frame upload.
		glTexImage2D(GL_TEXTURE_2D, 0, GL_R32UI, TERM_LIST::max_width, TERM_LIST::max_height,
		             0, GL_RED_INTEGER, GL_UNSIGNED_INT, nullptr);
		glBindTexture(GL_TEXTURE_2D, 0);

	// FL-4482: bind the compiler-owned exact native registry. Every page joins
	// one texture only after its content hash passes. Numeric gaps keep LUT
	// alpha zero and therefore fail closed in the shader.
		const int lut_columns = (int)FL4482_NATIVE_ATLAS_LUT_COLUMNS;
		const int lut_rows = (int)FL4482_NATIVE_ATLAS_LUT_ROWS;
		uint8_t* lut_pixels = (uint8_t*)malloc((size_t)lut_columns * (size_t)lut_rows * 4u);
		uint8_t* page_pixels = NULL;
		int page_w = 0, page_h = 0, page_layers = 0;
		int page_cell_px = kTermCompiledGlyphCellPx;
		bool atlas_loaded = lut_pixels &&
			TermLoadCompiledGlyphAtlasLayers(
				&page_pixels, &page_w, &page_h, &page_layers, &page_cell_px);
		if (lut_pixels)
			TermBuildCompiledGlyphLut(lut_pixels);

		glGenTextures(1, &term->lut_tex_id);
		glBindTexture(GL_TEXTURE_2D, term->lut_tex_id);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		if (atlas_loaded)
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, lut_columns, lut_rows, 0, GL_RGBA, GL_UNSIGNED_BYTE, lut_pixels);
		else
		{
			static const uint8_t stub_pixel[4] = {0, 0, 0, 0};
			glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, stub_pixel);
		}
		glBindTexture(GL_TEXTURE_2D, 0);

		glGenTextures(1, &term->page_atlas_id);
		glBindTexture(GL_TEXTURE_2D_ARRAY, term->page_atlas_id);
		glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
		if (atlas_loaded)
			glTexImage3D(GL_TEXTURE_2D_ARRAY, 0, GL_RGBA8, page_w, page_h,
				page_layers, 0, GL_RGBA, GL_UNSIGNED_BYTE, page_pixels);
		else
		{
			static const uint8_t stub_pixel[4] = {0, 0, 0, 0};
			glTexImage3D(GL_TEXTURE_2D_ARRAY, 0, GL_RGBA8, 1, 1, 1,
				0, GL_RGBA, GL_UNSIGNED_BYTE, stub_pixel);
		}
		glBindTexture(GL_TEXTURE_2D_ARRAY, 0);

		term->lut_columns = atlas_loaded ? lut_columns : 0;
		term->lut_rows = atlas_loaded ? lut_rows : 0;
		term->page_atlas_cols = atlas_loaded && page_cell_px > 0 ? (float)(page_w / page_cell_px) : 1.0f;
		term->page_atlas_rows = atlas_loaded && page_cell_px > 0 ? (float)(page_h / page_cell_px) : 1.0f;
		printf("[FL-4482] TERM++ native atlas exact_bindings=%u layers=%u hash=%s loaded=%d\n",
			(unsigned)FL4482_NATIVE_ATLAS_BINDING_COUNT,
			(unsigned)page_layers,
			FL4482_NATIVE_ATLAS_REGISTRY_SHA256,
			atlas_loaded ? 1 : 0);
		fflush(stdout);
		if (page_pixels)
			free(page_pixels);
		if (lut_pixels)
			free(lut_pixels);
	}
	else
	{
		term->lut_columns = 0;
		term->lut_rows = 0;
		term->page_atlas_cols = 1.0f;
		term->page_atlas_rows = 1.0f;
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

	// FL-4131 item 4 step 4b: only walk the singly-linked window-bound list
	// when a real A3D_WND is backing this TERM_LIST. The embed constructor
	// owns its TERM_LIST pointer directly and stays off this list so
	// TermCloseAll/TermResizeAll don't touch it.
	if (wnd)
	{
		term->prev = term_tail;
		term->next = 0;
		if (term_tail)
			term_tail->next = term;
		else
			term_head = term;
		term_tail = term;

		a3dSetCookie(wnd, term);
		a3dSetIcon(wnd, "./assets/icons/app.png");
		// FL-4588: the HTML authoring shell embeds this framebuffer. When the
		// native process is its backend, retain the hidden GL window and never
		// expose a second TERM++ application surface.
		// Mark the presenter render-active in every mode. The SDL backend
		// keeps its physical window hidden for HTML authoring while preserving
		// `mapped=true`, which is required to produce the embedded final frame.
		a3dSetVisible(wnd, true);
		if (!getenv("ASCIIID_HTML_SHELL_BACKEND"))
		{
			a3dSetFocus(wnd);
		}
	}
	else
	{
		// FL-4131 item 4 step 4b: no-window embed. TermOpenEmbedded set
		// g_term_init_capture so it can fetch the new TERM_LIST without
		// a window cookie. The embed stays off term_head/term_tail.
		g_term_init_capture = term;
	}
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
		const bool mod_zoom =
			a3dGetKeyb(wnd, A3D_LCTRL) || a3dGetKeyb(wnd, A3D_RCTRL) ||
			a3dGetKeyb(wnd, A3D_LWIN)  || a3dGetKeyb(wnd, A3D_RWIN);
		if (ki == A3D_NUMPAD_ADD || (mod_zoom && ki == A3D_OEM_PLUS))
		{
			NextGLFont();
		}
		else if (ki == A3D_NUMPAD_SUBTRACT || (mod_zoom && ki == A3D_OEM_MINUS))
		{
			PrevGLFont();
		}
		else if (ki == A3D_F9)
		{
			TermSetPlayerSpriteVisible(!TermGetPlayerSpriteVisible());
		}
		else if (ki == A3D_F12)
		{
			// FL-4588: the retired TERM++ hotkey must not bypass the typed
			// HTML action owner. The browser toggle remains keyboard reachable
			// through the focused HTML checkbox Space behavior.
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
	if (term->final_glyph_tex) glDeleteTextures(1, &term->final_glyph_tex);
	if (term->lut_tex_id) glDeleteTextures(1, &term->lut_tex_id);
	if (term->page_atlas_id) glDeleteTextures(1, &term->page_atlas_id);
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
	// FL-4876 CELL-RES-01: ASCIICKER_WINDOW_SIZE=<w>x<h> (window points) opens a
	// larger or smaller window, e.g. to exercise the enlarged cell-grid caps.
	{
		const char* ws = getenv("ASCIICKER_WINDOW_SIZE");
		int ww = 0, wh = 0;
		if (ws && sscanf(ws, "%dx%d", &ww, &wh) == 2 && ww >= 320 && wh >= 200 && ww <= 16384 && wh <= 16384)
		{
			rc[2] = ww;
			rc[3] = wh;
		}
	}
	gd.wnd_mode = A3D_WND_NORMAL;
	gd.wnd_xywh = rc;

	A3D_WND *wnd = a3dOpen(&pi, &gd, share);
	g_term_look_wnd = wnd; // FL-4257 — handle for SetMouseLook pointer-lock toggle

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
	// FL-4588 single-window invariant evidence: every presenter window
	// creation announces itself with the TOTAL number of terminal windows
	// now alive. Under the sole-presenter cutover this must print exactly
	// once per loaded document with count=1; a second print with count=2
	// would be direct proof that a retired launch owner survived.
	{
		int open_windows = 0;
		for (TERM_LIST* walk = term_head; walk; walk = walk->next)
			++open_windows;
		printf("[FL-4588] TermOpen: presenter window created (count now %d)\n",
		       open_windows);
		fflush(stdout);
	}

	/*
	term->yaw = yaw;
	term->pos[0] = pos[0];
	term->pos[1] = pos[1];
	term->pos[2] = pos[2];
	term->water = 0;
	*/

	return term->game;
}

// FL-4588 condition-2 (sole continuous presenter): true when any detached
// TERM++ window is alive. term_head is the head of the linked list of all
// open terminal windows; it is only ever touched from the main thread (same
// contract as TermOpen/TermCloseAll), so a plain read is safe here.
int TermHasOpenWindow()
{
	return term_head != 0;
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

void TermRenderAll()
{
	A3D_PUSH_CONTEXT push;
	a3dPushContext(&push);

	TERM_LIST *term = term_head;
	while (term)
	{
		TERM_LIST *next = term->next;
		a3dSwitchContext(term->wnd);
		term_render(term->wnd);
		term = next;
	}

	a3dPopContext(&push);
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

static int TermApplyPlayerSpriteVisibilityToGame(Game* g)
{
	if (!g)
		return 0;

	if (g_term_player_sprite_visible)
	{
		if (!g->player.player_inst && g->player.sprite && world)
			EnsureLocalPlayerInst(g, g->player.sprite, g->player.pos, g->player.dir, g->player.anim, g->player.frame);
		if (g->player.player_inst)
		{
			SetInstFlags(g->player.player_inst, GetInstFlags(g->player.player_inst) | INST_VISIBLE);
			return 1;
		}
		return 0;
	}

	if (g->player.player_inst)
	{
		SetInstFlags(g->player.player_inst, GetInstFlags(g->player.player_inst) & ~INST_VISIBLE);
		return 1;
	}
	return 0;
}

int TermSetPlayerSpriteVisible(int visible)
{
	g_term_player_sprite_visible = visible != 0;
	int updated = 0;
	TERM_LIST* term = term_head;
	while (term)
	{
		updated += TermApplyPlayerSpriteVisibilityToGame(term->game);
		term = term->next;
	}
	return updated;
}

int TermGetPlayerSpriteVisible()
{
	return g_term_player_sprite_visible ? 1 : 0;
}

static int TermApplyEnvironmentalAnimationToGame(Game* g)
{
	if (!g || !g->renderer)
		return 0;
	RendererSetEnvironmentalAnimationEnabled(
		g->renderer, g_term_environment_animation_enabled ? 1 : 0);
	return 1;
}

int TermSetEnvironmentalAnimationEnabled(int enabled)
{
	const bool requested = enabled != 0;
	if (requested != g_term_environment_animation_enabled)
	{
		g_term_environment_animation_enabled = requested;
		++g_term_environment_animation_revision;
	}
	int updated = 0;
	for (TERM_LIST* term = term_head; term; term = term->next)
		updated += TermApplyEnvironmentalAnimationToGame(term->game);
	return updated;
}

int TermGetEnvironmentalAnimationEnabled()
{
	return g_term_environment_animation_enabled ? 1 : 0;
}

uint64_t TermGetEnvironmentalAnimationPresentationPackedV1()
{
	return (g_term_environment_animation_revision << 1) |
		(g_term_environment_animation_enabled ? 1ull : 0ull);
}

int TermResetEnvironmentalPhase()
{
	int updated = 0;
	for (TERM_LIST* term = term_head; term; term = term->next)
	{
		if (!term->game || !term->game->renderer)
			continue;
		// FL-4512 U9: this is the sole TERM++ renderer for a normal
		// user-requested environment-phase reset.  Do not create a proof-only
		// phase writer beside it.
		RendererResetObservePhase(term->game->renderer);
		updated++;
	}
	return updated;
}

int TermSetPerformanceHudVisible(int visible)
{
	RendererSetTermPerformanceHudVisible(visible ? 1 : 0);
	return 1;
}

int TermGetPerformanceHudVisible()
{
	return RendererGetTermPerformanceHudVisible();
}

int TermSetWindowRect(int x, int y, int w, int h)
{
	// FL-4588: window choreography is retired in HTML-shell backend mode.
	// The embedded TERM++ canvas has no standalone window to position.
	if (getenv("ASCIIID_HTML_SHELL_BACKEND"))
		return 0;
	if (!g_term_look_wnd || w < 320 || h < 240)
		return 0;
	int rect[4] = {x, y, w, h};
	if (!a3dSetRect(g_term_look_wnd, rect, A3D_WND_NORMAL))
		return 0;
	a3dSetVisible(g_term_look_wnd, true);
	a3dSetFocus(g_term_look_wnd);
	return 1;
}

// FL-4588: HTML authoring follow zoom for the Game::Render world pass.
// Zero/negative means "use the historical hard-coded 1.0f zoom" and also
// means authoring camera follow is NOT armed (Game may keep its own
// perspective / focus_node camera center — that path is for bare TERM++ /
// gameplay, not the dual HTML canvases).
static float g_term_authoring_world_zoom = 0.0f;
// Sole synchronized authoring footpoint. Written only by TermSetCameraViewEx
// when follow is armed. Game::Render reads this instead of player.pos because
// each TERM frame's PhysicsStep can overwrite player.pos from io.pos and
// revive a divergent camera center after the editor already pushed (x,y,z,yaw).
static float g_term_authoring_follow_x = 0.0f;
static float g_term_authoring_follow_y = 0.0f;
static float g_term_authoring_follow_z = 0.0f;
static float g_term_authoring_follow_yaw = 0.0f;

float TermGetAuthoringWorldZoomOrDefault(float fallback)
{
	if (g_term_authoring_world_zoom > 0.0f)
		return g_term_authoring_world_zoom;
	return fallback;
}

int TermAuthoringCameraFollowActive(void)
{
	// Armed only by TermSetCameraViewEx with a positive world zoom from the
	// editor follow block. Bare TermSetCameraView does not arm this flag.
	return g_term_authoring_world_zoom > 0.0f ? 1 : 0;
}

int TermGetAuthoringFollowCamera(
	float* out_x, float* out_y, float* out_z, float* out_yaw)
{
	if (!TermAuthoringCameraFollowActive())
		return 0;
	if (out_x) *out_x = g_term_authoring_follow_x;
	if (out_y) *out_y = g_term_authoring_follow_y;
	if (out_z) *out_z = g_term_authoring_follow_z;
	if (out_yaw) *out_yaw = g_term_authoring_follow_yaw;
	return 1;
}

int TermSetCameraViewEx(float x, float y, float z, float yaw, float world_zoom)
{
	if (world_zoom <= 0.0f)
		return 0;
	if (world_zoom < 0.2f) world_zoom = 0.2f;
	// Authoring follow may exceed the gameplay HUD zoom knob (5.0).
	if (world_zoom > 64.0f) world_zoom = 64.0f;
	g_term_authoring_world_zoom = world_zoom;
	// FL-4588 delete-first camera ownership: HTML authoring follow is render
	// transport, never a hidden player/physics teleport.  The legacy
	// TermSetCameraView path remains outside authoring follow; calling it here
	// revived player-position, teleport-command, session, and smoothing owners.
	g_term_authoring_follow_x = x;
	g_term_authoring_follow_y = y;
	g_term_authoring_follow_z = z;
	g_term_authoring_follow_yaw = yaw;
	int updated = 0;
	for (TERM_LIST* term = term_head; term; term = term->next)
		if (term->game)
			updated++;
	return updated;
}

int TermSetCameraView(float x, float y, float z, float yaw)
{
	int updated = 0;
	TERM_LIST* term = term_head;
	while (term)
	{
		Game* g = term->game;
		if (g)
		{
			g->player.pos[0] = x;
			g->player.pos[1] = y;
			g->player.pos[2] = z;
			g->player.dir = yaw;
			g->player.prev_yaw = yaw;
			PhysicsTeleportCommand cmd = {};
			cmd.set_pos = true;
			cmd.pos[0] = x;
			cmd.pos[1] = y;
			cmd.pos[2] = z;
			cmd.set_yaw = true;
			cmd.yaw = yaw;
			cmd.set_dir = true;
			cmd.dir = yaw;
			PhysicsTeleport(g->physics, cmd);
			TermApplyCleanInspectionUi(g);
			TermApplyPlayerSpriteVisibilityToGame(g);
			updated++;
		}
		term = term->next;
	}
	return updated;
}

int TermSetLight(float x, float y, float z, float ambience)
{
	int updated = 0;
	TERM_LIST* term = term_head;
	while (term)
	{
		Game* g = term->game;
		if (g)
		{
			g->session.light[0] = x;
			g->session.light[1] = y;
			g->session.light[2] = z;
			g->session.light[3] = ambience;
			updated++;
		}
		term = term->next;
	}
	return updated;
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

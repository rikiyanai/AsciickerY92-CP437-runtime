// =============================================================================
// ASCIICKER MAP EDITOR -- Terrain, Mesh, and Sprite Editing Application
// =============================================================================
//
// PURPOSE:
// Main editor application for creating and modifying Asciicker game maps.
// Provides 8 editing modes for terrain sculpting, material painting, mesh/sprite
// placement, item positioning, and enemy generation -- all rendered in real-time
// using OpenGL 3.3+/4.5 with a Dear ImGui overlay for tools and panels.
//
// WHY MONOLITHIC FILE (10,655 lines):
// - Single compilation unit: All editor state is file-scoped (static globals)
// - ImGui integration: UI code interleaves with rendering and input handling
// - Historical: Organic growth from initial prototype, not refactored into modules
// - Practical: Single file simplifies build system (one .o file, one makefile target)
//
// EIGHT EDITING MODES (edit_mode variable, line 1468):
//
// MODE 0: SCULPT -- Terrain height map editing
//   - Gaussian/Square/Noise brush shapes (brush_shape line 1474)
//   - Ascent/Descent: br_alpha sign controls raise (+) or lower (-) terrain
//   - Blur/Sharpen: Shift modifier for smoothing or sharpening height transitions
//   - Height probe: Ctrl+Shift samples terrain height at cursor
//   - Diagonal flip: Ctrl flips terrain triangle diagonal for smooth transitions
//   - Multi-tile: Alt creates/deletes patches in radius (br_tile_radius)
//
// MODE 1: MAT-id -- Material painting
//   - Paint material IDs (0-255) onto terrain visual cells (8x8 per patch)
//   - Auto-material by slope/elevation (ApplyAutoMatElev, ApplyAutoTexture)
//   - Material baking from meshes (BakeMeshesToTerrain)
//   - Material system: 256 slots (MyMaterial[256]), 4 elevation ramps x 16 shade levels
//
// MODE 2: MESH -- 3D mesh instance placement
//   - Browse mesh library from assets/meshes/ directory (.akm files)
//   - [DEPENDENCY:BLENDER] Meshes exported from Blender via io_mesh_akm addon
//   - Place with transform (MeshPrefs: scale, rotation, terrain alignment)
//   - Drag/nudge existing instances (arrow keys, mouse drag)
//   - Selection tools (marquee, individual click, delete with Ctrl)
//
// MODE 3: DIAG -- Terrain diagonal manipulation
//   - Flip triangle diagonals within terrain patches for smoother height transitions
//   - Each terrain cell has 2 triangles, diagonal determines which vertices connect
//
// MODE 4: SPRITE -- 2D sprite instance placement
//   - Browse sprite library from assets/sprites/ directory (.xp files)
//   - TODO(PIPELINE-FIX) Assumes raw .xp format, pipeline may pre-process sprites
//   - Place with animation/frame/yaw preferences (SpritePrefs)
//   - Randomization options (rand_anim, rand_frame, rand_yaw)
//   - Animation timing controls (t[0-3] for loop/ping-pong frame duplication)
//
// MODE 5: ITEM -- Inventory item placement
//   - Place items from inventory system (weapons, armor, consumables)
//   - Delete existing items (Ctrl+click)
//   - Item types: Weapon (W), Shield (S), Helmet (H), Armor (A), Potion (P), Food (F), Door (D)
//
// MODE 6: ENEMYGEN -- Enemy spawner placement
//   - Configure alive_max (simultaneous spawn count)
//   - Revive timing (revive_min/max are EXPONENTS: 2^n seconds)
//   - Equipment stats (armor, helmet, shield, sword, crossbow)
//   - Uses enemygen.xp sprite for preview (line 10580-10581)
//
// MODE 7: [Additional mode]
//
// RENDERING PIPELINE (RenderContext struct, lines 1506-2700):
// The editor includes a complete OpenGL 3.3+/4.5 rendering pipeline with multiple
// shader programs for different content types:
// - ansi: Terminal-style grid rendering (not used in main 3D view)
// - mesh: 3D mesh rendering with per-vertex colors
// - BSP: Binary space partitioning for efficient culling
// - terrain: Terrain patch rendering with height/visual maps
// - sprite: Billboard sprite rendering with animation support
//
// Shader code is embedded inline using CODE() macro (line 1504) which stringifies
// GLSL source. Total shader code: ~1200 lines within RenderContext::Create.
//
// MATERIAL SYSTEM (MyMaterial, lines 543-877):
// 256 material slots providing texture/color definitions for terrain visual cells.
// Each material inherits from render.h Material struct and adds Init() method.
// Default materials (water, grass, dirt, stone, sand, snow, mud, cobblestone, gravel)
// are initialized in MyMaterial::Init() with 4 elevation ramps x 16 shade levels = 64
// color variations per material type.
//
// Auto-material assignment: ApplyAutoTexture (line 3722) assigns materials based on
// terrain slope and elevation, allowing procedural terrain painting.
//
// FILE I/O (Load/Save/Merge):
// - Load() (line 5504): Reads .a3d binary format (terrain + materials + world + enemygens)
// - New() (line 5174): Creates new map with Perlin noise terrain or loaded heightmap
// - MergeOpen/MergeCommit (lines 351, 412): Imports patches/meshes from other maps
// - Save operations: Write terrain, materials, world instances, enemygens sequentially
//
// MAP MERGING (Merge struct, lines 234-440):
// Allows importing terrain patches and mesh instances from another .a3d file into
// current map with offset. Uses max-height merging for overlapping terrain patches.
// WHY: Level designers can create modular map sections and combine them.
//
// KEY DATA STRUCTURES (file-scoped statics):
// - terrain (Terrain*): Current height map + material grid
// - world (World*): Scene graph containing meshes and sprite instances
// - active_mesh (Mesh*): Selected mesh for MODE 2 placement
// - active_sprite (Sprite*): Selected sprite for MODE 4 placement
// - selected_inst (Inst*): Frame-selected instance for editing/deletion
// - mat[256] (MyMaterial): Material definitions for terrain painting
// - edit_mode (int): Current editing mode 0-7
// - render_context (RenderContext): OpenGL state, shaders, uniforms
//
// KEY FUNCTIONS:
// - my_init() (line 10015): Initialize OpenGL, ImGui, load assets, setup editor state
// - my_render() (line 5921): Main frame loop - ImGui UI, editing logic, 3D rendering
// - my_mouse() (line 9907): Mouse input handling for painting, selection, camera
// - my_keyb_key() (line 10238): Keyboard input handling, modifier keys to ImGui
// - Load() (line 5504): Load map from .a3d binary file
// - New() (line 5174): Create empty map with default/Perlin/image terrain
// - Stamp() (line 4664): Apply terrain height brush stroke with falloff
// - Palettize() (line 4842): Map RGB colors to palette indices via GPU shader
// - SpriteScan() (line 5087): Scan assets/sprites/ directory and load all .xp files
// - MeshScan() (line 5136): Scan assets/meshes/ directory and load all .akm files
//
// INTEGRATION POINTS:
// - terrain.h: Terrain height map and patch management (5x5 vertex grid, 8x8 visual cells)
// - world.h: World scene graph (meshes, sprites, instances, BSP spatial index)
// - sprite.h: Sprite loading and rendering (.xp format, multi-frame, multi-angle)
// - render.h: AnsiCell buffer format, Material struct, shared rendering definitions
// - urdo.h: Undo/redo system for all editor operations (terrain, instances, patches)
// - vendor/imgui/imgui.h: Dear ImGui for editor UI panels (mode selector, tool properties)
// - platform.h: Windowing/input abstraction (PlatformInterface, mouse, keyboard)
//
// [DEPENDENCY:BLENDER] Mesh files (.akm) exported from Blender via io_mesh_akm addon
// [DATA-CONTRACT:A3D] File format: terrain + materials + world + enemygens (binary)
// [DATA-CONTRACT:AKM] Mesh format: vertices, faces, per-vertex colors (Blender-exported)
// =============================================================================

/**
 * asciiid.cpp - Asciicker Map Editor
 *
 * This is the main editor application for creating and modifying Asciicker game maps.
 * The editor provides tools for:
 * - Terrain sculpting (height map editing)
 * - Material/texture painting
 * - Mesh placement (3D models)
 * - Sprite placement (2D billboards)
 * - Enemy generation
 * - Map import/export/merge
 *
 * UI is built with Dear ImGui, rendering uses OpenGL 3.3+/4.5
 * Supports undo/redo through urdo.h system
 */

#define NOMINMAX // Prevent Windows min/max macros from conflicting with std::min/max

#include <wchar.h>
#include <stdio.h>
#include <algorithm>
#include <unordered_set>
#include <vector>

#ifdef __linux__
#include <linux/limits.h>
#elif defined(__APPLE__)
#include <limits.h>
#else
#define PATH_MAX 1024
#endif

#define _USE_MATH_DEFINES
#include <math.h>
#include <ctype.h>
#include <stdint.h>
#include <stdlib.h>
#include <assert.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include <unordered_map>

#include "gl.h"
#include "gl45_emu.h"

#include "rgba8.h"

// [DEPENDENCY:IMGUI] Third-Party Library Integration
//
// WHAT: Dear ImGui v1.69 (vendor/imgui/*.cpp/h) -- immediate-mode GUI library
// WHY: Provides all editor UI panels (tools, meshes, sprites, materials)
// WHERE: Used exclusively by asciiid.cpp editor (NOT used in game builds)
//
// INTEGRATION POINTS:
// - vendor/imgui/imgui.h:             Core API (Begin, Button, Slider, Text, etc.)
// - vendor/imgui/imgui_internal.h:    Internal API (beta features: ImGuiItemFlags_Disabled)
// - imgui_impl_opengl3.cpp:    OpenGL 3.3+ rendering backend (RenderDrawData)
// - Custom platform backend:   This file implements keyboard/mouse input mapping
//
// INITIALIZATION: ImGui::CreateContext() + ImGui_ImplOpenGL3_Init() in my_init()
// FRAME LOOP: NewFrame() -> Begin/End windows -> Render() -> RenderDrawData() in my_render()
// INPUT: my_mouse() and my_keyb_key() feed events to ImGui io struct
//
// VERSION LOCK: v1.69 (2019) -- newer versions require docking branch changes
// WHY v1.69: Stable release before multi-viewport/docking API changes (v1.80+).
//            Newer versions require significant integration refactoring.

#include "vendor/imgui/imgui.h"
#include "vendor/imgui/imgui_internal.h" // beta: ImGuiItemFlags_Disabled

#include "imgui_impl_opengl3.h"

#include "platform/window_backend.h"
#include "platform/input_backend.h"
#include "platform/time_backend.h"
#include "platform/filesystem_backend.h"
#include "platform/image_backend.h"

#include "texheap.h"
#include "terrain.h"
#include "world_internal.h"
#include "enemygen.h"
#include "sprite.h"
#include "sprite_registry.h"
#include "glyph_manifest.h"
#include "material_sidecar.h"
#include "third_party/cjson/cJSON.h"

#include "urdo.h"

#include "matrix.h"

#include "fast_rand.h"

// MCP includes
#ifndef _WIN32
#include <sys/select.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#endif

#include "editor_state.h"
// game.h must be included BEFORE the compatibility macros below, because
// game.h uses 'story_id' as a struct field name which would collide with
// the #define story_id macro.
#include "game.h"

// [FL-3851] PNG writer for clean frame capture
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "vendor/stb_image_write.h"

// FL-2785: EditorDocumentState and EditorState consolidate file-scoped globals.
// Access via g_editor_document.field and g_editor_state.field.
EditorDocumentState g_editor_document = {};
EditorState g_editor_state = {};

// ── Compatibility macros for migrated globals ──
// These let existing code continue to compile without changes. New code should
// use g_editor_state.field / g_editor_document.field directly.
#define edit_mode       g_editor_state.edit_mode
#define br_radius       g_editor_state.br_radius
#define brush_shape     g_editor_state.brush_shape
#define br_alpha        g_editor_state.br_alpha
#define br_tile_radius  g_editor_state.br_tile_radius
#define br_limit        g_editor_state.br_limit
#define probe_z         g_editor_state.probe_z
#define story_id        g_editor_state.story_id
#define diag_flipped    g_editor_state.diag_flipped
#define creating        g_editor_state.creating
#define painting        g_editor_state.painting
#define painting_x      g_editor_state.painting_x
#define painting_y      g_editor_state.painting_y
#define eg_alive_max    g_editor_state.eg_alive_max
#define eg_revive_min   g_editor_state.eg_revive_min
#define eg_revive_max   g_editor_state.eg_revive_max
#define eg_armor        g_editor_state.eg_armor
#define eg_helmet       g_editor_state.eg_helmet
#define eg_shield       g_editor_state.eg_shield
#define eg_sword        g_editor_state.eg_sword
#define eg_crossbow     g_editor_state.eg_crossbow
#define spinning        g_editor_state.spinning
#define spinning_x      g_editor_state.spinning_x
#define spinning_y      g_editor_state.spinning_y
#define g_mcp_mode      g_editor_document.mcp_mode
#define g_batch_mode    g_editor_document.batch_mode
#define g_startup_viewer_mode  g_editor_document.startup_viewer_mode
#define g_startup_sprite_browser  g_editor_document.startup_sprite_browser
#define g_startup_map_path   g_editor_document.startup_map_path
#define g_startup_sprite_path g_editor_document.startup_sprite_path
#define g_current_map_path   g_editor_document.current_map_path
#define g_last_save_map_error g_editor_document.last_save_map_error

bool g_asciiid_editor_idle_yield = true;
static bool g_fl4131_headless_material_proof = false;

static bool AsciiidManifestLookupCoverage(GlyphId glyph_id, uint16_t* out_coverage);

enum
{
	kEditorPathMax = 4096,
	kPaletteCount = 256,
	kMaterialGridRowCount = 4,
	kMaterialGridColCount = 16,
};

bool IsStdinReady() {
#ifndef _WIN32
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(STDIN_FILENO, &fds);
    struct timeval tv = { 0, 0 };
    return select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv) > 0;
#else
    return false; // TODO: Windows implementation
#endif
}


// ProcessMCPCommand moved to ensure dependencies are loaded
void ProcessMCPCommand(char* line);

// ── CDP-style TCP server for external tool integration (FL-3851) ──
// Accepts one client on localhost:<port>. Messages are newline-delimited JSON:
//   Request:  {"id":1,"method":"LOAD_MAP","params":"output.a3d"}\n
//   Response: {"id":1,"result":"..."}\n
//   Event:    {"method":"FRAME_CAPTURED","params":{"path":"/tmp/frame.png"}}\n
// ProcessMCPCommand is reused — the method field becomes the MCP command line.
// Only one client at a time; new connections replace the old one.
#ifndef _WIN32
static int g_cdp_listen_fd = -1;   // listening socket
static int g_cdp_client_fd = -1;   // accepted client
static int g_cdp_port = 0;         // 0 = disabled
static char g_cdp_recv_buf[8192];  // partial message accumulator
static int g_cdp_recv_len = 0;

// Output capture: when a CDP request is being processed, stdout is redirected
// to this buffer so we can send the response back to the client.
static char g_cdp_response_buf[65536];
static int g_cdp_response_len = 0;
static bool g_cdp_capturing = false;
static int g_cdp_current_id = -1;

static void CdpInit(int port)
{
	g_cdp_port = port;
	g_cdp_listen_fd = socket(AF_INET, SOCK_STREAM, 0);
	if (g_cdp_listen_fd < 0) {
		printf("[CDP] Error: socket() failed\n");
		return;
	}

	int opt = 1;
	setsockopt(g_cdp_listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

	// Non-blocking listen socket
	int flags = fcntl(g_cdp_listen_fd, F_GETFL, 0);
	fcntl(g_cdp_listen_fd, F_SETFL, flags | O_NONBLOCK);

	struct sockaddr_in addr = {};
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK); // localhost only
	addr.sin_port = htons(port);

	if (bind(g_cdp_listen_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
		printf("[CDP] Error: bind(%d) failed\n", port);
		close(g_cdp_listen_fd);
		g_cdp_listen_fd = -1;
		return;
	}
	listen(g_cdp_listen_fd, 1);
	printf("[CDP] Listening on localhost:%d\n", port);
	fflush(stdout);
}

static void CdpSendRaw(const char* data, int len)
{
	if (g_cdp_client_fd < 0) return;
	int sent = 0;
	while (sent < len) {
		int n = (int)send(g_cdp_client_fd, data + sent, len - sent, 0);
		if (n <= 0) {
			printf("[CDP] Client disconnected (send)\n");
			close(g_cdp_client_fd);
			g_cdp_client_fd = -1;
			return;
		}
		sent += n;
	}
}

static void CdpSendResponse(int id, const char* result, int result_len)
{
	// Build: {"id":<id>,"result":"<escaped result>"}\n
	// For simplicity, base64 or escape newlines in result
	char header[64];
	int hlen = snprintf(header, sizeof(header), "{\"id\":%d,\"result\":\"", id);
	CdpSendRaw(header, hlen);

	// Escape the result string (newlines, quotes, backslashes)
	for (int i = 0; i < result_len; i++) {
		char c = result[i];
		if (c == '"')       { CdpSendRaw("\\\"", 2); }
		else if (c == '\\') { CdpSendRaw("\\\\", 2); }
		else if (c == '\n') { CdpSendRaw("\\n", 2); }
		else if (c == '\r') { CdpSendRaw("\\r", 2); }
		else if (c == '\t') { CdpSendRaw("\\t", 2); }
		else                { CdpSendRaw(&c, 1); }
	}

	CdpSendRaw("\"}\n", 3);
}

static void CdpSendEvent(const char* method, const char* params_json)
{
	char buf[4096];
	int len = snprintf(buf, sizeof(buf), "{\"method\":\"%s\",\"params\":%s}\n",
	                   method, params_json);
	if (len > 0 && len < (int)sizeof(buf))
		CdpSendRaw(buf, len);
}

// Called from printf-interception to capture MCP command output
static void CdpCaptureOutput(const char* data, int len)
{
	if (!g_cdp_capturing) return;
	int avail = (int)sizeof(g_cdp_response_buf) - g_cdp_response_len - 1;
	if (len > avail) len = avail;
	if (len > 0) {
		memcpy(g_cdp_response_buf + g_cdp_response_len, data, len);
		g_cdp_response_len += len;
	}
}

// Process one complete JSON message line
static void CdpProcessMessage(char* msg)
{
	// Minimal JSON parsing — extract "id", "method", "params"
	// Format: {"id":N,"method":"CMD","params":"args"}
	int id = -1;
	char method[256] = {0};
	char params[4096] = {0};

	// Extract id
	const char* id_key = strstr(msg, "\"id\"");
	if (id_key) {
		id_key += 4;
		while (*id_key == ':' || *id_key == ' ') id_key++;
		id = atoi(id_key);
	}

	// Extract method
	const char* m_key = strstr(msg, "\"method\"");
	if (m_key) {
		m_key += 8;
		while (*m_key == ':' || *m_key == ' ') m_key++;
		if (*m_key == '"') {
			m_key++;
			int i = 0;
			while (*m_key && *m_key != '"' && i < (int)sizeof(method) - 1)
				method[i++] = *m_key++;
			method[i] = 0;
		}
	}

	// Extract params (string value)
	const char* p_key = strstr(msg, "\"params\"");
	if (p_key) {
		p_key += 8;
		while (*p_key == ':' || *p_key == ' ') p_key++;
		if (*p_key == '"') {
			p_key++;
			int i = 0;
			while (*p_key && *p_key != '"' && i < (int)sizeof(params) - 1) {
				if (*p_key == '\\' && *(p_key+1)) { p_key++; } // unescape
				params[i++] = *p_key++;
			}
			params[i] = 0;
		}
	}

	if (!method[0]) {
		if (id >= 0) CdpSendResponse(id, "error: no method", 16);
		return;
	}

	// Build MCP command line: "METHOD params"
	char cmd_line[4352];
	if (params[0])
		snprintf(cmd_line, sizeof(cmd_line), "%s %s\n", method, params);
	else
		snprintf(cmd_line, sizeof(cmd_line), "%s\n", method);

	// Capture stdout during ProcessMCPCommand using dup2
	// Save original stdout, redirect to pipe, run command, restore stdout
	g_cdp_response_buf[0] = 0;
	g_cdp_response_len = 0;

	int pipefd[2] = {-1, -1};
	int saved_stdout = -1;
	bool capturing = (id >= 0 && pipe(pipefd) == 0);

	if (capturing) {
		fflush(stdout);
		saved_stdout = dup(STDOUT_FILENO);
		dup2(pipefd[1], STDOUT_FILENO);
		close(pipefd[1]);
		// Make read end non-blocking
		int fl = fcntl(pipefd[0], F_GETFL, 0);
		fcntl(pipefd[0], F_SETFL, fl | O_NONBLOCK);
	}

	ProcessMCPCommand(cmd_line);

	if (capturing) {
		fflush(stdout);
		// Restore stdout
		dup2(saved_stdout, STDOUT_FILENO);
		close(saved_stdout);

		// Read captured output from pipe
		while (g_cdp_response_len < (int)sizeof(g_cdp_response_buf) - 1) {
			int n = (int)read(pipefd[0], g_cdp_response_buf + g_cdp_response_len,
			                  sizeof(g_cdp_response_buf) - g_cdp_response_len - 1);
			if (n <= 0) break;
			g_cdp_response_len += n;
		}
		close(pipefd[0]);
		g_cdp_response_buf[g_cdp_response_len] = 0;

		CdpSendResponse(id, g_cdp_response_buf, g_cdp_response_len);

		// Also echo to real stdout for logging
		printf("[CDP] cmd=%s -> %d bytes response\n", method, g_cdp_response_len);
		fflush(stdout);
	}
}

// Poll for connections and data — call once per frame from the render loop
static void CdpPoll()
{
	if (g_cdp_listen_fd < 0) return;

	// Accept new connections
	if (g_cdp_client_fd < 0) {
		struct sockaddr_in client_addr;
		socklen_t client_len = sizeof(client_addr);
		int fd = accept(g_cdp_listen_fd, (struct sockaddr*)&client_addr, &client_len);
		if (fd >= 0) {
			// Set non-blocking
			int flags = fcntl(fd, F_GETFL, 0);
			fcntl(fd, F_SETFL, flags | O_NONBLOCK);
			g_cdp_client_fd = fd;
			g_cdp_recv_len = 0;
			printf("[CDP] Client connected\n");
			fflush(stdout);
		}
	}

	if (g_cdp_client_fd < 0) return;

	// [FL-4131] Drain queued CDP commands in one frame. The editor may idle
	// without another SDL event, so proof clients cannot rely on one render
	// frame per request.
	for (int cdp_reads = 0; cdp_reads < 64 && g_cdp_client_fd >= 0; cdp_reads++) {
		int avail = (int)sizeof(g_cdp_recv_buf) - g_cdp_recv_len - 1;
		if (avail > 0) {
			int n = (int)recv(g_cdp_client_fd, g_cdp_recv_buf + g_cdp_recv_len, avail, 0);
			if (n == 0) {
				printf("[CDP] Client disconnected\n");
				fflush(stdout);
				close(g_cdp_client_fd);
				g_cdp_client_fd = -1;
				g_cdp_recv_len = 0;
				return;
			}
			if (n > 0) {
				g_cdp_recv_len += n;
				g_cdp_recv_buf[g_cdp_recv_len] = 0;
			}
			if (n < 0 && !(errno == EAGAIN || errno == EWOULDBLOCK))
				break;
		}

		bool processed = false;
		while (g_cdp_recv_len > 0) {
			char* nl = (char*)memchr(g_cdp_recv_buf, '\n', g_cdp_recv_len);
			if (!nl) break;

			*nl = 0;
			int msg_len = (int)(nl - g_cdp_recv_buf);

			if (msg_len > 0) {
				CdpProcessMessage(g_cdp_recv_buf);
				processed = true;
			}

			int remaining = g_cdp_recv_len - msg_len - 1;
			if (remaining > 0)
				memmove(g_cdp_recv_buf, nl + 1, remaining);
			g_cdp_recv_len = remaining;
		}

		if (!processed && avail > 0)
			break;
	}
}

static void CdpShutdown()
{
	if (g_cdp_client_fd >= 0) { close(g_cdp_client_fd); g_cdp_client_fd = -1; }
	if (g_cdp_listen_fd >= 0) { close(g_cdp_listen_fd); g_cdp_listen_fd = -1; }
}
#endif // !_WIN32

#include "term.h"

#include "render.h"
// FL-4131 Phase 4: extended glyph picker browses GlyphManifest.entries
#include "glyph_manifest.h"
// game.h included earlier (before compatibility macros) — do not re-include
#include "enemygen.h"
#include "weather.h"

// Editor builds link game.cpp for shared data structures and helpers, but they
// do not own a runtime Game instance or timestamp callback like the app/server.
Game* game = 0;
uint64_t (*MakeStamp)() = 0;

char base_path[1024] = "./";
Sprite* enemygen_sprite = 0;

// Markers — ephemeral coordinate markers for area operations (not saved to .a3d)
struct Marker {
    Marker* next;
    Marker* prev;
    float pos[3];
    int id;
};
static Marker* marker_head = 0;
static Marker* marker_tail = 0;
static int marker_next_id = 1;

static void FreeMarkers()
{
    Marker* mk = marker_head;
    while (mk)
    {
        Marker* next = mk->next;
        free(mk);
        mk = next;
    }
    marker_head = 0;
    marker_tail = 0;
    marker_next_id = 1;
}

extern Sprite* player_nude;

void akAPI_Exec(const char* str, int len, bool root)
{
}

void Buzz()
{
}

extern "C" void SyncConf()
{
}

extern "C" const char* GetConfPath()
{
	// USER_DIR
    return "asciicker.cfg";
}

bool Server::Send(const uint8_t* ptr, int size)
{
	return false;
}

void Server::Proc()
{
}

void Server::Log(const char* str)
{
}

// just for write(fd)
#ifndef _WIN32
#include <unistd.h>
#endif

#if 0
A3D_VT* term = 0;
#endif

#define MOUSE_QUEUE

#ifdef MOUSE_QUEUE
// Mouse input queue for high-frequency mouse events
// Allows buffering mouse movements to prevent loss at high sample rates
struct MouseQueue
{
	int x, y;           // Screen coordinates
	MouseInfo mi;       // Button states and modifiers
};

int mouse_queue_len=0;
const int mouse_queue_size = 256; // Buffer size - handles up to 15K samples/sec
MouseQueue mouse_queue[mouse_queue_size];
#endif

static bool IsMouseMoveEvent(MouseInfo mi)
{
	return (mi & 0xF) == MouseInfo::MOVE;
}

// Global UI state
ImFont* pFont = 0;                      // Custom font for ImGui (if loaded)
char ini_path[4096];                     // Path to ImGui settings file

// Core editor data structures
Terrain* terrain = 0;                    // Current terrain (height map + materials)
World* world = 0;                        // Current world (meshes, sprites, instances)
Mesh* active_mesh = 0;                   // Currently selected mesh for placement

void EditorTerrainOverviewMarkPatchDirty(Patch* p);
void EditorTerrainOverviewMarkTerrainTopologyDirty(Terrain* t);

static void EditorUpdateTerrainHeightMap(Patch* p)
{
	::UpdateTerrainHeightMap(p);
	EditorTerrainOverviewMarkPatchDirty(p);
}

static void EditorUpdateTerrainVisualMap(Patch* p)
{
	::UpdateTerrainVisualMap(p);
	EditorTerrainOverviewMarkPatchDirty(p);
}

static void EditorSetTerrainDiag(Patch* p, uint16_t diag)
{
	::SetTerrainDiag(p, diag);
	EditorTerrainOverviewMarkPatchDirty(p);
}

#define UpdateTerrainHeightMap(p) EditorUpdateTerrainHeightMap(p)
#define UpdateTerrainVisualMap(p) EditorUpdateTerrainVisualMap(p)
#define SetTerrainDiag(p, diag) EditorSetTerrainDiag(p, diag)

static bool g_explicit_mesh_placement_mode = false;

static bool g_fl3714_mesh_scan_active = false;
static int g_fl3714_mesh_scan_files = 0;
static int g_fl3714_mesh_scan_skipped = 0;
static int g_fl3714_mesh_scan_existing = 0;
static int g_fl3714_mesh_scan_loaded = 0;
static int g_fl3714_mesh_scan_failed = 0;
static uint64_t g_fl3714_mesh_scan_load_us = 0;
static uint64_t g_fl3714_last_render_log_us = 0;
static bool g_fl3714_drag_probe_active = false;
static int g_fl3714_drag_probe_raw_events = 0;
static int g_fl3714_drag_probe_queue_start = 0;
static int g_fl3714_drag_probe_consumed = 0;
static int g_fl3714_drag_probe_frames = 0;
static bool g_fl3714_drag_probe_require_consumed = false;
static uint64_t g_fl3714_drag_probe_start_us = 0;
static uint64_t g_fl3714_drag_probe_mouse_queue_us = 0;
static const uint64_t kFL3714DragProbeStaleUs = 30000000;
static const int kFL3714DragProbeStaleFrames = 600;
static const int kFL3714DragProbeMaxCoord = 1000000;

static uint64_t FL3714Now()
{
	return a3dGetTime();
}

static void FL3714Stage(const char* prefix, const char* stage, uint64_t t0)
{
	const uint64_t now = FL3714Now();
	printf("%s [FL-3714] stage=%s elapsed_ms=%.3f\n",
		prefix ? prefix : "[EDITOR]",
		stage,
		(double)(now - t0) / 1000.0);
	fflush(stdout);
}

static void FL3714Mark(const char* prefix, const char* stage)
{
	printf("%s [FL-3714] stage=%s\n", prefix ? prefix : "[EDITOR]", stage);
	fflush(stdout);
}

static void FL3714ResetDragProbe()
{
	g_fl3714_drag_probe_active = false;
	g_fl3714_drag_probe_raw_events = 0;
	g_fl3714_drag_probe_queue_start = 0;
	g_fl3714_drag_probe_consumed = 0;
	g_fl3714_drag_probe_frames = 0;
	g_fl3714_drag_probe_require_consumed = false;
	g_fl3714_drag_probe_start_us = 0;
	g_fl3714_drag_probe_mouse_queue_us = 0;
}

static bool FL3714DragProbeInputOk(int x0, int y0, int x1, int y1)
{
	return x0 >= -kFL3714DragProbeMaxCoord && x0 <= kFL3714DragProbeMaxCoord &&
		y0 >= -kFL3714DragProbeMaxCoord && y0 <= kFL3714DragProbeMaxCoord &&
		x1 >= -kFL3714DragProbeMaxCoord && x1 <= kFL3714DragProbeMaxCoord &&
		y1 >= -kFL3714DragProbeMaxCoord && y1 <= kFL3714DragProbeMaxCoord;
}

static bool FL3714ClearStaleDragProbeIfNeeded()
{
	if (!g_fl3714_drag_probe_active)
		return false;
	const uint64_t elapsed_us = g_fl3714_drag_probe_start_us ? FL3714Now() - g_fl3714_drag_probe_start_us : 0;
	if (elapsed_us <= kFL3714DragProbeStaleUs)
		return false;
	printf("[MCP] DragProbeResult: error=stale_probe_reset raw_events=%d queue_start=%d consumed=%d frames=%d queue_remaining=%d mouse_queue_ms=%.3f total_ms=%.3f\n",
		g_fl3714_drag_probe_raw_events,
		g_fl3714_drag_probe_queue_start,
		g_fl3714_drag_probe_consumed,
		g_fl3714_drag_probe_frames,
		mouse_queue_len,
		g_fl3714_drag_probe_mouse_queue_us / 1000.0,
		elapsed_us / 1000.0);
	fflush(stdout);
	FL3714ResetDragProbe();
	return true;
}

void my_mouse(A3D_WND* wnd, int x, int y, MouseInfo mi);
extern A3D_WND* wnd_head;
extern "C" int a3dQueueSdlMouseDragProbe(int x0, int y0, int x1, int y1, int steps) __attribute__((weak));
Sprite* active_sprite = 0;               // Currently selected sprite for placement
Sprite* item_preview_sprite = 0;         // Sprite preview for item selection
int active_item = 0;                     // Active item index in inventory
Inst* selected_inst = 0;                 // Currently frame-selected instance (for arrow nudge etc)
Inst* drag_inst = 0;                     // Currently dragged instance
bool inst_list_dirty = true;             // Invalidate INSTANCES panel cache on world mutation

struct EditorBundleItemChoice
{
	const char* label;
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t presentation_kind_id;
};

static const EditorBundleItemChoice editor_bundle_items[] =
{
	{ "Gold Hat / Default", 400, 500, 603 },
	{ "Gold Hat / Gold", 400, 501, 603 },
	{ "Gold Hat / Dark", 400, 502, 603 },
	{ "Cyan Suit / Default", 401, 500, 603 },
	{ "Cyan Suit / Gold", 401, 501, 603 },
	{ "Cyan Suit / Dark", 401, 502, 603 },
	{ "Shield / Default", 402, 500, 603 },
	{ "Shield / Gold", 402, 501, 603 },
	{ "Shield / Dark", 402, 502, 603 },
	{ "Sword", 403, 500, 603 },
	{ "Heavy Weapon", 404, 500, 603 },
	{ "Heal", 405, 500, 603 },
	{ "Big Heal", 406, 500, 603 },
	{ "Bone Loot", 407, 500, 603 },
	{ "Gem Loot", 408, 500, 603 },
};

static const int editor_bundle_item_count =
	(int)(sizeof(editor_bundle_items) / sizeof(editor_bundle_items[0]));


// Story/hover interaction state (for interactive elements)
bool hover_story_hover = false;          // Is cursor hovering over a story element?
int  hover_story_value = -1;             // Story ID of hovered element

bool g_enable_enemies = true;

// WHY: Manual sprite reload flag for F5 hotkey
// [FLOW:PIPELINE] Enables iterative sprite development without editor restart
static bool reload_sprites_requested = false;
static uint16_t g_term_skin_requested_id = 0;
// [FL-4131] Deferred flag set by the MCP OPEN_TERMPP command. my_render
// services it next frame using the live wnd/rot_yaw/pos_* scope, the same
// path the on-screen "TERM++" button uses (editor/asciiid.cpp:15923-15927).
static bool g_open_termpp_requested = false;

static void DebugProbe();
// edit_mode migrated to g_editor_state.edit_mode (FL-2785)

void DeleteAllEnemyGens();


/**
 * SpritePrefs - Preferences for sprite placement
 * Controls how sprites are instantiated when placed in the world
 */
struct SpritePrefs
{
	float yaw;          // Rotation angle around Y axis (degrees)
	int anim;           // Animation index to use
	int frame;          // Specific frame (only used if t[] are all 0)

	// Animation timing: [rep_first, rep_every_forward, rep_last, rep_every_backward]
	int t[4];           // Controls animation playback timing

	float height;       // Vertical offset above terrain when placing sprite
	                    // TODO: Add similar offset system for meshes

	// Randomization options for placed instances
	bool rand_anim;     // Randomize animation on placement
	bool rand_frame;    // Randomize starting frame on placement
	bool rand_yaw;      // Randomize rotation on placement
};

// Initialize sprite placement preferences if missing.
static void InitSpritePrefs(Sprite* s)
{
	if (!s)
		return;

	if (GetSpriteCookie(s))
		return;

	SpritePrefs* sp = (SpritePrefs*)malloc(sizeof(SpritePrefs));
	memset(sp, 0, sizeof(SpritePrefs));

	sp->anim = s->anims > 1 ? 1 : 0;
	sp->frame = 0;
	sp->yaw = 0;

	if (sp->anim)
	{
		// loops
		sp->t[0] = 0; // duplicate first frame
		sp->t[1] = 4; // duplicate every frame during fwd play
		sp->t[2] = 0; // duplicate last frame
		sp->t[3] = 0; // duplicate every frame during rev play
	}
	else
	{
		// ping pong
		sp->t[0] = 20; // duplicate first frame
		sp->t[1] = 2;  // duplicate every frame during fwd play
		sp->t[2] = 10; // duplicate last frame
		sp->t[3] = 4;  // duplicate every frame during rev play
	}

	sp->rand_anim = false;
	sp->rand_frame = false;
	sp->rand_yaw = false;

	SetSpriteCookie(s, sp);
}

// Find a loaded sprite by name (filename as stored in the sprite list).
static Sprite* FindSpriteByName(const char* name)
{
	if (!name || !name[0])
		return 0;

	char buf[256];
	Sprite* s = GetFirstSprite(false);
	while (s)
	{
		memset(buf, 0, sizeof(buf));
		GetSpriteName(s, buf, 256);
		if (strcmp(buf, name) == 0)
			return s;
		s = s->next;
	}
	return 0;
}

static void CopyClamped(char* dst, int size, const char* src)
{
	if (!dst || size <= 0)
		return;
	if (!src)
		src = "";
	strncpy(dst, src, size - 1);
	dst[size - 1] = 0;
}

static void EnableHeadlessBatchEnv()
{
#ifdef _WIN32
	_putenv_s("ASCIICKER_DISABLE_TERRAIN_TEXHEAP", "1");
#else
	setenv("ASCIICKER_DISABLE_TERRAIN_TEXHEAP", "1", 1);
#endif
}

static const char* BasenamePtr(const char* path)
{
	if (!path || !path[0])
		return "";

	const char* slash = strrchr(path, '/');
	const char* backslash = strrchr(path, '\\');
	const char* base = path;
	if (slash && slash + 1 > base)
		base = slash + 1;
	if (backslash && backslash + 1 > base)
		base = backslash + 1;
	return base;
}

static void SetCurrentMapPath(const char* path)
{
	CopyClamped(g_current_map_path, (int)sizeof(g_current_map_path), path);
}

static void ClearLastSaveMapError()
{
	g_last_save_map_error[0] = 0;
}

static void SetLastSaveMapError(const char* message)
{
	CopyClamped(g_last_save_map_error, (int)sizeof(g_last_save_map_error), message);
}

static void SetLastSaveMapErrno(const char* prefix)
{
	char message[256];
	snprintf(message, sizeof(message), "%s: %s", prefix, strerror(errno));
	SetLastSaveMapError(message);
}

static bool IsAbsolutePath(const char* path)
{
	if (!path || !path[0])
		return false;
	return path[0] == '/' || (strlen(path) > 1 && path[1] == ':');
}

static bool FileExists(const char* path)
{
	if (!path || !path[0])
		return false;
	FILE* f = fopen(path, "rb");
	if (!f)
		return false;
	fclose(f);
	return true;
}

static void SilenceViewerTerminalLogs()
{
#ifdef _WIN32
	const char* null_device = "NUL";
#else
	const char* null_device = "/dev/null";
#endif
	FILE* out = freopen(null_device, "a", stdout);
	if (out)
		setvbuf(stdout, NULL, _IONBF, 0);
	FILE* err = freopen(null_device, "a", stderr);
	if (err)
		setvbuf(stderr, NULL, _IONBF, 0);
}

static char ToLowerASCII(char ch)
{
	if (ch >= 'A' && ch <= 'Z')
		return (char)(ch - 'A' + 'a');
	return ch;
}

static bool HasSuffixNoCase(const char* text, const char* suffix)
{
	if (!text || !suffix)
		return false;

	int text_len = (int)strlen(text);
	int suffix_len = (int)strlen(suffix);
	if (text_len < suffix_len)
		return false;

	const char* tail = text + text_len - suffix_len;
	for (int i = 0; i < suffix_len; i++)
	{
		if (ToLowerASCII(tail[i]) != ToLowerASCII(suffix[i]))
			return false;
	}
	return true;
}

static bool IsSpriteXPFile(const char* name)
{
	return HasSuffixNoCase(name, ".xp");
}

static bool ResolveSpriteTarget(const char* raw, char* resolved_path, int resolved_size, char* resolved_name, int name_size)
{
	if (!raw || !raw[0] || !resolved_path || resolved_size <= 0 || !resolved_name || name_size <= 0)
		return false;

	const char* path = raw;
	if (IsAbsolutePath(raw) || FileExists(raw))
	{
		CopyClamped(resolved_path, resolved_size, raw);
		path = resolved_path;
	}
	else
	{
		snprintf(resolved_path, resolved_size, "%sassets/sprites/%s", base_path, raw);
		resolved_path[resolved_size - 1] = 0;
		path = resolved_path;
	}

	const char* base = strrchr(path, '/');
	if (!base)
		base = strrchr(path, '\\');
	base = base ? base + 1 : path;
	CopyClamped(resolved_name, name_size, base);
	return true;
}

static bool ActivateSpriteTarget(const char* raw)
{
	char resolved_path[1024];
	char resolved_name[256];
	if (!ResolveSpriteTarget(raw, resolved_path, sizeof(resolved_path), resolved_name, sizeof(resolved_name)))
		return false;

	Sprite* s = FindSpriteByName(resolved_name);
	if (!s)
		s = LoadSprite(resolved_path, resolved_name, 0, false);
	if (!s)
		return false;

	InitSpritePrefs(s);
	active_sprite = s;
	item_preview_sprite = 0;
	edit_mode = 4;
	return true;
}

static bool ComputeLoadedMapTermSpawn(float spawn_pos[3])
{
	if (!spawn_pos)
		return false;

	if (world && WorldGetPlayerStart(world, spawn_pos))
		return true;

	if (!terrain)
		return false;

	int patch_count = 0;
	Patch** patches = 0;
	GetAllTerrainPatches(terrain, &patches, &patch_count);
	if (patch_count <= 0 || !patches)
		return false;

	int min_px = INT_MAX;
	int max_px = INT_MIN;
	int min_py = INT_MAX;
	int max_py = INT_MIN;
	for (int i = 0; i < patch_count; i++)
	{
		int px = 0;
		int py = 0;
		GetTerrainPatch(terrain, patches[i], &px, &py);
		if (px < min_px) min_px = px;
		if (px > max_px) max_px = px;
		if (py < min_py) min_py = py;
		if (py > max_py) max_py = py;
	}
	free(patches);

	if (min_px == INT_MAX || min_py == INT_MAX)
		return false;

	int cx = (min_px + max_px) / 2;
	int cy = (min_py + max_py) / 2;
	Patch* center_patch = GetTerrainPatch(terrain, cx, cy);
	if (!center_patch)
		return false;

	uint16_t* hmap = GetTerrainHeightMap(center_patch);
	if (!hmap)
		return false;

	int center_height = hmap[(HEIGHT_CELLS / 2) * (HEIGHT_CELLS + 1) + HEIGHT_CELLS / 2];
	spawn_pos[0] = (float)((min_px + max_px + 1) * VISUAL_CELLS) * 0.5f;
	spawn_pos[1] = (float)((min_py + max_py + 1) * VISUAL_CELLS) * 0.5f;
	spawn_pos[2] = (float)center_height + 200.0f;
	return true;
}

static void StoreEditorViewAsMapPlayerStart(float view_x, float view_y, float view_z, float view_yaw)
{
	if (!world)
		return;
	float spawn[3] = { view_x, view_y, view_z };
	WorldSetPlayerStart(world, spawn, view_yaw, 0.0f);
}

static int ApplyTermSkinSelection(A3D_WND* wnd, float yaw, const float spawn_pos[3], uint16_t skin_id)
{
	int hot = TermApplyPlayerSkinId(skin_id);
	if (hot > 0)
		return hot;

	float spawn[3] = {spawn_pos[0], spawn_pos[1], spawn_pos[2]};
	ComputeLoadedMapTermSpawn(spawn);
	if (!TermOpen(wnd, yaw, spawn))
		return 0;
	return TermApplyPlayerSkinId(skin_id);
}

extern float pos_x, pos_y, pos_z;
extern float rot_yaw;
extern float global_lt[4];

static void RunTermMovementProbe()
{
	if (!terrain || !world)
	{
		printf("[MCP] TermMovementProbe: error=no_loaded_world\n");
		fflush(stdout);
		return;
	}

	float spawn[3] = {pos_x, pos_y, pos_z};
	float yaw = rot_yaw;
	float dir = 0.0f;
	bool used_player_start = WorldGetPlayerStart(world, spawn, &yaw, &dir);
	if (!used_player_start)
		ComputeLoadedMapTermSpawn(spawn);

	float light[4] = {global_lt[0], global_lt[1], global_lt[2], global_lt[3]};
	uint64_t start_stamp = a3dGetTime();
	Game* game = CreateGame();
	InitGame(game, 55, spawn, yaw, dir, light, start_stamp);

	const int width = 112;
	const int height = 63;
	AnsiCell* cells = (AnsiCell*)calloc((size_t)width * (size_t)height, sizeof(AnsiCell));
	if (!cells)
	{
		printf("[MCP] TermMovementProbe: error=alloc_failed\n");
		FreeGame(game);
		DeleteGame(game);
		fflush(stdout);
		return;
	}

	float before[3] = {game->player.pos[0], game->player.pos[1], game->player.pos[2]};
	game->Render(start_stamp + 16666, cells, width, height);
	game->OnKeyb(GAME_KEYB::KEYB_DOWN, A3D_W);
	for (int i = 0; i < 90; i++)
		game->Render(start_stamp + 33332 + (uint64_t)i * 16666ull, cells, width, height);
	game->OnKeyb(GAME_KEYB::KEYB_UP, A3D_W);
	game->Render(start_stamp + 33332 + 90ull * 16666ull, cells, width, height);

	float after[3] = {game->player.pos[0], game->player.pos[1], game->player.pos[2]};
	float dx = after[0] - before[0];
	float dy = after[1] - before[1];
	float dz = after[2] - before[2];
	printf("[MCP] TermMovementProbe: used_player_start=%d spawn=(%.3f,%.3f,%.3f) before=(%.3f,%.3f,%.3f) after=(%.3f,%.3f,%.3f) delta=(%.3f,%.3f,%.3f) moved_xy=%.3f main_menu=%d menu_depth=%d talk_box=%d grounded=%d sprite=%d player_inst=%d force=(%.3f,%.3f) applied=(%.3f,%.3f) skin=%u render_layers=%d compose_stage=%u selector_failure=%u\n",
		used_player_start ? 1 : 0,
		spawn[0], spawn[1], spawn[2],
		before[0], before[1], before[2],
		after[0], after[1], after[2],
		dx, dy, dz, sqrtf(dx * dx + dy * dy),
		game->ui.main_menu ? 1 : 0,
		game->ui.menu_depth,
		game->player.talk_box ? 1 : 0,
		game->debug.dbg_local_grounded,
		game->player.sprite ? 1 : 0,
		game->player.player_inst ? 1 : 0,
		game->debug.dbg_io_x_force,
		game->debug.dbg_io_y_force,
		game->debug.dbg_io_x_force_applied,
		game->debug.dbg_io_y_force_applied,
		(unsigned)game->player.appearance_v2.skin_definition_id,
		game->debug.dbg_actor_render_layer_count,
		(unsigned)game->debug.dbg_actor_render_compose_failure_stage,
		(unsigned)game->player.presentation_selector_failure_reason);
	free(cells);
	FreeGame(game);
	DeleteGame(game);
	fflush(stdout);
}

static void RunEditorDragProbe(char* line)
{
	FL3714ClearStaleDragProbeIfNeeded();
	if (g_fl3714_drag_probe_active)
	{
		printf("[MCP] DragProbe: error=probe_already_active\n");
		fflush(stdout);
		return;
	}
	if (!terrain)
	{
		printf("[MCP] DragProbe: error=no_loaded_terrain\n");
		fflush(stdout);
		return;
	}

	int x0 = 100, y0 = 100, x1 = 900, y1 = 700, steps = 1000;
	if (sscanf(line, "%*s %d %d %d %d %d", &x0, &y0, &x1, &y1, &steps) < 5)
	{
		printf("[MCP] Error: RUN_MOUSE_DRAG_PROBE requires x0 y0 x1 y1 steps\n");
		fflush(stdout);
		return;
	}
	if (!FL3714DragProbeInputOk(x0, y0, x1, y1))
	{
		printf("[MCP] Error: RUN_MOUSE_DRAG_PROBE coordinates out of bounds max_abs=%d\n", kFL3714DragProbeMaxCoord);
		fflush(stdout);
		return;
	}
	if (steps < 1)
		steps = 1;
	if (steps > 20000)
		steps = 20000;

	g_fl3714_drag_probe_active = true;
	g_fl3714_drag_probe_raw_events = steps + 3;
	g_fl3714_drag_probe_queue_start = mouse_queue_len;
	g_fl3714_drag_probe_consumed = 0;
	g_fl3714_drag_probe_frames = 0;
	g_fl3714_drag_probe_require_consumed = false;
	g_fl3714_drag_probe_mouse_queue_us = 0;
	g_fl3714_drag_probe_start_us = FL3714Now();

	my_mouse(wnd_head, x0, y0, (MouseInfo)(MouseInfo::MOVE | MouseInfo::INSIDE));
	my_mouse(wnd_head, x0, y0, (MouseInfo)(MouseInfo::LEFT_DN | MouseInfo::INSIDE));
	for (int i = 1; i <= steps; i++)
	{
		int x = x0 + (int)((int64_t)(x1 - x0) * i / steps);
		int y = y0 + (int)((int64_t)(y1 - y0) * i / steps);
		my_mouse(wnd_head, x, y, (MouseInfo)(MouseInfo::MOVE | MouseInfo::LEFT | MouseInfo::INSIDE));
	}
	my_mouse(wnd_head, x1, y1, (MouseInfo)(MouseInfo::LEFT_UP | MouseInfo::INSIDE));

	printf("[MCP] DragProbe: queued raw_events=%d queue_before=%d queue_after=%d start=(%d,%d) end=(%d,%d) steps=%d\n",
		g_fl3714_drag_probe_raw_events,
		g_fl3714_drag_probe_queue_start,
		mouse_queue_len,
		x0, y0, x1, y1, steps);
	fflush(stdout);
}

static void RunSdlEditorDragProbe(char* line)
{
	FL3714ClearStaleDragProbeIfNeeded();
	if (g_fl3714_drag_probe_active)
	{
		printf("[MCP] SdlDragProbe: error=probe_already_active\n");
		fflush(stdout);
		return;
	}
	if (!terrain)
	{
		printf("[MCP] SdlDragProbe: error=no_loaded_terrain\n");
		fflush(stdout);
		return;
	}
	if (!a3dQueueSdlMouseDragProbe)
	{
		printf("[MCP] SdlDragProbe: error=sdl_backend_unavailable\n");
		fflush(stdout);
		return;
	}

	int x0 = 100, y0 = 100, x1 = 900, y1 = 700, steps = 1000;
	if (sscanf(line, "%*s %d %d %d %d %d", &x0, &y0, &x1, &y1, &steps) < 5)
	{
		printf("[MCP] Error: RUN_SDL_MOUSE_DRAG_PROBE requires x0 y0 x1 y1 steps\n");
		fflush(stdout);
		return;
	}
	if (!FL3714DragProbeInputOk(x0, y0, x1, y1))
	{
		printf("[MCP] Error: RUN_SDL_MOUSE_DRAG_PROBE coordinates out of bounds max_abs=%d\n", kFL3714DragProbeMaxCoord);
		fflush(stdout);
		return;
	}
	if (steps < 1)
		steps = 1;
	if (steps > 20000)
		steps = 20000;

	g_fl3714_drag_probe_active = true;
	g_fl3714_drag_probe_raw_events = steps + 3;
	g_fl3714_drag_probe_queue_start = mouse_queue_len;
	g_fl3714_drag_probe_consumed = 0;
	g_fl3714_drag_probe_frames = 0;
	g_fl3714_drag_probe_require_consumed = true;
	g_fl3714_drag_probe_mouse_queue_us = 0;
	g_fl3714_drag_probe_start_us = FL3714Now();

	int queued = a3dQueueSdlMouseDragProbe(x0, y0, x1, y1, steps);
	printf("[MCP] SdlDragProbe: queued_sdl_events=%d raw_events=%d queue_before=%d start=(%d,%d) end=(%d,%d) steps=%d\n",
		queued,
		g_fl3714_drag_probe_raw_events,
		g_fl3714_drag_probe_queue_start,
		x0, y0, x1, y1, steps);
	fflush(stdout);
}

// Apply currently selected editor sprite to all player mount slots
// (human/wolf/bee mounts, no armor/helmet/shield, all weapon states).

/**
 * MeshPrefs - Preferences for mesh placement
 * Controls transformations applied when placing 3D meshes in the world
 */
struct MeshPrefs
{
	// Scale transformations
	float scale_val[3];        // Base scale [X, Y, Z]
	float scale_rnd[3];        // Random scale variation [X, Y, Z]

	// Rotation around local Z axis (up/down in mesh space)
	float rotate_locZ_val;     // Base rotation value
	float rotate_locZ_rnd;     // Random rotation variation

	// Rotation around X and Y axes
	float rotate_XY_val[2];    // Base rotation [X, Y]
	float rotate_XY_rnd[2];    // Random rotation variation [X, Y]

	// Terrain alignment
	float rotate_align;        // How much to align mesh to terrain normal (0-1)

	// Height offset
	float height;              // Vertical offset above terrain when placing mesh

	// Disabled options (kept for reference):
	// float pre_trans[3];     // Pre-transformation translation
	// float translate_val[3]; // Translation offset
	// float translate_rnd[3]; // Random translation
};

/**
 * Merge - System for merging external map files into current map
 * Allows importing terrain and world data from another .bin file
 * and combining it with the current map at a specified offset
 */
struct Merge
{
	Terrain* _terrain;  // Terrain data being merged from external file
	World* _world;      // World data being merged from external file

	// WHY max-height merge strategy:
	// CommitPatch combines terrain from a source map into the current terrain.
	// For each cell, it takes the MAXIMUM height from source and destination,
	// preserving the tallest terrain features from both maps. This prevents
	// merge operations from lowering existing terrain. Creates patches on-demand
	// if destination doesn't have a patch at the source patch's location.
	/**
	 * CommitPatch - Callback to merge a terrain patch
	 * Combines height map data from source patch into destination patch
	 * Creates new patches if they don't exist in target terrain
	 */
	static void CommitPatch(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		Merge* mrg = (Merge*)cookie;
		mrg->patches_merged++;

		Patch* d = GetTerrainPatch(terrain, x / VISUAL_CELLS, y / VISUAL_CELLS);

		uint16_t diag = 0;

		if (!d)
		{
			// d = AddTerrainPatch(terrain, x / VISUAL_CELLS, y / VISUAL_CELLS, 0);
			d = URDO_Create(terrain, x / VISUAL_CELLS, y / VISUAL_CELLS, 0);
			URDO_Patch(d, true);
			uint16_t* src = GetTerrainVisualMap(p);
			uint16_t* dst = GetTerrainVisualMap(d);
			memcpy(dst, src, sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);
			UpdateTerrainVisualMap(d);
			diag = 1;
		}
		else
		{
			URDO_Patch(d, false);
		}

		uint16_t* src = GetTerrainHeightMap(p);
		uint16_t* dst = GetTerrainHeightMap(d);

		for (int i = 0, y = 0; y < HEIGHT_CELLS + 1; y++)
		{
			for (int x = 0; x < HEIGHT_CELLS + 1; x++,i++)
			{
				if (src[i] > dst[i])
				{
					dst[i] = src[i];
				}
			}
		}

		UpdateTerrainHeightMap(d);

		if (diag)
		{
			URDO_Diag(d);
			diag = GetTerrainDiag(p);
			SetTerrainDiag(d, diag);
		}
	}

	static void CommitSprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie)
	{
		assert(0);
	}

	// WHY name-based mesh matching and translation by dx*VISUAL_CELLS:
	// CommitMesh copies mesh instances from source map to destination map during
	// merge operations. Meshes are matched by name (e.g., "Tree.akm" in source
	// must have "Tree" mesh loaded in destination). The transform matrix is
	// translated by (dx, dy) offset to position merged content at cursor location.
	// dx*VISUAL_CELLS converts patch coordinates to world coordinates.
	static void CommitMesh(Inst* i, Mesh* m, double tm[16], void* cookie)
	{
		Merge* mrg = (Merge*)cookie;
		mrg->instances_added++;

		double ttm[16];
		memcpy(ttm, tm, sizeof(double) * 16);
		ttm[12] += mrg->dx * VISUAL_CELLS;
		ttm[13] += mrg->dy * VISUAL_CELLS;

		char mesh_name[256];
		GetMeshName(m, mesh_name, 256);
		int flags = INST_USE_TREE | INST_VISIBLE;

		Mesh* m2 = GetFirstMesh(world);
		while (m2)
		{
			char mesh_name2[256];
			GetMeshName(m2, mesh_name2, 256);
			if (strcmp(mesh_name, mesh_name2) == 0)
			{
				//CreateInst(m2, flags, ttm, 0);
				URDO_Create(m2, flags, ttm, -1/*dont merge story_id*/);
				break;
			}

			m2 = GetNextMesh(m2);
		}
	}

	int dx,dy;

	// todo:
	bool flip_x;
	bool flip_y;
	bool swap_xy;

	// MCP merge statistics (reset before commit, incremented by callbacks)
	int patches_merged;
	int instances_added;
};

Merge merge = { 0,0 };
float pos_x = 0, pos_y = 0, pos_z = 0;
extern float rot_yaw;

void MergeCancel()
{
	if (merge._terrain)
		DeleteTerrain(merge._terrain);
	merge._terrain = 0;

	if (merge._world)
		DeleteWorld(merge._world);
	merge._world = 0;
}

static bool MeshScan(A3D_DirItem item, const char* name, void* cookie);

// WHY skip materials on read and reload mesh geometry:
// MergeOpen loads a .a3d map file for merging into current map. It reads
// terrain and world data but SKIPS material definitions (current map's materials
// are preserved). After loading, mesh geometry is reloaded from .akm files
// (Blender-exported) to ensure source map's mesh instances reference valid
// geometry in destination editor.
void MergeOpen(const char* path)
{
	assert(!merge._terrain && !merge._world);

	// URDO_Purge();

	FILE* f = fopen(path, "rb");
	if (f)
	{
		merge._terrain = LoadTerrain(f);

		if (merge._terrain)
		{
			// skip mats
			for (int i = 0; i < 256; i++)
			{
				MatCell skip[64];
				if (fread(skip, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
					break;
			}

			merge._world = LoadWorldForEditor(f);

			if (merge._world)
			{
				// reload meshes too
				Mesh* m = GetFirstMesh(merge._world);

				while (m)
				{
					char mesh_name[256];
					GetMeshName(m, mesh_name, 256);
					char obj_path[4096];
					ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
					// [DEPENDENCY:BLENDER] Reload mesh geometry from .akm file (may have been re-exported from Blender since last save).
					if (!UpdateMesh(m, obj_path))
					{
						// what now?
						// missing mesh file!
					}

					MeshPrefs* mp = (MeshPrefs*)malloc(sizeof(MeshPrefs));
					memset(mp, 0, sizeof(MeshPrefs));
					SetMeshCookie(m, mp);

					m = GetNextMesh(m);
				}
			}
		}

		fclose(f);
	}

	if (!merge._terrain)
		merge._terrain = CreateTerrain();

	if (!merge._world)
		merge._world = CreateWorld();

	RebuildWorld(merge._world, true);
}

// WHY URDO_Open/Close wraps entire merge operation:
// MergeCommit applies the loaded merge data to current terrain+world. It's
// wrapped in URDO_Open/Close so the entire merge (potentially 100s of patch
// creates, mesh placements) becomes a single undo unit. User can undo the
// entire merge with one Ctrl+Z instead of undoing each individual change.
void MergeCommit()
{
	URDO_Open();

	merge.dx = (int)floor(pos_x / VISUAL_CELLS + 0.5);
	merge.dy = (int)floor(pos_y / VISUAL_CELLS + 0.5);

	if (merge._terrain)
	{
		int t[2];
		GetTerrainBase(merge._terrain, t);
		int o[2] = { t[0] - merge.dx, t[1] - merge.dy };
		SetTerrainBase(merge._terrain, o);
		QueryTerrain(merge._terrain, 0, 0, 0xAA, Merge::CommitPatch, &merge);
	}

	if (merge._world)
	{
		QueryWorldCB cb = { Merge::CommitMesh, Merge::CommitSprite };
		QueryWorld(merge._world, 0, 0, &cb, &merge);
		RebuildWorld(world, false);
	}


	URDO_Close();

	MergeCancel();

}

// ============================================================================
// MATERIAL SYSTEM
// ============================================================================
// The material system defines how terrain is rendered using ASCII characters.
//
// Key concepts:
// - 256 total materials (IDs 0-255)
// - Each material has 4 "elevation ramps" (for different vertical slopes)
// - Each ramp has 16 "shade levels" (for lighting/shadows)
// - Each shade entry defines: background color, foreground color, and ASCII glyph
//
// Material ID 0 = Water (defined explicitly)
// Material IDs 1-255 = Random colors (generated at startup for testing/placeholders)
//
// The terrain stores a material ID per cell, which is used to look up the
// rendering properties from the material array during rendering.
// ============================================================================

int fonts_loaded = 0;
int palettes_loaded = 0;
GLuint pal_tex = 0;
uint8_t* ipal = 0;

void* GetMaterialArr();
void* GetPaletteArr();
void* GetFontArr();

static bool LoadMaterialsFromA3D(const char* path, Material* mats)
{
	FILE* f = fopen(path, "rb");
	if (!f)
		return false;

	unsigned char sig[4];
	if (fread(sig, 1, 4, f) != 4 || memcmp(sig, "AS3D", 4) != 0)
	{
		fclose(f);
		return false;
	}

	uint32_t header_size = 0;
	uint32_t num_patches = 0;
	uint32_t reserved = 0;
	if (fread(&header_size, 4, 1, f) != 1 ||
		fread(&num_patches, 4, 1, f) != 1 ||
		fread(&reserved, 4, 1, f) != 1)
	{
		fclose(f);
		return false;
	}

	const size_t patch_size = 8 + VISUAL_CELLS * VISUAL_CELLS * 2 +
		(HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1) * 2 + 2;
	const long offset = (long)header_size + (long)num_patches * (long)patch_size;
	if (fseek(f, offset, SEEK_SET) != 0)
	{
		fclose(f);
		return false;
	}

	for (int i = 0; i < 256; i++)
	{
		if (fread(mats[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
		{
			fclose(f);
			return false;
		}
	}

	fclose(f);
	return true;
}

static bool LoadMaterialDefaults(Material* mats)
{
	const char* candidates[] =
	{
		"assets/a3d/game_map_y8_original_game_map.a3d",
		"assets/a3d/game_map_y8.a3d",
		"assets/a3d/game_map_y7.a3d",
	};

	char path[4096];
	for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++)
	{
		snprintf(path, sizeof(path), "%s%s", base_path, candidates[i]);
		path[sizeof(path) - 1] = 0;
		printf("[Material] Trying to load defaults from: %s\n", path);
		if (LoadMaterialsFromA3D(path, mats)) {
			printf("[Material] SUCCESS loading defaults from %s\n", path);
			return true;
		}
	}
	printf("[Material] FAILED to load any default materials.\n");
	return false;
}

/**
 * MyMaterial - Material definition for ASCII terrain rendering
 * Extends base Material struct with OpenGL texture management
 */
struct MyMaterial : Material
{
	static void Free()
	{
		glDeleteTextures(1,&tex);
	}

	// WHY 256 materials with 4 elevation ramps x 16 shade levels:
	// The material system provides visual variety for terrain rendering.
	// Each material has 4 elevation bands (valley, lowland, midland, highland)
	// and 16 shade levels (based on slope/lighting). This gives 64 color
	// variations per material, enabling smooth terrain appearance transitions.
	// Material 0 (water) and others (grass, dirt, stone, sand, snow, mud, etc.)
	// are initialized with default color schemes for rapid map creation.
	/**
	 * Init - Initialize all 256 materials with default values
	 * Called once at editor startup
	 */
	static void Init()
	{
		MyMaterial* m = (MyMaterial*)GetMaterialArr();
		printf("[Material] Initializing materials...\n");
		const bool loaded_defaults = LoadMaterialDefaults(m);

		// DEBUG: Print populated slots
		printf("[Material] Populated IDs from Default Map:\n");
		for (int i=1; i<256; i++) {
			if (m[i].shade[0][0].bg[0] != 0 || m[i].shade[0][0].bg[1] != 0 || m[i].shade[0][0].bg[2] != 0) {
				printf("%d ", i);
			}
		}
		printf("\n");

		// Leave material definitions intact; bake allocator will reuse unused IDs.

		if (!loaded_defaults)
		{
		// ====================================================================
		// MATERIAL 0: WATER (explicitly defined)
		// ====================================================================
		// This is the default/water material with specific glyphs and colors
		// Glyphs: ',' ' ' '!' ' ' for 4 elevation ramps
		// Foreground: Grayscale gradient (bright to dark)
		// Background: Light gray (0xCF = 207)

		uint8_t g[4] = {',',' ','!',' '};  // ASCII glyphs for each ramp
		uint8_t f[4] = {0xFF,0xA0,0x64,0x00};  // Foreground brightness levels

		for (int s=0; s<16; s++)  // 16 shade levels
		{
			for (int r=0; r<4; r++)  // 4 elevation ramps
			{
				// Set grayscale foreground color
				m[0].shade[r][s].fg[0]=f[r];
				m[0].shade[r][s].fg[1]=f[r];
				m[0].shade[r][s].fg[2]=f[r];

				// Set ASCII glyph for this ramp
				m[0].shade[r][s].gl = g[r];

				// Set light gray background
				m[0].shade[r][s].bg[0]=0xCF;
				m[0].shade[r][s].bg[1]=0xCF;
				m[0].shade[r][s].bg[2]=0xCF;

				m[0].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 1: GRASS (explicitly defined for playable area)
		// ====================================================================
		// This is the main terrain material for the playable area
		// Green color palette with grass-like ASCII characters

		// Grass color palette - various shades of green
		uint8_t grass_bg_base[3] = {34, 139, 34};      // Forest green base
		uint8_t grass_fg_base[3] = {144, 238, 144};    // Light green foreground
		uint8_t grass_glyphs[4] = {'"', '\'', '"', '`'}; // Grass characters for ramps

		for (int r = 0; r < 4; r++)  // For each elevation ramp
		{
			for (int s = 0; s < 16; s++)  // For each shade level
			{
				// Shade factor: 0=bright (1.0), 15=dark (0.4)
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				// Background: darker green shades
				m[1].shade[r][s].bg[0] = (uint8_t)(grass_bg_base[0] * shade_factor);
				m[1].shade[r][s].bg[1] = (uint8_t)(grass_bg_base[1] * shade_factor);
				m[1].shade[r][s].bg[2] = (uint8_t)(grass_bg_base[2] * shade_factor);

				// Foreground: lighter green shades
				m[1].shade[r][s].fg[0] = (uint8_t)(grass_fg_base[0] * shade_factor);
				m[1].shade[r][s].fg[1] = (uint8_t)(grass_fg_base[1] * shade_factor);
				m[1].shade[r][s].fg[2] = (uint8_t)(grass_fg_base[2] * shade_factor);

				// Grass glyph varies by elevation ramp
				m[1].shade[r][s].gl = grass_glyphs[r];

				m[1].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 2: DIRT (brown soil)
		// ====================================================================
		uint8_t dirt_bg_base[3] = {101, 67, 33};      // Saddle brown
		uint8_t dirt_fg_base[3] = {160, 120, 80};     // Lighter brown
		uint8_t dirt_glyphs[4] = {'.', ':', ',', '\''};  // Dirt textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				m[2].shade[r][s].bg[0] = (uint8_t)(dirt_bg_base[0] * shade_factor);
				m[2].shade[r][s].bg[1] = (uint8_t)(dirt_bg_base[1] * shade_factor);
				m[2].shade[r][s].bg[2] = (uint8_t)(dirt_bg_base[2] * shade_factor);

				m[2].shade[r][s].fg[0] = (uint8_t)(dirt_fg_base[0] * shade_factor);
				m[2].shade[r][s].fg[1] = (uint8_t)(dirt_fg_base[1] * shade_factor);
				m[2].shade[r][s].fg[2] = (uint8_t)(dirt_fg_base[2] * shade_factor);

				m[2].shade[r][s].gl = dirt_glyphs[r];
				m[2].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 3: STONE (gray rock)
		// ====================================================================
		uint8_t stone_bg_base[3] = {105, 105, 105};   // Dim gray
		uint8_t stone_fg_base[3] = {169, 169, 169};   // Dark gray (lighter)
		uint8_t stone_glyphs[4] = {'#', 'O', '8', '@'};  // Rock textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				m[3].shade[r][s].bg[0] = (uint8_t)(stone_bg_base[0] * shade_factor);
				m[3].shade[r][s].bg[1] = (uint8_t)(stone_bg_base[1] * shade_factor);
				m[3].shade[r][s].bg[2] = (uint8_t)(stone_bg_base[2] * shade_factor);

				m[3].shade[r][s].fg[0] = (uint8_t)(stone_fg_base[0] * shade_factor);
				m[3].shade[r][s].fg[1] = (uint8_t)(stone_fg_base[1] * shade_factor);
				m[3].shade[r][s].fg[2] = (uint8_t)(stone_fg_base[2] * shade_factor);

				m[3].shade[r][s].gl = stone_glyphs[r];
				m[3].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 4: SAND (tan/beige)
		// ====================================================================
		uint8_t sand_bg_base[3] = {194, 178, 128};    // Tan
		uint8_t sand_fg_base[3] = {238, 232, 170};    // Pale goldenrod
		uint8_t sand_glyphs[4] = {' ', '.', ':', ','};  // Sandy textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				m[4].shade[r][s].bg[0] = (uint8_t)(sand_bg_base[0] * shade_factor);
				m[4].shade[r][s].bg[1] = (uint8_t)(sand_bg_base[1] * shade_factor);
				m[4].shade[r][s].bg[2] = (uint8_t)(sand_bg_base[2] * shade_factor);

				m[4].shade[r][s].fg[0] = (uint8_t)(sand_fg_base[0] * shade_factor);
				m[4].shade[r][s].fg[1] = (uint8_t)(sand_fg_base[1] * shade_factor);
				m[4].shade[r][s].fg[2] = (uint8_t)(sand_fg_base[2] * shade_factor);

				m[4].shade[r][s].gl = sand_glyphs[r];
				m[4].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 5: SNOW (white/light blue)
		// ====================================================================
		uint8_t snow_bg_base[3] = {230, 240, 255};    // Very light blue-white
		uint8_t snow_fg_base[3] = {255, 255, 255};    // Pure white
		uint8_t snow_glyphs[4] = {'*', '+', '.', ' '};  // Snowy textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.5f;  // Less darkening for snow

				m[5].shade[r][s].bg[0] = (uint8_t)(snow_bg_base[0] * shade_factor);
				m[5].shade[r][s].bg[1] = (uint8_t)(snow_bg_base[1] * shade_factor);
				m[5].shade[r][s].bg[2] = (uint8_t)(snow_bg_base[2] * shade_factor);

				m[5].shade[r][s].fg[0] = (uint8_t)(snow_fg_base[0] * shade_factor);
				m[5].shade[r][s].fg[1] = (uint8_t)(snow_fg_base[1] * shade_factor);
				m[5].shade[r][s].fg[2] = (uint8_t)(snow_fg_base[2] * shade_factor);

				m[5].shade[r][s].gl = snow_glyphs[r];
				m[5].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 6: MUD (dark brown, wet looking)
		// ====================================================================
		uint8_t mud_bg_base[3] = {64, 46, 30};        // Very dark brown
		uint8_t mud_fg_base[3] = {96, 70, 46};        // Medium brown
		uint8_t mud_glyphs[4] = {'~', '=', '-', '.'};  // Muddy textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.7f;  // More darkening for mud

				m[6].shade[r][s].bg[0] = (uint8_t)(mud_bg_base[0] * shade_factor);
				m[6].shade[r][s].bg[1] = (uint8_t)(mud_bg_base[1] * shade_factor);
				m[6].shade[r][s].bg[2] = (uint8_t)(mud_bg_base[2] * shade_factor);

				m[6].shade[r][s].fg[0] = (uint8_t)(mud_fg_base[0] * shade_factor);
				m[6].shade[r][s].fg[1] = (uint8_t)(mud_fg_base[1] * shade_factor);
				m[6].shade[r][s].fg[2] = (uint8_t)(mud_fg_base[2] * shade_factor);

				m[6].shade[r][s].gl = mud_glyphs[r];
				m[6].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 7: COBBLESTONE (varied gray with texture)
		// ====================================================================
		uint8_t cobble_bg_base[3] = {112, 128, 144};  // Slate gray
		uint8_t cobble_fg_base[3] = {176, 196, 222};  // Light steel blue
		uint8_t cobble_glyphs[4] = {'o', 'O', '0', '@'};  // Cobble textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				m[7].shade[r][s].bg[0] = (uint8_t)(cobble_bg_base[0] * shade_factor);
				m[7].shade[r][s].bg[1] = (uint8_t)(cobble_bg_base[1] * shade_factor);
				m[7].shade[r][s].bg[2] = (uint8_t)(cobble_bg_base[2] * shade_factor);

				m[7].shade[r][s].fg[0] = (uint8_t)(cobble_fg_base[0] * shade_factor);
				m[7].shade[r][s].fg[1] = (uint8_t)(cobble_fg_base[1] * shade_factor);
				m[7].shade[r][s].fg[2] = (uint8_t)(cobble_fg_base[2] * shade_factor);

				m[7].shade[r][s].gl = cobble_glyphs[r];
				m[7].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIAL 8: GRAVEL (light gray with small stones)
		// ====================================================================
		uint8_t gravel_bg_base[3] = {150, 150, 150};  // Medium gray
		uint8_t gravel_fg_base[3] = {190, 190, 190};  // Light gray
		uint8_t gravel_glyphs[4] = {'.', ':', ';', ','};  // Small stone textures

		for (int r = 0; r < 4; r++)  // Elevation ramps
		{
			for (int s = 0; s < 16; s++)  // Shade levels
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				m[8].shade[r][s].bg[0] = (uint8_t)(gravel_bg_base[0] * shade_factor);
				m[8].shade[r][s].bg[1] = (uint8_t)(gravel_bg_base[1] * shade_factor);
				m[8].shade[r][s].bg[2] = (uint8_t)(gravel_bg_base[2] * shade_factor);

				m[8].shade[r][s].fg[0] = (uint8_t)(gravel_fg_base[0] * shade_factor);
				m[8].shade[r][s].fg[1] = (uint8_t)(gravel_fg_base[1] * shade_factor);
				m[8].shade[r][s].fg[2] = (uint8_t)(gravel_fg_base[2] * shade_factor);

				m[8].shade[r][s].gl = gravel_glyphs[r];
				m[8].shade[r][s].flags = 0;
			}
		}

		// ====================================================================
		// MATERIALS 9-255: RANDOM COLORS (placeholders)
		// ====================================================================
		// These remain random for now - can be defined later as needed
		// (additional biome types, custom materials, etc.)

			for (int i = 9; i < 256; i++)  // For each material ID
			{
				for (int r = 0; r < 4; r++)  // For each elevation ramp
				{
					for (int s = 0; s < 16; s++)  // For each shade level
				{
					// Initialize to BLACK (Empty) so allocator can use them
					m[i].shade[r][s].bg[0] = 0;
					m[i].shade[r][s].bg[1] = 0;
					m[i].shade[r][s].bg[2] = 0;

					m[i].shade[r][s].fg[0] = 0;
					m[i].shade[r][s].fg[1] = 0;
					m[i].shade[r][s].fg[2] = 0;

					m[i].shade[r][s].gl = 0;

					m[i].shade[r][s].flags = 0;
					}
				}
			}
		}

		gl3CreateTextures(GL_TEXTURE_2D, 1, &tex);

		gl3TextureStorage2D(tex, 1, GL_RGBA8UI, 128, 256);

		glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
		gl3TextureSubImage2D(tex, 0, 0, 0, 128, 256, GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, m->shade );
		glPixelStorei(GL_UNPACK_ALIGNMENT, 4);

		gl3TextureParameteri2D(tex, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(tex, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(tex, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		gl3TextureParameteri2D(tex, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	}

	void Update()
	{
		MyMaterial* m = (MyMaterial*)GetMaterialArr();
		int y = (int)(this-m);
		MatCell preview_shade[kMaterialGridRowCount][kMaterialGridColCount];
		memcpy(preview_shade, shade, sizeof(preview_shade));
		if (glyph_plane && glyph_plane->cells)
		{
			for (int row = 0; row < kMaterialGridRowCount; row++)
			{
				for (int col = 0; col < kMaterialGridColCount; col++)
				{
					const GlyphId glyph_id = material_glyph_plane_lookup(glyph_plane, row, col);
					if (!glyph_id_is_extended(glyph_id))
						continue;
					uint16_t coverage = material_glyph_plane_lookup_coverage(glyph_plane, row, col);
					if (coverage == 0)
						AsciiidManifestLookupCoverage(glyph_id, &coverage);
					MatCell* preview = &preview_shade[row][col];
					if (coverage == 0)
					{
						preview->fg[0] = preview->fg[1] = preview->fg[2] = 0;
						preview->bg[0] = 255;
						preview->bg[1] = preview->bg[2] = 0;
						preview->gl = '!';
					}
					else
					{
						preview->gl = material_glyph_plane_coverage_display_glyph(coverage);
					}
				}
			}
		}
		// update this single material texture slice !
		glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
		gl3TextureSubImage2D(tex, 0, 0, y, 128, 1, GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, preview_shade);
		glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
	}

	static GLuint tex; // single texture for all materials 128x256

	// althought we have only 16 cells, shade map has 7bits!
	// that makes timed shading 8x more precise spatialy :)
	// (last bit is left for elevation/transparency and depends on material mode)

//	int time_scale; // -80..-1 , 0 , +1..+80

	// TIMED SHADE_MAP EVALUATION:
	/*
		uint64_t time64_usec = a4dGetTime();
		int cell; // = ???
		if (time_scale == 0)
		{
			cell = (shade_map >> 3) &0xF;
		}
		else
		{
			int mul_arr[] = { 470, 431, 395, 462, 332, 304, 279, 256 };
			int abs_scale;

			int multiplier;
			if (time_scale>0)
			{
				abs_scale = time_scale;
				multiplier = mul_arr[(abs_scale+6)&7];
			}
			else
			{
				abs_scale = -time_scale;
				multiplier = -mul_arr[(abs_scale+6)&7];
			}

			int shift = 30 - ( ( abs_scale + 6 ) >> 3 );

			cell = (( time64_usec * multiplier + (shade_map << (shift-3)) ) >> shift ) & 0xF;

			// so at every frame every material should cache (time64_usec * multiplier) >> (shift-3)
			// then during shading cell is simply = ((mat_cache + shade_map) >> 3 ) & 0xF
		}
	*/
};

GLuint MyMaterial::tex = 0;

// ============================================================================
// MATERIAL ARRAY - All 256 material definitions
// ============================================================================
// mat[0] = Water (defined explicitly in MyMaterial::Init)
// mat[1-255] = Random colors (placeholders - should be replaced with real materials)
//
// Each material defines:
// - 4 elevation ramps (for different slopes: flat, gentle, steep, vertical)
// - 16 shade levels per ramp (for lighting/shadows)
// - Each entry has: background color, foreground color, ASCII glyph
//
// HOW IT WORKS:
// 1. Terrain cells store a material ID (0-255)
// 2. Renderer looks up mat[material_id] to get colors/glyphs
// 3. Elevation and lighting determine which ramp/shade to use
// 4. ASCII character is rendered with the specified colors
// ============================================================================
MyMaterial mat[256];

static bool g_editor_force_exact_terrain = false;

struct EditorOverviewVertex
{
	float xyz[3];
	float rgb[3];
};

struct EditorTerrainOverviewTile
{
	int key_x = 0;
	int key_y = 0;
	int min_patch_x = INT_MAX;
	int min_patch_y = INT_MAX;
	int max_patch_x = INT_MIN;
	int max_patch_y = INT_MIN;
	int patch_count = 0;
	uint16_t lo = 0;
	uint16_t hi = 0;
	float rgb[3] = { 0.0f, 0.0f, 0.0f };
	bool dirty = true;
	bool seeded = false;
};

struct EditorTerrainOverviewCache
{
	// [FL-3851] Keep overview tiles small enough that OSM roads, plazas, and
	// building footprints survive full-map proof captures. A 16-patch tile
	// averaged whole campus blocks into unreadable mush; 2-patch tiles still
	// bound render cost while preserving road-scale hierarchy.
	static const int kMacroPatchSpan = 2;
	static const int kMaxRenderedTiles = 16384;
	static const int kExactFocusPatchBudget = 2048;
	// FL-3714: keep cold overview cache refresh progressive during drag.
	// Refreshing 512 tiles after opening the SBU OSM map produced a 259ms
	// terrain frame even though mouse-event coalescing was bounded.
	static const int kMaxTileRefreshesPerFrame = 64;
	static const float kOverviewPatchPixelThreshold;

	Terrain* terrain_ptr = 0;
	int patch_count = -1;
	uint64_t generation = 0;
	std::vector<EditorTerrainOverviewTile> tiles;
	std::unordered_map<Patch*, int> patch_to_tile;

	int last_tiles = 0;
	int last_exact_budget = 0;
	int last_exact_rendered = 0;
	int last_dirty_remaining = 0;
	int last_refreshed_tiles = 0;
	int dirty_cursor = 0;
	bool last_used = false;

	static int FloorDiv(int a, int b)
	{
		return a >= 0 ? a / b : -((-a + b - 1) / b);
	}

	static long long Key(int x, int y)
	{
		return ((long long)x << 32) ^ (unsigned int)y;
	}

	void Reset()
	{
		terrain_ptr = 0;
		patch_count = -1;
		generation++;
		tiles.clear();
		patch_to_tile.clear();
		last_tiles = 0;
		last_exact_budget = 0;
		last_exact_rendered = 0;
		last_dirty_remaining = 0;
		last_refreshed_tiles = 0;
		dirty_cursor = 0;
	}

	void MarkPatchDirty(Patch* p)
	{
		std::unordered_map<Patch*, int>::iterator it = patch_to_tile.find(p);
		if (it != patch_to_tile.end() && it->second >= 0 && it->second < (int)tiles.size())
			tiles[it->second].dirty = true;
	}

	void MarkAllDirty()
	{
		for (size_t i = 0; i < tiles.size(); i++)
			tiles[i].dirty = true;
		dirty_cursor = 0;
	}

	void MarkTerrainTopologyDirty(Terrain* t)
	{
		if (!t || terrain_ptr == t)
			Reset();
	}

	void SeedTileFromPatch(Terrain* t, EditorTerrainOverviewTile& tile, Patch* p)
	{
		if (!t || !p)
			return;

		uint16_t plo = 0;
		uint16_t phi = 0;
		GetTerrainLimits(p, &plo, &phi);
		uint16_t* visual = GetTerrainVisualMap(p);
		int sample = (VISUAL_CELLS / 2) + (VISUAL_CELLS / 2) * VISUAL_CELLS;
		int mat_id = visual[sample] & 0xff;
		int shade = (visual[sample] >> 8) & 0x0f;
		int elev = (visual[sample] >> 15) & 0x01;
		MatCell* cell = &mat[mat_id].shade[elev ? 1 : 0][shade];
		tile.rgb[0] = (float)cell->bg[0] / 255.0f;
		tile.rgb[1] = (float)cell->bg[1] / 255.0f;
		tile.rgb[2] = (float)cell->bg[2] / 255.0f;
		tile.lo = plo;
		tile.hi = phi;
		tile.seeded = true;
	}

	void Build(Terrain* t)
	{
		Reset();
		terrain_ptr = t;
		patch_count = GetTerrainPatches(t);

		Patch** patches = 0;
		int count = 0;
		GetAllTerrainPatches(t, &patches, &count);
		if (!patches || count <= 0)
		{
			if (patches)
				free(patches);
			return;
		}

		std::unordered_map<long long, int> tile_by_key;
		for (int i = 0; i < count; i++)
		{
			int px = 0;
			int py = 0;
			GetTerrainPatch(t, patches[i], &px, &py);
			int tx = FloorDiv(px, kMacroPatchSpan);
			int ty = FloorDiv(py, kMacroPatchSpan);
			long long key = Key(tx, ty);

			int tile_idx;
			std::unordered_map<long long, int>::iterator found = tile_by_key.find(key);
			if (found == tile_by_key.end())
			{
				tile_idx = (int)tiles.size();
				tile_by_key[key] = tile_idx;
				EditorTerrainOverviewTile tile;
				tile.key_x = tx;
				tile.key_y = ty;
				tiles.push_back(tile);
			}
			else
			{
				tile_idx = found->second;
			}

			EditorTerrainOverviewTile& tile = tiles[tile_idx];
			if (px < tile.min_patch_x) tile.min_patch_x = px;
			if (py < tile.min_patch_y) tile.min_patch_y = py;
			if (px > tile.max_patch_x) tile.max_patch_x = px;
			if (py > tile.max_patch_y) tile.max_patch_y = py;
			tile.patch_count++;
			if (!tile.seeded)
				SeedTileFromPatch(t, tile, patches[i]);
			patch_to_tile[patches[i]] = tile_idx;
		}

		for (int i = 0; i < count; i++)
			MarkPatchDirty(patches[i]);
		free(patches);
	}

	void RefreshTile(Terrain* t, EditorTerrainOverviewTile& tile)
	{
		unsigned long long rgb_sum[3] = { 0, 0, 0 };
		unsigned long long samples = 0;
		uint16_t lo = 0xffff;
		uint16_t hi = 0x0000;

		for (int py = tile.min_patch_y; py <= tile.max_patch_y; py++)
		{
			for (int px = tile.min_patch_x; px <= tile.max_patch_x; px++)
			{
				Patch* p = GetTerrainPatch(t, px, py);
				if (!p)
					continue;

				uint16_t plo = 0;
				uint16_t phi = 0;
				GetTerrainLimits(p, &plo, &phi);
				if (plo < lo) lo = plo;
				if (phi > hi) hi = phi;

				uint16_t* visual = GetTerrainVisualMap(p);
				for (int i = 0; i < VISUAL_CELLS * VISUAL_CELLS; i++)
				{
					int mat_id = visual[i] & 0xff;
					int shade = (visual[i] >> 8) & 0x0f;
					int elev = (visual[i] >> 15) & 0x01;
					MatCell* cell = &mat[mat_id].shade[elev ? 1 : 0][shade];
					rgb_sum[0] += cell->bg[0];
					rgb_sum[1] += cell->bg[1];
					rgb_sum[2] += cell->bg[2];
					samples++;
				}
			}
		}

		if (!samples)
		{
			tile.rgb[0] = tile.rgb[1] = tile.rgb[2] = 0.0f;
			tile.lo = tile.hi = 0;
		}
		else
		{
			tile.rgb[0] = (float)rgb_sum[0] / (float)(samples * 255.0);
			tile.rgb[1] = (float)rgb_sum[1] / (float)(samples * 255.0);
			tile.rgb[2] = (float)rgb_sum[2] / (float)(samples * 255.0);
			tile.lo = lo == 0xffff ? 0 : lo;
			tile.hi = hi;
		}

		tile.dirty = false;
		tile.seeded = true;
	}

	void RefreshDirtyTiles(Terrain* t)
	{
		last_refreshed_tiles = 0;
		last_dirty_remaining = 0;
		if (!t || tiles.empty())
			return;

		int tile_count = (int)tiles.size();
		if (dirty_cursor < 0 || dirty_cursor >= tile_count)
			dirty_cursor = 0;

		for (int checked = 0; checked < tile_count && last_refreshed_tiles < kMaxTileRefreshesPerFrame; checked++)
		{
			int idx = dirty_cursor;
			dirty_cursor = (dirty_cursor + 1) % tile_count;
			EditorTerrainOverviewTile& tile = tiles[idx];
			if (!tile.dirty)
				continue;
			RefreshTile(t, tile);
			last_refreshed_tiles++;
		}

		for (size_t i = 0; i < tiles.size(); i++)
		{
			if (tiles[i].dirty)
				last_dirty_remaining++;
		}
	}

	void RefreshAllDirtyTilesForCapture(Terrain* t)
	{
		last_refreshed_tiles = 0;
		last_dirty_remaining = 0;
		if (!t || tiles.empty())
			return;

		for (size_t i = 0; i < tiles.size(); i++)
		{
			if (!tiles[i].dirty)
				continue;
			RefreshTile(t, tiles[i]);
			last_refreshed_tiles++;
		}
		dirty_cursor = 0;
	}

	bool ShouldUse(Terrain* t, float projected_patch_pixels)
	{
		last_used = false;
		if (!t)
			return false;
		if (g_editor_force_exact_terrain)
			return false;

		int current_patch_count = GetTerrainPatches(t);
		if (terrain_ptr != t || patch_count != current_patch_count)
			Build(t);

		if (current_patch_count < 4096)
			return false;
		if (projected_patch_pixels >= kOverviewPatchPixelThreshold)
			return false;

		last_used = true;
		return true;
	}

	void BuildVertices(Terrain* t, std::vector<EditorOverviewVertex>& out, bool force_refresh_for_capture = false)
	{
		if (terrain_ptr != t || patch_count != GetTerrainPatches(t))
			Build(t);

		out.clear();
		if (force_refresh_for_capture)
			RefreshAllDirtyTilesForCapture(t);
		else
			RefreshDirtyTiles(t);
		int emitted = 0;
		for (size_t i = 0; i < tiles.size() && emitted < kMaxRenderedTiles; i++)
		{
			EditorTerrainOverviewTile& tile = tiles[i];

			float x0 = (float)(tile.min_patch_x * VISUAL_CELLS);
			float y0 = (float)(tile.min_patch_y * VISUAL_CELLS);
			float x1 = (float)((tile.max_patch_x + 1) * VISUAL_CELLS);
			float y1 = (float)((tile.max_patch_y + 1) * VISUAL_CELLS);
			float z = (float)((tile.lo + tile.hi) * 0.5f);
			EditorOverviewVertex v[6] = {
				{{x0, y0, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
				{{x1, y0, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
				{{x1, y1, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
				{{x0, y0, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
				{{x1, y1, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
				{{x0, y1, z}, {tile.rgb[0], tile.rgb[1], tile.rgb[2]}},
			};
			out.insert(out.end(), v, v + 6);
			emitted++;
		}

		last_tiles = emitted;
	}
};

const float EditorTerrainOverviewCache::kOverviewPatchPixelThreshold = 6.0f;

static EditorTerrainOverviewCache g_editor_terrain_overview;
static std::vector<EditorOverviewVertex> g_editor_terrain_overview_vertices;

void EditorTerrainOverviewMarkPatchDirty(Patch* p)
{
	g_editor_terrain_overview.MarkPatchDirty(p);
}

void EditorTerrainOverviewMarkTerrainTopologyDirty(Terrain* t)
{
	g_editor_terrain_overview.MarkTerrainTopologyDirty(t);
}

static void EditorTerrainOverviewMarkAllDirty()
{
	g_editor_terrain_overview.MarkAllDirty();
}

// Extract filename (sans extension) from a path into a fixed-size buffer
static void ExtractNameFromPath(const char* path, char* out, int max_len)
{
	out[0] = 0;
	if (!path) return;
	const char* slash = strrchr(path, '/');
	const char* base = slash ? slash + 1 : path;
	const char* dot = strrchr(base, '.');
	int len = dot ? (int)(dot - base) : (int)strlen(base);
	if (len > max_len - 1) len = max_len - 1;
	memcpy(out, base, len);
	out[len] = 0;
}

struct MyPalette
{
	static void Init()
	{
		MyPalette* p = (MyPalette*)GetPaletteArr();
		for (int j = 0; j < 256; j++)
			for (int i = 0; i < 768; i++)
				p[j].rgb[i] = fast_rand() & 0xFF;
	}

	static bool Scan(A3D_DirItem item, const char* name, void* cookie)
	{
		if (!(item&A3D_FILE))
			return true;

		char buf[4096];
		snprintf(buf, 4095, "%s/%s", (char*)cookie, name);
		buf[4095] = 0;

		a3dLoadImage(buf, buf/*path as cookie*/, MyPalette::Load);
		return true;
	}

	static void Load(void* cookie, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
	{
		if (palettes_loaded == 256)
			return;

		MyPalette* p = (MyPalette*)GetPaletteArr() + palettes_loaded;

		uint32_t* buf = (uint32_t*)malloc(w*h * sizeof(uint32_t));
		Convert_UI32_AABBGGRR(buf, f, w, h, data, palsize, palbuf);

		// extract palette by sampling at centers of w/16 x h/16 patches
		int hx = (w + 16) / 32;
		int hy = (h + 16) / 32;

		for (int y = 0; y < 16; y++)
		{
			int row = w * (y * h / 16 + hy) + hx;
			for (int x = 0; x < 16; x++)
			{
				uint32_t rgb = buf[x * w / 16 + row];

				p->rgb[3 * (x + y * 16) + 0] = rgb & 0xFF;
				p->rgb[3 * (x + y * 16) + 1] = (rgb>>8) & 0xFF;
				p->rgb[3 * (x + y * 16) + 2] = (rgb>>16) & 0xFF;
			}
		}

		ExtractNameFromPath((const char*)cookie, p->name, sizeof(p->name));

		free(buf);
		palettes_loaded++;
	}

	uint8_t rgb[3 * 256];
	char name[64];
} pal[256];

static const uint16_t cp437[256] =
{
	0x0000, 0x263A, 0x263B, 0x2665, 0x2666, 0x2663, 0x2660, 0x2022,
	0x25D8, 0x25CB, 0x25D9, 0x2642, 0x2640, 0x266A, 0x266B, 0x263C,
	0x25BA, 0x25C4, 0x2195, 0x203C, 0x00B6, 0x00A7, 0x25AC, 0x21A8,
	0x2191, 0x2193, 0x2192, 0x2190, 0x221F, 0x2194, 0x25B2, 0x25BC,
	0x0020, 0x0021, 0x0022, 0x0023, 0x0024, 0x0025, 0x0026, 0x0027,
	0x0028, 0x0029, 0x002A, 0x002B, 0x002C, 0x002D, 0x002E, 0x002F,
	0x0030, 0x0031, 0x0032, 0x0033, 0x0034, 0x0035, 0x0036, 0x0037,
	0x0038, 0x0039, 0x003A, 0x003B, 0x003C, 0x003D, 0x003E, 0x003F,
	0x0040, 0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046, 0x0047,
	0x0048, 0x0049, 0x004A, 0x004B, 0x004C, 0x004D, 0x004E, 0x004F,
	0x0050, 0x0051, 0x0052, 0x0053, 0x0054, 0x0055, 0x0056, 0x0057,
	0x0058, 0x0059, 0x005A, 0x005B, 0x005C, 0x005D, 0x005E, 0x005F,
	0x0060, 0x0061, 0x0062, 0x0063, 0x0064, 0x0065, 0x0066, 0x0067,
	0x0068, 0x0069, 0x006A, 0x006B, 0x006C, 0x006D, 0x006E, 0x006F,
	0x0070, 0x0071, 0x0072, 0x0073, 0x0074, 0x0075, 0x0076, 0x0077,
	0x0078, 0x0079, 0x007A, 0x007B, 0x007C, 0x007D, 0x007E, 0x2302,
	0x00C7, 0x00FC, 0x00E9, 0x00E2, 0x00E4, 0x00E0, 0x00E5, 0x00E7,
	0x00EA, 0x00EB, 0x00E8, 0x00EF, 0x00EE, 0x00EC, 0x00C4, 0x00C5,
	0x00C9, 0x00E6, 0x00C6, 0x00F4, 0x00F6, 0x00F2, 0x00FB, 0x00F9,
	0x00FF, 0x00D6, 0x00DC, 0x00A2, 0x00A3, 0x00A5, 0x20A7, 0x0192,
	0x00E1, 0x00ED, 0x00F3, 0x00FA, 0x00F1, 0x00D1, 0x00AA, 0x00BA,
	0x00BF, 0x2310, 0x00AC, 0x00BD, 0x00BC, 0x00A1, 0x00AB, 0x00BB,
	0x2591, 0x2592, 0x2593, 0x2502, 0x2524, 0x2561, 0x2562, 0x2556,
	0x2555, 0x2563, 0x2551, 0x2557, 0x255D, 0x255C, 0x255B, 0x2510,
	0x2514, 0x2534, 0x252C, 0x251C, 0x2500, 0x253C, 0x255E, 0x255F,
	0x255A, 0x2554, 0x2569, 0x2566, 0x2560, 0x2550, 0x256C, 0x2567,
	0x2568, 0x2564, 0x2565, 0x2559, 0x2558, 0x2552, 0x2553, 0x256B,
	0x256A, 0x2518, 0x250C, 0x2588, 0x2584, 0x258C, 0x2590, 0x2580,
	0x03B1, 0x00DF, 0x0393, 0x03C0, 0x03A3, 0x03C3, 0x00B5, 0x03C4,
	0x03A6, 0x0398, 0x03A9, 0x03B4, 0x221E, 0x03C6, 0x03B5, 0x2229,
	0x2261, 0x00B1, 0x2265, 0x2264, 0x2320, 0x2321, 0x00F7, 0x2248,
	0x00B0, 0x2219, 0x00B7, 0x221A, 0x207F, 0x00B2, 0x25A0, 0x00FF
};

struct MyFont
{
	static bool Scan(A3D_DirItem item, const char* name, void* cookie)
	{
		if (!(item&A3D_FILE))
			return true;

		char buf[4096];
		snprintf(buf,4095,"%s/%s",(char*)cookie,name);
		buf[4095]=0;

		a3dLoadImage(buf, buf/*path as cookie*/, MyFont::Load);
		return true;
	}

	static int Sort(const void* a, const void* b)
	{
		MyFont* fa = (MyFont*)a;
		MyFont* fb = (MyFont*)b;

		int qa = fa->width*fa->height;
		int qb = fb->width*fb->height;

		return qa - qb;
	}

	static void Free()
	{
		MyFont* fnt = (MyFont*)GetFontArr();
		for (int i=0; i<fonts_loaded; i++)
		{
			glDeleteTextures(1,&fnt[i].tex);
		}
	}

	static bool WritePSF(const char* path, int w, int h, uint32_t* buf, int shift)
	{
		FILE* f = fopen(path,"wb");
		if (!f)
			return false;

		int cell_w = w>>4;
		int cell_h = h>>4;

		int chars = 256;

		struct psf2_header
		{
			unsigned char magic[4];
			unsigned int version;
			unsigned int headersize;    /* offset of bitmaps in file */
			unsigned int flags;
			unsigned int length;        /* number of glyphs */
			unsigned int charsize;      /* number of bytes for each character */
			unsigned int height, width; /* max dimensions of glyphs */
			/* charsize = height * ((width + 7) / 8) */
		};

		psf2_header hdr =
		{
			{0x72,0xb5,0x4a,0x86},
			0,
			32,
			1, // has unicode table
			(unsigned)chars,
			(unsigned)(cell_h * ((cell_w + 7)>>3)),
			(unsigned)cell_h, (unsigned)cell_w
		};

		fwrite(&hdr,32,1,f);

		int index = 0;
		while (index<256)
		{
			int gx = index&15;
			int gy = index>>4;

			for (int y=0; y<cell_h; y++)
			{
				uint8_t byte = 0;
				for (int x=0; x<cell_w; x++)
				{
					int px = gx*cell_w + x;
					int py = gy*cell_h + y;

					int s = x&7;

					if ( (buf[px + py*w] >> shift) & 0x80 )
						byte |= 128>>s;

					if (x == cell_w-1)
						fwrite(&byte, 1, 1, f);
					else
					if (s == 7)
					{
						fwrite(&byte, 1, 1, f);
						byte = 0;
					}
				}
			}
			index++;
		}

		// unicode table
		index = 0;
		while (index<256)
		{
			int uni = cp437[index];
			uint8_t utf[4];
			int len;

			if (uni<0x0080)
			{
				utf[0]=uni&0xFF;
				len=1;
			}
			else
			if (uni<0x0800)
			{
				utf[0] = 0xC0 | ( ( uni >> 6 ) & 0x1F );
				utf[1] = 0x80 | ( uni & 0x3F );
				len=2;
			}
			else
			{
				utf[0] = 0xE0 | ( ( uni >> 12 ) & 0x0F );
				utf[1] = 0x80 | ( ( uni >> 6 ) & 0x3F );
				utf[2] = 0x80 | ( uni & 0x3F );
				len=3;
			}

			utf[len++] = 0xFF; // glyph term
			fwrite(utf, 1, len, f);

			index++;
		}

		fclose(f);
		return true;
	}

	static bool WriteBDF(const char* path, int w, int h, uint32_t* buf, int shift)
	{
		FILE* f = fopen(path,"wb");
		if (!f)
			return false;

		int cell_w = w>>4;
		int cell_h = h>>4;

		int chars = 256;

		fprintf(f,"STARTFONT 2.1\n");
		fprintf(f,"FONT -gumix-asciicker-medium-r-normal--%d-120-72-72-c-120-iso10646-1\n", cell_h);
		fprintf(f,"SIZE %d 72 72\n", cell_h);
		fprintf(f,"FONTBOUNDINGBOX %d %d 0 0\n", cell_w, cell_h);

		fprintf(f,"STARTPROPERTIES 23\n");
		fprintf(f,"ADD_STYLE_NAME \"\"\n");
		fprintf(f,"AVERAGE_WIDTH 120\n");
		fprintf(f,"CHARSET_ENCODING \"1\"\n");
		fprintf(f,"CHARSET_REGISTRY \"ISO10646\"\n");
		fprintf(f,"COPYRIGHT \"gumix\"\n");
		fprintf(f,"FAMILY_NAME \"asciicker\"\n");
		fprintf(f,"FOUNDRY \"gumix\"\n");
		fprintf(f,"MIN_SPACE %d\n", cell_w);
		fprintf(f,"NOTICE \"Licensed\"\n");
		fprintf(f,"PIXEL_SIZE %d\n", cell_h);
		fprintf(f,"POINT_SIZE 120\n");
		fprintf(f,"QUAD_WIDTH %d\n", cell_w);
		fprintf(f,"RESOLUTION_X 72\n");
		fprintf(f,"RESOLUTION_Y 72\n");
		fprintf(f,"SETWIDTH_NAME \"Normal\"\n");
		fprintf(f,"SLANT \"R\"\n");
		fprintf(f,"SPACING \"M\"\n");
		fprintf(f,"WEIGHT 10\n");
		fprintf(f,"WEIGHT_NAME \"Bold\"\n");
		fprintf(f,"X_HEIGHT 10\n");
		fprintf(f,"DEFAULT_CHAR 33\n");
		fprintf(f,"FONT_DESCENT %d\n", 0);
		fprintf(f,"FONT_ASCENT %d\n", cell_h);
		fprintf(f,"ENDPROPERTIES\n");

		fprintf(f,"CHARS %d\n", chars);

		int index = 0;
		while (index<256)
		{
			int gx = index&15;
			int gy = index>>4;

			fprintf(f,"STARTCHAR U+%04X\n", cp437[index]);
			fprintf(f,"ENCODING %d\n", cp437[index]);
			fprintf(f,"SWIDTH 500 0\n");
			fprintf(f,"DWIDTH %d 0\n", cell_w);
			fprintf(f,"BBX %d %d 0 0\n", cell_w, cell_h);
			fprintf(f,"BITMAP\n");
			for (int y=0; y<cell_h; y++)
			{
				uint8_t byte = 0;
				for (int x=0; x<cell_w; x++)
				{
					int px = gx*cell_w + x;
					int py = gy*cell_h + y;

					int s = x&7;

					if ( (buf[px + py*w] >> shift) & 0x80 )
						byte |= 128>>s;

					if (x == cell_w-1)
						fprintf(f,"%02X\n",byte);
					else
					if (s == 7)
					{
						fprintf(f,"%02X",byte);
						byte = 0;
					}
				}
			}
			fprintf(f,"ENDCHAR\n");
			index++;
		}

		fprintf(f,"ENDFONT\n");
		fclose(f);
		return true;
	}

	static void Load(void* cookie, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
	{
		if (fonts_loaded==256)
			return;

		MyFont* fnt = (MyFont*)GetFontArr() + fonts_loaded;

		fnt->width = w;
		fnt->height = h;

		int ifmt = GL_RGBA8;
		int fmt = GL_RGBA;
		int type = GL_UNSIGNED_BYTE;

		uint32_t* buf = (uint32_t*)malloc(w * h * sizeof(uint32_t));

		uint8_t rgb[3] = { 0xff,0xff,0xff };
		ConvertLuminance_UI32_LLZZYYXX(buf, rgb, f, w, h, data, palsize, palbuf);

		char* path = (char*)cookie;
		char export_path[1024];
		sprintf(export_path,"%s.bdf",path);
		WriteBDF(export_path, w,h,buf,24);
		sprintf(export_path,"%s.psf",path);
		WritePSF(export_path, w,h,buf,24);

		gl3CreateTextures(GL_TEXTURE_2D, 1, &fnt->tex);
		gl3TextureStorage2D(fnt->tex, 1, ifmt, w, h);

		glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
		gl3TextureSubImage2D(fnt->tex, 0, 0, 0, w, h, fmt, type, buf ? buf : data);
		glPixelStorei(GL_UNPACK_ALIGNMENT, 4);

		float white_transp[4] = { 1,1,1,0 };

		gl3TextureParameteri2D(fnt->tex, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(fnt->tex, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(fnt->tex, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_BORDER);
		gl3TextureParameteri2D(fnt->tex, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_BORDER);

		gl3TextureParameterfv2D(fnt->tex, GL_TEXTURE_BORDER_COLOR, white_transp);


		/*
		// if we want to filter font we'd have first to
		// modify 3 things in font sampling by shader:
		// - clamp uv to glyph boundary during sampling
		// - fade result by distance normalized to 0.5 of texel
		//   between unclamped uv to clamping glyph boundary
		// - use manual lod as log2(font_zoom)

		int max_lod = 0;
		while (!((w & 1) | (h & 1)))
		{
			max_lod++;
			w >>= 1;
			h >>= 1;
		}
		glGenerateTextureMipmap(fnt->tex);
		glTextureParameteri(fnt->tex, GL_TEXTURE_MAX_LOD, max_lod);
		*/

		ExtractNameFromPath((const char*)cookie, fnt->name, sizeof(fnt->name));

		if (buf)
			free(buf);

		fonts_loaded++;

		qsort(GetFontArr(), fonts_loaded, sizeof(MyFont), MyFont::Sort);
	}

	void SetTexel(int x, int y, uint8_t val)
	{
		uint8_t texel[4] = { 0xFF,0xFF,0xFF,val };
		gl3TextureSubImage2D(tex, 0, x, y, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, texel);
	}

	uint8_t GetTexel(int x, int y)
	{
		uint8_t texel[4];
		gl3GetTextureSubImage(tex, 0, x, y, 0, 1, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, 4, texel);
		return texel[3];
	}

	int width;
	int height;

	GLuint tex;
	char name[64];
} font[256];

void* GetMaterialArr()
{
	return mat;
}

void* GetPaletteArr()
{
	return pal;
}

void EditorMaterialUndoUpdate(int material_id)
{
	if (g_fl4131_headless_material_proof)
		return;
	mat[material_id].Update();
	EditorTerrainOverviewMarkAllDirty();
}

void EditorPaletteUndoUpdate(int palette_id)
{
	(void)palette_id;
}

void* GetFontArr()
{
	return font;
}


int active_font = 0;
int active_glyph = 0x40; //@
static GlyphId active_glyph_id = 0x40; //@
int active_palette = 0;
int active_material = 0;
static bool invert_material_preview = false;
static int shade_contrast_min = 0;
static int shade_contrast_max = 15;
static int row_shade_contrast_min[kMaterialGridRowCount] = { 0, 0, 0, 0 };
static int row_shade_contrast_max[kMaterialGridRowCount] = { 15, 15, 15, 15 };
static const float NEAR_NEUTRAL_SAT_THRESHOLD = 0.15f;
static float RgbLuminance01(const uint8_t rgb[3]);
static float RgbSaturation01(const uint8_t rgb[3]);

struct PaletteTheme
{
	const char* name;
	uint8_t bg[3];
	uint8_t fg[3];
	uint8_t accent[6][3];
};

static const PaletteTheme g_palette_themes[] =
{
	{ "Gruvbox",     {0x28,0x28,0x28}, {0xeb,0xdb,0xb2}, {{0xcc,0x24,0x1d},{0x98,0x97,0x1a},{0xd7,0x99,0x21},{0x45,0x85,0x88},{0xb1,0x62,0x86},{0x68,0x9d,0x6a}} },
	{ "Nord",        {0x2e,0x34,0x40}, {0xec,0xef,0xf4}, {{0xbf,0x61,0x6a},{0xa3,0xbe,0x8c},{0xeb,0xcb,0x8b},{0x5e,0x81,0xac},{0xb4,0x8e,0xad},{0x88,0xc0,0xd0}} },
	{ "Dracula",     {0x28,0x2a,0x36}, {0xf8,0xf8,0xf2}, {{0xff,0x55,0x55},{0x50,0xfa,0x7b},{0xf1,0xfa,0x8c},{0x62,0x72,0xa4},{0xbd,0x93,0xf9},{0x8b,0xe9,0xfd}} },
	{ "Monokai",     {0x27,0x28,0x22}, {0xf8,0xf8,0xf2}, {{0xf9,0x26,0x72},{0xa6,0xe2,0x2e},{0xe6,0xdb,0x74},{0x66,0xd9,0xef},{0xae,0x81,0xff},{0x66,0xd9,0xef}} },
	{ "Tokyo Night", {0x1a,0x1b,0x26}, {0xa9,0xb1,0xd6}, {{0xf7,0x76,0x8e},{0x9e,0xce,0x6a},{0xe0,0xaf,0x68},{0x7a,0xa2,0xf7},{0x9d,0x7c,0xd8},{0x7d,0xcf,0xff}} },
	{ "Catppuccin",  {0x1e,0x1e,0x2e}, {0xcd,0xd6,0xf4}, {{0xf3,0x8b,0xa8},{0xa6,0xe3,0xa1},{0xf9,0xe2,0xaf},{0x89,0xb4,0xfa},{0xcb,0xa6,0xf7},{0x94,0xe2,0xd5}} },
	{ "Solarized",   {0x00,0x2b,0x36}, {0x83,0x94,0x96}, {{0xdc,0x32,0x2f},{0x85,0x99,0x00},{0xb5,0x89,0x00},{0x26,0x8b,0xd2},{0x6c,0x71,0xc4},{0x2a,0xa1,0x98}} },
	{ "One Dark",    {0x28,0x2c,0x34}, {0xab,0xb2,0xbf}, {{0xe0,0x6c,0x75},{0x98,0xc3,0x79},{0xe5,0xc0,0x7b},{0x61,0xaf,0xef},{0xc6,0x78,0xdd},{0x56,0xb6,0xc2}} },
	{ "PicoCAD",     {0x00,0x00,0x00}, {0xff,0xff,0xff}, {{0xff,0x00,0x33},{0xff,0x99,0x00},{0xff,0xff,0x33},{0x00,0xff,0x33},{0x33,0x99,0xff},{0xff,0x66,0x99}} },
};

static MatCell g_theme_saved_mat_backup[kPaletteCount][kMaterialGridRowCount][kMaterialGridColCount];
static uint8_t g_theme_saved_pal_backup[3 * kPaletteCount];
static int g_active_theme_index = -1;
static int g_theme_backup_palette_index = -1;
static bool g_has_theme_backup = false;
static bool g_theme_modified = false;
static bool g_theme_session_baseline_ready = false;
static MatCell g_theme_session_start_mat[kPaletteCount][kMaterialGridRowCount][kMaterialGridColCount];
static uint8_t g_theme_session_start_pal[kPaletteCount][3 * kPaletteCount];
static bool g_palette_expanded = false;
static int g_palette_selected = 0;
static int g_last_palette_index = -1;
static bool g_palette_edit_active = false;
static int g_palette_edit_palette_index = -1;
static uint8_t g_palette_edit_backup[3 * kPaletteCount];
static bool g_palette_edit_theme_modified_backup = false;
static uint8_t g_cached_theme_palette[3 * kPaletteCount];
static bool g_cached_theme_palette_valid = false;
static int g_cached_theme_palette_index = -1;
static bool g_material_row_peak_cache_valid[kPaletteCount] = {false};
static float g_material_row_peak_cache[kPaletteCount][kMaterialGridRowCount][2] = {{{0}}};

struct AsciiidExtendedGlyphLabel
{
	GlyphId glyph_id;
	const char* label;
	uint8_t fallback_cp437;
};

static const char* kAsciiidExtendedGlyphManifestPath = "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json";
static const char* kAsciiidExtendedGlyphManifestHash = "8da4013164f7f58ad868f0cbb963d67a350f1e8495a2d59a45556924af7007b3";
static const char* kAsciiidCompiledAtlasPagePathFmt = "assets/glyphs/atlases/material.additive.v1.page%d_rgba8.json";
static const int kAsciiidCompiledAtlasMaxCellPx = 40;
static const int kAsciiidCompiledAtlasCols = 16;
static const GlyphId kAsciiidCompiledAtlasFirstGlyphId = 512;
static const GlyphId kAsciiidCompiledAtlasLastGlyphId = 631;
static const int kAsciiidExtendedCellPxOptions[] = {4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40};
static int g_asciiid_extended_preview_cell_px = 16;
static GlyphId g_asciiid_selected_extended_glyph_id = kAsciiidCompiledAtlasFirstGlyphId;

static const AsciiidExtendedGlyphLabel kAsciiidExtendedGlyphLabels[] =
{
	{512, "integral", '|'}, {513, "delta", '^'}, {514, "nabla", 'v'}, {515, "angle", '<'},
	{516, "perpendicular", '+'}, {517, "parallel", '|'}, {518, "slash", '/'}, {519, "backslash", '\\'},
	{520, "wave", '~'}, {521, "dashed", '-'}, {522, "corner tl", '.'}, {523, "corner tr", '.'},
	{524, "corner bl", '\''}, {525, "corner br", '\''}, {526, "bottom", 'T'}, {527, "top", 'T'},
	{528, "dot", '.'}, {529, "bullet", '.'}, {530, "ring", 'o'}, {531, "small ring", 'o'},
	{532, "therefore", ':'}, {533, "because", ':'}, {534, "diag down", '.'}, {535, "diag up", '.'},
	{536, "diamond", 'o'}, {537, "solid diamond", '@'}, {538, "square", 'o'}, {539, "solid square", '#'},
	{540, "circle", 'o'}, {541, "target", 'O'}, {542, "triple wave", '~'}, {543, "approx", '='},
	{544, "sine wave", '~'}, {545, "reversed sine wave", '~'}, {546, "arc", '~'}, {547, "dot operator", '.'},
	{548, "ring above", '\''}, {549, "grass quote left", '\''}, {550, "grass quote right", '\''}, {551, "ditto", '"'},
	{552, "acute stroke", '\''}, {553, "grave stroke", '`'}, {554, "low stroke left", '`'}, {555, "low stroke right", '\''},
	{556, "katakana no", '/'}, {557, "katakana ha", 'v'}, {558, "katakana he", '^'}, {559, "katakana to", '7'},
	{560, "katakana i", 'i'}, {561, "katakana ri", '|'}, {562, "katakana so", 'v'}, {563, "katakana tsu", 'w'},
	{564, "word separator dot", '.'}, {565, "dot above", '\''}, {566, "right tack", 'l'}, {567, "left tack", 'l'},
	{568, "smile arc", 'u'}, {569, "frown arc", 'n'}, {570, "breve", 'v'}, {571, "snow star bright", '*'},
	{572, "snow star light", '*'}, {573, "six spoke star", '*'}, {574, "eight spoke star", '*'}, {575, "asterisk star", '*'},
	{576, "open center star", '*'}, {577, "asterisk operator", '*'}, {578, "star operator", '*'}, {579, "white square round", 'o'},
	{580, "square target", '#'}, {581, "diamond target", '@'}, {582, "diamond fill", '@'}, {583, "dotted circle", 'o'},
	{584, "circle fill", '@'}, {585, "left half circle", 'O'}, {586, "right half circle", 'O'}, {587, "lower half circle", 'O'},
	{588, "upper half circle", 'O'}, {589, "small up triangle", '^'}, {590, "small down triangle", 'v'}, {591, "small left triangle", '<'},
	{592, "small right triangle", '>'}, {593, "white left triangle", '<'}, {594, "white right triangle", '>'},
	{595, "black left triangle", '<'}, {596, "black right triangle", '>'}, {597, "white up triangle", '^'}, {598, "white down triangle", 'v'},
	{599, "lozenge", 'o'},
	{600, "katakana a", '?'}, {601, "katakana u", '?'}, {602, "katakana e", '?'}, {603, "katakana o", '?'},
	{604, "katakana ka", '?'}, {605, "katakana ki", '?'}, {606, "katakana ku", '?'}, {607, "katakana ke", '?'},
	{608, "katakana ko", '?'}, {609, "katakana sa", '?'}, {610, "katakana shi", '?'}, {611, "katakana su", '?'},
	{612, "katakana se", '?'}, {613, "katakana ta", '?'}, {614, "katakana chi", '?'}, {615, "katakana te", '?'},
	{616, "arabic alef", '|'}, {617, "arabic beh", '?'}, {618, "arabic teh", '?'}, {619, "arabic theh", '?'},
	{620, "arabic jeem", '?'}, {621, "arabic hah", '?'}, {622, "arabic khah", '?'}, {623, "arabic dal", '?'},
	{624, "arabic reh", '?'}, {625, "arabic zain", '?'}, {626, "arabic seen", '~'}, {627, "arabic sheen", '~'},
	{628, "arabic sad", 'o'}, {629, "arabic dad", 'o'}, {630, "arabic tah", 'o'}, {631, "arabic zah", 'o'},
};

struct AsciiidExtendedGlyphPreset
{
	const char* material;
	const char* name;
	const GlyphId* glyphs;
	int count;
	const char* note;
};

static const GlyphId kPresetWaterContourFlow[] = {512, 544, 542, 543, 545, 520, 521, 546};
static const GlyphId kPresetWaterSurfaceSparkle[] = {530, 531, 529, 547, 528, 548, 532, 533};
static const GlyphId kPresetWaterSurfaceSparkleCore[] = {530, 531, 529, 528, 532, 533};
static const GlyphId kPresetGrassBladeTexture[] = {549, 550, 551, 552, 553, 554, 555, 520};
static const GlyphId kPresetGrassKatakana[] = {556, 557, 558, 559, 560, 561, 562, 563};
static const GlyphId kPresetDirtPebbleNoise[] = {532, 533, 534, 535, 564, 528, 565, 529};
static const GlyphId kPresetDirtSlopeDust[] = {518, 519, 513, 514, 520, 521, 524, 525};
static const GlyphId kPresetStoneEdgeRidgeMath[] = {513, 514, 515, 516, 517, 518, 519, 520};
static const GlyphId kPresetStoneHardFracture[] = {522, 523, 524, 525, 526, 566, 567, 527};
static const GlyphId kPresetSandSoftDune[] = {544, 545, 543, 546, 568, 569, 570, 565};
static const GlyphId kPresetSandFineGrain[] = {528, 529, 530, 531, 547, 548, 532, 533};
static const GlyphId kPresetSnowCrystal[] = {571, 572, 573, 574, 575, 576, 577, 578};
static const GlyphId kPresetSnowDrift[] = {520, 546, 568, 548, 528, 530, 531, 529};
static const GlyphId kPresetMudWetSmear[] = {542, 543, 545, 544, 520, 521, 524, 525};
static const GlyphId kPresetMudRuts[] = {518, 519, 516, 517, 526, 566, 567, 527};
static const GlyphId kPresetCobbleStoneCell[] = {536, 537, 538, 539, 579, 580, 581, 582};
static const GlyphId kPresetCobbleRoundJoint[] = {540, 583, 541, 584, 585, 586, 587, 588};
static const GlyphId kPresetGravelMixedGrain[] = {528, 529, 530, 531, 547, 565, 532, 533};
static const GlyphId kPresetGravelSharpGrain[] = {589, 590, 591, 592, 593, 594, 534, 535};
static const GlyphId kPresetKatakanaGrass[] = {556, 557, 558, 561, 562, 563};
static const GlyphId kPresetKatakanaStone[] = {560, 606, 607, 559, 604, 605};
static const GlyphId kPresetKatakanaGravel[] = {610, 611, 612, 562, 614, 563};
static const GlyphId kPresetKatakanaDirt[] = {556, 560, 561, 559, 558};
static const GlyphId kPresetKatakanaFullAtoTe[] = {600, 601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612, 613, 614, 615};
static const GlyphId kPresetArabicCurves[] = {616, 617, 618, 619, 620, 621, 622, 623, 624, 625, 626, 627, 628, 629, 630, 631};
static const GlyphId kPresetArabicWave[] = {626, 627, 628, 629, 617, 618, 619, 620};

static const AsciiidExtendedGlyphPreset kAsciiidExtendedGlyphPresets[] =
{
	{"WATER", "Contour Flow", kPresetWaterContourFlow, (int)(sizeof(kPresetWaterContourFlow) / sizeof(kPresetWaterContourFlow[0])), "admitted extended material preset"},
	{"WATER", "Surface Sparkle", kPresetWaterSurfaceSparkle, (int)(sizeof(kPresetWaterSurfaceSparkle) / sizeof(kPresetWaterSurfaceSparkle[0])), "admitted extended material preset"},
	{"WATER", "Surface Sparkle Core", kPresetWaterSurfaceSparkleCore, (int)(sizeof(kPresetWaterSurfaceSparkleCore) / sizeof(kPresetWaterSurfaceSparkleCore[0])), "admitted Phase 2 core subset"},
	{"GRASS", "Blade Texture", kPresetGrassBladeTexture, (int)(sizeof(kPresetGrassBladeTexture) / sizeof(kPresetGrassBladeTexture[0])), "admitted extended material preset"},
	{"GRASS", "Katakana Grass", kPresetGrassKatakana, (int)(sizeof(kPresetGrassKatakana) / sizeof(kPresetGrassKatakana[0])), "admitted extended material preset"},
	{"DIRT", "Pebble Noise", kPresetDirtPebbleNoise, (int)(sizeof(kPresetDirtPebbleNoise) / sizeof(kPresetDirtPebbleNoise[0])), "admitted extended material preset"},
	{"DIRT", "Slope Dust", kPresetDirtSlopeDust, (int)(sizeof(kPresetDirtSlopeDust) / sizeof(kPresetDirtSlopeDust[0])), "admitted Phase 2 core"},
	{"STONE", "Edge / Ridge Math", kPresetStoneEdgeRidgeMath, (int)(sizeof(kPresetStoneEdgeRidgeMath) / sizeof(kPresetStoneEdgeRidgeMath[0])), "admitted Phase 2 core"},
	{"STONE", "Hard Fracture", kPresetStoneHardFracture, (int)(sizeof(kPresetStoneHardFracture) / sizeof(kPresetStoneHardFracture[0])), "admitted extended material preset"},
	{"SAND", "Soft Dune", kPresetSandSoftDune, (int)(sizeof(kPresetSandSoftDune) / sizeof(kPresetSandSoftDune[0])), "admitted extended material preset"},
	{"SAND", "Fine Grain", kPresetSandFineGrain, (int)(sizeof(kPresetSandFineGrain) / sizeof(kPresetSandFineGrain[0])), "admitted extended material preset"},
	{"SNOW", "Crystal", kPresetSnowCrystal, (int)(sizeof(kPresetSnowCrystal) / sizeof(kPresetSnowCrystal[0])), "admitted extended material preset"},
	{"SNOW", "Drift", kPresetSnowDrift, (int)(sizeof(kPresetSnowDrift) / sizeof(kPresetSnowDrift[0])), "admitted extended material preset"},
	{"MUD", "Wet Smear", kPresetMudWetSmear, (int)(sizeof(kPresetMudWetSmear) / sizeof(kPresetMudWetSmear[0])), "admitted extended material preset"},
	{"MUD", "Ruts", kPresetMudRuts, (int)(sizeof(kPresetMudRuts) / sizeof(kPresetMudRuts[0])), "admitted extended material preset"},
	{"COBBLE", "Stone Cell", kPresetCobbleStoneCell, (int)(sizeof(kPresetCobbleStoneCell) / sizeof(kPresetCobbleStoneCell[0])), "admitted extended material preset"},
	{"COBBLE", "Round / Joint", kPresetCobbleRoundJoint, (int)(sizeof(kPresetCobbleRoundJoint) / sizeof(kPresetCobbleRoundJoint[0])), "admitted extended material preset"},
	{"GRAVEL", "Mixed Grain", kPresetGravelMixedGrain, (int)(sizeof(kPresetGravelMixedGrain) / sizeof(kPresetGravelMixedGrain[0])), "admitted extended material preset"},
	{"GRAVEL", "Sharp Grain", kPresetGravelSharpGrain, (int)(sizeof(kPresetGravelSharpGrain) / sizeof(kPresetGravelSharpGrain[0])), "admitted extended material preset"},
	{"KATAKANA", "Grass", kPresetKatakanaGrass, (int)(sizeof(kPresetKatakanaGrass) / sizeof(kPresetKatakanaGrass[0])), "admitted extended material preset"},
	{"KATAKANA", "Stone", kPresetKatakanaStone, (int)(sizeof(kPresetKatakanaStone) / sizeof(kPresetKatakanaStone[0])), "admitted extended material preset"},
	{"KATAKANA", "Gravel", kPresetKatakanaGravel, (int)(sizeof(kPresetKatakanaGravel) / sizeof(kPresetKatakanaGravel[0])), "admitted extended material preset"},
	{"KATAKANA", "Dirt", kPresetKatakanaDirt, (int)(sizeof(kPresetKatakanaDirt) / sizeof(kPresetKatakanaDirt[0])), "admitted extended material preset"},
	{"KATAKANA", "Full A-Te", kPresetKatakanaFullAtoTe, (int)(sizeof(kPresetKatakanaFullAtoTe) / sizeof(kPresetKatakanaFullAtoTe[0])), "admitted extended material preset"},
	{"ARABIC", "Curves A-Zah", kPresetArabicCurves, (int)(sizeof(kPresetArabicCurves) / sizeof(kPresetArabicCurves[0])), "admitted extended material preset"},
	{"ARABIC", "Wave Bowl", kPresetArabicWave, (int)(sizeof(kPresetArabicWave) / sizeof(kPresetArabicWave[0])), "admitted extended material preset"},
};

static const int kAsciiidExtendedGlyphPresetCount = (int)(sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0]));

// FL-4131 P3: Unicode-block category buckets for the extended character
// palette. The browser uses these to group admitted glyphs into repertoire
// rows (math / box / blocks / shapes / katakana / etc.) so an operator can
// scan the palette by purpose, not by raw GlyphId.
enum AsciiidExtendedGlyphCategory
{
	kAsciiidExtCatMath = 0,
	kAsciiidExtCatBox,
	kAsciiidExtCatBlocks,
	kAsciiidExtCatShapes,
	kAsciiidExtCatArrows,
	kAsciiidExtCatPunct,
	kAsciiidExtCatHiragana,
	kAsciiidExtCatKatakana,
	kAsciiidExtCatCJKSym,
	kAsciiidExtCatArabic,
	kAsciiidExtCatOther,
	kAsciiidExtCatCount
};

static const char* AsciiidExtendedGlyphCategoryLabel(int cat)
{
	switch (cat)
	{
		case kAsciiidExtCatMath:     return "Math";
		case kAsciiidExtCatBox:      return "Box";
		case kAsciiidExtCatBlocks:   return "Blocks";
		case kAsciiidExtCatShapes:   return "Shapes";
		case kAsciiidExtCatArrows:   return "Arrows";
		case kAsciiidExtCatPunct:    return "Punct";
		case kAsciiidExtCatHiragana: return "Hiragana";
		case kAsciiidExtCatKatakana: return "Katakana";
		case kAsciiidExtCatCJKSym:   return "CJK Sym";
		case kAsciiidExtCatArabic:   return "Arabic";
		default:                     return "Other";
	}
}

static int AsciiidExtendedGlyphCategoryForUnicode(uint32_t u)
{
	if (u >= 0x2200 && u <= 0x22FF) return kAsciiidExtCatMath;     // Mathematical Operators
	if (u >= 0x2300 && u <= 0x23FF) return kAsciiidExtCatMath;     // Misc Technical
	if (u >= 0x2500 && u <= 0x257F) return kAsciiidExtCatBox;      // Box Drawing
	if (u >= 0x2580 && u <= 0x259F) return kAsciiidExtCatBlocks;   // Block Elements
	if (u >= 0x25A0 && u <= 0x25FF) return kAsciiidExtCatShapes;   // Geometric Shapes
	if (u >= 0x2600 && u <= 0x26FF) return kAsciiidExtCatShapes;   // Misc Symbols
	if (u >= 0x2700 && u <= 0x27BF) return kAsciiidExtCatShapes;   // Dingbats
	if (u >= 0x2190 && u <= 0x21FF) return kAsciiidExtCatArrows;   // Arrows
	if (u >= 0x27F0 && u <= 0x27FF) return kAsciiidExtCatArrows;   // Supplemental Arrows-A
	if (u >= 0x2900 && u <= 0x297F) return kAsciiidExtCatArrows;   // Supplemental Arrows-B
	if (u >= 0x2000 && u <= 0x206F) return kAsciiidExtCatPunct;    // General Punctuation
	if (u >= 0x02B0 && u <= 0x02FF) return kAsciiidExtCatPunct;    // Spacing Modifier Letters
	if (u >= 0x0300 && u <= 0x036F) return kAsciiidExtCatPunct;    // Combining Diacritical Marks
	if (u >= 0x00A0 && u <= 0x024F) return kAsciiidExtCatPunct;    // Latin-1 Supplement / Extended
	if (u >= 0x3040 && u <= 0x309F) return kAsciiidExtCatHiragana; // Hiragana
	if (u >= 0x30A0 && u <= 0x30FF) return kAsciiidExtCatKatakana; // Katakana
	if (u >= 0x3000 && u <= 0x303F) return kAsciiidExtCatCJKSym;   // CJK Symbols & Punctuation
	if (u >= 0x0600 && u <= 0x06FF) return kAsciiidExtCatArabic;   // Arabic
	return kAsciiidExtCatOther;
}

static void FormatAsciiidExtendedGlyphUtf8(GlyphId glyph_id, char* out, int out_size);

static void FormatAsciiidExtendedPresetGlyphText(const AsciiidExtendedGlyphPreset& preset, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;
	out[0] = 0;
	int used = 0;
	for (int i = 0; i < preset.count && used < out_size - 1; i++)
	{
		char glyph_text[8];
		FormatAsciiidExtendedGlyphUtf8(preset.glyphs[i], glyph_text, sizeof(glyph_text));
		int glyph_len = (int)strlen(glyph_text);
		if (glyph_len <= 0)
			continue;
		if (used + glyph_len >= out_size)
			break;
		memcpy(out + used, glyph_text, glyph_len);
		used += glyph_len;
		out[used] = 0;
	}
}

struct AsciiidPresetUiRect
{
	bool valid;
	int x0;
	int y0;
	int x1;
	int y1;
};

static AsciiidPresetUiRect g_asciiid_preset_ui_rects[32];
static AsciiidPresetUiRect g_asciiid_extended_picker_first_glyph_rect;
static AsciiidPresetUiRect g_asciiid_extended_picker_fill_rect;
static GlyphId g_asciiid_extended_picker_first_glyph_id = GLYPH_ID_NONE;
static int g_asciiid_sidebar_tab_debug = -1;
static int g_asciiid_extended_preset_ui_frame_count = 0;

static const AsciiidExtendedGlyphLabel* FindAsciiidExtendedGlyphLabel(GlyphId glyph_id)
{
	for (int i = 0; i < (int)(sizeof(kAsciiidExtendedGlyphLabels) / sizeof(kAsciiidExtendedGlyphLabels[0])); i++)
		if (kAsciiidExtendedGlyphLabels[i].glyph_id == glyph_id)
			return &kAsciiidExtendedGlyphLabels[i];
	return NULL;
}

static const uint32_t kAsciiidExtendedGlyphUnicodeScalars[] =
{
	8747, 8710, 8711, 8736, 10178, 8741, 8725, 8726, 8961, 8967, 8988, 8989, 8990, 8991, 8869, 8868,
	183, 8729, 8728, 9702, 8756, 8757, 8945, 8944, 9671, 9670, 9633, 9632, 9675, 9678, 8779, 8776,
	8767, 8765, 8978, 8901, 730, 8216, 8217, 12291, 180, 96, 8218, 8219, 12494, 12495, 12504, 12488,
	12452, 12522, 12477, 12484, 12539, 729, 8866, 8867, 8995, 8994, 728, 10052, 10053, 10033, 10036,
	10035, 10034, 8727, 8902, 9634, 9635, 9672, 9670, 9676, 9679, 9680, 9681, 9682, 9683, 9652, 9662,
	9666, 9656, 9665, 9655, 9664, 9654, 9651, 9661, 9674, 12450, 12454, 12456, 12458, 12459, 12461,
	12463, 12465, 12467, 12469, 12471, 12473, 12475, 12479, 12481, 12486,
	1575, 1576, 1578, 1579, 1580, 1581, 1582, 1583, 1585, 1586, 1587, 1588, 1589, 1590, 1591, 1592,
};

static bool AsciiidExtendedGlyphUnicodeScalar(GlyphId glyph_id, uint32_t* out_scalar)
{
	if (glyph_id < kAsciiidCompiledAtlasFirstGlyphId || glyph_id > kAsciiidCompiledAtlasLastGlyphId)
		return false;
	const int index = (int)(glyph_id - kAsciiidCompiledAtlasFirstGlyphId);
	if (index < 0 || index >= (int)(sizeof(kAsciiidExtendedGlyphUnicodeScalars) / sizeof(kAsciiidExtendedGlyphUnicodeScalars[0])))
		return false;
	if (out_scalar)
		*out_scalar = kAsciiidExtendedGlyphUnicodeScalars[index];
	return true;
}

static void AsciiidWriteUtf8(uint32_t codepoint, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;
	out[0] = 0;
	if (codepoint <= 0x7F)
		snprintf(out, out_size, "%c", (char)codepoint);
	else if (codepoint <= 0x7FF)
		snprintf(out, out_size, "%c%c",
			(char)(0xC0 | ((codepoint >> 6) & 0x1F)),
			(char)(0x80 | (codepoint & 0x3F)));
	else if (codepoint <= 0xFFFF)
		snprintf(out, out_size, "%c%c%c",
			(char)(0xE0 | ((codepoint >> 12) & 0x0F)),
			(char)(0x80 | ((codepoint >> 6) & 0x3F)),
			(char)(0x80 | (codepoint & 0x3F)));
	else if (codepoint <= 0x10FFFF)
		snprintf(out, out_size, "%c%c%c%c",
			(char)(0xF0 | ((codepoint >> 18) & 0x07)),
			(char)(0x80 | ((codepoint >> 12) & 0x3F)),
			(char)(0x80 | ((codepoint >> 6) & 0x3F)),
			(char)(0x80 | (codepoint & 0x3F)));
	else
		snprintf(out, out_size, "?");
}

static void FormatAsciiidExtendedGlyphUtf8(GlyphId glyph_id, char* out, int out_size)
{
	uint32_t scalar = 0;
	if (AsciiidExtendedGlyphUnicodeScalar(glyph_id, &scalar))
		AsciiidWriteUtf8(scalar, out, out_size);
	else if (out && out_size > 0)
		snprintf(out, out_size, "?");
}

static uint8_t AsciiidGlyphFallbackByte(GlyphId glyph_id)
{
	if (glyph_id <= 0xFF)
		return (uint8_t)glyph_id;
	const AsciiidExtendedGlyphLabel* label = FindAsciiidExtendedGlyphLabel(glyph_id);
	return label ? label->fallback_cp437 : '?';
}

static int MaterialGridRows()
{
	return kMaterialGridRowCount;
}

static int MaterialGridCols()
{
	return kMaterialGridColCount;
}

static MatCell* MaterialGridCell(int mat_id, int row, int col)
{
	return &mat[mat_id].shade[row][col];
}

static const MatCell* MaterialGridCellConst(int mat_id, int row, int col)
{
	return &mat[mat_id].shade[row][col];
}

static void MaterialGridCommit(int mat_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount)
		return;
	g_material_row_peak_cache_valid[mat_id] = false;
	if (g_fl4131_headless_material_proof)
		return;
	mat[mat_id].Update();
	EditorTerrainOverviewMarkAllDirty();
}

static bool EnsureMaterialGlyphPlane(int mat_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount)
		return false;
	if (mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells)
		return true;
	MaterialGlyphPlane* plane = material_glyph_plane_alloc();
	if (!plane)
	{
		fprintf(stderr, "[FL-4131] ASCIIID failed to allocate MaterialGlyphPlane for material %d\n", mat_id);
		return false;
	}
	material_glyph_plane_init(plane);
	mat[mat_id].glyph_plane = plane;
	return true;
}

static void MaterialGridSetGlyphId(int mat_id, int row, int col, GlyphId glyph_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount || row < 0 || row >= kMaterialGridRowCount || col < 0 || col >= kMaterialGridColCount)
		return;

	MatCell* cell = MaterialGridCell(mat_id, row, col);
	cell->gl = AsciiidGlyphFallbackByte(glyph_id);
	if (glyph_id <= 0xFF)
	{
		if (mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells)
		{
			const int cell_index = row * kMaterialGridColCount + col;
			mat[mat_id].glyph_plane->cells[cell_index] = GLYPH_ID_NONE;
			mat[mat_id].glyph_plane->coverage[cell_index] = 0;
		}
		return;
	}

	if (!EnsureMaterialGlyphPlane(mat_id))
		return;
	const int cell_index = row * kMaterialGridColCount + col;
	mat[mat_id].glyph_plane->cells[cell_index] = glyph_id;
	// [FL-4131] Populate coverage at edit time so the engine render path
	// (engine/render/render_resolve.cpp:33-46) sees a non-zero coverage and
	// renders the resolved display glyph instead of falling closed to the
	// red '!' diagnostic. Without this, TERM++/native/web/multiplayer render
	// every authored extended cell as a missing-coverage diagnostic.
	uint16_t coverage = 0;
	AsciiidManifestLookupCoverage(glyph_id, &coverage);
	mat[mat_id].glyph_plane->coverage[cell_index] = coverage;
}

static GlyphId MaterialGridGlyphIdConst(int mat_id, int row, int col)
{
	if (mat_id < 0 || mat_id >= kPaletteCount || row < 0 || row >= kMaterialGridRowCount || col < 0 || col >= kMaterialGridColCount)
		return GLYPH_ID_NONE;
	GlyphId glyph_id = material_glyph_plane_lookup(mat[mat_id].glyph_plane, row, col);
	if (glyph_id != GLYPH_ID_NONE && glyph_id != GLYPH_ID_UNRESOLVED)
		return glyph_id;
	return MaterialGridCellConst(mat_id, row, col)->gl;
}

static void MaterialGridRotateRowLeft(int mat_id, int row)
{
	MatCell tmp = *MaterialGridCell(mat_id, row, 0);
	memmove(MaterialGridCell(mat_id, row, 0), MaterialGridCell(mat_id, row, 1), sizeof(MatCell) * (MaterialGridCols() - 1));
	*MaterialGridCell(mat_id, row, MaterialGridCols() - 1) = tmp;
	if (mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells)
	{
		GlyphId* cells = mat[mat_id].glyph_plane->cells + row * kMaterialGridColCount;
		GlyphId glyph_tmp = cells[0];
		memmove(cells, cells + 1, sizeof(GlyphId) * (MaterialGridCols() - 1));
		cells[MaterialGridCols() - 1] = glyph_tmp;
	}
}

static void MaterialGridRotateRowRight(int mat_id, int row)
{
	MatCell tmp = *MaterialGridCell(mat_id, row, MaterialGridCols() - 1);
	memmove(MaterialGridCell(mat_id, row, 1), MaterialGridCell(mat_id, row, 0), sizeof(MatCell) * (MaterialGridCols() - 1));
	*MaterialGridCell(mat_id, row, 0) = tmp;
	if (mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells)
	{
		GlyphId* cells = mat[mat_id].glyph_plane->cells + row * kMaterialGridColCount;
		GlyphId glyph_tmp = cells[MaterialGridCols() - 1];
		memmove(cells + 1, cells, sizeof(GlyphId) * (MaterialGridCols() - 1));
		cells[0] = glyph_tmp;
	}
}

static int clamp_shade_contrast_column(int row, int col)
{
	int lo = shade_contrast_min;
	int hi = shade_contrast_max;
	if (row >= 0 && row < kMaterialGridRowCount)
	{
		if (row_shade_contrast_min[row] > lo)
			lo = row_shade_contrast_min[row];
		if (row_shade_contrast_max[row] < hi)
			hi = row_shade_contrast_max[row];
	}
	if (lo > hi)
		lo = hi;

	if (col < lo)
		return lo;
	if (col > hi)
		return hi;
	return col;
}

static float Clamp01(float value)
{
	if (value < 0.0f)
		return 0.0f;
	if (value > 1.0f)
		return 1.0f;
	return value;
}

static void FormatGlyphLabel(int glyph, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;

	int idx = glyph & 0xFF;
	if (idx >= 0x20 && idx < 0x7F)
		snprintf(out, out_size, "0x%02X '%c' U+%04X", idx, idx, cp437[idx]);
	else
		snprintf(out, out_size, "0x%02X U+%04X", idx, cp437[idx]);
}

static void FormatGlyphIdLabel(GlyphId glyph_id, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;
	if (glyph_id <= 0xFF)
	{
		FormatGlyphLabel((int)glyph_id, out, out_size);
		return;
	}
	const AsciiidExtendedGlyphLabel* label = FindAsciiidExtendedGlyphLabel(glyph_id);
	char glyph_text[8];
	FormatAsciiidExtendedGlyphUtf8(glyph_id, glyph_text, sizeof(glyph_text));
	if (label)
		snprintf(out, out_size, "GlyphId %u %s %s fallback 0x%02X", (unsigned)glyph_id, glyph_text, label->label, label->fallback_cp437);
	else
		snprintf(out, out_size, "GlyphId %u %s fallback 0x%02X", (unsigned)glyph_id, glyph_text, AsciiidGlyphFallbackByte(glyph_id));
}

static void DrawAsciiidExtendedGlyphTextOverlay(GlyphId glyph_id, ImVec2 min, ImVec2 max, ImU32 color, float font_size)
{
	char glyph_text[8];
	FormatAsciiidExtendedGlyphUtf8(glyph_id, glyph_text, sizeof(glyph_text));
	if (!glyph_text[0])
		return;
	ImFont* font = ImGui::GetFont();
	ImVec2 text_size = font->CalcTextSizeA(font_size, FLT_MAX, 0.0f, glyph_text);
	ImVec2 pos(
		min.x + ((max.x - min.x) - text_size.x) * 0.5f,
		min.y + ((max.y - min.y) - text_size.y) * 0.5f);
	ImGui::GetWindowDrawList()->AddText(font, font_size, pos, color, glyph_text);
}

static void SelectActiveGlyphId(GlyphId glyph_id)
{
	active_glyph_id = glyph_id;
	if (glyph_id > 0xFF)
		g_asciiid_selected_extended_glyph_id = glyph_id;
	active_glyph = AsciiidGlyphFallbackByte(glyph_id);
}

static const GlyphManifest* AsciiidExtendedPickerManifest()
{
	static GlyphManifest glyph_manifest;
	static bool attempted = false;
	static bool loaded = false;
	static char errbuf[512] = {0};

	if (!attempted)
	{
		attempted = true;
		GlyphManifestError err = glyph_manifest_load_and_verify(
			kAsciiidExtendedGlyphManifestPath,
			kAsciiidExtendedGlyphManifestHash,
			&glyph_manifest,
			errbuf,
			sizeof(errbuf));
		loaded = (err == GLYPH_MANIFEST_OK);
		if (!loaded)
			fprintf(stderr, "[FL-4131] ASCIIID extended picker manifest load failed: %s\n", errbuf[0] ? errbuf : "unknown error");
	}
	return loaded ? &glyph_manifest : NULL;
}

static bool AsciiidManifestSupportsGlyph(GlyphId glyph_id)
{
	const GlyphManifest* manifest = AsciiidExtendedPickerManifest();
	if (!manifest)
		return false;
	return glyph_manifest_is_admitted(manifest, glyph_id) != 0;
}

static bool AsciiidManifestLookupCoverage(GlyphId glyph_id, uint16_t* out_coverage)
{
	const GlyphManifest* manifest = AsciiidExtendedPickerManifest();
	if (!manifest)
		return false;
	if (!glyph_manifest_is_admitted(manifest, glyph_id))
		return false;
	return glyph_manifest_lookup_coverage(manifest, glyph_id, out_coverage) != 0;
}

static bool ReadWholeFile(const char* path, char** out_text);

static uint8_t* g_asciiid_compiled_atlas_pixels = NULL;
static int g_asciiid_compiled_atlas_width = 0;
static int g_asciiid_compiled_atlas_height = 0;
static int g_asciiid_compiled_atlas_cell_px = 0;
static bool g_asciiid_compiled_atlas_loaded = false;
static bool g_asciiid_compiled_atlas_load_attempted = false;

static bool LoadAsciiidCompiledAtlasPage(int cell_px)
{
	if (cell_px <= 0)
		cell_px = 16;
	if (g_asciiid_compiled_atlas_loaded && g_asciiid_compiled_atlas_cell_px == cell_px)
		return true;
	if (g_asciiid_compiled_atlas_load_attempted && g_asciiid_compiled_atlas_cell_px == cell_px)
		return false;
	free(g_asciiid_compiled_atlas_pixels);
	g_asciiid_compiled_atlas_pixels = NULL;
	g_asciiid_compiled_atlas_width = 0;
	g_asciiid_compiled_atlas_height = 0;
	g_asciiid_compiled_atlas_loaded = false;
	g_asciiid_compiled_atlas_load_attempted = true;
	g_asciiid_compiled_atlas_cell_px = cell_px;

	char* text = NULL;
	char page_path[256];
	snprintf(page_path, sizeof(page_path), kAsciiidCompiledAtlasPagePathFmt, cell_px);
	if (!ReadWholeFile(page_path, &text))
	{
		if (cell_px == 16 && ReadWholeFile("assets/glyphs/atlases/material.additive.v1.page0_rgba8.json", &text))
		{
			// page0 is the legacy 16px alias emitted by compile_glyph_manifest.py.
		}
		else
		{
			fprintf(stderr, "[FL-4131] ASCIIID extended atlas page missing: %s\n", page_path);
			return false;
		}
	}

	cJSON* root = cJSON_Parse(text);
	free(text);
	if (!root)
		return false;

	bool ok = false;
	const cJSON* width = cJSON_GetObjectItemCaseSensitive(root, "width");
	const cJSON* height = cJSON_GetObjectItemCaseSensitive(root, "height");
	const cJSON* page_cell_px = cJSON_GetObjectItemCaseSensitive(root, "cell_px");
	const cJSON* format = cJSON_GetObjectItemCaseSensitive(root, "format");
	const cJSON* rgba8 = cJSON_GetObjectItemCaseSensitive(root, "rgba8");
	if (cJSON_IsNumber(width) &&
		cJSON_IsNumber(height) &&
		cJSON_IsNumber(page_cell_px) &&
		cJSON_IsString(format) &&
		cJSON_IsArray(rgba8) &&
		width->valueint > 0 &&
		height->valueint > 0 &&
		page_cell_px->valueint == g_asciiid_compiled_atlas_cell_px &&
		width->valueint % page_cell_px->valueint == 0 &&
		height->valueint % page_cell_px->valueint == 0 &&
		strcmp(format->valuestring, "rgba8") == 0 &&
		cJSON_GetArraySize(rgba8) == width->valueint * height->valueint * 4)
	{
		const int expected_count = width->valueint * height->valueint * 4;
		g_asciiid_compiled_atlas_pixels = (uint8_t*)malloc((size_t)expected_count);
		if (!g_asciiid_compiled_atlas_pixels)
		{
			cJSON_Delete(root);
			return false;
		}
		int i = 0;
		ok = true;
		for (const cJSON* item = rgba8->child; item; item = item->next)
		{
			if (!cJSON_IsNumber(item) || item->valueint < 0 || item->valueint > 255 || i >= expected_count)
			{
				ok = false;
				break;
			}
			g_asciiid_compiled_atlas_pixels[i++] = (uint8_t)item->valueint;
		}
		ok = ok && i == expected_count;
		if (ok)
		{
			g_asciiid_compiled_atlas_width = width->valueint;
			g_asciiid_compiled_atlas_height = height->valueint;
		}
	}
	cJSON_Delete(root);
	g_asciiid_compiled_atlas_loaded = ok;
	return ok;
}

static bool AsciiidCompiledAtlasGlyphCell(GlyphId glyph_id, uint8_t compiled_atlas_pixels[kAsciiidCompiledAtlasMaxCellPx * kAsciiidCompiledAtlasMaxCellPx], int* out_cell_px)
{
	if (!compiled_atlas_pixels)
		return false;
	memset(compiled_atlas_pixels, 0, kAsciiidCompiledAtlasMaxCellPx * kAsciiidCompiledAtlasMaxCellPx);
	if (glyph_id < kAsciiidCompiledAtlasFirstGlyphId || glyph_id > kAsciiidCompiledAtlasLastGlyphId)
		return false;
	const int cell_px = g_asciiid_extended_preview_cell_px;
	if (cell_px <= 0 || cell_px > kAsciiidCompiledAtlasMaxCellPx)
		return false;
	if (!LoadAsciiidCompiledAtlasPage(cell_px))
		return false;

	const int glyph_index = (int)(glyph_id - kAsciiidCompiledAtlasFirstGlyphId);
	const int cols = g_asciiid_compiled_atlas_width / cell_px;
	const int atlas_x = (glyph_index % cols) * cell_px;
	const int atlas_y = (glyph_index / cols) * cell_px;
	if (atlas_x < 0 || atlas_y < 0 || atlas_x + cell_px > g_asciiid_compiled_atlas_width || atlas_y + cell_px > g_asciiid_compiled_atlas_height)
		return false;
	for (int y = 0; y < cell_px; y++)
	{
		for (int x = 0; x < cell_px; x++)
		{
			const int src = ((atlas_y + y) * g_asciiid_compiled_atlas_width + (atlas_x + x)) * 4;
			const uint8_t r = g_asciiid_compiled_atlas_pixels[src + 0];
			const uint8_t g = g_asciiid_compiled_atlas_pixels[src + 1];
			const uint8_t b = g_asciiid_compiled_atlas_pixels[src + 2];
			const uint8_t a = g_asciiid_compiled_atlas_pixels[src + 3];
			compiled_atlas_pixels[y * kAsciiidCompiledAtlasMaxCellPx + x] = (a != 0 && (r != 0 || g != 0 || b != 0)) ? 1 : 0;
		}
	}
	if (out_cell_px)
		*out_cell_px = cell_px;
	return true;
}

static int AsciiidExtendedCellPxOptionIndex(int cell_px)
{
	for (int i = 0; i < (int)(sizeof(kAsciiidExtendedCellPxOptions) / sizeof(kAsciiidExtendedCellPxOptions[0])); i++)
		if (kAsciiidExtendedCellPxOptions[i] == cell_px)
			return i;
	return -1;
}

struct AsciiidExtendedGlyphVisualSample
{
	bool supported;
	bool compiled_atlas_supported;
	uint16_t coverage;
	int foreground_pixels;
	int background_pixels;
	int diagnostic_pixels;
	uint8_t display_glyph;
	int compiled_atlas_cell_px;
	uint8_t compiled_atlas_pixels[kAsciiidCompiledAtlasMaxCellPx * kAsciiidCompiledAtlasMaxCellPx];
};

static AsciiidExtendedGlyphVisualSample SampleAsciiidExtendedGlyphVisual(GlyphId glyph_id)
{
	AsciiidExtendedGlyphVisualSample sample = {};
	sample.supported = AsciiidManifestLookupCoverage(glyph_id, &sample.coverage);
	if (!sample.supported)
	{
		sample.diagnostic_pixels = g_asciiid_extended_preview_cell_px * g_asciiid_extended_preview_cell_px;
		sample.display_glyph = '!';
		return sample;
	}

	sample.compiled_atlas_supported = AsciiidCompiledAtlasGlyphCell(glyph_id, sample.compiled_atlas_pixels, &sample.compiled_atlas_cell_px);
	if (!sample.compiled_atlas_supported)
	{
		sample.supported = false;
		sample.diagnostic_pixels = g_asciiid_extended_preview_cell_px * g_asciiid_extended_preview_cell_px;
		return sample;
	}

	for (int y = 0; y < sample.compiled_atlas_cell_px; y++)
	{
		for (int x = 0; x < sample.compiled_atlas_cell_px; x++)
		{
			if (sample.compiled_atlas_pixels[y * kAsciiidCompiledAtlasMaxCellPx + x])
				sample.foreground_pixels++;
			else
				sample.background_pixels++;
		}
	}
	sample.display_glyph = sample.foreground_pixels > 0 ? 1 : 0;
	if (sample.foreground_pixels == 0)
	{
		sample.supported = false;
		sample.compiled_atlas_supported = false;
		sample.diagnostic_pixels = sample.compiled_atlas_cell_px * sample.compiled_atlas_cell_px;
		sample.display_glyph = '!';
	}
	return sample;
}

static void DrawAsciiidUnknownGlyphMarker(ImDrawList* draw, const ImVec2& min, const ImVec2& max)
{
	draw->AddLine(min, max, IM_COL32(0, 0, 0, 255), 2.0f);
	draw->AddLine(ImVec2(max.x, min.y), ImVec2(min.x, max.y), IM_COL32(0, 0, 0, 255), 2.0f);
}

static void DrawAsciiidCompiledAtlasGlyphCell(
	ImDrawList* draw,
	const ImVec2& min,
	const ImVec2& size,
	const uint8_t compiled_atlas_pixels[kAsciiidCompiledAtlasMaxCellPx * kAsciiidCompiledAtlasMaxCellPx],
	int cell_px,
	const uint8_t* fg)
{
	if (cell_px <= 0 || cell_px > kAsciiidCompiledAtlasMaxCellPx)
		return;
	const float pad = 2.0f;
	const float cell_w = (size.x - pad * 2.0f) / (float)cell_px;
	const float cell_h = (size.y - pad * 2.0f) / (float)cell_px;
	const ImU32 ink = IM_COL32(fg[0], fg[1], fg[2], 255);
	for (int y = 0; y < cell_px; y++)
	{
		int x = 0;
		while (x < cell_px)
		{
			while (x < cell_px && !compiled_atlas_pixels[y * kAsciiidCompiledAtlasMaxCellPx + x])
				x++;
			if (x >= cell_px)
				break;
			const int run_x0 = x;
			while (x < cell_px && compiled_atlas_pixels[y * kAsciiidCompiledAtlasMaxCellPx + x])
				x++;
			const int run_x1 = x;
			if (run_x1 <= run_x0)
				continue;
			ImVec2 a(min.x + pad + run_x0 * cell_w, min.y + pad + y * cell_h);
			ImVec2 b(min.x + pad + run_x1 * cell_w, min.y + pad + (y + 1) * cell_h);
			draw->AddRectFilled(a, b, ink);
		}
	}
}

static bool DrawAsciiidExtendedGlyphButton(GlyphId glyph_id, const ImVec2& size, const uint8_t* fg, const uint8_t* bg)
{
	AsciiidExtendedGlyphVisualSample visual = SampleAsciiidExtendedGlyphVisual(glyph_id);
	ImGui::InvisibleButton("extended_glyph", size);
	const bool clicked = ImGui::IsItemClicked(0);
	if (!ImGui::IsItemVisible())
		return clicked;

	ImDrawList* draw = ImGui::GetWindowDrawList();
	ImVec2 min = ImGui::GetItemRectMin();
	ImVec2 max = ImGui::GetItemRectMax();
	ImU32 bg_col = visual.supported
		? IM_COL32(bg[0], bg[1], bg[2], 255)
		: IM_COL32(255, 0, 0, 255);
	ImU32 border_col = ImGui::IsItemHovered()
		? IM_COL32(255, 255, 255, 220)
		: IM_COL32(0, 0, 0, 180);
	draw->AddRectFilled(min, max, bg_col);

	if (visual.supported)
	{
		DrawAsciiidCompiledAtlasGlyphCell(draw, min, size, visual.compiled_atlas_pixels, visual.compiled_atlas_cell_px, fg);
	}
	else
	{
		DrawAsciiidUnknownGlyphMarker(draw, min, max);
	}
	draw->AddRect(min, max, border_col);
	return clicked;
}

static bool DrawAsciiidExtendedGlyphPresetStripButton(const AsciiidExtendedGlyphPreset& preset, const ImVec2& size, const uint8_t* fg, const uint8_t* bg)
{
	ImGui::InvisibleButton("extended_glyph_preset_strip", size);
	const bool clicked = ImGui::IsItemClicked(0);
	if (!ImGui::IsItemVisible())
		return clicked;

	ImDrawList* draw = ImGui::GetWindowDrawList();
	ImVec2 min = ImGui::GetItemRectMin();
	ImVec2 max = ImGui::GetItemRectMax();
	ImU32 bg_col = IM_COL32(bg[0], bg[1], bg[2], 255);
	ImU32 border_col = ImGui::IsItemHovered()
		? IM_COL32(255, 255, 255, 220)
		: IM_COL32(0, 0, 0, 180);
	draw->AddRectFilled(min, max, bg_col);

	const int cells = preset.count > 0 ? preset.count : 1;
	const float cell_w = size.x / (float)cells;
	for (int i = 0; i < preset.count; i++)
	{
		AsciiidExtendedGlyphVisualSample visual = SampleAsciiidExtendedGlyphVisual(preset.glyphs[i]);
		ImVec2 cell_min(min.x + i * cell_w, min.y);
		ImVec2 cell_size(cell_w, size.y);
		if (visual.supported)
			DrawAsciiidCompiledAtlasGlyphCell(draw, cell_min, cell_size, visual.compiled_atlas_pixels, visual.compiled_atlas_cell_px, fg);
		else
		{
			ImVec2 cell_max(cell_min.x + cell_w, cell_min.y + size.y);
			draw->AddRectFilled(cell_min, cell_max, IM_COL32(255, 0, 0, 255));
			DrawAsciiidUnknownGlyphMarker(draw, cell_min, cell_max);
		}
		if (i > 0)
			draw->AddLine(ImVec2(cell_min.x, min.y), ImVec2(cell_min.x, max.y), IM_COL32(0, 0, 0, 120));
	}

	draw->AddRect(min, max, border_col);
	return clicked;
}

static bool AsciiidExtendedPresetIsAdmitted(const AsciiidExtendedGlyphPreset& preset, GlyphId* first_missing)
{
	for (int i = 0; i < preset.count; i++)
	{
		if (!AsciiidManifestSupportsGlyph(preset.glyphs[i]))
		{
			if (first_missing)
				*first_missing = preset.glyphs[i];
			return false;
		}
	}
	if (first_missing)
		*first_missing = GLYPH_ID_NONE;
	return true;
}

static void FormatAsciiidExtendedPresetTooltip(const AsciiidExtendedGlyphPreset& preset, bool admitted, GlyphId first_missing, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;
	int used = 0;
	if (admitted)
	{
		used = snprintf(out, out_size,
			"Apply %d admitted extended GlyphIds to the active material ramp.\n%s\nGlyphIds/fallbacks:",
			preset.count,
			preset.note);
	}
	else
	{
		used = snprintf(out, out_size,
			"Unavailable: GlyphId %u is not admitted by the active manifest.\n%s\nGlyphIds/fallbacks:",
			(unsigned)first_missing,
			preset.note);
	}
	if (used < 0)
	{
		out[0] = '\0';
		return;
	}
	if (used >= out_size)
		used = out_size - 1;
	for (int i = 0; i < preset.count && used < out_size - 1; i++)
	{
		const uint8_t fallback = AsciiidGlyphFallbackByte(preset.glyphs[i]);
		const int wrote = snprintf(out + used, out_size - used, "%s%u/0x%02X",
			i == 0 ? " " : ", ",
			(unsigned)preset.glyphs[i],
			(unsigned)fallback);
		if (wrote < 0)
			break;
		if (wrote >= out_size - used)
		{
			used = out_size - 1;
			break;
		}
		used += wrote;
	}
	out[out_size - 1] = '\0';
}

static bool ApplyAsciiidExtendedPresetToMaterial(int material_id, int preset_index, GlyphId* first_missing);

static void CaptureMaterialGlyphProofState(int mat_id, MatCell out_cells[kMaterialGridRowCount][kMaterialGridColCount], GlyphId out_glyphs[kMaterialGridRowCount * kMaterialGridColCount], bool* out_had_plane)
{
	if (out_had_plane)
		*out_had_plane = mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells;
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			out_cells[row][col] = *MaterialGridCell(mat_id, row, col);
			out_glyphs[row * kMaterialGridColCount + col] = material_glyph_plane_lookup(mat[mat_id].glyph_plane, row, col);
		}
	}
}

static bool MaterialGlyphProofStateEquals(int mat_id, const MatCell cells[kMaterialGridRowCount][kMaterialGridColCount], const GlyphId glyphs[kMaterialGridRowCount * kMaterialGridColCount], bool had_plane)
{
	const bool current_had_plane = mat[mat_id].glyph_plane && mat[mat_id].glyph_plane->cells;
	if (current_had_plane != had_plane)
		return false;
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (memcmp(MaterialGridCellConst(mat_id, row, col), &cells[row][col], sizeof(MatCell)) != 0)
				return false;
			if (material_glyph_plane_lookup(mat[mat_id].glyph_plane, row, col) != glyphs[row * kMaterialGridColCount + col])
				return false;
		}
	}
	return true;
}

static void RestoreMaterialGlyphProofState(int mat_id, const MatCell cells[kMaterialGridRowCount][kMaterialGridColCount], const GlyphId glyphs[kMaterialGridRowCount * kMaterialGridColCount], bool had_plane)
{
	for (int row = 0; row < kMaterialGridRowCount; row++)
		for (int col = 0; col < kMaterialGridColCount; col++)
			*MaterialGridCell(mat_id, row, col) = cells[row][col];

	if (!had_plane)
	{
		material_glyph_plane_free(mat[mat_id].glyph_plane);
		mat[mat_id].glyph_plane = NULL;
	}
	else if (EnsureMaterialGlyphPlane(mat_id))
	{
		for (int i = 0; i < kMaterialGridRowCount * kMaterialGridColCount; i++)
		{
			mat[mat_id].glyph_plane->cells[i] = glyphs[i];
			// [FL-4131] Restore coverage too so post-proof engine renders
			// resolve display glyphs instead of falling closed to '!'.
			uint16_t coverage = 0;
			if (glyph_id_is_extended(glyphs[i]))
				AsciiidManifestLookupCoverage(glyphs[i], &coverage);
			mat[mat_id].glyph_plane->coverage[i] = coverage;
		}
	}
	MaterialGridCommit(mat_id);
}

// FL-4131 P6: forward-declare the cell counters so the workspace renderer
// (which is defined here, before the counters' bodies) can call them.
static int CountActiveMaterialExtendedGlyphCells(int mat_id);
static int CountMaterialExtendedGlyphCells();

// FL-4131 P4/P5: file-scope helpers backing the extended material workspace.
// Hoisted out of my_render so they're reachable from render_materials_owner,
// which lives in a lambda whose lexical scope does not see lambdas declared
// inside the FONT-tab block.
static void AsciiidApplyExtendedPresetSequence(const GlyphId* glyphs, int count)
{
	if (!glyphs || count <= 0)
		return;
	URDO_Open();
	URDO_Material(active_material);
	for (int row = 0; row < MaterialGridRows(); row++)
	{
		for (int col = 0; col < MaterialGridCols(); col++)
			MaterialGridSetGlyphId(active_material, row, col, glyphs[col * count / MaterialGridCols()]);
	}
	MaterialGridCommit(active_material);
	URDO_Close();
}

static void AsciiidRenderExtendedMaterialWorkspace(float swatch_unit)
{
	const GlyphManifest* manifest = AsciiidExtendedPickerManifest();
	if (!manifest)
	{
		ImGui::TextDisabled("Extended manifest unavailable — extended-glyph material edits disabled.");
		return;
	}

	ImGui::Separator();
	ImGui::Text("Extended Material Workspace");
	ImGui::TextDisabled("Active GlyphId: %u  (browse via Character tab)", (unsigned)active_glyph_id);

	// FL-4131 P6: save/reload sidecar UX. Surface the exact identity tuple
	// the next SAVE_MAP will emit (or remove) so the operator can verify
	// what's about to be written without grepping the JSON afterwards.
	{
		const int active_count = CountActiveMaterialExtendedGlyphCells(active_material);
		const int total_count = CountMaterialExtendedGlyphCells();
		const char* hash_short = kAsciiidExtendedGlyphManifestHash;
		const char* manifest_pack = "material.additive.v1";
		ImGui::Spacing();
		ImGui::TextDisabled("On-save sidecar:");
		ImGui::BulletText("Extended cells in this material: %d", active_count);
		ImGui::BulletText("Total extended cells in map: %d", total_count);
		if (total_count > 0)
			ImGui::BulletText("SAVE_MAP will emit <map>.glyph_profile.json");
		else
			ImGui::BulletText("SAVE_MAP will remove <map>.glyph_profile.json (no extended cells)");
		ImGui::BulletText("content_pack_id: %s", manifest_pack);
		char hash_clip[18];
		snprintf(hash_clip, sizeof(hash_clip), "%.16s", hash_short ? hash_short : "");
		ImGui::BulletText("manifest_hash: %s...", hash_clip);
		ImGui::BulletText("atlas: assets/glyphs/atlases/%s.atlas_of_atlases.json", manifest_pack);
	}

	if (ImGui::Button("Fill Material with active GlyphId##extended_glyph_fill_material"))
	{
		URDO_Open();
		URDO_Material(active_material);
		for (int row = 0; row < MaterialGridRows(); row++)
			for (int col = 0; col < MaterialGridCols(); col++)
				MaterialGridSetGlyphId(active_material, row, col, active_glyph_id);
		MaterialGridCommit(active_material);
		URDO_Close();
	}
	{
		ImVec2 item_min = ImGui::GetItemRectMin();
		ImVec2 item_max = ImGui::GetItemRectMax();
		g_asciiid_extended_picker_fill_rect.valid = true;
		g_asciiid_extended_picker_fill_rect.x0 = (int)item_min.x;
		g_asciiid_extended_picker_fill_rect.y0 = (int)item_min.y;
		g_asciiid_extended_picker_fill_rect.x1 = (int)item_max.x;
		g_asciiid_extended_picker_fill_rect.y1 = (int)item_max.y;
	}

	ImGui::Spacing();
	ImGui::Separator();
	ImGui::Text("Extended Glyph Presets");
	ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(3, 2));
	const float preset_swatch_h = fmaxf(swatch_unit * 1.4f, 22.0f);
	const float preset_cell_w = fmaxf(swatch_unit * 1.0f, 18.0f);
	const float preset_wrap_w = ImGui::GetContentRegionAvail().x;
	float row_used = 0.0f;
	for (int preset_index = 0; preset_index < kAsciiidExtendedGlyphPresetCount; preset_index++)
	{
		const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
		GlyphId first_missing = GLYPH_ID_NONE;
		const bool admitted = AsciiidExtendedPresetIsAdmitted(preset, &first_missing);
		if (!admitted)
		{
			ImGui::PushItemFlag(ImGuiItemFlags_Disabled, true);
			ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.45f);
		}
		ImGui::PushID(preset_index + 42000);
		uint8_t swatch_fg[3] = { 235, 235, 235 };
		uint8_t swatch_bg[3] = { 18, 18, 18 };
		const int visible_cells = preset.count > 0 ? preset.count : 1;
		const float preset_w = preset_cell_w * visible_cells;
		if (preset_index > 0 && row_used > 0.0f && row_used + preset_w <= preset_wrap_w)
			ImGui::SameLine();
		else
			row_used = 0.0f;
		// FL-4131 UX: one material preset button is a compact strip of the
		// preset's actual glyphs, matching the older Glyph Presets contract.
		bool apply_preset = DrawAsciiidExtendedGlyphPresetStripButton(
			preset, ImVec2(preset_w, preset_swatch_h), swatch_fg, swatch_bg);
		if (ImGui::IsItemHovered())
		{
			char tooltip[512];
			FormatAsciiidExtendedPresetTooltip(preset, admitted, first_missing, tooltip, sizeof(tooltip));
			ImGui::SetTooltip("[%s] %s\n%s", preset.material, preset.name, tooltip);
		}
		{
			ImVec2 rect_min = ImGui::GetItemRectMin();
			ImVec2 rect_max = ImGui::GetItemRectMax();
			g_asciiid_preset_ui_rects[preset_index].valid = true;
			g_asciiid_preset_ui_rects[preset_index].x0 = (int)rect_min.x;
			g_asciiid_preset_ui_rects[preset_index].y0 = (int)rect_min.y;
			g_asciiid_preset_ui_rects[preset_index].x1 = (int)rect_max.x;
			g_asciiid_preset_ui_rects[preset_index].y1 = (int)rect_max.y;
		}
		if (apply_preset)
			AsciiidApplyExtendedPresetSequence(preset.glyphs, preset.count);
		ImGui::PopID();
		row_used += preset_w + ImGui::GetStyle().ItemSpacing.x;
		if (!admitted)
		{
			ImGui::PopStyleVar();
			ImGui::PopItemFlag();
		}
	}
	ImGui::PopStyleVar();

	ImGui::Spacing();
	ImGui::TextDisabled("Per-cell paint: enable 'paint glyph' in the brush, then click cells in Raw Cells.");
}

static bool RunFl4131AsciiidExtendedMaterialProof()
{
	const GlyphManifest* manifest = AsciiidExtendedPickerManifest();
	const GlyphId proof_glyphs[] = {512, 513, 539, 543};
	const int preset_count = (int)(sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0]));
	const int proof_material_id = 1;
	const int proof_row = 2;
	const int proof_col = 0;
	const int proof_count = (int)(sizeof(proof_glyphs) / sizeof(proof_glyphs[0]));
	struct PresetApplyReceipt
	{
		bool applied;
		int extended_cells;
		GlyphId row0_glyph_ids[kMaterialGridColCount];
		uint8_t row0_fallback_bytes[kMaterialGridColCount];
	};
	PresetApplyReceipt preset_receipts[sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0])] = {};
	MatCell original_cells[kMaterialGridRowCount][kMaterialGridColCount];
	GlyphId original_glyphs[kMaterialGridRowCount * kMaterialGridColCount];
	MatCell applied_cells[kMaterialGridRowCount][kMaterialGridColCount];
	GlyphId applied_glyphs[kMaterialGridRowCount * kMaterialGridColCount];
	bool original_had_plane = false;
	bool applied_had_plane = false;
	bool manifest_loaded = manifest != NULL;
	bool admitted_all = manifest_loaded;
	bool preset_catalog_admitted_all = manifest_loaded;
	bool all_preset_apply_passed = manifest_loaded;
	bool rejected_unlisted = manifest_loaded && !AsciiidManifestSupportsGlyph(768);
	bool fallback_bytes_ok = true;
	bool sidecar_ids_ok = true;
	bool visual_coverage_render_ok = false;
	bool visual_display_not_fallback = false;
	bool unknown_diagnostic_render_ok = false;
	bool material_roundtrip_ok = true;
	bool undo_restored = false;
	bool redo_restored = false;
	bool restored_original = false;
	uint8_t observed_fallback_bytes[4] = {0, 0, 0, 0};
	AsciiidExtendedGlyphVisualSample supported_visual = {};
	AsciiidExtendedGlyphVisualSample unknown_visual = {};

	CaptureMaterialGlyphProofState(proof_material_id, original_cells, original_glyphs, &original_had_plane);

	if (manifest_loaded)
	{
		for (int i = 0; i < proof_count; i++)
			admitted_all = admitted_all && AsciiidManifestSupportsGlyph(proof_glyphs[i]);
		for (int preset_index = 0; preset_index < preset_count; preset_index++)
		{
			GlyphId first_missing = GLYPH_ID_NONE;
			preset_catalog_admitted_all = preset_catalog_admitted_all && AsciiidExtendedPresetIsAdmitted(kAsciiidExtendedGlyphPresets[preset_index], &first_missing);
		}
	}

	if (manifest_loaded && preset_catalog_admitted_all)
	{
		for (int preset_index = 0; preset_index < preset_count; preset_index++)
		{
			GlyphId first_missing = GLYPH_ID_NONE;
			PresetApplyReceipt& receipt = preset_receipts[preset_index];
			receipt.applied = ApplyAsciiidExtendedPresetToMaterial(proof_material_id, preset_index, &first_missing);
			all_preset_apply_passed = all_preset_apply_passed && receipt.applied && first_missing == GLYPH_ID_NONE;
			if (!receipt.applied)
				continue;
			const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
			for (int row = 0; row < kMaterialGridRowCount; row++)
			{
				for (int col = 0; col < kMaterialGridColCount; col++)
				{
					const GlyphId expected = preset.glyphs[col * preset.count / kMaterialGridColCount];
					const GlyphId actual = MaterialGridGlyphIdConst(proof_material_id, row, col);
					const MatCell* cell = MaterialGridCellConst(proof_material_id, row, col);
					all_preset_apply_passed = all_preset_apply_passed && actual == expected && cell->gl == AsciiidGlyphFallbackByte(expected);
					if (actual > 255 && !glyph_id_is_sentinel(actual))
						receipt.extended_cells++;
					if (row == 0)
					{
						receipt.row0_glyph_ids[col] = actual;
						receipt.row0_fallback_bytes[col] = cell->gl;
					}
				}
			}
			all_preset_apply_passed = all_preset_apply_passed && receipt.extended_cells == kMaterialGridRowCount * kMaterialGridColCount;
		}
	}

	RestoreMaterialGlyphProofState(proof_material_id, original_cells, original_glyphs, original_had_plane);
	URDO_Purge();

	if (manifest_loaded && admitted_all && rejected_unlisted && all_preset_apply_passed)
	{
		URDO_Purge();
		URDO_Open();
		URDO_Material(proof_material_id);
		for (int i = 0; i < proof_count; i++)
			MaterialGridSetGlyphId(proof_material_id, proof_row, proof_col + i, proof_glyphs[i]);
		MaterialGridCommit(proof_material_id);
		URDO_Close();

		for (int i = 0; i < proof_count; i++)
		{
			const uint8_t expected_fallback = AsciiidGlyphFallbackByte(proof_glyphs[i]);
			const MatCell* cell = MaterialGridCellConst(proof_material_id, proof_row, proof_col + i);
			observed_fallback_bytes[i] = cell->gl;
			fallback_bytes_ok = fallback_bytes_ok && (cell->gl == expected_fallback);
			sidecar_ids_ok = sidecar_ids_ok && (MaterialGridGlyphIdConst(proof_material_id, proof_row, proof_col + i) == proof_glyphs[i]);
		}
		supported_visual = SampleAsciiidExtendedGlyphVisual(proof_glyphs[0]);
		unknown_visual = SampleAsciiidExtendedGlyphVisual(768);
		visual_coverage_render_ok =
			supported_visual.supported &&
			supported_visual.coverage != 0 &&
			supported_visual.foreground_pixels > 0 &&
			supported_visual.background_pixels > 0 &&
			supported_visual.diagnostic_pixels == 0;
		visual_display_not_fallback =
			supported_visual.display_glyph != 0 &&
			supported_visual.display_glyph != observed_fallback_bytes[0];
		unknown_diagnostic_render_ok =
			!unknown_visual.supported &&
			unknown_visual.diagnostic_pixels > 0 &&
			unknown_visual.display_glyph == '!';
		material_roundtrip_ok = fallback_bytes_ok && sidecar_ids_ok && mat[proof_material_id].glyph_plane && mat[proof_material_id].glyph_plane->cells;

		CaptureMaterialGlyphProofState(proof_material_id, applied_cells, applied_glyphs, &applied_had_plane);
		URDO_Undo(1);
		undo_restored = MaterialGlyphProofStateEquals(proof_material_id, original_cells, original_glyphs, original_had_plane);
		URDO_Redo(1);
		redo_restored = MaterialGlyphProofStateEquals(proof_material_id, applied_cells, applied_glyphs, applied_had_plane);
	}

	RestoreMaterialGlyphProofState(proof_material_id, original_cells, original_glyphs, original_had_plane);
	restored_original = MaterialGlyphProofStateEquals(proof_material_id, original_cells, original_glyphs, original_had_plane);
	URDO_Purge();

	const bool pass = manifest_loaded && admitted_all && preset_catalog_admitted_all && all_preset_apply_passed && rejected_unlisted && material_roundtrip_ok && visual_coverage_render_ok && visual_display_not_fallback && unknown_diagnostic_render_ok && undo_restored && redo_restored && restored_original;
	printf("[FL4131_ASCIIID_PROOF_START]\n");
	printf("{\n");
	printf("  \"schema\": \"fl4131_asciiid_extended_material_roundtrip.v1\",\n");
	printf("  \"verdict\": \"%s\",\n", pass ? "PASS" : "FAIL");
	printf("  \"manifest_path\": \"%s\",\n", kAsciiidExtendedGlyphManifestPath);
	printf("  \"manifest_hash\": \"%s\",\n", kAsciiidExtendedGlyphManifestHash);
	printf("  \"manifest_loaded\": %s,\n", manifest_loaded ? "true" : "false");
	printf("  \"content_pack_id\": \"%s\",\n", manifest ? manifest->content_pack_id : "");
	printf("  \"entry_count\": %d,\n", manifest ? manifest->entry_count : 0);
	printf("  \"fallback_glyph_id\": %u,\n", (unsigned)(manifest ? manifest->fallback_glyph_id : GLYPH_ID_NONE));
	printf("  \"admitted_all\": %s,\n", admitted_all ? "true" : "false");
	printf("  \"preset_catalog_admitted_all\": %s,\n", preset_catalog_admitted_all ? "true" : "false");
	printf("  \"all_preset_apply_passed\": %s,\n", all_preset_apply_passed ? "true" : "false");
	printf("  \"applied_preset_count\": %d,\n", preset_count);
	printf("  \"applied_presets\": [\n");
	for (int preset_index = 0; preset_index < preset_count; preset_index++)
	{
		const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
		const PresetApplyReceipt& receipt = preset_receipts[preset_index];
		printf("    {\"index\": %d, \"family\": \"%s\", \"name\": \"%s\", \"count\": %d, \"applied\": %s, \"extended_cells\": %d, \"row0_glyph_ids\": [",
			preset_index,
			preset.material,
			preset.name,
			preset.count,
			receipt.applied ? "true" : "false",
			receipt.extended_cells);
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			printf("%u", (unsigned)receipt.row0_glyph_ids[col]);
		}
		printf("], \"row0_fallback_bytes\": [");
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			printf("%u", (unsigned)receipt.row0_fallback_bytes[col]);
		}
		printf("]}%s\n", preset_index + 1 < preset_count ? "," : "");
	}
	printf("  ],\n");
	printf("  \"rejected_unlisted_glyph_768\": %s,\n", rejected_unlisted ? "true" : "false");
	printf("  \"material_id\": %d,\n", proof_material_id);
	printf("  \"row\": %d,\n", proof_row);
	printf("  \"glyph_ids\": [%u,%u,%u,%u],\n", (unsigned)proof_glyphs[0], (unsigned)proof_glyphs[1], (unsigned)proof_glyphs[2], (unsigned)proof_glyphs[3]);
	printf("  \"fallback_bytes\": [%u,%u,%u,%u],\n",
		(unsigned)observed_fallback_bytes[0],
		(unsigned)observed_fallback_bytes[1],
		(unsigned)observed_fallback_bytes[2],
		(unsigned)observed_fallback_bytes[3]);
	printf("  \"matcell_fallback_preserved\": %s,\n", fallback_bytes_ok ? "true" : "false");
	printf("  \"glyph_plane_roundtrip\": %s,\n", material_roundtrip_ok ? "true" : "false");
	printf("  \"visual_sample\": {\"glyph_id\": %u, \"supported\": %s, \"coverage\": %u, \"foreground_pixels\": %d, \"background_pixels\": %d, \"display_glyph\": %u},\n",
		(unsigned)proof_glyphs[0],
		supported_visual.supported ? "true" : "false",
		(unsigned)supported_visual.coverage,
		supported_visual.foreground_pixels,
		supported_visual.background_pixels,
		(unsigned)supported_visual.display_glyph);
	printf("  \"visual_coverage_render_ok\": %s,\n", visual_coverage_render_ok ? "true" : "false");
	printf("  \"visual_display_not_fallback_bytes\": %s,\n", visual_display_not_fallback ? "true" : "false");
	printf("  \"unknown_visual_sample\": {\"glyph_id\": 768, \"supported\": %s, \"diagnostic_pixels\": %d, \"display_glyph\": %u},\n",
		unknown_visual.supported ? "true" : "false",
		unknown_visual.diagnostic_pixels,
		(unsigned)unknown_visual.display_glyph);
	printf("  \"unknown_diagnostic_render_ok\": %s,\n", unknown_diagnostic_render_ok ? "true" : "false");
	printf("  \"undo_restored\": %s,\n", undo_restored ? "true" : "false");
	printf("  \"redo_restored\": %s,\n", redo_restored ? "true" : "false");
	printf("  \"restored_original\": %s\n", restored_original ? "true" : "false");
	printf("}\n");
	printf("[FL4131_ASCIIID_PROOF_END]\n");
	fflush(stdout);
	return pass;
}

static bool BuildMaterialGlyphSidecarPath(const char* map_path, char* out, int out_size)
{
	if (!map_path || !map_path[0] || !out || out_size <= 0)
		return false;
	int written = snprintf(out, out_size, "%s.glyph_profile.json", map_path);
	return written > 0 && written < out_size;
}

static bool ReadWholeFile(const char* path, char** out_text)
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
	if (size < 0)
	{
		fclose(f);
		return false;
	}
	if (fseek(f, 0, SEEK_SET) != 0)
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

static int CountMaterialExtendedGlyphCells()
{
	int count = 0;
	for (int mat_id = 0; mat_id < kPaletteCount; mat_id++)
	{
		if (!mat[mat_id].glyph_plane || !mat[mat_id].glyph_plane->cells)
			continue;
		for (int i = 0; i < kMaterialGridRowCount * kMaterialGridColCount; i++)
		{
			GlyphId glyph_id = mat[mat_id].glyph_plane->cells[i];
			if (glyph_id > 255 && !glyph_id_is_sentinel(glyph_id))
				count++;
		}
	}
	return count;
}

static bool MaterialHasExtendedGlyphCells(int mat_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount || !mat[mat_id].glyph_plane || !mat[mat_id].glyph_plane->cells)
		return false;
	for (int i = 0; i < kMaterialGridRowCount * kMaterialGridColCount; i++)
	{
		GlyphId glyph_id = mat[mat_id].glyph_plane->cells[i];
		if (glyph_id > 255 && !glyph_id_is_sentinel(glyph_id))
			return true;
	}
	return false;
}

// FL-4131 P6: per-material extended cell count for the save-UX readout.
static int CountActiveMaterialExtendedGlyphCells(int mat_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount || !mat[mat_id].glyph_plane || !mat[mat_id].glyph_plane->cells)
		return 0;
	int count = 0;
	for (int i = 0; i < kMaterialGridRowCount * kMaterialGridColCount; i++)
	{
		GlyphId glyph_id = mat[mat_id].glyph_plane->cells[i];
		if (glyph_id > 255 && !glyph_id_is_sentinel(glyph_id))
			count++;
	}
	return count;
}

static bool SaveMaterialGlyphSidecarForMap(const char* map_path)
{
	char sidecar_path[kEditorPathMax];
	if (!BuildMaterialGlyphSidecarPath(map_path, sidecar_path, sizeof(sidecar_path)))
	{
		SetLastSaveMapError("Material glyph sidecar path is too long");
		return false;
	}

	const int total_cells = CountMaterialExtendedGlyphCells();
	if (total_cells == 0)
	{
		remove(sidecar_path);
		return true;
	}

	char tmp_path[kEditorPathMax];
	int tmp_written = snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", sidecar_path);
	if (tmp_written < 0 || tmp_written >= (int)sizeof(tmp_path))
	{
		SetLastSaveMapError("Material glyph sidecar temporary path is too long");
		return false;
	}
	FILE* f = fopen(tmp_path, "wb");
	if (!f)
	{
		SetLastSaveMapErrno("Could not open material glyph sidecar temporary path");
		return false;
	}

	fprintf(f, "{\n");
	fprintf(f, "  \"sidecar_version\": 1,\n");
	fprintf(f, "  \"profile_kind\": \"extended_material_glyph_v1\",\n");
	fprintf(f, "  \"content_pack_id\": \"material.additive.v1\",\n");
	fprintf(f, "  \"glyph_manifest_hash\": \"%s\",\n", kAsciiidExtendedGlyphManifestHash);
	fprintf(f, "  \"glyph_manifest_path\": \"%s\",\n", kAsciiidExtendedGlyphManifestPath);
	fprintf(f, "  \"material_entries\": [\n");
	bool first_entry = true;
	for (int mat_id = 0; mat_id < kPaletteCount; mat_id++)
	{
		if (!MaterialHasExtendedGlyphCells(mat_id))
			continue;
		if (!first_entry)
			fprintf(f, ",\n");
		first_entry = false;
		fprintf(f, "    {\n");
		fprintf(f, "      \"material_id\": %d,\n", mat_id);
		fprintf(f, "      \"cells\": [\n");
		bool first_cell = true;
		for (int row = 0; row < kMaterialGridRowCount; row++)
		{
			for (int col = 0; col < kMaterialGridColCount; col++)
			{
				GlyphId glyph_id = mat[mat_id].glyph_plane->cells[row * kMaterialGridColCount + col];
				if (glyph_id <= 255 || glyph_id_is_sentinel(glyph_id))
					continue;
				if (!first_cell)
					fprintf(f, ",\n");
				first_cell = false;
				fprintf(f, "        {\"elev\": %d, \"shade\": %d, \"glyph_id\": %u}", row, col, (unsigned)glyph_id);
			}
		}
		fprintf(f, "\n      ]\n");
		fprintf(f, "    }");
	}
	fprintf(f, "\n  ]\n");
	fprintf(f, "}\n");

	bool ok = true;
	if (fflush(f) != 0 || ferror(f))
	{
		SetLastSaveMapError("Failed while writing material glyph sidecar");
		ok = false;
	}
	if (fclose(f) != 0)
	{
		SetLastSaveMapErrno("Material glyph sidecar close failed");
		ok = false;
	}
	if (!ok)
	{
		remove(tmp_path);
		return false;
	}
	if (rename(tmp_path, sidecar_path) != 0)
	{
		SetLastSaveMapErrno("Could not replace material glyph sidecar");
		remove(tmp_path);
		return false;
	}
	return true;
}

static bool LoadMaterialGlyphSidecarForMap(const char* map_path, const char* prefix)
{
	char sidecar_path[kEditorPathMax];
	if (!BuildMaterialGlyphSidecarPath(map_path, sidecar_path, sizeof(sidecar_path)))
		return false;
	char* text = NULL;
	if (!ReadWholeFile(sidecar_path, &text))
	{
		if (prefix)
			printf("%s Material glyph sidecar: none\n", prefix);
		return true;
	}

	MaterialSidecar sidecar = {};
	GlyphManifest manifest = {};
	GlyphManifestError manifest_err = GLYPH_MANIFEST_OK;
	char errbuf[512] = "";
	bool ok = false;
	if (material_sidecar_parse(text, &sidecar, errbuf, sizeof(errbuf)) != 0 ||
		material_sidecar_validate(&sidecar, errbuf, sizeof(errbuf)) != 0)
	{
		if (prefix)
			printf("%s Error: material glyph sidecar invalid: %s\n", prefix, errbuf[0] ? errbuf : "unknown error");
		goto done;
	}

	manifest_err = glyph_manifest_load_and_verify(
		sidecar.glyph_manifest_path,
		sidecar.glyph_manifest_hash,
		&manifest,
		errbuf,
		sizeof(errbuf));
	if (manifest_err != GLYPH_MANIFEST_OK)
	{
		if (prefix)
			printf("%s Error: material glyph manifest rejected: %s %s\n", prefix, glyph_manifest_error_name(manifest_err), errbuf);
		goto done;
	}

	for (int entry_i = 0; entry_i < sidecar.entry_count; entry_i++)
	{
		const MaterialSidecarEntry* entry = &sidecar.entries[entry_i];
		if (!EnsureMaterialGlyphPlane(entry->material_id))
			goto done_manifest;
		for (int cell_i = 0; cell_i < entry->cell_count; cell_i++)
		{
			const MaterialSidecarCell* cell = &entry->cells[cell_i];
			if (!glyph_manifest_is_admitted(&manifest, cell->glyph_id))
			{
				if (prefix)
					printf("%s Error: material glyph sidecar has unadmitted GlyphId %u\n", prefix, (unsigned)cell->glyph_id);
				goto done_manifest;
			}
			MaterialGridSetGlyphId(entry->material_id, cell->elev, cell->shade, cell->glyph_id);
		}
		MaterialGridCommit(entry->material_id);
	}
	ok = true;
	if (prefix)
		printf("%s Material glyph sidecar loaded: entries=%d cells=%d\n", prefix, sidecar.entry_count, CountMaterialExtendedGlyphCells());

done_manifest:
	glyph_manifest_free(&manifest);
done:
	material_sidecar_free(&sidecar);
	free(text);
	return ok;
}

static bool ApplyAsciiidExtendedPresetToMaterial(int material_id, int preset_index, GlyphId* first_missing)
{
	if (first_missing)
		*first_missing = GLYPH_ID_NONE;
	if (material_id < 0 || material_id >= kPaletteCount)
		return false;
	const int preset_count = (int)(sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0]));
	if (preset_index < 0 || preset_index >= preset_count)
		return false;
	const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
	if (!AsciiidExtendedPresetIsAdmitted(preset, first_missing))
		return false;
	URDO_Open();
	URDO_Material(material_id);
	for (int row = 0; row < MaterialGridRows(); row++)
		for (int col = 0; col < MaterialGridCols(); col++)
			MaterialGridSetGlyphId(material_id, row, col, preset.glyphs[col * preset.count / MaterialGridCols()]);
	MaterialGridCommit(material_id);
	URDO_Close();
	return true;
}

static void PrintMaterialGlyphIdsJson(int material_id)
{
	printf("{\"material_id\":%d,\"glyph_ids\":[", material_id);
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		if (row)
			printf(",");
		printf("[");
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			printf("%u", (unsigned)MaterialGridGlyphIdConst(material_id, row, col));
		}
		printf("]");
	}
	printf("],\"fallback_bytes\":[");
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		if (row)
			printf(",");
		printf("[");
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			printf("%u", (unsigned)MaterialGridCellConst(material_id, row, col)->gl);
		}
		printf("]");
	}
	printf("]}\n");
}

static void PrintMaterialPreviewResolveJson(int material_id)
{
	printf("{\"material_id\":%d,\"preview_glyphs\":[", material_id);
	int extended_cells = 0;
	int coverage_cells = 0;
	int diagnostic_cells = 0;
	int display_not_fallback = 0;
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		if (row)
			printf(",");
		printf("[");
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			const GlyphId glyph_id = MaterialGridGlyphIdConst(material_id, row, col);
			const MatCell* cell = MaterialGridCellConst(material_id, row, col);
			uint8_t preview_gl = cell->gl;
			if (glyph_id_is_extended(glyph_id))
			{
				extended_cells++;
				uint16_t coverage = material_glyph_plane_lookup_coverage(mat[material_id].glyph_plane, row, col);
				if (coverage == 0)
					AsciiidManifestLookupCoverage(glyph_id, &coverage);
				if (coverage == 0)
				{
					preview_gl = '!';
					diagnostic_cells++;
				}
				else
				{
					preview_gl = material_glyph_plane_coverage_display_glyph(coverage);
					coverage_cells++;
				}
				if (preview_gl != cell->gl)
					display_not_fallback++;
			}
			printf("%u", (unsigned)preview_gl);
		}
		printf("]");
	}
	printf("],\"fallback_bytes\":[");
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		if (row)
			printf(",");
		printf("[");
		for (int col = 0; col < kMaterialGridColCount; col++)
		{
			if (col)
				printf(",");
			printf("%u", (unsigned)MaterialGridCellConst(material_id, row, col)->gl);
		}
		printf("]");
	}
	printf("],\"extended_cells\":%d,\"coverage_cells\":%d,\"diagnostic_cells\":%d,\"display_not_fallback\":%d}\n",
		extended_cells,
		coverage_cells,
		diagnostic_cells,
		display_not_fallback);
}

static void CopyMaterialRow(int mat_id, int row, MatCell out[kMaterialGridColCount])
{
	memcpy(out, MaterialGridCell(mat_id, row, 0), sizeof(MatCell) * MaterialGridCols());
}

static bool MaterialRowEquals(const MatCell lhs[kMaterialGridColCount], const MatCell rhs[kMaterialGridColCount])
{
	return memcmp(lhs, rhs, sizeof(MatCell) * MaterialGridCols()) == 0;
}

static void ScaleColorToLumaTarget(const uint8_t src[3], float target_luma01, uint8_t out[3])
{
	target_luma01 = Clamp01(target_luma01);
	float r = src[0] / 255.0f;
	float g = src[1] / 255.0f;
	float b = src[2] / 255.0f;
	float current_luma = RgbLuminance01(src);
	if (current_luma < 0.01f || RgbSaturation01(src) < NEAR_NEUTRAL_SAT_THRESHOLD)
	{
		uint8_t gray = (uint8_t)roundf(target_luma01 * 255.0f);
		out[0] = gray;
		out[1] = gray;
		out[2] = gray;
		return;
	}

	float scale = target_luma01 / current_luma;
	out[0] = (uint8_t)roundf(fminf(r * scale, 1.0f) * 255.0f);
	out[1] = (uint8_t)roundf(fminf(g * scale, 1.0f) * 255.0f);
	out[2] = (uint8_t)roundf(fminf(b * scale, 1.0f) * 255.0f);
}

static float RowChannelPeakPercentFromCells(int mat_id, int row, bool foreground)
{
	float max_luma = 0.0f;
	for (int col = 0; col < MaterialGridCols(); col++)
	{
		const MatCell* cell = MaterialGridCellConst(mat_id, row, col);
		const uint8_t* rgb = foreground ? cell->fg : cell->bg;
		float luma = RgbLuminance01(rgb);
		if (luma > max_luma)
			max_luma = luma;
	}
	return max_luma * 100.0f;
}

static void RefreshMaterialRowPeakCache(int mat_id)
{
	if (mat_id < 0 || mat_id >= kPaletteCount || g_material_row_peak_cache_valid[mat_id])
		return;

	for (int row = 0; row < MaterialGridRows(); row++)
	{
		g_material_row_peak_cache[mat_id][row][0] = RowChannelPeakPercentFromCells(mat_id, row, false);
		g_material_row_peak_cache[mat_id][row][1] = RowChannelPeakPercentFromCells(mat_id, row, true);
	}
	g_material_row_peak_cache_valid[mat_id] = true;
}

static float CachedRowChannelPeakPercent(int mat_id, int row, bool foreground)
{
	if (row < 0 || row >= kMaterialGridRowCount)
		return 0.0f;
	RefreshMaterialRowPeakCache(mat_id);
	return g_material_row_peak_cache[mat_id][row][foreground ? 1 : 0];
}

static void InvalidateAllMaterialRowPeakCaches()
{
	memset(g_material_row_peak_cache_valid, 0, sizeof(g_material_row_peak_cache_valid));
}

static void ApplyRowChannelPercentFromSnapshot(int mat_id, int row, const MatCell snapshot[kMaterialGridColCount], bool foreground, float percent)
{
	float peak_luma = Clamp01(percent / 100.0f);
	for (int col = 0; col < MaterialGridCols(); col++)
	{
		float t = MaterialGridCols() > 1 ? (float)col / (float)(MaterialGridCols() - 1) : 1.0f;
		float target_luma = peak_luma * (0.08f + 0.84f * t);
		MatCell* dst = MaterialGridCell(mat_id, row, col);
		const uint8_t* src = foreground ? snapshot[col].fg : snapshot[col].bg;
		uint8_t remapped[3];
		ScaleColorToLumaTarget(src, target_luma, remapped);
		if (foreground)
			memcpy(dst->fg, remapped, sizeof(remapped));
		else
			memcpy(dst->bg, remapped, sizeof(remapped));
	}
	MaterialGridCommit(mat_id);
}

static bool PointInRect(const ImVec2& point, const ImVec2& rect_min, const ImVec2& rect_max)
{
	return point.x >= rect_min.x && point.x <= rect_max.x && point.y >= rect_min.y && point.y <= rect_max.y;
}

static Terrain* g_terrain_preview_scene = 0;
static int g_terrain_preview_material = -1;
static const int kTerrainPreviewPatchSpan = 4;
static const int kTerrainPreviewBaseHeight = 0x9800;

static double TerrainPreviewSmoothCircle(double x, double y, double cx, double cy, double radius, double peak)
{
	double dx = x - cx;
	double dy = y - cy;
	double dist = sqrt(dx * dx + dy * dy);
	if (dist >= radius)
		return 0.0;
	double t = 1.0 - dist / radius;
	return peak * t * t * (3.0 - 2.0 * t);
}

static double TerrainPreviewSmoothEllipse(double x, double y, double cx, double cy, double rx, double ry, double peak)
{
	double dx = (x - cx) / rx;
	double dy = (y - cy) / ry;
	double dist2 = dx * dx + dy * dy;
	if (dist2 >= 1.0)
		return 0.0;
	double t = 1.0 - sqrt(dist2);
	return peak * t * t * (3.0 - 2.0 * t);
}

static int TerrainPreviewAccentMaterial(int seed)
{
	int start = seed % kPaletteCount;
	for (int step = 0; step < kPaletteCount; step++)
	{
		int candidate = (start + step) % kPaletteCount;
		if (candidate != active_material)
			return candidate;
	}
	return active_material;
}

static void DeleteTerrainPreviewScene()
{
	if (g_terrain_preview_scene)
	{
		DeleteTerrain(g_terrain_preview_scene);
		g_terrain_preview_scene = 0;
	}
	g_terrain_preview_material = -1;
}

static void RebuildTerrainPreviewScene()
{
	DeleteTerrainPreviewScene();

	Terrain* preview = CreateTerrain();
	if (!preview)
		return;

	const int accent_a = TerrainPreviewAccentMaterial(1);
	const int accent_b = TerrainPreviewAccentMaterial(2);
	const int accent_c = TerrainPreviewAccentMaterial(3);
	const double center = 0.5 * kTerrainPreviewPatchSpan * VISUAL_CELLS;

	for (int py = 0; py < kTerrainPreviewPatchSpan; py++)
	{
		for (int px = 0; px < kTerrainPreviewPatchSpan; px++)
		{
			Patch* patch = AddTerrainPatch(preview, px, py, kTerrainPreviewBaseHeight);
			if (!patch)
				continue;

			uint16_t* height = GetTerrainHeightMap(patch);
			for (int hy = 0; hy <= HEIGHT_CELLS; hy++)
			{
				for (int hx = 0; hx <= HEIGHT_CELLS; hx++)
				{
					double wx = px * VISUAL_CELLS + hx * (double)VISUAL_CELLS / HEIGHT_CELLS;
					double wy = py * VISUAL_CELLS + hy * (double)VISUAL_CELLS / HEIGHT_CELLS;
					double h = kTerrainPreviewBaseHeight;
					h += TerrainPreviewSmoothCircle(wx, wy, center - 3.0, center - 2.0, 8.0, 0x0F00);
					h += TerrainPreviewSmoothCircle(wx, wy, center + 6.0, center - 7.0, 6.0, 0x0900);
					h += TerrainPreviewSmoothCircle(wx, wy, center - 9.0, center + 7.0, 5.5, 0x0600);
					h += TerrainPreviewSmoothEllipse(wx, wy, center + 1.5, center + 6.0, 12.0, 4.0, 0x0500);
					h -= TerrainPreviewSmoothEllipse(wx, wy, center - 11.0, center - 9.0, 5.0, 3.0, 0x0300);
					if (h < 0.0)
						h = 0.0;
					if (h > 65535.0)
						h = 65535.0;
					height[hx + hy * (HEIGHT_CELLS + 1)] = (uint16_t)round(h);
				}
			}
			UpdateTerrainHeightMap(patch);

			uint16_t* visual = GetTerrainVisualMap(patch);
			for (int vy = 0; vy < VISUAL_CELLS; vy++)
			{
				for (int vx = 0; vx < VISUAL_CELLS; vx++)
				{
					int wx = px * VISUAL_CELLS + vx;
					int wy = py * VISUAL_CELLS + vy;
					int mat_id = active_material;
					if (wy < 5)
						mat_id = accent_a;
					if (wx < 5 || (wx > 23 && wy > 17))
						mat_id = accent_b;
					if ((wx > 21 && wy < 10) || (wx > 10 && wx < 19 && wy > 21))
						mat_id = accent_c;

					double cell_h = TerrainPreviewSmoothCircle(wx + 0.5, wy + 0.5, center - 3.0, center - 2.0, 8.0, 1.0);
					cell_h += TerrainPreviewSmoothCircle(wx + 0.5, wy + 0.5, center + 6.0, center - 7.0, 6.0, 0.8);
					uint16_t visual_word = (uint16_t)(mat_id & 0xFF);
					if (cell_h > 0.45 || wy > 18)
						visual_word |= 0x8000;
					visual[vx + vy * VISUAL_CELLS] = visual_word;
				}
			}
			UpdateTerrainVisualMap(patch);
		}
	}

	g_terrain_preview_scene = preview;
	g_terrain_preview_material = active_material;
}

static Terrain* GetTerrainPreviewScene()
{
	if (!g_terrain_preview_scene || g_terrain_preview_material != active_material)
		RebuildTerrainPreviewScene();
	return g_terrain_preview_scene;
}

static bool SaveMapToFile(FILE* f)
{
	if (!f)
	{
		SetLastSaveMapError("Save failed: invalid output file");
		return false;
	}

	if (!SaveTerrain(terrain, f))
	{
		SetLastSaveMapError("SaveTerrain failed");
		return false;
	}

	const size_t bytes_per_material = sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount;
	for (int i = 0; i < kPaletteCount; i++)
	{
		if (fwrite(mat[i].shade, 1, bytes_per_material, f) != bytes_per_material)
		{
			SetLastSaveMapError("Save failed while writing material ramps");
			return false;
		}
	}

	StoreEditorViewAsMapPlayerStart(pos_x, pos_y, pos_z, rot_yaw);
	if (!SaveWorld(world, f))
	{
		SetLastSaveMapError("SaveWorld failed");
		return false;
	}
	if (!SaveEnemyGens(f))
	{
		SetLastSaveMapError("SaveEnemyGens failed");
		return false;
	}
	if (!SaveMinimapMarkers(f))
	{
		SetLastSaveMapError("SaveMinimapMarkers failed");
		return false;
	}
	if (fflush(f) != 0)
	{
		SetLastSaveMapErrno("Save flush failed");
		return false;
	}
	if (ferror(f))
	{
		SetLastSaveMapError("Save failed due to a stream I/O error");
		return false;
	}
	return true;
}

static bool SaveMapToPath(const char* path)
{
	ClearLastSaveMapError();
	if (!path || !path[0])
	{
		SetLastSaveMapError("Save path is empty");
		return false;
	}

	char tmp_path[kEditorPathMax];
	int tmp_written = snprintf(tmp_path, sizeof(tmp_path), "%s.tmp", path);
	if (tmp_written < 0 || tmp_written >= (int)sizeof(tmp_path))
	{
		SetLastSaveMapError("Save path is too long");
		return false;
	}

	remove(tmp_path);

	FILE* f = fopen(tmp_path, "wb");
	if (!f)
	{
		SetLastSaveMapErrno("Could not open temporary save path");
		return false;
	}

	bool ok = SaveMapToFile(f);
	if (fclose(f) != 0)
	{
		if (ok)
			SetLastSaveMapErrno("Save close failed");
		ok = false;
	}
	if (!ok)
	{
		remove(tmp_path);
		return false;
	}
	if (rename(tmp_path, path) != 0)
	{
		SetLastSaveMapErrno("Could not replace destination map");
		remove(tmp_path);
		return false;
	}
	if (!SaveMaterialGlyphSidecarForMap(path))
		return false;

	SetCurrentMapPath(path);
	return true;
}

static void CopyParentDir(char* out, int size, const char* path)
{
	if (!out || size <= 0)
		return;
	out[0] = 0;
	if (!path || !path[0])
		return;

	CopyClamped(out, size, path);
	char* slash = strrchr(out, '/');
	char* backslash = strrchr(out, '\\');
	char* cut = slash;
	if (backslash && (!cut || backslash > cut))
		cut = backslash;
	if (cut)
		*cut = 0;
	else
		out[0] = 0;
}

static void ResetRowShadeContrast()
{
	for (int row = 0; row < kMaterialGridRowCount; row++)
	{
		row_shade_contrast_min[row] = 0;
		row_shade_contrast_max[row] = 15;
	}
}

static const MatCell* mat_shade_cell(int mat_id, int row, int col)
{
	return MaterialGridCellConst(mat_id, row, clamp_shade_contrast_column(row, col));
}

static void ComputeDisplayColors(const MatCell* src, const uint8_t** fg, const uint8_t** bg)
{
	if (invert_material_preview)
	{
		*fg = src->bg;
		*bg = src->fg;
		return;
	}

	*fg = src->fg;
	*bg = src->bg;
}

static void InvalidateCachedThemePalette()
{
	g_cached_theme_palette_valid = false;
	g_cached_theme_palette_index = -1;
}

static void ResetPaletteOwnerThemeState()
{
	g_has_theme_backup = false;
	g_active_theme_index = -1;
	g_theme_backup_palette_index = -1;
	g_theme_modified = false;
	g_theme_session_baseline_ready = false;
	g_palette_expanded = false;
	g_palette_selected = 0;
	g_last_palette_index = -1;
	g_palette_edit_active = false;
	g_palette_edit_palette_index = -1;
	g_palette_edit_theme_modified_backup = false;
	InvalidateCachedThemePalette();
}

static void CaptureThemeSessionBaseline()
{
	if (g_theme_session_baseline_ready)
		return;

	for (int m = 0; m < kPaletteCount; m++)
		memcpy(g_theme_session_start_mat[m], mat[m].shade, sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount);
	for (int p = 0; p < kPaletteCount; p++)
		memcpy(g_theme_session_start_pal[p], pal[p].rgb, sizeof(g_theme_session_start_pal[p]));
	g_theme_session_baseline_ready = true;
}

static float RgbLuminance01(const uint8_t rgb[3])
{
	return (0.299f * rgb[0] + 0.587f * rgb[1] + 0.114f * rgb[2]) / 255.0f;
}

static float RgbSaturation01(const uint8_t rgb[3])
{
	float r = rgb[0] / 255.0f;
	float g = rgb[1] / 255.0f;
	float b = rgb[2] / 255.0f;
	float lo = fminf(r, fminf(g, b));
	float hi = fmaxf(r, fmaxf(g, b));
	float delta = hi - lo;
	if (delta <= 0.0001f)
		return 0.0f;
	float l = 0.5f * (hi + lo);
	return delta / (1.0f - fabsf(2.0f * l - 1.0f));
}

static void GenerateThemePalette(const uint8_t bg[3], const uint8_t fg[3], const uint8_t accent[6][3], uint8_t out_rgb[3 * 256])
{
	const uint8_t* anchors[16];
	anchors[0] = bg;
	anchors[1] = fg;
	for (int i = 0; i < 6; i++)
		anchors[2 + i] = accent[i];
	for (int i = 0; i < 4; i++)
		anchors[8 + i] = accent[i];
	anchors[12] = accent[4];
	anchors[13] = accent[5];
	anchors[14] = fg;
	anchors[15] = accent[0];

	for (int row = 0; row < 16; row++)
	{
		const uint8_t* anchor = anchors[row];
		float r = anchor[0] / 255.0f;
		float g = anchor[1] / 255.0f;
		float b = anchor[2] / 255.0f;
		for (int col = 0; col < 16; col++)
		{
			float tl = 0.08f + (col / 15.0f) * 0.84f;
			if (row >= 8 && row <= 11)
				tl = 0.04f + (col / 15.0f) * 0.50f;
			float cl = RgbLuminance01(anchor);
			if (cl < 0.01f)
				cl = 0.01f;
			float s = tl / cl;
			float ro = fminf(r * s, 1.0f);
			float go = fminf(g * s, 1.0f);
			float bo = fminf(b * s, 1.0f);
			int idx = 3 * (col + row * 16);
			out_rgb[idx + 0] = (uint8_t)roundf(ro * 255.0f);
			out_rgb[idx + 1] = (uint8_t)roundf(go * 255.0f);
			out_rgb[idx + 2] = (uint8_t)roundf(bo * 255.0f);
		}
	}
}

static const uint8_t* CachedThemePalette(int theme_index)
{
	int theme_count = (int)(sizeof(g_palette_themes) / sizeof(g_palette_themes[0]));
	if (theme_index < 0 || theme_index >= theme_count)
		return 0;
	if (!g_cached_theme_palette_valid || g_cached_theme_palette_index != theme_index)
	{
		GenerateThemePalette(
			g_palette_themes[theme_index].bg,
			g_palette_themes[theme_index].fg,
			g_palette_themes[theme_index].accent,
			g_cached_theme_palette);
		g_cached_theme_palette_valid = true;
		g_cached_theme_palette_index = theme_index;
	}
	return g_cached_theme_palette;
}

static const uint8_t* ThemeRemapColor(const uint8_t src_rgb[3], const uint8_t fg[3], const uint8_t accent[6][3], const uint8_t theme_palette[3 * 256])
{
	float saturation = RgbSaturation01(src_rgb);
	int row = 1;
	if (saturation >= NEAR_NEUTRAL_SAT_THRESHOLD)
	{
		int best = 0;
		int best_dist = 0x7fffffff;
		for (int i = 0; i < 6; i++)
		{
			int dr = (int)src_rgb[0] - (int)accent[i][0];
			int dg = (int)src_rgb[1] - (int)accent[i][1];
			int db = (int)src_rgb[2] - (int)accent[i][2];
			int d = dr * dr + dg * dg + db * db;
			if (d < best_dist)
			{
				best_dist = d;
				best = i;
			}
		}
		row = 2 + best;
	}
	else if (RgbLuminance01(src_rgb) < RgbLuminance01(fg) * 0.35f)
	{
		row = 0;
	}

	int col = (int)roundf(RgbLuminance01(src_rgb) * 15.0f);
	if (col < 0) col = 0;
	if (col > 15) col = 15;
	return theme_palette + 3 * (col + row * 16);
}
// ============================================================================
// BRUSH STATE - Material Painting
// ============================================================================
// active_material: Currently selected material ID (0-255) for painting
// When you paint terrain in MAT-id mode, this value is written to the terrain
//
// Material 0 = Water (default)
// Material 1-255 = Random colors (should be defined as grass, dirt, stone, etc.)
//
// The terrain stores one material ID per cell (8-bit value)
// During rendering, this ID looks up colors/glyphs from the mat[] array
// ============================================================================
int active_elev = 0;

// term_font_zoom: user zoom offset for term++ windows (0 = auto-selected, +N = larger, -N = smaller)
int term_font_zoom = 0;

// used by Term — pick font closest to 120×75 cell target for given window size
static int FindFontForWindow(const int wnd_wh[2])
{
	float err = 0;
	int j = 0;
	float area = (float)(wnd_wh[0] * wnd_wh[1]);
	for (int i = 0; i < fonts_loaded; i++)
	{
		float e = fabsf(120.0f * 75.0f - area / ((font[i].width >> 4) * (font[i].height >> 4)));
		if (!i || e < err) { j = i; err = e; }
	}
	return j;
}

// When wnd_wh provided: auto-select by window area + term_font_zoom offset (term++ path).
// When wnd_wh is null: return active_font as absolute index (editor renderer path).
int GetGLFont(int wh[2], const int wnd_wh[2], int* id)
{
	int j;
	if (wnd_wh)
	{
		j = FindFontForWindow(wnd_wh) + term_font_zoom;
		if (j < 0) j = 0;
		if (j >= fonts_loaded) j = fonts_loaded - 1;
	}
	else
	{
		j = active_font;
	}

	MyFont* f = font + j;
	if (wh) { wh[0] = f->width; wh[1] = f->height; }
	if (id) *id = j;
	return f->tex;
}

bool PrevGLFont()
{
	if (term_font_zoom <= -(fonts_loaded - 1))
		return false;
	term_font_zoom--;
	TermResizeAll();
	return true;
}

bool NextGLFont()
{
	if (term_font_zoom >= fonts_loaded - 1)
		return false;
	term_font_zoom++;
	TermResizeAll();
	return true;
}

/*
float dawn_color[3] = { 1,.8f,0 };
float noon_color[3] = { 1,1,1 };
float dusk_color[3] = { 1,.2f,0 };
float midnight_color[3] = { .1f,.1f,.5f };
*/

float font_size = 10;// 0.125;// 16; // so every visual cell appears as 16px
float rot_yaw = 45;
float rot_pitch = 30;//90;

// FL-3707/FL-3714: large OSM maps need true zoom-out for whole-campus
// inspection. The previous 6px floor made FOCUS VIEW clamp back into a tiny
// local crop on 4k+ terrain spans. Do not "fix" asciiid freezing by raising
// this floor; fix the expensive drag/capture path that burns CPU at low zoom.
static const float kMinInteractiveFontSize = 0.25f;

static void ClampInteractiveFontSize()
{
	float before = font_size;
	if (font_size < kMinInteractiveFontSize)
		font_size = kMinInteractiveFontSize;
	if (font_size > 32.0f)
		font_size = 32.0f;
	if (font_size != before)
	{
		printf("[EDITOR] [FL-3714] zoom_clamped from=%.3f to=%.3f\n", before, font_size);
		fflush(stdout);
	}
}

float global_lt[4] = { 0,0,1,0 };

float inst_yaw = 0.0;
bool  inst_yaw_rnd = false;
float inst_pitch_avr = 0.0;
float inst_pitch_var = 0.0;
float inst_roll = 0.0;
bool  inst_added = false;

float lit_yaw = 45;
float lit_pitch = 30;//90;
float lit_time = 12.0f;
float ambience = 0.5;
float grid_alpha = 1.0f;

bool spin_anim = false;
int mouse_in = 0;
static bool g_mouse_right_physical = false;
static bool g_mouse_middle_physical = false;

int panning = 0;
int panning_x = 0;
int panning_y = 0;
double panning_dx = 0;
double panning_dy = 0;

float zoom_wheel = 0;

bool marquee_active = false;
ImVec2 marquee_start;
ImVec2 marquee_end;

// spinning, spinning_x, spinning_y migrated to g_editor_state (FL-2785).

// FL-2785: edit_mode, br_radius, brush_shape, br_alpha, br_tile_radius,
// br_limit, probe_z, story_id, diag_flipped, creating, painting,
// painting_x, painting_y, eg_*, spinning_* migrated to g_editor_state.
const float STAMP_R = 0.50;
const float STAMP_A = 1.00;
double painting_dx;
double painting_dy;
double paint_dist;
bool enemygen_preview = false;
float enemygen_preview_pos[3] = { 0,0,0 };

// OSM projection parameters — loaded from terrain_metadata.json on map load.
// Used by TerrainProbe to display lat/lon for clicked cells.
static struct {
	bool valid = false;
	double scene_lat, scene_lon;
	double content_scale;
	double shift_x, shift_y;
	double cal_x, cal_y;
} g_osm_proj;

static void world_to_latlon(double wx, double wy, double* out_lat, double* out_lon)
{
	if (!g_osm_proj.valid) { *out_lat = 0; *out_lon = 0; return; }
	const double R = 6378137.0;
	double x_m = (wx - g_osm_proj.cal_x - g_osm_proj.shift_x) / (g_osm_proj.content_scale * R);
	double y_m = (wy - g_osm_proj.cal_y - g_osm_proj.shift_y) / (g_osm_proj.content_scale * R);
	double D = y_m + g_osm_proj.scene_lat * M_PI / 180.0;
	*out_lon = g_osm_proj.scene_lon + atan(sinh(x_m) / cos(D)) * 180.0 / M_PI;
	*out_lat = asin(sin(D) / cosh(x_m)) * 180.0 / M_PI;
}

// Terrain probe result — populated by Ctrl+Shift+Click, displayed in INFO window
struct ProbeResult
{
	bool valid = false;
	double world_x, world_y, world_z;
	double lat, lon;
	int patch_x, patch_y;
	int cell_u, cell_v;
	int height;
	int mat_id;
	int elev_flag;
	int shade_idx;
	uint8_t matlib_bg[3];
	uint8_t matlib_fg[3];
	int xterm_bk;
	int xterm_fg;
	uint8_t glyph;
};
static ProbeResult g_probe;

static void TerrainProbe(Patch* p, double hit[3])
{
	if (!p) { g_probe.valid = false; return; }

	int px, py;
	GetTerrainPatch(terrain, p, &px, &py);

	struct mod_floor {
		mod_floor(int d) : y(d) {}
		int mod(int x) { int r = x % y; if (r && (r^y)<0) r += y; return r; }
		int y;
	} mf(VISUAL_CELLS);

	int u = mf.mod((int)floor(hit[0]));
	int v = mf.mod((int)floor(hit[1]));

	uint16_t* visual = GetTerrainVisualMap(p);
	uint16_t* hmap = GetTerrainHeightMap(p);
	uint16_t vis = visual[u + v * VISUAL_CELLS];

	int mid = vis & 0xFF;
	int elv = (vis >> 15) & 1;

	// approximate shade from height gradient (simplified — use shade 8 as default)
	int shd = 8;

	// height at nearest vertex
	int hu = u * (HEIGHT_CELLS) / VISUAL_CELLS;
	int hv = v * (HEIGHT_CELLS) / VISUAL_CELLS;
	int h = hmap[hu + hv * (HEIGHT_CELLS + 1)];

	MyMaterial* m = (MyMaterial*)GetMaterialArr();
	const MatCell* mc = &m[mid].shade[elv][shd];

	g_probe.valid = true;
	g_probe.world_x = hit[0];
	g_probe.world_y = hit[1];
	g_probe.world_z = hit[2];
	world_to_latlon(hit[0], hit[1], &g_probe.lat, &g_probe.lon);
	g_probe.patch_x = px;
	g_probe.patch_y = py;
	g_probe.cell_u = u;
	g_probe.cell_v = v;
	g_probe.height = h;
	g_probe.mat_id = mid;
	g_probe.elev_flag = elv;
	g_probe.shade_idx = shd;
	g_probe.matlib_bg[0] = mc->bg[0];
	g_probe.matlib_bg[1] = mc->bg[1];
	g_probe.matlib_bg[2] = mc->bg[2];
	g_probe.matlib_fg[0] = mc->fg[0];
	g_probe.matlib_fg[1] = mc->fg[1];
	g_probe.matlib_fg[2] = mc->fg[2];
	g_probe.glyph = mc->gl;

	// encode as xterm — same formula as active render path (line 3954)
	g_probe.xterm_bk = 16 + 36 * ((mc->bg[0] + 25) / 51) + 6 * ((mc->bg[1] + 25) / 51) + ((mc->bg[2] + 25) / 51);
	g_probe.xterm_fg = 16 + 36 * ((mc->fg[0] + 25) / 51) + 6 * ((mc->fg[1] + 25) / 51) + ((mc->fg[2] + 25) / 51);

	printf("[TerrainProbe] World(%.1f,%.1f,%.1f) Patch(%d,%d) Cell(%d,%d) mat=%d elv=%d h=%d bg=(%d,%d,%d)->xt%d fg=(%d,%d,%d)->xt%d gl=0x%02X\n",
		hit[0], hit[1], hit[2], px, py, u, v, mid, elv, h,
		mc->bg[0], mc->bg[1], mc->bg[2], g_probe.xterm_bk,
		mc->fg[0], mc->fg[1], mc->fg[2], g_probe.xterm_fg, mc->gl);
}

uint64_t g_Time; // in microsecs

#define QUOT(a) #a
#define DEFN(a) "#define " #a " " QUOT(a) "\n"
#define DEFN2(a,s) "#define " #a #s "\n"
#define CODE(...) #__VA_ARGS__

struct RenderContext
{
	int uni_ansi_vp;
	int uni_ansi_wh;
	int uni_ansi;
	int uni_font;
	int uni_asciiid_extended_glyph_enabled;
	int uni_asciiid_sidecar_tex;
	int uni_asciiid_lut_tex;
	int uni_asciiid_page_atlas;
	int uni_asciiid_lut_width;
	int uni_asciiid_fallback_glyph_id;
	int mesh_selected_loc;
	GLuint overview_prg = 0;
	GLuint overview_vbo = 0;
	GLuint overview_vao = 0;
	GLint overview_tm_loc = -1;
	int overview_tiles = 0;
	bool overview_mode = false;
	int patch_budget = 0;
	int patches_budget_skipped = 0;

	// WHY multiple shader programs:
	// The editor renders 5 different types of geometry, each requiring different shader behavior:
	// 1. ANSI/Terminal shader: 256-color palette, CP437 font, xterm color cube
	// 2. Mesh shader: Per-vertex RGB color from .akm files
	// 3. BSP shader: (legacy, minimal usage)
	// 4. Terrain shader: Height map + material index → elevation ramp + shade levels
	// 5. Sprite shader: Billboard projection, animation frames, yaw rotation
	// Each shader is compiled separately and bound during the corresponding render pass.
	//
	// WHY shader documentation BEFORE CODE() blocks:
	// The CODE() macro wraps GLSL source as C string literals. Comments inside
	// CODE() would become part of the shader source, not C++ comments. All shader
	// WHY comments are placed before the string assignment.
	void Create()
	{
		GLsizei loglen = 999;
		char logstr[1000];
		GLuint shader[3];

		// WHY ANSI/terminal shader:
		// Renders sprite/text overlays using AnsiCell buffer format (256-color palette indices).
		// Uses xterm 6x6x6 RGB cube mapping (Pal() function) to convert palette indices to RGB.
		// Supports CP437 font atlas with variable-width glyphs.
		const char* term_vs_src =
			CODE(#version 330\n)
			CODE(
				/*layout(location = 0)*/ uniform ivec2 ansi_vp;  // viewport size in cells
				layout(location = 0) in vec2 uv; // normalized to viewport size
				out vec2 cell_coord;
				void main()
				{
					gl_Position = vec4(2.0*uv - vec2(1.0), 0.0, 1.0);
					cell_coord = uv * ansi_vp;
				}
			);

		// FL-4131 Phase 3 — ASCIIID editor shader pin
		// ─────────────────────────────────────────────────────────────────────
		// MODEL_PIN: asciiid_shader_manifest_lookup
		//
		// PURPOSE: Pin the editor shader's resolution path for extended
		// GlyphIds. CP437 ids (0..255) keep the existing path below: the
		// fragment samples `font` (the bound CP437 atlas) at
		// `(glyph_idx & 0xF, glyph_idx >> 4) / 16.0 + frac/16`.
		//
		// EXTENDED GLYPH PATH (atlas_of_atlases binding model, shader side):
		//   - Editor binds the active sprite's GlyphPlane sidecar (Phase 2
		//     admission) plus its glyph_manifest (engine/glyph_manifest.h)
		//     plus a uniform array of repertoire-page atlas samplers.
		//   - For each cell where GlyphPlane.cells[i] > 255, the shader reads
		//     a small LUT (uniform texture or admission-compacted index buffer)
		//     keyed by GlyphId. The LUT yields (page_index, atlas_x, atlas_y)
		//     and the shader samples
		//         page_atlas[page_index].sample(
		//             (vec2(atlas_x, atlas_y) + frac_cell) / 16.0
		//         ).a
		//     which is the same 16x16 sampling shape as the CP437 path above —
		//     this is intentional, to keep the page-rect / WebGL1 binding
		//     contract unchanged.
		//
		// FAIL-CLOSED FALLBACK:
		//   - LUT miss (glyph not admitted by bound manifest) substitutes
		//     manifest.fallback_glyph_id (Phase 2 validator enforces this is
		//     always an admitted extended glyph >255; fallback ∈ entries is a
		//     parse-time hard constraint) and re-attempts extended LUT resolution
		//     (which succeeds because fallback is guaranteed to be in entries).
		//   - Sentinels (GLYPH_ID_NONE, GLYPH_ID_UNRESOLVED) also route through
		//     the manifest fallback. The shader never samples undefined atlas
		//     memory and never silently lands on glyph 0.
		//
		// LANE: this is the editor preview / shader path. The native render
		// pipeline pin lives in engine/render/render.h
		// (shader_lookup_lut_model_pinned). The picker UI pin lives below near
		// the 16x16 glyph grid (asciiid_input_model_pinned, Phase 4).
		//
		// COMPANION ANCHORS (review M5 — structured block, grep-stable):
		//   engine/render/render.h               : MODEL_PIN shader_lookup_lut_model_pinned
		//   editor/asciiid.cpp (picker grid)     : MODEL_PIN asciiid_input_model_pinned
		//   web/game_web.html (fragment shader)  : MODEL_PIN web_extended_glyph_buffer
		//   web/game_web.cpp                     : MODEL_PIN web_extended_glyph_buffer
		//   engine/glyph_manifest.h              : Phase 2 manifest + RFC8785+SHA-256
		//   engine/glyph_plane.h                 : Phase 2 sprite-side carrier
		//   server/protocol/protocol_join.h      : MODEL_PIN multiplayer_manifest_hash_match
		// ─────────────────────────────────────────────────────────────────────
		const char* term_fs_src =
			CODE(#version 330\n)
			DEFN2(P(r, g, b), vec3(r / 6., g / 7., b / 6.))
			CODE(
				layout(location = 0) out vec4 color;
				/*layout(location = 1)*/ uniform sampler2D ansi;
				/*layout(location = 2)*/ uniform sampler2D font;
				uniform int asciiid_extended_glyph_enabled;
				uniform sampler2D sidecar_tex;
				uniform sampler2D lut_tex;
				uniform sampler2D page_atlas;
				uniform float lut_width;
				uniform float fallback_glyph_id;
				/*layout(location = 3)*/ uniform ivec2 ansi_wh;  // ansi texture size (in cells), constant = 160x90
				in vec2 cell_coord;

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

				vec3 Pal(float p)
				{
					p = clamp(floor(p - 16.0 + 0.5), 0.0, 215.0);

					float blue = floor(p / 36.0);
					p -= 36.0*blue;

					float green = floor(p / 6.0);
					float red = p - 6.0*green;

					return vec3(blue, green, red) * 0.2;
				}

				float SampleAsciiidGlyphAlpha(int glyph_idx, vec2 ansi_coord, vec2 frac_cell, out bool diagnostic_fallback)
				{
					diagnostic_fallback = false;
					if (asciiid_extended_glyph_enabled != 0)
					{
						vec4 sidecar = texture(sidecar_tex, ansi_coord);
						float glyph_id = dot(sidecar, vec4(1.0, 256.0, 65536.0, 16777216.0));
						if (glyph_id > 255.5)
						{
							if (lut_width <= 0.0)
							{
								diagnostic_fallback = true;
								glyph_idx = int(fallback_glyph_id + 0.5);
							}
							else
							{
								float remap_x = (glyph_id + 0.5) / lut_width;
								vec4 lookup = texture(lut_tex, vec2(remap_x, 0.5));
								if (lookup.a <= 0.0)
								{
									diagnostic_fallback = true;
									glyph_idx = int(fallback_glyph_id + 0.5);
								}
								else
								{
									vec2 atlas_coord = lookup.xy + frac_cell * lookup.zw;
									return texture(page_atlas, atlas_coord).a;
								}
							}
						}
					}
					vec2 glyph_coord = (vec2(glyph_idx & 0xF, glyph_idx >> 4) + frac_cell) / vec2(16.0);
					return texture(font, glyph_coord).a;
				}

				void main()
				{
					// sample ansi buffer
					vec2 quot_cell = floor(cell_coord);
					vec2 frac_cell = fract(cell_coord);

					vec2 ansi_coord = (quot_cell + vec2(0.5)) / ansi_wh;

					vec4 cell = texture(ansi, ansi_coord);

					int glyph_idx = int(round(cell.b * 255.0));

					frac_cell.y = 1.0 - frac_cell.y;
					bool diagnostic_fallback = false;
					float glyph_alpha = SampleAsciiidGlyphAlpha(glyph_idx, ansi_coord, frac_cell, diagnostic_fallback);

					/*
					vec3 fg_color = XTermPal(int(round(cell.r * 255.0)));
					vec3 bg_color = XTermPal(int(round(cell.g * 255.0)));
					*/

					vec4 fg_color = vec4( Pal(cell.x*255.00), 1.0 );
					vec4 bg_color = vec4( Pal(cell.y*255.00), 1.0 );

					if (cell.x == 1.0)
						fg_color = vec4(0.0);
					if (cell.y == 1.0)
						bg_color = vec4(0.0);

					if (diagnostic_fallback)
					{
						fg_color = vec4(0.0, 0.0, 0.0, 1.0);
						bg_color = vec4(1.0, 0.0, 0.0, 1.0);
					}

					color = mix(bg_color, fg_color, glyph_alpha);

					if (color.a == 0.0)
						discard;
				}
			);

		GLenum ansi_st[2] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
		const char* ansi_src[2] = { term_vs_src, term_fs_src };
		ansi_prg = glCreateProgram();

		for (int i = 0; i < 2; i++)
		{
			shader[i] = glCreateShader(ansi_st[i]);
			if (!shader[i])
			{
				printf("glCreateShader failed\n");
				exit(-1);
			}

			GLint len = (GLint)strlen(ansi_src[i]);
			glShaderSource(shader[i], 1, &(ansi_src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(ansi_prg, shader[i]);
		}

		glLinkProgram(ansi_prg);

		for (int i = 0; i < 2; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(ansi_prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		uni_ansi_vp = glGetUniformLocation(ansi_prg, "ansi_vp");
		uni_ansi_wh = glGetUniformLocation(ansi_prg, "ansi_wh");
		uni_ansi = glGetUniformLocation(ansi_prg, "ansi");
		uni_font = glGetUniformLocation(ansi_prg, "font");
		uni_asciiid_extended_glyph_enabled = glGetUniformLocation(ansi_prg, "asciiid_extended_glyph_enabled");
		uni_asciiid_sidecar_tex = glGetUniformLocation(ansi_prg, "sidecar_tex");
		uni_asciiid_lut_tex = glGetUniformLocation(ansi_prg, "lut_tex");
		uni_asciiid_page_atlas = glGetUniformLocation(ansi_prg, "page_atlas");
		uni_asciiid_lut_width = glGetUniformLocation(ansi_prg, "lut_width");
		uni_asciiid_fallback_glyph_id = glGetUniformLocation(ansi_prg, "fallback_glyph_id");

		ansi_buf_size[0] = 64;
		ansi_buf_size[1] = 64;

		ansi_buf = (AnsiCell*)malloc(sizeof(AnsiCell)*ansi_buf_size[0]* ansi_buf_size[1]);
		gl3CreateTextures(GL_TEXTURE_2D, 1, &ansi_tex);
		gl3TextureStorage2D(ansi_tex, 1, GL_RGBA8, ansi_buf_size[0], ansi_buf_size[1]);
		gl3TextureParameteri2D(ansi_tex, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(ansi_tex, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
		gl3TextureParameteri2D(ansi_tex, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
		gl3TextureParameteri2D(ansi_tex, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

		gl3CreateBuffers(1, &ansi_vbo);
		float vbo_data[] = { 0,0, 1,0, 1,1, 0,1 };
		gl3NamedBufferStorage(ansi_vbo, 4 * sizeof(float[2]), 0, GL_DYNAMIC_STORAGE_BIT);
		gl3NamedBufferSubData(ansi_vbo, 0, 4 * sizeof(float[2]), vbo_data);

		gl3CreateVertexArrays(1, &ansi_vao);
		glBindVertexArray(ansi_vao);
		glBindBuffer(GL_ARRAY_BUFFER, ansi_vao);
		glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(float[2]), (void*)0);
		glEnableVertexAttribArray(0);
		glBindVertexArray(0);

		// meshes & bsp
		gl3CreateBuffers(1, &mesh_vbo);
		int mesh_face_size = 3*sizeof(float[3]) + 3*sizeof(uint8_t[4]) + sizeof(uint32_t); // 3*pos_xyz, visual, rgba
		gl3NamedBufferStorage(mesh_vbo, 1024 * mesh_face_size, 0, GL_DYNAMIC_STORAGE_BIT);

		gl3CreateVertexArrays(1, &mesh_vao);
		glBindVertexArray(mesh_vao);
		glBindBuffer(GL_ARRAY_BUFFER, mesh_vbo);
		glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, mesh_face_size, (void*)0);
		glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, mesh_face_size, (void*)((char*)0 + sizeof(float[3])));
		glVertexAttribPointer(2, 3, GL_FLOAT, GL_FALSE, mesh_face_size, (void*)((char*)0 + 2 * sizeof(float[3])));
		glVertexAttribPointer(3, 4, GL_UNSIGNED_BYTE, GL_TRUE, mesh_face_size,   (void*)((char*)0 + 3 * sizeof(float[3])));
		glVertexAttribPointer(4, 4, GL_UNSIGNED_BYTE, GL_TRUE, mesh_face_size,   (void*)((char*)0 + 3 * sizeof(float[3]) + sizeof(uint8_t[4])));
		glVertexAttribPointer(5, 4, GL_UNSIGNED_BYTE, GL_TRUE, mesh_face_size,   (void*)((char*)0 + 3 * sizeof(float[3]) + 2 * sizeof(uint8_t[4])));
		glVertexAttribIPointer(6, 1, GL_UNSIGNED_INT, mesh_face_size,   (void*)((char*)0 + 3 * sizeof(float[3]) + 3 * sizeof(uint8_t[4])));

		glBindBuffer(GL_ARRAY_BUFFER, 0);
		glEnableVertexAttribArray(0);
		glEnableVertexAttribArray(1);
		glEnableVertexAttribArray(2);
		glEnableVertexAttribArray(3);
		glEnableVertexAttribArray(4);
		glEnableVertexAttribArray(5);
		glEnableVertexAttribArray(6);
		glBindVertexArray(0);

		// WHY mesh shader:
		// Renders 3D mesh instances from Blender-exported .akm files.
		// Uses per-vertex RGB color stored in mesh data (no material system).
		// Supports transformation matrix for instance placement.
		const char* mesh_vs_src =
			CODE(#version 330\n)
			CODE(
				layout(location = 0) in vec3 a;
				layout(location = 1) in vec3 b;
				layout(location = 2) in vec3 c;
				layout(location = 3) in vec4 ca;
				layout(location = 4) in vec4 cb;
				layout(location = 5) in vec4 cc;
				layout(location = 6) in uint visual;

				uniform mat4 inst_tm;

				out vec3 va,vb,vc;
				out vec4 vca, vcb, vcc;
				out uint vis;
				void main()
				{
					va = (inst_tm * vec4(a, 1.0)).xyz;
					vb = (inst_tm * vec4(b, 1.0)).xyz;
					vc = (inst_tm * vec4(c, 1.0)).xyz;
					vca = ca;
					vcb = cb;
					vcc = cc;
					vis = visual;
				}
			);

		const char* mesh_gs_src =
			CODE(#version 330\n)
			CODE(
				layout(points) in;
				layout(triangle_strip, max_vertices = 3) out;
				uniform mat4 tm;
				in vec3 va[];
				in vec3 vb[];
				in vec3 vc[];
				in vec4 vca[];
				in vec4 vcb[];
				in vec4 vcc[];
				in uint vis[];

				flat out vec3 nrm;
				flat out vec3 view_nrm;
				flat out uint matid;

				out float shade;
				out float elev;
				out vec4 tint;

				void main()
				{
					vec3 a = va[0];
					vec3 b = vb[0];
					vec3 c = vc[0];

					matid = vis[0] & uint(0xFF);
					nrm = normalize( cross( b-a, c-a ) );
					view_nrm = normalize((tm * vec4(nrm, 0)).xyz);

					shade = float((vis[0] >> 8) & uint(0x7f)) / 8.0;
					elev = float((vis[0] >> 15) & uint(0x1));
					tint = vca[0];
					gl_Position = tm * vec4(a, 1.0);
					EmitVertex();

					shade = float((vis[0] >> 16) & uint(0x7f)) / 8.0;
					elev = float((vis[0] >> 23) & uint(0x1));
					tint = vcb[0];
					gl_Position = tm * vec4(b, 1.0);
					EmitVertex();

					shade = float((vis[0] >> 24) & uint(0x7f)) / 8.0;
					elev = float((vis[0] >> 31) & uint(0x1));
					tint = vcc[0];
					gl_Position = tm * vec4(c, 1.0);
					EmitVertex();

					EndPrimitive();
				}
			);


		const char* mesh_fs_src =
			CODE(#version 330\n)
			CODE(
				uniform sampler2D a_tex;
				uniform sampler2D f_tex;
				uniform sampler3D p_tex;
				uniform vec4 lt;

				uniform vec4 lt_dif_clr;
				uniform vec4 lt_amb_clr;

				uniform ivec2 ansi_depth_ofs;
				uniform ivec2 sprite_wh;
				uniform ivec2 ansi_wh;

				uniform float selected;

				layout(location = 0) out vec4 color;

				flat in vec3 nrm;
				flat in vec3 view_nrm;
				flat in uint matid;
				in float shade;
				in float elev;
				in vec4 tint;

				vec3 Pal(float p)
				{
					p = clamp(floor(p - 16.0 + 0.5), 0.0, 215.0);

					float blue = floor(p / 36.0);
					p -= 36.0*blue;

					float green = floor(p / 6.0);
					float red = p - 6.0*green;

					return vec3(blue, green, red) * 0.2;
				}

				void main()
				{
					if (matid != uint(0))
					{
						vec2 cell_coord = tint.rg * sprite_wh;

						// sample ansi buffer
						vec2 quot_cell = floor(cell_coord);
						vec2 frac_cell = fract(cell_coord);

						vec2 ansi_coord = (quot_cell + vec2(0.5)) / ansi_wh;

						vec4 cell = texture(a_tex, ansi_coord);

						float ds = 2.0 * (/*zoom*/ 1.0 * /*scale*/ 3.0) / 8/*VISUAL_CELLS*/ * 0.5 /*we're not dbl_wh*/;
						float dz_dy = 16/*HEIGHT_SCALE*/ / (cos(30 * 3.141592/*M_PI*/ / 180) * 4/*HEIGHT_CELLS*/ * ds);
						gl_FragDepth = (16/*HEIGHT_SCALE*/ / 4 + ansi_depth_ofs.x + (2.0*cell.w*255.0 + ansi_depth_ofs.y) * 0.5 * dz_dy) / 0xFFFF; // *2.0 / 0xFFFF - 1.0;

						int glyph_idx = int(round(cell.z * 255.0));

						frac_cell.y = 1.0 - frac_cell.y;
						vec2 glyph_coord = (vec2(glyph_idx & 0xF, glyph_idx >> 4) + frac_cell) / vec2(16.0);
						float glyph_alpha = texture(f_tex, glyph_coord).a;

						vec4 fg_color = vec4(Pal(cell.x*255.00), 1.0);
						vec4 bg_color = vec4(Pal(cell.y*255.00), 1.0);

						if (cell.x == 1.0)
							fg_color = vec4(0.0);
						if (cell.y == 1.0)
							bg_color = vec4(0.0);

						color = mix(bg_color, fg_color, glyph_alpha);

						//color = vec4(frac_cell, 0.5, 1);

						if (color.a == 0.0)
							discard;
					}
					else
					{
						gl_FragDepth = gl_FragCoord.z;

						color = tint;
						color.a = 1.0;

						vec3 light_pos = normalize(lt.xyz);
						float light = max(0.0, 0.5*lt.w + (1.0 - 0.5*lt.w)*dot(light_pos, normalize(nrm)));

						color.rgb *= light * lt_dif_clr.rgb;
						color.rgb += lt_amb_clr.rgb;
					}

					if (selected > 0.5)
						color.rgb = mix(color.rgb, vec3(1.0, 1.0, 0.0), 0.3); // Highlight yellow

					// palettize
					color.rgb = texture(p_tex, color.xyz).rgb;
				}
			);

		// WHY BSP shader:
		// Legacy shader for BSP (Binary Space Partition) rendering.
		// Minimal usage in current editor, kept for compatibility.
		const char* bsp_vs_src =
			CODE(#version 330\n)
			CODE(
				layout(location = 0) in vec3 a;
				layout(location = 1) in vec3 b;
				layout(location = 2) in vec3 c;

				out vec2 va,vb,vc;
				void main()
				{
					va = a.xy;
					vb = b.xy;
					vc = c.xy;
				}
			);

		const char* bsp_gs_src =
			CODE(#version 330\n)
			CODE(
				layout(points) in;
				layout(line_strip, max_vertices = 18) out;
				uniform mat4 tm;
				in vec2 va[];
				in vec2 vb[];
				in vec2 vc[];

				void main()
				{
					vec2 x = va[0];
					vec2 y = vb[0];
					vec2 z = vc[0];

					vec4 v[8];
					for (int i=0; i<8; i++)
					{
						int ix = i&1;
						int iy = (i>>1)&1;
						int iz = (i>>2)&1;
						v[i] = tm * vec4(x[ix],y[iy],z[iz],1.0);
					}

					int quad[5] = int[5](0,1,3,2,0);

					// 2 quads
					for (int j=0; j<2; j++)
					{
						for (int i=0; i<5; i++)
						{
							gl_Position = v[quad[i]+4*j];
							EmitVertex();
						}
						EndPrimitive();
					}

					// 4 joints
					for (int i=0; i<4; i++)
					{
						gl_Position = v[i];
						EmitVertex();
						gl_Position = v[i+4];
						EmitVertex();
						EndPrimitive();
					}
				}
			);


		const char* bsp_fs_src =
			CODE(#version 330\n)
			CODE(

				layout(location = 0) out vec4 color;

				void main()
				{
					color = vec4(0,0,0,0.33);
				}
			);



		// patches
		gl3CreateBuffers(1, &vbo);
		gl3NamedBufferStorage(vbo, TERRAIN_TEXHEAP_CAPACITY * sizeof(GLint[5]), 0, GL_DYNAMIC_STORAGE_BIT);

		gl3CreateVertexArrays(1, &vao);
		glBindVertexArray(vao);
		glBindBuffer(GL_ARRAY_BUFFER, vbo);
		glVertexAttribIPointer(0, 4, GL_INT, sizeof(GLint[5]), (void*)0);
		glVertexAttribIPointer(1, 1, GL_UNSIGNED_INT, sizeof(GLint[5]), (void*)sizeof(GLint[4]));
		glBindBuffer(GL_ARRAY_BUFFER, 0);
		glEnableVertexAttribArray(0);
		glEnableVertexAttribArray(1);
		glBindVertexArray(0);

		// ghost
		gl3CreateBuffers(1, &ghost_vbo);
		gl3NamedBufferStorage(ghost_vbo, sizeof(GLint[3*4*HEIGHT_CELLS]), 0, GL_DYNAMIC_STORAGE_BIT);

		gl3CreateVertexArrays(1, &ghost_vao);
		glBindVertexArray(ghost_vao);
		glBindBuffer(GL_ARRAY_BUFFER, ghost_vbo);
		glVertexAttribIPointer(0, 3, GL_INT, sizeof(GLint[3]), (void*)0);
		glBindBuffer(GL_ARRAY_BUFFER, 0);
		glEnableVertexAttribArray(0);
		glBindVertexArray(0);

		gl3CreateBuffers(1, &overview_vbo);
		gl3NamedBufferStorage(overview_vbo, sizeof(EditorOverviewVertex) * 6 * EditorTerrainOverviewCache::kMaxRenderedTiles, 0, GL_DYNAMIC_STORAGE_BIT);

		gl3CreateVertexArrays(1, &overview_vao);
		glBindVertexArray(overview_vao);
		glBindBuffer(GL_ARRAY_BUFFER, overview_vbo);
		glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(EditorOverviewVertex), (void*)0);
		glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, sizeof(EditorOverviewVertex), (void*)(sizeof(float) * 3));
		glBindBuffer(GL_ARRAY_BUFFER, 0);
		glEnableVertexAttribArray(0);
		glEnableVertexAttribArray(1);
		glBindVertexArray(0);

		const char* overview_vs_src =
			CODE(#version 330\n)
			CODE(
				layout(location = 0) in vec3 xyz;
				layout(location = 1) in vec3 rgb;
				uniform mat4 tm;
				out vec3 color;
				void main()
				{
					color = rgb;
					gl_Position = tm * vec4(xyz, 1.0);
				}
			);

		const char* overview_fs_src =
			CODE(#version 330\n)
			CODE(
				layout(location = 0) out vec4 out_color;
				in vec3 color;
				void main()
				{
					out_color = vec4(color, 1.0);
				}
			);

		// WHY ghost/terrain shader:
		// Renders terrain patches with height map elevation and material-based coloring.
		// Uses height value to index into material's elevation ramp (4 bands).
		// Supports diagonal flags for triangle orientation in quad subdivision.
		const char* ghost_vs_src =
			CODE(#version 330\n)
			DEFN(HEIGHT_SCALE)
			DEFN(HEIGHT_CELLS)
			DEFN(VISUAL_CELLS)
			CODE(
				layout(location = 0) in ivec3 xyz;
				uniform mat4 tm;
				void main()
				{
					float scale = float(VISUAL_CELLS) / float(HEIGHT_CELLS);
					vec4 pos = vec4(xyz, 1.0);
					pos.xy *= scale;
					gl_Position = tm * pos;
				}
			);

		const char* ghost_fs_src =
			CODE(#version 330\n)
			DEFN(HEIGHT_SCALE)
			DEFN(HEIGHT_CELLS)
			DEFN(VISUAL_CELLS)
			CODE(
				layout(location = 0) out vec4 color;
				uniform vec4 cl;
				void main()
				{
					color = cl;
				}
			);

		const char* vs_src =
		CODE(#version 330\n)
		DEFN(HEIGHT_SCALE)
		DEFN(HEIGHT_CELLS)
		DEFN(VISUAL_CELLS)
		CODE(
			layout(location = 0) in ivec4 in_xyuv;
			layout(location = 1) in uint in_diag;
			out ivec4 xyuv;
			out uint diag;

			void main()
			{
				xyuv = in_xyuv;
				diag = in_diag;
			}
		);

		const char* gs_src =
		CODE(#version 330\n)
		DEFN(HEIGHT_SCALE)
		DEFN(HEIGHT_CELLS)
		DEFN(VISUAL_CELLS)
		CODE(
			layout(points) in;
			layout(triangle_strip, max_vertices = 64/*4*HEIGHT_CELLS*HEIGHT_CELLS*/ ) out;

			uniform vec4 br;
			uniform usampler2D z_tex;
			uniform mat4 tm;

			uniform vec3 pr; // .x=height , .y=alpha (alpha=0.5 when probing, otherwise 1.0), .z is br_limit direction (+1/-1 or 0 if disabled)


			in ivec4 xyuv[];
			in uint diag[];

			out vec4 world_xyuv;
			out vec3 uvh;
			flat out vec3 normal;

			void main()
			{
				uint z;
				vec4 v;
				ivec2 xy;

				vec3 xyz[4];
				vec2 uv[4];

				float rvh = float(VISUAL_CELLS) / float(HEIGHT_CELLS);
				float dxy = 1.0 / float(HEIGHT_CELLS);
				ivec2 bxy = xyuv[0].xy*HEIGHT_CELLS;

				// todo: emit optimized strips
				// should allow having upto 6x6 patches -> 12 scalars * 6 strips * (6+1) cols * 2 verts = 1008 components (out of 1024)
				// currently max is 4x4 -> 12 scalars * 4*4 quads * 4 verts -> 768 components

				uint rot = diag[0];
				ivec4 order[2] = ivec4[2](ivec4(0, 1, 2, 3), ivec4(1, 3, 0, 2));

				for (int y = 0; y < HEIGHT_CELLS; y++)
				{
					for (int x = 0; x < HEIGHT_CELLS; x++)
					{
						xy = ivec2(x, y + 1);
						uv[0] = (xyuv[0].zw + vec2(xy) / HEIGHT_CELLS) * VISUAL_CELLS;
						z = texelFetch(z_tex, xyuv[0].zw*(HEIGHT_CELLS+1) + xy, 0).r;
						xy = bxy + xy*VISUAL_CELLS;
						xyz[0] = vec3(xy*dxy, z);

						xy = ivec2(x, y);
						uv[1] = (xyuv[0].zw + vec2(xy) / HEIGHT_CELLS) * VISUAL_CELLS;
						z = texelFetch(z_tex, xyuv[0].zw*(HEIGHT_CELLS + 1) + xy, 0).r;
						xy = bxy + xy*VISUAL_CELLS;
						xyz[1] = vec3(xy*dxy, z);

						xy = ivec2(x + 1, y + 1);
						uv[2] = (xyuv[0].zw + vec2(xy) / HEIGHT_CELLS) * VISUAL_CELLS;
						z = texelFetch(z_tex, xyuv[0].zw*(HEIGHT_CELLS + 1) + xy, 0).r;
						xy = bxy + xy * VISUAL_CELLS;
						xyz[2] = vec3(xy*dxy, z);

						xy = ivec2(x + 1, y);
						uv[3] = (xyuv[0].zw + vec2(xy) / HEIGHT_CELLS) * VISUAL_CELLS;
						z = texelFetch(z_tex, xyuv[0].zw*(HEIGHT_CELLS + 1) + xy, 0).r;
						xy = bxy + xy * VISUAL_CELLS;
						xyz[3] = vec3(xy*dxy, z);

						if (br.w != 0.0 && br.z>0 && br.w<=1.0 && br.w>=-1.0)
						{
							for (int i = 0; i < 4; i++)
							{
								vec2 d = xyz[i].xy - br.xy;
								float len = length(d);
								if (len < br.z)
								{
									float gauss = (0.5 + 0.5*cos(len/br.z*3.141592));

									int d = int(round(gauss*gauss * br.w * br.z * HEIGHT_SCALE));

									float z = xyz[i].z + d;

									if (pr.z!=0) // limit enabled
									{
										if (d > 0)
										{
											if (xyz[i].z > pr.x)
												z = xyz[i].z;
											else
											if (z > pr.x)
												z = pr.x;
										}
										else
										if (d < 0)
										{
											if (xyz[i].z < pr.x)
												z = xyz[i].z;
											else
											if (z < pr.x)
												z = pr.x;
										}
									}
									else
									{
										if (z < 0)
											z = 0;
										if (z > 0xffff)
											z = 0xffff;
									}

									xyz[i].z = z;

									// xyz[i].z += int(round(gauss*gauss * br.w * br.z * HEIGHT_SCALE));
									// xyz[i].z = clamp(xyz[i].z, 0, 0xffff);
								}
							}
						}

						vec3 norm[4];
						norm[0] = cross(xyz[1] - xyz[0], xyz[2] - xyz[0]);
						norm[1] = cross(xyz[2] - xyz[3], xyz[1] - xyz[3]);
						norm[2] = cross(xyz[3] - xyz[1], xyz[0] - xyz[1]);
						norm[3] = cross(xyz[0] - xyz[2], xyz[3] - xyz[2]);

						uint r = rot & uint(1);

						normal = norm[2 * int(r)];
						normal.xy *= 1.0 / HEIGHT_SCALE;

						{
							int i = order[r][0];

							world_xyuv = vec4(xyz[i].xy, uv[i]);
							uvh.xyz = xyz[i] - ivec3(xyuv[0].xy, 0);
							uvh.xyz /= vec3(rvh, rvh, HEIGHT_SCALE);

							gl_Position = tm * vec4(xyz[i], 1.0);
							EmitVertex();
						}
						{
							int i = order[r][1];

							world_xyuv = vec4(xyz[i].xy, uv[i]);
							uvh.xyz = xyz[i] - ivec3(xyuv[0].xy, 0);
							uvh.xyz /= vec3(rvh, rvh, HEIGHT_SCALE);

							gl_Position = tm * vec4(xyz[i], 1.0);
							EmitVertex();
						}
						{
							int i = order[r][2];

							world_xyuv = vec4(xyz[i].xy, uv[i]);
							uvh.xyz = xyz[i] - ivec3(xyuv[0].xy, 0);
							uvh.xyz /= vec3(rvh, rvh, HEIGHT_SCALE);

							gl_Position = tm * vec4(xyz[i], 1.0);
							EmitVertex();
						}

						normal = norm[2 * int(r) + 1];
						normal.xy *= 1.0 / HEIGHT_SCALE;

						{
							int i = order[r][3];

							world_xyuv = vec4(xyz[i].xy, uv[i]);
							uvh.xyz = xyz[i] - ivec3(xyuv[0].xy, 0);
							uvh.xyz /= vec3(rvh, rvh, HEIGHT_SCALE);

							gl_Position = tm * vec4(xyz[i], 1.0);
							EmitVertex();
						}

						rot = rot >> 1;
						EndPrimitive();
					}
				}
			}
		);

		const char* fs_src =
		CODE(#version 330\n)
		DEFN(HEIGHT_SCALE)
		DEFN(HEIGHT_CELLS)
		DEFN(VISUAL_CELLS)
		CODE(
			layout(location = 0) out vec4 color;

			uniform usampler2D v_tex;
			uniform usampler2D m_tex;
			uniform sampler2D f_tex;
			uniform sampler3D p_tex;

			uniform vec4 lt; // light pos
			uniform vec4 br; // brush
			uniform vec3 qd; // quad diag (.z==1 height quad, .z==2 visual map quad)
			uniform vec3 pr; // .x=height , .y=alpha (alpha=0.5 when probing, otherwise 1.0), .z is br_limit direction (+1/-1 or 0 if disabled)
			uniform float fz; // font zoom

			uniform float grid_alpha;

			uniform uint br_matid;

			flat in vec3 normal;
			in vec3 uvh;
			in vec4 world_xyuv;

			float Grid(vec2 d, vec2 p, float s)
			{
				d *= s;
				p = fract(p*s + vec2(0.5));

				float r = 1.0;

				if (d.x < 0.25)
				{
					float a = clamp(-log2(d.x * 4), 0.0, 1.0);
					float m = smoothstep(0.5 - d.x, 0.5, p.x) * smoothstep(0.5 + d.x, 0.5, p.x);
					r *= mix(1.0, pow(1.0 - m, 0.5), a);
				}
				if (d.y < 0.25)
				{
					float a = clamp(-log2(d.y * 4), 0.0, 1.0);
					float m = smoothstep(0.5 - d.y, 0.5, p.y) * smoothstep(0.5 + d.y, 0.5, p.y);
					r *= mix(1.0, pow(1.0 - m, 0.5), a);
				}

				return r;
			}

			void main()
			{
				// sample terrain visual
				uint visual = texelFetch(v_tex, ivec2(floor(world_xyuv.zw)), 0).r;
				//visual = 12345;

				vec3 light_pos = normalize(lt.xyz);
				float light = max(0.0, 0.5*lt.w + (1.0-0.5*lt.w)*dot(light_pos, normalize(normal)));

				bool elevated = false;

				{
					uint matid = visual & uint(0xFF);
					uint shade = (visual >> 8) & uint(0x7F);
					uint elev  = (visual >> 15) & uint(0x1);

					/*
					if (mode == 1) // replace shade with lighting
						shade = uint(round(light * 15.0));
					else
					if (mode == 2)
						shade = uint(round(light * shade));
					else
					if (mode == 3)
						shade = uint(round(light * 15.0)*(1 - shade) + shade);
					*/

					uint diffuse = uint(round(15.0*light));

					// if we're painting matid
					// replace matid if we're inside the brush

					if (br.w == 4.0) // mat-elev paint
					{
					}
					else
					if (br.w == 2.0) // mat-id paint
					{
						// flat (no-alpha) matid brush
						float abs_r = abs(br.z);
						float len = length(world_xyuv.xy - br.xy);

						if (len<abs_r)
						{
							if (pr.z>0) // limit to above
							{
								if (uvh.z * HEIGHT_SCALE >= pr.x)
									matid = br_matid;
							}
							else
							if (pr.z<0) // limit to below
							{
								if (uvh.z * HEIGHT_SCALE < pr.x)
									matid = br_matid;
							}
							else // no z-limit
								matid = br_matid;
						}
					}

					/*
						we could define mode on 2 bits:
						- 0: use shade map than apply lighting to rgb (useful for sculpting w/o defined materials in editor)
						- 1: overwrite shade with lighting   \
						- 2: multiply shade map by lighting   >-- for game
						- 3: screen shade map with lighting  /
					*/

					elevated = elev != uint(0);

					// convert elev to 0,1,2 material row of shades
					elev = uint(1);

					// sample material array
					// y=0,1 -> descent; y=2,3 -> fill; y=4,5 -> ascent
					uint mat_x = uint(2) * diffuse + uint(32) * elev;
					uvec4 fill_rgbc = texelFetch(m_tex, ivec2(uint(0)+mat_x, matid), 0);
					uvec4 fill_rgbp = texelFetch(m_tex, ivec2(uint(1)+mat_x, matid), 0);

					//fill_rgbc.w = 44;

					uvec2 font_size = uvec2(textureSize(f_tex,0));
					uvec2 glyph_size = font_size / uint(16);

					vec2 glyph_fract = fract(gl_FragCoord.xy * fz / glyph_size);
					glyph_fract.y = 1.0 - glyph_fract.y;
					if (glyph_fract.x < 0)
						glyph_fract.x += 1;
					if (glyph_fract.y < 0)
						glyph_fract.y += 1;
					if (glyph_fract.x >= 1)
						glyph_fract.x -= 1;
					if (glyph_fract.y >= 1)
						glyph_fract.y -= 1;

					// sample font texture (pure alpha)
					vec2 glyph_coord = vec2(fill_rgbc.w & uint(0xF), fill_rgbc.w >> 4);
					float glyph = texture(f_tex, (glyph_coord + glyph_fract) / 16.0).a;

					// compose glyph
					color = vec4(mix(vec3(fill_rgbp.rgb), vec3(fill_rgbc.rgb), glyph) / 255.0, 1.0);
					//color = vec4(glyph_fract, 0.5, 1.0);

					// if (mode == 0) // editing

					// already diffused by material ramp
					// color.rgb *= light;
				}

				// palettize
				color.rgb = texture(p_tex, color.xyz).rgb;

				if (qd.z>0)
				{
					if (qd.z > 3.0)
					{
						color.rgb = mix(color.rgb, vec3(0, 1, 1), 0.25);
					}
					else
					if (qd.z > 1.5)
					{
						// matid probe
						vec2 pos = floor(world_xyuv.xy);
						if (pos == qd.xy)
						{
							color.rgb = mix(color.rgb, vec3(0, 0, 1), 0.25);
						}
					}
					else
					{
						// diagonal flip preview
						float d = float(VISUAL_CELLS) / float(HEIGHT_CELLS);
						if (world_xyuv.x >= qd.x && world_xyuv.x < qd.x + d &&
							world_xyuv.y >= qd.y && world_xyuv.y < qd.y + d)
						{
							//color.rb = mix(color.rb, color.rb * 0.5, qd.z);
							color.rgb = mix(color.rgb, vec3(0, 1, 0), 0.25);
						}
					}
				}
				else
				if (qd.z < 0)
				{
					float d = float(VISUAL_CELLS);
					// patch delete preview
					if (world_xyuv.x >= qd.x && world_xyuv.x < qd.x + d &&
						world_xyuv.y >= qd.y && world_xyuv.y < qd.y + d)
					{
						//color.rb = mix(color.rb, color.rb * 0.5, qd.z);
						color.rgb = mix(color.rgb, vec3(1, .2, 0), -qd.z*0.25);
					}
				}

				{
					// height probe

					if (uvh.z * HEIGHT_SCALE < pr.x)
					{
						//color.g *= (1.0 - 0.25 * pr.y);
						color.rgb = mix(color.rgb, vec3(0.25, 0.5, 0.75), 0.1 + 0.1 * pr.y);
					}

					if (pr.x>0)
					{
						float dz = 2.0 * fwidth(uvh.z) * HEIGHT_SCALE;
						float lo = smoothstep(-dz, 0, uvh.z * HEIGHT_SCALE - pr.x);
						float hi = smoothstep(+dz, 0, uvh.z * HEIGHT_SCALE - pr.x);
						float silh = lo*hi;
						color.rgb *= 1.0 - 0.5*silh*pr.y;
					}
				}

				if (!gl_FrontFacing)
					color.rgb = 0.25 * (vec3(1.0) - color.rgb);

				float dx = 1.25*length(vec2(dFdx(uvh.x), dFdy(uvh.x)));
				float dy = 1.25*length(vec2(dFdx(uvh.y), dFdy(uvh.y)));

				vec2 d = vec2(dx, dy);

				float grid = 1.0;
				grid = min(grid, Grid(d*1.50, uvh.xy, 1.0 / float(HEIGHT_CELLS)));
				grid = min(grid, Grid(d*1.25, uvh.xy, 1.0));
				grid = min(grid, Grid(d*1.00, uvh.xy, float(VISUAL_CELLS) / float(HEIGHT_CELLS)));

				grid = 1.0 + grid_alpha*(grid - 1.0);

				// color.rgb *= grid;

				vec3 grid_color = elevated ? vec3(0,1,1) : vec3(0, 0, 1);
				color.rgb = mix(grid_color, color.rgb, grid);

				// brush preview
				if (br.w == 4.0)
				{
					// flat (no-alpha) matid brush
					float abs_r = abs(br.z);
					float len = length(world_xyuv.xy - br.xy);
					float alf = (abs_r - len) / abs_r;

					float dalf = fwidth(alf) * 2.0; // 2x thicker

					float lo = smoothstep(-dalf, 0, alf);
					float hi = smoothstep(+dalf, 0, alf);
					float silh = lo * hi;

					color.rgb *= 1.0 - 0.5*silh; // bit stronger (was .25)
				}
				else
				if (br.w == 2.0)
				{
					// flat (no-alpha) matid brush
					float abs_r = abs(br.z);
					float len = length(world_xyuv.xy - br.xy);
					float alf = (abs_r - len) / abs_r;

					float dalf = fwidth(alf) * 2.0; // 2x thicker

					float lo = smoothstep(-dalf, 0, alf);
					float hi = smoothstep(+dalf, 0, alf);
					float silh =  lo * hi;

					color.rgb *= 1.0 - 0.5*silh; // bit stronger (was .25)
				}
				else
				if (br.w != 0.0)
				{
					float abs_r = abs(br.z);
					float len = length(world_xyuv.xy - br.xy);
					float alf = (abs_r - len) / abs_r;

					float dalf = fwidth(alf);
					float silh = smoothstep(-dalf, 0, alf) * smoothstep(+dalf, 0, alf);

					alf = max(0.0, alf);

					if (br.z>0)
						color.gb *= 1.0 - alf;
					else
						color.rg *= 1.0 - alf;

					color.rgb *= 1.0 - silh*0.25;
				}
			}
		);

		loglen = 999;

		GLenum bsp_st[3] = { GL_VERTEX_SHADER, GL_GEOMETRY_SHADER, GL_FRAGMENT_SHADER };
		const char* bsp_src[3] = { bsp_vs_src, bsp_gs_src, bsp_fs_src };
		bsp_prg = glCreateProgram();

		for (int i = 0; i < 3; i++)
		{
			shader[i] = glCreateShader(bsp_st[i]);
			GLint len = (GLint)strlen(bsp_src[i]);
			glShaderSource(shader[i], 1, &(bsp_src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(bsp_prg, shader[i]);
		}

		glLinkProgram(bsp_prg);

		for (int i = 0; i < 3; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(bsp_prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		bsp_tm_loc = glGetUniformLocation(bsp_prg, "tm");


		GLenum mesh_st[3] = { GL_VERTEX_SHADER, GL_GEOMETRY_SHADER, GL_FRAGMENT_SHADER };
		const char* mesh_src[3] = { mesh_vs_src, mesh_gs_src, mesh_fs_src };
		mesh_prg = glCreateProgram();

		for (int i = 0; i < 3; i++)
		{
			shader[i] = glCreateShader(mesh_st[i]);
			GLint len = (GLint)strlen(mesh_src[i]);
			glShaderSource(shader[i], 1, &(mesh_src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(mesh_prg, shader[i]);
		}

		glLinkProgram(mesh_prg);

		for (int i = 0; i < 3; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(mesh_prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		mesh_inst_tm_loc = glGetUniformLocation(mesh_prg, "inst_tm");
		mesh_tm_loc = glGetUniformLocation(mesh_prg, "tm");
		mesh_lt_loc = glGetUniformLocation(mesh_prg, "lt");
		mesh_a_tex_loc = glGetUniformLocation(mesh_prg, "a_tex");
		mesh_f_tex_loc = glGetUniformLocation(mesh_prg, "f_tex");
		mesh_p_tex_loc = glGetUniformLocation(mesh_prg, "p_tex");

		mesh_ansi_wh_loc = glGetUniformLocation(mesh_prg, "ansi_wh");
		mesh_sprite_wh_loc = glGetUniformLocation(mesh_prg, "sprite_wh");
		mesh_ansi_depth_ofs_loc = glGetUniformLocation(mesh_prg, "ansi_depth_ofs");

		mesh_lt_dif_clr = glGetUniformLocation(mesh_prg, "lt_dif_clr");
		mesh_lt_amb_clr = glGetUniformLocation(mesh_prg, "lt_amb_clr");

		mesh_selected_loc = glGetUniformLocation(mesh_prg, "selected");

		GLenum ghost_st[3] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
		const char* ghost_src[3] = { ghost_vs_src, ghost_fs_src };
		ghost_prg = glCreateProgram();

		for (int i = 0; i < 2; i++)
		{
			shader[i] = glCreateShader(ghost_st[i]);
			GLint len = (GLint)strlen(ghost_src[i]);
			glShaderSource(shader[i], 1, &(ghost_src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(ghost_prg, shader[i]);
		}

		glLinkProgram(ghost_prg);

		for (int i = 0; i < 2; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(ghost_prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		ghost_tm_loc = glGetUniformLocation(ghost_prg, "tm");
		ghost_cl_loc = glGetUniformLocation(ghost_prg, "cl");

		GLenum overview_st[2] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
		const char* overview_src[2] = { overview_vs_src, overview_fs_src };
		overview_prg = glCreateProgram();

		for (int i = 0; i < 2; i++)
		{
			shader[i] = glCreateShader(overview_st[i]);
			GLint len = (GLint)strlen(overview_src[i]);
			glShaderSource(shader[i], 1, &(overview_src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(overview_prg, shader[i]);
		}

		glLinkProgram(overview_prg);

		for (int i = 0; i < 2; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(overview_prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		overview_tm_loc = glGetUniformLocation(overview_prg, "tm");

		prg = glCreateProgram();

		GLenum st[3] = { GL_VERTEX_SHADER, GL_GEOMETRY_SHADER, GL_FRAGMENT_SHADER };
		const char* src[3] = { vs_src, gs_src, fs_src };

		for (int i = 0; i < 3; i++)
		{
			shader[i] = glCreateShader(st[i]);
			GLint len = (GLint)strlen(src[i]);
			glShaderSource(shader[i], 1, &(src[i]), &len);
			glCompileShader(shader[i]);

			loglen = 999;
			glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
			logstr[loglen] = 0;

			if (loglen)
				printf("%s", logstr);

			glAttachShader(prg, shader[i]);
		}

		glLinkProgram(prg);

		for (int i = 0; i < 3; i++)
			glDeleteShader(shader[i]);

		loglen = 999;
		glGetProgramInfoLog(prg, loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		tm_loc = glGetUniformLocation(prg, "tm");
		z_tex_loc = glGetUniformLocation(prg, "z_tex");
		v_tex_loc = glGetUniformLocation(prg, "v_tex");
		m_tex_loc = glGetUniformLocation(prg, "m_tex");
		f_tex_loc = glGetUniformLocation(prg, "f_tex");
		p_tex_loc = glGetUniformLocation(prg, "p_tex");
		br_loc = glGetUniformLocation(prg, "br");
		qd_loc = glGetUniformLocation(prg, "qd");
		pr_loc = glGetUniformLocation(prg, "pr");
		lt_loc = glGetUniformLocation(prg, "lt");
		//lc_loc = glGetUniformLocation(prg, "lc");
		fz_loc = glGetUniformLocation(prg, "fz");
		br_matid_loc = glGetUniformLocation(prg, "br_matid");

		ga_loc = glGetUniformLocation(prg, "grid_alpha");
	}

	void Delete()
	{
		glDeleteVertexArrays(1, &vao);
		glDeleteBuffers(1, &vbo);
		glDeleteProgram(prg);

		glDeleteVertexArrays(1, &ghost_vao);
		glDeleteBuffers(1, &ghost_vbo);
		glDeleteProgram(ghost_prg);

		glDeleteVertexArrays(1, &overview_vao);
		glDeleteBuffers(1, &overview_vbo);
		glDeleteProgram(overview_prg);

		glDeleteBuffers(1, &mesh_vbo);
		glDeleteVertexArrays(1, &mesh_vao);
		glDeleteProgram(mesh_prg);

		glDeleteProgram(bsp_prg);

		glDeleteTextures(1, &ansi_tex);
		glDeleteBuffers(1, &ansi_vbo);
		glDeleteVertexArrays(1, &ansi_vao);
		glDeleteProgram(ansi_prg);

		if (ansi_buf)
			free(ansi_buf);
	}

	void PaintGhost(const double* tm, int px, int py, int pz, uint16_t ghost[4 * HEIGHT_CELLS])
	{
		GLint buf[3 * 4 * HEIGHT_CELLS];
		int g = 0, b = 0;

		px *= HEIGHT_CELLS;
		py *= HEIGHT_CELLS;

		for (int x = 0; x < HEIGHT_CELLS; x++)
		{
			buf[b++] = px + x;
			buf[b++] = py;
			buf[b++] = ghost[g++];
		}

		for (int y = 0; y < HEIGHT_CELLS; y++)
		{
			buf[b++] = px + HEIGHT_CELLS;
			buf[b++] = py + y;
			buf[b++] = ghost[g++];
		}

		for (int x = HEIGHT_CELLS; x > 0; x--)
		{
			buf[b++] = px + x;
			buf[b++] = py + HEIGHT_CELLS;
			buf[b++] = ghost[g++];
		}

		for (int y = HEIGHT_CELLS; y > 0; y--)
		{
			buf[b++] = px;
			buf[b++] = py + y;
			buf[b++] = ghost[g++];
		}

		float ftm[16];// NV bug! workaround
		for (int i = 0; i < 16; i++)
			ftm[i] = (float)tm[i];

		glBindVertexArray(ghost_vao);
		glUseProgram(ghost_prg);

		glUniformMatrix4fv(ghost_tm_loc, 1, GL_FALSE, ftm);

		gl3NamedBufferSubData(ghost_vbo, 0, sizeof(GLint[3 * 4 * HEIGHT_CELLS]), buf);

		glUniform4f(ghost_cl_loc, 0, 0, 0, 1.0f);
		glLineWidth(2.0f);
		glDrawArrays(GL_LINE_LOOP, 0, 4 * HEIGHT_CELLS);
		glLineWidth(1.0f);

		// flatten
		for (b = 0; b < 4 * HEIGHT_CELLS; b++)
			buf[3 * b + 2] = pz;
		gl3NamedBufferSubData(ghost_vbo, 0, sizeof(GLint[3 * 4 * HEIGHT_CELLS]), buf);

		glEnable(GL_BLEND);
		glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

		glUniform4f(ghost_cl_loc, 0, 0, 0, 0.2f);
		glDrawArrays(GL_TRIANGLE_FAN, 0, 4 * HEIGHT_CELLS);

		glDisable(GL_BLEND);

		glUseProgram(0);
		glBindVertexArray(0);
	}

	void RenderOverview(const double* tm, const EditorOverviewVertex* vertices, int vertex_count, int tile_count)
	{
		overview_tiles = tile_count;
		if (!vertices || vertex_count <= 0)
			return;

		float ftm[16];
		for (int i = 0; i < 16; i++)
			ftm[i] = (float)tm[i];

		glUseProgram(overview_prg);
		glUniformMatrix4fv(overview_tm_loc, 1, GL_FALSE, ftm);
		glBindVertexArray(overview_vao);
		gl3NamedBufferSubData(overview_vbo, 0, sizeof(EditorOverviewVertex) * vertex_count, vertices);
		glDrawArrays(GL_TRIANGLES, 0, vertex_count);
		glBindVertexArray(0);
		glUseProgram(0);
	}


	void BeginBSP(const double* tm)
	{
		float ftm[16];
		for (int i=0; i<16; i++)
			ftm[i] = (float)tm[i];

		glUseProgram(bsp_prg);

		glUniformMatrix4fv(bsp_tm_loc, 1, GL_FALSE, ftm);

		glBindVertexArray(mesh_vao);

		//glEnable(GL_CULL_FACE);

		glEnable(GL_DEPTH_TEST);
		glDepthFunc(GL_GEQUAL);
		glCullFace(GL_BACK);
		glDepthMask(0);

		glEnable(GL_BLEND);
		glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
		//glLineWidth(4.0f);

		mesh_faces=0;

		glBindBuffer(GL_ARRAY_BUFFER, mesh_vbo);
	}

	static void RenderBSP(int level, const float bbox[6], void* cookie)
	{
		RenderContext* rc = (RenderContext*)cookie;

		float* buf = rc->mesh_map[rc->mesh_faces].abc;
		buf[0] = bbox[0];
		buf[1] = bbox[1];
		buf[3] = bbox[2];
		buf[4] = bbox[3];
		buf[6] = bbox[4];
		buf[7] = bbox[5];
		rc->mesh_faces++;

		if (rc->mesh_faces/* == 1024*/)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, rc->mesh_faces * sizeof(Face), rc->mesh_map);
			glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
			rc->mesh_faces=0;
		}
	}

	void EndBSP()
	{
		if (mesh_faces)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, mesh_faces * sizeof(Face), mesh_map);
			glDrawArrays(GL_POINTS, 0, mesh_faces);
			mesh_faces=0;
		}

		glBindBuffer(GL_ARRAY_BUFFER, 0);

		glBindVertexArray(0);
		glUseProgram(0);

		//glDisable(GL_CULL_FACE);
		glDisable(GL_DEPTH_TEST);
		glDisable(GL_BLEND);
		glDepthMask(1);
		//glLineWidth(1.0f);
	}

	void BeginMeshes(const double* tm, const float* lt)
	{
		float ftm[16];
		for (int i=0; i<16; i++)
			ftm[i] = (float)tm[i];

		glUseProgram(mesh_prg);

		glUniformMatrix4fv(mesh_tm_loc, 1, GL_FALSE, ftm);
		glUniform4fv(mesh_lt_loc, 1, lt);
		glUniform1i(mesh_a_tex_loc, 2);
		glUniform1i(mesh_f_tex_loc, 3);
		glUniform1i(mesh_p_tex_loc, 4);

		float dif[4] = { 1,1,1,1 };
		glUniform4fv(mesh_lt_dif_clr, 1, dif);

		float amb[4] = { 0,0,0,0 };
		glUniform4fv(mesh_lt_amb_clr, 1, amb);

		glBindVertexArray(mesh_vao);

		gl3BindTextureUnit2D(2, ansi_tex);
		gl3BindTextureUnit2D(3, font[active_font].tex);
		gl3BindTextureUnit3D(4, pal_tex);

		//glEnable(GL_CULL_FACE);
		//glCullFace(GL_BACK);

		glEnable(GL_DEPTH_TEST);
		glDepthFunc(GL_GEQUAL);

		//mesh_map=0;
		mesh_faces=0;

		glBindBuffer(GL_ARRAY_BUFFER, mesh_vbo);

		glEnable(GL_DEPTH_CLAMP);
	}

	static void RenderFace(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie)
	{
		if (visual&(1<<31)) // skip lines
			return;

		RenderContext* rc = (RenderContext*)cookie;

		memcpy(rc->mesh_map[rc->mesh_faces].abc, coords, sizeof(float[9]));
		memcpy(rc->mesh_map[rc->mesh_faces].clr, colors, sizeof(uint8_t[12]));
		rc->mesh_map[rc->mesh_faces].visual = visual;
		rc->mesh_faces++;

		if (rc->mesh_faces == 1024)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, rc->mesh_faces * sizeof(Face), rc->mesh_map);
			glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
			rc->mesh_faces=0;
		}
	}

	// WHY sprite frame billboard rendering with padding and clip regions:
	// RenderFrame renders a single sprite animation frame as a billboard quad
	// (always faces camera). Computes quad corners accounting for sprite width,
	// height, ref point (origin), and yaw rotation. Padding (pad_x, pad_y) and
	// clip regions (clip_x, clip_y, clip_w, clip_h) support sprite atlases and
	// partial frame rendering for effects.
	static void RenderFrame(Sprite::Frame* f, float pos[3], void* cookie)
	{
		RenderContext* rc = (RenderContext*)cookie;

		float zoom = 2.0f/ 3.0f;
		float cos30 = (float)cos(30 * M_PI / 180);
		float dwx = (float)(zoom * f->width * 0.5f * cos(rot_yaw*M_PI / 180));
		float dwy = (float)(zoom * f->width * 0.5f * sin(rot_yaw*M_PI / 180));
		float dlz = zoom * -f->ref[1] * 0.5f / cos30 * HEIGHT_SCALE;
		float dhz = zoom * (f->height - f->ref[1] * 0.5f) / cos30 * HEIGHT_SCALE;

		float coords[2][9]; // [2 triangles] x [3 verts x {xyz}]
		uint8_t colors[2][12];

		coords[0][0] = pos[0] - dwx;
		coords[0][1] = pos[1] - dwy;
		coords[0][2] = pos[2] + dlz;
		colors[0][0] = 0;
		colors[0][1] = 0;
		colors[0][2] = 0;
		colors[0][3] = 0;

		coords[0][3] = pos[0] + dwx;
		coords[0][4] = pos[1] + dwy;
		coords[0][5] = pos[2] + dlz;
		colors[0][4] = 255;
		colors[0][5] = 0;
		colors[0][6] = 0;
		colors[0][7] = 0;

		coords[0][6] = pos[0] + dwx;
		coords[0][7] = pos[1] + dwy;
		coords[0][8] = pos[2] + dhz;
		colors[0][8] = 255;
		colors[0][9] = 255;
		colors[0][10] = 0;
		colors[0][11] = 0;

		//

		coords[1][0] = pos[0] + dwx;
		coords[1][1] = pos[1] + dwy;
		coords[1][2] = pos[2] + dhz;
		colors[1][0] = 255;
		colors[1][1] = 255;
		colors[1][2] = 0;
		colors[1][3] = 0;

		coords[1][3] = pos[0] - dwx;
		coords[1][4] = pos[1] - dwy;
		coords[1][5] = pos[2] + dhz;
		colors[1][4] = 0;
		colors[1][5] = 255;
		colors[1][6] = 0;
		colors[1][7] = 0;

		coords[1][6] = pos[0] - dwx;
		coords[1][7] = pos[1] - dwy;
		coords[1][8] = pos[2] + dlz;
		colors[1][8] = 0;
		colors[1][9] = 0;
		colors[1][10] = 0;
		colors[1][11] = 0;

		glUniform2i(rc->mesh_sprite_wh_loc, f->width, f->height);
		glUniform2i(rc->mesh_ansi_wh_loc, rc->ansi_buf_size[0], rc->ansi_buf_size[1]);
		glUniform2i(rc->mesh_ansi_depth_ofs_loc, (int)floorf(pos[2] + 0.5f), f->ref[2]);

		for (int face = 0; face < 2; face++)
		{
			memcpy(rc->mesh_map[rc->mesh_faces].abc, (float*)coords + 9 * face, sizeof(float[9]));
			memcpy(rc->mesh_map[rc->mesh_faces].clr, (uint8_t*)colors + 12 * face, sizeof(uint8_t[12]));
			rc->mesh_map[rc->mesh_faces].visual = 1; // MatID!=0 -> sprite
			rc->mesh_faces++;
		}

		if (f->width > rc->ansi_buf_size[0])
		{
			int cpy_w = f->width < rc->ansi_buf_size[0] ? f->width : rc->ansi_buf_size[0];
			int cpy_h = f->height < rc->ansi_buf_size[1] ? f->height : rc->ansi_buf_size[1];

			for (int y = 0; y < cpy_h; y++)
			{
				for (int x = 0; x < cpy_w; x++)
				{
					AnsiCell* dst = rc->ansi_buf + x + y * rc->ansi_buf_size[0];
					AnsiCell* src = f->cell + x + y * f->width;
					*dst = *src;
				}
			}
			gl3TextureSubImage2D(rc->ansi_tex, 0, 0, 0, rc->ansi_buf_size[0], cpy_h, GL_RGBA, GL_UNSIGNED_BYTE, rc->ansi_buf);
		}
		else
		{
			int cpy_h = f->height < rc->ansi_buf_size[1] ? f->height : rc->ansi_buf_size[1];
			gl3TextureSubImage2D(rc->ansi_tex, 0, 0, 0, f->width, cpy_h, GL_RGBA, GL_UNSIGNED_BYTE, f->cell);
		}


		glBufferSubData(GL_ARRAY_BUFFER, 0, rc->mesh_faces * sizeof(Face), rc->mesh_map);
		glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
		rc->mesh_faces = 0;
	}

	// WHY sprite animation frame computation and yaw rotation:
	// RenderSprite selects the correct animation and frame index from sprite data,
	// applies yaw rotation for directional sprites (8-way rotation), and queries
	// sprite frames via callback. Handles special cases: items (anim<0 uses purpose
	// field), randomized frames (rand_frame flag), and animation looping (reps array).
	static void RenderSprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie)
	{
		if (anim<0)
		{
			int purpose = frame;
			Item* item = (Item*)reps;
			if (purpose != Item::EDIT)
				return;
			anim = frame = 0;

			static int _reps[4] = { -1,-1,-1,-1 };
			reps = _reps;
		}

		RenderContext* rc = (RenderContext*)cookie;

		if (rc->mesh_faces)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, rc->mesh_faces * sizeof(Face), rc->mesh_map);
			glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
			rc->mesh_faces = 0;
		}

		// flushed, safe to change uniforms


		float ftm[16] = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1 };
		glUniformMatrix4fv(rc->mesh_inst_tm_loc, 1, GL_FALSE, ftm);

		/*
		if (GetMeshWorld(m) == merge._world)
		{
			ftm[12] += merge.dx * VISUAL_CELLS;
			ftm[13] += merge.dy * VISUAL_CELLS;
		}
		*/

		// draw temporarily a black billboard
		float angle = yaw;
		int ang = (int)floor((angle - rot_yaw) * s->angles / 360.0f + 0.5f);
		ang = ang >= 0 ? ang % s->angles : (ang % s->angles + s->angles) % s->angles;

		int i = frame + ang * s->anim[anim].length;
		//if (proj && s->projs > 1)
		//	i += s->anim[anim].length * s->angles;
		Sprite::Frame* f = s->atlas + s->anim[anim].frame_idx[i];

		RenderFrame(f, pos, cookie);

		if (inst)
		{
			AnsiCell id[32];
			Sprite::Frame id_frame;
			char idstr[16];

			int len = sprintf(idstr, "%d", GetInstStoryID(inst));

			id_frame.cell = id;
			id_frame.width = len;
			id_frame.height = 1;
			id_frame.ref[0] = len;
			id_frame.ref[1] = +3;
			id_frame.ref[2] = +4;

			if (inst == rc->hover_inst)
			{
				for (int x = 0; x < len; x++)
				{
					id[x].fg = 16;
					id[x].gl = idstr[x];
					id[x].bk = 16 + 215;
					id[x].spare = 0;
				}
			}
			else
			{
				for (int x = 0; x < len; x++)
				{
					id[x].fg = 16 + 215;
					id[x].gl = idstr[x];
					id[x].bk = 16;
					id[x].spare = 0;
				}
			}

			RenderFrame(&id_frame, pos, cookie);
		}
	}

	static void RenderMesh(Inst* i, Mesh* m, double tm[16], void* cookie)
	{
		RenderContext* rc = (RenderContext*)cookie;

		if (rc->mesh_faces)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, rc->mesh_faces * sizeof(Face), rc->mesh_map);
			glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
			rc->mesh_faces=0;
		}

		float ftm[16];
		for (int i=0; i<16; i++)
			ftm[i] = (float)tm[i];

		if (GetMeshWorld(m) == merge._world)
		{
			ftm[12] += merge.dx * VISUAL_CELLS;
			ftm[13] += merge.dy * VISUAL_CELLS;
		}

		bool selected = i && (GetInstFlags(i) & INST_SELECTED);
		glUniform1f(rc->mesh_selected_loc, selected ? 1.0f : 0.0f);

		glUniformMatrix4fv(rc->mesh_inst_tm_loc, 1, GL_FALSE, ftm);
		QueryMesh(m, RenderFace, rc);
	}

	void EndMeshes()
	{
		if (mesh_faces)
		{
			// flush
			glBufferSubData(GL_ARRAY_BUFFER, 0, mesh_faces * sizeof(Face), mesh_map);
			glDrawArrays(GL_POINTS, 0, mesh_faces);
			mesh_faces=0;
		}

		glBindBuffer(GL_ARRAY_BUFFER, 0);

		gl3BindTextureUnit2D(2, 0);
		gl3BindTextureUnit2D(3, 0);
		gl3BindTextureUnit3D(4, 0);

		glBindVertexArray(0);
		glUseProgram(0);

		//glDisable(GL_CULL_FACE);
		glDisable(GL_DEPTH_TEST);

		glDisable(GL_DEPTH_CLAMP);
	}

	// WHY terrain patch rendering with quad projection to screen space:
	// BeginPatches sets up GPU state for terrain rendering: shader program, uniforms,
	// transformation matrices. The tm (transform matrix), lt (light dir), br (brush),
	// qd (quad), and pr (projection) parameters configure the vertex/fragment shaders
	// for rendering terrain patches as height-mapped quads with material coloring.
	void BeginPatches(const double* tm, const float* lt, const float* br, const float* qd, const float* pr)
	{
		glUseProgram(prg);

		static const float br_off[] = { 0,0,1,0 };
		if (!br)
			br = br_off;

		/*
		float* c1;
		float* c2;
		float w;
		if (lit_time < 6)
		{
			w = lit_time / 6.0f;
			c1 = midnight_color;
			c2 = dawn_color;
		}
		else
		if (lit_time < 12)
		{
			w = powf((lit_time-6) / 6.0f, 0.3f);
			c1 = dawn_color;
			c2 = noon_color;
		}
		else
		if (lit_time < 18)
		{
			w = 1.0f - powf(1.0f - (lit_time - 12) / 6.0f, 0.3f);
			c1 = noon_color;
			c2 = dusk_color;
		}
		else
		{
			w = (lit_time - 18) / 6.0f;
			c1 = dusk_color;
			c2 = midnight_color;
		}

		float lit_color[3];
		for (int c=0; c<3; c++)
			lit_color[c] = c1[c]*(1-w) + c2[c]*w;
		*/

		//glUniformMatrix4dv(tm_loc, 1, GL_FALSE, tm);
		float ftm[16];// NV bug! workaround
		for (int i = 0; i < 16; i++)
			ftm[i] = (float)tm[i];

		double font_zoom; // calc using lengths of diagonals

		font_zoom = font[active_font].width * font[active_font].width + font[active_font].height * font[active_font].height;
		font_zoom /= 512.0 * font_size * font_size;
		font_zoom = sqrt(font_zoom);

		glUniformMatrix4fv(tm_loc, 1, GL_FALSE, ftm);
		glUniform4fv(lt_loc, 1, lt);
		//glUniform3fv(lc_loc, 1, lit_color);
		glUniform1i(z_tex_loc, 0);
		glUniform1i(v_tex_loc, 1);
		glUniform1i(m_tex_loc, 2);
		glUniform1i(f_tex_loc, 3);
		glUniform1i(p_tex_loc, 4);

		glUniform1f(ga_loc, grid_alpha);

		glUniform4fv(br_loc, 1, br);
		glUniform3fv(qd_loc, 1, qd);
		glUniform3fv(pr_loc, 1, pr);
		glUniform1f(fz_loc, (float)font_zoom);
		glUniform1ui(br_matid_loc, (GLuint)active_material);
		glBindVertexArray(vao);

		gl3BindTextureUnit2D(2, MyMaterial::tex);
		gl3BindTextureUnit2D(3, font[active_font].tex);
		gl3BindTextureUnit3D(4, pal_tex);

		head = 0;
		patches = 0;
		draws = 0;
		changes = 0;
		page_tex = 0;
		overview_tiles = 0;
		overview_mode = false;
		patch_budget = 0;
		patches_budget_skipped = 0;

		render_time = a3dGetTime();
	}

	static void RenderPatch(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		RenderContext* rc = (RenderContext*)cookie;

		if (rc->patch_budget > 0 && rc->patches >= rc->patch_budget)
		{
			rc->patches_budget_skipped++;
			return;
		}

		rc->patches++;
		TexAlloc* ta = GetTerrainTexAlloc(p);

		TexPageBuffer* buf = (TexPageBuffer*)ta->page->user;

		if (buf->size == 0)
		{
			if (rc->head)
				((TexPageBuffer*)rc->head->user)->prev = ta->page;
			buf->prev = 0;
			buf->next = rc->head;
			rc->head = ta->page;
		}

		GLint* patch = buf->data + 5 * buf->size;

		patch[0] = x;
		patch[1] = y;
		patch[2] = ta->x;
		patch[3] = ta->y;
		patch[4] = GetTerrainDiag(p);

		buf->size++;

		if (buf->size == TERRAIN_TEXHEAP_CAPACITY)
		{
			rc->draws++;

			if (rc->page_tex != ta->page)
			{
				rc->changes++;
				rc->page_tex = ta->page;

				for (int u=0; u<2; u++)
					gl3BindTextureUnit2D(u, rc->page_tex->tex[u]);
			}

			gl3NamedBufferSubData(rc->vbo, 0, sizeof(GLint[5]) * buf->size, buf->data);
			glDrawArrays(GL_POINTS, 0, buf->size);

			if (buf->prev)
				((TexPageBuffer*)buf->prev->user)->next = buf->next;
			else
				rc->head = buf->next;

			if (buf->next)
				((TexPageBuffer*)buf->next->user)->prev = buf->prev;

			buf->size = 0;
			buf->next = 0;
			buf->prev = 0;
		}

	}

	void EndPatches()
	{
		TexPage* tp = head;
		while (tp)
		{
			TexPageBuffer* buf = (TexPageBuffer*)tp->user;

			if (page_tex != tp)
			{
				changes++;
				page_tex = tp;

				for (int u=0; u<2; u++)
					gl3BindTextureUnit2D(u, page_tex->tex[u]);
			}

			draws++;
			gl3NamedBufferSubData(vbo, 0, sizeof(GLint[5]) * buf->size, buf->data);
			glDrawArrays(GL_POINTS, 0, buf->size);

			tp = buf->next;
			buf->size = 0;
			buf->next = 0;
			buf->prev = 0;
		}

		page_tex = 0;
		head = 0;

		for (int u = 0; u < 5; u++)
			gl3BindTextureUnit2D(u,0);

		glBindVertexArray(0);
		glUseProgram(0);

		render_time = a3dGetTime() - render_time;
	}

	GLint tm_loc; // uniform
	GLint lt_loc;
	//GLint lc_loc;
	GLint z_tex_loc;
	GLint v_tex_loc;
	GLint m_tex_loc;
	GLint f_tex_loc;
	GLint p_tex_loc;
	GLint ga_loc;

	GLint br_loc;
	GLint qd_loc;
	GLint pr_loc;

	GLint fz_loc;
	GLint br_matid_loc;

	GLuint prg;
	GLuint vao;
	GLuint vbo;

	GLuint ghost_prg;
	GLuint ghost_vbo;
	GLuint ghost_vao;
	GLint ghost_tm_loc;
	GLint ghost_cl_loc;

	GLuint mesh_prg;
	GLuint mesh_vbo;
	GLuint mesh_vao;
	GLint mesh_inst_tm_loc;
	GLint mesh_tm_loc;
	GLint mesh_lt_loc;
	GLint mesh_a_tex_loc;
	GLint mesh_f_tex_loc;
	GLint mesh_p_tex_loc;
	GLint mesh_lt_dif_clr;
	GLint mesh_lt_amb_clr;
	GLint mesh_ansi_wh_loc;
	GLint mesh_sprite_wh_loc;
	GLint mesh_ansi_depth_ofs_loc;

	GLuint bsp_prg;
	GLint bsp_tm_loc;

	int mesh_faces;
	struct Face
	{
		float abc[9];
		uint8_t clr[12];
		uint32_t visual;
	}; // * mesh_map;

	Face mesh_map[1024];

	// sprite widget
	int ansi_buf_size[2];
	AnsiCell* ansi_buf;
	GLuint ansi_tex;
	GLuint ansi_prg;
	GLuint ansi_vao;
	GLuint ansi_vbo;

	Inst* hover_inst;

	TexPage* page_tex;
	TexPage* head;

	int patches; // rendered stats
	int draws;
	int changes;
	uint64_t render_time;
};

RenderContext render_context;

void GL_APIENTRY glDebugCall(GLenum source, GLenum type, GLuint id, GLenum severity, GLsizei length, const GLchar *message, const void *userParam)
{
	static const char* source_str[] = // 0x8246 - 0x824B
	{
		"API",
		"WINDOW_SYSTEM",
		"SHADER_COMPILER",
		"THIRD_PARTY",
		"APPLICATION",
		"OTHER"
	};

	const char* src = "?";
	if (source >= 0x8246 && source <= 0x824B)
		src = source_str[source - 0x8246];

	static const char* type_str[] = // 0x824C - 0x8251
	{
		"ERROR",
		"DEPRECATED_BEHAVIOR",
		"UNDEFINED_BEHAVIOR",
		"PORTABILITY",
		"PERFORMANCE",
		"OTHER"
	};

	const char* typ = "?";
	if (type >= 0x824C && type <= 0x8251)
		typ = type_str[type - 0x824C];

	static const char* severity_str[] = // 0x9146 - 0x9148 , 0x826B
	{
		"HIGH",
		"MEDIUM",
		"LOW",
		"NOTIFICATION",
	};

	const char* sev = "?";
	if (severity >= 0x9146 && severity <= 0x9148)
		sev = severity_str[severity - 0x9146];
	else
		if (severity == 0x826B)
		{
			return;
			sev = severity_str[3];
		}

	printf("src:%s type:%s id:%d severity:%s\n%s\n\n", src, typ, id, sev, (const char*)message);
}

// [FL-3828] Track which patches have been URDO-snapshotted during the current
// brush stroke. Prevents redundant per-frame URDO_Patch allocations during
// drag — without this, 70% CPU was spent in nanov2_free/calloc on large maps.
static std::unordered_set<Patch*> g_painted_patches;

struct MatIDStamp
{
	static void SetMatCB(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		MatIDStamp* t = (MatIDStamp*)cookie;

		double r2 = t->r * t->r;
		double* hit = t->hit;

		uint16_t* visual = GetTerrainVisualMap(p);

		if (g_painted_patches.insert(p).second)
			URDO_Patch(p, true);
		bool diff = true;

		for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
		{
			for (int u = 0; u < VISUAL_CELLS; u++, i++)
			{
				double dx = u + x - hit[0];
				double dy = v + y - hit[1];
				double d2 = dx*dx + dy*dy;
				bool inside = (d2 < r2);
				if (brush_shape == 1) // Square
					inside = (fabs(dx) < t->r && fabs(dy) < t->r);
				else if (brush_shape == 2 && inside) // Noise spray
					inside = (fast_rand() & 255) > 128;

				if (inside)
				{
					if (painting == 2)
					{
						int old = visual[i] & 0xFF;
						if (old != active_material)
						{
							if (t->z_lim > 0)
							{
								if (HitTerrain(p, (u + 0.5) / VISUAL_CELLS, (v + 0.5) / VISUAL_CELLS) < t->z)
									continue;
							}
							else
							if (t->z_lim < 0)
							{
								if (HitTerrain(p, (u + 0.5) / VISUAL_CELLS, (v + 0.5) / VISUAL_CELLS) >= t->z)
									continue;
							}

							if (!diff)
							{
								URDO_Patch(p, true);
								diff = true;
							}

							visual[i] = (visual[i] & ~0x00FF) | active_material;
						}
					}
					else
					if (painting == 3)
					{
						int old = (visual[i] >> 15) & 1;
						if (old != active_elev)
						{
							if (t->z_lim > 0)
							{
								if (HitTerrain(p, (u + 0.5) / VISUAL_CELLS, (v + 0.5) / VISUAL_CELLS) < t->z)
									continue;
							}
							else
							if (t->z_lim < 0)
							{
								if (HitTerrain(p, (u + 0.5) / VISUAL_CELLS, (v + 0.5) / VISUAL_CELLS) >= t->z)
									continue;
							}

							if (!diff)
							{
								URDO_Patch(p, true);
								diff = true;
							}

							visual[i] = (visual[i] & ~0x8000) | (active_elev << 15);
						}
					}
				}
			}
		}

		if (diff)
			UpdateTerrainVisualMap(p);
	}

	int z_lim;
	double z;
	double r;
	double* hit;
};

// WHY bilinear interpolation for smooth terrain sampling:
// SampleHeightBilinear reads terrain height at non-integer coordinates by
// interpolating between the 4 nearest height map grid points. This provides
// smooth height values for ray casting, slope calculation, and mesh baking,
// avoiding staircase artifacts from nearest-neighbor sampling. Uses standard
// bilinear interpolation: lerp in X, then lerp in Y.
static double SampleHeightBilinear(const uint16_t* map, double fx, double fy)
{
	if (!map)
		return 0.0;

	if (fx < 0.0)
		fx = 0.0;
	if (fy < 0.0)
		fy = 0.0;
	if (fx > HEIGHT_CELLS)
		fx = HEIGHT_CELLS;
	if (fy > HEIGHT_CELLS)
		fy = HEIGHT_CELLS;

	int x0 = (int)floor(fx);
	int y0 = (int)floor(fy);
	int x1 = std::min(HEIGHT_CELLS, x0 + 1);
	int y1 = std::min(HEIGHT_CELLS, y0 + 1);

	double tx = fx - x0;
	double ty = fy - y0;

	int stride = HEIGHT_CELLS + 1;
	double h00 = map[y0 * stride + x0];
	double h10 = map[y0 * stride + x1];
	double h01 = map[y1 * stride + x0];
	double h11 = map[y1 * stride + x1];

	double h0 = h00 * (1.0 - tx) + h10 * tx;
	double h1 = h01 * (1.0 - tx) + h11 * tx;

	return h0 * (1.0 - ty) + h1 * ty;
}

static double SampleTerrainHeightAtWorld(Terrain* t, double wx, double wy, double fallback)
{
	if (!t)
		return fallback;

	int patch_x = (int)floor(wx / (HEIGHT_CELLS * 2));
	int patch_y = (int)floor(wy / (HEIGHT_CELLS * 2));
	Patch* p = GetTerrainPatch(t, patch_x, patch_y);
	if (!p)
		return fallback;

	uint16_t* hmap = GetTerrainHeightMap(p);
	double lx = fmod(wx, (double)(HEIGHT_CELLS * 2));
	double ly = fmod(wy, (double)(HEIGHT_CELLS * 2));
	if (lx < 0.0)
		lx += HEIGHT_CELLS * 2;
	if (ly < 0.0)
		ly += HEIGHT_CELLS * 2;
	return SampleHeightBilinear(hmap, lx / 2.0, ly / 2.0);
}

static bool g_editor_minimap_visible = true;

static bool EditorMinimapMarkerIsGenericBuildingName(const char* name)
{
	if (!name || !name[0])
		return false;

	const char prefix[] = "Building_";
	size_t prefix_len = sizeof(prefix) - 1;
	if (strncmp(name, prefix, prefix_len) != 0)
		return false;

	const char* p = name + prefix_len;
	if (!isdigit((unsigned char)*p))
		return false;
	while (isdigit((unsigned char)*p))
		p++;
	if (*p == 0)
		return true;
	if (strncmp(p, "_clone", 6) == 0)
		return true;
	return false;
}

static int EditorMinimapMarkerLabel(MinimapMarker* marker, char* out, int out_size)
{
	if (!out || out_size <= 0)
		return 0;
	out[0] = 0;
	if (!marker)
		return 0;

	const char* name = GetMinimapMarkerName(marker);
	const char* label = GetMinimapMarkerLabel(marker);
	if (label && label[0])
	{
		snprintf(out, out_size, "%s", label);
		return (int)strlen(out);
	}
	if (GetMinimapMarkerType(marker) == 1 && EditorMinimapMarkerIsGenericBuildingName(name))
		return 0;
	if (!name || !name[0])
		return 0;

	int written = 0;
	bool in_separator = false;
	for (const char* p = name; *p && written + 1 < out_size; p++)
	{
		char c = *p;
		if (c == '_' || c == '-' || c == '.')
		{
			if (written > 0)
				in_separator = true;
			continue;
		}
		if (in_separator && written + 2 < out_size)
			out[written++] = ' ';
		out[written++] = c;
		in_separator = false;
	}
	out[written] = 0;
	return written;
}

static bool EditorWorldToScreen(const double tm[16], double wx, double wy, double wz, float display_w, float display_h, ImVec2* out)
{
	double pos[4] = { wx, wy, wz, 1.0 };
	double r[4];
	Product(tm, pos, r);
	if (r[3] <= 0.1)
		return false;

	double ndc_x = r[0] / r[3];
	double ndc_y = r[1] / r[3];
	if (ndc_x < -1.2 || ndc_x > 1.2 || ndc_y < -1.2 || ndc_y > 1.2)
		return false;

	out->x = (float)((ndc_x + 1.0) * 0.5 * display_w);
	out->y = (float)((1.0 - ndc_y) * 0.5 * display_h);
	return true;
}

static bool EditorWorldToScreen(const double tm[16], double wx, double wy, double wz, ImVec2* out)
{
	ImGuiIO& io = ImGui::GetIO();
	return EditorWorldToScreen(tm, wx, wy, wz, io.DisplaySize.x, io.DisplaySize.y, out);
}

static void DrawEditorMinimapMarkerLabels(const double tm[16])
{
	ImDrawList* draw = ImGui::GetForegroundDrawList();
	for (MinimapMarker* marker = GetFirstMinimapMarker(); marker; marker = GetNextMinimapMarker(marker))
	{
		char label[256];
		if (EditorMinimapMarkerLabel(marker, label, sizeof(label)) <= 0)
			continue;

		double wx = GetMinimapMarkerX(marker);
		double wy = GetMinimapMarkerY(marker);
		double wz = SampleTerrainHeightAtWorld(terrain, wx, wy, 0.0) + 48.0;

		ImVec2 screen;
		if (!EditorWorldToScreen(tm, wx, wy, wz, &screen))
			continue;

		ImVec2 text = ImGui::CalcTextSize(label);
		ImVec2 pad(4.0f, 2.0f);
		ImVec2 label_pos(screen.x - text.x * 0.5f, screen.y - text.y - 10.0f);
		ImVec2 rect_min(label_pos.x - pad.x, label_pos.y - pad.y);
		ImVec2 rect_max(label_pos.x + text.x + pad.x, label_pos.y + text.y + pad.y);

		draw->AddRectFilled(rect_min, rect_max, IM_COL32(0, 0, 0, 180), 3.0f);
		draw->AddText(label_pos, IM_COL32(255, 255, 128, 255), label);
	}
}

static void BuildEditorViewMatrix(double out_tm[16], float display_w, float display_h)
{
	double rx = 0.5 * display_w / font_size;
	double ry = 0.5 * display_h / font_size;
	double pitch = rot_pitch * (M_PI / 180);
	double yaw = rot_yaw * (M_PI / 180);
	double z_scale = 1.0 / HEIGHT_SCALE;

	out_tm[0] = +cos(yaw)/rx;
	out_tm[1] = -sin(yaw)*sin(pitch)/ry;
	out_tm[2] = 0;
	out_tm[3] = 0;
	out_tm[4] = +sin(yaw)/rx;
	out_tm[5] = +cos(yaw)*sin(pitch)/ry;
	out_tm[6] = 0;
	out_tm[7] = 0;
	out_tm[8] = 0;
	out_tm[9] = +cos(pitch)*z_scale/ry;
	out_tm[10] = +2./0xffff;
	out_tm[11] = 0;
	out_tm[12] = -(pos_x * out_tm[0] + pos_y * out_tm[4] + pos_z * out_tm[8]);
	out_tm[13] = -(pos_x * out_tm[1] + pos_y * out_tm[5] + pos_z * out_tm[9]);
	out_tm[14] = -1.0;
	out_tm[15] = 1.0;
}

static void BuildEditorViewMatrix(double out_tm[16])
{
	ImGuiIO& io = ImGui::GetIO();
	BuildEditorViewMatrix(out_tm, io.DisplaySize.x, io.DisplaySize.y);
}

static int PrintProjectedEditorMarkerLabels(float display_w, float display_h)
{
	double tm[16];
	BuildEditorViewMatrix(tm, display_w, display_h);
	int total = 0;
	int projected = 0;
	int labeled = 0;

	printf("[MCP] [PROJECT_MARKER_LABELS_START] camera=%.2f %.2f %.2f yaw=%.2f pitch=%.2f font_size=%.3f display=%.0fx%.0f\n",
		pos_x, pos_y, pos_z, rot_yaw, rot_pitch, font_size, display_w, display_h);
	for (MinimapMarker* marker = GetFirstMinimapMarker(); marker; marker = GetNextMinimapMarker(marker))
	{
		total++;
		char label[256];
		int label_len = EditorMinimapMarkerLabel(marker, label, sizeof(label));
		if (label_len <= 0)
			continue;
		labeled++;

		double wx = GetMinimapMarkerX(marker);
		double wy = GetMinimapMarkerY(marker);
		double wz = SampleTerrainHeightAtWorld(terrain, wx, wy, 0.0) + 48.0;
		ImVec2 screen;
		bool visible = EditorWorldToScreen(tm, wx, wy, wz, display_w, display_h, &screen);
		if (visible)
			projected++;
		printf("[MCP] marker name=%s label=\"%s\" world=%.1f,%.1f,%.1f screen=%.1f,%.1f visible=%d\n",
			GetMinimapMarkerName(marker), label, wx, wy, wz, screen.x, screen.y, visible ? 1 : 0);
	}
	printf("[MCP] [PROJECT_MARKER_LABELS_END] total=%d labeled=%d projected=%d\n", total, labeled, projected);
	fflush(stdout);
	return projected;
}

static double SampleSlopeMagnitude(const uint16_t* map, double fx, double fy, double step)
{
	double h_l = SampleHeightBilinear(map, fx - step, fy);
	double h_r = SampleHeightBilinear(map, fx + step, fy);
	double h_d = SampleHeightBilinear(map, fx, fy - step);
	double h_u = SampleHeightBilinear(map, fx, fy + step);
	double dx = (h_r - h_l) / (2.0 * step);
	double dy = (h_u - h_d) / (2.0 * step);
	return sqrt(dx * dx + dy * dy);
}

static bool HasElevationDelta(const uint16_t* map, double fx, double fy, double step, double threshold)
{
	if (!map)
		return false;

	double h_center = SampleHeightBilinear(map, fx, fy);
	double h_min = h_center;
	double h = SampleHeightBilinear(map, fx - step, fy);
	h_min = std::min(h_min, h);
	h = SampleHeightBilinear(map, fx + step, fy);
	h_min = std::min(h_min, h);
	h = SampleHeightBilinear(map, fx, fy - step);
	h_min = std::min(h_min, h);
	h = SampleHeightBilinear(map, fx, fy + step);
	h_min = std::min(h_min, h);

	return (h_center - h_min) > threshold;
}

struct AutoMatElev
{
	int mode; // 0 slope, 1 height
	double slope_threshold;
	int height_threshold;
	bool overwrite;

	static void Apply(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		AutoMatElev* ctx = (AutoMatElev*)cookie;
		uint16_t* visual = GetTerrainVisualMap(p);
		uint16_t* height = GetTerrainHeightMap(p);

		const double step = 0.25;
		bool changed = false;

		for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
		{
			for (int u = 0; u < VISUAL_CELLS; u++, i++)
			{
				double fx = (u + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
				double fy = (v + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;

				int auto_bit = 0;
				if (ctx->mode == 0)
				{
					double slope = SampleSlopeMagnitude(height, fx, fy, step);
					auto_bit = slope >= ctx->slope_threshold ? 1 : 0;
				}
				else
				{
					double h = SampleHeightBilinear(height, fx, fy);
					auto_bit = h >= ctx->height_threshold ? 1 : 0;
				}

				uint16_t old = visual[i];
				uint16_t next = old;
				if (ctx->overwrite)
					next = (old & ~0x8000) | (auto_bit << 15);
				else if (auto_bit)
					next = old | 0x8000;

				if (next != old)
				{
					if (!changed)
					{
						URDO_Patch(p, true);
						changed = true;
					}
					visual[i] = next;
				}
			}
		}

		if (changed)
			UpdateTerrainVisualMap(p);
	}
};

// WHY automatic material assignment by slope threshold and elevation bands:
// ApplyAutoMatElev scans all terrain patches and sets material IDs based on
// terrain slope (steepness) and height. Low slope = flat terrain (grass, dirt),
// high slope = cliffs (stone, rock). Height bands allow snow at high elevation,
// sand at low. This automates terrain texturing without manual painting.
static void ApplyAutoMatElev(int mode, double slope_threshold, int height_threshold, bool overwrite)
{
	if (!terrain)
		return;

	AutoMatElev ctx = { mode, slope_threshold, height_threshold, overwrite };
	URDO_Open();
	QueryTerrain(terrain, 0.0, 0.0, 1e9, 0xAA, AutoMatElev::Apply, &ctx);
	URDO_Close();
}

struct AutoTexture
{
	int mode; // 0:slope, 1:height
	double slope_threshold;
	int height_min;
	int height_max;
	int material_id;
	bool overwrite;

	static void Apply(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		AutoTexture* ctx = (AutoTexture*)cookie;
		uint16_t* visual = GetTerrainVisualMap(p);
		uint16_t* height = GetTerrainHeightMap(p);

		const double step = 0.25;
		bool changed = false;

		for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
		{
			for (int u = 0; u < VISUAL_CELLS; u++, i++)
			{
				double fx = (u + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
				double fy = (v + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;

				bool match = false;
				if (ctx->mode == 0) // Slope
				{
					double slope = SampleSlopeMagnitude(height, fx, fy, step);
					if (slope >= ctx->slope_threshold)
						match = true;
				}
				else // Height
				{
					double h = SampleHeightBilinear(height, fx, fy);
					if (h >= ctx->height_min && h <= ctx->height_max)
						match = true;
				}

				if (match)
				{
					uint16_t old = visual[i];
					// Preserve the elevation bit (0x8000) and other flags if we want,
					// but usually we just want to change the material ID (lower 8 bits).
					// Let's preserve the upper bits (flags).
					uint16_t next = (old & 0xFF00) | (ctx->material_id & 0xFF);

					if (!ctx->overwrite && (old & 0xFF) != 0) // Assuming 0 is "empty" or default water, wait.. 0 is water.
					{
						// If overwrite is false, we only paint on "default" material?
						// Or maybe we need a "target mask"? For now, let's just use overwrite flag.
						// If overwrite is false, we don't change anything if it's already set?
						// Let's strictly follow the bool.
						match = false;
					}

					if (ctx->overwrite || (old & 0xFF) == 2 /*Dirt is default? no*/)
					{
						// Actually, typical use case: "Paint Rock on everything steeper than X"
						// So we usually ALWAYS overwrite.
						// "Overwrite" in UI usually means "Replace everything" vs "Only replace specific stuff".
						// For this simple implementation, let's assume 'overwrite' means 'always apply'.
						// If !overwrite, maybe we should only paint if current mat is... distinct?
						// Let's stick to the simpler logic: if match, apply.
						// Wait, the UI checkbox says "Overwrite Existing".
						// If false, maybe we shouldn't paint?
						// Let's treat !overwrite as "Don't paint if not Material 0 or 2 (common bases)".
						// Actually, let's just make it simple: Apply if match.
					}

					if (next != old)
					{
						if (!changed)
						{
							URDO_Patch(p, true);
							changed = true;
						}
						visual[i] = next;
					}
				}
			}
		}

		if (changed)
			UpdateTerrainVisualMap(p);
	}
};

static void ApplyAutoTexture(int mode, double slope_th, int h_min, int h_max, int mat_id, bool overwrite)
{
	if (!terrain) return;
	AutoTexture ctx = { mode, slope_th, h_min, h_max, mat_id, overwrite };
	URDO_Open();
	QueryTerrain(terrain, 0.0, 0.0, 1e9, 0xAA, AutoTexture::Apply, &ctx);
	URDO_Close();
}

static void ClearMatElev()
{
	if (!terrain)
		return;

	struct ClearCB
	{
		static void Apply(Patch* p, int x, int y, int view_flags, void* cookie)
		{
			uint16_t* visual = GetTerrainVisualMap(p);
			bool changed = false;
			for (int i = 0; i < VISUAL_CELLS * VISUAL_CELLS; i++)
			{
				if (visual[i] & 0x8000)
				{
					if (!changed)
					{
						URDO_Patch(p, true);
						changed = true;
					}
					visual[i] &= ~0x8000;
				}
			}
			if (changed)
				UpdateTerrainVisualMap(p);
		}
	};

	URDO_Open();
	QueryTerrain(terrain, 0.0, 0.0, 1e9, 0xAA, ClearCB::Apply, 0);
	URDO_Close();
}

// ============================================================================
// DEFERRED OPERATION FRAMEWORK [FL-3830]
// ============================================================================
typedef void (*DeferredPatchCB)(Patch* p, int x, int y, int view_flags, void* cookie);
struct DeferredOp
{
	const char* name;
	bool active;
	bool cancelled;
	bool urdo_opened;
	Patch** patches;
	int total;
	int processed;
	DeferredPatchCB apply;
	uint8_t cookie_data[256];
	float progress() const { return total > 0 ? (float)processed / total : 0.0f; }
};
static DeferredOp g_deferred = {};
static void StartDeferredOp(const char* name, DeferredPatchCB apply, const void* cookie, int cookie_size)
{
	if (g_deferred.active || !terrain) return;
	Patch** patches = 0;
	int count = 0;
	GetAllTerrainPatches(terrain, &patches, &count);
	if (count <= 0) { if (patches) free(patches); return; }
	memset(&g_deferred, 0, sizeof(g_deferred));
	g_deferred.name = name;
	g_deferred.active = true;
	g_deferred.patches = patches;
	g_deferred.total = count;
	g_deferred.apply = apply;
	if (cookie && cookie_size > 0 && cookie_size <= (int)sizeof(g_deferred.cookie_data))
		memcpy(g_deferred.cookie_data, cookie, cookie_size);
	printf("[DEFERRED] started %s (%d patches)\n", name, count);
	fflush(stdout);
}
static void StepDeferredOp()
{
	if (!g_deferred.active) return;
	if (g_deferred.cancelled)
	{
		if (g_deferred.urdo_opened) { URDO_Close(); URDO_Undo(64); }
		printf("[DEFERRED] %s cancelled at %d/%d\n", g_deferred.name, g_deferred.processed, g_deferred.total);
		fflush(stdout);
		if (g_deferred.patches) free(g_deferred.patches);
		memset(&g_deferred, 0, sizeof(g_deferred));
		return;
	}
	if (!g_deferred.urdo_opened) { URDO_Open(); g_deferred.urdo_opened = true; }
	uint64_t start = a3dGetTime();
	const uint32_t budget_us = 16000;
	while (g_deferred.processed < g_deferred.total)
	{
		g_deferred.apply(g_deferred.patches[g_deferred.processed], 0, 0, 0xAA, g_deferred.cookie_data);
		g_deferred.processed++;
		if ((uint32_t)(a3dGetTime() - start) >= budget_us) break;
	}
	if (g_deferred.processed >= g_deferred.total)
	{
		URDO_Close();
		printf("[DEFERRED] %s finished (%d patches)\n", g_deferred.name, g_deferred.total);
		fflush(stdout);
		if (g_deferred.patches) free(g_deferred.patches);
		memset(&g_deferred, 0, sizeof(g_deferred));
	}
}
static void RenderDeferredOpProgress()
{
	if (!g_deferred.active) return;
	ImGui::OpenPopup("##deferred_progress");
	if (ImGui::BeginPopupModal("##deferred_progress", NULL, ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoTitleBar))
	{
		ImGui::Text("%s: %d / %d patches (%.0f%%)", g_deferred.name, g_deferred.processed, g_deferred.total, g_deferred.progress() * 100.0f);
		ImGui::ProgressBar(g_deferred.progress(), ImVec2(350, 0));
		if (ImGui::Button("Cancel") || ImGui::IsKeyPressed(ImGui::GetIO().KeyMap[ImGuiKey_Escape]))
			g_deferred.cancelled = true;
		ImGui::EndPopup();
	}
}
// ============================================================================
// CONFIRMATION DIALOG [FL-3830]
// ============================================================================
typedef void (*ConfirmAction)(void* cookie);
struct ConfirmDialog
{
	bool pending;
	bool just_opened;
	char message[512];
	ConfirmAction action;
	uint8_t cookie_data[256];
};
static ConfirmDialog g_confirm = {};
static void RequestConfirm(const char* msg, ConfirmAction action, const void* cookie = 0, int cookie_size = 0)
{
	if (g_deferred.active) return;
	memset(&g_confirm, 0, sizeof(g_confirm));
	g_confirm.pending = true;
	g_confirm.just_opened = true;
	snprintf(g_confirm.message, sizeof(g_confirm.message), "%s", msg);
	g_confirm.action = action;
	if (cookie && cookie_size > 0 && cookie_size <= (int)sizeof(g_confirm.cookie_data))
		memcpy(g_confirm.cookie_data, cookie, cookie_size);
}
static void RenderConfirmDialog()
{
	if (!g_confirm.pending) return;
	if (g_confirm.just_opened) { ImGui::OpenPopup("Confirm##editor_confirm"); g_confirm.just_opened = false; }
	if (ImGui::BeginPopupModal("Confirm##editor_confirm", NULL, ImGuiWindowFlags_AlwaysAutoResize))
	{
		ImGui::TextWrapped("%s", g_confirm.message);
		ImGui::Separator();
		if (ImGui::Button("OK", ImVec2(120, 0)))
		{ g_confirm.action(g_confirm.cookie_data); g_confirm.pending = false; ImGui::CloseCurrentPopup(); }
		ImGui::SameLine();
		if (ImGui::Button("Cancel", ImVec2(120, 0)) || ImGui::IsKeyPressed(ImGui::GetIO().KeyMap[ImGuiKey_Escape]))
		{ g_confirm.pending = false; ImGui::CloseCurrentPopup(); }
		ImGui::EndPopup();
	}
}

static bool g_material_used_ready = false;
static bool g_material_used[256] = { false };

static void RefreshMaterialUsage()
{
	memset(g_material_used, 0, sizeof(g_material_used));
	g_material_used[0] = true;

	if (terrain)
	{
		struct MarkTerrain
		{
			static void Apply(Patch* p, int x, int y, int view_flags, void* cookie)
			{
				bool* used = (bool*)cookie;
				uint16_t* visual = GetTerrainVisualMap(p);
				for (int i = 0; i < VISUAL_CELLS * VISUAL_CELLS; i++)
					used[visual[i] & 0xFF] = true;
			}
		};

		QueryTerrain(terrain, 0.0, 0.0, 1e9, 0xAA, MarkTerrain::Apply, g_material_used);
	}

	if (world)
	{
		Inst** insts = 0;
		int count = CollectMeshInsts(world, &insts);
		if (count > 0 && insts)
		{
			struct MarkMesh
			{
				static void Apply(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie)
				{
					bool* used = (bool*)cookie;
					used[visual & 0xFF] = true;
				}
			};

			Mesh** meshes = (Mesh**)malloc(sizeof(Mesh*) * count);
			int meshes_count = 0;

			for (int i = 0; i < count; i++)
			{
				Mesh* mesh = GetInstMesh(insts[i]);
				if (!mesh)
					continue;
				bool seen = false;
				for (int j = 0; j < meshes_count; j++)
				{
					if (meshes[j] == mesh)
					{
						seen = true;
						break;
					}
				}
				if (seen)
					continue;

				meshes[meshes_count++] = mesh;

				QueryMesh(mesh, MarkMesh::Apply, g_material_used);
			}

			free(meshes);
		}

		free(insts);
	}

	printf("[Material] Used IDs:");
	for (int i = 0; i < 256; i++)
	{
		if (g_material_used[i])
			printf(" %d", i);
	}
	printf("\n");

	g_material_used_ready = true;
}

static uint8_t GetOrAllocateMaterialID(uint8_t rgb[3])
{
	MyMaterial* m = (MyMaterial*)GetMaterialArr();
	int best_id = 1;
	double best_dist = 1e30;
	int free_id = -1;
	int free_fallback = -1;

	// Use Euclidean distance squared
	// Threshold: strictly match baked colors to avoid "mostly right" materials
	double threshold = 5.0 * 5.0;

	if (!g_material_used_ready)
		RefreshMaterialUsage();

	for (int i = 1; i < 256; i++) // Skip material 0 (water)
	{
		// Check usage (Expensive but necessary if defaults are loaded)
		// We only check if an ID matches strictly

		// 1. Check if material looks empty (Black)
		bool looks_empty = (m[i].shade[0][0].bg[0] == 0 && m[i].shade[0][0].bg[1] == 0 && m[i].shade[0][0].bg[2] == 0);
		bool used = g_material_used[i];
		if (!used)
		{
			if (looks_empty && free_id == -1)
				free_id = i;
			else if (!looks_empty && free_fallback == -1)
				free_fallback = i;
		}

		// Don't match against empty slots unless we initialized them
		if (m[i].shade[0][0].bg[0] == 0 && m[i].shade[0][0].bg[1] == 0 && m[i].shade[0][0].bg[2] == 0) continue;

		double dr = (double)rgb[0] - m[i].shade[0][0].bg[0];
		double dg = (double)rgb[1] - m[i].shade[0][0].bg[1];
		double db = (double)rgb[2] - m[i].shade[0][0].bg[2];
		double dist = dr * dr + dg * dg + db * db;
		if (dist < best_dist)
		{
			best_dist = dist;
			best_id = i;
		}
	}
	if (free_id == -1)
		free_id = free_fallback;

	if (best_dist > threshold && free_id != -1)
	{
		// Allocate new material
		int i = free_id;

		// Initialize new material similar to Dirt (Mat 2) pattern but with target color
		uint8_t glyphs[4] = {'.', ':', ',', '\''};

		// Basic lighting ramp logic
		for (int r = 0; r < 4; r++)
		{
			for (int s = 0; s < 16; s++)
			{
				float shade_factor = 1.0f - (s / 16.0f) * 0.6f;

				// Apply shade to requested RGB
				m[i].shade[r][s].bg[0] = (uint8_t)(rgb[0] * shade_factor);
				m[i].shade[r][s].bg[1] = (uint8_t)(rgb[1] * shade_factor);
				m[i].shade[r][s].bg[2] = (uint8_t)(rgb[2] * shade_factor);

				// Lighter foreground
				m[i].shade[r][s].fg[0] = (uint8_t)std::min(255.0, rgb[0] * shade_factor * 1.5);
				m[i].shade[r][s].fg[1] = (uint8_t)std::min(255.0, rgb[1] * shade_factor * 1.5);
				m[i].shade[r][s].fg[2] = (uint8_t)std::min(255.0, rgb[2] * shade_factor * 1.5);

				m[i].shade[r][s].gl = glyphs[r];
				m[i].shade[r][s].flags = 0;
			}
		}
		m[i].Update();
		g_material_used[i] = true;
		return (uint8_t)i;
	}

	return (uint8_t)best_id;
}

struct MeshBake
{
	bool bake_height;
	bool bake_material;
	bool bake_vertex_colors;
	bool overwrite_height;
	bool overwrite_material;
	bool solid_only;
	double ray_top;
	uint8_t material_id;
	Inst** insts;
	int inst_count;

	struct HeightRaster
	{
		MeshBake* ctx;
		int patch_x;
		int patch_y;
		double step;
		double* heights;
		bool* hits;
		double tm[16];
	};

	struct PatchInst
	{
		Inst* inst;
		Mesh* mesh;
		double tm[16];
		double bbox[6];
	};

	struct MeshRaycast
	{
		const double* tm;
		double* ray;
		bool positive_only;
		bool solid_only;
		bool want_color;
		bool hit;
		double ret[3];
		double nrm[3];
		uint8_t color[3];
	};

	static bool RayIntersectsBBox(const double ray[10], const double bbox[6], bool positive_only)
	{
		double tmin = positive_only ? 0.0 : -DBL_MAX;
		double tmax = ray[9];
		for (int axis = 0; axis < 3; axis++)
		{
			double origin = ray[6 + axis];
			double dir = ray[3 + axis];
			double bmin = bbox[axis * 2 + 0];
			double bmax = bbox[axis * 2 + 1];
			if (fabs(dir) < 1e-9)
			{
				if (origin < bmin || origin > bmax)
					return false;
				continue;
			}

			double inv = 1.0 / dir;
			double t0 = (bmin - origin) * inv;
			double t1 = (bmax - origin) * inv;
			if (t0 > t1)
				std::swap(t0, t1);
			tmin = std::max(tmin, t0);
			tmax = std::min(tmax, t1);
			if (tmin > tmax)
				return false;
		}
		return true;
	}

	static void RaycastFace(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie)
	{
		if (visual & (1u << 31))
			return;

		MeshRaycast* raycast = (MeshRaycast*)cookie;
		if (raycast->solid_only)
		{
			if (!((colors[3] | colors[7] | colors[11]) & 0x80))
				return;
		}

		double v0[4] = { coords[0], coords[1], coords[2], 1.0 };
		double v1[4] = { coords[3], coords[4], coords[5], 1.0 };
		double v2[4] = { coords[6], coords[7], coords[8], 1.0 };
		double w0[4];
		double w1[4];
		double w2[4];
		Product(raycast->tm, v0, w0);
		Product(raycast->tm, v1, w1);
		Product(raycast->tm, v2, w2);

		double hit[3];
		double u, v;
		if (!RayIntersectsTriangle(raycast->ray, w0, w1, w2, hit, raycast->positive_only, &u, &v))
			return;

		double d1[3] = { w1[0] - w0[0], w1[1] - w0[1], w1[2] - w0[2] };
		double d2[3] = { w2[0] - w0[0], w2[1] - w0[1], w2[2] - w0[2] };
		CrossProduct(d1, d2, raycast->nrm);
		memcpy(raycast->ret, hit, sizeof(hit));
		if (raycast->want_color)
		{
			double w = 1.0 - u - v;
			raycast->color[0] = (uint8_t)(colors[0] * w + colors[4] * u + colors[8] * v);
			raycast->color[1] = (uint8_t)(colors[1] * w + colors[5] * u + colors[9] * v);
			raycast->color[2] = (uint8_t)(colors[2] * w + colors[6] * u + colors[10] * v);
		}
		raycast->hit = true;
	}

	static Inst* HitPatchInsts(const std::vector<PatchInst>& patch_insts, double p[3], double v[3], double ret[3], double nrm[3],
		bool positive_only, bool solid_only, uint8_t* out_color)
	{
		double ray[] =
		{
			p[1] * v[2] - p[2] * v[1],
			p[2] * v[0] - p[0] * v[2],
			p[0] * v[1] - p[1] * v[0],
			v[0], v[1], v[2],
			p[0], p[1], p[2],
			FLT_MAX
		};

		Inst* best = 0;
		for (const PatchInst& patch_inst : patch_insts)
		{
			if (!patch_inst.mesh)
				continue;
			if (!RayIntersectsBBox(ray, patch_inst.bbox, positive_only))
				continue;

			MeshRaycast raycast = {};
			raycast.tm = patch_inst.tm;
			raycast.ray = ray;
			raycast.positive_only = positive_only;
			raycast.solid_only = solid_only;
			raycast.want_color = out_color != 0;
			QueryMesh(patch_inst.mesh, RaycastFace, &raycast);
			if (!raycast.hit)
				continue;

			best = patch_inst.inst;
			memcpy(ret, raycast.ret, sizeof(raycast.ret));
			if (nrm)
				memcpy(nrm, raycast.nrm, sizeof(raycast.nrm));
			if (out_color)
				memcpy(out_color, raycast.color, sizeof(raycast.color));
		}

		return best;
	}

	// WHY rasterize mesh triangles to terrain height map:
	// MeshBaker "bakes" 3D mesh geometry into the 2D terrain system for collision
	// and rendering. Each triangle is rasterized to the terrain height map by
	// iterating cells covered by the triangle's bounding box, computing barycentric
	// coordinates, and writing the interpolated height. This converts 3D mesh data
	// (.akm from Blender) into 2D terrain data (height map + material grid).
	static void RasterizeHeightFace(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie)
	{
		if (visual & (1u << 31))
			return;

		HeightRaster* raster = (HeightRaster*)cookie;
		if (raster->ctx->solid_only)
		{
			if (!((colors[3] | colors[7] | colors[11]) & 0x80))
				return;
		}

		double v0[4] = { coords[0], coords[1], coords[2], 1.0 };
		double v1[4] = { coords[3], coords[4], coords[5], 1.0 };
		double v2[4] = { coords[6], coords[7], coords[8], 1.0 };
		double w0[4];
		double w1[4];
		double w2[4];
		Product(raster->tm, v0, w0);
		Product(raster->tm, v1, w1);
		Product(raster->tm, v2, w2);

		double min_x = std::min(w0[0], std::min(w1[0], w2[0]));
		double max_x = std::max(w0[0], std::max(w1[0], w2[0]));
		double min_y = std::min(w0[1], std::min(w1[1], w2[1]));
		double max_y = std::max(w0[1], std::max(w1[1], w2[1]));

		double patch_min_x = (double)raster->patch_x;
		double patch_min_y = (double)raster->patch_y;
		double patch_max_x = patch_min_x + VISUAL_CELLS;
		double patch_max_y = patch_min_y + VISUAL_CELLS;
		if (max_x < patch_min_x || min_x > patch_max_x || max_y < patch_min_y || min_y > patch_max_y)
			return;

		double x0 = w0[0];
		double y0 = w0[1];
		double z0 = w0[2];
		double x1 = w1[0];
		double y1 = w1[1];
		double z1 = w1[2];
		double x2 = w2[0];
		double y2 = w2[1];
		double z2 = w2[2];

		double denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
		if (fabs(denom) < 1e-9)
			return;

		double inv_denom = 1.0 / denom;
		double step = raster->step;
		int hx0 = (int)floor((min_x - patch_min_x) / step);
		int hx1 = (int)ceil((max_x - patch_min_x) / step);
		int hy0 = (int)floor((min_y - patch_min_y) / step);
		int hy1 = (int)ceil((max_y - patch_min_y) / step);
		hx0 = std::max(0, std::min(HEIGHT_CELLS, hx0));
		hx1 = std::max(0, std::min(HEIGHT_CELLS, hx1));
		hy0 = std::max(0, std::min(HEIGHT_CELLS, hy0));
		hy1 = std::max(0, std::min(HEIGHT_CELLS, hy1));

		for (int hy = hy0; hy <= hy1; hy++)
		{
			double py = patch_min_y + hy * step;
			for (int hx = hx0; hx <= hx1; hx++)
			{
				double px = patch_min_x + hx * step;
				double b0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) * inv_denom;
				double b1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) * inv_denom;
				double b2 = 1.0 - b0 - b1;

				if (b0 < -1e-6 || b1 < -1e-6 || b2 < -1e-6)
					continue;

				double z = b0 * z0 + b1 * z1 + b2 * z2;
				int idx = hx + hy * (HEIGHT_CELLS + 1);
				if (!raster->hits[idx] || z > raster->heights[idx])
				{
					raster->heights[idx] = z;
					raster->hits[idx] = true;
				}
			}
		}
	}

	// WHY iterate all mesh faces to bake height and material:
	// MeshBaker::Apply is called for each terrain patch overlapping the bake region.
	// For each patch, it iterates all mesh instances, queries each mesh's faces via
	// RasterizeHeightFace callback, and writes the maximum height to the terrain.
	// Material baking (if enabled) writes the mesh face color to terrain visual cells.
	// This converts 3D mesh geometry to 2D terrain representation for collision/rendering.
	static void Apply(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		MeshBake* ctx = (MeshBake*)cookie;
		uint16_t* visual = GetTerrainVisualMap(p);
		uint16_t* height = GetTerrainHeightMap(p);
		const double step = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;
		const double elev_step = 1.0;
		const double elev_threshold = (double)HEIGHT_SCALE * 4.0;

		bool changed = false;
		std::vector<PatchInst> patch_insts;
		patch_insts.reserve(ctx->inst_count);
		const double patch_min_x = (double)x;
		const double patch_min_y = (double)y;
		const double patch_max_x = patch_min_x + VISUAL_CELLS;
		const double patch_max_y = patch_min_y + VISUAL_CELLS;

		for (int i = 0; i < ctx->inst_count; i++)
		{
			Inst* inst = ctx->insts[i];
			if (!inst)
				continue;
			int flags = GetInstFlags(inst);
			if (flags & INST_VOLATILE)
				continue;
			if (!(flags & INST_VISIBLE))
				continue;

			PatchInst patch_inst = {};
			patch_inst.inst = inst;
			patch_inst.mesh = GetInstMesh(inst);
			if (!patch_inst.mesh)
				continue;
			GetInstBBox(inst, patch_inst.bbox);
			if (patch_inst.bbox[1] < patch_min_x || patch_inst.bbox[0] > patch_max_x ||
				patch_inst.bbox[3] < patch_min_y || patch_inst.bbox[2] > patch_max_y)
				continue;
			if (!GetInstTM(inst, patch_inst.tm))
				continue;
			patch_insts.push_back(patch_inst);
		}

		if (patch_insts.empty())
			return;

		if (ctx->bake_height)
		{
			double heights[(HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1)];
			bool hits[(HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1)];
			for (int i = 0; i < (HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1); i++)
			{
				heights[i] = -1e9;
				hits[i] = false;
			}

			for (const PatchInst& patch_inst : patch_insts)
			{
				HeightRaster raster = {};
				raster.ctx = ctx;
				raster.patch_x = x;
				raster.patch_y = y;
				raster.step = step;
				raster.heights = heights;
				raster.hits = hits;
				memcpy(raster.tm, patch_inst.tm, sizeof(raster.tm));
				QueryMesh(patch_inst.mesh, RasterizeHeightFace, &raster);
			}

			for (int hy = 0; hy <= HEIGHT_CELLS; hy++)
			{
				for (int hx = 0; hx <= HEIGHT_CELLS; hx++)
				{
					int idx = hx + hy * (HEIGHT_CELLS + 1);
					if (!hits[idx])
						continue;

					// FL-1181: bake heights are quantized to 16-unit terrain steps.
					// If a caller anchors mesh terrain to an off-grid floor like 120,
					// edge samples can round down to 112. Topology callers that want
					// a "do not cut below floor" contract must not use overwrite_height=1.
					int final_height = (int)(round(heights[idx] / 16.0) * 16.0);
					if (final_height < 0)
						final_height = 0;
					uint16_t h = (uint16_t)std::min(0xFFFF, final_height);

					if (!ctx->overwrite_height && h <= height[idx])
						continue;

					if (!changed)
					{
						URDO_Patch(p, true);
						changed = true;
					}
					height[idx] = h;
				}
			}
		}

		if (ctx->bake_material)
		{
			bool wall_hit_mask[VISUAL_CELLS * VISUAL_CELLS] = {};
			// Vertex-color baking is the expensive lane; use center samples and let the
			// footprint fill pass expand coverage instead of spending 9x the raycasts.
			const int samples_per_axis = ctx->bake_vertex_colors ? 1 : 3;

			for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
			{
				for (int u = 0; u < VISUAL_CELLS; u++, i++)
				{
					// Supersample for robust material detection

					struct CellBakeData {
						float max_height = -1e9f;
						bool hit = false;
						uint8_t color[3] = {0,0,0};
						float wall_height = -1e9f;
						bool wall_hit = false;
						uint8_t wall_color[3] = {0,0,0};
					} cell_data;

					for (int sy = 0; sy < samples_per_axis; sy++)
						for (int sx = 0; sx < samples_per_axis; sx++)
						{
							double wx = x + u + (samples_per_axis == 1 ? 0.5 : (sx + 1.0) / 4.0);
							double wy = y + v + (samples_per_axis == 1 ? 0.5 : (sy + 1.0) / 4.0);

							double p0[3] = { wx, wy, ctx->ray_top };
							double jitter_x = samples_per_axis == 1 ? 0.0 : 0.15 * (sx - 1);
							double jitter_y = samples_per_axis == 1 ? 0.0 : 0.15 * (sy - 1);
							double v0[3] = { jitter_x, jitter_y, -1 };
							double hit[3];
							double nrm[3] = {0,0,1};
							uint8_t color[3];
							Inst* inst = HitPatchInsts(patch_insts, p0, v0, hit, nrm, true, ctx->solid_only, ctx->bake_vertex_colors ? color : 0);

							if (inst) {
								double len = sqrt(nrm[0]*nrm[0] + nrm[1]*nrm[1] + nrm[2]*nrm[2]);
								if (len > 1e-6) {
									nrm[0] /= len; nrm[1] /= len; nrm[2] /= len;
								}
								bool is_wall = fabs(nrm[2]) < 0.7;
								if (is_wall) {
									cell_data.wall_hit = true;
									if (hit[2] > cell_data.wall_height) {
										cell_data.wall_height = hit[2];
										if (ctx->bake_vertex_colors) memcpy(cell_data.wall_color, color, 3);
									}
								}

								if (hit[2] > cell_data.max_height) {
									cell_data.max_height = hit[2];
									cell_data.hit = true;
									if (ctx->bake_vertex_colors) memcpy(cell_data.color, color, 3);
								}
							}
						}

					if (!cell_data.hit)
						continue;

					wall_hit_mask[i] = cell_data.wall_hit;

					uint16_t old = visual[i];
					uint8_t old_id = (uint8_t)(old & 0xFF);
					if (!ctx->overwrite_material && old_id != 0)
						continue;

					uint8_t id = ctx->material_id;
					if (ctx->bake_vertex_colors) {
						uint8_t* bake_color = cell_data.wall_hit ? cell_data.wall_color : cell_data.color;
						id = GetOrAllocateMaterialID(bake_color);
					}

					// Elevation based on local height delta, not absolute height.
					// Require a drop of >4 height steps to avoid low-height walls.
					double fx = (u + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
					double fy = (v + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
					uint16_t elev_mask = HasElevationDelta(height, fx, fy, elev_step, elev_threshold) ? 0x8000 : 0;

					uint16_t next = (old & 0x7F00) | elev_mask | id;

					if (next == old)
						continue;

					if (!changed)
					{
						URDO_Patch(p, true);
						changed = true;
					}
					visual[i] = next;
				}
			}

			// Fill untextured elevated cells using the height bake footprint.
			if (ctx->bake_height)
			{
				uint16_t base_visual[VISUAL_CELLS * VISUAL_CELLS];
				memcpy(base_visual, visual, sizeof(base_visual));

				const int ring_radius = 2;
				const int search_radius = 3;

				auto HasWallNearby = [&](int u, int v) -> bool
				{
					for (int dv = -ring_radius; dv <= ring_radius; dv++)
					{
						for (int du = -ring_radius; du <= ring_radius; du++)
						{
							int uu = u + du;
							int vv = v + dv;
							if (uu < 0 || uu >= VISUAL_CELLS || vv < 0 || vv >= VISUAL_CELLS)
								continue;
							if (wall_hit_mask[uu + vv * VISUAL_CELLS])
								return true;
						}
					}
					return false;
				};

				auto FindNearestMatID = [&](int u, int v, bool prefer_walls) -> uint8_t
				{
					int best_dist = 999;
					uint8_t best_id = 0;
					for (int dv = -search_radius; dv <= search_radius; dv++)
					{
						for (int du = -search_radius; du <= search_radius; du++)
						{
							int uu = u + du;
							int vv = v + dv;
							if (uu < 0 || uu >= VISUAL_CELLS || vv < 0 || vv >= VISUAL_CELLS)
								continue;
							uint16_t vis = base_visual[uu + vv * VISUAL_CELLS];
							uint8_t id = (uint8_t)(vis & 0xFF);
							if (!id)
								continue;
							if (prefer_walls && !wall_hit_mask[uu + vv * VISUAL_CELLS])
								continue;
							int dist = abs(du) + abs(dv);
							if (dist < best_dist)
							{
								best_dist = dist;
								best_id = id;
							}
						}
					}
					return best_id;
				};

				for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
				{
					for (int u = 0; u < VISUAL_CELLS; u++, i++)
					{
						uint16_t old = base_visual[i];
						uint8_t old_id = (uint8_t)(old & 0xFF);
						if (!ctx->overwrite_material && old_id != 0)
							continue;

						bool wants_fill = wall_hit_mask[i] || HasWallNearby(u, v);
						if (!wants_fill)
							continue;

						uint8_t id = FindNearestMatID(u, v, true);
						if (!id)
							id = FindNearestMatID(u, v, false);
						if (!id)
							continue;

						uint16_t next = (old & 0xFF00) | id;
						if (next == old)
							continue;

						if (!changed)
						{
							URDO_Patch(p, true);
							changed = true;
						}
						visual[i] = next;
					}
				}
			}
		}

		if (changed)
		{
			if (ctx->bake_height)
				UpdateTerrainHeightMap(p);
			if (ctx->bake_material)
				UpdateTerrainVisualMap(p);
		}
	}
};
// Per-instance result from BakeMeshesToTerrain (FL-1181 Candidate 1).
// Cells stuck at BAKE_COVERAGE_BASELINE after bake are terrain holes —
// the player falls through them. The old _report_terrain_floor_regressions
// gate (h < baseline) was blind to these; this surface makes them visible.
//
// WHY 128 not 120 (FL-1181 Candidate 2): The bake quantizes heights to
// multiples of HEIGHT_SCALE (16). 120 % 16 = 8, so 120 is off-grid.
// Edge samples at ~119 quantized DOWN to 112. With overwrite_height=0,
// the gate skipped writes where 112 <= 120, leaving cells at 120 in raised
// terrain — terrain holes (FL-1181, FL-2546, FL-2573).
// 128 = 8 × HEIGHT_SCALE is on-grid. Must match BAKE_COVERAGE_BASELINE in
// osm_bake_contract.py and TERRAIN_EXPORT_BASELINE in a3d_format.py.
static const uint16_t BAKE_COVERAGE_BASELINE = 128;

struct BakeInstCoverage {
	char name[256];
	int footprint_cells;   // height vertices inside instance AABB
	int above_baseline;    // height vertices > BAKE_COVERAGE_BASELINE after bake
	int at_baseline;       // height vertices == BAKE_COVERAGE_BASELINE (holes)
};

struct CoverageQuery {
	double bbox[6];  // [xmin, xmax, ymin, ymax, zmin, zmax]
	int footprint_cells;
	int above_baseline;
	int at_baseline;

	static void Apply(Patch* p, int px, int py, int view_flags, void* cookie)
	{
		CoverageQuery* q = (CoverageQuery*)cookie;
		uint16_t* height = GetTerrainHeightMap(p);
		const double step = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;
		for (int hy = 0; hy <= HEIGHT_CELLS; hy++)
		{
			for (int hx = 0; hx <= HEIGHT_CELLS; hx++)
			{
				double wx = (double)px + hx * step;
				double wy = (double)py + hy * step;
				if (wx < q->bbox[0] || wx > q->bbox[1] ||
					wy < q->bbox[2] || wy > q->bbox[3])
					continue;
				uint16_t h = height[hx + hy * (HEIGHT_CELLS + 1)];
				q->footprint_cells++;
				if (h > BAKE_COVERAGE_BASELINE)
					q->above_baseline++;
				else if (h == BAKE_COVERAGE_BASELINE)
					q->at_baseline++;
			}
		}
	}
};

static std::vector<BakeInstCoverage> BakeMeshesToTerrain(bool bake_height, bool bake_material, bool bake_vertex_colors, bool overwrite_height,
	bool overwrite_material, bool solid_only, double ray_top, uint8_t material_id)
{
	if (!terrain || !world)
		return {};

	if (bake_material && bake_vertex_colors)
		RefreshMaterialUsage();

	Inst** insts = 0;
	int inst_count = 0;
	if (bake_height || bake_material)
		inst_count = CollectMeshInsts(world, &insts);
	if (!inst_count)
	{
		if (insts)
			free(insts);
		return {};
	}

	MeshBake ctx = { bake_height, bake_material, bake_vertex_colors, overwrite_height, overwrite_material,
		solid_only, ray_top, material_id, insts, inst_count };
	struct ScopedBakeQuery
	{
		MeshBake* ctx;
		std::unordered_set<Patch*> seen;

		static void ApplyOnce(Patch* p, int x, int y, int view_flags, void* cookie)
		{
			ScopedBakeQuery* scoped = (ScopedBakeQuery*)cookie;
			if (!scoped->seen.insert(p).second)
				return;
			MeshBake::Apply(p, x, y, view_flags, scoped->ctx);
		}
	} scoped = { &ctx };

	URDO_Open();
	for (int i = 0; i < inst_count; i++)
	{
		Inst* inst = insts[i];
		if (!inst)
			continue;
		int flags = GetInstFlags(inst);
		if (flags & INST_VOLATILE)
			continue;
		if (!(flags & INST_VISIBLE))
			continue;

		double bbox[6];
		GetInstBBox(inst, bbox);
		const double cx = 0.5 * (bbox[0] + bbox[1]);
		const double cy = 0.5 * (bbox[2] + bbox[3]);
		const double dx = bbox[1] - bbox[0];
		const double dy = bbox[3] - bbox[2];
		const double radius = std::max(0.5 * sqrt(dx * dx + dy * dy) + VISUAL_CELLS, (double)VISUAL_CELLS);
		QueryTerrain(terrain, cx, cy, radius, 0xAA, ScopedBakeQuery::ApplyOnce, &scoped);
	}
	URDO_Close();

	// Post-bake per-instance coverage scan (FL-1181 / Candidate 1).
	// Reads terrain heights in each instance's AABB after the bake completes.
	// Cells still at BAKE_COVERAGE_BASELINE are holes — the bake wrote nothing
	// there, either because overwrite_height=0 skipped them (quantized edge
	// samples round down below 120 and 112 < 120 triggers the skip guard) or
	// because the AKMs loaded at wrong scale and missed the footprint entirely.
	std::vector<BakeInstCoverage> coverage;
	if (bake_height && inst_count > 0)
	{
		for (int i = 0; i < inst_count; i++)
		{
			Inst* inst = insts[i];
			if (!inst)
				continue;
			int flags = GetInstFlags(inst);
			if (flags & INST_VOLATILE)
				continue;
			if (!(flags & INST_VISIBLE))
				continue;

			double bbox[6];
			GetInstBBox(inst, bbox);
			const double cx = 0.5 * (bbox[0] + bbox[1]);
			const double cy = 0.5 * (bbox[2] + bbox[3]);
			const double dx = bbox[1] - bbox[0];
			const double dy = bbox[3] - bbox[2];
			const double radius = std::max(0.5 * sqrt(dx * dx + dy * dy) + VISUAL_CELLS, (double)VISUAL_CELLS);

			CoverageQuery q = {};
			memcpy(q.bbox, bbox, sizeof(bbox));
			QueryTerrain(terrain, cx, cy, radius, 0xAA, CoverageQuery::Apply, &q);

			BakeInstCoverage cov = {};
			const char* inst_name = GetInstName(inst);
			if (inst_name)
				snprintf(cov.name, sizeof(cov.name), "%s", inst_name);
			cov.footprint_cells = q.footprint_cells;
			cov.above_baseline = q.above_baseline;
			cov.at_baseline = q.at_baseline;
			coverage.push_back(cov);
		}
	}

	if (insts)
		free(insts);
	return coverage;
}

static void ClearSelection()
{
	Inst** insts;
	int count = CollectMeshInsts(world, &insts);
	for (int i = 0; i < count; i++)
		SetInstFlags(insts[i], GetInstFlags(insts[i]) & ~INST_SELECTED);
	free(insts);
	inst_list_dirty = true;
}

// WHY screen-space AABB selection with perspective projection:
// SelectArea converts a 2D screen rectangle (p1, p2) to 3D world bounds for
// selecting mesh instances. It uses inverse projection (screen → world) to
// compute 3D bounding box corners, then tests each mesh instance's position
// against the box. This enables rectangle-drag selection in the 3D viewport.
// The perspective projection means screen-space rectangles map to frustum
// volumes in world space, not simple axis-aligned boxes.
static void SelectArea(const double tm[16], ImVec2 p1, ImVec2 p2)
{
	float x1 = std::min(p1.x, p2.x);
	float y1 = std::min(p1.y, p2.y);
	float x2 = std::max(p1.x, p2.x);
	float y2 = std::max(p1.y, p2.y);

	if (abs(x1 - x2) < 2 && abs(y1 - y2) < 2) return;

	Inst** insts;
	int count = CollectMeshInsts(world, &insts);
	int sel_count = 0;
	ImGuiIO& io = ImGui::GetIO();

	printf("[MARQUEE] rect=(%.0f,%.0f)-(%.0f,%.0f) display=%.0fx%.0f fbscale=%.1fx%.1f\n",
		x1, y1, x2, y2, io.DisplaySize.x, io.DisplaySize.y,
		io.DisplayFramebufferScale.x, io.DisplayFramebufferScale.y);

	for (int i = 0; i < count; i++)
	{
		double bbox[6];
		GetInstBBox(insts[i], bbox);

		float min_sx = 1e9, max_sx = -1e9;
		float min_sy = 1e9, max_sy = -1e9;
		int visible_verts = 0;

		for(int c=0; c<8; c++)
		{
			double pos[4] = {
				(c&1) ? bbox[1] : bbox[0],
				(c&2) ? bbox[3] : bbox[2],
				(c&4) ? bbox[5] : bbox[4],
				1.0
			};
			double r[4];
			Product(tm, pos, r);

			if (r[3] > 0.1) // Avoid near plane issues
			{
				float sx = (float)((r[0] / r[3] + 1.0) * 0.5 * io.DisplaySize.x);
				float sy = (float)((1.0 - r[1] / r[3]) * 0.5 * io.DisplaySize.y);

				min_sx = std::min(min_sx, sx);
				max_sx = std::max(max_sx, sx);
				min_sy = std::min(min_sy, sy);
				max_sy = std::max(max_sy, sy);
				visible_verts++;
			}
		}

		if (visible_verts > 0)
		{
			// Use center-point containment: select only if the projected bbox center
			// falls inside the marquee. Prevents giant-bbox meshes (buildings) from
			// being selected by any small marquee within their footprint.
			float cx = (min_sx + max_sx) * 0.5f;
			float cy = (min_sy + max_sy) * 0.5f;
			if (cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2)
			{
				SetInstFlags(insts[i], GetInstFlags(insts[i]) | INST_SELECTED);
				sel_count++;
			}
		}
	}
	printf("[MARQUEE] selected %d of %d instances\n", sel_count, count);
	fflush(stdout);
	inst_list_dirty = true;
	free(insts);
}

static void DeleteSelected()
{
	Inst** insts;
	int count = CollectMeshInsts(world, &insts);
	bool opened = false;
	for (int i = 0; i < count; i++)
	{
		if (GetInstFlags(insts[i]) & INST_SELECTED)
		{
			if (selected_inst == insts[i]) selected_inst = 0;
			if (drag_inst == insts[i]) drag_inst = 0;
			if (!opened) { URDO_Open(); opened = true; }
			URDO_Delete(insts[i]);
		}
	}
	if (opened) { URDO_Close(); inst_list_dirty = true; }
	free(insts);
}

static void DeleteAllMeshInsts()
{
	if (!world)
		return;

	Inst** insts = 0;
	int count = CollectMeshInsts(world, &insts);
	if (count <= 0)
		return;

	URDO_Open();
	for (int i = 0; i < count; i++)
		URDO_Delete(insts[i]);
	URDO_Close();

	free(insts);
	inst_list_dirty = true;
	RebuildWorld(world);
}


struct Gather
{
	int x, y; // patch aligned
	int count; // number of actually queried patches
	int size; // in patches
	int* tmp_x;
	int* tmp_y;
	Patch* patch[1];

	int GetPatchIdx(int px, int py)
	{
		int dx = px - x;
		int dy = py - y;

		int bx = dx / VISUAL_CELLS;
		int by = dy / VISUAL_CELLS;

		assert(bx >= 0 && bx < size && by >= 0 && by < size);
		return bx + by * size;
	}

	int Sample(int hx, int hy) // hx and hy are in height map samples relative to Gather::x,y
	{
		int px = hx / HEIGHT_CELLS;
		int py = hy / HEIGHT_CELLS;

		int sx = hx % HEIGHT_CELLS;
		int sy = hy % HEIGHT_CELLS;

		int idx = px + py * size;
		Patch* p = patch[idx];

		if (!p)
			return -1;

		uint16_t* map = GetTerrainHeightMap(p);

		return map[sx + sy * (HEIGHT_CELLS + 1)];
	}
};



Gather* gather = 0;

static void GatherCB(Patch* p, int x, int y, int view_flags, void* cookie)
{
	gather->count++;
	gather->patch[gather->GetPatchIdx(x, y)] = p;
}

static void StampCB(Patch* p, int x, int y, int view_flags, void* cookie)
{
	double mul = br_alpha * br_radius * HEIGHT_SCALE;
	if (fabs(mul) < 0.499)
		return;

	uint16_t lo, hi;
	GetTerrainLimits(p, &lo, &hi);
	if (hi == 0 && br_alpha < 0 || lo == 0xffff && br_alpha>0)
		return;

	if (g_painted_patches.insert(p).second)
		URDO_Patch(p);

	double* xy = (double*)cookie;
	uint16_t* map = GetTerrainHeightMap(p);

	const static double sxy = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;

	double max_r2 = 0;

	for (int i=0, hy = 0; hy <= HEIGHT_CELLS; hy++)
	{
		double dy = y + sxy * hy - xy[1];
		dy *= dy;
		for (int hx = 0; hx <= HEIGHT_CELLS; hx++, i++)
		{
			double dx = x + sxy * hx - xy[0];
			dx *= dx;

			double len = sqrt(dx + dy);
			double gauss = 0;
			if (brush_shape == 1) // Square
			{
				// dx and dy are actually squared distances here
				if (dx < br_radius*br_radius && dy < br_radius*br_radius)
					gauss = 1.0;
			}
			else if (len < br_radius)
			{
				if (brush_shape == 0) // Gaussian
					gauss = 0.5 + 0.5*cos(len / br_radius * M_PI);
				else if (brush_shape == 2) // Noise
					gauss = (0.5 + 0.5*cos(len / br_radius * M_PI)) * ((fast_rand() & 255) / 255.0);
			}

				if (gauss > 0)
				{

				int d = (int)(round(gauss*gauss * mul));
				if (d)
					max_r2 = fmax(max_r2, dx + dy);

				int z = map[i] + d;

				if (br_limit)
				{
					if (d > 0)
					{
						if (map[i] > probe_z)
							z = map[i];
						else
						if (z > probe_z)
							z = probe_z;
					}
					else
					if (d < 0)
					{
						if (map[i] < probe_z)
							z = map[i];
						else
						if (z < probe_z)
							z = probe_z;
					}
				}
				else
				{
					if (z < 0)
						z = 0;
					if (z > 0xffff)
						z = 0xffff;
				}
				map[i] = z;
			}
		}
	}

	xy[2] = fmax(xy[2], max_r2);
	UpdateTerrainHeightMap(p);
}

// WHY Gaussian brush with terrain height accumulation:
// Terrain height editing uses a brush "stamp" approach where each mouse-drag step
// modifies terrain height at the cursor position. The brush shape (Gaussian, Square,
// or Noise) defines the falloff pattern, and br_alpha controls intensity. Heights are
// accumulated (not set absolutely) so multiple strokes build up terrain gradually.
//
// WHY the gather/stamp callback pattern:
// GatherCB collects all patches in brush radius, then Stamp() iterates height cells
// within each patch, computing distance to brush center and applying falloff.
// This two-pass approach avoids modifying patches while iterating (terrain query
// uses spatial index that would be invalidated by mid-iteration changes).
//
// WHY br_alpha sign controls ascent/descent:
// Positive br_alpha raises terrain (adds to height map), negative lowers it.
// The sign carries through the falloff calculation, making ascent/descent symmetric.
void Stamp(double x, double y, int forced_mode = 0)
{
	// query all patches int radial range br_xyra[2] from x,y
	// get their heightmaps apply brush on height samples and update TexHeap pages

	if (!painting)
		g_painted_patches.clear();

	int stamp_mode;
	if (forced_mode > 0)
		stamp_mode = forced_mode;
	else
	{
		ImGuiIO& io = ImGui::GetIO();
		stamp_mode = io.KeysDown[A3D_LSHIFT] ? 2 : 1;
	}

	if (stamp_mode == 1)
	{
		URDO_Open();
		double xy[3] = { x,y,0 };
		QueryTerrain(terrain, x, y, br_radius * 1.5, 0x00, StampCB, xy);
		URDO_Close();
	}
	else
	{
		double mul = br_alpha * br_radius * HEIGHT_SCALE;
		if (fabs(mul) < 0.499)
			return;

		// gather
		int size = 4 * (int)ceil(br_radius / VISUAL_CELLS) + 2;
		int tmp_buf_size = sizeof(int)*(size*HEIGHT_CELLS)*(size*HEIGHT_CELLS);
		if (!gather || gather->size != size)
		{
			if (gather)
			{
				free(gather->tmp_x);
				free(gather->tmp_y);
				free(gather);
			}
			int bs = sizeof(Gather) + sizeof(Patch*)*(size*size - 1);
			gather = (Gather*)malloc(bs);
			gather->size = size;

			gather->tmp_x = (int*)malloc(tmp_buf_size);
			gather->tmp_y = (int*)malloc(tmp_buf_size);
		}

		memset(gather->patch, 0, sizeof(Patch*)*(size*size));

		gather->x = (int)floor(x / VISUAL_CELLS - 0.5 * size) * VISUAL_CELLS;
		gather->y = (int)floor(y / VISUAL_CELLS - 0.5 * size) * VISUAL_CELLS;

		gather->count=0;
		QueryTerrain(terrain, x, y, 2.0*br_radius, 0x00, GatherCB, 0);

		if (!gather->count)
			return;

		int* tmp_x = gather->tmp_x;
		memset(tmp_x, -1, tmp_buf_size);

		int r = (int)floor(br_radius * HEIGHT_CELLS / VISUAL_CELLS);
		for (int hy = 0; hy < size * HEIGHT_CELLS; hy++)
		{
			for (int hx = r; hx < size * HEIGHT_CELLS - r; hx++)
			{
				double acc = 0;
				double den = 0;

				for (int sx = hx-r; sx < hx+r; sx++)
				{
					int h = gather->Sample(sx, hy);
					if (h >= 0)
					{
						// HERE we use TRUE gaussian filter (must be separable)
						double len = (double)sx * VISUAL_CELLS / HEIGHT_CELLS + gather->x - x;
						len /= br_radius;
						double gauss = exp(-len * len * 3);

						acc += h * gauss;
						den += gauss;
					}
				}

				if (den > 0)
					tmp_x[hx + hy * size * HEIGHT_CELLS] = (uint16_t)round(acc / den);
				else
					tmp_x[hx + hy * size * HEIGHT_CELLS] = -1;
			}
		}

		int* tmp_y = gather->tmp_y;
		memset(tmp_y, -1, tmp_buf_size);

		for (int hy = r; hy < size * HEIGHT_CELLS - r; hy++)
		{
			for (int hx = r; hx < size * HEIGHT_CELLS - r; hx++)
			{
				double acc = 0;
				double den = 0;

				for (int sy = hy - r; sy < hy + r; sy++)
				{
					int h = tmp_x[hx + sy * size * HEIGHT_CELLS];
					if (h >= 0)
					{
						// HERE we use TRUE gaussian filter (must be separable)
						double len = (double)sy * VISUAL_CELLS / HEIGHT_CELLS + gather->y - y;
						len /= br_radius;
						double gauss = exp(-len*len*3);

						acc += h * gauss;
						den += gauss;
					}
				}

				if (den > 0)
					tmp_y[hx + hy * size * HEIGHT_CELLS] = (uint16_t)round(acc / den);
				else
					tmp_y[hx + hy * size * HEIGHT_CELLS] = -1;
			}
		}

		// run all patches
		URDO_Open();
		for (int py = gather->size/4; py < gather->size - gather->size / 4; py++)
		{
			for (int px = gather->size / 4; px < gather->size - gather->size / 4; px++)
			{
				Patch* p = gather->patch[px + size * py];
				if (p)
				{
					URDO_Patch(p);
					uint16_t* map = GetTerrainHeightMap(p);

					for (int sy = 0; sy <= HEIGHT_CELLS; sy++)
					{
						int hy = (HEIGHT_CELLS * py + sy);
						double dy = gather->y + hy * VISUAL_CELLS / (double)HEIGHT_CELLS - y;
						dy *= dy;
						for (int sx = 0; sx <= HEIGHT_CELLS; sx++)
						{
							int hx = (HEIGHT_CELLS * px + sx);
							double dx = gather->x + hx * VISUAL_CELLS / (double)HEIGHT_CELLS - x;
							dx *= dx;

							double len = sqrt(dx + dy);

							if (len < br_radius)
							{
								double gauss = 0.5 + 0.5*cos(len / br_radius * M_PI);
								gauss *= gauss * br_alpha;

								if (gauss < 0)
								{
									double diff = gauss * (tmp_y[hx + hy * size * HEIGHT_CELLS] - map[sx + sy * (HEIGHT_CELLS + 1)]);
									int z = (int)round(diff) + map[sx + sy * (HEIGHT_CELLS + 1)];
									if (z < 0)
										z = 0;
									if (z > 0xffff)
										z = 0xffff;

									map[sx + sy * (HEIGHT_CELLS + 1)] = z;
								}
								else
								{
									double blend = map[sx + sy * (HEIGHT_CELLS + 1)] * (1.0 - gauss);
									blend += tmp_y[hx + hy * size * HEIGHT_CELLS] * gauss;
									map[sx + sy * (HEIGHT_CELLS + 1)] = (uint16_t)round(blend);
								}
							}
						}
					}

					UpdateTerrainHeightMap(p);
				}
			}
		}
		URDO_Close();
	}
}

// WHY RGB-to-palette conversion via GPU 3D texture lookup:
// Palettize converts RGB colors to palette indices using a 3D texture as a
// lookup table (16x16x16 RGB cube → palette index). This enables GPU-accelerated
// nearest-color matching for sprite rendering. The 3D texture is populated on
// first call, then uploaded to GPU. Each RGB coordinate (r,g,b) in texture
// space maps to the nearest palette index, avoiding CPU-side distance calculations.
void Palettize(const uint8_t p[768])
{
	if (!p && ipal)
	{
		free(ipal);
		ipal = 0;
	}
	else
	if (p && !ipal)
	{
		ipal = (uint8_t*)malloc(1<<24);
	}

	//glFinish();
	uint64_t t0 = a3dGetTime();

	GLuint vbo;
	gl3CreateBuffers(1, &vbo);
	float quad[8] = { 0,0,1,0,1,1,0,1 };
	gl3NamedBufferStorage(vbo, sizeof(float[2])*4, quad, 0);

	GLuint vao;
	gl3CreateVertexArrays(1, &vao);
	glBindVertexArray(vao);
	glBindBuffer(GL_ARRAY_BUFFER, vbo);
	glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, sizeof(float[2]), (void*)0);
	glBindBuffer(GL_ARRAY_BUFFER, 0);
	glEnableVertexAttribArray(0);
	glBindVertexArray(0);

	GLuint prg;

	GLsizei loglen = 999;
	char logstr[1000];

	const char* vs_src =
		CODE(#version 330\n)
		CODE(
			layout(location = 0) in vec2 pos; // 0.0 - 1.0
			uniform float slice; // 0.0 - 255.0
			out vec3 fpos;       // 0.0-0.5/255 - 1.0+0.5/255
			void main()
			{
				float d0 = 0.0 - 0.5;
				float d1 = 255.0 + 0.5;
				fpos = vec3( mix(vec2(d0, d0), vec2(d1, d1), pos), slice );
				gl_Position = vec4(2.0*pos-vec2(1.0),0.0,1.0);
			}
		);

	const char* fs_src =
		CODE(#version 330\n)
		CODE(
			uniform uvec3 pal[256]; // 0 - 255
			uniform bool unpal;
			layout(location = 0) out vec4 lut;
			in vec3 fpos;
			void main()
			{
				if (unpal)
					lut = vec4(fpos / 255.0, 1.0);
				else
				{
					float diff = 100000000; // greater than max possible diff
					int idx = -1;

					// find closest color in palette
					for (int j = 0; j < 256; j++)
					{
						vec3 dd = fpos - vec3(pal[j]);
						dd *= dd;

						float d = max(max(fpos.r, fpos.g), fpos.b) - float(max(max(pal[j].r, pal[j].g), pal[j].b));
						d *= 16 * d; // mostly luminance
						d += 2 * dd.r + 4 * dd.g + 3 * dd.b; // bit of chrominance

						if (d < diff)
						{
							idx = j;
							diff = d;
						}
					}

					lut = vec4(vec3(pal[idx]) / 255.0, float(idx) / 255.0);
				}
			}
		);

	GLenum st[3] = { GL_VERTEX_SHADER, GL_FRAGMENT_SHADER };
	const char* src[3] = { vs_src, fs_src };
	prg = glCreateProgram();
	GLuint shader[3];

	for (int i = 0; i < 2; i++)
	{
		shader[i] = glCreateShader(st[i]);
		GLint len = (GLint)strlen(src[i]);
		glShaderSource(shader[i], 1, &(src[i]), &len);
		glCompileShader(shader[i]);

		loglen = 999;
		glGetShaderInfoLog(shader[i], loglen, &loglen, logstr);
		logstr[loglen] = 0;

		if (loglen)
			printf("%s", logstr);

		glAttachShader(prg, shader[i]);
	}

	glLinkProgram(prg);

	for (int i = 0; i < 2; i++)
		glDeleteShader(shader[i]);

	GLint slice_loc = glGetUniformLocation(prg,"slice");
	GLint pal_loc = glGetUniformLocation(prg, "pal");
	GLint unpal_loc = glGetUniformLocation(prg, "unpal");
	glUseProgram(prg);

	if (p)
	{
		GLuint uipal[768];
		for (int i = 0; i < 768; i++)
			uipal[i] = (GLuint)p[i];
		glUniform3uiv(pal_loc, 256, uipal);
		glUniform1i(unpal_loc, false);
	}
	else
		glUniform1i(unpal_loc, true);

	GLuint fbo;
	glGenFramebuffers(1, &fbo);
	glBindFramebuffer(GL_FRAMEBUFFER, fbo);

	glBindVertexArray(vao);

	glViewport(0, 0, 256, 256);
	for (int slice = 0; slice < 256; slice++)
	{
		glFramebufferTexture3D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_3D, pal_tex, 0, slice);
		glUniform1f(slice_loc, (float)slice);
		glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
	}

	glDeleteFramebuffers(1, &fbo);
	glDeleteVertexArrays(1, &vao);
	glDeleteBuffers(1, &vbo);
	glDeleteProgram(prg);


	//glFinish();
	uint64_t t1 = a3dGetTime();
	printf("palettized in %d us\n", (int)(t1 - t0));

	if (ipal)
	{
		glGetTextureImage(pal_tex, 0, GL_ALPHA, GL_UNSIGNED_BYTE, 1<<24, ipal);
		uint64_t t2 = a3dGetTime();
		printf("fetched ipal in %d us\n", (int)(t2 - t1));
	}
}


struct DirItem
{
	A3D_DirItem item;
	DirItem* next;
	char name[1];
};

void FreeDir(DirItem** dir)
{
	DirItem** i = dir;
	while (*i)
	{
		free(*i);
		i++;
	}
	free(dir);
}

int AllocDir(DirItem*** dir, DirItem** list = 0)
{
	if (!dir)
		return -1;

	struct X
	{
		struct Head
		{
			int num;
			DirItem* list;
		};

		static int cmp(const void* a, const void* b)
		{
			const DirItem* p = *(const DirItem**)a;
			const DirItem* q = *(const DirItem**)b;

			if (p->item == A3D_DIRECTORY && q->item == A3D_FILE)
				return -1;
			if (p->item == A3D_FILE && q->item == A3D_DIRECTORY)
				return 1;
			return strcmp(p->name, q->name);
		}


		static bool Scan(A3D_DirItem item, const char* name, void* cookie)
		{
			Head* h = (Head*)cookie;
			DirItem* i = (DirItem*)malloc(sizeof(DirItem) + strlen(name));

			i->item = item;
			i->next = h->list;
			strcpy(i->name, name);
			h->list = i;
			h->num++;

			return true;
		}
	};

	X::Head head = { 0,0 };
	a3dListDir(".", X::Scan, &head);

	if (list)
		*list = head.list;

	DirItem* itm = head.list;
	DirItem** arr = (DirItem**)malloc(sizeof(DirItem*)*(head.num+1));
	for (int i = 0; i < head.num; i++)
	{
		arr[i] = itm;
		itm = itm->next;
	}

	qsort(arr, head.num, sizeof(DirItem*), X::cmp);

	arr[head.num] = 0;
	*dir = arr;

	return head.num;
}

// TODO(PIPELINE-FIX): SpriteScan loads all files from assets/sprites/ directory as raw .xp sprites.
// When the asset pipeline supports pre-processed sprites (palette-normalized, multi-frame
// assembled), update scan filter and LoadSprite call to handle staged output format.
struct SpriteScanConfig
{
	const char* dirname;
	bool quiet_failures;
};

static bool SpriteScan(A3D_DirItem item, const char* name, void* cookie)
{
	if (!(item&A3D_FILE))
		return true;

	if (!IsSpriteXPFile(name))
		return true;

	SpriteScanConfig* config = (SpriteScanConfig*)cookie;
	if (!config || !config->dirname)
		return true;

	char buf[4096];
	snprintf(buf, 4095, "%s/%s", config->dirname, name);
	buf[4095] = 0;

	Sprite* s = 0;
	{
		// TODO(PIPELINE-FIX): LoadSprite loads raw .xp files directly. Pipeline may pre-process
		// sprites (palette normalization, frame assembly) before editor consumption.
		s = LoadSprite(/*world,*/ buf, name, 0, false, config->quiet_failures);
		if (s)
		{
			InitSpritePrefs(s);
		}
	}

	return true;
}

static void ScanEditorSpriteDirectory(bool quiet_failures)
{
	char sprite_dirname[1024+20];
	sprintf(sprite_dirname, "%sassets/sprites", base_path);
	SpriteScanConfig config = { sprite_dirname, quiet_failures };
	a3dListDir(sprite_dirname, SpriteScan, &config);
}

static void LoadEnemygenSpriteForEditor()
{
	if (g_startup_viewer_mode)
		return;

	char enemygen_path[1024+20];
	sprintf(enemygen_path, "%sassets/sprites/enemygen.xp", base_path);
	enemygen_sprite = LoadSprite(enemygen_path, "enemygen.xp", 0, false);
}

// [DEPENDENCY:BLENDER] Scan assets/meshes/ directory for .akm files exported from Blender via io_mesh_akm addon.
// WHY: The editor's mesh library is populated by scanning the assets/meshes/ directory at startup or on New().
// Each .akm file was exported from Blender using the io_mesh_akm addon. Mesh names are matched by
// filename (e.g., "Cube.akm") and duplicate names are skipped (mesh already loaded from scene file
// takes priority).
static bool MeshScan(A3D_DirItem item, const char* name, void* cookie)
{
	if (!(item&A3D_FILE))
		return true;

	if (g_fl3714_mesh_scan_active)
		g_fl3714_mesh_scan_files++;

	if (strstr(name, "laundry") || strstr(name, "brick") || strstr(name, "bridge"))
	{
		if (g_fl3714_mesh_scan_active)
			g_fl3714_mesh_scan_skipped++;
		return true;
	}

	char buf[4096];
	snprintf(buf, 4095, "%s/%s", (char*)cookie, name);
	buf[4095] = 0;

	Mesh* m = GetFirstMesh(world);
	while (m)
	{
		char mesh_name[256];
		GetMeshName(m,mesh_name,256);

		if (strcmp(name,mesh_name)==0)
			break;

		m=GetNextMesh(m);
	}

	if (!m)
	{
		uint64_t t0 = FL3714Now();
		// [DEPENDENCY:BLENDER] Mesh data format (.akm) defined by Blender export addon -- vertices, faces, colors.
		m = LoadMesh(world, buf, name);
		if (g_fl3714_mesh_scan_active)
			g_fl3714_mesh_scan_load_us += FL3714Now() - t0;
		if (m)
		{
			if (g_fl3714_mesh_scan_active)
				g_fl3714_mesh_scan_loaded++;
			MeshPrefs* mp = (MeshPrefs*)malloc(sizeof(MeshPrefs));
			memset(mp,0,sizeof(MeshPrefs));
			SetMeshCookie(m,mp);
		}
		else if (g_fl3714_mesh_scan_active)
		{
			g_fl3714_mesh_scan_failed++;
		}
	}
	else if (g_fl3714_mesh_scan_active)
	{
		g_fl3714_mesh_scan_existing++;
	}

	return true;
}

// WHY Perlin noise for default terrain generation:
// New() creates an empty map with procedurally generated default terrain.
// Perlin noise provides smooth, natural-looking height variation (hills/valleys).
// The function also supports loading height maps from image files (when user
// provides a path), converting grayscale pixel values to terrain elevation.
// After terrain generation, mesh library is rescanned to populate editor state.
void New()
{
	SetCurrentMapPath("");

	// free mesh prefs !!!
	Mesh* m = GetFirstMesh(world);
	while (m)
	{
		MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(m);
		free(mp);
		m = GetNextMesh(m);
	}

	URDO_Purge();
	EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
	DeleteTerrain(terrain);
	DeleteWorld(world);
	FreeMinimapMarkers();
	world = 0;
	terrain = 0;
	inst_list_dirty = true;

	terrain = CreateTerrain();
	world = CreateWorld();


	// [DEPENDENCY:BLENDER] New map creation loads mesh library from assets/meshes/ directory.
	// add meshes from library that aren't present in scene file
	char mesh_dirname[4096];
	sprintf(mesh_dirname,"%sassets/meshes",base_path);
	a3dListDir(mesh_dirname, MeshScan, mesh_dirname);

	RebuildWorld(world);

	active_mesh = GetFirstMesh(world);

	// init some planar terrain
	#if 0

	struct Perlin
	{
		Perlin()
		{
			SEED = 0;

			static const int data[] =
			{
				208,34,231,213,32,248,233,56,161,78,24,140,71,48,140,254,245,255,247,247,40,
				185,248,251,245,28,124,204,204,76,36,1,107,28,234,163,202,224,245,128,167,204,
				9,92,217,54,239,174,173,102,193,189,190,121,100,108,167,44,43,77,180,204,8,81,
				70,223,11,38,24,254,210,210,177,32,81,195,243,125,8,169,112,32,97,53,195,13,
				203,9,47,104,125,117,114,124,165,203,181,235,193,206,70,180,174,0,167,181,41,
				164,30,116,127,198,245,146,87,224,149,206,57,4,192,210,65,210,129,240,178,105,
				228,108,245,148,140,40,35,195,38,58,65,207,215,253,65,85,208,76,62,3,237,55,89,
				232,50,217,64,244,157,199,121,252,90,17,212,203,149,152,140,187,234,177,73,174,
				193,100,192,143,97,53,145,135,19,103,13,90,135,151,199,91,239,247,33,39,145,
				101,120,99,3,186,86,99,41,237,203,111,79,220,135,158,42,30,154,120,67,87,167,
				135,176,183,191,253,115,184,21,233,58,129,233,142,39,128,211,118,137,139,255,
				114,20,218,113,154,27,127,246,250,1,8,198,250,209,92,222,173,21,88,102,219
			};

			hash = data;
		}

		int SEED;
		const int* hash;

		int noise2(int x, int y)
		{
			int tmp = hash[(y + SEED) % 256];
			return hash[(tmp + x) % 256];
		}

		float lin_inter(float x, float y, float s)
		{
			return x + s * (y - x);
		}

		float smooth_inter(float x, float y, float s)
		{
			return lin_inter(x, y, s * s * (3 - 2 * s));
		}

		float noise2d(float x, float y)
		{
			int x_int = x;
			int y_int = y;
			float x_frac = x - x_int;
			float y_frac = y - y_int;
			int s = noise2(x_int, y_int);
			int t = noise2(x_int + 1, y_int);
			int u = noise2(x_int, y_int + 1);
			int v = noise2(x_int + 1, y_int + 1);
			float low = smooth_inter(s, t, x_frac);
			float high = smooth_inter(u, v, x_frac);
			return smooth_inter(low, high, y_frac);
		}

		float perlin2d(float x, float y, float freq, int depth)
		{
			float xa = x * freq;
			float ya = y * freq;
			float amp = 1.0;
			float fin = 0;
			float div = 0.0;

			int i;
			for (i = 0; i < depth; i++)
			{
				div += 256 * amp;
				fin += noise2d(xa, ya) * amp;
				amp /= 2;
				xa *= 2;
				ya *= 2;
			}

			return fin / div;
		}
	};

	Perlin perlin;

	const int num1 = 256;
	const int num2 = num1*num1;

	uint32_t* rnd = (uint32_t*)malloc(sizeof(uint32_t)*num2);
	int n = num2;
	for (int i = 0; i < num2; i++)
		rnd[i] = i;

	for (int i = 0; i < num2; i++)
	{
		int r = (fast_rand() + fast_rand()*(FAST_RAND_MAX+1)) % n;

		uint32_t uv = rnd[r];
		rnd[r] = rnd[--n];
		uint32_t u = uv % num1;
		uint32_t v = uv / num1;
		AddTerrainPatch(terrain, u, v, (int)(300*perlin.perlin2d(u,v,0.1,10)));
		EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
	}

	free(rnd);

	pos_x = num1 * VISUAL_CELLS / 2;
	pos_y = num1 * VISUAL_CELLS / 2;
	pos_z = 0x0;
	#endif

	struct MAP
	{
		static void cb(void* cookie, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf)
		{
			if (f != A3D_RGB8 && f != A3D_LUMINANCE8 && f != A3D_RGBA8)
				return;
			int patches_x = (w-1) / 4;
			int patches_y = (h-1) / 4;

			uint8_t* rgb = (uint8_t*)data;

			int max_n = 0;

			// skip 1 patch at each edge (safe normals)
			for (int py = 1; py < patches_y-1; py++)
			{
				for (int px = 1; px < patches_x-1; px++)
				{
					Patch* p = AddTerrainPatch(terrain, px, py, 0);
					EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
					uint16_t* map = GetTerrainHeightMap(p);
					uint16_t* vmap = GetTerrainVisualMap(p);
					const uint8_t* pix;
					if (f == A3D_LUMINANCE8)
						pix = (const uint8_t*)data + HEIGHT_CELLS * px + (HEIGHT_CELLS * py)*w;
					else
					if (f == A3D_RGB8)
						pix = (const uint8_t*)data + 3 * (HEIGHT_CELLS * px + (HEIGHT_CELLS * py)*w);
					else
					if (f == A3D_RGBA8)
						pix = (const uint8_t*)data + 4 * (HEIGHT_CELLS * px + (HEIGHT_CELLS * py)*w);

					for (int vy = 0; vy <= HEIGHT_CELLS; vy++)
					{
						for (int vx = 0; vx <= HEIGHT_CELLS; vx++)
						{
							if (f == A3D_RGB8)
								map[vx + vy * (HEIGHT_CELLS + 1)] = 4 * pix[3*(vx+vy*w)+2]; // B
							else
							if (f == A3D_LUMINANCE8)
								map[vx + vy * (HEIGHT_CELLS + 1)] = 4 * pix[vx + vy * w]; // L
							else
							if (f == A3D_RGBA8)
							{
								map[vx + vy * (HEIGHT_CELLS + 1)] = 8 * pix[4*(vx + vy * w)+0]; // R?
							}
						}
					}

					UpdateTerrainHeightMap(p);

					if (f == A3D_RGBA8)
					{
						for (int vy = 0; vy < VISUAL_CELLS; vy++)
						{
							for (int vx = 0; vx < VISUAL_CELLS; vx++)
							{
								uint16_t* m = vmap + (vx + vy * VISUAL_CELLS);

								int n = 0;
								n += std::abs(pix[4*((vx/2+1) + (vy/2+0) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2+0) + (vy/2+1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2-1) + (vy/2+0) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2+0) + (vy/2-1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2-1) + (vy/2-1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2+1) + (vy/2+1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2-1) + (vy/2+1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);
								n += std::abs(pix[4*((vx/2+1) + (vy/2-1) * w)+0] - pix[4*(vx/2 + vy/2 * w)+0]);

								max_n = std::max(max_n,n);

								if (n<20)
								{
									int w = 0;
									w += pix[4*((vx/2+0) + (vy/2+0) * w)+2]; // B
									w += pix[4*((vx/2+1) + (vy/2+0) * w)+2]; // B
									w += pix[4*((vx/2+0) + (vy/2+1) * w)+2]; // B
									w += pix[4*((vx/2+1) + (vy/2+1) * w)+2]; // B

									if (w<=100)
									{
										// FLAT GREEN
										*m = 1;
									}
									else
									{
										// FLAT SAND
										*m = 2;

										// greenish soil
										// soil
										// sand
										// wet sand
										// water
									}
								}
								else
								{
									// ROCK
									*m = 4;
								}
							}
						}

						UpdateTerrainVisualMap(p);
					}
				}
			}

			printf("MAX_N=%d\n",max_n);
		};
	};

	char newmap_path[1024+20];
	sprintf(newmap_path, "%sassets/maps/new.png", base_path);
	a3dLoadImage(newmap_path, 0, MAP::cb);
}

// ============================================================================
// CONFIRMATION CALLBACKS [FL-3830]
// ============================================================================
static void ConfirmNew(void*) { New(); }
static void ConfirmDeleteAllMeshInsts(void*) { DeleteAllMeshInsts(); }
static void ConfirmDeleteAllEnemyGens(void*) { DeleteAllEnemyGens(); }
static void ConfirmClearMatElev(void*) { ClearMatElev(); }
static void ConfirmCastShadows(void*) { UpdateTerrainDark(terrain, world, global_lt, true); }
static void ConfirmApplyAutoMatElev(void* cookie)
{ StartDeferredOp("Auto MAT-elev", AutoMatElev::Apply, cookie, sizeof(AutoMatElev)); }
static void ConfirmApplyAutoTexture(void* cookie)
{ StartDeferredOp("Auto Texture", AutoTexture::Apply, cookie, sizeof(AutoTexture)); }
struct BakeConfirmParams { bool height, material, vertex_colors, overwrite_height, overwrite_material, solid_only; float ray_top; uint8_t mat_id; };
static void ConfirmBakeMeshes(void* cookie)
{ BakeConfirmParams* p = (BakeConfirmParams*)cookie; BakeMeshesToTerrain(p->height, p->material, p->vertex_colors, p->overwrite_height, p->overwrite_material, p->solid_only, p->ray_top, p->mat_id); }

void TranslateMap(int delta_z, bool water_limit)
{
	struct Translate
	{
		static void QueryPatch(Patch* p, int x, int y, int vf, void* cookie)
		{
			Translate* t = (Translate*)cookie;
			uint16_t* map = GetTerrainHeightMap(p);
			int num = (HEIGHT_CELLS + 1)*(HEIGHT_CELLS + 1);

			if (!t->water_limit)
			{
				if (t->delta_z > 0)
				{
					for (int i = 0; i < num; i++)
						map[i] = std::min(0xFFFF, map[i] + t->delta_z);
				}
				else
				{
					for (int i = 0; i < num; i++)
						map[i] = std::max(0, map[i] + t->delta_z);
				}
			}
			else
			{
				if (t->delta_z > 0)
				{
					for (int i = 0; i < num; i++)
						if (map[i] >= t->water)
							map[i] = std::min(0xFFFF, map[i] + t->delta_z);
				}
				else
				{
					for (int i = 0; i < num; i++)
						if (map[i] < t->water)
							map[i] = std::max(0, map[i] + t->delta_z);
				}
			}

			UpdateTerrainHeightMap(p);
		}

		static void QuerySprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie)
		{
			assert(0);
		}

		static void QueryMesh(Inst* i, Mesh* m, double* tm, void* cookie)
		{
			Translate* t = (Translate*)cookie;
			tm[14] += t->delta_z;
		}

		int delta_z;
		int water;
		bool water_limit;
	};

	Translate t;
	t.delta_z = delta_z;
	t.water = probe_z;
	t.water_limit = water_limit;

	QueryTerrain(terrain, 0, 0, 0xAA, Translate::QueryPatch, &t);

	QueryWorldCB cb = { Translate::QueryMesh, Translate::QuerySprite };
	QueryWorld(world, 0, 0, &cb, &t);

	RebuildWorld(world, true);
}

// WHY terrain+materials+world sequential read order:
// Load() reads .a3d map files in strict order: terrain patches first, then
// material definitions, then world scene graph (meshes, sprites, instances),
// then enemy generators. This order matches Save() write order and allows
// streaming load without seeking. After loading terrain data, mesh geometry
// is reloaded from .akm files (Blender-exported) to sync with instance data.
bool Load(const char* path)
{
	uint64_t load_t0 = FL3714Now();
	printf("[EDITOR] [FL-3714] Load begin path=%s\n", path ? path : "(null)");
	fflush(stdout);

	FILE* f = fopen(path,"rb");
	if (!f)
	{
		printf("[EDITOR] [FL-3714] Load fopen_failed path=%s\n", path ? path : "(null)");
		fflush(stdout);
		return false;
	}

	// close all terms
	uint64_t stage_t0 = FL3714Now();
	TermCloseAll();
	FL3714Stage("[EDITOR]", "term_close_all", stage_t0);

	// free mesh prefs !!!
	stage_t0 = FL3714Now();
	Mesh* m = GetFirstMesh(world);
	while (m)
	{
		MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(m);
		free(mp);
		m = GetNextMesh(m);
	}
	FL3714Stage("[EDITOR]", "free_mesh_prefs", stage_t0);

	stage_t0 = FL3714Now();
	URDO_Purge();
	EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
	DeleteTerrain(terrain);
	DeleteWorld(world);
	world = 0;
	terrain = 0;
	FL3714Stage("[EDITOR]", "delete_old_world", stage_t0);

	bool loaded_from_file = false;
	if (f)
	{
		stage_t0 = FL3714Now();
		terrain = LoadTerrain(f);
		loaded_from_file = terrain != 0;
		printf("[EDITOR] [FL-3714] stage=load_terrain elapsed_ms=%.3f patches=%d loaded=%d\n",
			(double)(FL3714Now() - stage_t0) / 1000.0,
			terrain ? GetTerrainPatches(terrain) : 0,
			loaded_from_file ? 1 : 0);
		fflush(stdout);

		if (terrain)
		{
			stage_t0 = FL3714Now();
			for (int i=0; i<256; i++)
			{
				if ( fread(mat[i].shade,1,sizeof(MatCell)*4*16,f) != sizeof(MatCell)*4*16 )
					break;
				// FL-4131 Phase 2: legacy CP437-only material load, glyph_plane stays NULL.
				material_glyph_plane_free(mat[i].glyph_plane);
				mat[i].glyph_plane = NULL;
				/*
				if (i == 1 || i == 3)
					memcpy(mat[i].shade, mat[0].shade, sizeof(MatCell) * 4 * 16);
				*/
				mat[i].Update();
			}
			FL3714Stage("[EDITOR]", "load_materials", stage_t0);
			if (!LoadMaterialGlyphSidecarForMap(path, "[EDITOR]"))
			{
				// FL-4131 fail-closed: the sidecar/manifest hash check
				// rejected this map. terrain was already loaded and the
				// OLD world+terrain were deleted at load entry, so
				// active_mesh / selected_inst / drag_inst now dangle
				// into freed memory. If we just `return false`, the
				// next render frame dereferences those pointers and
				// the process crashes silently (~1s later, before any
				// further CDP command can be processed).
				// Restore an empty editor world so the render loop is
				// safe and the caller's error response reaches the
				// client.
				fclose(f);
				DeleteTerrain(terrain);
				terrain = CreateTerrain();
				if (world) DeleteWorld(world);
				world = CreateWorld();
				active_mesh = NULL;
				selected_inst = 0;
				drag_inst = 0;
				inst_list_dirty = true;
				printf("[EDITOR] [FL-4131] Load aborted; editor restored to empty world\n");
				fflush(stdout);
				return false;
			}

			stage_t0 = FL3714Now();
			world = LoadWorldForEditor(f);
			printf("[EDITOR] [FL-3714] stage=load_world elapsed_ms=%.3f world=%d\n",
				(double)(FL3714Now() - stage_t0) / 1000.0,
				world ? 1 : 0);
			fflush(stdout);
			if (world)
			{
				// reload meshes too
				Mesh* m = GetFirstMesh(world);

				int scene_meshes = 0;
				int scene_mesh_loaded = 0;
				int scene_mesh_failed = 0;
				uint64_t scene_mesh_us = 0;
				while (m)
				{
					scene_meshes++;
					char mesh_name[256];
					GetMeshName(m,mesh_name,256);
					char obj_path[4096];
					ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
					// [DEPENDENCY:BLENDER] UpdateMesh reloads mesh geometry from .akm file (Blender-exported geometry).
					uint64_t mesh_t0 = FL3714Now();
					if (!UpdateMesh(m,obj_path))
					{
						scene_mesh_failed++;
						printf("[Mesh] Failed to load %s from %s\n", mesh_name, obj_path);
					}
					else if (strstr(mesh_name, "skull") || strstr(mesh_name, "Skull"))
					{
						scene_mesh_loaded++;
						printf("[Mesh] Loaded %s faces=%d\n", mesh_name, GetMeshFaces(m));
					}
					else
					{
						scene_mesh_loaded++;
					}
					scene_mesh_us += FL3714Now() - mesh_t0;

					MeshPrefs* mp = (MeshPrefs*)malloc(sizeof(MeshPrefs));
					memset(mp,0,sizeof(MeshPrefs));
					SetMeshCookie(m,mp);

					m = GetNextMesh(m);
				}
				printf("[EDITOR] [FL-3714] stage=reload_scene_meshes elapsed_ms=%.3f meshes=%d loaded=%d failed=%d\n",
					(double)scene_mesh_us / 1000.0,
					scene_meshes,
					scene_mesh_loaded,
					scene_mesh_failed);
				fflush(stdout);
			}

			stage_t0 = FL3714Now();
			LoadEnemyGens(f);
			FL3714Stage("[EDITOR]", "load_enemy_gens", stage_t0);
			if (!g_enable_enemies)
				FreeEnemyGens();
			stage_t0 = FL3714Now();
			LoadMinimapMarkers(f);
			FL3714Stage("[EDITOR]", "load_minimap_markers", stage_t0);
		}

		fclose(f);
		FL3714Mark("[EDITOR]", "file_closed");
	}

	// Create terrain if not loaded from file
	if (!terrain)
		terrain = CreateTerrain();

	if (!world)
		world = CreateWorld();

	// [DEPENDENCY:BLENDER] Load map operation loads mesh library from assets/meshes/ directory.
	// add meshes from library that aren't present in scene file
	char mesh_dirname[4096];
	sprintf(mesh_dirname,"%sassets/meshes",base_path);
	g_fl3714_mesh_scan_active = true;
	g_fl3714_mesh_scan_files = 0;
	g_fl3714_mesh_scan_skipped = 0;
	g_fl3714_mesh_scan_existing = 0;
	g_fl3714_mesh_scan_loaded = 0;
	g_fl3714_mesh_scan_failed = 0;
	g_fl3714_mesh_scan_load_us = 0;
	stage_t0 = FL3714Now();
	a3dListDir(mesh_dirname, MeshScan, mesh_dirname);
	printf("[EDITOR] [FL-3714] stage=scan_mesh_library elapsed_ms=%.3f files=%d skipped=%d existing=%d loaded=%d failed=%d mesh_load_ms=%.3f\n",
		(double)(FL3714Now() - stage_t0) / 1000.0,
		g_fl3714_mesh_scan_files,
		g_fl3714_mesh_scan_skipped,
		g_fl3714_mesh_scan_existing,
		g_fl3714_mesh_scan_loaded,
		g_fl3714_mesh_scan_failed,
		(double)g_fl3714_mesh_scan_load_us / 1000.0);
	fflush(stdout);
	g_fl3714_mesh_scan_active = false;

	// this is the only case when instances has no valid bboxes yet
	// as meshes weren't present during their creation
	// now meshes are loaded ...
	// so we need to update instance boxes with (,true)
	stage_t0 = FL3714Now();
	RebuildWorld(world, true);
	FL3714Stage("[EDITOR]", "rebuild_world_after_load", stage_t0);

	active_mesh = GetFirstMesh(world);


	//TranslateMap(-100, false);
	printf("[EDITOR] [FL-3714] Load end elapsed_ms=%.3f active_mesh=%d loaded_from_file=%d\n",
		(double)(FL3714Now() - load_t0) / 1000.0,
		active_mesh ? 1 : 0,
		loaded_from_file ? 1 : 0);
	fflush(stdout);
	return loaded_from_file;
}

static void ResetCameraForLoadedMap(const char* prefix)
{
	if (!terrain)
		return;

	float spawn[3] = {0.0f, 0.0f, 0.0f};
	float yaw = rot_yaw;
	if (world && WorldGetPlayerStart(world, spawn, &yaw))
	{
		pos_x = spawn[0];
		pos_y = spawn[1];
		pos_z = spawn[2];
		rot_yaw = yaw;
		probe_z = pos_z > 0x100 ? (int)pos_z - 0x100 : 0;
		if (prefix)
			printf("%s Camera repositioned to player-start: pos=(%.1f,%.1f,%.1f) probe_z=%d\n",
				prefix, pos_x, pos_y, pos_z, probe_z);

		// [FL-3838] Override camera for automated frame capture
		const char* cam_override = getenv("ASCIICKER_CAMERA_TOPDOWN");
		if (cam_override && *cam_override)
		{
			pos_z = 1300;
			rot_pitch = 90;
			rot_yaw = 0;
			printf("%s Camera overridden to top-down: pos=(%.1f,%.1f,%.1f) pitch=%.0f\n",
				prefix, pos_x, pos_y, pos_z, rot_pitch);
		}
		return;
	}

	Patch* p0 = GetTerrainPatch(terrain, 0, 0);
	if (!p0)
		return;

	uint16_t* hmap = GetTerrainHeightMap(p0);
	int h = hmap[(HEIGHT_CELLS/2) * (HEIGHT_CELLS+1) + HEIGHT_CELLS/2];
	pos_z = (float)h;
	probe_z = h > 0x100 ? h - 0x100 : 0;
	if (prefix)
		printf("%s Camera repositioned: pos_z=%d probe_z=%d\n", prefix, h, probe_z);
}

static bool LoadMapForSession(const char* path, const char* prefix)
{
	char resolved_path[1024];
	if (!path || !path[0])
	{
		snprintf(resolved_path, sizeof(resolved_path), "%sassets/a3d/game_map_y8.a3d", base_path);
		path = resolved_path;
	}

	if (prefix)
	{
		printf("%s Loading map: %s\n", prefix, path);
		fflush(stdout);
	}
	if (!Load(path))
	{
		if (prefix)
		{
			printf("%s Error: Failed to load map: %s\n", prefix, path);
			fflush(stdout);
		}
		return false;
	}
	ResetCameraForLoadedMap(prefix);
	SetCurrentMapPath(path);
	ResetPaletteOwnerThemeState();
	InvalidateAllMaterialRowPeakCaches();
	inst_list_dirty = true;
	selected_inst = 0;
	drag_inst = 0;
	return true;
}





// ============================================================================
// MATRIX VIEW IMPLEMENTATION
// ============================================================================

struct JsonContext {
    bool first;
    int count;
};

void json_mesh_cb(Inst* i, Mesh* m, double tm[16], void* cookie) {
    JsonContext* ctx = (JsonContext*)cookie;
    if (!ctx->first) printf(",\n");
    ctx->first = false;
    ctx->count++;

    // Extract position
    float x = (float)tm[12];
    float y = (float)tm[13];
    float z = (float)tm[14];

    // Extract scale (approximate from diagonal)
    float sx = (float)sqrt(tm[0]*tm[0] + tm[1]*tm[1] + tm[2]*tm[2]);
    float sy = (float)sqrt(tm[4]*tm[4] + tm[5]*tm[5] + tm[6]*tm[6]);
    float sz = (float)sqrt(tm[8]*tm[8] + tm[9]*tm[9] + tm[10]*tm[10]);

    // Cleanup mesh name (remove path)
    char mesh_name[256];
    GetMeshName(m, mesh_name, 256);

    // JSON Object
    printf("    {\n");
    printf("      \"id\": \"%p\",\n", i);
    printf("      \"name\": \"mesh\",\n");
    printf("      \"asset\": \"%s\",\n", mesh_name);
    printf("      \"pos\": [%.2f, %.2f, %.2f],\n", x, y, z);
    printf("      \"rot\": [0.0, 0.0, 0.0],\n"); // TODO: Decompose rotation
    printf("      \"scale\": [%.2f, %.2f, %.2f]\n", sx, sy, sz);
    printf("    }");
}

void json_sprite_cb(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie) {
    // TODO: Implement sprite dumping if needed
}

void DumpWorldJSON() {
    printf("[MATRIX_START]\n");
    printf("{\n");
    printf("  \"schema_version\": \"matrix-v1\",\n");

    // Pass 1: Count Meshes (Optional, but good for header)
    // Actually we can just stream the array.

    printf("  \"meshes\": [\n");

    JsonContext ctx = { true, 0 };
    QueryWorldCB cb = { json_mesh_cb, json_sprite_cb };

    // Query all objects
    QueryWorld(world, 0, 0, &cb, &ctx);

    printf("\n  ],\n");
    printf("  \"mesh_count\": %d,\n", ctx.count);
    printf("  \"sprites\": [],\n");
    printf("  \"sprite_count\": 0\n");
    printf("}\n");
    printf("[MATRIX_END]\n");
    fflush(stdout);
}

int Base64Encode(unsigned char* data, int len, char* base64)
{
	static const char chr[] =
		"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
		"abcdefghijklmnopqrstuvwxyz"
		"0123456789+/=";

	int chunks = len / 3, i = 0;
	for (; i < chunks; i++)
	{
		int s = 3 * i;
		int d = 4 * i;

		unsigned char
			a = data[s + 0],
			b = data[s + 1],
			c = data[s + 2];

		base64[d + 0] = chr[a >> 2];
		base64[d + 1] = chr[((a & 0x3) << 4) | (b >> 4)];
		base64[d + 2] = chr[((b & 0xF) << 2) | (c >> 6)];
		base64[d + 3] = chr[c & 0x3F];
	}

	int s = 3 * i;
	if (s<len)
	{
		int d = 4 * i;
		unsigned char a = data[s + 0];
		if (s + 1 >= len)
		{
			base64[d + 0] = chr[a >> 2];
			base64[d + 1] = chr[(a & 0x3) << 4];
			base64[d + 2] = chr[64];
			base64[d + 3] = chr[64];
		}
		else
		if (s + 2 >= len)
		{
			unsigned char b = data[s + 1];
			base64[d + 0] = chr[a >> 2];
			base64[d + 1] = chr[((a & 0x3) << 4) | (b >> 4)];
			base64[d + 2] = chr[(b & 0xF) << 2];
			base64[d + 3] = chr[64];
		}
		return d + 4;
	}

	return 4 * i;
}

// WHY MCP command protocol:
// ProcessMCPCommand enables scripted testing and external tool integration
// via text commands (QUIT, ECHO, LOAD_MAP, SAVE, LIST_MESHES, etc.).
// Three transport modes feed into this function:
//   1. --mcp: stdin/stdout text protocol (one command per render frame)
//   2. --cdp PORT: TCP JSON server on localhost (FL-3851, request/response with id correlation)
//   3. --batch: synchronous stdin processing (data queries only, no render-loop commands)
// This allows automated test scripts, capture_proof.py, and external tools to
// drive the editor without GUI interaction.
static bool g_batch_exit_after_clean_capture = false;
void ProcessMCPCommand(char* line) {
    printf("[MCP] Received command: %s", line);
    fflush(stdout);
    char cmd[256];
    if (sscanf(line, "%s", cmd) != 1) return;

    if (strcmp(cmd, "QUIT") == 0) {
        exit(0);
    }
    else if (strcmp(cmd, "ECHO") == 0) {
        // Skip "ECHO "
        char* msg = line + 5;
        // Trim newline
        char* nl = strchr(msg, '\n');
        if (nl) *nl = 0;
        printf("%s\n", msg);
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_ASCIIID_EXTENDED_MATERIAL_PROOF") == 0) {
        if (!RunFl4131AsciiidExtendedMaterialProof())
            printf("[MCP] FL4131_ASCIIID_EXTENDED_MATERIAL_PROOF failed\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_APPLY_EXTENDED_PRESET") == 0) {
        int material_id = -1;
        int preset_index = -1;
        if (sscanf(line, "%*s %d %d", &material_id, &preset_index) != 2) {
            printf("[MCP] Error: Usage: FL4131_APPLY_EXTENDED_PRESET <material_id> <preset_index>\n");
            fflush(stdout);
            return;
        }
        GlyphId first_missing = GLYPH_ID_NONE;
        if (!ApplyAsciiidExtendedPresetToMaterial(material_id, preset_index, &first_missing)) {
            printf("[MCP] Error: FL4131 preset apply failed material=%d preset=%d first_missing=%u\n",
                   material_id, preset_index, (unsigned)first_missing);
            fflush(stdout);
            return;
        }
        const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
        printf("[MCP] FL4131 preset applied material=%d preset_index=%d family=%s name=%s count=%d\n",
               material_id, preset_index, preset.material, preset.name, preset.count);
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_DUMP_EXTENDED_PRESET_TOOLTIP") == 0) {
        int preset_index = -1;
        if (sscanf(line, "%*s %d", &preset_index) != 1 || preset_index < 0 || preset_index >= (int)(sizeof(kAsciiidExtendedGlyphPresets) / sizeof(kAsciiidExtendedGlyphPresets[0]))) {
            printf("[MCP] Error: Usage: FL4131_DUMP_EXTENDED_PRESET_TOOLTIP <preset_index>\n");
            fflush(stdout);
            return;
        }
        GlyphId first_missing = GLYPH_ID_NONE;
        const AsciiidExtendedGlyphPreset& preset = kAsciiidExtendedGlyphPresets[preset_index];
        const bool admitted = AsciiidExtendedPresetIsAdmitted(preset, &first_missing);
        char tooltip[512];
        FormatAsciiidExtendedPresetTooltip(preset, admitted, first_missing, tooltip, sizeof(tooltip));
        for (char* p = tooltip; *p; p++) {
            if (*p == '\n')
                *p = '|';
        }
        printf("[MCP] FL4131_PRESET_TOOLTIP index=%d admitted=%d text=%s\n",
               preset_index,
               admitted ? 1 : 0,
               tooltip);
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_DUMP_MATERIAL_GLYPHS") == 0) {
        int material_id = -1;
        if (sscanf(line, "%*s %d", &material_id) != 1 || material_id < 0 || material_id >= kPaletteCount) {
            printf("[MCP] Error: Usage: FL4131_DUMP_MATERIAL_GLYPHS <material_id>\n");
            fflush(stdout);
            return;
        }
        printf("[MCP] FL4131_MATERIAL_GLYPHS ");
        PrintMaterialGlyphIdsJson(material_id);
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_DUMP_MATERIAL_PREVIEW_RESOLVE") == 0) {
        int material_id = -1;
        if (sscanf(line, "%*s %d", &material_id) != 1 || material_id < 0 || material_id >= kPaletteCount) {
            printf("[MCP] Error: Usage: FL4131_DUMP_MATERIAL_PREVIEW_RESOLVE <material_id>\n");
            fflush(stdout);
            return;
        }
        printf("[MCP] FL4131_MATERIAL_PREVIEW_RESOLVE ");
        PrintMaterialPreviewResolveJson(material_id);
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_LIST_EXTENDED_PRESETS") == 0) {
        const int preset_count = kAsciiidExtendedGlyphPresetCount;
        printf("[MCP] FL4131_PRESETS_START count=%d\n", preset_count);
        for (int i = 0; i < preset_count; i++) {
            GlyphId first_missing = GLYPH_ID_NONE;
            const bool admitted = AsciiidExtendedPresetIsAdmitted(kAsciiidExtendedGlyphPresets[i], &first_missing);
            printf("[MCP] FL4131_PRESET index=%d family=%s name=%s admitted=%d first_missing=%u count=%d\n",
                   i,
                   kAsciiidExtendedGlyphPresets[i].material,
                   kAsciiidExtendedGlyphPresets[i].name,
                   admitted ? 1 : 0,
                   (unsigned)first_missing,
                   kAsciiidExtendedGlyphPresets[i].count);
        }
        printf("[MCP] FL4131_PRESETS_END\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_DUMP_PRESET_UI_RECTS") == 0) {
        printf("[MCP] FL4131_PRESET_UI_RECTS_START count=%d sidebar_tab=%d extended_frames=%d\n",
               kAsciiidExtendedGlyphPresetCount,
               g_asciiid_sidebar_tab_debug,
               g_asciiid_extended_preset_ui_frame_count);
        for (int i = 0; i < kAsciiidExtendedGlyphPresetCount; i++) {
            const AsciiidPresetUiRect& r = g_asciiid_preset_ui_rects[i];
            printf("[MCP] FL4131_PRESET_UI_RECT index=%d family=%s name=%s valid=%d x0=%d y0=%d x1=%d y1=%d\n",
                   i,
                   kAsciiidExtendedGlyphPresets[i].material,
                   kAsciiidExtendedGlyphPresets[i].name,
                   r.valid ? 1 : 0,
                   r.x0,
                   r.y0,
                   r.x1,
                   r.y1);
        }
        printf("[MCP] FL4131_PRESET_UI_RECTS_END\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "FL4131_DUMP_EXTENDED_PICKER_UI_RECTS") == 0) {
        const AsciiidPresetUiRect& glyph = g_asciiid_extended_picker_first_glyph_rect;
        const AsciiidPresetUiRect& fill = g_asciiid_extended_picker_fill_rect;
        printf("[MCP] FL4131_EXTENDED_PICKER_UI_RECTS sidebar_tab=%d extended_frames=%d first_glyph_id=%u first_valid=%d first_x0=%d first_y0=%d first_x1=%d first_y1=%d fill_valid=%d fill_x0=%d fill_y0=%d fill_x1=%d fill_y1=%d\n",
               g_asciiid_sidebar_tab_debug,
               g_asciiid_extended_preset_ui_frame_count,
               (unsigned)g_asciiid_extended_picker_first_glyph_id,
               glyph.valid ? 1 : 0,
               glyph.x0,
               glyph.y0,
               glyph.x1,
               glyph.y1,
               fill.valid ? 1 : 0,
               fill.x0,
               fill.y0,
               fill.x1,
               fill.y1);
        fflush(stdout);
    }
    else if (strcmp(cmd, "RUN_MOUSE_CLICK_PROBE") == 0) {
        int x = -1;
        int y = -1;
        if (sscanf(line, "%*s %d %d", &x, &y) != 2) {
            printf("[MCP] Error: RUN_MOUSE_CLICK_PROBE requires x y\n");
            fflush(stdout);
            return;
        }
        if (!FL3714DragProbeInputOk(x, y, x, y)) {
            printf("[MCP] Error: RUN_MOUSE_CLICK_PROBE coordinates out of bounds max_abs=%d\n", kFL3714DragProbeMaxCoord);
            fflush(stdout);
            return;
        }
        my_mouse(wnd_head, x, y, (MouseInfo)(MouseInfo::MOVE | MouseInfo::INSIDE));
        my_mouse(wnd_head, x, y, (MouseInfo)(MouseInfo::LEFT_DN | MouseInfo::INSIDE));
        my_mouse(wnd_head, x, y, (MouseInfo)(MouseInfo::LEFT_UP | MouseInfo::INSIDE));
        printf("[MCP] RUN_MOUSE_CLICK_PROBE queued x=%d y=%d queue_after=%d\n", x, y, mouse_queue_len);
        fflush(stdout);
    }
    else if (strcmp(cmd, "SATELLITE_VIEW") == 0) {
        // Open satellite view in browser + set asciiid camera to top-down.
        if (!g_osm_proj.valid) {
            printf("[MCP] Error: no OSM projection loaded (terrain_metadata.json missing)\n");
        } else {
            double cam_x = 0, cam_y = 0;
            if (sscanf(line, "%*s %lf %lf", &cam_x, &cam_y) < 2) {
                cam_x = pos_x; cam_y = pos_y;
            }
            // Set asciiid camera to top-down view matching satellite zoom
            pos_x = (float)cam_x; pos_y = (float)cam_y;
            pos_z = 1300; rot_pitch = 90; rot_yaw = 12;
            font_size = 0.431f; ClampInteractiveFontSize();
            char script_cmd[1024];
            snprintf(script_cmd, sizeof(script_cmd),
                "python3 \"%sscripts/pipeline/satellite_view.py\" --world %.1f %.1f --bounds &",
                base_path, cam_x, cam_y);
            system(script_cmd);
            double lat, lon;
            world_to_latlon(cam_x, cam_y, &lat, &lon);
            printf("[MCP] SATELLITE_VIEW: %.7f, %.7f (world %.1f, %.1f) camera set to top-down\n", lat, lon, cam_x, cam_y);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "RENDER") == 0) {
        int w = 160;
        int h = 90;
        AnsiCell* buf = (AnsiCell*)malloc(sizeof(AnsiCell) * w * h);
        if (!buf) {
            printf("[RENDER_ERROR] Out of memory\n");
            fflush(stdout);
            return;
        }

        Renderer* r = CreateRenderer(0);
        if (!r) {
            printf("[RENDER_ERROR] Could not create renderer\n");
            free(buf);
            fflush(stdout);
            return;
        }

        float pos[3] = { pos_x, pos_y, pos_z };
        float lt[4] = { 1, 1, 1, 1 }; // default light
        int shift[2] = { 0, 0 };

        float sw_zoom = 1.0f;
        float sw_yaw = rot_yaw * (M_PI / 180.0f);

        // [ROOT-15 FIX] Use game's actual water level (55), not 0x8000.
        // 0x8000 is the legacy terrain-encoding scale — using it here made
        // MCP RENDER output show terrain/water boundaries differently from
        // the real game (which uses water=55 in mainmenu.cpp:1393).
        Render(r, 0, terrain, world, 55,
               sw_zoom, sw_yaw, pos, lt,
               w, h, buf, 0, shift, true);

        int data_len = w * h * 3; // gl, fg, bk
        unsigned char* compact = (unsigned char*)malloc(data_len);
        for(int i=0; i<w*h; i++) {
            compact[i*3+0] = buf[i].gl;
            compact[i*3+1] = buf[i].fg;
            compact[i*3+2] = buf[i].bk;
        }

        int b64_len = (data_len + 2) / 3 * 4;
        char* b64 = (char*)malloc(b64_len + 1);
        int final_len = Base64Encode(compact, data_len, b64);
        b64[final_len] = 0;

        printf("[RENDER_DATA_START] w=%d h=%d format=b64\n", w, h);
        // Print in chunks to avoid stdout buffer issues if any
        for(int i=0; i<final_len; i+=1024) {
            int len = final_len - i;
            if (len > 1024) len = 1024;
            fwrite(b64 + i, 1, len, stdout);
            if (i % 4096 == 0) fflush(stdout);
        }
        printf("\n[RENDER_DATA_END]\n");
        fflush(stdout);

        DeleteRenderer(r);
        free(buf);
        free(compact);
        free(b64);
    }
    else if (strcmp(cmd, "DUMP_MATRIX") == 0) {
        DumpWorldJSON();
    }
    else if (strcmp(cmd, "PLACE_MESH") == 0) {
        char mesh_file[512];
        float x, y, z, scale;
        if (sscanf(line, "%*s %511s %f %f %f %f", mesh_file, &x, &y, &z, &scale) == 5) {

            // Reject path traversal in mesh name
            if (strstr(mesh_file, "..") != NULL) {
                printf("[MCP] Error: PLACE_MESH rejected: mesh name contains '..': '%s'\n", mesh_file);
                fflush(stdout);
                return;
            }

            // Append .akm extension if not already present (mesh names in .a3d include it)
            char mesh_name[512];
            size_t mesh_file_len = strlen(mesh_file);
            if (mesh_file_len >= 4 && strcmp(mesh_file + mesh_file_len - 4, ".akm") == 0) {
                snprintf(mesh_name, sizeof(mesh_name), "%s", mesh_file);
            } else if (mesh_file_len > sizeof(mesh_name) - 5) {
                printf("[MCP] Error: PLACE_MESH mesh name too long: '%s'\n", mesh_file);
                fflush(stdout);
                return;
            } else {
                snprintf(mesh_name, sizeof(mesh_name), "%s.akm", mesh_file);
            }
            char obj_path[4096];
            ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
            Mesh* m = FindOrLoadMesh(world, obj_path, mesh_name);

            if (!m) {
                printf("[MCP] Error: LoadMesh failed for '%s'\n", mesh_file);
            } else {
                 // Construct 4x4 matrix
                 // Scale Z by HEIGHT_SCALE as per editor conventions
                 double tm[16] = {
                     (double)scale, 0, 0, 0,
                     0, (double)scale, 0, 0,
                     0, 0, (double)(scale * HEIGHT_SCALE), 0,
                     (double)x, (double)y, (double)z, 1
                 };

                 int flags = INST_USE_TREE | INST_VISIBLE;
                 // "MCP_Inst" name, parent=0
                 Inst* inst = CreateInst(m, flags, tm, "MCP_Inst", 0);
                 (void)inst;

                 RebuildWorld(world);
                 inst_list_dirty = true;
                 printf("[MCP] Success: Placed mesh '%s' at %.2f %.2f %.2f scale %.2f\n", mesh_file, x, y, z, scale);
            }
        } else {
            printf("[MCP] Error: Invalid PLACE_MESH args. Usage: PLACE_MESH <file> <x> <y> <z> <scale>. line='%s'\n", line);
        }
        fflush(stdout);
    }
    // ------------------------------------------------------------------
    // PICOCAD_PALETTE <akm_path> — audit AKM palette safety
    // Reads an ASCII PLY AKM file, parses the header to find RGB property
    // indices, then checks every vertex color against the SAFE_LEVELS set
    // {0,51,102,153,204,255} (must match picocad_to_akm.py:38).
    // ------------------------------------------------------------------
    else if (strcmp(cmd, "PICOCAD_PALETTE") == 0) {
        char akm_rel[4096];
        if (sscanf(line, "%*s %4095[^\n]", akm_rel) != 1) {
            printf("[MCP] Error: PICOCAD_PALETTE: missing path argument\n");
            fflush(stdout);
            return;
        }

        // Reject path traversal
        if (strstr(akm_rel, "..") != NULL) {
            printf("[MCP] Error: PICOCAD_PALETTE rejected: path contains \"..\": '%s'\n", akm_rel);
            fflush(stdout);
            return;
        }

        // Resolve path — absolute or relative to base_path
        char full_path[4096];
        if (akm_rel[0] == '/') {
            snprintf(full_path, sizeof(full_path), "%s", akm_rel);
        } else {
            snprintf(full_path, sizeof(full_path), "%s%s", base_path, akm_rel);
        }

        FILE* fp = fopen(full_path, "r");
        if (!fp) {
            printf("[MCP] Error: PICOCAD_PALETTE: cannot open '%s'\n", full_path);
            fflush(stdout);
            return;
        }

        // --- Parse PLY header ---
        int vert_count = 0;
        char props[32][32];
        int n_props = 0;
        bool header_done = false;
        char hdr_line[256];

        while (fgets(hdr_line, sizeof(hdr_line), fp)) {
            // Trim trailing whitespace
            size_t hdr_len = strlen(hdr_line);
            while (hdr_len > 0 && (hdr_line[hdr_len-1] == '\n' || hdr_line[hdr_len-1] == '\r' || hdr_line[hdr_len-1] == ' ')) {
                hdr_line[--hdr_len] = 0;
            }

            if (strncmp(hdr_line, "element vertex ", 15) == 0) {
                vert_count = atoi(hdr_line + 15);
            } else if (strncmp(hdr_line, "property ", 9) == 0) {
                // Extract the last token (property name)
                const char* last_space = strrchr(hdr_line, ' ');
                if (last_space && n_props < 32) {
                    snprintf(props[n_props], sizeof(props[n_props]), "%s", last_space + 1);
                    n_props++;
                }
            } else if (strcmp(hdr_line, "end_header") == 0) {
                header_done = true;
                break;
            }
        }

        if (!header_done) {
            fclose(fp);
            printf("[MCP] Error: PICOCAD_PALETTE: no end_header found in '%s'\n", full_path);
            fflush(stdout);
            return;
        }

        if (vert_count == 0) {
            fclose(fp);
            printf("[MCP] Error: PICOCAD_PALETTE: no vertices in '%s'\n", full_path);
            fflush(stdout);
            return;
        }

        // Find color property indices
        int r_idx = -1, g_idx = -1, b_idx = -1;
        for (int i = 0; i < n_props; i++) {
            if (strcmp(props[i], "red") == 0)   r_idx = i;
            if (strcmp(props[i], "green") == 0) g_idx = i;
            if (strcmp(props[i], "blue") == 0)  b_idx = i;
        }

        if (r_idx < 0 || g_idx < 0 || b_idx < 0) {
            fclose(fp);
            printf("[MCP] Error: PICOCAD_PALETTE: no vertex colors in '%s'\n", full_path);
            fflush(stdout);
            return;
        }

        // --- SAFE_LEVELS (must match picocad_to_akm.py:38) ---
        int safe[6] = {0, 51, 102, 153, 204, 255};

        auto is_safe = [&safe](int v) -> bool {
            for (int i = 0; i < 6; i++) {
                if (v == safe[i]) return true;
            }
            return false;
        };

        // --- Parse vertex data lines ---
        int n_bad = 0;
        char vline[1024];

        for (int i = 0; i < vert_count; i++) {
            if (!fgets(vline, sizeof(vline), fp)) break;

            // Tokenize by whitespace
            char* tokens[16];
            int n_tok = 0;
            char* tok = strtok(vline, " \t\r\n");
            while (tok && n_tok < 16) {
                tokens[n_tok++] = tok;
                tok = strtok(NULL, " \t\r\n");
            }

            if (n_tok <= r_idx || n_tok <= g_idx || n_tok <= b_idx) continue;

            int r = atoi(tokens[r_idx]);
            int g = atoi(tokens[g_idx]);
            int b = atoi(tokens[b_idx]);

            if (!is_safe(r) || !is_safe(g) || !is_safe(b)) {
                n_bad++;
            }
        }

        fclose(fp);

        if (n_bad == 0) {
            printf("[MCP] PICOCAD_PALETTE: %d verts, SAFE_LEVELS clean\n", vert_count);
        } else {
            int pct = (int)(100.0 * n_bad / vert_count);
            printf("[MCP] PICOCAD_PALETTE: %d verts, %d outside SAFE_LEVELS (%d%%)\n",
                   vert_count, n_bad, pct);
        }
        fflush(stdout);
    }
    // ------------------------------------------------------------------
    // PICOCAD_CONVERT <gltf_path> --output <akm_path>
    // Invokes scripts/picocad_to_akm.py via system() to convert a GLTF
    // file to AKM format.  --output is required.  Paths are sanitized
    // against shell metacharacters and path traversal.
    // ------------------------------------------------------------------
    else if (strcmp(cmd, "PICOCAD_CONVERT") == 0) {
        char gltf_rel[4096] = "";
        char out_rel[4096] = "";

        // Parse manually so paths with spaces are handled correctly:
        //   PICOCAD_CONVERT <gltf_path> --output <akm_path>
        // After the command, look for " --output " as a separator and take
        // whatever lies before and after it.
        const char* out_tok = strstr(line, "--output");
        if (!out_tok) {
            printf("[MCP] Error: PICOCAD_CONVERT: --output <akm_path> required\n");
            fflush(stdout); return;
        }

        // gltf_rel: everything between the command and " --output"
        const char* line_start = line + strlen(cmd) + 1;  // skip command + space
        const char* out_marker = out_tok - 1;  // last char before " --output"
        while (out_marker > line_start && (out_marker[0] == ' ' || out_marker[0] == '\t')) {
            out_marker--;
        }
        size_t gltf_len = out_marker - line_start + 1;
        if (gltf_len >= sizeof(gltf_rel)) gltf_len = sizeof(gltf_rel) - 1;
        memcpy(gltf_rel, line_start, gltf_len);
        gltf_rel[gltf_len] = '\0';

        // out_rel: everything after "--output" to end-of-line
        const char* out_val = out_tok + strlen("--output");
        while (*out_val == ' ' || *out_val == '\t') out_val++;
        // trim trailing whitespace / newlines
        size_t out_len = strlen(out_val);
        while (out_len > 0 && (out_val[out_len - 1] == '\n' || out_val[out_len - 1] == '\r' || out_val[out_len - 1] == ' ' || out_val[out_len - 1] == '\t')) {
            out_len--;
        }
        if (out_len >= sizeof(out_rel)) out_len = sizeof(out_rel) - 1;
        memcpy(out_rel, out_val, out_len);
        out_rel[out_len] = '\0';

        if (!gltf_rel[0] || !out_rel[0]) {
            printf("[MCP] Error: PICOCAD_CONVERT: --output <akm_path> required\n");
            fflush(stdout); return;
        }

        // Reject path traversal in both paths and base_path
        auto has_dotdot = [](const char* s) -> bool {
            return strstr(s, "..") != NULL;
        };
        if (has_dotdot(gltf_rel) || has_dotdot(out_rel) || has_dotdot(base_path)) {
            printf("[MCP] Error: PICOCAD_CONVERT rejected: path contains \"..\"\n");
            fflush(stdout);
            return;
        }

        // Shell metacharacter check: reject if any path contains dangerous chars
        auto has_unsafe_chars = [](const char* s) -> bool {
            const char unsafe[] = {'\'', '"', '`', '$', '\\', ';', '|', '&',
                                   '(', ')', '\n', '\r', '\0'};
            for (const char* p = s; *p; p++) {
                for (size_t ui = 0; ui < sizeof(unsafe); ui++) {
                    if (*p == unsafe[ui]) return true;
                }
            }
            return false;
        };
        if (has_unsafe_chars(gltf_rel) || has_unsafe_chars(out_rel) || has_unsafe_chars(base_path)) {
            printf("[MCP] Error: PICOCAD_CONVERT: unsafe chars in path\n");
            fflush(stdout);
            return;
        }

        // Determine Python interpreter
        const char* py = getenv("PYTHON");
        if (!py) py = "python3";
        if (has_unsafe_chars(py)) {
            printf("[MCP] Error: PICOCAD_CONVERT: unsafe chars in PYTHON path\n");
            fflush(stdout); return;
        }

        // Resolve script path (always relative to base_path)
        char script_path[4096];
        snprintf(script_path, sizeof(script_path), "%s/scripts/picocad_to_akm.py", base_path);
        // Strip duplicate slash if base_path already ends with /
        // (base_path always has trailing / from init)
        {
            size_t sl = strlen(script_path);
            // script_path is e.g. "//scripts/picocad_to_akm.py" if
            // base_path was "/" — but base_path is always "./" or
            // absolute with trailing /, so skip_leading = base_path[0] == '/';
            // Actually just check for double //:
            if (sl > 2 && script_path[0] == '/' && script_path[1] == '/') {
                // Remove the duplicate / by memmove
                memmove(script_path, script_path + 1, sl);
            }
        }

        // Resolve input/output paths — absolute or relative to base_path
        char gltf_full[4096], out_full[4096];
        if (gltf_rel[0] == '/') {
            snprintf(gltf_full, sizeof(gltf_full), "%s", gltf_rel);
        } else {
            snprintf(gltf_full, sizeof(gltf_full), "%s%s", base_path, gltf_rel);
        }
        if (out_rel[0] == '/') {
            snprintf(out_full, sizeof(out_full), "%s", out_rel);
        } else {
            snprintf(out_full, sizeof(out_full), "%s%s", base_path, out_rel);
        }

        // Build shell command: all paths in single quotes, stdout to /dev/null
        char cmd_buf[16384];
        int n = snprintf(cmd_buf, sizeof(cmd_buf),
            "'%s' '%s' '%s' --output '%s' >/dev/null",
            py, script_path, gltf_full, out_full);

        if (n >= (int)sizeof(cmd_buf)) {
            printf("[MCP] Error: PICOCAD_CONVERT: command too long\n");
            fflush(stdout);
            return;
        }

        int ret = system(cmd_buf);
        if (ret != 0) {
            printf("[MCP] Error: PICOCAD_CONVERT: %s exited with code %d\n", py, ret);
            fflush(stdout);
            return;
        }

        // Verify output file was actually created (silent Python failure check)
        if (access(out_full, F_OK) != 0) {
            printf("[MCP] Error: PICOCAD_CONVERT: output file not created at %s\n", out_full);
            fflush(stdout);
            return;
        }

        printf("[MCP] PICOCAD_CONVERT: Success: %s\n", out_full);
        fflush(stdout);
    }
    else if (strcmp(cmd, "BAKE_MESH_TO_TERRAIN") == 0) {
        if (!terrain || !world) {
            printf("[MCP] Error: terrain/world not ready for bake\n");
            fflush(stdout);
            return;
        }

        int bake_height = 1;
        int bake_material = 1;
        int bake_vertex_colors = 1;
        int overwrite_height = 1;
        int overwrite_material = 1;
        int solid_only = 0;
        double ray_top = 70000.0;
        int material_id = 0;
        int parsed = sscanf(
            line,
            "%*s %d %d %d %d %d %d %lf %d",
            &bake_height,
            &bake_material,
            &bake_vertex_colors,
            &overwrite_height,
            &overwrite_material,
            &solid_only,
            &ray_top,
            &material_id
        );

        if (!(parsed == -1 || parsed == 0 || parsed == 8)) {
            printf("[MCP] Error: Usage: BAKE_MESH_TO_TERRAIN <bake_height> <bake_material> <bake_vertex_colors> <overwrite_height> <overwrite_material> <solid_only> <ray_top> <material_id>\n");
            fflush(stdout);
            return;
        }

        if (material_id < 0) material_id = 0;
        if (material_id > 255) material_id = 255;

        auto coverage = BakeMeshesToTerrain(
            bake_height != 0,
            bake_material != 0,
            bake_vertex_colors != 0,
            overwrite_height != 0,
            overwrite_material != 0,
            solid_only != 0,
            ray_top,
            (uint8_t)material_id
        );
        // Emit per-instance coverage before the success line so Python can
        // parse them as part of the same batch stdout block (FL-1181 Candidate 1).
        for (const auto& cov : coverage) {
            printf("[MCP] BakeCoverage: name=%s footprint=%d above_baseline=%d at_baseline=%d\n",
                cov.name[0] ? cov.name : "(unknown)",
                cov.footprint_cells, cov.above_baseline, cov.at_baseline);
        }
        printf(
            "[MCP] Success: baked meshes to terrain (height=%d material=%d vertex_colors=%d overwrite_height=%d overwrite_material=%d solid_only=%d ray_top=%.1f material_id=%d)\n",
            bake_height != 0,
            bake_material != 0,
            bake_vertex_colors != 0,
            overwrite_height != 0,
            overwrite_material != 0,
            solid_only != 0,
            ray_top,
            material_id
        );
        fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_ALL_MESHES") == 0) {
        if (!world) {
            printf("[MCP] Error: no world loaded\n");
            fflush(stdout);
            return;
        }
        DeleteAllMeshInsts();
        printf("[MCP] Success: deleted all mesh instances\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "LOAD_SPRITE") == 0) {
        char* path = line + strlen("LOAD_SPRITE");
        while (*path == ' ')
            path++;
        char* nl = strchr(path, '\n');
        if (nl) *nl = 0;

        if (!path[0]) {
            printf("[MCP] Error: Invalid LOAD_SPRITE args. Usage: LOAD_SPRITE <path>\n");
            fflush(stdout);
            return;
        }

        char fullpath[1024];
        const char* name = path;

        // If path is relative, assume assets/sprites/ directory under base_path.
        if (!(path[0] == '/' || (strlen(path) > 1 && path[1] == ':'))) {
            snprintf(fullpath, sizeof(fullpath), "%sassets/sprites/%s", base_path, path);
            name = path;
            path = fullpath;
        } else {
            const char* base = strrchr(path, '/');
            if (!base) base = strrchr(path, '\\');
            if (base) name = base + 1;
        }

        Sprite* s = LoadSprite(path, name, 0, false);
        if (!s) {
            printf("[MCP] Error: LoadSprite failed for '%s'\n", path);
        } else {
            InitSpritePrefs(s);
            active_sprite = s;
            printf("[MCP] Success: Loaded sprite '%s'\n", name);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_ACTIVE_SPRITE") == 0) {
        char* path = line + strlen("SET_ACTIVE_SPRITE");
        while (*path == ' ')
            path++;
        char* nl = strchr(path, '\n');
        if (nl) *nl = 0;

        if (!path[0]) {
            printf("[MCP] Error: Invalid SET_ACTIVE_SPRITE args. Usage: SET_ACTIVE_SPRITE <name|path>\n");
            fflush(stdout);
            return;
        }

        char fullpath[1024];
        const char* name = path;

        if (!(path[0] == '/' || (strlen(path) > 1 && path[1] == ':'))) {
            snprintf(fullpath, sizeof(fullpath), "%sassets/sprites/%s", base_path, path);
            name = path;
            path = fullpath;
        } else {
            const char* base = strrchr(path, '/');
            if (!base) base = strrchr(path, '\\');
            if (base) name = base + 1;
        }

        Sprite* s = FindSpriteByName(name);
        if (!s) {
            s = LoadSprite(path, name, 0, false);
        }

        if (!s) {
            printf("[MCP] Error: Could not activate sprite '%s'\n", name);
        } else {
            InitSpritePrefs(s);
            active_sprite = s;
            printf("[MCP] Success: Active sprite set to '%s'\n", name);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "PLACE_SPRITE") == 0) {
        char sprite_path[512];
        float x, y, z, yaw;
        int anim = -1;
        int frame = -1;

        int count = sscanf(line, "%*s %s %f %f %f %f %d %d", sprite_path, &x, &y, &z, &yaw, &anim, &frame);
        if (count < 5) {
            printf("[MCP] Error: Invalid PLACE_SPRITE args. Usage: PLACE_SPRITE <path> <x> <y> <z> <yaw> [anim] [frame]\n");
            fflush(stdout);
            return;
        }

        char fullpath[1024];
        const char* name = sprite_path;
        const char* path = sprite_path;

        // If path is relative, assume assets/sprites/ directory under base_path.
        if (!(sprite_path[0] == '/' || (strlen(sprite_path) > 1 && sprite_path[1] == ':'))) {
            snprintf(fullpath, sizeof(fullpath), "%sassets/sprites/%s", base_path, sprite_path);
            name = sprite_path;
            path = fullpath;
        } else {
            const char* base = strrchr(sprite_path, '/');
            if (!base) base = strrchr(sprite_path, '\\');
            if (base) name = base + 1;
        }

        Sprite* s = LoadSprite(path, name, 0, false);
        if (!s) {
            printf("[MCP] Error: LoadSprite failed for '%s'\n", path);
            fflush(stdout);
            return;
        }

        InitSpritePrefs(s);
        SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(s);
        SpritePrefs defs = {0};
        if (!sp) sp = &defs;

        int _anim = anim >= 0 ? anim : sp->anim;
        if (_anim < 0 || _anim >= s->anims)
            _anim = 0;

        int _frame = frame >= 0 ? frame : sp->frame;
        if (_frame < 0 || _frame >= s->anim[_anim].length)
            _frame = 0;

        float pos[3] = { x, y, z + sp->height };
        int flags = INST_USE_TREE | INST_VISIBLE;
        int inst_story_id = -1;

        Inst* inst = URDO_Create(world, s, flags, pos, yaw, _anim, _frame, sp->t, inst_story_id);
        if (inst) {
            active_sprite = s;
            inst_list_dirty = true;
            RebuildWorld(world);
            printf("[MCP] Success: Placed sprite '%s' at %.2f %.2f %.2f yaw %.2f anim %d frame %d\n",
                   name, x, y, z, yaw, _anim, _frame);
        } else {
            printf("[MCP] Error: Failed to place sprite '%s'\n", name);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "PLACE_SPRITE_ACTIVE") == 0) {
        float x, y, z, yaw;
        int anim = -1;
        int frame = -1;

        int count = sscanf(line, "%*s %f %f %f %f %d %d", &x, &y, &z, &yaw, &anim, &frame);
        if (count < 4) {
            printf("[MCP] Error: Invalid PLACE_SPRITE_ACTIVE args. Usage: PLACE_SPRITE_ACTIVE <x> <y> <z> <yaw> [anim] [frame]\n");
            fflush(stdout);
            return;
        }

        if (!active_sprite) {
            printf("[MCP] Error: No active sprite loaded. Use LOAD_SPRITE first.\n");
            fflush(stdout);
            return;
        }

        InitSpritePrefs(active_sprite);
        SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(active_sprite);
        SpritePrefs defs = {0};
        if (!sp) sp = &defs;

        int _anim = anim >= 0 ? anim : sp->anim;
        if (_anim < 0 || _anim >= active_sprite->anims)
            _anim = 0;

        int _frame = frame >= 0 ? frame : sp->frame;
        if (_frame < 0 || _frame >= active_sprite->anim[_anim].length)
            _frame = 0;

        float pos[3] = { x, y, z + sp->height };
        int flags = INST_USE_TREE | INST_VISIBLE;
        int inst_story_id = -1;

        Inst* inst = URDO_Create(world, active_sprite, flags, pos, yaw, _anim, _frame, sp->t, inst_story_id);
        if (inst) {
            inst_list_dirty = true;
            RebuildWorld(world);
            printf("[MCP] Success: Placed active sprite at %.2f %.2f %.2f yaw %.2f anim %d frame %d\n",
                   x, y, z, yaw, _anim, _frame);
        } else {
            printf("[MCP] Error: Failed to place active sprite\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "PLACE_SPRITE_ACTIVE_REL") == 0) {
        float dx, dy, dz, yaw;
        int anim = -1;
        int frame = -1;

        int count = sscanf(line, "%*s %f %f %f %f %d %d", &dx, &dy, &dz, &yaw, &anim, &frame);
        if (count < 4) {
            printf("[MCP] Error: Invalid PLACE_SPRITE_ACTIVE_REL args. Usage: PLACE_SPRITE_ACTIVE_REL <dx> <dy> <dz> <yaw> [anim] [frame]\n");
            fflush(stdout);
            return;
        }

        if (!active_sprite) {
            printf("[MCP] Error: No active sprite loaded. Use LOAD_SPRITE or SET_ACTIVE_SPRITE first.\n");
            fflush(stdout);
            return;
        }

        InitSpritePrefs(active_sprite);
        SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(active_sprite);
        SpritePrefs defs = {0};
        if (!sp) sp = &defs;

        int _anim = anim >= 0 ? anim : sp->anim;
        if (_anim < 0 || _anim >= active_sprite->anims)
            _anim = 0;

        int _frame = frame >= 0 ? frame : sp->frame;
        if (_frame < 0 || _frame >= active_sprite->anim[_anim].length)
            _frame = 0;

        float x = pos_x + dx;
        float y = pos_y + dy;
        float z = pos_z + dz;

        float pos[3] = { x, y, z + sp->height };
        int flags = INST_USE_TREE | INST_VISIBLE;
        int inst_story_id = -1;

        Inst* inst = URDO_Create(world, active_sprite, flags, pos, yaw, _anim, _frame, sp->t, inst_story_id);
        if (inst) {
            inst_list_dirty = true;
            RebuildWorld(world);
            printf("[MCP] Success: Placed active sprite at %.2f %.2f %.2f yaw %.2f anim %d frame %d (relative)\n",
                   x, y, z, yaw, _anim, _frame);
        } else {
            printf("[MCP] Error: Failed to place active sprite (relative)\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_TERRAIN_HEIGHT") == 0) {
        int h = 0;
        if (sscanf(line, "%*s %d", &h) == 1) {

            if (!terrain) {
                printf("[MCP] Error: No terrain\n");
            } else {
                // Ensure we have at least one patch at 0,0
                if (!GetTerrainPatch(terrain, 0, 0))
                {
                    AddTerrainPatch(terrain, 0, 0, h);
                    EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
                }

                int patch_count = 0;
                Patch** patches = 0;
                GetAllTerrainPatches(terrain, &patches, &patch_count);

                for(int i=0; i<patch_count; i++) {
                    uint16_t* map = GetTerrainHeightMap(patches[i]);
                    // Only flatten existing chunks
                    for(int j=0; j<(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1); j++) map[j] = (uint16_t)h;
                    UpdateTerrainHeightMap(patches[i]);
                }
                if(patches) free(patches);
                printf("[MCP] Success: Terrain height set to %d\n", h);
            }
        } else {
            printf("[MCP] Error: Invalid SET_TERRAIN_HEIGHT args. Usage: SET_TERRAIN_HEIGHT <h>. line='%s'\n", line);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "PROBE_TERRAIN") == 0) {
        float x, y;
        if (sscanf(line, "%*s %f %f", &x, &y) == 2) {
             if (!terrain) {
                printf("[MCP] Error: No terrain\n");
            } else {
                int px = (int)floor(x / (double)(HEIGHT_CELLS * VISUAL_CELLS));
                int py = (int)floor(y / (double)(HEIGHT_CELLS * VISUAL_CELLS));

                // Adjust negatives for patch grid logic if needed
                if (x < 0) px -= 1; // Basic floor logic handles this usually but checking patch grid alignment
                // Actually GetTerrainPatch takes VISUAL_CELLS units?
                // Let's use the standard patch coordinate conversion:
                // Patch size in cells = HEIGHT_CELLS (actually 16 z-steps per cell?)
                // wait, HEIGHT_CELLS is 4. VISUAL_CELLS is 8.
                // Looking at Merge::CommitPatch: d = GetTerrainPatch(terrain, x / VISUAL_CELLS, y / VISUAL_CELLS);
                // So patch coords are (cell_x / VISUAL_CELLS, cell_y / VISUAL_CELLS).

                int cell_x = (int)floor(x);
                int cell_y = (int)floor(y);

                int patch_x = (int)floor((double)cell_x / VISUAL_CELLS);
                int patch_y = (int)floor((double)cell_y / VISUAL_CELLS);

                Patch* p = GetTerrainPatch(terrain, patch_x, patch_y);

                if (p) {
                    uint16_t* map = GetTerrainHeightMap(p);
                    // Local visual-cell coordinates within patch
                    int lx = cell_x % VISUAL_CELLS;
                    int ly = cell_y % VISUAL_CELLS;
                    if (lx < 0) lx += VISUAL_CELLS;
                    if (ly < 0) ly += VISUAL_CELLS;
                    // Convert visual-cell coords to height-space coords for bilinear sampling.
                    // Height grid is (HEIGHT_CELLS+1)x(HEIGHT_CELLS+1) = 5x5 vertices.
                    // Visual grid is VISUAL_CELLS x VISUAL_CELLS = 8x8 cells.
                    // +0.5 centers the sample within the visual cell.
                    double fx = (lx + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
                    double fy = (ly + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;

                    int h_val = (int)round(SampleHeightBilinear(map, fx, fy));
                    printf("[MCP] Terrain at %.1f,%.1f: height=%d\n", x, y, h_val);
                } else {
                     printf("[MCP] Terrain at %.1f,%.1f: height=0 (No Patch)\n", x, y);
                }
            }
        } else {
            printf("[MCP] Error: Invalid PROBE_TERRAIN args. Usage: PROBE_TERRAIN <x> <y>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_TERRAIN_GRID") == 0) {
        // Batch terrain query: returns a grid of material IDs and heights.
        // Usage: QUERY_TERRAIN_GRID cx cy grid_w grid_h scale
        //   cx, cy   = world-space center of the query area
        //   grid_w   = number of columns in the output grid
        //   grid_h   = number of rows in the output grid
        //   scale    = world units per grid cell
        //
        // NOTE: Height values are raw uint16 from A3D terrain encoding.
        // The game renderer uses water=55 (mainmenu.cpp:1393, game_app.cpp:2060,
        // game_web.cpp:1042, server_state.h SVR_WATER_LEVEL). 0x8000 was a legacy
        // minimap threshold and is NOT the water level. Consumers of this data
        // should compare heights against 55, not 0x8000.
        float cx, cy, scale;
        int gw, gh;
        if (sscanf(line, "%*s %f %f %d %d %f", &cx, &cy, &gw, &gh, &scale) == 5) {
            if (!terrain) {
                printf("[MCP] Error: No terrain\n");
            } else if (gw < 1 || gw > 256 || gh < 1 || gh > 256) {
                printf("[MCP] Error: Grid size must be 1-256 in each dimension\n");
            } else if (scale <= 0.0f) {
                printf("[MCP] Error: Scale must be positive\n");
            } else {
                printf("[TERRAIN_GRID_START] w=%d h=%d cx=%.2f cy=%.2f scale=%.2f\n", gw, gh, cx, cy, scale);
                for (int gy = 0; gy < gh; gy++) {
                    for (int gx = 0; gx < gw; gx++) {
                        float wx = cx + (gx - gw / 2) * scale;
                        float wy = cy + (gy - gh / 2) * scale;

                        int patch_x = (int)floor(wx / (HEIGHT_CELLS * 2));
                        int patch_y = (int)floor(wy / (HEIGHT_CELLS * 2));
                        Patch* p = GetTerrainPatch(terrain, patch_x, patch_y);

                        int mat_id = -1;
                        int height = 0;
                        if (p) {
                            uint16_t* hmap = GetTerrainHeightMap(p);
                            uint16_t* vmap = GetTerrainVisualMap(p);

                            float lx = fmod(wx, (float)(HEIGHT_CELLS * 2));
                            float ly = fmod(wy, (float)(HEIGHT_CELLS * 2));
                            if (lx < 0) lx += HEIGHT_CELLS * 2;
                            if (ly < 0) ly += HEIGHT_CELLS * 2;

                            int hx = (int)(lx / 2) % (HEIGHT_CELLS + 1);
                            int hy = (int)(ly / 2) % (HEIGHT_CELLS + 1);
                            int vx = (int)(lx) % (VISUAL_CELLS + 1);
                            int vy = (int)(ly) % (VISUAL_CELLS + 1);

                            height = hmap[hy * (HEIGHT_CELLS + 1) + hx];
                            uint16_t v = vmap[vy * (VISUAL_CELLS + 1) + vx];
                            mat_id = v & 0xFF;
                        }

                        if (gx > 0) printf(" ");
                        printf("%d,%d", mat_id, height);
                    }
                    printf("\n");
                }
                printf("[TERRAIN_GRID_END]\n");
            }
        } else {
            printf("[MCP] Error: Usage: QUERY_TERRAIN_GRID <cx> <cy> <grid_w> <grid_h> <scale>\n");
        }
        fflush(stdout);
    }
    // ------------------------------------------------------------------
    // QUERY_TERRAIN_HEIGHT <x> <y>
    // Returns the raw terrain height value at world-space (x, y).
    // Uses bilinear interpolation across the 5x5 height-map grid of the
    // enclosing terrain patch.  The raw value is the same uint16 stored in
    // the .a3d file — pass it directly as the z argument to PLACE_MESH.
    // Returns "[MCP] QUERY_TERRAIN_HEIGHT: <x> <y> -> <h>"
    // ------------------------------------------------------------------
    else if (strcmp(cmd, "QUERY_TERRAIN_HEIGHT") == 0) {
        float wx, wy;
        if (sscanf(line, "%*s %f %f", &wx, &wy) == 2) {
            if (!terrain) {
                printf("[MCP] Error: QUERY_TERRAIN_HEIGHT: no terrain loaded\n");
            } else {
                int patch_x = (int)floor((double)wx / (HEIGHT_CELLS * 2));
                int patch_y = (int)floor((double)wy / (HEIGHT_CELLS * 2));
                Patch* p = GetTerrainPatch(terrain, patch_x, patch_y);
                if (!p) {
                    printf("[MCP] Error: QUERY_TERRAIN_HEIGHT: no terrain at %.2f %.2f\n", wx, wy);
                } else {
                    uint16_t* hmap = GetTerrainHeightMap(p);
                    double lx = fmod((double)wx, (double)(HEIGHT_CELLS * 2));
                    double ly = fmod((double)wy, (double)(HEIGHT_CELLS * 2));
                    if (lx < 0.0) lx += HEIGHT_CELLS * 2;
                    if (ly < 0.0) ly += HEIGHT_CELLS * 2;
                    double h = SampleHeightBilinear(hmap, lx / 2.0, ly / 2.0);
                    printf("[MCP] QUERY_TERRAIN_HEIGHT: %.2f %.2f -> %.0f\n", wx, wy, h);
                }
            }
        } else {
            printf("[MCP] Error: Usage: QUERY_TERRAIN_HEIGHT <x> <y>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_WATER_LEVEL") == 0) {
        // Returns the game's canonical water level used by the renderer.
        // This is the threshold below which terrain is rendered as water.
        // Canonical sources: mainmenu.cpp:1393, game_app.cpp:2060,
        // game_web.cpp:1042, server_state.h SVR_WATER_LEVEL.
        // NOTE: 0x8000 is the elevation bit in the visual map encoding,
        // NOT the water level. 0xA000 is the default new-terrain height.
        printf("[WATER_LEVEL] 55\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_GRID") == 0) {
        float val = 1.0f;
        if (sscanf(line, "%*s %f", &val) == 1) {
            grid_alpha = val;
            printf("[MCP] Success: Grid alpha set to %.2f\n", grid_alpha);
        } else {
            printf("[MCP] Error: Invalid SET_GRID args. Usage: SET_GRID <alpha>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_TERRAIN_OVERVIEW") == 0) {
        int enabled = 1;
        if (sscanf(line, "%*s %d", &enabled) == 1) {
            g_editor_force_exact_terrain = enabled ? false : true;
            printf("[MCP] Success: Terrain overview %s\n", g_editor_force_exact_terrain ? "disabled" : "enabled");
        } else {
            printf("[MCP] Error: Invalid SET_TERRAIN_OVERVIEW args. Usage: SET_TERRAIN_OVERVIEW 0|1\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "GET_CAMERA") == 0) {
        printf("[MCP] Camera: pos=%.2f,%.2f,%.2f yaw=%.2f pitch=%.2f font_size=%.3f\n",
               pos_x, pos_y, pos_z, rot_yaw, rot_pitch, font_size);
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_CAMERA") == 0) {
        float x, y, z, yaw, pitch;
        if (sscanf(line, "%*s %f %f %f %f %f", &x, &y, &z, &yaw, &pitch) == 5) {
            pos_x = x; pos_y = y; pos_z = z;
            rot_yaw = yaw;
            rot_pitch = pitch;
            if (rot_pitch > 90) rot_pitch = 90;
            if (rot_pitch < 1) rot_pitch = 1;
            printf("[MCP] Success: Camera set to %.2f,%.2f,%.2f yaw=%.2f pitch=%.2f font_size=%.3f\n", x, y, z, yaw, rot_pitch, font_size);
        } else {
            printf("[MCP] Error: Invalid SET_CAMERA args. Usage: SET_CAMERA <x> <y> <z> <yaw> <pitch>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_CAMERA_VIEW") == 0) {
        float x, y, z, yaw, pitch, zoom;
        if (sscanf(line, "%*s %f %f %f %f %f %f", &x, &y, &z, &yaw, &pitch, &zoom) == 6) {
            pos_x = x; pos_y = y; pos_z = z;
            rot_yaw = yaw;
            rot_pitch = pitch;
            font_size = zoom;
            if (rot_pitch > 90) rot_pitch = 90;
            if (rot_pitch < 1) rot_pitch = 1;
            ClampInteractiveFontSize();
            printf("[MCP] Success: Camera view set to %.2f,%.2f,%.2f yaw=%.2f pitch=%.2f font_size=%.3f\n",
                   pos_x, pos_y, pos_z, rot_yaw, rot_pitch, font_size);
        } else {
            printf("[MCP] Error: Invalid SET_CAMERA_VIEW args. Usage: SET_CAMERA_VIEW <x> <y> <z> <yaw> <pitch> <font_size>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "CAPTURE_FRAME") == 0) {
        // [FL-3851] MCP-triggered frame capture at current camera position.
        // Usage: CAPTURE_FRAME /path/to/output.ppm
        // The capture happens on the next rendered frame (3 frame delay for camera settle).
        extern char g_mcp_capture_path[1024];
        char path[1024] = {0};
        if (sscanf(line, "%*s %1023s", path) == 1) {
            strncpy(g_mcp_capture_path, path, sizeof(g_mcp_capture_path) - 1);
            g_mcp_capture_path[sizeof(g_mcp_capture_path) - 1] = 0;
            printf("[MCP] CAPTURE_FRAME queued: %s (will capture in 3 frames)\n", path);
        } else {
            printf("[MCP] Error: CAPTURE_FRAME requires path. Usage: CAPTURE_FRAME /path/to/output.ppm\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "DUMP_MATERIAL_TABLE") == 0) {
        // [FL-3851] Dump loaded material palette as JSON.
        // Usage: DUMP_MATERIAL_TABLE [id0 id1 id2 ...]
        // If no ids given, dumps all 256 materials.
        // Output per material: id, mode, 4x16 shade cells with fg/bg RGB, glyph, flags.
        Material* mats = (Material*)GetMaterialArr();
        if (!mats) {
            printf("[MCP] Error: Material array not initialized\n");
            fflush(stdout);
            return;
        }
        // Parse optional material IDs from remaining args
        int ids[256];
        int id_count = 0;
        char* rest = line + strlen("DUMP_MATERIAL_TABLE");
        while (*rest == ' ') rest++;
        if (*rest && *rest != '\n' && *rest != '\r') {
            char* tok = strtok(rest, " \t\r\n");
            while (tok && id_count < 256) {
                ids[id_count++] = atoi(tok);
                tok = strtok(nullptr, " \t\r\n");
            }
        }
        if (id_count == 0) {
            for (int i = 0; i < 256; i++) ids[id_count++] = i;
        }
        printf("{\n  \"materials\": [\n");
        bool first_mat = true;
        for (int mi = 0; mi < id_count; mi++) {
            int mat_id = ids[mi];
            if (mat_id < 0 || mat_id >= 256) continue;
            if (!first_mat) printf(",\n");
            first_mat = false;
            printf("    {\n");
            printf("      \"id\": %d,\n", mat_id);
            printf("      \"mode\": %d,\n", mats[mat_id].mode);
            printf("      \"shade\": [\n");
            for (int elev = 0; elev < 4; elev++) {
                printf("        {\n");
                printf("          \"elevation\": %d,\n", elev);
                printf("          \"cells\": [\n");
                for (int col = 0; col < 16; col++) {
                    MatCell& c = mats[mat_id].shade[elev][col];
                    char glyph_char = (c.gl >= 32 && c.gl < 127) ? (char)c.gl : '?';
                    printf("            {\"col\": %d, \"fg\": [%d,%d,%d], \"bg\": [%d,%d,%d], \"glyph\": %d, \"glyph_char\": \"%c\", \"flags\": %d}",
                        col, c.fg[0], c.fg[1], c.fg[2], c.bg[0], c.bg[1], c.bg[2], c.gl, glyph_char, c.flags);
                    if (col < 15) printf(",");
                    printf("\n");
                }
                printf("          ]\n");
                printf("        }");
                if (elev < 3) printf(",");
                printf("\n");
            }
            printf("      ]\n");
            printf("    }");
        }
        printf("\n  ]\n}\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_TOPDOWN_VIEW") == 0) {
        // [FL-3851] Set camera to top-down view for clean frame capture.
        // Usage: SET_TOPDOWN_VIEW FULL
        //        SET_TOPDOWN_VIEW BBOX min_x min_y max_x max_y
        // Python resolves feature names; C only does FULL and BBOX.
        char mode[64] = {0};
        if (sscanf(line, "%*s %63s", mode) == 1) {
            if (strcmp(mode, "FULL") == 0) {
                // Enumerate all patches, compute world extent, center + zoom with 5% margin
                if (!terrain) {
                    printf("[MCP] Error: SET_TOPDOWN_VIEW FULL: no terrain loaded\n");
                } else {
                    int patch_count = 0;
                    Patch** patches = 0;
                    GetAllTerrainPatches(terrain, &patches, &patch_count);
                    if (patch_count > 0) {
                        int min_px = INT_MAX, max_px = INT_MIN;
                        int min_py = INT_MAX, max_py = INT_MIN;
                        for (int i = 0; i < patch_count; i++) {
                            int px, py;
                            GetTerrainPatch(terrain, patches[i], &px, &py);
                            if (px < min_px) min_px = px;
                            if (px > max_px) max_px = px;
                            if (py < min_py) min_py = py;
                            if (py > max_py) max_py = py;
                        }
                        pos_x = (float)((min_px + max_px + 1) * VISUAL_CELLS) * 0.5f;
                        pos_y = (float)((min_py + max_py + 1) * VISUAL_CELLS) * 0.5f;
                        int cx = (min_px + max_px) / 2;
                        int cy = (min_py + max_py) / 2;
                        Patch* cp = GetTerrainPatch(terrain, cx, cy);
                        if (cp) {
                            uint16_t* hmap = GetTerrainHeightMap(cp);
                            int h = hmap[(HEIGHT_CELLS/2) * (HEIGHT_CELLS+1) + HEIGHT_CELLS/2];
                            pos_z = (float)h + 200.0f;
                            probe_z = h > 0x100 ? h - 0x100 : 0;
                        }
                        int span_x = (max_px - min_px + 1) * VISUAL_CELLS;
                        int span_y = (max_py - min_py + 1) * VISUAL_CELLS;
                        int max_span = span_x > span_y ? span_x : span_y;
                        if (max_span > 0) {
                            ImGuiIO& io = ImGui::GetIO();
                            float display_min = io.DisplaySize.x < io.DisplaySize.y ? io.DisplaySize.x : io.DisplaySize.y;
                            font_size = display_min / (float)max_span * 0.95f; // 5% margin
                            ClampInteractiveFontSize();
                        }
                    }
                    if (patches) free(patches);
                    rot_pitch = 90.0f;
                    rot_yaw = 0.0f;
                    ImGuiIO& io = ImGui::GetIO();
                    printf("[MCP] SET_TOPDOWN_VIEW: pos=(%.1f,%.1f,%.1f) pitch=%.1f yaw=%.1f font_size=%.3f viewport=%.0fx%.0f\n",
                        pos_x, pos_y, pos_z, rot_pitch, rot_yaw, font_size,
                        io.DisplaySize.x, io.DisplaySize.y);
                }
            } else if (strcmp(mode, "BBOX") == 0) {
                float min_x, min_y, max_x, max_y;
                if (sscanf(line, "%*s %*s %f %f %f %f", &min_x, &min_y, &max_x, &max_y) == 4) {
                    pos_x = (min_x + max_x) * 0.5f;
                    pos_y = (min_y + max_y) * 0.5f;
                    // Use center height from terrain if available
                    if (terrain) {
                        int cx = (int)floor(pos_x / (HEIGHT_CELLS * 2));
                        int cy = (int)floor(pos_y / (HEIGHT_CELLS * 2));
                        Patch* cp = GetTerrainPatch(terrain, cx, cy);
                        if (cp) {
                            uint16_t* hmap = GetTerrainHeightMap(cp);
                            int h = hmap[(HEIGHT_CELLS/2) * (HEIGHT_CELLS+1) + HEIGHT_CELLS/2];
                            pos_z = (float)h + 200.0f;
                            probe_z = h > 0x100 ? h - 0x100 : 0;
                        }
                    }
                    float span_x = max_x - min_x;
                    float span_y = max_y - min_y;
                    float max_span = span_x > span_y ? span_x : span_y;
                    if (max_span > 0) {
                        ImGuiIO& io = ImGui::GetIO();
                        float display_min = io.DisplaySize.x < io.DisplaySize.y ? io.DisplaySize.x : io.DisplaySize.y;
                        font_size = display_min / max_span * 0.95f;
                        ClampInteractiveFontSize();
                    }
                    rot_pitch = 90.0f;
                    rot_yaw = 0.0f;
                    ImGuiIO& io = ImGui::GetIO();
                    printf("[MCP] SET_TOPDOWN_VIEW: pos=(%.1f,%.1f,%.1f) pitch=%.1f yaw=%.1f font_size=%.3f viewport=%.0fx%.0f\n",
                        pos_x, pos_y, pos_z, rot_pitch, rot_yaw, font_size,
                        io.DisplaySize.x, io.DisplaySize.y);
                } else {
                    printf("[MCP] Error: SET_TOPDOWN_VIEW BBOX requires min_x min_y max_x max_y\n");
                }
            } else {
                printf("[MCP] Error: Unknown SET_TOPDOWN_VIEW mode '%s'. Use FULL or BBOX min_x min_y max_x max_y\n", mode);
            }
        } else {
            printf("[MCP] Error: SET_TOPDOWN_VIEW requires mode. Usage: SET_TOPDOWN_VIEW FULL | BBOX min_x min_y max_x max_y\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "CAPTURE_CLEAN_FRAME") == 0 || strcmp(cmd, "CAPTURE_CLEAN_FRAME_AND_QUIT") == 0) {
        // [FL-3851] Schedule a clean frame capture (no UI, before overlays).
        // Usage: CAPTURE_CLEAN_FRAME <directory>
        //        CAPTURE_CLEAN_FRAME_AND_QUIT <directory>
        // Writes frame.png and frame.camera.json in the given directory.
        extern char g_mcp_clean_capture_dir[1024];
        char dir[1024] = {0};
        if (sscanf(line, "%*s %1023s", dir) == 1) {
            mkdir(dir, 0755); // ensure directory exists
            strncpy(g_mcp_clean_capture_dir, dir, sizeof(g_mcp_clean_capture_dir) - 1);
            g_mcp_clean_capture_dir[sizeof(g_mcp_clean_capture_dir) - 1] = 0;
            if (strcmp(cmd, "CAPTURE_CLEAN_FRAME_AND_QUIT") == 0)
                g_batch_exit_after_clean_capture = true;
            printf("[MCP] CAPTURE_CLEAN_FRAME queued: %s (captures at the next clean render point)\n", dir);
        } else {
            printf("[MCP] Error: CAPTURE_CLEAN_FRAME requires directory path. Usage: CAPTURE_CLEAN_FRAME <dir>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "CAPTURE_UI_FRAME") == 0) {
        // [FL-4131] Schedule a UI frame capture (game + ImGui panels, post-RenderDrawData).
        // Usage: CAPTURE_UI_FRAME <directory>
        // Writes ui_frame.png in the given directory. Only valid in headed/daemon mode;
        // ImGui panels are not populated in --batch so the result would show only the game render.
        extern char g_mcp_ui_capture_dir[1024];
        char dir[1024] = {0};
        if (sscanf(line, "%*s %1023s", dir) == 1) {
            mkdir(dir, 0755);
            strncpy(g_mcp_ui_capture_dir, dir, sizeof(g_mcp_ui_capture_dir) - 1);
            g_mcp_ui_capture_dir[sizeof(g_mcp_ui_capture_dir) - 1] = 0;
            printf("[MCP] CAPTURE_UI_FRAME queued: %s\n", dir);
        } else {
            printf("[MCP] Error: CAPTURE_UI_FRAME requires directory path. Usage: CAPTURE_UI_FRAME <dir>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "PROJECT_MARKER_LABELS") == 0) {
        float display_w = 1600.0f;
        float display_h = 1200.0f;
        sscanf(line, "%*s %f %f", &display_w, &display_h);
        if (display_w <= 0.0f) display_w = 1600.0f;
        if (display_h <= 0.0f) display_h = 1200.0f;
        PrintProjectedEditorMarkerLabels(display_w, display_h);
    }
    else if (strcmp(cmd, "FOCUS_ORIGIN") == 0) {
        pos_x = 0; pos_y = 0; pos_z = 0;
        rot_yaw = 45;
        printf("[MCP] Success: Camera focused on origin\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "DEBUG_AXIS") == 0) {
        // [DEPENDENCY:BLENDER] Debug axis mesh assumes Blender-exported Cube.akm exists in assets/meshes/ directory.
        const char* axis_mesh = "assets/meshes/Cube.akm";

        // Non-recursive manual creation
        Mesh* m = LoadMesh(world, axis_mesh);
        if (m) {
            // Origin
            double tm0[16] = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 0, 0, 0, 1 };
            CreateInst(m, INST_USE_TREE|INST_VISIBLE, tm0, "DEBUG_AXIS_O", 0);

            // X (+5)
            double tmX[16] = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 5, 0, 0, 1 };
            CreateInst(m, INST_USE_TREE|INST_VISIBLE, tmX, "DEBUG_AXIS_X", 0);

            // Y (+5)
            double tmY[16] = { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 0, 5, 0, 1 };
            CreateInst(m, INST_USE_TREE|INST_VISIBLE, tmY, "DEBUG_AXIS_Y", 0);

            RebuildWorld(world);
            inst_list_dirty = true;
            printf("[MCP] Success: Spawned DEBUG_AXIS\n");
        } else {
             printf("[MCP] Error: Could not load %s for DEBUG_AXIS\n", axis_mesh);
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "LIST_INSTANCES") == 0) {
         Inst** insts = 0;
         int count = CollectMeshInsts(world, &insts);

         printf("[MCP] Instance List (Meshes only):\n");
         for(int i=0; i<count; i++) {
             float pos[3] = {0,0,0}; float yaw=0;
             // GetInstItem is for Items? GetInstTM?
             // Use GetInstTM to get position?
             double tm[16];
             float x=0,y=0,z=0;
             if (GetInstTM(insts[i], tm)) {
                 x = (float)tm[12];
                 y = (float)tm[13];
                 z = (float)tm[14];
             }

             const char* name = GetInstName(insts[i]);
             printf("[MCP]  %d %s %.2f %.2f %.2f\n", i, (name && name[0]) ? name : "???", x, y, z);
         }

         if (insts) free(insts);
         printf("[MCP] Total Mesh Instances: %d\n", count);
         fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_INSTANCE") == 0)
    {
        int idx;
        if (sscanf(line, "%*s %d", &idx) == 1 && world)
        {
            Inst** insts = 0;
            int count = CollectMeshInsts(world, &insts);
            if (idx >= 0 && idx < count)
            {
                URDO_Open();
                URDO_Delete(insts[idx]);
                URDO_Close();
                inst_list_dirty = true;
                printf("[MCP] Success: deleted inst %d\n", idx);
            }
            else
            {
                printf("[MCP] Error: Instance index %d out of range (0-%d)\n", idx, count - 1);
            }
            if (insts) free(insts);
        }
        else
        {
            printf("[MCP] Error: Usage: DELETE_INSTANCE <idx>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "LIST_MESHES") == 0) {
        if (!world) { printf("[MCP] Error: no world loaded\n"); fflush(stdout); return; }
        int count = 0;
        printf("[MCP] Mesh List:\n");
        for (Mesh* m = GetFirstMesh(world); m; m = GetNextMesh(m)) {
            char name[256];
            GetMeshName(m, name, 256);
            int faces = GetMeshFaces(m);
            printf("[MCP]  %d %s faces=%d\n", count, name, faces);
            count++;
        }
        printf("[MCP] Total Meshes: %d\n", count);
        fflush(stdout);
    }
    else if (strcmp(cmd, "LIST_SPRITES") == 0) {
        int count = 0;
        printf("[MCP] Sprite List:\n");
        for (Sprite* s = GetFirstSprite(false); s; s = GetNextSprite(s, false)) {
            printf("[MCP]  %d %s angles=%d anims=%d frames=%d projs=%d\n",
                   count, s->name ? s->name : "???",
                   s->angles, s->anims, s->frames, s->projs);
            count++;
        }
        printf("[MCP] Total Sprites: %d\n", count);
        fflush(stdout);
    }
    else if (strcmp(cmd, "RELOAD_SPRITES") == 0) {
        reload_sprites_requested = true;
        printf("[MCP] Success: Sprite reload requested\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "OPEN_TERMPP") == 0) {
        // [FL-4131] Defer the actual TermOpen to the next my_render frame so it
        // runs with the editor's live A3D_WND*/yaw/pos scope (same path as the
        // on-screen TERM++ button).
        g_open_termpp_requested = true;
        printf("[MCP] Success: TERM++ open requested\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "SELECT_INSTANCE") == 0) {
        if (!world) { printf("[MCP] Error: no world loaded\n"); fflush(stdout); return; }
        int idx = -1; int add = 0;
        if (sscanf(line, "%*s %d %d", &idx, &add) < 1) {
            printf("[MCP] Error: Usage: SELECT_INSTANCE <idx> [add]\n"); fflush(stdout); return;
        }
        Inst** insts = 0;
        int count = CollectMeshInsts(world, &insts);
        if (idx >= 0 && idx < count) {
            if (!add) ClearSelection();
            SetInstFlags(insts[idx], GetInstFlags(insts[idx]) | INST_SELECTED);
            selected_inst = insts[idx];
            active_mesh = GetInstMesh(insts[idx]);
            printf("[MCP] Success: selected instance %d\n", idx);
        } else {
            printf("[MCP] Error: index %d out of range (0-%d)\n", idx, count-1);
        }
        if (insts) free(insts);
        fflush(stdout);
    }
    else if (strcmp(cmd, "CLEAR_SELECTION") == 0) {
        if (!world) { printf("[MCP] Error: no world loaded\n"); fflush(stdout); return; }
        ClearSelection();
        selected_inst = 0;
        printf("[MCP] Success: selection cleared\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "GET_SELECTED") == 0) {
        if (!world) { printf("[MCP] Error: no world loaded\n"); fflush(stdout); return; }
        Inst** insts = 0;
        int count = CollectMeshInsts(world, &insts);
        int sel_count = 0;
        for (int i = 0; i < count; i++) {
            if (GetInstFlags(insts[i]) & INST_SELECTED) {
                double tm[16]; float x=0,y=0,z=0;
                if (GetInstTM(insts[i], tm)) { x=(float)tm[12]; y=(float)tm[13]; z=(float)tm[14]; }
                const char* name = GetInstName(insts[i]);
                printf("  %d\t%s\t%.2f,%.2f,%.2f\n", i, name?name:"???", x, y, z);
                sel_count++;
            }
        }
        printf("[MCP] Selected: %d of %d\n", sel_count, count);
        if (insts) free(insts);
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_MESH_FOOTPRINTS") == 0) {
        // QUERY_MESH_FOOTPRINTS cx cy gw gh scale min_size
        float cx = 0, cy = 0, scale = 16.0f, min_size = 16.0f;
        int gw = 48, gh = 24;
        char* arg = line + strlen("QUERY_MESH_FOOTPRINTS");
        sscanf(arg, "%f %f %d %d %f %f", &cx, &cy, &gw, &gh, &scale, &min_size);
        if (gw < 1) gw = 1; if (gw > 256) gw = 256;
        if (gh < 1) gh = 1; if (gh > 256) gh = 256;
        if (scale < 1.0f) scale = 1.0f;

        Inst** insts = 0;
        int count = world ? CollectMeshInsts(world, &insts) : 0;

        float vx0 = cx - (gw / 2) * scale;
        float vx1 = cx + (gw / 2) * scale;
        float vy0 = cy - (gh / 2) * scale;
        float vy1 = cy + (gh / 2) * scale;

        int qual_count = 0;
        for (int i = 0; i < count; i++) {
            double bbox[6];
            GetInstBBox(insts[i], bbox);
            float x_span = (float)(bbox[1] - bbox[0]);
            float y_span = (float)(bbox[3] - bbox[2]);
            if (x_span <= 0 || y_span <= 0) continue;
            if (x_span < min_size && y_span < min_size) continue;
            if ((float)bbox[1] < vx0 || (float)bbox[0] > vx1) continue;
            if ((float)bbox[3] < vy0 || (float)bbox[2] > vy1) continue;
            qual_count++;
        }

        printf("[MCP] [MESH_FOOTPRINTS_START] count=%d cx=%.2f cy=%.2f scale=%.2f min_size=%.2f\n",
               qual_count, cx, cy, scale, min_size);

        for (int i = 0; i < count; i++) {
            double bbox[6];
            GetInstBBox(insts[i], bbox);
            float x_span = (float)(bbox[1] - bbox[0]);
            float y_span = (float)(bbox[3] - bbox[2]);
            if (x_span <= 0 || y_span <= 0) continue;
            if (x_span < min_size && y_span < min_size) continue;
            if ((float)bbox[1] < vx0 || (float)bbox[0] > vx1) continue;
            if ((float)bbox[3] < vy0 || (float)bbox[2] > vy1) continue;

            const char* name = GetInstName(insts[i]);
            printf("[MCP] %s %.2f %.2f %.2f %.2f\n",
                   name ? name : "unnamed",
                   bbox[0], bbox[1], bbox[2], bbox[3]);
        }

        printf("[MCP] [MESH_FOOTPRINTS_END]\n");
        if (insts) free(insts);
        fflush(stdout);
    }
    else if (strcmp(cmd, "MERGE_OPEN") == 0) {
        char* arg = line + strlen("MERGE_OPEN");
        while (*arg == ' ') arg++;
        char* nl = strchr(arg, '\n');
        if (nl) *nl = 0;
        nl = strchr(arg, '\r');
        if (nl) *nl = 0;

        if (!arg[0]) {
            printf("[MCP] Error: MERGE_OPEN requires a path argument\n");
            fflush(stdout);
        } else if (merge._terrain || merge._world) {
            printf("[MCP] Error: Merge already open, MERGE_COMMIT or MERGE_CANCEL first\n");
            fflush(stdout);
        } else {
            char path[4096];
            snprintf(path, sizeof(path), "%s", arg);

            // Pre-check: verify file exists before calling MergeOpen
            // (MergeOpen creates empty terrain/world on failure for GUI use)
            FILE* check = fopen(path, "rb");
            if (!check) {
                printf("[MCP] Error: Cannot open file: %s\n", path);
                fflush(stdout);
            } else {
                fclose(check);
                MergeOpen(path);

                // Count meshes in merge world
                int mesh_count = 0;
                if (merge._world) {
                    for (Mesh* m = GetFirstMesh(merge._world); m; m = GetNextMesh(m))
                        mesh_count++;
                }

                printf("[MCP] Merge opened: %s terrain=%s meshes=%d\n",
                       path,
                       merge._terrain ? "yes" : "no",
                       mesh_count);
                fflush(stdout);
            }
        }
    }
    else if (strcmp(cmd, "MERGE_COMMIT") == 0) {
        if (!merge._terrain && !merge._world) {
            printf("[MCP] Error: No merge open, use MERGE_OPEN first\n");
            fflush(stdout);
        } else {
            merge.patches_merged = 0;
            merge.instances_added = 0;
            MergeCommit();
            int dx = merge.dx;
            int dy = merge.dy;
            int patches = merge.patches_merged;
            int instances = merge.instances_added;
            MergeCancel();
            printf("[MCP] Merge committed: patches=%d instances=%d offset=%d,%d\n",
                   patches, instances, dx, dy);
            fflush(stdout);
        }
    }
    else if (strcmp(cmd, "MERGE_CANCEL") == 0) {
        if (!merge._terrain && !merge._world) {
            printf("[MCP] Error: No merge open\n");
            fflush(stdout);
        } else {
            MergeCancel();
            printf("[MCP] Merge cancelled\n");
            fflush(stdout);
        }
    }
    else if (strcmp(cmd, "LOAD_MAP") == 0) {
        char path[4096] = "";
        char* arg = line + strlen("LOAD_MAP");
        while (*arg == ' ') arg++;
        // trim newline
        char* nl = strchr(arg, '\n');
        if (nl) *nl = 0;
        nl = strchr(arg, '\r');
        if (nl) *nl = 0;

        if (arg[0]) {
            snprintf(path, sizeof(path), "%s", arg);
        } else {
            snprintf(path, sizeof(path), "%sassets/a3d/game_map_y8.a3d", base_path);
        }

	        if (!LoadMapForSession(path, "[MCP]")) {
	            return;
	        }
	        inst_list_dirty = true;

        int inst_count = 0;
        if (world) {
            Inst** insts = 0;
            inst_count = CollectMeshInsts(world, &insts);
            if (insts) free(insts);
        }
        inst_list_dirty = true;
        printf("[MCP] Map loaded: terrain=%s world=%s instances=%d\n",
               terrain ? "yes" : "no",
               world ? "yes" : "no",
               inst_count);

        // Try to load OSM projection params from terrain_metadata.json
        // next to the loaded map (same directory).
        {
            g_osm_proj.valid = false;
            char meta_path[kEditorPathMax];
            const char* slash = strrchr(g_current_map_path, '/');
            if (slash) {
                int dir_len = (int)(slash - g_current_map_path);
                snprintf(meta_path, sizeof(meta_path), "%.*s/terrain_metadata.json", dir_len, g_current_map_path);
            } else {
                snprintf(meta_path, sizeof(meta_path), "terrain_metadata.json");
            }
            FILE* mf = fopen(meta_path, "r");
            if (mf) {
                char buf[4096];
                size_t n = fread(buf, 1, sizeof(buf)-1, mf);
                buf[n] = 0;
                fclose(mf);
                // Simple key extraction (no JSON library needed)
                auto grab = [&](const char* key) -> double {
                    char pat[64]; snprintf(pat, sizeof(pat), "\"%s\":", key);
                    const char* p = strstr(buf, pat);
                    if (!p) return 0.0;
                    p += strlen(pat);
                    while (*p == ' ' || *p == '\t') p++;
                    return atof(p);
                };
                g_osm_proj.scene_lat = grab("scene_lat");
                g_osm_proj.scene_lon = grab("scene_lon");
                g_osm_proj.content_scale = grab("content_scale");
                // terrain_shift is nested — search for "x" after "terrain_shift"
                const char* ts = strstr(buf, "\"terrain_shift\"");
                if (ts) {
                    const char* tx = strstr(ts, "\"x\":");
                    const char* ty = strstr(ts, "\"y\":");
                    if (tx) { tx += 4; while(*tx==' '||*tx=='\t') tx++; g_osm_proj.shift_x = atof(tx); }
                    if (ty) { ty += 4; while(*ty==' '||*ty=='\t') ty++; g_osm_proj.shift_y = atof(ty); }
                }
                g_osm_proj.cal_x = grab("calibration_offset_x");
                g_osm_proj.cal_y = grab("calibration_offset_y");
                g_osm_proj.valid = (g_osm_proj.content_scale > 0 && g_osm_proj.scene_lat != 0);
                if (g_osm_proj.valid)
                    printf("[MCP] OSM projection loaded: scene=(%.7f,%.7f) scale=%.1f shift=(%.0f,%.0f) cal=(%.1f,%.1f)\n",
                        g_osm_proj.scene_lat, g_osm_proj.scene_lon, g_osm_proj.content_scale,
                        g_osm_proj.shift_x, g_osm_proj.shift_y, g_osm_proj.cal_x, g_osm_proj.cal_y);
            }
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SAVE") == 0 || strcmp(cmd, "SAVE_MAP") == 0) {
        const char* cmd_name = strcmp(cmd, "SAVE") == 0 ? "SAVE" : "SAVE_MAP";
        char path[kEditorPathMax] = "";
        char* arg = line + strlen(cmd_name);
        while (*arg == ' ') arg++;
        char* nl = strchr(arg, '\n');
        if (nl) *nl = 0;
        nl = strchr(arg, '\r');
        if (nl) *nl = 0;

        if (!arg[0]) {
            printf("[MCP] Error: Invalid %s args. Usage: %s <path>\n", cmd_name, cmd_name);
            fflush(stdout);
            return;
        }

        snprintf(path, sizeof(path), "%s", arg);
        printf("[MCP] Saving map: %s\n", path);
        fflush(stdout);

        if (!SaveMapToPath(path)) {
            printf("[MCP] Error: Save failed for %s (%s)\n",
                   path,
                   g_last_save_map_error[0] ? g_last_save_map_error : "unknown error");
            fflush(stdout);
            return;
        }

        printf("[MCP] Map saved: %s\n", path);
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_WEATHER") == 0)
    {
        int state;
        if (sscanf(line, "%*s %d", &state) == 1 && state >= 0 && state <= 3)
        {
            if (!weather) CreateWeather();
            SetWeather(state);
            printf("[MCP] OK weather=%d\n", state);
        }
        else
        {
            printf("[MCP] ERR SET_WEATHER <0-3>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "GET_WEATHER") == 0)
    {
        printf("[MCP] weather=%d intensity=%.2f\n",
               GetWeather(),
               weather ? weather->intensity : 0.0f);
        fflush(stdout);
    }
    // ── Marker commands ────────────────────────────────────────────
    else if (strcmp(cmd, "PLACE_MARKER") == 0)
    {
        float x, y, z;
        if (sscanf(line, "%*s %f %f %f", &x, &y, &z) == 3)
        {
            Marker* mk = (Marker*)malloc(sizeof(Marker));
            mk->pos[0] = x; mk->pos[1] = y; mk->pos[2] = z;
            mk->id = marker_next_id++;
            mk->next = 0;
            mk->prev = marker_tail;
            if (marker_tail) marker_tail->next = mk;
            else marker_head = mk;
            marker_tail = mk;
            printf("[MCP] Success: Marker %d at %.2f,%.2f,%.2f\n", mk->id, x, y, z);
        }
        else
        {
            printf("[MCP] Error: Usage: PLACE_MARKER <x> <y> <z>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "LIST_MARKERS") == 0)
    {
        int count = 0;
        Marker* mk = marker_head;
        while (mk)
        {
            printf("[MCP] Marker %d: %.2f,%.2f,%.2f\n", mk->id, mk->pos[0], mk->pos[1], mk->pos[2]);
            count++;
            mk = mk->next;
        }
        printf("[MCP] Total Markers: %d\n", count);
        fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_MARKER") == 0)
    {
        int id = 0;
        if (sscanf(line, "%*s %d", &id) == 1)
        {
            Marker* mk = marker_head;
            while (mk && mk->id != id) mk = mk->next;
            if (mk)
            {
                if (mk->prev) mk->prev->next = mk->next;
                else marker_head = mk->next;
                if (mk->next) mk->next->prev = mk->prev;
                else marker_tail = mk->prev;
                free(mk);
                printf("[MCP] Success: Deleted marker %d\n", id);
            }
            else
            {
                printf("[MCP] Error: Marker %d not found\n", id);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: DELETE_MARKER <id>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "CLEAR_MARKERS") == 0)
    {
        int count = 0;
        Marker* mk = marker_head;
        while (mk)
        {
            Marker* next = mk->next;
            free(mk);
            mk = next;
            count++;
        }
        marker_head = 0;
        marker_tail = 0;
        printf("[MCP] Success: Cleared %d markers\n", count);
        fflush(stdout);
    }
    // ── Terrain cell commands ──────────────────────────────────────
    else if (strcmp(cmd, "SET_CELL_MATERIAL") == 0)
    {
        int cell_x, cell_y, mat_id;
        if (sscanf(line, "%*s %d %d %d", &cell_x, &cell_y, &mat_id) == 3 && terrain)
        {
            int px = (int)floor((double)cell_x / VISUAL_CELLS);
            int py = (int)floor((double)cell_y / VISUAL_CELLS);
            Patch* p = GetTerrainPatch(terrain, px, py);
            if (p)
            {
                int lx = ((cell_x % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int ly = ((cell_y % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int vi = lx + ly * VISUAL_CELLS;
                uint16_t* visual = GetTerrainVisualMap(p);
                int old_mat = visual[vi] & 0xFF;
                URDO_Open();
                URDO_Patch(p, true);
                visual[vi] = (visual[vi] & ~0xFF) | (mat_id & 0xFF);
                UpdateTerrainVisualMap(p);
                URDO_Close();
                printf("[MCP] Success: cell %d,%d mat %d->%d\n", cell_x, cell_y, old_mat, mat_id & 0xFF);
            }
            else
            {
                printf("[MCP] Error: No patch at cell %d,%d\n", cell_x, cell_y);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: SET_CELL_MATERIAL <cell_x> <cell_y> <mat_id>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "GET_CELL_VISUAL") == 0)
    {
        int cell_x, cell_y;
        if (sscanf(line, "%*s %d %d", &cell_x, &cell_y) == 2 && terrain)
        {
            int px = (int)floor((double)cell_x / VISUAL_CELLS);
            int py = (int)floor((double)cell_y / VISUAL_CELLS);
            Patch* p = GetTerrainPatch(terrain, px, py);
            if (p)
            {
                int lx = ((cell_x % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int ly = ((cell_y % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int vi = lx + ly * VISUAL_CELLS;
                uint16_t* visual = GetTerrainVisualMap(p);
                int mat = visual[vi] & 0xFF;

                double fx = (lx + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
                double fy = (ly + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
                int hx = (int)round(fx);
                int hy = (int)round(fy);
                if (hx > HEIGHT_CELLS) hx = HEIGHT_CELLS;
                if (hy > HEIGHT_CELLS) hy = HEIGHT_CELLS;
                int hi = hx + hy * (HEIGHT_CELLS + 1);
                uint16_t* height = GetTerrainHeightMap(p);

                printf("[MCP] Cell %d,%d: mat=%d height=%d\n", cell_x, cell_y, mat, height[hi]);
            }
            else
            {
                printf("[MCP] Error: No patch at cell %d,%d\n", cell_x, cell_y);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: GET_CELL_VISUAL <cell_x> <cell_y>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "BATCH_SET_CELLS") == 0)
    {
        int mat_id, n;
        char* arg = line + strlen("BATCH_SET_CELLS");
        if (sscanf(arg, " %d %d", &mat_id, &n) == 2 && terrain && n > 0 && n <= 10000)
        {
            // Advance past mat_id and n
            char* p = arg;
            for (int skip = 0; skip < 2; skip++) {
                while (*p == ' ') p++;
                while (*p && *p != ' ') p++;
            }

            URDO_Open();
            int count = 0;
            const int MAX_PATCHES = 4096;
            Patch* snapped[MAX_PATCHES];
            int snap_count = 0;
            bool overflow = false;

            for (int i = 0; i < n; i++)
            {
                int cx, cy;
                if (sscanf(p, " %d %d", &cx, &cy) != 2) break;
                for (int skip = 0; skip < 2; skip++) {
                    while (*p == ' ') p++;
                    while (*p && *p != ' ') p++;
                }

                int px = (int)floor((double)cx / VISUAL_CELLS);
                int py = (int)floor((double)cy / VISUAL_CELLS);
                Patch* patch = GetTerrainPatch(terrain, px, py);
                if (!patch) continue;

                bool already = false;
                for (int s = 0; s < snap_count; s++) {
                    if (snapped[s] == patch) { already = true; break; }
                }
                if (!already) {
                    if (snap_count < MAX_PATCHES) {
                        URDO_Patch(patch, true);
                        snapped[snap_count++] = patch;
                    } else {
                        overflow = true;
                    }
                }

                int lx = ((cx % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int ly = ((cy % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int vi = lx + ly * VISUAL_CELLS;
                uint16_t* visual = GetTerrainVisualMap(patch);
                visual[vi] = (visual[vi] & ~0xFF) | (mat_id & 0xFF);
                count++;
            }

            for (int s = 0; s < snap_count; s++)
                UpdateTerrainVisualMap(snapped[s]);
            URDO_Close();

            if (overflow)
                printf("[MCP] Warning: patch limit exceeded, some patches may lack undo support\n");
            printf("[MCP] Success: set %d cells to mat %d\n", count, mat_id & 0xFF);
        }
        else
        {
            printf("[MCP] Error: Usage: BATCH_SET_CELLS <mat_id> <N> <x1> <y1> ... <xN> <yN>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "BATCH_ELEV_DELTA") == 0)
    {
        int delta_h, n;
        char* arg = line + strlen("BATCH_ELEV_DELTA");
        if (sscanf(arg, " %d %d", &delta_h, &n) == 2 && terrain && n > 0 && n <= 10000)
        {
            char* p = arg;
            for (int skip = 0; skip < 2; skip++) {
                while (*p == ' ') p++;
                while (*p && *p != ' ') p++;
            }

            URDO_Open();
            const int MAX_PATCHES = 4096;
            Patch* snapped[MAX_PATCHES];
            int snap_count = 0;
            bool overflow = false;

            struct HV { Patch* patch; int hi; };
            const int MAX_HV = 10000;
            HV hvs[MAX_HV];
            int hv_count = 0;

            for (int i = 0; i < n; i++)
            {
                int cx, cy;
                if (sscanf(p, " %d %d", &cx, &cy) != 2) break;
                for (int skip = 0; skip < 2; skip++) {
                    while (*p == ' ') p++;
                    while (*p && *p != ' ') p++;
                }

                int px = (int)floor((double)cx / VISUAL_CELLS);
                int py = (int)floor((double)cy / VISUAL_CELLS);
                Patch* patch = GetTerrainPatch(terrain, px, py);
                if (!patch) continue;

                int lx = ((cx % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                int ly = ((cy % VISUAL_CELLS) + VISUAL_CELLS) % VISUAL_CELLS;
                double fx = (lx + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
                double fy = (ly + 0.5) * (double)HEIGHT_CELLS / (double)VISUAL_CELLS;
                int hx = (int)round(fx);
                int hy = (int)round(fy);
                if (hx > HEIGHT_CELLS) hx = HEIGHT_CELLS;
                if (hy > HEIGHT_CELLS) hy = HEIGHT_CELLS;
                int hi = hx + hy * (HEIGHT_CELLS + 1);

                bool dup = false;
                for (int d = 0; d < hv_count; d++) {
                    if (hvs[d].patch == patch && hvs[d].hi == hi) { dup = true; break; }
                }
                if (dup || hv_count >= MAX_HV) continue;
                hvs[hv_count++] = { patch, hi };

                bool already = false;
                for (int s = 0; s < snap_count; s++) {
                    if (snapped[s] == patch) { already = true; break; }
                }
                if (!already) {
                    if (snap_count < MAX_PATCHES) {
                        URDO_Patch(patch, false);
                        snapped[snap_count++] = patch;
                    } else {
                        overflow = true;
                    }
                }
            }

            for (int i = 0; i < hv_count; i++)
            {
                uint16_t* height = GetTerrainHeightMap(hvs[i].patch);
                int val = (int)height[hvs[i].hi] + delta_h;
                if (val < 0) val = 0;
                if (val > 65535) val = 65535;
                height[hvs[i].hi] = (uint16_t)val;
            }

            for (int s = 0; s < snap_count; s++)
                UpdateTerrainHeightMap(snapped[s]);
            URDO_Close();

            if (overflow)
                printf("[MCP] Warning: patch limit exceeded, some patches may lack undo support\n");
            printf("[MCP] Success: elevated %d vertices by %d\n", hv_count, delta_h);
        }
        else
        {
            printf("[MCP] Error: Usage: BATCH_ELEV_DELTA <delta_h> <N> <x1> <y1> ... <xN> <yN>\n");
        }
        fflush(stdout);
    }
    // ── Brush sculpting commands ────────────────────────────────────
    else if (strcmp(cmd, "STAMP") == 0)
    {
        // STAMP <x> <y> <radius> <alpha> <shape>
        // x,y = world-space center (float, same units as camera/PROBE_TERRAIN)
        // radius = brush radius in world units (float, 5-100)
        // alpha = intensity (-0.5 to 0.5, positive=raise, negative=lower)
        // shape = 0=Gaussian, 1=Square, 2=Noise
        double sx, sy, sradius, salpha;
        int sshape;
        if (sscanf(line, "%*s %lf %lf %lf %lf %d", &sx, &sy, &sradius, &salpha, &sshape) == 5)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (sradius < 1.0 || sradius > 200.0)
            {
                printf("[MCP] Error: radius must be 1-200, got %.1f\n", sradius);
            }
            else if (salpha < -0.5 || salpha > 0.5)
            {
                printf("[MCP] Error: alpha must be -0.5 to 0.5, got %.3f\n", salpha);
            }
            else if (sshape < 0 || sshape > 2)
            {
                printf("[MCP] Error: shape must be 0-2 (0=Gaussian,1=Square,2=Noise), got %d\n", sshape);
            }
            else
            {
                // Save globals, apply, restore
                float old_alpha = br_alpha, old_radius = br_radius;
                int old_shape = brush_shape;
                bool old_limit = br_limit;

                br_alpha = (float)salpha;
                br_radius = (float)sradius;
                brush_shape = sshape;
                br_limit = false;

                Stamp(sx, sy, 1); // mode 1 = direct stamp

                br_alpha = old_alpha;
                br_radius = old_radius;
                brush_shape = old_shape;
                br_limit = old_limit;

                printf("[MCP] Success: stamped at %.2f,%.2f radius=%.1f alpha=%.3f shape=%d\n",
                       sx, sy, sradius, salpha, sshape);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: STAMP <x> <y> <radius> <alpha> <shape>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "BLUR_TERRAIN") == 0)
    {
        // BLUR_TERRAIN <x> <y> <radius> <strength>
        // Separable Gaussian blur centered at world-space (x,y)
        // strength = 0.0-1.0 blend toward smoothed height
        double bx, by, bradius, bstrength;
        if (sscanf(line, "%*s %lf %lf %lf %lf", &bx, &by, &bradius, &bstrength) == 4)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (bradius < 1.0 || bradius > 200.0)
            {
                printf("[MCP] Error: radius must be 1-200, got %.1f\n", bradius);
            }
            else if (bstrength < 0.0 || bstrength > 1.0)
            {
                printf("[MCP] Error: strength must be 0.0-1.0, got %.3f\n", bstrength);
            }
            else
            {
                float old_alpha = br_alpha, old_radius = br_radius;
                bool old_limit = br_limit;

                br_alpha = (float)bstrength;
                br_radius = (float)bradius;
                br_limit = false;

                Stamp(bx, by, 2); // mode 2 = blur/sharpen (positive alpha = blur)

                br_alpha = old_alpha;
                br_radius = old_radius;
                br_limit = old_limit;

                printf("[MCP] Success: blurred at %.2f,%.2f radius=%.1f strength=%.3f\n",
                       bx, by, bradius, bstrength);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: BLUR_TERRAIN <x> <y> <radius> <strength>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SHARPEN_TERRAIN") == 0)
    {
        // SHARPEN_TERRAIN <x> <y> <radius> <strength>
        // Amplify height deviation from smoothed Gaussian at world-space (x,y)
        // strength = 0.0-1.0 sharpening intensity
        double shx, shy, shradius, shstrength;
        if (sscanf(line, "%*s %lf %lf %lf %lf", &shx, &shy, &shradius, &shstrength) == 4)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (shradius < 1.0 || shradius > 200.0)
            {
                printf("[MCP] Error: radius must be 1-200, got %.1f\n", shradius);
            }
            else if (shstrength < 0.0 || shstrength > 1.0)
            {
                printf("[MCP] Error: strength must be 0.0-1.0, got %.3f\n", shstrength);
            }
            else
            {
                float old_alpha = br_alpha, old_radius = br_radius;
                bool old_limit = br_limit;

                br_alpha = -(float)shstrength; // negative alpha = sharpen
                br_radius = (float)shradius;
                br_limit = false;

                Stamp(shx, shy, 2); // mode 2 = blur/sharpen

                br_alpha = old_alpha;
                br_radius = old_radius;
                br_limit = old_limit;

                printf("[MCP] Success: sharpened at %.2f,%.2f radius=%.1f strength=%.3f\n",
                       shx, shy, shradius, shstrength);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: SHARPEN_TERRAIN <x> <y> <radius> <strength>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SET_HEIGHT_CELL") == 0)
    {
        // SET_HEIGHT_CELL <px> <py> <hx> <hy> <height>
        // px,py = patch coordinates (same as GetTerrainPatch)
        // hx,hy = height-vertex within patch (0 to HEIGHT_CELLS inclusive)
        // height = absolute height value (0-65535)
        int shc_px, shc_py, shc_hx, shc_hy, shc_h;
        if (sscanf(line, "%*s %d %d %d %d %d", &shc_px, &shc_py, &shc_hx, &shc_hy, &shc_h) == 5)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (shc_hx < 0 || shc_hx > HEIGHT_CELLS || shc_hy < 0 || shc_hy > HEIGHT_CELLS)
            {
                printf("[MCP] Error: hx,hy must be 0-%d, got %d,%d\n", HEIGHT_CELLS, shc_hx, shc_hy);
            }
            else if (shc_h < 0 || shc_h > 0xFFFF)
            {
                printf("[MCP] Error: height must be 0-65535, got %d\n", shc_h);
            }
            else
            {
                Patch* p = GetTerrainPatch(terrain, shc_px, shc_py);
                if (!p)
                {
                    printf("[MCP] Error: No patch at %d,%d\n", shc_px, shc_py);
                }
                else
                {
                    URDO_Patch(p);
                    uint16_t* map = GetTerrainHeightMap(p);
                    int idx = shc_hx + shc_hy * (HEIGHT_CELLS + 1);
                    uint16_t old_h = map[idx];
                    map[idx] = (uint16_t)shc_h;
                    UpdateTerrainHeightMap(p);
                    printf("[MCP] Success: set height at patch %d,%d cell %d,%d from %d to %d\n",
                           shc_px, shc_py, shc_hx, shc_hy, old_h, shc_h);
                }
            }
        }
        else
        {
            printf("[MCP] Error: Usage: SET_HEIGHT_CELL <px> <py> <hx> <hy> <height>\n");
        }
        fflush(stdout);
    }
    // ── Instance commands ──────────────────────────────────────────
    else if (strcmp(cmd, "MOVE_INSTANCE") == 0)
    {
        int idx;
        float dx, dy, dz;
        if (sscanf(line, "%*s %d %f %f %f", &idx, &dx, &dy, &dz) == 4 && world)
        {
            Inst** insts = 0;
            int count = CollectMeshInsts(world, &insts);
            if (idx >= 0 && idx < count)
            {
                double tm[16];
                if (GetInstTM(insts[idx], tm))
                {
                    DetachInst(world, insts[idx]);
                    tm[12] += dx; tm[13] += dy; tm[14] += dz;
                    SetInstTM(insts[idx], tm);
                    AttachInst(world, insts[idx]);
                    printf("[MCP] Success: moved inst %d to %.2f,%.2f,%.2f\n",
                           idx, (float)tm[12], (float)tm[13], (float)tm[14]);
                }
                else
                {
                    printf("[MCP] Error: Cannot read transform for inst %d\n", idx);
                }
            }
            else
            {
                printf("[MCP] Error: Instance index %d out of range (0-%d)\n", idx, count - 1);
            }
            if (insts) free(insts);
        }
        else
        {
            printf("[MCP] Error: Usage: MOVE_INSTANCE <idx> <dx> <dy> <dz>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "SCALE_INSTANCE") == 0)
    {
        int idx;
        float sx, sy, sz;
        if (sscanf(line, "%*s %d %f %f %f", &idx, &sx, &sy, &sz) == 4 && world)
        {
            Inst** insts = 0;
            int count = CollectMeshInsts(world, &insts);
            if (idx >= 0 && idx < count)
            {
                double tm[16];
                if (GetInstTM(insts[idx], tm))
                {
                    DetachInst(world, insts[idx]);
                    // Scale columns 0-2 of the 4x4 matrix
                    tm[0] *= sx; tm[1] *= sx; tm[2] *= sx;
                    tm[4] *= sy; tm[5] *= sy; tm[6] *= sy;
                    tm[8] *= sz; tm[9] *= sz; tm[10] *= sz;
                    SetInstTM(insts[idx], tm);
                    AttachInst(world, insts[idx]);
                    printf("[MCP] Success: scaled inst %d\n", idx);
                }
                else
                {
                    printf("[MCP] Error: Cannot read transform for inst %d\n", idx);
                }
            }
            else
            {
                printf("[MCP] Error: Instance index %d out of range (0-%d)\n", idx, count - 1);
            }
            if (insts) free(insts);
        }
        else
        {
            printf("[MCP] Error: Usage: SCALE_INSTANCE <idx> <sx> <sy> <sz>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_INSTANCE") == 0)
    {
        int idx;
        if (sscanf(line, "%*s %d", &idx) == 1 && world)
        {
            Inst** insts = 0;
            int count = CollectMeshInsts(world, &insts);
            if (idx >= 0 && idx < count)
            {
                URDO_Open();
                URDO_Delete(insts[idx]);
                URDO_Close();
                printf("[MCP] Success: deleted inst %d\n", idx);
            }
            else
            {
                printf("[MCP] Error: Instance index %d out of range (0-%d)\n", idx, count - 1);
            }
            if (insts) free(insts);
        }
        else
        {
            printf("[MCP] Error: Usage: DELETE_INSTANCE <idx>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_MESH_FOOTPRINTS") == 0) {
        // QUERY_MESH_FOOTPRINTS cx cy gw gh scale min_size
        float cx = 0, cy = 0, scale = 16.0f, min_size = 16.0f;
        int gw = 48, gh = 24;
        char* arg = line + strlen("QUERY_MESH_FOOTPRINTS");
        sscanf(arg, "%f %f %d %d %f %f", &cx, &cy, &gw, &gh, &scale, &min_size);
        if (gw < 1) gw = 1; if (gw > 256) gw = 256;
        if (gh < 1) gh = 1; if (gh > 256) gh = 256;
        if (scale < 1.0f) scale = 1.0f;

        Inst** insts = 0;
        int count = world ? CollectMeshInsts(world, &insts) : 0;

        float vx0 = cx - (gw / 2) * scale;
        float vx1 = cx + (gw / 2) * scale;
        float vy0 = cy - (gh / 2) * scale;
        float vy1 = cy + (gh / 2) * scale;

        int qual_count = 0;
        for (int i = 0; i < count; i++) {
            double bbox[6];
            GetInstBBox(insts[i], bbox);
            float x_span = (float)(bbox[1] - bbox[0]);
            float y_span = (float)(bbox[3] - bbox[2]);
            if (x_span <= 0 || y_span <= 0) continue;
            if (x_span < min_size && y_span < min_size) continue;
            if ((float)bbox[1] < vx0 || (float)bbox[0] > vx1) continue;
            if ((float)bbox[3] < vy0 || (float)bbox[2] > vy1) continue;
            qual_count++;
        }

        printf("[MCP] [MESH_FOOTPRINTS_START] count=%d cx=%.2f cy=%.2f scale=%.2f min_size=%.2f\n",
               qual_count, cx, cy, scale, min_size);

        for (int i = 0; i < count; i++) {
            double bbox[6];
            GetInstBBox(insts[i], bbox);
            float x_span = (float)(bbox[1] - bbox[0]);
            float y_span = (float)(bbox[3] - bbox[2]);
            if (x_span <= 0 || y_span <= 0) continue;
            if (x_span < min_size && y_span < min_size) continue;
            if ((float)bbox[1] < vx0 || (float)bbox[0] > vx1) continue;
            if ((float)bbox[3] < vy0 || (float)bbox[2] > vy1) continue;

            const char* name = GetInstName(insts[i]);
            printf("[MCP] %s %.2f %.2f %.2f %.2f\n",
                   name ? name : "unnamed",
                   bbox[0], bbox[1], bbox[2], bbox[3]);
        }

        printf("[MCP] [MESH_FOOTPRINTS_END]\n");
        if (insts) free(insts);
        fflush(stdout);
    }
    // ── Undo/Redo commands ─────────────────────────────────────────
    else if (strcmp(cmd, "UNDO") == 0)
    {
        if (URDO_CanUndo())
        {
            URDO_Undo(1); inst_list_dirty = true;
            printf("[MCP] Success: undone\n");
        }
        else
        {
            printf("[MCP] Nothing to undo\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "REDO") == 0)
    {
        if (URDO_CanRedo())
        {
            URDO_Redo(1); inst_list_dirty = true;
            printf("[MCP] Success: redone\n");
        }
        else
        {
            printf("[MCP] Nothing to redo\n");
        }
        fflush(stdout);
    }
    // ── Enemy Generator commands ──────────────────────────────────────
    else if (strcmp(cmd, "PLACE_ENEMYGEN") == 0)
    {
        float x, y, z;
        int alive = eg_alive_max, rmin = eg_revive_min, rmax = eg_revive_max;
        int arm = eg_armor, helm = eg_helmet, shld = eg_shield;
        int swd = eg_sword, xbow = eg_crossbow;

        int parsed = sscanf(line, "%*s %f %f %f %d %d %d %d %d %d %d %d",
                            &x, &y, &z, &alive, &rmin, &rmax, &arm, &helm, &shld, &swd, &xbow);
        if (parsed >= 3)
        {
            // Clamp values to valid ranges
            if (alive < 1) alive = 1; if (alive > 7) alive = 7;
            if (rmin < 0) rmin = 0; if (rmin > 10) rmin = 10;
            if (rmax < rmin) rmax = rmin; if (rmax > 10) rmax = 10;
            if (arm < 0) arm = 0; if (arm > 10) arm = 10;
            if (helm < 0) helm = 0; if (helm > 10) helm = 10;
            if (shld < 0) shld = 0; if (shld > 10) shld = 10;
            if (swd < 0) swd = 0; if (swd > 10) swd = 10;
            if (xbow < 0) xbow = 0; if (xbow > 10) xbow = 10;

            EnemyGen* eg = (EnemyGen*)malloc(sizeof(EnemyGen));
            eg->pos[0] = x;
            eg->pos[1] = y;
            eg->pos[2] = z;
            eg->alive_max = alive;
            eg->revive_min = rmin;
            eg->revive_max = rmax;
            eg->armor = arm;
            eg->helmet = helm;
            eg->shield = shld;
            eg->sword = swd;
            eg->crossbow = xbow;

            eg->prev = 0;
            eg->next = enemygen_head;
            if (enemygen_head)
                enemygen_head->prev = eg;
            else
                enemygen_tail = eg;
            enemygen_head = eg;

            printf("[MCP] Success: placed enemygen at %.1f,%.1f,%.1f alive_max=%d\n",
                   x, y, z, alive);
        }
        else
        {
            printf("[MCP] Error: Usage: PLACE_ENEMYGEN x y z [alive rmin rmax armor helmet shield sword crossbow]\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "LIST_ENEMYGENS") == 0)
    {
        int count = 0;
        printf("[MCP] EnemyGen List:\n");
        for (EnemyGen* eg = enemygen_head; eg; eg = eg->next)
        {
            printf("[MCP]  %d pos=%.1f,%.1f,%.1f alive=%d revive=%d-%d armor=%d helmet=%d shield=%d sword=%d crossbow=%d\n",
                   count, eg->pos[0], eg->pos[1], eg->pos[2],
                   eg->alive_max, eg->revive_min, eg->revive_max,
                   eg->armor, eg->helmet, eg->shield,
                   eg->sword, eg->crossbow);
            count++;
        }
        printf("[MCP] Total EnemyGens: %d\n", count);
        fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_ENEMYGEN") == 0)
    {
        int idx;
        if (sscanf(line, "%*s %d", &idx) == 1)
        {
            int count = 0;
            EnemyGen* target = 0;
            for (EnemyGen* eg = enemygen_head; eg; eg = eg->next, count++)
            {
                if (count == idx) { target = eg; break; }
            }
            if (target)
            {
                DeleteEnemyGen(target);
                printf("[MCP] Success: deleted enemygen %d\n", idx);
            }
            else
            {
                printf("[MCP] Error: EnemyGen index %d out of range\n", idx);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: DELETE_ENEMYGEN <idx>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "DELETE_ALL_ENEMYGENS") == 0)
    {
        DeleteAllEnemyGens();
        printf("[MCP] Success: all enemygens deleted\n");
        fflush(stdout);
    }
    // ── Terrain patch management commands ─────────────────────────────────
    else if (strcmp(cmd, "FLIP_DIAG") == 0)
    {
        // FLIP_DIAG <px> <py> <hx> <hy>
        // px,py = patch coordinates (same as GetTerrainPatch)
        // hx,hy = height-cell coordinates within patch (0 to HEIGHT_CELLS-1)
        int px, py, hx, hy;
        if (sscanf(line, "%*s %d %d %d %d", &px, &py, &hx, &hy) == 4)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (hx < 0 || hx >= HEIGHT_CELLS || hy < 0 || hy >= HEIGHT_CELLS)
            {
                printf("[MCP] Error: hx,hy must be 0-%d, got %d,%d\n", HEIGHT_CELLS - 1, hx, hy);
            }
            else
            {
                Patch* p = GetTerrainPatch(terrain, px, py);
                if (!p)
                {
                    printf("[MCP] Error: No patch at %d,%d\n", px, py);
                }
                else
                {
                    uint16_t diag = GetTerrainDiag(p);
                    diag ^= 1 << (hx + hy * HEIGHT_CELLS);
                    URDO_Diag(p);
                    SetTerrainDiag(p, diag);
                    printf("[MCP] Success: flipped diag at patch %d,%d cell %d,%d diag=0x%04x\n",
                           px, py, hx, hy, diag);
                }
            }
        }
        else
        {
            printf("[MCP] Error: Usage: FLIP_DIAG <px> <py> <hx> <hy>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "ADD_PATCH") == 0)
    {
        // ADD_PATCH <px> <py> [height]
        // px,py = patch coordinates
        // height = initial height value (default 0)
        int px, py, h = 0;
        if (sscanf(line, "%*s %d %d %d", &px, &py, &h) >= 2)
        {
            // Clamp height to uint16 range (terrain height map is uint16_t)
            if (h < 0) h = 0;
            if (h > 0xFFFF) h = 0xFFFF;

            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else if (GetTerrainPatch(terrain, px, py))
            {
                printf("[MCP] Error: Patch already exists at %d,%d\n", px, py);
            }
            else
            {
                Patch* p = URDO_Create(terrain, px, py, h);
                if (p)
                    printf("[MCP] Success: added patch at %d,%d height=%d\n", px, py, h);
                else
                    printf("[MCP] Error: AddTerrainPatch failed at %d,%d\n", px, py);
            }
        }
        else
        {
            printf("[MCP] Error: Usage: ADD_PATCH <px> <py> [height]\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "DEL_PATCH") == 0)
    {
        // DEL_PATCH <px> <py>
        // px,py = patch coordinates
        int px, py;
        if (sscanf(line, "%*s %d %d", &px, &py) == 2)
        {
            if (!terrain)
            {
                printf("[MCP] Error: No terrain\n");
            }
            else
            {
                Patch* p = GetTerrainPatch(terrain, px, py);
                if (!p)
                {
                    printf("[MCP] Error: No patch at %d,%d\n", px, py);
                }
                else
                {
                    URDO_Delete(terrain, p);
                    printf("[MCP] Success: deleted patch at %d,%d\n", px, py);
                }
            }
        }
        else
        {
            printf("[MCP] Error: Usage: DEL_PATCH <px> <py>\n");
        }
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_BUNDLE_SKINS") == 0)
    {
        // Test bundle load and skin discovery for FL-3697/FL-3698 diagnosis.
        uint16_t skin_ids[16];
        int skin_count = GameGetBundleSkinIds(skin_ids, 16);
        printf("[MCP] bundle_skin_count=%d", skin_count);
        for (int i = 0; i < skin_count; i++)
            printf(" skin_id=%u", (unsigned)skin_ids[i]);
        printf("\n");
        fflush(stdout);
    }
    else if (strcmp(cmd, "QUERY_TERM_STATUS") == 0)
    {
        // Report TERM++ skin state for FL-3697/FL-3698.
        printf("[MCP] term_skin_requested_id=%u\n",
            (unsigned)g_term_skin_requested_id);
        fflush(stdout);
    }
    else if (strcmp(cmd, "RUN_TERM_MOVEMENT_PROBE") == 0)
    {
        RunTermMovementProbe();
    }
    else if (strcmp(cmd, "RUN_MOUSE_DRAG_PROBE") == 0)
    {
        RunEditorDragProbe(line);
    }
    else if (strcmp(cmd, "RUN_SDL_MOUSE_DRAG_PROBE") == 0)
    {
        RunSdlEditorDragProbe(line);
    }
}

// WHY single 4100-line main frame loop function:
// Dear ImGui requires NewFrame/EndFrame wrapping ALL UI + editing + rendering
// in a single function call per frame. This architectural constraint, combined
// with file-scoped editor state (static globals), makes splitting my_render()
// into separate functions impractical without major refactoring. The function
// serves as the main event loop for the editor, handling ImGui panels, terrain
// editing, mesh/sprite placement, and 3D rendering all in one frame.
//
// SECTION GUIDE (navigate by searching these markers):
// - "IMGUI PANELS SECTION" (~line 6100-7500): UI panels, file operations
// - "TERRAIN EDITING SECTION" (~line 7500-8500): Brush editing, material paint
// - "MESH/SPRITE PLACEMENT SECTION" (~line 8500-9000): Instance placement
// - "3D RENDERING SECTION" (~line 9000-10200): OpenGL rendering, camera
// [FL-3851] External reference to clean capture directory, set by CAPTURE_CLEAN_FRAME MCP command.
extern char g_mcp_clean_capture_dir[1024];
// [FL-4131] External reference to UI frame capture directory, set by CAPTURE_UI_FRAME MCP command.
extern char g_mcp_ui_capture_dir[1024];
extern A3D_WND* wnd_head;

void my_render(A3D_WND* wnd)
{
	ClampInteractiveFontSize();

    if (g_mcp_mode) {
        // [FL-3851] Drain queued stdin commands in one frame. Processing only
        // one line per render tick lets piped proof scripts stall after a view
        // change when the window has no further events to wake another frame.
        for (int mcp_cmds = 0; mcp_cmds < 64 && IsStdinReady(); mcp_cmds++) {
            char line[1024];
            if (!fgets(line, sizeof(line), stdin))
                break;
            ProcessMCPCommand(line);
            if (g_mcp_clean_capture_dir[0])
                break;
        }
    }

#ifndef _WIN32
    // [FL-3851] CDP server: poll for TCP client commands each frame
    if (g_cdp_port > 0)
        CdpPoll();
#endif

	// [FL-4131] Service deferred OPEN_TERMPP MCP request. Mirrors the
	// on-screen TERM++ button at editor/asciiid.cpp:15923-15927 — opens a
	// child TERM++ window using the live wnd/rot_yaw/pos_* scope so the
	// driver can prove ASCIIID->TERM++ live material/glyph hot-push.
	if (g_open_termpp_requested)
	{
		g_open_termpp_requested = false;
		float pos[3] = { pos_x, pos_y, pos_z };
		ComputeLoadedMapTermSpawn(pos);
		TermOpen(wnd, rot_yaw, pos);
	}

	// Handle sprite reload request (F5 hotkey)
	// [FLOW:PIPELINE] Manual asset refresh for iterative development
	if (reload_sprites_requested)
	{
		reload_sprites_requested = false;

		// Free all current sprites — invalidates ALL sprite pointers
		FreeSprites();

		// Reset pointers that held sprites before free (#226)
		enemygen_sprite = 0;
		item_preview_sprite = 0;

		// Rescan sprite directory and reload using the same startup policy.
		ScanEditorSpriteDirectory(g_startup_viewer_mode);

		// Reset active sprite pointer
		active_sprite = GetFirstSprite(false);

		// Re-load enemygen sprite (same path as startup)
		LoadEnemygenSpriteForEditor();

		printf("[EDITOR] Sprites reloaded (F5)\n");
	}

	StepDeferredOp();
	RenderDeferredOpProgress();
	RenderConfirmDialog();

	ImGuiIO& io = ImGui::GetIO();

	// static bool oldRight = false; // HACK(xylit): prob not a good solution, but works it works :p

	#ifdef MOUSE_QUEUE
	uint64_t fl3714_mouse_queue_t0 = FL3714Now();
	int fl3714_mouse_queue_processed = 0;
	while (mouse_queue_len) // accumulate wheel sequence only
	{
		mouse_queue_len--;
		fl3714_mouse_queue_processed++;

		bool sync = false;

		int x = mouse_queue[0].x;
		int y = mouse_queue[0].y;
		MouseInfo mi = mouse_queue[0].mi;

		if ((mi & 0xF) == MouseInfo::LEAVE)
		{
			sync = true;
			mouse_in = 0;
		}
		else
		{
			if ((mi & 0xF) == MouseInfo::ENTER)
			{
				sync = true;
				mouse_in = 1;
			}

			io.MousePos = ImVec2((float)x, (float)y);

			switch (mi & 0xF)
			{
				case MouseInfo::WHEEL_DN:
					zoom_wheel--;
					io.MouseWheel -= 1.0;
					break;
				case MouseInfo::WHEEL_UP:
					zoom_wheel++;
					io.MouseWheel += 1.0;
					break;

				case MouseInfo::LEFT_DN:
					sync=true;
					io.MouseDown[0] = true;
					break;
				case MouseInfo::LEFT_UP:
					sync=true;
					io.MouseDown[0] = false;
					break;
				case MouseInfo::RIGHT_DN:
					sync=true;
					g_mouse_right_physical = true;
					io.MouseDown[1] = true;
					break;
				case MouseInfo::RIGHT_UP:
					sync=true;
					g_mouse_right_physical = false;
					io.MouseDown[1] = false;
					break;
				case MouseInfo::MIDDLE_DN:
					sync=true;
					g_mouse_middle_physical = true;
					io.MouseDown[2] = true;
					break;
				case MouseInfo::MIDDLE_UP:
					sync=true;
					g_mouse_middle_physical = false;
					io.MouseDown[2] = false;
					break;
			}
		}

		for (int i=0; i<mouse_queue_len; i++)
			mouse_queue[i] = mouse_queue[i+1];

		if (sync)
			break;
	}
	if (g_fl3714_drag_probe_active)
	{
		g_fl3714_drag_probe_frames++;
		g_fl3714_drag_probe_consumed += fl3714_mouse_queue_processed;
		g_fl3714_drag_probe_mouse_queue_us += FL3714Now() - fl3714_mouse_queue_t0;
		const uint64_t fl3714_drag_probe_elapsed_us = FL3714Now() - g_fl3714_drag_probe_start_us;
		if (mouse_queue_len == 0 && (!g_fl3714_drag_probe_require_consumed || g_fl3714_drag_probe_consumed > g_fl3714_drag_probe_queue_start))
		{
			uint64_t total_us = fl3714_drag_probe_elapsed_us;
			printf("[MCP] DragProbeResult: raw_events=%d queue_start=%d consumed=%d frames=%d queue_remaining=%d mouse_queue_ms=%.3f total_ms=%.3f font_size=%.3f overview=%d\n",
				g_fl3714_drag_probe_raw_events,
				g_fl3714_drag_probe_queue_start,
				g_fl3714_drag_probe_consumed,
				g_fl3714_drag_probe_frames,
				mouse_queue_len,
				g_fl3714_drag_probe_mouse_queue_us / 1000.0,
				total_us / 1000.0,
				font_size,
				g_editor_force_exact_terrain ? 0 : 1);
			fflush(stdout);
			FL3714ResetDragProbe();
		}
		else if (g_fl3714_drag_probe_frames > kFL3714DragProbeStaleFrames || fl3714_drag_probe_elapsed_us > kFL3714DragProbeStaleUs)
		{
			uint64_t total_us = fl3714_drag_probe_elapsed_us;
			printf("[MCP] DragProbeResult: error=timeout raw_events=%d queue_start=%d consumed=%d frames=%d queue_remaining=%d mouse_queue_ms=%.3f total_ms=%.3f font_size=%.3f overview=%d\n",
				g_fl3714_drag_probe_raw_events,
				g_fl3714_drag_probe_queue_start,
				g_fl3714_drag_probe_consumed,
				g_fl3714_drag_probe_frames,
				mouse_queue_len,
				g_fl3714_drag_probe_mouse_queue_us / 1000.0,
				total_us / 1000.0,
				font_size,
				g_editor_force_exact_terrain ? 0 : 1);
			fflush(stdout);
			FL3714ResetDragProbe();
		}
	}

	#endif

	io.MouseDown[1] = g_mouse_right_physical;
	io.MouseDown[2] = g_mouse_middle_physical;
	if (io.KeyAlt && g_mouse_right_physical)
	{
		io.MouseDown[1] = false;
		io.MouseDown[2] = true;
	}

	// THINGZ
	const float clear_in[4]={0.45f, 0.55f, 0.60f, 1.00f};
	const float clear_out[4]={0.40f, 0.50f, 0.55f, 0.95f};

	static int last_heap_ops = 0;

	//const float* clear_color = mouse_in ? clear_in : clear_out;
	const float* clear_color = clear_in;

	{
		ImGui_ImplOpenGL3_NewFrame();
		{
			// Setup time step
			ImGuiIO& io = ImGui::GetIO();
			uint64_t current_time = a3dGetTime();
			uint64_t delta = current_time - g_Time;
			io.DeltaTime = delta>0 ? delta / 1000000.0f : FLT_MIN;
			g_Time = current_time;
			// Start the frame
			ImGui::NewFrame();
		}


//		if (pFont)
//			ImGui::PushFont(pFont);

//		ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 0);
//		ImGui::SetNextWindowPos(ImVec2(0,0),ImGuiCond_Always);
		//ImGui::SetNextWindowSizeConstraints(ImVec2(0,0),ImVec2(0,0),Dock::Size,0);
//		ImGui::PopStyleVar();

		struct SpriteWidget
		{
			static void draw_cb(const ImDrawList* parent_list, const ImDrawCmd* cmd)
			{
				SpriteWidget* sw = (SpriteWidget*)cmd->UserCallbackData;
				if (!sw)
					return;

				int vp[4];
				glGetIntegerv(GL_VIEWPORT, vp);

				int sc[4];
				glGetIntegerv(GL_SCISSOR_BOX, sc);

				int vao;
				glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &vao);

				int vbo;
				glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vbo);

				int prg;
				glGetIntegerv(GL_CURRENT_PROGRAM, &prg);

				//bool cull_face;
				//cull_face = glIsEnabled(GL_CULL_FACE);

				//int cull_mode;
				//glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);

				int depth_func;
				glGetIntegerv(GL_DEPTH_FUNC, &depth_func);

				bool depth_test;
				depth_test = glIsEnabled(GL_DEPTH_TEST);

				RenderContext* rc = &render_context;



				// RenderSprite()
				Sprite* s = active_sprite;


				SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(s);
				SpritePrefs defs = {0};

				if (!sp)
					sp = &defs;

				{

					int anim = sp->anim;
					if (anim < 0 || anim >= s->anims)
						anim = 0;

					int time = 0;

					int len = sp->t[0] + sp->t[1] * s->anim[anim].length + sp->t[2] + sp->t[3] * s->anim[anim].length;

					int frame = 0;

					if (len <= 0)
						frame = sp->frame % s->anim[anim].length;
					else
					{
						time = (a3dGetTime() >> 14) /*61.035 FPS*/ % len;

						if (time < sp->t[0])
							frame = 0;
						else
						if (time < sp->t[0] + sp->t[1] * s->anim[anim].length)
							frame = (time - sp->t[0]) / sp->t[1];
						else
						if (time < sp->t[0] + sp->t[1] * s->anim[anim].length + sp->t[2])
							frame = s->anim[anim].length - 1;
						else
							frame = s->anim[anim].length - 1 - (time - sp->t[0] - sp->t[1] * s->anim[anim].length - sp->t[2]) / sp->t[3];

						time++;
					}

					assert(frame >= 0 && frame < s->anim[anim].length);

					int proj = 0;

					float angle = sp->yaw;
					int ang = (int)floor( (angle - rot_yaw) * s->angles / 360.0f + 0.5f);
					ang = ang >= 0 ? ang % s->angles : (ang % s->angles + s->angles) % s->angles;

					int i = frame + ang * s->anim[anim].length;
					if (proj && s->projs>1)
						i += s->anim[anim].length * s->angles;
					Sprite::Frame* f = s->atlas + s->anim[anim].frame_idx[i];

					int view_size[2] = { 16,16 };

					if (view_size[0] > rc->ansi_buf_size[0])
						view_size[0] = rc->ansi_buf_size[0];
					if (view_size[1] > rc->ansi_buf_size[1])
						view_size[1] = rc->ansi_buf_size[1];

					int n = view_size[0] * view_size[1];
					for (int i = 0; i < n; i++)
					{
						AnsiCell* c = rc->ansi_buf + i;
						c->bk = 0xFF;//fast_rand() & 0xFF;
						c->fg = 0xFF;//fast_rand() & 0xFF;
						c->gl = 0xFF;//fast_rand() & 0xFF;
						c->spare = 0xFF;
					}

					int cpy_w = f->width < view_size[0] ? f->width : view_size[0];
					int cpy_h = f->height < view_size[1] ? f->height : view_size[1];

					int dst_x = (view_size[0] - f->width) / 2;
					int dst_y = (view_size[1] - f->height) / 2;

					if (dst_x < 0)
						dst_x = 0;
					if (dst_y < 0)
						dst_y = 0;

					int src_x = (f->width - view_size[0]) / 2;
					int src_y = (f->height - view_size[1]) / 2;

					if (src_x < 0)
						src_x = 0;
					if (src_y < 0)
						src_y = 0;

					for (int y = 0; y < cpy_h; y++)
					{
						for (int x = 0; x < cpy_w; x++)
						{
							AnsiCell* dst = rc->ansi_buf + (x + dst_x) + (y + dst_y) * view_size[0];
							AnsiCell* src = f->cell + (x + src_x) + (y + src_y) * f->width;
							*dst = *src;
						}
					}

					gl3TextureSubImage2D(rc->ansi_tex, 0, 0, 0, view_size[0], view_size[1], GL_RGBA, GL_UNSIGNED_BYTE, rc->ansi_buf);

					{ float ds = sw->dpi_scale;
					glViewport(
						(int)(sw->rect.Min.x * ds),
						vp[3] - (int)(sw->rect.Max.y * ds),
						(int)((sw->rect.Max.x - sw->rect.Min.x) * ds),
						(int)((sw->rect.Max.y - sw->rect.Min.y) * ds));
					}

					glScissor(
						(int)(sw->rect.Min.x * sw->dpi_scale),
						vp[3] - (int)(sw->rect.Max.y * sw->dpi_scale),
						(int)((sw->rect.Max.x - sw->rect.Min.x) * sw->dpi_scale),
						(int)((sw->rect.Max.y - sw->rect.Min.y) * sw->dpi_scale));

					glUseProgram(rc->ansi_prg);
					glUniform2i(rc->uni_ansi_vp, view_size[0], view_size[1]);

					glUniform1i(rc->uni_ansi, 0);
					glUniform1i(rc->uni_asciiid_extended_glyph_enabled, 0);
					glUniform1i(rc->uni_asciiid_sidecar_tex, 2);
					glUniform1i(rc->uni_asciiid_lut_tex, 3);
					glUniform1i(rc->uni_asciiid_page_atlas, 4);
					glUniform1f(rc->uni_asciiid_lut_width, 0.0f);
					glUniform1f(rc->uni_asciiid_fallback_glyph_id, 33.0f);

					int font_size[2];
					int font_tex = GetGLFont(font_size, 0, 0);

					gl3BindTextureUnit2D(0, rc->ansi_tex);

					glUniform1i(rc->uni_font, 1);
					gl3BindTextureUnit2D(1, font_tex);

					glUniform2i(rc->uni_ansi_wh, rc->ansi_buf_size[0], rc->ansi_buf_size[1]);

					glBindVertexArray(rc->ansi_vao);
					//glEnable(GL_BLEND);

					glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);

					glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
					glUseProgram(0);
					glBindVertexArray(0);

					//glDisable(GL_BLEND);


					gl3BindTextureUnit2D(0, 0);
					gl3BindTextureUnit2D(1, 0);

					// we should restore !!!!

					glBindBuffer(GL_ARRAY_BUFFER, vbo);

					gl3BindTextureUnit2D(2, 0);
					gl3BindTextureUnit2D(3, 0);
					gl3BindTextureUnit3D(4, 0);

					glBindVertexArray(vao);
					glUseProgram(prg);

					glViewport(vp[0], vp[1], vp[2], vp[3]);
					glScissor(sc[0], sc[1], sc[2], sc[3]);

					//if (!cull_face)
					//	glDisable(GL_CULL_FACE);
					//glCullFace(cull_mode);

					if (!depth_test)
						glDisable(GL_DEPTH_TEST);

					glDepthFunc(depth_func);
				}
			}

			bool Widget(const char* label, const ImVec2& size)
			{
				ImGuiWindow* window = ImGui::GetCurrentWindow();
				if (window->SkipItems)
					return false;

				ImGuiContext& g = *GImGui;
				const ImGuiStyle& style = g.Style;
				const ImGuiID id = window->GetID(label);

				ImVec2 pos = window->DC.CursorPos;
				ImVec2 adv(pos.x + size.x, pos.y + size.y);

				const ImRect bb(pos, adv);
				rect = bb;
				dpi_scale = ImGui::GetIO().DisplayFramebufferScale.x;

				ImGui::ItemSize(size, style.FramePadding.y);
				if (!ImGui::ItemAdd(bb, id))
					return false;

				ImGui::GetWindowDrawList()->AddCallback(draw_cb, this);
				return true;
			}

			ImRect rect;
			float dpi_scale;
		};

		struct MeshWidget
		{
			static void draw_cb(const ImDrawList* parent_list, const ImDrawCmd* cmd)
			{
				MeshWidget* mw = (MeshWidget*)cmd->UserCallbackData;
				if (!mw)
					return;

				if (!active_mesh)
					return;

				int vp[4];
				glGetIntegerv(GL_VIEWPORT,vp);

				int sc[4];
				glGetIntegerv(GL_SCISSOR_BOX,sc);

				int vao;
				glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &vao);

				int vbo;
				glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vbo);

				int prg;
				glGetIntegerv(GL_CURRENT_PROGRAM,&prg);

				//bool cull_face;
				//cull_face = glIsEnabled(GL_CULL_FACE);

				//int cull_mode;
				//glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);

				int depth_func;
				glGetIntegerv(GL_DEPTH_FUNC, &depth_func);

				bool depth_test;
				depth_test = glIsEnabled(GL_DEPTH_TEST);

				glViewport(
					(int)(mw->rect.Min.x * mw->dpi_scale),
					vp[3] - (int)(mw->rect.Max.y * mw->dpi_scale),
					(int)((mw->rect.Max.x - mw->rect.Min.x) * mw->dpi_scale),
					(int)((mw->rect.Max.y - mw->rect.Min.y) * mw->dpi_scale));

				glScissor(
					(int)(mw->rect.Min.x * mw->dpi_scale),
					vp[3] - (int)(mw->rect.Max.y * mw->dpi_scale),
					(int)((mw->rect.Max.x - mw->rect.Min.x) * mw->dpi_scale),
					(int)((mw->rect.Max.y - mw->rect.Min.y) * mw->dpi_scale));

				float bbox[6];
				GetMeshBBox(active_mesh, bbox);

				float radius = 0.5f * sqrtf( (bbox[1]-bbox[0])*(bbox[1]-bbox[0]) + (bbox[3]-bbox[2])*(bbox[3]-bbox[2]) );
				// todo radius could be calculated from bounding circle on XY

				// radius = 0.5 * fmaxf( (bbox[1]-bbox[0]), (bbox[3]-bbox[2]) );

				float height = bbox[5]-bbox[4];
				float alpha = atan2f(2*radius,height);
				if (alpha < (float)M_PI/6)
					alpha = (float)M_PI/6;

				float x_proj = 2*radius;
				float y_proj = fmaxf(2*radius, height * cosf(alpha) + 2*radius*sinf(alpha));

				float box_aspect = x_proj / y_proj;
				float vue_aspect = (mw->rect.Max.x - mw->rect.Min.x) / (mw->rect.Max.y - mw->rect.Min.y);

				float s[3];

				if (box_aspect > vue_aspect)
				{
					// mesh is wider than view
					s[0] = 2.0f / x_proj;
					s[1] = s[0] * vue_aspect;
				}
				else
				{
					// mesh is taller than view
					s[1] = 2.0f / y_proj;
					s[0] = s[1] / vue_aspect;
				}

				// depth scaling, bit over estimated.
				s[2] = -2.0f / (bbox[5]-bbox[4] + bbox[3]-bbox[2] + bbox[1]-bbox[0]);

				float vtm[16] =
				{
					s[0], 0.0,  0.0,  0.0,
					0.0,  s[1], 0.0,  0.0,
					0.0,  0.0,  s[2], 0.0,
					0.0,  0.0,  0.0,  1.0
				};

				float t[3] =
				{
					-0.5f*(bbox[0]+bbox[1]),
					-0.5f*(bbox[2]+bbox[3]),
					-0.5f*(bbox[4]+bbox[5])
				};

				float trn[16] = { 1,0,0,0, 0,1,0,0, 0,0,1,0, t[0], t[1], t[2], 1 };

				float rot1[16];
				float rot2[16];
				float v1[3] = {1,0,0};
				float v2[3] = {0,0,1};
				Rotation(v1, M_PI/180 * (rot_pitch-90), rot1);
				Rotation(v2, M_PI/180 * (-rot_yaw), rot2);

				float rot[16];
				MatProduct(rot1, rot2, rot);

				// projection matrix (based purely on viewing angles and widget canvas)
				float ftm[16];
				MatProduct(vtm, rot, ftm);

				// instance tm (based purely on mesh instance sliders)

				// here we do only:
				// 2. rotate around z by given angle + random_z
				// 3. rotate by given world's xy axis + random_xy (length is angle)
				MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(active_mesh);

				float itm[16];

				float angle = (float)M_PI / 180 * mp->rotate_locZ_val;
				Rotation(v2, angle, rot2);

				v1[0] = mp->rotate_XY_val[0];
				v1[1] = mp->rotate_XY_val[1];
				v1[2] = 0;

				angle = sqrtf(v1[0]*v1[0] + v1[1]*v1[1]);
				if (angle != 0)
				{
					v1[0]/=angle;
					v1[1]/=angle;
				}

				if (angle>1)
					angle = 1;

				Rotation(v1, angle * (float)M_PI, rot1);

				MatProduct(rot1, rot2, rot);

				MatProduct(rot, trn, itm);

				// draw!
				RenderContext* rc = &render_context;

				double noon_yaw[2] =
				{
					// zero is behind viewer
					-sin(-lit_yaw * M_PI / 180),
					-cos(-lit_yaw * M_PI / 180),
				};

				double dusk_yaw[3] =
				{
					-noon_yaw[1],
					noon_yaw[0],
					0
				};

				double noon_pos[4] =
				{
					noon_yaw[0] * cos(lit_pitch*M_PI / 180),
					noon_yaw[1] * cos(lit_pitch*M_PI / 180),
					sin(lit_pitch*M_PI / 180),
					0
				};

				double lit_axis[3];

				CrossProduct(dusk_yaw, noon_pos, lit_axis);

				double time_tm[16];
				Rotation(lit_axis, (lit_time - 12)*M_PI / 12, time_tm);

				double lit_pos[4];
				Product(time_tm, noon_pos, lit_pos);

				float lt[4] =
				{
					(float)lit_pos[0],
					(float)lit_pos[1],
					(float)lit_pos[2],
					ambience
				};

				glClearDepth(1.0);
				glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);

				glUseProgram(rc->mesh_prg);

				glUniformMatrix4fv(rc->mesh_inst_tm_loc, 1, GL_FALSE, itm);
				glUniformMatrix4fv(rc->mesh_tm_loc, 1, GL_FALSE, ftm);
				glUniform4fv(rc->mesh_lt_loc, 1, lt);
				glUniform1i(rc->mesh_a_tex_loc, 2);
				glUniform1i(rc->mesh_f_tex_loc, 3);
				glUniform1i(rc->mesh_p_tex_loc, 4);

				glBindVertexArray(rc->mesh_vao);

				gl3BindTextureUnit2D(2, rc->ansi_tex);
				gl3BindTextureUnit2D(3, font[active_font].tex);
				gl3BindTextureUnit3D(4, pal_tex);

				//glEnable(GL_CULL_FACE);
				//glCullFace(GL_BACK);
				glEnable(GL_DEPTH_TEST);
				glDepthFunc(GL_LEQUAL);

				glBindBuffer(GL_ARRAY_BUFFER, rc->mesh_vbo);

				rc->mesh_faces = 0;
				QueryMesh(active_mesh, RenderContext::RenderFace, rc);

				if (rc->mesh_faces)
				{
					// flush!!!
					glBufferSubData(GL_ARRAY_BUFFER,0,sizeof(RenderContext::Face)*rc->mesh_faces,rc->mesh_map);
					glDrawArrays(GL_POINTS, 0, rc->mesh_faces);
					rc->mesh_faces = 0;
				}

				// we should restore !!!!

				glBindBuffer(GL_ARRAY_BUFFER, vbo);

				gl3BindTextureUnit2D(2, 0);
				gl3BindTextureUnit2D(3, 0);
				gl3BindTextureUnit3D(4, 0);

				glBindVertexArray(vao);
				glUseProgram(prg);

				glViewport(vp[0],vp[1],vp[2],vp[3]);
				glScissor(sc[0],sc[1],sc[2],sc[3]);

				//if (!cull_face)
				//	glDisable(GL_CULL_FACE);
				//glCullFace(cull_mode);

				if (!depth_test)
					glDisable(GL_DEPTH_TEST);

				glDepthFunc(depth_func);

			}

			bool Widget(const char* label, const ImVec2& size)
			{
				ImGuiWindow* window = ImGui::GetCurrentWindow();
				if (window->SkipItems)
					return false;

				ImGuiContext& g = *GImGui;
				const ImGuiStyle& style = g.Style;
				const ImGuiID id = window->GetID(label);

				ImVec2 pos = window->DC.CursorPos;
				ImVec2 adv(pos.x+size.x,pos.y+size.y);

				const ImRect bb(pos, adv);
				rect = bb;
				dpi_scale = ImGui::GetIO().DisplayFramebufferScale.x;

				ImGui::ItemSize(size, style.FramePadding.y);
				if (!ImGui::ItemAdd(bb, id))
					return false;

				ImGui::GetWindowDrawList()->AddCallback(draw_cb, this);
				return true;
			}

			ImRect rect;
			float dpi_scale;
		};

		struct TerrainPreviewWidget
		{
			static void draw_cb(const ImDrawList* parent_list, const ImDrawCmd* cmd)
			{
				(void)parent_list;
				TerrainPreviewWidget* tw = (TerrainPreviewWidget*)cmd->UserCallbackData;
				if (!tw)
					return;

				Terrain* preview = GetTerrainPreviewScene();
				if (!preview)
					return;

				int vp[4];
				glGetIntegerv(GL_VIEWPORT, vp);

				int sc[4];
				glGetIntegerv(GL_SCISSOR_BOX, sc);

				int vao;
				glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &vao);

				int vbo;
				glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &vbo);

				int prg;
				glGetIntegerv(GL_CURRENT_PROGRAM, &prg);

				int depth_func;
				glGetIntegerv(GL_DEPTH_FUNC, &depth_func);

				bool depth_test = glIsEnabled(GL_DEPTH_TEST);

				glViewport(
					(int)(tw->rect.Min.x * tw->dpi_scale),
					vp[3] - (int)(tw->rect.Max.y * tw->dpi_scale),
					(int)((tw->rect.Max.x - tw->rect.Min.x) * tw->dpi_scale),
					(int)((tw->rect.Max.y - tw->rect.Min.y) * tw->dpi_scale));

				glScissor(
					(int)(tw->rect.Min.x * tw->dpi_scale),
					vp[3] - (int)(tw->rect.Max.y * tw->dpi_scale),
					(int)((tw->rect.Max.x - tw->rect.Min.x) * tw->dpi_scale),
					(int)((tw->rect.Max.y - tw->rect.Min.y) * tw->dpi_scale));

				glClearDepth(0.0);
				glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);

				const float widget_w = tw->rect.Max.x - tw->rect.Min.x;
				const float widget_h = tw->rect.Max.y - tw->rect.Min.y;
				const float scene_span = (float)kTerrainPreviewPatchSpan * VISUAL_CELLS;
				float preview_font_size = fminf(widget_w / (scene_span + 6.0f), widget_h / 18.0f);
				if (preview_font_size < 3.0f)
					preview_font_size = 3.0f;

				double rx = 0.5 * widget_w / preview_font_size;
				double ry = 0.5 * widget_h / preview_font_size;
				double yaw = 40.0 * M_PI / 180.0;
				double pitch = 38.0 * M_PI / 180.0;
				double z_scale = 1.0 / HEIGHT_SCALE;
				double center = 0.5 * scene_span;

				double tm[16];
				tm[0] = +cos(yaw) / rx;
				tm[1] = -sin(yaw) * sin(pitch) / ry;
				tm[2] = 0;
				tm[3] = 0;
				tm[4] = +sin(yaw) / rx;
				tm[5] = +cos(yaw) * sin(pitch) / ry;
				tm[6] = 0;
				tm[7] = 0;
				tm[8] = 0;
				tm[9] = +cos(pitch) * z_scale / ry;
				tm[10] = +2.0 / 0xFFFF;
				tm[11] = 0;
				tm[12] = -(center * tm[0] + center * tm[4]);
				tm[13] = -(center * tm[1] + center * tm[5] + (kTerrainPreviewBaseHeight + 0x0600) * tm[9]);
				tm[14] = -1.0;
				tm[15] = 1.0;

				double noon_yaw[2] =
				{
					-sin(-lit_yaw * M_PI / 180),
					-cos(-lit_yaw * M_PI / 180),
				};

				double dusk_yaw[3] =
				{
					-noon_yaw[1],
					noon_yaw[0],
					0
				};

				double noon_pos[4] =
				{
					noon_yaw[0] * cos(lit_pitch * M_PI / 180),
					noon_yaw[1] * cos(lit_pitch * M_PI / 180),
					sin(lit_pitch * M_PI / 180),
					0
				};

				double lit_axis[3];
				CrossProduct(dusk_yaw, noon_pos, lit_axis);

				double time_tm[16];
				Rotation(lit_axis, (lit_time - 12) * M_PI / 12, time_tm);

				double lit_pos[4];
				Product(time_tm, noon_pos, lit_pos);

				float lt[4] =
				{
					(float)lit_pos[0],
					(float)lit_pos[1],
					(float)lit_pos[2],
					ambience
				};

				float br_off[4] = { 0, 0, 1, 0 };
				float qd_off[3] = { 0, 0, 0 };
				float probe_off[3] = { 0, 1, 0 };
				float old_font_size = font_size;
				float old_grid_alpha = grid_alpha;
				font_size = preview_font_size;
				grid_alpha = 0.20f;

				RenderContext* rc = &render_context;
				glEnable(GL_DEPTH_TEST);
				glDepthFunc(GL_GEQUAL);
				rc->BeginPatches(tm, lt, br_off, qd_off, probe_off);
				QueryTerrain(preview, center, center, scene_span, 0xAA, RenderContext::RenderPatch, rc);
				rc->EndPatches();

				grid_alpha = old_grid_alpha;
				font_size = old_font_size;

				glBindBuffer(GL_ARRAY_BUFFER, vbo);
				glBindVertexArray(vao);
				glUseProgram(prg);
				glViewport(vp[0], vp[1], vp[2], vp[3]);
				glScissor(sc[0], sc[1], sc[2], sc[3]);

				if (!depth_test)
					glDisable(GL_DEPTH_TEST);
				glDepthFunc(depth_func);
			}

			bool Widget(const char* label, const ImVec2& size)
			{
				ImGuiWindow* window = ImGui::GetCurrentWindow();
				if (window->SkipItems)
					return false;

				ImGuiContext& g = *GImGui;
				const ImGuiStyle& style = g.Style;
				const ImGuiID id = window->GetID(label);

				ImVec2 pos = window->DC.CursorPos;
				ImVec2 adv(pos.x + size.x, pos.y + size.y);

				const ImRect bb(pos, adv);
				rect = bb;
				dpi_scale = ImGui::GetIO().DisplayFramebufferScale.x;

				ImGui::ItemSize(size, style.FramePadding.y);
				if (!ImGui::ItemAdd(bb, id))
					return false;

				ImGui::GetWindowDrawList()->AddCallback(draw_cb, this);
				return true;
			}

			ImRect rect;
			float dpi_scale;
		};

		// ── UNIFIED SIDEBAR ──────────────────────────────────────────────────────────
		// Replaces all former free-floating editor windows with a single anchored left
		// panel. The isometric viewport renders to the full framebuffer; this panel
		// occludes the left 360px. io.WantCaptureMouse blocks camera controls here.
		static int sidebar_tab = 1; // 0=VIEW 1=EDIT 2=SPRITE 3=MESH 4=INST 5=FONT 6=SKIN 7=INFO
		g_asciiid_sidebar_tab_debug = sidebar_tab;
		for (int i = 0; i < kAsciiidExtendedGlyphPresetCount; i++)
			g_asciiid_preset_ui_rects[i].valid = false;
		g_asciiid_extended_picker_first_glyph_rect.valid = false;
		g_asciiid_extended_picker_fill_rect.valid = false;
		g_asciiid_extended_picker_first_glyph_id = GLYPH_ID_NONE;

		// Font/glyph vars needed by both FONT tab (5) and SKIN tab (6).
		// Hoisted here so SKIN can use them when FONT tab has never been rendered.
		float font_width  = (float)font[active_font].width;
		float font_height = (float)font[active_font].height;
		if (font_width < 256)
		{
			font_height *= 256.0f / font[active_font].width;
			font_width = 256;
		}
		const int   glyph_w  = font[active_font].width  / 16;
		const int   glyph_h  = font[active_font].height / 16;
		if (AsciiidExtendedCellPxOptionIndex(g_asciiid_extended_preview_cell_px) < 0 &&
			glyph_w == glyph_h && glyph_w > 0 && glyph_w <= kAsciiidCompiledAtlasMaxCellPx)
			g_asciiid_extended_preview_cell_px = glyph_w;
		const float glyph_x  = (active_glyph & 0xf) * glyph_w / (float)font[active_font].width;
		const float glyph_y  = (active_glyph >> 4)  * glyph_h / (float)font[active_font].height;
		const float texel_w  = 1.0f / font[active_font].width;
		const float texel_h  = 1.0f / font[active_font].height;
		const float but_w    = 13 + 48.0f / (font_width / 16);
		const float but16_w  = font_width  / 16;
		const float but16_h  = font_height / 16;
		auto render_font_switcher = [&](const char* id_prefix, bool show_cell_size)
		{
			if (!fonts_loaded)
				return;

			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushID(id_prefix);
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("left", ImGuiDir_Left))
			{
				if (active_font > 0)
				{
					active_font--;
					TermResizeAll();
				}
			}
			ImGui::SameLine(0.0f, spacing);
			if (ImGui::ArrowButton("right", ImGuiDir_Right))
			{
				if (active_font < fonts_loaded - 1)
				{
					active_font++;
					TermResizeAll();
				}
			}
			ImGui::PopButtonRepeat();
			ImGui::SameLine();
			ImGui::Text("%s  [%d]", font[active_font].name[0] ? font[active_font].name : "???", active_font);
			if (show_cell_size)
				ImGui::Text("CELL SIZE: %dx%d px", font[active_font].width / 16, font[active_font].height / 16);
			ImGui::PopID();
		};
		auto render_palette_switcher = [&](const char* id_prefix)
		{
			if (!palettes_loaded)
				return;

			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushID(id_prefix);
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("left", ImGuiDir_Left))
			{
				if (active_palette > 0)
					active_palette--;
			}
			ImGui::SameLine(0.0f, spacing);
			if (ImGui::ArrowButton("right", ImGuiDir_Right))
			{
				if (active_palette < palettes_loaded - 1)
					active_palette++;
			}
			ImGui::PopButtonRepeat();
			ImGui::SameLine();
			ImGui::Text("%s  [%d]", pal[active_palette].name[0] ? pal[active_palette].name : "???", active_palette);
			ImGui::PopID();
		};

		auto render_character_owner = [&]()
		{
			if (!fonts_loaded)
				return;

			if (!ImGui::CollapsingHeader("Character", ImGuiTreeNodeFlags_DefaultOpen))
				return;

			render_font_switcher("edit_character_font_switcher", true);

			struct GlyphPreset
			{
				const char* name;
				const char* desc;
				const uint8_t* glyphs;
				int count;
			};

			static const uint8_t preset_legible[] = { '.', ':', 0x2D, '=', '+', '*', '#', '%', '@' };
			static const uint8_t preset_dense[] = { '.', ',', ':', ';', 'i', 'r', 's', 'X' };
			static const uint8_t preset_silhouette[] = { '.', ':', 0x2D, '=', '+', '*', '#', '%', '@', '/', '\\', '|', '_' };
			static const uint8_t preset_blocks[] = { 0xFA, 0xB0, 0xB1, 0xB2, 0xDB }; // ·░▒▓█
			static const uint8_t preset_organic[] = { '.', ',', ';', ':', '~', '*', '#' };
			static const uint8_t preset_minimal[] = { '.', 0xFA, ':' }; // . · :
			static const uint8_t preset_geometric[] = { 0xFA, 0x07, 0x09, 0xFE, 0xDB }; // ·•○■█
			static const uint8_t preset_halves[] = { 0xDC, 0xDD, 0xDE, 0xDF, 0xDB }; // ▄▌▐▀█
			static const GlyphPreset presets[] =
			{
				{ ".:-=+*#%@", "Legible — stable readability", preset_legible, 9 },
				{ ".,:;irsX", "Dense — more texture", preset_dense, 8 },
				{ ".:-=+*#%@/\\|_", "Silhouette — preserves edges", preset_silhouette, 13 },
				{ ".,;:~*#", "Organic — natural/vegetation feel", preset_organic, 7 },
				{ ".`:", "Minimal — very subtle texturing", preset_minimal, 3 },
				{ "blocks", "Blocks — CP437 shade blocks", preset_blocks, 5 },
				{ "halves", "Halves — half-block characters", preset_halves, 5 },
				{ "geometric", "Geometric — shape-based density", preset_geometric, 5 },
			};
			static int active_preset = -1;
			static bool custom_seeded = false;
			static uint8_t custom_preset[16];
			static uint8_t custom_preset_edit[16];
			static bool custom_edit_open = false;
			static bool custom_edit_dirty = false;
			static int custom_edit_slot = 0;
			ImVec2 atlas_rect_min(0, 0), atlas_rect_max(0, 0);
			ImVec2 custom_edit_rect_min(0, 0), custom_edit_rect_max(0, 0);
			ImVec2 custom_edit_toggle_min(0, 0), custom_edit_toggle_max(0, 0);

			auto apply_preset_sequence = [&](const uint8_t* glyphs, int count)
			{
				URDO_Open();
				URDO_Material(active_material);
				for (int row = 0; row < MaterialGridRows(); row++)
				{
					for (int col = 0; col < MaterialGridCols(); col++)
						MaterialGridSetGlyphId(active_material, row, col, glyphs[col * count / MaterialGridCols()]);
				}
				MaterialGridCommit(active_material);
				URDO_Close();
			};

			auto apply_extended_preset_sequence = [&](const GlyphId* glyphs, int count)
			{
				URDO_Open();
				URDO_Material(active_material);
				for (int row = 0; row < MaterialGridRows(); row++)
				{
					for (int col = 0; col < MaterialGridCols(); col++)
						MaterialGridSetGlyphId(active_material, row, col, glyphs[col * count / MaterialGridCols()]);
				}
				MaterialGridCommit(active_material);
				URDO_Close();
			};

			auto close_custom_editor = [&](bool commit)
			{
				if (commit && custom_edit_dirty)
					memcpy(custom_preset, custom_preset_edit, sizeof(custom_preset));
				custom_edit_open = false;
				custom_edit_dirty = false;
			};

			if (!custom_seeded)
			{
				for (int col = 0; col < MaterialGridCols(); col++)
					custom_preset[col] = preset_legible[col * (int)(sizeof(preset_legible) / sizeof(preset_legible[0])) / MaterialGridCols()];
				custom_seeded = true;
			}

			// FL-4131 P3: pure character browser — repertoire-grouped extended
			// GlyphId palette under the CP437 16x16 grid in the Character tab.
			// No material write operations live here.
			auto render_extended_character_palette = [&]()
			{
				g_asciiid_extended_preset_ui_frame_count++;
				const GlyphManifest* manifest = AsciiidExtendedPickerManifest();
				ImGui::Separator();
				ImGui::Text("Extended Glyphs");
				if (!manifest)
				{
					ImGui::TextDisabled("Manifest unavailable.");
					return;
				}

				ImGui::Spacing();
				ImGui::Text("Extended Glyph Palette");
				static char glyph_filter[64] = "";
				static int glyph_page = 0;
				static int glyph_category_filter = -1; // -1 = All
				const GlyphId extended_preview_glyph_id = (active_glyph_id > 0xFF)
					? active_glyph_id
					: g_asciiid_selected_extended_glyph_id;
				ImGui::PushItemWidth(180.0f);
				ImGui::InputText("Filter##extended_glyph_filter", glyph_filter, sizeof(glyph_filter));
				ImGui::PopItemWidth();
				{
					int size_index = AsciiidExtendedCellPxOptionIndex(g_asciiid_extended_preview_cell_px);
					if (size_index < 0)
					{
						size_index = AsciiidExtendedCellPxOptionIndex(16);
						g_asciiid_extended_preview_cell_px = 16;
					}
					ImGui::SameLine();
					ImGui::Text("EXTENDED SIZE: %dx%d px", g_asciiid_extended_preview_cell_px, g_asciiid_extended_preview_cell_px);
					ImGui::SameLine();
					ImGui::PushButtonRepeat(true);
					if (ImGui::ArrowButton("##extended_size_prev", ImGuiDir_Left) && size_index > 0)
						g_asciiid_extended_preview_cell_px = kAsciiidExtendedCellPxOptions[size_index - 1];
					ImGui::SameLine();
					if (ImGui::ArrowButton("##extended_size_next", ImGuiDir_Right) &&
						size_index < (int)(sizeof(kAsciiidExtendedCellPxOptions) / sizeof(kAsciiidExtendedCellPxOptions[0])) - 1)
						g_asciiid_extended_preview_cell_px = kAsciiidExtendedCellPxOptions[size_index + 1];
					ImGui::PopButtonRepeat();
					ImGui::SameLine();
					if (glyph_w == glyph_h && AsciiidExtendedCellPxOptionIndex(glyph_w) >= 0 &&
						ImGui::SmallButton("Match CP437 size##extended_size_match_cp437"))
						g_asciiid_extended_preview_cell_px = glyph_w;
				}
				ImGui::SameLine();
				if (ImGui::ArrowButton("##extended_glyph_prev_page", ImGuiDir_Left) && glyph_page > 0)
					glyph_page--;
				ImGui::SameLine();
				if (ImGui::ArrowButton("##extended_glyph_next_page", ImGuiDir_Right))
					glyph_page++;

				// FL-4131 P3: category quick-filter row. "All" disables the bucket
				// filter; each bucket maps an Unicode block range to a label so an
				// operator can scan Math / Box / Blocks / Shapes / Kana etc.
				{
					ImGui::Spacing();
					if (ImGui::SmallButton(glyph_category_filter == -1 ? "[All]" : "All"))
					{
						glyph_category_filter = -1;
						glyph_page = 0;
					}
					for (int c = 0; c < kAsciiidExtCatCount; c++)
					{
						ImGui::SameLine();
						char btn_label[24];
						snprintf(btn_label, sizeof(btn_label), "%s%s",
							glyph_category_filter == c ? "[" : "",
							AsciiidExtendedGlyphCategoryLabel(c));
						if (glyph_category_filter == c)
							snprintf(btn_label, sizeof(btn_label), "[%s]", AsciiidExtendedGlyphCategoryLabel(c));
						if (ImGui::SmallButton(btn_label))
						{
							glyph_category_filter = (glyph_category_filter == c) ? -1 : c;
							glyph_page = 0;
						}
					}
				}

				{
					ImGui::Spacing();
					const float preview_dim = 48.0f;
					ImGui::PushID(extended_preview_glyph_id + 81000);
					uint8_t preview_fg[3] = { 235, 235, 235 };
					uint8_t preview_bg[3] = { 18, 18, 18 };
					DrawAsciiidExtendedGlyphButton(extended_preview_glyph_id, ImVec2(preview_dim, preview_dim), preview_fg, preview_bg);
					ImGui::PopID();
					ImGui::SameLine();
					char selected_label[96];
					FormatGlyphIdLabel(extended_preview_glyph_id, selected_label, sizeof(selected_label));
					ImGui::Text("%s", selected_label);
				}

				const int glyphs_per_page = 48;
				int matched = 0;
				int shown = 0;
				bool any_after_page = false;
				char filter_lower[64];
				snprintf(filter_lower, sizeof(filter_lower), "%s", glyph_filter);
				for (char* p = filter_lower; *p; p++)
					if (*p >= 'A' && *p <= 'Z')
						*p = (char)(*p - 'A' + 'a');

				ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(1, 1));
				for (int entry_i = 0; entry_i < manifest->entry_count; entry_i++)
				{
					const GlyphId glyph_id = manifest->entries[entry_i].glyph_id;
					char label[96];
					FormatGlyphIdLabel(glyph_id, label, sizeof(label));
					if (glyph_category_filter >= 0)
					{
						uint32_t u = 0;
						int cat = AsciiidExtendedGlyphUnicodeScalar(glyph_id, &u)
							? AsciiidExtendedGlyphCategoryForUnicode(u)
							: kAsciiidExtCatOther;
						if (cat != glyph_category_filter)
							continue;
					}
					bool passes_filter = true;
					if (filter_lower[0])
					{
						char haystack[128];
						snprintf(haystack, sizeof(haystack), "%u %s", (unsigned)glyph_id, label);
						for (char* p = haystack; *p; p++)
							if (*p >= 'A' && *p <= 'Z')
								*p = (char)(*p - 'A' + 'a');
						passes_filter = strstr(haystack, filter_lower) != NULL;
					}
					if (!passes_filter)
						continue;
					const int visible_index = matched++;
					if (visible_index < glyph_page * glyphs_per_page)
						continue;
					if (shown >= glyphs_per_page)
					{
						any_after_page = true;
						continue;
					}

					ImGui::PushID(entry_i + 70000);
					const bool pushed_active_glyph_color = (extended_preview_glyph_id == glyph_id);
					if (pushed_active_glyph_color)
						ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
					const float swatch_dim = fmaxf(24.0f, fmaxf(but16_w * 1.5f, but16_h * 1.5f));
					// FL-4131 palette UX: show the actual generated extended
					// atlas cell, not the CP437 fallback byte or active CP437
					// character state.
					uint8_t swatch_fg[3] = { 235, 235, 235 };
					uint8_t swatch_bg[3] = { 18, 18, 18 };
					if (DrawAsciiidExtendedGlyphButton(glyph_id, ImVec2(swatch_dim, swatch_dim), swatch_fg, swatch_bg))
						SelectActiveGlyphId(glyph_id);
					if (ImGui::IsItemHovered())
						ImGui::SetTooltip("%s", label);
					if (!g_asciiid_extended_picker_first_glyph_rect.valid)
					{
						ImVec2 item_min = ImGui::GetItemRectMin();
						ImVec2 item_max = ImGui::GetItemRectMax();
						g_asciiid_extended_picker_first_glyph_rect.valid = true;
						g_asciiid_extended_picker_first_glyph_rect.x0 = (int)item_min.x;
						g_asciiid_extended_picker_first_glyph_rect.y0 = (int)item_min.y;
						g_asciiid_extended_picker_first_glyph_rect.x1 = (int)item_max.x;
						g_asciiid_extended_picker_first_glyph_rect.y1 = (int)item_max.y;
						g_asciiid_extended_picker_first_glyph_id = glyph_id;
					}
					if (pushed_active_glyph_color)
						ImGui::PopStyleColor();
					ImGui::PopID();
					shown++;
					if ((shown % 16) != 0)
						ImGui::SameLine();
				}
				ImGui::PopStyleVar();
				if (glyph_page > 0 && matched <= glyph_page * glyphs_per_page)
					glyph_page = 0;

				ImGui::Spacing();
				ImGui::Separator();
				ImGui::TextDisabled("Selected glyph preview");
				{
					const float preview_dim = 64.0f;
					ImGui::PushID(extended_preview_glyph_id + 80000);
					uint8_t preview_fg[3] = { 235, 235, 235 };
					uint8_t preview_bg[3] = { 18, 18, 18 };
					DrawAsciiidExtendedGlyphButton(extended_preview_glyph_id, ImVec2(preview_dim, preview_dim), preview_fg, preview_bg);
					ImGui::PopID();
					ImGui::SameLine();
					ImGui::BeginGroup();
					const AsciiidExtendedGlyphLabel* label_meta = FindAsciiidExtendedGlyphLabel(extended_preview_glyph_id);
					ImGui::Text("GlyphId %u", (unsigned)extended_preview_glyph_id);
					if (label_meta)
						ImGui::Text("Label: %s", label_meta->label);
					else
						ImGui::TextDisabled("Label: (none)");
					uint8_t fallback_byte = AsciiidGlyphFallbackByte(extended_preview_glyph_id);
					ImGui::Text("Fallback CP437: 0x%02X", (unsigned)fallback_byte);
					ImGui::EndGroup();
				}
				ImGui::Spacing();
				ImGui::TextDisabled("%d/%d%s — material presets and Fill live in the Materials section.", shown, matched, any_after_page ? "+" : "");
				ImGui::TextDisabled("%s: %d admitted glyphs.",
					manifest->content_pack_id[0] ? manifest->content_pack_id : "glyph_manifest",
					manifest->entry_count);
			};

			// FL-4131 P4/P5: extended material workspace lives in
			// AsciiidRenderExtendedMaterialWorkspace (file-scope static).
			// Defined here so render_materials_owner — which lives in a
			// different lambda scope inside my_render — can call it without
			// requiring lambda hoisting.

			// FL-4131 Phase 4 — ASCIIID authoring/input pin
			// ─────────────────────────────────────────────────────────────────
			// MODEL_PIN: asciiid_input_model_pinned
			//
			// PURPOSE: Pin the ASCIIID authoring/input model for extended
			// GlyphIds. The 16x16 grid immediately below is the CP437 picker
			// (active_glyph ∈ [0..255]); it remains the only authoring surface
			// until the extended picker is wired. Pinning the model now keeps
			// future extended-picker work from drifting into a second owner.
			//
			// EXTENDED-PICKER MODEL:
			//   - Browse glyph_manifest.entries (Phase 2; engine/glyph_manifest.h)
			//     paginated by repertoire page (atlas_of_atlases binding). The
			//     picker uses the same 16x16 page-rect sample shape as this
			//     CP437 grid; the only difference is the bound atlas sampler
			//     and the LUT-resolved (page_index, atlas_x, atlas_y) tuple.
			//   - Selecting an entry sets active_glyph_id (extended GlyphId)
			//     and writes to the live MatCell/AnsiCell through URDO-aware
			//     content edits. Authoring writes carry the GlyphId via the
			//     companion GlyphPlane sidecar — AnsiCell.gl / MatCell.gl stay
			//     CP437 fallback bytes (no widening; FL-4131 hard rule).
			//   - Font switching is presentation-only: it MUST NOT rewrite
			//     stored MatCell.gl values, material shade rows, or sprite
			//     glyphs. This is the FL-3833 / B-19 boundary (canon spec).
			//
			// FAIL-CLOSED UNSUPPORTED CLUSTERS:
			//   - If the active manifest lacks coverage for a GlyphId the user
			//     attempts to paint, the picker greys it out (or refuses the
			//     write) and emits a [FL-4131] diagnostic. The picker MUST NOT
			//     silently fall back to a CP437 lookalike — that path violates
			//     the no_silent_glyph_truncation gate.
			//
			// LANE: this is the authoring UI pin. The renderer/shader pin
			// lives above (asciiid_shader_manifest_lookup) and in
			// engine/render/render.h (shader_lookup_lut_model_pinned).
			//
			// COMPANION ANCHORS (review M5 — structured block, grep-stable):
			//   engine/render/render.h               : MODEL_PIN shader_lookup_lut_model_pinned
			//   editor/asciiid.cpp (term_fs_src)     : MODEL_PIN asciiid_shader_manifest_lookup
			//   web/game_web.html (fragment shader)  : MODEL_PIN web_extended_glyph_buffer
			//   web/game_web.cpp                     : MODEL_PIN web_extended_glyph_buffer
			//   engine/glyph_manifest.h              : Phase 2 manifest + RFC8785+SHA-256
			//   server/protocol/protocol_join.h      : MODEL_PIN multiplayer_manifest_hash_match
			// ─────────────────────────────────────────────────────────────────
			ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
			ImVec4 tint_normal(1, 1, 1, 0.33f);
			ImVec4 tint_onedim(1, 1, 1, 0.50f);
			ImVec4 tint_active(1, 1, 1, 1.00f);
			atlas_rect_min = ImGui::GetCursorScreenPos();
			for (int y = 0; y < 16; y++)
			{
				for (int x = 0; x < 16; x++)
				{
					ImVec4* tint = &tint_normal;
					bool pushed = false;
					if (x + y * 16 == active_glyph)
					{
						ImVec4 hi = ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive);
						ImGui::PushStyleColor(ImGuiCol_Button, hi);
						tint = &tint_active;
						pushed = true;
					}
					else if (x == (active_glyph & 0xf) || y == (active_glyph >> 4))
					{
						tint = &tint_onedim;
					}

					ImGui::PushID(x + y * 16);
					if (ImGui::ImageButton((void*)(intptr_t)font[active_font].tex,
						ImVec2(font_width / 16.f, font_height / 16.f),
						ImVec2(x / 16.0f, y / 16.0f), ImVec2((x + 1) / 16.0f, (y + 1) / 16.0f),
						1, ImVec4(0, 0, 0, 0), *tint))
					{
						SelectActiveGlyphId((GlyphId)(x + y * 16));
						if (custom_edit_open)
						{
							custom_preset_edit[custom_edit_slot] = (uint8_t)active_glyph;
							custom_edit_dirty = true;
							if (custom_edit_slot < MaterialGridCols() - 1)
								custom_edit_slot++;
						}
					}
					if (ImGui::IsItemHovered())
					{
						int idx = x + y * 16;
						char glyph_label[64];
						FormatGlyphLabel(idx, glyph_label, sizeof(glyph_label));
						if (custom_edit_open)
							ImGui::SetTooltip("%s\nCustom slot %d will update on click.", glyph_label, custom_edit_slot);
						else
							ImGui::SetTooltip("%s", glyph_label);
					}
					ImGui::PopID();

					if (pushed)
						ImGui::PopStyleColor();
					if (x < 15)
						ImGui::SameLine();
				}
			}
			ImGui::PopStyleVar();
			atlas_rect_max = ImGui::GetItemRectMax();

			// FL-4131 P3: Character tab hosts the repertoire browser only.
			// Material-side actions (Fill, presets, per-cell paint) live in
			// the Materials section via render_extended_material_workspace.
			render_extended_character_palette();

			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("##edit_chr_left", ImGuiDir_Left)) { if (active_glyph > 0) SelectActiveGlyphId((GlyphId)(active_glyph - 1)); }
			ImGui::SameLine(0.0f, spacing);
			if (ImGui::ArrowButton("##edit_chr_right", ImGuiDir_Right)) { if (active_glyph < 0xff) SelectActiveGlyphId((GlyphId)(active_glyph + 1)); }
			ImGui::PopButtonRepeat();
			ImGui::SameLine();
			{
				// FL-4131 UX: CP437 size browser owns CP437 byte info ONLY.
				// The extended-GlyphId label belongs to the Extended Glyph Browser
				// preview panel; duplicating it here produced "GlyphId 512 integral
				// fallback 0x7C" twice in one section when an extended id is active.
				if (active_glyph_id <= 0xFF)
				{
					char cp437_label[64];
					FormatGlyphLabel((int)active_glyph_id, cp437_label, sizeof(cp437_label));
					ImGui::Text("%s", cp437_label);
				}
				else
				{
					ImGui::TextDisabled("CP437 0x%02X (extended GlyphId %u active via Glyph Browser)",
						(unsigned)active_glyph, (unsigned)active_glyph_id);
				}
			}

			if (active_glyph_id > 0xFF)
			{
				uint8_t preview_fg[3] = { 235, 235, 235 };
				uint8_t preview_bg[3] = { 18, 18, 18 };
				const float preview_w = but_w * glyph_w;
				const float preview_h = but_w * glyph_h;
				ImGui::PushID(active_glyph_id + 260000);
				DrawAsciiidExtendedGlyphButton(active_glyph_id, ImVec2(preview_w, preview_h), preview_fg, preview_bg);
				DrawAsciiidExtendedGlyphTextOverlay(active_glyph_id, ImGui::GetItemRectMin(), ImGui::GetItemRectMax(), IM_COL32(255, 255, 255, 255), fminf(preview_h * 0.72f, 44.0f));
				ImGui::PopID();
				ImGui::SameLine();
				ImGui::TextDisabled("Extended GlyphId selected; CP437 pixel editor is disabled for this glyph.");
			}
			else
			{
				ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
				for (int y = 0; y < glyph_h; y++)
				{
					for (int x = 0; x < glyph_w; x++)
					{
						ImGui::PushID(x + y * glyph_w + 256);
						if (ImGui::ImageButton((void*)(intptr_t)font[active_font].tex, ImVec2(but_w, but_w),
							ImVec2(glyph_x + x * texel_w, glyph_y + y * texel_h),
							ImVec2(glyph_x + (x + 1) * texel_w, glyph_y + (y + 1) * texel_h),
							1, ImVec4(0, 0, 0, .5f), ImVec4(1, 1, 1, .5f)))
						{
							int u = x + glyph_w * (active_glyph & 0xF);
							int v = y + glyph_h * (active_glyph >> 4);
							uint8_t p = font[active_font].GetTexel(u, v);
							p ^= 0xFF;
							font[active_font].SetTexel(u, v, p);
						}
						ImGui::PopID();
						if (x < glyph_w - 1)
							ImGui::SameLine();
					}
				}
				ImGui::PopStyleVar();
			}

			ImGui::Separator();
			ImGui::Text("Glyph Presets");
			for (int i = 0; i < (int)(sizeof(presets) / sizeof(presets[0])); i++)
			{
				bool selected = (active_preset == i);
				if (selected)
					ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
				if (ImGui::Button(presets[i].name))
				{
					if (custom_edit_open)
						close_custom_editor(false);
					active_preset = i;
					apply_preset_sequence(presets[i].glyphs, presets[i].count);
				}
				if (ImGui::IsItemHovered())
					ImGui::SetTooltip("%s", presets[i].desc);
				if (selected)
					ImGui::PopStyleColor();
				ImGui::SameLine();
			}
			if (ImGui::Button("Custom"))
			{
				active_preset = -1;
				apply_preset_sequence(custom_edit_open ? custom_preset_edit : custom_preset, MaterialGridCols());
			}
			ImGui::SameLine();
			if (ImGui::Button(custom_edit_open ? "[close]" : "[edit]"))
			{
				custom_edit_toggle_min = ImGui::GetItemRectMin();
				custom_edit_toggle_max = ImGui::GetItemRectMax();
				if (custom_edit_open)
				{
					close_custom_editor(true);
				}
				else
				{
					memcpy(custom_preset_edit, custom_preset, sizeof(custom_preset));
					custom_edit_open = true;
					custom_edit_dirty = false;
					custom_edit_slot = 0;
					active_preset = -1;
				}
			}
			else
			{
				custom_edit_toggle_min = ImGui::GetItemRectMin();
				custom_edit_toggle_max = ImGui::GetItemRectMax();
			}
			if (!custom_edit_open)
			{
				ImGui::SameLine();
				ImGui::TextDisabled("Custom slot is session-scoped.");
			}

			if (custom_edit_open)
			{
				if (ImGui::IsKeyPressed(ImGuiKey_Escape))
					close_custom_editor(true);

				ImGui::TextDisabled("Custom slot: click atlas glyphs above to rewrite the selected column.");
				custom_edit_rect_min = ImGui::GetCursorScreenPos();
				ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(2, 2));
				for (int col = 0; col < MaterialGridCols(); col++)
				{
					uint8_t glyph = custom_preset_edit[col];
					float gx = (glyph & 0xF) / 16.0f;
					float gy = (glyph >> 4) / 16.0f;
					if (col == custom_edit_slot)
						ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
					ImGui::PushID(col + 9000);
					if (ImGui::ImageButton((void*)(intptr_t)font[active_font].tex,
						ImVec2(but16_w, but16_h),
						ImVec2(gx, gy), ImVec2(gx + 1 / 16.0f, gy + 1 / 16.0f),
						1, ImVec4(0, 0, 0, 0.75f), ImVec4(1, 1, 1, 1)))
					{
						custom_edit_slot = col;
						SelectActiveGlyphId(glyph);
					}
					if (ImGui::IsItemHovered())
					{
						char glyph_label[64];
						FormatGlyphLabel(glyph, glyph_label, sizeof(glyph_label));
						ImGui::SetTooltip("Column %d\n%s", col, glyph_label);
					}
					ImGui::PopID();
					if (col == custom_edit_slot)
						ImGui::PopStyleColor();
					if (col < MaterialGridCols() - 1)
						ImGui::SameLine();
				}
				ImGui::PopStyleVar();
				custom_edit_rect_max = ImGui::GetItemRectMax();
				if (ImGui::Button("Use Active Glyph For Selected"))
				{
					custom_preset_edit[custom_edit_slot] = (uint8_t)active_glyph;
					custom_edit_dirty = true;
				}
				ImGui::SameLine();
				if (ImGui::Button("Reset From Legible"))
				{
					for (int col = 0; col < MaterialGridCols(); col++)
						custom_preset_edit[col] = preset_legible[col * (int)(sizeof(preset_legible) / sizeof(preset_legible[0])) / MaterialGridCols()];
					custom_edit_dirty = true;
				}
				ImGui::SameLine();
				ImGui::TextDisabled(custom_edit_dirty ? "Dismiss saves to the in-session Custom slot." : "Dismiss keeps the current in-session Custom slot.");

				if (ImGui::IsMouseClicked(0))
				{
					ImVec2 mouse = ImGui::GetMousePos();
					bool inside_custom = PointInRect(mouse, custom_edit_rect_min, custom_edit_rect_max);
					bool inside_atlas = PointInRect(mouse, atlas_rect_min, atlas_rect_max);
					bool inside_toggle = PointInRect(mouse, custom_edit_toggle_min, custom_edit_toggle_max);
					if (!inside_custom && !inside_atlas && !inside_toggle)
						close_custom_editor(true);
				}
			}

		};

			auto render_palette_owner = [&]()
		{
			ImVec2 palette_grid_min(0, 0), palette_grid_max(0, 0);
			ImVec2 palette_toggle_min(0, 0), palette_toggle_max(0, 0);

			auto finish_palette_edit = [&](bool commit)
			{
				if (!g_palette_edit_active || g_palette_edit_palette_index < 0)
					return;
				int palette_index = g_palette_edit_palette_index;
				if (!commit)
				{
					memcpy(pal[palette_index].rgb, g_palette_edit_backup, sizeof(g_palette_edit_backup));
					g_theme_modified = g_palette_edit_theme_modified_backup;
				}
				else if (memcmp(g_palette_edit_backup, pal[palette_index].rgb, sizeof(g_palette_edit_backup)) != 0)
				{
					uint8_t final_palette[3 * 256];
					memcpy(final_palette, pal[palette_index].rgb, sizeof(final_palette));
					memcpy(pal[palette_index].rgb, g_palette_edit_backup, sizeof(g_palette_edit_backup));
					URDO_Open();
					URDO_Palette(palette_index);
					memcpy(pal[palette_index].rgb, final_palette, sizeof(final_palette));
					URDO_Close();
				}
				g_palette_edit_active = false;
				g_palette_edit_palette_index = -1;
			};

			if (!palettes_loaded)
				return;
			if (!ImGui::CollapsingHeader("Palette"))
				return;
			CaptureThemeSessionBaseline();

			if (g_last_palette_index != active_palette)
			{
				finish_palette_edit(false);
				g_palette_expanded = false;
				g_palette_selected = 0;
				g_last_palette_index = active_palette;
				if (g_has_theme_backup && g_theme_backup_palette_index != active_palette)
				{
					g_active_theme_index = -1;
					g_theme_backup_palette_index = -1;
					g_has_theme_backup = false;
					g_theme_modified = false;
				}
			}
			if (g_palette_selected < 0)
				g_palette_selected = 0;
			if (g_palette_selected > 255)
				g_palette_selected = 255;
			if (g_palette_expanded && ImGui::IsKeyPressed(ImGuiKey_Escape))
			{
				if (g_palette_edit_active)
					finish_palette_edit(false);
				else
					g_palette_expanded = false;
			}

			render_palette_switcher("edit_palette_switcher");

			ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
			for (int x = 0; x < 16; x++)
			{
				int idx = x;
				ImVec4 tint(
					pal[active_palette].rgb[3 * idx + 0] / 255.0f,
					pal[active_palette].rgb[3 * idx + 1] / 255.0f,
					pal[active_palette].rgb[3 * idx + 2] / 255.0f,
					1.0f
				);
				if (idx == g_palette_selected)
					ImGui::PushStyleColor(ImGuiCol_Border, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
				ImGui::PushID(idx + 4096);
				if (ImGui::ColorButton("##palette_row0", tint, ImGuiColorEditFlags_NoTooltip | ImGuiColorEditFlags_NoDragDrop, ImVec2(but16_w, but16_h)))
					g_palette_selected = idx;
				if (ImGui::IsItemHovered())
					ImGui::SetTooltip("Select swatch %d for readout/editing. This does not paint terrain by itself.", idx);
				ImGui::PopID();
				if (idx == g_palette_selected)
					ImGui::PopStyleColor();
				if (x < 15)
					ImGui::SameLine();
			}
			ImGui::PopStyleVar();

			const uint8_t* selected_rgb = pal[active_palette].rgb + 3 * g_palette_selected;
			ImGui::Text("Selected: %d  #%02X%02X%02X", g_palette_selected, selected_rgb[0], selected_rgb[1], selected_rgb[2]);
			ImGui::SameLine();
			ImGui::ColorButton("##palette_selected", ImVec4(selected_rgb[0] / 255.0f, selected_rgb[1] / 255.0f, selected_rgb[2] / 255.0f, 1.0f));
			ImGui::SameLine();
			if (ImGui::Button(g_palette_expanded ? "Collapse Palette" : "Expand Palette"))
				g_palette_expanded = !g_palette_expanded;
			palette_toggle_min = ImGui::GetItemRectMin();
			palette_toggle_max = ImGui::GetItemRectMax();

			if (g_palette_expanded)
			{
				palette_grid_min = ImGui::GetCursorScreenPos();
				ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
				for (int y = 0; y < 16; y++)
				{
					for (int x = 0; x < 16; x++)
					{
						int idx = x + 16 * y;
						ImVec4 tint(
							pal[active_palette].rgb[3 * idx + 0] / 255.0f,
							pal[active_palette].rgb[3 * idx + 1] / 255.0f,
							pal[active_palette].rgb[3 * idx + 2] / 255.0f,
							1.0f
						);
						ImGui::PushID(idx + 256 + glyph_w * glyph_h);
						if (ImGui::ColorEdit3("", (float*)&tint, ImGuiColorEditFlags_NoInputs, ImVec2(but16_w + 2, but16_h + 2)))
						{
							pal[active_palette].rgb[3 * idx + 0] = (int)round(tint.x * 255);
							pal[active_palette].rgb[3 * idx + 1] = (int)round(tint.y * 255);
							pal[active_palette].rgb[3 * idx + 2] = (int)round(tint.z * 255);
							g_palette_selected = idx;
							if (g_active_theme_index >= 0)
								g_theme_modified = true;
						}
						if (ImGui::IsItemActivated() && !g_palette_edit_active)
						{
							memcpy(g_palette_edit_backup, pal[active_palette].rgb, sizeof(g_palette_edit_backup));
							g_palette_edit_theme_modified_backup = g_theme_modified;
							g_palette_edit_active = true;
							g_palette_edit_palette_index = active_palette;
						}
						if (ImGui::IsItemDeactivatedAfterEdit() && g_palette_edit_active)
							finish_palette_edit(true);
						ImGui::PopID();
						if (x < 15)
							ImGui::SameLine();
					}
				}
				ImGui::PopStyleVar();
				palette_grid_max = ImGui::GetItemRectMax();
			}

			auto snapshot_theme_undo = [&]()
			{
				URDO_Open();
				for (int m = 0; m < kPaletteCount; m++)
					URDO_Material(m);
				URDO_Palette(active_palette);
			};

			ImGui::Separator();
			ImGui::Text("Palette Themes");
			for (int i = 0; i < (int)(sizeof(g_palette_themes) / sizeof(g_palette_themes[0])); i++)
			{
				if (i > 0 && i != 4)
					ImGui::SameLine();
				bool selected = (g_active_theme_index == i);
				if (selected)
					ImGui::PushStyleColor(ImGuiCol_Button, ImGui::GetStyleColorVec4(ImGuiCol_ButtonActive));
				if (ImGui::Button(g_palette_themes[i].name))
				{
					snapshot_theme_undo();
					if (g_active_theme_index == i)
					{
						if (g_has_theme_backup)
						{
							for (int m = 0; m < kPaletteCount; m++)
							{
								memcpy(mat[m].shade, g_theme_saved_mat_backup[m], sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount);
								mat[m].Update();
							}
							memcpy(pal[active_palette].rgb, g_theme_saved_pal_backup, sizeof(g_theme_saved_pal_backup));
							g_has_theme_backup = false;
							g_theme_backup_palette_index = -1;
						}
						g_active_theme_index = -1;
						g_theme_modified = false;
					}
					else
					{
						if (!g_has_theme_backup)
						{
							for (int m = 0; m < kPaletteCount; m++)
								memcpy(g_theme_saved_mat_backup[m], mat[m].shade, sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount);
							memcpy(g_theme_saved_pal_backup, pal[active_palette].rgb, sizeof(g_theme_saved_pal_backup));
							g_has_theme_backup = true;
						}
						else
						{
							for (int m = 0; m < kPaletteCount; m++)
								memcpy(mat[m].shade, g_theme_saved_mat_backup[m], sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount);
						}

						g_active_theme_index = i;
						g_theme_backup_palette_index = active_palette;
						g_theme_modified = false;

						const uint8_t* theme_colors = CachedThemePalette(i);
						memcpy(pal[active_palette].rgb, theme_colors, 3 * kPaletteCount);

						for (int m = 0; m < kPaletteCount; m++)
						{
							for (int row = 0; row < MaterialGridRows(); row++)
							{
								for (int col = 0; col < MaterialGridCols(); col++)
								{
									MatCell* cell = MaterialGridCell(m, row, col);
									const uint8_t* fg_theme = ThemeRemapColor(cell->fg, g_palette_themes[i].fg, g_palette_themes[i].accent, theme_colors);
									const uint8_t* bg_theme = ThemeRemapColor(cell->bg, g_palette_themes[i].fg, g_palette_themes[i].accent, theme_colors);
									memcpy(cell->fg, fg_theme, 3);
									memcpy(cell->bg, bg_theme, 3);
								}
							}
							MaterialGridCommit(m);
						}
					}
					URDO_Close();
				}
				if (selected)
					ImGui::PopStyleColor();
			}

			if (g_active_theme_index >= 0)
			{
				const uint8_t* active_theme_palette = CachedThemePalette(g_active_theme_index);
				g_theme_modified = memcmp(active_theme_palette, pal[active_palette].rgb, 3 * kPaletteCount) != 0;
				ImGui::Text("%s", g_theme_modified ? "Theme modified" : "Theme applied — Ctrl+Z to revert");
				if (ImGui::Button("Revert to Session Start"))
				{
					if (g_theme_session_baseline_ready)
					{
						snapshot_theme_undo();
						for (int m = 0; m < kPaletteCount; m++)
						{
							memcpy(mat[m].shade, g_theme_session_start_mat[m], sizeof(MatCell) * kMaterialGridRowCount * kMaterialGridColCount);
							mat[m].Update();
						}
						memcpy(pal[active_palette].rgb, g_theme_session_start_pal[active_palette], sizeof(g_theme_session_start_pal[active_palette]));
						URDO_Close();
						g_has_theme_backup = false;
						g_theme_backup_palette_index = -1;
						g_theme_modified = false;
					}
					g_active_theme_index = -1;
				}
			}

			if (g_palette_expanded && ImGui::IsMouseClicked(0))
			{
				ImVec2 mouse = ImGui::GetMousePos();
				float scrollbar_threshold = ImGui::GetWindowPos().x + ImGui::GetWindowSize().x - ImGui::GetStyle().ScrollbarSize;
				bool on_window_scrollbar = mouse.x >= scrollbar_threshold;
				bool inside_grid = PointInRect(mouse, palette_grid_min, palette_grid_max);
				bool inside_toggle = PointInRect(mouse, palette_toggle_min, palette_toggle_max);
				if (!inside_grid && !inside_toggle && !on_window_scrollbar)
				{
					if (g_palette_edit_active)
						finish_palette_edit(true);
					g_palette_expanded = false;
				}
			}
		};

		auto render_options_owner = [&]()
		{
			if (!ImGui::CollapsingHeader("Options"))
				return;

			ImGui::Checkbox("Invert colors", &invert_material_preview);
			if (ImGui::IsItemHovered())
				ImGui::SetTooltip("Swaps foreground/background in the shade grid preview.\nDoes not affect 3D terrain rendering.");
			ImGui::Checkbox("Spin", &spin_anim);
			if (ImGui::Button("Reset"))
			{
				if (active_font != 0)
				{
					active_font = 0;
					TermResizeAll();
				}
				active_palette = 0;
				active_material = 0;
				spin_anim = false;
				invert_material_preview = false;
				shade_contrast_min = 0;
				shade_contrast_max = 15;
				ResetRowShadeContrast();
			}
			if (ImGui::IsItemHovered())
				ImGui::SetTooltip("Restore selection + display controls without mutating material/palette data");
		};

		auto render_mesh_owner = [&]()
		{
			if (!ImGui::CollapsingHeader("Mesh", ImGuiTreeNodeFlags_DefaultOpen))
				return;

			static MeshWidget mesh_widget;
			static TerrainPreviewWidget terrain_preview_widget;
			static bool bake_height = true;
			static bool bake_material = true;
			static bool bake_vertex_colors = true;
			static bool bake_overwrite_height = true;
			static bool bake_overwrite_material = true;
			static bool bake_solid_only = false;
			static float bake_ray_top = 70000.0f;

			int mesh_total = 0;
			int mesh_idx = 0;
			for (Mesh* m = GetFirstMesh(world); m; m = GetNextMesh(m))
			{
				mesh_total++;
				if (m == active_mesh)
					mesh_idx = mesh_total;
			}

			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("##edit_mesh_prev", ImGuiDir_Left))
			{
				Mesh* prev = GetPrevMesh(active_mesh);
				active_mesh = prev ? prev : GetLastMesh(world);
			}
			ImGui::SameLine(0.0f, spacing);
			if (ImGui::ArrowButton("##edit_mesh_next", ImGuiDir_Right))
			{
				Mesh* next = GetNextMesh(active_mesh);
				active_mesh = next ? next : GetFirstMesh(world);
			}
			ImGui::PopButtonRepeat();
			ImGui::SameLine();

			char mesh_name[256];
			if (active_mesh)
				GetMeshName(active_mesh, mesh_name, 256);
			else
				strcpy(mesh_name, "(none)");
			ImGui::Text("[%d/%d] %s", mesh_idx, mesh_total, mesh_name);

			if (!active_mesh)
			{
				ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "No meshes loaded");
				return;
			}

			ImGui::TextWrapped("This is the authoritative mesh owner for terrain appearance. Click terrain to place, Shift+click to select, Ctrl+click to delete.");
			ImGui::TextWrapped("Arrow keys nudge the selected instance while mesh placement mode is active. Shift = fine move, Ctrl+Up/Down = Z.");

			float widget_w = ImGui::GetContentRegionAvail().x;
			if (widget_w > 340.0f)
				widget_w = 340.0f;
			if (widget_w < 180.0f)
				widget_w = ImGui::GetContentRegionAvail().x;

			mesh_widget.Widget("edit_mesh_widget", ImVec2(widget_w, 180.0f));

			if (edit_mode == 2)
			{
				if (ImGui::Button("Leave Mesh Placement"))
				{
					printf("[EDITOR] [FL-3714] mesh_panel_action=leave_mesh_placement previous_edit_mode=%d\n", edit_mode);
					fflush(stdout);
					g_explicit_mesh_placement_mode = false;
					edit_mode = 0;
				}
				ImGui::SameLine();
				ImGui::Text("Mesh placement mode active");
			}
			else
			{
				if (ImGui::Button("Activate Mesh Placement"))
				{
					printf("[EDITOR] [FL-3714] mesh_panel_action=activate_mesh_placement previous_edit_mode=%d active_mesh=%d\n",
						edit_mode,
						active_mesh ? 1 : 0);
					fflush(stdout);
					g_explicit_mesh_placement_mode = true;
					edit_mode = 2;
				}
			}

			const char* mode = "";
			if (io.KeyAlt)
				mode = "ADD/REMOVE TILES";
			else if (io.KeyCtrl)
				mode = "DELETE MESH";
			else
				mode = "INSERT MESH";
			ImGui::Text("Viewport mode (ctrl/alt): %s", mode);

			extern int bsp_insts, bsp_nodes, bsp_tests;
			ImGui::Text("INSTS:%d, NODES:%d, TESTS:%d", bsp_insts, bsp_nodes, bsp_tests);

			MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(active_mesh);
			ImGui::SliderFloat3("ScaleValue", mp->scale_val, -5, +5);
			ImGui::SliderFloat3("ScaleRand", mp->scale_rnd, 0, 1);
			ImGui::Separator();
			ImGui::SliderFloat("RotateLocZValue", &mp->rotate_locZ_val, -180, 180);
			ImGui::SliderFloat("RotateLocZRand", &mp->rotate_locZ_rnd, 0, 1);
			ImGui::Separator();
			ImGui::SliderFloat2("RotateXYValue", mp->rotate_XY_val, -180, +180);
			ImGui::SliderFloat2("RotateXYRand", mp->rotate_XY_rnd, 0, 1);
			ImGui::Separator();
			ImGui::SliderFloat("RotateAlign", &mp->rotate_align, 0, 1);
			ImGui::SliderFloat("Height", &mp->height, -500, 500);

			ImGui::Separator();
			ImGui::Text("Bake Meshes to Terrain");
			ImGui::Checkbox("Bake Height", &bake_height);
			ImGui::Checkbox("Bake Material", &bake_material);
			ImGui::Checkbox("Bake Vertex Colors", &bake_vertex_colors);
			if (ImGui::IsItemHovered())
				ImGui::SetTooltip("Bake mesh vertex colors into terrain materials (allocates new materials if needed).");
			ImGui::Checkbox("Overwrite Height", &bake_overwrite_height);
			ImGui::Checkbox("Overwrite Material", &bake_overwrite_material);
			ImGui::Checkbox("Solid Only (Alpha)", &bake_solid_only);
			ImGui::SliderFloat("Ray Top", &bake_ray_top, 1000.0f, 120000.0f);
			ImGui::Text("Material ID: 0x%02X (%d)", active_material, active_material);
			if (ImGui::Button("Bake Meshes"))
			{
				BakeConfirmParams bp = { bake_height, bake_material, bake_vertex_colors, bake_overwrite_height,
					bake_overwrite_material, bake_solid_only, bake_ray_top, (uint8_t)active_material };
				RequestConfirm("Bake all mesh instances onto terrain?", ConfirmBakeMeshes, &bp, sizeof(bp));
			}
			ImGui::SameLine();
			if (ImGui::Button("Delete All Meshes"))
				RequestConfirm("Delete ALL mesh instances?", ConfirmDeleteAllMeshInsts);
			if (ImGui::Button("Clear Selection"))
				ClearSelection();
			if (ImGui::IsItemHovered())
				ImGui::SetTooltip("Clear the current mesh-instance selection without deleting anything.");
			ImGui::SameLine();
			if (ImGui::Button("Delete Selected"))
				DeleteSelected();
			if (ImGui::IsItemHovered())
				ImGui::SetTooltip("Delete currently selected instances. Shift+Drag to select area.");

			ImGui::Separator();
			ImGui::TextDisabled("Fixed sample terrain scene preview");
			ImGui::TextWrapped("Updates live while glyph presets, material ramps, palette edits, and themes change. The main land uses the active material; side accents use neighboring material slots.");
			terrain_preview_widget.Widget("terrain_appearance_preview", ImVec2(widget_w, 150.0f));
		};

		auto render_materials_owner = [&]()
		{
			static bool paint_mat_glyph = true;
			static bool paint_mat_foreground = true;
			static bool paint_mat_background = true;
			static float paint_mat_fg[3] = { .2f, .3f, .4f };
			static float paint_mat_bg[3] = { .2f, .2f, .1f };
			// FL-4131 P5: default Raw Cells open so per-cell paint is
			// reachable without an extra discovery click. The per-cell
			// paint path is the canonical "paint the active GlyphId into
			// one cell" surface.
			static bool raw_cells_open = true;
			static MatCell mat_clip[kMaterialGridColCount] = { 0 };
			static const char* elev_label[kMaterialGridRowCount] = { "FLAT  ", "GENTLE", "SLOPE ", "STEEP " };
			static const char* lab[kMaterialGridRowCount][4] =
			{
				{"Cp##0","Ps##0","<<##0",">>##0"},
				{"Cp##1","Ps##1","<<##1",">>##1"},
				{"Cp##2","Ps##2","<<##2",">>##2"},
				{"Cp##3","Ps##3","<<##3",">>##3"}
			};
			static bool fg_drag_active[kMaterialGridRowCount] = { false, false, false, false };
			static bool bg_drag_active[kMaterialGridRowCount] = { false, false, false, false };
			static MatCell fg_drag_backup[kMaterialGridRowCount][kMaterialGridColCount];
			static MatCell bg_drag_backup[kMaterialGridRowCount][kMaterialGridColCount];
			static float fg_drag_value[kMaterialGridRowCount] = { 0, 0, 0, 0 };
			static float bg_drag_value[kMaterialGridRowCount] = { 0, 0, 0, 0 };

			if (!fonts_loaded)
				return;
			if (!ImGui::CollapsingHeader("Materials", ImGuiTreeNodeFlags_DefaultOpen))
				return;

			auto commit_row_drag = [&](int row, bool foreground, bool active_flags[kMaterialGridRowCount], MatCell backup_rows[kMaterialGridRowCount][kMaterialGridColCount])
			{
				(void)foreground;
				if (!active_flags[row])
					return;
				MatCell final_row[kMaterialGridColCount];
				CopyMaterialRow(active_material, row, final_row);
				bool changed = !MaterialRowEquals(final_row, backup_rows[row]);
				if (changed)
				{
					memcpy(MaterialGridCell(active_material, row, 0), backup_rows[row], sizeof(MatCell) * MaterialGridCols());
					URDO_Open();
					URDO_Material(active_material);
					memcpy(MaterialGridCell(active_material, row, 0), final_row, sizeof(MatCell) * MaterialGridCols());
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				else
				{
					memcpy(MaterialGridCell(active_material, row, 0), backup_rows[row], sizeof(MatCell) * MaterialGridCols());
					MaterialGridCommit(active_material);
				}
				active_flags[row] = false;
			};

			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("##edit_mat_left", ImGuiDir_Left)) { if (active_material > 0) active_material--; }
			ImGui::SameLine(0.0f, spacing);
			if (ImGui::ArrowButton("##edit_mat_right", ImGuiDir_Right)) { if (active_material < 0xff) active_material++; }
			ImGui::PopButtonRepeat();
			ImGui::SameLine();
			ImGui::Text("0x%02X (%d) Elevation ramps", active_material, active_material);

			ImGui::Separator();
			ImGui::TextDisabled("DISPLAY ADJUSTMENTS (view only)");
			ImGui::DragIntRange2("Shade Contrast", &shade_contrast_min, &shade_contrast_max, 1.0f, 0, 15);
			ImGui::TextDisabled("Column 0 = darkest, column 15 = brightest.");

			ImGui::Separator();
			ImGui::Text("Material data");
			ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
			for (int row = 0; row < MaterialGridRows(); row++)
			{
				ImGui::AlignTextToFramePadding();
				ImGui::Text("%s", elev_label[row]);
				ImGui::SameLine();
				for (int col = 0; col < MaterialGridCols(); col++)
				{
					const int display_col = clamp_shade_contrast_column(row, col);
					const MatCell* display_cell = MaterialGridCellConst(active_material, row, display_col);
					GlyphId display_glyph_id = MaterialGridGlyphIdConst(active_material, row, display_col);
					const uint8_t* display_fg = 0;
					const uint8_t* display_bg = 0;
					ComputeDisplayColors(display_cell, &display_fg, &display_bg);
					ImGui::PushID(col + row * 16 + 2048);
					if (display_glyph_id > 0xFF)
					{
						DrawAsciiidExtendedGlyphButton(
							display_glyph_id,
							ImVec2(but16_w, but16_h),
							display_fg,
							display_bg);
					}
					else
					{
						float gx = (display_cell->gl & 0xF) / 16.0f;
						float gy = (display_cell->gl >> 4) / 16.0f;
						ImGui::ImageButton((void*)(intptr_t)font[active_font].tex,
							ImVec2(but16_w, but16_h),
							ImVec2(gx, gy), ImVec2(gx + 1 / 16.0f, gy + 1 / 16.0f),
							1, ImVec4(display_bg[0] / 255.f, display_bg[1] / 255.f, display_bg[2] / 255.f, 1),
							ImVec4(display_fg[0] / 255.f, display_fg[1] / 255.f, display_fg[2] / 255.f, 1));
					}
					if (ImGui::IsItemHovered())
						ImGui::SetTooltip("Preview only. Use the row controls below or open Raw Cells for authored edits.");
					ImGui::PopID();
					if (col < MaterialGridCols() - 1)
						ImGui::SameLine();
				}
			}
			ImGui::PopStyleVar();

			// FL-4131 P5: shade-column fill. Each shade column gets a thin
			// button that writes the active GlyphId into every elevation row
			// at that column. Mirrors the per-row fill image button below.
			ImGui::Separator();
			ImGui::Text("Shade-column fill (writes active GlyphId across all elevations)");
			ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
			ImGui::Text("      ");
			ImGui::SameLine();
			for (int col = 0; col < MaterialGridCols(); col++)
			{
				ImGui::PushID(col + 8500);
				char shade_label[8];
				snprintf(shade_label, sizeof(shade_label), "%X", col & 0xF);
				if (ImGui::Button(shade_label, ImVec2(but16_w, but16_h)))
				{
					URDO_Open();
					URDO_Material(active_material);
					for (int row = 0; row < MaterialGridRows(); row++)
						MaterialGridSetGlyphId(active_material, row, col, active_glyph_id);
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				if (ImGui::IsItemHovered())
				{
					char glyph_label[64];
					FormatGlyphIdLabel(active_glyph_id, glyph_label, sizeof(glyph_label));
					ImGui::SetTooltip("Fill shade column %X with active glyph: %s", col & 0xF, glyph_label);
				}
				ImGui::PopID();
				if (col < MaterialGridCols() - 1)
					ImGui::SameLine();
			}
			ImGui::PopStyleVar();

			ImGui::Separator();
			ImGui::Text("Row controls");
			for (int row = 0; row < MaterialGridRows(); row++)
			{
				ImGui::PushID(row + 7000);
				ImGui::Text("%s", elev_label[row]);
				ImGui::SameLine();

				const MatCell* sample_cell = MaterialGridCellConst(active_material, row, MaterialGridCols() / 2);
				float glyph_x = (sample_cell->gl & 0xF) / 16.0f;
				float glyph_y = (sample_cell->gl >> 4) / 16.0f;
				if (ImGui::ImageButton((void*)(intptr_t)font[active_font].tex,
					ImVec2(but16_w, but16_h),
					ImVec2(glyph_x, glyph_y), ImVec2(glyph_x + 1 / 16.0f, glyph_y + 1 / 16.0f),
					1, ImVec4(0, 0, 0, 0.75f), ImVec4(1, 1, 1, 1)))
				{
					URDO_Open();
					URDO_Material(active_material);
					for (int col = 0; col < MaterialGridCols(); col++)
						MaterialGridSetGlyphId(active_material, row, col, active_glyph_id);
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				if (ImGui::IsItemHovered())
				{
					char glyph_label[64];
					FormatGlyphIdLabel(active_glyph_id, glyph_label, sizeof(glyph_label));
					ImGui::SetTooltip("Apply active glyph across %s.\nCurrent active glyph: %s", elev_label[row], glyph_label);
				}
				ImGui::SameLine();
				if (ImGui::Button("Shift <"))
				{
					URDO_Open();
					URDO_Material(active_material);
					MaterialGridRotateRowLeft(active_material, row);
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				ImGui::SameLine();
				if (ImGui::Button("Shift >"))
				{
					URDO_Open();
					URDO_Material(active_material);
					MaterialGridRotateRowRight(active_material, row);
					MaterialGridCommit(active_material);
					URDO_Close();
				}

				float fg_pct = fg_drag_active[row] ? fg_drag_value[row] : CachedRowChannelPeakPercent(active_material, row, true);
				ImGui::PushItemWidth(145.0f);
				if (ImGui::SliderFloat("FG %", &fg_pct, 0.0f, 100.0f, "%.0f%%"))
				{
					if (!fg_drag_active[row])
					{
						CopyMaterialRow(active_material, row, fg_drag_backup[row]);
						fg_drag_active[row] = true;
					}
					fg_drag_value[row] = fg_pct;
					ApplyRowChannelPercentFromSnapshot(active_material, row, fg_drag_backup[row], true, fg_pct);
				}
				ImGui::PopItemWidth();
				if (ImGui::IsItemDeactivatedAfterEdit())
					commit_row_drag(row, true, fg_drag_active, fg_drag_backup);

				float bg_pct = bg_drag_active[row] ? bg_drag_value[row] : CachedRowChannelPeakPercent(active_material, row, false);
				ImGui::PushItemWidth(145.0f);
				if (ImGui::SliderFloat("BG %", &bg_pct, 0.0f, 100.0f, "%.0f%%"))
				{
					if (!bg_drag_active[row])
					{
						CopyMaterialRow(active_material, row, bg_drag_backup[row]);
						bg_drag_active[row] = true;
					}
					bg_drag_value[row] = bg_pct;
					ApplyRowChannelPercentFromSnapshot(active_material, row, bg_drag_backup[row], false, bg_pct);
				}
				ImGui::PopItemWidth();
				if (ImGui::IsItemDeactivatedAfterEdit())
					commit_row_drag(row, false, bg_drag_active, bg_drag_backup);

				ImGui::PushItemWidth(145.0f);
				ImGui::DragIntRange2("Contrast", &row_shade_contrast_min[row], &row_shade_contrast_max[row], 1.0f, 0, 15);
				ImGui::PopItemWidth();
				if (ImGui::IsItemHovered())
					ImGui::SetTooltip("Preview-only clamp for %s. Combine with the global Shade Contrast above.", elev_label[row]);
				ImGui::PopID();
			}

			// FL-4131 P4: extended material workspace (Fill + presets +
			// per-cell paint hint) lives inside the Materials section,
			// not in the Character tab. Implementation is the file-scope
			// AsciiidRenderExtendedMaterialWorkspace so render_materials_owner
			// can reach it across lambda-scope boundaries.
			AsciiidRenderExtendedMaterialWorkspace(but16_w);

			if (ImGui::Button(raw_cells_open ? "Hide Raw Cells" : "Raw Cells"))
				raw_cells_open = !raw_cells_open;

			if (!raw_cells_open)
				return;

			if (!font[active_font].tex)
			{
				ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "Font texture not loaded.");
				return;
			}

			if (active_material < 0 || active_material >= kPaletteCount)
				return;

			ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(0, 0));
			for (int row = 0; row < MaterialGridRows(); row++)
			{
				ImGui::Text("%s", elev_label[row]);
				ImGui::SameLine();
				for (int col = 0; col < MaterialGridCols(); col++)
				{
					const MatCell* display_cell = MaterialGridCellConst(active_material, row, col);
					GlyphId display_glyph_id = MaterialGridGlyphIdConst(active_material, row, col);
					MatCell* edit_cell = MaterialGridCell(active_material, row, col);
					const uint8_t* display_fg = 0;
					const uint8_t* display_bg = 0;
					ComputeDisplayColors(display_cell, &display_fg, &display_bg);
					ImGui::PushID(col + row * 16 + 512 + glyph_w * glyph_h);
					bool glyph_button_clicked = false;
					if (display_glyph_id > 0xFF)
					{
						glyph_button_clicked = DrawAsciiidExtendedGlyphButton(
							display_glyph_id,
							ImVec2(but16_w, but16_h),
							display_fg,
							display_bg);
					}
					else
					{
						float glyph_x = (display_cell->gl & 0xF) / 16.0f;
						float glyph_y = (display_cell->gl >> 4) / 16.0f;
						glyph_button_clicked = ImGui::ImageButton((void*)(intptr_t)font[active_font].tex,
							ImVec2(but16_w, but16_h),
							ImVec2(glyph_x, glyph_y), ImVec2(glyph_x + 1 / 16.0f, glyph_y + 1 / 16.0f),
							1, ImVec4(display_bg[0] / 255.f, display_bg[1] / 255.f, display_bg[2] / 255.f, 1),
							ImVec4(display_fg[0] / 255.f, display_fg[1] / 255.f, display_fg[2] / 255.f, 1));
					}
					if (glyph_button_clicked)
					{
						URDO_Open();
						URDO_Material(active_material);
						if (paint_mat_glyph)
							MaterialGridSetGlyphId(active_material, row, col, active_glyph_id);
						if (paint_mat_foreground)
						{
							edit_cell->fg[0] = (int)round(paint_mat_fg[0] * 255);
							edit_cell->fg[1] = (int)round(paint_mat_fg[1] * 255);
							edit_cell->fg[2] = (int)round(paint_mat_fg[2] * 255);
						}
						if (paint_mat_background)
						{
							edit_cell->bg[0] = (int)round(paint_mat_bg[0] * 255);
							edit_cell->bg[1] = (int)round(paint_mat_bg[1] * 255);
							edit_cell->bg[2] = (int)round(paint_mat_bg[2] * 255);
						}
						MaterialGridCommit(active_material);
						URDO_Close();
					}
					if (ImGui::IsItemClicked(1) && !io.MouseDown[0])
					{
						if (paint_mat_foreground)
						{
							paint_mat_fg[0] = edit_cell->fg[0] / 255.0f;
							paint_mat_fg[1] = edit_cell->fg[1] / 255.0f;
							paint_mat_fg[2] = edit_cell->fg[2] / 255.0f;
						}
						if (paint_mat_background)
						{
							paint_mat_bg[0] = edit_cell->bg[0] / 255.0f;
							paint_mat_bg[1] = edit_cell->bg[1] / 255.0f;
							paint_mat_bg[2] = edit_cell->bg[2] / 255.0f;
						}
						if (paint_mat_glyph)
							SelectActiveGlyphId(MaterialGridGlyphIdConst(active_material, row, col));
					}
					if (ImGui::BeginDragDropSource(ImGuiDragDropFlags_None))
					{
						int cookie = col + 16 * row;
						ImGui::SetDragDropPayload("DND_MAT_RAMPING", &cookie, sizeof(int));
						ImGui::Text("RAMPING");
						ImGui::EndDragDropSource();
					}
					if (ImGui::BeginDragDropTarget())
					{
						if (const ImGuiPayload* payload = ImGui::AcceptDragDropPayload("DND_MAT_RAMPING"))
						{
							IM_ASSERT(payload->DataSize == sizeof(int));
							int cookie = *(const int*)payload->Data;
							int x1 = cookie & 0xF;
							int y1 = cookie >> 4;
							int x2 = col;
							int y2 = row;
							if (y1 > y2) { int s = y1; y1 = y2; y2 = s; }
							if (x1 > x2) { int s = x1; x1 = x2; x2 = s; }
							URDO_Open();
							URDO_Material(active_material);
							for (int dy = y1; dy <= y2; dy++)
							{
								MatCell c1 = *MaterialGridCell(active_material, dy, x1);
								MatCell c2 = *MaterialGridCell(active_material, dy, x2);
								GlyphId g1 = MaterialGridGlyphIdConst(active_material, dy, x1);
								GlyphId g2 = MaterialGridGlyphIdConst(active_material, dy, x2);
								for (int dx = x1 + 1; dx < x2; dx++)
								{
									MatCell* c = MaterialGridCell(active_material, dy, dx);
									float w = (float)(dx - x1) / (float)(x2 - x1);
									if (paint_mat_foreground)
									{
										c->fg[0] = (int)roundf(c1.fg[0] * (1 - w) + c2.fg[0] * w);
										c->fg[1] = (int)roundf(c1.fg[1] * (1 - w) + c2.fg[1] * w);
										c->fg[2] = (int)roundf(c1.fg[2] * (1 - w) + c2.fg[2] * w);
									}
									if (paint_mat_background)
									{
										c->bg[0] = (int)roundf(c1.bg[0] * (1 - w) + c2.bg[0] * w);
										c->bg[1] = (int)roundf(c1.bg[1] * (1 - w) + c2.bg[1] * w);
										c->bg[2] = (int)roundf(c1.bg[2] * (1 - w) + c2.bg[2] * w);
									}
									if (paint_mat_glyph)
										MaterialGridSetGlyphId(active_material, dy, dx, (dx - x1 < x2 - dx) ? g1 : g2);
								}
							}
							MaterialGridCommit(active_material);
							URDO_Close();
						}
						ImGui::EndDragDropTarget();
					}
					ImGui::PopID();
					ImGui::SameLine();
				}

				ImGui::SameLine();
				if (ImGui::Button(lab[row][0]))
					memcpy(mat_clip, MaterialGridCell(active_material, row, 0), sizeof(MatCell) * MaterialGridCols());
				if (ImGui::IsItemHovered()) ImGui::SetTooltip("Copy this row's 16 shade cells");
				ImGui::SameLine();
				if (ImGui::Button(lab[row][1]))
				{
					URDO_Open();
					URDO_Material(active_material);
					memcpy(MaterialGridCell(active_material, row, 0), mat_clip, sizeof(MatCell) * MaterialGridCols());
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				if (ImGui::IsItemHovered()) ImGui::SetTooltip("Paste copied cells into this row");
				ImGui::SameLine();
				if (ImGui::Button(lab[row][2]))
				{
					URDO_Open();
					URDO_Material(active_material);
					MaterialGridRotateRowLeft(active_material, row);
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				if (ImGui::IsItemHovered()) ImGui::SetTooltip("Shift ramp left (rotate shade transition)");
				ImGui::SameLine();
				if (ImGui::Button(lab[row][3]))
				{
					URDO_Open();
					URDO_Material(active_material);
					MaterialGridRotateRowRight(active_material, row);
					MaterialGridCommit(active_material);
					URDO_Close();
				}
				if (ImGui::IsItemHovered()) ImGui::SetTooltip("Shift ramp right (rotate shade transition)");
			}
			ImGui::PopStyleVar();

			ImGui::Separator();
			ImGui::Checkbox("Glyph", &paint_mat_glyph); ImGui::SameLine();
			{
				char glyph_label[64];
				FormatGlyphIdLabel(active_glyph_id, glyph_label, sizeof(glyph_label));
				ImGui::Text("%s", glyph_label);
			}
			ImGui::Checkbox("Foreground", &paint_mat_foreground); ImGui::SameLine(); ImGui::ColorEdit3("###FG", paint_mat_fg);
			ImGui::Checkbox("Background", &paint_mat_background); ImGui::SameLine(); ImGui::ColorEdit3("###BG", paint_mat_bg);
		};

		// Instances cache — kept outside the panel so count stays current every frame.
		static Inst** cached_insts = 0;
		static int cached_count = 0;
		static int last_clicked_inst = -1;
		if (world && inst_list_dirty) {
			if (cached_insts) free(cached_insts);
			cached_count = CollectMeshInsts(world, &cached_insts);
			inst_list_dirty = false;
		}

		ImGui::SetNextWindowPos(ImVec2(0, 0), ImGuiCond_Always);
		float sidebar_width = fminf(420.0f, io.DisplaySize.x * 0.45f);
		ImGui::SetNextWindowSize(ImVec2(sidebar_width, io.DisplaySize.y), ImGuiCond_Always);
		ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 0.0f);
		ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 0.0f);
		ImGui::Begin("##sidebar", nullptr,
			ImGuiWindowFlags_NoMove        |
			ImGuiWindowFlags_NoResize      |
			ImGuiWindowFlags_NoTitleBar    |
			ImGuiWindowFlags_NoCollapse    |
			ImGuiWindowFlags_NoBringToFrontOnFocus);
		ImGui::PopStyleVar(2);

		if (ImGui::BeginTabBar("##sidebar_tabs", ImGuiTabBarFlags_None))
		{
			// Do not force ImGuiTabItemFlags_SetSelected from sidebar_tab every
			// frame. Doing so pins the previous tab and prevents normal clicks
			// from switching away from EDIT.
			if (ImGui::BeginTabItem("EDIT"))   { sidebar_tab = 1; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("VIEW"))   { sidebar_tab = 0; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("SPRITE")) { sidebar_tab = 2; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("MESH"))   { sidebar_tab = 3; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("INST"))   { sidebar_tab = 4; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("FONT"))   { sidebar_tab = 5; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("SKIN"))   { sidebar_tab = 6; ImGui::EndTabItem(); }
			if (ImGui::BeginTabItem("INFO"))   { sidebar_tab = 7; ImGui::EndTabItem(); }
			ImGui::EndTabBar();
		}
		g_asciiid_sidebar_tab_debug = sidebar_tab;

		// ── TAB CONTENT ──────────────────────────────────────────────────────────────
		if (sidebar_tab == 2) // SPRITE
		{
			static SpriteWidget sw;
			static ImGuiTextFilter sprite_filter;
			static bool combo_open = false;

			char cur_name[256];
			GetSpriteName(active_sprite, cur_name, 256);

			// Search filter
			sprite_filter.Draw("Search##sprite_filter", 200);

			// Searchable dropdown combo
			if (ImGui::BeginCombo("##sprite_combo", cur_name))
			{
				for (Sprite* s = GetFirstSprite(false); s; s = GetNextSprite(s, false))
				{
					char sname[256];
					GetSpriteName(s, sname, 256);
					if (sprite_filter.PassFilter(sname))
					{
						bool selected = (s == active_sprite);
						if (ImGui::Selectable(sname, selected))
							active_sprite = s;
						if (selected)
							ImGui::SetItemDefaultFocus();
					}
				}
				ImGui::EndCombo();
			}

			// Arrow buttons with Repeater
			float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
			ImGui::PushButtonRepeat(true);
			if (ImGui::ArrowButton("##sprite_prev", ImGuiDir_Left))
			{
				Sprite* prev = GetPrevSprite(active_sprite,false);
				if (prev)
					active_sprite = prev;
			}

			ImGui::SameLine(0.0f, spacing);

			if (ImGui::ArrowButton("##sprite_next", ImGuiDir_Right))
			{
				Sprite* next = GetNextSprite(active_sprite,false);
				if (next)
					active_sprite = next;
			}
			ImGui::PopButtonRepeat();
			ImGui::SameLine();
			ImGui::Text("%s", cur_name);

			sw.Widget("sprite_zonk", ImVec2(320, 320));
		}

		if (sidebar_tab == 3) // MESH
		{
			ImGui::TextDisabled("LEGACY");
			ImGui::TextWrapped("MESH is no longer the authoritative terrain-appearance owner.");
			ImGui::TextWrapped("Use EDIT -> Mesh for mesh browsing, placement controls, and the compact terrain preview.");
		}

		if (sidebar_tab == 4) // INST (statics and cache update hoisted before sidebar Begin)
		{
			if (world)
			{
				if (ImGui::Button("Refresh")) inst_list_dirty = true;
				ImGui::SameLine();
				ImGui::Text("%d instances", cached_count);

				ImGui::BeginChild("inst_scroll", ImVec2(0, 0), true);
				for (int i = 0; i < cached_count; i++)
				{
						// Show mesh name (e.g. "tree-3.akm") instead of instance name (usually empty)
						char mesh_name[256] = "???";
					Mesh* inst_mesh = GetInstMesh(cached_insts[i]);
					if (inst_mesh) GetMeshName(inst_mesh, mesh_name, 256);
					double inst_tm[16]; float x=0,y=0,z=0;
					if (GetInstTM(cached_insts[i], inst_tm)) { x=(float)inst_tm[12]; y=(float)inst_tm[13]; z=(float)inst_tm[14]; }
					bool is_sel = (GetInstFlags(cached_insts[i]) & INST_SELECTED) != 0;

					char label[512];
					snprintf(label, 512, "[%d] %s  (%.1f, %.1f, %.1f)", i, mesh_name, x, y, z);

					if (ImGui::Selectable(label, is_sel))
					{
						if (io.KeyShift)
						{
								// Shift+click: range select (additive, never clears)
								int anchor = (last_clicked_inst >= 0 && last_clicked_inst < cached_count)
								? last_clicked_inst : i;
							int lo = std::min(anchor, i);
							int hi = std::max(anchor, i);
							for (int r = lo; r <= hi; r++)
								SetInstFlags(cached_insts[r], GetInstFlags(cached_insts[r]) | INST_SELECTED);
							selected_inst = cached_insts[i];
							active_mesh = GetInstMesh(cached_insts[i]);
						}
						else if (io.KeyCtrl)
						{
								// Ctrl+click: toggle this one
								if (is_sel)
								SetInstFlags(cached_insts[i], GetInstFlags(cached_insts[i]) & ~INST_SELECTED);
							else
							{
								SetInstFlags(cached_insts[i], GetInstFlags(cached_insts[i]) | INST_SELECTED);
								selected_inst = cached_insts[i];
								active_mesh = GetInstMesh(cached_insts[i]);
							}
						}
						else
						{
								// Plain click: exclusive select
								ClearSelection();
							SetInstFlags(cached_insts[i], GetInstFlags(cached_insts[i]) | INST_SELECTED);
							selected_inst = cached_insts[i];
							active_mesh = GetInstMesh(cached_insts[i]);
						}
						last_clicked_inst = i;
							inst_list_dirty = true;
						}
				}
				ImGui::EndChild();
			}
		}

		static int save = 0; // 0-no , 1-save, 2-save_as
		static DirItem** dir_arr = 0;
		static char save_path[4096]="";
		static bool save_overwrite_confirm = false;
		static char save_overwrite_path[4096] = "";

		if (sidebar_tab == 0) // VIEW
		{
		ImGui::Text("Map: %s", g_current_map_path[0] ? BasenamePtr(g_current_map_path) : "Unsaved / in-memory");
		if (g_current_map_path[0] && ImGui::IsItemHovered())
			ImGui::SetTooltip("%s", g_current_map_path);
		ImGui::Text("VT HEAP Ops: %d", last_heap_ops);

		int xywh[4],wh[2];
		a3dGetRect(wnd, xywh, wh);
		ImGui::Text("%d,%d,%d,%d %d,%d %s",
			xywh[0], xywh[1], xywh[2], xywh[3],
			wh[0], wh[1], a3dIsMaximized(wnd) ? "MAXIMIZED" : "normal");

		if (ImGui::Button(io.KeyShift ? "DEPALETTIZE" : "PALETTIZE"))
		{
			Palettize(io.KeyShift ? 0 : pal[active_palette].rgb);
		}

#ifdef DARK_TERRAIN
		/* for every (maybe currently on screen?) terrain visual or maybe height sample
		   calculate minimum distance over this terrain sample required
		   to see the sun (unoccluded by both terrain and meshes)
		   store that distance in 7bit shade part of visual
		   possibly in linear (max 127) or exponential form (max base^127)
		*/

		if (ImGui::Button("CAST SHADOWS"))
		{
			RequestConfirm("Cast shadows on entire map? This may take a while on large maps.", ConfirmCastShadows);
		}
#endif

		if (!save)
		{
			if (ImGui::Button("SAVE AS"))
			{
				save = 1;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;

				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;

				if (g_current_map_path[0])
				{
					char current_dir[4096];
					CopyParentDir(current_dir, (int)sizeof(current_dir), g_current_map_path);
					if (current_dir[0])
						a3dSetCurDir(current_dir);
					CopyClamped(save_path, (int)sizeof(save_path), g_current_map_path);
				}
				else
				{
					a3dGetCurDir(save_path,4096);
				}
				AllocDir(&dir_arr);
			}

			ImGui::SameLine();

			if (ImGui::Button("LOAD"))
			{
				save = 2;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;

				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;

				a3dGetCurDir(save_path,4096);
				AllocDir(&dir_arr);
			}

			ImGui::SameLine();

			if (ImGui::Button("MERGE"))
			{
				save = 3;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;

				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;

				a3dGetCurDir(save_path, 4096);
				AllocDir(&dir_arr);
			}

			ImGui::SameLine();


			if (ImGui::Button("NEW"))
			{
				RequestConfirm("Delete entire map and start new?", ConfirmNew);
			}

			ImGui::SameLine();

			if (ImGui::Button("TERM++"))
			{
				float pos[3] = { pos_x,pos_y,pos_z };
				ComputeLoadedMapTermSpawn(pos);
				TermOpen(wnd, rot_yaw, pos);
			}

			ImGui::SameLine();

			// FL-728: TERM++ SKIN — cycle player skin through available V2
			// bundle skin families and hot-swap all running TERM windows.
			if (ImGui::Button("TERM++ SKIN"))
			{
				uint16_t skin_ids[16];
				int skin_count = GameGetBundleSkinIds(skin_ids, 16);
				if (skin_count > 0)
				{
					// Cycle: find current skin id, advance to next.
					static int skin_cycle_index = 0;
					skin_cycle_index = (skin_cycle_index + 1) % skin_count;
					uint16_t next_skin = skin_ids[skin_cycle_index];
					g_term_skin_requested_id = next_skin;
					float pos[3] = { pos_x, pos_y, pos_z };
					int hot = ApplyTermSkinSelection(wnd, rot_yaw, pos, next_skin);

					if (hot > 0)
						printf("[EDITOR] TERM++ SKIN: applied skin_id=%u (%d window%s)\n",
							(unsigned)next_skin, hot, hot == 1 ? "" : "s");
					else
						printf("[EDITOR] TERM++ SKIN: failed to apply skin_id=%u\n", (unsigned)next_skin);
				}
				else
				{
					printf("[EDITOR] TERM++ SKIN: no skins available in bundle\n");
				}
			}

		}
		else
		{
			if (ImGui::Button("Cancel"))
			{
				if (save == 3)
					MergeCancel();

				save = 0;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;
				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;
			}
		}


		if (ImGui::Button("FULL"))
		{
			a3dSetRect(wnd, 0, A3D_WND_FULLSCREEN);
		}
		ImGui::SameLine();
		if (ImGui::Button("NORM"))
		{
			a3dSetRect(wnd, 0, A3D_WND_NORMAL);
		}
		ImGui::SameLine();
		if (ImGui::Button("PURE"))
		{
			a3dSetRect(wnd, 0, A3D_WND_FRAMELESS);
		}
		ImGui::SameLine();
		if (ImGui::Button("KEEP"))
		{
			int r[4];
			WndMode mode = a3dGetRect(wnd, r, 0);
			a3dSetRect(wnd, r, mode);
		}

		ImGui::SameLine();
		if (ImGui::Button("COVERAGE"))
		{
			int width = font[active_font].width;
			int height = font[active_font].height;
			uint8_t* img = (uint8_t*)malloc(width*height);
			gl3GetTextureSubImage(font[active_font].tex, 0, 0, 0, 0, width, height, 1, GL_ALPHA, GL_UNSIGNED_BYTE, width*height, img);

			int cw = width / 32;
			int ch = height / 32;

			int cov[32][32] = {{0}};

			for (int y = 0; y < height; y++)
			{
				int cy = y / ch;
				for (int x = 0; x < width; x++)
				{
					int cx = x / cw;
					cov[cy][cx] += img[y*width+x];
				}
			}

			int denom = 255 * (width >> 5)*(height >> 5) / 4;

			for (int cy=0; cy<32; cy++)
				for (int cx = 0; cx < 32; cx++)
					cov[cy][cx] = (cov[cy][cx] + (denom>>1)) / denom;

			for (int cy = 0; cy < 32; cy += 2)
			{
				for (int cx = 0; cx < 32; cx += 2)
				{
					// flip upper/lower
					printf("0x%d%d%d%d,", cov[cy][cx+1], cov[cy][cx], cov[cy+1][cx+1], cov[cy+1][cx]);
				}
				printf("\n");
			}

			printf("--------\n");
			printf("darken\n");
			for (int j = 0; j < 16; j++)
			{
				for (int i = 0; i < 16; i++)
				{
					int v = j * 16 + i;
					if (v < 16 || v >= 16 + 6 * 6 * 6)
					{
						printf("0xFF,");
						continue;
					}

					int c = v - 16;
					int cr = c / 36;
					c -= cr * 36;
					int cg = c / 6;
					c -= cr * 6;
					int cb = c;

					cr = cr ? cr - 1 : 0;
					cg = cg ? cg - 1 : 0;
					cb = cb ? cb - 1 : 0;

					v = 16 + cb + cg * 6 + cr * 36;

					printf("0x%02X,",v);
				}
				printf("\n");
			}

			free(img);
		}


		// ====================================================================
		// VIEW CONTROL SECTION
		// Camera and rendering parameters
		// ====================================================================
		if (ImGui::CollapsingHeader("View Control", ImGuiTreeNodeFlags_DefaultOpen))
		{
			// Camera pitch angle (vertical rotation)
				ImGui::SliderFloat("VIEW PITCH", &rot_pitch, +1.0f, +90.0f);
				if (ImGui::IsItemHovered()) ImGui::SetTooltip("Camera vertical angle (1-90 deg). Hold Right Mouse Button to rotate.");

				ImGui::SliderFloat("VIEW YAW", &rot_yaw, -180.0f, +180.0f);

				ImGui::SliderFloat("ZOOM", &font_size, kMinInteractiveFontSize, 32.0f);
				ClampInteractiveFontSize();
			ImGui::SameLine();
			ImGui::SameLine();
			ImGui::Text("%dx%d", (int)round(io.DisplaySize.x/font_size), (int)round(io.DisplaySize.y / font_size));

			ImGui::SliderFloat("GRID", &grid_alpha, 0.0f, 1.0f);

			// Camera position + lat/lon readout
			ImGui::Text("Pos: %.0f, %.0f, %.0f", pos_x, pos_y, pos_z);
			if (g_osm_proj.valid) {
				double cam_lat, cam_lon;
				world_to_latlon(pos_x, pos_y, &cam_lat, &cam_lon);
				ImGui::Text("Lat/Lon: %.7f, %.7f", cam_lat, cam_lon);
			}

			if (ImGui::Button("FOCUS VIEW") && terrain)
			{
				int patch_count = 0;
				Patch** patches = 0;
				GetAllTerrainPatches(terrain, &patches, &patch_count);
				if (patch_count > 0)
				{
					int min_px = INT_MAX, max_px = INT_MIN;
					int min_py = INT_MAX, max_py = INT_MIN;
					for (int i = 0; i < patch_count; i++)
					{
						int px, py;
						GetTerrainPatch(terrain, patches[i], &px, &py);
						if (px < min_px) min_px = px;
						if (px > max_px) max_px = px;
						if (py < min_py) min_py = py;
						if (py > max_py) max_py = py;
					}
					// Center in visual-cell world space
					pos_x = (float)((min_px + max_px + 1) * VISUAL_CELLS) * 0.5f;
					pos_y = (float)((min_py + max_py + 1) * VISUAL_CELLS) * 0.5f;
					// Set camera height from center patch (same logic as ResetCameraForLoadedMap)
					int cx = (min_px + max_px) / 2;
					int cy = (min_py + max_py) / 2;
					Patch* cp = GetTerrainPatch(terrain, cx, cy);
					if (cp)
					{
						uint16_t* hmap = GetTerrainHeightMap(cp);
						int h = hmap[(HEIGHT_CELLS/2) * (HEIGHT_CELLS+1) + HEIGHT_CELLS/2];
						pos_z = (float)h;
						probe_z = h > 0x100 ? h - 0x100 : 0;
					}
					// Auto-zoom to fit the full terrain extent
					int span_x = (max_px - min_px + 1) * VISUAL_CELLS;
					int span_y = (max_py - min_py + 1) * VISUAL_CELLS;
					int max_span = span_x > span_y ? span_x : span_y;
					if (max_span > 0)
					{
						float display_min = io.DisplaySize.x < io.DisplaySize.y ? io.DisplaySize.x : io.DisplaySize.y;
						font_size = display_min / (float)max_span;
						ClampInteractiveFontSize();
					}
				}
				if (patches) free(patches);
			}
		}

		if (ImGui::CollapsingHeader("Stats", ImGuiTreeNodeFlags_DefaultOpen))
		{
			ImGui::Text("PATCHES: %d, DRAWS: %d, CHANGES: %d", render_context.patches, render_context.draws, render_context.changes);
			ImGui::Text("TERRAIN MODE: %s, OVERVIEW TILES: %d, REFRESHED: %d, DIRTY: %d, BUDGET SKIP: %d",
				render_context.overview_mode ? "overview" : "exact",
				render_context.overview_tiles,
				g_editor_terrain_overview.last_refreshed_tiles,
				g_editor_terrain_overview.last_dirty_remaining,
				render_context.patches_budget_skipped);
			ImGui::Text("RENDER TIME: %6jd [" /*micro*/"\xc2\xb5"/*utf8*/ "s]", render_context.render_time);
			ImGui::Text("%zu BYTES", GetTerrainBytes(terrain));
		}

		if (ImGui::CollapsingHeader("Light Control", ImGuiTreeNodeFlags_DefaultOpen))
		{
			ImGui::SliderFloat("NOON PITCH", &lit_pitch, 0.0f, +90.0f);
			ImGui::SliderFloat("NOON YAW", &lit_yaw, -180.0f, +180.0f);
			ImGui::SliderFloat("LIGHT TIME", &lit_time, 0, 24);
			ImGui::SliderFloat("AMBIENCE", &ambience, 0, 1);

			/*
			ImGui::ColorEdit3("DAWN", dawn_color);
			ImGui::ColorEdit3("NOON", noon_color);
			ImGui::ColorEdit3("DUSK", dusk_color);
			ImGui::ColorEdit3("MIDNIGHT", midnight_color);
			*/
		}

		if (ImGui::CollapsingHeader("Weather"))
		{
			int ws = weather ? weather->state : 0;
			const char* weather_names = "CLEAR\0LIGHT_SNOW\0HEAVY_SNOW\0BLIZZARD\0\0";
			if (ImGui::Combo("Weather State", &ws, weather_names))
			{
				if (!weather) CreateWeather();
				SetWeather(ws);
			}
			if (weather)
			{
				ImGui::Text("Intensity: %.2f", weather->intensity);
				ImGui::Text("Snow Line: %.1f", weather->snow_line);
				ImGui::Text("Particles: %d/%d", weather->pool.count, ParticlePool::CAPACITY);
			}
		}

		} // VIEW tab content


		if (save)
		{
			bool save_do = false; // dbl click indicator
			bool show = true;
			static char save_error_text[256] = "";
			auto try_close_save_dialog = [&]()
			{
				save = 0;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;
				save_error_text[0] = 0;
				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;
			};
			auto try_save_map = [&]() -> bool
			{
				if (!save_path[0])
				{
					CopyClamped(save_error_text, (int)sizeof(save_error_text), "Save failed: choose a path first.");
					return false;
				}
				if (save_overwrite_confirm && strcmp(save_overwrite_path, save_path) != 0)
				{
					save_overwrite_confirm = false;
					save_overwrite_path[0] = 0;
				}
				if (FileExists(save_path) && (!save_overwrite_confirm || strcmp(save_overwrite_path, save_path) != 0))
				{
					save_overwrite_confirm = true;
					CopyClamped(save_overwrite_path, (int)sizeof(save_overwrite_path), save_path);
					save_error_text[0] = 0;
					return false;
				}
				if (SaveMapToPath(save_path))
				{
					save_error_text[0] = 0;
					try_close_save_dialog();
					return true;
				}
				snprintf(save_error_text, sizeof(save_error_text), "Save failed: %s", g_last_save_map_error[0] ? g_last_save_map_error : "check disk space or permissions");
				return false;
			};
			ImGui::Begin(save == 1 ? "SAVE" : save == 2 ? "LOAD" : "MERGE", &show);

			DirItem* cwd = 0;
			ImGui::PushItemWidth(-1);
			if (ImGui::InputText("###path",save_path,4096,ImGuiInputTextFlags_EnterReturnsTrue))
			{
				if (save == 1)
				{
					try_save_map();
				}
				else
				if (save == 2)
				{
					LoadMapForSession(save_path, "[EDITOR]");

					try_close_save_dialog();
				}
				else
				if (save == 3)
				{
					// apply merge
					MergeCommit();

					try_close_save_dialog();
				}
			}

			if (save == 1 && save_overwrite_confirm)
				ImGui::TextColored(ImVec4(1.0f, 0.7f, 0.3f, 1.0f), "Overwrite existing file? Press OVERWRITE to confirm.");
			if (save == 1 && save_error_text[0])
				ImGui::TextColored(ImVec4(1.0f, 0.35f, 0.35f, 1.0f), "%s", save_error_text);

			if (save && ImGui::ListBoxHeader("###dir", ImVec2(-1, -ImGui::GetItemsLineHeightWithSpacing()) ))
			{
				// fill from dir_arr
				DirItem** di = dir_arr;
				while (*di)
				{
					if ((*di)->item == A3D_DIRECTORY)
						ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1,1,0,1));

					if (ImGui::Selectable((*di)->name,false, ImGuiSelectableFlags_AllowDoubleClick))
					{
						if ((*di)->item == A3D_FILE)
						{
							// just copy its path to editbox
							char cd[4096];
							a3dGetCurDir(cd,4096);
							int written = snprintf(save_path,4096,"%s%s",cd,(*di)->name);
							if (written < 0 || written >= 4096)
							{
								save_path[0] = 0; // truncation — clear rather than use partial path
								CopyClamped(save_error_text, (int)sizeof(save_error_text), "Selected path is too long.");
							}
							else
							{
								save_error_text[0] = 0;
							}

							if (save == 3)
							{
								// unload any pending merge
								MergeCancel();

								// load new one
								MergeOpen(save_path);
							}

							if (ImGui::IsMouseDoubleClicked(0))
								save_do = true;
						}
						else
						{
							// change current directory and rescan after
							cwd = *di;
						}
					}
					if ((*di)->item == A3D_DIRECTORY)
						ImGui::PopStyleColor();
					di++;
				}
				ImGui::ListBoxFooter();
			}



			const char* commit_label = save == 1 ? (save_overwrite_confirm ? "OVERWRITE" : "SAVE") : save == 2 ? "LOAD" : "MERGE";
			if (save && (ImGui::Button(commit_label) || save_do))
			{
				if (save == 1)
				{
					try_save_map();
				}
				else
				if (save == 2)
				{
					// load
					LoadMapForSession(save_path, "[EDITOR]");

					try_close_save_dialog();
				}
				else
				if (save == 3)
				{
					// apply merge
					MergeCommit();

					try_close_save_dialog();
				}
			}

			ImGui::SameLine();
			if (save && ImGui::Button(save == 1 && save_overwrite_confirm ? "CLEAR CONFIRM" : "CANCEL"))
			{
				// close save/load/merge dialog
				if (save == 1 && save_overwrite_confirm)
				{
					save_overwrite_confirm = false;
					save_overwrite_path[0] = 0;
				}
				else
				{
					if (save == 3)
						MergeCancel();
					try_close_save_dialog();
				}
			}

			if (save && cwd && show)
			{
				if (save == 3)
					MergeCancel();

				a3dSetCurDir(cwd->name);
				a3dGetCurDir(save_path,4096);
				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;

				a3dGetCurDir(save_path,4096);
				AllocDir(&dir_arr);
			}

			ImGui::End();

			if (!show)
			{
				if (save == 3)
					MergeCancel();

				if (dir_arr)
					FreeDir(dir_arr);
				dir_arr = 0;

				save = 0;
				save_overwrite_confirm = false;
				save_overwrite_path[0] = 0;
			}
		}


		if (sidebar_tab == 1) // EDIT
		{
		if (ImGui::CollapsingHeader("Undo / Redo", ImGuiTreeNodeFlags_DefaultOpen))
		{
			if (!URDO_CanUndo())
			{
				ImGui::PushItemFlag(ImGuiItemFlags_Disabled, true);
				ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				ImGui::Button("<<");
				ImGui::SameLine();
				ImGui::Button("<");
				ImGui::PopStyleVar();
				ImGui::PopItemFlag();
			}
			else
			{
				if (ImGui::Button("<<") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)
					{ URDO_Undo(0); inst_list_dirty = true; }
					ImGui::SameLine();
					if (ImGui::Button("<") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)
					{ URDO_Undo(1); inst_list_dirty = true; }
			}
			ImGui::SameLine();
			if (!URDO_CanRedo())
			{
				ImGui::PushItemFlag(ImGuiItemFlags_Disabled, true);
				ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				ImGui::Button(">");
				ImGui::SameLine();
				ImGui::Button(">>");
				ImGui::PopStyleVar();
				ImGui::PopItemFlag();
			}
			else
			{
				if (ImGui::Button(">") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)
					{ URDO_Redo(1); inst_list_dirty = true; }
					ImGui::SameLine();
					if (ImGui::Button(">>") || ImGui::IsItemActive() && io.MouseDownDuration[0] > .25f)
					{ URDO_Redo(0); inst_list_dirty = true; }
			}
			ImGui::SameLine();
			if (!URDO_CanRedo() && !URDO_CanUndo())
			{
				ImGui::PushItemFlag(ImGuiItemFlags_Disabled, true);
				ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				ImGui::Button("PURGE");
				ImGui::PopStyleVar();
				ImGui::PopItemFlag();
			}
			else
				if (ImGui::Button("PURGE"))
					URDO_Purge();
			ImGui::SameLine();
			ImGui::Text("%zu BYTES", URDO_Bytes());
		}

		render_character_owner();
		render_materials_owner();
		render_palette_owner();
		render_options_owner();
		render_mesh_owner();

		// ========================================================================
		// BRUSH EDITING SECTION
		// Main terrain/material editing UI with multiple modes
		// ========================================================================
		if (ImGui::CollapsingHeader("Brush", ImGuiTreeNodeFlags_DefaultOpen))
		{
			ImGuiTabBarFlags tab_bar_flags = ImGuiTabBarFlags_None;
			if (ImGui::BeginTabBar("MyTabBar", tab_bar_flags))
			{
				bool pushed = false;

				// Dim inactive tabs for visual clarity
				if (edit_mode != 0)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}

					// ====================================================================
					// SCULPT TAB - Edit terrain height map
					// ====================================================================
					if (ImGui::BeginTabItem("SCULPT"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 0;  // Set active edit mode to sculpting
						ImGui::Text("Sculpting modifies terrain height map \n ");

					// Display current brush mode based on modifier keys
					const char* mode = "";

					if (!painting && io.KeyCtrl && io.KeyShift)
					{
						mode = "HEIGHT PROBE";      // Sample height value from terrain
					}
					else
					if (!painting && io.KeyCtrl)
						mode = "DIAGONAL FLIP";     // Flip terrain diagonal for smoother transitions
					else
					{
						if (io.KeyShift)
							// Blur or sharpen based on alpha sign
							mode = br_alpha >= 0 ? "BLURRING" : "SHARPENING";
						else
							// Raise or lower terrain based on alpha sign
							mode = br_alpha >= 0 ? "ASCENT" : "DESCENT";
					}

					ImGui::Text("MODE (shift/ctrl): %s", mode);

					// Brush radius controls multi-tile coverage for height and mat-id painting
					ImGui::SliderFloat("BRUSH RADIUS", &br_radius, 5.f, 100.f);
					ImGui::Combo("Brush Shape", &brush_shape, "Gaussian\0Square\0Noise\0\0");
					if (ImGui::IsItemHovered()) ImGui::SetTooltip("Brush falloff shape for sculpting and painting.");
					ImGui::SliderFloat("BRUSH ALPHA", &br_alpha, -0.5f, +0.5f);

					// Tile creation brush radius (for Alt+click mode)
					if (io.KeyAlt)
					{
						ImGui::Separator();
						ImGui::Text("TILE CREATION MODE (Alt)");
					ImGui::SliderFloat("TILE RADIUS", &br_tile_radius, 0.5f, 20.f);
						ImGui::Text("Creates/deletes patches in radius");
					}


					ImGui::Checkbox("BRUSH HEIGHT LIMIT",&br_limit);
					ImGui::SameLine();

					// Arrow buttons with Repeater
					float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
					ImGui::PushButtonRepeat(true);
					if (ImGui::ArrowButton("##probe_left", ImGuiDir_Left)) { if (probe_z>0) probe_z-=1; }
					ImGui::SameLine(0.0f, spacing);
					if (ImGui::ArrowButton("##probe_right", ImGuiDir_Right)) { if (probe_z<0xffff) probe_z+=1; }
					ImGui::PopButtonRepeat();
					ImGui::SameLine();
					ImGui::Text("%d", probe_z);
					ImGui::Text("%s", "ctrl+shift to probe");

					// ImGui::SliderFloat("BRUSH HEIGHT", &probe_z, 0.0f, 65535.0f);

					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				if (edit_mode != 1)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}
				// ====================================================================
				// MAT-id TAB - Paint material IDs onto terrain
				// ====================================================================
				// Material IDs are 0-255 values stored in the terrain
				// Each ID references a material definition in mat[] array
				//
				// CURRENT MATERIAL SYSTEM:
				// - Material 0 = Water (blue-gray, defined explicitly)
				// - Materials 1-255 = RANDOM COLORS (generated at startup)
				//
				// WHY RANDOM? These are placeholders! In a real game, you would
				// define specific materials like:
				// - mat[1] = Grass (green shades)
				// - mat[2] = Dirt (brown shades)
				// - mat[3] = Stone (gray shades)
				// - etc.
				//
				// The random colors let you visually distinguish different
				// material IDs during editing, even though they're not finalized.
				// ====================================================================
					if (ImGui::BeginTabItem("MAT-id"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 1;
					ImGui::Text("Material channel selects which material \ndefinition should be used (0-255)");

					const char* mode = "";

					// Painting with shift (and enabled z-limit)
					// allows painting above or below a height threshold

					if (!painting && io.KeyCtrl && io.KeyShift)
					{
						mode = "HEIGHT PROBE";
					}
					else
					if (!painting && io.KeyCtrl)
						mode = "MAT-id PROBE";
					else
					{
						if (br_limit)
						{
							if (io.KeyShift)
								mode = "PAINT BELOW";
							else
								mode = "PAINT ABOVE";
						}
						else
							mode = "PAINT";
					}

					ImGui::Text("MODE (shift/ctrl): %s", mode);
					ImGui::SliderFloat("BRUSH DIAMETER", &br_radius, 1.f, 100.f);

					float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
					ImGui::PushButtonRepeat(true);
					if (ImGui::ArrowButton("##matid_left", ImGuiDir_Left)) { if (active_material>0) active_material-=1; }
					ImGui::SameLine(0.0f, spacing);
					if (ImGui::ArrowButton("##matid_right", ImGuiDir_Right)) { if (active_material<0xff) active_material+=1; }
					ImGui::PopButtonRepeat();
					ImGui::SameLine();
					ImGui::Text("MAT-id 0x%02X (%d)", active_material, active_material);
					ImGui::SameLine();
					ImGui::Text("%s", "ctrl to probe");


					ImGui::Checkbox("BRUSH HEIGHT LIMIT",&br_limit);
					ImGui::SameLine();

					// Arrow buttons with Repeater
					ImGui::PushButtonRepeat(true);
					if (ImGui::ArrowButton("##probe_left", ImGuiDir_Left)) { if (probe_z>0) probe_z-=1; }
					ImGui::SameLine(0.0f, spacing);
					if (ImGui::ArrowButton("##probe_right", ImGuiDir_Right)) { if (probe_z<0xffff) probe_z+=1; }
					ImGui::PopButtonRepeat();
					ImGui::SameLine();
					ImGui::Text("%d", probe_z);
					ImGui::Text("%s", "ctrl+shift to probe");
					ImGui::Text("%s", "press shift to paint below limit");

					ImGui::Separator();
					ImGui::Text("Auto MAT-elev");

					static int auto_elev_mode = 0;
					static float auto_elev_slope = 64.0f;
					static int auto_elev_height = 0xA000;
					static bool auto_elev_overwrite = true;
					const char* auto_modes[] = { "Slope", "Height" };
					ImGui::Combo("Mode##auto_elev", &auto_elev_mode, auto_modes, IM_ARRAYSIZE(auto_modes));
					if (auto_elev_mode == 0)
					{
						ImGui::SliderFloat("Slope Threshold", &auto_elev_slope, 0.0f, 512.0f);
					}
					else
					{
						ImGui::SliderInt("Height Threshold", &auto_elev_height, 0, 0xFFFF);
					}
					ImGui::Checkbox("Overwrite Existing", &auto_elev_overwrite);
					if (ImGui::Button("Apply Auto MAT-elev") && !g_deferred.active)
					{
						AutoMatElev ctx = { auto_elev_mode, auto_elev_slope, auto_elev_height, auto_elev_overwrite };
						RequestConfirm("Apply auto material-elevation to entire map?", ConfirmApplyAutoMatElev, &ctx, sizeof(ctx));
					}
					ImGui::SameLine();
					if (ImGui::Button("Clear MAT-elev"))
						RequestConfirm("Clear all material-elevation flags?", ConfirmClearMatElev);

					ImGui::Separator();
					ImGui::Text("Auto Texture");

					static int auto_tex_mode = 0;
					static float auto_tex_slope = 64.0f;
					static int auto_tex_h_min = 0;
					static int auto_tex_h_max = 0xA000;
					static int auto_tex_mat_id = 1;
					static bool auto_tex_overwrite = true;

					ImGui::Combo("Mode##auto_tex", &auto_tex_mode, auto_modes, IM_ARRAYSIZE(auto_modes));

					if (auto_tex_mode == 0)
					{
						ImGui::SliderFloat("Slope Threshold##tex", &auto_tex_slope, 0.0f, 512.0f);
					}
					else
					{
						ImGui::DragIntRange2("Height Range", &auto_tex_h_min, &auto_tex_h_max, 1.0f, 0, 0xFFFF);
					}

					ImGui::SliderInt("Material ID", &auto_tex_mat_id, 0, 255);
					ImGui::Checkbox("Overwrite##tex", &auto_tex_overwrite);

					if (ImGui::Button("Apply Auto Texture") && !g_deferred.active)
					{
						AutoTexture ctx = { auto_tex_mode, auto_tex_slope, auto_tex_h_min, auto_tex_h_max, auto_tex_mat_id, auto_tex_overwrite };
						RequestConfirm("Apply auto texture to entire map?", ConfirmApplyAutoTexture, &ctx, sizeof(ctx));
					}

					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}


				if (edit_mode != 3)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}
					if (ImGui::BeginTabItem("MAT-elev"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 3;
					ImGui::Text("Material elevation selects which ramp to use (1 of 4)\ndepending on vertical elevation change:\n(0/1:top,1/1:upper,1/0:lower,0/0:bottom)");

					const char* mode = "";

					// painting with shift (and enabled z-limit)
					// could reverse painting above with below ....

					if (!painting && io.KeyCtrl && io.KeyShift)
					{
						mode = "HEIGHT PROBE";
					}
					else
						if (!painting && io.KeyCtrl)
							mode = "MAT-elev PROBE";
						else
						{
							if (br_limit)
							{
								if (io.KeyShift)
									mode = "PAINT BELOW";
								else
									mode = "PAINT ABOVE";
							}
							else
								mode = "PAINT";
						}

					ImGui::Text("MODE (shift/ctrl): %s", mode);
					ImGui::SliderFloat("BRUSH DIAMETER", &br_radius, 1.f, 100.f);

					bool elev = active_elev != 0;

					ImGui::Checkbox("ELEVATED", &elev);
					ImGui::SameLine();
					ImGui::Text("%s", "ctrl to probe");

					active_elev = elev ? 1 : 0;

					ImGui::Checkbox("BRUSH HEIGHT LIMIT", &br_limit);
					ImGui::SameLine();

					// Arrow buttons with Repeater
					float spacing = ImGui::GetStyle().ItemInnerSpacing.x;
					ImGui::PushButtonRepeat(true);
					if (ImGui::ArrowButton("##probe_left", ImGuiDir_Left)) { if (probe_z > 0) probe_z -= 1; }
					ImGui::SameLine(0.0f, spacing);
					if (ImGui::ArrowButton("##probe_right", ImGuiDir_Right)) { if (probe_z < 0xffff) probe_z += 1; }
					ImGui::PopButtonRepeat();
					ImGui::SameLine();
					ImGui::Text("%d", probe_z);
					ImGui::Text("%s", "ctrl+shift to probe");
					ImGui::Text("%s", "press shift to paint below limit");

					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				static bool add_verts = false;
				static bool build_poly = false;

				if (active_sprite && edit_mode != 4)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);

					add_verts = false;
					build_poly = false;
				}
					if (active_sprite && ImGui::BeginTabItem("SPRITE"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 4;

					// when putting new instance we do:
					// 1. pretranslate (to have 0 in rot/scale center)
					// 2. scale by constant_xyz * random_xyz
					// 2. rotate around z by given angle + random_z
					// 3. rotate by given world's xy axis + random_xy (length is angle)
					// 4. rotate toward terrain normal by given weight
					// 5. post translate by constant xyz + random xyz

					extern int bsp_insts, bsp_nodes, bsp_tests;
					ImGui::Text("INSTS:%d, NODES:%d, TESTS:%d \n ", bsp_insts, bsp_nodes, bsp_tests);

					const char* mode = "";

					if (io.KeyAlt)
						mode = "ADD/REMOVE TILES";
					else
						if (io.KeyCtrl)
							mode = "DELETE SPRITE";
						else
							mode = "INSERT SPRITE";

					ImGui::Text("MODE (ctrl): %s", mode);


					SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(active_sprite);

					ImGui::SliderInt("Animation", &sp->anim, 0, active_sprite->anims-1);
					ImGui::SliderFloat("Rotate", &sp->yaw, 0, 360);
					ImGui::SliderInt("Still Frame", &sp->frame, 0, active_sprite->anim[sp->anim].length-1);
					ImGui::Separator();
					ImGui::SliderInt("RepFirst", sp->t+0, 0, 50);
					ImGui::SliderInt("RepForward", sp->t+1, 0, 50);
					ImGui::SliderInt("RepLast", sp->t+2, 0, 50);
					ImGui::SliderInt("RepBackward", sp->t+3, 0, 50);
					ImGui::Separator();
					ImGui::Checkbox("Rand Animation", &sp->rand_anim);
					ImGui::Checkbox("Rand Frame", &sp->rand_frame);
					ImGui::Checkbox("Rand Rotate", &sp->rand_yaw);
					ImGui::Separator();
					ImGui::SliderFloat("Height", &sp->height, -500, 500);

					ImGui::EndTabItem();
				}
				if (active_sprite && pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				if (edit_mode != 5)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);

					add_verts = false;
					build_poly = false;

					item_preview_sprite = 0;
				}
					if (ImGui::BeginTabItem("ITEM"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 5;

					// when putting new instance we do:
					// 1. pretranslate (to have 0 in rot/scale center)
					// 2. scale by constant_xyz * random_xyz
					// 2. rotate around z by given angle + random_z
					// 3. rotate by given world's xy axis + random_xy (length is angle)
					// 4. rotate toward terrain normal by given weight
					// 5. post translate by constant xyz + random xyz

					extern int bsp_insts, bsp_nodes, bsp_tests;
					ImGui::Text("INSTS:%d, NODES:%d, TESTS:%d \n ", bsp_insts, bsp_nodes, bsp_tests);

					const char* mode = "";

					if (io.KeyAlt)
						mode = "ADD/REMOVE TILES";
					else
						if (io.KeyCtrl)
							mode = "DELETE SPRITE";
						else
							mode = "INSERT SPRITE";

					ImGui::Text("MODE (ctrl): %s", mode);


					// TODO:
					// add count

					// TODO:
					// add reset WORLD items
					// (delete all WORLD items, rescan all EDIT items and create WORLD clones)

					if (ImGui::Button("RESET items"))
					{
						ResetItemInsts(world);
					}

					struct StaticNames
					{
						StaticNames()
						{
							items = 0;
							while (items < editor_bundle_item_count)
							{
								names[items] = editor_bundle_items[items].label;
								items++;
							}
						}

						int items;
						const char* names[256];
					};

					static StaticNames names;
					if (active_item < 0)
						active_item = 0;
					if (active_item >= names.items)
						active_item = names.items - 1;
					ImGui::ListBox("Item", &active_item, names.names, names.items);

					active_sprite = 0;
					item_preview_sprite = 0;

					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}


				if (edit_mode != 6)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);

					add_verts = false;
					build_poly = false;

					item_preview_sprite = 0;
				}
					if (ImGui::BeginTabItem("ENEMY"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 6;

					ImGui::Checkbox("Enable Enemy Gen", &g_enable_enemies);
					if (ImGui::IsItemHovered()) ImGui::SetTooltip("Toggle spawning of enemies from generators.");

					if (ImGui::Button("Delete All Generators"))
						RequestConfirm("Delete ALL enemy generators?", ConfirmDeleteAllEnemyGens);
					if (ImGui::IsItemHovered()) ImGui::SetTooltip("Permanently remove all enemy generators from the map.");

					ImGui::Separator();

					if (ImGui::SliderInt("MaxAlive", &eg_alive_max, 1, 7))
					{
						if (eg_alive_max < 0)
							eg_alive_max = 0;
						if (eg_alive_max > 7)
							eg_alive_max = 7;
					}

					if (ImGui::SliderInt("ReviveMax", &eg_revive_min, 0, eg_revive_max))
					{
						if (eg_revive_min < 0)
							eg_revive_min = 0;
						if (eg_revive_min > 10)
							eg_revive_min = 10;
					}
					if (ImGui::SliderInt("ReviveMin", &eg_revive_max, eg_revive_min, 10))
					{
						if (eg_revive_max < 0)
							eg_revive_max = 0;
						if (eg_revive_max > 10)
							eg_revive_max = 10;
					}


					if (ImGui::SliderInt("Armor", &eg_armor, 0, 10))
					{
						if (eg_armor < 0)
							eg_armor = 0;
						if (eg_armor > 10)
							eg_armor = 10;
					}


					if (ImGui::SliderInt("Helmet", &eg_helmet, 0, 10))
					{
						if (eg_helmet < 0)
							eg_helmet = 0;
						if (eg_helmet > 10)
							eg_helmet = 10;
					}

					if (ImGui::SliderInt("Shield", &eg_shield, 0, 10))
					{
						if (eg_shield < 0)
							eg_shield = 0;
						if (eg_shield > 10)
							eg_shield = 10;
					}

					if (ImGui::SliderInt("Sword", &eg_sword, 0, 10))
					{
						if (eg_sword < 0)
							eg_sword = 0;
						if (eg_sword > 10)
							eg_sword = 10;
						eg_crossbow = 10 - eg_sword;
					}
					if (ImGui::SliderInt("Crossbow", &eg_crossbow, 0, 10))
					{
						if (eg_crossbow < 0)
							eg_crossbow = 0;
						if (eg_crossbow > 10)
							eg_crossbow = 10;
						eg_sword = 10 - eg_crossbow;
					}

					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				if (edit_mode != 7)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);

					add_verts = false;
					build_poly = false;

					item_preview_sprite = 0;
				}
					if (ImGui::BeginTabItem("STORY"))
					{
						if (!g_explicit_mesh_placement_mode)
							edit_mode = 7;
					// here we track:
					// meshes, sprites, items and enemy-gens
					// on click we set new story-id

					ImGui::InputInt("story_id", &story_id);
					if (hover_story_hover)
						ImGui::Text("current %d", hover_story_value);
					else
						ImGui::Text("current ?");
					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				/*
				if (edit_mode != 2)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}
				if (ImGui::BeginTabItem("sh-MODE"))
				{
					edit_mode = 2;
					ImGui::Text("Shade mode channel specifies how lighting \naffects shading ramp (0-3)");
					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				if (edit_mode != 3)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}
				if (ImGui::BeginTabItem("sh-RAMP"))
				{
					edit_mode = 3;
					ImGui::Text("Shade ramp channel selects a cell \nhorizontaly from a material ramps (0-15)");
					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}

				if (edit_mode != 4)
				{
					pushed = true;
					ImGui::PushStyleVar(ImGuiStyleVar_Alpha, ImGui::GetStyle().Alpha * 0.5f);
				}
				if (ImGui::BeginTabItem("ELEV"))
				{
					edit_mode = 4;
					ImGui::Text("Elevation bits are used to choose ramps \nvertically from material by bit difference");
					ImGui::EndTabItem();
				}
				if (pushed)
				{
					pushed = false;
					ImGui::PopStyleVar();
				}
				*/

				ImGui::EndTabBar();
			}
		}

		// Clear mesh selection when leaving mode 2
		if (edit_mode != 2)
		{
			selected_inst = 0;
			drag_inst = 0;
		}

		} // EDIT tab content
		if (sidebar_tab == 5) // FONT
		{
			ImGui::TextDisabled("LEGACY");
			ImGui::TextWrapped("FONT is no longer the authoritative terrain-appearance editor.");
			ImGui::TextWrapped("Use EDIT -> Character for glyph selection, character size, and presets.");
		}
		if (sidebar_tab == 6) // SKIN
		{
			// FL-728: V2 bundle skin apply through the server-owned ID path.
			ImGui::TextWrapped("Cycle player skin family via V2 bundle and apply to TERM++ windows.");
			ImGui::Separator();

			// Show available skin families from compiled bundle.
			static uint16_t skin_ids[16];
			static int skin_count = 0;
			static int skin_selected = 0;
			static bool skin_ids_loaded = false;
			if (!skin_ids_loaded)
			{
				skin_count = GameGetBundleSkinIds(skin_ids, 16);
				skin_ids_loaded = true;
			}

			if (skin_count > 0)
			{
				// Current skin indicator.
				uint16_t current_skin = 0;
				if (g_term_skin_requested_id != 0)
					current_skin = g_term_skin_requested_id;
				else if (prime_game && prime_game->player.appearance_v2.valid)
					current_skin = prime_game->player.appearance_v2.skin_definition_id;
				ImGui::Text("Active skin: %u", (unsigned)current_skin);
				ImGui::Text("Available: %d skin families", skin_count);

				// Skin selector buttons.
				for (int i = 0; i < skin_count; i++)
				{
					char label[32];
					snprintf(label, sizeof(label), "Skin %u", (unsigned)skin_ids[i]);
					if (skin_ids[i] == current_skin)
					{
						ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.2f, 0.6f, 0.2f, 1.0f));
						ImGui::Button(label);
						ImGui::PopStyleColor();
					}
					else if (ImGui::Button(label))
					{
						skin_selected = i;
						g_term_skin_requested_id = skin_ids[i];
						float pos[3] = { pos_x, pos_y, pos_z };
						int hot = ApplyTermSkinSelection(wnd, rot_yaw, pos, g_term_skin_requested_id);
						if (hot > 0)
							printf("[TERM++ SKIN] Applied skin_id=%u to %d window(s)\n",
								(unsigned)g_term_skin_requested_id, hot);
						else
							printf("[TERM++ SKIN] Failed to apply skin_id=%u\n",
								(unsigned)g_term_skin_requested_id);
					}
					if (i + 1 < skin_count) ImGui::SameLine();
				}
			}
			else
			{
				ImGui::TextColored(ImVec4(1,0.4f,0.4f,1), "Bundle not loaded or no skins found.");
				if (ImGui::Button("Reload"))
					skin_ids_loaded = false;
			}

			ImGui::Separator();
			ImGui::TextDisabled("LEGACY NOTE");
			ImGui::TextWrapped("Terrain appearance editing moved to EDIT -> Materials / Palette / Options.");
		}
		if (sidebar_tab == 7) // INFO
		{
		// Coordinate HUD — always visible
		ImGui::Text("Camera: %.1f, %.1f, %.1f", pos_x, pos_y, pos_z);
		ImGui::Text("Probe Z: %d (0x%04X)", probe_z, probe_z);
		ImGui::Separator();

		// Terrain probe readout — populated by Ctrl+Shift+Click
		if (g_probe.valid)
		{
			ImGui::TextColored(ImVec4(0.4f, 1.0f, 0.4f, 1.0f), "Terrain Probe (Ctrl+Shift+Click)");
			ImGui::Text("World: %.1f, %.1f, %.1f", g_probe.world_x, g_probe.world_y, g_probe.world_z);
			if (g_osm_proj.valid)
				ImGui::Text("Lat/Lon: %.7f, %.7f", g_probe.lat, g_probe.lon);
			ImGui::Text("Patch: %d, %d  Cell: %d, %d", g_probe.patch_x, g_probe.patch_y, g_probe.cell_u, g_probe.cell_v);
			ImGui::Text("Height: %d (0x%04X)", g_probe.height, g_probe.height);
			ImGui::Text("mat_id: %d  elev: %d  shade: %d", g_probe.mat_id, g_probe.elev_flag, g_probe.shade_idx);
			ImGui::Text("Matlib BG: (%d,%d,%d) -> xt %d", g_probe.matlib_bg[0], g_probe.matlib_bg[1], g_probe.matlib_bg[2], g_probe.xterm_bk);
			ImGui::Text("Matlib FG: (%d,%d,%d) -> xt %d", g_probe.matlib_fg[0], g_probe.matlib_fg[1], g_probe.matlib_fg[2], g_probe.xterm_fg);
			ImGui::Text("Glyph: 0x%02X (%d) '%c'", g_probe.glyph, g_probe.glyph, g_probe.glyph >= 32 && g_probe.glyph < 127 ? g_probe.glyph : '?');

			// Color preview boxes
			float bk_r = ((g_probe.xterm_bk - 16) / 36) / 5.0f;
			float bk_g = (((g_probe.xterm_bk - 16) % 36) / 6) / 5.0f;
			float bk_b = ((g_probe.xterm_bk - 16) % 6) / 5.0f;
			float fg_r = ((g_probe.xterm_fg - 16) / 36) / 5.0f;
			float fg_g = (((g_probe.xterm_fg - 16) % 36) / 6) / 5.0f;
			float fg_b = ((g_probe.xterm_fg - 16) % 6) / 5.0f;
			ImGui::ColorButton("##bk_preview", ImVec4(bk_r, bk_g, bk_b, 1.0f)); ImGui::SameLine();
			ImGui::Text("BK"); ImGui::SameLine();
			ImGui::ColorButton("##fg_preview", ImVec4(fg_r, fg_g, fg_b, 1.0f)); ImGui::SameLine();
			ImGui::Text("FG");
		}
		else
		{
			ImGui::TextDisabled("Ctrl+Shift+Click terrain to probe");
		}
		ImGui::Separator();

		if (ImGui::Button("Debug Probe")) DebugProbe();
		if (ImGui::TreeNode("Shading Quick Guide"))
		{
			ImGui::BulletText("MAT-id paints the material ID (0-255).");
			ImGui::BulletText("Each material has 4 ramps (slope) x 16 shades (light).");
			ImGui::BulletText("MAT-elev sets the 1-bit flag that selects ramps.");
			ImGui::BulletText("Light/ambient controls pick the shade level.");
			ImGui::BulletText("PALETTIZE snaps colors to the active palette.");
			ImGui::Separator();
			ImGui::TextWrapped("Auto MAT-elev is a heuristic. Slope mode marks steep cells; Height mode marks cells above a height threshold. Use Undo if it looks wrong.");
			ImGui::TextWrapped("Bake Meshes to Terrain casts rays onto meshes and writes height/material into terrain. Run Auto MAT-elev after baking to get ramp shading.");
			ImGui::TreePop();
		}
		} // INFO tab content

		ImGui::End(); // close ##sidebar panel window

		static bool show_demo_window = true;
		static bool show_another_window = false;

		// 1. Show the big demo window (Most of the sample code is in ImGui::ShowDemoWindow()! You can browse its code to learn more about Dear ImGui!).
		//if (show_demo_window)
		//	ImGui::ShowDemoWindow(&show_demo_window);

		/*

		// 2. Show a simple window that we create ourselves. We use a Begin/End pair to created a named window.
		{
			static float f = 0.0f;
			static int counter = 0;

			ImGui::Begin("Hello, world!");                          // Create a window called "Hello, world!" and append into it.

			ImGui::Text("This is some useful text.");               // Display some text (you can use a format strings too)
			ImGui::Checkbox("Demo Window", &show_demo_window);      // Edit bools storing our window open/close state
			ImGui::Checkbox("Another Window", &show_another_window);

			ImGui::SliderFloat("float", &f, 0.0f, 1.0f);            // Edit 1 float using a slider from 0.0f to 1.0f
			ImGui::ColorEdit3("clear color", (float*)&clear_color); // Edit 3 floats representing a color

			if (ImGui::Button("Button"))                            // Buttons return true when clicked (most widgets return true when edited/activated)
				counter++;
			ImGui::SameLine();
			ImGui::Text("counter = %d", counter);

			ImGui::Text("Application average %.3f ms/frame (%.1f FPS)", 1000.0f / ImGui::GetIO().Framerate, ImGui::GetIO().Framerate);

			ImGui::Text("PATCHES: %d, DRAWS: %d, CHANGES: %d", render_context.patches, render_context.draws, render_context.changes);

			ImGui::End();
		}
		*/

		// 3. Show another simple window.
		/*
		if (show_another_window)
		{
			ImGui::Begin("Another Window", &show_another_window);   // Pass a pointer to our bool variable (the window will have a closing button that will clear the bool when clicked)
			ImGui::Text("Hello from another window!");
			if (ImGui::Button("Close Me"))
				show_another_window = false;
			ImGui::End();
		}
		*/

//		if (pFont)
//			ImGui::PopFont();
	}

	if (marquee_active)
		ImGui::GetForegroundDrawList()->AddRect(marquee_start, marquee_end, IM_COL32(255, 255, 0, 255));

	int fb_w = (int)(io.DisplaySize.x * io.DisplayFramebufferScale.x);
	int fb_h = (int)(io.DisplaySize.y * io.DisplayFramebufferScale.y);
	glViewport(0, 0, fb_w, fb_h);

	glClearColor(clear_color[0], clear_color[1], clear_color[2], clear_color[3]);
	glClearDepth(0);
	glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);

	RenderContext* rc = &render_context;
	double tm[16];

	// currently we're assuming: 1 visual cell = 1 font_size

	double z_scale = 1.0 / HEIGHT_SCALE; // this is a constant, (what fraction of font_size is produced by +1 height_map)

	if (!io.MouseDown[0])
	{
		diag_flipped = false;
		inst_added = false;
	}

	if (!io.MouseDown[1])
	{
		spinning = 0;
	}

	if (!io.MouseDown[2])
	{
		panning = 0;
	}

	if (!io.WantCaptureMouse)
	{
		if (zoom_wheel)
		{
			font_size *= powf(1.1f, zoom_wheel);
			ClampInteractiveFontSize();
			zoom_wheel = 0;
		}

		if (spinning)
		{
			double mdx = spinning_x - round(io.MousePos.x);
			double mdy = -(spinning_y - round(io.MousePos.y));

			rot_yaw += (float)(mdx * 0.1);
			if (rot_yaw < -180)
				rot_yaw += 360;
			if (rot_yaw > 180)
				rot_yaw -= 360;

			rot_pitch += (float)(mdy * 0.1);
			if (rot_pitch > 90)
				rot_pitch = 90;
			if (rot_pitch < 10)
				rot_pitch = 10;


			spinning_x = (int)roundf(io.MousePos.x);
			spinning_y = (int)roundf(io.MousePos.y);
		}
		else
		if (io.MouseDown[1])
		{
			spinning = 1;
			spinning_x = (int)roundf(io.MousePos.x);
			spinning_y = (int)roundf(io.MousePos.y);
		}
	}
	else
	{
		zoom_wheel = 0;
	}


	double rx = 0.5 * io.DisplaySize.x / font_size;
	double ry = 0.5 * io.DisplaySize.y / font_size;

	double pitch = rot_pitch * (M_PI / 180);
	double yaw = rot_yaw * (M_PI / 180);


	if (spin_anim)
	{
		rot_yaw += 0.1f;
		if (rot_yaw > 180)
			rot_yaw -= 360;
	}

	if (!io.WantCaptureMouse)
	{
		if (panning)
		{
			double mdx = panning_x - round(io.MousePos.x);
			double mdy = -(panning_y - round(io.MousePos.y)) / sin(pitch);
			pos_x = (float)(panning_dx + (mdx*cos(yaw) - mdy * sin(yaw)) / font_size);
			pos_y = (float)(panning_dy + (mdx*sin(yaw) + mdy * cos(yaw)) / font_size);

			panning_x = (int)roundf(io.MousePos.x);
			panning_y = (int)roundf(io.MousePos.y);

			panning_dx = pos_x;
			panning_dy = pos_y;
		}
		else
		if (io.MouseDown[2])
		{
			panning = 1;
			panning_x = (int)roundf(io.MousePos.x);
			panning_y = (int)roundf(io.MousePos.y);
			panning_dx = pos_x;
			panning_dy = pos_y;
		}
	}

	// Arrow key camera panning — ignored when ImGui is using the keyboard
	// (e.g. a text field has focus). Speed scales with zoom so apparent motion
	// is constant regardless of font_size.
	if (!io.WantCaptureKeyboard)
	{
		float step = 5.0f / (float)font_size;
		if (io.KeysDown[io.KeyMap[ImGuiKey_LeftArrow]])
		{
			pos_x += (float)(step * cos(yaw));
			pos_y += (float)(step * sin(yaw));
		}
		if (io.KeysDown[io.KeyMap[ImGuiKey_RightArrow]])
		{
			pos_x -= (float)(step * cos(yaw));
			pos_y -= (float)(step * sin(yaw));
		}
		if (io.KeysDown[io.KeyMap[ImGuiKey_UpArrow]])
		{
			pos_x -= (float)(step * sin(yaw));
			pos_y += (float)(step * cos(yaw));
		}
		if (io.KeysDown[io.KeyMap[ImGuiKey_DownArrow]])
		{
			pos_x += (float)(step * sin(yaw));
			pos_y -= (float)(step * cos(yaw));
		}
	}

	tm[0] = +cos(yaw)/rx;
	tm[1] = -sin(yaw)*sin(pitch)/ry;
	tm[2] = 0;
	tm[3] = 0;
	tm[4] = +sin(yaw)/rx;
	tm[5] = +cos(yaw)*sin(pitch)/ry;
	tm[6] = 0;
	tm[7] = 0;
	tm[8] = 0;
	tm[9] = +cos(pitch)*z_scale/ry;
	tm[10] = +2./0xffff;
	tm[11] = 0;
	tm[12] = -(pos_x * tm[0] + pos_y * tm[4] + pos_z * tm[8]);
	tm[13] = -(pos_x * tm[1] + pos_y * tm[5] + pos_z * tm[9]);
	tm[14] = -1.0;
	tm[15] = 1.0;

	float br_xyra[4] = { 0,0, br_radius, 0 };
	float br_quad[3] = { 0,0,0 };
	float br_probe[3] = { (float)probe_z, 1.0f, br_limit ? br_alpha : 0.0f };

	bool create_preview = false;
	int create_preview_px = 0;
	int create_preview_py = 0;

	double inst_tm[16];
	Mesh* inst_preview = 0;
	Inst* hover_inst = 0;
	EnemyGen* hover_eg = 0;

	bool sprite_preview = false;
	float sprite_preview_pos[3] = { 0,0,0 };

	enemygen_preview = false;

	if (!io.WantCaptureMouse && mouse_in)
	{
		if (painting || creating)
		{
			if (creating)
			{
				double mdx = painting_x - round(io.MousePos.x);
				double mdy = -(painting_y - round(io.MousePos.y)) / sin(pitch);
				double dx = -(mdx*cos(yaw) - mdy * sin(yaw)) / font_size;
				double dy = -(mdx*sin(yaw) + mdy * cos(yaw)) / font_size;
				double x = painting_dx + dx;
				double y = painting_dy + dy;

				int px = (int)floor(x / VISUAL_CELLS);
				int py = (int)floor(y / VISUAL_CELLS);

				// Multi-tile creation/deletion with brush radius
				int radius_patches = (int)ceil(br_tile_radius);
				for (int dy = -radius_patches; dy <= radius_patches; dy++)
				{
					for (int dx = -radius_patches; dx <= radius_patches; dx++)
					{
						// Check if this patch is within the circular radius
						float dist = sqrt((float)(dx*dx + dy*dy));
						if (dist > br_tile_radius)
							continue;

						int target_px = px + dx;
						int target_py = py + dy;

						if (creating < 0)
						{
							// LOCATE & DELETE PATCH IF EXIST
							Patch* p = GetTerrainPatch(terrain, target_px, target_py);
							if (p)
								URDO_Delete(terrain, p);
						}
						else
						{
							// IF NO PATCH THERE, CREATE ONE
							Patch* p = GetTerrainPatch(terrain, target_px, target_py);
							if (!p)
								p = URDO_Create(terrain, target_px, target_py, probe_z);
						}
					}
				}

				painting_dx = x;
				painting_dy = y;
				painting_x = (int)round(io.MousePos.x);
				painting_y = (int)round(io.MousePos.y);

				if (!io.MouseDown[0])
				{
					creating = 0;
					URDO_Close();
				}
			}
			else // painting
			{
				if (painting == 1)
				{
					//DRAG and/or DROP
					double mdx = painting_x - round(io.MousePos.x);
					double mdy = -(painting_y - round(io.MousePos.y)) / sin(pitch);
					double dx = -(mdx*cos(yaw) - mdy * sin(yaw)) / font_size;
					double dy = -(mdx*sin(yaw) + mdy * cos(yaw)) / font_size;
					double x = painting_dx + dx;
					double y = painting_dy + dy;

					double dist = paint_dist + sqrt(dx*dx + dy * dy);

					int i = 0;
					float alpha = br_alpha;
					br_alpha *= STAMP_A;
					while (1)
					{
						double w = ((i + 1) * br_radius * STAMP_R - paint_dist) / (dist - paint_dist);

						if (w >= 1)
							break;

						double sx = painting_dx + w * dx;
						double sy = painting_dy + w * dy;

						Stamp(sx, sy);

						i++;
					}
					br_alpha = alpha;

					paint_dist = dist - i * br_radius * STAMP_R;
					painting_dx = x;
					painting_dy = y;
					painting_x = (int)round(io.MousePos.x);
					painting_y = (int)round(io.MousePos.y);

					br_xyra[0] = (float)x;
					br_xyra[1] = (float)y;

					if (!io.MouseDown[0])
					{
						// DROP
						float alpha = br_alpha;
						br_alpha *= (float)pow(paint_dist / (br_radius * STAMP_R) * STAMP_A, 2.0);
						Stamp(x, y);
						br_alpha = alpha;
						br_xyra[3] = 0;
						painting = 0;
						g_painted_patches.clear();
						URDO_Close();
					}
					else
						br_xyra[3] = (float)pow(paint_dist / (br_radius * STAMP_R) * STAMP_A, 2.0) * br_alpha;
				}
				else
				if (painting == 2)
				{
					double mdx = painting_x - round(io.MousePos.x);
					double mdy = -(painting_y - round(io.MousePos.y)) / sin(pitch);

					if (mdx || mdy)
					{
						double dx = -(mdx*cos(yaw) - mdy * sin(yaw)) / font_size;
						double dy = -(mdx*sin(yaw) + mdy * cos(yaw)) / font_size;
						double x = painting_dx + dx;
						double y = painting_dy + dy;

						// FL-3838: distance throttle like painting=1 sculpt.
						// Without this, QueryTerrain fires every mouse pixel -> 98%+ CPU on 66K+ maps.
						paint_dist += sqrt(dx*dx + dy*dy);

						if (paint_dist >= br_radius * STAMP_R)
						{
							double hit[2] = { x,y };
							MatIDStamp stamp;
							stamp.r = br_radius;
							stamp.hit = hit;
							stamp.z = br_probe[0];
							stamp.z_lim = br_limit ? (io.KeyShift ? -1 : 1) : 0;

							URDO_Open();
							QueryTerrain(terrain, hit[0], hit[1], br_radius * 1.5, 0x00, MatIDStamp::SetMatCB, &stamp);
							URDO_Close();

							paint_dist = 0;
						}

						painting_dx = x;
						painting_dy = y;
						painting_x = (int)round(io.MousePos.x);
						painting_y = (int)round(io.MousePos.y);
					}

					if (!io.MouseDown[0])
					{
						// DROP
						painting = 0;
						g_painted_patches.clear();
						URDO_Close();
					}
				}
				else
				if (painting == 3)
				{
					double mdx = painting_x - round(io.MousePos.x);
					double mdy = -(painting_y - round(io.MousePos.y)) / sin(pitch);

					if (mdx || mdy)
					{
						double dx = -(mdx*cos(yaw) - mdy * sin(yaw)) / font_size;
						double dy = -(mdx*sin(yaw) + mdy * cos(yaw)) / font_size;
						double x = painting_dx + dx;
						double y = painting_dy + dy;

						// FL-3838: distance throttle like painting=1 sculpt.
						// Without this, QueryTerrain fires every mouse pixel -> 98%+ CPU on 66K+ maps.
						paint_dist += sqrt(dx*dx + dy*dy);

						if (paint_dist >= br_radius * STAMP_R)
						{
							double hit[2] = { x,y };
							MatIDStamp stamp;
							stamp.r = br_radius;
							stamp.hit = hit;
							stamp.z = br_probe[0];
							stamp.z_lim = br_limit ? (io.KeyShift ? -1 : 1) : 0;

							URDO_Open();
							QueryTerrain(terrain, hit[0], hit[1], br_radius * 1.5, 0x00, MatIDStamp::SetMatCB, &stamp);
							URDO_Close();

							paint_dist = 0;
						}

						painting_dx = x;
						painting_dy = y;
						painting_x = (int)round(io.MousePos.x);
						painting_y = (int)round(io.MousePos.y);
					}

					if (!io.MouseDown[0])
					{
						// DROP
						painting = 0;
						g_painted_patches.clear();
						URDO_Close();
					}
				}
			}
		}
		else
		{
			// HOVER preview
			// all coords in world space!
			double itm[16];
			Invert(tm, itm);

			double ray_p[4];
			double ray_v[4];

			// mouse ray
			double clip_mouse[4] =
			{
				2.0 * io.MousePos.x / io.DisplaySize.x - 1.0,
				1.0 - 2.0 * io.MousePos.y / io.DisplaySize.y,
				-1.1, // bit under floor
				1
			};

			Product(itm, clip_mouse, ray_p);

			clip_mouse[2] = -1.2; // bit under bit under floor

			Product(itm, clip_mouse, ray_v);

			ray_v[0] -= ray_p[0];
			ray_v[1] -= ray_p[1];
			ray_v[2] -= ray_p[2];

            // PATCH: Drag Logic
            if (drag_inst)
            {
                if (io.MouseDown[0])
                {
                    double tm[16];
                    if (GetInstTM(drag_inst, tm))
                    {
                        // Plane intersection: (ray_p + t*ray_v).z = drag_z
                        // t*ray_v.z = drag_z - ray_p.z
                        // t = (drag_z - ray_p.z) / ray_v.z
                        if (fabs(ray_v[2]) > 0.0001)
                        {
                            double t = (tm[14] - ray_p[2]) / ray_v[2];
                            double nx = ray_p[0] + t*ray_v[0];
                            double ny = ray_p[1] + t*ray_v[1];

                            // Only update X/Y
                            tm[12] = nx;
                            tm[13] = ny;
                            SetInstTM(drag_inst, tm);
                        }
                    }
                }
                else
                {
                    drag_inst = 0; // Drop
                }
            }

			double hit[4];
			double hit_nrm[3];

			Patch* p = HitTerrain(terrain, ray_p, ray_v, hit, hit_nrm);
			if (edit_mode == 2)
			{
				static uint64_t last_interactive_place_state_log = 0;
				uint64_t now = FL3714Now();
				if (io.MouseDown[0] || now - last_interactive_place_state_log > 1000000)
				{
					char active_mesh_name[256] = {0};
					if (active_mesh)
						GetMeshName(active_mesh, active_mesh_name, sizeof(active_mesh_name));
					else
						strcpy(active_mesh_name, "(none)");
					printf("[EDITOR] [FL-3714] mesh_place_state mouse_down=%d mouse_clicked=%d capture_mouse=%d edit_mode=%d active_mesh=%s terrain_hit=%d hit=(%.2f,%.2f,%.2f) mouse=(%.1f,%.1f) display=(%.1f,%.1f)\n",
						io.MouseDown[0] ? 1 : 0,
						io.MouseClicked[0] ? 1 : 0,
						io.WantCaptureMouse ? 1 : 0,
						edit_mode,
						active_mesh_name,
						p ? 1 : 0,
						p ? hit[0] : 0.0,
						p ? hit[1] : 0.0,
						p ? hit[2] : 0.0,
						io.MousePos.x,
						io.MousePos.y,
						io.DisplaySize.x,
						io.DisplaySize.y);
					fflush(stdout);
					last_interactive_place_state_log = now;
				}
			}

			if (p)
			{
				// limit hitworld to what we've already intersected with:
				ray_p[0] = hit[0];
				ray_p[1] = hit[1];
				ray_p[2] = hit[2];

				// normalize
				hit_nrm[0] /= HEIGHT_SCALE;
				hit_nrm[1] /= HEIGHT_SCALE;
				double nrm_len = sqrt(hit_nrm[0]*hit_nrm[0]+hit_nrm[1]*hit_nrm[1]+hit_nrm[2]*hit_nrm[2]);
				hit_nrm[0] /= nrm_len;
				hit_nrm[1] /= nrm_len;
				hit_nrm[2] /= nrm_len;
			}
			else
			{
				// clip ray so it won't hit hidden mesh parts below bottom plane
				// ray_p as at z=-1.1 and ray_v has z_length 0.1, so (p - v).z = -1.0 (bottom)
				ray_p[0] -= ray_v[0];
				ray_p[1] -= ray_v[1];
				ray_p[2] -= ray_v[2];
			}

			// Terrain probe — Shift+RightClick or Shift+hover, any edit_mode
			if (p && io.KeyShift && !io.KeyAlt && !io.KeyCtrl)
			{
				if (io.MouseDown[1])
				{
					probe_z = (int)round(hit[2]);
					br_probe[0] = (float)probe_z;
					br_probe[1] = 0.5f;
					TerrainProbe(p, hit);
				}
				else if (!io.MouseDown[0])
				{
					br_probe[0] = (float)round(hit[2]);
					br_probe[1] = 0.5f;
					TerrainProbe(p, hit);
				}
			}

			if (p || edit_mode == 2 && (io.KeyShift || io.KeyCtrl))
			{
				if (io.KeyAlt)
				{
					if (io.MouseDown[0])
					{
						URDO_Open();
						creating = -1;

						painting_x = (int)roundf(io.MousePos.x);
						painting_y = (int)roundf(io.MousePos.y);

						painting_dx = hit[0];
						painting_dy = hit[1];
					}
					else
					{
						// paint similar preview as for diag flipping but
						// hilight entire PATCH (instead of quad) and use RED color

						// add here quad preview
						double qx = floor(hit[0] / VISUAL_CELLS) * VISUAL_CELLS;
						double qy = floor(hit[1] / VISUAL_CELLS) * VISUAL_CELLS;
						br_quad[0] = (float)qx;
						br_quad[1] = (float)qy;
						br_quad[2] = -1.0f; // indicates full patch
					}
				}
				else
				if (edit_mode == 0)
				{
					if (io.KeyCtrl)
					{
						if (io.KeyShift)
						{
							// add here probe preview
							if (io.MouseDown[0] || io.MouseDown[1])
							{
								// height-probe + terrain cell probe
								// MouseDown[1] for macOS where Ctrl+Click = right-click
								probe_z = (int)round(hit[2]);
								br_probe[0] = (float)probe_z;
								br_probe[1] = 0.5f;
								TerrainProbe(p, hit);
							}
							else
							{
								// preview
								br_probe[0] = (float)round(hit[2]);
								br_probe[1] = 0.5f;
							}
						}
						else
						{
							// add here quad preview
							double qx = floor(hit[0] * HEIGHT_CELLS / VISUAL_CELLS) * VISUAL_CELLS / HEIGHT_CELLS;
							double qy = floor(hit[1] * HEIGHT_CELLS / VISUAL_CELLS) * VISUAL_CELLS / HEIGHT_CELLS;
							br_quad[0] = (float)qx;
							br_quad[1] = (float)qy;
							br_quad[2] = 1.0f; // indicates real height quad

							if (!diag_flipped && io.MouseDown[0])
							{
								struct mod_floor
								{
									mod_floor(int d) : y(d) {}
									int mod(int x)
									{
										int r = x % y;
										if (/*(r != 0) && ((r < 0) != (y < 0))*/ r && (r^y)<0)
											r += y;
										return r;
									}
									int y;
								} mf(HEIGHT_CELLS);

								// floor xy hit coords to height cells
								//int hx = (int)floor(hit[0] * HEIGHT_CELLS / VISUAL_CELLS) % HEIGHT_CELLS;
								//int hy = (int)floor(hit[1] * HEIGHT_CELLS / VISUAL_CELLS) % HEIGHT_CELLS;

								int hx = mf.mod((int)floor(hit[0] * HEIGHT_CELLS / VISUAL_CELLS));
								int hy = mf.mod((int)floor(hit[1] * HEIGHT_CELLS / VISUAL_CELLS));

								{
									uint16_t diag = GetTerrainDiag(p);
									diag ^= 1 << (hx + hy * HEIGHT_CELLS);

									URDO_Diag(p);
									SetTerrainDiag(p, diag);
								}

								// one per click
								diag_flipped = true;
							}
						}
					}
					else
					{
						br_xyra[0] = (float)hit[0];
						br_xyra[1] = (float)hit[1];
						br_xyra[3] = br_alpha;

						if (io.MouseDown[0])
						{
							//BEGIN
							URDO_Open();
							g_painted_patches.clear();
							painting = 1;

							painting_x = (int)roundf(io.MousePos.x);
							painting_y = (int)roundf(io.MousePos.y);

							painting_dx = hit[0];
							painting_dy = hit[1];
							paint_dist = 0.0;

							float alpha = br_alpha;
							br_alpha *= STAMP_A;
							Stamp(hit[0], hit[1]);
							br_alpha = alpha;

							// stamped, don't apply preview to it
						}
					}
				}
				else
				if (edit_mode == 1)
				{
					if (io.KeyCtrl)
					{
						if (io.KeyShift)
						{
							// add here probe preview
							if (io.MouseDown[0] || io.MouseDown[1])
							{
								// height-probe + terrain cell probe
								// MouseDown[1] for macOS where Ctrl+Click = right-click
								probe_z = (int)round(hit[2]);
								br_probe[0] = (float)probe_z;
								br_probe[1] = 0.5f;
								TerrainProbe(p, hit);
							}
							else
							{
								// preview
								br_probe[0] = (float)round(hit[2]);
								br_probe[1] = 0.5f;
							}
						}
						else
						{
							// add here quad preview of matid probe
							double qx = floor(hit[0]);
							double qy = floor(hit[1]);
							br_quad[0] = (float)qx;
							br_quad[1] = (float)qy;
							br_quad[2] = 2.0f; // indicates quad on visual map

							if (io.MouseDown[0])
							{
								struct mod_floor
								{
									mod_floor(int d) : y(d) {}
									int mod(int x)
									{
										int r = x % y;
										if (/*(r != 0) && ((r < 0) != (y < 0))*/ r && (r^y)<0)
											r += y;
										return r;
									}
									int y;
								} mf(VISUAL_CELLS);

								// sample matid
								int uv[2] = { mf.mod((int)qx), mf.mod((int)qy) };
								uint16_t* visual = GetTerrainVisualMap(p);
								active_material = visual[uv[0] + uv[1]*VISUAL_CELLS] & 0xFF;
							}
						}
					}
					else
					{
						br_xyra[0] = (float)hit[0];
						br_xyra[1] = (float)hit[1];
						br_xyra[2] = (float)br_radius * 0.5f;
						br_xyra[3] = 2; // 2 -> painting matid

						if (br_limit)
						{
							if (io.KeyShift)
								br_probe[2] = -1.0;
							else
								br_probe[2] = 1.0;
						}
						else
							br_probe[2] = 0;

						if (io.MouseDown[0])
						{
							//BEGIN
							URDO_Open();
							g_painted_patches.clear();
							painting = 2;

							MatIDStamp stamp;
							stamp.r = br_radius;
							stamp.hit = hit;
							stamp.z = br_probe[0];
							stamp.z_lim = br_limit ? (io.KeyShift ? -1 : 1) : 0;

							URDO_Open();
							QueryTerrain(terrain, hit[0], hit[1], br_radius * 1.5, 0x00, MatIDStamp::SetMatCB, &stamp);
							URDO_Close();

							painting_x = (int)roundf(io.MousePos.x);
							painting_y = (int)roundf(io.MousePos.y);

							painting_dx = hit[0];
							painting_dy = hit[1];
							paint_dist = 0.0;
						}
					}
				}
				else
				if (edit_mode == 3)
				{
					if (io.KeyCtrl)
					{
						if (io.KeyShift)
						{
							// add here probe preview
							if (io.MouseDown[0] || io.MouseDown[1])
							{
								// height-probe + terrain cell probe
								// MouseDown[1] for macOS where Ctrl+Click = right-click
								probe_z = (int)round(hit[2]);
								br_probe[0] = (float)probe_z;
								br_probe[1] = 0.5f;
								TerrainProbe(p, hit);
							}
							else
							{
								// preview
								br_probe[0] = (float)round(hit[2]);
								br_probe[1] = 0.5f;
							}
						}
						else
						{
							// add here quad preview of matid probe
							double qx = floor(hit[0]);
							double qy = floor(hit[1]);
							br_quad[0] = (float)qx;
							br_quad[1] = (float)qy;
							br_quad[2] = 2.0f; // indicates quad on visual map (elev)

							if (io.MouseDown[0])
							{
								struct mod_floor
								{
									mod_floor(int d) : y(d) {}
									int mod(int x)
									{
										int r = x % y;
										if (/*(r != 0) && ((r < 0) != (y < 0))*/ r && (r^y) < 0)
											r += y;
										return r;
									}
									int y;
								} mf(VISUAL_CELLS);

								// sample elev
								int uv[2] = { mf.mod((int)qx), mf.mod((int)qy) };
								uint16_t* visual = GetTerrainVisualMap(p);
								active_elev = ((visual[uv[0] + uv[1] * VISUAL_CELLS]) >> 15) & 0x1;
							}
						}
					}
					else
					{
						br_xyra[0] = (float)hit[0];
						br_xyra[1] = (float)hit[1];
						br_xyra[2] = (float)br_radius * 0.5f;
						br_xyra[3] = 4; // 4 -> painting mat-elev

						if (br_limit)
						{
							if (io.KeyShift)
								br_probe[2] = -1.0;
							else
								br_probe[2] = 1.0;
						}
						else
							br_probe[2] = 0;

						if (io.MouseDown[0])
						{
							//BEGIN
							URDO_Open();
							g_painted_patches.clear();
							painting = 3;

							MatIDStamp stamp;
							stamp.r = br_radius;
							stamp.hit = hit;
							stamp.z = br_probe[0];
							stamp.z_lim = br_limit ? (io.KeyShift ? -1 : 1) : 0;

							URDO_Open();
							QueryTerrain(terrain, hit[0], hit[1], br_radius * 1.5, 0x00, MatIDStamp::SetMatCB, &stamp);
							URDO_Close();

							painting_x = (int)roundf(io.MousePos.x);
							painting_y = (int)roundf(io.MousePos.y);

							painting_dx = hit[0];
							painting_dy = hit[1];
							paint_dist = 0.0;
						}
					}
				}
				else
				if (edit_mode == 2)
				{
					if (!active_mesh)
					{
						static uint64_t last_no_mesh_log = 0;
						uint64_t now = FL3714Now();
						if (now - last_no_mesh_log > 1000000)
						{
							printf("[EDITOR] [FL-3714] mesh_place_blocked reason=no_active_mesh world=%d terrain=%d\n",
								world ? 1 : 0,
								terrain ? 1 : 0);
							fflush(stdout);
							last_no_mesh_log = now;
						}
						inst_preview = 0;
					}
					else
					{

					if (io.KeyShift && !io.KeyCtrl && !inst_added)
					{
						if (io.MouseClicked[0])
						{
							marquee_active = true;
							marquee_start = io.MousePos;
						}
					}

					if (marquee_active)
					{
						marquee_end = io.MousePos;
						if (io.MouseReleased[0])
						{
							if (!io.KeyShift) ClearSelection();
							SelectArea(tm, marquee_start, marquee_end);
							marquee_active = false;
						}


					}

					if (!inst_added || !io.MouseDown[0])
					{
						Inst* inst = 0;
						if (io.KeyCtrl || io.KeyShift)
						{
							// HITTEST!
							inst = HitWorld(world, ray_p, ray_v, hit, hit_nrm, false, HitFilter(true));

							// and set this inst for hover hilight
							hover_inst = inst;
						}

						if (io.KeyShift && !marquee_active)
						{
							// pick, works also with CTRL (delete)
							inst_preview = 0;

							if (inst && !inst_added && io.MouseDown[0])
							{
								active_mesh = GetInstMesh(inst);
								selected_inst = inst;
                                drag_inst = inst; // Start Drag
								printf("[Editor] Selected instance: %p\n", inst);
								inst_added = true;
							}
						}
						else
						if (!io.KeyCtrl)
						{
							// hit against meshes, stacking?
							inst = HitWorld(world, ray_p, ray_v, hit, 0, false, HitFilter(true));

							if (hit[2] < probe_z)
								hit[2] = probe_z;

							// pretranslate and scale
							MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(active_mesh);

							double ptm[16] = { 0 };
							ptm[0] = pow(2.0, mp->scale_val[0] + 2 * mp->scale_rnd[0] * ((double)fast_rand() / 0x7fff - 0.5));
							ptm[5] = pow(2.0, mp->scale_val[1] + 2 * mp->scale_rnd[1] * ((double)fast_rand() / 0x7fff - 0.5));
							ptm[10] = pow(2.0, mp->scale_val[2] + 2 * mp->scale_rnd[2] * ((double)fast_rand() / 0x7fff - 0.5));
							ptm[15] = 1;
							ptm[12] = 0; //mp->pre_trans[0] * ptm[0];
							ptm[13] = 0; //mp->pre_trans[1] * ptm[5];
							ptm[14] = 0; //mp->pre_trans[2] * ptm[10];

							// rot loc Z
							double ztm[16];
							double loc_z[3] = { 0,0,1 };
							double ang_z = mp->rotate_locZ_val + 360 * mp->rotate_locZ_rnd*((double)fast_rand() / 0x7fff - 0.5);
							Rotation(loc_z, ang_z * M_PI / 180, ztm);

							// rot xy
							double rot[16]; //rtm[16];
							double rot_xy[3] =
							{
								mp->rotate_XY_val[0] / 180.0 + 2 * mp->rotate_XY_rnd[0] * ((double)fast_rand() / 0x7fff - 0.5),
								mp->rotate_XY_val[1] / 180.0 + 2 * mp->rotate_XY_rnd[1] * ((double)fast_rand() / 0x7fff - 0.5),
								0
							};

							double ang_xy = sqrt(rot_xy[0] * rot_xy[0] + rot_xy[1] * rot_xy[1]);
							if (ang_xy != 0)
							{
								rot_xy[0] /= ang_xy;
								rot_xy[1] /= ang_xy;
							}

							if (ang_xy > 1)
								ang_xy = 1;

							Rotation(rot_xy, ang_xy * M_PI, rot/*rtm*/);

							// last thing, align with terrain normal!
							double up[4] = { 0,0,1,0 };
							double dir[4];
							Product(rot,/*rtm,*/up, dir);

							// alignment rot axis
							double align_axis[3];
							CrossProduct(dir, hit_nrm, align_axis);

							// alignment angle
							double align_len = sqrt(align_axis[0] * align_axis[0] + align_axis[1] * align_axis[1] + align_axis[2] * align_axis[2]);
							double align_ang = asin(align_len);

							if (align_len > 0)
							{
								align_axis[0] /= align_len;
								align_axis[1] /= align_len;
								align_axis[2] /= align_len;
							}

							double atm[16];
							Rotation(align_axis, align_ang * mp->rotate_align, atm);

							double rtm[16];
							MatProduct(atm, rot, rtm);

							double itm[16] = { 0 };

							// post-scale and translate
							itm[0] = 1;
							itm[5] = 1;
							itm[10] = HEIGHT_SCALE;
							itm[15] = 1;

							itm[12] = hit[0];
							itm[13] = hit[1];
							itm[14] = hit[2] + mp->height;

							double tm1[16];
							double tm2[16];

							// inst_tm = itm * rtm * ztm * ptm
							MatProduct(itm, rtm, tm1);
							MatProduct(ztm, ptm, tm2);
							MatProduct(tm1, tm2, inst_tm);

							int inst_story_id = -1; // READ IT FROM UI

							if (!inst_added && io.MouseDown[0])
							{
								int flags = INST_USE_TREE | INST_VISIBLE;
								// inst = CreateInst(active_mesh, flags, inst_tm, 0);
								char active_mesh_name[256] = {0};
								GetMeshName(active_mesh, active_mesh_name, sizeof(active_mesh_name));
								uint64_t place_t0 = FL3714Now();
								inst = URDO_Create(active_mesh, flags, inst_tm, inst_story_id);
								uint64_t create_us = FL3714Now() - place_t0;

								inst_added = true;
								inst_list_dirty = true;
								uint64_t rebuild_t0 = FL3714Now();
								RebuildWorld(world);
								printf("[EDITOR] [FL-3714] mesh_place mesh=%s x=%.2f y=%.2f z=%.2f create_ms=%.3f rebuild_ms=%.3f inst=%d\n",
									active_mesh_name,
									inst_tm[12],
									inst_tm[13],
									inst_tm[14],
									(double)create_us / 1000.0,
									(double)(FL3714Now() - rebuild_t0) / 1000.0,
									inst ? 1 : 0);
								fflush(stdout);
							}
							else
							{
								// we'll need to paint active_mesh with inst_tm
								inst_preview = active_mesh;
							}
						}

						if (io.KeyCtrl)
						{
							inst_preview = 0;

							if (inst)
							{
								if (!inst_added && io.MouseDown[0])
								{
									// delete this inst (clear hilight + selection too)
									hover_inst = 0;
									if (selected_inst == inst) selected_inst = 0;
									if (drag_inst == inst) drag_inst = 0;

									//DeleteInst(inst);
									URDO_Delete(inst);
									inst_list_dirty = true;

									inst_added = true;
								}
							}
						}
					}
					}
				}
				else
				if (edit_mode == 4)
				{
					if (!inst_added)
					{
						Inst* inst = HitWorld(world, ray_p, ray_v, hit, 0, false, HitFilter(true));
						Sprite* sprite = inst ? GetInstSprite(inst,0,0,0,0,0) : 0;

						if (io.KeyCtrl)
						{
							// with ctrl don't paint sprite_preview !!!

							if (!inst_added && sprite)
							{
								if (io.MouseDown[0])
								{
									// delete it
									URDO_Delete(inst);
									inst_list_dirty = true;
									inst_added = true;
									hover_inst = 0;
								}
								else
								{
									// and set this inst for hover hilight
									hover_inst = inst;
								}
							}
							else
							{
								hover_inst = 0;
							}
						}
						else
						{
							SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(active_sprite);

							if (!inst_added && io.MouseDown[0])
							{
								int flags = INST_USE_TREE | INST_VISIBLE;
								// inst = CreateInst(active_mesh, flags, inst_tm, 0);

								float pos[3] = { (float)hit[0], (float)hit[1], (float)hit[2] + sp->height };

								int _anim = sp->rand_anim ? fast_rand() % active_sprite->anims : sp->anim;
								int _frame = sp->rand_frame ? fast_rand() % active_sprite->anim[_anim].length : sp->frame % active_sprite->anim[_anim].length;
								float _yaw = sp->rand_yaw ? fast_rand() % 360 : sp->yaw;

								int inst_story_id = -1; // TODO: READ IT FROM UI

								Inst* inst = URDO_Create(world, active_sprite, flags, pos, _yaw, _anim, _frame, sp->t, inst_story_id);

								inst_added = true;
								inst_list_dirty = true;
								RebuildWorld(world);
							}
							else
							{
								// we'll need to paint active_mesh with inst_tm
								sprite_preview = true;
								sprite_preview_pos[0] = (float)(hit[0]);
								sprite_preview_pos[1] = (float)(hit[1]);
								sprite_preview_pos[2] = (float)(hit[2]) + sp->height;
							}
						}
					}
				}
				else
				if (edit_mode == 5)
				{
					if (!inst_added)
					{
						// we are insterested ONLY in non-volatile items!
						Inst* inst = HitWorld(world, ray_p, ray_v, hit, 0, false, HitFilter(true));
						Item* item = inst ? GetInstItem(inst,0,0) : 0;

						if (io.KeyCtrl)
						{
							// with ctrl don't paint sprite_preview !!!

							if (!inst_added && item)
							{
								if (io.MouseDown[0])
								{
									// delete it
									URDO_Delete(inst);
									inst_list_dirty = true;
									inst_added = true;
									hover_inst = 0;
								}
								else
								{
									// and set this inst for hover hilight
									hover_inst = inst;
								}
							}
							else
							{
								hover_inst = 0;
							}
						}
						else
						{
							if (!inst_added && io.MouseDown[0])
							{
								int flags = INST_USE_TREE | INST_VISIBLE;
								// inst = CreateInst(active_mesh, flags, inst_tm, 0);

								float pos[3] = { (float)hit[0], (float)hit[1], (float)hit[2] };

								int inst_story_id = -1; // READ IT FROM UI

								const EditorBundleItemChoice* choice = &editor_bundle_items[active_item];
								Item* item = CreateItem();
								item->item_definition_id = choice->item_definition_id;
								item->visual_style_id = choice->visual_style_id;
								item->presentation_kind_id = choice->presentation_kind_id;
								item->count = 1;
								item->purpose = Item::EDIT;
								item->inst = 0;
								item->inst = URDO_Create(world, item, flags, pos, 0, inst_story_id);

								// and world clone
								Item* clone = CreateItem();
								clone->item_definition_id = choice->item_definition_id;
								clone->visual_style_id = choice->visual_style_id;
								clone->presentation_kind_id = choice->presentation_kind_id;
								clone->count = 1;
								clone->purpose = Item::WORLD;
								clone->inst = 0;
								clone->inst = CreateInst(world, clone, flags | INST_VOLATILE, pos, 0, story_id);

								inst_added = true;
								inst_list_dirty = true;
								RebuildWorld(world);
							}
							else
							{
								// we'll need to paint active_mesh with inst_tm
								sprite_preview = true;
								sprite_preview_pos[0] = (float)(hit[0]);
								sprite_preview_pos[1] = (float)(hit[1]);
								sprite_preview_pos[2] = (float)(hit[2]);
							}
						}
					}
				}
				else
				if (edit_mode == 6)
				{
					Inst* inst = HitWorld(world, ray_p, ray_v, hit, 0, false, HitFilter(true));

					if (io.KeyCtrl)
					{
						// hit test against all enemygens
						// pick closest one

						EnemyGen* eg = HitEnemyGen(ray_p, ray_v);

						if (io.MouseDown[0] && !inst_added)
						{
							// delete it
							hover_eg = 0;

							if (eg)
							{
								inst_added = true;
								DeleteEnemyGen(eg);
							}
						}
						else
						{
							// hilight it
							hover_eg = eg;
						}
					}
					else
					{
						hover_eg = 0;
						if (!inst_added && io.MouseDown[0])
						{
							int flags = INST_USE_TREE | INST_VISIBLE;
							// inst = CreateInst(active_mesh, flags, inst_tm, 0);

							//AddEnemyGen(hit);
							EnemyGen* eg = (EnemyGen*)malloc(sizeof(EnemyGen));
							eg->pos[0] = (float)(hit[0]);
							eg->pos[1] = (float)(hit[1]);
							eg->pos[2] = (float)(hit[2]);

							eg->alive_max = eg_alive_max;
							eg->revive_min = eg_revive_min;
							eg->revive_max = eg_revive_max;
							eg->armor = eg_armor;
							eg->helmet = eg_helmet;
							eg->shield = eg_shield;
							eg->sword = eg_sword;
							eg->crossbow = eg_crossbow;

							eg->prev = 0;
							eg->next = enemygen_head;

							if (enemygen_head)
								enemygen_head->prev = eg;
							else
								enemygen_tail = eg;

							enemygen_head = eg;
							inst_added = true;
						}
						else
						{
							enemygen_preview = true;
							enemygen_preview_pos[0] = (float)(hit[0]);
							enemygen_preview_pos[1] = (float)(hit[1]);
							enemygen_preview_pos[2] = (float)(hit[2]);
						}
					}
				}
				else
				if (edit_mode == 7)
				{
					//if (!inst_added)
					{
						Inst* inst = HitWorld(world, ray_p, ray_v, hit, 0, false, HitFilter(true));
						hover_inst = inst;

						if (inst)
						{
							hover_story_hover = true;
							hover_story_value = GetInstStoryID(inst);
							if (io.MouseDown[0] && !inst_added)
							{
								SetInstStoryID(inst,story_id);
								inst_added = true;
								hover_inst = 0;
							}
						}
						else
						{
							hover_story_hover = false;
						}
					}
				}
			}
			else
			{
				if (io.KeyAlt)
				{
					double t = (probe_z - ray_p[2]) / ray_v[2];
					double vx = ray_p[0] + t * ray_v[0];
					double vy = ray_p[1] + t * ray_v[1];

					// probably create
					if (io.MouseDown[0])
					{
						URDO_Open();
						creating = +1;

						painting_x = (int)roundf(io.MousePos.x);
						painting_y = (int)roundf(io.MousePos.y);

						painting_dx = vx;
						painting_dy = vy;
					}
					else
					{
						create_preview = true;
						create_preview_px = (int)floor(vx / VISUAL_CELLS);
						create_preview_py = (int)floor(vy / VISUAL_CELLS);

						// paint imaginary patch?
						// that requires extra draw command!
					}
				}
			}
		}
	}

	render_context.hover_inst = hover_inst;

	if (panning || spinning)
	{
		br_xyra[3] = 0;
	}

	if (edit_mode==0 && io.KeysDown[A3D_LSHIFT])
	{
		br_xyra[2] = -br_xyra[2];
	}

	// 4 clip planes in clip-space

	double clip_left[4] =   { 1, 0, 0,+.9 };
	double clip_right[4] =  {-1, 0, 0,+.9 };
	double clip_bottom[4] = { 0, 1, 0,+.9 };
	double clip_top[4] =    { 0,-1, 0,+.9 }; // adjust by max brush descent

	double brush_extent = cos(pitch) * br_xyra[3] * br_xyra[2] / ry;

	if (br_xyra[2] > 0)
	{
		// adjust by max brush ASCENT
		if (br_xyra[3] > 0)
			clip_bottom[3] += brush_extent;

		// adjust by max brush DESCENT
		if (br_xyra[3] < 0)
			clip_top[3] -= brush_extent;
	}

	// transform them to world-space (mul by tm^-1)

	double clip_world[4][4];
	TransposeProduct(tm, clip_left, clip_world[0]);
	TransposeProduct(tm, clip_right, clip_world[1]);
	TransposeProduct(tm, clip_bottom, clip_world[2]);
	TransposeProduct(tm, clip_top, clip_world[3]);

	int planes = 4;
	int view_flags = 0xAA; // should contain only bits that face viewing direction

	double noon_yaw[2] =
	{
		// zero is behind viewer
		-sin(-lit_yaw*M_PI / 180),
		-cos(-lit_yaw*M_PI / 180),
	};

	double dusk_yaw[3] =
	{
		-noon_yaw[1],
		noon_yaw[0],
		0
	};

	double noon_pos[4] =
	{
		noon_yaw[0]*cos(lit_pitch*M_PI / 180),
		noon_yaw[1]*cos(lit_pitch*M_PI / 180),
		sin(lit_pitch*M_PI / 180),
		0
	};

	double lit_axis[3];

	CrossProduct(dusk_yaw, noon_pos, lit_axis);

	double time_tm[16];
	Rotation(lit_axis, (lit_time-12)*M_PI / 12, time_tm);

	double lit_pos[4];
	Product(time_tm, noon_pos, lit_pos);

	float lt[4] =
	{
		(float)lit_pos[0],
		(float)lit_pos[1],
		(float)lit_pos[2],
		ambience
	};

	// term
	global_lt[0] = lt[0];
	global_lt[1] = lt[1];
	global_lt[2] = lt[2];
	global_lt[3] = ambience;

		glEnable(GL_DEPTH_TEST);
		glDepthFunc(GL_GEQUAL);
		uint64_t terrain_render_start_us = FL3714Now();
		bool use_terrain_overview = g_editor_terrain_overview.ShouldUse(terrain, font_size * VISUAL_CELLS);
		if (use_terrain_overview)
		{
			// [FL-3851] Proof captures need the settled overview, not the first
			// provisional seed colors. This synchronous refresh is only for the
			// explicit clean-capture path; interactive frames stay bounded.
			g_editor_terrain_overview.BuildVertices(terrain, g_editor_terrain_overview_vertices,
				g_mcp_clean_capture_dir[0] != 0);
			rc->RenderOverview(tm, g_editor_terrain_overview_vertices.empty() ? 0 : &g_editor_terrain_overview_vertices[0],
				(int)g_editor_terrain_overview_vertices.size(), g_editor_terrain_overview.last_tiles);
		}
		rc->BeginPatches(tm, lt, br_xyra, br_quad, br_probe);
		rc->overview_mode = use_terrain_overview;
		rc->overview_tiles = use_terrain_overview ? g_editor_terrain_overview.last_tiles : 0;
		if (use_terrain_overview)
		{
			rc->patch_budget = EditorTerrainOverviewCache::kExactFocusPatchBudget;
			double exact_radius = br_radius * 2.0;
			if (exact_radius < 96.0)
				exact_radius = 96.0;
			QueryTerrain(terrain, pos_x, pos_y, exact_radius, view_flags, RenderContext::RenderPatch, rc);
			if (br_xyra[3] != 0.0f)
				QueryTerrain(terrain, br_xyra[0], br_xyra[1], exact_radius, view_flags, RenderContext::RenderPatch, rc);
			g_editor_terrain_overview.last_exact_budget = EditorTerrainOverviewCache::kExactFocusPatchBudget;
			g_editor_terrain_overview.last_exact_rendered = rc->patches;
		}
		else
		{
			rc->patch_budget = 0;
			QueryTerrain(terrain, planes, clip_world, view_flags, RenderContext::RenderPatch, rc);
			g_editor_terrain_overview.last_exact_budget = 0;
			g_editor_terrain_overview.last_exact_rendered = 0;
		}

		merge.dx = (int)floor(pos_x / VISUAL_CELLS + 0.5);
		merge.dy = (int)floor(pos_y / VISUAL_CELLS + 0.5);

	if (merge._terrain)
	{
		int t[2];
		GetTerrainBase(merge._terrain, t);
		int o[2] = { t[0] - merge.dx, t[1] - merge.dy};
		SetTerrainBase(merge._terrain, o);
		if (use_terrain_overview)
			QueryTerrain(merge._terrain, pos_x, pos_y, 96.0, view_flags, RenderContext::RenderPatch, rc);
		else
			QueryTerrain(merge._terrain, planes, clip_world, view_flags, RenderContext::RenderPatch, rc);
			SetTerrainBase(merge._terrain, t);
		}

		rc->EndPatches();
		uint64_t terrain_render_us = FL3714Now() - terrain_render_start_us;
		if (terrain_render_us > 50000 || terrain_render_start_us - g_fl3714_last_render_log_us > 1000000)
		{
			printf("[EDITOR] [FL-3714] terrain_render mode=%s patches=%d overview_tiles=%d overview_refreshed=%d overview_dirty=%d draws=%d tex_changes=%d elapsed_ms=%.3f pos=(%.1f,%.1f,%.1f) font_size=%.3f planes=%d exact_budget=%d exact_rendered=%d budget_skipped=%d\n",
				rc->overview_mode ? "overview" : "exact",
				rc->patches,
				rc->overview_tiles,
				g_editor_terrain_overview.last_refreshed_tiles,
				g_editor_terrain_overview.last_dirty_remaining,
				rc->draws,
				rc->changes,
				terrain_render_us / 1000.0,
				pos_x,
				pos_y,
				pos_z,
				font_size,
				planes,
				g_editor_terrain_overview.last_exact_budget,
				g_editor_terrain_overview.last_exact_rendered,
				rc->patches_budget_skipped);
			fflush(stdout);
			g_fl3714_last_render_log_us = terrain_render_start_us;
		}

		rc->BeginMeshes(tm, lt);

	QueryWorldCB cb = { RenderContext::RenderMesh , RenderContext::RenderSprite };
	QueryWorld(world, planes, clip_world, &cb, rc);

	if (merge._world)
		QueryWorld(merge._world, 0,0/*planes, clip_world*/, &cb, rc);

	if (inst_preview)
		RenderContext::RenderMesh(0, inst_preview, inst_tm, rc);

	if (sprite_preview)
	{
		if (item_preview_sprite)
		{
			RenderContext::RenderSprite(0, item_preview_sprite, sprite_preview_pos, 0, -1, Item::EDIT, 0, rc);
		}
		else if (active_sprite)
		{
			SpritePrefs* sp = (SpritePrefs*)GetSpriteCookie(active_sprite);
			int _anim = sp->rand_anim ? fast_rand() % active_sprite->anims : sp->anim;
			int _frame = sp->rand_frame ? fast_rand() % active_sprite->anim[_anim].length : sp->frame % active_sprite->anim[_anim].length;
			float _yaw = sp->rand_yaw ? fast_rand() % 360 : sp->yaw;
			RenderContext::RenderSprite(0, active_sprite, sprite_preview_pos, _yaw, _anim, _frame, sp->t, rc);
		}
	}

	if (enemygen_sprite)
	{
		if (enemygen_preview)
		{
			// draw something
			RenderContext::RenderSprite(0, enemygen_sprite, enemygen_preview_pos, 0, -1, Item::EDIT, 0, rc);
		}

		EnemyGen* eg = enemygen_head;
		while (eg)
		{
			// draw something
			RenderContext::RenderSprite(0, enemygen_sprite, eg->pos, 0, 0, eg==hover_eg ? 1 : 0, 0, rc);
			eg = eg->next;
		}
	}

	// Markers — transient overlays, same pattern as EnemyGen
	if (enemygen_sprite)
	{
		Marker* mk = marker_head;
		while (mk)
		{
			RenderContext::RenderSprite(0, enemygen_sprite, mk->pos, 0, -1, Item::EDIT, 0, rc);
			mk = mk->next;
		}
	}

//	if (sprite_preview)
//		RenderContext::RenderSprite(sprite_preview, ..., rc);

	rc->EndMeshes();


	// STENCIL PASS (terrain z-offset)
	// (enabled depth test, disabled depth write)
	// stencil ++ on fronface, stencil -- on backface (wrap mode)

	// SHADOW PASS (screen quad)
	// ...


	// bsp hierarchy boxes
	/*
	rc->BeginBSP(tm);
	QueryWorldBSP(world, planes, clip_world, RenderContext::RenderBSP, rc);
	rc->EndBSP();
	*/

	// overlay patch creation
	// slihouette of newly created patch

	if (hover_inst)
	{
		Mesh* hover_mesh = GetInstMesh(hover_inst);
		if (hover_mesh)
		{
			glPolygonMode(GL_FRONT_AND_BACK, GL_LINE);
			glEnable(GL_POLYGON_OFFSET_LINE);
			glPolygonOffset(1, -1);

			rc->BeginMeshes(tm, lt);
			//glEnable(GL_CULL_FACE);

			float dif[4] = { 0,0,0,1 };
			glUniform4fv(rc->mesh_lt_dif_clr, 1, dif);

			float amb[4] = { 1,0,0,1 };
			glUniform4fv(rc->mesh_lt_amb_clr, 1, amb);

			if (io.KeyCtrl)
				glLineWidth(3);
			else
				glLineWidth(1);

			double itm[16];
			GetInstTM(hover_inst, itm);
			RenderContext::RenderMesh(hover_inst, hover_mesh, itm, rc);
			rc->EndMeshes();


			//glDisable(GL_CULL_FACE);
			glPolygonOffset(0, 0);
			glPolygonMode(GL_FRONT_AND_BACK, GL_FILL);
			glDisable(GL_POLYGON_OFFSET_LINE);
			glLineWidth(1);
		}
		// CURRENTLY ONLY ID IS HIGHLIGHTED
		/*
		else // so it must be Item or Sprite
		{
			float pos[3], yaw;
			int anim = 0, frame = 0, reps[4] = { 0 };
			Sprite* s = 0;
			s = GetInstSprite(hover_inst, pos, &yaw, &anim, &frame, reps);

			float angle = yaw;
			int ang = (int)floor((angle - rot_yaw) * s->angles / 360.0f + 0.5f);
			ang = ang >= 0 ? ang % s->angles : (ang % s->angles + s->angles) % s->angles;

			int i = frame + ang * s->anim[anim].length;
			//if (proj && s->projs > 1)
			//	i += s->anim[anim].length * s->angles;
			Sprite::Frame* f = s->atlas + s->anim[anim].frame_idx[i];

			// TODO:
			// frame it
			// ...
		}
		*/
	}

	// [FL-3851] CLEAN CAPTURE POINT — capture frame before UI overlays
	{
		if (g_mcp_clean_capture_dir[0]) {
				ImGuiIO& io = ImGui::GetIO();
				int vp_w = (int)(io.DisplaySize.x * io.DisplayFramebufferScale.x);
				int vp_h = (int)(io.DisplaySize.y * io.DisplayFramebufferScale.y);
				unsigned char* px = (unsigned char*)malloc(vp_w * vp_h * 3);
				if (px) {
					glPixelStorei(GL_PACK_ALIGNMENT, 1);
					glReadPixels(0, 0, vp_w, vp_h, GL_RGB, GL_UNSIGNED_BYTE, px);
					char png_path[2048];
					snprintf(png_path, sizeof(png_path), "%s/frame.png", g_mcp_clean_capture_dir);
					unsigned char* flipped = (unsigned char*)malloc(vp_w * vp_h * 3);
					if (flipped) {
						for (int row = 0; row < vp_h; row++) {
							memcpy(flipped + row * vp_w * 3,
							       px + (vp_h - 1 - row) * vp_w * 3, vp_w * 3);
						}
						stbi_write_png(png_path, vp_w, vp_h, 3, flipped, vp_w * 3);
						free(flipped);
					}
					free(px);
					char cam_path[2048];
					snprintf(cam_path, sizeof(cam_path), "%s/frame.camera.json", g_mcp_clean_capture_dir);
					FILE* fc = fopen(cam_path, "w");
					if (fc) {
						fprintf(fc, "{\n");
						fprintf(fc, "  \"tm\": [");
						for (int i = 0; i < 16; i++) {
							fprintf(fc, "%.15g%s", tm[i], i < 15 ? ", " : "");
						}
						fprintf(fc, "],\n");
						fprintf(fc, "  \"viewport_px\": [%d, %d],\n", vp_w, vp_h);
						fprintf(fc, "  \"display_size\": [%.1f, %.1f],\n", io.DisplaySize.x, io.DisplaySize.y);
						fprintf(fc, "  \"dpi_scale\": [%.3f, %.3f],\n", io.DisplayFramebufferScale.x, io.DisplayFramebufferScale.y);
						fprintf(fc, "  \"pos\": [%.1f, %.1f, %.1f],\n", pos_x, pos_y, pos_z);
						fprintf(fc, "  \"yaw\": %.4f,\n", rot_yaw);
						fprintf(fc, "  \"pitch\": %.4f,\n", rot_pitch);
						fprintf(fc, "  \"font_size\": %.6f,\n", font_size);
						fprintf(fc, "  \"z_scale\": %.8f,\n", z_scale);
						fprintf(fc, "  \"HEIGHT_SCALE\": %d,\n", HEIGHT_SCALE);
						fprintf(fc, "  \"HEIGHT_CELLS\": %d,\n", HEIGHT_CELLS);
						fprintf(fc, "  \"VISUAL_CELLS\": %d,\n", VISUAL_CELLS);
						fprintf(fc, "  \"overview\": {\"mode\": %s, \"tiles\": %d, \"dirty_remaining\": %d},\n",
							g_editor_terrain_overview.last_used ? "true" : "false",
							g_editor_terrain_overview.last_tiles,
							g_editor_terrain_overview.last_dirty_remaining);
						fprintf(fc, "  \"osm\": {\n");
						fprintf(fc, "    \"valid\": %s,\n", g_osm_proj.valid ? "true" : "false");
						fprintf(fc, "    \"scene_lat\": %.7f,\n", g_osm_proj.scene_lat);
						fprintf(fc, "    \"scene_lon\": %.7f,\n", g_osm_proj.scene_lon);
						fprintf(fc, "    \"content_scale\": %.1f,\n", g_osm_proj.content_scale);
						fprintf(fc, "    \"shift\": [%.1f, %.1f],\n", g_osm_proj.shift_x, g_osm_proj.shift_y);
						fprintf(fc, "    \"cal\": [%.1f, %.1f]\n", g_osm_proj.cal_x, g_osm_proj.cal_y);
						fprintf(fc, "  },\n");
						fprintf(fc, "  \"map_path\": \"%s\"\n", g_current_map_path);
						fprintf(fc, "}\n");
						fclose(fc);
					}
					printf("[MCP] CAPTURE_CLEAN_FRAME: %dx%d -> %s + camera.json\n", vp_w, vp_h, png_path);
					fflush(stdout);
				}
				g_mcp_clean_capture_dir[0] = 0;
				if (g_batch_exit_after_clean_capture) {
					exit(0);
				}
		}
	}
	// END CLEAN CAPTURE

	if (create_preview)
	{
		uint16_t ghost[4 * HEIGHT_CELLS];
		bool exist = CalcTerrainGhost(terrain, create_preview_px, create_preview_py, probe_z, ghost);
		if (!exist)
			rc->PaintGhost(tm, create_preview_px, create_preview_py, probe_z, ghost);
	}



	glDisable(GL_DEPTH_TEST);

	if (g_editor_minimap_visible)
		DrawEditorMinimapMarkerLabels(tm);
	ImGui::Render();

	//glUseProgram(0); // You may want this if using this code in an OpenGL 3+ context where shaders may be bound, but prefer using the GL3+ code.

	ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());

	// [FL-4131] UI CAPTURE POINT — full composited framebuffer (game + ImGui panels post-RenderDrawData).
	// Unlike CAPTURE_CLEAN_FRAME which fires before ImGui, this captures the complete visible frame
	// including all open editor panels. Only valid in headed/daemon mode.
	if (g_mcp_ui_capture_dir[0]) {
		ImGuiIO& io = ImGui::GetIO();
		int vp_w = (int)(io.DisplaySize.x * io.DisplayFramebufferScale.x);
		int vp_h = (int)(io.DisplaySize.y * io.DisplayFramebufferScale.y);
		unsigned char* px = (unsigned char*)malloc(vp_w * vp_h * 3);
		if (px) {
			glPixelStorei(GL_PACK_ALIGNMENT, 1);
			glReadPixels(0, 0, vp_w, vp_h, GL_RGB, GL_UNSIGNED_BYTE, px);
			char png_path[2048];
			snprintf(png_path, sizeof(png_path), "%s/ui_frame.png", g_mcp_ui_capture_dir);
			unsigned char* flipped = (unsigned char*)malloc(vp_w * vp_h * 3);
			if (flipped) {
				for (int row = 0; row < vp_h; row++) {
					memcpy(flipped + row * vp_w * 3,
					       px + (vp_h - 1 - row) * vp_w * 3, vp_w * 3);
				}
				stbi_write_png(png_path, vp_w, vp_h, 3, flipped, vp_w * 3);
				free(flipped);
			}
			free(px);
			printf("[MCP] CAPTURE_UI_FRAME: %dx%d -> %s\n", vp_w, vp_h, png_path);
			fflush(stdout);
		}
		g_mcp_ui_capture_dir[0] = 0;
	}

}

static void DebugProbe()
{
	ImGuiIO& io = ImGui::GetIO();
	double rx = 0.5 * io.DisplaySize.x / font_size;
	double ry = 0.5 * io.DisplaySize.y / font_size;
	double pitch = rot_pitch * (M_PI / 180);
	double yaw = rot_yaw * (M_PI / 180);
	double z_scale = 1.0 / HEIGHT_SCALE;

	double tm[16];
	tm[0] = +cos(yaw)/rx;
	tm[1] = -sin(yaw)*sin(pitch)/ry;
	tm[2] = 0;
	tm[3] = 0;
	tm[4] = +sin(yaw)/rx;
	tm[5] = +cos(yaw)*sin(pitch)/ry;
	tm[6] = 0;
	tm[7] = 0;
	tm[8] = 0;
	tm[9] = +cos(pitch)*z_scale/ry;
	tm[10] = +2./0xffff;
	tm[11] = 0;
	tm[12] = -(pos_x * tm[0] + pos_y * tm[4] + pos_z * tm[8]);
	tm[13] = -(pos_x * tm[1] + pos_y * tm[5] + pos_z * tm[9]);
	tm[14] = -1.0;
	tm[15] = 1.0;

	double itm[16];
	Invert(tm, itm);

	double ray_p[4], ray_v[4];
	double clip_mouse[4] = {
		2.0 * io.MousePos.x / io.DisplaySize.x - 1.0,
		1.0 - 2.0 * io.MousePos.y / io.DisplaySize.y,
		-1.1, 1
	};
	Product(itm, clip_mouse, ray_p);
	clip_mouse[2] = -1.2;
	Product(itm, clip_mouse, ray_v);
	ray_v[0] -= ray_p[0];
	ray_v[1] -= ray_p[1];
	ray_v[2] -= ray_p[2];

	double hit[3];
	double nrm[3] = {0,0,1};
	uint8_t color[3] = {128,128,128};
	// Argument 8 (solid_only) is false for probe to ensure we hit even transparent-flagged meshes if needed
	printf("[DebugProbe] Casting ray from (%.2f,%.2f,%.2f) dir (%.2f,%.2f,%.2f)\n", ray_p[0], ray_p[1], ray_p[2], ray_v[0], ray_v[1], ray_v[2]);
	Inst* inst = HitWorld(world, ray_p, ray_v, hit, nrm, false, HitFilter(true, false, true), color);

	const char* name = GetInstName(inst);
	printf("[DebugProbe] Hit: %s at (%.2f, %.2f, %.2f) Normal=(%.2f, %.2f, %.2f) Color=(%d, %d, %d)\n",
		name ? name : "None",
		hit[0], hit[1], hit[2],
		nrm[0], nrm[1], nrm[2],
		color[0], color[1], color[2]);

    if (GetInstMesh(inst)) {
        printf("    MeshInst hit. Color interpolation result: R=%d G=%d B=%d\n", color[0], color[1], color[2]);
    }
}

void my_mouse(A3D_WND* wnd, int x, int y, MouseInfo mi)
{
	bool is_down = ((mi&0xF) == MouseInfo::LEFT_DN || (mi&0xF) == MouseInfo::RIGHT_DN || (mi&0xF) == MouseInfo::MIDDLE_DN);
	ImGuiIO& io = ImGui::GetIO();
	if (is_down && io.KeyCtrl && !painting) printf("[Probe] Probe Click at %d,%d (EditMode=%d)\n", x, y, edit_mode);
	#ifdef MOUSE_QUEUE

	// allow overwriting mouse moves
	if (mouse_queue_len)
	{
		MouseQueue* mq = mouse_queue + mouse_queue_len - 1;
		if (IsMouseMoveEvent(mi) && IsMouseMoveEvent(mq->mi))
		{
			mq->x = x;
			// FL-3714: coalesce true MOVE events, preserving the real Y.
			// The platform enum uses MOVE=1, so checking for low nibble 0
			// silently disabled coalescing and let drag floods stall the editor.
			mq->y = y;
			mq->mi = mi;
			return;
		}
	}

	if (mouse_queue_len==mouse_queue_size)
	{
		mouse_queue_len--;
		for (int i=0; i<mouse_queue_len; i++)
			mouse_queue[i] = mouse_queue[i+1];
	}
	mouse_queue[mouse_queue_len].x = x;
	mouse_queue[mouse_queue_len].y = y;
	mouse_queue[mouse_queue_len].mi = mi;
	mouse_queue_len++;

	#else

	if ((mi & 0xF) == MouseInfo::LEAVE)
	{
		mouse_in = 0;
		return;
	}

	ImGuiIO& io = ImGui::GetIO();

	io.MousePos = ImVec2((float)x, (float)y);

	if ((mi & 0xF) == MouseInfo::ENTER)
		mouse_in = 1;

	switch (mi & 0xF)
	{
		case MouseInfo::WHEEL_DN:
			zoom_wheel--;
			io.MouseWheel -= 1.0;
			break;
		case MouseInfo::WHEEL_UP:
			zoom_wheel++;
			io.MouseWheel += 1.0;
			break;

		default:
			if (mouse_queue_len==mouse_queue_size)
			{
				mouse_queue_len--;
				for (int i=0; i<mouse_queue_len; i++)
					mouse_queue[i] = mouse_queue[i+1];
			}
			mouse_queue[mouse_queue_len++] = mi & 0xF;
			break;

		case MouseInfo::LEFT_DN:
			io.MouseDown[0] = true;
			break;
		case MouseInfo::LEFT_UP:
			io.MouseDown[0] = false;
			break;
		case MouseInfo::RIGHT_DN:
			g_mouse_right_physical = true;
			io.MouseDown[1] = true;
			break;
		case MouseInfo::RIGHT_UP:
			g_mouse_right_physical = false;
			io.MouseDown[1] = false;
			break;
		case MouseInfo::MIDDLE_DN:
			g_mouse_middle_physical = true;
			io.MouseDown[2] = true;
			break;
		case MouseInfo::MIDDLE_UP:
			g_mouse_middle_physical = false;
			io.MouseDown[2] = false;
			break;
	}

	#endif
}

void my_resize(A3D_WND* wnd, int w, int h)
{
	ImGuiIO& io = ImGui::GetIO();
	int xywh[4];
	a3dGetRect(wnd, xywh, 0);
	int win_w = xywh[2];
	int win_h = xywh[3];
	if (win_w > 0 && win_h > 0)
	{
		io.DisplaySize = ImVec2((float)win_w, (float)win_h);
		io.DisplayFramebufferScale = ImVec2((float)w / win_w, (float)h / win_h);
	}
	else
	{
		io.DisplaySize = ImVec2((float)w, (float)h);
	}
}

// WHY initialization order matters:
// my_init() sets up the editor in strict dependency order:
// 1. World (scene graph) - must exist before assets/meshes/sprites
// 2. Mesh library scan - populates mesh list for placement
// 3. Sprite library scan - populates sprite list for placement
// 4. Material system - terrain rendering requires materials
// 5. OpenGL state - must be after world/assets for GPU upload
// 6. ImGui setup - must be last, requires OpenGL context
// Incorrect order causes segfaults (null pointers) or OpenGL errors.
void my_init(A3D_WND* wnd)
{
	// FL-3851: SDL/MCP startup on macOS can enter my_init before GL strings are
	// available; glGetString itself can crash before returning. Skip these
	// diagnostics in MCP mode because they are not part of the command protocol.
	if (!g_mcp_mode)
	{
		const GLubyte* renderer = glGetString(GL_RENDERER);
		const GLubyte* vendor = glGetString(GL_VENDOR);
		const GLubyte* version = glGetString(GL_VERSION);
		const GLubyte* shaders = glGetString(GL_SHADING_LANGUAGE_VERSION);
		printf("RENDERER: %s\n", renderer ? (const char*)renderer : "(unavailable)");
		printf("VENDOR:   %s\n", vendor ? (const char*)vendor : "(unavailable)");
		printf("VERSION:  %s\n", version ? (const char*)version : "(unavailable)");
		printf("SHADERS:  %s\n", shaders ? (const char*)shaders : "(unavailable)");
	}

	world = CreateWorld();

	// [DEPENDENCY:BLENDER] Initial mesh library scan loads all .akm files from assets/meshes/ directory at startup.
	char mesh_dirname[1024+20];
	sprintf(mesh_dirname, "%sassets/meshes", base_path);
	a3dListDir(mesh_dirname, MeshScan, mesh_dirname);
	active_mesh = GetFirstMesh(world);

	// TODO(PIPELINE-FIX): Sprite directory scan assumes all .xp files in assets/sprites/ are ready for
	// editor use. Pipeline staging workflow may change directory structure or format.
	ScanEditorSpriteDirectory(g_startup_viewer_mode);
	active_sprite = GetFirstSprite(false/*world*/);

	RebuildWorld(world);

	gl3CreateTextures(GL_TEXTURE_3D, 1, &pal_tex);
	gl3TextureStorage3D(pal_tex, 1, GL_RGBA8, 256, 256, 256); // alpha holds pal-indexes!
	gl3TextureParameteri3D(pal_tex, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
	gl3TextureParameteri3D(pal_tex, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	gl3TextureParameteri3D(pal_tex, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	gl3TextureParameteri3D(pal_tex, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	gl3TextureParameteri3D(pal_tex, GL_TEXTURE_WRAP_R, GL_CLAMP_TO_EDGE);
	Palettize(0);

	MyMaterial::Init();

	char font_dirname[1024+20];
	sprintf(font_dirname,"%sassets/fonts",base_path);
	fonts_loaded = 0;
	a3dListDir(font_dirname, MyFont::Scan, font_dirname);

	MyPalette::Init();
	char pal_dirname[1024+20];
	sprintf(pal_dirname,"%sassets/palettes",base_path);
	palettes_loaded = 0;
	a3dListDir(pal_dirname, MyPalette::Scan, pal_dirname);

	g_Time = a3dGetTime();
	render_context.Create();

	#ifndef USE_GL3
	glDebugMessageCallback(glDebugCall, 0/*cookie*/);
	#endif

	// Setup Dear ImGui context
	ImGui::CreateContext();
	ImGuiIO& io = ImGui::GetIO();

	{
		// USER_DIR
		snprintf(ini_path,4096,"./imgui.ini");
		ini_path[4095]=0;
		io.IniFilename = ini_path;
	}

	io.BackendPlatformName = "imgui_impl_a3d";

	io.KeyMap[ImGuiKey_Tab] = A3D_TAB;
	io.KeyMap[ImGuiKey_LeftArrow] = A3D_LEFT;
	io.KeyMap[ImGuiKey_RightArrow] = A3D_RIGHT;
	io.KeyMap[ImGuiKey_UpArrow] = A3D_UP;
	io.KeyMap[ImGuiKey_DownArrow] = A3D_DOWN;
	io.KeyMap[ImGuiKey_PageUp] = A3D_PAGEUP;
	io.KeyMap[ImGuiKey_PageDown] = A3D_PAGEDOWN;
	io.KeyMap[ImGuiKey_Home] = A3D_HOME;
	io.KeyMap[ImGuiKey_End] = A3D_END;
	io.KeyMap[ImGuiKey_Insert] = A3D_INSERT;
	io.KeyMap[ImGuiKey_Delete] = A3D_DELETE;
	io.KeyMap[ImGuiKey_Backspace] = A3D_BACKSPACE;
	io.KeyMap[ImGuiKey_Space] = A3D_SPACE;
	io.KeyMap[ImGuiKey_Enter] = A3D_ENTER;
	io.KeyMap[ImGuiKey_Escape] = A3D_ESCAPE;
	io.KeyMap[ImGuiKey_A] = A3D_A;
	io.KeyMap[ImGuiKey_C] = A3D_C;
	io.KeyMap[ImGuiKey_V] = A3D_V;
	io.KeyMap[ImGuiKey_X] = A3D_X;
	io.KeyMap[ImGuiKey_Y] = A3D_Y;
	io.KeyMap[ImGuiKey_Z] = A3D_Z;

	io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;  // Enable Keyboard Controls

	// Setup Dear ImGui style
	ImGui::StyleColorsDark();
	//ImGui::StyleColorsClassic();

	ImGui_ImplOpenGL3_Init("#version 330");

	ImWchar range[]={
		0x0020, 0x03FF,
		0x2000, 0x27BF,
		0x3000, 0x30FF,
		0
	};
	char ui_font_path[1024+30];
	sprintf(ui_font_path,"%sassets/fonts/Roboto-Medium.ttf",base_path);
	pFont = io.Fonts->AddFontFromFileTTF(ui_font_path, 16, NULL, range);
	ImFontConfig glyph_fallback_config;
	glyph_fallback_config.MergeMode = true;
	glyph_fallback_config.PixelSnapH = true;
	const char* glyph_fallback_font_paths[] =
	{
		"/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
		"/Library/Fonts/Arial Unicode.ttf",
	};
	for (int i = 0; i < (int)(sizeof(glyph_fallback_font_paths) / sizeof(glyph_fallback_font_paths[0])); i++)
	{
		const char* glyph_fallback_font_path = glyph_fallback_font_paths[i];
		FILE* glyph_fallback_font = fopen(glyph_fallback_font_path, "rb");
		if (glyph_fallback_font)
		{
			fclose(glyph_fallback_font);
			io.Fonts->AddFontFromFileTTF(glyph_fallback_font_path, 16, &glyph_fallback_config, range);
			break;
		}
	}
	io.Fonts->Build();

	// Create initial terrain structure
	// Materials will be set after patches are created
	// (see terrain initialization below)
	terrain = CreateTerrain();

	// ALTERNATIVE:
	// terrain = CreateTerrain(int x, int y, int w, int h, uint16_t* data);
	// xywh coords are in patches, so data is w*4+1,h*4+1 !!!!!!!!!!!!!!!!

	const int num1 = 16;
	const int num2 = num1*num1;

	uint32_t* rnd = (uint32_t*)malloc(sizeof(uint32_t)*num2);
	int n = num2;
	for (int i = 0; i < num2; i++)
		rnd[i] = i;

	for (int i = 0; i < num2; i++)
	{
		int r = (fast_rand() + fast_rand()*(FAST_RAND_MAX+1)) % n;

		uint32_t uv = rnd[r];
		rnd[r] = rnd[--n];
		uint32_t u = uv % num1;
		uint32_t v = uv / num1;
		// Height 0xA000 = above water level (water is ~0x8000)
		// This ensures terrain is visible and not underwater
		AddTerrainPatch(terrain, u, v, 0xA000);
		EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
	}

	free(rnd);

	// ========================================================================
	// INITIALIZE TERRAIN MATERIALS
	// Set playable area to grass (Material 1), surrounded by water (Material 0)
	// ========================================================================
	//
	// Layout: 16x16 patches, each patch is 4x4 visual cells
	// Water border: outer 2 patches (ring)
	// Grass playable area: inner 12x12 patches
	//
	//     0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
	//   ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐
	// 0 │WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│ Water
	// 1 │WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│ Water
	// 2 │WW│WW│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│WW│WW│
	// 3 │WW│WW│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│WW│WW│
	// ...│..│..│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│..│..│ Grass (playable)
	//13 │WW│WW│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│GG│WW│WW│
	//14 │WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│ Water
	//15 │WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│WW│ Water
	//   └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘

	const int water_border = 2;  // Number of patches for water border

	for (int py = 0; py < num1; py++)
	{
		for (int px = 0; px < num1; px++)
		{
			Patch* p = GetTerrainPatch(terrain, px, py);
			if (!p) continue;

			// Determine if this patch is in water border or grass area
			bool is_border = (px < water_border || px >= num1 - water_border ||
			                  py < water_border || py >= num1 - water_border);

			uint8_t material_id = is_border ? 0 : 1;  // 0=water, 1=grass

			// Get visual map for this patch (stores material IDs)
			uint16_t* visual = GetTerrainVisualMap(p);
			if (visual)
			{
				// Each patch has VISUAL_CELLS x VISUAL_CELLS cells (4x4 = 16 cells)
				// Set all cells in this patch to the same material
				for (int i = 0; i < VISUAL_CELLS * VISUAL_CELLS; i++)
				{
					// Visual map format: lower 8 bits = material ID
					visual[i] = (visual[i] & 0xFF00) | material_id;
				}

				// Update the visual map on GPU
				UpdateTerrainVisualMap(p);
			}
		}
	}

	printf("Terrain initialized: %d grass patches, %d water patches\n",
	       (num1 - 2*water_border) * (num1 - 2*water_border),
	       num1 * num1 - (num1 - 2*water_border) * (num1 - 2*water_border));

	pos_x = num1 * VISUAL_CELLS / 2;
	pos_y = num1 * VISUAL_CELLS / 2;
	// Camera height matches terrain height (0xA000) for proper visibility
	pos_z = 0xA000;

	char title_buf[256];
	snprintf(title_buf, sizeof(title_buf), "ASCIIID Edit [PID %d]", getpid());
	a3dSetTitle(wnd, title_buf);

	char icon_path[1024+20];
	sprintf(icon_path,"%sassets/icons/app.png",base_path);
	a3dSetIcon(wnd,icon_path);
	a3dSetVisible(wnd,true);

	//int rect[] = { 1920 * 2, 0, 1920,1080 };
	//int rect[] = { 1920, 0, 1920,1080 };
	//int rect[] = { 0, 0, 1920,1080 };
	//a3dSetRect(wnd,rect, A3D_WND_NORMAL);

	// do the perf test
	/*
	Load("./assets/a3d/fence_test4.a3d");
	float pos[3] = { pos_x,pos_y,pos_z };
	TermOpen(wnd, rot_yaw, pos);
	a3dSetVisible(wnd, false);
	*/
}

void my_keyb_char(A3D_WND* wnd, wchar_t chr)
{
	ImGuiIO& io = ImGui::GetIO();
	io.AddInputCharacter((unsigned short)chr);
}

void my_keyb_key(A3D_WND* wnd, KeyInfo ki, bool down)
{
	ki = (KeyInfo)(ki & ~A3D_AUTO_REPEAT);

	ImGuiIO& io = ImGui::GetIO();
	if (ki < IM_ARRAYSIZE(io.KeysDown))
		io.KeysDown[ki] = down;

	io.KeysDown[A3D_ENTER] = a3dGetKeyb(wnd,A3D_ENTER) || a3dGetKeyb(wnd, A3D_NUMPAD_ENTER);

	#ifdef __APPLE__ // it has only RALT
	io.KeyAlt = a3dGetKeyb(wnd, A3D_LALT) || a3dGetKeyb(wnd,A3D_RALT);
	#else
	io.KeyAlt = a3dGetKeyb(wnd, A3D_LALT);
	#endif

	io.KeyCtrl = a3dGetKeyb(wnd, A3D_LCTRL) || a3dGetKeyb(wnd, A3D_RCTRL);
	io.KeyShift = a3dGetKeyb(wnd, A3D_LSHIFT) || a3dGetKeyb(wnd, A3D_RSHIFT);
	io.KeySuper = a3dGetKeyb(wnd, A3D_LWIN) || a3dGetKeyb(wnd, A3D_RWIN);

	if (down && ki == A3D_M && !io.WantTextInput && !io.KeyCtrl && !io.KeyAlt && !io.KeySuper)
	{
		g_editor_minimap_visible = !g_editor_minimap_visible;
		printf("[EDITOR] Minimap markers: %s\n", g_editor_minimap_visible ? "ON" : "OFF");
		fflush(stdout);
		return;
	}

    // PATCH: Arrow Key Nudge (mode 2 only, respect ImGui keyboard focus)
    if (down && selected_inst && edit_mode == 2 && !io.WantCaptureKeyboard
        && (ki == A3D_LEFT || ki == A3D_RIGHT || ki == A3D_UP || ki == A3D_DOWN))
    {
        double tm[16];
        if (GetInstTM(selected_inst, tm))
        {
            bool changed = false;
            float step = io.KeyShift ? 0.1f : 1.0f;

            if (ki == A3D_LEFT) { tm[12] -= step; changed = true; }
            if (ki == A3D_RIGHT) { tm[12] += step; changed = true; }
            if (ki == A3D_UP) {
                if (io.KeyCtrl) tm[14] += step; // Z up
                else tm[13] += step; // Y up
                changed = true;
            }
            if (ki == A3D_DOWN) {
                if (io.KeyCtrl) tm[14] -= step; // Z down
                else tm[13] -= step; // Y down
                changed = true;
            }

            if (changed) {
                DetachInst(world, selected_inst);
                SetInstTM(selected_inst, tm);
                AttachInst(world, selected_inst);
                printf("[Editor] Nudge inst %p to %.2f %.2f %.2f\n", selected_inst, tm[12], tm[13], tm[14]);
            }
        }
    }

	// F5: Reload all sprites from disk
	// [FLOW:PIPELINE] Manual asset refresh for iterative development
	if (ki == A3D_F5 && down)
	{
		reload_sprites_requested = true;
	}
}

void my_keyb_focus(A3D_WND* wnd, bool set)
{
	// TODO:
	// clear all modifiers, drags etc...
}

void my_close(A3D_WND* wnd)
{
#ifndef _WIN32
	CdpShutdown();
#endif
	TermCloseAll();

	if (pal_tex)
		glDeleteTextures(1, &pal_tex);
	pal_tex = 0;

	// free mesh prefs !!!
	Mesh* m = GetFirstMesh(world);
	while (m)
	{
		MeshPrefs* mp = (MeshPrefs*)GetMeshCookie(m);
		free(mp);
		m = GetNextMesh(m);
	}

	URDO_Purge();

	DeleteWorld(world);

	DeleteTerrain(terrain);
	DeleteTerrainPreviewScene();

	FreeEnemyGens();
	FreeMarkers();

	PurgeItemInstCache();

	MyFont::Free();
	MyMaterial::Free();

	if (gather)
	{
		if (gather->tmp_x)
			free(gather->tmp_x);
		if (gather->tmp_y)
			free(gather->tmp_y);
		free(gather);
	}

	if (ipal)
	{
		free(ipal);
		ipal = 0;
	}

	ImGui_ImplOpenGL3_Shutdown();
	ImGui::DestroyContext();

	render_context.Delete();

	a3dClose(wnd);
}

extern "C" void DumpLeakCounter();



/**
 * main - Entry point for Asciicker Map Editor
 *
 * Initializes the editor, sets up the graphics context, loads resources,
 * and enters the main rendering loop.
 *
 * @param argc Number of command-line arguments
 * @param argv Command-line arguments (argv[0] is executable path)
 * @return Exit code (0 for success)
 */
void DeleteAllEnemyGens()
{
	while(enemygen_head)
		DeleteEnemyGen(enemygen_head);
}

// ------------------------------------------------------------------------------------------------
// TESTING FRAMEWORK
// ------------------------------------------------------------------------------------------------

// Forward declarations of internal functions we need to access
static std::vector<BakeInstCoverage> BakeMeshesToTerrain(bool bake_height, bool bake_material, bool bake_vertex_colors, bool overwrite_height,
	bool overwrite_material, bool solid_only, double ray_top, uint8_t material_id);

extern "C" void RunTestScript(const char* script_path) {
    FILE* f = fopen(script_path, "r");
    if (!f) {
        printf("[Test] Error opening script: %s\n", script_path);
        exit(1);
    }

    printf("[Test] Running script: %s\n", script_path);
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        char* nl = strchr(line, '\n');
        if (nl) *nl = 0;
        if (line[0] == '#' || line[0] == 0) continue;

        char cmd[256];
        if (sscanf(line, "%s", cmd) != 1) continue;

        if (strcmp(cmd, "SET_TERRAIN_HEIGHT") == 0) {
            int h = 0;
            sscanf(line, "%*s %d", &h);
            printf("[Test] Setting terrain height to %d\n", h);
            if (!terrain) { printf("Error: No terrain\n"); continue; }

            // Ensure we have at least one patch at 0,0
            if (!GetTerrainPatch(terrain, 0, 0))
            {
                AddTerrainPatch(terrain, 0, 0, h);
                EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
            }

            int patch_count = 0;
            Patch** patches = 0;
            GetAllTerrainPatches(terrain, &patches, &patch_count);

            for(int i=0; i<patch_count; i++) {
                uint16_t* map = GetTerrainHeightMap(patches[i]);
                for(int j=0; j<(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1); j++) map[j] = (uint16_t)h;
                UpdateTerrainHeightMap(patches[i]);
            }
            if(patches) free(patches);
        }
        else if (strcmp(cmd, "PLACE_MESH") == 0) {
            char mesh_file[512];
            float x, y, z;
            if (sscanf(line, "%*s %511s %f %f %f", mesh_file, &x, &y, &z) == 4) {
                 printf("[Test] Placing mesh %s at %.1f %.1f %.1f\n", mesh_file, x, y, z);

                 Mesh* m = LoadMesh(world, mesh_file);
                 if (!m) {
                     printf("[Test] Error loading mesh %s\n", mesh_file);
                     continue;
                 }

                 // Construct 4x4 matrix
                 // Match editor mesh placement: scale Z by HEIGHT_SCALE.
                 double tm[16] = {
                     1, 0, 0, 0,
                     0, 1, 0, 0,
                     0, 0, (double)HEIGHT_SCALE, 0,
                     (double)x, (double)y, (double)z, 1
                 };

                 int flags = INST_USE_TREE | INST_VISIBLE;
                 Inst* inst = CreateInst(m, flags, tm, "TestMesh", 0);
                 (void)inst;
                 RebuildWorld(world);
            }
        }
        else if (strcmp(cmd, "BAKE_MESH_TO_TERRAIN") == 0) {
            printf("[Test] Baking meshes to terrain...\n");
            BakeMeshesToTerrain(true, true, true, true, true, false, 70000.0, 0);
        }
        else if (strcmp(cmd, "EXPORT_TERRAIN_DATA") == 0) {
            char out_file[512];
            sscanf(line, "%*s %s", out_file);
            printf("[Test] Exporting terrain data to %s\n", out_file);

            FILE* fout = fopen(out_file, "wb");
            if (fout) {
                 int patch_count = 0;
                 Patch** patches = 0;
                 GetAllTerrainPatches(terrain, &patches, &patch_count);
                 // Sort patches? tests usually use 0,0 only
                 for(int i=0; i<patch_count; i++) {
                     uint16_t* visuals = GetTerrainVisualMap(patches[i]);
                     fwrite(visuals, sizeof(uint16_t), VISUAL_CELLS*VISUAL_CELLS, fout);
                 }
                 if(patches) free(patches);
                 fclose(fout);
            }
        }
        else if (strcmp(cmd, "EXPORT_HEIGHT_SAMPLES") == 0) {
            char out_file[512];
            sscanf(line, "%*s %s", out_file);
            printf("[Test] Exporting height samples to %s\n", out_file);

             FILE* fout = fopen(out_file, "w");
            if (fout) {
                 int patch_count = 0;
                 Patch** patches = 0;
                 GetAllTerrainPatches(terrain, &patches, &patch_count);
                 for(int i=0; i<patch_count; i++) {
                     uint16_t* heights = GetTerrainHeightMap(patches[i]);
                     for(int j=0; j<(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1); j++) {
                         fprintf(fout, "%d,", heights[j]);
                     }
                 }
                 if(patches) free(patches);
                 fclose(fout);
            }
        }
    }
    fclose(f);
    printf("[Test] Script execution complete.\n");
    exit(0);
}

struct MeshBakingTest {
    static void TestQuantization() {
        printf("[UnitTest] TestQuantization...\n");
        float test_heights[] = {0.0, 7.99, 8.0, 8.01, 15.99, 16.0, 16.01, 23.99, 24.0};
        for (float h : test_heights) {
            int quantized = (int)(round(h / 16.0) * 16.0);
            printf("  Height %.2f -> %d\n", h, quantized);
        }
    }

    static void RunAllTests() {
        printf("=== MESH BAKING DEBUG TESTS ===\n");
        TestQuantization();
        printf("=== END TESTS ===\n");
    }
};

extern "C" void CMD_TestMeshBaking(const char* args) {
    MeshBakingTest::RunAllTests();
}

int main(int argc, char *argv[])
{
	char abs_buf[PATH_MAX];
	char* abs_path = 0;
	bool fl4131_material_proof_cli = false;

    // Determine base path from executable location
    // This is where we'll look for assets (assets/, etc.)
    if (argc < 1)
        strcpy(base_path,"./");
    else
    {
        size_t len = 2;
		strcpy(abs_buf, "./");
		abs_path = abs_buf;
		#if defined(__linux__) || defined(__APPLE__)
        abs_path = realpath(argv[0], abs_buf);
        char* last_slash = strrchr(abs_path, '/');
        if (last_slash)
			len = last_slash - abs_path + 1;
        #else
        len = GetFullPathNameA(argv[0],1024,abs_buf,&abs_path);
		if (!len)
			len = 2;
		if (abs_path)
			len = abs_path - abs_buf;
		abs_path = abs_buf;
		#endif

		memcpy(base_path, abs_path, len);
		base_path[len] = 0;

		if (len > 4)
		{
			char* dotrun[4] =
			{
				strstr(base_path, "/.run/"),
#ifdef _WIN32
				strstr(base_path, "\\.run\\"),
				strstr(base_path, "\\.run/"),
				strstr(base_path, "/.run\\"),
#else
				0,0,0
#endif
			};

			int dotpos = -1;
			for (int i = 0; i < 4; i++)
			{
				if (dotrun[i])
				{
					int pos = (int)(dotrun[i] - base_path);
					if (dotpos < 0 || pos < dotpos)
						dotpos = pos;
				}
			}

			if (dotpos >= 0)
				base_path[dotpos+1] = 0;
		}
    }

    // Early --help check (before LoadSprites so it exits fast)
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
	            printf(
	                "Usage: asciiid [OPTIONS] [MAP_FILE]\n"
	                "\n"
	                "Options:\n"
	                "  --map FILE       Load FILE after startup.\n"
	                "  --sprite FILE    Activate FILE in sprite mode after startup.\n"
	                "  --viewer         Use lean launcher/viewer bootstrap.\n"
	                "  --sprite-browser Start directly in the native sprite browser UI.\n"
	                "\n"
		                "  --batch          Batch/headless mode (recommended for scripting).\n"
		                "                   Reads MCP commands from stdin, one per line.\n"
		                "                   Processes each synchronously; exits on EOF.\n"
		                "  --fl4131-material-proof\n"
		                "                   Run the FL-4131 material GlyphId roundtrip proof without SDL/OpenGL.\n"
	                "                   All [MCP]-prefixed responses on stdout.\n"
                "                   Example:\n"
                "                     printf 'LOAD_MAP map.a3d\\nPROBE_TERRAIN 100 100\\n' \\\n"
                "                       | asciiid --batch 2>/dev/null | grep '^\\[MCP\\]'\n"
                "\n"
                "  --mcp            Interactive MCP mode (for persistent daemon).\n"
                "                   Keeps process alive; reads commands each render frame.\n"
                "                   Used by asciiid_daemon.py for long-lived sessions.\n"
                "\n"
                "  --cdp PORT       CDP-style TCP server on localhost:PORT.\n"
                "                   JSON request/response over TCP (no pipes needed).\n"
                "                   Send: {\"id\":1,\"method\":\"LOAD_MAP\",\"params\":\"map.a3d\"}\\n\n"
                "                   Recv: {\"id\":1,\"result\":\"...\"}\\n\n"
                "                   Used by capture_proof.py for reliable scripting.\n"
                "\n"
                "  --test-script F  Run legacy test script F and exit.\n"
                "\n"
                "MCP commands (available in --batch and --mcp modes):\n"
                "  ECHO <msg>                 Echo msg back (test)\n"
                "  LOAD_MAP <path>            Load A3D file\n"
                "  SAVE / SAVE_MAP <path>     Save current map to A3D\n"
                "  PROBE_TERRAIN <x> <y>      Height+mat_id at world coord\n"
                "  QUERY_TERRAIN_GRID <cx> <cy> <w> <h> <scale>\n"
                "  QUERY_TERRAIN_HEIGHT <x> <y>   Bilinear height at world pos (use as z for PLACE_MESH)\n"
                "                             Grid of height,mat_id cells\n"
                "  QUERY_WATER_LEVEL          Returns game water level (55)\n"
                "  LIST_INSTANCES             Dump all mesh instances\n"
                "  LIST_MESHES                Dump loaded mesh names\n"
                "  PLACE_MESH <f> <x> <y> <z> <scale>  Place mesh instance\n"
                "  BAKE_MESH_TO_TERRAIN [h m vc oh om solid ray_top mat]\n"
                "                             Bake visible meshes into terrain\n"
                "  DELETE_ALL_MESHES          Delete all mesh instances\n"
                "  RENDER                     Capture viewport as ANSI text\n"
                "  SET_CAMERA <x> <y> <z> <yaw> <pitch>\n"
                "  GET_CAMERA\n"
                "  SET_TOPDOWN_VIEW FULL|BBOX x0 y0 x1 y1  Top-down camera\n"
                "  SET_TERRAIN_OVERVIEW 0|1 Disable/enable overview LOD\n"
                "  CAPTURE_FRAME <path.ppm>   Capture frame as PPM (after UI)\n"
                "  CAPTURE_CLEAN_FRAME <dir>  Capture clean frame PNG + camera.json\n"
                "  CAPTURE_CLEAN_FRAME_AND_QUIT <dir>  Capture clean frame then exit\n"
                "  CAPTURE_UI_FRAME <dir>     Capture full composited frame (game + ImGui panels) as PNG\n"
                "  OPEN_TERMPP                Open a child TERM++ window from the loaded map\n"
                "  DUMP_MATERIAL_TABLE [ids]  Dump material palette as JSON\n"
                "  RUN_MOUSE_DRAG_PROBE x0 y0 x1 y1 steps  Queue drag flood through editor mouse path\n"
                "  RUN_SDL_MOUSE_DRAG_PROBE x0 y0 x1 y1 steps  Queue drag flood through SDL_PollEvent path\n"
                "  QUIT                       Exit process\n"
                "\n"
                "Python batch API (no daemon required):\n"
                "  from cli_anything.asciiid.utils.asciiid_backend import run_batch\n"
                "  r = run_batch([\"LOAD_MAP map.a3d\", \"PROBE_TERRAIN 100 100\"])\n"
                "  print(r['mcp'])   # list of [MCP]-prefixed lines, stripped\n"
            );
            return 0;
        }
    }

#ifdef _WIN32
	//_CrtSetBreakAlloc(11952);
#endif

	// Check for test script
	const char* test_script = 0;
	    for (int i=1; i<argc; i++) {
	        if (strcmp(argv[i], "--test-script") == 0 && i+1 < argc) {
	            test_script = argv[++i];
	            continue;
	        }
	        if (strcmp(argv[i], "--mcp") == 0) {
	            g_mcp_mode = true;
	            // Disable C stdio buffering on stdin so that select() on STDIN_FILENO
	            // always reflects the actual pipe state. Without this, fgets() pre-reads
	            // a 4KB block from the OS pipe into the stdio buffer; subsequent select()
	            // calls return 0 (OS pipe empty) even though unread data sits in the C
	            // stdio buffer, causing IsStdinReady() to permanently miss commands 2+.
	            setvbuf(stdin, NULL, _IONBF, 0);
	            printf("[MCP] Mode enabled\n");
	            fflush(stdout);
	            continue;
	        }
		        if (strcmp(argv[i], "--batch") == 0 || strcmp(argv[i], "--headless-batch") == 0) {
		            g_batch_mode = true;
		            setvbuf(stdin,  NULL, _IONBF, 0);
		            setvbuf(stdout, NULL, _IONBF, 0);
		            continue;
		        }
		        if (strcmp(argv[i], "--fl4131-material-proof") == 0) {
		            fl4131_material_proof_cli = true;
		            g_batch_mode = true;
		            setvbuf(stdout, NULL, _IONBF, 0);
		            continue;
		        }
#ifndef _WIN32
	        if (strcmp(argv[i], "--cdp") == 0 && i+1 < argc) {
	            int port = atoi(argv[++i]);
	            if (port > 0 && port < 65536) {
	                CdpInit(port);
	                // CDP implies MCP mode (commands are processed in render loop)
	                g_mcp_mode = true;
	                setvbuf(stdin, NULL, _IONBF, 0);
	            } else {
	                printf("[CDP] Error: invalid port %d\n", port);
	            }
	            continue;
	        }
#endif
	        if (strcmp(argv[i], "--map") == 0 && i+1 < argc) {
	            CopyClamped(g_startup_map_path, sizeof(g_startup_map_path), argv[++i]);
	            continue;
	        }
	        if (strcmp(argv[i], "--sprite") == 0 && i+1 < argc) {
	            CopyClamped(g_startup_sprite_path, sizeof(g_startup_sprite_path), argv[++i]);
	            continue;
	        }
	        if (strcmp(argv[i], "--viewer") == 0) {
	            g_startup_viewer_mode = true;
	            continue;
	        }
	        if (strcmp(argv[i], "--sprite-browser") == 0) {
	            g_startup_sprite_browser = true;
	            continue;
	        }
	        if (argv[i][0] != '-') {
	            CopyClamped(g_startup_map_path, sizeof(g_startup_map_path), argv[i]);
	        }
	    }

	bool quiet_viewer_terminal = g_startup_viewer_mode && !g_batch_mode && !g_mcp_mode;
	if (quiet_viewer_terminal)
	{
		SilenceViewerTerminalLogs();
	}
	else
	{
		printf("exec path: %s\n", argv[0]);
		printf("BASE PATH: %s\n", base_path);
	}

	if (g_batch_mode)
	{
		// Batch loads terrain without any interactive render-time texheap need.
		// Keep the terrain GL upload path disabled before startup/load work so
		// headless scripting uses one explicit owner for this mode.
		EnableHeadlessBatchEnv();
	}

	if (fl4131_material_proof_cli)
	{
		g_fl4131_headless_material_proof = true;
		memset(mat, 0, sizeof(mat));
		bool ok = RunFl4131AsciiidExtendedMaterialProof();
		g_fl4131_headless_material_proof = false;
		return ok ? 0 : 1;
	}

	if (!g_startup_viewer_mode)
	{
		LoadSprites();
		LoadEnemygenSpriteForEditor();
	}

	PlatformInterface pi;
	pi.close = my_close;
	pi.render = my_render;
	pi.resize = my_resize;
	pi.init = my_init;
	pi.keyb_char = my_keyb_char;
	pi.keyb_key = my_keyb_key;
	pi.keyb_focus = my_keyb_focus;
	pi.mouse = my_mouse;

	// pi.ptydata = my_ptydata;

	GraphicsDesc gd;
	gd.color_bits = 32;
	gd.alpha_bits = 8;
	gd.depth_bits = 24;
	gd.stencil_bits = 8;
	#ifdef USE_GL3
	gd.version[0]=3;
	gd.version[1]=3;
	#else
	gd.version[0] = 4;
	gd.version[1] = 5;
	#endif
	gd.flags = (GraphicsDesc::FLAGS) (GraphicsDesc::DEBUG_CONTEXT | GraphicsDesc::DOUBLE_BUFFER);

	int rc[] = {0,0,1920*2,1080+2*1080};
	gd.wnd_mode = A3D_WND_NORMAL;
	gd.wnd_xywh = 0;

	// FL-4131: allow visual-proof drivers to launch the editor at a custom
	// initial window size so off-viewport ImGui surfaces (e.g. the Extended
	// Glyph Palette swatch grid which renders at y~602+) are inside the
	// captured viewport. No protocol surface; env-only.
	static int g_initial_xywh[4] = {100, 100, 800, 600};
	const char* env_w = getenv("ASCIICKER_INITIAL_WINDOW_WIDTH");
	const char* env_h = getenv("ASCIICKER_INITIAL_WINDOW_HEIGHT");
	if ((env_w && *env_w) || (env_h && *env_h))
	{
		if (env_w && *env_w) g_initial_xywh[2] = atoi(env_w);
		if (env_h && *env_h) g_initial_xywh[3] = atoi(env_h);
		if (g_initial_xywh[2] < 320) g_initial_xywh[2] = 320;
		if (g_initial_xywh[3] < 240) g_initial_xywh[3] = 240;
		gd.wnd_xywh = g_initial_xywh;
		printf("[EDITOR] [FL-4131] initial window size override: %dx%d\n",
			g_initial_xywh[2], g_initial_xywh[3]);
		fflush(stdout);
	}

		if (!a3dOpen(&pi, &gd, 0))
		{
			fprintf(stderr, "[ASCIIID] ERROR: failed to open SDL/OpenGL window\n");
			return 1;
		}

	    if (g_startup_map_path[0]) {
	        if (!LoadMapForSession(g_startup_map_path, "[EDITOR]")) {
	            return 1;
	        }
	    }
	    if (g_startup_sprite_browser) {
	        edit_mode = 4;
	    }
	    if (g_startup_sprite_path[0]) {
	        if (!ActivateSpriteTarget(g_startup_sprite_path)) {
	            printf("[EDITOR] Failed to activate startup sprite: %s\n", g_startup_sprite_path);
	        }
	    }

	    if (test_script) {
        // Run test script - requires world to be initialized (happens in my_init called by a3dOpen)
        RunTestScript(test_script);
    }

    if (g_batch_mode) {
        // Batch mode: process all stdin commands synchronously, no render loop.
        // Usage: printf "LOAD_MAP foo.a3d\nQUERY_TERRAIN_GRID 0 0 256 256\n" | asciiid --batch
        // Output: [MCP]-prefixed lines to stdout; caller parses them.
        // This is simpler and more reliable than the daemon for pipeline use.
        // NOTE: pass the raw fgets line (with \n) to ProcessMCPCommand — it expects
        // the \n for correct output formatting in [MCP] Received command: prints.
        char line[1024];
        while (fgets(line, sizeof(line), stdin)) {
            // Skip blank/comment lines (check first char before the '\n')
            if (line[0] == '\n' || line[0] == '\0' || line[0] == '#') continue;
            ProcessMCPCommand(line);
            fflush(stdout);
            // [FL-3851] If a clean capture was requested, render frames until it completes.
            extern char g_mcp_clean_capture_dir[1024];
            if (g_mcp_clean_capture_dir[0]) {
                // Batch command processing is outside the platform render loop.
                // Enter the real loop so GL/ImGui frame setup is valid, then
                // exit from the clean capture point after files are written.
                g_batch_exit_after_clean_capture = true;
                a3dLoop();
                exit(0);
            }
        }
        exit(0);
    }

	a3dLoop();

	Sprite* s = GetFirstSprite(false);
	while (s)
	{
		void* sp = GetSpriteCookie(s);
		SetSpriteCookie(s,0);
		if (sp)
			free(sp);
		s = s->next;
	}

	FreeSprites();

	DumpLeakCounter();

#ifdef _WIN32
	_CrtDumpMemoryLeaks();
#endif


	return 0;
}

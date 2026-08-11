// game_app.cpp - Native Desktop Entry Point
//
// PURPOSE: Native desktop platform implementation (Windows, macOS, Linux) with V8 JavaScript
// engine for NPC scripting. Initializes platform backends (SDL/X11/Win32), V8 isolate, OpenGL
// rendering context, and runs main game loop with native filesystem and input.
//
// PLATFORM-SPECIFIC FEATURES:
// - V8 JavaScript Engine: Full V8 isolate for NPC scripting (NOT available on web - web uses browser JS)
// - Native Filesystem: Direct OS filesystem access (vs web's virtual IndexedDB filesystem)
// - Native OpenGL: Direct GPU access (vs web's WebGL context)
// - Native Input: Keyboard, mouse, gamepad via SDL/X11/Win32 APIs
// - Native Main Loop: Preemptive while(1) game loop (vs web's cooperative emscripten_set_main_loop)
//
// INITIALIZATION ORDER:
// 1. Parse command-line arguments (--server, --fullscreen, --width, --height, etc.)
// 2. Initialize V8 JavaScript engine (platform, isolate, global context)
// 3. Initialize platform backends (SDL for cross-platform, or X11/Win32 for native)
// 4. Load world data from filesystem (.a3d world file, .xp sprites, .akm meshes)
// 5. Initialize rendering (OpenGL context, shader compilation, texture upload)
// 6. Initialize physics (collision detection, character controller)
// 7. Run main game loop: input → physics → render → present (60 FPS target)
//
// MAIN LOOP PATTERN:
// - while(1) { process_input(); update_physics(); render_frame(); swap_buffers(); }
// - WHY PREEMPTIVE: Native platforms support blocking loops, can yield to OS scheduler
// - CONTRAST WEB: Browser requires cooperative scheduling (emscripten_set_main_loop with requestAnimationFrame)
// - CONTRAST SERVER: Server uses tick-based loop with network message processing
//
// V8 JAVASCRIPT ENGINE SETUP:
// - V8 Isolate: Isolated JavaScript VM instance (heap, garbage collector, context)
// - Global Template: Exposes akPrint(), akAPI_Call() to JavaScript
// - Shared ArrayBuffer: akAPI_Buff mapped to V8 heap for zero-copy C++ <-> JS data exchange
// - NPC Scripts: JavaScript files loaded from filesystem, executed per-character
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
// - main(): Command-line parsing, V8 init, platform init, game loop
// - init_v8() / free_v8(): V8 JavaScript engine lifecycle
// - GetTime(): High-resolution monotonic timestamp (platform-specific clock)
// - SyncConf(): Write config to filesystem (native vs web's FS.syncfs)
// - Buzz(): Haptic feedback via SDL gamepad rumble
// - GetConfPath(): Config file path (native filesystem vs web virtual filesystem)
//
// KEY FILES:
// - game.cpp: Shared game logic (world update, physics, rendering) - platform-independent
// - game_api.cpp: JavaScript <-> C++ bridge (used by V8 on native, browser JS on web)
// - render.cpp: OpenGL rendering backend (shader management, draw calls)
// - physics.cpp: Collision detection and character controller
// - world.cpp: BSP tree world data structure
// - game_web.cpp: WebAssembly/Emscripten platform entry point (cross-reference for differences)
// - game_svr.cpp: Headless server entry point (cross-reference for differences)
//
// CROSS-REFERENCES:
// - See game_web.cpp header for Emscripten/WebAssembly platform differences
// - See game_svr.cpp header for headless server platform differences
// - See game_api.cpp header for JavaScript bridge architecture

#include <stdio.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>
#include <stdarg.h>

#if defined(__linux__) || defined(__APPLE__)
#include <sys/ioctl.h>
#include <sys/poll.h>
#include <sys/wait.h>
#include <fcntl.h>
#ifdef __linux__
# include <linux/limits.h>
#include <linux/input.h>
#include <linux/joystick.h>
#else
# include <limits.h>
#endif
#include <unistd.h>
#include <signal.h>
#include <termios.h>
#include <time.h>
#ifdef USE_GPM
# include <gpm.h>
#endif

// work around including <netinet/tcp.h>
// which also defines TCP_CLOSE
#ifndef TCP_DELAY
#define TCP_NODELAY 1
#endif

#else
#define PATH_MAX 1024
#endif

#include <assert.h>

#include "platform/filesystem_backend.h"
#include "platform/time_backend.h"
#include "platform/gamepad_backend.h"
#include "platform/backend/host_terminal_ansi.h"
#include "platform/backend/host_input.h"

#include "render.h"
#include "physics.h"
#include "sprite.h"
#include "material_glyph_plane.h"
#include "material_sidecar.h"
#include "matrix.h"
#include "actor_visual_profile_table.generated.h"

#include "network.h"

#include "game_api.h"
#include "engine/input_state.h"


#ifdef _WIN32
#pragma comment(lib,"v8_monolith.lib")
#pragma comment(lib,"Dbghelp.lib")
#pragma comment(lib,"Winmm.lib")
#endif

#ifdef _WIN32
 #ifdef _WIN64
 #define V8_COMPRESS_POINTERS 1
 #define V8_ENABLE_SANDBOX 1
 #endif
#endif

#ifdef __APPLE__
 #define V8_COMPRESS_POINTERS 1
 //#define V8_ENABLE_SANDBOX 1
#endif

#ifdef __linux__
 // i'am lazy gumix
 // only x64 linux is supported
 #define V8_COMPRESS_POINTERS 1
 #define V8_ENABLE_SANDBOX 1
#endif

#include <libplatform/libplatform.h>
#include <v8.h>

// FOR GL 
#include "term.h"
#include "gl.h"
#include "gl45_emu.h"
#include "rgba8.h"

#include "game.h"
#include "game_utility.h"
#include "sprite_registry.h"
#include "enemygen.h"


// FOR AUDIO
#include "audio.h"
#include "fast_rand.h"

int tty = -1;

// configurable, or auto lookup?

// Trigger haptic feedback (gamepad rumble).
// WHY PLATFORM-SPECIFIC: Native platforms use SDL gamepad API, web uses Vibration API,
// server has no haptic output.
void Buzz()
{
}

char base_path[1024] = "./";

static void SetJoinV2ContractError(char* error_buf, size_t error_cap, const char* fmt, ...)
{
	if (!error_buf || error_cap == 0)
		return;
	error_buf[0] = 0;
	if (!fmt)
		return;
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(error_buf, error_cap, fmt, ap);
	va_end(ap);
	error_buf[error_cap - 1] = 0;
}

static bool ExtractJsonStringValue(const char* json, const char* key, char* out, size_t out_cap)
{
	if (!json || !key || !out || out_cap == 0)
		return false;
	out[0] = 0;
	char needle[128];
	int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
	if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
		return false;
	const char* pos = strstr(json, needle);
	if (!pos)
		return false;
	pos += needle_len;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != ':')
		return false;
	pos++;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != '"')
		return false;
	pos++;
	const char* end = strchr(pos, '"');
	if (!end)
		return false;
	size_t len = (size_t)(end - pos);
	if (len + 1 > out_cap)
		return false;
	memcpy(out, pos, len);
	out[len] = 0;
	return true;
}

static bool ExtractJsonUIntValue(const char* json, const char* key, uint32_t* out)
{
	if (!json || !key || !out)
		return false;
	char needle[128];
	int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
	if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
		return false;
	const char* pos = strstr(json, needle);
	if (!pos)
		return false;
	pos += needle_len;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != ':')
		return false;
	pos++;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos < '0' || *pos > '9')
		return false;
	char* end_ptr = 0;
	unsigned long value = strtoul(pos, &end_ptr, 10);
	if (end_ptr == pos)
		return false;
	*out = (uint32_t)value;
	return true;
}

static bool JoinV2ReportStringMatches(
	const char* json,
	const char* key,
	const char* expected,
	const char* path,
	char* error_buf,
	size_t error_cap)
{
	char actual[256] = {};
	if (!ExtractJsonStringValue(json, key, actual, sizeof(actual)))
	{
		SetJoinV2ContractError(error_buf, error_cap,
			"missing or malformed %s in %s", key, path);
		return false;
	}
	if (!expected || !expected[0] || strcmp(actual, expected) != 0)
	{
		SetJoinV2ContractError(error_buf, error_cap,
			"%s mismatch in %s", key, path);
		return false;
	}
	return true;
}

static bool JoinV2ReportUIntMatches(
	const char* json,
	const char* key,
	uint32_t expected,
	const char* path,
	char* error_buf,
	size_t error_cap)
{
	uint32_t actual = 0;
	if (!ExtractJsonUIntValue(json, key, &actual))
	{
		SetJoinV2ContractError(error_buf, error_cap,
			"missing or malformed %s in %s", key, path);
		return false;
	}
	if (actual != expected)
	{
		SetJoinV2ContractError(error_buf, error_cap,
			"%s mismatch in %s", key, path);
		return false;
	}
	return true;
}

static bool ReadJoinV2Contract(STRUCT_REQ_JOIN_V2* out, const char* user, char* error_buf, size_t error_cap)
{
	if (!out || !user)
	{
		SetJoinV2ContractError(error_buf, error_cap, "invalid join-v2 contract arguments");
		return false;
	}
	memset(out, 0, sizeof(*out));
	out->token = 'G';
	uint32_t contract_version = kCompiledActorVisualTableHeader.compiled_schema_version;
	if (contract_version == 0 || contract_version > 0xFFFFu)
		return false; // FL-971: reject version values that would silently truncate on uint16_t cast
	out->appearance_contract_version = (uint16_t)contract_version;
	strncpy(out->bundle_hash, ACTOR_VISUAL_PROFILE_COMPILED_TABLE_SHA256, sizeof(out->bundle_hash) - 1);
	strncpy(out->ids_lock_hash, ACTOR_VISUAL_PROFILE_IDS_SHA256, sizeof(out->ids_lock_hash) - 1);
	// FL-4131 Phase 7 — claim the client's local glyph manifest identity.
	// nullptr or empty-string constants mean "CP437-only build"; authored
	// extended glyph content sends both manifest hash and content_pack_id.
	if (ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 && ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256[0])
		strncpy(out->glyph_manifest_hash, ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256, sizeof(out->glyph_manifest_hash) - 1);
	if (ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID && ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID[0])
		strncpy(out->content_pack_id, ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID, sizeof(out->content_pack_id) - 1);
	strncpy(out->name, user, 30);
	out->name[30] = 0;
	// FL-4131 P10 — claim the client's atlas runtime identity (lut + page-chain).
	// Empty only for CP437-only builds; non-empty mismatch against the server's
	// deployed atlas rejects the join with a visible reason.
	if (ACTOR_VISUAL_PROFILE_LUT_SHA256 && ACTOR_VISUAL_PROFILE_LUT_SHA256[0])
		strncpy(out->lut_hash, ACTOR_VISUAL_PROFILE_LUT_SHA256, sizeof(out->lut_hash) - 1);
	if (ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256 && ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256[0])
		strncpy(out->page_atlas_chain_hash, ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256, sizeof(out->page_atlas_chain_hash) - 1);
	return true;
}

// Synchronize configuration to persistent storage.
// WHY PLATFORM-SPECIFIC: Native platforms write directly to filesystem (synchronous),
// web platforms use FS.syncfs() to flush IndexedDB (asynchronous), server may skip.
extern "C" void SyncConf()
{
}

char conf_path[1024+20]="";
// Get platform-specific path to configuration file.
// WHY PLATFORM-SPECIFIC: Native uses user home directory or SNAP_USER_DATA,
// web uses /data/ mount in virtual filesystem, server uses current directory.
extern "C" const char* GetConfPath()
{
	if (conf_path[0] == 0)
	{
		#if defined(__linux__) || defined(__APPLE__)
        const char* user_dir = getenv("SNAP_USER_DATA");
        if (!user_dir || user_dir[0]==0)
        {
            user_dir = getenv("HOME");
            if (!user_dir || user_dir[0]==0)
                sprintf(conf_path,"%sasciicker.cfg",base_path);
            else
                sprintf(conf_path,"%s/asciicker.cfg",user_dir);
        }
        else
            sprintf(conf_path,"%s/asciicker.cfg",user_dir);

 		#elif defined(_WIN32)
		
		const char* user_dir = getenv("APPDATA");
		if (!user_dir || user_dir[0] == 0)
			sprintf(conf_path, "%sasciicker.cfg", base_path);
		else
			sprintf(conf_path, "%s\\asciicker.cfg", user_dir);
		
		#endif
	}

	return conf_path;
}

#if defined(__linux__) || defined(__APPLE__)
/*
https://superuser.com/questions/1185824/configure-vga-colors-linux-ubuntu
https://int10h.org/oldschool-pc-fonts/fontlist/
https://www.zap.org.au/software/fonts/console-fonts-distributed/psftx-freebsd-11.1/index.html
ftp://ftp.zap.org.au/pub/fonts/console-fonts-zap/console-fonts-zap-2.2.tar.xz
*/

template <uint16_t C> static int UTF8(char* buf)
{
    if (C<0x0080)
    {
        buf[0]=C&0xFF;
        return 1;            
    }

    if (C<0x0800)
    {
        buf[0] = (char)(0xC0 | ( ( C >> 6 ) & 0x1F ));
        buf[1] = (char)(0x80 | ( C & 0x3F ));
        return 2;
    }

    buf[0] = (char)(0xE0 | ( ( C >> 12 ) & 0x0F ));
    buf[1] = (char)(0x80 | ( ( C >> 6 ) & 0x3F ));
    buf[2] = (char)(0x80 | ( C & 0x3F ));
    return 3;
}
//To ask claude later: what is wrong with this init? are these related to the gamma correction processes? 
static int (* const CP437[256])(char*) =
{
    UTF8<0x0020>, UTF8<0x263A>, UTF8<0x263B>, UTF8<0x2665>, UTF8<0x2666>, UTF8<0x2663>, UTF8<0x2660>, UTF8<0x2022>, 
    UTF8<0x25D8>, UTF8<0x25CB>, UTF8<0x25D9>, UTF8<0x2642>, UTF8<0x2640>, UTF8<0x266A>, UTF8<0x266B>, UTF8<0x263C>,
    UTF8<0x25BA>, UTF8<0x25C4>, UTF8<0x2195>, UTF8<0x203C>, UTF8<0x00B6>, UTF8<0x00A7>, UTF8<0x25AC>, UTF8<0x21A8>, 
    UTF8<0x2191>, UTF8<0x2193>, UTF8<0x2192>, UTF8<0x2190>, UTF8<0x221F>, UTF8<0x2194>, UTF8<0x25B2>, UTF8<0x25BC>,
    UTF8<0x0020>, UTF8<0x0021>, UTF8<0x0022>, UTF8<0x0023>, UTF8<0x0024>, UTF8<0x0025>, UTF8<0x0026>, UTF8<0x0027>,
    UTF8<0x0028>, UTF8<0x0029>, UTF8<0x002A>, UTF8<0x002B>, UTF8<0x002C>, UTF8<0x002D>, UTF8<0x002E>, UTF8<0x002F>,
    UTF8<0x0030>, UTF8<0x0031>, UTF8<0x0032>, UTF8<0x0033>, UTF8<0x0034>, UTF8<0x0035>, UTF8<0x0036>, UTF8<0x0037>,
    UTF8<0x0038>, UTF8<0x0039>, UTF8<0x003A>, UTF8<0x003B>, UTF8<0x003C>, UTF8<0x003D>, UTF8<0x003E>, UTF8<0x003F>,
    UTF8<0x0040>, UTF8<0x0041>, UTF8<0x0042>, UTF8<0x0043>, UTF8<0x0044>, UTF8<0x0045>, UTF8<0x0046>, UTF8<0x0047>,
    UTF8<0x0048>, UTF8<0x0049>, UTF8<0x004A>, UTF8<0x004B>, UTF8<0x004C>, UTF8<0x004D>, UTF8<0x004E>, UTF8<0x004F>,
    UTF8<0x0050>, UTF8<0x0051>, UTF8<0x0052>, UTF8<0x0053>, UTF8<0x0054>, UTF8<0x0055>, UTF8<0x0056>, UTF8<0x0057>,
    UTF8<0x0058>, UTF8<0x0059>, UTF8<0x005A>, UTF8<0x005B>, UTF8<0x005C>, UTF8<0x005D>, UTF8<0x005E>, UTF8<0x005F>,
    UTF8<0x0060>, UTF8<0x0061>, UTF8<0x0062>, UTF8<0x0063>, UTF8<0x0064>, UTF8<0x0065>, UTF8<0x0066>, UTF8<0x0067>,
    UTF8<0x0068>, UTF8<0x0069>, UTF8<0x006A>, UTF8<0x006B>, UTF8<0x006C>, UTF8<0x006D>, UTF8<0x006E>, UTF8<0x006F>,
    UTF8<0x0070>, UTF8<0x0071>, UTF8<0x0072>, UTF8<0x0073>, UTF8<0x0074>, UTF8<0x0075>, UTF8<0x0076>, UTF8<0x0077>,
    UTF8<0x0078>, UTF8<0x0079>, UTF8<0x007A>, UTF8<0x007B>, UTF8<0x007C>, UTF8<0x007D>, UTF8<0x007E>, UTF8<0x2302>,
    UTF8<0x00C7>, UTF8<0x00FC>, UTF8<0x00E9>, UTF8<0x00E2>, UTF8<0x00E4>, UTF8<0x00E0>, UTF8<0x00E5>, UTF8<0x00E7>, 
    UTF8<0x00EA>, UTF8<0x00EB>, UTF8<0x00E8>, UTF8<0x00EF>, UTF8<0x00EE>, UTF8<0x00EC>, UTF8<0x00C4>, UTF8<0x00C5>, 
    UTF8<0x00C9>, UTF8<0x00E6>, UTF8<0x00C6>, UTF8<0x00F4>, UTF8<0x00F6>, UTF8<0x00F2>, UTF8<0x00FB>, UTF8<0x00F9>, 
    UTF8<0x00FF>, UTF8<0x00D6>, UTF8<0x00DC>, UTF8<0x00A2>, UTF8<0x00A3>, UTF8<0x00A5>, UTF8<0x20A7>, UTF8<0x0192>, 
    UTF8<0x00E1>, UTF8<0x00ED>, UTF8<0x00F3>, UTF8<0x00FA>, UTF8<0x00F1>, UTF8<0x00D1>, UTF8<0x00AA>, UTF8<0x00BA>, 
    UTF8<0x00BF>, UTF8<0x2310>, UTF8<0x00AC>, UTF8<0x00BD>, UTF8<0x00BC>, UTF8<0x00A1>, UTF8<0x00AB>, UTF8<0x00BB>, 
    UTF8<0x2591>, UTF8<0x2592>, UTF8<0x2593>, UTF8<0x2502>, UTF8<0x2524>, UTF8<0x2561>, UTF8<0x2562>, UTF8<0x2556>, 
    UTF8<0x2555>, UTF8<0x2563>, UTF8<0x2551>, UTF8<0x2557>, UTF8<0x255D>, UTF8<0x255C>, UTF8<0x255B>, UTF8<0x2510>, 
    UTF8<0x2514>, UTF8<0x2534>, UTF8<0x252C>, UTF8<0x251C>, UTF8<0x2500>, UTF8<0x253C>, UTF8<0x255E>, UTF8<0x255F>, 
    UTF8<0x255A>, UTF8<0x2554>, UTF8<0x2569>, UTF8<0x2566>, UTF8<0x2560>, UTF8<0x2550>, UTF8<0x256C>, UTF8<0x2567>, 
    UTF8<0x2568>, UTF8<0x2564>, UTF8<0x2565>, UTF8<0x2559>, UTF8<0x2558>, UTF8<0x2552>, UTF8<0x2553>, UTF8<0x256B>, 
    UTF8<0x256A>, UTF8<0x2518>, UTF8<0x250C>, UTF8<0x2588>, UTF8<0x2584>, UTF8<0x258C>, UTF8<0x2590>, UTF8<0x2580>, 
    UTF8<0x03B1>, UTF8<0x00DF>, UTF8<0x0393>, UTF8<0x03C0>, UTF8<0x03A3>, UTF8<0x03C3>, UTF8<0x00B5>, UTF8<0x03C4>, 
    UTF8<0x03A6>, UTF8<0x0398>, UTF8<0x03A9>, UTF8<0x03B4>, UTF8<0x221E>, UTF8<0x03C6>, UTF8<0x03B5>, UTF8<0x2229>, 
    UTF8<0x2261>, UTF8<0x00B1>, UTF8<0x2265>, UTF8<0x2264>, UTF8<0x2320>, UTF8<0x2321>, UTF8<0x00F7>, UTF8<0x2248>, 
    UTF8<0x00B0>, UTF8<0x2219>, UTF8<0x00B7>, UTF8<0x221A>, UTF8<0x207F>, UTF8<0x00B2>, UTF8<0x25A0>, UTF8<0x0020>
};


int mouse_x = -1;
int mouse_y = -1;
int mouse_down = 0;
int gpm = -1;

bool GetWH(int wh[2])
{
    struct winsize size = {0};
    if (ioctl(0, TIOCGWINSZ, (char *)&size)>=0)
    {
        wh[0] = size.ws_col;
        wh[1] = size.ws_row;

        if (wh[0] > 160)
            wh[0] = 160;
        if (wh[1] > 90)
            wh[1] = 90;
    
    	return true;
    }

	return false;
}


#define FLUSH() \
    do \
    { \
        int w = write(STDOUT_FILENO,out,out_pos); \
        out_pos=0; \
    } while(0)

#define WRITE(...) \
    do \
    { \
        out_pos += sprintf(out+out_pos,__VA_ARGS__); \
        if (out_pos>=out_size-48) FLUSH(); \
    } while(0)


// it turns out we should use our own palette
// it's quite different than xterm!!!!!

uint8_t pal_16[256];

const uint8_t pal_rgba[256][3]=
{
    //{0,0,0},{0,0,170},{0,170,0},{0,85,170},{170,0,0},{170,0,170},{170,170,0},{170,170,170},
    //{85,85,85},{85,85,255},{85,255,85},{85,255,255},{255,85,85},{255,85,255},{255,255,85},{255,255,255},

    {0,0,0},{170,0,0},{0,170,0},{170,85,0},{0,0,170},{170,0,170},{0,170,170},{170,170,170},
    {85,85,85},{255,85,85},{85,255,85},{255,255,85},{85,85,255},{255,85,255},{85,255,255},{255,255,255},

    {  0,  0,  0},{  0,  0, 51},{  0,  0,102},{  0,  0,153},{  0,  0,204},{  0,  0,255},
    {  0, 51,  0},{  0, 51, 51},{  0, 51,102},{  0, 51,153},{  0, 51,204},{  0, 51,255},
    {  0,102,  0},{  0,102, 51},{  0,102,102},{  0,102,153},{  0,102,204},{  0,102,255},
    {  0,153,  0},{  0,153, 51},{  0,153,102},{  0,153,153},{  0,153,204},{  0,153,255},
    {  0,204,  0},{  0,204, 51},{  0,204,102},{  0,204,153},{  0,204,204},{  0,204,255},
    {  0,255,  0},{  0,255, 51},{  0,255,102},{  0,255,153},{  0,255,204},{  0,255,255},

    { 51,  0,  0},{ 51,  0, 51},{ 51,  0,102},{ 51,  0,153},{ 51,  0,204},{ 51,  0,255},
    { 51, 51,  0},{ 51, 51, 51},{ 51, 51,102},{ 51, 51,153},{ 51, 51,204},{ 51, 51,255},
    { 51,102,  0},{ 51,102, 51},{ 51,102,102},{ 51,102,153},{ 51,102,204},{ 51,102,255},
    { 51,153,  0},{ 51,153, 51},{ 51,153,102},{ 51,153,153},{ 51,153,204},{ 51,153,255},
    { 51,204,  0},{ 51,204, 51},{ 51,204,102},{ 51,204,153},{ 51,204,204},{ 51,204,255},
    { 51,255,  0},{ 51,255, 51},{ 51,255,102},{ 51,255,153},{ 51,255,204},{ 51,255,255},
    
    {102,  0,  0},{102,  0, 51},{102,  0,102},{102,  0,153},{102,  0,204},{102,  0,255},
    {102, 51,  0},{102, 51, 51},{102, 51,102},{102, 51,153},{102, 51,204},{102, 51,255},
    {102,102,  0},{102,102, 51},{102,102,102},{102,102,153},{102,102,204},{102,102,255},
    {102,153,  0},{102,153, 51},{102,153,102},{102,153,153},{102,153,204},{102,153,255},
    {102,204,  0},{102,204, 51},{102,204,102},{102,204,153},{102,204,204},{102,204,255},
    {102,255,  0},{102,255, 51},{102,255,102},{102,255,153},{102,255,204},{102,255,255},
    
    {153,  0,  0},{153,  0, 51},{153,  0,102},{153,  0,153},{153,  0,204},{153,  0,255},
    {153, 51,  0},{153, 51, 51},{153, 51,102},{153, 51,153},{153, 51,204},{153, 51,255},
    {153,102,  0},{153,102, 51},{153,102,102},{153,102,153},{153,102,204},{153,102,255},
    {153,153,  0},{153,153, 51},{153,153,102},{153,153,153},{153,153,204},{153,153,255},
    {153,204,  0},{153,204, 51},{153,204,102},{153,204,153},{153,204,204},{153,204,255},
    {153,255,  0},{153,255, 51},{153,255,102},{153,255,153},{153,255,204},{153,255,255},
    
    {204,  0,  0},{204,  0, 51},{204,  0,102},{204,  0,153},{204,  0,204},{204,  0,255},
    {204, 51,  0},{204, 51, 51},{204, 51,102},{204, 51,153},{204, 51,204},{204, 51,255},
    {204,102,  0},{204,102, 51},{204,102,102},{204,102,153},{204,102,204},{204,102,255},
    {204,153,  0},{204,153, 51},{204,153,102},{204,153,153},{204,153,204},{204,153,255},
    {204,204,  0},{204,204, 51},{204,204,102},{204,204,153},{204,204,204},{204,204,255},
    {204,255,  0},{204,255, 51},{204,255,102},{204,255,153},{204,255,204},{204,255,255},

    {255,  0,  0},{255,  0, 51},{255,  0,102},{255,  0,153},{255,  0,204},{255,  0,255},
    {255, 51,  0},{255, 51, 51},{255, 51,102},{255, 51,153},{255, 51,204},{255, 51,255},
    {255,102,  0},{255,102, 51},{255,102,102},{255,102,153},{255,102,204},{255,102,255},
    {255,153,  0},{255,153, 51},{255,153,102},{255,153,153},{255,153,204},{255,153,255},
    {255,204,  0},{255,204, 51},{255,204,102},{255,204,153},{255,204,204},{255,204,255},
    {255,255,  0},{255,255, 51},{255,255,102},{255,255,153},{255,255,204},{255,255,255},        
};

void Print(AnsiCell* buf, int w, int h, const char utf[256][4])
{
    // heading
    // w x (fg,bg,3bytes)

    int bk=-1,fg=-1;

    // home

    // 2.3MB out buffer
    const int out_size = 3/*header*/ + 40/*fg,bg,ch*/ * 320/*width*/ * 180/*height*/ + 180/*'\n'*/; // 4096;
    char out[out_size];
    int out_pos = 0;

    WRITE("\x1B[H");

    int fg16 = 0;
    int bk16 = 1;

#ifdef USE_GPM
    if (gpm>=0)
    {
        // bake mouse into buffer
        if (mouse_x>=0 && mouse_y>=0 && mouse_x<w && mouse_y<h)
        {
            static const AnsiCell mouse = { 0, 231, '+', 0 };
            buf[mouse_x + w*(h-1-mouse_y)] = mouse;
        }
    }
#endif // USE_GPM


    if (tty>=0)
    {
        // in linux virtual console we will use just 2 colors
        WRITE("\x1B[%d;%d;%dm",(fg16&7)+(fg16<8?30:90),(bk16&7)+40,bk16<8?25:5);    

        for (int y = h-1; y>=0; y--)
        {
            AnsiCell* ptr = buf + y*w;
            for (int x=0; x<w; x++,ptr++)
            {
                const char* chr = utf[ptr->gl];
                if (ptr->fg != fg)
                {
                    if (ptr->bk != bk)
                    {
                        WRITE("\e]P%X%02x%02x%02x", fg16, pal_rgba[ptr->fg][0], pal_rgba[ptr->fg][1], pal_rgba[ptr->fg][2]);
                        WRITE("\e]P%X%02x%02x%02x", bk16, pal_rgba[ptr->bk][0], pal_rgba[ptr->bk][1], pal_rgba[ptr->bk][2]);
                        WRITE("%s", chr);
                    }
                    else
                    {
                        WRITE("\e]P%X%02x%02x%02x", fg16, pal_rgba[ptr->fg][0], pal_rgba[ptr->fg][1], pal_rgba[ptr->fg][2]);
                        WRITE("%s", chr);
                    }
                }
                else
                {
                    if (ptr->bk != bk)
                    {
                        WRITE("\e]P%X%02x%02x%02x", bk16, pal_rgba[ptr->bk][0], pal_rgba[ptr->bk][1], pal_rgba[ptr->bk][2]);
                        WRITE("%s", chr);
                    }
                    else
                        WRITE("%s",chr);
                }
                bk=ptr->bk;
                fg=ptr->fg;
            }

            if (y)
                WRITE("\n");
        }
    }
    else
    {
        for (int y = h-1; y>=0; y--)
        {
            AnsiCell* ptr = buf + y*w;
            for (int x=0; x<w; x++,ptr++)
            {
                //const char* chr = (x+y)&1 ? "X":"Y";
                const char* chr = utf[ptr->gl];
                if (ptr->fg != fg)
                    if (ptr->bk != bk)
                        WRITE("\x1B[38;5;%d;48;5;%dm%s",ptr->fg,ptr->bk,chr);
                    else
                        WRITE("\x1B[38;5;%dm%s",ptr->fg,chr);
                else
                    if (ptr->bk != bk)
                        WRITE("\x1B[48;5;%dm%s",ptr->bk,chr);
                    else
                        WRITE("%s",chr);

                bk=ptr->bk;
                fg=ptr->fg;
            }

            if (y)
                WRITE("\n");
        }
    }

    FLUSH();
}

bool running = false;
void exit_handler(int signum)
{
    running = false;
#ifdef PURE_TERM
    SetScreen(false);
#endif
    FreeAudio();
    if (tty>0)
    {
        // restore old font
        const char* temp_dir = getenv("SNAP_USER_DATA");
        if (!temp_dir || !temp_dir[0])
            temp_dir = "/tmp";
        
        char cmd[2048];
        sprintf(cmd,"setfont %s/asciicker.%d.psf; rm %s/asciicker.%d.psf; clear;", temp_dir, tty, temp_dir, tty);
        int errlvl = system(cmd);
    }

    exit(0);
}

// Get high-resolution monotonic timestamp in microseconds.
// WHY PLATFORM-SPECIFIC: Different platforms have different clock sources.
// - Linux/macOS: clock_gettime(CLOCK_MONOTONIC) via POSIX
// - Windows: a3dGetTime() via QueryPerformanceCounter
// - Web: clock_gettime(CLOCK_MONOTONIC) emulated by Emscripten
// WHY MONOTONIC: Immune to system clock adjustments (NTP, daylight saving).
// Used for frame timing, animation, profiling.
uint64_t GetTime()
{
	static timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

#ifdef PURE_TERM
// PURE_TERM builds exclude sdl.cpp/x11.cpp; provide a3dGetTime() here
uint64_t a3dGetTime() { return GetTime(); }
#endif

#else

#define GetTime() a3dGetTime()

#endif

Material mat[256];
void* GetMaterialArr()
{
    return mat;
}

static int ApplyFL4131NativeMaterialGlyphCell(void* user, int material_id, int elev, int shade, GlyphId glyph_id, uint16_t coverage)
{
	Material* materials = (Material*)user;
	if (!materials || material_id < 0 || material_id >= 256 || elev < 0 || elev >= 4 || shade < 0 || shade >= 16)
		return 1;
	if (!materials[material_id].glyph_plane)
	{
		materials[material_id].glyph_plane = material_glyph_plane_alloc();
		if (!materials[material_id].glyph_plane)
			return 1;
		material_glyph_plane_init(materials[material_id].glyph_plane);
	}
	const int idx = elev * 16 + shade;
	materials[material_id].glyph_plane->cells[idx] = glyph_id;
	materials[material_id].glyph_plane->coverage[idx] = coverage;
	return 0;
}

static bool RunFL4131NativeMaterialSidecarProof()
{
	const GlyphId expected_glyphs[] = {
		512, 513, 514, 515, 516, 517, 518, 519,
		520, 521, 522, 523, 524, 525, 526, 527,
		528, 529, 530, 531, 532, 533, 534, 535,
		536, 537, 538, 539, 540, 541, 542, 543,
		544, 545, 546, 547, 548, 549, 550, 551,
		552, 553, 554, 555, 556, 557, 558, 559,
		560, 561, 562, 563, 564, 565, 566, 567,
		568, 569, 570, 571, 572, 573, 574, 575,
		576, 577, 578, 579, 580, 581, 582, 583,
		584, 585, 586, 587, 588, 589, 590, 591,
		592, 593, 594, 595, 596, 597, 598, 599,
		600, 601, 602, 603, 604,
		605, 606, 607, 608, 609, 610, 611, 612,
		613, 614, 615, 616, 617, 618, 619, 620,
		621, 622, 623, 624, 625, 626, 627, 628,
		629, 630, 631
	};
	const int expected_count = (int)(sizeof(expected_glyphs) / sizeof(expected_glyphs[0]));
	for (int i = 0; i < 256; i++)
	{
		material_glyph_plane_free(mat[i].glyph_plane);
		mat[i].glyph_plane = NULL;
	}
	char errbuf[512] = "";
	int applied_cells = 0;
	const char* map_path = "assets/glyphs/fixtures/fl4131_material_sidecar_valid.a3d";
	const int rc = material_sidecar_load_apply_for_map(
		map_path,
		ApplyFL4131NativeMaterialGlyphCell,
		mat,
		"[FL4131_NATIVE_PROOF]",
		&applied_cells,
		errbuf,
		sizeof(errbuf));
	const bool loaded = rc == 0;
	bool cells_ok = true;
	bool coverage_ok = true;
	bool display_not_fallback = true;
	int coverage_cells = 0;
	int display_cells = 0;
	for (int i = 0; i < expected_count; i++)
	{
		const int material_id = i < 64 ? 1 : 2;
		const int cell_index = i < 64 ? i : i - 64;
		const int elev = cell_index / 16;
		const int shade = cell_index % 16;
		const MaterialGlyphPlane* plane = mat[material_id].glyph_plane;
		const GlyphId actual = material_glyph_plane_lookup(plane, elev, shade);
		const uint16_t coverage = material_glyph_plane_lookup_coverage(plane, elev, shade);
		const uint8_t display_glyph = material_glyph_plane_coverage_display_glyph(coverage);
		cells_ok = cells_ok && actual == expected_glyphs[i];
		coverage_ok = coverage_ok && coverage != 0;
		display_not_fallback = display_not_fallback && display_glyph != mat[material_id].shade[elev][shade].gl;
		if (coverage != 0)
			coverage_cells++;
		if (coverage != 0 && display_glyph != 0 && display_glyph != mat[material_id].shade[elev][shade].gl)
			display_cells++;
	}
	const bool pass = loaded && applied_cells == expected_count && cells_ok && coverage_ok && display_not_fallback;
	printf("[FL4131_NATIVE_MATERIAL_SIDECAR_PROOF_START]\n");
	printf("{\n");
	printf("  \"schema\": \"fl4131_native_material_sidecar_load.v1\",\n");
	printf("  \"verdict\": \"%s\",\n", pass ? "PASS" : "FAIL");
	printf("  \"map_path\": \"%s\",\n", map_path);
	printf("  \"loaded\": %s,\n", loaded ? "true" : "false");
	printf("  \"applied_cells\": %d,\n", applied_cells);
	printf("  \"expected_cells\": %d,\n", expected_count);
	printf("  \"first_glyph_id\": %u,\n", (unsigned)material_glyph_plane_lookup(mat[1].glyph_plane, 0, 0));
	printf("  \"last_glyph_id\": %u,\n", (unsigned)material_glyph_plane_lookup(mat[2].glyph_plane, 3, 7));
	printf("  \"cells_ok\": %s,\n", cells_ok ? "true" : "false");
	printf("  \"coverage_cells\": %d,\n", coverage_cells);
	printf("  \"coverage_ok\": %s,\n", coverage_ok ? "true" : "false");
	printf("  \"display_cells\": %d,\n", display_cells);
	printf("  \"display_not_fallback_bytes\": %s,\n", display_not_fallback ? "true" : "false");
	printf("  \"error\": \"%s\"\n", loaded ? "" : errbuf);
	printf("}\n");
	printf("[FL4131_NATIVE_MATERIAL_SIDECAR_PROOF_END]\n");
	return pass;
}

// Initialize materials - must match editor definitions (asciiid.cpp)
void InitMaterials()
{
	// Fast random number generator for placeholder materials
	static uint32_t fast_rand_seed = 123456789;
	auto fast_rand = []() -> uint32_t {
		fast_rand_seed = (1103515245 * fast_rand_seed + 12345) & 0x7FFFFFFF;
		return fast_rand_seed;
	};

	// MATERIAL 0: WATER
	uint8_t water_glyphs[4] = {',',' ','!',' '};
	uint8_t water_fg[4] = {0xFF,0xA0,0x64,0x00};
	for (int s=0; s<16; s++)
	{
		for (int r=0; r<4; r++)
		{
			mat[0].shade[r][s].fg[0]=water_fg[r];
			mat[0].shade[r][s].fg[1]=water_fg[r];
			mat[0].shade[r][s].fg[2]=water_fg[r];
			mat[0].shade[r][s].gl = water_glyphs[r];
			mat[0].shade[r][s].bg[0]=0xCF;
			mat[0].shade[r][s].bg[1]=0xCF;
			mat[0].shade[r][s].bg[2]=0xCF;
			mat[0].shade[r][s].flags = 0;
		}
	}

	// MATERIAL 1: GRASS
	uint8_t grass_bg_base[3] = {34, 139, 34};
	uint8_t grass_fg_base[3] = {144, 238, 144};
	uint8_t grass_glyphs[4] = {'"', '\'', '"', '`'};
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
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

	// MATERIAL 2: DIRT
	uint8_t dirt_bg_base[3] = {101, 67, 33};
	uint8_t dirt_fg_base[3] = {160, 120, 80};
	uint8_t dirt_glyphs[4] = {'.', ':', ',', '\''};
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

	// MATERIAL 3: STONE
	uint8_t stone_bg_base[3] = {105, 105, 105};
	uint8_t stone_fg_base[3] = {169, 169, 169};
	uint8_t stone_glyphs[4] = {'#', 'O', '8', '@'};
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

	// MATERIAL 4: SAND
	uint8_t sand_bg_base[3] = {194, 178, 128};
	uint8_t sand_fg_base[3] = {238, 232, 170};
	uint8_t sand_glyphs[4] = {' ', '.', ':', ','};
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

	// MATERIAL 5: SNOW
	uint8_t snow_bg_base[3] = {230, 240, 255};
	uint8_t snow_fg_base[3] = {255, 255, 255};
	uint8_t snow_glyphs[4] = {'*', '+', '.', ' '};
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
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

	// MATERIAL 6: MUD
	uint8_t mud_bg_base[3] = {64, 46, 30};
	uint8_t mud_fg_base[3] = {96, 70, 46};
	uint8_t mud_glyphs[4] = {'~', '=', '-', '.'};
	for (int r = 0; r < 4; r++)
	{
		for (int s = 0; s < 16; s++)
		{
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

	// MATERIAL 7: COBBLESTONE
	uint8_t cobble_bg_base[3] = {112, 128, 144};
	uint8_t cobble_fg_base[3] = {176, 196, 222};
	uint8_t cobble_glyphs[4] = {'o', 'O', '0', '@'};
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

	// MATERIAL 8: GRAVEL
	uint8_t gravel_bg_base[3] = {150, 150, 150};
	uint8_t gravel_fg_base[3] = {190, 190, 190};
	uint8_t gravel_glyphs[4] = {'.', ':', ';', ','};
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
	for (int i = 9; i < 256; i++)
	{
		for (int r = 0; r < 4; r++)
		{
			for (int s = 0; s < 16; s++)
			{
				mat[i].shade[r][s].bg[0] = fast_rand() & 0xFF;
				mat[i].shade[r][s].bg[1] = fast_rand() & 0xFF;
				mat[i].shade[r][s].bg[2] = fast_rand() & 0xFF;
				mat[i].shade[r][s].fg[0] = fast_rand() & 0xFF;
				mat[i].shade[r][s].fg[1] = fast_rand() & 0xFF;
				mat[i].shade[r][s].fg[2] = fast_rand() & 0xFF;
				mat[i].shade[r][s].gl = fast_rand() & 0xFF;
				mat[i].shade[r][s].flags = 0;
			}
		}
	}
}

void* GetFontArr();
int fonts_loaded=0;
struct MyFont
{
	static bool Scan(A3D_DirItem item, const char* name, void* cookie)
	{
		if (!(item&A3D_FILE))
			return true;

		char buf[4096];
		snprintf(buf,4095,"%s/%s",(char*)cookie,name);
		buf[4095]=0;

		a3dLoadImage(buf, 0, MyFont::Load);
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
} font[256];

void* GetFontArr()
{
	return font;
}

// make term happy
float pos_x,pos_y,pos_z;
float rot_yaw;
int probe_z;
float global_lt[4];
World* world=0;
Terrain* terrain=0;

int font_zoom = 0;

static int FindFont(const int wnd_wh[2])
{
    float err = 0;

    assert(wnd_wh);
    float area = (float)(wnd_wh[0]*wnd_wh[1]);

	int j = -1;
    for (int i=0; i<fonts_loaded; i++)
    {
        MyFont* f = font + i;
        float e = fabsf( 120.0f*75.0f - area / ((f->width>>4)*(f->height>>4)));

        if (!i || e<err)
        {
			j = i;
            err = e;
        }
    }

    return j;
}

int GetGLFont(int wh[2], const int wnd_wh[2], int* id)
{
    int j = FindFont(wnd_wh);

	j += font_zoom;
	j = j < 0 ? 0 : j >= fonts_loaded ? fonts_loaded - 1 : j;

    // FIX IF TOO SMALL (45x36)
    while (j)
    {
        int cw = wnd_wh[0] / (font[j].width >> 4);
        int ch = wnd_wh[1] / (font[j].height >> 4);

        if (cw<45 || ch<36)
            j--;
        else
            break;
    }

	MyFont* f = font + j;

	if (wh)
	{
		wh[0] = f->width;
		wh[1] = f->height;
	}

    if (id)
        *id = j;

	return f->tex;
}

static int tty_font = 4;
static const int tty_fonts[] = {6,8,10,12,14,16,18,20,24,28,32,-1};

// TODO WEB ZOOMING & FULLSCREENING!
// ...

#ifdef PURE_TERM
static bool xterm_fullscreen = false;
void ToggleFullscreen(Game* g)
{
    const char* term_env = getenv("TERM");
    if (!term_env)
        term_env = "";
    if (strcmp( term_env, "linux" ) != 0)
    {
        xterm_fullscreen = !xterm_fullscreen;
        if (xterm_fullscreen)
            int w = write(STDOUT_FILENO, "\033[9;1t",6);
        else
            int w = write(STDOUT_FILENO, "\033[9;0t",6);
    }
}

bool IsFullscreen(Game* g)
{
    return xterm_fullscreen;
}
#endif

bool PrevGLFont()
{
    #ifdef PURE_TERM
    if (tty>0)
    {
        int errlvl;
        tty_font--;
        if (tty_font<0)
            tty_font=0;
        char cmd[1024+50];
        sprintf(cmd,"setfont %sassets/fonts/cp437_%dx%d.png.psf", base_path, tty_fonts[tty_font], tty_fonts[tty_font]);
        errlvl = system(cmd);
    }
    else
    {
        // this will work only if xterm has enabled font ops
        int w = write(STDOUT_FILENO, "\033]50;#-1\a",9);
        if (xterm_fullscreen)
            int w = write(STDOUT_FILENO, "\033[9;1t",6);
        else
            int w = write(STDOUT_FILENO, "\033[9;0t",6);
    }
    #else
    /*
	font_zoom--;
	if (font_zoom < -fonts_loaded / 2)
	{
		font_zoom = -fonts_loaded / 2;
		return false;
	}
    */

    int wh[2], font_wh[2];
    a3dGetRect(0, 0, wh);
    int f;
    GetGLFont(font_wh,wh,&f);
    if (f==0)
        return false;
    int u = font_zoom;
    font_zoom = f - FindFont(wh) - 1;
    int f2;
    GetGLFont(font_wh,wh,&f2);
    if (f2 == f) // clamped!
    {
        font_zoom = u;  // revert!
        return false;
    }
    else
    {
        font_zoom = f2 - FindFont(wh);
    	TermResizeAll();
    }

    #endif
	return true;
}

bool NextGLFont()
{
    #ifdef PURE_TERM
    if (tty>0)
    {
        int errlvl;
        tty_font++;
        if (tty_fonts[tty_font]<0)
            tty_font--;
        char cmd[1024+50];
        sprintf(cmd,"setfont %sassets/fonts/cp437_%dx%d.png.psf", base_path, tty_fonts[tty_font], tty_fonts[tty_font]);
        errlvl = system(cmd);
    }
    else
    {
        // this will work only if xterm has enabled font ops
        int w = write(STDOUT_FILENO, "\033]50;#+1\a",9);
        if (xterm_fullscreen)
            int w = write(STDOUT_FILENO, "\033[9;1t",6);
        else
            int w = write(STDOUT_FILENO, "\033[9;0t",6);
    }
    #else
    /*
	font_zoom++;
	if (font_zoom > fonts_loaded/2)
	{
		font_zoom = fonts_loaded/2;
		return false;
	}
    */

    int wh[2], font_wh[2];
    a3dGetRect(0, 0, wh);
    int f;
    GetGLFont(font_wh,wh,&f);
    if (f==fonts_loaded-1)
        return false;
    int u = font_zoom;
    font_zoom = f - FindFont(wh) + 1;
    int f2;
    GetGLFont(font_wh,wh,&f2);
    if (f2 == f) // clamped!
    {
        font_zoom = u;  // revert!
        return false;
    }
    else
    {
        font_zoom = f2 - FindFont(wh);
    	TermResizeAll();
    }

    #endif
	return true;
}

static bool local_authoritative_session_owned = false;
#if defined(__linux__) || defined(__APPLE__)
static pid_t local_authoritative_server_pid = -1;
#else
static intptr_t local_authoritative_server_pid = 0;
#endif
static char local_authoritative_server_port[16] = "";
static char local_authoritative_server_log_path[PATH_MAX] = "";

struct GameServer : Server
{
	TCP_SOCKET server_socket;

	struct MSG_FIFO
	{
		uint8_t data[2048];
		int size;
	};

	static const int max_msg_size = (int)sizeof(MSG_FIFO::data);
	static const int msg_size = (1 << 16) / max_msg_size;
	MSG_FIFO msg[msg_size];

	int msg_read; // r/w only by main-thread wrapped at 256 to 0
	int msg_write; // r/w only by net-thread wrapped at 256 to 0

	volatile unsigned int msg_num; // inter_inc by net-thread, inter_and(0) by main-thread
	uint32_t dropped_refreshable_packets = 0;
	uint8_t last_dropped_refreshable_token = 0;
	uint32_t queue_full_wait_logs = 0;
	uint32_t dropped_refreshable_logs = 0;
	uint32_t send_logs = 0;

	static bool IsDroppableInboundPacketToken(const uint8_t* data, int size)
	{
		if (!data || size <= 0)
			return false;

		switch (data[0])
		{
			case 'b': // baseline snapshots refresh state the same way later snapshots do
			case 'p': // legacy pose; authoritative snapshots supersede it
			case 'q': // authoritative delta snapshot; later deltas refresh state
			case 'l': // lag echo; telemetry-only
				return true;
			default:
				return false;
		}
	}

	bool Start()
	{
		authority.head = 0;
		authority.tail = 0;

		authority.others = (Human*)calloc((size_t)connection.max_clients, sizeof(Human));
		if (!authority.others)
			return false;

		msg_read = 0;
		msg_write = 0;
		msg_num = 0;

		bool ok = THREAD_CREATE_DETACHED(Entry, this);

		if (!ok)
		{
			return false;
		}

        connection.stamp = GetTime();

		return true;
	}

	void Recv()
	{
		uint8_t scratch[max_msg_size];
		bool logged_first_read = false;
		while (1)
		{
			bool queue_full = (__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE) == msg_size);
			uint8_t* read_buf = queue_full ? scratch : (msg + msg_write)->data;
			if (!logged_first_read)
			{
				uint8_t peek_buf[16] = {};
				int peek_flags = MSG_PEEK;
#if defined(__linux__) || defined(__APPLE__)
				peek_flags |= MSG_DONTWAIT;
#endif
				errno = 0;
				int peek_r = recv(server_socket, (char*)peek_buf, (int)sizeof(peek_buf), peek_flags);
				printf("[FL-2896-RECV-PEEK] r=%d errno=%d b0=%02x b1=%02x b2=%02x b3=%02x local_id=%d\n",
					peek_r,
					errno,
					peek_r > 0 ? (unsigned)peek_buf[0] : 0u,
					peek_r > 1 ? (unsigned)peek_buf[1] : 0u,
					peek_r > 2 ? (unsigned)peek_buf[2] : 0u,
					peek_r > 3 ? (unsigned)peek_buf[3] : 0u,
					connection.local_id);
				fflush(stdout);
			}
			int r = WS_READ(server_socket, read_buf, max_msg_size, 0);
			if (!logged_first_read)
			{
				printf("[FL-2896-RECV-FIRST-READ] r=%d queue_full=%d local_id=%d\n",
					r, queue_full ? 1 : 0, connection.local_id);
				fflush(stdout);
				logged_first_read = true;
			}

			if (r <= 0)
			{
				while (__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE) == msg_size)
				{
					if (queue_full_wait_logs < 32)
					{
						printf("[FL-2896-RECV-QUEUE-FULL] stamp_us=%llu msg_num=%u size=%d token=%c local_id=%d close_marker=1\n",
							(unsigned long long)(GetTime() * 1000000.0),
							(unsigned)__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE),
							r,
							'-',
							connection.local_id);
						fflush(stdout);
						queue_full_wait_logs++;
					}
					THREAD_SLEEP(15);
				}
				MSG_FIFO* m = msg + msg_write;
				m->size = r;
				INTERLOCKED_INC(&msg_num);
				break;
			}

			if (queue_full && IsDroppableInboundPacketToken(read_buf, r))
			{
				dropped_refreshable_packets++;
				last_dropped_refreshable_token = read_buf[0];
				if (dropped_refreshable_logs < 32)
				{
					printf("[FL-2896-RECV-DROP] stamp_us=%llu msg_num=%u size=%d token=%c dropped=%u local_id=%d\n",
						(unsigned long long)(GetTime() * 1000000.0),
						(unsigned)__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE),
						r,
						(char)read_buf[0],
						(unsigned)dropped_refreshable_packets,
						connection.local_id);
					fflush(stdout);
					dropped_refreshable_logs++;
				}
				continue;
			}

			// FL-2896: never block the recv thread when the queue is full.
			// During map loading, Proc() cannot drain the queue because the
			// main thread is blocked in LoadGame(). Blocking here stops
			// WS_READ, fills the TCP recv buffer, stalls server outbound,
			// and eventually triggers keepalive_timeout on the server side.
			// Drop the packet instead — the server will re-send fresh state
			// once the main thread resumes draining.
			if (__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE) == msg_size)
			{
				dropped_refreshable_packets++;
				last_dropped_refreshable_token = read_buf[0];
				if (queue_full_wait_logs < 32)
				{
					printf("[FL-2896-RECV-QUEUE-DROP] stamp_us=%llu msg_num=%u size=%d token=%c local_id=%d\n",
						(unsigned long long)(GetTime() * 1000000.0),
						(unsigned)__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE),
						r,
						(r > 0 && read_buf) ? (char)read_buf[0] : '-',
						connection.local_id);
					fflush(stdout);
					queue_full_wait_logs++;
				}
				continue;
			}
			// FL-2896: was a blocking spin-wait — caused the recv thread to stall
			// during map loading when Proc() could not drain the queue.
			// while (__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE) == msg_size)
			// {
			// 	if (queue_full_wait_logs < 32)
			// 	{
			// 		printf("[FL-2896-RECV-QUEUE-FULL] stamp_us=%llu msg_num=%u size=%d token=%c local_id=%d close_marker=0\n",
			// 			(unsigned long long)(GetTime() * 1000000.0),
			// 			(unsigned)__atomic_load_n(&msg_num, __ATOMIC_ACQUIRE),
			// 			r,
			// 			(r > 0 && read_buf) ? (char)read_buf[0] : '-',
			// 			connection.local_id);
			// 		fflush(stdout);
			// 		queue_full_wait_logs++;
			// 	}
			// 	THREAD_SLEEP(15);
			// }

			MSG_FIFO* m = msg + msg_write;
			if (read_buf != m->data)
				memcpy(m->data, read_buf, (size_t)r);
			m->size = r;

			INTERLOCKED_INC(&msg_num);
			msg_write = (msg_write + 1)&(msg_size - 1);
		}
	}

	static void* Entry(void* arg)
	{
		GameServer* gs = (GameServer*)arg;
		gs->Recv();
		return 0;
	}

	void Stop()
	{
		// finish thread
		// close socket
		if (server_socket != INVALID_TCP_SOCKET)
		{
			TCP_CLOSE(server_socket);
			TCP_CLEANUP();
		}

		server_socket = INVALID_TCP_SOCKET;

		if (authority.others)
			free(authority.others);
		authority.others = 0;
	}
};

static GameServer* Connect(const char* addr, const char* port, const char* path, const char* user);

static void StopGameServerClientConnection()
{
	if (!server)
		return;
	GameServer* gs = (GameServer*)server;
	printf("[NATIVE-AUTH-STOP-CONNECTION] local_owned=%d pid=%d server=%p local_id=%d socket=%d dropped_refreshable=%u last_drop=%c\n",
		local_authoritative_session_owned ? 1 : 0,
		(int)local_authoritative_server_pid,
		(void*)server,
		gs->connection.local_id,
		(int)gs->server_socket,
		(unsigned)gs->dropped_refreshable_packets,
		gs->last_dropped_refreshable_token ? (char)gs->last_dropped_refreshable_token : '-');
	fflush(stdout);
	gs->Stop();
	free(server);
	server = 0;
}

#if defined(__linux__) || defined(__APPLE__)
static bool LocalAuthoritativeServerStillRunning()
{
	if (local_authoritative_server_pid <= 0)
		return false;
	int status = 0;
	pid_t rc = waitpid(local_authoritative_server_pid, &status, WNOHANG);
	if (rc == 0)
		return true;
	if (rc == local_authoritative_server_pid)
	{
		local_authoritative_server_pid = -1;
		return false;
	}
	return false;
}

static bool ChooseOwnedLocalAuthoritativeListenerSocket(TCP_SOCKET* listen_socket, char* out, size_t out_cap)
{
	if (!listen_socket || !out || out_cap == 0)
		return false;
	*listen_socket = INVALID_TCP_SOCKET;
	out[0] = 0;

	TCP_SOCKET probe = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (probe == INVALID_TCP_SOCKET)
		return false;

	int optval = 1;
	setsockopt(probe, SOL_SOCKET, SO_REUSEADDR, (const char*)&optval, sizeof(optval));

	struct sockaddr_in addr;
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_port = htons(0);
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	if (bind(probe, (struct sockaddr*)&addr, sizeof(addr)) < 0)
	{
		TCP_CLOSE(probe);
		return false;
	}
	if (listen(probe, 1) < 0)
	{
		TCP_CLOSE(probe);
		return false;
	}

	socklen_t addr_len = (socklen_t)sizeof(addr);
	if (getsockname(probe, (struct sockaddr*)&addr, &addr_len) < 0)
	{
		TCP_CLOSE(probe);
		return false;
	}

	unsigned port = (unsigned)ntohs(addr.sin_port);
	if (port == 0)
	{
		TCP_CLOSE(probe);
		return false;
	}

	snprintf(out, out_cap, "%u", port);
	out[out_cap - 1] = 0;
	*listen_socket = probe;
	return true;
}

static bool SpawnOwnedLocalAuthoritativeServer(const char* port, const char* map_path, TCP_SOCKET listen_socket)
{
	if (!port || !port[0])
		return false;

	char server_path[PATH_MAX];
	snprintf(server_path, sizeof(server_path), "%s.run/server", base_path);
	if (access(server_path, X_OK) != 0)
	{
		printf("LOCAL AUTH: missing server binary at %s\n", server_path);
		return false;
	}

	snprintf(local_authoritative_server_log_path, sizeof(local_authoritative_server_log_path),
		"/tmp/asciicker-local-server-%d-%s.log", (int)getpid(), port);

	int log_fd = open(local_authoritative_server_log_path, O_CREAT | O_WRONLY | O_TRUNC, 0644);
	pid_t pid = fork();
	if (pid < 0)
	{
		if (log_fd >= 0)
			close(log_fd);
		printf("LOCAL AUTH: fork() failed\n");
		return false;
	}

	if (pid == 0)
	{
		if (log_fd >= 0)
		{
			dup2(log_fd, STDOUT_FILENO);
			dup2(log_fd, STDERR_FILENO);
			if (log_fd > STDERR_FILENO)
				close(log_fd);
		}
		if (listen_socket != INVALID_TCP_SOCKET)
		{
			char fd_buf[32];
			snprintf(fd_buf, sizeof(fd_buf), "%d", (int)listen_socket);
			if (setenv("ASCIICKER_LISTEN_FD", fd_buf, 1) != 0)
			{
				fprintf(stderr, "LOCAL AUTH: failed to set ASCIICKER_LISTEN_FD=%s\n", fd_buf);
				fflush(stderr);
				_exit(127);
			}
			if (setenv("ASCIICKER_LISTEN_PORT", port, 1) != 0)
			{
				fprintf(stderr, "LOCAL AUTH: failed to set ASCIICKER_LISTEN_PORT=%s\n", port);
				fflush(stderr);
				_exit(127);
			}
		}
		if (setenv("ASCIICKER_DISABLE_WS_KEEPALIVE", "1", 1) != 0)
		{
			fprintf(stderr, "LOCAL AUTH: failed to disable WS keepalive for owned local server\n");
			fflush(stderr);
			_exit(127);
		}
		if (map_path && map_path[0])
			execl(server_path, server_path, "--port", port,
				"--max-players", "1", "--map", map_path, (char*)0);
		else
			execl(server_path, server_path, "--port", port,
				"--max-players", "1", (char*)0);
		_exit(127);
	}

	if (log_fd >= 0)
		close(log_fd);
	if (listen_socket != INVALID_TCP_SOCKET)
		TCP_CLOSE(listen_socket);

	local_authoritative_server_pid = pid;
	local_authoritative_session_owned = true;
	snprintf(local_authoritative_server_port, sizeof(local_authoritative_server_port), "%s", port);
	return true;
}

static void StopOwnedLocalAuthoritativeServerProcess()
{
	if (!local_authoritative_session_owned)
		return;

	StopGameServerClientConnection();

	if (local_authoritative_server_pid > 0)
	{
		kill(local_authoritative_server_pid, SIGTERM);
		for (int i = 0; i < 20; i++)
		{
			if (!LocalAuthoritativeServerStillRunning())
				break;
			THREAD_SLEEP(50);
		}
		if (LocalAuthoritativeServerStillRunning())
		{
			kill(local_authoritative_server_pid, SIGKILL);
			waitpid(local_authoritative_server_pid, 0, 0);
			local_authoritative_server_pid = -1;
		}
	}

	local_authoritative_session_owned = false;
	local_authoritative_server_port[0] = 0;
	local_authoritative_server_log_path[0] = 0;
}
#else
static void StopOwnedLocalAuthoritativeServerProcess()
{
	if (!local_authoritative_session_owned)
		return;
	StopGameServerClientConnection();
	local_authoritative_session_owned = false;
	local_authoritative_server_pid = 0;
	local_authoritative_server_port[0] = 0;
	local_authoritative_server_log_path[0] = 0;
}
#endif

bool EnsureNormalGameAuthoritativeSession(const char* user, const char* map_path)
{
	if (server)
		return true;

#if defined(__linux__) || defined(__APPLE__)
	const char* safe_user = (user && user[0]) ? user : "player";
	for (int attempt = 0; attempt < 12; attempt++)
	{
		TCP_SOCKET listener = INVALID_TCP_SOCKET;
		char port_buf[16];
		if (!ChooseOwnedLocalAuthoritativeListenerSocket(&listener, port_buf, sizeof(port_buf)))
		{
			printf("LOCAL AUTH: could not reserve a loopback port for owned server startup\n");
			break;
		}
		if (!SpawnOwnedLocalAuthoritativeServer(port_buf, map_path, listener))
		{
			if (listener != INVALID_TCP_SOCKET)
				TCP_CLOSE(listener);
			continue;
		}

		for (int wait_i = 0; wait_i < 200; wait_i++)
		{
			GameServer* gs = Connect("127.0.0.1", port_buf, "ws/y8/", safe_user);
			if (gs)
			{
				server = gs;
				if (!gs->Start())
				{
					StopOwnedLocalAuthoritativeServerProcess();
					printf("LOCAL AUTH: client start failed after connect on port %s\n", port_buf);
					return false;
				}
				printf("LOCAL AUTH: connected to owned local server on port %s (pid=%d)\n",
					port_buf, (int)local_authoritative_server_pid);
				return true;
			}
			if (!LocalAuthoritativeServerStillRunning())
				break;
			THREAD_SLEEP(50);
		}

		printf("LOCAL AUTH: failed to connect on port %s, see %s\n",
			port_buf,
			local_authoritative_server_log_path[0] ? local_authoritative_server_log_path : "(no log)");
		StopOwnedLocalAuthoritativeServerProcess();
	}

	printf("LOCAL AUTH: could not start local authoritative session\n");
	return false;
#else
	printf("LOCAL AUTH: unsupported on this platform build\n");
	return false;
#endif
}

void StopNormalGameAuthoritativeSession()
{
	StopOwnedLocalAuthoritativeServerProcess();
}

bool Server::Send(const uint8_t* data, int size)
{
	GameServer* gs = (GameServer*)this;
	if (gs->send_logs < 64)
	{
		printf("[FL-2896-SEND] stamp_us=%llu token=%c size=%d local_id=%d\n",
			(unsigned long long)(GetTime() * 1000000.0),
			(data && size > 0) ? (char)data[0] : '-',
			size,
			gs->connection.local_id);
		fflush(stdout);
		gs->send_logs++;
	}
	int w = WS_WRITE(gs->server_socket, (const uint8_t*)data, size, 0, 0x2);
	if (w <= 0)
	{
		printf("[NATIVE-AUTH-SEND-FAIL] w=%d errno=%d local_id=%d dropped_refreshable=%u last_drop=%c size=%d\n",
			w,
			errno,
			gs->connection.local_id,
			(unsigned)gs->dropped_refreshable_packets,
			gs->last_dropped_refreshable_token ? (char)gs->last_dropped_refreshable_token : '-',
			size);
		fflush(stdout);
		StopGameServerClientConnection();
		return false;
	}
	return true;
}

void Server::Proc()
{
	GameServer* gs = (GameServer*)this;
	int num = __atomic_load_n(&gs->msg_num, __ATOMIC_ACQUIRE);
	for (int i = 0; i < num; i++)
	{
		GameServer::MSG_FIFO* m = gs->msg + gs->msg_read;
        if (m->size<=0)
        {
            printf("[NATIVE-AUTH-RECV-CLOSE] size=%d errno=%d local_id=%d dropped_refreshable=%u last_drop=%c msg_num=%u\n",
                m->size,
                errno,
                gs->connection.local_id,
                (unsigned)gs->dropped_refreshable_packets,
                gs->last_dropped_refreshable_token ? (char)gs->last_dropped_refreshable_token : '-',
                (unsigned)num);
            fflush(stdout);
			StopGameServerClientConnection();
            return;
        }
		Server::Proc(m->data, m->size); // this would be called directly by JS
		gs->msg_read = (gs->msg_read + 1)&(GameServer::msg_size - 1);
	}

	INTERLOCKED_SUB(&gs->msg_num, num);
}

void Server::Log(const char* str)
{
    //printf("%s",str);
}

GameServer* Connect(const char* addr, const char* port, const char* path, const char* user)
{
	int iResult;

	// Initialize Winsock
	iResult = TCP_INIT();
	if (iResult != 0)
	{
		printf("WSAStartup failed: %d\n", iResult);
		return 0;
	}

	TCP_SOCKET server_socket = INVALID_TCP_SOCKET;

	const char* hostname = addr;
	const char* portname = port;
	struct addrinfo hints;
	memset(&hints, 0, sizeof(hints));
	hints.ai_family = AF_INET;
	hints.ai_socktype = SOCK_STREAM;
	hints.ai_protocol = IPPROTO_TCP;
	hints.ai_flags = AI_PASSIVE;
	struct addrinfo* result = 0;
	iResult = getaddrinfo(hostname, portname, &hints, &result);
	if (iResult != 0)
	{
		printf("getaddrinfo failed: %d\n", iResult);
		TCP_CLEANUP();
		return 0;
	}

	// socket create and varification 
	server_socket = socket(result->ai_family, result->ai_socktype, result->ai_protocol);
	if (server_socket == INVALID_TCP_SOCKET)
	{
		printf("socket creation failed...\n");
		TCP_CLEANUP();
		return 0;
	}
	else
		printf("Socket successfully created..\n");

	// connect the client socket to server socket 
	if (connect(server_socket, result->ai_addr, (int)result->ai_addrlen) != 0)
	{
		printf("connection with the server failed...\n");
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}
	else
		printf("connected to the server..\n");

    int optval = 1;
    if (setsockopt(server_socket, SOL_SOCKET, SO_KEEPALIVE, (const char*)&optval, sizeof(optval)) != 0)
    {
        // ok we can live without it
    }

	optval = 1;
	if (setsockopt(server_socket, IPPROTO_TCP, TCP_NODELAY, (const char*)&optval, sizeof(optval)) != 0)
	{
		// ok we can live without it
	}

	freeaddrinfo(result);

	// first, send HTTP->WS upgrade request (over http)
	const char* request_fmt =
		"GET /%s HTTP/1.1\r\n"
        "Host: %s\r\n"
#ifdef _WIN32
		"User-Agent: native-asciicker-windows\r\n"
#else
		"User-Agent: native-asciicker-linux\r\n"
#endif
		"Accept: */*\r\n"
		"Accept-Language: en-US,en;q=0.5\r\n"
		"Sec-WebSocket-Version: 13\r\n"
		"Sec-WebSocket-Key: btsPdKGunHdaTPnSSDlfow==\r\n"
		"Pragma: no-cache\r\n"
		"Cache-Control: no-cache\r\n"
		"Upgrade: WebSocket\r\n"
		"Connection: Upgrade\r\n\r\n";

    char request[2048];
    sprintf(request, request_fmt, path, addr);

	int w = TCP_WRITE(server_socket, (uint8_t*)request, (int)strlen(request));
	if (w < 0)
	{
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}

	// wait for response (check HTTP status / headers)
	struct Headers
	{
		static int cb(const char* header, const char* value, void* param)
		{
			Headers* h = (Headers*)param;

			if (header)
			{
				if (strcmp("Content-Length", header) == 0)
					h->content_len = atoi(value);
			}

			return 0;
		}

		int content_len;
	} headers;

	headers.content_len = 0;

	char buf[2048];
	int over_read = HTTP_READ(server_socket, Headers::cb, &headers, buf);

	if (over_read < 0 || over_read > headers.content_len || headers.content_len > 2048)
	{
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}

	while (headers.content_len > over_read)
	{
		int r = TCP_READ(server_socket, (uint8_t*)buf + over_read, headers.content_len - over_read);
		if (r <= 0)
		{
			TCP_CLOSE(server_socket);
			TCP_CLEANUP();
			return 0;
		}
		over_read += r;
	}

	// Server admission is V2-only: the contract hashes must be available before join.
	STRUCT_REQ_JOIN_V2 req_join = {};
	char join_v2_error[512] = {};
	if (!ReadJoinV2Contract(&req_join, user, join_v2_error, sizeof(join_v2_error)))
	{
		printf("[join-v2] contract metadata unavailable: %s\n",
			join_v2_error[0] ? join_v2_error : "unknown contract read failure");
		fflush(stdout);
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}
	int ws = WS_WRITE(server_socket, (uint8_t*)&req_join, sizeof(STRUCT_REQ_JOIN_V2), 0, 0x2);
	if (ws <= 0)
	{
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}

	// Recv for ID (over ws) (it can send us some content to display!)
	STRUCT_RSP_JOIN rsp_join = { 0 };
	ws = WS_READ(server_socket, (uint8_t*)&rsp_join, sizeof(STRUCT_RSP_JOIN), 0);
	if (ws <= 0 || rsp_join.token != 'n')
	{
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}

	int ID = rsp_join.id;
	printf("connected with ID:%d/%d\n", ID, rsp_join.maxcli);

	GameServer* gs = (GameServer*)calloc(1, sizeof(GameServer));
	if (!gs)
	{
		TCP_CLOSE(server_socket);
		TCP_CLEANUP();
		return 0;
	}
	gs->server_socket = server_socket;
	gs->connection.max_clients = rsp_join.maxcli;
	gs->connection.local_id = ID; // FL-164/FL-274: server-assigned slot id; must be set before Start()/Proc()

	return gs;
}

extern "C" void DumpLeakCounter();

#ifdef __linux__

#define KEY_MAX_LARGE 0x2FF
//#define KEY_MAX_SMALL 0x1FF
#define AXMAP_SIZE (ABS_MAX + 1)
#define BTNMAP_SIZE (KEY_MAX_LARGE - BTN_MISC + 1)

static uint16_t js_btnmap[BTNMAP_SIZE];
static uint8_t  js_axmap[AXMAP_SIZE];

static const int js_btnmap_sdl[]=
{
    0, 1, -1,    // A,B
    2, 3, -1,    // X,Y
    9,10, -1,-1, // L,R SHOULDERS
    4,6,5,       // BACK, START, GUIDE
    7,8, -1,     // L,R STICK
    -1,-1,-1,-1, -1,-1,-1,-1,
    -1,-1,-1,-1, -1,-1,-1,-1    
};

static const int js_axmap_sdl[]=
{
    0,1,4, // LEFT X,Y,TRIG
    2,3,5, // RIGHT X,Y,TRIG

    -1,-1,
    -1,-1,-1,-1, -1,-1,-1,-1,

    0xFE,0xFF, // X,Y AXIS FOR DIRPAD (left:13, right:14, up:11, down:12) !!!
    -1,-1, -1,-1,-1,-1, 
    -1,-1,-1,-1, -1,-1,-1,-1
};
#endif


int scan_js(char* gamepad_name, int* gamepad_axes, int* gamepad_buttons, uint8_t* gamepad_mapping)
{
    #ifdef __linux__
    static int index = 0;
    static int skip = 0;

    if (skip>0)
    {
        skip--;
        return -1;
    }

    char js_term_dev[32];
    sprintf(js_term_dev,"/dev/input/js%d",index);

    int fd = -1;
    if ((fd = open(js_term_dev, O_RDONLY)) < 0) 
    {
        // printf("can't open %s\n", js_term_dev);
        index = (index+1) & 0xF;
        skip = 10;
		fd = -1;
	}
    else
    {
        #define NAME_LENGTH 128
        unsigned char axes = 2;
        unsigned char buttons = 2;
        int version = 0x000800;
        char name[NAME_LENGTH] = "Unknown";

        ioctl(fd, JSIOCGVERSION, &version);
        ioctl(fd, JSIOCGAXES, &axes);
        ioctl(fd, JSIOCGBUTTONS, &buttons);
        ioctl(fd, JSIOCGNAME(NAME_LENGTH), name);

        strcpy(gamepad_name,name);
        *gamepad_axes = axes;
        *gamepad_buttons = buttons;

        // fetch button map
        memset(js_btnmap,-1,sizeof(js_btnmap));
        int btnmap_res = ioctl(fd, JSIOCGBTNMAP, js_btnmap);

        // fetch axis map
        memset(js_axmap,-1,sizeof(js_axmap));
        int axmap_res = ioctl(fd, JSIOCGAXMAP, js_axmap);


        // construct mapping
        uint8_t* m = gamepad_mapping;
        for (int i=0; i<buttons; i++)
        {
            int abs = 2*axes + i;
            switch(js_btnmap[i])
            {
                case BTN_A: m[abs] = (1<<7) | (0<<6) | 0x00; break;
                case BTN_B: m[abs] = (1<<7) | (0<<6) | 0x01; break;
                case BTN_X: m[abs] = (1<<7) | (0<<6) | 0x02; break;
                case BTN_Y: m[abs] = (1<<7) | (0<<6) | 0x03; break;

                case BTN_SELECT: m[abs] = (1<<7) | (0<<6) | 0x04/*back_button*/; break;
                case BTN_MODE:   m[abs] = (1<<7) | (0<<6) | 0x05/*guide_button*/; break;
                case BTN_START:  m[abs] = (1<<7) | (0<<6) | 0x06/*start_button*/; break;

                case BTN_THUMBL: m[abs] = (1<<7) | (0<<6) | 0x07/*left_stick_button*/; break;
                case BTN_THUMBR: m[abs] = (1<<7) | (0<<6) | 0x08/*right_stick_button*/; break;

                case BTN_TL: m[abs] = (1<<7) | (0<<6) | 0x09/*left_shoulder_button*/; break;
                case BTN_TR: m[abs] = (1<<7) | (0<<6) | 0x0A/*right_shoulder_button*/; break;

                case BTN_TL2: m[abs] = (0<<7) | (0<<6) | 0x02/*left_trigger_axis*/; break;
                case BTN_TR2: m[abs] = (0<<7) | (0<<6) | 0x05/*right_trigger_axis*/; break;

                default: 
                    m[i] = 0xFF;
            }
        }

        for (int i=0; i<axes; i++)
        {
            int neg = 2*i;
            int pos = 2*i+1;
            switch(js_axmap[i])
            {
                case 0: //left-x
                    m[neg] = (0<<7) | (1<<6) | 0x00; 
                    m[pos] = (0<<7) | (0<<6) | 0x00; 
                    break;

                case 1: //left-y
                    m[neg] = (0<<7) | (1<<6) | 0x01; 
                    m[pos] = (0<<7) | (0<<6) | 0x01; 
                    break;

                case 2: // left-z (compressed, 0x04 output is unsigned )
                    m[neg] = (0<<7) | (0<<6) | 0x04;
                    m[pos] = (0<<7) | (0<<6) | 0x04; 
                    break;

                case 3: //right-x
                    m[neg] = (0<<7) | (1<<6) | 0x02; 
                    m[pos] = (0<<7) | (0<<6) | 0x02; 
                    break;

                case 4: //right-y
                    m[neg] = (0<<7) | (1<<6) | 0x03; 
                    m[pos] = (0<<7) | (0<<6) | 0x03; 
                    break;

                case 5: //right-z (compressed, 0x05 output is unsigned )
                    m[neg] = (0<<7) | (0<<6) | 0x05; 
                    m[pos] = (0<<7) | (0<<6) | 0x05; 
                    break;

                case 16: // dirpad-x
                    m[neg] = (1<<7) | (0<<6) | 0x0D; 
                    m[pos] = (1<<7) | (0<<6) | 0x0E; 
                    break;

                case 17: // dirpad-y
                    m[neg] = (1<<7) | (0<<6) | 0x0B; 
                    m[pos] = (1<<7) | (0<<6) | 0x0C; 
                    break;

                default: 
                    m[i] = 0xFF;
            }
        }
    }

    return fd;
    #endif        

    return -1;    
}


bool read_js(int fd)
{
    #ifdef __linux__
        #define MAX_JS_READ 64
        js_event js_arr[MAX_JS_READ];
        int size = read(fd, js_arr, sizeof(js_event)*MAX_JS_READ);
        if (size<=0 || size % sizeof(js_event))
            return false;

        /*
        static int dirpad_x = 0;
        static int dirpad_y = 0;
        */

        int n = size / sizeof(js_event);
        for (int i=0; i<n; i++)
        {
            js_event* js = js_arr+i;

            // process
            switch(js->type & ~JS_EVENT_INIT) 
            {
                case JS_EVENT_BUTTON:
                    GamePadButton(js->number,js->value ? 32767 : 0);
                    break;
                case JS_EVENT_AXIS:
                    GamePadAxis(js->number,js->value == -32768 ? -32767 : js->value);
                    break;
            }
        }

        return true;
    #endif

    return false;
}

Game* game = 0;

void init_v8();
void free_v8();

uint64_t (*MakeStamp)() = 0;

// =============================================================================
// GameInputDispatch — thin adapter from InputDispatch to Game*
// =============================================================================
// Injected into GameInputSink inside TerminalGamePoll.  Keeps game_app.cpp as
// wiring-only: no input policy lives here, only pointer plumbing.

struct GameInputDispatch : InputDispatch
{
    Game* game = nullptr;
    explicit GameInputDispatch(Game* g = nullptr) : game(g) {}
    void SetGame(Game* g) { game = g; }

    void OnKeyb(int keyb_type, int key) override
    {
        if (game)
            game->OnKeyb(static_cast<GAME_KEYB>(keyb_type), key);
    }
    void OnMouse(int mouse_type, int x, int y) override
    {
        if (game)
            game->OnMouse(static_cast<GAME_MOUSE>(mouse_type), x, y);
    }
    void OnSize(int w, int h, int fw, int fh) override
    {
        if (game)
            game->OnSize(w, h, fw, fh);
    }
};

struct TerminalGamePoll final : HostPollInterface
{
    GameInputDispatch dispatch;
    GameInputSink input;

    bool Init() override
    {
        game = CreateGame();
        dispatch.SetGame(game);
        input.SetDispatch(&dispatch);
        return game != 0;
    }

    void Tick(uint64_t dt_us) override
    {
        // Advance input adapter clock first so hold expirations are
        // processed before server pumping and rendering.
        input.Tick(dt_us);
        // Current terminal Tick work is dt-agnostic server pumping only.
        (void)dt_us;
        if (server)
            server->Proc();
    }

    void Render(uint64_t stamp_us, AnsiCell* buf, int width, int height) override
    {
        if (game)
            game->Render(stamp_us, buf, width, height);
    }

    void Shutdown() override
    {
        // Host terminal resources are released by a3dRunPolling(); this hook owns
        // only game/runtime teardown after the host loop has finished polling.
        free_v8();
        akAPI_Free();

        if (terrain)
            DeleteTerrain(terrain);

        if (world)
            DeleteWorld(world);

        if (game)
        {
            FreeGame(game);
            DeleteGame(game);
            game = 0;
        }

        StopNormalGameAuthoritativeSession();
        PurgeItemInstCache();
        FreeSprites();
        FreeAudio();
    }
};


#define CODE(...) #__VA_ARGS__

int main(int argc, char* argv[])
{
    init_v8();
    
    akAPI_Exec(CODE(
    {
        // emulate emscripten heap view
        this.akAPI_Buff = 0;
        this.akAPI_This = {};
        
        this.Module = 
        {
            HEAPF64 : new Float64Array(akAPI_V8AB),
            HEAPF32 : new Float32Array(akAPI_V8AB),
            HEAPU32 : new Uint32Array(akAPI_V8AB),
            HEAP32  : new Int32Array(akAPI_V8AB),
            HEAPU16 : new Uint16Array(akAPI_V8AB),
            HEAP16  : new Int16Array(akAPI_V8AB),
            HEAPU8  : new Uint8Array(akAPI_V8AB),
            HEAP8   : new Int8Array(akAPI_V8AB),
        };
        
        this.UTF8ToString = function(ptr,len)
        {
            let arr = Module.HEAPU8;
            let endIdx = ptr+len;
            let idx = ptr;
            let str = '';
            while (!(idx >= endIdx)) 
            {
                let u0 = arr[idx++];
                if (!u0) 
                    return str;
                if (!(u0 & 0x80)) 
                { 
                    str += String.fromCharCode(u0); 
                    continue; 
                }
                let u1 = arr[idx++] & 63;
                if ((u0 & 0xE0) == 0xC0) 
                { 
                    str += String.fromCharCode(((u0 & 31) << 6) | u1); 
                    continue; 
                }
                let u2 = arr[idx++] & 63;
                if ((u0 & 0xF0) == 0xE0) 
                    u0 = ((u0 & 15) << 12) | (u1 << 6) | u2;
                else 
                    u0 = ((u0 & 7) << 18) | (u1 << 12) | (u2 << 6) | (arr[idx++] & 63);

                if (u0 < 0x10000) 
                    str += String.fromCharCode(u0);
                else 
                {
                    let ch = u0 - 0x10000;
                    str += String.fromCharCode(0xD800 | (ch >> 10), 0xDC00 | (ch & 0x3FF));
                }
            }
            return str;
        };

        this.stringToUTF8 = function(str,ptr,len)
        {
            if (!(len > 0))
                return 0;
            
            let arr = Module.HEAPU8;
            let startIdx = ptr;
            let endIdx = ptr + len -1;
            let idx = ptr;
            for (let i = 0; i < str.length; ++i) 
            {
                let u = str.charCodeAt(i); // possibly a lead surrogate
                if (u >= 0xD800 && u <= 0xDFFF) 
                {
                    let u1 = str.charCodeAt(++i);
                    u = 0x10000 + ((u & 0x3FF) << 10) | (u1 & 0x3FF);
                }
                if (u <= 0x7F) 
                {
                    if (idx >= endIdx) 
                        break;
                    arr[idx++] = u;
                } 
                else 
                if (u <= 0x7FF) 
                {
                    if (idx + 1 >= endIdx) 
                        break;
                    arr[idx++] = 0xC0 | (u >> 6);
                    arr[idx++] = 0x80 | (u & 63);
                }
                else 
                if (u <= 0xFFFF) 
                {
                    if (idx + 2 >= endIdx) 
                        break;
                    arr[idx++] = 0xE0 | (u >> 12);
                    arr[idx++] = 0x80 | ((u >> 6) & 63);
                    arr[idx++] = 0x80 | (u & 63);
                } 
                else 
                {
                    if (idx + 3 >= endIdx) 
                        break;

                    arr[idx++] = 0xF0 | (u >> 18);
                    arr[idx++] = 0x80 | ((u >> 12) & 63);
                    arr[idx++] = 0x80 | ((u >> 6) & 63);
                    arr[idx++] = 0x80 | (u & 63);
                }
            }

            arr[idx] = 0;
            return idx - startIdx;
        };
    }),-1,true);

    akAPI_Init();

    /*
	FILE* fpal = fopen("d:\\ascii-work\\asciicker.act", "wb");
	for (int i = 0; i < 16; i++)
	{
		uint8_t col[3] = { 0,0,0 };
		fwrite(col, 3, 1, fpal);
	}
	for (int r = 0; r < 6; r++)
	for (int g = 0; g < 6; g++)
	for (int b = 0; b < 6; b++)
	{
		uint8_t col[3] = { r*51,g*51,b*51 };
		fwrite(col, 3, 1, fpal);
	}
	for (int i = 0; i < 24; i++)
	{
		uint8_t col[3] = { 0,0,0 };
		fwrite(col, 3, 1, fpal);
	}
	return 0;
	*/

    char abs_buf[PATH_MAX];
    char* abs_path = 0;

    if (argc < 1)
        strcpy(base_path,"./");
    else
    {
        size_t len = 0;
        #if defined(__linux__) || defined(__APPLE__)
        abs_path = realpath(argv[0], abs_buf);
        char* last_slash = strrchr(abs_path, '/');
        if (!last_slash)
            strcpy(base_path,"./");
        else
        {
            len = last_slash - abs_path + 1;
            memcpy(base_path,abs_path,len);
            base_path[len] = 0;
        }
        #else
        GetFullPathNameA(argv[0],1024,abs_buf,&abs_path);
		memcpy(base_path, abs_buf, abs_path - abs_buf);
		#endif

        len = strlen(base_path);

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

    printf("exec path: %s\n", argv[0]);
    printf("BASE PATH: %s\n", base_path);

	for (int p = 1; p < argc; p++)
	{
		if (strcmp(argv[p], "--fl4131-native-material-sidecar-proof") == 0)
		{
			InitMaterials();
			return RunFL4131NativeMaterialSidecarProof() ? 0 : 1;
		}
	}

	InitAudio();
	InitMaterials();  // Initialize terrain material definitions

    // TODO:
    // REFACTOR ME SUCH TERM IS INITIALIZED BEFORE
    // CONNECTING TO SERVER AND LOADING SPRITES & WORLD!
    // this is really needed for driving intro / main manu

    /*
    int c16 = 13;
    printf("\x1B[%d;%dm%s",(c16&7)+40,c16<8?25:5,"\n");

    for (int b=0; b<6; b++)
    {
        for (int r=0; r<6; r++)
        {
            for (int g=0; g<6; g++)
            {
                int c256 = b + 6*g + 36*r + 16;
                printf("\e]P%X%02x%02x%02x", c16, pal_rgba[c256][0], pal_rgba[c256][1], pal_rgba[c256][2]);
                printf(" ");
            }
        }
        printf("\n");
    }

    exit(0);
    */



#ifdef _WIN32
	
	PostMessage(GetConsoleWindow(), WM_SYSCOMMAND, SC_MINIMIZE, 0);
	//_CrtSetBreakAlloc(5971);
#endif

    // if there is no '-term' arg given run A3D (GL) term
    // ...

    // otherwise continue with following crap
    // ...

    // consider configuring kbd, it solves only key up, not multiple keys down
    /*
        gsettings set org.gnome.desktop.peripherals.keyboard repeat-interval 30
        gsettings set org.gnome.desktop.peripherals.keyboard delay 250    
    */

	// if -user is given get its name and connect to server !


	char* url = 0; // must be in form [user@]server_address/path[:port]
	char* map_path = 0;
	char* observe_render_output_dir = 0;
	char* observe_render_view_tuple = 0;
	char* observe_render_schema_version = 0;
	bool fl4131_native_material_sidecar_proof = false;

	// to be upgraded by host (good if no encryption is needed but can't connect on weird port)
	/*
	const char* user = "player";
	const char* addr = "asciicker.com";
	const char* path = "/ws/y8/";
	const char* port = "80";
	*/

	// to be upgraded by host (good if encryption is needed)
	/*
	const char* user = "player";
	const char* addr = "asciicker.com";
	const char* path = "/ws/y8/";
	const char* port = "443";
	*/

	// directly (best but no encryption and requires weird port to be allowed to send request to)
	/*
	const char* user = "player";
	const char* addr = "asciicker.com";
	const char* path = "/ws/y8/"; // just to check if same as server expects
	const char* port = "8080";
	*/

    bool term = false;
    for (int p=1; p<argc; p++)
    {
        if (strcmp(argv[p],"-term")==0)
            term = true;
		else if (strcmp(argv[p], "--fl4131-native-material-sidecar-proof") == 0)
			fl4131_native_material_sidecar_proof = true;
		else if (strcmp(argv[p], "--observe-render") == 0 && p + 1 < argc)
		{
			p++;
			observe_render_output_dir = argv[p];
		}
		else if (strcmp(argv[p], "--view-tuple") == 0 && p + 1 < argc)
		{
			p++;
			observe_render_view_tuple = argv[p];
		}
		else if (strcmp(argv[p], "--schema-version") == 0 && p + 1 < argc)
		{
			p++;
			observe_render_schema_version = argv[p];
		}
		else if ((strcmp(argv[p], "-map") == 0 || strcmp(argv[p], "--map") == 0) && p+1<argc)
		{
			p++;
			map_path = argv[p];
		}
		else if (argv[p][0] != '-' && strstr(argv[p], ".a3d"))
		{
			map_path = argv[p];
		}
		else if (p+1<argc)
		{
			if (strcmp(argv[p], "-url") == 0)
			{
				p++;
				url = argv[p];
			}
		}
	}

	if (fl4131_native_material_sidecar_proof)
		return RunFL4131NativeMaterialSidecarProof() ? 0 : 1;

	ConfigureObserveRender(observe_render_output_dir, observe_render_view_tuple, observe_render_schema_version);

	if (map_path)
	{
		strncpy(g_requested_a3d_path, map_path, sizeof(g_requested_a3d_path) - 1);
		g_requested_a3d_path[sizeof(g_requested_a3d_path) - 1] = 0;
		printf("MAP PATH: %s\n", g_requested_a3d_path);
	}
	else
	{
		g_requested_a3d_path[0] = 0;
	}

	// NET_TODO:
	// if url is given try to open connection
	GameServer* gs = 0;

	if (url)
	{
		// [user@]server_address/path[:port]
		char* monkey = strchr(url, '@');
		char* colon = monkey ? strchr(monkey, ':') : strchr(url, ':');
        char* slash = colon ? strchr(colon, '/') : monkey ? strchr(monkey, '/') : strchr(url, '/');

        char def_user[] = "player";
        char def_port[] = "8080";
        char def_path[] = "";

		char* addr = url;
		char* user = def_user;
		char* port = def_port;
		char* path = def_path;

		if (monkey)
		{
			*monkey = 0;
			user = url;
			addr = monkey + 1;
		}

		if (colon)
		{
			*colon = 0;
			port = colon + 1;
		}

        if (slash)
        {
            *slash = 0;
            path = slash + 1;
        }

        if (addr && addr[0])
        {
            gs = Connect(addr, port, path, user);
            if (!gs)
            {
                printf("Couldn't connect to server, starting solo ...\n");
            }
        }

		strcpy(player_name, user);
	        ConvertToCP437(player_name_cp437, player_name, (int)sizeof(player_name_cp437));

		// here we should know if server is present or not
		// so we can creare game or term with or without server
		// ...
	}
    else
    {
        strcpy(player_name, "player");
	        ConvertToCP437(player_name_cp437, player_name, (int)sizeof(player_name_cp437));
    }
    

    float water = 55;
    float dir = 0;

    float yaw = 45;
    float pos[3] = {0,15,0};
    float lt[4] = {1,0,1,.5};
    GetDefaultGameStart(&water, pos, &yaw, &dir, lt);

	float last_yaw = yaw;

	LoadSprites();


	if (gs)
	{
		server = gs;

		if (!gs->Start())
		{
			TCP_CLEANUP();
            FreeAudio();
			return false;
		}
	}

    #ifndef PURE_TERM
    if (!term)
    {
        probe_z = (int)water;

        pos_x = pos[0];
        pos_y = pos[1];
        pos_z = pos[2];
        rot_yaw = yaw;

        global_lt[0] = lt[0];
        global_lt[1] = lt[1];
        global_lt[2] = lt[2];
        global_lt[3] = lt[3];

        game = TermOpen(0, yaw, pos, MyFont::Free);

        MakeStamp = a3dGetTime;

        if (game)
        {
            char font_dirname[1024+10];
            sprintf(font_dirname, "%sassets/fonts", base_path); // = "./assets/fonts";
            fonts_loaded = 0;
            a3dListDir(font_dirname, MyFont::Scan, font_dirname);

			LoopInterface li = { GamePadMount, GamePadUnmount, GamePadButton, GamePadAxis };
            a3dLoop(&li);
        }

		// NET_TODO:
		// close network if open
		// ...

        if (terrain)
            DeleteTerrain(terrain);

        if (world)
            DeleteWorld(world);

		PurgeItemInstCache();

		FreeSprites();

		DumpLeakCounter();

#ifdef _WIN32
		_CrtDumpMemoryLeaks();
#endif
        FreeAudio();
        return 0;
    }

#endif // #ifndef PURE_TERM

#if defined(PURE_TERM) && (defined(__linux__) || defined(__APPLE__))

    // LIGHT
    {
        float lit_yaw = 45;
        float lit_pitch = 30;//90;
        float lit_time = 12.0f;
        float ambience = 0.5;

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

        lt[0] = (float)lit_pos[0];
        lt[1] = (float)lit_pos[1];
        lt[2] = (float)lit_pos[2];
        lt[3] = ambience;    
    }

    probe_z = (int)water;
    pos_x = pos[0];
    pos_y = pos[1];
    pos_z = pos[2];
    rot_yaw = yaw;
    global_lt[0] = lt[0];
    global_lt[1] = lt[1];
    global_lt[2] = lt[2];
    global_lt[3] = lt[3];

    TerminalGamePoll term_poll;
    a3dRunPolling(&term_poll, &term_poll.input);
    return 0;

#else

    printf("Currently -term parameter is unsupported on this build\n");

#endif

	PurgeItemInstCache();

	FreeSprites();

#ifdef _WIN32
	_CrtDumpMemoryLeaks();
#endif

    FreeAudio();

	return 0;
}

//////////////////////////////////////////////////

v8::Isolate* isolate = 0;
std::unique_ptr<v8::Platform> platform = 0;
v8::ArrayBuffer::Allocator* array_buffer_allocator = 0;

// Extracts a C string from a V8 Utf8Value.
const char* ToCString(const v8::String::Utf8Value& value) {
    return *value ? *value : "<string conversion failed>";
}

void akAPI_CallV8(const v8::FunctionCallbackInfo<v8::Value>& args/*id*/) 
{
    if (args.Length() != 1 || !args[0]->IsInt32())
    {
        printf("%s problem\n", __FUNCTION__);
        return;
    }
    v8::Isolate* isolate = args.GetIsolate();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    akAPI_Call(args[0]->Int32Value(context).ToChecked());
}

void akPrint(const v8::FunctionCallbackInfo<v8::Value>& args) 
{
    bool first = true;
    for (int i = 0; i < args.Length(); i++) 
    {
        v8::HandleScope handle_scope(args.GetIsolate());
        if (first) 
        {
            first = false;
        }
        else 
        {
            printf(" ");
        }
        v8::String::Utf8Value str(args.GetIsolate(), args[i]);
        const char* cstr = ToCString(str);
        printf("%s", cstr);
    }
    printf("\n");
    fflush(stdout);
}

void akAPI_CB(int id)
{
    uint64_t t0 = GetTime();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    // defined by akAPI_Init 
    v8::Local<v8::String> cb_key = v8::String::NewFromUtf8Literal(isolate, "akAPI_CB");

    v8::Local<v8::Value> cb_val;
    bool ok = context->Global()->Get(context, cb_key).ToLocal(&cb_val);
    v8::Local<v8::Function> cb_fnc = cb_val.As<v8::Function>();

    v8::Local<v8::Value> id_val = v8::Int32::New(isolate,id);

    // invoke
    v8::Local<v8::Value> recv;
    v8::TryCatch trycatch(isolate);
    v8::MaybeLocal<v8::Value> result = cb_fnc->Call(context, context->Global(), 1, &id_val);
    if (result.IsEmpty())
    {
        v8::Local<v8::Value> exception = trycatch.Exception();
        v8::String::Utf8Value exception_str(isolate,exception);
        printf("Exception: %s\n", *exception_str);          
    }

    uint64_t t1 = GetTime();
    //printf("CALLBACK in %d us\n", (int)(t1-t0));
}

void free_v8()
{
    {
        v8::HandleScope handle_scope(isolate);
        v8::Local<v8::Context> context = isolate->GetCurrentContext();
        context->Exit();
    }
    isolate->Exit();

    akAPI_Buff = 0;

    // Dispose the isolate and tear down V8.
    isolate->Dispose();
    v8::V8::Dispose();

    v8::V8::DisposePlatform();

    if (array_buffer_allocator)
        delete array_buffer_allocator;
    array_buffer_allocator = 0;

    printf("V8 DISPOSED.\n");
}

void init_v8()
{

#ifdef V8_INTL_SUPPORT
    assert(!"V8_INTL_SUPPORT")
#endif

#ifdef V8_USE_EXTERNAL_STARTUP_DATA
        assert(!"V8_USE_EXTERNAL_STARTUP_DATA")
#endif

        // our own v8_monolith build should be compiled 
        // w/o i18n and esd
        // v8::V8::InitializeICUDefaultLocation(argv[0]);
        // v8::V8::InitializeExternalStartupData(argv[0]);

        platform = v8::platform::NewDefaultPlatform();
    v8::V8::InitializePlatform(platform.get());
    v8::V8::Initialize();

    printf("INITIALIZED V8 %s\n", v8::V8::GetVersion());

    // Create a new Isolate and make it the current one.
    array_buffer_allocator = v8::ArrayBuffer::Allocator::NewDefaultAllocator();
    v8::Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = array_buffer_allocator;

    isolate = v8::Isolate::New(create_params);
    isolate->Enter(); // v8::Isolate::Scope isolate_scope(isolate);

    v8::HandleScope handle_scope(isolate);

    v8::Local<v8::ObjectTemplate> global_templ = v8::ObjectTemplate::New(isolate);
    
    global_templ->Set(isolate, "akPrint", v8::FunctionTemplate::New(isolate, akPrint));
    global_templ->Set(isolate, "akAPI_Call", v8::FunctionTemplate::New(isolate, akAPI_CallV8));

    v8::Local<v8::Context> context = v8::Context::New(isolate, nullptr, global_templ);
    context->Enter(); // v8::Context::Scope context_scope(context);

    v8::Local<v8::ArrayBuffer> arrbuf = v8::ArrayBuffer::New(isolate, AKAPI_BUF_SIZE);
    akAPI_Buff = arrbuf->Data();
    memset(akAPI_Buff,0,AKAPI_BUF_SIZE);

    v8::Local<v8::String> key = v8::String::NewFromUtf8Literal(isolate,"akAPI_V8AB");
    v8::Maybe<bool> ok = context->Global()->Set(context, key, arrbuf);
    assert(ok.ToChecked());
}

void akAPI_Exec(const char* str, int len, bool root)
{
    uint64_t t0 = GetTime();
    char* buf = 0;
    if (!root)
    {
        // lets isolate custom code from polluting with vars and lets
        // they should declare variables using this.variable=something;

        // (function(){...}())
        // ^-extra parenthesis makes expression
        //   instead of a statement (which would require a function name)

        // we should also hide all things except "ak" and "akPrint"

        static const char* prefix = 
        "(function("
            "ak,akPrint," // pass only these 2
            /*
            "akAPI_Back,"
            "akAPI_Buff,"
            "akAPI_This,"
            "akAPI_CB,"
            "Module,"
            "UTF8ToString,"
            "stringToUTF8,"
            "akGetF32,"
            "akSetF32,"
            "akReadF32,"
            "akWriteF32,"
            "akGetI32,"
            "akSetI32,"
            "akReadI32,"
            "akWriteI32,"
            "akGetStr,"
            "akSetStr"
            */
        "){";
        static const char* suffix = "}.apply(akAPI_This,[ak,akPrint]))";
        static const int prefix_len = strlen(prefix);
        static const int suffix_len = strlen(suffix);
        buf = (char*)malloc(prefix_len+len+suffix_len+1);
        memcpy(buf,prefix,prefix_len);
        memcpy(buf+prefix_len,str,len);
        memcpy(buf+prefix_len+len,suffix,suffix_len+1);
        len += prefix_len + suffix_len;
        str = buf;
    }

    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    // Create a string containing the JavaScript source code.
    v8::MaybeLocal<v8::String> source = v8::String::NewFromUtf8(isolate, str, v8::NewStringType::kNormal, len);

    // Compile the source code.
    v8::TryCatch trycatch(isolate);
    v8::MaybeLocal<v8::Script> script = v8::Script::Compile(context, source.ToLocalChecked());

    if (!script.IsEmpty())
    {
        // Run the script to get the result.
        v8::MaybeLocal<v8::Value> result = script.ToLocalChecked()->Run(context);

        if (result.IsEmpty())
        {
            v8::Local<v8::Value> exception = trycatch.Exception();
            v8::String::Utf8Value exception_str(isolate,exception);
            printf("Exception: %s\n", *exception_str);            
        }
        else
        {
            /*
            v8::String::Utf8Value utf8(isolate, result.ToLocalChecked()->ToString(context).ToLocalChecked());
            printf("string %s\n", *utf8);
            */
        }
    }
    else
    {
        v8::Local<v8::Value> exception = trycatch.Exception();
        v8::String::Utf8Value exception_str(isolate,exception);
        printf("Exception: %s\n", *exception_str);
    }

    if (buf)
        free(buf);

    uint64_t t1 = GetTime();
    //printf("COMPILE+EXECUTE IN %dus\n",(int)(t1-t0));
}

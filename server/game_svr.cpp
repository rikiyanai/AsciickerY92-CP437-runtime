// game_svr.cpp - Headless Server Entry Point
//
// PURPOSE: Headless dedicated server for multiplayer game hosting. Runs game logic without
// rendering, JavaScript scripting, or local input. Processes network messages, updates world
// state, and broadcasts state to connected clients.
//
// PLATFORM-SPECIFIC FEATURES:
// - Headless: No rendering, no GPU, no display (server-only environment)
// - No JavaScript: No V8 engine, no NPC scripting (game logic only)
// - Network-Only Input: Player actions received via network messages, no local keyboard/mouse
// - Tick-Based Loop: Server tick loop processes network messages, updates physics, broadcasts state
// - Native Sockets: Direct BSD sockets API for TCP/UDP networking (vs WebSocket on web)
//
// INITIALIZATION ORDER:
// 1. Parse command-line arguments (--port, --world, --max-players, etc.)
// 2. Load world data from filesystem (.a3d world file, no sprites/meshes needed)
// 3. Initialize physics (collision detection, no rendering)
// 4. Bind network socket, start listening for connections
// 5. Run server loop: accept connections → process messages → update world → broadcast state
//
// MAIN LOOP PATTERN:
// - while(1) { poll_network(); process_messages(); update_physics(); broadcast_state(); }
// - WHY TICK-BASED: Server updates at fixed rate (e.g., 20 Hz) independent of client frame rate
// - CONTRAST NATIVE: Native uses 60 FPS render loop driven by vsync
// - CONTRAST WEB: Web uses requestAnimationFrame driven by browser
//
// SERVER MODEL:
// - Connection Management: Accept new clients, track active connections, handle disconnects
// - Player Join/Leave: Assign player IDs, spawn characters, remove on disconnect
// - State Broadcasting: Send world state delta to all clients (position, health, actions)
// - Input Processing: Receive client input (movement, actions), validate, apply to game state
// - WHY AUTHORITATIVE: Server validates all actions to prevent cheating (client is untrusted)
//
// NETWORK PROTOCOL:
// - Message format: WebSocket binary packets queued through ClientIO::InMsg
// - Connection: TCP for reliability (player actions, world state)
// - Broadcasting: Send to all connected clients (multicast)
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
// - main(): Parse args, load world, bind socket, run server loop (not yet implemented - stubs only)
// - Server::Send(): Send packet to client via native sockets (stub returns false)
// - Server::Proc(): Process server tick (stub - no-op)
// - akAPI_Exec(): JavaScript execution (stub - no V8 on server)
// - Buzz(), SyncConf(): Platform stubs (no haptics, no config sync)
//
// KEY FILES:
// - game.cpp: Shared game logic (physics, world update) - platform-independent
// - network.cpp: Network protocol implementation (packet serialization, connection management)
// - world.cpp: BSP tree world data structure
// - game_app.cpp: Native desktop entry point (cross-reference for differences)
// - game_web.cpp: WebAssembly entry point (cross-reference for differences)
//
// CROSS-REFERENCES:
// - See game_app.cpp header for native desktop platform differences (V8, rendering, input)
// - See game_web.cpp header for WebAssembly platform differences (browser integration)
// - See network.cpp for multiplayer protocol details
//
// IMPLEMENTATION STATUS: Authoritative server with 3-thread architecture.
// Accept thread: connection acceptance + WS handshake
// IO thread: multiplexed recv/send via poll()
// Tick thread: 30Hz authoritative game state loop

#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <time.h>

#include "terrain.h"
#include "world.h"
#include "render.h"
#include "game.h"
#include "network.h"
#include "server_state.h"
#include "sprite_registry.h"

#if defined(__linux__) || defined(__APPLE__)
#ifdef __linux__
#include <linux/limits.h>
#else
#include <limits.h>
#endif

// work around including <netinet/tcp.h>
// which also defines TCP_CLOSE
#ifndef TCP_DELAY
#define TCP_NODELAY 1
#endif

#else
#define PATH_MAX 1024
#endif

// base_path: server entry point owns this global (client builds: game_app.cpp owns it).
char base_path[1024] = "./";

#define MAX_CLIENTS 50
int max_players = 5; // runtime cap, configurable via --max-players

static bool EnvFlagEnabled(const char* name, bool default_value)
{
	const char* raw = getenv(name);
	if (!raw || !raw[0])
		return default_value;
	if (strcmp(raw, "0") == 0 || strcasecmp(raw, "false") == 0 || strcasecmp(raw, "off") == 0 || strcasecmp(raw, "no") == 0)
		return false;
	if (strcmp(raw, "1") == 0 || strcasecmp(raw, "true") == 0 || strcasecmp(raw, "on") == 0 || strcasecmp(raw, "yes") == 0)
		return true;
	return true;
}

static int EnvIntClamped(const char* name, int default_value, int min_value, int max_value)
{
	const char* raw = getenv(name);
	if (!raw || !raw[0])
		return default_value;
	char* end = nullptr;
	long parsed = strtol(raw, &end, 10);
	if (!end || end == raw || *end != '\0')
		return default_value;
	if (parsed < (long)min_value)
		return min_value;
	if (parsed > (long)max_value)
		return max_value;
	return (int)parsed;
}

// JavaScript execution stub.
// WHY STUB: Headless server has no JavaScript engine (no V8, no browser JS).
// Game logic runs in C++ only. NPC scripting not available on server.
void akAPI_Exec(const char* str, int len, bool root)
{
}

// Signal handler for graceful shutdown.
// WHY: SIGINT (Ctrl+C) should save world state, close connections cleanly.
void exit_handler(int sig)
{
	extern volatile bool isRunning;
	__atomic_store_n(&isRunning, false, __ATOMIC_RELEASE);
	printf("\nSignal %d received, shutting down...\n", sig);
}

// Haptic feedback stub.
// WHY STUB: Headless server has no haptic output (no gamepad, no display).
void Buzz()
{
}

// Configuration sync stub.
// WHY STUB: Server config writes are synchronous to native filesystem (no IndexedDB sync needed).
extern "C" void SyncConf()
{
}

// Get configuration file path.
// WHY PLATFORM-SPECIFIC: Server uses current directory (no user home, no virtual filesystem).
extern "C" const char* GetConfPath()
{
    return "asciicker.cfg";
}

// ---------------------------------------------------------------------------
// Globals defined in game_app.cpp — server needs its own definitions
// ---------------------------------------------------------------------------
Game* game = 0;
uint64_t server_make_stamp();
uint64_t (*MakeStamp)() = server_make_stamp;

uint64_t server_make_stamp()
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

// a3dGetTime() — normally in sdl.cpp (not compiled into server)
// Returns microseconds from CLOCK_MONOTONIC (same as sdl.cpp:1281)
uint64_t a3dGetTime()
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

// ---------------------------------------------------------------------------
// Weather stubs — server is headless, no particle rendering
// ---------------------------------------------------------------------------
#include "weather.h"
Weather* weather = 0;

Weather* CreateWeather() { return 0; }
void DeleteWeather(Weather*) {}
void SetWeather(int) {}
int GetWeather() { return 0; }
void UpdateWeather(uint64_t, float, float, float) {}
void CompositeSnowParticles(Weather*, AnsiCell*, int, int, Renderer*, uint64_t) {}
void UpdateSnowAccumulation(Weather*, Terrain*, uint64_t) {}

// Linker stubs — deprecated API stubs, no longer called by tick pipeline.
bool Server::Send(const uint8_t* data, int size) { return false; }
void Server::Proc() {}

// Log server message with timestamp prefix.
void Server::Log(const char* str)
{
	time_t now = time(NULL);
	struct tm* t = localtime(&now);
	char ts[32];
	strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", t);
	printf("[%s] %s\n", ts, str);
}

extern "C" void SHA1(void* data, int len, unsigned char digest[20]);

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

volatile bool isRunning = true;

// ═══════════════════════════════════════════════════════════════════
// NETWORK SETUP
// ═══════════════════════════════════════════════════════════════════

static bool GetListenFdFromEnv(TCP_SOCKET* out_fd)
{
	if (!out_fd)
		return false;
	*out_fd = INVALID_TCP_SOCKET;
	const char* fd_env = getenv("ASCIICKER_LISTEN_FD");
	if (!fd_env || !fd_env[0])
		return false;

	char* end = 0;
	long fd = strtol(fd_env, &end, 10);
	if (end == fd_env || *end != '\0')
		return false;
	if (fd < 0 || fd > 0x7fffffffL)
		return false;
	*out_fd = (TCP_SOCKET)fd;
	return true;
}

static bool GetListenSocketPort(TCP_SOCKET fd, uint16_t* out_port)
{
	if (!out_port || fd == INVALID_TCP_SOCKET)
		return false;
	struct sockaddr_in addr;
	socklen_t len = (socklen_t)sizeof(addr);
	memset(&addr, 0, sizeof(addr));
	if (getsockname(fd, (struct sockaddr*)&addr, &len) < 0)
		return false;
	if (addr.sin_family != AF_INET)
		return false;
	if (addr.sin_addr.s_addr != htonl(INADDR_LOOPBACK))
		return false;
	*out_port = (uint16_t)ntohs(addr.sin_port);
	if (*out_port == 0)
		return false;
	return true;
}

static TCP_SOCKET SetupListenSocket(const char* port)
{
	int iResult = TCP_INIT();
	if (iResult != 0)
	{
		printf("TCP_INIT failed: %d\n", iResult);
		return INVALID_TCP_SOCKET;
	}

	struct addrinfo *result = NULL, hints;
	memset(&hints, 0, sizeof(hints));
	hints.ai_family = AF_INET;
	hints.ai_socktype = SOCK_STREAM;
	hints.ai_protocol = IPPROTO_TCP;
	hints.ai_flags = AI_PASSIVE;

	iResult = getaddrinfo(NULL, port, &hints, &result);
	if (iResult != 0)
	{
		printf("getaddrinfo failed: %d\n", iResult);
		TCP_CLEANUP();
		return INVALID_TCP_SOCKET;
	}

	TCP_SOCKET s = socket(result->ai_family, result->ai_socktype, result->ai_protocol);
	if (s == INVALID_TCP_SOCKET)
	{
		freeaddrinfo(result);
		TCP_CLEANUP();
		return INVALID_TCP_SOCKET;
	}

	#ifndef _WIN32
	int optval = 1;
	setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char*)&optval, sizeof(optval));
	#endif

	iResult = bind(s, result->ai_addr, (int)result->ai_addrlen);
	freeaddrinfo(result);
	if (iResult < 0)
	{
		// FL-4137: This errno is required to distinguish "headed proof never
		// reached gameplay" from a real block collision/stand-on failure. The
		// Playwright harness can hit errno=1 under sandboxed child-process
		// launches; do not collapse that into gameplay proof status.
		printf("bind failed: errno=%d (%s) port=%s\n", errno, strerror(errno), port ? port : "(null)");
		TCP_CLOSE(s);
		TCP_CLEANUP();
		return INVALID_TCP_SOCKET;
	}

	if (listen(s, SOMAXCONN) < 0)
	{
		TCP_CLOSE(s);
		TCP_CLEANUP();
		return INVALID_TCP_SOCKET;
	}

	return s;
}


// terrain and world: server entry point owns these globals (client builds: game_app.cpp owns them).
Terrain* terrain = 0;
World* world = 0;
Material mat[256];
void* GetMaterialArr()
{
	return mat;
}

// Global server state (heap-allocated due to size: ~20MB)
static ServerState* g_state = NULL;

static bool IsAbsoluteA3DPath(const char* path)
{
	if (!path || !path[0])
		return false;
	if (path[0] == '/' || path[0] == '\\')
		return true;
#ifdef _WIN32
	if (((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z'))
		&& path[1] == ':'
		&& (path[2] == '\\' || path[2] == '/'))
		return true;
#endif
	return false;
}

int main(int argc, char* argv[])
{
    // Parse CLI args: --port N, --max-players N, --map/--world FILE
    const char* port = "8080";
    const char* map_path = 0;
    for (int i = 1; i < argc; i++)
    {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc)
        {
            port = argv[++i];
        }
        else if (strcmp(argv[i], "--max-players") == 0 && i + 1 < argc)
        {
            max_players = atoi(argv[++i]);
            if (max_players < 1) max_players = 1;
            if (max_players > SVR_MAX_CLIENTS) max_players = SVR_MAX_CLIENTS;
        }
        else if ((strcmp(argv[i], "--map") == 0 || strcmp(argv[i], "--world") == 0) && i + 1 < argc)
        {
            map_path = argv[++i];
        }
        else if (argv[i][0] != '-' && strstr(argv[i], ".a3d"))
        {
            map_path = argv[i];
        }
        else if (strcmp(argv[i], "--help") == 0)
        {
            printf("Usage: server [--port N] [--max-players N] [--map FILE]\n");
            printf("  --port N          Listen port (default: 8080)\n");
            printf("  --max-players N   Max concurrent players (default: 5, max: %d)\n", SVR_MAX_CLIENTS);
            printf("  --map FILE        A3D map to load (default: assets/a3d/game_map_y8.a3d)\n");
            printf("  --world FILE      Alias for --map\n");
            return 0;
        }
    }

    // Register signal handlers for graceful shutdown
#if defined(__linux__) || defined(__APPLE__)
    signal(SIGINT, exit_handler);
    signal(SIGTERM, exit_handler);
#endif

    printf("Asciicker Authoritative Server starting (port=%s, max_players=%d)\n", port, max_players);
    fflush(stdout);

    char abs_buf[PATH_MAX];
    char* abs_path = 0;

    if (argc > 1 && argv[1][0] != '-')
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

    printf("exec path: %s\n", argv[0]);
    printf("BASE PATH: %s\n", base_path);


	LoadSprites();

	char a3d_path[1024+20];
	if (map_path && map_path[0])
	{
		if (IsAbsoluteA3DPath(map_path))
			snprintf(a3d_path, sizeof(a3d_path), "%s", map_path);
		else
			snprintf(a3d_path, sizeof(a3d_path), "%s%s", base_path, map_path);
	}
	else
	{
		snprintf(a3d_path, sizeof(a3d_path), "%sassets/a3d/game_map_y8.a3d", base_path); // P5: a3d/ moved to assets/a3d/
	}
	printf("MAP PATH: %s\n", a3d_path);
	FreeMinimapMarkers();
	FILE* f = fopen(a3d_path, "rb");

	if (f)
	{
		terrain = LoadTerrain(f);

		if (terrain)
		{
			for (int i = 0; i < 256; i++)
			{
				if (fread(mat[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
					break;
			}

			world = LoadWorldRuntime(f);
			if (world)
			{
				// Load NPC spawn points (same FILE*, sequential after LoadWorld)
				LoadEnemyGens(f);
				LoadMinimapMarkers(f);

				// Reload meshes
				Mesh* m = GetFirstMesh(world);
				while (m)
				{
					char mesh_name[256];
					GetMeshName(m, mesh_name, 256);
					char obj_path[4096];
					ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
					if (!UpdateMesh(m, obj_path))
					{
						// missing mesh file
					}
					m = GetNextMesh(m);
				}
			}
		}

		fclose(f);
	}

	if (world)
		RebuildWorld(world, true);

	// ── Initialize authoritative server state ─────────────────────
	g_state = (ServerState*)calloc(1, sizeof(ServerState));
	if (!g_state)
	{
		printf("FATAL: Failed to allocate ServerState (%zu bytes)\n", sizeof(ServerState));
		return 1;
	}
	SvrStateInit(g_state, terrain, world);
	g_state->debug_runtime_diagnostics_enabled = EnvFlagEnabled("ASCIICKER_DEBUG_RUNTIME_DIAGNOSTICS", false);
	// Law 7: fly-mode input is only accepted in explicit debug/dev lanes (FL-1760 / RQ-029).
	g_state->debug_fly_mode_enabled = EnvFlagEnabled("ASCIICKER_DEBUG_FLY_MODE", false);
	g_state->authoritative_publish_interval_ticks = (uint32_t)EnvIntClamped("ASCIICKER_AUTH_PUBLISH_INTERVAL", 10, 1, 600);
	// Retire any stale authoritative-state artifact before startup.
	remove("/dev/shm/asciicker-authoritative_state.json");
	remove("/dev/shm/asciicker-authoritative_state.json.tmp");
	remove(".web/authoritative_state.json");
	remove(".web/authoritative_state.json.tmp");
	char appearance_contract_error[256] = {};
	if (!SvrLoadStartupAppearanceContract(g_state, appearance_contract_error, sizeof(appearance_contract_error)))
	{
		printf("FATAL: appearance bundle contract load failed: %s\n",
			appearance_contract_error[0] ? appearance_contract_error : "unknown error");
		free(g_state);
		return 1;
	}
	if (g_state->debug_runtime_diagnostics_enabled)
	{
		printf("SERVER runtime diagnostics enabled\n");
		fflush(stdout);
	}
	printf("SERVER authoritative publish interval=%u tick(s)\n", (unsigned)g_state->authoritative_publish_interval_ticks);
	fflush(stdout);
	printf("SERVER appearance contract version=%u bundle_hash=%.12s ids_lock_hash=%.12s\n",
		(unsigned)g_state->appearance_contract.contract_version,
		g_state->appearance_contract.bundle_hash,
		g_state->appearance_contract.ids_lock_hash);
	fflush(stdout);
	printf("SERVER authoritative state path=%s\n", ".web/authoritative_state.json");
	fflush(stdout);

	// Initialize NPCs from EnemyGen spawn points
	SvrInitNpcs(g_state);
	SvrInitWorldItems(g_state);

	// ── Setup listen socket ──────────────────────────────────────
	TCP_SOCKET inherited_listen = INVALID_TCP_SOCKET;
	uint16_t inherited_port = 0;
	bool use_inherited = false;
	char listen_port_print[16];
	snprintf(listen_port_print, sizeof(listen_port_print), "%s", port);
	if (GetListenFdFromEnv(&inherited_listen))
	{
		const char* inherited_port_str = getenv("ASCIICKER_LISTEN_PORT");
		if (inherited_port_str && inherited_port_str[0])
			snprintf(listen_port_print, sizeof(listen_port_print), "%s", inherited_port_str);
		if (GetListenSocketPort(inherited_listen, &inherited_port))
		{
			snprintf(listen_port_print, sizeof(listen_port_print), "%u", inherited_port);
			use_inherited = true;
			g_state->listen_socket = inherited_listen;
		}
		else
		{
			printf("SERVER: ignored invalid inherited listen fd, rebinding\n");
			unsetenv("ASCIICKER_LISTEN_FD");
			unsetenv("ASCIICKER_LISTEN_PORT");
		}
	}
	if (!use_inherited)
	{
		g_state->listen_socket = SetupListenSocket(port);
	}

	if (g_state->listen_socket == INVALID_TCP_SOCKET)
	{
		printf("FATAL: Failed to bind listen socket on port %s\n", port);
		free(g_state);
		return 1;
	}

	printf("SERVER listening on port %s (authoritative mode, %d Hz tick, 3-thread)\n",
	       listen_port_print, SVR_TICK_RATE);
	fflush(stdout);

	// ── Launch accept thread (owns accept + WS handshake) ────────
	THREAD_HANDLE* accept_thread = THREAD_CREATE(AcceptThreadEntry, g_state);
	if (!accept_thread)
	{
		printf("FATAL: Failed to create accept thread\n");
		TCP_CLOSE(g_state->listen_socket);
		free(g_state);
		return 1;
	}

	// ── Launch IO thread (owns recv/send, no game state writes) ──
	THREAD_HANDLE* io_thread = THREAD_CREATE(IOThreadEntry, g_state);
	if (!io_thread)
	{
		printf("FATAL: Failed to create IO thread\n");
		__atomic_store_n(&isRunning, false, __ATOMIC_RELEASE);
		THREAD_JOIN(accept_thread);
		TCP_CLOSE(g_state->listen_socket);
		free(g_state);
		return 1;
	}

	// ── Run tick loop on main thread ─────────────────────────────
	ServerTickLoop(g_state);

	// ── Shutdown ─────────────────────────────────────────────────
	__atomic_store_n(&isRunning, false, __ATOMIC_RELEASE);
	THREAD_JOIN(accept_thread);
	THREAD_JOIN(io_thread);

	// Send WS close frames (status 1001 "Going Away") then close sockets (U-05)
	// NOTE: threads are joined above; no live IO thread. Direct socket access is safe here.
	// Use MSG_NOSIGNAL on Linux to avoid SIGPIPE if the remote peer already disconnected.
	for (int i = 0; i < SVR_MAX_CLIENTS; i++)
	{
		if (g_state->clients[i].socket != INVALID_TCP_SOCKET)
		{
			uint8_t close_payload[2] = {0x03, 0xE9}; // 1001 big-endian
			uint8_t close_frame[16];
			int close_len = WS_FRAME_ENCODE(close_frame, close_payload, 2, 0x8);
#ifdef __linux__
			send(g_state->clients[i].socket, close_frame, close_len, MSG_NOSIGNAL);
#else
			send(g_state->clients[i].socket, close_frame, close_len, 0); // SO_NOSIGPIPE set at accept time
#endif
			TCP_CLOSE(g_state->clients[i].socket);
		}
	}

	// Clean up NPC physics
	for (int i = 0; i < g_state->npc_count; i++)
	{
		if (g_state->npcs[i].physics)
			DeletePhysics(g_state->npcs[i].physics);
	}

	TCP_CLOSE(g_state->listen_socket);
	free(g_state);

	TCP_CLEANUP();

	DeleteWorld(world);
	DeleteTerrain(terrain);
	FreeSprites();
	FreeEnemyGens();

	printf("Server shutdown complete.\n");
}

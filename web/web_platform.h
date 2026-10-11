// web_platform.h — Web platform entry / browser bridge seam
//
// PURPOSE: Narrow interface for browser platform entry points (render, input,
// timing, haptics). Extracted from web/game_web.cpp to separate the browser
// event bridge from filesystem, networking, and diagnostics.
//
// INTEGRATION POINTS:
// - game_web.cpp: defines Load, Render, Size, Keyb, Mouse, Touch, GamePad, Focus, Buzz, a3dGetTime
// - JavaScript: calls exported Load(), Render(), Size(), Keyb(), Mouse(), Touch(), GamePad(), Focus()
//
// SEE ALSO:
// - web_platform.cpp — implementation (to be extracted)

#pragma once

#include <stdint.h>
#include <stddef.h>

// Haptic feedback (gamepad rumble / mobile vibration).
void Buzz(void);

// Microsecond timestamp (delegates to a3dGetTime).
uint64_t GetTime(void);

// Function pointer for timestamp generation (allows swapping timing implementations).
extern uint64_t (*MakeStamp)(void);

// JS-callable exports — must match extern "C" definitions in web_platform.cpp.
#ifdef __cplusplus
extern "C" {
#endif

// Initialize game with player name (called from JavaScript).
void Load(const char* name);

// Set the client-side A3D path before Load() starts Main()/LoadGame().
void SetRequestedA3dPath(const char* path);

// Set deterministic world seed from multiplayer join response.
void SetNetSeed(uint32_t seed);

// Main render function (called every frame from JavaScript).
// Returns pointer to AnsiCell buffer.
void* Render(int width, int height);

// Handle window/canvas resize (called from JavaScript).
void Size(int w, int h, int fw, int fh);

// Handle keyboard events (called from JavaScript).
// type: 0=keydown, 1=keyup, val=key code
void Keyb(int type, int val);

// Handle mouse events (called from JavaScript).
void Mouse(int type, int x, int y);

// Handle touch events (called from JavaScript).
void Touch(int type, int id, int x, int y);

// Handle gamepad events (called from JavaScript).
void GamePad(int ev, int idx, float val);

// Handle window focus/blur (called from JavaScript).
void Focus(int set);

#ifdef __cplusplus
}
#endif

// Base path for loading game resources (models, textures, etc.).
extern char base_path[1024];

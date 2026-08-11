// web_disconnect_witness.h — Web disconnect/proof witness seam
//
// PURPOSE: Browser-specific disconnect detection (page close, navigation)
// and proof witness surfaces (verifier respawn, recorder state JSON bridge).
// Extracted from web/game_web.cpp to isolate browser lifecycle and proof
// helpers from platform entry, filesystem, networking, and diagnostics.
//
// INTEGRATION POINTS:
// - game_web.cpp: exit_handler, VerifierRespawn, RecorderStateJson moved here
// - web_recorder_bridge.cpp: BuildRecorderStateJson() called from here
// - JavaScript: calls exported functions via EM_ASM
//
// SEE ALSO:
// - web/web_disconnect_witness.cpp — implementation
// - server/rate_limit_disconnect_witness.h — server-side disconnect witness

#pragma once

#include <stdint.h>

struct Game;
struct Server;

// ── Disconnect lifecycle ──

// Handle game exit request (ESC key, quit command, browser close).
// Attempts to close window or navigate back in browser history.
// Named exit_handler for compatibility with mainmenu.cpp / game.cpp callers
// in the web build. The same function name exists in game_app.cpp for native
// builds and game_svr.cpp for server builds with different implementations.
void exit_handler(int signum);

// ── Proof witness surfaces (extern "C" exports for JavaScript) ──

#ifdef __cplusplus
extern "C" {
#endif

// Verifier: send respawn request packet to server.
// Uses global game state and Server::Send (Packet).
int VerifierRespawn(void);

// Recorder/proof bridge: build structured recorder state JSON snapshot
// from current game/server globals. Returns pointer to internal static buffer.
const char* RecorderStateJson(void);

// Legacy alias for RecorderStateJson.
const char* ClientObservationJsonV1(void);

#ifdef __cplusplus
}
#endif

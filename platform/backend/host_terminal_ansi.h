// platform/backend/host_terminal_ansi.h — Terminal ANSI Host Adapter Declarations
// =============================================================================
//
// PURPOSE:
// Declares the terminal ANSI polling driver plus the helper contracts it consumes
// during the RuntimeHost extraction. The polling loop now lives in
// host_terminal_ansi.cpp. The helper implementations are still shared with the
// existing desktop app TU in this pass.
//
// BACKEND CONFORMANCE:
// See FL-2910 for the per-target source-list contract and backend conformance
// tests that lock this seam.
//
// COMPILED ONLY IN:
//   - makefile_game_term / makefile_game_term_mac (terminal build)
//   - NOT compiled into SDL, editor, or web targets.

#pragma once

#include <stdint.h>
#include "runtime_host.h"

struct Game;
struct InputSink;

void a3dRunPolling(HostPollInterface* hpi, InputSink* sink);

// ---------------------------------------------------------------------------
// time_backend.h contract
// ---------------------------------------------------------------------------
uint64_t a3dGetTime();

// ---------------------------------------------------------------------------
// Terminal window / screen state
// ---------------------------------------------------------------------------
bool GetWH(int wh[2]);
void SetScreen(bool alt);

// ---------------------------------------------------------------------------
// Linux joystick / gamepad scan (host input probe)
// ---------------------------------------------------------------------------
int scan_js(char* gamepad_name, int* gamepad_axes, int* gamepad_buttons, uint8_t* gamepad_mapping);
bool read_js(int fd);

// ---------------------------------------------------------------------------
// Terminal fullscreen / font (host presentation seam)
// ---------------------------------------------------------------------------
void ToggleFullscreen(Game* g);
bool IsFullscreen(Game* g);
bool PrevGLFont();
bool NextGLFont();

// ---------------------------------------------------------------------------
// Signal / atexit cleanup hook (host lifecycle)
// ---------------------------------------------------------------------------
void exit_handler(int sig);

// ---------------------------------------------------------------------------
// Global host state (was in game_app.cpp; owned by this adapter)
// ---------------------------------------------------------------------------
extern int mouse_x;
extern int mouse_y;
extern int mouse_down;
extern int gpm;
extern int tty;
extern bool running;

// =============================================================================
// [FLOW:TERMINAL] Terminal Emulator Public API — OpenGL Window Management
// =============================================================================
//
// PURPOSE:
// Public API for creating and managing OpenGL terminal emulator windows.
// Each window runs an independent game instance (Game object) and renders
// its AnsiCell buffer to screen via OpenGL shaders (see term.cpp for pipeline).
//
// FUNCTIONS:
//
// - TermOpen(A3D_WND* share, float yaw, float pos[3], void(*close)())
//   Create new terminal window with OpenGL context.
//   - share: Shared OpenGL context (for texture/buffer sharing, can be NULL)
//   - yaw: Initial camera rotation (radians, passed to game instance)
//   - pos[3]: Initial camera position (x, y, z world coordinates)
//   - close: Optional callback invoked when window closes (cleanup hook)
//   - Returns: Game* pointer to game instance for this window
//   - Allocates TERM_LIST node, initializes OpenGL resources (textures, shaders,
//     VAO/VBO for fullscreen quad), creates Game instance, adds to linked list
//   - Registers PlatformInterface callbacks (term_render, term_resize, term_keyb_*,
//     term_mouse) to route OS events to Game object
//
// - TermCloseAll()
//   Close all terminal windows and cleanup OpenGL resources.
//   - Traverses linked list of TERM_LIST nodes (term_head → term_tail)
//   - For each window: delete textures/shaders/buffers, free game instance, close window
//   - Resets linked list to empty (term_head = term_tail = 0)
//   - Called on program exit (main shutdown sequence)
//
// - TermResizeAll()
//   Resize all terminal windows to match current viewport dimensions.
//   - Traverses linked list of TERM_LIST nodes
//   - For each window: call term_resize() to recompute cell dimensions and
//     notify Game::OnSize() for viewport-dependent logic (e.g., UI layout)
//   - Called when font size changes (NextGLFont/PrevGLFont) or display scaling changes
//
// TERMINAL MANAGEMENT:
// - Linked list of TERM_LIST nodes (term_head, term_tail in term.cpp)
// - Each node contains: OpenGL window handle (A3D_WND*), game instance (Game*),
//   OpenGL resources (textures, shaders, VAO/VBO), input state (keys[], yaw)
// - Multiple windows supported (each with independent OpenGL context and game state)
//
// LIFECYCLE:
// 1. TermOpen() — Create window, initialize OpenGL, start game instance
// 2. Platform event loop (platform.h) — OS dispatches events to PlatformInterface callbacks
// 3. term_render() (term.cpp) — Render game to screen every frame
// 4. term_close() (term.cpp) or TermCloseAll() — Cleanup OpenGL resources, free game
//
// INTEGRATION POINTS:
// - platform.h: PlatformInterface callbacks route OS events (render, resize, keyboard,
//   mouse) to term_* functions in term.cpp
// - game.cpp: Game object API (Render, OnSize, OnKeyb, OnMouse, OnFocus) receives
//   events from term_* callbacks
// - GetGLFont() (asciiid.cpp/game_app.cpp): Provides CP437 font texture for rendering
//
// THREAD SAFETY:
// - NOT thread-safe — all functions assume single-threaded OpenGL context
// - OpenGL contexts are per-thread, TERM_LIST operations modify global linked list
//   without locking (expects single main thread calling TermOpen/TermCloseAll)
// =============================================================================

#pragma once

#include <stdint.h>
#include "window_backend.h"
#include "input_backend.h"
#include "terminal_backend.h"

struct Game;

Game* TermOpen(A3D_WND* share, float yaw, float pos[3], void(*close)() = 0);
void TermCloseAll();
void TermResizeAll();
int TermApplyPlayerSkinId(uint16_t skin_id);
int TermApplyPlayerSkin();

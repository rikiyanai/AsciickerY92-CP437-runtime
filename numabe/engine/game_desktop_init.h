// engine/game_desktop_init.h — Shared desktop initialization / shutdown
// =============================================================================
//
// PURPOSE:
// Provides GameDesktopInit() and GameDesktopShutdown(), the shared book-ends
// used by both SDL (main_sdl.cpp) and terminal (main_terminal.cpp) entry points.
//
// BACKEND CONFORMANCE:
// FL-2910: exactly one host adapter per target.  This header is NOT backend-
// specific; it abstracts the common V8, audio, network, and asset setup that
// every desktop target needs before handing control to its host driver.

#pragma once

// Initialize shared desktop state (V8, audio, materials, command-line,
// server connection, sprite load).  Returns false on fatal startup failure.
bool GameDesktopInit(int argc, char* argv[]);

// Shutdown shared desktop state (V8, audio, world, sprites).  Idempotent.
void GameDesktopShutdown();

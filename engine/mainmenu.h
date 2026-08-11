// ============================================================================
// MAINMENU.H - Main Menu Public API
// ============================================================================
//
// PURPOSE:
// Public interface for the main menu system. Provides functions for rendering,
// input handling, and resource management.
//
// CONDITIONAL COMPILATION:
// Main menu is only compiled for game client builds (not EDITOR or SERVER).
// WHY: Editor and server builds don't need menu UI - they use different entry points.
// For EDITOR/SERVER builds, all functions are stubbed out as empty inline functions
// to avoid link errors while keeping zero runtime overhead.
//
// INTEGRATION:
// - Called from game_app.cpp (native desktop client)
// - Called from game_web.cpp (Emscripten/WebAssembly client)
// - NOT used by editor.cpp (uses different UI system)
// - NOT used by server builds (headless, no UI)
//
// ============================================================================

#ifndef MAINMENU_H
#define MAINMENU_H

#include "game.h"

// WHY: Conditional compilation - main menu only exists in game client builds
// EDITOR builds use different UI system, SERVER builds are headless (no menu)
#if !defined EDITOR && !defined SERVER

// Resource Management
int LoadMainMenuSprites(const char* base_path);  // Load background, palette, logo
void FreeMainMenuSprites();                      // Free allocated resources

// Menu Control
void MainMenu_Show();  // Open menu with dither fade-in (currently unused - menu always visible)

// Rendering (called every frame from platform render loop)
void MainMenu_Render(uint64_t _stamp, AnsiCell* ptr, int width, int height);

// Input Handlers (multi-platform)
void MainMenu_OnSize(int w, int h, int fw, int fh);           // Window resize
void MainMenu_OnKeyb(GAME_KEYB keyb, int key);                // Keyboard input
void MainMenu_OnMouse(GAME_MOUSE mouse, int x, int y);        // Mouse input
void MainMenu_OnTouch(GAME_TOUCH touch, int id, int x, int y); // Touch input
void MainMenu_OnFocus(bool set);                              // Focus change
void MainMenu_OnPadMount(bool connect);                       // Gamepad connect/disconnect
void MainMenu_OnPadButton(int b, bool down);                  // Gamepad button
void MainMenu_OnPadAxis(int a, int16_t pos);                  // Gamepad analog stick

#else
// WHY: Empty inline stubs for EDITOR/SERVER builds (zero overhead, avoid link errors)
inline void MainMenu_Show() {}
inline int LoadMainMenuSprites(const char* base_path) { return 0; }
inline void FreeMainMenuSprites() {}
inline void MainMenu_Render(uint64_t _stamp, AnsiCell* ptr, int width, int height){}
inline void MainMenu_OnSize(int w, int h, int fw, int fh){}
inline void MainMenu_OnKeyb(GAME_KEYB keyb, int key){}
inline void MainMenu_OnMouse(GAME_MOUSE mouse, int x, int y){}
inline void MainMenu_OnTouch(GAME_TOUCH touch, int id, int x, int y){}
inline void MainMenu_OnFocus(bool set){}
inline void MainMenu_OnPadMount(bool connect){}
inline void MainMenu_OnPadButton(int b, bool down){}
inline void MainMenu_OnPadAxis(int a, int16_t pos){}
#endif

#endif
// =============================================================================
// [FLOW:INPUT] Input Event Flow — OS to Game Action Routing
// =============================================================================
//
// PURPOSE:
// Documents the three-stage input event flow from OS capture through platform
// abstraction to game actions. Currently minimal (placeholder), with future
// plans to aggregate multiple input sources and dispatch to screen stacking.
//
// THREE-STAGE INPUT PIPELINE:
//
// STAGE 1: OS CAPTURES RAW INPUT
// - SDL backend (sdl.cpp):     SDL_PollEvent() → SDL_KEYDOWN, SDL_MOUSEBUTTONDOWN, SDL_CONTROLLERAXISMOTION
// - X11 backend (x11.cpp):     XNextEvent() → KeyPress, ButtonPress, MotionNotify
// - Win32 backend (mswin.cpp): GetMessage() → WM_KEYDOWN, WM_LBUTTONDOWN, WM_MOUSEMOVE
// - Web backend (game_web.cpp): Emscripten callbacks → keydown, mousedown, touchstart
//
// STAGE 2: PLATFORM BACKEND TRANSLATES TO ABSTRACTIONS
// - SDL:     SDL_SCANCODE_SPACE → KeyInfo::A3D_SPACE
// - X11:     XLookupKeysym(XK_space) → KeyInfo::A3D_SPACE
// - Win32:   VK_SPACE → KeyInfo::A3D_SPACE
// - Gamepad: SDL_CONTROLLER_BUTTON_A → gpad_button(0, 32767)
//
// STAGE 3: GAME LAYER RECEIVES ABSTRACTED EVENTS
// - Keyboard: wnd->platform_api.keyb_key(wnd, A3D_SPACE, true) → game.cpp OnKeyb()
// - Mouse:    wnd->platform_api.mouse(wnd, x, y, LEFT_DN) → game.cpp OnMouse()
// - Gamepad:  li->gpad_button(0, 32767) → gamepad.cpp UpdateGamePadButton()
//
// INPUT SOURCES (current and planned):
// 1. A3D window (platform backends):   Keyboard, character, mouse, gamepad
//    - PlatformInterface callbacks (keyb_key, keyb_char, mouse)
//    - LoopInterface callbacks (gpad_button, gpad_axis)
// 2. Terminal esc codes (xterm.cpp):   Character, mouse, Kitty keyboard codes
//    - FUTURE: parse ANSI escape sequences, dispatch to game
// 3. Web callbacks (game_web.cpp):     Character, keyboard, mouse, touch, gamepad
//    - Emscripten EM_KEY_*, EM_MOUSE_*, EM_TOUCH_* events
//    - FUTURE: unify with platform backend abstraction
//
// KEY DATA FLOW EXAMPLE:
// OS → Backend → PlatformInterface → game.cpp → game action
// User presses SPACE to jump:
//   1. SDL_PollEvent() → SDL_KEYDOWN, SDL_SCANCODE_SPACE
//   2. sdl.cpp translates: SDL_SCANCODE_SPACE → KeyInfo::A3D_SPACE
//   3. Backend calls: wnd->platform_api.keyb_key(wnd, A3D_SPACE, true)
//   4. game.cpp OnKeyb(): if (key == A3D_SPACE) player->jump = true
//
// FUTURE: INPUT ROUTING ABSTRACTION
// Currently input goes directly from platform backend → game.cpp.
// Future plans:
// - input.cpp aggregates ALL input sources (A3D window, terminal, web, network)
// - Dispatch input to screen stacking (modals, overlays, game world)
// - Priority system: modal dialogs capture input before game world
// - Input mapping: rebindable keys, custom gamepad layouts
//
// KEY FILES:
// - input.cpp        — This file: input routing (currently minimal)
// - platform.h       — PlatformInterface, LoopInterface contract
// - sdl.cpp, x11.cpp, mswin.cpp — Platform backends (STAGE 1 → STAGE 2)
// - game.cpp         — Game engine (STAGE 3 → game actions)
// - gamepad.cpp      — Gamepad mapping and configuration
// - game_web.cpp     — Web input handling (Emscripten)
// =============================================================================

#include "platform/input_backend.h"
#include "platform/gamepad_backend.h"

// 1. aggregate ALL input kinds:
// - A3D window (term.cpp): keyboard, character, mouse, gamepad
// - terminal esc codes (xterm.cpp): character, mouse, +kitty's keyboard codes
// - web callbacks (web.cpp): character, keyboard, mouse, touch, gamepad

// 2. dispatch input to screen stacking 


// =============================================================================
// Platform Abstraction Layer — OS Integration Contract
// =============================================================================
//
// PURPOSE:
// Defines the contract that each platform backend (sdl.cpp, x11.cpp, mswin.cpp)
// must implement to integrate the game engine with OS windowing, input, timing,
// and file I/O. The abstraction layer allows the game engine to be portable
// across Windows, Linux (X11), macOS, and web (Emscripten) with minimal changes.
//
// BACKEND SELECTION (compile-time):
// - USE_SDL defined:        sdl.cpp   (SDL2-based, cross-platform, Windows/Linux/macOS)
// - _WIN32 && !USE_SDL:     mswin.cpp (Windows native, Win32 API)
// - (__linux__ || __APPLE__) && !USE_SDL: x11.cpp (Unix native, X11/GLX)
// - __EMSCRIPTEN__:         game_web.cpp (Web, Emscripten/WebGL)
//
// PLATFORM INTERFACE CONTRACT:
// Each backend must implement PlatformInterface callbacks (lines 216-233):
// - void init(A3D_WND* wnd):           Called once when window is created
// - void render(A3D_WND* wnd):         Called every frame (60 FPS target)
// - void resize(A3D_WND* wnd, w, h):   Called when window size changes
// - void close(A3D_WND* wnd):          Called when window is about to close
// - void keyb_key(wnd, KeyInfo, down): Called for keyboard key press/release
// - void keyb_char(wnd, wchar_t):      Called for text input (Unicode character)
// - void keyb_focus(wnd, bool):        Called when window gains/loses focus
// - void mouse(wnd, x, y, MouseInfo):  Called for mouse movement/clicks
//
// NULL CALLBACK POLICY:
// Callbacks are OPTIONAL. Backends MUST check for null before calling:
//   if (wnd->platform_api.keyb_key)
//       wnd->platform_api.keyb_key(wnd, ki, down);
// Use case: Headless server builds can leave callbacks null (no input handling).
//
// LOOP INTERFACE CONTRACT (optional gamepad support):
// Backends that support gamepads must implement LoopInterface callbacks (lines 241-247):
// - void gpad_mount(name, axes, buttons, mapping[]):  Called when gamepad connected
// - void gpad_unmount():                              Called when gamepad disconnected
// - void gpad_button(int b, int16_t pos):             Called on button press/release
// - void gpad_axis(int a, int16_t pos):               Called on axis movement
//
// TIMING CONTRACT (mandatory):
// Each backend must implement:
// - uint64_t a3dGetTime():  Returns microseconds since arbitrary epoch (wraps every 584542 years)
//   - Unix (sdl.cpp, x11.cpp): clock_gettime(CLOCK_MONOTONIC, &ts) → ts.tv_sec * 1000000 + ts.tv_nsec / 1000
//   - Windows (mswin.cpp): QueryPerformanceCounter(&pc) → pc * 1000000 / frequency
//   - WHY monotonic: never goes backwards (unaffected by NTP adjustments, leap seconds)
//   - WHY uint64_t microseconds: high precision for physics (1/1000000 second)
//   - Wraparound period: 2^64 / 1000000 / 60 / 60 / 24 / 365 = 584542 years
//
// WINDOW MANAGEMENT CONTRACT (mandatory):
// - a3dOpen():    Create window with GraphicsDesc (OpenGL context, size, fullscreen mode)
// - a3dClose():   Destroy window (if PlatformInterface::close == null, called automatically)
// - a3dLoop():    Main event loop (blocks until all windows closed)
//
// THREAD/MUTEX:
// Threading primitives live in server/network.h (THREAD_CREATE, MUTEX_CREATE).
// The a3d threading API was removed (zero callers).
//
// INPUT EVENT FLOW (brief):
// OS → Backend → PlatformInterface callbacks → Game engine
// Example: User presses SPACE key:
//   1. SDL: SDL_PollEvent() → SDL_KEYDOWN → SDL_SCANCODE_SPACE
//   2. Backend: Translate SDL_SCANCODE_SPACE → KeyInfo::A3D_SPACE
//   3. Callback: wnd->platform_api.keyb_key(wnd, A3D_SPACE, true)
//   4. Game: game.cpp OnKeyb() receives A3D_SPACE, triggers jump action
//
// FEATURE MATRIX (backend capabilities):
// - SDL (sdl.cpp):    Gamepad YES (SDL_GameController), Embedded terminal NO
// - X11 (x11.cpp):    Gamepad NO (no standard X11 API), Embedded terminal YES (pty)
// - Win32 (mswin.cpp): Gamepad NO, Embedded terminal NO
// - Web (game_web.cpp): Gamepad YES (HTML5 Gamepad API), Embedded terminal NO
//
// INTEGRATION POINTS:
// - game.cpp         — Implements PlatformInterface callbacks (OnKeyb, OnMouse, OnRender)
// - game_app.cpp     — Calls a3dOpen(), a3dLoop() for native desktop entry
// - gamepad.cpp      — Implements gamepad mapping UI and inverse lookup tables
// - input.cpp        — Future: input routing abstraction (currently minimal)
//
// KEY FILES:
// - platform.h       — This file: contract definitions, enums, function prototypes
// - sdl.cpp          — SDL2 backend (USE_SDL)
// - x11.cpp          — X11/GLX backend (Unix native)
// - mswin.cpp        — Win32/WGL backend (Windows native)
// - game_web.cpp     — Emscripten/WebGL backend (web)
// - gamepad.cpp      — Gamepad mapping UI and event processing
// - input.cpp        — Input event flow documentation
// =============================================================================

// platform.h — Historical reference.
//
// This monolithic header has been split into 7 narrow subheaders.
// No code includes this file anymore. Include the specific subheaders
// listed above instead.
//
// To find the right subheader, search by function name:
//   grep -rn 'a3dOpen\|a3dClose' platform/*.h


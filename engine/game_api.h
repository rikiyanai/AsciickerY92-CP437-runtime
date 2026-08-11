// =============================================================================
// Game API — JavaScript/WASM Binding Declarations
// =============================================================================
//
// PURPOSE:
// Declares the shared-memory bridge between C++ game engine and JavaScript NPC
// scripts. At runtime, JavaScript writes arguments into a flat buffer at known
// offsets, calls akAPI_Call(id) to invoke a C++ handler, and reads results back
// from the same buffer. This zero-copy protocol avoids marshaling overhead for
// the 60+ calls per frame that NPC AI scripts typically make.
//
// KEY TYPES / SYMBOLS:
// - AKAPI_BUF_SIZE: Total shared buffer size (data region + callback bitfield).
// - akAPI_Buff:     Pointer to the shared memory buffer (allocated by platform).
// - akAPI_Call:     C-linkage dispatch function (JavaScript calls this via WASM).
// - Game:           Forward-declared opaque game state (defined in game.h).
//
// PLATFORM SPLIT:
// - akAPI_Exec / akAPI_CB are implemented differently per platform:
//   - game_app.cpp: V8 JavaScript engine (native desktop builds)
//   - game_web.cpp: Emscripten WASM (web builds)
// - akAPI_Init / akAPI_Free are implemented in game_api.cpp (shared).
//
// INCLUDED BY:
// - game_api.cpp  (bridge initialization, JavaScript API surface definition)
// - game.cpp      (C++ side of akAPI_Call dispatch handlers)
// - game_app.cpp  (V8 integration, buffer allocation via ArrayBuffer)
// - game_web.cpp  (Emscripten integration, buffer allocation via malloc)
//
// RELATIONSHIP TO game_api.cpp:
// This header declares the interface; game_api.cpp defines akAPI_Buff,
// akAPI_Init() (which injects the JavaScript "ak" object with read-only state
// queries, explicit action requests, and callbacks), and akAPI_Free().
// The akAPI_Call() dispatch and akAPI_Exec()/akAPI_CB() are platform-specific.
//
// DATA FLOW:
//   JS: ak.requestMove([x,y,blend]) -> akWriteF32 -> akAPI_Call(13) -> C++: input owner reads buffer
//   C++: write result to buffer -> return -> JS: akReadF32 -> ak.getPos()
// =============================================================================

#ifndef GAME_API_H
#define GAME_API_H

struct Game;

// [DATA-CONTRACT:SCRIPT]
// WHY this size: 65536 bytes of data exchange region (sufficient for transferring
// position vectors, strings, inventory lists etc. at known offsets) plus 256/8 = 32
// bytes of callback registration bitfield (256 possible callback slots, 1 bit each).
// Total: 65568 bytes. Both JavaScript and C++ access this buffer at the same
// absolute WASM heap address.
// TODO(PIPELINE-FIX): The expression 256/8 relies on integer division giving 32.
// If callback slot count ever changes, this macro and the bitfield indexing in
// game_api.cpp (akAPI_Buff[65536 + (idx>>3)]) must be updated in lockstep.
#define AKAPI_BUF_SIZE (65536+256/8)

// [FLOW:SCRIPT] C-linkage entry point called from JavaScript via WASM.
// WHY extern "C": Prevents C++ name mangling so that Emscripten can export
// this symbol with a predictable name for JavaScript to call directly.
extern "C" void akAPI_Call(int id);

// [DATA-CONTRACT:SCRIPT] Shared memory buffer -- see game_api.cpp for layout.
extern void* akAPI_Buff;

// WHY global game pointer: akAPI_Call handlers need access to game state
// (player position, inventory, NPC list). A global avoids passing Game*
// through the JavaScript bridge on every call.
extern Game* game;

// [FLOW:SCRIPT] Initializes the JavaScript API surface (ak object) by executing
// JavaScript code that defines queries, requests, callback registration, etc.
void akAPI_Init();
void akAPI_Free();

// [FLOW:SCRIPT] Platform-specific JavaScript execution.
// WHY separate from akAPI_Init: akAPI_Init calls akAPI_Exec to inject JS code,
// but the execution mechanism differs per platform (V8 vs Emscripten eval).
// WHY root param: When true, executes in global scope (for initialization).
// When false, executes in NPC script sandbox scope.
 // implemented by game_app or game_web
void akAPI_Exec(const char* str, int len = -1, bool root=false);
// [FLOW:SCRIPT] Platform-specific callback dispatch (invokes registered JS callback by id).
void akAPI_CB(int id);

// WHY conditional compilation: Game and Emscripten builds provide real callback
// implementations that invoke JavaScript handlers. Editor/server builds (no JS
// engine) get inline stubs that return false (no script callbacks active).
// The callbacks are:
// - OnSay:   Chat message from player, script can filter/modify
// - OnItem:  Inventory action (pickup, drop, use), script can intercept
// - OnFrame: Per-frame tick for NPC AI logic
#if defined GAME || defined EMSCRIPTEN
bool akAPI_OnSay(const char* str, int len, bool* allowed=0);
bool akAPI_OnItem(int action, int story_id, int kind, int subkind, int weight, const char* str,
                  bool* allowed=0, int* out_story_id=0, const char** out_desc=0);
bool akAPI_OnFrame();
#else
inline bool akAPI_OnSay(const char* str, int len, bool* allowed=0) {return false;}
inline bool akAPI_OnItem(int action, int story_id, int kind, int subkind, int weight, const char* str,
                  bool* allowed=0, int* out_story_id=0, const char** out_desc=0) {return false;}
inline bool akAPI_OnFrame() {return false;}
#endif

#endif

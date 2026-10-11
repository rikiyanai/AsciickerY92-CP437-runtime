// =============================================================================
// platform/backend/host_input.h — Host Input Seam
// =============================================================================
//
// PURPOSE:
// Declares the InputSink seam: host adapters emit normalized input events;
// the engine-owned adapter owns hold synthesis, text-to-key mapping,
// and game dispatch.  InputSink is a sibling to HostPollInterface (lifecycle/
// render); the two seams do not couple to each other.
//
// WHY SEPARATE FROM HostPollInterface:
// HostPollInterface owns lifecycle/render. InputSink owns input events.
// Keeping them in separate headers prevents "everything host-related" drift
// and ages better when SDL3/raylib adapters arrive.
//
// HOST ADAPTER ROLE:
// Translate native events (SDL, ANSI escapes, GPM, kitty keyboard protocol,
// xterm mouse protocol) into normalized HostKeyEvent / HostTextEvent /
// HostMouseEvent / HostResizeEvent structs and call the corresponding
// InputSink method.  Do NOT synthesize holds, map characters to game keys,
// or maintain hold deadlines — the engine-side adapter owns all of that.
//
// ENGINE ADAPTER ROLE:
// Receive normalized events, apply hold synthesis, map characters to
// game key codes, and forward to Game::OnKeyb / Game::OnMouse / Game::OnSize.

#pragma once

#include <stdint.h>

// ---------------------------------------------------------------------------
// Key events — physical key press/release with platform key codes
// ---------------------------------------------------------------------------

enum class KeyAction : uint8_t
{
    Down,   // physical key pressed (may be held)
    Up,     // physical key released
    Press,  // non-char terminal input with modifiers (F-keys, arrow+mods, etc.)
};

enum class HostKeyDomain : uint8_t
{
    KeyInfo,       // normalized KeyInfo values from input_backend.h
    TerminalByte,  // terminal protocol byte/token; engine adapter must normalize
};

struct HostKeyEvent
{
    int           key;     // KeyInfo value or terminal protocol token (see domain)
    int           mods;    // modifier bits (shift=1<<8, ctrl=2<<8, alt=4<<8)
    KeyAction     action;
    HostKeyDomain domain = HostKeyDomain::KeyInfo;
};

// ---------------------------------------------------------------------------
// Text events — Unicode codepoint input (separate from key state)
// ---------------------------------------------------------------------------

struct HostTextEvent
{
    uint32_t codepoint;  // Unicode codepoint
};

// ---------------------------------------------------------------------------
// Mouse events — button, movement, wheel
// ---------------------------------------------------------------------------

enum class MouseAction : uint8_t
{
    Move,
    ButtonDown,
    ButtonUp,
    Wheel,
};

struct HostMouseEvent
{
    MouseAction action;
    int x;
    int y;
    int button;  // 0=left, 1=middle, 2=right (ButtonDown/Up); unused for Move/Wheel
    int wheel;   // +1=up, -1=down (Wheel); unused otherwise
};

// ---------------------------------------------------------------------------
// Resize events — terminal/desktop size change in cells
// ---------------------------------------------------------------------------

struct HostResizeEvent
{
    int cols;  // width in cells
    int rows;  // height in cells
    int fbw;   // font cell width (1 for terminal targets, glyph width for GL)
    int fbh;   // font cell height (1 for terminal targets, glyph height for GL)
};

// ---------------------------------------------------------------------------
// InputSink — engine-owned adapter implements this
// ---------------------------------------------------------------------------

struct InputSink
{
    virtual ~InputSink() = default;
    virtual void OnKey(const HostKeyEvent& ev) = 0;
    virtual void OnText(const HostTextEvent& ev) = 0;
    virtual void OnMouse(const HostMouseEvent& ev) = 0;
    virtual void OnResize(const HostResizeEvent& ev) = 0;
};

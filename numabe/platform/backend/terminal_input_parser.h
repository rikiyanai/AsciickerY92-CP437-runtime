// =============================================================================
// platform/backend/terminal_input_parser.h — Terminal Input Byte Parser
// =============================================================================
//
// PURPOSE:
// Extracted raw-byte dispatch from host_terminal_ansi.cpp.
// Owns the partial-sequence buffer and all ANSI / kitty / SGR mouse decode
// logic.  Returns normalized HostKeyEvent / HostTextEvent / HostMouseEvent
// through the InputSink seam.  No game types, no timing state, no global game.
//
// INTERFACE:
//   Feed(bytes, len, sink)  → processes raw bytes, appends to internal buffer
//   Reset()                 → clears partial-sequence state
//
// TESTABILITY:
// Can be driven by synthetic byte sequences without a terminal, window,
// or game dependency.  See tests/cpp/terminal_input_parser_test.cpp.

#pragma once

#include "host_input.h"

struct TerminalInputParser {
    // Process raw bytes from the terminal.
    // Returns true if a host-quit sequence (kitty alt+U) was detected;
    // the caller should set running=false and discard further input.
    bool Feed(const char* bytes, int len, InputSink* sink);

    // Clear any buffered partial sequence (e.g. after terminal reset).
    void Reset();

private:
    char buf[256] = {};
    int buf_len = 0;
    int mouse_x = 0;
    int mouse_y = 0;
    bool mouse_position_valid = false;
};

// FL-4257: PURE_TERM pointer-look policy remains platform-owned. The game
// toggles one backend seam; the parser converts terminal absolute motion into
// deltas without stealing left/middle/right click events from context actions.
void SetTerminalMouseLookEnabled(bool enabled);
bool TerminalMouseLookEnabled();

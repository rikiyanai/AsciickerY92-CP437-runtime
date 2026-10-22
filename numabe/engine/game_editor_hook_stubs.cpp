// engine/game_editor_hook_stubs.cpp
//
// Game-only inert definitions for editor-owned hook functions.
//
// The editor (editor/asciiid.cpp) is the SINGLE OWNER of these features:
//   - AsciiidExternalCommandLoopModeEnabled : editor external command-loop mode
// The game binary does not link the editor, so at the game link these symbols are
// otherwise undefined. On macOS/arm64 an undefined weak *reference* (as declared
// in platform/host_sdl2.cpp / platform/sdl.cpp / platform/terminal_gl_present.cpp)
// still fails static linking, so the game needs real definitions present.
//
// This TU is GAME-ONLY (listed in the game makefiles, never in the editor
// makefile), so there is no duplicate-owner conflict with asciiid.cpp. The
// definitions are marked weak so a strong editor definition wins if this TU is
// ever co-linked. Behavior is fail-closed (Law 6): the game reports "no editor
// authority present" — command-loop mode off, no trace highlight — and never
// fabricates editor state. This adds no gameplay or presentation ownership.
//
// FL-4208: this stub repairs the native TERM++ link so the N0a byte-identity
// proof can run.

extern "C" __attribute__((weak)) bool AsciiidExternalCommandLoopModeEnabled()
{
	return false; // game has no external command-loop mode
}

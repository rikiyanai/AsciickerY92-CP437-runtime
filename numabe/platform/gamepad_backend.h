// platform/gamepad_backend.h — Gamepad input contract
//
// PURPOSE: Optional gamepad mount/button/axis callbacks and state queries.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// WHY SEPARATE: Gamepad is optional (not all backends implement it).
// SDL backend: YES. X11/Win32 backends: NO.
//
// BACKENDS: sdl.cpp (SDL_GameController), game_web.cpp (HTML5 Gamepad API)
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

struct A3D_WND;

// Gamepad event callback interface (optional)
struct LoopInterface
{
	void(*gpad_mount)(const char* name, int axes, int buttons, const uint8_t mapping[]);
	void(*gpad_unmount)();
	void(*gpad_button)(int b, int16_t pos);
	void(*gpad_axis)(int a, int16_t pos);
};

bool a3dGetGamePad();
bool a3dGetGamePadButton(int b);
int16_t a3dGetGamePadAxis(int a);

// platform/time_backend.h — Timing contract
//
// PURPOSE: Monotonic microsecond timestamp.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: sdl.cpp, x11.cpp (clock_gettime), mswin.cpp (QueryPerformanceCounter),
//           game_web.cpp (emscripten_get_now)
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

// Returns microseconds since arbitrary epoch. Monotonic, wraps every 584542 years.
uint64_t a3dGetTime();

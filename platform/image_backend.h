// platform/image_backend.h — Image loading / icon contract
//
// PURPOSE: Load image files and set window icons.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: sdl.cpp, x11.cpp, mswin.cpp, game_web.cpp
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

struct A3D_WND;

enum A3D_ImageFormat
{
	A3D_NULL,
	A3D_RGB8,
	A3D_RGB16,
	A3D_RGBA8,
	A3D_RGBA16,
	A3D_LUMINANCE1,
	A3D_LUMINANCE2,
	A3D_LUMINANCE4,
	A3D_LUMINANCE8,
	A3D_LUMINANCE16,
	A3D_LUMINANCE_ALPHA8,
	A3D_LUMINANCE_ALPHA16,
	A3D_INDEX1_RGB,
	A3D_INDEX2_RGB,
	A3D_INDEX4_RGB,
	A3D_INDEX8_RGB,
	A3D_INDEX1_RGBA,
	A3D_INDEX2_RGBA,
	A3D_INDEX4_RGBA,
	A3D_INDEX8_RGBA,
};

bool a3dLoadImage(const char* path, void* cookie, void(*cb)(void* cookie, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf));

bool a3dSetIcon(A3D_WND* wnd, const char* path);
bool a3dSetIconData(A3D_WND* wnd, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf);

// platform/window_backend.h — Window/context lifecycle contract
//
// PURPOSE: Window creation, destruction, title, visibility, rect, mode, multi-context.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: sdl.cpp, x11.cpp, mswin.cpp
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

struct A3D_WND;

struct A3D_PUSH_CONTEXT
{
	void* data[3];
};

void a3dPushContext(A3D_PUSH_CONTEXT* ctx);
void a3dPopContext(const A3D_PUSH_CONTEXT* ctx);
void a3dSwitchContext(const A3D_WND* wnd);

enum WndMode
{
	A3D_WND_CURRENT = 0,
	A3D_WND_NORMAL,
	A3D_WND_FRAMELESS,
	A3D_WND_FULLSCREEN,
};

struct GraphicsDesc
{
	enum FLAGS
	{
		DEBUG_CONTEXT = 1,
		DOUBLE_BUFFER = 2,
	};

	int flags;
	int version[2]; // [0]:Major, [1]:Minor
	int color_bits; // (incl. alpha)
	int alpha_bits;
	int depth_bits;
	int stencil_bits;

	const int* wnd_xywh;
	WndMode wnd_mode;
};

struct PlatformInterface;

A3D_WND* a3dOpen(const PlatformInterface* pi, const GraphicsDesc* gd, A3D_WND* share);
void a3dClose(A3D_WND* wnd);

void a3dSetCookie(A3D_WND* wnd, void* cookie);
void* a3dGetCookie(A3D_WND* wnd);

void a3dSetTitle(A3D_WND* wnd, const char* utf8_name);
int a3dGetTitle(A3D_WND* wnd, char* utf8_name, int size);

void a3dSetVisible(A3D_WND* wnd, bool set);
bool a3dGetVisible(A3D_WND* wnd);

bool a3dIsMaximized(A3D_WND* wnd);

WndMode a3dGetRect(A3D_WND* wnd, int* xywh, int* client_wh);
bool a3dSetRect(A3D_WND* wnd, const int* xywh, WndMode wnd_mode);

void a3dLoop(const struct LoopInterface* li = 0);

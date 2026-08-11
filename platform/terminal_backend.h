// platform/terminal_backend.h — Virtual terminal / PTY contract
//
// PURPOSE: VT100 terminal emulator and pseudoterminal (PTY) APIs.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: x11.cpp (PTY support), term.cpp (VT renderer)
// NOTE: SDL and Win32 backends do NOT implement terminal/PTY.
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>
#include <stddef.h>

struct A3D_VT;
struct A3D_PTY;

A3D_VT* a3dCreateVT(int w, int h, const char* path, char* const argv[], char* const envp[]);
void a3dDestroyVT(A3D_VT* vt);
int a3dWriteVT(A3D_VT* vt, const void* buf, size_t size);
bool a3dGetVTCursorsMode(A3D_VT* vt);
int a3dDumpVT(A3D_VT* vt, int tw, int th);

A3D_PTY* a3dOpenPty(int w, int h, const char* path, char* const argv[], char* const envp[]);
int a3dReadPTY(A3D_PTY* pty, void* buf, size_t size);
int a3dWritePTY(A3D_PTY* pty, const void* buf, size_t size);
void a3dResizePTY(A3D_PTY* pty, int w, int h);
void a3dUnblockPTY(A3D_PTY* pty);
void a3dClosePTY(A3D_PTY* pty);

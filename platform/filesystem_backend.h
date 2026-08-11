// platform/filesystem_backend.h — Filesystem / directory contract
//
// PURPOSE: Directory listing and current-directory operations.
// Extracted from platform/platform.h as part of the platform abstraction split.
//
// BACKENDS: sdl.cpp, x11.cpp, mswin.cpp (native), game_web.cpp (IDBFS virtual)
//
// SEE ALSO: platform.h (umbrella shim)

#pragma once

#include <stdint.h>

enum A3D_DirItem
{
	A3D_DIRECTORY,
	A3D_FILE
};

int a3dListDir(const char* dir_path, bool (*cb)(A3D_DirItem item, const char* name, void* cookie), void* cookie);

bool a3dSetCurDir(const char* dir_path);
bool a3dGetCurDir(char* dir_path, int size);

// web_classic_stubs.cpp - host symbols for the Classic web build.
//
// PURPOSE: supplies the platform/host symbols that the Classic SRCS expect from
// the native host (engine/game_app.cpp, SDL, GL presenter) when linked into the
// wasm module driven by web_classic_main.cpp. Everything here is a host service,
// never renderer logic, so the CP437 cell output is produced entirely by the
// shared Classic renderer TUs.
//
// WHAT EACH STUB CHANGES IN RENDERING:
//   base_path          "./" so assets/... resolve in the MEMFS preload.
//   mat / GetMaterialArr  the 256-entry material table the Classic cell mapper
//                      reads (native: engine/game_app.cpp). Contents come from
//                      the loaded .a3d (ClassicLoadMap reads all 256 tables), so
//                      nothing is invented; unreferenced slots stay zeroed
//                      instead of native InitMaterials defaults.
//   a3dGetTime         microsecond clock from emscripten_get_now (water/noise
//                      animation phase only).
//   glClearBufferfv    GL ES3 call inside the GPU terrain helper
//                      (render_s3_gpu_terrain.cpp). The Classic CPU Render()
//                      path never reaches it with a live GL context in wasm;
//                      a no-op keeps the link closed and changes no CPU cells.

#include <emscripten.h>
#include <stdint.h>

#include "render.h"

char base_path[1024] = "./";

Material mat[256];

void* GetMaterialArr()
{
    return mat;
}

uint64_t a3dGetTime()
{
    return (uint64_t)(emscripten_get_now() * 1000.0);
}

extern "C" void glClearBufferfv(unsigned int buffer, int drawbuffer, const float* value)
{
    (void)buffer;
    (void)drawbuffer;
    (void)value;
}

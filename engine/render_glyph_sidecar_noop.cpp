#include <stdint.h>

// PURE_TERM has no GL sidecar atlas uploader. It keeps the CP437 terminal
// output while the native GL and web targets use their own sidecar writers.
extern "C" void NativeRenderGlyphSidecarWrite(int, int, int, int, uint32_t)
{
}

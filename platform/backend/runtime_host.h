// =============================================================================
// platform/backend/runtime_host.h — Runtime Host Seam
// =============================================================================
//
// PURPOSE:
// Declares the RuntimeHost seam: one adapter per target, chosen at link time.
// Backend owns window / input / audio / files / present.
// Backend does NOT own gameplay, snapshots, authority, bundle truth, or proof.
//
// SEAM CONTRACT:
// Each host adapter (host_sdl2.cpp, host_x11.cpp, host_web.cpp,
// host_terminal_ansi.cpp, host_headless.cpp) implements the platform service
// headers below.  The makefile selects exactly one adapter per binary.
// No #ifdef host-selection guards inside translation units.
//
// BACKEND CONFORMANCE:
// See FL-2910 for the per-target source-list contract and backend conformance
// tests that lock this seam.
//
// INCLUDES (existing service contracts):
#include <stdint.h>

#include "window_backend.h"
#include "input_backend.h"
#include "time_backend.h"
#include "gamepad_backend.h"
#include "filesystem_backend.h"
#include "image_backend.h"
// Audio seam: extracted in Candidate 12 step 3 (FL-2910).
// Future: #include "audio_backend.h" when audio_backend.h is created.

struct AnsiCell;

// Tight polling seam for the extracted PURE_TERM host path.
// Input events flow through the sibling InputSink seam (host_input.h);
// the host adapter no longer reaches into Game callbacks directly.
// The adapter drives lifecycle, per-frame non-render work, and rendering into
// the host-owned AnsiCell buffer.
struct HostPollInterface
{
    virtual ~HostPollInterface() = default;
    virtual bool Init() = 0;
    virtual void Tick(uint64_t dt_us) = 0;
    virtual void Render(uint64_t stamp_us, AnsiCell* buf, int width, int height) = 0;
    virtual void Shutdown() = 0;
};

// render_debug.cpp — Render debug/telemetry helpers
//
// Placeholder for future per-stage debug telemetry extractors.
// Current live render-report surfaces:
//   - render_debug_observation.cpp  — SampleBuffer -> TrackedRemoteClampReport adapter
//   - render_observation_builder.cpp — pure report-section builders
//   - render_sprite_blit.cpp        — queue + per-sprite depth diagnostics
//
// SEE ALSO:
// - engine/render_scene.cpp         — Render() main function
// - engine/render_core.cpp          — core render context/state
// - engine/render_debug_observation.cpp — already-extracted diagnostic helpers

#include "render_internal.h"

// TODO: when additional stages are extracted, move per-stage diagnostic
// helpers here and delete the inline observation blocks from callers.

// glyph_plane.h — FL-4131: versioned GlyphId sidecar carrier
//
// PURPOSE:
// The GlyphPlane is the 1:1 indexed sidecar carrier for a Sprite::Frame's
// AnsiCell buffer. It travels alongside the legacy cell grid and carries
// extended GlyphId data when the sprite was loaded with a valid sidecar.
//
// 1:1 INDEX CONTRACT:
//   plane->cell_count == frame.width * frame.height
//   plane->cells[i] maps to frame.cell[i] (same column-major index)
//   Carrier is currently inert (cells = NULL). Population is later-phase work.
//
// LIFETIME (CP437-only assets pay zero overhead):
//   - No .glyph_profile.json sidecar  ->  Sprite::Frame.glyph_plane = NULL
//   - Valid sidecar + all glyphs <=255 ->  glyph_plane allocated inert (cells=NULL)
//   - Valid sidecar + glyph >255       ->  load fails closed (later phase)
//   - Invalid sidecar                  ->  load fails closed
//
// FAIL-CLOSED:
//   - glyph_plane_alloc(0, N) or (N, 0) -> NULL
//   - glyph_plane_dimensions_match(plane, wrong_w, wrong_h) -> 0
//   - glyph_plane_free(NULL) -> no-op (safe)
//   Byte-domain paths MUST NOT dereference cells while it is inert.
//
// DO NOT:
//   - Put extended GlyphIds in cells while inert
//   - Use cells for CP437 rendering (legacy path stays unchanged)
//   - Add render paths keyed on GlyphPlane presence while inert

#pragma once

#include "glyph_id.h"

#ifdef __cplusplus
extern "C" {
#endif

// ── Version ───────────────────────────────────────────────────────────────────

#define GLYPH_PLANE_VERSION 1

// ── Carrier struct ────────────────────────────────────────────────────────────

// Versioned GlyphId sidecar carrier for one Sprite::Frame.
// Indexed 1:1 with the frame's AnsiCell buffer.
// Currently inert: cells is always NULL. Population is later-phase work.
typedef struct GlyphPlane {
    int version;       // always GLYPH_PLANE_VERSION (1)
    int frame_width;   // sprite frame width at alloc time
    int frame_height;  // sprite frame height at alloc time
    int cell_count;    // always frame_width * frame_height
    GlyphId* cells;    // NULL in Phase 1; Phase 2 will alloc and populate
} GlyphPlane;

// ── API ───────────────────────────────────────────────────────────────────────

// Allocate an inert GlyphPlane for a frame with the given dimensions.
// Returns NULL (fail closed) if frame_width <= 0 or frame_height <= 0.
// On success: version=GLYPH_PLANE_VERSION, cell_count=frame_width*frame_height,
// cells=NULL (inert).
GlyphPlane* glyph_plane_alloc(int frame_width, int frame_height);

// Returns 1 if plane->frame_width == fw && plane->frame_height == fh.
// Returns 0 (fail closed) on any mismatch, or if plane is NULL.
// Use after loading to verify the plane matches the frame it was attached to.
int glyph_plane_dimensions_match(const GlyphPlane* plane, int fw, int fh);

// Free the plane and its cells (if non-NULL).
// No-op if plane is NULL.
void glyph_plane_free(GlyphPlane* plane);

#ifdef __cplusplus
}
#endif

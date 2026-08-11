// glyph_coverage_lookup.h — FL-4131 byte-domain coverage lookup
//
// PURPOSE:
// Holds the byte-domain `glyph_coverage[256]` table (previously inline in
// engine/sprite.cpp) behind a GlyphId-typed lookup API with an explicit status
// discriminator. Legacy CP437 IDs flow through unchanged; extended GlyphIds and
// sentinel IDs fail closed at the renderer/compositor layer instead of
// silently indexing the byte table via `& 0xFF` truncation.
//
// Phase 2 addition:
//   - GLYPH_COV_STATUS_EXTENDED_ADMITTED is returned when a manifest is
//     provided and the glyph is admitted with coverage data.
//   - glyph_coverage_lookup_with_manifest() exposes the manifest-aware path.
//   - The original glyph_coverage_lookup() remains manifest-free and returns
//     EXTENDED_UNBOUND for all extended glyphs, preserving the Phase 1 fail-
//     closed contract for callers that do not yet have manifest context.
//
// CONTRACT:
//   - The legacy `glyph_coverage[256]` table lives in
//     engine/glyph_coverage_lookup.cpp. Nothing else in the engine accesses
//     it directly.
//   - The only public surfaces are `glyph_coverage_lookup(GlyphId)` and
//     `glyph_coverage_lookup_with_manifest(GlyphId, const GlyphManifest*)`.
//   - LEGACY_CP437     -> coverage == glyph_coverage_table[gl]; byte-identical
//                         to the pre-move behavior.
//   - EXTENDED_ADMITTED -> coverage from manifest entries (Phase 2+).
//   - EXTENDED_UNBOUND -> coverage == 0, fail-closed. No manifest binding.
//   - SENTINEL         -> coverage == 0, fail-closed.
//
// LAW: No path in this API or its callers may index `glyph_coverage_table`
// with `gl & 0xFF` when the input is extended or sentinel. That is exactly
// the silent truncation FL-4131 forbids.

#pragma once

#include "glyph_id.h"
#include "glyph_manifest.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum GlyphCoverageStatus {
    GLYPH_COV_STATUS_LEGACY_CP437     = 0,
    GLYPH_COV_STATUS_EXTENDED_UNBOUND = 1,
    GLYPH_COV_STATUS_SENTINEL         = 2,
    GLYPH_COV_STATUS_EXTENDED_ADMITTED = 3,
} GlyphCoverageStatus;

typedef struct GlyphCoverageResult {
    uint16_t coverage;
    GlyphCoverageStatus status;
} GlyphCoverageResult;

// Public lookup without manifest context. Always classifies the input; never
// indexes the byte table for non-legacy IDs. Extended glyphs return
// EXTENDED_UNBOUND (fail-closed) because no manifest is supplied.
GlyphCoverageResult glyph_coverage_lookup(GlyphId gl);

// Manifest-aware lookup. Returns EXTENDED_ADMITTED with real coverage when
// the manifest admits the glyph and has coverage data. Falls back to the
// same fail-closed behavior as glyph_coverage_lookup() for all other cases.
GlyphCoverageResult glyph_coverage_lookup_with_manifest(GlyphId gl, const GlyphManifest* manifest);

#ifdef __cplusplus
}
#endif

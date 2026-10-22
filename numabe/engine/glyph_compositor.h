// glyph_compositor.h — FL-4131 GlyphId-typed sub-cell compositing
//
// PURPOSE:
// Hosts the GlyphId-typed sub-cell averaging functions extracted from the
// byte-domain AverageGlyph / AverageGlyphTransp consumers in
// engine/sprite.cpp. The AnsiCell-wrapped legacy callers in
// engine/sprite.cpp delegate here.
//
// CONTRACT:
//   - The function takes an effective GlyphId, not a uint8_t cell byte.
//   - The compositor routes through glyph_coverage_lookup(gl_eff). Non-
//     LEGACY_CP437 status returns SPRITE_TRANSPARENT_INDEX (255). No path in
//     this function indexes any byte table with `gl & 0xFF`.
//
// SEE ALSO:
//   - engine/glyph_id.h               — GlyphId typedef + sentinels
//   - engine/glyph_coverage_lookup.h  — byte-table lookup API
//   - engine/sprite.cpp:AverageGlyph  — legacy AnsiCell wrapper (delegates here)

#pragma once

#include "glyph_id.h"

#ifdef __cplusplus
extern "C" {
#endif

// GlyphId-typed analogue of AverageGlyph(const AnsiCell*, int mask). The
// AnsiCell wrapper in engine/sprite.cpp passes ptr->fg / ptr->bk through. For
// LEGACY_CP437 input the math is byte-identical to the pre-move behavior. For
// EXTENDED_UNBOUND and SENTINEL the function returns SPRITE_TRANSPARENT_INDEX.
int AverageGlyphForId(GlyphId gl_eff, int fg, int bk, int mask);

// GlyphId-typed analogue of AverageGlyphTransp. Same fail-closed routing as
// AverageGlyphForId; differs only in that the legacy path does not flip
// fg<->bk on SPRITE_TRANSPARENT_INDEX.
int AverageGlyphTranspForId(GlyphId gl_eff, int fg, int bk, int mask);

#ifdef __cplusplus
}
#endif

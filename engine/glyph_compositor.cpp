// glyph_compositor.cpp — FL-4131 GlyphId-typed sub-cell compositing
//
// Implementation of the GlyphId-typed sub-cell averaging functions. Linked by
// the engine via engine/sprite.cpp's AnsiCell wrappers.

#include "glyph_compositor.h"
#include "glyph_coverage_lookup.h"
#include "sprite_constants.h"

extern "C" int AverageGlyphForId(GlyphId gl_eff, int fg, int bk, int mask)
{
    GlyphCoverageResult lk = glyph_coverage_lookup(gl_eff);
    if (lk.status != GLYPH_COV_STATUS_LEGACY_CP437)
        return SPRITE_TRANSPARENT_INDEX;

    int cov = (int)lk.coverage;

    int num = 0;
    int sum = 0;
    if (mask & 1) { sum += cov & 0xf;          num++; }
    if (mask & 2) { sum += (cov >> 4) & 0xf;   num++; }
    if (mask & 4) { sum += (cov >> 8) & 0xf;   num++; }
    if (mask & 8) { sum += (cov >> 12) & 0xf;  num++; }

    if (sum > num * 2)
        return fg != SPRITE_TRANSPARENT_INDEX ? fg : bk;
    return bk != SPRITE_TRANSPARENT_INDEX ? bk : fg;
}

extern "C" int AverageGlyphTranspForId(GlyphId gl_eff, int fg, int bk, int mask)
{
    GlyphCoverageResult lk = glyph_coverage_lookup(gl_eff);
    if (lk.status != GLYPH_COV_STATUS_LEGACY_CP437)
        return SPRITE_TRANSPARENT_INDEX;

    int cov = (int)lk.coverage;

    int num = 0;
    int sum = 0;
    if (mask & 1) { sum += cov & 0xf;          num++; }
    if (mask & 2) { sum += (cov >> 4) & 0xf;   num++; }
    if (mask & 4) { sum += (cov >> 8) & 0xf;   num++; }
    if (mask & 8) { sum += (cov >> 12) & 0xf;  num++; }

    if (sum > num * 2)
        return fg;
    return bk;
}

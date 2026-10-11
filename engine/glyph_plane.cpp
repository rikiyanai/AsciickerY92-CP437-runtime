// glyph_plane.cpp — FL-4131: versioned GlyphId sidecar carrier impl
//
// See glyph_plane.h for contract.

#include "glyph_plane.h"

#include <stdlib.h>
#include <string.h>

GlyphPlane* glyph_plane_alloc(int frame_width, int frame_height)
{
    // Fail closed: invalid dimensions produce no plane (not a silent truncation).
    if (frame_width <= 0 || frame_height <= 0)
        return NULL;

    GlyphPlane* p = (GlyphPlane*)malloc(sizeof(GlyphPlane));
    if (!p)
        return NULL;

    p->version      = GLYPH_PLANE_VERSION;
    p->frame_width  = frame_width;
    p->frame_height = frame_height;
    p->cell_count   = frame_width * frame_height;
    p->cells        = (GlyphId*)malloc(sizeof(GlyphId) * p->cell_count);
    if (!p->cells) {
        free(p);
        return NULL;
    }
    // Initialize all cells to GLYPH_ID_NONE (unpopulated sentinel).
    // LoadSpriteLayer will overwrite with real XPCell.glyph values for
    // both CP437 and extended glyphs.
    for (int i = 0; i < p->cell_count; i++)
        p->cells[i] = GLYPH_ID_NONE;

    return p;
}

int glyph_plane_dimensions_match(const GlyphPlane* plane, int fw, int fh)
{
    if (!plane)
        return 0;
    return (plane->frame_width == fw && plane->frame_height == fh) ? 1 : 0;
}

void glyph_plane_free(GlyphPlane* plane)
{
    if (!plane)
        return;
    if (plane->cells)
        free(plane->cells);
    free(plane);
}

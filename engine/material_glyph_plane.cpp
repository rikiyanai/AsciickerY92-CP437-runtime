// FL-4131 Phase 2: MaterialGlyphPlane implementation.
// See engine/material_glyph_plane.h for contract.

#include "material_glyph_plane.h"
#include <stdlib.h>
#include <string.h>

MaterialGlyphPlane* material_glyph_plane_alloc(void)
{
	MaterialGlyphPlane* plane = (MaterialGlyphPlane*)calloc(1, sizeof(MaterialGlyphPlane));
	if (!plane)
		return NULL;
	plane->cells = (GlyphId*)calloc(64, sizeof(GlyphId));
	if (!plane->cells) {
		free(plane);
		return NULL;
	}
	return plane;
}

void material_glyph_plane_free(MaterialGlyphPlane* plane)
{
	if (!plane)
		return;
	if (plane->cells) {
		free(plane->cells);
		plane->cells = NULL;
	}
	free(plane);
}

void material_glyph_plane_init(MaterialGlyphPlane* plane)
{
	if (!plane || !plane->cells)
		return;
	// Initialize all cells to GLYPH_ID_NONE (sentinel for "no extended glyph").
	// Renderer falls back to MatCell.gl (CP437) when seeing NONE/UNRESOLVED.
	for (int i = 0; i < 64; i++) {
		plane->cells[i] = GLYPH_ID_NONE;
		plane->coverage[i] = 0;
	}
}

GlyphId material_glyph_plane_lookup(const MaterialGlyphPlane* plane, int elev, int shade)
{
	if (!plane || !plane->cells)
		return GLYPH_ID_NONE;
	if (elev < 0 || elev >= 4 || shade < 0 || shade >= 16)
		return GLYPH_ID_NONE;
	return plane->cells[elev * 16 + shade];
}

uint16_t material_glyph_plane_lookup_coverage(const MaterialGlyphPlane* plane, int elev, int shade)
{
	if (!plane || !plane->cells)
		return 0;
	if (elev < 0 || elev >= 4 || shade < 0 || shade >= 16)
		return 0;
	return plane->coverage[elev * 16 + shade];
}

uint8_t material_glyph_plane_coverage_display_glyph(uint16_t coverage)
{
	int q0 = coverage & 0xF;
	int q1 = (coverage >> 4) & 0xF;
	int q2 = (coverage >> 8) & 0xF;
	int q3 = (coverage >> 12) & 0xF;
	int bottom = q0 + q1;
	int top = q2 + q3;
	int left = q0 + q2;
	int right = q1 + q3;
	int total = bottom + top;

	if (total <= 0)
		return '!';
	if (total >= 56)
		return 219; // full block
	if (top >= bottom * 2 && top >= 4)
		return 223; // upper half block
	if (bottom >= top * 2 && bottom >= 4)
		return 220; // lower half block
	if (left >= right * 2 && left >= 4)
		return 221; // left half block
	if (right >= left * 2 && right >= 4)
		return 222; // right half block
	if (total >= 40)
		return 178; // dense shade
	if (total >= 24)
		return 177; // medium shade
	return 176; // light shade
}

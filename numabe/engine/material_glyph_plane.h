// FL-4131 Phase 2: MaterialGlyphPlane carrier for extended glyph admission.
// Analogous to GlyphPlane (engine/glyph_plane.h) but for material shade tables.
//
// Each material has 4 elevation rows × 16 shade columns = 64 cells.
// MaterialGlyphPlane stores the extended GlyphId for each cell, separate from
// MatCell.gl which remains CP437 fallback bytes only (FL-4131 Law).
//
// Ownership: MaterialGlyphPlane is owned by the Material struct. It is
// populated when a material sidecar + manifest are loaded and verified.
// Missing/invalid sidecar → MaterialGlyphPlane remains NULL (legacy CP437).

#pragma once

#include "glyph_id.h"
#include <stdint.h>

// MaterialGlyphPlane: extended glyph storage for one material's shade table.
// 64 cells matching Material.shade[4][16] layout.
//
// WHY separate from MatCell: MatCell.gl is uint8_t CP437 fallback only.
// Extended glyph identity lives here, indexed by [elevation][shade].
// Renderer looks up MaterialGlyphPlane.cells[elev*16 + shade] for final
// GlyphId, then falls back to MatCell.gl if NULL or sentinel.
struct MaterialGlyphPlane
{
	// FL-4131 Phase 2: versioned carrier. cells=NULL for legacy CP437-only.
	// Populated when material sidecar + manifest load + hash verify + admission pass.
	// Freed in Material destructor or material unload.
	GlyphId* cells; // [64] = [4][16], row-major. NULL if no sidecar.
	uint16_t coverage[64]; // Manifest coverage cached at load time for render.
};

// Allocation/deallocation for MaterialGlyphPlane.
// Returns NULL on allocation failure. Caller must check.
MaterialGlyphPlane* material_glyph_plane_alloc(void);
void material_glyph_plane_free(MaterialGlyphPlane* plane);

// Initialize cells to GLYPH_ID_NONE. Called after alloc.
void material_glyph_plane_init(MaterialGlyphPlane* plane);

// Lookup helper: returns cells[elev*16 + shade] or GLYPH_ID_NONE if NULL.
GlyphId material_glyph_plane_lookup(const MaterialGlyphPlane* plane, int elev, int shade);
uint16_t material_glyph_plane_lookup_coverage(const MaterialGlyphPlane* plane, int elev, int shade);

// Deterministic native terminal approximation for an admitted extended glyph's
// manifest coverage. This is not a fallback byte; it is derived from the
// manifest coverage carried beside the MaterialGlyphPlane cell.
uint8_t material_glyph_plane_coverage_display_glyph(uint16_t coverage);

// WHY 64 cells: matches Material.shade[4][16] exactly. Row-major layout
// simplifies iteration during material rasterization and URDO snapshot.

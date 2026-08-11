// FL-4131 Phase 2: Material sidecar parser for .a3d.glyph_profile.json.
// Analogous to glyph_sidecar.h but for material shade tables.
//
// Sidecar format:
//   {
//     "sidecar_version": 1,
//     "profile_kind": "extended_material_glyph_v1",
//     "content_pack_id": "terrain.materials.extended.v1",
//     "glyph_manifest_hash": "<64-char hex SHA-256>",
//     "glyph_manifest_path": "assets/glyphs/...",
//     "material_entries": [
//       {
//         "material_id": 1,
//         "cells": [
//           {"elev": 0, "shade": 0, "glyph_id": 256},
//           ...
//         ]
//       }
//     ]
//   }
//
// WHY this exists: Materials have 4x16=64 cells each. Extended glyph identity
// for material cells lives in MaterialGlyphPlane, not MatCell.gl. This sidecar
// declares which extended glyphs are used by which material cells.

#pragma once

#include "glyph_id.h"
#include <stdint.h>

// MaterialSidecarCell: one extended glyph assignment for one material cell.
struct MaterialSidecarCell
{
	uint8_t elev;   // 0-3 elevation category
	uint8_t shade;  // 0-15 shade level
	GlyphId glyph_id;
};

// MaterialSidecarEntry: extended glyph cells for one material.
struct MaterialSidecarEntry
{
	int material_id;
	MaterialSidecarCell* cells;
	int cell_count;
};

// MaterialSidecar: parsed sidecar for one .a3d or global material set.
struct MaterialSidecar
{
	int sidecar_version;
	char profile_kind[64];       // "extended_material_glyph_v1"
	char content_pack_id[128];   // content pack identifier
	char glyph_manifest_hash[65]; // SHA-256 hex
	char glyph_manifest_path[512]; // relative path to manifest JSON
	MaterialSidecarEntry* entries;
	int entry_count;
};

// Parse sidecar from JSON string. Returns 0 on success, non-zero on failure.
// errbuf receives error message if provided.
int material_sidecar_parse(const char* json_text, MaterialSidecar* out, char* errbuf, int errbuf_size);

// Free allocated memory in MaterialSidecar.
void material_sidecar_free(MaterialSidecar* sidecar);

// Validate sidecar schema: version, profile_kind, required fields, no duplicates.
// Returns 0 on valid, non-zero on invalid.
int material_sidecar_validate(const MaterialSidecar* sidecar, char* errbuf, int errbuf_size);

// Load, validate, verify manifest hash/admission, then call `apply_cell` for
// each admitted extended material cell. Missing sidecar is success with zero
// applied cells. Returns 0 on success, non-zero on failure.
typedef int (*MaterialSidecarApplyCellFn)(void* user, int material_id, int elev, int shade, GlyphId glyph_id, uint16_t coverage);
int material_sidecar_load_apply_for_map(
	const char* map_path,
	MaterialSidecarApplyCellFn apply_cell,
	void* user,
	const char* prefix,
	int* out_applied_cells,
	char* errbuf,
	int errbuf_size);

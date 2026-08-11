// glyph_id.h — project-owned glyph identity typedef + legacy boundary helpers
//
// PURPOSE:
// Pins the GlyphId identity contract for FL-4131 Phase -1
// (`glyph_storage_layout_decision_pinned`). Glyph identity is content/manifest
// identity, NOT renderer-owned, so this header sits outside engine/render/ and
// is includable by bundle compiler, manifest tooling, recorder, asciiid, web
// bridges, and editor code without dragging the render-cell layout.
//
// CONTRACT (FL-4131 Phase -1, decided 2026-05-26):
//   - GlyphId is uint32_t. Wider than uint16_t because the project owns
//     repertoire cluster IDs and future non-scalar repertoires that must not
//     collide with the 16-bit Unicode BMP namespace.
//   - Legacy CP437 IDs `0..255` remain the existing live contract. CP437 IDs
//     are NOT reinterpreted as Unicode scalars and the byte-domain renderer
//     fast paths stay unchanged.
//   - Extended GlyphIds (>255) are only valid under a versioned extended
//     authored profile (`profile_kind="extended_glyph_v1"`, FL-4131 Phase 0
//     sidecar). Extended IDs flowing through byte-domain cells without a
//     valid sidecar are invalid — fail closed; no `gl & 0xFF` truncation,
//     no silent `0x3F`, no `spare`-byte packing.
//   - AnsiCell (engine/render/render.h:76, uint8_t gl) stays 4 bytes. Extended
//     glyph identity travels via a versioned final-glyph sidecar plane
//     indexed 1:1 with the AnsiCell buffer. When the sidecar is present for
//     an extended profile, the sidecar is authoritative and AnsiCell.gl is
//     the legacy/fallback presentation byte only.
//   - MatCell (engine/render/render.h:90, uint8_t gl) stays 8 bytes. Extended
//     material identity travels via a versioned material-glyph sidecar keyed
//     to the same shade/material cell index. MatCell.gl stays the legacy
//     CP437/fallback byte. The flags field keeps its blend/transparency
//     semantics and is NOT used to extend glyph bits.
//   - editor/urdo.cpp visual snapshot records carry a schema version. Legacy
//     CP437 snapshots replay only when all glyphs <=255. Extended snapshots
//     carry full GlyphId sidecar data. Mode switch MAY clear incompatible
//     undo history visibly; the architecture is versioned records, not a
//     permanent hard-incompatible design.
//
// PERF BUDGET (part of `glyph_storage_layout_decision_pinned`):
//   Extended-glyph paths must stay within 1.2x the current terrain shade-loop
//   time on the desktop perf gate, or record a reviewed exception. The
//   sidecar choice for AnsiCell and MatCell exists so CP437-only runs pay
//   zero overhead and extended runs pay one extra indexed lookup.
//
// SCOPE:
// This header pins identity only. No runtime consumers are introduced here.
// Phase 0 (`glyph_manifest_directory_tree_exists`, `glyph_manifest_schema_file_exists`,
// `compile_glyph_manifest_check_wired`, `extended_glyph_fixture_exists`,
// `glyph_sidecar_parser_python_exists`, `glyph_sidecar_parser_engine_exists`,
// `glyph_sidecar_parsers_contract_parity`, `engine_json_library_vendored`,
// `val03_sidecar_branch_wired`, `rfc8785_sha256_helper_exists`,
// `web_atlas_of_atlases_binding`) is the first slice that consumes this pin.

#pragma once

#include <stdint.h>

// ── Identity ──

// Project-owned glyph identity. Carries CP437 0..255 (legacy live contract)
// and extended manifest-declared glyph IDs (>255) in the same namespace.
// Unicode codepoints are NOT directly stored — extended IDs are project-
// owned and assigned by the glyph manifest, which may be backed by Unicode
// scalars, project cluster IDs, or repertoire-paged IDs depending on the
// content pack.
typedef uint32_t GlyphId;

// ── Sentinels (NON-RENDERABLE) ──
//
// Both sentinels below are FAIL-CLOSED markers. They are NOT the renderable
// replacement glyph. The renderable replacement (the "unknown glyph" the
// reader actually sees on screen) is a real, manifest-declared GlyphId loaded
// per content pack at runtime, accessed as `manifest.fallback_glyph_id`
// (Phase 0 schema), and MUST have coverage data in the atlas. A sentinel
// VALUE can never satisfy `unknown_glyph_fallback_all_targets` or any
// coverage gate by itself — validators must reject these sentinels as
// renderable assignments.
//
// The sentinels are placed at the top of the uint32_t range so they cannot
// collide with any real manifest assignment (which lives in 0..(2^32-3)).

// Sentinel for "no glyph assigned" / uninitialized slot. Distinguished from
// UNRESOLVED so loaders can tell uninitialized memory from a lookup that
// completed but produced no manifest binding.
static const GlyphId GLYPH_ID_NONE = 0xFFFFFFFFu;

// Sentinel for "looked up but no manifest binding exists." Set by loaders/
// resolvers when an extended GlyphId arrives without a valid sidecar or
// when a manifest lookup misses. NOT renderable: byte-domain consumers
// must fail closed or route to the manifest-declared `fallback_glyph_id`
// (Phase 0) — they MUST NOT render this sentinel directly.
static const GlyphId GLYPH_ID_UNRESOLVED = 0xFFFFFFFEu;

// ── Legacy CP437 boundary ──

// Upper bound (inclusive) of the legacy CP437 live contract. IDs <= this
// value flow through existing byte-domain renderer paths unchanged.
static const GlyphId GLYPH_ID_CP437_MAX = 0xFFu;

// True if the glyph fits the legacy CP437 byte-domain contract. Byte-domain
// renderer fast paths (AnsiCell.gl, MatCell.gl, RecolorSpriteInPlace,
// glyph_coverage[256], bit2gl/gl2bit) are valid for these IDs.
static inline int glyph_id_is_legacy_cp437(GlyphId g) {
    return g <= GLYPH_ID_CP437_MAX;
}

// True if the glyph requires the extended sidecar path. Callers that touch
// AnsiCell.gl, MatCell.gl, or any byte-oriented glyph table must check this
// and route to the sidecar; they must NOT truncate via `& 0xFF`, pack into
// spare bytes, or silently substitute CP437 `0x3F` (`?`). Sentinels are
// excluded so a fail-closed marker is never classified as a renderable
// extended glyph.
static inline int glyph_id_is_extended(GlyphId g) {
    return g > GLYPH_ID_CP437_MAX && g < GLYPH_ID_UNRESOLVED;
}

// True if the glyph carries a fail-closed sentinel rather than a real
// content assignment. Validators, renderers, and recorder paths MUST treat
// sentinels as non-renderable: route to the manifest's fallback_glyph_id
// (Phase 0) or fail closed. Returning true here is NEVER coverage proof.
static inline int glyph_id_is_sentinel(GlyphId g) {
    return g == GLYPH_ID_NONE || g == GLYPH_ID_UNRESOLVED;
}

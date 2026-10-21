// glyph_id.h — project-owned glyph identity typedef + legacy boundary helpers
//
// PURPOSE:
// Pins the GlyphId identity contract for FL-4131 Phase -1
// (`glyph_storage_layout_decision_pinned`). Glyph identity is content/manifest
// identity, NOT renderer-owned, so this header sits outside engine/render/ and
// is includable by bundle compiler, manifest tooling, recorder, asciiid, web
// bridges, and editor code without dragging the render-cell layout.
//
// CONTRACT (FL-4482 cutover, revised 2026-07-14):
//   - GlyphId is uint32_t. Wider than uint16_t because the project owns
//     repertoire cluster IDs and future non-scalar repertoires that must not
//     collide with the 16-bit Unicode BMP namespace.
//   - Legacy CP437 IDs `0..255` remain assigned content identities. They are
//     not reinterpreted as Unicode scalars.
//   - Every presented cell has one uint32_t final GlyphId. Extended and CP437
//     identities use the same final plane. Missing identity fails closed; no
//     byte truncation, question-mark substitution, spare-byte packing, or
//     AnsiCell.gl fallback survives in final presentation.
//   - AnsiCell (engine/render/render.h:76, uint8_t gl) stays 4 bytes as an
//     upstream legacy render input. The presenter promotes that input into
//     the final plane before selection and never reads AnsiCell.gl to choose
//     the displayed glyph.
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
//   Full-identity presentation must stay within 1.2x the current terrain
//   shade-loop time on the desktop perf gate, unless a reviewed exception is
//   recorded. Every cell pays one indexed final-identity lookup.
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

// Project-owned glyph identity. Carries CP437 0..255 and extended
// manifest-declared glyph IDs in the same final namespace. The FL-4482
// universal registry also gives every valid Unicode scalar a deterministic,
// noncolliding identity while admission metadata decides renderability.
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
// collide with any real manifest assignment (which lives in 0..(2^32-4)).

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

// Sentinel for "Harri resolver was active for this cell, but no admitted
// extended glyph reached the final sidecar." TERM++ renders this as an
// explicit yellow-on-black diagnostic marker; it is not content identity
// and must never pass admission/coverage validation as a real glyph.
static const GlyphId GLYPH_ID_DIAGNOSTIC_MISSING = 0xFFFFFFFDu;

// ── Absence tile (C6 / deliverable #1, FL-4482 / FL-4512) ──
//
// The manifest-admitted absence tile GlyphId. Allocated through the central
// registry `assets/glyphs/glyph_id_registry.json` after scanning ALL allocation
// surfaces (packed content range 512..671; morphology shape catalog 672..1875,
// 1204 entries). 1876 is the collision-free next-free id at pin commit
// b6869e5a3 (2026-07-09); the registry is the single allocator and a re-pin
// re-scans every surface. This is a REAL content GlyphId (not a sentinel): it
// is manifest-admitted, atlas-covered, and the SOLE legal resolution of an
// empty legal pool (C6). It is excluded from ordinary candidate pools
// P_{m,q,k} (never a candidate); it is the fail-closed absence result. The
// four absence classes (live / empty_absence / blocked_missing_fact /
// blocked_unadmitted_glyph) are carried by Fl4260BucketState, not by this id;
// the id is the rendered glyph for every non-live bucket_state.
static const GlyphId GLYPH_ID_ABSENT_TILE = 1876u;

// ── FL-4512 G5: distinct absence-class GlyphIds (paper U5) ──
//
// U5 mandates that each non-live absence state present a DISTINCT registry-
// owned, atlas-covered glyph (not merely a color change on one tile). These
// ids are allocated in assets/glyphs/glyph_id_registry.json and
// rasterized into the extended atlas from the unifont font:
//   GLYPH_ID_ABSENT_EMPTY            1876  U+25A1 □  "No legal glyph"
//   GLYPH_ID_ABSENT_MISSING_FACT     1877  U+25A4 ▤  "Fact unavailable"
//   GLYPH_ID_ABSENT_UNADMITTED_GLYPH 1878  U+2612 ☒  "Glyph not admitted"
//   GLYPH_ID_ABSENT_POOL_LOAD_FAILED  1879  U+26A0 ⚠  "Pool transaction unavailable"
// Each pairs with a reserved fg/bg color (FL4260_ABSENCE_*_ * in
// fl4131_runtime_harri_resolver.h). Like GLYPH_ID_ABSENT_TILE they are
// renderable (in the manifest admission_set) but NEVER candidate glyphs, so a
// live pool can never select one. GLYPH_ID_ABSENT_EMPTY aliases the historical
// GLYPH_ID_ABSENT_TILE (same id) so old empty-pool call sites keep working.
static const GlyphId GLYPH_ID_ABSENT_EMPTY            = 1876u;
static const GlyphId GLYPH_ID_ABSENT_MISSING_FACT     = 1877u;
static const GlyphId GLYPH_ID_ABSENT_UNADMITTED_GLYPH = 1878u;
static const GlyphId GLYPH_ID_ABSENT_POOL_LOAD_FAILED  = 1879u;

// ── Legacy CP437 boundary ──

// Upper bound (inclusive) of the legacy CP437 live contract. IDs <= this
// value flow through existing byte-domain renderer paths unchanged.
static const GlyphId GLYPH_ID_CP437_SPACE = 0x20u;
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
    return g > GLYPH_ID_CP437_MAX && g < GLYPH_ID_DIAGNOSTIC_MISSING;
}

// True if the glyph carries a fail-closed sentinel rather than a real
// content assignment. Validators, renderers, and recorder paths MUST treat
// sentinels as non-renderable: route to the manifest's fallback_glyph_id
// (Phase 0) or fail closed. Returning true here is NEVER coverage proof.
static inline int glyph_id_is_sentinel(GlyphId g) {
    return g == GLYPH_ID_NONE || g == GLYPH_ID_UNRESOLVED || g == GLYPH_ID_DIAGNOSTIC_MISSING;
}

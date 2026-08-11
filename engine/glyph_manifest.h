// glyph_manifest.h — FL-4131 Phase 2: engine-side glyph manifest loader + lookup
//
// PURPOSE:
// Loads the compiled glyph manifest JSON produced by
// scripts/compile_glyph_manifest.py --check. Verifies the SHA-256 of the
// RFC8785 canonical JSON form against the sidecar's glyph_manifest_hash.
// Provides O(log n) lookup for admission, coverage, and fallback.
//
// COMPILED MANIFEST CONTRACT:
//   - Single JSON file per content pack
//   - Engine parses the JSON with cJSON, serializes it back to canonical
//     RFC8785 form (sorted keys, no whitespace, UTF-8), then SHA-256s
//     those bytes to verify against the sidecar hash.
//   - No _computed_sha256 trust: the digest is recomputed from the file
//     contents every load.
//
// LIFETIME:
//   - Load once per sprite (lazy, at LoadSpriteLayer time).
//   - Caller owns the returned GlyphManifest and must call
//     glyph_manifest_free() before freeing the struct pointer.
//   - If the manifest is missing/hash-mismatch/invalid, the load fails
//     closed and the sprite returns NULL.
//
// PHASE BOUNDARY:
//   Phase 2 = backend admission only. No renderer, no shader lookup,
//   no ASCIIID picker, no web buffer, no multiplayer hash.

#pragma once

#include "glyph_id.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// ── Entry ──────────────────────────────────────────────────────────────────

typedef struct GlyphManifestEntry {
    GlyphId glyph_id;
    uint16_t coverage_quadrants;
} GlyphManifestEntry;

// ── Manifest ─────────────────────────────────────────────────────────────────

typedef struct GlyphManifest {
    char content_pack_id[128];
    char sha256_hex[65];
    GlyphId fallback_glyph_id;
    GlyphManifestEntry* entries;
    int entry_count;
    GlyphId* admission_set;
    int admission_count;
} GlyphManifest;

// ── Error codes ──────────────────────────────────────────────────────────────

typedef enum GlyphManifestError {
    GLYPH_MANIFEST_OK = 0,
    GLYPH_MANIFEST_ERR_NOT_FOUND,
    GLYPH_MANIFEST_ERR_JSON,
    GLYPH_MANIFEST_ERR_SCHEMA,
    GLYPH_MANIFEST_ERR_HASH_MISMATCH,
    GLYPH_MANIFEST_ERR_DUPLICATE_GLYPH,
    GLYPH_MANIFEST_ERR_SENTINEL,
    GLYPH_MANIFEST_ERR_MISSING_COVERAGE,
    GLYPH_MANIFEST_ERR_UNADMITTED,
} GlyphManifestError;

// ── API ─────────────────────────────────────────────────────────────────────

// Load the manifest from `manifest_path`, parse it, validate schema, and
// verify that the recomputed RFC8785+SHA-256 digest matches
// `expected_sha256_hex` (from the sidecar).
//
// On success: writes parsed data into *out (caller-provided storage),
//             returns GLYPH_MANIFEST_OK.
// On failure: returns an error code, writes a human-readable message to
//             errbuf (if non-NULL, up to errbuf_size bytes including NUL).
//
// The caller must later call glyph_manifest_free(out) to release internal
// arrays, then free the struct pointer itself if it was heap-allocated.
GlyphManifestError glyph_manifest_load_and_verify(
    const char* manifest_path,
    const char* expected_sha256_hex,
    GlyphManifest* out,
    char* errbuf,
    int errbuf_size);

// Release all heap-allocated arrays inside *manifest.
// Does NOT free the manifest struct itself.
void glyph_manifest_free(GlyphManifest* manifest);

// True (non-zero) if `glyph` is admitted by this manifest.
//   - Legacy CP437 (<=255) is always admitted.
//   - If manifest has no admission_set, all extended entries are admitted.
//   - If manifest has admission_set, the glyph must be listed there.
//   - NULL manifest -> false.
int glyph_manifest_is_admitted(const GlyphManifest* manifest, GlyphId glyph);

// Look up coverage_quadrants for an admitted glyph.
// Returns 1 and writes to *out_coverage on success.
// Returns 0 if manifest is NULL or glyph is not found in entries.
int glyph_manifest_lookup_coverage(const GlyphManifest* manifest, GlyphId glyph, uint16_t* out_coverage);

// Return the manifest's fallback_glyph_id.
// Returns GLYPH_ID_NONE if manifest is NULL.
GlyphId glyph_manifest_fallback_glyph(const GlyphManifest* manifest);

// Human-readable name for an error code.
const char* glyph_manifest_error_name(GlyphManifestError err);

#ifdef __cplusplus
}
#endif

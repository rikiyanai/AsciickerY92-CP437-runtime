// glyph_sidecar.h — FL-4131 Phase 0: C engine glyph sidecar parser
//
// Gate: glyph_sidecar_parser_engine_exists
// Parity gate: glyph_sidecar_parsers_contract_parity
//
// A glyph sidecar file sits alongside an .xp sprite at:
//   <sprite_path>.glyph_profile.json
//
// It declares that the .xp uses extended GlyphIds (>255) and references the
// glyph manifest that defines those IDs. The sidecar is the discriminator
// for the VAL-03 extended-glyph branch at LoadSprite time.
//
// Sidecar JSON contract (profile_kind = "extended_glyph_v1"):
//   {
//     "sidecar_version": 1,
//     "profile_kind": "extended_glyph_v1",
//     "content_pack_id": "<string>",
//     "glyph_manifest_hash": "<64-hex-char lowercase SHA-256>",
//     "glyph_manifest_path": "<string or null>"
//   }
//
// Fail-closed rules (must match scripts/glyph_sidecar.py):
//   - profile_kind != "extended_glyph_v1"      -> GLYPH_SIDECAR_ERR_PROFILE
//   - sidecar_version != 1                     -> GLYPH_SIDECAR_ERR_VERSION
//   - glyph_manifest_hash missing/malformed    -> GLYPH_SIDECAR_ERR_HASH
//   - Any required field missing           -> GLYPH_SIDECAR_ERR_MISSING_FIELD
//   - JSON parse failure                   -> GLYPH_SIDECAR_ERR_JSON
//   - File not found                       -> GLYPH_SIDECAR_ERR_NOT_FOUND
//
// Valid sidecar + any extended glyph in the .xp: Phase 0 recognises the
// sidecar and bypasses the VAL-03 hard-reject, but the extended loader is
// not implemented until Phase 2. The VAL-03 sidecar branch MUST fail closed
// with an explicit "extended loader not implemented until Phase 2" message
// and return NULL from LoadSprite.
//
// Legacy .xp files (no sidecar): existing VAL-03 rejection of glyph > 255
// is preserved unchanged.

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// ── Error codes ──────────────────────────────────────────────────────────────

typedef enum GlyphSidecarError {
    GLYPH_SIDECAR_OK               = 0,
    GLYPH_SIDECAR_ERR_NOT_FOUND    = 1,  // sidecar file not found (legacy path)
    GLYPH_SIDECAR_ERR_JSON         = 2,  // JSON parse failure
    GLYPH_SIDECAR_ERR_NOT_OBJECT   = 3,  // root is not a JSON object
    GLYPH_SIDECAR_ERR_VERSION      = 4,  // sidecar_version missing or != 1
    GLYPH_SIDECAR_ERR_PROFILE      = 5,  // profile_kind missing or not "extended_glyph_v1"
    GLYPH_SIDECAR_ERR_MISSING_FIELD= 6,  // required field absent (content_pack_id)
    GLYPH_SIDECAR_ERR_HASH         = 7,  // glyph_manifest_hash missing or malformed
    GLYPH_SIDECAR_ERR_FIELD_TYPE   = 8,  // field has wrong JSON type
} GlyphSidecarError;

// ── Parsed sidecar descriptor ─────────────────────────────────────────────────

// Maximum string lengths (including NUL terminator).
#define GLYPH_SIDECAR_CONTENT_PACK_ID_MAX     129
#define GLYPH_SIDECAR_MANIFEST_HASH_LEN        65   // 64 hex chars + NUL
#define GLYPH_SIDECAR_MANIFEST_PATH_MAX       512

// Parity surface: must match GlyphSidecar.to_dict() from scripts/glyph_sidecar.py.
typedef struct GlyphSidecar {
    int    sidecar_version;                                    // must be 1
    char   profile_kind[32];                                   // "extended_glyph_v1"
    char   content_pack_id[GLYPH_SIDECAR_CONTENT_PACK_ID_MAX];
    char   glyph_manifest_hash[GLYPH_SIDECAR_MANIFEST_HASH_LEN]; // 64 hex + NUL
    int    has_glyph_manifest_path;                            // 1 if glyph_manifest_path is non-null
    char   glyph_manifest_path[GLYPH_SIDECAR_MANIFEST_PATH_MAX];
} GlyphSidecar;

// ── API ───────────────────────────────────────────────────────────────────────

// Build the sidecar path for an .xp file.
// out must be at least (xp_path_len + 20 + 1) bytes.
// Returns 0 on success, -1 if out is too small.
int glyph_sidecar_path(const char* xp_path, char* out, int out_size);

// Check whether a sidecar file exists alongside the given .xp path.
// Returns 1 if the sidecar file exists, 0 otherwise.
int glyph_sidecar_exists(const char* xp_path);

// Parse and validate the sidecar file at sidecar_path into *out.
// Returns GLYPH_SIDECAR_OK on success. On failure, writes an error message
// to errbuf (if non-NULL, up to errbuf_size bytes including NUL) and returns
// a non-zero GlyphSidecarError code.
//
// Fail-closed: every validation failure returns a non-zero code. The caller
// MUST treat a non-OK result as a hard load failure for the associated .xp.
GlyphSidecarError glyph_sidecar_parse(
    const char* sidecar_path,
    GlyphSidecar* out,
    char* errbuf,
    int errbuf_size
);

// Convenience: parse the sidecar for an .xp path (constructs path internally).
// Returns GLYPH_SIDECAR_ERR_NOT_FOUND if no sidecar exists.
GlyphSidecarError glyph_sidecar_parse_for_xp(
    const char* xp_path,
    GlyphSidecar* out,
    char* errbuf,
    int errbuf_size
);

// Return a human-readable name for a GlyphSidecarError code.
const char* glyph_sidecar_error_name(GlyphSidecarError err);

#ifdef __cplusplus
}
#endif

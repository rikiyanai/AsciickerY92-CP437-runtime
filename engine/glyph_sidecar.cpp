// glyph_sidecar.cpp — FL-4131 Phase 0: C engine glyph sidecar parser impl
//
// Gate: glyph_sidecar_parser_engine_exists
// Parity gate: glyph_sidecar_parsers_contract_parity
//
// See glyph_sidecar.h for the contract. Parity with scripts/glyph_sidecar.py
// is verified by scripts/test_glyph_sidecar_parity.py against
// assets/glyphs/fixtures/sidecar_parity_corpus.json.

#include "glyph_sidecar.h"
#include "third_party/cjson/cJSON.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ── Helpers ───────────────────────────────────────────────────────────────────

static void errf(char* errbuf, int errbuf_size, const char* fmt, ...)
{
    if (!errbuf || errbuf_size <= 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(errbuf, (size_t)errbuf_size, fmt, ap);
    va_end(ap);
}

// Read entire file into a malloc'd NUL-terminated buffer.
// Returns NULL on failure. Caller must free().
static char* read_file(const char* path)
{
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0 || sz > 16 * 1024 * 1024) { fclose(f); return NULL; } // 16 MB guard
    char* buf = (char*)malloc((size_t)(sz + 1));
    if (!buf) { fclose(f); return NULL; }
    size_t n = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[n] = '\0';
    return buf;
}

// Validate a 64-char lowercase hex SHA-256 string.
// Returns 1 if valid, 0 if not.
static int is_valid_manifest_hash(const char* s)
{
    if (!s) return 0;
    int len = 0;
    for (; s[len]; len++) {
        char c = s[len];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return 0;
    }
    return len == 64;
}

// Safe string copy with truncation. Returns 1 if the source fit, 0 if truncated.
static int safe_strcpy(char* dst, int dst_size, const char* src)
{
    if (!dst || dst_size <= 0 || !src) return 0;
    int src_len = (int)strlen(src);
    if (src_len >= dst_size) {
        memcpy(dst, src, (size_t)(dst_size - 1));
        dst[dst_size - 1] = '\0';
        return 0;
    }
    memcpy(dst, src, (size_t)(src_len + 1));
    return 1;
}

// ── Public API ────────────────────────────────────────────────────────────────

int glyph_sidecar_path(const char* xp_path, char* out, int out_size)
{
    if (!xp_path || !out || out_size <= 0) return -1;
    static const char suffix[] = ".glyph_profile.json";
    int xp_len = (int)strlen(xp_path);
    int need = xp_len + (int)(sizeof(suffix)); // sizeof includes NUL
    if (need > out_size) return -1;
    memcpy(out, xp_path, (size_t)xp_len);
    memcpy(out + xp_len, suffix, sizeof(suffix));
    return 0;
}

int glyph_sidecar_exists(const char* xp_path)
{
    char sp[4096];
    if (glyph_sidecar_path(xp_path, sp, (int)sizeof(sp)) != 0) return 0;
    FILE* f = fopen(sp, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

GlyphSidecarError glyph_sidecar_parse(
    const char* sidecar_path,
    GlyphSidecar* out,
    char* errbuf,
    int errbuf_size)
{
    if (!sidecar_path || !out) {
        errf(errbuf, errbuf_size, "glyph_sidecar_parse: null argument");
        return GLYPH_SIDECAR_ERR_JSON;
    }
    memset(out, 0, sizeof(*out));

    // ── Read file ──
    char* text = read_file(sidecar_path);
    if (!text) {
        errf(errbuf, errbuf_size, "sidecar not found: %s", sidecar_path);
        return GLYPH_SIDECAR_ERR_NOT_FOUND;
    }

    // ── Parse JSON ──
    cJSON* root = cJSON_ParseWithLength(text, strlen(text));
    free(text);
    if (!root) {
        const char* ep = cJSON_GetErrorPtr();
        errf(errbuf, errbuf_size, "sidecar JSON parse error in %s near: %.40s",
             sidecar_path, ep ? ep : "(unknown)");
        return GLYPH_SIDECAR_ERR_JSON;
    }

    GlyphSidecarError rc = GLYPH_SIDECAR_OK;

    // ── Root must be object ──
    if (!cJSON_IsObject(root)) {
        errf(errbuf, errbuf_size, "sidecar root must be an object: %s", sidecar_path);
        rc = GLYPH_SIDECAR_ERR_NOT_OBJECT;
        goto done;
    }

    // ── sidecar_version ──
    {
        cJSON* sv_item = cJSON_GetObjectItemCaseSensitive(root, "sidecar_version");
        if (!sv_item || !cJSON_IsNumber(sv_item)) {
            errf(errbuf, errbuf_size, "sidecar missing or invalid 'sidecar_version': %s", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_VERSION;
            goto done;
        }
        int sv = (int)sv_item->valuedouble;
        if (sv != 1) {
            errf(errbuf, errbuf_size,
                 "sidecar 'sidecar_version' must be 1, got %d: %s", sv, sidecar_path);
            rc = GLYPH_SIDECAR_ERR_VERSION;
            goto done;
        }
        out->sidecar_version = sv;
    }

    // ── profile_kind ──
    {
        cJSON* pk_item = cJSON_GetObjectItemCaseSensitive(root, "profile_kind");
        if (!pk_item || !cJSON_IsString(pk_item)) {
            errf(errbuf, errbuf_size, "sidecar missing or invalid 'profile_kind': %s", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_PROFILE;
            goto done;
        }
        const char* pk = cJSON_GetStringValue(pk_item);
        if (strcmp(pk, "extended_glyph_v1") != 0) {
            errf(errbuf, errbuf_size,
                 "sidecar 'profile_kind' must be 'extended_glyph_v1', got '%s': %s. "
                 "Fail-closed: extended loader not implemented for this profile_kind until the relevant Phase ships.",
                 pk, sidecar_path);
            rc = GLYPH_SIDECAR_ERR_PROFILE;
            goto done;
        }
        safe_strcpy(out->profile_kind, (int)sizeof(out->profile_kind), pk);
    }

    // ── content_pack_id ──
    {
        cJSON* cpid_item = cJSON_GetObjectItemCaseSensitive(root, "content_pack_id");
        if (!cpid_item || !cJSON_IsString(cpid_item)) {
            errf(errbuf, errbuf_size, "sidecar missing or invalid 'content_pack_id': %s", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_MISSING_FIELD;
            goto done;
        }
        const char* cpid = cJSON_GetStringValue(cpid_item);
        if (!cpid || cpid[0] == '\0' || strlen(cpid) > 128) {
            errf(errbuf, errbuf_size,
                 "sidecar 'content_pack_id' must be a non-empty string <=128 chars: %s", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_MISSING_FIELD;
            goto done;
        }
        safe_strcpy(out->content_pack_id, (int)sizeof(out->content_pack_id), cpid);
    }

    // ── glyph_manifest_hash ──
    {
        cJSON* mh_item = cJSON_GetObjectItemCaseSensitive(root, "glyph_manifest_hash");
        if (!mh_item || !cJSON_IsString(mh_item)) {
            errf(errbuf, errbuf_size, "sidecar missing or invalid 'glyph_manifest_hash': %s", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_HASH;
            goto done;
        }
        const char* mh = cJSON_GetStringValue(mh_item);
        if (!is_valid_manifest_hash(mh)) {
            errf(errbuf, errbuf_size,
                 "sidecar 'glyph_manifest_hash' must be a 64-char lowercase hex SHA-256, got '%.70s': %s",
                 mh ? mh : "(null)", sidecar_path);
            rc = GLYPH_SIDECAR_ERR_HASH;
            goto done;
        }
        memcpy(out->glyph_manifest_hash, mh, 65); // 64 chars + NUL, validated above
    }

    // ── glyph_manifest_path (optional, may be null) ──
    {
        cJSON* mp_item = cJSON_GetObjectItemCaseSensitive(root, "glyph_manifest_path");
        if (mp_item) {
            if (cJSON_IsNull(mp_item)) {
                out->has_glyph_manifest_path = 0;
            } else if (cJSON_IsString(mp_item)) {
                const char* mp = cJSON_GetStringValue(mp_item);
                out->has_glyph_manifest_path = 1;
                safe_strcpy(out->glyph_manifest_path, (int)sizeof(out->glyph_manifest_path), mp ? mp : "");
            } else {
                errf(errbuf, errbuf_size,
                     "sidecar 'glyph_manifest_path' must be a string or null: %s", sidecar_path);
                rc = GLYPH_SIDECAR_ERR_FIELD_TYPE;
                goto done;
            }
        }
    }

done:
    cJSON_Delete(root);
    return rc;
}

GlyphSidecarError glyph_sidecar_parse_for_xp(
    const char* xp_path,
    GlyphSidecar* out,
    char* errbuf,
    int errbuf_size)
{
    char sp[4096];
    if (glyph_sidecar_path(xp_path, sp, (int)sizeof(sp)) != 0) {
        errf(errbuf, errbuf_size, "glyph_sidecar_parse_for_xp: xp_path too long: %s", xp_path);
        return GLYPH_SIDECAR_ERR_NOT_FOUND;
    }
    FILE* probe = fopen(sp, "rb");
    if (!probe) return GLYPH_SIDECAR_ERR_NOT_FOUND;
    fclose(probe);
    return glyph_sidecar_parse(sp, out, errbuf, errbuf_size);
}

const char* glyph_sidecar_error_name(GlyphSidecarError err)
{
    switch (err) {
        case GLYPH_SIDECAR_OK:                return "GLYPH_SIDECAR_OK";
        case GLYPH_SIDECAR_ERR_NOT_FOUND:     return "GLYPH_SIDECAR_ERR_NOT_FOUND";
        case GLYPH_SIDECAR_ERR_JSON:          return "GLYPH_SIDECAR_ERR_JSON";
        case GLYPH_SIDECAR_ERR_NOT_OBJECT:    return "GLYPH_SIDECAR_ERR_NOT_OBJECT";
        case GLYPH_SIDECAR_ERR_VERSION:       return "GLYPH_SIDECAR_ERR_VERSION";
        case GLYPH_SIDECAR_ERR_PROFILE:       return "GLYPH_SIDECAR_ERR_PROFILE";
        case GLYPH_SIDECAR_ERR_MISSING_FIELD: return "GLYPH_SIDECAR_ERR_MISSING_FIELD";
        case GLYPH_SIDECAR_ERR_HASH:          return "GLYPH_SIDECAR_ERR_HASH";
        case GLYPH_SIDECAR_ERR_FIELD_TYPE:    return "GLYPH_SIDECAR_ERR_FIELD_TYPE";
        default:                              return "GLYPH_SIDECAR_ERR_UNKNOWN";
    }
}

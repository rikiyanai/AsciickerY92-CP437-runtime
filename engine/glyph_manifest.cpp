// glyph_manifest.cpp — FL-4131 Phase 2: engine-side glyph manifest loader + lookup impl
//
// Implements:
//   - SHA-256 (FIPS 180-4 / RFC 6234, small self-contained implementation)
//   - RFC8785-style canonical JSON serialization from a cJSON tree
//   - Manifest schema validation (matches scripts/compile_glyph_manifest.py rules)
//   - Admission set and coverage lookup via sorted arrays + binary search
//
// Hard rules enforced:
//   - Every manifest is hash-verified at load time (no trust-sidecar-only).
//   - Canonical bytes are recomputed; raw file bytes are NOT hashed directly.
//   - Duplicate glyph_id, sentinel values, missing coverage, and admission
//     violations all fail closed with explicit errors.

#include "glyph_manifest.h"
#include "third_party/cjson/cJSON.h"
#include "glyph_id.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ═══════════════════════════════════════════════════════════════════════════════
//  SHA-256 helper (self-contained, no external deps)
// ═══════════════════════════════════════════════════════════════════════════════

typedef struct {
    uint32_t state[8];
    uint64_t bitcount;
    uint8_t  buffer[64];
    int      buflen;
} Sha256Ctx;

static const uint32_t K[64] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
    0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
    0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
    0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
    0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
    0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
    0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
    0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
    0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
    0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
    0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U
};

#define ROTR(x,n)  (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x,y,z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define EP0(x)     (ROTR(x, 2) ^ ROTR(x,13) ^ ROTR(x,22))
#define EP1(x)     (ROTR(x, 6) ^ ROTR(x,11) ^ ROTR(x,25))
#define SIG0(x)    (ROTR(x, 7) ^ ROTR(x,18) ^ ((x) >>  3))
#define SIG1(x)    (ROTR(x,17) ^ ROTR(x,19) ^ ((x) >> 10))

static void sha256_transform(Sha256Ctx* ctx, const uint8_t* data)
{
    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];
    uint32_t W[64], t1, t2;
    int i;

    for (i = 0; i < 16; i++) {
        W[i] = ((uint32_t)data[i*4    ] << 24) |
               ((uint32_t)data[i*4 + 1] << 16) |
               ((uint32_t)data[i*4 + 2] <<  8) |
               ((uint32_t)data[i*4 + 3]      );
    }
    for (i = 16; i < 64; i++) {
        W[i] = SIG1(W[i-2]) + W[i-7] + SIG0(W[i-15]) + W[i-16];
    }

    for (i = 0; i < 64; i++) {
        t1 = h + EP1(e) + CH(e,f,g) + K[i] + W[i];
        t2 = EP0(a) + MAJ(a,b,c);
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(Sha256Ctx* ctx)
{
    ctx->state[0] = 0x6a09e667U; ctx->state[1] = 0xbb67ae85U;
    ctx->state[2] = 0x3c6ef372U; ctx->state[3] = 0xa54ff53aU;
    ctx->state[4] = 0x510e527fU; ctx->state[5] = 0x9b05688cU;
    ctx->state[6] = 0x1f83d9abU; ctx->state[7] = 0x5be0cd19U;
    ctx->bitcount = 0;
    ctx->buflen   = 0;
}

static void sha256_update(Sha256Ctx* ctx, const uint8_t* data, size_t len)
{
    size_t i = 0;
    while (i < len) {
        if (ctx->buflen == 0 && len - i >= 64) {
            sha256_transform(ctx, data + i);
            ctx->bitcount += 64 * 8;
            i += 64;
        } else {
            ctx->buffer[ctx->buflen++] = data[i++];
            if (ctx->buflen == 64) {
                sha256_transform(ctx, ctx->buffer);
                ctx->bitcount += 64 * 8;
                ctx->buflen = 0;
            }
        }
    }
}

static void sha256_final(Sha256Ctx* ctx, uint8_t hash[32])
{
    uint64_t total_bits = ctx->bitcount + (uint64_t)ctx->buflen * 8;
    ctx->buffer[ctx->buflen++] = 0x80;
    if (ctx->buflen > 56) {
        while (ctx->buflen < 64) ctx->buffer[ctx->buflen++] = 0x00;
        sha256_transform(ctx, ctx->buffer);
        ctx->buflen = 0;
    }
    while (ctx->buflen < 56) ctx->buffer[ctx->buflen++] = 0x00;
    ctx->buffer[56] = (uint8_t)(total_bits >> 56);
    ctx->buffer[57] = (uint8_t)(total_bits >> 48);
    ctx->buffer[58] = (uint8_t)(total_bits >> 40);
    ctx->buffer[59] = (uint8_t)(total_bits >> 32);
    ctx->buffer[60] = (uint8_t)(total_bits >> 24);
    ctx->buffer[61] = (uint8_t)(total_bits >> 16);
    ctx->buffer[62] = (uint8_t)(total_bits >>  8);
    ctx->buffer[63] = (uint8_t)(total_bits      );
    sha256_transform(ctx, ctx->buffer);
    for (int i = 0; i < 8; i++) {
        hash[i*4    ] = (uint8_t)(ctx->state[i] >> 24);
        hash[i*4 + 1] = (uint8_t)(ctx->state[i] >> 16);
        hash[i*4 + 2] = (uint8_t)(ctx->state[i] >>  8);
        hash[i*4 + 3] = (uint8_t)(ctx->state[i]      );
    }
}

static void sha256_hex(const char* data, size_t len, char out[65])
{
    Sha256Ctx ctx;
    uint8_t hash[32];
    sha256_init(&ctx);
    sha256_update(&ctx, (const uint8_t*)data, len);
    sha256_final(&ctx, hash);
    static const char hex[17] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i*2    ] = hex[hash[i] >> 4];
        out[i*2 + 1] = hex[hash[i] & 0x0F];
    }
    out[64] = '\0';
}

// ═══════════════════════════════════════════════════════════════════════════════
//  RFC8785 canonical JSON builder (matches Python json.dumps sort_keys=True,
//  separators=(',', ':'), ensure_ascii=False)
// ═══════════════════════════════════════════════════════════════════════════════

typedef struct {
    char*  data;
    size_t len;
    size_t cap;
} StringBuilder;

static void sb_init(StringBuilder* sb)
{
    sb->data = (char*)malloc(512);
    sb->cap = 512;
    sb->len = 0;
    if (sb->data) sb->data[0] = '\0';
}

static void sb_free(StringBuilder* sb)
{
    free(sb->data);
    sb->data = NULL;
    sb->len = sb->cap = 0;
}

static void sb_ensure(StringBuilder* sb, size_t need)
{
    if (sb->cap - sb->len >= need) return;
    size_t new_cap = sb->cap * 2;
    while (new_cap < sb->len + need) new_cap *= 2;
    char* p = (char*)realloc(sb->data, new_cap);
    if (p) { sb->data = p; sb->cap = new_cap; }
}

static void sb_append(StringBuilder* sb, const char* s)
{
    size_t n = strlen(s);
    sb_ensure(sb, n + 1);
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

static void sb_append_n(StringBuilder* sb, const char* s, size_t n)
{
    sb_ensure(sb, n + 1);
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

static void sb_append_char(StringBuilder* sb, char c)
{
    sb_ensure(sb, 2);
    sb->data[sb->len++] = c;
    sb->data[sb->len] = '\0';
}

static void sb_append_json_string(StringBuilder* sb, const char* s)
{
    sb_append_char(sb, '"');
    for (const char* p = s; *p; p++) {
        unsigned char ch = (unsigned char)*p;
        switch (ch) {
            case '"':  sb_append(sb, "\\\""); break;
            case '\\': sb_append(sb, "\\\\"); break;
            case '\b': sb_append(sb, "\\b");  break;
            case '\f': sb_append(sb, "\\f");  break;
            case '\n': sb_append(sb, "\\n");  break;
            case '\r': sb_append(sb, "\\r");  break;
            case '\t': sb_append(sb, "\\t");  break;
            default:
                if (ch < 0x20) {
                    char buf[7];
                    snprintf(buf, sizeof(buf), "\\u%04x", ch);
                    sb_append(sb, buf);
                } else {
                    sb_append_char(sb, (char)ch);
                }
        }
    }
    sb_append_char(sb, '"');
}

static int compare_cjson_keys(const void* a, const void* b)
{
    cJSON* ca = *(cJSON**)a;
    cJSON* cb = *(cJSON**)b;
    return strcmp(ca->string, cb->string);
}

static int canonical_item(cJSON* item, StringBuilder* sb);

static int canonical_object(cJSON* obj, StringBuilder* sb)
{
    sb_append_char(sb, '{');
    int n = 0;
    cJSON* child = obj->child;
    for (cJSON* c = child; c; c = c->next) n++;
    if (n > 0) {
        cJSON** order = (cJSON**)malloc(sizeof(cJSON*) * n);
        if (!order) return 0;
        int i = 0;
        for (cJSON* c = child; c; c = c->next) order[i++] = c;
        qsort(order, (size_t)n, sizeof(cJSON*), compare_cjson_keys);
        for (int j = 0; j < n; j++) {
            if (j > 0) sb_append_char(sb, ',');
            sb_append_json_string(sb, order[j]->string);
            sb_append_char(sb, ':');
            if (!canonical_item(order[j], sb)) { free(order); return 0; }
        }
        free(order);
    }
    sb_append_char(sb, '}');
    return 1;
}

static int canonical_array(cJSON* arr, StringBuilder* sb)
{
    sb_append_char(sb, '[');
    int n = cJSON_GetArraySize(arr);
    for (int i = 0; i < n; i++) {
        if (i > 0) sb_append_char(sb, ',');
        cJSON* elem = cJSON_GetArrayItem(arr, i);
        if (!canonical_item(elem, sb)) return 0;
    }
    sb_append_char(sb, ']');
    return 1;
}

static int canonical_item(cJSON* item, StringBuilder* sb)
{
    if (!item) {
        sb_append(sb, "null");
        return 1;
    }
    if (cJSON_IsNull(item)) {
        sb_append(sb, "null");
        return 1;
    }
    if (cJSON_IsBool(item)) {
        sb_append(sb, item->type == cJSON_True ? "true" : "false");
        return 1;
    }
    if (cJSON_IsNumber(item)) {
        double d = item->valuedouble;
        // For integers, print without decimal point to match Python's json.dumps
        // for integral values.
        if (d == (double)(int64_t)d) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%lld", (int64_t)d);
            sb_append(sb, buf);
        } else {
            char buf[64];
            snprintf(buf, sizeof(buf), "%.17g", d);
            sb_append(sb, buf);
        }
        return 1;
    }
    if (cJSON_IsString(item)) {
        sb_append_json_string(sb, item->valuestring);
        return 1;
    }
    if (cJSON_IsObject(item)) {
        return canonical_object(item, sb);
    }
    if (cJSON_IsArray(item)) {
        return canonical_array(item, sb);
    }
    return 0;
}

// ═══════════════════════════════════════════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════════════════════════════════════════

static void errf(char* errbuf, int errbuf_size, const char* fmt, ...)
{
    if (!errbuf || errbuf_size <= 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(errbuf, (size_t)errbuf_size, fmt, ap);
    va_end(ap);
}

static char* read_file(const char* path)
{
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0 || sz > 16 * 1024 * 1024) { fclose(f); return NULL; }
    char* buf = (char*)malloc((size_t)(sz + 1));
    if (!buf) { fclose(f); return NULL; }
    size_t n = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[n] = '\0';
    return buf;
}

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

// Binary search for a GlyphId in a sorted array.
// Returns index if found, otherwise -1.
static int bsearch_glyphid(const GlyphId* arr, int count, GlyphId key)
{
    int lo = 0, hi = count - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (arr[mid] == key) return mid;
        if (arr[mid] < key) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}

static int bsearch_entry(const GlyphManifestEntry* arr, int count, GlyphId key)
{
    int lo = 0, hi = count - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (arr[mid].glyph_id == key) return mid;
        if (arr[mid].glyph_id < key) lo = mid + 1;
        else hi = mid - 1;
    }
    return -1;
}

static int cmp_glyphid(const void* a, const void* b)
{
    GlyphId ga = *(const GlyphId*)a;
    GlyphId gb = *(const GlyphId*)b;
    if (ga < gb) return -1;
    if (ga > gb) return 1;
    return 0;
}

static int cmp_entry_glyphid(const void* a, const void* b)
{
    GlyphId ga = ((const GlyphManifestEntry*)a)->glyph_id;
    GlyphId gb = ((const GlyphManifestEntry*)b)->glyph_id;
    if (ga < gb) return -1;
    if (ga > gb) return 1;
    return 0;
}

// ═══════════════════════════════════════════════════════════════════════════════
//  Public API
// ═══════════════════════════════════════════════════════════════════════════════

GlyphManifestError glyph_manifest_load_and_verify(
    const char* manifest_path,
    const char* expected_sha256_hex,
    GlyphManifest* out,
    char* errbuf,
    int errbuf_size)
{
    memset(out, 0, sizeof(*out));

    if (!manifest_path || !expected_sha256_hex || !out) {
        errf(errbuf, errbuf_size, "glyph_manifest_load_and_verify: null argument");
        return GLYPH_MANIFEST_ERR_SCHEMA;
    }

    // ── Read file ──
    char* text = read_file(manifest_path);
    if (!text) {
        errf(errbuf, errbuf_size, "manifest not found: %s", manifest_path);
        return GLYPH_MANIFEST_ERR_NOT_FOUND;
    }

    // ── Parse JSON ──
    cJSON* root = cJSON_ParseWithLength(text, strlen(text));
    free(text);
    if (!root) {
        errf(errbuf, errbuf_size, "manifest JSON parse error in %s", manifest_path);
        return GLYPH_MANIFEST_ERR_JSON;
    }

    if (!cJSON_IsObject(root)) {
        errf(errbuf, errbuf_size, "manifest root must be an object: %s", manifest_path);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_JSON;
    }

    // ── Canonicalize and hash ──
    StringBuilder sb;
    sb_init(&sb);
    if (!canonical_item(root, &sb) || !sb.data) {
        errf(errbuf, errbuf_size, "manifest canonicalization failed: %s", manifest_path);
        sb_free(&sb);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_SCHEMA;
    }
    char computed_hash[65];
    sha256_hex(sb.data, sb.len, computed_hash);
    sb_free(&sb);

    if (strcmp(computed_hash, expected_sha256_hex) != 0) {
        errf(errbuf, errbuf_size,
             "manifest hash mismatch: expected %s, computed %s: %s",
             expected_sha256_hex, computed_hash, manifest_path);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_HASH_MISMATCH;
    }
    safe_strcpy(out->sha256_hex, sizeof(out->sha256_hex), computed_hash);

    // ── Schema validation ──

    // manifest_version == 1
    {
        cJSON* mv = cJSON_GetObjectItemCaseSensitive(root, "manifest_version");
        if (!mv || !cJSON_IsNumber(mv) || (int)mv->valuedouble != 1) {
            errf(errbuf, errbuf_size, "manifest_version must be 1: %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
    }

    // profile_kind == "extended_glyph_v1"
    {
        cJSON* pk = cJSON_GetObjectItemCaseSensitive(root, "profile_kind");
        if (!pk || !cJSON_IsString(pk) || strcmp(pk->valuestring, "extended_glyph_v1") != 0) {
            errf(errbuf, errbuf_size, "profile_kind must be 'extended_glyph_v1': %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
    }

    // content_pack_id
    {
        cJSON* cp = cJSON_GetObjectItemCaseSensitive(root, "content_pack_id");
        if (!cp || !cJSON_IsString(cp) || !cp->valuestring[0] || strlen(cp->valuestring) > 128) {
            errf(errbuf, errbuf_size, "content_pack_id must be non-empty string <=128 chars: %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        safe_strcpy(out->content_pack_id, sizeof(out->content_pack_id), cp->valuestring);
    }

    // fallback_glyph_id
    GlyphId fallback_id = GLYPH_ID_NONE;
    {
        cJSON* fb = cJSON_GetObjectItemCaseSensitive(root, "fallback_glyph_id");
        if (!fb || !cJSON_IsNumber(fb)) {
            errf(errbuf, errbuf_size, "fallback_glyph_id missing or not a number: %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        double d = fb->valuedouble;
        if (d < 0 || d != (double)(GlyphId)d) {
            errf(errbuf, errbuf_size, "fallback_glyph_id must be a non-negative integer: %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        fallback_id = (GlyphId)d;
        if (glyph_id_is_sentinel(fallback_id)) {
            errf(errbuf, errbuf_size, "fallback_glyph_id must not be a sentinel: %s", manifest_path);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SENTINEL;
        }
        out->fallback_glyph_id = fallback_id;
    }

    // entries array
    cJSON* entries_arr = cJSON_GetObjectItemCaseSensitive(root, "entries");
    if (!entries_arr || !cJSON_IsArray(entries_arr)) {
        errf(errbuf, errbuf_size, "entries must be an array: %s", manifest_path);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_SCHEMA;
    }
    int entry_count = cJSON_GetArraySize(entries_arr);
    if (entry_count <= 0) {
        errf(errbuf, errbuf_size, "entries must be non-empty: %s", manifest_path);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_SCHEMA;
    }

    GlyphManifestEntry* entries = (GlyphManifestEntry*)malloc(sizeof(GlyphManifestEntry) * entry_count);
    if (!entries) {
        errf(errbuf, errbuf_size, "out of memory allocating entries: %s", manifest_path);
        cJSON_Delete(root);
        return GLYPH_MANIFEST_ERR_SCHEMA;
    }
    memset(entries, 0, sizeof(GlyphManifestEntry) * entry_count);

    int entry_set_count = 0;
    for (int i = 0; i < entry_count; i++) {
        cJSON* e = cJSON_GetArrayItem(entries_arr, i);
        if (!e || !cJSON_IsObject(e)) {
            errf(errbuf, errbuf_size, "entries[%d] must be an object: %s", i, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }

        cJSON* gid_item = cJSON_GetObjectItemCaseSensitive(e, "glyph_id");
        if (!gid_item || !cJSON_IsNumber(gid_item)) {
            errf(errbuf, errbuf_size, "entries[%d] missing glyph_id: %s", i, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        GlyphId gid = (GlyphId)gid_item->valuedouble;
        if (gid <= GLYPH_ID_CP437_MAX) {
            errf(errbuf, errbuf_size, "entries[%d].glyph_id=%u is in CP437 range (0-255): %s", i, gid, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        if (glyph_id_is_sentinel(gid)) {
            errf(errbuf, errbuf_size, "entries[%d].glyph_id is a sentinel: %s", i, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SENTINEL;
        }
        // duplicate check (quadratic is fine for small manifests)
        for (int j = 0; j < entry_set_count; j++) {
            if (entries[j].glyph_id == gid) {
                errf(errbuf, errbuf_size, "duplicate glyph_id %u at entries[%d]: %s", gid, i, manifest_path);
                free(entries);
                cJSON_Delete(root);
                return GLYPH_MANIFEST_ERR_DUPLICATE_GLYPH;
            }
        }

        cJSON* cov_item = cJSON_GetObjectItemCaseSensitive(e, "coverage_quadrants");
        if (!cov_item || !cJSON_IsNumber(cov_item)) {
            errf(errbuf, errbuf_size, "entries[%d] missing coverage_quadrants: %s", i, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_MISSING_COVERAGE;
        }
        int cov = (int)cov_item->valuedouble;
        if (cov < 0 || cov > 65535) {
            errf(errbuf, errbuf_size, "entries[%d].coverage_quadrants out of range: %s", i, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_MISSING_COVERAGE;
        }

        entries[entry_set_count].glyph_id = gid;
        entries[entry_set_count].coverage_quadrants = (uint16_t)cov;
        entry_set_count++;
    }

    // fallback_glyph_id must exist in entries
    {
        int found = 0;
        for (int i = 0; i < entry_set_count; i++) {
            if (entries[i].glyph_id == fallback_id) { found = 1; break; }
        }
        if (!found) {
            errf(errbuf, errbuf_size, "fallback_glyph_id %u not found in entries: %s", fallback_id, manifest_path);
            free(entries);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
    }

    // Sort entries by glyph_id for binary search
    qsort(entries, (size_t)entry_set_count, sizeof(GlyphManifestEntry), cmp_entry_glyphid);
    out->entries = entries;
    out->entry_count = entry_set_count;

    // admission_set (optional)
    cJSON* adm_arr = cJSON_GetObjectItemCaseSensitive(root, "admission_set");
    if (adm_arr) {
        if (!cJSON_IsArray(adm_arr)) {
            errf(errbuf, errbuf_size, "admission_set must be an array: %s", manifest_path);
            glyph_manifest_free(out);
            cJSON_Delete(root);
            return GLYPH_MANIFEST_ERR_SCHEMA;
        }
        int adm_count = cJSON_GetArraySize(adm_arr);
        if (adm_count > 0) {
            GlyphId* admission = (GlyphId*)malloc(sizeof(GlyphId) * adm_count);
            if (!admission) {
                errf(errbuf, errbuf_size, "out of memory allocating admission_set: %s", manifest_path);
                glyph_manifest_free(out);
                cJSON_Delete(root);
                return GLYPH_MANIFEST_ERR_SCHEMA;
            }
            for (int i = 0; i < adm_count; i++) {
                cJSON* a = cJSON_GetArrayItem(adm_arr, i);
                if (!a || !cJSON_IsNumber(a)) {
                    errf(errbuf, errbuf_size, "admission_set[%d] must be an integer: %s", i, manifest_path);
                    free(admission);
                    glyph_manifest_free(out);
                    cJSON_Delete(root);
                    return GLYPH_MANIFEST_ERR_SCHEMA;
                }
                GlyphId ag = (GlyphId)a->valuedouble;
                if (ag <= GLYPH_ID_CP437_MAX) {
                    errf(errbuf, errbuf_size, "admission_set[%d]=%u must be >=256: %s", i, ag, manifest_path);
                    free(admission);
                    glyph_manifest_free(out);
                    cJSON_Delete(root);
                    return GLYPH_MANIFEST_ERR_UNADMITTED;
                }
                if (glyph_id_is_sentinel(ag)) {
                    errf(errbuf, errbuf_size, "admission_set[%d] is a sentinel: %s", i, manifest_path);
                    free(admission);
                    glyph_manifest_free(out);
                    cJSON_Delete(root);
                    return GLYPH_MANIFEST_ERR_SENTINEL;
                }
                admission[i] = ag;
            }
            qsort(admission, (size_t)adm_count, sizeof(GlyphId), cmp_glyphid);
            out->admission_set = admission;
            out->admission_count = adm_count;
        }
    }

    cJSON_Delete(root);
    return GLYPH_MANIFEST_OK;
}

void glyph_manifest_free(GlyphManifest* manifest)
{
    if (!manifest) return;
    if (manifest->entries) {
        free(manifest->entries);
        manifest->entries = NULL;
    }
    if (manifest->admission_set) {
        free(manifest->admission_set);
        manifest->admission_set = NULL;
    }
    manifest->entry_count = 0;
    manifest->admission_count = 0;
}

int glyph_manifest_is_admitted(const GlyphManifest* manifest, GlyphId glyph)
{
    if (!manifest) return 0;
    if (glyph_id_is_legacy_cp437(glyph)) return 1;
    if (glyph_id_is_sentinel(glyph)) return 0;
    if (!manifest->admission_set || manifest->admission_count == 0) {
        // No explicit admission set: all extended entries are admitted
        return bsearch_entry(manifest->entries, manifest->entry_count, glyph) >= 0;
    }
    return bsearch_glyphid(manifest->admission_set, manifest->admission_count, glyph) >= 0;
}

int glyph_manifest_lookup_coverage(const GlyphManifest* manifest, GlyphId glyph, uint16_t* out_coverage)
{
    if (!manifest || !out_coverage) return 0;
    int idx = bsearch_entry(manifest->entries, manifest->entry_count, glyph);
    if (idx < 0) return 0;
    *out_coverage = manifest->entries[idx].coverage_quadrants;
    return 1;
}

GlyphId glyph_manifest_fallback_glyph(const GlyphManifest* manifest)
{
    if (!manifest) return GLYPH_ID_NONE;
    return manifest->fallback_glyph_id;
}

const char* glyph_manifest_error_name(GlyphManifestError err)
{
    switch (err) {
        case GLYPH_MANIFEST_OK:                     return "GLYPH_MANIFEST_OK";
        case GLYPH_MANIFEST_ERR_NOT_FOUND:          return "GLYPH_MANIFEST_ERR_NOT_FOUND";
        case GLYPH_MANIFEST_ERR_JSON:               return "GLYPH_MANIFEST_ERR_JSON";
        case GLYPH_MANIFEST_ERR_SCHEMA:              return "GLYPH_MANIFEST_ERR_SCHEMA";
        case GLYPH_MANIFEST_ERR_HASH_MISMATCH:       return "GLYPH_MANIFEST_ERR_HASH_MISMATCH";
        case GLYPH_MANIFEST_ERR_DUPLICATE_GLYPH:   return "GLYPH_MANIFEST_ERR_DUPLICATE_GLYPH";
        case GLYPH_MANIFEST_ERR_SENTINEL:            return "GLYPH_MANIFEST_ERR_SENTINEL";
        case GLYPH_MANIFEST_ERR_MISSING_COVERAGE:    return "GLYPH_MANIFEST_ERR_MISSING_COVERAGE";
        case GLYPH_MANIFEST_ERR_UNADMITTED:          return "GLYPH_MANIFEST_ERR_UNADMITTED";
        default:                                     return "GLYPH_MANIFEST_ERR_UNKNOWN";
    }
}

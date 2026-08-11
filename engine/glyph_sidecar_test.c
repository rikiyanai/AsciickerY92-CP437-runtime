/* glyph_sidecar_test.c — FL-4131 Phase 0 C engine parity test harness.
 *
 * Gate: glyph_sidecar_parsers_contract_parity (C side)
 *
 * Protocol: argv[1]=tmp_dir, argv[2]=case_json_string
 *   Runs one corpus case from assets/glyphs/fixtures/sidecar_parity_corpus.json
 *   through glyph_sidecar_parse() and checks result against expected_fields /
 *   error_contains from the case JSON.
 *   Exits 0 on PASS, 1 on FAIL.
 *   Prints one line to stdout (detail string matching Python runner format).
 *
 * Build: see scripts/test_glyph_sidecar_parity.py --build-c
 *   clang++ -std=c++11 -I engine -I engine/third_party/cjson \
 *     engine/third_party/cjson/cJSON.c engine/glyph_sidecar.cpp \
 *     engine/glyph_sidecar_test.c -o /tmp/glyph_sidecar_test
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "glyph_sidecar.h"

static int str_contains(const char* hay, const char* needle) {
    if (!needle || !needle[0]) return 1;
    if (!hay) return 0;
    return strstr(hay, needle) != NULL;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage: glyph_sidecar_test <tmp_dir> <case_json>\n");
        return 1;
    }

    cJSON* c = cJSON_Parse(argv[2]);
    if (!c) {
        fprintf(stderr, "FAIL: could not parse case_json argument\n");
        return 1;
    }

    const char* case_id = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(c, "id"));
    const char* sj      = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(c, "sidecar_json"));
    const char* expect  = cJSON_GetStringValue(cJSON_GetObjectItemCaseSensitive(c, "expect"));

    if (!case_id || !sj || !expect) {
        fprintf(stderr, "FAIL: case missing id/sidecar_json/expect\n");
        cJSON_Delete(c);
        return 1;
    }

    /* Write sidecar JSON to temp file (name matches Python runner convention) */
    char sidecar_path[1024];
    snprintf(sidecar_path, sizeof(sidecar_path), "%s/case_%s.xp.sidecar.json", argv[1], case_id);
    {
        FILE* f = fopen(sidecar_path, "w");
        if (!f) {
            printf("[%s] FAIL: cannot write temp file %s\n", case_id, sidecar_path);
            cJSON_Delete(c);
            return 1;
        }
        fputs(sj, f);
        fclose(f);
    }

    GlyphSidecar out;
    char errbuf[512];
    memset(&out, 0, sizeof(out));
    GlyphSidecarError rc = glyph_sidecar_parse(sidecar_path, &out, errbuf, (int)sizeof(errbuf));

    char detail[1024];
    int ok = 0;

    if (strcmp(expect, "ok") == 0) {
        if (rc != GLYPH_SIDECAR_OK) {
            snprintf(detail, sizeof(detail), "[%s] expected OK but got error(%d): %s",
                     case_id, (int)rc, errbuf);
        } else {
            /* Validate expected_fields against parsed GlyphSidecar */
            cJSON* ef = cJSON_GetObjectItemCaseSensitive(c, "expected_fields");
            char mdbuf[512];
            const char* mismatch = NULL;

            if (ef && cJSON_IsObject(ef)) {
                cJSON* sv = cJSON_GetObjectItemCaseSensitive(ef, "sidecar_version");
                if (!mismatch && sv && cJSON_IsNumber(sv) &&
                    out.sidecar_version != (int)sv->valuedouble) {
                    snprintf(mdbuf, sizeof(mdbuf), "sidecar_version: expected %d, got %d",
                             (int)sv->valuedouble, out.sidecar_version);
                    mismatch = mdbuf;
                }
                cJSON* pk = cJSON_GetObjectItemCaseSensitive(ef, "profile_kind");
                if (!mismatch && pk && cJSON_IsString(pk) &&
                    strcmp(out.profile_kind, cJSON_GetStringValue(pk)) != 0) {
                    snprintf(mdbuf, sizeof(mdbuf), "profile_kind: expected '%s', got '%s'",
                             cJSON_GetStringValue(pk), out.profile_kind);
                    mismatch = mdbuf;
                }
                cJSON* cp = cJSON_GetObjectItemCaseSensitive(ef, "content_pack_id");
                if (!mismatch && cp && cJSON_IsString(cp) &&
                    strcmp(out.content_pack_id, cJSON_GetStringValue(cp)) != 0) {
                    snprintf(mdbuf, sizeof(mdbuf), "content_pack_id: expected '%s', got '%s'",
                             cJSON_GetStringValue(cp), out.content_pack_id);
                    mismatch = mdbuf;
                }
                cJSON* mh = cJSON_GetObjectItemCaseSensitive(ef, "glyph_manifest_hash");
                if (!mismatch && mh && cJSON_IsString(mh) &&
                    strcmp(out.glyph_manifest_hash, cJSON_GetStringValue(mh)) != 0) {
                    snprintf(mdbuf, sizeof(mdbuf), "glyph_manifest_hash: expected '%s', got '%s'",
                             cJSON_GetStringValue(mh), out.glyph_manifest_hash);
                    mismatch = mdbuf;
                }
                cJSON* mp = cJSON_GetObjectItemCaseSensitive(ef, "glyph_manifest_path");
                if (!mismatch && mp) {
                    if (cJSON_IsNull(mp)) {
                        if (out.has_glyph_manifest_path) {
                            snprintf(mdbuf, sizeof(mdbuf),
                                     "glyph_manifest_path: expected null, has_path=1 val='%s'",
                                     out.glyph_manifest_path);
                            mismatch = mdbuf;
                        }
                    } else if (cJSON_IsString(mp)) {
                        const char* want = cJSON_GetStringValue(mp);
                        if (!out.has_glyph_manifest_path) {
                            snprintf(mdbuf, sizeof(mdbuf),
                                     "glyph_manifest_path: expected '%s', got null", want);
                            mismatch = mdbuf;
                        } else if (strcmp(out.glyph_manifest_path, want) != 0) {
                            snprintf(mdbuf, sizeof(mdbuf),
                                     "glyph_manifest_path: expected '%s', got '%s'",
                                     want, out.glyph_manifest_path);
                            mismatch = mdbuf;
                        }
                    }
                }
            }

            if (mismatch) {
                snprintf(detail, sizeof(detail), "[%s] field mismatch: %s", case_id, mismatch);
            } else {
                snprintf(detail, sizeof(detail), "[%s] OK", case_id);
                ok = 1;
            }
        }
    } else if (strcmp(expect, "error") == 0) {
        if (rc == GLYPH_SIDECAR_OK) {
            snprintf(detail, sizeof(detail), "[%s] expected error but parse succeeded", case_id);
        } else {
            cJSON* ec = cJSON_GetObjectItemCaseSensitive(c, "error_contains");
            const char* needle = (ec && cJSON_IsString(ec)) ? cJSON_GetStringValue(ec) : "";
            if (!str_contains(errbuf, needle)) {
                snprintf(detail, sizeof(detail),
                         "[%s] error missing expected substring '%s': %s",
                         case_id, needle, errbuf);
            } else {
                snprintf(detail, sizeof(detail), "[%s] OK (rejected as expected)", case_id);
                ok = 1;
            }
        }
    } else {
        snprintf(detail, sizeof(detail), "[%s] unknown expect value '%s'", case_id, expect);
    }

    printf("%s\n", detail);
    cJSON_Delete(c);
    return ok ? 0 : 1;
}

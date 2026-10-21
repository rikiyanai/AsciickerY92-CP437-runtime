/* glyph_manifest_test.c — FL-4131 Phase 2: C engine manifest parser test runner.
 *
 * Tests glyph_manifest_load_and_verify against known fixtures.
 * Build:
 *   clang -std=c11 -I engine -I engine/third_party/cjson \
 *     engine/glyph_manifest.cpp engine/third_party/cjson/cJSON.c \
 *     engine/glyph_manifest_test.c -o glyph_manifest_test
 *
 * Exit code: 0 = all pass, 1 = any failure.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "glyph_manifest.h"

/* Harness-owned base_path stub. The real game/asciiid binary links the
 * strong definition from engine/game_app.cpp instead. Relative manifest
 * paths in the tests below resolve against this "./" root when cwd is
 * the repo checkout (scripts/test_glyph_manifest_parity.py sets that). */
char base_path[1024] = "./";

static int g_pass = 0;
static int g_fail = 0;

static void check(int condition, const char* test_id, const char* desc)
{
    if (condition) {
        printf("[PASS] %s %s\n", test_id, desc);
        g_pass++;
    } else {
        printf("[FAIL] %s %s\n", test_id, desc);
        g_fail++;
    }
}

int main(void)
{
    printf("FL-4131 Phase 2 glyph_manifest_test\n");

    /* T0: sole base_path-aware resolve owner (no cwd-relative open) */
    {
        char resolved[2048];
        char tiny[8];
        strcpy(base_path, "/tmp/fake_app_bin/");

        /* Relative → join base_path */
        int ok = glyph_manifest_resolve_path(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0a", "relative resolve succeeds");
        check(
            strcmp(
                resolved,
                "/tmp/fake_app_bin/assets/glyphs/fixtures/extended_glyph_terrain_v1.json") == 0,
            "T0b",
            "relative joins base_path (not process cwd)");

        /* POSIX absolute passthrough */
        ok = glyph_manifest_resolve_path(
            "/tmp/fl4131_absolute_manifest.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0c", "POSIX absolute resolve succeeds");
        check(
            strcmp(resolved, "/tmp/fl4131_absolute_manifest.json") == 0,
            "T0d",
            "POSIX absolute path passes through unchanged");

        /* Windows drive absolute must NOT join runtime base_path */
        ok = glyph_manifest_resolve_path(
            "C:\\assets\\glyphs\\fixtures\\extended_glyph_terrain_v1.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0e", "Windows drive absolute resolve succeeds");
        check(
            strcmp(
                resolved,
                "C:\\assets\\glyphs\\fixtures\\extended_glyph_terrain_v1.json") == 0,
            "T0f",
            "Windows drive absolute passes through (not base-relative)");
        ok = glyph_manifest_resolve_path(
            "D:/assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0g", "Windows drive forward-slash absolute succeeds");
        check(
            strcmp(
                resolved,
                "D:/assets/glyphs/fixtures/extended_glyph_terrain_v1.json") == 0,
            "T0h",
            "Windows drive forward-slash absolute passes through");

        /* UNC absolute must NOT join runtime base_path */
        ok = glyph_manifest_resolve_path(
            "\\\\server\\share\\glyphs\\extended_glyph_terrain_v1.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0i", "UNC absolute resolve succeeds");
        check(
            strcmp(
                resolved,
                "\\\\server\\share\\glyphs\\extended_glyph_terrain_v1.json") == 0,
            "T0j",
            "UNC absolute passes through (not base-relative)");

        /* Drive-relative "C:foo" is NOT absolute — still joins base_path */
        ok = glyph_manifest_resolve_path(
            "C:relative_manifest.json",
            resolved,
            (int)sizeof(resolved));
        check(ok == 1, "T0k", "drive-relative resolves as relative");
        check(
            strcmp(resolved, "/tmp/fake_app_bin/C:relative_manifest.json") == 0,
            "T0l",
            "drive-relative joins base_path");

        /* Fail-closed: empty requested path */
        ok = glyph_manifest_resolve_path("", resolved, (int)sizeof(resolved));
        check(ok == 0, "T0m", "empty path fails closed");
        check(resolved[0] == '\0', "T0n", "empty path clears out buffer");

        /* Fail-closed: overflow when relative join cannot fit */
        strcpy(base_path, "/tmp/fake_app_bin/");
        ok = glyph_manifest_resolve_path(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            tiny,
            (int)sizeof(tiny));
        check(ok == 0, "T0o", "overflow join fails closed");
        check(tiny[0] == '\0', "T0p", "overflow clears out buffer");

        /* Fail-closed: absolute passthrough that cannot fit out_size */
        ok = glyph_manifest_resolve_path(
            "\\\\server\\share\\glyphs\\extended_glyph_terrain_v1.json",
            tiny,
            (int)sizeof(tiny));
        check(ok == 0, "T0q", "absolute overflow fails closed");
        check(tiny[0] == '\0', "T0r", "absolute overflow clears out buffer");

        /* Restore checkout-relative base for the load fixtures below. */
        strcpy(base_path, "./");
    }

    /* T1: valid manifest loads and hash matches */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_OK, "T1a", "valid manifest loads OK");
        check(m.entry_count > 0, "T1b", "entries non-empty");
        check(m.fallback_glyph_id != GLYPH_ID_NONE, "T1c", "fallback_glyph_id valid");
        check(strcmp(m.content_pack_id, "terrain.extended.v1") == 0, "T1d", "content_pack_id correct");
        glyph_manifest_free(&m);
    }

    /* T2: hash mismatch fails closed */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            "0000000000000000000000000000000000000000000000000000000000000000",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_ERR_HASH_MISMATCH, "T2", "hash mismatch fails closed");
        glyph_manifest_free(&m);
    }

    /* T3: missing manifest file fails closed */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/nonexistent.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_ERR_NOT_FOUND, "T3", "missing manifest fails closed");
    }

    /* T4: malformed JSON fails closed */
    {
        // Write a temp bad JSON file
        FILE* f = fopen("/tmp/fl4131_bad_manifest.json", "w");
        fprintf(f, "{this is not json");
        fclose(f);
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "/tmp/fl4131_bad_manifest.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_ERR_JSON, "T4", "malformed JSON fails closed");
        remove("/tmp/fl4131_bad_manifest.json");
    }

    /* T5: admission_set enforces membership */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_OK, "T5a", "manifest loaded for admission check");
        check(glyph_manifest_is_admitted(&m, 256) == 1, "T5b", "glyph 256 admitted");
        check(glyph_manifest_is_admitted(&m, 999) == 0, "T5c", "glyph 999 not admitted");
        check(glyph_manifest_is_admitted(&m, 65) == 1, "T5d", "CP437 glyph 65 always admitted");
        glyph_manifest_free(&m);
    }

    /* T6: coverage lookup returns correct value */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_OK, "T6a", "manifest loaded for coverage check");
        uint16_t cov = 0;
        check(glyph_manifest_lookup_coverage(&m, 256, &cov) == 1, "T6b", "coverage lookup succeeds for 256");
        check(cov == 65535, "T6c", "coverage for 256 is 65535");
        check(glyph_manifest_lookup_coverage(&m, 999, &cov) == 0, "T6d", "coverage lookup fails for 999");
        glyph_manifest_free(&m);
    }

    /* T7: fallback_glyph_id retrieval */
    {
        GlyphManifest m;
        char err[512];
        GlyphManifestError rc = glyph_manifest_load_and_verify(
            "assets/glyphs/fixtures/extended_glyph_terrain_v1.json",
            "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd",
            &m, err, sizeof(err));
        check(rc == GLYPH_MANIFEST_OK, "T7a", "manifest loaded for fallback check");
        check(glyph_manifest_fallback_glyph(&m) == 256, "T7b", "fallback_glyph_id == 256");
        glyph_manifest_free(&m);
    }

    printf("\nResult: %d pass, %d fail\n", g_pass, g_fail);
    return g_fail > 0 ? 1 : 0;
}

// sprite_validate.cpp - Standalone validation binary for .xp sprite files
// Part of Phase 29: C++ Test Harness (v4.1 Pipeline Hardening)
//
// Exit codes:
//   0 - Valid sprite
//   1 - Validation failure (error to stderr)
//   2 - Usage error (wrong arguments)
//
// Error format follows existing [SPRITE] convention from sprite.cpp

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include "sprite_constants.h"

// External dependency from tinfl.c (gzip/deflate decompression)
extern "C" void *tinfl_decompress_mem_to_heap(const void *pSrc_buf, size_t src_buf_len,
                                               size_t *pOut_len, int flags);

// [DATA-CONTRACT:SPRITE] Gzip header structure (10 bytes)
// From sprite.cpp line 379
struct GZ {
    uint8_t id1, id2, cm, flg;
    uint8_t mtime[4];
    uint8_t xfl, os;
};

// [DATA-CONTRACT:SPRITE] XP cell format - 10 bytes per cell
// From sprite.cpp line 532
#pragma pack(push,1)
struct XPCell {
    uint32_t glyph;
    uint8_t fg[3];
    uint8_t bk[3];

    // Get digit value from glyph (0-9, A-Z=10-35, a-z=10-35)
    // Returns -1 if not a digit
    int GetDigit() const {
        int digit = -1;
        if (glyph >= '0' && glyph <= '9')
            digit = glyph - '0';
        else if (glyph >= 'A' && glyph <= 'Z')
            digit = glyph + 0xA - 'A';
        else if (glyph >= 'a' && glyph <= 'z')
            digit = glyph + 0xa - 'a';
        return digit;
    }
};
#pragma pack(pop)

// ValidateXPFile - Main validation function
// Validates:
//   1. Gzip header (ID1=31, ID2=139, CM=8)
//   2. Gzip optional fields (FEXTRA, FNAME, FCOMMENT, FHCRC)
//   3. Layer count >= SPRITE_MIN_LAYERS
//   4. Dimensions > 0
//   5. Glyph range 0-255 on first 3 layers
//   6. Frame alignment (width % fr_num_x == 0, height % fr_num_y == 0)
//
// Returns true on valid sprite, false on validation failure
// Errors printed to stderr in [SPRITE] format
bool ValidateXPFile(const char* path) {
    FILE* f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "[SPRITE] %s: file not found\n", path);
        return false;
    }

    // =========================================================================
    // VALIDATION 1: Gzip header (from sprite.cpp lines 386-406)
    // =========================================================================
    GZ gz;
    int r = static_cast<int>(fread(&gz, 10, 1, f));
    if (r != 1) {
        fprintf(stderr, "[SPRITE] %s: failed to read gzip header\n", path);
        fclose(f);
        return false;
    }

    if (gz.id1 != 31 || gz.id2 != 139 || gz.cm != 8) {
        fprintf(stderr, "[SPRITE] %s: invalid gzip header (id1=%d id2=%d cm=%d, expected 31 139 8)\n",
                path, gz.id1, gz.id2, gz.cm);
        fclose(f);
        return false;
    }

    // =========================================================================
    // VALIDATION 2: Skip optional gzip fields (from sprite.cpp lines 408-442)
    // =========================================================================

    // FEXTRA (bit 2): 2-byte length + extra field data
    if (gz.flg & (1 << 2)) {
        int hi = 0, lo = 0;
        r = static_cast<int>(fread(&lo, 1, 1, f));
        r = static_cast<int>(fread(&hi, 1, 1, f));
        int len = (hi << 8) | lo;
        fseek(f, len, SEEK_CUR);
    }

    // FNAME (bit 3): null-terminated string
    if (gz.flg & (1 << 3)) {
        uint8_t ch;
        do {
            ch = 0;
            r = static_cast<int>(fread(&ch, 1, 1, f));
        } while (ch);
    }

    // FCOMMENT (bit 4): null-terminated string
    if (gz.flg & (1 << 4)) {
        uint8_t ch;
        do {
            ch = 0;
            r = static_cast<int>(fread(&ch, 1, 1, f));
        } while (ch);
    }

    // FHCRC (bit 1): 2-byte CRC
    if (gz.flg & (1 << 1)) {
        uint16_t crc;
        r = static_cast<int>(fread(&crc, 2, 1, f));
    }

    // =========================================================================
    // Read compressed data and decompress
    // =========================================================================
    long now = ftell(f);
    fseek(f, 0, SEEK_END);
    long end = ftell(f);

    // Validate file has enough data for trailer
    if (end - now < 8) {
        fprintf(stderr, "[SPRITE] %s: file too small (missing gzip trailer)\n", path);
        fclose(f);
        return false;
    }

    unsigned long insize = end - now - 8;
    unsigned char* in = static_cast<unsigned char*>(malloc(insize));
    if (!in) {
        fprintf(stderr, "[SPRITE] %s: memory allocation failed\n", path);
        fclose(f);
        return false;
    }

    fseek(f, now, SEEK_SET);
    r = static_cast<int>(fread(in, 1, insize, f));

    // Decompress using tinfl
    size_t out_size = 0;
    void* out = tinfl_decompress_mem_to_heap(in, insize, &out_size, 0);
    free(in);

    if (!out) {
        fprintf(stderr, "[SPRITE] %s: gzip decompression failed\n", path);
        fclose(f);
        return false;
    }

    // Read gzip trailer (crc32, isize) for size validation
    uint32_t crc32_val, isize;
    r = static_cast<int>(fread(&crc32_val, 4, 1, f));
    r = static_cast<int>(fread(&isize, 4, 1, f));
    fclose(f);

    // Validate decompressed size matches trailer
    if (isize != out_size) {
        fprintf(stderr, "[SPRITE] %s: decompressed size mismatch (expected %u, got %zu)\n",
                path, isize, out_size);
        free(out);
        return false;
    }

    // =========================================================================
    // VALIDATION 3: Layer count >= SPRITE_MIN_LAYERS (from sprite.cpp lines 494-504)
    // =========================================================================

    // Validate minimum header size (4 ints = 16 bytes)
    if (out_size < 16) {
        fprintf(stderr, "[SPRITE] %s: decompressed data too small for header\n", path);
        free(out);
        return false;
    }

    int* header = static_cast<int*>(out);
    int version = header[0];  // version (unused but could validate)
    int layers = header[1];
    int width = header[2];
    int height = header[3];

    (void)version;  // Silence unused warning

    if (layers < SPRITE_MIN_LAYERS) {
        fprintf(stderr, "[SPRITE] %s: layer count %d, expected >= %d\n",
                path, layers, SPRITE_MIN_LAYERS);
        free(out);
        return false;
    }

    // =========================================================================
    // VALIDATION 4: Dimensions > 0 (from sprite.cpp lines 506-512)
    // =========================================================================
    if (width < 1 || height < 1) {
        fprintf(stderr, "[SPRITE] %s: invalid dimensions (width=%d height=%d)\n",
                path, width, height);
        free(out);
        return false;
    }

    // Validate decompressed size is sufficient for all layers
    // Format: 16-byte global header + layers*(cells*10) + (layers-1)*8 bytes inter-layer gaps
    // Layer 0 starts at offset 16, subsequent layers have 8-byte header before them
    size_t cells_per_layer = static_cast<size_t>(width) * height;
    size_t expected_min_size = 16 + (layers * cells_per_layer * sizeof(XPCell)) + ((layers - 1) * 8);

    if (out_size < expected_min_size) {
        fprintf(stderr, "[SPRITE] %s: decompressed data too small (expected >= %zu, got %zu)\n",
                path, expected_min_size, out_size);
        free(out);
        return false;
    }

    // =========================================================================
    // VALIDATION 5: Glyph range 0-255 (from sprite.cpp lines 565-577)
    // =========================================================================
    int cells = width * height;
    XPCell* layer0 = reinterpret_cast<XPCell*>(header + 4);
    XPCell* layer1 = reinterpret_cast<XPCell*>(reinterpret_cast<int*>(layer0 + cells) + 2);
    XPCell* layer2 = reinterpret_cast<XPCell*>(reinterpret_cast<int*>(layer1 + cells) + 2);

    for (int c = 0; c < cells; c++) {
        if (layer0[c].glyph > 255 || layer1[c].glyph > 255 || layer2[c].glyph > 255) {
            fprintf(stderr, "[SPRITE] %s: glyph out of range at cell %d (L0=%u L1=%u L2=%u, max=255)\n",
                    path, c, layer0[c].glyph, layer1[c].glyph, layer2[c].glyph);
            free(out);
            return false;
        }
    }

    // =========================================================================
    // VALIDATION 6: Frame alignment (from sprite.cpp lines 800-879)
    // =========================================================================

    // Parse atlas layout from layer 0
    const int max_anims = 16;
    int projs = 1;
    int anims = 1;
    int anim_len[max_anims] = { 1 };
    int anim_sum = 1;
    int angles = layer0[0].GetDigit();

    if (angles > 0) {
        projs = 2;
        anim_sum = 0;
        anims = 0;
        for (int a = 1; a < width && anims < max_anims; a++) {
            int len = layer0[height * a].GetDigit();
            if (len > 0) {
                anim_sum += len;
                anim_len[anims] = len;
                anims++;
            } else {
                break;
            }
        }

        if (!anims) {
            anims = 1;
            anim_sum = 1;
        }
    } else {
        angles = 1;

        anim_sum = 0;
        anims = 0;
        for (int a = 1; a < width && anims < max_anims; a++) {
            int len = layer0[height * a].GetDigit();
            if (len > 0) {
                anim_sum += len;
                anim_len[anims] = len;
                anims++;
            } else {
                break;
            }
        }

        if (!anims) {
            anims = 1;
            anim_sum = 1;
        }
    }

    int fr_num_x = projs * anim_sum;
    int fr_num_y = angles;

    // Check frame alignment
    if (fr_num_x > 0 && width % fr_num_x != 0) {
        fprintf(stderr, "[SPRITE] %s: width %d not divisible by frame count %d (projs=%d anims=%d anim_sum=%d)\n",
                path, width, fr_num_x, projs, anims, anim_sum);
        free(out);
        return false;
    }
    if (fr_num_y > 0 && height % fr_num_y != 0) {
        fprintf(stderr, "[SPRITE] %s: height %d not divisible by angles %d\n",
                path, height, fr_num_y);
        free(out);
        return false;
    }

    free(out);
    return true;
}

int main(int argc, char* argv[]) {
    // Check command line arguments
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <sprite.xp>\n", argv[0]);
        fprintf(stderr, "\nValidates .xp sprite file format:\n");
        fprintf(stderr, "  - Gzip compression header\n");
        fprintf(stderr, "  - Layer count >= %d\n", SPRITE_MIN_LAYERS);
        fprintf(stderr, "  - Dimensions > 0\n");
        fprintf(stderr, "  - Glyph range 0-255\n");
        fprintf(stderr, "  - Frame alignment\n");
        fprintf(stderr, "\nExit codes:\n");
        fprintf(stderr, "  0 - Valid sprite\n");
        fprintf(stderr, "  1 - Validation failure\n");
        fprintf(stderr, "  2 - Usage error\n");
        return 2;
    }

    const char* path = argv[1];

    if (!ValidateXPFile(path)) {
        return 1;  // Validation failure
    }

    printf("OK: %s passed validation\n", path);
    return 0;  // Success
}

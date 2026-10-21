#pragma once

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "upng.h"

// FL-4482: native consumers decode compiler-owned PNG pages through one
// fail-closed helper. Runtime JSON pixel arrays were deleted because universal
// offline compilation produces many pages and PNG is the canonical compact
// transport artifact.
static inline int Fl4482LoadAtlasPngRgba8(
    const char* path, int expected_width, int expected_height,
    uint8_t** out_pixels, size_t* out_size)
{
    if (out_pixels) *out_pixels = NULL;
    if (out_size) *out_size = 0;
    if (!path || !out_pixels || expected_width <= 0 || expected_height <= 0)
        return 0;
    upng_t* png = upng_new_from_file(path);
    if (!png)
        return 0;
    int ok = upng_get_error(png) == UPNG_EOK &&
        upng_header(png) == UPNG_EOK &&
        upng_get_format(png) == UPNG_RGBA8 &&
        (int)upng_get_width(png) == expected_width &&
        (int)upng_get_height(png) == expected_height &&
        upng_decode(png) == UPNG_EOK;
    const size_t expected_size = (size_t)expected_width * (size_t)expected_height * 4u;
    ok = ok && upng_get_size(png) == expected_size && upng_get_buffer(png) != NULL;
    uint8_t* pixels = ok ? (uint8_t*)malloc(expected_size) : NULL;
    if (ok && !pixels)
        ok = 0;
    if (ok)
        memcpy(pixels, upng_get_buffer(png), expected_size);
    upng_free(png);
    if (!ok)
    {
        free(pixels);
        return 0;
    }
    *out_pixels = pixels;
    if (out_size) *out_size = expected_size;
    return 1;
}

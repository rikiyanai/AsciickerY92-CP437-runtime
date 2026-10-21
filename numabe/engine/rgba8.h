// rgba8.h - Pixel Format Conversion API
//
// Purpose: Declares conversion functions that transform any A3D_ImageFormat
// (1-bit through 16-bit luminance, indexed, RGB, RGBA) into a packed 32-bit
// pixel suitable for GPU upload or display output.
//
// WHY three Convert variants exist:
//   Convert_UI32_AABBGGRR  - OpenGL byte-order (GL_RGBA on little-endian),
//                             used for glTexSubImage2D uploads
//   Convert_UI32_AARRGGBB  - Native display order (Windows DIB / macOS
//                             bitmap), used for software framebuffer blits
//   Convert_UL_AARRGGBB    - unsigned long variant for X11 XImage compat,
//                             because X11 pixel buffers use unsigned long
//                             (which is 8 bytes on 64-bit Linux)
//
// WHY ConvertLuminance exists separately:
//   Converts color images to a single luminance channel packed into the
//   high byte (LL000000 layout) with an optional constant RGB tint.
//   Used for directional lighting calculations where only brightness
//   matters, not full color. The weighted formula (2R + 3G + 1B) / 6
//   approximates perceptual luminance.

#pragma once

#include <stdint.h>
#include "platform/image_backend.h"

void Convert_UI32_AABBGGRR(uint32_t* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf);
void Convert_UI32_AARRGGBB(uint32_t* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf);
void Convert_UL_AARRGGBB(unsigned long* buf, A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf);

void ConvertLuminance_UI32_LLZZYYXX(uint32_t* buf, const uint8_t xyz[3], A3D_ImageFormat f, int w, int h, const void* data, int palsize, const void* palbuf);

// =============================================================
// a3d_wire.h — explicit little-endian wire primitives for the
//              A3D document codec (FL-4588)
// =============================================================
// The .a3d format is FROZEN as little-endian (operator ruling
// 2026-07-16). The legacy loaders used raw fread/fwrite of host
// integers, which happened to produce little-endian bytes only
// because every supported host so far was x86/ARM little-endian.
// That is a host-order dependence, not a contract — and the
// portable way to freeze the contract is to encode and decode
// the bytes EXPLICITLY, one byte at a time, so the same source
// works on any host (including MSVC, which does not define the
// GCC/Clang __BYTE_ORDER__ macros).
//
// Every multi-byte field the codec touches goes through these
// helpers:
//   - u32 / i32: four bytes, least-significant byte first;
//   - f32: the IEEE-754 single-precision bit pattern, stored as
//     a little-endian u32 (bit pattern moved with memcpy — the
//     only strict-aliasing-safe way to view float bits).
// Single bytes (u8) need no helper.
//
// These read/write EXACTLY the bytes the historical x86/macOS
// builds produced, so existing documents remain valid and the
// independent Python evaluator can mirror one exact definition:
//   u32 = b[0] | b[1]<<8 | b[2]<<16 | b[3]<<24.
// =============================================================
#pragma once

#include <stdio.h>
#include <stdint.h>
#include <string.h>

// Read one little-endian unsigned 16-bit value (terrain visual/height
// cells and the patch diagonal bitfield).
static inline bool A3dReadU16LE(FILE* f, uint16_t* out)
{
	unsigned char b[2];
	if (fread(b, 1, 2, f) != 2)
		return false;
	*out = (uint16_t)((uint16_t)b[0] | ((uint16_t)b[1] << 8));
	return true;
}

// Write one little-endian unsigned 16-bit value.
static inline bool A3dWriteU16LE(FILE* f, uint16_t value)
{
	unsigned char b[2];
	b[0] = (unsigned char)(value & 0xFF);
	b[1] = (unsigned char)((value >> 8) & 0xFF);
	return fwrite(b, 1, 2, f) == 2;
}

// Read one little-endian unsigned 32-bit value. Returns false on any
// short read; *out is unspecified on failure.
static inline bool A3dReadU32LE(FILE* f, uint32_t* out)
{
	unsigned char b[4];
	if (fread(b, 1, 4, f) != 4)
		return false;
	// Assemble least-significant byte first — the frozen wire order.
	*out = (uint32_t)b[0] | ((uint32_t)b[1] << 8) |
	       ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
	return true;
}

// Read one little-endian signed 32-bit value (two's complement — the
// same representation every supported compiler uses for int32_t).
static inline bool A3dReadI32LE(FILE* f, int32_t* out)
{
	uint32_t u = 0;
	if (!A3dReadU32LE(f, &u))
		return false;
	// Move the bits, don't convert the value: memcpy is the defined way
	// to reinterpret an unsigned bit pattern as signed.
	memcpy(out, &u, 4);
	return true;
}

// Read one IEEE-754 single-precision float stored as a little-endian
// 32-bit bit pattern.
static inline bool A3dReadF32LE(FILE* f, float* out)
{
	uint32_t bits = 0;
	if (!A3dReadU32LE(f, &bits))
		return false;
	memcpy(out, &bits, 4); // bit-pattern move, not a numeric conversion
	return true;
}

// Write one little-endian unsigned 32-bit value.
static inline bool A3dWriteU32LE(FILE* f, uint32_t value)
{
	unsigned char b[4];
	b[0] = (unsigned char)(value & 0xFF);         // least-significant first
	b[1] = (unsigned char)((value >> 8) & 0xFF);
	b[2] = (unsigned char)((value >> 16) & 0xFF);
	b[3] = (unsigned char)((value >> 24) & 0xFF);
	return fwrite(b, 1, 4, f) == 4;
}

// Write one little-endian signed 32-bit value (bit pattern of the
// two's-complement representation).
static inline bool A3dWriteI32LE(FILE* f, int32_t value)
{
	uint32_t u = 0;
	memcpy(&u, &value, 4);
	return A3dWriteU32LE(f, u);
}

// Write one IEEE-754 single-precision float as its little-endian
// 32-bit bit pattern.
static inline bool A3dWriteF32LE(FILE* f, float value)
{
	uint32_t bits = 0;
	memcpy(&bits, &value, 4);
	return A3dWriteU32LE(f, bits);
}

// Read one little-endian unsigned 64-bit value (mesh-instance
// persistent uid in world format version 6).
static inline bool A3dReadU64LE(FILE* f, uint64_t* out)
{
	unsigned char b[8];
	if (fread(b, 1, 8, f) != 8)
		return false;
	*out = 0;
	for (int index = 7; index >= 0; --index)
		*out = (*out << 8) | (uint64_t)b[index]; // LSB first on the wire
	return true;
}

// Write one little-endian unsigned 64-bit value.
static inline bool A3dWriteU64LE(FILE* f, uint64_t value)
{
	unsigned char b[8];
	for (int index = 0; index < 8; ++index)
		b[index] = (unsigned char)((value >> (index * 8)) & 0xFF);
	return fwrite(b, 1, 8, f) == 8;
}

// Read one IEEE-754 double-precision float (mesh-instance 4x4 transform
// components) stored as a little-endian 64-bit bit pattern.
static inline bool A3dReadF64LE(FILE* f, double* out)
{
	uint64_t bits = 0;
	if (!A3dReadU64LE(f, &bits))
		return false;
	memcpy(out, &bits, 8); // bit-pattern move, not a numeric conversion
	return true;
}

// Write one IEEE-754 double-precision float as its little-endian 64-bit
// bit pattern.
static inline bool A3dWriteF64LE(FILE* f, double value)
{
	uint64_t bits = 0;
	memcpy(&bits, &value, 8);
	return A3dWriteU64LE(f, bits);
}

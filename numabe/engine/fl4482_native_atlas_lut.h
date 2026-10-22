#pragma once
// =============================================================================
// fl4482_native_atlas_lut.h — one place that turns a glyph identity into a
//                             lookup-table slot number
// =============================================================================
// THE PROBLEM THIS SOLVES
// ----------------------
// Every character the renderer can draw has a numeric identity called a
// GlyphId. To actually draw one, the GPU needs to know where that character's
// picture lives inside the texture atlas. It finds out by reading a lookup
// table ("LUT"): a strip of pixels where each position holds the atlas address
// of one glyph.
//
// So something must answer: "for GlyphId N, which position in that strip?"
//
// That question used to be answered independently in more than one place, and
// two answers that drift apart mean the GPU reads the address of a DIFFERENT
// character than the one that was chosen -- the wrong letter appears on screen,
// with nothing reporting an error. This header exists so there is exactly one
// answer, shared by every consumer.
//
// WHY THE MAPPING IS NOT SIMPLY "SLOT = GLYPH ID"
// -----------------------------------------------
// GlyphIds are not packed together. They occupy two widely separated ranges:
//
//   * a LOW range starting at zero, holding the original character set and the
//     project's own authored glyphs;
//   * a UNIVERSAL range starting very high (over sixteen million), which mirrors
//     the Unicode scalar values so any Unicode character has a stable identity.
//
// Using the GlyphId directly as a slot number would demand a lookup strip with
// millions of mostly-empty positions. Instead the two live ranges are laid end
// to end: the low range keeps its own numbering, and the universal range is
// shifted down to sit immediately after it. The numeric gap between them
// disappears from the table without either range losing its identity.
//
// WHAT "FAIL CLOSED" MEANS HERE
// -----------------------------
// Any GlyphId that falls in neither range has no picture in the atlas. That
// includes the gap between the ranges and the reserved sentinel values used to
// mark failures (for example 0xFFFFFFFD, the "this cell could not be resolved"
// marker). For those this function returns UINT32_MAX, which is not a valid
// slot. Callers must skip such glyphs rather than draw them.
//
// Returning a plausible-looking slot for an unmapped identity would be the
// dangerous behaviour: the GPU would happily draw whatever picture happened to
// sit at that address, turning a missing glyph into a confidently wrong one.
//
// ADMISSION IS NOT DECIDED HERE
// -----------------------------
// This header does NOT decide which glyphs exist nor which are allowed to be
// drawn. That is settled by the generated binding registry included below,
// which is produced from the atlas build. This file only maps an
// already-admitted identity into the presenter's address space.
// =============================================================================

#include "generated/fl4482_native_atlas_bindings.h"

// Map a GlyphId to its slot in the atlas lookup table.
//
//   glyph_id : the identity of the character to be drawn.
//
// Returns the zero-based slot number, or UINT32_MAX when this identity has no
// place in the table and therefore must not be drawn.
static inline uint32_t Fl4482NativeAtlasLutSlot(uint32_t glyph_id)
{
	// The low range maps one-to-one: slot number equals glyph id.
	if (glyph_id < FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT)
		return glyph_id;

	// The universal range is shifted down so it begins right where the low
	// range ended, closing the numeric gap between the two.
	if (glyph_id >= FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE &&
		glyph_id < FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE + FL4482_NATIVE_ATLAS_UNIVERSAL_SCALAR_SPAN)
		return FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT +
			(glyph_id - FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE);

	// Neither range: the gap between them, anything above the universal range,
	// and every reserved failure sentinel. No slot exists, so say so.
	return UINT32_MAX;
}

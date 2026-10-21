// world_minimap_markers.h — Minimap marker storage and accessors
//
// Extracted from engine/world_serialization_a3d.cpp and world_internal.h.
// Owns the MinimapMarker struct, linked-list storage, and all marker
// load/save/query functions.
//
// [DATA-CONTRACT:A3D] Embedded minimap markers live after enemy generators
// in newer .a3d files. Older files may omit this section entirely.
//
// SEE ALSO:
// - engine/world_minimap_markers.cpp — implementation
// - engine/world_serialization_a3d.cpp — calls Load/SaveMinimapMarkers during A3D I/O

#pragma once

#include <stdint.h>
#include <stdio.h>

struct MinimapMarker
{
    char* name;
    char* label;
    float x;
    float y;
    uint8_t fg;
    uint8_t glyph;
    uint8_t type;
    MinimapMarker* next;
    MinimapMarker* prev;
};

// Storage lifecycle
void FreeMinimapMarkers();
void LoadMinimapMarkers(FILE* f);
bool SaveMinimapMarkers(FILE* f);

// ---------------------------------------------------------------------------
// FL-4588 document-codec cores (operator rulings 2026-07-16).
//
// The single hardened marker parser/serializer. LoadMinimapMarkers and
// SaveMinimapMarkers above are thin wrappers over these — there is exactly
// ONE parsing owner for this section. The list core:
//   - parses into a CALLER-OWNED list (the global registry is untouched),
//     so staged document validation never risks live state;
//   - returns explicit success; every failure (short read, over-budget
//     length or count, non-finite coordinate, nonzero reserved byte,
//     embedded NUL in text, unknown marker type, allocation failure)
//     frees the partial list — no partial state ever survives;
//   - preserves file order (append at tail) for byte-stable round trips.
//
// max_bytes is the section budget INCLUDING the 4-byte count prefix; every
// read is proven to fit BEFORE it consumes the stream. budget_is_exact
// additionally requires the section to consume the entire budget (used
// when the caller knows the exact section extent, e.g. the trailing
// section of a document). Callers with legacy files that OMIT the section
// must handle that omission explicitly before calling this parser.
bool LoadMinimapMarkerList(FILE* f, MinimapMarker** out_head,
                           MinimapMarker** out_tail, uint32_t* out_count,
                           long max_bytes, bool budget_is_exact);
// Serialize one marker list from an explicit head pointer (identical byte
// layout to the section the parser accepts; refuses strings the parser
// would reject so round trips stay byte-stable).
bool SaveMinimapMarkerList(FILE* f, const MinimapMarker* head);
// Free a caller-owned list produced by LoadMinimapMarkerList.
void FreeMinimapMarkerList(MinimapMarker* head);
// Transfer one fully-validated caller-owned marker list into the sole live
// registry. The old list is released only at this explicit adoption point.
void ReplaceMinimapMarkerList(MinimapMarker* head, MinimapMarker* tail);

// Linked-list traversal
MinimapMarker* GetFirstMinimapMarker();
MinimapMarker* GetNextMinimapMarker(MinimapMarker* marker);

// Field accessors (null-safe)
const char* GetMinimapMarkerName(MinimapMarker* marker);
const char* GetMinimapMarkerLabel(MinimapMarker* marker);
float GetMinimapMarkerX(MinimapMarker* marker);
float GetMinimapMarkerY(MinimapMarker* marker);
uint8_t GetMinimapMarkerFg(MinimapMarker* marker);
uint8_t GetMinimapMarkerGlyph(MinimapMarker* marker);
uint8_t GetMinimapMarkerType(MinimapMarker* marker);

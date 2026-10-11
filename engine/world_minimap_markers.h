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

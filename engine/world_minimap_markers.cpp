// world_minimap_markers.cpp — Minimap marker storage and accessors
//
// Extracted from engine/world_serialization_a3d.cpp (lines 107-233).
// Owns the linked-list storage, load/save, and field accessors for
// minimap markers embedded in .a3d files.
//
// SEE ALSO:
// - engine/world_minimap_markers.h — header
// - engine/world_serialization_a3d.cpp — calls Load/SaveMinimapMarkers during A3D I/O

#include "world_minimap_markers.h"

#include <stdlib.h>
#include <string.h>

// ── Linked-list storage ──

static MinimapMarker* minimap_marker_head = nullptr;
static MinimapMarker* minimap_marker_tail = nullptr;

static char* DuplicateMinimapMarkerString(const char* src)
{
    if (!src) return nullptr;
    size_t len = strlen(src);
    char* d = (char*)malloc(len + 1);
    if (d) memcpy(d, src, len + 1);
    return d;
}

static void AppendMinimapMarker(const char* name, const char* label,
    float x, float y, uint8_t fg, uint8_t glyph, uint8_t type)
{
    MinimapMarker* m = (MinimapMarker*)malloc(sizeof(MinimapMarker));
    m->name = DuplicateMinimapMarkerString(name);
    m->label = DuplicateMinimapMarkerString(label);
    m->x = x;
    m->y = y;
    m->fg = fg;
    m->glyph = glyph;
    m->type = type;
    m->next = 0;
    m->prev = minimap_marker_tail;

    if (minimap_marker_tail)
        minimap_marker_tail->next = m;
    else
        minimap_marker_head = m;
    minimap_marker_tail = m;
}

// ── Storage lifecycle ──

void FreeMinimapMarkers()
{
    MinimapMarker* m = minimap_marker_head;
    while (m)
    {
        MinimapMarker* n = m->next;
        if (m->name) free(m->name);
        if (m->label) free(m->label);
        free(m);
        m = n;
    }
    minimap_marker_head = 0;
    minimap_marker_tail = 0;
}

void LoadMinimapMarkers(FILE* f)
{
    FreeMinimapMarkers();
    uint32_t count = 0;
    if (fread(&count, sizeof(count), 1, f) != 1)
        return;

    for (uint32_t i = 0; i < count; i++)
    {
        uint32_t name_len = 0;
        if (fread(&name_len, sizeof(name_len), 1, f) != 1) break;
        char* name = (char*)malloc(name_len + 1);
        if (fread(name, 1, name_len, f) != name_len) { free(name); break; }
        name[name_len] = 0;

        uint32_t label_len = 0;
        if (fread(&label_len, sizeof(label_len), 1, f) != 1) { free(name); break; }
        char* label = (char*)malloc(label_len + 1);
        if (fread(label, 1, label_len, f) != label_len) { free(label); free(name); break; }
        label[label_len] = 0;

        float mx, my;
        uint8_t mfg, mglyph, mtype;
        if (fread(&mx, sizeof(mx), 1, f) != 1) { free(label); free(name); break; }
        if (fread(&my, sizeof(my), 1, f) != 1) { free(label); free(name); break; }
        if (fread(&mfg, sizeof(mfg), 1, f) != 1) { free(label); free(name); break; }
        if (fread(&mglyph, sizeof(mglyph), 1, f) != 1) { free(label); free(name); break; }
        if (fread(&mtype, sizeof(mtype), 1, f) != 1) { free(label); free(name); break; }
        uint8_t reserved = 0;
        if (fread(&reserved, sizeof(reserved), 1, f) != 1) { free(label); free(name); break; }

        AppendMinimapMarker(name, label, mx, my, mfg, mglyph, mtype);
        free(name);
        free(label);
    }
}

bool SaveMinimapMarkers(FILE* f)
{
    // Count markers
    uint32_t count = 0;
    for (MinimapMarker* m = minimap_marker_head; m; m = m->next)
        count++;

    if (fwrite(&count, sizeof(count), 1, f) != 1)
        return false;

    for (MinimapMarker* m = minimap_marker_head; m; m = m->next)
    {
        uint32_t name_len = m->name ? (uint32_t)strlen(m->name) : 0;
        uint32_t label_len = m->label ? (uint32_t)strlen(m->label) : 0;

        if (fwrite(&name_len, sizeof(name_len), 1, f) != 1) return false;
        if (name_len > 0 && fwrite(m->name, 1, name_len, f) != name_len) return false;

        if (fwrite(&label_len, sizeof(label_len), 1, f) != 1) return false;
        if (label_len > 0 && fwrite(m->label, 1, label_len, f) != label_len) return false;

        if (fwrite(&m->x, sizeof(m->x), 1, f) != 1) return false;
        if (fwrite(&m->y, sizeof(m->y), 1, f) != 1) return false;
        if (fwrite(&m->fg, sizeof(m->fg), 1, f) != 1) return false;
        if (fwrite(&m->glyph, sizeof(m->glyph), 1, f) != 1) return false;
        if (fwrite(&m->type, sizeof(m->type), 1, f) != 1) return false;
        uint8_t reserved = 0;
        if (fwrite(&reserved, sizeof(reserved), 1, f) != 1) return false;
    }
    return true;
}

// ── Linked-list traversal ──

MinimapMarker* GetFirstMinimapMarker() { return minimap_marker_head; }
MinimapMarker* GetNextMinimapMarker(MinimapMarker* marker) { return marker ? marker->next : nullptr; }

// ── Field accessors ──

const char* GetMinimapMarkerName(MinimapMarker* marker) { return marker ? marker->name : nullptr; }
const char* GetMinimapMarkerLabel(MinimapMarker* marker) { return marker ? marker->label : nullptr; }
float GetMinimapMarkerX(MinimapMarker* marker) { return marker ? marker->x : 0.0f; }
float GetMinimapMarkerY(MinimapMarker* marker) { return marker ? marker->y : 0.0f; }
uint8_t GetMinimapMarkerFg(MinimapMarker* marker) { return marker ? marker->fg : 0; }
uint8_t GetMinimapMarkerGlyph(MinimapMarker* marker) { return marker ? marker->glyph : 0; }
uint8_t GetMinimapMarkerType(MinimapMarker* marker) { return marker ? marker->type : 0; }

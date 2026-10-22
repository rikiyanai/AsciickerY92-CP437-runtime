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
#include <math.h> // isfinite: NaN/Inf marker coordinates are rejected (FL-4588)
#include "a3d_wire.h" // explicit little-endian wire primitives (FL-4588)

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

// FL-4588 single-parser rule (operator reviews 2026-07-16): this global
// loader no longer owns a parsing body. Order matters — PARSE FIRST into a
// caller-owned temporary list through the ONE hardened core, and only
// after complete success free the previous registry and swap the new list
// in. Malformed or truncated input therefore never destroys the document
// that was already loaded. Exactly one omission case exists: a legacy file
// that ENDS before this trailing section is explicitly treated as "this
// document has no markers" and CLEARS the registry — that clear is a
// decision about a validly-shorter document, not a parse-failure side
// effect.
void LoadMinimapMarkers(FILE* f)
{
    if (!f)
        return;

    // Measure the bytes remaining in the stream so the core can bound
    // every count and length against physical reality. An unmeasurable
    // stream is refused rather than parsed unbounded.
    long pos = ftell(f);
    if (pos < 0 || fseek(f, 0, SEEK_END) != 0)
        return;
    long end = ftell(f);
    if (end < 0 || fseek(f, pos, SEEK_SET) != 0 || end < pos)
        return;

    // Legacy .a3d files predate this trailing section and simply END here.
    // Explicit omission handling: clear the registry (an old document has
    // no markers) — this is a decision, not a parse failure.
    if (end == pos)
    {
        FreeMinimapMarkers();
        return;
    }

    // The marker section is the LAST section of the document, so the
    // remaining stream is an EXACT budget: unexplained trailing bytes
    // after the markers are a malformed file and fail the parse.
    MinimapMarker* head = nullptr;
    MinimapMarker* tail = nullptr;
    uint32_t count = 0;
    if (!LoadMinimapMarkerList(f, &head, &tail, &count, end - pos, true))
        return; // parse failed — the PREVIOUS registry survives untouched

    // Complete success: now (and only now) replace the live registry.
    FreeMinimapMarkers();
    minimap_marker_head = head;
    minimap_marker_tail = tail;
}

// FL-4588 single-serializer rule: the global save is now a thin wrapper
// over the ONE list serializer below, so temporary documents and the live
// registry can never drift onto different byte layouts.
bool SaveMinimapMarkers(FILE* f)
{
    return SaveMinimapMarkerList(f, minimap_marker_head);
}

// ===========================================================================
// FL-4588 document-codec cores (operator ruling 2026-07-16)
// ===========================================================================
// LoadMinimapMarkers above mutates the global marker list, returns void,
// and silently keeps whatever partial state it managed to read from a
// truncated stream. A staged document validation must instead parse a
// browser-uploaded byte stream into CALLER-OWNED temporary storage with an
// explicit success result, and only swap it into the live registry after
// the WHOLE document validates. These cores provide that:
//   - the global list is never touched;
//   - any short read, oversized length, or allocation failure frees the
//     partial list and returns false (fail closed, no partial state);
//   - list order preserves file order (append at tail), so a parse ->
//     serialize round trip is byte-stable.
// max_bytes bounds every length/count against the bytes actually remaining
// in the stream, so a hostile prefix cannot demand unbounded allocations.

// The marker wire ABI depends on 4-byte floats and 4-byte length words.
static_assert(sizeof(float) == 4, "A3D minimap-marker ABI requires 4-byte float");
static_assert(sizeof(uint32_t) == 4, "A3D minimap-marker ABI requires 4-byte length words");

// Free a caller-owned list produced by LoadMinimapMarkerList. Safe on NULL.
void FreeMinimapMarkerList(MinimapMarker* head)
{
    while (head)
    {
        MinimapMarker* next = head->next;
        if (head->name) free(head->name);
        if (head->label) free(head->label);
        free(head);
        head = next;
    }
}

void ReplaceMinimapMarkerList(MinimapMarker* head, MinimapMarker* tail)
{
    FreeMinimapMarkers();
    minimap_marker_head = head;
    minimap_marker_tail = tail;
}

// Frozen authoring bound for marker name/label text (operator review
// 2026-07-16). Uploaded documents with longer strings are rejected rather
// than trusted; the editor never authors anything near this length.
static const uint32_t kMinimapMarkerTextMax = 1024;

// Explicit bounded byte reader: every read method proves the requested
// bytes fit inside the remaining budget BEFORE consuming the stream, so a
// declared budget can never be overdrawn and no signed arithmetic ever
// goes negative. The budget covers the WHOLE marker section including the
// 4-byte count prefix.
struct MarkerSectionReader
{
    FILE* f;
    unsigned long remaining; // bytes the section may still consume

    bool U32(uint32_t* out)
    {
        if (remaining < 4) return false;   // budget check BEFORE the read
        if (!A3dReadU32LE(f, out)) return false; // explicit little-endian
        remaining -= 4;
        return true;
    }
    bool F32(float* out)
    {
        if (remaining < 4) return false;
        if (!A3dReadF32LE(f, out)) return false; // explicit little-endian
        remaining -= 4;
        return true;
    }
    bool U8(uint8_t* out)
    {
        if (remaining < 1) return false;
        if (fread(out, 1, 1, f) != 1) return false;
        remaining -= 1;
        return true;
    }
    // Length-prefixed text into a fresh heap buffer. Empty strings are
    // legal. Rejects lengths that exceed the remaining budget (physically
    // impossible) or the frozen authoring bound (never a real document),
    // and rejects embedded NUL bytes: the serializer measures strings with
    // strlen, so a NUL inside the text would silently truncate on re-save
    // and break the byte-stable round trip this codec guarantees.
    bool Text(char** out)
    {
        *out = nullptr;
        uint32_t len = 0;
        if (!U32(&len)) return false;
        if (len > remaining || len > kMinimapMarkerTextMax) return false;
        char* text = (char*)malloc((size_t)len + 1);
        if (!text) return false;
        if (len && fread(text, 1, len, f) != len)
        {
            free(text);
            return false;
        }
        if (len && memchr(text, 0, len))
        {
            free(text);
            return false;
        }
        text[len] = 0;
        remaining -= len;
        *out = text;
        return true;
    }
};

// Frozen valid marker kinds, from the AUTHORITATIVE format enum in
// addons/io_asciicker/scene/a3d_format.py (A3DMinimapMarker): TYPE_CUSTOM=0,
// TYPE_BUILDING=1, TYPE_REGION=2. The renderer special-cases only
// type==1 (engine/render/minimap_renderer.cpp:268/335) and treats the
// rest as plain markers, but the tracked corpus authors TYPE_REGION too
// (game_map_y8.a3d carries a type=2 'spawn' region marker — FL-4588
// census 2026-07-16, which caught an earlier over-narrow {0,1} freeze
// here). An uploaded document may carry 0, 1, or 2; nothing else is
// authored anywhere.
static const uint8_t kMinimapMarkerTypeMax = 2;

bool LoadMinimapMarkerList(FILE* f, MinimapMarker** out_head,
                           MinimapMarker** out_tail, uint32_t* out_count,
                           long max_bytes, bool budget_is_exact)
{
    if (!f || !out_head || !out_tail || !out_count)
        return false;
    *out_head = nullptr;
    *out_tail = nullptr;
    *out_count = 0;

    // max_bytes is the total section budget INCLUDING the count prefix.
    // A negative or sub-prefix budget is malformed input from the caller,
    // rejected before any stream byte is consumed. (There is deliberately
    // no "unknown budget" mode: every caller can and must measure.)
    if (max_bytes < 4)
        return false;

    MarkerSectionReader reader{f, (unsigned long)max_bytes};
    uint32_t count = 0;
    if (!reader.U32(&count))
        return false;

    // Minimum marker record: two zero-length strings (4+4) + x,y floats
    // (8) + fg/glyph/type/reserved (4) = 20 bytes. A count whose records
    // cannot physically fit in the remaining budget is malformed; this
    // also bounds the loop below (no count overflow into allocations).
    if ((unsigned long)count > reader.remaining / 20)
        return false;

    MinimapMarker* head = nullptr;
    MinimapMarker* tail = nullptr;
    for (uint32_t i = 0; i < count; i++)
    {
        char* name = nullptr;
        char* label = nullptr;
        float x = 0.0f, y = 0.0f;
        uint8_t fg = 0, glyph = 0, type = 0, reserved = 0;
        // Every field cut is budget-checked inside the reader. Validation
        // beyond structure: coordinates must be finite (NaN/Inf is never a
        // valid map position) and the reserved byte must be zero exactly
        // as SaveMinimapMarkers writes it.
        if (!reader.Text(&name) ||
            !reader.Text(&label) ||
            !reader.F32(&x) || !reader.F32(&y) ||
            !reader.U8(&fg) || !reader.U8(&glyph) ||
            !reader.U8(&type) || !reader.U8(&reserved) ||
            !isfinite(x) || !isfinite(y) || reserved != 0 ||
            type > kMinimapMarkerTypeMax)
        {
            if (name) free(name);
            if (label) free(label);
            FreeMinimapMarkerList(head);
            return false;
        }

        MinimapMarker* m = (MinimapMarker*)malloc(sizeof(MinimapMarker));
        if (!m)
        {
            free(name);
            free(label);
            FreeMinimapMarkerList(head);
            return false;
        }
        // Ownership of name/label moves into the node (freed by
        // FreeMinimapMarkerList).
        m->name = name;
        m->label = label;
        m->x = x;
        m->y = y;
        m->fg = fg;
        m->glyph = glyph;
        m->type = type;
        m->next = nullptr;
        m->prev = tail;
        if (tail)
            tail->next = m;
        else
            head = m;
        tail = m;
    }

    // When the caller declared an EXACT section budget, every budgeted
    // byte must have been consumed: unexplained trailing bytes are a
    // malformed document, not slack. (Legacy files that OMIT this whole
    // trailing section are handled explicitly by the outer document
    // version path — they never reach this parser with a budget.)
    if (budget_is_exact && reader.remaining != 0)
    {
        FreeMinimapMarkerList(head);
        return false;
    }

    *out_head = head;
    *out_tail = tail;
    *out_count = count;
    return true;
}

bool SaveMinimapMarkerList(FILE* f, const MinimapMarker* head)
{
    if (!f)
        return false;

    // Preflight pass: validate EVERY node before any byte is emitted, so a
    // late invalid node cannot leave a partial supposedly-canonical
    // document already written to the stream. The serializer refuses
    // everything its own parser would reject: overlong strings (which also
    // guards the strlen -> uint32_t narrowing), non-finite coordinates,
    // unknown marker types, and a count that overflows the 4-byte prefix.
    unsigned long count_long = 0;
    for (const MinimapMarker* m = head; m; m = m->next)
    {
        const size_t name_size = m->name ? strlen(m->name) : 0;
        const size_t label_size = m->label ? strlen(m->label) : 0;
        if (name_size > kMinimapMarkerTextMax || label_size > kMinimapMarkerTextMax)
            return false;
        if (!isfinite(m->x) || !isfinite(m->y) || m->type > kMinimapMarkerTypeMax)
            return false;
        if (++count_long > 0xFFFFFFFFul)
            return false;
    }
    if (!A3dWriteU32LE(f, (uint32_t)count_long))
        return false;

    // Identical byte layout to the section the parser accepts, from an
    // explicit head pointer so temporary documents can serialize too.
    // Multi-byte fields are written explicitly little-endian (a3d_wire.h);
    // strings and single bytes are order-free.
    for (const MinimapMarker* m = head; m; m = m->next)
    {
        uint32_t name_len = m->name ? (uint32_t)strlen(m->name) : 0;
        uint32_t label_len = m->label ? (uint32_t)strlen(m->label) : 0;

        if (!A3dWriteU32LE(f, name_len)) return false;
        if (name_len > 0 && fwrite(m->name, 1, name_len, f) != name_len) return false;

        if (!A3dWriteU32LE(f, label_len)) return false;
        if (label_len > 0 && fwrite(m->label, 1, label_len, f) != label_len) return false;

        if (!A3dWriteF32LE(f, m->x)) return false;
        if (!A3dWriteF32LE(f, m->y)) return false;
        if (fwrite(&m->fg, 1, 1, f) != 1) return false;
        if (fwrite(&m->glyph, 1, 1, f) != 1) return false;
        if (fwrite(&m->type, 1, 1, f) != 1) return false;
        uint8_t reserved = 0;
        if (fwrite(&reserved, 1, 1, f) != 1) return false;
    }
    return !ferror(f);
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

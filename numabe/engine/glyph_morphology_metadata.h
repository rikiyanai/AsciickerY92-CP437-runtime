// glyph_morphology_metadata.h -- FL-4208/FL-4260 glyph morphology metadata owner
//
// This table is keyed by GlyphId. It is candidate metadata only: it does not admit
// glyphs, select visible cells, or override the render resolver. Per-cell facts are
// produced by render/resolve and consumed separately.

#pragma once

#include "glyph_id.h"
#include <stdint.h>

// FL-4512: this legacy side-contact mask is candidate metadata only. It is not
// the stroke-port graph required to connect diagonal and curved contour chains.
#define GLYPH_MORPH_EDGE_CONTACT_N 0x01u
#define GLYPH_MORPH_EDGE_CONTACT_E 0x02u
#define GLYPH_MORPH_EDGE_CONTACT_S 0x04u
#define GLYPH_MORPH_EDGE_CONTACT_W 0x08u

#ifdef __cplusplus
extern "C" {
#endif

typedef struct GlyphMorphologyEntry {
    GlyphId glyph_id;
    char role[64];
    char roles[160];
    char stroke_class[48];
    char direction_bucket[48];
    char convexity_bucket[48];
    char concavity_bucket[48];
    char density_bucket[48];
    char terminal_shape[48];
    char rejected_combo_status[48];
    char repertoire[48];
    char source_owner[48];
    uint8_t edge_contact_mask;
    int palette_entry_id;
    int candidate_receipt_id;
    int candidate_receipt_required;
} GlyphMorphologyEntry;

typedef struct GlyphMorphologyTable {
    GlyphMorphologyEntry* entries;
    int entry_count;
    int catalog_count;
    int foliage_count;
    int water_count;
    char loaded_sources[256];
} GlyphMorphologyTable;

int glyph_morphology_load_defaults(GlyphMorphologyTable* table, char* errbuf, int errbuf_size);
void glyph_morphology_free(GlyphMorphologyTable* table);
const GlyphMorphologyEntry* glyph_morphology_lookup(const GlyphMorphologyTable* table, GlyphId glyph_id);

#ifdef __cplusplus
}
#endif

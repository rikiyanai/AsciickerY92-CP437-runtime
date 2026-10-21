// glyph_morphology_metadata.cpp -- FL-4208/FL-4260 glyph metadata loader

#include "glyph_morphology_metadata.h"
#include "third_party/cjson/cJSON.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char* kMorphologyCatalogPath = "assets/glyphs/generated/material.morphology.v2.shape_catalog.json";
static const char* kFoliagePalettePath = "assets/glyphs/fixtures/fl4208_foliage_palette_v1.json";
static const char* kWaterRoleManifestPath = "assets/glyphs/generated/fl4208_water_role_scoring_manifest.json";

static void morph_set_error(char* errbuf, int errbuf_size, const char* msg)
{
    if (errbuf && errbuf_size > 0)
        snprintf(errbuf, (size_t)errbuf_size, "%s", msg ? msg : "");
}

static int morph_read_file(const char* path, char** out_text)
{
    if (!path || !out_text)
        return 0;
    *out_text = NULL;
    FILE* f = fopen(path, "rb");
    if (!f)
        return 0;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0 || sz > 16 * 1024 * 1024)
    {
        fclose(f);
        return 0;
    }
    char* buf = (char*)malloc((size_t)sz + 1);
    if (!buf)
    {
        fclose(f);
        return 0;
    }
    size_t rd = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    if (rd != (size_t)sz)
    {
        free(buf);
        return 0;
    }
    buf[sz] = 0;
    *out_text = buf;
    return 1;
}

static void morph_copy(char* dst, size_t cap, const char* src)
{
    if (!dst || cap == 0)
        return;
    snprintf(dst, cap, "%s", src ? src : "");
}

static void morph_set_concavity_from_convexity(GlyphMorphologyEntry* e)
{
    if (!e || e->concavity_bucket[0] || !e->convexity_bucket[0])
        return;
    if (strcmp(e->convexity_bucket, "inward") == 0 ||
        strcmp(e->convexity_bucket, "concave") == 0)
        morph_copy(e->concavity_bucket, sizeof(e->concavity_bucket), "concave");
    else if (strcmp(e->convexity_bucket, "outward") == 0 ||
             strcmp(e->convexity_bucket, "convex") == 0)
        morph_copy(e->concavity_bucket, sizeof(e->concavity_bucket), "convex");
    else if (strcmp(e->convexity_bucket, "flat") == 0)
        morph_copy(e->concavity_bucket, sizeof(e->concavity_bucket), "flat");
}

static double morph_number_field(cJSON* obj, const char* key, double fallback)
{
    if (!cJSON_IsObject(obj) || !key)
        return fallback;
    cJSON* v = cJSON_GetObjectItem(obj, key);
    if (!cJSON_IsNumber(v))
        return fallback;
    return cJSON_GetNumberValue(v);
}

static uint8_t morph_edge_contact_from_weights(double top, double right,
                                               double bottom, double left)
{
    const double threshold = 0.08;
    uint8_t mask = 0;
    if (top >= threshold) mask |= GLYPH_MORPH_EDGE_CONTACT_N;
    if (right >= threshold) mask |= GLYPH_MORPH_EDGE_CONTACT_E;
    if (bottom >= threshold) mask |= GLYPH_MORPH_EDGE_CONTACT_S;
    if (left >= threshold) mask |= GLYPH_MORPH_EDGE_CONTACT_W;
    return mask;
}

static uint8_t morph_edge_contact_from_direction(const char* direction)
{
    if (!direction || !direction[0])
        return 0;
    if (strstr(direction, "left"))
        return GLYPH_MORPH_EDGE_CONTACT_W;
    if (strstr(direction, "right"))
        return GLYPH_MORPH_EDGE_CONTACT_E;
    if (strstr(direction, "curl"))
        return GLYPH_MORPH_EDGE_CONTACT_E | GLYPH_MORPH_EDGE_CONTACT_W;
    return GLYPH_MORPH_EDGE_CONTACT_N | GLYPH_MORPH_EDGE_CONTACT_S;
}

static void morph_fill_edge_contact_from_terminal(GlyphMorphologyEntry* e)
{
    if (!e || e->edge_contact_mask)
        return;
    uint8_t mask = morph_edge_contact_from_direction(e->direction_bucket);
    if (!mask && e->terminal_shape[0])
        mask = GLYPH_MORPH_EDGE_CONTACT_N | GLYPH_MORPH_EDGE_CONTACT_S;
    e->edge_contact_mask = mask;
}

static int morph_cmp_gid(const void* a, const void* b)
{
    const GlyphMorphologyEntry* ea = (const GlyphMorphologyEntry*)a;
    const GlyphMorphologyEntry* eb = (const GlyphMorphologyEntry*)b;
    if (ea->glyph_id < eb->glyph_id) return -1;
    if (ea->glyph_id > eb->glyph_id) return 1;
    return 0;
}

static GlyphMorphologyEntry* morph_find_linear(GlyphMorphologyTable* table, GlyphId gid)
{
    if (!table || gid == GLYPH_ID_NONE)
        return NULL;
    for (int i = 0; i < table->entry_count; i++)
        if (table->entries[i].glyph_id == gid)
            return &table->entries[i];
    return NULL;
}

static GlyphMorphologyEntry* morph_upsert(GlyphMorphologyTable* table, GlyphId gid)
{
    if (!table || gid == GLYPH_ID_NONE)
        return NULL;
    GlyphMorphologyEntry* existing = morph_find_linear(table, gid);
    if (existing)
        return existing;
    GlyphMorphologyEntry* next = (GlyphMorphologyEntry*)realloc(
        table->entries, sizeof(GlyphMorphologyEntry) * (size_t)(table->entry_count + 1));
    if (!next)
        return NULL;
    table->entries = next;
    GlyphMorphologyEntry* e = &table->entries[table->entry_count++];
    memset(e, 0, sizeof(*e));
    e->glyph_id = gid;
    return e;
}

static void morph_join_string_array(cJSON* arr, char* out, size_t out_cap)
{
    if (!out || out_cap == 0)
        return;
    out[0] = 0;
    if (!cJSON_IsArray(arr))
        return;
    int n = cJSON_GetArraySize(arr);
    for (int i = 0; i < n; i++)
    {
        cJSON* v = cJSON_GetArrayItem(arr, i);
        const char* s = cJSON_IsString(v) ? cJSON_GetStringValue(v) : "";
        if (!s || !*s)
            continue;
        if (out[0])
            strncat(out, ", ", out_cap - strlen(out) - 1);
        strncat(out, s, out_cap - strlen(out) - 1);
    }
}

static int morph_load_catalog(GlyphMorphologyTable* table)
{
    char* text = NULL;
    if (!morph_read_file(kMorphologyCatalogPath, &text))
        return 0;
    cJSON* root = cJSON_Parse(text);
    free(text);
    if (!root)
        return 0;
    cJSON* entries = cJSON_GetObjectItem(root, "entries");
    if (!cJSON_IsArray(entries))
    {
        cJSON_Delete(root);
        return 0;
    }
    cJSON* item = NULL;
    cJSON_ArrayForEach(item, entries)
    {
        if (!cJSON_IsObject(item))
            continue;
        cJSON* gid_j = cJSON_GetObjectItem(item, "glyph_id");
        if (!cJSON_IsNumber(gid_j))
            continue;
        GlyphMorphologyEntry* e = morph_upsert(table, (GlyphId)cJSON_GetNumberValue(gid_j));
        if (!e)
            continue;
        cJSON* rep = cJSON_GetObjectItem(item, "repertoire");
        cJSON* stroke = cJSON_GetObjectItem(item, "stroke_class");
        if (cJSON_IsString(rep))
            morph_copy(e->repertoire, sizeof(e->repertoire), cJSON_GetStringValue(rep));
        if (cJSON_IsString(stroke))
            morph_copy(e->stroke_class, sizeof(e->stroke_class), cJSON_GetStringValue(stroke));
        e->edge_contact_mask |= morph_edge_contact_from_weights(
            morph_number_field(item, "top_weight", 0.0),
            morph_number_field(item, "right_weight", 0.0),
            morph_number_field(item, "bottom_weight", 0.0),
            morph_number_field(item, "left_weight", 0.0));
        morph_join_string_array(cJSON_GetObjectItem(item, "roles"), e->roles, sizeof(e->roles));
        if (!e->role[0] && e->roles[0])
        {
            const char* comma = strchr(e->roles, ',');
            size_t len = comma ? (size_t)(comma - e->roles) : strlen(e->roles);
            if (len >= sizeof(e->role)) len = sizeof(e->role) - 1;
            memcpy(e->role, e->roles, len);
            e->role[len] = 0;
        }
        if (!e->source_owner[0])
            morph_copy(e->source_owner, sizeof(e->source_owner), "material.morphology.v2");
        table->catalog_count++;
    }
    cJSON_Delete(root);
    return 1;
}

static int morph_load_foliage(GlyphMorphologyTable* table)
{
    char* text = NULL;
    if (!morph_read_file(kFoliagePalettePath, &text))
        return 0;
    cJSON* root = cJSON_Parse(text);
    free(text);
    if (!root)
        return 0;
    cJSON* palette = cJSON_GetObjectItem(root, "palette");
    if (!cJSON_IsArray(palette))
    {
        cJSON_Delete(root);
        return 0;
    }
    cJSON* row = NULL;
    cJSON_ArrayForEach(row, palette)
    {
        if (!cJSON_IsObject(row))
            continue;
        cJSON* gid_j = cJSON_GetObjectItem(row, "glyph_id");
        if (!cJSON_IsNumber(gid_j))
            continue;
        GlyphMorphologyEntry* e = morph_upsert(table, (GlyphId)cJSON_GetNumberValue(gid_j));
        if (!e)
            continue;
        cJSON* v = cJSON_GetObjectItem(row, "role");
        if (cJSON_IsString(v)) morph_copy(e->role, sizeof(e->role), cJSON_GetStringValue(v));
        v = cJSON_GetObjectItem(row, "density_bucket");
        if (cJSON_IsString(v)) morph_copy(e->density_bucket, sizeof(e->density_bucket), cJSON_GetStringValue(v));
        v = cJSON_GetObjectItem(row, "direction_bucket");
        if (cJSON_IsString(v)) morph_copy(e->direction_bucket, sizeof(e->direction_bucket), cJSON_GetStringValue(v));
        v = cJSON_GetObjectItem(row, "convexity_bucket");
        if (cJSON_IsString(v)) {
            morph_copy(e->convexity_bucket, sizeof(e->convexity_bucket), cJSON_GetStringValue(v));
            morph_set_concavity_from_convexity(e);
        }
        v = cJSON_GetObjectItem(row, "terminal_shape");
        if (cJSON_IsString(v)) morph_copy(e->terminal_shape, sizeof(e->terminal_shape), cJSON_GetStringValue(v));
        morph_fill_edge_contact_from_terminal(e);
        v = cJSON_GetObjectItem(row, "rejected_combo_status");
        if (cJSON_IsString(v)) morph_copy(e->rejected_combo_status, sizeof(e->rejected_combo_status), cJSON_GetStringValue(v));
        v = cJSON_GetObjectItem(row, "source_owner");
        morph_copy(e->source_owner, sizeof(e->source_owner), cJSON_IsString(v) ? cJSON_GetStringValue(v) : "FL-4208");
        v = cJSON_GetObjectItem(row, "palette_entry_id");
        if (cJSON_IsNumber(v)) e->palette_entry_id = (int)cJSON_GetNumberValue(v);
        v = cJSON_GetObjectItem(row, "candidate_receipt_id");
        if (cJSON_IsNumber(v)) e->candidate_receipt_id = (int)cJSON_GetNumberValue(v);
        v = cJSON_GetObjectItem(row, "candidate_receipt_required");
        if (cJSON_IsBool(v)) e->candidate_receipt_required = cJSON_IsTrue(v) ? 1 : 0;
        table->foliage_count++;
    }
    cJSON_Delete(root);
    return 1;
}

static const char* morph_water_direction_from_role(const char* role)
{
    if (!role) return "";
    if (strstr(role, "left")) return "left";
    if (strstr(role, "right")) return "right";
    if (strstr(role, "curl")) return "curl";
    return "neutral";
}

static int morph_load_water(GlyphMorphologyTable* table)
{
    char* text = NULL;
    if (!morph_read_file(kWaterRoleManifestPath, &text))
        return 0;
    cJSON* root = cJSON_Parse(text);
    free(text);
    if (!root)
        return 0;
    cJSON* roles = cJSON_GetObjectItem(root, "extended_glyph_codepoints");
    if (!cJSON_IsObject(roles))
    {
        cJSON_Delete(root);
        return 0;
    }
    for (cJSON* it = roles->child; it; it = it->next)
    {
        if (!it->string || !cJSON_IsObject(it))
            continue;
        GlyphId gid = (GlyphId)strtoul(it->string, NULL, 10);
        if (gid == GLYPH_ID_NONE)
            continue;
        GlyphMorphologyEntry* e = morph_upsert(table, gid);
        if (!e)
            continue;
        cJSON* role = cJSON_GetObjectItem(it, "role");
        const char* role_s = cJSON_IsString(role) ? cJSON_GetStringValue(role) : "";
        morph_copy(e->role, sizeof(e->role), role_s);
        morph_copy(e->roles, sizeof(e->roles), role_s);
        morph_copy(e->source_owner, sizeof(e->source_owner), "FL-4208-water");
        if (!e->direction_bucket[0])
            morph_copy(e->direction_bucket, sizeof(e->direction_bucket), morph_water_direction_from_role(role_s));
        morph_fill_edge_contact_from_terminal(e);
        if (!e->rejected_combo_status[0])
            morph_copy(e->rejected_combo_status, sizeof(e->rejected_combo_status), "accepted");
        table->water_count++;
    }
    cJSON_Delete(root);
    return 1;
}

int glyph_morphology_load_defaults(GlyphMorphologyTable* table, char* errbuf, int errbuf_size)
{
    if (!table)
    {
        morph_set_error(errbuf, errbuf_size, "null morphology table");
        return 0;
    }
    glyph_morphology_free(table);
    memset(table, 0, sizeof(*table));
    int ok_count = 0;
    if (morph_load_catalog(table)) ok_count++;
    if (morph_load_foliage(table)) ok_count++;
    if (morph_load_water(table)) ok_count++;
    if (table->entry_count > 1)
        qsort(table->entries, (size_t)table->entry_count, sizeof(GlyphMorphologyEntry), morph_cmp_gid);
    snprintf(table->loaded_sources, sizeof(table->loaded_sources),
        "catalog=%d foliage=%d water=%d", table->catalog_count, table->foliage_count, table->water_count);
    if (table->entry_count <= 0)
    {
        morph_set_error(errbuf, errbuf_size, "no glyph morphology metadata loaded");
        return 0;
    }
    if (ok_count <= 0)
    {
        morph_set_error(errbuf, errbuf_size, "all glyph morphology sources failed");
        return 0;
    }
    morph_set_error(errbuf, errbuf_size, table->loaded_sources);
    return 1;
}

void glyph_morphology_free(GlyphMorphologyTable* table)
{
    if (!table)
        return;
    free(table->entries);
    table->entries = NULL;
    table->entry_count = 0;
    table->catalog_count = 0;
    table->foliage_count = 0;
    table->water_count = 0;
    table->loaded_sources[0] = 0;
}

const GlyphMorphologyEntry* glyph_morphology_lookup(const GlyphMorphologyTable* table, GlyphId glyph_id)
{
    if (!table || !table->entries || table->entry_count <= 0 || glyph_id == GLYPH_ID_NONE)
        return NULL;
    int lo = 0;
    int hi = table->entry_count - 1;
    while (lo <= hi)
    {
        int mid = lo + (hi - lo) / 2;
        GlyphId mg = table->entries[mid].glyph_id;
        if (mg == glyph_id)
            return &table->entries[mid];
        if (mg < glyph_id)
            lo = mid + 1;
        else
            hi = mid - 1;
    }
    return NULL;
}

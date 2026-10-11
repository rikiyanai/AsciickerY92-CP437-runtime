// FL-4131 Phase 2: Material sidecar parser implementation.
// See engine/material_sidecar.h for contract.

#include "material_sidecar.h"
#include "glyph_manifest.h"
#include "third_party/cjson/cJSON.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

static int parse_cell(cJSON* obj, MaterialSidecarCell* out, char* errbuf, int errbuf_size)
{
	if (!cJSON_IsObject(obj)) {
		snprintf(errbuf, errbuf_size, "cell entry not an object");
		return 1;
	}
	cJSON* elev = cJSON_GetObjectItem(obj, "elev");
	cJSON* shade = cJSON_GetObjectItem(obj, "shade");
	cJSON* glyph_id = cJSON_GetObjectItem(obj, "glyph_id");
	if (!elev || !shade || !glyph_id) {
		snprintf(errbuf, errbuf_size, "cell missing elev/shade/glyph_id");
		return 1;
	}
	if (!cJSON_IsNumber(elev) || !cJSON_IsNumber(shade) || !cJSON_IsNumber(glyph_id)) {
		snprintf(errbuf, errbuf_size, "cell elev/shade/glyph_id must be numbers");
		return 1;
	}
	int e = (int)elev->valuedouble;
	int s = (int)shade->valuedouble;
	unsigned int gid = (unsigned int)glyph_id->valuedouble;
	if (e < 0 || e > 3) {
		snprintf(errbuf, errbuf_size, "cell elev %d out of range [0,3]", e);
		return 1;
	}
	if (s < 0 || s > 15) {
		snprintf(errbuf, errbuf_size, "cell shade %d out of range [0,15]", s);
		return 1;
	}
	if (gid <= 255) {
		snprintf(errbuf, errbuf_size, "cell glyph_id %u is CP437, expected extended (>255)", gid);
		return 1;
	}
	if (glyph_id_is_sentinel((GlyphId)gid)) {
		snprintf(errbuf, errbuf_size, "cell glyph_id %u is sentinel (NONE/UNRESOLVED)", gid);
		return 1;
	}
	out->elev = (uint8_t)e;
	out->shade = (uint8_t)s;
	out->glyph_id = (GlyphId)gid;
	return 0;
}

static int parse_entry(cJSON* obj, MaterialSidecarEntry* out, char* errbuf, int errbuf_size)
{
	if (!cJSON_IsObject(obj)) {
		snprintf(errbuf, errbuf_size, "material entry not an object");
		return 1;
	}
	cJSON* mat_id = cJSON_GetObjectItem(obj, "material_id");
	cJSON* cells_arr = cJSON_GetObjectItem(obj, "cells");
	if (!mat_id || !cells_arr) {
		snprintf(errbuf, errbuf_size, "entry missing material_id or cells");
		return 1;
	}
	if (!cJSON_IsNumber(mat_id)) {
		snprintf(errbuf, errbuf_size, "material_id must be number");
		return 1;
	}
	int mid = (int)mat_id->valuedouble;
	if (mid < 0 || mid > 255) {
		snprintf(errbuf, errbuf_size, "material_id %d out of range [0,255]", mid);
		return 1;
	}
	if (!cJSON_IsArray(cells_arr)) {
		snprintf(errbuf, errbuf_size, "cells must be array");
		return 1;
	}
	int n = cJSON_GetArraySize(cells_arr);
	if (n <= 0) {
		snprintf(errbuf, errbuf_size, "cells array empty");
		return 1;
	}
	out->material_id = mid;
	out->cells = (MaterialSidecarCell*)calloc((size_t)n, sizeof(MaterialSidecarCell));
	if (!out->cells) {
		snprintf(errbuf, errbuf_size, "allocation failed for %d cells", n);
		return 1;
	}
	out->cell_count = n;
	for (int i = 0; i < n; i++) {
		cJSON* cell = cJSON_GetArrayItem(cells_arr, i);
		if (parse_cell(cell, &out->cells[i], errbuf, errbuf_size) != 0) {
			return 1;
		}
	}
	return 0;
}

int material_sidecar_parse(const char* json_text, MaterialSidecar* out, char* errbuf, int errbuf_size)
{
	if (!json_text || !out) {
		snprintf(errbuf ? errbuf : (char*)"", errbuf_size ? errbuf_size : 1, "null input");
		return 1;
	}
	memset(out, 0, sizeof(MaterialSidecar));
	cJSON* root = cJSON_Parse(json_text);
	if (!root) {
		snprintf(errbuf ? errbuf : (char*)"", errbuf_size ? errbuf_size : 1, "JSON parse failed");
		return 1;
	}
	// sidecar_version
	cJSON* v = cJSON_GetObjectItem(root, "sidecar_version");
	if (!v || !cJSON_IsNumber(v)) {
		snprintf(errbuf, errbuf_size, "missing sidecar_version");
		cJSON_Delete(root);
		return 1;
	}
	out->sidecar_version = (int)v->valuedouble;
	// profile_kind
	cJSON* pk = cJSON_GetObjectItem(root, "profile_kind");
	if (!pk || !cJSON_IsString(pk)) {
		snprintf(errbuf, errbuf_size, "missing profile_kind");
		cJSON_Delete(root);
		return 1;
	}
	strncpy(out->profile_kind, pk->valuestring, sizeof(out->profile_kind) - 1);
	// content_pack_id
	cJSON* cpid = cJSON_GetObjectItem(root, "content_pack_id");
	if (!cpid || !cJSON_IsString(cpid)) {
		snprintf(errbuf, errbuf_size, "missing content_pack_id");
		cJSON_Delete(root);
		return 1;
	}
	strncpy(out->content_pack_id, cpid->valuestring, sizeof(out->content_pack_id) - 1);
	// glyph_manifest_hash
	cJSON* hash = cJSON_GetObjectItem(root, "glyph_manifest_hash");
	if (!hash || !cJSON_IsString(hash)) {
		snprintf(errbuf, errbuf_size, "missing glyph_manifest_hash");
		cJSON_Delete(root);
		return 1;
	}
	strncpy(out->glyph_manifest_hash, hash->valuestring, sizeof(out->glyph_manifest_hash) - 1);
	// glyph_manifest_path
	cJSON* path = cJSON_GetObjectItem(root, "glyph_manifest_path");
	if (!path || !cJSON_IsString(path)) {
		snprintf(errbuf, errbuf_size, "missing glyph_manifest_path");
		cJSON_Delete(root);
		return 1;
	}
	strncpy(out->glyph_manifest_path, path->valuestring, sizeof(out->glyph_manifest_path) - 1);
	// material_entries
	cJSON* entries = cJSON_GetObjectItem(root, "material_entries");
	if (!entries || !cJSON_IsArray(entries)) {
		snprintf(errbuf, errbuf_size, "missing material_entries array");
		cJSON_Delete(root);
		return 1;
	}
	int n = cJSON_GetArraySize(entries);
	if (n <= 0) {
		snprintf(errbuf, errbuf_size, "material_entries empty");
		cJSON_Delete(root);
		return 1;
	}
	out->entries = (MaterialSidecarEntry*)calloc((size_t)n, sizeof(MaterialSidecarEntry));
	if (!out->entries) {
		snprintf(errbuf, errbuf_size, "allocation failed for %d entries", n);
		cJSON_Delete(root);
		return 1;
	}
	out->entry_count = n;
	for (int i = 0; i < n; i++) {
		cJSON* entry = cJSON_GetArrayItem(entries, i);
		if (parse_entry(entry, &out->entries[i], errbuf, errbuf_size) != 0) {
			cJSON_Delete(root);
			return 1;
		}
	}
	cJSON_Delete(root);
	return 0;
}

void material_sidecar_free(MaterialSidecar* sidecar)
{
	if (!sidecar)
		return;
	if (sidecar->entries) {
		for (int i = 0; i < sidecar->entry_count; i++) {
			if (sidecar->entries[i].cells)
				free(sidecar->entries[i].cells);
		}
		free(sidecar->entries);
		sidecar->entries = NULL;
	}
	sidecar->entry_count = 0;
}

int material_sidecar_validate(const MaterialSidecar* sidecar, char* errbuf, int errbuf_size)
{
	if (!sidecar) {
		snprintf(errbuf, errbuf_size, "null sidecar");
		return 1;
	}
	if (sidecar->sidecar_version != 1) {
		snprintf(errbuf, errbuf_size, "unsupported sidecar_version %d", sidecar->sidecar_version);
		return 1;
	}
	if (strcmp(sidecar->profile_kind, "extended_material_glyph_v1") != 0) {
		snprintf(errbuf, errbuf_size, "profile_kind '%s' != 'extended_material_glyph_v1'", sidecar->profile_kind);
		return 1;
	}
	// Hash format: 64 hex chars
	if (strlen(sidecar->glyph_manifest_hash) != 64) {
		snprintf(errbuf, errbuf_size, "glyph_manifest_hash length %zu != 64", strlen(sidecar->glyph_manifest_hash));
		return 1;
	}
	// Check for duplicate (material_id, elev, shade) tuples
	for (int i = 0; i < sidecar->entry_count; i++) {
		for (int j = i + 1; j < sidecar->entry_count; j++) {
			if (sidecar->entries[i].material_id == sidecar->entries[j].material_id) {
				// Same material, check cell duplicates
				for (int ci = 0; ci < sidecar->entries[i].cell_count; ci++) {
					for (int cj = 0; cj < sidecar->entries[j].cell_count; cj++) {
						if (sidecar->entries[i].cells[ci].elev == sidecar->entries[j].cells[cj].elev &&
						    sidecar->entries[i].cells[ci].shade == sidecar->entries[j].cells[cj].shade) {
							snprintf(errbuf, errbuf_size, "duplicate cell mat=%d elev=%d shade=%d",
							         sidecar->entries[i].material_id,
							         sidecar->entries[i].cells[ci].elev,
							         sidecar->entries[i].cells[ci].shade);
							return 1;
						}
					}
				}
			}
		}
		// Check within same entry
		for (int ci = 0; ci < sidecar->entries[i].cell_count; ci++) {
			for (int cj = ci + 1; cj < sidecar->entries[i].cell_count; cj++) {
				if (sidecar->entries[i].cells[ci].elev == sidecar->entries[i].cells[cj].elev &&
				    sidecar->entries[i].cells[ci].shade == sidecar->entries[i].cells[cj].shade) {
					snprintf(errbuf, errbuf_size, "duplicate cell mat=%d elev=%d shade=%d",
					         sidecar->entries[i].material_id,
					         sidecar->entries[i].cells[ci].elev,
					         sidecar->entries[i].cells[ci].shade);
					return 1;
				}
			}
		}
	}
	return 0;
}

static int build_sidecar_path(const char* map_path, char* out, int out_size)
{
	if (!map_path || !map_path[0] || !out || out_size <= 0)
		return 1;
	int written = snprintf(out, out_size, "%s.glyph_profile.json", map_path);
	return written <= 0 || written >= out_size;
}

static int read_whole_file(const char* path, char** out_text)
{
	if (!path || !out_text)
		return 1;
	*out_text = NULL;
	FILE* f = fopen(path, "rb");
	if (!f)
		return 1;
	if (fseek(f, 0, SEEK_END) != 0)
	{
		fclose(f);
		return 1;
	}
	long size = ftell(f);
	if (size < 0)
	{
		fclose(f);
		return 1;
	}
	if (fseek(f, 0, SEEK_SET) != 0)
	{
		fclose(f);
		return 1;
	}
	char* text = (char*)malloc((size_t)size + 1);
	if (!text)
	{
		fclose(f);
		return 1;
	}
	size_t n = fread(text, 1, (size_t)size, f);
	fclose(f);
	if (n != (size_t)size)
	{
		free(text);
		return 1;
	}
	text[n] = 0;
	*out_text = text;
	return 0;
}

int material_sidecar_load_apply_for_map(
	const char* map_path,
	MaterialSidecarApplyCellFn apply_cell,
	void* user,
	const char* prefix,
	int* out_applied_cells,
	char* errbuf,
	int errbuf_size)
{
	if (out_applied_cells)
		*out_applied_cells = 0;
	if (!apply_cell)
	{
		snprintf(errbuf, errbuf_size, "null apply_cell callback");
		return 1;
	}

	char sidecar_path[4096];
	if (build_sidecar_path(map_path, sidecar_path, sizeof(sidecar_path)) != 0)
	{
		snprintf(errbuf, errbuf_size, "material glyph sidecar path too long");
		return 1;
	}

	char* text = NULL;
	if (read_whole_file(sidecar_path, &text) != 0)
	{
		if (prefix)
			printf("%s Material glyph sidecar: none\n", prefix);
		return 0;
	}

	MaterialSidecar sidecar = {};
	GlyphManifest manifest = {};
	GlyphManifestError manifest_err = GLYPH_MANIFEST_OK;
	int applied = 0;
	int ok = 1;

	if (material_sidecar_parse(text, &sidecar, errbuf, errbuf_size) != 0 ||
		material_sidecar_validate(&sidecar, errbuf, errbuf_size) != 0)
	{
		if (prefix)
			printf("%s Error: material glyph sidecar invalid: %s\n", prefix, errbuf && errbuf[0] ? errbuf : "unknown error");
		goto done;
	}

	manifest_err = glyph_manifest_load_and_verify(
		sidecar.glyph_manifest_path,
		sidecar.glyph_manifest_hash,
		&manifest,
		errbuf,
		errbuf_size);
	if (manifest_err != GLYPH_MANIFEST_OK)
	{
		if (prefix)
			printf("%s Error: material glyph manifest rejected: %s %s\n", prefix, glyph_manifest_error_name(manifest_err), errbuf && errbuf[0] ? errbuf : "");
		goto done_manifest;
	}

	for (int entry_i = 0; entry_i < sidecar.entry_count; entry_i++)
	{
		const MaterialSidecarEntry* entry = &sidecar.entries[entry_i];
		for (int cell_i = 0; cell_i < entry->cell_count; cell_i++)
		{
			const MaterialSidecarCell* cell = &entry->cells[cell_i];
			if (!glyph_manifest_is_admitted(&manifest, cell->glyph_id))
			{
				snprintf(errbuf, errbuf_size, "material glyph sidecar has unadmitted GlyphId %u", (unsigned)cell->glyph_id);
				if (prefix)
					printf("%s Error: %s\n", prefix, errbuf);
				goto done_manifest;
			}
			uint16_t coverage = 0;
			if (!glyph_manifest_lookup_coverage(&manifest, cell->glyph_id, &coverage))
			{
				snprintf(errbuf, errbuf_size, "material glyph sidecar GlyphId %u has no coverage", (unsigned)cell->glyph_id);
				if (prefix)
					printf("%s Error: %s\n", prefix, errbuf);
				goto done_manifest;
			}
			if (apply_cell(user, entry->material_id, cell->elev, cell->shade, cell->glyph_id, coverage) != 0)
			{
				snprintf(errbuf, errbuf_size, "failed applying material glyph mat=%d elev=%d shade=%d glyph_id=%u",
					entry->material_id, cell->elev, cell->shade, (unsigned)cell->glyph_id);
				if (prefix)
					printf("%s Error: %s\n", prefix, errbuf);
				goto done_manifest;
			}
			applied++;
		}
	}

	ok = 0;
	if (out_applied_cells)
		*out_applied_cells = applied;
	if (prefix)
		printf("%s Material glyph sidecar loaded: entries=%d cells=%d\n", prefix, sidecar.entry_count, applied);

done_manifest:
	glyph_manifest_free(&manifest);
done:
	material_sidecar_free(&sidecar);
	free(text);
	return ok;
}

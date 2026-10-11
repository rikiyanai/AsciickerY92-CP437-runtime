// appearance_contract_state.cpp — appearance contract loading and catalog
//
// Extracted from server/server_tick.cpp. Owns:
//   - SvrActorVisualProfileCatalog: cached server/runtime-owned catalog
//   - Contract loading: SvrLoadStartupAppearanceContract
//   - Contract queries: SvrAppearanceContractVersion, SvrValidateJoinV2Claims
//   - Catalog lookup helpers: all SvrFindAppearance* functions
//   - Appearance state helpers (UpsertEntry, BumpRevision, etc.)

#include "appearance_contract_state.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <math.h>
#include <ctype.h>

#include "server_state.h"
#include "protocol/protocol_join.h"
#include "mount_state_types.h"
#include "actor_visual_catalog_source.h"
#include "../engine/placed_block_geometry.h"
#include "../engine/actor_visual_profile_table.generated.h"

extern char base_path[];

// ── Shared constants ────────────────────────────────────────────
// FL-3955 V-3 FIX: These constants MUST match protocol_common.h values exactly.
// Previously DEFAULT=0/GOLD=3/DARK=4 which contradicted server_tick.cpp and
// protocol_common.h (DEFAULT=500/GOLD=501/DARK=502). Any mismatch causes silent
// ActorVisualProfile key divergence — the server sends one style id, the compiler
// enumerates another, and the runtime gets SELECTOR_NOT_FOUND.
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_DEFAULT = APPEARANCE_VISUAL_STYLE_DEFAULT; // 500 — must match protocol_common.h
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_GOLD = APPEARANCE_VISUAL_STYLE_GOLD;       // 501
static const uint16_t SVR_APPEARANCE_VISUAL_STYLE_DARK = APPEARANCE_VISUAL_STYLE_DARK;       // 502

// =====================================================================
// FILE I/O helpers
// =====================================================================

static bool SvrReadTextFile(const char* path, char** out_buf)
{
    if (!path || !out_buf)
        return false;
    *out_buf = 0;

    char resolved_path[4096] = {};
    const char* open_path = path;
    if (path[0] != '/' && !(path[0] && path[1] == ':') && base_path[0])
    {
        snprintf(resolved_path, sizeof(resolved_path), "%s%s", base_path, path);
        open_path = resolved_path;
    }

    FILE* f = fopen(open_path, "rb");
    if (!f)
        return false;
    if (fseek(f, 0, SEEK_END) != 0)
    {
        fclose(f);
        return false;
    }
    long size = ftell(f);
    if (size < 0)
    {
        fclose(f);
        return false;
    }
    rewind(f);

    char* buf = (char*)malloc((size_t)size + 1);
    if (!buf)
    {
        fclose(f);
        return false;
    }
    size_t read_sz = fread(buf, 1, (size_t)size, f);
    bool read_ok = (read_sz == (size_t)size) || feof(f);
    fclose(f);
    if (!read_ok)
    {
        free(buf);
        return false;
    }
    buf[read_sz] = 0;
    *out_buf = buf;
    return true;
}

static bool SvrExtractJsonStringValue(const char* json,
                                      const char* key,
                                      char* out,
                                      size_t out_cap)
{
    if (!json || !key || !out || out_cap == 0)
        return false;
    out[0] = 0;

    char needle[128];
    int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
        return false;

    const char* pos = strstr(json, needle);
    if (!pos)
        return false;
    pos += needle_len;

    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != ':')
        return false;
    pos++;
    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != '"')
        return false;
    pos++;

    const char* end = strchr(pos, '"');
    if (!end)
        return false;

    size_t len = (size_t)(end - pos);
    if (len + 1 > out_cap)
        return false;
    memcpy(out, pos, len);
    out[len] = 0;
    return true;
}

static bool SvrExtractJsonUIntValue(const char* json, const char* key, uint32_t* out)
{
    if (!json || !key || !out)
        return false;
    char needle[128];
    int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
        return false;
    const char* pos = strstr(json, needle);
    if (!pos)
        return false;
    pos += needle_len;
    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos != ':')
        return false;
    pos++;
    while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
        pos++;
    if (*pos < '0' || *pos > '9')
        return false;
    char* end_ptr = 0;
    unsigned long value = strtoul(pos, &end_ptr, 10);
    if (end_ptr == pos)
        return false;
    *out = (uint32_t)value;
    return true;
}

// =====================================================================
// Appearance catalog loading
// =====================================================================

bool SvrLoadActorVisualProfileCatalog(SvrActorVisualProfileCatalog* out_cache)
{
    static bool attempted = false;
    static bool cached_ok = false;
    static SvrActorVisualProfileCatalog cached = {};
    if (!attempted)
    {
        attempted = true;
        memset(&cached, 0, sizeof(cached));
        cached.loaded = true;
        cached.contract_version = APPEARANCE_CATALOG_CONTRACT_VERSION;
        cached.default_profile_id = APPEARANCE_CATALOG_DEFAULT_PROFILE_ID;

        if (kAppearanceCatalogProfileCount > (int)(sizeof(cached.profiles) / sizeof(cached.profiles[0])) ||
            kAppearanceCatalogSeatCount > (int)(sizeof(cached.seats) / sizeof(cached.seats[0])) ||
            kAppearanceCatalogItemCount > (int)(sizeof(cached.items) / sizeof(cached.items[0])) ||
            kAppearanceCatalogMountCount > (int)(sizeof(cached.mounts) / sizeof(cached.mounts[0])))
        {
            fprintf(stderr, "[svr-appearance] profile catalog exceeds server cache capacity\n");
            cached.loaded = false;
            cached_ok = false;
        }
        else
        {
            for (int i = 0; i < kAppearanceCatalogProfileCount; i++)
            {
                const AppearanceCatalogProfileDef* src = &kAppearanceCatalogProfiles[i];
                SvrActorVisualProfileCatalogProfileDef* dst = &cached.profiles[cached.profile_count++];
                dst->id = src->id;
                dst->skin_definition_id = src->skin_definition_id;
                snprintf(dst->slug, sizeof(dst->slug), "%s", src->slug ? src->slug : "");
                // FL-4074: copy authored starter loadout so SvrApplyProfileToAppearance
                // can upsert the starter entries when a player spawns. Without this,
                // CompiledActorVisualRow keys with non-zero slot ids are unreachable.
                const uint8_t capped_count = src->starter_count <= SVR_MAX_APPEARANCE_LOADOUT_ENTRIES
                    ? src->starter_count
                    : (uint8_t)SVR_MAX_APPEARANCE_LOADOUT_ENTRIES;
                dst->starter_count = capped_count;
                for (uint8_t s = 0; s < capped_count; s++)
                {
                    dst->starter_entries[s].slot_kind_id = src->starter_entries[s].slot_kind_id;
                    dst->starter_entries[s].item_instance_id = 0;
                    dst->starter_entries[s].item_definition_id = src->starter_entries[s].item_definition_id;
                    dst->starter_entries[s].visual_style_id = src->starter_entries[s].visual_style_id;
                    dst->starter_entries[s].state_flags = src->starter_entries[s].state_flags;
                }
            }
            for (int i = 0; i < kAppearanceCatalogSeatCount; i++)
            {
                const AppearanceCatalogSeatDef* src = &kAppearanceCatalogSeats[i];
                SvrActorVisualProfileCatalogSeatDef* dst = &cached.seats[cached.seat_count++];
                snprintf(dst->seat_alias, sizeof(dst->seat_alias), "%s",
                         src->seat_alias ? src->seat_alias : "");
                dst->appearance_profile_id = src->appearance_profile_id;
            }
            for (int i = 0; i < kAppearanceCatalogItemCount; i++)
            {
                const AppearanceCatalogItemDef* src = &kAppearanceCatalogItems[i];
                SvrActorVisualProfileCatalogItemDef* dst = &cached.items[cached.item_count++];
                dst->id = src->id;
                dst->slot_kind_id = src->slot_kind_id;
                dst->mount_definition_id = src->mount_definition_id;
                dst->swing_presentation_kind_id = src->swing_presentation_kind_id;
                dst->gameplay_kind = src->gameplay_kind;
                dst->swing_presentation_ticks = src->swing_presentation_ticks;
                dst->placeable = src->placeable;
                dst->explicit_pickup_only = src->explicit_pickup_only;
                dst->block_break_power = src->block_break_power;
                dst->placed_durability = src->placed_durability;
                dst->place_distance_units = src->place_distance_units;
                dst->collision_radius_units = src->collision_radius_units;
                dst->collision_height_units = src->collision_height_units;
                // CKPT-D (FL-4137): placed-block geometry is AUTHORED in
                // server/actor_visual_catalog_source.h, not derived from
                // sprite proj_bbox. The old PlacedBlockGeometryLoadFromSpritePath
                // override is gone — sprite art no longer dictates collision
                // dimensions. The cube now matches the AKM mesh CKPT-A spawns,
                // and kMpMaxImplicitStepUp constraints are satisfied by
                // authored height alone.
                snprintf(dst->slug, sizeof(dst->slug), "%s", src->slug ? src->slug : "");
            }
            for (int i = 0; cached.loaded && i < kAppearanceCatalogMountCount; i++)
            {
                const AppearanceCatalogMountDef* src = &kAppearanceCatalogMounts[i];
                SvrActorVisualProfileCatalogMountDef* dst = &cached.mounts[cached.mount_count++];
                dst->id = src->id;
                dst->runtime_mount_state = src->runtime_mount_state;
                snprintf(dst->slug, sizeof(dst->slug), "%s", src->slug ? src->slug : "");
            }
            cached_ok = cached.loaded;
        }
    }
    if (out_cache)
        *out_cache = cached;
    return cached_ok;
}

// =====================================================================
// ActorVisualProfile catalog lookup helpers
// =====================================================================

const SvrActorVisualProfileCatalogProfileDef* SvrFindAppearanceProfileById(
    const SvrActorVisualProfileCatalog* cache,
    uint16_t appearance_profile_id)
{
    if (!cache)
        return 0;
    for (int i = 0; i < cache->profile_count; i++)
    {
        if (cache->profiles[i].id == appearance_profile_id)
            return &cache->profiles[i];
    }
    return 0;
}

const SvrActorVisualProfileCatalogSeatDef* SvrFindAppearanceSeatByAlias(
    const SvrActorVisualProfileCatalog* cache,
    const char* seat_alias)
{
    if (!cache || !seat_alias || !seat_alias[0])
        return 0;
    for (int i = 0; i < cache->seat_count; i++)
    {
        if (strcmp(cache->seats[i].seat_alias, seat_alias) == 0)
            return &cache->seats[i];
    }
    return 0;
}

const SvrActorVisualProfileCatalogItemDef* SvrFindAppearanceItemBySlug(
    const SvrActorVisualProfileCatalog* cache,
    const char* slug)
{
    if (!cache || !slug || !slug[0])
        return 0;
    for (int i = 0; i < cache->item_count; i++)
    {
        if (strcmp(cache->items[i].slug, slug) == 0)
            return &cache->items[i];
    }
    return 0;
}

const SvrActorVisualProfileCatalogMountDef* SvrFindAppearanceMountBySlug(
    const SvrActorVisualProfileCatalog* cache,
    const char* slug)
{
    if (!cache || !slug || !slug[0])
        return 0;
    for (int i = 0; i < cache->mount_count; i++)
    {
        if (strcmp(cache->mounts[i].slug, slug) == 0)
            return &cache->mounts[i];
    }
    return 0;
}

const SvrActorVisualProfileCatalogMountDef* SvrFindAppearanceMountById(
    const SvrActorVisualProfileCatalog* cache,
    uint16_t id)
{
    if (!cache || id == 0)
        return 0;
    for (int i = 0; i < cache->mount_count; i++)
    {
        if (cache->mounts[i].id == id)
            return &cache->mounts[i];
    }
    return 0;
}

const SvrActorVisualProfileCatalogItemDef* SvrFindAppearanceItemById(
    const SvrActorVisualProfileCatalog* cache,
    uint16_t item_definition_id)
{
    if (!cache || item_definition_id == 0)
        return 0;
    for (int i = 0; i < cache->item_count; i++)
    {
        if (cache->items[i].id == item_definition_id)
            return &cache->items[i];
    }
    return 0;
}

// =====================================================================
// Format validation
// =====================================================================

bool SvrIsLowerHexHash64(const char* value)
{
    if (!value)
        return false;
    for (int i = 0; i < APPEARANCE_HASH_HEX_LEN; i++)
    {
        const char c = value[i];
        const bool is_digit = c >= '0' && c <= '9';
        const bool is_lower_hex = c >= 'a' && c <= 'f';
        if (!is_digit && !is_lower_hex)
            return false;
    }
    return value[APPEARANCE_HASH_HEX_LEN] == 0;
}

bool SvrAppearanceContractError(char* error, size_t error_cap, const char* fmt, ...);

// =====================================================================
// Contract error helper
// =====================================================================

bool SvrAppearanceContractError(char* error, size_t error_cap, const char* fmt, ...)
{
    if (error && error_cap > 0)
    {
        va_list args;
        va_start(args, fmt);
        vsnprintf(error, error_cap, fmt, args);
        va_end(args);
    }
    return false;
}

// =====================================================================
// Startup contract cache validation
// =====================================================================

static inline bool SvrValidateStartupAppearanceContractCache(const SvrActorVisualProfileCatalog* cache,
                                                      char* error,
                                                      size_t error_cap)
{
    if (!cache || !cache->loaded)
        return SvrAppearanceContractError(error, error_cap, "ActorVisualProfile catalog not loaded");
    if (cache->profile_count <= 0)
        return SvrAppearanceContractError(error, error_cap, "ActorVisualProfile catalog has no appearance profiles");
    for (int i = 0; i < cache->profile_count; i++)
    {
        const SvrActorVisualProfileCatalogProfileDef* profile = &cache->profiles[i];
        if (profile->id == 0 || profile->skin_definition_id == 0)
        {
            return SvrAppearanceContractError(error, error_cap,
                "appearance profile has invalid skin_definition_id slug=%s id=%u skin=%u",
                profile->slug[0] ? profile->slug : "(unnamed)",
                (unsigned)profile->id,
                (unsigned)profile->skin_definition_id);
        }
    }

    // FL-4049: hardcoded slugs must not be duplicated between server_tick.cpp and
    // ActorVisualProfile source/compiler data.
    // parse_check.cpp. No shared constant definition across Python/C++ boundary.
    static const char* const required_startup_item_slugs[] = {
        WEAPON_CROSSBOW_SLUG,
        "normal_armour",
        "normal_helmet",
        "wolf_mountable",
        "bee_mountable",
    };
    for (int i = 0; i < (int)(sizeof(required_startup_item_slugs) / sizeof(required_startup_item_slugs[0])); i++)
    {
        const char* slug = required_startup_item_slugs[i];
        const SvrActorVisualProfileCatalogItemDef* item = SvrFindAppearanceItemBySlug(cache, slug);
        if (!item || item->id == 0 || item->slot_kind_id == 0 ||
            item->gameplay_kind == SVR_ITEM_GAMEPLAY_UNKNOWN)
        {
            return SvrAppearanceContractError(error, error_cap,
                "required startup item slug missing or invalid: %s", slug);
        }
        if (item->gameplay_kind == SVR_ITEM_GAMEPLAY_MOUNTABLE &&
            item->mount_definition_id == 0)
        {
            return SvrAppearanceContractError(error, error_cap,
                "required startup mountable slug missing mount_definition_id: %s", slug);
        }
    }
    return true;
}

// =====================================================================
// Contract loading API
// =====================================================================

bool SvrLoadStartupAppearanceContract(ServerState* state, char* error, size_t error_cap)
{
    if (!state)
        return SvrAppearanceContractError(error, error_cap, "missing server state");

    memset(&state->appearance_contract, 0, sizeof(state->appearance_contract));

    SvrActorVisualProfileCatalog cache = {};
    if (!SvrLoadActorVisualProfileCatalog(&cache) || !cache.loaded || cache.contract_version == 0)
    {
        return SvrAppearanceContractError(error, error_cap,
            "ActorVisualProfile catalog missing or invalid");
    }
    if (!SvrValidateStartupAppearanceContractCache(&cache, error, error_cap))
        return false;

    char bundle_hash[APPEARANCE_HASH_HEX_LEN + 1] = {};
    char ids_lock_hash[APPEARANCE_HASH_HEX_LEN + 1] = {};
    snprintf(bundle_hash, sizeof(bundle_hash), "%s", ACTOR_VISUAL_PROFILE_COMPILED_TABLE_SHA256);
    snprintf(ids_lock_hash, sizeof(ids_lock_hash), "%s", ACTOR_VISUAL_PROFILE_IDS_SHA256);

    // FL-4131 Phase 7 — glyph manifest identity. The compiler emits the
    // manifest hash and paired content_pack_id when authored extended glyph
    // content is bound into this runtime; CP437-only builds emit nullptrs.
    char glyph_manifest_hash[APPEARANCE_HASH_HEX_LEN + 1] = {};
    if (ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 && ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256[0])
        snprintf(glyph_manifest_hash, sizeof(glyph_manifest_hash), "%s", ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256);
    char content_pack_id[APPEARANCE_CONTENT_PACK_ID_CAP] = {};
    if (ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID && ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID[0])
        snprintf(content_pack_id, sizeof(content_pack_id), "%s", ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID);

    state->appearance_contract.loaded = true;
    state->appearance_contract.contract_version =
        (uint16_t)kCompiledActorVisualTableHeader.compiled_schema_version;
    snprintf(state->appearance_contract.bundle_hash,
             sizeof(state->appearance_contract.bundle_hash), "%s", bundle_hash);
    snprintf(state->appearance_contract.ids_lock_hash,
             sizeof(state->appearance_contract.ids_lock_hash), "%s", ids_lock_hash);
    snprintf(state->appearance_contract.glyph_manifest_hash,
             sizeof(state->appearance_contract.glyph_manifest_hash), "%s", glyph_manifest_hash);
    snprintf(state->appearance_contract.content_pack_id,
             sizeof(state->appearance_contract.content_pack_id), "%s", content_pack_id);
    // FL-4131 P10 — bind atlas runtime identity from the compiled table.
    char lut_hash_buf[APPEARANCE_HASH_HEX_LEN + 1] = {};
    char page_chain_hash_buf[APPEARANCE_HASH_HEX_LEN + 1] = {};
    if (ACTOR_VISUAL_PROFILE_LUT_SHA256 && ACTOR_VISUAL_PROFILE_LUT_SHA256[0])
        snprintf(lut_hash_buf, sizeof(lut_hash_buf), "%s", ACTOR_VISUAL_PROFILE_LUT_SHA256);
    if (ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256 && ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256[0])
        snprintf(page_chain_hash_buf, sizeof(page_chain_hash_buf), "%s", ACTOR_VISUAL_PROFILE_PAGE_ATLAS_CHAIN_SHA256);
    snprintf(state->appearance_contract.lut_hash,
             sizeof(state->appearance_contract.lut_hash), "%s", lut_hash_buf);
    snprintf(state->appearance_contract.page_atlas_chain_hash,
             sizeof(state->appearance_contract.page_atlas_chain_hash), "%s", page_chain_hash_buf);
    return true;
}

uint16_t SvrAppearanceContractVersion(const ServerState* state)
{
    if (!state || !state->appearance_contract.loaded)
        return 0;
    return state->appearance_contract.contract_version;
}

const char* SvrAppearanceContractRejectReasonString(uint8_t reason_code)
{
    switch (reason_code)
    {
        case APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH:
            return "contract_version_mismatch";
        case APPEARANCE_CONTRACT_REJECT_REASON::BUNDLE_HASH_MISMATCH:
            return "bundle_hash_mismatch";
        case APPEARANCE_CONTRACT_REJECT_REASON::IDS_LOCK_HASH_MISMATCH:
            return "ids_lock_hash_mismatch";
        case APPEARANCE_CONTRACT_REJECT_REASON::PROOF_SEAT_NAME_REJECTED:
            return "proof_join_name_rejected";
        case APPEARANCE_CONTRACT_REJECT_REASON::JOIN_ACCEPT_FAILED:
            return "join_accept_failed";
        case APPEARANCE_CONTRACT_REJECT_REASON::NAME_INVALID_CHARS:
            return "name_invalid_chars";
        case APPEARANCE_CONTRACT_REJECT_REASON::NAME_DUPLICATE:
            return "name_duplicate";
        // FL-4131 Phase 7 — slot 8 = glyph_manifest_mismatch.
        case APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH:
            return "glyph_manifest_mismatch";
        default:
            return "none";
    }
}

uint8_t SvrValidateJoinV2Claims(const ServerState* state,
                                uint16_t appearance_contract_version,
                                const char* bundle_hash,
                                const char* ids_lock_hash,
                                const char* glyph_manifest_hash,
                                const char* content_pack_id,
                                const char* lut_hash,
                                const char* page_atlas_chain_hash)
{
    const SvrAppearanceContractState* contract = state ? &state->appearance_contract : 0;
    const uint16_t server_contract_version = SvrAppearanceContractVersion(state);
    if (server_contract_version == 0 ||
        appearance_contract_version != server_contract_version)
    {
        return APPEARANCE_CONTRACT_REJECT_REASON::CONTRACT_VERSION_MISMATCH;
    }

    if (!contract->loaded || !contract->bundle_hash[0] ||
        !bundle_hash || !bundle_hash[0] || strcmp(bundle_hash, contract->bundle_hash) != 0)
        return APPEARANCE_CONTRACT_REJECT_REASON::BUNDLE_HASH_MISMATCH;
    if (!contract->ids_lock_hash[0] ||
        !ids_lock_hash || !ids_lock_hash[0] || strcmp(ids_lock_hash, contract->ids_lock_hash) != 0)
        return APPEARANCE_CONTRACT_REJECT_REASON::IDS_LOCK_HASH_MISMATCH;

    // FL-4131 Phase 7 — glyph manifest identity check (server-authoritative).
    // FAIL-CLOSED: if either side declares a manifest, both must match exactly.
    // Legacy CP437-only sessions are represented by both sides sending empty
    // glyph identity fields. A client declaring a manifest to a CP437-only
    // server is a mismatch, not a soft accept.
    const bool server_has_glyph_manifest = contract->glyph_manifest_hash[0] != 0;
    const bool client_has_glyph_manifest = glyph_manifest_hash && glyph_manifest_hash[0];
    const bool server_has_content_pack = contract->content_pack_id[0] != 0;
    const bool client_has_content_pack = content_pack_id && content_pack_id[0];
    if (server_has_glyph_manifest || client_has_glyph_manifest ||
        server_has_content_pack || client_has_content_pack)
    {
        if (!server_has_glyph_manifest || !client_has_glyph_manifest ||
            strcmp(glyph_manifest_hash, contract->glyph_manifest_hash) != 0)
            return APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH;
        // content_pack_id is part of the same identity. Empty is permitted only
        // for CP437-only runtimes where both sides leave glyph identity empty.
        if (server_has_content_pack != client_has_content_pack ||
            (server_has_content_pack &&
             strncmp(content_pack_id, contract->content_pack_id, APPEARANCE_CONTENT_PACK_ID_CAP - 1) != 0))
            return APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH;
    }

    // FL-4131 P10 — atlas runtime identity check. lut_hash and
    // page_atlas_chain_hash mirror the manifest fail-closed rule: if either
    // side declares a non-empty value, both must match. Old clients sending
    // empty atlas hashes against a server with extended content are rejected
    // here just like glyph_manifest mismatches; the reason code is the same
    // GLYPH_MANIFEST_MISMATCH bucket so existing client UX surfaces remain
    // consistent until a dedicated ATLAS_MISMATCH code is added.
    const bool server_has_lut = contract->lut_hash[0] != 0;
    const bool client_has_lut = lut_hash && lut_hash[0];
    const bool server_has_chain = contract->page_atlas_chain_hash[0] != 0;
    const bool client_has_chain = page_atlas_chain_hash && page_atlas_chain_hash[0];
    if (server_has_lut || client_has_lut)
    {
        if (!server_has_lut || !client_has_lut ||
            strcmp(lut_hash, contract->lut_hash) != 0)
            return APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH;
    }
    if (server_has_chain || client_has_chain)
    {
        if (!server_has_chain || !client_has_chain ||
            strcmp(page_atlas_chain_hash, contract->page_atlas_chain_hash) != 0)
            return APPEARANCE_CONTRACT_REJECT_REASON::GLYPH_MANIFEST_MISMATCH;
    }

    return APPEARANCE_CONTRACT_REJECT_REASON::NONE;
}

// =====================================================================
// Appearance state helpers
// =====================================================================

uint16_t SvrAppearanceVisualStyleFromPresentationVariant(uint8_t variant)
{
    switch (variant)
    {
        case 2: return SVR_APPEARANCE_VISUAL_STYLE_GOLD;
        case 3: return SVR_APPEARANCE_VISUAL_STYLE_DARK;
        default: return SVR_APPEARANCE_VISUAL_STYLE_DEFAULT;
    }
}

uint8_t SvrPresentationVariantFromAppearanceVisualStyle(uint16_t visual_style_id)
{
    switch (visual_style_id)
    {
        case SVR_APPEARANCE_VISUAL_STYLE_GOLD:
        case 1: return 2;
        case SVR_APPEARANCE_VISUAL_STYLE_DARK:
        case 2: return 3;
        default: return 1;
    }
}

void SvrCopyAppearanceSubjectKey(char dst[32], const char* src)
{
    if (!dst)
        return;
    dst[0] = 0;
    if (!src || !src[0])
        return;
    strncpy(dst, src, 31);
    dst[31] = 0;
}

void SvrBumpAppearanceRevision(SvrAuthoritativeAppearanceState* appearance)
{
    if (!appearance)
        return;
    appearance->loadout_revision++;
    if (appearance->loadout_revision == 0)
        appearance->loadout_revision = 1;
}

void SvrSetAppearanceIdentity(SvrAuthoritativeAppearanceState* appearance,
                              uint16_t contract_version,
                              uint8_t source_kind,
                              uint8_t projection_kind,
                              uint8_t subject_kind,
                              const char* subject_key,
                              uint16_t appearance_profile_id,
                              uint16_t skin_definition_id)
{
    if (!appearance)
        return;
    appearance->appearance_contract_version = contract_version;
    appearance->source_kind = source_kind;
    appearance->projection_kind = projection_kind;
    appearance->subject_kind = subject_kind;
    appearance->appearance_profile_id = appearance_profile_id;
    appearance->skin_definition_id = skin_definition_id;
    appearance->mount_definition_id = 0;
    appearance->variation_id = 0;
    appearance->rig_id = 0;
    SvrCopyAppearanceSubjectKey(appearance->subject_key, subject_key);
}

int SvrFindAppearanceEntryIndexBySlot(const SvrAuthoritativeAppearanceState* appearance,
                                      uint16_t slot_kind_id)
{
    if (!appearance)
        return -1;
    for (int i = 0; i < appearance->entry_count; i++)
    {
        if (appearance->entries[i].slot_kind_id == slot_kind_id)
            return i;
    }
    return -1;
}

const SvrAppearanceLoadoutEntry* SvrFindAppearanceEntryByItemInstanceId(
    const SvrAuthoritativeAppearanceState* appearance,
    uint16_t item_instance_id)
{
    if (!appearance || item_instance_id == 0)
        return 0;
    for (int i = 0; i < appearance->entry_count; i++)
    {
        if (appearance->entries[i].item_instance_id == item_instance_id)
            return &appearance->entries[i];
    }
    return 0;
}

const SvrAppearanceLoadoutEntry* SvrFindEquippedAppearanceEntryForItem(
    const ServerState* state,
    const SvrItemState* it)
{
    if (!state || !it || it->owner_id == 0xFFFF || it->owner_id >= SVR_MAX_CLIENTS)
        return 0;

    const SvrPlayerState* owner = &state->players[it->owner_id];
    if (!owner->active)
        return 0;

    return SvrFindAppearanceEntryByItemInstanceId(&owner->appearance, it->item_id);
}

bool SvrUpsertAppearanceEntry(SvrAuthoritativeAppearanceState* appearance,
                              const SvrAppearanceLoadoutEntry* entry,
                              bool bump_revision)
{
    if (!appearance || !entry || entry->slot_kind_id == 0)
        return false;
    int idx = SvrFindAppearanceEntryIndexBySlot(appearance, entry->slot_kind_id);
    if (idx < 0)
    {
        if (appearance->entry_count >= SVR_MAX_APPEARANCE_LOADOUT_ENTRIES)
            return false;
        idx = appearance->entry_count++;
    }
    appearance->entries[idx] = *entry;
    if (bump_revision)
        SvrBumpAppearanceRevision(appearance);
    return true;
}

bool SvrRemoveAppearanceEntryBySlot(SvrAuthoritativeAppearanceState* appearance,
                                    uint16_t slot_kind_id,
                                    bool bump_revision)
{
    if (!appearance)
        return false;
    int idx = SvrFindAppearanceEntryIndexBySlot(appearance, slot_kind_id);
    if (idx < 0)
        return false;
    for (int i = idx; i + 1 < appearance->entry_count; i++)
        appearance->entries[i] = appearance->entries[i + 1];
    if (appearance->entry_count > 0)
    {
        appearance->entry_count--;
        memset(&appearance->entries[appearance->entry_count], 0, sizeof(appearance->entries[0]));
    }
    if (bump_revision)
        SvrBumpAppearanceRevision(appearance);
    return true;
}

void SvrClearAppearanceEntries(SvrAuthoritativeAppearanceState* appearance, bool bump_revision)
{
    if (!appearance)
        return;
    memset(appearance->entries, 0, sizeof(appearance->entries));
    appearance->entry_count = 0;
    if (bump_revision)
        SvrBumpAppearanceRevision(appearance);
}

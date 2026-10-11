#pragma once

// appearance_contract_state.h — appearance contract loading and catalog
//
// Owns:
//   - SvrActorVisualProfileCatalog: cached server/runtime-owned catalog
//   - Contract loading: SvrLoadStartupAppearanceContract
//   - Contract queries: SvrAppearanceContractVersion, SvrValidateJoinV2Claims
//   - Catalog lookup helpers: SvrFindAppearanceProfileById, SvrFindAppearanceItemBySlug, etc.
//   - Format validation: SvrIsLowerHexHash64
//
// STEP 5 / CONTRACT 5 in the bundle walkthrough (see server_tick.cpp header).

#include <stdint.h>

#include "server_state.h"   // SvrAuthoritativeAppearanceState, SvrAppearanceLoadoutEntry
#include "protocol/protocol_join.h"  // APPEARANCE_HASH_HEX_LEN, APPEARANCE_CONTRACT_REJECT_REASON

struct ServerState;
struct SvrPlayerState;
struct SvrNpcState;

// ── Server catalog structs (loaded from the server/runtime catalog) ─────────

struct SvrActorVisualProfileCatalogProfileDef
{
    uint16_t id;
    uint16_t skin_definition_id;
    char slug[32];
    uint8_t starter_count;
    SvrAppearanceLoadoutEntry starter_entries[SVR_MAX_APPEARANCE_LOADOUT_ENTRIES];
};

struct SvrActorVisualProfileCatalogSeatDef
{
    char seat_alias[32];
    uint16_t appearance_profile_id;
};

struct SvrActorVisualProfileCatalogItemDef
{
    uint16_t id;
    uint16_t slot_kind_id;
    uint16_t mount_definition_id;
    uint16_t swing_presentation_kind_id;
    uint8_t gameplay_kind;
    uint8_t swing_presentation_ticks;
    uint8_t placeable;
    uint8_t explicit_pickup_only;
    uint8_t block_break_power;
    uint16_t placed_durability;
    float place_distance_units;
    float collision_radius_units;
    float collision_height_units;
    char slug[48];
};

struct SvrActorVisualProfileCatalogMountDef
{
    uint16_t id;
    uint8_t runtime_mount_state;
    char slug[48];
};

struct SvrActorVisualProfileCatalog
{
    bool loaded;
    uint16_t contract_version;
    uint16_t default_profile_id;
    uint8_t profile_count;
    uint8_t seat_count;
    uint8_t item_count;
    uint8_t mount_count;
    SvrActorVisualProfileCatalogProfileDef profiles[16];
    SvrActorVisualProfileCatalogSeatDef seats[8];
    SvrActorVisualProfileCatalogItemDef items[32];
    SvrActorVisualProfileCatalogMountDef mounts[8];
};

// ── Appearance contract API ─────────────────────────────────────

// Load and validate the appearance bundle contract once at startup.
// Populates state->appearance_contract with bundle_hash + ids_lock_hash.
bool SvrLoadStartupAppearanceContract(struct ServerState* state, char* error, size_t error_cap);

// Get the loaded contract version (0 if not loaded).
uint16_t SvrAppearanceContractVersion(const struct ServerState* state);

// Check a join v2 claim against the server's loaded contract.
// Returns APPEARANCE_CONTRACT_REJECT_REASON::NONE on match, or the rejection reason.
// FL-4131 Phase 7 — glyph_manifest_hash and content_pack_id are now part of the
// claim. Missing or mismatching glyph manifest identity rejects with
// GLYPH_MANIFEST_MISMATCH. Existing call sites pass empty strings if the wire
// did not yet carry these fields (and the validator rejects on empty server hash
// only when the server has a non-empty manifest bound — see implementation).
// FL-4131 P10 — lut_hash and page_atlas_chain_hash extend the claim with
// the client's runtime atlas identity. Both follow the same fail-closed rule
// as glyph_manifest_hash: empty on both sides is valid only for a CP437-only
// build; non-empty mismatch rejects the join.
uint8_t SvrValidateJoinV2Claims(const struct ServerState* state,
                                uint16_t appearance_contract_version,
                                const char* bundle_hash,
                                const char* ids_lock_hash,
                                const char* glyph_manifest_hash,
                                const char* content_pack_id,
                                const char* lut_hash,
                                const char* page_atlas_chain_hash);

// Human-readable name for a reject reason code.
const char* SvrAppearanceContractRejectReasonString(uint8_t reason_code);

// Format validation: 64-char lowercase hex hash.
bool SvrIsLowerHexHash64(const char* value);

// Always returns false to enable `return SvrAppearanceContractError(...)` idiom.
bool SvrAppearanceContractError(char* error, size_t error_cap, const char* fmt, ...);

// ── ActorVisualProfile catalog loading and lookups ────────────────────────────

// Load the server/runtime-owned catalog (cached after first load).
bool SvrLoadActorVisualProfileCatalog(SvrActorVisualProfileCatalog* out_cache);

const SvrActorVisualProfileCatalogProfileDef* SvrFindAppearanceProfileById(
    const SvrActorVisualProfileCatalog* cache, uint16_t id);

const SvrActorVisualProfileCatalogSeatDef* SvrFindAppearanceSeatByAlias(
    const SvrActorVisualProfileCatalog* cache, const char* alias);

const SvrActorVisualProfileCatalogItemDef* SvrFindAppearanceItemBySlug(
    const SvrActorVisualProfileCatalog* cache, const char* slug);

const SvrActorVisualProfileCatalogMountDef* SvrFindAppearanceMountBySlug(
    const SvrActorVisualProfileCatalog* cache, const char* slug);

const SvrActorVisualProfileCatalogMountDef* SvrFindAppearanceMountById(
    const SvrActorVisualProfileCatalog* cache, uint16_t id);

const SvrActorVisualProfileCatalogItemDef* SvrFindAppearanceItemById(
    const SvrActorVisualProfileCatalog* cache, uint16_t id);

// ── Appearance state helpers ────────────────────────────────────

// Look up a bundle item definition id from the legacy presentation variant.
uint16_t SvrAppearanceVisualStyleFromPresentationVariant(uint8_t variant);

// Reverse: presentation variant from visual style id.
uint8_t SvrPresentationVariantFromAppearanceVisualStyle(uint16_t visual_style_id);

// Copy subject key (bounded, null-terminated).
void SvrCopyAppearanceSubjectKey(char dst[32], const char* src);

// Bump the loadout revision counter (skips zero).
void SvrBumpAppearanceRevision(SvrAuthoritativeAppearanceState* appearance);

// Set the top-level identity fields on an appearance state.
void SvrSetAppearanceIdentity(SvrAuthoritativeAppearanceState* appearance,
                              uint16_t contract_version,
                              uint8_t source_kind,
                              uint8_t projection_kind,
                              uint8_t subject_kind,
                              const char* subject_key,
                              uint16_t appearance_profile_id,
                              uint16_t skin_definition_id);

// Find the index of a slot kind id in the appearance entry list.
int SvrFindAppearanceEntryIndexBySlot(const SvrAuthoritativeAppearanceState* appearance,
                                      uint16_t slot_kind_id);

// Find an entry by item instance id.
const SvrAppearanceLoadoutEntry* SvrFindAppearanceEntryByItemInstanceId(
    const SvrAuthoritativeAppearanceState* appearance,
    uint16_t item_instance_id);

// Find the equipped entry for a given SvrItemState (looks up the owner player).
const SvrAppearanceLoadoutEntry* SvrFindEquippedAppearanceEntryForItem(
    const struct ServerState* state,
    const struct SvrItemState* it);

// Upsert an entry (add or replace by slot_kind_id).
bool SvrUpsertAppearanceEntry(SvrAuthoritativeAppearanceState* appearance,
                              const SvrAppearanceLoadoutEntry* entry,
                              bool bump_revision);

// Remove entry by slot kind id.
bool SvrRemoveAppearanceEntryBySlot(SvrAuthoritativeAppearanceState* appearance,
                                    uint16_t slot_kind_id,
                                    bool bump_revision);

// Clear all entries.
void SvrClearAppearanceEntries(SvrAuthoritativeAppearanceState* appearance, bool bump_revision);

// server/actor_visual_key_derivation.cpp
//
// Implementation of the server-side CompiledActorVisualKey builder and the
// runtime compile-gate check. Contract owner: engine/actor_visual_key_derivation.h.
//
// Mirrors the client-side static-inline BuildCompiledActorVisualKey in
// engine/actor_visual_profile_runtime.h. Any divergence between the two is
// an FL-3955 V-2 regression and must be caught by the equivalence harness.

#include "../engine/actor_visual_key_derivation.h"

#include <string.h>

#include "../engine/actor_visual_profile.h"
#include "../engine/actor_visual_profile_runtime.h"  // FindCompiledActorVisualRow
#include "protocol/protocol_common.h"
#include "server_state.h"  // SvrAuthoritativeAppearanceState, SvrAppearanceLoadoutEntry

bool SvrBuildCompiledActorVisualKey(
    const SvrAuthoritativeAppearanceState* appearance,
    uint16_t presentation_kind_id,
    CompiledActorVisualKey* out)
{
    if (!appearance || !out)
        return false;
    *out = {};
    out->skin_id = appearance->skin_definition_id;
    // actor_style_id is not yet authored on SvrAuthoritativeAppearanceState; default 0
    // until the snapshot path carries it. Matches BuildCompiledActorVisualKey.
    out->actor_style_id = 0;
    out->presentation_kind_id = presentation_kind_id;
    out->variation_id = appearance->variation_id;
    out->mount_id = appearance->mount_definition_id;
    out->rig_id = appearance->rig_id;

    int future_slot_count = 0;
    for (int i = 0; i < appearance->entry_count && i < SVR_MAX_APPEARANCE_LOADOUT_ENTRIES; i++)
    {
        const SvrAppearanceLoadoutEntry& entry = appearance->entries[i];
        switch (entry.slot_kind_id)
        {
        case APPEARANCE_SLOT_KIND_HEAD:
            out->head_item_id = entry.item_definition_id;
            out->head_style_id = entry.visual_style_id;
            break;
        case APPEARANCE_SLOT_KIND_ARMOR:
            out->chest_item_id = entry.item_definition_id;
            out->chest_style_id = entry.visual_style_id;
            break;
        case APPEARANCE_SLOT_KIND_WEAPON:
            out->weapon_item_id = entry.item_definition_id;
            out->weapon_style_id = entry.visual_style_id;
            break;
        case APPEARANCE_SLOT_KIND_SHIELD:
            out->shield_item_id = entry.item_definition_id;
            out->shield_style_id = entry.visual_style_id;
            break;
        case APPEARANCE_SLOT_KIND_BODY:
        case APPEARANCE_SLOT_KIND_MOUNT:
            break;
        default:
        {
            if (future_slot_count >= 4)
                return false;
            int insert_at = future_slot_count;
            while (insert_at > 0)
            {
                uint16_t prev_slot = out->future_slot_kind_ids[insert_at - 1];
                uint16_t prev_item = out->future_item_ids[insert_at - 1];
                uint16_t prev_style = out->future_style_ids[insert_at - 1];
                if (prev_slot < entry.slot_kind_id ||
                    (prev_slot == entry.slot_kind_id &&
                        prev_item <= entry.item_definition_id))
                    break;
                out->future_slot_kind_ids[insert_at] = prev_slot;
                out->future_item_ids[insert_at] = prev_item;
                out->future_style_ids[insert_at] = prev_style;
                insert_at--;
            }
            out->future_slot_kind_ids[insert_at] = entry.slot_kind_id;
            out->future_item_ids[insert_at] = entry.item_definition_id;
            out->future_style_ids[insert_at] = entry.visual_style_id;
            future_slot_count++;
            break;
        }
        }
    }
    return true;
}

bool ValidateCompiledActorVisualKeyHasRow(const CompiledActorVisualKey* key)
{
    if (!key)
        return false;
    return FindCompiledActorVisualRow(*key) != nullptr;
}

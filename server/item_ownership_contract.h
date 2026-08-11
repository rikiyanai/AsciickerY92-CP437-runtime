#pragma once

// Item ownership contract — server-authoritative bundle plan aligned.
//
// Rules:
//   - World-owned items have owner_id == 0xFFFF.
//   - Ownership transitions go through SvrClaimWorldItemForOwnerAfterEvent
//     when an item-change event is required; direct claims are for local
//     speculation with explicit rollback.
//   - Consumption always emits the change event first; state mutation only
//     happens if the event was successfully queued (fail-closed on queue drop).
//   - Callers apply gameplay side-effects (heal, etc.) only on return true.
//
// FL-1004: this file was missing; server_tick.cpp included it before it existed.

#include "server_state.h"

#include <stdint.h>

// Callback type: emit one item change event.
// Returns true if the event was queued, false if it was dropped.
typedef bool (*SvrItemChangeEmitFn)(void* ctx,
                                    const SvrItemState* it,
                                    uint8_t kind,
                                    uint16_t owner_id);

// Claim an unclaimed world-item for owner_id.
// Precondition: it->active && it->owner_id == 0xFFFF.
// On success: sets it->owner_id = owner_id, clears stale equip slot state, returns true.
// On failure (null, inactive, already owned): returns false unchanged.
static inline bool SvrClaimWorldItemForOwner(SvrItemState* it, uint16_t owner_id)
{
    if (!it || !it->active)
        return false;
    if (it->owner_id != 0xFFFF)
        return false;
    it->owner_id = owner_id;
    it->equip_slot_kind_id = 0;
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    return true;
}

// Restore a speculative claim that happened before a later event could be
// queued. This is only for rollback of already-claimed items; normal world
// transitions should use SvrClaimWorldItemForOwnerAfterEvent.
static inline void SvrReleaseClaimedWorldItem(SvrItemState* it,
                                              uint16_t previous_owner_id,
                                              uint16_t previous_equip_slot_kind_id)
{
    if (!it)
        return;
    it->owner_id = previous_owner_id;
    it->equip_slot_kind_id = previous_equip_slot_kind_id;
}

// Event-first world claim. The emitted item snapshot carries the post-claim
// owner/equip state, but the authoritative item is mutated only after queue
// success.
static inline bool SvrClaimWorldItemForOwnerAfterEvent(SvrItemState* it,
                                                       uint16_t owner_id,
                                                       uint8_t kind,
                                                       SvrItemChangeEmitFn emit_fn,
                                                       void* ctx)
{
    if (!it || !emit_fn || !it->active)
        return false;
    if (it->owner_id != 0xFFFF)
        return false;

    SvrItemState event_item = *it;
    event_item.owner_id = owner_id;
    event_item.equip_slot_kind_id = 0;
    if (!emit_fn(ctx, &event_item, kind, owner_id))
        return false;

    it->owner_id = owner_id;
    it->equip_slot_kind_id = 0;
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    return true;
}

// Gate check: item is active and currently owned by owner_id.
static inline bool SvrOwnedItemCanBeConsumed(const SvrItemState* it, uint16_t owner_id)
{
    if (!it || !it->active)
        return false;
    return it->owner_id == owner_id;
}

// Emit the item change event and — only if successfully queued — mark the
// item inactive and zero its equip slot.
// Returns true if the event was queued and the item consumed.
// Returns false if the event was not queued; no state is mutated.
// Callers must apply gameplay side-effects (hp restore, etc.) only on true.
static inline bool SvrConsumeOwnedItemAfterEvent(SvrItemState* it,
                                                  uint16_t owner_id,
                                                  uint8_t kind,
                                                  SvrItemChangeEmitFn emit_fn,
                                                  void* ctx)
{
    if (!it || !emit_fn)
        return false;
    if (!SvrOwnedItemCanBeConsumed(it, owner_id))
        return false;
    if (!emit_fn(ctx, it, kind, owner_id))
        return false;
    it->active = false;
    it->equip_slot_kind_id = 0;
    return true;
}

// Event-first world-item consume. This is for proximity auto-consume paths
// where the item starts world-owned and must not be speculatively claimed
// before the consume event has been accepted.
static inline bool SvrConsumeWorldItemForOwnerAfterEvent(SvrItemState* it,
                                                         uint16_t owner_id,
                                                         uint8_t kind,
                                                         SvrItemChangeEmitFn emit_fn,
                                                         void* ctx)
{
    if (!it || !emit_fn || !it->active)
        return false;
    if (it->owner_id != 0xFFFF)
        return false;

    SvrItemState event_item = *it;
    event_item.owner_id = owner_id;
    event_item.equip_slot_kind_id = 0;
    if (!emit_fn(ctx, &event_item, kind, owner_id))
        return false;

    it->owner_id = owner_id;
    it->active = false;
    it->equip_slot_kind_id = 0;
    it->placed_flags = SVR_PLACED_ITEM_NONE;
    it->placed_durability = 0;
    it->placed_yaw = 0.0f;
    return true;
}

#pragma once

// Server-owned actor visual reachability authority (FL-4062 / Q5.1 schema slice).
//
// This module owns the exact set of CompiledActorVisualKey values the server
// can publish during gameplay. Everything else — the dump CLI, the compiler's
// validator, and (Q5.2) runtime network_ingest_appearance.cpp — derives its
// view of "reachable" from here.
//
// Q5.2:
//   - EnumerateReachableKeys() is the sole reachable-key enumeration owner.
//   - server/actor_visual_reachability_dump.cpp is a thin JSON-emit wrapper.
//   - CanonicalActorVisualReachableKey mirrors CompiledActorVisualKey exactly
//     (skin/style/kind/variation/mount/rig + canonical slot item/style pairs
//     + future_slots).
//   - The dump emits only the full key shape; legacy partial fields are gone.
//   - Validator cross-check is bidirectional: missing reachable rows and
//     orphan profile rows both fail.
//   - Runtime render can fail exact lookup when the active presentation plus
//     AppearanceState cannot exact-match a compiled row. Network ingest must
//     still accept and store the server-owned AppearanceState so stale local
//     visuals cannot masquerade as a successful exact match.
//
// Authority chain (internal design notes / FL-4066):
//   upstream/source assets
//     -> server_catalog_hash         (ID vocabulary; this header)
//     -> server_reachability_hash    (exact full-key set; this module)
//     -> ActorVisualProfile authoring
//     -> compiler exact lookup
//     -> ordered masked compose
//     -> headed proof

#include "actor_visual_catalog_source.h"

#include <algorithm>
#include <stdint.h>
#include <vector>

// Named server-owned defaults. A field set to one of these means the server
// has explicitly chosen the default value for that dimension — NOT that the
// dimension is unknown or that a placeholder is being emitted.
static constexpr uint16_t ACTOR_VISUAL_REACHABILITY_ACTOR_STYLE_DEFAULT = 0;
static constexpr uint16_t ACTOR_VISUAL_REACHABILITY_MOUNT_NONE = 0;

struct CanonicalActorVisualReachableSlot
{
	uint16_t slot_kind_id;
	uint16_t item_id;
	uint16_t visual_style_id;
};

// Full 15-field shape mirroring CompiledActorVisualKey (see
// scripts/compile_actor_visual_profiles.py:_normalise_key).
//
// Dump JSON emits this full shape only; old presentation_kind/variation/slots[]
// compatibility fields were deleted in Q5.2 so validators cannot match a
// partial selector-shaped key.
struct CanonicalActorVisualReachableKey
{
	uint16_t skin_id;
	uint16_t actor_style_id;
	uint16_t presentation_kind_id;
	uint16_t variation_id;
	uint16_t mount_id;
	uint16_t rig_id;
	uint16_t head_item_id;
	uint16_t head_style_id;
	uint16_t chest_item_id;
	uint16_t chest_style_id;
	uint16_t weapon_item_id;
	uint16_t weapon_style_id;
	uint16_t shield_item_id;
	uint16_t shield_style_id;
	std::vector<CanonicalActorVisualReachableSlot> future_slots;
};

namespace actor_visual_reachability_detail {

inline bool SlotLess(const CanonicalActorVisualReachableSlot& a,
                     const CanonicalActorVisualReachableSlot& b)
{
	if (a.slot_kind_id != b.slot_kind_id)
		return a.slot_kind_id < b.slot_kind_id;
	if (a.item_id != b.item_id)
		return a.item_id < b.item_id;
	return a.visual_style_id < b.visual_style_id;
}

inline bool FutureSlotsLess(const std::vector<CanonicalActorVisualReachableSlot>& a,
                            const std::vector<CanonicalActorVisualReachableSlot>& b)
{
	if (a.size() != b.size())
		return a.size() < b.size();
	for (size_t i = 0; i < a.size(); i++)
	{
		if (SlotLess(a[i], b[i])) return true;
		if (SlotLess(b[i], a[i])) return false;
	}
	return false;
}

inline bool KeyLess(const CanonicalActorVisualReachableKey& a,
                    const CanonicalActorVisualReachableKey& b)
{
	if (a.skin_id != b.skin_id) return a.skin_id < b.skin_id;
	if (a.actor_style_id != b.actor_style_id) return a.actor_style_id < b.actor_style_id;
	if (a.presentation_kind_id != b.presentation_kind_id) return a.presentation_kind_id < b.presentation_kind_id;
	if (a.variation_id != b.variation_id) return a.variation_id < b.variation_id;
	if (a.mount_id != b.mount_id) return a.mount_id < b.mount_id;
	if (a.rig_id != b.rig_id) return a.rig_id < b.rig_id;
	if (a.head_item_id != b.head_item_id) return a.head_item_id < b.head_item_id;
	if (a.head_style_id != b.head_style_id) return a.head_style_id < b.head_style_id;
	if (a.chest_item_id != b.chest_item_id) return a.chest_item_id < b.chest_item_id;
	if (a.chest_style_id != b.chest_style_id) return a.chest_style_id < b.chest_style_id;
	if (a.weapon_item_id != b.weapon_item_id) return a.weapon_item_id < b.weapon_item_id;
	if (a.weapon_style_id != b.weapon_style_id) return a.weapon_style_id < b.weapon_style_id;
	if (a.shield_item_id != b.shield_item_id) return a.shield_item_id < b.shield_item_id;
	if (a.shield_style_id != b.shield_style_id) return a.shield_style_id < b.shield_style_id;
	return FutureSlotsLess(a.future_slots, b.future_slots);
}

inline bool SameKey(const CanonicalActorVisualReachableKey& a,
                    const CanonicalActorVisualReachableKey& b)
{
	return !KeyLess(a, b) && !KeyLess(b, a);
}

// Variation derivation is deliberately constant-default until the server
// catalog publishes a non-default variation as state. The compiler may not
// infer variation from weapon identity or authored profile names.
inline uint16_t VariationForServerAppearanceKey(
	uint16_t presentation_kind_id,
	const AppearanceCatalogItemDef* weapon)
{
	(void)presentation_kind_id;
	(void)weapon;
	return APPEARANCE_VARIATION_DEFAULT;
}

inline bool WeaponPublishesAttackPresentation(const AppearanceCatalogItemDef* weapon)
{
	return !weapon ||
		weapon->swing_presentation_kind_id ==
			APPEARANCE_PRESENTATION_KIND_ATTACK;
}

inline std::vector<uint16_t> EnumerateMountIds()
{
	std::vector<uint16_t> mounts;
	mounts.push_back(ACTOR_VISUAL_REACHABILITY_MOUNT_NONE);
	for (int i = 0; i < kAppearanceCatalogItemCount; i++)
	{
		const AppearanceCatalogItemDef* item = &kAppearanceCatalogItems[i];
		if (item->gameplay_kind == APPEARANCE_CATALOG_GAMEPLAY_MOUNTABLE &&
			item->mount_definition_id != 0)
		{
			mounts.push_back(item->mount_definition_id);
		}
	}
	std::sort(mounts.begin(), mounts.end());
	mounts.erase(std::unique(mounts.begin(), mounts.end()), mounts.end());
	return mounts;
}

// Append the "slot empty" sentinel plus every catalog item whose slot_kind_id
// matches and whose gameplay_kind is wearable or weapon. Equippable items
// only — mountables are enumerated separately as the mount dimension.
inline void PushSlotOption(std::vector<const AppearanceCatalogItemDef*>& out,
                           uint16_t slot_kind_id)
{
	out.push_back(0);
	for (int i = 0; i < kAppearanceCatalogItemCount; i++)
	{
		const AppearanceCatalogItemDef* item = &kAppearanceCatalogItems[i];
		if (item->slot_kind_id == slot_kind_id &&
			(item->gameplay_kind == APPEARANCE_CATALOG_GAMEPLAY_WEARABLE ||
			 item->gameplay_kind == APPEARANCE_CATALOG_GAMEPLAY_WEAPON))
		{
			out.push_back(item);
		}
	}
}

inline void AssignSlot(CanonicalActorVisualReachableKey& key,
                       uint16_t slot_kind_id,
                       const AppearanceCatalogItemDef* item)
{
	if (!item)
		return;
	uint16_t item_id = item->id;
	uint16_t style_id = APPEARANCE_VISUAL_STYLE_DEFAULT;
	switch (slot_kind_id)
	{
		case APPEARANCE_SLOT_KIND_HEAD:
			key.head_item_id = item_id;
			key.head_style_id = style_id;
			break;
		case APPEARANCE_SLOT_KIND_ARMOR:
			key.chest_item_id = item_id;
			key.chest_style_id = style_id;
			break;
		case APPEARANCE_SLOT_KIND_WEAPON:
			key.weapon_item_id = item_id;
			key.weapon_style_id = style_id;
			break;
		case APPEARANCE_SLOT_KIND_SHIELD:
			key.shield_item_id = item_id;
			key.shield_style_id = style_id;
			break;
		default:
		{
			CanonicalActorVisualReachableSlot future = {};
			future.slot_kind_id = slot_kind_id;
			future.item_id = item_id;
			future.visual_style_id = style_id;
			key.future_slots.push_back(future);
			break;
		}
	}
}

inline void AddKey(std::vector<CanonicalActorVisualReachableKey>& keys,
                   uint16_t skin_id,
                   uint16_t presentation_kind_id,
                   uint16_t mount_id,
                   const AppearanceCatalogItemDef* head,
                   const AppearanceCatalogItemDef* chest,
                   const AppearanceCatalogItemDef* weapon,
                   const AppearanceCatalogItemDef* shield)
{
	CanonicalActorVisualReachableKey key = {};
	key.skin_id = skin_id;
	key.actor_style_id = ACTOR_VISUAL_REACHABILITY_ACTOR_STYLE_DEFAULT;
	key.presentation_kind_id = presentation_kind_id;
	key.variation_id = APPEARANCE_VARIATION_DEFAULT;
	key.mount_id = mount_id;
	// Q5.2 + Rig Contract: rig is catalog-owned, not derived from mount presence.
	// Mount IDs that don't map to a catalog entry return RIG_DEFAULT — never
	// invented. This deletes the dual C++ rig derivation seen at FL-4076 / Codex
	// review of server_tick.cpp:1413.
	key.rig_id = AppearanceCatalogRigForMount(mount_id);
	AssignSlot(key, APPEARANCE_SLOT_KIND_HEAD, head);
	AssignSlot(key, APPEARANCE_SLOT_KIND_ARMOR, chest);
	AssignSlot(key, APPEARANCE_SLOT_KIND_WEAPON, weapon);
	AssignSlot(key, APPEARANCE_SLOT_KIND_SHIELD, shield);
	std::sort(key.future_slots.begin(), key.future_slots.end(), SlotLess);
	keys.push_back(key);
}

}  // namespace actor_visual_reachability_detail

inline uint16_t ActorVisualReachabilityVariationForAppearance(
	uint16_t presentation_kind_id,
	uint16_t weapon_item_definition_id)
{
	const AppearanceCatalogItemDef* weapon =
		weapon_item_definition_id ? FindAppearanceCatalogItemById(weapon_item_definition_id) : 0;
	return actor_visual_reachability_detail::VariationForServerAppearanceKey(
		presentation_kind_id, weapon);
}

// Sole owner of the reachable-key enumeration. Q5.2 adds a bidirectional
// matching surface; Q5.1 just owns the enumeration so dump.cpp stops carrying
// hardcoded constants and hand-written for-loops.
inline std::vector<CanonicalActorVisualReachableKey> EnumerateReachableKeys()
{
	using namespace actor_visual_reachability_detail;

	std::vector<const AppearanceCatalogItemDef*> heads;
	std::vector<const AppearanceCatalogItemDef*> chests;
	std::vector<const AppearanceCatalogItemDef*> weapons;
	std::vector<const AppearanceCatalogItemDef*> shields;
	PushSlotOption(heads, APPEARANCE_SLOT_KIND_HEAD);
	PushSlotOption(chests, APPEARANCE_SLOT_KIND_ARMOR);
	PushSlotOption(weapons, APPEARANCE_SLOT_KIND_WEAPON);
	PushSlotOption(shields, APPEARANCE_SLOT_KIND_SHIELD);

	std::vector<uint16_t> mounts = EnumerateMountIds();

	std::vector<CanonicalActorVisualReachableKey> keys;
	for (int profile_index = 0;
	     profile_index < kAppearanceCatalogProfileCount;
	     profile_index++)
	{
		const AppearanceCatalogProfileDef* profile =
			&kAppearanceCatalogProfiles[profile_index];
		const uint16_t skin_id = profile->skin_definition_id;
		if (profile->reachability_policy ==
			APPEARANCE_PROFILE_REACHABILITY_FIXED_COMPANION)
		{
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_IDLE_WALK,
			       ACTOR_VISUAL_REACHABILITY_MOUNT_NONE, 0, 0, 0, 0);
			continue;
		}
		if (profile->reachability_policy ==
			APPEARANCE_PROFILE_REACHABILITY_FIXED_PLAYER)
		{
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_IDLE_WALK,
			       ACTOR_VISUAL_REACHABILITY_MOUNT_NONE, 0, 0, 0, 0);
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_ATTACK,
			       ACTOR_VISUAL_REACHABILITY_MOUNT_NONE, 0, 0, 0, 0);
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_DEATH,
			       ACTOR_VISUAL_REACHABILITY_MOUNT_NONE, 0, 0, 0, 0);
			continue;
		}

		for (const AppearanceCatalogItemDef* head : heads)
		for (const AppearanceCatalogItemDef* chest : chests)
		for (const AppearanceCatalogItemDef* weapon : weapons)
		for (const AppearanceCatalogItemDef* shield : shields)
		for (uint16_t mount_id : mounts)
		{
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_IDLE_WALK,
			       mount_id, head, chest, weapon, shield);
			keys.back().variation_id = VariationForServerAppearanceKey(
				APPEARANCE_PRESENTATION_KIND_IDLE_WALK, weapon);
			AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_DEATH,
			       mount_id, head, chest, weapon, shield);
			keys.back().variation_id = VariationForServerAppearanceKey(
				APPEARANCE_PRESENTATION_KIND_DEATH, weapon);

			if (WeaponPublishesAttackPresentation(weapon))
			{
				AddKey(keys, skin_id, APPEARANCE_PRESENTATION_KIND_ATTACK,
				       mount_id, head, chest, weapon, shield);
				keys.back().variation_id = VariationForServerAppearanceKey(
					APPEARANCE_PRESENTATION_KIND_ATTACK, weapon);
			}
		}
	}
	std::sort(keys.begin(), keys.end(), KeyLess);
	keys.erase(std::unique(keys.begin(), keys.end(), SameKey), keys.end());
	return keys;
}

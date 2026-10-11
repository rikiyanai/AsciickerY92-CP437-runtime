#pragma once

// Server-owned player skin selection (2026-10-08 spooky skins).
//
// Clients only ever send a requested alias (JOIN_V2 requested_skin) or a
// cycle request ('S'). The server resolves the alias here, validates the
// target against the appearance catalog, and applies it through the normal
// appearance owner. Clients never send profile or skin ids.
//
// Every selectable profile must be a FIXED_PLAYER catalog profile with no
// starter loadout: those profiles publish exactly the no-equipment
// IDLE_WALK / ATTACK / DEATH keys, so switching between them can never
// produce a CompiledActorVisualKey without a compiled row.

#include <stddef.h>
#include <stdint.h>

#include "actor_visual_catalog_source.h"
#include "protocol/protocol_join.h"  // APPEARANCE_REQUESTED_SKIN_CAP

struct AppearanceSelectableSkinDef
{
	const char* alias;
	uint16_t appearance_profile_id;
};

// Order is the in-game cycle order. Index 0 is the default skin and keeps the
// pre-existing behaviour: the Wallace scene profile.
static constexpr AppearanceSelectableSkinDef kAppearanceSelectableSkins[] = {
	{"wallace", APPEARANCE_CATALOG_WALLACE_PROFILE_ID},
	{"grandma", APPEARANCE_CATALOG_GRANDMA_PROFILE_ID},
	{"dracula", APPEARANCE_CATALOG_DRACULA_PROFILE_ID},
	{"witch", APPEARANCE_CATALOG_WITCH_PROFILE_ID},
};
static constexpr int kAppearanceSelectableSkinCount =
	sizeof(kAppearanceSelectableSkins) / sizeof(kAppearanceSelectableSkins[0]);
static constexpr int APPEARANCE_SELECTABLE_SKIN_DEFAULT_INDEX = 0;

namespace appearance_skin_selection_detail {

constexpr bool CatalogProfileIsSelectable(uint16_t profile_id, int index = 0)
{
	return index >= kAppearanceCatalogProfileCount
		? false
		: (kAppearanceCatalogProfiles[index].id == profile_id
			? (kAppearanceCatalogProfiles[index].reachability_policy ==
				   APPEARANCE_PROFILE_REACHABILITY_FIXED_PLAYER &&
			   kAppearanceCatalogProfiles[index].starter_count == 0)
			: CatalogProfileIsSelectable(profile_id, index + 1));
}

constexpr bool AllSelectableSkinsAreFixedPlayers(int index = 0)
{
	return index >= kAppearanceSelectableSkinCount
		? true
		: (CatalogProfileIsSelectable(
			   kAppearanceSelectableSkins[index].appearance_profile_id) &&
		   AllSelectableSkinsAreFixedPlayers(index + 1));
}

constexpr size_t AliasLength(const char* alias, size_t n = 0)
{
	return alias[n] ? AliasLength(alias, n + 1) : n;
}

constexpr bool AllAliasesFitWire(int index = 0)
{
	return index >= kAppearanceSelectableSkinCount
		? true
		: (AliasLength(kAppearanceSelectableSkins[index].alias) > 0 &&
		   AliasLength(kAppearanceSelectableSkins[index].alias) <
			   APPEARANCE_REQUESTED_SKIN_CAP &&
		   AllAliasesFitWire(index + 1));
}

}  // namespace appearance_skin_selection_detail

static_assert(kAppearanceSelectableSkinCount > 0,
              "at least one selectable skin is required");
static_assert(kAppearanceSelectableSkins[APPEARANCE_SELECTABLE_SKIN_DEFAULT_INDEX]
                      .appearance_profile_id == APPEARANCE_CATALOG_WALLACE_PROFILE_ID,
              "default selectable skin must stay the Wallace scene profile");
static_assert(appearance_skin_selection_detail::AllSelectableSkinsAreFixedPlayers(),
              "every selectable skin must be a FIXED_PLAYER catalog profile without starters");
static_assert(appearance_skin_selection_detail::AllAliasesFitWire(),
              "every selectable alias must fit STRUCT_REQ_JOIN_V2::requested_skin");

enum AppearanceSkinRequestOutcome : uint8_t
{
	APPEARANCE_SKIN_REQUEST_NONE = 0,     // empty field: default skin
	APPEARANCE_SKIN_REQUEST_DEFAULT = 1,  // explicit "default": default skin
	APPEARANCE_SKIN_REQUEST_MATCHED = 2,  // known alias
	APPEARANCE_SKIN_REQUEST_INVALID = 3,  // unknown or malformed: default skin
};

static inline const char* AppearanceSkinRequestOutcomeString(uint8_t outcome)
{
	switch (outcome)
	{
		case APPEARANCE_SKIN_REQUEST_NONE: return "none";
		case APPEARANCE_SKIN_REQUEST_DEFAULT: return "default";
		case APPEARANCE_SKIN_REQUEST_MATCHED: return "matched";
		case APPEARANCE_SKIN_REQUEST_INVALID: return "invalid";
		default: return "unknown";
	}
}

static inline bool AppearanceSkinAliasEquals(const char* field, size_t cap, const char* alias)
{
	size_t i = 0;
	for (; i < cap; i++)
	{
		char c = field[i];
		if (c >= 'A' && c <= 'Z')
			c = (char)(c - 'A' + 'a');
		if (c != alias[i])
			return false;
		if (c == 0)
			return true;
	}
	return false;
}

// Resolve a client-supplied requested_skin field (cap bytes, not trusted to be
// terminated) to an index into kAppearanceSelectableSkins. Never fails: an
// empty, "default", unterminated, or unknown request resolves to the default
// skin and reports why through out_outcome.
static inline int ResolveAppearanceSkinRequest(const char* field,
                                               size_t cap,
                                               uint8_t* out_outcome)
{
	uint8_t outcome = APPEARANCE_SKIN_REQUEST_INVALID;
	int index = APPEARANCE_SELECTABLE_SKIN_DEFAULT_INDEX;
	bool terminated = false;
	for (size_t i = 0; field && i < cap; i++)
	{
		if (field[i] == 0)
		{
			terminated = true;
			break;
		}
	}
	if (!field || cap == 0 || field[0] == 0)
	{
		outcome = APPEARANCE_SKIN_REQUEST_NONE;
	}
	else if (terminated)
	{
		if (AppearanceSkinAliasEquals(field, cap, "default"))
		{
			outcome = APPEARANCE_SKIN_REQUEST_DEFAULT;
		}
		else
		{
			for (int s = 0; s < kAppearanceSelectableSkinCount; s++)
			{
				if (AppearanceSkinAliasEquals(field, cap, kAppearanceSelectableSkins[s].alias))
				{
					outcome = APPEARANCE_SKIN_REQUEST_MATCHED;
					index = s;
					break;
				}
			}
		}
	}
	if (out_outcome)
		*out_outcome = outcome;
	return index;
}

// Index of the skin after current_profile_id in cycle order. A profile that is
// not selectable (e.g. the default full-player profile) cycles to index 0.
static inline int NextAppearanceSelectableSkinIndex(uint16_t current_profile_id)
{
	for (int s = 0; s < kAppearanceSelectableSkinCount; s++)
	{
		if (kAppearanceSelectableSkins[s].appearance_profile_id == current_profile_id)
			return (s + 1) % kAppearanceSelectableSkinCount;
	}
	return APPEARANCE_SELECTABLE_SKIN_DEFAULT_INDEX;
}

// Runtime twin of the static_assert above, for the equipment guard: only full
// player profiles enumerate equipment/mount keys.
static inline bool AppearanceCatalogProfileAllowsEquipment(uint16_t profile_id)
{
	if (profile_id == 0)
		profile_id = APPEARANCE_CATALOG_DEFAULT_PROFILE_ID;
	for (int i = 0; i < kAppearanceCatalogProfileCount; i++)
	{
		if (kAppearanceCatalogProfiles[i].id == profile_id)
			return kAppearanceCatalogProfiles[i].reachability_policy ==
				APPEARANCE_PROFILE_REACHABILITY_FULL_PLAYER;
	}
	return false;
}

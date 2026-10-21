// engine/local_player_state.cpp — LocalPlayerState helpers
//
// Appearance bootstrap and skin-apply functions moved from the old
// GameEnsureLocalPlayerAppearance / GameApplyLocalPlayerSkin helpers
// (which took Game*) to explicit LocalPlayerState functions that
// take only the state they need plus explicit inst/world dependencies.

#include <string.h>
#include <stdio.h>
#include <stdint.h>

#include "local_player_state.h"
#include "actor_visual_profile.h"
#include "actor_visual_profile_runtime.h"
#include "actor_presentation_result.h"
#include "authoritative_presentation_adapters.h"
#include "world.h"
#include "platform/time_backend.h"

bool EnsureLocalPlayerAppearance(LocalPlayerState& player)
{
	if (player.appearance_v2.valid)
		return true;

	// Build a minimal valid AppearanceStateV2 from the compiled profile's first
	// skin. This mirrors what the server does via
	// SvrApplyProfileToAppearance but stays local-only.

	uint16_t first_skin_id = ACTOR_VISUAL_PROFILE_DEFAULT_SKIN_ID;
	if (first_skin_id == 0)
		return false;

	// Populate the minimal appearance state.  Only skin_definition_id and a
	// single body entry are needed; items/mounts are optional.
	AppearanceStateV2* a = &player.appearance_v2;
	memset(a, 0, sizeof(*a));
	a->valid = true;
	a->appearance_contract_version = 1;
	a->appearance_profile_id = 200;  // default profile id from bundle
	a->skin_definition_id = first_skin_id;
	a->source_kind = 1;   // SVR_APPEARANCE_SOURCE_DEFAULT_PROFILE
	a->projection_kind = 1; // SVR_APPEARANCE_PROJECTION_PROFILE
	a->subject_kind = 1;  // SVR_APPEARANCE_SUBJECT_DEFAULT
	snprintf(a->subject_key, sizeof(a->subject_key), "editor_local");
	a->entry_count = 1;
	a->entries[0].slot_kind_id = APPEARANCE_SLOT_KIND_BODY;
	a->entries[0].item_definition_id = first_skin_id;
	a->entries[0].visual_style_id = APPEARANCE_VISUAL_STYLE_DEFAULT;
	a->entries[0].state_flags = APPEARANCE_ENTRY_STATE_EQUIPPED;

	return true;
}

bool ApplyLocalPlayerSkin(LocalPlayerState& player, uint16_t skin_id, uint64_t stamp, World* world)
{
	if (skin_id == 0)
		return false;

	// Ensure we have a valid base appearance.
	if (!EnsureLocalPlayerAppearance(player))
		return false;

	// Update the skin_definition_id — this is the V2 bundle ID path.
	player.appearance_v2.skin_definition_id = skin_id;

	// Re-resolve the presentation immediately so the visual change is instant.
	ActorPresentationResult resolved = ResolveLocalWallClockCharacterPresentation(
		&player, player.clr, 0, stamp);
	if (!resolved.sprite)
		return false;

	player.sprite = resolved.sprite;
	player.anim = resolved.anim;
	player.frame = resolved.frame;

	// Update the world inst so the running game window renders the new sprite.
	if (player.player_inst && world)
	{
		int reps[4] = {0, 0, 0, 0};
		UpdateSpriteInst(world, player.player_inst, player.sprite,
			player.pos, player.dir,
			player.anim, player.frame, reps);
	}
	return true;
}

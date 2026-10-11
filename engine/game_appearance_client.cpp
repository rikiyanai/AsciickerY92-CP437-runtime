// game_appearance_client.cpp -- ActorVisualProfile helpers
// extracted from game.cpp
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include "actor_visual_profile.h"

// FL-4049: bundle catalog ownership was deleted. Until content-owned profile
// catalog loading exists, expose the one source-known default skin id.
int GameGetBundleSkinIds(uint16_t* out_ids, int max_ids)
{
	if (!out_ids || max_ids <= 0)
		return 0;
	out_ids[0] = ACTOR_VISUAL_PROFILE_DEFAULT_SKIN_ID;
	return 1;
}

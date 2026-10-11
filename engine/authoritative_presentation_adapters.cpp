#include "authoritative_presentation_adapters.h"

#include <string.h>

#include "actor_visual_profile_runtime.h"
#include "game.h"

// FL-4073: deleted LOCAL_ATTACK_PRESENTATION_US_PER_FRAME (was 20000) and the
// non-locomotion wall-clock branch that forced out.anim=0 and divided
// stamp_us by that hardcoded cadence. Both violated the bundle-refactor
// addability rule: per-frame cadence and anim-track selection are
// CompiledActorVisualRow concerns, not per-driver C++ branches. Frame
// computation for every presentation_kind now flows through
// ResolveActorVisualProfilePresentation, which reads playback_mode,
// steady_frame_index, and locomotion_anim_track[] from the row.

ActorPresentationResult ResolveWallClockActorPresentation(
	const ActorPresentationInput& input,
	int step_phase,
	uint64_t stamp_us,
	const Character* fallback_character)
{
	(void)step_phase;
	(void)stamp_us;
	ActorPresentationResult out = ResolveActorVisualProfilePresentation(input);
	if (!out.sprite && fallback_character &&
		fallback_character->presentation_selector_failure_reason != ACTOR_VISUAL_PROFILE_FAILURE_NONE &&
		out.selector_failure_reason == ACTOR_VISUAL_PROFILE_FAILURE_NONE)
	{
		out.selector_failure_reason = fallback_character->presentation_selector_failure_reason;
		out.selector_found = 0;
	}
	return out;
}

// --- Public adapters --------------------------------------------------------

bool GetActorAppearanceStateV2FromSnapshotNpc(
	const ServerSnapshotNpcRepository::SnapshotNpcState* sn,
	AppearanceStateV2* out_state)
{
	if (!sn || !out_state)
		return false;
	*out_state = sn->appearance_v2;
	return out_state->valid;
}

ActorPresentationResult ResolveRemoteAuthoritativeCharacterPresentation(
	const Character* c, int clr, uint32_t authoritative_tick)
{
	if (!c)
		return ActorPresentationResult{};

	AppearanceStateV2 appearance_state = {};
	// Reuse the snapshot NPC extraction path temporarily. For Character* the
	// state lives on c->appearance_v2 — the helper accepts it by pointer.
	// TODO: extract inline after the old facade is fully retired.
	// For now build the input directly from Character fields.
	appearance_state = c->appearance_v2;
	if (!appearance_state.valid)
		memset(&appearance_state, 0, sizeof(appearance_state));

	ActorPresentationInput input = {};
	input.appearance_state = &appearance_state;
	input.presentation_kind_id = c->presentation_kind_id;
	input.life_state = c->life_state;
	input.locomotion_state = c->locomotion_state;
	input.combat_state = c->combat_state;
	input.mount_state = c->mount_state;
	input.clr = clr;
	input.authoritative_tick = authoritative_tick;
	input.presentation_started_tick = c->presentation_started_tick;
	return ResolveActorVisualProfilePresentation(input);
}

ActorPresentationResult ResolveLocalWallClockCharacterPresentation(
	const Character* c, int clr, int step_phase, uint64_t stamp_us)
{
	if (!c)
		return ActorPresentationResult{};

	AppearanceStateV2 appearance_state = c->appearance_v2;
	if (!appearance_state.valid)
		memset(&appearance_state, 0, sizeof(appearance_state));

	ActorPresentationInput input = {};
	input.appearance_state = &appearance_state;
	input.presentation_kind_id = c->presentation_kind_id;
	input.life_state = c->life_state;
	input.locomotion_state = c->locomotion_state;
	input.combat_state = c->combat_state;
	input.mount_state = c->mount_state;
	input.clr = clr;
	input.authoritative_tick = (uint32_t)(stamp_us / 1000ull);
	input.presentation_started_tick = c->presentation_started_tick;
	return ResolveWallClockActorPresentation(input, step_phase, stamp_us, c);
}

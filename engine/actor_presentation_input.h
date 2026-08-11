#pragma once

#include <stdint.h>

#include "actor_visual_profile.h"

struct ActorPresentationInput
{
	const AppearanceStateV2* appearance_state;
	uint16_t presentation_kind_id;
	uint8_t life_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	uint8_t mount_state;
	int clr;
	uint32_t authoritative_tick;
	uint32_t presentation_started_tick;
};

// Callers building an ActorPresentationInput from a raw Character/SnapshotNpcState
// should copy the AppearanceStateV2 to a local, point appearance_state at it, and
// fill the remaining fields from the character/npc struct.

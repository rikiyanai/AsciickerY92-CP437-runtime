#pragma once

#include <stdint.h>

#include "actor_presentation_result.h"
#include "../server/snapshot_npc_repository.h"

struct Character;

bool GetActorAppearanceStateV2FromSnapshotNpc(
	const ServerSnapshotNpcRepository::SnapshotNpcState* sn,
	AppearanceStateV2* out_state);

ActorPresentationResult ResolveRemoteAuthoritativeCharacterPresentation(
	const Character* c, int clr, uint32_t authoritative_tick);

ActorPresentationResult ResolveLocalWallClockCharacterPresentation(
	const Character* c, int clr, int step_phase, uint64_t stamp_us);

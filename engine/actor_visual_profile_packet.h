#pragma once

// ActorVisualProfile packet adapter.
//
// Copies server-owned appearance packet ids into the local ActorVisualProfile.
// This is packet transport only; it does not resolve sprites, wrappers, layers,
// RenderPlans, or fallback visuals.

#include "actor_visual_profile.h"
#include "protocol/protocol_join.h"

static inline void ApplyActorVisualProfilePacketToClientState(
	ActorVisualProfile* out,
	const STRUCT_BRC_APPEARANCE_STATE_V2* packet)
{
	if (!out || !packet)
		return;
	*out = ActorVisualProfile();
	out->valid = true;
	out->loadout_revision = packet->loadout_revision;
	out->appearance_contract_version = packet->appearance_contract_version;
	out->appearance_profile_id = packet->appearance_profile_id;
	out->skin_id = packet->skin_definition_id;
	out->mount_id = packet->mount_definition_id;
	out->variation_id = packet->variation_id;
	out->rig_id = packet->rig_id;
	out->source_kind = packet->source_kind;
	out->projection_kind = packet->projection_kind;
	out->subject_kind = packet->subject_kind;
	out->slot_count = packet->entry_count <= APPEARANCE_STATE_V2_MAX_ENTRIES
		? packet->entry_count
		: APPEARANCE_STATE_V2_MAX_ENTRIES;
	for (int i = 0; i < (int)out->slot_count; i++)
	{
		out->slots[i].slot_kind_id = packet->entries[i].slot_kind_id;
		out->slots[i].item_definition_id = packet->entries[i].item_definition_id;
		out->slots[i].visual_style_id = packet->entries[i].visual_style_id;
		out->slots[i].state_flags = packet->entries[i].state_flags;
	}
	for (int i = 0; i < (int)sizeof(out->subject_key) - 1; i++)
	{
		out->subject_key[i] = packet->subject_key[i];
		if (out->subject_key[i] == 0)
			return;
	}
	out->subject_key[sizeof(out->subject_key) - 1] = 0;
}

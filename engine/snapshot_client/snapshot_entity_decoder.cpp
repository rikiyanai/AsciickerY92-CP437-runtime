#include "snapshot_client/snapshot_entity_decoder.h"

#include <stdio.h>
#include <string.h>

#include "actor_visual_profile_runtime.h"
#include "game.h"
#include "multiplayer_protocol.h"

namespace
{
static uint16_t rd_u16(const uint8_t* p)
{
	uint16_t v = 0;
	memcpy(&v, p, sizeof(v));
	return v;
}

static int16_t rd_i16(const uint8_t* p)
{
	int16_t v = 0;
	memcpy(&v, p, sizeof(v));
	return v;
}

static uint32_t rd_u32(const uint8_t* p)
{
	uint32_t v = 0;
	memcpy(&v, p, sizeof(v));
	return v;
}

static float rd_f32(const uint8_t* p)
{
	float v = 0.0f;
	memcpy(&v, p, sizeof(v));
	return v;
}
}

bool ValidateAppearanceRuntimeInputs(
	uint8_t* life_state,
	uint8_t* mount_state,
	uint8_t* locomotion_state,
	uint8_t* combat_state,
	uint16_t* presentation_kind_id,
	const char* source,
	uint16_t entity_id,
	uint8_t entity_type)
{
	if (!life_state || !mount_state || !locomotion_state || !combat_state || !presentation_kind_id)
		return false;

	if (!AppearanceRuntimeLifeStateKnown(*life_state))
	{
		fprintf(stderr, "[snapshot-ingest] invalid life_state=%u source=%s entity=%u type=%u\n",
			(unsigned)*life_state, source ? source : "(null)",
			(unsigned)entity_id, (unsigned)entity_type);
		return false;
	}
	if (*mount_state >= MOUNT::SIZE)
	{
		fprintf(stderr, "[snapshot-ingest] invalid mount_state=%u source=%s entity=%u type=%u\n",
			(unsigned)*mount_state, source ? source : "(null)",
			(unsigned)entity_id, (unsigned)entity_type);
		return false;
	}
	if (!AppearanceRuntimeLocomotionStateKnown(*locomotion_state))
	{
		fprintf(stderr, "[snapshot-ingest] invalid locomotion_state=%u source=%s entity=%u type=%u\n",
			(unsigned)*locomotion_state, source ? source : "(null)",
			(unsigned)entity_id, (unsigned)entity_type);
		return false;
	}
	if (!AppearanceRuntimeCombatStateKnown(*combat_state))
	{
		fprintf(stderr, "[snapshot-ingest] invalid combat_state=%u source=%s entity=%u type=%u\n",
			(unsigned)*combat_state, source ? source : "(null)",
			(unsigned)entity_id, (unsigned)entity_type);
		return false;
	}
	if (!AppearanceRuntimePresentationKindKnown(*presentation_kind_id))
	{
		fprintf(stderr, "[snapshot-ingest] invalid presentation_kind_id=%u source=%s entity=%u type=%u\n",
			(unsigned)*presentation_kind_id, source ? source : "(null)",
			(unsigned)entity_id, (unsigned)entity_type);
		return false;
	}
	return true;
}

bool ParseAuthoritativeSnapshotEntity(
	const uint8_t* raw,
	int raw_size,
	SnapshotEntityDecoded* out)
{
	if (!raw || !out || raw_size < (int)sizeof(STRUCT_SNAPSHOT_ENTITY))
		return false;

	out->entity_id = rd_u16(raw + 0);
	out->entity_type = raw[2];
	out->life_state = raw[3];
	out->mount_state = raw[4];
	out->locomotion_state = raw[5];
	out->combat_state = raw[6];
	out->presentation_kind_id = rd_u16(raw + 7);
	out->state_flags = (uint16_t)raw[9];
	out->pos[0] = rd_f32(raw + 10);
	out->pos[1] = rd_f32(raw + 14);
	out->pos[2] = rd_f32(raw + 18);
	out->dir = rd_f32(raw + 22);
	out->hp = rd_i16(raw + 26);
	out->max_hp = rd_i16(raw + 28);
	out->last_authoritative_tick = rd_u32(raw + 30);
	out->presentation_started_tick = rd_u32(raw + 34);
	out->applied_input_seq = rd_u16(raw + 38);
	out->vel[0] = rd_f32(raw + 40);
	out->vel[1] = rd_f32(raw + 44);
	out->vel[2] = rd_f32(raw + 48);
	out->yaw = rd_f32(raw + 52);
	out->yaw_vel = rd_f32(raw + 56);
	out->slope = rd_f32(raw + 60);
	out->accum_contact = rd_f32(raw + 64);
	out->knockback[0] = rd_f32(raw + 68);
	out->knockback[1] = rd_f32(raw + 72);
	out->terrain_z = rd_f32(raw + 76);
	out->support_z = rd_f32(raw + 80);
	out->support_item_id = rd_u16(raw + 84);
	out->support_source = raw[86];
	out->support_valid = raw[87];
	if (out->state_flags & SNAPSHOT_STATE_REMOVE)
		return true;
	if (!ValidateAppearanceRuntimeInputs(
			&out->life_state,
			&out->mount_state,
			&out->locomotion_state,
			&out->combat_state,
			&out->presentation_kind_id,
			"SNAPSHOT_ENTITY",
			out->entity_id,
			out->entity_type))
		return false;
	return true;
}

void CopyNormalizedPlayerSnapshotEntity(
	const SnapshotEntityDecoded* in,
	STRUCT_SNAPSHOT_ENTITY* out)
{
	if (!in || !out)
		return;
	memset(out, 0, sizeof(*out));
	out->entity_id = in->entity_id;
	out->entity_type = SNAPSHOT_ENTITY_PLAYER;
	out->life_state = in->life_state;
	out->mount_state = in->mount_state;
	out->locomotion_state = in->locomotion_state;
	out->combat_state = in->combat_state;
	out->presentation_kind_id = in->presentation_kind_id;
	out->state_flags = (uint8_t)(in->state_flags & 0xFFu);
	out->pos[0] = in->pos[0];
	out->pos[1] = in->pos[1];
	out->pos[2] = in->pos[2];
	out->dir = in->dir;
	out->hp = in->hp;
	out->max_hp = in->max_hp;
	out->vel[0] = in->vel[0];
	out->vel[1] = in->vel[1];
	out->vel[2] = in->vel[2];
	out->yaw = in->yaw;
	out->yaw_vel = in->yaw_vel;
	out->slope = in->slope;
	out->accum_contact = in->accum_contact;
	out->knockback[0] = in->knockback[0];
	out->knockback[1] = in->knockback[1];
	out->terrain_z = in->terrain_z;
	out->support_z = in->support_z;
	out->support_item_id = in->support_item_id;
	out->support_source = in->support_source;
	out->support_valid = in->support_valid;
	out->last_authoritative_tick = in->last_authoritative_tick;
	out->presentation_started_tick = in->presentation_started_tick;
	out->applied_input_seq = in->applied_input_seq;
}

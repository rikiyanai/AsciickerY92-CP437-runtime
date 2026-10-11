#pragma once

#include <stdint.h>

struct STRUCT_SNAPSHOT_ENTITY;

struct SnapshotEntityDecoded
{
	uint16_t entity_id;
	uint8_t entity_type;
	uint8_t life_state;
	uint8_t mount_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	uint16_t presentation_kind_id;
	uint16_t state_flags;
	float pos[3];
	float dir;
	float vel[3];
	float yaw;
	float yaw_vel;
	float slope;
	float accum_contact;
	float knockback[2];
	float terrain_z; // FL-2957: server-sampled terrain height for floor coherence
	float support_z; // FL-4137: server-authored support hit height
	uint16_t support_item_id; // FL-4137: placed block item id when support_source is placed block
	uint8_t support_source; // MpSupportSource value from server/mp_step.h
	uint8_t support_valid;
	int16_t hp;
	int16_t max_hp;
	uint32_t last_authoritative_tick;
	uint32_t presentation_started_tick;
	uint16_t applied_input_seq;
};

bool ValidateAppearanceRuntimeInputs(
	uint8_t* life_state,
	uint8_t* mount_state,
	uint8_t* locomotion_state,
	uint8_t* combat_state,
	uint16_t* presentation_kind_id,
	const char* source,
	uint16_t entity_id,
	uint8_t entity_type);

bool ParseAuthoritativeSnapshotEntity(
	const uint8_t* raw,
	int raw_size,
	SnapshotEntityDecoded* out);

void CopyNormalizedPlayerSnapshotEntity(
	const SnapshotEntityDecoded* in,
	STRUCT_SNAPSHOT_ENTITY* out);

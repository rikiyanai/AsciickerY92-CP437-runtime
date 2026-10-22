#pragma once

struct AuthoritativeItemState
{
	uint16_t item_id;
	uint16_t owner_id; // 0xFFFF = world/no-owner
	uint16_t item_definition_id;
	uint16_t visual_style_id;
	uint16_t equip_slot_kind_id;
	uint16_t v2_state_flags;
	uint8_t last_kind;
	uint8_t v2_valid;
	uint8_t valid;
	float pos[3];
	uint32_t last_event_id;
	uint32_t last_event_tick;
};

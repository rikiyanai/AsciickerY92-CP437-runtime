#pragma once

#include <stdint.h>

// Snapshot stream observability extracted from Server.
struct SnapshotClientState
{
	uint16_t last_snapshot_seq = 0;
	uint32_t last_snapshot_tick = 0;
	uint32_t snapshot_packets = 0;
	uint32_t snapshot_gap_count = 0;
	uint32_t snapshot_rejected_stale_seq_count = 0;
	uint32_t snapshot_rejected_stale_tick_count = 0;
	uint32_t snapshot_rejected_delta_gap_count = 0;
	uint16_t snapshot_last_rejected_seq = 0;
	uint32_t snapshot_last_rejected_tick = 0;
	uint32_t snapshot_ack_packets = 0;
	uint16_t last_snapshot_ack_seq = 0;
	uint32_t last_snapshot_ack_tick = 0;
	uint64_t last_snapshot_wall_stamp_us = 0;
	uint32_t snapshot_last_entity_count = 0;
	uint8_t snapshot_last_is_delta = 0;
	uint8_t snapshot_last_local_present = 0;
	uint8_t snapshot_last_local_pose_sane = 0;
	uint8_t snapshot_last_local_applied = 0;
	uint32_t snapshot_last_local_apply_reason = 0; // 0 none, 1 no ent/game, 2 no world core, 3 bad local id, 4 entity mismatch, 5 bad pose, 6 stale-origin reject, 7 apply reject, 8 accepted
	uint16_t snapshot_last_local_entity_id = 0;
	float snapshot_last_local_pos[3]{};
	uint8_t snapshot_last_local_support_valid = 0;
	uint8_t snapshot_last_local_support_source = 0;
	uint16_t snapshot_last_local_support_item_id = 0;
	float snapshot_last_local_support_z = 0.0f;
	uint32_t snapshot_npc_entities_last = 0;
	uint32_t snapshot_npc_entities_total = 0;
};

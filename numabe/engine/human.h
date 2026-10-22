#pragma once

// human.h — Human actor state (players and human-type NPCs)
//
// PURPOSE:
// Extends Character with name, level, talk boxes, multiplayer combat
// debug observability counters, movement state, and remote presentation
// tracking. Also defines the empty NPC specializations (NPC_Creature,
// NPC_Human). Extracted from game.h.

#include <stdint.h>

#include "character.h"
#include "item_owner.h"
#include "talkbox.h"
#include "mp_move.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"

struct Human : Character
{
	char name[32*4];
	char name_cp437[32];

	int level;

	// REMOVED: max_hp / cur_hp — duplicate of Character::HP / Character::MAX_HP.
	// Character::HP is the authoritative HP field used by all multiplayer damage,
	// death detection, and HP bar rendering. These aliases created a split-read
	// hazard (FL-164 / FL-274) where a developer could write to cur_hp believing
	// it authoritative while Character::HP remained the live field. Deleted 2026-04-04.
	// NOTE(dead): max_xp/cur_xp/pr/max_mp/cur_mp/max_speed/cur_speed/max_power/cur_power/
	// prot_hit/prot_fire/nutr_* (17 fields) deleted — never referenced anywhere in C++.

	// -------------

	void Say(const char* str, int len, uint64_t stamp);

	TalkBox* talk_box;

	struct Talk
	{
		uint64_t stamp;
		TalkBox* box;
		float pos[3];
	};

	int talks;
	Talk talk[3];

	// Multiplayer combat sync (Phase 21)
	uint32_t dbg_obs_death_seq;
	uint32_t dbg_obs_last_death_source;
	uint32_t dbg_obs_respawn_seq;
	uint32_t dbg_obs_corpse_create_seq;
	uint32_t dbg_obs_corpse_delete_seq;
	uint32_t dbg_obs_corpse_create_count;
	uint32_t dbg_obs_corpse_delete_count;
	uint32_t dbg_obs_last_corpse_create_reason;
	uint32_t dbg_obs_last_corpse_delete_reason;
	uint32_t dbg_obs_death_transition_count;
	uint32_t dbg_obs_first_death_transition_source;
	uint32_t dbg_obs_death_transition_source31_count;
	uint32_t dbg_obs_death_transition_source21_count;
	uint32_t dbg_obs_death_transition_source24_count;
	int dbg_obs_first_death_transition_setaction_ok;
	int dbg_obs_first_death_transition_post_action;
	int dbg_obs_first_death_transition_post_mount;
	int dbg_obs_first_death_transition_frame;
	int dbg_obs_first_death_transition_sprite_family_kind;
	uint32_t dbg_obs_last_death_transition_source;
	int dbg_obs_last_death_transition_setaction_ok;
	int dbg_obs_last_death_transition_pre_action;
	int dbg_obs_last_death_transition_pre_mount;
	int dbg_obs_last_death_transition_post_action;
	int dbg_obs_last_death_transition_post_mount;
	int dbg_obs_last_death_transition_frame;
	int dbg_obs_last_death_transition_sprite_family_kind;
	uint32_t dbg_obs_death_snapshot_count;
	int dbg_obs_first_death_snapshot_life_state;
	int dbg_obs_first_death_snapshot_mount_state;
	int dbg_obs_first_death_snapshot_locomotion_state;
	int dbg_obs_first_death_snapshot_presentation_kind_id;
	uint32_t dbg_obs_first_death_snapshot_tick;
	int dbg_obs_last_death_snapshot_life_state;
	int dbg_obs_last_death_snapshot_mount_state;
	int dbg_obs_last_death_snapshot_locomotion_state;
	int dbg_obs_last_death_snapshot_presentation_kind_id;
	uint32_t dbg_obs_last_death_snapshot_tick;

	MpMoveState mp_move; // local authoritative movement/replay bookkeeping only
	RemoteActorPresentationTrack remote_presentation_track;
};

struct NPC_Creature : Character, ItemOwner {};
struct NPC_Human : Human, ItemOwner {};

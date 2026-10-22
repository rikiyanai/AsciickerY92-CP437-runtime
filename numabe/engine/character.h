#pragma once

// character.h — Base entity state for all actors
//
// PURPOSE:
// Core fields shared by every actor in the game: position, direction,
// impulse, HP, animation, life/mount/locomotion/combat state, presentation
// kind, appearance, physics data, and AI targeting. All player types
// (Human, NPC_Creature, NPC_Human) inherit from this.
// Extracted from game.h.

#include <stdint.h>

#include "actor_visual_profile.h"

struct Sprite;
struct Inst;

struct Character
{
	// recolor?
	Sprite* sprite;

	int anim;
	int frame;
	float pos[3];
	float dir;
	float impulse[2];

	int HP;
	int MAX_HP;

	// Local single-player/editor presentation timing. Multiplayer snapshots keep
	// authoritative timing in presentation_started_tick instead.
	uint64_t action_stamp;

	Character* prev;
	Character* next;

	uint8_t life_state;
	uint8_t mount_state;
	uint8_t locomotion_state;
	uint8_t combat_state;
	uint16_t presentation_kind_id;
	uint8_t presentation_selector_failure_reason;
	uint32_t presentation_started_tick;
	AppearanceStateV2 appearance_v2;

	int leak; // blood / guts
	int leak_steps;

	Inst* inst; // only server players
	int clr;
	int stuck;
	int around;
	float unstuck[2][3]; // [0]unstuck, [1]candid
	void* data; // npc physics
	Character* master;
	Character* target; // can be 0, master or any enemy
	int followers;
	bool jump; // helper if got stuck
	bool enemy; // buddy otherwise!
};

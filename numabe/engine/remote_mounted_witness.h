#pragma once

#include <stdint.h>

#include "actor_presentation_result.h"

struct Game;
struct Human;
struct Server;

struct RemoteActorPresentationMaterializationResult
{
	int recreate_reason;
	int has_inst;
	int inst_world_match;
	int inst_visible;
	int on_screen;
	int body_visible;
	int label_visible;
	int label_only;
	int inst_sprite_family_kind;
	int inst_sprite_matches_owner;
	int view_x;
	int view_y;
};

struct RemoteActorPresentationDebugSurface
{
	float render_pos[3];
	float render_dir;
	int interp_active;
	int interp_ring_depth;
	float interp_delay_ms;
	float interp_lerp_t;
	int interp_fallback_mode;
	int interp_newest_tick;
	int interp_older_tick;
	float interp_newest_wall_age_ms;
	float interp_older_wall_age_ms;
	float interp_target_age_ms;
	RemoteActorPresentationMaterializationResult materialized;
};

struct RemoteMountedWitnessPublishInput
{
	const Human* remote;
	int remote_pid;
	uint32_t render_tick;
	int screen_center_glyph;
	int render_sprite_family_kind;
	uint32_t resolved_presentation_key;
	int sprite_family_kind;
	const ActorPresentationResult* resolved;
	const RemoteActorPresentationDebugSurface* surface;
};

void RemoteMountedWitnessResetObserverDeathHistory(Human* remote);
void RemoteMountedWitnessNoteObserverDeathSnapshot(
	Human* remote,
	uint8_t life_state,
	uint8_t mount_state,
	uint8_t locomotion_state,
	uint16_t presentation_kind_id,
	uint32_t tick);
void PublishRemoteMountedWitness(
	Game* game,
	const Server* server,
	const RemoteMountedWitnessPublishInput& input);

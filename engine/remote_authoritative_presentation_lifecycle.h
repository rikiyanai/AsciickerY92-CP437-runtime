#pragma once

#include <stdint.h>

#include "actor_presentation_result.h"
#include "remote_mounted_witness.h"

struct Game;
struct Human;
struct RemoteActorPresentationTrack;
struct Renderer;
struct Server;
struct World;

enum REMOTE_ACTOR_PRESENTATION_RECOVERY_REASON
{
	REMOTE_ACTOR_PRESENTATION_RECOVERY_NONE = 0,
	REMOTE_ACTOR_PRESENTATION_RECOVERY_HIDDEN = 2,
	REMOTE_ACTOR_PRESENTATION_RECOVERY_MISSING = 3,
};

struct RemoteAuthoritativePresentationLifecycleResult
{
	bool processed;
	int tracked_pid;
	uint32_t render_tick;
	ActorPresentationResult resolved;
	RemoteActorPresentationDebugSurface surface;
};

bool RemoteAuthoritativePresentationIsServerLocalSlot(const Server* server, const Human* remote);
void RequestRemoteActorPresentationInstInvalidation(
	RemoteActorPresentationTrack* track,
	int reason,
	bool clear_aliases);
void QueueRemoteActorPresentationInstInvalidation(Human* remote, World* world);
int DebugRemotePresentationCenterGlyph(const Human* remote);

int RemoteAuthoritativePresentationPurgeDuplicateInsts(Server* server);

void RemoteAuthoritativePresentationDeleteInst(
	Game* game,
	Server* server,
	Human* remote,
	int tracked_pid,
	int reason,
	bool clear_aliases);

bool RemoteAuthoritativePresentationRecreateInst(
	Game* game,
	Server* server,
	World* world,
	Human* remote,
	bool snap_to_target,
	int reason,
	int trigger_on_screen = 0,
	int trigger_label_visible = 0,
	int trigger_body_visible = 0,
	int trigger_label_only = 0,
	const ActorPresentationResult* resolved_this_frame = 0);

RemoteAuthoritativePresentationLifecycleResult RunRemoteAuthoritativePresentationLifecycle(
	Game* game,
	Server* server,
	World* world,
	Renderer* renderer,
	int width,
	int height,
	Human* remote,
	uint64_t render_stamp_us);

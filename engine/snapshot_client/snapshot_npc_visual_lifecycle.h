#pragma once

#include <stdint.h>

#include "actor_presentation_result.h"
#include "server/snapshot_npc_repository.h"

struct Renderer;
struct SnapshotClientState;
struct World;

struct SnapshotNpcVisualLifecycleProbe
{
	const ServerSnapshotNpcRepository::SnapshotNpcState* snapshot;
	const ServerSnapshotNpcRepository::SnapshotNpcVisual* visual;
	ActorPresentationResult resolved;
	float pos[3];
	int on_screen;
	uint32_t resolve_us;
	uint32_t compose_us;
	bool resolved_valid;
};

void DestroySnapshotNpcVisuals(ServerSnapshotNpcRepository* repo);

bool UpdateSnapshotNpcVisualLifecycleSlot(
	const SnapshotClientState* snapshot_client,
	ServerSnapshotNpcRepository* npc_repo,
	World* world,
	Renderer* renderer,
	uint64_t render_stamp_us,
	int viewport_width,
	int viewport_height,
	int slot_index,
	SnapshotNpcVisualLifecycleProbe* out);

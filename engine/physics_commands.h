// physics_commands.h — The only public command seam for physics state mutation.
//
// Gameplay code must not call low-level setters directly. All teleport/yaw/dir
// commands route through PhysicsTeleport() so that LocalPlayerAuthority remains
// the single gameplay adapter.

#pragma once

#include "physics.h"

struct PhysicsTeleportCommand
{
	bool set_pos = false;
	bool set_yaw = false;
	bool set_dir = false;
	float pos[3] = { 0.0f, 0.0f, 0.0f };
	float vel[3] = { 0.0f, 0.0f, 0.0f };
	float yaw = 0.0f;
	float yaw_vel = 0.0f;
	float dir = 0.0f;
};

void PhysicsTeleport(
	Physics* actor_id,
	const PhysicsTeleportCommand& command);

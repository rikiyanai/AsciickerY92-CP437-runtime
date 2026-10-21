#pragma once

#include "physics.h"
#include "mp_move.h"

struct PhysicsWorld
{
	Physics* actor = 0;
	MpMoveState* multiplayer_state = 0;
};

struct PhysicsStepInput
{
	PhysicsIO* io = 0;
	uint64_t stamp = 0;
	const LocalPhysicsActorProfile* actor_profile = 0;
	bool use_multiplayer_path = false;
	bool has_authoritative_server = false;
	bool allow_local_animate = false;
	bool me = false;
};

struct PhysicsStepResult
{
	int steps = 0;
	bool stepped = false;
	bool used_multiplayer_path = false;
};

static inline PhysicsStepResult PhysicsStep(
	PhysicsWorld& world,
	const PhysicsStepInput& input)
{
	PhysicsStepResult result = {};
	if (!world.actor || !input.io)
		return result;

	if (input.use_multiplayer_path && world.multiplayer_state)
	{
		if (!world.multiplayer_state->active)
			MpMoveActivate(world.multiplayer_state, world.actor, input.stamp);
		MpMoveTickResult tick_result =
			MpMoveTick(world.multiplayer_state,
				world.actor,
				input.io,
				input.stamp,
				input.has_authoritative_server);
		result.steps = tick_result.steps;
		result.stepped = tick_result.steps > 0;
		result.used_multiplayer_path = true;
		return result;
	}

	if (input.allow_local_animate)
	{
		result.steps = Animate(
			world.actor,
			input.stamp,
			input.io,
			input.actor_profile,
			input.me);
		result.stepped = result.steps > 0;
	}
	return result;
}

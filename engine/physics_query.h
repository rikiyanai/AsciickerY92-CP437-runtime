#pragma once

#include "physics.h"

struct PhysicsPose
{
	bool valid = false;
	float pos[3] = { 0.0f, 0.0f, 0.0f };
	float vel[3] = { 0.0f, 0.0f, 0.0f };
	float yaw = 0.0f;
	float dir = 0.0f;
	bool grounded = false;
};

// Public read seam — render and debug may call this. Do not touch raw getters.
PhysicsPose PhysicsReadPose(const Physics* actor_id);

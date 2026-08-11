#include "physics_query.h"
#include "physics_state.h"

PhysicsPose PhysicsReadPose(const Physics* actor_id)
{
	PhysicsPose pose = {};
	if (!actor_id)
		return pose;

	Physics* actor = const_cast<Physics*>(actor_id);
	pose.valid = true;
	GetPhysicsPos(actor, pose.pos);
	GetPhysicsVel(actor, pose.vel);
	pose.grounded = GetPhysicsGrounded(actor);

	PhysicsFullState state = {};
	SavePhysicsState(actor, &state);
	pose.yaw = state.yaw;
	pose.dir = state.player_dir;
	return pose;
}

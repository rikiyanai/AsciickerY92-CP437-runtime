#include "physics_commands.h"
#include "physics_state.h"

void PhysicsTeleport(
	Physics* actor_id,
	const PhysicsTeleportCommand& command)
{
	if (!actor_id)
		return;
	if (command.set_pos)
	{
		float pos[3] = { command.pos[0], command.pos[1], command.pos[2] };
		float vel[3] = { command.vel[0], command.vel[1], command.vel[2] };
		SetPhysicsPos(actor_id, pos, vel);
	}
	if (command.set_yaw)
		SetPhysicsYaw(actor_id, command.yaw, command.yaw_vel);
	if (command.set_dir)
		SetPhysicsDir(actor_id, command.dir);
}

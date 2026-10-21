#pragma once

#include <stdint.h>

struct Physics;

struct PhysicsFullState
{
	uint64_t stamp;
	int mat;
	float water;
	float pos[3];
	float vel[3];
	float player_dir;
	int player_stp;
	float yaw;
	float yaw_vel;
	float slope;
	float accum_contact;
	// FL-4257 — persisted server-authoritative S3 pose (see MpStepState).
	double s3_ground[3];
	double s3_east[3];
	uint8_t s3_valid;
};

// Low-level state mutators — admin-only machinery used by PhysicsTeleport.
// Do not call directly from gameplay or render modules.
void SetPhysicsPos(Physics* phys, float pos[3], float vel[3]);
void SetPhysicsYaw(Physics* phys, float yaw, float vel);
void SetPhysicsDir(Physics* phys, float dir);

void SavePhysicsState(Physics* phys, PhysicsFullState* state);
void RestorePhysicsState(Physics* phys, const PhysicsFullState* state);

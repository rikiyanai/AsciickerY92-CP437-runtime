#pragma once

// facing_space.h — single front door for facing-space conversions
//
// OWNERSHIP:
// - gameplay/physics/server own world-facing truth via FacingMovementStep().
// - this helper owns the conversion from world-facing truth into
//   camera-relative signed degrees and sprite angle-row indices.
//
// MOVEMENT-FACING CONVENTION (legacy sprite-compensated):
// FacingWorldDirFromWorldVector() returns atan2(dy,dx)+90°, which maps:
//   N movement → 180°, E → 90°, S → 0°, W → -90°.
// This is NOT a clean world-direction convention — it is the pre-complaint
// contract the raw renderer consumed. South maps to 0° because character XP
// sheets are authored south-first (row 0 = front/south-facing), so S=0°
// feeds directly into FacingSpriteAngleIndex() → row 0 without any offset.
//
// RENDER CONVENTION:
// All sprites (characters AND items) use FacingSpriteAngleIndex() with the
// legacy compensated direction. FacingSouthFirstSpriteAngleIndex() exists as
// a utility but is NOT used at runtime — the compensation lives in the
// gameplay-facing formula, not in the render path.
//
// FL-3858: The May 10 patch swapped to atan2(dx,dy) + 180° render offset,
// which fixed N/S rows but swapped E/W. Restored to pre-complaint convention
// with idle-preserve kept in both SP and MP.

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static inline float FacingWrapDegrees(float deg)
{
	while (deg <= -180.0f)
		deg += 360.0f;
	while (deg > 180.0f)
		deg -= 360.0f;
	return deg;
}

static inline float FacingWorldDirFromWorldVector(float world_dx, float world_dy)
{
	// Legacy sprite-compensated convention: atan2(dy, dx) + 90°.
	// N(+Y)→180°, E(+X)→90°, S(-Y)→0°, W(-X)→-90°.
	// The +90° makes S=0° so the raw renderer maps south-facing to row 0
	// (front) without needing a separate 180° render offset.
	return FacingWrapDegrees((float)(atan2(world_dy, world_dx) * 180.0 / M_PI) + 90.0f);
}

static inline float FacingCameraSignedDegrees(float world_dir, float camera_yaw)
{
	return FacingWrapDegrees(world_dir - camera_yaw);
}

static inline int FacingSpriteAngleIndexFromSignedDegrees(float signed_deg, int sprite_angles)
{
	int ang = (int)floor((signed_deg * sprite_angles) / 360.0f + 0.5f);
	return ang >= 0 ? ang % sprite_angles : (ang % sprite_angles + sprite_angles) % sprite_angles;
}

static inline int FacingSpriteAngleIndex(float world_dir, float camera_yaw, int sprite_angles)
{
	return FacingSpriteAngleIndexFromSignedDegrees(
		FacingCameraSignedDegrees(world_dir, camera_yaw),
		sprite_angles);
}

// Utility: adds 180° for sheets where row 0 = south-facing. NOT used at
// runtime — the legacy sprite-compensated formula (atan2(dy,dx)+90) already
// maps S→0° so the raw FacingSpriteAngleIndex() gives the correct row.
// Kept for tooling/offline use only.
static inline int FacingSouthFirstSpriteAngleIndex(float world_dir, float camera_yaw, int sprite_angles)
{
	return FacingSpriteAngleIndex(FacingWrapDegrees(world_dir + 180.0f), camera_yaw, sprite_angles);
}

// ---------------------------------------------------------------------------
// Active movement-facing step
// ---------------------------------------------------------------------------
//
// Canonical implementation of the active-movement facing computation.
// Both engine/physics.cpp (SP owner) and server/mp_step.cpp (MP owner)
// MUST call this function — do not open-code the facing step.
//
// Uses the legacy sprite-compensated formula via FacingWorldDirFromWorldVector
// (atan2(dy,dx)+90°). The raw renderer consumes the result directly via
// FacingSpriteAngleIndex() — no south-first offset needed.
// Idle facing policy is NOT handled here — callers own the idle branch.
//
// Wrap helper: intentionally duplicates physics_tick.h::MpWrapYaw body
// to avoid coupling facing_space.h to physics_tick.h (which has unrelated
// dependencies). The boundary behavior (< -180, not <= -180) matches
// MpWrapYaw exactly for byte-preserving extraction.

static inline float FacingMovementWrapYaw(float yaw)
{
	while (yaw > 180.0f)
		yaw -= 360.0f;
	while (yaw < -180.0f)
		yaw += 360.0f;
	return yaw;
}

static inline float FacingMovementStep(float cur_dir, float move_dx_world, float move_dy_world)
{
	float cur_ang = FacingMovementWrapYaw(cur_dir);
	float new_ang = FacingMovementWrapYaw(FacingWorldDirFromWorldVector(move_dx_world, move_dy_world));
	float delta = FacingMovementWrapYaw(new_ang - cur_ang);
	if (fabsf(delta) >= 20.0f)
		return new_ang;
	return FacingMovementWrapYaw(cur_dir + delta * 0.35f);
}

#pragma once

#include <stdint.h>
#include "terrain.h"
#include "world.h"

struct LocalPhysicsActorProfile
{
	enum Kind
	{
		HUMAN = 0,
		WOLF,
		BEE
	};
	Kind kind;
};
static_assert(LocalPhysicsActorProfile::HUMAN == 0 &&
	LocalPhysicsActorProfile::WOLF == 1 &&
	LocalPhysicsActorProfile::BEE == 2,
	"LocalPhysicsActorProfile::Kind ordinal values are stable; physics.cpp and audio.cpp branch on them by value");

// PhysicsIO — Input/output structure for physics integration
//
// WHY PhysicsIO pattern: Decouples game logic (game.cpp) from physics internals (physics.cpp).
// Game doesn't access Physics* directly (opaque pointer). Instead, game fills PhysicsIO with
// input forces and reads back output position/state after MpStepOnce()/MpMoveTick() call.
// This allows physics implementation to change without modifying game.cpp.
//
// USAGE PATTERN (current — Animate() was deleted, see comment at line 130):
// 1. Game fills INPUT fields (x_force, y_force, jump, etc.) based on player input or AI
// 2. Game calls MpMoveTick() / MpStepOnce() — these update OUTPUT fields via MpStepApplyStateToIO
// 3. Game reads OUTPUT fields to update rendering (sprite position, animation frame)
//
// FIELD CATEGORIES:
// - INPUT:  Set by game.cpp before the step call. Physics reads these.
// - OUTPUT: Set exclusively by physics.cpp during the step call. CALLER MUST NOT WRITE THESE
//           after the step. Writing OUTPUT fields from game.cpp is a dual-write ownership
//           violation that can cause multiplayer pose desync.
// - IO:     Read and modified by both (e.g., jump flag consumed by physics, impulses accumulated)
struct PhysicsIO
{
    // ========== INPUT FIELDS (game.cpp → physics.cpp) ==========

    // WHY x_force, y_force: Horizontal movement forces from gamepad/keyboard input or AI.
    // Normalized to [-1, 1] range (game.cpp thresholds analog stick deadzone).
    // Physics integrates these into velocity with acceleration and friction.
    // Units: dimensionless force magnitude (scaled by physics constants internally).
    float x_force;  // Horizontal force X (right=positive, left=negative)
    float y_force;  // Horizontal force Y (forward=positive, backward=negative)

    // WHY z_force: Vertical force for fly mode only (ignored when grounded).
    // Used by flying mounts (bees) to ascend/descend. Ground-based characters use
    // gravity and jump instead.
    // Units: dimensionless force magnitude (scaled by physics constants internally).
    float z_force;  // Vertical force (up=positive, down=negative, fly mode only)

    // WHY torque: Angular force for yaw rotation (camera/character turning).
    // Integrated into yaw velocity with damping. If torque >= 1000000, treated as
    // absolute yaw value (snap to angle instead of applying rotational force).
    // Units: dimensionless force magnitude (scaled by physics constants internally).
    float torque;   // Yaw rotation force (or absolute yaw if >= 1000000)

    // WHY water: Water surface Z coordinate for buoyancy calculation.
    // Physics uses this to determine if character is submerged and apply upward
    // buoyant force (Archimedes principle). Set by game.cpp based on terrain water level.
    // Units: world Z coordinate (same as pos[2]).
    float water;    // Water surface Z (for buoyancy, set by game.cpp)

    // ========== IO FIELDS (both game.cpp ↔ physics.cpp) ==========

    // WHY jump flag: Game sets to true when jump input detected (spacebar, gamepad button).
    // Physics consumes flag (sets to false) when jump impulse is applied (grounded check).
    // This prevents jump from re-triggering every frame while button is held.
    bool jump;      // INPUT: Jump requested (set by game), OUTPUT: Consumed by physics if handled

    // WHY fly flag: Enables fly mode (disables gravity, enables z_force control).
    // Set by game.cpp for flying mounts (bees) or debug fly mode.
    bool fly;       // INPUT: Fly mode enabled (set by game)

	// WHY impulses: Accumulated forces from combat knockback, collisions, etc.
	// Game accumulates impulses (e.g., hit by enemy adds impulse), physics applies
	// and drains them over multiple frames (impulse *= 0.5 each frame until negligible).
	// This allows discrete events (hit) to create smooth motion (knockback arc).
	// Units: velocity delta (added directly to vel[0], vel[1]).
	float x_impulse; // IO: Accumulated horizontal impulse X (game adds, physics drains)
	float y_impulse; // IO: Accumulated horizontal impulse Y (game adds, physics drains)

    // ========== OUTPUT FIELDS (physics.cpp → game.cpp) ==========
    // OWNERSHIP: These fields are written by physics.cpp only, during MpStepOnce()/MpMoveTick().
    // Callers MUST NOT write these after the step call. See dual-write note in struct comment above.

    // WHY pos[3]: Updated world position after physics integration.
    // Game reads this to update sprite rendering position and camera following.
    // Units: world coordinates (XYZ).
    float pos[3];   // OUTPUT: World position (X, Y, Z) — physics-owned, do not write from caller

    // WHY yaw: Updated camera/character yaw angle after angular velocity integration.
    // Game reads this to update camera orientation (first-person) or sprite facing (third-person).
    // Units: degrees. NOTE: MpWrapYaw() wraps to [-180, +180], not 0-360 as previously documented.
    float yaw;      // OUTPUT: Camera/character yaw angle in degrees [-180, 180] — physics-owned

    // WHY player_dir: Character facing direction for animation frame selection.
    // Separate from yaw because character can face one direction while camera looks another.
    // Game uses this to select correct sprite frame (8-directional sprite sheets).
    // Units: degrees (0-360, quantized to 8 directions: 0, 45, 90, 135, 180, 225, 270, 315).
    float player_dir;  // OUTPUT: Character facing direction for animation — physics-owned

    // WHY player_stp: Animation step counter incremented by velocity magnitude.
    // Game uses this to advance walk/run animation frames. Higher velocity = faster animation.
    // Value -1 means idle (no movement), >=0 means walking (frame = player_stp / 1024).
    // Units: dimensionless counter (divide by 1024 to get animation frame index).
    int player_stp;    // OUTPUT: Animation step counter (-1=idle, >=0=walking) — physics-owned

	// WHY dt: Actual physics timestep in microseconds (for debugging/telemetry).
	// Game can use this to detect physics stalls (dt > expected) or show physics timing.
	// Normal value: ~15000 (15ms per physics step at 66 Hz).
	// Units: microseconds.
	int dt;            // OUTPUT: Physics timestep duration in microseconds — physics-owned

	// WHY grounded: Boolean flag indicating if character has ground contact.
	// Game uses this to:
	// - Select animation (idle/walk vs. jump/fall)
	// - Enable jump input (can only jump when grounded)
	// - Play footstep sounds (only when grounded)
	// - Apply friction (grounded characters have friction, airborne don't)
	bool grounded;     // OUTPUT: True if character has ground contact (can jump) — physics-owned
};

struct Physics;

// Animate() is restored for single-player/editor only (server == 0).
// Multiplayer clients stay on the server-authoritative MpMoveTick/MpStepOnce path.
// actor_profile may be null for legacy single-player/editor callers; null is
// interpreted as HUMAN inside Animate().
int Animate(Physics* phys, uint64_t stamp, PhysicsIO* io, const LocalPhysicsActorProfile* actor_profile, bool me);

enum PhysicsCreateFlags
{
    PHYSICS_CREATE_EXACT_POS = 0,
    PHYSICS_CREATE_TERRAIN_SAFE_LIFT = 1 << 0,
};

Physics* CreatePhysics(Terrain* t, World* w, float pos[3], float dir, float yaw, uint64_t stamp, uint32_t create_flags);
void DeletePhysics(Physics* phys);

// NOTE: SetPhysicsPos/SetPhysicsYaw/SetPhysicsDir are no longer declared here.
// They are internal to physics.cpp and only callable through PhysicsTeleport
// (see physics_commands.h). Any caller that needs direct physics state mutation
// must go through the PhysicsTeleport seam instead.
void SyncPhysicsStamp(Physics* phys, uint64_t stamp);
void GetPhysicsVel(Physics* phys, float vel[3]);
void GetPhysicsPos(Physics* phys, float out[3]);
void GetPhysicsSlope(Physics* phys, float* out);
void GetPhysicsAccumContact(Physics* phys, float* out);
bool GetPhysicsGrounded(Physics* phys);
int GetPhysicsDebugZeroed(Physics* phys);
int GetPhysicsDebugZeroMask(Physics* phys);
float GetPhysicsDebugContactNormalZ(Physics* phys);
int GetPhysicsDebugAutoJump(Physics* phys);
int GetPhysicsDebugIx(Physics* phys);
int GetPhysicsDebugIy(Physics* phys);
float GetPhysicsDebugInputLen(Physics* phys);
void GetPhysicsDebugMoveWorld(Physics* phys, float move[2]);
void GetPhysicsDebugPreVel(Physics* phys, float vel[3]);
void GetPhysicsDebugPostVel(Physics* phys, float vel[3]);

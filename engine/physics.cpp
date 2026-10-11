// =============================================================================
// Physics System — Sphere-Based Collision and Movement Integration
// =============================================================================
//
// PURPOSE:
// Implements continuous collision detection and dynamic movement integration for
// sphere-based characters in a triangle soup world. All characters (players, NPCs,
// mounts) are modeled as 1.0 unit radius spheres that collide with terrain
// heightfield and world mesh geometry.
//
// COLLISION MODEL:
// - Sphere: 1.0 unit radius, centered at character position + 0.5*height offset
// - Geometry Sources:
//   * Terrain heightfield (quadtree patches, 2 triangles per height cell)
//   * World meshes (BSP tree instances, triangle soup)
// - Detection Method: Time-of-impact (TOI) sweep — finds earliest collision along velocity vector
// - Response: Velocity reflection along contact normal with friction/restitution
//
// PHYSICS INTEGRATION PIPELINE (Animate function):
//
//   Game Input (PhysicsIO)
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Force Accumulation                   │
//   │ - Gravity (constant -9.8 in Z, modulated by water)  │
//   │ - Input forces (x_force, y_force from gamepad/keys) │
//   │ - Water buoyancy (Archimedes principle)             │
//   │ - Impulses (combat knockback, collision response)   │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Velocity Integration (Euler)         │
//   │ vel += forces * dt                                   │
//   │ Apply friction (ground contact only)                │
//   │ Apply water resistance (depth-based)                │
//   │ Clamp to max velocity (27 units/sec air, 10 water)  │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Geometry Query                       │
//   │ QueryWorld() → collect triangle soup from BSP tree  │
//   │ QueryTerrain() → collect triangles from quadtree    │
//   │ Transform to sphere space (scale by radius)         │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Collision Sweep (substep iteration)  │
//   │ for each triangle in soup:                          │
//   │   toi = CheckCollision(sphere_pos, sphere_vel, ...)│
//   │   if toi < earliest_toi:                            │
//   │     earliest_toi = toi                              │
//   │     collision_normal = contact - sphere_center      │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Position Update                      │
//   │ sphere_pos += sphere_vel * toi                      │
//   │ (move to exact collision point)                     │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Contact Response                     │
//   │ vel -= normal * dot(vel, normal)  (reflect)         │
//   │ vel *= (1 - collision_time)        (consume time)   │
//   │ Accumulate contact normal Z for grounded detection  │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Grounded Detection                   │
//   │ accum_contact_z >= 1.0 → grounded = true            │
//   │ (requires upward normal from floor collision)       │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   ┌─────────────────────────────────────────────────────┐
//   │ [FLOW:PHYSICS] Substep Iteration                    │
//   │ Repeat sweep until:                                  │
//   │ - Velocity below threshold (stopped)                │
//   │ - Max iterations reached (10)                       │
//   │ - No more collisions found                          │
//   └─────────────────────────────────────────────────────┘
//         |
//         v
//   Updated State (PhysicsIO output)
//
// COLLISION ALGORITHM (CheckCollision method):
// Sphere-triangle intersection reduces to 3 sequential tests:
//
// 1. FACE COLLISION (plane intersection + barycentric containment):
//    - Calculate TOI when sphere surface hits triangle plane
//    - Project contact point onto plane along velocity vector
//    - Test if contact is inside triangle using barycentric coordinates
//    - If inside: return TOI (valid face collision)
//
// 2. EDGE COLLISION (sphere-vs-line-segment, only if face test fails):
//    - For each of 3 triangle edges:
//      * Find closest point on edge to sphere center
//      * Calculate TOI when sphere hits that point (moving capsule test)
//    - Return earliest edge TOI if any edge was hit
//
// 3. VERTEX COLLISION (sphere-vs-sphere, only if face and edge fail):
//    - For each of 3 triangle vertices:
//      * Calculate TOI when sphere hits vertex (moving sphere-sphere test)
//    - Return earliest vertex TOI if any vertex was hit
//
// WHY 3 tests: Face test handles flat triangle interior. Edge test handles
// glancing blows along triangle edges (barycentric fails here). Vertex test
// handles corner cases where sphere clips triangle vertex.
//
// COORDINATE SYSTEMS:
// - World space: global XYZ, +Z is up (gravity acts in -Z direction)
// - Sphere space: scaled by 1.0/radius for collision math (normalized to unit sphere)
// - Velocity units: world units per second (typical: 0-27 for running, 10 in water)
// - TOI range: [0, 1] representing fraction of timestep until collision
//   * TOI = 0: already colliding (penetration)
//   * TOI = 0.5: collision at halfway point
//   * TOI >= 2: no collision (return value convention)
//
// PHYSICS CONSTANTS:
// - Gravity: ~9.8 world units/sec² downward (modulated by water buoyancy)
// - Friction: velocity damping 0.9^dt per substep (grounded only)
// - Water resistance: 0.5*depth_factor damping for XY, 0.1*depth_factor for Z
// - Restitution: implicit ~0.3 (velocity reflection without energy boost)
// - Timestep: 15ms fixed physics step (~66 Hz), multiple substeps per frame
// - Max velocity: 27 units/sec in air, 10 units/sec in water
//
// KEY DATA STRUCTURES:
// - PhysicsIO: Input/output structure for Animate() (see physics.h for field docs)
//   * INPUT:  x_force, y_force, z_force, torque, jump, fly, water level
//   * OUTPUT: pos[3], yaw, player_dir, player_stp, grounded, dt
//   * IO:     x_impulse, y_impulse (accumulated until handled)
//
// - Physics: Internal state (opaque to game.cpp, managed by this file)
//   * Position, velocity, yaw, yaw_vel
//   * Terrain* and World* pointers for geometry queries
//   * SoupItem* array (dynamic triangle soup buffer)
//   * Collision state (accum_contact, material votes, max_height)
//
// - SoupItem: Precomputed triangle with plane equation for fast collision tests
//   * tri[3][3]: triangle vertices in sphere space
//   * nrm[4]: plane equation (nx, ny, nz, d) where dot(N, X) + d = 0
//   * material: surface material ID for audio (rock, wood, grass, etc.)
//
// KEY FUNCTIONS:
// - Animate():           Main physics update — integrate forces, sweep collisions, update position
//                        Called once per frame from game.cpp for each character (player, NPCs)
//                        Returns number of physics substeps executed (for animation frame sync)
//
// - CheckCollision():    Sphere-triangle collision test returning TOI and contact point
//                        Used by Animate() during collision sweep
//                        Returns TOI in [0, 1] or >=2 if no collision
//
// - CreatePhysics():     Allocate Physics state, bind to terrain/world, initialize position
//                        Called once per character at spawn time
//
// - DeletePhysics():     Free Physics state and soup buffer
//                        Called when character is removed from world
//
// - SetPhysicsPos():     Teleport character (bypasses collision, for spawn/respawn/editor)
//                        Sets position and/or velocity directly without physics integration
//
// - SetPhysicsYaw():     Set yaw and angular velocity directly
//
// - SetPhysicsDir():     Set player facing direction (for animation)
//
// KEY FILES:
// - physics.h         — Public API and PhysicsIO structure (input/output contract)
// - game.cpp          — Calls Animate() each frame for player and NPCs (lines ~5667, 5940, 6015)
// - terrain.h/cpp     — Heightfield geometry queried during collision sweep (PatchCollect callback)
// - world.h/cpp       — BSP tree mesh geometry queried during collision sweep (MeshCollect callback)
// - audio.cpp         — AudioWalk() called on footstep events (material-based sound)
// - matrix.h          — Matrix transformations for instance geometry (Product, DotProduct, CrossProduct)
//
// MATERIAL DETECTION (for footstep audio):
// Triangle color is analyzed during soup collection to determine material:
// - Rock (0):      low saturation (grayscale)
// - Wood (1):      red or blue dominant
// - Dirt (2):      explicit terrain material
// - Grass (3):     green dominant + low elevation
// - Hi-Grass (4):  green dominant + high elevation
// - Blood (5):     explicit terrain material
// - Water (6):     character legs submerged (overrides triangle material)
//
// NUMERICAL STABILITY NOTES:
// - TOI division by zero: When velocity parallel to plane (closer ≈ 0), no collision occurs
// - Barycentric epsilon: Floating-point tolerance (1e-6) for boundary cases
// - Embedded sphere: When sphere penetrates plane (face < 0), resolve by moving to surface
// - Iteration limit: Max 10 substeps prevents infinite loops in degenerate geometry
//
// =============================================================================

#define _USE_MATH_DEFINES
#include <math.h>
#include <stdlib.h>
#include <assert.h>
#include <climits>
#include "facing_space.h"
#include "matrix.h"
#include "physics.h"
#include "physics_state.h"
#include "physics_tick.h"
#include "audio.h"

static bool PhysicsDebugEnabled()
{
	static int cached = -1;
	if (cached < 0)
	{
		const char* env = getenv("ASCIICKER_PHYS_DEBUG");
		cached = (env && *env) ? 1 : 0;
	}
	return cached == 1;
}


struct SoupItem
{
	float tri[3][3];
	int material; // for audio :)
	float nrm[4]; // {nrm, w} is plane equ


	// WHY CheckCollision: This function performs sphere-triangle collision detection
	// for continuous collision (sweeps sphere along velocity vector to find TOI).
	// It handles 3 cases: face collision (inside triangle), edge collision (hits edge),
	// vertex collision (hits corner). Returns TOI in [0, 1] or >=2 if no collision.
	float CheckCollision(const float sphere_pos[3], const float sphere_vel[3], float contact_pos[3])
	{
		// [FLOW:PHYSICS] Collision sweep — TOI (Time Of Impact) calculation
		//
		// WHY TOI calculation: We solve for time t when sphere surface (radius=1.0)
		// intersects the triangle's plane. This allows continuous collision detection
		// preventing tunneling through thin geometry.
		//
		// DERIVATION:
		// Sphere center trajectory: P(t) = sphere_pos + t * sphere_vel
		// Sphere surface point along normal: P_surface(t) = P(t) - nrm (radius=1.0)
		// Plane equation: dot(nrm, X) + nrm[3] = 0
		//
		// Substitute sphere surface into plane equation:
		//   dot(nrm, sphere_pos + t*sphere_vel - nrm) + nrm[3] = 0
		// Expand:
		//   dot(nrm, sphere_pos) + t*dot(nrm, sphere_vel) - dot(nrm, nrm) + nrm[3] = 0
		// Since dot(nrm, nrm) = 1 (normalized):
		//   dot(nrm, sphere_pos) + t*dot(nrm, sphere_vel) - 1 + nrm[3] = 0
		// Solve for t:
		//   t = (1 - nrm[3] - dot(nrm, sphere_pos)) / dot(nrm, sphere_vel)
		//
		// Simplify with variables:
		//   col = sphere_pos - nrm        (point on sphere surface at t=0)
		//   dist = dot(nrm, col) + nrm[3] (signed distance from sphere surface to plane)
		//   vel_dot_nrm = -dot(nrm, sphere_vel) (approach rate, negated for sign convention)
		// Then: t = dist / vel_dot_nrm
		//
		// WHY dist sign matters:
		//   dist > 0:  sphere in front of plane (approaching from outside)
		//   dist <= 0: sphere behind plane (penetrating or already passed)
		//
		// WHY vel_dot_nrm sign matters:
		//   vel_dot_nrm > 0:  velocity toward plane (will collide)
		//   vel_dot_nrm <= 0: velocity away from plane or parallel (no collision)
		//
		// NUMERICAL STABILITY:
		// If vel_dot_nrm ≈ 0 (velocity parallel to plane), no collision occurs.
		// We early-exit to avoid division by zero.

		const float raw_vel_dot_nrm = -DotProduct(sphere_vel, nrm); // Approach rate (negated for convention)
		const float side = raw_vel_dot_nrm >= 0.0f ? 1.0f : -1.0f;
		const float solid_nrm[4] =
		{
			nrm[0] * side,
			nrm[1] * side,
			nrm[2] * side,
			nrm[3] * side,
		};
		float col[3] = // Point on sphere surface closest to plane at time t=0
		{
			sphere_pos[0] - solid_nrm[0],
			sphere_pos[1] - solid_nrm[1],
			sphere_pos[2] - solid_nrm[2]
		};
		float vel_dot_nrm = fabsf(raw_vel_dot_nrm);
		float plane_t = 2; // Default: no collision (return value >1 means no hit)

		if (vel_dot_nrm > 0) // else: velocity parallel to plane
		{
			float dist = DotProduct(col, solid_nrm) + solid_nrm[3]; // Signed distance from sphere surface to plane

			if (dist > 0)
			{
				// WHY division: Calculate exact TOI when sphere surface touches plane
				// Equation: t = dist / vel_dot_nrm
				// Example: dist=2.0, vel_dot_nrm=4.0 → t=0.5 (collision at halfway point)
				plane_t = dist / vel_dot_nrm;
			}
			else
			if (dist > -1)
			{
				// WHY embedded case: Sphere surface has penetrated plane (dist <= 0)
				// but not too deeply (dist > -1, within sphere radius).
				// This can happen due to:
				// - Large timestep (sphere moved too far in one frame)
				// - Teleportation (SetPhysicsPos)
				// - Numerical precision (accumulated error)
				//
				// RESOLUTION: Project contact point back to plane surface along normal,
				// then treat as immediate collision (plane_t = 0).
				dist = 1.0f + dist; // Penetration depth (1.0 + negative value)
				contact_pos[0] = col[0] - dist * solid_nrm[0];
				contact_pos[1] = col[1] - dist * solid_nrm[1];
				contact_pos[2] = col[2] - dist * solid_nrm[2];
				plane_t = 0; // Collision happened in the past (resolve immediately)
			}
			else
				return 2; // Sphere deeply embedded (dist <= -1) — ignore to prevent explosion

			// [FLOW:PHYSICS] Contact point calculation — project along velocity to collision time
			// WHY project along velocity: At TOI t, sphere center is at (sphere_pos + t*sphere_vel).
			// Contact point (on sphere surface) is sphere center minus normal.
			contact_pos[0] = col[0] + plane_t * sphere_vel[0];
			contact_pos[1] = col[1] + plane_t * sphere_vel[1];
			contact_pos[2] = col[2] + plane_t * sphere_vel[2];

			// [FLOW:PHYSICS] Barycentric containment test — check if contact inside triangle
			//
			// WHY barycentric coordinates: To determine if contact point C is inside triangle
			// (V0, V1, V2), we express C as a weighted sum of vertices:
			//   C = u*V0 + v*V1 + w*V2, where u+v+w=1
			// If u>=0, v>=0, w>=0, then C is inside triangle (or on boundary).
			//
			// GEOMETRIC INTERPRETATION:
			// u, v, w are ratios of sub-triangle areas:
			//   u = area(C, V1, V2) / area(V0, V1, V2)  (opposite vertex V0)
			//   v = area(V0, C, V2) / area(V0, V1, V2)  (opposite vertex V1)
			//   w = area(V0, V1, C) / area(V0, V1, V2)  (opposite vertex V2)
			//
			// COMPUTATION using cross products:
			// Area of triangle (A, B, C) = 0.5 * |cross(B-A, C-A)|
			// Since we only need ratios, we can skip the 0.5 and magnitude:
			//   u ∝ dot(nrm, cross(edge1, vect1))
			//   v ∝ dot(nrm, cross(edge2, vect2))
			//   w ∝ dot(nrm, cross(edge0, vect0))
			// where edge[i] = V[(i+1)%3] - V[i], vect[i] = contact_pos - V[i]
			//
			// WHY dot with nrm: Cross product gives area vector perpendicular to triangle.
			// Dotting with triangle normal gives signed area (positive if same orientation).
			// If all three are positive, point is inside.
			//
			// FLOATING-POINT TOLERANCE:
			// We use exact ">= 0" test (no epsilon) because barycentric coordinates are
			// area ratios that should be exact for points inside the triangle. Edge cases
			// (contact on edge or vertex) are handled by separate edge/vertex collision tests
			// if this barycentric test fails.

			// Triangle edges (V1-V0, V2-V1, V0-V2)
			float edge[3][3] =
			{
				{tri[1][0] - tri[0][0], tri[1][1] - tri[0][1], tri[1][2] - tri[0][2]},
				{tri[2][0] - tri[1][0], tri[2][1] - tri[1][1], tri[2][2] - tri[1][2]},
				{tri[0][0] - tri[2][0], tri[0][1] - tri[2][1], tri[0][2] - tri[2][2]}
			};

			// Vectors from each vertex to contact point
			float vect[3][3] =
			{
				{ contact_pos[0] - tri[0][0], contact_pos[1] - tri[0][1], contact_pos[2] - tri[0][2]},
				{ contact_pos[0] - tri[1][0], contact_pos[1] - tri[1][1], contact_pos[2] - tri[1][2]},
				{ contact_pos[0] - tri[2][0], contact_pos[1] - tri[2][1], contact_pos[2] - tri[2][2]},
			};

			float cross[3][3];
			float dot[3]; // Signed area ratios (barycentric coordinates)

			// WHY cross products: cross(edge[i], vect[i]) gives area vector of sub-triangle
			// formed by edge[i] and vector to contact point. Dotting with triangle normal
			// gives signed area (positive if contact is on the "inside" side of the edge).
			CrossProduct(edge[0], vect[0], cross[0]);
			dot[0] = DotProduct(cross[0], nrm) * side; // Barycentric coordinate w (opposite V2)

			CrossProduct(edge[1], vect[1], cross[1]);
			dot[1] = DotProduct(cross[1], nrm) * side; // Barycentric coordinate u (opposite V0)

			CrossProduct(edge[2], vect[2], cross[2]);
			dot[2] = DotProduct(cross[2], nrm) * side; // Barycentric coordinate v (opposite V1)

			// WHY all three non-negative: If contact is on the inside side of all three edges,
			// it must be inside the triangle (or on the boundary).
			if (dot[0] >= 0 && dot[1] >= 0 && dot[2] >= 0)
			{
				// Contact point is inside triangle face (valid face collision)
				// WHY range check: TOI must be in [0, 1] to be within current timestep.
				// Return 2 if out of range (no collision this frame).
				return plane_t > 1 ? 2 : plane_t;
			}
			else
			{
				// [FLOW:PHYSICS] Edge/vertex collision fallback — barycentric test failed
				//
				// WHY fallback needed: Contact point is outside triangle face (one or more
				// barycentric coordinates negative). This happens when sphere hits triangle
				// edge or vertex instead of face interior. We must test sphere-vs-line-segment
				// (edge) and sphere-vs-point (vertex) collisions.
				//
				// EDGE COLLISION:
				// Sphere moving along velocity vector hits infinite cylinder around edge.
				// We clamp to edge endpoints (finite line segment) and find earliest TOI.
				//
				// VERTEX COLLISION:
				// Sphere moving along velocity vector hits point (treated as stationary sphere).
				// Standard moving sphere-vs-sphere collision (quadratic equation).

				 plane_t = 2; // Reset to "no collision" (will be updated if edge/vertex hit found)

				// [FLOW:PHYSICS] Vertex collision test — sphere-vs-sphere (moving sphere, stationary point)
				//
				// WHY sphere-vs-sphere: Treat triangle vertex as a point (zero-radius sphere).
				// Sphere center trajectory: P(t) = sphere_pos + t * sphere_vel
				// Distance from trajectory to vertex V: |P(t) - V| = radius (1.0)
				//
				// DERIVATION:
				// |P(t) - V|² = r²
				// |sphere_pos + t*sphere_vel - V|² = 1.0
				// Let D = sphere_pos - V:
				// |D + t*sphere_vel|² = 1.0
				// D·D + 2*t*(D·sphere_vel) + t²*(sphere_vel·sphere_vel) = 1.0
				//
				// Quadratic form: A*t² + B*t + C = 0
				// A := sphere_vel·sphere_vel (velocity magnitude squared)
				// B := 2*dot(D, sphere_vel) = 2*dot(sphere_pos - V, sphere_vel)
				// C := D·D - 1.0 = |sphere_pos - V|² - 1.0
				//
				// Solution: t = (-B ± sqrt(B² - 4AC)) / (2A)
				// Pick smaller root (earlier collision): t = (-B - sqrt(D)) / (2A)
				//
				// WHY smaller root: Sphere approaches vertex, hits it, then departs.
				// We want first contact (approach), not second contact (departure).
				{
					float A = DotProduct(sphere_vel, sphere_vel);

					for (int s = 0; s < 3; s++)
					{
						float p_ps[3] =
						{
							sphere_pos[0] - tri[s][0],
							sphere_pos[1] - tri[s][1],
							sphere_pos[2] - tri[s][2]
						};

						float B = 2 * DotProduct(p_ps, sphere_vel);
						float C = DotProduct(p_ps, p_ps) - 1;

						float D = B * B - 4 * A*C;
						if (D >= 0)
						{
							// pick smaller root (A is positive, so take -sqrt)
							float t = (-B - sqrtf(D)) / (2 * A);

							if (t >= 0 && t <= 1)
							{
								if (t < plane_t)
								{
									plane_t = t;
									contact_pos[0] = tri[s][0];
									contact_pos[1] = tri[s][1];
									contact_pos[2] = tri[s][2];
								}
							}
						}
					}
				}

				// [FLOW:PHYSICS] Edge collision test — sphere-vs-infinite-cylinder (clamped to segment)
				//
				// WHY cylinder: Treating edge as infinite cylinder (radius=1.0) around edge line.
				// Sphere moving along velocity hits cylinder when distance from sphere center
				// to edge line equals sphere radius (1.0).
				//
				// DERIVATION (moving sphere vs. infinite cylinder):
				// Let edge line be: L(h) = pc + h*vc, where pc=edge start, vc=edge vector, h∈[0,1]
				// Sphere trajectory: S(t) = p + t*v, where p=sphere_pos, v=sphere_vel
				// Distance from S(t) to line: |S(t) - L(h)| for closest h
				//
				// Closest point on line to S(t):
				//   h(t) = dot(S(t) - pc, vc) / dot(vc, vc)
				//   L_closest(t) = pc + h(t)*vc
				// Distance squared: D²(t) = |S(t) - L_closest(t)|²
				//
				// Perpendicular component of (S(t) - pc) to edge vector vc:
				//   perp(t) = (S(t) - pc) - vc * dot(S(t) - pc, vc) / |vc|²
				//
				// Collision when |perp(t)| = r (radius=1.0):
				//   |perp(t)|² = r²
				//
				// ALGEBRAIC EXPANSION:
				// Let p_pc = p - pc (sphere center relative to edge start)
				// S(t) - pc = p_pc + t*v
				// vc² = dot(vc, vc)
				//
				// Perpendicular vector (rejecting vc component):
				//   perp(t) = (p_pc + t*v)*vc² - vc*dot(vc, p_pc + t*v)
				//           = p_pc*vc² - vc*dot(vc, p_pc) + t*(v*vc² - vc*dot(vc, v))
				//
				// Let U = p_pc*vc² - vc*dot(vc, p_pc)  (perpendicular at t=0)
				//     V = v*vc² - vc*dot(vc, v)        (perpendicular velocity)
				// Then: perp(t) = U + t*V
				//
				// Collision condition: |U + t*V|² = r²*vc⁴
				// (We multiply by vc² to avoid division in the derivation)
				//
				// Expand: (U + t*V)·(U + t*V) = r²*vc⁴
				//         U·U + 2*t*(U·V) + t²*(V·V) = r²*vc⁴
				//
				// Quadratic form: A*t² + B*t + C = 0
				// A = V·V         (perpendicular velocity magnitude squared)
				// B = 2*(U·V)     (twice the dot product)
				// C = U·U - r²*vc⁴  (perpendicular distance squared at t=0, scaled)
				//
				// Solution: t = (-B ± sqrt(B² - 4AC)) / (2A)
				// Pick smaller root (first contact): t = (-B - sqrt(D)) / (2A)
				//
				// CLAMPING TO SEGMENT:
				// After finding TOI, we check if h(t) ∈ [0, 1] (contact on finite edge).
				// If h < 0 or h > 1, contact is outside edge → handled by vertex collision test.
				{
					for (int c = 0; c < 3; c++)
					{
						float vcvc = DotProduct(edge[c], edge[c]);
						float p_pc[3] =
						{
							sphere_pos[0] - tri[c][0],
							sphere_pos[1] - tri[c][1],
							sphere_pos[2] - tri[c][2]
						};

						float vc_dot_p_pc = DotProduct(edge[c], p_pc);

						float U[3] =
						{
							p_pc[0] * vcvc - edge[c][0] * vc_dot_p_pc,
							p_pc[1] * vcvc - edge[c][1] * vc_dot_p_pc,
							p_pc[2] * vcvc - edge[c][2] * vc_dot_p_pc
						};

						float vc_dot_v = DotProduct(edge[c], sphere_vel);

						float V[3] =
						{
							sphere_vel[0] * vcvc - edge[c][0] * vc_dot_v,
							sphere_vel[1] * vcvc - edge[c][1] * vc_dot_v,
							sphere_vel[2] * vcvc - edge[c][2] * vc_dot_v
						};

						float A = DotProduct(V, V);
						float B = 2 * DotProduct(U, V);
						float C = DotProduct(U, U) - vcvc * vcvc;

						float D = B * B - 4 * A*C;
						if (D >= 0)
						{
							// pick smaller root (A is positive, so take -sqrt)
							float t = (-B - sqrtf(D)) / (2 * A);

							if (t >= 0 && t <= 1)
							{
								if (t < plane_t)
								{
									float _pc[3] =
									{
										sphere_pos[0] + t * sphere_vel[0] - tri[c][0],
										sphere_pos[1] + t * sphere_vel[1] - tri[c][1],
										sphere_pos[2] + t * sphere_vel[2] - tri[c][2]
									};

									float h_mul_vc = DotProduct(_pc, edge[c]);
									if (h_mul_vc >= 0 && h_mul_vc <= vcvc)
									{
										plane_t = t;
										float h_div_vc = h_mul_vc / vcvc;
										contact_pos[0] = tri[c][0] + edge[c][0] * h_div_vc;
										contact_pos[1] = tri[c][1] + edge[c][1] * h_div_vc;
										contact_pos[2] = tri[c][2] + edge[c][2] * h_div_vc;
									}
								}
							}
						}
					}
				}
			}
		}
		return plane_t;
	}
};

struct Physics
{
    uint64_t stamp;

	SoupItem* soup;
	int soup_alloc;
	int soup_items;

	double* collect_tm;
	float collect_mul_xy;
	float collect_mul_z;

	int mat;

	float max_height;

    //bool collision_failure;

    float slope;
    float water;
    float player_dir;
    int player_stp;
    float yaw_vel;
    float yaw;
    float vel[3];
    float pos[3];

	float accum_contact;
	int dbg_last_zeroed_after_sweep;
	int dbg_last_zero_reason_mask;
	float dbg_last_contact_normal_z;
	int dbg_last_auto_jump;
	int dbg_last_ix;
	int dbg_last_iy;
	float dbg_last_xy_len;
	float dbg_last_move_dx_world;
	float dbg_last_move_dy_world;
	float dbg_last_pre_sweep_vel[3];
	float dbg_last_post_sweep_vel[3];

    Terrain* terrain;
    World* world;

	static void FaceCollect(float coords[9], uint8_t* colors, uint32_t visual, void* cookie)
	{
		if (visual&(1 << 31)) // skip lines (but could be worked out to collide lines too!)
			return;
		if (PhysicsDebugEnabled())
		{
			printf("FACE_COLLECT_ENTRY\n");
			fflush(stdout);
		}
		if (colors[3] > 128 || colors[7] > 128 || colors[11] > 128) // skip leafs
			return;

		Physics* phys = (Physics*)cookie;
		SoupItem* item = phys->soup + phys->soup_items;

		// check face color
		// greenish, greish, yellowish, redish

		int rgb[3]= // 0..765
		{
			colors[0]+colors[4]+colors[8],
			colors[1]+colors[5]+colors[9],
			colors[2]+colors[6]+colors[10]
		};

		int sat=0,lum=0,mat=3;
		if (rgb[1]>=rgb[2] && rgb[1]>=rgb[0])
		{
			// yellow - cyan
			lum = rgb[1];
			mat=3; // green
			if (rgb[0]>rgb[2])
			{
				// yellow-green
				sat = rgb[0]-rgb[2];
			}
			else
			{
				// green-cyan
				sat = rgb[2]-rgb[0];
			}
		}
		else
		if (rgb[0]>=rgb[1] && rgb[0]>=rgb[2])
		{
			// magenta-yellow
			lum = rgb[0];
			mat=1; // wood

			if (rgb[1]>rgb[2])
			{
				// red-yellow
				sat = rgb[1]-rgb[2];
			}
			else
			{
				// magenta-red
				sat = rgb[2]-rgb[1];
			}
		}
		else
		//if (rgb[2]>=rgb[0] && rgb[2]>=rgb[1])
		{
			// cyan-magenta
			lum = rgb[2];
			mat=1; // wood

			if (rgb[0]>rgb[1])
			{
				// blue-magenta
				sat = rgb[0]-rgb[1];
			}
			else
			{
				// cyan-blue
				sat = rgb[1]-rgb[0];
			}
		}

		if (sat*10<lum)
		{
			mat = 0; // rock
		}

		item->material = mat;

		// multiply coords by collect_tm
		// then multiply x & y by collect_mul_xy and z by collect_mul_z
		// ...

		float v[3][4]=
		{
			{coords[0],coords[1],coords[2],1},
			{coords[3],coords[4],coords[5],1},
			{coords[6],coords[7],coords[8],1},
		};

		float tmv[4];

		Product(phys->collect_tm, v[0], tmv);
		phys->max_height = fmaxf(tmv[2], phys->max_height);

		item->tri[0][0] = tmv[0] * phys->collect_mul_xy;
		item->tri[0][1] = tmv[1] * phys->collect_mul_xy;
		item->tri[0][2] = tmv[2] * phys->collect_mul_z;

		Product(phys->collect_tm, v[1], tmv);
		phys->max_height = fmaxf(tmv[2], phys->max_height);

		item->tri[1][0] = tmv[0] * phys->collect_mul_xy;
		item->tri[1][1] = tmv[1] * phys->collect_mul_xy;
		item->tri[1][2] = tmv[2] * phys->collect_mul_z;

		Product(phys->collect_tm, v[2], tmv);
		phys->max_height = fmaxf(tmv[2], phys->max_height);

		item->tri[2][0] = tmv[0] * phys->collect_mul_xy;
		item->tri[2][1] = tmv[1] * phys->collect_mul_xy;
		item->tri[2][2] = tmv[2] * phys->collect_mul_z;



		{
			float* v[3] = { item->tri[0], item->tri[1], item->tri[2] };
			float e1[3] = { v[0][0] - v[2][0],v[0][1] - v[2][1],v[0][2] - v[2][2] };
			float e2[3] = { v[1][0] - v[2][0],v[1][1] - v[2][1],v[1][2] - v[2][2] };
			CrossProduct(e1, e2, item->nrm);
			float nrm = 1.0f / sqrtf(
				item->nrm[0] * item->nrm[0] +
				item->nrm[1] * item->nrm[1] +
				item->nrm[2] * item->nrm[2]);
			item->nrm[0] *= nrm;
			item->nrm[1] *= nrm;
			item->nrm[2] *= nrm;
			item->nrm[3] = -(v[2][0] * item->nrm[0] + v[2][1] * item->nrm[1] + v[2][2] * item->nrm[2]);
		}

		phys->soup_items ++;
	}

	static void SpriteCollect(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie)
	{
		// no collisions with sprites at the moment
	}

	static void MeshCollect(Inst* i, Mesh* m, double tm[16], void* cookie)
	{
		Physics* phys = (Physics*)cookie;
		if (PhysicsDebugEnabled())
		{
			printf("MESH_COLLECT items=%d\n", phys->soup_items);
			fflush(stdout);
		}

		int faces = GetMeshFaces(m);
		if (phys->soup_alloc < phys->soup_items + faces)
		{
			phys->soup_alloc = 1414 * phys->soup_alloc / 1000 + faces;
			phys->soup = (SoupItem*)realloc(phys->soup, sizeof(SoupItem) * phys->soup_alloc);
		}

		phys->collect_tm = tm;

		QueryMesh(m, FaceCollect, cookie);
	}

	static void PatchCollect(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		Physics* phys = (Physics*)cookie;

		int faces = 2 * HEIGHT_CELLS*HEIGHT_CELLS;

		if (phys->soup_alloc < phys->soup_items + faces)
		{
			phys->soup_alloc = 1414 * phys->soup_alloc / 1000 + faces;
			phys->soup = (SoupItem*)realloc(phys->soup, sizeof(SoupItem) * phys->soup_alloc);
		}

		SoupItem* item = phys->soup + phys->soup_items;
		uint16_t diag = GetTerrainDiag(p);
		uint16_t* hmap = GetTerrainHeightMap(p);

		uint16_t* vmap = GetTerrainVisualMap(p);

		static const double sxy = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;
		bool hit = false;

		int rot = GetTerrainDiag(p);

		float hi = GetTerrainHi(p);
		phys->max_height = fmaxf(hi, phys->max_height);

		for (int hy = 0; hy < HEIGHT_CELLS; hy++)
		{
			for (int hx = 0; hx < HEIGHT_CELLS; hx++)
			{
				uint16_t vis = *vmap;
				int elv = vis>>15;
				int mat = vis&0x3F;
				vmap+=2; // 2x visual cells / height cell

				if (mat == 4)
					mat = 0; // rock
				else
				if (mat == 5)
					mat = 5; // blood
				else
				if (mat != 2) // else 2->2 (dirt)
					mat = 3+elv; // [hi]grass

				float x0 = (float)((x + hx * sxy) * phys->collect_mul_xy), x1 = (float)(x0 + sxy * phys->collect_mul_xy);
				float y0 = (float)((y + hy * sxy) * phys->collect_mul_xy), y1 = (float)(y0 + sxy * phys->collect_mul_xy);

				float v[4][3] =
				{
					{x0,y0,(float)hmap[hy*(HEIGHT_CELLS+1) + hx] * phys->collect_mul_z},
					{x1,y0,(float)hmap[hy*(HEIGHT_CELLS + 1) + hx + 1] * phys->collect_mul_z},
					{x0,y1,(float)hmap[(hy + 1)*(HEIGHT_CELLS + 1) + hx] * phys->collect_mul_z},
					{x1,y1,(float)hmap[(hy + 1)*(HEIGHT_CELLS + 1) + hx + 1] * phys->collect_mul_z},
				};

				if (rot & 1)
				{
					// v[2], v[0], v[1]
					{
						item->tri[0][0] = v[2][0];
						item->tri[0][1] = v[2][1];
						item->tri[0][2] = v[2][2];

						item->tri[1][0] = v[0][0];
						item->tri[1][1] = v[0][1];
						item->tri[1][2] = v[0][2];

						item->tri[2][0] = v[1][0];
						item->tri[2][1] = v[1][1];
						item->tri[2][2] = v[1][2];

						float e1[3] = { v[0][0] - v[2][0],v[0][1] - v[2][1],v[0][2] - v[2][2] };
						float e2[3] = { v[1][0] - v[2][0],v[1][1] - v[2][1],v[1][2] - v[2][2] };
						CrossProduct(e1, e2, item->nrm);

						assert(fabsf(item->nrm[0]) + fabsf(item->nrm[1]) + fabsf(item->nrm[2]) > 0.001);

						float nrm = 1.0f / sqrtf(
							item->nrm[0] * item->nrm[0] +
							item->nrm[1] * item->nrm[1] +
							item->nrm[2] * item->nrm[2]);

						item->nrm[0] *= nrm;
						item->nrm[1] *= nrm;
						item->nrm[2] *= nrm;
						item->nrm[3] = -(v[2][0] * item->nrm[0] + v[2][1] * item->nrm[1] + v[2][2] * item->nrm[2]);

						item->material = mat;
						item++;
					}

					// v[2], v[1], v[3]
					{
						item->tri[0][0] = v[2][0];
						item->tri[0][1] = v[2][1];
						item->tri[0][2] = v[2][2];

						item->tri[1][0] = v[1][0];
						item->tri[1][1] = v[1][1];
						item->tri[1][2] = v[1][2];

						item->tri[2][0] = v[3][0];
						item->tri[2][1] = v[3][1];
						item->tri[2][2] = v[3][2];

						float e1[3] = { v[1][0] - v[2][0],v[1][1] - v[2][1],v[1][2] - v[2][2] };
						float e2[3] = { v[3][0] - v[2][0],v[3][1] - v[2][1],v[3][2] - v[2][2] };
						CrossProduct(e1, e2, item->nrm);
						float nrm = 1.0f / sqrtf(
							item->nrm[0] * item->nrm[0] +
							item->nrm[1] * item->nrm[1] +
							item->nrm[2] * item->nrm[2]);
						item->nrm[0] *= nrm;
						item->nrm[1] *= nrm;
						item->nrm[2] *= nrm;
						item->nrm[3] = -(v[2][0] * item->nrm[0] + v[2][1] * item->nrm[1] + v[2][2] * item->nrm[2]);

						item->material = mat;
						item++;
					}
				}
				else
				{
					// v[0], v[3], v[2]
					{
						item->tri[0][0] = v[0][0];
						item->tri[0][1] = v[0][1];
						item->tri[0][2] = v[0][2];

						item->tri[1][0] = v[3][0];
						item->tri[1][1] = v[3][1];
						item->tri[1][2] = v[3][2];

						item->tri[2][0] = v[2][0];
						item->tri[2][1] = v[2][1];
						item->tri[2][2] = v[2][2];

						float e1[3] = { v[3][0] - v[0][0],v[3][1] - v[0][1],v[3][2] - v[0][2] };
						float e2[3] = { v[2][0] - v[0][0],v[2][1] - v[0][1],v[2][2] - v[0][2] };
						CrossProduct(e1, e2, item->nrm);
						float nrm = 1.0f / sqrtf(
							item->nrm[0] * item->nrm[0] +
							item->nrm[1] * item->nrm[1] +
							item->nrm[2] * item->nrm[2]);
						item->nrm[0] *= nrm;
						item->nrm[1] *= nrm;
						item->nrm[2] *= nrm;
						item->nrm[3] = -(v[0][0] * item->nrm[0] + v[0][1] * item->nrm[1] + v[0][2] * item->nrm[2]);

						item->material = mat;
						item++;
					}

					// v[0], v[1], v[3]
					{
						item->tri[0][0] = v[0][0];
						item->tri[0][1] = v[0][1];
						item->tri[0][2] = v[0][2];

						item->tri[1][0] = v[1][0];
						item->tri[1][1] = v[1][1];
						item->tri[1][2] = v[1][2];

						item->tri[2][0] = v[3][0];
						item->tri[2][1] = v[3][1];
						item->tri[2][2] = v[3][2];

						float e1[3] = { v[1][0] - v[0][0],v[1][1] - v[0][1],v[1][2] - v[0][2] };
						float e2[3] = { v[3][0] - v[0][0],v[3][1] - v[0][1],v[3][2] - v[0][2] };
						CrossProduct(e1, e2, item->nrm);
						float nrm = 1.0f / sqrtf(
							item->nrm[0] * item->nrm[0] +
							item->nrm[1] * item->nrm[1] +
							item->nrm[2] * item->nrm[2]);
						item->nrm[0] *= nrm;
						item->nrm[1] *= nrm;
						item->nrm[2] *= nrm;
						item->nrm[3] = -(v[0][0] * item->nrm[0] + v[0][1] * item->nrm[1] + v[0][2] * item->nrm[2]);

						item->material = mat;
						item++;
					}
				}

				rot >>= 1;
			}

			vmap+=VISUAL_CELLS; // 2x visual cells / height cell
		}
		phys->soup_items += faces;
	}
};

// ═══════════════════════════════════════════════════════════════════════════
// Animate() — SINGLE-PLAYER / EDITOR ONLY (server==0)
// ═══════════════════════════════════════════════════════════════════════════
// WARNING TO ALL AGENTS: This function runs ONLY in single-player/editor mode.
// For multiplayer clients, physics is server-authoritative via MpStepOnce() in
// server_tick.cpp. The client receives position snapshots — Animate() must NOT
// be called when a server connection exists.
//
// Restored from commit 933fb386 (deleted in c96f25cf during multiplayer refactor).
// Guarded by `if (server == 0)` in game.cpp.
//
// KNOWN BUG: FL-040 direction mismatch — atan2 argument order in the direction
// calculation (line ~1430) does not match PrepareLocalMovementStepIO() convention.
// Movement works but facing direction is rotated ~90 degrees.
// ═══════════════════════════════════════════════════════════════════════════

int Animate(Physics* phys, uint64_t stamp, PhysicsIO* io, const LocalPhysicsActorProfile* actor_profile, bool me)
{
	// [FLOW:PHYSICS] Main Physics Integration Loop
	// Uses fixed-timestep (15ms / ~66Hz) to ensure deterministic behavior.
	// Handles:
	// - Input Forces & Torque
	// - Velocity Integration (Euler method)
	// - Collision Detection against World (triangle soup from BSP + terrain)
	// - State Updates (Position, Yaw, Grounded flag)
    static int calls = 0;
	if (PhysicsDebugEnabled() && calls % 10 == 0 && me)
	{
		printf("ANIM_ENTRY: me=%d stamp=%llu phys->stamp=%llu dt_calc=%d\n", me, stamp, phys->stamp, (int)(stamp - phys->stamp));
		fflush(stdout);
	}
	calls++;

	// Legacy single-player/editor call sites may not have an actor profile.
	// Treat null as HUMAN and pass this value downstream instead of the pointer.
	const LocalPhysicsActorProfile::Kind actor_kind =
		actor_profile ? actor_profile->kind : LocalPhysicsActorProfile::HUMAN;
	float xy_speed = 0.13f;
	float radius_cells = actor_kind != LocalPhysicsActorProfile::HUMAN ? 3.0f : 2.0f; // in full x-cells
	float patch_cells = 3.0f * HEIGHT_CELLS; // patch size in screen cells (zoom is 3.0)
	float world_patch = VISUAL_CELLS; // patch size in world coords
	float world_radius = radius_cells / patch_cells * world_patch;
	float height_cells = actor_kind != LocalPhysicsActorProfile::HUMAN ? 9.0f : 7.0f; // 7.5; decreased (hair are soft)

	// 2/3 = 1/(zoom*sin30)
	const float world_height = height_cells * 2 / 3 / (float)cos(30 * M_PI / 180) * HEIGHT_SCALE;

	static const int interval = 15000; // update physics step in [us]

	uint64_t dt_us = stamp - phys->stamp;
	io->dt = (dt_us > (uint64_t)INT_MAX) ? INT_MAX : (int)dt_us;
	if (dt_us > 500000)
	{
		// stall — skip catch-up, sync stamps (FL-032: was int cast, wrapped negative for large gaps)
		io->dt = 0;
		phys->stamp = stamp;
	}

	int steps_handled = 0;

	// Render interpolation: save position before last physics step so we can
	// interpolate between pre-step and post-step positions for smooth rendering.
	// Without this, the 15ms fixed timestep misaligned with ~16.67ms render frames
	// causes periodic "double step" frames (~7x/sec) visible as movement jitter.
	float prev_pos[3] = { phys->pos[0], phys->pos[1], phys->pos[2] };

	while (stamp - phys->stamp >= interval) // 15ms physics steps ( ~66 steps/sec )
	{
		// FL-032: safety cap — prevent runaway catch-up loops
		if (steps_handled >= 34) // ~500ms max catch-up (34 * 15ms)
		{
			phys->stamp = stamp;
			break;
		}
		prev_pos[0] = phys->pos[0];
		prev_pos[1] = phys->pos[1];
		prev_pos[2] = phys->pos[2];
		steps_handled++;

		phys->dbg_last_zeroed_after_sweep = 0;
		phys->dbg_last_zero_reason_mask = 0;
		phys->dbg_last_contact_normal_z = 0.0f;
		phys->dbg_last_auto_jump = 0;
		phys->dbg_last_ix = 0;
		phys->dbg_last_iy = 0;
		phys->dbg_last_xy_len = 0.0f;
		phys->dbg_last_move_dx_world = 0.0f;
		phys->dbg_last_move_dy_world = 0.0f;
		phys->dbg_last_pre_sweep_vel[0] = phys->vel[0];
		phys->dbg_last_pre_sweep_vel[1] = phys->vel[1];
		phys->dbg_last_pre_sweep_vel[2] = phys->vel[2];
		phys->dbg_last_post_sweep_vel[0] = phys->vel[0];
		phys->dbg_last_post_sweep_vel[1] = phys->vel[1];
		phys->dbg_last_post_sweep_vel[2] = phys->vel[2];

		uint64_t elaps = stamp - phys->stamp;
		if (elaps > interval)
			elaps = interval;
		phys->stamp += elaps;
		float dt = elaps * (60.0f / 1000000.0f);

        // Debug Physics
        static int p_debug = 0;
		if (PhysicsDebugEnabled() && p_debug++ % 100 == 0 && me)
		{
			printf("P_DBG: dt=%.2f force=(%.2f, %.2f) vel=(%.2f, %.2f) pos=(%.2f, %.2f, %.2f)\n",
				dt, io->x_force, io->y_force, phys->vel[0], phys->vel[1], phys->pos[0], phys->pos[1], phys->pos[2]);
			fflush(stdout);
		}

		const int step_offs = 3*1024;
		const int step_mask = (8*1024-1);
		int prev_step;
		float xy_vel;
		float in_water;

		// by having old and new water level we can (in future) keep player floating on top of waves
		phys->water = io->water;

		// YAW
		{
			if (io->torque >= 1000000)
			{
				phys->yaw = io->yaw;
				phys->yaw_vel = 0;
				phys->yaw_vel = 0;
			}
			else
			{
				int da = 0;
				if (io->torque < 0)
					da--;
				if (io->torque > 0)
					da++;

				phys->yaw_vel += dt * io->torque; //da;

				if (phys->yaw_vel > 10)
					phys->yaw_vel = 10;
				else
					if (phys->yaw_vel < -10)
						phys->yaw_vel = -10;

				if (fabsf(phys->yaw_vel) < 1 && !da)
					phys->yaw_vel = 0;

				phys->yaw += dt * 0.5f * phys->yaw_vel;

				float vel_damp = powf(0.9f, dt);
				phys->yaw_vel *= vel_damp;
				phys->yaw_vel *= vel_damp;
			}
		}

		// VEL & ACC
		float xy_len = sqrtf(io->x_force * io->x_force + io->y_force * io->y_force);

		int ix = 0, iy = 0;
		{
			if (io->x_force < 0)
				ix--;
			if (io->x_force > 0)
				ix++;
			if (io->y_force > 0)
				iy++;
			if (io->y_force < 0)
				iy--;

			/*
			float dir[3][3] =
			{
				{315,  0 , 45},
				{270, -1 , 90},
				{225, 180, 135},
			};
			*/

			float dx, dy;
			float move_dx_world = 0;
			float move_dy_world = 0;
			if (xy_len < 0.01)
			{
				// FL-3858 ACTIVE: This is the SINGLE-PLAYER/LOCAL facing owner.
				// Physics.cpp owns player_dir for local mode; mp_step.cpp owns it
				// for multiplayer. Both MUST use the same world-facing convention
				// because render_sprite_blit.cpp and game_input.cpp consume
				// player.dir from whichever owner set it. Changing only one owner
				// leaves the other mode broken. Pre-complaint convention (60fb2c33)
				// had idle reset to yaw + movement atan2(dy,dx)+90; see FL-3858.
				// Preserve the last gameplay-facing direction while idle. Local TERM++
				// behavior already does this, and forcing idle facing back to camera
				// yaw here would recreate the "auto-swivel on stop" bug that should
				// be owned by gameplay state, not by presentation.
				xy_len = 0;
				ix = 0;
				iy = 0;
				dx = 0;
				dy = 0;
			}
			else
			{
				dx = io->x_force / xy_len;
				dy = io->y_force / xy_len;
				if (xy_len > 1)
					xy_len = 1;

				const float yaw_rad = (float)(phys->yaw * (M_PI / 180));
				move_dx_world = dx * cosf(yaw_rad) - dy * sinf(yaw_rad);
				move_dy_world = dx * sinf(yaw_rad) + dy * cosf(yaw_rad);

				// Active movement facing: shared helper prevents SP/MP convention
				// drift. See facing_space.h::FacingMovementStep.
				// FL-3858: This is a mechanical extraction only — formula, smoothing,
				// and snap threshold are unchanged. Does NOT close FL-3858.
				phys->player_dir = FacingMovementStep(phys->player_dir, move_dx_world, move_dy_world);
			}

			phys->dbg_last_ix = ix;
			phys->dbg_last_iy = iy;
			phys->dbg_last_xy_len = xy_len;
			phys->dbg_last_move_dx_world = move_dx_world;
			phys->dbg_last_move_dy_world = move_dy_world;

			/*
			if (dir[iy + 1][ix + 1] >= 0)
				phys->player_dir = dir[iy + 1][ix + 1] + phys->yaw;
			*/

			if (ix || iy)
			{
				float cs = cosf(phys->slope);
				phys->vel[0] += dt * move_dx_world * cs;
				phys->vel[1] += dt * move_dy_world * cs;
			}

			float sqr_vel_xy = phys->vel[0] * phys->vel[0] + phys->vel[1] * phys->vel[1];
			if (sqr_vel_xy < 1 && !ix && !iy)
			{
				phys->vel[0] = 0;
				phys->vel[1] = 0;

				if (actor_kind != LocalPhysicsActorProfile::BEE)
				{
					phys->player_stp = -1;
					/*
					if (me)
						AudioWalk(0, 65535, actor_kind, phys->mat);
					*/
				}
			}
			else
			{
				// speed limit is 27 for air / ground and 10 for full in water
				float xy_limit = 27 - 17 * (phys->water - phys->pos[2]) / world_height;

				float lim = 27;
				lim *= xy_len * xy_len*xy_len;

				if (xy_limit < 10)
					xy_limit = 10;
				if (xy_limit > lim)
					xy_limit = lim;

				if (sqr_vel_xy > xy_limit)
				{
					float n = sqrtf(xy_limit / sqr_vel_xy);
					sqr_vel_xy = xy_limit;
					phys->vel[0] *= n;
					phys->vel[1] *= n;
				}

				if (phys->player_stp < 0)
					phys->player_stp = 0;

				// so 8 frame walk anim divides stp / 1024 to get frame num

				prev_step = (phys->player_stp + step_offs) & step_mask;

				xy_vel = sqrtf(sqr_vel_xy);

				if (actor_kind == LocalPhysicsActorProfile::BEE) // slower for flying mounts
					phys->player_stp = (~(1 << 31))&(phys->player_stp + (int)(24 * xy_vel));
				else
					phys->player_stp = (~(1 << 31))&(phys->player_stp + (int)(64 * xy_vel));

				float vel_damp = powf(0.9f, dt);
				phys->vel[0] *= vel_damp;
				phys->vel[1] *= vel_damp;
			}

			// [FLOW:PHYSICS] Gravity and Buoyancy (Newton vs. Archimedes)
			//
			// WHY water buoyancy: When character is submerged, water exerts upward buoyant
			// force (Archimedes principle: buoyant force equals weight of displaced water).
			// Below water surface, buoyancy opposes gravity. Above water, full gravity applies.
			//
			// BUOYANCY CALCULATION:
			// wave = sinusoidal wave animation (cosmetic, doesn't affect physics significantly)
			// cnt = center of mass as fraction of character height (0.78 ± wave amplitude)
			// acc = (water_z - character_center_z) / (2 * cnt * height)
			//     = buoyancy acceleration (positive = upward, negative = downward)
			//
			// WHY clamping: Prevents extreme acceleration when deeply submerged or high above water.
			// Clamp to [-cnt, 1-cnt] keeps acceleration within reasonable bounds.
			//
			// FLY MODE OVERRIDE:
			// WHY separate fly path: Flying mounts ignore gravity/buoyancy, use z_force directly.
			// This allows ascending/descending without physics fighting the input.
			float wave = 2 * (int)((phys->stamp >> 10) & 0x7FF) * (float)M_PI / 0x800;
			float ampl = 0.05f;
			if (ix || iy)
				ampl = 0.1f; // Larger wave amplitude when moving (more disturbance)

			float cnt = 0.78f + ampl * sinf(wave); // Center of mass (fraction of height, with wave)
			float acc = (phys->water - (phys->pos[2] + cnt * world_height)) / (2 * cnt*world_height);
			if (acc < 0 - cnt)
				acc = 0 - cnt; // Clamp downward acceleration (falling in air)
			if (acc > 1 - cnt)
				acc = 1 - cnt; // Clamp upward acceleration (deeply submerged)

			if (io->fly)
			{
				// WHY fly mode: Flying mounts (bees) use explicit z_force input instead of gravity.
				// Player controls vertical movement directly via z_force (from input or AI).
				float z_acc = io->z_force;
				if (fabsf(z_acc) > 0.001f)
				{
					// [FLOW:PHYSICS] Velocity integration — vertical force (fly mode)
					phys->vel[2] += dt * z_acc;
				}

				// WHY drag: Damping prevents infinite acceleration in fly mode.
				// Drag factor 0.9^dt (exponential decay) simulates air resistance.
				float z_damp = powf(0.9f, dt);
				phys->vel[2] *= z_damp;
			}
			else
			{
				// [FLOW:PHYSICS] Velocity integration — gravity + buoyancy (ground/water mode)
				// WHY Euler integration: Simple vel += acc*dt is sufficient for game physics.
				// More accurate methods (Verlet, RK4) aren't needed for this collision model.
				phys->vel[2] += dt * acc;
			}


			//		if (phys->vel[2] < -1)
			//			phys->vel[2] = -1;

			// water resistance
			float res = (phys->water - phys->pos[2]) / world_height;
			if (res < 0)
				res = 0;
			if (res > 1)
				res = 1;

			in_water = res;

			float xy_res = powf(1.0f - 0.5f * res, dt);
			float z_res = powf(1.0f - 0.1f * res, dt);

			if (actor_kind == LocalPhysicsActorProfile::BEE && phys->vel[2] < 0)
				z_res = (float)pow(1.0f - 0.1f, dt);

			phys->vel[0] *= xy_res;
			phys->vel[1] *= xy_res;
			phys->vel[2] *= z_res;
		}

		phys->vel[0] += io->x_impulse;
		phys->vel[1] += io->y_impulse;

		if (fabsf(io->x_impulse) + fabsf(io->y_impulse) > 1 && phys->vel[2] > 0)
			phys->vel[2] = 0;

		io->x_impulse *= 0.5;
		io->y_impulse *= 0.5;

		int material_votes[6] = {0}; /* rock:0, wood:1, dirt:2, grass:3, hi-grass:4, blood:5, water:6 */

		// POS - troubles!
		float prev_vel_z = phys->vel[2];
		float contact_normal_z = 0;
		{
			////////////////////
			float dx = dt * phys->vel[0];
			float dy = dt * phys->vel[1];

			double cx = phys->pos[0] + dx * 0.5;
			double cy = phys->pos[1] + dy * 0.5;
			double th = 0.1;

			double qx = fabs(dx) * 0.5 + world_radius + th;
			double qy = fabs(dy) * 0.5 + world_radius + th;

			double clip_world[4][4] =
			{
				{ 1, 0, 0, qx - cx },
				{-1, 0, 0, qx + cx },
				{ 0, 1, 0, qy - cy },
				{ 0,-1, 0, qy + cy },
				//	{ 0, 0, 1,            0 - phys->pos[2] },
				//	{ 0, 0,-1, world_height + phys->pos[2] }
			};

			// [FLOW:PHYSICS] Geometry Query — collect triangle soup from world and terrain
			//
			// WHY triangle soup: Physics operates on raw triangles, not high-level meshes.
			// We query the BSP tree (world meshes) and quadtree (terrain heightfield) to
			// collect all triangles near the character's trajectory. This "soup" is then
			// transformed to sphere-space and tested for collisions.
			//
			// WHY collect_mul_xy and collect_mul_z: Transform world coordinates to sphere space.
			// In sphere space, character is a unit sphere (radius=1.0), simplifying collision math.
			// collect_mul_xy = 1.0 / world_radius (scales XY to sphere space)
			// collect_mul_z = 2.0 / world_height  (scales Z to sphere space, factor of 2 for ellipsoid)
			//
			// WHY query clipping planes: Only collect triangles in a bounding box around
			// character trajectory. This culls distant geometry, improving performance.
			// clip_world defines 4 planes (±X, ±Y) forming a box around sphere path.
			phys->soup_items = 0;
			phys->collect_mul_xy = 1.0f / world_radius;
			phys->collect_mul_z = 2.0f / world_height;

			phys->max_height = io->water; // Track highest triangle Z (for flying height limit)

			// WHY callbacks: QueryWorld and QueryTerrain invoke callbacks for each mesh/patch.
			// MeshCollect transforms mesh triangles to sphere space and adds to soup.
			// PatchCollect transforms terrain heightfield quads to triangles and adds to soup.
			QueryWorldCB cb = { Physics::MeshCollect , Physics::SpriteCollect };
			QueryWorld(phys->world, 4, clip_world, &cb, phys);
			QueryTerrain(phys->terrain, 4, clip_world, 0xAA, Physics::PatchCollect, phys);
			if (PhysicsDebugEnabled())
			{
				printf("PHYS_QUERY: world=%p soup_items=%d\n", (void*)phys->world, phys->soup_items);
				fflush(stdout);
			}

			// note: phys should keep soup allocation, resize it x2 if needed

			// transform Z so our ellipsolid becomes a sphere
			// just multiply:
			//   px,py, dx,dy, and all verts x,y coords by 1.0/horizontal_radius
			//   pz, dz and all verts z coords by 1.0/(HEIGHT_SCALE*vertical_radius)

			float sphere_pos[3] =  // set current sphere center
			{
				phys->pos[0] * phys->collect_mul_xy,
				phys->pos[1] * phys->collect_mul_xy,
				(phys->pos[2] + world_height * 0.5f) * phys->collect_mul_z,
			};

			float sphere_vel[3] =
			{
				xy_speed * phys->vel[0] * dt * phys->collect_mul_xy,
				xy_speed * phys->vel[1] * dt * phys->collect_mul_xy,
				phys->vel[2] * dt * phys->collect_mul_z,
			}; // set velocity (must include gravity impact)

			const float xy_thresh = 0.002f;
			const float z_thresh = 0.001f;

			// [FLOW:PHYSICS] Collision Sweep — substep iteration loop
			//
			// WHY substeps: A single physics timestep (15ms) may require multiple collision
			// responses. For example, if sphere bounces between two walls, each bounce is
			// a separate substep. We iterate until velocity falls below threshold or max
			// iterations reached (prevents infinite loops in degenerate geometry).
			//
			// WHY velocity threshold: xy_thresh=0.002, z_thresh=0.001 in sphere space.
			// Below this, sphere is effectively stopped. Continuing would waste CPU and
			// risk numerical instability from tiny floating-point velocities.
			//
			// WHY max iterations: 10 substeps is empirically sufficient for typical scenarios.
			// If geometry is degenerate (e.g., self-intersecting triangles), we bail out
			// to prevent hangs. Character may clip slightly but game remains responsive.
			int items = phys->soup_items;
			int iters_left = 10;

			while (fabsf(sphere_vel[0]) > xy_thresh || fabsf(sphere_vel[1]) > xy_thresh || fabsf(sphere_vel[2]) > z_thresh)
			{
				// [FLOW:PHYSICS] Collision sweep — find earliest TOI across all triangles
				//
				// WHY iterate all triangles: We must test every triangle in soup to find
				// the FIRST collision along the velocity vector. If we only tested one
				// triangle, sphere might tunnel through earlier collisions.
				SoupItem* collision_item = 0;
				float collision_time = 2.0f; // No collision (>1 means TOI out of range)
				float collision_pos[3];

				for (int i = 0; i < items; i++)
				{
					SoupItem* item = phys->soup + i;

					float contact_pos[3];
					float time = item->CheckCollision(sphere_pos, sphere_vel, contact_pos);

					assert(time >= 0); // Sanity check: TOI should never be negative

					if (time < collision_time)
					{
						// WHY distance validation: Verify that at time t, sphere surface
						// is exactly 1.0 unit from contact point (within epsilon). This
						// catches bugs in CheckCollision math.
						float check[3] =
						{
							sphere_pos[0] + sphere_vel[0] * time - contact_pos[0],
							sphere_pos[1] + sphere_vel[1] * time - contact_pos[1],
							sphere_pos[2] + sphere_vel[2] * time - contact_pos[2],
						};

						float sqr_dist = DotProduct(check, check);

						if (fabsf(sqr_dist) - 1.0f > 0.001)
						{
							// Distance check failed — CheckCollision returned invalid contact
							// This should never happen (indicates bug in collision math)
							assert(0);
						}

						// Update earliest collision
						collision_item = item;
						collision_time = time;
						collision_pos[0] = contact_pos[0];
						collision_pos[1] = contact_pos[1];
						collision_pos[2] = contact_pos[2];
					}
				}

				if (!collision_item)
				{
					// [FLOW:PHYSICS] Position update — no collision, move full distance
					sphere_pos[0] += sphere_vel[0];
					sphere_pos[1] += sphere_vel[1];
					sphere_pos[2] += sphere_vel[2];
					break; // No more collisions, exit substep loop
				}

				// [FLOW:PHYSICS] Position update — move to collision point
				//
				// WHY move to collision: Sphere center moves along velocity vector until
				// sphere surface touches contact point at time=collision_time.
				float full_step[3] =
				{
					sphere_vel[0] * collision_time,
					sphere_vel[1] * collision_time,
					sphere_vel[2] * collision_time
				};

				// [FLOW:PHYSICS] Contact normal calculation
				//
				// WHY slide_normal: Normal vector from contact point to sphere center.
				// This is the direction to reflect velocity (perpendicular to surface).
				// Formula: N = (sphere_center_at_collision - contact_point)
				//            = (sphere_pos + full_step) - collision_pos
				float slide_normal[3] =
				{
					sphere_pos[0] + full_step[0] - collision_pos[0],
					sphere_pos[1] + full_step[1] - collision_pos[1],
					sphere_pos[2] + full_step[2] - collision_pos[2]
				};

				// WHY material votes: Track which material was hit most often this frame.
				// Used for footstep audio (rock, wood, grass sounds). Voting system handles
				// multiple collisions per frame (e.g., running along rough terrain hits many triangles).
				material_votes[collision_item->material]++;

				// WHY safe distance: Move sphere to 0.01 units before exact collision point.
				// This prevents numerical precision issues where sphere "embeds" slightly
				// due to floating-point error. Safe distance ensures clean separation.
				float full_len = sqrtf(full_step[0] * full_step[0] + full_step[1] * full_step[1] + full_step[2] * full_step[2]);
				float ratio = 0.0f;
				if (full_len > 0.01f)
					ratio = (full_len - 0.01f) / full_len;

				sphere_pos[0] += full_step[0] * ratio;
				sphere_pos[1] += full_step[1] * ratio;
				sphere_pos[2] += full_step[2] * ratio;

				// WHY consume time: After collision at time t, we've consumed t fraction
				// of the timestep. Remaining velocity is scaled by (1-t) to represent
				// the remaining time available for movement after the bounce.
				float remain = 1.0f - collision_time;
				if (remain >= 0.99f)
					remain = 0.99f; // Clamp to prevent remain > 1.0 (numerical precision)

				float pre_vel_xy_x = sphere_vel[0];
				float pre_vel_xy_y = sphere_vel[1];
				float pre_vel_xy_z = sphere_vel[2];
				sphere_vel[0] *= remain;
				sphere_vel[1] *= remain;
				sphere_vel[2] *= remain;

				// [FLOW:PHYSICS] Auto-jump — step climbing
				//
				// WHY auto-jump: When character runs into a low obstacle (step, curb),
				// automatically trigger a small jump to climb over it. This feels more
				// natural than stopping dead when hitting a 1-unit-tall step.
				//
				// CONDITION: collision_time < 0.2 (hit obstacle very early in trajectory,
				// suggesting we're running straight into it, not glancing off), AND
				// slide_normal[2] < 0.8 (wall is steep, not a floor — prevents jumping
				// on gentle slopes).
				//
				// TODO: Add height check — only auto-jump for steps below threshold (e.g., 0.5 units)
				if (!io->jump && !io->fly && collision_time < 0.2f && slide_normal[2] < 0.8f) // high wall
				{
					// check if step is low enough
					// ... (height check not yet implemented)
					io->jump = true;
					phys->dbg_last_auto_jump = 1;
					if (PhysicsDebugEnabled()) printf("AUTO_JUMP triggered! ct=%f nz=%f\n", collision_time, slide_normal[2]);
				}

				// [FLOW:PHYSICS] Contact response — velocity reflection
				//
				// WHY velocity reflection: When sphere hits surface, velocity component
				// perpendicular to surface is removed (sphere can't move into surface).
				// Velocity component parallel to surface is preserved (sphere slides along surface).
				//
				// DERIVATION:
				// vel_perp = dot(vel, N) * N  (velocity component along normal)
				// vel_parallel = vel - vel_perp (velocity component tangent to surface)
				// After reflection: vel_new = vel_parallel = vel - dot(vel, N) * N
				//
				// WHY no restitution multiplier: We remove perpendicular component entirely
				// (inelastic collision, restitution=0). This prevents bouncing, which would
				// look unnatural for character movement. Elastic collision (restitution=1)
				// would be: vel_new = vel_parallel - vel_perp (bounce off surface).
				//
				// NOTE: Commented-out code below would add a small push away from surface,
				// but this causes jittering when sphere is trapped between surfaces.
				// Current approach (no push) is more stable.
				/*
				sphere_pos[0] += slide_normal[0] * 0.001;
				sphere_pos[1] += slide_normal[1] * 0.001;
				sphere_pos[2] += slide_normal[2] * 0.001;
				*/

				float project = DotProduct(sphere_vel, slide_normal); // vel component along normal
				sphere_vel[0] -= slide_normal[0] * project; // Remove perpendicular component
				sphere_vel[1] -= slide_normal[1] * project;
				sphere_vel[2] -= slide_normal[2] * project;

				if (me && PhysicsDebugEnabled())
				{
					static int deflect_logs = 0;
					if (deflect_logs < 128)
					{
						float pre_xy_mag = sqrtf(pre_vel_xy_x * pre_vel_xy_x + pre_vel_xy_y * pre_vel_xy_y);
						float post_xy_mag = sqrtf(sphere_vel[0] * sphere_vel[0] + sphere_vel[1] * sphere_vel[1]);
						if (pre_xy_mag > xy_thresh && post_xy_mag > xy_thresh)
						{
							float pre_heading = (float)(atan2(pre_vel_xy_x, pre_vel_xy_y) * 180.0 / M_PI);
							float post_heading = (float)(atan2(sphere_vel[0], sphere_vel[1]) * 180.0 / M_PI);
							float deflection = fabsf(post_heading - pre_heading);
							while (deflection > 360.0f) deflection -= 360.0f;
							if (deflection > 180.0f) deflection = 360.0f - deflection;
							if (deflection >= 30.0f)
							{
								printf("[PHYS-DEFLECT] pos=(%.2f,%.2f,%.2f) pre=(%.3f,%.3f,%.3f) post=(%.3f,%.3f,%.3f) head=(%.2f->%.2f) def=%.2f ct=%.3f remain=%.3f nrm=(%.3f,%.3f,%.3f) mat=%d\n",
									sphere_pos[0] / phys->collect_mul_xy,
									sphere_pos[1] / phys->collect_mul_xy,
									sphere_pos[2] / phys->collect_mul_z - world_height * 0.5f,
									pre_vel_xy_x, pre_vel_xy_y, pre_vel_xy_z,
									sphere_vel[0], sphere_vel[1], sphere_vel[2],
									pre_heading, post_heading, deflection,
									collision_time, remain,
									slide_normal[0], slide_normal[1], slide_normal[2],
									collision_item->material);
								fflush(stdout);
								deflect_logs++;
							}
						}
					}
				}

				// [FLOW:PHYSICS] Grounded detection — accumulate upward contact normals
				//
				// WHY accumulate contact_normal_z: A single collision with normal Z < 1.0
				// (sloped floor) may not be enough to consider character grounded. By
				// accumulating normal Z across multiple collisions, we handle rough terrain
				// (many small triangles) and slopes. If total accumulated Z >= 1.0, character
				// is grounded (has sufficient upward support).
				contact_normal_z = fmaxf(contact_normal_z, slide_normal[2]);

				// [FLOW:PHYSICS] Substep iteration — decrement iteration counter
				if (!--iters_left)
					break; // Max iterations reached, bail out (prevents infinite loop)
			}

			/*
			if (iters_left)
				phys->collision_failure = false;
			else
				if (!phys->collision_failure && !ix && !iy && contact_normal_z > 0)
				{
					// something's wrong
					// relax - ignore collisions from top
					phys->collision_failure = true;
					//printf("CRITICAL! move to resolve\n");
				}

			if (phys->collision_failure && !ignore_roof)
			{
				ignore_roof = true;
				goto retry_without_roof;
			}
			*/

			//printf("iters_left:%d\n", iters_left);

			// convert back to world coords
			// just multiply:
			//   px,py by horizontal_radius
			//   and pz by (HEIGHT_SCALE*vertical_radius)

			// we are done, update

			float pos[3] =
			{
				sphere_pos[0] / phys->collect_mul_xy,
				sphere_pos[1] / phys->collect_mul_xy,
				sphere_pos[2] / phys->collect_mul_z - world_height * 0.5f
			};

			/*

			float vel[3] =
			{
				(pos[0] - phys->pos[0]) / dt,
				(pos[1] - phys->pos[1]) / dt,
				(pos[2] - phys->pos[2]) / (dt*HEIGHT_SCALE)
			};

			float org_vel[3] =
			{
				phys->vel[0],
				phys->vel[1],
				phys->vel[2]
			};

			phys->vel[0] *= xy_speed;
			phys->vel[1] *= xy_speed;
			phys->vel[2] /= HEIGHT_SCALE;
			float vn = sqrtf(phys->vel[0] * phys->vel[0] + phys->vel[1] * phys->vel[1] + phys->vel[2] * phys->vel[2]);

			if (vn > 0.001)
			{


				vn = 1.0 / vn;
				// leave direction only
				phys->vel[0] *= vn;
				phys->vel[1] *= vn;
				phys->vel[2] *= vn;

				// project
				vn = DotProduct(phys->vel, vel);

				// apply magnitude from poisition offs
				phys->vel[0] *= vn / xy_speed;
				phys->vel[1] *= vn / xy_speed;
				phys->vel[2] *= vn * HEIGHT_SCALE;

				printf("iters_left:%d, in: %f,%f out: %f,%f\n", iters_left, org_vel[0], org_vel[1], phys->vel[0], phys->vel[1]);

				// average org and new
				phys->vel[0] = phys->vel[0] * 0.0 + org_vel[0] * 1.0;
				phys->vel[1] = phys->vel[1] * 0.0 + org_vel[1] * 1.0;
				phys->vel[2] = phys->vel[2] * 0.0 + org_vel[2] * 1.0;
			}
			else
			{
				phys->vel[0] = 0;
				phys->vel[1] = 0;
				phys->vel[2] = 0;
			}
			*/

			float org_vel[3] =
			{
				phys->vel[0],
				phys->vel[1],
				phys->vel[2]
			};

			phys->vel[0] = (pos[0] - phys->pos[0]) / (xy_speed * dt);
			phys->vel[1] = (pos[1] - phys->pos[1]) / (xy_speed * dt);
			phys->vel[2] = (pos[2] - phys->pos[2]) / dt;

			float adz = fmaxf(0, phys->vel[2]) / HEIGHT_SCALE * 4;
			float adxy = xy_speed * sqrtf(phys->vel[0] * phys->vel[0] + phys->vel[1] * phys->vel[1]);
			phys->slope = atan2f(adz, adxy);

			// printf("iters_left:%d, in: %f,%f out: %f,%f\n", iters_left, org_vel[0], org_vel[1], phys->vel[0], phys->vel[1]);

			// slippery threshold?
			// use org (no-slippery) use new (full slippery)

			phys->vel[0] = 1.0f * phys->vel[0] + org_vel[0] * 0.0f;
			phys->vel[1] = 1.0f * phys->vel[1] + org_vel[1] * 0.0f;
			phys->vel[2] = 1.0f * phys->vel[2] + org_vel[2] * 0.0f;
			phys->dbg_last_post_sweep_vel[0] = phys->vel[0];
			phys->dbg_last_post_sweep_vel[1] = phys->vel[1];
			phys->dbg_last_post_sweep_vel[2] = phys->vel[2];
			phys->dbg_last_contact_normal_z = contact_normal_z;

			if (me && PhysicsDebugEnabled())
			{
				static int phys_sweep_deflect_logs = 0;
				float pre_xy_mag = sqrtf(org_vel[0] * org_vel[0] + org_vel[1] * org_vel[1]);
				float post_xy_mag = sqrtf(phys->vel[0] * phys->vel[0] + phys->vel[1] * phys->vel[1]);
				if (phys_sweep_deflect_logs < 80 &&
					pre_xy_mag > 0.01f &&
					post_xy_mag > 0.01f)
				{
					float pre_h = atan2f(org_vel[0], org_vel[1]) * 180.0f / M_PI;
					float post_h = atan2f(phys->vel[0], phys->vel[1]) * 180.0f / M_PI;
					while (pre_h < 0.0f) pre_h += 360.0f;
					while (pre_h >= 360.0f) pre_h -= 360.0f;
					while (post_h < 0.0f) post_h += 360.0f;
					while (post_h >= 360.0f) post_h -= 360.0f;
					float def = fabsf(post_h - pre_h);
					if (def > 180.0f)
						def = 360.0f - def;
					if (def >= 15.0f)
					{
						printf("[PHYS-SWEEP-DEFLECT] pos=(%.2f,%.2f,%.2f) pre_h=%.1f post_h=%.1f "
						       "def=%.1f pre_mag=%.3f post_mag=%.3f iters=%d cnz=%.3f accum=%.3f\n",
						       pos[0], pos[1], pos[2],
						       pre_h, post_h, def,
						       pre_xy_mag, post_xy_mag,
						       10 - iters_left,
						       contact_normal_z,
						       phys->accum_contact);
						fflush(stdout);
						phys_sweep_deflect_logs++;
					}
				}
			}

			// printf("contact_normal_z:%f, vel[0]: %f, vel[1]:%f, vel[2]:%f\n", contact_normal_z,fabsf(phys->vel[0]),fabsf(phys->vel[1]),fabsf(phys->vel[2]));

			if (ix || iy || contact_normal_z <= 0.0 || fabsf(phys->vel[0]) > 0.1 || fabsf(phys->vel[1]) > 0.1 || fabsf(phys->vel[2]) > 0.1 * 16)
			{
				int reason = 0;
				if (ix) reason |= 1 << 0;
				if (iy) reason |= 1 << 1;
				if (contact_normal_z <= 0.0f) reason |= 1 << 2;
				if (fabsf(phys->vel[0]) > 0.1f) reason |= 1 << 3;
				if (fabsf(phys->vel[1]) > 0.1f) reason |= 1 << 4;
				if (fabsf(phys->vel[2]) > 0.1f * 16.0f) reason |= 1 << 5;
				phys->dbg_last_zero_reason_mask = reason;
				phys->pos[0] = pos[0];
				phys->pos[1] = pos[1];
				phys->pos[2] = pos[2];
			}
			else
			{
				phys->dbg_last_zeroed_after_sweep = 1;
				phys->vel[0] = 0;
				phys->vel[1] = 0;
				phys->vel[2] = 0;
			}
		}

		// votes given, choose new winner
		{
			int mat = phys->mat; // default to prev winner (was grass)
			int votes = 0;
			for (int m=0; m<6; m++)
			{
				if (material_votes[m]>votes)
				{
					votes = material_votes[m];
					mat = m;
				}
			}
			phys->mat = mat;

			// override if legs are in water
			if (in_water>0.1)
				phys->mat = 6; // voting veto!
		}

		// [FLOW:PHYSICS] Grounded state accumulation
		//
		// WHY accumulate contact over multiple frames: Grounded detection shouldn't be
		// binary (grounded/airborne). Character on rough terrain may momentarily lose
		// contact between substeps, but should still be considered "grounded" for gameplay
		// purposes (can jump, footstep sounds, friction).
		//
		// WHY accumulation threshold 1.0: If contact_normal_z sums to >= 1.0 over recent
		// frames, character has sufficient upward support. This handles:
		// - Slopes: normal Z < 1.0 but still grounded (multiple collisions accumulate)
		// - Stairs: small vertical steps maintain contact accumulation
		// - Rough terrain: many small triangle collisions sum to grounded state
		//
		// WHY clamp to 5.0: Prevents unbounded accumulation when standing still on flat
		// floor (would accumulate infinitely). Clamping at 5 allows quick recovery from
		// airborne state but prevents overflow.

		float prev_contact = phys->accum_contact;

		phys->accum_contact += fmaxf(0.0f,contact_normal_z);
		if (phys->accum_contact > 5)
			phys->accum_contact = 5;

		// [FLOW:PHYSICS] Landing audio event
		//
		// WHY landing sound: When character transitions from airborne (accum_contact < 1.0)
		// to grounded (accum_contact >= 1.0), play landing sound. Volume is max (65535)
		// because landing impact is always loud (energy loss proportional to fall distance).
		if (me && prev_contact < 1.0 && phys->accum_contact >= 1.0)
		{
			// TODO: Scale volume by fall velocity (phys->vel[2]) for softer/harder landings
			AudioWalk(0, 65535, actor_kind, phys->mat);
		}
		else
		// [FLOW:PHYSICS] Footstep audio events
		//
		// WHY footstep sounds: When character is moving (player_stp >= 0) and grounded or
		// in water, play footstep sound at regular intervals (every 2048 steps).
		// Volume is proportional to velocity (louder when running, quieter when walking).
		if (me && (in_water>0.5 || phys->accum_contact >= 1.0) && phys->player_stp>=0)
		{
			// WHY logarithmic volume: Human perception of loudness is logarithmic.
			// log10(vel + 1) maps velocity [0, inf) to volume [0, inf) with diminishing returns.
			int volume = (int)(65535 * 1.0f*log10f(xy_vel + 1.0f));
			if (volume > 65535)
				volume = 65535;
			int next_step = (phys->player_stp + step_offs) & step_mask;
			if (prev_step < 2048 && next_step >= 2048)
				AudioWalk(1, volume, actor_kind, phys->mat); // Left footstep
			else
			if (prev_step < 3 * 2048 && next_step >= 3 * 2048)
				AudioWalk(2, volume, actor_kind, phys->mat); // Right footstep
		}


		// [FLOW:PHYSICS] Jump handling — vertical impulse
		//
		// WHY grounded check: Jump impulse is only applied when character has ground
		// contact (accum_contact >= 1.0) or is flying (mount>1). This prevents mid-air
		// double jumps and ensures realistic physics.
		//
		// WHY reset accum_contact: When jump is triggered, reset accumulation to 0.
		// This ensures character is considered "airborne" immediately after jump, preventing
		// multiple jumps in quick succession (accumulation needs time to rebuild).
		//
		// WHY max_fly_height check: Flying mounts can only jump if below max terrain height + 100.
		// This prevents flying infinitely high (breaks game balance, rendering issues).
		if (phys->accum_contact >= 1.0 || actor_kind == LocalPhysicsActorProfile::BEE)
		{
			if (io->jump)
			{

				phys->accum_contact = 0; // Reset grounded accumulation (now airborne)

				// ensure for bee flight current height is not > ground + max_fly_height
				if (actor_kind != LocalPhysicsActorProfile::BEE || phys->pos[2] < phys->max_height + 100)
				{
					// WHY velocity check: If character is falling (vel[2] < 0), set to jump velocity.
					// If already rising (vel[2] > 0), add to existing velocity (allows double-jump feel).
					if (phys->vel[2] < 0)
						phys->vel[2] = 10; // Jump velocity (10 units/sec upward)
					else
						phys->vel[2] += 10;

					if (me)
						AudioJump(65535, actor_kind);
				}

				io->jump = false; // Consume jump input (prevent re-triggering)
			}
		}

		// [FLOW:PHYSICS] Grounded flag output
		//
		// WHY output grounded to game.cpp: Game logic needs to know if character is
		// grounded for:
		// - Animation selection (idle/walk vs. fall/jump animations)
		// - Jump input validation (can only jump when grounded)
		// - Friction application (grounded characters have friction, airborne don't)
		io->grounded = phys->accum_contact >= 1.0;

		// WHY decay accumulation: accum_contact decays by 10% per frame to prevent stale
		// grounded state. If character goes airborne, accumulation drains quickly (within
		// ~10 frames). This allows quick transition to airborne animations.
		phys->accum_contact *= 0.9f;

		if (phys->vel[2] > 20)
			phys->vel[2] = 20;

		if (actor_kind == LocalPhysicsActorProfile::BEE)
		{
			if (!io->grounded)
			{
				float v = fmaxf(1.0f, phys->vel[2]);
				// Normalize by frame dt (io->dt is μs, interval=15000μs per physics step).
				// This runs once per frame outside the fixed-timestep loop, so without
				// normalization, animation pace scales with framerate.
				float dt_scale = io->dt / 15000.0f;
				if (dt_scale > 4.0f) dt_scale = 4.0f; // clamp for lag spikes
				phys->player_stp = (~(1 << 31))&(phys->player_stp + (int)(v * 64 * 2 * dt_scale));
			}
			else
			if (!io->x_force && !io->y_force)
			{
				phys->player_stp = -1;
			}
		}

		/*
		// what was this for?
		for (int h = 63; h > 0; h--)
		{
			io->xyz[h][0] = io->xyz[h - 1][0];
			io->xyz[h][1] = io->xyz[h - 1][1];
			io->xyz[h][2] = io->xyz[h - 1][2];
		}

		io->xyz[0][0] = phys->pos[0];
		io->xyz[0][1] = phys->pos[1];
		io->xyz[0][2] = phys->pos[2];
		*/
	}

	// Render interpolation helps remote/non-local actors, but for the locally
	// controlled player it introduces sample drag: instantaneous physics heading
	// is correct while the displayed/sampled displacement lags behind by one
	// fixed-step blend window. Preserve direct local control by rendering the
	// local player at the current physics position.
	if (me)
	{
		io->pos[0] = phys->pos[0];
		io->pos[1] = phys->pos[1];
		io->pos[2] = phys->pos[2];
	}
	else if (steps_handled > 0)
	{
		float remaining = (float)(stamp - phys->stamp);
		float alpha = remaining / (float)interval;
		if (alpha < 0.0f) alpha = 0.0f;
		if (alpha > 1.0f) alpha = 1.0f;
		io->pos[0] = prev_pos[0] + (phys->pos[0] - prev_pos[0]) * alpha;
		io->pos[1] = prev_pos[1] + (phys->pos[1] - prev_pos[1]) * alpha;
		io->pos[2] = prev_pos[2] + (phys->pos[2] - prev_pos[2]) * alpha;
	}
	else
	{
		// No steps this frame — use current physics position (unchanged)
		io->pos[0] = phys->pos[0];
		io->pos[1] = phys->pos[1];
		io->pos[2] = phys->pos[2];
	}

	io->yaw = phys->yaw;
	io->player_dir = phys->player_dir;
	io->player_stp = phys->player_stp;

	return steps_handled;

	// OLD POS
	// after updating x,y,z by time and keyb bits
	// we need to fix z so player doesn't penetrate terrain
	/*
	{
		double p[3] = { phys->pos[0],phys->pos[1],-1 };
		double v[3] = { 0,0,-1 };
		double r[4] = { 0,0,0,1 };
		double n[3];
		Patch* patch = HitTerrain(terrain, p, v, r, n);

		double r2[4] = { 0,0,0,1 };
		double n2[4];

		{
			// it's almost ok, but need to exclude all meshes hanging above player
			// so initialize p[2] to plyer's center.z (instead of -1)
			double height_cells = 8.0;

			// 2/3 = 1.0/(zoom*sin30)
			double world_height = height_cells * 2/3 / cos(30 * M_PI / 180) * HEIGHT_SCALE;
			p[2] = phys->pos[2] + world_height*0.5;
		}

		Inst* inst = HitWorld(world, p, v, r2, n2, true); // true = positive only

		if (inst && (!patch || patch && r2[2] > r[2]))
		{
			n[0] = n2[0];
			n[1] = n2[1];
			n[2] = n2[2];
			r[2] = r2[2];
			patch = 0;
		}

		if (inst || patch)
		{
			if (!patch && r[2] - phys->pos[2] > 48) // do pasa
			{
				// nie pozwalamy na wslizg
				phys->pos[0] = push[0];
				phys->pos[1] = push[1];
				phys->pos[2] = push[2];
				phys->vel[0] = 0;
				phys->vel[1] = 0;
				phys->vel[2] = 0;
				return;
			}


			// we need contact to jump
			if (phys->IsKeyDown(A3D_SPACE) && r[2] - phys->pos[2] > -16)
				phys->vel[2] = 10;
			else
				phys->keys[A3D_SPACE >> 3] &= ~(1 << (A3D_SPACE & 7));

			if (r[2] >= phys->pos[2])
			{
				double n_len = sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
				phys->player_slope = min(0.1,n[2] / n_len);
				phys->pos[2] = r[2];
				if (phys->vel[2]<0)
					phys->vel[2] = 0;
			}
			else
				phys->player_slope = 0.1;
		}
		else
		{
			// we need contact to jump
			if (phys->IsKeyDown(A3D_SPACE) && phys->pos[2] < 16)
				phys->vel[2] = 10;
			else
				phys->keys[A3D_SPACE >> 3] &= ~(1 << (A3D_SPACE & 7));

			phys->player_slope = 0.1;
			if (phys->pos[2] < 0)
				phys->pos[2] = 0;
		}
	}
	*/
}

// WHY CreatePhysics: Allocates and initializes physics state for a new character
// (player, NPC, mount). Binds physics to terrain and world geometry sources.
// Called once per character at spawn time (game.cpp OnRender, NPC creation).
//
// INPUT:
// - t: Terrain* for heightfield collision queries
// - w: World* for BSP tree mesh collision queries
// - pos[3]: Initial world position (may be adjusted to safe spawn point)
// - dir: Initial facing direction in degrees (for animation)
// - yaw: Initial camera/character yaw angle in degrees
// - stamp: Current game timestamp in microseconds (for physics timing)
//
// OUTPUT:


// WHY CreatePhysics: Allocates and initializes physics state for a new character
// (player, NPC, mount). Binds physics to terrain and world geometry sources.
// Called once per character at spawn time (game.cpp OnRender, NPC creation).
//
// INPUT:
// - t: Terrain* for heightfield collision queries
// - w: World* for BSP tree mesh collision queries
// - pos[3]: Initial world position (may be adjusted to safe spawn point)
// - dir: Initial facing direction in degrees (for animation)
// - yaw: Initial camera/character yaw angle in degrees
// - stamp: Current game timestamp in microseconds (for physics timing)
//
// OUTPUT:
// - Physics* opaque handle (game.cpp stores this, passes to Animate each frame)
//
// WHY safe-lift flag: Generic callers can opt into the historical terrain +200
// lift, but server player bootstrap already resolves authoritative spawn Z and
// must preserve it exactly (FL-642).
Physics* CreatePhysics(Terrain* t, World* w, float pos[3], float dir, float yaw, uint64_t stamp, uint32_t create_flags)
{
    Physics* phys = (Physics*)malloc(sizeof(Physics));

	phys->stamp = stamp;

	phys->mat = 3; // Default to grass material (for footstep sounds before first collision)

    phys->terrain = t;
    phys->world = w;

	// Triangle soup buffer (dynamically resized during geometry queries)
	phys->soup = 0;
	phys->soup_alloc = 0;
	phys->soup_items = 0;

	phys->yaw = yaw;
	phys->yaw_vel = 0; // Angular velocity (rotations per frame)

	phys->pos[0] = pos[0];
	phys->pos[1] = pos[1];
	phys->pos[2] = pos[2];
	phys->vel[0] = 0; // Spawn with zero velocity (character starts at rest)
	phys->vel[1] = 0;
	phys->vel[2] = 0;

	// FL-2957 H-P4: bootstrap deadlock fix. When spawning at a server-resolved
	// terrain position (no TERRAIN_SAFE_LIFT flag), the player is already ON
	// the ground — start with accum_contact = 1.0 so GetPhysicsGrounded()
	// returns true immediately. Without this, accum_contact starts at 0,
	// support search (+0.75 offset in MpStepSupportHeightAt) misses terrain
	// at spawn height, and accum_contact never reaches 1.0 — a permanent
	// bootstrap deadlock that blocks the idle fast path forever.
	// TERRAIN_SAFE_LIFT spawns 200 units above terrain and legitimately start airborne.
	// POST-MORTEM 2026-05-05: this fix is INSUFFICIENT at spawn (-2.8,-73.6).
	// accum_contact starts at 1.0f but decays via *0.9 per tick (kMpGroundContactDecay
	// in mp_step.cpp). The collision sweep at this position produces minimal
	// contact_normal_z (soup-coverage hole), so accum_contact drops ~0.9 each tick
	// without replenishment. By tick ~10, accum_contact ≈ 0.0 regardless of bootstrap.
	// The structural fix must be at the soup-coverage or contact_normal_z floor, not
	// the bootstrap value. See mp_step.cpp:MpStepSupportHeightAt commentary.
	// LINEAGE_JSON: {"fl":"FL-2957","hypothesis":"H-P4","source_fix":"insufficient","root_cause":"soup_coverage_hole_at_spawn_(-2.8,-73.6)","patch_seam":"mp_step.cpp:contact_normal_z floor or support retry"}
	phys->accum_contact = (create_flags & PHYSICS_CREATE_TERRAIN_SAFE_LIFT) ? 0.0f : 1.0f;

		// WHY safe spawn position check: Prevent spawning inside terrain or falling through world
		// for generic callers that explicitly request the historical terrain-safe lift.
		// FL-642 / FL-394: this +200 path is legacy compatibility only. It is NOT the
		// authoritative multiplayer spawn fix; server bootstrap must resolve terrain Z
		// first and avoid reviving world-height / SetPhysicsPos compensation families.
		if (create_flags & PHYSICS_CREATE_TERRAIN_SAFE_LIFT)
	{
		double p[3] = { phys->pos[0],phys->pos[1],-1 }; // Ray origin (spawn XY, very low Z)
		double v[3] = { 0,0,-1 }; // Ray direction (downward)
		double r[4] = { 0,0,0,1 }; // Ray hit result (XYZ + t)
		double n[3]; // Hit normal (unused)
		Patch* patch = HitTerrain(phys->terrain, p, v, r, n);

		if (patch)
			phys->pos[2] = (float)r[2] + 200; // Spawn 200 units above terrain
	}

	phys->slope = 0; // Current ground slope (for animation blending)
	phys->player_dir = dir; // Facing direction (for sprite selection)
	phys->player_stp = -1; // Animation step counter (-1 = idle, >=0 = walking)

    return phys;
}

// WHY DeletePhysics: Frees physics state and triangle soup buffer when character
// is removed from world (death, despawn, level transition). Must be called to
// prevent memory leaks (soup buffer can grow to several KB for complex scenes).
void DeletePhysics(Physics* phys)
{
    if (phys->soup)
        free(phys->soup); // Free dynamically allocated triangle soup
    free(phys);
}

// WHY SetPhysicsPos: Teleport character to new position/velocity without physics
// integration. Used for:
// - Respawn (teleport to spawn point after death)
// - Cutscenes (move character to scripted position)
// - Editor mode (place character at cursor)
// - Network sync (correct client position to match server)
//
// WARNING: This bypasses collision detection. Character may end up inside geometry.
// TODO: Add collision resolution to find nearest safe position (project out of geometry).
//
// OWNERSHIP: Routes through SavePhysicsState/RestorePhysicsState so that
// RestorePhysicsState remains the single writer for all Physics* fields.
// On teleport, accum_contact and slope are reset to avoid stale grounded/lean
// state from the pre-teleport position carrying into the next physics step.
void SetPhysicsPos(Physics* phys, float pos[3], float vel[3])
{
	// TODO: should be safe (resolve collisions)
	// Current implementation: Teleport immediately, may embed in geometry.
	// Desired implementation: Raycast to find safe spawn point, or push out of collisions.

	if (!phys)
		return;

	PhysicsFullState state = {};
	SavePhysicsState(phys, &state);

	if (pos)
	{
		state.pos[0] = pos[0];
		state.pos[1] = pos[1];
		state.pos[2] = pos[2];
	}

	if (vel)
	{
		state.vel[0] = vel[0];
		state.vel[1] = vel[1];
		state.vel[2] = vel[2];
	}

	// Reset motion-derived state that is no longer valid after a position jump.
	// Leaving these stale would carry the pre-teleport grounded/slope context into
	// the next physics step, which can cause the character to treat mid-air
	// teleport destinations as grounded landings.
	state.accum_contact = 0.0f;
	state.slope = 0.0f;

	RestorePhysicsState(phys, &state);
}

// WHY SetPhysicsYaw: Set camera/character yaw directly, bypassing yaw velocity
// integration. Routes through Save/Restore so RestorePhysicsState remains the single
// writer for all Physics* fields (matching the SetPhysicsPos ownership model).
// Used for:
// - Snap to specific angle (cutscenes, scripted events)
// - Network sync (correct client yaw to match server)
// - Editor mode (orient character to match camera)
void SetPhysicsYaw(Physics* phys, float yaw, float vel)
{
	if (!phys)
		return;
	PhysicsFullState state = {};
	SavePhysicsState(phys, &state);
	state.yaw = yaw;
	state.yaw_vel = vel;
	RestorePhysicsState(phys, &state);
}

// WHY SetPhysicsDir: Set player facing direction for animation frame selection.
// Routes through Save/Restore so RestorePhysicsState remains the single writer.
// This is separate from yaw (camera angle) because character can face one direction
// while camera looks another (e.g., strafing).
void SetPhysicsDir(Physics* phys, float dir)
{
	if (!phys)
		return;
	PhysicsFullState state = {};
	SavePhysicsState(phys, &state);
	state.player_dir = dir;
	RestorePhysicsState(phys, &state);
}

void SyncPhysicsStamp(Physics* phys, uint64_t stamp)
{
	phys->stamp = stamp;
}

void GetPhysicsVel(Physics* phys, float vel[3])
{
	if (!phys || !vel)
		return;
	vel[0] = phys->vel[0];
	vel[1] = phys->vel[1];
	vel[2] = phys->vel[2];
}

void GetPhysicsPos(Physics* phys, float out[3])
{
	if (!phys || !out)
	{
		if (out) { out[0] = out[1] = out[2] = 0.0f; }
		return;
	}
	out[0] = phys->pos[0];
	out[1] = phys->pos[1];
	out[2] = phys->pos[2];
}

void GetPhysicsSlope(Physics* phys, float* out)
{
	if (!out)
		return;
	*out = phys ? phys->slope : 0.0f;
}

void GetPhysicsAccumContact(Physics* phys, float* out)
{
	if (!out)
		return;
	*out = phys ? phys->accum_contact : 0.0f;
}

void SavePhysicsState(Physics* phys, PhysicsFullState* state)
{
	if (!phys || !state)
		return;
	state->stamp = phys->stamp;
	state->mat = phys->mat;
	state->water = phys->water;
	state->pos[0] = phys->pos[0];
	state->pos[1] = phys->pos[1];
	state->pos[2] = phys->pos[2];
	state->vel[0] = phys->vel[0];
	state->vel[1] = phys->vel[1];
	state->vel[2] = phys->vel[2];
	state->player_dir = phys->player_dir;
	state->player_stp = phys->player_stp;
	state->yaw = phys->yaw;
	state->yaw_vel = phys->yaw_vel;
	state->slope = phys->slope;
	state->accum_contact = phys->accum_contact;
}

void RestorePhysicsState(Physics* phys, const PhysicsFullState* state)
{
	if (!phys || !state)
		return;
	phys->stamp = state->stamp;
	phys->mat = state->mat;
	phys->water = state->water;
	phys->pos[0] = state->pos[0];
	phys->pos[1] = state->pos[1];
	phys->pos[2] = state->pos[2];
	phys->vel[0] = state->vel[0];
	phys->vel[1] = state->vel[1];
	phys->vel[2] = state->vel[2];
	phys->player_dir = state->player_dir;
	phys->player_stp = state->player_stp;
	phys->yaw = state->yaw;
	phys->yaw_vel = state->yaw_vel;
	phys->slope = state->slope;
	phys->accum_contact = state->accum_contact;
}

bool GetPhysicsGrounded(Physics* phys)
{
	if (!phys)
		return false;
	return phys->accum_contact >= 1.0f;
}

int GetPhysicsDebugZeroed(Physics* phys)
{
	if (!phys)
		return 0;
	return phys->dbg_last_zeroed_after_sweep;
}

int GetPhysicsDebugZeroMask(Physics* phys)
{
	if (!phys)
		return 0;
	return phys->dbg_last_zero_reason_mask;
}

float GetPhysicsDebugContactNormalZ(Physics* phys)
{
	if (!phys)
		return 0.0f;
	return phys->dbg_last_contact_normal_z;
}

int GetPhysicsDebugAutoJump(Physics* phys)
{
	if (!phys)
		return 0;
	return phys->dbg_last_auto_jump;
}

int GetPhysicsDebugIx(Physics* phys)
{
	if (!phys)
		return 0;
	return phys->dbg_last_ix;
}

int GetPhysicsDebugIy(Physics* phys)
{
	if (!phys)
		return 0;
	return phys->dbg_last_iy;
}

float GetPhysicsDebugInputLen(Physics* phys)
{
	if (!phys)
		return 0.0f;
	return phys->dbg_last_xy_len;
}

void GetPhysicsDebugMoveWorld(Physics* phys, float move[2])
{
	if (!phys || !move)
		return;
	move[0] = phys->dbg_last_move_dx_world;
	move[1] = phys->dbg_last_move_dy_world;
}

void GetPhysicsDebugPreVel(Physics* phys, float vel[3])
{
	if (!phys || !vel)
		return;
	vel[0] = phys->dbg_last_pre_sweep_vel[0];
	vel[1] = phys->dbg_last_pre_sweep_vel[1];
	vel[2] = phys->dbg_last_pre_sweep_vel[2];
}

void GetPhysicsDebugPostVel(Physics* phys, float vel[3])
{
	if (!phys || !vel)
		return;
	vel[0] = phys->dbg_last_post_sweep_vel[0];
	vel[1] = phys->dbg_last_post_sweep_vel[1];
	vel[2] = phys->dbg_last_post_sweep_vel[2];
}

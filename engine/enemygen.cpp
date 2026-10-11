// ==============================================================================
// ENEMYGEN.CPP - Enemy Spawn Point System
// ==============================================================================
//
// PURPOSE:
//   Manages enemy spawn points (EnemyGen) placed in the world by level designers.
//   Each spawn point defines a location, population parameters, and equipment
//   probability distributions for NPCs that spawn from it. Spawn points are
//   stored in .a3d level files and loaded into a global doubly-linked list.
//
// SPAWN LIFECYCLE:
//   1. LoadA3D() (world.cpp) calls LoadEnemyGens() to read spawn points from binary file
//   2. InitGame() (game.cpp) iterates enemygen_head linked list
//   3. For each spawn point, creates up to alive_max NPCs with randomized equipment
//   4. When NPC dies, revive timer starts (2^(revive_min to revive_max) seconds)
//   5. SaveA3D() (world.cpp) calls SaveEnemyGens() to persist spawn point data
//
// EQUIPMENT PROBABILITY SYSTEM:
//   Armor, helmet, and shield use a 0-10 scale with independent probability rolls:
//     - Value 0  = 0% chance   (fast_rand() % 11 < 0  is never true)
//     - Value 5  = 45% chance  (fast_rand() % 11 < 5  returns 0-4, 5 outcomes)
//     - Value 10 = 90% chance  (fast_rand() % 11 < 10 returns 0-9, 10 outcomes)
//   WHY 11 outcomes: fast_rand() % 11 produces 0-10 inclusive (11 values), giving
//   clean 9% increments per point (0%, 9%, 18%, ..., 90%). Using % 10 would only
//   allow 0-90% in 10% steps; % 11 gives finer granularity.
//
//   Weapon choice uses WEIGHTED selection (not independent probability):
//     - sword and crossbow are weights, summed together
//     - fast_rand() % (sword + crossbow + 1) < sword chooses SWORD, else CROSSBOW
//     - Example: sword=7, crossbow=3 → 70% sword, 30% crossbow
//     - The +1 prevents division-by-zero when both are 0
//
// REVIVE TIMER ALGORITHM:
//   revive_min and revive_max are EXPONENTS for 2^n seconds (not direct seconds):
//     - revive_min=5, revive_max=8 → random 2^5 to 2^8 = 32 to 256 seconds
//     - revive_min=0, revive_max=10 → random 2^0 to 2^10 = 1 to 1024 seconds
//   WHY exponents: Provides exponential distribution for respawn times, allowing
//   both short (2-4 sec) and very long (10+ min) delays with compact storage.
//
// LINKED LIST MANAGEMENT:
//   Global doubly-linked list with head/tail pointers:
//     - Insert-at-head for O(1) insertion (order doesn't matter for spawn points)
//     - Doubly-linked for O(1) deletion from middle (editor use)
//     - Iterate head→next for traversal
//     - Free all nodes before world reload to prevent memory leaks
//
// DATA STRUCTURE:
//   struct EnemyGen (enemygen.h):
//     - next/prev:       Doubly-linked list pointers
//     - pos[3]:          World position XYZ (float) where NPCs spawn
//     - alive_max:       Max simultaneous NPCs from this spawn point (1-7)
//     - revive_min/max:  Exponents for 2^n second respawn timer
//     - armor/helmet/shield: Independent probabilities (0-10 scale)
//     - sword/crossbow:  Weapon weights for weighted random choice
//
// KEY FUNCTIONS:
//   LoadEnemyGens(FILE* f)  - Read spawn points from .a3d binary format
//   SaveEnemyGens(FILE* f)  - Write spawn points to .a3d binary format
//   FreeEnemyGens()         - Deallocate entire linked list (world cleanup)
//   HitEnemyGen()           - EDITOR: Raycast selection of spawn points in 3D view
//   DeleteEnemyGen()        - EDITOR: Remove spawn point from linked list
//
// INTEGRATION POINTS:
//   - game.cpp InitGame():    Iterates enemygen_head to spawn NPCs with equipment
//   - world.cpp LoadA3D():    Calls LoadEnemyGens() to load spawn point data
//   - world.cpp SaveA3D():    Calls SaveEnemyGens() to persist spawn point data
//   - sprite.h HitSprite():   Used by HitEnemyGen() for editor raycast testing
//
// SPAWN FLOW DIAGRAM:
//   .a3d file → LoadEnemyGens() → enemygen_head → InitGame() → NPC creation
//                                       ↓
//                                  linked list
//                                  eg→next→next
//                                       ↓
//                                SaveEnemyGens() → .a3d file
//
// KEY FILES:
//   enemygen.h    - EnemyGen struct definition, function prototypes
//   game.cpp      - InitGame() reads spawn points, creates NPCs with equipment
//   world.cpp     - LoadA3D/SaveA3D orchestrate spawn point persistence
//   sprite.h      - HitSprite() used for editor raycast selection
//
// ==============================================================================

#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include "enemygen.h"
#include "sprite.h"
#include "terrain.h"
#include "world_internal.h"

// [FLOW:ENTITY] Spawn point registry — global doubly-linked list of all EnemyGen spawn points.
// WHY globals: Single authoritative list accessed by game logic (NPC spawn), editor (selection),
// and persistence layer (save/load). Head/tail pointers enable O(1) insert-at-head and O(n) traversal.
EnemyGen* enemygen_head = 0;
EnemyGen* enemygen_tail = 0;

#ifdef EDITOR
extern Sprite* enemygen_sprite;

// [FLOW:ENTITY] Editor spawn point selection — raycast test against spawn point sprites.
// WHY: Level designers need to select spawn points in the 3D editor view to adjust
// equipment probabilities, population limits, and revive timers. This performs a raycast
// test against each spawn point's visual representation (enemygen_sprite) to find the
// closest hit along the ray direction.
//
// ALGORITHM:
//   1. Iterate all spawn points in linked list (O(n) acceptable for editor use)
//   2. For each, test HitSprite() with spawn point position
//   3. Calculate dot product projection along ray to find closest hit
//   4. Return closest spawn point, or NULL if no hit
//
// WHY dot product: proj = v·(r-p) measures distance along ray direction v.
// Smaller proj (more negative) = closer to ray origin = better hit.
EnemyGen* HitEnemyGen(double* p, double* v)
{
	double proj = 0;
	EnemyGen* best = 0;

	int anim = 0;
	int frame = 0;
	float yaw = 0;
	Sprite* sprite = enemygen_sprite;

	EnemyGen* eg = enemygen_head;
	while (eg)
	{
		double r[3];
		bool hit = HitSprite(sprite, anim, frame, eg->pos, yaw, p, v, r, false);
		if (hit)
		{
			// Calculate projection along ray to find closest hit
			double pr = v[0]*(r[0]-p[0]) + v[1]*(r[1]-p[1]) + v[2]*(r[2]-p[2]);
			if (pr < proj || !best)
			{
				proj = pr;
				best = eg;
			}
		}
		eg = eg->next;
	}

	if (best)
		printf("EG-HIT\n");

	return best;
}

// [FLOW:ENTITY] Editor spawn point deletion — unlink from doubly-linked list.
// WHY doubly-linked: O(1) removal from middle of list without traversing from head.
// Standard prev/next pointer update handles all cases (head, tail, middle).
//
// EDGE CASES:
//   - If eg is head (eg->prev == NULL): Update enemygen_head to next node
//   - If eg is tail (eg->next == NULL): Update enemygen_tail to prev node
//   - If eg is middle: Update prev->next and next->prev to skip this node
//
// Unlink and free an EnemyGen node from the global linked list.
void DeleteEnemyGen(EnemyGen* eg)
{
	if (eg)
	{
		// Unlink from previous node (or update head if this is first node)
		if (eg->prev)
			eg->prev->next = eg->next;
		else
			enemygen_head = eg->next;

		// Unlink from next node (or update tail if this is last node)
		if (eg->next)
			eg->next->prev = eg->prev;
		else
			enemygen_tail = eg->prev;

		free(eg);
	}
}
#endif

// [FLOW:ENTITY] Spawn points cleanup — deallocate entire linked list.
// WHY: Called before loading a new world to prevent memory leaks and stale spawn points
// from previous level. Walk-and-free pattern is standard for linked list cleanup.
//
// SAFETY: Resets head/tail to NULL to prevent dangling pointers after deallocation.
void FreeEnemyGens()
{
	EnemyGen* eg = enemygen_head;
	while (eg)
	{
		EnemyGen* n = eg->next;
		free(eg);
		eg = n;
	}

	enemygen_head = 0;
	enemygen_tail = 0;
}

// [FLOW:ENTITY] Spawn points load — binary format from .a3d file.
// WHY FreeEnemyGens first: Clear stale data from previous world before loading new spawn points.
//
// [DATA-CONTRACT:A3D] EnemyGen binary structure (44 bytes per spawn point):
//   Offset 0:  pos[3]       — 12 bytes (3× float32) — world position XYZ
//   Offset 12: alive_max    — 4 bytes (int32) — max simultaneous NPCs (1-7)
//   Offset 16: revive_min   — 4 bytes (int32) — min revive exponent (2^n seconds)
//   Offset 20: revive_max   — 4 bytes (int32) — max revive exponent (2^n seconds)
//   Offset 24: armor        — 4 bytes (int32) — armor probability (0-10)
//   Offset 28: helmet       — 4 bytes (int32) — helmet probability (0-10)
//   Offset 32: shield       — 4 bytes (int32) — shield probability (0-10)
//   Offset 36: sword        — 4 bytes (int32) — sword weight for weapon choice
//   Offset 40: crossbow     — 4 bytes (int32) — crossbow weight for weapon choice
//
// INSERTION STRATEGY:
//   WHY insert-at-head: O(1) insertion, spawn point order doesn't matter for game logic.
//   New spawn points become new head, with old head as next node.
//
// The first loaded node owns both head and tail. Later nodes prepend at head.
void LoadEnemyGens(FILE* f)
{
	FreeEnemyGens();

	int num = 0;

	if (fread(&num, 4, 1, f) != 1)
		return;

	for (int i = 0; i < num; i++)
	{
		// [FLOW:ENTITY] Spawn point allocation — create and link new node
		EnemyGen* eg = (EnemyGen*)malloc(sizeof(EnemyGen));

		if (3 != fread(eg->pos,        sizeof(float), 3, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->alive_max, sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->revive_min,sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->revive_max,sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->armor,     sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->helmet,    sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->shield,    sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->sword,     sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }
		if (1 != fread(&eg->crossbow,  sizeof(int),  1, f)) { free(eg); FreeEnemyGens(); return; }

		// Insert at head of linked list (O(1) operation)
		eg->prev = 0;
		eg->next = enemygen_head;

		if (enemygen_head)
			enemygen_head->prev = eg;
		else
			enemygen_tail = eg;

		enemygen_head = eg;
	}
}

// [FLOW:ENTITY] Spawn points save — binary format to .a3d file.
// WHY count first: Binary format needs count prefix so LoadEnemyGens knows how many
// spawn points to read. Two-pass algorithm: (1) count nodes, (2) write data.
//
// [DATA-CONTRACT:A3D] Write order matches LoadEnemyGens read order exactly (44 bytes per node).
bool SaveEnemyGens(FILE* f)
{
	if (!f)
		return false;
	// First pass: count spawn points in linked list
	int num = 0;
	EnemyGen* eg = enemygen_head;
	while (eg)
	{
		num++;
		eg = eg->next;
	}

	// Write count prefix
	fwrite(&num, 4, 1, f);

	// Second pass: write each spawn point's data
	eg = enemygen_head;
	while (eg)
	{
		fwrite(eg->pos, sizeof(float), 3, f);
		fwrite(&eg->alive_max, sizeof(int), 1, f);
		fwrite(&eg->revive_min, sizeof(int), 1, f);
		fwrite(&eg->revive_max, sizeof(int), 1, f);
		fwrite(&eg->armor, sizeof(int), 1, f);
		fwrite(&eg->helmet, sizeof(int), 1, f);
		fwrite(&eg->shield, sizeof(int), 1, f);
		fwrite(&eg->sword, sizeof(int), 1, f);
		fwrite(&eg->crossbow, sizeof(int), 1, f);
		eg = eg->next;
	}
	return !ferror(f);
}

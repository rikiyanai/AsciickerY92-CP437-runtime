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
#include <string.h> // memset in LoadEnemyGenList (FL-4588 codec core)
#include <math.h>   // isfinite: NaN/Inf spawn positions are rejected (FL-4588)
#include "a3d_wire.h" // explicit little-endian wire primitives (FL-4588)
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
// FL4588-ENEMY-CANVAS-CUTOVER: DeleteEnemyGen() DELETED. It unlinked AND freed a
// spawn point immediately, OUTSIDE Undo/Redo history. Editor deletion now goes
// through the reversible URDO_DeleteEnemyGen attach command (editor/urdo.cpp),
// which detaches without freeing and frees only when purged from history while
// detached. Durable list teardown still uses FreeEnemyGens/ReplaceEnemyGenList.
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

void ReplaceEnemyGenList(EnemyGen* head, EnemyGen* tail)
{
	FreeEnemyGens();
	enemygen_head = head;
	enemygen_tail = tail;
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
// FL-4588 single-parser rule (operator review 2026-07-16): this global
// loader no longer owns a parsing body. It measures the remaining stream,
// parses through the ONE hardened core (LoadEnemyGenList) into a temporary
// list, and swaps the global registry only after the whole section
// validated. Older .a3d files that END before this section leave the
// registry empty; so does any parse failure — partial state never
// survives. NOTE: the legacy body inserted records at HEAD, presenting the
// section reversed; the core preserves file order, which is required for
// byte-stable round trips (FL-4588).
void LoadEnemyGens(FILE* f)
{
	if (!f)
		return;

	// Measure the bytes remaining in the stream so the core can bound the
	// record count against physical reality. An unmeasurable stream is
	// refused rather than parsed unbounded.
	long pos = ftell(f);
	if (pos < 0 || fseek(f, 0, SEEK_END) != 0)
		return;
	long end = ftell(f);
	if (end < 0 || fseek(f, pos, SEEK_SET) != 0 || end < pos)
		return;

	// Legacy files may simply end before this section. Explicit omission
	// handling: an old document has no spawn points, so clear the registry
	// — this is a decision, not a parse failure.
	if (end == pos)
	{
		FreeEnemyGens();
		return;
	}

	// PARSE FIRST into a temporary list; the live registry is untouched
	// until the whole section validated, so malformed input never destroys
	// the document that was already loaded.
	EnemyGen* head = 0;
	EnemyGen* tail = 0;
	int count = 0;
	if (!LoadEnemyGenList(f, &head, &tail, &count, end - pos))
		return; // parse failed — the PREVIOUS registry survives untouched

	// Complete success: now (and only now) replace the live registry.
	FreeEnemyGens();
	enemygen_head = head;
	enemygen_tail = tail;
}

// [FLOW:ENTITY] Spawn points save — binary format to .a3d file.
// WHY count first: Binary format needs count prefix so LoadEnemyGens knows how many
// spawn points to read. Two-pass algorithm: (1) count nodes, (2) write data.
//
// [DATA-CONTRACT:A3D] Write order matches LoadEnemyGens read order exactly (44 bytes per node).
// FL-4588 single-serializer rule: the global save is a thin wrapper over
// the ONE list serializer (SaveEnemyGenList below), so temporary documents
// and the live registry can never drift onto different byte layouts.
bool SaveEnemyGens(FILE* f)
{
	return SaveEnemyGenList(f, enemygen_head);
}

// ===========================================================================
// FL-4588 document-codec cores (operator ruling 2026-07-16)
// ===========================================================================
// See enemygen.h for the full rationale. These parse/write CALLER-OWNED
// lists so a staged document validation can happen without ever touching
// the global spawn-point registry, and they fail closed: any structural
// problem frees everything parsed so far and reports false.

// Free a caller-owned list produced by LoadEnemyGenList. Safe on NULL.
void FreeEnemyGenList(EnemyGen* head)
{
	while (head)
	{
		EnemyGen* next = head->next;
		free(head);
		head = next;
	}
}

// The 44-byte record ABI below assumes 4-byte floats and 4-byte ints. If a
// platform ever violates this, the format code must be rewritten, not
// silently mis-sized — fail the build instead. Byte ORDER is not assumed at
// all: every multi-byte field goes through the explicit little-endian
// primitives in a3d_wire.h (operator portability ruling 2026-07-16), so the
// codec produces identical bytes on any host, including MSVC/Windows.
static_assert(sizeof(float) == 4, "A3D enemy-generator ABI requires 4-byte float");
static_assert(sizeof(int) == 4, "A3D enemy-generator ABI requires 4-byte int");

// Frozen accepted ranges for an uploaded document, derived from CORPUS
// EVIDENCE plus CONSUMING CODE (FL-4588 census of every tracked map,
// 2026-07-16) — not just the struct's slider documentation:
//   - alive_max: 1..7 (authoring slider; corpus max is 3);
//   - revive_min/max: 0..30 with min<=max. The server consumes these as
//     `1 << exponent` seconds (server_tick.cpp:5278), and a 32-bit shift
//     is only DEFINED through 30 — sandbox_20x20.a3d's legacy 1000..2000
//     values are genuinely unexecutable (undefined behavior at spawn)
//     and stay rejected until that asset is repaired;
//   - equipment probabilities and weapon weights: 0..15. Consumption is
//     `rand() % 11 < value` (saturates: 11+ means "always"), and the
//     tracked corpus contains authored 11s (game_map_y8.a3d), so the
//     documented 0..10 slider bound under-fits real documents. 15 covers
//     the corpus with headroom while still rejecting garbage ints.
// Arbitrary values outside these ranges are NOT a valid document.
static bool EnemyGenRecordValid(const EnemyGen* eg)
{
	for (int axis = 0; axis < 3; axis++)
		if (!isfinite(eg->pos[axis]))
			return false; // NaN/Inf position is never a valid spawn point
	if (eg->alive_max < 1 || eg->alive_max > 7) return false;
	if (eg->revive_min < 0 || eg->revive_min > 30) return false;
	if (eg->revive_max < 0 || eg->revive_max > 30) return false;
	// The revive timer draws a random exponent from [revive_min, revive_max];
	// an inverted interval is not an authorable document.
	if (eg->revive_min > eg->revive_max) return false;
	if (eg->armor < 0 || eg->armor > 15) return false;
	if (eg->helmet < 0 || eg->helmet > 15) return false;
	if (eg->shield < 0 || eg->shield > 15) return false;
	if (eg->sword < 0 || eg->sword > 15) return false;
	if (eg->crossbow < 0 || eg->crossbow > 15) return false;
	return true;
}

bool LoadEnemyGenList(FILE* f, EnemyGen** out_head, EnemyGen** out_tail,
                      int* out_count, long max_bytes)
{
	if (!f || !out_head || !out_tail || !out_count)
		return false;
	*out_head = 0;
	*out_tail = 0;
	*out_count = 0;

	// max_bytes is the stream budget for the WHOLE section INCLUDING the
	// 4-byte count prefix. Browser-uploaded documents require MEASURED
	// input (operator review 2026-07-16), so there is no unbounded mode: a
	// negative budget is caller error and a budget that cannot even hold
	// the prefix is malformed — both rejected before any read happens.
	// (Without this pre-check, integer division below — e.g. (3-4)/44 == 0
	// under C++ truncation — would accept num=0 for a section that
	// physically has no count at all.)
	if (max_bytes < 4)
		return false;

	// The section starts with a 4-byte signed record count, decoded
	// explicitly as little-endian (never host order).
	int32_t num = 0;
	if (!A3dReadI32LE(f, &num))
		return false;

	// Bound the count against physical reality: each record is exactly
	// 44 bytes (3 floats position + 8 ints), so a section with max_bytes
	// total cannot contain more than (max_bytes - 4) / 44 records. A
	// negative count is always malformed. This is what stops a hostile
	// 4-byte prefix from demanding gigabytes of allocations.
	if (num < 0)
		return false;
	if ((long)num > (max_bytes - 4) / 44)
		return false;

	EnemyGen* head = 0;
	EnemyGen* tail = 0;
	for (int i = 0; i < num; i++)
	{
		EnemyGen* eg = (EnemyGen*)malloc(sizeof(EnemyGen));
		if (!eg)
		{
			FreeEnemyGenList(head);
			return false;
		}
		// Zero everything first so the two link pointers (and any future
		// non-serialized fields) never carry stack/heap garbage.
		memset(eg, 0, sizeof(EnemyGen));

		// Read the 44-byte record in the exact serializer write order —
		// every field explicitly little-endian — then validate it against
		// the frozen authoring ranges. Both failures free everything
		// parsed so far — no partial state.
		if (!A3dReadF32LE(f, &eg->pos[0]) ||
		    !A3dReadF32LE(f, &eg->pos[1]) ||
		    !A3dReadF32LE(f, &eg->pos[2]) ||
		    !A3dReadI32LE(f, &eg->alive_max)  ||
		    !A3dReadI32LE(f, &eg->revive_min) ||
		    !A3dReadI32LE(f, &eg->revive_max) ||
		    !A3dReadI32LE(f, &eg->armor)      ||
		    !A3dReadI32LE(f, &eg->helmet)     ||
		    !A3dReadI32LE(f, &eg->shield)     ||
		    !A3dReadI32LE(f, &eg->sword)      ||
		    !A3dReadI32LE(f, &eg->crossbow)   ||
		    !EnemyGenRecordValid(eg))
		{
			free(eg);
			FreeEnemyGenList(head);
			return false;
		}

		// Append at TAIL so the list preserves file order. (The legacy
		// global loader inserted at head, which reversed the section on
		// every load->save cycle and broke byte-stable round trips.)
		eg->prev = tail;
		if (tail)
			tail->next = eg;
		else
			head = eg;
		tail = eg;
	}

	*out_head = head;
	*out_tail = tail;
	*out_count = num;
	return true;
}

bool SaveEnemyGenList(FILE* f, const EnemyGen* head)
{
	if (!f)
		return false;

	// Preflight pass: validate EVERY record against the same frozen ranges
	// the parser enforces, and count with overflow protection, BEFORE any
	// byte is emitted. The product codec must never emit bytes its own
	// parser would reject, and a late invalid node must not leave a
	// partial supposedly-canonical document already written to the stream.
	long num = 0;
	for (const EnemyGen* eg = head; eg; eg = eg->next)
	{
		if (!EnemyGenRecordValid(eg))
			return false;
		if (++num > 2147483647L)
			return false; // count would overflow the 4-byte signed prefix
	}
	if (!A3dWriteI32LE(f, (int32_t)num))
		return false;

	// Second pass: 44-byte records in list order (matches
	// LoadEnemyGenList), every field explicitly little-endian.
	for (const EnemyGen* eg = head; eg; eg = eg->next)
	{
		if (!A3dWriteF32LE(f, eg->pos[0]) ||
		    !A3dWriteF32LE(f, eg->pos[1]) ||
		    !A3dWriteF32LE(f, eg->pos[2]) ||
		    !A3dWriteI32LE(f, eg->alive_max)  ||
		    !A3dWriteI32LE(f, eg->revive_min) ||
		    !A3dWriteI32LE(f, eg->revive_max) ||
		    !A3dWriteI32LE(f, eg->armor)      ||
		    !A3dWriteI32LE(f, eg->helmet)     ||
		    !A3dWriteI32LE(f, eg->shield)     ||
		    !A3dWriteI32LE(f, eg->sword)      ||
		    !A3dWriteI32LE(f, eg->crossbow))
			return false;
	}
	return !ferror(f);
}

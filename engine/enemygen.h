#pragma once

// EnemyGen - Enemy spawn point structure
//
// PURPOSE: Defines a location in the world where NPCs spawn with randomized equipment.
// Level designers place spawn points in the editor; game logic reads these at InitGame()
// to create NPCs with equipment probabilities controlled by the fields below.
//
// LINKED LIST: Forms a global doubly-linked list via next/prev pointers, allowing O(1)
// insertion and deletion. See enemygen_head/enemygen_tail globals below.
struct EnemyGen
{
	EnemyGen* next;   // Next spawn point in global linked list (NULL if tail)
	EnemyGen* prev;   // Previous spawn point in global linked list (NULL if head)

	float pos[3];     // World position XYZ (float) where NPCs spawn

	// POPULATION PARAMETERS:
	int alive_max;    // Max simultaneous NPCs from this spawn point (range: 1-7)
	                  // Editor displays as slider. Higher values = more crowded spawn.

	// REVIVE TIMER (exponential backoff):
	int revive_min;   // Min revive EXPONENT (0-10): random 2^revive_min seconds
	int revive_max;   // Max revive EXPONENT (0-10): random 2^revive_max seconds
	                  // Example: revive_min=5, revive_max=8 → 32-256 second respawn
	                  // WHY exponents: Allows both short (2-4s) and long (10+ min) timers
	                  // with compact integer storage.

	// EQUIPMENT PROBABILITIES (independent rolls):
	// Each uses 0-10 scale mapped to probability via: fast_rand() % 11 < value
	//   0  = 0% chance  (never equipped)
	//   5  = 45% chance (fast_rand() % 11 returns 0-10, < 5 is 5 outcomes)
	//   10 = 90% chance (< 10 is 10 outcomes)
	// WHY 0-10 scale: fast_rand() % 11 gives 11 outcomes (0-10 inclusive), enabling
	// 9% increments. Using % 10 would only allow 10% steps (0%, 10%, ..., 90%).
	int armor;        // Armor probability (0-10): independently rolled for each NPC
	int helmet;       // Helmet probability (0-10): independently rolled for each NPC
	int shield;       // Shield probability (0-10): independently rolled for each NPC

	// WEAPON WEIGHTS (weighted random choice, NOT independent probabilities):
	// fast_rand() % (sword + crossbow + 1) < sword chooses SWORD, else CROSSBOW.
	// Example: sword=7, crossbow=3 → 70% sword, 30% crossbow.
	// WHY weights instead of probabilities: Allows flexible weapon distribution control
	// (e.g., "mostly swords" vs "equal mix" vs "rare crossbows").
	int sword;        // Sword weight for weapon choice (0-10)
	int crossbow;     // Crossbow weight for weapon choice (0-10)

	// FUTURE EXTENSION:
	// maybe add story_id
	// for generated enemies?
	// WHY: Would allow quest system to track "this specific enemy from spawn point X"
	// for objectives like "kill the bandit leader" (currently all enemies are anonymous).
};

// [FLOW:ENTITY] Spawn point registry — global doubly-linked list of all EnemyGen spawn points.
// WHY globals: Single authoritative list accessed by game logic (NPC spawn), editor (selection),
// and persistence layer (save/load). Initialized to NULL, populated by LoadEnemyGens().
extern EnemyGen* enemygen_head;  // First spawn point in linked list (NULL if empty)
extern EnemyGen* enemygen_tail;  // Last spawn point in linked list (NULL if empty)

// Lifecycle functions (called by world.cpp LoadA3D/SaveA3D):
void FreeEnemyGens();              // Deallocate entire linked list (before new world load)
void LoadEnemyGens(FILE* f);       // Read spawn points from .a3d binary format
bool SaveEnemyGens(FILE* f);       // Write spawn points to .a3d binary format

// Editor functions (3D view interaction):
#ifdef EDITOR
EnemyGen* HitEnemyGen(double* p, double* v);  // Raycast selection of spawn points
void DeleteEnemyGen(EnemyGen* eg);            // Unlink and free spawn point
#endif

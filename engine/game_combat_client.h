#pragma once

// game_combat_client.h — Combat client and verifier hook declarations
//
// PURPOSE:
// Houses blood-decal, combat, and verifier exports that were previously
// declared inline in game.h. Extracted as a leaf module.

struct Game;
struct Character;

bool ShouldWriteLocalSinglePlayerBloodDecals();
void BloodLeak(Character* c, int steps);

// Verifier hooks for scripted web multiplayer tests (pickup/use/combat scenario).
// Return >=0 on success (target/item id or status), <0 on failure.
int VerifierPickupAuthoritativeWorldItem(Game* g, int index);
int VerifierUseAuthoritativeItem(Game* g, int index);
int VerifierDropAuthoritativeItem(Game* g, int index);
int VerifierStartAttackNearest(Game* g, int target_kind);
int VerifierSetNearestNpcHp(Game* g, int hp);
int VerifierSetDebugDamage(Game* g, int enabled);
int VerifierSetCheckpointPosition(Game* g, float x, float y, float z);

#pragma once
#include <stdint.h>
#include "server/protocol/protocol_combat.h"

struct Server;
struct Game;
struct Terrain;
struct World;

// ---------------------------------------------------------------------------
// Floating damage numbers — data owned in game.cpp, written by ingest modules
// ---------------------------------------------------------------------------
static const int MAX_DAMAGE_FLOATERS = 8;
static const uint64_t FLOATER_LIFETIME = 1000000; // 1 second in microseconds
struct DamageFloater
{
    float pos[3];
    int damage;
    uint64_t spawn_stamp;
    bool active;
};
extern DamageFloater damage_floaters[MAX_DAMAGE_FLOATERS];
void SpawnDamageFloater(float x, float y, float z, int damage, uint64_t stamp);

static const int MAX_PROJECTILE_VISUALS = 16;
static const uint64_t PROJECTILE_VISUAL_LIFETIME_US = 450000; // moving projectile + short fade tail
struct ProjectileVisual
{
    float from[3];
    float to[3];
    uint64_t spawn_stamp;
    uint16_t item_definition_id;
    bool active;
};
extern ProjectileVisual projectile_visuals[MAX_PROJECTILE_VISUALS];
void SpawnProjectileVisual(uint16_t item_definition_id, const float from[3], const float to[3], uint64_t stamp);

// ---------------------------------------------------------------------------
// Packet family handlers — each is an independently auditable owner
// ---------------------------------------------------------------------------
bool ApplyJoinPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyExitPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyAppearancePacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplySnapshotPacket(Server* server, Game* game, Terrain* terrain, World* world,
    const uint8_t* ptr, int size);
bool ApplyItemPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyDecalPacket(Server* server, Game* game, Terrain* terrain, const uint8_t* ptr, int size);
bool ApplyCollisionDebugPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyCombatPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyChatPacket(Server* server, Game* game, const uint8_t* ptr, int size);
bool ApplyLagPacket(Server* server, Game* game, const uint8_t* ptr, int size);

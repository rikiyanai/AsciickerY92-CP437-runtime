#pragma once

#include <stdint.h>
#include <string.h>

static const int SERVER_WORLD_ENTITY_MAX = 1024;

enum ServerWorldEntityFlags : uint16_t
{
	SERVER_WORLD_ENTITY_NONE = 0,
	SERVER_WORLD_ENTITY_COLLIDABLE = 1u << 0,
	SERVER_WORLD_ENTITY_SUPPORT = 1u << 1,
	SERVER_WORLD_ENTITY_INTERACTABLE = 1u << 2,
	SERVER_WORLD_ENTITY_PLACEABLE = 1u << 3,
};

struct ServerWorldEntity
{
	bool active;
	uint64_t entity_id;
	uint16_t item_id;
	uint16_t catalog_id;
	uint16_t owner_id;
	uint16_t flags;
	float pos[3];
	float yaw;
	float collision_half_extent;
	float collision_height;
	uint8_t explicit_pickup;
	// Runtime world-mesh instance (engine Inst*) for the AKM cube that backs
	// this placed block. Owned by server_tick.cpp via CreateInst/DeleteInst.
	// void* keeps engine/world.h out of this header.
	void* mesh_inst;
};

struct ServerWorldEntityRegistry
{
	ServerWorldEntity entities[SERVER_WORLD_ENTITY_MAX];
	uint64_t next_entity_id;
};

static inline void ServerWorldEntityRegistryInit(ServerWorldEntityRegistry* registry)
{
	if (!registry)
		return;
	memset(registry, 0, sizeof(*registry));
	registry->next_entity_id = 1;
}

static inline ServerWorldEntity* ServerWorldEntityRegistryFindByItemId(
	ServerWorldEntityRegistry* registry,
	uint16_t item_id)
{
	if (!registry || item_id == 0)
		return 0;
	for (int i = 0; i < SERVER_WORLD_ENTITY_MAX; i++)
	{
		ServerWorldEntity* entity = &registry->entities[i];
		if (entity->active && entity->item_id == item_id)
			return entity;
	}
	return 0;
}

static inline const ServerWorldEntity* ServerWorldEntityRegistryFindByItemIdConst(
	const ServerWorldEntityRegistry* registry,
	uint16_t item_id)
{
	return ServerWorldEntityRegistryFindByItemId(
		(ServerWorldEntityRegistry*)registry, item_id);
}

static inline ServerWorldEntity* ServerWorldEntityRegistryUpsertPlacedBlock(
	ServerWorldEntityRegistry* registry,
	uint16_t item_id,
	uint16_t catalog_id,
	uint16_t owner_id,
	const float pos[3],
	float yaw,
	float half_extent,
	float height,
	bool explicit_pickup)
{
	if (!registry || item_id == 0 || !pos)
		return 0;
	ServerWorldEntity* entity =
		ServerWorldEntityRegistryFindByItemId(registry, item_id);
	if (!entity)
	{
		for (int i = 0; i < SERVER_WORLD_ENTITY_MAX; i++)
		{
			if (!registry->entities[i].active)
			{
				entity = &registry->entities[i];
				memset(entity, 0, sizeof(*entity));
				entity->active = true;
				if (registry->next_entity_id == 0)
					registry->next_entity_id = 1;
				entity->entity_id = registry->next_entity_id++;
				if (registry->next_entity_id == 0)
					registry->next_entity_id = 1;
				break;
			}
		}
	}
	if (!entity)
		return 0;

	entity->item_id = item_id;
	entity->catalog_id = catalog_id;
	entity->owner_id = owner_id;
	entity->flags =
		SERVER_WORLD_ENTITY_COLLIDABLE |
		SERVER_WORLD_ENTITY_SUPPORT |
		SERVER_WORLD_ENTITY_INTERACTABLE |
		SERVER_WORLD_ENTITY_PLACEABLE;
	memcpy(entity->pos, pos, sizeof(entity->pos));
	entity->yaw = yaw;
	entity->collision_half_extent = half_extent > 0.0f ? half_extent : 1.0f;
	entity->collision_height = height > 0.0f ? height : 2.0f;
	entity->explicit_pickup = explicit_pickup ? 1 : 0;
	return entity;
}

static inline bool ServerWorldEntityRegistryRemoveByItemId(
	ServerWorldEntityRegistry* registry,
	uint16_t item_id)
{
	ServerWorldEntity* entity =
		ServerWorldEntityRegistryFindByItemId(registry, item_id);
	if (!entity)
		return false;
	memset(entity, 0, sizeof(*entity));
	return true;
}

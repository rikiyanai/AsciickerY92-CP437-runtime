#include <stdint.h>
#include <string.h>
#include "network_ingest.h"
#include "game.h"
#include "remote_mounted_witness.h"
#include "remote_observer_probe.h"
#include "snapshot_client/snapshot_npc_repository.h"
#include "server/actor_visual_catalog_source.h"
#include "server/multiplayer_protocol.h"

void SpawnDamageFloater(float x, float y, float z, int damage, uint64_t stamp)
{
	int best = 0;
	uint64_t oldest = stamp;
	for (int i = 0; i < MAX_DAMAGE_FLOATERS; i++)
	{
		if (!damage_floaters[i].active)
		{
			best = i;
			break;
		}
		if (damage_floaters[i].spawn_stamp < oldest)
		{
			oldest = damage_floaters[i].spawn_stamp;
			best = i;
		}
	}
	damage_floaters[best].pos[0] = x;
	damage_floaters[best].pos[1] = y;
	damage_floaters[best].pos[2] = z;
	damage_floaters[best].damage = damage;
	damage_floaters[best].spawn_stamp = stamp;
	damage_floaters[best].active = true;
}

void SpawnProjectileVisual(uint16_t item_definition_id, const float from[3], const float to[3], uint64_t stamp)
{
	if (!from || !to)
		return;
	int best = 0;
	uint64_t oldest = stamp;
	for (int i = 0; i < MAX_PROJECTILE_VISUALS; i++)
	{
		if (!projectile_visuals[i].active)
		{
			best = i;
			break;
		}
		if (projectile_visuals[i].spawn_stamp < oldest)
		{
			oldest = projectile_visuals[i].spawn_stamp;
			best = i;
		}
	}
	memcpy(projectile_visuals[best].from, from, sizeof(projectile_visuals[best].from));
	memcpy(projectile_visuals[best].to, to, sizeof(projectile_visuals[best].to));
	projectile_visuals[best].spawn_stamp = stamp;
	projectile_visuals[best].item_definition_id = item_definition_id;
	projectile_visuals[best].active = true;
}

static void ApplyCombatSwingPacket(Server* server, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_SWING))
		return;
	STRUCT_BRC_SWING* swing = (STRUCT_BRC_SWING*)ptr;
	server->authority.combat_obs.swing_event_packets++;
	server->authority.combat_obs.last_swing_attacker_id = swing->attacker_id;
	server->authority.combat_obs.last_swing_target_id = swing->target_id;
	const AppearanceCatalogItemDef* item =
		FindAppearanceCatalogItemById(swing->weapon_item_id);
	if (item && item->spawns_projectile_on_swing)
		SpawnProjectileVisual(swing->weapon_item_id, swing->pos, swing->target_pos,
			server->connection.stamp);
}

static void ApplyCombatDamagePacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_DAMAGE))
		return;
	STRUCT_BRC_DAMAGE* dmg = (STRUCT_BRC_DAMAGE*)ptr;
	server->authority.combat_obs.damage_event_packets++;
	const bool attacker_is_npc = MultiplayerEntityIdIsNpc(dmg->attacker_id);
	const bool target_is_npc = MultiplayerEntityIdIsNpc(dmg->target_id);
	if (attacker_is_npc && target_is_npc)
		server->authority.combat_obs.damage_npc_to_npc_packets++;
	else if (attacker_is_npc)
		server->authority.combat_obs.damage_npc_to_player_packets++;
	else if (target_is_npc)
		server->authority.combat_obs.damage_player_to_npc_packets++;
	else
		server->authority.combat_obs.damage_player_to_player_packets++;
	server->authority.combat_obs.last_damage_target_id = dmg->target_id;
	server->authority.combat_obs.last_damage_attacker_id = dmg->attacker_id;
	server->authority.combat_obs.last_damage_amount = dmg->damage;
	server->authority.combat_obs.last_damage_new_hp = dmg->new_hp;

	if (game && server->connection.local_id >= 0 &&
		server->connection.local_id < server->connection.max_clients &&
		dmg->target_id == (uint16_t)server->connection.local_id)
	{
		const float* player_pos = game->player.pos;
		SpawnDamageFloater(player_pos[0], player_pos[1], player_pos[2],
			dmg->damage, server->connection.stamp);
	}
	else
	{
		if (dmg->target_id < server->connection.max_clients)
		{
			Human* target = server->authority.others + dmg->target_id;
			SpawnDamageFloater(target->pos[0], target->pos[1], target->pos[2],
				dmg->damage, server->connection.stamp);
		}
		else
		{
			ServerSnapshotNpcRepository::SnapshotNpcState* npc =
				FindSnapshotNpcStateByEntityId(&server->authority.npc_repo, dmg->target_id);
			if (npc)
				SpawnDamageFloater(npc->pos[0], npc->pos[1], npc->pos[2],
					dmg->damage, server->connection.stamp);
		}
	}
}

static void ApplyCombatDeathPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_DEATH))
		return;
	STRUCT_BRC_DEATH* death = (STRUCT_BRC_DEATH*)ptr;
	server->authority.combat_obs.death_event_packets++;
	server->authority.combat_obs.last_death_dead_id = death->dead_id;
	server->authority.combat_obs.last_death_killer_id = death->killer_id;

	if (death->dead_id < server->connection.max_clients)
	{
		const bool local_death =
			game && server->connection.local_id >= 0 &&
			server->connection.local_id < server->connection.max_clients &&
			death->dead_id == (uint16_t)server->connection.local_id;
		if (local_death)
		{
			Character& local = game->player;
			local.HP = 0;
			local.life_state = LIFE_STATE::DEAD;
			local.combat_state = COMBAT_STATE::NONE;
			if (local.presentation_kind_id != APPEARANCE_PRESENTATION_KIND_DEATH)
			{
				local.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_DEATH;
				const uint32_t snapshot_tick = server->authority.snapshot_client.last_snapshot_tick;
				local.presentation_started_tick =
					snapshot_tick ? snapshot_tick : (uint32_t)(game->stamp / 1000ull);
			}
		}
		else
		{
			Human* dead = server->authority.others + death->dead_id;
			bool was_dead = RemoteObserverHasDeathEpoch(dead);
			if (!was_dead)
			{
				RemoteMountedWitnessResetObserverDeathHistory(dead);
				RemoteObserverNoteDeathSeq(server, dead, (int)death->dead_id, 3);
			}
			dead->HP = 0;
			dead->life_state = LIFE_STATE::DEAD;
			dead->combat_state = COMBAT_STATE::NONE;
			if (dead->presentation_kind_id != APPEARANCE_PRESENTATION_KIND_DEATH)
			{
				dead->presentation_kind_id = APPEARANCE_PRESENTATION_KIND_DEATH;
				const uint32_t snapshot_tick = server->authority.snapshot_client.last_snapshot_tick;
				dead->presentation_started_tick =
					snapshot_tick ? snapshot_tick : (uint32_t)(game ? (game->stamp / 1000ull) : 0);
			}
		}
	}
}

static void ApplyCombatRespawnPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_RESPAWN))
		return;
	STRUCT_BRC_RESPAWN* resp = (STRUCT_BRC_RESPAWN*)ptr;
	if (resp->player_id >= server->connection.max_clients)
		return;
	server->authority.combat_obs.respawn_event_packets++;
	server->authority.combat_obs.last_respawn_player_id = resp->player_id;

	if (!(game && server->connection.local_id >= 0 &&
		server->connection.local_id < server->connection.max_clients &&
		resp->player_id == (uint16_t)server->connection.local_id))
	{
		Human* h = server->authority.others + resp->player_id;
		RemoteObserverNoteRespawnSeq(server, h, (int)resp->player_id);
	}
}

bool ApplyCombatPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	switch (ptr[0])
	{
		case 'h':
			ApplyCombatSwingPacket(server, ptr, size);
			return true;
		case 'd':
			ApplyCombatDamagePacket(server, game, ptr, size);
			return true;
		case 'k':
			ApplyCombatDeathPacket(server, game, ptr, size);
			return true;
		case 'r':
			ApplyCombatRespawnPacket(server, game, ptr, size);
			return true;
	}
	return false;
}

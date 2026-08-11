#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "network_ingest.h"
#include "game.h"
#include "authoritative_item_command_surface.h"
#include "authoritative_item_query_surface.h"
#include "authoritative_world_item_appearance.h"
#include "authoritative_world_item_pickup_strip.h"
#include "actor_visual_profile_runtime.h"
#include "a3d_load_context.h"
#include "server/multiplayer_protocol.h"

bool ApplyItemPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_ITEM_CHANGE_V2))
		return true;
	const STRUCT_BRC_ITEM_CHANGE_V2* itemv2 = (const STRUCT_BRC_ITEM_CHANGE_V2*)ptr;
	server->authority.auth_item.item_event_packets++;
	server->authority.auth_item.item_event_v2_packets++;
	server->authority.auth_item.last_item_event_id = itemv2->event_id;
	server->authority.auth_item.last_item_event_tick = itemv2->tick;
	server->authority.auth_item.last_item_definition_id_v2 = itemv2->item_definition_id;
	server->authority.auth_item.last_item_visual_style_id_v2 = itemv2->visual_style_id;
	if (itemv2->item_id == 0xffff)
		return true;

	int slot = -1;
	int free_slot = -1;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		AuthoritativeItemState* ai = server->authority.auth_item.items + i;
		if (ai->valid)
		{
			if (ai->item_id == itemv2->item_id)
			{
				slot = i;
				break;
			}
		}
		else if (free_slot < 0)
		{
			free_slot = i;
		}
	}
	if (slot < 0 && free_slot >= 0)
	{
		slot = free_slot;
		memset(server->authority.auth_item.items + slot, 0,
			sizeof(server->authority.auth_item.items[slot]));
		server->authority.auth_item.items[slot].valid = 1;
		server->authority.auth_item.items[slot].item_id = itemv2->item_id;
	}
	if (slot >= 0)
	{
		AuthoritativeItemState* ai = server->authority.auth_item.items + slot;
		const uint16_t previous_owner_id = ai->owner_id;
		const bool previously_local =
			ai->v2_valid && server->connection.local_id >= 0 &&
			previous_owner_id == (uint16_t)server->connection.local_id;
		ai->owner_id = itemv2->owner_id;
		ai->item_definition_id = itemv2->item_definition_id;
		ai->visual_style_id = itemv2->visual_style_id;
		ai->equip_slot_kind_id = itemv2->equip_slot_kind_id;
		ai->v2_state_flags = itemv2->state_flags;
		ai->last_kind = itemv2->kind;
		ai->v2_valid = 1;
		ai->pos[0] = itemv2->pos[0];
		ai->pos[1] = itemv2->pos[1];
		ai->pos[2] = itemv2->pos[2];
		ai->last_event_id = itemv2->event_id;
		ai->last_event_tick = itemv2->tick;
		if (server->connection.local_id >= 0 &&
			(itemv2->owner_id == (uint16_t)server->connection.local_id || previously_local))
		{
			if (game)
			{
				game->debug.dbg_auth_item_local_event_kind = itemv2->kind;
				game->debug.dbg_auth_item_local_event_item_id = itemv2->item_id;
				game->debug.dbg_auth_item_local_event_owner_id = itemv2->owner_id;
				game->debug.dbg_auth_item_local_event_sync_calls++;
			}
		}
		if (itemv2->kind == ITEM_CHANGE_KIND_CONSUME ||
			itemv2->kind == ITEM_CHANGE_KIND_REMOVE)
		{
			ai->valid = 0;
		}
		server->authority.auth_item.item_event_applied_packets++;
		server->authority.auth_item.state_apply_packets++;
		RebuildLocalAuthoritativeItemState(game);
		if (game)
			game->CancelItemContacts();
	}
	return true;
}

bool ApplyDecalPacket(Server* server, Game* game, Terrain* terrain_ctx, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_DECAL_ADD))
		return true;
	STRUCT_BRC_DECAL_ADD* decal = (STRUCT_BRC_DECAL_ADD*)ptr;
	server->authority.auth_item.decal_event_packets++;
	server->authority.auth_item.last_decal_event_id = decal->event_id;
	server->authority.auth_item.last_decal_event_tick = decal->tick;
	if (terrain_ctx && game && game->session.blood && decal->r > 0.0f && decal->matid < 255)
	{
		float xy[2] = { decal->x, decal->y };
		PaintTerrain(xy, decal->r, decal->matid);
		server->authority.auth_item.decal_event_applied_packets++;
	}
	return true;
}

bool ApplyCollisionDebugPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	(void)game;
	if (!server || !ptr || size != (int)sizeof(STRUCT_BRC_COLLISION_DEBUG))
		return true;
	const STRUCT_BRC_COLLISION_DEBUG* packet = (const STRUCT_BRC_COLLISION_DEBUG*)ptr;
	CollisionDebugClientState* out = &server->authority.collision_debug;
	if (!out->valid ||
		packet->chunk_index == 0 ||
		out->tick != packet->tick ||
		out->player_id != packet->player_id)
	{
		memset(out, 0, sizeof(*out));
	}
	out->valid = 1;
	out->player_id = packet->player_id;
	out->tick = packet->tick;
	out->support_source = packet->support_source;
	out->push_source = packet->push_source;
	out->support_item_id = packet->support_item_id;
	out->player_pos[0] = packet->player_pos[0];
	out->player_pos[1] = packet->player_pos[1];
	out->player_pos[2] = packet->player_pos[2];
	out->support_z = packet->support_z;
	uint16_t base = (uint16_t)(packet->chunk_index * COLLISION_DEBUG_PACKET_SAMPLE_MAX);
	uint16_t count = packet->count;
	if (base >= COLLISION_DEBUG_SAMPLE_MAX)
		count = 0;
	else if (base + count > COLLISION_DEBUG_SAMPLE_MAX)
		count = (uint16_t)(COLLISION_DEBUG_SAMPLE_MAX - base);
	if (count > 0)
		memcpy(out->samples + base, packet->samples, (size_t)count * sizeof(out->samples[0]));
	const uint16_t next_count = (uint16_t)(base + count);
	if (next_count > out->count)
		out->count = next_count;
	if (packet->total_count < out->count)
		out->count = packet->total_count;
	return true;
}

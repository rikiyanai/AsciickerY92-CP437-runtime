#include "authoritative_item_command_surface.h"
#include "authoritative_item_query_surface.h"
#include "authoritative_world_item_appearance.h"

#include "game.h"
#include "game_utility.h"
#include "server/actor_visual_catalog_source.h"
#include "server/protocol/protocol_common.h"
#include "terrain.h"

static const uint64_t MOBILE_AUTO_PICKUP_COOLDOWN_US = 400000;
static float g_authoritative_place_debug_z_offset = 0.0f;

float GetAuthoritativePlaceDebugZOffset()
{
	return g_authoritative_place_debug_z_offset;
}

void AdjustAuthoritativePlaceDebugZOffset(Game* game, float delta_z)
{
	g_authoritative_place_debug_z_offset += delta_z;
	if (g_authoritative_place_debug_z_offset < -HEIGHT_SCALE * 16.0f)
		g_authoritative_place_debug_z_offset = -HEIGHT_SCALE * 16.0f;
	if (g_authoritative_place_debug_z_offset > HEIGHT_SCALE * 16.0f)
		g_authoritative_place_debug_z_offset = HEIGHT_SCALE * 16.0f;
	if (game)
		ChatLog("DEBUG block placement Z offset: %.1f", g_authoritative_place_debug_z_offset);
}


static bool RequestPickupAuthoritativeItemById(Game* game, uint16_t item_id)
{
	if (!game || !server)
	{
		if (game)
			game->debug.dbg_auth_pickup_req_last_reason = 1;
		return false;
	}
	if (item_id == 0xffff)
	{
		game->debug.dbg_auth_pickup_req_last_reason = 4;
		return false;
	}
	const ::AuthoritativeItemState* ai =
		FindAuthoritativeItemStateById(server, item_id);
	if (!ai || !ai->valid || ai->owner_id != 0xffff)
	{
		game->debug.dbg_auth_pickup_req_last_reason = 5;
		return false;
	}

	STRUCT_REQ_ITEM_ACTION req = {};
	req.token = 'I';
	req.kind = ITEM_ACTION_REQ_PICKUP;
	req.item_id = item_id;
	req.pos[0] = ai->pos[0];
	req.pos[1] = ai->pos[1];
	req.pos[2] = ai->pos[2];
	bool sent = server->Send((const uint8_t*)&req, sizeof(req));
	if (sent)
	{
		game->debug.dbg_auth_pickup_req_sent++;
		game->debug.dbg_auth_pickup_req_last_reason = 7;
	}
	else
	{
		game->debug.dbg_auth_pickup_req_send_fail++;
		game->debug.dbg_auth_pickup_req_last_reason = 6;
		ChatLog("AUTH ITEM MODE: pickup request send failed item=%u x=%.1f y=%.1f z=%.1f\n",
			(unsigned int)item_id,
			req.pos[0], req.pos[1], req.pos[2]);
	}
	return sent;
}

uint16_t RebuildLocalAuthoritativeItemState(Game* game)
{
	if (!server)
		return 0;

	uint16_t prev_local_owned = server->authority.auth_item.item_local_owned_count;
	uint16_t known = 0;
	uint16_t world_known = 0;
	uint16_t local_owned = 0;
	int local_ids_n = 0;
	int local_id = server->connection.local_id;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		::AuthoritativeItemState* x = server->authority.auth_item.items + i;
		if (!x->valid)
			continue;
		known++;
		if (x->owner_id == 0xffff)
			world_known++;
		if (local_id >= 0 && x->owner_id == (uint16_t)local_id)
		{
			local_owned++;
			if (local_ids_n < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
				server->authority.auth_item.item_local_ids[local_ids_n++] = x->item_id;
		}
	}
	for (int a = 0; a < local_ids_n; a++)
	{
		for (int b = a + 1; b < local_ids_n; b++)
		{
			if (server->authority.auth_item.item_local_ids[b] <
				server->authority.auth_item.item_local_ids[a])
			{
				uint16_t t = server->authority.auth_item.item_local_ids[a];
				server->authority.auth_item.item_local_ids[a] =
					server->authority.auth_item.item_local_ids[b];
				server->authority.auth_item.item_local_ids[b] = t;
			}
		}
	}
	for (int a = local_ids_n; a < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; a++)
		server->authority.auth_item.item_local_ids[a] = 0xffff;
	server->authority.auth_item.item_count = known;
	server->authority.auth_item.item_world_count = world_known;
	server->authority.auth_item.item_local_owned_count = local_owned;
	if (game)
		ClampAuthoritativeInventoryFocus(game);
	return prev_local_owned;
}

bool UseRespawnAuthoritativeItemBatch(Game* game)
{
	return game && game->authoritative.item_respawn_batch_enabled;
}

void FlushPendingRespawnAuthoritativeItemRefresh(Game* game)
{
	if (!game || !game->authoritative.item_respawn_refresh_pending)
		return;
	RebuildLocalAuthoritativeItemState(game);
	game->CancelItemContacts();
	if (!server)
		game->ui.show_inventory = false;
	game->input.pad_item = 0;
	game->authoritative.item_respawn_refresh_pending = false;
}


bool RequestUseAuthoritativeItemByIndex(Game* game, int index)
{
	if (!game || !server)
	{
		if (game)
			game->debug.dbg_auth_use_req_last_reason = 1;
		return false;
	}
	game->debug.dbg_auth_use_req_attempts++;
	game->debug.dbg_auth_use_req_last_index = index;
	game->debug.dbg_auth_use_req_last_item_id = 0xffff;
	int owned_n = (int)server->authority.auth_item.item_local_owned_count;
	if (owned_n > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		owned_n = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
	if (owned_n <= 0)
	{
		game->debug.dbg_auth_use_req_last_reason = 2;
		return false;
	}

	int pick_index = index;
	if (pick_index < 0) pick_index = 0;
	if (pick_index >= owned_n) pick_index = owned_n - 1;
	uint16_t item_id = server->authority.auth_item.item_local_ids[pick_index];
	if (item_id == 0xffff)
	{
		game->debug.dbg_auth_use_req_last_reason = 3;
		return false;
	}
	game->debug.dbg_auth_use_req_last_item_id = item_id;

	STRUCT_REQ_ITEM_ACTION req = {};
	req.token = 'I';
	req.kind = ITEM_ACTION_REQ_USE;
	req.item_id = item_id;
	req.pos[0] = game->player.pos[0];
	req.pos[1] = game->player.pos[1];
	req.pos[2] = game->player.pos[2];
	bool sent = server->Send((const uint8_t*)&req, sizeof(req));
	if (sent)
	{
		game->debug.dbg_auth_use_req_sent++;
		game->debug.dbg_auth_use_req_last_reason = 5;
	}
	else
	{
		game->debug.dbg_auth_use_req_send_fail++;
		game->debug.dbg_auth_use_req_last_reason = 4;
	}
	return sent;
}

bool RequestPlaceAuthoritativeItemByIndex(Game* game, int index)
{
	if (!game || !server)
	{
		if (game)
			game->debug.dbg_auth_place_req_last_reason = 1;
		return false;
	}
	game->debug.dbg_auth_place_req_attempts++;
	game->debug.dbg_auth_place_req_last_index = index;
	game->debug.dbg_auth_place_req_last_item_id = 0xffff;
	int owned_n = (int)server->authority.auth_item.item_local_owned_count;
	if (owned_n > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		owned_n = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
	if (owned_n <= 0)
	{
		game->debug.dbg_auth_place_req_last_reason = 2;
		return false;
	}

	int pick_index = index;
	if (pick_index < 0) pick_index = 0;
	if (pick_index >= owned_n) pick_index = owned_n - 1;
	uint16_t item_id = server->authority.auth_item.item_local_ids[pick_index];
	if (item_id == 0xffff)
	{
		game->debug.dbg_auth_place_req_last_reason = 3;
		return false;
	}
	game->debug.dbg_auth_place_req_last_item_id = item_id;

	STRUCT_REQ_ITEM_ACTION req = {};
	req.token = 'I';
	req.kind = ITEM_ACTION_REQ_PLACE;
	req.item_id = item_id;
	req.pos[0] = game->player.pos[0];
	req.pos[1] = game->player.pos[1];
	req.pos[2] = game->player.pos[2] + g_authoritative_place_debug_z_offset;
	bool sent = server->Send((const uint8_t*)&req, sizeof(req));
	if (sent)
	{
		game->debug.dbg_auth_place_req_sent++;
		game->debug.dbg_auth_place_req_last_reason = 5;
	}
	else
	{
		game->debug.dbg_auth_place_req_send_fail++;
		game->debug.dbg_auth_place_req_last_reason = 4;
	}
	return sent;
}

bool RequestPlaceEquippedPlaceableAuthoritativeItem(Game* game)
{
	if (!game || !server || server->connection.local_id < 0)
	{
		if (game)
			game->debug.dbg_auth_place_req_last_reason = 10;
		return false;
	}
	game->debug.dbg_auth_place_req_attempts++;
	game->debug.dbg_auth_place_req_last_index = -2;
	game->debug.dbg_auth_place_req_last_item_id = 0xffff;
	const uint16_t local_owner = (uint16_t)server->connection.local_id;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		const AuthoritativeItemState* ai = &server->authority.auth_item.items[i];
		if (!ai->valid || ai->owner_id != local_owner)
			continue;
		if (ai->equip_slot_kind_id != APPEARANCE_SLOT_KIND_HELD_ITEM)
			continue;
		const AppearanceCatalogItemDef* item =
			FindAppearanceCatalogItemById(ai->item_definition_id);
		if (!item || !item->placeable)
			continue;
		game->debug.dbg_auth_place_req_last_item_id = ai->item_id;
		STRUCT_REQ_ITEM_ACTION req = {};
		req.token = 'I';
		req.kind = ITEM_ACTION_REQ_PLACE;
		req.item_id = ai->item_id;
		req.pos[0] = game->player.pos[0];
		req.pos[1] = game->player.pos[1];
		req.pos[2] = game->player.pos[2] + g_authoritative_place_debug_z_offset;
		bool sent = server->Send((const uint8_t*)&req, sizeof(req));
		if (sent)
		{
			game->debug.dbg_auth_place_req_sent++;
			game->debug.dbg_auth_place_req_last_reason = 15;
		}
		else
		{
			game->debug.dbg_auth_place_req_send_fail++;
			game->debug.dbg_auth_place_req_last_reason = 14;
		}
		return sent;
	}
	game->debug.dbg_auth_place_req_last_reason = 11;
	return false;
}

bool RequestPickupAuthoritativeWorldItemByListIndex(Game* game, int index)
{
	if (!game)
		return false;
	game->debug.dbg_auth_pickup_req_attempts++;
	game->debug.dbg_auth_pickup_req_last_index = index;
	game->debug.dbg_auth_pickup_req_last_item_id = 0xffff;
	game->debug.dbg_auth_pickup_req_last_reason = 0;
	game->debug.dbg_auth_pickup_req_source_strip_item_id = 0xffff;
	game->debug.dbg_auth_pickup_req_source_strip_count = game->authoritative.world_pickup_rows_count;
	int n = game->authoritative.world_pickup_rows_count;
	if (n <= 0)
	{
		game->debug.dbg_auth_pickup_req_last_reason = 2;
		ChatLog("AUTH ITEM MODE: pickup request blocked index=%d pickup_rows=%d reason=no_pickup_rows\n",
			index, n);
		return false;
	}
	if (index < 0 || index >= n)
	{
		game->debug.dbg_auth_pickup_req_last_reason = 3;
		ChatLog("AUTH ITEM MODE: pickup request blocked index=%d pickup_rows=%d reason=index_oob\n",
			index, n);
		return false;
	}
	uint16_t item_id = game->authoritative.world_pickup_item_ids[index];
	game->debug.dbg_auth_pickup_req_last_item_id = item_id;
	game->debug.dbg_auth_pickup_req_source_strip_item_id = item_id;
	if (item_id == 0xffff)
	{
		game->debug.dbg_auth_pickup_req_last_reason = 4;
		ChatLog("AUTH ITEM MODE: pickup request blocked index=%d world_items=%d reason=sentinel_item\n",
			index, n);
		return false;
	}
	return RequestPickupAuthoritativeItemById(game, item_id);
}

void UpdateMobileAutoPickup(Game* game, uint64_t stamp)
{
	if (!game)
		return;
	// Pickup candidacy is built from authoritative player.pos while the camera
	// may be following the blended local_display_pos surface. That bounded
	// mismatch is intentional: pickup requests stay on gameplay truth even if
	// a nearby item is momentarily framed by the render-only display surface.
	if (!server || server->connection.local_id < 0)
		return;
	if (game->ui.main_menu || game->ui.menu_depth >= 0 || game->ui.show_gamepad ||
		game->player.talk_box)
		return;
	if (game->authoritative.world_pickup_rows_count <= 0)
		return;
	int cap = game->authoritative.world_pickup_rows_count;
	if (cap <= 0)
		return;
	uint16_t item_id = game->authoritative.world_pickup_item_ids[0];
	if (item_id == 0xffff)
		return;
	const ::AuthoritativeItemState* ai =
		FindAuthoritativeItemStateById(server, item_id);
	if (ai && (ai->v2_state_flags & APPEARANCE_ITEM_STATE_EXPLICIT_PICKUP_ONLY))
		return;
	if (game->inventory_view.mobile_auto_pickup_stamp != 0 &&
		stamp >= game->inventory_view.mobile_auto_pickup_stamp &&
		stamp - game->inventory_view.mobile_auto_pickup_stamp < MOBILE_AUTO_PICKUP_COOLDOWN_US)
	{
		return;
	}

	game->inventory_view.mobile_auto_pickup_item_id = item_id;
	game->inventory_view.mobile_auto_pickup_stamp = stamp;
	game->debug.dbg_auth_pickup_req_attempts++;
	game->debug.dbg_auth_pickup_req_last_index = -2;
	game->debug.dbg_auth_pickup_req_last_item_id = item_id;
	game->debug.dbg_auth_pickup_req_last_reason = 9;
	game->debug.dbg_auth_pickup_req_source_strip_item_id = item_id;
	game->debug.dbg_auth_pickup_req_source_strip_count = game->authoritative.world_pickup_rows_count;
	if (RequestPickupAuthoritativeItemById(game, item_id))
		game->debug.dbg_auth_pickup_req_last_reason = 9;
}

#include "game.h"
#include "game_utility.h"

#include "authoritative_item_command_surface.h"
#include "authoritative_item_query_surface.h"


void Game::CancelItemContacts()
{
	for (int i = 0; i < 4; i++)
	{
		if (input.contact[i].action == Input::Contact::ITEM_GRID_CLICK ||
			input.contact[i].action == Input::Contact::ITEM_LIST_CLICK ||
			input.contact[i].action == Input::Contact::ITEM_GRID_DRAG ||
			input.contact[i].action == Input::Contact::ITEM_LIST_DRAG)
			input.contact[i].action = Input::Contact::NONE;
	}
}

void Game::ExecuteItem(int my_item)
{
	(void)my_item;
	if ((server != 0))
	{
		if (UseAuthoritativeInventoryPanel(this))
		{
			ClampAuthoritativeInventoryFocus(this);
			if (RequestUseAuthoritativeItemByIndex(this, inventory_view.authoritative_inventory_focus))
				return;
		}
		ChatLog("AUTH ITEM MODE: authoritative use request failed\n");
		return;
	}
	abort();
}

int Game::CheckPick(const int cp[2])
{
	(void)cp;
	abort();
}

bool Game::CheckDrop(int c, int drop_xy[2], AnsiCell* ptr, int width, int height)
{
	(void)c;
	(void)drop_xy;
	(void)ptr;
	(void)width;
	(void)height;
	abort();
}

bool Game::PickItem(Item* item)
{
	if ((server != 0))
	{
		CancelItemContacts();
		input.pad_item = 0;
		debug.dbg_auth_pickup_req_last_index = -1;
		debug.dbg_auth_pickup_req_last_item_id = 0xffff;
		debug.dbg_auth_pickup_req_last_reason = 8;
		(void)item;
		ChatLog("AUTH ITEM MODE: legacy PickItem server path blocked; use render-owned pickup strip\n");
		return false;
	}
	return false;
}

bool Game::DropItem(int index)
{
	if ((server != 0))
	{
		if (server)
		{
			uint16_t best_id = 0xffff;
			int owned_n = (int)server->authority.auth_item.item_local_owned_count;
			if (owned_n > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
				owned_n = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
			if (owned_n > 0)
			{
				int pick_index = index;
				if (pick_index < 0) pick_index = 0;
				if (pick_index >= owned_n) pick_index = owned_n - 1;
				best_id = server->authority.auth_item.item_local_ids[pick_index];
			}
			STRUCT_REQ_ITEM_ACTION req = {};
			req.token = 'I';
			req.kind = ITEM_ACTION_REQ_DROP;
			req.item_id = best_id;
			const float* player_pos = player.pos;
			req.pos[0] = player_pos[0];
			req.pos[1] = player_pos[1];
			req.pos[2] = player_pos[2];
			bool sent_drop = server->Send((const uint8_t*)&req, sizeof(req));
			(void)sent_drop;
			server->authority.auth_item.drop_blocked_packets++;
		}
		CancelItemContacts();
		ui.show_inventory = false;
		input.pad_item = 0;
		return true;
	}
	return false;
}

void Game::ScreenToCell(int p[2]) const
{
	p[0] = (2 * p[0] - input.size[0] + session.render_size[0] * session.font_size[0]) / (2 * session.font_size[0]);
	p[1] = (input.size[1] - 1 - 2 * p[1] + session.render_size[1] * session.font_size[1]) / (2 * session.font_size[1]);
}

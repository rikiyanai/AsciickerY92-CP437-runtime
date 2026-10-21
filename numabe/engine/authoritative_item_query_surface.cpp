#include "authoritative_item_query_surface.h"

#include "game.h"
#include "server/actor_visual_catalog_source.h"
#include "server/protocol/protocol_common.h"

static int GetAuthoritativeInventoryRows()
{
	if (!server)
		return 0;
	int rows = (int)server->authority.auth_item.item_local_owned_count;
	if (rows < 0) rows = 0;
	if (rows > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
		rows = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
	return rows;
}

const ::AuthoritativeItemState* FindAuthoritativeItemStateById(
	const Server* server_state,
	uint16_t item_id)
{
	if (!server_state || item_id == 0xffff)
		return 0;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		const ::AuthoritativeItemState* ai = &server_state->authority.auth_item.items[i];
		if (!ai->valid)
			continue;
		if (ai->item_id == item_id)
			return ai;
	}
	return 0;
}

const char* GetAuthoritativeItemLabel(const ::AuthoritativeItemState* ai)
{
	if (!ai || !ai->valid)
		return "UNKNOWN";

	const AppearanceCatalogItemDef* item =
		FindAppearanceCatalogItemById(ai->item_definition_id);
	if (!item || !item->slug || !item->slug[0])
		return "UNMAPPED";

	return item->slug;
}

bool IsAuthoritativeMountItem(const ::AuthoritativeItemState* ai)
{
	if (!ai || !ai->valid)
		return false;

	const AppearanceCatalogItemDef* item =
		FindAppearanceCatalogItemById(ai->item_definition_id);
	return item && item->slot_kind_id == APPEARANCE_SLOT_KIND_MOUNT;
}

bool UseAuthoritativeInventoryPanel(Game* game)
{
	if (!game || !game->ui.show_inventory)
		return false;
	if (!server)
		return false;
	return true;
}

bool GetAuthoritativeInventoryPanelLayout(
	Game* game,
	AuthoritativeInventoryPanelLayout* out)
{
	if (!UseAuthoritativeInventoryPanel(game))
		return false;

	int total_rows = GetAuthoritativeInventoryRows();
	ClampAuthoritativeInventoryFocus(game);
	int visible_rows = total_rows;
	if (visible_rows > 6)
		visible_rows = 6;

	int selected_index = game->inventory_view.authoritative_inventory_focus;
	if (selected_index < 0)
		selected_index = 0;
	if (selected_index >= total_rows)
		selected_index = total_rows - 1;

	int visible_start = 0;
	if (visible_rows > 0 && total_rows > visible_rows)
	{
		visible_start = selected_index - visible_rows / 2;
		if (visible_start < 0)
			visible_start = 0;
		if (visible_start > total_rows - visible_rows)
			visible_start = total_rows - visible_rows;
	}

	if (out)
	{
		out->panel_x = game->inventory_view.layout_x + 1;
		out->panel_y = game->inventory_view.layout_y + 1;
		out->panel_w = game->inventory_view.layout_width - 2;
		out->list_y = game->inventory_view.layout_y + 8;
		out->list_h = game->inventory_view.layout_height - 12;
		if (out->list_h < 1)
			out->list_h = 1;
		out->total_rows = total_rows;
		out->visible_rows = visible_rows;
		out->visible_start = visible_start;
	}
	return true;
}

void ClampAuthoritativeInventoryFocus(Game* game)
{
	if (!game)
		return;
	int rows = GetAuthoritativeInventoryRows();
	if (rows <= 0)
	{
		game->inventory_view.authoritative_inventory_focus = 0;
		return;
	}
	if (game->inventory_view.authoritative_inventory_focus < 0)
		game->inventory_view.authoritative_inventory_focus = 0;
	if (game->inventory_view.authoritative_inventory_focus >= rows)
		game->inventory_view.authoritative_inventory_focus = rows - 1;
}

bool MoveAuthoritativeInventoryFocus(Game* game, int delta)
{
	if (!UseAuthoritativeInventoryPanel(game))
		return false;
	ClampAuthoritativeInventoryFocus(game);
	if (delta < 0)
		game->inventory_view.authoritative_inventory_focus--;
	else if (delta > 0)
		game->inventory_view.authoritative_inventory_focus++;
	ClampAuthoritativeInventoryFocus(game);
	return true;
}

bool HitAuthoritativeInventoryPanelRow(Game* game, const int cp[2], int* out_row)
{
	if (!cp)
		return false;
	AuthoritativeInventoryPanelLayout layout = {};
	if (!GetAuthoritativeInventoryPanelLayout(game, &layout))
		return false;
	if (layout.visible_rows <= 0)
		return false;

	int x0 = layout.panel_x;
	int x1 = layout.panel_x + layout.panel_w - 1;
	int y0 = layout.list_y;
	int y1 = y0 + layout.visible_rows - 1;
	if (cp[0] < x0 || cp[0] > x1 || cp[1] < y0 || cp[1] > y1)
		return false;

	int row = layout.visible_start + (cp[1] - y0);
	if (row < 0 || row >= layout.total_rows)
		return false;
	if (out_row)
		*out_row = row;
	return true;
}

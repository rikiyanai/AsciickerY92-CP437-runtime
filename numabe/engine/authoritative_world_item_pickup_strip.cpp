#include "authoritative_world_item_pickup_strip.h"

#include "authoritative_item_command_surface.h"
#include "actor_visual_profile_runtime.h"
#include "game.h"

void ResetAuthoritativeWorldItemPickupStripState(Game* game)
{
	if (!game)
		return;
	game->authoritative.world_items_count = 0;
	game->inventory_view.items_xarr[0] = 0;
	game->inventory_view.items_ylo = 0;
	game->inventory_view.items_yhi = 0;
	for (int i = 0;
		i < (int)(sizeof(game->authoritative.world_item_ids) /
			sizeof(game->authoritative.world_item_ids[0]));
		i++)
	{
		game->authoritative.world_item_ids[i] = 0xffff;
		game->authoritative.world_definition_ids[i] = 0;
		game->authoritative.world_visual_style_ids[i] = 0;
		game->authoritative.world_visual_failure_reasons[i] =
			ACTOR_VISUAL_ITEM_FAILURE_NONE;
	}
}

int GetAuthoritativeWorldItemPickupStripCount(const Game* game)
{
	if (!game)
		return 0;
	int n = game->authoritative.world_items_count;
	int cap = (int)(sizeof(game->authoritative.world_item_ids) /
		sizeof(game->authoritative.world_item_ids[0]));
	if (n < 0)
		return 0;
	if (n > cap)
		return cap;
	return n;
}

int HitAuthoritativeWorldItemPickupStripSlot(
	const Game* game,
	int cell_x,
	int cell_y)
{
	int n = GetAuthoritativeWorldItemPickupStripCount(game);
	if (!game || n <= 0)
		return -1;
	if (cell_y <= game->inventory_view.items_ylo || cell_y >= game->inventory_view.items_yhi)
		return -1;
	for (int i = 0; i < n; i++)
	{
		if (cell_x > game->inventory_view.items_xarr[i] && cell_x < game->inventory_view.items_xarr[i + 1])
			return i;
	}
	return -1;
}

bool IsWithinAuthoritativeWorldItemPickupStripBounds(
	const Game* game,
	int cell_x,
	int cell_y)
{
	int n = GetAuthoritativeWorldItemPickupStripCount(game);
	if (!game || n <= 0)
		return false;
	if (cell_y < game->inventory_view.items_ylo || cell_y > game->inventory_view.items_yhi)
		return false;
	return cell_x >= game->inventory_view.items_xarr[0] &&
		cell_x <= game->inventory_view.items_xarr[n];
}

void RenderAuthoritativeWorldItemPickupStrip(
	Game* game,
	AnsiCell* ptr,
	int width,
	int height,
	int scene_shift,
	Item** items_inrange,
	const AuthoritativeWorldItemAppearanceFrame* frame)
{
	ResetAuthoritativeWorldItemPickupStripState(game);
	if (!game || !ptr || !frame || !server)
		return;

	int items = 0;
	int items_width = 0;
	int max_height = 0;
	while (items < frame->pickup_row_count && items < 9)
	{
		const AuthoritativeWorldItemAppearanceRow* pickup_row =
			&frame->pickup_rows[items];
		if (!pickup_row->pickup_sprite_2d || !pickup_row->pickup_sprite_2d->atlas)
			break;
		Sprite::Frame* frame2d = pickup_row->pickup_sprite_2d->atlas;
		if (1 + items_width + frame2d->width + items >= width - scene_shift)
			break;
		max_height = max_height < frame2d->height ? frame2d->height : max_height;
		items_width += frame2d->width;
		items++;
	}

	game->authoritative.world_items_count = items;
	game->inventory_view.items_count = 0;
	game->inventory_view.items_inrange = items_inrange;
	game->input.pad_item =
		game->input.pad_item < game->authoritative.world_items_count ?
			game->input.pad_item :
			game->authoritative.world_items_count;

	int items_x = scene_shift/2 + (width - (items_width + items - 1)) / 2;
	int items_y = height / 2 - 2;
	items_y -= (items_y - max_height) / 2;

	if (items)
	{
		int y = items_y - max_height - 1;
		AnsiCell* ac = ptr + items_x + y * width;
		ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 192;
		y++;
		for (; y < items_y; y++)
		{
			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 179;
		}
		ac = ptr + items_x + y * width;
		ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 218;
		items_x++;
	}

	for (int i = 0; i < items; i++)
	{
		const AuthoritativeWorldItemAppearanceRow* pickup_row =
			&frame->pickup_rows[i];
		game->authoritative.world_item_ids[i] = pickup_row->item_id;
		game->inventory_view.items_xarr[i] = items_x - 1;
		game->authoritative.world_visual_failure_reasons[i] =
			pickup_row->pickup_visual_failure_reason;
		game->authoritative.world_definition_ids[i] = pickup_row->definition_id;
		game->authoritative.world_visual_style_ids[i] = pickup_row->visual_style_id;
		if (!pickup_row->pickup_sprite_2d || !pickup_row->pickup_sprite_2d->atlas)
			break;
		Sprite::Frame* frame2d = pickup_row->pickup_sprite_2d->atlas;

		if (i + 1 == game->input.pad_item)
		{
			int x0 = items_x;
			int x1 = items_x + frame2d->width - 1;
			int y0 = items_y - max_height;
			int y1 = items_y - 1;
			for (int y = y0; y <= y1; y++)
			for (int x = x0; x <= x1; x++)
			{
				AnsiCell* ac = ptr + x + y * width;
				ac->bk = brown; ac->fg = black; ac->gl = 32;
			}
		}

		int y = items_y - (max_height + frame2d->height) / 2;
		if (game->input.last_hit_char == '1' + i)
			RequestPickupAuthoritativeWorldItemByListIndex(game, i);
		BlitSprite(ptr, width, height, frame2d, items_x, y);

		for (int x = items_x; x < items_x + frame2d->width; x++)
		{
			AnsiCell* ac = ptr + x + items_y * width;
			ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 196;
			ac = ptr + x + (items_y - max_height - 1) * width;
			if (x == items_x + frame2d->width / 2)
			{
				ac->bk = black; ac->fg = white; ac->gl = '1' + i;
			}
			else
			{
				ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 196;
			}
		}

		items_x += frame2d->width;
		y = items_y - max_height - 1;
		AnsiCell* ac = ptr + items_x + y * width;
		ac->bk = AverageGlyph(ac, 0xF); ac->fg = black;
		ac->gl = (i == items - 1) ? 217 : 193;
		y++;
		for (; y < items_y; y++)
		{
			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF); ac->fg = black; ac->gl = 179;
		}
		ac = ptr + items_x + y * width;
		ac->bk = AverageGlyph(ac, 0xF); ac->fg = black;
		ac->gl = (i == items - 1) ? 191 : 194;
		items_x++;
	}

	game->inventory_view.items_xarr[items] = items_x - 1;
	game->inventory_view.items_ylo = items_y - max_height - 1;
	game->inventory_view.items_yhi = items_y;
}

#pragma once

#include <stdint.h>

#include "game.h"

struct Game;

struct AuthoritativeInventoryPanelLayout
{
	int panel_x;
	int panel_y;
	int panel_w;
	int list_y;
	int list_h;
	int total_rows;
	int visible_rows;
	int visible_start;
};

const ::AuthoritativeItemState* FindAuthoritativeItemStateById(
	const Server* server_state,
	uint16_t item_id);
const char* GetAuthoritativeItemLabel(
	const ::AuthoritativeItemState* ai);
bool IsAuthoritativeMountItem(
	const ::AuthoritativeItemState* ai);
bool UseAuthoritativeInventoryPanel(Game* game);
bool GetAuthoritativeInventoryPanelLayout(
	Game* game,
	AuthoritativeInventoryPanelLayout* out);
void ClampAuthoritativeInventoryFocus(Game* game);
bool MoveAuthoritativeInventoryFocus(Game* game, int delta);
bool HitAuthoritativeInventoryPanelRow(Game* game, const int cp[2], int* out_row);

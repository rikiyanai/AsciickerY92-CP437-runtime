#pragma once

#include "authoritative_world_item_appearance.h"

struct Game;
struct AnsiCell;
struct Item;

void ResetAuthoritativeWorldItemPickupStripState(Game* game);
void RenderAuthoritativeWorldItemPickupStrip(
	Game* game,
	AnsiCell* ptr,
	int width,
	int height,
	int scene_shift,
	Item** items_inrange,
	const AuthoritativeWorldItemAppearanceFrame* frame);
int GetAuthoritativeWorldItemPickupStripCount(const Game* game);
int HitAuthoritativeWorldItemPickupStripSlot(
	const Game* game,
	int cell_x,
	int cell_y);
bool IsWithinAuthoritativeWorldItemPickupStripBounds(
	const Game* game,
	int cell_x,
	int cell_y);

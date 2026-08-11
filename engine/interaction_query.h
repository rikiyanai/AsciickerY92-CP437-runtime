#pragma once

#include <string.h>

#include "inventory.h"
#include "terrain.h"
#include "world.h"

struct PlayerPose
{
	float pos[3];
	float dir;
	float yaw;
};

struct InteractionQueryResult
{
	static constexpr int kMaxItems = 9;
	static constexpr int kMaxCharacters = 3;

	int item_count;
	Item* items[kMaxItems + 1];
	float item_distance2[kMaxItems];

	// Phase 6 first slice: gameplay only consumes world-item pickup rows today.
	// Keep the character lane in the result shape so the render-side collector
	// stays deleted instead of coming back through a new getter later.
	int character_count;
	Inst* characters[kMaxCharacters + 1];
	float character_distance2[kMaxCharacters];
};

static inline void ResetInteractionQueryResult(InteractionQueryResult* out)
{
	if (!out)
		return;
	memset(out, 0, sizeof(*out));
	for (int i = 0; i < InteractionQueryResult::kMaxItems; i++)
		out->item_distance2[i] = 1.0e30f;
	for (int i = 0; i < InteractionQueryResult::kMaxCharacters; i++)
		out->character_distance2[i] = 1.0e30f;
}

struct InteractionItemCollector
{
	const PlayerPose* player_pose;
	InteractionQueryResult* out;
};

static inline void InsertInteractionItemSorted(
	InteractionQueryResult* out,
	Item* item,
	float distance2)
{
	if (!out || !item)
		return;

	int slot = out->item_count;
	if (slot >= InteractionQueryResult::kMaxItems)
		slot = InteractionQueryResult::kMaxItems - 1;
	else
		out->item_count++;
	if (slot < 0)
		return;

	while (slot > 0 && distance2 < out->item_distance2[slot - 1])
	{
		out->items[slot] = out->items[slot - 1];
		out->item_distance2[slot] = out->item_distance2[slot - 1];
		slot--;
	}

	out->items[slot] = item;
	out->item_distance2[slot] = distance2;
	out->items[out->item_count] = 0;
}

static inline void CollectInteractionItem(
	Inst* inst,
	Item* item,
	const float pos[3],
	float yaw,
	int /*story_id*/,
	void* cookie)
{
	(void)inst;
	(void)yaw;
	InteractionItemCollector* collector = (InteractionItemCollector*)cookie;
	if (!collector || !collector->player_pose || !collector->out || !item || !pos)
		return;

	const PlayerPose& player_pose = *collector->player_pose;
	float dx = player_pose.pos[0] - pos[0];
	float dy = player_pose.pos[1] - pos[1];
	float dz = (player_pose.pos[2] + 3 * HEIGHT_SCALE - pos[2]) / HEIGHT_SCALE;
	float distance2 = dx * dx + dy * dy + dz * dz;

	// Match the old renderer-side pickup collector radius exactly so deleting
	// the render getter does not silently change pickup reach.
	const float max_item_distance2 = 20.0f;
	if (distance2 >= max_item_distance2)
		return;

	InsertInteractionItemSorted(collector->out, item, distance2);
}

static inline InteractionQueryResult QueryInteractions(
	const World& world,
	const Terrain& terrain,
	const PlayerPose& player_pose)
{
	(void)terrain;
	InteractionQueryResult result = {};
	ResetInteractionQueryResult(&result);

	InteractionItemCollector collector = {};
	collector.player_pose = &player_pose;
	collector.out = &result;
	QueryWorldItems(const_cast<World*>(&world), CollectInteractionItem, &collector);
	return result;
}

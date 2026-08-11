#pragma once

// authoritative_client_state.h — Client-side authoritative item/pickup wire state
//
// PURPOSE:
// Holds the client's local mirror of server-authoritative item ownership,
// world item visibility, pickup proximity, and inventory contents.
// Extracted from game.h.

#include <stdint.h>

#include "../server/authoritative_item_server_state.h"

// FL-4137 Gap A: mobile tap-on-floating-preview contact surface. The held
// placeable preview's screen hit rect, published by the appearance pass so
// the mobile tap router can dispatch ITEM_ACTION_REQ_PLACE intent via the
// same helper as desktop P and the mobile player double-tap. Deliberately
// NOT inserted into world_pickup_rows — that surface remains pickup-only
// (Law 1: single ownership of pickup vs place; Law 3: client sends intent
// only). Server remains the final placement validator (Law 6).
struct AuthoritativeHeldPreviewMobileContact
{
	uint8_t valid;
	uint16_t item_id;
	// Cell-space rect after ScreenToCell — inclusive
	// [cell_x0, cell_x1] x [cell_y0, cell_y1].
	int16_t cell_x0;
	int16_t cell_y0;
	int16_t cell_x1;
	int16_t cell_y1;
};

struct AuthoritativeClientState
{
	bool item_respawn_refresh_pending;
	bool item_respawn_batch_enabled;
	bool local_npcs_retired;
	int world_items_count;
	uint16_t world_item_ids[9];
	int world_pickup_rows_count;
	uint16_t world_pickup_item_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	float world_pickup_distance2[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	uint16_t world_definition_ids[9];
	uint16_t world_visual_style_ids[9];
	uint8_t world_visual_failure_reasons[9];
	int inventory_items_count;
	uint16_t inventory_item_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	uint16_t inventory_definition_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	uint16_t inventory_visual_style_ids[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	uint8_t inventory_visual_failure_reasons[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	AuthoritativeHeldPreviewMobileContact held_preview_mobile_contact;
};

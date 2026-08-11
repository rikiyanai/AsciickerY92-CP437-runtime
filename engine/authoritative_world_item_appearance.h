#pragma once

#include <stdint.h>

#include "game.h"

struct Game;

// FL-4137 Gap B: held-preview placement-validity reasons. The client mirrors
// the server placement rule purely as a render hint; the server remains the
// sole writer of placed pos/state (Law 3, Law 7). preview_reason is set only
// on rows that represent the local player's held placeable preview; placed
// world rows leave it at PREVIEW_PLACEMENT_REASON_NONE.
//
// Reason vocabulary mirrors the server reject strings emitted by
// SvrPlacedBlockPositionOccupied ("placed_block_overlap"). A stack that
// reaches above the player's max-lift cap is not a separate reason — the
// server's lift loop drops cap-excluded candidates back to terrain_z, and the
// subsequent overlap check then reports placed_block_overlap. The client
// mirror collapses to the same vocabulary so client preview verdicts can be
// compared 1:1 to server reject logs (Law 11, Law 13).
enum AuthoritativeWorldItemPreviewReason
{
	PREVIEW_PLACEMENT_REASON_NONE = 0,
	PREVIEW_PLACEMENT_REASON_OK = 1,
	PREVIEW_PLACEMENT_REASON_BLOCKED_PLACED_OVERLAP = 2,
};

struct AuthoritativeWorldItemAppearanceRow
{
	uint16_t item_id;
	uint16_t definition_id;
	uint16_t visual_style_id;
	uint16_t world_sprite_source_hash;
	uint16_t world_sprite_family_kind;
	uint8_t render_visual_failure_reason;
	uint8_t pickup_visual_failure_reason;
	Sprite* pickup_sprite_2d;
	const ::AuthoritativeItemState* state;
	int on_screen;
	int inst_visible;
	int pickup_in_range;
	float pickup_distance2;
	// FL-4137 #31 invariant probe: visual top/bottom derived from the sprite's
	// projected bbox (spr3d->proj_bbox[4..5]) plus the item's authoritative
	// pos.z. Proof asserts visual_top_z == pos.z + collision_height_units so
	// no future render/asset/catalog drift can produce a visible-vs-climbable
	// mismatch without falsifying.
	float visual_bottom_z;
	float visual_top_z;
	// FL-4137 Gap B: additive placement preview/affordance fields. Pure
	// render/observation data — not authority. placed=1 means
	// APPEARANCE_ITEM_STATE_PLACED is present on the authoritative item state
	// (already-placed world block, gated by the server PLACED bit so dropped/
	// world-owned non-placed loot does not falsely register as placed);
	// preview_valid mirrors the server placement rule for the local held
	// preview only.
	uint8_t placed;
	uint8_t preview_valid;
	uint8_t preview_reason;
	float half_extent;
	float height;
	// FL-4137 #35 / FL-4163: screen projection of this row's TOP and BOTTOM,
	// computed where ProjectCoords already runs in the appearance pass. The
	// visibility regression test reads these to sample renderbuf inside the
	// projected rectangle and assert a block-body cell appears at the topmost
	// row of that rect — closing the "visible top == world top" contract via
	// the same oracle pattern FL-4079 uses for armor/shield cells.
	// screen_valid=0 means ProjectCoords failed (block off-screen or behind
	// camera); the test must skip ROI sampling for that row.
	uint8_t screen_valid;
	int16_t screen_top_col;
	int16_t screen_top_row;
	int16_t screen_bottom_col;
	int16_t screen_bottom_row;
	// FL-4137 collision wireframe: 8 projected corners of the placed block's
	// collision AABB. Order is the 3-bit cube convention:
	//   bit 0 = +X, bit 1 = +Y, bit 2 = +Z
	//   corner[0] = -x,-y,bottom  corner[4] = -x,-y,top
	//   corner[1] = +x,-y,bottom  corner[5] = +x,-y,top
	//   corner[2] = -x,+y,bottom  corner[6] = -x,+y,top
	//   corner[3] = +x,+y,bottom  corner[7] = +x,+y,top
	// Drawn by the JS overlay in game_web.html as a red wireframe so operator
	// can compare the collision volume against the rendered sprite extent
	// (visible-top == world-top contract).
	uint8_t corners_valid;
	int16_t corner_col[8];
	int16_t corner_row[8];
};

struct AuthoritativeWorldItemAppearanceFrame
{
	int visible_row_count;
	int pickup_row_count;
	// FL-4137 Gap A: per-frame mobile preview tap rect. Published by the
	// appearance pass and copied into game->authoritative.held_preview_mobile_contact
	// by PublishAuthoritativeWorldItemAppearanceRows. Memset to zero by
	// ResetAuthoritativeWorldItemFrame, so .valid defaults to 0 each frame
	// when no held placeable preview is visible.
	AuthoritativeHeldPreviewMobileContact held_preview_mobile_contact;
	AuthoritativeWorldItemAppearanceRow visible_rows[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
	AuthoritativeWorldItemAppearanceRow pickup_rows[AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS];
};

Sprite* ResolveAuthoritativeItemSprite3D(
	const ::AuthoritativeItemState* ai,
	uint8_t* out_failure_reason = 0);
Sprite* ResolveAuthoritativeItemSprite2D(
	const ::AuthoritativeItemState* ai,
	uint8_t* out_failure_reason = 0);
void ResetAuthoritativeWorldItemAppearanceRows(Game* game);
void PublishAuthoritativeWorldItemAppearanceRows(
	Game* game,
	const AuthoritativeWorldItemAppearanceFrame* frame);
bool UpdateAuthoritativeWorldItemAppearance(
	Game* game,
	World* world,
	Renderer* renderer,
	uint64_t render_stamp_us,
	int viewport_width,
	int viewport_height,
	AuthoritativeWorldItemAppearanceFrame* out);

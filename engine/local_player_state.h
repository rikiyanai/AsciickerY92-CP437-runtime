#pragma once

// local_player_state.h — Local controlled player state
//
// PURPOSE:
// Extends Human with local-only fields: authoritative snapshot validity,
// snapshot presentation tracking, yaw smoothing, grounding state, and
// the player's world instance pointer. Extracted from game.h.

#include "human.h"
#include "snapshot_client/local_snapshot_presentation_track.h"

struct Inst;
struct World;

struct LocalPlayerState : Human
{
	bool authoritative_snapshot_valid;
	LocalSnapshotPresentationTrack snapshot_presentation_track;
	float prev_yaw;
	float yaw_vel;
	uint64_t last_torque_active_stamp; // FL-1733: suppress snapshot yaw resync during active Q/E input
	bool prev_grounded; // moved from static Render::prev_grounded
	Inst* player_inst;
};

// Bootstrap a minimal valid V2 appearance on the local player when no server
// has sent one, using the bundle's default profile.
// Returns true if appearance_v2 is now valid.
bool EnsureLocalPlayerAppearance(LocalPlayerState& player);

// Switch the local player's body-owner family to |skin_id| through the V2
// bundle path.  Resolves the presentation immediately and updates the player
// sprite + inst so the change is visible on the current frame.
// |stamp| is a microsecond timestamp for presentation resolution.
// |world| is the inst world needed for UpdateSpriteInst; may be null.
// Returns true if the sprite was successfully resolved.
bool ApplyLocalPlayerSkin(LocalPlayerState& player, uint16_t skin_id, uint64_t stamp, World* world);

#pragma once

// camera_state.h — Camera view parameters
//
// PURPOSE:
// Holds camera zoom, pan, and smoothing state.
// Extracted from game.h.

struct CameraState
{
	int scene_shift;
	int cam_shift; // vertical camera pan
	float cam_smooth_z; // smoothed camera Z to reduce vertical jitter
	bool cam_smooth_z_init; // false until first frame sets cam_smooth_z
	float zoom; // render zoom factor

	// FL-4257 — client-owned spherical (S3) first-person camera. s3_first_person
	// and s3_free_pitch are PRESENTATION ONLY (consumed only by render_s3.cpp,
	// ignored in the Euclidean baseline so the far-side parity checkpoint stays
	// byte-identical until the player toggles first-person). Default = orbit.
	bool  s3_first_person = false; // false = orbit camera, true = first person
	// FL-4257 — first-person horizontal look is GAMEPLAY INTENT, not a render
	// offset (the reviewer-finding-1 fix): this is a PENDING body-yaw rotation
	// (deg) accumulated by look input (keyboard h/l, and pointer-lock mouse-X)
	// and consumed once per authority tick in local_player_authority.cpp, where
	// it feeds the single yaw-intent owner (io->yaw) exactly like the mouse-drag
	// torque path. Facing follows the view because the server adopts that yaw
	// and movement uses it (Law 2/3). It is zeroed after each tick.
	float s3_look_yaw_delta = 0.0f;
	float s3_free_pitch = 0.0f;    // first-person look pitch (deg, +up); PRESENTATION only, clamped in builder
};

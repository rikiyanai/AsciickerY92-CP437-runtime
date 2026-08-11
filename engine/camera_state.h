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
};

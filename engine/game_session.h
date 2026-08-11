#pragma once

// game_session.h — Persistent session preferences and render settings
//
// PURPOSE:
// Holds user-configurable session state: font/render size, water level,
// lighting, fly mode, audio, control scheme, perspective, and gore toggle.
// Rarely mutated mid-frame — mostly read by rendering and input paths.
// Extracted from game.h.

struct GameSession
{
	int font_size[2];
	int render_size[2];
	int water;
	float light[4];
	bool fly_mode;
	bool mute;
	bool mobile_controls;
	bool perspective;
	bool blood;
};

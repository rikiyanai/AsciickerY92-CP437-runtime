#pragma once
#include <stdint.h>

struct Sprite;
struct AnsiCell;
struct Game;
struct Server;
struct DebugTelemetryState;
struct LocalPlayerState;
struct InventoryViewState;
struct UiState;
struct GameSession;
struct InputState;
struct Character;

extern Sprite* keyb_sprite[5];
extern Sprite* caps_sprite[3];

struct Keyb
{
	int plane = 0;
	int sect = 0;
	int dir = 11;
	bool pad_plane = false;

	int GetPadCap(char* ch, bool shift_on);
	int GetCap(int dx, int dy, int width, int height, char* ch, bool shift_on) const;
	int Width(int width, int height) const;
	int Height(int width, int height) const;
	void Paint(AnsiCell* ptr, int width, int height, int hide, const uint8_t key[32], bool gamepad) const;
};

extern Keyb keyb;

// Called from Game::Render (game.cpp) -- defined in game_input.cpp
void UpdateMobileAutoCombat(Server* srv, DebugTelemetryState& debug, LocalPlayerState& player,
	InventoryViewState& inventory_view, const UiState& ui,
	const GameSession& session, const InputState& input,
	uint64_t stamp, const float pos[3], float dir);

void UpdateOfflineWallClockPresentationAuthority(Character& actor, uint64_t stamp);

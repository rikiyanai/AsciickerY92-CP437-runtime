#pragma once

struct Game;
struct GameSession;
struct UiState;

struct MenuIO
{
	GameSession* session;
	UiState* ui;
	int screen_size[2];
	Game* close_game;
};

struct Menu
{
	const char* str;
	const Menu* sub;
	void (*action)(MenuIO* io);
	bool (*getter)(MenuIO* io);
};

extern const Menu game_menu[];

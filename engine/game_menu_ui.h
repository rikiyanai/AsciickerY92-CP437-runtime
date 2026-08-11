#pragma once

// game_menu_ui.h — Menu item callback declarations
//
// PURPOSE:
// Houses the MenuIO* → void/bool menu callback signatures that Game::OpenMenu
// wires into static Menu tables. Extracted from game.h as a leaf module.
//
// All callbacks take MenuIO* instead of Game*; callers construct the MenuIO
// from Game sub-structs at the call site.

struct MenuIO;

void menu_perspective(MenuIO* io);
bool menu_perspective_getter(MenuIO* io);
void menu_blood(MenuIO* io);
bool menu_blood_getter(MenuIO* io);
void menu_yes_exit(MenuIO* io);
void menu_no_exit(MenuIO* io);
void menu_fullscreen(MenuIO* io);
bool menu_fullscreen_getter(MenuIO* io);
void menu_mute(MenuIO* io);
bool menu_mute_getter(MenuIO* io);
void menu_mobile_controls(MenuIO* io);
bool menu_mobile_controls_getter(MenuIO* io);
void menu_zoomin(MenuIO* io);
void menu_zoomout(MenuIO* io);
void main_menu(MenuIO* io);

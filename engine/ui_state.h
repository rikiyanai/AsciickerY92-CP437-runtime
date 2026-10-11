#pragma once

// ui_state.h — Menu, talk box, and overlay UI state
//
// PURPOSE:
// Holds transient UI visibility, menu focus, and talk-box state.
// Extracted from game.h.

struct UiState
{
	struct TalkMem
	{
		char buf[256];
		int len;
	};

	bool show_keyb; // activated together with talk_box by clicking on character
	int keyb_hide;  // show / hide animator (vertical position)
	bool show_gamepad;
	bool show_cam_overlay;
	bool show_inventory;
	bool show_minimap;
	bool show_buts; // true only if no popup is visible
	bool main_menu; // true when main menu is open (before gameplay)
	int bars_pos; // used to hide buts (0..7)
	int TalkBox_blink;
	int menu_stack[4]; // menu_stack[menu_depth] contains current item (hilight)
	int menu_depth; // -1 when closed, 0 just after OpenMenu
	int menu_down; // 0: released, 1:mouse_captured, 2:touch_captured
	int menu_down_x;
	int menu_down_y;
	int menu_temp; // last highlighted row when pad/keyb focus hands off to mouse/touch
	TalkMem talk_mem[4];
};

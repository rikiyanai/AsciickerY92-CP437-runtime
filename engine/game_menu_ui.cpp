// game_menu_ui.cpp -- menu lifecycle + menu item callbacks
// extracted from game.cpp
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#if defined(_WIN32)
#include <process.h>
#endif
#include "game.h"
#include "game_utility.h"
#include "font1.h"
#include "mainmenu.h"
#include "menu.h"

#ifndef SERVER
bool NextGLFont();
bool PrevGLFont();
void ToggleFullscreen(Game* g);
bool IsFullscreen(Game* g);
void AudioMute(bool);
#endif
extern void exit_handler(int signum);






void menu_perspective(MenuIO* io)
{
	io->session->perspective = !io->session->perspective;
	WriteConf(*io->session, *io->ui);
}


void menu_blood(MenuIO* io)
{
	io->session->blood = !io->session->blood;
	WriteConf(*io->session, *io->ui);
}

void menu_yes_exit(MenuIO* io)
{
	(void)io;
	#ifdef USE_SDL
	exit(0);
	#else
	exit_handler(0);
	#endif // TODO: [Backlog Ref] #endif
}


void menu_fullscreen(MenuIO* io)
{
	#ifndef SERVER
	ToggleFullscreen(io->close_game);
	#endif // TODO: [Backlog Ref] #endif
}


void menu_mute(MenuIO* io)
{
	#ifndef SERVER
	io->session->mute = !io->session->mute;
	AudioMute(io->session->mute);
	WriteConf(*io->session, *io->ui);
	#endif // TODO: [Backlog Ref] #endif
}


void menu_mobile_controls(MenuIO* io)
{
	#ifndef SERVER
	io->session->mobile_controls = true;
	WriteConf(*io->session, *io->ui);
	#endif // TODO: [Backlog Ref] #endif
}



void menu_zoomin(MenuIO* io)
{
	(void)io;
	#ifndef SERVER
	NextGLFont();
	#endif // TODO: [Backlog Ref] #endif
}


void menu_zoomout(MenuIO* io)
{
	(void)io;
	#ifndef SERVER
	PrevGLFont();
	#endif // TODO: [Backlog Ref] #endif
}


void main_menu(MenuIO* io)
{
	#ifndef EDITOR
	io->close_game->CloseMenu();
	io->ui->main_menu = true;

	MainMenu_Show();

	MainMenu_OnSize(
		io->screen_size[0],
		io->screen_size[1],
		io->session->font_size[0],
		io->session->font_size[1]);

	#endif // TODO: [Backlog Ref] #endif
}



void Game::OpenMenu(int method)
{
	if (ui.menu_depth>=0)
		return;

	ui.menu_temp = 0;
	ui.menu_down = 0;
	ui.menu_down_x = 0;
	ui.menu_down_y = 0;

	ui.show_gamepad = false;
	// will be cleared by menu
	// ui.show_buts = true;


	if (player.talk_box)
	{
		free(player.talk_box);
		player.talk_box = 0;
	}

	if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
		memset(input.keyb_key, 0, 32);
	ui.show_keyb = false;
	input.KeybAutoRepChar = 0;
	input.KeybAutoRepCap = 0;
	for (int i=0; i<4; i++)
	{
		if (input.contact[i].action == Input::Contact::KEYBCAP)
			input.contact[i].action = Input::Contact::NONE;
	}

	CancelItemContacts();
	ui.show_inventory = false;

	ui.show_buts = false;
	ui.menu_depth = 0;

	ui.menu_stack[ui.menu_depth] = method != 1 && method != 2 ? 0 : -1;
}


void Game::CloseMenu()
{
	if (ui.menu_depth<0)
		return;
	ui.show_buts = true;
	ui.menu_depth = -1;

	// clear input
	input.but = 0;
}


void Game::ToggleMenu(int method)
{
	if (ui.menu_depth>=0)
		CloseMenu();
	else
		OpenMenu(method);
}


int Game::HitMenu(int hx, int hy)
{
	if (ui.menu_depth<0)
		return -3;

	int cp[2] = { hx, hy };
	ScreenToCell(cp);
	hx=cp[0];
	hy=cp[1];

	const Menu* m = game_menu;
	const char* title = "MENU";
	for (int d=0; d<ui.menu_depth; d++)
	{
		title = m[ ui.menu_stack[d] ].str;
		m = m[ ui.menu_stack[d] ].sub;
	}

	// right align
	int x = session.render_size[0]-5;
	int y = session.render_size[1]-10;

	// title test
	{
		int w = 0, h = 0;
		Font1Size(title,&w,&h);

		if (hx >= 3+x-w /*&& hx<3+x*/ && hy >=y && hy<y+h)
		{
			// title hit
			return -1;
		}

		y -= h+2;
	}

	int i=0;
	while(m[i].str)
	{
		int w = 0, h = 0;
		Font1Size(m[i].str,&w,&h);

		if (hx >= x-w /*&& hx < x*/ && hy>=y && hy<y+h)
		{
			// item hit
			return i;
		}

		y -= h+1;
		i++;
	}

	return -2;
}


bool menu_perspective_getter(MenuIO* io)
{
	return io->session->perspective;
}

bool menu_blood_getter(MenuIO* io)
{
	return io->session->blood;
}

void menu_no_exit(MenuIO* io)
{
	io->ui->menu_depth--;
	io->ui->menu_temp = io->ui->menu_stack[io->ui->menu_depth];
}

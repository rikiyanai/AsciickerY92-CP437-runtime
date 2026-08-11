// input handling extracted from game.cpp
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdint.h>
#if defined(_WIN32)
#include <process.h>
#define A3D_GETPID _getpid
#else
#include <unistd.h>
#define A3D_GETPID getpid
#endif
#define _USE_MATH_DEFINES
#include <math.h>
#include "game.h"
#include "facing_space.h"
#include "game_menu_ui.h"
#include "local_player_authority.h"
#include "game_utility.h"
#include "authoritative_presentation_adapters.h"
#include "snapshot_client/remote_authoritative_snapshot.h"
#include "authoritative_item_command_surface.h"
#include "authoritative_item_query_surface.h"
#include "snapshot_client/snapshot_entity_decoder.h"
#include "snapshot_client/snapshot_stream_applier.h"
#include "actor_visual_profile_packet.h"
#include "authoritative_world_item_appearance.h"
#include "authoritative_world_item_pickup_strip.h"
#include "interaction_query.h"
#include "snapshot_client/local_snapshot_presentation_track.h"
#include "physics_query.h"
#include "physics_step.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"
#include "remote_actor_roster.h"
#include "remote_authoritative_presentation_lifecycle.h"
#include "remote_mounted_witness.h"
#include "remote_observer_probe.h"
#include "actor_visual_profile_runtime.h"
#include "snapshot_client/snapshot_npc_repository.h"
#include "enemygen.h"
#include "platform/input_backend.h"

#include "multiplayer_protocol.h"
#include "physics_tick.h"
#include "mp_step.h"
#include "a3d_load_context.h"
#include "snapshot_client/snapshot_npc_visual_lifecycle.h"
#include "matrix.h"
#include "fast_rand.h"
#include "font1.h"
#include "gamepad.h"
#include "audio.h"
#include "game_api.h"
#include "sprite_registry.h"
#ifdef __EMSCRIPTEN__
extern uint64_t GetTime();
#endif
#include "lexer.h"
#include "mainmenu.h"
#include "weather.h"

extern Material mat[256];

#include "game_input.h"

// FL-2481: client cooldown must exceed the server's 12-tick (400ms) cooldown
// by enough margin to absorb network jitter and tick-boundary alignment.
// 500ms gives ~100ms of headroom before the server counts a violation.
static const uint64_t MP_SWING_COOLDOWN = 500000;
static const uint64_t MOBILE_PLAYER_PLACE_DOUBLE_TAP_US = 350000;


struct SwingFireSupportSample
{
	bool terrain_hit;
	bool world_hit;
	float terrain_z;
	float world_z;
	float support_z;
	float support_dz;
	int support_kind; // 0=none,1=terrain,2=world
};

static SwingFireSupportSample SampleSwingFireSupport(const float pos[3])
{
	SwingFireSupportSample out = { false, false, 0.0f, 0.0f, 0.0f, 0.0f, 0 };
	double probe[3] = { (double)pos[0], (double)pos[1], (double)pos[2] + 3.0 * HEIGHT_SCALE };
	double downward[3] = { 0.0, 0.0, -probe[2] - 1.0 };
	double terrain_ret[4] = { 0.0, 0.0, 0.0, 1.0 };
	double world_ret[4] = { 0.0, 0.0, 0.0, 1.0 };

	if (terrain && HitTerrain(terrain, probe, downward, terrain_ret, 0, true))
	{
		out.terrain_hit = true;
		out.terrain_z = (float)terrain_ret[2];
		out.support_z = out.terrain_z;
		out.support_kind = 1;
	}
	if (world && HitWorld(world, probe, downward, world_ret, 0, true))
	{
		out.world_hit = true;
		out.world_z = (float)world_ret[2];
		if (!out.terrain_hit || out.world_z > out.support_z)
		{
			out.support_z = out.world_z;
			out.support_kind = 2;
		}
	}
	if (out.support_kind != 0)
		out.support_dz = pos[2] - out.support_z;
	return out;
}

static int NormalizeDebugInputKey(GAME_KEYB keyb, int key, int* out_auto_repeat)
{
	int auto_repeat = 0;
	int normalized = key;
	if (keyb == GAME_KEYB::KEYB_DOWN)
	{
		auto_repeat = (key & A3D_AUTO_REPEAT) != 0 ? 1 : 0;
		normalized &= ~A3D_AUTO_REPEAT;
	}
	if (out_auto_repeat)
		*out_auto_repeat = auto_repeat;
	return normalized;
}

static void RecordDebugInputEvent(DebugTelemetryState& debug, uint64_t stamp, const LocalPlayerState& player, const UiState& ui, GAME_KEYB keyb, int key)
{
	int auto_repeat = 0;
	int normalized = NormalizeDebugInputKey(keyb, key, &auto_repeat);
	uint32_t next_seq = debug.dbg_input_event_seq + 1;
	uint32_t dt_ms = 0;
	if (debug.dbg_input_event_last_stamp > 0 && stamp >= debug.dbg_input_event_last_stamp)
	{
		uint64_t dt_us = stamp - debug.dbg_input_event_last_stamp;
		dt_ms = (uint32_t)(dt_us / 1000ull);
	}

	DebugTelemetryState::DebugInputEvent* ev = &debug.dbg_input_event[next_seq % DebugTelemetryState::DBG_INPUT_EVENT_RING];
	ev->seq = next_seq;
	ev->kind = (int)keyb;
	ev->key = normalized;
	ev->auto_repeat = auto_repeat;
	ev->dt_ms = dt_ms;
	ev->main_menu_active = ui.main_menu ? 1 : 0;
	ev->show_inventory_active = ui.show_inventory ? 1 : 0;
	ev->talk_box_active = player.talk_box ? 1 : 0;
	ev->menu_depth_value = ui.menu_depth;

	debug.dbg_input_event_seq = next_seq;
	debug.dbg_input_event_last_stamp = stamp;
}

static bool UseServerOwnedLocalAction(Server* srv)
{
	return srv && srv->connection.local_id >= 0;
}

static bool HandlePlaceHeightDebugKey(Game* game, int key)
{
	if (!game || server == 0)
		return false;
	if (key != '[' && key != ']' && key != A3D_OEM_OPEN && key != A3D_OEM_CLOSE)
	{
		return false;
	}
	const bool down = key == '[' || key == A3D_OEM_OPEN;
	AdjustAuthoritativePlaceDebugZOffset(game, down ? -(float)HEIGHT_SCALE : (float)HEIGHT_SCALE);
	return true;
}

void UpdateOfflineWallClockPresentationAuthority(Character& actor, uint64_t stamp)
{
	const uint32_t presentation_tick = (uint32_t)(stamp / 1000ull);

	// FL-3955: offline/local has no server snapshot owner, so the gameplay/input
	// layer owns presentation_kind_id. Render/adapters may read it but must not
	// infer it from life/combat state again.
	if (actor.HP <= 0 || actor.life_state == LIFE_STATE::DEAD)
	{
		actor.life_state = LIFE_STATE::DEAD;
		actor.combat_state = COMBAT_STATE::NONE;
		if (actor.presentation_kind_id != APPEARANCE_PRESENTATION_KIND_DEATH)
		{
			actor.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_DEATH;
			actor.presentation_started_tick = presentation_tick;
			actor.action_stamp = stamp;
		}
		return;
	}

	if (actor.life_state != LIFE_STATE::ALIVE)
		actor.life_state = LIFE_STATE::ALIVE;

	if (actor.combat_state == COMBAT_STATE::ATTACKING &&
		stamp >= actor.action_stamp &&
		stamp - actor.action_stamp >= MP_SWING_COOLDOWN)
	{
		actor.combat_state = COMBAT_STATE::NONE;
		if (actor.presentation_kind_id != APPEARANCE_PRESENTATION_KIND_IDLE_WALK)
		{
			actor.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
			actor.presentation_started_tick = presentation_tick;
		}
	}

	if (actor.presentation_kind_id == 0)
	{
		actor.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
		actor.presentation_started_tick = presentation_tick;
	}
}

Sprite* keyb_sprite[5] = { 0,0,0,0,0 };
Sprite* caps_sprite[3] = { 0,0,0 };

enum
{
	// private virtual keys
	KBD_COMMA = A3D_MAPEND, KBD_PERIOD, KBD_QUESTION, KBD_PLUS, KBD_MINUS, KBD_MULTIPLY, KBD_SLASH, KBD_UNDERLINE, KBD_EQUAL,
	KBD_EXCLAMATION, KBD_MONKEY, KBD_HASH, KBD_DOLLAR, KBD_PERCENT, KBD_DASH, KBD_AMPERSAND,
	KBD_OPEN, KBD_CLOSE, KBD_CURLYOPEN, KBD_CURLYCLOSE, KBD_BRACKETOPEN, KBD_BRACKETCLOSE, KBD_SMALLER, KBD_GREATER, KBD_TILDE,
	KBD_BACKSLASH, KBD_COLON, KBD_SEMICOLON, KBD_APOSTROPHE, KBD_QUOTATION, KBD_BACKQUOTE, KBD_PIPE
};

static const int caps_plane[3][3][10] =
{
	{
		{ A3D_Q, A3D_W, A3D_E, A3D_R, A3D_T, A3D_Y, A3D_U, A3D_I, A3D_O, A3D_P },
		{ A3D_A, A3D_S, A3D_D, A3D_F, A3D_G, A3D_H, A3D_J, A3D_K, A3D_L, A3D_ENTER },
		{ A3D_LSHIFT, A3D_Z, A3D_X, A3D_C, A3D_V, A3D_B, A3D_N, A3D_M, A3D_SPACE, A3D_RSHIFT },
	},
	{
		{ A3D_0, A3D_1, A3D_2, A3D_3, A3D_4, A3D_5, A3D_6, A3D_7, A3D_8, A3D_9 },
		{ KBD_COMMA, KBD_PERIOD, KBD_QUESTION, KBD_PLUS, KBD_MINUS, KBD_MULTIPLY, KBD_SLASH, KBD_UNDERLINE, KBD_EQUAL, A3D_ENTER},
		{ A3D_LSHIFT, KBD_BACKSLASH, KBD_COLON, KBD_SEMICOLON, KBD_APOSTROPHE, KBD_QUOTATION, KBD_BACKQUOTE, KBD_PIPE, A3D_SPACE, A3D_RSHIFT}
	},
	{
		{ A3D_0, A3D_1, A3D_2, A3D_3, A3D_4, A3D_5, A3D_6, A3D_7, A3D_8, A3D_9 },
		{ KBD_OPEN, KBD_CLOSE, KBD_CURLYOPEN, KBD_CURLYCLOSE, KBD_BRACKETOPEN, KBD_BRACKETCLOSE, KBD_SMALLER, KBD_GREATER, KBD_TILDE, A3D_ENTER},
		{ A3D_LSHIFT, KBD_EXCLAMATION, KBD_MONKEY, KBD_HASH, KBD_DOLLAR, KBD_PERCENT, KBD_DASH, KBD_AMPERSAND, A3D_SPACE, A3D_RSHIFT}
	},
};

static const char char_plane[3][3][10] =
{
	{
		{ 'q','w','e','r','t','y','u','i','o','p' },
		{ 'a','s','d','f','g','h','j','k','l', '\n' },
		{  1 ,'z','x','c','v','b','n','m',' ', 2  },
	},
	{
		{ '0','1','2','3','4','5','6','7','8','9' },
		{ ',','.','?','+','-','*','/','_','=', '\n'  },
		{  1,'\\',':',';','\'','"','`','|',' ', 2  },
	},
	{
		{ '0','1','2','3','4','5','6','7','8','9' },
		{ '(',')','{','}','[',']','<','>','~', '\n'  },
		{  1 ,'!','@','#','$','%','^','&',' ', 2  },
	}
};

int Keyb::GetPadCap(char* ch, bool shift_on)
{
		if (dir == 11)
		{
			if (ch)
				*ch = 0;
			return 0;
		}


		int i = 0;
		int j = 0;
		switch (dir) 
		{
			case 0: i = 1; j = 1; break;
			case 1: i = 2; j = 2; break;
			case 2: i = 3; j = 2; break;
			case 3: i = 2; j = 1; break;
			case 4: i = 3; j = 0; break;
			case 5: i = 2; j = 0; break;
			case 6: i = 1; j = 0; break;
			case 7: i = 0; j = 0; break;
			case 8: i = 0; j = 1; break;
			case 9: i = 0; j = 2; break;
			case 10: i = 1; j = 2; break;
		}

		i += sect * 3;

		char cc = char_plane[plane][2-j][i];
		if (shift_on)
		{
			if (cc >= 'a' && cc <= 'z')
				cc += 'A' - 'a';
			if (cc == ' ')
				cc = 8; // shift + space = backspace !!!
		}

		if (ch)
			*ch = cc;

		return caps_plane[plane][2-j][i];
	}

int Keyb::GetCap(int dx, int dy, int width, int height, char* ch, bool shift_on) const
{
		int sprite_w = 2 * (width - 1) / 21 + 1;
		int sprite_h = 0;
		int sprite_i = 0;
		int delta_x = 0;
		int delta_y = 0;
		int delta_d = 0;
		int caps_dy = 0;

		if (sprite_w < 9)
		{
			sprite_w = 7;
			sprite_h = 8;
			delta_x = 3;
			delta_y = 5;
			delta_d = 0;
			caps_dy = 0;
			sprite_i = 0;
		}
		else
		if (sprite_w < 11)
		{
			sprite_w = 9;
			sprite_h = 8;
			delta_x = 4;
			delta_y = 5;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 1;
		}
		else
		if (sprite_w < 13)
		{
			sprite_w = 11;
			sprite_h = 10;
			delta_x = 5;
			delta_y = 6;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 2;
		}
		else
		if (sprite_w < 15)
		{
			sprite_w = 13;
			sprite_h = 13;
			delta_x = 6;
			delta_y = 8;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 3;
		}
		else
		{
			sprite_w = 15;
			sprite_h = 15;
			delta_x = 7;
			delta_y = 9;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 4;
		}

		int keyb_w = 21 * (sprite_w - 1) / 2 + 1;
		int keyb_h = 2 * delta_y + sprite_h;

		int center_x = (width - keyb_w) / 2;


		static int press_j = -1;
		static int press_i = -1;

		static int clicker = 0;
		clicker++;
		if (clicker == 10)
		{
			press_j = fast_rand() % 3;
			press_i = fast_rand() % 10;
		}
		if (clicker == 15)
		{
			clicker = 0;
			press_j = -1;
			press_i = -1;
		}

		int hide = 0;

		for (int j = 0; j < 3; j++)
		{
			for (int i = 0; i < 10; i++)
			{
				int x = center_x + i * (sprite_w - 1) + (j & 1) * delta_x;
				int y = j * delta_y - hide;

				Sprite::Frame* sf = keyb_sprite[sprite_i]->atlas;

				int cx = dx - x;
				int cy = dy - y;
				if (cx >= 0 && cy >= 0 && cx < sprite_w && cy < sprite_h)
				{
					AnsiCell* ac = sf->cell + cy * sf->width + cx;

					// opaqueness test
					if (ac->bk != 255 && ac->gl != 32 && ac->gl != 0 || ac->fg != 255 && ac->gl != 219)
					{
						if (ch)
						{
							char cc = char_plane[plane][2 - j][i];
							if (shift_on)
							{
								if (cc >= 'a' && cc <= 'z')
									cc += 'A' - 'a';
								if (cc == ' ')
									cc = 8; // shift + space = backspace !!!
							}
							*ch = cc;
						}

						return caps_plane[plane][2 - j][i];
					}
				}
			}
		}

		if (ch)
			*ch = 0;
		return -1; // return 0 if it was very close to keyb // TODO: [Backlog Ref] return 0 if it was very close to keyb
	}

int Keyb::Width(int width, int height) const
{
		int sprite_w = 2 * (width - 1) / 21 + 1;
		int sprite_h = 0;
		int sprite_i = 0;
		int delta_x = 0;
		int delta_y = 0;
		int delta_d = 0;
		int caps_dy = 0;

		if (sprite_w < 9)
		{
			sprite_w = 7;
			sprite_h = 8;
			delta_x = 3;
			delta_y = 5;
			delta_d = 0;
			caps_dy = 0;
			sprite_i = 0;
		}
		else
		if (sprite_w < 11)
		{
			sprite_w = 9;
			sprite_h = 8;
			delta_x = 4;
			delta_y = 5;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 1;
		}
		else
		if (sprite_w < 13)
		{
			sprite_w = 11;
			sprite_h = 10;
			delta_x = 5;
			delta_y = 6;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 2;
		}
		else
		if (sprite_w < 15)
		{
			sprite_w = 13;
			sprite_h = 13;
			delta_x = 6;
			delta_y = 8;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 3;
		}
		else
		{
			sprite_w = 15;
			sprite_h = 15;
			delta_x = 7;
			delta_y = 9;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 4;
		}

		int keyb_w = 21 * (sprite_w - 1) / 2 + 1;
		int keyb_h = 2 * delta_y + sprite_h;

		return keyb_w;
	}

int Keyb::Height(int width, int height) const
{
		int sprite_w = 2 * (width - 1) / 21 + 1;
		int sprite_h = 0;
		int sprite_i = 0;
		int delta_x = 0;
		int delta_y = 0;
		int delta_d = 0;
		int caps_dy = 0;

		if (sprite_w < 9)
		{
			sprite_w = 7;
			sprite_h = 8;
			delta_x = 3;
			delta_y = 5;
			delta_d = 0;
			caps_dy = 0;
			sprite_i = 0;
		}
		else
		if (sprite_w < 11)
		{
			sprite_w = 9;
			sprite_h = 8;
			delta_x = 4;
			delta_y = 5;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 1;
		}
		else
		if (sprite_w < 13)
		{
			sprite_w = 11;
			sprite_h = 10;
			delta_x = 5;
			delta_y = 6;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 2;
		}
		else
		if (sprite_w < 15)
		{
			sprite_w = 13;
			sprite_h = 13;
			delta_x = 6;
			delta_y = 8;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 3;
		}
		else
		{
			sprite_w = 15;
			sprite_h = 15;
			delta_x = 7;
			delta_y = 9;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 4;
		}

		int keyb_w = 21 * (sprite_w - 1) / 2 + 1;
		int keyb_h = 2 * delta_y + sprite_h;

		return keyb_h;
	}

void Keyb::Paint(AnsiCell* ptr, int width, int height, int hide, const uint8_t key[32], bool gamepad) const
	{
		// hide should be netween 0 and Height()

		// shift modifies appeariance of space->BS and enter->LF, (possibly caps az->AZ)
		bool shift_on = key[A3D_LSHIFT >> 3] & (1 << (A3D_LSHIFT & 7));
		shift_on |= (bool)(key[A3D_RSHIFT >> 3] & (1 << (A3D_RSHIFT & 7)));


		int sprite_w = 2 * (width - 1) / 21 + 1;
		int sprite_h = 0;
		int sprite_i = 0;
		int delta_x = 0;
		int delta_y = 0;
		int delta_d = 0;
		int caps_dy = 0;

		if (sprite_w < 9)
		{
			sprite_w = 7;
			sprite_h = 8;
			delta_x = 3;
			delta_y = 5;
			delta_d = 0;
			caps_dy = 0;
			sprite_i = 0;
		}
		else
		if (sprite_w < 11)
		{
			sprite_w = 9;
			sprite_h = 8;
			delta_x = 4;
			delta_y = 5;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 1;
		}
		else
		if (sprite_w < 13)
		{
			sprite_w = 11;
			sprite_h = 10;
			delta_x = 5;
			delta_y = 6;
			delta_d = 1;
			caps_dy = 0;
			sprite_i = 2;
		}
		else
		if (sprite_w < 15)
		{
			sprite_w = 13;
			sprite_h = 13;
			delta_x = 6;
			delta_y = 8;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 3;
		}
		else
		{
			sprite_w = 15;
			sprite_h = 15;
			delta_x = 7;
			delta_y = 9;
			delta_d = 1;
			caps_dy = 1;
			sprite_i = 4;
		}

		int keyb_w = 21 * (sprite_w - 1) / 2 + 1;
		int keyb_h = 2 * delta_y + sprite_h;

		int center_x = (width - keyb_w) / 2;



		for (int j = 2; j >= 0; j--)
		{
			for (int i = 9; i >= 0; i--)
			{
				int x = center_x + i * (sprite_w - 1) + (j & 1) * delta_x;
				int y = j * delta_y - hide;

				Sprite::Frame* sf = keyb_sprite[sprite_i]->atlas;

				bool press = false;

				int clip[] = { 0,0,sprite_w,sprite_h };
				if (gamepad)
				{
					if (j == 2)  // 4
					{
						if (sect == 0 && i < 4 ||
							sect == 1 && i >= 3 && i < 7 ||
							sect == 2 && i >= 6 && i < 10)
						{
							bool hi = false;
							int k = i - 3 * sect;
							switch (dir)
							{
								case 9: hi = k == 0; break; // leftmost
								case 10: hi = k == 1; break; // left
								case 1: hi = k == 2; break; // right
								case 2: hi = k == 3; break; // rightmost
							}

							if (hi)
							{
								clip[0] = 2 * sprite_w;
								clip[2] = 3 * sprite_w;
								if (!press)
									y -= delta_d;
								press = true;
							}
							else
							{
								clip[0] = sprite_w;
								clip[2] = 2 * sprite_w;
							}
						}
					}
					else
					if (j == 1) // 3
					{
						if (sect == 0 && i < 3 ||
							sect == 1 && i >= 3 && i < 6 ||
							sect == 2 && i >= 6 && i < 9)
						{
							bool hi = false;
							int k = i - 3 * sect;
							switch (dir)
							{
								case 8: hi = k == 0; break; // left
								case 0: hi = k == 1; break; // center
								case 3: hi = k == 2; break; // right
							}

							if (hi)
							{
								clip[0] = 2 * sprite_w;
								clip[2] = 3 * sprite_w;
								if (!press)
									y -= delta_d;
								press = true;
							}
							else
							{
								clip[0] = sprite_w;
								clip[2] = 2 * sprite_w;
							}
						}
					}
					else 
					if (j == 0) // 4
					{
						if (sect == 0 && i < 4 ||
							sect == 1 && i >= 3 && i < 7 ||
							sect == 2 && i >= 6 && i < 10)
						{
							bool hi = false;
							int k = i - 3 * sect;
							switch (dir)
							{
								case 7: hi = k == 0; break; // leftmost
								case 6: hi = k == 1; break; // left
								case 5: hi = k == 2; break; // right
								case 4: hi = k == 3; break; // rightmost
							}

							if (hi)
							{
								clip[0] = 2 * sprite_w;
								clip[2] = 3 * sprite_w;
								if (!press)
									y -= delta_d;
								press = true;
							}
							else
							{
								clip[0] = sprite_w;
								clip[2] = 2 * sprite_w;
							}
						}
					}
				}

				int cap = caps_plane[plane][2-j][i];

				if (key[cap>>3] & (1<<(cap&7)))
				{
					if (!press)
						y -= delta_d;
					clip[0] = 2 * sprite_w;
					clip[2] = 3 * sprite_w;
				}

				BlitSprite(ptr, width, height, sf, x, y, clip);

				int caps_clip[] = {i*5, j*5, (i+1) * 5, (j+1) * 5 };

				Sprite::Frame* caps_sf = caps_sprite[plane]->atlas;
				BlitSprite(ptr, width, height, caps_sf, x + sprite_w/2 - 2, y + sprite_h/2 - 2 + caps_dy, caps_clip);
			}
		}

	}

Keyb keyb;

static bool HasMobileAssistTouch(const InputState& input)
{
	for (int i = 0; i < 4; i++)
	{
		const InputState::Contact* con = &input.contact[i];
		if (!con->drag)
			continue;
		switch (con->action)
		{
			case Game::Input::Contact::KEYBCAP:
			case Game::Input::Contact::ITEM_LIST_CLICK:
			case Game::Input::Contact::ITEM_LIST_DRAG:
			case Game::Input::Contact::ITEM_GRID_CLICK:
			case Game::Input::Contact::ITEM_GRID_DRAG:
			case Game::Input::Contact::ITEM_GRID_SCROLL:
				continue;
			default:
				return true;
		}
	}
	return false;
}

static bool HasMobileAutoCombatArmed(const InventoryViewState& inventory_view)
{
	return inventory_view.mobile_auto_combat_state != MOBILE_AUTO_COMBAT_STATE::NONE;
}

static bool HasMobileCombatAssistActive(const InventoryViewState& inventory_view, const InputState& input)
{
	return inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::ARMED ||
		HasMobileAssistTouch(input);
}

static uint16_t FindLocalEquippedWeaponItemId(const Server* s)
{
	if (!s || s->connection.local_id < 0)
		return 0xffff;
	const uint16_t local_id = (uint16_t)s->connection.local_id;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		const ::AuthoritativeItemState* ai = &s->authority.auth_item.items[i];
		if (!ai->valid || !ai->v2_valid)
			continue;
		if (ai->owner_id != local_id)
			continue;
		if (ai->equip_slot_kind_id == APPEARANCE_SLOT_KIND_WEAPON)
			return ai->item_id;
	}
	return 0xffff;
}

static void ClearMobileAutoCombatState(InventoryViewState& inventory_view)
{
	inventory_view.mobile_auto_combat_item_id = 0xffff;
	inventory_view.mobile_auto_combat_stamp = 0;
	inventory_view.mobile_auto_combat_state = MOBILE_AUTO_COMBAT_STATE::NONE;
}

static void SetMobileAutoCombatState(InventoryViewState& inventory_view, uint8_t state, uint16_t item_id, uint64_t stamp)
{
	inventory_view.mobile_auto_combat_state = state;
	inventory_view.mobile_auto_combat_item_id = item_id;
	inventory_view.mobile_auto_combat_stamp = stamp;
}

static void PauseMobileAutoCombatState(InventoryViewState& inventory_view)
{
	if (inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::PENDING_USE ||
		inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::ARMED)
	{
		inventory_view.mobile_auto_combat_state = MOBILE_AUTO_COMBAT_STATE::PAUSED;
	}
}

static bool MobileAutoCombatSuppressedByAction()
{
	return false;
}

static float GetCurrentLocalAttackDir(const LocalPlayerState& player)
{
	return player.dir;
}

static bool SendAuthoritativeSwingRequest(Server* srv, DebugTelemetryState& debug, const LocalPlayerState& player,
	uint64_t stamp, const float local_pose_pos[3],
	float attack_dir, uint16_t debug_target_id, const float debug_target_pos[3])
{
	if (!UseServerOwnedLocalAction(srv))
		return false;
	// FL-2896/FL-2481: enforce client-side cooldown so manual attack mashing
	// doesn't accumulate server-side rate-limit violations and disconnect.
	static uint64_t last_swing_send_stamp = 0;
	if (last_swing_send_stamp != 0 && stamp >= last_swing_send_stamp &&
		stamp - last_swing_send_stamp < MP_SWING_COOLDOWN)
		return false;
	last_swing_send_stamp = stamp;
	float player_pos_sample[3] = { player.pos[0], player.pos[1], player.pos[2] };
	const float* player_pos_storage = local_pose_pos ? local_pose_pos : player_pos_sample;

	SwingFireSupportSample player_support = SampleSwingFireSupport(player_pos_storage);
	debug.dbg_mp_swing_fire_player_pos[0] = player_pos_storage[0];
	debug.dbg_mp_swing_fire_player_pos[1] = player_pos_storage[1];
	debug.dbg_mp_swing_fire_player_pos_z = player_pos_storage[2];
	debug.dbg_mp_swing_fire_player_terrain_z = player_support.terrain_hit ? player_support.terrain_z : -9999.0f;
	debug.dbg_mp_swing_fire_player_world_z = player_support.world_hit ? player_support.world_z : -9999.0f;
	debug.dbg_mp_swing_fire_player_support_z = player_support.support_kind ? player_support.support_z : -9999.0f;
	debug.dbg_mp_swing_fire_player_support_dz = player_support.support_kind ? player_support.support_dz : -9999.0f;
	debug.dbg_mp_swing_fire_player_support_kind = player_support.support_kind;
	debug.dbg_mp_swing_fire_remote_pos[0] = 0.0f;
	debug.dbg_mp_swing_fire_remote_pos[1] = 0.0f;
	debug.dbg_mp_swing_fire_remote_pos_z = 0.0f;
	debug.dbg_mp_swing_fire_remote_terrain_z = -9999.0f;
	debug.dbg_mp_swing_fire_remote_world_z = -9999.0f;
	debug.dbg_mp_swing_fire_remote_support_z = -9999.0f;
	debug.dbg_mp_swing_fire_remote_support_dz = -9999.0f;
	debug.dbg_mp_swing_fire_remote_support_kind = 0;
	debug.dbg_mp_swing_fire_rdd = -1.0f;
	debug.dbg_mp_swing_fire_dif = 9999.0f;
	debug.dbg_mp_swing_fire_remote_hp = -1;
	debug.dbg_mp_swing_eval_remote_pos[0] = 0.0f;
	debug.dbg_mp_swing_eval_remote_pos[1] = 0.0f;
	debug.dbg_mp_swing_eval_rdd = -1.0f;
	debug.dbg_mp_swing_eval_dif = 9999.0f;
	debug.dbg_mp_swing_eval_remote_hp = -1;
	debug.dbg_mp_swing_last_remote_candidates = 0;
	debug.dbg_mp_swing_last_remote_in_cone = 0;
	debug.dbg_mp_swing_last_best_remote_id = -1;
	debug.dbg_mp_swing_last_best_remote_dd = 0.0f;
	debug.dbg_mp_swing_last_best_remote_dif = 0.0f;
	debug.dbg_mp_swing_last_npc_candidates = 0;
	debug.dbg_mp_swing_last_npc_in_cone = 0;
	debug.dbg_mp_swing_last_best_npc_id = -1;
	debug.dbg_mp_swing_last_best_npc_dd = 0.0f;
	debug.dbg_mp_swing_last_best_npc_dif = 0.0f;

	float current_dir = GetCurrentLocalAttackDir(player);
	if (debug_target_pos)
	{
		float dx = debug_target_pos[0] - player_pos_storage[0];
		float dy = debug_target_pos[1] - player_pos_storage[1];
		float dist2 = dx * dx + dy * dy;

		float dif = attack_dir - current_dir;
		dif = fmodf(dif, 360.0f);
		if (dif < -180.0f) dif += 360.0f;
		if (dif > +180.0f) dif -= 360.0f;

		debug.dbg_mp_swing_eval_remote_pos[0] = debug_target_pos[0];
		debug.dbg_mp_swing_eval_remote_pos[1] = debug_target_pos[1];
		debug.dbg_mp_swing_eval_rdd = dist2;
		debug.dbg_mp_swing_eval_dif = dif;
		debug.dbg_mp_swing_fire_remote_pos[0] = debug_target_pos[0];
		debug.dbg_mp_swing_fire_remote_pos[1] = debug_target_pos[1];
		debug.dbg_mp_swing_fire_remote_pos_z = debug_target_pos[2];
		debug.dbg_mp_swing_fire_rdd = dist2;
		debug.dbg_mp_swing_fire_dif = dif;

		SwingFireSupportSample remote_support = SampleSwingFireSupport(debug_target_pos);
		debug.dbg_mp_swing_fire_remote_terrain_z = remote_support.terrain_hit ? remote_support.terrain_z : -9999.0f;
		debug.dbg_mp_swing_fire_remote_world_z = remote_support.world_hit ? remote_support.world_z : -9999.0f;
		debug.dbg_mp_swing_fire_remote_support_z = remote_support.support_kind ? remote_support.support_z : -9999.0f;
		debug.dbg_mp_swing_fire_remote_support_dz = remote_support.support_kind ? remote_support.support_dz : -9999.0f;
		debug.dbg_mp_swing_fire_remote_support_kind = remote_support.support_kind;

		if (debug_target_id < (uint16_t)srv->connection.max_clients)
		{
			debug.dbg_mp_swing_last_selected_kind = 1;
			debug.dbg_mp_swing_last_remote_candidates = 1;
			debug.dbg_mp_swing_last_remote_in_cone = 1;
			debug.dbg_mp_swing_last_best_remote_id = (int)debug_target_id;
			debug.dbg_mp_swing_last_best_remote_dd = dist2;
			debug.dbg_mp_swing_last_best_remote_dif = dif;
			debug.dbg_mp_swing_eval_remote_hp = 0;
			debug.dbg_mp_swing_fire_remote_hp = 0;
		}
		else
		{
			debug.dbg_mp_swing_last_selected_kind = 2;
			debug.dbg_mp_swing_last_npc_candidates = 1;
			debug.dbg_mp_swing_last_npc_in_cone = 1;
			debug.dbg_mp_swing_last_best_npc_id = (int)debug_target_id;
			debug.dbg_mp_swing_last_best_npc_dd = dist2;
			debug.dbg_mp_swing_last_best_npc_dif = dif;
		}
	}
	else
		debug.dbg_mp_swing_last_selected_kind = 0;
	debug.dbg_mp_swing_fire_player_dir = attack_dir;

	STRUCT_REQ_SWING req_swing = {};
	req_swing.token = debug_target_pos ? 'X' : 'H';
	req_swing.target_id = debug_target_pos ? debug_target_id : 0xFFFF;
	if (!srv->Send((const uint8_t*)&req_swing, sizeof(req_swing)))
		return false;

	debug.dbg_mp_swing_send_attempts++;
	debug.dbg_mp_swing_last_target_id = req_swing.target_id;
	debug.dbg_mp_swing_last_player_dir = attack_dir;
	return true;
}

static bool SendAuthoritativeSwordSwing(Server* srv, DebugTelemetryState& debug, const LocalPlayerState& player,
	uint64_t stamp, const float local_pose_pos[3])
{
	float pos_s[3] = { player.pos[0], player.pos[1], player.pos[2] };
	const float* pos = local_pose_pos ? local_pose_pos : pos_s;
	return SendAuthoritativeSwingRequest(srv, debug, player, stamp, pos, GetCurrentLocalAttackDir(player), 0xffff, 0);
}

static bool StartManualAttack(Server* srv, DebugTelemetryState& debug, LocalPlayerState& player, uint64_t stamp, const float local_pose_pos[3])
{
	(void)local_pose_pos;
	// Gate attack on having a weapon equipped. Upstream design: unarmed players
	// cannot swing. Without this gate, click-to-attack fires the local ATTACK
	// presentation_kind and sends a swing request even with no weapon, producing
	// a confusing attack pose and a no-op server-side swing.
	if (srv && FindLocalEquippedWeaponItemId(srv) == 0xffff)
		return false;
	if (!UseServerOwnedLocalAction(srv))
	{
		player.combat_state = COMBAT_STATE::ATTACKING;
		player.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_ATTACK;
		player.action_stamp = stamp;
		player.presentation_started_tick = (uint32_t)(stamp / 1000ull);
		return true;
	}
	return SendAuthoritativeSwordSwing(srv, debug, player, stamp, local_pose_pos);
}

void UpdateMobileAutoCombat(Server* srv, DebugTelemetryState& debug, LocalPlayerState& player,
	InventoryViewState& inventory_view, const UiState& ui,
	const GameSession& session, const InputState& input,
	uint64_t stamp, const float local_pose_pos[3], float combat_dir)
{
	(void)combat_dir;

	if (!session.mobile_controls || !srv || srv->connection.local_id < 0)
	{
		ClearMobileAutoCombatState(inventory_view);
		return;
	}

	if (ui.main_menu || ui.menu_depth >= 0 || player.talk_box)
	{
		PauseMobileAutoCombatState(inventory_view);
		return;
	}

	if (MobileAutoCombatSuppressedByAction())
	{
		PauseMobileAutoCombatState(inventory_view);
		return;
	}

	uint16_t equipped_weapon_item_id = FindLocalEquippedWeaponItemId(srv);
	bool has_equipped_weapon = equipped_weapon_item_id != 0xffff;
	if (has_equipped_weapon &&
		(inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::NONE ||
		 inventory_view.mobile_auto_combat_item_id != equipped_weapon_item_id))
	{
		SetMobileAutoCombatState(inventory_view, MOBILE_AUTO_COMBAT_STATE::ARMED, equipped_weapon_item_id, 0);
	}
	else if (!has_equipped_weapon && HasMobileAutoCombatArmed(inventory_view))
	{
		ClearMobileAutoCombatState(inventory_view);
	}

	bool combat_assist_active = HasMobileCombatAssistActive(inventory_view, input);
	bool auto_combat_armed =
		(inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::ARMED);
	bool auto_combat_resume_pending =
		(inventory_view.mobile_auto_combat_state == MOBILE_AUTO_COMBAT_STATE::PAUSED);

	if (auto_combat_resume_pending)
	{
		SetMobileAutoCombatState(inventory_view, MOBILE_AUTO_COMBAT_STATE::ARMED, inventory_view.mobile_auto_combat_item_id, inventory_view.mobile_auto_combat_stamp);
		auto_combat_armed = true;
	}

	if (!combat_assist_active && !auto_combat_armed)
	{
		return;
	}

	if (auto_combat_armed && !has_equipped_weapon)
		return;
	if (inventory_view.mobile_auto_combat_stamp != 0 &&
		stamp >= inventory_view.mobile_auto_combat_stamp &&
		stamp - inventory_view.mobile_auto_combat_stamp < MP_SWING_COOLDOWN)
	{
		return;
	}

	if (StartManualAttack(srv, debug, player, stamp, local_pose_pos))
	{
		inventory_view.mobile_auto_combat_stamp = stamp;
		if (has_equipped_weapon)
			inventory_view.mobile_auto_combat_item_id = equipped_weapon_item_id;
	}
}

static bool PlayerHit(const LocalPlayerState& player, const GameSession& session, int scene_shift, int cell_x, int cell_y)
{
	int cx = cell_x - scene_shift / 2;
	int cy = cell_y;

	// if talkbox is open check it too // TODO: [Backlog Ref] if talkbox is open check it too
	if (player.talk_box)
	{
		int w = player.talk_box->size[0] + 3;
		int left = session.render_size[0] / 2 - w / 2;
		int center = left + w / 2;
		int right = left + w - 1;
		int bottom = session.render_size[1] / 2 + 8;
		int lower = bottom + 1;
		int upper = bottom + 4 + player.talk_box->size[1];

		if (cx >= left && cx <= right && cy >= lower && cy <= upper || cx == center && cy == bottom)
			return true;
	}

	Sprite* s = player.sprite;
	if (s)
	{
		float player_dir = player.dir;
		// Hit/card selection uses the same raw angle index as RenderSprite().
		// player.dir carries the legacy sprite-compensated direction (S=0°),
		// so FacingSpriteAngleIndex maps directly to the correct row.
		int ang = FacingSpriteAngleIndex(player_dir, player.prev_yaw, s->angles);
		int i = player.frame + ang * s->anim[player.anim].length;
		// refl: i += s->anim[anim].length * s->angles;
		Sprite::Frame* f = s->atlas + s->anim[player.anim].frame_idx[i];

		int sx = cx - session.render_size[0] / 2 + f->ref[0] / 2;
		int sy = cy - session.render_size[1] / 2 + f->ref[1] / 2;

		if (sx >= 0 && sx < f->width && sy >= 0 && sy < f->height)
		{
			AnsiCell* ac = f->cell + sx + f->width * sy;
			if (ac->fg != 255 && ac->gl != 0 && ac->gl != 32 || ac->bk != 255 && ac->gl != 219)
			{
				return true;
			}
		}
	}

	return false;
}

// - Attack: Left-click/gamepad trigger → input.jump (repurposed for contact attack)
// - Use: E key/gamepad button → item pickup/consumption
// - Inventory: I key/gamepad button → ui.show_inventory toggle
// - Minimap: M key → ui.show_minimap toggle
// - Camera: Mouse drag/touch drag → yaw rotation (SetPhysicsYaw)
// - Menu: ESC/gamepad start → OpenMenu()
//
// MULTI-INPUT FUSION:
// Multiple input sources can be active simultaneously (keyboard + gamepad, or
// touch + keyboard). Forces are accumulated (not replaced) to support hybrid
// control schemes. Last active input method determines UI hints (show keyboard
// prompts vs gamepad button icons).
//
// LAYERED INPUT DISPATCH:
// Input is handled by first matching layer: main_menu → pause menu → gamepad
// config → inventory_view → game world. Each layer consumes input, preventing
// pass-through (e.g., typing in chat doesn't also trigger movement).
//
void Game::OnKeyb(GAME_KEYB keyb, int key)
{
	/*
	if (keyb==KEYB_CHAR)
	{
		int freq = 100 + fast_rand()%100;
		CallAudio((uint8_t*)&freq,sizeof(freq));
	}
	*/

	RecordDebugInputEvent(debug, stamp, player, ui, keyb, key);

	if (keyb == GAME_KEYB::KEYB_DOWN)
	{
		bool auto_rep = (key & A3D_AUTO_REPEAT) != 0;
		int shot_key = key & ~A3D_AUTO_REPEAT;
		static int fljit_onkeyb_down_logs = 0;
		if (fljit_onkeyb_down_logs < 48 &&
			(shot_key == A3D_W || shot_key == A3D_A || shot_key == A3D_S || shot_key == A3D_D))
		{
			printf("[FLJIT-ONKEYB-DOWN] key=%d auto=%d main_menu=%d ui.menu_depth=%d talk_box=%d gamepad=%d\n",
				shot_key, auto_rep ? 1 : 0, ui.main_menu ? 1 : 0, ui.menu_depth, player.talk_box ? 1 : 0, ui.show_gamepad ? 1 : 0);
			fflush(stdout);
			fljit_onkeyb_down_logs++;
		}

		if ((shot_key == A3D_F3 || shot_key == A3D_OEM_TILDE) && !auto_rep)
		{
			int w = (GetWeather() + 1) % 4;
			SetWeather(w);
		}
		if (shot_key == A3D_F9 && !auto_rep)
			ui.show_cam_overlay = !ui.show_cam_overlay;
		if (shot_key == A3D_F10 && !auto_rep)
			input.shot = true;
	}

	// WHY layered input dispatch:
	// UI layers (main_menu, pause menu, gamepad config) consume input exclusively
	// to prevent pass-through to game world. For example, pressing ESC in menu
	// closes menu rather than opening inventory_view.
	// handle layers first ...
	if (ui.main_menu)
	{
		MainMenu_OnKeyb(keyb,key);
		return;
	}

	if (ui.menu_depth>=0)
	{
		MenuKeyb(keyb,key);
		return;
	}

	if (ui.show_gamepad)
	{
		int k = -1;
		switch (keyb)
		{
			case GAME_KEYB::KEYB_CHAR:
			{
				switch (key)
				{
					case ' ': k = 0; break;
					case '\n': k = 1; break;
					case 8:
					case '\\':
					case 27: k = 2; break;

					default:
						if (key>32 && key<127)
							k = key;
				}
				break;
			}

			case GAME_KEYB::KEYB_PRESS:
			case GAME_KEYB::KEYB_DOWN:
			{
				switch (key)
				{
					case A3D_ENTER: k = 1; break;
					case A3D_ESCAPE: k = 2; break;
					case A3D_UP: k = 3; break;
					case A3D_DOWN: k = 4; break;
					case A3D_LEFT: k = 5; break;
					case A3D_RIGHT: k = 6; break;
				}
				break;
			}

			default:
				break;
		}

		if (k>=0)
			GamePadKeyb(k, stamp);

		return;
	}
	

	// if nothing focused // TODO: [Backlog Ref] if nothing focused

	// in case it comes from the real keyboard
	// if emulated, theoretically caller must revert it // TODO: [Backlog Ref] if emulated, theoretically caller must revert it
	// but in practice it will reset it later to emulated cap
	input.KeybAutoRepChar = 0;

	if (keyb == GAME_KEYB::KEYB_DOWN)
	{
		if (key == A3D_ESCAPE)
		{
			// cancel all contacts
			for (int i = 0; i < 4; i++)
			{
				input.contact[i].action = Input::Contact::NONE;
			}
		}

		bool auto_rep = (key & A3D_AUTO_REPEAT) != 0;
		key &= ~A3D_AUTO_REPEAT;

		if (!player.talk_box && HandlePlaceHeightDebugKey(this, key))
			return;

		if (!player.talk_box && key == A3D_M && !auto_rep)
		{
			ui.show_minimap = !ui.show_minimap;
			ChatLog("Minimap: %s", ui.show_minimap ? "ON" : "OFF");
			return;
		}

		if ((key == A3D_TAB || key == A3D_ESCAPE) && !auto_rep)
		{
			if (!player.talk_box && key == A3D_TAB/* && ui.show_buts*/)
			{
				CancelItemContacts();
				//ui.show_buts = false;
				ui.TalkBox_blink = 32;
				player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
				memset(player.talk_box, 0, sizeof(TalkBox));
				player.talk_box->max_width = 33;
				player.talk_box->max_height = 7; // 0: off
				int s[2],p[2];
				player.talk_box->Reflow(s,p);
				player.talk_box->size[0] = s[0];
				player.talk_box->size[1] = s[1];
				player.talk_box->cursor_xy[0] = p[0];
				player.talk_box->cursor_xy[1] = p[1];
			}
			else
			if (player.talk_box)
			{
				//ui.show_buts = true;

				if (player.talk_box->len > 0 && key == A3D_TAB)
				{
					if (player.talks == 3)
					{
						free(player.talk[0].box);
						player.talks--;
						for (int i = 0; i < player.talks; i++)
							player.talk[i] = player.talk[i + 1];
					}

					if (player.talk_box->len>1 &&
						player.talk_box->buf[0]=='\\' && 
						player.talk_box->buf[1]!='\\')
					{
						// hacker mode
						akAPI_Exec(player.talk_box->buf+1, player.talk_box->len-1);
					}
					else
					{
						//ConvertToUTF8((char*)akAPI_Buff,player.talk_box->buf,player.talk_box->len); // TODO: [Backlog Ref] ConvertToUTF8((char*)akAPI_Buff,player.talk_box->buf,player.talk_box->len);
						bool allowed=false;
						if (!akAPI_OnSay(player.talk_box->buf, player.talk_box->len,&allowed) || allowed)
						{
							int idx = player.talks;
							player.talk[idx].box = player.talk_box;
							player.talk[idx].pos[0] = player.pos[0]; player.talk[idx].pos[1] = player.pos[1]; player.talk[idx].pos[2] = player.pos[2];
							player.talk[idx].stamp = stamp;

							if (server)
							{
								STRUCT_REQ_TALK req_talk = { 0 };
								req_talk.token = 'T';
								req_talk.len = player.talk[idx].box->len;
								memcpy(req_talk.str, player.talk[idx].box->buf, player.talk[idx].box->len);
								bool sent_talk = server->Send((const uint8_t*)&req_talk, 4 + req_talk.len);
								(void)sent_talk; // Local talk display is optimistic; web Send() records failures.
							}

							ChatLog("%s : %.*s\n", player.name, player.talk[player.talks].box->len, player.talk[player.talks].box->buf);
							player.talks++;

							// alloc new
							player.talk_box = 0;

							ui.TalkBox_blink = 32;
							player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
						}
						
						memset(player.talk_box, 0, sizeof(TalkBox));
						player.talk_box->max_width = 33;
						player.talk_box->max_height = 7; // 0: off
						int s[2], p[2];
						player.talk_box->Reflow(s, p);
						player.talk_box->size[0] = s[0];
						player.talk_box->size[1] = s[1];
						player.talk_box->cursor_xy[0] = p[0];
						player.talk_box->cursor_xy[1] = p[1];
					}
				}
				else
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
			}
			else
			if (ui.show_inventory && key == A3D_ESCAPE)
			{
				CancelItemContacts();
				ui.show_inventory = false;
			}
		}

		if (!player.talk_box && !auto_rep)
		{
			if (key == A3D_SPACE)
				input.jump = true;

			}

		// FL-3023 fix: always set the key bit, even on auto-repeat.
		// Previously, auto-repeat events (key+256) were gated by !auto_rep
		// and did NOT re-set the bit. After a focus-loss wipe (OnFocus(false)
		// memsets input to 0), held keys stayed dead because only auto-repeat
		// events arrive — the initial KEYB_DOWN already fired. This made
		// manual keyboard movement impossible during two-tab browser runs.
		// LINEAGE_JSON: {"fl":"FL-3023","commit":"pending","attempt":"remove !auto_rep guard so auto-repeat re-sets key bit after focus-loss wipe","result":"pending"}
		{
			input.key[key >> 3] |= 1 << (key & 0x7);
			static int fljit_input_set_logs = 0;
			if (fljit_input_set_logs < 48 &&
				(key == A3D_W || key == A3D_A || key == A3D_S || key == A3D_D))
			{
				printf("[FLJIT-INPUT-SET] key=%d auto=%d w=%d a=%d s=%d d=%d\n",
					key, auto_rep ? 1 : 0,
					input.IsKeyDown(A3D_W) ? 1 : 0,
					input.IsKeyDown(A3D_A) ? 1 : 0,
					input.IsKeyDown(A3D_S) ? 1 : 0,
					input.IsKeyDown(A3D_D) ? 1 : 0);
				fflush(stdout);
				fljit_input_set_logs++;
			}
		}

		// temp (also with auto_rep)
		ui.TalkBox_blink = 0;
		if (player.talk_box)
		{
			if (key == A3D_PAGEUP)
				player.talk_box->MoveCursorHead();
			if (key == A3D_PAGEDOWN)
				player.talk_box->MoveCursorTail();
			if (key == A3D_HOME)
				player.talk_box->MoveCursorHome();
			if (key == A3D_END)
				player.talk_box->MoveCursorEnd();
			if (key == A3D_LEFT)
				player.talk_box->MoveCursorX(-1);
			if (key == A3D_RIGHT)
				player.talk_box->MoveCursorX(+1);
			if (key == A3D_UP)
				player.talk_box->MoveCursorY(-1);
			if (key == A3D_DOWN)
				player.talk_box->MoveCursorY(+1);

			int mem_idx = -1;
			switch (key)
			{
				case A3D_F5: mem_idx = 0; break;
				case A3D_F6: mem_idx = 1; break;
				case A3D_F7: mem_idx = 2; break;
				case A3D_F8: mem_idx = 3; break;
			}

			if (mem_idx >= 0)
			{
				// if +shift then restore! // TODO: [Backlog Ref] if +shift then restore!
				bool left_shift = ((input.key[A3D_LSHIFT >> 3] | input.keyb_key[A3D_LSHIFT >> 3]) & (1 << (A3D_LSHIFT & 7))) != 0;
				bool right_shift = ((input.key[A3D_RSHIFT >> 3] | input.keyb_key[A3D_RSHIFT >> 3]) & (1 << (A3D_RSHIFT & 7))) != 0;
				if (left_shift || right_shift)
				{
					memset(player.talk_box, 0, sizeof(TalkBox));
					memcpy(player.talk_box->buf, ui.talk_mem[mem_idx].buf, 256);
					player.talk_box->len = ui.talk_mem[mem_idx].len;
					
					player.talk_box->max_width = 33;
					player.talk_box->max_height = 7; // 0: off!
					int s[2], p[2];
					if (player.talk_box->Reflow(s, p) >= 0)
					{
						player.talk_box->size[0] = s[0];
						player.talk_box->size[1] = s[1];
						player.talk_box->cursor_xy[0] = p[0];
						player.talk_box->cursor_xy[1] = p[1];
					}
				}
				else
				{
					// store, even if empty
					memcpy(ui.talk_mem[mem_idx].buf, player.talk_box->buf, 256);
					ui.talk_mem[mem_idx].len = player.talk_box->len;
					WriteConf(this);
				}
			}				
		}
		else
		{
			int mem_idx = -1;
			switch (key)
			{
				case A3D_F5: mem_idx = 0; break;
				case A3D_F6: mem_idx = 1; break;
				case A3D_F7: mem_idx = 2; break;
				case A3D_F8: mem_idx = 3; break;
			}

			if (mem_idx >= 0 && ui.talk_mem[mem_idx].len > 0)
			{
				player.Say(ui.talk_mem[mem_idx].buf, ui.talk_mem[mem_idx].len, stamp);
			}

			if (ui.show_inventory)
			{
				// Arrows always move the authoritative inventory focus. If an
				// ITEM_GRID_DRAG touch contact happens to still be active, also
				// advance the pixel offset so a concurrent finger-drag stays in
				// sync — but keyboard navigation is never blocked by a stale
				// touch state.
				if (input.contact[3].action == Input::Contact::ITEM_GRID_DRAG)
				{
					switch (key)
					{
						case A3D_UP:
							input.contact[3].pos[1]-=session.font_size[1];
							break;
						case A3D_DOWN:
							input.contact[3].pos[1]+=session.font_size[1];
							break;
						case A3D_LEFT:
							input.contact[3].pos[0]-=session.font_size[0];
							break;
						case A3D_RIGHT:
							input.contact[3].pos[0]+=session.font_size[0];
							break;
					}
				}
				switch (key)
				{
					case A3D_UP:
						MoveAuthoritativeInventoryFocus(this, -1);
						break;
					case A3D_DOWN:
						MoveAuthoritativeInventoryFocus(this, +1);
						break;
					case A3D_LEFT:
						MoveAuthoritativeInventoryFocus(this, -1);
						break;
					case A3D_RIGHT:
						MoveAuthoritativeInventoryFocus(this, +1);
						break;
				}
			}
		}
	}
	else
	if (keyb == GAME_KEYB::KEYB_UP)
	{
		input.key[key >> 3] &= ~(1 << (key & 0x7));
		static int fljit_input_clear_logs = 0;
		if (fljit_input_clear_logs < 48 &&
			(key == A3D_W || key == A3D_A || key == A3D_S || key == A3D_D))
		{
			printf("[FLJIT-INPUT-CLEAR] key=%d w=%d a=%d s=%d d=%d\n",
				key,
				input.IsKeyDown(A3D_W) ? 1 : 0,
				input.IsKeyDown(A3D_A) ? 1 : 0,
				input.IsKeyDown(A3D_S) ? 1 : 0,
				input.IsKeyDown(A3D_D) ? 1 : 0);
			fflush(stdout);
			fljit_input_clear_logs++;
		}
	}
	else
	if (keyb == GAME_KEYB::KEYB_CHAR)
	{
		input.last_hit_char = key;

		if (!player.talk_box)
		{
					if (key == '\n' || key == '\r')
					{
						debug.dbg_attack_key_attempts++;
						debug.dbg_attack_last_req_action_before = 0;
						bool attack_armed = StartManualAttack(server, debug, player, stamp, 0);
					if (attack_armed)
						debug.dbg_attack_setaction_success++;
					else
						debug.dbg_attack_setaction_fail++;
					debug.dbg_attack_last_req_action_after = 0;
				}

			if (key == '\\' || key == '|')
			{
				// session.perspective = !session.perspective;
				ToggleMenu(0);
			}

			if (key == '0')
			{
				if (UseAuthoritativeInventoryPanel(this))
				{
					ClampAuthoritativeInventoryFocus(this);
					DropItem(inventory_view.authoritative_inventory_focus);
				}
			}

			if (key=='y' || key=='Y')
			{
				if (UseAuthoritativeInventoryPanel(this))
				{
					ClampAuthoritativeInventoryFocus(this);
					DropItem(inventory_view.authoritative_inventory_focus);
					return;
				}
				return;
			}

			if (input.IsKeyDown(A3D_LCTRL) || input.IsKeyDown(A3D_RCTRL) ||
				input.IsKeyDown(A3D_LWIN) || input.IsKeyDown(A3D_RWIN))
			{
				if (key == '+' || key == '=')
				{
					camera.zoom *= 1.1f;
					if (camera.zoom > 5.0f) camera.zoom = 5.0f;
				}
				if (key == '-' || key == '_')
				{
					camera.zoom /= 1.1f;
					if (camera.zoom < 0.2f) camera.zoom = 0.2f;
				}
			}

			if (key == 'f' || key == 'F')
			{
				session.fly_mode = !session.fly_mode;
				ChatLog("Fly Mode: %s", session.fly_mode ? "ON" : "OFF");
			}

			if (key=='b' || key=='B' || key=='i' || key=='I')
			{
				if (ui.show_inventory)
				{
					CancelItemContacts();
					ui.show_inventory = false;
				}
				else
				{
					ui.show_inventory = true;
				}			
			}

				if (key == 'u' || key == 'U')
				{
					if ((server != 0))
				{
					if (UseAuthoritativeInventoryPanel(this))
					{
						ClampAuthoritativeInventoryFocus(this);
						if (!RequestUseAuthoritativeItemByIndex(this, inventory_view.authoritative_inventory_focus))
							ChatLog("AUTH ITEM MODE: use/equip request failed\n");
						}
						return;
					}
					if (server)
						return;
					if (inventory_view.focus >= 0)
					{
					if (ui.show_inventory)
					{
						CancelItemContacts();
						ExecuteItem(inventory_view.focus);
					}
					else
					{
						abort();
					}
				}
			}

			if (key == 'p' || key == 'P')
			{
				if (server != 0)
				{
					if (RequestPlaceEquippedPlaceableAuthoritativeItem(this))
						return;
					if (UseAuthoritativeInventoryPanel(this))
					{
						ClampAuthoritativeInventoryFocus(this);
						if (!RequestPlaceAuthoritativeItemByIndex(this, inventory_view.authoritative_inventory_focus))
							ChatLog("AUTH ITEM MODE: place request failed\n");
						return;
					}
					return;
				}
			}
			if (HandlePlaceHeightDebugKey(this, key))
				return;
		}

		if (key != 9) // we skip all TABs
		{
			if (key == '\r') // windows only? todo: check linux and browsers // TODO: [Backlog Ref] windows only? todo: check linux and browsers
				key = '\n';
			if ((key < 32 || key > 127) && key!=8 && key!='\n')
				return;

			// if type box is visible pass this input to it // TODO: [Backlog Ref] if type box is visible pass this input to it
			// printf("CH:%d (%c)\n", key, key); // TODO: [Backlog Ref] printf("CH:%d (%c)\n", key, key);

			ui.TalkBox_blink = 0;
			if (player.talk_box)
				player.talk_box->Input(key);
		}
	}
	else
	if (keyb == GAME_KEYB::KEYB_PRESS)
	{
		int mods = (key>>8) & 0xFF;
		key = key & 0xFF;

		if (key == A3D_ESCAPE)
		{
			// cancel all contacts
			for (int i = 0; i < 4; i++)
			{
				input.contact[i].action = Input::Contact::NONE;
			}
		}

		// it is like a KEYB_CHAR (not producing releases) but for non-printable keys
		// main input from terminals 
		// ....

		ui.TalkBox_blink = 0;
		if (player.talk_box)
		{
			if (key == A3D_PAGEUP)
				player.talk_box->MoveCursorHead();
			if (key == A3D_PAGEDOWN)
				player.talk_box->MoveCursorTail();
			if (key == A3D_HOME)
				player.talk_box->MoveCursorHome();
			if (key == A3D_END)
				player.talk_box->MoveCursorEnd();			
			if (key == A3D_LEFT)
				player.talk_box->MoveCursorX(-1);
			if (key == A3D_RIGHT)
				player.talk_box->MoveCursorX(+1);
			if (key == A3D_UP)
				player.talk_box->MoveCursorY(-1);
			if (key == A3D_DOWN)
				player.talk_box->MoveCursorY(+1);

			int mem_idx = -1;
			switch (key)
			{
				case A3D_F5: mem_idx = 0; break;
				case A3D_F6: mem_idx = 1; break;
				case A3D_F7: mem_idx = 2; break;
				case A3D_F8: mem_idx = 3; break;
			}

			if (mem_idx >= 0)
			{
				// mix of GUI/TERM mods, dirty but works!
				bool left_shift = ((input.key[A3D_LSHIFT >> 3] | input.keyb_key[A3D_LSHIFT >> 3]) & (1 << (A3D_LSHIFT & 7))) != 0;
				bool right_shift = ((input.key[A3D_RSHIFT >> 3] | input.keyb_key[A3D_RSHIFT >> 3]) & (1 << (A3D_RSHIFT & 7))) != 0;

				// if +shift then restore! // TODO: [Backlog Ref] if +shift then restore!
				if ((mods & 1) || left_shift || right_shift)
				{
					memset(player.talk_box, 0, sizeof(TalkBox));
					memcpy(player.talk_box->buf, ui.talk_mem[mem_idx].buf, 256);
					player.talk_box->len = ui.talk_mem[mem_idx].len;
					
					player.talk_box->max_width = 33;
					player.talk_box->max_height = 7; // 0: off!
					int s[2], p[2];
					if (player.talk_box->Reflow(s, p) >= 0)
					{
						player.talk_box->size[0] = s[0];
						player.talk_box->size[1] = s[1];
						player.talk_box->cursor_xy[0] = p[0];
						player.talk_box->cursor_xy[1] = p[1];
					}
				}
				else
				{
					// store, even if empty
					memcpy(ui.talk_mem[mem_idx].buf, player.talk_box->buf, 256);
					ui.talk_mem[mem_idx].len = player.talk_box->len;
					WriteConf(this);
				}
			}
		}
		else
		{
			int mem_idx = -1;
			switch (key)
			{
				case A3D_F5: mem_idx = 0; break;
				case A3D_F6: mem_idx = 1; break;
				case A3D_F7: mem_idx = 2; break;
				case A3D_F8: mem_idx = 3; break;
			}

			if (mem_idx >= 0 && ui.talk_mem[mem_idx].len > 0)
			{
				player.Say(ui.talk_mem[mem_idx].buf, ui.talk_mem[mem_idx].len, stamp);
			}

			int hold_key = 0;
			switch (key)
			{
				case 'w': case 'W': hold_key = A3D_W; break;
				case 'a': case 'A': hold_key = A3D_A; break;
				case 's': case 'S': hold_key = A3D_S; break;
				case 'd': case 'D': hold_key = A3D_D; break;
				case 'q': case 'Q': hold_key = A3D_Q; break;
				case 'e': case 'E': hold_key = A3D_E; break;
			}
			if (hold_key && input.IsKeyDown(hold_key))
			{
				static int fljit_char_hold_skip_logs = 0;
				if (fljit_char_hold_skip_logs < 32)
				{
					printf("[FLJIT-CHAR-HOLD-SKIP] char=%d hold_key=%d already_down=1\n", key, hold_key);
					fflush(stdout);
					fljit_char_hold_skip_logs++;
				}
			}
			else

			// simulate key down / up based on a time relaxation
			// for: QWEASD and cursor keys // TODO: [Backlog Ref] for: QWEASD and cursor keys

			// here: 
			// if new key is different than stored key // TODO: [Backlog Ref] if new key is different than stored key
			//   then: emulate stored KEY_UP and new KEY_DOWN
			// store current stamp
			// store new key

			// in render(): 
			// if there is stored key and time elapsed since it was pressed > thresh // TODO: [Backlog Ref] if there is stored key and time elapsed since it was pressed > thresh
			//   then: emulate stored KEY_UP and clear stored key

			{
				if (key != input.PressKey)
				{
					OnKeyb(GAME_KEYB::KEYB_UP, input.PressKey);
					input.PressKey = 0;

					// here we can filter keys
					if (key != A3D_TAB && (key<A3D_F5 || key>A3D_F8))
					{
						input.PressKey = key;
						input.PressStamp = stamp;
						OnKeyb(GAME_KEYB::KEYB_DOWN, input.PressKey);
					}
				}
				else
				{
					input.PressStamp = stamp; // - 500000 + 50000;
				}
			}
		}

		//if (key == A3D_TAB) // TODO: [Backlog Ref] if (key == A3D_TAB)
		if ((key == A3D_TAB || key == A3D_ESCAPE) /*&& !auto_rep*/)
		{
			// HANDLED BY EMULATION!
			if (!player.talk_box && key == A3D_TAB/* && ui.show_buts*/)
			{
				CancelItemContacts();
				//ui.show_buts = false;
				ui.TalkBox_blink = 32;
				player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
				memset(player.talk_box, 0, sizeof(TalkBox));
				player.talk_box->max_width = 33;
				player.talk_box->max_height = 7; // 0: off
				int s[2],p[2];
				player.talk_box->Reflow(s,p);
				player.talk_box->size[0] = s[0];
				player.talk_box->size[1] = s[1];
				player.talk_box->cursor_xy[0] = p[0];
				player.talk_box->cursor_xy[1] = p[1];
			}
			else
			{
				//ui.show_buts = true;

				if (player.talk_box->len > 0 && key == A3D_TAB)
				{
					if (player.talks == 3)
					{
						free(player.talk[0].box);
						player.talks--;
						for (int i = 0; i < player.talks; i++)
							player.talk[i] = player.talk[i + 1];
					}

					if (player.talk_box->len>1 &&
						player.talk_box->buf[0]=='\\' && 
						player.talk_box->buf[1]!='\\')
					{
						// hacker mode
						akAPI_Exec(player.talk_box->buf+1, player.talk_box->len-1);
					}
					else
					{
						//ConvertToUTF8((char*)akAPI_Buff,player.talk_box->buf,player.talk_box->len); // TODO: [Backlog Ref] ConvertToUTF8((char*)akAPI_Buff,player.talk_box->buf,player.talk_box->len);
						bool allowed=false;
						if (!akAPI_OnSay(player.talk_box->buf, player.talk_box->len,&allowed) || allowed)
						{
								int idx = player.talks;
								player.talk[idx].box = player.talk_box;
								player.talk[idx].pos[0] = player.pos[0]; player.talk[idx].pos[1] = player.pos[1]; player.talk[idx].pos[2] = player.pos[2];
								player.talk[idx].stamp = stamp;

							if (server)
							{
								STRUCT_REQ_TALK req_talk = { 0 };
								req_talk.token = 'T';
								req_talk.len = player.talk[idx].box->len;
								memcpy(req_talk.str, player.talk[idx].box->buf, player.talk[idx].box->len);
								bool sent_talk = server->Send((const uint8_t*)&req_talk, 4 + req_talk.len);
								(void)sent_talk; // Local talk display is optimistic; web Send() records failures.
							}

							ChatLog("%s : %.*s\n", player.name, player.talk[player.talks].box->len, player.talk[player.talks].box->buf);
							player.talks++;

							// alloc new
							player.talk_box = 0;

							ui.TalkBox_blink = 32;
							player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
						}
						
						memset(player.talk_box, 0, sizeof(TalkBox));
						player.talk_box->max_width = 33;
						player.talk_box->max_height = 7; // 0: off
						int s[2], p[2];
						player.talk_box->Reflow(s, p);
						player.talk_box->size[0] = s[0];
						player.talk_box->size[1] = s[1];
						player.talk_box->cursor_xy[0] = p[0];
						player.talk_box->cursor_xy[1] = p[1];
					}
				}
				else
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

			}


	}
}
}

void Game::StartContact(int id, int x, int y, int b)
{
	Input::Contact* con = input.contact + id;

	int cp[2] = { x,y };
	ScreenToCell(cp);

	bool hit = false;
	int mrg = 0;
	int cap = -1;
	float yaw = 0;
	Item* item = 0;

	if (/*ui.show_buts &&*/ cp[1] >= session.render_size[1] - 6 && (cp[0] < ui.bars_pos || cp[0] >= session.render_size[0] - ui.bars_pos) && b == 1)
	{
		// main but
		// perform action immediately
		// ...

		if (cp[0] >= session.render_size[0] - ui.bars_pos)
		{
			// temporarily switch ortho/session.perspective
			//session.perspective = !session.perspective;
			if (id==0)
				ToggleMenu(1);
			else
				ToggleMenu(2);

			// temporarily drop item
			/*
			if (inventory_view.my_items > 0)
			{
				// only if not in use
				if (!inventory_view.my_item[inventory_view.focus].in_use)
					DropItem(inventory_view.focus);
			}
			*/

			// TODO: // TODO: [Backlog Ref] TODO:
			// SHOW HELP!
		}
		else
		{
			if (ui.show_inventory)
			{
				CancelItemContacts();
				ui.show_inventory = false;
			}
			else
			{
				ui.show_inventory = true;
			}
			
		}

		// make contact dead
		con->action = Input::Contact::NONE;
		con->drag = b;
		con->pos[0] = x;
		con->pos[1] = y;
		con->drag_from[0] = x;
		con->drag_from[1] = y;

		con->item = item;
		con->keyb_cap = cap;
		con->margin = mrg;
		con->player_hit = hit;
		con->start_yaw = yaw;
		return;
	}

	if (ui.show_inventory && !player.talk_box)
	{
		if ((server != 0))
		{
			if (cp[0] >= inventory_view.layout_x &&
				cp[0] < inventory_view.layout_x + inventory_view.layout_width &&
				cp[1] >= inventory_view.layout_y &&
				cp[1] < inventory_view.layout_y + inventory_view.layout_height)
			{
				int auth_row = -1;
				if (HitAuthoritativeInventoryPanelRow(this, cp, &auth_row))
				{
					inventory_view.authoritative_inventory_focus = auth_row;
					ClampAuthoritativeInventoryFocus(this);
					inventory_view.animate_scroll = false;
					// Right click / alternate button drops immediately; left click selects.
					if (b != 1)
					{
						DropItem(inventory_view.authoritative_inventory_focus);
						con->action = Input::Contact::NONE;
					}
					else
					{
						con->action = Input::Contact::ITEM_GRID_CLICK;
						con->my_item = inventory_view.authoritative_inventory_focus;
					}
				}
				else
				{
					con->action = Input::Contact::NONE;
				}

				con->drag = b;
				con->pos[0] = x;
				con->pos[1] = y;
				con->drag_from[0] = x;
				con->drag_from[1] = y;
				con->item = item;
				con->keyb_cap = cap;
				con->margin = mrg;
				con->player_hit = hit;
				con->start_yaw = yaw;
				return;
			}
		}

		// if this is touch and theres another grid click touch // TODO: [Backlog Ref] if this is touch and theres another grid click touch
		// synthetize consumption / use / unuse
		if (id>0)
		{
			for (int i=1; i<4; i++)
			{
				if (i==id)
					continue;
				if (input.contact[i].action == Input::Contact::ITEM_GRID_CLICK)
				{
					if (!server)
						ExecuteItem(input.contact[i].my_item);
					input.contact[i].action = Input::Contact::NONE;
					con->action = Input::Contact::NONE;
					return;
				}
			}
		}

		bool can_scroll = false;
		bool inside = false;

		int _x = x, _y = y;

		{ // protect x,y
			int width = session.render_size[0];
			int height = session.render_size[1];
			Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

			// check if inside widget
			int left = inventory_view.layout_frame[0];
			int right = inventory_view.layout_frame[2];
			int upper = inventory_view.layout_frame[3];
			int lower = inventory_view.layout_frame[1];

			if (cp[0] >= inventory_view.layout_x && 
				cp[0] < inventory_view.layout_x + inventory_view.layout_width && 
				cp[1] >= inventory_view.layout_y && 
				cp[1] < inventory_view.layout_y + inventory_view.layout_height)
			{
				inside = true;

				if (cp[0] <= left || cp[0] >= right || cp[1] <= lower || cp[1] >= upper)
				{
					if (b==1)
						can_scroll = true;
				}
				else
				{
					int my_item = CheckPick(cp);
					// check for unused space
					if (my_item < 0 && b==1)
						can_scroll = true;
					else
					if (my_item >=0)
					{
						// if this item is not handled by any other contact // TODO: [Backlog Ref] if this item is not handled by any other contact

						bool can_click = true;

						for (int c = 0; c < 4; c++)
						{
							if (input.contact[c].action == Input::Contact::ITEM_LIST_CLICK ||
								input.contact[c].action == Input::Contact::ITEM_LIST_DRAG ||
								input.contact[c].action == Input::Contact::ITEM_GRID_CLICK ||
								input.contact[c].action == Input::Contact::ITEM_GRID_DRAG)
							{
								// if (my_item == input.contact->my_item) // TODO: [Backlog Ref] if (my_item == input.contact->my_item)
								// ENFORCED TO ONE SCROLL AND ONE CLICK/DRAG CONTACT AT ONCE
								{
									can_click = false;
									break;
								}
							}
						}

						if (can_click)
						{
							inventory_view.SetFocus(my_item);
							inventory_view.animate_scroll = true;

							// note: in case of mouse, it can be right or left click
							// left is for entire group move/remove, right is for 1 entity move/remove (split) 
							con->action = Input::Contact::ITEM_GRID_CLICK;
							con->my_item = my_item;
							con->item = inventory_view.my_item[my_item].item;

							con->drag = b;
							con->pos[0] = _x;
							con->pos[1] = _y;
							con->drag_from[0] = _x;
							con->drag_from[1] = _y;

							con->keyb_cap = cap;
							con->margin = mrg;
							con->player_hit = hit;
							con->start_yaw = yaw;
							return;
						}
					}
				}
			}
		}

		if (can_scroll)
		{
			for (int i = 0; i < 4; i++)
			{
				if (input.contact[i].action == Input::Contact::ITEM_GRID_SCROLL)
				{
					can_scroll = false;
					break;
				}
			}

			if (can_scroll)
			{
				inventory_view.animate_scroll = false;
				con->action = Input::Contact::ITEM_GRID_SCROLL;
				con->scroll = inventory_view.scroll;

				con->drag = b;
				con->pos[0] = x;
				con->pos[1] = y;
				con->drag_from[0] = x;
				con->drag_from[1] = y;

				con->item = item;
				con->keyb_cap = cap;
				con->margin = mrg;
				con->player_hit = hit;
				con->start_yaw = yaw;
				return;
			}
		}
		else
		if (inside)
		{
			con->action = Input::Contact::NONE;
			con->drag = b;
			con->pos[0] = x;
			con->pos[1] = y;
			con->drag_from[0] = x;
			con->drag_from[1] = y;

			con->item = item;
			con->keyb_cap = cap;
			con->margin = mrg;
			con->player_hit = hit;
			con->start_yaw = yaw;
			return;
		}
	}

	bool auth_world_strip = (server != 0);
	int world_pick_strip_count = auth_world_strip ?
		GetAuthoritativeWorldItemPickupStripCount(this) : inventory_view.items_count;
	if (world_pick_strip_count && cp[1] >= inventory_view.items_ylo && cp[1] <= inventory_view.items_yhi && b == 1)
	{
		// check item pickup
		if (cp[1] > inventory_view.items_ylo && cp[1] < inventory_view.items_yhi)
		{
			if (auth_world_strip)
			{
				int hit_slot = HitAuthoritativeWorldItemPickupStripSlot(
					this, cp[0], cp[1]);
				if (hit_slot >= 0)
				{
					// ensure no other contact with this item
					bool ok = true;
					for (int c = 0; c < 4; c++)
					{
						if (input.contact[c].action == Input::Contact::ITEM_LIST_CLICK ||
							input.contact[c].action == Input::Contact::ITEM_LIST_DRAG ||
							input.contact[c].action == Input::Contact::ITEM_GRID_CLICK ||
							input.contact[c].action == Input::Contact::ITEM_GRID_DRAG)
						{
							ok = false;
							break;
						}
					}

					// FL-4137 behavior 6 fix: was `if (!ok)` which gated pickup
					// to the conflicting-contact branch, blocking the normal
					// mouse/touch-click-on-pickup-strip path. Comment above says
					// "ensure no other contact with this item" — the safe-to-
					// pickup case is ok==true (no inventory drag/click conflict).
					if (ok)
					{
						RequestPickupAuthoritativeWorldItemByListIndex(
							this, hit_slot);

						con->action = Input::Contact::NONE;
						con->drag = b;
						con->pos[0] = x;
						con->pos[1] = y;
						con->drag_from[0] = x;
						con->drag_from[1] = y;

						con->item = item;
						con->keyb_cap = cap;
						con->margin = mrg;
						con->player_hit = hit;
						con->start_yaw = yaw;
						return;
					}
				}
			}
			else
			{
				for (int i = 0; i < world_pick_strip_count; i++)
				{
					if (cp[0] > inventory_view.items_xarr[i] && cp[0] < inventory_view.items_xarr[i + 1])
					{
						// ensure no other contact with this item
						bool ok = true;
						for (int c = 0; c < 4; c++)
						{
							if (input.contact[c].action == Input::Contact::ITEM_LIST_CLICK ||
								input.contact[c].action == Input::Contact::ITEM_LIST_DRAG ||
								input.contact[c].action == Input::Contact::ITEM_GRID_CLICK ||
								input.contact[c].action == Input::Contact::ITEM_GRID_DRAG)
							{
								ok = false;
								break;
							}
						}

						if (!ok)
							break;

						con->action = Input::Contact::ITEM_LIST_CLICK;
						con->item = inventory_view.items_inrange[i];

						con->drag = b;
						con->pos[0] = x;
						con->pos[1] = y;
						con->drag_from[0] = x;
						con->drag_from[1] = y;

						con->keyb_cap = cap;
						con->margin = mrg;
						con->player_hit = hit;
						con->start_yaw = yaw;
						return;
					}
				}
			}
		}

		// slightly missed (border / frame between items in list)
		if ((auth_world_strip &&
			IsWithinAuthoritativeWorldItemPickupStripBounds(this, cp[0], cp[1])) ||
			(!auth_world_strip &&
				cp[0] >= inventory_view.items_xarr[0] &&
				cp[0] <= inventory_view.items_xarr[world_pick_strip_count]))
		{
			con->action = Input::Contact::NONE;
			con->drag = b;
			con->pos[0] = x;
			con->pos[1] = y;
			con->drag_from[0] = x;
			con->drag_from[1] = y;

			con->item = item;
			con->keyb_cap = cap;
			con->margin = mrg;
			con->player_hit = hit;
			con->start_yaw = yaw;
			return;
		}
	}

	// FL-4137 Gap A: mobile tap-on-floating-preview => place via the
	// authoritative intent path. Ordered AFTER the world pickup strip block
	// above (so placed-block / loot pickup taps stay explicit pickups) and
	// BEFORE the keyboard / world-tap routing below (so a tap on the held
	// preview swallows the tap rather than triggering PLAYER double-tap or
	// FORCE movement). Same helper as desktop P / mobile player double-tap:
	// RequestPlaceEquippedPlaceableAuthoritativeItem emits
	// ITEM_ACTION_REQ_PLACE — the server is the sole validator and remains
	// the only writer of placement truth (Law 3, Law 6). Mobile-only and
	// primary-tap-only gate so desktop right-click and torque/force gestures
	// are untouched. The contact rect is published by the appearance pass and
	// is empty (valid=0) whenever no held placeable preview is on-screen.
	if (server && session.mobile_controls && b == 1)
	{
		const AuthoritativeHeldPreviewMobileContact& contact =
			authoritative.held_preview_mobile_contact;
		if (contact.valid &&
			cp[0] >= contact.cell_x0 && cp[0] <= contact.cell_x1 &&
			cp[1] >= contact.cell_y0 && cp[1] <= contact.cell_y1)
		{
			if (RequestPlaceEquippedPlaceableAuthoritativeItem(this))
			{
				con->action = Input::Contact::NONE;
				con->drag = b;
				con->pos[0] = x;
				con->pos[1] = y;
				con->drag_from[0] = x;
				con->drag_from[1] = y;
				con->item = item;
				con->keyb_cap = cap;
				con->margin = mrg;
				con->player_hit = hit;
				con->start_yaw = yaw;
				return;
			}
		}
	}

	{
		if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
		{
			bool left_shift = ((input.key[A3D_LSHIFT >> 3] | input.keyb_key[A3D_LSHIFT >> 3]) & (1 << (A3D_LSHIFT & 7))) != 0;
			bool right_shift = ((input.key[A3D_RSHIFT >> 3] | input.keyb_key[A3D_RSHIFT >> 3]) & (1 << (A3D_RSHIFT & 7))) != 0;
			bool shift_on = left_shift || right_shift;
			
			char ch=0;
			
			cap = keyb.GetCap(cp[0], cp[1], session.render_size[0], session.render_size[1], &ch, shift_on);

			if (left_shift && cap == A3D_RSHIFT)
			{
				keyb.plane = (keyb.plane + 1) % 3; // cycle++
				keyb.pad_plane = false; // prevent resetting by pad
			}
			else
			if (right_shift && cap == A3D_LSHIFT)
			{
				keyb.plane = (keyb.plane + 2) % 3; // cycle--
				keyb.pad_plane = false; // prevent resetting by pad
			}
			

			if (b!=1 && cap > 0)
				cap = 0;

			if (ch>=32 && ch<127 || ch==8 || ch=='\n')
				Buzz();

			if (cap>0)
			{
				// ensure one contact per keycap
				for (int i=0; i<4; i++)
				{
					if (i==id)
						continue;
					if (input.contact[i].action == Input::Contact::KEYBCAP && input.contact[i].keyb_cap == cap)
						cap = 0;
				}
			}

			if (cap > 0)
			{
				if (cap == A3D_LSHIFT)
				{
					if (id==0)
						input.keyb_key[cap >> 3] ^= 1 << (cap & 7);  // toggle shift
					else
						input.keyb_key[cap >> 3] |= 1 << (cap & 7);
				}
				else
				{
					if (ch)
					{
						if (ch == '\n' && !( input.keyb_key[A3D_LSHIFT >> 3] & (1 << (A3D_LSHIFT & 7)) ) )
						{
							if (player.talk_box->len > 0)
							{
								if (player.talks == 3)
								{
									free(player.talk[0].box);
									player.talks--;
									for (int i = 0; i < player.talks; i++)
										player.talk[i] = player.talk[i + 1];
								}

								if (player.talk_box->len>1 &&
									player.talk_box->buf[0]=='\\' && 
									player.talk_box->buf[1]!='\\')
								{
									// hacker mode
									akAPI_Exec(player.talk_box->buf+1, player.talk_box->len-1);
								}
								else
								{

									int idx = player.talks;
									player.talk[idx].box = player.talk_box;
									player.talk[idx].pos[0] = player.pos[0]; player.talk[idx].pos[1] = player.pos[1]; player.talk[idx].pos[2] = player.pos[2];
									player.talk[idx].stamp = stamp;

									if (server)
									{
										STRUCT_REQ_TALK req_talk = { 0 };
										req_talk.token = 'T';
										req_talk.len = player.talk[idx].box->len;
										memcpy(req_talk.str, player.talk[idx].box->buf, player.talk[idx].box->len);
										bool sent_talk = server->Send((const uint8_t*)&req_talk, 4 + req_talk.len);
										(void)sent_talk; // Local talk display is optimistic; web Send() records failures.
									}

									ChatLog("%s : %.*s\n", player.name, player.talk[player.talks].box->len, player.talk[player.talks].box->buf);
									player.talks++;

									// alloc new
									player.talk_box = 0;

									ui.TalkBox_blink = 32;
									player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
									memset(player.talk_box, 0, sizeof(TalkBox));
									player.talk_box->max_width = 33;
									player.talk_box->max_height = 7; // 0: off
									int s[2], p[2];
									player.talk_box->Reflow(s, p);
									player.talk_box->size[0] = s[0];
									player.talk_box->size[1] = s[1];
									player.talk_box->cursor_xy[0] = p[0];
									player.talk_box->cursor_xy[1] = p[1];
								}
							}

							ch = 0;
						}
						else
							OnKeyb(GAME_KEYB::KEYB_CHAR, ch); // like from terminal!
					}
					input.keyb_key[cap >> 3] |= 1 << (cap & 7);  // just to hilight keycap
				}
				con->keyb_cap = cap;

				// setup autorepeat initial delay...
				// not for shift
				input.KeybAutoRepCap = cap;
				input.KeybAuroRepDelayStamp = stamp;
				input.KeybAutoRepChar = ch; // must be nulled on any real keyb input!

				con->action = Input::Contact::KEYBCAP;
			}

			if (cap == 0)
				con->action = Input::Contact::NONE;
		}

		if (cap<0)
		{
			// ensure not on inventory_view
			Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

			int width = session.render_size[0];
			int height = session.render_size[1];

			// ensure not on inventory_view
			if (cp[0] < camera.scene_shift && 
				cp[1]>= inventory_view.layout_y && 
				cp[1]< inventory_view.layout_y+ inventory_view.layout_height)
			{
				con->action = Input::Contact::NONE;	
			}
			else
			if (id==0 && b==2)
			{
				// absolute mouse torque (mrg=0)
				con->action = Input::Contact::TORQUE;
				yaw = player.prev_yaw;

				// ensure no timer torque is pending
				for (int i=1; i<4; i++)
				{
					if (input.contact[i].action == Input::Contact::TORQUE)
					{
						con->action = Input::Contact::NONE;
						yaw = 0;
						break;
					}
				}
			}
			else
			{
				if (PlayerHit(player, session, camera.scene_shift, cp[0], cp[1]))
					con->action = Input::Contact::PLAYER;
				else
				// ADDED SCENE_SHIFT FOR LEFT-TORQUE TOUCH! id>0 guard removed: primary touch (id=0) now activates torque zone. Zone widened 5->8px for mobile.
				if (cp[0] < 8+camera.scene_shift && cp[0] > camera.scene_shift &&
					input.contact[0].action != Input::Contact::TORQUE)
				{
					mrg = -1;
					con->action = Input::Contact::TORQUE;
				}
				else
				if (cp[0] >= session.render_size[0]-8 &&
					input.contact[0].action != Input::Contact::TORQUE)
				{
					mrg = +1;
					con->action = Input::Contact::TORQUE;
				}
				else
				if (b==1)
				{
					con->action = Input::Contact::FORCE;

					// ensure no other contact is in force mode
					for (int i=0; i<4; i++)
					{
						if (i==id)
							continue;

						if (input.contact[i].action == Input::Contact::FORCE)
						{
							if (id != 0 && i != 0) // both are touches
							{
								int cp2[2] = { input.contact[i].drag_from[0], input.contact[i].drag_from[1] };
								ScreenToCell(cp2);
								
								if (2 * cp2[0] - camera.scene_shift < session.render_size[0])
								{
									input.jump = true;
								}
								else
								{
									input.jump = true;
								}
							}
							else
							{
								input.jump = true;
							}
							con->action = Input::Contact::NONE;
							break;
						}
					}
				}
				else
				{
					con->action = Input::Contact::NONE;				
				}
			}
		}
	}

	con->drag = b;

	con->pos[0] = x;
	con->pos[1] = y;
	con->drag_from[0] = x;
	con->drag_from[1] = y;

	con->item = item;
	con->keyb_cap = cap;
	con->margin = mrg;

	con->player_hit = hit;
	con->start_yaw = yaw;
}

void Game::MoveContact(int id, int x, int y)
{
	Input::Contact* con = input.contact + id;	
	con->pos[0] = x;
	con->pos[1] = y;

	switch (con->action)
	{
		case Input::Contact::ITEM_GRID_CLICK:
		{
			// if moved my 2 or more cells, change it into DRAG // TODO: [Backlog Ref] if moved my 2 or more cells, change it into DRAG
			int down[2] = { con->drag_from[0], con->drag_from[1] };
			ScreenToCell(down);

			int up[2] = { x, y };
			ScreenToCell(up);

			int rel[2] = { up[0] - down[0], up[1] - down[1] };
			if (rel[0] * rel[0] + rel[1] * rel[1] >= 4)
			{
				// turn into moving/splitting/removing item
				con->action = Input::Contact::ITEM_GRID_DRAG;
			}
			break;
		}

		case Input::Contact::ITEM_GRID_SCROLL:
		{
			int down[2] = { con->drag_from[0], con->drag_from[1] };
			ScreenToCell(down);

			int up[2] = { x, y };
			ScreenToCell(up);

			Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

			int scroll = con->scroll + up[1] - down[1];
			if (scroll < 0)
				scroll = 0;
			if (scroll > inventory_view.layout_max_scroll)
				scroll = inventory_view.layout_max_scroll;

			inventory_view.scroll = scroll;
			inventory_view.smooth_scroll = inventory_view.scroll;


			break;
		}
		case Input::Contact::ITEM_LIST_CLICK:
		{
			int cp[2] = { x, y };
			ScreenToCell(cp);

			// locate in list
			for (int i = 0; i < inventory_view.items_count; i++)
			{
				if (con->item == inventory_view.items_inrange[i])
				{
					if (cp[1] >= inventory_view.items_ylo && cp[1] <= inventory_view.items_yhi &&
						cp[0] > inventory_view.items_xarr[i] && cp[0] < inventory_view.items_xarr[i + 1])
					{
						// still inside item rect, nutting to do
						return;
					}

					// dragged out of the list
					con->action = Input::Contact::ITEM_LIST_DRAG;

					if (!ui.show_inventory) // TODO: [Backlog Ref] if (!ui.show_inventory)
						ui.show_inventory = true;

					return;
				}
			}

			// not in the list anymore
			con->action = Input::Contact::NONE;
			break;
		}

		case Input::Contact::PLAYER:
		{
			int down[2] = { con->drag_from[0], con->drag_from[1] };
			ScreenToCell(down);

			int up[2] = { x, y };
			ScreenToCell(up);

			up[0] -= down[0];
			up[1] -= down[1];

			int ph_cell[2] = { x, y };
		ScreenToCell(ph_cell);
		if (up[0] * up[0] > 1 || up[1] * up[1] > 1 || !PlayerHit(player, session, camera.scene_shift, ph_cell[0], ph_cell[1]))
			{
				con->action = Input::Contact::FORCE;
				for (int i=0; i<4; i++)
				{
					if (i==id)
						continue;
					if (input.contact[i].action == Input::Contact::FORCE)
					{
						con->action = Input::Contact::NONE;
						break;
					}
				}
			}
			break;
		}

		case Input::Contact::KEYBCAP:
		{			
			int cp[2] = { x,y };
			ScreenToCell(cp);
			int cap = keyb.GetCap(cp[0], cp[1], session.render_size[0], session.render_size[1], 0, false);

			if (cap != con->keyb_cap)
			{
				con->action = Input::Contact::NONE;
				
				int uncap = con->keyb_cap;
				if (uncap != A3D_LSHIFT || id!=0)
					input.keyb_key[uncap >> 3] &= ~(1 << (uncap & 7));  // un-hilight keycap

				if (uncap == input.KeybAutoRepCap)
				{
					input.KeybAutoRepCap = 0;
					input.KeybAutoRepChar = 0;
				}
			}
			break;

		}
	}
}

void Game::EndContact(int id, int x, int y)
{
	Input::Contact* con = input.contact + id;

	// any contact end must cancel autorep
	con->pos[0] = x;
	con->pos[1] = y;

	if ((server != 0))
	{
		switch (con->action)
		{
			case Input::Contact::ITEM_GRID_CLICK:
			case Input::Contact::ITEM_GRID_DRAG:
			{
				// Mobile tap on inventory_view grid equips the item via auth path.
				inventory_view.authoritative_inventory_focus = con->my_item;
				ClampAuthoritativeInventoryFocus(this);
				RequestUseAuthoritativeItemByIndex(this, inventory_view.authoritative_inventory_focus);
				con->action = Input::Contact::NONE;
				con->drag = 0;
				return;
			}
			case Input::Contact::ITEM_LIST_CLICK:
			case Input::Contact::ITEM_LIST_DRAG:
			{
				// The hit source may still carry a legacy local item pointer, but release
				// is always re-routed into the authoritative pickup request path.
				if (con->item)
					PickItem(con->item);
				con->action = Input::Contact::NONE;
				con->drag = 0;
				return;
			}
			default:
				break;
		}
	}

	switch (con->action)
	{
		case Input::Contact::TORQUE:
		{
			if (!server)
				ApplyLocalInputYawRelease(physics, player.prev_yaw, player.yaw_vel);
			else
				player.yaw_vel = 0.0f;
			break;
		}

		case Input::Contact::ITEM_GRID_CLICK:
		{
			// eat/use/unuse
			// (only with right click)
			if (con->drag==2 && !server)
				ExecuteItem(con->my_item);

			break;
		}

		case Input::Contact::ITEM_GRID_DRAG:
		{
			if (con->drag == 2 )
			{
				// SPLIT
			}

			int drop_at[2];
			if (CheckDrop(id, drop_at, 0, session.render_size[0], session.render_size[1]))
			{
				if (drop_at[0] < 0 || drop_at[1] < 0)
				{
					// REMOVE! (if not in use)
					if (!server && !inventory_view.my_item[con->my_item].in_use)
						DropItem(con->my_item);
					break;
				}

				}

			break;
		}

		case Input::Contact::ITEM_LIST_CLICK:
		case Input::Contact::ITEM_LIST_DRAG:
		{
			int cp[2] = { x, y };
			ScreenToCell(cp);

			// locate in list
			for (int i = 0; i < inventory_view.items_count; i++)
			{
				if (con->item == inventory_view.items_inrange[i])
				{
					if (con->action == Input::Contact::ITEM_LIST_CLICK)
					{
						if (cp[1] >= inventory_view.items_ylo && cp[1] <= inventory_view.items_yhi &&
							cp[0] > inventory_view.items_xarr[i] && cp[0] < inventory_view.items_xarr[i + 1])
						{
							// TRY TO PICK
							if (!server)
								PickItem(inventory_view.items_inrange[i]);
							break;
						}
					}

					break;
				}
			}

			break;
		}

		case Input::Contact::KEYBCAP:
		{
			// maybe we should clear it also when another cap is pressed?
			if (con->keyb_cap!=A3D_LSHIFT || id!=0)
				input.keyb_key[con->keyb_cap >> 3] &= ~(1 << (con->keyb_cap & 7));  // un-hilight keycap

			if (input.KeybAutoRepCap == con->keyb_cap)
			{
				input.KeybAutoRepCap = 0;
				input.KeybAutoRepChar = 0;
			}
			break;
		}

		case Input::Contact::PLAYER:
		{
			int down[2] = { con->drag_from[0], con->drag_from[1] };
			ScreenToCell(down);

			int up[2] = { x, y };
			ScreenToCell(up);

			up[0] -= down[0];
			up[1] -= down[1];

			if (up[0] * up[0] <= 1 && up[1] * up[1] <= 1)
			{
				if (server)
				{
					const uint64_t previous_tap = inventory_view.mobile_player_tap_stamp;
					const bool double_tap =
						previous_tap != 0 &&
						stamp >= previous_tap &&
						stamp - previous_tap <= MOBILE_PLAYER_PLACE_DOUBLE_TAP_US;
					inventory_view.mobile_player_tap_stamp = stamp;
					if (double_tap && RequestPlaceEquippedPlaceableAuthoritativeItem(this))
					{
						if (player.talk_box)
						{
							free(player.talk_box);
							player.talk_box = 0;
							ui.show_keyb = false;
							input.KeybAutoRepChar = 0;
							input.KeybAutoRepCap = 0;
						}
						break;
					}
				}
				if (player.talk_box)
				{
					// start showing main buts
					//ui.show_buts = true;

					// close talk_box (and keyb if also open)
					free(player.talk_box);
					player.talk_box = 0;
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
				}
				else
				//if (ui.show_buts) // TODO: [Backlog Ref] if (ui.show_buts)
				{
					CancelItemContacts();
					//ui.show_buts = false;
					// open talk_box (and keyb if not open)
					ui.TalkBox_blink = 32;
					player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
					memset(player.talk_box, 0, sizeof(TalkBox));
					player.talk_box->max_width = 33;
					player.talk_box->max_height = 7; // 0: off
					int s[2],p[2];
					player.talk_box->Reflow(s,p);
					player.talk_box->size[0] = s[0];
					player.talk_box->size[1] = s[1];
					player.talk_box->cursor_xy[0] = p[0];
					player.talk_box->cursor_xy[1] = p[1];
				
					ui.show_keyb = true;
				}
			}

			break;
		}
	}

	con->action = Input::Contact::NONE;
	con->drag = 0;
}

int Game::GetContact(int id)
{

	Input::Contact* con = input.contact + id;
	return con->drag;
}


// note:
//   only left-button is moved with mouse, 
//   other emu-touches remains on their initial pos

// #define TOUCH_EMU // TODO: [Backlog Ref] #define TOUCH_EMU


#ifdef TOUCH_EMU
int FirstFree(int size, int* arr)
{
	for (int id=1; id<=size; id++)
	{
		int i = 0;
		for (; i<size; i++)
			if (arr[i] == id)
				break;
		if (i==size)
			return id;
	}

	return -1;
}
#endif // TODO: [Backlog Ref] #endif

void Game::OnMouse(GAME_MOUSE mouse, int x, int y)
{
	/*
	if (mouse==MOUSE_LEFT_BUT_DOWN)
	{
		int freq = 100 + fast_rand()%100;
		CallAudio((uint8_t*)&freq,sizeof(freq));
	}
	*/

	// handle layers first ...

	if (ui.main_menu)
	{
		MainMenu_OnMouse(mouse,x,y);
		return;
	}

	if (ui.menu_depth>=0)
	{
		MenuMouse(mouse,x,y);
		return;
	}

	if (ui.show_gamepad)
	{
		int ev = -1;
		switch (mouse)
		{
			case GAME_MOUSE::MOUSE_LEFT_BUT_DOWN: ev = 0; break;
			case GAME_MOUSE::MOUSE_MOVE: ev = 1; break;
			case GAME_MOUSE::MOUSE_LEFT_BUT_UP: ev = 2; break;

			default:
				break;
		}

		if (ev>=0)
		{
			int p[2] = {x,y};
			ScreenToCell(p);
			GamePadContact(0,ev,p[0],p[1], stamp);
		}

		return;
	}

	#ifdef TOUCH_EMU
	static int buts_id[3] = {-1,-1,-1}; // L,R,M
	// emulate touches for easier testing
	switch (mouse)
	{
		case GAME_MOUSE::MOUSE_LEFT_BUT_DOWN: 
		case GAME_MOUSE::MOUSE_RIGHT_BUT_DOWN: 
		case GAME_MOUSE::MOUSE_MIDDLE_BUT_DOWN: 
		{
			int idx = ((int)mouse-1) >> 1;
			assert(buts_id[idx]<0);
			buts_id[idx] = FirstFree(3,buts_id);
			OnTouch(GAME_TOUCH::TOUCH_BEGIN,buts_id[idx],x,y);

			for (int i = 0; i < 1; i++)
				if (i != idx && buts_id[i]>0)
					OnTouch(GAME_TOUCH::TOUCH_MOVE,buts_id[i],x,y);
			
			break;
		}

		case GAME_MOUSE::MOUSE_LEFT_BUT_UP: 
		case GAME_MOUSE::MOUSE_RIGHT_BUT_UP: 
		case GAME_MOUSE::MOUSE_MIDDLE_BUT_UP: 
		{
			int idx = ((int)mouse-2) >> 1;
			if (buts_id[idx]>0)
			{
				OnTouch(GAME_TOUCH::TOUCH_END,buts_id[idx],x,y);
				buts_id[idx] = -1;

				for (int i = 0; i < 1; i++)
					if (i != idx && buts_id[i]>0)
						OnTouch(GAME_TOUCH::TOUCH_MOVE,buts_id[i],x,y);
			}

			break;
		}

		case GAME_MOUSE::MOUSE_MOVE: 
			for (int i = 0; i < 1; i++)
				if (buts_id[i]>0)
					OnTouch(GAME_TOUCH::TOUCH_MOVE,buts_id[i],x,y);
			break;
	}
	return;
	#endif // TODO: [Backlog Ref] #endif

	switch (mouse)
	{
		// they are handled
		// after switch !!!
		case MOUSE_WHEEL_DOWN:
			if (camera.scene_shift)
			{
				int cp[2] = { x,y };
				ScreenToCell(cp);
				// if mouse on x-visible part of inventory_view // TODO: [Backlog Ref] if mouse on x-visible part of inventory_view
				if (cp[0]<camera.scene_shift)
				{
					Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

					if (cp[1]>= inventory_view.layout_y && 
						cp[1]< inventory_view.layout_y+ inventory_view.layout_height)
					{
						inventory_view.animate_scroll = false;
						inventory_view.smooth_scroll += 5;
					}
				}
			}
			break;
		case MOUSE_WHEEL_UP:
			if (camera.scene_shift)
			{
				int cp[2] = { x,y };
				ScreenToCell(cp);
				// if mouse on x-visible part of inventory_view // TODO: [Backlog Ref] if mouse on x-visible part of inventory_view
				if (cp[0]<camera.scene_shift)
				{
					Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

					if (cp[1]>= inventory_view.layout_y && 
						cp[1]< inventory_view.layout_y+ inventory_view.layout_height)
					{
						inventory_view.animate_scroll = false;
						inventory_view.smooth_scroll -= 5;
					}
				}
			}
			break;

		case GAME_MOUSE::MOUSE_LEFT_BUT_DOWN: 

			if (input.but != 0)
			{
				if (input.contact[0].action == Input::Contact::TORQUE)
				{

					StartManualAttack(server, debug, player, stamp, 0);

					/*
					int cp[2] = { x,y };
					ScreenToCell(cp);
					input.shoot = true;
					input.shoot_xy[0] = cp[0];
					input.shoot_xy[1] = cp[1];
					// input.contact[0].action = Input::Contact::NONE;
					*/
				}

				input.but |= 0x1;
				break;
			}

			if (input.but == 0x0)
				StartContact(0, x,y, 1);
			else
				MoveContact(0, x,y);
			input.but |= 0x1;
			break;

		case GAME_MOUSE::MOUSE_LEFT_BUT_UP: 
			if (GetContact(0) == 1)
				EndContact(0, x,y);
			input.but &= ~0x1;
			break;

		case GAME_MOUSE::MOUSE_RIGHT_BUT_DOWN: 
			if (input.but == 0)
				StartContact(0, x,y, 2);
			else
			{
				if (input.contact[0].action == Input::Contact::FORCE)
				{
					input.jump = true;
				}
				MoveContact(0, x,y);
			}
			input.but |= 0x2;
			break;

		case GAME_MOUSE::MOUSE_RIGHT_BUT_UP: 
			if (GetContact(0) == 2)
				EndContact(0, x,y);
			input.but &= ~0x2;
			break;

		case GAME_MOUSE::MOUSE_MIDDLE_BUT_DOWN: 
			if (input.but == 0)
				StartContact(0, x,y, 3);
			else
				MoveContact(0, x,y);
			input.but |= 0x4;
			break;

		case GAME_MOUSE::MOUSE_MIDDLE_BUT_UP: 
			if (GetContact(0) == 3)
				EndContact(0, x,y);
			input.but &= ~0x4;
			break;

		case GAME_MOUSE::MOUSE_MOVE: 
			if (GetContact(0))
				MoveContact(0, x,y);
			break;
	}
}

void Game::OnTouch(GAME_TOUCH touch, int id, int x, int y)
{
	if (id<1 || id>3)
		return;
	/*
	if (touch==TOUCH_BEGIN)
	{
		int freq = 100 + fast_rand()%100;
		CallAudio((uint8_t*)&freq,sizeof(freq));
	}
	*/


	// handle layers first ...

	if (ui.main_menu)
	{
		MainMenu_OnTouch(touch,id,x,y);
		return;
	}

	if (ui.menu_depth>=0)
	{
		MenuTouch(touch,id,x,y);
		return;
	}


	if (ui.show_gamepad)
	{
		int ev = -1;
		switch (touch)
		{
			case GAME_TOUCH::TOUCH_BEGIN: ev = 0; break;
			case GAME_TOUCH::TOUCH_MOVE: ev = 1; break;
			case GAME_TOUCH::TOUCH_END: ev = 2; break;
			case GAME_TOUCH::TOUCH_CANCEL: ev = 3; break;
		}

		if (ev>=0)
		{
			int p[2] = {x,y};
			ScreenToCell(p);
			GamePadContact(id,ev,p[0],p[1], stamp);
		}

		return;
	}

	switch (touch)
	{
		case TOUCH_BEGIN:
			StartContact(id, x,y, 1);
			break;

			break;
		case TOUCH_MOVE:
			MoveContact(id, x,y);
			break;

		case TOUCH_END:
			EndContact(id, x,y);
			break;

		case TOUCH_CANCEL:
			if (input.contact[id].action == Input::Contact::KEYBCAP)
			{
				// this should be always true
				if (input.contact[id].keyb_cap!=A3D_LSHIFT || id!=0)
					input.keyb_key[input.contact[id].keyb_cap >> 3] &= ~(1 << (input.contact[id].keyb_cap & 7));  // un-hilight keycap
			 
				if (input.contact[id].keyb_cap == input.KeybAutoRepCap)
				{
					input.KeybAutoRepCap = 0;
					input.KeybAutoRepChar = 0;
				}
			}
			input.contact[id].drag = 0;
			input.contact[id].action = Input::Contact::NONE;
			break;
	}
}

void Game::OnFocus(bool set)
{
	// Focus loss is the authoritative local wipe for held-key state. Any browser/tab
	// reactivation fix must therefore either avoid this path or guarantee that a
	// later KEYB_DOWN/auto-repeat re-arms input.key[] before movement IO is built.
	// TODO: [Backlog Ref] if loosing focus, clear all tracking / dragging / keyboard states
	if (!set)
	{
		debug.dbg_focus_loss_count++;
		input.KeybAutoRepCap = 0;
		input.KeybAutoRepChar = 0;
		for (int i=0; i<4; i++)
		{
			input.contact[i].action = Input::Contact::NONE;
			input.contact[i].drag = 0;
		}

		int w = input.size[0], h = input.size[1];
		bool pad = input.pad_connected;
		memset(&input, 0, sizeof(Input));
		input.pad_connected = pad;
		input.size[0] = w;
		input.size[1] = h;
	}
	else
	{
		debug.dbg_focus_gain_count++;
	}

	if (ui.main_menu)
	{
		MainMenu_OnFocus(set);
	}
}

void Game::OnMessage(const uint8_t* msg, int len)
{
	// NET_TODO: // TODO: [Backlog Ref] TODO:
	// this is called by JS or game_app (already on game's thread)
}

void Game::OnPadMount(bool connect)
{
	input.pad_connected = connect;
	input.pad_button = 0;
	input.pad_autorep = 0;
	input.pad_item = 0;
	input.pad_stamp = stamp;
	memset(input.pad_axis, 0, sizeof(int16_t) * 32);
	if (connect)
		OnPadAxis(-1, 0);

	if (ui.menu_depth>=0)
	{
		MenuPadMount(connect);
	}

	if (ui.main_menu)
	{
		MainMenu_OnPadMount(connect);
		return;
	}	
}

void Game::OnPadButton(int b, bool down)
{
	if (ui.main_menu)
	{
		MainMenu_OnPadButton(b,down);
		return;
	}	

	if (ui.show_gamepad)
	{
		return;
	}		

	if (input.pad_autorep == 2+1 && !player.talk_box)
	{
		// clear and or ignore delete autorep if not in chat_box
		input.pad_autorep = 0;
		if (b==2 && !down)
			return;
	}

	if (b >= 0 && b < 32)
	{
		if (down)
		{
			if (b>=11 && b <=14 || b==2 && player.talk_box) // autorep dirpad and delete char in talk_box
			{
				if (input.pad_autorep != b+1)
				{
					input.pad_autorep = b+1; // +1 so mem-setting to 0 is fine
					input.pad_stamp = stamp;
				}
			}
			input.pad_button |= 1 << b;
		}
		else
		{
			input.pad_autorep = 0;
			input.pad_button &= ~(1 << b);
		}

		if (ui.menu_depth>=0)
		{
			// handle menu navi
			MenuPadButton(b,down);
			return;
		}

		if (down)
		{
			if (!player.talk_box)
			{
				switch (b)
				{
					case 0:
					{
						StartManualAttack(server, debug, player, stamp, 0);
						break;
					}

					case 1: 
					{
						input.jump = true;
						break;
					}

					case 2:
					{
						// item_grid (inventory_view ops)
						if (ui.show_inventory)
						{
							Input::Contact* con = input.contact+3;
							if ((server != 0))
							{
								if (con->action == Input::Contact::NONE)
								{
									ClampAuthoritativeInventoryFocus(this);
									con->action = Input::Contact::ITEM_GRID_CLICK;
									con->my_item = inventory_view.authoritative_inventory_focus;
								}
							}
							else if (con->action == Input::Contact::NONE && inventory_view.focus>=0)
							{
								// cancel all contacts
								// ... or prevent entering 'y' state if already there is item contact
								CancelItemContacts();

								// get xy from focused item
								int* xy = inventory_view.my_item[inventory_view.focus].xy;

								// keyb_y = 0
								// gamepad_x = 1
								// gamepad_y = 2

								int but = 0; // keyb_y
								// StartContact(3/*KEYB/PAD*/, xy[0],xy[1], but); // TODO: [Backlog Ref] StartContact(3/*KEYB/PAD*/, xy[0],xy[1], but);

								// synthetize contact
								
								// note: until dirpad we're in CLICK state (not DRAG)
								con->action = Input::Contact::ITEM_GRID_CLICK;

								con->item = inventory_view.my_item[inventory_view.focus].item;


								con->my_item = inventory_view.focus; // ?

								// calc synthetized scrren position for contact

								int ix = inventory_view.layout_x + xy[0]*4 + 4;
								int iy = inventory_view.layout_y + xy[1]*4 + 
										inventory_view.layout_height - 6 - (inventory_view.height*4-1) + 
										inventory_view.scroll;

								abort();

								// note: until dirpad we're in CLICK state (not DRAG)
								// shift slightly UP (so user can see we're in moving state)
								// iy++;

								// flip y axis (sceen coords are top to bottom)
								iy = session.render_size[1] - 1 - iy;	

								con->drag = 0; // button
								con->pos[0] = ix*session.font_size[0] + session.font_size[0]/2;
								con->pos[1] = iy*session.font_size[1] + session.font_size[1]/2;
								con->drag_from[0] = con->pos[0];
								con->drag_from[1] = con->pos[1];

								con->keyb_cap = -1;
								con->margin = 0;
								con->player_hit = false;
								con->start_yaw = 0;
							}
						}
						break;
					}

					case 3:
					{
						// item_list (pickup popup)
						Input::Contact* con = input.contact+3;
						if (con->action == Input::Contact::NONE && !player.talk_box && inventory_view.items_count)
						{
							// hilight first
							input.pad_item = 0+1;
						}
						break;
					}

					case 5:
					{
						if (ui.show_inventory)
						{
							// show gampad help for inventory_view operations
							// and item pick up
						}
						else
						{
							// show gamepad help for run, jump, attack
							// camera rot, open inventory_view, open chat
							// and item pick up
						}
						// lock processing any input until any key is pressed
						// then close this vidget
						break;
					}

					case 6:
					{
						// mini-menu
						//session.perspective = !session.perspective;
						//ui.show_buts = !ui.show_buts; // just test
						ToggleMenu(3);
						break;
					}

					case 9:
					{
						CancelItemContacts();
						ui.show_inventory = !ui.show_inventory;
						break;
					}

					case 10:
					{
						//if (ui.show_buts) // TODO: [Backlog Ref] if (ui.show_buts)
						{
							CancelItemContacts();
							//ui.show_buts = false;
							// open talk_box (and keyb if not open)
							ui.TalkBox_blink = 32;
							player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
							memset(player.talk_box, 0, sizeof(TalkBox));
							player.talk_box->max_width = 33;
							player.talk_box->max_height = 7; // 0: off
							int s[2],p[2];
							player.talk_box->Reflow(s,p);
							player.talk_box->size[0] = s[0];
							player.talk_box->size[1] = s[1];
							player.talk_box->cursor_xy[0] = p[0];
							player.talk_box->cursor_xy[1] = p[1];
				
							ui.show_keyb = true;
						}

						break;
					}

					case 11:
					{
						if (ui.show_inventory)
						{
							if (input.contact[3].action == Input::Contact::NONE)
								MoveAuthoritativeInventoryFocus(this, -1);
							else
							{
								input.contact[3].pos[1]-=session.font_size[1];
								if (input.contact[3].action == Input::Contact::ITEM_GRID_CLICK)
									input.contact[3].action = Input::Contact::ITEM_GRID_DRAG;
							}
						}
						break;
					}
					case 12:
					{
						if (ui.show_inventory)
						{
							if (input.contact[3].action == Input::Contact::NONE)
								MoveAuthoritativeInventoryFocus(this, +1);
							else
							{
								input.contact[3].pos[1]+=session.font_size[1];
								if (input.contact[3].action == Input::Contact::ITEM_GRID_CLICK)
									input.contact[3].action = Input::Contact::ITEM_GRID_DRAG;
							}
						}

						break;
					}
					case 13:
					{
						if (input.pad_item>1)
						{
							input.pad_item--;
						}
						else
						if (ui.show_inventory)
						{
							if (input.contact[3].action == Input::Contact::NONE)
								MoveAuthoritativeInventoryFocus(this, -1);
							else
							{
								input.contact[3].pos[0]-=session.font_size[0];
								if (input.contact[3].action == Input::Contact::ITEM_GRID_CLICK)
									input.contact[3].action = Input::Contact::ITEM_GRID_DRAG;
							}
						}
						break;
					}
					case 14:
					{
							int world_pick_strip_count = (server != 0) ?
								GetAuthoritativeWorldItemPickupStripCount(this) :
								inventory_view.items_count;
						if (input.pad_item && input.pad_item<world_pick_strip_count)
						{
							input.pad_item++;
						}
						else
						if (ui.show_inventory)
						{
							if (input.contact[3].action == Input::Contact::NONE)
								MoveAuthoritativeInventoryFocus(this, +1);
							else
							{
								input.contact[3].pos[0]+=session.font_size[0];
								if (input.contact[3].action == Input::Contact::ITEM_GRID_CLICK)
									input.contact[3].action = Input::Contact::ITEM_GRID_DRAG;
							}
						}
						break;
					}
				}
			}
			else
			{
				switch (b)
				{
					case 5: // guide / logo
					{
						// show gampad help for typing
						// lock processing any input until any key is pressed
						// then close this vidget
						break;
					}

					case 6: // start
					{
						// mini-menu
						//session.perspective = !session.perspective;
						//ui.show_buts = !ui.show_buts; // just test
						ToggleMenu(3);
						break;
					}

					case 9:
					{
						ui.show_inventory = !ui.show_inventory;
						break;
					}

					case 10:
					{
						// start showing main buts
						//ui.show_buts = true;

						// close talk_box (and keyb if also open)
						free(player.talk_box);
						player.talk_box = 0;
						if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
							memset(input.keyb_key, 0, 32);
						ui.show_keyb = false;
						input.KeybAutoRepChar = 0;
						input.KeybAutoRepCap = 0;
						for (int i = 0; i < 4; i++)
						{
							if (input.contact[i].action == Input::Contact::KEYBCAP)
								input.contact[i].action = Input::Contact::NONE;
						}
						break;
					}

					case 11:
						player.talk_box->MoveCursorY(-1);
						break;
					case 12:
						player.talk_box->MoveCursorY(+1);
						break;
					case 13:
						player.talk_box->MoveCursorX(-1);
						break;
					case 14:
						player.talk_box->MoveCursorX(+1);
						break;

					case 2: // backspace
						player.talk_box->Input(8);
						break;
					case 3: // SEND
					{
						Buzz();
						if (player.talk_box->len > 0)
						{
							if (player.talks == 3)
							{
								free(player.talk[0].box);
								player.talks--;
								for (int i = 0; i < player.talks; i++)
									player.talk[i] = player.talk[i + 1];
							}

							if (player.talk_box->len>1 &&
								player.talk_box->buf[0]=='\\' && 
								player.talk_box->buf[1]!='\\')
							{
								// hacker mode
								akAPI_Exec(player.talk_box->buf+1, player.talk_box->len-1);
							}
							else
							{
								int idx = player.talks;
								player.talk[idx].box = player.talk_box;
								player.talk[idx].pos[0] = player.pos[0]; player.talk[idx].pos[1] = player.pos[1]; player.talk[idx].pos[2] = player.pos[2];
								player.talk[idx].stamp = stamp;


								if (server)
								{
									STRUCT_REQ_TALK req_talk = { 0 };
									req_talk.token = 'T';
									req_talk.len = player.talk[idx].box->len;
									memcpy(req_talk.str, player.talk[idx].box->buf, player.talk[idx].box->len);
									bool sent_talk = server->Send((const uint8_t*)&req_talk, 4 + req_talk.len);
									(void)sent_talk; // Local talk display is optimistic; web Send() records failures.
								}

								ChatLog("%s : %.*s\n", player.name, player.talk[player.talks].box->len, player.talk[player.talks].box->buf);
								player.talks++;

								// alloc new
								player.talk_box = 0;

								ui.TalkBox_blink = 32;
								player.talk_box = (TalkBox*)malloc(sizeof(TalkBox));
								memset(player.talk_box, 0, sizeof(TalkBox));
								player.talk_box->max_width = 33;
								player.talk_box->max_height = 7; // 0: off
								int s[2], p[2];
								player.talk_box->Reflow(s, p);
								player.talk_box->size[0] = s[0];
								player.talk_box->size[1] = s[1];
								player.talk_box->cursor_xy[0] = p[0];
								player.talk_box->cursor_xy[1] = p[1];
							}
						}
						break;
					}	
				}
			}
		}
		else
		{
			// up!
			// todo: check if this button release should generate keyb char! // TODO: [Backlog Ref] todo: check if this button release should generate keyb char!
			// ...

			if (b==2)
			{
				if (input.contact[3].action!=Input::Contact::NONE)
				{
					if (input.contact[3].action==Input::Contact::ITEM_GRID_CLICK)
					{
						if ((server != 0))
						{
							ClampAuthoritativeInventoryFocus(this);
							RequestUseAuthoritativeItemByIndex(this, inventory_view.authoritative_inventory_focus);
						}
						else
						{
							ExecuteItem(inventory_view.focus);
						}
					}
					EndContact(3,input.contact[3].pos[0],input.contact[3].pos[1]);
				}
			}

			if (b==3)
			{
					int world_pick_strip_count = (server != 0) ?
						GetAuthoritativeWorldItemPickupStripCount(this) :
						inventory_view.items_count;
				if (input.pad_item>0 && input.pad_item<=world_pick_strip_count)
				{
					if ((server != 0))
						RequestPickupAuthoritativeWorldItemByListIndex(this, input.pad_item-1);
					else
						PickItem(inventory_view.items_inrange[input.pad_item-1]);
				}

				// reset item pickup hilight 
				input.pad_item = 0;
			}

			if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
			{
				if (b == 0 || b == 1)
				{
					bool shift_on = b == 1;
					char ch = 0;
					int key = keyb.GetPadCap(&ch,shift_on);
					if (ch)
					{
						Buzz();
						ui.TalkBox_blink = 0;
						if (player.talk_box)
							player.talk_box->Input(ch);
					}
				}
			}
		}
	}

	// just update states
	OnPadAxis(-1, 0);
}

void Game::OnPadAxis(int a, int16_t pos)
{
	if (ui.main_menu)
	{
		MainMenu_OnPadAxis(a,pos);
		return;
	}	

	if (ui.show_gamepad)
	{
		return;
	}		

	if (a>=0 && a<32)
		input.pad_axis[a] = pos;

	if (ui.menu_depth>=0)
	{
		// handle menu navi
		MenuPadAxis(a,pos);
		return;
	}

	//if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
	{
		if (ui.show_keyb && (input.pad_button & 3))
		{
			// locked plane and sect, change dir
			if (a == 0 || a == 1 || a == -1)
			{
				int dir = 11, ang;
				if (input.pad_axis[0] >= -20000 && input.pad_axis[0] <= +20000 &&
					input.pad_axis[1] >= -20000 && input.pad_axis[1] <= +20000)
				{
					dir = 0;
				}
				else
				{
					if (input.pad_axis[0] < 0) // left
					{
						ang = (int)(atan2(-input.pad_axis[1], -input.pad_axis[0]) / M_PI * 180);
						if (ang < -54)
							dir = 6;
						else
						if (ang < -18)
							dir = 7;
						else
						if (ang < +18)
							dir = 8;
						else
						if (ang < +54)
							dir = 9;
						else
							dir = 10;
					}
					else // right
					{
						ang = (int)(atan2(input.pad_axis[1], input.pad_axis[0]) / M_PI * 180);
						if (ang < -54)
							dir = 1;
						else
						if (ang < -18)
							dir = 2;
						else
						if (ang < +18)
							dir = 3;
						else
						if (ang < +54)
							dir = 4;
						else
							dir = 5;
					}
				}

				//printf("ang=%d, dir=%d\n", ang,dir); // TODO: [Backlog Ref] printf("ang=%d, dir=%d\n", ang,dir);
			}
		}
		else
		{
			// change sect, undefine dir
			if (a == 0 || a == -1)
			{
				if (input.pad_axis[0] < -10000)
					keyb.sect = 0;
				else
				if (input.pad_axis[0] > +10000)
					keyb.sect = 2;
				else
					keyb.sect = 1;
			}
			else
			if (a == 1 || a == -1)
			{
				if (input.pad_axis[1] < -10000)
				{
					keyb.plane = 1;
					keyb.pad_plane = true; // indicate we can reset plane by stick
				}
				else
				if (input.pad_axis[1] > +10000)
				{
					keyb.plane = 2;
					keyb.pad_plane = true; // indicate we can reset plane by stick
				}
				else
				{
					// prevent resetting plane to 0
					// if user set it with mouse or touch // TODO: [Backlog Ref] if user set it with mouse or touch
					// to something else
					if (keyb.pad_plane)
						keyb.plane = 0;
				}
			}
		}
	}
}

void Game::MenuKeyb(GAME_KEYB keyb, int key)
{
	MenuIO io = {};
	io.session = &session;
	io.ui = &ui;
	io.screen_size[0] = input.size[0];
	io.screen_size[1] = input.size[1];
	io.close_game = this;
	if (ui.menu_down)
		return; // captured by mouse/touch

	if (keyb==KEYB_DOWN && (key==A3D_ENTER || key==A3D_NUMPAD_ENTER))
	{
		// handle only char->press!
		return;
	}

	if (keyb==KEYB_CHAR && (key=='\\' || key=='|') ||
		(keyb==KEYB_DOWN || keyb==KEYB_PRESS) && key==A3D_ESCAPE)
	{
		CloseMenu();
		return;
	}

	if (keyb==KEYB_CHAR && key==8)
	{
		keyb=KEYB_PRESS;
		key=A3D_BACKSPACE;
	}

	if (keyb==KEYB_CHAR && (key=='\n' || key=='\r'))
	{
		keyb=KEYB_PRESS;
		key=A3D_ENTER;
	}

	if (keyb==KEYB_DOWN || keyb==KEYB_PRESS)
	{
		const Menu* m = game_menu;
		for (int d=0; d<ui.menu_depth; d++)
			m = m[ ui.menu_stack[d] ].sub;

		if (ui.menu_stack[ui.menu_depth]>=0)
		{
			if (key==A3D_RIGHT && m[ ui.menu_stack[ui.menu_depth] ].sub || key==A3D_ENTER)
			{
				if (m[ ui.menu_stack[ui.menu_depth] ].sub)
				{
					ui.menu_depth++;
					ui.menu_stack[ui.menu_depth]=0;
					ui.menu_temp = ui.menu_stack[ui.menu_depth];
				}
				else
				if (m[ ui.menu_stack[ui.menu_depth] ].action)
				{
					m[ ui.menu_stack[ui.menu_depth] ].action(&io);
				}
				return;
			}
		}
		else
		if (key==A3D_RIGHT || key==A3D_ENTER)
		{
			ui.menu_stack[ui.menu_depth]=ui.menu_temp;
		}

		if (key==A3D_LEFT || keyb==KEYB_PRESS && key==A3D_BACKSPACE)
		{
			if (ui.menu_depth==0)
			{
				CloseMenu();
				return;
			}
			ui.menu_depth--;
			ui.menu_temp = ui.menu_stack[ui.menu_depth];
			return;
		}

		if (key==A3D_DOWN)
		{
			if (ui.menu_stack[ui.menu_depth] < 0)
				ui.menu_stack[ui.menu_depth] = ui.menu_temp;
			else
			if (m[ui.menu_stack[ui.menu_depth]+1].str)
			{
				ui.menu_stack[ui.menu_depth]++;
				ui.menu_temp = ui.menu_stack[ui.menu_depth];
			}
			return;
		}

		if (key==A3D_UP)
		{
			if (ui.menu_stack[ui.menu_depth] < 0)
				ui.menu_stack[ui.menu_depth] = ui.menu_temp;
			else
			if (ui.menu_stack[ui.menu_depth]>0)
			{
				ui.menu_stack[ui.menu_depth]--;
				ui.menu_temp = ui.menu_stack[ui.menu_depth];
			}
			return;
		}
	}
}


void Game::MenuMouse(GAME_MOUSE mouse, int x, int y)
{
	MenuIO io = {};
	io.session = &session;
	io.ui = &ui;
	io.screen_size[0] = input.size[0];
	io.screen_size[1] = input.size[1];
	io.close_game = this;
	if (ui.menu_down==2)
		return; // captured by touch

	if (mouse == GAME_MOUSE::MOUSE_MOVE)
	{
		if (ui.menu_down)
		{
			// retest
			int hit = HitMenu(x,y);
			if (hit != ui.menu_stack[ui.menu_depth])
				ui.menu_stack[ui.menu_depth] = -1;
		}
	}

	if (mouse == GAME_MOUSE::MOUSE_LEFT_BUT_DOWN)
	{
		ui.menu_down = 1;

		int hit = HitMenu(x,y);
		if (hit<-1)
		{
			CloseMenu();
			return;						
		}

		if (hit>=0)
		{
			ui.menu_stack[ui.menu_depth]=hit;
			ui.menu_temp = ui.menu_stack[ui.menu_depth];
		}
		else
			ui.menu_stack[ui.menu_depth]=-1;

		return;
	}

	if (mouse == GAME_MOUSE::MOUSE_LEFT_BUT_UP)
	{
		if (ui.menu_down)
		{
			// retest
			int hit = HitMenu(x,y);
			if (hit == ui.menu_stack[ui.menu_depth])
			{
				if (hit==-1)
				{
					// go back
					if (ui.menu_depth==0)
					{
						CloseMenu();
						return;						
					}
					else
					{
						ui.menu_depth--;
						ui.menu_temp = ui.menu_stack[ui.menu_depth];
					}
				}
				else
				if (hit>=0)
				{
					const Menu* m = game_menu;
					for (int d=0; d<ui.menu_depth; d++)
						m = m[ ui.menu_stack[d] ].sub;		

					// action!
					if (m[ ui.menu_stack[ui.menu_depth] ].sub)
					{
						ui.menu_depth++;
						ui.menu_stack[ui.menu_depth]=-1; // clear next hilight
						ui.menu_temp = 0;
					}
					else
					if (m[ ui.menu_stack[ui.menu_depth] ].action)
					{
	MenuIO menu_io = {};
	menu_io.session = &session;
	menu_io.ui = &ui;
	menu_io.screen_size[0] = input.size[0];
	menu_io.screen_size[1] = input.size[1];
	menu_io.close_game = this;
						m[ ui.menu_stack[ui.menu_depth] ].action(&menu_io);
					}
				}
			}
		}

		ui.menu_down = 0;
		ui.menu_stack[ui.menu_depth]=-1;
	}
}

void Game::MenuTouch(GAME_TOUCH touch, int id, int x, int y)
{
	MenuIO io = {};
	io.session = &session;
	io.ui = &ui;
	io.screen_size[0] = input.size[0];
	io.screen_size[1] = input.size[1];
	io.close_game = this;
	if (ui.menu_down==1)
		return; // captured by mouse

	if (id==1)
	{
		switch(touch)
		{
			case GAME_TOUCH::TOUCH_BEGIN:
			{
				ui.menu_down = 2;
				int hit = HitMenu(x,y);
				if (hit<-1)
				{
					CloseMenu();
					return;						
				}

				if (hit>=0)
				{
					ui.menu_stack[ui.menu_depth]=hit;
					ui.menu_temp = ui.menu_stack[ui.menu_depth];
				}
				else
					ui.menu_stack[ui.menu_depth]=-1;

				break;
			}

			case GAME_TOUCH::TOUCH_MOVE:
				if (ui.menu_down)
				{
					// retest
					int hit = HitMenu(x,y);
					if (hit != ui.menu_stack[ui.menu_depth])
						ui.menu_stack[ui.menu_depth] = -1;
				}
				break;

			case GAME_TOUCH::TOUCH_END:
			{
				if (ui.menu_down)
				{
					// retest
					int hit = HitMenu(x,y);
					if (hit == ui.menu_stack[ui.menu_depth])
					{
						if (hit==-1)
						{
							// go back
							if (ui.menu_depth==0)
							{
								CloseMenu();
								return;						
							}
							else
							{
								ui.menu_depth--;
								ui.menu_temp = ui.menu_stack[ui.menu_depth];
							}
						}
						else
						if (hit>=0)
						{
							const Menu* m = game_menu;
							for (int d=0; d<ui.menu_depth; d++)
								m = m[ ui.menu_stack[d] ].sub;		

							// action!
							if (m[ ui.menu_stack[ui.menu_depth] ].sub)
							{
								ui.menu_depth++;
								ui.menu_stack[ui.menu_depth]=-1; // clear next hilight
								ui.menu_temp = 0;
							}
							else
							if (m[ ui.menu_stack[ui.menu_depth] ].action)
							{
	MenuIO menu_io = {};
	menu_io.session = &session;
	menu_io.ui = &ui;
	menu_io.screen_size[0] = input.size[0];
	menu_io.screen_size[1] = input.size[1];
	menu_io.close_game = this;
								m[ ui.menu_stack[ui.menu_depth] ].action(&menu_io);
							}
						}
					}
				}

				ui.menu_down = 0;
				ui.menu_stack[ui.menu_depth]=-1;				
				break;
			}

			case GAME_TOUCH::TOUCH_CANCEL:
				ui.menu_down = 0;
				ui.menu_stack[ui.menu_depth]=-1;
				break;
		}
	}
}

void Game::MenuPadMount(bool connected)
{
}

void Game::MenuPadButton(int b, bool down)
{
	MenuIO io = {};
	io.session = &session;
	io.ui = &ui;
	io.screen_size[0] = input.size[0];
	io.screen_size[1] = input.size[1];
	io.close_game = this;
	if (ui.menu_down)
		return; // captured by mouse/touch

	if (!down)
		return;

	const Menu* m = game_menu;
	for (int d=0; d<ui.menu_depth; d++)
		m = m[ ui.menu_stack[d] ].sub;		

	switch (b)
	{
		case 0:
		{
			if (ui.menu_stack[ui.menu_depth]>=0)
			{
				if (m[ ui.menu_stack[ui.menu_depth] ].sub)
				{
					ui.menu_depth++;
					ui.menu_stack[ui.menu_depth]=0;
					ui.menu_temp = ui.menu_stack[ui.menu_depth];
				}
				else
				if (m[ ui.menu_stack[ui.menu_depth] ].action)
				{
	MenuIO menu_io = {};
	menu_io.session = &session;
	menu_io.ui = &ui;
	menu_io.screen_size[0] = input.size[0];
	menu_io.screen_size[1] = input.size[1];
	menu_io.close_game = this;
					m[ ui.menu_stack[ui.menu_depth] ].action(&menu_io);
				}
			}
			else
				ui.menu_stack[ui.menu_depth]=ui.menu_temp;
			break;
		}

		case 1: 
		{
			// jump
			break;
		}

		case 5:
		{
			break;
		}

		case 6:
		{
			CloseMenu();
			break;
		}

		case 9:
		{
			// left shoulder
			break;
		}

		case 10:
		{
			// right shoulder
			break;
		}

		case 11:
		{
			// dir up
			if (ui.menu_stack[ui.menu_depth]<0)
				ui.menu_stack[ui.menu_depth]=ui.menu_temp;
			else
			if (ui.menu_stack[ui.menu_depth]>0)
			{
				ui.menu_stack[ui.menu_depth]--;			
				ui.menu_temp = ui.menu_stack[ui.menu_depth];
			}
			break;
		}
		case 12:
		{
			// dir down
			if (ui.menu_stack[ui.menu_depth]<0)
				ui.menu_stack[ui.menu_depth]=ui.menu_temp;
			else
			if (m[ui.menu_stack[ui.menu_depth]+1].str)
			{
				ui.menu_stack[ui.menu_depth]++;			
				ui.menu_temp = ui.menu_stack[ui.menu_depth];
			}
			break;
		}
		case 13:
		{
			// dir left
			if (ui.menu_depth==0)
			{
				CloseMenu();
				return;
			}
			ui.menu_depth--;
			ui.menu_temp = ui.menu_stack[ui.menu_depth];
			break;
		}
		case 14:
		{
			if (ui.menu_stack[ui.menu_depth]>=0)
			{
				// dir right
				// only sub, with dir_right
				// action requires main button
				if (m[ ui.menu_stack[ui.menu_depth] ].sub)
				{
					ui.menu_depth++;
					ui.menu_stack[ui.menu_depth]=0;
					ui.menu_temp = ui.menu_stack[ui.menu_depth];
				}
			}
			else
				ui.menu_stack[ui.menu_depth]=ui.menu_temp;
			break;
		}
	}
}

void Game::MenuPadAxis(int a, int16_t pos)
{
}

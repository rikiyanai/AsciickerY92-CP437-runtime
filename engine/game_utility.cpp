// game_utility.cpp -- config, path, environment helpers
// extracted from game.cpp
#include <string.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <ctype.h>
#if defined(_WIN32)
#include <process.h>
#define A3D_GETPID _getpid
#else
#include <unistd.h>
#define A3D_GETPID getpid
#endif
#include "game.h"
#include "game_utility.h"
#include "a3d_load_context.h"
#include "audio.h"
#include "physics.h"

extern char base_path[];
extern char g_loaded_a3d_path[1024];
extern "C" void SyncConf();
extern "C" const char* GetConfPath();
static uint32_t multiplayer_world_seed = 0;

// ---------------------------------------------------------------------------

float ReadEnvFloatOrDefault(const char* name, float fallback)
{
	const char* raw = getenv(name);
	if (!raw || !*raw)
		return fallback;
	char* end = 0;
	double value = strtod(raw, &end);
	if (!end || end == raw)
		return fallback;
	return (float)value;
}

void GetDefaultGameStart(float* water, float pos[3], float* yaw, float* dir, float lt[4])
{
	if (water)
		*water = ReadEnvFloatOrDefault("ASCIICKER_WATER_LEVEL", 55.0f);
	if (yaw)
		*yaw = ReadEnvFloatOrDefault("ASCIICKER_SPAWN_YAW", -57.4f);
	if (dir)
		*dir = ReadEnvFloatOrDefault("ASCIICKER_SPAWN_DIR", 0.0f);
	if (pos)
	{
		// WARNING (FL-2540): keep this path as the legacy fallback only. Native
		// selected-map startup now prefers the player-start loaded from the A3D
		// world contract; these env/global defaults remain for old maps and
		// direct/manual runs that still have no map-owned spawn record.
		pos[0] = ReadEnvFloatOrDefault("ASCIICKER_SPAWN_X", -2.8f);
		pos[1] = ReadEnvFloatOrDefault("ASCIICKER_SPAWN_Y", -73.6f);
		// World-space terrain on this map lives near 0xA000/40960, not z=0.
		// Keeping the bootstrap/default start in the same range lets stale-origin
		// guards reject bogus near-zero snapshots instead of briefly rendering
		// the player in water at the origin.
		pos[2] = ReadEnvFloatOrDefault("ASCIICKER_SPAWN_Z", 40960.0f);
	}
	if (lt)
	{
		lt[0] = 1.0f;
		lt[1] = 0.0f;
		lt[2] = 1.0f;
		lt[3] = 0.5f;
	}
}

uint8_t ConvertToCP437(uint32_t uc)
{
	static const uint8_t tab00A1[95]=
	{
		0xAD,0x9B,0x9C,0x00,0x9D,0x00,0x15,0x00,0x00,0xA6,0xAE,0xAA,0x00,0x00,0x00,0xF8,
		0xF1,0xFD,0x00,0x00,0xE6,0x14,0xFA,0x00,0x00,0xA7,0xAF,0xAC,0xAB,0x00,0xA8,0x00,
		0x00,0x00,0x00,0x8E,0x8F,0x92,0x80,0x00,0x90,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xA5,0x00,0x00,0x00,0x00,0x99,0x00,0x00,0x00,0x00,0x00,0x9A,0x00,0x00,0xE1,0x85,
		0xA0,0x83,0x00,0x84,0x86,0x91,0x87,0x8A,0x82,0x88,0x89,0x8D,0xA1,0x8C,0x8B,0x00,
		0xA4,0x95,0xA2,0x93,0x00,0x94,0xF6,0x00,0x97,0xA3,0x96,0x81,0x00,0x00,0x98
	};

	/*
	static const uint8_t tab0192[1]=
	{
		0x9F
	};
	*/

	static const uint8_t tab0393[52]=
	{
		0xE2,0x00,0x00,0x00,0x00,0xE9,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xE4,0x00,0x00,0xE8,0x00,0x00,0xEA,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xE0,0x00,
		0x00,0xEB,0xEE,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xE3,0x00,0x00,
		0xE5,0xE7,0x00,0xED
	};

	static const uint8_t tab2022[134]=
	{
		0x07,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x13,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xFC,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x9E
	};

	static const uint8_t tab2190[25]=
	{
		0x1B,0x18,0x1A,0x19,0x1D,0x12,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x17
	};

	static const uint8_t tab2219[77]=
	{
		0xF9,0xFB,0x00,0x00,0x00,0xEC,0x1C,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xEF,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xF7,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xF0,0x00,0x00,0xF3,0xF2
	};

	static const uint8_t tab2302[32]=
	{
		0x7F,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xA9,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xF4,0xF5
	};

	static const uint8_t tab2500[218]=
	{
		0xC4,0x00,0xB3,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xDA,0x00,0x00,0x00,
		0xBF,0x00,0x00,0x00,0xC0,0x00,0x00,0x00,0xD9,0x00,0x00,0x00,0xC3,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0xB4,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xC2,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0xC1,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xC5,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xCD,0xBA,0xD5,0xD6,0xC9,0xB8,0xB7,0xBB,0xD4,0xD3,0xC8,0xBE,0xBD,0xBC,0xC6,0xC7,
		0xCC,0xB5,0xB6,0xB9,0xD1,0xD2,0xCB,0xCF,0xD0,0xCA,0xD8,0xD7,0xCE,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xDF,0x00,0x00,0x00,0xDC,0x00,0x00,0x00,0xDB,0x00,0x00,0x00,0xDD,0x00,0x00,0x00,
		0xDE,0xB0,0xB1,0xB2,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0xFE,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x16,0x00,0x00,0x00,
		0x00,0x00,0x1E,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x10,0x00,0x1F,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x11,0x00,0x00,0x00,0x00,0x00,0x00,0x09,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x08,0x0A
	};

	static const uint8_t tab263A[50]=
	{
		0x01,0x02,0x0F,0x00,0x00,0x00,0x0C,0x00,0x0B,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
		0x00,0x00,0x00,0x00,0x00,0x00,0x06,0x00,0x00,0x05,0x00,0x03,0x04,0x00,0x00,0x00,
		0x0D,0x0E
	};

	uint8_t cp = 0x20;

	if (uc >= 0x2219)
	{
		if (uc < 0x2219 + sizeof(tab2219))
			cp = tab2219[uc - 0x2219];
		else
		if (uc >= 0x2302)
		{
			if (uc < 0x2302 + sizeof(tab2302))
				cp = tab2302[uc - 0x2302];
			if (uc >= 0x2500)
			{
				if (uc < 0x2500 + sizeof(tab2500))
					cp = tab2500[uc - 0x2500];
				else
				if (uc >= 0x263A)
				{
					if (uc < 0x263A + sizeof(tab263A))
						cp = tab263A[uc - 0x263A];
				}
			}
		}
	}
	else
	if (uc >= 0x0020)
	{
		if (uc < 0x7F)
			cp = (char)uc;
		else
		if (uc >= 0x00A1)
		{
			if (uc < 0x00A1 + sizeof(tab00A1))
				cp = tab00A1[uc - 0x00A1];
			else
			if (uc == 0x0192) // tab0192
				cp = 0x9F;
			else
			if (uc >= 0x0393)
			{
				if (uc < 0x0393 + sizeof(tab0393))
					cp = tab0393[uc - 0x0393];
				else
				if (uc >= 0x2022)
				{
					if (uc < 0x2022 + sizeof(tab2022))
						cp = tab2022[uc - 0x2022];
					else
					if (uc >= 0x2190)
					{
						if (uc < 0x2190 + sizeof(tab2190))
							cp = tab2190[uc - 0x2190];
					}
				}
			}
		}
	}

	return cp;
}

void ConvertToCP437(char* cp437, const char* _utf8, int maxlen)
{
	const uint8_t* utf8 = (const uint8_t*)_utf8;

	int i=0,j=0;
	while (j!=maxlen)
	{
		uint32_t uc;

		// unify, eat upto 4 bytes
		if (utf8[i]<128) // 01111111 (7bits)
		{
			uc = utf8[i];
			i++;
		}
		else
		if (utf8[i]<128+64) // 10xxxxxx (err)
		{
			// err
			i++;
			continue;
		}
		else
		if (utf8[i]<128+64+32) // 110xxxxx 10xxxxxx (11bits)
		{
			if ((utf8[i+1]>>6) != 2)
			{
				// err
				i++;
				continue;
			}

			uc = ((utf8[i]&0x3F)<<6) | (utf8[i+1]&0x3F);
			i+=2;
		}
		else
		if (utf8[i]<128+64+32+16) // 1110xxxx 10xxxxxx 10xxxxxx (16bits)
		{
			if ((utf8[i+1]>>6) != 2 || (utf8[i+2]>>6) != 2)
			{
				// err
				i++;
				continue;
			}

			uc = ((utf8[i]&0xF)<<12) | ((utf8[i+1]&0x3F)<<6) | (utf8[i+2]&0x3F);
			i+=3;
		}
		else
		if (utf8[i]<128+64+32+16+8) // 11110xxx 10xxxxxx 10xxxxxx 10xxxxxx (21bits)
		{
			if ((utf8[i+1]>>6) != 2 || (utf8[i+2]>>6) != 2 || (utf8[i+3]>>6) != 2)
			{
				// err
				i++;
				continue;
			}

			uc = ((utf8[i]&0x7)<<18) | ((utf8[i+1]&0x3F)<<12) | ((utf8[i+2]&0x3F)<<6) | (utf8[i+3]&0x3F);
			i+=4;
		}
		else // 11111xxx (err)
		{
			// err
			i++;
			continue;
		}

		if (uc==0)
		{
			cp437[j++] = 0;
			break;
		}

		uint8_t cp = ConvertToCP437(uc);
		cp437[j++] = (char)cp;
	}
}

const char* A3dTitleMapPath()
{
	if (g_loaded_a3d_path[0])
		return g_loaded_a3d_path;
	if (g_requested_a3d_path[0])
		return g_requested_a3d_path;
	return "-";
}

void ReadGitCodestateLabel(char* out, int out_size)
{
	static bool init = false;
	static char cached[128] = "";
	if (!out || out_size <= 0)
		return;
	if (!init)
	{
		init = true;
		snprintf(cached, sizeof(cached), "unknown");
#if defined(_WIN32)
		snprintf(cached, sizeof(cached), "git-unavailable");
#else
		if (base_path[0])
		{
			char cmd[1400];
			snprintf(
				cmd,
				sizeof(cmd),
				"git -C \"%s\" describe --always --dirty --broken --abbrev=8 2>/dev/null",
				base_path);
			FILE* pipe = popen(cmd, "r");
			if (pipe)
			{
				if (fgets(cached, sizeof(cached), pipe))
				{
					int len = (int)strlen(cached);
					while (len > 0 && (cached[len - 1] == '\n' || cached[len - 1] == '\r'))
					{
						cached[len - 1] = 0;
						len--;
					}
					if (!cached[0])
						snprintf(cached, sizeof(cached), "unknown");
				}
				pclose(pipe);
			}
		}
#endif
	}
	snprintf(out, out_size, "%s", cached[0] ? cached : "unknown");
}

bool IsAbsoluteA3dPath(const char* path)
{
	if (!path || !path[0])
		return false;
	if (path[0] == '/' || path[0] == '\\')
		return true;
#ifdef _WIN32
	if (((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z'))
		&& path[1] == ':'
		&& (path[2] == '\\' || path[2] == '/'))
		return true;
#endif
	return false;
}

const char* ResolveRequestedA3dPath(char* out, int out_size, const char* base_path)
{
	const char* rel = g_requested_a3d_path[0] ? g_requested_a3d_path : "assets/a3d/game_map_y8.a3d";
	if (!out || out_size <= 0)
		return rel;
	if (IsAbsoluteA3dPath(rel))
		snprintf(out, out_size, "%s", rel);
	else
		snprintf(out, out_size, "%s%s", base_path ? base_path : "", rel);
	out[out_size - 1] = 0;
	return out;
}

void BuildGameTermTitle(char* out, int out_size)
{
	if (!out || out_size <= 0)
		return;
	char codestate[128];
	ReadGitCodestateLabel(codestate, sizeof(codestate));
	// WARNING (FL-2541): the runtime title must come from live game state, not a
	// platform/editor hardcode. The loaded/requested A3D path is the only honest
	// map truth available until player-start and other front-door context are
	// fully map-owned.
	snprintf(out, out_size, "GAME TERM [%s] [%d] [%s]", codestate, (int)A3D_GETPID(), A3dTitleMapPath());
	out[out_size - 1] = 0;
}

uint32_t GetMultiplayerWorldSeed()
{
	return multiplayer_world_seed;
}

void SetMultiplayerWorldSeed(uint32_t seed)
{
	multiplayer_world_seed = seed;
}

void ChatLog(const char* fmt, ...)
{
	// move it to game_app/web/srv and asciid
	// we dont want to printf in -term mode!
	va_list args;
	va_start(args,fmt);
	vprintf(fmt,args);
	va_end(args);
}

void WriteJsonString(FILE* f, const char* str)
{
	fputc('\"', f);
	for (const char* p = str; *p; ++p)
	{
		if (*p == '\\' || *p == '\"')
			fputc('\\', f);
		fputc(*p, f);
	}
	fputc('\"', f);
}

void WriteShotJson(const char* path, uint64_t stamp, const PhysicsIO* io, const Game* g, int width, int height)
{
	if (!path || !io || !g)
		return;

	float lt[3] = { g->session.light[0], g->session.light[1], g->session.light[2] };
	float n = lt[0] * lt[0] + lt[1] * lt[1] + lt[2] * lt[2];
	if (n > 0.001f)
	{
		float inv = 1.0f / sqrtf(n);
		lt[0] *= inv;
		lt[1] *= inv;
		lt[2] *= inv;
	}

	FILE* f = fopen(path, "wb");
	if (!f)
		return;

	fprintf(f, "{\n");
	fprintf(f, "  \"version\": 1,\n");
	fprintf(f, "  \"stamp\": %llu,\n", (unsigned long long)stamp);
	fprintf(f, "  \"size\": {\"width\": %d, \"height\": %d},\n", width, height);
	if (g_loaded_a3d_path[0])
	{
		fprintf(f, "  \"map_path\": ");
		WriteJsonString(f, g_loaded_a3d_path);
		fprintf(f, ",\n");
	}
	else
		fprintf(f, "  \"map_path\": null,\n");
	fprintf(f, "  \"camera\": {\n");
	fprintf(f, "    \"pos\": [%.4f, %.4f, %.4f],\n", io->pos[0], io->pos[1], io->pos[2]);
	fprintf(f, "    \"yaw\": %.4f,\n", io->yaw);
	fprintf(f, "    \"zoom\": %.4f,\n", g->camera.zoom);
	fprintf(f, "    \"perspective\": %s,\n", g->session.perspective ? "true" : "false");
	fprintf(f, "    \"scene_shift\": %d,\n", g->camera.scene_shift);
	fprintf(f, "    \"cam_shift\": %d\n", g->camera.cam_shift);
	fprintf(f, "  },\n");
	float player_pos[3] = { g->player.pos[0], g->player.pos[1], g->player.pos[2] };
	float player_dir = g->player.dir;
	fprintf(f, "  \"player\": {\n");
	fprintf(f, "    \"pos\": [%.4f, %.4f, %.4f],\n", player_pos[0], player_pos[1], player_pos[2]);
	fprintf(f, "    \"dir\": %.4f\n", player_dir);
	fprintf(f, "  },\n");
	fprintf(f, "  \"light\": {\n");
	fprintf(f, "    \"dir\": [%.4f, %.4f, %.4f],\n", lt[0], lt[1], lt[2]);
	fprintf(f, "    \"ambience\": %.4f\n", g->session.light[3]);
	fprintf(f, "  },\n");
	fprintf(f, "  \"minimap\": {\n");
	fprintf(f, "    \"marker_visible_count\": %d,\n", g->debug.dbg_minimap_marker_visible_count);
	fprintf(f, "    \"marker_right_half_visible_count\": %d,\n", g->debug.dbg_minimap_marker_right_half_visible_count);
	fprintf(f, "    \"marker_label_chars_drawn\": %d,\n", g->debug.dbg_minimap_marker_label_chars_drawn);
	fprintf(f, "    \"marker_right_half_label_chars_drawn\": %d,\n", g->debug.dbg_minimap_marker_right_half_label_chars_drawn);
	fprintf(f, "    \"remote_expected_count\": %d,\n", g->debug.dbg_minimap_remote_expected_count);
	fprintf(f, "    \"remote_drawn_count\": %d\n", g->debug.dbg_minimap_remote_drawn_count);
	fprintf(f, "  },\n");
	fprintf(f, "  \"water\": %d,\n", g->session.water);
	// FL-2907 / FL-2378: mount and presentation diagnostics for visual regression tests
	fprintf(f, "  \"actor\": {\n");
	fprintf(f, "    \"mount_state\": %d,\n", g->debug.dbg_actor_mount_state);
	fprintf(f, "    \"presentation_kind_id\": %d,\n", (int)g->player.presentation_kind_id);
	fprintf(f, "    \"life_state\": %d,\n", g->debug.dbg_actor_life_state);
	fprintf(f, "    \"server_local_id\": %d,\n", g->debug.dbg_server_local_id);
	fprintf(f, "    \"mount_layer_count\": %d,\n", g->debug.dbg_actor_mount_layer_count);
	fprintf(f, "    \"render_layer_count\": %d,\n", g->debug.dbg_actor_render_layer_count);
	fprintf(f, "    \"grounded\": %d\n", g->debug.dbg_local_grounded);
	fprintf(f, "  }\n");
	fprintf(f, "}\n");
	fclose(f);
}

bool AutoShotFlagPath(char* out, int out_size)
{
	if (!out || out_size <= 0 || !base_path[0])
		return false;
	snprintf(out, out_size, "%s.run/auto-shot-on-first-frame.flag", base_path);
	return true;
}

bool AutoShotFlagPresent()
{
	char flag_path[1200];
	if (!AutoShotFlagPath(flag_path, sizeof(flag_path)))
		return false;
	FILE* flag = fopen(flag_path, "rb");
	if (!flag)
		return false;
	fclose(flag);
	return true;
}

void ConsumeAutoShotFlag()
{
	char flag_path[1200];
	if (!AutoShotFlagPath(flag_path, sizeof(flag_path)))
		return;
	remove(flag_path);
}

bool AutoShotOnFirstFrameEnabled()
{
	static int enabled = -1;
	if (enabled < 0)
	{
		enabled = AutoShotFlagPresent() ? 1 : 0;
		if (!enabled)
		{
			const char* raw = getenv("ASCIICKER_AUTO_SHOT_ON_FIRST_FRAME");
			enabled = (raw && raw[0] && strcmp(raw, "0") != 0) ? 1 : 0;
		}
	}
	return enabled == 1;
}

static bool g_observe_render_enabled = false;
static char g_observe_render_output_dir[1024] = {0};
static char g_observe_render_view_tuple_path[1024] = {0};
static char g_observe_render_schema_version[64] = {0};

void ConfigureObserveRender(const char* output_dir, const char* view_tuple_json_path, const char* schema_version)
{
	g_observe_render_enabled = (output_dir && output_dir[0]) ? true : false;
	g_observe_render_output_dir[0] = 0;
	g_observe_render_view_tuple_path[0] = 0;
	g_observe_render_schema_version[0] = 0;

	if (output_dir && output_dir[0])
	{
		strncpy(g_observe_render_output_dir, output_dir, sizeof(g_observe_render_output_dir) - 1);
		g_observe_render_output_dir[sizeof(g_observe_render_output_dir) - 1] = 0;
	}
	if (view_tuple_json_path && view_tuple_json_path[0])
	{
		strncpy(g_observe_render_view_tuple_path, view_tuple_json_path, sizeof(g_observe_render_view_tuple_path) - 1);
		g_observe_render_view_tuple_path[sizeof(g_observe_render_view_tuple_path) - 1] = 0;
	}
	if (schema_version && schema_version[0])
	{
		strncpy(g_observe_render_schema_version, schema_version, sizeof(g_observe_render_schema_version) - 1);
		g_observe_render_schema_version[sizeof(g_observe_render_schema_version) - 1] = 0;
	}
}

bool ObserveRenderEnabled()
{
	return g_observe_render_enabled;
}

const char* ObserveRenderOutputDir()
{
	return g_observe_render_output_dir;
}

const char* ObserveRenderViewTuplePath()
{
	return g_observe_render_view_tuple_path;
}

const char* ObserveRenderSchemaVersion()
{
	return g_observe_render_schema_version;
}

void ReadConf(Game* g)
{
	g->session.mobile_controls = true;
	FILE* f = fopen(GetConfPath(), "rb");
	if (f)
	{
		//printf("ReadConf ok\n"); // TODO: [Backlog Ref] printf("ReadConf ok\n");
		int r = (int)fread(g->ui.talk_mem, sizeof(UiState::TalkMem), 4, f);

		r = (int)fread(&g->session.perspective, 1, 1, f);
		r = (int)fread(&g->session.blood, 1, 1, f);
		r = (int)fread(&g->session.mute, 1, 1, f);
		r = (int)fread(&g->session.mobile_controls, 1, 1, f);

		fclose(f);
	}
	else
	{
		//printf("ReadConf err\n"); // TODO: [Backlog Ref] printf("ReadConf err\n");
	}

	// Manual/mobile authoritative combat must always come up with touch controls
	// enabled. Persisted config is allowed to store the old bit, but startup no
	// longer respects "off" because the touch attack path is a gameplay requirement.
	g->session.mobile_controls = true;

	// Apply persisted audio state after config is known. AudioMute(false) also
	// starts forest ambient, so InitAudio() does not race by playing before a
	// saved mute preference can be applied.
	fprintf(stderr, "[AUDIO] ReadConf: applying mute=%d from %s\n", (int)g->session.mute, GetConfPath());
	AudioMute(g->session.mute);
}

void WriteConf(Game* g)
{

	FILE* f = fopen(GetConfPath(), "wb");
	if (f)
	{
		//printf("WriteConf ok\n"); // TODO: [Backlog Ref] printf("WriteConf ok\n");
		fwrite(g->ui.talk_mem, sizeof(UiState::TalkMem), 4, f);

		fwrite(&g->session.perspective, 1, 1, f);
		fwrite(&g->session.blood, 1, 1, f);
		fwrite(&g->session.mute, 1, 1, f);
		fwrite(&g->session.mobile_controls, 1, 1, f);

		fclose(f);
	}
	else
	{
		//printf("WriteConf err\n"); // TODO: [Backlog Ref] printf("WriteConf err\n");
	}

	SyncConf();
}

void WriteConf(GameSession& session, UiState& ui)
{
	FILE* f = fopen(GetConfPath(), "wb");
	if (f)
	{
		fwrite(ui.talk_mem, sizeof(UiState::TalkMem), 4, f);

		fwrite(&session.perspective, 1, 1, f);
		fwrite(&session.blood, 1, 1, f);
		fwrite(&session.mute, 1, 1, f);
		fwrite(&session.mobile_controls, 1, 1, f);

		fclose(f);
	}

	SyncConf();
}

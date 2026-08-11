// game_render_bridge.cpp -- render bridge functions
// extracted from game.cpp
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include <stdlib.h>
#include <errno.h>
#include "game.h"
#include "game_utility.h"
#include "game_combat_client.h"
#include "network_ingest.h"
#include "a3d_load_context.h"
#include "local_player_authority.h"
#include "render.h"
#include "sprite.h"
#include "sprite_constants.h"
#include "world.h"
#include "font1.h"
#include "interaction_query.h"
#include "physics_query.h"
#include "authoritative_item_query_surface.h"
#include "authoritative_world_item_pickup_strip.h"
#include "authoritative_item_command_surface.h"
#include "actor_visual_profile_runtime.h"
#include "facing_space.h"
#include "render_frame_input.h"
#include "render_frame_report.h"
#include "material_glyph_plane.h"
#include "material_sidecar.h"
#include "actor_visual_profile_packet.h"
#include "snapshot_client/local_snapshot_presentation_track.h"
#include "remote_observer_probe.h"
#include "platform/time_backend.h"
#include "game_input.h"
#include "game_menu_ui.h"
#include "gamepad.h"
#include "mainmenu.h"
#include "enemygen.h"
#include "remote_actor_roster.h"
#include "weather.h"

static int ApplyRuntimeMaterialGlyphCell(void* user, int material_id, int elev, int shade, GlyphId glyph_id, uint16_t coverage)
{
	Material* materials = (Material*)user;
	if (!materials || material_id < 0 || material_id >= 256 || elev < 0 || elev >= 4 || shade < 0 || shade >= 16)
		return 1;
	if (!materials[material_id].glyph_plane)
	{
		materials[material_id].glyph_plane = material_glyph_plane_alloc();
		if (!materials[material_id].glyph_plane)
			return 1;
		material_glyph_plane_init(materials[material_id].glyph_plane);
	}
	const int idx = elev * 16 + shade;
	materials[material_id].glyph_plane->cells[idx] = glyph_id;
	materials[material_id].glyph_plane->coverage[idx] = coverage;
	return 0;
}

static bool LoadRuntimeMaterialGlyphSidecar(const char* map_path, Material* materials, const char* prefix)
{
	char errbuf[512] = "";
	int applied_cells = 0;
	if (material_sidecar_load_apply_for_map(map_path, ApplyRuntimeMaterialGlyphCell, materials, prefix, &applied_cells, errbuf, sizeof(errbuf)) != 0)
		return false;
	if (applied_cells > 0)
		printf("%s Runtime material glyph sidecar applied cells=%d\n", prefix ? prefix : "[GAME]", applied_cells);
	return true;
}

struct ObserveRenderViewTuple
{
	bool valid = false;
	float cam_pos[3] = {0, 0, 0};
	float cam_yaw = 0.0f;
	float cam_zoom = 1.0f;
	bool perspective = false;
	int scene_shift = 0;
	float light[4] = {0, 0, 0, 0};
	int water = 0;
};

static bool ExtractJsonFloatValue(const char* json, const char* key, float* out)
{
	if (!json || !key || !out)
		return false;
	char needle[128];
	int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
	if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
		return false;
	const char* pos = strstr(json, needle);
	if (!pos)
		return false;
	pos += needle_len;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != ':')
		return false;
	pos++;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	char* end_ptr = nullptr;
	double value = strtod(pos, &end_ptr);
	if (end_ptr == pos)
		return false;
	*out = (float)value;
	return true;
}

static bool ExtractJsonIntValue(const char* json, const char* key, int* out)
{
	if (!json || !key || !out)
		return false;
	char needle[128];
	int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
	if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
		return false;
	const char* pos = strstr(json, needle);
	if (!pos)
		return false;
	pos += needle_len;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != ':')
		return false;
	pos++;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	char* end_ptr = nullptr;
	long value = strtol(pos, &end_ptr, 10);
	if (end_ptr == pos)
		return false;
	*out = (int)value;
	return true;
}

static bool ExtractJsonBoolValue(const char* json, const char* key, bool* out)
{
	if (!json || !key || !out)
		return false;
	char needle[128];
	int needle_len = snprintf(needle, sizeof(needle), "\"%s\"", key);
	if (needle_len <= 0 || (size_t)needle_len >= sizeof(needle))
		return false;
	const char* pos = strstr(json, needle);
	if (!pos)
		return false;
	pos += needle_len;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (*pos != ':')
		return false;
	pos++;
	while (*pos == ' ' || *pos == '\t' || *pos == '\r' || *pos == '\n')
		pos++;
	if (strncmp(pos, "true", 4) == 0)
	{
		*out = true;
		return true;
	}
	if (strncmp(pos, "false", 5) == 0)
	{
		*out = false;
		return true;
	}
	return false;
}

static bool ExtractJsonFloat3InObject(const char* json, const char* object_key, const char* array_key, float out3[3])
{
	if (!json || !object_key || !array_key)
		return false;
	char obj_needle[128];
	int obj_len = snprintf(obj_needle, sizeof(obj_needle), "\"%s\"", object_key);
	if (obj_len <= 0 || (size_t)obj_len >= sizeof(obj_needle))
		return false;
	const char* obj = strstr(json, obj_needle);
	if (!obj)
		return false;
	const char* brace = strchr(obj, '{');
	if (!brace)
		return false;
	const char* end = strchr(brace, '}');
	if (!end)
		return false;

	char key_needle[128];
	int key_len = snprintf(key_needle, sizeof(key_needle), "\"%s\"", array_key);
	if (key_len <= 0 || (size_t)key_len >= sizeof(key_needle))
		return false;
	const char* key_pos = strstr(brace, key_needle);
	if (!key_pos || key_pos > end)
		return false;
	const char* array_pos = strchr(key_pos, '[');
	if (!array_pos || array_pos > end)
		return false;
	float a = 0, b = 0, c = 0;
	int read = sscanf(array_pos, " [ %f , %f , %f", &a, &b, &c);
	if (read != 3)
		return false;
	out3[0] = a;
	out3[1] = b;
	out3[2] = c;
	return true;
}

static bool LoadObserveRenderViewTuple(const char* path, ObserveRenderViewTuple* out)
{
	if (!out)
		return false;
	*out = ObserveRenderViewTuple();
	if (!path || !path[0])
		return false;
	FILE* f = fopen(path, "rb");
	if (!f)
		return false;
	fseek(f, 0, SEEK_END);
	long sz = ftell(f);
	fseek(f, 0, SEEK_SET);
	if (sz <= 0 || sz > 1024 * 1024)
	{
		fclose(f);
		return false;
	}
	char* buf = (char*)malloc((size_t)sz + 1);
	if (!buf)
	{
		fclose(f);
		return false;
	}
	size_t got = fread(buf, 1, (size_t)sz, f);
	fclose(f);
	buf[got] = 0;

	ObserveRenderViewTuple t;
	bool ok = true;
	ok = ok && ExtractJsonFloat3InObject(buf, "camera", "pos", t.cam_pos);
	ok = ok && ExtractJsonFloatValue(strstr(buf, "\"camera\""), "yaw", &t.cam_yaw);
	ok = ok && ExtractJsonFloatValue(strstr(buf, "\"camera\""), "zoom", &t.cam_zoom);
	ok = ok && ExtractJsonBoolValue(strstr(buf, "\"camera\""), "perspective", &t.perspective);
	ok = ok && ExtractJsonIntValue(strstr(buf, "\"camera\""), "scene_shift", &t.scene_shift);
	ok = ok && ExtractJsonFloat3InObject(buf, "light", "dir", t.light);
	ok = ok && ExtractJsonFloatValue(strstr(buf, "\"light\""), "ambience", &t.light[3]);
	ok = ok && ExtractJsonIntValue(buf, "water", &t.water);

	free(buf);
	if (!ok)
		return false;
	t.valid = true;
	*out = t;
	return true;
}

static void WriteResolvedCellsJsonl(FILE* f, const AnsiCell* ptr, int width, int height)
{
	if (!f || !ptr || width <= 0 || height <= 0)
		return;
	for (int y = 0; y < height; y++)
	{
		for (int x = 0; x < width; x++)
		{
			const AnsiCell* c = ptr + y * width + x;
			fprintf(f,
				"{\"x\":%d,\"y\":%d,\"glyph_codepoint\":%u,\"fg_palette_idx\":%u,\"bg_palette_idx\":%u,\"spare\":%u}\n",
				x, y,
				(unsigned)c->gl,
				(unsigned)c->fg,
				(unsigned)c->bk,
				(unsigned)c->spare);
		}
	}
}
#include "physics_step.h"
#include "platform/input_backend.h"
#include "authoritative_presentation_adapters.h"
#include "snapshot_client/snapshot_npc_visual_lifecycle.h"
#include "remote_authoritative_presentation_lifecycle.h"
#include "game_api.h"
#include "sprite_registry.h"
#include "a3d_load_context.h"
#if !defined(_WIN32)
#include <unistd.h>
#include <fcntl.h>
#endif
#include <cstdlib>

extern Keyb keyb;
#if !defined(__EMSCRIPTEN__)
void StopNormalGameAuthoritativeSession();
#endif
#if !defined(PURE_TERM) && !defined(__EMSCRIPTEN__)
extern "C" int a3dQueueSdlQuit();
#endif

extern bool auto_shot_fired;
extern const Menu game_menu[];
extern uint32_t g_web_render_stage_code;
// FL-2957: total client render duration already proved multi-second stalls.
// Keep per-slice timings at the render bridge so the next run can identify the
// real client owner instead of reviving transport or watchdog families.
uint32_t g_web_render_interaction_query_duration_us = 0;
uint32_t g_web_render_status_bar_duration_us = 0;
uint32_t g_web_render_talk_overlay_duration_us = 0;
uint32_t g_web_render_player_overlay_duration_us = 0;
uint32_t g_web_render_deferred_terrain_dark_duration_us = 0;
uint32_t g_web_render_core_world_duration_us = 0;
uint32_t g_web_render_weather_duration_us = 0;
uint32_t g_web_render_minimap_duration_us = 0;
uint32_t g_web_render_api_hook_duration_us = 0;
uint32_t g_web_render_remote_duplicate_purge_duration_us = 0;
uint32_t g_web_render_remote_duplicate_purge_deleted_count = 0;
uint32_t g_web_render_snapshot_npc_visual_lifecycle_duration_us = 0;
uint32_t g_web_render_snapshot_npc_visual_lifecycle_slots = 0;
uint32_t g_web_render_authoritative_item_appearance_duration_us = 0;
uint32_t g_web_render_authoritative_item_appearance_slots = 0;
uint32_t g_web_render_frame_input_npc_visual_copy_duration_us = 0;
uint32_t g_web_render_frame_input_npc_visual_copy_slots = 0;
uint32_t g_web_render_lag_probe_sent_this_frame = 0;
uint32_t g_web_render_lag_probe_send_stage_code = 0;
uint32_t g_web_render_lag_probe_send_to_render_end_us = 0;
uint32_t g_web_render_lag_probe_send_seq = 0;
extern void PaintLocalAuthorityHoldScreen(AnsiCell* ptr, int width, int height);
static const int MP_MAX_HP = 100;
#include "hp_bar.h"

// FL-3955 S-4: TWO independent slot_kind_id switches. Same enum, two purposes.
// FL-3955 S-4 FIXED: Delegates to SlotKindToAttachmentBitmask in protocol_common.h.
// The shared function is the single authority, not this local switch.
static inline uint32_t TrackedNpcAttachmentMaskBit(uint16_t slot_kind_id)
{
	return SlotKindToAttachmentBitmask(slot_kind_id);
}

static uint16_t CountSpriteVisibleCellsForFrame(Sprite* sprite, int anim, int frame)
{
	if (!sprite || sprite->anims <= 0 || sprite->angles <= 0)
		return 0;
	if (anim < 0)
		anim = 0;
	if (anim >= sprite->anims)
		anim = sprite->anims - 1;
	int len = sprite->anim[anim].length;
	if (len <= 0)
		return 0;
	if (frame < 0)
		frame = 0;
	else
		frame %= len;
	uint32_t max_visible = 0;
	for (int angle = 0; angle < sprite->angles; angle++)
	{
		int atlas_index = frame + angle * len;
		Sprite::Frame* f = sprite->atlas + sprite->anim[anim].frame_idx[atlas_index];
		uint32_t visible = 0;
		for (int i = 0; i < f->width * f->height; i++)
		{
			const AnsiCell* cell = f->cell + i;
			const bool src_empty =
				(cell->bk == SPRITE_TRANSPARENT_INDEX && cell->fg == SPRITE_TRANSPARENT_INDEX) ||
				((cell->gl == 32 || cell->gl == 0) && cell->bk == SPRITE_TRANSPARENT_INDEX) ||
				(cell->gl == 219 && cell->fg == SPRITE_TRANSPARENT_INDEX);
			if (!src_empty)
				visible++;
		}
		if (visible > max_visible)
			max_visible = visible;
	}
	return max_visible > 65535u ? 65535u : (uint16_t)max_visible;
}
#include "minimap_renderer.h"
extern bool EnsureNormalGameAuthoritativeSession(const char* user, const char* map_path);
#ifdef __EMSCRIPTEN__
extern "C" void WebGlyphSidecarBeginFrame(int render_w, int render_h);
#endif

// Minimap helpers (defined in minimap_renderer.h)
struct MinimapMarker;

// Globals and helpers defined in game.cpp
extern Character* player_head;
void RetireLocalNpcActorsForAuthoritativeMode(Game* g);
void EnsureLocalPlayerInst(Game* g, Sprite* sprite, const float pos[3],
	float yaw, int anim, int frame);

void Game::Render(uint64_t _stamp, AnsiCell* ptr, int width, int height)
{
#ifdef __EMSCRIPTEN__
	WebGlyphSidecarBeginFrame(width, height);
#endif
	g_web_render_stage_code = 1; // frame start
	g_web_render_interaction_query_duration_us = 0;
	g_web_render_status_bar_duration_us = 0;
	g_web_render_talk_overlay_duration_us = 0;
	g_web_render_player_overlay_duration_us = 0;
	g_web_render_deferred_terrain_dark_duration_us = 0;
	g_web_render_core_world_duration_us = 0;
	g_web_render_weather_duration_us = 0;
	g_web_render_minimap_duration_us = 0;
	g_web_render_api_hook_duration_us = 0;
	g_web_render_remote_duplicate_purge_duration_us = 0;
	g_web_render_remote_duplicate_purge_deleted_count = 0;
	g_web_render_snapshot_npc_visual_lifecycle_duration_us = 0;
	g_web_render_snapshot_npc_visual_lifecycle_slots = 0;
	g_web_render_authoritative_item_appearance_duration_us = 0;
	g_web_render_authoritative_item_appearance_slots = 0;
	g_web_render_frame_input_npc_visual_copy_duration_us = 0;
	g_web_render_frame_input_npc_visual_copy_slots = 0;
	g_web_render_lag_probe_sent_this_frame = 0;
	g_web_render_lag_probe_send_stage_code = 0;
	g_web_render_lag_probe_send_to_render_end_us = 0;
	g_web_render_lag_probe_send_seq = 0;
	uint32_t render_lag_probe_send_us32 = 0;
	FlushPendingRespawnAuthoritativeItemRefresh(this);
	// Reset per-frame visibility counters (used by web E2E visual sync checks).
	bool dbg_remote_visibility_issue_this_frame = false;
	ResetRenderDebugTelemetry(_stamp);
	SanitizeRemoteActorRoster(this, server);

    // --- TEST HARNESS BEGIN ---
    static bool test_mode_init = false;
    static bool is_test_mode = false;

    if (!test_mode_init) {
        if (getenv("ASCIICKER_TEST_MODE")) {
            is_test_mode = true;
            #ifndef _WIN32
            // Set stdin to non-blocking
            int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
            fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
            #endif // TODO: [Backlog Ref] #endif
            printf("TEST_MODE_ACTIVE\n");
            fflush(stdout);

            // FORCE DISABLE MAIN MENU // TODO: [Backlog Ref] FORCE DISABLE MAIN MENU
            ui.main_menu = false;

            // FORCE INIT GAME if physics is missing (bypassing MainMenu "Play" button) // TODO: [Backlog Ref] FORCE INIT GAME if physics is missing (bypassing MainMenu "Play" button)
            if (!physics) {
                char a3d_path[1024 + 20];
                ResolveRequestedA3dPath(a3d_path, sizeof(a3d_path), base_path);
                strncpy(g_loaded_a3d_path, a3d_path, sizeof(g_loaded_a3d_path) - 1);
                g_loaded_a3d_path[sizeof(g_loaded_a3d_path) - 1] = 0;
                FreeMinimapMarkers();
                FILE* f = fopen(a3d_path, "rb");
                if (f) {
                    extern Material mat[256];
                    terrain = LoadTerrain(f);
                    if (terrain) {
                        for (int i = 0; i < 256; i++) {
                            if (fread(mat[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
                                break;
                            material_glyph_plane_free(mat[i].glyph_plane);
                            mat[i].glyph_plane = NULL;
                        }
                        if (!LoadRuntimeMaterialGlyphSidecar(a3d_path, mat, "[GAME]")) {
                            fclose(f);
                            printf("TEST_MODE_MATERIAL_GLYPH_SIDECAR_FAILED\n");
                            return;
                        }
                        world = LoadWorldRuntime(f);
                        if (world) {
                            Mesh* m = GetFirstMesh(world);
                            while (m) {
                                char mesh_name[256];
                                GetMeshName(m, mesh_name, 256);
                                char obj_path[4096];
                                ResolveMeshAssetPath(obj_path, sizeof(obj_path), base_path, mesh_name);
                                bool ok = UpdateMesh(m, obj_path);
                                printf("TEST_MODE_UPDATE_MESH: %s ok=%d faces=%d\n", mesh_name, ok, GetMeshFaces(m));
                                m = GetNextMesh(m);
                            }
                            LoadEnemyGens(f);
                            LoadMinimapMarkers(f);
                            RebuildWorld(world, true);
                        }
                    }
                    fclose(f);
                    printf("TEST_MODE_LOADED_SCENE: %s\n", a3d_path);
                }

                if (!terrain) {
                    terrain = CreateTerrain();
                    for (int v = 0; v < 16; v++) {
                        for (int u = 0; u < 16; u++) {
                            AddTerrainPatch(terrain, u, v, 0xA000);
                        }
                    }
                }
                if (!world) {
                    world = CreateWorld();
                }

#if !defined(__EMSCRIPTEN__) && !defined(EDITOR) && !defined(PURE_TERM) && !defined(SERVER)
				if (!server)
				{
					if (!EnsureNormalGameAuthoritativeSession(player_name, 0))
						printf("TEST_MODE_LOCAL_AUTH_START_FAILED\n");
				}
#endif

                float pos[3] = {0, 0, 0xA000 + 200.0f};
                float lt[4] = {1, 1, 1, 1};
                InitGame(this, 0x8000, pos, 0, 0, lt, _stamp);
                printf("TEST_MODE_FORCE_INIT: terrain=%p world=%p water=%d\n", (void*)terrain, (void*)world, this->session.water);
            }
        }
        test_mode_init = true;
    }

    if (is_test_mode) {
        static char cmd_buf[1024];
        static int cmd_pos = 0;
        #ifndef _WIN32
        int n = read(STDIN_FILENO, cmd_buf + cmd_pos, 1023 - cmd_pos);
        if (n > 0) {
            cmd_pos += n;
            cmd_buf[cmd_pos] = 0;
            char* line_start = cmd_buf;
            char* p;
            while ((p = strchr(line_start, '\n'))) {
                *p = 0;
                char* line = line_start;
                line_start = p + 1;

                if (strncmp(line, "MOVE_FORWARD", 12) == 0) {
                    input.key[A3D_W >> 3] |= (1 << (A3D_W & 7));
                    printf("TEST_CMD: MOVE_FORWARD\n");
                }
                else if (strncmp(line, "STOP", 4) == 0) {
                    input.key[A3D_W >> 3] &= ~(1 << (A3D_W & 7));
                    printf("TEST_CMD: STOP\n");
                }
                else if (strncmp(line, "ATTACK_NEAREST_NPC", 18) == 0) {
                    printf("TEST_CMD: ATTACK_NEAREST_NPC disabled_by_FL_1148\n");
#if 0
                    int rc = VerifierStartAttackNearest(this, 2);
                    printf("TEST_CMD: ATTACK_NEAREST_NPC rc=%d\n", rc);
#endif
                }
                else if (strncmp(line, "DUMP_NPCS", 9) == 0) {
                    if (!server) {
                        printf("TEST_NPCS: no_server\n");
                    }
                    else if (server->authority.npc_repo.npc_count <= 0) {
                        printf("TEST_NPCS: count=0 tick=%u\n", server->authority.npc_repo.npc_tick);
                    }
                    else {
                        printf("TEST_NPCS: count=%u tick=%u\n",
                            (unsigned)server->authority.npc_repo.npc_count,
                            (unsigned)server->authority.npc_repo.npc_tick);
                        for (int i = 0; i < (int)server->authority.npc_repo.npc_count; i++) {
                            const ServerSnapshotNpcRepository::SnapshotNpcState* sn = &server->authority.npc_repo.npcs[i];
                            printf(
                                "TEST_NPC idx=%d eid=%u hp=%d/%d pos=(%.2f,%.2f,%.2f) dir=%.2f life=%u combat=%u kind=%u flags=0x%04x tick=%u\n",
                                i,
                                (unsigned)sn->entity_id,
                                (int)sn->hp,
                                (int)sn->max_hp,
                                sn->pos[0], sn->pos[1], sn->pos[2],
                                sn->dir,
                                (unsigned)sn->life_state,
                                (unsigned)sn->combat_state,
                                (unsigned)sn->presentation_kind_id,
                                (unsigned)sn->state_flags,
                                (unsigned)sn->last_authoritative_tick);
                        }
                    }
                }
                else if (strncmp(line, "TELEPORT", 8) == 0) {
                    printf("TEST_CMD: TELEPORT disabled_by_FL_1148\n");
                }
            }
            // Move remaining partial line to start
            if (line_start > cmd_buf) {
                int rem = strlen(line_start);
                memmove(cmd_buf, line_start, rem + 1);
                cmd_pos = rem;
            }
        }
        #endif // TODO: [Backlog Ref] #endif

        static int frame_skip = 0;
        if (frame_skip++ % 10 == 0) {
             const float* debug_pos = player.pos;
             printf(
                 "STATE: POS %.2f %.2f %.2f HP=%d/%d COMBAT=%d P_KIND=%u W_DOWN=%d LOCAL_ID=%d SNAP=%d NPCS=%u\n",
                 debug_pos[0], debug_pos[1], debug_pos[2],
                 (int)debug.dbg_self_hp, (int)debug.dbg_self_max_hp,
                 (int)player.combat_state,
                 (unsigned)player.presentation_kind_id,
                 input.IsKeyDown(A3D_W),
                 server ? server->connection.local_id : -99,
                 player.authoritative_snapshot_valid ? 1 : 0,
                 server ? (unsigned)server->authority.npc_repo.npc_count : 0u);
             printf("TIME: %llu PHYS: %p\n", _stamp, (void*)physics);
             fflush(stdout);
        }
    }
    // --- TEST HARNESS END ---

	// Weather system init (once, on first frame)
	bool authoritative_web_multiplayer_render = false;
	#ifdef __EMSCRIPTEN__
	authoritative_web_multiplayer_render = (server != 0);
	#endif
	static bool weather_init = false;
	if (!authoritative_web_multiplayer_render && !weather_init)
	{
		weather = CreateWeather();
		weather_init = true;
	}

	if (server)
		server->connection.stamp = _stamp;

	uint64_t fps_window_time = _stamp - debug.fps_window[debug.fps_window_pos];
	debug.fps_window[debug.fps_window_pos++] = _stamp;
	if (debug.fps_window_pos == debug.fps_window_size)
		debug.fps_window_pos = 0;

	int FPSx10 = (int)((debug.fps_window_size * 10000000 + (fps_window_time>>1)) / fps_window_time);
	// RQ-11 Phase C: observe-render artifacts must be deterministic and match the
	// committed baseline. Runtime FPS variance would otherwise perturb the status
	// bar text and break shot.xp parity.
	if (ObserveRenderEnabled())
		FPSx10 = 1199; // "119.9 fps"

	if (_stamp-stamp > 500000) // treat lags longer than 0.5s as stall
		stamp = _stamp;

	// handle dirpad autorep
	if (input.pad_connected && input.pad_autorep>0)
	{
		if (stamp - input.pad_stamp > 500000)
		{
			input.pad_stamp = stamp - 500000 + 50000; // 20Hz
			OnPadButton(input.pad_autorep-1, true);
		}
	}

	if (input.PressKey && _stamp - input.PressStamp > 50000 /*500000*/)
	{
		// in render():
		// if there is stored key and time elapsed since it was pressed > thresh // TODO: [Backlog Ref] if there is stored key and time elapsed since it was pressed > thresh
		//   then: emulate stored KEY_UP and clear stored key

		char ch = input.KeybAutoRepChar;
		OnKeyb(GAME_KEYB::KEYB_UP, input.PressKey);
		input.PressKey = 0;
		// revert it (OnKeyb nulls it)
			input.KeybAutoRepChar = ch;
	}

	int f120 = (int)(1 + (_stamp - stamp) / 8264);
	ui.TalkBox_blink += f120;

	if (input.KeybAutoRepChar)
	{
		char ch = input.KeybAutoRepChar;
		while (_stamp - input.KeybAuroRepDelayStamp >= 500000) // half sec delay
		{
			OnKeyb(GAME_KEYB::KEYB_CHAR, ch);
			input.KeybAuroRepDelayStamp += 30000;
		}
		// revert it (OnKeyb nulls it)
		input.KeybAutoRepChar = ch;
	}

	if (session.render_size[0] != width || session.render_size[1] != height)
	{
		int kh = keyb.Height(width, height);
		if (!ui.show_keyb || ui.keyb_hide > kh)
			ui.keyb_hide = kh;
	}

	session.render_size[0] = width;
	session.render_size[1] = height;

	if (ui.main_menu)
	{
		g_web_render_stage_code = 2; // main menu render
		MainMenu_Render(_stamp, ptr, width, height);
		return;
	}

	if (server && !debug.entered_world_logged && physics && world && terrain &&
		player.authoritative_snapshot_valid)
	{
		printf("[GAME_STATE] ENTERED_WORLD\n");
		fflush(stdout);
		debug.entered_world_logged = true;
	}
	if (is_test_mode && server && player.authoritative_snapshot_valid)
	{
		// FL-1148: ASCIICKER_TEST_MODE_AUTH_TELEPORT is disabled because it
		// mutates gameplay state outside ordinary user-reachable input.
#if 0
		static bool test_mode_auth_teleport_done = false;
		if (!test_mode_auth_teleport_done)
		{
			const char* auth_teleport = getenv("ASCIICKER_TEST_MODE_AUTH_TELEPORT");
			if (auth_teleport && auth_teleport[0])
			{
				float tx = 0.0f, ty = 0.0f, tz = 0.0f;
				if (sscanf(auth_teleport, "%f %f %f", &tx, &ty, &tz) == 3)
				{
					int rc = VerifierSetCheckpointPosition(this, tx, ty, tz);
					printf("TEST_MODE_AUTH_TELEPORT: %.2f %.2f %.2f rc=%d\n", tx, ty, tz, rc);
					fflush(stdout);
					test_mode_auth_teleport_done = true;
				}
			}
		}
#endif
	}

	float lt[4] = {session.light[0],session.light[1],session.light[2],session.light[3]};
	float n = lt[0] * lt[0] + lt[1] * lt[1] + lt[2] * lt[2];
	if (n > 0.001)
	{
		lt[0] /= n;
		lt[1] /= n;
		lt[2] /= n;
	}


	// [FLOW:PHYSICS] Force accumulation setup
	// WHY forces are set in game.cpp (not physics.cpp):
	// Physics module is game-agnostic. Game-specific logic like water reducing gravity,
	// mount movement restrictions, attack animation freezing, and multi-input fusion
	// (keyboard + gamepad + touch + API) happens here. PhysicsIO struct carries forces
	// into Animate() where they're integrated with gravity and collisions.
	//
	// FL-2957 TRACE: The 'M' movement path — OnKeyb sets input.key[],
	// PrepareLocalMovementStepIO reads those bits and builds PhysicsIO force vectors here.
	// This is the LOCAL client-side build step (Attempt #23: source trace overlays).
	// Forces then go out via SendLocalNetworkUpdates (engine/local_player_authority.cpp:408).
	// See FAILURE_LOG.md FL-2957 FAILED ATTEMPTS COUNTER — 23 attempts, 0 closed.
	PhysicsIO io = {};
	PrepareLocalMovementStepIO(input, player, camera, session, ui, debug,
		HasAuthoritativeServerSession(this),
		server != nullptr,
		server ? server->connection.local_id : -99,
		_stamp, stamp, physics, &io);
	if (is_test_mode && (io.x_force != 0 || io.y_force != 0))
	{
		static int p_input_debug = 0;
		if (p_input_debug++ % 10 == 0)
		{
			printf("TEST_PHYS_INPUT: force=(%.2f, %.2f) combat=%d W_DOWN=%d\n", io.x_force, io.y_force, player.combat_state, input.IsKeyDown(A3D_W));
			fflush(stdout);
		}
	}
	// [FLOW:PHYSICS] Main player physics integration
	// WHY Animate() is called here:
	// After accumulating all input forces (keyboard, gamepad, touch, API) into PhysicsIO,
	// Animate() integrates forces, applies gravity (reduced by water level), sweeps terrain
	// collisions via BSP ray tests, and updates position. Called once per frame before
	// rendering to ensure player position matches latest input state.
	// Returns: number of physics substeps taken (for animation frame advance)
	// FL-2588 REVERTED: local_server restructuring added an else{Animate()}
	// branch and re-nested HasAuthoritativeServerSession guards. This broke
	// the multiplayer tick path — in a server session the else branch ran
	// Animate() instead of MpMoveTick(), causing all players to be invisible.
	// ORIGINAL STRUCTURE RESTORED (8af87f5c). Do NOT re-indent or add else
	// branches without a dedicated FL entry + proof run.
	// Related: FL-2345 (mounted composition partially resolved — 3-surface model live),
	// FL-3848 (mount TYPES still code-locked), FL-2500, FL-2543.
	// Audit 2026-05-10: FL-903 partially resolved (bundle V2 live, SkinFamilyDefinition deleted).
	const float* pre_anim_pos = player.pos;
		int steps = 0;
		if (physics)
		{
			PhysicsWorld physics_world = {};
			physics_world.actor = physics;
			physics_world.multiplayer_state = &player.mp_move;

			PhysicsStepInput step_input = {};
			step_input.io = &io;
			step_input.stamp = _stamp;
			step_input.use_multiplayer_path =
				(server != 0 && !HasAuthoritativeServerSession(this));
			step_input.has_authoritative_server = (server != 0);
			step_input.allow_local_animate =
				(server == 0);
			step_input.me =
				(server == 0);

			PhysicsStepResult step_result = PhysicsStep(physics_world, step_input);
			steps = step_result.steps;
		}

	// [FLOW:PHYSICS] Grounded state usage
	// WHY we read io.grounded after Animate():
	// Animate() updates io.grounded based on terrain collision sweep. Grounded state
	// affects gameplay: session.blood leak particles, jump availability (set earlier), friction,
	// animation state. Previous frame's grounded state stored for bee mount flight logic.
	// When the server is authoritative, io was not stepped — do not consume stale io
	// state from PrepareLocalMovementStepIO (FL-164/FL-274/FL-303 dual-writer pattern).
	if (!HasAuthoritativeServerSession(this))
	{
		if (io.grounded && session.blood)
			BloodLeak(&player, steps);

		player.prev_grounded = io.grounded;
	}
	float dbg_local_vel[3] = { 0,0,0 };
	if (physics)
		GetPhysicsVel(physics, dbg_local_vel);
	debug.dbg_local_vel_x = dbg_local_vel[0];
	debug.dbg_local_vel_y = dbg_local_vel[1];
	debug.dbg_local_vel_z = dbg_local_vel[2];
	// Detect velocity direction reversal without reconcile.
	// Dot product of previous and current XY velocity < 0 means direction reversed.
	// Only count if both velocities are non-trivial (> 0.5 units/s).
	{
		float prev_vx = debug.dbg_local_prev_vel_x;
		float prev_vy = debug.dbg_local_prev_vel_y;
		float prev_mag2 = prev_vx * prev_vx + prev_vy * prev_vy;
		float cur_mag2 = dbg_local_vel[0] * dbg_local_vel[0] + dbg_local_vel[1] * dbg_local_vel[1];
		float dot = prev_vx * dbg_local_vel[0] + prev_vy * dbg_local_vel[1];
		if (prev_mag2 > 0.25f && cur_mag2 > 0.25f && dot < 0.0f && !debug.dbg_reconcile_applied)
			debug.dbg_local_vel_dir_change_no_reconcile++;
		debug.dbg_local_prev_vel_x = dbg_local_vel[0];
		debug.dbg_local_prev_vel_y = dbg_local_vel[1];
	}
	if (!HasAuthoritativeServerSession(this))
	{
		debug.dbg_local_grounded = io.grounded ? 1 : 0;
		debug.dbg_local_became_airborne = (player.prev_grounded && !io.grounded) ? 1 : 0;
		player.pos[0] = io.pos[0];
		player.pos[1] = io.pos[1];
		player.pos[2] = io.pos[2];
		player.dir = io.player_dir;
		// FL-3858 DIVERGENCE: This is the BRIDGE between SP physics owner
		// (physics.cpp -> io.player_dir) and the shared render/input consumer
		// (player.dir). In multiplayer, player.dir is set from server authoritative
		// state instead. Both paths must agree on the same world-facing convention.
		// See FL-3858 body for the full SP/MP ownership split audit.
		player.impulse[0] = io.x_impulse;
		player.impulse[1] = io.y_impulse;
	}

		if (steps > 0)
		{
		input.jump = false;
	}

	g_web_render_stage_code = 20; // local/snapshot actor update complete

	// Update dedicated sprite instances from authoritative
	// NPC snapshots. Snapshot-slot presence is the owner boundary here: if the
	// server still exports an NPC slot, keep rendering that slot even after death
	// so FALL/DEAD frames and corpse presentation remain observable.
	{
		g_web_render_stage_code = 30; // authoritative NPC visuals begin
		float tracked_npc_best_dd = 1e30f;
		auto update_tracked_npc0 = [&](const ServerSnapshotNpcRepository::SnapshotNpcState* sn,
			const ServerSnapshotNpcRepository::SnapshotNpcVisual* vis, const float pos[3], int on_screen,
			const ActorPresentationResult* resolved) -> void
		{
			if (!sn || !vis)
				return;
			const float* player_pos = player.pos;
			float dx = pos[0] - player_pos[0];
			float dy = pos[1] - player_pos[1];
			float dd = dx * dx + dy * dy;
			// tracked_npc0 is only the nearest observed NPC for this tab. It is not
			// a stable playback owner and can hop to a different entity mid-run, so
			// do not derive animation timing from this debug focus surface.
			if (debug.dbg_tracked_npc0_entity_id >= 0 && dd >= tracked_npc_best_dd)
				return;
			tracked_npc_best_dd = dd;
			debug.dbg_tracked_npc0_entity_id = (int)sn->entity_id;
			debug.dbg_tracked_npc0_pos[0] = pos[0];
			debug.dbg_tracked_npc0_pos[1] = pos[1];
			debug.dbg_tracked_npc0_pos[2] = pos[2];
			debug.dbg_tracked_npc0_on_screen = on_screen;
			debug.dbg_tracked_npc0_inst_visible = (vis->inst && (GetInstFlags(vis->inst) & INST_VISIBLE)) ? 1 : 0;
			debug.dbg_tracked_npc0_hp = (int)sn->hp;
			debug.dbg_tracked_npc0_life_state = (int)sn->life_state;
			debug.dbg_tracked_npc0_needs_physics_step =
				(sn->state_flags & SNAPSHOT_STATE_NPC_NEEDS_PHYSICS) ? 1 : 0;
			debug.dbg_tracked_npc0_authoritative_tick = (int)sn->last_authoritative_tick;
			debug.dbg_tracked_npc0_presentation_started_tick = (int)sn->presentation_started_tick;
			debug.dbg_tracked_npc0_death_tick =
				(sn->life_state == LIFE_STATE::DEAD) ? (int)sn->presentation_started_tick : 0;
			debug.dbg_tracked_npc0_corpse_hold_age_ticks =
				(debug.dbg_tracked_npc0_death_tick > 0 &&
				 sn->last_authoritative_tick >= sn->presentation_started_tick)
					? (int)(sn->last_authoritative_tick - sn->presentation_started_tick)
					: 0;
			debug.dbg_tracked_npc0_presentation_kind_id = (int)sn->presentation_kind_id;
			debug.dbg_tracked_npc0_render_presentation_kind_id =
				resolved ? (int)resolved->presentation_kind_id : 0;
			int tracked_npc0_sample_owner_stable_frames = 1;
			if (debug.dbg_last_tracked_npc0_entity_id == (int)sn->entity_id &&
				debug.dbg_last_tracked_npc0_presentation_kind_id == (int)sn->presentation_kind_id &&
				debug.dbg_last_tracked_npc0_life_state == (int)sn->life_state)
			{
				tracked_npc0_sample_owner_stable_frames =
					debug.dbg_last_tracked_npc0_sample_owner_stable_frames + 1;
			}
			debug.dbg_tracked_npc0_sample_owner_stable_frames =
				tracked_npc0_sample_owner_stable_frames;
			debug.dbg_tracked_npc0_sample_owner_ready =
				(tracked_npc0_sample_owner_stable_frames >= 2) ? 1 : 0;
			// FL-3012: these fields capture resolved-stack truth only. A nonzero
			// head layer id / render layer count does not guarantee the body later
			// survived RenderSprite clip/depth rejection.
			ActorPresentationResult runtime_frame_resolved = {};
			const ActorPresentationResult* frame_resolved = resolved;
			if (resolved)
			{
				runtime_frame_resolved = *resolved;
				frame_resolved = &runtime_frame_resolved;
			}
			debug.dbg_tracked_npc0_render_head_layer_definition_id =
				frame_resolved ? frame_resolved->head_layer_definition_id : 0;
			if (frame_resolved &&
				frame_resolved->sprite &&
				frame_resolved->sprite->angles > 0)
				{
					const int runtime_angle =
						FacingSpriteAngleIndex(sn->dir, io.yaw, frame_resolved->sprite->angles);
					ActorVisualProfileRefreshResultRuntimeFrameFields(
						&runtime_frame_resolved,
						runtime_angle,
						0);
				}
			debug.dbg_tracked_npc0_render_profile_id_hash =
				frame_resolved ? frame_resolved->render_profile_id_hash : 0;
			debug.dbg_tracked_npc0_render_atlas_frame_index =
				frame_resolved ? frame_resolved->render_atlas_frame_index : 0;
			debug.dbg_tracked_npc0_render_contribution_angle =
				frame_resolved ? frame_resolved->render_contribution_angle : 0;
			debug.dbg_tracked_npc0_render_contribution_projection =
				frame_resolved ? frame_resolved->render_contribution_projection : 0;
			debug.dbg_tracked_npc0_render_contribution_scope =
				frame_resolved ? frame_resolved->render_contribution_scope : 0;
			debug.dbg_tracked_npc0_render_layer_count =
				frame_resolved ? frame_resolved->render_layer_count : 0;
			debug.dbg_tracked_npc0_render_attachment_expected_mask = 0;
			debug.dbg_tracked_npc0_render_attachment_source_visible_mask = 0;
			debug.dbg_tracked_npc0_render_attachment_source_missing_mask = 0;
			for (int render_i = 0; render_i < ACTOR_VISUAL_MAX_RENDER_LAYERS; render_i++)
			{
				debug.dbg_tracked_npc0_render_slot_kind_ids[render_i] =
					frame_resolved ? frame_resolved->render_slot_kind_ids[render_i] : 0;
				debug.dbg_tracked_npc0_render_layer_definition_ids[render_i] =
					frame_resolved ? frame_resolved->render_layer_definition_ids[render_i] : 0;
				debug.dbg_tracked_npc0_render_layer_semantic_contribution_set_indices[render_i] =
					frame_resolved
						? frame_resolved->render_layer_semantic_contribution_set_indices[render_i]
						: 0;
				debug.dbg_tracked_npc0_render_layer_source_layer_indices[render_i] =
					frame_resolved
						? frame_resolved->render_layer_source_layer_indices[render_i]
						: 0;
				debug.dbg_tracked_npc0_render_layer_source_path_hashes[render_i] =
					frame_resolved ? frame_resolved->render_layer_source_path_hashes[render_i] : 0;
				debug.dbg_tracked_npc0_render_layer_visible_cell_counts[render_i] =
					frame_resolved ? frame_resolved->render_layer_visible_cell_counts[render_i] : 0;
				debug.dbg_tracked_npc0_render_layer_contributed_cell_counts[render_i] =
					frame_resolved ? frame_resolved->render_layer_contributed_cell_counts[render_i] : 0;
				debug.dbg_tracked_npc0_render_layer_occluded_cell_counts[render_i] =
					frame_resolved ? frame_resolved->render_layer_occluded_cell_counts[render_i] : 0;
				if (!frame_resolved || render_i >= frame_resolved->render_layer_count)
					continue;
				const uint16_t slot_kind_id =
					frame_resolved->render_slot_kind_ids[render_i];
				const uint16_t layer_definition_id =
					frame_resolved->render_layer_definition_ids[render_i];
				const uint32_t attachment_bit = TrackedNpcAttachmentMaskBit(slot_kind_id);
				if (attachment_bit)
					debug.dbg_tracked_npc0_render_attachment_expected_mask |= attachment_bit;
				(void)layer_definition_id;
				const uint16_t visible_cells =
					frame_resolved->render_layer_contributed_cell_counts[render_i];
				debug.dbg_tracked_npc0_render_layer_visible_cell_counts[render_i] =
					frame_resolved->render_layer_visible_cell_counts[render_i];
				if (!attachment_bit)
					continue;
				if (visible_cells > 0)
					debug.dbg_tracked_npc0_render_attachment_source_visible_mask |=
						attachment_bit;
				else
					debug.dbg_tracked_npc0_render_attachment_source_missing_mask |=
						attachment_bit;
			}
			debug.dbg_tracked_npc0_render_compose_mode =
				resolved ? (int)resolved->compose_mode : 0;
			debug.dbg_tracked_npc0_render_compose_failure_stage =
				resolved ? resolved->compose_failure_stage : 0;
			debug.dbg_tracked_npc0_render_compose_failure_base_layer_definition_id =
				resolved ? resolved->compose_failure_base_layer_definition_id : 0;
			debug.dbg_tracked_npc0_render_compose_failure_overlay_layer_definition_id =
				resolved ? resolved->compose_failure_overlay_layer_definition_id : 0;
			debug.dbg_tracked_npc0_render_compose_failure_overlay_slot_kind_id =
				resolved ? resolved->compose_failure_overlay_slot_kind_id : 0;
			debug.dbg_tracked_npc0_anim = resolved ? resolved->anim : 0;
			debug.dbg_tracked_npc0_frame = resolved ? resolved->frame : 0;
			debug.dbg_tracked_npc0_anim_length = resolved ? resolved->anim_length : 0;
			debug.dbg_tracked_npc0_frame_clamped = resolved ? (int)resolved->playback_frame_clamped : 0;
			debug.dbg_tracked_npc0_frame_changed_expected =
				resolved ? (int)resolved->playback_frame_changed_expected : 0;
			debug.dbg_tracked_npc0_render_diverged_from_snapshot =
				(debug.dbg_tracked_npc0_render_presentation_kind_id > 0 &&
				 debug.dbg_tracked_npc0_presentation_kind_id > 0 &&
				 debug.dbg_tracked_npc0_render_presentation_kind_id != debug.dbg_tracked_npc0_presentation_kind_id) ? 1 : 0;
			debug.dbg_tracked_npc0_corpse_visible =
				(debug.dbg_tracked_npc0_death_tick > 0 &&
				 debug.dbg_tracked_npc0_on_screen &&
				 debug.dbg_tracked_npc0_inst_visible &&
				 vis->sprite) ? 1 : 0;
			debug.dbg_tracked_npc0_sprite_miss_frames = (int)vis->sprite_miss_frames;
			debug.dbg_tracked_npc0_selector_failure_reason = (int)vis->selector_failure_reason;
			debug.dbg_tracked_npc0_inst_create_count = (int)vis->inst_create_count;
			debug.dbg_tracked_npc0_inst_delete_count = (int)vis->inst_delete_count;
			debug.dbg_tracked_npc0_last_inst_delete_reason = (int)vis->last_inst_delete_reason;
			debug.dbg_tracked_npc0_last_inst_delete_miss_frames = (int)vis->last_inst_delete_miss_frames;
		};
		if (server)
		{
			uint64_t snapshot_npc_visual_lifecycle_begin_us = a3dGetTime();
			int snapshot_npc_visual_lifecycle_slots = 0;
			for (int i = 0; i < ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS; i++)
			{
				ServerSnapshotNpcRepository::SnapshotNpcVisual* vis =
					&server->authority.npc_repo.visuals[i];
				const bool had_inst = vis->inst != 0;
				const uint16_t before_create = vis->inst_create_count;
				const uint16_t before_delete = vis->inst_delete_count;
				SnapshotNpcVisualLifecycleProbe probe = {};
				const bool slot_processed = UpdateSnapshotNpcVisualLifecycleSlot(
					&server->authority.snapshot_client,
					&server->authority.npc_repo,
					world, renderer, _stamp, width, height, i, &probe);
				if (!slot_processed)
				{
					if (had_inst && !vis->inst)
						debug.dbg_snapshot_npc_inst_delete_count++;
					continue;
				}
				debug.dbg_actor_visual_resolve_us += probe.resolve_us;
				debug.dbg_actor_visual_compose_us += probe.compose_us;
				if (vis->inst_create_count != before_create)
					debug.dbg_snapshot_npc_inst_create_count++;
				if (vis->inst_delete_count != before_delete)
					debug.dbg_snapshot_npc_inst_delete_count++;
				if (!vis->sprite)
					debug.dbg_snapshot_npc_sprite_null_count++;
				debug.dbg_snapshot_npc_sprite_miss_total += vis->sprite_miss_frames;
				update_tracked_npc0(
					probe.snapshot,
					probe.visual,
					probe.pos,
					probe.on_screen,
					probe.resolved_valid ? &probe.resolved : 0);
				snapshot_npc_visual_lifecycle_slots++;
			}
			g_web_render_snapshot_npc_visual_lifecycle_duration_us =
				(uint32_t)(a3dGetTime() - snapshot_npc_visual_lifecycle_begin_us);
			g_web_render_snapshot_npc_visual_lifecycle_slots =
				(uint32_t)snapshot_npc_visual_lifecycle_slots;
		}
	}
	g_web_render_stage_code = 39; // authoritative NPC visuals complete
	AuthoritativeWorldItemAppearanceFrame authoritative_world_item_appearance = {};

	// Render authoritative world items as real sprite instances and derive the
	// frame-local visible/pickup rows from that same lifecycle.
	{
		g_web_render_stage_code = 40; // authoritative item visuals begin
		uint64_t authoritative_item_appearance_begin_us = a3dGetTime();
		UpdateAuthoritativeWorldItemAppearance(
			this,
			world,
			renderer,
			_stamp,
			width,
			height,
			&authoritative_world_item_appearance);
		PublishAuthoritativeWorldItemAppearanceRows(
			this,
			&authoritative_world_item_appearance);
		g_web_render_authoritative_item_appearance_duration_us =
			(uint32_t)(a3dGetTime() - authoritative_item_appearance_begin_us);
		g_web_render_authoritative_item_appearance_slots =
			server ? (uint32_t)AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS : 0u;
		g_web_render_stage_code = 49; // authoritative item visuals complete
	}

	// When authoritative NPC snapshot visuals are active,
	// retire local NPC actors and delete any remaining local NPC sprite instances so
	// client-owned NPC visuals stop competing with server-owned NPC snapshots.
	{
		g_web_render_stage_code = 50; // local NPC retire/delete begin
		bool use_authoritative_npc_visuals = (server && server->authority.npc_repo.npc_count > 0);
		if (use_authoritative_npc_visuals)
			RetireLocalNpcActorsForAuthoritativeMode(this);
		if (use_authoritative_npc_visuals)
		{
			Character* h_vis = player_head;
			while (h_vis)
			{
				if (h_vis != &player && h_vis->inst)
				{
					DeleteInst(h_vis->inst);
					h_vis->inst = 0;
				}
				h_vis = h_vis->next;
			}
		}
	}
	g_web_render_stage_code = 59; // local NPC retire/delete complete

	player.prev_yaw = io.yaw;
		float local_display_pos[3];
		float local_display_dir;
		SampleLocalSnapshotPresentationTrack(
			&player.snapshot_presentation_track,
			HasAuthoritativeServerSession(this) && LocalPlayerAuthoritativePoseReady(this->player, server != nullptr),
			_stamp,
			player.pos,
			player.dir,
			player.mp_move.auth_state.pos,
			player.mp_move.auth_state.player_dir,
			player.life_state,
			local_display_pos,
			&local_display_dir);
	const bool hold_local_authoritative_pose = !LocalPlayerAuthoritativePoseReady(this->player, server != nullptr);
	int    local_render_anim = player.anim;
	int    local_render_frame = player.frame;
	debug.dbg_local_pre_anim_pos_x = pre_anim_pos[0];
	debug.dbg_local_pre_anim_pos_y = pre_anim_pos[1];
	debug.dbg_local_pre_anim_pos_z = pre_anim_pos[2];
	debug.dbg_local_pos_x = player.pos[0];
	debug.dbg_local_pos_y = player.pos[1];
	debug.dbg_local_pos_z = player.pos[2];
	// FL-4137 mobile-proof helper: project local player to screen cell space.
	{
		int player_view[3] = { 0, 0, 0 };
		if (ProjectCoords(renderer, player.pos, player_view))
		{
			debug.dbg_local_screen_col = (int16_t)player_view[0];
			debug.dbg_local_screen_row = (int16_t)player_view[1];
			debug.dbg_local_screen_valid = 1;
		}
		else
		{
			debug.dbg_local_screen_col = 0;
			debug.dbg_local_screen_row = 0;
			debug.dbg_local_screen_valid = 0;
		}
	}
	// FL-4165: all-collision overlay is sourced from physics debug packets,
	// not item appearance rows. Render only projects the server-owned sample
	// corners into cell space so the browser overlay and recorder can inspect
	// the exact collision boxes/soup AABBs used around the player.
	if (server && server->authority.collision_debug.valid)
	{
		const CollisionDebugClientState& cd = server->authority.collision_debug;
		debug.dbg_collision_debug_valid = 1;
		debug.dbg_collision_debug_count = cd.count > COLLISION_DEBUG_SAMPLE_MAX
			? COLLISION_DEBUG_SAMPLE_MAX
			: cd.count;
		debug.dbg_collision_debug_player_id = cd.player_id;
		debug.dbg_collision_debug_tick = cd.tick;
		debug.dbg_collision_debug_support_source = cd.support_source;
		debug.dbg_collision_debug_push_source = cd.push_source;
		debug.dbg_collision_debug_support_item_id = cd.support_item_id;
		debug.dbg_collision_debug_player_pos[0] = cd.player_pos[0];
		debug.dbg_collision_debug_player_pos[1] = cd.player_pos[1];
		debug.dbg_collision_debug_player_pos[2] = cd.player_pos[2];
		debug.dbg_collision_debug_support_z = cd.support_z;
		for (uint16_t i = 0; i < debug.dbg_collision_debug_count; i++)
		{
			const STRUCT_BRC_COLLISION_DEBUG_SAMPLE& src = cd.samples[i];
			debug.dbg_collision_debug_sample_source[i] = src.source;
			debug.dbg_collision_debug_sample_flags[i] = src.flags;
			debug.dbg_collision_debug_sample_item_id[i] = src.item_id;
			debug.dbg_collision_debug_sample_entity_id[i] = src.entity_id;
			debug.dbg_collision_debug_sample_inst_id[i] = src.inst_id;
			debug.dbg_collision_debug_sample_mesh_id[i] = src.mesh_id;
			debug.dbg_collision_debug_sample_face_ordinal[i] = src.face_ordinal;
			for (int axis = 0; axis < 3; axis++)
			{
				debug.dbg_collision_debug_sample_bmin[i][axis] = src.bmin[axis];
				debug.dbg_collision_debug_sample_bmax[i][axis] = src.bmax[axis];
				debug.dbg_collision_debug_sample_normal[i][axis] = src.normal[axis];
			}
			uint8_t corners_valid = 1;
			for (int corner = 0; corner < 8; corner++)
			{
				float p[3] =
				{
					(corner & 1) ? src.bmax[0] : src.bmin[0],
					(corner & 2) ? src.bmax[1] : src.bmin[1],
					(corner & 4) ? src.bmax[2] : src.bmin[2],
				};
				int view[3] = {0, 0, 0};
				if (!ProjectCoords(renderer, p, view))
					corners_valid = 0;
				debug.dbg_collision_debug_sample_corner_col[i][corner] = (int16_t)view[0];
				debug.dbg_collision_debug_sample_corner_row[i][corner] = (int16_t)view[1];
			}
			debug.dbg_collision_debug_sample_corners_valid[i] = corners_valid;
		}
	}
		debug.dbg_local_visual_pos_x = local_display_pos[0];
		debug.dbg_local_visual_pos_y = local_display_pos[1];
		debug.dbg_local_visual_pos_z = local_display_pos[2];
		debug.dbg_local_last_acked_input_seq = (uint32_t)player.mp_move.last_acked_seq;
		debug.dbg_local_render_medium_snap_count = player.snapshot_presentation_track.medium_snap_count;
		debug.dbg_local_render_hard_snap_count = player.snapshot_presentation_track.hard_snap_count;
	debug.dbg_local_grounded = io.grounded ? 1 : 0;
	debug.dbg_local_became_airborne = (debug.dbg_last_local_grounded && !debug.dbg_local_grounded) ? 1 : 0;
	if (player.mp_move.last_snapshot_wall_stamp > 0 && _stamp >= player.mp_move.last_snapshot_wall_stamp)
		debug.dbg_local_snapshot_age_ms = (float)(_stamp - player.mp_move.last_snapshot_wall_stamp) / 1000.0f;
	else
		debug.dbg_local_snapshot_age_ms = 0.0f;
	if (!hold_local_authoritative_pose)
	{
		UpdateMobileAutoPickup(this, _stamp);
			// Keep auto-combat on authoritative gameplay pose. The local body can
			// blend toward LocalSnapshotPresentationTrack for a short render-only window, but
		// target selection and swing requests must continue to use server-owned
		// player.pos/player.dir to avoid reintroducing gameplay/display mixed ownership.
		UpdateMobileAutoCombat(server, debug, player, inventory_view, ui, session, input, _stamp, player.pos, player.dir);
	}

		uint32_t local_render_tick = (server && server->authority.snapshot_client.last_snapshot_tick != 0) ?
			server->authority.snapshot_client.last_snapshot_tick : (uint32_t)(_stamp / 1000ull);
		Sprite* render_sprite = player.sprite;
		ActorPresentationResult local_resolved = {};
		debug.dbg_actor_profile_load_status = 0;
		debug.dbg_actor_selector_found = 0;
		debug.dbg_actor_selector_failure_reason = 0;
		debug.dbg_actor_profile_selector_count = 0;
		debug.dbg_actor_profile_layer_count = 0;
		if (server)
		{
			const uint64_t resolve_begin_us = a3dGetTime();
			local_resolved = ResolveRemoteAuthoritativeCharacterPresentation(
				&player, player.clr, local_render_tick);
			debug.dbg_actor_visual_resolve_us +=
				(uint32_t)(a3dGetTime() - resolve_begin_us);
			debug.dbg_actor_visual_compose_us += local_resolved.render_compose_us;
		}
		else
		{
			UpdateOfflineWallClockPresentationAuthority(player, _stamp);
			const uint64_t resolve_begin_us = a3dGetTime();
			local_resolved = ResolveLocalWallClockCharacterPresentation(
				&player, player.clr, io.player_stp, _stamp);
			debug.dbg_actor_visual_resolve_us +=
				(uint32_t)(a3dGetTime() - resolve_begin_us);
			debug.dbg_actor_visual_compose_us += local_resolved.render_compose_us;
		}
			// FL-2500 symptom + FL-2518 spent family:
			// the runtime ref[2] re-anchoring patch was deleted after headed/manual
			// falsification. If mounted still resolves to a null sprite here, do not
			// re-add that bypass; FL-2518 records why it failed and the evidence that
			// spent the family. Check compose-failure diagnostics, wrapper/rider
			// authoring contract, and the remote-inst lifecycle families first.
			//
			// FL-2545/FL-3993 correction: the old fail-visible path preserved stale
			// standing/idle visuals on resolver miss. Under the content-owned bundle
			// contract, a null exact lookup is a prelaunch/content failure; render must
			// clear the inst instead of owning visual recovery.
			player.sprite = local_resolved.sprite;
			player.anim = local_resolved.anim;
			player.frame = local_resolved.frame;
		render_sprite = player.sprite;
		local_render_anim = player.anim;
		local_render_frame = player.frame;

		float actor_render_dir = local_display_dir;
			if (local_resolved.sprite && local_resolved.sprite->angles > 0)
			{
				const int runtime_angle =
					FacingSpriteAngleIndex(actor_render_dir, io.yaw, local_resolved.sprite->angles);
				ActorVisualProfileRefreshResultRuntimeFrameFields(
					&local_resolved,
					runtime_angle,
					0);
			}

		// Stage A diagnostic: actor rendered facing
			debug.dbg_actor_render_dir = actor_render_dir;
			debug.dbg_actor_life_state = player.life_state;
			debug.dbg_actor_mount_state = player.mount_state;
			debug.dbg_actor_mount_layer_count = local_resolved.mount_layer_count; // FL-2378
			debug.dbg_actor_render_presentation_kind_id = local_resolved.presentation_kind_id;
			debug.dbg_actor_render_skin_definition_id = local_resolved.skin_definition_id;
			debug.dbg_actor_render_loadout_signature = local_resolved.loadout_signature;
			debug.dbg_actor_render_profile_id_hash = local_resolved.render_profile_id_hash;
			debug.dbg_actor_render_atlas_frame_index = local_resolved.render_atlas_frame_index;
			debug.dbg_actor_render_contribution_angle = local_resolved.render_contribution_angle;
			debug.dbg_actor_render_contribution_projection =
				local_resolved.render_contribution_projection;
			debug.dbg_actor_render_contribution_scope =
				local_resolved.render_contribution_scope;
				debug.dbg_actor_server_visual_key_valid = local_resolved.selector_found;
				debug.dbg_actor_server_visual_key_hash = 0;
				debug.dbg_actor_render_plan_found = local_resolved.profile_found;
				debug.dbg_actor_render_plan_key_hash = 0;
				debug.dbg_actor_render_plan_layer_count = local_resolved.profile_layer_count;
			debug.dbg_actor_asset_load_failure_count =
				local_resolved.asset_load_failure_count;
			debug.dbg_actor_render_body_layer_definition_id = local_resolved.body_layer_definition_id;
			debug.dbg_actor_render_head_layer_definition_id = local_resolved.head_layer_definition_id;
			debug.dbg_actor_render_compose_mode =
				local_resolved.compose_mode;
			debug.dbg_actor_render_ref_alignment_observed =
				local_resolved.ref_alignment_observed;
			debug.dbg_actor_render_ref_alignment_overlay_layer_definition_id =
				local_resolved.ref_alignment_overlay_layer_definition_id;
			debug.dbg_actor_render_ref_alignment_overlay_slot_kind_id =
				local_resolved.ref_alignment_overlay_slot_kind_id;
			debug.dbg_actor_render_ref_alignment_base_ref_z =
				local_resolved.ref_alignment_base_ref_z;
			debug.dbg_actor_render_ref_alignment_overlay_ref_z =
				local_resolved.ref_alignment_overlay_ref_z;
			debug.dbg_actor_render_compose_failure_stage =
				local_resolved.compose_failure_stage;
			debug.dbg_actor_render_compose_failure_base_layer_definition_id =
				local_resolved.compose_failure_base_layer_definition_id;
			debug.dbg_actor_render_compose_failure_overlay_layer_definition_id =
				local_resolved.compose_failure_overlay_layer_definition_id;
			debug.dbg_actor_render_compose_failure_overlay_slot_kind_id =
				local_resolved.compose_failure_overlay_slot_kind_id;
			debug.dbg_actor_render_compose_failure_base_ref_z =
				local_resolved.compose_failure_base_ref_z;
			debug.dbg_actor_render_compose_failure_overlay_ref_z =
				local_resolved.compose_failure_overlay_ref_z;
			// FL-2345 delete-first: these exported legacy fields are zero-only now.
			debug.dbg_actor_render_fail_visible_fallback_used = 0;
			debug.dbg_actor_render_original_compose_failure_stage = 0;
			debug.dbg_actor_render_layer_count = local_resolved.render_layer_count;
			for (int i = 0; i < ACTOR_VISUAL_MAX_RENDER_LAYERS; i++)
			{
				debug.dbg_actor_render_slot_kind_ids[i] = local_resolved.render_slot_kind_ids[i];
				debug.dbg_actor_render_layer_definition_ids[i] = local_resolved.render_layer_definition_ids[i];
				debug.dbg_actor_render_item_definition_ids[i] = local_resolved.render_item_definition_ids[i];
				debug.dbg_actor_render_visual_style_ids[i] = local_resolved.render_visual_style_ids[i];
				debug.dbg_actor_render_layer_semantic_contribution_set_indices[i] =
					local_resolved.render_layer_semantic_contribution_set_indices[i];
				debug.dbg_actor_render_layer_source_layer_indices[i] =
					local_resolved.render_layer_source_layer_indices[i];
				debug.dbg_actor_render_layer_source_path_hashes[i] =
					local_resolved.render_layer_source_path_hashes[i];
				debug.dbg_actor_render_layer_visible_cell_counts[i] =
					local_resolved.render_layer_visible_cell_counts[i];
				debug.dbg_actor_render_layer_contributed_cell_counts[i] =
					local_resolved.render_layer_contributed_cell_counts[i];
				debug.dbg_actor_render_layer_occluded_cell_counts[i] =
					local_resolved.render_layer_occluded_cell_counts[i];
				// FL-4079
				debug.dbg_actor_render_layer_source_xp_indices[i] =
					local_resolved.render_layer_source_xp_indices[i];
			}
			debug.dbg_actor_profile_load_status = local_resolved.profile_load_status;
			debug.dbg_actor_selector_found = local_resolved.selector_found;
			debug.dbg_actor_selector_failure_reason = local_resolved.selector_failure_reason;
			debug.dbg_actor_profile_selector_count = local_resolved.selector_count;
			debug.dbg_actor_profile_layer_count = local_resolved.profile_layer_count;
			debug.dbg_actor_authoritative_tick = (int)local_render_tick;
			debug.dbg_actor_presentation_started_tick = (int)player.presentation_started_tick;
			debug.dbg_actor_playback_elapsed_ticks = (int)local_resolved.playback_elapsed_ticks;
			debug.dbg_actor_frame_clamped = (int)local_resolved.playback_frame_clamped;
			debug.dbg_actor_frame_changed_expected =
				(int)local_resolved.playback_frame_changed_expected;
			// FL-4079: row-owned playback metadata for wearable proof seam
			debug.dbg_actor_render_playback_mode = local_resolved.playback_mode;
			debug.dbg_actor_render_steady_frame_index = local_resolved.playback_steady_frame_index;
			debug.dbg_actor_render_selected_locomotion_anim_track =
				local_resolved.selected_locomotion_anim_track;

			// [DEBUG-walk-stuck] step_phase passed into wall-clock walk-frame derivation
			debug.dbg_actor_step_phase = io.player_stp;
			debug.dbg_actor_step_phase_div_1024 =
				(io.player_stp >= 0) ? (io.player_stp / 1024) : -1;
			debug.dbg_actor_profile_cache_hit_count = local_resolved.render_cache_hit_count;
			debug.dbg_actor_profile_cache_null_hit_count = local_resolved.render_cache_null_hit_count;
			debug.dbg_actor_profile_cache_miss_count = local_resolved.render_cache_miss_count;
			debug.dbg_actor_profile_cache_full_count = local_resolved.render_cache_full_count;
			debug.dbg_actor_profile_cache_failure_count = local_resolved.render_cache_failure_count;
			debug.dbg_actor_profile_row_lookup_cache_hit_count =
				local_resolved.render_row_lookup_cache_hit_count;
			debug.dbg_actor_profile_row_lookup_cache_null_hit_count =
				local_resolved.render_row_lookup_cache_null_hit_count;
			debug.dbg_actor_profile_row_lookup_cache_miss_count =
				local_resolved.render_row_lookup_cache_miss_count;
			debug.dbg_actor_profile_row_lookup_table_scan_count =
				local_resolved.render_row_lookup_table_scan_count;

		// update player inst in world — always apply sprite/presentation even
		// when pose is not yet authoritative, so death/mount/combat sprites
		// are never frozen on a stale idle sprite (FL-734).
		int reps[4] = { 0,0,0,0 };
		EnsureLocalPlayerInst(this, render_sprite, local_display_pos,
			actor_render_dir, local_render_anim, local_render_frame);
		if (player.player_inst && render_sprite)
			UpdateSpriteInst(world, player.player_inst, render_sprite, local_display_pos, actor_render_dir, local_render_anim, local_render_frame, reps);
		else if (player.player_inst && !render_sprite)
		{
			DeleteInst(player.player_inst);
			player.player_inst = 0;
		}
	if (player.player_inst)
	{
		int inst_reps[4] = { 0, 0, 0, 0 };
		Sprite* inst_sprite = GetInstSprite(player.player_inst, 0, 0, 0, 0, inst_reps);
		debug.dbg_actor_inst_sprite_matches_owner = (inst_sprite == render_sprite) ? 1 : 0;
	}
	else
	{
		debug.dbg_actor_inst_sprite_family_kind = 0;
		debug.dbg_actor_inst_sprite_matches_owner = 0;
	}

	if (!server)
	{
		for (Character* npc = player_head; npc; npc = npc->next)
		{
			if (npc == &player)
				continue;
			UpdateOfflineWallClockPresentationAuthority(*npc, _stamp);
			ActorPresentationResult npc_resolved =
				ResolveLocalWallClockCharacterPresentation(npc, npc->clr, 0, _stamp);
			npc->sprite = npc_resolved.sprite;
			npc->anim = npc_resolved.anim;
			npc->frame = npc_resolved.frame;
			if (npc->inst && npc->sprite && world)
			{
				int reps[4] = { 0,0,0,0 };
				UpdateSpriteInst(world, npc->inst, npc->sprite,
					npc->pos, npc->dir, npc->anim, npc->frame, reps);
			}
			else if (npc->inst && !npc->sprite)
			{
				DeleteInst(npc->inst);
				npc->inst = 0;
			}
		}
	}

	int inventory_width = 39;

	if (ui.show_inventory && camera.scene_shift < inventory_width) // inventory_view width with margins is 58
	{
		camera.scene_shift += f120;
		if (camera.scene_shift > inventory_width)
			camera.scene_shift = inventory_width;
	}
	else
	if (!ui.show_inventory && camera.scene_shift > 0)
	{
		camera.scene_shift-=f120;
		if (camera.scene_shift < 0)
			camera.scene_shift = 0;
	}

	int ss[2] = { camera.scene_shift/2 , 0 };

	if (!authoritative_web_multiplayer_render && weather)
	{
		UpdateWeather(_stamp, local_display_pos[0], local_display_pos[1], local_display_pos[2]);
		if (terrain)
		{
			UpdateSnowAccumulation(weather, terrain, _stamp);
		}
	}

	// Smooth camera Z to reduce per-frame vertical jitter from terrain sampling.
	// Player position (io.pos) is unchanged; only the render camera uses smoothed Z.
	if (!camera.cam_smooth_z_init)
	{
		camera.cam_smooth_z = local_display_pos[2];
		camera.cam_smooth_z_init = true;
	}
	float dz = local_display_pos[2] - camera.cam_smooth_z;
	if (dz > 5.0f || dz < -5.0f) // teleport/respawn snap
		camera.cam_smooth_z = local_display_pos[2];
	else
		camera.cam_smooth_z += dz * 0.5f;
	float cam_pos[3] = { local_display_pos[0], local_display_pos[1], camera.cam_smooth_z };

	auto publish_authoritative_web_core_render_probe = [&]() -> void
	{
		g_web_render_stage_code = 76; // probe: local player visibility
		auto inst_visible_in_world = [&](Inst* inst) -> bool
		{
			return inst &&
				(!world || GetInstWorld(inst) == world) &&
				(GetInstFlags(inst) & INST_VISIBLE);
		};
		auto projected_on_screen = [&](const float pos[3], int view[3]) -> bool // FL-2957: NPC screen projection
		{
			ProjectCoords(renderer, pos, view);
			return view[0] >= 0 && view[0] < width && view[1] >= 0 && view[1] < height;
		};
		debug.dbg_render_local_seen = 1;
		int local_view[3];
		const bool local_on_screen = projected_on_screen(player.pos, local_view);
		const bool local_body_visible = local_on_screen && inst_visible_in_world(player.player_inst);
		if (local_on_screen)
			debug.dbg_visible_local_players++;
		if (local_body_visible)
			debug.dbg_visible_local_body_players++;

		if (!server)
			return;

		g_web_render_stage_code = 77; // probe: remote player visibility walk
		for (Human* draw_h = server->authority.head; draw_h; draw_h = (Human*)draw_h->next)
		{
			if (draw_h == &player || RemoteAuthoritativePresentationIsServerLocalSlot(server, draw_h))
				continue;

			debug.dbg_render_linked_remote_count++;
			if (!debug.dbg_render_remote0_seen)
				debug.dbg_render_remote0_seen = 1;

			int remote_pid = -1;
			if (draw_h >= server->authority.others && draw_h < server->authority.others + server->connection.max_clients)
				remote_pid = (int)(draw_h - server->authority.others);

			int remote_view[3];
			const bool remote_on_screen = projected_on_screen(draw_h->pos, remote_view);
			const bool remote_inst_visible = inst_visible_in_world(draw_h->inst);
			const bool remote_body_visible = remote_on_screen && draw_h->sprite && remote_inst_visible;

			if (remote_on_screen)
				debug.dbg_visible_remote_players++;
			if (remote_body_visible)
				debug.dbg_visible_remote_body_players++;
			else if (remote_on_screen)
			{
				debug.dbg_visible_remote_label_only_players++;
				debug.dbg_latched_remote_label_only_events++;
				LatchRemoteVisibilityIssue(_stamp, &dbg_remote_visibility_issue_this_frame);
			}

				if (debug.dbg_remote0_view_x == -9999)
				{
					debug.dbg_camera_yaw = io.yaw;
					debug.dbg_remote0_on_screen = remote_on_screen ? 1 : 0;
					debug.dbg_remote0_in_list = 1;
					debug.dbg_remote0_has_sprite = draw_h->sprite ? 1 : 0;
				debug.dbg_remote0_has_inst = draw_h->inst ? 1 : 0;
				debug.dbg_remote0_inst_world_match =
					(draw_h->inst && (!world || GetInstWorld(draw_h->inst) == world)) ? 1 : 0;
				debug.dbg_remote0_inst_visible = remote_inst_visible ? 1 : 0;
				debug.dbg_remote0_pid = remote_pid;
				debug.dbg_remote0_hp = 0;
				debug.dbg_remote0_inst_cookie_match =
					(draw_h->inst && GetInstSpriteData(draw_h->inst) == draw_h) ? 1 : 0;
			}
		}

		g_web_render_stage_code = 78; // probe: authoritative item/NPC sampling
		authoritative.world_items_count = 0;
		authoritative.inventory_items_count = 0;
		for (int i = 0; i < (int)(sizeof(authoritative.world_item_ids) / sizeof(authoritative.world_item_ids[0])); i++)
		{
			authoritative.world_item_ids[i] = 0xffff;
			authoritative.world_definition_ids[i] = 0;
			authoritative.world_visual_style_ids[i] = 0;
			authoritative.world_visual_failure_reasons[i] = ACTOR_VISUAL_ITEM_FAILURE_NONE;
		}
		for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
		{
			authoritative.inventory_item_ids[i] = 0xffff;
			authoritative.inventory_definition_ids[i] = 0;
			authoritative.inventory_visual_style_ids[i] = 0;
			authoritative.inventory_visual_failure_reasons[i] = ACTOR_VISUAL_ITEM_FAILURE_NONE;
		}

		authoritative.inventory_items_count = server->authority.auth_item.item_local_owned_count;
		if (authoritative.inventory_items_count > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
			authoritative.inventory_items_count = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
		for (int i = 0; i < authoritative.inventory_items_count; i++)
		{
			uint16_t item_id = server->authority.auth_item.item_local_ids[i];
			const ::AuthoritativeItemState* ai = FindAuthoritativeItemStateById(server, item_id);
			authoritative.inventory_item_ids[i] = item_id;
			authoritative.inventory_definition_ids[i] = ai ? ai->item_definition_id : 0;
			authoritative.inventory_visual_style_ids[i] = ai ? ai->visual_style_id : 0;
			uint8_t inventory_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_NONE;
			ResolveAuthoritativeItemSprite2D(ai, &inventory_failure_reason);
			authoritative.inventory_visual_failure_reasons[i] = inventory_failure_reason;
		}

		if (server->authority.npc_repo.npc_count > 0)
		{
			for (int i = 0; i < (int)server->authority.npc_repo.npc_count; i++)
			{
				const ServerSnapshotNpcRepository::SnapshotNpcState* sn = &server->authority.npc_repo.npcs[i];
				if (sn->entity_id == 0)
					continue;
				int npc_view[3];
				if (projected_on_screen(sn->pos, npc_view))
					debug.dbg_visible_authoritative_npc_markers++;
			}
		}

		};

	// Build narrow render frame input — replaces direct prime_game/server access in render
	RenderFrameReport render_report = {};
	RenderFrameInput render_fi = {};
	render_fi.valid = (server != nullptr);
	render_fi.tracking.tracked_remote_pid = debug.dbg_remote0_pid;
	render_fi.tracking.tracked_npc_entity_id = debug.dbg_tracked_npc0_entity_id;
	render_fi.auto_shot_enabled = AutoShotOnFirstFrameEnabled();
	for (int i = 0; i < MAX_PROJECTILE_VISUALS && render_fi.projectile_line_count < RENDER_MAX_PROJECTILE_LINES; i++)
	{
		if (!projectile_visuals[i].active)
			continue;
		uint64_t elapsed = stamp - projectile_visuals[i].spawn_stamp;
		if (elapsed >= PROJECTILE_VISUAL_LIFETIME_US)
		{
			projectile_visuals[i].active = false;
			continue;
		}
		RenderProjectileLine* line = &render_fi.projectile_lines[render_fi.projectile_line_count++];
		memcpy(line->from, projectile_visuals[i].from, sizeof(line->from));
		memcpy(line->to, projectile_visuals[i].to, sizeof(line->to));
		line->spawn_stamp = projectile_visuals[i].spawn_stamp;
		line->item_definition_id = projectile_visuals[i].item_definition_id;
		line->active = 1;
	}
	if (server)
	{
		render_fi.remote.others = server->authority.others;
		render_fi.remote.max_clients = server->connection.max_clients;
		render_fi.remote.local_player = (const Character*)&player;
		for (int i = 0; i < 64 && i < (int)server->authority.npc_repo.npc_count; i++)
		{
			render_fi.snapshot_npcs.npcs[i].entity_id = server->authority.npc_repo.npcs[i].entity_id;
			render_fi.snapshot_npcs.npcs[i].life_state = server->authority.npc_repo.npcs[i].life_state;
		}
		render_fi.snapshot_npcs.npc_count = (int)server->authority.npc_repo.npc_count;
		// FL-2957 secondary falsifier: copy only active snapshot NPC visuals.
		// RenderFrameInput is zero-initialized, so inactive slots stay empty
		// without repeating the post-rebuild fixed 64-slot copy each frame.
		uint64_t frame_input_npc_visual_copy_begin_us = a3dGetTime();
		int frame_input_npc_visual_copy_slots = 0;
		for (int i = 0; i < 64 && i < (int)server->authority.npc_repo.npc_count; i++)
		{
			render_fi.snapshot_npcs.visuals[i].entity_id = server->authority.npc_repo.visuals[i].entity_id;
			render_fi.snapshot_npcs.visuals[i].presentation_kind_id = server->authority.npc_repo.visuals[i].presentation_kind_id;
			render_fi.snapshot_npcs.visuals[i].inst = server->authority.npc_repo.visuals[i].inst;
			render_fi.snapshot_npcs.visuals[i].hp = server->authority.npc_repo.visuals[i].hp;
			render_fi.snapshot_npcs.visuals[i].max_hp = server->authority.npc_repo.visuals[i].max_hp;
			frame_input_npc_visual_copy_slots++;
		}
		g_web_render_frame_input_npc_visual_copy_duration_us =
			(uint32_t)(a3dGetTime() - frame_input_npc_visual_copy_begin_us);
		g_web_render_frame_input_npc_visual_copy_slots =
			(uint32_t)frame_input_npc_visual_copy_slots;

		// Remote player dots for minimap
		int remote_idx = 0;
		for (Human* draw_h = server->authority.head; draw_h && remote_idx < 64; draw_h = (Human*)draw_h->next)
		{
			if (draw_h == &player || RemoteAuthoritativePresentationIsServerLocalSlot(server, draw_h))
				continue;
			render_fi.remote_dots[remote_idx].pos[0] = draw_h->pos[0];
			render_fi.remote_dots[remote_idx].pos[1] = draw_h->pos[1];
			render_fi.remote_dots[remote_idx].alive = (draw_h->life_state != LIFE_STATE::DEAD) ? 1 : 0;
			remote_idx++;
		}
		render_fi.remote_dot_count = remote_idx;
	}

	// NPC dots for minimap (from player_head linked list)
	{
		int npc_idx = 0;
		for (Character* npc = player_head; npc && npc_idx < 64; npc = npc->next)
		{
			if (npc == &player)
				continue;
			render_fi.npc_dots[npc_idx].pos[0] = npc->pos[0];
			render_fi.npc_dots[npc_idx].pos[1] = npc->pos[1];
			render_fi.npc_dots[npc_idx].is_enemy = npc->enemy ? 1 : 0;
			render_fi.npc_dots[npc_idx].has_data = npc->data ? 1 : 0;
			npc_idx++;
		}
		render_fi.npc_dot_count = npc_idx;
	}

	if (!ObserveRenderEnabled() && MinimapRenderer::PrimeAutoShotProofCapture(&render_fi, local_display_pos, &local_display_dir))
	{
		io.pos[0] = player.pos[0];
		io.pos[1] = player.pos[1];
	}

	// FL-2957/FL-3011: terrain dark must finish for shadow quality, but the old
	// uncapped 512-patch step caused 3.4-second browser longtasks that blocked
	// WebSocket onmessage and produced 8-12s measured lag.
	// The rebuild baseline (73ad4200) had this COMMENTED OUT with the note:
	//   "Not in original 8af87f5c. Commented out until proven safe in a dedicated FL entry."
	// Peak run (manual-20260417-135740) had lag_max=117ms with this disabled.
	// All post-rebuild runs with this enabled: longtask=3.4s, lag_max=7-12s.
	// Keep the high patch ceiling for catch-up, but cap wall-clock work per frame.
	// This preserves the bounded/non-catastrophic lag family while preventing
	// the one-patch under-rendering that left headed shadows barely visible.
	if (terrain && world)
	{
		uint64_t deferred_terrain_begin_us = a3dGetTime();
		// FL-2957/FL-3011 guardrail: do not "fix" lag by starving terrain-dark
		// enough to degrade shadows or NPC/floor visual evidence. The 2000us cap
		// was user-visible product damage; keep the prior visual-preserving cap
		// unless a preservation contract proves another render edit is safe.
		StepDeferredTerrainDarkBootstrap(terrain, world, 512, 12000);
		g_web_render_deferred_terrain_dark_duration_us =
			(uint32_t)(a3dGetTime() - deferred_terrain_begin_us);
	}

	auto materialize_remote_authoritative_presentations = [&]()
	{
		if (!server)
			return;

		uint64_t remote_duplicate_purge_begin_us = a3dGetTime();
		g_web_render_remote_duplicate_purge_deleted_count =
			(uint32_t)RemoteAuthoritativePresentationPurgeDuplicateInsts(server);
		g_web_render_remote_duplicate_purge_duration_us =
			(uint32_t)(a3dGetTime() - remote_duplicate_purge_begin_us);

		Human* rp = server->authority.head;
		int rp_idx = 0;
		while (rp)
		{
			RemoteAuthoritativePresentationLifecycleResult rp_lifecycle =
				RunRemoteAuthoritativePresentationLifecycle(
					this, server, world, renderer, width, height, rp, _stamp);
			if (!rp_lifecycle.processed)
			{
				rp = (Human*)rp->next;
				continue;
			}
			if (!rp->sprite)
			{
				debug.dbg_remote_sprite_null_players++;
				debug.dbg_latched_remote_sprite_null_events++;
				LatchRemoteVisibilityIssue(_stamp, &dbg_remote_visibility_issue_this_frame);
			}
			if (rp_lifecycle.surface.materialized.recreate_reason ==
				REMOTE_ACTOR_PRESENTATION_RECOVERY_HIDDEN)
			{
				debug.dbg_remote_inst_hidden_players++;
				debug.dbg_latched_remote_inst_hidden_events++;
				LatchRemoteVisibilityIssue(_stamp, &dbg_remote_visibility_issue_this_frame);
			}
			else if (rp_lifecycle.surface.materialized.recreate_reason ==
				REMOTE_ACTOR_PRESENTATION_RECOVERY_MISSING)
			{
				debug.dbg_remote_inst_missing_players++;
				debug.dbg_latched_remote_inst_missing_events++;
				LatchRemoteVisibilityIssue(_stamp, &dbg_remote_visibility_issue_this_frame);
			}
			if (rp_idx == 0)
			{
				RemoteMountedWitnessPublishInput mounted_witness = {};
				mounted_witness.remote = rp;
				mounted_witness.remote_pid = rp_lifecycle.tracked_pid;
				mounted_witness.render_tick = rp_lifecycle.render_tick;
				mounted_witness.screen_center_glyph = DebugRemotePresentationCenterGlyph(rp);
				mounted_witness.resolved = &rp_lifecycle.resolved;
				mounted_witness.surface = &rp_lifecycle.surface;
				PublishRemoteMountedWitness(this, server, mounted_witness);
			}

			rp_idx++;
			rp = (Human*)rp->next;
		}
	};
	materialize_remote_authoritative_presentations();

	g_web_render_stage_code = 70; // core world renderer call
	uint64_t core_world_begin_us = a3dGetTime();
	float render_cam_pos[3] = { cam_pos[0], cam_pos[1], cam_pos[2] };
	float render_yaw = io.yaw;
	float render_water = (float)session.water;
	bool render_perspective = session.perspective;
	int render_ss[2] = { ss[0], ss[1] };
	float render_lt[4] = { lt[0], lt[1], lt[2], lt[3] };

	static bool observe_tuple_loaded = false;
	static ObserveRenderViewTuple observe_tuple;
	if (ObserveRenderEnabled() && !observe_tuple_loaded)
	{
		observe_tuple_loaded = true;
		const char* tuple_path = ObserveRenderViewTuplePath();
		if (tuple_path && tuple_path[0])
		{
			if (!LoadObserveRenderViewTuple(tuple_path, &observe_tuple))
			{
				printf("OBSERVE_RENDER_VIEW_TUPLE_PARSE_FAIL path=%s errno=%d\n", tuple_path, errno);
				fflush(stdout);
			}
		}
	}

	if (ObserveRenderEnabled() && observe_tuple.valid)
	{
		render_cam_pos[0] = observe_tuple.cam_pos[0];
		render_cam_pos[1] = observe_tuple.cam_pos[1];
		render_cam_pos[2] = observe_tuple.cam_pos[2];
		render_yaw = observe_tuple.cam_yaw;
		render_water = (float)observe_tuple.water;
		render_perspective = observe_tuple.perspective;
		render_ss[0] = observe_tuple.scene_shift / 2;
		render_ss[1] = 0;
		render_lt[0] = observe_tuple.light[0];
		render_lt[1] = observe_tuple.light[1];
		render_lt[2] = observe_tuple.light[2];
		render_lt[3] = observe_tuple.light[3];
	}

	uint64_t stamp_for_render = _stamp;
	if (ObserveRenderEnabled())
		stamp_for_render = 0;

	::Render(renderer, stamp_for_render, terrain, world, render_water, 1.0f, render_yaw, render_cam_pos, render_lt,
		width, height, ptr, nullptr, render_ss, render_perspective, &render_fi, &render_report);
	g_web_render_core_world_duration_us =
		(uint32_t)(a3dGetTime() - core_world_begin_us);
	g_web_render_stage_code = 71; // core world renderer returned

	debug.ApplyRenderFrameReport(render_report);

	if (authoritative_web_multiplayer_render)
	{
		g_web_render_stage_code = 75; // authoritative web core render proof
		publish_authoritative_web_core_render_probe();
		g_web_render_stage_code = 79; // probe complete
	}

	if (!authoritative_web_multiplayer_render && weather && weather->intensity > 0.0f)
		{
			uint64_t weather_begin_us = a3dGetTime();
			g_web_render_stage_code = 72; // weather composite
			CompositeSnowParticles(weather, ptr, width, height, renderer, _stamp);
			g_web_render_weather_duration_us =
				(uint32_t)(a3dGetTime() - weather_begin_us);
		}

		bool render_runtime_minimap = ui.show_minimap && !ui.show_inventory && !ui.main_menu;
		if (render_runtime_minimap)
		{
			uint64_t minimap_begin_us = a3dGetTime();
			g_web_render_stage_code = 73; // minimap
			MinimapRenderer::Render(ptr, width, height, local_display_pos[0], local_display_pos[1], local_display_pos[2],
			              local_display_dir,
			              io.yaw, camera.zoom, world, terrain, (float)session.water, &render_fi, &render_report);
			g_web_render_minimap_duration_us =
				(uint32_t)(a3dGetTime() - minimap_begin_us);
		}

		if (!authoritative_web_multiplayer_render)
		{
			uint64_t api_hook_begin_us = a3dGetTime();
			g_web_render_stage_code = 81; // script/api frame hook
			(void)akAPI_OnFrame();
			g_web_render_api_hook_duration_us =
				(uint32_t)(a3dGetTime() - api_hook_begin_us);
		}

	PlayerPose interaction_pose = {};
	interaction_pose.pos[0] = player.pos[0];
	interaction_pose.pos[1] = player.pos[1];
	interaction_pose.pos[2] = player.pos[2];
	interaction_pose.dir = player.dir;
	interaction_pose.yaw = io.yaw;
	uint64_t interaction_query_begin_us = a3dGetTime();
	if (world && terrain)
		inventory_view.interaction_query_result =
			QueryInteractions(*world, *terrain, interaction_pose);
	else
		ResetInteractionQueryResult(&inventory_view.interaction_query_result);
	g_web_render_interaction_query_duration_us =
		(uint32_t)(a3dGetTime() - interaction_query_begin_us);
	Item** inrange = inventory_view.interaction_query_result.items;

	// TODO: // TODO: [Backlog Ref] TODO:
	// add GetNearbyStoryThings (that are not items and have story_id>=0)
	// for every story thing in range, ask story teller for interact sprite // TODO: [Backlog Ref] for every story thing in range, ask story teller for interact sprite
	// being displayed in the pick-up list (if returned not null)
	// if selected, notify story teller about it! // TODO: [Backlog Ref] if selected, notify story teller about it!

	{
		uint64_t status_bar_begin_us = a3dGetTime();
		g_web_render_stage_code = 82; // status bar
		AnsiCell status;
		char status_text[80];
		int len_left = 4;
		int len_right = 4;
		int lag_ms = server ? server->connection.lag.lag_ms : 0;
		if (ObserveRenderEnabled())
			lag_ms = 0;
		if (server)
		{
			int len = sprintf(status_text,"ON LINE %4d | %d.%d fps", lag_ms, FPSx10/10, FPSx10%10);
			len_left = len/2;
			len_right = len - len_left;

			status.fg = 16;
			status.bk = dk_green;
			status.gl=' ';
			status.spare = 0;
		}
		else
		{
			int len = sprintf(status_text,"OFF LINE | %d.%d fps", FPSx10/10, FPSx10%10);
			len_left = len/2;
			len_right = len - len_left;

			status.fg = yellow;
			status.bk = dk_red;
			status.gl=' ';
			status.spare = 0;
		}
		AnsiCell* top = ptr + (height-1)*width;
		int x = 0;
		int center = width/2;
		for (; x<center - len_left; x++)
			if (x>=0 && x<width)
				top[x] = status;
		for (; x<center + len_right; x++)
		{
			int i = x - (center - len_left);
			status.gl = status_text[i];
			if (x>=0 && x<width)
				top[x] = status;
		}
		status.gl = ' ';
		for (; x<width; x++)
			if (x>=0 && x<width)
				top[x] = status;
		g_web_render_status_bar_duration_us =
			(uint32_t)(a3dGetTime() - status_bar_begin_us);
	}
	

	// NET_TODO: // TODO: [Backlog Ref] TODO:
	// compare inrange with server confirmed inrange
	// if differs request change by ITEM command // TODO: [Backlog Ref] if differs request change by ITEM command
	// ...

	// NET_TODO: // TODO: [Backlog Ref] TODO:
	// construct inrange array containing only intersection of confirmed and rendered items
	// use it for rendering list of pickup items
	// ...

	// inrange list MUST be already booked by server for us as exclusive!
	// no other player will be able to see these items in their lists!
	// in this way we are sure we can handle picking up safely

	Character* h;
	if (server)
	{
		h = server->authority.head;
		if (!h)
			h = &player;
	}
	else
	{
		h = player_head; // asciid multi-term
	}

	int num = 0;
	bool did_player_talks = false;
	uint64_t talk_overlay_begin_us = a3dGetTime();
	g_web_render_stage_code = 83; // talk / player overlay walk
	while (h)
	{
		{
			num++;
			// todo: ALL TALKS should be sorted by ascending stamp // TODO: [Backlog Ref] todo: ALL TALKS should be sorted by ascending stamp
			// should be Z tested! (so maybe better move it to render)
			// and should have some kind of fade out
			Human* human = (Human*)h;
			for (int i = 0; i < human->talks; i++)
			{
				int speed = 100000 + human->talk[i].box->len*400000/255; // 100000 for len=0 , 500000 for len=255
				int elaps = (int)(stamp - human->talk[i].stamp);
				int dy = elaps / speed; // 10 dy per sec (len=0)

				if (dy <= 30)
				{
					int view[3];
					ProjectCoords(renderer, human->talk[i].pos, view);
					human->talk[i].box->Paint(ptr, width, height, view[0], view[1] + 8 + dy, false, human->name_cp437);
				}
				else
				if (h == &player || server)
				{
					// each player handles its own talks (except server players, they need help)
					free(human->talk[i].box);
					human->talks--;
					for (int j = i; j < human->talks; j++)
						human->talk[j] = human->talk[j + 1];
					i--;
				}
			}
		}

		if (h == &player && server)
		{
			// After processing local player in MP mode, stop — don't follow
			// player.next into NPC list (would cycle back via the hack below)
			h = NULL;
		}
		else if (!h->next && server && h!=&player && !did_player_talks)
		{
			h = &player;
			did_player_talks = true;
		}
		else
			h = h->next;
	}
	g_web_render_talk_overlay_duration_us =
		(uint32_t)(a3dGetTime() - talk_overlay_begin_us);


	// Player world labels and visibility diagnostics (Phase 21)
	if (server)
	{
		uint64_t player_overlay_begin_us = a3dGetTime();
		// Walk visible players for name labels and render diagnostics.
		Human* hp_h = server->authority.head;
		bool did_local = false;
		while (hp_h || !did_local)
		{
			Human* draw_h = hp_h;
			if (!hp_h)
			{
				draw_h = &player;
				did_local = true;
			}
			else
			{
				hp_h = (Human*)hp_h->next;
			}
			if (RemoteAuthoritativePresentationIsServerLocalSlot(server, draw_h))
				continue;
			if (hold_local_authoritative_pose && draw_h == &player)
				continue;

			if (draw_h == &player)
				debug.dbg_render_local_seen = 1;
			else
			{
				debug.dbg_render_linked_remote_count++;
				if (!debug.dbg_render_remote0_seen)
					debug.dbg_render_remote0_seen = 1;
			}

			if (draw_h != &player && debug.dbg_remote0_pid < 0)
			{
				debug.dbg_remote0_hp = 0;
				debug.dbg_remote0_would_skip_death_check = 0;
			}

				int view[3];
				float draw_pose[3] = { 0.0f, 0.0f, 0.0f };
				if (draw_h == &player)
				{
					draw_pose[0] = local_display_pos[0];
					draw_pose[1] = local_display_pos[1];
					draw_pose[2] = local_display_pos[2];
				}
				else
				{
					if (draw_h->remote_presentation_track.last_render_pose_valid)
					{
						draw_pose[0] = draw_h->remote_presentation_track.last_render_pos[0];
						draw_pose[1] = draw_h->remote_presentation_track.last_render_pos[1];
						draw_pose[2] = draw_h->remote_presentation_track.last_render_pos[2];
					}
					else
					{
						draw_pose[0] = draw_h->pos[0];
						draw_pose[1] = draw_h->pos[1];
						draw_pose[2] = draw_h->pos[2];
					}
				}
				ProjectCoords(renderer, draw_pose, view);
			bool on_screen = (view[0] >= 0 && view[0] < width && view[1] >= 0 && view[1] < height);
			bool body_visible = false;
			if (on_screen)
			{
				if (draw_h == &player)
				{
					if (player.player_inst && (GetInstFlags(player.player_inst) & INST_VISIBLE))
						body_visible = true;
				}
				else
				{
					if (draw_h->sprite &&
						draw_h->inst &&
						(!world || GetInstWorld(draw_h->inst) == world) &&
						(GetInstFlags(draw_h->inst) & INST_VISIBLE))
					{
						body_visible = true;
					}
				}
			}
			// Capture detailed diagnostics for first remote player.
			if (draw_h != &player && debug.dbg_remote0_view_x == -9999)
			{
				debug.dbg_camera_yaw = io.yaw;
				debug.dbg_remote0_pos[0] = draw_pose[0];
				debug.dbg_remote0_pos[1] = draw_pose[1];
				debug.dbg_remote0_pos[2] = draw_pose[2];
				debug.dbg_remote0_view_x = view[0];
				debug.dbg_remote0_view_y = view[1];
				debug.dbg_remote0_post_interp_view_x = view[0];
				debug.dbg_remote0_post_interp_view_y = view[1];
				debug.dbg_remote0_on_screen = on_screen ? 1 : 0;
				debug.dbg_remote0_in_list = 1;
				debug.dbg_remote0_has_sprite = draw_h->sprite ? 1 : 0;
				debug.dbg_remote0_has_inst = draw_h->inst ? 1 : 0;
				debug.dbg_remote0_inst_world_match = (draw_h->inst && (!world || GetInstWorld(draw_h->inst) == world)) ? 1 : 0;
				debug.dbg_remote0_inst_visible = (draw_h->inst && (GetInstFlags(draw_h->inst) & INST_VISIBLE)) ? 1 : 0;
				debug.dbg_remote0_hp = 0;
				debug.dbg_remote0_inst_cookie_match =
					(draw_h->inst && GetInstSpriteData(draw_h->inst) == draw_h) ? 1 : 0;
				debug.dbg_remote0_would_skip_death_check = 0;
			}
			if (on_screen)
				{
					if (draw_h == &player)
						debug.dbg_visible_local_players++;
					else
						debug.dbg_visible_remote_players++;
				}
				if (body_visible)
				{
					if (draw_h == &player)
					{
						debug.dbg_visible_local_body_players++;
					}
					else
						debug.dbg_visible_remote_body_players++;
				}
				else if (on_screen && draw_h != &player)
				{
					debug.dbg_visible_remote_label_only_players++;
					debug.dbg_latched_remote_label_only_events++;
					LatchRemoteVisibilityIssue(_stamp, &dbg_remote_visibility_issue_this_frame);
					// Self-heal path for intermittent "label-only" remotes:
					// if the label is on-screen but the sprite inst is missing/hidden,
					// recreate immediately so the body can recover within the same frame.
					(void)RemoteAuthoritativePresentationRecreateInst(
						this, server, world, draw_h, true, 1, 1, 1, 0, 1);
				}

			// Name label (local vs remote) so multiplayer is visually obvious in-world.
			{
				const char* nm = (draw_h == &player) ? player.name_cp437 : draw_h->name_cp437;
				char label[40];
				int llen = snprintf(label, sizeof(label), "%s:%s", (draw_h == &player) ? "YOU" : "NET", nm);
				if (llen < 0) llen = 0;
				if (llen > (int)sizeof(label)) llen = (int)sizeof(label);
				bool label_drawn = false;
				bool is_remote0 = (server &&
					draw_h != &player &&
					draw_h == (Human*)server->authority.head);

				int name_y = view[1] - 1;
				if (draw_h->sprite &&
					draw_h->anim >= 0 &&
					draw_h->anim < draw_h->sprite->anims &&
					draw_h->frame >= 0 &&
					draw_h->frame < draw_h->sprite->anim[draw_h->anim].length)
				{
					int frame_idx = draw_h->sprite->anim[draw_h->anim].frame_idx[draw_h->frame];
					Sprite::Frame* label_frame = draw_h->sprite->atlas + frame_idx;
					name_y = view[1] - label_frame->ref[1] / 2 - 1;
				}
				int name_x = view[0] - llen / 2;
				if (name_y >= 0 && name_y < height)
				{
					AnsiCell* nrow = ptr + name_y * width;
					uint8_t nfg = (draw_h == &player) ? lt_green : lt_cyan;
					for (int i = 0; i < llen; i++)
					{
						int sx = name_x + i;
						if (sx >= 0 && sx < width)
						{
							nrow[sx].gl = label[i];
							nrow[sx].fg = nfg;
							nrow[sx].bk = black;
							nrow[sx].spare = 0;
							label_drawn = true;
						}
					}
				}
				if (is_remote0) { if (label_drawn) debug.dbg_remote0_final_label_drawn = 1; }
			}

			if (did_local && !hp_h) break;
		}
		g_web_render_player_overlay_duration_us =
			(uint32_t)(a3dGetTime() - player_overlay_begin_us);
	}

	// Authoritative NPC snapshot probe.
	// Sprite instances are now the visual truth; keep only an on-screen count for
	// probe/debug, including dead/corpse slots that the server still exports.
	if (server && server->authority.npc_repo.npc_count > 0)
	{
		for (int i = 0; i < (int)server->authority.npc_repo.npc_count; i++)
		{
			const ServerSnapshotNpcRepository::SnapshotNpcState* sn = &server->authority.npc_repo.npcs[i];
			if (sn->entity_id == 0)
				continue;

			int view[3];
			ProjectCoords(renderer, sn->pos, view);
			if (view[0] < 0 || view[0] >= width || view[1] < 0 || view[1] >= height)
				continue;

			debug.dbg_visible_authoritative_npc_markers++;
		}
	}

		// Floating damage numbers (Phase 21)
	{
		uint64_t now = stamp;
		for (int i = 0; i < MAX_DAMAGE_FLOATERS; i++)
		{
			if (!damage_floaters[i].active) continue;

			uint64_t elapsed = now - damage_floaters[i].spawn_stamp;
			if (elapsed >= FLOATER_LIFETIME)
			{
				damage_floaters[i].active = false;
				continue;
			}

			// Float upward: 0 to ~2 units over lifetime
			float t = (float)elapsed / (float)FLOATER_LIFETIME;
			float float_pos[3];
			float_pos[0] = damage_floaters[i].pos[0];
			float_pos[1] = damage_floaters[i].pos[1];
			float_pos[2] = damage_floaters[i].pos[2] + t * 2.0f; // drift up

			int view[3];
			ProjectCoords(renderer, float_pos, view);

			// Render damage text "-N" in red
			char dmg_text[8];
			int dmg_len = sprintf(dmg_text, "-%d", damage_floaters[i].damage);
			int dx = view[0] - dmg_len / 2;
			int dy = view[1];

			// Fade: full brightness for first 70%, then dim
			uint8_t fg = (t < 0.7f) ? lt_red : dk_red;

			if (dy >= 0 && dy < height)
			{
				AnsiCell* drow = ptr + dy * width;
				for (int j = 0; j < dmg_len; j++)
				{
					int sx = dx + j;
					if (sx >= 0 && sx < width)
					{
						drow[sx].gl = dmg_text[j];
						drow[sx].fg = fg;
						drow[sx].bk = black;
						drow[sx].spare = 0;
					}
				}
			}
		}
	}

	int contact_items = 0;
	int contact_item[4] = { -1,-1,-1,-1 };

	inventory_view.UpdateLayout(width,height,camera.scene_shift,ui.bars_pos);

	if (camera.scene_shift > 0)
	{
		bool auth_inventory_only = (server);
		Sprite::Frame* sf = SpriteRegistry::inventory_sprite->atlas;

		int clip[] = { 0,0,inventory_view.layout_width,8 };
		BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y, clip);

		clip[1] = 8; clip[3] = 9;
		for (int i=0; i< inventory_view.layout_reps[0]; i++)
			BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + i, clip);

		clip[1] = 9; clip[3] = 17;
		BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + inventory_view.layout_reps[0], clip);

		clip[1] = 17; clip[3] = 18;
		for (int i = 0; i < inventory_view.layout_reps[1]; i++)
			BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + inventory_view.layout_reps[0] + 8 + i, clip);

		clip[1] = 18; clip[3] = 26;
		BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + inventory_view.layout_reps[0] + 8 + inventory_view.layout_reps[1], clip);

		clip[1] = 26; clip[3] = 27;
		for (int i = 0; i < inventory_view.layout_reps[2]; i++)
			BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + inventory_view.layout_reps[0] + 8 + inventory_view.layout_reps[1] + 8 + i, clip);

		clip[1] = 27; clip[3] = 35;
		BlitSprite(ptr, width, height, sf, inventory_view.layout_x, inventory_view.layout_y + 8 + inventory_view.layout_reps[0] + 8 + inventory_view.layout_reps[1] + 8 + inventory_view.layout_reps[2], clip);

		if (auth_inventory_only)
		{
			inventory_view.animate_scroll = false;
			inventory_view.scroll = 0;
			inventory_view.smooth_scroll = 0;
		}
		else
		if (inventory_view.animate_scroll)
		{
			if (!inventory_view.my_items)
				inventory_view.animate_scroll = false;
			else
			{
				int i = inventory_view.focus;
				int iy = inventory_view.layout_y + inventory_view.my_item[i].xy[1] * 4 + inventory_view.layout_height - 6 - (inventory_view.height * 4 - 1) + inventory_view.scroll;

				Sprite::Frame* isf = 0;
				abort();

				if (iy < inventory_view.layout_y + 9)
				{
					int d = inventory_view.layout_y + 9 - iy;
					inventory_view.scroll += d<f120 ? d : f120;
				}
				if (iy + isf->height > inventory_view.layout_y + inventory_view.layout_height - 5 - 2)
				{
					int d = (iy + isf->height)-(inventory_view.layout_y + inventory_view.layout_height - 5 - 2);
					inventory_view.scroll -= d < f120 ? d : f120;
				}
			}
		}

		if (inventory_view.animate_scroll)
			inventory_view.smooth_scroll = inventory_view.scroll;
		else
		{
			if (inventory_view.smooth_scroll < 0)
				inventory_view.smooth_scroll = 0;
			if (inventory_view.smooth_scroll > inventory_view.layout_max_scroll)
				inventory_view.smooth_scroll = inventory_view.layout_max_scroll;

			if (inventory_view.smooth_scroll < inventory_view.scroll)
			{
				int d = inventory_view.scroll - inventory_view.smooth_scroll;
				inventory_view.scroll-= d < f120 ? d : f120;
			}
			else
			if (inventory_view.smooth_scroll > inventory_view.scroll)
			{
				int d = inventory_view.smooth_scroll - inventory_view.scroll;
				inventory_view.scroll+= d < f120 ? d : f120;
			}
		}

		if (inventory_view.scroll < 0)
			inventory_view.scroll = 0;
		if (inventory_view.scroll > inventory_view.layout_max_scroll)
			inventory_view.scroll = inventory_view.layout_max_scroll;

		int scroll = inventory_view.scroll;

		int dst_clip[4] = { inventory_view.layout_x + 4, inventory_view.layout_y + 8, inventory_view.layout_x + 4 + 4 * inventory_view.width, inventory_view.layout_y + inventory_view.layout_height - 5 -1};
		int frm_clip[4] = { inventory_view.layout_x + 3, inventory_view.layout_y + 7, inventory_view.layout_x + 5 + 4 * inventory_view.width, inventory_view.layout_y + inventory_view.layout_height - 4 -1};

		AnsiCell item_bk = { black,brown,32,0 };
		AnsiCell item_inuse_bk = { yellow,lt_red, 249/*dot*/,0 };

		// for all contacts dragging items // TODO: [Backlog Ref] for all contacts dragging items
		// check if they can drop at where they are, 
		// paint hilight rects

		if (ui.show_inventory && !auth_inventory_only)
		{
			for (int c = 0; c < 4; c++)
				CheckDrop(c, 0, ptr, width, height);
		}

		for (int i = 0; !auth_inventory_only && i < inventory_view.consume_anims; i++)
		{
			ConsumeAnim* a = inventory_view.consume_anim + i;
			int elaps = (int)((_stamp - a->stamp) / 50000); // 20 frames a sec (0.25 sec duration for 5x5 sprite)
			int max_elaps = a->sprite->atlas->height;
		if (elaps >= max_elaps)
			{
				inventory_view.consume_anims--;
				i--;
				continue;
			}

			int ix = inventory_view.layout_x + a->pos[0]*4 + 4;
			int iy = inventory_view.layout_y + a->pos[1]*4 + inventory_view.layout_height - 6 - (inventory_view.height*4-1) + scroll;

			int clip[4] = { ix, iy, ix + a->sprite->atlas->width, iy + a->sprite->atlas->height };
			if (clip[0] < dst_clip[0])
				clip[0] = dst_clip[0];
			if (clip[1] < dst_clip[1])
				clip[1] = dst_clip[1];
			if (clip[2] > dst_clip[2])
				clip[2] = dst_clip[2];
			if (clip[3] > dst_clip[3])
				clip[3] = dst_clip[3];

			BlitSprite(ptr, width, height, a->sprite->atlas, ix, iy + elaps, clip, false, 0);
		}

		int focus_rect[4];
		Item* focus_item = 0;
		const char* item_desc = 0;
		for (int i = 0; !auth_inventory_only && i < inventory_view.my_items; i++)
		{
			int ix = inventory_view.layout_x + inventory_view.my_item[i].xy[0]*4 + 4;
			int iy = inventory_view.layout_y + inventory_view.my_item[i].xy[1]*4 + inventory_view.layout_height - 6 - (inventory_view.height*4-1) + scroll;

			Sprite::Frame* isf = 0;
			abort();

			// if being dragged, attach to contact! // TODO: [Backlog Ref] if being dragged, attach to contact!
			int in_contact = -1;
			for (int c = 0; c < 4; c++)
			{
				if (input.contact[c].action == Input::Contact::ITEM_GRID_DRAG &&
					input.contact[c].my_item == i)
				{
					in_contact = c;
					break;
				}
			}
				
			if (in_contact>=0)
			{
				// fill bk, defer sprite attached to contact
				int fill[4]; // need to clip, can be scrolled out!
				fill[0] = ix < dst_clip[0] ? dst_clip[0] : ix;
				fill[1] = iy < dst_clip[1] ? dst_clip[1] : iy;
				fill[2] = ix+isf->width > dst_clip[2] ? dst_clip[2] : ix+isf->width;
				fill[3] = iy+isf->height > dst_clip[3] ? dst_clip[3] : iy+isf->height;

				if (inventory_view.my_item[i].in_use)
					FillRect(ptr, width, height, fill[0], fill[1], fill[2]-fill[0], fill[3]-fill[1], item_inuse_bk);
				else
					FillRect(ptr, width, height, fill[0], fill[1], fill[2]-fill[0], fill[3]-fill[1], item_bk);

				contact_item[contact_items] = in_contact; // deferred render
				contact_items++;
			}
			else
			{
				if (inventory_view.my_item[i].in_use)
					BlitSprite(ptr, width, height, isf, ix, iy, dst_clip, false, &item_inuse_bk);
				else
					BlitSprite(ptr, width, height, isf, ix, iy, dst_clip, false, &item_bk);
			}

			// FOCUS
			if (i == inventory_view.focus /*&& in_contact<0*/)
			{
				// deferred
				focus_rect[0] = ix - 1;
				focus_rect[1] = iy - 1;
				focus_rect[2] = isf->width + 2;
				focus_rect[3] = isf->height + 2;
				focus_item = inventory_view.my_item[i].item;
				item_desc = inventory_view.my_item[i].desc;
			}
			else
				PaintFrame(ptr, width, height, ix - 1, iy - 1, isf->width + 2, isf->height + 2, frm_clip, black/*fg*/, 255/*bk*/, true/*dbl-line*/,true/*combine*/);
		}

		if (focus_item)
		{
			PaintFrame(ptr, width, height, focus_rect[0], focus_rect[1], focus_rect[2], focus_rect[3], frm_clip, white/*fg*/, 255/*bk*/, true/*dbl-line*/, false/*combine*/);
			if (inventory_view.layout_y + 6 >= 0)
			{
				Item* item = focus_item;
				for (int s = 0; s<32 && item_desc[s]; s++)
				{
					if (inventory_view.layout_x + 4 + s >= 0 && inventory_view.layout_x + 4 + s < width)
					{
						AnsiCell* ac = ptr + inventory_view.layout_x + 4 + s + (inventory_view.layout_y + 6)*width;
						ac->gl = item_desc[s];
					}
				}
			}
		}

		if (scroll > 0 && inventory_view.layout_y + inventory_view.layout_height - 6 >=0 && 
			inventory_view.layout_y + inventory_view.layout_height - 6 <height)
		{
			// overwrite upper inventory_view clip-line with ----
			AnsiCell* row = ptr + (inventory_view.layout_y + inventory_view.layout_height - 6)*width;
			for (int dx = inventory_view.layout_x + 3; dx < inventory_view.layout_x + 36; dx++)
			{
				if (dx >= 0 && dx < width)
				{
					row[dx].fg = black;
					row[dx].gl = 196;
				}
			}
		}

		if (scroll < inventory_view.layout_max_scroll && inventory_view.layout_y + 7 >= 0 && inventory_view.layout_y + 7 < height)
		{
			// overwrite lower inventory_view clip-line with ----
			AnsiCell* row = ptr + (inventory_view.layout_y + 7)*width;
			for (int dx = inventory_view.layout_x + 3; dx < inventory_view.layout_x + 36; dx++)
			{
				if (dx >= 0 && dx < width)
				{
					row[dx].fg = black;
					row[dx].gl = 196;
				}
			}
		}
	}

	// ptr is valid till next Render
	// store it in Game for mouse handling
	authoritative.inventory_items_count = 0;
	ResetAuthoritativeWorldItemPickupStripState(this);
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		authoritative.inventory_item_ids[i] = 0xffff;
		authoritative.inventory_definition_ids[i] = 0;
		authoritative.inventory_visual_style_ids[i] = 0;
		authoritative.inventory_visual_failure_reasons[i] = ACTOR_VISUAL_ITEM_FAILURE_NONE;
	}

	if (!player.talk_box)
	{
		bool auth_world_item_list = (server);
		if (auth_world_item_list)
		{
			RenderAuthoritativeWorldItemPickupStrip(
				this,
				ptr,
				width,
				height,
				camera.scene_shift,
				inrange,
				&authoritative_world_item_appearance);

			if (input.pad_item)
			{
				int x0 = inventory_view.items_xarr[input.pad_item-1];
				int x1 = inventory_view.items_xarr[input.pad_item];
				int y0 = inventory_view.items_ylo;
				int y1 = inventory_view.items_yhi;
				int xc = (x0+x1)/2;
				AnsiCell* ac;
				ac = ptr + x0 + y0 * width; ac->fg = yellow; ac->gl = 192;
				ac = ptr + x1 + y0 * width; ac->fg = yellow; ac->gl = 217;
				ac = ptr + x0 + y1 * width; ac->fg = yellow; ac->gl = 218;
				ac = ptr + x1 + y1 * width; ac->fg = yellow; ac->gl = 191;
				for (int x=x0+1; x<x1; x++)
				{
					ac = ptr + x + y0 * width; ac->fg = yellow; if (x != xc) ac->gl = 196;
					ac = ptr + x + y1 * width; ac->fg = yellow; ac->gl = 196;
				}
				for (int y=y0+1; y<y1; y++)
				{
					ac = ptr + x0 + y * width; ac->fg = yellow; ac->gl = 179;
					ac = ptr + x1 + y * width; ac->fg = yellow; ac->gl = 179;
				}
			}
		}
		else
		{
		int items = 0, items_width = 0;
		int max_height = 0;
		while (inrange[items])
		{
			Sprite::Frame* frame = 0;
			abort();

			if (1 + items_width + frame->width + items >= width - camera.scene_shift)
				break;

			max_height = max_height < frame->height ? frame->height : max_height;
			items_width += frame->width;
			items++;
		}

		// store 
		// NET_TODO: store intersection of confirmed and rendered items! // TODO: [Backlog Ref] TODO:
		inventory_view.items_count = items;
		inventory_view.items_inrange = inrange;

		// crop pad_item index (note: it is in range (0..items_count, where 0 means no selection)
		input.pad_item = input.pad_item < inventory_view.items_count ? input.pad_item : inventory_view.items_count;

		int clip_width = width - camera.scene_shift/2;

		// int items_x = (width - (items_width + items - 1)) / 2; // TODO: [Backlog Ref] int items_x = (width - (items_width + items - 1)) / 2;
		int items_x = camera.scene_shift/2 + (width - (items_width + items - 1)) / 2;
		int items_y = height / 2 - 2;

		items_y -= (items_y - max_height) / 2; // center below player

		if (items)
		{
			int y = items_y - max_height - 1;
			AnsiCell* ac;
			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF);
			ac->fg = black;
			ac->gl = 192;

			y++;

			for (; y < items_y; y++)
			{
				AnsiCell* ac;
				ac = ptr + items_x + y * width;
				ac->bk = AverageGlyph(ac, 0xF);
				ac->fg = black;
				ac->gl = 179;
			}

			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF);
			ac->fg = black;
			ac->gl = 218;

			items_x++;
		}

		for (int i = 0; i < items; i++)
		{
			inventory_view.items_xarr[i] = items_x-1;

			Sprite::Frame* frame = 0;
			abort();

			if (i+1 == input.pad_item)
			{
				int x0 = items_x;
				int x1 = items_x + frame->width - 1;
				int y0 = items_y - max_height;
				int y1 = items_y - 1;

				AnsiCell* ac;
				for (int y=y0; y<=y1; y++)
				{
					for (int x=x0; x<=x1; x++)
					{
						ac = ptr + x + y * width;
						ac->bk = brown;
						ac->fg = black;
						ac->gl = 32;
					}
				}
			}			

			// check if in contact
			int in_contact = -1;
			for (int c = 0; c < 4; c++)
			{
				if (input.contact[c].action == Input::Contact::ITEM_LIST_DRAG ||
					input.contact[c].action == Input::Contact::ITEM_GRID_DRAG)
				{
					if (input.contact[c].item == inrange[i])
					{
						in_contact = c;
						break;
					}
				}
			}

			int y = items_y - (max_height + frame->height) / 2;

			if (in_contact < 0)
			{
				if (input.last_hit_char == '1' + i)
				{
					// check if in inventory_view there is xy to put this item at
					// ...

					if (!PickItem(inrange[i]))
					{
						// display status: "INVENTORY FULL / FRAGGED"
					}
				}

				BlitSprite(ptr, width, height, frame, items_x, y);
			}
			else
			{
				contact_item[contact_items] = in_contact; // deferred render
				contact_items++;
			}

			for (int x = items_x; x < items_x + frame->width; x++)
			{
				AnsiCell* ac;
				ac = ptr + x + items_y * width;
				ac->bk = AverageGlyph(ac, 0xF);
				ac->fg = black;
				ac->gl = 196;
				ac = ptr + x + (items_y - max_height - 1) * width;
				if (in_contact<0 && x == items_x + frame->width / 2)
				{
					ac->bk = black;
					ac->fg = white;
					ac->gl = '1' + i;
				}
				else
				{
					ac->bk = AverageGlyph(ac, 0xF);
					ac->fg = black;
					ac->gl = 196;
				}
			}

			items_x += frame->width;

			y = items_y - max_height - 1;

			AnsiCell* ac;
			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF);
			ac->fg = black;

			if (i == items - 1) // L
			{
				ac->gl = 217;
			}
			else // T
			{
				ac->gl = 193;
			}

			y++;

			for (; y < items_y; y++)
			{
				AnsiCell* ac;
				ac = ptr + items_x + y * width;
				ac->bk = AverageGlyph(ac, 0xF);
				ac->fg = black;
				ac->gl = 179;
			}

			ac = ptr + items_x + y * width;
			ac->bk = AverageGlyph(ac, 0xF);
			ac->fg = black;

			if (i == items - 1) // L
			{
				ac->gl = 191;
			}
			else // T
			{
				ac->gl = 194;
			}

			items_x++;
		}

		inventory_view.items_xarr[items] = items_x - 1;
		inventory_view.items_ylo = items_y - max_height - 1;
		inventory_view.items_yhi = items_y;

		if (input.pad_item)
		{
			// redraw frame hilight
			int x0 = inventory_view.items_xarr[input.pad_item-1];
			int x1 = inventory_view.items_xarr[input.pad_item];
			int y0 = inventory_view.items_ylo;
			int y1 = inventory_view.items_yhi;

			int xc = (x0+x1)/2;

			AnsiCell* ac;

			ac = ptr + x0 + y0 * width;
			ac->fg = yellow;
			ac->gl = 192; // ll

			ac = ptr + x1 + y0 * width;
			ac->fg = yellow;
			ac->gl = 217; // lr

			ac = ptr + x0 + y1 * width;
			ac->fg = yellow;
			ac->gl = 218; // ul

			ac = ptr + x1 + y1 * width;
			ac->fg = yellow;
			ac->gl = 191; // ur

			for (int x=x0+1; x< x1; x++)
			{
				ac = ptr + x + y0 * width;
				ac->fg = yellow;
				if (x != xc)
					ac->gl = 196; // hor

				ac = ptr + x + y1 * width;
				ac->fg = yellow;
				ac->gl = 196; // hor
			}
			for (int y=y0+1; y< y1; y++)
			{
				ac = ptr + x0 + y * width;
				ac->fg = yellow;
				ac->gl = 179; // ver

				ac = ptr + x1 + y * width;
				ac->fg = yellow;
				ac->gl = 179; // ver
			}
		}
		}
	}
	else
	{
		inventory_view.items_count = 0;
		authoritative.world_items_count = 0;
	}

	// When server-authoritative item mutation is active,
	// overlay a compact authoritative-owned-item mirror list in the inventory_view panel.
	// This is UI-only (non-interactive) and helps transition away from local inventory_view truth.
	if (ui.show_inventory && server)
	{
		ClampAuthoritativeInventoryFocus(this);
		AuthoritativeInventoryPanelLayout layout = {};
		GetAuthoritativeInventoryPanelLayout(this, &layout);
		int total_rows = layout.total_rows;
		int rows = layout.visible_rows;
		int panel_x = layout.panel_x;
		int panel_y = layout.panel_y;
		int panel_w = layout.panel_w;
		int list_y = layout.list_y;
		int list_h = layout.list_h;

		AnsiCell auth_panel_bk = { lt_grey, dk_grey, 32, 0 };
		AnsiCell auth_preview_bk = { lt_grey, black, 32, 0 };
		AnsiCell auth_row_bk = { white, black, 32, 0 };
		AnsiCell auth_row_sel_bk = { black, yellow, 32, 0 };

		FillRect(ptr, width, height,
			panel_x, panel_y,
			panel_w, inventory_view.layout_height - 2,
			auth_panel_bk);

		char hdr[48];
		snprintf(hdr, sizeof(hdr), "AUTH INVENTORY %u", (unsigned int)server->authority.auth_item.item_local_owned_count);
		DrawMiniText(ptr, width, height,
			panel_x,
			panel_y,
			hdr, lt_cyan, black, panel_w);

		authoritative.inventory_items_count = total_rows;
		if (authoritative.inventory_items_count > AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
			authoritative.inventory_items_count = AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS;
		for (int i = 0; i < authoritative.inventory_items_count; i++)
		{
			uint16_t item_id = server->authority.auth_item.item_local_ids[i];
			const ::AuthoritativeItemState* ai = FindAuthoritativeItemStateById(server, item_id);
			authoritative.inventory_item_ids[i] = item_id;
			authoritative.inventory_definition_ids[i] = ai ? ai->item_definition_id : 0;
			authoritative.inventory_visual_style_ids[i] = ai ? ai->visual_style_id : 0;
			uint8_t inventory_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_NONE;
			ResolveAuthoritativeItemSprite2D(ai, &inventory_failure_reason);
			authoritative.inventory_visual_failure_reasons[i] = inventory_failure_reason;
		}

		if (total_rows > 0)
		{
			int selected_index = inventory_view.authoritative_inventory_focus;
			if (selected_index < 0)
				selected_index = 0;
			if (selected_index >= total_rows)
				selected_index = total_rows - 1;
			int visible_start = layout.visible_start;

			uint16_t selected_item_id = server->authority.auth_item.item_local_ids[selected_index];
			const ::AuthoritativeItemState* selected_ai = FindAuthoritativeItemStateById(server, selected_item_id);
			uint8_t selected_visual_failure_reason = ACTOR_VISUAL_ITEM_FAILURE_NONE;
			Sprite* selected_spr = ResolveAuthoritativeItemSprite2D(
				selected_ai, &selected_visual_failure_reason);
			if (selected_index >= 0 && selected_index < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
				authoritative.inventory_visual_failure_reasons[selected_index] = selected_visual_failure_reason;
			Sprite::Frame* selected_frame = (selected_spr && selected_spr->atlas) ? selected_spr->atlas : 0;
			int preview_x = panel_x;
			int preview_y = inventory_view.layout_y + 3;
			FillRect(ptr, width, height,
				panel_x, preview_y,
				panel_w, 4,
				auth_preview_bk);

			if (selected_frame)
			{
				int clip[4] = {
					inventory_view.layout_x + 1,
					inventory_view.layout_y + 3,
					inventory_view.layout_x + inventory_view.layout_width - 2,
					inventory_view.layout_y + inventory_view.layout_height - 2
				};
				BlitSprite(ptr, width, height, selected_frame, preview_x, preview_y, clip, false, 0);
			}

			char sel_line[64];
			const bool selected_equipped =
				selected_ai && (selected_ai->v2_state_flags & APPEARANCE_ITEM_STATE_EQUIPPED);
			snprintf(sel_line, sizeof(sel_line), "SEL %d:%u %s%s",
				selected_index,
				(unsigned int)selected_item_id,
				GetAuthoritativeItemLabel(selected_ai),
				selected_equipped ? " *EQUIPPED*" : "");
			DrawMiniText(ptr, width, height,
				panel_x + 8,
				preview_y,
				sel_line, white, black, panel_w - 8);
			char action_line[64];
			if (selected_equipped && IsAuthoritativeMountItem(selected_ai))
				snprintf(action_line, sizeof(action_line), "U DISMOUNT  0/Y DROP  ARROWS SELECT");
			else
				snprintf(action_line, sizeof(action_line), "U EQUIP/USE  0/Y DROP  ARROWS SELECT");
			DrawMiniText(ptr, width, height,
				panel_x + 8,
				preview_y + 1,
				action_line, lt_cyan, black, panel_w - 8);

			for (int i = 0; i < rows; i++)
			{
				int item_index = visible_start + i;
				char line[64];
				uint16_t item_id = server->authority.auth_item.item_local_ids[item_index];
				bool selected = (item_index == inventory_view.authoritative_inventory_focus);
				const ::AuthoritativeItemState* ai = FindAuthoritativeItemStateById(server, item_id);
				bool equipped = (ai && (ai->v2_state_flags & APPEARANCE_ITEM_STATE_EQUIPPED) != 0);
				int row_y = list_y + i;
				FillRect(ptr, width, height,
					panel_x, row_y,
					panel_w, 1,
					selected ? auth_row_sel_bk : auth_row_bk);
				snprintf(line, sizeof(line), "%c %02d  %05u  %-8s %s",
					selected ? '>' : ' ',
					item_index + 1,
					(unsigned int)item_id,
					GetAuthoritativeItemLabel(ai),
					equipped ? "EQUIPPED" : "READY");
				DrawMiniText(ptr, width, height,
					panel_x + 1,
					row_y,
					line,
					selected ? black : white,
					selected ? yellow : black,
					panel_w - 2);
			}
			if (total_rows > rows)
			{
				char range_line[64];
				snprintf(range_line, sizeof(range_line), "SHOWING %d-%d OF %d",
					visible_start + 1,
					visible_start + rows,
					total_rows);
				DrawMiniText(ptr, width, height,
					panel_x,
					inventory_view.layout_y + inventory_view.layout_height - 4,
					range_line, lt_cyan, black, panel_w);
			}
		}
		else
		{
			FillRect(ptr, width, height,
				panel_x, list_y,
				panel_w, list_h,
				auth_row_bk);
			DrawMiniText(ptr, width, height,
				panel_x + 1,
				list_y + 1,
				"NO AUTH ITEMS OWNED", white, black, panel_w - 2);
		}

		DrawMiniText(ptr, width, height,
			panel_x,
			inventory_view.layout_y + inventory_view.layout_height - 3,
			"OWNED ITEMS ARE AUTHORITATIVE", lt_cyan, black, panel_w);
	}


	for (int ic = 0; ic < contact_items; ic++)
	{
		int in_contact = contact_item[ic];
		int cp[2] = { input.contact[in_contact].pos[0], input.contact[in_contact].pos[1] };
		ScreenToCell(cp);

		Sprite::Frame* frame = 0;
		abort();

		cp[0] -= frame->width / 2;

		if (ic==0) // if dragged by touch, leave it above finger // TODO: [Backlog Ref] if dragged by touch, leave it above finger
			cp[1] -= frame->height / 2;

		// if this item is currently in use // TODO: [Backlog Ref] if this item is currently in use
		// trap it inside inventory_view
		if (input.contact[in_contact].action == Input::Contact::ITEM_GRID_DRAG &&
			inventory_view.my_item[input.contact[in_contact].my_item].in_use)
		{
			if (cp[0] < inventory_view.layout_x)
				cp[0] = inventory_view.layout_x;
			if (cp[0] + frame->width > inventory_view.layout_x + inventory_view.layout_width)
				cp[0] = inventory_view.layout_x + inventory_view.layout_width - frame->width;

			if (cp[1] < inventory_view.layout_y)
				cp[1] = inventory_view.layout_y;
			if (cp[1] + frame->height > inventory_view.layout_y + inventory_view.layout_height)
				cp[1] = inventory_view.layout_y + inventory_view.layout_height - frame->height;
		}

		BlitSprite(ptr, width, height, frame, cp[0], cp[1]);
	}

	{
		HPBar bar;

		int bar_w = (width - 2 * 7) / 3;
		const int info_pad = 1;
		const int info_min_w = 28;
		int info_gap = width - 2 * ui.bars_pos - 2 * bar_w - 2 * info_pad;
		if (info_gap < info_min_w)
		{
			int shrink = (info_min_w - info_gap + 1) / 2;
			bar_w -= shrink;
			if (bar_w < 12)
				bar_w = 12;
			info_gap = width - 2 * ui.bars_pos - 2 * bar_w - 2 * info_pad;
		}
		int hp_xyw[] = { ui.bars_pos, height - bar.height, bar_w };
		int mp_xyw[] = { width - ui.bars_pos - bar_w, height - bar.height, bar_w };

		//static int f = 0; // TODO: [Backlog Ref] static int f = 0;
		//f++;
		// float val = 0.5*(1.0 + sinf(f*0.02)); // TODO: [Backlog Ref] float val = 0.5*(1.0 + sinf(f*0.02));

		int hp_cur = (int)debug.dbg_self_hp;
		int hp_max = (int)debug.dbg_self_max_hp;
		if (hp_max <= 0)
			hp_max = MP_MAX_HP;
		if (hp_cur < 0)
			hp_cur = 0;
		if (hp_cur > hp_max)
			hp_cur = hp_max;
		float val = (hp_max > 0) ? ((float)hp_cur / (float)hp_max) : 0.0f;
		bar.Paint(ptr, width, height, val, hp_xyw, false);
		bar.Paint(ptr, width, height, 1.0f, mp_xyw, true);

		if (info_gap >= 16)
		{
			int info_x = ui.bars_pos + bar_w + info_pad;
			int info_w = width - 2 * ui.bars_pos - 2 * bar_w - 2 * info_pad;
			char line1[64];
			char line2[64];
			float local_info_pos[3] = { local_display_pos[0], local_display_pos[1], local_display_pos[2] };
			float local_info_dir = local_display_dir;
			snprintf(line1, sizeof(line1), "x%.1f y%.1f z%.1f", local_info_pos[0], local_info_pos[1], local_info_pos[2]);
			snprintf(line2, sizeof(line2), "yaw %.1f dir %.1f zm %.2f", io.yaw, local_info_dir, camera.zoom);
			DrawMiniText(ptr, width, height, info_x, height - bar.height + 1, line1, white, black, info_w);
			DrawMiniText(ptr, width, height, info_x, height - bar.height + 2, line2, white, black, info_w);
		}

		BlitSprite(ptr, width, height, SpriteRegistry::character_button->atlas + 0, ui.bars_pos-7, height - SpriteRegistry::character_button->atlas[0].height);
		BlitSprite(ptr, width, height, SpriteRegistry::character_button->atlas + 1, 7-ui.bars_pos + width - SpriteRegistry::character_button->atlas[1].width, height - SpriteRegistry::character_button->atlas[1].height);
	}

	if (player.talk_box)
		player.talk_box->Paint(ptr, width, height, width / 2 + camera.scene_shift/2, height / 2 + 8, (ui.TalkBox_blink & 63) < 32);

	if (!ui.show_inventory && server && server->authority.auth_item.item_local_owned_count > 0)
	{
		char auth_hint[80];
		int hint_index = inventory_view.authoritative_inventory_focus;
		if (hint_index < 0)
			hint_index = 0;
		if (hint_index >= (int)server->authority.auth_item.item_local_owned_count)
			hint_index = (int)server->authority.auth_item.item_local_owned_count - 1;
		uint16_t hint_item_id = server->authority.auth_item.item_local_ids[hint_index];
		const ::AuthoritativeItemState* hint_ai = FindAuthoritativeItemStateById(server, hint_item_id);
		snprintf(auth_hint, sizeof(auth_hint), "AUTH ITEMS %u  %s  B/I INVENTORY",
			(unsigned int)server->authority.auth_item.item_local_owned_count,
			GetAuthoritativeItemLabel(hint_ai));
		DrawMiniText(ptr, width, height, 1, 1, auth_hint, lt_cyan, black, width - 2);
	}

	if (ui.show_buts && ui.bars_pos < 7)
		ui.bars_pos++;
	if (!ui.show_buts && ui.bars_pos > 0)
		ui.bars_pos--;

	int kh = keyb.Height(width, height);

	if (ui.show_keyb || ui.keyb_hide < kh)
	{

		// w = (sprite_w-1) * 10.5 + 1

		// (sprite_w-1) * 10.5 = w-1
		// sprite_w-1 = (w-1)/10.5
		// sprite_w = (w-1)/10.5 + 1


		uint8_t key[32];
		for (int i = 0; i < 32; i++)
			key[i] = input.keyb_key[i] | input.key[i];
		keyb.Paint(ptr, width, height, ui.keyb_hide, key, input.pad_connected);
	}
	
	if (ui.show_keyb) // TODO: [Backlog Ref] if (ui.show_keyb)
	{
		if (ui.keyb_hide > kh)
			ui.keyb_hide = kh;

		if (ui.keyb_hide > 0)
		{
			ui.keyb_hide -= f120;
			if (ui.keyb_hide < 0)
				ui.keyb_hide = 0;
		}
	}
	else
	{
		if (ui.keyb_hide < kh)
		{
			ui.keyb_hide += f120;
			if (ui.keyb_hide > kh)
				ui.keyb_hide = kh;
		}
	}

	input.last_hit_char = 0;

	uint64_t prev_stamp = stamp;
	stamp = _stamp;

	if (server)
	{
		const bool authoritative_snapshot_ready =
			(server->authority.snapshot_client.snapshot_packets > 0);

		// Timeout stale lag_wait: if no response arrived within 500ms, allow next ping.
		// Handles EAGAIN-dropped responses on the server IO thread (no retry path exists).
		if (server->connection.lag.lag_wait && stamp - server->connection.lag.last_lag >= 500000)
		{
			server->connection.lag.lag_wait = false;
			server->connection.lag.lag_wait_timeout_count++;
		}

		if (authoritative_snapshot_ready &&
			stamp - server->connection.lag.last_lag >= 100000 && !server->connection.lag.lag_wait) // 10x per sec
		{
			server->connection.lag.last_lag = stamp;
			server->connection.lag.lag_wait = true;
			server->connection.lag.lag_last_request_stamp = stamp;
			server->connection.lag.lag_request_count++;
			server->connection.lag.lag_trace_request_seq = ++server->connection.lag.lag_trace_next_seq;
			server->connection.lag.lag_trace_request_stamp = stamp;

			STRUCT_REQ_LAG req_lag = { 0 };
			req_lag.token = 'L';
			uint32_t s = (uint32_t)stamp;
			req_lag.stamp[0] = s & 0xFF;
			req_lag.stamp[1] = (s >> 8) & 0xFF;
			req_lag.stamp[2] = (s >> 16) & 0xFF;
			req_lag.trace_seq = server->connection.lag.lag_trace_request_seq;
			req_lag.client_send_us32 = (uint32_t)a3dGetTime();
			server->connection.lag.lag_trace_send_call_stamp = req_lag.client_send_us32;

#ifdef __EMSCRIPTEN__
			const bool lag_probe_send_ok = server->Send((const uint8_t*)&req_lag, sizeof(STRUCT_REQ_LAG));
			render_lag_probe_send_us32 = (uint32_t)a3dGetTime();
			g_web_render_lag_probe_sent_this_frame = lag_probe_send_ok ? 1u : 0u;
			g_web_render_lag_probe_send_stage_code = g_web_render_stage_code;
			g_web_render_lag_probe_send_seq = req_lag.trace_seq;
			if (!lag_probe_send_ok)
			{
				server->connection.lag.lag_wait = false;
				server->connection.lag.lag_request_send_fail_count++;
			}
#else
			(void)server->Send((const uint8_t*)&req_lag, sizeof(STRUCT_REQ_LAG));
			render_lag_probe_send_us32 = (uint32_t)a3dGetTime();
			g_web_render_lag_probe_sent_this_frame = 1u;
			g_web_render_lag_probe_send_stage_code = g_web_render_stage_code;
			g_web_render_lag_probe_send_seq = req_lag.trace_seq;
#endif
		}

		const MpMoveSendLifecycleResult send_local_result = SendLocalNetworkUpdates(
			this->player.mp_move,
			server,
			stamp,
			io,
			terrain,
			world,
			(float)this->session.water);
		if (send_local_result.jump_consumed)
			this->input.jump = false;

			// Remote authoritative presentations were materialized before the core
			// world renderer so remote bodies and name labels share one render pose.

			// Ping display (top-right corner)
			{
			char ping_text[16];
			int ping_lag_ms = ObserveRenderEnabled() ? 0 : server->connection.lag.lag_ms;
			int ping_len = sprintf(ping_text, "PING:%dms", ping_lag_ms);

			uint8_t ping_fg, ping_bk;
			if (ping_lag_ms < 100)
			{
				ping_fg = white;
				ping_bk = dk_green;
			}
			else if (ping_lag_ms < 200)
			{
				ping_fg = black;
				ping_bk = yellow;
			}
			else
			{
				ping_fg = white;
				ping_bk = dk_red;
			}

			int px = width - ping_len - 1;
			if (px < 0) px = 0;
			int py = height - 1;
			AnsiCell* row = ptr + py * width;
			for (int i = 0; i < ping_len && px + i < width; i++)
			{
				row[px + i].gl = ping_text[i];
				row[px + i].fg = ping_fg;
				row[px + i].bk = ping_bk;
				row[px + i].spare = 0;
			}
		}

		// Player list overlay (Tab key held)
		if (input.IsKeyDown(A3D_TAB))
		{
			// Count connected players
			int player_count = 1; // local player
			Human* rp = server->authority.head;
			while (rp)
			{
				player_count++;
				rp = (Human*)rp->next;
			}

			// Overlay dimensions
			int overlay_w = 22;
			int overlay_h = player_count + 2; // header + players + border
			int ox = width - overlay_w - 1;
			int oy = 1;
			if (ox < 0) ox = 0;

			// Draw header
			if (oy < height && ox >= 0)
			{
				char hdr[32];
				int hdr_len = sprintf(hdr, " Players (%d/%d) ", player_count, server->connection.max_clients);
				AnsiCell* hrow = ptr + oy * width;
				for (int i = 0; i < overlay_w && ox + i < width; i++)
				{
					hrow[ox + i].bk = dk_blue;
					hrow[ox + i].fg = white;
					hrow[ox + i].gl = (i < hdr_len) ? hdr[i] : ' ';
					hrow[ox + i].spare = 0;
				}
			}

			// Draw local player
			int row_y = oy + 1;
			if (row_y < height)
			{
				AnsiCell* prow = ptr + row_y * width;
				char pline[32];
				int plen = sprintf(pline, " %-16s %3d", player.name_cp437, server->connection.lag.lag_ms);
				for (int i = 0; i < overlay_w && ox + i < width; i++)
				{
					prow[ox + i].bk = dk_grey;
					prow[ox + i].fg = lt_green;
					prow[ox + i].gl = (i < plen) ? pline[i] : ' ';
					prow[ox + i].spare = 0;
				}
			}

			// Draw remote players
			rp = server->authority.head;
			while (rp)
			{
				row_y++;
				if (row_y < height)
				{
					AnsiCell* prow = ptr + row_y * width;
					char pline[32];
					int plen = sprintf(pline, " %-16s %3d", rp->name_cp437, server->connection.lag.lag_ms);
					for (int i = 0; i < overlay_w && ox + i < width; i++)
					{
						prow[ox + i].bk = dk_grey;
						prow[ox + i].fg = (rp->life_state != LIFE_STATE::DEAD) ? lt_cyan : dk_red;
						prow[ox + i].gl = (i < plen) ? pline[i] : ' ';
						prow[ox + i].spare = 0;
					}
				}
				rp = (Human*)rp->next;
			}
		}
	}

	if (ui.show_gamepad)
		PaintGamePad(ptr, width, height, stamp);

	PaintMenu(ptr, width, height);
	/*
	Font1Paint(ptr, width, height, 10, 22, "0.123456789\nZOMBEK DOMBEK\nZIBALABULGAMUF?", 0);
	Font1Paint(ptr, width, height, 10, 10, "0.123456789\nZOMBEK DOMBEK\nZIBALABULGAMUF?", 1);
	*/

	if (ui.show_cam_overlay && ui.menu_depth < 0)
	{
		const char* map_name = g_loaded_a3d_path[0] ? g_loaded_a3d_path : "-";
		const char* slash = strrchr(map_name, '/');
		const char* bslash = strrchr(map_name, '\\');
		if (slash || bslash)
		{
			const char* cut = slash > bslash ? slash : bslash;
			if (cut && cut[1])
				map_name = cut + 1;
		}

		float lt_dir[3] = { session.light[0], session.light[1], session.light[2] };
		float ln = lt_dir[0] * lt_dir[0] + lt_dir[1] * lt_dir[1] + lt_dir[2] * lt_dir[2];
		if (ln > 0.001f)
		{
			float inv = 1.0f / sqrtf(ln);
			lt_dir[0] *= inv;
			lt_dir[1] *= inv;
			lt_dir[2] *= inv;
		}

		char overlay[512];
		snprintf(overlay, sizeof(overlay),
			"CAM pos %.2f %.2f %.2f\n"
			"yaw %.2f zoom %.2f\n"
			"light %.2f %.2f %.2f amb %.2f\n"
			"water %d map %s",
			io.pos[0], io.pos[1], io.pos[2],
			io.yaw, camera.zoom,
			lt_dir[0], lt_dir[1], lt_dir[2], session.light[3],
			session.water, map_name);

		Font1Paint(ptr, width, height, 1, 1, overlay, FONT1_GREY_SKIN);
	}

	static bool observe_render_shot_queued = false;
	if (ObserveRenderEnabled() && !observe_render_shot_queued)
	{
		bool have_snapshot =
			!server ||
			(server->authority.snapshot_client.last_snapshot_seq != 0 &&
				server->authority.snapshot_client.last_snapshot_tick != 0);
		if (have_snapshot)
		{
			input.shot = true;
			observe_render_shot_queued = true;
			printf("[observe-render] queued shot capture\n");
			fflush(stdout);
		}
	}

	static bool auto_shot_fired = false;
	if (!auto_shot_fired && AutoShotOnFirstFrameEnabled())
	{
		input.shot = true;
		auto_shot_fired = true;
		ConsumeAutoShotFlag();
		printf("[auto-shot] queued first-frame shot capture\n");
		fflush(stdout);
	}

	if (input.shot)
	{
		input.shot = false;
		char shot_xp_path[1200];
		char shot_json_path[1200];
		char resolved_jsonl_path[1200];

		const char* out_dir = ObserveRenderEnabled() ? ObserveRenderOutputDir() : nullptr;
		if (out_dir && out_dir[0])
		{
			snprintf(shot_xp_path, sizeof(shot_xp_path), "%s/%s", out_dir, "source-shot.xp");
			snprintf(shot_json_path, sizeof(shot_json_path), "%s/%s", out_dir, "source-shot.json");
			snprintf(resolved_jsonl_path, sizeof(resolved_jsonl_path), "%s/%s", out_dir, "source-resolved-cells.jsonl");
		}
		else
		{
			snprintf(shot_xp_path, sizeof(shot_xp_path), "%sshot.xp", base_path);
			snprintf(shot_json_path, sizeof(shot_json_path), "%sshot.json", base_path);
			resolved_jsonl_path[0] = 0;
		}

		FILE* f = fopen(shot_xp_path, "wb");
		if (f)
		{
			uint32_t hdr[4] = { (uint32_t)-1, (uint32_t)1, (uint32_t)width, (uint32_t)height };
			fwrite(hdr, sizeof(uint32_t), 4, f);
			for (int x = 0; x < width; x++)
			{
				for (int y = height - 1; y >= 0; y--)
				{
					AnsiCell* c = ptr + y * width + x;
					int fg = c->fg - 16;
					int f_r = (fg % 6) * 51; fg /= 6;
					int f_g = (fg % 6) * 51; fg /= 6;
					int f_b = (fg % 6) * 51; fg /= 6;

					int bk = c->bk - 16;
					int b_r = (bk % 6) * 51; bk /= 6;
					int b_g = (bk % 6) * 51; bk /= 6;
					int b_b = (bk % 6) * 51; bk /= 6;

					uint8_t f_rgb[3] = { (uint8_t)f_b,(uint8_t)f_g,(uint8_t)f_r };
					uint8_t b_rgb[3] = { (uint8_t)b_b,(uint8_t)b_g,(uint8_t)b_r };
					uint32_t chr = c->gl;

					fwrite(&chr, sizeof(uint32_t), 1, f);
					fwrite(f_rgb, 1, 3, f);
					fwrite(b_rgb, 1, 3, f);
				}
			}

			fclose(f);
			if (ObserveRenderEnabled())
			{
				ObserveRenderViewTuple tuple;
				bool have_tuple = false;
				const char* tuple_path = ObserveRenderViewTuplePath();
				if (tuple_path && tuple_path[0])
					have_tuple = LoadObserveRenderViewTuple(tuple_path, &tuple);

				FILE* jf = fopen(shot_json_path, "wb");
				if (jf)
				{
					fprintf(jf, "{\n");
					fprintf(jf, "  \"version\": 1,\n");
					fprintf(jf, "  \"stamp\": %llu,\n", (unsigned long long)0);
					fprintf(jf, "  \"size\": {\"width\": %d, \"height\": %d},\n", width, height);
					if (g_loaded_a3d_path[0])
					{
						fprintf(jf, "  \"map_path\": ");
						WriteJsonString(jf, g_loaded_a3d_path);
						fprintf(jf, ",\n");
					}
					else
						fprintf(jf, "  \"map_path\": null,\n");

					fprintf(jf, "  \"camera\": {\n");
					if (have_tuple && tuple.valid)
						fprintf(jf, "    \"pos\": [%.4f, %.4f, %.4f],\n", tuple.cam_pos[0], tuple.cam_pos[1], tuple.cam_pos[2]);
					else
						fprintf(jf, "    \"pos\": [%.4f, %.4f, %.4f],\n", io.pos[0], io.pos[1], io.pos[2]);
					fprintf(jf, "    \"yaw\": %.4f,\n", have_tuple && tuple.valid ? tuple.cam_yaw : io.yaw);
					fprintf(jf, "    \"zoom\": %.4f,\n", have_tuple && tuple.valid ? tuple.cam_zoom : camera.zoom);
					fprintf(jf, "    \"perspective\": %s,\n", (have_tuple && tuple.valid ? tuple.perspective : session.perspective) ? "true" : "false");
					fprintf(jf, "    \"scene_shift\": %d,\n", have_tuple && tuple.valid ? tuple.scene_shift : camera.scene_shift);
					fprintf(jf, "    \"cam_shift\": %d\n", 0);
					fprintf(jf, "  },\n");

					fprintf(jf, "  \"light\": {\n");
					if (have_tuple && tuple.valid)
						fprintf(jf, "    \"dir\": [%.4f, %.4f, %.4f],\n", tuple.light[0], tuple.light[1], tuple.light[2]);
					else
						fprintf(jf, "    \"dir\": [%.4f, %.4f, %.4f],\n", session.light[0], session.light[1], session.light[2]);
					fprintf(jf, "    \"ambience\": %.4f\n", have_tuple && tuple.valid ? tuple.light[3] : session.light[3]);
					fprintf(jf, "  },\n");

					fprintf(jf, "  \"water\": %d\n", have_tuple && tuple.valid ? tuple.water : session.water);
					fprintf(jf, "}\n");
					fclose(jf);
				}
			}
			else
			{
				WriteShotJson(shot_json_path, _stamp, &io, this, width, height);
			}

			if (ObserveRenderEnabled() && resolved_jsonl_path[0])
			{
				FILE* rf = fopen(resolved_jsonl_path, "wb");
				if (rf)
				{
					WriteResolvedCellsJsonl(rf, ptr, width, height);
					fclose(rf);
				}
					printf("OBSERVE_RENDER_OK output_dir=%s shot_xp=%s resolved_cells=%s view_tuple=%s schema_version=%s\n",
						out_dir ? out_dir : "",
						shot_xp_path,
						resolved_jsonl_path,
						ObserveRenderViewTuplePath() ? ObserveRenderViewTuplePath() : "",
						ObserveRenderSchemaVersion() ? ObserveRenderSchemaVersion() : "");
				fflush(stdout);
				// RQ-11 Phase C: shutdown must be graceful (no leaked owned local server).
				// Queue SDL_QUIT when available so the normal cleanup path runs.
#if !defined(EDITOR) && !defined(__EMSCRIPTEN__)
				StopNormalGameAuthoritativeSession();
#endif
#if !defined(PURE_TERM) && !defined(__EMSCRIPTEN__)
				if (a3dQueueSdlQuit() <= 0)
					exit(0);
				return;
#else
				exit(0);
#endif
			}
		}
	}

	// Publish a stable completed-frame snapshot for async web diagnostics.
	PublishCompletedFrameDebugTelemetry();
	if (hold_local_authoritative_pose)
	{
		g_web_render_stage_code = 74; // waiting for authoritative local pose after reconnect/bootstrap
		PaintLocalAuthorityHoldScreen(ptr, width, height);
	}
	if (g_web_render_lag_probe_sent_this_frame && render_lag_probe_send_us32)
	{
		g_web_render_lag_probe_send_to_render_end_us =
			(uint32_t)((uint32_t)a3dGetTime() - render_lag_probe_send_us32);
	}
}

void Game::OnSize(int w, int h, int fw, int fh)
{
	bool pad = input.pad_connected;
	memset(&input, 0, sizeof(Input));
	input.pad_connected = pad;
	input.size[0] = w;
	input.size[1] = h;
	session.font_size[0] = fw;
	session.font_size[1] = fh;

	MainMenu_OnSize(w,h,fw,fh);
}

void Game::PaintMenu(AnsiCell* ptr, int width, int height)
{
	if (ui.menu_depth<0)
		return;

	const Menu* m = game_menu;
	const char* title = "MENU";
	for (int d=0; d<ui.menu_depth; d++)
	{
		title = m[ ui.menu_stack[d] ].str;
		m = m[ ui.menu_stack[d] ].sub;
	}

	// right align
	int x = width-5;
	int y = height-10;

	// paint title
	{
		int w = 0, h = 0;
		Font1Size(title,&w,&h);
		Font1Paint(ptr,width,height,3+x-w,y,title,FONT1_PINK_SKIN);
		y -= h+2;
	}


	int i=0;
	while(m[i].str)
	{
		int w = 0, h = 0;
		Font1Size(m[i].str,&w,&h);

		int skin = i == ui.menu_stack[ui.menu_depth] ? FONT1_GOLD_SKIN : FONT1_GREY_SKIN;
		Font1Paint(ptr,width,height,x-w,y,m[i].str,skin);

		const char* str = 0;
		if (m[i].sub)
			str = "\x03";
		else
		if (m[i].getter)
		{
			MenuIO menu_io = {};
			menu_io.session = &session;
			menu_io.ui = &ui;
			menu_io.screen_size[0] = input.size[0];
			menu_io.screen_size[1] = input.size[1];
			menu_io.close_game = this;
			str = m[i].getter(&menu_io) ? "\x02" : "\x01";
		}

		if (str)
			Font1Paint(ptr,width,height,x,y,str,FONT1_PINK_SKIN);

		y -= h+1;
		i++;
	}


}

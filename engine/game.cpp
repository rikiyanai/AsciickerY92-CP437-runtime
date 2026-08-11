/**
 * @file game.cpp
 * @brief Main game logic plus the front door into compiled actor presentation.
 *
 * Actor presentation is ActorVisualProfile-owned. The runtime stores
 * server-owned AppearanceStateV2 ids, builds a CompiledActorVisualKey, exact
 * looks up a CompiledActorVisualRow, composes that ordered layer list, and
 * advances the row-owned playback frame. It must not revive selector tables,
 * mounted admission lookup, condition evaluation, geometry fallback,
 * attachment order inference, default slot insertion, or filename/sprite-
 * family guessing.
 *
 * Current presentation owner chain:
 * - scripts/validate_actor_visual_profiles.py proves authored profile content.
 * - scripts/compile_actor_visual_profiles.py emits CompiledActorVisualRows
 *   keyed by CompiledActorVisualKey.
 * - server/server_tick.cpp owns presentation_kind_id and sends it through
 *   snapshots; the client must not rederive it from life/combat state.
 * - engine/actor_visual_profile_runtime.h builds the exact key, loads named
 *   source layers, and composes them in profile order.
 *
 * @see game.h for structure definitions
 * @see network.h for multiplayer integration
 * @see enemygen.h for NPC spawning
 * @see mainmenu.h for menu system
 */

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
#include "game_utility.h"
#include "game_menu_ui.h"
#include "game_combat_client.h"
#include "network_ingest.h"
#include "sprite_registry.h"
#include "authoritative_presentation_adapters.h"
#include "snapshot_client/remote_authoritative_snapshot.h"
#include "authoritative_item_command_surface.h"
#include "authoritative_item_query_surface.h"
#include "snapshot_client/snapshot_entity_decoder.h"
#include "snapshot_client/snapshot_stream_applier.h"
#include "actor_visual_profile_packet.h"
#include "authoritative_world_item_appearance.h"
#include "authoritative_world_item_pickup_strip.h"
// FL-4049: bundle runtime deleted. ActorVisualProfile runtime is fail-closed
// until the replacement renderer is authored.
#include "interaction_query.h"
#include "snapshot_client/local_snapshot_presentation_track.h"
#include "minimap_renderer.h"
#include "a3d_load_context.h"
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
#include "platform/time_backend.h"
#include "multiplayer_protocol.h"
#include "physics_tick.h"
#include "mp_step.h"
#include "snapshot_client/snapshot_npc_visual_lifecycle.h"
#include "matrix.h"
#include "fast_rand.h"

#include "font1.h"
#include "hp_bar.h"

static const int MP_MAX_HP = 100;

#include "gamepad.h"
#include "audio.h"

#include "game_api.h"
#include "game_input.h"

#ifdef __EMSCRIPTEN__
extern uint64_t GetTime();
#endif
#include "lexer.h"

#include "mainmenu.h"
#include "weather.h"

// --- TEST HARNESS HEADERS ---
#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif // TODO: [Backlog Ref] #endif
// ---------------------------

#if !defined(__EMSCRIPTEN__) && !defined(SERVER)
// Native single-player/local test authority is owned by game_app.cpp.
// game.cpp may request a session, but must not own process bootstrap.
bool EnsureNormalGameAuthoritativeSession(const char* user, const char* map_path);
#endif

// Presentation state is selected through the appearance bundle runtime masks.

Server* volatile server = 0;

// Multiplayer combat constants (Phase 21)
// In authoritative multiplayer, range validation belongs to the server.
// The client still picks the nearest in-cone target for REQ_SWING, but should
// not discard candidates locally just because they are outside a legacy range threshold.
static const uint64_t MP_SWING_COOLDOWN = 400000; // 400ms in microseconds
static const uint64_t MOBILE_AUTO_PICKUP_COOLDOWN_US = 400000; // 400ms in microseconds
static const int stand_us_per_frame = 30000;
static const int ACTOR_VISUAL_LOG_ONCE_CAPACITY = 256;
// kAuthoritativeSwordAttackFrames deleted — was used by client-side
// frame back-derivation. Server owns frame tables.

static void GetHumanPhysicsPos(const Human* h, float out[3])
{
	if (!out)
		return;
	PhysicsPose pose = PhysicsReadPose(h ? (Physics*)h->data : 0);
	out[0] = pose.pos[0];
	out[1] = pose.pos[1];
	out[2] = pose.pos[2];
}

bool HasAuthoritativeServerSession(const Game* g)
{
	return g && server && server->connection.local_id >= 0;
}

// AuthoritativeAttackElapsedFrames deleted — was used by client-side
// action_stamp back-derivation which is now gutted. Server owns timing.


void RetireLocalNpcActorsForAuthoritativeMode(Game* g);

// Floating combat visuals — data owned here, written by network_ingest_combat.cpp.
DamageFloater damage_floaters[MAX_DAMAGE_FLOATERS];
ProjectileVisual projectile_visuals[MAX_PROJECTILE_VISUALS];

extern int g_web_render_stage_code;
int g_web_render_stage_code = 0;
static uint32_t g_web_fl933_cross_tu_probe = 0x13579bdfu;

extern "C" uint32_t GameFL933RenderStageAddr32()
{
	return (uint32_t)(uintptr_t)&g_web_render_stage_code;
}

extern "C" uint32_t GameFL933CrossTuProbeAddr32()
{
	return (uint32_t)(uintptr_t)&g_web_fl933_cross_tu_probe;
}

extern "C" uint32_t GameFL933CrossTuProbe(uint32_t salt)
{
	g_web_fl933_cross_tu_probe ^= salt ? salt : 0x9e3779b9u;
	g_web_fl933_cross_tu_probe = g_web_fl933_cross_tu_probe * 1664525u + 1013904223u;
	return g_web_fl933_cross_tu_probe;
}

extern Material mat[256];

Game* prime_game = 0;
extern Game* game; // web runtime pointer owned by game_web.cpp

// asciiid pseudo-multiplayer
// or game npcs?
Character* player_head = 0;
Character* player_tail = 0;

#ifdef __EMSCRIPTEN__
extern "C" void WebFL933ServerPointerWatch(const char* stage,
                                           const void* game_ptr,
                                           uint32_t game_size,
                                           const void* observed_player_head,
                                           const void* observed_player_tail);
extern "C" int WebFL933AssertAuthoritativeServerPresent(const char* stage,
                                                        const void* game_ptr,
                                                        uint32_t game_size,
                                                        const void* observed_player_head,
                                                        const void* observed_player_tail);

static void FL933TraceInitGameStage(const char* stage, Game* g)
{
	WebFL933ServerPointerWatch(stage, g, (uint32_t)sizeof(Game), player_head, player_tail);
	WebFL933AssertAuthoritativeServerPresent(stage, g, (uint32_t)sizeof(Game), player_head, player_tail);
}
#else
static void FL933TraceInitGameStage(const char*, Game*) {}
#endif

char player_name[32*4] = "player";
char player_name_cp437[32] = "player";
char g_requested_a3d_path[1024] = "";


extern "C" const char* GetConfPath();
extern "C" void SyncConf();

bool GetGamePadConfPath(char* path, const char* name, int axes, int buttons)
{
	const char* cfg = GetConfPath();
	const char* filepart1 = strrchr(cfg,'/');
	const char* filepart2 = strrchr(cfg,'\\');

	if (!filepart1 && !filepart2)
		return false;

	if (!filepart2)
		filepart2 = filepart1;
	if (!filepart1)
		filepart1 = filepart2;

	int pos1 = (int)(filepart1 - cfg);
	int pos2 = (int)(filepart2 - cfg);
	int pos = pos1>pos2 ? pos1 : pos2;

	memcpy(path,cfg,pos+1);
	sprintf(path+pos+1,"asciicker_(%s)_A%d_B%d.cfg",name,axes,buttons);

	for (int i=pos+1; path[i]; i++)
	{
		// fix characters not valid for path
		if (path[i]<=32 || path[i]>=127)
			path[i]='_';
		else
		if (path[i]=='<')
			path[i]='[';
		else
		if (path[i]=='>')
			path[i]=']';
		else
		if (path[i]=='/' || path[i]=='\\' || path[i]=='|' || path[i]=='?' || path[i]=='*')
			path[i]='.';
		else
		if (path[i]=='\"')
			path[i]='\'';
		else
		if (path[i]==':')
			path[i]=';';
	}

	return true;
}

bool ReadGamePadConf(uint8_t map[256], const char* name, int axes, int buttons)
{
	char path[1024];
	if (!GetGamePadConfPath(path,name,axes,buttons))
		return false;

	FILE* f = fopen(path,"rb");
	if (!f)
		return false;

	int n = 2*axes+buttons;
	int r = (int)fread(map,1,n,f);
	fclose(f);

	return n==r;
}

bool WriteGamePadConf(const uint8_t* map, const char* name, int axes, int buttons)
{
	char path[1024];
	if (!GetGamePadConfPath(path,name,axes,buttons))
		return false;

	FILE* f = fopen(path,"wb");
	if (!f)
		return false;

	int n = 2*axes+buttons;
	int w = (int)fwrite(map,1,n,f);
	fclose(f);

	SyncConf();

	return n==w;
}


////////////////////////////////////////////////////////////////////////////////////


// here we need all character sprites
// ...


Sprite* character_button = 0;
Sprite* inventory_sprite = 0;
Sprite* fire_sprite = 0;

// Generic inst clear helper. Keep this name honest: the split mount-aux
// experiment is gone, so this helper must stay generic and must not re-grow
// hidden aux-owner semantics under the old label.
static void DeleteInstAndClear(Inst** inst_ptr)
{
	if (!inst_ptr || !*inst_ptr)
		return;
	DeleteInst(*inst_ptr);
	*inst_ptr = 0;
}

// TODO: // TODO: [Backlog Ref] TODO:
// CreateGame will be called before loading world !!!
void EnsureLocalPlayerInst(Game* g, Sprite* sprite, const float pos[3],
	float yaw, int anim, int frame)
{
	if (!g || !world || !sprite || !pos)
		return;
	if (g->player.player_inst && GetInstWorld(g->player.player_inst) != world)
	{
		DeleteInst(g->player.player_inst);
		g->player.player_inst = 0;
	}
	if (g->player.player_inst)
		return;
	int flags = INST_USE_TREE | INST_VISIBLE | INST_VOLATILE;
	int reps[4] = { 0,0,0,0 };
	float inst_pos[3] = { pos[0], pos[1], pos[2] };
	g->player.player_inst = CreateInst(world, sprite, flags, inst_pos, yaw, anim, frame,
		reps, 0, -1/*not in story*/);
	if (g->player.player_inst)
		SetInstSpriteData(g->player.player_inst, &g->player);
}

void InitGame(Game* g, int water, float pos[3], float yaw, float dir, float lt[4], uint64_t stamp)
{
	FL933TraceInitGameStage("InitGame:entry", g);
	g->ui.menu_depth = -1;

	g->session.perspective = false;
	g->session.blood = true;

	memset(&g->debug, 0, sizeof(DebugTelemetryState));
	memset(&g->inventory_view, 0, sizeof(InventoryViewState));
	memset(&g->authoritative, 0, sizeof(AuthoritativeClientState));
	memset(&g->input,0,sizeof(Game::Input));
	memset(&g->player,0,sizeof(LocalPlayerState));
	g->authoritative.item_respawn_batch_enabled = true;
	g->inventory_view.mobile_auto_combat_item_id = 0xffff;
	g->inventory_view.mobile_auto_combat_state = MOBILE_AUTO_COMBAT_STATE::NONE;
	g->inventory_view.mobile_auto_pickup_item_id = 0xffff;
	ResetLocalSnapshotPresentationTrack(&g->player.snapshot_presentation_track);

	ResetInteractionQueryResult(&g->inventory_view.interaction_query_result);
	g->inventory_view.items_inrange = g->inventory_view.interaction_query_result.items;
	g->inventory_view.items_count = 0;
	g->authoritative.world_items_count = 0;
	g->authoritative.world_pickup_rows_count = 0;
	for (int i = 0; i < (int)(sizeof(g->authoritative.world_item_ids) / sizeof(g->authoritative.world_item_ids[0])); i++)
	{
		g->authoritative.world_item_ids[i] = 0xffff;
	}
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		g->authoritative.world_pickup_item_ids[i] = 0xffff;
		g->authoritative.world_pickup_distance2[i] = 1.0e30f;
	}
	g->inventory_view.items_ylo = 0;
	g->inventory_view.items_yhi = 0;

	g->player.prev_grounded = 0;
	g->camera.cam_shift = 0;
	g->camera.cam_smooth_z = 0;
	g->camera.cam_smooth_z_init = false;
	memset(g->input.keyb_key,0,32);

	uint32_t world_seed = GetMultiplayerWorldSeed();
	bool authoritative_multiplayer = (server != 0);
	FL933TraceInitGameStage("InitGame:after-authoritative-mode-read", g);
	if (world_seed == 0)
	{
		if (server)
			world_seed = 0x4D505752u; // deterministic fallback for legacy peers without join seed
		else
			world_seed = (uint32_t)stamp;
	}
	fast_srand((int)world_seed);

	if (authoritative_multiplayer)
	{
		if (world)
			PurgeWorldItemInsts(world);
		g->authoritative.local_npcs_retired = true;
	}

	strcpy(g->player.name, player_name);
	strcpy(g->player.name_cp437, player_name_cp437);

	if (server)
	{
		memset(&server->connection.lag, 0, sizeof(server->connection.lag));
		server->connection.lag.last_lag = stamp;
	}
	FL933TraceInitGameStage("InitGame:after-server-lag-init", g);

	g->player.prev = 0;
	g->player.next = player_head;
	if (player_head)
		player_head->prev = &g->player;
	else
		player_tail = &g->player;
	player_head = &g->player;
	FL933TraceInitGameStage("InitGame:after-player-list-link", g);

	g->player.pos[0] = pos[0];
	g->player.pos[1] = pos[1];
	g->player.pos[2] = pos[2];
	g->player.dir = dir;
	g->player.prev_yaw = yaw;

	// just initialized,
	// nowhere modified!
	g->ui.show_minimap = true;
	g->ui.show_buts = true;
	g->ui.bars_pos = 7;

	int width = 112, height = 63;
	g->ui.keyb_hide = 1000;// keyb.Height(width, height);

	g->renderer = CreateRenderer(stamp);
	g->physics = CreatePhysics(terrain, world, pos, dir, yaw, stamp, PHYSICS_CREATE_TERRAIN_SAFE_LIFT);
	FL933TraceInitGameStage("InitGame:after-CreatePhysics", g);
	g->stamp = stamp;

	g->player.data = g->physics;

	// init player!
	g->player.MAX_HP = MP_MAX_HP;
	g->player.HP = g->player.MAX_HP;
	g->player.master = 0;
	g->player.target = 0;
	g->player.followers = 0;
	g->player.enemy = false; // sounds ridiculous
	g->debug.dbg_self_hp = g->player.HP;
	g->debug.dbg_self_max_hp = g->player.MAX_HP;
		g->player.clr = 0;
		g->session.water = water;
	g->player.life_state = LIFE_STATE::ALIVE;
	g->player.mount_state = MOUNT::NONE;
	g->player.locomotion_state = LOCOMOTION_STATE::IDLE;
	g->player.combat_state = COMBAT_STATE::NONE;
	// FL-728: ensure local player has a valid V2 appearance so the profile
	// resolver can produce a sprite even when no server has sent one.
	EnsureLocalPlayerAppearance(g->player);
		g->player.presentation_kind_id = APPEARANCE_PRESENTATION_KIND_IDLE_WALK;
		g->player.presentation_selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	g->player.presentation_started_tick = (uint32_t)(stamp / 1000ull);
	g->player.action_stamp = stamp;
	ActorPresentationResult initial_player_presentation =
		ResolveLocalWallClockCharacterPresentation(&g->player, g->player.clr, 0, stamp);
	FL933TraceInitGameStage("InitGame:after-ResolveLocalWallClockCharacterPresentation", g);
	g->player.sprite = initial_player_presentation.sprite;
	g->player.anim = initial_player_presentation.anim;
	g->player.frame = initial_player_presentation.frame;
	if (!g->player.sprite)
	{
		printf("[appearance-init] local player presentation unresolved kind=%u load_status=%u selector_found=%u selector_failure_reason=%u\n",
			(unsigned)g->player.presentation_kind_id,
			(unsigned)initial_player_presentation.profile_load_status,
			(unsigned)initial_player_presentation.selector_found,
			(unsigned)(initial_player_presentation.selector_failure_reason
				? initial_player_presentation.selector_failure_reason
				: g->player.presentation_selector_failure_reason));
		fflush(stdout);
	}
	g->player.prev_yaw = yaw;
	g->camera.zoom = 1.0f;  // match legacy web build default zoom
	#ifdef PURE_TERM
	g->session.fly_mode = true;
	#else
	g->session.fly_mode = !authoritative_multiplayer;
	#endif

	for (int i=0; i<4; i++)
		g->session.light[i]=lt[i];

	// fancy part
	// create player sprite instance inside world but never paint
	// this is to be used by other clients only!
	// renderer will hide its client sprite

	if (g->player.sprite)
		EnsureLocalPlayerInst(g, g->player.sprite, pos, yaw, g->player.anim, g->player.frame);
	FL933TraceInitGameStage("InitGame:after-EnsureLocalPlayerInst", g);

	if (!prime_game)
		prime_game = g;
	FL933TraceInitGameStage("InitGame:exit", g);
}

// ---------------------------------------------------------------------------
// FL-728: Editor skin apply path — V2 bundle ID route
// ---------------------------------------------------------------------------

Game* CreateGame()
{
	// load defaults
	Game* g = (Game*)malloc(sizeof(Game));
	memset(g, 0, sizeof(Game));
	g->debug.dbg_remote0_pid = -1;
	g->debug.dbg_last_remote0_pid = -1;
	g->inventory_view.mobile_auto_combat_item_id = 0xffff;
	g->inventory_view.mobile_auto_combat_stamp = 0;
	g->inventory_view.mobile_auto_combat_state = MOBILE_AUTO_COMBAT_STATE::NONE;
	g->inventory_view.mobile_auto_pickup_item_id = 0xffff;
	g->inventory_view.mobile_auto_pickup_stamp = 0;
	g->authoritative.item_respawn_batch_enabled = true;
	MpMoveInit(&g->player.mp_move);

	ReadConf(g);

	if (!prime_game)
		prime_game = g;

	#ifdef EDITOR // TODO: [Backlog Ref] #ifdef EDITOR
	g->ui.main_menu = false;
	#else
	g->ui.main_menu = true; // in this case we must not use World / Terrain etc ...
	MainMenu_Show();
	#endif // TODO: [Backlog Ref] #endif

	return g;
}

void DeleteGame(Game* g)
{
	if (prime_game == g)
		prime_game = 0;

	free(g);
}

void FreeGame(Game* g)
{
	if (g)
	{
		if (g == prime_game)
			prime_game = 0;

		DeleteInstAndClear(&g->player.player_inst);

		for (int i = 0; i < g->inventory_view.my_items; i++)
			DestroyItem(g->inventory_view.my_item[i].item);

		for (int i = 0; i < g->player.talks; i++)
			free(g->player.talk[i].box);

		if (g->player.talk_box)
			free(g->player.talk_box);
		if (g->renderer)
			DeleteRenderer(g->renderer);
		if (g->physics)
			DeletePhysics(g->physics);

		if (g->player.prev)
			g->player.prev->next = g->player.next;
		else
			player_head = g->player.next;
		if (g->player.next)
			g->player.next->prev = g->player.prev;
		else
			player_tail = g->player.prev;

		#ifndef EDITOR
		Character* h = player_head;
		while (h)
		{
			Character* n = h->next;
			if (h->data != g->physics)
			{
				if (h->prev)
					h->prev->next = h->next;
				else
					player_head = h->next;

				if (h->next)
					h->next->prev = h->prev;
				else
					player_tail = h->prev;

					DeleteInstAndClear(&h->inst);
					DeletePhysics((Physics*)h->data);

				ItemOwner* io = (ItemOwner*)(NPC_Human*)h;

				for (int i = 0; i < io->items; i++)
				{
					io->has[i].item->inst = 0;
					DestroyItem(io->has[i].item);
				}

				free(h);
			}
			h = n;
		}
		#endif // TODO: [Backlog Ref] #endif
	}
}

// SetWeapon/SetShield/SetHelmet/SetArmor deleted — server owns equipment state

void Human::Say(const char* str, int len, uint64_t stamp)
{
	Human& player = *this;

	// immediate post
	TalkBox* box = 0;
	if (player.talks == 3)
	{
		box = player.talk[0].box;
		player.talks--;
		for (int i = 0; i < player.talks; i++)
			player.talk[i] = player.talk[i + 1];
	}
	else
		box = (TalkBox*)malloc(sizeof(TalkBox));

	int lim = len < 256 ? len : 256;
	memset(box, 0, sizeof(TalkBox));
	memcpy(box->buf, str, lim);
	box->len = lim;
	box->cursor_pos = box->len;

	box->max_width = 33;
	box->max_height = 0; // 0: off!
	int s[2], p[2];
	int bl = box->Reflow(s, p);
	box->size[0] = s[0];
	box->size[1] = s[1] < 7 ? s[1] : 7;
	box->cursor_xy[0] = p[0];
	box->cursor_xy[1] = p[1];

	if (box->len>1 &&
		box->buf[0]=='\\' &&
		box->buf[1]!='\\')
	{
		// hacker mode
		akAPI_Exec(box->buf+1, box->len-1);
	}
	else
	{
		int idx = player.talks;
		GetHumanPhysicsPos(&player, player.talk[idx].pos);
		player.talk[idx].box = box;
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
	}
}

void PaintLocalAuthorityHoldScreen(AnsiCell* ptr, int width, int height)
{
	if (!ptr || width <= 0 || height <= 0)
		return;

	memset(ptr, 0, (size_t)width * (size_t)height * sizeof(*ptr));

	const char* line1 = "MULTIPLAYER RECONNECTING";
	const char* line2 = "WAITING FOR AUTHORITATIVE PLAYER SNAPSHOT (please refresh your browser)";
	int line1_x = (width - (int)strlen(line1)) / 2;
	int line2_x = (width - (int)strlen(line2)) / 2;
	if (line1_x < 0)
		line1_x = 0;
	if (line2_x < 0)
		line2_x = 0;
	int line_y = height / 2;
	DrawMiniText(ptr, width, height, line1_x, line_y - 1, line1, yellow, black, width);
	DrawMiniText(ptr, width, height, line2_x, line_y + 1, line2, white, black, width);
}


// [FLOW:GAME]
// [FLOW:RENDER]
// Main Game Loop & Rendering Orchestration
// 1. Handles Test Harness inputs (if active).
// 2. Updates FPS counters.
// 3. Processes Input (Keyboard, Mouse, Gamepad).
// 4. Updates Game Logic (Physics, AI, Player State).
// 5. Calls Renderer::Render to draw the frame.

// =============================================================================
// INPUT HANDLING SYSTEM - Keyboard, Mouse, Touch, Gamepad
// =============================================================================
//
// WHY input pipeline is structured this way:
// Input flows from platform-specific events (SDL/Browser/Terminal) through
// OnKeyb/OnMouse/OnTouch/OnPad accumulation functions into Input struct,
// then processed during Render() frame to update game state and trigger actions.
//
// INPUT PIPELINE:
// 1. Platform events → OnKeyb/OnMouse/OnTouch/OnPad (accumulate in Input struct)
// 2. Render() reads Input → update key state, contact actions, pad state
// 3. Input mapped to actions: move forces (x_force, y_force), jump, attack, inventory_view
// 4. Actions applied to player/camera/UI before physics integration
// 5. Input cleared/consumed for next frame
//
// KEY ACTION TYPES:
// - Movement: WASD/arrows → x_force, y_force in PhysicsIO
// - Jump: Space/gamepad button → input.jump flag (checked for grounded state)

struct MatIDStamp
{
	static void SetMatCB(Patch* p, int x, int y, int view_flags, void* cookie)
	{
		MatIDStamp* t = (MatIDStamp*)cookie;

		double r2 = t->r * t->r;
		double* hit = t->hit;
		int matid = t->matid;

		uint16_t* visual = GetTerrainVisualMap(p);

		bool diff = false;
		diff = true;

		for (int v = 0, i = 0; v < VISUAL_CELLS; v++)
		{
			for (int u = 0; u < VISUAL_CELLS; u++, i++)
			{
				double dx = u + x - hit[0];
				double dy = v + y - hit[1];
				if (dx*dx + dy * dy < r2)
				{
					int old = visual[i] & 0xFF;
					if (old != matid)
					{
						diff = true;
						visual[i] = (visual[i] & ~0x00FF) | matid;
					}
				}
			}
		}

		if (diff)
			UpdateTerrainVisualMap(p);
	}

	int matid;
	double hit[2];
	double r;
};

void PaintTerrain(float* xy, float r, int matid)
{
#ifndef EDITOR
	MatIDStamp stamp;
	stamp.hit[0] = xy[0];
	stamp.hit[1] = xy[1];
	stamp.r = r;
	stamp.matid = matid;

	QueryTerrain(terrain, xy[0], xy[1], r * 0.501, 0x00, MatIDStamp::SetMatCB, &stamp);
	#endif // TODO: [Backlog Ref] #endif
}

int VerifierPickupAuthoritativeWorldItem(Game* g, int index)
{
	(void)g; (void)index;
	return -1;
}

int VerifierSetCheckpointPosition(Game* g, float x, float y, float z)
{
	(void)g; (void)x; (void)y; (void)z;
	return -1;
}

int VerifierUseAuthoritativeItem(Game* g, int index)
{
	(void)g; (void)index;
	return -1;
}

int VerifierDropAuthoritativeItem(Game* g, int index)
{
	(void)g; (void)index;
	return -1;
}

int VerifierSetNearestNpcHp(Game* g, int hp)
{
	(void)g; (void)hp;
	return -1;
}

void RetireLocalNpcActorsForAuthoritativeMode(Game* g)
{
	if (!g)
		return;
	if (g->authoritative.local_npcs_retired)
		return;

	// Local NPC graph references become invalid once we free local map NPC actors.
	// Clear target/master pointers up front so later render/combat/UI passes don't
	// dereference freed NPCs in the same or subsequent frames.
	for (Character* c = player_head; c; c = c->next)
	{
		c->target = 0;
		c->master = 0;
		c->followers = 0;
	}
	g->player.target = 0;
	g->player.master = 0;
	g->player.followers = 0;
	g->CancelItemContacts();
	g->inventory_view.items_count = 0;
	g->authoritative.world_items_count = 0;
	for (int i = 0; i < (int)(sizeof(g->authoritative.world_item_ids) / sizeof(g->authoritative.world_item_ids[0])); i++)
	{
		g->authoritative.world_item_ids[i] = 0xffff;
	}
	g->authoritative.world_pickup_rows_count = 0;
	for (int i = 0; i < AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS; i++)
	{
		g->authoritative.world_pickup_item_ids[i] = 0xffff;
		g->authoritative.world_pickup_distance2[i] = 1.0e30f;
	}
	g->input.pad_item = 0;

	Character* h = player_head;
	while (h)
	{
		Character* n = h->next;
		if (h != &g->player)
		{
			if (h->prev)
				h->prev->next = h->next;
			else
				player_head = h->next;

			if (h->next)
				h->next->prev = h->prev;
			else
				player_tail = h->prev;

			if (h->inst)
			{
				DeleteInst(h->inst);
				h->inst = 0;
			}

			if (h->data)
			{
				DeletePhysics((Physics*)h->data);
				h->data = 0;
			}

			ItemOwner* io = (ItemOwner*)(NPC_Human*)h;

			if (io)
			{
				for (int i = 0; i < io->items; i++)
				{
					if (io->has[i].item)
					{
						io->has[i].item->inst = 0;
						DestroyItem(io->has[i].item);
						io->has[i].item = 0;
					}
				}
				io->items = 0;
			}

			free(h);
		}
		h = n;
	}

	g->authoritative.local_npcs_retired = true;
}

// they are global (not related to game / player or anything)
// but if something calls them, we can be certainly sure we have exactly 1 game object
// so let's use prime_game blindly

void GamePadMount(const char* name, int axes, int buttons, const uint8_t mapping[])
{
	ConnectGamePad(name, axes, buttons, mapping);

	uint8_t map[256];
	if (ReadGamePadConf(map,name,axes,buttons))
		SetGamePadMapping(map);

	// do specialized readconf with {name,axes,buttons} query
	// if found, replace current mapping: GamePadLoad(map); // TODO: [Backlog Ref] if found, replace current mapping: GamePadLoad(map);

	if (prime_game)
		prime_game->OnPadMount(true);
}

void GamePadUnmount()
{
	DisconnectGamePad();

	if (prime_game)
		prime_game->OnPadMount(false);
}

void GamePadButton(int b, int16_t pos)
{
	uint32_t out[1];
	int outs = UpdateGamePadButton(b, pos < 0 ? 0 : pos, out);

	if (prime_game)
	{
		for (int o=0; o<outs; o++)
		{
			uint32_t map = out[o];
			switch ((map >> 16) & 0xFF)
			{
				case 0:
					prime_game->OnPadAxis(map>>24, (int16_t)(map&0xFFFF));
					break;
				case 1:
					prime_game->OnPadButton(map>>24, (int16_t)(map&0xFFFF) >= 16384);
					break;
			}
		}
	}
}

void GamePadAxis(int a, int16_t pos)
{
	uint32_t out[4];
	int outs = UpdateGamePadAxis(a, pos, out);

	// 0 if unmapped
	// 1 button/axis
	// 2 buttons/axes
	// 2 axes (when mapped on L/R-Joy)
	// 4 buttons (when mapped on D-Pad)

	if (prime_game)
	{
		for (int o=0; o<outs; o++)
		{
			uint32_t map = out[o];
			switch ((map >> 16) & 0xFF)
			{
				case 0:
					prime_game->OnPadAxis(map>>24, (int16_t)(map&0xFFFF));
					break;
				case 1:
					prime_game->OnPadButton(map>>24, (int16_t)(map&0xFFFF) >= 16384);
					break;
			}
		}
	}
}

////////////////////////////////////////////////////////

void exit_handler(int signum);


// TODO: // TODO: [Backlog Ref] TODO:
// - MAKE SIMILAR HACK FOR WEB !
// - ON TTY: system("setfont ./assets/fonts/font-%d.psf"); ?
#ifndef SERVER
bool NextGLFont();
bool PrevGLFont();
void ToggleFullscreen(Game* g);
bool IsFullscreen(Game* g);
#endif // TODO: [Backlog Ref] #endif

bool menu_fullscreen_getter(MenuIO* io)
{
	(void)io;
	#ifndef SERVER
	return IsFullscreen(io->close_game);
	#endif // TODO: [Backlog Ref] #endif
	return false;
}

bool menu_mute_getter(MenuIO* io)
{
	return io->session->mute;
}

bool menu_mobile_controls_getter(MenuIO* io)
{
	return io->session->mobile_controls;
}

void gamepad_close(void* _io)
{
	MenuIO* io = (MenuIO*)_io;
	if (io)
	{
		io->ui->show_gamepad = false;
		io->ui->show_buts = true;

		const uint8_t* map = GetGamePadMapping();
		int axes, buttons;
		const char* name = GetGamePad(&axes, &buttons);
		if (map && name)
			WriteGamePadConf(map,name,axes,buttons);
	}
}

void menu_gamepad(MenuIO* io)
{
	io->close_game->CloseMenu();
	io->ui->show_gamepad = true;
	io->ui->show_buts = false;
	GamePadOpen(gamepad_close,(void*)io);
}

static const Menu video_menu[]=
{
	{"ZOOM IN", 0, menu_zoomin, 0},
	{"ZOOM OUT", 0, menu_zoomout, 0},
	{"FULL SCREEN", 0, menu_fullscreen, menu_fullscreen_getter},
	{"PERSPECTIVE", 0, menu_perspective, menu_perspective_getter},
	{"SHOW BLOOD", 0, menu_blood, menu_blood_getter},
	{0}
};

static const Menu touch_controls_menu[]=
{
	{"MOBILE CONTROLS", 0, menu_mobile_controls, menu_mobile_controls_getter},
	{0}
};

static const Menu controls_menu[]=
{
	{"KEYBOARD", 0, 0, 0},
	{"MOUSE", 0, 0, 0},
	{"TOUCH", touch_controls_menu, 0, 0},
	{"GAMEPAD", 0, menu_gamepad, 0},
	{0}
};

static const Menu exit_menu[]=
{
	{"NO", 0, menu_no_exit, 0},
	{"YES", 0, menu_yes_exit, 0},
	{0}
};


const Menu game_menu[]=
{
	//{"SETTINGS", settings_menu, 0, 0},
	//{"AUDIO", audio_menu, 0, 0},
	{"VIDEO", video_menu, 0, 0},
	{"MUTE SOUND", 0, menu_mute, menu_mute_getter},
	{"CONTROLS", controls_menu, 0, 0},
	{"MAIN MENU", 0, main_menu, 0},
	{"EXIT?", exit_menu, 0, 0},
	{0}
};
